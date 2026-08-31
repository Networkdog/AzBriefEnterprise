# AzBrief Enterprise Quality Campaign Rubric

## Purpose

This rubric governs pre-release improvement of the complete deployed system: one Foundry Hosted
Agent, six Prompt Agents, their tools, and the report artifact. It deliberately separates output
quality, process quality, action safety, and reliability. A high prose score cannot offset a
fabricated resource, a blocked command, a failed specialist, or an unreproducible run.

## Anchored Scale

Every semantic dimension uses the same absolute 1-5 anchor.

| Score | Meaning |
|---:|---|
| 5 | Theoretical ideal. Better than a strong Azure expert across all known edge cases, with live proof across failures, scale, languages, roles, and repeated runs. No known gap exists. |
| 4 | Production-excellent. Grounded, complete, concise, and directly useful; only a minor non-blocking improvement remains. |
| 3 | Adequate. Directionally correct but missing material evidence, specificity, or depth. |
| 2 | Poor. Superficial, weakly grounded, difficult to act on, or mismatched to the reader. |
| 1 | Harmful. Fabricated, unsafe, materially misleading, or worse than no report. |

`5` is a north star, not an automated stopping condition. Optimizing until every stochastic judge
returns 5 encourages verbosity, overfitting, and rubric manipulation. Release uses the measurable
gates below; continuous improvement continues after release.

## Quality Layers

| Layer | Evaluator | What it proves | Blocking rule |
|---|---|---|---|
| Semantic output | G-Eval quality-reviewer Prompt Agent | Actionability, faithfulness, job relevance, structure, architectural depth | Weighted mean >= 4.5; every dimension mean >= 4.0; every case >= 3.0; zero critical flaw or dimension error |
| Deterministic artifact | `ReportQualityEvaluator` | Contract consistency, required fields, URLs/dates, language patterns, action structure, HTML scanability | Fleet mean >= 90/100; every case >= 80 |
| Agent process | `TrajectoryEvaluator` plus trace events | Task completion, tool success, retry burden, KQL health, revision churn, specialist gaps | Mean >= 90/100; every case >= 70; every trajectory passes |
| Action safety | `ActionItemVerifier` | Scope, grounding, placeholders, destructive behavior, independent cross-check, command withholding | Zero blocked or unverified item; no missing verification when actions exist |
| Reliability | `quality_campaign compare` | Generation success, full selected-case coverage, A/A noise, paired change, untouched holdout | Zero execution/generation failure; candidate clears noise without regressions |
| Deployment fidelity | Hosted `evaluate_update` operation | The deployed Hosted Agent and immutable Prompt Agent roster behave like the accepted source candidate | Full-period Hosted run has `release_eligible=true` |

The first five G-Eval dimensions remain orthogonal:

1. **Actionability**: a responsible operator can begin within five minutes; targets, procedure,
   completion criteria, precautions, rollback, and real deadlines are present when applicable.
2. **Faithfulness**: every tenant, product, date, command, and URL claim is supported by the same
   evidence snapshot used to write the report. An explicit unknown is better than invented certainty.
3. **Job relevance**: the canonical evidence is reframed around the subscriber's actual remit rather
   than decorated with a role name.
4. **Structure**: the three-second summary, thirty-second scan, and engineering detail path are clear
   without duplicated sections or padding.
5. **Architectural depth**: grounded dependency, migration, WAF, cost, security, and operational
   consequences appear when material; trivial updates are not inflated with invented complexity.

## Experimental Protocol

1. Freeze the period, update payloads, category strata, seed, diagnosis split, holdout split, dataset
   hash, source commit/worktree hash, Hosted contract version, Agent roster, runtime, and concurrency.
2. Before editing source, run diagnosis twice unchanged. Treat the larger of the paired mean drift and
   95% confidence half-width as the A/A noise floor. Use `compare --mode aa`; it rejects runs whose
   source/worktree, immutable Agent versions, runtime, or concurrency differ. A/A accepts only
   completed runs with stable start/end lineage. Default to one concurrent analysis because each
   analysis already fans out three evidence specialists; raise it only after measuring Prompt Agent
   capacity and keep the same value across a comparison.
3. Also run the holdout baseline before editing. Never regenerate or inspect holdout feedback while
   choosing the fix.
4. Diagnose one repeated defect from report text and trace events. State one falsifiable root-cause
   hypothesis and identify the smallest source change that tests it.
5. Re-run the same diagnosis cases. A score change inside the noise floor is `inconclusive`, not an
   improvement. Any increase in generation errors, critical flaws, failed trajectories, or blocked
   actions, unverified actions, missing diagnostics, or dimension-evaluation errors is a regression
   regardless of mean score. Candidate comparison records changed axes;
   source, Agent version, and runtime changes must be isolated rather than attributed as one fix.
6. Only a diagnosis winner reaches holdout. Keep it only when the target defect also improves on the
   untouched holdout and import/tests remain green.
7. After user-approved provisioning/deployment, run the same cases through Hosted `evaluate_update`.
   The release campaign uses every update in the selected period (`--sample 0 --split all`).
8. Stop an optimization branch after three attempts below the A/A noise floor. Record failed
   hypotheses in campaign artifacts, but add repository Learnings only for validated results.

Long runs preserve every attempt in `attempts/` and checkpoint the final case outcome atomically in
`records/`. Transient connection/rate-limit failures and generation placeholders receive one deferred
retry after the first pass, allowing a short outage to clear while unrelated cases continue. Retry and
recovery counts remain visible; an exhausted final error is still a blocker. `run.json` freezes the ordered
case set and experimental lineage; `progress.json` records active elapsed time and completion. Resume
only through `--resume-run` and only when dataset, split, runtime, concurrency, source/worktree hash,
and immutable Agent versions are unchanged. An in-flight case without a completed record is rerun; completed cases
are never charged twice. Start/end lineage drift marks the run invalid even if every case returned.
The worktree hash covers the HEAD commit, tracked binary diff, and path plus bytes of every non-ignored
untracked file. The loader rejects schema, rubric, release-threshold, or Hosted-contract drift instead
of comparing artifacts produced under different evaluation semantics.

Repeated-run stability follows the spirit of `pass^k`: one lucky pass is not reliability. For a release
candidate, repeat high-risk retirement, breaking-change, and executable-action cases enough times to
show that all trials pass their blockers. Keep stochastic scores paired by case and never compare two
different datasets as if they were the same experiment.

## Trace Contract

Correlate every event with the campaign `trace_id`:

- `hosted_request_started` / `hosted_request_completed`
- `foundry_prompt_agent_started` / `foundry_prompt_agent_completed`
- `foundry_agent_local_tool_completed`
- `foundry_specialist_completed` and specialist error/normalization events
- phase `llm_call`, `geval_done`, `action_verification_done`, `trajectory_evaluated`
- `analysis_complete` and `report_content`

Log role, Agent and response IDs, model/status, prompt/output sizes and fingerprints, tool name and
argument fingerprint, validated claim IDs/evidence references/gaps, token usage, elapsed time, and
failure category. Do not log or export private chain-of-thought. The evaluation wire contract exports
scores and actionable feedback but not raw tenant evidence or judge reasoning. Raw development traces
remain subject to Application Insights access and retention controls.

## Human Calibration

At least quarterly, Azure practitioners should blind-score a stratified set of approximately 50
reports. Compare dimension-level agreement with the automated judge using weighted Cohen's kappa or
Krippendorff's alpha. Investigate dimensions below 0.75 agreement, add labeled boundary examples, and
rerun position/order and verbosity perturbations. Human calibration changes the evaluator only when
the old anchor is ambiguous; it must never be used to excuse a known product defect.

## Research Basis

- [G-Eval: NLG Evaluation using GPT-4 with Better Human Alignment](https://arxiv.org/abs/2303.16634):
  form-filling evaluation and probability-weighted scores align better with human judgments than
  surface overlap metrics, while model-generated-text bias remains a risk.
- [Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena](https://arxiv.org/abs/2306.05685):
  strong judges are useful but exhibit position, verbosity, and self-enhancement biases. A/A tests,
  independent dimensions, and human calibration are therefore mandatory.
- [tau-bench](https://arxiv.org/abs/2406.12045): repeated trials reveal severe agent inconsistency;
  end-state success and `pass^k` motivate stability gates instead of accepting one successful trace.
- [Microsoft Foundry agent evaluators](https://learn.microsoft.com/azure/foundry/concepts/evaluation-evaluators/agent-evaluators):
  production agents require both end-to-end system evaluation and process evaluation of tool
  selection, input accuracy, output utilization, call success, and navigation efficiency.
- [Evaluate deployed interactions](https://learn.microsoft.com/azure/foundry/observability/how-to/cloud-evaluation-deployed-interactions):
  deployed traces can be evaluated by trace ID, and representative sampling should retain diverse
  edge and failure paths.
- [Trace a Hosted Agent](https://learn.microsoft.com/azure/foundry/observability/quickstarts/quickstart-tracing-hosted-agent):
  Hosted Agent traces expose end-to-end latency and behavior and can be searched by trace or response
  ID before release.