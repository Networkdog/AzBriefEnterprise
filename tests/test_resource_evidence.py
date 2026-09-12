"""Deterministic resource counts, request isolation, and scope-preserving query links."""

import asyncio
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
        {"executed_query": "Resources | where name != '" + "x" * MAX_PORTAL_QUERY_URL_LENGTH + "' | project id"},
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