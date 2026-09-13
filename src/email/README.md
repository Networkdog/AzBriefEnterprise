# `src/email`

[프로젝트 README](../../README.md) > [`src`](../README.md) > `email`

`AnalysisResult`를 이메일 client에서 읽을 수 있는 **HTML과 plain text로 결정론적으로 렌더링**하고
Azure Communication Services로 전달합니다. 분석 판단은 이 계층에서 바꾸지 않습니다.

## 파일

| 파일 | 책임 |
|---|---|
| [templates.py](templates.py) | 제한된 Markdown 변환, 공통 문서·section formatter, `EMAIL_COLORS` / `FONT_SIZE_PX`, responsive HTML |
| [`service.py`](service.py) | 단건/digest 조립, subscriber 전송, feedback 알림, ACS client, console fallback |
| [`__init__.py`](__init__.py) | package 표시 |

## 렌더링 흐름

```text
AnalysisResult + AzureUpdate
  -> section formatters
  -> HTML_EMAIL_TEMPLATE / HTML_DIGEST_TEMPLATE (shared document shell)
  -> independent plain-text body
  -> ACS managed identity or connection string
  -> console fallback when transport is unconfigured
```

두 template은 `_EMAIL_DOCUMENT_START` / `_EMAIL_DOCUMENT_END`를 공유합니다.
`format_email_masthead_html()`, `format_report_header_html()`, `format_email_section_html()`,
`format_email_footer_html()`, `format_digest_intro_html()`이 지면의 공통 위계를 구성합니다.
`MicrosoftLearnService`가 공식 Learn 본문에서 설명 있는 PNG/JPEG/GIF 후보를 추출하면
`AnalysisResult.visual_assets`가 이를 전달 전용으로 보존합니다. `format_visual_assets_html()`은
HTTPS와 별도 Microsoft 호스트 allow-list, 확장자, 비어 있지 않은 `alt`를 다시 검사한 뒤 이미지,
캡션, 원문 링크를 전체 너비로 표시합니다. 단건은 최대 2개, digest는 업데이트당 1개·전체 4개로
제한합니다. 이미지가 없거나 client가 원격 이미지를 차단해도 본문은 달라지지 않으며 plain text에는
캡션과 원문 URL이 남습니다. 이 필드는 Archive v1 투영에서 제외해 불변 스키마를 바꾸지 않습니다.

`markdown_to_html()`은 heading, list, blockquote concept box, pipe table, bold, inline code와 안전한
Markdown link만 지원합니다. 전체 Markdown/HTML engine이 아니며 LLM output을 그대로 신뢰하지
않습니다.

Hierarchy scope가 있는 구독자는 canonical 결과를 후처리로 자르지 않습니다. `service.py`가
Management Group/Subscription/Resource Group을 담은 scoped Hosted 분석을 먼저 실행하고 그 결과만
맞춤화·전달합니다. Scope 분석 실패 시 unscoped 원본을 보내지 않으며 scoped email에는 범위가 다른
canonical Archive 링크를 넣지 않습니다.

단건과 digest의 HTML/plain-text footer는 `FEEDBACK_BASE_URL`에서 만든 현지화된 `/feedback`
링크를 함께 표시합니다. URL에는 Update ID 또는 digest 기간만 넣고 보고서 본문·tenant 근거·연락처는
넣지 않습니다. 피드백 자체는 먼저 private state에 저장되며 `send_feedback_notification()`이 이후
명시적으로 설정한 `FEEDBACK_RECIPIENT_ADDRESS`로 내용을 보냅니다. 수신자가 비어 있으면 저장은
유지하고 notification만 fail-closed로 건너뜁니다.

## 사용 예시

```python
from src.email.templates import markdown_to_html

html = markdown_to_html(
    "> **Private Link**: VNet 경로로 Azure PaaS에 연결합니다.\n\n"
    "- Public endpoint 노출을 줄입니다."
)
```

실제 `AnalysisResult` fixture를 통한 단건·digest 렌더링은 테스트에서 확인합니다.

`relevance_evidence`는 단건 및 digest 상세에 공통 **환경 연관성** 제목으로 표시하며 plain text도
같은 현지화 label을 사용합니다. 적용/가치 근거와 불확실성은 수정 없이 전달하고 리소스가 없다는
이유로 숨기지 않습니다. 구독자 맞춤화도 역할·관심 서비스가 있으면 리소스 수와 독립적으로
직무연관성을 평가하며, 입력 JSON에 원본 `relevance_evidence`를 포함합니다.

## 오프라인 미리보기와 검증

### 대규모 리소스 요약

`format_affected_resources_html()`과 `format_affected_resources_text()`는 선택적 keyword 인자
`resource_queries`와 신뢰된 `archive_url`을 받습니다. 단건과 digest 모두 같은 증거 집계 함수를
사용하며, `format_resource_count_text()`는 digest 목차와 동일한 건수 의미를 제공합니다.

- **20개 이하**는 기존 이름·구독·리소스 그룹·종류의 전체 표를 유지합니다. **20개 초과**는
  개별 이름 행 대신 총수와 사유별 건수를 평면 목록으로 표시합니다. 사유는 최대 **10개**이며,
  나머지 사유 개수를 명시하고 Archive의 분석 당시 목록으로 안내합니다. 원본 identity 목록을
  잘라내거나 변경하지 않습니다.
- 총수는 실제 행의 정규화된 **ARM ID**로 중복 제거합니다. 구독이 다른 동명 리소스를 합치지
  않으며, ID 미확인 기록은 별도 건수로 표시합니다. 겹치는 조회는 사유별 건수의 합이 총수와
  다를 수 있으므로 합산할 수 없다는 안내를 남깁니다.
- `ResourceQueryEvidence`의 `reference`와 행의 `query_refs`, 실제 고유 ID 건수와 `count`,
  ID에 포함된 구독·리소스 그룹과 분석 범위를 대조합니다. 검증되고 완전한 조회만
  `portal_url()`과 `safe_email_href()`를 거쳐 Azure Resource Graph Explorer 링크가 됩니다.
  집계 쿼리의 숫자나 모델이 주장한 총수로 건수를 대체하지 않고, 렌더러가 KQL을 만들지 않습니다.
- 검증된 그룹에는 분석 시점을 표시합니다. Portal에서는 **열람자의 RBAC 권한으로 현재 상태**를
  조회하므로 일치하는 디렉터리와 범위를 선택해야 합니다. 링크는 쿼리를 열 뿐 실행하지 않습니다.
  Management Group, 재현할 수 없는 join, 길이 초과 등의 이유로 링크가 없으면 신뢰된 HTTPS
  Archive 링크를 사용합니다. Archive도 없거나 URL이 거부되면 세부 목록을 열 수 없음을 명시합니다.
- 부분 증거나 메타데이터 없는 과거 결과는 전체 목록으로 확정하지 않습니다. 확인된 ID 건수는
  하한이고 ID 없는 데이터는 기록 수일 뿐입니다. 확인된 행이 0개여도 불완전한 결과는
  **전체 규모 미확인**으로 표시합니다. 동일한 규칙이 운영 요약과 단건·digest plain text에도 적용됩니다.

조회 메타데이터는 전달 전용이며 Archive v1에는 포함하지 않습니다. 분석 당시의 전체 identity
목록은 보존됩니다. 런타임의 증거 수집·검증과 Archive 직렬화는 이 렌더러의 책임이 아닙니다.
모든 사유 텍스트를 HTML escape하며 새 이미지·웹폰트나 프런트엔드 의존성을 추가하지 않습니다.

### 합성 데이터 옵션

[preview_email.py](../../scripts/preview_email.py)는 고객 데이터가 아닌 **SYNTHETIC 합성 데이터**로
ko/en/ja 단건·digest의 전체 스타일 및 inline-only HTML 12개를 저장합니다. 전송 설정과 종료
이력을 mock하고 전송 client를 만들지 않으며 Azure 호출이나 이메일 발송도 하지 않습니다.
Inline-only 버전은 `<style>` 블록을 제거해 fallback을 점검합니다. 합성 HTML은 공개 Learn
스크린샷 URL 하나를 포함하므로 브라우저 시각 검증 시에는 해당 정적 이미지를 요청합니다.

```powershell
& .\.venv\Scripts\Activate.ps1
python -m scripts.preview_email --output-dir out/email-editorial-preview --language all
python -m scripts.preview_email --output-dir out/email-resource-summary --language all --resource-count 327
python -m pytest tests/test_email.py tests/test_email_editorial.py -o "addopts=" -q
```

`--resource-count`는 첫 합성 보고서에 0~20,000개의 고정 ID와 두 TLS 조건의 검증 가능한
`ResourceQueryEvidence`를 만듭니다. 327개에서는 164개와 163개의 두 그룹이 표시됩니다.
옵션 생략 시 기존 2개 리소스 예제와 12개 파일 구성을 유지합니다. Azure 호출·전송·이력 mock은
동일하며 새 유틸리티나 실테넌트 조회 경로를 만들지 않습니다.

구조·색상 대비·오프라인 동작 검사는 전체 suite와 브라우저·실제 이메일 client 검증을
대체하지 않습니다. 각각 실행한 범위만 검증 결과로 보고합니다.

## Annual Report 디자인

제공된 2026 DBIR의 121페이지를 PNG로 렌더링하고 글꼴·텍스트 경계·대표 지면을 대조한 뒤,
산세리프 위계, 3열 그리드의 2:1 배분, 직접 라벨 도표, 선으로 구분한 표를 이메일에 맞게 적용했습니다.
인쇄물의 56pt 제목·9pt 본문을 그대로 옮기지 않고, 좁은 화면과 이메일 fallback에서 읽는 순서가
유지되도록 구성합니다. Verizon 전용 폰트·입체 표지 그림·네온색·원본 통계는 복제하지 않습니다.
UI/UX 자료의 마케팅 Hero·애니메이션·말줄임 추천도 채택하지 않았습니다. 원격 폰트를 추가하지 않으며,
기존 Microsoft Learn 시각 자료·콘텐츠·검증 결과·안전한 링크·리소스 근거는 그대로 보존합니다.

| 디자인 요소 | 구현 |
|---|---|
| 지면과 색 | 흰 바탕, 흑연색 주제목, 절제된 적색 섹션 제목, 중성 본문, 청색 링크 |
| 표지 | 28px 굵은 sans 발행물 이름, 40px 제목. 800px 이상에서 66% 요약·34% 판정, 그 외 세로 배치 |
| 집계 | 중요성별 3행 도표, 8px 막대와 직접 붙인 28px 건수. 분석 완료 분모·생략 건수 분리, 세 자리 수치는 24px |
| 본문 | 24px/20px 굵은 제목 아래 전체 폭 내용. 18px/16px 평문 리드와 14px 본문, 개념 설명은 옅은 회색 음영 |
| 등급 | 셀 전체에 옅은 적색·황색·녹색을 채우고 테두리 없는 12px 높음/보통/낮음 텍스트를 표시 |
| 목차와 장 | 24px 굵은 목차 번호, 17px 제목·14px 요약. 상세는 32px 번호·구분선·복귀 링크 |
| 액션 | 24px 번호·17px 제목의 작업지, 검증 상태, 14px 세부 내용, 어두운 고정폭 명령 영역 |

### 주별 발송

`Send weekly digest emails`는 한 실행의 공지를 UTC 게시일 기준 월요일~일요일로 묶어
수신자마다 주당 한 통을 보냅니다. 주와 공지는 오래된 순서로 정렬하며 메일 기간에 양끝 날짜를
표시합니다. 날짜가 없는 항목은 별도 `N/A` 그룹입니다. 수신자 언어·조회 범위·모든 분석을 보존하며,
한 주의 실패가 다음 주 발송을 막지 않습니다. 모든 요청 발송이 성공해야 `email_sent=true`입니다.
실행 스케줄이나 별도 실행 사이의 중복 발송 처리는 바꾸지 않습니다.

### 검증 루프

1. `preview_email`로 같은 합성 데이터의 기준 화면을 `out/`에 보존합니다.
2. 읽는 순서·정렬·밀도 중 하나의 가설을 세우고 렌더러만 좁게 수정합니다.
3. 이메일 집중 테스트를 통과시킨 뒤 다른 출력 폴더에 후보 미리보기를 생성합니다.
4. Playwright에서 후보 HTML을 열고 [email_reports.cjs](../../tests/browser/email_reports.cjs)를
  실행합니다. 3개 언어 × 2개 보고서 종류 × 2개 스타일 × 6개 화면 크기, 총 72개 조합을 검사합니다.
5. `passed=true`와 별도로 스크린샷의 제목·요약 구분, 숫자 해석, 줄바꿈, 탐색을 평가합니다.
6. 발견한 결함을 수정하고 같은 입력과 검사를 반복한 뒤 import와 전체 pytest를 실행합니다.

검사 크기는 1440/768/640/390/320/844px이며 844px는 가로 방향입니다. 요청한 `innerWidth`,
제목·리드·숫자·배지의 실제 글자 경계, 모든 폭에서 제목/본문의 동일 시작선을 확인합니다.
브리프의 66/34 열 정렬·fallback 세로 순서와 막대의 실제 길이·행 라벨·수치 경계도 검사합니다.
리소스 합계 문구는 오른쪽 8px 여백으로 CJK 닫는 구두점의 글자 경계를 보호하고 원문·글자 크기는 유지합니다.
327개 리소스 미리보기에서도 같은 검사를 실행하고 Portal 링크는 로컬 route로 가로챕니다.
최종 미리보기는 새 폴더뿐 아니라 이미 사용자에게 안내한 현재 경로에도 갱신합니다.
기하 검사 통과를 심미성의 객관적 점수, 실제 독해 속도나 Outlook/Gmail 검증으로 표현하지 않습니다.

## Email client 불변식

- `EMAIL_COLORS`의 순백색 지면, 흑연색 `#202124`, 본문 `#404348`, 보조 글자 `#62666d`,
  구분선 `#dedfe3`, 링크 `#365b8c`, 섹션 제목·장/조치 번호 `#a92336`을 사용합니다.
  색을 채운 표지·요약 상자·큰 장 구분대나
  그림자 카드를 복원하지 않습니다. 별도 dark-mode override는 없습니다.
- 기본 layout은 table과 inline CSS이며 flex/grid/JavaScript에 의존하지 않습니다.
- 모바일 표는 table·tbody·행을 함께 재배치해 암묵적 셀 축소를 막습니다. 다이제스트 목차의
  inline-only/MSO 기본값은 전체 너비 제목·요약 다음에 평가축 세 개를 놓습니다. 축 이름은
  기본 표시하며 desktop media query에서만 머리글을 표시하고 제목 52%/평가축 각 16%로
  나란히 배치합니다. 문서 overflow뿐 아니라 배지가 자신의 셀을 넘지 않는지도 확인합니다.
- 지면은 `width="100%"`와 `max-width: 640px`를 함께 쓰고 Windows Outlook에는 640px MSO ghost
  table을 둡니다. Media query 지원 시 화면 800px에서 760px, 1100px에서 840px까지 확장합니다.
- mobile media query가 inline style을 덮으려면 대상 element에 `azb-*` class가 있어야 합니다.
- `azb-pad` 좌우 여백은 기본 32px, 화면 1100px 이상 40px, 640px 이하 20px, 400px 이하
  16px입니다. Inline-only fallback은 32px를 유지합니다.
- 본문 출력은 하드코딩 없이 `FONT_SIZE_PX["body"]`의 14px를 사용합니다. 발행물 이름은 28px, 주제목은 40px·digest 상세는
  32px이며 모바일 제목은 28px입니다. 섹션 제목 24px/20px·굵기 700, 리드 18px/16px·굵기 400을
  사용합니다. 목차·액션 번호 24px, 장 번호 32px, 집계 28px를 사용하며 세 자리 집계가 있으면
  모두 24px로 표시합니다. 숫자는 굵은 tabular sans입니다. 제목 자간은 0이며 어절 단위 줄바꿈과
  긴 식별자용 `overflow-wrap: anywhere`를 함께 사용합니다. `text-wrap: balance`는 선택적 향상입니다.
  본문 행간은 1.8~1.85이며 inline-only는 기본 크기를 유지합니다.
- `format_email_section_html()`은 모든 폭에서 제목 아래 전체 폭 내용을 같은 시작선에 배치합니다.
  `count_text`는 11px이며 public `full_width` 인자는 유지하되 측면 컬럼·강제 어절 줄바꿈은
  없습니다. 발행물 이름·날짜는 기본 세로이며 데스크톱에서만 35%/65%로 나란히 놓습니다.
- 검증·개념 설명·추가 확인에는 2px 선을 사용하며 개념 설명은 `wash` 배경을 채웁니다.
  등급은 `azb-level-cell` 전체에 `_LEVEL_COLORS`의 옅은 색을 CSS와 `bgcolor`로 적용하고
  셀 padding은 8px입니다. 12px·굵기 600·행 높이 18px의 텍스트 자체에는 배경·테두리·padding이나
  숨김 복제 라벨이 없습니다. 평가축 이름·값은 독립된 너비 33%의 auto-layout 표로 묶습니다. 상태 의미와
  텍스트 대비 **4.5:1 이상**을 유지하며 요약·운영 정보·액션·푸터는 상자 없이 구분합니다.
- 푸터는 분석 기반 소개 문구를 표시하지 않고 면책 고지·피드백·생성 정보를 남깁니다.
  실제 Microsoft Learn 참고 링크와 리소스 근거는 제거하지 않습니다.
- 공통 `FONT_STACK_SANS`는
  `'Noto Sans KR', 'AppleSDGothicR00', 'Malgun Gothic', 'Dotum', Arial, Helvetica, sans-serif`
  순서입니다. Noto Sans KR, AppleSDGothicR00, 맑은 고딕을 우선하며 설치되지 않은 글꼴은 다음
  글꼴로 넘어갑니다.
  `FONT_STACK_DISPLAY`도 같은 스택을 사용합니다. 전용 폰트나 웹폰트는 포함하지 않으며
  명령어·코드는 기존 고정폭 글꼴을 유지합니다.
- 단건과 digest 상세는 흰 표지·평문 리드·독립적인 3축 평가·흰색 2열 운영 정보를 공유합니다.
  화면 800px 이상에서는 요약 66%·평가 34%를 나란히 놓고 평가축을 세 행으로 표시합니다.
  좁은 화면과 inline-only에서는 요약 다음에 이름표가 붙은 세 평가축을 표시합니다.
  운영 정보는 15px 값과 얇은 행 구분선으로 구성하며 모바일에서는 세로로 쌓습니다.
  상세 시작의 번호와 복귀 링크는 흰 지면의 얇은 선 아래 표시합니다.
- Digest의 높음/보통/낮음 집계는 분석 완료 항목만 세고 건너뜀은 별도로 표시합니다. 모든 입력
  항목을 목차에 남기며, 분석 항목의 전체 제목·번호에서 상세 anchor로 이동하고 목차로 돌아옵니다.
  가로 막대의 분모는 `high + medium + low`이며 `digest_analyzed` 캡션에 이를 명시합니다.
  0건 행은 남기되 채움은 그리지 않고 100% 막대는 나머지 셀을 만들지 않습니다. 막대 셀만
  `aria-hidden`으로 표시하며 의미 있는 표의 캡션·행 제목·정확한 건수를 항상 남깁니다.
- 영향 차원 label 열은 HTML `width="96"`, inline `width: 96px` / `min-width: 96px`,
  `white-space: nowrap`, `word-break: keep-all`을 모두 유지합니다. Desktop 2×2 배치로 바꾸지 않습니다.
- 한국어 리소스 섹션 제목은 이메일·Archive·평가용 보고서 모두 `연관 리소스`이며,
  완전한 증거로 확인한 빈 상태는 `연관 리소스가 없습니다.`로 표시합니다. 불완전한 빈 결과를
  확정 부재로 바꾸지 않습니다. 내부 `affected_resources` 필드는 유지합니다.
  20개 이하의 연관 리소스는 사유별 병합 행 다음에 이름, 구독, 리소스 그룹, 종류의 네 열로 표시합니다.
  구독 GUID와 ARM 식별 정보가 충분할 때 앞의 세 열은 Azure Portal 범위 링크가 되며, 모호한
  중첩 리소스는 텍스트로 남깁니다. 화면 640px 이하에서는 각 열을 이름표가 있는 셀로 쌓되,
  전체 사유·그룹·리소스 및 Portal 식별 정보를 보존합니다. 20개 초과는 위의 대규모 리소스
  요약 규칙을 사용하며 메일에 수백 개의 개별 이름 행을 넣지 않습니다.
- 액션은 `01`부터 번호를 매긴 작업지로 표시하며 맥락, 절차, 어두운 고정폭 CLI 블록, 일정,
  가드레일 순으로 나눕니다. 검증 상태·주의 사항과 안전한 링크 동작은 유지합니다. 유일하게
  식별되는 대상과 절차의 `Azure Portal` 시작점은 해당 리소스로 연결합니다. Cloud Shell 링크는
  Bash/PowerShell을 판별해 열기만 하며 명령을 자동 입력하거나 실행하지 않습니다.
- 추가 확인은 번호가 있는 참고 문서 목록보다 앞에 둡니다. 참고 문서는 `description`의
  1~2문장 요약과 `related_content`의 보고서별 확인 지점을 제목 링크 아래에 표시합니다.
  이전 데이터에 `related_content`만 있으면 이를 설명으로 표시합니다.
- 주요 일정과 종료 countdown은 날짜/D-day와 항목을 나란히 놓는 2열 목록입니다. Countdown의
  이행 상태는 이모지 대신 현지화된 텍스트로 표시합니다.
- `str.format()` template의 literal brace는 `_escape_braces()`로 보호합니다.
- Markdown link는 허용 목록의 HTTPS URL과 in-page anchor만 `<a>`로 만들고 나머지는 텍스트로 남깁니다.
- 원격 이미지는 공식 Learn 본문의 설명 있는 PNG/JPEG/GIF만 허용합니다. 단건 2개, digest는
  항목당 1개·전체 4개로 제한하고 `alt`, 캡션, 원문 링크를 모두 보존합니다.
- 새 label은 `src/i18n/labels/ko.py`에 먼저 추가합니다.
- debug HTML 저장 실패가 이메일 전달을 막지 않으며, 구독자 한 명의 실패가 다른 전송을 막지
  않습니다.
- 피드백 알림 실패는 이미 저장된 submission을 지우거나 접수 실패로 바꾸지 않습니다.
