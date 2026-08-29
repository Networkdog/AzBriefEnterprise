# `src/email`

[프로젝트 README](../../README.md) > [`src`](../README.md) > `email`

`AnalysisResult`를 이메일 client에서 읽을 수 있는 **HTML과 plain text로 결정론적으로 렌더링**하고
Azure Communication Services로 전달합니다. 분석 판단은 이 계층에서 바꾸지 않습니다.

## 파일

| 파일 | 책임 |
|---|---|
| [`templates.py`](templates.py) | 제한된 Markdown 변환, section formatter, responsive HTML, 색상/type scale |
| [`service.py`](service.py) | 단건/digest 조립, subscriber 전송, ACS client, console fallback |
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

## 사용 예시

```python
from src.email.templates import markdown_to_html

html = markdown_to_html(
    "> **Private Link**: VNet 경로로 Azure PaaS에 연결합니다.\n\n"
    "- Public endpoint 노출을 줄입니다."
)
```

실제 `AnalysisResult` fixture를 통한 단건·digest 렌더링은 테스트에서 확인합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\test_email.py -o "addopts=" -q
```

## Email client 불변식

- 기본 layout은 table과 inline CSS이며 flex/grid/JavaScript에 의존하지 않습니다.
- card는 `width="100%"`와 max-width를 함께 쓰고 Windows Outlook에는 MSO ghost table을 둡니다.
- mobile media query가 inline style을 덮으려면 대상 element에 `azb-*` class가 있어야 합니다.
- CSS type size는 `FONT_SIZE_PX`의 단계만 사용합니다.
- `str.format()` template의 literal brace는 `_escape_braces()`로 보호합니다.
- Markdown link는 HTTP(S)와 in-page anchor만 `<a>`로 만들고 unsafe scheme은 URL을 버립니다.
- 새 label은 `src/i18n/labels/ko.py`에 먼저 추가합니다.
- debug HTML 저장 실패가 이메일 전달을 막지 않으며, 구독자 한 명의 실패가 다른 전송을 막지
  않습니다.
