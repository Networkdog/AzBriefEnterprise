"""Tests for the Microsoft Foundry cloud-evaluation bridge."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.foundry_evaluation import (
    ALL_EVALUATORS,
    DEFAULT_EVALUATORS,
    FoundryEvaluationError,
    build_evaluation_items,
    build_testing_criteria,
    submit_evaluation,
)


def _run_fixture(tmp_path: Path, *, run_valid: bool = True) -> Path:
    campaign_dir = tmp_path / "campaign"
    run_dir = campaign_dir / "runs" / "baseline-a"
    run_dir.mkdir(parents=True)
    update = {
        "case_id": "case-1",
        "split": "diagnosis",
        "bucket": "ga",
        "update": {
            "id": "update-1",
            "title": "Azure feature is generally available",
            "description": "The feature adds a supported monitoring capability.",
            "link": "https://azure.microsoft.com/updates?id=update-1",
            "published_date": "2026-08-01T00:00:00+00:00",
            "azure_services": ["Azure Monitor"],
            "categories": ["Launched"],
            "update_type": "General Availability",
            "status": "Now Available",
            "learn_more_links": [],
        },
    }
    dataset = json.dumps(update, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    (campaign_dir / "updates.jsonl").write_text(dataset, encoding="utf-8", newline="\n")
    (campaign_dir / "campaign.json").write_text(
        json.dumps(
            {
                "campaign_id": "campaign-1",
                "dataset_file": "updates.jsonl",
                "dataset_sha256": __import__("hashlib").sha256(dataset.encode()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "report_001_case-1.md").write_text(
        "# Azure feature\n\nThe report is concise and actionable.", encoding="utf-8"
    )
    (run_dir / "summary.json").write_text(
        json.dumps(
            {
                "run_id": "baseline-a",
                "campaign_id": "campaign-1",
                "dataset_sha256": json.loads(
                    (campaign_dir / "campaign.json").read_text(encoding="utf-8")
                )["dataset_sha256"],
                "run_valid": run_valid,
                "rubric_version": "test-rubric",
                "records": [
                    {
                        "index": 1,
                        "case_id": "case-1",
                        "trace_id": "trace-1",
                        "title": update["update"]["title"],
                        "generation_failed": False,
                        "semantic_score": 4.0,
                        "rule_based": {"percentage": 95.0},
                        "diagnostics": {"trajectory": {"score": 90.0}},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return run_dir


def test_build_items_uses_public_update_context_and_precomputed_report(tmp_path):
    run_dir = _run_fixture(tmp_path)

    items, metadata = build_evaluation_items(run_dir)

    assert len(items) == 1
    assert items[0]["case_id"] == "case-1"
    assert set(items[0]) == {"case_id", "query", "response"}
    assert "Azure feature is generally available" in items[0]["query"]
    assert "separately validated tenant evidence" in items[0]["query"]
    assert items[0]["response"].startswith("# Azure feature")
    assert "raw_evidence" not in items[0]
    assert metadata["case_lineage"][0]["trace_id"] == "trace-1"
    assert metadata["excluded_count"] == 0
    assert metadata["data_classification"] == "canonical_report_and_public_update_context"


def test_build_items_requires_valid_run_unless_explicitly_overridden(tmp_path):
    run_dir = _run_fixture(tmp_path, run_valid=False)

    with pytest.raises(FoundryEvaluationError, match="run_valid"):
        build_evaluation_items(run_dir)

    items, _ = build_evaluation_items(run_dir, allow_unvalidated_run=True)
    assert len(items) == 1


def test_build_items_rejects_email_like_values(tmp_path):
    run_dir = _run_fixture(tmp_path)
    (run_dir / "report_001_case-1.md").write_text(
        "Contact admin@example.com for approval.", encoding="utf-8"
    )

    with pytest.raises(FoundryEvaluationError, match="email-like"):
        build_evaluation_items(run_dir)


def test_build_items_rejects_report_over_foundry_row_limit(tmp_path):
    run_dir = _run_fixture(tmp_path)
    (run_dir / "report_001_case-1.md").write_text("x" * 2_000_000, encoding="utf-8")

    with pytest.raises(FoundryEvaluationError, match="2 MB row limit"):
        build_evaluation_items(run_dir)


def test_testing_criteria_use_supported_builtins_and_exact_mappings():
    criteria = build_testing_criteria("judge-deployment", ALL_EVALUATORS)

    assert tuple(item["name"] for item in criteria) == ALL_EVALUATORS
    assert all(item["type"] == "azure_ai_evaluator" for item in criteria)
    assert all(item["data_mapping"].get("response") == "{{item.response}}" for item in criteria)
    quality = {item["name"]: item for item in criteria if item["name"] != "indirect_attack"}
    assert all(
        item["initialization_parameters"] == {"deployment_name": "judge-deployment"}
        for item in quality.values()
    )
    safety = next(item for item in criteria if item["name"] == "indirect_attack")
    assert "initialization_parameters" not in safety


def test_default_criteria_exclude_region_limited_safety_evaluator():
    criteria = build_testing_criteria("judge-deployment")

    assert tuple(item["name"] for item in criteria) == DEFAULT_EVALUATORS
    assert "indirect_attack" not in DEFAULT_EVALUATORS


def test_safety_only_criteria_do_not_require_judge_deployment():
    criteria = build_testing_criteria("", ("indirect_attack",))

    assert criteria == [
        {
            "type": "azure_ai_evaluator",
            "name": "indirect_attack",
            "evaluator_name": "builtin.indirect_attack",
            "data_mapping": {"query": "{{item.query}}", "response": "{{item.response}}"},
        }
    ]


def test_enterprise_bicep_isolates_foundry_evaluation_storage():
    bicep = (Path(__file__).parents[1] / "infra" / "enterprise" / "main.bicep").read_text(
        encoding="utf-8"
    )

    assert "resource evaluationStorageAccount 'Microsoft.Storage/storageAccounts@" in bicep
    assert "resource foundryEvaluationStorageConnection " in bicep
    assert "category: 'AzureStorageAccount'" in bicep
    assert "authType: 'AAD'" in bicep
    assert "foundryProjectStorageBlobDataOwnerAssignment" in bicep
    assert "roleIds.storageBlobDataOwner" in bicep
    assert "scope: evaluationStorageAccount" in bicep
    assert "principalId: foundryProject.identity.principalId" in bicep
    assert "resource evaluationStoragePrivateEndpoint " in bicep
    assert "resource evaluationStoragePerimeterAssociation " in bicep


def test_submit_uses_inline_file_content_and_collects_output_items():
    calls = {}
    eval_object = SimpleNamespace(id="eval-1")
    initial_run = SimpleNamespace(id="run-1", status="queued")
    completed_run = SimpleNamespace(
        id="run-1",
        status="completed",
        report_url="https://ai.azure.com/evaluation/run-1",
        result_counts=SimpleNamespace(passed=5, failed=0, errored=0, total=5),
        per_testing_criteria_results=[],
    )

    class Evals:
        def create(self, **kwargs):
            calls["eval"] = kwargs
            return eval_object

        runs = None

    class Runs:
        output_items = SimpleNamespace(
            list=lambda **kwargs: [SimpleNamespace(model_dump=lambda mode=None: {"id": "item-1"})]
        )

        def create(self, **kwargs):
            calls["run"] = kwargs
            return initial_run

        def retrieve(self, **kwargs):
            return completed_run

    evals = Evals()
    evals.runs = Runs()
    client = SimpleNamespace(evals=evals)

    result = submit_evaluation(
        client,
        [{"case_id": "case-1", "query": "q", "response": "r"}],
        build_testing_criteria("judge-deployment"),
        name="azbrief-test",
        poll_interval_s=0,
        timeout_s=30,
    )

    source = calls["run"]["data_source"]["source"]
    assert source["type"] == "file_content"
    assert source["content"] == [{"item": {"case_id": "case-1", "query": "q", "response": "r"}}]
    assert calls["eval"]["data_source_config"]["type"] == "custom"
    assert result["status"] == "completed"
    assert result["output_items"] == [{"id": "item-1"}]


def test_submit_retries_transient_foundry_runtime_failure():
    created_runs = []
    criteria = build_testing_criteria("judge-deployment")
    definition = SimpleNamespace(
        data_source_config={
            "type": "custom",
            "schema_": {
                "item": {
                    "type": "object",
                    "properties": {
                        "case_id": {"type": "string"},
                        "query": {"type": "string"},
                        "response": {"type": "string"},
                    },
                    "required": ["case_id", "query", "response"],
                }
            },
            "include_sample_schema": False,
        },
        testing_criteria=criteria,
    )
    failed_run = SimpleNamespace(
        id="run-1",
        status="failed",
        error={"code": "SystemError", "message": "managed runtime initialization failed"},
        report_url="https://ai.azure.com/evaluation/run-1",
        result_counts=None,
        per_testing_criteria_results=[],
    )
    completed_run = SimpleNamespace(
        id="run-2",
        status="completed",
        error=None,
        report_url="https://ai.azure.com/evaluation/run-2",
        result_counts=SimpleNamespace(passed=5, failed=0, errored=0, total=5),
        per_testing_criteria_results=[],
    )

    class Runs:
        output_items = SimpleNamespace(list=lambda **kwargs: [])

        def create(self, **kwargs):
            created_runs.append(kwargs)
            return failed_run if len(created_runs) == 1 else completed_run

    client = SimpleNamespace(
        evals=SimpleNamespace(
            retrieve=lambda eval_id: definition,
            runs=Runs(),
        )
    )

    result = submit_evaluation(
        client,
        [{"case_id": "case-1", "query": "q", "response": "r"}],
        criteria,
        name="azbrief-test",
        poll_interval_s=0,
        timeout_s=30,
        eval_id="eval-1",
    )

    assert len(created_runs) == 2
    assert [attempt["status"] for attempt in result["attempts"]] == ["failed", "completed"]
    assert result["attempts"][0]["error"]["code"] == "SystemError"
    assert result["status"] == "completed"
    assert result["error"] is None


def test_submit_rejects_reused_definition_with_stale_schema():
    run_calls = []
    criteria = build_testing_criteria("judge-deployment")
    stale_definition = SimpleNamespace(
        data_source_config={
            "type": "custom",
            "schema_": {
                "item": {
                    "type": "object",
                    "properties": {
                        "case_id": {"type": "string"},
                        "trace_id": {"type": "string"},
                        "query": {"type": "string"},
                        "response": {"type": "string"},
                    },
                    "required": ["case_id", "trace_id", "query", "response"],
                }
            },
            "include_sample_schema": False,
        },
        testing_criteria=criteria,
    )
    client = SimpleNamespace(
        evals=SimpleNamespace(
            retrieve=lambda eval_id: stale_definition,
            runs=SimpleNamespace(create=lambda **kwargs: run_calls.append(kwargs)),
        )
    )

    with pytest.raises(FoundryEvaluationError, match="does not match"):
        submit_evaluation(
            client,
            [{"case_id": "case-1", "query": "q", "response": "r"}],
            criteria,
            name="azbrief-test",
            poll_interval_s=0,
            timeout_s=30,
            eval_id="eval-1",
        )

    assert run_calls == []
