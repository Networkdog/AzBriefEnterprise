# `src/email`

[프로젝트 README](../../README.md) > [`src`](../README.md) > `email`

`AnalysisResult`를 이메일 client에서 읽을 수 있는 **HTML과 plain text로 결정론적으로 렌더링**하고
Azure Communication Services로 전달합니다. 분석 판단은 이 계층에서 바꾸지 않습니다.

## 파일

| 파일 | 책임 |
|---|---|
| [`templates.py`](templates.py) | 제한된 Markdown 변환, section formatter, responsive HTML, 색상/type scale |
| [`service.py`](service.py) | 단건/digest 조립, subscriber 전송, feedback 알림, ACS client, console fallback |
| [`__init__.py`](__init__.py) | package 표시 |

## 렌더링 흐름

```text
AnalysisResult + AzureUpdate
  -> section formatters
  -> responsive HTML_EMAIL_TEMPLATE / digest HTML
  -> independent plain-text body
  -> ACS managed identity or connection string
  -> console fallback when transport is unconfigured
```

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

```powershell
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\test_email.py -o "addopts=" -q
```

## Email client 불변식

- 기본 layout은 table과 inline CSS이며 flex/grid/JavaScript에 의존하지 않습니다.
- card는 `width="100%"`와 max-width를 함께 쓰고 Windows Outlook에는 MSO ghost table을 둡니다.
- mobile media query가 inline style을 덮으려면 대상 element에 `azb-*` class가 있어야 합니다.
- CSS type size는 12px 본문을 기준으로 한 `FONT_SIZE_PX`의 10/11/12/14/16/20px 단계만 사용합니다.
- 한글 시스템 글꼴은 `AppleSDGothicNeo-Regular`, `Microsoft GothicNeo`, `맑은 고딕` 순으로
  우선하며 웹폰트는 사용하지 않습니다.
- 영향 분석 label 열은 HTML `width`와 inline `width`/`min-width`를 함께 사용해 Outlook에서도
  한 글자 너비로 축소되지 않게 합니다.
- 영향받는 리소스는 사유별 병합 행 다음에 이름, 구독, 리소스 그룹, 종류의 네 열로 표시합니다.
  구독 GUID와 ARM 식별 정보가 충분할 때 앞의 세 열은 Azure Portal 범위 링크가 되며, 모호한
  중첩 리소스는 텍스트로 남깁니다.
- 액션 아이템은 맥락, 실행 절차, 일정, 가드레일 순으로 나누며 하나의 카드 안에서 구분선만
  사용합니다. 유일하게 식별되는 대상과 절차의 `Azure Portal` 시작점은 해당 리소스로 연결합니다.
  CLI 블록은 Bash/PowerShell을 판별해 Cloud Shell을 열지만, 명령 자동 입력·실행은 지원한다고
  가정하지 않습니다.
- 참고 문서는 `description`의 1~2문장 내용 요약과 `related_content`의 보고서별 확인 지점을
  제목 링크 아래에 표시합니다. 이전 데이터에 `related_content`만 있으면 이를 설명으로 표시합니다.
- `str.format()` template의 literal brace는 `_escape_braces()`로 보호합니다.
- Markdown link는 HTTP(S)와 in-page anchor만 `<a>`로 만들고 unsafe scheme은 URL을 버립니다.
- 새 label은 `src/i18n/labels/ko.py`에 먼저 추가합니다.
- debug HTML 저장 실패가 이메일 전달을 막지 않으며, 구독자 한 명의 실패가 다른 전송을 막지
  않습니다.
- 피드백 알림 실패는 이미 저장된 submission을 지우거나 접수 실패로 바꾸지 않습니다.
