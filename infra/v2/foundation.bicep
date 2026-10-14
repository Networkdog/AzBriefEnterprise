targetScope = 'resourceGroup'

param location string
param virtualNetworkResourceGroupName string
param virtualNetworkName string
param peSubnetName string
param foundrySubnetName string
param containerAppsSubnetName string
param foundryAccountName string
param storageAccountName string
param cosmosAccountName string
param searchServiceName string
param useMicrosoftManagedAgentData bool
param containerAppsEnvironmentName string
param controlPlaneIdentityName string
param logAnalyticsWorkspaceName string
param applicationInsightsName string
param communicationServicesName string
param emailServiceName string
param emailDataLocation string
param searchSku string
param existingPrivateDnsZoneIds object
param deployContainerAppsPrivateEndpoint bool
param tags object

var vnetId = resourceId(virtualNetworkResourceGroupName, 'Microsoft.Network/virtualNetworks', virtualNetworkName)
var dnsZoneNames = [
  'privatelink.cognitiveservices.azure.com'
  'privatelink.openai.azure.com'
  'privatelink.services.ai.azure.com'
  'privatelink.blob.${environment().suffixes.storage}'
  'privatelink.documents.azure.com'
  'privatelink.search.windows.net'
  'privatelink.${location}.azurecontainerapps.io'
]

resource workspace 'Microsoft.OperationalInsights/workspaces@2025-02-01' = {
  name: logAnalyticsWorkspaceName
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    features: { disableLocalAuth: true }
  }
}

resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: applicationInsightsName
  location: location
  kind: 'web'
  tags: tags
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
    DisableLocalAuth: true
  }
}

module dnsZones 'br/public:avm/res/network/private-dns-zone:0.8.1' = [
  for (zoneName, index) in dnsZoneNames: if (!contains(existingPrivateDnsZoneIds, zoneName) && (!useMicrosoftManagedAgentData || (index != 4 && index != 5))) {
    name: 'v2-dns-${index}'
    params: {
      name: zoneName
      virtualNetworkLinks: [
        { name: 'v2-${uniqueString(vnetId)}', virtualNetworkResourceId: vnetId, registrationEnabled: false }
      ]
      tags: tags
      enableTelemetry: false
    }
  }
]

module foundry 'br/public:avm/res/cognitive-services/account:0.19.1' = {
  name: 'v2-foundry'
  params: {
    name: foundryAccountName
    location: location
    kind: 'AIServices'
    sku: 'S0'
    customSubDomainName: foundryAccountName
    allowProjectManagement: true
    managedIdentities: { systemAssigned: true }
    publicNetworkAccess: 'Disabled'
    disableLocalAuth: true
    networkAcls: { defaultAction: 'Deny' }
    networkInjections: {
      scenario: 'agent'
      subnetResourceId: '${vnetId}/subnets/${foundrySubnetName}'
      useMicrosoftManagedNetwork: false
    }
    tags: tags
    enableTelemetry: false
  }
}

module storage 'br/public:avm/res/storage/storage-account:0.33.1' = {
  name: 'v2-storage'
  params: {
    name: storageAccountName
    location: location
    kind: 'StorageV2'
    skuName: 'Standard_LRS'
    publicNetworkAccess: 'Disabled'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    networkAcls: { defaultAction: 'Deny', bypass: 'None' }
    blobServices: {
      containerDeleteRetentionPolicyEnabled: true
      containerDeleteRetentionPolicyDays: 7
      deleteRetentionPolicyEnabled: true
      deleteRetentionPolicyDays: 7
      containers: [
        { name: 'azbrief-state', publicAccess: 'None' }
        { name: 'azbrief-archive', publicAccess: 'None' }
      ]
    }
    tags: tags
    enableTelemetry: false
  }
}

module cosmos 'br/public:avm/res/document-db/database-account:0.21.1' = if (!useMicrosoftManagedAgentData) {
  name: 'v2-cosmos'
  params: {
    name: cosmosAccountName
    location: location
    databaseAccountOfferType: 'Standard'
    capacityMode: 'Serverless'
    failoverLocations: [{ locationName: location, failoverPriority: 0, isZoneRedundant: false }]
    zoneRedundant: false
    enableAutomaticFailover: false
    enableMultipleWriteLocations: false
    disableLocalAuthentication: true
    disableKeyBasedMetadataWriteAccess: true
    networkRestrictions: {
      publicNetworkAccess: 'Disabled'
      networkAclBypass: 'None'
      ipRules: []
      virtualNetworkRules: []
    }
    tags: tags
    enableTelemetry: false
  }
}

module search 'br/public:avm/res/search/search-service:0.13.0' = if (!useMicrosoftManagedAgentData) {
  name: 'v2-search'
  params: {
    name: searchServiceName
    location: location
    sku: searchSku
    replicaCount: 1
    partitionCount: 1
    publicNetworkAccess: 'Disabled'
    disableLocalAuth: true
    tags: tags
    enableTelemetry: false
  }
}

module identity 'br/public:avm/res/managed-identity/user-assigned-identity:0.6.0' = {
  name: 'v2-control-plane-identity'
  params: {
    name: controlPlaneIdentityName
    location: location
    tags: tags
    enableTelemetry: false
  }
}

module containerEnvironment 'br/public:avm/res/app/managed-environment:0.16.0' = {
  name: 'v2-container-environment'
  params: {
    name: containerAppsEnvironmentName
    location: location
    infrastructureSubnetResourceId: '${vnetId}/subnets/${containerAppsSubnetName}'
    internal: true
    publicNetworkAccess: 'Disabled'
    zoneRedundant: false
    workloadProfiles: [{ name: 'Consumption', workloadProfileType: 'Consumption' }]
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsWorkspaceResourceId: workspace.id
    }
    tags: tags
    enableTelemetry: false
  }
}

module internalIngressDns 'br/public:avm/res/network/private-dns-zone:0.8.1' = {
  name: 'v2-internal-ingress-dns'
  params: {
    name: containerEnvironment.outputs.defaultDomain
    a: [
      { name: '*', ttl: 300, aRecords: [{ ipv4Address: containerEnvironment.outputs.staticIp }] }
      { name: '@', ttl: 300, aRecords: [{ ipv4Address: containerEnvironment.outputs.staticIp }] }
    ]
    virtualNetworkLinks: [
      { name: 'v2-${uniqueString(vnetId)}', virtualNetworkResourceId: vnetId, registrationEnabled: false }
    ]
    tags: tags
    enableTelemetry: false
  }
}

module email '../kt/email.bicep' = {
  name: 'v2-email-services'
  params: {
    communicationServicesName: communicationServicesName
    emailServiceName: emailServiceName
    dataLocation: emailDataLocation
    tags: tags
  }
}

var endpointSpecs = [
  { name: 'pe-${foundryAccountName}', target: resourceId('Microsoft.CognitiveServices/accounts', foundryAccountName), groupId: 'account', zones: take(dnsZoneNames, 3) }
  { name: 'pe-${storageAccountName}', target: resourceId('Microsoft.Storage/storageAccounts', storageAccountName), groupId: 'blob', zones: [dnsZoneNames[3]] }
  { name: 'pe-${cosmosAccountName}', target: resourceId('Microsoft.DocumentDB/databaseAccounts', cosmosAccountName), groupId: 'Sql', zones: [dnsZoneNames[4]] }
  { name: 'pe-${searchServiceName}', target: resourceId('Microsoft.Search/searchServices', searchServiceName), groupId: 'searchService', zones: [dnsZoneNames[5]] }
  { name: 'pe-${containerAppsEnvironmentName}', target: resourceId('Microsoft.App/managedEnvironments', containerAppsEnvironmentName), groupId: 'managedEnvironments', zones: [dnsZoneNames[6]] }
]

@batchSize(1)
module privateEndpoints 'br/public:avm/res/network/private-endpoint:0.12.1' = [
  for (endpoint, index) in endpointSpecs: if ((endpoint.groupId != 'managedEnvironments' || deployContainerAppsPrivateEndpoint) && (!useMicrosoftManagedAgentData || (index != 2 && index != 3))) {
    name: 'v2-private-endpoint-${index}'
    params: {
      name: endpoint.name
      location: location
      subnetResourceId: '${vnetId}/subnets/${peSubnetName}'
      privateLinkServiceConnections: [
        { name: endpoint.name, properties: { privateLinkServiceId: endpoint.target, groupIds: [endpoint.groupId] } }
      ]
      privateDnsZoneGroup: {
        name: 'default'
        privateDnsZoneGroupConfigs: [
          for zoneName in endpoint.zones: {
            privateDnsZoneResourceId: existingPrivateDnsZoneIds[?zoneName] ?? resourceId('Microsoft.Network/privateDnsZones', zoneName)
          }
        ]
      }
      tags: tags
      enableTelemetry: false
    }
    dependsOn: [dnsZones, foundry, storage, cosmos, search, containerEnvironment]
  }
]

resource existingAccount 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryAccountName
}
resource existingStorage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}
resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = {
  parent: existingStorage
  name: 'default'
}
resource existingCosmos 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' existing = {
  name: cosmosAccountName
}
resource existingSearch 'Microsoft.Search/searchServices@2023-11-01' existing = {
  name: searchServiceName
}

resource foundryDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: existingAccount
  name: 'azbrief-v2'
  properties: {
    workspaceId: workspace.id
    logs: [{ categoryGroup: 'allLogs', enabled: true }]
    metrics: [{ category: 'AllMetrics', enabled: true }]
  }
  dependsOn: [foundry]
}
resource blobDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: blobService
  name: 'azbrief-v2'
  properties: {
    workspaceId: workspace.id
    logs: [{ categoryGroup: 'allLogs', enabled: true }]
    metrics: [{ category: 'AllMetrics', enabled: true }]
  }
  dependsOn: [storage]
}
resource cosmosDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: existingCosmos
  name: 'azbrief-v2'
  properties: {
    workspaceId: workspace.id
    logs: [{ categoryGroup: 'allLogs', enabled: true }]
    metrics: [{ category: 'AllMetrics', enabled: true }]
  }
  dependsOn: [cosmos]
}
resource searchDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: existingSearch
  name: 'azbrief-v2'
  properties: {
    workspaceId: workspace.id
    logs: [{ categoryGroup: 'allLogs', enabled: true }]
    metrics: [{ category: 'AllMetrics', enabled: true }]
  }
  dependsOn: [search]
}

output environmentDefaultDomain string = containerEnvironment.outputs.defaultDomain
output identityPrincipalId string = identity.outputs.principalId
output identityClientId string = identity.outputs.clientId
output emailEndpoint string = email.outputs.endpoint
output emailSenderAddress string = email.outputs.senderAddress
output emailSenderRoleDefinitionId string = email.outputs.senderRoleDefinitionId