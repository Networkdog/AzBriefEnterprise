targetScope = 'resourceGroup'

metadata description = 'Repeatable AzBrief v2 application installation/update with final RG deployment metadata. No foundation, model, BYO, RBAC or diagnostics writes.'

@description('Read from this resource group by default. The guarded CLI verifies version, ownership, names and live infrastructure before deployment.')
param infrastructureTags object = resourceGroup().tags
@allowed([true])
@description('Read-only Portal contract/inventory validation result. False blocks deployment.')
param portalValidationPassed bool = true

@allowed(['2'])
param infrastructureSchema string = infrastructureTags['azbrief.v2.schema']
@allowed(['ready'])
param infrastructureState string = infrastructureTags['azbrief.v2.state']
@allowed(['kt-private-v2'])
param infrastructureProfile string = infrastructureTags['azbrief.v2.profile']

@description('Approved immutable image digest for both the control-plane App and scheduler Job.')
param containerImage string

@description('Derived from fresh App inventory. Existing installations must retain their API secret.')
param reuseExistingApiKey bool

@secure()
@minLength(32)
@maxLength(256)
param apiKey string = replace('${newGuid()}${newGuid()}', '-', '')

@secure()
@description('Existing App/Job secrets plus explicitly supplied new secrets. Never log this parameter.')
param existingSecrets object = {}

param appConfiguration object = {}
param appTemplate object = {}
param jobConfiguration object = {}
param jobTemplate object = {}
param appEnvironment array = []
param jobEnvironment array = []
param webAuthConfiguration object = {}
@description('Portal updates read current App/Job settings and secrets inside ARM; never expose them in UI controls.')
param preserveExistingSettings bool = false
@minLength(20)
@maxLength(20)
@description('UTC start time of this application deployment, recorded only after every application resource succeeds. Never infer runtime readiness from it.')
param applicationDeploymentTimestamp string = utcNow('yyyy-MM-ddTHH:mm:ssZ')
param tags object = {
  application: 'AzBrief'
  customer: 'KT'
  deploymentProfile: 'kt-private-v2'
}
param jobTags object = tags

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
var environmentId = resourceBindings.?containerAppsEnvironmentResourceId ?? resourceId('Microsoft.App/managedEnvironments', infrastructureTags['azbrief.v2.environment'])
var communicationId = resourceBindings.?communicationServicesResourceId ?? resourceId('Microsoft.Communication/communicationServices', infrastructureTags['azbrief.v2.acs'])
var emailDomainId = resourceBindings.?emailDomainResourceId ?? resourceId('Microsoft.Communication/emailServices/domains', infrastructureTags['azbrief.v2.email'], 'AzureManagedDomain')
var foundryAccountId = resourceBindings.?foundryAccountResourceId ?? resourceId('Microsoft.CognitiveServices/accounts', infrastructureTags['azbrief.v2.foundry'])
var storageId = resourceBindings.?storageAccountResourceId ?? resourceId('Microsoft.Storage/storageAccounts', infrastructureTags['azbrief.v2.storage'])
var stateName = resourceBindings.?stateContainerName ?? 'azbrief-state'
var archiveName = resourceBindings.?archiveContainerName ?? 'azbrief-archive'
resource foundryAccount 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  scope: resourceGroup(split(foundryAccountId, '/')[2], split(foundryAccountId, '/')[4])
  name: infrastructureTags['azbrief.v2.foundry']
}

var liveUpdate = preserveExistingSettings && reuseExistingApiKey
module existingSettings 'application-snapshot.bicep' = {
  name: 'v2-application-readback'
  params: {
    appName: infrastructureTags['azbrief.v2.app']
    jobName: infrastructureTags['azbrief.v2.job']
    readExistingSettings: liveUpdate
  }
}
var imageRegistry = toLower(split(containerImage, '/')[0])
var initialManagedRegistry = !reuseExistingApiKey && endsWith(imageRegistry, '.azurecr.io') ? {
  registries: [
    {
      server: imageRegistry
      identity: controlPlaneIdentity.id
    }
  ]
} : {}
var previousAppSecrets = liveUpdate ? existingSettings.outputs.snapshot.appSecrets : []
var previousJobSecrets = liveUpdate ? existingSettings.outputs.snapshot.jobSecrets : []
var suppliedAppSecretNames = map(existingSecrets.?app ?? [], secret => secret.name)
var suppliedJobSecretNames = map(existingSecrets.?job ?? [], secret => secret.name)
var resolvedAppSecrets = concat(filter(previousAppSecrets, secret => !contains(suppliedAppSecretNames, secret.name)), existingSecrets.?app ?? [])
var resolvedJobSecrets = concat(filter(previousJobSecrets, secret => !contains(suppliedJobSecretNames, secret.name)), existingSecrets.?job ?? [])
var resolvedAppConfiguration = union(initialManagedRegistry, liveUpdate ? existingSettings.outputs.snapshot.appConfiguration : {}, appConfiguration)
var resolvedJobConfiguration = union(initialManagedRegistry, liveUpdate ? existingSettings.outputs.snapshot.jobConfiguration : {}, jobConfiguration)
var resolvedAppTemplate = union(liveUpdate ? existingSettings.outputs.snapshot.appTemplate : {}, appTemplate)
var resolvedJobTemplate = union(liveUpdate ? existingSettings.outputs.snapshot.jobTemplate : {}, jobTemplate)
var suppliedAppEnvironmentNames = map(appEnvironment, item => item.name)
var suppliedJobEnvironmentNames = map(jobEnvironment, item => item.name)
var resolvedAppEnvironment = concat(filter(liveUpdate ? (existingSettings.outputs.snapshot.appTemplate.containers[0].?env ?? []) : [], item => !contains(suppliedAppEnvironmentNames, item.name)), appEnvironment)
var resolvedJobEnvironment = concat(filter(liveUpdate ? (existingSettings.outputs.snapshot.jobTemplate.containers[0].?env ?? []) : [], item => !contains(suppliedJobEnvironmentNames, item.name)), jobEnvironment)

resource containerEnvironment 'Microsoft.App/managedEnvironments@2026-01-01' existing = {
  scope: resourceGroup(split(environmentId, '/')[2], split(environmentId, '/')[4])
  name: infrastructureTags['azbrief.v2.environment']
}
resource controlPlaneIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: infrastructureTags['azbrief.v2.identity']
}
resource communication 'Microsoft.Communication/communicationServices@2023-04-01' existing = {
  scope: resourceGroup(split(communicationId, '/')[2], split(communicationId, '/')[4])
  name: infrastructureTags['azbrief.v2.acs']
}
resource emailDomain 'Microsoft.Communication/emailServices/domains@2023-04-01' existing = {
  scope: resourceGroup(split(emailDomainId, '/')[2], split(emailDomainId, '/')[4])
  name: '${split(emailDomainId, '/')[8]}/${split(emailDomainId, '/')[10]}'
}
resource insights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: infrastructureTags['azbrief.v2.insights']
}

var applicationUrl = 'https://${infrastructureTags['azbrief.v2.app']}.${containerEnvironment.properties.defaultDomain}'
var stateContainerUrl = 'https://${infrastructureTags['azbrief.v2.storage']}.blob.${environment().suffixes.storage}/${stateName}'
var promptAgentNames = {
  coordinator: '${take(infrastructureTags['azbrief.v2.project'], 47)}-coordinator'
  resource_graph: '${take(infrastructureTags['azbrief.v2.project'], 47)}-resource-graph'
  azure_mcp: '${take(infrastructureTags['azbrief.v2.project'], 47)}-azure-mcp'
  azure_api: '${take(infrastructureTags['azbrief.v2.project'], 47)}-azure-api'
  report_writer: '${take(infrastructureTags['azbrief.v2.project'], 47)}-report-writer'
  quality_reviewer: '${take(infrastructureTags['azbrief.v2.project'], 47)}-quality-reviewer'
}
var apiSecret = reuseExistingApiKey
  ? filter(resolvedAppSecrets, secret => secret.name == 'orchestrator-api-key')[0]
  : { name: 'orchestrator-api-key', value: apiKey }
var appSecrets = concat(
  filter(resolvedAppSecrets, secret => secret.name != 'orchestrator-api-key'),
  [apiSecret]
)
var jobSecrets = concat(
  filter(resolvedJobSecrets, secret => secret.name != 'orchestrator-api-key'),
  [apiSecret]
)
var runtimeEnvironment = [
  { name: 'AZURE_TENANT_ID', value: tenant().tenantId }
  { name: 'AZURE_SUBSCRIPTION_ID', value: subscription().subscriptionId }
  { name: 'AZURE_CLIENT_ID', value: controlPlaneIdentity.properties.clientId }
  { name: 'FOUNDRY_PROJECT_ENDPOINT', value: 'https://${foundryAccount.properties.customSubDomainName}.services.ai.azure.com/api/projects/${infrastructureTags['azbrief.v2.project']}' }
  { name: 'FOUNDRY_HOSTED_AGENT_NAME', value: infrastructureTags['azbrief.v2.hosted'] }
  { name: 'API_KEY', secretRef: 'orchestrator-api-key' }
  { name: 'CHECKPOINT_BLOB_URL', value: '${stateContainerUrl}/checkpoint.json' }
  { name: 'ARCHIVE_BLOB_CONTAINER_URL', value: 'https://${infrastructureTags['azbrief.v2.storage']}.blob.${environment().suffixes.storage}/${archiveName}' }
  { name: 'ADMIN_CONFIG_BLOB_URL', value: '${stateContainerUrl}/admin-config.json' }
  { name: 'ARCHIVE_BASE_URL', value: applicationUrl }
  { name: 'FEEDBACK_BASE_URL', value: applicationUrl }
  { name: 'COMMUNICATION_SERVICES_ENDPOINT', value: 'https://${communication.properties.hostName}' }
  { name: 'EMAIL_SENDER_ADDRESS', value: 'DoNotReply@${emailDomain.properties.fromSenderDomain}' }
  { name: 'OTEL_ENABLED', value: 'true' }
  { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: insights.properties.ConnectionString }
]
var supportResources = filter([
  { id: 'managed_identity', label: 'User-assigned managed identity', name: infrastructureTags['azbrief.v2.identity'], resource_type: 'Microsoft.ManagedIdentity/userAssignedIdentities', api_version: '2023-01-31' }
  { id: 'state_storage', label: empty(infrastructureTags['azbrief.v2.cosmos']) ? 'Application Storage (State, Archive, Feedback)' : 'Shared Storage (State, Archive, Foundry)', name: infrastructureTags['azbrief.v2.storage'], resource_id: storageId, resource_type: 'Microsoft.Storage/storageAccounts', api_version: '2023-05-01' }
  { id: 'cosmos', label: 'Cosmos DB', name: infrastructureTags['azbrief.v2.cosmos'], resource_type: 'Microsoft.DocumentDB/databaseAccounts', api_version: '2024-11-15' }
  { id: 'search', label: 'Azure AI Search', name: infrastructureTags['azbrief.v2.search'], resource_type: 'Microsoft.Search/searchServices', api_version: '2023-11-01' }
], resource => !empty(resource.name))
var appDefaults = concat(runtimeEnvironment, [
  { name: 'MAX_CONCURRENT_ANALYSES', value: '1' }
  { name: 'ADMIN_UI_ENABLED', value: 'false' }
  { name: 'ADMIN_REQUIRE_AUTH', value: 'true' }
  { name: 'ARCHIVE_UI_ENABLED', value: 'false' }
  { name: 'ARCHIVE_REQUIRE_AUTH', value: 'true' }
  { name: 'FEEDBACK_UI_ENABLED', value: 'false' }
  { name: 'ADMIN_READINESS_RESOURCE_GROUP', value: resourceGroup().name }
  { name: 'ADMIN_READINESS_FOUNDRY_ACCOUNT', value: foundryAccountId }
  { name: 'ADMIN_READINESS_FOUNDRY_PROJECT', value: infrastructureTags['azbrief.v2.project'] }
  { name: 'ADMIN_READINESS_FOUNDRY_MODEL_DEPLOYMENT', value: infrastructureTags['azbrief.v2.coreModel'] }
  { name: 'ADMIN_READINESS_FOUNDRY_SIMPLE_MODEL_DEPLOYMENT', value: infrastructureTags['azbrief.v2.simpleModel'] }
  { name: 'ADMIN_READINESS_PROMPT_AGENTS', value: base64(string(promptAgentNames)) }
  { name: 'ADMIN_READINESS_CONTAINER_ENVIRONMENTS', value: base64(string([environmentId])) }
  { name: 'ADMIN_READINESS_CONTAINER_APPS', value: base64(string([infrastructureTags['azbrief.v2.app']])) }
  { name: 'ADMIN_READINESS_CONTAINER_JOBS', value: base64(string([infrastructureTags['azbrief.v2.job']])) }
  { name: 'ADMIN_READINESS_SUPPORT_RESOURCES', value: base64(string(supportResources)) }
])
var jobDefaults = concat(runtimeEnvironment, [
  { name: 'MAX_CONCURRENT_ANALYSES', value: '1' }
  { name: 'SCHEDULE_DISPATCH_ENABLED', value: 'true' }
  { name: 'RUN_TIME_BUDGET_S', value: '39600' }
])
var appEnvironmentNames = map(resolvedAppEnvironment, item => item.name)
var jobEnvironmentNames = map(resolvedJobEnvironment, item => item.name)
var mergedAppEnvironment = concat(
  filter(appDefaults, item => !contains(appEnvironmentNames, item.name)),
  resolvedAppEnvironment
)
var mergedJobEnvironment = concat(
  filter(jobDefaults, item => !contains(jobEnvironmentNames, item.name)),
  resolvedJobEnvironment
)
var appContainer = union({
  name: 'azbrief'
  resources: { cpu: json('0.25'), memory: '0.5Gi' }
  probes: [
    {
      type: 'Readiness'
      httpGet: { path: '/health', port: 8000 }
      initialDelaySeconds: 5
      periodSeconds: 10
    }
  ]
}, resolvedAppTemplate.?containers[?0] ?? {}, {
  image: containerImage
  env: mergedAppEnvironment
})
var jobContainer = union({
  name: 'azbrief-scheduler'
  resources: { cpu: json('1.0'), memory: '2Gi' }
}, resolvedJobTemplate.?containers[?0] ?? {}, {
  image: containerImage
  command: ['python', '-m', 'src.scheduler']
  env: mergedJobEnvironment
})
var jobConfigurationDefaults = union({
  triggerType: resolvedJobConfiguration.?triggerType ?? 'Manual'
  replicaTimeout: 43200
  replicaRetryLimit: 0
}, (resolvedJobConfiguration.?triggerType ?? 'Manual') == 'Manual' ? {
  manualTriggerConfig: { parallelism: 1, replicaCompletionCount: 1 }
} : {})

resource containerApp 'Microsoft.App/containerApps@2026-01-01' = {
  name: infrastructureTags['azbrief.v2.app']
  location: infrastructureTags['azbrief.v2.location']
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${controlPlaneIdentity.id}': {} }
  }
  properties: {
    environmentId: containerEnvironment.id
    workloadProfileName: 'Consumption'
    configuration: union({
      activeRevisionsMode: 'Single'
      ingress: { external: true, allowInsecure: false, targetPort: 8000, transport: 'auto' }
    }, resolvedAppConfiguration, { secrets: appSecrets })
    template: union({ scale: { minReplicas: 0, maxReplicas: 1 } }, resolvedAppTemplate, {
      containers: [appContainer]
    })
  }
}

resource webAuth 'Microsoft.App/containerApps/authConfigs@2026-01-01' = if (!empty(webAuthConfiguration)) {
  parent: containerApp
  name: 'current'
  properties: webAuthConfiguration
}

resource schedulerJob 'Microsoft.App/jobs@2026-01-01' = {
  name: infrastructureTags['azbrief.v2.job']
  location: infrastructureTags['azbrief.v2.location']
  tags: jobTags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${controlPlaneIdentity.id}': {} }
  }
  properties: {
    environmentId: containerEnvironment.id
    workloadProfileName: 'Consumption'
    configuration: union(jobConfigurationDefaults, resolvedJobConfiguration, { secrets: jobSecrets })
    template: union(resolvedJobTemplate, { containers: [jobContainer] })
  }
}

module mcp 'mcp-application.bicep' = if (contains(infrastructureTags, 'azbrief.v2.options0')) {
  name: 'v2-managed-mcp-application'
  params: {
    infrastructureTags: infrastructureTags
    installManagedMcp: !reuseExistingApiKey
  }
}

resource applicationDeploymentTags 'Microsoft.Resources/tags@2025-04-01' = {
  name: 'default'
  properties: {
    tags: union(resourceGroup().tags ?? {}, {
      'azbrief.v2.applicationState': 'deployed'
      'azbrief.v2.lastApplicationDeploymentAt': applicationDeploymentTimestamp
    })
  }
  dependsOn: [containerApp, schedulerJob, webAuth, mcp]
}

output application object = {
  schemaVersion: int(infrastructureSchema)
  profile: infrastructureProfile
  infrastructureState: infrastructureState
  portalValidationPassed: portalValidationPassed
  infrastructureDeploymentName: infrastructureTags['azbrief.v2.deployment']
  applicationReady: false
  applicationState: 'deployed'
  lastApplicationDeploymentAt: applicationDeploymentTimestamp
  containerAppResourceId: containerApp.id
  schedulerJobResourceId: schedulerJob.id
  containerImage: containerImage
  applicationUrl: applicationUrl
}