# `kql-resource-graph`

[프로젝트 README](../../../README.md) > [skills](../README.md) > `kql-resource-graph`

Azure Resource Graph의 제한된 KQL dialect로 **tenant 근거를 조회하고 실패·빈 결과를 복구**할
때 사용하는 skill입니다. 상세 규칙과 장애 사례는 [`SKILL.md`](SKILL.md)에 있습니다.

## 코드 연결

| 경로 | 책임 |
|---|---|
| [`src/services/resource_graph.py`](../../../src/services/resource_graph.py) | tenant-wide 실행과 `ResourceGraphQueryBuilder` |
| [`src/agent/tools.py`](../../../src/agent/tools.py) | sanitize, retry, builder fallback, semantic empty-result repair |
| [`src/agent/kql_knowledge.py`](../../../src/agent/kql_knowledge.py) | 성공 query와 schema 지식 재사용 |
| [`src/agent/prompts/tools.py`](../../../src/agent/prompts/tools.py) | planner에 전달하는 KQL 작성·완전성 규칙 |

## 사용 예시

기존 builder의 검증된 storage query를 재사용합니다.

```python
from src.services.resource_graph import ResourceGraphQueryBuilder

query = ResourceGraphQueryBuilder.get_storage_accounts_detail()
```

KQL 정규화와 retry 회귀를 함께 실행합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\test_kql_sanitize.py tests\test_kql_retry.py tests\test_kql_knowledge.py -o "addopts=" -q
```

## 불변식

- type 비교는 `=~`를 사용하고 `subscriptionId`를 보존하며 결과를 안정적으로 정렬합니다.
- `join`, `let`, `render`, `datatable`, `toscalar()`에 의존하지 않습니다.
- 넓은 query에서 raw `properties`, `tags`, `sku` bag 전체를 project하지 않습니다.
- property filter 결과가 비었다고 리소스 부재로 단정하지 않고 type-only probe로 구분합니다.
- 알려진 resource type은 generic dump보다 builder fallback을 우선합니다.
- ARM resource/property로 답할 수 있는 사실을 수동 점검으로 미루지 않습니다.
- 접근 가능한 모든 subscription이 query scope이며 하나의 subscription을 tenant 전체처럼 표현하지
  않습니다.
