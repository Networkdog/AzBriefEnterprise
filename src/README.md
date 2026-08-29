# `src`

[프로젝트 README](../README.md) > `src`

AzBrief Enterprise의 Python application package입니다. 하나의 패키지 안에 **Container Apps
제어면**과 **Foundry Hosted Agent 분석 런타임**이 함께 있지만, 두 런타임은 서로 다른 process와
identity에서 실행됩니다.

## 진입점과 소유권

| 파일/디렉터리 | 실행 위치 | 책임 |
|---|---|---|
| [`main.py`](main.py) | Container App | FastAPI, `/api/*`, `/admin`, `/mcp`, service lifespan |
| [`scheduler.py`](scheduler.py) | Container Apps Job | 예약 digest 한 번을 시작하고 process exit code 반환 |
| [`orchestrator.py`](orchestrator.py) | App/Job | RSS window, concurrency, digest, watermark/checkpoint |
| [`hosted_agent.py`](hosted_agent.py) | Foundry Hosted Agent | v2 contract 처리와 `AzureUpdateAnalyzer` 소유 |
| [`agent/`](agent/) | Hosted Agent 중심 | LangGraph, Prompt Agent adapter, tools, resilience, evaluation |
| [`admin/`](admin/) | Container App | EasyAuth 기반 관리 콘솔과 run API |
| [`email/`](email/) | App/Job | report/digest 렌더링과 ACS 전달 |
| [`i18n/`](i18n/) | 공용 | 언어 registry와 fallback |
| [`rss/`](rss/) | 공용 | Azure Update 수집·정규화 |
| [`services/`](services/) | Hosted Agent/제어면 | Azure 및 공개 API data access, durable checkpoint |
| [`config.py`](config.py) | 공용 | environment를 검증된 `Settings`로 변환 |
| [`middleware.py`](middleware.py) | Container App | API key와 bounded in-memory rate limiter |
| [`logging_config.py`](logging_config.py) | 모든 entry point | structlog/stdout/file/Azure Monitor logging 구성 |

## 실행 흐름

```text
Container Apps Job -> scheduler -> orchestrator -> HostedAgentAnalyzer
                                              -> Foundry Hosted Agent
                                              -> AzureUpdateAnalyzer
                                              -> AnalysisResult
                   <- digest customization/email/checkpoint
```

FastAPI lifespan도 같은 `HostedAgentAnalyzer`, `EmailService`, `AzureUpdateParser`를 만들어
orchestrator와 MCP에 등록합니다. 제어면은 `AzureUpdateAnalyzer`를 직접 import해 fallback으로
실행하지 않습니다.

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

예약 실행과 같은 제어 흐름을 한 번 실행합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; python -m src.scheduler
```

## 불변식

- 환경 설정은 `get_settings()`를 통해 읽고 새 setting은 `src/config.py`와 문서/배포 설정을 함께
  연결합니다.
- structlog를 사용하며 Container App/Job은 파일 대신 stdout logging을 기본으로 합니다.
- `/api/*`는 `API_KEY`가 설정된 경우 인증하고, `/mcp`는 key가 없을 때도 열리지 않습니다.
- rate limiter의 proxy header 신뢰는 검증된 reverse proxy 뒤에서만 활성화합니다.
- Python 3.10 문법 범위를 지키고 dependency를 `pyproject.toml`과 `requirements.txt`에 동시에
  반영합니다.

## 검증

```powershell
& .\.venv\Scripts\Activate.ps1; python -m pytest tests\test_api.py tests\test_scheduler.py tests\test_orchestrator.py -o "addopts=" -q
```
