# Customer Deployment

[English overview](../README.md#deployment) | [한국어 개요](../README.ko.md#배포)

The README **Deploy to Azure** button creates the foundation, not a working installation.
Foundry Agent versions and the authenticated Azure MCP server require a separate setup phase.
The initial hello-world application uses port 80 and `/`; the Job stays **Manual**. The setup
script installs the real image on port 8000 with `/health`, then enables scheduling only after
readiness checks and explicit operational acceptance. A foundation success, HTTP 200, or a Job
exit code alone is not proof that analysis, archive storage, and email work.

## Setup Stages

Run every stage from the same reviewed customer checkout with explicit `SubscriptionId`,
`ResourceGroup`, `DeploymentName`, and `Environment`. The normal `-WhatIf` path still reads ARM
outputs; it previews the target and stage without proving live readiness. Only
`-SetupFile <file> -WhatIf` uses a local `customerSetup` object without Azure reads.

| Stage | Required before running | What completion establishes |
|---|---|---|
| `Configure` | Succeeded current foundation deployment, correct default CLI tenant/subscription, clean customer clone without a root `.env` | Named azd bindings match the non-secret ARM setup contract; no application is deployed by this stage |
| `Mcp` | `Configure`, private Foundry connectivity, approved Graph/resource permissions | Separate pinned read-only MCP deployment and authenticated project connection; existing connections are not force-replaced |
| `Agents` | MCP URL recorded, clean reviewed source, model and Foundry access | Import/full tests and roster check pass, and the intended Hosted name is active; evidence permissions and analysis acceptance remain separate |
| `Application` | Manual Job, approved immutable ACR digest and App/Job managed-identity pull access | Paired image and bootstrap port/probe changes are submitted and read back; revision health is checked in `Verify` |
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
| Azure permissions | The deployment operator needs resource creation plus role-assignment rights at the required resource-group/subscription/ACR scopes. This is not a runtime permission grant. |
| Entra permissions | Azure MCP creates an Entra application/service principal and assigns its app role to the Foundry project identity. Obtain customer approval for these Microsoft Graph operations separately; Azure subscription Owner alone is insufficient. Use a dedicated deployment identity approved by the directory administrator. |
| Foundry | Verify Hosted Agents, VNet injection, the exact model/version/SKU, and quota in the selected region. The form deliberately requires a model and version. Capacity is model-specific units, not universally thousands of TPM. |
| Residency | Global Standard can process requests outside the account region. Regional Standard and Data Zone Standard have different availability, quota, and residency boundaries. Have the customer approve the selected SKU. |
| Network | Provide a VNet-connected deployment host with private DNS resolution. Ordinary Cloud Shell and a public hosted CI runner are not automatically inside this VNet. Do not open Foundry, Key Vault, or Storage to work around access failures. |
| Registry | Provide an existing customer ACR, its exact login server and RBAC/ABAC permission mode. The deployment operator needs remote-build/push access; App/Job receive pull-only access later. No registry password is used. |
| Browser access | Recommended: prepare a single-tenant Entra application, client secret and explicit administrator object IDs/UPNs. Enter the secret directly in the Portal password field, never in chat or source control. Add the callback URI after the app hostname is known. |
| Email | Choose the Communication Services data location and one approved acceptance mailbox. Verify service availability, sending limits, and the customer's mail-filtering policy. Production subscribers can be added after acceptance. |
| Cost | Budget for model tokens, Container App minimum replicas, Job runtime, the separate Azure MCP app/environment, private endpoints, storage, registry builds/storage, logs and ACS email. Obtain a customer-specific estimate and budget alert; no fixed monthly price is implied. |

The current Azure MCP module uses a separate **public HTTPS ingress protected by Entra** and
subscription Reader. VNet isolation of Foundry/Key Vault/Storage does not make that MCP ingress
private. A customer that requires every endpoint to be private must resolve this deployment
constraint before acceptance; do not remove MCP authentication or broaden its identity.

Resource providers must be available/registered: `Microsoft.App`, `Microsoft.CognitiveServices`,
`Microsoft.Communication`, `Microsoft.KeyVault`, `Microsoft.Network`, `Microsoft.Storage`,
`Microsoft.ManagedIdentity`, `Microsoft.Insights`, and `Microsoft.OperationalInsights`.

## 1. Deploy The Foundation

Open the README button and complete **Basics**, **Foundry and model**, **Email delivery**, and
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
ARM ID, tenant, model, Hosted name, and both name forms for each of the six specialists. It
refuses to rebind an existing environment to another tenant/subscription/project and restores
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

The stage deliberately does not overwrite an existing project connection with `--force`.
If creation reports that the connection already exists, inspect it with `azd ai connection show`
in the same environment, confirm the exact target/authentication/audience, and reconcile it with
the customer owner. Do not delete or replace an unreviewed connection to make setup pass.

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
```

Repeat only for explicitly approved evidence scopes. Allow time for role propagation. Billing
hierarchy access requires a separate Billing Reader or equivalent read-only billing permission;
subscription Reader does not grant it. Add service-specific data-plane permissions only for
tools the customer needs. Never solve a missing role by granting broad Contributor rights.

## 4. Build And Install The Control Plane

Build the **same reviewed source** into a unique customer ACR tag. Do not overwrite a release tag:

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
identity before continuing. The setup script never uses registry passwords.

```powershell
./scripts/setup_customer.ps1 @customer -Stage Application -Image $image -WhatIf
./scripts/setup_customer.ps1 @customer -Stage Application -Image $image
```

This initial-install path requires a Manual Job, verifies both registry bindings and runtime
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

This checks App/Job digest parity, port/revision health, the intended active Hosted Agent, exact
specialist roster, application health, and closure of temporary Foundry public access in VNet
mode. It does not invoke a model, inspect private report content, or send email.

Complete these operational checks with the customer's authorized operator:

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