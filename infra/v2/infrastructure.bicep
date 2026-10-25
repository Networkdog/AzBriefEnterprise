targetScope = 'resourceGroup'

metadata description = 'One-time AzBrief v2 infrastructure. Foundation and completion are internal orchestration stages; application containers and Agent versions are excluded.'

@allowed(['foundation', 'complete'])
param infrastructureStage string = 'foundation'
@allowed([true])
@description('Read-only Portal validation result. The guarded CLI validates before submitting; false always blocks deployment.')
param portalValidationPassed bool = true
@allowed(['cli', 'portal'])
@description('Portal input views require the generated server-side policy before any resource write. CLI retains its guarded preflight.')
param portalVerificationMode string = 'cli'
@description('Allow-listed non-secret Portal observations. Never include credentials or raw error bodies.')
param portalEvidence object = {}
@description('Separately evaluated non-secret ARM observations; independent of ordinary input changes.')
param portalResourceEvidence object = {}
@maxLength(10)
param namingPrefix string = ''
@maxLength(10)
param namingSuffix string = ''
@description('Infrastructure region, independent of model selection and RG metadata. Existing VNet uses its actual region; same-account model deployments inherit their parent Foundry region, not a model-only location.')
param location string = 'eastus'
@allowed(['existing', 'new'])
param virtualNetworkMode string = 'existing'
param virtualNetworkResourceGroupName string
param virtualNetworkName string
param virtualNetworkAddressPrefix string = '10.240.0.0/16'
param peSubnetName string = ''
param peSubnetAddressPrefix string = '10.240.1.32/28'
param foundrySubnetName string = ''
param foundrySubnetAddressPrefix string = '10.240.0.0/24'
param containerAppsSubnetName string = ''
param containerAppsSubnetAddressPrefix string = '10.240.1.0/27'
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
@allowed(['new', 'existing'])
param foundryAccountMode string = 'new'
param existingFoundryAccountResourceId string = ''
@allowed(['new', 'existing'])
param storageAccountMode string = 'new'
param existingStorageAccountResourceId string = ''
@allowed(['new', 'existing'])
param containerAppsEnvironmentMode string = 'new'
param existingContainerAppsEnvironmentResourceId string = ''
param existingLogAnalyticsWorkspaceResourceId string = ''
@allowed(['new', 'existing'])
param communicationServicesMode string = 'new'
param existingCommunicationServicesResourceId string = ''
param existingEmailDomainResourceId string = ''
param stateContainerName string = ''
param archiveContainerName string = ''
@allowed(['Africa', 'Asia Pacific', 'Australia', 'Brazil', 'Canada', 'Europe', 'France', 'Germany', 'India', 'Japan', 'Korea', 'Norway', 'Switzerland', 'UAE', 'UK', 'United States'])
param emailDataLocation string = 'Asia Pacific'
@allowed(['basic', 'standard'])
param searchSku string = 'basic'
param existingPrivateDnsZoneIds object = {}
param deployContainerAppsPrivateEndpoint bool = false
@description('Explicit initial Foundry inbound allow-list: publicIpRanges and existing subnetResourceIds. Empty retains private-only access. New managed-data accounts only; outbound injection and private Storage/MCP remain unchanged.')
param foundryPublishingAccess object = {}
param modelDeploymentName string = 'gpt-5-terra'
param simpleModelDeploymentName string = 'gpt-5-luna'
param coreModelName string = 'gpt-5.2'
param simpleModelName string = 'gpt-5-mini'
param modelVersion string = '2025-12-11'
@description('Simple model version; empty uses modelVersion for legacy-compatible deployment.')
param simpleModelVersion string = '2025-08-07'
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
@description('Optional preapproved setup-only identity/subnet/file-storage ARM IDs. Empty retains the legacy authorized CLI step. No directory permissions or setup networking are created.')
param mcpAutomation object = {}
param tags object = {
  application: 'AzBrief'
  customer: 'KT'
  deploymentProfile: 'kt-private-v2'
}

var complete = infrastructureStage == 'complete'
module portalVerification 'infrastructure.portal-policy.json' = if (portalVerificationMode == 'portal') {
  name: 'v2-portal-verification'
  params: {
    evidence: union(portalEvidence, portalResourceEvidence)
    inputs: {
      infrastructureStage: infrastructureStage
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
      projectName: projectName
      containerAppsEnvironmentName: containerAppsEnvironmentName
      containerAppName: containerAppName
      schedulerJobName: schedulerJobName
      controlPlaneIdentityName: controlPlaneIdentityName
      logAnalyticsWorkspaceName: logAnalyticsWorkspaceName
      applicationInsightsName: applicationInsightsName
      communicationServicesName: communicationServicesName
      emailServiceName: emailServiceName
      modelDeploymentName: modelDeploymentName
      simpleModelDeploymentName: simpleModelDeploymentName
      foundryHostedAgentName: foundryHostedAgentName
      azureMcpContainerAppName: azureMcpContainerAppName
      azureMcpIdentityName: azureMcpIdentityName
      azureMcpProjectConnectionName: azureMcpProjectConnectionName
      deployContainerAppsPrivateEndpoint: deployContainerAppsPrivateEndpoint
      existingPrivateDnsZoneIds: existingPrivateDnsZoneIds
      emailDataLocation: emailDataLocation
      searchSku: searchSku
      coreModelName: coreModelName
      simpleModelName: simpleModelName
      modelVersion: modelVersion
      modelCapacity: modelCapacity
      simpleModelCapacity: simpleModelCapacity
      namingPrefix: namingPrefix
      namingSuffix: namingSuffix
      useMicrosoftManagedAgentData: useMicrosoftManagedAgentData
      virtualNetworkMode: virtualNetworkMode
      virtualNetworkAddressPrefix: virtualNetworkAddressPrefix
      foundrySubnetAddressPrefix: foundrySubnetAddressPrefix
      containerAppsSubnetAddressPrefix: containerAppsSubnetAddressPrefix
      peSubnetAddressPrefix: peSubnetAddressPrefix
      simpleModelVersion: simpleModelVersion
      foundryAccountMode: foundryAccountMode
      existingFoundryAccountResourceId: existingFoundryAccountResourceId
      storageAccountMode: storageAccountMode
      existingStorageAccountResourceId: existingStorageAccountResourceId
      containerAppsEnvironmentMode: containerAppsEnvironmentMode
      existingContainerAppsEnvironmentResourceId: existingContainerAppsEnvironmentResourceId
      communicationServicesMode: communicationServicesMode
      existingCommunicationServicesResourceId: existingCommunicationServicesResourceId
      existingLogAnalyticsWorkspaceResourceId: existingLogAnalyticsWorkspaceResourceId
      existingEmailDomainResourceId: existingEmailDomainResourceId
      mcpAutomation: mcpAutomation
    }
  }
}
var workloadKey = uniqueString(resourceGroup().id)
var resourceTags = union(tags, { application: 'AzBrief', customer: 'KT', deploymentProfile: 'kt-private-v2' })
var accountId = foundryAccountMode == 'existing' ? existingFoundryAccountResourceId : resourceId('Microsoft.CognitiveServices/accounts', foundryAccountName)
var environmentId = containerAppsEnvironmentMode == 'existing' ? existingContainerAppsEnvironmentResourceId : resourceId('Microsoft.App/managedEnvironments', containerAppsEnvironmentName)
var storageId = storageAccountMode == 'existing' ? existingStorageAccountResourceId : resourceId('Microsoft.Storage/storageAccounts', storageAccountName)
var communicationId = communicationServicesMode == 'existing' ? existingCommunicationServicesResourceId : resourceId('Microsoft.Communication/communicationServices', communicationServicesName)
var workspaceId = containerAppsEnvironmentMode == 'existing' ? existingLogAnalyticsWorkspaceResourceId : resourceId('Microsoft.OperationalInsights/workspaces', logAnalyticsWorkspaceName)
var domainId = communicationServicesMode == 'existing' ? existingEmailDomainResourceId : resourceId('Microsoft.Communication/emailServices/domains', emailServiceName, 'AzureManagedDomain')
var stateName = !empty(stateContainerName) ? stateContainerName : (storageAccountMode == 'existing' ? 'azbrief-state-${toLower(containerAppName)}' : 'azbrief-state')
var archiveName = !empty(archiveContainerName) ? archiveContainerName : (storageAccountMode == 'existing' ? 'azbrief-archive-${toLower(containerAppName)}' : 'azbrief-archive')
var projectId = '${accountId}/projects/${projectName}'
var foundrySubdomain = foundryAccountMode == 'existing' ? account.properties.customSubDomainName : foundryAccountName
var projectEndpoint = 'https://${foundrySubdomain}.services.ai.azure.com/api/projects/${projectName}'
var sharedFoundryInjections = foundryAccountMode == 'existing' ? filter(account.properties.networkInjections ?? [], injection => injection.scenario == 'agent') : []
var boundFoundrySubnetId = foundryAccountMode == 'existing' ? (sharedFoundryInjections[?0].?subnetArmId ?? '') : ''
var boundEnvironmentSubnetId = containerAppsEnvironmentMode == 'existing' ? (containerEnvironment.properties.?vnetConfiguration.?infrastructureSubnetId ?? '') : ''
var networkResourceGroup = virtualNetworkMode == 'new' ? resourceGroup().name : virtualNetworkResourceGroupName
var networkName = virtualNetworkName
var vnetId = resourceId(networkResourceGroup, 'Microsoft.Network/virtualNetworks', networkName)
var deploymentLocation = toLower(replace(location, ' ', ''))
var resolvedFoundrySubnetName = !empty(foundrySubnetName) ? foundrySubnetName : last(split(boundFoundrySubnetId, '/'))
var resolvedEnvironmentSubnetName = !empty(containerAppsSubnetName) ? containerAppsSubnetName : last(split(boundEnvironmentSubnetId, '/'))
var foundrySubnetId = foundryAccountMode == 'existing' ? boundFoundrySubnetId : '${vnetId}/subnets/${resolvedFoundrySubnetName}'
var environmentSubnetId = containerAppsEnvironmentMode == 'existing' ? boundEnvironmentSubnetId : '${vnetId}/subnets/${resolvedEnvironmentSubnetName}'
var privateEndpointSubnetRequired = foundryAccountMode == 'new' || storageAccountMode == 'new' || !useMicrosoftManagedAgentData || (containerAppsEnvironmentMode == 'new' && deployContainerAppsPrivateEndpoint)
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
var deploymentOptions = union({
  foundryAccountMode: foundryAccountMode
  storageAccountMode: storageAccountMode
  containerAppsEnvironmentMode: containerAppsEnvironmentMode
  communicationServicesMode: communicationServicesMode
  virtualNetworkMode: virtualNetworkMode
  virtualNetworkAddressPrefix: virtualNetworkAddressPrefix
  peSubnetAddressPrefix: peSubnetAddressPrefix
  foundrySubnetAddressPrefix: foundrySubnetAddressPrefix
  containerAppsSubnetAddressPrefix: containerAppsSubnetAddressPrefix
  useMicrosoftManagedAgentData: useMicrosoftManagedAgentData
  emailDataLocation: emailDataLocation
  searchSku: searchSku
  coreModelName: coreModelName
  simpleModelName: simpleModelName
  modelVersion: modelVersion
  simpleModelVersion: empty(simpleModelVersion) ? modelVersion : simpleModelVersion
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
}, empty(mcpAutomation) ? {} : { mcpAutomation: mcpAutomation }, empty(foundryPublishingAccess) ? {} : { foundryPublishingAccess: publishingPolicy.outputs.normalizedAccess })
var promptAgentNames = {
  coordinator: '${take(projectName, 47)}-coordinator'
  resource_graph: '${take(projectName, 47)}-resource-graph'
  azure_mcp: '${take(projectName, 47)}-azure-mcp'
  azure_api: '${take(projectName, 47)}-azure-api'
  report_writer: '${take(projectName, 47)}-report-writer'
  quality_reviewer: '${take(projectName, 47)}-quality-reviewer'
}

module publishingPolicy 'foundry-publishing-access.bicep' = {
  name: 'v2-foundry-publishing-policy'
  params: {
    access: foundryPublishingAccess
    savedAccess: savedOptions.?foundryPublishingAccess ?? {}
    completing: complete
    newAccount: foundryAccountMode == 'new'
    managedAgentData: useMicrosoftManagedAgentData
  }
  dependsOn: [portalVerification]
}

resource networkBindingValidation 'Microsoft.Resources/deployments@2025-04-01' = {
  name: 'v2-network-binding-validation'
  dependsOn: [portalVerification, publishingPolicy]
  properties: {
    mode: 'Incremental'
    expressionEvaluationOptions: { scope: 'inner' }
    parameters: {
      sharedFoundryPrivateAgentSubnetReady: {
        value: foundryAccountMode == 'existing' ? (length(sharedFoundryInjections) == 1 && sharedFoundryInjections[?0].?useMicrosoftManagedNetwork == false && account.properties.provisioningState == 'Succeeded' && account.properties.publicNetworkAccess == 'Disabled' && account.properties.allowProjectManagement == true && account.properties.disableLocalAuth == true && toLower(account.id) == toLower(accountId) && toLower(foundrySubnetId) == toLower('${vnetId}/subnets/${resolvedFoundrySubnetName}') && toLower(replace(account.location, ' ', '')) == deploymentLocation) : true
      }
      sharedEnvironmentPrivateSubnetReady: {
        value: containerAppsEnvironmentMode == 'existing' ? (containerEnvironment.properties.provisioningState == 'Succeeded' && containerEnvironment.properties.publicNetworkAccess == 'Disabled' && containerEnvironment.properties.?vnetConfiguration.?internal == true && toLower(containerEnvironment.id) == toLower(environmentId) && toLower(environmentSubnetId) == toLower('${vnetId}/subnets/${resolvedEnvironmentSubnetName}') && toLower(replace(containerEnvironment.location, ' ', '')) == deploymentLocation && !deployContainerAppsPrivateEndpoint) : true
      }
      requiredNetworkRolesSelected: {
        value: !empty(networkResourceGroup) && !empty(networkName) && !empty(resolvedFoundrySubnetName) && !empty(resolvedEnvironmentSubnetName) && (!privateEndpointSubnetRequired || !empty(peSubnetName)) && (virtualNetworkMode != 'new' || (foundryAccountMode == 'new' && containerAppsEnvironmentMode == 'new' && !empty(peSubnetName)))
      }
    }
    template: {
      '$schema': 'https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#'
      contentVersion: '1.0.0.0'
      parameters: {
        sharedFoundryPrivateAgentSubnetReady: {
          type: 'bool'
          allowedValues: [true]
          metadata: { description: 'Shared Foundry requires one ready, private agent injection on the exact selected same-region VNet/subnet. Existing parents cannot move.' }
        }
        sharedEnvironmentPrivateSubnetReady: {
          type: 'bool'
          allowedValues: [true]
          metadata: { description: 'Shared Environment requires its ready internal/PNA-disabled same-region infrastructure subnet; existing networking is retained.' }
        }
        requiredNetworkRolesSelected: {
          type: 'bool'
          allowedValues: [true]
          metadata: { description: 'New Foundry/Environment require their dedicated subnets; a Private Endpoint subnet is required only for endpoints this workload creates.' }
        }
      }
      resources: []
    }
  }
}

module network 'new-network.bicep' = if (!complete && virtualNetworkMode == 'new') {
  name: 'v2-new-network'
  params: {
    location: deploymentLocation
    virtualNetworkName: networkName
    virtualNetworkAddressPrefix: virtualNetworkAddressPrefix
    peSubnetName: peSubnetName
    peSubnetAddressPrefix: peSubnetAddressPrefix
    foundrySubnetName: resolvedFoundrySubnetName
    foundrySubnetAddressPrefix: foundrySubnetAddressPrefix
    containerAppsSubnetName: resolvedEnvironmentSubnetName
    containerAppsSubnetAddressPrefix: containerAppsSubnetAddressPrefix
    tags: resourceTags
  }
  dependsOn: [networkBindingValidation]
}

module foundation 'foundation.bicep' = if (!complete) {
  name: 'v2-foundation'
  params: {
    location: deploymentLocation
    virtualNetworkResourceGroupName: networkResourceGroup
    virtualNetworkName: networkName
    peSubnetName: peSubnetName
    foundrySubnetName: resolvedFoundrySubnetName
    containerAppsSubnetName: resolvedEnvironmentSubnetName
    foundryAccountName: foundryAccountName
    foundryPublishingPublicNetworkAccess: publishingPolicy.outputs.publicNetworkAccess
    foundryPublishingNetworkAcls: publishingPolicy.outputs.networkAcls
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
    foundryAccountMode: foundryAccountMode
    storageAccountMode: storageAccountMode
    containerAppsEnvironmentMode: containerAppsEnvironmentMode
    communicationServicesMode: communicationServicesMode
    existingContainerAppsEnvironmentResourceId: containerAppsEnvironmentMode == 'existing' ? environmentId : ''
    existingLogAnalyticsWorkspaceResourceId: containerAppsEnvironmentMode == 'existing' ? workspaceId : ''
    existingCommunicationServicesResourceId: communicationServicesMode == 'existing' ? communicationId : ''
    existingEmailDomainResourceId: communicationServicesMode == 'existing' ? domainId : ''
    tags: resourceTags
  }
  dependsOn: [network, networkBindingValidation]
}

module mcpIdentity 'mcp-identity.bicep' = if (!complete && manageAzureMcp) {
  name: 'v2-mcp-identity'
  params: {
    name: azureMcpIdentityName
    location: deploymentLocation
    tags: resourceTags
  }
  dependsOn: [networkBindingValidation]
}

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  scope: resourceGroup(split(accountId, '/')[2], split(accountId, '/')[4])
  name: foundryAccountName
}
resource containerEnvironment 'Microsoft.App/managedEnvironments@2026-01-01' existing = {
  scope: resourceGroup(split(environmentId, '/')[2], split(environmentId, '/')[4])
  name: containerAppsEnvironmentName
}
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: controlPlaneIdentityName
}
resource insights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: applicationInsightsName
}
resource communication 'Microsoft.Communication/communicationServices@2023-04-01' existing = {
  scope: resourceGroup(split(communicationId, '/')[2], split(communicationId, '/')[4])
  name: communicationServicesName
}
resource emailDomain 'Microsoft.Communication/emailServices/domains@2023-04-01' existing = {
  scope: resourceGroup(split(domainId, '/')[2], split(domainId, '/')[4])
  name: '${split(domainId, '/')[8]}/${split(domainId, '/')[10]}'
}

module models '../kt/models.bicep' = if (complete) {
  name: foundryAccountMode == 'existing' ? 'v2-required-models-${workloadKey}' : 'v2-required-models'
  scope: resourceGroup(split(accountId, '/')[2], split(accountId, '/')[4])
  params: {
    foundryAccountName: foundryAccountName
    coreDeploymentName: modelDeploymentName
    simpleDeploymentName: simpleModelDeploymentName
    coreModelName: coreModelName
    simpleModelName: simpleModelName
    modelVersion: modelVersion
    simpleModelVersion: empty(simpleModelVersion) ? modelVersion : simpleModelVersion
    modelCapacity: modelCapacity
    simpleModelCapacity: simpleModelCapacity
    reuseCore: portalVerificationMode == 'portal' ? portalVerification!.outputs.reuseCoreModel : reuseCoreModel
    reuseSimple: portalVerificationMode == 'portal' ? portalVerification!.outputs.reuseSimpleModel : reuseSimpleModel
    useCoreForSimple: useCoreForSimpleModel
  }
  dependsOn: [networkBindingValidation]
}

module project 'foundry-workload.bicep' = if (complete) {
  name: foundryAccountMode == 'existing' ? 'v2-foundry-workload-${workloadKey}' : 'v2-foundry-workload'
  scope: resourceGroup(split(accountId, '/')[2], split(accountId, '/')[4])
  params: {
    foundryAccountName: foundryAccountName
    projectName: projectName
    location: deploymentLocation
    storageAccountName: storageAccountName
    cosmosAccountName: cosmosAccountName
    searchServiceName: searchServiceName
    useMicrosoftManagedAgentData: useMicrosoftManagedAgentData
    reuseExistingFoundrySetup: reuseExistingFoundrySetup
    reuseExistingTelemetryConnection: reuseExistingTelemetryConnection
    applicationInsightsResourceId: insights.id
    controlPlanePrincipalId: identity.properties.principalId
    sharedAccount: foundryAccountMode == 'existing'
    tags: union(resourceTags, { azbriefWorkloadResourceGroup: resourceGroup().id })
  }
  dependsOn: [models]
}

var projectPrincipalId = complete ? project!.outputs.principalId : ''

module sharedStorage 'storage-workload.bicep' = if (storageAccountMode == 'existing') {
  name: 'v2-shared-storage-workload-${workloadKey}'
  scope: resourceGroup(split(storageId, '/')[2], split(storageId, '/')[4])
  params: {
    storageAccountName: storageAccountName
    stateContainerName: stateName
    archiveContainerName: archiveName
    principalId: complete ? identity.properties.principalId : foundation!.outputs.identityPrincipalId
    createContainers: !complete
    assignRoles: complete
  }
  dependsOn: [networkBindingValidation]
}
module sharedStorageReader 'shared-observer.bicep' = if (complete && storageAccountMode == 'existing') {
  name: 'v2-shared-storage-reader-${workloadKey}'
  scope: resourceGroup(split(storageId, '/')[2], split(storageId, '/')[4])
  params: {
    storageAccountName: storageAccountName
    principalId: identity.properties.principalId
  }
  dependsOn: [networkBindingValidation]
}
module sharedEnvironmentReader 'shared-observer.bicep' = if (complete && containerAppsEnvironmentMode == 'existing') {
  name: 'v2-shared-environment-reader-${workloadKey}'
  scope: resourceGroup(split(environmentId, '/')[2], split(environmentId, '/')[4])
  params: {
    environmentName: containerAppsEnvironmentName
    principalId: identity.properties.principalId
  }
  dependsOn: [networkBindingValidation]
}
module sharedCommunicationAccess 'communication-access.bicep' = if (complete && communicationServicesMode == 'existing') {
  name: 'v2-shared-communication-access-${workloadKey}'
  scope: resourceGroup(split(communicationId, '/')[2], split(communicationId, '/')[4])
  params: {
    communicationServicesName: communicationServicesName
    principalId: identity.properties.principalId
  }
  dependsOn: [networkBindingValidation]
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
    assignFoundryRole: foundryAccountMode == 'new'
    assignStorageRoles: storageAccountMode == 'new'
    assignEmailRole: communicationServicesMode == 'new'
  }
  dependsOn: [project]
}

var infrastructureContract = {
  schemaVersion: 2
  profile: 'kt-private-v2'
  infrastructureComplete: complete
  mcpAuthenticationPending: complete && manageAzureMcp && empty(mcpAutomation)
  portalValidationPassed: portalValidationPassed
  applicationReady: false
  hostedRuntimeRolesPending: true
  deploymentName: deployment().name
  tenantId: tenant().tenantId
  subscriptionId: subscription().subscriptionId
  resourceGroup: resourceGroup().name
  location: deploymentLocation
  tags: resourceTags
  virtualNetworkResourceGroupName: networkResourceGroup
  virtualNetworkName: networkName
  virtualNetworkResourceId: vnetId
  foundrySubnetName: resolvedFoundrySubnetName
  foundrySubnetResourceId: foundrySubnetId
  containerAppsSubnetName: resolvedEnvironmentSubnetName
  infrastructureSubnetId: environmentSubnetId
  peSubnetName: peSubnetName
  privateEndpointSubnetId: empty(peSubnetName) ? '' : '${vnetId}/subnets/${peSubnetName}'
  deployContainerAppsPrivateEndpoint: deployContainerAppsPrivateEndpoint
  containerAppsEnvironmentName: containerAppsEnvironmentName
  containerAppsEnvironmentResourceId: containerEnvironment.id
  foundryAccountResourceId: accountId
  foundryAccountSubdomain: foundrySubdomain
  storageAccountResourceId: storageId
  communicationServicesResourceId: communicationId
  logAnalyticsWorkspaceResourceId: workspaceId
  emailDomainResourceId: domainId
  stateContainerName: stateName
  archiveContainerName: archiveName
  environmentDefaultDomain: complete ? containerEnvironment.properties.defaultDomain : foundation!.outputs.environmentDefaultDomain
  containerAppName: containerAppName
  schedulerJobName: schedulerJobName
  controlPlaneIdentityName: controlPlaneIdentityName
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
  stateContainerUrl: 'https://${storageAccountName}.blob.${environment().suffixes.storage}/${stateName}'
  archiveContainerUrl: 'https://${storageAccountName}.blob.${environment().suffixes.storage}/${archiveName}'
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
  dependsOn: [mcpIdentity, sharedStorage]
}

module handoff 'resource-group-tags.bicep' = if (complete) {
  name: 'v2-infrastructure-tags'
  params: {
    infrastructure: infrastructureContract
    infrastructureState: manageAzureMcp ? 'mcp_pending' : 'ready'
    infrastructureOptions: deploymentOptions
  }
  dependsOn: [controlPlaneRoles, projectTelemetryRole, project, sharedStorage, sharedStorageReader, sharedEnvironmentReader, sharedCommunicationAccess]
}

module automaticMcp 'mcp-authentication-automatic.bicep' = if (complete && manageAzureMcp && !empty(mcpAutomation)) {
  name: 'v2-mcp-automatic-authentication'
  params: {
    infrastructure: infrastructureContract
    infrastructureOptions: deploymentOptions
    automation: mcpAutomation
    mcpPrincipalId: mcpRuntimeIdentity!.properties.principalId
  }
  dependsOn: [handoff]
}

resource mcpRuntimeIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = if (complete && manageAzureMcp && !empty(mcpAutomation)) {
  name: azureMcpIdentityName
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
  location: deploymentLocation
  runtimeSetup: {
    schemaVersion: 1
    foundryAccountName: foundryAccountName
    foundryAccountResourceId: accountId
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