# Customer Deployment

[English overview](../README.md#deployment) | [한국어 개요](../README.ko.md#배포)

The README **Deploy to Azure** button creates the foundation, not a working installation.
Foundry Agent versions and the authenticated Azure MCP server require a separate setup phase.
The initial hello-world application uses port 80 and `/`; the Job stays **Manual**. The setup
script installs the real image on port 8000 with `/health`, then enables scheduling only after
readiness checks and explicit operational acceptance. A foundation success, HTTP 200, or a Job
exit code alone is not proof that analysis, archive storage, and email work.

## Model Policy

Core roles default to `gpt-5-terra` with `medium` reasoning. Only Azure MCP defaults to
`gpt-5-luna`, with no reasoning option supplied. Report writing, subscriber customization, KQL/API
interpretation, and quality/safety review remain on the core tier. The Portal suggests these names
but requires approved versions for both; the raw parameter example leaves versions empty rather
than inventing them. Validate the actual model IDs, reasoning options, Responses/tool/strict-JSON
support, region, SKU, quota and cost before deployment. Default names are not a compatibility or
performance guarantee. Run paired quality/latency/cost comparisons before production promotion.

`modelDeploymentName`, `modelName`, `modelVersion`, `modelSkuName`, and `modelCapacity` configure
the core deployment. Matching `simpleModel*` parameters configure a separate simple-task deployment;
the two deployment names must differ. `coreReasoningEffort` accepts `low`, `medium`, or `high`.
Capacity units must be assessed independently. The models deploy serially before the private
endpoint and project, and Admin readiness checks both deployments without invoking a model.

`customerSetup` v2/v3 binds both model deployments and effort and clears the legacy global model
override in the named customer environment. Existing v1 outputs remain supported as explicit
single-model installations. Do not reapply the foundation merely to change an existing Agent's
model; preserve existing resources/secure parameters and use reviewed provisioning configuration.
`--check` detects expected model/reasoning/sampling drift but does not replace a live model smoke.

## Shared Infrastructure

New installations use one Container Apps environment, one Application Insights component, one
Log Analytics workspace, and one Direct DCR targeting its `AzBriefFailures_CL` table. The
control-plane App and Job share their existing user-assigned identity and receive DCR-scoped
`Monitoring Metrics Publisher`; Azure MCP remains a separate app with its own system-assigned
identity and subscription Reader. The MCP template creates no additional environment, Application
Insights component, workspace, failure table, or DCR.

`customerSetup` v3 adds `containerAppEnvironmentId`, `containerAppWorkloadProfileName` and
`applicationInsightsName`. Current v3 outputs also carry the non-secret DCR endpoint, immutable ID,
resource ID, stream, and table name; older v3 contracts without those optional fields remain valid.
The installer binds the DCR endpoint/ID/stream into the Hosted Agent environment and binds the
environment/workload/Insights values into the MCP azd environment. The `Consumption` workload
profile is used for VNet injection; consumption-only environments use an empty profile. No
connection string or credential is added to the public setup contract. The `Mcp` stage requires
v3; legacy v1/v2 outputs remain usable for non-MCP stages.

Application Insights retains `DisableLocalAuth: true`. The pinned MCP
[3.0.0-beta.38 exporter](https://github.com/microsoft/mcp/blob/Azure.Mcp.Server-3.0.0-beta.38/core/Microsoft.Mcp.Core/src/Extensions/OpenTelemetryExtensions.cs)
sets a connection string but not a `TokenCredential`. Passing the shared connection string alone
would not satisfy Entra-only ingestion. Therefore MCP direct Application Insights traces/metrics
and Microsoft telemetry are disabled. MCP console logs still reach the shared workspace through
the environment's log configuration; distinguish them by Container App name. No extra collector
or telemetry-publishing role is added. AzBrief uses its authenticated exporter for traces and
redacted application errors; this does not enable direct MCP telemetry.
MCP trace/metric export would require a separately reviewed authenticated exporter or collector.

## Single Storage Account

New standard and KT deployments create **one dedicated StorageV2 / Standard_LRS account**.
Application data and Foundry data use different private Blob containers:

| Container | Owner and purpose |
|---|---|
| `azbrief-state` | App/Job checkpoint, Admin configuration, scheduler leases and feedback |
| `azbrief-archive` | App/Job create-only canonical reports and search metadata |
| Foundry-managed containers | Evaluation datasets/results in the standard profile; agent files and system data in KT. Foundry chooses the container names and lifecycle. |

The standard account keeps its existing `st{baseName}{suffix}` name and application URLs.
The `steval{baseName}{suffix}` **connection name** is retained but points to the shared account;
it no longer creates an account. Admin readiness checks one storage resource. In VNet mode one
Blob Private Endpoint serves all containers; in perimeter mode one storage association is used.
No shared keys or public containers are enabled. This removes a redundant endpoint, not the
capacity/transaction charges for the same stored data.

App/Job Blob Data Contributor is assigned separately to the two application containers. Foundry
keeps its required account-scoped Blob Data Owner for evaluation; KT also keeps the standard-setup
account roles. Therefore **this is logical data separation, not a hard boundary against the
Foundry project identity or storage administrators**. It shares account throughput, redundancy,
networking and failure scope. Use it only when the workloads belong to the same approved trust
boundary; do not apply it where policy requires independent account isolation.
Blob-service retention is shared too: KT preserves seven-day blob/container soft delete, which
also applies to Foundry-managed containers in the shared account.
See [Blob RBAC scopes](https://learn.microsoft.com/azure/storage/blobs/assign-azure-role-data-access),
[evaluation storage requirements](https://learn.microsoft.com/azure/foundry/concepts/evaluation-regions-limits-virtual-network#bring-your-own-storage)
and [standard agent permissions](https://learn.microsoft.com/azure/foundry/agents/concepts/standard-agent-setup).

**An existing two-account deployment is not automatically migrated.** Incremental ARM deployment
does not delete the old account, Private Endpoint, connection data or account-scoped role grants.
Before reapplying the new foundation, obtain a migration/rollback approval, stop new runs and
drain writers, inventory all dataset/file references, and back up the existing data and metadata.
Keep the standard state account; for KT, prefer the existing Foundry backing account because an
active Capability Host cannot be silently redirected. Copy required application blobs without
changing canonical bytes or metadata, and verify the checkpoint/configuration and archive listing.
Copying Foundry blobs alone does not update existing dataset/result references: retain the source
until a supported migration or deliberate retention plan has been verified.
After cutover, re-read ETags, validate private DNS, App/Job storage access and a real Foundry
evaluation or KT agent-file operation. Explicitly remove obsolete broad App/Job grants only after
the container grants work. Delete old accounts/endpoints/roles only after dependency checks,
retention requirements, rollback acceptance and separate approval. KT preflight rejects legacy
two-name input files and additional KT-profile storage accounts rather than silently selecting,
creating or deleting an account. Local compilation/tests do not perform this migration or prove
live Foundry permissions and networking.

## Setup Stages

Run every stage from the same reviewed customer checkout with explicit `SubscriptionId`,
`ResourceGroup`, `DeploymentName`, and `Environment`. The normal `-WhatIf` path still reads ARM
outputs; it previews the target and stage without proving live readiness. Only
`-SetupFile <file> -WhatIf` uses a local `customerSetup` object without Azure reads.

| Stage | Required before running | What completion establishes |
|---|---|---|
| `Configure` | Succeeded current foundation deployment, correct default CLI tenant/subscription, clean customer clone without a root `.env` | Named azd bindings match the non-secret ARM setup contract; no application is deployed by this stage |
| `Mcp` | `Configure`, v3 setup contract, shared infrastructure ready, private Foundry connectivity, approved Graph/resource permissions | Separate pinned read-only MCP app in the shared environment and authenticated project connection; existing connections are not force-replaced |
| `Agents` | MCP URL recorded, clean reviewed source, model and Foundry access | Import/full tests and roster check pass, and the intended Hosted name is active; evidence permissions and analysis acceptance remain separate |
| `Application` | Manual Job, approved immutable ACR digest and matching App/Job registry authentication (ManagedIdentity or Credentials) | Paired image and bootstrap port/probe changes are submitted and read back; revision health is checked in `Verify` |
| `Verify` | New App revision Healthy/Running, same App/Job digest, configured customer environment | Runtime/roster/health checks pass without model calls or email; it does not prove a canonical archive write or recipient inbox delivery |
| `EnableSchedule` | `Verify` plus operator-owned analysis/archive/auth/email acceptance and `-AcceptOperationalChecks` | Readiness is rechecked and the scheduled Job configuration is read back; the first real digest still needs observation |

`-AcceptOperationalChecks` is the operator's explicit attestation, not an automated execution of
the acceptance checklist. Failures stop the current stage; earlier successful resource changes
are not globally rolled back. Preserve the stage output and inspect partial state before retrying.

## Before The Button

| Required decision | Customer action |
|---|---|
| Release | Use a reviewed commit or release tag. The public `main` button is mutable; for a fixed release, replace `main` with the same reviewed ref in **both** the ARM and UI URLs. Use that same source for image and Hosted builds. |
| Azure target | Choose the customer tenant, subscription, a dedicated resource group, and regional/data-residency policy. Never reuse the maintainer's development azd environment or `.env`. |
| Azure permissions | The deployment operator needs resource creation plus role-assignment rights at the required customer resource-group/subscription scopes. Customer-owned ACR builds and role grants need separate ACR rights; consuming a publisher digest with a pull token does not. |
| Entra permissions | Azure MCP creates an Entra application/service principal and assigns its app role to the Foundry project identity. Obtain customer approval for these Microsoft Graph operations separately; Azure subscription Owner alone is insufficient. Use a dedicated deployment identity approved by the directory administrator. |
| Foundry | Verify Hosted Agents, VNet injection, both exact model/version/SKU combinations, reasoning/tool/schema support, and separate quotas in the selected region. The form requires approved versions for both suggested models. Capacity is model-specific units, not universally thousands of TPM. |
| Residency | Global Standard can process requests outside the account region. Regional Standard and Data Zone Standard have different availability, quota, and residency boundaries. Have the customer approve the selected SKU. |
| Network | Provide a VNet-connected deployment host with private DNS resolution. Ordinary Cloud Shell and a public hosted CI runner are not automatically inside this VNet. Do not open Foundry, Key Vault, or Storage to work around access failures. |
| Registry | Use a same-tenant ACR with ManagedIdentity (default), or a publisher/developer ACR in another tenant with Credentials. Supply its exact login server and ensure customer-network reachability. External consumers need only a customer-specific repository pull token, not build/push rights. |
| Browser access | Recommended: prepare a single-tenant Entra application, client secret and explicit administrator object IDs/UPNs. Enter the secret directly in the Portal password field, never in chat or source control. Add the callback URI after the app hostname is known. |
| Email | Choose the Communication Services data location and one approved acceptance mailbox. Verify service availability, sending limits, and the customer's mail-filtering policy. Production subscribers can be added after acceptance. |
| Cost | Budget for model tokens, Container App minimum replicas, Job runtime, the separate Azure MCP app in the shared environment, private endpoints, storage, registry builds/storage, logs and ACS email. Obtain a customer-specific estimate and budget alert; no fixed monthly price is implied. |

Azure MCP shares the control-plane environment's VNet and public/internal boundary, while retaining
Entra authentication and subscription Reader. VNet integration alone does not make ingress private.
With `internalIngressOnly: true`, MCP's app-level `external: true` allows callers outside the
environment over the VNet, not over the public internet. Verify the actual Foundry managed MCP
call path and private DNS before accepting an internal deployment. Do not remove authentication,
open public access, or broaden identities to work around a connectivity failure.

Resource providers must be available/registered: `Microsoft.App`, `Microsoft.CognitiveServices`,
`Microsoft.Communication`, `Microsoft.KeyVault`, `Microsoft.Network`, `Microsoft.Storage`,
`Microsoft.ManagedIdentity`, `Microsoft.Insights`, and `Microsoft.OperationalInsights`.

### External-Tenant Registry

The control-plane image may live in the developer's tenant while the customer's App, Job,
Key Vault, identities and data remain in the customer tenant. Select **Customer-specific pull
token (including another tenant)** in the Portal's registry authentication control. Existing
same-tenant installations keep **Managed identity (same tenant)** by default.

| Foundation parameter | External-registry value |
|---|---|
| `containerRegistryServer` | Publisher ACR login server, for example `publisher.azurecr.io` |
| `containerRegistryAuthMode` | `Credentials` |
| `containerRegistryUsername` | Customer-specific ACR token name, or an approved pull-only service principal client ID |
| `containerRegistryPassword` | Secure password input, entered directly in the Portal or supplied through a protected ARM parameter reference |

The publisher creates a scope map limited to the `azbrief-enterprise` repository with
`content/read` and, when tag/manifest inspection is needed, `metadata/read`. Create a separate
token for each customer, select that scope map, generate its password in the ACR Portal, and set
an approved expiry and rotation owner. Keep the ACR administrator account disabled. Do not use
registry-wide scope maps or give consumers write/delete permissions. Non-Entra repository tokens
are available in all ACR tiers and do not require a customer identity or subscription in the
publisher tenant. A publisher service principal is an alternative only with the correct
read-only RBAC/ABAC permissions; never try to grant the customer's Managed Identity directly
across tenants. `containerRegistryRoleAssignmentMode` is unused in Credentials mode.

The foundation stores the password as `container-registry-password` in the **customer's**
existing Key Vault. Both App and Job use `username`/`passwordSecretRef`; their existing UAMI reads
the Key Vault reference, not the publisher ACR. The password is not an application environment
variable. `customerSetup` adds only the authentication mode and ordinary Key Vault base URI;
it carries no registry password, username or secret path. Older contracts without the mode
continue to mean ManagedIdentity. Customer setup checks both runtime bindings without retrieving
the password. Never put it into chat, `.env`, committed parameters or command-line arguments.

Authentication does not provide connectivity. Verify DNS and outbound HTTPS from the customer
Container Apps environment to the registry login endpoint and its documented image-data/storage
endpoints. An ACR firewall must permit the actual customer egress, or use a separately approved
Private Link/DNS design. This template does not create an external ACR, private endpoint,
cross-tenant consent, firewall exception or tenant switch. Do not make customer Key Vault,
Foundry or Storage public to bypass a registry connectivity problem.

For rotation, keep the old token password valid while the publisher creates the alternate
password. Update the customer's Key Vault secret through an approved secret-management channel.
The foundation uses a versionless reference; allow secret refresh and verify a fresh App image
pull and import-only Job execution with the new credential before revoking the old one. Cached
images or a still-running revision do not prove new pull authentication. Retain approved current
and rollback digests, and rotate before expiry.

References: [ACR repository-scoped tokens](https://learn.microsoft.com/azure/container-registry/container-registry-token-based-repository-permissions)
and [Container Apps registry authentication](https://learn.microsoft.com/azure/container-apps/containers#container-registries).

## 1. Deploy The Foundation

Open the README button and complete **Basics**, **Foundry and models**, **Email delivery**, and
**Console access**. Keep the bootstrap image for the first deployment. The form fixes
`networkIsolationMode=vnetInjection`, `allowPublicAccessDuringSetup=false`,
`enableScheduledRuns=false`, and Key Vault purge protection on. Purge protection cannot be
disabled once enabled. The initial analysis concurrency is 1.

Foundry and the VNet use the **same region selected in Basics**, as required by
[Foundry network injection](https://learn.microsoft.com/azure/foundry/agents/concepts/networking-options#bring-your-own-virtual-network-requirements).
Confirm model/Hosted availability there before deployment. Separate Foundry and application
regions are supported by this setup flow only outside the VNet-injection profile.

For an existing VNet, custom CIDRs, IP restrictions, or a different isolation mode, deploy
[enterprise/main.bicep](enterprise/main.bicep) using reviewed parameters instead of the guided
form. All three required subnets and private DNS routing must satisfy the
[network constraints](../README.md#network-isolation-networkisolationmode). Do not change an
existing public Foundry account to VNet injection in place; that property is create-time-only.

Run target-specific ARM validation/what-if and policy/quota checks before creating resources.
Use [azbrief-enterprise.parameters.example.json](azbrief-enterprise.parameters.example.json)
only as a non-secret shape example, not as approved customer inputs. Keep real parameter files
under ignored `.azure/` and never print or commit secure values. For example, from an activated
project virtual environment:

```powershell
az deployment group validate --subscription '<subscription-id>' --resource-group '<resource-group>' `
  --template-file infra/enterprise/main.bicep --parameters '@.azure/customer.parameters.json'
az deployment group what-if --subscription '<subscription-id>' --resource-group '<resource-group>' `
  --template-file infra/enterprise/main.bicep --parameters '@.azure/customer.parameters.json'
```

Record the Portal deployment name. Outputs contain a versioned, **non-secret** `customerSetup`
object. No API key, client secret, ACS connection string, or subscriber list belongs in it.

## 2. Prepare An Isolated Deployment Host

Use PowerShell 7, Git, Python 3.11 or later, Azure CLI with the Container Apps extension, and azd
with its Microsoft Foundry/agent extension. Use the currently supported extension combination
for the checked-out manifest. Bicep compilation for this change was checked with **0.46.1**.
Hosted source deployment uses remote build and does not require local Docker.

```powershell
git clone --branch '<reviewed-release-tag>' https://github.com/Networkdog/AzBriefEnterprise.git AzBriefEnterprise-customer
Set-Location AzBriefEnterprise-customer
python -m venv .venv
& .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install -e '.[dev]'
az login --tenant '<customer-tenant-id>'
az account set --subscription '<customer-subscription-id>'
azd auth login --tenant-id '<customer-tenant-id>'
```

Do **not** create a root `.env` in this customer clone. The setup script refuses it because local
settings could redirect SDK calls into a development tenant. It verifies the default Azure CLI
account, reads only the selected deployment, and passes the explicit environment to azd.
`az` and `azd` have separate sign-in state. Do not share one shell across customer deployments.
The clone command above expects a branch or tag, not a commit SHA. When pinning a commit, check
out that reviewed commit explicitly before configuring the customer environment.

```powershell
$customer = @{
  SubscriptionId = '<customer-subscription-id>'
  ResourceGroup = '<resource-group>'
  DeploymentName = '<portal-deployment-name>'
  Environment = 'customer-prod'
}
./scripts/setup_customer.ps1 @customer -Stage Configure -WhatIf
./scripts/setup_customer.ps1 @customer -Stage Configure
```

`Configure` writes individual `azd env set NAME VALUE` bindings, including the project endpoint,
ARM ID, tenant, model policy, Hosted name, and both name forms for each of the six specialists. It
also reads `APPLICATIONINSIGHTS_CONNECTION_STRING` from the existing App/Job, requires a non-empty
matching destination, and records it in the named azd environment for local tooling. With v3 it also
checks the shared Application Insights component. Hosted does not consume a manifest override:
Foundry injects this reserved setting from the project's `AppInsights` connection. The foundation
creates that connection using `ProjectManagedIdentity` and the required `ResourceId` and
`ApplicationInsightsConnectionString` metadata. Existing projects must connect the same component
through project monitoring before Hosted publication; never add a second monitoring destination.
The public ARM setup contract still contains no connection string.
It refuses to rebind an existing environment to another tenant/subscription/project and restores
a pre-existing default environment after creating the new one. It never evaluates command
strings returned by ARM. `-SetupFile` accepts a saved `customerSetup` object **only with
`-WhatIf`** for offline contract review; live stages always re-read ARM.

## 3. Publish MCP And Agents

From the private-network deployment host:

```powershell
./scripts/setup_customer.ps1 @customer -Stage Mcp
./scripts/setup_customer.ps1 @customer -Stage Agents
```

`Mcp` uses [azure-mcp-server](azure-mcp-server/README.md), keeps the verified
`3.0.0-beta.38` image, Entra authentication, read-only mode, and the group/resourcehealth/advisor
namespace restriction. It creates the project-managed-identity connection and stores the server
URL only in the named customer environment. Use `-ServiceManagementReference '<guid>'` on this
stage when the customer's directory requires that application metadata.
Before provisioning, it checks the App/Job environment, region/profile, Entra-only Application
Insights and the common workspace destination. A legacy MCP app in a different environment stops
the stage before any provisioning. After provisioning it reads the MCP app back and checks its
environment and disabled direct exporters. `Verify` and `EnableSchedule` repeat these v3 checks.
These are configuration checks, not proof that a log has arrived or a private MCP call succeeds.

The stage deliberately does not overwrite an existing project connection with `--force`.
If creation reports that the connection already exists, inspect it with `azd ai connection show`
in the same environment, confirm the exact target/authentication/audience, and reconcile it with
the customer owner. Do not delete or replace an unreviewed connection to make setup pass.

## Consolidating Existing Installations

This is a planned migration, not an image-only upgrade or an automatic cleanup. An existing MCP
app cannot switch its managed environment through this setup flow. The script stops rather than
deleting or replacing it. Incremental ARM deployments also do not delete old resources that have
been removed from a template. Use a reviewed maintenance window and record rollback information.

1. Pause scheduled/manual analysis with the operator's existing controls and record the prior
  schedule, images, MCP URL, environment, Entra application/connection and Reader assignments.
  Preserve secure deployment inputs and monitoring retention/export requirements outside Git.
2. Validate/what-if the current foundation with the installation's existing parameters, immutable
  App/Job images, secrets and network configuration. Apply only an approved non-destructive
  update to obtain v3 outputs and the single-environment Admin readiness inventory. Do not
  recreate Foundry, change its injection mode, reset storage, or restore bootstrap images.
3. Run `Configure` against that deployment. Explicitly approve recreating only the MCP Container
  App in the shared environment. Remove the old MCP app during the window, preserving its old
  environment and monitoring resources for rollback/history. Its replacement gets a new
  system-assigned principal; the MCP template grants Reader to that new principal.
4. Review the existing named Foundry project connection before replacement. Its old MCP URL will
  change; retain its configuration, then remove only that approved connection before running
  `Mcp`, which creates the new authenticated connection without `--force`. Never remove unrelated
  connections or the MCP Entra application. Rerun `Agents` to publish/check matching definitions
  and the Hosted configuration, then use the normal `Verify` and operational acceptance gates.
5. Confirm a real authenticated read-only MCP call and new MCP console rows in the common
  workspace, as well as AzBrief tracing in the shared Application Insights. Confirm no MCP
  ingestion-authentication errors. Only then restore the approved schedule.
6. After acceptance and explicit deletion approval, remove the empty old MCP environment and
  obsolete identity role assignments. Retire the old MCP Application Insights/workspace only
  when retention/export obligations permit; historical telemetry is not moved automatically.
  Never delete the shared environment, shared monitoring, or a workspace used by another service.

The installer does not execute these destructive steps. Rollback may require recreating the MCP
app on its recorded old environment and restoring the previous connection/Agent versions; an
image rollback alone cannot restore a changed hostname or system-assigned principal.

## Agent Publication

`Agents` requires a clean source checkout, runs import/full tests, provisions the six distinct
Prompt Agents, runs the exact roster check, publishes the Hosted source, and verifies its name
and active status. It does **not** send email or grant tenant evidence rights. Re-running agent
publication can create another immutable Hosted version and incur build/model costs; inspect a
partially completed stage before retrying.

Obtain the **dedicated Hosted Agent principal ID**, not the Container Apps UAMI and not the
Foundry project identity:

```powershell
azd ai agent show azbrief-analysis-hosted --environment $customer.Environment --output json
az role assignment create --subscription $customer.SubscriptionId `
  --assignee-object-id '<dedicated-hosted-agent-principal-id>' --assignee-principal-type ServicePrincipal `
  --role Reader --scope "/subscriptions/$($customer.SubscriptionId)"
az role assignment create --subscription $customer.SubscriptionId `
  --assignee-object-id '<dedicated-hosted-agent-principal-id>' --assignee-principal-type ServicePrincipal `
  --role 'Monitoring Metrics Publisher' --scope $customer.azureMonitorDcrResourceId
```

Repeat only for explicitly approved evidence scopes. Allow time for role propagation. Billing
hierarchy access requires a separate Billing Reader or equivalent read-only billing permission;
subscription Reader does not grant it. Add service-specific data-plane permissions only for
tools the customer needs. Never solve a missing role by granting broad Contributor rights.

Grant the dedicated Hosted principal **Foundry User** (`53ca6127-db72-4b80-b1b0-d745d6d5456d`)
at `customerSetup.foundryProjectId`. The native specialist FunctionTool loop creates and deletes
conversations; subscription Reader alone produces an `agents/write` 403 even when model inference
and tenant inventory appear to work. Do not grant this permission at subscription scope.

For detailed error logs, separately grant the dedicated Hosted principal **Monitoring Metrics
Publisher** on the existing Application Insights component and on
`customerSetup.azureMonitorDcrResourceId`. The foundation already grants both scoped roles to the
App/Job UAMI, not to the subsequently created Hosted identity. Keep `DisableLocalAuth: true`; a
connection string or DCR endpoint identifies a destination but cannot replace Entra authorization.
The project managed identity also needs component-scoped Monitoring Metrics Publisher for service
traces; the foundation assigns it. Use Project Managed Identity authentication on the monitoring
connection and leave the reserved connection-string environment variable out of the Hosted manifest.
The Hosted manifest enables `OTEL_ENABLED`, reuses Application Insights, and sends only errors or
failure-marked records to `AzBriefFailures_CL`. Do not carry the test-only
`OTEL_SDK_DISABLED=true` into deployment.

After deploying the App/Job and Hosted source, verify a known failure's `trace_id` in the shared
workspace's `AppExceptions` (and `AppTraces` for warnings/errors without exceptions), using the
[failure-event query](../README.md#failure-events-in-log-analytics). Confirm the same event in
`AzBriefFailures_CL`, including `FailureKind`, runtime, run/update/trace correlation, and redacted
error fields. Export initialization and offline tests do not establish data arrival; check DCR
ingestion connectivity, role propagation, retention/access controls and exporter warnings separately.

## 4. Build And Install The Control Plane

For **ManagedIdentity**, build the **same reviewed source** into a unique customer ACR tag.
Do not overwrite a release tag:

```powershell
$acrName = '<customer-acr-name>'
$registry = az acr show --name $acrName --subscription $customer.SubscriptionId --query loginServer -o tsv
$imageTag = "customer-$(Get-Date -Format yyyyMMddHHmmss)-$(git rev-parse --short=12 HEAD)"
az acr build --registry $acrName --subscription $customer.SubscriptionId `
  --source-acr-auth-id '[caller]' --image "azbrief-enterprise:$imageTag" .
$digest = az acr repository show --name $acrName --subscription $customer.SubscriptionId `
  --image "azbrief-enterprise:$imageTag" --query digest -o tsv
$image = "$registry/azbrief-enterprise@$digest"
```

Grant the **Container Apps UAMI** pull-only access on that ACR, using `Container Registry
Repository Reader` for ABAC or `AcrPull` for legacy RBAC. The foundation's `grantAcrPullCommand`
shows the correct role for the mode supplied to the form. It is a different identity and scope
from Hosted evidence access. Confirm both App and Job use the configured registry with managed
identity before continuing. This role-grant step applies only to ManagedIdentity mode.

For **Credentials**, the publisher builds that reviewed release in a separate publisher-tenant
session and supplies the immutable digest plus source/build provenance. The customer deployment
session stays signed into the customer tenant and needs no publisher ACR management access:

```powershell
$image = 'publisher.azurecr.io/azbrief-enterprise@sha256:<64-lowercase-hex-digest>'
```

Both resources must already have the credential/Key Vault binding from the foundation. Do not
run `grantAcrPullCommand` as a cross-tenant role grant; its Credentials-mode output explains that
no customer identity grant is required. Neither installation nor upgrade scripts read the token
password. Use the following initial-install commands for either authentication mode:

```powershell
./scripts/setup_customer.ps1 @customer -Stage Application -Image $image -WhatIf
./scripts/setup_customer.ps1 @customer -Stage Application -Image $image
```

This initial-install path requires a Manual Job, verifies both registry authentication modes,
attached identities and credential secret references as applicable, and verifies runtime
targets, updates Job/App to the same digest, and changes the bootstrap port/probes to
8000 + `/health` without replacing runtime environment variables or Key Vault references.
On an update failure it submits the previous images/port/probes for rollback and returns a
failure; verify their actual health before retrying. On success, wait for the new App revision
to become Healthy/Running before the next stage. An image update alone is not an acceptance test.

If the console was enabled, add this **Web redirect URI** to the existing Entra application:

```text
https://<container-app-fqdn>/.auth/login/aad/callback
```

Use the exact HTTPS origin. Keep EasyAuth enabled, global `AllowAnonymous` for API-key routes,
application-level allow-lists, and the existing same-origin redirect allowance. Anonymous users
must not reach Admin/Archive data. Record the client-secret expiry and customer rotation owner.

## 5. Acceptance And Schedule Activation

```powershell
./scripts/setup_customer.ps1 @customer -Stage Verify
```

This checks App/Job digest and registry-authentication parity, port/revision health, the intended active Hosted Agent, exact
specialist roster, application health, and closure of temporary Foundry public access in VNet
mode. It does not invoke a model, inspect private report content, or send email.

Complete these operational checks with the customer's authorized operator:

- Confirm actual image pulls from both runtimes, including a fresh import-only Job execution.
  Registry metadata validation alone does not prove token validity, network access or expiry.
- Sign in with an allowed administrator and confirm an unrelated/anonymous principal is denied.
- In Admin readiness, confirm the declared support resources, agent versions and storage access.
- Run exactly one recent update in Admin with **send email off**. Require `analyzed=1`,
  `archived=1`, `failed=0`, `archive_failed=0`, and a readable canonical Archive entry. A manual
  run must not advance the scheduled checkpoint. Confirm real evidence, not failure placeholders.
- Review the Azure MCP direct-tool inventory and one read-only call. Missing evidence rights
  must remain explicit gaps, never fabricated absence.
- Send one explicitly approved test digest to the acceptance mailbox. Confirm both ACS send
  success and actual receipt; `email_sent` or a successful API response alone does not prove inbox
  delivery. Ensure no production subscriber receives this test unintentionally.
- Record source commit, image digest, Hosted/Prompt versions, model/version/SKU, trace/run/archive
  IDs, authentication result, email acceptance and rollback owners in customer-controlled storage.

With browser surfaces disabled, use the authenticated API with an API key handled locally from
Key Vault and verify the equivalent archive/run evidence; do not send the key through chat.

Only then explicitly attest acceptance and enable the dispatcher:

```powershell
./scripts/setup_customer.ps1 @customer -Stage EnableSchedule -AcceptOperationalChecks
```

The script rechecks readiness, PATCHes only Job configuration while retaining registries,
Key Vault references, timeout and retry policy, then reads the schedule back. It does not call
the scheduler's manual start command. Default dispatch is every five minutes; the protected
digest schedule is 02:00 UTC daily. Retain the first real digest's analysis/archive/delivery and
checkpoint evidence, not merely Job `Succeeded`. Application readiness is separate from report
quality release gates; use the existing quality campaign for source/prompt quality changes.

## Upgrades And Recovery

For an already-running installation consuming publisher images, use the approved digest directly:

```powershell
./scripts/deploy_dev.ps1 -SubscriptionId '<customer-subscription-id>' `
  -ResourceGroup '<customer-resource-group>' -ContainerAppName '<customer-app-name>' `
  -SchedulerJobName '<customer-job-name>' `
  -PrebuiltImage 'publisher.azurecr.io/azbrief-enterprise@sha256:<64-lowercase-hex-digest>' -WhatIf
```

Review the preview, then run the same command without `-WhatIf`. `-PrebuiltImage` must not be
combined with `-AcrName`, `-ImageName` or `-ImageTag`. It skips ACR lookup/build/digest discovery,
not local import/tests, revision health, HTTPS health, import-only Job smoke or rollback. Both
runtime registry bindings must already match and remain unchanged after rollout. Local source
fingerprints describe the validation checkout, not proof of the publisher artifact's provenance.
This is not the bootstrap installer and does not configure credentials or switch registries.

- Keep a supported old image digest and Hosted version. For normal upgrades, use
  [deploy_hosted_agent.ps1](../scripts/deploy_hosted_agent.ps1) and then
  [deploy_dev.ps1](../scripts/deploy_dev.ps1) with explicit customer targets. Despite its filename,
  the latter is the guarded paired App/Job image rollout; it is **not** the bootstrap installer.
- Existing azd environments must set `FOUNDRY_HOSTED_AGENT_NAME` to their actual deployed name
  before using the parameterized manifest. Do not rename a deployed agent accidentally.
- Deploy compatible Hosted changes before the control plane. Do not send a newer wire contract
  to an old Hosted version. Preserve the six distinct specialist identities and role boundaries.
- Do not reapply the full template for image-only changes: omitted secure inputs can rotate the
  generated API key or disable console authentication. For a deliberate full-template update,
  preserve **all** existing secure parameters and set `enableScheduledRuns=true` explicitly only
  when the installation has already passed acceptance; omission returns the Job to Manual.
- Switching an existing installation to a publisher registry is a separately reviewed foundation
  change. First make approved current/rollback images available there, preserve every existing
  secure input (including the registry password), and supply the intended real `containerImage`
  and schedule state. Keep the old access valid until new pulls are verified; do not remove the
  only pull binding for a still-used image. `-PrebuiltImage` alone cannot perform this migration.
- Adding `AzBriefFailures_CL` to an existing installation is a deliberate infrastructure update,
  not an image-only rollout. Redeploy the reviewed foundation with every existing secure parameter,
  confirm the custom table/DCR and App/Job DCR role, rerun `Configure` to bind Hosted variables,
  grant its dedicated identity DCR-scoped Monitoring Metrics Publisher, then publish Hosted and
  roll out the matching App/Job image. A new image alone cannot create the table or DCR.
- If Verify fails while a revision is provisioning, inspect its state and logs; rerun Verify after
  it is ready. Do not weaken probes, private access, authentication, or acceptance checks.
- If a stage fails, it is incomplete even if earlier resources were created. Inspect those
  resources before rerunning. No destructive cleanup, key rotation, or cross-role fallback is
  part of the setup script. Stop the dispatcher before manual recovery that could resend mail.

## Maintainer Handoff

Before sharing a release, run import/full pytest, customer setup tests, both Bicep compiles, exact
compiled-ARM drift checks and the official Portal UI schema check. Publish the matching source,
ARM and UI files together. Local files do not change a public README button until the reviewed
commit is pushed. Preview the form in the [Portal UI sandbox](https://portal.azure.com/#blade/Microsoft_Azure_CreateUIDef/SandboxBlade)
and perform one clean customer-like staging deployment before claiming end-to-end deployment
success. Local/mocked tests cannot establish subscription policy, quota, Graph consent, private
DNS, remote build, RBAC propagation, or email receipt.

Review the known workflow constraints in the [CI guide](../.github/workflows/README.md) and the
current Feedback browser-test limitation in the [test guide](../tests/README.md). A locally green
test run is not a GitHub Actions execution result or current browser interaction coverage.