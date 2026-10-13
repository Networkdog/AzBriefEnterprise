targetScope = 'resourceGroup'

metadata description = 'Non-secret AzBrief v2 infrastructure handoff. Preserve unrelated resource-group tags.'

@description('Same-resource-group infrastructure contract. Never include application secrets.')
param infrastructure object

@allowed(['foundation', 'mcp_pending', 'ready'])
param infrastructureState string = 'ready'

@description('Explicit allow-listed non-secret deployment options, serialized in bounded tag values.')
param infrastructureOptions object = {}

@maxLength(2048)
param optionsJson string = string(infrastructureOptions)
var optionsTags = toObject(range(0, 8), index => 'azbrief.v2.options${index}', index => take(skip(optionsJson, index * 256), 256))
var contractTags = union({
  'azbrief.v2.schema': '2'
  'azbrief.v2.profile': 'kt-private-v2'
  'azbrief.v2.state': infrastructureState
  'azbrief.v2.deployment': infrastructure.deploymentName
  'azbrief.v2.location': infrastructure.location
  'azbrief.v2.environment': infrastructure.containerAppsEnvironmentName
  'azbrief.v2.app': infrastructure.containerAppName
  'azbrief.v2.job': infrastructure.schedulerJobName
  'azbrief.v2.identity': last(split(infrastructure.controlPlaneIdentityResourceId, '/'))
  'azbrief.v2.storage': infrastructure.storageAccountName
  'azbrief.v2.cosmos': infrastructure.cosmosAccountName
  'azbrief.v2.search': infrastructure.searchServiceName
  'azbrief.v2.workspace': infrastructure.logAnalyticsWorkspaceName
  'azbrief.v2.insights': infrastructure.applicationInsightsName
  'azbrief.v2.acs': infrastructure.communicationServicesName
  'azbrief.v2.email': infrastructure.emailServiceName
  'azbrief.v2.foundry': infrastructure.foundryAccountName
  'azbrief.v2.project': infrastructure.projectName
  'azbrief.v2.hosted': infrastructure.foundryHostedAgentName
  'azbrief.v2.coreModel': infrastructure.modelDeploymentName
  'azbrief.v2.simpleModel': infrastructure.simpleModelDeploymentName
  'azbrief.v2.vnetRg': split(infrastructure.virtualNetworkResourceId, '/')[4]
  'azbrief.v2.vnet': last(split(infrastructure.virtualNetworkResourceId, '/'))
  'azbrief.v2.peSubnet': last(split(infrastructure.privateEndpointSubnetId, '/'))
  'azbrief.v2.foundrySubnet': last(split(infrastructure.foundrySubnetResourceId, '/'))
  'azbrief.v2.appSubnet': last(split(infrastructure.infrastructureSubnetId, '/'))
  'azbrief.v2.acaPeEnabled': toLower(string(infrastructure.deployContainerAppsPrivateEndpoint))
  'azbrief.v2.mcpUrl': infrastructure.azureMcpServerUrl
  'azbrief.v2.mcpConnection': infrastructure.azureMcpProjectConnectionName
  'azbrief.v2.mcpApp': infrastructure.azureMcpContainerAppName
  'azbrief.v2.mcpIdentity': infrastructure.azureMcpIdentityName
}, optionsTags)

resource handoff 'Microsoft.Resources/tags@2025-04-01' = {
  name: 'default'
  properties: {
    tags: union(resourceGroup().tags ?? {}, contractTags)
  }
}

output tagKeys array = objectKeys(contractTags)