<#
Runs an operator command as a one-off Fargate task in production and prints its logs.

  .\scripts\admin.ps1 create-business --name "Card Shop" --owner-email owner@shop.com
  .\scripts\admin.ps1 -Task migrate              # alembic upgrade head (normally CI does this)
  .\scripts\admin.ps1 -Task bootstrap            # one-time: create DB logins on a fresh RDS

Requires the AWS CLI signed in to the production account and access to the
Terraform state (it reads `terraform output`).
#>
[CmdletBinding(PositionalBinding = $false)]
param(
    [ValidateSet('admin', 'migrate', 'bootstrap')]
    [string]$Task = 'admin',
    [Parameter(ValueFromRemainingArguments)]
    [string[]]$EslArgs
)

$ErrorActionPreference = 'Stop'

function Invoke-Aws {
    $output = & aws @args
    if ($LASTEXITCODE -ne 0) { throw "aws $($args -join ' ') failed with exit code $LASTEXITCODE." }
    return $output
}

function Get-TaskLogs {
    param([string[]]$LogArgs, [ref]$EventsPrinted, [ref]$LastStderr)

    $local:ErrorActionPreference = 'Continue'
    $page = & aws @LogArgs 2>&1
    $exitCode = $LASTEXITCODE

    if ($exitCode -ne 0) {
        if ($page) {
            # Store stderr for later warning if no events are ever printed
            $LastStderr.Value = $page -join "`n"
        }
        return $null
    }

    if (-not $page) {
        return $null
    }

    $events = $page | ConvertFrom-Json -ErrorAction Continue
    if (-not $events -or -not $events.events) {
        return $null
    }

    foreach ($e in $events.events) {
        Write-Host $e.message
        $EventsPrinted.Value = $true
    }

    return $events.nextForwardToken
}

$infra = Join-Path $PSScriptRoot '..\infra\prod'
$outputs = terraform "-chdir=$infra" output -json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'terraform output failed; run terraform init in infra/prod first.' }

$region = $outputs.region.value
$cluster = $outputs.ecs_cluster_name.value
$family = $outputs.task_definitions.value.$Task
$subnets = $outputs.private_subnet_ids.value -join ','
$securityGroup = $outputs.task_security_group_id.value
$logGroup = $outputs.tasks_log_group.value

$runArgs = @(
    'ecs', 'run-task', '--region', $region, '--cluster', $cluster,
    '--task-definition', $family, '--launch-type', 'FARGATE',
    '--network-configuration', "awsvpcConfiguration={subnets=[$subnets],securityGroups=[$securityGroup],assignPublicIp=DISABLED}",
    '--query', 'tasks[0].taskArn', '--output', 'text'
)

$overrideFile = $null
if ($EslArgs -and $EslArgs.Count -gt 0) {
    # JSON goes through a file: PowerShell 5.1 mangles embedded quotes in native arguments.
    $command = @('esl', 'admin') + $EslArgs
    $overrides = @{ containerOverrides = @(@{ name = 'esl'; command = $command }) } | ConvertTo-Json -Depth 5 -Compress
    $overrideFile = New-TemporaryFile
    [System.IO.File]::WriteAllText($overrideFile.FullName, $overrides)
    $runArgs += @('--overrides', "file://$($overrideFile.FullName)")
}

try {
    $taskArn = Invoke-Aws @runArgs
} finally {
    if ($overrideFile) { Remove-Item $overrideFile.FullName -Force }
}
if (-not $taskArn -or $taskArn -eq 'None') { throw 'run-task did not start a task (check capacity / IAM).' }
$taskId = $taskArn.Split('/')[-1]
$stream = "$Task/esl/$taskId"
Write-Host "Started $family task $taskId" -ForegroundColor Cyan

$nextToken = $null
$status = ''
$eventsPrinted = $false
$lastStderr = ''

do {
    Start-Sleep -Seconds 3
    $status = Invoke-Aws ecs describe-tasks --region $region --cluster $cluster --tasks $taskArn --query 'tasks[0].lastStatus' --output text
    $logArgs = @('logs', 'get-log-events', '--region', $region, '--log-group-name', $logGroup,
                 '--log-stream-name', $stream, '--start-from-head', '--output', 'json')
    if ($nextToken) { $logArgs += @('--next-token', $nextToken) }
    $newToken = Get-TaskLogs $logArgs ([ref]$eventsPrinted) ([ref]$lastStderr)
    if ($newToken) { $nextToken = $newToken }
} while ($status -ne 'STOPPED')

# Final log drain: wait for any pending CloudWatch writes and fetch remaining logs
Start-Sleep -Seconds 5
$lastToken = $nextToken
do {
    $logArgs = @('logs', 'get-log-events', '--region', $region, '--log-group-name', $logGroup,
                 '--log-stream-name', $stream, '--start-from-head', '--output', 'json')
    if ($nextToken) { $logArgs += @('--next-token', $nextToken) }
    $newToken = Get-TaskLogs $logArgs ([ref]$eventsPrinted) ([ref]$lastStderr)
    if ($newToken) { $nextToken = $newToken } else { break }
} while ($nextToken -ne $lastToken -or -not $nextToken)

if (-not $eventsPrinted) {
    Write-Host "Warning: no logs printed from $logGroup/$stream" -ForegroundColor Yellow
    if ($lastStderr) {
        Write-Host "Last error: $lastStderr" -ForegroundColor Yellow
    }
}

$exitCode = Invoke-Aws ecs describe-tasks --region $region --cluster $cluster --tasks $taskArn --query 'tasks[0].containers[0].exitCode' --output text
if ($exitCode -eq 'None') {
    $reason = Invoke-Aws ecs describe-tasks --region $region --cluster $cluster --tasks $taskArn --query 'tasks[0].stoppedReason' --output text
    Write-Host "Task stopped without an exit code: $reason" -ForegroundColor Red
    exit 1
}
Write-Host "Exit code: $exitCode" -ForegroundColor ($(if ($exitCode -eq '0') { 'Green' } else { 'Red' }))
exit [int]$exitCode
