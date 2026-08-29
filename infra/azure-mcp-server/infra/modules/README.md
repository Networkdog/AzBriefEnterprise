# `infra/azure-mcp-server/infra/modules`

[프로젝트 README](../../../../README.md) > [Azure MCP IaC](../README.md) > `modules`

Azure MCP Server 배포의 resource ownership을 기능별 Bicep module로 분리합니다.

## Module

| 파일 | 책임 |
|---|---|
| [`aca-infrastructure.bicep`](aca-infrastructure.bicep) | MCP Container App, environment, system-assigned identity, ingress, scale, probe |
| [`application-insights.bicep`](application-insights.bicep) | 기존 connection string 재사용 또는 새 App Insights 생성 |
| [`entra-app.bicep`](entra-app.bicep) | Entra application/service principal, identifier URI, delegated scope와 app role |
| [`foundry-role-assignment-entraapp.bicep`](foundry-role-assignment-entraapp.bicep) | Foundry project identity에 MCP application role 부여 |
| [`subscription-reader.bicep`](subscription-reader.bicep) | MCP Container App identity에 대상 subscription Reader 부여 |

## Runtime 안전성

`aca-infrastructure.bicep`은 다음 인수를 함께 적용합니다.

```text
--transport http
--outgoing-auth-strategy UseHostingEnvironmentIdentity
--mode all
--read-only
--namespace group
--namespace resourcehealth
--namespace advisor
```

TLS는 외부 Container Apps ingress가 종료하며 container는 내부 `8080`에서 수신합니다. forwarded
header/HTTPS redirect 관련 environment flag를 다른 hosting 환경에 그대로 옮기면 안 됩니다.

## 사용 예시

개별 module을 직접 배포하지 말고 root에서 output과 cross-scope role assignment까지 검증합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; az bicep build --file infra\azure-mcp-server\infra\main.bicep --stdout
```

## 불변식

- read-only 보장은 `--read-only`, namespace allow-list, subscription `Reader`를 모두 유지할 때
  성립합니다.
- Entra app-role 문자열의 이름만 보고 Azure mutation 권한으로 해석하지 않습니다. 실제 Azure
  권한은 MCP process mode와 managed identity RBAC가 제한합니다.
- Contributor, secret data-plane role, 인증 우회 flag를 추가하지 않습니다.
- Container App의 startup/liveness probe와 single active revision을 유지해 호출자가 불완전한
  revision으로 연결되지 않게 합니다.