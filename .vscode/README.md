# `.vscode`

[프로젝트 README](../README.md) > `.vscode`

팀이 공유하는 VS Code 확장 권장, debug/task 설정, 로컬 MCP 서버 설정을 보관합니다. 이 파일들은
Azure에 배포되지 않습니다.

## 파일

| 파일 | 현재 역할 |
|---|---|
| [`extensions.json`](extensions.json) | Python 및 Azure Functions 확장 권장 |
| [`settings.json`](settings.json) | `.venv`와 Azure Functions 관련 workspace 설정 |
| [`tasks.json`](tasks.json) | Functions Core Tools의 `func host start` task |
| [`launch.json`](launch.json) | `localhost:9091`의 Python Functions debugger attach |
| [`mcp.json`](mcp.json) | VS Code용 GitHub MCP 서버와 실행 시 입력받는 PAT 정의 |

## 현재 애플리케이션 실행

이 저장소의 현재 제어면은 Azure Functions가 아니라 FastAPI/Container Apps입니다. 따라서
기존 `F5` 구성은 `host.json`과 `function_app.py`가 없는 현재 트리에서 동작하지 않는 레거시
설정입니다. 애플리케이션은 터미널에서 다음처럼 실행합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; python -m uvicorn src.main:app --reload
```

예약 실행의 로컬 진입점은 다음과 같습니다. 실제 분석은 구성된 Foundry Hosted Agent를
호출하므로 필요한 환경 변수와 Azure 로그인이 있어야 합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; python -m src.scheduler
```

## 보안과 유지보수

- `mcp.json`은 PAT 값을 저장하지 않고 실행 시 password input으로만 받습니다.
- `.env` 또는 토큰을 workspace 설정에 하드코딩하지 않습니다.
- F5를 현재 런타임에 맞게 복구하려면 `tasks.json`, `launch.json`, `settings.json`, 권장 확장을
  함께 갱신하고 FastAPI 시작과 Hosted Agent 로컬 adapter를 별도 구성으로 구분해야 합니다.
- 문서의 터미널 명령은 프로젝트 규칙에 따라 항상 `.venv` 활성화 뒤 실행합니다.