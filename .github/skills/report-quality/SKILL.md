---
name: report-quality
description: 'Evaluate and improve AzBrief report quality. Use when: report quality, report scoring, quality evaluation, improve report, report design, email report layout, scannability, actionability, CSA report standard, report structure, evaluate_report, run_quality_loop, quality metrics.'
---

# AzBrief Report Quality Evaluation & Improvement

> **Two-layer scoring.** This skill covers the fast **rule-based / mechanical** evaluator
> (`ReportQualityEvaluator`, regex heuristics, 100-pt) used as a deterministic pre-filter.
> For the **semantic G-Eval LLM-as-a-Judge** layer (five 1-5 dimensions, Chain-of-Thought,
> logprob-normalized continuous scores, self-improvement loop), see the **`report-evaluation`**
> skill. Both run together in `scripts/evaluate_report.py` — the rule check catches
> mechanical defects, the judge catches faithfulness/insight gaps a regex cannot.

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

---

## AzBrief Report Design Philosophy

### Core Mission
AzBrief 보고서의 핵심 목적: **"관련 없는 정보를 걸러내고, 내 환경에 직접 영향을 주는 업데이트만 골라서 다음 행동을 알려주는 것"**

보고서 품질 3대 핵심 지표:
1. **관련성 정밀도** — 포함된 업데이트 중 실제로 관리자가 "이건 나한테 해당돼"라고 동의하는 비율
2. **조치 완결성** — 보고서만 읽고 다음 행동을 결정할 수 있는가 (Portal 탐색 없이 즉시 조치 가능)
3. **스캔 시간** — 바쁜 관리자가 30초 스캔으로 오늘 할 일을 파악할 수 있는가

### 독자 계층 분리 원칙
같은 보고서 안에서 C-Level 요약과 엔지니어 세부 내용을 분리:
- **one_line_summary**: C-Level / 매니저 — 3초 판단
- **quick_decision card**: 관리자 — 10초 상황 파악
- **detailed_analysis + concept boxes**: 엔지니어 — 기술적 맥락 이해
- **action_items**: 실무자 — 즉시 실행 가능한 절차

---

## Quality Scoring Model (100점 만점)

### Category 1: Content Accuracy (30점)

보고서에 포함된 정보가 정확하고 검증 가능한가.

| Criterion | Points | What it checks |
|-----------|--------|----------------|
| `relevance_classification` | 5 | relevance와 affected_resources 일치 여부 |
| `one_line_summary` | 5 | 30-80자, 구체적, 내부 프로세스 미노출 |
| `no_fabricated_urls` | 5 | 모든 URL이 실제 도구 결과에서 획득 |
| `relevance_evidence` | 5 | 실제 리소스명/수치 포함된 매칭 근거 |
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
| `additional_checks` | 3 | CSA 검토 권장 사항 포함 (선택) |

**구조 표준**:
```
1. Status Header (3초 스캔) → one_line_summary + urgency badge + three-axis badges (중요성/영향도/직무연관성)
2. Quick Decision Card → 영향 범위, 조치 필요 여부, 기한, 작업량
3. Relevance Evidence → "왜 이 업데이트가 선택됐는지" Resource Graph 매칭 근거
4. Detailed Analysis → 기술 맥락, concept boxes, 환경 연관성
5. Key Dates Timeline → retirement/feature_change 시 마일스톤 시각화
6. Impact Analysis → cost/security/performance/operational 차원
7. Affected Resources → 리소스 테이블 (구독/리소스그룹/사유)
8. Action Items → 단계별 절차, CLI, 기한, 미조치 위험
9. Reference Docs → Microsoft Learn 링크
10. Additional Checks → CSA 검토 필요 항목
11. Footer → 면책 고지, 생성 시각
```

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
- **관련성 근거 명시**: "AKS 클러스터 2개가 감지되어 이 업데이트가 포함되었습니다"
- **조치 문장은 동사로 시작**: "업그레이드를 고려할 수 있습니다" ❌ → "노드 풀을 1.31.x로 업그레이드합니다" ✅
- **수치는 항상 맥락과 함께**: "23개 알림" ❌ → "23개 알림 중 조치 필요: 5건" ✅
- **능동태 사용**: "정책 위반이 탐지되었습니다" ❌ → "7건의 정책 위반을 확인했습니다" ✅
- **불확실성은 솔직하게**: AI 분석 결과임을 표시, 확신 어려운 판단에는 "검토 권장" 표시

### Category 4: Actionability (15점)

보고서를 읽고 즉시 행동할 수 있는가.

| Criterion | Points | What it checks |
|-----------|--------|----------------|
| `action_items_presence` | 5 | retirement/feature_change → 필수 존재 |
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
- **색상으로 상태 표현**: Critical(빨강), High(주황), Medium(노랑), Low(초록) 일관 사용
- **상단에 Health Status 한 줄**: 독자가 3초 안에 오늘 상황이 좋은지 나쁜지 파악
- **리소스 목록 전체 표시**: 영향 리소스를 모두 보여줌 (truncation 없음)
- **CTA 링크 포함**: Microsoft 공식 문서, Azure Portal 경로 (검증된 것만)
- **이모지 금지**: 보고서 본문에는 이모지 미사용 (이메일 제목줄은 허용)

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

### Issue: opportunity 업데이트가 dead-end (조치 방치)
**증상**: `relevance=opportunity` + `should_notify=true` 인데 `action_items: []` (알림 가치가 있는데 다음 행동 없음)
**원인**: `new_feature` 템플릿이 "empty [] default, do NOT fabricate evaluate actions"로 과도하게 억제
**수정**: `base.py`에 "Opportunity must never be a dead-end" 규칙 — 정확히 **1개 scoped 평가 action**(실제 후보 리소스명 + go/no-go 기준, `deadline=""`). `categories.py` new_feature와 정합

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
일괄 분석(digest) 보고서에서는 **모든 업데이트를 분석**하고 중요도별로 분류:
```
총 RSS 업데이트: 23건
중요 (high): 3건 — 직접 영향, 즉시 조치 필요
보통 (medium): 8건 — 관련 있음, 검토 권장
참고 (low): 12건 — 직접 관련 없음, 참고용
```
요약 항목에서 중요도에 따라 색상 배지로 구분하며, 제목 클릭 시 하단 상세 분석으로 이동.

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
