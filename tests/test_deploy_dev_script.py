import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "deploy_dev.ps1"


@pytest.fixture(scope="module")
def script_text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_development_deploy_uses_an_immutable_acr_image(script_text: str) -> None:
    assert 'if ($ImageTag -eq "latest")' in script_text
    assert '"--source-acr-auth-id", "[caller]"' in script_text
    assert '"--query", "digest"' in script_text
    assert '$deployedImage = "$acrLoginServer/$ImageName@$imageDigest"' in script_text
    assert "Refusing to overwrite existing immutable image tag" in script_text


def test_development_deploy_updates_and_verifies_both_runtimes(script_text: str) -> None:
    assert '"containerapp", "update"' in script_text
    assert '"containerapp", "job", "update"' in script_text
    assert "Wait-ContainerAppRevision" in script_text
    assert '$runningState -eq "Running" -and $healthState -eq "Healthy"' in script_text
    assert "latestReadyRevisionName" in script_text
    assert "Wait-HealthEndpoint" in script_text


def test_development_deploy_rechecks_source_before_build_and_rollout(script_text: str) -> None:
    validation_guard = script_text.index("$validatedFingerprint.Sha256 -ne $fingerprint.Sha256")
    build = script_text.index('"acr", "build",')
    build_guard = script_text.index("$builtFingerprint.Sha256 -ne $fingerprint.Sha256")
    rollout = script_text.index("$appChangeAttempted = $true")
    assert validation_guard < build < build_guard < rollout
    assert "No image was built; rerun with stable source" in script_text
    assert "No runtime was updated; rerun with stable source" in script_text


def test_job_smoke_does_not_dispatch_the_scheduler(script_text: str) -> None:
    assert "Wait-JobSmokeExecution" in script_text
    assert '"--command", "python"' in script_text
    assert '"--args", "/app/src/__init__.py"' in script_text
    assert '"containerapp", "job", "execution", "list"' in script_text


def test_development_deploy_rolls_back_both_images(script_text: str) -> None:
    assert "Restore-PreviousImages" in script_text
    assert "Set-SchedulerJobImage -Image $JobImage" in script_text
    assert "Set-ContainerAppImage -Image $AppImage" in script_text
    assert "previous images were restored" in script_text
    assert "--set-env-vars" not in script_text
    assert "--secrets" not in script_text
    assert "--cron-expression" not in script_text


def test_development_deploy_fails_closed_on_legacy_runtime_settings(
    script_text: str,
) -> None:
    assert "AllowLegacyRuntimeSettings" in script_text
    for setting in (
        "AZURE_OPENAI_DEPLOYMENT_NAME",
        "AZURE_OPENAI_ENDPOINT",
        "FOUNDRY_AGENTS",
        "FOUNDRY_MODEL_DEPLOYMENT",
        "LLM_BACKEND",
    ):
        assert setting in script_text
    assert "Remove them from both resources before deploying" in script_text


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is not installed")
@pytest.mark.parametrize(
    "scenario",
    [
        "success",
        "what-if",
        "managed-identity",
        "mutable-image",
        "build-option",
        "missing-registry",
        "wrong-secret-ref",
        "inline-password",
        "unattached-identity",
        "username-mismatch",
        "pytest-failure",
        "unhealthy-app",
        "failed-job",
        "post-update-drift",
        "post-update-app-drift",
    ],
)
def test_prebuilt_deployment_preserves_gates_without_acr_access(
    tmp_path: Path, scenario: str
) -> None:
    repo = tmp_path / "customer"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    script = scripts / SCRIPT.name
    shutil.copyfile(SCRIPT, script)
    activation = repo / ".venv" / "Scripts" / "Activate.ps1"
    activation.parent.mkdir(parents=True)
    activation.write_text("$global:LASTEXITCODE = 0", encoding="ascii")
    for name in (".dockerignore", "Dockerfile", "requirements.txt", "src/__init__.py"):
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="ascii")
    harness = tmp_path / "prebuilt.ps1"
    calls_path = tmp_path / "calls.json"
    harness.write_text(
        r"""
$ErrorActionPreference = 'Stop'
$global:calls = [System.Collections.Generic.List[object]]::new()
$global:appImage = 'publisher.azurecr.io/azbrief-enterprise@sha256:' + ('b' * 64)
$global:jobImage = $global:appImage
$global:oldImage = $global:appImage
$global:revision = 'old'
$global:started = $false
function global:git {
    $global:LASTEXITCODE = 0
    if ($args[0] -eq 'rev-parse') { return 'deadbeef' }
}
function global:python {
    $global:LASTEXITCODE = 0
    $global:calls.Add(@('python') + $args)
    if ($args -contains 'pytest' -and $env:SCENARIO -eq 'pytest-failure') { $global:LASTEXITCODE = 9 }
}
function global:az {
    $global:LASTEXITCODE = 0
    $global:calls.Add(@('az') + $args)
    if ($args[0] -eq 'account') { return '{"id":"00000000-0000-0000-0000-000000000001"}' }
    if ($args[0] -ne 'containerapp') { throw 'Unexpected Azure command' }
    $isJob = $args[1] -eq 'job'
    if ($args -contains 'execution') {
        if (-not $global:started) { return '[]' }
        $status = if ($env:SCENARIO -eq 'failed-job') { 'Failed' } else { 'Succeeded' }
        return ConvertTo-Json -InputObject @(@{ name = 'smoke'; properties = @{ status = $status } })
    }
    if ($args -contains 'start') {
        $global:started = $true
        return '{"name":"smoke"}'
    }
    if ($args -contains 'update') {
        $image = $args[[array]::IndexOf($args, '--image') + 1]
        if ($isJob) { $global:jobImage = $image }
        else {
            $global:appImage = $image
            $global:revision = if ($image -eq $global:oldImage) { 'old' } else { 'new' }
        }
    }
    $identityId = '/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg-customer/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-customer'
    $registry = @{ server = 'publisher.azurecr.io'; username = 'customer-token'; passwordSecretRef = 'registry-password' }
    $secret = @{ name = 'registry-password'; keyVaultUrl = 'https://kv-customer.vault.azure.net/secrets/registry-password'; identity = $identityId }
    if ($env:SCENARIO -eq 'managed-identity') { $registry = @{ server = 'publisher.azurecr.io'; identity = $identityId } }
    if ($isJob) {
        switch ($env:SCENARIO) {
            'missing-registry' { $registry.server = 'other.azurecr.io' }
            'wrong-secret-ref' { $registry.passwordSecretRef = 'missing' }
            'inline-password' { $secret.value = 'must-not-leak' }
            'unattached-identity' { $secret.identity = '/other/identity' }
            'username-mismatch' { $registry.username = 'different-token' }
            'post-update-drift' {
                if ($global:jobImage -ne $global:oldImage) { $registry.username = 'different-token' }
            }
        }
    }
    if (-not $isJob -and $env:SCENARIO -eq 'post-update-app-drift' -and $global:jobImage -ne $global:oldImage) {
        $registry.username = 'different-token'
    }
    $image = if ($isJob) { $global:jobImage } else { $global:appImage }
    $health = if ($env:SCENARIO -eq 'unhealthy-app' -and $global:revision -eq 'new') { 'Unhealthy' } else { 'Healthy' }
    return @{
        identity = @{ userAssignedIdentities = @{ $identityId = @{ principalId = 'customer-principal' } } }
        properties = @{
            configuration = @{
                activeRevisionsMode = 'Single'; registries = @($registry); secrets = @($secret)
                ingress = @{ fqdn = 'ca-customer.example.azurecontainerapps.io' }
            }
            template = @{ containers = @(@{ name = 'azbrief'; image = $image; env = @() }) }
            latestRevisionName = $global:revision; latestReadyRevisionName = $global:revision
            runningState = 'Running'; healthState = $health
        }
    } | ConvertTo-Json -Depth 30
}
function global:Invoke-WebRequest {
    $global:calls.Add(@('health'))
    return @{ StatusCode = 200 }
}
try {
    $options = @{}
    if ($env:SCENARIO -eq 'what-if') { $options.WhatIf = $true }
    if ($env:SCENARIO -eq 'build-option') { $options.AcrName = 'publisher' }
    $image = 'publisher.azurecr.io/azbrief-enterprise@sha256:' + ('a' * 64)
    if ($env:SCENARIO -eq 'mutable-image') { $image = 'publisher.azurecr.io/azbrief-enterprise:latest' }
    & $env:SCRIPT -SubscriptionId '00000000-0000-0000-0000-000000000001' `
        -ResourceGroup rg-customer -ContainerAppName ca-customer -PrebuiltImage $image @options
}
finally {
    @{ calls = @($global:calls.ToArray()); appImage = $global:appImage; jobImage = $global:jobImage } |
        ConvertTo-Json -Depth 30 | Set-Content $env:CALLS -Encoding utf8
}
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-File", str(harness)],
        cwd=repo,
        env={
            **os.environ,
            "SCRIPT": str(script),
            "CALLS": str(calls_path),
            "SCENARIO": scenario,
            "ACR_NAME": "unrelated-inherited-registry",
        },
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    report = json.loads(calls_path.read_text(encoding="utf-8-sig"))
    calls = report["calls"]
    assert not any(
        call[:2] in (["az", "acr"], ["az", "login"], ["az", "keyvault"]) for call in calls
    )
    assert "must-not-leak" not in result.stdout + result.stderr
    updates = [call for call in calls if "update" in call]
    if scenario in ("success", "managed-identity"):
        assert result.returncode == 0, result.stderr
        assert len(updates) == 2
        assert report["appImage"] == report["jobImage"]
        assert report["appImage"].endswith("a" * 64)
        assert ["python", "-c", "import src"] in calls
        assert ["python", "-m", "pytest", "tests/", "-o", "addopts=", "-x"] in calls
        assert ["health"] in calls
        assert any("/app/src/__init__.py" in call for call in calls)
        assert "unverified (publisher build)" in result.stdout
    elif scenario == "what-if":
        assert result.returncode == 0, result.stderr
        assert "What if:" in result.stdout
        assert not updates
        assert not any(call[0] == "python" for call in calls)
    elif scenario in ("unhealthy-app", "failed-job", "post-update-drift", "post-update-app-drift"):
        assert result.returncode != 0
        assert "previous images were restored" in result.stderr
        assert len(updates) == (2 if scenario == "unhealthy-app" else 4)
        assert report["appImage"] == report["jobImage"]
        assert report["appImage"].endswith("b" * 64)
    else:
        assert result.returncode != 0
        assert not updates


@pytest.mark.skipif(
    os.name != "nt" or shutil.which("pwsh") is None,
    reason="The Windows Azure CLI command shim requires PowerShell 7 on Windows",
)
def test_azure_cli_progress_does_not_corrupt_json(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_az = fake_bin / "az.cmd"
    fake_az.write_text(
        """@echo off
echo Running .. 1>&2
if "%1 %2 %3"=="containerapp job show" (
    echo {"properties":{"template":{"containers":[{"name":"azbrief","image":"mock.azurecr.io/azbrief-enterprise:old"}]}}}
    exit /b 0
)
if "%1 %2"=="containerapp show" (
    echo {"properties":{"configuration":{"activeRevisionsMode":"Single","registries":[{"server":"mock.azurecr.io"}]},"template":{"containers":[{"name":"azbrief","image":"mock.azurecr.io/azbrief-enterprise:old"}]}}}
    exit /b 0
)
if "%1 %2"=="account show" (
    echo {"id":"00000000-0000-0000-0000-000000000001"}
    exit /b 0
)
if "%1 %2"=="acr show" (
    echo mock.azurecr.io
    exit /b 0
)
echo Unexpected fake az command: %* 1>&2
exit /b 9
""",
        encoding="ascii",
    )
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "TEMP": str(tmp_path),
        "TMP": str(tmp_path),
    }
    environment.pop("VIRTUAL_ENV", None)
    environment.pop("_OLD_VIRTUAL_PATH", None)

    result = subprocess.run(
        [
            "pwsh",
            "-NoLogo",
            "-NoProfile",
            "-File",
            str(SCRIPT),
            "-ResourceGroup",
            "rg-test",
            "-ContainerAppName",
            "ca-test",
            "-AcrName",
            "mock",
            "-WhatIf",
        ],
        capture_output=True,
        check=False,
        cwd=ROOT,
        env=environment,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "Development deployment plan" in result.stdout
    assert "did not return valid JSON" not in result.stderr
    assert "Output to File" not in result.stdout
    assert "Remove File" not in result.stdout
    assert not list(tmp_path.glob("tmp*.tmp"))


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is not installed")
@pytest.mark.parametrize("original_value", [None, "", "false"])
@pytest.mark.parametrize("fail_tests", [False, True])
def test_development_test_telemetry_is_scoped_and_restored(
    original_value: str | None, fail_tests: bool
) -> None:
    command = """
$ErrorActionPreference = "Stop"
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:AZBRIEF_SCRIPT_UNDER_TEST, [ref]$null, [ref]$null
)
$testGate = $ast.Find({
    param($node)
    $node -is [System.Management.Automation.Language.IfStatementAst] -and
        $node.Extent.Text.StartsWith('if (-not $SkipTests)')
}, $true)
if (-not $testGate) { throw "Test gate not found" }
$SkipTests = $false
$originalValue = $env:OTEL_SDK_DISABLED
$script:testsInvoked = $false
function Invoke-NativeChecked {
    param([string]$FilePath, [string[]]$ArgumentList)
    if ($env:OTEL_SDK_DISABLED -cne "true") { throw "Test telemetry was not disabled" }
    if ($FilePath -cne "python" -or
        ($ArgumentList -join " ") -cne "-m pytest tests/ -o addopts= -x") {
        throw "Unexpected test command"
    }
    $script:testsInvoked = $true
    if ($env:AZBRIEF_SIMULATE_TEST_FAILURE -eq "true") { throw "synthetic pytest failure" }
}
$failureCaught = $false
try {
    & ([scriptblock]::Create($testGate.Extent.Text))
}
catch {
    if ($_.Exception.Message -notmatch "synthetic pytest failure") { throw }
    $failureCaught = $true
}
if (-not $script:testsInvoked) { throw "Tests did not run" }
if ($failureCaught -ne ($env:AZBRIEF_SIMULATE_TEST_FAILURE -eq "true")) {
    throw "Test failure was not preserved"
}
if ($env:OTEL_SDK_DISABLED -cne $originalValue) { throw "Test telemetry setting leaked" }
Write-Output "TEST_TELEMETRY_ISOLATION_OK"
"""
    environment = {
        **os.environ,
        "AZBRIEF_SCRIPT_UNDER_TEST": str(SCRIPT),
        "AZBRIEF_SIMULATE_TEST_FAILURE": str(fail_tests).lower(),
    }
    if original_value is None:
        environment.pop("OTEL_SDK_DISABLED", None)
    else:
        environment["OTEL_SDK_DISABLED"] = original_value
    result = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-Command", command],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    assert "TEST_TELEMETRY_ISOLATION_OK" in result.stdout


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is not installed")
def test_development_deploy_has_valid_powershell_syntax() -> None:
    command = """
$parseErrors = $null
[System.Management.Automation.Language.Parser]::ParseFile(
    $env:AZBRIEF_SCRIPT_UNDER_TEST,
    [ref]$null,
    [ref]$parseErrors
) | Out-Null
if ($parseErrors.Count -gt 0) {
    $parseErrors | ForEach-Object { Write-Error $_.Message }
    exit 1
}
"""
    result = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-Command", command],
        capture_output=True,
        check=False,
        env={**os.environ, "AZBRIEF_SCRIPT_UNDER_TEST": str(SCRIPT)},
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
