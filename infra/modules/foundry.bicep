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
    // Entra-only data plane: DefaultAzureCredential in workers/lenses;
    // keys are never issued, rotated, or leaked (FR-019 security baseline).
    disableLocalAuth: true
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
