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

@description('Derived from fresh Portal/CLI inventory after validating the exact existing project, ready BYO host and three connection bindings. Reuse never PUTs the project, BYO connections or host. Direct ARM callers must set this explicitly for a replay.')
param reuseExistingFoundrySetup bool = false

@description('Derived from a matching existing project AppInsights connection. Preserve it without another PUT; never use this to redirect telemetry.')
param reuseExistingTelemetryConnection bool = false

@description('Explicit temporary stage-two anonymous Admin/Archive access inside the isolated KT network. Every reachable client can administer the app and read reports. Never use for public or production access; API/MCP keys and Azure Managed Identity remain required.')
param enablePrivateAnonymousWebUi bool = false

@description('Existing customer-tenant Entra application client ID for optional stage-two Admin/Archive authentication. This template does not create the app registration.')
param adminEntraClientId string = ''

@description('Existing Entra app client secret value. Used only in stage two and stored as a Container App secret, never an environment value or output.')
@secure()
param adminEntraClientSecret string = ''

@description('Comma-separated administrator Object IDs or UPNs. Stage two enables Admin only with Entra credentials and a non-empty allow-list.')
param adminAllowedPrincipals string = ''

@description('Optional comma-separated Archive reader Object IDs, UPNs or group IDs. Administrators are readers automatically.')
param archiveAllowedPrincipals string = ''

@description('Assign all configured control-plane UAMI roles in stage two: RG Reader, project Foundry User, state/archive Blob Contributor and optional service-scoped publishing roles. Requires roleAssignments/write. False is a CLI-only manual-RBAC exception; Hosted evidence roles are never granted.')
param assignControlPlaneRoles bool = true

@description('Existing exact-scope role assignment names discovered by the guarded CLI. Leave empty for deterministic new assignments; never provide names belonging to another principal or role.')
param existingControlPlaneRoleAssignmentNames object = {}

@description('Existing approved Foundry model deployment name, not model ID. This template does not select a model version, allocate quota or create model deployments.')
param modelDeploymentName string = ''

@description('Optional existing separate model deployment for the Azure MCP specialist.')
param simpleModelDeploymentName string = ''

@description('Expected six Prompt Agent names. Defaults use the first 47 project-name characters plus role suffixes, within the 64-character limit. Overrides must provide six distinct names. These are targets, not Agent versions.')
param promptAgentNames object = {
  coordinator: '${take(projectName, 47)}-coordinator'
  resource_graph: '${take(projectName, 47)}-resource-graph'
  azure_mcp: '${take(projectName, 47)}-azure-mcp'
  azure_api: '${take(projectName, 47)}-azure-api'
  report_writer: '${take(projectName, 47)}-report-writer'
  quality_reviewer: '${take(projectName, 47)}-quality-reviewer'
}

@description('Create a stage-two scheduler Job using the same pinned App image and identity. It stays Manual; automatic email/schedules require separate acceptance.')
param deploySchedulerJob bool = false

@description('Name of the optional Manual scheduler Job. Preserve the name on subsequent deployments.')
param schedulerJobName string = 'job-${containerAppName}'

@description('Existing approved Azure Communication Services HTTPS endpoint. No ACS resource/domain or secret is created; sender permission and mail acceptance remain operator tasks.')
param communicationServicesEndpoint string = ''

@description('Existing ACS resource name in this deployment RG. Required for automatic email role assignment; its host must match communicationServicesEndpoint.')
param communicationServicesName string = ''

@description('GUID of an existing customer-approved minimum-permission ACS email sender role. Required for automatic email RBAC; no role definition, broad service-owner role or ACS keys are created.')
param emailSenderRoleDefinitionId string = ''

@description('Verified sender address on the existing ACS Email domain.')
param emailSenderAddress string = ''

@description('Approved digest recipient email address. Empty is allowed when no email setup is requested.')
param emailRecipientAddress string = ''

@description('Existing Entra-only Application Insights component in this RG. Empty retains the KT no-Insights profile. This never creates a component/workspace or changes its network/auth settings.')
param applicationInsightsName string = ''

@description('Read-only Azure MCP server HTTPS URL for the Agent publication handoff. Browser Anonymous does not disable MCP Entra authentication.')
param azureMcpServerUrl string = ''

@description('Existing Foundry project connection for the read-only Azure MCP server. Used by the separate Agent publication procedure.')
param azureMcpProjectConnectionName string = ''

@description('Compatibility parameter name for the pinned AzBrief control-plane image. Agent publication remains separate; stage two can configure authenticated Admin/Archive from an existing Entra app.')
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
var controlPlaneIdentityId = resourceId('Microsoft.ManagedIdentity/userAssignedIdentities', 'id-${containerAppName}')
var foundryProjectEndpoint = 'https://${foundryAccountName}.services.ai.azure.com/api/projects/${projectName}'
var credentialRegistry = containerRegistryAuthMode == 'Credentials'
var privateAnonymousWebUi = deployCapabilityHost && enablePrivateAnonymousWebUi
var adminAuthConfigured = deployCapabilityHost && !privateAnonymousWebUi && !empty(trim(adminEntraClientId)) && !empty(trim(adminEntraClientSecret))
var adminUiEnabled = privateAnonymousWebUi || (adminAuthConfigured && !empty(trim(replace(adminAllowedPrincipals, ',', ''))))
var archiveUiEnabled = privateAnonymousWebUi || (adminAuthConfigured && (adminUiEnabled || !empty(trim(replace(archiveAllowedPrincipals, ',', '')))))
var applicationUrl = 'https://${containerAppName}.${containerEnvironment.outputs.defaultDomain}'
var adminConfigBlobUrl = adminUiEnabled ? '${storage.outputs.primaryBlobEndpoint}azbrief-state/admin-config.json' : ''
var schedulerRequested = deployCapabilityHost && deploySchedulerJob
var telemetryConfigured = deployCapabilityHost && !empty(applicationInsightsName)
var adminReadinessSupportResources = [
  {
    id: 'managed_identity'
    label: 'User-assigned managed identity'
    name: 'id-${containerAppName}'
    resource_type: 'Microsoft.ManagedIdentity/userAssignedIdentities'
    api_version: '2023-01-31'
  }
  {
    id: 'state_storage'
    label: 'Shared Storage (State, Archive, Foundry)'
    name: storageAccountName
    resource_type: 'Microsoft.Storage/storageAccounts'
    api_version: '2023-05-01'
  }
  {
    id: 'cosmos'
    label: 'Cosmos DB'
    name: cosmosAccountName
    resource_type: 'Microsoft.DocumentDB/databaseAccounts'
    api_version: '2024-11-15'
  }
  {
    id: 'search'
    label: 'Azure AI Search'
    name: searchServiceName
    resource_type: 'Microsoft.Search/searchServices'
    api_version: '2023-11-01'
  }
]
var operationalEnv = [
  { name: 'ADMIN_READINESS_FOUNDRY_MODEL_DEPLOYMENT', value: deployCapabilityHost ? modelDeploymentName : '' }
  { name: 'ADMIN_READINESS_FOUNDRY_SIMPLE_MODEL_DEPLOYMENT', value: deployCapabilityHost ? simpleModelDeploymentName : '' }
  { name: 'ADMIN_READINESS_PROMPT_AGENTS', value: base64(string(promptAgentNames)) }
  { name: 'ADMIN_READINESS_CONTAINER_JOBS', value: base64(string(schedulerRequested ? [schedulerJobName] : [])) }
  { name: 'COMMUNICATION_SERVICES_ENDPOINT', value: deployCapabilityHost ? communicationServicesEndpoint : '' }
  { name: 'EMAIL_SENDER_ADDRESS', value: deployCapabilityHost ? emailSenderAddress : '' }
  { name: 'EMAIL_RECIPIENT_ADDRESS', value: deployCapabilityHost ? emailRecipientAddress : '' }
  { name: 'OTEL_ENABLED', value: toLower(string(telemetryConfigured)) }
  { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: telemetryConfigured ? existingInsights!.properties.ConnectionString : '' }
]
var controlPlaneRegistry = credentialRegistry ? [
  {
    server: 'ghcr.io'
    username: containerRegistryUsername
    passwordSecretRef: 'ghcr-pull-token'
  }
] : []
var webAuthConfig = {
  platform: {
    enabled: true
  }
  // API 키 호출과 health는 유지하고, 웹 화면은 앱의 인증·허용 목록 검사를 거친다.
  globalValidation: {
    unauthenticatedClientAction: 'AllowAnonymous'
  }
  httpSettings: {
    requireHttps: true
  }
  identityProviders: {
    azureActiveDirectory: {
      enabled: true
      registration: {
        clientId: trim(adminEntraClientId)
        clientSecretSettingName: 'microsoft-provider-authentication-secret'
        openIdIssuer: '${environment().authentication.loginEndpoint}${tenant().tenantId}/v2.0'
      }
      validation: {
        allowedAudiences: [
          'api://${trim(adminEntraClientId)}'
          trim(adminEntraClientId)
        ]
      }
    }
  }
  login: {
    allowedExternalRedirectUrls: [
      applicationUrl
    ]
    preserveUrlFragmentsForLogins: false
  }
}
var anonymousWebConfig = {
  platform: {
    enabled: false
  }
  globalValidation: {
    unauthenticatedClientAction: 'AllowAnonymous'
  }
  httpSettings: {
    requireHttps: true
  }
  identityProviders: {
    azureActiveDirectory: {
      enabled: false
    }
  }
  login: {
    allowedExternalRedirectUrls: [
      applicationUrl
    ]
    preserveUrlFragmentsForLogins: false
  }
}
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

var reuseFoundrySetup = deployCapabilityHost && reuseExistingFoundrySetup

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = if (deployCapabilityHost && !reuseFoundrySetup) {
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

// 재사용 분기에서만 ID로 조회한다. 같은 ID의 조건부 resource 선언은 ARM 검증과 충돌한다.
#disable-next-line use-resource-symbol-reference
var projectPrincipalId = deployCapabilityHost ? (reuseFoundrySetup ? reference(resourceId('Microsoft.CognitiveServices/accounts/projects', foundryAccountName, projectName), '2025-06-01', 'full').identity.principalId : project!.identity.principalId) : ''
#disable-next-line BCP053 use-resource-symbol-reference
var projectInternalId = deployCapabilityHost ? (reuseFoundrySetup ? reference(resourceId('Microsoft.CognitiveServices/accounts/projects', foundryAccountName, projectName), '2025-06-01', 'full').properties.internalId : project!.properties.internalId) : ''

module agentBindings 'agent-bindings.bicep' = if (deployCapabilityHost) {
  name: 'kt-agent-bindings'
  params: {
    foundryAccountName: foundryAccountName
    projectName: projectName
    projectPrincipalId: projectPrincipalId
    agentStorageAccountName: storageAccountName
    cosmosAccountName: cosmosAccountName
    searchServiceName: searchServiceName
    createConnections: !reuseFoundrySetup
  }
}

module capabilityHost 'capability-host.bicep' = if (deployCapabilityHost) {
  name: 'kt-project-capability-host'
  params: {
    foundryAccountName: foundryAccountName
    projectName: projectName
    projectPrincipalId: projectPrincipalId
    projectInternalId: projectInternalId
    agentStorageAccountName: storageAccountName
    cosmosAccountName: cosmosAccountName
    createHost: !reuseFoundrySetup
  }
  dependsOn: [
    agentBindings
  ]
}

resource existingContainerApp 'Microsoft.App/containerApps@2026-01-01' existing = {
  name: containerAppName
}
resource existingInsights 'Microsoft.Insights/components@2020-02-02' existing = if (telemetryConfigured) {
  name: applicationInsightsName
}

resource foundryMonitoringConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = if (telemetryConfigured && !reuseExistingTelemetryConnection) {
  name: '${foundryAccountName}/${projectName}/azbrief-app-insights'
  properties: {
    category: 'AppInsights'
    // 표준 Enterprise 프로필에서 검증한 인증 방식이다.
    #disable-next-line BCP036
    authType: 'ProjectManagedIdentity'
    target: existingInsights!.id
    isSharedToAll: true
    metadata: {
      ApiType: 'Azure'
      ResourceId: existingInsights!.id
      ApplicationInsightsConnectionString: existingInsights!.properties.ConnectionString
      location: existingInsights!.location
    }
    dependsOn: [
      project
    ]
  }
}

module controlPlaneRoles 'control-plane-roles.bicep' = if (deployCapabilityHost && assignControlPlaneRoles) {
  name: 'kt-control-plane-roles'
  params: {
    foundryAccountName: foundryAccountName
    projectName: projectName
    storageAccountName: storageAccountName
    controlPlanePrincipalId: controlPlaneIdentity.outputs.principalId
    applicationInsightsName: telemetryConfigured ? applicationInsightsName : ''
    communicationServicesName: communicationServicesName
    emailSenderRoleDefinitionId: emailSenderRoleDefinitionId
    existingAssignmentNames: existingControlPlaneRoleAssignmentNames
  }
  dependsOn: [
    project
    storage
  ]
}

var applicationApiSecret = reuseExistingApiKey
  ? filter(existingContainerApp.listSecrets().value, secret => secret.name == 'orchestrator-api-key')[0]
  : {
      name: 'orchestrator-api-key'
      value: apiKey
    }
var controlPlaneSecrets = concat(
  [applicationApiSecret],
  credentialRegistry ? [
    {
      name: 'ghcr-pull-token'
      value: containerRegistryPassword
    }
  ] : []
)
var baseRuntimeEnv = [
  { name: 'AZURE_TENANT_ID', value: tenant().tenantId }
  { name: 'AZURE_SUBSCRIPTION_ID', value: subscription().subscriptionId }
  { name: 'AZURE_CLIENT_ID', value: controlPlaneIdentity.outputs.clientId }
  { name: 'FOUNDRY_PROJECT_ENDPOINT', value: foundryProjectEndpoint }
  { name: 'FOUNDRY_HOSTED_AGENT_NAME', value: foundryHostedAgentName }
  { name: 'API_KEY', secretRef: 'orchestrator-api-key' }
  { name: 'ARCHIVE_BLOB_CONTAINER_URL', value: '${storage.outputs.primaryBlobEndpoint}azbrief-archive' }
  { name: 'CHECKPOINT_BLOB_URL', value: '${storage.outputs.primaryBlobEndpoint}azbrief-state/checkpoint.json' }
  { name: 'ADMIN_CONFIG_BLOB_URL', value: adminConfigBlobUrl }
  { name: 'MAX_CONCURRENT_ANALYSES', value: '1' }
]

resource scheduler 'Microsoft.App/jobs@2026-01-01' = if (schedulerRequested) {
  name: schedulerJobName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${controlPlaneIdentityId}': {}
    }
  }
  properties: {
    environmentId: containerEnvironment.outputs.resourceId
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 43200
      replicaRetryLimit: 0
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: controlPlaneRegistry
      secrets: controlPlaneSecrets
    }
    template: {
      containers: [
        {
          name: 'azbrief-scheduler'
          image: bootstrapImage
          command: ['python', '-m', 'src.scheduler']
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
          env: concat(baseRuntimeEnv, operationalEnv, [
            { name: 'SCHEDULE_DISPATCH_ENABLED', value: 'true' }
            { name: 'RUN_TIME_BUDGET_S', value: '39600' }
          ])
        }
      ]
    }
  }
  dependsOn: [
    privateEndpoints
    controlPlaneRoles
  ]
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
    // Incremental 배포의 null은 기존 인증을 제거하지 않으므로 익명 모드는 명시적으로 비활성화한다.
    authConfig: privateAnonymousWebUi ? anonymousWebConfig : (adminAuthConfigured ? webAuthConfig : null)
    registries: controlPlaneRegistry
    secrets: concat(
      controlPlaneSecrets,
      adminAuthConfigured ? [
        {
          name: 'microsoft-provider-authentication-secret'
          value: adminEntraClientSecret
        }
      ] : []
    )
    scaleSettings: {
      minReplicas: adminUiEnabled ? 1 : 0
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
          { name: 'ADMIN_CONFIG_BLOB_URL', value: adminConfigBlobUrl }
          { name: 'ARCHIVE_BASE_URL', value: archiveUiEnabled ? applicationUrl : '' }
          { name: 'ADMIN_UI_ENABLED', value: toLower(string(adminUiEnabled)) }
          { name: 'ADMIN_REQUIRE_AUTH', value: toLower(string(!privateAnonymousWebUi)) }
          { name: 'ADMIN_ALLOWED_PRINCIPALS', value: adminAuthConfigured && adminUiEnabled ? adminAllowedPrincipals : '' }
          { name: 'ADMIN_READINESS_RESOURCE_GROUP', value: resourceGroup().name }
          { name: 'ADMIN_READINESS_FOUNDRY_ACCOUNT', value: foundryAccountName }
          { name: 'ADMIN_READINESS_FOUNDRY_PROJECT', value: projectName }
          { name: 'ADMIN_READINESS_CONTAINER_ENVIRONMENTS', value: base64(string([containerAppsEnvironmentName])) }
          { name: 'ADMIN_READINESS_CONTAINER_APPS', value: base64(string([containerAppName])) }
          { name: 'ADMIN_READINESS_SUPPORT_RESOURCES', value: base64(string(adminReadinessSupportResources)) }
          { name: 'ARCHIVE_UI_ENABLED', value: toLower(string(archiveUiEnabled)) }
          { name: 'ARCHIVE_REQUIRE_AUTH', value: toLower(string(!privateAnonymousWebUi)) }
          { name: 'ARCHIVE_ALLOWED_PRINCIPALS', value: adminAuthConfigured && archiveUiEnabled ? archiveAllowedPrincipals : '' }
          { name: 'FEEDBACK_UI_ENABLED', value: 'false' }
          { name: 'MAX_CONCURRENT_ANALYSES', value: '1' }
          { name: 'ADMIN_READINESS_FOUNDRY_MODEL_DEPLOYMENT', value: deployCapabilityHost ? modelDeploymentName : '' }
          { name: 'ADMIN_READINESS_FOUNDRY_SIMPLE_MODEL_DEPLOYMENT', value: deployCapabilityHost ? simpleModelDeploymentName : '' }
          { name: 'ADMIN_READINESS_PROMPT_AGENTS', value: base64(string(promptAgentNames)) }
          { name: 'ADMIN_READINESS_CONTAINER_JOBS', value: base64(string(schedulerRequested ? [schedulerJobName] : [])) }
          { name: 'COMMUNICATION_SERVICES_ENDPOINT', value: deployCapabilityHost ? communicationServicesEndpoint : '' }
          { name: 'EMAIL_SENDER_ADDRESS', value: deployCapabilityHost ? emailSenderAddress : '' }
          { name: 'EMAIL_RECIPIENT_ADDRESS', value: deployCapabilityHost ? emailRecipientAddress : '' }
          { name: 'OTEL_ENABLED', value: toLower(string(telemetryConfigured)) }
          { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: telemetryConfigured ? existingInsights!.properties.ConnectionString : '' }
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
    controlPlaneRoles
  ]
}

output ktFoundation object = {
  schemaVersion: 1
  profile: 'kt-private-foundation'
  applicationReady: false
  capabilityHostRequested: deployCapabilityHost
  foundrySetupReused: reuseFoundrySetup
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
  foundryProjectPrincipalId: projectPrincipalId
  storageAccountResourceId: storageId
  agentStorageAccountResourceId: storageId
  stateStorageAccountResourceId: storageId
  stateContainerUrl: '${storage.outputs.primaryBlobEndpoint}azbrief-state'
  archiveContainerUrl: '${storage.outputs.primaryBlobEndpoint}azbrief-archive'
  containerAppsEnvironmentResourceId: environmentId
  containerAppResourceId: containerApp.outputs.resourceId
  containerImage: bootstrapImage
  containerRegistryAuthMode: containerRegistryAuthMode
  webAuthenticationConfigured: adminAuthConfigured
  privateAnonymousWebUiEnabled: privateAnonymousWebUi
  adminUiEnabled: adminUiEnabled
  archiveUiEnabled: archiveUiEnabled
  adminPageUrl: adminUiEnabled ? '${applicationUrl}/admin' : ''
  archivePageUrl: archiveUiEnabled ? '${applicationUrl}/archive' : ''
  adminConfigBlobUrl: adminConfigBlobUrl
  adminReadinessSupportResources: adminReadinessSupportResources
  entraRedirectUri: 'https://${containerApp.outputs.fqdn}/.auth/login/aad/callback'
  applicationUrl: 'https://${containerApp.outputs.fqdn}'
  bootstrapUrl: 'https://${containerApp.outputs.fqdn}'
  controlPlaneIdentityResourceId: controlPlaneIdentity.outputs.resourceId
  controlPlanePrincipalId: controlPlaneIdentity.outputs.principalId
  controlPlaneClientId: controlPlaneIdentity.outputs.clientId
  logAnalyticsWorkspaceResourceId: logAnalyticsWorkspaceResourceId
  controlPlaneRolesRequested: deployCapabilityHost && assignControlPlaneRoles
  schedulerJobRequested: schedulerRequested
  runtimeSetup: {
    schemaVersion: 1
    foundryAccountName: foundryAccountName
    projectName: projectName
    projectResourceId: resourceId('Microsoft.CognitiveServices/accounts/projects', foundryAccountName, projectName)
    projectEndpoint: foundryProjectEndpoint
    hostedAgentName: foundryHostedAgentName
    modelDeploymentName: deployCapabilityHost ? modelDeploymentName : ''
    simpleModelDeploymentName: deployCapabilityHost ? simpleModelDeploymentName : ''
    promptAgentNames: promptAgentNames
    azureMcpServerUrl: azureMcpServerUrl
    azureMcpProjectConnectionName: azureMcpProjectConnectionName
    schedulerJobName: schedulerRequested ? schedulerJobName : ''
    applicationInsightsName: telemetryConfigured ? applicationInsightsName : ''
    publicationRequired: true
    scheduledRunsEnabled: false
  }
}
