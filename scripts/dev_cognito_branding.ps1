<#
Applies infra/prod/branding/settings.json to a DEVELOPMENT user pool,
so the sign-in page can be previewed before production gets it. Safe to rerun
after editing settings.json:
  powershell -ExecutionPolicy Bypass -File .\scripts\dev_cognito_branding.ps1 -UserPoolId <id> -ClientId <id>
Production branding is managed by Terraform (infra/prod/cognito.tf); never
point this at the production pool.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$UserPoolId,
    [Parameter(Mandatory)][string]$ClientId,
    [string]$Region = 'us-west-2'
)

$ErrorActionPreference = 'Stop'

function Invoke-Aws {
    $output = & aws @args
    if ($LASTEXITCODE -ne 0) { throw "aws $($args -join ' ') failed with exit code $LASTEXITCODE." }
    return $output
}

# Branding only renders on a managed login (version 2) domain.
$domain = Invoke-Aws cognito-idp describe-user-pool --region $Region --user-pool-id $UserPoolId `
    --query 'UserPool.Domain' --output text
Invoke-Aws cognito-idp update-user-pool-domain --region $Region --user-pool-id $UserPoolId `
    --domain $domain --managed-login-version 2 | Out-Null

# Fails when the client has no branding yet. Under 'Stop', Windows PowerShell
# turns that stderr into a terminating error even with 2>$null.
$ErrorActionPreference = 'Continue'
$existing = & aws cognito-idp describe-managed-login-branding-by-client --region $Region `
    --user-pool-id $UserPoolId --client-id $ClientId `
    --query 'ManagedLoginBranding.ManagedLoginBrandingId' --output text 2>$null
if ($LASTEXITCODE -ne 0) { $existing = $null }
$ErrorActionPreference = 'Stop'

$branding = Join-Path $PSScriptRoot '..\infra\prod\branding'
$body = @{
    UserPoolId = $UserPoolId
    Settings   = Get-Content (Join-Path $branding 'settings.json') -Raw | ConvertFrom-Json
    # No logo (settings.json disables it). The tab icon is the app's favicon,
    # padded square because Cognito rejects any other ratio. Every format and
    # mode is set: any left out shows Cognito's default icon.
    Assets     = @(
        foreach ($mode in 'LIGHT', 'DARK') {
            foreach ($ext in 'SVG', 'ICO') {
                @{ Category = "FAVICON_$ext"; ColorMode = $mode; Extension = $ext
                   Bytes = [Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $branding "favicon.$($ext.ToLower())"))) }
            }
        }
    )
}
if ($existing) { $body.ManagedLoginBrandingId = $existing } else { $body.ClientId = $ClientId }

$request = Join-Path ([IO.Path]::GetTempPath()) 'esl-dev-branding.json'
$body | ConvertTo-Json -Depth 20 | Set-Content -Path $request -Encoding ascii
try {
    $verb = if ($existing) { 'update-managed-login-branding' } else { 'create-managed-login-branding' }
    Invoke-Aws cognito-idp $verb --region $Region --cli-input-json "file://$request" | Out-Null
} finally {
    Remove-Item $request
}

Write-Host "Branding applied to https://$domain.auth.$Region.amazoncognito.com" -ForegroundColor Green
Write-Host 'Changes can take a minute to show; hard-refresh the sign-in page.'
