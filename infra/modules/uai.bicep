param name string
param location string

output principalId string = identity.properties.principalId
output identityId string = identity.id
output clientId string = identity.properties.clientId

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-07-31-preview' = {
  name: name
  location: location
}
