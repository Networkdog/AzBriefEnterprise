#requires -Version 7.0

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F-]{36}$')]
    [string]$SubscriptionId,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ResourceGroup,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$DeploymentName,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9-]{0,47}$')]
    [string]$Environment,

    [ValidateSet('Configure', 'Mcp', 'Agents', 'Application', 'Verify', 'EnableSchedule')]
    [string]$Stage = 'Configure',

    [string]$Image = '',
    [string]$SetupFile = '',
    [string]$ServiceManagementReference = '',
    [switch]$AcceptOperationalChecks
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$mcpRoot = Join-Path $repoRoot 'infra/azure-mcp-server'

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments, [switch]$Json)

    if (-not $Json) {
        & $Executable @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "$Executable failed with exit code $LASTEXITCODE. The stage was not completed."
        }
        return
    }
    $requestedWhatIf = $WhatIfPreference
    $stderrPath = ''
    try {
        $WhatIfPreference = $false
        $stderrPath = [System.IO.Path]::GetTempFileName()
        $lines = @(& $Executable @Arguments 2> $stderrPath)
        if ($LASTEXITCODE -ne 0) {
            throw "$Executable failed with exit code $LASTEXITCODE. Check access and command prerequisites."
        }
        return ($lines -join "`n") | ConvertFrom-Json -AsHashtable -Depth 100
    }
    finally {
        if ($stderrPath) { [System.IO.File]::Delete($stderrPath) }
        $WhatIfPreference = $requestedWhatIf
    }
}

function Invoke-AzJson {
    param([string[]]$Arguments)
    return Invoke-Checked 'az' ($Arguments + @(
        '--subscription', $SubscriptionId, '--only-show-errors', '--output', 'json'
    )) -Json
}

function Invoke-AzdJson {
    param([string[]]$Arguments, [string]$Root = $repoRoot)
    return Invoke-Checked 'azd' ($Arguments + @(
        '--cwd', $Root, '--environment', $Environment, '--no-prompt', '--output', 'json'
    )) -Json
}

function Assert-SetupContract {
    param([hashtable]$Setup)
    if ($Setup.schemaVersion -ne 1) {
        throw 'Unsupported customerSetup contract. Deploy the current foundation template first.'
    }
    foreach ($name in @(
        'tenantId', 'subscriptionId', 'resourceGroup', 'location', 'foundryLocation',
        'foundryAccountName', 'foundryProjectName', 'foundryProjectId', 'foundryProjectEndpoint',
        'modelDeploymentName', 'hostedAgentName', 'containerAppName', 'schedulerJobName',
        'containerAppUrl', 'azureMcpContainerAppName', 'controlPlanePrincipalId',
        'scheduleDispatcherCronExpression', 'networkIsolationMode'
    )) {
        if (-not $Setup.ContainsKey($name) -or [string]::IsNullOrWhiteSpace($Setup[$name])) {
            throw "Missing customerSetup field: $name"
        }
    }
    foreach ($name in @('tenantId', 'subscriptionId', 'controlPlanePrincipalId')) {
        $parsedGuid = [guid]::Empty
        if (-not [guid]::TryParse($Setup[$name], [ref]$parsedGuid)) {
            throw "Invalid GUID in customerSetup: $name"
        }
    }
    if ($Setup.subscriptionId -ne $SubscriptionId -or $Setup.resourceGroup -ne $ResourceGroup) {
        throw 'Deployment outputs do not match the explicitly requested subscription/resource group.'
    }
    $expectedProjectId = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup/providers/" +
        "Microsoft.CognitiveServices/accounts/$($Setup.foundryAccountName)/projects/$($Setup.foundryProjectName)"
    if ($Setup.foundryProjectId -ne $expectedProjectId) {
        throw 'Foundry project resource ID does not belong to the deployment target.'
    }
    $expectedEndpoint = "https://$($Setup.foundryAccountName).services.ai.azure.com/api/projects/$($Setup.foundryProjectName)"
    if ($Setup.foundryProjectEndpoint.TrimEnd('/') -ne $expectedEndpoint) {
        throw 'Foundry endpoint does not match the deployed account and project.'
    }
    if ($Setup.networkIsolationMode -notin @('vnetInjection', 'perimeter', 'public')) {
        throw 'Unknown network isolation mode.'
    }
    if ($Setup.networkIsolationMode -eq 'vnetInjection' -and $Setup.foundryLocation -ne $Setup.location) {
        throw 'VNet isolation requires Foundry and deployment region to match.'
    }
    if ($Setup.hostedAgentName -notmatch '^[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}$' -or
        $Setup.containerAppUrl -notmatch '^https://[a-z0-9.-]+\.azurecontainerapps\.io$') {
        throw 'Invalid Hosted Agent name or Container App HTTPS origin.'
    }
    $agentNames = @($Setup.hostedAgentName)
    foreach ($role in @('coordinator', 'resourceGraph', 'azureMcp', 'azureApi', 'reportWriter', 'qualityReviewer')) {
        $name = [string]$Setup.specialistAgentNames[$role]
        if ($name -notmatch '^[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}$') {
            throw "Missing or invalid specialist agent name: $role"
        }
        $agentNames += $name
    }
    if (($agentNames | Sort-Object -Unique).Count -ne 7) {
        throw 'The Hosted Agent and six specialist names must all be distinct.'
    }
}

function Set-AzdBindings {
    param([hashtable]$Values, [string]$Root = $repoRoot)
    foreach ($name in $Values.Keys) {
        Invoke-Checked 'azd' @(
            'env', 'set', $name, [string]$Values[$name], '--cwd', $Root,
            '--environment', $Environment, '--no-prompt'
        )
    }
}

function Initialize-CustomerEnvironment {
    param([hashtable]$Values, [string]$Root = $repoRoot)
    $configurationPath = Join-Path $Root '.azure/config.json'
    $previousDefault = ''
    if (Test-Path -LiteralPath $configurationPath) {
        $configuration = Get-Content -LiteralPath $configurationPath -Raw -Encoding utf8 |
            ConvertFrom-Json -AsHashtable
        $previousDefault = [string]$configuration['defaultEnvironment']
    }
    try {
        if (Test-Path -LiteralPath (Join-Path $Root ".azure/$Environment")) {
            $existing = Invoke-AzdJson @('env', 'get-values') -Root $Root
            foreach ($name in @('AZURE_SUBSCRIPTION_ID', 'AZURE_TENANT_ID', 'AZURE_RESOURCE_GROUP', 'AZURE_AI_PROJECT_ID', 'FOUNDRY_PROJECT_RESOURCE_ID')) {
                if ($existing[$name] -and $Values[$name] -and $existing[$name] -ne $Values[$name]) {
                    throw "Refusing to rebind customer environment '$Environment': $name differs."
                }
            }
        }
        else {
            Invoke-Checked 'azd' @(
                'env', 'new', $Environment, '--subscription', $SubscriptionId,
                '--location', [string]$Values.AZURE_LOCATION, '--cwd', $Root, '--no-prompt'
            )
        }
        Set-AzdBindings $Values -Root $Root
    }
    finally {
        if ($previousDefault -and $previousDefault -ne $Environment) {
            Invoke-Checked 'azd' @('env', 'select', $previousDefault, '--cwd', $Root, '--no-prompt')
        }
    }
}

function Get-ControlPlane {
    $app = Invoke-AzJson @('containerapp', 'show', '--resource-group', $ResourceGroup, '--name', $setup.containerAppName)
    $job = Invoke-AzJson @('containerapp', 'job', 'show', '--resource-group', $ResourceGroup, '--name', $setup.schedulerJobName)
    foreach ($resource in @($app, $job)) {
        if (@($resource.properties.template.containers).Count -ne 1) {
            throw 'Expected exactly one container per control-plane resource.'
        }
        $settings = @{}
        foreach ($entry in $resource.properties.template.containers[0].env) {
            $settings[$entry.name] = $entry['value']
        }
        if ($settings.FOUNDRY_PROJECT_ENDPOINT -ne $setup.foundryProjectEndpoint -or
            $settings.FOUNDRY_HOSTED_AGENT_NAME -ne $setup.hostedAgentName) {
            throw 'The live control plane targets a different Foundry runtime.'
        }
    }
    return @{ App = $app; Job = $job }
}

function Assert-CustomerReadiness {
    $resources = Get-ControlPlane
    $app = $resources.App
    $job = $resources.Job
    $appImage = [string]$app.properties.template.containers[0].image
    if ($appImage -notmatch '@sha256:[a-f0-9]{64}$' -or
        $appImage -ne $job.properties.template.containers[0].image) {
        throw 'App and Job must use the same immutable image digest.'
    }
    if ($app.properties.configuration.ingress.targetPort -ne 8000 -or
        $app.properties.provisioningState -ne 'Succeeded' -or
        $app.properties.latestRevisionName -ne $app.properties.latestReadyRevisionName) {
        throw 'The application revision is not ready on port 8000.'
    }
    $revision = Invoke-AzJson @(
        'containerapp', 'revision', 'show', '--resource-group', $ResourceGroup,
        '--name', $setup.containerAppName, '--revision', $app.properties.latestReadyRevisionName
    )
    if ($revision.properties.healthState -ne 'Healthy' -or $revision.properties.runningState -ne 'Running') {
        throw 'The latest application revision is not Healthy/Running.'
    }
    if ($setup.networkIsolationMode -eq 'vnetInjection') {
        $account = Invoke-AzJson @('cognitiveservices', 'account', 'show', '--resource-group', $ResourceGroup, '--name', $setup.foundryAccountName)
        if ($account.properties.publicNetworkAccess -ne 'Disabled') {
            throw 'Close temporary Foundry public access before customer acceptance.'
        }
    }
    $agent = Invoke-AzdJson @('ai', 'agent', 'show', 'azbrief-analysis-hosted')
    if ($agent.name -ne $setup.hostedAgentName -or $agent.status -ne 'active') {
        throw 'The intended Hosted Agent is not active.'
    }
    Invoke-Checked 'python' @('-m', 'scripts.provision_foundry_agents', '--check')
    $health = Invoke-RestMethod -Uri "$($setup.containerAppUrl)/health" -TimeoutSec 30
    if ($health.status -ne 'healthy') {
        throw 'AzBrief health endpoint did not return healthy.'
    }
    return $resources
}

function Set-ApplicationImage {
    param([hashtable]$App, [string]$ImageReference, [int]$Port, [array]$Probes)
    $null = Invoke-AzJson @(
        'resource', 'update', '--ids', $App.id, '--api-version', '2024-03-01', '--set',
        "properties.template.containers[0].image=$ImageReference", "properties.configuration.ingress.targetPort=$Port",
        "properties.template.containers[0].probes[0].httpGet.port=$($Probes[0].httpGet.port)",
        "properties.template.containers[0].probes[0].httpGet.path=$($Probes[0].httpGet.path)",
        "properties.template.containers[0].probes[1].httpGet.port=$($Probes[1].httpGet.port)",
        "properties.template.containers[0].probes[1].httpGet.path=$($Probes[1].httpGet.path)"
    )
}

if ($SetupFile -and -not $WhatIfPreference) {
    throw '-SetupFile is for offline -WhatIf validation only. Live stages must read ARM outputs.'
}
if ($Stage -eq 'Application' -and $Image -notmatch '^[a-z0-9.-]+(?::[0-9]+)?/[a-z0-9._/-]+@sha256:[a-f0-9]{64}$') {
    throw '-Image must be an immutable registry/repository@sha256:<64 lowercase hex digits> reference.'
}
if ($Stage -eq 'EnableSchedule' -and -not $AcceptOperationalChecks) {
    throw 'Confirm the documented analysis, archive, authentication, and email acceptance checks with -AcceptOperationalChecks.'
}

Push-Location $repoRoot
try {
    $activateScript = Join-Path $repoRoot '.venv/Scripts/Activate.ps1'
    if (-not (Test-Path -LiteralPath $activateScript)) {
        $activateScript = Join-Path $repoRoot '.venv/bin/Activate.ps1'
    }
    if (-not (Test-Path -LiteralPath $activateScript)) {
        throw 'Create the project .venv and install requirements.txt first.'
    }
    . $activateScript
    if ($SetupFile) {
        $setup = Get-Content -LiteralPath $SetupFile -Raw -Encoding utf8 | ConvertFrom-Json -AsHashtable -Depth 100
    }
    else {
        $deployment = Invoke-AzJson @(
            'deployment', 'group', 'show', '--resource-group', $ResourceGroup,
            '--name', $DeploymentName, '--query', '{state:properties.provisioningState,setup:properties.outputs.customerSetup.value}'
        )
        if ($deployment.state -ne 'Succeeded') {
            throw 'The foundation deployment has not succeeded.'
        }
        $setup = $deployment.setup
    }
    Assert-SetupContract $setup
    [pscustomobject]@{
        Stage = $Stage
        Environment = $Environment
        Subscription = $SubscriptionId
        ResourceGroup = $ResourceGroup
        FoundryProject = $setup.foundryProjectId
        HostedAgent = $setup.hostedAgentName
        Network = $setup.networkIsolationMode
    } | Format-List
    if (-not $PSCmdlet.ShouldProcess("$ResourceGroup / $Environment", "Customer setup stage: $Stage")) {
        return
    }
    if (Test-Path -LiteralPath (Join-Path $repoRoot '.env')) {
        throw 'Use a clean customer clone without a root .env. Developer credentials/settings must not enter customer setup.'
    }
    $account = Invoke-Checked 'az' @('account', 'show', '--only-show-errors', '--output', 'json') -Json
    if ($account.tenantId -ne $setup.tenantId -or $account.id -ne $SubscriptionId) {
        throw 'The default Azure CLI login does not match the customer tenant/subscription. Run az login and az account set explicitly.'
    }
    $bindings = @{
        AZURE_TENANT_ID = $setup.tenantId
        AZURE_SUBSCRIPTION_ID = $SubscriptionId
        AZURE_RESOURCE_GROUP = $ResourceGroup
        AZURE_LOCATION = $setup.foundryLocation
        AZURE_AI_PROJECT_ENDPOINT = $setup.foundryProjectEndpoint
        AZURE_AI_PROJECT_ID = $setup.foundryProjectId
        AZURE_AI_ACCOUNT_NAME = $setup.foundryAccountName
        AZURE_AI_PROJECT_NAME = $setup.foundryProjectName
        AZURE_AI_MODEL_DEPLOYMENT_NAME = $setup.modelDeploymentName
        FOUNDRY_PROJECT_ENDPOINT = $setup.foundryProjectEndpoint
        FOUNDRY_MODEL_DEPLOYMENT = $setup.modelDeploymentName
        FOUNDRY_HOSTED_AGENT_NAME = $setup.hostedAgentName
        FOUNDRY_COORDINATOR_WEB_SEARCH_ENABLED = 'true'
        AZURE_MCP_PROJECT_CONNECTION_NAME = "$($setup.azureMcpContainerAppName)-read-only"
    }
    $roles = @{
        coordinator = 'COORDINATOR'; resourceGraph = 'RESOURCE_GRAPH'; azureMcp = 'AZURE_MCP'
        azureApi = 'AZURE_API'; reportWriter = 'REPORT_WRITER'; qualityReviewer = 'QUALITY_REVIEWER'
    }
    foreach ($role in $roles.Keys) {
        $bindings["FOUNDRY_$($roles[$role])_AGENT_NAME"] = $setup.specialistAgentNames[$role]
        $bindings["AZBRIEF_PROMPT_$($roles[$role])_AGENT_NAME"] = $setup.specialistAgentNames[$role]
    }
    if ($Stage -eq 'Configure') {
        Initialize-CustomerEnvironment $bindings
        Write-Host 'Customer environment configured. Next: Mcp, Agents, dedicated Hosted identity access, Application, Verify, then acceptance.'
        return
    }
    $values = Invoke-AzdJson @('env', 'get-values')
    foreach ($name in $bindings.Keys) {
        if ($values[$name] -ne $bindings[$name]) {
            throw "Customer azd binding mismatch: $name. Run Configure in the correct environment."
        }
    }
    $savedEnvironment = @{}
    try {
        $runtimeValues = $bindings.Clone()
        $runtimeValues.AZURE_MCP_SERVER_URL = [string]$values['AZURE_MCP_SERVER_URL']
        $runtimeValues.AZURE_CLIENT_ID = ''
        $runtimeValues.AZURE_CLIENT_SECRET = ''
        $runtimeValues.AZURE_CLIENT_CERTIFICATE_PATH = ''
        $runtimeValues.PYTHON_DOTENV_DISABLED = '1'
        $runtimeValues.AZBRIEF_VERBOSE = 'false'
        foreach ($name in $runtimeValues.Keys) {
            $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
            [Environment]::SetEnvironmentVariable($name, [string]$runtimeValues[$name], 'Process')
        }
        switch ($Stage) {
            'Mcp' {
                $null = Invoke-AzdJson @('ai', 'project', 'show')
                $env:AZURE_LOCATION = $setup.location
                Initialize-CustomerEnvironment @{
                    AZURE_TENANT_ID = $setup.tenantId; AZURE_SUBSCRIPTION_ID = $SubscriptionId
                    AZURE_RESOURCE_GROUP = $ResourceGroup; AZURE_LOCATION = $setup.location
                    AZURE_MCP_CONTAINER_APP_NAME = $setup.azureMcpContainerAppName
                    FOUNDRY_PROJECT_RESOURCE_ID = $setup.foundryProjectId
                    SERVICE_MANAGEMENT_REFERENCE = $ServiceManagementReference
                } -Root $mcpRoot
                Invoke-Checked 'azd' @('provision', '--cwd', $mcpRoot, '--environment', $Environment, '--no-prompt')
                $mcp = Invoke-AzdJson @('env', 'get-values') -Root $mcpRoot
                $env:AZURE_LOCATION = $setup.foundryLocation
                if ($mcp.AZURE_MCP_SERVER_URL -notmatch '^https://[a-z0-9.-]+\.azurecontainerapps\.io/?$' -or
                    -not $mcp.AZURE_MCP_ENTRA_APP_IDENTIFIER_URI) {
                    throw 'Azure MCP deployment did not return its HTTPS endpoint and Entra audience.'
                }
                Invoke-Checked 'azd' @(
                    'ai', 'connection', 'create', $bindings.AZURE_MCP_PROJECT_CONNECTION_NAME,
                    '--kind', 'remote-tool', '--target', $mcp.AZURE_MCP_SERVER_URL,
                    '--auth-type', 'project-managed-identity', '--audience', $mcp.AZURE_MCP_ENTRA_APP_IDENTIFIER_URI,
                    '--project-endpoint', $setup.foundryProjectEndpoint,
                    '--cwd', $repoRoot, '--environment', $Environment, '--no-prompt'
                )
                Set-AzdBindings @{ AZURE_MCP_SERVER_URL = $mcp.AZURE_MCP_SERVER_URL }
            }
            'Agents' {
                if (-not $runtimeValues.AZURE_MCP_SERVER_URL) { throw 'Complete the Mcp stage first.' }
                $dirtyFiles = @(& git status --porcelain --untracked-files=all)
                if ($LASTEXITCODE -ne 0 -or $dirtyFiles.Count -gt 0) {
                    throw 'Agent publication requires a clean, reviewed source checkout.'
                }
                Invoke-Checked 'python' @('-c', 'import src')
                Invoke-Checked 'python' @('-m', 'pytest', 'tests/', '-o', 'addopts=', '-x')
                Invoke-Checked 'python' @('-m', 'scripts.provision_foundry_agents')
                Invoke-Checked 'python' @('-m', 'scripts.provision_foundry_agents', '--check')
                Invoke-Checked 'azd' @('deploy', 'azbrief-analysis-hosted', '--cwd', $repoRoot, '--environment', $Environment, '--no-prompt')
                $agent = Invoke-AzdJson @('ai', 'agent', 'show', 'azbrief-analysis-hosted')
                if ($agent.name -ne $setup.hostedAgentName -or $agent.status -ne 'active') {
                    throw 'The intended Hosted Agent did not become active.'
                }
                Write-Host 'Grant the dedicated Hosted Agent principal Reader on the approved evidence scope, then run the acceptance checks in infra/CUSTOMER_DEPLOYMENT.md.'
            }
            'Application' {
                $resources = Get-ControlPlane
                if ($resources.Job.properties.configuration.triggerType -ne 'Manual') {
                    throw 'Initial setup requires a Manual Job. Use the documented guarded upgrade path for an active scheduler.'
                }
                $appContainer = $resources.App.properties.template.containers[0]
                $registry = $Image.Split('/')[0]
                if ($registry -ne $setup.containerRegistryServer -or
                    $registry -notin @($resources.App.properties.configuration.registries.server) -or
                    $registry -notin @($resources.Job.properties.configuration.registries.server)) {
                    throw 'The image registry must match the managed-identity registry configured on BOTH resources.'
                }
                if (@($appContainer.probes).Count -ne 2 -or
                    $appContainer.probes[0].type -ne 'Liveness' -or $appContainer.probes[1].type -ne 'Readiness') {
                    throw 'Unexpected probe contract; refusing to replace customer configuration.'
                }
                $newProbes = @(
                    @{ httpGet = @{ port = 8000; path = '/health' } }
                    @{ httpGet = @{ port = 8000; path = '/health' } }
                )
                try {
                    $null = Invoke-AzJson @('containerapp', 'job', 'update', '--name', $setup.schedulerJobName, '--resource-group', $ResourceGroup, '--image', $Image)
                    Set-ApplicationImage $resources.App $Image 8000 $newProbes
                    $after = Get-ControlPlane
                    if ($after.App.properties.template.containers[0].image -ne $Image -or
                        $after.Job.properties.template.containers[0].image -ne $Image -or
                        $after.App.properties.configuration.ingress.targetPort -ne 8000) {
                        throw 'Image/port updates did not match the requested deployment.'
                    }
                }
                catch {
                    $failure = $_
                    $previousJobImage = $resources.Job.properties.template.containers[0].image
                    $null = Invoke-AzJson @('containerapp', 'job', 'update', '--name', $setup.schedulerJobName, '--resource-group', $ResourceGroup, '--image', $previousJobImage)
                    Set-ApplicationImage $resources.App $appContainer.image $resources.App.properties.configuration.ingress.targetPort $appContainer.probes
                    throw "Application stage failed; prior image/port/probe updates were submitted for rollback. Verify their health before retrying. $failure"
                }
                Write-Host 'Both image updates submitted; scheduling remains manual. Run Verify after the new revision is Healthy/Running.'
            }
            'Verify' {
                $null = Assert-CustomerReadiness
                Write-Host 'Infrastructure/runtime readiness checks passed. Analysis, archive persistence, sign-in and email acceptance are separate required checks.'
            }
            'EnableSchedule' {
                $resources = Assert-CustomerReadiness
                $configuration = $resources.Job.properties.configuration.Clone()
                $configuration.triggerType = 'Schedule'
                $configuration.Remove('manualTriggerConfig')
                $configuration.Remove('eventTriggerConfig')
                $configuration.scheduleTriggerConfig = @{
                    cronExpression = $setup.scheduleDispatcherCronExpression
                    parallelism = 1
                    replicaCompletionCount = 1
                }
                $payload = @{ properties = @{ configuration = $configuration } } | ConvertTo-Json -Depth 100
                $payloadPath = [System.IO.Path]::GetTempFileName()
                try {
                    [System.IO.File]::WriteAllText($payloadPath, $payload, [System.Text.UTF8Encoding]::new($false))
                    $null = Invoke-AzJson @(
                        'rest', '--method', 'patch',
                        '--url', "https://management.azure.com$($resources.Job.id)?api-version=2024-03-01",
                        '--body', "@$payloadPath"
                    )
                }
                finally {
                    [System.IO.File]::Delete($payloadPath)
                }
                $job = Invoke-AzJson @('containerapp', 'job', 'show', '--name', $setup.schedulerJobName, '--resource-group', $ResourceGroup)
                if ($job.properties.configuration.triggerType -ne 'Schedule' -or
                    $job.properties.configuration.scheduleTriggerConfig.cronExpression -ne $setup.scheduleDispatcherCronExpression) {
                    throw 'Scheduler activation could not be verified.'
                }
                Write-Host 'Schedule enabled and verified. Retain acceptance evidence and monitor the first real digest.'
            }
        }
    }
    finally {
        foreach ($name in $savedEnvironment.Keys) {
            [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process')
        }
    }
}
finally {
    Pop-Location
}