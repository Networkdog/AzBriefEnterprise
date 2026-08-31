"""Smoke test for the deterministic archive evaluator."""

import json

import pytest

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
async def test_archive_evaluator_passes_and_writes_metrics(tmp_path):
    result = await evaluate_archive(120, tmp_path)

    assert result["passed"] is True
    assert result["metrics"]["listed_count"] == 120
    assert result["metrics"]["duplicate_count"] == 0
    assert result["metrics"]["filter_false_negative_count"] == 0
    assert result["metrics"]["personalized_key_count"] == 0
    assert result["metrics"]["email_like_value_count"] == 0
    assert json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8")) == result
