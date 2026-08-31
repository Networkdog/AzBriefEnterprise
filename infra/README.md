# `infra`

[프로젝트 README](../README.md) > `infra`

AzBrief Enterprise의 Azure 인프라를 두 배포 단위로 나눠 보관합니다. 제품 전체 topology와
읽기 전용 Azure MCP Server는 identity와 lifecycle이 다르므로 같은 template에 합치지 않습니다.

## 구성

| 경로 | 목적 |
|---|---|
| [`enterprise/`](enterprise/README.md) | Foundry, Container App/Job, network, state, email, monitoring의 원본 Bicep |
| [`azbrief-enterprise-deploy.json`](azbrief-enterprise-deploy.json) | Deploy to Azure 버튼이 사용하는 compiled ARM template |
| [`azbrief-enterprise.parameters.example.json`](azbrief-enterprise.parameters.example.json) | 비밀값 없는 deployment parameter 예시 |
| [`azure-mcp-server/`](azure-mcp-server/README.md) | Azure MCP specialist가 호출하는 별도 Entra 인증 read-only Container App |

## 사용 예시

제품 template을 Bicep source에서 다시 생성합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; az bicep build --file infra\enterprise\main.bicep --outfile infra\azbrief-enterprise-deploy.json
```

Azure MCP template은 별도로 정적 compile할 수 있습니다.

```powershell
& .\.venv\Scripts\Activate.ps1; az bicep build --file infra\azure-mcp-server\infra\main.bicep --stdout
```

## Source of truth

- `enterprise/main.bicep`이 제품 topology의 원본입니다. checkpoint와 immutable analysis archive
  container도 여기서 함께 정의하며 compiled JSON을 손으로 수정하지 않습니다.
- Azure MCP는 `azure-mcp-server/infra/main.bicep`과 그 module이 원본입니다.
- `enterprise/main.json`은 현재 CI나 Deploy 버튼이 참조하지 않는 별도 snapshot입니다. 배포
  산출물로 사용하지 말고, 필요성이 확인될 때 원본/생성 경로를 정리해야 합니다.
- parameter example에는 실제 client secret, API key, 구독자 개인정보를 넣지 않습니다.

## 검증

CI는 Enterprise Bicep을 임시 JSON으로 compile한 뒤 추적 중인
`azbrief-enterprise-deploy.json`과 byte-level drift를 검사합니다. 정적 compile은 schema와 타입
오류를 잡지만 subscription policy, quota, RBAC 전파, private DNS 연결까지 증명하지는 않습니다.
실제 배포 전에는 대상 환경에서 what-if/validate를 별도로 수행해야 합니다.
