# `src/feedback`

[프로젝트 README](../../README.md) > [`src`](../README.md) > `feedback`

이메일 수신자가 프로그램 버그, 개선 사항, 향후 보고서에 적용할 컨텍스트를 제출하는 공개
surface입니다. 분석 또는 보고서 생성에는 관여하지 않습니다.

## 파일

| 파일 | 책임 |
|---|---|
| [`models.py`](models.py) | 세 유형의 strict bounded request와 immutable submission/receipt 계약 |
| [`page.py`](page.py) | `ko`/`en`/`ja` 반응형 form, 공유 design token, nonce CSP용 HTML |
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

## 검증

```powershell
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\test_feedback.py tests\test_email.py -o "addopts=" -q
```