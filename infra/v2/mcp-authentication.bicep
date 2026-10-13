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
    foundryProjectResourceId: resourceId('Microsoft.CognitiveServices/accounts/projects', infrastructure.foundryAccountName, infrastructure.projectName)
    entraAppServicePrincipalObjectId: authentication.outputs.entraAppServicePrincipalObjectId
    entraAppRoleId: authentication.outputs.entraAppRoleId
  }
}

resource connection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-10-01-preview' = {
  name: '${infrastructure.foundryAccountName}/${infrastructure.projectName}/${infrastructure.azureMcpProjectConnectionName}'
  properties: {
    #disable-next-line BCP036
    category: 'RemoteTool'
    #disable-next-line BCP036
    authType: 'ProjectManagedIdentity'
    target: infrastructure.azureMcpServerUrl
    #disable-next-line BCP037
    audience: 'api://${authentication.outputs.entraAppClientId}'
    isSharedToAll: true
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