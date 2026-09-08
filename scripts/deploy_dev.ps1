#requires -Version 7.0

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$SubscriptionId = $env:AZURE_SUBSCRIPTION_ID,

    [string]$ResourceGroup = $env:AZURE_RESOURCE_GROUP,

    [string]$ContainerAppName = $env:CONTAINER_APP_NAME,

    [string]$SchedulerJobName = $env:SCHEDULER_JOB_NAME,

    [string]$AcrName = $env:ACR_NAME,

    [ValidatePattern('^[a-z0-9]+(?:[._/-][a-z0-9]+)*$')]
    [string]$ImageName = "azbrief-enterprise",

    [string]$ImageTag = "",

    [string]$ContainerName = "",

    [ValidateRange(60, 3600)]
    [int]$TimeoutSeconds = 600,

    [ValidateRange(1, 60)]
    [int]$PollIntervalSeconds = 5,

    [switch]$AllowDirty,

    [switch]$AllowLegacyRuntimeSettings,

    [switch]$SkipTests,

    [switch]$SkipHealthCheck,

    [switch]$SkipJobSmoke,

    [switch]$SkipRollback
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$activateScript = Join-Path $repoRoot ".venv\Scripts\Activate.ps1"
$dockerInputPaths = @(".dockerignore", "Dockerfile", "requirements.txt", "src")
$legacyRuntimeEnvironmentVariables = @(
    "AZURE_OPENAI_DEPLOYMENT_NAME",
    "AZURE_OPENAI_ENDPOINT",
    "FOUNDRY_AGENTS",
    "FOUNDRY_MODEL_DEPLOYMENT",
    "LLM_BACKEND"
)

function Assert-RequiredValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [AllowEmptyString()]
        [string]$Value,

        [Parameter(Mandatory = $true)]
        [string]$EnvironmentVariable
    )

    if ([string]::IsNullOrWhiteSpace($Value)) {
        throw "$Name is required. Pass -$Name or set $EnvironmentVariable."
    }
}

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

function Invoke-NativeCaptured {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [Parameter(Mandatory = $true)]
        [string[]]$ArgumentList
    )

    $requestedWhatIf = $WhatIfPreference
    $stderrPath = ""
    try {
        $WhatIfPreference = $false
        $stderrPath = [System.IO.Path]::GetTempFileName()
        $stdoutLines = @(
            & $FilePath @ArgumentList 2> $stderrPath |
                ForEach-Object { $_.ToString() }
        )
        $exitCode = $LASTEXITCODE
        $stderr = [System.IO.File]::ReadAllText($stderrPath)
    }
    finally {
        if ($stderrPath) {
            Remove-Item -LiteralPath $stderrPath -Force -ErrorAction SilentlyContinue
        }
        $WhatIfPreference = $requestedWhatIf
    }

    return [pscustomobject]@{
        ExitCode = $exitCode
        StandardOutput = ($stdoutLines -join "`n").Trim()
        StandardError = $stderr.Trim()
    }
}

function Add-AzSubscriptionArgument {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$ArgumentList
    )

    if ($script:SubscriptionId -and -not ($ArgumentList -contains "--subscription")) {
        return @($ArgumentList + @("--subscription", $script:SubscriptionId))
    }
    return @($ArgumentList)
}

function Invoke-AzJson {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$ArgumentList
    )

    $arguments = Add-AzSubscriptionArgument -ArgumentList $ArgumentList
    $arguments += @("--only-show-errors", "--output", "json")
    $result = Invoke-NativeCaptured -FilePath "az" -ArgumentList $arguments
    if ($result.ExitCode -ne 0) {
        $details = @($result.StandardError, $result.StandardOutput) |
            Where-Object { $_ } |
            Join-String -Separator "`n"
        throw "az $($ArgumentList -join ' ') failed:`n$details"
    }

    $text = $result.StandardOutput
    if (-not $text) {
        return $null
    }

    try {
        return $text | ConvertFrom-Json -Depth 100
    }
    catch {
        throw "az $($ArgumentList -join ' ') did not return valid JSON: $text"
    }
}

function Invoke-AzText {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$ArgumentList
    )

    $arguments = Add-AzSubscriptionArgument -ArgumentList $ArgumentList
    $arguments += @("--only-show-errors", "--output", "tsv")
    $result = Invoke-NativeCaptured -FilePath "az" -ArgumentList $arguments
    if ($result.ExitCode -ne 0) {
        $details = @($result.StandardError, $result.StandardOutput) |
            Where-Object { $_ } |
            Join-String -Separator "`n"
        throw "az $($ArgumentList -join ' ') failed:`n$details"
    }
    return $result.StandardOutput
}

function Test-AcrImageExists {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Registry,

        [Parameter(Mandatory = $true)]
        [string]$Image
    )

    $arguments = Add-AzSubscriptionArgument -ArgumentList @(
        "acr", "repository", "show",
        "--name", $Registry,
        "--image", $Image,
        "--only-show-errors",
        "--output", "none"
    )
    $result = Invoke-NativeCaptured -FilePath "az" -ArgumentList $arguments
    if ($result.ExitCode -eq 0) {
        return $true
    }

    $message = @($result.StandardError, $result.StandardOutput) |
        Where-Object { $_ } |
        Join-String -Separator "`n"
    if ($message -match '(?i)(manifest_unknown|not found|does not exist)') {
        return $false
    }
    throw "Unable to check whether ACR image $Image exists:`n$message"
}

function Get-ContainerFromResource {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Resource,

        [Parameter(Mandatory = $true)]
        [string]$ResourceKind
    )

    $containers = @($Resource.properties.template.containers)
    if ($ContainerName) {
        $matches = @($containers | Where-Object { [string]$_.name -eq $ContainerName })
        if ($matches.Count -ne 1) {
            throw "$ResourceKind does not contain exactly one container named '$ContainerName'."
        }
        return $matches[0]
    }

    if ($containers.Count -ne 1) {
        throw "$ResourceKind has $($containers.Count) containers. Pass -ContainerName explicitly."
    }
    return $containers[0]
}

function Get-ContainerEnvironmentNames {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Container
    )

    $environmentProperty = $Container.PSObject.Properties["env"]
    if ($null -eq $environmentProperty) {
        return @()
    }
    return @($environmentProperty.Value | ForEach-Object { [string]$_.name })
}

function Get-DockerInputFingerprint {
    $files = [System.Collections.Generic.List[System.IO.FileInfo]]::new()
    foreach ($relativePath in @(".dockerignore", "Dockerfile", "requirements.txt")) {
        $path = Join-Path $repoRoot $relativePath
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Docker input not found: $path"
        }
        [void]$files.Add((Get-Item -LiteralPath $path))
    }

    $sourceRoot = Join-Path $repoRoot "src"
    foreach ($file in Get-ChildItem -LiteralPath $sourceRoot -File -Recurse) {
        if ($file.FullName -match '[\\/]__pycache__[\\/]' -or $file.Name -match '\.py[co]$') {
            continue
        }
        [void]$files.Add($file)
    }

    $hash = [System.Security.Cryptography.IncrementalHash]::CreateHash(
        [System.Security.Cryptography.HashAlgorithmName]::SHA256
    )
    $totalBytes = 0L
    try {
        foreach ($file in $files | Sort-Object FullName) {
            $relativePath = [System.IO.Path]::GetRelativePath(
                $repoRoot,
                $file.FullName
            ).Replace("\", "/")
            $content = [System.IO.File]::ReadAllBytes($file.FullName)
            foreach ($value in @($relativePath, [string]$content.Length)) {
                $hash.AppendData([System.Text.Encoding]::UTF8.GetBytes($value))
                $hash.AppendData([byte[]]@(0))
            }
            $hash.AppendData($content)
            $hash.AppendData([byte[]]@(0))
            $totalBytes += $content.Length
        }
        $digest = [System.Convert]::ToHexString($hash.GetHashAndReset()).ToLowerInvariant()
    }
    finally {
        $hash.Dispose()
    }

    return [pscustomobject]@{
        Sha256 = $digest
        FileCount = $files.Count
        TotalBytes = $totalBytes
    }
}

function Get-ContainerApp {
    return Invoke-AzJson -ArgumentList @(
        "containerapp", "show",
        "--name", $ContainerAppName,
        "--resource-group", $ResourceGroup
    )
}

function Get-SchedulerJob {
    return Invoke-AzJson -ArgumentList @(
        "containerapp", "job", "show",
        "--name", $SchedulerJobName,
        "--resource-group", $ResourceGroup
    )
}

function Set-ContainerAppImage {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Image
    )

    $arguments = @(
        "containerapp", "update",
        "--name", $ContainerAppName,
        "--resource-group", $ResourceGroup,
        "--image", $Image
    )
    if ($ContainerName) {
        $arguments += @("--container-name", $ContainerName)
    }
    return Invoke-AzJson -ArgumentList $arguments
}

function Set-SchedulerJobImage {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Image
    )

    $arguments = @(
        "containerapp", "job", "update",
        "--name", $SchedulerJobName,
        "--resource-group", $ResourceGroup,
        "--image", $Image
    )
    if ($ContainerName) {
        $arguments += @("--container-name", $ContainerName)
    }
    return Invoke-AzJson -ArgumentList $arguments
}

function Wait-ContainerAppRevision {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RevisionName,

        [Parameter(Mandatory = $true)]
        [string]$ExpectedImage
    )

    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $lastState = "not observed"
    try {
        while ($stopwatch.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
            try {
                $revision = Invoke-AzJson -ArgumentList @(
                    "containerapp", "revision", "show",
                    "--name", $ContainerAppName,
                    "--resource-group", $ResourceGroup,
                    "--revision", $RevisionName
                )
            }
            catch {
                $lastState = $_.Exception.Message
                Start-Sleep -Seconds $PollIntervalSeconds
                continue
            }

            $runningState = [string]$revision.properties.runningState
            $healthState = [string]$revision.properties.healthState
            $lastState = "running=$runningState, health=$healthState"
            Write-Progress -Activity "Waiting for Container App revision" -Status $lastState

            if ($runningState -eq "Running" -and $healthState -eq "Healthy") {
                $revisionContainer = Get-ContainerFromResource `
                    -Resource $revision `
                    -ResourceKind "Container App revision"
                if ([string]$revisionContainer.image -ne $ExpectedImage) {
                    throw (
                        "Revision $RevisionName is healthy but uses '$($revisionContainer.image)' " +
                        "instead of '$ExpectedImage'."
                    )
                }

                $app = Get-ContainerApp
                if ([string]$app.properties.latestReadyRevisionName -eq $RevisionName) {
                    return $app
                }
                $lastState += ", latestReady=$($app.properties.latestReadyRevisionName)"
            }

            if ($runningState -eq "Failed" -or $healthState -eq "Unhealthy") {
                throw "Revision $RevisionName failed ($lastState)."
            }
            Start-Sleep -Seconds $PollIntervalSeconds
        }
    }
    finally {
        Write-Progress -Activity "Waiting for Container App revision" -Completed
        $stopwatch.Stop()
    }

    throw "Revision $RevisionName did not become ready in $TimeoutSeconds seconds ($lastState)."
}

function Wait-HealthEndpoint {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Fqdn
    )

    $uri = "https://$Fqdn/health"
    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $lastError = "not attempted"
    try {
        while ($stopwatch.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
            try {
                $response = Invoke-WebRequest -Uri $uri -Method Get -TimeoutSec 30
                if ($response.StatusCode -eq 200) {
                    return $uri
                }
                $lastError = "HTTP $($response.StatusCode)"
            }
            catch {
                $lastError = $_.Exception.Message
            }
            Start-Sleep -Seconds $PollIntervalSeconds
        }
    }
    finally {
        $stopwatch.Stop()
    }
    throw "Health check $uri did not return HTTP 200 ($lastError)."
}

function Wait-JobSmokeExecution {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Image
    )

    $existingExecutions = @(Invoke-AzJson -ArgumentList @(
        "containerapp", "job", "execution", "list",
        "--name", $SchedulerJobName,
        "--resource-group", $ResourceGroup
    ))
    $existingNames = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::OrdinalIgnoreCase
    )
    foreach ($execution in $existingExecutions) {
        [void]$existingNames.Add([string]$execution.name)
    }

    $arguments = @(
        "containerapp", "job", "start",
        "--name", $SchedulerJobName,
        "--resource-group", $ResourceGroup,
        "--image", $Image
    )
    if ($ContainerName) {
        $arguments += @("--container-name", $ContainerName)
    }
    $arguments += @(
        "--command", "python",
        "--args", "/app/src/__init__.py"
    )
    $started = Invoke-AzJson -ArgumentList $arguments
    $executionName = [string]$started.name

    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $lastState = "waiting for execution record"
    try {
        while ($stopwatch.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
            $executions = @(Invoke-AzJson -ArgumentList @(
                "containerapp", "job", "execution", "list",
                "--name", $SchedulerJobName,
                "--resource-group", $ResourceGroup
            ))

            if (-not $executionName) {
                $newExecution = $executions |
                    Where-Object { -not $existingNames.Contains([string]$_.name) } |
                    Sort-Object { [datetime]$_.properties.startTime } -Descending |
                    Select-Object -First 1
                if ($newExecution) {
                    $executionName = [string]$newExecution.name
                }
            }

            $current = $executions |
                Where-Object { [string]$_.name -eq $executionName } |
                Select-Object -First 1
            if ($current) {
                $lastState = [string]$current.properties.status
                Write-Progress -Activity "Waiting for Container Apps Job smoke" `
                    -Status "$executionName`: $lastState"
                if ($lastState -eq "Succeeded") {
                    return $executionName
                }
                if ($lastState -eq "Failed") {
                    throw "Job smoke execution $executionName failed."
                }
            }
            Start-Sleep -Seconds $PollIntervalSeconds
        }
    }
    finally {
        Write-Progress -Activity "Waiting for Container Apps Job smoke" -Completed
        $stopwatch.Stop()
    }

    throw "Job smoke execution did not succeed in $TimeoutSeconds seconds ($lastState)."
}

function Restore-PreviousImages {
    param(
        [Parameter(Mandatory = $true)]
        [string]$AppImage,

        [Parameter(Mandatory = $true)]
        [string]$JobImage,

        [Parameter(Mandatory = $true)]
        [bool]$AppChangeAttempted,

        [Parameter(Mandatory = $true)]
        [bool]$JobChangeAttempted
    )

    $errors = [System.Collections.Generic.List[string]]::new()
    if ($JobChangeAttempted) {
        try {
            $null = Set-SchedulerJobImage -Image $JobImage
            $restoredJob = Get-SchedulerJob
            $restoredContainer = Get-ContainerFromResource `
                -Resource $restoredJob `
                -ResourceKind "restored scheduler Job"
            if ([string]$restoredContainer.image -ne $JobImage) {
                throw "Scheduler Job still uses '$($restoredContainer.image)'."
            }
        }
        catch {
            [void]$errors.Add("Scheduler Job rollback failed: $($_.Exception.Message)")
        }
    }

    if ($AppChangeAttempted) {
        try {
            $restoredApp = Set-ContainerAppImage -Image $AppImage
            $restoredRevision = [string]$restoredApp.properties.latestRevisionName
            if (-not $restoredRevision) {
                throw "Container App rollback did not return a revision name."
            }
            $null = Wait-ContainerAppRevision `
                -RevisionName $restoredRevision `
                -ExpectedImage $AppImage
        }
        catch {
            [void]$errors.Add("Container App rollback failed: $($_.Exception.Message)")
        }
    }
    return @($errors)
}

if (-not (Test-Path -LiteralPath $activateScript -PathType Leaf)) {
    throw "Virtual environment activation script not found: $activateScript"
}

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

    foreach ($command in @("az", "git", "python")) {
        if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
            throw "Required command not found: $command"
        }
    }

    Assert-RequiredValue `
        -Name "ResourceGroup" `
        -Value $ResourceGroup `
        -EnvironmentVariable "AZURE_RESOURCE_GROUP"
    Assert-RequiredValue `
        -Name "ContainerAppName" `
        -Value $ContainerAppName `
        -EnvironmentVariable "CONTAINER_APP_NAME"

    if (-not $SchedulerJobName) {
        $appSuffix = if ($ContainerAppName.StartsWith("ca-")) {
            $ContainerAppName.Substring(3)
        }
        else {
            $ContainerAppName
        }
        $SchedulerJobName = "caj-$appSuffix"
    }
    if ($ImageTag -and $ImageTag -notmatch '^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$') {
        throw "ImageTag is not a valid container image tag: $ImageTag"
    }
    if ($ImageTag -eq "latest") {
        throw "The mutable 'latest' tag is not allowed for a development deployment."
    }

    $account = Invoke-AzJson -ArgumentList @("account", "show")
    if (-not $SubscriptionId) {
        $SubscriptionId = [string]$account.id
    }
    if (-not $SubscriptionId) {
        throw "Azure CLI did not resolve a subscription."
    }

    $appBefore = Get-ContainerApp
    if ([string]$appBefore.properties.configuration.activeRevisionsMode -ne "Single") {
        throw "Development deployment requires Container Apps single-revision mode."
    }
    $jobBefore = Get-SchedulerJob
    $appContainerBefore = Get-ContainerFromResource `
        -Resource $appBefore `
        -ResourceKind "Container App"
    $jobContainerBefore = Get-ContainerFromResource `
        -Resource $jobBefore `
        -ResourceKind "scheduler Job"
    $previousAppImage = [string]$appContainerBefore.image
    $previousJobImage = [string]$jobContainerBefore.image
    if (-not $previousAppImage -or -not $previousJobImage) {
        throw "Unable to capture both rollback images."
    }

    $legacyRuntimeSettings = @(
        @(Get-ContainerEnvironmentNames -Container $appContainerBefore) +
            @(Get-ContainerEnvironmentNames -Container $jobContainerBefore) |
            Where-Object { $_ -in $legacyRuntimeEnvironmentVariables } |
            Sort-Object -Unique
    )
    if ($legacyRuntimeSettings.Count -gt 0) {
        $legacyList = $legacyRuntimeSettings -join ", "
        $message = (
            "Legacy runtime settings are absent from current IaC and ignored by the " +
            "control plane: $legacyList. Remove them from both resources before deploying."
        )
        if ($WhatIfPreference -or $AllowLegacyRuntimeSettings) {
            Write-Warning $message
        }
        else {
            throw "$message Pass -AllowLegacyRuntimeSettings only after reviewing the drift."
        }
    }

    if (-not $AcrName) {
        $registries = @($appBefore.properties.configuration.registries)
        if ($registries.Count -ne 1 -or -not $registries[0].server) {
            throw "Pass -AcrName because the Container App does not identify one registry."
        }
        $AcrName = ([string]$registries[0].server).Split(".")[0]
    }
    $acrLoginServer = Invoke-AzText -ArgumentList @(
        "acr", "show",
        "--name", $AcrName,
        "--query", "loginServer"
    )
    if (-not $acrLoginServer) {
        throw "ACR login server could not be resolved for $AcrName."
    }

    $fingerprint = Get-DockerInputFingerprint
    $commit = (& git rev-parse --short=8 HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $commit) {
        throw "Unable to resolve the current Git commit."
    }
    if (-not $ImageTag) {
        $timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddHHmmss")
        $ImageTag = "dev-$commit-$($fingerprint.Sha256.Substring(0, 10))-$timestamp"
    }
    $taggedImage = "$acrLoginServer/$ImageName`:$ImageTag"

    Write-Host "Development deployment plan"
    Write-Host "  Subscription: $SubscriptionId"
    Write-Host "  Resource group: $ResourceGroup"
    Write-Host "  Container App: $ContainerAppName"
    Write-Host "  Scheduler Job: $SchedulerJobName"
    Write-Host "  Image: $taggedImage"
    Write-Host "  Docker inputs: $($fingerprint.FileCount) files, $($fingerprint.TotalBytes) bytes"
    Write-Host "  Source SHA256: $($fingerprint.Sha256)"

    $target = "$ContainerAppName and $SchedulerJobName in $ResourceGroup"
    $action = "Build $taggedImage and roll both resources forward"
    if (-not $PSCmdlet.ShouldProcess($target, $action)) {
        return
    }

    $dirtyInputs = @(& git status --porcelain=v1 --untracked-files=all -- $dockerInputPaths)
    if ($LASTEXITCODE -ne 0) {
        throw "git status failed with exit code $LASTEXITCODE"
    }
    if ($dirtyInputs.Count -gt 0 -and -not $AllowDirty) {
        throw (
            "Docker inputs contain uncommitted changes. Review them and pass -AllowDirty to " +
            "deploy the current development worktree:`n$($dirtyInputs -join "`n")"
        )
    }
    if ($dirtyInputs.Count -gt 0) {
        Write-Warning "Deploying dirty Docker inputs; the source fingerprint is the deployment lineage."
    }

    Invoke-NativeChecked -FilePath "git" -ArgumentList @(
        "diff", "--check", "--", ".dockerignore", "Dockerfile", "requirements.txt", "src"
    )
    Invoke-NativeChecked -FilePath "python" -ArgumentList @("-c", "import src")
    if (-not $SkipTests) {
        Invoke-NativeChecked -FilePath "python" -ArgumentList @(
            "-m", "pytest", "tests/", "-o", "addopts=", "-x"
        )
    }

    if (Test-AcrImageExists -Registry $AcrName -Image "$ImageName`:$ImageTag") {
        throw "Refusing to overwrite existing immutable image tag $ImageName`:$ImageTag."
    }

    Invoke-NativeChecked -FilePath "az" -ArgumentList @(
        "acr", "build",
        "--registry", $AcrName,
        "--image", "$ImageName`:$ImageTag",
        "--file", "Dockerfile",
        "--source-acr-auth-id", "[caller]",
        "--subscription", $SubscriptionId,
        "--only-show-errors",
        "."
    )
    $imageDigest = Invoke-AzText -ArgumentList @(
        "acr", "repository", "show",
        "--name", $AcrName,
        "--image", "$ImageName`:$ImageTag",
        "--query", "digest"
    )
    if ($imageDigest -notmatch '^sha256:[0-9a-f]{64}$') {
        throw "ACR returned an invalid image digest: $imageDigest"
    }
    $deployedImage = "$acrLoginServer/$ImageName@$imageDigest"

    $appChangeAttempted = $false
    $jobChangeAttempted = $false
    try {
        $appChangeAttempted = $true
        $updatedApp = Set-ContainerAppImage -Image $deployedImage
        $newRevision = [string]$updatedApp.properties.latestRevisionName
        if (-not $newRevision) {
            throw "Container App update did not return a new revision name."
        }
        $readyApp = Wait-ContainerAppRevision `
            -RevisionName $newRevision `
            -ExpectedImage $deployedImage

        $healthUrl = "skipped"
        if (-not $SkipHealthCheck) {
            $fqdn = [string]$readyApp.properties.configuration.ingress.fqdn
            if (-not $fqdn) {
                throw "Container App has no ingress FQDN for the required health check."
            }
            $healthUrl = Wait-HealthEndpoint -Fqdn $fqdn
        }

        $jobChangeAttempted = $true
        $null = Set-SchedulerJobImage -Image $deployedImage
        $updatedJob = Get-SchedulerJob
        $updatedJobContainer = Get-ContainerFromResource `
            -Resource $updatedJob `
            -ResourceKind "updated scheduler Job"
        if ([string]$updatedJobContainer.image -ne $deployedImage) {
            throw (
                "Scheduler Job uses '$($updatedJobContainer.image)' instead of '$deployedImage'."
            )
        }

        $jobExecution = "skipped"
        if (-not $SkipJobSmoke) {
            $jobExecution = Wait-JobSmokeExecution -Image $deployedImage
        }
    }
    catch {
        $deploymentError = $_.Exception.Message
        if (($appChangeAttempted -or $jobChangeAttempted) -and -not $SkipRollback) {
            Write-Warning "Deployment verification failed; restoring both previous images."
            $rollbackErrors = @(Restore-PreviousImages `
                -AppImage $previousAppImage `
                -JobImage $previousJobImage `
                -AppChangeAttempted $appChangeAttempted `
                -JobChangeAttempted $jobChangeAttempted)
            if ($rollbackErrors.Count -gt 0) {
                throw (
                    "Development deployment failed: $deploymentError`n" +
                    "Rollback also failed:`n$($rollbackErrors -join "`n")"
                )
            }
            throw "Development deployment failed and previous images were restored: $deploymentError"
        }
        throw "Development deployment failed: $deploymentError"
    }

    [pscustomobject]@{
        Subscription = $SubscriptionId
        ResourceGroup = $ResourceGroup
        ContainerApp = $ContainerAppName
        Revision = $newRevision
        SchedulerJob = $SchedulerJobName
        JobSmokeExecution = $jobExecution
        TaggedImage = $taggedImage
        DeployedImage = $deployedImage
        SourceSha256 = $fingerprint.Sha256
        HealthUrl = $healthUrl
    } | Format-List
}
finally {
    Pop-Location
}