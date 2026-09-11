# `src/feedback`

[프로젝트 README](../../README.md) > [`src`](../README.md) > `feedback`

이메일 수신자가 프로그램 버그, 개선 사항, 향후 보고서에 적용할 컨텍스트를 제출하는 공개
surface입니다. 분석 또는 보고서 생성에는 관여하지 않습니다.

## 파일

| 파일 | 책임 |
|---|---|
| [`models.py`](models.py) | 세 유형의 strict bounded request와 immutable submission/receipt 계약 |
| [`page.py`](page.py) | `ko`/`en`/`ja` 독립 제출 form, 전용 헤더, 공유 design token/font, nonce CSP용 HTML |
| [`router.py`](router.py) | `/feedback`, `/api/feedback`, same-origin/rate-limit/honeypot 경계 |
| [`service.py`](service.py) | ID 생성, 저장 우선 순서, ACS notifier와 email footer URL |
| [`../services/feedback.py`](../services/feedback.py) | private state Blob/local file create-only backend |

## 처리 순서

```text
email footer -> GET /feedback -> POST /api/feedback
  -> strict validation -> create-only private state write
  -> ACS notification to FEEDBACK_RECIPIENT_ADDRESS
```

저장이 성공한 뒤에만 알림을 시도합니다. ACS 오류는 `notification_sent=false`로 반환하지만 이미
저장한 피드백을 지우거나 접수 실패로 바꾸지 않습니다. 피드백 본문과 연락처는 log에 남기지
않습니다. 이메일 링크에는 언어와 제한된 Update ID/digest 기간만 포함합니다.

## 화면과 접근

- `FEEDBACK_UI_ENABLED`로 활성화하며 Admin/Archive 로그인 없이 제출할 수 있습니다. 공개 제출이
  다른 관리 화면이나 저장된 피드백 조회 권한을 열어 주지는 않습니다.
- Admin/Archive와 token·글꼴·기본 컨트롤 스타일을 공유하지만 관리 탐색 메뉴와 언어 전환 컨트롤은
  표시하지 않습니다. `?lang=ko`, `?lang=en`, `?lang=ja`로 요청 언어를 지정하며 기본은 영어입니다.
- `render_feedback_page(nonce, language, report_reference)`는 세 입력만 받습니다. 관리 화면
  활성화 여부를 renderer에 전달하지 않으며, report reference는 HTML/JSON에 맞게 escape합니다.
- 제출 중에는 버튼을 잠그고, 성공하면 폼을 초기화한 뒤 URL에서 받은 report reference를
  복원합니다. 알림 실패는 저장 성공과 구분해 표시하며 다시 제출할 필요가 없습니다.
- 요청 실패 시에는 입력을 그대로 유지합니다. 초안의 지속 저장이나 페이지 이동을 넘는 복원,
  별도 접수증 화면, 새 피드백 시작 버튼은 제공하지 않습니다.
- [preview_web.py](../../scripts/preview_web.py)는 같은 renderer를 세 인자로 호출하고 헤더를
  `SYNTHETIC PREVIEW`로 표시합니다. 미리보기는 모의 접수만 수행하며 실제 저장·메일 발송은 없습니다.

## 검증

```powershell
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\test_feedback.py tests\test_email.py -o "addopts=" -q
```