---
name: language-naturalness
description: 'Audit and improve per-language naturalness of AzBrief report text (ko/en/ja). Use when: 자연스러운 문장, 번역체, 직역투, 주술 호응, 문체 단조로움, reads like machine translation, language quality, style guide, KOREAN_STYLE_GUIDE, ENGLISH_STYLE_GUIDE, JAPANESE_STYLE_GUIDE, translation_patterns, sentence ending variety, add a language rule, corpus scan for a phrasing defect.'
---

# Per-Language Naturalness Audit

## Foundry Runtime Guidance

- Write as a native senior engineer in the requested language while preserving facts,
  release stage, names, IDs, commands, dates, and uncertainty.
- State what changed directly. Avoid announcement framing, subject-predicate category
  mismatch, nominalization, passive defaults, causative translation, and repeated endings.
- In Korean, keep 합쇼체, write `약어(풀네임)`, and replace generic "CSA 사전 검토" with
  what to check, where, and why.
- Apply the rules to every user-facing field. Definition-style concept boxes are the
  exception to a blanket ban on noun-ending sentences.

<!-- End Foundry Runtime Guidance -->

Checks whether generated report text reads as if a native senior engineer wrote it —
per language (`ko`, `en`, `ja`) — and turns a reader's complaint into a rule that actually
holds across the whole corpus.

> **Scope.** This skill covers *how the sentences read*. For the 100-pt mechanical report
> score see **`report-quality`**; for the semantic LLM-as-a-Judge see **`report-evaluation`**.
> Neither of those scores naturalness (see the honest limit below), which is why this skill exists.

## When to Use

- A reader says a sentence "reads as AI-generated" / "번역체 같다" / "부자연스럽다"
- Adding or changing a rule in `src/agent/prompts/languages/{ko,en,ja}.py`
- Adding a pattern to `translation_patterns` in `scripts/evaluate_report.py`
- Auditing a batch of generated reports for recurring phrasing defects
- Deciding whether a phrasing defect is worth a prompt rule at all

## File Map

| File | Role |
|------|------|
| `src/agent/prompts/languages/ko.py` | `KOREAN_STYLE_GUIDE` — 8 sections: 문체/문장 구조/번역체/어휘/용어/표기/단조로움/concept box |
| `src/agent/prompts/languages/en.py` | `ENGLISH_STYLE_GUIDE` — voice, sentence structure, word choice, precision, concept boxes |
| `src/agent/prompts/languages/ja.py` | `JAPANESE_STYLE_GUIDE` — 文体/文構造/翻訳調/語彙/専門用語 |
| `src/agent/prompts/report/base.py` | Shared (language-agnostic) output rules — headings, concept boxes, self-check |
| `scripts/evaluate_report.py` | `_evaluate_language` → `_evaluate_korean_quality` / `_evaluate_english_quality` |
| `scripts/optimize_prompt.py` | **Autonomous A/B loop** — fixed update sample → generate → deterministic defect score → LLM rewrites one section → re-measure → keep/revert |
| `tests/test_quality_evaluator.py` | Regression tests for each mechanical language pattern |
| `results_*.jsonl`, `eval_runs/**/report*.md` | The measurement corpus (see Step 1) |

---

## Where naturalness is (and is not) enforced

| Layer | Mechanism | Catches | Limit |
|-------|-----------|---------|-------|
| **1. Prompt** | `{KO,EN,JA}_STYLE_GUIDE` injected in the report phase | Everything — this is the only layer that can *produce* natural text | Advisory only. There is **no per-run measurement** of whether a given rule was obeyed |
| **2. Mechanical** | `ReportQualityEvaluator._evaluate_language` (regex, 20 pts) | Known phrasings, deterministically, on every run | `ko` has 3 checks, `en` has 3, **`ja` has none** — `ja` falls to the default branch and is awarded a flat 12/15 |
| **3. Reader / corpus** | This skill's Step 1 scan + human review | Novel defects a regex was never written for | Manual; needs a corpus |

**Honest limit — do not expect the judge to catch 번역체.** `GEvalJudge` scores five dimensions
(`actionability`, `faithfulness`, `job_relevance`, `structure`, `architectural_depth`).
None of them is language naturalness, and `structure` explicitly judges Markdown layout, not prose.
A report full of 직역투 can score 4.0. Naturalness regressions are invisible to G-Eval by design.

---

## Workflow: measure → decide → fix → verify

### Step 1 — Measure the corpus before writing any rule

**Never promote a reader's correction straight into a rule.** A correction proves *one sentence*
was wrong, not that a *rule* is missing. Count the shape first.

Corpus (as of this writing: **332 documents** = 231 JSONL analyses + 101 `eval_runs` reports):

```powershell
& .\.venv\Scripts\Activate.ps1
```

Write the scanner to a file (never inline `python -c` with a Korean/regex argument — see Gotchas):

```python
# .tmp_lang_scan.py  — delete after use
import glob, json, re, sys

pat = re.compile(sys.argv[1])
docs = []
for f in glob.glob("results_*.jsonl"):
    for line in open(f, encoding="utf-8"):
        a = json.loads(line)["analysis"]
        parts = [a.get("relevance_reason") or "", a.get("one_line_summary") or "",
                 a.get("impact_summary") or ""]
        parts += [str(i.get("procedure") or "") for i in (a.get("action_items") or [])]
        docs.append("\n".join(parts))
for f in glob.glob("eval_runs/**/report*.md", recursive=True):
    docs.append(open(f, encoding="utf-8").read())

hits = [d for d in docs if pat.search(d)]
print(f"docs={len(docs)}  matched_docs={len(hits)}  "
      f"occurrences={sum(len(pat.findall(d)) for d in docs)}")
for d in hits[:3]:
    m = pat.search(d)
    print("  ...", d[max(0, m.start() - 60):m.end() + 20].replace("\n", " "))
```

```powershell
python .tmp_lang_scan.py "수 있게 (?:합니다|해 줍니다|해줍니다|만듭니다)"
# docs=332  matched_docs=17  occurrences=17
```

**Three measurement errors, each of which has already produced a false conclusion here:**

| Error | Measured example (332-doc corpus) | Guard |
|-------|-----------------------------------|-------|
| Regex language ≠ artifact language | English connectives (`but\|however\|trade-off`) over Korean reports → **0.9%** — a near-zero that says nothing about the reports | Match the pattern language to the report language |
| Corpus predates the fix under test | All 231 JSONL records were analyzed **2026-07-18**. A rule added after that date **cannot** be validated against them — e.g. the 사역형 rule landed 2026-08-05, so its 17 hits are the *motivation*, not a disobedience count | Check `analyzed_at`; regenerate reports to validate a new rule |
| Pattern too narrow | Explicit vocabulary only (`트레이드오프\|대가로`) → **0.0%**; the same idea as natural constructions (`하는 대신\|지만\|반면\|다만`) → **91.9%** | Widen once and re-check before reporting an absence |

> A `0%` reading is far more often a bad pattern than a real absence.

#### Establish the noise floor before believing any A/B result

The same prompt does **not** produce the same text twice. Measured 2026-08 on a fixed
6-update sample (`scripts/optimize_prompt.py --measure`), running the **identical** prompt
three times gave `per_1k` = **1.884 / 2.202 / 2.272** — spread **0.388**, stdev 0.169.

So a prompt change that moves the metric by less than ~0.4/1k on this sample size has
proved nothing. Use `--margin` (default 0.4) and prefer 2+ measurements per configuration.
The loop's own accepted change was 2.189 → 1.013 = **1.176**, three times the noise — that
one was real.

#### Prompt length is a measurable cost, not a free parameter

Same rules, different prompt length, same fixed sample:

| ko guide | §7 content | mean `per_1k` | n |
|----------|-----------|---------------|---|
| 9,492 | rewritten §7, **structured-field rules deleted** | **1.03** | 2 |
| 10,304 | rewritten §7 + rules restored **compressed** | 1.64 | 2 |
| 11,120 | rewritten §7 + rules restored verbatim | 2.12 | 3 |

Adding ~1,600 chars of *unrelated but correct* rules cost roughly **+1.1 defects per 1,000
Korean chars**. Every rule you add dilutes every other rule. Prefer compressing an existing
rule to appending a new one.

### Step 2 — Decide: promote or reject

Real decisions from this repo, with the counts that drove them:

| Candidate | Corpus count (332 docs) | Decision |
|-----------|------------------------|----------|
| 공지 주어 + 분류어 서술어 (`이번 preview는 … 기능입니다`) | **49 docs / 50 occ** | **Promote** — dominant defect, and the rule generalizes beyond the release-stage form |
| 공지 주어 + `~하는 내용/공지입니다` (은퇴·종료 공지) | **13 docs / 13 occ** | **Promote** — same root defect, different surface; the existing rule's examples were all *feature additions*, so retirements never matched |
| 공지를 원인 부사구로 (`이번 GA로 … 사용할 수 있습니다`) | 5 docs / 5 occ | **Promote** despite the low count — reader-reported on *fresh* output, corpus predates the current prompt, and the rewrite (`이제 …`) generalizes |
| 은퇴 (retire 직역, 동사형) | 1 doc / 1 occ | **Promote** — one substitution row, 0 false positives; the noun form (`은퇴 대상`) is left alone |
| 사역형 `~할 수 있게 합니다/해 줍니다/만듭니다` | **17 docs / 17 occ** | **Promote** — conflates enabler and actor; a whole class, not one sentence |
| `정식으로 사용` (GA 의역) | 4 docs / 4 occ | **Reject as a regex** — correct Korean when the update really is GA. The real defect (promoting a preview to GA) is a *faithfulness* issue, handled by a prompt rule instead |
| `지원되는` → `지원하는` (passive→active) | 16 docs / 17 occ, **all attributive** (`지원되는 최신 버전`, `지원되는 OS SKU`) with no agent | **Reject** — a blanket rule would break natural Korean |
| `수작업으로` → `수동으로` | 5 docs / 6 occ | **Reject** — not worth a line in a 17k-char prompt |

**Evidence that promoting to Layer 2 works** (correlation, not proof — the prompt rule and the regex
landed together): the three patterns mechanically enforced since **2026-04-18** (`되어지`,
`하는 것을 권장`, `에 의해`) score **0 occurrences** across all 332 documents, while 사역형 and
분류어 서술어 — added to `translation_patterns` only on **2026-08-05**, after the corpus was
generated — still appear 17 and 49 times in it.

Rules of thumb:
- **Promote** when the shape recurs across many documents *and* the fix generalizes to a rewrite recipe.
- **Reject** when the corpus shows the "bad" form is usually correct in context, or when the count is a handful.
- Prompt budget is finite. Every added line dilutes the rest.
- Before adding, **grep the style guide for the rule you are about to write** — it may already exist but be
  buried under an unrelated heading or written in vocabulary the model never emits, which makes it a no-op.
  Promote it to its own named bullet instead of adding a near-duplicate.

### Step 3 — Fix in up to four places

| # | Place | Always? |
|---|-------|---------|
| 1 | The language's style guide (`ko.py` / `en.py` / `ja.py`) — state the **general principle** under its own bullet heading, then BAD → GOOD | Yes |
| 2 | Sibling languages | **Only if the defect exists there.** `ko`+`ja` share the causative defect (`〜できるようにします`); English "enables you to" is idiomatic, so `en.py` was deliberately left alone. The classifier-predicate defect applies to all three |
| 3 | `translation_patterns` in `scripts/evaluate_report.py` | For `ko`, yes — prompt rules are not reliably obeyed, so mechanical enforcement is the safety net |
| 4 | `tests/test_quality_evaluator.py` | Yes — **two** tests: the BAD form is flagged, **and** the recommended GOOD rewrite is *not* flagged |

The BAD/GOOD pair must use the vocabulary the model actually emits. Writing the rule with
*공개 미리 보기* when the model writes *public preview* makes it a no-op.

#### Write the rule as a construction test, not a phrase list

**A blacklist relocates the defect; it does not remove it.** When a phrase is banned, the model
reaches for the nearest *unbanned synonym* of the same shape. Measured here (2026-08): the guide
banned 성격/형태/변화/구조/기능/내용입니다 across §2, §3 and §7(3) — but never 점/지점/방식/의미.
Every sentence a reader flagged used exactly those four:
"…했다는 **점입니다**", "달라지는 **지점은**", "보내는 **방식입니다**", "되었다는 **의미이며**".

So prefer **one test the model can apply to every sentence** over N phrase bans:

| Instead of | Write |
|------------|-------|
| a list of banned nouns | "서술어가 **의존명사 + 입니다** 꼴이면 다시 쓴다. 명사 안에 갇힌 동사를 서술어로 끌어올린다" |
| a list of banned hedges | "확실한 사실은 단정한다" + one worked rewrite |

And **always state the carve-out**, or the rule over-corrects: concept boxes are *required* to end
with `~입니다`, so a blanket ban on noun endings would break §8. The mechanical net must mirror the
carve-out (here: skip `>` blockquote lines).

> Corollary: a rule that already exists but has **no Layer-2 net** is close to a no-op. All 12
> expressions in `ko.py` §7(3) had zero `translation_patterns` coverage, and one of them
> (`구조입니다`) shipped in a live report on the day this was checked.

### Step 4 — Verify

```powershell
& .\.venv\Scripts\Activate.ps1
python -c "import src"
python -m pytest tests/test_quality_evaluator.py -o "addopts=" -q
python .tmp_lang_scan.py "<new pattern>"   # must flag the real cases, 0 false positives on the GOOD forms
Remove-Item .tmp_lang_scan.py
```

Validate the new regex **both ways** against the corpus: it should match the documents that
motivated it and match none of the recommended rewrites.

---

## Per-language audit checklist

Highest-yield checks when reading a generated report. Full rules live in the style guides.

### 한국어 (ko)

| Check | Defect | Fix |
|-------|--------|-----|
| 주술 호응 | 주어와 서술어의 범주가 다름 — "이 기능은 …업데이트입니다" | 주어·서술어만 뽑아 읽는다. 범주가 다르면 문장을 다시 세운다 |
| **명사화 종결** | 서술어가 의존명사+입니다 — "…했다는 **점입니다**", "…보내는 **방식입니다**", "…되었다는 **의미이며**" | 명사 안에 갇힌 동사를 서술어로 — "출시했습니다", "보냅니다". 개념 박스의 정의문은 예외 |
| 공지를 주어로 | "이번 GA는 …기능/변화/public preview입니다", "이번 공지는 …한다는 내용입니다" | 주어를 **실제로 추가·변경·종료되는 대상**으로, 출시 단계는 "~로" 부사구 |
| 공지를 원인 부사구로 | "이번 GA로 … 사용할 수 있습니다" | 시점 부사로 시작 — "이제 … 사용할 수 있게 되었습니다" |
| 출시 단계 의역·승격 | preview를 "정식으로 사용"으로 서술 | 원문 표기 그대로 (GA / public preview) |
| 은퇴 직역 | "2026년 9월 1일에 은퇴합니다" | "2026년 9월 1일부터 제공이 종료됩니다" |
| 사역형 | "~할 수 있게 합니다" (enables you to 직역) | 행위 주체 기준으로 분리하거나 조건-결과("~하면 ~할 수 있습니다")로 연결 |
| 이중 피동 | "~되어집니다" | "~됩니다" |
| 종결어미 3연속 | "~합니다. ~합니다. ~합니다." | 중간 문장의 종결을 교체 |
| 추상 분류어 | "~한 성격입니다 / ~한 형태입니다 / ~한 변화입니다" | 직접 서술 |
| 방향·구조 남용 | "~하는 방향의 / ~하는 구조입니다" | 동사로 직접 서술 |
| 헤징 하드오프 | "CSA 사전 검토가 필요합니다" | **금지** — 독자가 CSA 본인. 무엇을·어디서·왜를 직접 명시 |
| 해요체 혼용 | "~해요 / ~죠" | 합쇼체 통일 |
| 약어 표기 | "Dynamic Data Masking(DDM)" 역순 | **약어(풀네임)** 순서 |

### English (en)

| Check | Defect | Fix |
|-------|--------|-----|
| Subject-complement category | "This update is a feature that…" | An update is not a feature — "This update adds a feature that…" |
| Announcement as subject or cause | "This announcement is about X being retired", "With this GA, you can now…" | Start from the time ("Now…", "From <date>…") or from the thing that changed |
| Malformed reason clause | "The reason is because…" | "The reason is that…" / drop the frame |
| Passive default | "TLS connections will be blocked by this update" | "This update blocks TLS connections" |
| Existential filler | "There are 3 accounts that use…" | "3 accounts use…" |
| Weak verb + noun | "perform an upgrade" | "upgrade" |
| Double hedging | "may potentially impact" | "may impact" (only when genuinely uncertain) |
| Weasel quantities | "various resources" | "18 of 22 Storage Accounts" |
| Repeated sentence openings | "This update… This update… This update…" | Vary the subject |

### 日本語 (ja)

| Check | Defect | Fix |
|-------|--------|-----|
| 主述の呼応 | 「このアップデートは…機能です」 | 「この機能は…」/「このアップデートは…機能を追加します」 |
| 告知が主語・原因 | 「今回の告知は〜という内容です」「今回の GA により〜利用できます」 | 時点表現か、実際に変わった対象から書き始める |
| 使役形 | 「〜できるようにします」 | 行為主体で分割、または条件-結果で接続 |
| 冗長表現 | 「〜することができます」「〜を行う」 | 「〜できます」「直接動詞」 |
| 受動態 | 「期限が設定されています」 | 「期限は2024年10月です」 |
| 二重否定 | 「影響がないとは言えません」 | 「影響がある可能性があります」 |
| 名詞化構文 | 「アップグレードすることが推奨されます」 | 「アップグレードを推奨します」 |
| 文体混在 | です/ます調 と だ/である調 | です/ます調で統一 |

> **`ja` has no mechanical check.** `_evaluate_language` routes `ja` to the default branch
> (flat 12/15). A Japanese naturalness regression will not show up in the score — it must be
> caught by corpus scan or reading. Adding `_evaluate_japanese_quality` is the obvious next step
> if `ja` output ever ships to real readers.

---

## Known Gaps

- **`ja` is unscored** (above).
- **`en` is thin**: only passive-voice ratio, a 5-phrase hedging list, and repeated sentence openings —
  none of the subject-complement or weak-verb rules from the style guide are enforced.
- **Only `relevance_reason` is scored.** `_evaluate_language` reads `result.relevance_reason` only;
  `one_line_summary`, `impact_summary`, and `action_items[].procedure` are never language-checked,
  even though the corpus scan above covers them.
- **Prompt obedience is unmeasured per rule.** Nothing reports "rule X was violated in this run".
  Assume a new prompt rule is only partially obeyed until a corpus scan on *freshly generated* reports
  says otherwise.

## Gotchas

| Gotcha | Why | Do this |
|--------|-----|---------|
| Markdown backticks vanish in `python -c "..."` | Backtick is PowerShell's escape char — `` `a `` becomes BEL | Use a `.py` file, or `chr(96)` |
| `grep_search` / `file_search` return nothing for `eval_runs/**` | `eval_runs/` is in `.gitignore` and those tools are ignore-aware | Use `glob.glob(..., recursive=True)` in Python or `Get-ChildItem -Recurse` |
| `[^.!?]*` sentence regex truncates at a decimal | `requires TLS 1.2` → `requires TLS 1.` then dropped by a length filter — kills exactly the version-specific sentences | Mask `(?<=\d)\.(?=\d)` before matching, restore after |
| A rule "already exists" but keeps being violated | It is buried under an unrelated heading, or phrased in vocabulary the model does not emit | Promote it to its own named bullet with the model's own wording |
| Reader correction → immediate prompt edit | One wrong sentence ≠ a missing rule; the count often reveals a *bigger* defect the correction only hinted at | Always run Step 1 first |
