"""Batch report-quality evaluation across a date range (G-Eval, real data).

Complements ``scripts/evaluate_report.py`` (single update, self-improvement loop)
with a **fleet-level** view: sample N updates across a date range, generate and
score each one, then aggregate per-dimension averages so prompt/code changes can
be attributed to a measurable shift rather than one noisy sample.

Usage::

    # Baseline over the last 6 months, 12 stratified samples
    python -m scripts.evaluate_batch --months 6 --sample 12 --tag baseline

    # Re-run the exact same sample after a fix (same seed → same updates)
    python -m scripts.evaluate_batch --months 6 --sample 12 --tag after-fix1

    # Explicit range + higher concurrency
    python -m scripts.evaluate_batch --from 2026-02-01 --to 2026-07-31 --sample 20 --concurrency 3

Artifacts land in ``eval_runs/batch_<tag>_<timestamp>/`` (gitignored):
``summary.json`` (aggregate + per-update scores) and ``report_<n>.md`` per update.

Note:
    Each concurrent analysis uses its **own** ``AzureUpdateAnalyzer`` instance.
    The analyzer stores per-run evidence on itself (``_last_task_results``), so a
    shared instance would cross-contaminate the judge's evidence context.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Suppress the analyzer's per-phase console output — a batch run interleaves
# many analyses, so the narration is unreadable noise. Must be set before the
# analyzer module is imported (it reads the flag at import time).
os.environ.setdefault("AZBRIEF_VERBOSE", "false")

from src.agent.analyzer import AzureUpdateAnalyzer  # noqa: E402
from src.agent.geval import GEvalJudge, GEvalReport  # noqa: E402
from src.agent.resilience import TOOL_RESULT_BUDGET_CHARS  # noqa: E402
from src.config import get_settings  # noqa: E402
from src.rss.parser import AzureUpdate, AzureUpdateParser  # noqa: E402

EVAL_OUTPUT_ROOT = Path(__file__).resolve().parent.parent / "eval_runs"

# The analyzer degrades gracefully when the report LLM call fails: it emits a
# placeholder ``detailed_analysis`` starting with this marker instead of raising.
# That is correct for production (a partial email beats no email) but such a
# report is **not a measurable sample** — the judge floors every dimension at 3,
# which silently drags the fleet average down and can be misread as a quality
# regression. Detect it and exclude it from the aggregate.
_GENERATION_FAILURE_MARKER = "Report generation failed"


def _bucket_of(update: AzureUpdate) -> str:
    """Classify an update into a coarse stratum for balanced sampling."""
    text = f"{update.title} {update.update_type or ''}".lower()
    if any(k in text for k in ("retir", "deprecat", "end of support", "end of life")):
        return "retirement"
    if "breaking" in text:
        return "breaking"
    if any(k in text for k in ("security", "vulnerab", "cve", "defender", "encryption")):
        return "security"
    if any(k in text for k in ("generally available", "now available", " ga ", "ga:")):
        return "ga"
    if "preview" in text:
        return "preview"
    return "other"


def _stratified_sample(updates: list[AzureUpdate], size: int, seed: int) -> list[AzureUpdate]:
    """Pick `size` updates spread across strata (round-robin, deterministic).

    Round-robin over shuffled per-bucket queues keeps the mix representative even
    when one bucket (usually "other") dominates the feed.
    """
    rng = random.Random(seed)
    buckets: dict[str, list[AzureUpdate]] = defaultdict(list)
    for u in updates:
        buckets[_bucket_of(u)].append(u)
    for items in buckets.values():
        rng.shuffle(items)

    ordered_names = sorted(buckets.keys())
    picked: list[AzureUpdate] = []
    idx = 0
    while len(picked) < size and any(buckets[n] for n in ordered_names):
        name = ordered_names[idx % len(ordered_names)]
        if buckets[name]:
            picked.append(buckets[name].pop())
        idx += 1
    return picked


def _build_evidence(analyzer: AzureUpdateAnalyzer) -> str:
    """Assemble the same ground truth the report was generated from.

    Uses ``TOOL_RESULT_BUDGET_CHARS`` — the identical budget the analyzer used —
    so grounded resource names past a smaller cutoff do not trigger false
    faithfulness penalties.
    """
    parts: list[str] = []
    if getattr(analyzer, "_last_resource_summary", ""):
        parts.append("### Administrator resource summary\n" + analyzer._last_resource_summary)
    task_results = getattr(analyzer, "_last_task_results", {}) or {}
    if task_results:
        lines = ["### Tool / Resource Graph results"]
        for tid, res in task_results.items():
            lines.append(f"- **{tid}**: {str(res)[:TOOL_RESULT_BUDGET_CHARS]}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


async def _evaluate_one(
    index: int,
    update: AzureUpdate,
    language: str,
    target: Optional[float],
    run_dir: Path,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    """Generate and score a single update. Never raises — errors are recorded."""
    async with semaphore:
        t0 = time.monotonic()
        record: dict[str, Any] = {
            "index": index,
            "title": update.title,
            "url": update.link,
            "bucket": _bucket_of(update),
            "published": update.published_date.isoformat() if update.published_date else None,
        }
        try:
            # Own analyzer per task: per-run evidence lives on the instance.
            analyzer = AzureUpdateAnalyzer()
            judge = GEvalJudge(target_score=target)

            result = await analyzer.analyze_update(update)
            generation_failed = str(getattr(result, "detailed_analysis", "")).startswith(
                _GENERATION_FAILURE_MARKER
            )
            report_markdown = judge.render_report_markdown(result, update, language)
            geval: GEvalReport = await judge.evaluate(
                result,
                update,
                language=language,
                report_markdown=report_markdown,
                update_context=getattr(analyzer, "_last_update_context", None) or None,
                evidence_context=_build_evidence(analyzer),
            )

            (run_dir / f"report_{index:02d}.md").write_text(report_markdown, encoding="utf-8")
            (run_dir / f"geval_{index:02d}.json").write_text(
                json.dumps(geval.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
            )

            record.update(
                {
                    "relevance": getattr(result.relevance, "value", str(result.relevance)),
                    "weighted_score": round(geval.weighted_score, 3),
                    "grade": geval.grade,
                    "passed": geval.passed,
                    "critical_flaws": geval.critical_flaws,
                    "dimensions": {
                        d.key: {
                            "score": round(d.score, 3),
                            "integer_score": d.integer_score,
                            "feedback": d.feedback,
                        }
                        for d in geval.dimension_scores
                    },
                    "elapsed_s": round(time.monotonic() - t0, 1),
                }
            )
            if generation_failed:
                record["generation_failed"] = True
                print(
                    f"  [{index:02d}] SKIPPED (report generation failed — excluded "
                    f"from aggregate) {update.title[:45]}"
                )
            else:
                print(
                    f"  [{index:02d}] {geval.weighted_score:.2f}/5 "
                    f"({record['bucket']}) {update.title[:55]}"
                )
        except Exception as exc:  # keep the batch alive
            record["error"] = f"{type(exc).__name__}: {exc}"
            record["elapsed_s"] = round(time.monotonic() - t0, 1)
            print(f"  [{index:02d}] ERROR {type(exc).__name__}: {exc}")
        return record


def _aggregate(records: list[dict[str, Any]], target: float) -> dict[str, Any]:
    """Compute fleet-level averages and the weakest-dimension ranking.

    Records whose report generation failed are excluded: scoring a placeholder
    report floors every dimension and biases the average downward.
    """
    generation_failures = [r for r in records if r.get("generation_failed")]
    scored = [r for r in records if "weighted_score" in r and not r.get("generation_failed")]
    if not scored:
        return {"count": 0, "error": "no successful evaluations"}

    dim_totals: dict[str, list[float]] = defaultdict(list)
    for r in scored:
        for key, val in r.get("dimensions", {}).items():
            dim_totals[key].append(val["score"])

    dim_avg = {k: round(sum(v) / len(v), 3) for k, v in dim_totals.items()}
    overall = round(sum(r["weighted_score"] for r in scored) / len(scored), 3)

    by_bucket: dict[str, list[float]] = defaultdict(list)
    for r in scored:
        by_bucket[r["bucket"]].append(r["weighted_score"])

    return {
        "count": len(scored),
        "errors": len(records) - len(scored) - len(generation_failures),
        "generation_failures": len(generation_failures),
        "overall_avg": overall,
        "target": target,
        "passed_count": sum(1 for r in scored if r.get("passed")),
        "critical_flaw_count": sum(1 for r in scored if r.get("critical_flaws")),
        "dimension_avg": dict(sorted(dim_avg.items(), key=lambda kv: kv[1])),
        "bucket_avg": {k: round(sum(v) / len(v), 3) for k, v in sorted(by_bucket.items())},
        "worst_updates": [
            {"index": r["index"], "score": r["weighted_score"], "title": r["title"][:70]}
            for r in sorted(scored, key=lambda x: x["weighted_score"])[:5]
        ],
    }


async def run_batch(
    start: datetime,
    end: datetime,
    sample: int,
    seed: int,
    tag: str,
    concurrency: int,
    target: Optional[float],
    out_dir: Optional[str],
) -> None:
    """Sample, evaluate, and aggregate a batch of updates."""
    from src.logging_config import setup_logging

    setup_logging(console_level="CRITICAL")
    settings = get_settings()
    language = settings.report_language

    print(f"\n📡 Collecting updates {start:%Y-%m-%d} → {end:%Y-%m-%d} ...")
    parser = AzureUpdateParser()
    updates = await parser.get_updates_by_date_range(start, end)
    print(f"   {len(updates)} updates in range")
    if not updates:
        print("❌ No updates found in range.")
        return

    picked = _stratified_sample(updates, sample, seed)
    dist: dict[str, int] = defaultdict(int)
    for u in picked:
        dist[_bucket_of(u)] += 1
    print(f"   Sampled {len(picked)} (seed={seed}): {dict(sorted(dist.items()))}")

    root = Path(out_dir) if out_dir else EVAL_OUTPUT_ROOT
    run_dir = root / f"batch_{tag}_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"📁 Artifacts → {run_dir}")

    effective_target = target if target is not None else settings.geval_target_score
    print(f"⚖️  Target {effective_target}/5.0 · concurrency {concurrency}\n")

    semaphore = asyncio.Semaphore(concurrency)
    t0 = time.monotonic()
    records = await asyncio.gather(
        *(
            _evaluate_one(i, u, language, target, run_dir, semaphore)
            for i, u in enumerate(picked, start=1)
        )
    )
    elapsed = time.monotonic() - t0

    agg = _aggregate(list(records), effective_target)
    summary = {
        "tag": tag,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "range": {"from": start.strftime("%Y-%m-%d"), "to": end.strftime("%Y-%m-%d")},
        "sample": sample,
        "seed": seed,
        "elapsed_s": round(elapsed, 1),
        "aggregate": agg,
        "records": list(records),
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n{'=' * 70}")
    print(f"  BATCH RESULT — {tag}")
    print(f"{'=' * 70}")
    if agg.get("count"):
        print(f"  Overall G-Eval avg : {agg['overall_avg']:.3f}/5.00 (target {effective_target})")
        print(f"  Evaluated / errors : {agg['count']} / {agg['errors']}")
        if agg.get("generation_failures"):
            print(
                f"  ⚠️  Excluded        : {agg['generation_failures']} "
                "(report generation failed — NOT counted in the average)"
            )
        print(f"  Passed             : {agg['passed_count']}/{agg['count']}")
        print(f"  Critical flaws     : {agg['critical_flaw_count']}")
        print("\n  Dimension averages (weakest first):")
        for k, v in agg["dimension_avg"].items():
            bar = "█" * int(v / 5 * 20)
            print(f"    {k:22s} {v:.3f}  {bar}")
        print("\n  By update type:")
        for k, v in agg["bucket_avg"].items():
            print(f"    {k:22s} {v:.3f}")
        print("\n  Weakest updates:")
        for w in agg["worst_updates"]:
            print(f"    [{w['index']:02d}] {w['score']:.2f}  {w['title']}")
    else:
        print(f"  {agg.get('error')}")
    print(f"\n  Elapsed: {elapsed / 60:.1f} min · summary.json in {run_dir}")
    print(f"{'=' * 70}\n")


def main():
    p = argparse.ArgumentParser(description="AzBrief batch report quality evaluation (G-Eval)")
    p.add_argument("--from", dest="date_from", help="Start date YYYY-MM-DD")
    p.add_argument("--to", dest="date_to", help="End date YYYY-MM-DD")
    p.add_argument("--months", type=int, default=6, help="Look back N months (default: 6)")
    p.add_argument("--sample", type=int, default=12, help="Number of updates to evaluate")
    p.add_argument("--seed", type=int, default=42, help="Sampling seed (same seed = same sample)")
    p.add_argument("--tag", default="run", help="Label for the run directory")
    p.add_argument("--concurrency", type=int, default=2, help="Parallel analyses (default: 2)")
    p.add_argument("--target", type=float, default=None, help="G-Eval target (default: settings)")
    p.add_argument("--out-dir", default=None, help="Artifact root (default: eval_runs/)")
    args = p.parse_args()

    end = datetime.strptime(args.date_to, "%Y-%m-%d") if args.date_to else datetime.now()
    if args.date_from:
        start = datetime.strptime(args.date_from, "%Y-%m-%d")
    else:
        start = end - timedelta(days=args.months * 30)

    asyncio.run(
        run_batch(
            start=start,
            end=end,
            sample=args.sample,
            seed=args.seed,
            tag=args.tag,
            concurrency=args.concurrency,
            target=args.target,
            out_dir=args.out_dir,
        )
    )


if __name__ == "__main__":
    main()
