# Post-provision: ensure the Foundry project exists (idempotent).
#
# The ARM preflight for `accounts/projects` races the account's
# allowProjectManagement/identity PUTs inside a single deployment, so the
# project is created out-of-band here instead (see foundry.bicep comment).
# Safe to run repeatedly: skips when the project already exists.
$ErrorActionPreference = 'Stop'

$values = @{}
azd env get-values | ForEach-Object {
    if ($_ -match '^([A-Za-z0-9_]+)=') { $values[$Matches[1]] = ($_ -replace '^[^=]+=', '').Trim('"') }
}

$Account = $values['foundryAccountName']
$ResourceGroup = $values['resourceGroupName']
$ProjectName = 'harness'
foreach ($v in 'Account', 'ResourceGroup') { if (-not (Get-Variable $v -ValueOnly)) { throw "missing env value: $v" } }

# 1. allowProjectManagement must be on BEFORE project creation.
az rest --method patch --url "https://management.azure.com/subscriptions/$($values['AZURE_SUBSCRIPTION_ID'])/resourceGroups/$ResourceGroup/providers/Microsoft.CognitiveServices/accounts/$Account`?api-version=2025-04-01-preview" `
    --body '{"properties":{"allowProjectManagement":true}}' --headers 'Content-Type=application/json' | Out-Null

# 2. Create the project unless it already exists.
$existing = az rest --method get --url "https://management.azure.com/subscriptions/$($values['AZURE_SUBSCRIPTION_ID'])/resourceGroups/$ResourceGroup/providers/Microsoft.CognitiveServices/accounts/$Account/projects`?api-version=2025-09-01" 2>$null | ConvertFrom-Json
if ($existing.value | Where-Object { $_.name -like "*/$ProjectName" }) {
    Write-Host "foundry project '$ProjectName' already exists"
    exit 0
}
az cognitiveservices account project create --name $Account --resource-group $ResourceGroup --project-name $ProjectName --location $values['AZURE_LOCATION'] -o none
Write-Host "created foundry project '$ProjectName'"
