# Post-provision access grants for the shared managed identity.
# Self-sources azd output env vars; role GUIDs resolved BY NAME at runtime
# (tenant governance overlay replaces some built-ins - docs/harness-learnings.md).
$ErrorActionPreference = 'Stop'

$values = @{}
azd env get-values | ForEach-Object {
    if ($_ -match '^([A-Za-z0-9_]+)=') { $values[$Matches[1]] = ($_ -replace '^[^=]+=', '').Trim('"') }
}

$ResourceGroup = $values['resourceGroupName']
$PrincipalId = $values['managedIdentityPrincipalId']
$ServiceBusNamespaceName = $values['serviceBusNamespaceName']
$CosmosAccountName = $values['cosmosAccountName']
$StorageAccountName = $values['storageAccountName']
$AcrName = ($values['acrLoginServer'] -replace '\.azurecr\.io', '')

foreach ($v in 'ResourceGroup', 'PrincipalId', 'ServiceBusNamespaceName', 'CosmosAccountName', 'StorageAccountName', 'AcrName') {
    if (-not (Get-Variable $v -ValueOnly)) { throw "missing env value: $v" }
}

function Resolve-RoleId([string] $RoleName) {
    $id = az role definition list --query "[?roleName=='$RoleName'].id" -o tsv | Select-Object -First 1
    if (-not $id) { throw "role definition not found: $RoleName" }
    return $id.Trim()
}

function New-DeterministicGuid([string] $scope, [string] $roleKey, [string] $principal) {
    $bytes = [Text.Encoding]::UTF8.GetBytes("$scope|$roleKey|$principal")
    $md5 = [Security.Cryptography.MD5]::Create()
    ([Guid]$md5.ComputeHash($bytes)).ToString()
}

function Grant([string] $Scope, [string] $RoleName, [string] $RoleKey) {
    $roleId = Resolve-RoleId $RoleName
    $assignment = New-DeterministicGuid $Scope $RoleKey $PrincipalId
    az role assignment create `
        --assignee-object-id $PrincipalId --assignee-principal-type ServicePrincipal `
        --scope $Scope --role $roleId --name $assignment 2>&1 | Out-Null
    if ($LASTEXITCODE -gt 0) { Write-Host "warn: assignment may already exist ($RoleName)" }
    Write-Host "granted '$RoleName'"
}

$sbId = az servicebus namespace show -g $ResourceGroup -n $ServiceBusNamespaceName --query id -o tsv
$cosmosId = az cosmosdb show -g $ResourceGroup -n $CosmosAccountName --query id -o tsv
$stId = az storage account show -g $ResourceGroup -n $StorageAccountName --query id -o tsv
$acrId = az acr show -g $ResourceGroup -n $AcrName --query id -o tsv

Grant -Scope $sbId -RoleName 'Azure Service Bus Data Sender' -RoleKey 'sb-send'
Grant -Scope $sbId -RoleName 'Azure Service Bus Data Receiver' -RoleKey 'sb-recv'
Grant -Scope $stId -RoleName 'Storage Blob Data Contributor' -RoleKey 'blob'
Grant -Scope $acrId -RoleName 'AcrPull' -RoleKey 'acr'

# Cosmos native SQL RBAC: built-in contributor is account-scoped.
$builtinDefId = "$cosmosId/sqlRoleDefinitions/00000000-0000-0000-0000-000000000001"
$assignName = New-DeterministicGuid $cosmosId 'cosmos-data' $PrincipalId
az cosmosdb sql role assignment create `
    --account-name $CosmosAccountName --resource-group $ResourceGroup `
    --role-definition-id $builtinDefId `
    --principal-id $PrincipalId --scope $cosmosId `
    --unique-name $assignName 2>&1 | Out-Null
Write-Host "granted 'Cosmos DB Built-in Data Contributor'"

Write-Host 'ACCESS GRANTS COMPLETE'
