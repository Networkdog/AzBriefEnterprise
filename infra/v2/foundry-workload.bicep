targetScope = 'resourceGroup'

metadata description = 'Workload-owned project and Capability Host in a new or shared Foundry account. Never updates the parent account.'

param foundryAccountName string
param projectName string
param location string
param storageAccountName string
param cosmosAccountName string
param searchServiceName string
param useMicrosoftManagedAgentData bool
param reuseExistingFoundrySetup bool
param reuseExistingTelemetryConnection bool
param applicationInsightsResourceId string
param controlPlanePrincipalId string
param sharedAccount bool
param tags object

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryAccountName
}
resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = if (!reuseExistingFoundrySetup) {
  parent: account
  name: projectName
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: projectName
    description: useMicrosoftManagedAgentData ? 'AzBrief v2 Basic VNet project with Microsoft-managed agent data.' : 'AzBrief v2 project with customer-owned agent storage.'
  }
}

var projectId = '${account.id}/projects/${projectName}'
var projectKey = uniqueString(account.id, projectName)
#disable-next-line use-resource-symbol-reference
var projectPrincipalId = reuseExistingFoundrySetup ? reference(projectId, '2025-06-01', 'full').identity.principalId : project!.identity.principalId
#disable-next-line BCP053 use-resource-symbol-reference
var projectInternalId = useMicrosoftManagedAgentData ? '' : (reuseExistingFoundrySetup ? reference(projectId, '2025-06-01', 'full').properties.internalId : project!.properties.internalId)

module bindings '../kt/agent-bindings.bicep' = if (!useMicrosoftManagedAgentData) {
  name: sharedAccount ? 'v2-agent-bindings-${projectKey}' : 'v2-agent-bindings'
  params: {
    foundryAccountName: foundryAccountName
    projectName: projectName
    projectPrincipalId: projectPrincipalId
    agentStorageAccountName: storageAccountName
    cosmosAccountName: cosmosAccountName
    searchServiceName: searchServiceName
    createConnections: !reuseExistingFoundrySetup
  }
}
module capabilityHost '../kt/capability-host.bicep' = if (!useMicrosoftManagedAgentData) {
  name: sharedAccount ? 'v2-project-host-${projectKey}' : 'v2-project-host'
  params: {
    foundryAccountName: foundryAccountName
    projectName: projectName
    projectPrincipalId: projectPrincipalId
    projectInternalId: projectInternalId
    agentStorageAccountName: storageAccountName
    cosmosAccountName: cosmosAccountName
    createHost: !reuseExistingFoundrySetup
  }
  dependsOn: [bindings]
}
module managedCapabilityHost 'managed-capability-host.bicep' = if (useMicrosoftManagedAgentData) {
  name: sharedAccount ? 'v2-managed-project-host-${projectKey}' : 'v2-managed-project-host'
  params: {
    foundryAccountName: foundryAccountName
    projectName: projectName
    createHost: !reuseExistingFoundrySetup
  }
  dependsOn: [project]
}

resource insights 'Microsoft.Insights/components@2020-02-02' existing = {
  scope: resourceGroup(split(applicationInsightsResourceId, '/')[2], split(applicationInsightsResourceId, '/')[4])
  name: last(split(applicationInsightsResourceId, '/'))
}
resource telemetryConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = if (!reuseExistingTelemetryConnection) {
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

module access 'project-access.bicep' = if (sharedAccount) {
  name: 'v2-shared-foundry-access-${projectKey}'
  params: {
    foundryAccountName: foundryAccountName
    projectName: projectName
    principalId: controlPlanePrincipalId
    readAccount: true
  }
  dependsOn: [capabilityHost, managedCapabilityHost]
}

output principalId string = projectPrincipalId
output resourceId string = projectId
