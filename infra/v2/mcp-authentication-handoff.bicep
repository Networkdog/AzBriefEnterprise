targetScope = 'resourceGroup'

param infrastructure object
param infrastructureOptions object
param authentication object

var accountId = infrastructure.?foundryAccountResourceId ?? resourceId('Microsoft.CognitiveServices/accounts', infrastructure.foundryAccountName)
module connection 'mcp-connection.bicep' = {
  name: 'v2-mcp-project-connection-${uniqueString(accountId, infrastructure.projectName)}'
  scope: resourceGroup(split(accountId, '/')[2], split(accountId, '/')[4])
  params: {
    foundryAccountName: infrastructure.foundryAccountName
    projectName: infrastructure.projectName
    connectionName: infrastructure.azureMcpProjectConnectionName
    serverUrl: infrastructure.azureMcpServerUrl
    clientId: authentication.clientId
  }
}

module handoff 'resource-group-tags.bicep' = {
  name: 'v2-mcp-ready-tags'
  params: {
    infrastructure: infrastructure
    infrastructureState: 'ready'
    infrastructureOptions: union(infrastructureOptions, {
      azureMcpClientId: authentication.clientId
      azureMcpServicePrincipalId: authentication.servicePrincipalId
      azureMcpAppRoleId: authentication.appRoleId
    })
  }
  dependsOn: [connection]
}

output authentication object = union(authentication, {
  infrastructureState: 'ready'
  applicationReady: false
})
