targetScope = 'resourceGroup'

metadata description = 'One-time AzBrief v2 infrastructure. Foundation and completion are internal orchestration stages; application containers and Agent versions are excluded.'

@allowed(['foundation', 'complete'])
param infrastructureStage string = 'foundation'
@allowed([true])
@description('Read-only Portal validation result. The guarded CLI validates before submitting; false always blocks deployment.')
param portalValidationPassed bool = true
@maxLength(10)
param namingPrefix string = ''
@maxLength(10)
param namingSuffix string = ''
param location string
param virtualNetworkResourceGroupName string
param virtualNetworkName string
param peSubnetName string
param foundrySubnetName string
param containerAppsSubnetName string
param foundryAccountName string
@minLength(3)
@maxLength(24)
param storageAccountName string
@description('Use the official Basic VNet Capability Host with no customer agent-data connections. Existing BYO installs must retain false.')
param useMicrosoftManagedAgentData bool = true
param cosmosAccountName string = ''
param searchServiceName string = ''
param projectName string
param containerAppsEnvironmentName string
param containerAppName string
param schedulerJobName string = 'job-${containerAppName}'
param controlPlaneIdentityName string = 'id-${containerAppName}'
param logAnalyticsWorkspaceName string = 'log-${containerAppName}'
param applicationInsightsName string = 'appi-${containerAppName}'
param communicationServicesName string = 'acs-${containerAppName}'
param emailServiceName string = 'email-${containerAppName}'
@allowed(['Africa', 'Asia Pacific', 'Australia', 'Brazil', 'Canada', 'Europe', 'France', 'Germany', 'India', 'Japan', 'Korea', 'Norway', 'Switzerland', 'UAE', 'UK', 'United States'])
param emailDataLocation string = 'Asia Pacific'
@allowed(['basic', 'standard'])
param searchSku string = 'basic'
param existingPrivateDnsZoneIds object = {}
param deployContainerAppsPrivateEndpoint bool = false
param modelDeploymentName string = 'gpt-5-terra'
param simpleModelDeploymentName string = 'gpt-5-luna'
param coreModelName string = 'gpt-5.6-terra'
param simpleModelName string = 'gpt-5.6-luna'
param modelVersion string = '2026-07-09'
@minValue(1)
@maxValue(1000)
param modelCapacity int = 200
@minValue(1)
@maxValue(1000)
param simpleModelCapacity int = 200
param reuseCoreModel bool = false
param reuseSimpleModel bool = false
param useCoreForSimpleModel bool = false
param reuseExistingFoundrySetup bool = false
param reuseExistingTelemetryConnection bool = false
param foundryHostedAgentName string = 'azbrief-analysis-hosted'
param azureMcpServerUrl string = ''
param azureMcpProjectConnectionName string = 'azure-mcp-read-only'
param azureMcpContainerAppName string = 'ca-azbrief-mcp-v2'
param azureMcpIdentityName string = 'id-azbrief-mcp-v2'
param tags object = {
  application: 'AzBrief'
  customer: 'KT'
  deploymentProfile: 'kt-private-v2'
}

var complete = infrastructureStage == 'complete'
var resourceTags = union(tags, { application: 'AzBrief', customer: 'KT', deploymentProfile: 'kt-private-v2' })
var projectId = resourceId('Microsoft.CognitiveServices/accounts/projects', foundryAccountName, projectName)
var projectEndpoint = 'https://${foundryAccountName}.services.ai.azure.com/api/projects/${projectName}'
var vnetId = resourceId(virtualNetworkResourceGroupName, 'Microsoft.Network/virtualNetworks', virtualNetworkName)
var reuseFoundry = complete && reuseExistingFoundrySetup
var manageAzureMcp = empty(azureMcpServerUrl)
var savedOptions = complete ? json(concat(
  resourceGroup().tags[?'azbrief.v2.options0'] ?? '{}',
  resourceGroup().tags[?'azbrief.v2.options1'] ?? '',
  resourceGroup().tags[?'azbrief.v2.options2'] ?? '',
  resourceGroup().tags[?'azbrief.v2.options3'] ?? '',
  resourceGroup().tags[?'azbrief.v2.options4'] ?? '',
  resourceGroup().tags[?'azbrief.v2.options5'] ?? '',
  resourceGroup().tags[?'azbrief.v2.options6'] ?? '',
  resourceGroup().tags[?'azbrief.v2.options7'] ?? ''
)) : {}
var mcpClientId = manageAzureMcp && complete ? (savedOptions.?azureMcpClientId ?? '') : ''
var mcpServicePrincipalId = manageAzureMcp && complete ? (savedOptions.?azureMcpServicePrincipalId ?? '') : ''
var mcpAppRoleId = manageAzureMcp && complete ? (savedOptions.?azureMcpAppRoleId ?? '') : ''
var deploymentOptions = {
  useMicrosoftManagedAgentData: useMicrosoftManagedAgentData
  emailDataLocation: emailDataLocation
  searchSku: searchSku
  coreModelName: coreModelName
  simpleModelName: simpleModelName
  modelVersion: modelVersion
  modelCapacity: modelCapacity
  simpleModelCapacity: simpleModelCapacity
  existingPrivateDnsZoneIds: existingPrivateDnsZoneIds
  tags: resourceTags
  namingPrefix: namingPrefix
  namingSuffix: namingSuffix
  manageAzureMcp: manageAzureMcp
  azureMcpClientId: mcpClientId
  azureMcpServicePrincipalId: mcpServicePrincipalId
  azureMcpAppRoleId: mcpAppRoleId
}
var promptAgentNames = {
  coordinator: '${take(projectName, 47)}-coordinator'
  resource_graph: '${take(projectName, 47)}-resource-graph'
  azure_mcp: '${take(projectName, 47)}-azure-mcp'
  azure_api: '${take(projectName, 47)}-azure-api'
  report_writer: '${take(projectName, 47)}-report-writer'
  quality_reviewer: '${take(projectName, 47)}-quality-reviewer'
}

module foundation 'foundation.bicep' = if (!complete) {
  name: 'v2-foundation'
  params: {
    location: location
    virtualNetworkResourceGroupName: virtualNetworkResourceGroupName
    virtualNetworkName: virtualNetworkName
    peSubnetName: peSubnetName
    foundrySubnetName: foundrySubnetName
    containerAppsSubnetName: containerAppsSubnetName
    foundryAccountName: foundryAccountName
    storageAccountName: storageAccountName
    cosmosAccountName: cosmosAccountName
    searchServiceName: searchServiceName
    useMicrosoftManagedAgentData: useMicrosoftManagedAgentData
    containerAppsEnvironmentName: containerAppsEnvironmentName
    controlPlaneIdentityName: controlPlaneIdentityName
    logAnalyticsWorkspaceName: logAnalyticsWorkspaceName
    applicationInsightsName: applicationInsightsName
    communicationServicesName: communicationServicesName
    emailServiceName: emailServiceName
    emailDataLocation: emailDataLocation
    searchSku: searchSku
    existingPrivateDnsZoneIds: existingPrivateDnsZoneIds
    deployContainerAppsPrivateEndpoint: deployContainerAppsPrivateEndpoint
    tags: resourceTags
  }
}

module mcpIdentity 'mcp-identity.bicep' = if (!complete && manageAzureMcp) {
  name: 'v2-mcp-identity'
  params: {
    name: azureMcpIdentityName
    location: location
    tags: resourceTags
  }
}

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryAccountName
}
resource containerEnvironment 'Microsoft.App/managedEnvironments@2026-01-01' existing = {
  name: containerAppsEnvironmentName
}
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: controlPlaneIdentityName
}
resource insights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: applicationInsightsName
}
resource communication 'Microsoft.Communication/communicationServices@2023-04-01' existing = {
  name: communicationServicesName
}
resource emailDomain 'Microsoft.Communication/emailServices/domains@2023-04-01' existing = {
  name: '${emailServiceName}/AzureManagedDomain'
}

module models '../kt/models.bicep' = if (complete) {
  name: 'v2-required-models'
  params: {
    foundryAccountName: foundryAccountName
    coreDeploymentName: modelDeploymentName
    simpleDeploymentName: simpleModelDeploymentName
    coreModelName: coreModelName
    simpleModelName: simpleModelName
    modelVersion: modelVersion
    modelCapacity: modelCapacity
    simpleModelCapacity: simpleModelCapacity
    reuseCore: reuseCoreModel
    reuseSimple: reuseSimpleModel
    useCoreForSimple: useCoreForSimpleModel
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = if (complete && !reuseFoundry) {
  parent: account
  name: projectName
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: projectName
    description: useMicrosoftManagedAgentData ? 'AzBrief v2 Basic VNet project with Microsoft-managed agent data.' : 'AzBrief v2 project with customer-owned agent storage.'
  }
  dependsOn: [models]
}

#disable-next-line use-resource-symbol-reference
var projectPrincipalId = complete ? (reuseFoundry ? reference(projectId, '2025-06-01', 'full').identity.principalId : project!.identity.principalId) : ''
#disable-next-line BCP053 use-resource-symbol-reference
var projectInternalId = complete && !useMicrosoftManagedAgentData ? (reuseFoundry ? reference(projectId, '2025-06-01', 'full').properties.internalId : project!.properties.internalId) : ''

module bindings '../kt/agent-bindings.bicep' = if (complete && !useMicrosoftManagedAgentData) {
  name: 'v2-agent-bindings'
  params: {
    foundryAccountName: foundryAccountName
    projectName: projectName
    projectPrincipalId: projectPrincipalId
    agentStorageAccountName: storageAccountName
    cosmosAccountName: cosmosAccountName
    searchServiceName: searchServiceName
    createConnections: !reuseFoundry
  }
}

module capabilityHost '../kt/capability-host.bicep' = if (complete && !useMicrosoftManagedAgentData) {
  name: 'v2-project-host'
  params: {
    foundryAccountName: foundryAccountName
    projectName: projectName
    projectPrincipalId: projectPrincipalId
    projectInternalId: projectInternalId
    agentStorageAccountName: storageAccountName
    cosmosAccountName: cosmosAccountName
    createHost: !reuseFoundry
  }
  dependsOn: [bindings]
}

module managedCapabilityHost 'managed-capability-host.bicep' = if (complete && useMicrosoftManagedAgentData) {
  name: 'v2-managed-project-host'
  params: {
    foundryAccountName: foundryAccountName
    projectName: projectName
    createHost: !reuseFoundry
  }
  dependsOn: [project]
}

resource telemetryConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = if (complete && !reuseExistingTelemetryConnection) {
  #disable-next-line use-parent-property
  name: '${foundryAccountName}/${projectName}/azbrief-app-insights'
  properties: {
    category: 'AppInsights'
    #disable-next-line BCP036
    authType: 'ProjectManagedIdentity'
    target: insights.id
    isSharedToAll: true
    metadata: {
      ApiType: 'Azure'
      ResourceId: insights.id
      ApplicationInsightsConnectionString: insights.properties.ConnectionString
      location: insights.location
    }
  }
  dependsOn: [project]
}

module projectTelemetryRole 'monitoring-role.bicep' = if (complete) {
  name: 'v2-project-telemetry-role'
  params: {
    applicationInsightsName: applicationInsightsName
    principalId: projectPrincipalId
  }
}

module controlPlaneRoles '../kt/control-plane-roles.bicep' = if (complete) {
  name: 'v2-control-plane-roles'
  params: {
    foundryAccountName: foundryAccountName
    projectName: projectName
    storageAccountName: storageAccountName
    controlPlanePrincipalId: identity.properties.principalId
    applicationInsightsName: applicationInsightsName
    communicationServicesName: communicationServicesName
    emailSenderRoleDefinitionId: guid(toLower(resourceGroup().id), toLower(communicationServicesName), 'azbrief-email-sender')
  }
  dependsOn: [capabilityHost, managedCapabilityHost]
}

var infrastructureContract = {
  schemaVersion: 2
  profile: 'kt-private-v2'
  infrastructureComplete: complete
  mcpAuthenticationPending: complete && manageAzureMcp
  portalValidationPassed: portalValidationPassed
  applicationReady: false
  hostedRuntimeRolesPending: true
  deploymentName: deployment().name
  tenantId: tenant().tenantId
  subscriptionId: subscription().subscriptionId
  resourceGroup: resourceGroup().name
  location: location
  tags: resourceTags
  virtualNetworkResourceId: vnetId
  foundrySubnetResourceId: '${vnetId}/subnets/${foundrySubnetName}'
  infrastructureSubnetId: '${vnetId}/subnets/${containerAppsSubnetName}'
  privateEndpointSubnetId: '${vnetId}/subnets/${peSubnetName}'
  deployContainerAppsPrivateEndpoint: deployContainerAppsPrivateEndpoint
  containerAppsEnvironmentName: containerAppsEnvironmentName
  containerAppsEnvironmentResourceId: containerEnvironment.id
  environmentDefaultDomain: complete ? containerEnvironment.properties.defaultDomain : foundation!.outputs.environmentDefaultDomain
  containerAppName: containerAppName
  schedulerJobName: schedulerJobName
  controlPlaneIdentityResourceId: identity.id
  controlPlanePrincipalId: complete ? identity.properties.principalId : foundation!.outputs.identityPrincipalId
  controlPlaneClientId: complete ? identity.properties.clientId : foundation!.outputs.identityClientId
  storageAccountName: storageAccountName
  cosmosAccountName: useMicrosoftManagedAgentData ? '' : cosmosAccountName
  searchServiceName: useMicrosoftManagedAgentData ? '' : searchServiceName
  useMicrosoftManagedAgentData: useMicrosoftManagedAgentData
  logAnalyticsWorkspaceName: logAnalyticsWorkspaceName
  applicationInsightsName: applicationInsightsName
  communicationServicesName: communicationServicesName
  emailServiceName: emailServiceName
  communicationServicesEndpoint: complete ? 'https://${communication.properties.hostName}' : foundation!.outputs.emailEndpoint
  emailSenderAddress: complete ? 'DoNotReply@${emailDomain.properties.fromSenderDomain}' : foundation!.outputs.emailSenderAddress
  stateContainerUrl: 'https://${storageAccountName}.blob.${environment().suffixes.storage}/azbrief-state'
  archiveContainerUrl: 'https://${storageAccountName}.blob.${environment().suffixes.storage}/azbrief-archive'
  foundryAccountName: foundryAccountName
  projectName: projectName
  foundryProjectResourceId: projectId
  foundryProjectEndpoint: projectEndpoint
  foundryProjectPrincipalId: projectPrincipalId
  foundryHostedAgentName: foundryHostedAgentName
  modelDeploymentName: modelDeploymentName
  simpleModelDeploymentName: simpleModelDeploymentName
  promptAgentNames: promptAgentNames
  azureMcpServerUrl: manageAzureMcp ? 'https://${azureMcpContainerAppName}.${complete ? containerEnvironment.properties.defaultDomain : foundation!.outputs.environmentDefaultDomain}' : azureMcpServerUrl
  azureMcpProjectConnectionName: azureMcpProjectConnectionName
  azureMcpEntraAppUniqueName: 'azbrief-v2-mcp-${uniqueString(resourceGroup().id)}'
  azureMcpContainerAppName: azureMcpContainerAppName
  azureMcpIdentityName: azureMcpIdentityName
}

module foundationHandoff 'resource-group-tags.bicep' = if (!complete) {
  name: 'v2-foundation-tags'
  params: {
    infrastructure: infrastructureContract
    infrastructureState: 'foundation'
    infrastructureOptions: deploymentOptions
  }
  dependsOn: [mcpIdentity]
}

module handoff 'resource-group-tags.bicep' = if (complete) {
  name: 'v2-infrastructure-tags'
  params: {
    infrastructure: infrastructureContract
    infrastructureState: manageAzureMcp ? 'mcp_pending' : 'ready'
    infrastructureOptions: deploymentOptions
  }
  dependsOn: [controlPlaneRoles, projectTelemetryRole, telemetryConnection]
}

output infrastructure object = infrastructureContract
output ktFoundation object = {
  schemaVersion: 1
  profile: 'kt-private-foundation'
  capabilityHostRequested: complete
  applicationReady: false
  tenantId: tenant().tenantId
  subscriptionId: subscription().subscriptionId
  resourceGroup: resourceGroup().name
  location: location
  runtimeSetup: {
    schemaVersion: 1
    foundryAccountName: foundryAccountName
    projectName: projectName
    projectResourceId: projectId
    projectEndpoint: projectEndpoint
    hostedAgentName: foundryHostedAgentName
    modelDeploymentName: modelDeploymentName
    simpleModelDeploymentName: simpleModelDeploymentName
    promptAgentNames: promptAgentNames
    azureMcpServerUrl: infrastructureContract.azureMcpServerUrl
    azureMcpProjectConnectionName: azureMcpProjectConnectionName
    schedulerJobName: schedulerJobName
    applicationInsightsName: applicationInsightsName
    publicationRequired: true
    scheduledRunsEnabled: false
  }
}