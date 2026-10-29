<#
Sends a DEVELOPMENT user pool's email from no-reply@levstra.com through SES,
with the same custom message Lambda production uses
(src/esl/accounts/cognito_emails.py), so the emails can be tested before
production gets them:
  powershell -ExecutionPolicy Bypass -File .\scripts\dev_cognito_email.ps1 -UserPoolId <id> -RecipientEmail <you>
  ... -Disable    back to Cognito's default sender and wording
Needs the levstra.com SES identity from stage 1 (infra/prod/email.tf); it
never creates it. While SES is in the sandbox it only delivers to verified
addresses, so RecipientEmail gets a verification email the first time.
Production email is managed by Terraform (cognito_email_via_ses); never point
this at the production pool.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$UserPoolId,
    [string]$RecipientEmail,
    [string]$Region = 'us-west-2',
    [switch]$Disable
)

$ErrorActionPreference = 'Stop'

function Invoke-Aws {
    $output = & aws @args
    if ($LASTEXITCODE -ne 0) { throw "aws $($args -join ' ') failed with exit code $LASTEXITCODE." }
    return $output
}

# Lookups that fail when the thing doesn't exist yet. Under 'Stop', Windows
# PowerShell turns that stderr into a terminating error even with 2>$null.
function Test-Aws {
    $ErrorActionPreference = 'Continue'
    & aws @args 2>$null | Out-Null
    return $LASTEXITCODE -eq 0
}

$Domain = 'levstra.com'
$Name = 'esl-dev-cognito-emails'

$pool = (Invoke-Aws cognito-idp describe-user-pool --region $Region --user-pool-id $UserPoolId --output json |
    Out-String | ConvertFrom-Json).UserPool
if ($pool.Name -eq 'esl-prod') { throw "$UserPoolId is the production pool. Production email is managed by Terraform." }
if (-not $Disable -and -not $RecipientEmail) { throw '-RecipientEmail is required unless -Disable.' }

$account = Invoke-Aws sts get-caller-identity --query Account --output text
$lambdaArn = "arn:aws:lambda:${Region}:${account}:function:$Name"

if (-not $Disable) {
    if (-not (Test-Aws sesv2 get-email-identity --region $Region --email-identity $Domain)) {
        throw "SES has no $Domain identity in $Region. Apply stage 1 first (docs/deploy.md 'Email')."
    }
    if (-not (Test-Aws sesv2 get-email-identity --region $Region --email-identity $RecipientEmail)) {
        Invoke-Aws sesv2 create-email-identity --region $Region --email-identity $RecipientEmail | Out-Null
        Write-Host "SES sent a verification link to $RecipientEmail. Click it before testing." -ForegroundColor Yellow
    }

    $zip = Join-Path ([IO.Path]::GetTempPath()) "$Name.zip"
    Compress-Archive -Path (Join-Path $PSScriptRoot '..\src\esl\accounts\cognito_emails.py') -DestinationPath $zip -Force
    try {
        if (-not (Test-Aws iam get-role --role-name $Name)) {
            $trust = Join-Path ([IO.Path]::GetTempPath()) "$Name-trust.json"
            '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' |
                Set-Content -Path $trust -Encoding ascii
            Invoke-Aws iam create-role --role-name $Name --assume-role-policy-document "file://$trust" | Out-Null
            Remove-Item $trust
            Invoke-Aws iam attach-role-policy --role-name $Name `
                --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole | Out-Null
            Start-Sleep -Seconds 10 # a new role takes a moment before Lambda can assume it
        }
        if (Test-Aws lambda get-function --region $Region --function-name $Name) {
            Invoke-Aws lambda update-function-code --region $Region --function-name $Name `
                --zip-file "fileb://$zip" | Out-Null
        } else {
            Invoke-Aws lambda create-function --region $Region --function-name $Name `
                --runtime python3.12 --handler cognito_emails.handler --memory-size 128 --timeout 5 `
                --role "arn:aws:iam::${account}:role/$Name" --zip-file "fileb://$zip" `
                --environment 'Variables={APP_URL=http://localhost:5173}' | Out-Null
        }
        Invoke-Aws lambda wait function-updated-v2 --region $Region --function-name $Name
    } finally {
        Remove-Item $zip
    }
    # Replace rather than add, so reruns don't fail on a duplicate statement.
    $statement = "cognito-$UserPoolId"
    Test-Aws lambda remove-permission --region $Region --function-name $Name --statement-id $statement | Out-Null
    Invoke-Aws lambda add-permission --region $Region --function-name $Name --statement-id $statement `
        --action lambda:InvokeFunction --principal cognito-idp.amazonaws.com --source-arn $pool.Arn | Out-Null
}

# update-user-pool resets anything it isn't given, so pass the pool's current
# settings back and change only the email sender and the trigger.
$keep = 'Policies', 'DeletionProtection', 'AutoVerifiedAttributes', 'VerificationMessageTemplate',
    'SmsAuthenticationMessage', 'UserAttributeUpdateSettings', 'MfaConfiguration', 'DeviceConfiguration',
    'SmsConfiguration', 'UserPoolTags', 'AdminCreateUserConfig', 'UserPoolAddOns', 'AccountRecoverySetting',
    'UserPoolTier'
$body = @{ UserPoolId = $UserPoolId }
foreach ($key in $keep) {
    if ($null -ne $pool.$key) { $body[$key] = $pool.$key }
}
# Deprecated twin of TemporaryPasswordValidityDays; sending both is rejected.
if ($body.AdminCreateUserConfig) { $body.AdminCreateUserConfig.PSObject.Properties.Remove('UnusedAccountValidityDays') }

$lambda = @{}
if ($pool.LambdaConfig) {
    foreach ($p in $pool.LambdaConfig.PSObject.Properties) { $lambda[$p.Name] = $p.Value }
}
if ($Disable) {
    $lambda.Remove('CustomMessage')
    $body.EmailConfiguration = @{ EmailSendingAccount = 'COGNITO_DEFAULT' }
} else {
    $lambda.CustomMessage = $lambdaArn
    $body.EmailConfiguration = @{
        EmailSendingAccount = 'DEVELOPER'
        SourceArn           = "arn:aws:ses:${Region}:${account}:identity/$Domain"
        From                = "Levstra <no-reply@$Domain>"
        ReplyToEmailAddress = "levstra@$Domain"
    }
}
$body.LambdaConfig = $lambda

$request = Join-Path ([IO.Path]::GetTempPath()) 'esl-dev-email.json'
$body | ConvertTo-Json -Depth 20 | Set-Content -Path $request -Encoding ascii
try {
    Invoke-Aws cognito-idp update-user-pool --region $Region --cli-input-json "file://$request" | Out-Null
} finally {
    Remove-Item $request
}

if ($Disable) {
    Write-Host "$UserPoolId is back on Cognito's default email." -ForegroundColor Green
} else {
    Write-Host "$UserPoolId now sends from no-reply@$Domain through SES with $Name." -ForegroundColor Green
    Write-Host 'Try "Forgot password" on http://localhost:5173, or resend an invite:'
    Write-Host "  aws cognito-idp admin-create-user --region $Region --user-pool-id $UserPoolId --username <email> --message-action RESEND"
}
