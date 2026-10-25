targetScope = 'resourceGroup'

param access object = {}
param savedAccess object = {}
param completing bool = false
param newAccount bool
param managedAgentData bool

func ipNumber(address string) int => int(split(address, '.')[0]) * 16777216 + int(split(address, '.')[1]) * 65536 + int(split(address, '.')[2]) * 256 + int(split(address, '.')[3])
func ipText(number int) string => '${number / 16777216}.${number / 65536 % 256}.${number / 256 % 256}.${number % 256}'

var enabled = !empty(access)
var sourceIps = access.?publicIpRanges ?? []
var sourceSubnets = access.?subnetResourceIds ?? []
var blockSizes = [4294967296, 2147483648, 1073741824, 536870912, 268435456, 134217728, 67108864, 33554432, 16777216, 8388608, 4194304, 2097152, 1048576, 524288, 262144, 131072, 65536, 32768, 16384, 8192, 4096, 2048, 1024, 512, 256, 128, 64, 32, 16, 8, 4, 2, 1]
var excluded = [
  ['0.0.0.0', '0.255.255.255']
  ['10.0.0.0', '10.255.255.255']
  ['100.64.0.0', '100.127.255.255']
  ['127.0.0.0', '127.255.255.255']
  ['169.254.0.0', '169.254.255.255']
  ['172.16.0.0', '172.31.255.255']
  ['192.0.0.0', '192.0.0.255']
  ['192.0.2.0', '192.0.2.255']
  ['192.168.0.0', '192.168.255.255']
  ['198.18.0.0', '198.19.255.255']
  ['198.51.100.0', '198.51.100.255']
  ['203.0.113.0', '203.0.113.255']
  ['224.0.0.0', '255.255.255.255']
]
var parsed = [for rule in sourceIps: {
  source: rule
  address: split(rule, '/')[0]
  prefix: contains(rule, '/') ? int(split(rule, '/')[1]) : 32
  first: ipNumber(split(rule, '/')[0])
}]
var intervals = [for rule in parsed: union(rule, {
  last: rule.first + blockSizes[rule.prefix] - 1
})]
var validIps = map(intervals, rule => length(split(rule.source, '/')) <= 2 && length(split(rule.address, '.')) == 4 && rule.address == ipText(rule.first) && rule.prefix >= 0 && rule.prefix <= 32 && rule.first % blockSizes[rule.prefix] == 0 && length(filter(excluded, range => rule.first <= ipNumber(range[1]) && rule.last >= ipNumber(range[0]))) == 0 && length(filter(intervals, other => rule.first <= other.last && rule.last >= other.first)) == 1)
var ipGroups = [for rule in intervals: rule.prefix == 31 ? [ipText(rule.first), ipText(rule.last)] : [rule.prefix == 32 ? ipText(rule.first) : '${rule.address}/${rule.prefix}']]
var expandedIps = flatten(ipGroups)
var policy = enabled ? {
  publicIpRanges: sort(expandedIps, (left, right) => left < right)
  subnetResourceIds: sort(map(sourceSubnets, subnet => toLower(subnet)), (left, right) => left < right)
} : {}

module clientValidation 'foundry-publishing-subnet.bicep' = [for (subnet, index) in sourceSubnets: {
  name: 'v2-foundry-publishing-client-${index}'
  params: {
    subnetResourceId: subnet
  }
}]

resource validation 'Microsoft.Resources/deployments@2025-04-01' = {
  name: 'v2-foundry-publishing-policy-validation'
  dependsOn: [clientValidation]
  properties: {
    mode: 'Incremental'
    parameters: {
      approved: {
        value: (!enabled || (length(access) == 2 && contains(access, 'publicIpRanges') && contains(access, 'subnetResourceIds') && newAccount && managedAgentData && length(expandedIps) <= 100 && length(sourceSubnets) <= 100 && length(expandedIps) + length(sourceSubnets) > 0 && length(union(sourceSubnets, sourceSubnets)) == length(sourceSubnets) && !contains(validIps, false))) && (!completing || policy == savedAccess)
      }
    }
    template: {
      '$schema': 'https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#'
      contentVersion: '1.0.0.0'
      parameters: {
        approved: {
          type: 'bool'
          allowedValues: [true]
          metadata: { description: 'Foundry publishing requires explicit public IPv4/exact ready client subnets, default deny, a new managed-data account and unchanged saved policy. Empty policy retains private-only access.' }
        }
      }
      resources: []
    }
  }
}

var ipRules = [for address in expandedIps: { value: address }]
var virtualNetworkRules = [for subnet in sourceSubnets: { id: subnet, ignoreMissingVnetServiceEndpoint: false }]

output normalizedAccess object = policy
output publicNetworkAccess string = enabled ? 'Enabled' : 'Disabled'
output networkAcls object = enabled ? {
  defaultAction: 'Deny'
  bypass: 'None'
  ipRules: ipRules
  virtualNetworkRules: virtualNetworkRules
} : { defaultAction: 'Deny' }