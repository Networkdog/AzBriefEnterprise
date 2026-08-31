"""Deterministic scale and correctness evaluation for the analysis archive."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.agent.analyzer import AnalysisResult, RelevanceStatus, UrgencyLevel
from src.archive.models import (
    ArchiveAnalysisResultV1,
    ArchiveDocumentV1,
    ArchiveQuery,
    ArchiveSource,
    ArchiveUpdateV1,
)
from src.archive.service import create_archive_id
from src.services.archive import FileArchiveStore

UTC = timezone.utc
SERVICES = (
    "Azure Kubernetes Service",
    "Azure SQL Database",
    "Azure Storage",
    "Azure Key Vault",
)
CATEGORIES = (
    "retirement",
    "feature_change",
    "new_feature",
    "new_service",
    "region_expansion",
    "preview",
    "sdk_tooling",
    "pricing",
)
LEVELS = ("high", "medium", "low")
FORBIDDEN_PII_KEYS = frozenset({"email", "subscriber", "recipient", "principal"})
PERSONALIZED_ARCHIVE_KEYS = frozenset({"job_relevance"})
_EMAIL_LIKE_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)


def _document(index: int, base: datetime) -> ArchiveDocumentV1:
    analyzed_at = base + timedelta(seconds=index)
    update_id = f"update-{index // 3:05d}"
    service = SERVICES[index % len(SERVICES)]
    category = CATEGORIES[index % len(CATEGORIES)]
    level = LEVELS[index % len(LEVELS)]
    return ArchiveDocumentV1(
        archive_id=create_archive_id(analyzed_at, f"{index:032x}"),
        analyzed_at=analyzed_at,
        source=ArchiveSource.SCHEDULED_DIGEST,
        run_id=f"run-{index // 25:05d}",
        hosted_agent_name="azbrief-analysis-hosted",
        trace_id=f"trace-{index:08d}",
        report_language="ko" if index % 2 == 0 else "en",
        update=ArchiveUpdateV1(
            id=update_id,
            title=f"{service} synthetic update {index}",
            description="Deterministic archive evaluation fixture.",
            link=f"https://azure.microsoft.com/updates?id={update_id}",
            published_date=analyzed_at - timedelta(days=1),
            categories=[service],
            azure_services=[service],
            update_type="General Availability",
        ),
        result=ArchiveAnalysisResultV1.model_validate(
            AnalysisResult(
                update_id=update_id,
                update_title=f"{service} synthetic update {index}",
                update_category=category,
                urgency=UrgencyLevel(level),
                importance=level,
                impact_level=LEVELS[(index + 1) % len(LEVELS)],
                job_relevance=LEVELS[(index + 2) % len(LEVELS)],
                relevance=RelevanceStatus.RELEVANT,
                one_line_summary=f"Synthetic summary {index} for {service}",
                relevance_reason="Deterministic relevance evidence.",
                affected_resources=[],
                impact_summary="Deterministic impact.",
                recommendations=[],
                reference_docs=[],
                should_notify=True,
            ).model_dump(mode="json", exclude={"job_relevance"})
        ),
    )


async def _read_all(store: FileArchiveStore, query: ArchiveQuery) -> tuple[list, list[float], int]:
    items = []
    latencies_ms = []
    max_response_bytes = 0
    cursor = ""
    seen_cursors = set()
    while True:
        started = time.perf_counter()
        page = await store.list(query.model_copy(update={"cursor": cursor}))
        latencies_ms.append((time.perf_counter() - started) * 1_000)
        max_response_bytes = max(max_response_bytes, len(page.model_dump_json().encode("utf-8")))
        items.extend(page.items)
        if not page.has_more:
            break
        if not page.next_cursor or page.next_cursor in seen_cursors:
            raise RuntimeError("archive cursor did not advance")
        seen_cursors.add(page.next_cursor)
        cursor = page.next_cursor
    return items, latencies_ms, max_response_bytes


def _forbidden_key_count(value: Any) -> int:
    if isinstance(value, dict):
        count = sum(1 for key in value if str(key).casefold() in FORBIDDEN_PII_KEYS)
        return count + sum(_forbidden_key_count(item) for item in value.values())
    if isinstance(value, list):
        return sum(_forbidden_key_count(item) for item in value)
    return 0


def _personalized_key_count(value: Any) -> int:
    if isinstance(value, dict):
        count = sum(1 for key in value if str(key).casefold() in PERSONALIZED_ARCHIVE_KEYS)
        return count + sum(_personalized_key_count(item) for item in value.values())
    if isinstance(value, list):
        return sum(_personalized_key_count(item) for item in value)
    return 0


def _email_like_value_count(value: Any) -> int:
    if isinstance(value, dict):
        return sum(_email_like_value_count(item) for item in value.values())
    if isinstance(value, list):
        return sum(_email_like_value_count(item) for item in value)
    if isinstance(value, str):
        return len(_EMAIL_LIKE_RE.findall(value))
    return 0


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int((len(ordered) - 1) * percentile))
    return ordered[index]


async def evaluate_archive(record_count: int, output_root: Path) -> dict[str, Any]:
    """Generate a corpus, traverse it, and write deterministic quality metrics."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    with tempfile.TemporaryDirectory(prefix="azbrief-archive-eval-") as directory:
        store = FileArchiveStore(directory)
        generated = [_document(index, base) for index in range(record_count)]
        for document in generated:
            await store.put(document)

        all_items, page_latencies, max_response_bytes = await _read_all(
            store, ArchiveQuery(limit=50)
        )
        aks_items, filter_latencies, filter_response_bytes = await _read_all(
            store,
            ArchiveQuery(service="Azure Kubernetes Service", limit=50),
        )

        archive_ids = [item.archive_id for item in all_items]
        expected_order = sorted(document.archive_id for document in generated)
        expected_aks = {
            document.archive_id
            for document in generated
            if "Azure Kubernetes Service" in document.update.azure_services
        }
        actual_aks = {item.archive_id for item in aks_items}
        integrity_passed = 0
        pii_key_count = 0
        personalized_key_count = 0
        email_like_value_count = 0
        for document in generated:
            restored = await store.get(document.archive_id)
            if restored == document:
                integrity_passed += 1
            pii_key_count += _forbidden_key_count(document.model_dump(mode="json"))
            personalized_key_count += _personalized_key_count(document.model_dump(mode="json"))
            email_like_value_count += _email_like_value_count(document.model_dump(mode="json"))

    latencies = page_latencies + filter_latencies
    metrics = {
        "record_count": record_count,
        "listed_count": len(all_items),
        "duplicate_count": len(archive_ids) - len(set(archive_ids)),
        "ordering_mismatch_count": sum(
            1 for actual, expected in zip(archive_ids, expected_order) if actual != expected
        ),
        "filter_false_negative_count": len(expected_aks - actual_aks),
        "filter_false_positive_count": len(actual_aks - expected_aks),
        "integrity_pass_rate": integrity_passed / record_count if record_count else 1.0,
        "pii_key_count": pii_key_count,
        "personalized_key_count": personalized_key_count,
        "email_like_value_count": email_like_value_count,
        "list_p95_ms": round(_percentile(latencies, 0.95), 3),
        "max_response_bytes": max(max_response_bytes, filter_response_bytes),
    }
    gates = {
        "complete_listing": metrics["listed_count"] == record_count,
        "no_duplicates": metrics["duplicate_count"] == 0,
        "stable_order": metrics["ordering_mismatch_count"] == 0,
        "exact_filtering": (
            metrics["filter_false_negative_count"] == 0
            and metrics["filter_false_positive_count"] == 0
        ),
        "schema_integrity": metrics["integrity_pass_rate"] == 1.0,
        "no_pii_keys": metrics["pii_key_count"] == 0,
        "no_personalized_keys": metrics["personalized_key_count"] == 0,
        "no_email_like_values": metrics["email_like_value_count"] == 0,
        "bounded_response": metrics["max_response_bytes"] < 1_000_000,
        "bounded_file_p95": metrics["list_p95_ms"] < 1_000,
    }
    result = {"metrics": metrics, "gates": gates, "passed": all(gates.values())}
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "metrics.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=int, default=10_000)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.records < 1:
        parser.error("--records must be at least 1")
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    output_dir = args.output_dir or Path("eval_runs") / f"archive_{timestamp}"
    result = asyncio.run(evaluate_archive(args.records, output_dir))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
