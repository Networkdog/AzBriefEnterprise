targetScope = 'resourceGroup'

metadata description = 'Resume only the exact successful pending core with an explicitly preauthorized setup identity. No core, model, application or permission replay.'

param automation object
@allowed([true])
param portalValidationPassed bool

var savedTags = resourceGroup().?tags ?? {}
var bindings = json(concat(
  savedTags[?'azbrief.v2.resources0'] ?? '{}',
  savedTags[?'azbrief.v2.resources1'] ?? '',
  savedTags[?'azbrief.v2.resources2'] ?? '',
  savedTags[?'azbrief.v2.resources3'] ?? '',
  savedTags[?'azbrief.v2.resources4'] ?? '',
  savedTags[?'azbrief.v2.resources5'] ?? '',
  savedTags[?'azbrief.v2.resources6'] ?? '',
  savedTags[?'azbrief.v2.resources7'] ?? ''
))
var options = json(concat(
  savedTags[?'azbrief.v2.options0'] ?? '{}',
  savedTags[?'azbrief.v2.options1'] ?? '',
  savedTags[?'azbrief.v2.options2'] ?? '',
  savedTags[?'azbrief.v2.options3'] ?? '',
  savedTags[?'azbrief.v2.options4'] ?? '',
  savedTags[?'azbrief.v2.options5'] ?? '',
  savedTags[?'azbrief.v2.options6'] ?? '',
  savedTags[?'azbrief.v2.options7'] ?? ''
))
resource core 'Microsoft.Resources/deployments@2025-04-01' existing = {
  name: savedTags['azbrief.v2.deployment']
}
var infrastructure = core.properties.outputs.infrastructure.value
var accountId = bindings.?foundryAccountResourceId ?? resourceId('Microsoft.CognitiveServices/accounts', savedTags['azbrief.v2.foundry'])
var savedNames = {
  location: savedTags['azbrief.v2.location']
  containerAppsEnvironmentName: savedTags['azbrief.v2.environment']
  containerAppName: savedTags['azbrief.v2.app']
  schedulerJobName: savedTags['azbrief.v2.job']
  controlPlaneIdentityName: savedTags['azbrief.v2.identity']
  storageAccountName: savedTags['azbrief.v2.storage']
  cosmosAccountName: savedTags['azbrief.v2.cosmos']
  searchServiceName: savedTags['azbrief.v2.search']
  logAnalyticsWorkspaceName: savedTags['azbrief.v2.workspace']
  applicationInsightsName: savedTags['azbrief.v2.insights']
  communicationServicesName: savedTags['azbrief.v2.acs']
  emailServiceName: savedTags['azbrief.v2.email']
  foundryAccountName: savedTags['azbrief.v2.foundry']
  projectName: savedTags['azbrief.v2.project']
  foundryHostedAgentName: savedTags['azbrief.v2.hosted']
  modelDeploymentName: savedTags['azbrief.v2.coreModel']
  simpleModelDeploymentName: savedTags['azbrief.v2.simpleModel']
  virtualNetworkResourceGroupName: savedTags['azbrief.v2.vnetRg']
  virtualNetworkName: savedTags['azbrief.v2.vnet']
  peSubnetName: savedTags['azbrief.v2.peSubnet']
  foundrySubnetName: savedTags['azbrief.v2.foundrySubnet']
  containerAppsSubnetName: savedTags['azbrief.v2.appSubnet']
}
var expectedIds = {
  foundryAccountResourceId: accountId
  containerAppsEnvironmentResourceId: bindings.?containerAppsEnvironmentResourceId ?? resourceId('Microsoft.App/managedEnvironments', savedNames.containerAppsEnvironmentName)
  storageAccountResourceId: bindings.?storageAccountResourceId ?? resourceId('Microsoft.Storage/storageAccounts', savedNames.storageAccountName)
  communicationServicesResourceId: bindings.?communicationServicesResourceId ?? resourceId('Microsoft.Communication/communicationServices', savedNames.communicationServicesName)
  logAnalyticsWorkspaceResourceId: bindings.?logAnalyticsWorkspaceResourceId ?? resourceId('Microsoft.OperationalInsights/workspaces', savedNames.logAnalyticsWorkspaceName)
  emailDomainResourceId: bindings.?emailDomainResourceId ?? resourceId('Microsoft.Communication/emailServices/domains', savedNames.emailServiceName, 'AzureManagedDomain')
}
resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' existing = {
  scope: resourceGroup(split(accountId, '/')[2], split(accountId, '/')[4])
  name: '${savedTags['azbrief.v2.foundry']}/${savedTags['azbrief.v2.project']}'
}
resource mcpIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: savedTags['azbrief.v2.mcpIdentity']
}
resource exactPendingCore 'Microsoft.Resources/deployments@2025-04-01' = {
  name: 'v2-mcp-exact-pending-core'
  properties: {
    mode: 'Incremental'
    parameters: {
      approved: {
        value: !contains([
          portalValidationPassed
          savedTags['azbrief.v2.schema'] == '2'
          savedTags['azbrief.v2.profile'] == 'kt-private-v2'
          savedTags['azbrief.v2.state'] == 'mcp_pending'
          core.properties.provisioningState == 'Succeeded'
          infrastructure.schemaVersion == 2
          infrastructure.profile == 'kt-private-v2'
          infrastructure.infrastructureComplete == true
          infrastructure.tenantId == tenant().tenantId
          infrastructure.subscriptionId == subscription().subscriptionId
          toLower(infrastructure.resourceGroup) == toLower(resourceGroup().name)
          infrastructure.deploymentName == core.name
          empty(filter(items(savedNames), item => toLower(string(infrastructure[?item.key] ?? '')) != toLower(item.value)))
          empty(filter(items(expectedIds), item => toLower(string(infrastructure[?item.key] ?? '')) != toLower(item.value) || toLower(split(item.value, '/')[2]) != toLower(subscription().subscriptionId)))
          infrastructure.stateContainerName == (bindings.?stateContainerName ?? 'azbrief-state')
          infrastructure.archiveContainerName == (bindings.?archiveContainerName ?? 'azbrief-archive')
          toLower(infrastructure.controlPlaneIdentityResourceId) == toLower(resourceId('Microsoft.ManagedIdentity/userAssignedIdentities', savedNames.controlPlaneIdentityName))
          infrastructure.deployContainerAppsPrivateEndpoint == (savedTags['azbrief.v2.acaPeEnabled'] == 'true')
          toLower(infrastructure.foundryAccountResourceId) == toLower(accountId)
          toLower(infrastructure.foundryProjectResourceId) == toLower(project.id)
          infrastructure.foundryProjectPrincipalId == project.identity.principalId
          infrastructure.azureMcpEntraAppUniqueName == 'azbrief-v2-mcp-${uniqueString(resourceGroup().id)}'
          infrastructure.azureMcpServerUrl == savedTags['azbrief.v2.mcpUrl']
          infrastructure.azureMcpProjectConnectionName == savedTags['azbrief.v2.mcpConnection']
          infrastructure.azureMcpContainerAppName == savedTags['azbrief.v2.mcpApp']
          infrastructure.azureMcpIdentityName == mcpIdentity.name
          options.manageAzureMcp == true
          empty(options.?mcpAutomation) || empty(filter(items(automation), item => toLower(string(options.mcpAutomation[?item.key] ?? '')) != toLower(string(item.value))))
        ], false)
      }
    }
    template: {
      '$schema': 'https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#'
      contentVersion: '1.0.0.0'
      parameters: {
        approved: { type: 'bool', allowedValues: [true] }
      }
      resources: []
    }
  }
}

module authentication 'mcp-authentication-automatic.bicep' = {
  name: 'v2-mcp-authorized-portal-setup'
  params: {
    infrastructure: infrastructure
    infrastructureOptions: union(options, { mcpAutomation: automation })
    automation: automation
    mcpPrincipalId: mcpIdentity.properties.principalId
  }
  dependsOn: [exactPendingCore]
}

output authentication object = authentication.outputs.authentication
