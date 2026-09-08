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
$activateScript = Join-Path $repoRoot ".venv\Scripts\Activate.ps1"
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
    $state = Invoke-AzdJson -ArgumentList @(
        "ai", "agent", "show", $Service,
        "--environment", $Environment,
        "--no-prompt",
        "--output", "json"
    )

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
    foreach ($property in $values.PSObject.Properties) {
        [Environment]::SetEnvironmentVariable(
            $property.Name,
            [string]$property.Value,
            "Process"
        )
    }
}

function New-DeploymentStagingDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PackagePath
    )

    $stagingPath = Join-Path (
        [System.IO.Path]::GetTempPath()
    ) "azbrief-hosted-deploy-$([guid]::NewGuid().ToString('N'))"
    Expand-Archive -LiteralPath $PackagePath -DestinationPath $stagingPath
    Copy-Item -LiteralPath (Join-Path $repoRoot "azure.yaml") -Destination $stagingPath
    Copy-Item -LiteralPath (Join-Path $repoRoot ".agentignore") -Destination $stagingPath
    if (Test-Path -LiteralPath (Join-Path $repoRoot ".azure") -PathType Container) {
        Copy-Item -LiteralPath (Join-Path $repoRoot ".azure") -Destination $stagingPath -Recurse
    }
    return $stagingPath
}

function Sync-AzdEnvironmentFromStaging {
    param(
        [Parameter(Mandatory = $true)]
        [string]$StagingPath
    )

    $sourceEnvironmentPath = Join-Path $StagingPath ".azure\$Environment"
    $targetEnvironmentPath = Join-Path $repoRoot ".azure\$Environment"
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
    Invoke-NativeChecked -FilePath "azd" -ArgumentList @(
        "ai", "agent", "doctor",
        "--environment", $Environment,
        "--no-prompt"
    )
}

if (-not (Test-Path -LiteralPath $activateScript -PathType Leaf)) {
    throw "Virtual environment activation script not found: $activateScript"
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
        Invoke-NativeChecked -FilePath "python" -ArgumentList @(
            "-m", "pytest", "tests/", "-o", "addopts=", "-x"
        )
    }

    if (-not $SkipRosterCheck) {
        Invoke-NativeChecked -FilePath "python" -ArgumentList @(
            "-m", "scripts.provision_foundry_agents", "--check"
        )
    }

    if (-not $SkipDoctor) {
        Invoke-Doctor
    }

    if ($FromPackage) {
        $packagePath = (Resolve-Path -LiteralPath $FromPackage).Path
    }
    else {
        if (-not $OutputPath) {
            $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
            $OutputPath = Join-Path $repoRoot "out\deploy\$Service-$timestamp.zip"
        }
        $packagePath = [System.IO.Path]::GetFullPath($OutputPath, $repoRoot)
        $packageDirectory = Split-Path -Parent $packagePath
        New-Item -ItemType Directory -Path $packageDirectory -Force | Out-Null

        Invoke-NativeChecked -FilePath "azd" -ArgumentList @(
            "package", $Service,
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

    $before = Get-AgentState
    Write-Host "Before deployment: version=$($before.Version), status=$($before.Status)"

    if (-not $PSCmdlet.ShouldProcess(
        "$Service in azd environment '$Environment'",
        "Deploy package $packagePath"
    )) {
        return
    }

    $deploymentStagingPath = New-DeploymentStagingDirectory -PackagePath $packagePath
    Invoke-NativeChecked -FilePath "azd" -ArgumentList @(
        "deploy", $Service,
        "--cwd", $deploymentStagingPath,
        "--environment", $Environment,
        "--no-prompt"
    )

    Sync-AzdEnvironmentFromStaging -StagingPath $deploymentStagingPath
    Import-AzdEnvironment
    $after = Get-AgentState
    if ($after.Status -ne "active") {
        throw "Hosted Agent deployment did not become active (status=$($after.Status))"
    }

    if (-not $SkipDoctor) {
        Invoke-Doctor
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
