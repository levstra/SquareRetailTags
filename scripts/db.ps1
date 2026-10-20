<#
Opens a tunnel to the production database and connects you to it.

  .\scripts\db.ps1              # esl_readonly: tunnel up, password on the clipboard for Rider
  .\scripts\db.ps1 -Psql        # esl_readonly in psql instead
  .\scripts\db.ps1 -Admin       # esl_admin (can write anything); asks you to confirm first

The tunnel is a Session Manager port forward through the NAT instance to RDS
on localhost:15432 (not 5433: that's the local docker Postgres). It closes when
you leave psql, press Enter, or press Ctrl+C. Passwords come from Secrets
Manager on each run and are never written to disk.

Requires the AWS CLI signed in to the production account, the Session Manager
plugin, and access to the Terraform state (it reads `terraform output`).
See docs/deploy.md, "Querying the database".
#>
[CmdletBinding()]
param(
    [switch]$Admin,
    [switch]$Psql,
    [int]$Port = 15432
)

$ErrorActionPreference = 'Stop'

function Invoke-Aws {
    $output = & aws @args
    if ($LASTEXITCODE -ne 0) { throw "aws $($args[0..1] -join ' ') failed with exit code $LASTEXITCODE." }
    return $output
}

function Test-PortInUse([int]$LocalPort) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $client.Connect('127.0.0.1', $LocalPort)
        return $true
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Stop-Tunnel($Process) {
    if (-not $Process -or $Process.HasExited) { return }
    # aws starts session-manager-plugin as a child, and the child holds the port.
    if ($env:OS -eq 'Windows_NT') {
        & taskkill /PID $Process.Id /T /F 2>&1 | Out-Null
    } else {
        Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
    }
}

if (-not (Get-Command aws -ErrorAction SilentlyContinue)) { throw 'The AWS CLI is not on PATH.' }
if (-not (Get-Command session-manager-plugin -ErrorAction SilentlyContinue)) {
    throw 'The Session Manager plugin is not installed: https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html'
}
if ($Psql -and -not (Get-Command psql -ErrorAction SilentlyContinue)) {
    throw 'psql is not on PATH. Leave off -Psql to connect from Rider instead.'
}

$user = if ($Admin) { 'esl_admin' } else { 'esl_readonly' }
if ($Admin) {
    Write-Host 'esl_admin owns every table and can change or delete anything in production.' -ForegroundColor Yellow
    Write-Host 'label_updates edits can send radio writes to battery-powered tags.' -ForegroundColor Yellow
    $confirm = Read-Host 'Type WRITE to connect as esl_admin'
    if ($confirm -cne 'WRITE') { Write-Host 'Not connecting.'; exit 1 }
}

$infra = Join-Path $PSScriptRoot '..\infra\prod'
$outputs = terraform "-chdir=$infra" output -json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'terraform output failed; run terraform init in infra/prod first.' }
$region = $outputs.region.value
$nat = $outputs.nat_instance_id.value
$dbHost = $outputs.rds_address.value
$secret = $outputs.db_password_secrets.value.$user
if (-not $nat -or -not $dbHost -or -not $secret) {
    throw 'terraform output is missing nat_instance_id, rds_address or db_password_secrets; apply infra/prod first.'
}

if (Test-PortInUse $Port) { throw "Something is already listening on localhost:$Port. Close it or pass -Port." }

$password = Invoke-Aws secretsmanager get-secret-value --region $region --secret-id $secret --query SecretString --output text

$stdout = New-TemporaryFile
$stderr = New-TemporaryFile
$tunnel = $null
$copied = $false
try {
    $tunnel = Start-Process aws -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $stdout.FullName -RedirectStandardError $stderr.FullName `
        -ArgumentList @('ssm', 'start-session', '--region', $region, '--target', $nat,
                        '--document-name', 'AWS-StartPortForwardingSessionToRemoteHost',
                        '--parameters', "host=$dbHost,portNumber=5432,localPortNumber=$Port")

    Write-Host "Opening tunnel to $dbHost through $nat..." -ForegroundColor Cyan
    $deadline = (Get-Date).AddSeconds(30)
    while ($true) {
        if ($tunnel.HasExited) {
            throw "The tunnel failed to start:`n$((Get-Content $stderr.FullName -Raw) + (Get-Content $stdout.FullName -Raw))"
        }
        if ((Get-Content $stdout.FullName -Raw) -match 'Waiting for connections') { break }
        if ((Get-Date) -gt $deadline) { throw 'The tunnel did not come up within 30 seconds.' }
        Start-Sleep -Milliseconds 500
    }
    Write-Host "Tunnel open on localhost:$Port." -ForegroundColor Green

    if ($Psql) {
        $env:PGPASSWORD = $password
        & psql "host=localhost port=$Port dbname=esl user=$user sslmode=require"
    } else {
        Set-Clipboard -Value $password
        $copied = $true
        Write-Host ''
        Write-Host "  Host      localhost"
        Write-Host "  Port      $Port"
        Write-Host "  Database  esl"
        Write-Host "  User      $user"
        Write-Host "  SSL       require"
        Write-Host "  Password  on your clipboard"
        Write-Host ''
        Read-Host 'Connected? Press Enter here when you are done to close the tunnel'
    }
} finally {
    Stop-Tunnel $tunnel
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    if ($copied -and (Get-Clipboard) -eq $password) { Set-Clipboard -Value ' ' }
    Remove-Item $stdout.FullName, $stderr.FullName -Force -ErrorAction SilentlyContinue
    Write-Host 'Tunnel closed.'
}
