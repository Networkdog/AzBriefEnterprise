targetScope = 'resourceGroup'

@description('Location for Azure MCP resources.')
param location string = resourceGroup().location

@description('Name for the Azure MCP Server Container App.')
param acaName string = 'ca-azbrief-mcp'

@description('Existing AzBrief Container Apps environment resource ID.')
@minLength(1)
param containerAppEnvironmentId string

@description('Existing environment workload profile. Empty for consumption-only environments.')
param workloadProfileName string = ''

@description('Display name for the Azure MCP Entra application.')
param entraAppDisplayName string = 'AzBrief Azure MCP Server API'

@description('Subscription exposed to the read-only Azure MCP Server.')
param targetSubscriptionId string = subscription().subscriptionId

@description('Microsoft Foundry project resource ID.')
param foundryProjectResourceId string

@description('Optional Service Management Reference GUID for the Entra application.')
param serviceManagementReference string = ''

@description('Existing AzBrief Application Insights component in this resource group. MCP console logs use its shared workspace through the Container Apps environment.')
@minLength(1)
param applicationInsightsName string

@description('Pinned official Azure MCP Server image. Upgrade only after validating direct tool schemas and a live read-only inventory call.')
param azureMcpImage string = 'mcr.microsoft.com/azure-sdk/azure-mcp:3.0.0-beta.38'

param tags object = {
  application: 'azbrief-enterprise'
  environment: 'production'
}

var entraAppUniqueName = '${replace(toLower(entraAppDisplayName), ' ', '-')}-${uniqueString(resourceGroup().id)}'

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: applicationInsightsName
}

module entraApp 'modules/entra-app.bicep' = {
  name: 'azure-mcp-entra-app'
  params: {
    entraAppDisplayName: entraAppDisplayName
    entraAppUniqueName: entraAppUniqueName
    serviceManagementReference: serviceManagementReference
  }
}

module acaInfrastructure 'modules/aca-infrastructure.bicep' = {
  name: 'azure-mcp-container-app'
  params: {
    name: acaName
    location: location
    containerAppEnvironmentId: containerAppEnvironmentId
    workloadProfileName: workloadProfileName
    azureAdTenantId: tenant().tenantId
    azureAdClientId: entraApp.outputs.entraAppClientId
    targetSubscriptionId: targetSubscriptionId
    azureMcpImage: azureMcpImage
    tags: tags
  }
}

module subscriptionReader 'modules/subscription-reader.bicep' = {
  name: 'azure-mcp-subscription-reader'
  scope: subscription(targetSubscriptionId)
  params: {
    principalId: acaInfrastructure.outputs.containerAppPrincipalId
  }
}

module foundryRoleAssignment 'modules/foundry-role-assignment-entraapp.bicep' = {
  name: 'azure-mcp-foundry-app-role'
  params: {
    foundryProjectResourceId: foundryProjectResourceId
    entraAppServicePrincipalObjectId: entraApp.outputs.entraAppServicePrincipalObjectId
    entraAppRoleId: entraApp.outputs.entraAppRoleId
  }
}

output AZURE_TENANT_ID string = tenant().tenantId
output AZURE_SUBSCRIPTION_ID string = subscription().subscriptionId
output AZURE_RESOURCE_GROUP string = resourceGroup().name
output AZURE_LOCATION string = location
output AZURE_MCP_CONTAINER_APP_NAME string = acaInfrastructure.outputs.containerAppName
output AZURE_MCP_SERVER_URL string = acaInfrastructure.outputs.containerAppUrl
output AZURE_MCP_CONTAINER_APP_PRINCIPAL_ID string = acaInfrastructure.outputs.containerAppPrincipalId
output AZURE_MCP_ENTRA_APP_CLIENT_ID string = entraApp.outputs.entraAppClientId
output AZURE_MCP_ENTRA_APP_IDENTIFIER_URI string = entraApp.outputs.entraAppIdentifierUri
output AZURE_MCP_ENTRA_APP_ROLE_ID string = entraApp.outputs.entraAppRoleId
output AZURE_MCP_FOUNDRY_PROJECT_PRINCIPAL_ID string = foundryRoleAssignment.outputs.foundryProjectPrincipalId
output AZURE_MCP_READER_ROLE_ASSIGNMENT_ID string = subscriptionReader.outputs.roleAssignmentId
output AZURE_CONTAINER_APP_ENVIRONMENT_ID string = acaInfrastructure.outputs.containerAppEnvironmentId
output APPLICATION_INSIGHTS_NAME string = appInsights.name

@secure()
output APPLICATION_INSIGHTS_CONNECTION_STRING string = appInsights.properties.ConnectionString
