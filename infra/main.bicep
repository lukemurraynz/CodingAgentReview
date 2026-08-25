// Agentic Engineering Harness — infrastructure
// Images wired by azd ({service}Image params). Access grants are applied
// post-provision by scripts/grant-access.ps1 (tenant-specific role GUIDs
// resolved by name at runtime — see docs/harness-learnings.md).
// Validate: az bicep build --file infra/main.bicep

targetScope = 'subscription'

@minLength(5)
param namePrefix string

param location string = deployment().location

@secure()
param githubWebhookSecret string


@secure()
@allowed(['', 'PLACEHOLDER'])
param foundryApiKey string = 'PLACEHOLDER'

param controlplaneImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
param mcpserverImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
param workerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

var rgName = 'rg-${namePrefix}'
var lawName = 'log-${namePrefix}'
var acrName = 'acr${replace(namePrefix, '-', '')}'
var caeName = 'cae-${namePrefix}'
var sbName = 'sb-${namePrefix}'
var cosmosName = 'cos-${replace(namePrefix, '-', '')}'
var stName = '${replace(replace(namePrefix, '-', ''), 'i', '1')}0st'
var uaiName = 'id-${namePrefix}'
var foundryName = 'fnd-${namePrefix}'

resource rg 'Microsoft.Resources/resourceGroups@2024-11-01' = {
  name: rgName
  location: location
}

module law './modules/law.bicep' = {
  name: 'law'
  scope: rg
  params: {
    name: lawName
    location: location
  }
}

module acr './modules/acr.bicep' = {
  name: 'acr'
  scope: rg
  params: {
    name: acrName
    location: location
  }
}

module cae './modules/cae.bicep' = {
  name: 'cae'
  scope: rg
  params: {
    name: caeName
    location: location
    logAnalyticsWorkspaceId: law.outputs.workspaceId
  }
}

module sb './modules/servicebus.bicep' = {
  name: 'servicebus'
  scope: rg
  params: {
    name: sbName
    location: location
  }
}

module cosmos './modules/cosmos.bicep' = {
  name: 'cosmos'
  scope: rg
  params: {
    accountName: cosmosName
    location: location
  }
}

module storage './modules/storage.bicep' = {
  name: 'storage'
  scope: rg
  params: {
    name: stName
    location: location
  }
}

module uai './modules/uai.bicep' = {
  name: 'uai'
  scope: rg
  params: {
    name: uaiName
    location: location
  }
}

module foundry './modules/foundry.bicep' = {
  name: 'foundry'
  scope: rg
  params: {
    name: foundryName
    location: location
  }
}
module controlplane './modules/app-controlplane.bicep' = {
  name: 'controlplane'
  scope: rg
  params: {
    name: 'ca-${namePrefix}-controlplane'
    location: location
    environmentId: cae.outputs.environmentId
    identityId: uai.outputs.identityId
    acrLoginServer: acr.outputs.loginServer
    image: controlplaneImage
    serviceBusNamespaceName: sb.outputs.namespaceName
    cosmosEndpoint: cosmos.outputs.documentEndpoint
    githubWebhookSecret: githubWebhookSecret
    foundryEndpoint: foundry.outputs.modelEndpoint
    foundryDeployment: foundry.outputs.deploymentName
  }
}

module mcpserver './modules/app-mcpserver.bicep' = {
  name: 'mcpserver'
  scope: rg
  params: {
    name: 'ca-${namePrefix}-mcpserver'
    location: location
    environmentId: cae.outputs.environmentId
    identityId: uai.outputs.identityId
    acrLoginServer: acr.outputs.loginServer
    image: mcpserverImage
    cosmosEndpoint: cosmos.outputs.documentEndpoint
  }
}

module worker './modules/app-worker.bicep' = {
  name: 'worker-app'
  scope: rg
  params: {
    name: 'ca-${namePrefix}-worker'
    location: location
    environmentId: cae.outputs.environmentId
    identityId: uai.outputs.identityId
    acrLoginServer: acr.outputs.loginServer
    image: workerImage
    serviceBusNamespaceName: sb.outputs.namespaceName
    serviceBusListenerConnectionString: sb.outputs.listenerConnectionString
    cosmosEndpoint: cosmos.outputs.documentEndpoint
    blobEndpoint: storage.outputs.blobEndpoint
    foundryEndpoint: foundry.outputs.modelEndpoint
    foundryDeployment: foundry.outputs.deploymentName
  }
}

output resourceGroupName string = rg.name
output controlplaneFqdn string = controlplane.outputs.fqdn
output mcpserverFqdn string = mcpserver.outputs.fqdn
output foundryAccountName string = foundryName
output serviceBusNamespaceName string = sb.outputs.namespaceName
output cosmosAccountName string = cosmosName
output storageAccountName string = stName
output acrLoginServer string = acr.outputs.loginServer
output azureContainerRegistryEndpoint string = acr.outputs.loginServer
output managedIdentityPrincipalId string = uai.outputs.principalId
