"""Tests for KQL retry logic, sanitize_kql, and ResourceGraphQueryFixer rule-based fallback."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.tools import (
    ResourceGraphQueryFixer,
    sanitize_kql,
)


class TestSanitizeKqlAdvanced:
    """Additional edge cases for sanitize_kql beyond test_kql_sanitize.py."""

    def test_multiple_let_statements_removed(self):
        """Multiple let statements are all removed."""
        query = "let x = 1;\nlet y = 2;\nResources | take 10"
        result = sanitize_kql(query)
        assert "let " not in result
        assert "Resources" in result

    def test_query_starting_with_pipe(self):
        """Query starting with | gets Resources prepended."""
        query = "| where type =~ 'microsoft.compute/virtualmachines'"
        result = sanitize_kql(query)
        assert result.startswith("Resources")

    def test_render_operator_removed(self):
        """render operator is stripped."""
        query = "Resources | summarize count() by type | render barchart"
        result = sanitize_kql(query)
        assert "render" not in result

    def test_datatable_removed(self):
        """datatable blocks are removed."""
        query = "datatable(x:string) ['a','b'] | join (Resources) on x"
        result = sanitize_kql(query)
        assert "datatable" not in result.lower()

    def test_project_except_to_project_away(self):
        """project-except is converted to project-away."""
        query = "Resources | project-except id"
        result = sanitize_kql(query)
        assert "project-away" in result
        assert "project-except" not in result

    def test_kind_alias_collision_fixed(self):
        """kind=tostring(kind) is fixed to kindValue=tostring(kind)."""
        query = "Resources | project name, kind=tostring(kind)"
        result = sanitize_kql(query)
        assert "kindValue=tostring(kind)" in result

    def test_duplicate_pipes_cleaned(self):
        """|| is cleaned to |."""
        query = "Resources || where type =~ 'x'"
        result = sanitize_kql(query)
        assert "||" not in result

    def test_trailing_semicolons_stripped(self):
        """Trailing semicolons are removed."""
        query = "Resources | take 10;"
        result = sanitize_kql(query)
        assert not result.endswith(";")

    def test_normal_query_unchanged(self):
        """A well-formed query passes through unchanged."""
        query = (
            "Resources | where type =~ 'Microsoft.Compute/virtualMachines' | project name, location"
        )
        result = sanitize_kql(query)
        assert result.strip() == query.strip()


class TestResourceGraphQueryFixerRuleBased:
    """Test the rule-based fallback in ResourceGraphQueryFixer."""

    def setup_method(self):
        self.fixer = ResourceGraphQueryFixer()

    def test_attempt_1_fixes_top_without_by(self):
        """Attempt 1: fixes '| top N' without ORDER BY."""
        query = "Resources | where type =~ 'x' | top 50"
        result = self.fixer._rule_based_fix(query, "ParserFailure", 1)
        assert "top 50" not in result or "by" in result
        assert "take 50" in result

    def test_attempt_1_fixes_kind_alias(self):
        """Attempt 1: fixes kind=tostring(kind) collision."""
        query = "Resources | project name, kind=tostring(kind)"
        result = self.fixer._rule_based_fix(query, "ParserFailure", 1)
        # The fix renames the alias to avoid reserved word collision
        assert "kind=tostring(kind)" not in result
        assert "kindValue" in result

    def test_attempt_5_simplifies_projection(self):
        """Attempt 5: simplifies projection to safe fields."""
        query = "Resources | where type =~ 'x' | project name, complexField=tostring(a.b.c)"
        result = self.fixer._rule_based_fix(query, "ParserFailure", 5)
        assert "name" in result
        assert "location" in result
        assert "complexField" not in result

    def test_attempt_8_builds_minimal_query(self):
        """Attempt 8: builds a minimal query from resource type (no builder available)."""
        # routeTables has no predefined builder → falls back to the minimal raw query.
        query = "Resources | where type =~ 'Microsoft.Network/routeTables' | complex stuff"
        result = self.fixer._rule_based_fix(query, "ParserFailure", 8)
        assert "Microsoft.Network/routeTables" in result
        assert "name" in result
        assert "limit 100" in result

    def test_builder_fallback_preserves_intent(self):
        """A failing custom query for a type WITH a builder falls back to that builder.

        This preserves domain projections (e.g. storage TLS / privateEndpoint /
        publicNetworkAccess) instead of degrading to a generic raw-properties dump —
        the #1 cause of degraded queries found in the 3-month KB audit.
        """
        query = (
            "Resources | where type =~ 'Microsoft.Storage/storageAccounts' "
            "| project name, broken=tostring(a.b.c.d)"
        )
        result = self.fixer._rule_based_fix(query, "ParserFailure", 8)
        # Should be the storage builder, not a generic 'properties | limit 100' dump
        assert "minimumTlsVersion" in result
        assert "publicNetworkAccess" in result
        assert "privateEndpoints" in result

    def test_attempt_11_fallback_count_query(self):
        """Attempt 11: ultimate fallback to simple count query."""
        query = "completely broken"
        result = self.fixer._rule_based_fix(query, "Error", 11)
        assert "summarize count() by type" in result

    def test_strip_markdown_fences(self):
        """Markdown code fences are stripped from LLM output."""
        assert (
            self.fixer._strip_markdown_fences("```kql\nResources | take 5\n```")
            == "Resources | take 5"
        )
        assert self.fixer._strip_markdown_fences("```\nResources\n```") == "Resources"
        assert self.fixer._strip_markdown_fences("Resources | take 5") == "Resources | take 5"

    def test_column_reference_error_fix(self):
        """Error about unresolved column triggers extend removal."""
        query = "Resources | extend badCol = tostring(x) | where badCol == 'y'"
        error_msg = "Failed to resolve scalar expression named 'badCol'"
        result = self.fixer._rule_based_fix(query, error_msg, 2)
        # The extend for badCol should be removed
        assert "badCol" not in result or "extend" not in result

    def test_kind_tostring_extend_not_orphaned(self):
        """Unidentifiable ParserFailure must not orphan a moved project alias.

        `project name, kind=tostring(kind)` is rewritten into an extend feeding the
        projection; the fallback branch must keep that extend so `kindValue` stays
        defined rather than becoming a dangling reference.
        """
        query = (
            "Resources | where type =~ 'microsoft.web/sites' " "| project name, kind=tostring(kind)"
        )
        result = self.fixer._rule_based_fix(query, "ParserFailure near 'kind'", 1)
        assert "kindValue" in result
        assert "extend kindValue=tostring(kind)" in result
        assert "project name, kindValue" in result

    def test_strip_unreferenced_extends_keeps_referenced(self):
        """Helper keeps extends whose alias is used downstream, drops the rest."""
        from src.agent.tools import _strip_unreferenced_extends

        query = "Resources | extend a=tostring(x) | extend b=tostring(y) " "| project name, a"
        result = _strip_unreferenced_extends(query)
        assert "extend a=tostring(x)" in result  # referenced in project → kept
        assert "extend b=tostring(y)" not in result  # unreferenced → stripped
        assert "project name, a" in result


class TestExecuteKqlWithRetry:
    """Test execute_kql_with_retry retry logic."""

    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self):
        """Successful query on first attempt returns result."""
        from src.agent.tools import execute_kql_with_retry

        mock_service = MagicMock()
        mock_service.query_resources = AsyncMock(
            return_value={"data": [{"name": "r1"}], "count": 1}
        )
        mock_service.enrich_subscription_names = MagicMock()

        result = await execute_kql_with_retry(mock_service, "Resources | take 1", max_retries=3)

        assert result["count"] == 1
        mock_service.query_resources.assert_called_once()

    @pytest.mark.asyncio
    async def test_retries_on_invalid_query(self):
        """InvalidQuery error triggers retry with fixed query."""
        from src.agent.tools import execute_kql_with_retry

        mock_service = MagicMock()
        # First call fails, second succeeds
        mock_service.query_resources = AsyncMock(
            side_effect=[
                Exception("InvalidQuery: column 'badCol' not found"),
                {"data": [], "count": 0},
            ]
        )
        mock_service.enrich_subscription_names = MagicMock()

        # Mock the query fixer to return a fixed query
        with patch("src.agent.tools.get_query_fixer") as mock_fixer_fn:
            mock_fixer = MagicMock()
            mock_fixer.fix_query = AsyncMock(return_value="Resources | take 10")
            mock_fixer_fn.return_value = mock_fixer

            result = await execute_kql_with_retry(
                mock_service, "Resources | where badCol", max_retries=3
            )

        assert result["count"] == 0
        assert mock_service.query_resources.call_count == 2

    @pytest.mark.asyncio
    async def test_exhausts_retries_raises_runtime_error(self):
        """All retries exhausted raises RuntimeError."""
        from src.agent.tools import execute_kql_with_retry

        mock_service = MagicMock()
        mock_service.query_resources = AsyncMock(
            side_effect=Exception("InvalidQuery: persistent error")
        )

        with patch("src.agent.tools.get_query_fixer") as mock_fixer_fn:
            mock_fixer = MagicMock()
            mock_fixer.fix_query = AsyncMock(return_value="Resources | take 5")
            mock_fixer_fn.return_value = mock_fixer

            with pytest.raises(RuntimeError, match="failed after 2 retries"):
                await execute_kql_with_retry(mock_service, "bad query", max_retries=2)

    @pytest.mark.asyncio
    async def test_non_query_error_waits_and_retries(self):
        """Non-InvalidQuery errors wait and retry without fixing."""
        from src.agent.tools import execute_kql_with_retry

        mock_service = MagicMock()
        mock_service.query_resources = AsyncMock(
            side_effect=[
                Exception("Network timeout"),
                {"data": [{"name": "r1"}], "count": 1},
            ]
        )
        mock_service.enrich_subscription_names = MagicMock()

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await execute_kql_with_retry(mock_service, "Resources | take 1", max_retries=3)

        assert result["count"] == 1
        assert mock_service.query_resources.call_count == 2

    @pytest.mark.asyncio
    async def test_result_improvement_on_empty_filtered_result(self):
        """Empty result from a property-filtered query triggers a result-driven fix.

        Flow: original filtered query → empty; probe confirms the type exists;
        the fixer proposes an improved filter; the improved query returns rows.
        """
        from src.agent.tools import execute_kql_with_retry

        mock_service = MagicMock()
        mock_service.query_resources = AsyncMock(
            side_effect=[
                {"data": [], "count": 0},  # 1) original filtered query → empty
                {
                    "data": [{"name": "a1", "kind": "BlobStorage"}],
                    "count": 1,
                },  # 2) probe → type exists
                {"data": [{"name": "a1"}], "count": 1},  # 3) improved query → found
            ]
        )
        mock_service.enrich_subscription_names = MagicMock()

        with (
            patch("src.agent.tools.get_query_fixer") as mock_fixer_fn,
            patch("src.agent.tools.record_successful_query") as mock_record,
        ):
            mock_fixer = MagicMock()
            mock_fixer.improve_query_for_empty_result = AsyncMock(
                return_value=(
                    "Resources | where type =~ 'microsoft.storage/storageaccounts' "
                    "and kind =~ 'BlobStorage'"
                )
            )
            mock_fixer_fn.return_value = mock_fixer

            result = await execute_kql_with_retry(
                mock_service,
                "Resources | where type =~ 'microsoft.storage/storageaccounts' and kind == 'Storage'",
                max_retries=4,
            )

        assert result["count"] == 1
        assert mock_service.query_resources.call_count == 3  # original + probe + improved
        mock_fixer.improve_query_for_empty_result.assert_called_once()
        # The learned improvement is persisted for future reuse
        mock_record.assert_called_once()
        assert "Result-improved" in mock_record.call_args.kwargs["purpose"]

    @pytest.mark.asyncio
    async def test_no_improvement_when_type_absent(self):
        """Empty filtered result + empty probe (type absent) → accept empty, no LLM call."""
        from src.agent.tools import execute_kql_with_retry

        mock_service = MagicMock()
        mock_service.query_resources = AsyncMock(
            side_effect=[
                {"data": [], "count": 0},  # filtered → empty
                {"data": [], "count": 0},  # probe → also empty (type genuinely absent)
            ]
        )
        mock_service.enrich_subscription_names = MagicMock()

        with patch("src.agent.tools.get_query_fixer") as mock_fixer_fn:
            mock_fixer = MagicMock()
            mock_fixer.improve_query_for_empty_result = AsyncMock()
            mock_fixer_fn.return_value = mock_fixer

            result = await execute_kql_with_retry(
                mock_service,
                "Resources | where type =~ 'microsoft.storage/storageaccounts' and kind == 'Storage'",
                max_retries=4,
            )

        assert result["count"] == 0
        assert mock_service.query_resources.call_count == 2  # original + probe only
        mock_fixer.improve_query_for_empty_result.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_improvement_for_bare_type_only_empty(self):
        """A bare type-only query returning empty is accepted (no probe, no LLM)."""
        from src.agent.tools import execute_kql_with_retry

        mock_service = MagicMock()
        mock_service.query_resources = AsyncMock(return_value={"data": [], "count": 0})
        mock_service.enrich_subscription_names = MagicMock()

        with patch("src.agent.tools.get_query_fixer") as mock_fixer_fn:
            mock_fixer = MagicMock()
            mock_fixer.improve_query_for_empty_result = AsyncMock()
            mock_fixer_fn.return_value = mock_fixer

            result = await execute_kql_with_retry(
                mock_service,
                "Resources | where type =~ 'microsoft.storage/storageaccounts'",
                max_retries=4,
            )

        assert result["count"] == 0
        mock_service.query_resources.assert_called_once()  # no probe
        mock_fixer.improve_query_for_empty_result.assert_not_called()


class TestResultImprovementHelpers:
    """Unit tests for result-driven improvement helper functions."""

    def test_query_has_property_filter_true(self):
        from src.agent.tools import _query_has_property_filter

        assert _query_has_property_filter("Resources | where type =~ 'x' and kind == 'Storage'")
        assert _query_has_property_filter(
            "Resources | where type =~ 'x' | where properties.minimumTlsVersion == 'TLS1_0'"
        )

    def test_query_has_property_filter_false(self):
        from src.agent.tools import _query_has_property_filter

        assert not _query_has_property_filter(
            "Resources | where type =~ 'microsoft.storage/storageaccounts'"
        )
        assert not _query_has_property_filter(
            "Resources | where type =~ 'x' | project name, location"
        )

    def test_build_type_probe_query(self):
        from src.agent.tools import _build_type_probe_query

        q = _build_type_probe_query(
            "Resources | where type =~ 'Microsoft.Storage/storageAccounts' and kind == 'Storage'"
        )
        assert q is not None
        assert "Microsoft.Storage/storageAccounts" in q
        assert "limit 5" in q

    def test_build_type_probe_query_no_type(self):
        from src.agent.tools import _build_type_probe_query

        assert _build_type_probe_query("Resources | project name") is None


class TestLlmFallback:
    """Tests for codex -> primary LLM fallback in the query fixer."""

    @pytest.mark.asyncio
    async def test_falls_back_to_primary_on_availability_error(self):
        """A codex availability error (404 DeploymentNotFound) retries with primary."""
        from src.agent.tools import ResourceGraphQueryFixer

        codex = MagicMock()
        codex.ainvoke = AsyncMock(
            side_effect=Exception("Error code: 404 - {'code': 'DeploymentNotFound'}")
        )
        primary = MagicMock()
        primary.ainvoke = AsyncMock(return_value=MagicMock(content="Resources | take 1"))

        fixer = ResourceGraphQueryFixer(llm=codex, fallback_llm=primary)
        result = await fixer._ainvoke_with_fallback(["msg"])

        assert result.content == "Resources | take 1"
        codex.ainvoke.assert_called_once()
        primary.ainvoke.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_fallback_on_transient_error(self):
        """A transient (non-availability) error is not retried with the fallback."""
        from src.agent.tools import ResourceGraphQueryFixer

        codex = MagicMock()
        codex.ainvoke = AsyncMock(side_effect=Exception("429 rate limit exceeded"))
        primary = MagicMock()
        primary.ainvoke = AsyncMock()

        fixer = ResourceGraphQueryFixer(llm=codex, fallback_llm=primary)
        with pytest.raises(Exception, match="429"):
            await fixer._ainvoke_with_fallback(["msg"])
        primary.ainvoke.assert_not_called()

    def test_is_availability_error(self):
        from src.agent.tools import ResourceGraphQueryFixer

        assert ResourceGraphQueryFixer._is_availability_error("404 DeploymentNotFound")
        assert ResourceGraphQueryFixer._is_availability_error(
            "The requested operation is unsupported"
        )
        assert not ResourceGraphQueryFixer._is_availability_error("429 rate limit")
