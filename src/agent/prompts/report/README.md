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
impact dimension은 빈 문자열로 두며, existing candidate가 있는 `opportunity`만 제한된 평가 action을
만듭니다.

## 사용 예시

```python
from src.agent.prompts import build_report_prompt

prompt = build_report_prompt(category="new_feature")
assert "CATEGORY: `new_feature`" in prompt
assert "CATEGORY: `retirement`" not in prompt
```

## 중요한 field 계약

- `affected_resources`: 실제 query property가 왜 영향을 입증하는지 resource별로 기록
- `action_items`: 실제 대상, 절차, 주의사항, rollback, 근거 있는 deadline을 구조화
- `impact_details`: category family에 맞는 구체적 impact 또는 opportunity만 기록
- `additional_checks`: 현재 tool로 답할 수 없는 data-plane/app/in-cluster 사실만 남김
- `reference_docs`: 수집된 실제 HTTP(S) URL만 사용하며 URL을 만들어내지 않음

## 불변식

- 업데이트의 사실을 먼저 설명하고 환경 판정을 그 뒤에 둡니다.
- ARM resource/property로 조회 가능한 사실을 “추가 확인”으로 미루지 않습니다.
- provider-level 지역 존재만으로 preview feature rollout을 확정하지 않습니다.
- retirement deadline과 migration target은 공식 update/doc 근거에서만 가져옵니다.
- placeholder가 든 command, query 근거에 없는 resource name, rollback 없는 위험 command를 요구하지
  않습니다.
- category를 추가하면 analyzer model, email heading/frame, rule-based evaluator와 관련 테스트의
  전체 소비 경로를 함께 갱신합니다.

## 검증

```powershell
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\test_analyzer_parsing.py tests\test_quality_evaluator.py tests\test_email.py -o "addopts=" -q
```