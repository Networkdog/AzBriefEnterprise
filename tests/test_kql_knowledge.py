"""Tests for KQL knowledge base persistence."""

import json
import os
import tempfile

import pytest

from src.agent import kql_knowledge
from src.agent.scope import AnalysisScope, analysis_scope_context


@pytest.fixture(autouse=True)
def reset_kql_knowledge():
    """Reset the module-level cache before/after each test."""
    kql_knowledge._cache = None
    original_path = kql_knowledge._cache_path
    yield
    kql_knowledge._cache = None
    kql_knowledge._cache_path = original_path


@pytest.fixture
def temp_kb_path():
    """Create a temporary knowledge base file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"schemas": {}, "queries": {}}, f)
        path = f.name
    from pathlib import Path

    kql_knowledge._cache_path = Path(path)
    kql_knowledge._cache = None
    yield path
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


class TestKQLKnowledge:
    """Tests for KQL knowledge base module-level functions."""

    def test_checked_in_seed_contains_no_runtime_failures(self):
        seed = json.loads(kql_knowledge._SEED_PATH.read_text(encoding="utf-8"))

        assert seed["failed_queries"] == []

    def test_record_schema(self, temp_kb_path: str):
        """Recording a schema should persist and be retrievable."""
        resource_type = "microsoft.compute/virtualmachines"
        properties = ["name", "location", "properties.hardwareProfile.vmSize"]
        kql_knowledge.record_schema(resource_type, properties)

        result = kql_knowledge.get_known_schema(resource_type)
        assert len(result) >= 3
        assert "name" in result
        assert "properties.hardwareProfile.vmSize" in result

    def test_record_successful_query(self, temp_kb_path: str):
        """Recording a successful query should make it retrievable."""
        resource_type = "microsoft.compute/virtualmachines"
        query = "Resources | where type =~ 'microsoft.compute/virtualmachines' | take 10"
        kql_knowledge.record_successful_query(resource_type, "list VMs", query)

        queries = kql_knowledge.get_known_queries(resource_type)
        assert len(queries) > 0
        assert any(q["query"] == query for q in queries)

    def test_record_failed_query(self, temp_kb_path: str):
        """Recording a failed query should store it in the knowledge base."""
        query = "Resources | join kind=inner ..."
        error = "ParserFailure: join not supported"
        kql_knowledge.record_failed_query(query, error)

        kb = kql_knowledge._load()
        assert kb is not None

    def test_runtime_discoveries_do_not_modify_the_checked_in_seed(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AZBRIEF_DATA_DIR", str(tmp_path))
        kql_knowledge._cache_path = None
        kql_knowledge._cache = None
        seed_before = kql_knowledge._SEED_PATH.read_bytes()

        assert kql_knowledge.get_known_schema("microsoft.storage/storageaccounts")
        kql_knowledge.record_failed_query("Resources | take nope", "ParserFailure")

        runtime_path = tmp_path / "kql_knowledge_base.json"
        assert runtime_path.exists()
        assert kql_knowledge._SEED_PATH.read_bytes() == seed_before
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        assert runtime["failed_queries"][-1]["query"] == "Resources | take nope"

    def test_schema_case_insensitive(self, temp_kb_path: str):
        """Schema lookups should be case-insensitive on resource type."""
        kql_knowledge.record_schema("Microsoft.Compute/virtualMachines", ["name", "location"])
        result = kql_knowledge.get_known_schema("microsoft.compute/virtualmachines")
        assert len(result) >= 2

    def test_build_context_for_prompt(self, temp_kb_path: str):
        """build_context_for_prompt should return a string."""
        kql_knowledge.record_schema("microsoft.storage/storageaccounts", ["name", "sku"])
        context = kql_knowledge.build_context_for_prompt()
        assert isinstance(context, str)
        assert "storage" in context.lower()

    def test_reset_clears_cache(self, temp_kb_path: str):
        """reset() should clear the in-memory cache."""
        kql_knowledge.record_schema("microsoft.web/sites", ["name"])
        kql_knowledge.reset()
        # After reset, schemas should be empty
        result = kql_knowledge.get_known_schema("microsoft.web/sites")
        assert result == [] or result is None

    def test_bounded_scope_neither_reads_nor_writes_shared_knowledge(self, temp_kb_path: str):
        resource_type = "microsoft.storage/storageaccounts"
        canonical_query = "Resources | where type =~ 'microsoft.storage/storageaccounts'"
        kql_knowledge.record_schema(resource_type, ["properties.minimumTlsVersion"])
        kql_knowledge.record_successful_query(resource_type, "canonical", canonical_query)
        before = json.loads(open(temp_kb_path, encoding="utf-8").read())

        with analysis_scope_context(AnalysisScope(management_groups=["platform-mg"])):
            assert kql_knowledge.build_context_for_prompt() == ""
            assert kql_knowledge.get_known_schema(resource_type) == []
            assert kql_knowledge.get_known_queries(resource_type) == []
            kql_knowledge.record_schema(resource_type, ["properties.scopeSecret"])
            kql_knowledge.record_successful_query(
                resource_type,
                "scoped",
                canonical_query + " | where name == 'scope-secret'",
            )
            kql_knowledge.record_failed_query("Resources | take 7", "scoped failure")

        after = json.loads(open(temp_kb_path, encoding="utf-8").read())
        assert after == before
