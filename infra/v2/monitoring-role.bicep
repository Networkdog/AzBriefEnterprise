targetScope = 'resourceGroup'

param applicationInsightsName string
param principalId string

resource insights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: applicationInsightsName
}

resource publisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: insights
  name: guid(insights.id, principalId, '3913510d-42f4-4e42-8a64-420c390055eb')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '3913510d-42f4-4e42-8a64-420c390055eb')
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}