# `infra/enterprise`

[프로젝트 README](../../README.md) > [`infra`](../README.md) > `enterprise`

[`main.bicep`](main.bicep)은 AzBrief Enterprise 제품 topology의 단일 원본입니다. Foundry 분석
계층, Container Apps 제어면, 내구성 상태, 이메일, 관측성과 세 가지 network isolation profile을
하나의 resource-group deployment로 구성합니다.

## 배포되는 주요 경계

| 영역 | 리소스와 역할 |
|---|---|
| Foundry | AI Services account, project, model deployment, VNet mode의 project capability host |
| Control plane | 같은 image를 쓰는 Container App(API/Admin/Archive/MCP)과 인수 전 Manual 상태인 Container Apps Job |
| State | 공유 Entra-only Storage Account 하나의 `azbrief-state`, `azbrief-archive` 컨테이너. Key Vault secret reference는 별도 유지 |
| Evaluation | 같은 계정의 Foundry 관리 컨테이너, AAD connection, project identity의 필수 계정 범위 Blob Data Owner |
| Delivery | Communication Services와 Email Services managed domain |
| Observability | 공용 Log Analytics, Entra 전용 Application Insights, `AzBriefFailures_CL`과 Direct DCR |
| Identity | App/Job용 user-assigned identity, Foundry project system identity와 resource별 최소 범위 role assignment |
| Network | `vnetInjection`, `perimeter`, `public` 중 하나의 경계; 상태·archive·평가는 Blob endpoint 하나 공유 |

App과 Job에는 `ADMIN_READINESS_*` expected inventory가 동일하게 주입됩니다. 이 값은 Foundry
계정/Project/model, specialist Agent 이름, 공용 Container Apps Environment 하나와 App 두 개, Scheduler Job과
기반 Azure resource 목록만 담습니다. Agent definition이나 secret은 담지 않으며 `/admin`의 live
readiness checklist가 실제 ARM/Foundry 상태와 비교할 때 사용합니다.
List/map 형태의 값은 `base64(string(...))`으로 전달합니다. `az containerapp update`가 JSON quote를
제거해 startup을 깨뜨릴 수 있으므로 CLI rollout도 같은 Base64 JSON 계약을 따라야 합니다.

Prompt Agent version과 Hosted Agent version은 data-plane 객체이므로 이 Bicep이 만들지 않습니다.
인프라 배포 뒤 [`scripts/provision_foundry_agents.py`](../../scripts/provision_foundry_agents.py)와
루트 [`azure.yaml`](../../azure.yaml)이 각각 별도 lifecycle을 담당합니다. Bicep output은
coordinator, Resource Graph, Azure MCP, Azure API, report writer, quality reviewer의 고유 이름과
대상 tenant/subscription/project를 담은 비밀 값 없는 `customerSetup` 계약을 제공합니다.
v3는 Environment ID, workload profile, Application Insights 이름과 선택적인 failure-log DCR
binding도 포함합니다. 후속 MCP 배포는 이를 재사용하며 별도 Environment나 관측 리소스를 만들지
않습니다. App/Job UAMI는 DCR에 자동으로 쓸 수 있고, Hosted Agent 전용 identity에는 게시 후
`Monitoring Metrics Publisher`를 같은 DCR 범위로 별도 부여합니다. MCP 콘솔 로그는 같은
Workspace로 수집하지만 현재 image의 직접 Application Insights 추적·메트릭은 끕니다.
[setup_customer.ps1](../../scripts/setup_customer.ps1)이 이 값을 읽어 고객별 azd 환경을 구성하며,
`configureHostedAgentCommand`는 이 도구의 `Configure` 단계를 가리킵니다. Hosted 이름은
`FOUNDRY_HOSTED_AGENT_NAME`으로 manifest에 전달하고 여섯 전문가 이름은 각각 개별 설정으로
기록합니다. 출력의 셸 명령 문자열을 자동 실행하거나 여러 `NAME=VALUE`를 한 번의 `azd env set`에
전달하지 않습니다. [고객 가이드](../CUSTOMER_DEPLOYMENT.md)가 새 설치의 기준 절차입니다.

## 사용 예시

문법과 resource schema를 검사하며 compiled template을 갱신합니다.

```powershell
& .\.venv\Scripts\Activate.ps1; az bicep build --file infra\enterprise\main.bicep --outfile infra\azbrief-enterprise-deploy.json
```

변경 전에는 [`../azbrief-enterprise.parameters.example.json`](../azbrief-enterprise.parameters.example.json)
에서 공개 가능한 parameter 모양만 확인합니다. 실제 secret은 Key Vault 또는 안전한 deployment
입력으로 전달합니다.

## 중요한 parameter 묶음

- Compute: `containerImage`, `minReplicas`, `maxReplicas`, 보호된 기본 일정인
  `scheduleCronExpression`, 일정 확인 주기인 `scheduleDispatcherCronExpression`,
  `jobReplicaTimeoutSeconds`, 초기값 1인 `maxConcurrentAnalyses`, 기본 false인 `enableScheduledRuns`
- Registry: `containerRegistryServer`, 기본 `ManagedIdentity`인 `containerRegistryAuthMode`.
  외부 테넌트 ACR은 `Credentials`와 `containerRegistryUsername`, secure
  `containerRegistryPassword`를 사용하며 고객사 Key Vault의 같은 비밀 참조를 App/Job에 연결
- Foundry: `foundryLocation`, model 이름/SKU/capacity, `foundryHostedAgentName`
- Network: `networkIsolationMode`, 기존 VNet 또는 세 subnet prefix, `internalIngressOnly`
- Admin: Entra client ID/secret과 `adminAllowedPrincipals`가 모두 있어야 활성화
- Archive: 같은 Entra app을 쓰며 `archiveAllowedPrincipals` 또는 Admin allow-list가 있어야 활성화
- Delivery: `subscribers`, 기본 recipient, Communication Services data location

## 불변식

- 기본 network mode는 create-time 제약이 있는 `vnetInjection`입니다. 기존 public Foundry account를
  in-place로 전환할 수 있다고 가정하지 않습니다.
- VNet 주입에서는 Foundry와 배포 VNet의 리전이 같아야 합니다. 기본 Portal 폼은 같은 리전으로
  바인딩하며, 기존 VNet을 사용하는 경우에도 해당 리전과 subnet 위임을 사전에 확인합니다.
- App과 Job은 같은 image와 user-assigned identity를 쓰지만 entry point가 다릅니다. image rollout은
  둘을 함께 갱신합니다.
- 외부 ACR의 비밀번호는 고객사 Key Vault에만 저장하고 `customerSetup`이나 앱 환경 변수로
  전달하지 않습니다. 출력에는 인증 모드와 일반 Key Vault 주소만 추가합니다. 고객사 ID의
  테넌트를 바꾸거나 외부 ACR에 직접 역할을 부여하지 않으며, 네트워크와 실제 pull은 별도 검증합니다.
- 준비용 hello-world는 80 포트·`/`, AzBrief는 8000 포트·`/health`를 사용합니다. Bootstrap 이미지는
  `enableScheduledRuns=true`여도 정기 실행을 켜지 않습니다. 새 설치는 인수 전까지 Job이 수동입니다.
- Scheduler Job의 cron은 분석 일정 자체가 아니라 내구성 Admin 구성의 만기 슬롯을 확인하는
  디스패처 주기입니다. 실제 기본 분석 cron은 `SCHEDULE_CRON_EXPRESSION`으로 전달됩니다.
- `RUN_TIME_BUDGET_S`는 Job replica timeout보다 짧아야 미완료 항목을 다음 실행으로 넘길 수 있습니다.
  기본적으로 한 시간의 여유를 두며, 짧은 timeout에서도 음수가 되지 않도록 60초 하한을 적용합니다.
- Storage shared-key와 Foundry local auth를 켜서 편의상 우회하지 않습니다.
- Admin은 Entra 설정과 allow-list가 모두 없으면 닫혀 있어야 합니다.
- App/Job UAMI의 Blob Data Contributor는 `azbrief-state`와 `azbrief-archive` 각각에 부여하며
  계정 전체에는 부여하지 않습니다. Feedback은 기존처럼 상태 컨테이너 안에 보관합니다.
- Foundry evaluation artifact는 같은 계정의 서비스 관리 컨테이너에 기록합니다. 기존
  `steval...` 연결 이름은 계정 이름이 아닌 호환용 별칭입니다. Project identity의 계정 범위
  Blob Data Owner는 유지되므로 컨테이너 분리를 Foundry 관리자에 대한 강한 격리로 설명하지 않습니다.
- VNet mode는 Blob Private Endpoint 하나, perimeter mode는 storage NSP association 하나를
  사용합니다. 평가를 위해 Storage public access를 열지 않습니다.
- 기존 2계정 배포는 자동 통합되지 않습니다. Incremental 배포는 이전 계정·PE·광범위한 역할을
  제거하지 않으며, 데이터·참조·RBAC 이전은 [승인된 별도 절차](../CUSTOMER_DEPLOYMENT.md#single-storage-account)가 필요합니다.
- Archive가 구성되면 저장 성공이 digest와 checkpoint보다 먼저여야 합니다.
- control-plane identity에 Hosted Agent의 tenant evidence 권한을 대신 부여하지 않습니다.
