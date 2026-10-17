<#
Creates a development Cognito user pool for dashboard sign-in. Run once:
  powershell -ExecutionPolicy Bypass -File .\scripts\dev_cognito.ps1 -DomainPrefix esl-dev-<yourname>
Requires the AWS CLI signed in to your account. Free tier. Production pools
are managed by Terraform (rollout step 5).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$DomainPrefix,
    [string]$Region = 'us-west-2',
    [string]$FrontendOrigin = 'http://localhost:5173'
)

$ErrorActionPreference = 'Stop'

function Invoke-Aws {
    $output = & aws @args
    if ($LASTEXITCODE -ne 0) { throw "aws $($args -join ' ') failed with exit code $LASTEXITCODE." }
    return $output
}

$poolId = Invoke-Aws cognito-idp create-user-pool --region $Region --pool-name 'esl-dev' `
    --username-attributes email --auto-verified-attributes email `
    --username-configuration CaseSensitive=false `
    --admin-create-user-config 'AllowAdminCreateUserOnly=true' `
    --query 'UserPool.Id' --output text

$clientId = Invoke-Aws cognito-idp create-user-pool-client --region $Region --user-pool-id $poolId `
    --client-name 'esl-dev-web' --no-generate-secret `
    --allowed-o-auth-flows code --allowed-o-auth-scopes openid email `
    --allowed-o-auth-flows-user-pool-client --supported-identity-providers COGNITO `
    --callback-urls "$FrontendOrigin/auth/callback" --logout-urls "$FrontendOrigin/" `
    --explicit-auth-flows ALLOW_USER_SRP_AUTH ALLOW_REFRESH_TOKEN_AUTH `
    --query 'UserPoolClient.ClientId' --output text

Invoke-Aws cognito-idp create-user-pool-domain --region $Region --user-pool-id $poolId --domain $DomainPrefix | Out-Null

Write-Host ''
Write-Host 'Add to .env:' -ForegroundColor Green
Write-Host "COGNITO_REGION=$Region"
Write-Host "COGNITO_USER_POOL_ID=$poolId"
Write-Host "COGNITO_APP_CLIENT_ID=$clientId"
Write-Host ''
Write-Host 'Add to frontend/.env.local:' -ForegroundColor Green
Write-Host "VITE_COGNITO_AUTHORITY=https://cognito-idp.$Region.amazonaws.com/$poolId"
Write-Host "VITE_COGNITO_CLIENT_ID=$clientId"
Write-Host "VITE_COGNITO_DOMAIN=https://$DomainPrefix.auth.$Region.amazoncognito.com"
