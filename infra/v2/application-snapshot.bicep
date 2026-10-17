targetScope = 'resourceGroup'

param appName string
param jobName string
param readExistingSettings bool

resource app 'Microsoft.App/containerApps@2026-01-01' existing = {
  name: appName
}
resource job 'Microsoft.App/jobs@2026-01-01' existing = {
  name: jobName
}

@secure()
output snapshot object = readExistingSettings ? {
  appConfiguration: app.properties.configuration
  jobConfiguration: job.properties.configuration
  appTemplate: app.properties.template
  jobTemplate: job.properties.template
  appSecrets: app.listSecrets().value
  jobSecrets: job.listSecrets().value
} : {}
