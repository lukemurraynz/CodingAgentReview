param accountName string
param location string

var sqlDatabaseName = 'harness'

resource account 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: accountName
  location: location
  kind: 'GlobalDocumentDB'
  properties: {
    capabilities: [{ name: 'EnableServerless' }]
    consistencyPolicy: { defaultConsistencyLevel: 'Session' }
    databaseAccountOfferType: 'Standard'
    locations: [{ locationName: location, failoverPriority: 0 }]
  }
}

resource db 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-11-15' = {
  parent: account
  name: sqlDatabaseName
  properties: {
    resource: { id: sqlDatabaseName }
    options: {}
  }
}

var containers = ['findings', 'reviewRuns', 'changes', 'specifications', 'graphEntities', 'episodes']

@batchSize(1)
resource container 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-08-15' = [for c in containers: {
  parent: db
  name: c
  properties: {
    resource: {
      id: c
      partitionKey: { paths: ['/pk'], kind: 'Hash' }
      defaultTtl: c == 'episodes' ? 31536000 : -1
    }
    options: {}
  }
}]

output documentEndpoint string = account.properties.documentEndpoint
output accountId string = account.id
