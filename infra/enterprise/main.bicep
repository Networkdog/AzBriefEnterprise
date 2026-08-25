/*
  AzBrief Enterprise — multi-agent deployment.

  Topology:
    Container Apps Job (cron schedule)
        -> Microsoft Foundry project (hosted multi-agent)
        -> Azure Communication Services (email delivery)
    Container App (orchestrator API + Admin Page) — manual runs, same image

  The job runs the same image as the app with a different entry point, so the
  schedule inherits the app's identity, network and settings and a run is bounded
  by the job's replica timeout rather than by a shared sandbox. The "analysed up
  to" watermark is a blob in the state storage account, read at run start and
  advanced only after a run completes.

  Security posture (fail-closed defaults):
    * No secret is passed to a runtime component in clear text — every secret
      lives in Key Vault and is projected into the Container App by managed
      identity reference.
    * Foundry uses Entra-only auth (disableLocalAuth), so no API key exists.
    * The state storage account is Entra-only too (allowSharedKeyAccess: false)
      and the identity's write access is scoped to that account alone.
    * The Admin Page stays DISABLED unless an Entra app registration is
      supplied, and even then an explicit principal allow-list is required.
    * The orchestrator API is protected by a generated key and, optionally, by
      an ingress IP allow-list.

  Network isolation (networkIsolationMode):
    * vnetInjection — DEFAULT. Foundry agent compute joins a delegated subnet,
                      the Container Apps environment integrates with the same
                      VNet, and Foundry, Key Vault and the state account are
                      reachable only through private endpoints with private DNS.
                      Network injection can only be set while the Foundry account
                      is created, never added later — which is why it is the
                      default rather than an opt-in.
    * perimeter     — endpoints stay public but Foundry, Key Vault, Log
                      Analytics and the state account join a Network Security
                      Perimeter. Starts in Learning mode so NSPAccessLogs can be
                      reviewed first.
    * public        — public endpoints; authentication is the only boundary.
                      Evaluation and demo use only.

  Compile with:  az bicep build --file infra/enterprise/main.bicep \
                   --outfile infra/azbrief-enterprise-deploy.json
*/

targetScope = 'resourceGroup'

metadata description = 'AzBrief Enterprise — Container Apps Job (scheduler) + Microsoft Foundry hosted multi-agent + Container App (orchestrator/Admin Page) + Communication Services, with Key Vault backed secrets and Entra-only Foundry auth.'

// ============================================================================
// General
// ============================================================================

@description('Base name used to derive every resource name. 3-16 lowercase alphanumeric characters.')
@minLength(3)
@maxLength(16)
param baseName string = 'azbrief'

@description('Location for the regional resources. Defaults to the resource group location.')
param location string = resourceGroup().location

@description('Tags applied to every resource.')
param tags object = {
  Application: 'AzBrief'
  Edition: 'Enterprise'
}

// ============================================================================
// Microsoft Foundry
// ============================================================================

@description('Region for the Microsoft Foundry account. Model availability differs by region — keep the default unless the chosen model is unavailable there.')
param foundryLocation string = location

@description('Model deployment name used by the agents.')
param modelDeploymentName string = 'gpt-4o'

@description('Model to deploy into the Foundry account.')
param modelName string = 'gpt-4o'

@description('Model version. Leave empty to let Azure pick the default version for the model.')
param modelVersion string = ''

@description('Model deployment SKU.')
@allowed([
  'GlobalStandard'
  'Standard'
  'DataZoneStandard'
])
param modelSkuName string = 'GlobalStandard'

@description('Model deployment capacity in thousands of tokens per minute (TPM).')
@minValue(1)
@maxValue(1000)
param modelCapacity int = 30

// ============================================================================
// Container App (orchestrator + Admin Page)
// ============================================================================

@description('Container image for the orchestrator/Admin Page. The default is a placeholder — deploy the real AzBrief image afterwards (see the deployContainerImageCommand output).')
param containerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('Optional private registry login server (e.g. myacr.azurecr.io) pulled with the managed identity. Leave empty for public images.')
param containerRegistryServer string = ''

@description('Minimum replica count. 1 keeps the orchestrator warm; 0 scales to zero between runs.')
@minValue(0)
@maxValue(10)
param minReplicas int = 1

@description('Maximum replica count.')
@minValue(1)
@maxValue(30)
param maxReplicas int = 3

@description('Optional CIDR allow-list for the Container App ingress. Empty means any source may reach the ingress (authentication still applies).')
param allowedIpRanges array = []

@description('Existing subnet resource ID for Container Apps infrastructure (VNet integration). Overrides the subnet the template would otherwise use. Leave empty to let Azure manage the network.')
param infrastructureSubnetId string = ''

@description('Make the Container App ingress internal (VNet-only). The scheduled job is unaffected because it analyses in-process, but /admin and /api/* then resolve only from inside the virtual network.')
param internalIngressOnly bool = false

// ============================================================================
// Network isolation
// ============================================================================

@description('How the topology is protected on the network. "vnetInjection" (default) injects the Foundry agents and the Container App into a virtual network and reaches Foundry, Key Vault and the state account over private endpoints. "perimeter" keeps public endpoints but wraps Foundry, Key Vault and Log Analytics in a Network Security Perimeter. "public" keeps public endpoints with authentication as the only boundary — evaluation use only. Foundry network injection cannot be added after the account exists, so switching to vnetInjection later requires deleting and purging the account.')
@allowed([
  'vnetInjection'
  'perimeter'
  'public'
])
param networkIsolationMode string = 'vnetInjection'

@description('Keep the Foundry data plane reachable from the public internet even in vnetInjection mode. An agent can only be created from a network that can reach the data plane, so the roster cannot be provisioned from a workstation once the account is closed. Turn this on for the initial setup, then redeploy with it off. Agent network injection and the private endpoints are unaffected either way.')
param allowPublicAccessDuringSetup bool = false

@description('Existing virtual network resource ID used when networkIsolationMode is vnetInjection. Leave empty to have the template create one. When supplied, the three subnets named below must already exist with the delegations described in their descriptions.')
param existingVnetResourceId string = ''

@description('Address space of the virtual network the template creates. Must sit inside an RFC1918 range — the Foundry agent subnet rejects anything else.')
param vnetAddressPrefix string = '10.60.0.0/16'

@description('Subnet dedicated to Foundry agent network injection. Delegated to Microsoft.App/environments and never shared with a second Foundry account.')
param agentSubnetName string = 'snet-foundry-agent'

@description('Address prefix for the Foundry agent subnet. /24 is the size Microsoft recommends for this delegation.')
param agentSubnetPrefix string = '10.60.0.0/24'

@description('Subnet hosting the Container Apps environment. Delegated to Microsoft.App/environments.')
param containerAppSubnetName string = 'snet-container-apps'

@description('Address prefix for the Container Apps subnet. /27 is the documented minimum for a workload profiles environment.')
param containerAppSubnetPrefix string = '10.60.1.0/24'

@description('Subnet holding the Foundry and Key Vault private endpoints. Must not be delegated.')
param privateEndpointSubnetName string = 'snet-private-endpoints'

@description('Address prefix for the private endpoint subnet.')
param privateEndpointSubnetPrefix string = '10.60.2.0/27'

@description('Access mode of the Network Security Perimeter associations. Learning (Transition) logs without blocking — read NSPAccessLogs first, then redeploy with Enforced.')
@allowed([
  'Learning'
  'Audit'
  'Enforced'
])
param perimeterAccessMode string = 'Learning'

@description('Inbound CIDR ranges allowed through the Network Security Perimeter. Empty means no IP-based exception.')
param perimeterInboundIpRanges array = []

@description('Subscription IDs whose managed identities may reach the perimeter resources. Empty defaults to the deployment subscription, which is what lets the Container App call Foundry.')
param perimeterAllowedSubscriptions array = []

@description('Outbound FQDNs the perimeter resources may call. Defaults to the two domains AzBrief itself allows for document fetching.')
param perimeterOutboundFqdns array = [
  'azure.microsoft.com'
  'learn.microsoft.com'
]

// ============================================================================
// Admin Page authentication (fail-closed)
// ============================================================================

@description('Entra ID application (client) ID for Admin Page sign-in. Leave empty to keep the Admin Page disabled.')
param adminEntraClientId string = ''

@description('Client secret for the Admin Page app registration. Required when adminEntraClientId is set.')
@secure()
param adminEntraClientSecret string = ''

@description('Comma-separated allow-list of admin principals (UPN/email or object ID). Required for the Admin Page to serve anything.')
param adminAllowedPrincipals string = ''

// ============================================================================
// Email (Azure Communication Services)
// ============================================================================

@description('Data residency for Communication Services.')
@allowed([
  'Africa'
  'Asia Pacific'
  'Australia'
  'Brazil'
  'Canada'
  'Europe'
  'France'
  'Germany'
  'India'
  'Japan'
  'Korea'
  'Norway'
  'Switzerland'
  'UAE'
  'UK'
  'United States'
])
param emailDataLocation string = 'Korea'

@description('Fallback recipient address used when no subscriber list is configured.')
param emailRecipientAddress string = ''

@description('Subscriber list as a JSON array, e.g. [{"email":"a@co.com","name":"A","role":"Cloud Architect","language":"ko"}].')
param subscribers string = ''

// ============================================================================
// Scheduler (Container Apps Job)
// ============================================================================

@description('Cron expression, in UTC, for the daily digest job. Default: 02:00 UTC every day.')
param scheduleCronExpression string = '0 2 * * *'

@description('Seconds a single job execution may run before Container Apps stops it. Keep RUN_TIME_BUDGET_S below this so the run defers leftover updates and commits its checkpoint before the replica is killed.')
@minValue(600)
@maxValue(604800)
param jobReplicaTimeoutSeconds int = 43200

// ============================================================================
// Security
// ============================================================================

@description('API key protecting the orchestrator endpoint. A random value is generated when left at the default.')
@secure()
param orchestratorApiKey string = newGuid()

@description('Enable Key Vault purge protection. Recommended for production; note that a purge-protected vault cannot be deleted for the retention period.')
param enableKeyVaultPurgeProtection bool = false

@description('Key Vault soft-delete retention in days.')
@minValue(7)
@maxValue(90)
param keyVaultRetentionDays int = 7

@description('Log Analytics retention in days.')
@minValue(30)
@maxValue(730)
param logRetentionDays int = 30

// ============================================================================
// Variables
// ============================================================================

var suffix = toLower(uniqueString(resourceGroup().id, baseName))
var shortSuffix = substring(suffix, 0, 8)

var managedIdentityName = 'id-${baseName}'
var logAnalyticsName = 'log-${baseName}-${shortSuffix}'
var appInsightsName = 'appi-${baseName}-${shortSuffix}'
var keyVaultName = take('kv-${baseName}-${shortSuffix}', 24)
var foundryAccountName = 'aif-${baseName}-${shortSuffix}'
var foundryProjectName = '${baseName}-agents'
var containerEnvName = 'cae-${baseName}-${shortSuffix}'
var containerAppName = 'ca-${baseName}'
var communicationServiceName = 'acs-${baseName}-${shortSuffix}'
var emailServiceName = 'acs-email-${baseName}-${shortSuffix}'
var schedulerJobName = 'caj-${baseName}'
var storageAccountName = take('st${toLower(replace(baseName, '-', ''))}${shortSuffix}', 24)
var vnetName = 'vnet-${baseName}-${shortSuffix}'
var perimeterName = 'nsp-${baseName}-${shortSuffix}'
var perimeterProfileName = 'azbrief'

var stateContainerName = 'azbrief-state'
var checkpointBlobUrl = 'https://${storageAccountName}.blob.${environment().suffixes.storage}/${stateContainerName}/checkpoint.json'

// Built-in role definition IDs.
var roleIds = {
  keyVaultSecretsUser: '4633458b-17de-408a-b874-0445c86b69e6'
  foundryUser: '53ca6127-db72-4b80-b1b0-d745d6d5456d'
  cognitiveServicesOpenAIUser: '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
  reader: 'acdd72a7-3385-48ef-bd42-f606fba81ae7'
  acrPull: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
  storageBlobDataContributor: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
}

var hasRegistry = !empty(containerRegistryServer)

var vnetMode = networkIsolationMode == 'vnetInjection'
// Closed only when the isolation mode asks for it AND setup access is not held open.
var foundryPublicAccess = !vnetMode || allowPublicAccessDuringSetup
var perimeterMode = networkIsolationMode == 'perimeter'
var createVnet = vnetMode && empty(existingVnetResourceId)
var vnetResourceId = createVnet ? resourceId('Microsoft.Network/virtualNetworks', vnetName) : existingVnetResourceId
// Built as strings so a bring-your-own VNet in another resource group or
// subscription needs no cross-scope module.
var agentSubnetId = '${vnetResourceId}/subnets/${agentSubnetName}'
var containerAppSubnetId = '${vnetResourceId}/subnets/${containerAppSubnetName}'
var privateEndpointSubnetId = '${vnetResourceId}/subnets/${privateEndpointSubnetName}'
// An explicit infrastructureSubnetId still wins, so an existing deployment that
// already pinned a subnet keeps it.
var effectiveInfrastructureSubnetId = !empty(infrastructureSubnetId)
  ? infrastructureSubnetId
  : (vnetMode ? containerAppSubnetId : '')
var useVnet = !empty(effectiveInfrastructureSubnetId)
var internalIngress = useVnet && internalIngressOnly
// Derived from the effective subnet, so a bring-your-own infrastructureSubnetId
// links its own virtual network instead of the one this template would create.
var containerAppVnetResourceId = useVnet ? split(effectiveInfrastructureSubnetId, '/subnets/')[0] : ''

var perimeterSubscriptions = empty(perimeterAllowedSubscriptions)
  ? [ subscription().subscriptionId ]
  : perimeterAllowedSubscriptions

// Foundry resolves through three names depending on which endpoint the caller
// uses; Key Vault and the checkpoint blob add one each.
var privateDnsZoneNames = [
  'privatelink.services.ai.azure.com'
  'privatelink.openai.azure.com'
  'privatelink.cognitiveservices.azure.com'
  'privatelink.vaultcore.azure.net'
  'privatelink.blob.${environment().suffixes.storage}'
]

// The Admin Page needs an Entra app registration AND an explicit allow-list.
// Missing either one keeps it switched off rather than silently open.
var adminAuthConfigured = !empty(adminEntraClientId) && !empty(adminEntraClientSecret)
var adminUiEnabled = adminAuthConfigured && !empty(adminAllowedPrincipals)

var foundryProjectEndpoint = 'https://${foundryAccountName}.services.ai.azure.com/api/projects/${foundryProjectName}'
var azureOpenAIEndpoint = 'https://${foundryAccountName}.openai.azure.com/'
var containerAppUrl = 'https://${containerApp.properties.configuration.ingress.fqdn}'

// Multi-agent roster consumed by FOUNDRY_AGENTS. Each stage maps to a hosted
// Foundry agent; AzBrief degrades to the single-model path if one is missing.
var foundryAgentRoster = [
  {
    name: '${baseName}-research'
    stage: 'research'
  }
  {
    name: '${baseName}-impact'
    stage: 'impact'
  }
  {
    name: '${baseName}-action'
    stage: 'action'
  }
  {
    name: '${baseName}-review'
    stage: 'review'
  }
]

// An hour of headroom under the replica timeout: the run needs time to defer
// what no longer fits and commit the checkpoint before the replica is killed.
var runTimeBudgetSeconds = jobReplicaTimeoutSeconds - 3600

var ipRestrictions = [
  for (range, i) in allowedIpRanges: {
    name: 'allow-${i}'
    description: 'Operator-supplied allow-list entry'
    ipAddressRange: range
    action: 'Allow'
  }
]

// Shared by the Container App and the scheduler job so the two can never drift
// into analysing with different settings.
var runtimeEnv = [
  { name: 'AZURE_TENANT_ID', value: tenant().tenantId }
  { name: 'AZURE_CLIENT_ID', value: managedIdentity.properties.clientId }
  { name: 'AZURE_SUBSCRIPTION_ID', value: subscription().subscriptionId }
  { name: 'LLM_BACKEND', value: 'foundry' }
  { name: 'FOUNDRY_PROJECT_ENDPOINT', value: foundryProjectEndpoint }
  { name: 'FOUNDRY_MODEL_DEPLOYMENT', value: modelDeploymentName }
  { name: 'FOUNDRY_AGENTS', value: string(foundryAgentRoster) }
  { name: 'AZURE_OPENAI_ENDPOINT', value: azureOpenAIEndpoint }
  { name: 'AZURE_OPENAI_DEPLOYMENT_NAME', value: modelDeploymentName }
  { name: 'CHECKPOINT_BLOB_URL', value: checkpointBlobUrl }
  { name: 'RUN_TIME_BUDGET_S', value: string(runTimeBudgetSeconds) }
  { name: 'COMMUNICATION_SERVICES_ENDPOINT', value: 'https://${communicationService.properties.hostName}' }
  { name: 'COMMUNICATION_SERVICES_CONNECTION_STRING', secretRef: 'acs-connection-string' }
  { name: 'EMAIL_SENDER_ADDRESS', value: 'DoNotReply@${emailDomain.properties.fromSenderDomain}' }
  { name: 'EMAIL_RECIPIENT_ADDRESS', value: emailRecipientAddress }
  { name: 'SUBSCRIBERS', value: subscribers }
  { name: 'API_KEY', secretRef: 'orchestrator-api-key' }
  { name: 'ADMIN_UI_ENABLED', value: string(adminUiEnabled) }
  { name: 'ADMIN_ALLOWED_PRINCIPALS', value: adminAllowedPrincipals }
  { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
  { name: 'OTEL_ENABLED', value: 'true' }
  { name: 'LOG_LEVEL', value: 'INFO' }
  { name: 'LOG_FILE_ENABLED', value: 'false' }
]

var keyVaultSecretRefs = [
  {
    name: 'acs-connection-string'
    keyVaultUrl: secretAcsConnectionString.properties.secretUri
    identity: managedIdentity.id
  }
  {
    name: 'orchestrator-api-key'
    keyVaultUrl: secretOrchestratorApiKey.properties.secretUri
    identity: managedIdentity.id
  }
]

// ============================================================================
// Identity
// ============================================================================

resource managedIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: managedIdentityName
  location: location
  tags: tags
}

// ============================================================================
// Networking — only materialises for networkIsolationMode = vnetInjection
// ============================================================================

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = if (createVnet) {
  name: vnetName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [
        vnetAddressPrefix
      ]
    }
    subnets: [
      {
        name: agentSubnetName
        properties: {
          addressPrefix: agentSubnetPrefix
          delegations: [
            {
              name: 'Microsoft.App.environments'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
      {
        name: containerAppSubnetName
        properties: {
          addressPrefix: containerAppSubnetPrefix
          delegations: [
            {
              name: 'Microsoft.App.environments'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
      {
        name: privateEndpointSubnetName
        properties: {
          addressPrefix: privateEndpointSubnetPrefix
          // A private endpoint cannot be placed in a subnet that still enforces
          // network policies on private endpoints.
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}

resource privateDnsZones 'Microsoft.Network/privateDnsZones@2024-06-01' = [
  for zone in privateDnsZoneNames: if (vnetMode) {
    name: zone
    location: 'global'
    tags: tags
  }
]

resource privateDnsZoneLinks 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = [
  #disable-next-line use-parent-property
  for (zone, i) in privateDnsZoneNames: if (vnetMode) {
    name: '${privateDnsZones[i].name}/link-${shortSuffix}'
    location: 'global'
    properties: {
      registrationEnabled: false
      virtualNetwork: {
        id: vnetResourceId
      }
    }
    dependsOn: [
      vnet
    ]
  }
]

resource foundryPrivateEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = if (vnetMode) {
  name: 'pe-${foundryAccountName}'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: privateEndpointSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'foundry'
        properties: {
          privateLinkServiceId: foundryAccount.id
          groupIds: [
            'account'
          ]
        }
      }
    ]
  }
  // modelDeployment, not just the account: a Cognitive Services account accepts
  // only ONE operation at a time, and the account PUT returns while the account
  // is still 'Accepted'. Attaching a private endpoint to an account in that
  // state fails with AccountProvisioningStateInvalid.
  dependsOn: [
    vnet
    modelDeployment
  ]
}

resource foundryPrivateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = if (vnetMode) {
  parent: foundryPrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'services-ai'
        properties: {
          privateDnsZoneId: privateDnsZones[0].id
        }
      }
      {
        name: 'openai'
        properties: {
          privateDnsZoneId: privateDnsZones[1].id
        }
      }
      {
        name: 'cognitiveservices'
        properties: {
          privateDnsZoneId: privateDnsZones[2].id
        }
      }
    ]
  }
}

resource keyVaultPrivateEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = if (vnetMode) {
  name: 'pe-${keyVaultName}'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: privateEndpointSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'keyvault'
        properties: {
          privateLinkServiceId: keyVault.id
          groupIds: [
            'vault'
          ]
        }
      }
    ]
  }
  dependsOn: [
    vnet
  ]
}

resource keyVaultPrivateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = if (vnetMode) {
  parent: keyVaultPrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'vault'
        properties: {
          privateDnsZoneId: privateDnsZones[3].id
        }
      }
    ]
  }
}

resource storagePrivateEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = if (vnetMode) {
  name: 'pe-${storageAccountName}'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: privateEndpointSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'blob'
        properties: {
          privateLinkServiceId: storageAccount.id
          groupIds: [
            'blob'
          ]
        }
      }
    ]
  }
  dependsOn: [
    vnet
  ]
}

resource storagePrivateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = if (vnetMode) {
  parent: storagePrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'blob'
        properties: {
          privateDnsZoneId: privateDnsZones[4].id
        }
      }
    ]
  }
}

// ============================================================================
// Observability
// ============================================================================

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: logRetentionDays
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
    DisableLocalAuth: true
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

// ============================================================================
// Key Vault — single home for every runtime secret
// ============================================================================

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: tenant().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: keyVaultRetentionDays
    // ARM rejects an explicit false here — the property may only be true or absent.
    enablePurgeProtection: enableKeyVaultPurgeProtection ? true : null
    // 'Disabled' still lets the Key Vault resource provider write the secrets this
    // template declares — trusted-service traffic is exempt from the block.
    publicNetworkAccess: vnetMode ? 'Disabled' : 'Enabled'
    networkAcls: {
      defaultAction: vnetMode ? 'Deny' : 'Allow'
      bypass: 'AzureServices'
    }
  }
}

resource secretAcsConnectionString 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'acs-connection-string'
  properties: {
    value: communicationService.listKeys().primaryConnectionString
    contentType: 'Azure Communication Services connection string'
  }
}

resource secretOrchestratorApiKey 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'orchestrator-api-key'
  properties: {
    value: orchestratorApiKey
    contentType: 'AzBrief orchestrator API key'
  }
}

resource secretAdminClientSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (adminAuthConfigured) {
  parent: keyVault
  name: 'admin-entra-client-secret'
  properties: {
    value: adminEntraClientSecret
    contentType: 'Entra ID app registration client secret (Admin Page)'
  }
}

// ============================================================================
// Microsoft Foundry — account, project, model deployment
// ============================================================================

resource foundryAccount 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: foundryAccountName
  location: foundryLocation
  tags: tags
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    // Required for the .services.ai.azure.com / .openai.azure.com endpoints.
    customSubDomainName: foundryAccountName
    // Enables Foundry projects (hosted agents) on this account.
    allowProjectManagement: true
    // Entra-only: no API key exists to leak or rotate.
    disableLocalAuth: true
    publicNetworkAccess: foundryPublicAccess ? 'Enabled' : 'Disabled'
    networkAcls: {
      defaultAction: foundryPublicAccess ? 'Allow' : 'Deny'
      bypass: 'AzureServices'
    }
    // Agent compute joins the delegated subnet. Network injection can only be
    // set while the account is being created; it cannot be added afterwards.
    networkInjections: vnetMode
      ? [
          {
            scenario: 'agent'
            subnetArmId: agentSubnetId
            useMicrosoftManagedNetwork: false
          }
        ]
      : null
  }
  dependsOn: [
    vnet
  ]
}

// Everything below hangs off the Foundry account, which serialises operations
// on its own: two children deployed in parallel make the second one fail with
// RequestConflict. ARM parallelises by default, so the chain is explicit —
// model deployment, then the private endpoint and its DNS, then the project.
resource modelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: foundryAccount
  name: modelDeploymentName
  sku: {
    name: modelSkuName
    capacity: modelCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: modelName
      version: empty(modelVersion) ? null : modelVersion
    }
    versionUpgradeOption: 'OnceCurrentVersionExpired'
  }
}

resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: foundryAccount
  name: foundryProjectName
  location: foundryLocation
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    displayName: 'AzBrief agents'
    description: 'Hosted multi-agent workspace for AzBrief Azure Update analysis'
  }
  // A conditional resource that is not deployed drops out of dependsOn, so this
  // stays correct when networkIsolationMode is not vnetInjection.
  dependsOn: [
    modelDeployment
    foundryPrivateDnsZoneGroup
  ]
}

// A network-injected account needs an explicit Agents capability host on the
// project. The account-level host is auto-created by the resource provider.
resource foundryProjectCapabilityHost 'Microsoft.CognitiveServices/accounts/projects/capabilityHosts@2025-04-01-preview' = if (vnetMode) {
  parent: foundryProject
  name: 'caphostproj'
  properties: {
    // The Bicep type for this preview API predates the property; ARM requires it.
    #disable-next-line BCP037
    capabilityHostKind: 'Agents'
  }
}

// ============================================================================
// Azure Communication Services (email delivery)
// ============================================================================

resource emailService 'Microsoft.Communication/emailServices@2023-04-01' = {
  name: emailServiceName
  location: 'global'
  tags: tags
  properties: {
    dataLocation: emailDataLocation
  }
}

resource emailDomain 'Microsoft.Communication/emailServices/domains@2023-04-01' = {
  parent: emailService
  name: 'AzureManagedDomain'
  location: 'global'
  tags: tags
  properties: {
    domainManagement: 'AzureManaged'
    userEngagementTracking: 'Disabled'
  }
}

resource communicationService 'Microsoft.Communication/communicationServices@2023-04-01' = {
  name: communicationServiceName
  location: 'global'
  tags: tags
  properties: {
    dataLocation: emailDataLocation
    linkedDomains: [
      emailDomain.id
    ]
  }
}

// ============================================================================
// State store — durable digest checkpoint
// ============================================================================

// The digest checkpoint lives here. It is the only state that has to survive a
// restart: the run registry may be lost, because a stale checkpoint costs
// duplicate analysis rather than a skipped update.
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    // Entra-only, matching the Foundry account: there is no account key to leak.
    allowSharedKeyAccess: false
    publicNetworkAccess: vnetMode ? 'Disabled' : 'Enabled'
    networkAcls: {
      defaultAction: vnetMode ? 'Deny' : 'Allow'
      bypass: 'AzureServices'
    }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
}

resource stateContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: stateContainerName
  properties: {
    publicAccess: 'None'
  }
}

// ============================================================================
// Container Apps
// ============================================================================

resource containerEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: containerEnvName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
    vnetConfiguration: useVnet
      ? {
          infrastructureSubnetId: effectiveInfrastructureSubnetId
          internal: internalIngressOnly
        }
      : null
    // A workload profiles environment is what makes the /27 delegated subnet
    // legal; a consumption-only environment would demand a /23 without one.
    workloadProfiles: vnetMode
      ? [
          {
            name: 'Consumption'
            workloadProfileType: 'Consumption'
          }
        ]
      : null
    zoneRedundant: false
  }
  dependsOn: [
    vnet
  ]
}

// An internal environment is not resolvable from public DNS. Without this zone
// nothing inside the VNet could reach the orchestrator either.
module containerEnvDns 'modules/internal-ingress-dns.bicep' = if (internalIngress) {
  name: 'azbrief-internal-ingress-dns'
  params: {
    defaultDomain: containerEnv.properties.defaultDomain
    staticIp: containerEnv.properties.staticIp
    vnetResourceId: containerAppVnetResourceId
    linkSuffix: shortSuffix
    tags: tags
  }
}

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: containerAppName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerEnv.id
    workloadProfileName: vnetMode ? 'Consumption' : null
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: !internalIngress
        targetPort: 8000
        allowInsecure: false
        transport: 'auto'
        ipSecurityRestrictions: empty(allowedIpRanges) ? null : ipRestrictions
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
      registries: hasRegistry
        ? [
            {
              server: containerRegistryServer
              identity: managedIdentity.id
            }
          ]
        : null
      secrets: concat(
        keyVaultSecretRefs,
        adminAuthConfigured
          ? [
              {
                name: 'microsoft-provider-authentication-secret'
                keyVaultUrl: secretAdminClientSecret!.properties.secretUri
                identity: managedIdentity.id
              }
            ]
          : []
      )
    }
    template: {
      containers: [
        {
          name: 'azbrief'
          image: containerImage
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
          env: runtimeEnv
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              initialDelaySeconds: 20
              periodSeconds: 30
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              initialDelaySeconds: 10
              periodSeconds: 10
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
      }
    }
  }
  dependsOn: [
    keyVaultSecretsUserAssignment
  ]
}

// Entra ID sign-in for the Admin Page. Absent unless an app registration was
// supplied — the app itself then refuses to serve /admin.
//
// AllowAnonymous, not RedirectToLoginPage: platform-level "require
// authentication" applies to EVERY request, which would bounce an API-key call
// to /api/* into an interactive login. Instead the sidecar validates whatever
// token is presented and injects the principal headers, and the application
// authorizes — browsers hitting /admin are sent to the provider by the app
// itself.
resource containerAppAuth 'Microsoft.App/containerApps/authConfigs@2024-03-01' = if (adminAuthConfigured) {
  parent: containerApp
  name: 'current'
  properties: {
    platform: {
      enabled: true
    }
    globalValidation: {
      unauthenticatedClientAction: 'AllowAnonymous'
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: adminEntraClientId
          clientSecretSettingName: 'microsoft-provider-authentication-secret'
          openIdIssuer: '${environment().authentication.loginEndpoint}${tenant().tenantId}/v2.0'
        }
        validation: {
          allowedAudiences: [
            'api://${adminEntraClientId}'
            adminEntraClientId
          ]
        }
      }
    }
    login: {
      preserveUrlFragmentsForLogins: false
    }
  }
}

// ============================================================================
// Scheduler — Container Apps Job
// ============================================================================

// Same image, same identity and same environment (so the same VNet) as the
// orchestrator; only the entry point differs. replicaRetryLimit is 0 on purpose:
// a failed execution did not advance the checkpoint, so the next schedule
// re-covers the window instead of paying for the same analysis twice in a night.
resource schedulerJob 'Microsoft.App/jobs@2024-03-01' = {
  name: schedulerJobName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentity.id}': {}
    }
  }
  properties: {
    environmentId: containerEnv.id
    workloadProfileName: vnetMode ? 'Consumption' : null
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: jobReplicaTimeoutSeconds
      // No automatic retry: a failed execution left the checkpoint untouched, so
      // the next scheduled run re-covers the window without paying for the same
      // analysis twice in one night.
      replicaRetryLimit: 0
      scheduleTriggerConfig: {
        cronExpression: scheduleCronExpression
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: hasRegistry
        ? [
            {
              server: containerRegistryServer
              identity: managedIdentity.id
            }
          ]
        : null
      secrets: keyVaultSecretRefs
    }
    template: {
      containers: [
        {
          name: 'azbrief-scheduler'
          image: containerImage
          command: [
            'python'
            '-m'
            'src.scheduler'
          ]
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
          env: runtimeEnv
        }
      ]
    }
  }
  dependsOn: [
    keyVaultSecretsUserAssignment
    storageBlobDataContributorAssignment
  ]
}

// ============================================================================
// Role assignments (least privilege, resource-scoped)
// ============================================================================

resource keyVaultSecretsUserAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: keyVault
  name: guid(keyVault.id, managedIdentity.id, roleIds.keyVaultSecretsUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.keyVaultSecretsUser)
    principalId: managedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Scoped to the state account only. The checkpoint is not a secret, so it does
// not earn write access to the vault that holds the real ones.
resource storageBlobDataContributorAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storageAccount
  name: guid(storageAccount.id, managedIdentity.id, roleIds.storageBlobDataContributor)
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      roleIds.storageBlobDataContributor
    )
    principalId: managedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource foundryUserAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: foundryAccount
  name: guid(foundryAccount.id, managedIdentity.id, roleIds.foundryUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.foundryUser)
    principalId: managedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
  // Last in the account's serialised chain — a role assignment against an
  // account still mid-provision is rejected like any other operation.
  dependsOn: [
    foundryProject
  ]
}

resource openAIUserAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: foundryAccount
  name: guid(foundryAccount.id, managedIdentity.id, roleIds.cognitiveServicesOpenAIUser)
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      roleIds.cognitiveServicesOpenAIUser
    )
    principalId: managedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
  dependsOn: [
    foundryUserAssignment
  ]
}

// Resource-group Reader so the app can inspect its own deployment. Tenant- or
// subscription-wide Reader stays a deliberate, separately granted step.
resource resourceGroupReaderAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, managedIdentity.id, roleIds.reader)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.reader)
    principalId: managedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ============================================================================
// Network Security Perimeter — only for networkIsolationMode = perimeter
// ============================================================================

resource networkPerimeter 'Microsoft.Network/networkSecurityPerimeters@2024-07-01' = if (perimeterMode) {
  name: perimeterName
  location: location
  tags: tags
  properties: {}
}

resource perimeterProfile 'Microsoft.Network/networkSecurityPerimeters/profiles@2024-07-01' = if (perimeterMode) {
  parent: networkPerimeter
  name: perimeterProfileName
  properties: {}
}

// Managed-identity callers from these subscriptions. This is the rule that keeps
// the Container App able to reach Foundry once the perimeter is enforced.
resource perimeterSubscriptionRule 'Microsoft.Network/networkSecurityPerimeters/profiles/accessRules@2024-07-01' = if (perimeterMode) {
  parent: perimeterProfile
  name: 'inbound-subscriptions'
  properties: {
    direction: 'Inbound'
    subscriptions: [
      for subscriptionId in perimeterSubscriptions: {
        id: '/subscriptions/${subscriptionId}'
      }
    ]
  }
}

resource perimeterInboundIpRule 'Microsoft.Network/networkSecurityPerimeters/profiles/accessRules@2024-07-01' = if (perimeterMode && !empty(perimeterInboundIpRanges)) {
  parent: perimeterProfile
  name: 'inbound-ip'
  properties: {
    direction: 'Inbound'
    addressPrefixes: perimeterInboundIpRanges
  }
}

resource perimeterOutboundFqdnRule 'Microsoft.Network/networkSecurityPerimeters/profiles/accessRules@2024-07-01' = if (perimeterMode && !empty(perimeterOutboundFqdns)) {
  parent: perimeterProfile
  name: 'outbound-fqdn'
  properties: {
    direction: 'Outbound'
    fullyQualifiedDomainNames: perimeterOutboundFqdns
  }
}

resource foundryPerimeterAssociation 'Microsoft.Network/networkSecurityPerimeters/resourceAssociations@2024-07-01' = if (perimeterMode) {
  parent: networkPerimeter
  name: 'assoc-foundry'
  properties: {
    privateLinkResource: {
      id: foundryAccount.id
    }
    profile: {
      id: perimeterProfile.id
    }
    accessMode: perimeterAccessMode
  }
}

resource keyVaultPerimeterAssociation 'Microsoft.Network/networkSecurityPerimeters/resourceAssociations@2024-07-01' = if (perimeterMode) {
  parent: networkPerimeter
  name: 'assoc-keyvault'
  properties: {
    privateLinkResource: {
      id: keyVault.id
    }
    profile: {
      id: perimeterProfile.id
    }
    accessMode: perimeterAccessMode
  }
}

// The log destination belongs inside the same perimeter as the resources it
// records; otherwise the access logs themselves become an egress exception.
resource logAnalyticsPerimeterAssociation 'Microsoft.Network/networkSecurityPerimeters/resourceAssociations@2024-07-01' = if (perimeterMode) {
  parent: networkPerimeter
  name: 'assoc-loganalytics'
  properties: {
    privateLinkResource: {
      id: logAnalytics.id
    }
    profile: {
      id: perimeterProfile.id
    }
    accessMode: perimeterAccessMode
  }
}

resource storagePerimeterAssociation 'Microsoft.Network/networkSecurityPerimeters/resourceAssociations@2024-07-01' = if (perimeterMode) {
  parent: networkPerimeter
  name: 'assoc-storage'
  properties: {
    privateLinkResource: {
      id: storageAccount.id
    }
    profile: {
      id: perimeterProfile.id
    }
    accessMode: perimeterAccessMode
  }
}

// Learning mode is only useful if the decisions are readable — NSPAccessLogs is
// what tells you which rules you still need before switching to Enforced.
resource perimeterDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = if (perimeterMode) {
  scope: networkPerimeter
  name: 'nsp-access-logs'
  properties: {
    workspaceId: logAnalytics.id
    logs: [
      {
        categoryGroup: 'allLogs'
        enabled: true
      }
    ]
  }
}

// ============================================================================
// Outputs
// ============================================================================

@description('Container App URL hosting the orchestrator API and Admin Page.')
output containerAppUrl string = internalIngress ? '${containerAppUrl} (VNet-only — reachable from inside the virtual network)' : containerAppUrl

@description('Network isolation applied to this deployment.')
output networkIsolationMode string = networkIsolationMode

@description('Virtual network carrying the Foundry agent subnet and the Container Apps environment. Empty unless networkIsolationMode is vnetInjection.')
output virtualNetworkId string = vnetMode ? vnetResourceId : ''

@description('Subnet delegated to Foundry agent network injection.')
output foundryAgentSubnetId string = vnetMode ? agentSubnetId : ''

@description('Network Security Perimeter guarding Foundry, Key Vault and Log Analytics. Empty unless networkIsolationMode is perimeter.')
output networkSecurityPerimeterName string = perimeterMode ? perimeterName : ''

@description('Command that switches the perimeter associations from Learning to Enforced once NSPAccessLogs looks clean.')
output enforcePerimeterCommand string = perimeterMode
  ? 'az network perimeter association update --name assoc-foundry --perimeter-name ${perimeterName} --resource-group ${resourceGroup().name} --access-mode Enforced'
  : '(networkIsolationMode is not "perimeter")'

@description('Admin Page URL. Serves content only when Entra sign-in and an allow-list are configured.')
output adminPageUrl string = adminUiEnabled ? '${containerAppUrl}/admin' : '(disabled — supply adminEntraClientId, adminEntraClientSecret and adminAllowedPrincipals)'

@description('Microsoft Foundry project endpoint used by the hosted agents.')
output foundryProjectEndpoint string = foundryProjectEndpoint

@description('Azure OpenAI compatible endpoint on the same Foundry account.')
output azureOpenAIEndpoint string = azureOpenAIEndpoint

@description('Azure managed email sender domain.')
output emailSenderDomain string = emailDomain.properties.fromSenderDomain

@description('Managed identity principal ID — grant it Reader on the subscriptions AzBrief should analyse.')
output managedIdentityPrincipalId string = managedIdentity.properties.principalId

@description('Key Vault holding every runtime secret.')
output keyVaultName string = keyVault.name

@description('Container Apps Job that runs the daily digest.')
output schedulerJobName string = schedulerJob.name

@description('Cron expression (UTC) the scheduler job runs on.')
output scheduleCronExpression string = scheduleCronExpression

@description('Blob holding the digest checkpoint. Delete it to re-analyse from the default window.')
output checkpointBlobUrl string = checkpointBlobUrl

@description('Command that starts a digest run immediately instead of waiting for the schedule.')
output runNowCommand string = 'az containerapp job start --name ${schedulerJobName} --resource-group ${resourceGroup().name}'

@description('Command that grants the identity subscription-wide Reader access.')
output grantReaderCommand string = 'az role assignment create --assignee ${managedIdentity.properties.principalId} --role Reader --scope /subscriptions/${subscription().subscriptionId}'

@description('Command that lets the identity pull from your registry. The template wires the registry reference but cannot assign a role on a registry it does not own.')
output grantAcrPullCommand string = hasRegistry
  ? 'az role assignment create --assignee ${managedIdentity.properties.principalId} --role AcrPull --scope $(az acr show --name ${split(containerRegistryServer, '.')[0]} --query id -o tsv)'
  : '(no containerRegistryServer supplied)'

@description('Commands that roll the real AzBrief image onto BOTH the app and the scheduler job. Updating only the app leaves the nightly digest on the previous build.')
output deployContainerImageCommand string = 'az containerapp update --name ${containerAppName} --resource-group ${resourceGroup().name} --image <your-registry>/azbrief-enterprise:latest ; az containerapp job update --name ${schedulerJobName} --resource-group ${resourceGroup().name} --image <your-registry>/azbrief-enterprise:latest'

@description('Command that creates the hosted agent roster. ARM cannot: agents are data-plane objects, so the project stays empty until this runs.')
output provisionAgentsCommand string = 'FOUNDRY_PROJECT_ENDPOINT=${foundryProjectEndpoint} python -m scripts.provision_foundry_agents'

@description('Post-deployment checklist.')
output nextSteps string = '1) Run grantReaderCommand so Resource Graph queries can see your resources. 2) Push the AzBrief image, run grantAcrPullCommand, then deployContainerImageCommand — it updates the app AND the scheduler job, which runs "python -m src.scheduler" and does nothing useful until the real image is in place. 3) Run provisionAgentsCommand to create the ${baseName}-research/-impact/-action/-review agents: the template configures FOUNDRY_AGENTS but ARM cannot create the agents themselves, and without them every analysis silently falls back to the single-model path. 4) Optional: register an Entra app and redeploy with adminEntraClientId/Secret plus adminAllowedPrincipals to switch the Admin Page on. 5) networkIsolationMode=vnetInjection: the data plane is private, so run step 3 from inside the virtual network — or deploy once with allowPublicAccessDuringSetup=true, provision the agents, and redeploy with it off. 6) networkIsolationMode=perimeter: review NSPAccessLogs, then run enforcePerimeterCommand for each association.'
