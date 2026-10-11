#requires -Version 7.0

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [guid]$SubscriptionId,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-zA-Z0-9_.()-]{1,90}$')]
    [string]$ResourceGroup,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-zA-Z0-9_.()-]{1,64}$')]
    [string]$DeploymentName,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9-]{0,47}$')]
    [string]$Environment,

    [ValidateSet('Configure', 'Publish', 'Check')]
    [string]$Stage = 'Configure'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments, [switch]$Json)
    if ($Json) {
        $stderrPath = [System.IO.Path]::GetTempFileName()
        $requestedWhatIf = $WhatIfPreference
        try {
            $WhatIfPreference = $false
            $lines = @(& $Executable @Arguments 2> $stderrPath)
            if ($LASTEXITCODE -ne 0) {
                throw "$Executable failed; verify the explicit customer identity and target."
            }
            return ($lines -join "`n") | ConvertFrom-Json -AsHashtable -Depth 100
        }
        finally {
            [System.IO.File]::Delete($stderrPath)
            $WhatIfPreference = $requestedWhatIf
        }
    }
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Executable failed with exit code $LASTEXITCODE." }
}

$deployment = Invoke-Checked 'az' @(
    'deployment', 'group', 'show', '--subscription', $SubscriptionId.ToString(),
    '--resource-group', $ResourceGroup, '--name', $DeploymentName,
    '--query', 'properties.{provisioningState:provisioningState,foundation:outputs.ktFoundation.value}',
    '--only-show-errors', '--output', 'json'
) -Json
if ($deployment -isnot [hashtable] -or $deployment.provisioningState -cne 'Succeeded' -or
    $deployment.foundation -isnot [hashtable]) {
    throw 'Use a successful second-stage KT deployment for this exact scope.'
}
$foundation = $deployment.foundation
if ($foundation.schemaVersion -isnot [long] -and $foundation.schemaVersion -isnot [int]) {
    throw 'Unsupported KT deployment output schema.'
}
if ($foundation.schemaVersion -ne 1 -or $foundation.profile -cne 'kt-private-foundation' -or
    $foundation.subscriptionId -ne $SubscriptionId.ToString() -or
    $foundation.resourceGroup -ne $ResourceGroup -or
    $foundation.capabilityHostRequested -isnot [bool] -or
    $foundation.capabilityHostRequested -ne $true -or
    -not $foundation.ContainsKey('runtimeSetup')) {
    throw 'Use the current, successful second-stage KT deployment output for this exact scope.'
}
$setup = $foundation.runtimeSetup
foreach ($name in @('tenantId', 'subscriptionId')) {
    $parsed = [guid]::Empty
    if (-not [guid]::TryParse([string]$foundation[$name], [ref]$parsed)) {
        throw "Invalid KT identity field: $name."
    }
}
if ($foundation.location -notmatch '^[a-z0-9]+$' -or $setup -isnot [hashtable] -or
    ($setup.schemaVersion -isnot [int] -and $setup.schemaVersion -isnot [long]) -or
    $setup.schemaVersion -ne 1) {
    throw 'Unsupported KT runtime setup output.'
}
foreach ($name in @('modelDeploymentName', 'simpleModelDeploymentName', 'foundryAccountName',
    'projectName', 'projectResourceId', 'projectEndpoint', 'hostedAgentName', 'azureMcpServerUrl',
    'azureMcpProjectConnectionName')) {
    if (-not $setup.ContainsKey($name) -or $setup[$name] -isnot [string]) {
        throw "Missing KT runtime field: $name."
    }
}
if (-not $setup.modelDeploymentName -or -not $setup.azureMcpServerUrl -or
    -not $setup.azureMcpProjectConnectionName) {
    throw 'Configure an approved model deployment and the read-only Azure MCP URL/connection in the protected KT parameter file first.'
}
foreach ($name in @('foundryAccountName', 'projectName', 'hostedAgentName')) {
    if ($setup[$name] -cnotmatch '^[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}[a-zA-Z0-9]$') {
        throw "Invalid KT resource name: $name."
    }
}
foreach ($name in @('modelDeploymentName', 'simpleModelDeploymentName')) {
    if ($setup[$name] -and $setup[$name] -cnotmatch '^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$') {
        throw "Invalid model deployment name: $name."
    }
}
if (($setup.simpleModelDeploymentName -and
    $setup.simpleModelDeploymentName -eq $setup.modelDeploymentName) -or
    $setup.azureMcpProjectConnectionName -cnotmatch '^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$') {
    throw 'Invalid model tier or Azure MCP connection name.'
}
$mcpUri = $null
if (-not [uri]::TryCreate($setup.azureMcpServerUrl, [UriKind]::Absolute, [ref]$mcpUri) -or
    $mcpUri.Scheme -cne 'https' -or -not $mcpUri.Host -or $mcpUri.UserInfo -or
    $mcpUri.Query -or $mcpUri.Fragment) {
    throw 'Azure MCP must use an approved HTTPS URL without credentials or query.'
}
$expectedProjectId = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup/providers/Microsoft.CognitiveServices/accounts/$($setup.foundryAccountName)/projects/$($setup.projectName)"
$expectedEndpoint = "https://$($setup.foundryAccountName).services.ai.azure.com/api/projects/$($setup.projectName)"
if ($setup.projectResourceId -ne $expectedProjectId -or $setup.projectEndpoint -ne $expectedEndpoint) {
    throw 'The KT output project identity or endpoint does not match its declared resource names.'
}
$roles = @(
    'coordinator', 'resource_graph', 'azure_mcp', 'azure_api', 'report_writer', 'quality_reviewer'
)
if ($setup.promptAgentNames -isnot [hashtable]) {
    throw 'The KT output must contain six distinct specialist Agent names.'
}
$actualRoles = ($setup.promptAgentNames.Keys | Sort-Object) -join ','
$expectedRoles = ($roles | Sort-Object) -join ','
$invalidNames = @($setup.promptAgentNames.Values | Where-Object {
    $_ -isnot [string] -or $_ -cnotmatch '^[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}[a-zA-Z0-9]$'
})
$uniqueNames = @($setup.promptAgentNames.Values | ForEach-Object { [string]$_ } | Sort-Object -Unique)
if ($actualRoles -cne $expectedRoles -or $invalidNames.Count -ne 0 -or
    $uniqueNames.Count -ne 6 -or $setup.hostedAgentName -in $uniqueNames) {
    throw 'The KT output must contain six distinct specialist Agent names.'
}
$bindings = @{
    AZURE_TENANT_ID = $foundation.tenantId
    AZURE_CLIENT_ID = ''
    AZURE_CLIENT_SECRET = ''
    AZURE_CLIENT_CERTIFICATE_PATH = ''
    AZURE_SUBSCRIPTION_ID = $SubscriptionId.ToString()
    AZURE_RESOURCE_GROUP = $ResourceGroup
    AZURE_LOCATION = $foundation.location
    AZURE_AI_PROJECT_ENDPOINT = $setup.projectEndpoint
    AZURE_AI_PROJECT_ID = $setup.projectResourceId
    AZURE_AI_ACCOUNT_NAME = $setup.foundryAccountName
    AZURE_AI_PROJECT_NAME = $setup.projectName
    AZURE_AI_MODEL_DEPLOYMENT_NAME = $setup.modelDeploymentName
    FOUNDRY_PROJECT_ENDPOINT = $setup.projectEndpoint
    FOUNDRY_HOSTED_AGENT_NAME = $setup.hostedAgentName
    FOUNDRY_MODEL_DEPLOYMENT = $setup.modelDeploymentName
    FOUNDRY_CORE_MODEL_DEPLOYMENT = $setup.modelDeploymentName
    FOUNDRY_SIMPLE_MODEL_DEPLOYMENT = $setup.simpleModelDeploymentName
    FOUNDRY_CORE_REASONING_EFFORT = 'medium'
    AZURE_MCP_SERVER_URL = $setup.azureMcpServerUrl
    AZURE_MCP_PROJECT_CONNECTION_NAME = $setup.azureMcpProjectConnectionName
    AZURE_MONITOR_INGESTION_ENDPOINT = ''
    AZURE_MONITOR_DCR_RULE_ID = ''
    AZURE_MONITOR_DCR_STREAM_NAME = ''
    APPLICATIONINSIGHTS_CONNECTION_STRING = ''
}
if ($setup.simpleModelDeploymentName) { $bindings.FOUNDRY_MODEL_DEPLOYMENT = '' }
foreach ($role in $roles) {
    $name = $role.ToUpperInvariant()
    $bindings["FOUNDRY_${name}_AGENT_NAME"] = $setup.promptAgentNames[$role]
    $bindings["AZBRIEF_PROMPT_${name}_AGENT_NAME"] = $setup.promptAgentNames[$role]
}
if (-not $PSCmdlet.ShouldProcess($expectedProjectId, "KT Agent setup: $Stage in $Environment")) {
    return
}
if (Test-Path -LiteralPath (Join-Path $repoRoot '.env')) {
    throw 'Use a clean customer clone without a root developer .env.'
}
$account = Invoke-Checked 'az' @('account', 'show', '--only-show-errors', '--output', 'json') -Json
if ($account.id -ne $SubscriptionId.ToString() -or $account.tenantId -ne $foundation.tenantId) {
    throw 'Default Azure CLI login must match the KT tenant/subscription. Do not switch it implicitly.'
}
$previousProcessEnvironment = @{}
foreach ($entry in Get-ChildItem Env:) { $previousProcessEnvironment[$entry.Name] = $entry.Value }
try {
    $env:AZURE_DEV_USER_AGENT = 'microsoft_foundry_skill'
    $activate = Join-Path $repoRoot '.venv\Scripts\Activate.ps1'
    if (-not (Test-Path -LiteralPath $activate)) { throw 'Prepare the customer virtual environment first.' }
    & $activate
    $insightsName = if ($setup.ContainsKey('applicationInsightsName')) { $setup.applicationInsightsName } else { '' }
    if ($insightsName) {
        if ($insightsName -cnotmatch '^[a-zA-Z0-9][a-zA-Z0-9._()-]{0,254}$') {
            throw 'Invalid existing Application Insights name.'
        }
        $insightsId = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup/providers/Microsoft.Insights/components/$insightsName"
        $insights = Invoke-Checked 'az' @(
            'resource', 'show', '--ids', $insightsId, '--subscription', $SubscriptionId.ToString(),
            '--api-version', '2020-02-02', '--query', 'properties', '--only-show-errors', '--output', 'json'
        ) -Json
        if ($insights.DisableLocalAuth -isnot [bool] -or $insights.DisableLocalAuth -ne $true -or
            -not $insights.ConnectionString) {
            throw 'Application Insights must be Entra-only and match the approved KT binding.'
        }
        $bindings.APPLICATIONINSIGHTS_CONNECTION_STRING = $insights.ConnectionString
    }
    $environmentPath = Join-Path $repoRoot ".azure\$Environment"
    if (Test-Path -LiteralPath $environmentPath) {
        $existing = Invoke-Checked 'azd' @(
            'env', 'get-values', '--cwd', $repoRoot, '--environment', $Environment, '--output', 'json'
        ) -Json
        foreach ($name in @('AZURE_TENANT_ID', 'AZURE_SUBSCRIPTION_ID', 'AZURE_AI_PROJECT_ID', 'AZURE_AI_PROJECT_ENDPOINT')) {
            if ($existing[$name] -and $existing[$name] -ne $bindings[$name]) {
                throw "Refusing to rebind another customer environment: $name."
            }
        }
    }
    elseif ($Stage -ne 'Configure') { throw 'Run Configure for this explicit KT environment first.' }
    if ($Stage -eq 'Configure') {
        $configPath = Join-Path $repoRoot '.azure\config.json'
        $previousDefault = ''
        if (Test-Path -LiteralPath $configPath) {
            $config = Get-Content -LiteralPath $configPath -Raw -Encoding utf8 |
                ConvertFrom-Json -AsHashtable
            if ($config.ContainsKey('defaultEnvironment')) { $previousDefault = $config.defaultEnvironment }
        }
        try {
            if (-not (Test-Path -LiteralPath $environmentPath)) {
                Invoke-Checked 'azd' @('env', 'new', $Environment, '--cwd', $repoRoot,
                    '--subscription', $SubscriptionId.ToString(), '--location', $foundation.location,
                    '--no-prompt')
            }
            foreach ($name in $bindings.Keys) {
                Invoke-Checked 'azd' @('env', 'set', $name, [string]$bindings[$name],
                    '--cwd', $repoRoot, '--environment', $Environment, '--no-prompt')
            }
        }
        finally {
            if ($previousDefault -and $previousDefault -ne $Environment) {
                Invoke-Checked 'azd' @('env', 'select', $previousDefault, '--cwd', $repoRoot, '--no-prompt')
            }
        }
        return
    }
    if ($insightsName) {
        $connection = Invoke-Checked 'az' @(
            'resource', 'show', '--ids', "$expectedProjectId/connections/azbrief-app-insights",
            '--subscription', $SubscriptionId.ToString(), '--api-version', '2025-06-01',
            '--query', 'properties', '--only-show-errors', '--output', 'json'
        ) -Json
        if ($connection.category -cne 'AppInsights' -or $connection.authType -cne 'ProjectManagedIdentity' -or
            $connection.target -ne $insightsId -or $connection.metadata.ResourceId -ne $insightsId -or
            $connection.metadata.ApplicationInsightsConnectionString -cne $bindings.APPLICATIONINSIGHTS_CONNECTION_STRING) {
            throw 'The Hosted telemetry project connection does not match the approved component.'
        }
    }
    foreach ($name in $bindings.Keys) {
        if ([string]$existing[$name] -cne [string]$bindings[$name]) {
            throw "KT environment binding changed: $name. Run Configure against the approved output."
        }
        [Environment]::SetEnvironmentVariable($name, [string]$bindings[$name], 'Process')
    }
    Push-Location $repoRoot
    try {
        if ($Stage -eq 'Publish') {
            if (@(& git --no-pager status --porcelain).Count -ne 0 -or $LASTEXITCODE -ne 0) {
                throw 'Publish from a clean reviewed source revision.'
            }
            Invoke-Checked 'python' @('-c', 'import src')
            $previousOtel = $env:OTEL_SDK_DISABLED
            try {
                $env:OTEL_SDK_DISABLED = 'true'
                Invoke-Checked 'python' @('-m', 'pytest', 'tests\', '-o', 'addopts=', '-x')
            }
            finally { $env:OTEL_SDK_DISABLED = $previousOtel }
            Invoke-Checked 'python' @('-m', 'scripts.provision_foundry_agents')
        }
        Invoke-Checked 'python' @('-m', 'scripts.provision_foundry_agents', '--check')
        if ($Stage -eq 'Publish') {
            & (Join-Path $PSScriptRoot 'deploy_hosted_agent.ps1') -Environment $Environment
        }
        $agent = Invoke-Checked 'azd' @(
            'ai', 'agent', 'show', 'azbrief-analysis-hosted', '--cwd', $repoRoot,
            '--environment', $Environment, '--no-prompt', '--output', 'json'
        ) -Json
        if ($agent.name -cne $setup.hostedAgentName -or $agent.status -cne 'active' -or -not $agent.version) {
            throw 'The intended Hosted Agent must have an active version.'
        }
        [pscustomobject]@{ Stage = $Stage; Environment = $Environment; Agent = $agent.name; Version = $agent.version }
    }
    finally { Pop-Location }
}
finally {
    foreach ($entry in Get-ChildItem Env:) {
        if (-not $previousProcessEnvironment.ContainsKey($entry.Name)) {
            [Environment]::SetEnvironmentVariable($entry.Name, $null, 'Process')
        }
    }
    foreach ($name in $previousProcessEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previousProcessEnvironment[$name], 'Process')
    }
}
