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
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "Development deployment plan" in result.stdout
    assert "did not return valid JSON" not in result.stderr
    assert "Output to File" not in result.stdout
    assert "Remove File" not in result.stdout
    assert not list(tmp_path.glob("tmp*.tmp"))


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
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
