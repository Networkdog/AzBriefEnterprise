# `kql-resource-graph`

[프로젝트 README](../../../README.md) > [skills](../README.md) > `kql-resource-graph`

Azure Resource Graph KQL로 **중첩 속성·배열·관계를 조사하고 문법·결과 오류를 재작성**할
때 사용하는 skill입니다. 상세 규칙과 장애 사례는 [`SKILL.md`](SKILL.md)에 있습니다.

## 코드 연결

| 경로 | 책임 |
|---|---|
| [`src/services/resource_graph.py`](../../../src/services/resource_graph.py) | 범위 강제, 제한된 페이지 수집, 선택적 baseline builder |
| [`src/agent/tools.py`](../../../src/agent/tools.py) | 의미 보존 전처리, 목적·필수 열 기반 재작성, 다중 표본 스키마 탐색 |
| [`src/agent/kql_knowledge.py`](../../../src/agent/kql_knowledge.py) | 성공 query와 schema 지식 재사용 |
| [`src/agent/prompts/tools.py`](../../../src/agent/prompts/tools.py) | planner에 전달하는 KQL 작성·완전성 규칙 |

## 사용 예시

조사할 조건과 필요한 결과 열을 명시합니다. 기존 builder 사용은 선택 사항입니다.

```python
from src.agent.tools import ResourceGraphQueryTool

result = await ResourceGraphQueryTool().ainvoke({
  "query": "Resources | where type =~ 'microsoft.storage/storageaccounts' "
       "| project id, subscriptionId, tls=tostring(properties.minimumTlsVersion) "
       "| order by id asc",
  "purpose": "Establish actual TLS settings, keeping unknown values explicit.",
  "expected_columns": ["id", "tls"],
})
```

KQL 정규화와 retry 회귀를 함께 실행합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\test_kql_sanitize.py tests\test_kql_retry.py tests\test_kql_knowledge.py -o "addopts=" -q
```

## 불변식

- type 비교는 `=~`를 사용하고 `subscriptionId`를 보존하며 결과를 안정적으로 정렬합니다.
- project 계산식, join/union, 배열·속성 백 mv-expand를 실제 지원 한도 안에서 사용합니다.
  let/render/datatable/toscalar는 작성하지 않습니다. `kind` 직접 투영 또는 다른 별칭을 씁니다.
- 넓은 raw bag 열거는 피하되 스키마 탐색을 위한 1~5개 표본은 허용합니다. 캐시는 참고 정보입니다.
- 문법 오류, 목적과 다른 결과, 필수 값 누락에는 근거 기반 재작성·재실행을 합니다. 정상적인
  0건은 억지로 늘리지 않고 false/0과 null을 구분합니다. 실패를 builder/count로 대체하지 않습니다.
- 주 조회 최대 8회, 결과 보정 최대 2회이며 반복·권한 거부·취소를 중단하고 미해결 사항을 남깁니다.
- 페이지는 최대 10개를 읽고 미수집 여부를 표시합니다. 수신한 상세 행은 모두 저장소에 전달하며,
  로컬 ref 검색으로 미수집 Azure 페이지까지 조회했다고 간주하지 않습니다.
- 적절한 ARG 테이블·속성을 먼저 조사하되 지원하지 않거나 가려진 ARM 속성은 미확인으로 남깁니다.
- 접근 가능한 모든 subscription이 query scope이며 하나의 subscription을 tenant 전체처럼 표현하지
  않습니다.
