param name string
param location string
param environmentId string
param image string
param identityId string
param acrLoginServer string
param cosmosEndpoint string

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: name
  location: location
  tags: { 'azd-service-name': 'mcpserver' }
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identityId}': {} } }
  properties: {
    managedEnvironmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
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
          ]
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 5 }
    }
  }
}

output fqdn string = app.properties.configuration.ingress.fqdn

