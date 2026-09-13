"""Deterministic resource counts, request isolation, and scope-preserving query links."""

import asyncio
import json
from urllib.parse import unquote

import pytest

from src.agent.resource_evidence import (
    MAX_PORTAL_QUERY_URL_LENGTH,
    register_resource_query,
    resolve_resource_queries,
    resource_evidence_context,
)

SUBSCRIPTION = "11111111-1111-1111-1111-111111111111"


def query_result(count: int = 327, **changes) -> dict:
    result = {
        "data": [
            {
                "id": f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-a/providers/Microsoft.Storage/storageAccounts/account{index}",
                "minimumTlsVersion": "TLS1_0",
            }
            for index in range(count)
        ],
        "total_records": count,
        "executed_query": "Resources | where type =~ 'microsoft.storage/storageaccounts' | where properties.minimumTlsVersion == 'TLS1_0' | project id | order by id asc",
        "query_scope": {
            "subscriptions": [SUBSCRIPTION],
            "management_groups": [],
            "resource_groups": [],
        },
        "query_status": "complete",
        "result_truncated": False,
        "queried_at": "2026-09-12T09:00:00+00:00",
    }
    return {**result, **changes}


def test_large_result_is_rehydrated_from_reference_not_model_rows():
    with resource_evidence_context():
        result = register_resource_query(query_result())
        rows, evidence = resolve_resource_queries(
            [{"reference": result["resource_query_ref"], "reason": "Legacy TLS configuration"}], []
        )
    assert len(rows) == evidence[0].count == 327
    assert rows[-1]["name"] == "account326"
    assert rows[-1]["subscriptionId"] == SUBSCRIPTION
    assert evidence[0].complete
    url = evidence[0].portal_url()
    query = unquote(url.split("/query/", 1)[1])
    assert f"subscriptionId in~ ('{SUBSCRIPTION}')" in query
    assert "properties.minimumTlsVersion == 'TLS1_0'" in query
    assert "account326" not in query


def test_tool_result_reference_round_trips_through_report_parser_and_wire():
    from src.agent.analyzer import AnalysisResult, AzureUpdateAnalyzer
    from src.agent.resource_evidence import resource_query_prompt_context
    from src.agent.tools import format_rg_result
    from tests.test_analyzer_parsing import _make_state, _make_update

    with resource_evidence_context():
        text = format_rg_result(query_result(), "Resource Graph query")
        catalog = resource_query_prompt_context()
        entry = json.loads(catalog.splitlines()[-1])
        assert entry["reference"] in text.splitlines()[0]
        state = _make_state(
            json.dumps(
                {
                    "relevance": "relevant",
                    "affected_resources": [],
                    "resource_queries": [
                        {"reference": entry["reference"], "reason": "Legacy TLS configuration"}
                    ],
                }
            )
        )
        analyzer = object.__new__(AzureUpdateAnalyzer)
        result = analyzer._parse_analysis_result(state, _make_update())
        analyzer._attach_result_evidence(result, "", {}, "")

    restored = AnalysisResult.model_validate_json(result.model_dump_json())
    assert len(restored.affected_resources) == 327
    assert restored.resource_queries[0].count == 327
    assert restored.resource_queries[0].portal_url()
    judge_evidence = analyzer.build_evidence_context(result)
    assert '"unique_resource_count": 327' in judge_evidence
    assert "minimumTlsVersion" in judge_evidence
    assert "Legacy TLS configuration" not in judge_evidence


def test_narrower_subscriber_scope_drops_broader_query_metadata():
    from src.agent.analyzer import AzureUpdateAnalyzer
    from src.agent.scope import AnalysisScope
    from tests.test_analyzer_parsing import _parse

    with resource_evidence_context():
        result = register_resource_query(query_result(2))
        report = _parse(
            json.dumps(
                {
                    "resource_queries": [
                        {"reference": result["resource_query_ref"], "reason": "Reason"}
                    ]
                }
            )
        )
    narrowed = AzureUpdateAnalyzer._filter_result_to_scope(
        report, AnalysisScope(subscriptions=[SUBSCRIPTION], resource_groups=["rg-a"])
    )
    assert len(narrowed.affected_resources) == 2
    assert not narrowed.resource_queries
    assert all(not row["query_refs"] for row in narrowed.affected_resources)


def test_customization_translates_reasons_without_changing_count_scope_or_identities():
    from types import SimpleNamespace

    from src.agent.analyzer import AzureUpdateAnalyzer
    from tests.test_analyzer_parsing import _make_update, _parse

    with resource_evidence_context():
        result = register_resource_query(query_result())
        report = _parse(
            json.dumps(
                {
                    "resource_queries": [
                        {"reference": result["resource_query_ref"], "reason": "Reason"}
                    ]
                }
            )
        )
    analyzer = object.__new__(AzureUpdateAnalyzer)
    analyzer.settings = SimpleNamespace(action_verification_enabled=False)
    customized = {
        "resource_queries": [
            {"reference": result["resource_query_ref"], "reason": "Translated reason"}
        ],
        "affected_resources": [{"name": "invented resource"}],
    }
    translated = analyzer._build_customized_result(report, customized, _make_update(), "en")
    assert len(translated.affected_resources) == 327
    assert {row["reason"] for row in translated.affected_resources} == {"Translated reason"}
    assert translated.resource_queries[0].count == 327
    assert translated.resource_queries[0].portal_url() == report.resource_queries[0].portal_url()
    assert {row["id"] for row in translated.affected_resources} == {
        row["id"] for row in report.affected_resources
    }
    with pytest.raises(ValueError, match="preserve every"):
        analyzer._build_customized_result(report, {"resource_queries": []}, _make_update(), "en")


def test_report_and_subscriber_prompts_preserve_query_reference_contract():
    from src.agent.prompts import SUBSCRIBER_CUSTOMIZATION_PROMPT, build_report_prompt

    prompt = build_report_prompt(
        category="retirement",
        update_context="",
        resource_summary="",
        task_results_summary="",
        report_language="ko",
    )
    assert '"resource_queries": []' in prompt
    assert "more than 20 matching resources" in prompt
    assert "EVERY returned resource" in prompt
    assert "Overlapping query counts must not be summed" in prompt
    assert "Preserve every `reference` exactly" in SUBSCRIBER_CUSTOMIZATION_PROMPT


def test_overlapping_queries_and_model_rows_do_not_double_count():
    with resource_evidence_context():
        first = register_resource_query(query_result(2))
        second = register_resource_query(query_result(3))
        rows, evidence = resolve_resource_queries(
            [
                {"reference": first["resource_query_ref"], "reason": "First reason"},
                {"reference": second["resource_query_ref"], "reason": "Second reason"},
            ],
            [{**query_result(1)["data"][0], "reason": "Repeated model row"}],
        )
    assert len(rows) == 3
    assert [item.count for item in evidence] == [2, 3]
    assert rows[0]["reason"] == "First reason\nSecond reason"
    assert len(rows[0]["query_refs"]) == 2


def test_nested_resource_names_preserve_parent_identity_in_archive_projection():
    from src.archive.models import ArchiveAffectedResourceV1

    data = [
        {
            "id": f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-a/providers/Microsoft.Sql/servers/{parent}/databases/shared",
            "name": "shared",
            "type": "Microsoft.Sql/servers/databases",
        }
        for parent in ("server-a", "server-b")
    ]
    with resource_evidence_context():
        result = register_resource_query(
            query_result(
                2,
                data=data,
                executed_query="Resources | where type =~ 'Microsoft.Sql/servers/databases' | project id, name, type",
            )
        )
        rows, evidence = resolve_resource_queries(
            [{"reference": result["resource_query_ref"], "reason": "Same applicability"}], []
        )
    archived = [
        ArchiveAffectedResourceV1.model_validate(
            {key: value for key, value in row.items() if key not in {"id", "query_refs"}}
        )
        for row in rows
    ]
    assert evidence[0].count == 2
    assert [row.name for row in archived] == ["server-a/shared", "server-b/shared"]


@pytest.mark.parametrize(
    "changes",
    [
        {"result_truncated": True, "total_records": 900},
        {"query_status": "partial"},
        {"executed_query": "Resources | project id | take 3"},
        {"data": [*query_result(2)["data"], {"name": "missing-id"}]},
    ],
)
def test_partial_results_never_claim_a_complete_count_or_link(changes):
    with resource_evidence_context():
        result = register_resource_query(query_result(3, **changes))
        rows, evidence = resolve_resource_queries(
            [{"reference": result["resource_query_ref"], "reason": "Confirmed candidates"}], []
        )
    assert rows
    assert not result["resource_count_complete"]
    assert not evidence[0].complete
    assert not evidence[0].portal_url()


@pytest.mark.parametrize(
    "changes",
    [
        {"query_scope": {"management_groups": ["production"], "subscriptions": []}},
        {"executed_query": "Resources | join (ResourceContainers) on subscriptionId | project id"},
        {
            "executed_query": "Resources | where name != '"
            + "x" * MAX_PORTAL_QUERY_URL_LENGTH
            + "' | project id"
        },
    ],
)
def test_unreproducible_or_long_queries_have_no_portal_link(changes):
    with resource_evidence_context():
        result = register_resource_query(query_result(3, **changes))
        rows, evidence = resolve_resource_queries(
            [{"reference": result["resource_query_ref"], "reason": "Verified reason"}], []
        )
    assert len(rows) == 3
    assert evidence[0].complete
    assert not evidence[0].portal_url()


def test_invalid_reference_fails_closed_and_context_is_cleared():
    with resource_evidence_context():
        result = register_resource_query(query_result(1))
        with pytest.raises(ValueError, match="Unknown resource query"):
            resolve_resource_queries([{"reference": "invented", "reason": "Reason"}], [])
    with pytest.raises(ValueError, match="current analysis"):
        resolve_resource_queries(
            [{"reference": result["resource_query_ref"], "reason": "Reason"}], []
        )


@pytest.mark.asyncio
async def test_concurrent_analyses_do_not_share_resource_references():
    async def analyze(count: int):
        with resource_evidence_context():
            result = register_resource_query(query_result(count))
            await asyncio.sleep(0)
            rows, _ = resolve_resource_queries(
                [{"reference": result["resource_query_ref"], "reason": "Reason"}], []
            )
            return len(rows), result["resource_query_ref"]

    first, second = await asyncio.gather(analyze(2), analyze(5))
    assert first[0] == 2
    assert second[0] == 5
    assert first[1] != second[1]
