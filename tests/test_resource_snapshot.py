"""Tests for the deployment-excluded NDJSON Resource Graph replay engine."""

import json
import re
from pathlib import Path

import pytest

from scripts.resource_snapshot import (
    SNAPSHOT_GAP_TOOL_NAMES,
    SnapshotError,
    SnapshotResourceGraphService,
    build_snapshot_tools,
)


def _write_ndjson(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


@pytest.fixture
def snapshot_dir(tmp_path: Path) -> Path:
    subscriptions = [
        {"subscriptionId": "00000000-0000-4000-8000-000000000001", "displayName": "Prod"},
        {"subscriptionId": "00000000-0000-4000-8000-000000000002", "displayName": "Dev"},
    ]
    resources = [
        {
            "id": "/subscriptions/00000000-0000-4000-8000-000000000001/resourceGroups/rg-a/providers/Microsoft.Storage/storageAccounts/store-a",
            "name": "store-a",
            "type": "microsoft.storage/storageaccounts",
            "location": "koreacentral",
            "resourceGroup": "rg-a",
            "subscriptionId": subscriptions[0]["subscriptionId"],
            "sku": {"name": "Standard_LRS"},
            "properties": {
                "minimumTlsVersion": "TLS1_0",
                "privateEndpointConnections": [{"id": "one"}],
                "publicNetworkAccess": "Enabled",
            },
            "tags": {"environment": "prod"},
        },
        {
            "id": "/subscriptions/00000000-0000-4000-8000-000000000002/resourceGroups/rg-b/providers/Microsoft.Storage/storageAccounts/store-b",
            "name": "store-b",
            "type": "microsoft.storage/storageaccounts",
            "location": "eastus2",
            "resourceGroup": "rg-b",
            "subscriptionId": subscriptions[1]["subscriptionId"],
            "sku": {"name": "Standard_GRS"},
            "properties": {
                "minimumTlsVersion": "TLS1_2",
                "privateEndpointConnections": [],
                "publicNetworkAccess": "Disabled",
            },
            "tags": {"environment": "dev"},
        },
        {
            "id": "/subscriptions/00000000-0000-4000-8000-000000000001/resourceGroups/rg-a/providers/Microsoft.Compute/virtualMachines/vm-a",
            "name": "vm-a",
            "type": "microsoft.compute/virtualmachines",
            "location": "koreacentral",
            "resourceGroup": "rg-a",
            "subscriptionId": subscriptions[0]["subscriptionId"],
            "properties": {"hardwareProfile": {"vmSize": "Standard_D2s_v5"}},
        },
    ]
    manifest = {
        "tool": "azsnapshot",
        "version": "1.0.0",
        "startedUtc": "2026-08-11T03:47:52+00:00",
        "finishedUtc": "2026-08-11T04:25:55+00:00",
        "subscriptions": subscriptions,
        "counts": {"resources": 3, "resourcecontainers": 0, "advisorresources": 2},
        "warnings": [],
        "errors": [],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _write_ndjson(tmp_path / "resources.ndjson", resources)
    _write_ndjson(tmp_path / "resourcecontainers.ndjson", [])
    _write_ndjson(
        tmp_path / "healthresources.ndjson",
        [
            {
                "name": "current",
                "type": "microsoft.resourcehealth/availabilitystatuses",
                "subscriptionId": subscriptions[0]["subscriptionId"],
                "resourceGroup": "rg-a",
                "properties": {
                    "availabilityState": "Available",
                    "previousAvailabilityState": "Unknown",
                    "targetResourceId": resources[0]["id"],
                    "targetResourceType": "microsoft.storage/storageaccounts",
                },
            }
        ],
    )
    _write_ndjson(
        tmp_path / "advisorresources.ndjson",
        [
            {
                "subscriptionId": subscriptions[0]["subscriptionId"],
                "properties": {
                    "value": [
                        {"id": "rec-1", "properties": {"category": "Cost"}},
                        {"id": "rec-2", "properties": {"category": "Security"}},
                    ]
                },
            }
        ],
    )
    return tmp_path


@pytest.mark.asyncio
async def test_resource_summary_and_nested_projection(snapshot_dir: Path) -> None:
    service = SnapshotResourceGraphService(snapshot_dir)

    summary = await service.get_resource_types_summary()
    detail = await service.query_resources(
        """
        Resources
        | where type =~ 'Microsoft.Storage/storageAccounts'
        | extend tls = tostring(properties.minimumTlsVersion)
        | extend privateEndpoints = array_length(properties.privateEndpointConnections)
        | project name, subscriptionId, location, tls, privateEndpoints, skuName = sku.name
        | order by name asc
        """
    )

    assert summary["data"] == [
        {"type": "microsoft.storage/storageaccounts", "count_": 2},
        {"type": "microsoft.compute/virtualmachines", "count_": 1},
    ]
    assert detail["data"] == [
        {
            "name": "store-a",
            "subscriptionId": "00000000-0000-4000-8000-000000000001",
            "location": "koreacentral",
            "tls": "TLS1_0",
            "privateEndpoints": 1,
            "skuName": "Standard_LRS",
        },
        {
            "name": "store-b",
            "subscriptionId": "00000000-0000-4000-8000-000000000002",
            "location": "eastus2",
            "tls": "TLS1_2",
            "privateEndpoints": 0,
            "skuName": "Standard_GRS",
        },
    ]


@pytest.mark.asyncio
async def test_where_functions_scope_and_advisor_wrapper(snapshot_dir: Path) -> None:
    service = SnapshotResourceGraphService(snapshot_dir)
    scoped = await service.query_resources(
        """
        Resources
        | where type contains 'storage' and properties.minimumTlsVersion in~ ('TLS1_0')
        | project name, environment = tags['environment']
        """,
        subscriptions=["00000000-0000-4000-8000-000000000001"],
    )
    advisor = await service.query_resources(
        "AdvisorResources | summarize count() by category = properties.category | order by category asc"
    )

    assert scoped["data"] == [{"name": "store-a", "environment": "prod"}]
    assert advisor["data"] == [
        {"category": "Cost", "count_": 1},
        {"category": "Security", "count_": 1},
    ]


def test_validation_and_prompt_context_are_snapshot_specific(snapshot_dir: Path) -> None:
    service = SnapshotResourceGraphService(snapshot_dir)

    validation = service.validate(full=True)

    assert validation["ok"] is True
    assert validation["count_mismatches"] == {}
    assert service.metadata.subscription_count == 2
    assert "2026-08-11T04:25:55+00:00" in service.prompt_context()
    assert "never as current state" in service.prompt_context()


@pytest.mark.asyncio
async def test_unknown_table_and_live_only_kql_fail_closed(snapshot_dir: Path) -> None:
    service = SnapshotResourceGraphService(snapshot_dir)

    with pytest.raises(SnapshotError, match="not present"):
        await service.query_resources("MissingTable | take 1")
    with pytest.raises(SnapshotError, match="does not support join"):
        await service.query_resources("Resources | join ResourceContainers on subscriptionId")


@pytest.mark.asyncio
async def test_snapshot_tools_replace_live_evidence_sources(snapshot_dir: Path) -> None:
    service = SnapshotResourceGraphService(snapshot_dir)
    tools = {tool.name: tool for tool in build_snapshot_tools(service)}

    assert SNAPSHOT_GAP_TOOL_NAMES <= tools.keys()
    assert tools["query_azure_resources"]._service is service
    health = await tools["get_resource_health"].ainvoke(
        {"resource_type": "Microsoft.Storage/storageAccounts"}
    )
    cost_gap = await tools["get_cost_by_service"].ainvoke({"service_name": "Storage"})

    assert "Available" in health
    assert "offline snapshot gap" in cost_gap.lower()
    assert "No live Azure call was made" in cost_gap


def test_snapshot_runtime_files_are_excluded_from_both_deployments() -> None:
    root = Path(__file__).parents[1]
    agentignore = (root / ".agentignore").read_text(encoding="utf-8")
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")

    assert re.search(r"(?m)^scripts/$", agentignore)
    assert "COPY scripts/" not in dockerfile