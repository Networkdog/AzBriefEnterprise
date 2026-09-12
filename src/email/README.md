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

[preview_email.py](../../scripts/preview_email.py)는 고객 데이터가 아닌 **SYNTHETIC 합성 데이터**로
ko/en/ja 단건·digest의 전체 스타일 및 inline-only HTML 12개를 저장합니다. 전송 설정과 종료
이력을 mock하고 전송 client를 만들지 않으며 Azure 호출이나 이메일 발송도 하지 않습니다.
Inline-only 버전은 `<style>` 블록을 제거해 fallback을 점검합니다. 합성 HTML은 공개 Learn
스크린샷 URL 하나를 포함하므로 브라우저 시각 검증 시에는 해당 정적 이미지를 요청합니다.

```powershell
& .\.venv\Scripts\Activate.ps1
python -m scripts.preview_email --output-dir out/email-editorial-preview --language all
python -m pytest tests/test_email.py tests/test_email_editorial.py -o "addopts=" -q
```

구조·색상 대비·오프라인 동작 검사는 전체 suite와 브라우저·실제 이메일 client 검증을
대체하지 않습니다. 각각 실행한 범위만 검증 결과로 보고합니다.

## 디자인 근거와 개선 루프

[Redokun의 Annual Report Design 가이드](https://redokun.com/blog/annual-report-design)는
일관된 위계, 정확한 데이터 시각화, 독자의 판단 순서와 출력별 검증을 강조합니다. 예시 이미지는
참고만 하며 사진·템플릿을 이메일에 복제하지 않습니다. 보고서 안의 원격 이미지는 위의 공식
Learn 본문 allow-list 경로에만 한정합니다.
작은 변화로는 부족하다는 피드백 후 [Monzo Annual Report 2025 웹 원문](https://monzo.com/annual-report/2025)의
고해상도 표지·수치 화면을 추가로 분석했습니다. 공개 PDF와 화면은 로컬 `out/`에만 보존하고
발행물의 글자 크기 대비와 면 구성, 제목·본문 그리드를 이메일에 맞게 재해석합니다.

| 참고 요소 | 이메일에 적용한 방식 |
|---|---|
| Monzo 표지의 크기 대비 | 36px 발행물 이름, 48px 문서 제목, 18.75px 핵심 문장과 13px 본문 |
| Monzo 주요 수치의 독립된 면 | 48px 건수를 세 개의 옅은 의미 색면에 배치하고 분석 건수 비율의 8px 막대 유지 |
| 38번 Great Typography의 면과 큰 제목 | 전체 폭 청록색 장 구분대에 48px 번호·목차 복귀 링크 배치 |
| 57번 Company Data Sheet의 정보 열 | 데스크톱 제목 18%·본문 82% 그리드, 목차는 전체 너비, 좁은 화면은 세로 배치 |
| 59번 Interactive Layout의 문서 내 탐색 | 목차 번호를 29px 별도 열로 분리, 17px 제목·13px 요약과 3축 보존 |

반복 절차는 다음과 같습니다.

1. `preview_email`로 같은 합성 데이터의 기준 화면을 `out/`에 보존합니다.
2. 읽는 순서·정렬·밀도 중 하나의 가설을 세우고 렌더러만 좁게 수정합니다.
3. 이메일 집중 테스트를 통과시킨 뒤 다른 출력 폴더에 후보 미리보기를 생성합니다.
4. Playwright에서 후보 HTML을 열고 [email_reports.cjs](../../tests/browser/email_reports.cjs)를
  실행합니다. 3개 언어 × 2개 보고서 종류 × 2개 스타일 × 6개 화면 크기, 총 72개 조합을 검사합니다.
5. `passed=true`와 별도로 스크린샷의 제목·요약 구분, 숫자 해석, 줄바꿈, 탐색을 평가합니다.
6. 발견한 결함을 수정하고 같은 입력과 검사를 반복한 뒤 import와 전체 pytest를 실행합니다.

검사 크기는 1440/768/640/390/320/844px이며 844px는 가로 방향입니다. `innerWidth`가 요청값과
같은지도 검사합니다. 이 루프에서 CSS 제거·320px 영어 목차의 `Medium` 배지 7px 넘침과
매우 좁은 제목 열을 발견해 전체 너비 기본 레이아웃으로 수정했습니다. 실제 이메일 client,
사람의 독해 속도, 분석 품질은 별도의 평가 대상입니다.
두 번째 구조 재설계에서는 커진 로고와 날짜가 inline-only에서 겹치는 문제를 화면으로 발견했습니다.
발행물 이름·날짜도 기본 세로 배치로 바꾸고 검사 대상에 실제 글자 경계와 제목 레일 정렬을
추가했습니다. 기하 검사가 통과해도 심미성 검증을 대신하지 않으며, 같은 크기의 전후 지면과
참고 이미지에서 어떤 구조를 적용했는지 함께 확인합니다.

## Email client 불변식

- `EMAIL_COLORS`의 순백색 바탕과 지면, 잉크색 `#182b32`, 청록색 `#08746b`를 사용합니다.
  짙은 남색 hero나 둥근 그림자 카드를 복원하지 않습니다. 별도 dark-mode override는 없습니다.
- 기본 layout은 table과 inline CSS이며 flex/grid/JavaScript에 의존하지 않습니다.
- 모바일 표는 table·tbody·행을 함께 재배치해 암묵적 셀 축소를 막습니다. 다이제스트 목차의
  inline-only/MSO 기본값은 전체 너비 제목·요약 다음에 평가축 세 개를 놓습니다. 축 이름은
  기본 표시하며 desktop media query에서만 머리글을 표시하고 제목 52%/평가축 각 16%로
  나란히 배치합니다. 문서 overflow뿐 아니라 배지가 자신의 셀을 넘지 않는지도 확인합니다.
- 지면은 `width="100%"`와 `max-width: 640px`를 함께 쓰고 Windows Outlook에는 640px MSO ghost
  table을 둡니다. Media query 지원 시 화면 800px에서 760px, 1100px에서 900px까지 확장합니다.
- mobile media query가 inline style을 덮으려면 대상 element에 `azb-*` class가 있어야 합니다.
- `azb-pad` 좌우 여백은 기본 32px, 화면 1100px 이상 48px, 640px 이하 20px, 400px 이하
  16px입니다. Inline-only fallback은 32px를 유지합니다.
- `FONT_SIZE_PX`의 본문 13px 단계는 유지하고 `cover=36`, `stat=48`을 표시용으로 추가합니다.
  발행물 이름은 36px, 문서 제목은 48px이며 모바일에서는 모두 29px입니다. 공통 section 제목과
  핵심 문장은 데스크톱 18.75px·모바일 15.75px이며 굵기는 각각 525·700입니다.
  Inline-only 출력은 기본 크기를 유지합니다. 주요 본문 블록 행간은 1.8~1.85입니다. 목차는 별도의 29px 번호 열과
  17px 제목·13px 요약을 사용합니다. 건수·상세 번호는 48px tabular numerals이며 세 자리 건수는
  모두 29px로 표시합니다. 제목 자간은 0으로 유지합니다.
- `format_email_section_html()`은 화면 800px 이상에서 제목 18%·본문 82% 그리드로 배치하고
  inline/MSO 기본값은 전체 너비 표를 세로로 쌓습니다. `full_width=True`인 목차는 전체 너비를
  유지합니다. 발행물 이름·날짜도 기본값은 세로이며 데스크톱에서만 44%/56%로 나란히 놓습니다.
- 등급·검증 배지, 핵심 요약, concept box, 추가 확인의 세로 강조선에는 공통
  `SEMANTIC_ACCENT_WIDTH_PX = 4`를 적용합니다. 배지의 위아래
  padding은 각각 4px를 유지하고 등급 배지 글자는 18px를 사용합니다. 각 평가축 이름·배지는
  독립된 너비 33%의 auto-layout 표로 묶어 좁은 화면에서 함께 줄바꿈하며, inline-only에서도
  `Medium` 글자를 줄이거나 자르지 않습니다. 상태 텍스트·기존 색상과 텍스트 대비 **4.5:1 이상**을 유지하고
  중성 구분선은 기존의 얇은 두께를 유지합니다.
- 공통 `FONT_STACK_SANS`는
  `'Apple SD Gothic Neo', 'Malgun Gothic', 'Dotum', Arial, Helvetica, sans-serif` 순서입니다.
  웹폰트는 사용하지 않으며 명령어·코드 블록은 기존 고정폭 글꼴을 유지합니다.
- 단건과 digest 상세는 흰 hero, 핵심 요약, 독립적인 중요성·영향도·직무연관성 strip과 2열
  운영 정보를 공유합니다. 운영 정보는 옅은 바탕과 청록색 위 구분선으로 묶고 모바일에서 세로로
  쌓습니다. 상세 시작에서는 전체 폭 청록 구분대에 큰 번호와 목차 복귀 링크를 나란히 표시합니다.
  이 구간의 `on_accent` 글자색도 배경 대비 4.5:1 이상을 검증합니다.
- Digest의 높음/보통/낮음 집계는 분석 완료 항목만 세고 건너뜀은 별도로 표시합니다. 모든 입력
  항목을 목차에 남기며, 분석 항목의 전체 제목·번호에서 상세 anchor로 이동하고 목차로 돌아옵니다.
  비례 막대의 분모는 `high + medium + low`이며 0건 구간은 생략하고 분석 완료 0건이면 막대 자체를
  생략합니다. 막대는 텍스트 집계의 보조 표현이며 정확한 건수와 이름표를 항상 남깁니다.
- 영향 차원 label 열은 HTML `width="96"`, inline `width: 96px` / `min-width: 96px`,
  `white-space: nowrap`, `word-break: keep-all`을 모두 유지합니다. Desktop 2×2 배치로 바꾸지 않습니다.
- 영향받는 리소스는 사유별 병합 행 다음에 이름, 구독, 리소스 그룹, 종류의 네 열로 표시합니다.
  구독 GUID와 ARM 식별 정보가 충분할 때 앞의 세 열은 Azure Portal 범위 링크가 되며, 모호한
  중첩 리소스는 텍스트로 남깁니다. 화면 640px 이하에서는 각 열을 이름표가 있는 셀로 쌓되,
  전체 사유·그룹·리소스 및 Portal 식별 정보를 보존합니다.
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
