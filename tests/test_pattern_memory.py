"""Tests for the analysis pattern memory (planning-hint) store."""

from types import SimpleNamespace

import pytest

from src.agent import pattern_memory as pm


@pytest.fixture(autouse=True)
def _isolate_store(tmp_path, monkeypatch):
    """Redirect the pattern store to a temp file for every test."""
    store = tmp_path / "analysis_patterns.json"
    monkeypatch.setattr(pm, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(pm, "_PATTERN_FILE", store)
    yield


def _update(services):
    return SimpleNamespace(azure_services=services)


def _result(affected=None, category="retirement"):
    return SimpleNamespace(
        affected_resources=affected or [],
        update_category=category,
    )


def test_normalize_service_strips_acronym_and_case():
    assert pm._normalize_service("Azure Kubernetes Service (AKS)") == "azure kubernetes service"
    assert pm._normalize_service("  Storage   Accounts ") == "storage accounts"


def test_service_keys_dedup_and_normalize():
    upd = _update(["AKS (AKS)", "aks", "Storage"])
    keys = pm._service_keys(upd)
    assert keys == ["aks", "storage"]


def test_extract_successful_tools_only_completed():
    state = {
        "analysis_plan": {
            "tasks": [
                {"tool_name": "query_azure_resources", "status": "completed"},
                {"tool_name": "get_resource_health", "status": "failed"},
                {"tool_name": "get_policy_compliance", "status": "completed"},
                {"status": "completed"},  # missing tool_name → skipped
            ]
        }
    }
    tools = pm.extract_successful_tools(state)
    assert tools == ["query_azure_resources", "get_policy_compliance"]


def test_extract_successful_tools_empty_state():
    assert pm.extract_successful_tools({}) == []
    assert pm.extract_successful_tools({"analysis_plan": {}}) == []


def test_record_then_hint_after_min_samples():
    upd = _update(["Azure Kubernetes Service"])
    res = _result(
        affected=[{"type": "Microsoft.ContainerService/managedClusters"}],
        category="retirement",
    )
    # First sample: below the min-samples threshold → no hint yet.
    pm.record_analysis_pattern(upd, res, ["get_service_resource_details", "get_resource_health"])
    assert pm.build_pattern_hint_for_prompt(upd) == ""

    # Second sample crosses the threshold → hint appears.
    pm.record_analysis_pattern(upd, res, ["get_service_resource_details", "get_policy_compliance"])
    hint = pm.build_pattern_hint_for_prompt(upd)
    assert hint != ""
    assert "azure kubernetes service" in hint
    assert "get_service_resource_details" in hint
    assert "managedclusters" in hint.lower()


def test_record_noop_without_services_or_tools():
    # No services → nothing stored → no hint.
    pm.record_analysis_pattern(_update([]), _result(), ["query_azure_resources"])
    assert pm.build_pattern_hint_for_prompt(_update(["storage"])) == ""

    # No successful tools → nothing stored.
    pm.record_analysis_pattern(_update(["storage"]), _result(), [])
    assert pm.build_pattern_hint_for_prompt(_update(["storage"])) == ""


def test_hint_empty_for_unknown_service():
    upd = _update(["Azure Kubernetes Service"])
    res = _result()
    pm.record_analysis_pattern(upd, res, ["get_resource_health"])
    pm.record_analysis_pattern(upd, res, ["get_resource_health"])
    # Different service was never recorded → no hint.
    assert pm.build_pattern_hint_for_prompt(_update(["Cosmos DB"])) == ""


def test_tool_frequency_accumulates():
    upd = _update(["storage"])
    res = _result()
    for _ in range(3):
        pm.record_analysis_pattern(upd, res, ["query_azure_resources"])
    pm.record_analysis_pattern(upd, res, ["get_security_posture"])
    hint = pm.build_pattern_hint_for_prompt(upd)
    # The more frequent tool should be listed; both are present.
    assert "query_azure_resources" in hint
    assert "from 4 past analyses" in hint


def test_save_does_not_fail_analysis_when_directory_is_read_only(monkeypatch):
    class ReadOnlyDirectory:
        def mkdir(self, **_kwargs):
            raise OSError(30, "Read-only file system")

    monkeypatch.setattr(pm, "_DATA_DIR", ReadOnlyDirectory())

    pm._save({"patterns": {}})
