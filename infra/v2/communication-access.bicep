targetScope = 'resourceGroup'

param communicationServicesName string
param principalId string

resource communication 'Microsoft.Communication/communicationServices@2023-04-01' existing = {
  name: communicationServicesName
}
var senderRoleId = guid(communication.id, principalId, 'azbrief-email-sender')
resource senderRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: senderRoleId
  properties: {
    roleName: 'AzBrief Email Sender ${principalId}'
    description: 'Allows managed-identity email sending on the assigned Communication Services resource without keys or delete permissions.'
    type: 'CustomRole'
    assignableScopes: [resourceGroup().id]
    permissions: [{
      actions: [
        'Microsoft.Communication/CommunicationServices/read'
        'Microsoft.Communication/CommunicationServices/write'
        'Microsoft.Communication/EmailServices/write'
      ]
      notActions: []
      dataActions: []
      notDataActions: []
    }]
  }
}
resource sender 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: communication
  name: guid(communication.id, principalId, senderRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', senderRoleId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
  dependsOn: [senderRole]
}
