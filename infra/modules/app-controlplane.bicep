param name string
param location string
param environmentId string
param image string
param identityId string
param acrLoginServer string
param serviceBusNamespaceName string
param cosmosEndpoint string
@secure()
param githubWebhookSecret string
param foundryEndpoint string
@secure()
@allowed(['', 'PLACEHOLDER'])
param foundryApiKey string = 'PLACEHOLDER'

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: name
  location: location
  tags: { 'azd-service-name': 'controlplane' }
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identityId}': {} } }
  properties: {
    managedEnvironmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
    registries: [
      { server: acrLoginServer, identity: identityId }
    ]
      secrets: [
        { name: 'github-webhook-secret', value: githubWebhookSecret }
        { name: 'foundry-api-key', value: foundryApiKey }
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
          name: 'controlplane'
          image: image
          resources: { cpu: json('0.5'), memory: '1Gi' }

          env: [
            { name: 'HARNESS_SERVICEBUS_NS', value: serviceBusNamespaceName }
            { name: 'HARNESS_COSMOS_ENDPOINT', value: cosmosEndpoint }
            { name: 'HARNESS_GITHUB_WEBHOOK_SECRET', secretRef: 'github-webhook-secret' }
            { name: 'HARNESS_FOUNDRY_ENDPOINT', value: foundryEndpoint }
            { name: 'HARNESS_FOUNDRY_API_KEY', secretRef: 'foundry-api-key' }
          ]
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 5 }
    }
  }
}

output fqdn string = app.properties.configuration.ingress.fqdn

