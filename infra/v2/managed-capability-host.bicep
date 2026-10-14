targetScope = 'resourceGroup'

metadata description = 'Basic VNet project Capability Host with Microsoft-managed agent data; no customer backing resource connections or data roles.'

param foundryAccountName string
param projectName string
param createHost bool = true

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryAccountName
}
resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' existing = {
  parent: account
  name: projectName
}

resource host 'Microsoft.CognitiveServices/accounts/projects/capabilityHosts@2025-04-01-preview' = if (createHost) {
  parent: project
  name: 'agents'
  properties: {
    #disable-next-line BCP037
    capabilityHostKind: 'Agents'
  }
}