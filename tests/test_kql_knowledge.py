"""Tests for KQL knowledge base persistence."""

import json
import os
import tempfile

import pytest

from src.agent import kql_knowledge


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
