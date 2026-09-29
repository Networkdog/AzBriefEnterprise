targetScope = 'resourceGroup'

param foundryAccountName string
param projectName string
param projectPrincipalId string
param projectInternalId string
param agentStorageAccountName string
param cosmosAccountName string

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryAccountName
}
resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' existing = {
  parent: account
  name: projectName
}

// agent VNet injection이 만드는 account host를 중복 생성하면 409가 발생한다.
resource host 'Microsoft.CognitiveServices/accounts/projects/capabilityHosts@2025-04-01-preview' = {
  parent: project
  name: 'agents'
  properties: {
    #disable-next-line BCP037
    capabilityHostKind: 'Agents'
    storageConnections: ['agent-storage']
    threadStorageConnections: ['agent-cosmos']
    vectorStoreConnections: ['agent-search']
  }
}

var workspaceHex = replace(projectInternalId, '-', '')
var workspaceId = '${substring(workspaceHex, 0, 8)}-${substring(workspaceHex, 8, 4)}-${substring(workspaceHex, 12, 4)}-${substring(workspaceHex, 16, 4)}-${substring(workspaceHex, 20, 12)}'

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: agentStorageAccountName
}
resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = {
  parent: storage
  name: 'default'
}
resource agentContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  parent: blobs
  name: '${workspaceId}-azureml-agent'
}
resource agentBlobOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: agentContainer
  name: guid(agentContainer.id, projectPrincipalId, 'blob-owner')
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'
    )
    principalId: projectPrincipalId
    principalType: 'ServicePrincipal'
  }
  dependsOn: [
    host
  ]
}
resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' existing = {
  name: cosmosAccountName
}
resource cosmosDataContributor 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = {
  parent: cosmos
  name: guid(cosmos.id, projectPrincipalId, 'enterprise-memory-contributor')
  properties: {
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002'
    principalId: projectPrincipalId
    scope: '${cosmos.id}/dbs/enterprise_memory'
  }
  dependsOn: [
    host
  ]
}
