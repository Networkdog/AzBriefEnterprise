# `src`

[프로젝트 README](../README.md) > `src`

AzBrief Enterprise의 Python application package입니다. 하나의 패키지 안에 **Container Apps
제어면**과 **Foundry Hosted Agent 분석 런타임**이 함께 있지만, 두 런타임은 서로 다른 process와
identity에서 실행됩니다.

## 진입점과 소유권

| 파일/디렉터리 | 실행 위치 | 책임 |
|---|---|---|
| [`main.py`](main.py) | Container App | FastAPI, `/api/*`, `/admin`, `/archive`, `/feedback`, `/mcp`, service lifespan |
| [`scheduler.py`](scheduler.py) | Container Apps Job | 정기 실행 활성화 후 내구성 일정 lease를 선점하고 예약 digest를 시작하며 종료 코드 반환 |
| [`orchestrator.py`](orchestrator.py) | App/Job | checkpoint/기간/최근 N/번호/URL 선택, concurrency, digest, watermark |
| [`hosted_agent.py`](hosted_agent.py) | Foundry Hosted Agent | 범위 지정이 가능한 v3 contract 처리, 기존 v2 비범위 요청 호환, `AzureUpdateAnalyzer` 소유 |
| [`agent/`](agent/) | Hosted Agent 중심 | LangGraph, Prompt Agent adapter, tools, resilience, evaluation |
| [`admin/`](admin/) | Container App | EasyAuth 기반 관리 콘솔과 run API |
| [`archive/`](archive/) | Container App/Job | canonical 문서 계약, reader 인가, 검색 API와 browser shell |
| [`feedback/`](feedback/) | Container App | 공개 제출 폼, 검증과 비공개 저장, 저장 후 선택적 이메일 알림 |
| [`email/`](email/) | App/Job | report/digest 렌더링과 ACS 전달 |
| [`i18n/`](i18n/) | 공용 | 언어 registry와 fallback |
| [`rss/`](rss/) | 공용 | Azure Update 수집·정규화 |
| [`services/`](services/) | Hosted Agent/제어면 | Azure 및 공개 API data access, durable checkpoint/archive |
| [`config.py`](config.py) | 공용 | environment를 검증된 `Settings`로 변환 |
| [`middleware.py`](middleware.py) | Container App | API key와 bounded in-memory rate limiter |
| [`logging_config.py`](logging_config.py) | 모든 entry point | structlog/stdout/file/Azure Monitor logging 구성 |
| [`web_design.py`](web_design.py), [`web_fonts.py`](web_fonts.py) | 웹 화면 | 공통 디자인 token과 font/CSP 정책. Admin/Archive 탐색과 Feedback 전용 헤더는 별도 구성 |

## 실행 흐름

```text
Container Apps Job -> schedule dispatcher/lease -> scheduler -> orchestrator -> HostedAgentAnalyzer
                                              -> Foundry Hosted Agent
                                              -> evidence specialists (parallel)
                                              -> coordinator / writer / reviewer
                                              -> AzureUpdateAnalyzer
                                              -> AnalysisResult
                   <- canonical archive -> digest customization/email -> checkpoint
```

FastAPI lifespan도 같은 `HostedAgentAnalyzer`, `ArchiveService`, `EmailService`,
`AzureUpdateParser`를 만들어 orchestrator와 MCP에 등록합니다. 제어면은
`AzureUpdateAnalyzer`를 직접 import해 fallback으로 실행하지 않습니다.

새 고객 배포의 Job은 기본적으로 Manual이며 자동으로 이 흐름을 시작하지 않습니다.
[고객 배포 가이드](../infra/CUSTOMER_DEPLOYMENT.md)의 인수 검증 후 정기 실행을 활성화합니다.
Hosted v3를 먼저 게시한 뒤 호환되는 제어면을 배포하며, 구독자별 범위 분석 결과는 이메일 전용으로
취급해 공유 canonical Archive에 섞지 않습니다.

## 사용 예시

패키지 import 계약을 확인합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; python -c "import src"
```

FastAPI 제어면을 로컬에서 시작합니다. Hosted Agent 환경 변수가 없으면 lifespan 초기화가
fail closed합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; python -m uvicorn src.main:app --reload
```

예약 실행과 같은 제어 흐름을 강제로 한 번 실행합니다. 배포된 Job은
`SCHEDULE_DISPATCH_ENABLED=true`이므로 만기 일정이 있을 때만 이 경로를 호출합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; python -m src.scheduler
```

## 불변식

- 환경 설정은 `get_settings()`를 통해 읽고 새 setting은 `src/config.py`와 문서/배포 설정을 함께
  연결합니다.
- structlog를 사용하며 Container App/Job은 파일 대신 stdout logging을 기본으로 합니다.
- machine-facing 분석 API는 `API_KEY`, `/admin`과 `/archive`는 EasyAuth allow-list를 사용하고,
  `/mcp`는 key가 없을 때도 열리지 않습니다.
- `/feedback`은 활성화 시 공개 제출 경로이며 Admin/Archive 조회 권한을 부여하지 않습니다.
  입력 검증·same-origin 검사·rate limit을 유지하고, 비공개 저장이 성공한 뒤에만 알림을 보냅니다.
- Archive가 구성된 run은 canonical 문서 저장 뒤에만 digest와 checkpoint를 진행합니다.
- rate limiter의 proxy header 신뢰는 검증된 reverse proxy 뒤에서만 활성화합니다.
- Python 3.10 문법 범위를 지키고 dependency를 `pyproject.toml`과 `requirements.txt`에 동시에
  반영합니다.

## 검증

```powershell
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\test_api.py tests\test_scheduler.py tests\test_orchestrator.py -o "addopts=" -q
```
