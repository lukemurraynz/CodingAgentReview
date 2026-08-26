// Standalone Microsoft Foundry instance owned by this harness deployment.
// Entra-first: local auth disabled; access granted via Cognitive Services User.
param name string
param location string

var modelName = 'model-router'
var projectUaiName = '${name}-proj-uai'

// Projects preflight requires a user-assigned managed identity on the account.
resource projectUai 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: projectUaiName
  location: location
}

// 2025-04-01-preview: required for allowProjectManagement (projects support).
resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: name
  location: location
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: {
    type: 'SystemAssigned,UserAssigned'
    userAssignedIdentities: { '${projectUai.id}': {} }
  }
  properties: {
    customSubDomainName: name
    publicNetworkAccess: 'Enabled'
    // Required so the nested `projects` resource can be created on this account.
    allowProjectManagement: true
    // INTERIM: local auth enabled because CA+UAI workload-identity federation
    // isn't wired yet (Phase-2 hardening). Keys live only in CA secret store;
    // lenses attempt Entra first and fall back to key (llm.py dual-path).
  }
}

resource modelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: account
  name: modelName
  sku: {
    name: 'GlobalStandard'
    capacity: 100
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: modelName
      // Model Router: routes each request to the best first-party model for
      // the workload — strong reasoning on complex diffs, cheap minis on
      // trivial ones. Lenses get best-model-per-review with one deployment.
    }
  }
}

// Native model-inference surface consumed by azure-ai-inference SDK.
output modelEndpoint string = 'https://${name}.services.ai.azure.com'
output deploymentName string = modelName
output accountId string = account.id

// Foundry project — hosts the Agent-Framework harness surface
// (FOUNDRY_PROJECT_ENDPOINT for agent-framework-foundry).
// Created out-of-band via `az cognitiveservices account project create` because
// the ARM preflight races the account's allowProjectManagement/identity PUTs.
// Referenced as existing so deployments don't re-create it.
resource harnessProject 'Microsoft.CognitiveServices/accounts/projects@2025-09-01' existing = {
  parent: account
  name: 'harness'
}

output projectEndpoint string = 'https://${name}.services.ai.azure.com/api/projects/harness'

output apiKey string = listKeys(account.id, '2024-10-01').key1
