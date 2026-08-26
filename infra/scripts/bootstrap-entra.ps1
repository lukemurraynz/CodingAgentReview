# Pre-provision Entra bootstrap for the MCP/admin auth surface.
#
# Idempotent: reuses the existing app registration when the audience parameter
# is already present in azd env; otherwise creates app + service principal +
# client secret, wires repo-scoped app roles, and stores everything under
# infra.parameters.* so main.bicep picks the values up automatically.
#
# Env inputs (all optional):
#   HARNESS_REPOS            comma-separated repo ids to grant (default "org/repo")
# Output azd parameters:
#   HARNESS_MCP_ENTRA_TENANT_ID / _AUDIENCE / _CLIENT_ID
#   HARNESS_ENTRA_TENANT_ID / _AUDIENCE
#   HARNESS_ADMIN_TOKEN (generated once, then reused)
$ErrorActionPreference = 'Stop'

$values = @{}
azd env get-values | ForEach-Object {
    if ($_ -match '^([A-Za-z0-9_]+)=') { $values[$Matches[1]] = ($_ -replace '^[^=]+=', '').Trim('"') }
}

function Set-Param([string]$Name, [string]$Value) {
    azd env config set "infra.parameters.$Name" $Value | Out-Null
    Write-Host "param $Name set"
}

function Get-Param([string]$Name) {
    # azd env config get emits either a bare quoted string or JSON {"name":...,"value":...}
    $raw = azd env config get "infra.parameters.$Name" 2>$null
    if (-not $raw) { return $null }
    if ($raw -match '"value"\s*:\s*"([^"]+)"') { return $Matches[1] }
    return $raw.Trim().Trim('"')
}

$tenantId = az account show --query tenantId -o tsv
$appDisplayName = "harness-mcp-$($values['AZURE_ENV_NAME'])"

# --- 1. Reuse or create the app registration -------------------------------
$appId = Get-Param 'HARNESS_MCP_ENTRA_AUDIENCE'
if ($appId) {
    Write-Host "reusing existing app registration $appId"
} else {
    $app = az ad app create --display-name $appDisplayName --sign-in-audience AzureADMyOrg -o json | ConvertFrom-Json
    $appId = $app.appId
    # v2 access tokens so issuer matches login.microsoftonline.com/{tid}/v2.0
    az ad app update --id $app.id --set "api.accessTokenAcceptedVersion=2" 2>$null | Out-Null
    Write-Host "created app registration $appId"
}

# Service principal (client-credentials flow)
$sp = az ad sp show --id $appId --query id -o tsv 2>$null
if (-not $sp) { $sp = az ad sp create --id $appId --query id -o tsv }
Write-Host "service principal: $sp"

# Client secret: reuse if already stored, else mint one now.
$clientSecret = $env:HARNESS_MCP_CLIENT_SECRET
if (-not $clientSecret) {
    Write-Warning "HARNESS_MCP_CLIENT_SECRET not set in env; minting a new secret (store it in your secret manager)."
    $clientSecret = az ad app credential reset --id $appId --query password -o tsv
}

# --- 2. Admin token (generated once) ---------------------------------------
$adminToken = Get-Param 'HARNESS_ADMIN_TOKEN'
if (-not $adminToken) {
    $adminToken = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 43 | ForEach-Object { [char]$_ })
}

# --- 3. Repo-grant app roles ------------------------------------------------
$repos = (@($env:HARNESS_REPOS -split ',') | Where-Object { $_ }) + @('org/repo') | Select-Object -Unique
$appObj = az ad app show --id $appId --query id -o tsv
$graphTok = az account get-access-token --resource https://graph.microsoft.com --query accessToken -o tsv
$appRoles = (Invoke-RestMethod -Method Get -Uri "https://graph.microsoft.com/v1.0/applications/$appObj" -Headers @{ Authorization = "Bearer $graphTok" }).appRoles
foreach ($repo in $repos) {
    $value = "repo:$repo`:read"
    if ($appRoles | Where-Object { $_.value -eq $value }) { continue }
    $appRoles += @{
        allowedMemberTypes = @('Application'); displayName = "Repo read $repo"
        id = [guid]::NewGuid().ToString(); isEnabled = $true
        description = "MCP $value"; value = $value
    }
}
$body = @{ appRoles = $appRoles } | ConvertTo-Json -Depth 6
Invoke-RestMethod -Method Patch -Uri "https://graph.microsoft.com/v1.0/applications/$appObj" `
    -Headers @{ Authorization = "Bearer $graphTok"; 'Content-Type' = 'application/json' } `
    -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) | Out-Null

# Self-assign each role to the SP (admin consent for application permissions).
foreach ($repo in $repos) {
    $value = "repo:$repo`:read"
    $role = $appRoles | Where-Object { $_.value -eq $value } | Select-Object -First 1
    $existing = az rest --method get --url "https://graph.microsoft.com/v1.0/servicePrincipals/$sp/appRoleAssignments" `
        --headers "Authorization=Bearer $graphTok" 2>$null | ConvertFrom-Json
    if ($existing.value | Where-Object { $_.appRoleId -eq $role.id }) { continue }
    $assignBody = @{ principalId = $sp; resourceId = $sp; appRoleId = $role.id } | ConvertTo-Json
    az rest --method post --url "https://graph.microsoft.com/v1.0/servicePrincipals/$sp/appRoleAssignments" `
        --headers "Authorization=Bearer $graphTok" "Content-Type=application/json" --body $assignBody 2>$null | Out-Null
}

# --- 4. Persist into azd parameters -----------------------------------------
Set-Param 'HARNESS_MCP_ENTRA_TENANT_ID' $tenantId
Set-Param 'HARNESS_MCP_ENTRA_AUDIENCE' $appId
Set-Param 'HARNESS_MCP_ENTRA_CLIENT_ID' $appId
Set-Param 'HARNESS_ENTRA_TENANT_ID' $tenantId
Set-Param 'HARNESS_ENTRA_AUDIENCE' $appId
Set-Param 'HARNESS_ADMIN_TOKEN' $adminToken
Write-Host "entra bootstrap complete."
