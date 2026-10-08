# KT 전용 프라이빗 인프라 템플릿

[인프라 색인](../README.md) · [Network Requirements](../NETWORK_REQUIREMENTS.md) ·
[Bicep 원본](main.bicep) · [배포용 ARM](azuredeploy.json) ·
[Portal UI](createUiDefinition.json) · [입력 예시](main.parameters.example.json) ·
[사전검사·배포 CLI](../../scripts/deploy_kt.py) ·
[다른 Agent에도 적용하는 KT 배포 Skill](../../.github/skills/kt-private-deployment/SKILL.md)

기존 VNet을 사용하는 **별도 인프라 초기 배포(bootstrap) 프로필**입니다.
일반 Enterprise 템플릿, 개발 환경, 기존 VNet을 교체하지 않습니다.
요청의 “Capacity Host”는 Foundry **Capability Host**로 구현했습니다. 모델의 PTU/예약 용량을
구매하는 설정이 아닙니다.

이 배포에서 확인된 subnet 선택, Container Apps deny Policy, AVM 로그 판별형, 중복 Private
DNS link, bootstrap 및 격리망 업데이트 교훈은 위 KT 배포 Skill의 범용 체크리스트에 기록합니다.
KT에 다른 Agent를 추가할 때도 해당 Skill을 먼저 적용하십시오.

> **범위:** 프라이빗 Foundry Standard Agent Setup과 GHCR의 실제 AzBrief 제어면 이미지를
> 배포합니다. 운영 설치 완료를 의미하지 않습니다. 모델 배포,
> 여섯 Prompt Agent 및 Hosted Agent 게시, Job·Entra 인증·메일·스케줄 설정은
> [애플리케이션 전환](#애플리케이션-전환) 단계입니다. `ktFoundation.applicationReady`는
> 의도적으로 `false`이며 일반 `customerSetup` 출력과 호환되지 않습니다.
> 초기 API 키는 자동 생성·저장하며 인증은 유지합니다. Entra 설정 전 Admin/Archive/Feedback은 비활성 상태로 유지합니다.

> **임시 수동 PE 진단:** `deployContainerAppsPrivateEndpoint=false`가 기본값입니다.
> Container Apps Environment와 앱은 생성하지만 그 Environment용 PE 및 DNS zone group
> (`kt-private-endpoint-4`)은 생성·갱신하지 않습니다. Foundry·Storage·Cosmos DB·Search의
> 네 PE는 그대로 생성합니다. 자동 생성을 재개하려면 명시적으로 `true`를 지정하거나 Portal
> 옵션을 선택하십시오. Incremental 배포는 이전에 만든 PE를 삭제하지 않습니다.

## 배포 구성

| 항목 | KT 설정 |
|---|---|
| VNet | 지정한 기존 VNet만 사용. VNet 리소스나 전체 subnet 목록을 PUT하지 않음 |
| Foundry | `AIServices/S0`, 시스템 ID, 로컬 키 인증 비활성, Public Network Access 비활성 |
| Foundry inbound | `account` Private Endpoint → 선택한 Private Endpoint subnet |
| Foundry outbound | 계정 최초 생성 시 `scenario=agent`, 선택한 Foundry 전용 subnet으로 VNet Injection |
| Capability Host | 1단계는 account host 준비까지만. 2단계에서 프로젝트·연결·project `agents` host를 함께 생성. account host 중복 생성 없음 |
| 공유 Storage Account | **StorageV2 / Standard_LRS 계정 하나**, Blob PE 하나, Public Network Access 비활성, Entra 전용 |
| Blob 컨테이너 | 앱용 `azbrief-state`, `azbrief-archive`와 Foundry가 만드는 agent/system 컨테이너를 논리적으로 분리 |
| Cosmos DB | NoSQL, 단일 리전 Serverless, `Sql` PE, Public Network Access·로컬 키 인증 비활성 |
| AI Search | Basic, replica 1 / partition 1, `searchService` PE, Public Network Access·로컬 키 인증 비활성 |
| Container Apps Environment | Workload Profiles 유형, **Consumption** 프로필, 전용 subnet 주입, Public Network Access 비활성 |
| Container Apps inbound | 내부 Environment 유지. `managedEnvironments` PE는 기본 수동 생성, 옵션으로 자동 생성 재개 |
| Container App | **0.25 vCPU / 0.5 GiB**, 최소 replica 0 / 최대 1, HTTPS, GHCR AzBrief 고정 digest, 내부 포트 **8000**, readiness **`/health`** |
| 초기 인증 | API 키 자동 생성 및 기존 키 재사용. 공개 GHCR은 PAT 없이 다운로드. Entra 설정 전 Admin/Archive는 비활성 |
| Application Insights | **배포하지 않음**. Foundry 연결, 계측 설정, publishing 역할도 생성하지 않음 |
| Log Analytics | 기존 workspace ID를 선택적으로 연결. 새 workspace·DCR·AMPLS는 생성하지 않음 |
| 추가 비용 리소스 | 새 VNet, 전용 D4 노드, NAT Gateway, Firewall, ACR, 모델 배포, scheduler Job, 메일 서비스는 생성하지 않음 |

Foundry 프로젝트 ID에는 공유 storage의 Storage Account Contributor·Blob Data Contributor,
Cosmos DB Operator, Search Service Contributor·Index Data Contributor를 먼저 부여합니다.
host가 컨테이너를 만든 뒤 `<workspaceId>-azureml-agent`에 Blob Data Owner를,
Cosmos의 `enterprise_memory`에 Cosmos-native Data Contributor를 부여합니다. 현재 Responses의
`agent-definitions-v1`·`run-state-v1`도 포함하는 DB 범위이며 Classic 컨테이너 세 개만 지정하지 않습니다.
프로젝트 이름이 아닌 `project.properties.internalId`로 workspace GUID를 계산합니다.

Container App 제어면 UAMI와 Hosted Agent의 **운영 역할·범위는 배포 후 운영자가 직접 부여**합니다.
템플릿은 이들 ID에 Blob 또는 Foundry 운영 역할을 자동 부여하지 않습니다.
위의 프로젝트 ID 권한은 BYO 초기화·데이터 저장에 필요한 별도 권한이므로 유지합니다.
Foundry의 필수 계정 권한 때문에 **프로젝트 ID가
canonical archive를 변경할 수 없다는 격리는 더 이상 제공하지 않습니다**. AzBrief 전용 계정
안의 동일한 신뢰 경계로 승인된 환경에 사용하며, 처리량·중복성·네트워크와 계정 장애 범위도 공유합니다.
Blob·컨테이너 soft delete는 기존 7일 설정을 유지하며, 공유 계정의 Foundry 컨테이너에도 적용됩니다.
실제 역할·scope 선택은 [배포 후 수동 운영 권한](#배포-후-수동-운영-권한)을 참고하십시오.

## GHCR 이미지와 초기 인증

README의 KT 배포 버튼은 다음 이미지를 사용하는 ARM/UI 페어를 엽니다.

```text
ghcr.io/networkdog/azbriefenterprise@sha256:6d8fe1e237110318344f5786602b5105c6e662f6a45186dcfc8bc6cb8bd2aaa3
```

- [GitHub 패키지](https://github.com/users/Networkdog/packages/container/package/azbriefenterprise)
  · 소스 revision `7bf1983b4d4c8558c3105342206b86757d367558`
  · 태그 `sha-7bf1983b4d4c8558c3105342206b86757d367558`.
- `linux/amd64`, 비-root `appuser`, `uvicorn src.main:app`, 포트 8000입니다.
  호환을 위해 매개변수 이름 `bootstrapImage`는 유지하지만 값은 더 이상 hello-world가 아닙니다.
- **2026-10-06 검증:** 패키지 Public 상태와 자격 증명 없는 전체 다운로드를 확인했습니다.
  고정 manifest, config 및 8개 layer의 SHA-256이 일치했고 앱 소스 103개도 위 revision과
  일치했습니다. 이는 개발 환경에서의 검증이며 KT 고객망의 egress·이미지 pull 성공을 보증하지 않습니다.
- 기본 `containerRegistryAuthMode=Anonymous`는 PAT 입력 없이 다운로드하며 registry 자격
  증명과 pull-token secret을 만들지 않습니다. 템플릿 선택만으로 패키지가 공개되지는 않습니다.
  **Public 전환과 고정 digest의 익명 layer 다운로드를 확인한 뒤 배포하십시오.**
  GitHub의 Public 전환은 되돌릴 수 없으므로 이미지의 비밀·고객 데이터 포함 여부를 먼저 확인합니다.
- 비공개 접근이 필요한 경우에만 `Credentials`와 패키지 읽기 권한이 있는 사용자의 이름,
  **PAT(classic), `read:packages`**를 지정합니다. 게시용 토큰을 배포하지 마십시오. 토큰은
  `ghcr-pull-token` App secret으로만 전달합니다. Azure 관리 ID는 GHCR의 로그인 수단이 아닙니다.
- Portal에서는 API 키를 입력하지 않습니다. 신규 앱은 ARM의 secure 기본값으로 **64자리
  무작위 키**를 생성하고 `orchestrator-api-key` App secret에 저장합니다. `API_KEY`는 계속
  `secretRef`로 연결되므로 분석 API와 MCP 인증은 유지됩니다. Key Vault는 추가하지 않습니다.
- Portal/CLI는 ARM 인벤토리로 `reuseExistingApiKey`를 결정합니다. 기존 앱은 `listSecrets`로
  저장된 키를 그대로 재사용하며 조회 실패·키 누락을 새 키로 대체하지 않습니다. 배포자에게
  `Microsoft.App/containerApps/listSecrets/action` 권한이 필요합니다. 키는 Portal 입력·배포 출력에
  노출하지 않습니다. 운영 API 호출에 필요할 때만 권한 있는 관리자가 App Secret을 조회하십시오.
- Portal은 조회 실패·미완료 페이지·비소유 앱을 차단합니다. 같은 앱에 대한 배포는 직렬로
  실행하고, raw ARM을 직접 호출할 때는 최신 인벤토리를 확인하여 `reuseExistingApiKey`를
  명시해야 합니다. CLI에서는 이 값을 입력하지 않습니다. 키가 없는 순정 hello-world 전환은
  CLI를 사용합니다. 선택적 초기 `apiKey`는 공백 없는 32~256자이며 기존 앱의 키 회전 수단이 아닙니다.
- `foundryHostedAgentName`과 배포 프로젝트 endpoint, 제어면 UAMI, archive/checkpoint 경로를
  앱에 연결합니다. 이름을 설정하는 것은 Hosted Agent를 게시하거나 검증하는 작업이 아닙니다.
  `/`는 AzBrief JSON을 반환하지만 `/admin`, `/archive`는 후속 Entra/허용 목록 설정 전까지
  404입니다. `/health` 응답만으로 전체 운영 준비가 완료됐다고 판단하지 마십시오.
- 기존 순정 hello-world 앱은 같은 RG·이름으로 전환할 수 있습니다. CLI는 이미지가 같더라도
  UI 활성화, 추가 환경 변수·secret, 실행 명령 또는 운영용 replica/리소스 설정이 있는 앱에
  초기 비활성 설정을 덮어쓰지 않습니다. 운영 전환 이후에는 foundation 재배포를 사용하지 않습니다.

GHCR도 외부 레지스트리입니다. 격리망에서는 `ghcr.io`와 이미지 다운로드 시 사용하는 GitHub
Packages 저장소 엔드포인트의 HTTPS/DNS 경로를 별도로 승인해야 합니다. GHCR 선택만으로
인터넷 차단을 우회하지 못하며 ACA 플랫폼의 필수 egress도 그대로 필요합니다.
출발지별 목적지·포트·적용 조건, 사설 DNS 및 미확정 사항과 인수 절차는
[Network Requirements](../NETWORK_REQUIREMENTS.md)의 **3.1 최초 요청안**을 기준으로
고객사에 요청하고, 3.2/3.3의 추가 출처·메일·관측 기능은 실제 사용 시 별도 승인하십시오.
커뮤니티 목적지를 제외하려면 기본 활성인 보강 기능을 Hosted runtime에서 명시적으로
꺼야 합니다. 문서 목록을 줄여도 실행 설정이 자동으로 바뀌지는 않습니다.
고객 구독에 ACR을 만들지는 않습니다. 이번 이미지는 개발 ACR의 원격 빌더에서 만들고
GitHub Actions 없이 GHCR로 복사했습니다. 빌드 컨텍스트에는 공개 revision의
Dockerfile·requirements·src만 넣었으며 로컬 비밀·로그·데이터는 포함하지 않았습니다.
후속 릴리스도 새 소스 태그로 게시하고 실제 확인한 digest로 Bicep을 갱신·컴파일하십시오.
GitHub 코드 변경이나 이미지 게시만으로 기존 Container App이 자동 업데이트되지는 않습니다.

기존 2계정 환경은 [저장소 통합 절차](../CUSTOMER_DEPLOYMENT.md#single-storage-account)를 먼저
승인해야 합니다. CLI는 이전의 `agentStorageAccountName`/`stateStorageAccountName` 입력과
선택 계정 외의 KT-profile 저장소가 남아 있는 경우를 차단합니다. 계정·데이터·역할을 자동으로
삭제하거나 옮기지 않습니다. 활성 Capability Host가 있으면 기존 Foundry backing 계정을
`storageAccountName`으로 유지하고 상태·archive 이전을 별도로 검증하십시오.
`ktFoundation.storageAccountResourceId`와 호환용 `agentStorageAccountResourceId`/
`stateStorageAccountResourceId`는 모두 같은 계정을 가리킵니다.

## 신규 리소스 기본 이름

Portal ARM 입력 화면과 CLI는 다음 이름을 미리 채웁니다. 기존 KT 프로필의
`<리소스약어>-azbrief-kt` 형식을 확장한 **수정 가능한 제안값**이며, KT 공식 명명 표준의
서비스별 접두사·환경·리전 코드가 확인된 것은 아닙니다. 확인되지 않은 환경이나 리전 코드는
추가하지 않았습니다. 실제 배포 전 KT의 승인된 명명 규칙에 맞게 검토하십시오.

| 입력 | 기본값 |
|---|---|
| `foundryAccountName` | `ai-azbrief-kt` |
| `storageAccountName` | `stazbriefkt` |
| `cosmosAccountName` | `cosmos-azbrief-kt` |
| `searchServiceName` | `srch-azbrief-kt` |
| `projectName` | `azbrief-kt` |
| `containerAppsEnvironmentName` | `cae-azbrief-kt` |
| `containerAppName` | `ca-azbrief-kt` |

Storage는 Azure 이름 제약에 따라 하이픈 없이 소문자·숫자만 사용합니다. 기존 project/App/
Environment 기본값은 유지하며, Private Endpoint는 `pe-<대상 리소스 이름>`, 제어면 ID는
`id-<Container App 이름>`으로 계속 파생합니다. 서비스가 정하는 DNS zone, Capability Host 및
컨테이너 이름은 바꾸지 않습니다. subnet 이름은 고정하지 않고 선택한 실제 이름을 그대로 사용합니다.

**기존 `virtualNetworkName`은 기본값 없이 직접 입력**합니다. 해당 VNet의 resource group과
location도 실제 값이 필요합니다. 기본 이름은 전역 가용성을 보장하거나 예약하지 않으므로
Foundry의 custom subdomain, Storage, Cosmos DB, Search 이름이 이미 사용 중이면 승인된 다른
이름으로 입력하십시오. Portal의 이름 필드와 parameter JSON에서 모두 덮어쓸 수 있으며
CLI도 명시한 이름을 그대로 사용합니다. 기존 배포에 재적용할 때는 원래 이름을 유지하십시오.

## 최소 서브넷과 기존 설정 보존

| 역할(이름 제한 없음) | 새로 만들 때의 최소 크기 | 위임 | 용도 |
|---|---|---|---|
| Private Endpoint subnet | **/28** — 16개 주소 중 Azure 예약 5개 제외 11개 | 없음 | 기본 네 PE 7개 IP, ACA PE 자동 생성 시 8개 IP 예산 |
| Foundry 전용 subnet | **/27** | `Microsoft.App/environments` | 하나의 Foundry 계정 전용 |
| Container Apps 전용 subnet | **/27** | `Microsoft.App/environments` | Container Apps Environment 전용 |

Foundry와 Container Apps는 위임 이름이 같아도 **같은 서브넷을 공유할 수 없습니다**.
Portal은 선택한 VNet의 실제 subnet 목록을 읽어 역할별 드롭다운으로 표시합니다. 고정된
`PESubnet`·`FoundrySubnet`·`ContainerAppsSubnet` 이름은 요구하지 않습니다. CLI에서는
`peSubnetName`, `foundrySubnetName`, `containerAppsSubnetName`으로 같은 매핑을 명시하며,
세 이름은 대소문자를 무시하고 서로 달라야 합니다.
Foundry의 `/24`는 운영 확장 권장값이며 여기서는 요청한 최소 `/27`을 사용합니다.
입력 CIDR은 KT 네트워크 관리자가 승인해야 합니다. 예시의 `10.70.*`는 실제 KT 주소가 아닙니다.
더 큰 기존 서브넷은 축소하지 않으며, 신규 서브넷에도 명시적으로 더 큰 CIDR을 줄 수 있습니다.
이 프로필의 사전검사는 IPv4 단일-prefix 서브넷을 대상으로 합니다.

자동 배포 IP 예산은 Foundry 최대 3개를 위한 여유, 단일 리전 Cosmos 2개, 공유 Blob·Search 각각
1개로 7개입니다. ACA PE 자동 생성 시 1개를 더해 8개이며, 수동 생성 시에도 해당 IP 여유를
별도로 확보하십시오. `/29`의 3개 usable IP로는 부족합니다. 실제 NIC 할당은 배포 후 확인합니다.
Cosmos 리전이나 storage 서비스 PE를 추가하면 예산을 다시 계산해야 합니다.

CLI는 매번 live inventory를 읽어 **없는 child subnet만** 생성하도록 플래그를 계산합니다.
기존 subnet의 NSG, UDR, delegation, prefix, endpoint policy를 재배포하지 않습니다.
맞지 않는 CIDR·위임, 다른 서비스가 점유한 전용 subnet, 주소 중첩, ACA 예약 주소는 중단 사유입니다.
선택한 Private Endpoint subnet은 필요한 새 PE에 대해 실제 IP 가용성을 조회합니다. 최대 256개 후보만 확인하며
충분한 주소를 확인하지 못하면 추측해서 진행하지 않습니다. 기존 PE는 대상·subnet·승인을 검사합니다.

**네트워크 변경 창을 독점해서 사용하십시오.** 사전 조회와 ARM 쓰기는 원자적 연산이 아니므로,
그 사이 다른 운영자가 같은 이름의 subnet을 만드는 동시 변경은 금지합니다.
`create*Subnet` 플래그를 직접 설정하거나 예전 사전검사 결과로 ARM을 실행하지 마십시오.
기존 `.env`나 azd 개발 환경은 읽지 않으며 default CLI 계정과 명시한 KT tenant/subscription이
다르면 중단합니다. 다른 프로필의 기존 서비스를 이름만으로 인수하지 않습니다.

## Private DNS 및 접속

다음 zone을 생성하고 기존 VNet에 등록 비활성 링크로 연결합니다. 기존 zone은
`existingPrivateDnsZoneIds`의 **zone 이름 → ARM ID** 매핑으로 재사용할 수 있습니다.
대상 RG의 같은 이름 zone은 자동 인식합니다. 다른 RG/구독의 중앙 DNS zone은 명시적으로
전달해야 하며, 이 경우에도 해당 VNet 링크를 미리 구성해야 합니다. 기존 zone/link는 수정하지 않습니다.

- `privatelink.cognitiveservices.azure.com`
- `privatelink.openai.azure.com`
- `privatelink.services.ai.azure.com`
- `privatelink.blob.core.windows.net`
- `privatelink.documents.azure.com`
- `privatelink.search.windows.net`
- `privatelink.<region>.azurecontainerapps.io`

자동 생성하는 각 PE의 zone group이 서비스 레코드를 연결합니다. ACA PE 자동 생성을 꺼도
위 zone 준비·재사용은 유지하므로 수동 PE의 DNS 연결에 사용할 수 있습니다. 수동 ACA PE의
생성·승인·레코드 연결과 실제 HTTPS 접근은 운영자가 별도로 확인해야 합니다.
중앙 DNS/온프레미스 DNS를 사용하면
KT DNS 담당자가 Azure Private DNS로의 전달·해석을 구성해야 합니다. 이 템플릿은 DNS 서버,
VPN, ExpressRoute, peering, NSG/UDR, outbound 방화벽을 변경하지 않습니다.
신규 subnet에도 NSG/UDR를 자동 생성하지 않으므로 KT 정책에 따른 검토·연결이 필요합니다.

Container Apps Environment는 `internal=true`인 내부 load balancer 환경이며 Public Network
Access도 `Disabled`입니다. App의 `ingressExternal=true`는 이 환경 안에서 ingress를
VNet/Private Endpoint 경로에 노출한다는 뜻이지 공용 인터넷 허용이 아닙니다. KT에 적용된
built-in Policy `d074ddf8-01a5-4b5e-a2b8-964aed452c0a`의 표시 이름은 Public Network Access를
언급하지만 실제 deny 조건은 `vnetConfiguration.internal`이 없거나 `false`인 경우입니다.
따라서 `internal=true`와 `publicNetworkAccess=Disabled`를 Environment 최초 `PUT`에 함께
포함하며 사후 PATCH로 전환하지 않습니다. bootstrap URL은 VNet/승인된 사설 경로에서
접근합니다. `applicationUrl`과 호환용 `bootstrapUrl`은 같은 App 주소입니다.

이미 `internal=false`로 생성된 Environment는 이 프로필에서 인수하지 않습니다. accessibility
level을 제자리에서 전환하지 말고, 앱·Private Endpoint·DNS·이미지의 롤백 정보를 기록한
유지보수 창에서 기존 foundation을 명시적으로 제거한 뒤 내부 Environment로 다시 생성합니다.
삭제는 자동화하지 않습니다. Portal 이름 단계는 같은 RG·이름의 기존 Environment를 Resource
Graph로 조회하고 `internal=true`, PNA `Disabled`, KT 소유권 태그가 아니면 ARM validation 전에
진행을 막습니다. **2단계에서 같은 RG·이름의 1단계 Environment를 재사용하는 것은 정상**이며,
세 조건을 만족하면 그대로 진행합니다. 기존 환경 감지는 오류가 아니라 안내로 표시하고,
이름이 같다는 이유만으로 삭제·재생성을 요구하지 않습니다. 이름을 변경해 일치 항목이 없으면
빈 결과에 `first()`를 적용하지 않고 비준수 일치 항목만 필터링합니다.

ARM에서 `internal=true`인 정상 환경도 Resource Graph `objectArray`의 `tobool(...)` 결과는
숫자 `1`로 반환될 수 있습니다. CreateUiDefinition의 `equals()`는 타입도 비교하므로
`equals(1, true)`는 `false`입니다. 조회 쿼리는 `tostring(tobool(...))`로 `"true"`/`"false"`를
반환하고 폼도 문자열 `'true'`와 비교합니다. 알 수 없거나 누락된 값은 내부 환경으로 간주하지
않습니다. 이전 폼에서 정상 환경이 차단됐다면 수정된 ARM/UI 페어를 게시한 뒤 배포 화면을
새로 열고 1단계와 같은 값을 입력합니다. 이 수정은 입력 검증만 바꾸며 기존 Azure 리소스를
변경하거나 실제 2단계 배포 성공을 보장하지 않습니다.

VNet injection만으로 모든 인터넷 egress가 차단되지는 않습니다. Foundry 플랫폼, Entra,
ARM, 이미지 다운로드, 패키지 빌드, Microsoft Learn/RSS 등의 필요한 outbound 경로와 DNS를
KT 정책에 맞게 승인해야 합니다. 리소스가 ARM에서 성공해도 이 경로가 막히면 운영할 수 없습니다.

## 사용 순서

### Portal 버튼

프로젝트 README의 KT 버튼은 GitHub `main` 브랜치의 [ARM](azuredeploy.json)과
[전용 CreateUIDefinition](createUiDefinition.json)을 함께 엽니다. 두 파일을 같은 소스 버전으로
게시하고 익명 접근이 가능하게 해야 합니다. 일반 Enterprise UI와 혼용하지 않습니다.

| 화면 | 제공 기능 |
|---|---|
| 기본 사항 | 배포 구독·RG·지역 선택, 프라이빗 bootstrap 범위 안내 |
| 기존 네트워크 | 같은 구독·지역의 기존 VNet 선택. 이름과 무관하게 역할별 기존 subnet을 선택하고 ARM 조회 CIDR·위임으로 후보 제한 |
| 리소스 이름 | 일곱 기본 이름과 호출할 Hosted Agent 이름 편집, Storage 단일 입력 및 다섯 PE 대상 이름 충돌 차단 |
| 이미지·인증·DNS·로그 | GHCR 익명 다운로드 기본값·선택적 읽기 토큰, API 키 자동 관리 안내, ACA PE 자동 생성 선택(기본 해제), Search/DNS/기존 Log Analytics 선택 |
| 단계·필수 확인 | 선택 요약, 기반/완료 단계 선택, 사전검사·소유권·비용·bootstrap 범위 동의 |

VNet을 새로 만드는 옵션은 없습니다. 선택한 subnet 이름은 배포 매개변수로 전달되고 CIDR은
드롭다운 설명으로만 표시되므로 사용자가 주소를 다시 입력하지 않습니다. ARM에서 읽은 단일
IPv4 prefix, RFC1918 범위, 최소 크기, 위임 및 Container Apps 예약 범위를 충족하는 subnet만
역할별 후보에 포함됩니다. 조회 실패·후보 없음은 진행 조건을 충족하지 못합니다.
`createPESubnet`, `createFoundrySubnet`, `createContainerAppsSubnet`은 항상 `false`를 전달하므로
**Portal에서는 준비된 서브넷만 재사용**합니다. 새 subnet이 필요하면 CLI에서 같은 역할별
이름과 CIDR을 명시합니다.
공식 `VirtualNetworkCombo`는 기존 VNet 안의 subnet 생성을 지원하지 않고 새 VNet도 제안하므로,
이 UI는 existing-resource selector와 read-only API control을 사용합니다.

기본 DNS 모드는 선택한 구독에서 Resource Graph로 VNet의 기존 `virtualNetworkLinks`를 조회합니다.
필수 namespace와 일치하는 zone은 RG와 이름에 관계없이 자동 재사용하고, 연결되지 않은
namespace만 배포 RG에 생성합니다. 이 방식은 같은 VNet에 동일하거나 겹치는 namespace의
zone을 두 번 연결하는 Azure 오류를 방지하며 기존 zone의 레코드를 복사하지 않습니다. 새
Private Endpoint의 zone group이 기존 zone을 참조하므로 Azure가 필요한 A 레코드를 그 zone에
추가합니다.

다른 구독이거나 배포 사용자에게 읽기 권한이 없어 자동 조회 범위 밖인 중앙 DNS는 기존
**리소스 그룹 ARM ID 하나**를 명시합니다. Foundry용 세 zone, blob/documents/search zone,
선택한 지역의 ACA zone이 그 RG에
모두 있어야 하며 기존 VNet 링크·권한을 별도로 확인합니다. CLI도 subscription의 기존 link를
자동 발견하며, 명시적 cross-subscription 또는 분산 구성은 `existingPrivateDnsZoneIds` 개별
매핑으로 override합니다. 로그 옵션은 기존 workspace만 연결하며 Application Insights나 오류
커스텀 테이블을 만들지 않습니다.
로그 연결을 선택하지 않으면 Container Apps Environment의 `appLogsConfiguration`을
`null`로 생략합니다. AVM `0.16.0`의 판별형은 `azure-monitor`와 `log-analytics`만
허용하므로 `destination: none`을 전달하지 않습니다.

수동 ACA PE 진단 중에는 **Container Apps Environment PE 자동 생성**을 선택하지 않습니다.
`ktFoundation.containerAppsPrivateEndpointRequested`는 자동 생성 선택값을 표시할 뿐 수동 PE의
존재나 정상 동작을 보증하지 않습니다. CLI는 수동 ACA PE가 없어도 계속 진행하며, 이미 존재하는
수동/실패 ACA PE의 상태를 읽거나 수정하지 않습니다. Environment 자체의 소유권·전용 subnet·
`internal=true`·PNA 비활성 검사는 그대로 유지됩니다. 기존 실패 PE 정리는 자동 실행하지 않습니다.

처음에는 기본 선택인 **1단계 — 네트워크·저장소·계정 기반**으로 실행합니다
(`deployCapabilityHost=false`). 이 단계는 **Foundry 프로젝트를 생성하지 않습니다.**
계정의 자동 Capability Host가 `Succeeded`인 것을 확인하고,
**같은 RG·이름·네트워크·DNS·이미지 인증 입력**으로 다시 열어
**2단계 — 프로젝트·연결·BYO host 구성**을 선택합니다. UI는 두 배포를 자동 실행하거나
account host 준비를 기다리지 않습니다.

2단계는 1단계의 Foundry 계정을 `existing` 리소스로만 참조합니다. 계정 AVM 모듈이나
`customSubDomainName`을 다시 제출하지 않으므로 정상적인 동일 이름 재사용은 전역 subdomain
가용성 검사로 차단되지 않습니다. 2단계에서 `CustomDomainInUse`가 발생한다면 이전 버전의
ARM/UI 조합을 사용 중인지 먼저 확인하십시오. 살아 있는 1단계 계정을 삭제하거나 purge하지
말고, 같은 source revision에서 생성된 `azuredeploy.json`과 `createUiDefinition.json`을
다시 게시하십시오. 직접 ARM을 호출할 때도 1단계는 명시적 또는 기본
`deployCapabilityHost=false`, 2단계만 `true`를 사용합니다.

| 단계 | 생성·갱신 범위 | 프로젝트 상태 |
|---|---|---|
| 1단계 | 기존 네트워크 기반, 사설 endpoint, Foundry 계정·자동 account host, 저장소, 초기 App와 제어면 ID | 새 프로젝트·연결·프로젝트 역할·BYO project host를 생성하지 않음 |
| 2단계 | Foundry 계정은 재배포하지 않고 `existing`으로 참조하며, 나머지 기반을 재사용해 프로젝트 → BYO 필수 권한·연결 → BYO host → 프로젝트 데이터 역할 순으로 구성 | 한 배포 안에서 BYO 구성을 완료. App·Hosted 운영 역할은 별도 수동 부여 |

프로젝트만 먼저 만든 상태로 두 단계 사이에 기다리면 기본 저장소용 host가 초기화되어
나중의 BYO 연결 추가가 거부될 수 있습니다. 따라서 2단계 전에 프로젝트를 수동으로 만들거나
Agent를 실행하지 마십시오. 1단계 출력의 `foundryProjectResourceId`와
`foundryProjectEndpoint`는 생성 예정 주소이며, `foundryProjectPrincipalId`는 빈 문자열입니다.
프로젝트 principal과 BYO host가 준비되는 시점은 2단계가 성공한 뒤입니다.
API 키는 기존 앱에서 자동 재사용하므로 다시 입력하지 않습니다.
Basics에서 기존 RG를 선택한 경우 앱 조회는 그 RG의 `Microsoft.App/containerApps` 목록
API를 사용합니다. 새 RG는 아직 존재하지 않는 상태의 404를 피하도록 기존의 RG 필터가 있는
일반 인벤토리 조회를 유지합니다. 배포 단계 번호로 조회 실패를 무시하지 않습니다.
앱 조회 검증은 ARM 응답의 `value`, `error`, `nextLink` 필드를 직접 사용합니다. 정상 응답의
빈 오류 필드는 실패로 간주하지 않습니다. `value`가 아직 없거나 실제 오류·다음 페이지가
있으면 계속 차단하며, 같은 이름의 비소유 앱과 기존 API 키를 덮어쓰지 않습니다.
실패 시에는 응답 수신 여부·ARM 오류 코드·다음 페이지 유무를 표시합니다. 오류 원문이나
서명된 다음 페이지 URL은 표시하지 않습니다. CLI에서 같은 API가 성공해도 포털 동작이
검증된 것은 아니므로 수정된 UI 정의를 게시한 뒤 새 배포 화면에서 확인해야 합니다.
단계를 자동 처리하고 점유자·실제 IP 여유·이름 소유권을 검사하려면 CLI를 사용하십시오.
운영 설정을 마친 환경에 초기 비활성 설정을 다시 적용하지 마십시오.

#### 기존 설치와 중단된 배포

프로젝트와 BYO host가 이미 정상이라면 CLI 사전검사로 연결 이름·대상 리소스 ID를 확인한 뒤
같은 설정으로 재배포할 수 있습니다. Incremental 1단계에서 프로젝트를 생략해도 기존
프로젝트나 데이터는 삭제되지 않습니다. 원래 API 키도 계속 재사용합니다.

이전 템플릿의 1단계가 프로젝트를 이미 만들었거나 2단계가 도중에 실패했다면 먼저 CLI
사전검사를 수행하십시오. 기본 자동 host(`aml_aiagentservice`), host 부재·미완료 또는
다른 저장소 연결은 자동 보정하지 않고 중단합니다. 특히 `past the window for adding
bring-your-own (BYO) connections`는 기다리거나 같은 요청을 반복해서 해결할 오류가 아닙니다.
기존 host 재생성은 Agent·대화·파일·벡터 상태를 영구적으로 고립시킬 수 있어 데이터 보존 여부와
별도 승인이 필요합니다. 템플릿·CLI에는 host 자동 삭제나 초기화 경로가 없습니다.

로컬 검사와 ARM what-if는 생성 순서·계획 검증입니다. 신규 고객의 실제 두 단계 배포,
RBAC 전파, BYO 데이터 읽기·쓰기 및 사설 경로 인수는 별도로 완료해야 합니다.

### CLI 사전검사와 배포

필요 조건:

1. **Azure public cloud**, 기존 VNet 및 대상 RG. VNet과 서비스의 subscription은 같아야 합니다.
   Foundry·VNet·ACA의 region도 같아야 하며 해당 리전의 Hosted/Standard Setup 지원을 확인합니다.
2. 새 Foundry 계정 이름. non-injected 계정을 이 방식으로 변경할 수 없습니다. 자동 삭제·purge 없음.
3. `Microsoft.CognitiveServices`, `Microsoft.App`, `Microsoft.Network`, `Microsoft.Storage`,
   `Microsoft.DocumentDB`, `Microsoft.Search`, `Microsoft.ManagedIdentity` 공급자 등록.
4. 배포/네트워크 작업과 RBAC 할당 권한. 예: 해당 범위 Contributor + Role Based Access Control
   Administrator. 기존 VNet과 중앙 DNS에 대해서도 필요한 join/link 권한을 별도로 확인합니다.
5. Azure CLI와 이 저장소의 Python 가상 환경. 읽기·빌드 작업도 가상 환경을 활성화합니다.
6. 공개 GHCR 이미지의 익명 다운로드 확인. 기본 예제에는 API 키·PAT이 없으며 ARM이 키를 생성합니다.
  비공개 접근을 명시한 경우에만 별도 읽기 전용 토큰이 필요합니다. 재배포에는 기존 App secret 조회 권한이 필요합니다.

입력 예시를 Git 제외 폴더에 복사해 기존 네트워크 placeholder와 CIDR을 실제 승인값으로 바꿉니다.
신규 리소스 이름은 위 기본값이 채워져 있으므로 KT 표준·전역 가용성을 확인한 뒤 필요하면 변경합니다.
기존 subnet을 그대로 재사용한다면 해당 CIDR 값을 비워도 됩니다. 값이 있으면 기존 prefix와
정확히 일치해야 합니다. 예시에는 실제 고객 ID, 비밀, 개발 환경 기본값이 없습니다.
선택적 초기 API 키나 private-registry 토큰을 넣은 parameter 파일은 Git 제외·접근 제한된
위치에서만 보관하고 채팅·로그에 붙여 넣지 마십시오. CLI는 입력한 secure-string 타입과 길이,
인증 모드, API 키와 토큰의 분리를 검사하고 오류/what-if 로그의 비밀을 마스킹합니다.
생략한 `apiKey`는 ARM이 평가하도록 두며 기본값 식을 실제 키 문자열로 전달하지 않습니다. 임시 ARM parameter 파일은 실패 시에도
삭제됩니다. 이 검사는 실제 GHCR 권한이나 격리망 연결성 검증을 대신하지 않습니다.

```powershell
& .\.venv\Scripts\Activate.ps1
Copy-Item infra\kt\main.parameters.example.json out\kt.parameters.json

# KT 값으로 파일을 수정하고, CLI 로그인/기본 구독도 KT 대상으로 선택한 뒤 실행합니다.
python -m scripts.deploy_kt `
  --tenant '<KT-tenant-GUID>' `
  --subscription '<KT-subscription-GUID>' `
  --resource-group '<KT-deployment-RG>' `
  --parameters out\kt.parameters.json `
  --mode preflight
```

동일한 인수로 `--mode validate`와 `--mode what-if`를 실행합니다. 결과의 정책·권한·quota·
예상 변경을 검토한 후에만 **`--mode deploy`**로 실행합니다. 기본 모드는 읽기 전용
`preflight`이며 Azure 리소스를 만들지 않습니다. 본 문서 작성 시 KT 구독에 배포하지 않았습니다.

`deploy`는 두 번의 Incremental 배포를 수행합니다.

1. 네트워크·서비스·PE/DNS·프로젝트·AAD 연결·사전 RBAC와 초기 비활성 상태의 AzBrief App을 생성.
2. 자동 account Capability Host를 10초 간격으로 최대 31회 확인. 다시 live inventory를 읽어 신규 subnet
   플래그를 해제한 뒤 project Capability Host와 생성된 컨테이너의 data 역할을 구성.
3. host 상태, 필수 subnet/자동 관리 PE의 존재·승인·binding, DNS 링크, 각 서비스의 Public Network Access
   비활성 설정과 각 PE의 `provisioningState=Succeeded`를 읽어 확인. 연결의 `Approved`만으로
   PE 생성 성공을 판단하지 않음. 비공개 데이터 경로의 실제 연결 검사는 아래 인수 단계에서 수행.

실패하면 비정상 종료하며 원래 오류를 출력합니다. RBAC 전파 지연은 권한과 기존 host 상태를
먼저 확인한 후 같은 입력으로 재실행합니다. host 연결을 다른 데이터 저장소로 변경하거나
host를 삭제·재생성하지 않습니다. 생성된 서비스를 자동 삭제하는 롤백도 없습니다.
ARM subnet flag는 재실행마다 다시 계산합니다. 임시 parameter 파일은 성공/실패 후 삭제합니다.
운영 설정을 마친 App에는 초기 비활성 설정을 재적용하지 않도록 CLI가 차단합니다.

## Bootstrap 이미지 다운로드 실패

아래는 **이전 MCR hello-world 기반 배포**의 오류 진단입니다. 현재 GHCR 이미지의
401/403은 패키지 공개 여부와 읽기 토큰을, 연결 오류는 GHCR egress를 먼저 확인합니다.

`kt-container-app`이 `template.containers.bootstrap.image`를 invalid로 표시하면서
`Get "https://mcr.microsoft.com/v2/": EOF`를 반환하면, 우선 **레지스트리 접속·이미지 pull
경로의 실패**로 분류합니다. 이 메시지만으로 이미지 이름이 틀렸다거나 PE 오류가 원인이라고
판단하지 않습니다. `EOF`는 응답을 정상적으로 읽기 전에 연결이 끝났다는 뜻이며 방화벽,
TLS 검사/프록시, DNS·라우팅 또는 일시적인 서비스 연결 문제가 원인인지 따로 확인해야 합니다.

- Container Apps Environment의 실제 아웃바운드 경로에서 DNS, NSG, UDR, 방화벽 로그를
  배포 시각과 대조합니다. 다른 VNet의 개발 PC나 일반 Cloud Shell에서 접속되는 것은
  해당 Environment의 통신 성공을 증명하지 않습니다.
- MCR HTTPS 다운로드에는 `mcr.microsoft.com`과 `*.data.mcr.microsoft.com`이 필요합니다.
  NSG의 `MicrosoftContainerRegistry` 및 `AzureFrontDoor.FirstParty` TCP 443 요구사항도
  확인하십시오. 나머지 플랫폼 의존성은
  [공식 방화벽 가이드](https://learn.microsoft.com/azure/container-apps/use-azure-firewall)와
  [NSG 가이드](https://learn.microsoft.com/azure/container-apps/firewall-integration)를 따릅니다.
- Registry `/v2/`에서 HTTP 응답을 받는지 확인하는 것은 연결 진단일 뿐 전체 이미지 layer
  다운로드 성공이 아닙니다. Portal의 **Diagnose and solve problems → Image Pull Failures**와
  새 revision의 실제 pull/기동 결과로 검증합니다. 인증서 검사를 끄는 우회는 사용하지 않습니다.
- PNA `Disabled`와 Environment PE는 **인바운드** 경계입니다. 이 설정을 완화하거나 PE를
  추가한다고 MCR **아웃바운드** 접속 문제가 해결되는 것은 아닙니다.

공개 MCR 접근을 허용할 수 없다면 승인된 Registry에 Bootstrap 이미지를 반입하고 연결·pull
인증을 별도로 구성해야 합니다. 현재 KT 템플릿은 공개 MCR 이미지로 고정되어 있으며 임의의
내부 Registry로 자동 전환하지 않습니다. 이 변경은 별도 배포 구성 작업이고, 앱 이미지를
미러링해도 플랫폼 자체의 필수 egress까지 없어지는 것은 아닙니다.
Environment가 `Succeeded`라면 앱의 image-pull 실패와 Environment용 수동 PE 진단은 분리할 수
있습니다. 이미지 다운로드 실패만으로 정상 Environment·Foundry·저장소를 삭제하지 마십시오.

## Private Endpoint 생성 실패 진단

`kt-private-endpoint-4`는 현재 단일 저장소 프로필에서 Container Apps Environment에 연결하는
PE입니다. 수동 진단 기본값에서는 이 중첩 배포를 건너뛰므로 아래 배포 조회는 **기존 실패 기록
또는 자동 생성을 켠 실행**에 사용합니다. 이번 변경은 500 원인 해결이 아니라 수동 비교를 위한
배포 경계 분리입니다. `Microsoft.Network/privateEndpoints/pe-<Environment 이름>`이 `Failed`이고 내부
오류가 `InternalServerError`라면 **일시 장애인지 구성 문제인지 그 오류만으로는 확정할 수
없습니다**. `RequestDisallowedByPolicy`나 DNS zone link 충돌과도 구분하십시오.

현재 생성 계약은 다음과 같습니다.

- Environment 최초 요청: `internal=true`, `publicNetworkAccess=Disabled`, Consumption workload
  profile, 선택한 전용 subnet. legacy Consumption-only Environment가 아닙니다.
- PE 대상: 같은 배포 RG의 `Microsoft.App/managedEnvironments/<선택한 이름>`.
- PE group ID: 공식 [생성 예제](https://learn.microsoft.com/azure/container-apps/how-to-use-private-endpoint#create-a-private-endpoint)의
  `managedEnvironments`. 실제 대상의 `privateLinkResources` 응답과도 대조하십시오.
- `dependsOn`은 Environment 배포 완료를 기다리며 PE 루프는 직렬입니다. DNS zone group은
  PE 생성 후에 연결됩니다. `dependsOn`은 추가적인 서비스 내부 준비 시간을 보장하지 않습니다.

삭제하기 전에 **실제로 실패한 고객 구독**의 배포 시간, Correlation ID, service request ID와
leaf 오류를 보존합니다. 다른 개발 구독에서 통과한 validate/what-if는 해당 고객의 재현이
아닙니다. 아래 명령은 읽기 전용이며 기본 계정이나 Azure 리소스를 변경하지 않습니다.
승인된 고객 배포 호스트에서 실행하고 결과는 고객이 승인한 위치에 보관하십시오.

```powershell
& .\.venv\Scripts\Activate.ps1
$subscriptionId = '<customer-subscription-id>'
$resourceGroup = '<deployment-resource-group>'
$environmentName = '<actual-environment-name>'
$environmentId = "/subscriptions/$subscriptionId/resourceGroups/$resourceGroup/providers/Microsoft.App/managedEnvironments/$environmentName"

az deployment group show --subscription $subscriptionId --resource-group $resourceGroup `
  --name kt-private-endpoint-4 --only-show-errors `
  --query "{state:properties.provisioningState,time:properties.timestamp,correlationId:properties.correlationId,error:properties.error}" --output json

az deployment operation group list --subscription $subscriptionId --resource-group $resourceGroup `
  --name kt-private-endpoint-4 --only-show-errors `
  --query "[].{time:properties.timestamp,state:properties.provisioningState,requestId:properties.serviceRequestId,target:properties.targetResource.id,error:properties.statusMessage}" --output json

az rest --method get --url "https://management.azure.com${environmentId}?api-version=2026-01-01" `
  --only-show-errors `
  --query "{state:properties.provisioningState,internal:properties.vnetConfiguration.internal,publicNetworkAccess:properties.publicNetworkAccess,subnet:properties.vnetConfiguration.infrastructureSubnetId,profiles:properties.workloadProfiles}" --output json

az network private-link-resource list --subscription $subscriptionId --id $environmentId `
  --only-show-errors --output json

az network private-endpoint show --subscription $subscriptionId --resource-group $resourceGroup `
  --name "pe-$environmentName" --only-show-errors `
  --query "{state:provisioningState,subnet:subnet.id,connections:privateLinkServiceConnections,nics:networkInterfaces}" --output json
```

확인 순서는 Environment `Succeeded` → PNA/internal/workload profile → 실제 PE 대상·group·subnet
→ 양쪽 private-endpoint connection 상태 → subnet IP 여유·NSG/UDR·DNS·필수 egress입니다.
해당 구독·리전의 Service Health와 Activity Log도 같은 시간/Correlation ID로 확인합니다.
오래된 API에서 PNA가 누락됐다는 이유로 `Enabled`라고 추정하지 마십시오.

Environment가 정상이고 PE만 실패했다면 전체 RG, Foundry/Capability Host, 공유 저장소부터
삭제하지 않습니다. 승인된 동일 설정으로 실패 단계의 재시도를 검토하고, 삭제가 필요한 경우
실패한 PE와 연결의 의존성·DNS 레코드 소유권부터 확인합니다. 같은 최소 PE 생성이 반복해
500으로 실패하면 Correlation ID와 요청 정보를 Azure 지원에 전달해 서비스 측 조사를 요청합니다.
고정 sleep 추가, 무한 재시도, PNA 활성화 또는 `internal=false` 전환을 검증 없는 해결책으로
사용하지 않습니다. PE의 `Succeeded`와 연결의 `Approved`를 모두 확인한 뒤 DNS와 HTTPS를
실제로 점검해야 합니다.

## 비용과 용량의 한계

- Workload Profiles는 Dedicated 노드 구매를 뜻하지 않습니다. Consumption 프로필을 사용해
  D4 노드 상시 요금을 피하고 준비용 App은 유휴 시 0 replica로 줄입니다.
- **전체 유휴 비용이 0원인 것은 아닙니다.** 자동 Private Endpoint 네 개(ACA 자동 선택 시 다섯 개), Private DNS,
  Search Basic 1 SU, storage, Container Apps 관리 네트워크 등에 비용이 발생할 수 있습니다.
  Cosmos Serverless는 작은 초기 사용량을 가정한 선택이며 지속적인 부하에서 항상 최저가는 아닙니다.
- Search Free는 PE를 지원하지 않아 제외했습니다. Basic은 문서상 PE 지원 하한입니다.
  공식 Foundry 표준 샘플은 S1을 사용하므로 **Basic의 실제 KT Capability Host 생성·파일 검색·
  용량 적합성은 별도 live 인수 항목**입니다. 필요하면 배포 전 `searchSku=standard`를 승인합니다.
- 0.25 vCPU / 0.5 GiB는 플랫폼 할당 최소값이지 AzBrief의 메모리·성능 검증 결과가 아닙니다.
  모델·region·quota와 운영 부하가 확정되기 전 비용이나 운영 가능성을 보장하지 않습니다.
- 기존 Log Analytics를 연결하면 console 로그 수집 비용이 추가됩니다. 미연결 시 새 영구
  로그 저장소를 만들지 않습니다. Application Insights 및 `AzBriefFailures_CL` DCR도 생성하지 않습니다.
  오류 커스텀 테이블이 필요하면 Insights 없이 별도로 workspace/table/DCR와 제한된 ID 권한을
  구성하고 Logs Ingestion 연결성을 인수해야 합니다.

## 애플리케이션 전환

이 단계는 **별도의 KT 애플리케이션 구성 작업**입니다. 일반
[setup_customer.ps1](../../scripts/setup_customer.ps1)과 일반
[Azure MCP 템플릿](../azure-mcp-server/README.md)은 공유 Application Insights를 전제로 합니다.
`ktFoundation`을 `customerSetup`으로 변환해 넘기거나 그 스크립트의 검증을 생략하지 마십시오.

### 배포 후 수동 운영 권한

운영자는 다음 최소 권한 예시를 기준으로 실제 사용할 역할과 scope를 결정하여 직접 부여합니다.
이 표는 템플릿의 자동 부여 목록이 아닙니다. Container App의 대상은 배포 출력
`controlPlanePrincipalId`이며, `controlPlaneClientId`나 `foundryProjectPrincipalId`와 혼동하지 마십시오.
Hosted Agent는 게시 후 생성되는 **별도의 identity object ID**를 확인해야 합니다.

| 대상 ID | 사용할 기능 | 역할·범위 예시 |
|---|---|---|
| Container App 제어면 UAMI | 상태·체크포인트 및 분석 archive 저장 | Storage Blob Data Contributor, `azbrief-state`와 `azbrief-archive` 각각의 컨테이너 범위 |
| Container App 제어면 UAMI | 배포된 Hosted Agent 호출 | Foundry User, 대상 Foundry 프로젝트 범위 |
| Hosted Agent 전용 ID | Azure 리소스 근거 조회 | 필요한 읽기 역할을 실제 분석 대상 구독·관리 그룹·RG에 한정. 제어면·프로젝트 ID에 대신 부여하지 않음 |
| Hosted Agent 전용 ID | 프로젝트 Responses/네이티브 도구 사용 | Foundry User, 대상 Foundry 프로젝트 범위 |

Billing 등 추가 기능의 권한도 사용 여부와 서비스별 범위에 맞춰 별도 부여합니다. 역할 전파 후
실제 호출·저장을 검증하기 전에는 `applicationReady=false` 상태를 운영 준비 완료로 보지 않습니다.
기본 앱의 `/health` 성공은 운영 권한 부여가 끝났다는 뜻이 아닙니다.

이 변경은 기존 Azure 권한을 회수하지 않습니다. Incremental 배포에서 생략한 기존 역할 할당은
유지되며 정리·범위 조정은 운영자가 별도로 승인합니다. Foundry **프로젝트 ID**에 대한 BYO
초기화 권한은 템플릿에 남으므로, 배포자는 그 권한을 부여할 수 있는 접근 권한이 여전히 필요합니다.

### 운영 설정과 인수

1. 승인된 모델 ID/버전/SKU/처리량을 배포하고 여섯 specialist를 고유 이름으로 게시합니다.
   KT 전용 azd 환경과 배포 패키지를 사용하고 개발 `.env`는 재사용하지 않습니다.
2. 인증된 read-only Azure MCP를 이 Environment에 별도 App/ID로 구성합니다. Insights 리소스나
   `AppInsights` 프로젝트 연결을 만들지 않습니다. Hosted에 `APPLICATIONINSIGHTS_CONNECTION_STRING`을
   수동 선언하지 않으며 KT용 runtime은 Insights exporter를 사용하지 않도록 설정합니다.
3. Hosted Agent를 게시하고 **그 ID**에 evidence 및 프로젝트 데이터 역할을 직접 부여합니다.
   제어면/프로젝트 ID에 tenant evidence Reader를 대신 부여하지 않습니다.
4. App에 배포된 GHCR digest와 8000 `/health` 설정을 확인하고, 후속 scheduler Job에도 같은
  승인 digest·레지스트리 인증을 적용합니다. 제어면 ID에 필요한 운영 역할을 직접 부여하고
  저장소·Foundry 호출을 검증합니다. Entra/allow-list를 설정한 뒤 Admin/Archive를
   활성화하고, 초기 API 키·private archive/checkpoint·이메일을 인수합니다.
   이미지가 배포됐다는 사실만으로 AzBrief 운영 설치가 완료되는 것은 아닙니다.
5. 현재 Admin Manual Run은 App 내 background 작업입니다. 실제 운영 전 **최소 replica 1**과
   검증된 CPU/메모리로 전환하십시오. scale-to-zero는 긴 분석 도중 App을 종료할 수 있습니다.
   scheduler는 인수 전 Manual 상태로 두고 분석 동시성은 1에서 시작합니다.

필수 인수: 사설 경로에서 FQDN→PE IP, bootstrap/실제 health, Foundry Responses, Blob 저장/조회,
Cosmos 및 Search 접근, 여섯 역할 readiness, 한 건 분석·canonical archive, Entra 인가 거부,
실제 이메일 전달, 오류 로그 수신을 각각 확인합니다. 공용 경로의 접속 차단도 확인합니다.
`applicationReady=false`를 배포 성공만으로 `true`라고 해석하지 마십시오.

## 로컬 검증과 출처

```powershell
& .\.venv\Scripts\Activate.ps1
az bicep build --file infra\kt\main.bicep --outfile infra\kt\azuredeploy.json
python -m pytest tests\test_kt_deployment.py -o addopts= -q
```

Bicep **0.46.1**과 고정된 Azure Verified Modules를 사용합니다. compiled ARM을 손으로 수정하지
않습니다. CI에서 다시 compile해 drift를 확인합니다. compile과 mock 테스트는 KT 구독의 정책,
quota, RBAC 전파, Basic Search 호환성, 사설 DNS·실제 연결 성공을 증명하지 않습니다.

UI 계약 테스트는 ARM 출력 매핑, 이름 기본값·정규식, 서브넷 최소값·예약 범위, 고정 재사용
플래그와 단계별 확인을 검사합니다. 공개된 legacy CreateUIDefinition JSON Schema에는 문서화된
`Microsoft.Solutions.ResourceSelector`와 `ArmApiControl` 형식이 아직 포함되어 있지 않습니다.
기존 스키마가 지원하는 구조/일반 control 검증과 이 두 control의 Learn 계약 검증을 구분하며,
전체 Portal 동작을 스키마 통과만으로 보장하지 않습니다.
[공식 Sandbox](https://portal.azure.com/#blade/Microsoft_Azure_CreateUIDef/SandboxBlade)에서
UI 파일을 붙여 넣어 Preview하고, VNet 변경·조회 거부·누락 subnet·두 배포 단계·DNS/로그 옵션을
실제 고객 로그인으로 확인하십시오. 이번 로컬 작업에서는 Sandbox가 로그인 화면으로 이동하여
렌더링 및 live ARM readback은 확인하지 못했습니다.

- [Foundry agent networking 및 최소 subnet](https://learn.microsoft.com/azure/foundry/agents/how-to/virtual-networks)
- [Capability Hosts](https://learn.microsoft.com/azure/foundry/agents/concepts/capability-hosts)
- [Standard Agent Setup 및 BYO 서비스/RBAC](https://learn.microsoft.com/azure/foundry/agents/concepts/standard-agent-setup)
- [공식 private-network Standard Setup Bicep 샘플](https://github.com/microsoft-foundry/foundry-samples/tree/f71a76389c96a322cece0c9f1fc8b9f91d82dce7/infrastructure/infrastructure-setup-bicep/15-private-network-standard-agent-setup)
- [Container Apps VNet 크기](https://learn.microsoft.com/azure/container-apps/custom-virtual-networks)
- [Container Apps Private Endpoint](https://learn.microsoft.com/azure/container-apps/how-to-use-private-endpoint)
- [Consumption workload profile](https://learn.microsoft.com/azure/container-apps/workload-profiles-overview)
- [Search Private Endpoint의 Basic 이상 조건](https://learn.microsoft.com/azure/search/service-create-private-endpoint)
- [Cosmos Private Endpoint IP 수](https://learn.microsoft.com/azure/cosmos-db/how-to-configure-private-endpoints#fetch-the-private-ip-addresses)
- [Azure Verified Modules 원본과 MIT 라이선스](https://github.com/Azure/bicep-registry-modules)
- [기존 리소스 선택 control](https://learn.microsoft.com/azure/azure-resource-manager/managed-applications/microsoft-solutions-resourceselector)
- [읽기 API control](https://learn.microsoft.com/azure/azure-resource-manager/managed-applications/microsoft-solutions-armapicontrol)
- [Portal UI Sandbox 검증](https://learn.microsoft.com/azure/azure-resource-manager/managed-applications/test-createuidefinition)
