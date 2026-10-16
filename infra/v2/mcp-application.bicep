targetScope = 'resourceGroup'

param infrastructureTags object
param installManagedMcp bool
var options = json(concat(
  infrastructureTags['azbrief.v2.options0'], infrastructureTags['azbrief.v2.options1'],
  infrastructureTags['azbrief.v2.options2'], infrastructureTags['azbrief.v2.options3'],
  infrastructureTags['azbrief.v2.options4'], infrastructureTags['azbrief.v2.options5'],
  infrastructureTags['azbrief.v2.options6'], infrastructureTags['azbrief.v2.options7']
))
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

module server '../azure-mcp-server/infra/modules/aca-infrastructure.bicep' = if (options.manageAzureMcp && installManagedMcp) {
  name: 'v2-mcp-server-application'
  params: {
    name: infrastructureTags['azbrief.v2.mcpApp']
    location: infrastructureTags['azbrief.v2.location']
    containerAppEnvironmentId: resourceBindings.?containerAppsEnvironmentResourceId ?? resourceId('Microsoft.App/managedEnvironments', infrastructureTags['azbrief.v2.environment'])
    workloadProfileName: 'Consumption'
    azureAdTenantId: tenant().tenantId
    azureAdClientId: options.azureMcpClientId
    userAssignedIdentityResourceId: resourceId('Microsoft.ManagedIdentity/userAssignedIdentities', infrastructureTags['azbrief.v2.mcpIdentity'])
    targetSubscriptionId: subscription().subscriptionId
    azureMcpImage: 'mcr.microsoft.com/azure-sdk/azure-mcp:3.0.0-beta.38'
    tags: options.tags
  }
}