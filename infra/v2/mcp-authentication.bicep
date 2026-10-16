targetScope = 'resourceGroup'

metadata description = 'Authorized CLI-only MCP directory setup after core infrastructure. No foundation, application, model or Azure role replay.'

param infrastructure object
param infrastructureOptions object

module authentication '../azure-mcp-server/infra/modules/entra-app.bicep' = {
  name: 'v2-mcp-authentication'
  params: {
    entraAppDisplayName: 'AzBrief v2 Azure MCP API'
    entraAppUniqueName: infrastructure.azureMcpEntraAppUniqueName
  }
}

module projectPermission '../azure-mcp-server/infra/modules/foundry-role-assignment-entraapp.bicep' = {
  name: 'v2-mcp-project-permission'
  params: {
    foundryProjectResourceId: infrastructure.?foundryProjectResourceId ?? resourceId('Microsoft.CognitiveServices/accounts/projects', infrastructure.foundryAccountName, infrastructure.projectName)
    entraAppServicePrincipalObjectId: authentication.outputs.entraAppServicePrincipalObjectId
    entraAppRoleId: authentication.outputs.entraAppRoleId
  }
}

var accountId = infrastructure.?foundryAccountResourceId ?? resourceId('Microsoft.CognitiveServices/accounts', infrastructure.foundryAccountName)
module connection 'mcp-connection.bicep' = {
  name: 'v2-mcp-project-connection-${uniqueString(accountId, infrastructure.projectName)}'
  scope: resourceGroup(split(accountId, '/')[2], split(accountId, '/')[4])
  params: {
    foundryAccountName: infrastructure.foundryAccountName
    projectName: infrastructure.projectName
    connectionName: infrastructure.azureMcpProjectConnectionName
    serverUrl: infrastructure.azureMcpServerUrl
    clientId: authentication.outputs.entraAppClientId
  }
  dependsOn: [projectPermission]
}

module handoff 'resource-group-tags.bicep' = {
  name: 'v2-mcp-ready-tags'
  params: {
    infrastructure: infrastructure
    infrastructureState: 'ready'
    infrastructureOptions: union(infrastructureOptions, {
      azureMcpClientId: authentication.outputs.entraAppClientId
      azureMcpServicePrincipalId: authentication.outputs.entraAppServicePrincipalObjectId
      azureMcpAppRoleId: authentication.outputs.entraAppRoleId
    })
  }
  dependsOn: [connection]
}

output authentication object = {
  clientId: authentication.outputs.entraAppClientId
  servicePrincipalId: authentication.outputs.entraAppServicePrincipalObjectId
  appRoleId: authentication.outputs.entraAppRoleId
  infrastructureState: 'ready'
  applicationReady: false
}