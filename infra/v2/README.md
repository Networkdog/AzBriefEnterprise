# Two-Stage Deployment v2

An opt-in private profile with new/existing VNet selection, derived from the existing
[KT template](../kt/README.md) and [Enterprise template](../enterprise/main.bicep).
It does not replace their Portal buttons or adopt an existing KT installation.
Use a new set of resource names in one explicitly selected resource group.

## Ownership

| User stage | Owns | Does not do |
| --- | --- | --- |
| Infrastructure, once | Foundry account, models, project/Capability Host with Microsoft-managed agent data, application Blob Storage, control-plane and MCP UAMIs, internal Environment, DNS/PEs, logs/Insights, email, scoped roles, MCP Entra application/project connection, staged RG tag handoff | No Foundry-only Cosmos/Search/BYO stores by default; no App/Job/MCP image or Prompt/Hosted Agent versions |
| Application, initially and repeatedly | Agent code/policies through the existing guarded publication workflow; real App/Job image, environment, secrets, registry configuration and CLI-prepared Entra web sign-in/default operators | No infrastructure, model, BYO, diagnostic or RBAC replay during image/configuration updates |

The infrastructure command internally creates the foundation, waits for the account
Capability Host, and then completes models/project/Host/RBAC. These are implementation
steps inside one infrastructure operation, not another application stage. Do not
precreate the project or replay the account's custom subdomain.
The Portal ARM excludes all Microsoft Graph resources. For managed MCP, core completion
stops at `mcp_pending` until the authorized CLI prepares directory authentication once.
The full infrastructure CLI runs this automatically. App install always requires `ready`.

The Hosted Agent identity is created by the platform only when its code is first
published. Its project Foundry User, component Monitoring Metrics Publisher and
subscription Reader grants are therefore a narrow **late infrastructure completion**.
[runtime-roles.bicep](runtime-roles.bicep) changes only those grants. It does not replay
the infrastructure and is not part of ordinary App/Job updates. Use the verified
Hosted principal, not the control-plane or project identity. Billing hierarchy access
and separately approved evidence scopes remain operator-managed.

## Files

| Source | Compiled artifact / purpose |
| --- | --- |
| [infrastructure.bicep](infrastructure.bicep) | [infrastructure.json](infrastructure.json), one-time infrastructure |
| [foundation.bicep](foundation.bicep) | Internal foundation; reuses the pinned KT AVM modules and email module |
| [managed-capability-host.bicep](managed-capability-host.bicep) | Basic VNet project Host with `capabilityHostKind=Agents` and no BYO connection properties or data roles |
| [resource-group-tags.bicep](resource-group-tags.bicep) | Foundation, mcp_pending and ready handoff, including bounded saved options |
| [mcp-authentication.bicep](mcp-authentication.bicep) | [mcp-authentication.json](mcp-authentication.json), CLI-only Graph app/project role/RemoteTool connection and ready promotion |
| [application.bicep](application.bicep) | [application.json](application.json), repeatable App/Job installation and updates |
| [runtime-roles.bicep](runtime-roles.bicep) | [runtime-roles.json](runtime-roles.json), late Hosted permissions |
| [monitoring-role.bicep](monitoring-role.bicep), [subscription-reader.bicep](subscription-reader.bicep) | Scoped role modules, never application resources |
| [deploy_v2.py](../../scripts/deploy_v2.py) | Explicit-target preflight and Incremental deployment CLI |
| [test_deployment_v2.py](../../tests/test_deployment_v2.py) | Offline ARM and install/update contract tests |
| [infrastructure.createUiDefinition.json](infrastructure.createUiDefinition.json), [application.createUiDefinition.json](application.createUiDefinition.json) | Paired Portal forms attached by each README Deploy button |
| [build_v2_ui.py](../../scripts/build_v2_ui.py) | Normalize supported form guards and reuse the tested KT network validation contract |
| [mcp-identity.bicep](mcp-identity.bicep), [mcp-application.bicep](mcp-application.bicep) | Dedicated infrastructure-owned Reader UAMI and application-owned read-only MCP container |

Never hand-edit the compiled JSON. CI pins Bicep 0.46.1 and checks all four artifacts.

## Prerequisites

- An existing target RG and matching default Azure CLI tenant/subscription. The tool
  does not sign in, switch accounts, create a RG, or read developer `.env` state.
- An approved same-region existing VNet with three distinct prepared IPv4 subnets.
  Preserve the KT CIDR, RFC1918, delegation, exclusivity and free-address requirements.
  The tool does not PUT the VNet or create/change subnets.
- Or choose `virtualNetworkMode=new` for a dedicated VNet in the target RG. The infrastructure
  creates that VNet and its three subnets before foundation; it never adopts or overwrites an
  existing same-name VNet. Existing mode remains the backward-compatible default.
- Deployment and role-definition/assignment permissions, plus private DNS reads and
  any explicit canonical central-DNS IDs. Failed or incomplete reads block writes.
- Permission to create the MCP Entra application/service principal and assign its app role
  through the CLI-only Microsoft Graph Bicep modules. The Portal artifact does not perform
  these operations. This is separate from ARM Contributor; verify directory approval before
  authorized preparation. No authentication bypass or secret is created.
  MCP uses the same Environment/workspace and a dedicated Reader-only UAMI. Its URL comes
  from the actual Environment domain and customer-named MCP App, not a fabricated address.
  The shared MCP app module accepts that infrastructure-owned identity and sets its outgoing
  `AZURE_CLIENT_ID`; its empty optional identity input retains legacy system-assigned behavior.
- Graph Bicep delegated deployments require both the signed-in user's directory privileges
  and the deployment client's OAuth scopes. ARM Owner is not a Graph grant; the same user
  can succeed through Azure CLI and fail through the Azure Portal client. Use the supported,
  approved Azure CLI/PowerShell path for `Authorization_RequestDenied`, never broader roles,
  secrets or an incoming-auth bypass. Validate/recover only the original failed module and
  its exact uniqueName; a child success does not resume or prove the parent's readiness.
  See [Graph Bicep permissions](https://learn.microsoft.com/graph/templates/bicep/concept-permissions-and-privileges).
- Actual model catalog, Responses/Agents capabilities and quota. The completion
  preflight reuses the KT checks; aliases alone are not availability proof.
- An approved immutable control-plane image digest and registry reachability.
  Public GHCR needs no token; a private registry needs explicit secret references.
  No image is built, mirrored, published or automatically sent during infrastructure setup.
- Automatic first application setup requires a human CLI login in the explicit target tenant,
  a readable signed-in Graph profile with a real `mail` value, and approved permission to create
  the dedicated single-tenant web login application/service principal and bounded client secret.
  This adds no directory/ARM roles, API grants or admin consent. Directory policy can block it;
  never fall back to anonymous UI or guessed mail. Use explicit configuration with
  `--no-automatic-setup` for a separately approved existing login app or app-only deployment.
- A clean customer clone/private-network publication host for the existing Agent
  scripts. App/Job deployment does not bypass their import, pytest, roster or smoke gates.

New v2 defaults use Core `gpt-5.2` / `2025-12-11` and Simple `gpt-5-mini` / `2025-08-07`,
with 200 `GlobalStandard` capacity units each. ARM/CLI location defaults to `koreacentral`;
the Portal still honors its explicit Basics region and existing-VNet location. No PTU SKU,
reservation or automatic PTU fallback is used. Logical aliases remain `gpt-5-terra` / `gpt-5-luna`.
The existing KT profile and saved v2 choices retain their previous defaults/configuration.
GlobalStandard processing can leave the account region.
ACS/email data location defaults to Asia Pacific. Validate policy, price and capacity
before opting into `--mode deploy`.

On 2026-10-15, read-only checks in the reference subscription reported quota/provider headroom
of 2,000/2,000 units for the selected Core and 1,000/1,000 for Simple in Korea Central. This
supports the 200/200 defaults at that time; it reserves no capacity and guarantees neither
another subscription's availability nor runtime performance. Recheck both gates before deployment.

## Model And Region Selection

The Portal exposes `coreModelName`, `simpleModelName`, `modelVersion`, `simpleModelVersion`,
`modelCapacity` and `simpleModelCapacity` only for a new foundation. Logical aliases and the
six-role policy remain unchanged: Core uses `medium` reasoning and Azure MCP omits reasoning.
These are explicit inputs, not an automatic best-model selector. Check candidates without writes:

```powershell
& .\.venv\Scripts\Activate.ps1
python -m scripts.deploy_v2 infrastructure --subscription <subscription-guid> --tenant <tenant-guid> --resource-group <target-rg> --list-model-options --model-regions koreacentral japaneast eastus2 swedencentral --candidate-models gpt-5.2 gpt-5-mini --mode preflight
```

`available` is the smaller of unused subscription quota and reported regional provider capacity,
in deployment capacity units, not tokens or a reservation. The command needs explicit regions
or a parameter-file location, filters for OpenAI GPT-5 Responses/AgentsV2 GlobalStandard models,
and makes no Azure writes. Unreadable, incomplete or ambiguous capacity fails closed. Verify actual
reasoning, strict JSON, tools, private connectivity, price and paired report quality before promotion;
catalog/capacity success alone proves none of those runtime properties.

Omitting `simpleModelVersion` in a new deployment selects its independent `2025-08-07` default.
Explicitly empty values and older saved tags without this field still retain `modelVersion`.
Supply the exact versions when overriding the physical models; never reuse a new default for
an older saved profile. Completion combines shared quota and checks each selected profile;
it never silently substitutes models. Existing aliases are reused without PUT only when model,
version and SKU agree.

For a new VNet, the Basics region selects the new Foundry account, child model deployments and
VNet together. An existing VNet fixes that region to its actual location; completion uses the saved
location. This is not a remote-model connection or an existing-account move. Use a new installation
for another region; private/PNA/Entra restrictions remain unchanged.

A saved `foundation` blocked before any model/project was created may explicitly reselect the six
physical model/version/capacity fields with `--stage complete --reselect-models` and the approved
protected parameter file. Start with `--mode preflight`, then use the same file for `--mode what-if`
and `--mode deploy`. Region, aliases, names, network, data mode and other options remain immutable.
Existing models/projects, unreadable inventory or a later ready/pending state block this exception.
It never replaces models, rewrites tags by itself or resets Hosts. A partial attempt that created
models/projects needs separate diagnosis before retry.

## New Or Existing VNet

Choose the mode in the Portal network tab or `virtualNetworkMode` in the protected CLI
parameter file. `existing` retains the approved VNet selector, three exact subnet choices
and original CIDR/delegation/occupancy/DNS checks. CIDR inputs for new mode never resize or
reinterpret existing subnets. Cross-RG existing VNet selection remains supported.

`new` creates only a dedicated target-RG VNet, with no peering, NSG, route, public IP, NAT
or changes to customer networks. Defaults are VNet `10.240.0.0/16`, Foundry `10.240.0.0/24`,
Container Apps `10.240.1.0/27` and private endpoints `10.240.1.32/28`. Portal offers validated
`10.240`, `10.241` or `172.28` /16 profiles and explicit customer names. The CLI accepts
advanced `virtualNetworkAddressPrefix`, `foundrySubnetAddressPrefix`,
`containerAppsSubnetAddressPrefix` and `peSubnetAddressPrefix` after the same fail-closed
private IPv4, containment, overlap, size and delegation validation. Choose a range that
does not overlap existing/on-prem networks before any later peering or connectivity work.

New mode requires `virtualNetworkResourceGroupName` to be the explicit target RG; Portal
derives it automatically. The new network module runs only in foundation. Completion verifies
ownership and exact saved addresses and never PUTs the VNet. Mode/prefix choices persist in
the existing option chunks without changing the 41-tag budget. Missing legacy mode means
`existing`. Changed saved choices, unknown inventory and name collisions block, not adopt.

Both modes retain the same private Foundry/Container Apps, Basic managed agent data, internal
ingress DNS, Entra and API-key protections. An installed App update remains network-free.
Offline expression/compile tests are not authenticated Portal acceptance or actual deployment.

## Agent Data Storage

New v2 installations default to `useMicrosoftManagedAgentData=true`, following the official
[Basic VNet data options](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/networking-options#bring-your-own-virtual-network-requirements)
and [Basic VNet sample](https://github.com/microsoft-foundry/foundry-samples/tree/main/infrastructure/infrastructure-setup-bicep/11-private-network-basic-vnet).
The account retains `networkInjections.scenario=agent`, the approved delegated subnet,
`useMicrosoftManagedNetwork=false`, PNA Disabled and its private endpoint. The project
Capability Host remains and uses API `2025-04-01-preview` with only `capabilityHostKind=Agents`.
It **omits**, rather than empties, `storageConnections`, `threadStorageConnections` and
`vectorStoreConnections`. Foundry uses platform-managed agent data resources; this is not a
claim that each project gets newly provisioned, dedicated backing resources in the customer RG.

The default template creates no Foundry-only Cosmos DB, Search, their PE/DNS resources, BYO
project connections or Foundry data-plane role assignments. It still creates the app's private
Storage account and Blob PE for `azbrief-state`/`azbrief-archive`: checkpoint, Admin settings,
Feedback and canonical Archive are app responsibilities, not Foundry conversation storage.
Admin readiness excludes absent Cosmos/Search targets. Default PE automation creates only
Foundry and app Blob PEs; ACA PE remains optional. The conservative IP budget drops to 5
(6 with ACA PE), with five canonical DNS namespaces plus the separate internal ingress zone.

The data mode is saved inside the existing eight bounded option tags; the 41-tag budget and
schema remain unchanged. Existing options without this flag mean the original BYO mode.
Old Foundation completion requires explicit `useMicrosoftManagedAgentData=false` and its
original Cosmos/Search names; Portal completion reuses the saved mode. An existing BYO Host
cannot be silently treated as a Basic Host. The CLI rejects changed modes, non-ready Host state,
unexpected data connections or modified telemetry instead of resetting resources or broadening
permissions. Existing standard Enterprise/KT templates keep their original BYO behavior.

Incremental omission does not delete previously created Cosmos/Search/PE/DNS resources or old
grants, nor migrate files/conversations. Those require separate approved retirement/migration.
Actual region/API acceptance, private runtime/MCP connectivity and Agent creation still require
live validation. Standard-private guides require BYO for **Standard** setup; that does not rule
out the documented **Basic VNet** path. No network/auth restriction is removed by this change.

## Resource Group Handoff

New infrastructure merges **41** `azbrief.v2.*` tags without removing unrelated RG tags.
Foundation success writes `schema=2`, `profile=kt-private-v2`, `state=foundation`, customer
names and required options. Core completion reuses them and sets `mcp_pending` for managed
MCP (`ready` only for the legacy externally prepared MCP path). The authorized preparation
module promotes `ready` only after Graph authentication/project app-role and connection succeed.
Application still accepts only `ready`; pending never becomes an anonymous install fallback.
An App Review error for `portalValidationPassed=false` is a blocking prerequisite result,
not permission to force the parameter to true. `foundation` means run the same-RG core
completion first; `mcp_pending` means authorized MCP preparation is still required. A full
model quota blocks completion. Inspect current inventory errors/ownership before retrying.
The two README buttons do not collapse the Portal's internal infrastructure phases. A
successful deployment with `infrastructureStage=foundation` is not completed infrastructure:
it does not create the project/model deployments or make the application installable.
Use the same infrastructure button with **Complete existing foundation**, then authorized
MCP preparation, and only afterward install the App. Zero model quota blocks core completion
and cannot be corrected by editing validation/state tags or retrying the App template.

The application image field also validates `foundation`, `mcp_pending`, unknown and `ready`
states before ARM submission, with the missing step named in the error. The independent
`portalValidationPassed` true-only ARM guard remains unchanged. Publish the changed UI before
claiming the public button shows these new messages. A locally deployed CLI/snapshot smoke
is not proof that a public Portal form, another RG or the default model allocation is ready.
The eight `options0` through `options7` values each hold at most 256 characters of one
allow-listed JSON object (2048 characters total). It preserves model IDs/version/capacity,
the agent-data mode, legacy Search SKU, email location, canonical DNS IDs, resource tags and
non-secret MCP identifiers. `cosmos` and `search` name tags are empty in managed mode.

Names include `environment`, `app`, `job`, `identity`, `storage`, `cosmos`, `search`,
`workspace`, `insights`, `acs`, `email`, `foundry`, `project`, `hosted`, `coreModel`,
`simpleModel`, `vnetRg`, `vnet`, `peSubnet`, `foundrySubnet` and `appSubnet`.
`deployment` identifies the successful completion output used by the guarded Agent
handoff. Optional MCP URL/connection and the ACA PE flag are also recorded.
The core output includes the saved VNet RG/name, three subnet names and control-plane identity
name. MCP preparation accepts older missing name fields only when their corresponding ARM IDs
match the exact saved targets. A present wrong name, missing ID or redirected ID still blocks;
this compatibility path never replays successful foundation/model/project resources.
On Windows MSI installations, the shared CLI adapter calls bundled Python directly so a Graph
query URL containing encoded fields and `&` stays one argument, without shell parsing.

Application defaults to `resourceGroup().tags` and resolves IDs/endpoints from names
and actual existing resources in **that same RG**. Operators do not re-enter all
infrastructure names or pass a copied infrastructure-output object on every update.
Tags are editable, plaintext routing metadata, not identity, permission or cryptographic
proof. The CLI also checks explicit context, ownership, typed internal/PNA/subnet state
and exact saved Host/data-mode bindings. Unknown/corrupt/missing contracts block writes.

### Application Deployment Record

| RG tag | Meaning |
| --- | --- |
| `azbrief.v2.state` | Existing infrastructure progress: `foundation`, `mcp_pending` or `ready`; unchanged by application installation |
| `azbrief.v2.applicationState` | `not_deployed` at new infrastructure handoff; `deployed` after App/Job/requested auth/MCP ARM resources succeed |
| `azbrief.v2.lastApplicationDeploymentAt` | Empty before deployment; ISO 8601 UTC such as `2026-10-13T07:15:30Z` for the most recent successful application deployment |

`ready` plus `not_deployed` means infrastructure only; `ready` plus `deployed` records
application deployment too. The timestamp is the **start time of that successful ARM
application deployment**, not a measured resource completion time. The final tag resource
depends on App, Job, requested web auth and managed MCP; failed resource deployments and
preflight/validate/what-if do not advance the record. An update failure retains the last
successful timestamp, so inspect the latest deployment separately for failure details.

The tag write merges the live RG tags and preserves unrelated metadata. Infrastructure/MCP
handoff preserves existing application records rather than resetting them. Older 39-tag
installs remain valid; absence of the optional pair means **not recorded**, not proven absence
of an App. The next successful application deployment writes it, without guessing historical
dates. The CLI validates complete pairs and UTC dates; Portal guards check budget/basic pair
shape while live inventory and auth/key checks remain controlling. Tag-edit permission
(`Microsoft.Resources/tags/write`) is needed in addition to the existing deployment permissions.
Direct ARM callers must perform equivalent fresh budget/contract checks.

These editable deployment records do not prove Hosted/Prompt publication, login, analysis,
mail delivery or runtime readiness. Outputs retain `applicationReady=false`. No current Azure
RG is backfilled by this source change; apply/publish the updated paired artifacts first.

Azure permits 50 tags per RG and 256 characters per value. New v2 installs reserve 41 slots;
the CLI checks the merged budget before provisioning. Resource tags have a separate
15-tag limit because the same input also reaches Private DNS. Use canonical tag-key
casing. Never store API keys, registry tokens, client secrets or subscriber data here.
See [Azure tag limits](https://learn.microsoft.com/azure/azure-resource-manager/management/tag-resources)
and [Bicep tag application](https://learn.microsoft.com/azure/azure-resource-manager/management/tag-resources-bicep).

## Infrastructure Once

Both README buttons attach their own `createUIDefinitionUri`, following the existing
Enterprise/KT ARM/UI pairing. Publish each pair to GitHub main together; a local link
change does not update the remote Portal form. Normalize forms with
`python -m scripts.build_v2_ui` and check drift with `--check`.

Portal customers enter one common `namingPrefix`/`namingSuffix` pair; a read-only preview
shows the generated core/MCP App, UAMI, Environment, Job, storage, logs, email and project
names. Both inputs remain in the ARM contract and saved options for exact completion reuse.
Validation rows are hidden, not editable or removed safety checks. Each UI outputs the
combined `portalValidationPassed` result, constrained to `true` by both ARM templates.
Recompile after source changes and check that every UI output names an ARM parameter.
Internal model aliases, Hosted name and MCP connection name are automatic; physical model names,
versions and capacities are explicit new-foundation inputs. No MCP URL/connection field is shown.
Normal completion reads saved names/options. Model quota/catalog/provider capacity and Graph
permissions remain preflight gates, not implied by automatic values. Unprovisioned model reselection
requires the explicit CLI path above; the Portal cannot silently change saved choices.

Portal `Microsoft_Azure_CreateUIDef / CustomDeploymentBlade` errors before the form opens
are UI initialization failures, not proof of ARM/Graph provisioning failure. The normalizer
guards initial output JSON assembly before RG tags or VNet inputs exist; no reliance on
lazy branch evaluation is needed for those outputs. An initialization fallback never satisfies
the completion guard. Test the unloaded state as well as a populated saved contract, and
do not claim a generic Portal error is resolved from local expression tests alone.
CreateUI tag reads project dotted keys to temporary dot-access properties through the
documented string/replace/parse functions, rather than assuming ARM-style string-key
indexing is accepted. Stored tag keys and option JSON values remain unchanged. A Python AST
test harness is not the Portal parser; a repeated error after publication still requires
the real CreateUIDef diagnostics or authenticated browser console to establish its cause.
Comparison with the working KT definition retains its default non-wizard navigation,
VNet selector options, three exact subnet choice expressions/defaults and five validation
rules. `bladeTitle` is optional in the official schema and also absent from some KT steps;
do not label its absence a proven cause. The v2-only initial tag-contract step remains a
separate difference. Structural parity reduces candidates but does not establish a fix
without a real Portal reload/diagnostic comparison.
For Review-stage `InvalidTemplate` errors naming `virtualNetworkResourceGroupName`, the
foundation output derives the VNet RG from the selected resource ID, matching KT. It does
not assume the ResourceSelector returns a `resourceGroup` property or use the application
RG as a fallback. Empty initial selections remain guarded; completion retains the saved
`azbrief.v2.vnetRg` value even with no VNet picker value.

Prepare a protected parameter file based on
[infrastructure.parameters.example.json](infrastructure.parameters.example.json).
Replace every placeholder. Internal stage/reuse flags are derived by the CLI and
must not be supplied. Keep custom parameter/configuration files out of Git and logs.

```powershell
& .\.venv\Scripts\Activate.ps1
python -m scripts.deploy_v2 infrastructure --subscription <subscription-guid> --tenant <tenant-guid> --resource-group <target-rg> --parameters <protected-parameters.json> --mode preflight
python -m scripts.deploy_v2 infrastructure --subscription <subscription-guid> --tenant <tenant-guid> --resource-group <target-rg> --parameters <protected-parameters.json> --mode what-if
python -m scripts.deploy_v2 infrastructure --subscription <subscription-guid> --tenant <tenant-guid> --resource-group <target-rg> --parameters <protected-parameters.json> --mode deploy
```

`preflight` is the default and is read-only. An initial `what-if` covers the foundation
because the account/project do not exist yet. `deploy` runs fresh preflight, ARM validate
and what-if immediately before **each** internal create operation. All deployments
are Incremental; no Complete mode, resource deletion or automatic migration is used.

For Portal deployments, complete the two core steps first, then prepare MCP authentication
once from an approved CLI identity with the required directory privileges:

```powershell
& .\.venv\Scripts\Activate.ps1
python -m scripts.deploy_v2 infrastructure --subscription <subscription-guid> --tenant <tenant-guid> --resource-group <same-target-rg> --prepare-mcp-authentication --mode preflight
python -m scripts.deploy_v2 infrastructure --subscription <subscription-guid> --tenant <tenant-guid> --resource-group <same-target-rg> --prepare-mcp-authentication --mode deploy
```

The command validates the successful core output, exact same-RG private/BYO bindings,
deterministic directory uniqueName and saved option budget before touching Graph. It never
replays core/model resources or takes internal IDs as user inputs. CLI full infrastructure
`--mode deploy` runs this step automatically. A failed/denied setup stays `mcp_pending` and
cannot install an App. Existing `ready` installs keep their update path. A previously failed
old Portal foundation is not adopted by this command; diagnose and approve its recovery separately.

After success, infrastructure reruns are blocked by the RG contract. If the foundation
succeeded but completion was interrupted, use explicit `--stage complete` after
diagnosis. It verifies every foundation target and reuses matching ready models,
project/BYO/telemetry rather than replaying their PUTs. An automatic/missing/mismatched
project host needs separately approved recovery, never silent delete/reset.
An empty saved DNS override object means foundation-created default zones, not missing DNS.
Completion accepts those only after exact same-RG ownership and approved VNet-link checks;
it preserves the empty saved choice. Explicit central-zone IDs remain immutable. Foreign,
incomplete, paged, denied or redirected DNS reads still block completion.

The ACA Environment remains internal with PNA Disabled. ACA PE automation retains the
KT default `false`: the other four PEs and DNS preparation remain. No endpoint is
deleted by Incremental omission. Manual ACA DNS/HTTPS and all real PE states still
require acceptance. This is not a migration path for an old KT installation.
Foundation creates the Environment's exact `defaultDomain` private zone with `*` and `@`
A records to its actual `staticIp`, and a non-registering link to the approved VNet.
This internal-ingress DNS is separate from `privatelink.<region>.azurecontainerapps.io`
for an optional ACA PE. Verify actual MCP/App HTTPS resolution; a successful replica or
cluster exec status is not DNS/HTTP proof. Existing installs missing this zone need a
separately validated exact DNS-only recovery, never an account/foundation replay or PNA bypass.

## Application Install And Updates

Agent publication is part of the application stage, not ARM infrastructure:
use [setup_kt_agents.ps1](../../scripts/setup_kt_agents.ps1) with the completed
deployment name from `azbrief.v2.deployment`, the exact target and an isolated named
azd environment. Its compatible `ktFoundation.runtimeSetup` output is retained.
Install the application/MCP containers before the guarded Agent publication smoke, then
complete Configure/Publish/Check using the existing private-network guide. These
steps publish the six distinct Prompt Agents and the Hosted code, not merely names.
Do not overwrite the developer `.env` or skip a failed publication/smoke gate.

After the platform exposes the intended Hosted principal, complete only its grants:

```powershell
& .\.venv\Scripts\Activate.ps1
python -m scripts.deploy_v2 infrastructure --subscription <subscription-guid> --tenant <tenant-guid> --resource-group <target-rg> --hosted-principal-id <verified-hosted-principal-guid> --mode deploy
```

If initial publication exposes an active version but fails a smoke due to those missing
permissions, stop and confirm the exact version/principal, finish the grants, then
rerun the existing Check/smoke. Never treat the failed gate as success or reset the Agent.
Role propagation, Billing access and live tenant evidence are separate acceptance checks.

Install the control plane, then use the same command for every subsequent update:

```powershell
& .\.venv\Scripts\Activate.ps1
python -m scripts.deploy_v2 application --subscription <subscription-guid> --tenant <tenant-guid> --resource-group <same-target-rg> --image '<approved-registry/repository@sha256:digest>' --mode preflight
python -m scripts.deploy_v2 application --subscription <subscription-guid> --tenant <tenant-guid> --resource-group <same-target-rg> --image '<approved-registry/repository@sha256:digest>' --mode deploy
```

Use [application.configuration.example.json](application.configuration.example.json)
as a non-secret patch example. To update only the image, omit `--configuration`.
The file can patch `appEnvironment`, `jobEnvironment`, App/Job configuration/templates,
`webAuthConfiguration` and secure `existingSecrets.app`/`existingSecrets.job` arrays.
Secret entries use `name` plus `value`, or existing Key Vault `keyVaultUrl`/`identity`.
Credential environment variables use `secretRef`; never inline credentials in ordinary
environment parameters. Explicit existing web auth can use a same-tenant Entra client ID,
customer issuer, allowed audiences and client-secret reference; defaults supply the access
lists unless explicitly overridden. `--no-automatic-setup` skips only first-install defaults
and directory preparation. It never resets existing installations or permits anonymous UI.

### Automatic Operational Defaults

The first CLI application install reads the actual signed-in user's Graph `id` and `mail`,
not an input field, guessed UPN or service principal. It automatically supplies:

| Default | Applied to |
| --- | --- |
| Directory mail as `EMAIL_RECIPIENT_ADDRESS` | App and Job; an explicit App recipient/subscriber/language patch also supplies the initial Job default unless overridden there |
| Directory mail as `FEEDBACK_RECIPIENT_ADDRESS` | App feedback notifications |
| Directory object ID as `ADMIN_ALLOWED_PRINCIPALS` and `ARCHIVE_ALLOWED_PRINCIPALS` | App; administrators also retain normal Archive access |
| Admin, Archive and Feedback enabled; Admin/Archive authentication required | App; Feedback retains its existing private-network public-form behavior |
| Exact private App origin as Archive/Feedback base URLs | Shared App/Job runtime; Job feedback-link enablement follows the initial App setting |
| Dedicated single-tenant web login app, exact HTTPS `/.auth/login/aad/callback`, bounded 365-day secret | CLI deploy; secret is stored only in the App secret inventory, never Job env, tags or outputs |
| Korean report language, concurrency 1, Manual Job | App/Job; timeout 43200, retry 0, budget 39600 and generated matching API key remain unchanged |

Authenticated UI raises minimum replicas to at least 1 (idle cost). No email is sent and
no schedule or Agent version is started by this setup. Explicit configuration wins; normal
updates preserve existing recipients, access, auth, secret values and accepted schedules.

`preflight`, `validate` and `what-if` do not create directory objects or credentials. A missing
login app uses provisional ID/secret values only to validate the intended ARM shape; that is
not Entra readiness. `deploy` creates the owned registration/service principal/secret only
after the read-only checks, then runs ARM validate/what-if again with the exact issued values.
The registration is bound to the exact RG and private App origin; ambiguous, paged, denied,
foreign-owner, changed callback/audience/permissions or existing orphaned credentials block.
It never rotates a credential after failure or expands roles/API permissions. An interrupted
install with issued credentials but no complete App/Job requires explicit recovery. The secret
expires after 365 days and renewal remains a separately approved operation, not an image update.

### Optional Operational Overrides

No ordinary manual environment value makes incomplete infrastructure `ready`. Complete
core models/project/BYO and MCP preparation, then install App/Job and publish the six
Prompt/Hosted Agent versions through the guarded private-network workflow.

| Override | When needed | Location |
| --- | --- | --- |
| `SUBSCRIBERS` or `EMAIL_RECIPIENT_ADDRESS` | Different/additional approved recipients or supplied role/language/scope | Optional environment patch or Admin-managed subscribers |
| `ADMIN_ALLOWED_PRINCIPALS` | Different/additional named administrators | Optional App environment patch |
| `ARCHIVE_ALLOWED_PRINCIPALS` | Additional authorized archive readers | Optional App environment patch |
| `ADMIN_UI_ENABLED`, `ARCHIVE_UI_ENABLED` | Explicitly change protected UI availability | Optional App patch, retain complete Entra auth and `*_REQUIRE_AUTH=true` |
| `SCHEDULE_CRON_EXPRESSION` | Optional deployment-owned UTC digest schedule | Job environment; actual Manual-to-Schedule trigger activation is separately approved |
| `REPORT_LANGUAGE` | Optional default language (Korean by default) | App/Job environment; subscribers can override |
| `FEEDBACK_UI_ENABLED`, `FEEDBACK_RECIPIENT_ADDRESS` | Explicitly change feedback availability or notification recipient | Optional App patch |

Entra client ID and client secret are not ordinary plaintext application environment values.
Automatic CLI setup prepares these without a configuration file. To use an existing tenant
app instead, supply its client ID/issuer/audiences through `webAuthConfiguration`, store its
secret through `existingSecrets.app`, and point `clientSecretSettingName` at that secret.
The validated patch requires same-origin HTTPS and non-empty principal allow-lists.
Never place the client secret in `appEnvironment`, source, tags, outputs or chat.

Tenant/subscription/UAMI IDs, `API_KEY`, Foundry project/Hosted target, Blob routes,
Insights destination, ACS endpoint/sender and readiness targets are automatic from the
verified infrastructure. Do not hand-enter them or add an ACS connection-string secret.
Agent role names and publishing model bindings are populated by `setup_kt_agents.ps1`;
ARM names alone do not create a running Agent. Apply non-secret App/Job values through
`--configuration`; preserve schedules/keys and never write the developer `.env` implicitly.

Updates preserve current secrets, registry configuration, auth, environments, replicas,
resources, unrelated resource tags and accepted scheduler trigger/cron. They never
rotate a key after a read failure, disable authentication, turn a Schedule Job into
Manual or activate a new schedule. A partial App/Job install requires explicit recovery.
Infrastructure-bound identity/endpoint/storage routes cannot be redirected in a patch.
Temporary parameter files are removed and their values are never logged.
An installed App without web-auth settings can return `Not Found(<JSON>)` with the exact
`AuthConfigNotFound` code instead of an empty list. The CLI accepts only that structured
absence. It still rejects denied, malformed and generic NotFound responses, and requires
complete Entra configuration before enabling either protected UI. Existing auth is preserved.

The Portal application form reads the same RG contract and complete App/Job inventory.
It derives initial install versus key reuse; failed, pending, paged, foreign or partial
inventory blocks. On updates, ARM reads existing secrets/settings internally, retains
auth by omission and keeps the accepted trigger/cron. Secrets never enter UI controls.
The MCP container uses the pinned official image, incoming Entra authentication and only
the group/resourcehealth/advisor namespaces with `--read-only`. A bare Portal initial install
retains the low-level Admin/Archive/Feedback-off defaults; Portal cannot perform the new Graph
web setup. Use the CLI once for automatic first application setup, then either update path.
The infrastructure MCP connection uses `RemoteTool`, `ProjectManagedIdentity`, its explicit
`api://<client-id>` audience and API `2025-10-01-preview`, following the official remote-tool
ARM example. Do not use the invalid `RemoteToolServer` category or weaken authentication to
fix a deserialization error. Existing BYO/model/role successes are preserved on completion retry.

Serialize deployments to one RG/App. This tool has no distributed deployment lock,
image provenance/vulnerability certification, automatic live rollback or smoke acceptance.
Review/approve artifacts and what-if first. Updated Agent code/policies need their own
guarded publication; changing only the App/Job image does not update the Hosted runtime.

## Verification And Acceptance

```powershell
& .\.venv\Scripts\Activate.ps1
foreach ($template in @('infrastructure', 'application', 'runtime-roles', 'mcp-authentication')) {
    & "$HOME\.azure\bin\bicep.exe" build "infra/v2/$template.bicep" --outfile "infra/v2/$template.json"
    if ($LASTEXITCODE -ne 0) { throw "Build failed: $template" }
}
python -c "import src"
python -m pytest tests/test_deployment_v2.py -o addopts= -q
python -m pytest tests/ -o addopts= -x
```

Offline tests check ARM write boundaries, symbolic-resource and literal-resource
forms, exact compiled RBAC scopes, completion ordering, existing network/DNS retention,
tag limits, initial install plus two updates, secret reuse, auth/schedule guards and
temporary-file cleanup. They do not perform Azure provisioning or prove registry pulls,
DNS routing, login/denial, PE Succeeded/Approved, actual log ingestion, Agent inference,
Archive access, email delivery or schedule readiness. Outputs deliberately keep
`applicationReady=false`; accept those customer paths before enabling automatic runs.
Deployment-record tests cover optional legacy tags, state/UTC validation, reserved tag room,
compiled success dependencies, customer-tag preservation and failed/preview timestamp retention.

Offline expression tests and JSON checks are not a real Portal Sandbox acceptance. Verify
the published ARM/UI pair in the authenticated customer Portal before production rollout.