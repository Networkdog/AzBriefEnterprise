targetScope = 'resourceGroup'

param environmentName string = ''
param storageAccountName string = ''
param principalId string

resource environment 'Microsoft.App/managedEnvironments@2026-01-01' existing = if (!empty(environmentName)) {
  name: environmentName
}
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = if (!empty(storageAccountName)) {
  name: storageAccountName
}
resource environmentReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(environmentName)) {
  scope: environment
  name: guid(environment!.id, principalId, 'acdd72a7-3385-48ef-bd42-f606fba81ae7')
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'acdd72a7-3385-48ef-bd42-f606fba81ae7')
  }
}
resource storageReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(storageAccountName)) {
  scope: storage
  name: guid(storage!.id, principalId, 'acdd72a7-3385-48ef-bd42-f606fba81ae7')
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'acdd72a7-3385-48ef-bd42-f606fba81ae7')
  }
}
