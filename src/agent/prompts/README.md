# `src/agent/prompts`

[프로젝트 README](../../../README.md) > [`src/agent`](../README.md) > `prompts`

Agent phase마다 필요한 instruction만 조립해 Prompt Agent에 전달합니다. 하나의 거대한 prompt를
모든 호출에 재사용하지 않으므로 context 비용과 서로 충돌하는 지침의 노출 범위를 줄입니다.

## Module 책임

| 파일/디렉터리 | 내용 | 주입 phase |
|---|---|---|
| [`core.py`](core.py) | 정체성, mission, evidence/accuracy 원칙 | 모든 phase |
| [`analysis.py`](analysis.py) | 중요성·영향도·직무연관성 축과 품질 기준 | plan, evaluate, report |
| [`tools.py`](tools.py) | tool 설명, KQL 작성과 ARM 완전성 규칙 | plan, execute |
| [`workflow.py`](workflow.py) | 짧은 전체 workflow 안내 | plan |
| [`phases.py`](phases.py) | plan/evaluate/revise/execute용 task prompt | 해당 node |
| [`writing.py`](writing.py) | 근거 중심 보고서 문장과 구조 | report |
| [`subscriber.py`](subscriber.py) | 역할·언어별 customization | customization |
| [`languages/`](languages/README.md) | 요청 언어 하나의 style guide/translation notes | report/customization |
| [`report/`](report/README.md) | JSON schema, 공통 규칙, category 하나의 template | report |
| [`__init__.py`](__init__.py) | 공개 builder와 호환용 full constants | 조립 경계 |

## Phase matrix

| Section | Planning | Execution | Evaluation | Report |
|---|:---:|:---:|:---:|:---:|
| Core | O | O | O | O |
| Analysis | O |  | O | O |
| Tools | O | O |  |  |
| Writing |  |  |  | O |
| Requested language |  |  |  | O |
| Workflow overview | O |  |  |  |

## 사용 예시

```python
from src.agent.prompts import build_report_prompt, build_system_prompt

planning_system = build_system_prompt(phase="planning")
report_system = build_system_prompt(phase="report", language="ko-KR")
retirement_prompt = build_report_prompt(category="retirement")
```

`build_report_prompt()`에 format 값을 전달하면 `update_context`, `resource_summary`,
`task_results_summary`, `report_language` 등 template placeholder를 모두 제공해야 합니다. category가
알려져 있을 때 빈 값으로 호출하면 모든 category template가 들어가므로 production 경로에서는
분류된 category를 전달합니다.

## 불변식

- 새 호출 코드는 호환용 `SYSTEM_PROMPT`/`REPORT_PROMPT`보다 dynamic builder를 사용합니다.
- Prompt는 영어로 작성해 token을 절약하되 최종 user-facing 결과 언어는 language guide가
  결정합니다.
- category-specific 지침은 한 category만 주입하고 언어 guide도 요청 언어 하나만 주입합니다.
- Python `.format()`을 통과하는 literal brace는 두 번 escape합니다.
- 규칙을 계속 덧붙이지 않고 기존 원칙을 일반화·압축해 prompt dilution을 제한합니다.
- 외부 tool 결과를 system instruction처럼 취급하지 않습니다.
- KQL은 고정 builder가 아니라 조사 목적과 필수 속성에서 출발합니다. 지원되는 계산식·조인·배열
  탐색을 허용하고 스키마 결과를 받은 뒤 후속 질의를 작성합니다. `purpose`/`expected_columns`,
  `query_intent`와 명시적 gap으로 실행 성공과 정보 확보를 구분합니다. 정상 0건이나 false/0을
  오류로 바꾸지 않으며, 범위·적용 조건을 유지합니다. 변경된 지침은 새 Prompt Agent 버전이 필요합니다.
- 공통 analysis 지침은 CSA 브리핑을 기능 설명이 아닌 의사결정 메모로 정의합니다. 판단축,
  조건부 권고와 반대 조건, 근거 있는 손익, 운영 책임, 결정을 닫는 증거를 plan/evaluate/report가
  함께 사용하며 Microsoft 내부 영업 절차나 만들어 낸 고객 계획은 포함하지 않습니다.
- Subscriber customization은 역할별 우선순위를 바꿀 수 있지만 이 의사결정 요소를 삭제하거나
  일반적인 역할 조언으로 바꾸지 않습니다.

## 알려진 정합성 점검

보고서 본문은 구체적으로 바뀐 대상을 먼저 명명한 뒤 환경 판정과 의사결정으로 이동합니다.
`writing.py`, 공통 report ordering, 언어 guide 어느 곳도 환경 부재 판정을 첫 문장으로 허용하지
않아야 합니다.

## 검증

```powershell
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\test_analyzer.py tests\test_analyzer_parsing.py tests\test_i18n.py tests\test_quality_evaluator.py -o "addopts=" -q
```
