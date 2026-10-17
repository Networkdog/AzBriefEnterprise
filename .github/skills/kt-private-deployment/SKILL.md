---
name: kt-private-deployment
description: 'Prepare, review, and troubleshoot agent deployments in the KT private Azure environment. Use when: KT deployment, KT private network, existing VNet, subnet selection, Container Apps Environment policy, publicNetworkAccess Disabled, Private DNS overlap, virtual network link conflict, appLogsConfiguration discriminator, isolated deployment, bootstrap image, manual agent release.'
---

# KT Private Deployment

This is a GitHub Copilot developer skill, not a Foundry Agent runtime instruction.
Use it for AzBrief and for other agents that deploy Microsoft Foundry, Container Apps,
Private Endpoints, or supporting data services into the KT environment.

## When to Use

- Creating or reviewing a KT-specific ARM/Bicep deployment
- Adding another Hosted Agent or Container Apps workload to the KT network
- Troubleshooting KT Policy denial or Private DNS link conflicts
- Designing a Portal `createUiDefinition.json` for existing KT infrastructure
- Preparing a manual release when GitHub-hosted CI/CD cannot reach the deployment environment
- Reusing the lessons from `infra/kt` without copying AzBrief-specific runtime architecture

## Sources of Truth

- Operator guide: [`infra/kt/README.md`](../../../infra/kt/README.md)
- Customer firewall/DNS matrix: [`infra/NETWORK_REQUIREMENTS.md`](../../../infra/NETWORK_REQUIREMENTS.md)
- Bicep source: [`infra/kt/main.bicep`](../../../infra/kt/main.bicep)
- Compiled Portal artifact: [`infra/kt/azuredeploy.json`](../../../infra/kt/azuredeploy.json)
- Portal UI: [`infra/kt/createUiDefinition.json`](../../../infra/kt/createUiDefinition.json)
- Read-only preflight/deploy CLI: [`scripts/deploy_kt.py`](../../../scripts/deploy_kt.py)
- Guarded Agent handoff: [`scripts/setup_kt_agents.ps1`](../../../scripts/setup_kt_agents.ps1)
- Contract tests: [`tests/test_kt_deployment.py`](../../../tests/test_kt_deployment.py)

Never hand-edit the compiled ARM JSON. Change Bicep, compile with the pinned compiler, and keep
the ARM/UI pair from the same source revision.
Top-level READMEs expose only two plain-text v2 infrastructure/application Portal links, paired
with their exact CreateUiDefinition files. Do not restore Deploy image buttons or legacy
Enterprise/KT template links there; preserved legacy templates remain internal compatibility
assets. Check the exact public v2 ARM/UI pair, not just local source. Do not restore the combined
modelDeploymentRegion network control or call it an independent GlobalStandard region. Keep customer
RG resource-name inputs, automatic aliases/MCP settings, explicit v2 models and guarded live
preflight.

## Two-Stage Template v2

V2 can independently reference existing Foundry, ACS, Container Apps Environment and
application Storage by exact typed ARM ID in the same subscription, including other RGs.
Its Portal order is services, network, then workload names. Derive shared Foundry/Environment
VNet, region and subnet roles from exact live readbacks rather than asking for the same subnet
again. Hide shared-role/new-VNet controls, neutralize hidden new-Environment PE values, and ask
only for subnets needed by new resources. Shared-only managed data can omit the PE role only
when no new PE is requested; preserve an empty ID, never a fabricated `/subnets/` path.
The CLI rejects explicit network/region conflicts. Put compatibility errors beside each service
and model errors beside model options, not the common prefix. A uniquely discovered Environment
workspace needs no extra picker. Public/uninjected shared resources remain unsupported by the
private profile. Repeat actual Portal testing after this redesign, not only the earlier lambda fix.
Never redeploy the shared parents, network, diagnostics, keys or unrelated workloads.
Create only workload projects/Hosts, apps, private named Blob containers and scoped access.
Shared Foundry/Environment must already use the selected same-region VNet/subnets and
private DNS/approved PEs; occupied subnets need those exact owner bindings. Retain the
Environment's actual workspace and ACS's already linked verified sender domain.
Full IDs and container names use contiguous resources0..resources7 tags (2048 chars total);
count actual chunks plus the original 41 slots against the 50-tag limit, never drop tags.
Save each mode in options; old no-ID contracts stay same-RG and cannot become shared by
completion. Verify project workload ownership, selected model aliases/GlobalStandard and
exact IDs through App/MCP/Graph/Hosted grants/Agent setup/readiness. Keep scoped Blob data
Contributor on the workload containers, not all shared account data. Never add PTU fallback.

The opt-in [`infra/v2`](../../../infra/v2/README.md) profile reuses this private-network
security/lifecycle knowledge but does not replace the KT Portal button or adopt old resources.
Its one-time infrastructure command internally sequences foundation, account-host readiness
and model/project/Host/RBAC completion without an App image. New foundation saves 41 base
`azbrief.v2.*` RG tags plus bounded ID chunks, all non-secret. Managed MCP core completion records
mcp_pending. Portal infrastructure contains zero Microsoft.Graph resources; authorized CLI-only
mcp-authentication prepares app/project app-role/RemoteTool then promotes ready. Full CLI deploy
runs it automatically; Portal operators use --prepare-mcp-authentication once without internal ID
inputs. Keep same-core-output/private/BYO/uniqueName/budget checks and ready-only App acceptance.
New v2 uses the documented Basic VNet Microsoft-managed agent data mode: keep injection,
PNA Disabled and project Host but omit the three BYO connection properties. Create no default
Cosmos/Search/BYO data roles or their PE/DNS; retain app-owned private Blob state/Archive.
Mode lives in option tags; missing old flag means BYO and explicit false/original names is
required for legacy completion. Never switch/reset old Host data, delete old stores, weaken
network/auth or confuse Basic VNet with Standard-private BYO requirements. Only v2 changes;
standard/KT profiles keep BYO. Five canonical DNS namespaces replace the old seven for the new managed mode. Count only
newly requested PEs for the subnet IP budget; shared parent PEs/DNS are retained. Keep stable skipped PE indices.
Verify actual Host/API/region and private MCP behavior separately from offline validation.
Only v2 adds existing/new network choice. Existing mode keeps all customer VNet guards and
never PUTs it; new mode creates one target-RG VNet and validated role subnets before foundation,
with explicit name-collision refusal and no peering/NSG/routes/public IP changes. Save mode and
CIDRs in bounded options; completion verifies exact owned network and never recreates it.
Legacy missing mode is existing; App updates are still network-free. Network-only acceptance
is not full infrastructure/App success, especially when model quota blocks completion.
V2 new foundations select GPT-5 physical models, independent versions/capacities and region;
default to East US, gpt-5.2/2025-12-11 and gpt-5-mini/2025-08-07, GlobalStandard 200 each.
Never use PTU SKUs/reservations/fallback. Preserve logical aliases, role reasoning, saved older
profiles and KT defaults. New omitted Simple version uses its independent default; explicit empty
and old missing saved versions use Core. --list-model-options is read-only catalog/quota/provider
capacity discovery, not runtime acceptance. Strict v2 selection never uses KT's fallback and
reuses models only on exact model/version/SKU match. Combine shared quota separately from
version-specific provider capacity. Model discovery regions never mutate infrastructure.
--stage complete --reselect-models changes
only six physical model fields on saved foundation with zero deployments/projects; all other
choices stay immutable. Block partial/existing state, denied reads, tag forcing and Host resets.
For explicitly shared Foundry, zero-state applies to the selected aliases/workload project,
not unrelated projects/models in the account. Never adopt or replace those other workloads.
V2 Portal uses actual existing-VNet location, or native Basics infrastructure location for a new
VNet. Use documented VNet location=all with subscription scope and exact known region checks.
Model settings cannot redirect VNet/Environment. Show actual parent Foundry model/quota region:
direct accounts/deployments has no independent location, and GlobalStandard inference is global.
Connected models in a separate regional resource need an explicit authentication/network design;
do not silently add accounts, keys or gateway connections. Completed KRC profiles keep their region.
Completion InfoBox renders actual saved models/versions/region, not new-default marketing text.
Preserve legacy missing Simple-version fallback and safe unloaded parsing; identify the explicit
zero-model/project CLI reselection path without adding silent swaps or readiness bypasses.
Two application tags record not_deployed/empty initially, then deployed and the successful
ARM application's UTC start time after App/Job/requested auth/MCP dependencies. Preserve
customer and previous-success tags; failed/preview paths never advance them. Legacy 39-tag
contracts remain accepted as unrecorded, not proof of an absent App; never invent old dates.
Keep tag-write permission, two-slot budget reservation and live inventory/auth checks. These
tags neither change infrastructure state nor prove runtime readiness or Agent publication.
The same-RG application template owns App/Job/auth and the pinned read-only MCP container;
the shared MCP module must accept its existing infrastructure-owned UAMI and bind the outgoing
AZURE_CLIENT_ID. Preserve the optional empty-input SystemAssigned path for other profiles and
never move Reader grants into the application template. Compile the parent and test the source
parameter declaration as well as the compiled identity/credential expressions.
Core output must carry all saved network/identity names. For older absent names, recover only
from exact saved ARM IDs; reject present mismatches, missing IDs and redirected targets without
replaying successful infrastructure. Windows MSI AzureCli uses bundled Python with explicit
-X utf8, not the batch shim, preserving Graph query URLs and Korean ARM/policy JSON. Docker fingerprinting
includes the explicitly selected hidden .dockerignore with Get-Item -Force on Linux.
[`scripts/deploy_v2.py`](../../../scripts/deploy_v2.py) preserves current keys, secrets,
environments, registry/auth settings and accepted schedules on repeated updates. Missing,
paged, denied, foreign or redirected reads remain blocking. Retain canonical linked DNS,
private ingress and existing subnet protections, tag limits and unrelated tags. Raw templates
are not substitutes for live preflight. Initial Hosted identity permissions are late
infrastructure completion after guarded Agent publication, never a broad role or infrastructure
replay during App upgrades. v2 keeps Entra-only UI; it does not inherit KT's temporary anonymous
exception. Normalize its real UI forms with scripts/build_v2_ui.py and retain all five existing
KT subnet/VNet validators. Directory app/role permissions remain separate from ARM permissions.
Guard initial UI output parsing with empty RG/tag/VNet state; test both conditional branches,
not only completed selections. Preserve completion/ownership guards. A CreateUIDef
CustomDeploymentBlade error is a Portal UI symptom, not an Azure Graph deployment verdict.
Read dotted tag names through temporary dot-access projections, preserving stored keys and
option JSON values. Do not equate the Python test evaluator with the actual Portal parser;
repeat symptoms need authenticated CreateUIDef diagnostics, not another success claim.
Keep the working KT selector/subnet choice contract. V2 explicitly uses isWizard=true to start
at Basics and validate each step before Next; leave the legacy KT navigation unchanged.
Foundation stores service modes in options chunks, full IDs in resource chunks, and the exact
VNet/subnet names in existing tags. Completion/App never ask for those choices again. Gate all
foundation inputs on the selected Foundation stage and a New RG or exact successful unbound RG
read. Saved/loading/denied/stale RGs never reopen a fresh form. Warn when a saved RG is still on
Foundation without silently changing the stage. App shows a read-only saved setup summary.
Reject duplicate control names before publishing, including nested Sections and step names.
Compare schema-required differences before editing optional titles or shared properties.
VNet RG outputs must come from the selected ARM ID, not the picker's optional resourceGroup
field. Test id/name/location-only picker fixtures, cross-RG networks, customer casing and
saved-tag completion without a live picker. Never invent the application RG as a fallback.
Keep Bicep/ARM sources in sync and distinguish offline tests from customer Portal acceptance.
Common namingPrefix/namingSuffix inputs stay in the ARM contract and saved options.
Hide validation rows without dropping the internal portalValidationPassed=true constraint;
compile the current sources and verify every UI output matches an ARM parameter.
Graph Bicep authorization is the intersection of client delegated scopes and user directory
privileges; ARM Owner or Global Administrator membership alone does not prove Portal success.
An approved exact-module CLI/PowerShell recovery keeps uniqueName and authentication, not a
new broad grant. A child success is not parent/tag readiness. MCP connection requests use
RemoteTool/ProjectManagedIdentity, explicit audience and 2025-10-01-preview, not RemoteToolServer.
Validate actual request acceptance and preserve successful model/project/BYO/role state on retry.
V2 foundation also owns the internal Environment's defaultDomain private DNS with private
staticIp wildcard/apex A records and a non-registering approved VNet link; ACA PE DNS alone
does not resolve internal ingress. Existing missing zones need exact DNS-only recovery, not
foundation/PNA/auth replay. A cluster-success exec without a real HTTP success marker is failure.
For v2 updates, only parsed AuthConfigNotFound/observed Not Found(JSON) is optional-auth absence;
403, malformed/forged wrappers and other NotFound remain blocking. UI enablement still needs Entra.
V2 App foundation/pending failures identify unmet readiness, not a broken true-only guard.
The existing application image field has native state validations naming the missing core/
MCP step before ARM submission; do not reintroduce editable guard rows. Foundation Succeeded
is not full infrastructure readiness. Compare the exact live RG and published ARM/UI, and
distinguish local CLI/snapshot acceptance from the customer's public Portal path.
DNS completion distinguishes empty saved overrides from explicitly bound central zones: seven
owned same-RG defaults with verified links are valid, redirected/denied/incomplete zones are not.
Preserve saved options and quota allocations. Explain manual subscriber/principal/UI/schedule
overrides separately from automatic first CLI install: directory-verified deployer mail for email/
feedback, deployer object ID for Admin/Archive, all UIs on with an owned single-tenant web app and
365-day App-only secret. No Graph writes in preflight/validate/what-if or Portal; materialize only
for CLI deploy and revalidate/preview the exact issued parameters before ARM create. Missing mail,
app-only login, paged/denied/redirected/foreign directory state and orphaned credentials block.
Retain explicit configuration and installed settings; never rotate existing credentials or start
mail/schedules automatically. Disclose minReplicas=1 idle cost, renewal and private login/send
acceptance. Bare Portal first install remains UI-off; use CLI first setup then Portal or CLI updates.
These defaults are v2-only, not a change to the existing KT profile or its manual web contract.
Keep Archive/Feedback base URLs in the shared App/Job runtime and initial Job feedback enablement
aligned with the App, so configured pages are also reachable through delivered report links.

## Non-Negotiable KT Contract

| Area | Required behavior |
|---|---|
| Existing network | Reuse the approved VNet. Never replace it or PUT its full subnet collection. |
| Subnet identity | Names are not fixed. Select explicit role mappings and preserve the actual names. |
| Private Endpoint subnet | RFC1918 IPv4, `/28` or larger, no service delegation, enough verified free IPs. |
| Foundry subnet | Separate `/27` or larger subnet delegated to `Microsoft.App/environments`; one Foundry account only. |
| Container Apps subnet | Separate `/27` or larger subnet delegated to `Microsoft.App/environments`; never share it with Foundry. |
| Container Apps Environment | `vnetConfiguration.internal: true` and `publicNetworkAccess: Disabled` must both be in the initial `Microsoft.App/managedEnvironments` PUT. |
| Container Apps PE | Temporarily manual by default (`deployContainerAppsPrivateEndpoint=false`). Keep the Environment/App and four other PEs. Explicit true restores only the ACA PE and its DNS zone group. |
| Private DNS | Reuse a required zone already linked to the VNet. Create only missing namespaces. Never link a second overlapping zone. |
| Private Endpoint records | Point the new Private Endpoint DNS zone group at the canonical existing zone. Let Azure manage its A record; do not copy managed records between zones. |
| Private Endpoint readiness | Final readback requires every automatically managed PE to be `Succeeded` and `Approved` with the expected target/subnet. Manual ACA PE acceptance is separate; never label an ignored endpoint healthy. |
| Optional ACA logs | Pass `null` when no Log Analytics workspace is selected. AVM `0.16.0` accepts only `azure-monitor` or `log-analytics`, not `none`. |
| Resource exposure | Disable public network access on Foundry, storage, Cosmos DB, Search, and the Container Apps Environment at creation. |
| Initial application | Use the pinned GHCR AzBrief image on 8000 `/health`, not hello-world. Stage one keeps Admin/Archive off and auth required. Image startup is not operational readiness. |
| Stage-two web setup | Accept an existing customer-tenant Entra client ID, secure secret and explicit administrator allow-list; Archive readers are optional. Configure EasyAuth, protected UI flags, Admin config/readiness and Archive links. Admin uses minReplicas=1 with disclosed idle cost. Never create a directory app or issue a secret; control-plane roles follow the explicit stage-two contract below. |
| Temporary anonymous exception | `enablePrivateAnonymousWebUi` is false by ARM default and acts only in stage two. The Portal offers it with mandatory risk acknowledgement and Entra off by default. Explicitly disable EasyAuth and both UI auth requirements only inside the internal/PNA-disabled KT profile; retain API/MCP keys, MI, HTTPS and minReplicas=1. Every reachable private client can manage/read; no user-level audit or production-auth claim. |
| Project timing | Stage one creates the Foundry account but not the project, its connections or project roles. After the account host is ready, stage two references the account as `existing` without replaying its custom subdomain, then creates the project, role/connection bindings and BYO host together. Stage-one project IDs are planned; its principal is empty. |
| Application image | Use an approved immutable digest and matching registry authentication. Do not deploy `latest`. |
| GHCR credentials | Default Anonymous after verifying Public visibility and anonymous layer downloads. Explicit Credentials accepts only a read-only PAT via App secret. Never grant GHCR an Azure identity role or deploy the publisher token. |
| API key lifecycle | No Portal key input. ARM generates the initial secure random key; complete Portal/CLI ARM inventory selects existing-secret reuse. Read failures or missing keys never trigger rotation. Keep API authentication. |
| Identities | Keep Container Apps control-plane, Foundry project, Hosted Agent, and Azure MCP identities distinct. |
| Runtime roles | Stage two automatically assigns UAMI RG Reader, project Foundry User and state/archive container Blob Contributor, plus configured resource-scoped email/Insights publishers. Automatic email creates only its three-action custom sender role. Require roleDefinitions/write and roleAssignments/write; never grant Hosted evidence roles, broad/key/delete rights or remove old assignments. CLI-only false is a manual exception. |
| Storage | One `storageAccountName` and one Blob PE. Keep app state/archive separate and App/Job grants container-scoped. Retain project-ID BYO provisioning/data roles; these share the trust boundary, not hard account isolation. |

Built-in policy `d074ddf8-01a5-4b5e-a2b8-964aed452c0a` is named “Container Apps environment should
disable public network access,” but its actual rule denies a missing or false
`Microsoft.App/managedEnvironments/vnetConfiguration.internal` alias. Do not treat the display name
as proof that setting only `publicNetworkAccess` is sufficient. KT requires an internal load
balancer environment **and** disabled public network access. `ingressExternal: true` on an app means
environment-level ingress; in this internal environment it remains reachable only through approved
private/VNet paths.

## Portal Form Design

1. Put deployment-stage selection in the first Basics tab and reference
   `basics('deploymentStage')` in all later gates/outputs. Do not choose it again at Review or
   silently switch it from a resource name. Existing RG and stage are independent; stage two
   requires an existing foundation, while a first installation may also use an Existing RG.
   Select an existing VNet filtered to the deployment subscription and region.
2. Read the VNet through `Microsoft.Solutions.ArmApiControl`. This is a nonvisual API
   dependency, not a visible input; do not attach the subnet fields' `visible` gate.
   Keep the working KT request `concat(steps('network').virtualNetwork.id, '?api-version=2024-05-01')`.
   V2 may resolve the VNet ID from a reused parent's verified subnet instead; the no-shared-parent
   branch retains the direct picker dependency. Never replace an unresolved selector with a
   subscription GET: that valid fallback does not prove reactive VNet loading.
   Test the serialized dependency, selected-ID changes and name-list output, not only
   dropdowns with a prefilled response. Distinguish absent/different response IDs, read
   failure, missing subnet data and an actually empty list using safe IDs/error codes.
   Never surface error bodies, assume a read failure means no subnets, or auto-grant access.
   Explicit isWizard=true starts both v2 forms at Basics with sequential validation; this does
   not change legacy KT navigation. Remove duplicate saved-network notices and reject duplicate
   native names during generation. A screenshot with no initial active tab still needs Portal
   diagnostics; do not claim browser acceptance merely because clicking Basics or local tests work.
   Real Portal probes also established that nested map/filter lambdas cannot capture an
   enclosing saved/opts/context parameter. This caused blank initial tabs and absent VNet
   output despite successful ARM data. Keep each predicate's inputs in its own binding and inline
   remaining shared references; move static owner checks outside per-subnet predicates. Retain
   binding/mode/tag-budget/readiness checks and reject cross-lambda captures in both generated
   forms. Python lexical closure tests alone cannot establish Portal compatibility.
3. Use simple independent dropdowns for the three role subnet selections in legacy KT stages.
   V2 asks for them only during Foundation and restores tags during completion/App:
   - populate choices only from the selected VNet's subnet names using a short nullable-safe map;
     retain a literal empty-choice default, never an automatic first subnet;
     compose the choice array through JSON strings rather than assuming ARM-style array concat;
   - keep resource names as TextBoxes; do not restore existing-resource selection or auto-defaults;
   - derive CIDR and delegation from ARM;
   - do not ask the operator to re-enter CIDR;
   - validate selected names separately against the VNet and reject invalid sizes, non-RFC1918 ranges,
     multiple/conflicting prefixes, wrong delegations, and
     Container Apps reserved ranges.
   - Do not reintroduce complex prefix/delegation filtering or references to other subnet controls
     in the list initializer after the earlier customer control-creation failures. Keep name-only
     choices, separate validation and current VNet response identity; no fabricated
     first choice or skipped CIDR/delegation/reserved-range validation.
   - Nullable/empty prefix arrays and unrelated IPv6 rows must not break validation. Pad arrays
     or select a guaranteed non-empty fallback before first(), avoid unsafe numeric casts, and test eager branch evaluation as well as
     the normal expression path. A reproduced local first(null) risk is not a captured Portal
     exception or customer acceptance. Keep distinct roles and selected names unchanged.
4. Output the selected subnet names. The Bicep template constructs the exact child resource IDs.
5. Use Azure Resource Graph to find required Private DNS zones already linked to the VNet in the
   selected subscription.
6. Default to automatic DNS reuse. Keep an explicit central-DNS override for cross-subscription or
   unreadable zones.
7. Treat CreateUiDefinition schema checks as structural only. Preview the published ARM/UI pair in
   the Portal Sandbox and perform a customer-like validation; the legacy schema does not model every
   `Microsoft.Solutions` control.
8. Read the exact typed Environment through ARM in stage two. Reuse
   it only when `internal=true`, PNA is `Disabled`, and the KT ownership tag matches; otherwise block
   the form before ARM validation and inspect the actual ARM values before planning recovery.
   Check the full returned ID and selected infrastructure subnet. The 2026-01-01 ARM response
   uses Boolean true; numeric/string substitutes stay unknown. The old ARG string-normalization
   workaround is historical. A compliant stage-one Environment must pass with the same name.
   First-stage/New-RG requests use a filtered list instead of GETting an absent target.
   Missing/unknown values are not proof of an internal environment. Keep PNA and
   ownership checks; never bypass them based on the selected stage or recommend deletion solely
   because a name exists. Evaluate the serialized predicate against typed fixtures, not only
   substring assertions, and distinguish live query verification from a full Portal deployment.
9. For `resourceGroup().mode == 'Existing'`, use the RG-scoped Container Apps list API instead
   of generic subscription inventory. Preserve the RG-filtered generic request for `New` groups
   that do not exist yet; do not bypass a 404 or invent absence. Validate returned `value`, `error`
   and `nextLink` fields directly. A returned empty array is a valid new-install result, while
   missing/null results, real errors, pending pages and foreign ownership remain blocking.
   Null/empty error fields are not failures. Show only response state, error code and page
   presence, never error bodies or signed continuation URLs. Test both generated request paths
   and the guards; a live ARM read or offline expression test is not Portal Sandbox acceptance.
10. Resource names are direct TextBoxes for both RG modes; pass the entered leaf names to
    Bicep without hidden existing-resource fallbacks. Optional selection/project inventory and
    auto-subnet defaults caused a customer Portal control-creation failure and are removed.
    Keep essential App/key and typed Environment safety checks plus name uniqueness. Use
    CLI preflight for other ownership and BYO bindings. Stage two still requires the existing
    foundation and omits account creation. Do not mask essential errors to make the UI green.
    Local tests/schema are not customer Portal acceptance; verify the published pair separately.

Portal discovery depends on the deploying principal having read access to the VNet links and zones.
V2 native completion also needs explicit string-boundary predicates. A live minimal Portal
probe returned false for empty split-element equality where Python returned true. Use
startsWith/endsWith for saved JSON, ARM roots, container/ACS suffixes and application timestamps;
preserve typed IDs, contiguous chunks, all eight resource fields and the true-only guard.
Do not repair this by forcing a guard or editing RG tags. Inactive parent-ID assembly must
coalesce native null split elements before concat/toLower, including subnet and email parents.
Test native-empty semantics and eager branches together for initial/new-VNet/App states, not as
separate checks. Empty IDs stay empty and cannot satisfy readiness. Verify both forms.
Model reuse also needs null-safe properties/model/SKU parents. Native coalesce does not catch a
missing intermediate parent, and even an inactive candidate can be evaluated during initialization.
Use an empty/zero unloaded schema and retain the real match-count/model/version/SKU/capacity
requirements. Never seed a success status or positive capacity. Include strict missing-member
regressions and authenticated first-render/Review checks. Local routing of a corrected artifact
in Portal is native-engine evidence, not public publication.
Keep v2 navigation work bounded: project only referenced tag keys, bind source tags before
derived options/resources, validate the binding directly, and bind a model candidate together
with its expected options. Do not expand saved completion JSON in foundation-only subnet checks.
Bind tag-budget inputs once and compute 256-character chunks with integer ceiling division,
retaining actual chunk counts, the 2,048-character payload limit and the 50-tag ceiling.
The generated forms enforce 900,000 unindented UTF-8 bytes and 200,000 guard characters in
regression tests. No checks may be dropped and no mutable hidden caches added to meet those
budgets. Offline replay timings do not establish actual Next-button latency. Publish the exact
ARM/UI pair and repeat authenticated Portal acceptance; a device-compliance denial stays blocking
and must never be bypassed by transferring CLI tokens to the browser.
For first installation of an immutable ACR image, App/Job default to the saved workload UAMI;
explicit registry settings override this and updates retain them. Pregrant only exact-repository
pull access (ABAC Repository Reader when applicable). Application deployment never creates
registry roles or enables the registry admin account.
An App-only interrupted first install is not a normal update. The explicit
`--recover-initial-install` path requires a never-deployed tag pair and the original readable
App API key; it preserves App settings/auth and copies that key to the missing Job. Wrong owner,
identity/environment/profile/topology, in-flight state, job-only state, completed tags and
unreadable/missing keys stay blocked. Correct registry-only initialization via a documented
merge patch omitting secrets, then verify Succeeded before using this recovery. Never rotate
on a secret-read failure or fake a missing App in inventory.
For native Portal image updates, use the independent secure application snapshot module rather
than referencing/listing secrets on a resource in the same PUT. Pass saved string names, not App
symbols that introduce reverse dependencies. Keep secrets in secureObject outputs and preserve
the final App/Job/auth/MCP tag dependencies. Initial installs disable the snapshot's live reads.
Use the tested MCP Python SDK pin in both manifests. A configured private-host pytest run must
not inherit real Foundry roles. Empty optional azd credentials are removed through the process Env
provider; explicit nonempty credentials are never discarded to hide an authentication failure.
If a linked central zone is in another subscription or outside that read scope, require its explicit
resource ID rather than silently creating a competing zone.

## Bicep and ARM Pattern

- Convert Portal-discovered `{zoneName, zoneId}` rows to an object with `toObject()`.
- Union discovered IDs with reviewed explicit IDs; explicit same-zone overrides may win.
- Fail closed if CLI discovery and an explicit ID point to different resources for one namespace.
- Create a Private DNS zone only when its namespace is absent from the effective ID map.
- Point each Private Endpoint `privateDnsZoneGroupConfigs` entry to the effective ID.
- Keep the deployment `Incremental`. It preserves existing resources but does **not** delete
  orphaned resources from a failed deployment.
- Include `internal: true` and `publicNetworkAccess: Disabled` in the initial managed-environment
  leaf resource request, not merely in a later update command.
- Use one Storage Account AVM instance for Foundry and the two application containers. Default to
  four automatically managed PEs and a seven-IP preflight budget; explicit ACA PE opt-in restores
  five/eight. Retain the /28 PE subnet minimum and capacity for the operator's manual PE.
  Legacy storage output aliases point to the
  same resource ID. Reject old two-name parameter files and additional KT-profile storage accounts
  instead of selecting an account or deleting/moving data automatically. Active Capability Host
  connections must still pass the no-redirect check. Existing two-account installations require the
  documented, approved data/reference/RBAC migration before using the new template.

## CLI Preflight Pattern

The preflight must remain read-only and rerun immediately before validate, what-if, and deploy.

1. Assert the exact Azure public cloud, tenant, subscription, and deployment resource group.
2. Read the live VNet and map the three operator-supplied subnet names.
3. Validate CIDR, delegation, exclusivity, overlap, occupancy, and bounded free-IP evidence.
4. Inventory required Private DNS zones across the subscription.
5. For each matching namespace, read its `virtualNetworkLinks`:
   - reuse the zone linked to the target VNet;
   - require a non-registration link for Private Endpoint zones;
   - reject multiple linked resources for the same namespace;
   - reject a conflicting explicit zone ID.
6. Verify existing resource ownership before any write.
7. Permit the uncustomized legacy hello-world to move to GHCR. Reject foundation reruns that would
   overwrite a promoted image or operational settings even when the digest is unchanged.
   `--stage complete` requires all existing foundation resources and a ready account host, and
   skips the account module. It can reuse only matching known stage-two web settings. Do not let
   that exception authorize a `both`/`foundation` downgrade or overwrite unrelated customization.
   Retain old GHCR environment projections; compare readiness resource names and principal sets
   semantically rather than treating harmless case/spacing differences as a new owner.
8. Omit `apiKey` by default so ARM evaluates its secure random default, never serialize the
   `newGuid()` expression as a literal key. Validate optional initial keys (32–256 non-whitespace
   characters) and secure-string types case-insensitively. Derive reserved `reuseExistingApiKey`
   from fresh owned app inventory before each stage; an existing real app needs exactly one
   `orchestrator-api-key` secret. ARM reuses that secret through `listSecrets` and must fail on
   unreadable/missing secrets, not generate a replacement. Direct ARM callers must set the reuse
   decision explicitly; serialize deployments to one app. Portal blocks failed/paged inventory
   and leaves legacy hello-world transitions to the CLI. Keep API/MCP authentication unchanged.
9. Require private-registry credentials only in Credentials mode. Never reuse the registry PAT
   as the API key or leave an unused password in Anonymous mode. Redact errors and what-if
   diagnostics with the shared `src/error_logging.py` helpers.
10. A pre-existing project must have one ready `agents` host with matching BYO connection names
   and resource IDs. Missing, automatic, unready or redirected hosts stop preflight before writes.
   Never delete/reset a project host automatically. Recovery requires separate data-retention
   approval and exact project-host identity; do not replace the account or backing stores.
    Preserve the exact project name, including hyphens. Another name means another internal AML
    workspace even when the backing account is the same. Portal/CLI require complete project/host/
    connection inventory and validate category, AAD, ResourceId and endpoint before setting
    reuseExistingFoundrySetup. Replays skip the project, three BYO connection and host PUTs;
    project roles remain, and matching AppInsights connections skip their PUT as well. Do not
    declare a second existing-project alias: ARM checks an unconditional alias during stage one
    and rejects complementary conditional declarations of the same ID. Read reused metadata by
    full ARM ID only inside the verified branch; new telemetry depends on the new project. Do not
    retry workspace-ownership UserError, delete connections or route writes through an internal
    workspace to evade ownership. Direct ARM callers must choose reuse explicitly. The Portal
    safety reads do not restore resource-name dropdowns and block failed/paged/stale responses.
11. Optional Entra inputs must be complete: GUID client ID, non-whitespace secret and a non-empty
    administrator or reader list. Secrets must differ from the API key and registry token and
    remain secure parameters/App secrets, never environment values or outputs. Stage one must
    neither enable pages nor store that secret. Stage two retains API-key-compatible
    `AllowAnonymous`, HTTPS, the exact customer issuer/audiences and same-origin redirects.
    Final readback checks these plus UI activation; it does not prove a real sign-in or Blob access.
    The Portal hides and clears auth outputs outside opted-in stage two, and requires operator
    confirmation of an existing app/valid secret/Web callback/login connectivity. Registration,
    callback updates, Hosted roles and private-path acceptance stay manual.
12. The requested temporary private-anonymous option must clear all Entra outputs and reject
    unused Entra inputs in CLI files. Stage one stays disabled/auth-required; stage two explicitly
    submits `platform.enabled=false` and a disabled Entra provider, because null/Incremental
    omission leaves old auth in place. Check Environment internal/PNA/ownership/subnet before
    anonymous writes; missing or public state stays blocking. Reject automatic Entra downgrades,
    arbitrary customization and foundation resets of active anonymous apps. Allow later explicit
    Entra strengthening of the known anonymous projection. Keep UAMI storage/Foundry access and
    API/MCP keys; this exception is neither a public profile nor an individual authorization model.
    Verify actual private reachability and Blob permissions. Existing non-auth runtime principal
    names remain `local-development`, not a verified human operator.
13. Wire Admin's existing readiness settings to actual Environment/App and UAMI/shared Storage/
    Cosmos/Search descriptors, encoded as Base64 JSON. The metadata itself does not grant reads or provision
    models or Agents. Reuse legacy projections when adding these metadata
    keys; keep descriptor keys canonical and reject malformed, redirected or extended targets.
    Explain scoped ARM Reader versus project Foundry User versus container Blob data access for
    the control-plane UAMI. 403/listing failure is not resource absence, 0/6 mappings is not a live
    Agent count, and Configured is not an invocation/storage result. Preserve real errors and
    explicit post-deployment work; never fabricate success to green the total.
14. Stage-two UAMI grants default on and the Portal requires scope/permission acknowledgement.
    Match the actual principal Object ID, not client ID or project/Hosted identity. CLI reads fresh
    exact principal/role/scope assignments and preserves existing names; failures, invalid names,
    duplicate or conditional assignments block before writes.
    Accept only the documented service-injected ephemeralStorage default (App 1Gi/Job 4Gi);
    the live Job API's empty-string automatic-storage value is also accepted only for Job.
    It is a serialization representation, not a measured capacity.
    CPU/memory, unknown storage sizes and other execution/configuration drift remain blocking.
    Accept legacy ARM True/False casing without changing boolean meaning; emit lowercase env
    flags and retain the Entra-to-anonymous downgrade guard.
    Deterministic Portal names cannot
    adopt other legacy names; use the CLI for those instead of deleting old grants. Optional ACS
    legacy assignment needs the ready same-RG service, matching HTTPS endpoint and an approved
    minimal custom role; automatic email creates only that minimal role. Reject wildcard,
    delete/key permissions and foreign or ambiguous evidence.
    Use the resource symbol directly for conditional communication/insights assignment scope;
    the pinned compiler omitted null-forgiving scope expressions. Assert compiled scope and
    actual resource-level grants, not only Bicep text.
    Insights must be existing and Entra-only. Its project AppInsights connection must not redirect
    a different destination; project/Hosted publisher permissions and actual ingestion remain separate.
15. Model aliases alone are not deployments. Stage two defaults to actual serialized model
    creation: gpt-5.6-terra/luna 2026-07-09, GlobalStandard, 200 units each. Verify catalog and
    quota; a missing simple model alone may explicitly fall back to Terra when combined quota fits.
    Existing ready GPT-5 deployment versions/SKUs/capacities are never changed. No quota is reclaimed
    from other accounts. Global processing can leave Korea Central; disclose the resolved policy.
    Default email creates ACS/Email Services/AzureManagedDomain in Asia Pacific, resolves actual
    sender/endpoint and creates the three-action resource-scoped sender role without keys.
    Regional ACS hostnames include a geography label; validate the allow-listed suffix, not
    one-label assumptions, and use the provider's real hostName.
    Reuse matching ready services/domains/roles, preserving links/tags/identity when adding missing
    links. No fake read absence, arbitrary sender/recipient or automatic send. UI/CLI derive reuse
    from complete scoped inventory; raw ARM callers must provide those decisions.
16. Prompt/Hosted Agent versions are not ARM metadata. Bind six distinct
    names (project prefix capped at 47 characters, Agent names at 64), then use setup_kt_agents.ps1
    only with a successful exact-scope runtimeSetup output,
    matching CLI login and isolated named azd environment from a clean customer clone. WhatIf only
    reads the handoff; Publish retains import/full pytest, roster, guarded Hosted/smoke and exact
    intended-name ACTIVE gates. Set the local user agent before every azd call and restore caller
    environment/default state. Disable OTel export only during local pytest and restore it even on
    failure. Never bypass a failing publication gate. The optional scheduler stays Manual with the
    same image, UAMI, credentials, bounded limits and runtime bindings; reject customization or a
    Schedule reset. Recipient/telemetry inputs, Host identity grants, actual mail delivery,
    network and scheduling acceptance are not invented to make 25/28 checks pass.

Never infer that a same-name zone in the deployment resource group is the linked canonical zone.
Compare full ARM IDs.

## Failure Patterns and Lessons

| Observed failure | Root cause | Preventive rule |
|---|---|---|
| Subnet field showed `/subscriptions/.../subnets/...` and CIDR warnings | The form queried fixed child names and reused an ARM result as an address input. | Select arbitrary existing subnets by role; derive CIDR from the selected VNet and do not expose duplicate address inputs. |
| `appLogsConfiguration` discriminator rejected `destination: none` | The managed-environment AVM union allows only `azure-monitor` and `log-analytics`. | Pass `null` when logging is disabled. Test the compiled module parameter, not only Bicep source text. |
| KT Policy denied Container Apps Environment even with `publicNetworkAccess: Disabled` | Policy `d074ddf8-01a5-4b5e-a2b8-964aed452c0a` checks `vnetConfiguration.internal`, not the PNA field named in its title. | Set both `internal: true` and PNA `Disabled` at creation; assert both compiled leaf mappings. |
| Older API readback showed PNA as `null` while `2026-01-01` showed `Disabled` | The older response projection omits the newer property; `null` was not proof that Azure enabled it. | Verify with the deployment API contract and inspect the actual Policy aliases before changing the template. |
| Stage two rejected a compliant stage-one CAE | ARM returned `internal: true`, but the same Portal ARG query returned numeric `internal: 1`; type-strict `equals(1, true)` failed. | Normalize the ARG projection to a string and compare with `'true'`; preserve PNA/ownership guards and reuse the same Environment. |
| Stage two failed with `CustomDomainInUse` for the live stage-one Foundry account | The account AVM module replayed `customSubDomainName` during the project/BYO deployment, causing the service to run the global-name reservation path again. | Run the account module only when `deployCapabilityHost=false`; stage two uses an `existing` account and deploys only project-scoped resources. Treat a recurrence as a stale ARM/UI pair, not a purge instruction. |
| `Connection agent-storage already exist ... only ... workspace that created it` | A differently spelled project targeted another internal AML workspace; replaying workspace-owned connection PUTs is also unsafe. | Verify exact original project/ready BYO bindings, then skip project/connection/host PUTs on reuse. Never delete the connection or grant broader RBAC as a workaround. |
| Existing-Environment warning remained after entering a new name | Validation used `or(empty(matches), first(matches)...)`; `first()` on the empty result still invalidated the control. | Avoid relying on logical short-circuiting. Filter only same-name noncompliant rows and assert that result is empty. |
| `A virtual network cannot be linked to multiple zones with overlapping namespaces` | A different zone with the same namespace was already linked to the VNet. | Discover and reuse the linked zone ID; create only missing namespaces. |
| Portal validation passed but deployment failed | CreateUiDefinition validates input shape, not every nested AVM discriminator, Policy rule, live link, quota, or permission. | Pair UI checks with compiled-template tests, CLI preflight, ARM validate/what-if, and live acceptance. |
| Portal reported failure to create `existingContainerAppsEnvironment` and cascaded unrelated validation errors | The optional existing-resource control did not initialize, so its dependent discovery/default expressions had no usable outputs. The precise Portal evaluator exception was not available. | Remove the optional controls and automatic defaults; use direct names/manual subnets, preserving essential App/key and typed Environment checks. Do not claim customer acceptance from local expression tests. |
| Portal still failed to create the `containerAppsSubnetName` DropDown | A complex dynamic subnet initializer remained; local eager-branch tests also reproduced first(null) on singular-prefix ARM fixtures. The exact Portal exception was not captured. | Use only simple name-based subnet choices, with prefix/delegation checks separate from initialization. Test unloaded arrays, varied prefixes and VNet changes; retain scope, size, reserved ranges and outputs rather than silencing validation. |
| Container Apps PE failed with generic `InternalServerError` | The error alone does not distinguish a transient provider failure from an unsupported or incomplete configuration. | Preserve exact customer operations, Correlation ID, Environment/PE states and `privateLinkResources`; do not weaken PNA/internal settings or delete the whole foundation as a first response. |
| Bootstrap image reported invalid with `Get https://mcr.microsoft.com/v2/: EOF` | The registry connection ended before a usable response, not proof of an invalid image name or an inbound PE failure. | Check actual Environment DNS/egress/TLS and MCR data endpoints; preserve PNA/internal settings and verify a real image pull. |
| GitHub source changed but deployed runtime did not | Git is source control, not a deployment trigger in the isolated environment. | Transfer reviewed immutable artifacts and deploy manually from an approved private-network host. |
| Project host is past the window for adding BYO connections | Stage one created the project; its automatic host was already finalized when a later stage attempted BYO attachment. | Defer the project and all BYO dependencies to the same second-stage deployment. Existing data-bearing hosts need explicit recovery approval, never silent reset. |
| `/admin` displayed the Container Apps welcome page after stage two | Both infrastructure stages still used the hello-world image; a Capability Host is not application code. | Deploy the real image together with 8000 `/health`, tenant/UAMI/Hosted configuration and API protection. Do not enable Admin/Archive without Entra and an allow-list. |
| `/admin` and `/archive` returned `{"detail":"Not Found"}` after the real image started | Disabled UI flags deliberately hide the routes; infrastructure success is not web-auth setup. | Use the stage-two existing-Entra inputs and exact Web callback, keep auth required, and accept login/denial/Blob access separately. Never solve this by turning authentication off. |
| ACR task built and tested the image but push returned unauthorized | The development registry used ABAC repository permissions and the quick task lacked source-registry authentication. | Use documented `--source-acr-auth-id '[caller]'` with the existing authorized identity. Do not enable the ACR admin or broaden roles. |

## DNS Consolidation and Recovery

Azure does not merge two Private DNS zone resources automatically. Use one canonical zone per
namespace for a VNet.

- For a new Private Endpoint, attach its zone group to the canonical existing zone. Azure adds the
  endpoint record when names do not collide.
- Do not manually copy SOA/NS records.
- Do not blindly copy or combine Private Endpoint-managed A records. Changing or deleting a zone
  group can remove records it owns.
- A failed Incremental deployment can leave an unlinked duplicate zone in the deployment resource
  group. Inspect all records and VNet links first. Delete it only with explicit approval and only
  when it is confirmed unused.
- If two Private Endpoints would own the same record label, do not assume a multi-value A record is
  safe. Review the service-specific DNS lifecycle and choose an explicit design.

### Container Apps Private Endpoint failures

Use the [KT diagnostic procedure](../../../infra/kt/README.md#private-endpoint-생성-실패-진단).
In the current single-account profile, `kt-private-endpoint-4` targets the Container Apps
Environment with group `managedEnvironments` when `deployContainerAppsPrivateEndpoint=true`.
The temporary default false skips that module and its DNS zone group without renumbering other
PE deployments. Keep DNS zones prepared for manual integration. Do not read or adopt an existing
manual/failed ACA PE when disabled, but continue checking the Environment's ownership, subnet,
internal flag and PNA. The output `containerAppsPrivateEndpointRequested` discloses the selection;
it is not proof that a manual PE exists or is ready. Incremental deployment does not delete it.
The operator must separately verify manual PE approval, DNS and HTTPS before application acceptance.
This switch isolates diagnosis; it does not claim to fix the PE service failure.

When enabled, the explicit dependency waits for the Environment deployment; the DNS zone group
depends on the PE. Do not assert that a DNS link caused a PE resource
500 without the corresponding operation evidence, or claim that a fixed delay will repair it.
Resource-provider asynchronous readiness may need investigation even after ARM reports Succeeded.

An offline regression reproduced the old final verifier accepting an Approved connection whose PE
was Failed. The verifier now checks provisioning state separately; this fixes a readiness gap,
not an Azure provisioning failure. No live recovery is established by mocked tests.

Read only the user's actual target subscription; an inaccessible customer scope must remain an
explicit evidence gap, not a reason to substitute a developer subscription. Before any deletion,
preserve the failure and check the Environment, PE/connection, subnet capacity and service health.
If only the PE failed, prefer an approved focused recovery over deleting Foundry, the Capability
Host or shared storage. Repeated minimal PE failures need provider-side investigation with request
IDs. Never add a broad 500 retry or silent success fallback.

## Isolated Release and Update

GitHub Actions is not required, but a controlled deployment path is.
Maintain source/phase-specific destinations and evidence in `infra/NETWORK_REQUIREMENTS.md`.
Use section 3.1 for the initial platform/core-analysis request, not all of section 3. Treat allowed
document hosts as a maximum fetch boundary, not mandatory egress. Optional-source/telemetry additions
need feature approval; excluding default-on community fetches requires an explicit Hosted runtime
setting and accepts narrower evidence coverage. Never claim documentation changed the runtime.
Separate Internet egress, private endpoints, DNS/platform-local traffic, builders and browsers.
Preserve the Workload Profiles versus Consumption-only distinction and explicit remote-build,
Cosmos-mode, A365 and TLS-inspection acceptance gaps; do not turn observed redirects into a
permanent fixed-IP guarantee or treat VNet injection alone as proof of NVA traversal.

The current image is `ghcr.io/networkdog/azbriefenterprise`, pinned by digest under the compatibility
parameter `bootstrapImage`. It was built from tracked public source only and verified as linux/amd64,
non-root, with live container HTTP checks before publication. The registry transfer must preserve
the tested manifest digest. A private GHCR package requires a separate read-only PAT; the publishing
PAT stays in the developer's credential store and temporary authentication files must be removed.
Keep `applicationReady=false`: model/Agent publication, Entra, scheduler and email remain separate.
Initial image HTTP/health checks do not prove Hosted or customer network readiness.

Approve HTTPS/DNS to `ghcr.io` and GitHub Packages download endpoints before a KT pull. GHCR is not
an offline registry. The previous hello-world image required `mcr.microsoft.com` and
`*.data.mcr.microsoft.com`; ACA platform dependencies remain even after changing the app image.
Diagnose egress on the actual Environment route, not a developer PC or unrelated Cloud Shell.
Inbound PNA/PE settings do not grant outbound access. Do not disable TLS checks or add credentials
to fix a connection EOF. Runtime secrets are inputs, never deployment outputs or image layers.

1. Freeze and review a source revision outside or inside the approved build boundary.
2. Produce:
   - source/package SHA-256;
   - Hosted Agent package/version;
   - Prompt Agent definition versions when applicable;
   - OCI image digest for Container App/Job;
   - compiled ARM/UI pair for infrastructure changes.
3. Transfer artifacts through the approved channel.
4. Deploy from a host with private line of sight to ARM, Foundry, the registry, and required Entra
   endpoints.
5. For a contract change, publish a backward-compatible Hosted version before the Container Apps
   control plane.
6. Apply the same immutable image digest to App and Job, verify health and an execution-only smoke,
   then restore the schedule.

A GitHub commit or raw-template update never changes existing Azure resources by itself.

## Validation Checklist

```powershell
& .\.venv\Scripts\Activate.ps1
az bicep build --file infra\kt\main.bicep --outfile infra\kt\azuredeploy.json
python -m pytest tests\test_kt_deployment.py -o "addopts=" -q
python -c "import src"
```

Before customer deployment, also complete:

- Portal ARM/UI source-version pairing
- JSON parsing and CreateUIDefinition reference checks
- `validate` and `what-if` using the exact KT tenant/subscription and current live inventory
- Policy denial review, including the evaluated alias and submitted property value
- Private DNS linked-zone and record review
- Private endpoint approval and DNS resolution from an approved private path
- Foundation health, then separate application/Agent acceptance

Local compilation and mocked tests do not prove KT Policy, RBAC propagation, live DNS, quota,
private routing, or Hosted Agent availability.
