# Network Requirements

**AzBrief Enterprise — KT 프라이빗 배포·운영 네트워크 요구사항**

[프로젝트 README](../README.md) · [인프라 색인](README.md) ·
[KT 배포 가이드](kt/README.md) · [일반 Enterprise 배포 가이드](CUSTOMER_DEPLOYMENT.md)

| 항목 | 기준 |
|---|---|
| 조사 기준일 | **2026-10-05** |
| 대상 독자 | 고객 네트워크·보안 담당자, Azure 플랫폼 담당자, 배포·운영 담당자 |
| 기본 범위 | Azure Public Cloud의 **KT 프로필**. 기존 VNet, Workload Profiles Container Apps Environment의 Consumption 프로필, Foundry VNet Injection |
| 근거 | Microsoft/GitHub 공식 문서, 현재 저장소의 실행 경로, 개발 환경에서 수행한 제한된 다운로드 경로 확인 |
| 검증 한계 | **고객 방화벽 통과를 검증한 최종 정책이 아님.** 실제 출발지·DNS·라우팅·선택 기능을 확정하고 인수 검증 후 승인 |

> 이 문서는 방화벽 요청·검토 기준이다. 읽거나 링크를 추가해도 Azure 리소스, UDR, NSG,
> DNS, 방화벽 정책은 변경되지 않는다. 일반 Enterprise의 ACR·Key Vault·Application Insights
> 구성에 그대로 적용하지 말고, 해당 배포의 실제 endpoint와 선택 기능을 반영한다.

## 목차

- [1. 적용 범위와 원칙](#1-적용-범위와-원칙)
- [2. 출발지와 고객 입력값](#2-출발지와-고객-입력값)
- [3. 인터넷 아웃바운드](#3-인터넷-아웃바운드)
- [4. 사설 리소스 연결과 DNS](#4-사설-리소스-연결과-dns)
- [5. 배포 호스트와 빌더](#5-배포-호스트와-빌더)
- [6. 사용자 브라우저와 메일 클라이언트](#6-사용자-브라우저와-메일-클라이언트)
- [7. 기본 허용 목록에서 제외할 항목](#7-기본-허용-목록에서-제외할-항목)
- [8. 방화벽 정책 작성 조건](#8-방화벽-정책-작성-조건)
- [9. 확정 전 확인 사항](#9-확정-전-확인-사항)
- [10. 인수 검증과 변경 관리](#10-인수-검증과-변경-관리)
- [11. 조사 근거와 검증 범위](#11-조사-근거와-검증-범위)
- [12. 출처](#12-출처)

## 1. 적용 범위와 원칙

### 현재 구성

- Container Apps는 **Workload Profiles Environment + Consumption 프로필**이다.
  과거의 **Consumption-only Environment**와 네트워크 요구사항이 다르다. [S01], [S02]
- AzBrief 제어면 이미지는 GHCR에서, 별도 read-only Azure MCP 이미지는 MCR에서 받는다.
  **앱 이미지를 GHCR로 바꿔도 ACA 플랫폼의 MCR 의존성은 없어지지 않는다.** [S02], [S03]
- Foundry는 전용 subnet으로 주입한다. Hosted Python 코드와 여섯 persisted Prompt Agent의
  관리형 실행·도구 경로는 같은 것이 아니다. Hosted 코드는 자체 NIC를 사용하고,
  도구 연결에는 프로젝트 data proxy가 관여한다. [S06]
- Hosted Agent 소스 배포는 [azure.yaml](../azure.yaml)의
  `codeConfiguration.dependencyResolution: remote_build`, `runtime: python_3_13`을 사용한다.
- Foundry, Blob Storage, Cosmos DB, AI Search는 사설 경로를 사용한다.
  Container Apps Environment PE 자동 생성은 현재 KT 기본값에서 꺼져 있으므로,
  수동 PE 또는 승인된 내부 ingress 경로를 별도로 확인한다.
- KT 초기 배포는 Application Insights, scheduler Job, 메일 서비스를 생성하지 않는다.
  기존 Log Analytics 연결은 선택 사항이며, Job·메일·Agent 운영 설정은 후속 작업이다.
- 커뮤니티 보강은 `COMMUNITY_INSIGHTS_ENABLED=true`가 기본값이다.
  관리형 Web Search의 provisioning 설정 `FOUNDRY_COORDINATOR_WEB_SEARCH_ENABLED`는
  **기본값이 false**이다. 소스 기본값과 실제 게시된 Agent 설정은 각각 확인한다. [C03]

### 정책 원칙

1. **VNet Injection만으로 모든 통신의 North–South 경유가 보장되지는 않는다.**
   Hosted NIC, Foundry data proxy, 관리형 빌더, Azure 로컬 플랫폼, Private Endpoint의
   실행 위치·유효 경로를 구분한다. `0.0.0.0/0 → NVA` 경로도 실제 subnet에서 확인한다. [S04], [S06]
2. 인터넷 FQDN 허용, 사설 PE 접근, 로컬 플랫폼 통신, 사용자 브라우저 허용을 분리한다.
3. 표에 별도 표시가 없으면 **HTTPS / TCP 목적지 443**, 출발지 포트는 동적 포트다.
   Stateful 방화벽의 응답 트래픽을 사용하며 다운로드를 위해 인터넷 inbound를 열지 않는다.
4. **필수**는 명시된 출발지·기능의 요구사항이다. **조건부**는 해당 기능을 사용할 때만
   추가한다. **확인 필요**는 광범위한 허용으로 대신하지 않는다.
5. URL의 경로는 사용 목적을 설명한다. 일반 FQDN/SNI 방화벽은
   `learn.microsoft.com/api/mcp` 중 호스트만 구분하며 URL 경로 제한까지 제공하지 않는다.

## 2. 출발지와 고객 입력값

### 출발지 그룹

| ID | 실제 출발지 | 역할 |
|---|---|---|
| **A** | Container Apps Environment 전용 subnet | AzBrief App, 향후 scheduler Job, Azure MCP App, 플랫폼 이미지 다운로드 |
| **B** | Foundry 전용 injection subnet의 Hosted NIC 및 프로젝트 data proxy 경로 | 분석·문서 조사·Prompt Agent 호출·MCP·데이터 저장소 접근 |
| **C** | 고객망 내 배포 PC/관리 VM | Azure CLI·azd·Bicep, 코드·패키지 다운로드, Agent 게시 |
| **D** | 관리자 브라우저·메일 클라이언트 또는 해당 웹 프록시 | Portal·Entra 로그인·Admin/Archive·보고서 링크와 이미지 |
| **E** | 실제 빌더 | 고객 내 빌더, 개발 구독 ACR Task, Foundry 관리형 remote builder를 구분 |

A/B는 단일 replica IP가 아니라 필요한 subnet/플랫폼 경로를 기준으로 한다. 방화벽에
중간 NAT 주소가 보이면 그 주소와 원본 출발지의 대응 관계를 기록한다. E가 고객 VNet 밖에
있으면 그 빌더의 인터넷 통신을 고객 A/B 방화벽 규칙으로 신청하지 않는다.

### 고객 환경에 맞게 채울 값

실제 고객 값은 이 공개 문서에 기록하지 않고 별도의 고객 변경 요청서에 기입한다.

| 입력값 | 확인 내용 |
|---|---|
| Azure cloud·region | 이 문서는 Public Cloud 기준. `<region>`은 실제 리전으로 치환 |
| A/B subnet CIDR, C/D 주소, NAT | 각 방화벽 구간에서 관측되는 source IP/CIDR |
| UDR·NVA·DNS | 연결된 route table, next hop, 고객 DNS/Resolver IP와 전달 경로 |
| Foundry endpoint | 실제 account/project FQDN, PE IP, 주입 subnet |
| Storage·Cosmos·Search | 계정별 endpoint, 글로벌/리전별 DNS 레코드 및 PE IP |
| App·MCP | 실제 FQDN과 HTTPS 사설 접근 경로, PE 또는 내부 ingress의 대상 IP |
| Registry | GHCR/사설 mirror 여부, Public/Private 상태, 필요한 읽기 인증 |
| 선택 기능 | ACS, Log Analytics, DCR/DCE, Insights/AMPLS, A365, cloud evaluation, Web Search |
| 빌드 위치·설치 방법 | 사전 설치/오프라인 반입인지, 고객 내 빌드인지, 관리형 remote build인지 |
| 브라우저 인증 | Entra/연합 IdP/MFA, 사용하는 Portal blade와 웹 프록시 |

요청서에는 아래 표의 규칙 ID 외에 **실제 source, 담당자, 승인 사유, 적용 기간, 검증 결과,
변경 티켓**을 함께 기록한다.

## 3. 인터넷 아웃바운드

### 3.1 플랫폼·이미지·인증

| 규칙 | 출발지 | 목적지 FQDN | 포트 | 적용 조건·목적 | 근거 |
|---|---|---|---|---|---|
| N01 | A | `ghcr.io` | TCP 443 | GHCR 이미지 사용 시 필수. 인증·manifest·layer 요청 | [S11], [V01] |
| N02 | A | `pkg-containers.githubusercontent.com` | TCP 443 | GHCR 이미지 데이터 다운로드. `ghcr.io`만 허용하면 부족 | [S11], [V01] |
| N03 | A, B | `mcr.microsoft.com` | TCP 443 | ACA 시스템/MCP 이미지, Foundry private-VNet 소스 배포 | [S02], [S03], [S08] |
| N04 | A; B는 실제 MCR 데이터 경로 확인 | `*.data.mcr.microsoft.com` | TCP 443 | ACA의 MCR 데이터 다운로드 필수. Foundry 소스 문서는 MCR 본체만 명시하므로 전이 경로 별도 확인 | [S02], [S03], [S08] |
| N05 | A | `packages.aks.azure.com` | TCP 443 | 현재 ACA Workload Profiles 문서의 All scenarios 플랫폼 구성요소 다운로드 | [S03] |
| N06 | A | `acs-mirror.azureedge.net` | TCP 443 | 같은 문서에 남아 있는 플랫폼 바이너리/CNI 다운로드 | [S03] |
| N07 | A, B | `*.identity.azure.net` | TCP 443 | 관리 ID 및 Foundry Agent Service ACA 위임 | [S03], [S07] |
| N08 | A, B | `login.microsoftonline.com`, `*.login.microsoftonline.com`, `*.login.microsoft.com` | TCP 443 | Entra 인증·토큰·플랫폼 위임. wildcard가 apex를 포함한다고 가정하지 않음 | [S03], [S07], [S08] |
| N09 | A, B | `management.azure.com` | TCP 443 | AzBrief ARM·Resource Graph·Policy·Health·Advisor·Cost·Billing 조회, App 상태/준비 확인 | [C04] |

Service Tag 방식의 대안은 아래와 같다. FQDN 규칙과 태그 규칙을 모두 중복 허용해야
한다는 뜻은 아니다. NSG가 별도로 제한되어 있다면 그 계층에서도 통신이 허용되어야 한다.

| 목적 | Service Tag | 목적지 포트·주의 |
|---|---|---|
| MCR | `MicrosoftContainerRegistry` + `AzureFrontDoor.FirstParty` | TCP 443. 정식 태그 이름의 점을 유지 |
| Entra | `AzureActiveDirectory` | TCP 443. 서비스 전체 주소 범위이며 고객 테넌트 전용 범위가 아님 |
| ARM | `AzureResourceManager` | TCP 443 |
| 선택한 Azure Monitor 연동 | `AzureMonitor` | TCP 443. 기능 조건은 3.3 참조 |

태그 지원·IP 갱신은 [S16] 기준으로 관리한다. **`AzureContainerRegistry` 태그는 GHCR을
허용하는 규칙이 아니다.** GitHub 공식 Packages 목록의 `*.pkg.github.com`은 다른 패키지
형식까지 사용할 때 검토한다. 조사한 GHCR image pull에서는 해당 wildcard를 사용하지 않았다.

### 3.2 AzBrief 업무 기능

| 규칙 | 출발지 | 목적지 FQDN | 포트 | 적용 조건·목적 | 근거 |
|---|---|---|---|---|---|
| N10 | A의 App/Job | `www.microsoft.com` | TCP 443 | 필수. `/releasecommunications/api/v2/azure/...` RSS·상세·과거 기간 조회 | [C01], [V02] |
| N11 | B | `learn.microsoft.com` | TCP 443 | 필수. 문서 검색·본문과 Learn MCP `/api/mcp` | [C02], [C05] |
| N12 | B | `azure.microsoft.com`, `www.microsoft.com` | TCP 443 | 관련 공지·공식 문서 본문을 조회하는 경우 | [C02] |
| N13 | B | `techcommunity.microsoft.com`, `devblogs.microsoft.com`, `github.com` | TCP 443 | 허용된 관련 기술 문서·샘플 페이지, Tech Community RSS 본문 | [C02], [C03] |
| N14 | B | `aka.ms`, `go.microsoft.com` | TCP 443 | 문서 단축 링크/redirect 사용 시. 최종 목적지도 애플리케이션 허용 목록에 있어야 함 | [C02] |
| N15 | B | `azureweekly.info` | TCP 443 | 커뮤니티 보강. **기본 활성**. 불필요하면 `COMMUNITY_INSIGHTS_ENABLED=false`를 명시 | [C03], [V02] |
| N16 | A의 App/Job | `<ACS리소스명>.communication.azure.com` | TCP 443 | 메일 기능 사용 시 필수. 실제 ACS 리소스 endpoint로 제한 | [C06], [S15] |

N12–N15를 차단해도 프로세스가 기동될 수 있지만, **근거 수집 범위 축소와 정상 운영은
동일하지 않다.** 문서 조사를 지원하려면 코드의 허용 호스트와 방화벽 정책을 함께 맞춘다.
`github.com` 문서 조회는 런타임 코드 업로드를 의미하지 않는다.

Learn MCP는 Foundry 관리형 도구/data proxy 경로이며, 로컬 문서 fetch는 Hosted Python에서
실행된다. 두 경로를 모두 Hosted 프로세스의 직접 HTTP 호출이라고 기록하지 않는다. [S06], [S09]
보고서에 링크가 있다는 사실만으로 서버가 그 사이트를 방문한다고 판단하지 않는다.

현재 메일 전송은 ACS HTTPS API다. **SMTP 25/465/587 개방은 이 구현의 요구사항이 아니다.**
메일 본문·수신자 정보는 ACS로, 테넌트 근거는 설정된 Foundry 경로로 전송되므로
네트워크 허용과 별도로 고객의 데이터 처리·지역 정책도 승인해야 한다.

### 3.3 로그·관측·선택 기능

| 규칙 | 출발지 | 대상 | 포트 | 적용 조건 | 근거 |
|---|---|---|---|---|---|
| O01 | A | Service Tag `AzureMonitor` | TCP 443 | ACA의 기존 Log Analytics/Monitor 연결을 사용할 때 | [S02], [S14] |
| O02 | A/B의 Logs Query 호출자 | `api.loganalytics.io`, `*.api.loganalytics.io`, `api.loganalytics.azure.com`, `api.monitor.azure.com`, `*.api.monitor.azure.com` | TCP 443 | Admin 오류 이력·분석 도구의 Logs Query API. 조사한 SDK 기본 주소는 `api.loganalytics.io` | [C07], [S14] |
| O03 | A/B의 Logs Ingestion 호출자 | 설정된 실제 DCR/DCE ingestion FQDN, 통상 `*.ingest.monitor.azure.com` 계열 | TCP 443 | `AzBriefFailures_CL` 등 별도 수집을 구성할 때. 실제 endpoint와 필요한 서비스 redirect를 확인 | [C08], [S14] |
| O04 | 활성화된 Insights 호출자 | `settings.sdk.monitor.azure.com`, `*.in.applicationinsights.azure.com`, `*.livediagnostics.monitor.azure.com` | TCP 443 | Application Insights 연결·계측을 실제 사용할 때. KT 초기 배포에는 없음 | [C08], [S07], [S14] |
| O05 | Foundry hosting library | `agent365.svc.cloud.microsoft` | TCP 443 | A365 데이터 수집이 켜진 경우. **기본 on/off는 조사 문서에서 미확인**, 실제 상태 확인 | [S07], [S08] |
| O06 | Foundry cloud evaluation 실행 경로 | `AzureMachineLearning` 태그 또는 해당 리전의 `<region>.api.azureml.ms`, `*.dataproxy.<region>.api.azureml.ms` | TCP 443 | 해당 cloud evaluation/catalog 기능을 사용할 때. 일반 Quality Reviewer/G-Eval 호출만으로 추가하지 않음 | [S07] |

- Private Monitor/AMPLS를 별도로 구성했다면 해당 endpoint의 DNS·사설 경로를 우선 적용한다.
  이 표는 AMPLS 구축 완료를 전제하지 않는다.
- Azure Monitor의 전체 제품 목록에는 Profiler, Availability, JS SDK, AMA 등 다른 기능도
  포함된다. 사용하지 않는 기능의 endpoint를 모두 가져오지 않는다.
- A365 수집을 사용하지 않으면 지원되는 `a365LoggingEnabled=false` 설정을 검토한다.
  문서상 **새로 시작하는 세션부터** 연결 시도가 멈추고 기존 세션은 계속 시도할 수 있다.
- `AzureFrontDoor.Frontend`는 A365 문서에 있는 넓은 대안이다. 가능하면 O05의 정확한
  FQDN으로 제한한다. 이는 MCR의 `AzureFrontDoor.FirstParty`와 다른 태그다.

## 4. 사설 리소스 연결과 DNS

### 4.1 사설 데이터 경로

다음은 **인터넷 public endpoint 허용 목록이 아니다.** 일반 서비스 FQDN을 애플리케이션에
사용하고, DNS가 승인된 PE/내부 ingress IP를 반환하도록 구성한다. 사설 경로가 방화벽을
통과하면 그 구간의 규칙으로 신청한다.

| 규칙 | 출발지 → 대상 | 사용할 FQDN | 포트·조건 | 근거 |
|---|---|---|---|---|
| P01 | A/B/C → Foundry | `<Foundry계정>.services.ai.azure.com` | TCP 443. 프로젝트 Responses, Agent 관리, 소스 업로드 | [C09], [S06], [S08] |
| P02 | 실제 해당 API 호출자 → Foundry | 계정의 실제 `openai.azure.com`, `cognitiveservices.azure.com` 계열 endpoint | TCP 443. 사용 여부와 PE 레코드 확인. 직접 외부 OpenAI API를 뜻하지 않음 | [S10] |
| P03 | A/B → 공유 Storage | `<Storage계정>.blob.core.windows.net` | TCP 443. Archive·checkpoint·설정과 Foundry backing store | [C10], [S10] |
| P04 | Foundry → Cosmos DB | `<Cosmos계정>.documents.azure.com` 및 실제 리전별 endpoint | Gateway는 TCP 443. Foundry 관리형 client의 mode 확인 필요 | [S17], [S18] |
| P05 | Foundry → AI Search | `<Search서비스>.search.windows.net` | TCP 443 | [S10] |
| P06 | Foundry data proxy → Azure MCP | 실제 MCP Container App FQDN | HTTPS ingress TCP 443. 컨테이너 내부 8080과 구분 | [S09], [C11] |
| P07 | C/D → AzBrief | 실제 AzBrief Container App FQDN | 사설 ingress TCP 443. 내부 8000과 구분 | [C12] |
| P08 | 실제 호출자 → Key Vault/사설 registry | 구성한 리소스의 실제 FQDN | 해당 기능을 추가한 경우만. KT 초기 프로필은 Key Vault/ACR을 만들지 않음 | 해당 배포 구성 |

**Cosmos DB는 모드에 따라 포트가 달라진다.**

- Gateway mode의 HTTPS는 TCP 443이다.
- Direct mode + Private Link는 공식 문서가 **TCP 0–65535**를 명시한다.
- 이것이 Foundry 관리형 client가 Direct mode를 사용한다는 증거는 아니다.
  mode가 확인되지 않았으면 전 포트를 자동 승인하지 않고, 443만으로 반드시 충분하다고
  확정하지도 않는다.
- Direct mode가 실제 필요하다면 해당 출발지와 **Cosmos PE IP들**로 범위를 제한한다.
  글로벌 endpoint 외에 리전별 PE IP도 확인한다. [S17], [S18]

### 4.2 Private DNS

| 리소스 | 사용하는 Private DNS zone |
|---|---|
| Foundry | `privatelink.cognitiveservices.azure.com`, `privatelink.openai.azure.com`, `privatelink.services.ai.azure.com` |
| Blob Storage | `privatelink.blob.core.windows.net` |
| Cosmos NoSQL | `privatelink.documents.azure.com` |
| AI Search | `privatelink.search.windows.net` |
| Container Apps Environment PE | `privatelink.<region>.azurecontainerapps.io` |

일반 서비스 FQDN → Private Link alias → 실제 사설 A 레코드까지 확인한다. 고객 DNS는
필요한 서비스 suffix를 Azure 측 Resolver/전달 경로로 해석할 수 있어야 한다. 온프레미스
DNS가 Azure 플랫폼 IP에 직접 접근할 수 있다고 가정하지 않는다. [S05], [S10]

ACA PE를 사용하는지 내부 load balancer ingress를 사용하는지에 따라 DNS의 대상 IP를
확정한다. 존재하지 않는 수동 PE를 전제하거나 두 경로의 레코드를 임의로 섞지 않는다.
같은 namespace가 VNet에 이미 연결되어 있으면 해당 zone을 재사용한다.

### 4.3 DNS와 로컬 플랫폼 통신

| 규칙 | 출발지 → 대상 | 프로토콜·포트 | 처리 |
|---|---|---|---|
| L01 | A/B/C → 고객 DNS/Resolver 실제 IP | **UDP/TCP 53** | 필수. TCP fallback도 허용 |
| L02 | Azure 측 플랫폼/적절한 DNS forwarder → `168.63.129.16` | **UDP/TCP 53** | Azure DNS. 일반 인터넷 목적지나 UDR 대상이 아님 |
| L03 | ACA subnet → 동일 ACA subnet | 공식 ACA 표의 **Any protocol/port** | 플랫폼 내부 East–West 통신 보존. 인터넷 전체 허용을 뜻하지 않음 |

Consumption workload profile은 custom DNS를 사용해도 `AzurePlatformDNS` 접근을 유지해야
한다. Dedicated 프로필에만 해당하는 차단 가능 조건을 적용하지 않는다. [S05]
`*.hcp.<region>.azmk8s.io` 등 ACA가 명시하는 플랫폼 이름의 DNS도 차단·재정의하지 않는다.
DNS 해석 필요성과 임의의 모든 AKS tunnel 포트 개방을 혼동하지 않는다.

`169.254.169.254` IMDS나 관리 ID용 로컬 endpoint는 인터넷 egress 규칙이 아니다.
프록시로 우회시키지 않는다. VM의 IMDS 설명이 ACA 앱에서 직접 IMDS를 호출하라는 뜻도
아니다. Azure Load Balancer의 플랫폼 health probe는 **별도의 inbound NSG 요구사항**이며,
이 outbound 표가 이를 대체하지 않는다. [S02], [S19], [S20]

## 5. 배포 호스트와 빌더

**완성된 이미지를 pull하는 ACA에는 PyPI·Debian·Docker Hub를 일괄 허용하지 않는다.**
해당 작업을 수행하는 C/E에만 아래 규칙을 적용한다.

| 규칙 | 출발지 | 대상 | 포트 | 필요한 작업·조건 |
|---|---|---|---|---|
| D01 | C | `management.azure.com`, `login.microsoftonline.com` | TCP 443 | ARM 배포, Azure CLI 인증 |
| D02 | C | `graph.microsoft.com` | TCP 443 | Entra 앱·서비스 주체·app-role 설정을 해당 호스트에서 수행할 때 |
| D03 | C | 실제 Foundry 프로젝트 FQDN | TCP 443, **사설 경로** | Prompt Agent 게시, Hosted 소스 업로드·버전 조회 |
| D04 | 실제 템플릿/소스 다운로드 호출자 | `raw.githubusercontent.com` | TCP 443 | raw ARM/UI·설정 파일 다운로드. Portal 브라우저, 로컬 도구, Azure의 template fetch 중 실제 호출자 구분 |
| D05 | C/E | `github.com`, `api.github.com`, `codeload.github.com` | TCP 443 | 소스 clone/API/ZIP. 승인된 오프라인 반입이면 제외 가능 |
| D06 | C/E | `release-assets.githubusercontent.com`, 필요 시 `objects.githubusercontent.com` | TCP 443 | 선택한 CLI·확장·Bicep release 다운로드. 버전별 실제 redirect 확인 |
| D07 | C | `mcr.microsoft.com` 및 필요한 MCR 데이터 endpoint | TCP 443 | `br/public:avm/...` Bicep restore. 이미 컴파일된 ARM JSON만 배포하면 로컬 restore 불필요 |
| D08 | C | `aka.ms` → `azcliprod.blob.core.windows.net` | TCP 443 | Windows Azure CLI 설치. 조사 시 실제 redirect |
| D09 | C | `aka.ms` → `azuresdkartifacts.z5.web.core.windows.net` | TCP 443 | azd 설치 스크립트·배포 파일. 조사 시 실제 redirect |
| D10 | C/E | `pypi.org`, `files.pythonhosted.org` | TCP 443 | Python 의존성 설치. wheel 반입/사내 mirror 사용 시 그 경로로 대체 |
| D11 | 고객 내 이미지 빌더 E | `auth.docker.io`, `registry-1.docker.io`, `production.cloudfront.docker.com` | TCP 443 | 현재 Dockerfile의 `python:3.11-slim` 다운로드. 조사한 base image 경로 |
| D12 | 고객 내 이미지 빌더 E | `deb.debian.org` | **TCP 80** | 조사한 base image의 apt 설정. 현재 Dockerfile의 build 단계에서 GCC 설치 |
| D13 | 실제 image pull/push 호스트 C/E | `ghcr.io`, `pkg-containers.githubusercontent.com` | TCP 443 | 고객 호스트가 이미지 다운로드/게시를 수행할 때 |
| D14 | ACR 사용 C/E | 실제 `<registry>.azurecr.io`와 해당 data endpoint | TCP 443 또는 사설 경로 | ACR 빌드·미러를 선택한 경우만. GHCR을 사용한다는 이유로 추가하지 않음 |

근거: [S08], [S11], [S21], [S22], [S23], [현재 Dockerfile][C13], [조사 관측][V01].
D12를 없애려면 내부 mirror 또는 HTTPS apt 설정으로 빌드 경로를 변경·검증해야 한다.
현재 이미지를 받기만 하는 A/B에 D11/D12를 적용하지 않는다.

기존 GHCR 이미지는 고객 VNet 밖의 개발 ACR 빌더에서 만들었다. 해당 빌더의 인터넷 접속
성공은 고객 subnet의 접속 성공을 증명하지 않는다. CLI·확장의 설치/갱신 경로는 버전에 따라
달라지므로, 사전 반입하거나 실제 배포 버전의 download manifest를 확인한다.
GitHub Actions runner/OIDC/artifact 전체 목록은 이 수동 배포의 기본 요구사항이 아니다.

### Foundry remote build

현재 공식 소스 배포 문서는 다음을 확인해 준다. [S08]

- 소스 ZIP의 문서화된 upload/download 경로는 Foundry 프로젝트 API다.
- `remote_build`는 provisioning 중 Python 의존성을 설치한다.
- private-VNet 소스 배포에 `mcr.microsoft.com`, `*.login.microsoft.com`이 필요하며,
  연결한 telemetry의 endpoint는 별도로 적용한다.

**의존성 설치 worker의 정확한 네트워크 위치와 전체 전이 다운로드 목록은 공개 문서만으로
확정되지 않는다.** `pypi.org`/`files.pythonhosted.org`는 Python 설치에 필요한 후보이지만,
항상 고객 B subnet에서 접속한다고 단정하지 않는다. 빌드 로그와 NVA deny 로그를 대조하고
필요하면 Microsoft에 해당 배포 방식의 source/egress 계약을 확인한다.
확인되지 않은 내부 artifact 저장소를 이유로 `*.blob.core.windows.net`을 일괄 열지 않는다.

## 6. 사용자 브라우저와 메일 클라이언트

이 절은 **D 출발지**다. A/B 서버에 아래 전체 목록을 복사하지 않는다.
표는 HTTPS 사용 기준이며 실제 HTTP 주소가 필요한 기능은 별도 검토한다.

| 규칙 | 기능 | 대상 | 적용 조건 |
|---|---|---|---|
| U01 | Admin/Archive | 실제 AzBrief FQDN, 사설 TCP 443 | 내부 앱 접속 |
| U02 | 웹 폰트 | `cdn.jsdelivr.net`, TCP 443 | Pretendard 다운로드. 차단 시 시스템 폰트 fallback이 있어 기능 필수는 아님 |
| U03 | Azure Portal | `portal.azure.com`, `*.portal.azure.com`, `*.hosting.portal.azure.net`, `*.hosting-ms.portal.azure.net`, `*.reactblade.portal.azure.net`, `*.ext.azure.com`, `hosting.partners.azure.net` | Portal 기본 framework |
| U04 | Portal 리소스·권한 | `management.azure.com`, `graph.microsoft.com`, `*.graph.microsoft.com`, 필요 시 `graph.windows.net`, `*.graph.windows.net` | 사용하는 blade에 맞춰 적용 |
| U05 | 로그인 | `login.microsoft.com`, `login.microsoftonline.com`, 계정 종류에 따라 `login.live.com` | 연합 IdP/MFA 추가 대상은 고객 구성에서 확인 |
| U06 | 로그인 정적 콘텐츠 | `*.aadcdn.msftauth.net`, `*.aadcdn.msftauthimages.net`, `*.aadcdn.msauthimages.net`, `*.logincdn.msftauth.net`, `*.msauth.net`, `*.aadcdn.microsoftonline-p.com`, `*.microsoftonline-p.com` | 공식 Portal 인증 목록. wildcard/apex 처리 확인 |
| U07 | ACA console/log stream | `azurecontainerapps.dev`와 실제 생성된 console/log-stream 호스트 | 해당 Portal 기능 사용 시. 실제 endpoint로 범위 확정 |
| U08 | Foundry Portal | `ai.azure.com`과 사용하는 Portal 인증·정적 콘텐츠 경로 | 브라우저 이용 시. 실제 선택 기능의 추가 endpoint 확인 |
| U09 | 보고서 링크·선택 이미지 | 보고서의 실제 문서/이미지 호스트 | 사용자 단말 또는 메일 image proxy에서 다운로드 |

웹 폰트는 [C14], Portal은 [S13], ACA Portal 기능은 [S01]을 기준으로 한다.
Portal의 계정 관리·개별 서비스 blade는 [공식 전체 목록][S13]의 해당 기능 항목을 추가한다.
이 표만으로 Azure Portal의 모든 서비스 기능을 허용했다고 판단하지 않는다.
문서 HTML을 읽는 Hosted가 브라우저처럼 해당 페이지의 모든 JS·CSS·이미지를 내려받는
것은 아니므로 브라우저용 CDN을 자동으로 B에 추가하지 않는다.

## 7. 기본 허용 목록에서 제외할 항목

| 항목 | 제외하거나 별도 확인하는 이유 |
|---|---|
| `AzureCloud.<region>` UDP 1194 / TCP 9000 | ACA Consumption-only 표의 항목. 현재 Workload Profiles 기본 규칙으로 옮기지 않음 |
| `EventHub.<region>` TCP 5671/5672, 인터넷 UDP 123 | 같은 legacy 표를 자동 적용하지 않음. 별도 실제 사용 근거가 있을 때 검토 |
| `api.openai.com`, `api.anthropic.com` | 현재 앱은 프로젝트-scoped Foundry Responses 경로를 사용 |
| `*.bing.com` 및 모든 검색 결과 사이트 | Web Search 기본 비활성. 관리형 검색의 내부 경로와 Hosted 직접 fetch는 다름 |
| `ai.azure.com`, `storage.azure.com`의 일괄 runtime 허용 | 코드에 있는 OAuth audience/scope일 수 있음. 실제 요청 endpoint를 확인 |
| `*.azure.com`, `*.microsoft.com`, `*.core.windows.net`, 전체 `AzureCloud` | 필요한 목적지보다 훨씬 넓은 범위. 누락된 개별 요구사항의 대체 규칙이 아님 |
| SMTP 25/465/587 | 현재 메일 구현은 ACS HTTPS API |
| 모든 NuGet/npm/Ubuntu 업데이트 endpoint | 현재 선택한 빌드·플랫폼 기능의 사용 근거가 있는 경우만 허용 |
| `schemas.microsoft.com`, `schemas.xmlsoap.org` | 현재 인증 코드의 claim 식별자. 해당 문자열 존재만으로 네트워크 요청을 뜻하지 않음 |

Web Search를 켜는 경우 공개 endpoint 사용은 문서화되어 있지만, 내부 서비스 hop 전체가
고객 NVA를 지나는지와 완전한 customer-firewall FQDN 계약은 확인되지 않았다. “모든 외부
도구 통신이 고객 방화벽에서 관측되어야 한다”는 요건이 있으면 별도 서비스 확인 또는
지원되는 기능 비활성화를 검토한다. [S06], [S07]

## 8. 방화벽 정책 작성 조건

1. **FQDN/SNI 정책 우선.** CDN의 DNS 결과를 고정 IP로 영구 등록하지 않는다.
   CNAME 재귀 추적, apex/wildcard 의미, DNS TTL 반영 방식은 방화벽 제품별로 확인한다.
2. **MCR alias와 데이터 경로 구분.** 조사 시 `mcr.microsoft.com`의 CNAME은
   `mcr.trafficmanager.net`이었다. ACA/AKS 관련 문서의 MCR alias 목록에도 차이가 있으므로
   실제 pull과 DNS 정책을 대조한다. 이를 이유로 전체 `*.trafficmanager.net`을 허용하지 않는다.
3. **Service Tag는 지속 갱신.** Microsoft의 공식 JSON/API를 통해 필요한 태그의 IP 범위를
   동기화하고 `changeNumber`와 변경 이력을 관리한다. 태그는 고객 테넌트 인증 경계가 아니다.
   Azure 로컬 플랫폼 태그를 일반 NVA Internet destination처럼 취급하지 않는다. [S16]
4. **GitHub IP 목록은 완전하지 않다.** Meta API가 GitHub Packages 전체 IP를 제공한다는
   보장이 없다. GHCR에는 FQDN 정책과 실제 artifact redirect 확인을 사용한다. [S12]
5. **TLS를 보존.** 플랫폼·인증·registry 경로는 우선 종단 간 TLS를 보존해 검증한다.
   관리형 구성요소 전체의 TLS inspection/사내 root CA 지원은 확인되지 않았다.
   인증서 검증 비활성화로 해결하지 않는다.
6. **긴 HTTP 연결 고려.** Responses·Learn MCP의 streaming/장시간 응답을 프록시의 idle
   timeout·응답 크기 제한이 끊지 않는지 확인한다. 확인 없이 임의의 전역 timeout을 늘리지 않는다.
7. **인증과 방화벽을 별개로 검증.** GHCR 401/403은 PAT·package 권한 문제일 수 있다.
   `EOF`, DNS 실패, timeout과 구분한다. PAT·Authorization·서명 URL·고객 payload는 로그에 남기지 않는다.
8. **외부 반입·유출을 구분해 승인.** Registry·문서 수집은 다운로드, 소스 게시·로그·메일은
   업로드를 포함한다. 같은 443 허용이라도 기능·데이터 종류·출발지별 감사 요구를 기록한다.
9. **PE 장애를 공용 접근으로 우회하지 않음.** PNA/internal 설정을 보존하고 DNS·라우팅·PE
   상태를 수정한다. 연결 Approved만으로 PE provisioning 성공을 판단하지 않는다.

## 9. 확정 전 확인 사항

| 항목 | 현재 확인된 사실 | 최종 승인 전 필요한 증거 |
|---|---|---|
| Foundry `remote_build` | provisioning 중 의존성 설치, 문서화된 MCR/로그인 요구사항 | 실제 빌더 source/경로, 전이 package/artifact endpoint. 배포·deny 로그 또는 Microsoft 확인 |
| Foundry Cosmos mode | Gateway와 Direct의 포트가 다름 | 관리형 client mode와 실제 private endpoint 연결 결과 |
| A365 수집 상태 | enabled이면 `agent365.svc.cloud.microsoft`에 연결 | 실제 `a365LoggingEnabled` 상태. disabled 전환 시 새 세션에서 확인 |
| TLS inspection | 모든 관리형 구성요소의 호환성은 미확인 | 인증·image pull·원격 빌드·MCP·Responses 경로별 결과 또는 서비스 확인 |
| 실제 고객 경로 | VNet 주입만으로 NVA 경유를 확정할 수 없음 | A/B/C/D/E별 UDR·DNS·NAT·방화벽 관측 |
| 선택한 기능 | 소스 기본값은 실제 게시·운영 설정과 다를 수 있음 | Agent 도구, 로그/메일, registry 공개 여부 등 실제 설정 |

이 항목은 “모든 endpoint를 조사했으니 모든 배포가 성공한다”는 보장을 하지 않기 위한
명시적 인수 조건이다. 전체 Internet 허용으로 확인 절차를 대체하지 않는다.

## 10. 인수 검증과 변경 관리

각 항목은 **해당 실제 출발지**에서 시험하고 시각, trace/run ID, 목적지 FQDN, 포트,
NVA rule/action을 기록한다. 개발 PC의 성공은 고객 subnet의 성공으로 대체할 수 없다.

- [ ] A/B subnet의 UDR·NVA·DNS·NAT 경로와 로컬 플랫폼 예외를 확인했다.
- [ ] 정상 서비스 FQDN이 승인된 PE/내부 ingress IP로 해석된다.
- [ ] 새 revision/새 replica에서 GHCR·MCR image pull이 성공했다. 기존 캐시만 시험하지 않았다.
- [ ] 관리 ID 인증과 ARM·Resource Graph·필요한 Cost/Billing 권한을 확인했다.
- [ ] RSS·상세·기간 API, Learn 검색·본문·MCP, 사용하는 커뮤니티 보강을 확인했다.
- [ ] Hosted 새 버전의 source upload/remote build와 Prompt Agent 호출을 확인했다.
- [ ] Foundry → Azure MCP와 Blob/Cosmos/Search의 사설 접근을 확인했다.
- [ ] 한 건 분석 → canonical archive → 조회 → 요청된 메일 전달까지 확인했다.
- [ ] 선택한 Log Analytics·DCR·Insights/A365의 전송/조회 또는 비활성 상태를 확인했다.
- [ ] 관리자 브라우저 로그인·인가 거부·선택 폰트와 보고서 링크를 확인했다.
- [ ] 금지한 공용 접근이 차단되고, 허용한 통신만 정책·로그에서 확인된다.
- [ ] 9절의 미확정 항목을 해소하고 고객 변경 티켓에 최종 정책을 승인했다.

이미지 digest, Foundry/SDK/CLI 버전, Agent 도구, 모니터링, 리전, DNS 또는 NVA가 바뀌면
영향받는 규칙과 검증을 재검토한다. 새 목적지는 deny 로그와 기능 증거를 근거로 개별
승인하며, 실패를 근거 없이 일시적인 Azure 장애로 처리하거나 광범위한 wildcard로 덮지 않는다.

## 11. 조사 근거와 검증 범위

다음은 **2026-10-05 개발 환경의 관측**이며 고객 인수 결과가 아니다.
자격 증명·서명된 다운로드 URL·실제 고객 ID/IP는 문서에 포함하지 않는다.

| 관측 | 결과 |
|---|---|
| GHCR AzBrief image | digest `sha256:6d8fe1e237110318344f5786602b5105c6e662f6a45186dcfc8bc6cb8bd2aaa3`의 config/layer **9개 모두** `ghcr.io` 307 → `pkg-containers.githubusercontent.com` 206 |
| Docker base image | 게시 이미지의 `python:3.11-slim` base manifest를 추적한 linux/amd64 layer **4개 모두** `registry-1.docker.io` 307 → `production.cloudfront.docker.com` 206 |
| RSS·Learn·community·font | 각각 `www.microsoft.com` 200, `learn.microsoft.com` 206, `azureweekly.info` 206, `cdn.jsdelivr.net` 206. 제한된 GET/range 요청 |
| Windows Azure CLI installer | `aka.ms` → `azcliprod.blob.core.windows.net` |
| azd installer | `aka.ms` → `azuresdkartifacts.z5.web.core.windows.net` |
| PyPI wheel metadata | `azure-ai-agentserver-responses==2.0.0b0`의 wheel host는 `files.pythonhosted.org` |
| 기존 이미지 build 로그 | GCC 설치용 apt가 `http://deb.debian.org/debian` 및 `debian-security`를 사용 |
| SDK 기본값 | 조사 환경의 `LogsQueryClient`는 `https://api.loganalytics.io`를 기본 endpoint로 사용 |

이 결과는 특정 버전·시점의 경로 증거다. 이후 모든 CDN 주소가 같다는 보장이나,
관리형 Foundry builder가 같은 경로를 사용한다는 증거가 아니다. 범위 밖의 실제 고객
방화벽·TLS inspection·DNS 인수는 10절에서 수행한다.

## 12. 출처

### 공식 문서

- [S01 — ACA networking 및 Environment 유형][S01]
- [S02 — ACA firewall/NSG inbound·outbound 표][S02]
- [S03 — ACA Azure Firewall application/network rules][S03]
- [S04 — ACA user-defined routes][S04]
- [S05 — ACA Private Endpoint와 custom DNS][S05]
- [S06 — Foundry Hosted/Prompt Agent networking deep dive][S06]
- [S07 — Foundry private networking, firewall allowlisting, 선택 endpoint][S07]
- [S08 — Hosted Agent source deployment와 private-VNet firewall 요구사항][S08]
- [S09 — MCP public/private server endpoints][S09]
- [S10 — Foundry VNet 및 Private DNS 구성][S10]
- [S11 — GitHub Packages/container 및 release 통신 대상][S11]
- [S12 — GitHub IP 목록의 범위와 변경 주의사항][S12]
- [S13 — Azure Portal firewall/proxy 허용 목록][S13]
- [S14 — Azure Monitor endpoint와 firewall][S14]
- [S15 — ACS Email HTTPS 전송 API][S15]
- [S16 — Azure Service Tags와 외부 방화벽 IP 동기화][S16]
- [S17 — Cosmos DB 연결 모드][S17]
- [S18 — Cosmos Private Link와 Direct-mode 포트][S18]
- [S19 — Azure 특수 IP 168.63.129.16][S19]
- [S20 — Azure IMDS의 로컬 통신 경계][S20]
- [S21 — Bicep public module registry][S21]
- [S22 — Docker image pull 관련 domain 목록][S22]
- [S23 — azd 공식 설치 스크립트][S23]

### 저장소의 실행 경로

- [C01 — RSS·상세·기간 API][C01]
- [C02 — 문서 조회·redirect 허용 목록][C02]
- [C03 — 선택 기능 기본값][C03], [community service](../src/services/community_insights.py)
- [C04 — Azure Management REST][C04], [Resource Graph](../src/services/resource_graph.py),
  [Cost](../src/services/cost_management.py), [Billing](../src/services/billing.py)
- [C05 — 관리형 Learn MCP·Web Search provisioning][C05]
- [C06 — ACS Email client][C06]
- [C07 — Logs Query client][C07]
- [C08 — Logs Ingestion 설정][C08], [Insights exporter](../src/agent/telemetry.py)
- [C09 — Container Apps → Hosted proxy][C09], [Prompt Responses](../src/agent/foundry_backend.py)
- [C10 — KT backing-resource connections][C10]
- [C11 — 별도 Azure MCP App][C11]
- [C12 — KT 네트워크·초기 앱 템플릿][C12]
- [C13 — Container image build][C13]
- [C14 — 브라우저 폰트 정책][C14]

[S01]: https://learn.microsoft.com/azure/container-apps/networking
[S02]: https://learn.microsoft.com/azure/container-apps/firewall-integration
[S03]: https://learn.microsoft.com/azure/container-apps/use-azure-firewall
[S04]: https://learn.microsoft.com/azure/container-apps/user-defined-routes
[S05]: https://learn.microsoft.com/azure/container-apps/private-endpoints-with-dns
[S06]: https://learn.microsoft.com/azure/foundry/agents/concepts/agents-networking-deep-dive
[S07]: https://learn.microsoft.com/azure/foundry/how-to/configure-private-link#firewall-allowlisting
[S08]: https://learn.microsoft.com/azure/foundry/agents/how-to/deploy-hosted-agent-code
[S09]: https://learn.microsoft.com/azure/foundry/agents/how-to/tools/model-context-protocol#public-and-private-mcp-server-endpoints
[S10]: https://learn.microsoft.com/azure/foundry/agents/how-to/virtual-networks#dns-zone-configurations-summary
[S11]: https://docs.github.com/en/actions/reference/runners/self-hosted-runners#communication-requirements
[S12]: https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/about-githubs-ip-addresses
[S13]: https://learn.microsoft.com/azure/azure-portal/azure-portal-safelist-urls
[S14]: https://learn.microsoft.com/azure/azure-monitor/fundamentals/azure-monitor-network-access
[S15]: https://learn.microsoft.com/rest/api/communication/email/email/send?view=rest-communication-email-2023-03-31
[S16]: https://learn.microsoft.com/azure/virtual-network/service-tags-overview
[S17]: https://learn.microsoft.com/azure/cosmos-db/sdk-connection-modes
[S18]: https://learn.microsoft.com/azure/cosmos-db/how-to-configure-private-endpoints#port-range-when-using-direct-mode
[S19]: https://learn.microsoft.com/azure/virtual-network/what-is-ip-address-168-63-129-16
[S20]: https://learn.microsoft.com/azure/virtual-machines/instance-metadata-service
[S21]: https://learn.microsoft.com/azure/azure-resource-manager/bicep/modules
[S22]: https://docs.docker.com/desktop/enterprise/allow-list/
[S23]: https://azuresdkartifacts.z5.web.core.windows.net/azd/standalone/installer/install-azd.ps1
[C01]: ../src/rss/parser.py
[C02]: ../src/services/microsoft_learn.py
[C03]: ../src/config.py
[C04]: ../src/services/azure_rest.py
[C05]: ../scripts/provision_foundry_agents.py
[C06]: ../src/email/service.py
[C07]: ../src/services/log_analytics.py
[C08]: ../src/logging_config.py
[C09]: ../src/agent/hosted_client.py
[C10]: kt/agent-bindings.bicep
[C11]: azure-mcp-server/infra/modules/aca-infrastructure.bicep
[C12]: kt/main.bicep
[C13]: ../Dockerfile
[C14]: ../src/web_fonts.py
[V01]: #11-조사-근거와-검증-범위
[V02]: #11-조사-근거와-검증-범위
