#requires -Version 7.0

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [guid]$SubscriptionId,
    [Parameter(Mandatory = $true)]
    [guid]$TenantId,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-zA-Z0-9_.()-]{1,90}$')]
    [string]$ResourceGroup,
    [Parameter(Mandatory = $true)]
    [string]$ParametersFile,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-z0-9][a-z0-9.-]*/[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$')]
    [string]$Image,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9-]{0,47}$')]
    [string]$Environment,
    [string]$ConfigurationFile = '',
    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,45}$')]
    [string]$DeploymentName = 'azbrief-kt',
    [ValidateSet('Plan', 'Preflight', 'Deploy')]
    [string]$Mode = 'Plan',
    [string]$ReportPath = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$previousEnvironment = @{}
foreach ($entry in Get-ChildItem Env:) { $previousEnvironment[$entry.Name] = $entry.Value }
$previousEncoding = [Console]::OutputEncoding
$report = $null

function Read-ObjectFile {
    param([string]$Path)
    try {
        $value = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 |
            ConvertFrom-Json -AsHashtable -Depth 100
    }
    catch { throw 'An input JSON file is missing or invalid. No input values are logged.' }
    if ($value -isnot [hashtable]) { throw 'Input JSON must be an object.' }
    return $value
}

function Save-InstallReport {
    if (-not $ReportPath) { return }
    [IO.Directory]::CreateDirectory((Split-Path $ReportPath -Parent)) | Out-Null
    [IO.File]::WriteAllText($ReportPath, ($report | ConvertTo-Json -Depth 12),
        [Text.UTF8Encoding]::new($false))
}

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments, [switch]$Json)
    if ($Json) {
        $stderrPath = [IO.Path]::GetTempFileName()
        try {
            $lines = @(& $Executable @Arguments 2> $stderrPath)
            if ($LASTEXITCODE -ne 0) { throw 'A deployment command failed; the next stage was not started.' }
            return ($lines -join "`n") | ConvertFrom-Json -AsHashtable -Depth 100
        }
        finally { [IO.File]::Delete($stderrPath) }
    }
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'A deployment command failed; the next stage was not started.' }
}

function Invoke-InstallStage {
    param([string]$Name, [scriptblock]$Action)
    $row = @($report.stages | Where-Object { $_.name -eq $Name })[0]
    $row.status = 'running'
    Save-InstallReport
    & $Action
    $row.status = 'completed'
    Save-InstallReport
}

function Test-CompiledTemplates {
    $compiler = Get-Command bicep -ErrorAction SilentlyContinue
    $executable = if ($compiler) { $compiler.Name } else { Join-Path $HOME '.azure/bin/bicep.exe' }
    if (-not $compiler -and -not (Test-Path -LiteralPath $executable)) {
        throw 'Install the CI-pinned Bicep 0.46.1 compiler before deployment.'
    }
    $version = @(& $executable --version) -join "`n"
    if ($LASTEXITCODE -ne 0 -or $version -notmatch '\b0\.46\.1\b') {
        throw 'The Bicep compiler must match CI version 0.46.1.'
    }
    $temporary = Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString('N'))
    [IO.Directory]::CreateDirectory($temporary) | Out-Null
    try {
        foreach ($name in @('infrastructure', 'application', 'runtime-roles')) {
            $source = Join-Path $repoRoot "infra/v2/$name.bicep"
            $output = Join-Path $temporary "$name.json"
            Invoke-Checked $executable @('build', $source, '--outfile', $output)
            if ((Get-FileHash -LiteralPath $output -Algorithm SHA256).Hash -cne
                (Get-FileHash -LiteralPath (Join-Path $repoRoot "infra/v2/$name.json") -Algorithm SHA256).Hash) {
                throw 'A compiled deployment artifact differs from its reviewed Bicep source.'
            }
        }
    }
    finally { [IO.Directory]::Delete($temporary, $true) }
}

try {
    $activate = Join-Path $repoRoot '.venv/Scripts/Activate.ps1'
    if (-not (Test-Path -LiteralPath $activate -PathType Leaf)) {
        throw 'Prepare the customer Python virtual environment first.'
    }
    & $activate
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
    $ParametersFile = (Resolve-Path -LiteralPath $ParametersFile).Path
    if ($ConfigurationFile) { $ConfigurationFile = (Resolve-Path -LiteralPath $ConfigurationFile).Path }
    if ($ReportPath) {
        $ReportPath = [IO.Path]::GetFullPath($ReportPath)
        if (Test-Path -LiteralPath $ReportPath) { throw 'Use a new report path; existing files are never overwritten.' }
    }
    $inputDocument = Read-ObjectFile $ParametersFile
    if (-not $inputDocument.ContainsKey('parameters') -or $inputDocument.parameters -isnot [hashtable]) {
        throw 'Use an ARM parameters file containing literal parameter values.'
    }
    $configuration = if ($ConfigurationFile) { Read-ObjectFile $ConfigurationFile } else { @{} }
    $infrastructure = Read-ObjectFile (Join-Path $repoRoot 'infra/v2/infrastructure.json')
    $application = Read-ObjectFile (Join-Path $repoRoot 'infra/v2/application.json')
    $blockers = [Collections.Generic.List[string]]::new()
    $serializedInfrastructure = $infrastructure | ConvertTo-Json -Depth 100 -Compress
    $serializedApplication = $application | ConvertTo-Json -Depth 100 -Compress
    $mcpMode = $infrastructure.parameters['mcpAuthenticationMode']
    $requestedMcpMode = $inputDocument.parameters['mcpAuthenticationMode']
    $effectiveMcpMode = if ($requestedMcpMode) { $requestedMcpMode.value } elseif ($mcpMode) { $mcpMode.defaultValue } else { '' }
    if (-not $mcpMode -or $mcpMode.allowedValues -notcontains 'foundrySubnetTrust' -or
        $effectiveMcpMode -cne 'foundrySubnetTrust') {
        $blockers.Add('MCP_SUBNET_TRUST_UNAVAILABLE: the compiled template does not select a supported Foundry-subnet trust mode.')
    }
    if ($serializedInfrastructure -match '(?i)Microsoft\.Graph/|graph\.microsoft\.com') {
        $blockers.Add('MCP_DIRECTORY_DEPENDENCY: the current infrastructure still contains directory setup; subnet-trusted MCP is not implemented.')
    }
    if ($serializedApplication.Contains('ADMIN_CONFIG_BLOB_URL')) {
        $blockers.Add('BLOB_ADMIN_SETTINGS_DEPENDENCY: administrator settings are not yet environment-only.')
    }
    if ($configuration.ContainsKey('webAuthConfiguration') -and $configuration.webAuthConfiguration.Count) {
        $blockers.Add('SSO_PROHIBITED: webAuthConfiguration is not allowed for this customer profile.')
    }
    foreach ($field in @('appEnvironment', 'jobEnvironment')) {
        foreach ($entry in @($configuration[$field])) {
            if ($null -eq $entry) { continue }
            if ($entry -isnot [hashtable] -or -not $entry.ContainsKey('name')) {
                throw 'Environment entries must contain a name and value or secretRef.'
            }
            if ($entry.ContainsKey('value') -and $entry.name -match '(API_KEY|PASSWORD|SECRET|TOKEN)$') {
                throw 'Secret values must use secretRef, never ordinary environment values.'
            }
            if ($entry.name -in @('ADMIN_UI_ENABLED', 'ARCHIVE_UI_ENABLED') -and
                $entry.ContainsKey('value') -and [string]$entry.value -ine 'false') {
                $blockers.Add('LOCAL_WEB_AUTH_UNAVAILABLE: keep web UIs disabled until local authentication is implemented.')
            }
        }
    }
    if ($configuration.ContainsKey('jobConfiguration') -and
        $configuration.jobConfiguration.ContainsKey('triggerType') -and
        $configuration.jobConfiguration.triggerType -cne 'Manual') {
        throw 'Initial installation must retain a Manual Job; schedule activation requires operational acceptance.'
    }
    $sharedSettings = @('SUBSCRIBERS', 'REPORT_LANGUAGE', 'SCHEDULE_CRON_EXPRESSION',
        'ADMIN_CONFIGURATION_SOURCE', 'AUTOMATIC_RUN_TIMES_UTC')
    foreach ($setting in $sharedSettings) {
        $appEntry = @($configuration['appEnvironment'] | Where-Object { $null -ne $_ -and $_.name -eq $setting })
        $jobEntry = @($configuration['jobEnvironment'] | Where-Object { $null -ne $_ -and $_.name -eq $setting })
        if (($appEntry | ConvertTo-Json -Compress -Depth 4) -cne ($jobEntry | ConvertTo-Json -Compress -Depth 4)) {
            throw "App and Job must receive identical shared settings: $setting."
        }
    }
    $publishing = $inputDocument.parameters['foundryPublishingAccess']
    if ($publishing -and $publishing.value.Count) {
        $blockers.Add('PRIVATE_ACCEPTANCE_AND_CLOSURE_UNAVAILABLE: automated private-path acceptance and temporary public-access closure are not implemented.')
    }
    if (Test-Path -LiteralPath (Join-Path $repoRoot '.env')) {
        $blockers.Add('CLEAN_CUSTOMER_SOURCE_REQUIRED: use a reviewed customer clone without a root developer .env.')
    }
    $stageNames = @('LocalGates', 'InfrastructurePreflight', 'Infrastructure', 'Application',
        'ConfigureAgents', 'PublishAgents', 'HostedIdentity', 'HostedRoles', 'CheckAgents',
        'ApplicationReadback', 'OperationalAcceptance')
    $report = [ordered]@{
        schemaVersion = 1
        profile = 'kt-restricted-install-v1'
        mode = $Mode
        status = if ($blockers.Count) { 'blocked' } else { 'planned' }
        tenantId = $TenantId.ToString()
        subscriptionId = $SubscriptionId.ToString()
        resourceGroup = $ResourceGroup
        environment = $Environment
        parametersSha256 = (Get-FileHash -LiteralPath $ParametersFile -Algorithm SHA256).Hash
        configurationSha256 = if ($ConfigurationFile) { (Get-FileHash -LiteralPath $ConfigurationFile -Algorithm SHA256).Hash } else { '' }
        image = $Image
        azureWritesStarted = $false
        operationallyReady = $false
        scheduleEnabled = $false
        blockers = @($blockers)
        stages = @($stageNames | ForEach-Object { [ordered]@{ name = $_; status = 'not_started' } })
    }
    Save-InstallReport
    if ($Mode -eq 'Plan' -or $WhatIfPreference) {
        Write-Output ($report | ConvertTo-Json -Depth 12)
        return
    }
    if ($blockers.Count) {
        Write-Output ($report | ConvertTo-Json -Depth 12)
        throw 'Restricted KT installation is blocked before Azure access. Resolve every reported implementation gap first.'
    }
    if (-not $PSCmdlet.ShouldProcess("$SubscriptionId/$ResourceGroup", "KT installation: $Mode")) { return }
    Push-Location $repoRoot
    try {
        $env:AZURE_DEV_USER_AGENT = 'microsoft_foundry_skill'
        $env:PYTHONUTF8 = '1'
        $scope = @('--subscription', $SubscriptionId.ToString(), '--tenant', $TenantId.ToString(),
            '--resource-group', $ResourceGroup, '--name', $DeploymentName)
        Invoke-InstallStage 'LocalGates' {
            $dirty = @(Invoke-Checked 'git' @('status', '--porcelain'))
            if ($dirty.Count) { throw 'Publish from a clean reviewed customer source revision.' }
            Test-CompiledTemplates
            Invoke-Checked 'python' @('-c', 'import src')
            Invoke-Checked 'python' @('-m', 'scripts.build_v2_ui', '--check')
            $oldOtel = $env:OTEL_SDK_DISABLED
            try {
                $env:OTEL_SDK_DISABLED = 'true'
                Invoke-Checked 'python' @('-m', 'pytest', 'tests/', '-o', 'addopts=', '-x')
            }
            finally { [Environment]::SetEnvironmentVariable('OTEL_SDK_DISABLED', $oldOtel, 'Process') }
        }
        Invoke-InstallStage 'InfrastructurePreflight' {
            Invoke-Checked 'python' (@('-m', 'scripts.deploy_v2', 'infrastructure') + $scope +
                @('--parameters', $ParametersFile, '--mode', 'preflight'))
        }
        if ($Mode -eq 'Preflight') {
            $report.status = 'preflight_passed_not_deployed'
            Save-InstallReport
            return
        }
        $report.azureWritesStarted = $true
        Invoke-InstallStage 'Infrastructure' {
            Invoke-Checked 'python' (@('-m', 'scripts.deploy_v2', 'infrastructure') + $scope +
                @('--parameters', $ParametersFile, '--mode', 'deploy'))
        }
        $appArguments = @('-m', 'scripts.deploy_v2', 'application') + $scope +
            @('--image', $Image, '--no-automatic-setup')
        if ($ConfigurationFile) { $appArguments += @('--configuration', $ConfigurationFile) }
        Invoke-InstallStage 'Application' { Invoke-Checked 'python' ($appArguments + @('--mode', 'deploy')) }
        $agentArguments = @{
            SubscriptionId = $SubscriptionId
            ResourceGroup = $ResourceGroup
            DeploymentName = "$DeploymentName-complete"
            Environment = $Environment
        }
        Invoke-InstallStage 'ConfigureAgents' { & (Join-Path $PSScriptRoot 'setup_kt_agents.ps1') @agentArguments -Stage Configure }
        Invoke-InstallStage 'PublishAgents' { & (Join-Path $PSScriptRoot 'setup_kt_agents.ps1') @agentArguments -Stage Publish }
        $agent = $null
        Invoke-InstallStage 'HostedIdentity' {
            $script:agent = Invoke-Checked 'azd' @('ai', 'agent', 'show', 'azbrief-analysis-hosted',
                '--cwd', $repoRoot, '--environment', $Environment, '--no-prompt', '--output', 'json') -Json
            $hostedInput = $inputDocument.parameters['foundryHostedAgentName']
            $expectedHostedName = if ($hostedInput) { $hostedInput.value } else { $infrastructure.parameters.foundryHostedAgentName.defaultValue }
            if ($script:agent.name -cne $expectedHostedName -or $script:agent.status -cne 'active' -or -not $script:agent.version) {
                throw 'The intended Hosted Agent name and active version could not be verified.'
            }
            $principal = [guid]::Empty
            if (-not $script:agent.ContainsKey('instance_identity') -or
                -not [guid]::TryParse([string]$script:agent.instance_identity.principal_id, [ref]$principal) -or
                $principal -eq [guid]::Empty) { throw 'The platform-created Hosted Agent principal could not be verified.' }
        }
        Invoke-InstallStage 'HostedRoles' {
            Invoke-Checked 'python' (@('-m', 'scripts.deploy_v2', 'infrastructure') + $scope +
                @('--hosted-principal-id', $script:agent.instance_identity.principal_id, '--mode', 'deploy'))
        }
        Invoke-InstallStage 'CheckAgents' { & (Join-Path $PSScriptRoot 'setup_kt_agents.ps1') @agentArguments -Stage Check }
        Invoke-InstallStage 'ApplicationReadback' { Invoke-Checked 'python' ($appArguments + @('--mode', 'preflight')) }
        $report.status = 'deployed_pending_operational_acceptance'
        Save-InstallReport
        Write-Output ($report | ConvertTo-Json -Depth 12)
    }
    finally { Pop-Location }
}
catch {
    if ($null -ne $report) {
        if ($report.status -ne 'blocked') { $report.status = 'failed' }
        foreach ($row in $report.stages) {
            if ($row.status -eq 'running') { $row.status = 'failed' }
        }
        Save-InstallReport
    }
    throw
}
finally {
    [Console]::OutputEncoding = $previousEncoding
    foreach ($entry in @(Get-ChildItem Env:)) {
        if (-not $previousEnvironment.ContainsKey($entry.Name)) { Remove-Item -LiteralPath "Env:$($entry.Name)" }
    }
    foreach ($name in $previousEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], 'Process')
    }
}