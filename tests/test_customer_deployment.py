"""Offline customer setup contracts. No Azure resources or model calls are used."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "setup_customer.ps1"
SUBSCRIPTION = "00000000-0000-0000-0000-000000000001"


def customer_script_copy(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "customer"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    script = scripts / SCRIPT.name
    shutil.copyfile(SCRIPT, script)
    activation = repo / ".venv" / "Scripts" / "Activate.ps1"
    activation.parent.mkdir(parents=True)
    activation.write_text("$global:LASTEXITCODE = 0", encoding="ascii")
    return repo, script


@pytest.fixture
def setup_contract() -> dict:
    return {
        "schemaVersion": 1,
        "tenantId": "00000000-0000-0000-0000-000000000002",
        "subscriptionId": SUBSCRIPTION,
        "resourceGroup": "rg-customer",
        "location": "koreacentral",
        "foundryLocation": "koreacentral",
        "foundryAccountName": "aif-customer",
        "foundryProjectName": "customer-agents",
        "foundryProjectId": (
            f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-customer/providers/"
            "Microsoft.CognitiveServices/accounts/aif-customer/projects/customer-agents"
        ),
        "foundryProjectEndpoint": (
            "https://aif-customer.services.ai.azure.com/api/projects/customer-agents"
        ),
        "modelDeploymentName": "test-model",
        "hostedAgentName": "customer-analysis-hosted",
        "specialistAgentNames": {
            "coordinator": "customer-coordinator",
            "resourceGraph": "customer-resource-graph",
            "azureMcp": "customer-azure-mcp",
            "azureApi": "customer-azure-api",
            "reportWriter": "customer-report-writer",
            "qualityReviewer": "customer-quality-reviewer",
        },
        "containerAppName": "ca-customer",
        "schedulerJobName": "caj-customer",
        "containerAppUrl": "https://ca-customer.example.azurecontainerapps.io",
        "azureMcpContainerAppName": "ca-customer-mcp",
        "containerRegistryServer": "customer.azurecr.io",
        "controlPlanePrincipalId": "00000000-0000-0000-0000-000000000003",
        "scheduleDispatcherCronExpression": "*/5 * * * *",
        "networkIsolationMode": "vnetInjection",
        "allowPublicAccessDuringSetup": False,
    }


def run_preview(tmp_path: Path, setup: dict, *arguments: str) -> subprocess.CompletedProcess:
    repo, script = customer_script_copy(tmp_path)
    fixture = tmp_path / "setup.json"
    fixture.write_text(json.dumps(setup), encoding="utf-8")
    return subprocess.run(
        [
            "pwsh",
            "-NoLogo",
            "-NoProfile",
            "-File",
            str(script),
            "-SubscriptionId",
            SUBSCRIPTION,
            "-ResourceGroup",
            "rg-customer",
            "-DeploymentName",
            "customer-foundation",
            "-Environment",
            "customer-prod",
            "-SetupFile",
            str(fixture),
            "-WhatIf",
            *arguments,
        ],
        cwd=repo,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is not installed")
def test_customer_setup_preview_is_offline(tmp_path: Path, setup_contract: dict) -> None:
    result = run_preview(tmp_path, setup_contract)

    assert result.returncode == 0, result.stderr
    assert "customer-prod" in result.stdout
    assert "customer-analysis-hosted" in result.stdout
    assert "What if:" in result.stdout


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is not installed")
@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("schemaVersion", 2, "Unsupported customerSetup"),
        ("subscriptionId", "00000000-0000-0000-0000-000000000009", "do not match"),
        ("resourceGroup", "rg-another", "do not match"),
        ("foundryProjectId", "/subscriptions/wrong", "does not belong"),
        ("foundryProjectEndpoint", "https://example.com", "does not match"),
        ("tenantId", "default", "Invalid GUID"),
        ("foundryLocation", "eastus2", "requires Foundry and deployment region to match"),
        ("hostedAgentName", "customer-coordinator", "must all be distinct"),
    ],
)
def test_customer_setup_rejects_cross_target_contracts(
    tmp_path: Path, setup_contract: dict, field: str, value: object, error: str
) -> None:
    setup_contract[field] = value
    result = run_preview(tmp_path, setup_contract)

    assert result.returncode != 0
    assert error in result.stderr


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is not installed")
def test_customer_setup_rejects_mutable_images(tmp_path: Path, setup_contract: dict) -> None:
    result = run_preview(
        tmp_path,
        setup_contract,
        "-Stage",
        "Application",
        "-Image",
        "customer.azurecr.io/app:latest",
    )

    assert result.returncode != 0
    assert "immutable" in result.stderr


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is not installed")
def test_schedule_requires_explicit_operational_acceptance(
    tmp_path: Path, setup_contract: dict
) -> None:
    result = run_preview(tmp_path, setup_contract, "-Stage", "EnableSchedule")

    assert result.returncode != 0
    assert "AcceptOperationalChecks" in result.stderr


def test_customer_setup_preserves_security_boundaries() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert "Invoke-Expression" not in text
    assert "--force" not in text
    assert "--secrets" not in text
    assert "--set-env-vars" not in text
    assert "dedicated Hosted Agent principal" in text
    assert "Developer credentials/settings must not enter customer setup" in text
    assert "Live stages must read ARM outputs" in text
    assert "--auth-type', 'project-managed-identity'" in text
    assert "--environment', $Environment" in text
    assert "The stage was not completed" in text


def test_portal_wizard_outputs_match_the_deployment_contract() -> None:
    wizard = json.loads((ROOT / "infra" / "createUiDefinition.json").read_text(encoding="utf-8"))
    template = json.loads(
        (ROOT / "infra" / "azbrief-enterprise-deploy.json").read_text(encoding="utf-8")
    )
    parameters = wizard["parameters"]
    outputs = parameters["outputs"]

    assert [step["name"] for step in parameters["steps"]] == ["foundry", "delivery", "access"]
    assert set(outputs) <= set(template["parameters"])
    assert outputs["networkIsolationMode"] == "vnetInjection"
    assert outputs["foundryLocation"] == outputs["location"] == "[location()]"
    assert outputs["allowPublicAccessDuringSetup"] is False
    assert outputs["enableScheduledRuns"] is False
    assert outputs["enableKeyVaultPurgeProtection"] is True
    controls = {
        element["name"]: element for step in parameters["steps"] for element in step["elements"]
    }
    secret = controls["adminSecret"]
    assert secret["type"] == "Microsoft.Common.PasswordBox"
    assert "defaultValue" not in secret
    assert template["parameters"]["adminEntraClientSecret"]["type"].lower() == "securestring"
    assert "adminSecret.password" not in outputs["adminEntraClientSecret"]
    assert controls["model"]["constraints"]["required"] is True
    assert controls["version"]["constraints"]["required"] is True
    assert "defaultValue" not in controls["model"]


def test_readme_buttons_publish_the_same_arm_and_ui_pair() -> None:
    expected_root = "https://raw.githubusercontent.com/Networkdog/AzBriefEnterprise/main/infra/"
    for name in ("README.md", "README.ko.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        links = re.findall(r"\]\((https://portal\.azure\.com/#create/[^)]+)\)", text)
        assert len(links) == 2
        for link in links:
            decoded = unquote(link)
            assert f"/uri/{expected_root}azbrief-enterprise-deploy.json" in decoded
            assert f"/createUIDefinitionUri/{expected_root}createUiDefinition.json" in decoded
        assert "infra/CUSTOMER_DEPLOYMENT.md" in text
    assert (ROOT / "infra" / "CUSTOMER_DEPLOYMENT.md").is_file()


def run_mock_stage(
    tmp_path: Path, setup: dict, stage: str, scenario: str = "", *arguments: str
) -> tuple[subprocess.CompletedProcess, list[list[str]]]:
    repo, script = customer_script_copy(tmp_path)
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps(setup), encoding="utf-8")
    call_log = tmp_path / "calls.jsonl"
    harness = tmp_path / "mock.ps1"
    harness.write_text(
        r"""
$ErrorActionPreference = 'Stop'
$global:fixture = Get-Content $env:FIXTURE -Raw | ConvertFrom-Json -AsHashtable
$global:calls = [System.Collections.Generic.List[object]]::new()
$global:appImage = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
$global:jobImage = $global:appImage
$global:appPort = 80
$global:updateCount = 0
$global:triggerType = 'Manual'
if ($env:STAGE -in @('Verify', 'EnableSchedule')) {
    $global:appImage = $env:IMAGE
    $global:jobImage = if ($env:SCENARIO -eq 'image-mismatch') { 'different' } else { $env:IMAGE }
    $global:appPort = 8000
}
function global:az {
    $global:LASTEXITCODE = 0
    $global:calls.Add(@('az') + $args)
    if ($args[0] -eq 'deployment') {
        return @{ state = 'Succeeded'; setup = $global:fixture } | ConvertTo-Json -Depth 30
    }
    if ($args[0] -eq 'account') {
        return @{ id = $global:fixture.subscriptionId; tenantId = $global:fixture.tenantId } | ConvertTo-Json
    }
    if ($args[0] -eq 'cognitiveservices') {
        return @{ properties = @{ publicNetworkAccess = if ($env:SCENARIO -eq 'public-open') { 'Enabled' } else { 'Disabled' } } } | ConvertTo-Json
    }
    if ($args[0] -eq 'rest') {
        $bodyPath = $args[[array]::IndexOf($args, '--body') + 1].Substring(1)
        $body = Get-Content $bodyPath -Raw
        $body | Set-Content $env:BODY_OUTPUT -Encoding utf8
        $global:triggerType = 'Schedule'
        return '{}'
    }
    if ($args[0] -eq 'resource') {
        $global:updateCount += 1
        if ($env:SCENARIO -eq 'app-failure' -and $global:updateCount -eq 1) {
            $global:LASTEXITCODE = 9
            return
        }
        $imageSetting = @($args | Where-Object { $_ -like 'properties.template.containers[0].image=*' -or $_.StartsWith('properties.template.containers[0].image=') })[0]
        $global:appImage = $imageSetting.Split('=', 2)[1]
        $portSetting = @($args | Where-Object { $_.StartsWith('properties.configuration.ingress.targetPort=') })[0]
        $global:appPort = [int]$portSetting.Split('=', 2)[1]
        return '{}'
    }
    if ($args[0] -eq 'containerapp') {
        if ($args[1] -eq 'revision') {
            return '{"properties":{"healthState":"Healthy","runningState":"Running"}}'
        }
        $isJob = $args[1] -eq 'job'
        if ($args -contains 'update') {
            $global:jobImage = $args[[array]::IndexOf($args, '--image') + 1]
            return '{}'
        }
        $imageValue = if ($isJob) { $global:jobImage } else { $global:appImage }
        $registry = @(@{ server = $global:fixture.containerRegistryServer })
        $configuration = @{ registries = $registry; ingress = @{ targetPort = $global:appPort } }
        if ($isJob) {
            $configuration.triggerType = if ($env:SCENARIO -eq 'scheduled') { 'Schedule' } else { $global:triggerType }
            $configuration.replicaTimeout = 43200
            $configuration.replicaRetryLimit = 0
            $configuration.secrets = @(@{ name = 'api-key'; keyVaultUrl = 'https://example.vault.azure.net/secrets/api-key'; identity = '/mock/identity' })
            if ($global:triggerType -eq 'Manual') {
                $configuration.manualTriggerConfig = @{ parallelism = 1; replicaCompletionCount = 1 }
            }
            else {
                $configuration.scheduleTriggerConfig = @{ cronExpression = '*/5 * * * *'; parallelism = 1; replicaCompletionCount = 1 }
            }
        }
        $container = @{
            name = 'azbrief'; image = $imageValue
            env = @(
                @{ name = 'FOUNDRY_PROJECT_ENDPOINT'; value = $global:fixture.foundryProjectEndpoint }
                @{ name = 'FOUNDRY_HOSTED_AGENT_NAME'; value = $global:fixture.hostedAgentName }
                @{ name = 'API_KEY'; secretRef = 'orchestrator-api-key' }
            )
            probes = @(
                @{ type = 'Liveness'; httpGet = @{ path = '/'; port = 80 } }
                @{ type = 'Readiness'; httpGet = @{ path = '/'; port = 80 } }
            )
        }
        return @{ id = '/mock/resource'; properties = @{ provisioningState = 'Succeeded'; latestRevisionName = 'ready'; latestReadyRevisionName = 'ready'; configuration = $configuration; template = @{ containers = @($container) } } } | ConvertTo-Json -Depth 30
    }
    throw "Unexpected az command: $args"
}
function global:azd {
    $global:LASTEXITCODE = 0
    $global:calls.Add(@('azd') + $args)
    if ($args[0] -eq 'ai' -and $args[1] -eq 'agent') {
        return @{ name = $global:fixture.hostedAgentName; status = 'active' } | ConvertTo-Json
    }
    if ($args[0] -eq 'ai' -and $args[1] -eq 'project') { return '{}' }
    if ($args[0] -eq 'ai' -and $args[1] -eq 'connection') { return }
    if ($args[0] -eq 'deploy') { return }
    if ($args[0] -eq 'provision') {
        $global:calls.Add(@('mcp-location', $env:AZURE_LOCATION))
        return
    }
    if ($args[0] -eq 'env' -and $args[1] -eq 'get-values') {
        $workingDirectory = $args[[array]::IndexOf($args, '--cwd') + 1]
        if ($workingDirectory.Replace('\', '/').EndsWith('/infra/azure-mcp-server')) {
            return @{
                AZURE_MCP_SERVER_URL = 'https://ca-customer-mcp.example.azurecontainerapps.io'
                AZURE_MCP_ENTRA_APP_IDENTIFIER_URI = 'api://00000000-0000-0000-0000-000000000004'
            } | ConvertTo-Json
        }
        $values = @{
            AZURE_TENANT_ID = $global:fixture.tenantId; AZURE_SUBSCRIPTION_ID = $global:fixture.subscriptionId
            AZURE_RESOURCE_GROUP = $global:fixture.resourceGroup; AZURE_LOCATION = $global:fixture.foundryLocation
            AZURE_AI_PROJECT_ENDPOINT = $global:fixture.foundryProjectEndpoint; AZURE_AI_PROJECT_ID = $global:fixture.foundryProjectId
            AZURE_AI_ACCOUNT_NAME = $global:fixture.foundryAccountName; AZURE_AI_PROJECT_NAME = $global:fixture.foundryProjectName
            AZURE_AI_MODEL_DEPLOYMENT_NAME = $global:fixture.modelDeploymentName
            FOUNDRY_PROJECT_ENDPOINT = $global:fixture.foundryProjectEndpoint; FOUNDRY_MODEL_DEPLOYMENT = $global:fixture.modelDeploymentName
            FOUNDRY_HOSTED_AGENT_NAME = $global:fixture.hostedAgentName; FOUNDRY_COORDINATOR_WEB_SEARCH_ENABLED = 'true'
            AZURE_MCP_PROJECT_CONNECTION_NAME = "$($global:fixture.azureMcpContainerAppName)-read-only"
        }
        $roles = @{ coordinator = 'COORDINATOR'; resourceGraph = 'RESOURCE_GRAPH'; azureMcp = 'AZURE_MCP'; azureApi = 'AZURE_API'; reportWriter = 'REPORT_WRITER'; qualityReviewer = 'QUALITY_REVIEWER' }
        foreach ($role in $roles.Keys) {
            $values["FOUNDRY_$($roles[$role])_AGENT_NAME"] = $global:fixture.specialistAgentNames[$role]
            $values["AZBRIEF_PROMPT_$($roles[$role])_AGENT_NAME"] = $global:fixture.specialistAgentNames[$role]
        }
        if ($env:SCENARIO -eq 'cross-environment') { $values.AZURE_TENANT_ID = 'another-tenant' }
        if ($env:STAGE -eq 'Agents') { $values.AZURE_MCP_SERVER_URL = 'https://ca-customer-mcp.example.azurecontainerapps.io' }
        return $values | ConvertTo-Json
    }
    if ($args[0] -eq 'env' -and $args[1] -in @('new', 'set', 'select')) { return }
    throw "Unexpected azd command: $args"
}
function global:python {
    $global:LASTEXITCODE = 0
    $global:calls.Add(@('python') + $args)
    if ($env:SCENARIO -eq 'roster-failure' -and $args -contains '--check') { $global:LASTEXITCODE = 8 }
}
function global:git {
    $global:LASTEXITCODE = 0
    if ($env:SCENARIO -eq 'dirty-source') { return ' M src/hosted_agent.py' }
}
function global:Invoke-RestMethod { return @{ status = 'healthy' } }
try {
    $acceptance = @{}
    if ($env:STAGE -eq 'EnableSchedule') { $acceptance.AcceptOperationalChecks = $true }
    & $env:SCRIPT -SubscriptionId $global:fixture.subscriptionId -ResourceGroup $global:fixture.resourceGroup `
        -DeploymentName customer-foundation -Environment customer-prod -Stage $env:STAGE -Image $env:IMAGE @acceptance
}
finally {
    $global:calls | ForEach-Object { ConvertTo-Json -InputObject $_ -Compress } | Set-Content $env:CALLS -Encoding utf8
}
""",
        encoding="utf-8",
    )
    if scenario == "cross-environment":
        (repo / ".azure" / "customer-prod").mkdir(parents=True)
    result = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-File", str(harness), *arguments],
        cwd=repo,
        env={
            **os.environ,
            "SCRIPT": str(script),
            "FIXTURE": str(fixture),
            "CALLS": str(call_log),
            "BODY_OUTPUT": str(tmp_path / "schedule-body.json"),
            "STAGE": stage,
            "SCENARIO": scenario,
            "IMAGE": "customer.azurecr.io/azbrief-enterprise@sha256:" + "a" * 64,
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    calls = [json.loads(line) for line in call_log.read_text(encoding="utf-8-sig").splitlines()]
    return result, calls


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is not installed")
def test_configure_writes_one_value_per_azd_command(tmp_path: Path, setup_contract: dict) -> None:
    result, calls = run_mock_stage(tmp_path, setup_contract, "Configure")

    assert result.returncode == 0, result.stderr
    settings = [call for call in calls if call[:3] == ["azd", "env", "set"]]
    assert len(settings) == 26
    assert all(call[5:7] == ["--cwd", str(tmp_path / "customer")] for call in settings)
    assert all(call[-3:] == ["--environment", "customer-prod", "--no-prompt"] for call in settings)
    assert {call[3]: call[4] for call in settings}["FOUNDRY_HOSTED_AGENT_NAME"] == (
        "customer-analysis-hosted"
    )
    account_call = next(call for call in calls if call[:3] == ["az", "account", "show"])
    assert "--subscription" not in account_call


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is not installed")
def test_configure_never_rebinds_another_tenant(tmp_path: Path, setup_contract: dict) -> None:
    result, calls = run_mock_stage(tmp_path, setup_contract, "Configure", "cross-environment")

    assert result.returncode != 0
    assert "Refusing to rebind" in result.stderr
    assert not any(call[:3] == ["azd", "env", "set"] for call in calls)


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is not installed")
def test_application_transitions_both_images_and_bootstrap_probes(
    tmp_path: Path, setup_contract: dict
) -> None:
    result, calls = run_mock_stage(tmp_path, setup_contract, "Application")

    assert result.returncode == 0, result.stderr
    assert "scheduling remains manual" in result.stdout
    updates = [call for call in calls if "update" in call]
    assert len(updates) == 2
    assert updates[0][:4] == ["az", "containerapp", "job", "update"]
    assert "properties.configuration.ingress.targetPort=8000" in updates[1]
    assert "properties.template.containers[0].probes[0].httpGet.path=/health" in updates[1]
    assert not any("--trigger-type" in call for call in calls)


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is not installed")
def test_application_failure_rolls_back_without_starting_schedules(
    tmp_path: Path, setup_contract: dict
) -> None:
    result, calls = run_mock_stage(tmp_path, setup_contract, "Application", "app-failure")

    assert result.returncode != 0
    assert "rollback" in result.stderr
    updates = [call for call in calls if "update" in call]
    assert len(updates) == 4
    assert "properties.configuration.ingress.targetPort=80" in updates[-1]
    assert "properties.template.containers[0].probes[0].httpGet.path=/" in updates[-1]
    assert not any("--trigger-type" in call for call in calls)


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is not installed")
def test_initial_application_setup_rejects_running_schedule(
    tmp_path: Path, setup_contract: dict
) -> None:
    result, calls = run_mock_stage(tmp_path, setup_contract, "Application", "scheduled")

    assert result.returncode != 0
    assert "requires a Manual Job" in result.stderr
    assert not any("update" in call for call in calls)


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is not installed")
def test_enable_schedule_preserves_existing_job_configuration(
    tmp_path: Path, setup_contract: dict
) -> None:
    result, calls = run_mock_stage(tmp_path, setup_contract, "EnableSchedule")

    assert result.returncode == 0, result.stderr
    assert "Schedule enabled and verified" in result.stdout
    body = json.loads((tmp_path / "schedule-body.json").read_text(encoding="utf-8-sig"))
    configuration = body["properties"]["configuration"]
    assert configuration["triggerType"] == "Schedule"
    assert "manualTriggerConfig" not in configuration
    assert "eventTriggerConfig" not in configuration
    assert configuration["replicaTimeout"] == 43200
    assert configuration["replicaRetryLimit"] == 0
    assert configuration["secrets"][0]["keyVaultUrl"].endswith("/secrets/api-key")
    assert configuration["scheduleTriggerConfig"]["cronExpression"] == "*/5 * * * *"
    assert any(call[:2] == ["az", "rest"] for call in calls)
    assert not any("--trigger-type" in call for call in calls)


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is not installed")
@pytest.mark.parametrize("scenario", ["public-open", "image-mismatch"])
def test_enable_schedule_rejects_incomplete_readiness(
    tmp_path: Path, setup_contract: dict, scenario: str
) -> None:
    result, calls = run_mock_stage(tmp_path, setup_contract, "EnableSchedule", scenario)

    assert result.returncode != 0
    assert not any(call[:2] == ["az", "rest"] for call in calls)
    assert not (tmp_path / "schedule-body.json").exists()


def test_ci_pins_the_compiler_and_runs_customer_setup_on_windows() -> None:
    template = json.loads(
        (ROOT / "infra" / "azbrief-enterprise-deploy.json").read_text(encoding="utf-8")
    )
    compiler = ".".join(template["metadata"]["_generator"]["version"].split(".")[:3])
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert f"az bicep install --version v{compiler}" in workflow
    assert "runs-on: windows-latest" in workflow
    assert "tests/test_customer_deployment.py" in workflow
    assert workflow.count('"azure.yaml"') == 2
    assert workflow.count('".github/workflows/ci.yml"') == 2
    assert re.search(r"^permissions:\n  contents: read$", workflow, re.MULTILINE)


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is not installed")
def test_mcp_stage_keeps_customer_region_and_authenticated_connection(
    tmp_path: Path, setup_contract: dict
) -> None:
    setup_contract["foundryLocation"] = "eastus2"
    setup_contract["networkIsolationMode"] = "perimeter"
    result, calls = run_mock_stage(tmp_path, setup_contract, "Mcp")

    assert result.returncode == 0, result.stderr
    assert ["mcp-location", "koreacentral"] in calls
    connection = next(call for call in calls if call[:4] == ["azd", "ai", "connection", "create"])
    assert connection[4] == "ca-customer-mcp-read-only"
    assert connection[connection.index("--auth-type") + 1] == "project-managed-identity"
    assert (
        connection[connection.index("--project-endpoint") + 1]
        == setup_contract["foundryProjectEndpoint"]
    )
    saved_url = next(
        call for call in calls if call[:4] == ["azd", "env", "set", "AZURE_MCP_SERVER_URL"]
    )
    assert saved_url[4] == "https://ca-customer-mcp.example.azurecontainerapps.io"


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is not installed")
def test_agent_publication_checks_source_tests_and_roster_before_deploy(
    tmp_path: Path, setup_contract: dict
) -> None:
    result, calls = run_mock_stage(tmp_path, setup_contract, "Agents")

    assert result.returncode == 0, result.stderr
    python_calls = [call for call in calls if call[0] == "python"]
    assert python_calls == [
        ["python", "-c", "import src"],
        ["python", "-m", "pytest", "tests/", "-o", "addopts=", "-x"],
        ["python", "-m", "scripts.provision_foundry_agents"],
        ["python", "-m", "scripts.provision_foundry_agents", "--check"],
    ]
    deployment = next(call for call in calls if call[:2] == ["azd", "deploy"])
    assert calls.index(python_calls[-1]) < calls.index(deployment)
    assert deployment[2] == "azbrief-analysis-hosted"
    assert deployment[-3:] == ["--environment", "customer-prod", "--no-prompt"]


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is not installed")
@pytest.mark.parametrize("scenario", ["dirty-source", "roster-failure"])
def test_agent_publication_stops_before_hosted_deploy_on_failed_gates(
    tmp_path: Path, setup_contract: dict, scenario: str
) -> None:
    result, calls = run_mock_stage(tmp_path, setup_contract, "Agents", scenario)

    assert result.returncode != 0
    assert not any(call[:2] == ["azd", "deploy"] for call in calls)
