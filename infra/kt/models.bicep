targetScope = 'resourceGroup'

param foundryAccountName string
param coreDeploymentName string
param simpleDeploymentName string
param coreModelName string
param simpleModelName string
param modelVersion string
param simpleModelVersion string = modelVersion
param modelCapacity int
param simpleModelCapacity int
param reuseCore bool = false
param reuseSimple bool = false
param useCoreForSimple bool = false

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryAccountName
}

resource core 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = if (!reuseCore) {
  parent: account
  name: coreDeploymentName
  sku: {
    name: 'GlobalStandard'
    capacity: modelCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: coreModelName
      version: modelVersion
    }
    versionUpgradeOption: 'NoAutoUpgrade'
  }
}

resource simple 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = if (!reuseSimple) {
  parent: account
  name: simpleDeploymentName
  sku: {
    name: 'GlobalStandard'
    capacity: simpleModelCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: useCoreForSimple ? coreModelName : simpleModelName
      version: useCoreForSimple ? modelVersion : simpleModelVersion
    }
    versionUpgradeOption: 'NoAutoUpgrade'
  }
  dependsOn: [
    core
  ]
}

#disable-next-line use-resource-symbol-reference
output coreModel object = reuseCore ? reference(resourceId('Microsoft.CognitiveServices/accounts/deployments', foundryAccountName, coreDeploymentName), '2025-06-01').model : core!.properties.model!
#disable-next-line use-resource-symbol-reference
output simpleModel object = reuseSimple ? reference(resourceId('Microsoft.CognitiveServices/accounts/deployments', foundryAccountName, simpleDeploymentName), '2025-06-01').model : simple!.properties.model!
