"""Tests for KQL sanitization logic in src/agent/tools.py."""

import pytest

from src.agent.tools import sanitize_kql


class TestSanitizeKql:
    """Test KQL sanitization/pre-processing."""

    def test_top_without_by_replaced(self):
        """'| top N' without ORDER BY should become '| take N'."""
        query = "Resources | where type =~ 'Microsoft.Compute/virtualMachines' | top 50"
        result = sanitize_kql(query)
        assert "| take 50" in result
        assert "| top 50" not in result

    def test_top_with_by_preserved(self):
        """'| top N by col' is valid KQL — keep it."""
        query = "Resources | top 10 by name asc"
        result = sanitize_kql(query)
        assert "| top 10 by name asc" in result

    def test_stray_top_without_pipe(self):
        """'top N' not preceded by pipe → '| take N'."""
        query = "Resources | project name top 50"
        result = sanitize_kql(query)
        assert "| take 50" in result

    def test_let_statements_removed(self):
        """let statements (unsupported in Resource Graph) are removed."""
        query = "let foo = 42;\nResources | take 10"
        result = sanitize_kql(query)
        assert "let " not in result
        assert "Resources" in result

    def test_render_removed(self):
        """render operator (unsupported) is removed."""
        query = "Resources | summarize count() by type | render barchart"
        result = sanitize_kql(query)
        assert "render" not in result

    def test_project_except_to_project_away(self):
        """project-except → project-away (Resource Graph syntax)."""
        query = "Resources | project-except name"
        result = sanitize_kql(query)
        assert "project-away" in result
        assert "project-except" not in result

    def test_kind_alias_requires_explicit_repair_not_silent_renaming(self):
        """Repair must update the whole alias contract, not only its declaration."""
        query = "Resources | project id, kind=tostring(kind) | where kind == 'StorageV2'"
        assert sanitize_kql(query) == query

    @pytest.mark.parametrize(
        "expression",
        [
            "tostring(properties.minimumTlsVersion)",
            "coalesce(tostring(properties.minimumTlsVersion), 'unknown')",
            "iff(isnull(properties.minimumTlsVersion), 'unknown', "
            "tostring(properties.minimumTlsVersion))",
            "properties.minimumTlsVersion",
        ],
    )
    def test_project_expression_and_downstream_filter_preserved(self, expression: str):
        query = f"Resources | project id, tls={expression} | where tls == 'TLS1_0'"
        assert sanitize_kql(query) == query

    def test_duplicate_pipes_cleaned(self):
        """|| or '| |' collapsed to single |."""
        query = "Resources || where type =~ 'x'"
        result = sanitize_kql(query)
        assert "||" not in result

    def test_trailing_semicolons_stripped(self):
        """Trailing semicolons removed."""
        query = "Resources | take 10;"
        result = sanitize_kql(query)
        assert not result.endswith(";")

    def test_missing_table_name_prepended(self):
        """Query starting with '|' gets 'Resources' prepended."""
        query = "| where type =~ 'Microsoft.Compute/virtualMachines'"
        result = sanitize_kql(query)
        assert result.startswith("Resources")

    def test_unsupported_table_is_not_replaced_with_resources(self):
        """Unsupported syntax must fail explicitly, not become unrelated inventory."""
        query = "datatable(a:string)['foo'] | take 1"
        assert sanitize_kql(query) == query

    @pytest.mark.parametrize(
        "query",
        [
            "Resources | project id, label='please take care' | order by id asc",
            'Resources | where tags["project name"] == "top 10" | project id',
            "Resources | where name == 'let take; | render table' | project id",
            "Resources | where name == 'can''t take this' | project id",
            r"Resources | where name == 'can\'t take this' | project id",
            "Resources // project name take 1\n| project id",
            "Resources /* | top 10 */ | project id",
            "Resources\n|    project id\n|    order by id asc",
            "Resources | mv-expand subnet=properties.subnets limit 2000 | project id, subnet",
            "Resources | project id, owner=tolower(properties.managedBy) "
            "| join kind=leftouter (Resources | project owner=tolower(id), ownerName=name) "
            "on owner | project id, ownerName",
        ],
    )
    def test_valid_exploratory_query_preserved(self, query: str):
        assert sanitize_kql(query) == query

    def test_let_inlining_does_not_replace_literal_content(self):
        query = "let tier = 'Basic'; Resources | where sku.name == tier | project note='tier'"
        result = sanitize_kql(query)
        assert "sku.name == 'Basic'" in result
        assert "note='tier'" in result

    def test_normal_query_unchanged(self):
        """Well-formed query passes through without modification."""
        query = (
            "Resources\n"
            "| where type =~ 'Microsoft.Storage/storageAccounts'\n"
            "| project name, location, sku\n"
            "| take 100"
        )
        result = sanitize_kql(query)
        # Core query structure should be preserved
        assert "Microsoft.Storage/storageAccounts" in result
        assert "| take 100" in result

    def test_let_value_inlined_no_dangling_reference(self):
        """let VALUE is inlined into references so no dangling identifier remains.

        Removing the `let` alone would leave `== minTls` as an unresolved column,
        causing the re-query to fail forever.
        """
        query = (
            "let minTls = 'TLS1_0'; Resources "
            "| where tostring(properties.minimumTlsVersion) == minTls | project name"
        )
        result = sanitize_kql(query)
        assert "let " not in result
        assert "== 'TLS1_0'" in result
        assert "minTls" not in result  # variable fully inlined, no dangling ref

    def test_missing_pipe_before_project(self):
        """A missing pipe before 'project' is inserted."""
        query = "Resources | where type =~ 'microsoft.storage/storageaccounts' project name"
        result = sanitize_kql(query)
        assert "| project name" in result

    def test_missing_pipe_before_extend(self):
        """A missing pipe before 'extend' is inserted."""
        query = "Resources | where type =~ 'microsoft.web/sites' extend foo = tolower(name)"
        result = sanitize_kql(query)
        assert "| extend foo" in result
