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

## 브라우저 동작

- Admin/Archive와 공통 헤더, 글꼴, 로컬 아이콘, 입력 크기를 사용합니다. 활성화된 화면의 탐색 링크만
  표시하며 인증이 필요한 화면의 권한은 그대로 검사합니다.
- 한국어·영어·일본어를 변경해도 입력값을 유지합니다. 초안은 페이지 메모리에만 있으며
  localStorage/sessionStorage에 저장하지 않습니다. 미제출 내용이 있으면 이탈을 경고합니다.
- 제목/상세/이메일은 인라인 검증과 첫 오류 포커스를 제공합니다. 429와 503은 서로 다른 메시지를
  표시하고 입력을 유지합니다.
- 성공 응답은 `accepted`를 확인하고 접수 번호를 표시합니다. `notification_sent=false`는 이미
  저장된 상태로 표시하며 재제출을 유도하지 않습니다. 새 제출은 별도 버튼으로 시작합니다.
- Archive에서 진입하면 언어와 `archive:<불변 ID>`를 전달받고 해당 보고서로 돌아갈 수 있습니다.
  임의의 외부 URL을 복귀 링크로 사용하지 않습니다.

## 검증

```powershell
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\test_feedback.py tests\test_email.py -o "addopts=" -q
```