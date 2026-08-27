extension microsoftGraphV1

@description('Microsoft Foundry project resource ID.')
param foundryProjectResourceId string

@description('Entra application service principal object ID.')
param entraAppServicePrincipalObjectId string

@description('Entra application role ID assigned to the Foundry project identity.')
param entraAppRoleId string

var resourceIdParts = split(foundryProjectResourceId, '/')
var projectSubscriptionId = resourceIdParts[2]
var projectResourceGroup = resourceIdParts[4]
var accountName = resourceIdParts[8]
var projectName = resourceIdParts[10]

resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' existing = {
  scope: resourceGroup(projectSubscriptionId, projectResourceGroup)
  name: '${accountName}/${projectName}'
}

resource appRoleAssignment 'Microsoft.Graph/appRoleAssignedTo@v1.0' = {
  principalId: foundryProject.identity.principalId
  resourceId: entraAppServicePrincipalObjectId
  appRoleId: entraAppRoleId
}

output roleAssignmentId string = appRoleAssignment.id
output foundryProjectPrincipalId string = foundryProject.identity.principalId
