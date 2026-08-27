@description('Location for all resources.')
param location string = resourceGroup().location

@description('Application Insights connection string. Empty creates a new component.')
@secure()
param appInsightsConnectionString string = ''

@description('Name for the new Application Insights component.')
param name string

var shouldCreate = empty(appInsightsConnectionString)
var isDisabled = appInsightsConnectionString == 'DISABLED'

resource logAnalyticsWorkspace 'Microsoft.OperationalInsights/workspaces@2022-10-01' = if (shouldCreate) {
  name: '${name}-workspace'
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource applicationInsights 'Microsoft.Insights/components@2020-02-02' = if (shouldCreate) {
  name: name
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalyticsWorkspace.id
  }
}

@secure()
output connectionString string = shouldCreate
  ? (applicationInsights.?properties.ConnectionString ?? '')
  : (isDisabled ? '' : appInsightsConnectionString)
