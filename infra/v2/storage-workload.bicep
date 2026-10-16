targetScope = 'resourceGroup'

metadata description = 'Only workload containers and container-scoped data permissions in shared Storage. No account, service, network or diagnostic updates.'

param storageAccountName string
param stateContainerName string
param archiveContainerName string
param principalId string
param createContainers bool
param assignRoles bool

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}
resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = {
  parent: storage
  name: 'default'
}
resource containers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = [
  for name in [stateContainerName, archiveContainerName]: if (createContainers) {
    parent: blobs
    name: name
    properties: { publicAccess: 'None' }
  }
]
resource existingContainers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = [
  for name in [stateContainerName, archiveContainerName]: {
    parent: blobs
    name: name
  }
]
resource contributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for (name, index) in [stateContainerName, archiveContainerName]: if (assignRoles) {
    scope: existingContainers[index]
    name: guid('${storage.id}/blobServices/default/containers/${name}', principalId, 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
    properties: {
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
      principalId: principalId
      principalType: 'ServicePrincipal'
    }
  }
]
