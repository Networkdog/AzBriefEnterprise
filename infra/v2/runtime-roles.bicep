targetScope = 'resourceGroup'

metadata description = 'Infrastructure-owned, post-publication Hosted identity RBAC. Agent identity exists only after first publication; no App or infrastructure resource is recreated.'

param infrastructureTags object = resourceGroup().tags
@allowed(['2'])
param infrastructureSchema string = infrastructureTags['azbrief.v2.schema']
@allowed(['ready'])
param infrastructureState string = infrastructureTags['azbrief.v2.state']
@minLength(36)
@maxLength(36)
param hostedPrincipalId string

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' existing = {
  name: '${infrastructureTags['azbrief.v2.foundry']}/${infrastructureTags['azbrief.v2.project']}'
}

resource foundryUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: project
  name: guid(project.id, hostedPrincipalId, '53ca6127-db72-4b80-b1b0-d745d6d5456d')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')
    principalId: hostedPrincipalId
    principalType: 'ServicePrincipal'
  }
}

module monitoring 'monitoring-role.bicep' = {
  name: 'v2-hosted-telemetry-role'
  params: {
    applicationInsightsName: infrastructureTags['azbrief.v2.insights']
    principalId: hostedPrincipalId
  }
}

module evidence 'subscription-reader.bicep' = {
  name: 'v2-hosted-evidence-reader'
  scope: subscription()
  params: { principalId: hostedPrincipalId }
}

output runtimeRoles object = {
  schemaVersion: int(infrastructureSchema)
  infrastructureState: infrastructureState
  hostedPrincipalId: hostedPrincipalId
  applicationReady: false
}