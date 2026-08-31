# `src/archive`

[프로젝트 README](../../README.md) > [`src`](../README.md) > `archive`

Container Apps 제어면이 Microsoft Foundry Hosted Agent에서 돌려받은 공용 canonical
`AnalysisResult`를 불변 버전으로 보존하고, EasyAuth reader가 검색·상세 조회하도록 합니다.
Hosted Agent 내부의 `src/agent/history.py` JSONL은 bounded cross-update memory일 뿐 이 archive의
원장이 아닙니다.

Archive는 여러 reader가 공유하는 공용 원본이므로 중요성과 환경 영향도만 보존·표시·필터링합니다.
직무연관성은 subscriber 개인화 결과가 이메일로 전달될 때만 의미가 있으므로 Archive 문서,
metadata, query API와 화면에 포함하지 않습니다.

## 파일과 책임

| 파일 | 책임 |
|---|---|
| [`models.py`](models.py) | strict schema v1 문서, summary, query/page, source, receipt 계약 |
| [`service.py`](service.py) | 문서 생성, reverse timestamp ID, 저장소 호출, browser deep link |
| [`auth.py`](auth.py) | EasyAuth principal과 Admin/archive reader allow-list 인가 |
| [`router.py`](router.py) | `/archive`, `/api/archive/analyses` 목록·상세 route와 CSP/no-store |
| [`page.py`](page.py) | 외부 asset 없는 responsive 검색·상세 browser shell |
| [`../services/archive.py`](../services/archive.py) | inert/File/Blob data-access backend와 metadata projection |

## 저장 계약

- 하나의 분석 버전은 `entries/{reverse_epoch_ms}-{uuid}.json` Block Blob 하나입니다.
- `If-None-Match: *` create-only PUT으로 기존 버전을 덮어쓰지 않습니다.
- 같은 PUT의 `x-ms-meta-*`가 목록 projection을 보관하므로 mutable catalog가 없습니다. Metadata
	limit 때문에 projection이 잘린 드문 문서는 목록 검색 시 full document를 읽어 복원합니다.
- timeout 뒤 동일 ID/동일 bytes가 발견되면 멱등 성공, 다른 bytes면 conflict입니다.
- 상세 GET은 metadata의 SHA-256과 strict Pydantic schema를 모두 검증합니다. Wrapper뿐 아니라
	Update, AnalysisResult, impact, resource, action, reference nested 모델까지 `extra="forbid"`인 v1
	계약이므로 새 runtime 필드는 schema version 결정 없이 조용히 섞이지 않습니다.
- 저장 문서에는 subscriber, recipient, principal, 이메일 주소를 넣지 않습니다. 합성 evaluator는
	금지 key와 free-text의 email-like 값을 모두 검사합니다.

## 처리 순서

예약/Admin orchestration에서는 다음 순서가 불변식입니다.

```text
Hosted Agent result -> archive commit -> digest customization/email -> checkpoint advance
```

Archive backend가 구성됐는데 저장이 실패하면 run은 `failed`가 되고 이메일과 checkpoint는 진행하지
않습니다. 그렇지 않으면 checkpoint가 이미 지난 분석을 Archive에서 찾을 수 없는 상태가 생깁니다.
단건 REST, batch, AzBrief MCP도 결과를 반환하거나 이메일을 예약하기 전에 같은 ArchiveService를
호출합니다. Archive가 미구성인 local profile에서는 명시적인 no-op receipt로 기존 동작을 유지합니다.

## 인증과 출력 안전

- `ARCHIVE_UI_ENABLED=false`이면 page와 API 모두 404입니다.
- browser는 로그인하지 않았으면 `/.auth/login/aad`로 이동하고 JSON API는 401을 반환합니다.
- reader는 `ARCHIVE_ALLOWED_PRINCIPALS ∪ ADMIN_ALLOWED_PRINCIPALS`입니다. 빈 집합은 deny-all입니다.
- Blob URL, SAS, access token은 API에 반환하지 않습니다.
- 분석 text는 `textContent`와 구조화 DOM으로만 렌더합니다. HTML/Markdown을 실행하지 않습니다.
- 외부 링크는 허용된 HTTPS Microsoft/GitHub/Azure Weekly domain만 anchor로 만듭니다.
- Storage bearer token은 검증된 Azure cloud의 Blob container endpoint에만 전송합니다.

## 검증

```powershell
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\test_archive.py tests\test_archive_store.py tests\test_archive_evaluation.py -o "addopts=" -q
& .\.venv\Scripts\Activate.ps1; python -m scripts.evaluate_archive --records 10000
```

브라우저 검증은 1440×900과 390×844에서 목록, filter, 상세 deep link, keyboard focus, CSP console,
horizontal overflow를 확인합니다. 합성 fixture를 만들 때 PowerShell here-string으로 한국어 source를
pipe하면 cp949로 손상될 수 있으므로 UTF-8 파일 또는 ASCII fixture를 사용합니다.
