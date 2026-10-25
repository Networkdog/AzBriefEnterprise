targetScope = 'resourceGroup'

metadata description = 'Explicit preauthorized deployment identity only. No Graph consent, RBAC, networking or runtime identity changes.'

param infrastructure object
param infrastructureOptions object
param automation object
param mcpPrincipalId string
param runId string = utcNow()

resource installer 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  scope: resourceGroup(split(automation.identityResourceId, '/')[2], split(automation.identityResourceId, '/')[4])
  name: last(split(automation.identityResourceId, '/'))
}
resource executionSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' existing = {
  scope: resourceGroup(split(automation.subnetResourceId, '/')[2], split(automation.subnetResourceId, '/')[4])
  name: '${split(automation.subnetResourceId, '/')[8]}/${split(automation.subnetResourceId, '/')[10]}'
}
resource executionStorage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  scope: resourceGroup(split(automation.storageAccountResourceId, '/')[2], split(automation.storageAccountResourceId, '/')[4])
  name: last(split(automation.storageAccountResourceId, '/'))
}
resource executionNetwork 'Microsoft.Network/virtualNetworks@2024-05-01' existing = {
  scope: resourceGroup(split(automation.subnetResourceId, '/')[2], split(automation.subnetResourceId, '/')[4])
  name: split(automation.subnetResourceId, '/')[8]
}
resource approvedSetup 'Microsoft.Resources/deployments@2025-04-01' = {
  name: 'v2-mcp-automation-prerequisites'
  properties: {
    mode: 'Incremental'
    parameters: {
      approved: {
        value: !contains([
          length(automation) == 3
          length(string(union(infrastructureOptions, {
            azureMcpClientId: '00000000-0000-0000-0000-000000000000'
            azureMcpServicePrincipalId: '00000000-0000-0000-0000-000000000000'
            azureMcpAppRoleId: '00000000-0000-0000-0000-000000000000'
          }))) <= 2048
          toLower(installer.id) == toLower(automation.identityResourceId)
          toLower(installer.properties.tenantId) == toLower(tenant().tenantId)
          toLower(split(installer.id, '/')[2]) == toLower(subscription().subscriptionId)
          toLower(installer.properties.principalId) != toLower(infrastructure.foundryProjectPrincipalId)
          toLower(installer.properties.principalId) != toLower(infrastructure.controlPlanePrincipalId)
          toLower(installer.properties.principalId) != toLower(mcpPrincipalId)
          toLower(executionSubnet.id) == toLower(automation.subnetResourceId)
          toLower(split(executionSubnet.id, '/')[2]) == toLower(subscription().subscriptionId)
          executionSubnet.properties.provisioningState == 'Succeeded'
          toLower(replace(executionNetwork.location, ' ', '')) == toLower(replace(infrastructure.location, ' ', ''))
          length(executionSubnet.properties.delegations ?? []) == 1
          executionSubnet.properties.delegations[?0].?properties.serviceName == 'Microsoft.ContainerInstance/containerGroups'
          !contains([infrastructure.foundrySubnetResourceId, infrastructure.infrastructureSubnetId, infrastructure.privateEndpointSubnetId], executionSubnet.id)
          toLower(executionStorage.id) == toLower(automation.storageAccountResourceId)
          toLower(split(executionStorage.id, '/')[2]) == toLower(subscription().subscriptionId)
          toLower(executionStorage.id) != toLower(infrastructure.storageAccountResourceId)
          executionStorage.properties.provisioningState == 'Succeeded'
          contains(['StorageV2', 'FileStorage'], executionStorage.kind)
          executionStorage.properties.publicNetworkAccess == 'Disabled' || executionStorage.properties.networkAcls.defaultAction == 'Deny'
          toLower(replace(executionStorage.location, ' ', '')) == toLower(replace(infrastructure.location, ' ', ''))
        ], false)
      }
    }
    template: {
      '$schema': 'https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#'
      contentVersion: '1.0.0.0'
      parameters: {
        approved: {
          type: 'bool'
          allowedValues: [true]
          metadata: { description: 'MCP automation requires distinct preapproved setup identity, ACI subnet and private/restricted setup File Storage. Existing workload resources are never converted.' }
        }
      }
      resources: []
    }
  }
}
var roleValue = 'Mcp.Tools.ReadWrite.All'
var scopeValue = 'Mcp.Tools.ReadWrite'
var setup = {
  tenantId: tenant().tenantId
  installerPrincipalId: installer.properties.principalId
  projectPrincipalId: infrastructure.foundryProjectPrincipalId
  controlPlanePrincipalId: infrastructure.controlPlanePrincipalId
  mcpPrincipalId: mcpPrincipalId
  workloadResourceGroupId: resourceGroup().id
  uniqueName: infrastructure.azureMcpEntraAppUniqueName
  appRoleId: guid(subscription().id, infrastructure.azureMcpEntraAppUniqueName, roleValue)
  scopeId: guid(subscription().id, infrastructure.azureMcpEntraAppUniqueName, scopeValue)
}

resource directory 'Microsoft.Resources/deploymentScripts@2023-08-01' = {
  name: 'azbrief-mcp-authentication'
  location: infrastructure.location
  tags: infrastructure.tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${installer.id}': {} }
  }
  kind: 'AzureCLI'
  properties: {
    azCliVersion: '2.89.1'
    forceUpdateTag: runId
    timeout: 'PT15M'
    retentionInterval: 'P1D'
    cleanupPreference: 'Always'
    storageAccountSettings: {
      storageAccountName: last(split(automation.storageAccountResourceId, '/'))
    }
    containerSettings: {
      subnetIds: [{ id: automation.subnetResourceId }]
    }
    environmentVariables: [{ name: 'AZBRIEF_MCP_SETUP', value: string(setup) }]
    scriptContent: 'set -euo pipefail\npython3 - <<\'AZBRIEF_PY\'\n${loadTextContent('../../scripts/provision_mcp_identity.py')}\nAZBRIEF_PY\n'
  }
  dependsOn: [approvedSetup]
}

module handoff 'mcp-authentication-handoff.bicep' = {
  name: 'v2-mcp-automatic-handoff'
  params: {
    infrastructure: infrastructure
    infrastructureOptions: infrastructureOptions
    authentication: {
      clientId: directory.properties.outputs.clientId
      servicePrincipalId: directory.properties.outputs.servicePrincipalId
      appRoleId: directory.properties.outputs.appRoleId
    }
  }
}

output authentication object = handoff.outputs.authentication
