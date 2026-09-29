"""Smoke test for the deterministic archive evaluator."""

import json
from itertools import count
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import evaluate_archive as archive_evaluator
from scripts.evaluate_archive import (
    _email_like_value_count,
    _personalized_key_count,
    evaluate_archive,
)


def test_email_like_value_scanner_checks_nested_free_text():
    payload = {"result": {"notes": ["safe", "subscriber@example.com"]}}
    assert _email_like_value_count(payload) == 1


def test_personalized_key_scanner_rejects_job_relevance_at_any_depth():
    payload = {"result": {"job_relevance": "high"}}
    assert _personalized_key_count(payload) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("latency_ms, expected_passed", [(1, True), (1_000, False)])
async def test_archive_evaluator_passes_and_writes_metrics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    latency_ms: int,
    expected_passed: bool,
):
    clock = count(step=latency_ms / 1_000)
    monkeypatch.setattr(
        archive_evaluator, "time", SimpleNamespace(perf_counter=lambda: next(clock))
    )
    result = await evaluate_archive(120, tmp_path)

    assert result["passed"] is expected_passed
    assert result["metrics"]["list_p95_ms"] == latency_ms
    assert result["gates"]["bounded_file_p95"] is expected_passed
    assert all(passed for gate, passed in result["gates"].items() if gate != "bounded_file_p95")
    assert result["metrics"]["listed_count"] == 120
    assert result["metrics"]["duplicate_count"] == 0
    assert result["metrics"]["filter_false_negative_count"] == 0
    assert result["metrics"]["personalized_key_count"] == 0
    assert result["metrics"]["email_like_value_count"] == 0
    assert json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8")) == result
