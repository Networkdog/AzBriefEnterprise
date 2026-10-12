targetScope = 'resourceGroup'

param name string
param location string
param tags object

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: name
  location: location
  tags: tags
}

module reader 'subscription-reader.bicep' = {
  name: 'v2-mcp-subscription-reader'
  scope: subscription()
  params: { principalId: identity.properties.principalId }
}

output resourceId string = identity.id
output clientId string = identity.properties.clientId