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

var resourceBindings = json(concat(
  infrastructureTags[?'azbrief.v2.resources0'] ?? '{}',
  infrastructureTags[?'azbrief.v2.resources1'] ?? '',
  infrastructureTags[?'azbrief.v2.resources2'] ?? '',
  infrastructureTags[?'azbrief.v2.resources3'] ?? '',
  infrastructureTags[?'azbrief.v2.resources4'] ?? '',
  infrastructureTags[?'azbrief.v2.resources5'] ?? '',
  infrastructureTags[?'azbrief.v2.resources6'] ?? '',
  infrastructureTags[?'azbrief.v2.resources7'] ?? ''
))
var accountId = resourceBindings.?foundryAccountResourceId ?? resourceId('Microsoft.CognitiveServices/accounts', infrastructureTags['azbrief.v2.foundry'])
module foundryUser 'project-access.bicep' = {
  name: split(accountId, '/')[4] == resourceGroup().name ? 'v2-hosted-project-user' : 'v2-hosted-project-user-${uniqueString(resourceGroup().id)}'
  scope: resourceGroup(split(accountId, '/')[2], split(accountId, '/')[4])
  params: {
    foundryAccountName: infrastructureTags['azbrief.v2.foundry']
    projectName: infrastructureTags['azbrief.v2.project']
    principalId: hostedPrincipalId
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