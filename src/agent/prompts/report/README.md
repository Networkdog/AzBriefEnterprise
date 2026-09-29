# `src/agent/prompts/report`

[프로젝트 README](../../../../README.md) > [`src/agent/prompts`](../README.md) > `report`

최종 `AnalysisResult`의 공통 schema/근거 규칙과 update category별 서술 frame을 분리합니다.
Reporter는 분류된 category 하나의 template만 받아 현재 변경에 맞는 질문에 집중합니다.

## 파일

| 파일 | 책임 |
|---|---|
| [`base.py`](base.py) | category 전후 공통 instruction, JSON field 계약, ordering, self-check |
| [`categories.py`](categories.py) | 8개 category의 tone, 필수/선택 field, action/impact 규칙 |
| [`__init__.py`](__init__.py) | report component package 표시 |

## Category family

| Family | Category | 보고서의 중심 질문 |
|---|---|---|
| Change | `retirement`, `feature_change`, `pricing` | 무엇이 바뀌고 어느 resource가 영향받으며 언제 무엇을 해야 하는가 |
| Capability | `new_feature`, `new_service`, `region_expansion`, `preview`, `sdk_tooling` | 이전에는 어떻게 해결했고 이제 어떤 기회와 adoption trade-off가 생겼는가 |

Capability 보고서에 기존 운영 “영향 없음”을 채우는 것은 정보가 아닙니다. 실제 gain이 없는
impact dimension은 빈 문자열로 둡니다. 새 서비스 미보유만으로 무관함을 단정하지 않고 확인된
업무·워크로드·요구에 맞는 가치와 도입 조건을 설명합니다. 알려진 대상과 go/no-go 기준이 있을 때만
최대 1개의 선택적 비변경 평가 action을 만듭니다. 변경·종료도 적용이 확인됐을 때만 조치를 요구하며
확인된 부재와 근거 부족을 구분합니다. SDK/API의 breaking change는 Change 계열로 분류하고,
가격 변경은 비용 증가/필수 변경과 선택적 절감을 구분합니다.

## 사용 예시

```python
from src.agent.prompts import build_report_prompt

prompt = build_report_prompt(category="new_feature")
assert "CATEGORY: `new_feature`" in prompt
assert "CATEGORY: `retirement`" not in prompt
```

## 중요한 field 계약

- `one_line_summary`: 원본 영문 제목과 분리된 선택 언어의 공지 전용 한 문장. 영향 범위·리소스 수·
  작업량·역할별 권고·주사용 리전의 적용 판정은 넣지 않습니다. 공지 자체의 날짜·리전 확장·버전·가격은
  허용합니다. `ANNOUNCEMENT_SUMMARY_PROMPT`와 `_summarize_announcement()`가 원문 제목·본문만
  별도 호출에 전달하고, 핵심 동작·대상과 중요한 조건을 요약합니다. 발췌 일치·출력 완결성·ko/ja 문자
  존재 여부를 검사하며 의미 전체의 정확성을 자동 판정하지는 않습니다. 30~80자 제한 때문에 핵심을
  버리지 않도록 길이 지시는 간결한 한 문장으로 바꾸고 기계 평가의 길이 감점은 240자 초과로 둡니다.
  구독자 맞춤화도 먼저 해당 언어의 원문 요약을 생성한 뒤 역할별 편집과 분리하고 제외 항목에도
  적용합니다. 품질 재작성의 재평가 전에도 원문 요약을 복원하므로 평가와 최종 결과가 일치합니다.
  발췌는 일시 검증용이며 `AnalysisResult`나 Archive 스키마에 새 필드를 추가하지 않습니다.
  런타임 리전 보정과 평가기는 리전 결과를 `detailed_analysis` 또는 `relevance_evidence`에서 확인하며
  요약에 리전 접두어·리소스 수·조치 패턴을 강제하지 않습니다.
- `relevance_evidence`: 분석 시점·범위의 환경 연관성. 변경에는 적용/조치 필요성, 신규 역량에는
  확인된 가치/도입 조건을 기록하며 리소스 이름·개수는 해당할 때만 요구합니다. 실제 계획을 지어내지
  않고 SDK 사용·간접 의존성 등 중요한 근거가 없으면 `unknown`으로 판단 한계를 보존합니다.
- `affected_resources`: 실제 query property가 왜 영향을 입증하는지 resource별로 기록
- `resource_queries`: 대량의 전체 조회 결과가 적용될 때만 실행 카탈로그의 `reference`/`reason`을
  선택합니다. 런타임이 전체 식별 목록과 고유 건수를 복원하므로 이름·KQL·URL을 다시 만들지 않습니다.
  일부만 적용되는 목록은 참조로 확대하지 않으며 부분 조회와 그룹 간 중복을 명시합니다.
  구독자 맞춤화는 사유만 번역하고 참조·수량·식별 정보는 유지합니다.
- 파서는 `영향_리소스`와 `영향받는_리소스`를 호환하되 `affected_resources`가 있으면 빈 배열도
  그대로 우선합니다. 이 별칭 지원은 작성자에게 표준 필드 대신 번역된 키를 요구하는 규칙이 아닙니다.
- `action_items`: 실제 대상, 절차, 주의사항, rollback, 근거 있는 deadline을 구조화
- `impact_details`: category family에 맞는 구체적 impact 또는 opportunity만 기록
- `impact_summary.cost_impact` (생성 JSON): 비용 관련 업데이트의 실제 지출 기준선은 조회
  범위·기간·통화와 함께 기록하고, 예상 증감은 단가·사용량 근거와 가정을 별도로 제시합니다.
  빈 데이터나 조회 실패는 0원으로 쓰지 않으며, 관련 없는 전체 비용에 절감률을 곱하지 않습니다.
- `additional_checks`: 현재 tool로 답할 수 없는 data-plane/app/in-cluster 사실만 남김
- `reference_docs`: 수집된 실제 HTTP(S) URL만 사용하며 URL을 만들어내지 않음
  이메일은 제목·설명·확인 내용으로 본문 설명 박스를 만들고 관련 문단에 가깝게 배치합니다.
  이를 위해 새 필드나 Markdown 문법을 만들지 않으며 원본·불변 Archive는 그대로 유지합니다.

## 불변식

- 업데이트의 사실을 먼저 설명하고 환경 판정을 그 뒤에 둡니다.
- ARM resource/property로 조회 가능한 사실을 “추가 확인”으로 미루지 않습니다.
- Resource Graph의 리소스 수로 주사용 Region을 정하고, provider/resource-type 지역 존재만으로
  GA 또는 Preview feature rollout을 확정하지 않습니다. Azure Update 상세 원문이나 가져온
  Microsoft Learn 기능 본문에서 Region별 결과를 확인합니다.
- retirement deadline과 migration target은 공식 update/doc 근거에서만 가져옵니다.
- placeholder가 든 command, query 근거에 없는 resource name, rollback 없는 위험 command를 요구하지
  않습니다.
- category를 추가하면 analyzer model, email heading/frame, rule-based evaluator와 관련 테스트의
  전체 소비 경로를 함께 갱신합니다.

## 검증

```powershell
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\test_analyzer_parsing.py tests\test_quality_evaluator.py tests\test_email.py -o "addopts=" -q
```