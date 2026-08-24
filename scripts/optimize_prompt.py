#!/usr/bin/env python
"""Autonomous Korean-naturalness prompt optimization loop.

Picks a fixed sample of real Azure Updates, generates reports, scores them with a
deterministic defect metric, asks the primary LLM to revise the offending style-guide
section, applies the revision to ``src/agent/prompts/languages/ko.py``, re-measures,
and keeps the change only when the score improves.

Measurement always runs in a subprocess so every round loads the prompt files fresh.

Usage:
    python -m scripts.optimize_prompt --sample 6 --rounds 3
    python -m scripts.optimize_prompt --measure sample.json out.json   # internal
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

KO_PROMPT_FILE = ROOT / "src" / "agent" / "prompts" / "languages" / "ko.py"
WORK_DIR = ROOT / "eval_runs" / "prompt_opt"

# ---------------------------------------------------------------------------
# Deterministic defect metric — lower is better
# ---------------------------------------------------------------------------
# "body" patterns skip `>` concept-box lines, where a `~입니다` definition is required.
DEFECTS: dict[str, tuple[str, str]] = {
    "명사화_종결": (
        r"(?:것|점|지점|방식|의미|내용|구조|성격|형태|부분|측면|수준|셈|차원|결합)(?:입니다|이며)",
        "body",
    ),
    "공지_프레임": (
        r"(?:이번|이)\s*(?:업데이트|공지|발표|변경|릴리스|GA|preview)[는은][^.]{0,160}?"
        r"(?:내용|공지|의미|점|기능|변화|성격)(?:입니다|이며)",
        "all",
    ),
    "공지_원인부사구": (
        r"이번\s*(?:GA|공지|발표|업데이트|preview|릴리스|출시|변경)\s*(?:로|으로|에\s*따라)\s",
        "all",
    ),
    "서두_공지프레임": (
        r"(?m)^(?:\*\*)?(?:이번|금번)\s*(?:\*\*)?\s*"
        r"(?:업데이트|공지|발표|변경|릴리스|출시|GA|preview)",
        "all",
    ),
    "영문토큰_되다": (r"[A-Za-z]{2,}\s*(?:되었습니다|됩니다|되어|되며|되면)", "all"),
    "은퇴_직역": (r"은퇴(?:합니다|됩니다|한다|하는|되는|되며)", "all"),
    "사역형": (r"수 있게 (?:합니다|해 줍니다|해줍니다|만듭니다)", "all"),
    "우회_표현": (
        r"보는 (?:편이|것이) 맞습니다|성격입니다|의미가 (?:큽니다|있습니다)|여지가 있습니다"
        r"|셈입니다|에 해당합니다|방향의",
        "all",
    ),
    "이중_피동": (r"되어지", "all"),
    "CSA_하드오프": (r"CSA[^.]{0,25}(?:검토|필요)", "all"),
    "하는것을_권장": (r"하는 것을 권장", "all"),
}

_COMPILED = {name: (re.compile(pat), scope) for name, (pat, scope) in DEFECTS.items()}

# Rules the metric does NOT measure. Without this guard the optimizer deletes them,
# because a shorter prompt costs nothing on a score that never sees them (Goodhart).
REQUIRED_ANCHORS = (
    "CSA",
    "additional_checks",
    "affected_resources",
    "action_items",
    "relevance_evidence",
    "합쇼체",
    "약어(풀네임)",
    "3문장 연속",
    "명사화 종결",
    "공지가 아니라 사실을 서술하기",
    "영문 용어를 동사 어간",
    "Azure 서비스명은 영문 그대로",
)


def missing_anchors() -> list[str]:
    text = KO_PROMPT_FILE.read_text(encoding="utf-8")
    return [a for a in REQUIRED_ANCHORS if a not in text]


def _body_only(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith(">"))


def score_text(text: str) -> dict:
    """Count defect occurrences and normalize per 1,000 Korean characters."""
    ko_chars = len(re.findall(r"[가-힣]", text))
    counts: dict[str, int] = {}
    samples: dict[str, list[str]] = {}
    for name, (rx, scope) in _COMPILED.items():
        target = _body_only(text) if scope == "body" else text
        hits = list(rx.finditer(target))
        if hits:
            counts[name] = len(hits)
            samples[name] = [
                target[max(0, m.start() - 90) : m.end() + 5].replace("\n", " ") for m in hits[:3]
            ]
    total = sum(counts.values())
    return {
        "ko_chars": ko_chars,
        "total": total,
        "per_1k": round(total / ko_chars * 1000, 3) if ko_chars else 0.0,
        "counts": counts,
        "samples": samples,
    }


def report_text_of(result) -> str:
    """Concatenate every Korean-bearing field of an AnalysisResult."""
    parts = [
        result.one_line_summary or "",
        result.relevance_evidence or "",
        result.relevance_reason or "",
        result.impact_summary or "",
    ]
    impact = getattr(result, "impact_details", None)
    if impact is not None:
        for attr in ("cost_impact", "security_impact", "performance_impact", "operational_impact"):
            parts.append(getattr(impact, attr, "") or "")
    for res in result.affected_resources or []:
        if isinstance(res, dict):
            parts.append(str(res.get("reason") or ""))
    for item in result.action_items or []:
        for attr in ("task", "why", "procedure", "risk_if_skipped", "prerequisite", "rollback"):
            parts.append(str(getattr(item, attr, "") or ""))
    parts += [str(c) for c in (getattr(result, "additional_checks", None) or [])]
    parts += [str(r) for r in (result.recommendations or [])]
    return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# ko.py section read/write (anchored on the markdown headings inside the string)
# ---------------------------------------------------------------------------
_SECTION_RE = re.compile(r"^#### (\d+)\. (.+)$", re.M)


def list_sections() -> list[tuple[str, str]]:
    text = KO_PROMPT_FILE.read_text(encoding="utf-8")
    return [(m.group(1), m.group(2)) for m in _SECTION_RE.finditer(text)]


def read_section(number: str) -> str:
    """Return the full text of `#### <number>. ...` up to the next `#### ` heading."""
    text = KO_PROMPT_FILE.read_text(encoding="utf-8").replace("\r\n", "\n")
    marks = [(m.start(), m.group(1)) for m in _SECTION_RE.finditer(text)]
    for i, (pos, num) in enumerate(marks):
        if num == number:
            end = marks[i + 1][0] if i + 1 < len(marks) else text.index('"""', pos)
            return text[pos:end]
    raise KeyError(f"section {number} not found")


def write_section(number: str, new_body: str) -> None:
    raw = KO_PROMPT_FILE.read_bytes()
    crlf = b"\r\n" in raw
    text = raw.decode("utf-8").replace("\r\n", "\n")
    old = read_section(number)
    assert text.count(old) == 1, "section anchor is not unique"
    updated = text.replace(old, new_body.rstrip() + "\n\n")
    ast.parse(updated)
    out = updated.replace("\n", "\r\n") if crlf else updated
    KO_PROMPT_FILE.write_bytes(out.encode("utf-8"))


def validate_prompt_file() -> tuple[bool, str]:
    """Import the package and assemble the Korean guide in a fresh process."""
    code = (
        "import src; "
        "from src.agent.prompts import build_system_prompt, build_report_prompt; "
        "from src.agent.prompts.languages import get_style_guide; "
        "g = get_style_guide('ko'); "
        "assert len(g) > 3000, 'style guide too short'; "
        "assert build_system_prompt(language='ko'); "
        "assert build_report_prompt(category='new_feature'); "
        "print('OK', len(g))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()[-800:]


# ---------------------------------------------------------------------------
# Measurement (subprocess mode)
# ---------------------------------------------------------------------------
async def _measure(sample_path: Path, out_path: Path, concurrency: int) -> None:
    from src.agent.analyzer import AzureUpdateAnalyzer
    from src.rss.parser import AzureUpdate

    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    analyzer = AzureUpdateAnalyzer()
    await analyzer.get_resource_summary()

    sem = asyncio.Semaphore(concurrency)

    async def one(entry: dict) -> dict:
        update = AzureUpdate(
            id=entry["id"],
            title=entry["title"],
            description=entry["description"],
            link=entry["link"],
            published_date=datetime.fromisoformat(entry["published_date"]),
            categories=entry["categories"],
            azure_services=entry["azure_services"],
            update_type=entry["update_type"],
            status=entry.get("status"),
        )
        async with sem:
            t0 = time.time()
            try:
                result = await analyzer.analyze_update(update)
            except Exception as exc:  # a failed update must not abort the round
                return {"id": entry["id"], "title": entry["title"], "error": str(exc)[:300]}
            text = report_text_of(result)
            sc = score_text(text)
            sc.update(
                {
                    "id": entry["id"],
                    "title": entry["title"],
                    "elapsed_s": round(time.time() - t0, 1),
                    "text": text,
                }
            )
            return sc

    results = await asyncio.gather(*(one(e) for e in sample))
    ok = [r for r in results if "error" not in r]
    total_ko = sum(r["ko_chars"] for r in ok)
    total_hits = sum(r["total"] for r in ok)
    agg: dict[str, int] = {}
    for r in ok:
        for k, v in r["counts"].items():
            agg[k] = agg.get(k, 0) + v
    out_path.write_text(
        json.dumps(
            {
                "reports": results,
                "summary": {
                    "updates": len(ok),
                    "failed": len(results) - len(ok),
                    "ko_chars": total_ko,
                    "total_defects": total_hits,
                    "per_1k": round(total_hits / total_ko * 1000, 3) if total_ko else 0.0,
                    "by_defect": dict(sorted(agg.items(), key=lambda kv: -kv[1])),
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def run_measurement(sample_path: Path, out_path: Path, concurrency: int) -> dict:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.optimize_prompt",
            "--measure",
            str(sample_path),
            str(out_path),
            "--concurrency",
            str(concurrency),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0 or not out_path.exists():
        raise RuntimeError(f"measurement failed:\n{(proc.stdout + proc.stderr)[-2000:]}")
    return json.loads(out_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Sample selection
# ---------------------------------------------------------------------------
async def _build_sample(size: int, out_path: Path) -> list[dict]:
    from src.rss.parser import AzureUpdateParser

    updates = await AzureUpdateParser().get_updates()
    # Spread across update types so one defect class cannot dominate the metric.
    buckets: dict[str, list] = {}
    for u in updates:
        buckets.setdefault((u.update_type or "Other").split()[0], []).append(u)
    picked, i = [], 0
    while len(picked) < size and any(buckets.values()):
        for key in sorted(buckets):
            if len(picked) >= size:
                break
            if i < len(buckets[key]):
                picked.append(buckets[key][i])
        i += 1
    sample = [
        {
            "id": u.id,
            "title": u.title,
            "description": u.description,
            "link": u.link,
            "published_date": u.published_date.isoformat(),
            "categories": u.categories,
            "azure_services": u.azure_services,
            "update_type": u.update_type,
            "status": u.status,
        }
        for u in picked[:size]
    ]
    out_path.write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")
    return sample


# ---------------------------------------------------------------------------
# LLM-driven section revision
# ---------------------------------------------------------------------------
REVISION_PROMPT = """You are editing a Korean writing style guide that is injected into an \
LLM prompt. The guide is failing: the model still produces the defective sentences below.

## Current section (verbatim)
```
{section}
```

## Defective sentences the current section failed to prevent
{evidence}

## Defect counts across {n} generated reports
{counts}

## Your task
Rewrite ONLY this section so the model stops producing those sentences.

Rules for your rewrite:
1. Keep the exact same `#### N. <title>` heading line, unchanged.
2. Write a CONSTRUCTION TEST the model applies to every sentence, not a list of banned \
phrases. A blacklist only pushes the model to the nearest unbanned synonym — that is \
exactly why the current section failed.
3. Keep or shorten the length. A longer prompt dilutes every rule. Delete anything \
redundant or already covered elsewhere.
4. Every BAD example must be a real sentence from the evidence above, paired with a \
concrete GOOD rewrite.
5. State carve-outs precisely. Concept boxes (`>` blocks) are REQUIRED to end with ~입니다; \
never ban that. But a body sentence is never exempt.
6. Do not contradict rules that are outside this section.
7. NEVER delete a rule just because it is unrelated to the defect you are fixing. \
Rules about additional_checks, affected_resources, action_items, relevance_evidence, \
CSA hand-offs, honorific level, or abbreviation order MUST survive verbatim if they are \
in this section.
8. Write the section in Korean, in the same markdown style as the original.

Output ONLY the rewritten section text. No preamble, no code fences."""


async def _revise_section(section_text: str, evidence: list[str], counts: dict, n: int) -> str:
    from langchain_core.messages import HumanMessage

    from src.agent.analyzer import AzureUpdateAnalyzer

    analyzer = AzureUpdateAnalyzer()
    prompt = REVISION_PROMPT.format(
        section=section_text,
        evidence="\n".join(f"- {e}" for e in evidence[:20]) or "- (none)",
        counts=json.dumps(counts, ensure_ascii=False),
        n=n,
    )
    resp = await analyzer.llm.ainvoke([HumanMessage(content=prompt)])
    text = resp.content if hasattr(resp, "content") else str(resp)
    return re.sub(r"^```[a-z]*\n|\n```$", "", text.strip())


# Which style-guide section governs which defect.
DEFECT_SECTION = {
    "명사화_종결": "2",
    "공지_프레임": "3",
    "서두_공지프레임": "3",
    "공지_원인부사구": "3",
    "영문토큰_되다": "4",
    "은퇴_직역": "3",
    "사역형": "3",
    "우회_표현": "7",
    "이중_피동": "2",
    "CSA_하드오프": "7",
    "하는것을_권장": "3",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--measure", nargs=2, metavar=("SAMPLE", "OUT"))
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--sample", type=int, default=6, help="number of updates in the fixed sample")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument(
        "--margin",
        type=float,
        default=0.4,
        help="minimum per_1k improvement to KEEP. Measured run-to-run spread of an "
        "identical prompt on a 6-update sample was 0.388, so anything smaller is noise.",
    )
    ap.add_argument("--reuse-sample", type=str, default="")
    args = ap.parse_args()

    if args.measure:
        asyncio.run(_measure(Path(args.measure[0]), Path(args.measure[1]), args.concurrency))
        return 0

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    run_dir = WORK_DIR / datetime.now().strftime("run_%Y%m%d_%H%M%S")
    run_dir.mkdir()
    log_path = run_dir / "loop.md"

    def log(msg: str) -> None:
        print(msg, flush=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(msg + "\n")

    sample_path = Path(args.reuse_sample) if args.reuse_sample else run_dir / "sample.json"
    if not sample_path.exists():
        asyncio.run(_build_sample(args.sample, sample_path))
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    log(f"# prompt optimization loop\n\nsample: {len(sample)} updates -> {sample_path}")
    for s in sample:
        log(f"  - [{s['update_type']}] {s['title'][:70]}")

    backup = run_dir / "ko.py.orig"
    best_file = run_dir / "ko.py.best"
    shutil.copyfile(KO_PROMPT_FILE, backup)
    shutil.copyfile(KO_PROMPT_FILE, best_file)
    log(f"\nbaseline prompt backed up -> {backup}")

    log("\n## round 0 (baseline)")
    best = run_measurement(sample_path, run_dir / "round0.json", args.concurrency)
    b = best["summary"]
    log(f"  defects={b['total_defects']}  per_1k={b['per_1k']}  ko_chars={b['ko_chars']}")
    log(f"  by_defect={json.dumps(b['by_defect'], ensure_ascii=False)}")

    for rnd in range(1, args.rounds + 1):
        by_defect = best["summary"]["by_defect"]
        if not by_defect:
            log("\nno defects left — stopping")
            break
        target = max(by_defect, key=lambda k: by_defect[k])
        section_no = DEFECT_SECTION.get(target)
        log(f"\n## round {rnd} — target '{target}' ({by_defect[target]}회) -> section {section_no}")
        if not section_no:
            log("  no section mapped; stopping")
            break

        evidence: list[str] = []
        for rep in best["reports"]:
            evidence += rep.get("samples", {}).get(target, [])
        section_text = read_section(section_no)
        try:
            new_section = asyncio.run(
                _revise_section(section_text, evidence, by_defect, len(sample))
            )
        except Exception as exc:
            log(f"  revision call failed: {exc}")
            break

        if not new_section.startswith(f"#### {section_no}."):
            log("  rejected: rewrite did not keep the heading")
            continue
        (run_dir / f"round{rnd}_section.md").write_text(new_section, encoding="utf-8")
        log(f"  proposed section: {len(section_text)} -> {len(new_section)} chars")

        write_section(section_no, new_section)
        gone = missing_anchors()
        if gone:
            log(f"  REVERT: rewrite deleted unmeasured rules -> {gone}")
            shutil.copyfile(best_file, KO_PROMPT_FILE)
            continue
        ok, detail = validate_prompt_file()
        if not ok:
            log(f"  REVERT: prompt file failed validation\n{detail}")
            shutil.copyfile(best_file, KO_PROMPT_FILE)
            continue

        cand = run_measurement(sample_path, run_dir / f"round{rnd}.json", args.concurrency)
        c, p = cand["summary"], best["summary"]
        log(f"  candidate: defects={c['total_defects']} per_1k={c['per_1k']}  (was {p['per_1k']})")
        log(f"  by_defect={json.dumps(c['by_defect'], ensure_ascii=False)}")
        if p["per_1k"] - c["per_1k"] > args.margin:
            best = cand
            shutil.copyfile(KO_PROMPT_FILE, best_file)
            shutil.copyfile(KO_PROMPT_FILE, run_dir / f"ko.py.round{rnd}")
            log(
                f"  KEEP (improved by {round(p['per_1k'] - c['per_1k'], 3)} > margin {args.margin})"
            )
        else:
            # restore the best so far, NOT the round-0 baseline
            shutil.copyfile(best_file, KO_PROMPT_FILE)
            log(f"  REVERT (delta {round(p['per_1k'] - c['per_1k'], 3)} within noise)")

    log(
        f"\n## final\n  per_1k={best['summary']['per_1k']}  "
        f"defects={best['summary']['total_defects']}"
    )
    log(f"  artifacts: {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
