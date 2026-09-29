# `src/archive`

[프로젝트 README](../../README.md) > [`src`](../README.md) > `archive`

Container Apps 제어면이 Microsoft Foundry Hosted Agent에서 돌려받은 공용 canonical
`AnalysisResult`를 불변 버전으로 보존하고, EasyAuth reader가 검색·상세 조회하도록 합니다.
Hosted Agent 내부의 `src/agent/history.py` JSONL은 bounded cross-update memory일 뿐 이 archive의
원장이 아닙니다.

Archive는 여러 reader가 공유하는 공용 원본이므로 중요성과 환경 영향도만 보존·표시·필터링합니다.
직무연관성은 subscriber 개인화 결과가 이메일로 전달될 때만 의미가 있으므로 Archive 문서,
metadata, query API와 화면에 포함하지 않습니다.
구독자 hierarchy scope로 추가 실행한 분석도 email-only variant입니다. 공용 canonical 원본의
불변성과 PII 격리를 위해 Archive에 저장하거나 scoped email에서 해당 원본으로 링크하지 않습니다.

상세의 `relevance_evidence`는 **환경 연관성 / Environment Relevance / 環境との関連性**으로
표시합니다. 변경·종료의 적용/조치 필요성 또는 신규 역량의 가치/도입 조건을 설명하는 필드이며
보고서를 선택한 이유가 아닙니다. 내용이 있으면 리소스 행이 없어도 섹션은 표시합니다. 제목 변경은
저장 schema나 과거 원문을 바꾸지 않으며, 분석 시점·조회 범위를 명시하는 새 생성 지침은 새 분석부터 적용됩니다.

선택 섹션은 실제 렌더링한 텍스트가 있을 때만 제목과 목차 항목을 만들고 빈 목록 항목도 생략합니다.
영향·기회의 `impact_details`가 빈 객체이거나 모든 차원이 공백이면 내용이 있는 `impact_summary`를
[공통 표시 정규화](../report_presentation.py)로 처리합니다. JSON 객체라면 알려진 비용·보안·성능·
운영 차원만 해석하고, 빈 값과 `N/A`는 생략하며 내부 JSON을 노출하지 않습니다. JSON이 아닌
실제 서술형 요약은 안전한 Markdown으로 표시합니다. 모두 비었으면 섹션을 만들지 않습니다.
개요 문단마다 붙은 단독 outline 번호를 제거하지만 실제 연속 목록·명시적 시작 번호·코드는 보존합니다.
정규화는 표시용이며 저장 원문을 수정하지 않습니다.

이메일과 같은 순서로 평문 한 줄 요약, 판정, 개요, 환경/리소스, 변경 영향, 조치·추가 확인을 표시합니다.
신규 역량의 활용 항목은 별도 제목 없이 개요에 포함합니다. 환경 설명과 리소스 목록은 하나의 섹션이며,
참고문서는 별도 하단 목록 대신 제목·설명·확인 내용·링크가 있는 본문 박스로 표시합니다. 명시적 링크나
충분한 문단 단어 일치가 있으면 그 문단 뒤에, 불명확하면 개요 끝에 둡니다. 용어 박스도 첫 용어 언급 뒤로
옮길 수 있을 때만 이동합니다. 실제 조치의 사유·예상 시간·검증 설명·안전한 참고 링크를 보존합니다.
CSS와 리소스 표현은 브라우저에 맞게 유지하며 이메일 전용 이미지·쿼리 링크·직무연관성은 가져오지 않습니다.

## 파일과 책임

| 파일 | 책임 |
|---|---|
| [`models.py`](models.py) | strict schema v1 문서, summary, query/page, source, receipt 계약 |
| [`service.py`](service.py) | 문서 생성, reverse timestamp ID, 저장소 호출, browser deep link |
| [`auth.py`](auth.py) | EasyAuth principal과 Admin/archive reader allow-list 인가 |
| [`router.py`](router.py) | `/archive`, `/api/archive/analyses` 목록·상세 route와 CSP/no-store |
| [`page.py`](page.py) | 공통 light 운영 shell을 사용하는 responsive 검색·상세 browser UI |
| [../report_presentation.py](../report_presentation.py) | 이메일과 공유하는 표시 전용 정규화; 원본 모델과 분리 |
| [`../web_design.py`](../web_design.py) | 공통 token·focus·responsive primitive와 Admin/Archive header/navigation·로컬 아이콘. Feedback은 별도 헤더 사용 |
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
- 기본 상세 GET은 기존 문서 그대로입니다. `?view=report`를 명시하면 같은 원문과 별도
	`presentation` 필드(정규화한 개요·영향·표시 정책)를 반환합니다. 브라우저는 이 값을 사용하며
	저장 계약이나 원문 응답을 표시 목적으로 덮어쓰지 않습니다.
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
- reader는 `ARCHIVE_ALLOWED_PRINCIPALS ∪ ADMIN_ALLOWED_PRINCIPALS ∪ managed Admin`입니다.
	빈 집합은 deny-all입니다.
- Blob URL, SAS, access token은 API에 반환하지 않습니다.
- 분석 Markdown은 heading, paragraph, list, blockquote, fenced/inline code, bold, link를 구조화
	DOM으로 렌더합니다. HTML은 실행하지 않고 `innerHTML`을 사용하지 않습니다.
- Header와 main content는 같은 제한 폭으로 중앙 정렬하며 mobile에서는 viewport 폭에 맞춥니다.
- 목록은 Admin과 같은 control shell을 유지하고 상세는 pure white canvas/teal accent의 편집 지면을 사용하며 Feedback과는 디자인
	token을 공유합니다. 검색과 서비스는
	기본 도구 모음에 두고 고급 필터는 `aria-expanded` toggle로 펼칩니다. 상세는 1120px 이내에서
	번호가 있는 sticky 목차와 본문을 나란히 배치합니다. 제목/section/body는 데스크톱 48/25/16px,
	모바일 32/21/14px이며 판정·리소스·액션은 반복 card 대신 얇은 선으로 나눕니다. 모바일에서는
	목차를 한 줄 가로 탐색으로 바꾸고 상세가 열리면 중복되는 목록 소개를 숨깁니다.
- Archive browser UI는 report language와 독립적으로 English를 기본값으로 사용합니다. 모든 filter는
	visible Optional 표식과 예시 placeholder를 제공하며 input/select와 command는 공통 40px 높이를
	사용합니다. 도구 모음 버튼은 내용에 맞는 폭을 사용하고 mobile action row는 세 칸을 균등 분배합니다.
	분석 제목은 새 탭으로도 열 수 있는 실제 링크이며 내용에 따라 줄바꿈합니다.
- URL filter는 새로고침·로그인·상세 복귀 시 유지하고 같은 페이지에서 복귀하면 불러온 행과 포커스도
	복원합니다. 목록/상세 요청은 별도 취소·순서 guard로 늦은 응답을 무시합니다.
- 건수는 불러온 기록 수이며 storage 전체 건수로 표시하지 않습니다. 빈 결과, 추가 scan 가능,
	로딩, 오류와 재시도 상태를 구분합니다. 날짜 범위 역전은 제출 전에 차단합니다.
- 상세의 링크 복사와 목차는 저장 문서를 변경하지 않습니다. 활성화된 Feedback 링크는 언어와
	`archive:<불변 ID>`만 전달하며 보고서 본문이나 구독자 정보를 URL에 넣지 않습니다.
- Page `h1`은 하나이며 결과와 상세 문서 제목은 `h2`입니다. Skip link와 모든 control의 visible
	keyboard focus를 유지하고, Admin link는 Admin UI가 활성화된 경우에만 표시합니다.
- Apple local font를 우선하고 고정 Pretendard WOFF2 한 개만 CSP에서 허용하며 실패 시 시스템
  폰트로 fallback합니다. 외부 stylesheet나 JavaScript는 로드하지 않습니다.
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

`python -m scripts.preview_web --port 8765`의 합성 Archive 페이지를 연 뒤 Playwright MCP에서
[archive_reports.cjs](../../tests/browser/archive_reports.cjs)를 `filename`으로 실행하면 빈 객체·공백·
기존 요약·JSON 차원·문단 번호·실제 목록/코드·설명 박스·안전한 조치 표시를 ko/en/ja와
1440/768/390/320px에서 검사합니다. 미리보기의 `/api/preview/report-presentation`은 합성
시나리오를 같은 Python 정규화에 통과시키는 테스트 전용 경로이며 운영 API에 추가하지 않습니다.
`passed=true`를 확인하고 화면은 `out/`에 별도로 캡처합니다. 실제 저장소나 이메일 전송은 사용하지 않습니다.
