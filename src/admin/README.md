# `src/admin`

[프로젝트 README](../../README.md) > [`src`](../README.md) > `admin`

Container Apps의 EasyAuth가 검증한 Entra principal을 인가하고, 외부 asset 없이 server-rendered
관리 콘솔과 제한된 운영 API를 제공합니다. Archive UI가 활성화되면 header에서 `/archive`로
이동할 수 있지만, reader 인가는 `src/archive/auth.py`의 별도 allow-list 계약을 따릅니다.

## 파일

| 파일 | 책임 |
|---|---|
| [`auth.py`](auth.py) | EasyAuth header parsing, principal/group 식별, allow-list 인가 |
| [`page.py`](page.py) | 외부 dependency 없는 HTML/CSS/JavaScript shell 렌더링 |
| [`router.py`](router.py) | status, subscriber, update, run 조회와 수동 run 시작 route |

## 요청 흐름

1. Container Apps auth sidecar가 제시된 Entra token을 검증하고 inbound 위조 header를 제거합니다.
2. `extract_principal()`이 `X-MS-CLIENT-PRINCIPAL*`에서 object ID, UPN/email, group을 읽습니다.
3. `require_admin()`이 기능 활성화, 인증 여부, 명시적 allow-list를 차례로 검사합니다.
4. `/admin`은 요청마다 CSP nonce를 만들고 `Cache-Control: no-store`로 HTML을 반환합니다.
5. 수동 실행은 전용 분석 경로를 만들지 않고 공용 `orchestrator.start_run()`을 호출합니다.

## 상태 코드 계약

| 상태 | 의미 |
|---|---|
| `404` | Admin UI가 비활성화되어 surface 존재 자체를 숨김 |
| `302` | 로그인하지 않은 browser를 `/.auth/login/aad`로 이동 |
| `401` | Admin API 호출에 인증 principal이 없음 |
| `403` | 인증됐지만 object ID/UPN/group이 allow-list에 없음 |
| `409` | 이미 digest run이 진행 중이라 새 run을 시작할 수 없음 |

## 사용 예시

인증 상태와 nonce CSP 및 run API는 mock request로 검증합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\test_admin.py -o "addopts=" -q
```

로컬 개발에서만 `ADMIN_REQUIRE_AUTH=false`를 사용할 수 있습니다. 이 경우에도
`ADMIN_UI_ENABLED=true`가 필요하며 production ingress에서는 인증을 끄지 않습니다.

## 불변식

- 빈 `ADMIN_ALLOWED_PRINCIPALS`는 allow-all이 아니라 deny-all입니다.
- status response에는 secret 값이나 connection string을 포함하지 않고 설정 여부/이름만
  반환합니다.
- 사용자 입력을 inline script/style에 삽입하지 않으며 CSP nonce와 `frame-ancestors 'none'`을
  유지합니다.
- run 기록은 메모리 관측 정보입니다. 처리 완료의 내구성 source of truth는 checkpoint입니다.
- Archive 상태 카드는 backend/UI 설정 여부만 보여 주며 Blob URL이나 credential은 노출하지 않습니다.