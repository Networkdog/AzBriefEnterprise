targetScope = 'resourceGroup'

param foundryAccountName string
param projectName string
param storageAccountName string
param controlPlanePrincipalId string
param applicationInsightsName string = ''
param communicationServicesName string = ''
param emailSenderRoleDefinitionId string = ''
param existingAssignmentNames object = {}

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryAccountName
}
resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' existing = {
  parent: account
  name: projectName
}
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}
resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = {
  parent: storage
  name: 'default'
}
resource state 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  parent: blobService
  name: 'azbrief-state'
}
resource archive 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  parent: blobService
  name: 'azbrief-archive'
}
resource insights 'Microsoft.Insights/components@2020-02-02' existing = if (!empty(applicationInsightsName)) {
  name: applicationInsightsName
}
resource communication 'Microsoft.Communication/communicationServices@2023-03-31' existing = if (!empty(communicationServicesName)) {
  name: communicationServicesName
}

resource reader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: existingAssignmentNames.?reader ?? guid(resourceGroup().id, controlPlanePrincipalId, 'acdd72a7-3385-48ef-bd42-f606fba81ae7')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'acdd72a7-3385-48ef-bd42-f606fba81ae7')
    principalId: controlPlanePrincipalId
    principalType: 'ServicePrincipal'
  }
}
resource foundryUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: project
  name: existingAssignmentNames.?foundryUser ?? guid(project.id, controlPlanePrincipalId, '53ca6127-db72-4b80-b1b0-d745d6d5456d')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')
    principalId: controlPlanePrincipalId
    principalType: 'ServicePrincipal'
  }
}
resource stateContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: state
  name: existingAssignmentNames.?stateContributor ?? guid(state.id, controlPlanePrincipalId, 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
    principalId: controlPlanePrincipalId
    principalType: 'ServicePrincipal'
  }
}
resource archiveContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: archive
  name: existingAssignmentNames.?archiveContributor ?? guid(archive.id, controlPlanePrincipalId, 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
    principalId: controlPlanePrincipalId
    principalType: 'ServicePrincipal'
  }
}
resource telemetryPublisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(applicationInsightsName)) {
  scope: insights
  name: existingAssignmentNames.?telemetryPublisher ?? guid(insights!.id, controlPlanePrincipalId, '3913510d-42f4-4e42-8a64-420c390055eb')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '3913510d-42f4-4e42-8a64-420c390055eb')
    principalId: controlPlanePrincipalId
    principalType: 'ServicePrincipal'
  }

}

resource emailSender 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(communicationServicesName) && !empty(emailSenderRoleDefinitionId)) {
  scope: communication
  name: existingAssignmentNames.?emailSender ?? guid(communication!.id, controlPlanePrincipalId, emailSenderRoleDefinitionId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', emailSenderRoleDefinitionId)
    principalId: controlPlanePrincipalId
    principalType: 'ServicePrincipal'
  }
}
