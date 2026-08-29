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

## 알려진 정합성 점검

Prompt를 수정할 때 환경 부재 판정으로 보고서 본문을 시작해도 되는지에 관한 예시를 반드시
교차 검사합니다. 공통 report ordering과 한국어 guide는 업데이트 사실을 먼저 설명하도록
요구하므로, `writing.py`의 예시가 이 순서를 다시 허용하지 않는지 집중 검토가 필요합니다.

## 검증

```powershell
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\test_analyzer.py tests\test_analyzer_parsing.py tests\test_i18n.py tests\test_quality_evaluator.py -o "addopts=" -q
```
