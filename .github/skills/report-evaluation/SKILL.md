---
name: report-evaluation
description: 'G-Eval LLM-as-a-Judge methodology for scoring and autonomously improving AzBrief analysis reports. Use when: G-Eval, LLM-as-a-Judge, report evaluation, quality scoring, rubric, dimension score, self-improvement loop, geval.py, GEvalJudge, logprob normalization, score normalization, calibration, actionability, faithfulness, job relevance, architectural depth, evaluate report quality, iterate to perfect score.'
---

# AzBrief Report Evaluation — G-Eval LLM-as-a-Judge

## Foundry Runtime Guidance

- Evaluate independently across actionability, faithfulness, job relevance, structure, and
  architectural depth. Faithfulness outranks polish.
- Treat any fabricated resource, date, command, or URL as critical. Reward concise evidence,
  honest zero-impact findings, and explicit limits rather than verbosity.
- Return evidence-addressed corrections that name the unsupported claim or missing fact and
  the smallest required change; never rewrite merely to raise a score.
- Judge or parser failure is not a pass. Preserve the error and fail closed.

<!-- End Foundry Runtime Guidance -->

## When to Use

- Scoring a generated report's **semantic** quality with `src/agent/geval.py` (`GEvalJudge`)
- Running the generate → evaluate → improve loop to push a report toward the score ceiling
- Adding, tuning, or reweighting a G-Eval **dimension** or its 1-5 **rubric**
- Debugging logprob score normalization or the self-correction feedback loop
- Calibrating the judge against human expert scores (judge drift)

> For the fast **rule-based / mechanical** scorer (regex heuristics, 100-pt), see the
> `report-quality` skill. G-Eval is the semantic layer on top of it; both run together
> in `scripts/evaluate_report.py`.

## Quick Reference

```bash
# Generate a real-data report, score it with G-Eval, iterate to the target (3 rounds)
python -m scripts.evaluate_report --latest --with-html --iterate 3

# Evaluate a specific update
python -m scripts.evaluate_report --url "https://azure.microsoft.com/updates?id=..." --iterate 3

# Raise the passing bar (default 4.5/5)
python -m scripts.evaluate_report --latest --iterate 4 --target 4.7

# Rule-based scoring only (no LLM judge)
python -m scripts.evaluate_report --latest --no-geval

# Unit tests for the judge (no network — uses a fake LLM)
python -m pytest tests/test_geval.py -o "addopts=" -q
```

Each iteration writes artifacts to a timestamped run folder under `eval_runs/` (gitignored):
`report_iter{N}.md` (rendered report), `report_iter{N}.html` (email), `geval_iter{N}.json` (scores).
Override the location with `--out-dir DIR` or the `AZBRIEF_EVAL_DIR` environment variable.

## File Map

| File | Role |
|------|------|
| `src/agent/geval.py` | `GEvalJudge`, `GEvalReport`, `DimensionScore`, `DIMENSIONS` — the judge |
| `scripts/evaluate_report.py` | CLI loop: generate → rule pre-check → G-Eval → feedback → repeat |
| `src/config.py` | `geval_*` settings (enabled, target, logprob, max_iterations) |
| `tests/test_geval.py` | Judge unit tests (fake LLM, logprob math, aggregation, edge cases) |
| `src/agent/prompts/` | Report prompts — the lever that G-Eval feedback improves |

---

## Why LLM-as-a-Judge (not BLEU/ROUGE, not 1-10)

Surface string-matching metrics (BLEU, ROUGE) cannot capture the fluency, logical
consistency, factual grounding, and job relevance a cloud architecture report requires.
G-Eval uses a strong reasoning LLM as the judge. Two design choices make the scores
trustworthy:

1. **1-5 anchored absolute scale**, not binary (no resolution) and not 1-10 (7-vs-9 is
   noise). Each point has an explicit, independent meaning that anchors the model.
2. **5 = unreachable theoretical ideal; 4 = production-excellent.** Placing perfection out
   of reach prevents *score saturation* — the model keeps finding improvements instead of
   awarding an easy 5 and stopping. **Never award 4+ if any flaw exists.**

## The Five Orthogonal Dimensions

Dimensions are **independent** (orthogonal) so a flaw in one does not double-penalize
another (no anchor bleed / halo effect). Each is judged in its **own parallel LLM call**.

| # | Dimension (`key`) | Korean | Weight | Measures |
|---|-------------------|--------|:------:|----------|
| D1 | `actionability` | 실질적 유용성·실행 가능성 | 1.2 | Can the reader act in 5 min? Named resources, exact CLI, deadlines, rollback |
| D2 | `faithfulness` | 맥락적 사실성·데이터 충실성 | 1.3 | Every claim grounded in source context; zero hallucination |
| D3 | `job_relevance` | 직무 연관성·독자 맞춤화 | 1.0 | Narrative re-centered on the subscriber's role |
| D4 | `structure` | 구조적 명확성·시각적 디자인 | 0.9 | Headings, tables, emphasis → 1-second scannability |
| D5 | `architectural_depth` | 클라우드 아키텍처 통찰 | 1.0 | 2nd/3rd-order ripple effects, WAF pillars, compliance |

`faithfulness` carries the highest weight — a fabricated fact is the most dangerous
failure. `structure` carries the least — polish matters less than correctness.

### Rubric shape (all dimensions share this anchoring)

- **5 (Ideal)** — Beyond a human expert; anticipates every edge case and 2nd/3rd-order effect.
- **4 (Excellent)** — Production-ready. Complete, grounded, actionable. *Best achievable in practice.*
- **3 (Adequate)** — Direction present but gaps (missing commands, vague estimates).
- **2 (Poor)** — Superficial; not executable / not grounded / not role-fit.
- **1 (Harmful)** — Negative value: wrong commands, critical hallucination, misread role.

The full rubric text lives in `src/agent/geval.py` (`_ACTIONABILITY`, `_FAITHFULNESS`, …).

## How a Score Is Produced

For each dimension, `GEvalJudge._evaluate_dimension` runs:

1. **Chain-of-Thought (form-filling)** — the judge writes explicit reasoning for each
   evaluation step *before* emitting a score. This blocks shallow pattern matching and
   forces the attention mechanism to hunt for logical defects. Output is strict JSON:
   ```json
   {"reasoning": "...", "score": 4, "feedback_for_improvement": "..."}
   ```
   The judge **may emit half-points** (e.g. `3.5`); `_coerce_score()` snaps to the nearest
   0.5 and clamps to [1, 5]. Half-points are the primary resolution mechanism — see the
   warning below.
2. **Score normalization via log-probabilities** (optional, on by default) — a constrained
   follow-up call emits a single score digit with `logprobs=True, top_logprobs=5`. The
   linear probabilities of the score tokens {1..5} are weighted to yield a **continuous**
   score (e.g. `4.13`).
   ```
   score = Σ (token_value · e^logprob) / Σ e^logprob   over tokens ∈ {1,2,3,4,5}
   ```
   Auto-disabled for o-series reasoning models → falls back to the integer score
   (`normalized=False`). Skipped entirely when the judge already returned a half-point,
   because that pass sees only the reasoning text (no report, no evidence) and must never
   overwrite a better-informed score.

   > ⚠️ **Verify logprobs actually arrive before trusting `normalized=True`.**
   > Some deployments (observed on `gpt-5.4`) ignore `top_logprobs=N` and return only the
   > chosen token. `_weighted_score_from_logprobs()` therefore returns `None` when fewer
   > than 2 score candidates come back — a single candidate yields zero resolution while
   > still *looking* continuous. Symptom: different reports scoring identically.
3. **Parallel, isolated** — all five dimensions run via `asyncio.gather`; an exception in
   one is captured (`DimensionScore.error`, integer_score=3) without failing the others.

### Aggregation

`GEvalReport.calculate()` computes a weighted mean over the continuous scores, a
percentage (`score/5·100`), a grade band (S/A/B/C/D/F), `passed` (≥ target), the ordered
`aggregated_feedback` (weakest dimension first), and `critical_flaws` (any dimension ≤ 2).

## Edge-Case Handling & Verbosity-Bias Defense

Every rubric embeds explicit exemptions the judge must honor — do **not** deduct when:

- **Zero affected resources**: a report that transparently says so and gives only monitoring
  guidance is *correct* for `actionability`. Do not demand fabricated commands/deadlines.
- **Honest limits**: "compatibility cannot be confirmed with the collected data" is a
  *positive* `faithfulness` signal, not a deduction.
- **No subscriber profile**: score `job_relevance` against a general Azure admin; don't
  over-penalize the absence of narrow personalization.
- **Trivial UI/notification update**: declaring "no architectural impact" scores well on
  `architectural_depth`; don't reward invented complexity.

But one absence statement is **not** exempt: for a Capability-family update (`new_feature`,
`new_service`, `region_expansion`, `preview`, `sdk_tooling`) the judge treats "이 업데이트는 운영에
영향이 없습니다 / 도입하지 않아도 리스크가 없습니다" as a substantive `actionability` gap, because a
newly released capability never changes existing behaviour — the sentence is a tautology. The useful
answer is the opportunity: what becomes possible, for which named candidates, at what adoption cost,
and whose responsibility it is. `render_report_markdown()` therefore shows the judge the update
category in the badge line and titles the impact table `활용 기회` (not `영향 분석`) for those
categories, matching what the reader sees in the email.

**Verbosity bias**: the judge is instructed to reward sharp, concise insight over length —
a defense against reward hacking (padding text to inflate scores).

## The Self-Improvement Loop

```
Generate report (real Azure data)
        │
        ▼
Rule-based mechanical pre-check  ──►  fast deterministic filter (regex, 100-pt)
        │
        ▼
G-Eval semantic scoring (5 dims, parallel, CoT + logprob)
        │
        ├─ passed (≥ target, no critical flaws) ──►  STOP 🏆
        │
        ▼
build_feedback_prompt()  →  weakest-dimension instructions + critical flaws
        │
        ▼
Inject into settings.custom_system_prompt  →  regenerate  (loop, max_iterations)
```

`GEvalJudge.build_feedback_prompt()` returns concrete, per-dimension rewrite instructions
(e.g. *"add the region-egress-cost financial insight to the cost-optimization paragraph"*),
ordered weakest-first, plus a critical-flaws block. The loop injects this into the report
prompt for the next round. Typical trajectory: an initial ~3.x report rises to the 4.x
production-excellent band within 2 revisions.

## Configuration

| Setting (`src/config.py`) | Default | Purpose |
|---------------------------|:-------:|---------|
| `geval_enabled` | `True` | Turn the LLM judge on/off in the loop |
| `geval_target_score` | `4.5` | Passing threshold on the 1-5 scale (loop stop point) |
| `geval_logprob_normalization` | `True` | Continuous scoring via logprobs (auto-off for o-series) |
| `geval_max_iterations` | `3` | Max generate→evaluate→improve rounds |

The judge model is a **deterministic** (temperature=0, seed=42) instance of the primary
deployment, built by `GEvalJudge._create_judge_llm()`.

## Extending the Judge

### Add or modify a dimension
1. Define a `GEvalDimension` in `src/agent/geval.py` (key, title, weight, rubric, steps,
   edge_cases) and add it to the `DIMENSIONS` tuple.
2. Keep the rubric anchored 1-5 with 5 = unreachable ideal, 4 = production-excellent.
3. Always include an **edge-case** clause and honor the verbosity-bias rule.
4. Add a case to `tests/test_geval.py` (the fake LLM maps dimension title → score).

### Reweight
Adjust `weight` on each dimension. Weights are relative; the aggregate is a weighted mean,
so absolute values don't need to sum to any constant. Raise `faithfulness` if hallucination
is the top risk; lower `structure` if polish is over-counted.

### Change the score ceiling behavior
Edit `_JUDGE_SYSTEM` (the "never award 4+ if any flaw exists" clause) — this is the primary
lever against score saturation.

## Calibration (Human Alignment)

Judge scores must stay aligned with expert judgment; models drift over time.

- **Quarterly**: sample ~50 reports of varied difficulty, have infra-lead engineers
  blind-score them against these rubrics, and compute correlation (Cohen's κ / Krippendorff's
  α) vs the G-Eval continuous scores.
- If any dimension's agreement drops below ~75%, its anchors are ambiguous. Fix by
  **hard-coding few-shot labeled examples** for the fuzzy 2 and 4 bands into that
  dimension's prompt.
- **Position-bias check**: re-score the same report with reordered/reformatted sections to
  confirm the verdict is stable.

## Interpreting Results

```
G-EVAL SCORE: 4.13/5.00 (83%) — A (프로덕션 우수 / production-excellent)
Target: 4.5/5.0  |  Verdict: NEEDS IMPROVEMENT
  ✅ Actionability & Practical Helpfulness   ≈4.20/5  ██████████████████░░  (int 4)
  ⚠️ Cloud Architectural Depth               ≈3.60/5  ██████████████░░░░░░  (int 4)
```

- `≈` marks a logprob-normalized (continuous) score; a space marks a raw integer.
- A `faithfulness` integer of 1 is a **critical hallucination** — treat as a release blocker.
- `passed` requires the weighted score ≥ target **and** zero critical flaws.
- Because 5.0 is intentionally unreachable, the practical ceiling is the 4.x band; the loop
  targets 4.5 by default, not 5.0.

### Attributing a prompt change — text diff, not the aggregate score

Single-pass G-Eval on one update is **noisy**, especially for `not_relevant` / low-relevance
updates (which are inherently score-capped). Two runs of the *same* update can differ by
±0.25 purely from LLM sampling — a different stochastic faithfulness slip (e.g. naming an
un-evidenced resource) can drop the aggregate even when your change was correct.

- **Attribute a source fix by diffing the generated `report_iter{N}.md` at the sentence you
  targeted**, not by the weighted score alone. If the targeted defect text is gone and
  replaced by the intended phrasing, the fix worked — regardless of an orthogonal score dip.
- Do **not** revert a demonstrably-correct fix because one noisy re-run scored lower for an
  unrelated reason. Re-run 2-3× or test a holdout of a different update type instead.
- Chasing the last tenths on a low-relevance sample invites reward-hacking/verbosity — stop at
  diminishing returns (see the guardrails in `.github/prompts/self-improve-reports.prompt.md`).

### Fleet measurement — and the A/A test you must run first

`scripts/evaluate_batch.py` scores N updates across a date range and aggregates per dimension:

```bash
python -m scripts.evaluate_batch --months 6 --sample 12 --seed 42 --tag baseline --concurrency 4
```

Same `--seed` → same sampled updates, so two tags are directly comparable. **But before
comparing anything, re-run the *identical* configuration under a second tag (an A/A test)
and measure the gap.** The report generator is an LLM agent, so it is not deterministic.

**Measured on this repo (2026-08, 12-update sample, `gpt-5.4`):**

| quantity | value |
|---|---|
| A/A paired difference (n=8 clean) | **−0.064** (95% CI [−0.215, +0.087] — includes 0) |
| SD of paired difference | **0.181** |
| **Minimum detectable effect (95%)** | **≈ 0.15 points** |
| per-dimension A/A drift | up to **±0.19** |

Consequences:

- **Any observed change below ~0.15 is indistinguishable from noise.** Four prompt
  experiments in that session moved the score by 0.02–0.16 and were therefore *all*
  inconclusive. Do not report such a delta as an improvement.
- To detect **0.10** you need **~24** updates per run; for **0.05**, ~95. Budget accordingly,
  or repeat each condition 3× and average.
- Prefer **countable, noise-resistant signals** over the weighted score when validating a
  prompt change: `critical_flaws` count, occurrences of a banned phrase, presence of CLI
  commands. These move 0 → N unambiguously.

### Exclude failed generations from the aggregate

The analyzer **degrades gracefully**: if the report LLM call fails it returns a placeholder
(`detailed_analysis` starting with `"Report generation failed"`) instead of raising. That is
correct for production but poison for measurement — the judge scores the empty report at a
flat 3.00 and it silently drags the fleet average down (observed: 3 failures pulled a
12-update average down by 0.11, which nearly got misread as a prompt regression).

`evaluate_batch.py` detects the marker, sets `record["generation_failed"]`, excludes it from
`_aggregate()`, and reports it separately:

```
  ⚠️  Excluded        : 3 (report generation failed — NOT counted in the average)
```

When writing any new evaluation harness against this codebase, apply the same rule:
**an absent exception is not success — inspect the artifact.**

### Feed the judge the same evidence the report was built from

A recurring "delete the reference to `<named resource>` unless you cite the query output"
**faithfulness** penalty is usually **not** a hallucination — it's the judge being *starved of
evidence*. `scripts/evaluate_report.py` builds `evidence_context` from
`analyzer._last_task_results`; it must truncate each task result to
**`TOOL_RESULT_BUDGET_CHARS`** (the same 8000-char budget the analyzer used to build the
report), **not** a smaller value. A smaller cap hides grounded resource names that live past
the cutoff in a large enumeration (e.g. the affected account in a 26-account estate), so the
judge flags a *true* claim as unverified.

- When the judge repeatedly flags **grounded** resource names as unverified, suspect the
  **evidence pipeline** (what the judge is shown) before editing the report or the prompt.
- Aligning the judge's evidence budget with the analyzer's is **fair evaluation, not
  reward-hacking** — it removes a false-negative bias without touching the rubric, target, or
  weights, and real hallucinations (claims absent from the full evidence) are still caught.
- Since `src/agent/context_store.py` landed, an over-budget task result is stored as a
  **preview + `[ref=Rn]` handle**, so `_last_task_results` holds the handle rather than a raw
  cut. Judge and report therefore still see the same text. When the agent reaches past a
  preview it does so with a `query_tool_result` task, whose output lands in `task_results` and
  is visible to the judge on its own.


