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
| [`billing.py`](billing.py) | tenant-scope Microsoft.Billing account/profile 조회 (`2024-04-01`) |
| [`log_analytics.py`](log_analytics.py) | workspace KQL query와 오류/activity 요약 |
| [`microsoft_learn.py`](microsoft_learn.py) | Learn 검색, allow-listed page fetch, command block 추출 |
| [`community_insights.py`](community_insights.py) | Azure Weekly의 topic-matched practitioner caveat cache |
| [`checkpoint.py`](checkpoint.py) | inert/file/blob watermark store와 forward-only conditional write |

`log_analytics.py`는 query 전용입니다. 실행 중 오류와 failure 상태의 write 경로는 service/tool이
아니라 `src/logging_config.py`의 중앙 logging handler가 담당합니다. 이 handler는 redacted
실패 이벤트만 Direct DCR을 통해 같은 workspace의 `AzBriefFailures_CL`에 적재하며, exporter
오류가 원래 실행 결과를 바꾸거나 재귀 log 전송을 만들지 않게 합니다.
`get_failure_events()`는 Admin의 기간·Run ID 필터에 고정 table/projection을 적용합니다.
정확한 Run ID에 연결된 Trace ID만 추가 조회하며 최신순 limit+1로 추가 기록 여부를 판별합니다.
SDK `LogsTable.columns`는 문자열 목록이고, 부분 query 결과는 성공으로 취급하지 않습니다.
화면용 마스킹·필드 허용 목록은 [`admin/errors.py`](../admin/errors.py)가 담당합니다.
| [`archive.py`](archive.py) | inert/file/blob canonical analysis store, create-only write, metadata cursor listing |
| [`runtime_inventory.py`](runtime_inventory.py) | Admin readiness용 ARM resource와 Foundry Agent 최신 version safe projection |
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

Learn의 `fetch_documentation_page()`는 `success/data/error`와 함께 잘리지 않은 본문,
요청·최종 URL, 절·본문 링크·코드·이미지를 반환합니다. HTTPS·허용 호스트를 리디렉션마다
요청 전에 검사하고, 단축 URL은 리디렉션에만 사용합니다. 비 HTML·과대 응답은 실패로
반환하고 링크 추출 제한을 명시합니다. 기술적 경고를 UI 요소로 제거하지 않습니다.
Learn의 제목과 본문이 여러 `div.content`로 나뉘어도 전체 영역을 중복 없이 읽으며,
제목만 있는 응답은 문서 본문을 읽은 성공으로 처리하지 않습니다.
기존 `fetch_page_content()`는 미리보기 호환 API이며 재귀 조사에는 전체 본문 API를 씁니다.
링크 우선순위·깊이·공유 예산·근거 ref 관리는 서비스가 아니라 Agent 계층의 책임입니다.

`AzureRestClient.call_api()`는 `value` array와 `nextLink`가 있는 list endpoint용이고,
`get_resource()`는 provider metadata처럼 JSON object 하나를 반환하는 endpoint용입니다. 경로에
`{subscriptionId}`가 있을 때만 subscription을 요구하며, Microsoft.Billing 같은 tenant-scope
경로는 현재 identity가 직접 접근할 수 있는 범위만 반환합니다. 403이나 빈 접근 범위를 tenant에
billing account가 없다는 뜻으로 바꾸지 않습니다.

`CostManagementService`는 명시·설정된 구독 또는 유일하게 발견한 구독에서 `ActualCost`를
조회합니다. `resource_type`/`service_name` 필터를 서버에 전달하고 기간·통화·범위·필터와
`has_cost_data`를 반환합니다. 전체 행을 합산·정렬한 뒤 표시 개수를 제한하며 여러 통화나 추가
페이지가 있으면 확정 합계를 내지 않습니다. 비용 도구는 실패를 예외로 전달합니다. 범위 제한
구독자 분석은 전체 범위를 강제할 수 없으므로 차단하며, 빈 데이터는 비용 0의 근거가 아닙니다.

## 불변식

- Azure credential과 SDK/HTTP client는 lazy 생성하며, 병렬 readiness 조회에서도 shared credential은
  lock으로 한 번만 초기화합니다.
- tenant-wide 질문은 enabled accessible subscription을 모두 고려하고 subscription ID/name 근거를
  보존합니다.
- 서비스 실패를 리소스 부재로 바꾸지 않고 오류 또는 낮은 confidence로 Agent에 전달합니다.
- runtime inventory는 여러 ARM resource를 병렬 조회하되 각 결과를 독립된 `success/data/error`
  envelope로 반환하고, Foundry Agent는 이름/version/definition kind/status만 투영합니다. 최종
  green/red 판정은 Admin 계층이 exact kind와 `ACTIVE` 상태를 함께 확인합니다.
- page fetch는 HTTPS·allow-list를 모든 리디렉션 전에 검사해 SSRF를 막습니다.
- checkpoint blob은 HTTPS와 Entra token만 사용하고 ETag로 뒤로 쓰기/동시 writer를 방지합니다.
- archive blob은 HTTPS/Entra와 `If-None-Match: *`를 사용하고, payload와 search metadata를 한 PUT에
  commit합니다. business document 생성과 reader policy는 `src/archive`가 소유합니다.
- 서비스에서 report wording이나 category를 결정하지 않습니다.
- 새 dependency는 `requirements.txt`와 `pyproject.toml`에 함께 추가합니다.

## 검증

```powershell
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\test_services.py tests\test_billing.py tests\test_subscription_discovery.py tests\test_azure_rest.py tests\test_checkpoint.py tests\test_archive_store.py tests\test_community_insights.py -o "addopts=" -q
```
