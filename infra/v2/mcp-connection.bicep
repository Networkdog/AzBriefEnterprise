targetScope = 'resourceGroup'

param foundryAccountName string
param projectName string
param connectionName string
param serverUrl string
param clientId string

resource connection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-10-01-preview' = {
  name: '${foundryAccountName}/${projectName}/${connectionName}'
  properties: {
    #disable-next-line BCP036
    category: 'RemoteTool'
    #disable-next-line BCP036
    authType: 'ProjectManagedIdentity'
    target: serverUrl
    #disable-next-line BCP037
    audience: 'api://${clientId}'
    isSharedToAll: true
  }
}
