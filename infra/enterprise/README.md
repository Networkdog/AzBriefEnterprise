# `infra/enterprise`

[프로젝트 README](../../README.md) > [`infra`](../README.md) > `enterprise`

[`main.bicep`](main.bicep)은 AzBrief Enterprise 제품 topology의 단일 원본입니다. Foundry 분석
계층, Container Apps 제어면, 내구성 상태, 이메일, 관측성과 세 가지 network isolation profile을
하나의 resource-group deployment로 구성합니다.

## 배포되는 주요 경계

| 영역 | 리소스와 역할 |
|---|---|
| Foundry | AI Services account, project, model deployment, VNet mode의 project capability host |
| Control plane | 같은 image를 쓰는 Container App(API/Admin/MCP)과 Container Apps Job(schedule) |
| State | Entra-only Storage account의 checkpoint container, private immutable archive container, Key Vault secret reference |
| Delivery | Communication Services와 Email Services managed domain |
| Observability | Log Analytics와 Application Insights |
| Identity | App/Job용 user-assigned identity와 resource별 최소 범위 role assignment |
| Network | `vnetInjection`, `perimeter`, `public` 중 하나의 경계 |

Prompt Agent version과 Hosted Agent version은 data-plane 객체이므로 이 Bicep이 만들지 않습니다.
인프라 배포 뒤 [`scripts/provision_foundry_agents.py`](../../scripts/provision_foundry_agents.py)와
루트 [`azure.yaml`](../../azure.yaml)이 각각 별도 lifecycle을 담당합니다. Bicep output은
coordinator, Resource Graph, Azure MCP, Azure API, report writer, quality reviewer의 고유 이름과
Hosted 배포용 `azd env set` 명령을 제공합니다.

## 사용 예시

문법과 resource schema를 검사하며 compiled template을 갱신합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; az bicep build --file infra\enterprise\main.bicep --outfile infra\azbrief-enterprise-deploy.json
```

변경 전에는 [`../azbrief-enterprise.parameters.example.json`](../azbrief-enterprise.parameters.example.json)
에서 공개 가능한 parameter 모양만 확인합니다. 실제 secret은 Key Vault 또는 안전한 deployment
입력으로 전달합니다.

## 중요한 parameter 묶음

- Compute: `containerImage`, `minReplicas`, `maxReplicas`, `scheduleCronExpression`,
  `jobReplicaTimeoutSeconds`
- Foundry: `foundryLocation`, model 이름/SKU/capacity, `foundryHostedAgentName`
- Network: `networkIsolationMode`, 기존 VNet 또는 세 subnet prefix, `internalIngressOnly`
- Admin: Entra client ID/secret과 `adminAllowedPrincipals`가 모두 있어야 활성화
- Archive: 같은 Entra app을 쓰며 `archiveAllowedPrincipals` 또는 Admin allow-list가 있어야 활성화
- Delivery: `subscribers`, 기본 recipient, Communication Services data location

## 불변식

- 기본 network mode는 create-time 제약이 있는 `vnetInjection`입니다. 기존 public Foundry account를
  in-place로 전환할 수 있다고 가정하지 않습니다.
- App과 Job은 같은 image와 user-assigned identity를 쓰지만 entry point가 다릅니다. image rollout은
  둘을 함께 갱신합니다.
- `RUN_TIME_BUDGET_S`는 Job replica timeout보다 짧아야 미완료 항목을 다음 실행으로 넘길 수 있습니다.
- Storage shared-key와 Foundry local auth를 켜서 편의상 우회하지 않습니다.
- Admin은 Entra 설정과 allow-list가 모두 없으면 닫혀 있어야 합니다.
- Archive container는 public access가 없고 App/Job UAMI만 REST data plane으로 읽고 씁니다.
- Archive가 구성되면 저장 성공이 digest와 checkpoint보다 먼저여야 합니다.
- control-plane identity에 Hosted Agent의 tenant evidence 권한을 대신 부여하지 않습니다.
