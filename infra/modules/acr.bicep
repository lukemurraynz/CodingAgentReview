param name string
param location string

var validName = substring(replace(replace(name, '-', ''), 'i', 'i'), 0, min(length(name), 50))

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: validName
  location: location
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: false
    policies: {
      quarantinePolicy: { status: 'disabled' }
      trustPolicy: { status: 'disabled' }
    }
  }
}

output loginServer string = registry.properties.loginServer
output registryId string = registry.id
