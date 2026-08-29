@description('Location for all resources.')
param location string = resourceGroup().location

@description('Azure Container App name and resource prefix.')
param name string

@description('Application Insights connection string.')
@secure()
param appInsightsConnectionString string

@description('Whether Azure MCP telemetry is enabled.')
param azureMcpCollectTelemetry string

@description('Microsoft Entra tenant ID.')
param azureAdTenantId string

@description('Microsoft Entra application client ID used for incoming authentication.')
param azureAdClientId string

@description('Default subscription exposed to read-only Azure MCP tools.')
param targetSubscriptionId string

@description('Pinned official Azure MCP Server image.')
param azureMcpImage string

@description('Azure MCP namespaces exposed to the impact Agent. Keep this list narrow and read-only.')
@minLength(1)
@maxLength(3)
param namespaces array = [
  'group'
  'resourcehealth'
  'advisor'
]

@description('Number of CPU cores allocated to the server.')
param cpuCores string = '0.5'

@description('Memory allocated to the server.')
param memorySize string = '1Gi'

@description('Minimum number of replicas.')
param minReplicas int = 1

@description('Maximum number of replicas.')
param maxReplicas int = 3

param tags object = {}

var environmentName = '${name}-env'
var baseArgs = [
  '--transport'
  'http'
  '--outgoing-auth-strategy'
  'UseHostingEnvironmentIdentity'
  '--mode'
  'all'
  '--read-only'
]
var namespaceArgs = [for namespace in namespaces: ['--namespace', namespace]]
var serverArgs = flatten(concat([baseArgs], namespaceArgs))
var baseEnvironment = [
  {
    name: 'ASPNETCORE_ENVIRONMENT'
    value: 'Production'
  }
  {
    name: 'ASPNETCORE_URLS'
    value: 'http://+:8080'
  }
  {
    name: 'AZURE_TOKEN_CREDENTIALS'
    value: 'managedidentitycredential'
  }
  {
    name: 'AZURE_MCP_INCLUDE_PRODUCTION_CREDENTIALS'
    value: 'true'
  }
  {
    name: 'AZURE_MCP_COLLECT_TELEMETRY'
    value: azureMcpCollectTelemetry
  }
  {
    name: 'AZURE_SUBSCRIPTION_ID'
    value: targetSubscriptionId
  }
  {
    name: 'AZURE_TENANT_ID'
    value: azureAdTenantId
  }
  {
    name: 'AzureAd__Instance'
    value: environment().authentication.loginEndpoint
  }
  {
    name: 'AzureAd__TenantId'
    value: azureAdTenantId
  }
  {
    name: 'AzureAd__ClientId'
    value: azureAdClientId
  }
  {
    name: 'AZURE_LOG_LEVEL'
    value: 'Information'
  }
  {
    name: 'AZURE_MCP_DANGEROUSLY_DISABLE_HTTPS_REDIRECTION'
    value: 'true'
  }
  {
    name: 'AZURE_MCP_DANGEROUSLY_ENABLE_FORWARDED_HEADERS'
    value: 'true'
  }
]

resource containerAppsEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  tags: tags
  properties: {}
}

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: name
  location: location
  tags: union(tags, {
    product: 'azbrief'
    component: 'azure-mcp-server'
  })
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: containerAppsEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
        allowInsecure: false
        transport: 'http'
        traffic: [
          {
            weight: 100
            latestRevision: true
          }
        ]
      }
    }
    template: {
      containers: [
        {
          name: name
          image: azureMcpImage
          command: []
          args: serverArgs
          resources: {
            cpu: json(cpuCores)
            memory: memorySize
          }
          env: concat(
            baseEnvironment,
            !empty(appInsightsConnectionString)
              ? [
                  {
                    name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
                    value: appInsightsConnectionString
                  }
                ]
              : []
          )
          probes: [
            {
              type: 'Startup'
              tcpSocket: {
                port: 8080
              }
              initialDelaySeconds: 5
              periodSeconds: 5
              failureThreshold: 30
            }
            {
              type: 'Liveness'
              tcpSocket: {
                port: 8080
              }
              periodSeconds: 30
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          {
            name: 'http-scaler'
            http: {
              metadata: {
                concurrentRequests: '20'
              }
            }
          }
        ]
      }
    }
  }
}

output containerAppResourceId string = containerApp.id
output containerAppUrl string = 'https://${containerApp.properties.configuration.ingress.fqdn}'
output containerAppName string = containerApp.name
output containerAppPrincipalId string = containerApp.identity.principalId
output containerAppEnvironmentId string = containerAppsEnvironment.id
