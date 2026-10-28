#requires -Version 7.0

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidateNotNullOrEmpty()]
    [string]$Environment = "hosted-dev",

    [ValidateNotNullOrEmpty()]
    [string]$Service = "azbrief-analysis-hosted",

    [string]$FromPackage = "",

    [string]$OutputPath = "",

    [switch]$PackageOnly,

    [switch]$AllowDirtyRuntime,

    [switch]$SkipTests,

    [switch]$SkipRosterCheck,

    [switch]$SkipDoctor,

    [switch]$SkipSmokeTest,

    [string]$SmokeUpdateUrl = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$activateScript = @(
    (Join-Path $repoRoot '.venv/Scripts/Activate.ps1')
    (Join-Path $repoRoot '.venv/bin/Activate.ps1')
) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
$runtimePathPattern = '^(src/|hosted_agent_main\.py$|requirements\.txt$|pyproject\.toml$)'

function Invoke-NativeChecked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [Parameter(Mandatory = $true)]
        [string[]]$ArgumentList
    )

    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath failed with exit code $LASTEXITCODE"
    }
}

function Invoke-AzdJson {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$ArgumentList
    )

    $lines = @(& azd @ArgumentList 2>&1 | ForEach-Object { $_.ToString() })
    if ($LASTEXITCODE -ne 0) {
        throw "azd $($ArgumentList -join ' ') failed with exit code $LASTEXITCODE"
    }

    $text = $lines -join "`n"
    $jsonStart = $text.IndexOf("{")
    $jsonEnd = $text.LastIndexOf("}")
    if ($jsonStart -lt 0 -or $jsonEnd -lt $jsonStart) {
        throw "azd did not return a JSON object"
    }

    return $text.Substring($jsonStart, $jsonEnd - $jsonStart + 1) | ConvertFrom-Json -Depth 100
}

function Get-AgentState {
    param([string]$StagingPath)

    $state = Invoke-AzdJson -ArgumentList @(
        "ai", "agent", "show", $Service,
        "--cwd", $StagingPath,
        "--environment", $Environment,
        "--no-prompt",
        "--output", "json"
    )

    if ($state.status -ceq "not_deployed") {
        $name = $state.PSObject.Properties["name"]
        return [pscustomobject]@{
            Name = if ($name) { [string]$name.Value } else { "" }
            Version = ""
            Status = "not_deployed"
            ContentHash = ""
            PlaygroundUrl = ""
        }
    }

    return [pscustomobject]@{
        Name = [string]$state.name
        Version = [string]$state.version
        Status = [string]$state.status
        ContentHash = [string]$state.definition.code_configuration.content_hash
        PlaygroundUrl = [string]$state.playground_url
    }
}

function Import-AzdEnvironment {
    $values = Invoke-AzdJson -ArgumentList @(
        "env", "get-values",
        "--environment", $Environment,
        "--output", "json"
    )
    $optionalCredentialNames = @(
        "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "AZURE_CLIENT_CERTIFICATE_PATH",
        "AZURE_CLIENT_CERTIFICATE_PASSWORD", "AZURE_FEDERATED_TOKEN_FILE"
    )
    foreach ($property in $values.PSObject.Properties) {
        $value = [string]$property.Value
        if ($property.Name -in $optionalCredentialNames -and $value.Length -eq 0) {
            if (Test-Path -LiteralPath "Env:$($property.Name)") {
                Remove-Item -LiteralPath "Env:$($property.Name)"
            }
            continue
        }
        [Environment]::SetEnvironmentVariable(
            $property.Name,
            $value,
            "Process"
        )
    }
}

function New-DeploymentStagingDirectory {
    param(
        [string]$PackagePath = ""
    )

    $hostedName = $env:FOUNDRY_HOSTED_AGENT_NAME
    if ($hostedName -cnotmatch '^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$') {
        throw "FOUNDRY_HOSTED_AGENT_NAME must identify the exact 1-63 character deployment target."
    }
    $sourceEnvironmentPath = Join-Path $repoRoot ".azure/$Environment"
    if (-not (Test-Path -LiteralPath $sourceEnvironmentPath -PathType Container)) {
        throw "Configure the explicit named azd environment before creating a deployment workspace."
    }
    $failureTelemetryNames = @(
        "AZURE_MONITOR_INGESTION_ENDPOINT", "AZURE_MONITOR_DCR_RULE_ID", "AZURE_MONITOR_DCR_STREAM_NAME"
    )
    $configuredTelemetry = @($failureTelemetryNames | Where-Object {
        -not [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($_, "Process"))
    })
    if ($configuredTelemetry.Count -notin @(0, $failureTelemetryNames.Count)) {
        throw "Failure-log ingestion requires all three explicit endpoint/rule/stream bindings."
    }
    $stagingPath = Join-Path (
        [System.IO.Path]::GetTempPath()
    ) "azbrief-hosted-deploy-$([guid]::NewGuid().ToString('N'))"
    $requestedWhatIf = $WhatIfPreference
    $completed = $false
    try {
        $WhatIfPreference = $false
        if ($PackagePath) {
            Expand-Archive -LiteralPath $PackagePath -DestinationPath $stagingPath -WhatIf:$false
        }
        else {
            New-Item -ItemType Directory -Path $stagingPath | Out-Null
            $inputs = @(& git -C $repoRoot ls-files -- src hosted_agent_main.py requirements.txt pyproject.toml README.md LICENSE)
            if ($LASTEXITCODE -ne 0 -or $inputs.Count -eq 0) {
                throw "The reviewed tracked runtime input list could not be read."
            }
            foreach ($relative in $inputs) {
                $destination = Join-Path $stagingPath $relative
                New-Item -ItemType Directory -Path (Split-Path $destination) -Force | Out-Null
                Copy-Item -LiteralPath (Join-Path $repoRoot $relative) -Destination $destination
            }
        }
        $manifestPath = Join-Path $stagingPath "azure.yaml"
        $manifest = [System.IO.File]::ReadAllText(
            (Join-Path $repoRoot "azure.yaml"), [System.Text.Encoding]::UTF8
        )
        $namePattern = '(?m)^([ \t]*)name:[ \t]*\$\{FOUNDRY_HOSTED_AGENT_NAME\}[ \t]*(\r?)$'
        if ([regex]::Matches($manifest, $namePattern).Count -ne 1) {
            throw "The Hosted manifest must contain exactly one explicit target-name binding."
        }
        $manifest = [regex]::Replace($manifest, $namePattern, {
            param($match)
            "$($match.Groups[1].Value)name: $hostedName$($match.Groups[2].Value)"
        })
        if ($configuredTelemetry.Count -eq 0) {
            foreach ($name in $failureTelemetryNames) {
                $escaped = [regex]::Escape($name)
                $pattern = '(?m)^[ \t]*-[ \t]*name:[ \t]*' + $escaped +
                    '[ \t]*\r?\n[ \t]*value:[ \t]*\$\{' + $escaped + '\}[ \t]*(?:\r?\n|$)'
                if ([regex]::Matches($manifest, $pattern).Count -gt 1) {
                    throw "The optional failure-log manifest binding is ambiguous: $name"
                }
                $manifest = [regex]::Replace($manifest, $pattern, "")
            }
            Write-Host "Optional failure-log ingestion is unconfigured; retain stdout/project telemetry without empty DCR bindings."
        }
        [System.IO.File]::WriteAllText(
            $manifestPath, $manifest, [System.Text.UTF8Encoding]::new($false)
        )
        Copy-Item -LiteralPath (Join-Path $repoRoot ".agentignore") -Destination $stagingPath
        $azurePath = Join-Path $stagingPath ".azure"
        New-Item -ItemType Directory -Path $azurePath | Out-Null
        Copy-Item -LiteralPath $sourceEnvironmentPath -Destination $azurePath -Recurse
        [System.IO.File]::WriteAllText(
            (Join-Path $azurePath "config.json"),
            (@{ defaultEnvironment = $Environment } | ConvertTo-Json),
            [System.Text.UTF8Encoding]::new($false)
        )
        $completed = $true
        return $stagingPath
    }
    finally {
        if (-not $completed -and (Test-Path -LiteralPath $stagingPath)) {
            Remove-Item -LiteralPath $stagingPath -Recurse -Force
        }
        $WhatIfPreference = $requestedWhatIf
    }
}

function Sync-AzdEnvironmentFromStaging {
    param(
        [Parameter(Mandatory = $true)]
        [string]$StagingPath
    )

    $sourceEnvironmentPath = Join-Path $StagingPath ".azure/$Environment"
    $targetEnvironmentPath = Join-Path $repoRoot ".azure/$Environment"
    foreach ($name in @(".env", "config.json")) {
        $source = Join-Path $sourceEnvironmentPath $name
        if (Test-Path -LiteralPath $source -PathType Leaf) {
            Copy-Item -LiteralPath $source -Destination $targetEnvironmentPath -Force
        }
    }
}

function Get-DirtyRuntimePaths {
    $entries = @(& git status --porcelain=v1 --untracked-files=all)
    if ($LASTEXITCODE -ne 0) {
        throw "git status failed with exit code $LASTEXITCODE"
    }

    $paths = foreach ($entry in $entries) {
        $line = [string]$entry
        if ($line.Length -lt 4) {
            continue
        }
        $path = $line.Substring(3).Trim('"')
        if ($path.Contains(" -> ")) {
            $path = ($path -split ' -> ', 2)[-1].Trim('"')
        }
        $path = $path.Replace("\", "/")
        if ($path -match $runtimePathPattern) {
            $path
        }
    }
    return @($paths | Sort-Object -Unique)
}

function Invoke-Doctor {
    param([string]$StagingPath, [switch]$LocalOnly)

    $arguments = @(
        "ai", "agent", "doctor",
        "--cwd", $StagingPath,
        "--environment", $Environment,
        "--no-prompt"
    )
    if ($LocalOnly) { $arguments += "--local-only" }
    Invoke-NativeChecked -FilePath "azd" -ArgumentList $arguments
}

if (-not $activateScript) {
    throw "Virtual environment activation script not found (.venv/Scripts on Windows or .venv/bin in Cloud Shell/Linux)."
}

if ($PackageOnly -and $FromPackage) {
    throw "-PackageOnly cannot be combined with -FromPackage"
}

$deploymentStagingPath = ""
Push-Location $repoRoot
try {
    $requestedWhatIf = $WhatIfPreference
    try {
        $WhatIfPreference = $false
        . $activateScript
    }
    finally {
        $WhatIfPreference = $requestedWhatIf
    }
    $env:AZURE_DEV_USER_AGENT = "microsoft_foundry_skill"

    foreach ($command in @("azd", "git", "python")) {
        if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
            throw "Required command not found: $command"
        }
    }

    Import-AzdEnvironment

    if (-not $FromPackage) {
        $dirtyRuntimePaths = Get-DirtyRuntimePaths
        if ($dirtyRuntimePaths.Count -gt 0 -and -not $AllowDirtyRuntime) {
            $renderedPaths = $dirtyRuntimePaths -join "`n  - "
            throw (
                "Refusing to package dirty Hosted runtime inputs. " +
                "Use a clean worktree, deploy a reviewed ZIP with -FromPackage, or pass " +
                "-AllowDirtyRuntime after reviewing:`n  - $renderedPaths"
            )
        }
    }

    Invoke-NativeChecked -FilePath "python" -ArgumentList @("-c", "import src")

    if (-not $SkipTests) {
        $previousOtel = $env:OTEL_SDK_DISABLED
        try {
            $env:OTEL_SDK_DISABLED = "true"
            Invoke-NativeChecked -FilePath "python" -ArgumentList @(
                "-m", "pytest", "tests/", "-o", "addopts=", "-x"
            )
        }
        finally { $env:OTEL_SDK_DISABLED = $previousOtel }
    }

    if (-not $SkipRosterCheck) {
        Invoke-NativeChecked -FilePath "python" -ArgumentList @(
            "-m", "scripts.provision_foundry_agents", "--check"
        )
    }

    if ($FromPackage) {
        $packagePath = (Resolve-Path -LiteralPath $FromPackage).Path
        $deploymentStagingPath = New-DeploymentStagingDirectory -PackagePath $packagePath
    }
    else {
        if (-not $OutputPath) {
            $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
            $OutputPath = Join-Path $repoRoot "out\deploy\$Service-$timestamp.zip"
        }
        $packagePath = [System.IO.Path]::GetFullPath($OutputPath, $repoRoot)
        $packageDirectory = Split-Path -Parent $packagePath
        New-Item -ItemType Directory -Path $packageDirectory -Force | Out-Null
        $deploymentStagingPath = New-DeploymentStagingDirectory
    }

    if (-not $SkipDoctor) {
        $preflightState = Get-AgentState -StagingPath $deploymentStagingPath
        $initialDeployment = $preflightState.Status -ceq "not_deployed"
        if ($initialDeployment) {
            Write-Host "First Hosted deployment: local doctor before deploy; full doctor remains mandatory afterward."
        }
        Invoke-Doctor -StagingPath $deploymentStagingPath -LocalOnly:$initialDeployment
    }
    if (-not $FromPackage) {
        Invoke-NativeChecked -FilePath "azd" -ArgumentList @(
            "package", $Service,
            "--cwd", $deploymentStagingPath,
            "--environment", $Environment,
            "--output-path", $packagePath,
            "--no-prompt"
        )
    }

    if (-not (Test-Path -LiteralPath $packagePath -PathType Leaf)) {
        throw "Deployment package was not created: $packagePath"
    }

    $packageHash = (Get-FileHash -LiteralPath $packagePath -Algorithm SHA256).Hash.ToLowerInvariant()
    Write-Host "Package: $packagePath"
    Write-Host "Package SHA256: $packageHash"

    if ($PackageOnly) {
        return
    }

    $before = Get-AgentState -StagingPath $deploymentStagingPath
    if ($env:FOUNDRY_HOSTED_AGENT_NAME -and $before.Status -cne "not_deployed" -and
        $before.Name -cne $env:FOUNDRY_HOSTED_AGENT_NAME) {
        throw "The existing Hosted Agent does not match the configured target; no deployment was started."
    }
    Write-Host "Before deployment: version=$($before.Version), status=$($before.Status)"

    if (-not $PSCmdlet.ShouldProcess(
        "$Service in azd environment '$Environment'",
        "Deploy package $packagePath"
    )) {
        return
    }

    Invoke-NativeChecked -FilePath "azd" -ArgumentList @(
        "deploy", $Service,
        "--cwd", $deploymentStagingPath,
        "--environment", $Environment,
        "--no-prompt"
    )

    Sync-AzdEnvironmentFromStaging -StagingPath $deploymentStagingPath
    Import-AzdEnvironment
    $after = Get-AgentState -StagingPath $deploymentStagingPath
    if ($after.Status -ne "active" -or
        ($env:FOUNDRY_HOSTED_AGENT_NAME -and $after.Name -cne $env:FOUNDRY_HOSTED_AGENT_NAME)) {
        throw "Hosted Agent deployment did not activate the configured target (status=$($after.Status))"
    }

    if (-not $SkipDoctor) {
        Invoke-Doctor -StagingPath $deploymentStagingPath
    }

    if (-not $SkipSmokeTest) {
        $previousVerbose = $env:AZBRIEF_VERBOSE
        try {
            $env:AZBRIEF_VERBOSE = "false"
            $env:FOUNDRY_HOSTED_AGENT_NAME = $after.Name
            $smokeArguments = @("-m", "scripts.smoke_hosted_agent")
            if ($SmokeUpdateUrl) {
                $smokeArguments += @("--url", $SmokeUpdateUrl)
            }
            Invoke-NativeChecked -FilePath "python" -ArgumentList $smokeArguments
        }
        finally {
            $env:AZBRIEF_VERBOSE = $previousVerbose
        }
    }

    [pscustomobject]@{
        Service = $after.Name
        Environment = $Environment
        PreviousVersion = $before.Version
        ActiveVersion = $after.Version
        Status = $after.Status
        ContentHash = $after.ContentHash
        PackageSha256 = $packageHash
        PlaygroundUrl = $after.PlaygroundUrl
    } | Format-List
}
finally {
    if ($deploymentStagingPath) {
        Remove-Item -LiteralPath $deploymentStagingPath -Recurse -Force -ErrorAction SilentlyContinue
    }
    Pop-Location
}
