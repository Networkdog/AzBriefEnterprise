extension microsoftGraphV1

@description('Display name for the Entra application.')
param entraAppDisplayName string

@description('Unique name for the Entra application.')
param entraAppUniqueName string

@description('Optional Service Management Reference GUID.')
param serviceManagementReference string = ''

var entraAppRoleValue = 'Mcp.Tools.ReadWrite.All'
var entraAppRoleId = guid(subscription().id, entraAppUniqueName, entraAppRoleValue)
var entraAppScopeValue = 'Mcp.Tools.ReadWrite'
var entraAppScopeId = guid(subscription().id, entraAppUniqueName, entraAppScopeValue)
var vsCodeClientAppId = 'aebc6443-996d-45c2-90f0-388ff96faa56'

resource entraApp 'Microsoft.Graph/applications@v1.0' = {
  uniqueName: entraAppUniqueName
  displayName: entraAppDisplayName
  serviceManagementReference: !empty(serviceManagementReference)
    ? serviceManagementReference
    : null
  appRoles: [
    {
      id: entraAppRoleId
      displayName: 'Azure MCP Tools ReadWrite All'
      description: 'Application permission for authenticated Azure MCP tool calls'
      value: entraAppRoleValue
      isEnabled: true
      allowedMemberTypes: [
        'Application'
      ]
    }
  ]
  api: {
    requestedAccessTokenVersion: 2
    oauth2PermissionScopes: [
      {
        id: entraAppScopeId
        value: entraAppScopeValue
        type: 'User'
        adminConsentDisplayName: 'Azure MCP Tools ReadWrite'
        adminConsentDescription: 'Delegated permission for authenticated Azure MCP tool calls'
        userConsentDisplayName: 'Azure MCP Tools ReadWrite'
        userConsentDescription: 'Delegated permission for authenticated Azure MCP tool calls'
        isEnabled: true
      }
    ]
  }
}

resource entraAppUpdate 'Microsoft.Graph/applications@v1.0' = {
  uniqueName: entraAppUniqueName
  displayName: entraAppDisplayName
  serviceManagementReference: !empty(serviceManagementReference)
    ? serviceManagementReference
    : null
  appRoles: entraApp.appRoles
  identifierUris: [
    'api://${entraApp.appId}'
  ]
  api: {
    requestedAccessTokenVersion: 2
    oauth2PermissionScopes: entraApp.api.oauth2PermissionScopes
    preAuthorizedApplications: [
      {
        appId: vsCodeClientAppId
        delegatedPermissionIds: [
          entraAppScopeId
        ]
      }
    ]
  }
}

resource entraServicePrincipal 'Microsoft.Graph/servicePrincipals@v1.0' = {
  appId: entraApp.appId
}

output entraAppClientId string = entraApp.appId
output entraAppObjectId string = entraApp.id
output entraAppIdentifierUri string = 'api://${entraApp.appId}'
output entraAppRoleId string = entraApp.appRoles[0].id
output entraAppServicePrincipalObjectId string = entraServicePrincipal.id
