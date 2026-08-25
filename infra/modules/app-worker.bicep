param name string
param location string
param environmentId string
param image string
param identityId string
param acrLoginServer string
param serviceBusNamespaceName string
@secure()
param serviceBusListenerConnectionString string
param cosmosEndpoint string
param blobEndpoint string
param foundryEndpoint string
@secure()
@allowed(['', 'PLACEHOLDER'])
param foundryApiKey string = 'PLACEHOLDER'

// Worker runs as a Container App scaled by Service Bus queue length (KEDA).
// Scale-to-zero keeps idle cost at zero; continuous consume mode processes
// messages within replica lifetime. KEDA auth via listen-only connection string.
resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: name
  location: location
  tags: { 'azd-service-name': 'worker' }
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identityId}': {} } }
  properties: {
    managedEnvironmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
    registries: [
      { server: acrLoginServer, identity: identityId }
    ]
      secrets: [
        { name: 'foundry-api-key', value: foundryApiKey }
        { name: 'sb-listen-conn', value: serviceBusListenerConnectionString }
      ]
    }
    template: {
      containers: [
        {
          name: 'worker'
          image: image
          resources: { cpu: json('1'), memory: '2Gi' }
          env: [
            { name: 'HARNESS_SERVICEBUS_LISTEN_CONN', secretRef: 'sb-listen-conn' }
            { name: 'HARNESS_COSMOS_ENDPOINT', value: cosmosEndpoint }
            { name: 'HARNESS_BLOB_ENDPOINT', value: blobEndpoint }
            { name: 'HARNESS_FOUNDRY_ENDPOINT', value: foundryEndpoint }
            { name: 'HARNESS_FOUNDRY_API_KEY', secretRef: 'foundry-api-key' }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 3
        rules: [
          {
            name: 'sb-review-queue'
            custom: {
              type: 'azure-servicebus'
              metadata: {
                queueName: 'review-requests'
                namespace: serviceBusNamespaceName
                messageCount: '5'
              }
              auth: [
                { triggerParameter: 'connection', secretRef: 'sb-listen-conn' }
              ]
            }
          }
        ]
      }
    }
  }
}

output fqdn string = ''

