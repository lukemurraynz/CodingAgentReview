param name string
param location string
param environmentId string
param image string
param identityId string
param azureClientId string
param acrLoginServer string
param serviceBusNamespaceName string
param cosmosEndpoint string
@secure()
param githubWebhookSecret string
param foundryEndpoint string
param foundryDeployment string
param foundryProjectEndpoint string
@secure()
param applicationInsightsConnectionString string
@secure()
param foundryApiKey string = 'PLACEHOLDER'
@secure()
param HARNESS_ADMIN_TOKEN string = ''
@secure()
param HARNESS_ENTRA_TENANT_ID string = ''
@secure()
param HARNESS_ENTRA_AUDIENCE string = ''

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
        { name: 'foundry-api-key', value: foundryApiKey }
        { name: 'github-webhook-secret', value: githubWebhookSecret }
        { name: 'appinsights-connection-string', value: applicationInsightsConnectionString }
        ...(HARNESS_ADMIN_TOKEN != '' ? [{ name: 'admin-token', value: HARNESS_ADMIN_TOKEN }] : [])
        ...(HARNESS_ENTRA_TENANT_ID != '' ? [{ name: 'entra-tenant', value: HARNESS_ENTRA_TENANT_ID }] : [])
        ...(HARNESS_ENTRA_AUDIENCE != '' ? [{ name: 'entra-audience', value: HARNESS_ENTRA_AUDIENCE }] : [])
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
            { name: 'AZURE_CLIENT_ID', value: azureClientId }
            ...(HARNESS_ADMIN_TOKEN != '' ? [{ name: 'HARNESS_ADMIN_TOKEN', secretRef: 'admin-token' }] : [])
            ...(HARNESS_ENTRA_TENANT_ID != '' ? [{ name: 'HARNESS_ENTRA_TENANT_ID', secretRef: 'entra-tenant' }] : [])
            ...(HARNESS_ENTRA_AUDIENCE != '' ? [{ name: 'HARNESS_ENTRA_AUDIENCE', secretRef: 'entra-audience' }] : [])
            { name: 'HARNESS_SERVICEBUS_NS', value: serviceBusNamespaceName }
            { name: 'HARNESS_COSMOS_ENDPOINT', value: cosmosEndpoint }
            { name: 'HARNESS_GITHUB_WEBHOOK_SECRET', secretRef: 'github-webhook-secret' }
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
