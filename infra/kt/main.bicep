targetScope = 'resourceGroup'

@description('Azure public-cloud region of the EXISTING VNet. Foundry must use the same region.')
param location string

@description('Existing VNet resource group, in the deployment subscription.')
param virtualNetworkResourceGroupName string

@description('Existing VNet name. This template never deploys a VNet resource.')
param virtualNetworkName string

@description('Existing or CLI-created subnet used for private endpoints. The name is not fixed.')
param peSubnetName string

@description('Existing or CLI-created subnet dedicated to Foundry injection. The name is not fixed.')
param foundrySubnetName string

@description('Existing or CLI-created subnet dedicated to Container Apps. The name is not fixed.')
param containerAppsSubnetName string

@description('Globally unique name for a NEW network-injected Foundry account.')
param foundryAccountName string = 'ai-azbrief-kt'

@description('Globally unique name for one AzBrief StorageV2 account. App state/archive and Foundry use separate containers but share account-level Foundry permissions.')
@maxLength(24)
param storageAccountName string = 'stazbriefkt'

@description('Globally unique name for the single-region Cosmos DB for NoSQL account.')
param cosmosAccountName string = 'cosmos-azbrief-kt'

@description('Globally unique name for Azure AI Search.')
param searchServiceName string = 'srch-azbrief-kt'

param projectName string = 'azbrief-kt'
param containerAppsEnvironmentName string = 'cae-azbrief-kt'
param containerAppName string = 'ca-azbrief-kt'

@description('Temporarily false for manual Container Apps Environment PE diagnosis. Skips only its PE and DNS zone group, not the Environment or other endpoints. Incremental deployment does not delete an existing PE.')
param deployContainerAppsPrivateEndpoint bool = false

@description('Used ONLY when the selected private-endpoint subnet is missing. Minimum is /28.')
param peSubnetAddressPrefix string = ''

@description('Used ONLY when the selected Foundry subnet is missing. /27 minimum; /24 recommended.')
param foundrySubnetAddressPrefix string = ''

@description('Used ONLY when the selected Container Apps subnet is missing. /27 minimum.')
param containerAppsSubnetAddressPrefix string = ''

@description('Derived by scripts/deploy_kt.py after inventory. The Portal form reuses prepared subnets.')
param createPESubnet bool = false
param createFoundrySubnet bool = false
param createContainerAppsSubnet bool = false

@description('Zone-name to existing private-DNS-zone ARM ID. Existing zones and links are NOT modified.')
param existingPrivateDnsZoneIds object = {}

@description('VNet-linked private DNS zones discovered by the Portal form. Do not set manually.')
param linkedPrivateDnsZones array = []

@description('Optional existing Log Analytics workspace for ACA console logs. No workspace is created.')
param logAnalyticsWorkspaceResourceId string = ''

@description('Basic is the lowest Search tier with private endpoints. Validate capacity before production.')
@allowed([
  'basic'
  'standard'
])
param searchSku string = 'basic'

@description('False creates only the foundation, not a project. True creates the project, its role/connection bindings and BYO Capability Host together after the account host is ready. Never use stage one to precreate the project.')
param deployCapabilityHost bool = false

@description('Compatibility parameter name for the pinned AzBrief control-plane image. Agent publication and authenticated Admin/Archive setup remain separate.')
@allowed([
  'ghcr.io/networkdog/azbriefenterprise@sha256:6d8fe1e237110318344f5786602b5105c6e662f6a45186dcfc8bc6cb8bd2aaa3'
])
param bootstrapImage string = 'ghcr.io/networkdog/azbriefenterprise@sha256:6d8fe1e237110318344f5786602b5105c6e662f6a45186dcfc8bc6cb8bd2aaa3'

@description('Expected Hosted Agent name. This template configures its endpoint but does not publish it.')
@minLength(1)
param foundryHostedAgentName string = 'azbrief-analysis-hosted'

@description('Initial application API key. ARM generates a random secure value when omitted; existing installations always retain their stored key. Never use a GitHub PAT.')
@secure()
@minLength(32)
@maxLength(256)
param apiKey string = replace('${newGuid()}${newGuid()}', '-', '')

@description('Derived from live ARM inventory by the Portal or CLI. Required explicitly for direct ARM deployments: true reuses the existing app secret and fails if it cannot be read.')
param reuseExistingApiKey bool

@description('Anonymous downloads the public GHCR image without a PAT. Credentials is retained for explicitly approved private registry access.')
@allowed([
  'Credentials'
  'Anonymous'
])
param containerRegistryAuthMode string = 'Anonymous'

@description('GitHub user that owns the read:packages token. This is not an Azure managed identity.')
param containerRegistryUsername string = 'Networkdog'

@description('GHCR PAT classic with read:packages only, required in Credentials mode. Never deploy the publisher write token.')
@secure()
param containerRegistryPassword string = ''

param tags object = {
  application: 'AzBrief'
  customer: 'KT'
  deploymentProfile: 'kt-private-foundation'
}

var vnetId = resourceId(
  virtualNetworkResourceGroupName,
  'Microsoft.Network/virtualNetworks',
  virtualNetworkName
)
var peSubnetId = '${vnetId}/subnets/${peSubnetName}'
var foundrySubnetId = '${vnetId}/subnets/${foundrySubnetName}'
var containerAppsSubnetId = '${vnetId}/subnets/${containerAppsSubnetName}'
var foundryAccountId = resourceId('Microsoft.CognitiveServices/accounts', foundryAccountName)
var storageId = resourceId('Microsoft.Storage/storageAccounts', storageAccountName)
var cosmosId = resourceId('Microsoft.DocumentDB/databaseAccounts', cosmosAccountName)
var searchId = resourceId('Microsoft.Search/searchServices', searchServiceName)
var environmentId = resourceId('Microsoft.App/managedEnvironments', containerAppsEnvironmentName)
var foundryProjectEndpoint = 'https://${foundryAccountName}.services.ai.azure.com/api/projects/${projectName}'
var credentialRegistry = containerRegistryAuthMode == 'Credentials'
var dnsZoneNames = [
  'privatelink.cognitiveservices.azure.com'
  'privatelink.openai.azure.com'
  'privatelink.services.ai.azure.com'
  'privatelink.blob.${environment().suffixes.storage}'
  'privatelink.documents.azure.com'
  'privatelink.search.windows.net'
  'privatelink.${location}.azurecontainerapps.io'
]
var discoveredPrivateDnsZoneIds = toObject(
  linkedPrivateDnsZones,
  zone => zone.zoneName,
  zone => zone.zoneId
)
var effectivePrivateDnsZoneIds = union(discoveredPrivateDnsZoneIds, existingPrivateDnsZoneIds)

module peSubnet 'br/public:avm/res/network/virtual-network/subnet:0.2.0' = if (createPESubnet) {
  name: 'kt-pe-subnet'
  scope: resourceGroup(virtualNetworkResourceGroupName)
  params: {
    virtualNetworkName: virtualNetworkName
    name: peSubnetName
    addressPrefix: peSubnetAddressPrefix
    privateEndpointNetworkPolicies: 'Disabled'
    enableTelemetry: false
  }
}

module foundrySubnet 'br/public:avm/res/network/virtual-network/subnet:0.2.0' = if (createFoundrySubnet) {
  name: 'kt-foundry-subnet'
  scope: resourceGroup(virtualNetworkResourceGroupName)
  params: {
    virtualNetworkName: virtualNetworkName
    name: foundrySubnetName
    addressPrefix: foundrySubnetAddressPrefix
    delegation: 'Microsoft.App/environments'
    enableTelemetry: false
  }
  // 동일 VNet의 서브넷 쓰기는 직렬화한다.
  dependsOn: [
    peSubnet
  ]
}

module containerAppsSubnet 'br/public:avm/res/network/virtual-network/subnet:0.2.0' = if (createContainerAppsSubnet) {
  name: 'kt-container-apps-subnet'
  scope: resourceGroup(virtualNetworkResourceGroupName)
  params: {
    virtualNetworkName: virtualNetworkName
    name: containerAppsSubnetName
    addressPrefix: containerAppsSubnetAddressPrefix
    delegation: 'Microsoft.App/environments'
    enableTelemetry: false
  }
  dependsOn: [
    foundrySubnet
  ]
}

module dnsZones 'br/public:avm/res/network/private-dns-zone:0.8.1' = [
  for (zoneName, index) in dnsZoneNames: if (!contains(effectivePrivateDnsZoneIds, zoneName)) {
    name: 'kt-dns-${index}'
    params: {
      name: zoneName
      virtualNetworkLinks: [
        {
          name: 'kt-${uniqueString(vnetId)}'
          virtualNetworkResourceId: vnetId
          registrationEnabled: false
        }
      ]
      tags: tags
      enableTelemetry: false
    }
  }
]

module foundry 'br/public:avm/res/cognitive-services/account:0.19.1' = if (!deployCapabilityHost) {
  name: 'kt-foundry'
  params: {
    name: foundryAccountName
    location: location
    kind: 'AIServices'
    sku: 'S0'
    customSubDomainName: foundryAccountName
    allowProjectManagement: true
    managedIdentities: {
      systemAssigned: true
    }
    publicNetworkAccess: 'Disabled'
    disableLocalAuth: true
    networkAcls: {
      defaultAction: 'Deny'
    }
    networkInjections: {
      scenario: 'agent'
      subnetResourceId: foundrySubnetId
      useMicrosoftManagedNetwork: false
    }
    tags: tags
    enableTelemetry: false
  }
  dependsOn: [
    foundrySubnet
  ]
}

module storage 'br/public:avm/res/storage/storage-account:0.33.1' = {
  name: 'kt-storage'
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
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'None'
    }
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

module cosmos 'br/public:avm/res/document-db/database-account:0.21.1' = {
  name: 'kt-cosmos'
  params: {
    name: cosmosAccountName
    location: location
    databaseAccountOfferType: 'Standard'
    capacityMode: 'Serverless'
    failoverLocations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
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

module search 'br/public:avm/res/search/search-service:0.13.0' = {
  name: 'kt-search'
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

module controlPlaneIdentity 'br/public:avm/res/managed-identity/user-assigned-identity:0.6.0' = {
  name: 'kt-control-plane-identity'
  params: {
    name: 'id-${containerAppName}'
    location: location
    tags: tags
    enableTelemetry: false
  }
}

module containerEnvironment 'br/public:avm/res/app/managed-environment:0.16.0' = {
  name: 'kt-container-environment'
  params: {
    name: containerAppsEnvironmentName
    location: location
    infrastructureSubnetResourceId: containerAppsSubnetId
    // KT Policy d074ddf8 checks vnetConfiguration.internal, not the PNA field named in its title.
    internal: true
    publicNetworkAccess: 'Disabled'
    zoneRedundant: false
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
    appLogsConfiguration: empty(logAnalyticsWorkspaceResourceId)
      ? null
      : {
          destination: 'log-analytics'
          logAnalyticsWorkspaceResourceId: logAnalyticsWorkspaceResourceId
        }
    tags: tags
    enableTelemetry: false
  }
  dependsOn: [
    containerAppsSubnet
  ]
}

var endpointSpecs = [
  {
    name: 'pe-${foundryAccountName}'
    target: foundryAccountId
    groupId: 'account'
    zones: take(dnsZoneNames, 3)
  }
  {
    name: 'pe-${storageAccountName}'
    target: storageId
    groupId: 'blob'
    zones: [dnsZoneNames[3]]
  }
  {
    name: 'pe-${cosmosAccountName}'
    target: cosmosId
    groupId: 'Sql'
    zones: [dnsZoneNames[4]]
  }
  {
    name: 'pe-${searchServiceName}'
    target: searchId
    groupId: 'searchService'
    zones: [dnsZoneNames[5]]
  }
  {
    name: 'pe-${containerAppsEnvironmentName}'
    target: environmentId
    groupId: 'managedEnvironments'
    zones: [dnsZoneNames[6]]
  }
]

@batchSize(1)
module privateEndpoints 'br/public:avm/res/network/private-endpoint:0.12.1' = [
  for (endpoint, index) in endpointSpecs: if (endpoint.groupId != 'managedEnvironments' || deployContainerAppsPrivateEndpoint) {
    name: 'kt-private-endpoint-${index}'
    params: {
      name: endpoint.name
      location: location
      subnetResourceId: peSubnetId
      privateLinkServiceConnections: [
        {
          name: endpoint.name
          properties: {
            privateLinkServiceId: endpoint.target
            groupIds: [endpoint.groupId]
          }
        }
      ]
      privateDnsZoneGroup: {
        name: 'default'
        privateDnsZoneGroupConfigs: [
          for zoneName in endpoint.zones: {
            privateDnsZoneResourceId: effectivePrivateDnsZoneIds[?zoneName] ?? resourceId('Microsoft.Network/privateDnsZones', zoneName)
          }
        ]
      }
      tags: tags
      enableTelemetry: false
    }
    dependsOn: [
      peSubnet
      dnsZones
      foundry
      storage
      cosmos
      search
      containerEnvironment
    ]
  }
]

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryAccountName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = if (deployCapabilityHost) {
  parent: account
  name: projectName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    displayName: projectName
    description: 'KT private AzBrief project with customer-owned agent storage.'
  }
  dependsOn: [
    privateEndpoints
  ]
}

module agentBindings 'agent-bindings.bicep' = if (deployCapabilityHost) {
  name: 'kt-agent-bindings'
  params: {
    foundryAccountName: foundryAccountName
    projectName: projectName
    projectPrincipalId: project!.identity.principalId
    agentStorageAccountName: storageAccountName
    cosmosAccountName: cosmosAccountName
    searchServiceName: searchServiceName
  }
}

module capabilityHost 'capability-host.bicep' = if (deployCapabilityHost) {
  name: 'kt-project-capability-host'
  params: {
    foundryAccountName: foundryAccountName
    projectName: projectName
    projectPrincipalId: project!.identity.principalId
    // 공식 Standard Setup 샘플이 사용하는 내부 ID이며 생성된 Bicep 형식에는 빠져 있다.
    #disable-next-line BCP053
    projectInternalId: project!.properties.internalId
    agentStorageAccountName: storageAccountName
    cosmosAccountName: cosmosAccountName
  }
  dependsOn: [
    agentBindings
  ]
}

resource existingContainerApp 'Microsoft.App/containerApps@2026-01-01' existing = {
  name: containerAppName
}

var applicationApiSecret = reuseExistingApiKey
  ? filter(existingContainerApp.listSecrets().value, secret => secret.name == 'orchestrator-api-key')[0]
  : {
      name: 'orchestrator-api-key'
      value: apiKey
    }

module containerApp 'br/public:avm/res/app/container-app:0.23.0' = {
  name: 'kt-container-app'
  params: {
    name: containerAppName
    location: location
    environmentResourceId: containerEnvironment.outputs.resourceId
    workloadProfileName: 'Consumption'
    managedIdentities: {
      userAssignedResourceIds: [controlPlaneIdentity.outputs.resourceId]
    }
    ingressExternal: true
    ingressAllowInsecure: false
    ingressTargetPort: 8000
    activeRevisionsMode: 'Single'
    registries: credentialRegistry ? [
      {
        server: 'ghcr.io'
        username: containerRegistryUsername
        passwordSecretRef: 'ghcr-pull-token'
      }
    ] : []
    secrets: concat(
      [applicationApiSecret],
      credentialRegistry ? [
        {
          name: 'ghcr-pull-token'
          value: containerRegistryPassword
        }
      ] : []
    )
    scaleSettings: {
      minReplicas: 0
      maxReplicas: 1
    }
    containers: [
      {
        name: 'azbrief'
        image: bootstrapImage
        resources: {
          cpu: json('0.25')
          memory: '0.5Gi'
        }
        env: [
          { name: 'AZURE_TENANT_ID', value: tenant().tenantId }
          { name: 'AZURE_SUBSCRIPTION_ID', value: subscription().subscriptionId }
          { name: 'AZURE_CLIENT_ID', value: controlPlaneIdentity.outputs.clientId }
          { name: 'FOUNDRY_PROJECT_ENDPOINT', value: foundryProjectEndpoint }
          { name: 'FOUNDRY_HOSTED_AGENT_NAME', value: foundryHostedAgentName }
          { name: 'API_KEY', secretRef: 'orchestrator-api-key' }
          { name: 'ARCHIVE_BLOB_CONTAINER_URL', value: '${storage.outputs.primaryBlobEndpoint}azbrief-archive' }
          { name: 'CHECKPOINT_BLOB_URL', value: '${storage.outputs.primaryBlobEndpoint}azbrief-state/checkpoint.json' }
          { name: 'ADMIN_UI_ENABLED', value: 'false' }
          { name: 'ADMIN_REQUIRE_AUTH', value: 'true' }
          { name: 'ARCHIVE_UI_ENABLED', value: 'false' }
          { name: 'ARCHIVE_REQUIRE_AUTH', value: 'true' }
          { name: 'FEEDBACK_UI_ENABLED', value: 'false' }
          { name: 'MAX_CONCURRENT_ANALYSES', value: '1' }
        ]
        probes: [
          {
            type: 'Readiness'
            httpGet: {
              path: '/health'
              port: 8000
            }
            initialDelaySeconds: 5
            periodSeconds: 10
          }
        ]
      }
    ]
    tags: tags
    enableTelemetry: false
  }
  dependsOn: [
    privateEndpoints
  ]
}

output ktFoundation object = {
  schemaVersion: 1
  profile: 'kt-private-foundation'
  applicationReady: false
  capabilityHostRequested: deployCapabilityHost
  containerAppsPrivateEndpointRequested: deployContainerAppsPrivateEndpoint
  tenantId: tenant().tenantId
  subscriptionId: subscription().subscriptionId
  resourceGroup: resourceGroup().name
  location: location
  vnetResourceId: vnetId
  privateEndpointSubnetId: peSubnetId
  foundrySubnetId: foundrySubnetId
  containerAppsSubnetId: containerAppsSubnetId
  foundryAccountResourceId: foundryAccountId
  foundryProjectResourceId: resourceId('Microsoft.CognitiveServices/accounts/projects', foundryAccountName, projectName)
  foundryProjectEndpoint: foundryProjectEndpoint
  foundryHostedAgentName: foundryHostedAgentName
  foundryProjectPrincipalId: deployCapabilityHost ? project!.identity.principalId : ''
  storageAccountResourceId: storageId
  agentStorageAccountResourceId: storageId
  stateStorageAccountResourceId: storageId
  stateContainerUrl: '${storage.outputs.primaryBlobEndpoint}azbrief-state'
  archiveContainerUrl: '${storage.outputs.primaryBlobEndpoint}azbrief-archive'
  containerAppsEnvironmentResourceId: environmentId
  containerAppResourceId: containerApp.outputs.resourceId
  containerImage: bootstrapImage
  containerRegistryAuthMode: containerRegistryAuthMode
  applicationUrl: 'https://${containerApp.outputs.fqdn}'
  bootstrapUrl: 'https://${containerApp.outputs.fqdn}'
  controlPlaneIdentityResourceId: controlPlaneIdentity.outputs.resourceId
  controlPlanePrincipalId: controlPlaneIdentity.outputs.principalId
  controlPlaneClientId: controlPlaneIdentity.outputs.clientId
  logAnalyticsWorkspaceResourceId: logAnalyticsWorkspaceResourceId
}
