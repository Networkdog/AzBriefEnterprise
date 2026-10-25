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

module handoff 'mcp-authentication-handoff.bicep' = {
  name: 'v2-mcp-authentication-handoff'
  params: {
    infrastructure: infrastructure
    infrastructureOptions: infrastructureOptions
    authentication: {
      clientId: authentication.outputs.entraAppClientId
      servicePrincipalId: authentication.outputs.entraAppServicePrincipalObjectId
      appRoleId: authentication.outputs.entraAppRoleId
    }
  }
  dependsOn: [projectPermission]
}

output authentication object = handoff.outputs.authentication