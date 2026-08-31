"""Tests for the reproducible pre-release quality campaign."""

import asyncio
import json
from collections import Counter
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from scripts import quality_campaign
from scripts.quality_campaign import (
    _case_id,
    _evaluate_case,
    _load_campaign,
    _load_checkpoint_records,
    _remove_stale_temp_files,
    _resolve_agent_versions,
    _split_cases,
    _validate_resume_state,
    _worktree_sha256,
    aggregate_records,
    compare_runs,
    prepare_campaign,
    run_campaign,
)
from src.agent.analyzer import AnalysisResult, RelevanceStatus
from src.agent.hosted_contract import HostedEvaluationResult, HostedRunDiagnostics
from src.rss.parser import AzureUpdate


def _update(index: int, update_type: str = "General Availability") -> AzureUpdate:
    return AzureUpdate(
        id=f"update-{index}",
        title=f"Azure service update {index}",
        description="Description",
        link=f"https://azure.microsoft.com/updates/update-{index}",
        published_date=datetime(2026, 8, index + 1, tzinfo=timezone.utc),
        categories=["Compute"],
        azure_services=["Virtual Machines"],
        update_type=update_type,
        status="Launched",
    )


def _diagnostics(score: float = 4.6, blocked: int = 0) -> dict:
    dimensions = [
        {
            "key": key,
            "title": key,
            "integer_score": 5,
            "score": score,
            "weight": 1.0,
            "normalized": False,
            "feedback": "",
            "error": "",
        }
        for key in (
            "actionability",
            "faithfulness",
            "job_relevance",
            "structure",
            "architectural_depth",
        )
    ]
    return {
        "report_quality": {
            "weighted_score": score,
            "dimensions": dimensions,
            "critical_flaws": [],
        },
        "trajectory": {"score": 95.0, "passed": True, "issues": []},
        "action_verification": {
            "blocked": blocked,
            "unverified": 0,
            "finding_codes": ["unsafe"] if blocked else [],
        },
    }


def _record(case_id: str, score: float = 4.6, blocked: int = 0) -> dict:
    return {
        "case_id": case_id,
        "title": case_id,
        "trace_id": f"trace-{case_id}",
        "semantic_score": score,
        "action_items_count": 1,
        "diagnostics": _diagnostics(score, blocked),
        "rule_based": {"percentage": 95.0, "items": []},
    }


def _run(run_id: str, records: list[dict]) -> dict:
    return {
        "campaign_id": "campaign-1",
        "dataset_sha256": "dataset-hash",
        "split": "diagnosis",
        "run_id": run_id,
        "runtime": "local",
        "concurrency": 1,
        "run_valid": True,
        "version_lineage_complete": True,
        "source_lineage": {"worktree_sha256": "source-a"},
        "agent_lineage": {"resolved_versions": {"coordinator": {"name": "agent", "version": "1"}}},
        "records": records,
        "aggregate": aggregate_records(records, len(records)),
    }


def test_campaign_disables_verbose_console_after_local_dotenv_load():
    assert quality_campaign.analyzer_module._VERBOSE is False


def test_worktree_hash_includes_untracked_paths_and_contents_deterministically():
    first = _worktree_sha256(
        b"tracked diff",
        [("new-b.py", b"b"), ("new-a.py", b"a")],
    )
    reordered = _worktree_sha256(
        b"tracked diff",
        [("new-a.py", b"a"), ("new-b.py", b"b")],
    )

    assert first == reordered
    assert first != _worktree_sha256(
        b"tracked diff",
        [("new-a.py", b"changed"), ("new-b.py", b"b")],
    )
    assert first != _worktree_sha256(
        b"tracked diff",
        [("renamed-a.py", b"a"), ("new-b.py", b"b")],
    )
    assert first != _worktree_sha256(
        b"different tracked diff",
        [("new-a.py", b"a"), ("new-b.py", b"b")],
    )


def test_checkpoint_loader_rejects_corrupt_json_with_file_name(tmp_path):
    records_dir = tmp_path / "records"
    records_dir.mkdir()
    (records_dir / "001_case-1.json").write_text("{broken", encoding="utf-8")

    with pytest.raises(RuntimeError, match=r"001_case-1\.json \(JSONDecodeError\)"):
        _load_checkpoint_records(tmp_path, {"case-1": 1})


def test_stale_temp_cleanup_is_scoped_to_campaign_atomic_files(tmp_path):
    records_dir = tmp_path / "records"
    records_dir.mkdir()
    (tmp_path / "progress.json.tmp").write_text("stale", encoding="utf-8")
    (records_dir / "001_case.json.tmp").write_text("stale", encoding="utf-8")
    preserved = tmp_path / "notes.txt"
    preserved.write_text("keep", encoding="utf-8")

    assert _remove_stale_temp_files(tmp_path) == 2
    assert preserved.exists()
    assert not list(tmp_path.glob("*.tmp"))
    assert not list(records_dir.glob("*.tmp"))


def test_resolve_agent_versions_records_immutable_lineage_and_closes_clients(monkeypatch):
    names = {
        role: f"agent-{role}"
        for role in (
            "coordinator",
            "resource_graph",
            "azure_mcp",
            "azure_api",
            "report_writer",
            "quality_reviewer",
        )
    }
    settings = SimpleNamespace(
        foundry_project_endpoint="https://example.test/project",
        foundry_hosted_agent_name="agent-hosted",
        foundry_agent_for_role=lambda role: names[role],
    )
    credential = SimpleNamespace(closed=False)
    credential.close = lambda: setattr(credential, "closed", True)

    agents = [
        SimpleNamespace(
            name=name,
            id=f"logical-{role}",
            versions=SimpleNamespace(latest=SimpleNamespace(version="7", id=f"version-{role}")),
        )
        for role, name in {**names, "hosted": "agent-hosted"}.items()
    ]

    class Project:
        closed = False
        agents = SimpleNamespace(list=lambda: agents)

        def close(self):
            self.closed = True

    project = Project()
    monkeypatch.setattr(quality_campaign, "get_settings", lambda: settings)
    monkeypatch.setattr("azure.ai.projects.AIProjectClient", lambda **kwargs: project)
    monkeypatch.setattr("src.config.get_azure_credential", lambda: credential)

    lineage = _resolve_agent_versions()

    assert lineage["error"] == ""
    assert lineage["versions"]["hosted"]["version"] == "7"
    assert lineage["versions"]["resource_graph"]["id"] == "version-resource_graph"
    assert project.closed is True
    assert credential.closed is True


def test_split_is_deterministic_and_keeps_diagnosis_and_holdout():
    updates = [
        _update(0, "Retirement"),
        _update(1, "Retirement"),
        _update(2, "Public Preview"),
        _update(3, "Public Preview"),
        _update(4),
        _update(5),
    ]

    first = _split_cases(updates, holdout_ratio=0.25, seed=42)
    second = _split_cases(list(reversed(updates)), holdout_ratio=0.25, seed=42)

    assert first == second
    assert set(first) == {_case_id(update) for update in updates}
    assert set(first.values()) == {"diagnosis", "holdout"}


@pytest.mark.asyncio
async def test_prepare_freezes_period_dataset_with_missing_published_date(monkeypatch, tmp_path):
    updates = [_update(0), _update(1)]
    updates[0].published_date = None

    class Parser:
        async def get_updates_by_date_range(self, start, end):
            return updates

    monkeypatch.setattr(quality_campaign, "AzureUpdateParser", Parser)

    output = await prepare_campaign(
        datetime(2026, 8, 1),
        datetime(2026, 8, 31),
        sample=0,
        seed=42,
        holdout_ratio=0.25,
        output_dir=tmp_path / "campaign",
    )

    manifest = json.loads((output / "campaign.json").read_text(encoding="utf-8"))
    assert manifest["source_update_count"] == 2
    assert manifest["selected_update_count"] == 2
    assert manifest["full_period"] is True
    assert len(manifest["dataset_sha256"]) == 64
    assert (output / "updates.jsonl").read_text(encoding="utf-8").count("\n") == 2
    restored_manifest, restored_rows = _load_campaign(output)
    assert restored_manifest["dataset_sha256"] == manifest["dataset_sha256"]
    assert len(restored_rows) == 2


@pytest.mark.asyncio
async def test_load_campaign_rejects_rubric_drift(monkeypatch, tmp_path):
    updates = [_update(0), _update(1)]

    class Parser:
        async def get_updates_by_date_range(self, start, end):
            return updates

    monkeypatch.setattr(quality_campaign, "AzureUpdateParser", Parser)
    output = await prepare_campaign(
        datetime(2026, 8, 1),
        datetime(2026, 8, 31),
        sample=0,
        seed=42,
        holdout_ratio=0.25,
        output_dir=tmp_path / "campaign",
    )
    manifest_path = output / "campaign.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["rubric_version"] = "old-rubric"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(RuntimeError, match="rubric_version"):
        _load_campaign(output)


@pytest.mark.asyncio
async def test_evaluate_case_persists_hosted_report_and_all_quality_layers(tmp_path):
    update = _update(0)
    analysis = AnalysisResult(
        update_id=update.id,
        update_title=update.title,
        update_category="new_feature",
        relevance=RelevanceStatus.OPPORTUNITY,
        one_line_summary="새 기능을 평가할 후보 리소스 1개를 확인했습니다.",
        relevance_evidence="Virtual Machines 1개 중 평가 후보 1개를 확인했습니다.",
        relevance_reason=(
            "이 기능은 기존 운영 경로를 바꾸지 않고 새로운 관리 옵션을 제공합니다. "
            "현재 환경의 후보를 기준으로 도입 비용과 운영 책임을 먼저 비교합니다."
        ),
        affected_resources=[],
        impact_summary="후보 리소스에서 기능 채택 가능성을 평가합니다.",
        recommendations=[],
        reference_docs=[
            {
                "title": "Microsoft Learn",
                "url": "https://learn.microsoft.com/azure/virtual-machines/",
            }
        ],
        should_notify=True,
    )

    class HostedAnalyzer:
        async def evaluate_update(self, requested_update, trace_id=None):
            assert requested_update.id == update.id
            return HostedEvaluationResult(
                trace_id=trace_id,
                analysis=analysis.model_dump(mode="json"),
                diagnostics=HostedRunDiagnostics.model_validate(_diagnostics()),
            )

    row = {
        "case_id": _case_id(update),
        "split": "diagnosis",
        "bucket": "ga",
        "update": update.to_dict(),
    }

    record = await _evaluate_case(
        1,
        row,
        "hosted",
        "run-1",
        "ko",
        tmp_path,
        asyncio.Semaphore(1),
        HostedAnalyzer(),
    )

    assert "error" not in record
    assert record["semantic_score"] == 4.6
    assert record["rule_based"]["max_score"] == 100
    assert list(tmp_path.glob("report_*.md"))
    assert list(tmp_path.glob("analysis_*.json"))


@pytest.mark.asyncio
async def test_run_campaign_checkpoints_cases_and_resumes_only_missing(monkeypatch, tmp_path):
    updates = [_update(0), _update(1)]

    class Parser:
        async def get_updates_by_date_range(self, start, end):
            return updates

    monkeypatch.setattr(quality_campaign, "AzureUpdateParser", Parser)
    campaign_dir = await prepare_campaign(
        datetime(2026, 8, 1),
        datetime(2026, 8, 31),
        sample=0,
        seed=42,
        holdout_ratio=0.25,
        output_dir=tmp_path / "campaign",
    )
    settings = SimpleNamespace(report_language="ko", use_hosted_agent=False)

    def fake_get_settings():
        return settings

    fake_get_settings.cache_clear = lambda: None
    source_lineage = {
        "commit": "commit-a",
        "dirty": True,
        "worktree_sha256": "source-a",
        "untracked_file_count": 2,
    }
    versions = {
        "versions": {"coordinator": {"name": "coordinator", "version": "1", "id": "v1"}},
        "error": "",
    }
    agent_lineage = {
        "hosted_agent": "hosted",
        "prompt_agents": {"coordinator": "coordinator"},
    }
    calls = []

    async def fake_evaluate_case(
        index,
        row,
        runtime,
        run_id,
        language,
        run_dir,
        semaphore,
        hosted_analyzer,
        campaign_attempt=1,
    ):
        calls.append(row["case_id"])
        record = _record(row["case_id"])
        record.update(
            {
                "index": index,
                "split": row["split"],
                "bucket": row["bucket"],
                "url": row["update"]["link"],
                "runtime": runtime,
                "campaign_attempt": campaign_attempt,
                "generation_failed": False,
            }
        )
        return record

    monkeypatch.setattr(quality_campaign, "get_settings", fake_get_settings)
    monkeypatch.setattr(quality_campaign, "_git_lineage", lambda: source_lineage)
    monkeypatch.setattr(quality_campaign, "_resolve_agent_versions", lambda: versions)
    monkeypatch.setattr(quality_campaign, "_settings_lineage", lambda: dict(agent_lineage))
    monkeypatch.setattr(quality_campaign, "_evaluate_case", fake_evaluate_case)
    monkeypatch.setattr("src.logging_config.setup_logging", lambda **kwargs: None)

    run_dir = await run_campaign(
        campaign_dir,
        "baseline-a",
        "local",
        "all",
        concurrency=2,
    )

    record_paths = sorted((run_dir / "records").glob("*.json"))
    assert len(record_paths) == 2
    assert (
        json.loads((run_dir / "progress.json").read_text(encoding="utf-8"))["status"] == "completed"
    )

    (run_dir / "summary.json").unlink()
    record_paths[-1].unlink()
    calls.clear()

    resumed_dir = await run_campaign(
        campaign_dir,
        "baseline-a",
        "local",
        "all",
        concurrency=2,
        resume_run=run_dir,
    )

    summary = json.loads((resumed_dir / "summary.json").read_text(encoding="utf-8"))
    assert resumed_dir == run_dir
    assert calls == [_case_id(updates[1])]
    assert len(summary["records"]) == 2
    assert summary["resumed"] is True
    assert summary["run_valid"] is True


@pytest.mark.asyncio
async def test_run_campaign_retries_transient_case_after_first_pass(monkeypatch, tmp_path):
    updates = [_update(0), _update(1)]

    class Parser:
        async def get_updates_by_date_range(self, start, end):
            return updates

    monkeypatch.setattr(quality_campaign, "AzureUpdateParser", Parser)
    campaign_dir = await prepare_campaign(
        datetime(2026, 8, 1),
        datetime(2026, 8, 31),
        sample=0,
        seed=42,
        holdout_ratio=0.25,
        output_dir=tmp_path / "campaign",
    )
    settings = SimpleNamespace(report_language="ko", use_hosted_agent=False)

    def fake_get_settings():
        return settings

    fake_get_settings.cache_clear = lambda: None
    source_lineage = {
        "commit": "commit-a",
        "dirty": True,
        "worktree_sha256": "source-a",
        "untracked_file_count": 2,
    }
    versions = {
        "versions": {"coordinator": {"name": "coordinator", "version": "1", "id": "v1"}},
        "error": "",
    }
    attempts = Counter()

    async def fake_evaluate_case(
        index,
        row,
        runtime,
        run_id,
        language,
        run_dir,
        semaphore,
        hosted_analyzer,
        campaign_attempt=1,
    ):
        case_id = row["case_id"]
        attempts[case_id] += 1
        if case_id == _case_id(updates[0]) and campaign_attempt == 1:
            return {
                "index": index,
                "case_id": case_id,
                "title": row["update"]["title"],
                "trace_id": "trace-failed",
                "runtime": runtime,
                "campaign_attempt": campaign_attempt,
                "generation_failed": False,
                "error": "APIConnectionError: Connection error.",
            }
        record = _record(case_id)
        record.update(
            {
                "index": index,
                "split": row["split"],
                "bucket": row["bucket"],
                "url": row["update"]["link"],
                "runtime": runtime,
                "campaign_attempt": campaign_attempt,
                "generation_failed": False,
            }
        )
        return record

    monkeypatch.setattr(quality_campaign, "get_settings", fake_get_settings)
    monkeypatch.setattr(quality_campaign, "_git_lineage", lambda: source_lineage)
    monkeypatch.setattr(quality_campaign, "_resolve_agent_versions", lambda: versions)
    monkeypatch.setattr(
        quality_campaign,
        "_settings_lineage",
        lambda: {"hosted_agent": "hosted", "prompt_agents": {"coordinator": "coordinator"}},
    )
    monkeypatch.setattr(quality_campaign, "_evaluate_case", fake_evaluate_case)
    monkeypatch.setattr("src.logging_config.setup_logging", lambda **kwargs: None)

    run_dir = await run_campaign(
        campaign_dir,
        "baseline-a",
        "local",
        "all",
        concurrency=1,
    )

    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    recovered = next(
        record for record in summary["records"] if record["case_id"] == _case_id(updates[0])
    )
    assert attempts[_case_id(updates[0])] == 2
    assert attempts[_case_id(updates[1])] == 1
    assert recovered["campaign_attempt"] == 2
    assert summary["aggregate"]["error_count"] == 0
    assert summary["aggregate"]["case_retry_count"] == 1
    assert summary["aggregate"]["recovered_case_count"] == 1
    assert len(list((run_dir / "attempts").glob("*"))) == 3


def test_resume_rejects_changed_source_lineage():
    state = {
        "schema_version": quality_campaign.CAMPAIGN_SCHEMA_VERSION,
        "rubric_version": quality_campaign.RUBRIC_VERSION,
        "campaign_id": "campaign-1",
        "dataset_sha256": "dataset-1",
        "tag": "baseline-a",
        "runtime": "local",
        "split": "diagnosis",
        "concurrency": 2,
        "expected_cases": [{"index": 1, "case_id": "case-1"}],
        "source_lineage": {
            "commit": "commit-a",
            "dirty": True,
            "worktree_sha256": "source-a",
            "untracked_file_count": 2,
        },
        "agent_lineage": {"resolved_versions": {"coordinator": {"version": "1"}}},
    }

    with pytest.raises(RuntimeError, match="source lineage changed"):
        _validate_resume_state(
            state,
            manifest={"campaign_id": "campaign-1", "dataset_sha256": "dataset-1"},
            tag="baseline-a",
            runtime="local",
            split="diagnosis",
            concurrency=2,
            expected_cases=[{"index": 1, "case_id": "case-1"}],
            source_lineage={
                "commit": "commit-a",
                "dirty": True,
                "worktree_sha256": "source-b",
                "untracked_file_count": 2,
            },
            agent_lineage=state["agent_lineage"],
        )


def test_aggregate_passes_only_when_every_quality_layer_passes():
    records = [_record("case-1"), _record("case-2")]

    aggregate = aggregate_records(records, expected_count=2)

    assert aggregate["passed"] is True
    assert aggregate["semantic_average"] == 4.6
    assert aggregate["rule_average"] == 95.0
    assert aggregate["trajectory_average"] == 95.0
    assert all(aggregate["gates"].values())


def test_aggregate_fails_closed_when_semantic_diagnostics_are_missing():
    record = _record("case-1")
    record["diagnostics"]["report_quality"] = None

    aggregate = aggregate_records([record], expected_count=1)

    assert aggregate["passed"] is False
    assert aggregate["missing_report_quality"] == 1
    assert aggregate["gates"]["semantic_diagnostics_complete"] is False


def test_aggregate_average_cannot_hide_a_low_semantic_case():
    records = [_record(f"case-{index}", score=5.0) for index in range(4)]
    records.append(_record("case-low", score=2.9))

    aggregate = aggregate_records(records, expected_count=5)

    assert aggregate["semantic_average"] >= 4.5
    assert aggregate["gates"]["semantic_average_at_target"] is True
    assert aggregate["gates"]["no_semantic_case_below_adequate"] is False
    assert aggregate["passed"] is False


def test_aggregate_rejects_unverified_actions():
    record = _record("case-1")
    record["diagnostics"]["action_verification"]["unverified"] = 1

    aggregate = aggregate_records([record], expected_count=1)

    assert aggregate["unverified_action_count"] == 1
    assert aggregate["gates"]["no_unverified_actions"] is False
    assert aggregate["passed"] is False


def test_aggregate_rejects_geval_dimension_errors():
    record = _record("case-1")
    record["diagnostics"]["report_quality"]["dimensions"][0]["error"] = "429"

    aggregate = aggregate_records([record], expected_count=1)

    assert aggregate["dimension_error_count"] == 1
    assert aggregate["gates"]["no_dimension_errors"] is False
    assert aggregate["passed"] is False


def test_compare_requires_effect_beyond_noise_floor():
    baseline = _run("baseline", [_record("case-1", 4.0), _record("case-2", 4.1)])
    candidate = _run("candidate", [_record("case-1", 4.3), _record("case-2", 4.4)])

    comparison = compare_runs(baseline, candidate, noise_floor=0.15)

    assert comparison["verdict"] == "improved"
    assert comparison["overall_delta"]["mean"] == 0.3
    assert comparison["rule_delta"]["mean"] == 0.0
    assert comparison["trajectory_delta"]["mean"] == 0.0
    assert comparison["paired_case_count"] == 2


def test_compare_rejects_safety_regression_even_when_score_improves():
    baseline = _run("baseline", [_record("case-1", 4.0, blocked=0)])
    candidate = _run("candidate", [_record("case-1", 4.5, blocked=1)])

    comparison = compare_runs(baseline, candidate)

    assert comparison["verdict"] == "regression"
    assert any("blocked_action_count increased" in item for item in comparison["regressions"])


def test_compare_rejects_dimension_error_even_when_score_improves():
    baseline = _run("baseline", [_record("case-1", 4.0)])
    candidate_record = _record("case-1", 4.5)
    candidate_record["diagnostics"]["report_quality"]["dimensions"][0]["error"] = "429"
    candidate = _run("candidate", [candidate_record])

    comparison = compare_runs(baseline, candidate)

    assert comparison["verdict"] == "regression"
    assert any("dimension_error_count increased" in item for item in comparison["regressions"])


def test_compare_rejects_different_case_sets():
    baseline = _run("baseline", [_record("case-1"), _record("case-2")])
    candidate = _run("candidate", [_record("case-2"), _record("case-3")])

    with pytest.raises(ValueError, match="different case sets"):
        compare_runs(baseline, candidate)


def test_compare_reports_generation_failures_as_excluded_pairs():
    baseline_record = _record("case-1")
    candidate_record = _record("case-1")
    candidate_record["generation_failed"] = True
    baseline = _run("baseline", [baseline_record])
    candidate = _run("candidate", [candidate_record])

    comparison = compare_runs(baseline, candidate)

    assert comparison["case_count"] == 1
    assert comparison["paired_case_count"] == 0
    assert comparison["excluded_pair_count"] == 1
    assert comparison["verdict"] == "regression"


def test_aa_comparison_rejects_changed_source_or_agent_version():
    baseline = _run("baseline-a", [_record("case-1")])
    candidate = _run("baseline-b", [_record("case-1")])
    candidate["source_lineage"]["worktree_sha256"] = "source-b"

    with pytest.raises(ValueError, match="A/A runs changed experimental axes: source"):
        compare_runs(baseline, candidate, mode="aa")


def test_aa_comparison_rejects_changed_concurrency():
    baseline = _run("baseline-a", [_record("case-1")])
    candidate = _run("baseline-b", [_record("case-1")])
    candidate["concurrency"] = 2

    with pytest.raises(ValueError, match="A/A runs changed experimental axes: concurrency"):
        compare_runs(baseline, candidate, mode="aa")


def test_comparison_rejects_lineage_invalid_run():
    baseline = _run("baseline", [_record("case-1")])
    candidate = _run("candidate", [_record("case-1")])
    candidate["run_valid"] = False

    with pytest.raises(ValueError, match="changed source or Agent lineage"):
        compare_runs(baseline, candidate)
