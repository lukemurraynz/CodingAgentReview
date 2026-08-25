// Standalone Microsoft Foundry instance owned by this harness deployment.
// Entra-first: local auth disabled; access granted via Cognitive Services User.
param name string
param location string

var modelName = 'model-router'

resource account 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: name
  location: location
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: name
    publicNetworkAccess: 'Enabled'
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
resource harnessProject 'Microsoft.CognitiveServices/accounts/projects@2025-09-01' = {
  parent: account
  name: 'harness'
  properties: {
    displayName: 'Agentic Engineering Harness'
  }
}

output projectEndpoint string = 'https://${name}.services.ai.azure.com/api/projects/harness'

output apiKey string = listKeys(account.id, '2024-10-01').key1
