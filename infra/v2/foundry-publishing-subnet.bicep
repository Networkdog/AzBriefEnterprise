targetScope = 'resourceGroup'

param subnetResourceId string

var parts = split(subnetResourceId, '/')
resource subnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' existing = {
  scope: resourceGroup(parts[2], parts[4])
  name: '${parts[8]}/${parts[10]}'
}

resource validation 'Microsoft.Resources/deployments@2025-04-01' = {
  name: 'v2-foundry-publishing-subnet-validation'
  properties: {
    mode: 'Incremental'
    parameters: {
      approved: {
        value: length(parts) == 11 && toLower(subnetResourceId) == toLower(subnet.id) && toLower(parts[2]) == toLower(subscription().subscriptionId) && subnet.properties.provisioningState == 'Succeeded' && length(filter(subnet.properties.serviceEndpoints ?? [], endpoint => toLower(endpoint.service) == 'microsoft.cognitiveservices' && endpoint.provisioningState == 'Succeeded')) == 1
      }
    }
    template: {
      '$schema': 'https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#'
      contentVersion: '1.0.0.0'
      parameters: {
        approved: {
          type: 'bool'
          allowedValues: [true]
          metadata: { description: 'The exact same-subscription client subnet must be ready with a CognitiveServices service endpoint. Existing subnets are never changed.' }
        }
      }
      resources: []
    }
  }
}