/*
  Private DNS for an internal (VNet-only) Container Apps environment.

  The zone name is the environment's generated default domain, which ARM only
  learns once the environment exists. A resource name must be resolvable at the
  start of a deployment, so the value crosses a module boundary instead — that is
  the only place ARM accepts a runtime value as a name.
*/

metadata description = 'Wildcard private DNS zone that resolves an internal Container Apps environment inside the virtual network.'

@description('Container Apps environment default domain. Becomes the private DNS zone name.')
param defaultDomain string

@description('Static IP of the environment load balancer that the wildcard record points at.')
param staticIp string

@description('Virtual network the zone is linked to.')
param vnetResourceId string

@description('Suffix used to name the virtual network link.')
param linkSuffix string

@description('Tags applied to the zone.')
param tags object = {}

resource zone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: defaultDomain
  location: 'global'
  tags: tags
}

resource link 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: zone
  name: 'link-${linkSuffix}'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnetResourceId
    }
  }
}

resource wildcard 'Microsoft.Network/privateDnsZones/A@2024-06-01' = {
  parent: zone
  name: '*'
  properties: {
    ttl: 3600
    aRecords: [
      {
        ipv4Address: staticIp
      }
    ]
  }
}

@description('Private DNS zone that resolves the internal environment.')
output zoneName string = zone.name
