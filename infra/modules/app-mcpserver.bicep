param name string
param location string
param environmentId string
param image string
param identityId string
param azureClientId string
param acrLoginServer string
param cosmosEndpoint string
param foundryEndpoint string
param foundryDeployment string
param foundryProjectEndpoint string
@secure()
param applicationInsightsConnectionString string
@secure()
param foundryApiKey string = 'PLACEHOLDER'
@secure()
param HARNESS_MCP_ENTRA_TENANT_ID string = ''
@secure()
param HARNESS_MCP_ENTRA_AUDIENCE string = ''
@secure()
param HARNESS_MCP_ENTRA_CLIENT_ID string = ''

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
        { name: 'appinsights-connection-string', value: applicationInsightsConnectionString }
        ...(HARNESS_MCP_ENTRA_TENANT_ID != '' ? [{ name: 'mcp-entra-tenant', value: HARNESS_MCP_ENTRA_TENANT_ID }] : [])
        ...(HARNESS_MCP_ENTRA_AUDIENCE != '' ? [{ name: 'mcp-entra-audience', value: HARNESS_MCP_ENTRA_AUDIENCE }] : [])
        ...(HARNESS_MCP_ENTRA_CLIENT_ID != '' ? [{ name: 'mcp-entra-client-id', value: HARNESS_MCP_ENTRA_CLIENT_ID }] : [])
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
            { name: 'AZURE_CLIENT_ID', value: azureClientId }
            { name: 'HARNESS_MCP_TRANSPORT', value: 'streamable-http' }
            ...(HARNESS_MCP_ENTRA_TENANT_ID != '' ? [{ name: 'HARNESS_MCP_ENTRA_TENANT_ID', secretRef: 'mcp-entra-tenant' }] : [])
            ...(HARNESS_MCP_ENTRA_AUDIENCE != '' ? [{ name: 'HARNESS_MCP_ENTRA_AUDIENCE', secretRef: 'mcp-entra-audience' }] : [])
            ...(HARNESS_MCP_ENTRA_CLIENT_ID != '' ? [{ name: 'HARNESS_MCP_ENTRA_CLIENT_ID', secretRef: 'mcp-entra-client-id' }] : [])
            { name: 'HARNESS_FOUNDRY_ENDPOINT', value: foundryEndpoint }
            { name: 'FOUNDRY_PROJECT_ENDPOINT', value: foundryProjectEndpoint }
            { name: 'HARNESS_FOUNDRY_API_KEY', secretRef: 'foundry-api-key' }
            { name: 'HARNESS_FOUNDRY_DEPLOYMENT', value: foundryDeployment }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', secretRef: 'appinsights-connection-string' }
          ]
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 5 }
    }
  }
}

output fqdn string = app.properties.configuration.ingress.fqdn
