targetScope = 'resourceGroup'

param communicationServicesName string
param emailServiceName string
param dataLocation string
param tags object
param reuseCommunication bool = false
param reuseEmailService bool = false
param reuseDomain bool = false
param createSenderRole bool = true
param existingSenderRoleId string = ''
param existingCommunicationTags object = {}
param existingCommunicationIdentity object = { type: 'None' }
param existingLinkedDomains array = []

resource emailService 'Microsoft.Communication/emailServices@2023-04-01' = if (!reuseEmailService) {
  name: emailServiceName
  location: 'global'
  tags: tags
  properties: {
    dataLocation: dataLocation
  }
}

resource domain 'Microsoft.Communication/emailServices/domains@2023-04-01' = if (!reuseDomain) {
  #disable-next-line use-parent-property
  name: '${emailServiceName}/AzureManagedDomain'
  location: 'global'
  tags: tags
  properties: {
    domainManagement: 'AzureManaged'
    userEngagementTracking: 'Disabled'
  }
  dependsOn: [
    emailService
  ]
}

var domainId = resourceId('Microsoft.Communication/emailServices/domains', emailServiceName, 'AzureManagedDomain')
resource communication 'Microsoft.Communication/communicationServices@2023-04-01' = if (!reuseCommunication) {
  name: communicationServicesName
  location: 'global'
  tags: union(tags, existingCommunicationTags)
  identity: existingCommunicationIdentity
  properties: {
    dataLocation: dataLocation
    linkedDomains: union(existingLinkedDomains, [domainId])
  }
  dependsOn: [
    domain
  ]
}

var senderRoleId = empty(existingSenderRoleId) ? guid(toLower(resourceGroup().id), toLower(communicationServicesName), 'azbrief-email-sender') : existingSenderRoleId
resource senderRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = if (createSenderRole) {
  name: senderRoleId
  properties: {
    roleName: 'AzBrief Email Sender ${toLower(resourceGroup().id)}/${toLower(communicationServicesName)}'
    description: 'Allows managed-identity email sending on the assigned Communication Services resource without keys or delete permissions.'
    type: 'CustomRole'
    assignableScopes: [resourceGroup().id]
    permissions: [
      {
        actions: [
          'Microsoft.Communication/CommunicationServices/read'
          'Microsoft.Communication/CommunicationServices/write'
          'Microsoft.Communication/EmailServices/write'
        ]
        notActions: []
        dataActions: []
        notDataActions: []
      }
    ]
  }
}

// 기존 리소스 별칭을 중복 선언하지 않고 재사용 분기에서만 전체 ID로 조회한다.
#disable-next-line use-resource-symbol-reference
output endpoint string = reuseCommunication ? 'https://${reference(resourceId('Microsoft.Communication/communicationServices', communicationServicesName), '2023-04-01').hostName}' : 'https://${communication!.properties.hostName}'
#disable-next-line use-resource-symbol-reference
output senderAddress string = reuseDomain ? 'DoNotReply@${reference(domainId, '2023-04-01').fromSenderDomain}' : 'DoNotReply@${domain!.properties.fromSenderDomain}'
output senderRoleDefinitionId string = senderRoleId
