#!/usr/bin/env python
"""Reproducible pre-release quality campaigns for AzBrief Enterprise.

The campaign has three explicit phases:

1. ``prepare`` freezes Azure Updates from a date range and creates diagnosis and
   holdout splits.
2. ``run`` invokes the deployed Hosted Agent (or the in-process Hosted harness),
   renders each report, and collects semantic, deterministic, trajectory, and
   action-safety evaluations.
3. ``compare`` performs a paired comparison. Run A/A first to estimate the noise
   floor, then require a candidate to clear that floor without safety regressions.

Generated artifacts live below ``eval_runs/`` and are intentionally gitignored.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import random
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["AZBRIEF_VERBOSE"] = "false"

from scripts.evaluate_batch import _bucket_of, _stratified_sample  # noqa: E402
from scripts.evaluate_report import ReportQualityEvaluator  # noqa: E402
from src.agent import analyzer as analyzer_module  # noqa: E402
from src.agent.analyzer import AnalysisResult, AzureUpdateAnalyzer  # noqa: E402
from src.agent.geval import GEvalJudge  # noqa: E402
from src.agent.hosted_client import HostedAgentAnalyzer  # noqa: E402
from src.agent.hosted_contract import HOSTED_ANALYSIS_CONTRACT_VERSION  # noqa: E402
from src.config import SPECIALIST_AGENT_ROLES, get_settings  # noqa: E402
from src.email.service import EmailService  # noqa: E402
from src.rss.parser import AzureUpdate, AzureUpdateParser  # noqa: E402

os.environ["AZBRIEF_VERBOSE"] = "false"
analyzer_module._VERBOSE = False

UTC = timezone.utc
CAMPAIGN_SCHEMA_VERSION = "1"
RUBRIC_VERSION = "2026-08-31.3"
EVAL_OUTPUT_ROOT = ROOT / "eval_runs"
GENERATION_FAILURE_MARKER = "Report generation failed"
MAX_CASE_ATTEMPTS = 2

_TRANSIENT_CASE_ERROR_MARKERS = (
    "apiconnectionerror",
    "connection error",
    "timeouterror",
    "timed out",
    "rate_limit",
    "rate limit",
    "http 429",
    "http 503",
    "http 529",
    "temporarily unavailable",
)

RELEASE_THRESHOLDS = {
    "semantic_average": 4.5,
    "semantic_dimension_average": 4.0,
    "semantic_case_floor": 3.0,
    "rule_average": 90.0,
    "rule_case_floor": 80.0,
    "trajectory_average": 90.0,
    "trajectory_case_floor": 70.0,
}

RESEARCH_BASIS = (
    {
        "name": "G-Eval",
        "url": "https://arxiv.org/abs/2303.16634",
        "applied_to": "anchored form-filling semantic evaluation",
    },
    {
        "name": "MT-Bench LLM-as-a-Judge analysis",
        "url": "https://arxiv.org/abs/2306.05685",
        "applied_to": "A/A tests and defenses against position and verbosity bias",
    },
    {
        "name": "tau-bench",
        "url": "https://arxiv.org/abs/2406.12045",
        "applied_to": "repeated-run reliability and pass^k-style stability",
    },
    {
        "name": "Microsoft Foundry agent evaluators",
        "url": (
            "https://learn.microsoft.com/azure/foundry/concepts/"
            "evaluation-evaluators/agent-evaluators"
        ),
        "applied_to": "system and process evaluation of tasks and tools",
    },
    {
        "name": "Microsoft Foundry deployed-interaction evaluation",
        "url": (
            "https://learn.microsoft.com/azure/foundry/observability/how-to/"
            "cloud-evaluation-deployed-interactions"
        ),
        "applied_to": "trace-linked evaluation of the deployed Hosted Agent",
    },
)

AZD_ENV_ALIASES = {
    "FOUNDRY_PROJECT_ENDPOINT": (
        "FOUNDRY_PROJECT_ENDPOINT",
        "AZURE_AI_PROJECT_ENDPOINT",
        "AZURE_AIPROJECT_ENDPOINT",
    ),
    "FOUNDRY_HOSTED_AGENT_NAME": (
        "FOUNDRY_HOSTED_AGENT_NAME",
        "AGENT_AZBRIEF_ANALYSIS_HOSTED_NAME",
    ),
    "FOUNDRY_COORDINATOR_AGENT_NAME": ("AZBRIEF_PROMPT_COORDINATOR_AGENT_NAME",),
    "FOUNDRY_RESOURCE_GRAPH_AGENT_NAME": ("AZBRIEF_PROMPT_RESOURCE_GRAPH_AGENT_NAME",),
    "FOUNDRY_AZURE_MCP_AGENT_NAME": ("AZBRIEF_PROMPT_AZURE_MCP_AGENT_NAME",),
    "FOUNDRY_AZURE_API_AGENT_NAME": ("AZBRIEF_PROMPT_AZURE_API_AGENT_NAME",),
    "FOUNDRY_REPORT_WRITER_AGENT_NAME": ("AZBRIEF_PROMPT_REPORT_WRITER_AGENT_NAME",),
    "FOUNDRY_QUALITY_REVIEWER_AGENT_NAME": ("AZBRIEF_PROMPT_QUALITY_REVIEWER_AGENT_NAME",),
    "AZURE_TENANT_ID": ("AZURE_TENANT_ID",),
    "AZURE_SUBSCRIPTION_ID": ("AZURE_SUBSCRIPTION_ID",),
}

LEVER_BY_DIMENSION = {
    "faithfulness": (
        "Inspect specialist claims, tool outputs, evidence completeness, and URL/date grounding; "
        "then change the narrow query, tool, or evidence contract that lost the fact."
    ),
    "actionability": (
        "Inspect report category rules and action verification; improve procedure, scope, "
        "completion criteria, precautions, and rollback without inventing commands."
    ),
    "job_relevance": (
        "Inspect subscriber and report-writer prompts; make the recommendation specific to the "
        "reader's responsibility while preserving the canonical evidence."
    ),
    "structure": (
        "Inspect the report schema and email renderer; remove duplication and restore the "
        "three-second summary and thirty-second scan path."
    ),
    "architectural_depth": (
        "Inspect evidence tasks and category prompts; add only grounded dependency, WAF, "
        "migration, cost, or operational ripple effects."
    ),
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _worktree_sha256(
    tracked_diff: bytes,
    untracked_files: list[tuple[str, bytes]],
) -> str:
    """Hash tracked changes and non-ignored untracked source without ambiguity."""
    digest = hashlib.sha256()
    digest.update(b"tracked-diff\0")
    digest.update(len(tracked_diff).to_bytes(8, "big"))
    digest.update(tracked_diff)
    for relative_path, content in sorted(untracked_files, key=lambda item: item[0]):
        path_bytes = relative_path.replace("\\", "/").encode("utf-8", errors="surrogateescape")
        digest.update(b"untracked-file\0")
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _case_id(update: AzureUpdate) -> str:
    key = f"{update.id}\n{update.link}".encode("utf-8")
    return hashlib.sha256(key).hexdigest()[:16]


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _update_from_dict(value: dict[str, Any]) -> AzureUpdate:
    return AzureUpdate(
        id=value["id"],
        title=value["title"],
        description=value.get("description", ""),
        link=value.get("link", ""),
        published_date=_parse_datetime(value.get("published_date")),
        categories=list(value.get("categories") or []),
        azure_services=list(value.get("azure_services") or []),
        update_type=value.get("update_type"),
        status=value.get("status"),
        learn_more_links=list(value.get("learn_more_links") or []),
    )


def _published_sort_key(update: AzureUpdate) -> tuple[float, str]:
    published = update.published_date
    if published is None:
        return float("-inf"), _case_id(update)
    if published.tzinfo is None:
        published = published.replace(tzinfo=UTC)
    return published.timestamp(), _case_id(update)


def _split_cases(updates: list[AzureUpdate], holdout_ratio: float, seed: int) -> dict[str, str]:
    """Create a deterministic, category-aware diagnosis/holdout split."""
    if not 0.0 < holdout_ratio < 0.5:
        raise ValueError("holdout_ratio must be greater than 0 and less than 0.5")
    if len(updates) < 2:
        return {_case_id(update): "diagnosis" for update in updates}

    buckets: dict[str, list[AzureUpdate]] = defaultdict(list)
    for update in updates:
        buckets[_bucket_of(update)].append(update)

    holdout_ids: set[str] = set()
    for bucket_name, bucket_updates in sorted(buckets.items()):
        ordered = sorted(bucket_updates, key=_case_id)
        bucket_seed = int(
            hashlib.sha256(f"{seed}:{bucket_name}".encode("utf-8")).hexdigest()[:8], 16
        )
        random.Random(bucket_seed).shuffle(ordered)
        holdout_count = int(round(len(ordered) * holdout_ratio))
        if len(ordered) >= 2:
            holdout_count = min(len(ordered) - 1, max(1, holdout_count))
        else:
            holdout_count = 0
        holdout_ids.update(_case_id(update) for update in ordered[:holdout_count])

    if not holdout_ids:
        holdout_ids.add(_case_id(sorted(updates, key=_case_id)[-1]))
    return {
        _case_id(update): "holdout" if _case_id(update) in holdout_ids else "diagnosis"
        for update in updates
    }


def _git_lineage() -> dict[str, Any]:
    """Capture source lineage without storing the potentially sensitive diff."""

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=ROOT,
            capture_output=True,
            check=False,
        )

    head = run("rev-parse", "HEAD")
    diff = run("diff", "--binary", "HEAD")
    untracked = run("ls-files", "--others", "--exclude-standard", "-z")
    if head.returncode != 0 or diff.returncode != 0 or untracked.returncode != 0:
        return {
            "commit": "unknown",
            "dirty": None,
            "worktree_sha256": "unknown",
            "untracked_file_count": None,
        }
    untracked_files = []
    try:
        for path_bytes in sorted(path for path in untracked.stdout.split(b"\0") if path):
            relative_path = path_bytes.decode("utf-8", errors="surrogateescape")
            candidate = ROOT / relative_path
            if candidate.is_file():
                untracked_files.append((relative_path, candidate.read_bytes()))
    except OSError:
        return {
            "commit": head.stdout.decode("ascii", errors="replace").strip(),
            "dirty": None,
            "worktree_sha256": "unknown",
            "untracked_file_count": None,
        }
    return {
        "commit": head.stdout.decode("ascii", errors="replace").strip(),
        "dirty": bool(diff.stdout or untracked_files),
        "worktree_sha256": _worktree_sha256(diff.stdout, untracked_files),
        "untracked_file_count": len(untracked_files),
    }


def _settings_lineage() -> dict[str, Any]:
    settings = get_settings()
    return {
        "hosted_agent": settings.foundry_hosted_agent_name or "",
        "prompt_agents": {
            role: settings.foundry_agent_for_role(role) or "" for role in SPECIALIST_AGENT_ROLES
        },
        "report_language": settings.report_language,
        "geval_target": settings.geval_target_score,
        "geval_runtime_enabled": settings.geval_runtime_enabled,
        "trajectory_eval_enabled": settings.trajectory_eval_enabled,
        "action_verification_enabled": settings.action_verification_enabled,
    }


def _resolve_agent_versions() -> dict[str, Any]:
    """Resolve immutable Agent versions without exporting definitions or credentials."""
    settings = get_settings()
    names = {role: settings.foundry_agent_for_role(role) or "" for role in SPECIALIST_AGENT_ROLES}
    if settings.foundry_hosted_agent_name:
        names["hosted"] = settings.foundry_hosted_agent_name
    if not settings.foundry_project_endpoint:
        return {"versions": {}, "error": "Foundry project endpoint is not configured"}

    from azure.ai.projects import AIProjectClient

    from src.config import get_azure_credential

    credential = get_azure_credential()
    project = AIProjectClient(endpoint=settings.foundry_project_endpoint, credential=credential)
    try:
        agents = {str(agent.name): agent for agent in project.agents.list()}
        versions = {}
        for role, name in names.items():
            agent = agents.get(name)
            latest = getattr(getattr(agent, "versions", None), "latest", None)
            versions[role] = {
                "name": name,
                "version": str(getattr(latest, "version", "") or ""),
                "id": str(getattr(latest, "id", "") or getattr(agent, "id", "") or ""),
            }
        missing = sorted(role for role, item in versions.items() if not item["version"])
        return {
            "versions": versions,
            "error": f"Missing latest version for: {', '.join(missing)}" if missing else "",
        }
    except Exception as exc:
        return {"versions": {}, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
    finally:
        project.close()
        credential.close()


def _decode_azd_value(raw_value: str) -> str:
    value = raw_value.strip()
    if value.startswith('"') and value.endswith('"'):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, str) else value
        except json.JSONDecodeError:
            return value[1:-1]
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    return value


def load_azd_environment() -> list[str]:
    """Load only required Foundry identifiers from the current azd environment."""
    child_env = dict(os.environ)
    child_env["AZURE_DEV_USER_AGENT"] = "microsoft_foundry_skill"
    process = subprocess.run(
        ["azd", "env", "get-values"],
        cwd=ROOT,
        env=child_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if process.returncode != 0:
        raise RuntimeError("azd env get-values failed; authenticate or select an azd environment")
    source = {}
    for line in process.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip():
            source[key.strip()] = _decode_azd_value(value)

    loaded = []
    for target, aliases in AZD_ENV_ALIASES.items():
        value = next((source.get(alias, "") for alias in aliases if source.get(alias)), "")
        if value:
            os.environ[target] = value
            loaded.append(target)
    get_settings.cache_clear()
    return sorted(loaded)


async def prepare_campaign(
    start: datetime,
    end: datetime,
    sample: int,
    seed: int,
    holdout_ratio: float,
    output_dir: Path,
) -> Path:
    """Freeze one date-range dataset and its diagnosis/holdout split."""
    if end < start:
        raise ValueError("end date cannot be before start date")
    if sample < 0:
        raise ValueError("sample must be zero (all updates) or a positive integer")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"campaign directory is not empty: {output_dir}")

    updates = await AzureUpdateParser().get_updates_by_date_range(start, end)
    if not updates:
        raise RuntimeError("no Azure Updates were found in the requested period")
    if sample == 0 or sample >= len(updates):
        selected = list(updates)
    else:
        selected = _stratified_sample(updates, sample, seed)
    selected.sort(key=_published_sort_key)

    splits = _split_cases(selected, holdout_ratio, seed)
    rows = [
        {
            "case_id": _case_id(update),
            "split": splits[_case_id(update)],
            "bucket": _bucket_of(update),
            "update": update.to_dict(),
        }
        for update in selected
    ]
    dataset_text = "".join(_canonical_json(row) + "\n" for row in rows)
    dataset_hash = _sha256_bytes(dataset_text.encode("utf-8"))
    campaign_id = f"qc-{start:%Y%m%d}-{end:%Y%m%d}-{dataset_hash[:10]}"

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "updates.jsonl").write_bytes(dataset_text.encode("utf-8"))
    manifest = {
        "schema_version": CAMPAIGN_SCHEMA_VERSION,
        "campaign_id": campaign_id,
        "created_at": datetime.now(UTC).isoformat(),
        "period": {"from": start.date().isoformat(), "to": end.date().isoformat()},
        "source_update_count": len(updates),
        "selected_update_count": len(selected),
        "full_period": len(selected) == len(updates),
        "sample": sample,
        "seed": seed,
        "holdout_ratio": holdout_ratio,
        "splits": dict(Counter(splits.values())),
        "dataset_file": "updates.jsonl",
        "dataset_sha256": dataset_hash,
        "rubric_version": RUBRIC_VERSION,
        "release_thresholds": RELEASE_THRESHOLDS,
        "hosted_contract_version": HOSTED_ANALYSIS_CONTRACT_VERSION,
        "research_basis": list(RESEARCH_BASIS),
        "source_lineage": _git_lineage(),
        "agent_lineage": _settings_lineage(),
    }
    (output_dir / "campaign.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return output_dir


def _load_campaign(campaign_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = json.loads((campaign_dir / "campaign.json").read_text(encoding="utf-8"))
    expected_contract = {
        "schema_version": CAMPAIGN_SCHEMA_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "release_thresholds": RELEASE_THRESHOLDS,
        "hosted_contract_version": HOSTED_ANALYSIS_CONTRACT_VERSION,
    }
    drifted = [key for key, expected in expected_contract.items() if manifest.get(key) != expected]
    if drifted:
        raise RuntimeError(
            "campaign manifest is incompatible with this runner: " + ", ".join(sorted(drifted))
        )
    dataset_path = campaign_dir / manifest["dataset_file"]
    dataset_bytes = dataset_path.read_bytes()
    if _sha256_bytes(dataset_bytes) != manifest["dataset_sha256"]:
        raise RuntimeError("campaign dataset hash mismatch")
    rows = [json.loads(line) for line in dataset_bytes.decode("utf-8").splitlines() if line]
    if len(rows) != manifest["selected_update_count"]:
        raise RuntimeError("campaign dataset count does not match its manifest")
    return manifest, rows


def _write_json_atomic(path: Path, value: Any) -> None:
    """Write one JSON artifact atomically so interruption cannot leave partial state."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_bytes(json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))
    temporary.replace(path)


def _load_checkpoint_records(
    run_dir: Path,
    expected_cases: dict[str, int],
) -> dict[str, dict[str, Any]]:
    """Load completed case records and reject stale or duplicate checkpoints."""
    records: dict[str, dict[str, Any]] = {}
    records_dir = run_dir / "records"
    if not records_dir.exists():
        return records
    for path in sorted(records_dir.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"checkpoint record is unreadable: {path.name} ({type(exc).__name__})"
            ) from exc
        if not isinstance(record, dict):
            raise RuntimeError(f"checkpoint record is not an object: {path.name}")
        case_id = str(record.get("case_id", ""))
        if case_id not in expected_cases:
            raise RuntimeError(f"checkpoint contains an unexpected case: {case_id}")
        if case_id in records:
            raise RuntimeError(f"checkpoint contains a duplicate case: {case_id}")
        index = record.get("index")
        if not isinstance(index, int) or isinstance(index, bool):
            raise RuntimeError(f"checkpoint index is invalid: {case_id}")
        if index != expected_cases[case_id]:
            raise RuntimeError(f"checkpoint index does not match campaign case: {case_id}")
        records[case_id] = record
    return records


def _remove_stale_temp_files(run_dir: Path) -> int:
    """Remove only atomic-write leftovers owned by the campaign run."""
    candidates = list(run_dir.glob("*.tmp"))
    records_dir = run_dir / "records"
    if records_dir.exists():
        candidates.extend(records_dir.glob("*.tmp"))
    attempts_dir = run_dir / "attempts"
    if attempts_dir.exists():
        candidates.extend(attempts_dir.glob("*.tmp"))
    removed = 0
    for path in candidates:
        if path.is_file():
            path.unlink()
            removed += 1
    return removed


def _same_source_lineage(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return all(
        left.get(key) == right.get(key)
        for key in (
            "commit",
            "dirty",
            "worktree_sha256",
            "untracked_file_count",
        )
    )


def _validate_resume_state(
    state: dict[str, Any],
    *,
    manifest: dict[str, Any],
    tag: str,
    runtime: str,
    split: str,
    concurrency: int,
    expected_cases: list[dict[str, Any]],
    source_lineage: dict[str, Any],
    agent_lineage: dict[str, Any],
) -> None:
    """Fail closed when a resumed run would mix experimental conditions."""
    expected_values = {
        "schema_version": CAMPAIGN_SCHEMA_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "campaign_id": manifest["campaign_id"],
        "dataset_sha256": manifest["dataset_sha256"],
        "tag": tag,
        "runtime": runtime,
        "split": split,
        "concurrency": concurrency,
    }
    mismatches = [key for key, expected in expected_values.items() if state.get(key) != expected]
    if mismatches:
        raise RuntimeError("resume run metadata changed: " + ", ".join(sorted(mismatches)))
    if state.get("expected_cases") != expected_cases:
        raise RuntimeError("resume run case set or ordering changed")
    if not _same_source_lineage(state.get("source_lineage", {}), source_lineage):
        raise RuntimeError("resume run source lineage changed")
    if _canonical_json(state.get("agent_lineage", {})) != _canonical_json(agent_lineage):
        raise RuntimeError("resume run Agent lineage changed")


def _quality_report_dict(report: Any) -> dict[str, Any]:
    return {
        "total_score": report.total_score,
        "max_score": report.max_score,
        "percentage": round(report.percentage, 3),
        "grade": report.grade,
        "category_scores": report.category_scores,
        "critical_issues": list(report.critical_issues),
        "improvement_suggestions": list(report.improvement_suggestions),
        "items": [
            {
                "name": item.name,
                "category": item.category,
                "score": item.score,
                "max_score": item.max_score,
                "reason": item.reason,
                "deductions": list(item.deductions),
            }
            for item in report.items
        ],
    }


def _safe_mean(values: list[float]) -> Optional[float]:
    return round(statistics.fmean(values), 3) if values else None


def _is_retryable_case_record(record: dict[str, Any]) -> bool:
    """Return whether a failed case can be retried without changing semantics."""
    if record.get("generation_failed"):
        return True
    error = str(record.get("error") or "").casefold()
    return bool(error and any(marker in error for marker in _TRANSIENT_CASE_ERROR_MARKERS))


def aggregate_records(records: list[dict[str, Any]], expected_count: int) -> dict[str, Any]:
    """Aggregate all independent quality layers and calculate release gates."""
    errors = [record for record in records if record.get("error")]
    generation_failures = [record for record in records if record.get("generation_failed")]
    scored = [
        record
        for record in records
        if not record.get("error") and not record.get("generation_failed")
    ]

    semantic_scores: list[float] = []
    dimension_values: dict[str, list[float]] = defaultdict(list)
    rule_scores: list[float] = []
    trajectory_scores: list[float] = []
    critical_flaws = 0
    trajectory_failures = 0
    blocked_actions = 0
    unverified_actions = 0
    missing_report_quality = 0
    missing_trajectory = 0
    missing_action_verification = 0
    dimension_error_count = 0
    rule_deductions: Counter[str] = Counter()
    trajectory_issues: Counter[str] = Counter()
    action_findings: Counter[str] = Counter()

    for record in scored:
        diagnostics = record.get("diagnostics") or {}
        report_quality = diagnostics.get("report_quality")
        if report_quality:
            semantic_scores.append(float(report_quality.get("weighted_score", 0.0)))
            critical_flaws += len(report_quality.get("critical_flaws") or [])
            for dimension in report_quality.get("dimensions") or []:
                dimension_values[dimension["key"]].append(float(dimension["score"]))
                if dimension.get("error"):
                    dimension_error_count += 1
        else:
            missing_report_quality += 1

        trajectory = diagnostics.get("trajectory")
        if trajectory:
            trajectory_scores.append(float(trajectory.get("score", 0.0)))
            if not trajectory.get("passed", False):
                trajectory_failures += 1
            for issue in trajectory.get("issues") or []:
                trajectory_issues[str(issue.get("code", "unknown"))] += 1
        else:
            missing_trajectory += 1

        verification = diagnostics.get("action_verification")
        action_count = int(record.get("action_items_count", 0))
        if verification:
            blocked_actions += int(verification.get("blocked", 0))
            unverified_actions += int(verification.get("unverified", 0))
            for code in verification.get("finding_codes") or []:
                action_findings[str(code)] += 1
        elif action_count:
            missing_action_verification += 1

        rule = record.get("rule_based")
        if rule:
            rule_scores.append(float(rule["percentage"]))
            for item in rule.get("items") or []:
                for deduction in item.get("deductions") or []:
                    rule_deductions[f"{item['name']}: {deduction}"] += 1

    dimension_averages = {
        key: round(statistics.fmean(values), 3)
        for key, values in sorted(dimension_values.items())
        if values
    }
    overall_semantic = _safe_mean(semantic_scores)
    overall_rule = _safe_mean(rule_scores)
    overall_trajectory = _safe_mean(trajectory_scores)
    semantic_case_floor = min(semantic_scores) if semantic_scores else None
    rule_case_floor = min(rule_scores) if rule_scores else None
    trajectory_case_floor = min(trajectory_scores) if trajectory_scores else None
    complete_count = len(records) - len(errors)
    coverage = complete_count / expected_count if expected_count else 0.0
    case_retry_count = sum(
        max(0, int(record.get("campaign_attempt", 1) or 1) - 1) for record in records
    )
    recovered_case_count = sum(
        1
        for record in records
        if int(record.get("campaign_attempt", 1) or 1) > 1
        and not record.get("error")
        and not record.get("generation_failed")
    )

    gates = {
        "complete_coverage": len(records) == expected_count and coverage == 1.0,
        "no_execution_errors": not errors,
        "no_generation_failures": not generation_failures,
        "semantic_diagnostics_complete": missing_report_quality == 0 and bool(scored),
        "no_dimension_errors": dimension_error_count == 0,
        "semantic_average_at_target": (
            overall_semantic is not None
            and overall_semantic >= RELEASE_THRESHOLDS["semantic_average"]
        ),
        "all_semantic_dimensions_production_excellent": (
            bool(dimension_averages)
            and all(
                score >= RELEASE_THRESHOLDS["semantic_dimension_average"]
                for score in dimension_averages.values()
            )
        ),
        "no_semantic_case_below_adequate": (
            semantic_case_floor is not None
            and semantic_case_floor >= RELEASE_THRESHOLDS["semantic_case_floor"]
        ),
        "no_critical_flaws": critical_flaws == 0,
        "rule_average_at_target": (
            overall_rule is not None and overall_rule >= RELEASE_THRESHOLDS["rule_average"]
        ),
        "no_rule_case_below_floor": (
            rule_case_floor is not None and rule_case_floor >= RELEASE_THRESHOLDS["rule_case_floor"]
        ),
        "trajectory_diagnostics_complete": missing_trajectory == 0 and bool(scored),
        "trajectory_average_at_target": (
            overall_trajectory is not None
            and overall_trajectory >= RELEASE_THRESHOLDS["trajectory_average"]
        ),
        "no_trajectory_case_below_floor": (
            trajectory_case_floor is not None
            and trajectory_case_floor >= RELEASE_THRESHOLDS["trajectory_case_floor"]
        ),
        "all_trajectories_pass": trajectory_failures == 0,
        "action_verification_complete": missing_action_verification == 0,
        "no_blocked_actions": blocked_actions == 0,
        "no_unverified_actions": unverified_actions == 0,
    }

    return {
        "expected_count": expected_count,
        "record_count": len(records),
        "scored_count": len(scored),
        "error_count": len(errors),
        "generation_failure_count": len(generation_failures),
        "coverage": round(coverage, 4),
        "semantic_average": overall_semantic,
        "semantic_case_floor": semantic_case_floor,
        "semantic_dimension_average": dimension_averages,
        "rule_average": overall_rule,
        "rule_case_floor": rule_case_floor,
        "trajectory_average": overall_trajectory,
        "trajectory_case_floor": trajectory_case_floor,
        "critical_flaw_count": critical_flaws,
        "trajectory_failure_count": trajectory_failures,
        "blocked_action_count": blocked_actions,
        "unverified_action_count": unverified_actions,
        "missing_report_quality": missing_report_quality,
        "missing_trajectory": missing_trajectory,
        "missing_action_verification": missing_action_verification,
        "dimension_error_count": dimension_error_count,
        "case_retry_count": case_retry_count,
        "recovered_case_count": recovered_case_count,
        "top_rule_deductions": rule_deductions.most_common(20),
        "trajectory_issues": trajectory_issues.most_common(),
        "action_findings": action_findings.most_common(),
        "gates": gates,
        "passed": all(gates.values()),
    }


def build_improvement_plan(
    run_summary: dict[str, Any], records: list[dict[str, Any]], trace_log: str
) -> str:
    """Build a deterministic, evidence-addressed starting plan for Copilot."""
    aggregate = run_summary["aggregate"]
    lines = [
        f"# Quality improvement plan: {run_summary['run_id']}",
        "",
        "This file is a diagnosis aid, not permission to edit every listed surface. ",
        "Choose one repeated defect, state one falsifiable root-cause hypothesis, and make one ",
        "small change before rerunning the diagnosis split and then the holdout split.",
        "",
        "## Gate status",
        "",
    ]
    for gate, passed in aggregate["gates"].items():
        lines.append(f"- [{'x' if passed else ' '}] `{gate}`")

    lines.extend(["", "## Weakest semantic dimensions", ""])
    dimensions = sorted(aggregate["semantic_dimension_average"].items(), key=lambda item: item[1])
    if not dimensions:
        lines.append("- Semantic diagnostics are missing; fix evaluation telemetry first.")
    for key, score in dimensions:
        lines.append(f"- `{key}`: {score:.3f}/5.000")
        lines.append(f"  {LEVER_BY_DIMENSION.get(key, 'Inspect the matching rubric feedback.')}")

    lines.extend(["", "## Repeated deterministic defects", ""])
    deductions = aggregate.get("top_rule_deductions") or []
    if deductions:
        lines.extend(f"- {count}x {text}" for text, count in deductions[:10])
    else:
        lines.append("- None in the scored set.")

    lines.extend(["", "## Process and safety findings", ""])
    for code, count in aggregate.get("trajectory_issues") or []:
        lines.append(f"- trajectory `{code}`: {count}")
    for code, count in aggregate.get("action_findings") or []:
        lines.append(f"- action safety `{code}`: {count}")
    if not aggregate.get("trajectory_issues") and not aggregate.get("action_findings"):
        lines.append("- No repeated process or action-safety finding in this run.")

    scored = [record for record in records if record.get("semantic_score") is not None]
    lines.extend(["", "## Lowest-scoring cases", ""])
    for record in sorted(scored, key=lambda item: item["semantic_score"])[:8]:
        lines.append(
            f"- `{record['case_id']}` {record['semantic_score']:.3f}: "
            f"{record['title']} (trace `{record['trace_id']}`)"
        )

    lines.extend(
        [
            "",
            "## Required validation order",
            "",
            "1. Inspect the named case artifacts and trace events; do not infer from the average.",
            "2. Run an unchanged A/A pair to establish the stochastic noise floor.",
            "3. Change one source-level cause and run the same diagnosis cases.",
            "4. Compare paired results; reject safety regressions and deltas inside the noise floor.",
            "5. Run the untouched holdout split, import check, and full test suite.",
            "6. Keep the change only when the target defect disappears and no gate regresses.",
            "",
            "## Trace source",
            "",
            f"- Local proxy/harness events: `{trace_log}`",
            "- Hosted and Prompt Agent events: query Application Insights by each `trace_id`.",
            "- Logs contain validated outcomes and tool fingerprints, not private chain-of-thought.",
            "",
        ]
    )
    return "\n".join(lines)


async def _evaluate_case(
    index: int,
    row: dict[str, Any],
    runtime: str,
    run_id: str,
    language: str,
    run_dir: Path,
    semaphore: asyncio.Semaphore,
    hosted_analyzer: Optional[HostedAgentAnalyzer],
    campaign_attempt: int = 1,
) -> dict[str, Any]:
    async with semaphore:
        update = _update_from_dict(row["update"])
        trace_id = (
            "qc-"
            + hashlib.sha256(
                f"{run_id}:{row['case_id']}:{campaign_attempt}".encode("utf-8")
            ).hexdigest()[:20]
        )
        started = time.monotonic()
        record: dict[str, Any] = {
            "index": index,
            "case_id": row["case_id"],
            "split": row["split"],
            "bucket": row["bucket"],
            "title": update.title,
            "url": update.link,
            "trace_id": trace_id,
            "runtime": runtime,
            "campaign_attempt": campaign_attempt,
        }
        local_analyzer: Optional[AzureUpdateAnalyzer] = None
        try:
            if runtime == "hosted":
                if hosted_analyzer is None:
                    raise RuntimeError("Hosted Agent analyzer is not configured")
                snapshot = await hosted_analyzer.evaluate_update(update, trace_id=trace_id)
                result = AnalysisResult.model_validate(snapshot.analysis)
                diagnostics = snapshot.diagnostics.model_dump(mode="json")
            else:
                local_analyzer = AzureUpdateAnalyzer()
                result = await local_analyzer.analyze_update(update, trace_id=trace_id)
                diagnostics = local_analyzer.get_last_run_diagnostics()

            generation_failed = str(result.relevance_reason).startswith(GENERATION_FAILURE_MARKER)
            email = EmailService().build_email_content(update, result, language)
            quality = ReportQualityEvaluator().evaluate(
                result,
                update,
                email.get("html_content", ""),
                language,
            )
            markdown = GEvalJudge.render_report_markdown(result, update, language)
            artifact_name = f"{index:03d}_{row['case_id']}"
            (run_dir / f"report_{artifact_name}.md").write_text(markdown, encoding="utf-8")
            (run_dir / f"analysis_{artifact_name}.json").write_text(
                json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            report_quality = diagnostics.get("report_quality") or {}
            record.update(
                {
                    "generation_failed": generation_failed,
                    "relevance": result.relevance.value,
                    "update_category": result.update_category,
                    "affected_resources_count": len(result.affected_resources),
                    "action_items_count": len(result.action_items),
                    "semantic_score": report_quality.get("weighted_score"),
                    "diagnostics": diagnostics,
                    "rule_based": _quality_report_dict(quality),
                    "elapsed_s": round(time.monotonic() - started, 2),
                }
            )
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {str(exc)[:500]}"
            record["elapsed_s"] = round(time.monotonic() - started, 2)
        finally:
            if local_analyzer is not None:
                close = getattr(local_analyzer, "close", None)
                if callable(close):
                    outcome = close()
                    if asyncio.iscoroutine(outcome):
                        await outcome
        return record


async def run_campaign(
    campaign_dir: Path,
    tag: str,
    runtime: str,
    split: str,
    concurrency: int,
    use_azd_env: bool = False,
    resume_run: Optional[Path] = None,
) -> Path:
    """Run one immutable measurement over a frozen campaign dataset."""
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    loaded_azd_keys = load_azd_environment() if use_azd_env else []
    manifest, rows = _load_campaign(campaign_dir)
    if split != "all":
        rows = [row for row in rows if row["split"] == split]
    if not rows:
        raise RuntimeError(f"campaign split has no cases: {split}")

    if runtime == "local":
        os.environ["GEVAL_ENABLED"] = "true"
        os.environ["GEVAL_RUNTIME_ENABLED"] = "true"
        os.environ["TRAJECTORY_EVAL_ENABLED"] = "true"
        os.environ["ACTION_VERIFICATION_ENABLED"] = "true"
        get_settings.cache_clear()
    settings = get_settings()
    if runtime == "hosted" and not settings.use_hosted_agent:
        raise RuntimeError(
            "Hosted runtime requires FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_HOSTED_AGENT_NAME"
        )

    agent_versions = await asyncio.to_thread(_resolve_agent_versions)
    agent_lineage = _settings_lineage()
    agent_lineage["resolved_versions"] = agent_versions["versions"]
    agent_lineage["version_resolution_error"] = agent_versions["error"]
    version_lineage_complete = not agent_versions["error"]
    if not version_lineage_complete:
        raise RuntimeError(
            "quality campaign requires immutable Agent version lineage: " + agent_versions["error"]
        )
    source_lineage = _git_lineage()
    indexed_rows = list(enumerate(rows, start=1))
    expected_cases = [{"index": index, "case_id": row["case_id"]} for index, row in indexed_rows]
    expected_case_index = {item["case_id"]: item["index"] for item in expected_cases}

    if resume_run is not None:
        run_dir = resume_run.resolve()
        if not run_dir.is_dir():
            raise RuntimeError(f"resume run directory does not exist: {run_dir}")
        state_path = run_dir / "run.json"
        if not state_path.exists():
            raise RuntimeError("resume run has no run.json checkpoint metadata")
        if (run_dir / "summary.json").exists():
            return run_dir
        run_state = json.loads(state_path.read_text(encoding="utf-8"))
        _validate_resume_state(
            run_state,
            manifest=manifest,
            tag=tag,
            runtime=runtime,
            split=split,
            concurrency=concurrency,
            expected_cases=expected_cases,
            source_lineage=source_lineage,
            agent_lineage=agent_lineage,
        )
        run_id = str(run_state["run_id"])
        started_at = _parse_datetime(str(run_state["started_at"]))
        if started_at is None:
            raise RuntimeError("resume run has an invalid started_at value")
    else:
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        safe_tag = "".join(char if char.isalnum() or char in "-_" else "-" for char in tag) or "run"
        run_id = f"{safe_tag}-{split}-{timestamp}"
        run_dir = campaign_dir / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        started_at = datetime.now(UTC)
        run_state = {
            "schema_version": CAMPAIGN_SCHEMA_VERSION,
            "rubric_version": RUBRIC_VERSION,
            "run_id": run_id,
            "tag": tag,
            "runtime": runtime,
            "split": split,
            "concurrency": concurrency,
            "campaign_id": manifest["campaign_id"],
            "dataset_sha256": manifest["dataset_sha256"],
            "started_at": started_at.isoformat(),
            "source_lineage": source_lineage,
            "agent_lineage": agent_lineage,
            "version_lineage_complete": True,
            "expected_cases": expected_cases,
            "status": "running",
        }
        _write_json_atomic(run_dir / "run.json", run_state)

    from src.logging_config import setup_logging

    os.environ["LOG_LEVEL"] = "INFO"
    os.environ["LOG_CONSOLE_LEVEL"] = "CRITICAL"
    trace_log = setup_logging(
        console_level="CRITICAL", file_enabled=True, file_dir=str(run_dir / "logs")
    )
    stale_temp_files_removed = _remove_stale_temp_files(run_dir)
    checkpoint_records = _load_checkpoint_records(run_dir, expected_case_index)
    progress_path = run_dir / "progress.json"
    previous_active_elapsed = 0.0
    if progress_path.exists():
        previous_active_elapsed = float(
            json.loads(progress_path.read_text(encoding="utf-8")).get("active_elapsed_s", 0.0)
        )
    hosted_analyzer = HostedAgentAnalyzer(settings) if runtime == "hosted" else None
    semaphore = asyncio.Semaphore(concurrency)
    segment_started = time.monotonic()

    def pending_attempts() -> list[tuple[int, dict[str, Any], int]]:
        pending = []
        for index, row in indexed_rows:
            record = checkpoint_records.get(row["case_id"])
            if record is None:
                pending.append((index, row, 1))
                continue
            attempt = int(record.get("campaign_attempt", 1) or 1)
            if _is_retryable_case_record(record) and attempt < MAX_CASE_ATTEMPTS:
                pending.append((index, row, attempt + 1))
        return pending

    try:
        current_attempts = pending_attempts()
        while current_attempts:
            tasks = [
                asyncio.create_task(
                    _evaluate_case(
                        index,
                        row,
                        runtime,
                        run_id,
                        settings.report_language,
                        run_dir,
                        semaphore,
                        hosted_analyzer,
                        campaign_attempt,
                    )
                )
                for index, row, campaign_attempt in current_attempts
            ]
            for task in asyncio.as_completed(tasks):
                record = await task
                case_id = str(record["case_id"])
                attempt = int(record.get("campaign_attempt", 1) or 1)
                record_name = f"{int(record['index']):03d}_{case_id}"
                _write_json_atomic(
                    run_dir / "attempts" / f"{record_name}_attempt{attempt}.json",
                    record,
                )
                _write_json_atomic(
                    run_dir / "records" / f"{record_name}.json",
                    record,
                )
                checkpoint_records[case_id] = record
                retryable_cases = sum(
                    1
                    for item in checkpoint_records.values()
                    if _is_retryable_case_record(item)
                    and int(item.get("campaign_attempt", 1) or 1) < MAX_CASE_ATTEMPTS
                )
                _write_json_atomic(
                    progress_path,
                    {
                        "status": "running",
                        "completed_cases": len(checkpoint_records),
                        "expected_cases": len(rows),
                        "retryable_cases": retryable_cases,
                        "last_case_id": case_id,
                        "updated_at": datetime.now(UTC).isoformat(),
                        "active_elapsed_s": round(
                            previous_active_elapsed + time.monotonic() - segment_started,
                            2,
                        ),
                    },
                )
            current_attempts = pending_attempts()
    finally:
        if hosted_analyzer is not None:
            await hosted_analyzer.close()

    records = sorted(checkpoint_records.values(), key=lambda record: record["index"])
    aggregate = aggregate_records(records, len(rows))
    completed_source_lineage = _git_lineage()
    completed_agent_versions = await asyncio.to_thread(_resolve_agent_versions)
    source_lineage_stable = _same_source_lineage(source_lineage, completed_source_lineage)
    agent_version_lineage_stable = bool(
        not completed_agent_versions["error"]
        and _canonical_json(agent_versions["versions"])
        == _canonical_json(completed_agent_versions["versions"])
    )
    run_valid = source_lineage_stable and agent_version_lineage_stable
    active_elapsed_s = round(
        previous_active_elapsed + time.monotonic() - segment_started,
        2,
    )
    summary = {
        "schema_version": CAMPAIGN_SCHEMA_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "run_id": run_id,
        "tag": tag,
        "runtime": runtime,
        "split": split,
        "concurrency": concurrency,
        "campaign_id": manifest["campaign_id"],
        "dataset_sha256": manifest["dataset_sha256"],
        "started_at": started_at.isoformat(),
        "completed_at": datetime.now(UTC).isoformat(),
        "elapsed_s": active_elapsed_s,
        "source_lineage": source_lineage,
        "completed_source_lineage": completed_source_lineage,
        "source_lineage_stable": source_lineage_stable,
        "agent_lineage": agent_lineage,
        "completed_agent_versions": completed_agent_versions,
        "agent_version_lineage_stable": agent_version_lineage_stable,
        "version_lineage_complete": version_lineage_complete,
        "run_valid": run_valid,
        "resumed": resume_run is not None,
        "stale_temp_files_removed": stale_temp_files_removed,
        "azd_environment_keys_loaded": loaded_azd_keys,
        "aggregate": aggregate,
        "release_eligible": bool(
            split == "all"
            and runtime == "hosted"
            and manifest["full_period"]
            and version_lineage_complete
            and run_valid
            and aggregate["passed"]
        ),
        "records": records,
    }
    _write_json_atomic(run_dir / "summary.json", summary)
    (run_dir / "trace_ids.jsonl").write_bytes(
        "".join(
            _canonical_json(
                {
                    "case_id": record["case_id"],
                    "trace_id": record["trace_id"],
                    "title": record["title"],
                }
            )
            + "\n"
            for record in records
        ).encode("utf-8")
    )
    plan = build_improvement_plan(
        summary,
        list(records),
        str(trace_log or run_dir / "logs"),
    )
    (run_dir / "improvement_plan.md").write_text(plan, encoding="utf-8")
    _write_json_atomic(
        progress_path,
        {
            "status": "completed",
            "completed_cases": len(records),
            "expected_cases": len(rows),
            "updated_at": summary["completed_at"],
            "active_elapsed_s": active_elapsed_s,
        },
    )
    _write_json_atomic(
        run_dir / "run.json",
        {
            **run_state,
            "status": "completed",
            "completed_at": summary["completed_at"],
            "run_valid": run_valid,
        },
    )
    return run_dir


def _load_run(path: Path) -> dict[str, Any]:
    summary_path = path / "summary.json" if path.is_dir() else path
    return json.loads(summary_path.read_text(encoding="utf-8"))


def _paired_stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "mean": None, "stdev": None, "ci95": [None, None]}
    mean = statistics.fmean(values)
    if len(values) < 2:
        return {
            "count": 1,
            "mean": round(mean, 4),
            "stdev": None,
            "ci95": [None, None],
        }
    stdev = statistics.stdev(values)
    half_width = 1.96 * stdev / math.sqrt(len(values))
    return {
        "count": len(values),
        "mean": round(mean, 4),
        "stdev": round(stdev, 4),
        "ci95": [round(mean - half_width, 4), round(mean + half_width, 4)],
        "ci95_half_width": round(half_width, 4),
    }


def compare_runs(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    noise_floor: float = 0.0,
    mode: str = "candidate",
) -> dict[str, Any]:
    """Compare two runs case-by-case and reject unsafe or noisy wins."""
    if baseline["campaign_id"] != candidate["campaign_id"]:
        raise ValueError("runs belong to different campaigns")
    if baseline["dataset_sha256"] != candidate["dataset_sha256"]:
        raise ValueError("runs use different dataset snapshots")
    if baseline["split"] != candidate["split"]:
        raise ValueError("runs use different campaign splits")
    if mode not in ("aa", "candidate"):
        raise ValueError("comparison mode must be 'aa' or 'candidate'")
    if baseline.get("run_valid") is False or candidate.get("run_valid") is False:
        raise ValueError("runs with changed source or Agent lineage cannot be compared")

    source_changed = baseline.get("source_lineage", {}).get("worktree_sha256") != candidate.get(
        "source_lineage", {}
    ).get("worktree_sha256")
    baseline_versions = baseline.get("agent_lineage", {}).get("resolved_versions", {})
    candidate_versions = candidate.get("agent_lineage", {}).get("resolved_versions", {})
    agent_versions_changed = _canonical_json(baseline_versions) != _canonical_json(
        candidate_versions
    )
    runtime_changed = baseline.get("runtime") != candidate.get("runtime")
    concurrency_changed = baseline.get("concurrency") != candidate.get("concurrency")
    changed_axes = [
        name
        for name, changed in (
            ("source", source_changed),
            ("agent_versions", agent_versions_changed),
            ("runtime", runtime_changed),
            ("concurrency", concurrency_changed),
        )
        if changed
    ]
    if mode == "aa":
        if baseline.get("run_valid") is not True or candidate.get("run_valid") is not True:
            raise ValueError("A/A runs require stable source and Agent lineage")
        source_hash = baseline.get("source_lineage", {}).get("worktree_sha256")
        if source_hash in (None, "", "unknown"):
            raise ValueError("A/A runs require a resolved source worktree hash")
        if not baseline.get("version_lineage_complete") or not candidate.get(
            "version_lineage_complete"
        ):
            raise ValueError("A/A runs require complete immutable Agent version lineage")
        if changed_axes:
            raise ValueError(f"A/A runs changed experimental axes: {', '.join(changed_axes)}")

    baseline_records = {record["case_id"]: record for record in baseline["records"]}
    candidate_records = {record["case_id"]: record for record in candidate["records"]}
    baseline_case_ids = set(baseline_records)
    candidate_case_ids = set(candidate_records)
    if baseline_case_ids != candidate_case_ids:
        raise ValueError("runs contain different case sets")
    paired_ids = sorted(baseline_case_ids)
    deltas = []
    rule_deltas = []
    trajectory_deltas = []
    excluded_pair_count = 0
    dimension_deltas: dict[str, list[float]] = defaultdict(list)
    for case_id in paired_ids:
        before = baseline_records[case_id]
        after = candidate_records[case_id]
        if (
            before.get("error")
            or after.get("error")
            or before.get("generation_failed")
            or after.get("generation_failed")
            or before.get("semantic_score") is None
            or after.get("semantic_score") is None
        ):
            excluded_pair_count += 1
            continue
        deltas.append(float(after["semantic_score"]) - float(before["semantic_score"]))
        before_rule = (before.get("rule_based") or {}).get("percentage")
        after_rule = (after.get("rule_based") or {}).get("percentage")
        if before_rule is not None and after_rule is not None:
            rule_deltas.append(float(after_rule) - float(before_rule))
        before_trajectory = (before.get("diagnostics") or {}).get("trajectory") or {}
        after_trajectory = (after.get("diagnostics") or {}).get("trajectory") or {}
        if before_trajectory.get("score") is not None and after_trajectory.get("score") is not None:
            trajectory_deltas.append(
                float(after_trajectory["score"]) - float(before_trajectory["score"])
            )
        before_dims = {
            item["key"]: float(item["score"])
            for item in (before.get("diagnostics") or {})
            .get("report_quality", {})
            .get("dimensions", [])
        }
        after_dims = {
            item["key"]: float(item["score"])
            for item in (after.get("diagnostics") or {})
            .get("report_quality", {})
            .get("dimensions", [])
        }
        for key in set(before_dims) & set(after_dims):
            dimension_deltas[key].append(after_dims[key] - before_dims[key])

    overall = _paired_stats(deltas)
    dimensions = {key: _paired_stats(values) for key, values in sorted(dimension_deltas.items())}
    baseline_agg = baseline["aggregate"]
    candidate_agg = candidate["aggregate"]
    regressions = []
    for metric in (
        "error_count",
        "generation_failure_count",
        "critical_flaw_count",
        "trajectory_failure_count",
        "blocked_action_count",
        "unverified_action_count",
        "dimension_error_count",
        "missing_report_quality",
        "missing_trajectory",
        "missing_action_verification",
    ):
        if candidate_agg[metric] > baseline_agg[metric]:
            regressions.append(
                f"{metric} increased from {baseline_agg[metric]} to {candidate_agg[metric]}"
            )
    for key, stats in dimensions.items():
        if stats["mean"] is not None and stats["mean"] < -0.05:
            regressions.append(f"{key} mean regressed by {stats['mean']:.3f}")

    effect_threshold = max(0.05, noise_floor)
    mean_delta = overall["mean"]
    lower_ci = overall["ci95"][0]
    if regressions:
        verdict = "regression"
    elif mean_delta is None:
        verdict = "invalid"
    elif mean_delta > effect_threshold and (lower_ci is None or lower_ci >= 0.0):
        verdict = "improved"
    else:
        verdict = "inconclusive"

    aa_noise_floor = max(
        abs(mean_delta or 0.0),
        float(overall.get("ci95_half_width") or 0.0),
    )
    return {
        "campaign_id": baseline["campaign_id"],
        "split": baseline["split"],
        "baseline_run": baseline["run_id"],
        "candidate_run": candidate["run_id"],
        "mode": mode,
        "changed_axes": changed_axes,
        "confounded": mode == "candidate" and len(changed_axes) > 1,
        "case_count": len(paired_ids),
        "paired_case_count": overall["count"],
        "excluded_pair_count": excluded_pair_count,
        "overall_delta": overall,
        "rule_delta": _paired_stats(rule_deltas),
        "trajectory_delta": _paired_stats(trajectory_deltas),
        "dimension_delta": dimensions,
        "noise_floor_used": noise_floor,
        "effect_threshold": effect_threshold,
        "estimated_aa_noise_floor": round(aa_noise_floor, 4),
        "regressions": regressions,
        "verdict": verdict,
    }


def _parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="freeze a date-range evaluation dataset")
    prepare.add_argument("--from", dest="date_from", required=True, help="start date YYYY-MM-DD")
    prepare.add_argument("--to", dest="date_to", required=True, help="end date YYYY-MM-DD")
    prepare.add_argument(
        "--sample", type=int, default=0, help="stratified sample size; 0 evaluates all updates"
    )
    prepare.add_argument("--seed", type=int, default=42)
    prepare.add_argument("--holdout-ratio", type=float, default=0.25)
    prepare.add_argument("--output", type=Path)

    run = subparsers.add_parser("run", help="run one frozen campaign measurement")
    run.add_argument("--campaign", type=Path, required=True)
    run.add_argument("--tag", required=True)
    run.add_argument("--runtime", choices=("hosted", "local"), default="hosted")
    run.add_argument("--split", choices=("diagnosis", "holdout", "all"), default="diagnosis")
    run.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="parallel analyses; default 1 because each analysis already fans out specialists",
    )
    run.add_argument(
        "--use-azd-env",
        action="store_true",
        help="load required Foundry identifiers from the current azd environment in memory",
    )
    run.add_argument(
        "--resume-run",
        type=Path,
        help="resume an interrupted run directory after strict lineage validation",
    )

    compare = subparsers.add_parser("compare", help="compare paired campaign runs")
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--candidate", type=Path, required=True)
    compare.add_argument("--noise-floor", type=float, default=0.0)
    compare.add_argument("--mode", choices=("aa", "candidate"), default="candidate")
    compare.add_argument("--output", type=Path)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    if args.command == "prepare":
        start = _parse_date(args.date_from)
        end = _parse_date(args.date_to)
        output = args.output or (
            EVAL_OUTPUT_ROOT
            / f"campaign_{start:%Y%m%d}_{end:%Y%m%d}_{datetime.now(UTC):%Y%m%d_%H%M%S}"
        )
        path = asyncio.run(
            prepare_campaign(
                start,
                end,
                args.sample,
                args.seed,
                args.holdout_ratio,
                output,
            )
        )
        print(path)
        return 0

    if args.command == "run":
        path = asyncio.run(
            run_campaign(
                args.campaign,
                args.tag,
                args.runtime,
                args.split,
                args.concurrency,
                args.use_azd_env,
                args.resume_run,
            )
        )
        print(path)
        return 0

    baseline = _load_run(args.baseline)
    candidate = _load_run(args.candidate)
    comparison = compare_runs(baseline, candidate, args.noise_floor, args.mode)
    candidate_root = args.candidate if args.candidate.is_dir() else args.candidate.parent
    output = args.output or candidate_root / f"comparison_vs_{baseline['run_id']}.json"
    output.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(comparison, ensure_ascii=False, indent=2))
    return 1 if comparison["verdict"] == "regression" else 0


if __name__ == "__main__":
    raise SystemExit(main())
