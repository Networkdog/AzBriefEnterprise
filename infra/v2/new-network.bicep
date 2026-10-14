targetScope = 'resourceGroup'

metadata description = 'Dedicated new v2 VNet only. Existing networks are never updated by this module.'

param location string
param virtualNetworkName string
param virtualNetworkAddressPrefix string
param peSubnetName string
param peSubnetAddressPrefix string
param foundrySubnetName string
param foundrySubnetAddressPrefix string
param containerAppsSubnetName string
param containerAppsSubnetAddressPrefix string
param tags object

resource virtualNetwork 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: virtualNetworkName
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: [virtualNetworkAddressPrefix] }
    subnets: [
      {
        name: peSubnetName
        properties: {
          addressPrefix: peSubnetAddressPrefix
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
      {
        name: foundrySubnetName
        properties: {
          addressPrefix: foundrySubnetAddressPrefix
          delegations: [{ name: 'agent', properties: { serviceName: 'Microsoft.App/environments' } }]
        }
      }
      {
        name: containerAppsSubnetName
        properties: {
          addressPrefix: containerAppsSubnetAddressPrefix
          delegations: [{ name: 'container-apps', properties: { serviceName: 'Microsoft.App/environments' } }]
        }
      }
    ]
  }
}

output virtualNetworkResourceId string = virtualNetwork.id