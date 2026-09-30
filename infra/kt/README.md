# KT 전용 프라이빗 인프라 템플릿

[인프라 색인](../README.md) · [Bicep 원본](main.bicep) · [배포용 ARM](azuredeploy.json) ·
[Portal UI](createUiDefinition.json) · [입력 예시](main.parameters.example.json) ·
[사전검사·배포 CLI](../../scripts/deploy_kt.py)

기존 VNet을 사용하는 **별도 인프라 초기 배포(bootstrap) 프로필**입니다.
일반 Enterprise 템플릿, 개발 환경, 기존 VNet을 교체하지 않습니다.
요청의 “Capacity Host”는 Foundry **Capability Host**로 구현했습니다. 모델의 PTU/예약 용량을
구매하는 설정이 아닙니다.

> **범위:** 프라이빗 Foundry Standard Agent Setup과 최소 크기의 준비용 Container App을
> 배포합니다. 실제 AzBrief 애플리케이션 설치 완료를 의미하지 않습니다. 모델 배포,
> 여섯 Prompt Agent 및 Hosted Agent 게시, 실제 App/Job 이미지·인증·메일·스케줄 설정은
> [애플리케이션 전환](#애플리케이션-전환) 단계입니다. `ktFoundation.applicationReady`는
> 의도적으로 `false`이며 일반 `customerSetup` 출력과 호환되지 않습니다.

## 배포 구성

| 항목 | KT 설정 |
|---|---|
| VNet | 지정한 기존 VNet만 사용. VNet 리소스나 전체 subnet 목록을 PUT하지 않음 |
| Foundry | `AIServices/S0`, 시스템 ID, 로컬 키 인증 비활성, Public Network Access 비활성 |
| Foundry inbound | `account` Private Endpoint → 선택한 Private Endpoint subnet |
| Foundry outbound | 계정 최초 생성 시 `scenario=agent`, 선택한 Foundry 전용 subnet으로 VNet Injection |
| Capability Host | 자동 생성되는 account host 확인 후, project `agents` host 생성. account host 중복 생성 없음 |
| Agent backing storage | 별도 StorageV2 / Standard_LRS / Blob PE / Public Network Access 비활성 / Entra 전용 |
| 상태·아카이브 storage | 별도 StorageV2 / Standard_LRS / Blob PE. `azbrief-state`, `azbrief-archive` private container |
| Cosmos DB | NoSQL, 단일 리전 Serverless, `Sql` PE, Public Network Access·로컬 키 인증 비활성 |
| AI Search | Basic, replica 1 / partition 1, `searchService` PE, Public Network Access·로컬 키 인증 비활성 |
| Container Apps Environment | Workload Profiles 유형, **Consumption** 프로필, 전용 subnet 주입, Public Network Access 비활성 |
| Container Apps inbound | Environment의 `managedEnvironments` PE → 선택한 Private Endpoint subnet |
| 준비용 Container App | **0.25 vCPU / 0.5 GiB**, 최소 replica 0 / 최대 1, HTTPS, 포트 80의 hello-world 이미지 |
| Application Insights | **배포하지 않음**. Foundry 연결, 계측 설정, publishing 역할도 생성하지 않음 |
| Log Analytics | 기존 workspace ID를 선택적으로 연결. 새 workspace·DCR·AMPLS는 생성하지 않음 |
| 추가 비용 리소스 | 새 VNet, 전용 D4 노드, NAT Gateway, Firewall, ACR, 모델 배포, scheduler Job, 메일 서비스는 생성하지 않음 |

Foundry 프로젝트 ID에는 agent backing storage의 Storage Account Contributor·Blob Data Contributor,
Cosmos DB Operator, Search Service Contributor·Index Data Contributor를 먼저 부여합니다.
host가 컨테이너를 만든 뒤 `<workspaceId>-azureml-agent`에 Blob Data Owner를,
Cosmos의 `enterprise_memory`에 Cosmos-native Data Contributor를 부여합니다. 현재 Responses의
`agent-definitions-v1`·`run-state-v1`도 포함하는 DB 범위이며 Classic 컨테이너 세 개만 지정하지 않습니다.
프로젝트 이름이 아닌 `project.properties.internalId`로 workspace GUID를 계산합니다.

제어면 UAMI는 **상태 storage Blob Data Contributor + 프로젝트 Foundry User**만 받습니다.
프로젝트 ID가 canonical archive를 변경할 수 없도록 agent storage와 분리합니다.
Hosted 전용 ID의 구독/테넌트 근거 조회 권한은 게시 후 별도 부여합니다.

## 신규 리소스 기본 이름

Portal ARM 입력 화면과 CLI는 다음 이름을 미리 채웁니다. 기존 KT 프로필의
`<리소스약어>-azbrief-kt` 형식을 확장한 **수정 가능한 제안값**이며, KT 공식 명명 표준의
서비스별 접두사·환경·리전 코드가 확인된 것은 아닙니다. 확인되지 않은 환경이나 리전 코드는
추가하지 않았습니다. 실제 배포 전 KT의 승인된 명명 규칙에 맞게 검토하십시오.

| 입력 | 기본값 |
|---|---|
| `foundryAccountName` | `ai-azbrief-kt` |
| `agentStorageAccountName` | `stazbriefktagent` |
| `stateStorageAccountName` | `stazbriefktstate` |
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
| Private Endpoint subnet | **/28** — 16개 주소 중 Azure 예약 5개 제외 11개 | 없음 | 여섯 PE의 보수적인 9개 IP 예산 |
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

IP 예산은 Foundry 최대 3개를 위한 여유, 단일 리전 Cosmos 2개, Blob 두 개·Search·ACA 각각
1개로 9개입니다. `/29`의 3개 usable IP로는 부족합니다. 실제 NIC 할당은 배포 후 확인합니다.
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

각 PE의 zone group이 서비스 레코드를 연결합니다. 중앙 DNS/온프레미스 DNS를 사용하면
KT DNS 담당자가 Azure Private DNS로의 전달·해석을 구성해야 합니다. 이 템플릿은 DNS 서버,
VPN, ExpressRoute, peering, NSG/UDR, outbound 방화벽을 변경하지 않습니다.
신규 subnet에도 NSG/UDR를 자동 생성하지 않으므로 KT 정책에 따른 검토·연결이 필요합니다.

Container Apps의 `internal=false`, App의 `ingressExternal=true`는 **공용 인터넷 허용이
아닙니다**. Environment의 Public Network Access는 `Disabled`이며 PE로 들어온 요청을
App ingress까지 전달하는 구성입니다. KT Policy가 생성 요청을 검사하므로 이 값은 Environment
최초 `PUT`에 포함되며 사후 PATCH로 전환하지 않습니다. bootstrap URL은 VNet/승인된 사설
경로에서 접근합니다.

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
| 리소스 이름 | 여덟 기본 이름 편집, Azure 문자·길이 검사, Storage 분리 및 여섯 PE 대상 이름 충돌 차단 |
| 비용·DNS·로그 | Search Basic/S1 선택, DNS zone 생성 또는 중앙 RG의 일곱 zone 재사용, 기존 Log Analytics 선택 연결 |
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

DNS 재사용 모드는 기존 중앙 DNS **리소스 그룹 ARM ID 하나**로 일곱 zone ID를 구성합니다.
Foundry용 세 zone, blob/documents/search zone, 선택한 지역의 ACA zone이 그 RG에 모두 있어야
하며 기존 VNet 링크·권한은 별도로 확인합니다. 일부 zone만 재사용하거나 여러 RG에 분산된
구성은 CLI의 `existingPrivateDnsZoneIds` 개별 매핑을 사용하십시오. 로그 옵션은 기존 workspace만
연결하며 Application Insights나 오류 커스텀 테이블을 만들지 않습니다.
로그 연결을 선택하지 않으면 Container Apps Environment의 `appLogsConfiguration`을
`null`로 생략합니다. AVM `0.16.0`의 판별형은 `azure-monitor`와 `log-analytics`만
허용하므로 `destination: none`을 전달하지 않습니다.

처음에는 기본 선택인 **1단계 — 기반·연결·권한만 배포**로 실행합니다
(`deployCapabilityHost=false`). 성공 후 account Capability Host의 `Succeeded` 상태와 RBAC 전파를
확인하고, **같은 RG·이름·네트워크·DNS 입력**으로 다시 열어 **2단계 — project Capability Host 구성**을
선택합니다. UI는 두 배포를 자동 실행하거나 account host 준비를 기다리지 않습니다.
단계를 자동 처리하고 점유자·실제 IP 여유·이름 소유권을 검사하려면 CLI를 사용하십시오.
운영 이미지로 전환한 환경에 bootstrap을 다시 적용하지 마십시오.

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

입력 예시를 Git 제외 폴더에 복사해 기존 네트워크 placeholder와 CIDR을 실제 승인값으로 바꿉니다.
신규 리소스 이름은 위 기본값이 채워져 있으므로 KT 표준·전역 가용성을 확인한 뒤 필요하면 변경합니다.
기존 subnet을 그대로 재사용한다면 해당 CIDR 값을 비워도 됩니다. 값이 있으면 기존 prefix와
정확히 일치해야 합니다. 예시에는 실제 고객 ID, 비밀, 개발 환경 기본값이 없습니다.

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

1. 네트워크·서비스·PE/DNS·프로젝트·AAD 연결·사전 RBAC와 bootstrap App을 생성.
2. 자동 account Capability Host를 10초 간격으로 최대 31회 확인. 다시 live inventory를 읽어 신규 subnet
   플래그를 해제한 뒤 project Capability Host와 생성된 컨테이너의 data 역할을 구성.
3. host 상태, 필수 subnet/PE의 존재·승인·binding, DNS 링크, 각 서비스의 Public Network Access
   비활성 설정을 읽어 확인. 비공개 데이터 경로의 실제 연결 검사는 아래 인수 단계에서 수행.

실패하면 비정상 종료하며 원래 오류를 출력합니다. RBAC 전파 지연은 권한과 기존 host 상태를
먼저 확인한 후 같은 입력으로 재실행합니다. host 연결을 다른 데이터 저장소로 변경하거나
host를 삭제·재생성하지 않습니다. 생성된 서비스를 자동 삭제하는 롤백도 없습니다.
ARM subnet flag는 재실행마다 다시 계산합니다. 임시 parameter 파일은 성공/실패 후 삭제합니다.
실제 App 이미지로 전환한 뒤에는 bootstrap을 재적용하지 않도록 CLI가 차단합니다.

## 비용과 용량의 한계

- Workload Profiles는 Dedicated 노드 구매를 뜻하지 않습니다. Consumption 프로필을 사용해
  D4 노드 상시 요금을 피하고 준비용 App은 유휴 시 0 replica로 줄입니다.
- **전체 유휴 비용이 0원인 것은 아닙니다.** Private Endpoint 여섯 개, Private DNS,
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

1. 승인된 모델 ID/버전/SKU/처리량을 배포하고 여섯 specialist를 고유 이름으로 게시합니다.
   KT 전용 azd 환경과 배포 패키지를 사용하고 개발 `.env`는 재사용하지 않습니다.
2. 인증된 read-only Azure MCP를 이 Environment에 별도 App/ID로 구성합니다. Insights 리소스나
   `AppInsights` 프로젝트 연결을 만들지 않습니다. Hosted에 `APPLICATIONINSIGHTS_CONNECTION_STRING`을
   수동 선언하지 않으며 KT용 runtime은 Insights exporter를 사용하지 않도록 설정합니다.
3. Hosted Agent를 게시하고 **그 ID**에 evidence 및 프로젝트 데이터 역할을 부여합니다.
   제어면/프로젝트 ID에 tenant evidence Reader를 대신 부여하지 않습니다.
4. 실제 App과 scheduler Job에 같은 승인 digest·레지스트리 인증을 적용하고 8000 `/health`로
   전환합니다. API key, Entra/allow-list, secrets, private archive/checkpoint와 이메일을 연결합니다.
   bootstrap만 바꾸면 AzBrief 설치가 완료되는 것이 아닙니다.
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
