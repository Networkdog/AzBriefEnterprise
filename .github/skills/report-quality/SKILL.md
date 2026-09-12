---
name: report-quality
description: 'Evaluate and improve AzBrief report quality. Use when: report quality, report scoring, quality evaluation, improve report, report design, email report layout, scannability, actionability, CSA report standard, report structure, evaluate_report, run_quality_loop, quality metrics.'
---

# AzBrief Report Quality Evaluation & Improvement

## Foundry Runtime Guidance

- Create the reader-facing artifact only from validated evidence. Evaluate it independently
     and request only the smallest grounded correction when the assigned task is review.
- Analyze every update before classification; never silently filter coverage. Keep
     importance, tenant impact, and job relevance independent.
- Match category framing: changes explain impact and inaction risk; capabilities explain
     documented value for known workloads or supplied requirements, adoption cost, and owner.
     Resource ownership is not a relevance gate. `relevance_evidence` explains analysis-time scoped
     applicability/value, not selection. Preserve material gaps and never invent usage or plans.
     Changes require actions only for confirmed applicability; capability evaluation is optional
     and limited to one grounded, non-mutating fit check.
- Keep evidence, relevance, resource counts/reasons, and conclusions consistent. Actions name
     what, where, why, completion criteria, precautions, rollback, and only real deadlines.
- For material financial implications, report the scoped ActualCost baseline with period and
     currency in the cost-impact field. Separate observed spending from estimates; estimates need
     documented rates and matching usage, not an advertised discount on the entire bill. Preserve
     missing cost evidence and never treat empty data, denied access, or ActualCost zero as free use.
- Treat the artifact as a CSA decision brief, not feature education. Name the decision hinge,
     give a grounded recommendation plus the condition for the alternative/current state, contrast
     supported gains and trade-offs, identify the operational responsibility, and define the evidence
     that closes the decision. Never expose sales motions or invent customer plans.
- Distinguish non-mutating evaluation actions from executable changes. An `advisory_review`
     does not require CLI or rollback; treat an incomplete go/no-go check as caution, not unsafe.
     Its command must be empty or read-only; an evaluate/review task paired with `update`, `set`,
     `enable`, or another mutation is a blocking contradiction. Commands and state-changing
     Portal procedures remain fail-closed.
- Optimize for a 3-second summary and 30-second scan. Never expose internal mechanics or
     fabricate resources, work, dates, commands, or URLs.

<!-- End Foundry Runtime Guidance -->

> **Two-layer scoring.** This skill covers the fast **rule-based / mechanical** evaluator
> (`ReportQualityEvaluator`, regex heuristics, 100-pt) used as a deterministic pre-filter.
> For the **semantic G-Eval LLM-as-a-Judge** layer (five 1-5 dimensions, Chain-of-Thought,
> logprob-normalized continuous scores, self-improvement loop), see the **`report-evaluation`**
> skill. Both run together in `scripts/evaluate_report.py` — the rule check catches
> mechanical defects, the judge catches faithfulness/insight gaps a regex cannot.
> Pre-release campaigns add two non-compensating layers: trajectory/process quality and
> action-item safety. `scripts/quality_campaign.py` combines all four layers over a frozen
> diagnosis/holdout dataset; never let a high 100-point score hide a failed tool or blocked command.

## When to Use

- Evaluating generated report quality with `scripts/evaluate_report.py`
- Improving report scores through iterative generation
- Modifying report prompts in `src/agent/prompts/` to improve quality
- Adjusting email template design in `src/email/templates.py`
- Updating quality scoring criteria in `scripts/evaluate_report.py`
- Running iterative quality improvement loops

## Quick Reference

```bash
# Evaluate latest update (single run)
python -m scripts.evaluate_report --latest --with-html

# Iterative improvement (3 rounds)
python -m scripts.evaluate_report --latest --with-html --iterate 3

# Evaluate specific update
python -m scripts.evaluate_report --url "https://azure.microsoft.com/updates/..." --with-html

# Mock data evaluation (no Azure credentials needed)
python -m scripts.run_quality_loop
```

## File Map

| File | Role |
|------|------|
| `scripts/evaluate_report.py` | Quality scoring engine + CLI runner |
| `scripts/run_quality_loop.py` | Mock data evaluation demo |
| `src/agent/prompts/` | LLM prompts package — quality levers (writing.py, languages/, report/) |
| `src/email/templates.py` | HTML email rendering — design scoring |
| `src/email/service.py` | Email builder — assembles final output |
| `tests/test_quality_evaluator.py` | Unit tests for scoring logic |
| [scripts/preview_email.py](../../../scripts/preview_email.py) | Synthetic ko/en/ja single/digest previews, full-style and inline-only, without Azure calls or email delivery |
| [tests/test_email_editorial.py](../../../tests/test_email_editorial.py) | Editorial structure, links/anchors, count/identity preservation, verification display, contrast, and offline preview tests |

---

## AzBrief Report Design Philosophy

### Core Mission
AzBrief 보고서의 핵심 목적: **"모든 업데이트를 분석해 변경·은퇴에는 필요한 조치를, 신규 기능·서비스에는 얻을 수 있는 가치와 도입 조건을 알려주는 것"**

보고서 품질 3대 핵심 지표:
1. **관련성 정밀도** — 포함된 업데이트 중 실제로 관리자가 "이건 나한테 해당돼"라고 동의하는 비율
2. **조치 완결성** — 보고서만 읽고 다음 행동을 결정할 수 있는가 (Portal 탐색 없이 즉시 조치 가능)
3. **스캔 시간** — 바쁜 관리자가 30초 스캔으로 오늘 할 일을 파악할 수 있는가

### 독자 계층 분리 원칙
같은 보고서 안에서 C-Level 요약과 엔지니어 세부 내용을 분리:
- **one_line_summary**: C-Level / 매니저 — 3초 판단
- **quick_decision 운영 정보**: 관리자 — 범위·조치·일정·작업량을 2열로 빠르게 확인
- **detailed_analysis + concept boxes**: 엔지니어 — 기술적 맥락 이해
- **action_items**: 실무자 — 즉시 실행 가능한 절차

---

## Quality Scoring Model (100점 만점)

### Category 1: Content Accuracy (30점)

보고서에 포함된 정보가 정확하고 검증 가능한가.

| Criterion | Points | What it checks |
|-----------|--------|----------------|
| `relevance_classification` | 5 | 판단 근거 존재와 명시적 모순 검사. 리소스 수만으로 관련성을 판정하지 않으며 의미적 정확성은 동일 근거의 G-Eval로 검증 |
| `one_line_summary` | 5 | 30-80자, 구체적, 내부 프로세스 미노출 |
| `no_fabricated_urls` | 5 | 모든 URL이 실제 도구 결과에서 획득 |
| `relevance_evidence` | 5 | 변경의 적용 조건 또는 신규 가치와 근거. 리소스 행이 있을 때만 실제 이름/수치를 요구하고 코드·업무·요구 기반 근거도 허용 |
| `no_fabricated_dates` | 5 | "within 2 weeks" 등 조작된 기한 없음 |
| `update_category` | 5 | retirement/preview 등 update_type과 일치 + 카테고리 계열에 맞는 프레임(Capability 카테고리에서 "영향/리스크 없음" 동어반복 서술 시 항목당 -2점) |

**핵심 원칙**: 부정확한 보고서는 보고서가 없는 것보다 더 위험하다.

### Category 2: Structural Completeness (25점)

보고서 구조가 AzBrief 표준을 따르는가.

| Criterion | Points | What it checks |
|-----------|--------|----------------|
| `detailed_analysis` | 8 | 200자 이상, 소제목(`###`) 없음, concept box 3개 이하, 콘텐츠 중복 없음 |
| `impact_summary` | 5 | 내용이 있는 차원 수. Capability 카테고리는 개수를 보상하지 않고, "영향 없음"만 쓴 빈 차원을 감점 |
| `affected_resources` | 5 | Resource Graph 속성값 근거 포함 (reason 필드) |
| `reference_docs` | 4 | 1개 이상 Microsoft Learn URL 포함 |
| `additional_checks` | 3 | 미확인된 의사결정 사실을 WHAT/WHERE/WHY가 있는 자가 검증 항목으로 표현하며 CSA에게 되넘기지 않음 (선택) |

**구조 표준**:
```
1. Masthead + White Header → 전체 제목·출처, 핵심 요약, 독립적인 중요성/영향도/직무연관성 strip
2. Operational Facts → 영향 범위, 조치 필요 여부, 기한, 작업량의 2열 정보
3. Detailed Analysis → 기술 맥락, concept boxes, 환경 설명
4. Environment Relevance → 변경의 적용/조치 또는 신규 가치/도입 조건과 근거
5. Key Dates → retirement/feature_change의 날짜·작업 2열 목록
6. Impact / Opportunity → cost/security/performance/operational 차원별 정의 행
7. Affected Resources → 전체 사유와 그룹별 리소스·구독·리소스 그룹·종류
8. Action Sheets (01…) → 맥락, 절차, 고정폭 CLI, 일정, 가드레일·검증 표시
9. Additional Checks → 추가 확인 항목
10. Numbered References → 문서 링크, 내용 요약, 보고서별 확인 지점
11. Footer → 면책 고지, 피드백, 생성 시각
```

단건과 digest 상세는 같은 section formatter와 기존 조건부 표시 기준을 사용합니다.
Digest의 공통 masthead·집계·목차·종료 countdown은 상세 앞에, footer는 문서 끝에 둡니다.
집계의 비례 막대는 분석 완료 건수만 표현하고 건너뜀은 별도로 표시합니다. 상세 시작은 큰 번호와
목차 복귀 링크로 구분하며 운영 정보는 옅은 바탕의 표로 묶습니다.

### Category 3: Language Quality (20점)

보고서 텍스트가 전문적이고 자연스러운가.

> 언어별 자연스러움 규칙을 **추가·수정**하거나 번역체 결함을 코퍼스로 검증할 때는
> **`language-naturalness`** 스킬을 사용한다. 이 표는 점수 항목만 정의한다.

| Criterion | Points | What it checks |
|-----------|--------|----------------|
| `no_internal_exposure` | 5 | "Resource Graph returned", "쿼리 결과" 등 미노출 |
| `speech_level_consistency` | 5 | 합쇼체(~합니다/~입니다) 일관, 해요체 혼용 없음 |
| `translation_avoidance` | 5 | "~하는 것을 권장", "~에 의해", 사역형 "~할 수 있게 합니다" 등 번역체 없음. 서두 검사 포함 — 공지 프레임("이번 업데이트는…")과 환경 판정("현재 환경에는 ~가 없습니다") 둘 다 첫 문장 금지 |
| `sentence_ending_variety` | 5 | 동일 종결어미 4회 이상 연속 없음 |

**언어 작성 원칙**:
- **환경 연관성 명시**: 변경에는 적용 조건과 필요한 조치, 신규 역량에는 확인된 업무·워크로드의 가치와 도입 조건을 설명한다. 부재는 분석 시점과 조회 범위로 한정한다.
- **조치 문장은 동사로 시작**: "업그레이드를 고려할 수 있습니다" ❌ → "노드 풀을 1.31.x로 업그레이드합니다" ✅
- **수치는 항상 맥락과 함께**: "23개 알림" ❌ → "23개 알림 중 조치 필요: 5건" ✅
- **능동태 사용**: "정책 위반이 탐지되었습니다" ❌ → "7건의 정책 위반을 확인했습니다" ✅
- **불확실성은 솔직하게**: AI 분석 결과임을 표시, 확신 어려운 판단에는 "검토 권장" 표시

### Category 4: Actionability (15점)

보고서를 읽고 즉시 행동할 수 있는가.

| Criterion | Points | What it checks |
|-----------|--------|----------------|
| `action_items_presence` | 5 | 적용이 확인된 변경에는 필요. 신규 역량은 가치 우선이며 평가 작업은 최대 1개 선택 사항 |
| `action_items_quality` | 5 | task/why/target_resources/procedure 채움 |
| `action_items_ordering` | 5 | step 순서대로 논리적 배열 |

**조치 완결성 기준**:
- 읽는 사람이 **5분 안에 행동을 시작**할 수 있어야 함
- 각 조치 항목: **왜(why) + 어디서(procedure) + 무엇을(task) + 언제까지(deadline)**
- 미조치 시 위험(risk_if_not_done) + 사전 확인(precaution) + 롤백(rollback)

### Category 5: Scannability & Design (10점)

이메일 열었을 때 3초 안에 상황 파악이 가능한가.

| Criterion | Points | What it checks |
|-----------|--------|----------------|
| `text_formatting` | 5 | **bold** 강조, 단락 구분, 구조화된 텍스트 |
| `html_email_quality` | 5 | 테이블 레이아웃, AzBrief 브랜딩, 템플릿 변수 해소 |

**이메일 디자인 원칙**:
- **편집형 지면**: `EMAIL_COLORS`의 흰 지면, 잉크색 `#182b32`, 청록색 `#08746b`, 옅은 중성 바탕.
     짙은 남색 hero·둥근 그림자 카드 대신 제목, 핵심 요약, 독립적인 3축 평가와 운영 정보를 구분
- **공통 위계**: 단건과 digest 상세가 공통 header/section formatter를 사용. Section 제목은
     데스크톱 20.625px·모바일 17.325px·굵기 525, 핵심 문장은 18.75px·15.75px·굵기 700.
     본문은 13px·주요 블록 행간
     1.8~1.85. `cover=36`, `stat=48`로 발행물 이름·문서 제목·수치를 구분하고 모바일 제목은
     29px로 표시. 본문은 데스크톱 제목 15%·내용 85%, 목차는 전체 너비로 구성. 여러 어절인 제목은
     데스크톱에서 첫 어절 뒤 줄바꿈하며 모바일·inline-only에서는 이어 쓰고, 리소스 건수는 11px 보조 글자로 표시
- **상태는 텍스트와 색상으로 표현**: 중요성·영향도·직무연관성을 합치지 않고 각 높음/보통/낮음
     label은 기존 배지 박스 안에 13.5px 글자로 표시. 공간이 부족하면 평가축 이름과 배지를 함께 줄바꿈하며 글자를
     줄이거나 자르지 않음. 종료 countdown도 이모지 대신 현지화된 이행 상태 텍스트 사용
- **의미를 전달하는 세로 강조선**: 등급·검증 배지, 핵심 요약, concept box, 추가 확인에 공통
     `SEMANTIC_ACCENT_WIDTH_PX = 4`를 적용하고 배지 위아래 padding은 각각
     4px로 유지. 상태 텍스트·기존 색상과 텍스트 대비 **4.5:1 이상**은 보존하고 중성 구분선은 얇게 유지
- **전체 목차와 리소스 식별 정보**: 제목을 자르지 않고 상세·목차 복귀 링크를 유지. 모바일은
     전체 너비 제목 아래 세 평가 셀을 놓고 리소스 열은 이름표가 있는 셀로 쌓되 사유·그룹·Portal 링크 보존
- **좁은 화면 기본값**: CSS가 제거되어도 17px 제목·13px 요약 아래 3축 이름과 값을 표시하며
     목차 번호는 별도 29px 열로 분리. 데스크톱에서만 제목 52%·평가축 각 16%로 확장.
     발행물 로고와 날짜도 기본 세로 배치. 건수·상세 번호는 48px, 세 자리 건수는 모두 29px를 사용.
     비례 막대는 8px이며 0건 구간을 그리지 않고 수치와 제목을 생략하지 않음
- **반응형 fallback**: inline/MSO 640px, 화면 800px에서 760px·1100px에서 900px. `azb-pad` 여백은
     기본 32px, 1100px 이상 48px, 640px 이하 20px, 400px 이하 16px이며 inline-only는 32px 유지.
     영향 label은 HTML/CSS 너비·최소 너비 96px와 nowrap/keep-all을 유지하고 desktop 2×2로 나누지 않음
- **CTA 링크 포함**: Microsoft 공식 문서, Azure Portal 경로 (검증된 것만)
- **이모지 금지**: 보고서 본문에는 이모지 미사용 (이메일 제목줄은 허용)

표시 재설계는 Markdown 문법, 분석 동작, 안전한 링크·액션 검증 정책, 전송, Archive 스키마나
평가 배점을 바꾸지 않습니다. 스타일 문서 변경으로 bounded Foundry Runtime Guidance를 수정하지 않습니다.

### 디자인 개선 루프

같은 합성 데이터를 보존한 채 `가설 → 렌더러 수정 → 집중 테스트 → 미리보기 → 화면 평가 → 수정`을
반복합니다. `tests/browser/email_reports.cjs`는 ko/en/ja·단건/digest·full/inline-only를
1440/768/640/390/320/844px에서 검사합니다. `passed=true`, 정보·링크 보존, 가로 넘침과 배지
넘침 없음이 수용 조건이며, 스크린샷의 위계·줄바꿈·탐색을 따로 평가합니다. 합성 레이아웃 검사를
심미성의 객관적 점수, 실측 독해 속도, G-Eval 개선 또는 실제 Outlook/Gmail 검증으로 보고하지 않습니다.
디자인 변화가 작다는 피드백에는 이전 구도를 유지한 간격 조정만 반복하지 않습니다. 원본 보고서의
글자 크기 대비·면 구성·구획·그리드를 분해하고 같은 크기의 전후 지면에서 변화가 식별되는지 평가합니다.

---

## Scoring Engine Architecture

### `ReportQualityEvaluator` class (`scripts/evaluate_report.py`)

```python
evaluator = ReportQualityEvaluator()
qr = evaluator.evaluate(result, update, html_content, language="ko")

# qr.total_score: 0-100
# qr.percentage: 0.0-100.0
# qr.grade: S/A+/A/B+/B/C/D/F
# qr.category_scores: {"content_accuracy": {"score": 30, "max": 30, ...}, ...}
# qr.improvement_suggestions: ["[category/item] Fix this...", ...]
# qr.critical_issues: ["Missing relevance_evidence", ...]
```

### Grade Boundaries

| Grade | Percentage | Meaning |
|-------|-----------|---------|
| S | ≥ 95% | 만점 근접 — 상용 품질 |
| A+ | ≥ 90% | 우수 — 사소한 개선만 필요 |
| A | ≥ 85% | 양호 |
| B+ | ≥ 80% | 보통 이상 |
| B | ≥ 75% | 보통 |
| C | ≥ 65% | 개선 필요 |
| D | ≥ 50% | 심각한 문제 |
| F | < 50% | 사용 불가 |

### Iterative Improvement Loop

```
Generate Report → Evaluate (score) → Build Feedback Prompt
     ↑                                        ↓
     └────── Inject Feedback into System Prompt ──┘
```

`--iterate N` 옵션은 이 루프를 N회 반복합니다. 각 반복에서:
1. 보고서 생성 (Azure OpenAI)
2. 품질 평가 (점수 + 감점 항목)
3. 피드백 프롬프트 생성 (`_build_feedback_prompt`)
4. 피드백을 `custom_system_prompt`에 주입
5. 95% 이상이면 조기 종료

---

## Common Quality Issues & Fixes

### Issue: 종결어미 반복 (sentence_ending_variety)
**증상**: `합니다` 4회 이상 연속
**원인**: LLM이 한국어 합쇼체에서 `~합니다` 종결을 과용
**수정**:
- `src/agent/prompts/languages/ko.py`의 한국어 스타일 가이드 §7 (1) "같은 표현의 반복" 한도 표 강화
- 평가자는 복합 종결형 (`해야 합니다` vs `합니다`)을 별도로 인식

### Issue: 내부 프로세스 노출 (no_internal_exposure)
**증상**: "Resource Graph 쿼리 결과" 같은 문구가 보고서에 등장
**원인**: LLM이 도구 호출 과정을 보고서에 포함
**수정**: SYSTEM_PROMPT의 "Report Writing Standards" §1 "내부 프로세스 비공개" 규칙 강화

### Issue: 번역체 패턴 (translation_avoidance)
**증상**: "~하는 것을 권장합니다", "~에 의해" 등
**원인**: LLM의 영어 원문 직역 경향
**수정**: SYSTEM_PROMPT의 한국어 §3 "번역체 회피" 규칙에 BAD/GOOD 예시 추가

### Issue: 사역형 "~할 수 있게 합니다" (주술 불일치)
**증상**: "이번 GA는 appliance를 VNet에 배치해, ... 전달할 수 있게 합니다" — 공지가 배치 주체처럼 서술됨
**원인**: 영어 "This GA ... enables you to ..." 직역. *가능하게 만드는 주체*(공지·기능)와 *행위 주체*(관리자)가 한 문장에 혼재
**수정**: `languages/ko.py` §3 사역형 금지 규칙 (`ja.py` §3에 동일 규칙). 행위 주체 기준으로 문장 분리("이제 ~배치할 수 있습니다") 또는 조건-결과 연결("~배치하면 ~처리할 수 있습니다")

### Issue: 공지를 주어로, 분류어를 서술어로 쓴 문장 (범주 불일치 + 동어 반복)
**증상**: "이번 **업데이트**는 ...을 추가하는 public preview**입니다**" / "이번 preview는 ...하는 기능입니다" / "이번 GA는 ...한 변화입니다"
— 공지≠출시 단계/기능/변화이고, "업데이트"가 주어와 서술부에 중복
**빈도**: `기능입니다` 31 docs, `변화입니다` 15 docs, 출시 단계 15 docs (563 문서 기준)
**원인**: 영어 "This update is a public preview that adds ..." 직역
**수정**: `languages/ko.py` §3 "공지를 주어로, 분류어를 서술어로 쓰지 않기" (`ja.py` §3, `en.py` §2에 동일 규칙).
주어는 실제 추가·변경된 대상, 단계는 "~로" 부사구: "{기능}이 public preview로 추가되었습니다".
변화가 둘 이상이면 "~되었으며, 이제 ~되었습니다"로 대등하게 분리

### Issue: 참고 문서 누락 (reference_docs)
**증상**: reference_docs가 빈 배열
**원인**: Microsoft Learn 검색에서 결과 없음 + 업데이트 Learn More 링크 미활용
**수정**: REPORT_PROMPT에서 업데이트 공지 본문의 Learn More URL을 reference_docs에 포함하도록 지시

### Issue: 조치 항목 procedure 누락 (action_items_quality)
**증상**: action_items에 procedure/cli_command 없음
**원인**: LLM이 검증 안 된 명령을 조작하지 않은 것 (정확성 원칙 준수)
**대응**: reference_docs가 있으면 procedure 미작성을 허용 (정확성 > 완결성)

### Issue: "CSA 사전 검토" 헤지 남발 (additional_checks 신뢰 저하)
**증상**: `additional_checks` 대부분이 "CSA 사전 검토가 필요합니다"/"별도 검증 필요"로 끝남 (독자가 CSA면 순환 논리)
**원인**: 정확성 원칙 2가 "flag for CSA review"로 표현되어 LLM이 미확인 항목을 일괄 위임
**수정**: `core.py` 원칙 2 + `writing.py` 원칙 5를 **self-serviceable** 체크로 재작성 — WHAT/WHERE(Portal blade·CLI·doc)/WHY를 명시. 툴이 이미 답한 리전/SKU 질문은 재제기 금지. `base.py` 출력 포맷·self-check에 금지 규칙 추가

### Issue: GA/Preview의 주사용 Region 가용성이 provider 존재로 과대 확정됨
**증상**: `Microsoft.Network`가 Korea Central에 있다는 이유만으로 그 위의 신규 기능도 즉시
사용 가능하다고 보고하거나, 반대로 기능 문서에 Region 답이 있는데도 추가 확인으로 넘김
**원인**: ARM providers API의 resource-type 배치 범위와 기능별 rollout 범위를 같은 사실로 취급
**수정**: Resource Graph의 Region별 리소스 수로 상위 3개 주사용 Region을 정하고, Azure Update
상세 원문 및 `search_azure_docs(include_content=true, focus_terms=[...])`의 기능 본문을 우선합니다.
정확한 SKU/resource type API는 배치 가용성만 증명합니다. Evaluator는 Region별 네 가지 결과
(즉시 사용/선행 조건부/미지원/공식 근거 미확인)를 요구하고, 한 줄 요약 누락 시 한 번 재작성합니다.

### Issue: 리소스 부재를 무관함으로 단정하거나 기회에 의무 작업을 붙임
**증상**: 신규 서비스에 기존 리소스가 없다는 이유로 가치 설명을 생략하거나, `opportunity`마다
"평가하세요"를 강제로 추가함. 단건·Archive의 선택 이유 제목도 실제 적용/가치 근거와 충돌함.
**수정**: `base.py`, 계획·평가 지침과 category template은 변경의 적용/조치와 신규 역량의
가치/도입 조건을 구분한다. 기존 배포 없이도 확인된 업무·요구에 맞는 가치를 설명할 수 있지만
실제 도입 계획은 지어내지 않는다. 유용한 대상과 문서의 go/no-go 기준이 있을 때만 최대 1개
비변경 평가 action을 작성하고, 그렇지 않으면 `[]`가 맞다. 이름/숫자 없는 근거와 정당한 빈
action/resource 배열을 기계 평가에서 일괄 감점하지 않으며, 근거 없는 판정과 모순은 계속 검출한다.
평가 기준이 달라졌으므로 이전 총점과 직접 비교해 품질 개선을 주장하지 않는다.

### Issue: 신규 기능·서비스 보고서에 "없던 시절"이 빠짐
**증상**: `new_feature`/`new_service`/`preview` 보고서가 기능 설명과 이점만 나열하고, 그 기능이 없을 때 관리자가 같은 결과를 얻으려고 무엇을 했는지가 없어 변화의 크기를 가늠할 수 없음
**원인**: `base.py` 분석 본문 section 1에 soft bullet("If this is a new capability, explain what was impossible before") 한 줄뿐이라 자주 생략됨
**수정**: section 1을 **MANDATORY** 규칙으로 승격 — 이전 방식을 구체적으로(직접 운영하던 구성 요소 / 수동 절차 / 서드파티 제품 / 감수하던 제약) 지목하고 지금 무엇이 그 자리를 대신하는지 서술. 지어내기 금지(문서·업데이트 본문 근거, 확인 불가면 제거되는 제약을 대신 기술). **정형 문장 금지** — "이전에는 X, 이제는 Y"를 매번 같은 자리에 같은 문형으로 쓰면 그 자체가 단조로움 결함이므로 설명 안에 녹인다. `categories.py`의 new_feature·new_service 1번, preview 2번 항목이 이 대비를 문제 정의로 요구하고, `languages/{ko,en,ja}.py`에 각 1곳 GOOD/BAD 예시

### Issue: reference_docs에 SafeLinks/추적 URL 노출
**증상**: `nam06.safelinks.protection.outlook.com/?url=…`, `?ocid=…&msclkid=…` 같은 거대·추적 URL이 참고 문서로 렌더링
**원인**: RSS `learn_more_links` href가 SafeLinks로 래핑되어 유입, LLM이 그대로 복사
**수정**: `src/rss/parser.py::clean_url()` (SafeLinks 재귀 언래핑 + 추적 파라미터 제거, `?view=`/`?tabs=`/fragment 보존)를 파싱 시점(learn_more_links)과 리포트 조립 시점(`analyzer._normalize_reference_urls`) 양쪽 적용. `base.py`는 공지 자체 URL보다 Microsoft Learn 우선 지시

---

## AzBrief-Specific Report Standards

### 중요도 분류 (Digest 보고서)
일괄 분석(digest) HTML의 집계는 **분석 완료 항목**의 중요도를 세며 영향도·직무연관성과
구분합니다. 건너뜀은 낮음 건수에 더하지 않고 별도로 표시합니다. 예:
```
전체 목록: 24건
분석 완료: 23건 — high 3 / medium 8 / low 12
건너뜀: 1건 — 별도 집계, 목록에는 유지
```
모든 입력 항목을 목차에 남깁니다. 분석 항목은 번호와 전체 제목에서 상세 anchor로 이동하고,
상세의 복귀 링크로 목차에 돌아옵니다. 이 표시는 기존 분석·전달 정책을 바꾸지 않습니다.

### Resource Graph 매칭 근거 필수
각 영향 리소스 항목에 **왜 이 리소스가 선택됐는지** Resource Graph 속성값 기반 근거를 포함:
```
name: aks-aigora-dev
reason: "nodeImageVersion: AKSUbuntu-2204gen2containerd-202604.01.0 — Ubuntu 22.04 지원 종료 대상"
```
이것이 AzBrief가 단순 RSS 리더와 차별화되는 핵심.

### 수치 + 변화량 함께 제시 (CSA CISO 가이드 원칙)
- "MTTR 1.2시간" ❌ → "MTTR 40% 개선(전분기 대비)" ✅
- "300개 인시던트 처리" ❌ → "고객 데이터 파이프라인 잠재 침해를 4분 내 차단" ✅
- "Storage Account 3개 영향" ❌ → "Storage Account 22개 중 3개 영향 (14%)" ✅

### 섹션별 Takeaway 한 줄
독자가 해당 섹션에서 딱 하나를 기억해야 한다면 무엇인지 명시:
- `one_line_summary`: 전체 보고서의 takeaway
- `relevance_evidence`: "왜 이게 나한테 해당되는가"의 takeaway
- 각 action_item의 `task`: 해당 조치의 takeaway

### 이메일 포맷 호환성
| 요소 | 권장 방식 | 이유 |
|------|----------|------|
| 상태 표시 | urgency badge 텍스트 + 색상 | 다크모드/텍스트 뷰어 호환 |
| 섹션 구분 | border-top + bold 제목 | 이메일 CSS 미지원 대비 |
| 리소스 목록 | 전체 표시 | 관리자가 전수 확인 필요 |
| CTA | 텍스트 링크 "→" | 이미지 차단 환경 대비 |
| 제목 라인 | `[AzBrief] [긴급] 요약 | 날짜` | 오픈율 최적화 |

### 오프라인 디자인 검증

```powershell
& .\.venv\Scripts\Activate.ps1
python -m scripts.preview_email --output-dir out/email-editorial-preview --language all
python -m pytest tests/test_email.py tests/test_email_editorial.py -o "addopts=" -q
```

미리보기는 전송 설정·종료 이력을 mock한 **SYNTHETIC** ko/en/ja 단건·digest HTML 12개
(전체 스타일/inline-only)를 만들며 Azure 호출이나 이메일 발송은 없습니다. 편집형 테스트는
구조·탐색·집계·전체 제목·리소스 식별·검증 표시와 지정된 색상 대비 **4.5:1 이상**을 검사합니다.
Focused 통과나 합성 미리보기를 전체 suite·브라우저·실제 메일 client 검증 또는 분석 품질 향상으로
보고하지 않습니다. 완료한 검증만 별도로 기록합니다.

---

## Modifying Scoring Criteria

### Adding a New Criterion

1. `scripts/evaluate_report.py`의 해당 카테고리 메서드에 `ScoreItem` 추가
2. `max_score` 합계가 카테고리 총점과 일치하는지 확인
3. `tests/test_quality_evaluator.py`에 테스트 추가
4. 이 SKILL.md의 scoring table 업데이트

### Adjusting Point Allocation

전체 합계는 반드시 **100점**을 유지:
- Content Accuracy: 30점
- Structural Completeness: 25점
- Language Quality: 20점
- Actionability: 15점
- Scannability & Design: 10점

카테고리 내 개별 항목 점수는 자유롭게 조정 가능.

### Testing Changes

```bash
# 평가자 로직 단위 테스트
python -m pytest tests/test_quality_evaluator.py -v -o "addopts="

# 모의 데이터로 전체 루프 테스트
python -m scripts.run_quality_loop

# 실제 Azure 환경에서 품질 확인
python -m scripts.evaluate_report --latest --with-html --iterate 3
```

---

## Prompt Improvement Checklist

보고서 점수를 올리려면 `src/agent/prompts/`의 다음 파일을 조정:

| 점수 항목 | 프롬프트 위치 | 조정 방법 |
|-----------|-------------|----------|
| one_line_summary | REPORT_PROMPT "Executive One-liner" | 패턴 예시 추가 |
| relevance_evidence | REPORT_PROMPT "Output Format" | 근거 포함 예시 강화 |
| detailed_analysis | REPORT_PROMPT "Analysis Body" | 구조/길이 가이드 조정 |
| concept boxes | REPORT_PROMPT "Concept Explanation Boxes" | 필요 기반(quota 아님) — 과잉 설명 억제 |
| translation_avoidance | SYSTEM_PROMPT "Korean §3" | BAD/GOOD 예시 추가 |
| sentence_ending_variety | SYSTEM_PROMPT "Korean §7" | 종결어미 규칙 강화 |
| action_items | REPORT_PROMPT "Action Items" | 필수 필드 명시 |
| impact_summary | REPORT_PROMPT Self-Check | 차원 채움 규칙 강화 |
| update_category (프레임) | `report/base.py` "Report Frame Follows the Category" + `report/categories.py` 각 카테고리 `impact_summary` 항목 | Change 계열(retirement/feature_change/pricing)은 영향·리스크, Capability 계열(new_feature/new_service/region_expansion/preview/sdk_tooling)은 기회 서술. Capability에 "운영 영향 없음/미도입 리스크 없음"은 동어반복이라 금지 |

### Self-Check Checklist (REPORT_PROMPT 말미)

프롬프트 끝에 있는 "Pre-Submission Quality Self-Check"는 LLM이 최종 JSON 출력 전 검증하는 체크리스트.
새로운 품질 기준을 추가하면 여기에도 반영해야 함.
