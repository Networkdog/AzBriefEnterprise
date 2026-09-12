"""Tests for KQL retry logic, sanitize_kql, and ResourceGraphQueryFixer rule-based fallback."""

import asyncio
import json
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

    def test_unsupported_datatable_preserved_for_explicit_failure(self):
        query = "datatable(x:string) ['a','b'] | join (Resources) on x"
        assert sanitize_kql(query) == query

    def test_project_except_to_project_away(self):
        """project-except is converted to project-away."""
        query = "Resources | project-except id"
        result = sanitize_kql(query)
        assert "project-away" in result
        assert "project-except" not in result

    def test_kind_alias_preserved(self):
        query = "Resources | project name, kind=tostring(kind)"
        assert sanitize_kql(query) == query

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

    def test_attempt_1_preserves_kind_alias(self):
        query = "Resources | project name, kind=tostring(kind)"
        assert self.fixer._rule_based_fix(query, "ParserFailure", 1) == query

    def test_attempt_5_preserves_requested_projection(self):
        query = "Resources | where type =~ 'x' | project name, complexField=tostring(a.b.c)"
        assert self.fixer._rule_based_fix(query, "ParserFailure", 5) == query

    def test_attempt_8_does_not_replace_query_with_inventory(self):
        query = "Resources | where type =~ 'Microsoft.Network/routeTables' | complex stuff"
        assert self.fixer._rule_based_fix(query, "ParserFailure", 8) == query

    def test_builder_cannot_replace_update_specific_question(self):
        query = (
            "Resources | where type =~ 'Microsoft.Storage/storageAccounts' "
            "| where properties.minimumTlsVersion == 'TLS1_0' "
            "| project name, broken=tostring(a.b.c.d)"
        )
        assert self.fixer._rule_based_fix(query, "ParserFailure", 8) == query

    def test_attempt_11_cannot_turn_failure_into_unrelated_count(self):
        query = "completely broken"
        assert self.fixer._rule_based_fix(query, "Error", 11) == query

    def test_strip_markdown_fences(self):
        """Markdown code fences are stripped from LLM output."""
        assert (
            self.fixer._strip_markdown_fences("```kql\nResources | take 5\n```")
            == "Resources | take 5"
        )
        assert self.fixer._strip_markdown_fences("```\nResources\n```") == "Resources"
        assert self.fixer._strip_markdown_fences("Resources | take 5") == "Resources | take 5"

    def test_extracts_kql_from_strict_specialist_envelope(self):
        response = json.dumps(
            {
                "status": "ok",
                "claims": [
                    {
                        "id": "resource_graph-1",
                        "text": "Resources | where type =~ 'microsoft.storage/storageaccounts'",
                        "evidence": ["query:corrected-kql"],
                        "confidence": "high",
                    }
                ],
                "gaps": [],
            }
        )

        assert self.fixer._extract_kql_response(response).startswith("Resources | where")

    def test_specialist_gap_is_never_executed_as_kql(self):
        response = json.dumps(
            {
                "status": "partial",
                "claims": [],
                "gaps": ["The query intent is ambiguous"],
            }
        )

        assert self.fixer._extract_kql_response(response) == ""

    def test_column_reference_error_cannot_orphan_predicate(self):
        query = "Resources | extend badCol = tostring(x) | where badCol == 'y'"
        error_msg = "Failed to resolve scalar expression named 'badCol'"
        assert self.fixer._rule_based_fix(query, error_msg, 2) == query

    @pytest.mark.parametrize("attempt", [1, 3, 5, 8])
    @pytest.mark.parametrize(
        "query",
        [
            "Resources | where type =~ 'microsoft.network/virtualnetworks' "
            "| mv-expand subnet=properties.subnets limit 2000 "
            "| where subnet.properties.privateEndpointNetworkPolicies == 'Disabled' "
            "| project id, subnetName=tostring(subnet.name)",
            "Resources | where type =~ 'microsoft.compute/virtualmachines' "
            "| project id, owner=tolower(properties.managedBy) "
            "| join kind=leftouter (Resources | project owner=tolower(id), ownerName=name) "
            "on owner | project id, ownerName",
        ],
    )
    def test_relational_and_array_queries_never_degrade(self, query: str, attempt: int):
        assert self.fixer._rule_based_fix(query, "ParserFailure", attempt) == query


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

    def test_probe_keeps_nondefault_table_and_double_quoted_type(self):
        from src.agent.tools import _build_type_probe_query, _query_has_property_filter

        query = 'RecoveryServicesResources | where type == "microsoft.recoveryservices/items"'
        probe = _build_type_probe_query(query)
        assert probe is not None
        assert probe.startswith("RecoveryServicesResources")
        assert "subscriptionId" in probe
        assert "order by id asc" in probe
        assert not _query_has_property_filter(query)

    def test_relational_query_is_not_replaced_with_single_type_probe(self):
        from src.agent.tools import _build_type_probe_query

        query = (
            "Resources | where type =~ 'microsoft.compute/virtualmachines' "
            "| join (Resources | where type =~ 'microsoft.compute/disks') on id"
        )
        assert _build_type_probe_query(query) is None


class TestResultReviewLoop:
    @pytest.fixture(autouse=True)
    def isolate_fixer_state(self):
        with (
            patch("src.agent.tools._kql_fixer_circuit_breaker") as breaker,
            patch("src.agent.tools.record_successful_query"),
            patch("src.agent.tools.record_failed_query"),
        ):
            breaker.is_open = False
            yield

    @pytest.mark.asyncio
    async def test_missing_property_is_probed_rewritten_and_reexecuted(self):
        from src.agent.tools import execute_kql_with_retry

        original = (
            "Resources | where type =~ 'microsoft.storage/storageaccounts' "
            "| project id, tls=tostring(properties.tlsVersion)"
        )
        corrected = original.replace("properties.tlsVersion", "properties.minimumTlsVersion")
        service = MagicMock()
        service.query_resources = AsyncMock(
            side_effect=[
                {"data": [{"id": "account", "tls": None}], "count": 1},
                {"data": [{"id": "account", "properties": {"minimumTlsVersion": "TLS1_0"}}]},
                {"data": [{"id": "account", "tls": "TLS1_0"}], "count": 1},
            ]
        )
        fixer = MagicMock()
        fixer.improve_query_for_result = AsyncMock(return_value=corrected)
        with patch("src.agent.tools.get_query_fixer", return_value=fixer):
            result = await execute_kql_with_retry(service, original, expected_columns=["tls"])

        assert service.query_resources.call_count == 3
        assert "properties" in service.query_resources.call_args_list[1].args[0]
        assert service.query_resources.call_args_list[2].args[0] == corrected
        assert result["executed_query"] == corrected
        assert result["result_rewrites"] == 1
        assert result["query_status"] == "complete"
        assert "Required columns" in fixer.improve_query_for_result.call_args.kwargs["result_issue"]

    @pytest.mark.asyncio
    async def test_nonempty_off_topic_result_is_rewritten_using_purpose(self):
        from src.agent.tools import execute_kql_with_retry

        original = "Resources | project id, tls=tostring(properties.minimumTlsVersion)"
        corrected = original + " | where tls == 'TLS1_0'"
        purpose = "Find only accounts that still require TLS1_0 retirement remediation."
        service = MagicMock()
        service.query_resources = AsyncMock(
            side_effect=[
                {"data": [{"id": "modern", "tls": "TLS1_2"}], "count": 1},
                {"data": [{"id": "legacy", "tls": "TLS1_0"}], "count": 1},
            ]
        )
        fixer = MagicMock()
        fixer.improve_query_for_result = AsyncMock(side_effect=[corrected, corrected])
        with patch("src.agent.tools.get_query_fixer", return_value=fixer):
            result = await execute_kql_with_retry(
                service, original, purpose=purpose, expected_columns=["tls"]
            )

        assert service.query_resources.call_count == 2
        assert result["data"][0]["id"] == "legacy"
        assert result["query_status"] == "complete"
        assert fixer.improve_query_for_result.call_args.kwargs["original_query"] == original
        assert fixer.improve_query_for_result.call_args.kwargs["purpose"] == purpose

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", [False, 0])
    async def test_false_and_zero_are_valid_required_evidence(self, value: object):
        from src.agent.tools import execute_kql_with_retry

        service = MagicMock()
        service.query_resources = AsyncMock(return_value={"data": [{"setting": value}]})
        fixer = MagicMock()
        fixer.improve_query_for_result = AsyncMock()
        with patch("src.agent.tools.get_query_fixer", return_value=fixer):
            result = await execute_kql_with_retry(
                service,
                "Resources | project setting=properties.enabled",
                expected_columns=["setting"],
            )
        assert result["query_status"] == "complete"
        fixer.improve_query_for_result.assert_not_called()

    @pytest.mark.asyncio
    async def test_unresolved_required_property_is_partial_not_false(self):
        from src.agent.tools import execute_kql_with_retry

        service = MagicMock()
        service.query_resources = AsyncMock(return_value={"data": [{"setting": None}]})
        fixer = MagicMock()
        fixer.improve_query_for_result = AsyncMock(return_value=None)
        with patch("src.agent.tools.get_query_fixer", return_value=fixer):
            result = await execute_kql_with_retry(
                service,
                "Resources | project setting=properties.unknown",
                expected_columns=["setting"],
            )
        assert result["data"][0]["setting"] is None
        assert result["query_status"] == "partial"
        assert "Required columns" in result["evidence_gaps"][0]

    @pytest.mark.asyncio
    async def test_probe_cannot_be_returned_as_affected_resources(self):
        from src.agent.tools import execute_kql_with_retry

        service = MagicMock()
        service.query_resources = AsyncMock(
            side_effect=[
                {"data": [], "count": 0},
                {"data": [{"id": "modern", "properties": {"minimumTlsVersion": "TLS1_2"}}]},
            ]
        )
        fixer = MagicMock()
        fixer.improve_query_for_empty_result = AsyncMock(return_value=None)
        with patch("src.agent.tools.get_query_fixer", return_value=fixer):
            result = await execute_kql_with_retry(
                service,
                "Resources | where type =~ 'microsoft.storage/storageaccounts' "
                "| where properties.minimumTlsVersion == 'TLS1_0'",
            )
        assert result["data"] == []
        assert result["count"] == 0
        assert result["query_status"] == "partial"

    @pytest.mark.asyncio
    async def test_last_attempt_empty_result_returns_explicit_gap(self):
        from src.agent.tools import execute_kql_with_retry

        service = MagicMock()
        service.query_resources = AsyncMock(return_value={"data": [], "count": 0})
        with patch("src.agent.tools.get_query_fixer") as fixer:
            result = await execute_kql_with_retry(
                service,
                "Resources | where type =~ 'microsoft.storage/storageaccounts' "
                "| where properties.minimumTlsVersion == 'TLS1_0'",
                max_retries=1,
            )
        assert result["query_status"] == "partial"
        assert result["query_attempts"] == 1
        service.query_resources.assert_called_once()
        fixer.return_value.improve_query_for_empty_result.assert_not_called()

    @pytest.mark.asyncio
    async def test_syntax_rewrite_cycle_stops_before_repeating_query(self):
        from src.agent.tools import execute_kql_with_retry

        original = "Resources | project missing"
        revised = "Resources | project alsoMissing"
        service = MagicMock()
        service.query_resources = AsyncMock(side_effect=RuntimeError("InvalidQuery"))
        fixer = MagicMock()
        fixer.fix_query = AsyncMock(side_effect=[revised, original])
        with patch("src.agent.tools.get_query_fixer", return_value=fixer):
            with pytest.raises(RuntimeError, match="failed after 2 retries"):
                await execute_kql_with_retry(service, original)
        assert service.query_resources.call_count == 2

    @pytest.mark.asyncio
    async def test_semantic_rewrite_cycle_returns_last_result_with_gap(self):
        from src.agent.tools import execute_kql_with_retry

        original = "Resources | project id, setting=properties.first"
        revised = "Resources | project id, setting=properties.second"
        service = MagicMock()
        service.query_resources = AsyncMock(return_value={"data": [{"setting": "candidate"}]})
        fixer = MagicMock()
        fixer.improve_query_for_result = AsyncMock(side_effect=[revised, original])
        with patch("src.agent.tools.get_query_fixer", return_value=fixer):
            result = await execute_kql_with_retry(service, original, purpose="Check the setting.")
        assert service.query_resources.call_count == 2
        assert result["executed_query"] == revised
        assert result["query_status"] == "partial"
        assert any("repeated" in gap for gap in result["evidence_gaps"])

    @pytest.mark.asyncio
    async def test_scope_rejection_does_not_retry_or_rewrite(self):
        from src.agent.tools import execute_kql_with_retry

        service = MagicMock()
        service.query_resources = AsyncMock(side_effect=ValueError("outside the analysis scope"))
        with patch("src.agent.tools.get_query_fixer") as fixer:
            with pytest.raises(RuntimeError, match="outside the analysis scope"):
                await execute_kql_with_retry(service, "Resources | project id")
        service.query_resources.assert_called_once()
        fixer.return_value.fix_query.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status_code", [401, 403])
    async def test_permission_error_is_not_rewritten_or_retried(self, status_code: int):
        from azure.core.exceptions import HttpResponseError

        from src.agent.tools import execute_kql_with_retry

        error = HttpResponseError(message="AuthorizationFailed")
        error.status_code = status_code
        service = MagicMock()
        service.query_resources = AsyncMock(side_effect=error)
        with patch("src.agent.tools.get_query_fixer") as fixer:
            with pytest.raises(RuntimeError, match="failed after 1 retries"):
                await execute_kql_with_retry(service, "Resources | project id")
        service.query_resources.assert_called_once()
        fixer.return_value.fix_query.assert_not_called()

    @pytest.mark.asyncio
    async def test_tool_passes_result_requirements_to_loop(self):
        from src.agent.tools import ResourceGraphQueryTool

        service = MagicMock()
        tool = ResourceGraphQueryTool(service=service)
        with patch("src.agent.tools.execute_kql_with_retry", new_callable=AsyncMock) as execute:
            execute.return_value = {"data": [], "count": 0}
            await tool.ainvoke(
                {
                    "query": "Resources | project id",
                    "purpose": "Locate candidates",
                    "expected_columns": ["id"],
                }
            )
        execute.assert_awaited_once_with(
            service, "Resources | project id", purpose="Locate candidates", expected_columns=["id"]
        )

    @pytest.mark.asyncio
    async def test_empty_review_does_not_teach_relaxing_security_thresholds(self):
        specialist = MagicMock()
        query = "Resources | where properties.minimumTlsVersion == 'TLS1_0'"
        specialist.ainvoke = AsyncMock(return_value=MagicMock(content=query))
        fixer = ResourceGraphQueryFixer(llm=specialist)
        result = await fixer.improve_query_for_empty_result(
            query, '{"properties": {"minimumTlsVersion": "TLS1_2"}}'
        )
        assert result == query
        prompt = specialist.ainvoke.call_args.args[0][1].content
        assert "Zero rows can be the correct answer" in prompt
        assert "Never change TLS1_0 to TLS1_2" in prompt
        assert "therefore too strict" not in prompt


class TestKqlInvestigationGuidance:
    def test_cached_queries_do_not_define_allowed_query_shapes(self):
        from src.agent.kql_knowledge import build_context_for_prompt

        with patch(
            "src.agent.kql_knowledge._load",
            return_value={
                "schemas": {"test/type": {"paths": ["properties.oldField"]}},
                "queries": {},
                "failed_queries": [
                    {"query": "Resources | join (Resources) on id", "error": "ParserFailure"}
                ],
            },
        ):
            context = build_context_for_prompt()
        assert "not current tenant evidence" in context
        assert "not a ban on joins" in context
        assert "DO NOT REPEAT" not in context

    def test_persisted_runtime_guidance_matches_advanced_query_contract(self):
        from scripts.provision_foundry_agents import agent_instructions

        instructions = agent_instructions("resource_graph")
        assert "expected_columns" in instructions
        assert "Builder" not in instructions or "not limits" in instructions
        assert "no `join`" not in instructions
        assert "two result rewrites" in instructions
        assert "uncollected Azure pages" in instructions

    def test_plan_purpose_reaches_query_tool_without_replacing_custom_kql(self):
        from src.agent.analyzer import AnalysisTask, AzureUpdateAnalyzer
        from src.agent.tools import ResourceGraphQueryTool

        query = "ResourceChanges | project id, changed=todatetime(properties.changeAttributes.timestamp)"
        task = AnalysisTask(
            task_id="changed",
            description="Find configuration changes",
            method="kql",
            tool_name="query_azure_resources",
            tool_args={"query": query, "expected_columns": ["changed"]},
            purpose="Check the actual change timestamp for the update decision.",
        )
        AzureUpdateAnalyzer._fill_contextual_tool_args(
            task, ResourceGraphQueryTool(service=MagicMock()), {}
        )
        assert task.tool_args["query"] == query
        assert task.tool_args["expected_columns"] == ["changed"]
        assert task.tool_args["purpose"] == task.purpose

    def test_planning_and_specialist_allow_goal_directed_advanced_queries(self):
        from src.agent.foundry_backend import SPECIALIST_PROMPTS
        from src.agent.prompts.phases import PLANNING_PROMPT

        specialist = SPECIALIST_PROMPTS["resource_graph"].format(update_context="TLS retirement")
        for guidance in (PLANNING_PROMPT, specialist, ResourceGraphQueryFixer.SYSTEM_PROMPT):
            assert "no join" not in guidance.lower()
            assert "project expressions" in guidance or "project alias=expression" in guidance
        assert "FOLLOW THIS" not in PLANNING_PROMPT
        assert "expected_columns" in PLANNING_PROMPT
        assert "next native tool round" in specialist
        assert "Always add `| limit 200`" not in ResourceGraphQueryFixer.SYSTEM_PROMPT
        assert "reserved `kind=tostring(kind)`" in ResourceGraphQueryFixer.SYSTEM_PROMPT

    def test_evaluation_and_revision_require_query_intent_not_only_success(self):
        from src.agent.prompts.phases import EVALUATION_PROMPT, REVISE_TASKS_PROMPT

        evaluation = EVALUATION_PROMPT.format(
            update_context="update", task_results_summary="results"
        )
        revision = REVISE_TASKS_PROMPT.format(
            evaluation_result="partial", current_plan="plan", task_results_summary="results"
        )
        assert '"query_intent": true' in evaluation
        assert "query_intent: false" in evaluation
        assert "never received" in evaluation
        assert "expected_columns" in revision
        assert "Do not change thresholds just to produce rows" in revision


class TestSchemaExploration:
    @pytest.mark.asyncio
    async def test_live_samples_merge_nested_objects_arrays_and_false_values(self):
        from src.agent.tools import ExploreResourceSchemaTool

        service = MagicMock()
        service.query_resources = AsyncMock(
            return_value={
                "data": [
                    {"id": "first", "properties": {"provisioningState": "Succeeded"}},
                    {
                        "id": "second",
                        "properties": {
                            "storageProfile": {"fileCSIDriver": {"enabled": False}},
                            "agentPoolProfiles": [{"name": "pool"}, {"osSKU": "AzureLinux"}],
                        },
                    },
                ]
            }
        )
        with (
            patch("src.agent.tools.get_known_schema", return_value=["properties.oldPath"]),
            patch("src.agent.tools.record_schema") as record,
        ):
            output = await ExploreResourceSchemaTool(service=service)._arun(
                "Microsoft.ContainerService/managedClusters"
            )
        assert "properties.storageProfile.fileCSIDriver.enabled: false" in output
        assert "properties.agentPoolProfiles[].osSKU" in output
        assert "AzureLinux" in output
        assert "Historical path hints, not observed" in output
        assert "Not exhaustive" in output
        assert "order by id asc" in service.query_resources.call_args.args[0]
        assert "take 5" in service.query_resources.call_args.args[0]
        assert "properties.storageProfile.fileCSIDriver.enabled" in record.call_args.args[1]

    @pytest.mark.asyncio
    async def test_multiword_focus_matches_nested_path_without_second_query(self):
        from src.agent.tools import ExploreResourceSchemaTool

        service = MagicMock()
        service.query_resources = AsyncMock(
            return_value={
                "data": [{"id": "account", "properties": {"minimumTlsVersion": "TLS1_2"}}]
            }
        )
        with (
            patch("src.agent.tools.get_known_schema", return_value=[]),
            patch("src.agent.tools.record_schema"),
        ):
            output = await ExploreResourceSchemaTool(service=service)._arun(
                "Microsoft.Storage/storageAccounts", focus_area="TLS settings"
            )
        assert 'properties.minimumTlsVersion: "TLS1_2"' in output
        service.query_resources.assert_called_once()

    @pytest.mark.asyncio
    async def test_secondary_table_and_punctuated_property_names(self):
        from src.agent.tools import ExploreResourceSchemaTool

        service = MagicMock()
        service.query_resources = AsyncMock(
            return_value={"data": [{"id": "item", "properties": {"odata.type": "sample"}}]}
        )
        with (
            patch("src.agent.tools.get_known_schema", return_value=[]),
            patch("src.agent.tools.record_schema"),
        ):
            output = await ExploreResourceSchemaTool(service=service)._arun(
                "Microsoft.RecoveryServices/items", table="RecoveryServicesResources"
            )
        assert service.query_resources.call_args.args[0].startswith("RecoveryServicesResources")
        assert 'properties["odata.type"]' in output

    @pytest.mark.asyncio
    async def test_schema_cap_is_disclosed(self):
        from src.agent.tools import ExploreResourceSchemaTool

        service = MagicMock()
        service.query_resources = AsyncMock(
            return_value={"data": [{"id": "account", "properties": {"values": list(range(20))}}]}
        )
        with (
            patch("src.agent.tools.get_known_schema", return_value=[]),
            patch("src.agent.tools.record_schema"),
        ):
            output = await ExploreResourceSchemaTool(service=service)._arun(
                "Microsoft.Test/accounts"
            )
        assert "traversal_capped=true" in output
        assert "Missing paths are unknown" in output


class TestResourceGraphSpecialistBoundary:
    """The query fixer never crosses into another Prompt Agent specialty."""

    def test_standalone_fixer_disables_native_tools(self):
        fixer = ResourceGraphQueryFixer()
        with (
            patch("src.config.get_settings"),
            patch("src.agent.foundry_backend.create_foundry_chat_model") as create,
        ):
            model = fixer._get_llm()
        create.return_value.without_tools.assert_called_once_with()
        assert model is create.return_value.without_tools.return_value

    @pytest.mark.asyncio
    async def test_result_review_cancellation_propagates(self):
        specialist = MagicMock()
        specialist.ainvoke = AsyncMock(side_effect=asyncio.CancelledError())
        fixer = ResourceGraphQueryFixer(llm=specialist)
        with pytest.raises(asyncio.CancelledError):
            await fixer.improve_query_for_empty_result("Resources | where kind == 'legacy'", "[]")

    @pytest.mark.asyncio
    async def test_syntax_repair_cancellation_does_not_fall_back(self):
        fixer = ResourceGraphQueryFixer(llm=MagicMock())
        with (
            patch.object(
                fixer, "_search_docs_for_fix", new_callable=AsyncMock, return_value="docs"
            ),
            patch.object(
                fixer,
                "_llm_fix_query",
                new_callable=AsyncMock,
                side_effect=asyncio.CancelledError(),
            ),
            patch.object(fixer, "_rule_based_fix") as fallback,
        ):
            with pytest.raises(asyncio.CancelledError):
                await fixer.fix_query("Resources | broken", "ParserFailure", 1)
        fallback.assert_not_called()

    @pytest.mark.asyncio
    async def test_availability_error_is_not_sent_to_another_agent(self):
        from src.agent.tools import ResourceGraphQueryFixer

        specialist = MagicMock()
        specialist.ainvoke = AsyncMock(
            side_effect=Exception("Error code: 404 - {'code': 'DeploymentNotFound'}")
        )

        fixer = ResourceGraphQueryFixer(llm=specialist)
        with pytest.raises(Exception, match="DeploymentNotFound"):
            await fixer._ainvoke_specialist(["msg"])

        specialist.ainvoke.assert_called_once()

    @pytest.mark.asyncio
    async def test_transient_error_is_propagated(self):
        from src.agent.tools import ResourceGraphQueryFixer

        specialist = MagicMock()
        specialist.ainvoke = AsyncMock(side_effect=Exception("429 rate limit exceeded"))

        fixer = ResourceGraphQueryFixer(llm=specialist)
        with pytest.raises(Exception, match="429"):
            await fixer._ainvoke_specialist(["msg"])

    def test_is_availability_error(self):
        from src.agent.tools import ResourceGraphQueryFixer

        assert ResourceGraphQueryFixer._is_availability_error("404 DeploymentNotFound")
        assert ResourceGraphQueryFixer._is_availability_error(
            "The requested operation is unsupported"
        )
        assert not ResourceGraphQueryFixer._is_availability_error("429 rate limit")
