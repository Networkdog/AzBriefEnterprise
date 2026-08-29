# `email-template`

[프로젝트 README](../../../README.md) > [skills](../README.md) > `email-template`

분석 결과를 **반응형 HTML/plain-text 이메일로 렌더링하고 Azure Communication Services로
전달하는 경로**를 수정할 때 사용하는 skill입니다. 상세 호환성 규칙은 [`SKILL.md`](SKILL.md)에
있습니다.

## 코드 연결

| 경로 | 책임 |
|---|---|
| [`src/email/templates.py`](../../../src/email/templates.py) | 마크다운 변환, 섹션 formatter, HTML template, responsive/Outlook CSS |
| [`src/email/service.py`](../../../src/email/service.py) | 단건·digest 조립, ACS 전송, plain-text fallback |
| [`src/i18n/labels`](../../../src/i18n/labels/) | 언어별 화면/이메일 label |
| [`src/agent/analyzer.py`](../../../src/agent/analyzer.py) | renderer가 소비하는 `AnalysisResult` 모델 |

## 사용 예시

LLM이 만든 제한된 Markdown을 이메일 HTML로 변환할 수 있습니다.

```python
from src.email.templates import markdown_to_html

html = markdown_to_html("> **Private endpoint**: VNet 안에서 PaaS에 연결하는 경로입니다.")
```

렌더링과 이메일 client 호환성 회귀는 실제 formatter 테스트로 확인합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\test_email.py -o "addopts=" -x
```

## 불변식

- HTML 계층은 판단하지 않고 `AnalysisResult`를 결정론적으로 표현합니다.
- 새 label key는 canonical bundle인 `src/i18n/labels/ko.py`에 먼저 추가합니다.
- email client 호환성을 위해 table 기반 layout과 inline CSS를 기본으로 유지합니다.
- `str.format()` 문맥의 literal `{`와 `}`는 `_escape_braces()`로 보호합니다.
- 링크는 `http://`, `https://`, 내부 `#anchor`만 활성화하고 다른 scheme은 텍스트로 낮춥니다.
- Windows Outlook, narrow mobile, wide desktop을 모두 확인하며 JavaScript에 의존하지 않습니다.
- 전송 전 debug artifact 쓰기는 best-effort여야 하며 실패가 이메일 전송을 막아서는 안 됩니다.
