param name string
param location string
param environmentId string
param image string
param identityId string
param acrLoginServer string
param cosmosEndpoint string
param foundryEndpoint string
param foundryDeployment string
param foundryProjectEndpoint string
@secure()
param foundryApiKey string = 'PLACEHOLDER'

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: name
  location: location
  tags: { 'azd-service-name': 'mcpserver' }
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identityId}': {} } }
  properties: {
    managedEnvironmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      secrets: [
        { name: 'foundry-api-key', value: foundryApiKey }
      ]
    registries: [
      { server: acrLoginServer, identity: identityId }
    ]
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
      }
    }
    template: {
      containers: [
        {
          name: 'mcpserver'
          image: image
          resources: { cpu: json('0.5'), memory: '1Gi' }

          env: [
            { name: 'HARNESS_COSMOS_ENDPOINT', value: cosmosEndpoint }
            { name: 'HARNESS_MCP_TRANSPORT', value: 'streamable-http' }
            { name: 'HARNESS_FOUNDRY_ENDPOINT', value: foundryEndpoint }
            { name: 'FOUNDRY_PROJECT_ENDPOINT', value: foundryProjectEndpoint }
            { name: 'HARNESS_FOUNDRY_API_KEY', secretRef: 'foundry-api-key' }
            { name: 'HARNESS_FOUNDRY_DEPLOYMENT', value: foundryDeployment }
          ]
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 5 }
    }
  }
}

output fqdn string = app.properties.configuration.ingress.fqdn

