#!/usr/bin/env python
"""Submit AzBrief campaign reports to Microsoft Foundry cloud evaluation.

This bridge evaluates precomputed, canonical report output against public Azure
Update context. It deliberately excludes raw tenant evidence, subscriber data,
and private judge reasoning. Tenant-specific faithfulness remains the local
G-Eval reviewer's responsibility because that reviewer sees the exact evidence
snapshot used to write the report.

Foundry cloud evaluation is an independent cross-check for writing quality,
task adherence, relevance, and indirect prompt-injection behavior. Its results
do not replace the non-compensating local trajectory and action-safety gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.quality_campaign import load_azd_environment  # noqa: E402
from src.config import get_settings  # noqa: E402

UTC = timezone.utc
DEFAULT_EVALUATORS = (
    "coherence",
    "fluency",
    "relevance",
    "task_adherence",
)
OPTIONAL_EVALUATORS = ("indirect_attack",)
ALL_EVALUATORS = DEFAULT_EVALUATORS + OPTIONAL_EVALUATORS
MAX_BATCH_ROWS = 100_000
MAX_ROW_BYTES = 2_000_000
_TERMINAL_STATUSES = frozenset({"completed", "failed", "canceled", "cancelled"})
_RETRYABLE_ERROR_CODES = frozenset(
    {"internalservererror", "serviceunavailable", "systemerror", "timeout"}
)
_EMAIL_LIKE_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)


class FoundryEvaluationError(RuntimeError):
    """Raised when a campaign cannot be safely evaluated in Foundry."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _jsonable(model_dump(mode="json"))
        except TypeError:
            return _jsonable(model_dump())
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    return str(value)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FoundryEvaluationError(f"unable to read {path.name}: {type(exc).__name__}") from exc
    if not isinstance(value, dict):
        raise FoundryEvaluationError(f"{path.name} must contain a JSON object")
    return value


def _campaign_dir(run_dir: Path) -> Path:
    if run_dir.parent.name != "runs":
        raise FoundryEvaluationError("run directory must be under <campaign>/runs/")
    return run_dir.parent.parent


def _load_updates(campaign_dir: Path, expected_hash: str) -> dict[str, dict[str, Any]]:
    manifest = _load_json(campaign_dir / "campaign.json")
    dataset_path = campaign_dir / str(manifest.get("dataset_file", "updates.jsonl"))
    try:
        dataset_bytes = dataset_path.read_bytes()
    except OSError as exc:
        raise FoundryEvaluationError("campaign update dataset is unreadable") from exc
    actual_hash = _sha256(dataset_bytes)
    manifest_hash = str(manifest.get("dataset_sha256", ""))
    if not manifest_hash or actual_hash != manifest_hash or actual_hash != expected_hash:
        raise FoundryEvaluationError("campaign update dataset hash mismatch")

    updates = {}
    for line_number, line in enumerate(dataset_bytes.decode("utf-8").splitlines(), start=1):
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise FoundryEvaluationError(
                f"campaign update dataset is invalid at line {line_number}"
            ) from exc
        case_id = str(row.get("case_id", ""))
        update = row.get("update")
        if not case_id or not isinstance(update, dict):
            raise FoundryEvaluationError(
                f"campaign update dataset has an invalid row at line {line_number}"
            )
        updates[case_id] = update
    return updates


def _render_query(update: dict[str, Any]) -> str:
    services = ", ".join(str(item) for item in update.get("azure_services") or []) or "N/A"
    return (
        "Produce a concise Azure Update intelligence report for a general Azure administrator.\n"
        "Requirements:\n"
        "- Explain the public update before the environment conclusion.\n"
        "- Keep update facts, validated tenant findings, and unknowns distinct.\n"
        "- Frame changes as impact/risk and new capabilities as opportunity/adoption cost.\n"
        "- Name resources and provide executable actions only when evidence supports them.\n"
        "- Never invent resources, dates, commands, URLs, deadlines, or certainty.\n"
        "- Optimize for a three-second summary and a thirty-second scan.\n"
        "The response can contain tenant findings derived from separately validated tenant evidence. "
        "That evidence is intentionally omitted from this Foundry cross-check; do not treat its "
        "absence here as proof that a tenant-specific statement is fabricated.\n\n"
        "Public Azure Update context:\n"
        f"Title: {update.get('title', '')}\n"
        f"Description: {update.get('description', '')}\n"
        f"Update type: {update.get('update_type') or 'N/A'}\n"
        f"Status: {update.get('status') or 'N/A'}\n"
        f"Services: {services}\n"
        f"Published: {update.get('published_date') or 'N/A'}\n"
        f"URL: {update.get('link', '')}"
    )


def build_evaluation_items(
    run_dir: Path,
    *,
    max_cases: int = 0,
    allow_unvalidated_run: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build Foundry dataset rows from one completed campaign run."""
    run_dir = run_dir.resolve()
    summary = _load_json(run_dir / "summary.json")
    if summary.get("run_valid") is not True and not allow_unvalidated_run:
        raise FoundryEvaluationError(
            "campaign run_valid must be true; use --allow-unvalidated-run only for a smoke probe"
        )
    expected_hash = str(summary.get("dataset_sha256", ""))
    if not expected_hash:
        raise FoundryEvaluationError("campaign run has no dataset hash")
    updates = _load_updates(_campaign_dir(run_dir), expected_hash)

    items = []
    case_lineage = []
    excluded: Counter[str] = Counter()
    records = sorted(summary.get("records") or [], key=lambda item: int(item.get("index", 0)))
    for record in records:
        if record.get("error"):
            excluded["case_error"] += 1
            continue
        if record.get("generation_failed"):
            excluded["generation_failed"] += 1
            continue
        case_id = str(record.get("case_id", ""))
        update = updates.get(case_id)
        if update is None:
            raise FoundryEvaluationError(f"campaign update is missing for case {case_id}")
        report_path = run_dir / f"report_{int(record['index']):03d}_{case_id}.md"
        try:
            response = report_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise FoundryEvaluationError(f"report artifact is missing for case {case_id}") from exc
        query = _render_query(update)
        if _EMAIL_LIKE_RE.search(query) or _EMAIL_LIKE_RE.search(response):
            raise FoundryEvaluationError(f"email-like value found in Foundry item {case_id}")

        item = {
            "case_id": case_id,
            "query": query,
            "response": response,
        }
        row_bytes = (_canonical_json(item) + "\n").encode("utf-8")
        if len(row_bytes) > MAX_ROW_BYTES:
            raise FoundryEvaluationError(f"Foundry item {case_id} exceeds the 2 MB row limit")
        items.append(item)
        if len(items) > MAX_BATCH_ROWS:
            raise FoundryEvaluationError("Foundry evaluation exceeds the 100,000 row batch limit")
        trajectory = (record.get("diagnostics") or {}).get("trajectory") or {}
        case_lineage.append(
            {
                "case_id": case_id,
                "trace_id": str(record.get("trace_id", "")),
                "local_semantic_score": record.get("semantic_score"),
                "local_rule_score": (record.get("rule_based") or {}).get("percentage"),
                "local_trajectory_score": trajectory.get("score"),
            }
        )
        if max_cases > 0 and len(items) >= max_cases:
            break

    if not items:
        raise FoundryEvaluationError("campaign run has no successful report artifacts to evaluate")
    input_bytes = "".join(_canonical_json(item) + "\n" for item in items).encode("utf-8")
    return items, {
        "source_run_id": str(summary.get("run_id", run_dir.name)),
        "source_campaign_id": str(summary.get("campaign_id", "")),
        "source_rubric_version": str(summary.get("rubric_version", "")),
        "source_run_valid": summary.get("run_valid"),
        "selected_count": len(items),
        "excluded_count": sum(excluded.values()),
        "excluded_reasons": dict(sorted(excluded.items())),
        "case_lineage": case_lineage,
        "input_sha256": _sha256(input_bytes),
        "data_classification": "canonical_report_and_public_update_context",
        "raw_tenant_evidence_included": False,
        "subscriber_data_included": False,
        "private_reasoning_included": False,
    }


def build_testing_criteria(
    judge_deployment: str,
    evaluator_names: Optional[tuple[str, ...]] = None,
) -> list[dict[str, Any]]:
    """Return the fixed independent Foundry evaluator set."""
    selected = evaluator_names or DEFAULT_EVALUATORS
    if len(selected) != len(set(selected)):
        raise FoundryEvaluationError("evaluator names must be unique")
    unknown = sorted(set(selected) - set(ALL_EVALUATORS))
    if unknown:
        raise FoundryEvaluationError(f"unsupported evaluator: {', '.join(unknown)}")
    if any(name != "indirect_attack" for name in selected) and not judge_deployment:
        raise FoundryEvaluationError("a Foundry judge model deployment is required")
    query_response = {"query": "{{item.query}}", "response": "{{item.response}}"}
    criteria = []
    for name in selected:
        mapping = {"response": "{{item.response}}"} if name == "fluency" else query_response
        item: dict[str, Any] = {
            "type": "azure_ai_evaluator",
            "name": name,
            "evaluator_name": f"builtin.{name}",
            "data_mapping": dict(mapping),
        }
        if name != "indirect_attack":
            item["initialization_parameters"] = {"deployment_name": judge_deployment}
        criteria.append(item)
    return criteria


def _data_source_config() -> dict[str, Any]:
    properties = {
        "case_id": {"type": "string"},
        "query": {"type": "string"},
        "response": {"type": "string"},
    }
    return {
        "type": "custom",
        "item_schema": {
            "type": "object",
            "properties": properties,
            "required": ["case_id", "query", "response"],
        },
        "include_sample_schema": False,
    }


def _run_error(eval_run: Any) -> Any:
    return _jsonable(getattr(eval_run, "error", None))


def _is_retryable_run_failure(eval_run: Any) -> bool:
    error = _run_error(eval_run)
    if not isinstance(error, dict):
        return False
    return str(error.get("code", "")).casefold() in _RETRYABLE_ERROR_CODES


def _normalized_definition_criteria(criteria: Any) -> list[dict[str, Any]]:
    normalized = []
    for item in criteria or []:
        data = _jsonable(item)
        if not isinstance(data, dict):
            raise FoundryEvaluationError("reused eval definition has invalid testing criteria")
        normalized.append(
            {
                "type": data.get("type"),
                "name": data.get("name"),
                "evaluator_name": data.get("evaluator_name"),
                "data_mapping": data.get("data_mapping") or {},
                "initialization_parameters": data.get("initialization_parameters") or {},
            }
        )
    return normalized


def _normalized_definition_source(config: Any) -> dict[str, Any]:
    data = _jsonable(config)
    if not isinstance(data, dict):
        raise FoundryEvaluationError("reused eval definition has an invalid data source")
    item_schema = data.get("item_schema") or {}
    if not item_schema:
        schema = data.get("schema_") or data.get("schema") or {}
        if isinstance(schema, dict):
            item_schema = schema.get("item") or {}
    return {
        "type": data.get("type"),
        "item_schema": item_schema,
        "include_sample_schema": bool(data.get("include_sample_schema", False)),
    }


def _validate_reused_definition(definition: Any, criteria: list[dict[str, Any]]) -> None:
    data = _jsonable(definition)
    if not isinstance(data, dict):
        raise FoundryEvaluationError("reused eval definition has an invalid response")
    actual_criteria = _normalized_definition_criteria(data.get("testing_criteria"))
    expected_criteria = _normalized_definition_criteria(criteria)
    actual_source = _normalized_definition_source(data.get("data_source_config"))
    expected_source = _normalized_definition_source(_data_source_config())
    if actual_criteria != expected_criteria or actual_source != expected_source:
        raise FoundryEvaluationError(
            "reused eval definition does not match the requested evaluators or item schema"
        )


def submit_evaluation(
    openai_client: Any,
    items: list[dict[str, Any]],
    criteria: list[dict[str, Any]],
    *,
    name: str,
    poll_interval_s: float,
    timeout_s: float,
    eval_id: str = "",
    max_run_attempts: int = 2,
) -> dict[str, Any]:
    """Create or reuse an eval definition, run inline data, and collect results."""
    if max_run_attempts < 1:
        raise FoundryEvaluationError("max_run_attempts must be at least 1")
    if not eval_id:
        evaluation = openai_client.evals.create(
            name=name,
            data_source_config=_data_source_config(),
            testing_criteria=criteria,
        )
        eval_id = str(evaluation.id)
        definition_created = True
    else:
        definition = openai_client.evals.retrieve(eval_id)
        _validate_reused_definition(definition, criteria)
        definition_created = False

    started = time.monotonic()
    attempts = []
    eval_run = None
    data_source = {
        "type": "jsonl",
        "source": {
            "type": "file_content",
            "content": [{"item": item} for item in items],
        },
    }
    for attempt_number in range(1, max_run_attempts + 1):
        eval_run = openai_client.evals.runs.create(
            eval_id=eval_id,
            name=f"{name}-run-a{attempt_number}",
            data_source=data_source,
        )
        while str(getattr(eval_run, "status", "")).casefold() not in _TERMINAL_STATUSES:
            if time.monotonic() - started >= timeout_s:
                try:
                    openai_client.evals.runs.cancel(run_id=eval_run.id, eval_id=eval_id)
                finally:
                    raise FoundryEvaluationError("Foundry evaluation timed out and was canceled")
            if poll_interval_s > 0:
                time.sleep(poll_interval_s)
            eval_run = openai_client.evals.runs.retrieve(
                run_id=eval_run.id,
                eval_id=eval_id,
            )
        attempts.append(
            {
                "attempt": attempt_number,
                "eval_run_id": str(eval_run.id),
                "status": str(getattr(eval_run, "status", "")),
                "error": _run_error(eval_run),
            }
        )
        if not _is_retryable_run_failure(eval_run) or attempt_number == max_run_attempts:
            break

    assert eval_run is not None

    status = str(getattr(eval_run, "status", ""))
    output_items = []
    if status.casefold() == "completed":
        output_items = [
            _jsonable(item)
            for item in openai_client.evals.runs.output_items.list(
                run_id=eval_run.id,
                eval_id=eval_id,
            )
        ]
    return {
        "eval_id": eval_id,
        "eval_run_id": str(eval_run.id),
        "definition_created": definition_created,
        "status": status,
        "error": _run_error(eval_run),
        "attempts": attempts,
        "report_url": str(getattr(eval_run, "report_url", "") or ""),
        "result_counts": _jsonable(getattr(eval_run, "result_counts", None)),
        "per_testing_criteria_results": _jsonable(
            getattr(eval_run, "per_testing_criteria_results", None)
        ),
        "output_items": output_items,
        "elapsed_s": round(time.monotonic() - started, 2),
    }


def _safe_name(value: str, limit: int = 80) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-") or "azbrief-evaluation"
    return cleaned[:limit]


def _write_prepared(
    output_dir: Path,
    items: list[dict[str, Any]],
    metadata: dict[str, Any],
    criteria: list[dict[str, Any]],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=False)
    input_bytes = "".join(_canonical_json(item) + "\n" for item in items).encode("utf-8")
    (output_dir / "input.jsonl").write_bytes(input_bytes)
    (output_dir / "manifest.json").write_text(
        json.dumps(
            {
                **metadata,
                "created_at": datetime.now(UTC).isoformat(),
                "evaluators": [item["name"] for item in criteria],
                "evaluator_config_sha256": _sha256(_canonical_json(criteria).encode("utf-8")),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _create_clients() -> tuple[Any, Any, Any]:
    from azure.ai.projects import AIProjectClient

    from src.config import get_azure_credential

    settings = get_settings()
    if not settings.foundry_project_endpoint:
        raise FoundryEvaluationError("Foundry project endpoint is not configured")
    credential = get_azure_credential()
    project_client = AIProjectClient(
        endpoint=settings.foundry_project_endpoint,
        credential=credential,
    )
    return credential, project_client, project_client.get_openai_client()


def _default_output(run_dir: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return run_dir / "foundry_evaluations" / timestamp


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--allow-unvalidated-run", action="store_true")
    parser.add_argument("--judge-deployment", default="")
    parser.add_argument(
        "--evaluators",
        nargs="+",
        choices=ALL_EVALUATORS,
        default=list(DEFAULT_EVALUATORS),
        help=(
            "Foundry evaluators to run; indirect_attack is opt-in because its hosted safety "
            "runtime is not available in every batch-evaluation region"
        ),
    )
    parser.add_argument("--use-azd-env", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--eval-id", default="", help="reuse an existing eval definition")
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument("--max-run-attempts", type=int, default=2)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    if args.max_cases < 0:
        raise FoundryEvaluationError("--max-cases must be zero or greater")
    if args.use_azd_env:
        load_azd_environment()
    settings = get_settings()
    judge_deployment = args.judge_deployment or settings.foundry_model_deployment or ""
    criteria = build_testing_criteria(judge_deployment, tuple(args.evaluators))
    items, metadata = build_evaluation_items(
        args.run_dir,
        max_cases=args.max_cases,
        allow_unvalidated_run=args.allow_unvalidated_run,
    )
    output_dir = args.output_dir or _default_output(args.run_dir)
    _write_prepared(output_dir, items, metadata, criteria)
    if args.prepare_only:
        print(output_dir)
        return 0

    credential = project_client = openai_client = None
    try:
        credential, project_client, openai_client = _create_clients()
        name = _safe_name(f"azbrief-{metadata['source_run_id']}")
        result = submit_evaluation(
            openai_client,
            items,
            criteria,
            name=name,
            poll_interval_s=args.poll_interval,
            timeout_s=args.timeout,
            eval_id=args.eval_id,
            max_run_attempts=args.max_run_attempts,
        )
    finally:
        if openai_client is not None:
            openai_client.close()
        if project_client is not None:
            project_client.close()
        if credential is not None:
            credential.close()

    result["source"] = metadata
    result["evaluators"] = [item["name"] for item in criteria]
    result["evaluator_config_sha256"] = _sha256(_canonical_json(criteria).encode("utf-8"))
    (output_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(output_dir)
    if result["report_url"]:
        print(result["report_url"])
    return 0 if result["status"].casefold() == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
