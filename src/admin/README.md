# `src/admin`

[프로젝트 README](../../README.md) > [`src`](../README.md) > `admin`

Container Apps의 EasyAuth가 검증한 Entra principal을 인가하고, 외부 asset 없이 server-rendered
관리 콘솔과 제한된 운영 API를 제공합니다. Archive UI가 활성화되면 header에서 `/archive`로
이동할 수 있지만, reader 인가는 `src/archive/auth.py`의 별도 allow-list 계약을 따릅니다.

## 파일

| 파일 | 책임 |
|---|---|
| [`auth.py`](auth.py) | EasyAuth header parsing, principal/group 식별, allow-list 인가 |
| [`configuration.py`](configuration.py) | Subscriber/admin/매일 UTC 일정과 실행 lease를 ETag 보호 저장소에서 관리 |
| [`page.py`](page.py) | 외부 dependency 없는 HTML/CSS/JavaScript shell 렌더링 |
| [`readiness.py`](readiness.py) | ARM/Foundry evidence를 섹션별 녹색/빨간 운영 체크로 판정 |
| [`router.py`](router.py) | status, subscriber, schedule, update, run 조회와 제한된 mutation route |

## 요청 흐름

1. Container Apps auth sidecar가 제시된 Entra token을 검증하고 inbound 위조 header를 제거합니다.
2. `extract_principal()`이 `X-MS-CLIENT-PRINCIPAL*`에서 object ID, UPN/email, group을 읽습니다.
3. `require_admin()`이 기능 활성화, 인증 여부, 명시적 allow-list를 차례로 검사합니다.
4. `/admin`은 요청마다 CSP nonce를 만들고 `Cache-Control: no-store`로 HTML을 반환합니다.
5. 수동 실행은 기간, 최근 N개, 번호, URL을 `RunSelection`으로 검증한 뒤 공용
  `orchestrator.start_run()`을 호출하며 예약 checkpoint는 갱신하지 않습니다. 기본값은 이메일
  미발송이고 명시적으로 선택한 경우에만 digest를 전달합니다. 드라이런과 전달은 상호 배타적입니다.
6. 추가한 매일 UTC 시각은 private Admin 구성에 저장됩니다. Scheduler Job은 ETag lease로 만기
  슬롯 하나를 선점한 경우에만 분석 런타임을 초기화합니다.
7. `AdminReadinessCollector`는 ARM과 Foundry 조회를 병렬 실행하고 실패를 개별 빨간 체크와 조치
  문구로 변환합니다. 하나의 provider 오류가 `/api/admin/status` 전체를 실패시키지 않습니다.
8. Agent check는 latest version이 존재하는 것만으로 통과하지 않습니다. Hosted는
  `HostedAgentDefinition`/`ACTIVE`, 6개 specialist는 `PromptAgentDefinition`/`ACTIVE`여야 합니다.

EasyAuth는 browser session cookie로 인증한 상태 변경 요청(`POST`/`PUT`/`DELETE`)에 CSRF 검사를
적용합니다. Enterprise
template은 현재 Container App HTTPS origin을 `login.allowedExternalRedirectUrls`에 명시해 같은
origin의 분석 실행, 구독자·관리자 변경 요청만 sidecar를 통과시킵니다. Application의 principal
allow-list와 입력 검증은 그 뒤에도 그대로 적용됩니다.

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
- Main content는 `width: 100%`와 `max-width`를 함께 사용하고 넓은 viewport에서 좌우 중앙에 둡니다.
- 각 운영 영역은 고유 heading이 있는 full-width panel입니다. Mutation 버튼은 관련 입력과 같은
  `action-surface`의 작업 띠에 두고, 결과 table은 이름이 있는 subsection으로 분리합니다.
- 가로 스크롤이 필요한 관리 table은 작업 열을 첫 열에 두어 mobile에서도 명령을 먼저 노출합니다.
- run 기록은 메모리 관측 정보입니다. 처리 완료의 내구성 source of truth는 checkpoint입니다.
- Admin 수동 선택은 예약 checkpoint와 격리되며 한 요청에서 최대 100개 업데이트만 허용합니다.
- 실행 이력의 진단은 safe `RunRecord` projection만 사용해 Archive 실패, 전달, checkpoint, bounded
  error를 표시합니다. 원시 tenant evidence나 secret은 표시하지 않습니다.
- Console-managed 구독자는 ETag 보호 `PUT`으로 수정할 수 있지만 배포 구독자는 수정·삭제할 수
  없습니다. 이메일 변경은 모든 배포/관리 구독자와 중복되지 않아야 합니다.
- 배포 cron은 보호된 기본 일정이고 Admin 일정은 추가 항목입니다. 같은 일정 occurrence는
  `automatic_run_active` lease와 마지막 occurrence 기록으로 한 번만 선점합니다.
- Archive 상태 카드는 backend/UI 설정 여부만 보여 주며 Blob URL이나 credential은 노출하지 않습니다.
- readiness response는 safe projection만 반환하며 raw ARM/Agent 객체, token, secret, resource ID를
  노출하지 않습니다. Enterprise Bicep의 `ADMIN_READINESS_*` 값이 expected topology의 원본입니다.
- 복합 `ADMIN_READINESS_*` 값은 JSON 또는 Base64 JSON을 허용합니다. Bicep과 CLI deployment는
  Windows/Azure CLI의 quote 제거를 피하도록 Base64 JSON을 사용하며 malformed 값은 빈 inventory로
  fail closed합니다.