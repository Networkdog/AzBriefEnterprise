targetScope = 'resourceGroup'

param foundryAccountName string
param projectName string
param projectPrincipalId string
param agentStorageAccountName string
param cosmosAccountName string
param searchServiceName string

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryAccountName
}
resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' existing = {
  parent: account
  name: projectName
}
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: agentStorageAccountName
}
resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' existing = {
  name: cosmosAccountName
}
resource search 'Microsoft.Search/searchServices@2023-11-01' existing = {
  name: searchServiceName
}

// Foundry 필수 계정 권한은 유지한다. 공유 계정의 앱 컨테이너와 강한 권한 격리를 제공하지 않는다.
resource storageRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for role in [
    'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
    '17d1049b-9a84-46fb-8f53-869881c3d3ab'
  ]: {
    scope: storage
    name: guid(storage.id, projectPrincipalId, role)
    properties: {
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role)
      principalId: projectPrincipalId
      principalType: 'ServicePrincipal'
    }
  }
]

resource cosmosOperator 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: cosmos
  name: guid(cosmos.id, projectPrincipalId, 'cosmos-operator')
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '230815da-be43-4aae-9cb4-875f7bd000aa'
    )
    principalId: projectPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource searchRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for role in [
    '7ca78c08-252a-4471-8644-bb5ff32d4ba0'
    '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
  ]: {
    scope: search
    name: guid(search.id, projectPrincipalId, role)
    properties: {
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role)
      principalId: projectPrincipalId
      principalType: 'ServicePrincipal'
    }
  }
]

resource storageConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = {
  parent: project
  name: 'agent-storage'
  properties: {
    category: 'AzureStorageAccount'
    target: storage.properties.primaryEndpoints.blob
    authType: 'AAD'
    metadata: {
      ResourceId: storage.id
    }
  }
  dependsOn: [
    storageRoles
    cosmosOperator
    searchRoles
  ]
}

resource cosmosConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = {
  parent: project
  name: 'agent-cosmos'
  properties: {
    category: 'CosmosDb'
    target: cosmos.properties.documentEndpoint
    authType: 'AAD'
    metadata: {
      ResourceId: cosmos.id
    }
  }
  dependsOn: [
    storageConnection
  ]
}

resource searchConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = {
  parent: project
  name: 'agent-search'
  properties: {
    category: 'CognitiveSearch'
    target: 'https://${search.name}.search.windows.net'
    authType: 'AAD'
    metadata: {
      ResourceId: search.id
    }
  }
  dependsOn: [
    cosmosConnection
  ]
}
