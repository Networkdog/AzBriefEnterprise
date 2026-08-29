# `src/rss`

[프로젝트 README](../../README.md) > [`src`](../README.md) > `rss`

Microsoft Azure Update feed와 상세 API를 `AzureUpdate` domain model로 정규화합니다. live rolling
feed, local history archive, SafeLinks/tracking 제거가 이 경계에 모여 있습니다.

## 파일과 공개 API

[`parser.py`](parser.py)는 다음 기능을 제공합니다.

| API | 용도 |
|---|---|
| `AzureUpdateParser.get_updates()` | live RSS를 읽고 각 항목의 상세 설명/link를 보강 |
| `get_updates_by_date_range()` | live feed와 local history를 ID로 deduplicate해 기간 필터 |
| `parse_feed()` | 이미 받은 RSS XML을 순수 parsing; 단위 테스트에 적합 |
| `get_update_by_url()` / `fetch_update_by_id()` | 단건 update 조회 |
| `clean_url()` | 중첩 SafeLinks unwrap과 tracking query parameter 제거 |
| `AzureUpdate.to_dict()` | Hosted contract에 전달 가능한 직렬화 dict 생성 |

## 사용 예시

네트워크 없이 XML 문자열을 parsing할 수 있습니다.

```python
from src.rss import AzureUpdateParser

xml = """<rss><channel><item><title>Example</title><link>https://azure.microsoft.com/updates?id=1</link><guid>1</guid></item></channel></rss>"""
updates = AzureUpdateParser().parse_feed(xml)
assert updates[0].title == "Example"
```

live feed 목록은 CLI를 통해 확인합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; python -m scripts.test_local list -n 10
```

## 데이터 의미

- live RSS는 최신 약 200건의 rolling window이므로 오래된 월이 사라지는 것은 정상입니다.
- history archive는 `data/azure_updates_history.jsonl`이며 date-range 경로에서만 병합합니다.
- canonical ID로 live/history 중복을 제거하고 날짜는 timezone-aware UTC로 정규화합니다.
- 상세 API 실패는 RSS 항목 자체를 버리지 않고 짧은 description으로 degrade합니다.

## 불변식

- HTML 정리는 BeautifulSoup의 `html.parser`를 사용하고 `lxml` dependency를 추가하지 않습니다.
- SafeLinks를 재귀적으로 풀되 `view`, `tabs`, fragment 같은 functional URL 요소는 보존합니다.
- malformed RSS entry 하나가 전체 feed parsing을 중단시키지 않습니다.
- 외부 content는 untrusted이며 prompt instruction으로 승격하지 않습니다.

## 검증

```powershell
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\test_rss_parser.py tests\test_url_validation.py -o "addopts=" -q
```
