# `src/services`

[프로젝트 README](../../README.md) > [`src`](../README.md) > `services`

Azure와 공개 문서 source에서 **원시 근거를 읽는 data-access 계층**입니다. 관련성, 영향도,
action 우선순위 같은 business decision은 `src/agent`가 소유합니다.

## Service 지도

| 파일 | 책임 |
|---|---|
| [`resource_graph.py`](resource_graph.py) | accessible subscription 전체의 Resource Graph query와 KQL builder |
| [`azure_rest.py`](azure_rest.py) | ARM list pagination과 single-object metadata endpoint 호출 |
| [`cost_management.py`](cost_management.py) | subscription cost 집계 |
| [`log_analytics.py`](log_analytics.py) | workspace KQL query와 오류/activity 요약 |
| [`microsoft_learn.py`](microsoft_learn.py) | Learn 검색, allow-listed page fetch, command block 추출 |
| [`community_insights.py`](community_insights.py) | Azure Weekly의 topic-matched practitioner caveat cache |
| [`checkpoint.py`](checkpoint.py) | inert/file/blob watermark store와 forward-only conditional write |
| [`__init__.py`](__init__.py) | enabled subscription discovery와 process cache |

## 사용 예시

네트워크를 호출하지 않고 검증된 KQL builder를 사용할 수 있습니다.

```python
from src.services.resource_graph import ResourceGraphQueryBuilder

query = ResourceGraphQueryBuilder.get_query_for_update_service("Storage")
assert "microsoft.storage/storageaccounts" in query.lower()
```

Microsoft Learn service를 직접 사용할 때는 async client를 닫습니다.

```python
from src.services.microsoft_learn import MicrosoftLearnService

service = MicrosoftLearnService()
try:
    result = await service.search_azure_docs("Storage account minimum TLS version")
finally:
    await service.close()
```

## 호출 계약

각 service의 반환 모양과 cleanup API는 현재 서로 다릅니다. 예를 들어 Learn search는
`query/count/results`, ARM list는 `count/value`, 오류는 일부 service에서 `error` key로 표현합니다.
공통 `success/data/error` 계약이라고 추측하지 말고 해당 method와 Agent tool adapter를 함께
확인합니다.

`AzureRestClient.call_api()`는 `value` array와 `nextLink`가 있는 list endpoint용이고,
`get_resource()`는 provider metadata처럼 JSON object 하나를 반환하는 endpoint용입니다.

## 불변식

- Azure credential과 SDK/HTTP client는 lazy 생성합니다.
- tenant-wide 질문은 enabled accessible subscription을 모두 고려하고 subscription ID/name 근거를
  보존합니다.
- 서비스 실패를 리소스 부재로 바꾸지 않고 오류 또는 낮은 confidence로 Agent에 전달합니다.
- page fetch는 allow-list와 HTTP(S) scheme을 검사해 SSRF를 막습니다.
- checkpoint blob은 HTTPS와 Entra token만 사용하고 ETag로 뒤로 쓰기/동시 writer를 방지합니다.
- 서비스에서 report wording이나 category를 결정하지 않습니다.
- 새 dependency는 `requirements.txt`와 `pyproject.toml`에 함께 추가합니다.

## 검증

```powershell
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\test_services.py tests\test_subscription_discovery.py tests\test_azure_rest.py tests\test_checkpoint.py tests\test_community_insights.py -o "addopts=" -q
```
