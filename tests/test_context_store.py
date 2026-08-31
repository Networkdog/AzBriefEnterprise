"""Tests for the addressable tool result store and its sub-query tool.

Covers the context-recursion path that replaced silent truncation: full
results are retained, previews carry a queryable ref, and evidence past the
prompt budget stays reachable instead of being dropped.
"""

import asyncio
import json

import pytest

import src.agent.context_store as context_store
from src.agent.context_store import (
    ToolResultStore,
    build_result_handle,
    get_result_store,
    store_and_handle,
)
from src.agent.resilience import TOOL_RESULT_BUDGET_CHARS
from src.agent.tools import (
    FindRelatedResourcesTool,
    GetServiceRegionAvailabilityTool,
    QueryToolResultInput,
    QueryToolResultTool,
    format_rg_result,
)


def _rows(n: int, prefix: str = "stprod") -> list[dict]:
    return [
        {
            "name": f"{prefix}{i:04d}",
            "type": "microsoft.storage/storageaccounts",
            "location": "koreacentral",
            "resourceGroup": f"rg-{i % 5}",
            "subscriptionId": "sub-a",
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# ToolResultStore
# ---------------------------------------------------------------------------


class TestToolResultStore:
    def test_put_and_get_roundtrip(self):
        store = ToolResultStore()
        entry = store.put(tool="t", result="line1\nline2", trace_id="tr")
        assert store.get(entry.ref).content == "line1\nline2"
        assert entry.line_count == 2

    def test_refs_are_unique(self):
        store = ToolResultStore()
        refs = {store.put(tool="t", result=str(i)).ref for i in range(50)}
        assert len(refs) == 50

    def test_get_unknown_ref_returns_none(self):
        assert ToolResultStore().get("R999") is None

    def test_oversized_entry_is_capped(self):
        store = ToolResultStore(max_entry_chars=100)
        entry = store.put(tool="t", result="x" * 5000)
        assert entry.char_count < 200
        assert "exceeded store capacity" in entry.content

    def test_capped_entry_records_its_original_size(self):
        store = ToolResultStore(max_entry_chars=100)
        entry = store.put(tool="t", result="x" * 5000)
        assert entry.original_chars == 5000
        assert entry.is_partial

    def test_intact_entry_is_not_partial(self):
        entry = ToolResultStore().put(tool="t", result="small")
        assert not entry.is_partial

    def test_eviction_keeps_total_bounded(self):
        store = ToolResultStore(max_entry_chars=1000, max_total_chars=2000)
        for _ in range(10):
            store.put(tool="t", result="y" * 900)
        assert store.total_chars <= 2000 + 900

    def test_clear_trace_only_drops_that_trace(self):
        store = ToolResultStore()
        a = store.put(tool="t", result="a", trace_id="tr-1")
        b = store.put(tool="t", result="b", trace_id="tr-2")
        assert store.clear_trace("tr-1") == 1
        assert store.get(a.ref) is None
        assert store.get(b.ref) is not None

    def test_clear_trace_without_id_is_a_noop(self):
        store = ToolResultStore()
        store.put(tool="t", result="a", trace_id="tr-1")
        assert store.clear_trace("") == 0
        assert len(store) == 1

    def test_get_result_store_is_a_singleton(self):
        assert get_result_store() is get_result_store()


# ---------------------------------------------------------------------------
# build_result_handle / store_and_handle
# ---------------------------------------------------------------------------


class TestResultHandle:
    def test_small_result_passes_through_unchanged(self):
        assert store_and_handle(tool="t", result="short") == "short"

    def test_small_result_is_not_stored(self):
        before = len(get_result_store())
        store_and_handle(tool="t", result="short")
        assert len(get_result_store()) == before

    def test_oversized_result_gets_a_ref(self):
        handle = store_and_handle(tool="t", result="row\n" * 5000, trace_id="tr-handle")
        assert "[ref=R" in handle
        assert "query_tool_result" in handle
        get_result_store().clear_trace("tr-handle")

    def test_handle_warns_against_inferring_absence(self):
        handle = store_and_handle(tool="t", result="row\n" * 5000, trace_id="tr-warn")
        assert "Do NOT conclude that a resource is absent" in handle
        get_result_store().clear_trace("tr-warn")

    def test_handle_preview_ends_on_a_line_boundary(self):
        store = ToolResultStore()
        entry = store.put(tool="t", result="\n".join(f"row-{i:05d}-payload" for i in range(2000)))
        preview = build_result_handle(entry).split("\n... [TRUNCATED")[0]
        assert preview.splitlines()[-1].endswith("-payload")

    def test_handle_reports_the_full_size(self):
        store = ToolResultStore()
        entry = store.put(tool="t", result="x" * 20000)
        handle = build_result_handle(entry)
        assert f"of {20000:,} chars" in handle
        assert len(handle) < 20000


# ---------------------------------------------------------------------------
# QueryToolResultTool
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_store(monkeypatch):
    store = ToolResultStore()
    monkeypatch.setattr(context_store, "_store", store)
    return store


class TestQueryToolResultTool:
    def test_ref_must_be_a_stored_result_handle(self):
        assert QueryToolResultInput(ref="R7").ref == "R7"
        with pytest.raises(ValueError):
            QueryToolResultInput(ref="azure_mcp-2")

    def _stored(self, store, n=2000, needle="needle-resource-9999"):
        body = "\n".join(f"row-{i:05d}" for i in range(n)) + f"\n{needle}"
        return store.put(tool="query_azure_resources", result=body)

    def test_finds_a_line_past_the_preview_cutoff(self, isolated_store):
        entry = self._stored(isolated_store)
        assert "needle-resource-9999" not in build_result_handle(entry)

        out = asyncio.run(
            QueryToolResultTool()._arun(ref=entry.ref, pattern="needle-resource-9999")
        )
        assert "needle-resource-9999" in out

    def test_search_is_case_insensitive(self, isolated_store):
        entry = self._stored(isolated_store, needle="MyStorageAcct")
        out = asyncio.run(QueryToolResultTool()._arun(ref=entry.ref, pattern="mystorageacct"))
        assert "MyStorageAcct" in out

    def test_no_match_is_reported_as_a_confirmed_absence(self, isolated_store):
        entry = self._stored(isolated_store)
        out = asyncio.run(QueryToolResultTool()._arun(ref=entry.ref, pattern="does-not-exist"))
        assert "confirmed absence" in out

    def test_unknown_ref_explains_itself(self, isolated_store):
        out = asyncio.run(QueryToolResultTool()._arun(ref="R404", pattern="x"))
        assert "No stored result" in out

    def test_capped_entry_never_claims_a_confirmed_absence(self, monkeypatch):
        store = ToolResultStore(max_entry_chars=500)
        monkeypatch.setattr(context_store, "_store", store)
        entry = store.put(tool="t", result="\n".join(f"row-{i:05d}" for i in range(5000)))
        assert entry.is_partial

        out = asyncio.run(QueryToolResultTool()._arun(ref=entry.ref, pattern="row-04999"))
        assert "confirmed absence" not in out
        assert "NOT confirmed" in out
        assert "entry was capped" in out

    def test_tail_mode_returns_the_end(self, isolated_store):
        entry = self._stored(isolated_store)
        out = asyncio.run(QueryToolResultTool()._arun(ref=entry.ref, mode="tail", max_matches=3))
        assert "needle-resource-9999" in out

    def test_stats_mode_samples_without_a_pattern(self, isolated_store):
        entry = self._stored(isolated_store)
        out = asyncio.run(QueryToolResultTool()._arun(ref=entry.ref, mode="stats"))
        assert "evenly spaced sample" in out

    def test_regex_mode_matches(self, isolated_store):
        entry = self._stored(isolated_store)
        out = asyncio.run(
            QueryToolResultTool()._arun(ref=entry.ref, pattern=r"row-0000[12]$", regex=True)
        )
        assert "row-00001" in out and "row-00002" in out

    def test_invalid_regex_does_not_raise(self, isolated_store):
        entry = self._stored(isolated_store)
        out = asyncio.run(QueryToolResultTool()._arun(ref=entry.ref, pattern="[bad", regex=True))
        assert "Invalid regex" in out

    def test_overlong_regex_is_rejected(self, isolated_store):
        entry = self._stored(isolated_store)
        out = asyncio.run(QueryToolResultTool()._arun(ref=entry.ref, pattern="a" * 300, regex=True))
        assert "exceeds" in out

    def test_match_count_is_capped(self, isolated_store):
        entry = self._stored(isolated_store)
        out = asyncio.run(QueryToolResultTool()._arun(ref=entry.ref, pattern="row-", max_matches=5))
        assert "stopped at 5 matches" in out

    def test_result_stays_within_the_prompt_budget(self, isolated_store):
        entry = self._stored(isolated_store, n=20000)
        out = asyncio.run(
            QueryToolResultTool()._arun(ref=entry.ref, pattern="row-", max_matches=200)
        )
        assert len(out) <= TOOL_RESULT_BUDGET_CHARS + 100


class TestQueryToolResultOnBlob:
    """A serialized dict arrives as one enormous line, not as rows."""

    @staticmethod
    def _blob(store, needle="needle-acct-871"):
        body = str({"data": [{"name": f"res{i:05d}"} for i in range(3000)] + [{"name": needle}]})
        return store.put(tool="query_azure_resources", result=body)

    def test_match_is_returned_as_a_window_not_the_whole_line(self, isolated_store):
        entry = self._blob(isolated_store)
        out = asyncio.run(QueryToolResultTool()._arun(ref=entry.ref, pattern="needle-acct-871"))
        assert "needle-acct-871" in out
        assert len(out) < 1000
        assert "@" in out.splitlines()[2]

    def test_head_slices_characters_on_a_blob(self, isolated_store):
        entry = self._blob(isolated_store)
        out = asyncio.run(QueryToolResultTool()._arun(ref=entry.ref, mode="head", max_matches=5))
        assert "chars]" in out
        assert len(out) < 1000

    def test_stats_samples_character_windows_on_a_blob(self, isolated_store):
        entry = self._blob(isolated_store)
        out = asyncio.run(QueryToolResultTool()._arun(ref=entry.ref, mode="stats"))
        assert "character windows" in out

    def test_absence_on_a_blob_is_still_confirmed(self, isolated_store):
        entry = self._blob(isolated_store)
        out = asyncio.run(QueryToolResultTool()._arun(ref=entry.ref, pattern="zzz-absent"))
        assert "confirmed absence" in out


class TestFormatRgResult:
    def test_one_row_per_line(self):
        out = format_rg_result({"data": _rows(3), "count": 3, "total_records": 3}, "Q")
        assert len(out.splitlines()) == 4
        assert out.splitlines()[0].startswith("Q: 3 rows")

    def test_rows_are_valid_json(self):
        out = format_rg_result({"data": _rows(2)}, "Q")
        parsed = [json.loads(ln) for ln in out.splitlines()[1:]]
        assert parsed[0]["name"] == "stprod0000"

    def test_truncation_cuts_between_rows(self):
        out = format_rg_result({"data": _rows(500)}, "Q")
        preview = out[:TOOL_RESULT_BUDGET_CHARS]
        json.loads(preview.splitlines()[-2])

    def test_empty_result_is_explicit(self):
        assert "No matching resources" in format_rg_result({"data": [], "count": 0}, "Q")

    def test_non_dict_passes_through(self):
        assert format_rg_result("boom", "Q") == "boom"

    def test_non_serializable_values_do_not_raise(self):
        out = format_rg_result({"data": [{"name": "x", "when": object()}]}, "Q")
        assert "x" in out


# ---------------------------------------------------------------------------
# Tool formatters that used to overflow the budget
# ---------------------------------------------------------------------------


class TestFindRelatedResourcesFormat:
    def test_sixty_resources_fit_in_the_budget(self):
        out = FindRelatedResourcesTool._format_related(["storage"], {"data": _rows(60)})
        assert len(out) < TOOL_RESULT_BUDGET_CHARS
        assert "stprod0059" in out

    def test_summary_line_leads(self):
        out = FindRelatedResourcesTool._format_related(["storage"], {"data": _rows(3)})
        assert out.splitlines()[0].startswith("Found 3 resources")

    def test_type_distribution_survives_the_preview_cut(self):
        rows = _rows(400) + [
            {
                "name": f"vm{i}",
                "type": "microsoft.compute/virtualmachines",
                "location": "koreacentral",
                "resourceGroup": "rg",
                "subscriptionId": "sub-a",
            }
            for i in range(7)
        ]
        out = FindRelatedResourcesTool._format_related(["x"], {"data": rows})
        assert len(out) > TOOL_RESULT_BUDGET_CHARS
        preview = out[:TOOL_RESULT_BUDGET_CHARS]
        assert "microsoft.storage/storageaccounts: 400" in preview
        assert "microsoft.compute/virtualmachines: 7" in preview

    def test_empty_result_is_explicit(self):
        out = FindRelatedResourcesTool._format_related(["batch"], {"data": []})
        assert "No resources found" in out

    def test_subscription_shown_only_when_ambiguous(self):
        single = FindRelatedResourcesTool._format_related(["storage"], {"data": _rows(2)})
        assert "sub=" not in single

        rows = _rows(2)
        rows[1]["subscriptionId"] = "sub-b"
        multi = FindRelatedResourcesTool._format_related(["storage"], {"data": rows})
        assert "sub=sub-b" in multi

    def test_query_no_longer_projects_blob_columns(self):
        from src.services.resource_graph import ResourceGraphQueryBuilder

        query = ResourceGraphQueryBuilder.find_related_resources(["storage"])
        assert "properties" not in query
        assert "order by type asc, name asc" in query


class TestRegionAvailabilityVerdict:
    @staticmethod
    def _types(n: int) -> list[dict]:
        types = [
            {"resourceType": f"type{i:03d}", "locations": [f"Region {j}" for j in range(60)]}
            for i in range(n)
        ]
        types[0] = {"resourceType": "ddosProtectionPlans", "locations": ["Korea Central"]}
        return types

    def test_verdict_survives_the_preview_for_a_large_provider(self):
        out = GetServiceRegionAvailabilityTool._format_availability(
            "Microsoft.Network", self._types(250), ["koreacentral"], True
        )
        assert len(out) > TOOL_RESULT_BUDGET_CHARS
        assert "### Verdict" in out[:TOOL_RESULT_BUDGET_CHARS]

    def test_verdict_is_rolled_up_not_enumerated(self):
        out = GetServiceRegionAvailabilityTool._format_availability(
            "Microsoft.Network", self._types(250), ["koreacentral"], True
        )
        verdict = out.split("### Verdict\n", 1)[1].split("\n\n### Detail", 1)[0]
        assert "1/250 resource types available" in verdict
        assert len(verdict) < 1000

    def test_global_provider_reports_no_restriction(self):
        out = GetServiceRegionAvailabilityTool._format_availability(
            "Microsoft.Advisor",
            [{"resourceType": "recommendations", "locations": []}],
            ["koreacentral"],
            False,
        )
        assert "global service" in out

    def test_detail_section_still_present(self):
        out = GetServiceRegionAvailabilityTool._format_availability(
            "Microsoft.App", self._types(3), ["koreacentral"], False
        )
        assert "### Detail (3 resource types)" in out
        assert "Microsoft.App/type001" in out


# ---------------------------------------------------------------------------
# Analyzer wiring
# ---------------------------------------------------------------------------


class TestExecutionNodeWiring:
    @staticmethod
    def _run_execution(result_text: str, trace_id: str):
        from src.agent.analyzer import AnalysisPlan, AnalysisTask, AzureUpdateAnalyzer

        class _StubTool:
            name = "query_azure_resources"

            async def ainvoke(self, _args):
                return result_text

        analyzer = object.__new__(AzureUpdateAnalyzer)
        analyzer.tools = [_StubTool()]
        analyzer._inject_enrichment_tasks = lambda plan, state: plan

        plan = AnalysisPlan(
            plan_id="p1",
            update_summary="s",
            analysis_goal="g",
            tasks=[
                AnalysisTask(
                    task_id="task-1",
                    description="d",
                    method="kql",
                    tool_name="query_azure_resources",
                    tool_args={},
                    purpose="p",
                )
            ],
        )
        state = {
            "analysis_plan": plan.model_dump(),
            "task_results": {},
            "trace_id": trace_id,
            "iteration": 0,
        }
        return asyncio.run(analyzer._execution_node(state))

    def test_oversized_result_becomes_a_queryable_handle(self, isolated_store):
        body = "\n".join(f"resource-{i:05d}" for i in range(3000)) + "\nneedle-acct-871"
        out = self._run_execution(body, "tr-exec")

        stored_text = out["task_results"]["task-1"]
        assert len(stored_text) < len(body)
        assert "[ref=R" in stored_text
        assert "needle-acct-871" not in stored_text

        ref = stored_text.split("[ref=")[1].split("]")[0]
        found = asyncio.run(QueryToolResultTool()._arun(ref=ref, pattern="needle-acct-871"))
        assert "needle-acct-871" in found

    def test_small_result_is_stored_verbatim(self, isolated_store):
        out = self._run_execution("tiny result", "tr-exec-small")
        assert out["task_results"]["task-1"] == "tiny result"
        assert len(isolated_store) == 0

    def test_a_revision_task_can_resolve_a_ref(self, isolated_store):
        """The evaluation → revise → execute path is the only route to a stored ref."""
        from src.agent.analyzer import AnalysisPlan, AnalysisTask, AzureUpdateAnalyzer

        body = "\n".join(f"resource-{i:05d}" for i in range(3000)) + "\nneedle-acct-871"
        entry = isolated_store.put(tool="query_azure_resources", result=body, trace_id="tr-rev")

        analyzer = object.__new__(AzureUpdateAnalyzer)
        analyzer.tools = [QueryToolResultTool()]
        analyzer._inject_enrichment_tasks = lambda plan, state: plan

        plan = AnalysisPlan(
            plan_id="p1",
            update_summary="s",
            analysis_goal="g",
            tasks=[
                AnalysisTask(
                    task_id="task_r1",
                    description="Search the truncated result",
                    method="context",
                    tool_name="query_tool_result",
                    tool_args={"ref": entry.ref, "pattern": "needle-acct-871"},
                    purpose="p",
                )
            ],
        )
        out = asyncio.run(
            analyzer._execution_node(
                {
                    "analysis_plan": plan.model_dump(),
                    "task_results": {},
                    "trace_id": "tr-rev",
                    "iteration": 1,
                }
            )
        )
        assert "needle-acct-871" in out["task_results"]["task_r1"]
