# Two-Stage Deployment v2

An opt-in private profile with new/existing VNet selection, derived from the existing
[KT template](../kt/README.md) and [Enterprise template](../enterprise/main.bicep).
It does not replace their Portal buttons or adopt an existing KT installation.
Use an explicitly selected workload resource group. New workload resources need distinct
customer names; supported shared parents can remain in other RGs in the same subscription.

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
| [foundry-workload.bicep](foundry-workload.bicep), [project-access.bicep](project-access.bicep) | Account-RG project/Host/telemetry and project-scoped access, without redeploying a shared account |
| [storage-workload.bicep](storage-workload.bicep), [communication-access.bicep](communication-access.bicep), [shared-observer.bicep](shared-observer.bicep) | Workload containers and narrowly scoped access in referenced RGs |
| [mcp-connection.bicep](mcp-connection.bicep) | Exact project-RG RemoteTool connection after authorized Graph preparation |
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

### New Or Existing Shared Resources

The infrastructure form independently selects **new / existing** for four services.
New remains the backward-compatible default. Select an existing resource by full ARM ID;
the Portal resource picker includes other RGs in the explicit subscription. Cross-subscription
and cross-tenant reuse is not supported. A failed/forbidden read never becomes a request to
create a replacement.

| Service | New | Existing |
| --- | --- | --- |
| Foundry | Private AIServices account, then selected GlobalStandard models and workload project/Host | Retain the account, account Host and network; add an AzBrief-owned project/Host in that account's RG. Reuse model aliases only on the selected model/version/GlobalStandard contract; never replace another deployment |
| Container Apps Environment | Internal, PNA-disabled Environment and Log Analytics workspace | Retain Environment configuration, Consumption profile, VIP, domain, DNS and its actual workspace. Add App/Job/MCP in the workload RG using the saved Environment ID |
| Communication Services | ACS, Email Service and Azure-managed linked domain | Retain ACS, keys and linkedDomains. Select an already linked domain (automatic when unique); default `DoNotReply` must be supported and custom domains already verified |
| Storage | Private Entra-only account with the original state/archive container names | Retain the account and Blob service; create only `azbrief-state-<app-name>` and `azbrief-archive-<app-name>`. Refuse existing same-name containers at foundation, and scope data Contributor to those containers, not the account |

Use the same-region **existing VNet** when reusing Foundry or an Environment. Their actual
agent/infrastructure subnet IDs must match the selected VNet/subnets; occupied subnets are
accepted only for those verified existing owners. Both keep PNA Disabled and Entra/private
access, and the existing Foundry must already enable projects and have a ready account Host.
Private Endpoints and canonical DNS links for shared Foundry/Storage, and the Environment's
wildcard/root A records to its private VIP, must already work in that VNet. These shared
network/diagnostic settings are not rewritten. New-VNet mode cannot move a shared parent.

The existing Environment workspace is resolved from its log customer ID, not guessed from
its name and never replaced by a new empty-input workspace. Application Insights is new in
the workload RG and binds that workspace. Shared ACS's selected domain can be in another RG.
CLI resolves a unique domain automatically; multiple domains require an explicit linked
`existingEmailDomainResourceId`. A custom domain must already permit the `DoNotReply` sender.
No service keys or endpoint secrets are stored in RG tags.

For the protected parameter file, use these independent pairs:

| Mode parameter | Existing resource parameter |
| --- | --- |
| `foundryAccountMode` | `existingFoundryAccountResourceId` |
| `containerAppsEnvironmentMode` | `existingContainerAppsEnvironmentResourceId` |
| `communicationServicesMode` | `existingCommunicationServicesResourceId` |
| `storageAccountMode` | `existingStorageAccountResourceId` |

Each mode accepts `new` or `existing`. Existing leaf names are derived from IDs, so they are
not a second manual input. The CLI resolves `existingLogAnalyticsWorkspaceResourceId` and
the linked email domain where unambiguous. Shared choices, dependency IDs and workload
container names are frozen at foundation and verified on completion, MCP preparation, Agent
handoff and application updates. No old installation is automatically converted to shared use.
Use a unique naming prefix/suffix for each workload project and its apps; a shared account's
unrelated projects are allowed, but another workload's same-name project is never adopted.
Model reselection on a shared account requires the selected aliases and workload project to be
absent; unrelated projects/models do not authorize replacing either.
The shared ACS sender role definition is keyed to the workload's identity, so installing
another workload does not rewrite an existing common custom role definition.

The deployer needs child-resource and scoped role-definition/assignment permissions in the
referenced RGs as well as the workload RG. Runtime data permissions remain container/project
scoped; observer Reader grants are limited to the exact shared resources. Existing resource
reuse does not grant the control-plane identity query access to the shared log workspace.
Existing resource
tags, retention, SKUs, replicas, identities, domains, keys and other workloads are not changed.
The application stage remains App/Job/auth/MCP plus final deployment tags only.
Nested deployments into shared RGs include a deterministic workload/project suffix so two
workloads cannot overwrite each other's deployment outputs or role handoff. Initial creation
of common model aliases/custom sender role still needs normal provider concurrency handling;
existing compatible models are reused rather than resized or replaced.

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
with 200 `GlobalStandard` capacity units each. ARM/CLI `location` retains its backward-compatible
`eastus` infrastructure default; it is not an independent model location. No PTU SKU,
reservation or automatic PTU fallback is used. Logical aliases remain `gpt-5-terra` / `gpt-5-luna`.
The existing KT profile and saved v2 choices retain their previous defaults/configuration.
GlobalStandard processing can leave the account region.
ACS/email data location defaults to Asia Pacific. Validate policy, price and capacity
before opting into `--mode deploy`.

On 2026-10-15, read-only checks in the reference subscription reported quota/provider headroom
of 2,000/2,000 units for the selected Core and 1,000/1,000 for Simple in East US. This
supports the 200/200 defaults at that time; it reserves no capacity and guarantees neither
another subscription's availability nor runtime performance. Recheck both gates before deployment.

## Model And Region Selection

Infrastructure and model settings are separate. The misleading combined
`modelDeploymentRegion` control has been removed. An existing VNet supplies its verified actual
region; a new VNet uses the supported native Basics **New infrastructure deployment region**
control. Neither model choices nor model discovery can override the VNet/Environment location.
The VNet picker retains subscription scope and `location: all`; missing network region blocks
submission instead of falling back to a model region. No undocumented Basics `defaultValue`
property is used. RG metadata is not used as a substitute for the selected VNet's location.

The model options show the actual parent Foundry model/quota region. Direct
[`accounts/deployments`](https://learn.microsoft.com/azure/templates/microsoft.cognitiveservices/accounts/deployments)
resources do not accept a separate model-only `location`. GlobalStandard dynamically routes
inference globally; it does not pin processing to East US or another chosen region. This is
different from the account region used for deployment availability/quota. Models in a separate
regional resource can be used through
[connected models](https://learn.microsoft.com/azure/foundry/agents/how-to/connected-models),
but that needs explicit resource, authentication and private-connectivity design, and
`<connection>/<model>` Agent references. This template does not silently add a remote account,
API key, gateway connection or a second network. Removing the incorrect UI coupling does not
claim that unsupported same-account cross-region deployment is implemented.
The Portal also exposes `coreModelName`, `simpleModelName`, `modelVersion`, `simpleModelVersion`,
`modelCapacity` and `simpleModelCapacity` only for a new foundation. Logical aliases and the
six-role policy remain unchanged: Core uses `medium` reasoning and Azure MCP omits reasoning.
These are explicit inputs, not an automatic best-model selector. Check candidates without writes:

```powershell
& .\.venv\Scripts\Activate.ps1
python -m scripts.deploy_v2 infrastructure --subscription <subscription-guid> --tenant <tenant-guid> --resource-group <target-rg> --list-model-options --model-regions eastus eastus2 koreacentral japaneast swedencentral --candidate-models gpt-5.2 gpt-5-mini --mode preflight
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

The private Foundry account and Environment must remain compatible with the selected VNet's
actual region and exact subnets. This infrastructure constraint does not make model selection
a network-region control. Completion ignores all new selection controls and uses saved
`azbrief.v2.location`, preserving older Korea Central installs. Existing resources are not moved;
private/PNA/Entra restrictions remain unchanged.

A saved `foundation` blocked before any model/project was created may explicitly reselect the six
physical model/version/capacity fields with `--stage complete --reselect-models` and the approved
protected parameter file. Start with `--mode preflight`, then use the same file for `--mode what-if`
and `--mode deploy`. Region, aliases, names, network, data mode and other options remain immutable.
Existing models/projects, unreadable inventory or a later ready/pending state block this exception.
It never replaces models, rewrites tags by itself or resets Hosts. A partial attempt that created
models/projects needs separate diagnosis before retry.

The completion InfoBox renders the saved Core/Simple names, versions and region, including a
legacy missing Simple-version fallback. It deliberately does not advertise new defaults as the
current selection. For an older 5.6 foundation, normal completion still requests 5.6 and can hit
its exhausted quota; only the explicitly approved zero-model/zero-project CLI path changes it.

## New Or Existing VNet

Service modes and network selections are collected only during **Foundation**. Foundation
already writes the modes into `azbrief.v2.options0..7`, exact parent IDs into
`azbrief.v2.resources0..7`, and VNet RG/name plus PE/Foundry/Environment subnet names into their
existing tags. **Complete existing foundation** reads those values; it has no editable service,
VNet, subnet, workload-name or model-selection inputs. Application deployment likewise reads the
saved contract and shows a read-only service/network summary. No extra tags or tag-budget slots
are needed for this behavior.

For an Existing RG, foundation inputs stay hidden until the response ID matches the current
subscription/RG and the read succeeds without a pending page. A saved or incomplete v2 handoff
does not reopen fresh inputs. If Basics still says Foundation, an explicit warning directs the
operator to completion, MCP preparation or App deployment; it never changes the selected stage.
Completion and App hide the native deployment-location input and retain the saved region.

Choose new/existing services before networking and workload names. Reused Foundry and Environment
provide their exact infrastructure subnet IDs; the Portal and CLI derive the VNet, region and
role names from fresh typed ARM readbacks. Their subnet pickers are hidden. When either parent is
reused, the form inherits that VNet rather than offering a contradictory new VNet. Two reused
parents must agree on one VNet. A public or uninjected parent remains incompatible with this
private profile; no parent is converted or redeployed. Service errors appear beside the selected
service, model conflicts beside the model inputs, and the prefix validates only project-name
collisions. A unique Environment log workspace is resolved automatically; manual selection is
shown only when discovery cannot identify a unique workspace.

With no reused network parent, choose the mode in the Portal network tab or
`virtualNetworkMode` in the protected CLI parameter file. Existing mode retains the approved VNet
selector and asks only for new-resource role subnets, preserving CIDR/delegation/occupancy/DNS
checks. The shared roles are verified against their actual owners, not unused manual choices.
The PE role can be empty only when managed agent data is selected and no new PE is requested;
it is not a fabricated child ID. The CLI still rejects explicitly conflicting VNet, subnet or
region inputs. Display locations such as `East US` normalize to canonical `eastus`.
CIDR inputs for new mode never resize or reinterpret existing subnets. Cross-RG VNet selection
remains supported. Central DNS overrides and the optional new Environment PE are advanced inputs;
selecting a shared Environment clears the inactive new-PE output.

Subnet discovery uses a nonvisual `ArmApiControl` without a `visible` property. Its request
uses the working KT direct picker dependency when there is no reused network parent, or the
verified parent subnet's VNet ID otherwise, with `api-version=2024-05-01`. Never substitute a
subscription GET: a syntactically valid fallback can leave a non-VNet response and does not prove
that Portal observes later selections. Remaining dropdowns keep KT's name-only choices and
literal blank default. New-network and completion use their own saved/generated values and never
treat a subscription response as network data.

If no subnet choices appear, the network tab distinguishes an absent/different VNet response,
an ARM read error, a response without subnet data and a successfully read VNet with no
subnets. Selected/returned ARM IDs distinguish a retained subscription or old VNet response
from a missing response. Only safe identifiers/error codes are displayed, never error bodies.
Matching RG/VNet locations do not prove the request ran. Check the selected
subscription and `Microsoft.Network/virtualNetworks/read` access on the actual network RG.
Denied, missing or stale reads cannot authorize deployment; do not force validation tags,
invent subnets or broaden permissions. Publish the generated UI before retesting in a new
Portal tab. An exact-request/live-ARM replay verifies returned choices, not the authenticated
Portal rendering or a completed infrastructure/application deployment.
Both v2 forms explicitly set `isWizard=true`: the supported wizard flow makes **Basics** the
initial available step and validates each step before **Next**. This intentionally replaces
v2's free tab navigation; the legacy KT form is unchanged. The infrastructure Basics stage
selector remains populated. The normalizer also rejects duplicate names in every native scope,
including nested Sections and step names. The duplicated `savedNetworkNotice` that previously
reached the published infrastructure form is now emitted only once. These structural checks
do not replace authenticated Portal first-render acceptance.

The real Portal investigation reproduced both failures after loading the exact published
ARM/UI pair: no initially selected tab, and no subnet control output even though an ARM batch
returned the selected Korea Central VNet with ten subnets. A minimal live Portal trial showed
that a nested lambda cannot resolve an enclosing `saved` parameter; a single-scope lambda and
its null-safe equivalent work. Python's lexical closures had hidden this defect in offline
tests. The generator binds source tags before deriving saved options/resources, passes values
between independent lambda scopes, and expands remaining shared references within nested
lambda bodies. Binding validation no longer captures an enclosing `opts` lambda; shared-owner
checks stay outside per-subnet predicates, and foundation tag budgeting uses only current
foundation inputs. No ownership, scope, model, tag-budget or readiness checks are removed.

Native Portal completion exposed a second evaluator mismatch: comparing an empty leading or
trailing `split` element with `''` returned false even though the same offline Python expression
passed. Saved resource JSON therefore became `{}`, and the true-only ARM guard correctly blocked
deployment. Both forms now use documented `startsWith`/`endsWith` boundary predicates for JSON,
ARM roots, container-name suffixes, ACS host suffixes and application UTC timestamps. Regression
tests exercise the native empty-element case and the exact parsed eight-field resource shape.
Do not replace a failed guard with a constant or change stored tags to work around this defect.

The fresh-install replay also combines native empty-element semantics with eager branch
evaluation. An unused shared subnet/email parent can contain empty split elements while the
new-VNet branch is selected; those elements must be normalized before `concat` or `toLower`,
even when an outer `if` ultimately returns an empty ID. The generator preserves valid IDs,
returns empty/invalid evidence as before, and retains the false readiness guard. Regressions
cover unloaded and populated new/existing network forms plus an empty application form.
This is predeployment expression evidence, not authenticated Portal-rendering acceptance.

An authenticated Portal bisection also isolated model-reuse outputs as an initialization blocker:
before any model query, an empty candidate's `properties.provisioningState` was traversed. A final
`coalesce` cannot protect that missing parent. The generator now normalizes the properties, model
and SKU objects and gives an unloaded candidate an explicit empty/zero failure schema. The real
inventory still needs exactly one compatible row; these placeholders cannot make reuse succeed.
Regression tests model the native missing-parent exception, including absent/null model metadata.
Native first-render/Review testing may route the local corrected UI into the authenticated Portal
while keeping real ARM responses. Such evidence is not a claim that GitHub's public pair changed.

### Portal expression cost

The normalizer projects only the tag keys an expression reads, validates the saved eight-field
binding directly, and binds a model candidate together with its expected options. Foundation-only
subnet/owner checks use current foundation inputs instead of repeatedly decoding completion
metadata. Tag budgeting binds the source tags once and uses integer ceiling division for actual
256-character chunks. All scope, ownership, model, chunk, 2,048-character and 50-tag checks remain;
there are no mutable hidden cache controls or extra discovery requests.

In the expression-optimization comparison, before the later saved-stage visibility and wizard
changes, the infrastructure form decreased from 1,480,561 to
712,995 UTF-8 bytes and the application form from 117,944 to 58,172 bytes. The completion guard
decreased from 299,188 to 183,883 characters. Nine-sample synthetic guard-replay medians decreased
from 289.5 to 135.8 ms for a new foundation, 226.1 to 146.8 ms for shared services, and 279.8 to
138.8 ms for completion. These include the Python harness's parsing/evaluation work; they are
**not browser Next-button timings** and do not measure ARM request latency.
Regression budgets prevent either form exceeding 900,000 unindented UTF-8 bytes or a 200,000-character
guard. They are growth limits, not a user-facing latency SLO.

A public form can still contain the old boundary predicate until the paired artifacts are
published. Compare the exact fetched ARM/UI revision before retesting. A live-tag expression
replay and a real ARM deployment through an authorized CLI are separate from customer Portal
acceptance. If the browser is denied by device-compliance policy, stop that path; never copy CLI
tokens into it or claim an authenticated Portal interaction result.

Live CLI acceptance of the completion change replayed all 57 form outputs against a real saved
foundation and matched every value to strict preflight. The unchanged public infrastructure ARM
passed validation and what-if, then deployed successfully with those computed outputs. Readback
confirmed both original model/version/capacity selections, the project and Host, unchanged saved
bindings/options, and retained private networking. Managed MCP correctly stopped at `mcp_pending`;
directory preparation, application installation and runtime delivery were not part of that test.
The automated browser was denied by device-compliance policy, so authenticated Portal navigation
and end-to-end Next-button latency remain unverified.

Earlier lambda-fix acceptance, before the resource-first redesign, used the authenticated Portal and
unchanged live Azure data: both forms auto-selected Basics without clicking a tab; the
infrastructure form populated all ten subnet names, accepted all three role selections and
refreshed after switching VNet. With Korea Central, the existing production VNet and its
verified unused test subnets, all infrastructure tabs reached actual Portal Review final
validation with Create enabled (not clicked). The application form rejected an actual unprepared RG.
Static UI substitution is explicitly local testing, not publication. The test did not click
Create, create resources, force `ready`, or establish application/runtime delivery readiness.
No ready-tagged workload RG was available for a real successful application installation.
Publish the paired source artifacts and repeat customer acceptance before rollout.

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

Application defaults to the workload `resourceGroup().tags` and resolves IDs/endpoints from
the saved full IDs and actual existing resources, including other RGs. A contiguous sequence
of `azbrief.v2.resources0` through at most `resources7` stores one bounded JSON object:
the Foundry, Environment, Storage, ACS, workspace and email-domain IDs plus the two workload
container names. Each chunk is at most 256 characters; the complete object is at most 2048.
Partial chunks, invalid types/subscriptions and ID/name disagreement block writes.
Older contracts without this block retain the original same-RG name resolution; they are
not treated as shared installations. Operators do not re-enter all
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

Azure permits 50 tags per RG and 256 characters per value. V2 reserves the original 41 slots
plus only the ID chunks actually needed (at most eight); the CLI checks the merged budget
before provisioning and preserves customer tags. Long IDs/too many customer tags produce an
explicit error rather than truncation or deletion. Resource tags have a separate
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
Comparison with the working KT definition retains its VNet selector options, three exact
subnet choice expressions/defaults and five validation rules. V2 intentionally differs in
navigation by enabling the Basics-first wizard. `bladeTitle` is optional in the official schema
and also absent from some KT steps;
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
KT default `false`: create only the PEs needed by new parents, and retain shared PE/DNS.
No endpoint is
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
For shared Foundry, that output also carries the exact account ID/custom subdomain. The
guarded handoff validates the same subscription and project path and binds azd to the
Foundry RG, while the deployment output, Insights and App/Job stay in the workload RG.
Admin readiness also consumes typed full IDs instead of guessing shared parents' RGs.
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
For an initial immutable ACR image, the template derives its registry host and uses the saved
control-plane UAMI for both App and Job. This is first-install-only; explicit App/Job registry
configuration overrides it, and updates retain existing authentication and secrets. Pregrant
image pull in the infrastructure/access preparation: an ABAC registry needs Container Registry
Repository Reader restricted to the exact repository; legacy RBAC uses its supported pull role.
Do not enable the ACR admin account, make the repository public, grant registry-wide pull for
convenience, or put registry credentials in ordinary environment variables. Non-ACR registries
continue to require explicit configuration. The app template creates no registry role assignment.
Native Portal updates read existing App/Job settings and API secrets through the independent
[application-snapshot.bicep](application-snapshot.bicep) deployment. Its output is `secureObject`,
consumed server-side with `listOutputsWithSecureValues`; no secret values enter UI outputs or
ordinary deployment history. The module never depends on the App/Job it snapshots. Initial
installation runs it with reads disabled, so absent resources are not queried. This removes
the ARM circular dependency caused by referencing an App directly while overwriting that same
App. App/Job, authentication and requested MCP completion still precede the successful tag write.
If a first install failed after creating the App but before creating the Job, normal updates
still refuse the partial baseline. Diagnose the original failure first. Once the exact owned
App is stable and its existing API secret is readable, use explicit
`--recover-initial-install` with the same immutable image and reviewed configuration, first
`--mode preflight`, then `validate`/`what-if`/`deploy`. Recovery requires
`applicationState=not_deployed` with an empty successful-deployment timestamp, retains all App
settings/authentication/secrets and copies the same API secret to the missing Job. It rejects
foreign identities, in-flight resources, job-only state and missing/unreadable keys; it never
deletes resources, invents a replacement key or resets a completed installation.
For a registry-only initialization failure, the RP's documented JSON Merge Patch can correct
only `properties.configuration.registries` without sending a `secrets` field; confirm the App
reaches Succeeded before recovery. CLI wrappers that list secrets may themselves fail while
the initial App is Failed. Do not turn that read failure into a key reset.
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