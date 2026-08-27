<div align="center">

# AzBrief Enterprise

**Azure updates, analyzed for your environment, delivered to your inbox.**

[![Python](https://img.shields.io/badge/python-3.10+-3776AB.svg?style=flat&logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Microsoft Foundry](https://img.shields.io/badge/Microsoft_Foundry-multi--agent-0078D4.svg?style=flat&logo=microsoftazure&logoColor=white)](https://learn.microsoft.com/azure/ai-foundry/)
[![LangGraph](https://img.shields.io/badge/LangGraph-agent-blue.svg?style=flat)](https://github.com/langchain-ai/langgraph)
[![Container Apps](https://img.shields.io/badge/Container_Apps-job%20%2B%20app-0078D4.svg?style=flat&logo=microsoftazure&logoColor=white)](https://learn.microsoft.com/azure/container-apps/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Container Apps Job (cron) → Microsoft Foundry Prompt Agents → Communication Services
· Container App orchestrator + `/admin` · VNet injection + Private Endpoint by default

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2FNetworkdog%2FAzBriefEnterprise%2Fmain%2Finfra%2Fazbrief-enterprise-deploy.json)

</div>

---

> **Looking for the Standard edition?** The Automation Runbook deployment lives in
> [Networkdog/AzBrief](https://github.com/Networkdog/AzBrief). Standard and Enterprise are
> two editions of the same AzBrief product and share its mission and analysis core.

<!-- TABLE OF CONTENTS -->
<details>
<summary>Table of Contents</summary>

- [Product identity](#product-identity)
- [Why AzBrief Enterprise?](#why-azbrief-enterprise)
- [What you get](#what-you-get)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [Deployment](#deployment)
  - [One-click deploy](#one-click-deploy)
  - [Network isolation](#network-isolation-networkisolationmode)
  - [Post-deployment steps](#post-deployment-steps)
  - [Scheduling operations](#scheduling-operations)
- [Multi-agent pipeline](#multi-agent-pipeline)
- [Admin console](#admin-console)
- [How the analysis works](#how-the-analysis-works)
- [Per-subscriber reports](#per-subscriber-reports)
- [Configuration](#configuration)
- [API](#api)
- [Development](#development)
- [Project structure](#project-structure)
- [Tech stack](#tech-stack)
- [Troubleshooting](#troubleshooting)
- [License](#license)

</details>

## Product identity

AzBrief Enterprise is the enterprise edition of
[AzBrief](https://github.com/Networkdog/AzBrief), not a separate product with a
different purpose. Both editions share the same analysis core and product identity;
this repository extends that foundation with a governed Microsoft Foundry runtime,
private networking, durable state and enterprise operations.

### Overview

AzBrief is an **Azure Update Intelligence Agent** for Azure administrators. It collects
Azure updates, correlates them with the tenant's actual resources, evaluates each update
on the independent axes of importance, impact and job relevance, and turns the findings
into a role-specific daily digest with evidence and concrete actions.

### Mission

**Translate every generic Azure announcement into what it means for this environment and
what the responsible operator should do next.** AzBrief closes the gap between knowing
that Azure changed and making a timely, well-grounded operational decision.

### Product direction

| Principle | Direction |
|---|---|
| **Environment before generic summary** | Ground conclusions in the tenant's real resources, configuration, health, policy, cost and regional availability |
| **Action over notification** | Go beyond describing a change to provide scoped procedures, commands, deadlines and risk warnings |
| **Coverage without silent filtering** | Analyze every collected update so retirements, security risks and adoption opportunities are not discarded before evidence is gathered |
| **One update, many responsibilities** | Adapt the same evidence for infrastructure, security, architecture and other roles, in each subscriber's language |
| **Trust before autonomy** | Keep evidence traceable, validate executable actions and fail closed when identity, permissions or model capabilities are unclear |
| **Enterprise governance by design** | Run the same intelligence mission with Entra-only access, governed Prompt Agents, private-by-default networking, observability and recoverable state |

### Goals

- Reduce the daily work of reading and triaging a high-volume Azure update feed.
- Identify affected resources, risks and opportunities from tenant evidence rather than
  inference alone.
- Deliver the next safe, specific action early enough to avoid service, security, cost and
  governance surprises.
- Give every stakeholder a briefing shaped for their role without duplicating the core
  investigation.
- Scale the original AzBrief experience into regulated environments without weakening its
  relevance, actionability or language quality.

### Vision

Make Azure change intelligence a routine operational capability: every Azure team starts
the day knowing **what changed, where it matters, why it matters and what comes next** for
its own environment. In that future, an Azure update is no longer another announcement to
read; it is evidence-backed decision intelligence ready for the people responsible for it.

<p align="right">(<a href="#azbrief-enterprise">back to top</a>)</p>

## Why AzBrief Enterprise?

Azure publishes dozens of updates every week. New features, service retirements, security
patches, pricing changes — each one can affect your production environment. But keeping up
is hard:

- **Volume.** Hundreds of updates per year. Only a fraction matter to you. Finding them
  means reading the feed every day.
- **No context.** Azure tells you *what* changed, but not *which of your resources are
  affected* or *what you need to do about it*.
- **One size doesn't fit all.** An infrastructure engineer needs migration steps. A security
  officer needs compliance impact. The same announcement can't serve both.

AzBrief collects Azure updates, cross-references them against your tenant's actual resources
via Resource Graph, classifies each by importance, impact and job relevance, and delivers a
consolidated daily digest — complete with CLI commands, procedures, and deadlines — straight
to each team member's inbox.

The **Enterprise** edition adds what a regulated environment needs on top of that analysis:

| | |
|---|---|
| **Governed multi-agent** | Phase-specialized Prompt Agents plus optional research/impact/action/review enrichment, with standing instructions, models, tools and guardrails governed in Foundry |
| **No API keys** | Foundry runs Entra-only (`disableLocalAuth`); the state account is Entra-only too. There is no key to leak or rotate |
| **Private by default** | `vnetInjection` injects the agent compute into a delegated subnet, integrates Container Apps with the same VNet, and puts Foundry, Key Vault and the state account behind private endpoints |
| **No sandbox ceiling** | A Container Apps Job replaces the Automation sandbox, so a run is bounded by its replica timeout (12 h default, up to 7 days) instead of a 3-hour fair-share limit and a 400 MB memory cap |
| **Admin console** | `/admin` behind Entra ID sign-in with an explicit principal allow-list — trigger a run, inspect configuration, review run history |
| **Durable checkpoint** | The "analysed up to" watermark is a blob that only moves forward, written after a run completes, so an interrupted run repeats a window instead of skipping an update |

<p align="right">(<a href="#azbrief-enterprise">back to top</a>)</p>

## What you get

- **All updates analyzed** — No pre-filtering; every update gets a full analysis
- **Three-axis assessment** — Each update independently scored on three orthogonal axes:
  - **중요성 (Importance)** — the update's inherent significance in the Azure ecosystem
  - **영향도 (Impact)** — the effect on your actual resources, from Resource Graph queries
  - **직무연관성 (Job Relevance)** — the fit to the subscriber's specific role
- **Tenant-wide correlation** — Resource Graph queries across every accessible subscription
- **Verified commands from the docs** — The Learn page an update links to is fetched and its
  `<pre>` command blocks are extracted separately from the prose, so real `az`/PowerShell
  commands survive the context budget instead of degrading into "check the Portal"
- **Practitioner commentary** — Topic-matched write-ups from the
  [Azure Weekly](https://azureweekly.info) digest supply real-world caveats that official
  documentation does not carry (opt out with `COMMUNITY_INSIGHTS_ENABLED=false`)
- **Triple-checked action items** — An action item is the only part of the report a reader may
  run verbatim against production, so it passes a three-layer safety gate: a deterministic
  static gate (destructive commands without a rollback, unattended `--yes`/`--force`,
  unresolved `<placeholder>` values, resource names absent from the evidence, fabricated
  deadlines), an independent adversarial LLM cross-check over the same evidence, and a policy
  gate that **strips the command** from any item that fails. Each item ships with its verdict
  badge (검증 완료 / 주의 필요 / 실행 보류 / 교차 검증 미수행)
- **Role-based reports** — Same update, different perspective per subscriber
- **Multilingual** — Per-subscriber language from a pluggable registry (Korean, English and
  Japanese ship curated style guides; any other language still renders through fallback
  labels and a generated style guide)

<p align="right">(<a href="#azbrief-enterprise">back to top</a>)</p>

## Architecture

```
Container Apps Job  ──  cron (0 2 * * * UTC), python -m src.scheduler
  │
  ├─ Azure Update RSS ──────── what changed
  ├─ LangGraph agent ───────── Plan → Execute → Evaluate → Report
  │    ├─ Azure Resource Graph ─── affected resources
  │    ├─ Microsoft Learn ──────── related documentation
  │    ├─ Cost Management ──────── cost impact
  │    ├─ Azure Advisor ────────── optimization recommendations
  │    ├─ Resource Health ──────── availability status
  │    ├─ Policy Compliance ────── governance state
  │    ├─ Service Health ───────── active incidents & maintenance
  │    └─ Region Availability ──── service-by-region support (ARM providers API)
  │
  ├─ Microsoft Foundry Prompt Agents
  │    planner → evaluator → reporter     (primary fallback when unset)
  │    research ┐
  │    impact   ┴→ action → review        (optional evidence enrichment)
  │
  ├─ Storage blob ──────────── digest checkpoint (forward-only watermark)
  └─ Communication Services ── per-subscriber email

Container App  ──  same image, same identity, same VNet
  ├─ /admin ─────────────────  Entra ID sign-in + principal allow-list
  └─ /api/* ─────────────────  orchestrator API (X-API-Key)
```

The job and the app run the **same container image** with different entry points, so the
schedule inherits the app's identity, network and settings and the two can never drift into
analysing with different configuration.

AzBrief itself is **not a Foundry Hosted Agent**. The custom LangGraph harness runs in
Container Apps and invokes persisted Foundry Prompt Agents through the Agents data plane.
See [the architecture assessment](.github/skills/foundry-agent-architecture/references/assessment.md)
for the trade-off, rubric, and Hosted Agent migration criteria.

<p align="right">(<a href="#azbrief-enterprise">back to top</a>)</p>

## Quick Start

Local development uses your Azure CLI identity to invoke agents already published in a
Microsoft Foundry project. There is no Azure OpenAI/OpenAI endpoint or API key fallback.

```bash
git clone https://github.com/Networkdog/AzBriefEnterprise.git
cd AzBriefEnterprise
python -m venv .venv && .venv/Scripts/Activate.ps1  # or: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set at minimum in `.env`:

```env
AZURE_TENANT_ID=your-tenant-id
AZURE_CLIENT_ID=
AZURE_SUBSCRIPTION_ID=your-subscription-id
FOUNDRY_PROJECT_ENDPOINT=https://your-resource.services.ai.azure.com/api/projects/your-project
FOUNDRY_PRIMARY_AGENT_NAME=azbrief-primary
```

Leave `AZURE_CLIENT_ID` empty for local development, then sign in and select the subscription:

```powershell
az login --tenant <tenant-id>
az account set --subscription <subscription-id>
```

Then run:

```bash
python -m scripts.test_local list                                    # list recent updates
python -m scripts.test_local analyze --latest                        # analyze the newest one
python -m scripts.test_local analyze --from 2026-02-01 --to 2026-02-10
python -m scripts.test_local analyze --latest --jsonl results.jsonl  # export, skip email
python -m scripts.test_local resources                               # view your resource summary
```

> **Historical date ranges:** The live Azure Update RSS feed only exposes a rolling window of
> the most recent ~200 items, so months that have aged out return nothing when queried
> directly. For date-range analysis (`--from`/`--to`), AzBrief merges a locally crawled
> history archive (`data/azure_updates_history.jsonl`, de-duplicated against the live feed).
> Refresh it with `python -m scripts.crawl_azure_updates`.

<p align="right">(<a href="#azbrief-enterprise">back to top</a>)</p>

## Deployment

### One-click deploy

Deploys the full agent topology: a Foundry account and project with a model deployment,
the Container App (orchestrator API + admin console), the Container Apps Job that runs the
daily digest, Key Vault, the state storage account, and Communication Services.

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2FNetworkdog%2FAzBriefEnterprise%2Fmain%2Finfra%2Fazbrief-enterprise-deploy.json)

**What gets deployed** ([infra/azbrief-enterprise-deploy.json](infra/azbrief-enterprise-deploy.json),
authored in [infra/enterprise/main.bicep](infra/enterprise/main.bicep)):

| Resource | Name | Notes |
|----------|------|-------|
| User Assigned Managed Identity | `id-{baseName}` | Container App과 스케줄러 Job이 공유 |
| Microsoft Foundry account | `aif-{baseName}-{suffix}` | `AIServices` · `allowProjectManagement` · **`disableLocalAuth`** |
| Foundry project | `{baseName}-agents` | Prompt Agent 데이터 플레인 작업 공간 |
| Model deployment | `gpt-4o` (변경 가능) | GlobalStandard, 기본 200K TPM |
| Key Vault | `kv-{baseName}-{suffix}` | RBAC 전용, 모든 런타임 시크릿 보관 |
| Storage account + `azbrief-state` 컨테이너 | `st{baseName}{suffix}` | 체크포인트 blob, **`allowSharedKeyAccess: false`** |
| Container Apps Environment | `cae-{baseName}-{suffix}` | 기본값에서 VNet 통합 |
| Container App | `ca-{baseName}` | 오케스트레이터 API + `/admin` |
| Container Apps Job | `caj-{baseName}` | cron 스케줄, `python -m src.scheduler` |
| Container App authConfig | `current` | Entra ID 로그인 (클라이언트 ID 제공 시에만) |
| Communication Services + Email | `acs-{baseName}-{suffix}` | Azure 관리 도메인 자동 연결 |
| Log Analytics + Application Insights | `log-` / `appi-` | 구조화 로그 및 추적 |
| Role assignments | 5건 | Key Vault Secrets User · Storage Blob Data Contributor · Foundry User · Monitoring Metrics Publisher · RG Reader |

**보안 설계 (기본값이 안전한 쪽):**

- **Foundry는 키가 존재하지 않습니다** — `disableLocalAuth: true`라서 Entra ID 토큰만 통합니다. 유출될 키도, 교체할 키도 없습니다.
- **상태 저장소도 Entra 전용입니다** — Storage 계정은 `allowSharedKeyAccess: false`이고, 관리 ID의 쓰기 권한은 그 계정 하나로 범위가 제한됩니다. 체크포인트는 시크릿이 아니므로 진짜 시크릿이 든 금고에 쓰기 권한을 주지 않습니다.
- **런타임 시크릿은 Key Vault에만** 있습니다. Container App과 스케줄러 Job은 관리 ID로 참조만 하며, 템플릿 출력이나 API 응답에 값이 나타나지 않습니다.
- **관리자 콘솔은 이중 조건**을 모두 만족해야 열립니다 — Entra 앱 등록(`adminEntraClientId` + secret)과 명시적 허용 목록(`adminAllowedPrincipals`). 하나라도 비어 있으면 `/admin`은 404를 반환합니다.
- **오케스트레이터 API는 생성된 키**로 보호되며, 필요하면 `allowedIpRanges`로 인그레스를 CIDR 단위로 제한할 수 있습니다.
- **구독 전체 Reader 권한은 자동 부여하지 않습니다.** 템플릿은 리소스 그룹 범위 권한만 만들고, 더 넓은 범위는 관리자가 의도적으로 부여하도록 명령어를 출력합니다.

### Network isolation (`networkIsolationMode`)

| 값 | 무엇이 달라지나 | 언제 고르나 |
|----|----------------|------------|
| `vnetInjection` **(기본)** | Foundry 에이전트 컴퓨트가 위임 서브넷에 주입되고, Container Apps 환경이 같은 VNet에 통합되며, Foundry · Key Vault · 상태 계정은 **프라이빗 엔드포인트로만** 열립니다 | 엔터프라이즈 기본값. 트래픽이 VNet 밖으로 나가면 안 되는 환경 |
| `perimeter` | 엔드포인트는 공개로 두되 Foundry · Key Vault · Log Analytics · 상태 계정을 **Network Security Perimeter**로 묶어 유출 경로를 차단합니다 | VNet을 새로 둘 수 없거나 PaaS 경계만 필요할 때 |
| `public` | 엔드포인트는 공개이고 Entra 토큰 · API 키 · 허용 목록이 유일한 경계입니다 | 검증 · 데모 환경 전용 |

> **기본값이 `vnetInjection`인 이유:** Foundry의 네트워크 주입은 **계정을 만들 때만** 설정됩니다.
> `public`으로 배포한 계정은 나중에 주입으로 바꿀 수 없어 삭제 후 purge가 필요하므로, 되돌리기
> 어려운 쪽을 기본값으로 둡니다.

**`vnetInjection`이 추가로 만드는 리소스**

| 리소스 | 이름 | 비고 |
|--------|------|------|
| Virtual Network | `vnet-{baseName}-{suffix}` | `existingVnetResourceId`를 주면 기존 VNet을 그대로 사용 |
| Foundry 에이전트 서브넷 | `snet-foundry-agent` (`/24`) | `Microsoft.App/environments`에 위임, Foundry 계정 하나가 독점 |
| Container Apps 서브넷 | `snet-container-apps` (`/24`) | `Microsoft.App/environments`에 위임, 워크로드 프로필 환경 |
| 프라이빗 엔드포인트 서브넷 | `snet-private-endpoints` (`/27`) | 위임 없음 |
| Private DNS 존 5개 | `privatelink.services.ai.azure.com` · `privatelink.openai.azure.com` · `privatelink.cognitiveservices.azure.com` · `privatelink.vaultcore.azure.net` · `privatelink.blob.core.windows.net` | VNet에 연결 |
| Private Endpoint 3개 | `pe-aif-…` · `pe-kv-…` · `pe-st…` | Foundry(`account`) · Key Vault(`vault`) · Storage(`blob`) |
| Foundry 프로젝트 capability host | `caphostproj` | 네트워크 주입 계정에 필요 |

- **주소 공간은 RFC1918이어야 합니다.** Foundry 에이전트 서브넷은 `10.0.0.0/8` · `172.16-31.0.0/12` · `192.168.0.0/16` 밖의 범위를 거부합니다.
- **Key Vault와 상태 계정은 `publicNetworkAccess: Disabled`가 됩니다.** Container App과 스케줄러 Job은 관리 ID로 프라이빗 엔드포인트를 통해 시크릿과 체크포인트를 읽고 쓰며, 템플릿이 선언한 시크릿 쓰기는 신뢰할 수 있는 서비스 예외로 계속 동작합니다.
- **기존 VNet을 쓸 때는** 위 세 서브넷이 이미 존재하고 위임까지 끝나 있어야 합니다. 템플릿은 남의 서브넷 정책을 덮어쓰지 않습니다.
- **`internalIngressOnly: true`** 로 두면 인그레스가 VNet 전용이 되고 환경 기본 도메인을 가리키는 Private DNS 존이 자동으로 만들어집니다. 스케줄러가 Container Apps Job이라 **매일 실행은 그대로 동작합니다** — Job은 앱을 HTTP로 부르지 않고 직접 분석하기 때문입니다. 다만 `/admin`과 `/api/*`는 VNet 안에서만 접근됩니다.

**`perimeter`가 추가로 만드는 리소스**

| 리소스 | 이름 | 비고 |
|--------|------|------|
| Network Security Perimeter | `nsp-{baseName}-{suffix}` | |
| Profile | `azbrief` | 인바운드 · 아웃바운드 규칙 묶음 |
| 인바운드 규칙(구독) | `inbound-subscriptions` | 기본값은 배포 구독 — Container App이 Foundry를 호출할 수 있게 하는 규칙 |
| 인바운드 규칙(IP) | `inbound-ip` | `perimeterInboundIpRanges`를 채웠을 때만 |
| 아웃바운드 규칙(FQDN) | `outbound-fqdn` | 기본값 `azure.microsoft.com` · `learn.microsoft.com` |
| 리소스 연결 4건 | `assoc-foundry` · `assoc-keyvault` · `assoc-loganalytics` · `assoc-storage` | |
| 진단 설정 | `nsp-access-logs` | `NSPAccessLogs`를 Log Analytics로 |

- **기본 모드는 `Learning`(Transition)** 이라 차단하지 않고 기록만 합니다. `NSPAccessLogs` 테이블에서 거부될 뻔한 호출을 확인한 뒤 `perimeterAccessMode: Enforced`로 재배포하거나 출력의 `enforcePerimeterCommand`를 실행하세요.
- Container Apps · Communication Services는 아직 NSP에 온보딩되지 않았습니다. 이 앞단은 계속 인그레스 IP 제한과 API 키가 지킵니다.

### Post-deployment steps

1. **Reader 역할 부여** — 배포 출력의 `grantReaderCommand`를 그대로 실행합니다. 이 권한이 없으면 Resource Graph 조회가 빈 결과를 돌려주고 영향도 분석이 무의미해집니다.
2. **컨테이너 이미지 배포** — 템플릿은 자리표시자 이미지로 시작합니다. `deployContainerImageCommand` 출력을 사용하거나 `deploy-container-app.yml` 워크플로를 실행하세요. 워크플로는 Container App과 스케줄러 Job을 **함께** 갱신합니다.
3. **Foundry Prompt Agent 생성** — ARM은 Agent 데이터 플레인 객체를 만들 수 없습니다. `.env`에 프로젝트 endpoint, primary/phase Agent 이름, 프로비저닝 모델을 설정한 뒤 실행합니다:
   ```bash
   python -m scripts.provision_foundry_agents --dry-run   # 지시문 미리보기
  python -m scripts.provision_foundry_agents             # 생성 또는 갱신
   ```
  `planner`·`evaluator`·`reporter`·`codex`·`fast`는 각각 비워 두면 primary Agent를 사용합니다. 필수 Agent가 없거나 evaluation Agent가 실패하면 분석은 `model_error`로 종료되며 다른 모델 endpoint로 우회하지 않습니다.
4. **Enrichment 준비 확인** — research에는 documentation/web 도구를, impact에는 Azure resource 도구를 Foundry에서 붙인 뒤 읽기 전용 검증을 실행합니다:
  ```bash
  python -m scripts.provision_foundry_agents --check
  ```
  Agent 누락, instruction drift, research/impact의 도구 누락이 하나라도 있으면 실패합니다. 그 뒤에만 `enableFoundryEnrichmentAgents=true`로 재배포하세요. 기본값은 `false`입니다.
5. **(선택) 관리자 콘솔 활성화** — Entra 앱을 등록한 뒤 `adminEntraClientId` · `adminEntraClientSecret` · `adminAllowedPrincipals`를 채워 재배포합니다.

> **검증 범위:** 템플릿은 Bicep 타입 검사(리소스 종류·API 버전·속성명)를 통과했습니다. 구독에 대한
> ARM 프리플라이트(`az deployment group validate`)는 개발 환경의 MFA 요구로 실행하지 못했으므로,
> 첫 배포 전에 직접 한 번 실행해 보시기를 권합니다.

### Scheduling operations

| 하고 싶은 일 | 방법 |
|-------------|------|
| 실행 시각 변경 | `scheduleCronExpression` (UTC cron, 기본 `0 2 * * *`) 로 재배포 |
| 지금 바로 실행 | 배포 출력의 `runNowCommand` — `az containerapp job start`, 또는 `/admin`의 실행 버튼 |
| 한 실행의 최대 시간 조정 | `jobReplicaTimeoutSeconds` (기본 12시간, 최대 7일). `RUN_TIME_BUDGET_S`는 그보다 1시간 짧게 자동 설정됩니다 |
| 분석 범위를 되돌리기 | 출력의 `checkpointBlobUrl` blob을 삭제하면 기본 윈도우(24시간)로 돌아갑니다 |
| 실행 기록 확인 | Log Analytics 또는 `az containerapp job execution list` |

> Job은 재시도하지 않습니다(`replicaRetryLimit: 0`). 실패한 실행은 체크포인트를 옮기지 않았기
> 때문에 다음 스케줄이 같은 구간을 다시 다룹니다 — 같은 밤에 동일한 분석 비용을 두 번 치르지
> 않으려는 선택입니다.

<p align="right">(<a href="#azbrief-enterprise">back to top</a>)</p>

## Multi-agent pipeline

Every model-mediated call uses a persisted Foundry Prompt Agent. Optional role overrides
separate phase instructions, model selection, guardrails, and traces; every unset role falls
back to `FOUNDRY_PRIMARY_AGENT_NAME`:

```env
FOUNDRY_PRIMARY_AGENT_NAME=azbrief-primary
FOUNDRY_PLANNER_AGENT_NAME=azbrief-planner      # optional; primary when empty
FOUNDRY_EVALUATOR_AGENT_NAME=azbrief-evaluator  # optional; primary when empty
FOUNDRY_REPORTER_AGENT_NAME=azbrief-reporter    # optional; primary when empty
FOUNDRY_CODEX_AGENT_NAME=azbrief-codex  # optional; primary when empty
FOUNDRY_FAST_AGENT_NAME=azbrief-fast    # optional; primary when empty
```

`FOUNDRY_ENRICHMENT_AGENTS` optionally adds a staged pre-analysis pipeline. The ARM template
keeps it disabled until `enableFoundryEnrichmentAgents=true`; each stage maps to an Agent
governed in Foundry.

```bash
FOUNDRY_ENRICHMENT_AGENTS='[{"name":"azbrief-research","stage":"research"},
                            {"name":"azbrief-impact","stage":"impact"},
                            {"name":"azbrief-action","stage":"action"},
                            {"name":"azbrief-review","stage":"review"}]'
```

| Stage | Runs | Job |
|---|---|---|
| `research` | in parallel with `impact` | What actually changed: release stage, dates, prerequisites, official docs |
| `impact` | in parallel with `research` | Which resources, configurations and regions in *this* tenant are touched |
| `action` | after both | Concrete, self-serviceable next steps grounded in the two findings |
| `review` | last | Audits the findings and flags unsupported claims; a clean review adds nothing |

Every enrichment stage is **optional and independently fault-isolated** — a missing, failing
or timed-out stage contributes nothing. Stage output is strict JSON with stable claim IDs,
evidence, confidence, and gaps. Review rejection removes the rejected claim and every action
that depends on it before context reaches the planner. This does not apply to required runtime
Agents, which fail closed.

Tool wiring (Bing/Web search, Azure MCP, Microsoft Learn MCP, memory) lives **in the Foundry
portal or SDK**, referenced here only by agent name — so the agents stay governable and the
application stays version-robust. The managed identity needs **Foundry User** on the project,
which the template grants.

Create or update the roster with:

```bash
python -m scripts.provision_foundry_agents --dry-run          # review the instructions
python -m scripts.provision_foundry_agents                    # runtime + enrichment agents
python -m scripts.provision_foundry_agents --runtime-roles primary codex
python -m scripts.provision_foundry_agents --stages review    # one stage only
python -m scripts.provision_foundry_agents --check            # read-only readiness check
python -m scripts.provision_foundry_agents --delete           # tear the roster down
```

Each agent's standing instructions are **derived from** the runtime prompt in
`src/agent/foundry_backend.py`, so an agent's role and the message it receives per run can
never drift apart. A failure on one stage is reported and the rest still run.

Foundry versioned Skills and toolbox skill discovery are public preview, so AzBrief does not
make them a production prerequisite. When adopted, skills must be versioned and loaded through
the toolbox MCP resource discovery flow rather than copied into every Agent instruction.

The whole path is **read-only** with respect to your Azure resources. Model selection,
server-side tools, guardrails, and memory live on the Foundry Agent definitions; AzBrief
never constructs an Azure OpenAI/OpenAI chat client.

<p align="right">(<a href="#azbrief-enterprise">back to top</a>)</p>

## Admin console

`https://<container-app>/admin` 에서 구성 상태, 구독자, 최근 Azure 업데이트, 실행 이력을
확인하고 분석을 즉시 실행할 수 있습니다.

| 경로 | 설명 |
|------|------|
| `GET /admin` | 관리 콘솔 (서버 렌더링, 외부 리소스 없음, nonce 기반 CSP) |
| `GET /api/admin/status` | 유효 구성 요약 — 시크릿은 포함하지 않음 |
| `GET /api/admin/subscribers` | 구독자 목록 |
| `GET /api/admin/updates` | 최근 Azure 업데이트 |
| `GET /api/admin/runs` · `GET /api/admin/runs/{id}` | 실행 이력 및 단건 조회 |
| `POST /api/admin/runs` | 분석 실행 시작 (동시 실행 1건으로 제한) |

인증은 Container Apps 기본 제공 인증(EasyAuth)이 처리합니다. 사이드카가 Entra ID 토큰을
검증한 뒤 `X-MS-CLIENT-PRINCIPAL*` 헤더를 주입하며, 외부에서 들어온 동일 헤더는 제거합니다.
애플리케이션은 그 신원을 `ADMIN_ALLOWED_PRINCIPALS` 허용 목록과 대조합니다 — 목록이 비어
있으면 인증된 사용자라도 거부됩니다. 콘솔이 꺼져 있으면 `/admin`은 403이 아니라 **404**를
반환합니다. 잠긴 배포는 그 표면이 존재한다는 사실조차 알리지 않아야 하기 때문입니다.

플랫폼 인증은 **AllowAnonymous** 모드로 구성됩니다. 플랫폼 수준의 "인증 필수"는 예외 없이
*모든* 요청에 적용되어 API 키 호출까지 로그인 페이지로 돌려보내기 때문입니다. 대신 사이드카는
제시된 토큰만 검증하고, 인가는 애플리케이션이 수행합니다 — 로그인하지 않은 브라우저가
`/admin`에 접근하면 애플리케이션이 `/.auth/login/aad`로 리디렉션하고, `/api/*`는 API 키로
보호됩니다.

`ADMIN_REQUIRE_AUTH=false`는 로컬 개발 전용입니다. 인그레스가 유일한 접근 경로가 아닌 곳에서
이 값을 끄면 헤더 위조가 가능해집니다.

<p align="right">(<a href="#azbrief-enterprise">back to top</a>)</p>

## How the analysis works

Each update runs through a [LangGraph](https://github.com/langchain-ai/langgraph) state
machine, optionally preceded by the Foundry multi-agent enrichment above:

1. **Plan** — Reads the update, searches related docs, builds an investigation plan
2. **Execute** — Runs tools in parallel: Resource Graph queries, Learn searches, cost
   lookups, Advisor recommendations, resource health, policy compliance, region availability
3. **Evaluate** — Checks completeness; re-plans if coverage is insufficient (up to 2 revisions)
4. **Report** — Synthesizes findings into a structured analysis with action items
5. **Classify** — Assigns three independent metrics: importance, impact and job relevance
6. **Customize** — Rewrites the report for each subscriber's role and language, in parallel

If a Resource Graph query fails, the agent rewrites and retries it (up to 20 times). A query
that succeeds but returns nothing from an over-strict filter is probed against real data and
corrected, rather than being accepted as "no affected resources".

Tool results larger than the prompt budget are not discarded: the full text is kept in an
addressable store and the agent retrieves the rest with `query_tool_result`, so a needle past
the cutoff can still be found — and an absence can be *confirmed* instead of assumed.

<p align="right">(<a href="#azbrief-enterprise">back to top</a>)</p>

## Per-subscriber reports

Each subscriber gets the same update rewritten for their role and language:

```json
[
  {"email": "infra@co.com", "name": "Alice", "role": "VM and networking", "language": "en"},
  {"email": "sec@co.com",   "name": "Bob",   "role": "Security & compliance", "language": "en"},
  {"email": "ops@co.com",   "name": "Carol", "role": "Cloud Architect", "language": "ko"}
]
```

Set this as the `SUBSCRIBERS` environment variable (the ARM template takes it as the
`subscribers` parameter).

### Adding a language

`src/i18n/` is the single source of truth — nothing else in the codebase enumerates language
codes. Adding one is a single registry line; everything else is optional.

1. **Register it** in `src/i18n/__init__.py`:
   ```python
   register_language(LanguageSpec(code="fr", english_name="French", native_name="Français"))
   ```
   The language is now selectable via `REPORT_LANGUAGE` or a subscriber's `language` field.
2. **Translate the UI labels** (optional) — add `src/i18n/labels/fr.py` with a `LABELS` dict.
   A partial translation is safe: missing keys fall through the fallback chain, so a missing
   key can never raise `KeyError` at render time.
3. **Write a style guide** (optional) — add `src/agent/prompts/languages/fr.py` with
   `STYLE_GUIDE` and, if useful, `TRANSLATION_NOTES`.

Regional tags normalize automatically (`fr-FR` → `fr`), and `missing_label_keys("fr")`
reports what is still untranslated.

<p align="right">(<a href="#azbrief-enterprise">back to top</a>)</p>

## Configuration

<details>
<summary>Environment variables</summary>

| Variable | Description | Required | Default |
|----------|-------------|:--------:|---------|
| `AZURE_TENANT_ID` | Tenant ID | Yes | — |
| `AZURE_CLIENT_ID` | User-assigned managed identity client ID; empty with local `az login` | | — |
| `AZURE_SUBSCRIPTION_ID` | Subscription (all if unset) | | — |
| `FOUNDRY_PROJECT_ENDPOINT` | Foundry project endpoint | Yes | — |
| `FOUNDRY_PRIMARY_AGENT_NAME` | Required Agent for judging, safety verification, and role fallback | Yes | — |
| `FOUNDRY_PLANNER_AGENT_NAME` | Analysis planning Agent | | primary Agent |
| `FOUNDRY_EVALUATOR_AGENT_NAME` | Evidence-completeness evaluation Agent | | primary Agent |
| `FOUNDRY_REPORTER_AGENT_NAME` | Final report and output-recovery Agent | | primary Agent |
| `FOUNDRY_CODEX_AGENT_NAME` | KQL generation/repair Agent | | primary Agent |
| `FOUNDRY_FAST_AGENT_NAME` | Lightweight revision/customization Agent | | primary Agent |
| `FOUNDRY_MODEL_DEPLOYMENT` | Model used only when provisioning Agent definitions | * | — |
| `FOUNDRY_ENRICHMENT_AGENTS` | Optional pre-analysis multi-agent roster (JSON) | | — |
| `FOUNDRY_AGENT_TIMEOUT_S` | Per-agent timeout | | `180` |
| `CHECKPOINT_BLOB_URL` | Blob holding the digest checkpoint | | — |
| `CHECKPOINT_FILE_PATH` | Local checkpoint file, used only when the blob URL is unset | | — |
| `RUN_TIME_BUDGET_S` | Wall-clock budget for one run; keep below the job replica timeout | | `39600` |
| `MAX_CONCURRENT_ANALYSES` | Updates analyzed in parallel | | `3` |
| `ORCHESTRATOR_ENDPOINT` | Container App URL an external scheduler calls (https only) | | — |
| `ORCHESTRATOR_API_KEY` | Key an external scheduler presents as `X-API-Key` | | — |
| `API_KEY` | Key required by every `/api/*` route | | — |
| `ADMIN_UI_ENABLED` | Serve `/admin` and `/api/admin/*` | | `false` |
| `ADMIN_REQUIRE_AUTH` | Require an authenticated principal (local dev only when `false`) | | `true` |
| `ADMIN_ALLOWED_PRINCIPALS` | Comma-separated UPN/object-ID allow-list (empty denies all) | | — |
| `COMMUNICATION_SERVICES_CONNECTION_STRING` | Email service | | — |
| `COMMUNICATION_SERVICES_ENDPOINT` | Email via managed identity (no stored secret) | | — |
| `EMAIL_SENDER_ADDRESS` / `EMAIL_RECIPIENT_ADDRESS` | From / fallback To address | | — |
| `SUBSCRIBERS` | Subscriber list (JSON) | | — |
| `REPORT_LANGUAGE` | Default report language | | `ko` |
| `LOG_ANALYTICS_WORKSPACE_ID` | Log Analytics workspace for operational queries | | — |
| `CUSTOM_SYSTEM_PROMPT` | Extra analysis instructions | | — |
| `LOG_LEVEL` | Log level | | `INFO` |
| `REPORT_FILTERING_ENABLED` | Suppress `not_relevant` reports from email (`false` = deliver all) | | `false` |
| `REQUIRE_APPROVAL_BEFORE_SEND` | Withhold auto-dispatch; save preview + log for human approval | | `false` |
| `GEVAL_ENABLED` | Enable the G-Eval quality judge | | `true` |
| `GEVAL_TARGET_SCORE` | Passing score on the 1-5 scale | | `4.5` |
| `GEVAL_RUNTIME_ENABLED` | Judge + one rewrite inside `analyze_update` | | `false` |
| `TRAJECTORY_EVAL_ENABLED` | Rule-based agent process-quality score after each analysis | | `true` |
| `ACTION_VERIFICATION_ENABLED` | Three-layer action-item safety gate | | `true` |
| `COMMUNITY_INSIGHTS_ENABLED` | Azure Weekly practitioner commentary | | `true` |
| `OTEL_ENABLED` | OpenTelemetry tracing to Application Insights | | `false` |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | App Insights connection string for span export | | — |

\* `FOUNDRY_MODEL_DEPLOYMENT` is required by `scripts.provision_foundry_agents.py` unless
`--model` is supplied. It is not read by the running application.

</details>

<p align="right">(<a href="#azbrief-enterprise">back to top</a>)</p>

## API

```
POST /api/analyze                  Analyze an Azure Update URL
POST /api/rss/check                List updates not yet processed
POST /api/batch/analyze            Analyze up to 10 URLs
GET  /health                       Health check
GET  /                             Service info

GET  /admin                        Admin console (Entra ID sign-in)
GET  /api/admin/status             Effective configuration — no secrets
GET  /api/admin/subscribers        Subscriber list
GET  /api/admin/updates            Recent Azure updates
GET  /api/admin/runs               Run history
GET  /api/admin/runs/{id}          Single run
POST /api/admin/runs               Start a run (one at a time)
```

Every `/api/*` route requires the `X-API-Key` header when `API_KEY` is set, and is rate
limited per source IP.

<p align="right">(<a href="#azbrief-enterprise">back to top</a>)</p>

## Development

### Running tests

```bash
python -m pytest tests/ -o "addopts=" -q      # full suite
python -c "import src"                        # import check — must pass before committing
```

### Container image

```bash
docker build -t azbrief-enterprise:local .
docker run -p 8000:8000 --env-file .env azbrief-enterprise:local          # orchestrator API
docker run --env-file .env azbrief-enterprise:local python -m src.scheduler  # one digest run
```

### Infrastructure

`infra/azbrief-enterprise-deploy.json` is **compiled output** — edit the Bicep and recompile:

```bash
az bicep build --file infra/enterprise/main.bicep \
  --outfile infra/azbrief-enterprise-deploy.json
```

CI fails when the compiled template drifts from the Bicep source, because the Deploy button
points at the JSON.

### Report quality

```bash
# Generate a real-data report, score it, and iterate toward the target
python -m scripts.evaluate_report --latest --with-html --iterate 3

# Rule-based mechanical scoring only (no LLM judge)
python -m scripts.evaluate_report --latest --no-geval
```

Fleet-level measurement stratifies updates across categories with a fixed seed, so the same
seed selects the same updates and a before/after comparison stays valid:

```bash
python -m scripts.evaluate_batch --months 6 --count 12 --seed 42
```

<p align="right">(<a href="#azbrief-enterprise">back to top</a>)</p>

## Project structure

```
AzBriefEnterprise/
├── src/
│   ├── config.py               # pydantic-settings (env → Settings)
│   ├── main.py                 # FastAPI app — orchestrator API + /admin
│   ├── scheduler.py            # Container Apps Job entry point
│   ├── orchestrator.py         # Run registry, watermark cursor, checkpoint commit
│   ├── middleware.py           # API key auth + per-IP rate limiting
│   ├── admin/                  # Admin console (auth, page, router)
│   ├── agent/                  # LangGraph agent, tools, prompts
│   │   ├── analyzer.py         # Plan-Execute-Evaluate state machine
│   │   ├── foundry_backend.py  # Foundry Prompt Agent adapter + enrichment pipeline
│   │   ├── tools.py            # Tool definitions (LangChain BaseTool)
│   │   ├── context_store.py    # Addressable store for oversized tool results
│   │   ├── action_verification.py  # Three-layer action-item safety gate
│   │   ├── geval.py            # G-Eval LLM-as-a-Judge report quality
│   │   ├── resilience.py       # Retry, circuit breaker, run deadline
│   │   └── prompts/            # Phase-specific prompt assembly package
│   ├── i18n/                   # Language registry (single source of truth)
│   ├── rss/                    # Azure Update RSS parser
│   ├── email/                  # EmailService + HTML templates
│   └── services/               # Azure SDK data access (incl. checkpoint.py)
├── infra/
│   ├── enterprise/main.bicep           # source of truth — edit here
│   ├── enterprise/modules/             # modules inlined into the compiled template
│   └── azbrief-enterprise-deploy.json  # compiled ARM template (Deploy button)
├── scripts/                    # Local CLI, crawler, Foundry agent provisioning, quality eval
├── tests/
├── Dockerfile
├── pyproject.toml
└── requirements.txt
```

## Tech stack

| Area | Technology |
|------|-----------|
| Language | Python 3.10+ |
| AI framework | `langchain-core`, `langgraph`, `azure-ai-projects`, `azure-ai-agents` |
| Models | Microsoft Foundry Agent Service only |
| Web framework | FastAPI + Uvicorn |
| Settings | pydantic-settings |
| Logging | structlog (JSON) + OpenTelemetry → Application Insights |
| Azure SDKs | `azure-identity`, `azure-mgmt-resourcegraph`, `azure-mgmt-costmanagement`, `azure-communication-email`, `azure-monitor-query` |
| HTTP | httpx (async) |
| HTML parsing | BeautifulSoup4 with `html.parser` (stdlib — **not** lxml) |
| IaC | Bicep → ARM |
| Compute | Container Apps Job (schedule) + Container App (API/admin) |
| CI/CD | GitHub Actions |

<p align="right">(<a href="#azbrief-enterprise">back to top</a>)</p>

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Analysis reports no affected resources | The identity has no Reader on the subscription | Run the `grantReaderCommand` deployment output |
| `/admin` returns 404 | The console is disabled | Supply `adminEntraClientId` + secret **and** `adminAllowedPrincipals`, then redeploy |
| Startup fails with `FOUNDRY_PRIMARY_AGENT_NAME is required` | Foundry runtime settings are incomplete | Set the project endpoint and exact published primary Agent name |
| Foundry Agent returns no completed response | Agent name, RBAC, network access, or Agent run failed | Verify the Agent in Foundry and grant the identity `Foundry User`; there is no model-endpoint fallback |
| A dedicated KQL Agent is unavailable | `FOUNDRY_CODEX_AGENT_NAME` points to a missing Agent | Fix the name, or leave it empty so KQL uses the primary Agent |
| Cannot switch to `vnetInjection` | Foundry network injection is create-time only | Delete **and purge** the Foundry account, then redeploy |
| The nightly digest runs an old build | The job was not updated with the app | Redeploy via `deploy-container-app.yml`, which now updates both |
| Email is printed to the console instead of sent | No Communication Services configuration | Set `COMMUNICATION_SERVICES_ENDPOINT` (managed identity) or the connection string |
| A window was analysed twice | A previous run failed before committing | Expected: the checkpoint only advances after a completed run |

## License

MIT — see [LICENSE](LICENSE).
