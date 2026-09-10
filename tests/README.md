# `tests`

[프로젝트 README](../README.md) > `tests`

제어면, Hosted contract, Agent loop, KQL, 이메일, i18n, 서비스와 Enterprise 구성의 회귀를
pytest로 검증합니다. 대부분 외부 Azure/Foundry 호출을 mock해 빠르고 결정론적으로 실행합니다.

## 영역별 찾기

| 테스트 묶음 | 대표 파일 |
|---|---|
| Hosted 경계 | [`test_hosted_contract.py`](test_hosted_contract.py), [`test_hosted_client.py`](test_hosted_client.py), [`test_hosted_agent.py`](test_hosted_agent.py) |
| Agent loop와 resilience | [`test_analyzer.py`](test_analyzer.py), [`test_context_store.py`](test_context_store.py), [`test_resilience.py`](test_resilience.py) |
| Foundry specialist team | [`test_foundry_backend.py`](test_foundry_backend.py), [`test_foundry_multi_agent.py`](test_foundry_multi_agent.py), [`test_provision_foundry_agents.py`](test_provision_foundry_agents.py) |
| KQL과 Azure evidence | [`test_kql_sanitize.py`](test_kql_sanitize.py), [`test_kql_retry.py`](test_kql_retry.py), [`test_impact_tools.py`](test_impact_tools.py), [`test_billing.py`](test_billing.py) |
| 제어면 | [`test_api.py`](test_api.py), [`test_admin.py`](test_admin.py), [`test_admin_readiness.py`](test_admin_readiness.py), [`test_archive.py`](test_archive.py), [`test_mcp_server.py`](test_mcp_server.py), [`test_orchestrator.py`](test_orchestrator.py), [`test_scheduler.py`](test_scheduler.py) |
| 전달과 언어 | [`test_email.py`](test_email.py), [`test_i18n.py`](test_i18n.py), [`test_quality_evaluator.py`](test_quality_evaluator.py) |
| 편집형 이메일 | [test_email_editorial.py](test_email_editorial.py): 공통 문서 구조, 링크·anchor, 건너뜀 분리 집계, 전체 제목, 리소스 식별 정보, 액션 검증 표시, 색상 대비와 오프라인 미리보기 |
| 이메일 화면 루프 | [browser/email_reports.cjs](browser/email_reports.cjs): 72개 합성 레이아웃, CSS 제거 fallback, 정보 보존, 배지 경계, 목차 왕복, 허용된 원격 이미지·대체 텍스트와 대표 스크린샷 |
| 종류별 환경 연관성 | [`test_environment_relevance.py`](test_environment_relevance.py): 계획/보고 계약, 리소스 없는 가치·SDK 사례, 3개 언어 표시, Archive 원문 보존과 구독자 역할 평가 |
| Data access | [`test_services.py`](test_services.py), [`test_runtime_inventory.py`](test_runtime_inventory.py), [`test_checkpoint.py`](test_checkpoint.py), [`test_archive_store.py`](test_archive_store.py), [`test_rss_parser.py`](test_rss_parser.py) |
| 결정론적 평가 | [`test_archive_evaluation.py`](test_archive_evaluation.py), [`test_quality_evaluator.py`](test_quality_evaluator.py), [`test_quality_campaign.py`](test_quality_campaign.py) |
| Security/config | [`test_security.py`](test_security.py), [`test_config.py`](test_config.py), [`test_enterprise_config.py`](test_enterprise_config.py) |
| 공개 저장소 위생 | [`test_repository_hygiene.py`](test_repository_hygiene.py) |
| 웹 사용자 흐름 | [browser/control_surfaces.cjs](browser/control_surfaces.cjs): 합성 서버에서 탐색·필터 URL·요청 경합·폼 검증·접수증·반응형 경계 |

[`conftest.py`](conftest.py)는 `sample_rss_xml`, `sample_update`, `sample_analysis_result`처럼 여러
test가 공유하는 realistic fixture를 제공합니다.

## 실행 예시

가장 빠른 변경 범위 test를 먼저 실행합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\test_hosted_contract.py tests\test_hosted_client.py -o "addopts=" -q
```

이메일 변경은 기존 회귀와 편집형 레이아웃 계약을 함께 검사합니다.

```powershell
& .\.venv\Scripts\Activate.ps1
python -m pytest tests/test_email.py tests/test_email_editorial.py -o "addopts=" -q
```

[test_email_editorial.py](test_email_editorial.py)는 지정된 텍스트·배경 조합의 대비가 **4.5:1 이상**인지,
합성 ko/en/ja 단건·digest의 전체 스타일/inline-only HTML 12개가 전송 client 초기화 없이
생성되는지도 확인합니다. 구조와 오프라인 동작 검사는 전체 suite, 브라우저 레이아웃 또는
실제 이메일 client 검증을 대신하지 않습니다. 완료하지 않은 검증의 통과 건수를 기록하지 않습니다.

이메일 화면 검사는 `python -m scripts.preview_email --output-dir out/email-editorial-preview --language all`
실행 후 생성된 HTML 하나를 Playwright MCP에서 엽니다. `browser_run_code_unsafe`에 `filename`으로
[browser/email_reports.cjs](browser/email_reports.cjs)의 절대 경로를 전달합니다. 이 파일은 직접
평가하는 `async function checkEmailReports(page, baseUrl)`이며 기본값은 현재 페이지의 폴더입니다.
명시적인 `baseUrl`은 끝에 `/`를 붙인 합성 미리보기 폴더 주소여야 합니다. 로컬 파일 또는 loopback
HTTP만 허용합니다. ko/en/ja·single/digest·full/inline-only·1440/768/640/390/320/844px를 검사하고
`passed`, `failures`, 측정값을 반환합니다. `passed=true`를 수용 조건으로 사용합니다. file 미리보기의
대표 스크린샷은 HTML과 같은 폴더에 저장하므로 입력 폴더를 반드시 `out/` 아래에 둡니다.
숫자와 범례의 해석, 위계, 줄바꿈은 스크린샷을 열어 따로 평가하고 실패한 조합을 수정 후 재검사합니다.
발행물 로고, 큰 건수, 장·목차 번호와 배지는 DOM range의 실제 글자 경계로 검사하고,
데스크톱 24%/76% 제목 레일과 좁은 화면의 세로 배치도 확인합니다. 새 표시 크기의 회귀를
막기 위한 검사이며 심미성은 같은 크기의 전후 스크린샷으로 따로 평가합니다.

웹 검증은 먼저 `python -m scripts.preview_web --port 8765`로 합성 서버를 시작한 뒤 Playwright MCP의
`browser_run_code_unsafe`에 `filename`으로 [browser/control_surfaces.cjs](browser/control_surfaces.cjs)의
절대 경로를 전달합니다. 이 파일은 CommonJS module이 아니라 도구가 직접 평가하는
`async function checkControlSurfaces(page, baseUrl)`입니다. 파일을 직접 평가할 수 있는 Playwright
runner에서도 호출할 수 있으며 기본 주소는 `http://127.0.0.1:8765`입니다.
임시 데이터 변경은 미리보기에만 발생합니다. 429와 알림 실패는 브라우저 route mock으로 재현합니다.
스크린샷은 별도로 `out/`에 생성하고 읽어 확인하며, 이 함수의 DOM 검사만으로 시각 검증을 주장하지 않습니다.

전체 suite는 project default coverage option을 명시적으로 제거하고 첫 실패에서 멈출 수 있습니다.

```powershell
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\ -o "addopts=" -x
```

CI와 같은 coverage gate가 필요하면 project addopts를 유지하거나 명시적으로 실행합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\ --cov=src --cov-report=term-missing --cov-fail-under=40
```

## 테스트 작성 원칙

- 외부 API unit test는 network 대신 mock response로 성공, transient failure, malformed payload,
  authorization failure를 분리합니다.
- `get_settings()`가 cache되므로 환경 변수를 바꾸는 test는 cache와 관련 singleton을 정리합니다.
- Foundry role/roster test는 machine의 실제 `.env` 값을 상속하지 않도록 관련 변수를 명시적으로
  제거하거나 설정합니다.
- async test는 `pytest-asyncio`의 auto mode를 사용합니다.
- collection error는 “한 테스트 실패”가 아니라 해당 파일 전체가 미검증된 상태입니다. 즉시
  해결하고 숨겨진 실패를 확인합니다.
- local suite 통과는 live identity, quota, private network, deployed Agent version을 검증하지
  않습니다. 운영 smoke test 결과와 구분해 보고합니다.
- Quality campaign test는 기간 dataset hash/split, 다층 release gate, A/A paired 비교, 안전 회귀
  우선순위, fake Hosted snapshot의 report artifact 생성을 네트워크 없이 검증합니다.