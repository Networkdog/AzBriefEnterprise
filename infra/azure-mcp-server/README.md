# `infra/azure-mcp-server`

[프로젝트 README](../../README.md) > [`infra`](../README.md) > `azure-mcp-server`

Impact Prompt Agent가 실제 tenant 상태를 조회할 때 사용하는 **별도 Azure MCP Server**의 `azd`
배포 단위입니다. AzBrief Container App이 제공하는 `/mcp` 제어면과 목적이 다릅니다.

| MCP surface | 대상 | 노출 기능 |
|---|---|---|
| 이 디렉터리의 Azure MCP Server | Impact Agent | resource group, Resource Health, Advisor read-only evidence |
| [`src/mcp_server.py`](../../src/mcp_server.py) | 운영자/외부 MCP client | 최근 update, Hosted 분석, digest 실행 상태 |

## 구성

- [`azure.yaml`](azure.yaml): Bicep provider와 deployment output mapping
- [`infra/`](infra/README.md): Container App, Entra application, App Insights, RBAC Bicep
- `.azure/`: 로컬 `azd` environment state이며 Git에 포함하지 않음

## 사용 예시

리소스를 만들지 않고 Bicep 구문과 type을 확인합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; az bicep build --file infra\azure-mcp-server\infra\main.bicep --stdout
```

`azure.yaml`의 output은 서버 URL, Entra client ID/identifier URI, Container App identity를 후속
project connection과 검증 단계에 전달합니다. output 값은 코드에 복사하지 말고 배포 환경에서
해석합니다.

## 보안 불변식

- 공식 Azure MCP image는 검증한 version으로 pin하며 `latest`를 사용하지 않습니다.
- incoming Entra 인증을 유지하고 project managed identity에 필요한 app role만 부여합니다.
- 서버 identity는 대상 subscription의 `Reader`이며 Contributor가 아닙니다.
- runtime arguments의 `--read-only`와 좁은 namespace allow-list를 제거하지 않습니다.
- image version을 올린 뒤 direct tool schema와 실제 read-only inventory 호출을 검증한 다음 Impact
  Agent와 Hosted Agent의 새 version을 발행합니다.
