param name string
param location string

var queueName = 'review-requests'

resource ns 'Microsoft.ServiceBus/namespaces@2024-01-01' = {
  name: name
  location: location
  sku: { name: 'Standard', tier: 'Standard' }
  properties: {}
  resource queue 'queues@2024-01-01' = {
    name: queueName
    properties: {
      maxDeliveryCount: 5
      lockDuration: 'PT5M'
      deadLetteringOnMessageExpiration: true
    }
  }
}

resource listener 'Microsoft.ServiceBus/namespaces/authorizationRules@2024-01-01' = {
  parent: ns
  name: 'harness-worker-listener'
  properties: { rights: ['Listen'] }
}

output namespaceName string = ns.name
output namespaceId string = ns.id
output queueName string = queueName
output listenerConnectionString string = listener.listKeys().primaryConnectionString
