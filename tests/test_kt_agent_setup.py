"""Offline PowerShell checks for the explicit KT Agent publication handoff."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import deploy_kt as kt


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is not available")
@pytest.mark.parametrize("mode", ["Plan", "Preflight", "Deploy"])
def test_kt_once_blocks_current_directory_and_blob_dependencies_without_azure_access(
    tmp_path: Path, mode: str
) -> None:
    root = tmp_path / "customer"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    target = scripts / "deploy_kt_once.ps1"
    shutil.copy2(kt.ROOT / "scripts" / target.name, target)
    activation = root / ".venv" / "Scripts"
    activation.mkdir(parents=True)
    (activation / "Activate.ps1").write_text(
        "$env:AZBRIEF_ONCE_TEST = 'temporary'\n", encoding="utf-8"
    )
    infrastructure = root / "infra" / "v2"
    infrastructure.mkdir(parents=True)
    shutil.copy2(kt.ROOT / "infra/v2/infrastructure.json", infrastructure)
    shutil.copy2(kt.ROOT / "infra/v2/application.json", infrastructure)
    parameters = tmp_path / "parameters.json"
    parameters.write_text(json.dumps({"parameters": {}}), encoding="utf-8")
    report = tmp_path / "report.json"
    wrapper = tmp_path / "run-once.ps1"
    wrapper.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)\n"
        "function az { throw 'Azure access is forbidden in this test' }\n"
        "function azd { throw 'Agent publication is forbidden in this test' }\n"
        "function python { throw 'Python/cloud execution is forbidden before policy checks' }\n"
        "$env:AZBRIEF_ONCE_TEST = 'original'\n"
        "try {\n"
        f"& '{target}' -SubscriptionId '11111111-1111-1111-1111-111111111111' "
        "-TenantId '22222222-2222-2222-2222-222222222222' -ResourceGroup 'rg-kt' "
        f"-ParametersFile '{parameters}' -Environment 'kt-customer' "
        f"-Image 'example.azurecr.io/azbrief@sha256:{'a' * 64}' "
        f"-Mode '{mode}' -ReportPath '{report}'\n"
        "} finally {\n"
        "if ($env:AZBRIEF_ONCE_TEST -cne 'original') { throw 'Caller environment was not restored' }\n"
        "}\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-File", str(wrapper)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert report.exists(), result.stderr
    assert result.stderr is not None
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "blocked"
    assert payload["azureWritesStarted"] is False
    assert payload["operationallyReady"] is False
    assert payload["scheduleEnabled"] is False
    assert any("MCP_DIRECTORY_DEPENDENCY" in entry for entry in payload["blockers"])
    assert any("BLOB_ADMIN_SETTINGS_DEPENDENCY" in entry for entry in payload["blockers"])
    assert all(stage["status"] == "not_started" for stage in payload["stages"])
    assert result.returncode == (0 if mode == "Plan" else 1), result.stderr


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is not available")
@pytest.mark.parametrize(
    ("mode", "failure", "status", "failed_stage"),
    [
        ("Preflight", "none", "preflight_passed_not_deployed", ""),
        ("Deploy", "none", "deployed_pending_operational_acceptance", ""),
        ("Deploy", "dirty", "failed", "LocalGates"),
        ("Deploy", "infrastructure", "failed", "Infrastructure"),
        ("Deploy", "identity", "failed", "HostedIdentity"),
    ],
)
def test_kt_once_stage_order_and_failure_stop_with_synthetic_supported_artifacts(
    tmp_path: Path, mode: str, failure: str, status: str, failed_stage: str
) -> None:
    root = tmp_path / "customer"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    target = scripts / "deploy_kt_once.ps1"
    shutil.copy2(kt.ROOT / "scripts" / target.name, target)
    activation = root / ".venv" / "Scripts"
    activation.mkdir(parents=True)
    (activation / "Activate.ps1").write_text("$env:KT_ONCE_SCOPE = 'temporary'\n", encoding="utf-8")
    (scripts / "setup_kt_agents.ps1").write_text(
        "param($SubscriptionId,$ResourceGroup,$DeploymentName,$Environment,$Stage)\n"
        "$global:calls.Add('agents-' + $Stage)\n",
        encoding="utf-8",
    )
    infrastructure = root / "infra" / "v2"
    infrastructure.mkdir(parents=True)
    documents = {
        "infrastructure": {
            "parameters": {
                "mcpAuthenticationMode": {
                    "defaultValue": "foundrySubnetTrust",
                    "allowedValues": ["foundrySubnetTrust"],
                },
                "foundryHostedAgentName": {"defaultValue": "azbrief-analysis-hosted"},
            }
        },
        "application": {},
        "runtime-roles": {},
    }
    for name, document in documents.items():
        (infrastructure / f"{name}.json").write_text(json.dumps(document), encoding="utf-8")
        (infrastructure / f"{name}.bicep").write_text("", encoding="utf-8")
    parameters = tmp_path / "parameters.json"
    parameters.write_text(json.dumps({"parameters": {}}), encoding="utf-8")
    report = tmp_path / "report.json"
    calls = tmp_path / "calls.json"
    agent = {
        "name": "foreign-agent" if failure == "identity" else "azbrief-analysis-hosted",
        "status": "active",
        "version": "1",
        "instance_identity": {"principal_id": "33333333-3333-3333-3333-333333333333"},
    }
    wrapper = tmp_path / "run.ps1"
    wrapper.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)\n"
        "$global:calls = [Collections.Generic.List[string]]::new()\n"
        f"$global:fixtureRoot = '{root}'\n"
        f"$global:failure = '{failure}'\n"
        "function git { $global:LASTEXITCODE = 0; if ($global:failure -eq 'dirty') { ' M src/config.py' } }\n"
        "function bicep {\n"
        "  $global:LASTEXITCODE = 0\n"
        "  if ($args[0] -eq '--version') { 'Bicep CLI version 0.46.1'; return }\n"
        "  $name = [IO.Path]::GetFileNameWithoutExtension($args[1])\n"
        "  [IO.File]::WriteAllText($args[3], [IO.File]::ReadAllText((Join-Path $global:fixtureRoot ('infra/v2/' + $name + '.json'))), [Text.UTF8Encoding]::new($false))\n"
        "}\n"
        "function python {\n"
        "  if ($env:PYTHONUTF8 -cne '1') { throw 'Python must use UTF-8 during deployment checks' }\n"
        "  $text = $args -join ' '\n"
        "  $global:calls.Add($text)\n"
        "  $global:LASTEXITCODE = if ($global:failure -eq 'infrastructure' -and $text.Contains('infrastructure') -and $text.Contains('--mode deploy')) { 13 } else { 0 }\n"
        "}\n"
        "function az { throw 'Unexpected Azure CLI access' }\n"
        "function azd {\n"
        "  $global:calls.Add('hosted-identity')\n"
        "  $global:LASTEXITCODE = 0\n"
        f"  '{json.dumps(agent)}'\n"
        "}\n"
        "$env:KT_ONCE_SCOPE = 'original'\n"
        "$env:PYTHONUTF8 = '0'\n"
        "try {\n"
        f"& '{target}' -SubscriptionId '11111111-1111-1111-1111-111111111111' "
        "-TenantId '22222222-2222-2222-2222-222222222222' -ResourceGroup 'rg-kt' "
        f"-ParametersFile '{parameters}' -Environment 'kt-customer' "
        f"-Image 'example.azurecr.io/azbrief@sha256:{'a' * 64}' -Mode '{mode}' -ReportPath '{report}'\n"
        "} finally {\n"
        f"[IO.File]::WriteAllText('{calls}', (ConvertTo-Json -InputObject @($global:calls)), [Text.UTF8Encoding]::new($false))\n"
        "if ($env:KT_ONCE_SCOPE -cne 'original') { throw 'Environment restoration failed' }\n"
        "if ($env:PYTHONUTF8 -cne '0') { throw 'Python UTF-8 setting was not restored' }\n"
        "}\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-File", str(wrapper)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert report.exists(), result.stderr
    assert result.stderr is not None
    payload = json.loads(report.read_text(encoding="utf-8"))
    observed = json.loads(calls.read_text(encoding="utf-8"))
    assert payload["status"] == status, result.stderr
    assert payload["operationallyReady"] is False
    assert payload["scheduleEnabled"] is False
    assert result.returncode == (1 if failed_stage else 0), result.stderr
    if failed_stage:
        failed = next(
            index for index, stage in enumerate(payload["stages"]) if stage["name"] == failed_stage
        )
        assert payload["stages"][failed]["status"] == "failed"
        assert all(stage["status"] == "not_started" for stage in payload["stages"][failed + 1 :])
    if mode == "Preflight" or failure == "dirty":
        assert payload["azureWritesStarted"] is False
        assert not any("--mode deploy" in command for command in observed)
    if mode == "Deploy" and failure == "none":
        assert observed.index("agents-Configure") < observed.index("agents-Publish")
        assert observed.index("agents-Publish") < observed.index("hosted-identity")
        assert any(
            "--hosted-principal-id 33333333-3333-3333-3333-333333333333" in command
            for command in observed
        )
        assert all(
            "--no-automatic-setup" in command
            for command in observed
            if "scripts.deploy_v2 application" in command
        )
        assert payload["stages"][-1]["status"] == "not_started"


def _setup() -> dict:
    subscription = "11111111-1111-1111-1111-111111111111"
    return {
        "schemaVersion": 1,
        "profile": "kt-private-foundation",
        "subscriptionId": subscription,
        "tenantId": "22222222-2222-2222-2222-222222222222",
        "resourceGroup": "rg-kt",
        "location": "eastus2",
        "capabilityHostRequested": True,
        "runtimeSetup": {
            "schemaVersion": 1,
            "modelDeploymentName": "approved-core",
            "simpleModelDeploymentName": "",
            "foundryAccountName": "ai-kt",
            "projectName": "project-kt",
            "projectResourceId": (
                f"/subscriptions/{subscription}/resourceGroups/rg-kt"
                "/providers/Microsoft.CognitiveServices/accounts/ai-kt/projects/project-kt"
            ),
            "projectEndpoint": "https://ai-kt.services.ai.azure.com/api/projects/project-kt",
            "hostedAgentName": "kt-hosted",
            "promptAgentNames": {
                role: f"kt-{suffix}" for role, suffix in kt._ROLE_NAME_SUFFIXES.items()
            },
            "azureMcpServerUrl": "https://kt-mcp.example.invalid/mcp",
            "azureMcpProjectConnectionName": "kt-mcp-read-only",
        },
    }


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is not available")
@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("none", ""),
        ("scope", "successful second-stage KT"),
        ("model", "approved model deployment"),
        ("project", "project identity or endpoint"),
        ("roles", "six distinct"),
        ("state", "successful second-stage KT"),
        ("stage-string", "successful second-stage KT"),
        ("tenant", "Invalid KT identity"),
        ("schema", "Unsupported KT runtime"),
        ("agent-type", "six distinct"),
        ("hosted-collision", "six distinct"),
        ("model-url", "Invalid model deployment name"),
        ("mcp-url", "approved HTTPS URL"),
        ("shared-foundry", ""),
        ("shared-foundry-subdomain", ""),
        ("shared-foundry-project", "project identity or endpoint"),
        ("shared-foundry-subscription", "exact typed ID"),
        ("shared-foundry-type", "exact typed ID"),
        ("shared-foundry-invalid-subdomain", "Invalid Foundry custom subdomain"),
    ],
)
def test_kt_agent_setup_what_if_does_not_mutate_or_publish(
    tmp_path: Path, change: str, message: str
):
    setup = _setup()
    if change == "scope":
        setup["resourceGroup"] = "rg-foreign"
    elif change == "model":
        setup["runtimeSetup"]["modelDeploymentName"] = ""
    elif change == "project":
        setup["runtimeSetup"]["projectResourceId"] += "-foreign"
    elif change == "roles":
        setup["runtimeSetup"]["promptAgentNames"]["coordinator"] = setup["runtimeSetup"][
            "promptAgentNames"
        ]["resource_graph"]
    elif change == "stage-string":
        setup["capabilityHostRequested"] = "true"
    elif change == "tenant":
        setup["tenantId"] = "not-a-tenant"
    elif change == "schema":
        setup["runtimeSetup"]["schemaVersion"] = "1"
    elif change == "agent-type":
        setup["runtimeSetup"]["promptAgentNames"]["coordinator"] = None
    elif change == "hosted-collision":
        setup["runtimeSetup"]["hostedAgentName"] = setup["runtimeSetup"]["promptAgentNames"][
            "coordinator"
        ]
    elif change == "model-url":
        setup["runtimeSetup"]["modelDeploymentName"] = "https://unapproved.invalid"
    elif change == "mcp-url":
        setup["runtimeSetup"][
            "azureMcpServerUrl"
        ] = "https://user:private-marker@unapproved.invalid"
    if change.startswith("shared-foundry"):
        shared_id = (
            setup["runtimeSetup"]["projectResourceId"]
            .split("/projects/")[0]
            .replace("/resourceGroups/rg-kt/", "/resourceGroups/rg-shared-ai/")
        )
        setup["runtimeSetup"]["foundryAccountResourceId"] = shared_id
        setup["runtimeSetup"]["projectResourceId"] = shared_id + "/projects/project-kt"
        if change == "shared-foundry-subdomain":
            setup["runtimeSetup"]["foundryAccountSubdomain"] = "shared-endpoint"
            setup["runtimeSetup"][
                "projectEndpoint"
            ] = "https://shared-endpoint.services.ai.azure.com/api/projects/project-kt"
        elif change == "shared-foundry-project":
            setup["runtimeSetup"]["projectResourceId"] += "-foreign"
        elif change == "shared-foundry-subscription":
            setup["runtimeSetup"]["foundryAccountResourceId"] = shared_id.replace(
                setup["subscriptionId"], "99999999-9999-9999-9999-999999999999"
            )
        elif change == "shared-foundry-type":
            setup["runtimeSetup"]["foundryAccountResourceId"] = shared_id.replace(
                "Microsoft.CognitiveServices/accounts", "Microsoft.Storage/storageAccounts"
            )
        elif change == "shared-foundry-invalid-subdomain":
            setup["runtimeSetup"]["foundryAccountSubdomain"] = "https://not-a-subdomain.invalid"
    source = tmp_path / "setup.json"
    calls = tmp_path / "calls.txt"
    source.write_text(
        json.dumps(
            {
                "provisioningState": "Failed" if change == "state" else "Succeeded",
                "foundation": setup,
            }
        ),
        encoding="utf-8",
    )
    target = kt.ROOT / "scripts" / "setup_kt_agents.ps1"
    wrapper = tmp_path / "test-stage.ps1"
    wrapper.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "function az {\n"
        "  if ($args[0] -ne 'deployment') { throw 'Unexpected live Azure operation' }\n"
        f"  [IO.File]::AppendAllText('{calls}', 'read-deployment' + [Environment]::NewLine)\n"
        "  $global:LASTEXITCODE = 0\n"
        f"  Get-Content -LiteralPath '{source}' -Raw -Encoding UTF8\n"
        "}\n"
        "function azd { throw 'WhatIf must not mutate azd state' }\n"
        "function python { throw 'WhatIf must not run Python or publish agents' }\n"
        f"& '{target}' -SubscriptionId '{setup['subscriptionId']}' "
        "-ResourceGroup 'rg-kt' -DeploymentName 'stage-two' -Environment 'kt-test' -WhatIf\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-File", str(wrapper)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    if message:
        assert result.returncode != 0
        assert message in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert "What if:" in result.stdout
    assert calls.read_text(encoding="utf-8").splitlines() == ["read-deployment"]


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is not available")
@pytest.mark.parametrize(
    ("stage", "change", "error"),
    [
        ("Check", "none", ""),
        ("Publish", "none", ""),
        ("Check", "account", "Default Azure CLI"),
        ("Check", "environment", "another customer environment"),
        ("Check", "binding", "KT environment binding changed"),
        ("Check", "status", "active version"),
        ("Check", "name", "active version"),
        ("Publish", "tests", "exit code 13"),
    ],
)
def test_kt_setup_checks_targets_and_restores_the_callers_environment(
    tmp_path: Path, stage: str, change: str, error: str
):
    setup = _setup()
    root = tmp_path / "customer"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(kt.ROOT / "scripts" / "setup_kt_agents.ps1", scripts / "setup_kt_agents.ps1")
    activation = root / ".venv" / "Scripts"
    activation.mkdir(parents=True)
    (activation / "Activate.ps1").write_text(
        "$env:AZBRIEF_SETUP_ACTIVATED = 'temporary'\n", encoding="utf-8"
    )
    (scripts / "deploy_hosted_agent.ps1").write_text(
        "param([string]$Environment)\n"
        "$global:calls.Add('hosted-publication')\n"
        "$env:AZBRIEF_SETUP_ACTIVATED = 'publication'\n",
        encoding="utf-8",
    )
    state = tmp_path / "setup.json"
    state.write_text(
        json.dumps({"provisioningState": "Succeeded", "foundation": setup}), encoding="utf-8"
    )
    azure = root / ".azure"
    azure.mkdir()
    (azure / "config.json").write_text(
        json.dumps({"defaultEnvironment": "previous"}), encoding="utf-8"
    )
    calls = tmp_path / "calls.json"
    wrapper = tmp_path / "exercise.ps1"
    wrapper.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "$global:calls = [Collections.Generic.List[string]]::new()\n"
        "$global:values = @{}\n"
        "$global:LASTEXITCODE = 0\n"
        "$env:AZURE_DEV_USER_AGENT = 'original-agent'\n"
        "$env:AZURE_CLIENT_ID = 'ambient-client'\n"
        "$env:FOUNDRY_PROJECT_ENDPOINT = 'ambient-endpoint'\n"
        "$env:OTEL_SDK_DISABLED = 'original-sdk'\n"
        f"$global:setup = Get-Content -LiteralPath '{state}' -Raw -Encoding UTF8 | ConvertFrom-Json -AsHashtable\n"
        "function az {\n"
        "  $global:LASTEXITCODE = 0\n"
        "  if ($args[0] -eq 'deployment') { return ($global:setup | ConvertTo-Json -Depth 100) }\n"
        "  if ($args[0] -eq 'account') {\n"
        f"    return '{{\"id\":\"{setup['subscriptionId'] if change != 'account' else 'foreign'}\",\"tenantId\":\"{setup['tenantId']}\"}}'\n"
        "  }\n"
        "  throw 'Unexpected live Azure operation'\n"
        "}\n"
        "function azd {\n"
        "  if ($env:AZURE_DEV_USER_AGENT -cne 'microsoft_foundry_skill') { throw 'Missing local user agent' }\n"
        "  $global:LASTEXITCODE = 0\n"
        "  $global:calls.Add(($args[0..1] -join ' '))\n"
        "  if ($args[0] -eq 'env') {\n"
        "    switch ($args[1]) {\n"
        f"      'new' {{ New-Item -ItemType Directory -Path '{azure}\\kt-test' | Out-Null }}\n"
        "      'get-values' { return ($global:values | ConvertTo-Json -Depth 100) }\n"
        "      'set' { $global:values[$args[2]] = $args[3] }\n"
        "      'select' { if ($args[2] -cne 'previous') { throw 'Default environment changed' } }\n"
        "    }\n"
        "    return\n"
        "  }\n"
        "  if ($args[0] -eq 'ai' -and $args[1] -eq 'agent') {\n"
        f"    return '{{\"name\":\"{setup['runtimeSetup']['hostedAgentName'] if change != 'name' else 'foreign-agent'}\",\"version\":\"7\",\"status\":\"{'active' if change != 'status' else 'failed'}\"}}'\n"
        "  }\n"
        "  throw 'Unexpected azd operation'\n"
        "}\n"
        "function git { $global:LASTEXITCODE = 0 }\n"
        "function python {\n"
        "  $global:LASTEXITCODE = 0\n"
        "  $global:calls.Add(('python ' + ($args -join ' ')))\n"
        "  if ($args[1] -eq 'pytest') {\n"
        "    if ($env:OTEL_SDK_DISABLED -cne 'true') { throw 'Tests must disable SDK export locally' }\n"
        + ("    $global:LASTEXITCODE = 13\n" if change == "tests" else "")
        + "  }\n"
        "}\n"
        + (
            f"New-Item -ItemType Directory -Path '{azure}\\kt-test' | Out-Null\n"
            "$global:values['AZURE_SUBSCRIPTION_ID'] = 'foreign'\n"
            if change == "environment"
            else ""
        )
        + "$failed = $false\n"
        "try {\n"
        f"  & '{scripts}\\setup_kt_agents.ps1' -SubscriptionId '{setup['subscriptionId']}' "
        "-ResourceGroup 'rg-kt' -DeploymentName 'stage-two' -Environment 'kt-test'\n"
        + (
            "  $global:values['FOUNDRY_REPORT_WRITER_AGENT_NAME'] = 'wrong-agent'\n"
            if change == "binding"
            else ""
        )
        + f"  & '{scripts}\\setup_kt_agents.ps1' -SubscriptionId '{setup['subscriptionId']}' "
        f"-ResourceGroup 'rg-kt' -DeploymentName 'stage-two' -Environment 'kt-test' -Stage '{stage}'\n"
        "} catch { $failed = $true; [Console]::Error.WriteLine($_.Exception.Message) }\n"
        "finally {\n"
        f"  [IO.File]::WriteAllText('{calls}', (ConvertTo-Json -InputObject @($global:calls)))\n"
        "  if ($env:AZURE_DEV_USER_AGENT -cne 'original-agent' -or\n"
        "      $env:AZURE_CLIENT_ID -cne 'ambient-client' -or\n"
        "      $env:FOUNDRY_PROJECT_ENDPOINT -cne 'ambient-endpoint' -or\n"
        "      $env:OTEL_SDK_DISABLED -cne 'original-sdk' -or $env:AZBRIEF_SETUP_ACTIVATED) {\n"
        "    throw 'Caller environment was not restored'\n"
        "  }\n"
        "}\n"
        "if ($failed) { exit 1 }\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-File", str(wrapper)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    recorded = json.loads(calls.read_text(encoding="utf-8"))
    if error:
        assert result.returncode != 0
        assert error in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert "ai agent" in recorded
        assert ("hosted-publication" in recorded) is (stage == "Publish")
    assert "Caller environment was not restored" not in result.stderr
    if change in {"account", "environment"}:
        assert "env set" not in recorded
    if change == "tests":
        assert "python -m scripts.provision_foundry_agents" not in recorded
        assert "hosted-publication" not in recorded


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is not available")
@pytest.mark.parametrize("scenario", ["not-deployed", "active", "denied", "malformed-active"])
def test_guarded_hosted_state_supports_initial_publication_without_masking_failures(
    tmp_path: Path, scenario: str
):
    source = (kt.ROOT / "scripts" / "deploy_hosted_agent.ps1").read_text(encoding="utf-8")
    start = source.index("function Get-AgentState {")
    end = source.index("function Import-AzdEnvironment {", start)
    state = {
        "name": "kt-hosted",
        "version": "7",
        "status": "active",
        "definition": {"code_configuration": {"content_hash": "approved-hash"}},
        "playground_url": "https://example.invalid",
    }
    if scenario == "not-deployed":
        state = {"status": "not_deployed"}
    elif scenario == "malformed-active":
        state = {"name": "kt-hosted", "status": "active"}
    fixture = tmp_path / "agent.json"
    fixture.write_text(json.dumps(state), encoding="utf-8")
    wrapper = tmp_path / "state.ps1"
    invocation = (
        "throw 'Lookup denied'\n"
        if scenario == "denied"
        else f"Get-Content -LiteralPath '{fixture}' -Raw -Encoding UTF8 | ConvertFrom-Json\n"
    )
    wrapper.write_text(
        "Set-StrictMode -Version Latest\n$ErrorActionPreference = 'Stop'\n"
        "$Service = 'azbrief-analysis-hosted'\n$Environment = 'kt-test'\n"
        "function Invoke-AzdJson { param([string[]]$ArgumentList)\n"
        + invocation
        + "}\n"
        + source[start:end]
        + "Get-AgentState | ConvertTo-Json\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-File", str(wrapper)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    if scenario in {"denied", "malformed-active"}:
        assert result.returncode != 0
        assert '"Status": "not_deployed"' not in result.stdout
    else:
        assert result.returncode == 0, result.stderr
        actual = json.loads(result.stdout)
        assert actual["Status"] == ("not_deployed" if scenario == "not-deployed" else "active")
        assert actual["Version"] == ("" if scenario == "not-deployed" else "7")
