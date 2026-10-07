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
- Contract tests: [`tests/test_kt_deployment.py`](../../../tests/test_kt_deployment.py)

Never hand-edit the compiled ARM JSON. Change Bicep, compile with the pinned compiler, and keep
the ARM/UI pair from the same source revision.
Keep the paired KT Deploy button in both top-level READMEs while this profile ships. The repository
contract expects two standard Enterprise Portal links and one KT Portal link in each README.

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
| Initial application | Use the pinned GHCR AzBrief image on 8000 `/health`, not hello-world. Keep API protection and disabled Admin/Archive until Entra setup. Image startup is not operational readiness. |
| Project timing | Stage one must not create the project, its connections or project roles. After the account host is ready, stage two creates the project, role/connection bindings and BYO host together. Stage-one project IDs are planned; its principal is empty. |
| Application image | Use an approved immutable digest and matching registry authentication. Do not deploy `latest`. |
| GHCR credentials | Default Anonymous after verifying Public visibility and anonymous layer downloads. Explicit Credentials accepts only a read-only PAT via App secret. Never grant GHCR an Azure identity role or deploy the publisher token. |
| API key lifecycle | No Portal key input. ARM generates the initial secure random key; complete Portal/CLI ARM inventory selects existing-secret reuse. Read failures or missing keys never trigger rotation. Keep API authentication. |
| Identities | Keep Container Apps control-plane, Foundry project, Hosted Agent, and Azure MCP identities distinct. |
| Runtime roles | Operators choose and grant Container App/Job and Hosted Agent runtime permissions after deployment. Preserve principal outputs; no automatic control-plane/evidence grants or deletion of existing grants. |
| Storage | One `storageAccountName` and one Blob PE. Keep app state/archive separate and document container-scoped operator grants. Retain project-ID BYO provisioning/data roles; these share the trust boundary, not hard account isolation. |

Built-in policy `d074ddf8-01a5-4b5e-a2b8-964aed452c0a` is named “Container Apps environment should
disable public network access,” but its actual rule denies a missing or false
`Microsoft.App/managedEnvironments/vnetConfiguration.internal` alias. Do not treat the display name
as proof that setting only `publicNetworkAccess` is sufficient. KT requires an internal load
balancer environment **and** disabled public network access. `ingressExternal: true` on an app means
environment-level ingress; in this internal environment it remains reachable only through approved
private/VNet paths.

## Portal Form Design

1. Select an existing VNet filtered to the deployment subscription and region.
2. Read the VNet through `Microsoft.Solutions.ArmApiControl`.
3. Present role-based subnet dropdowns from the live subnet inventory:
   - derive CIDR and delegation from ARM;
   - show CIDR as the option description;
   - do not ask the operator to re-enter CIDR;
   - filter out invalid sizes, non-RFC1918 ranges, multiple prefixes, wrong delegations, and
     Container Apps reserved ranges.
4. Output the selected subnet names. The Bicep template constructs the exact child resource IDs.
5. Use Azure Resource Graph to find required Private DNS zones already linked to the VNet in the
   selected subscription.
6. Default to automatic DNS reuse. Keep an explicit central-DNS override for cross-subscription or
   unreadable zones.
7. Treat CreateUiDefinition schema checks as structural only. Preview the published ARM/UI pair in
   the Portal Sandbox and perform a customer-like validation; the legacy schema does not model every
   `Microsoft.Solutions` control.
8. Query the deployment resource group for an existing Environment with the requested name. Reuse
   it only when `internal=true`, PNA is `Disabled`, and the KT ownership tag matches; otherwise block
   the form before ARM validation and inspect the actual ARM values before planning recovery. Do not combine
   `or(empty(matches), first(matches)...)`: CreateUiDefinition may evaluate `first()` for an empty
   result. Filter same-name **noncompliant** rows and validate that the filtered array is empty.
   A compliant stage-one Environment must pass with the same name in stage two. ARG `objectArray`
   can serialize `tobool(...)` as numeric `1`/`0`; CreateUiDefinition `equals(1, true)` is false.
   Project `internal=tostring(tobool(properties.vnetConfiguration.internal))` and compare to the
   string `'true'`. Missing/unknown values are not proof of an internal environment. Keep PNA and
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

Portal discovery depends on the deploying principal having read access to the VNet links and zones.
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
| Existing-Environment warning remained after entering a new name | Validation used `or(empty(matches), first(matches)...)`; `first()` on the empty result still invalidated the control. | Avoid relying on logical short-circuiting. Filter only same-name noncompliant rows and assert that result is empty. |
| `A virtual network cannot be linked to multiple zones with overlapping namespaces` | A different zone with the same namespace was already linked to the VNet. | Discover and reuse the linked zone ID; create only missing namespaces. |
| Portal validation passed but deployment failed | CreateUiDefinition validates input shape, not every nested AVM discriminator, Policy rule, live link, quota, or permission. | Pair UI checks with compiled-template tests, CLI preflight, ARM validate/what-if, and live acceptance. |
| Container Apps PE failed with generic `InternalServerError` | The error alone does not distinguish a transient provider failure from an unsupported or incomplete configuration. | Preserve exact customer operations, Correlation ID, Environment/PE states and `privateLinkResources`; do not weaken PNA/internal settings or delete the whole foundation as a first response. |
| Bootstrap image reported invalid with `Get https://mcr.microsoft.com/v2/: EOF` | The registry connection ended before a usable response, not proof of an invalid image name or an inbound PE failure. | Check actual Environment DNS/egress/TLS and MCR data endpoints; preserve PNA/internal settings and verify a real image pull. |
| GitHub source changed but deployed runtime did not | Git is source control, not a deployment trigger in the isolated environment. | Transfer reviewed immutable artifacts and deploy manually from an approved private-network host. |
| Project host is past the window for adding BYO connections | Stage one created the project; its automatic host was already finalized when a later stage attempted BYO attachment. | Defer the project and all BYO dependencies to the same second-stage deployment. Existing data-bearing hosts need explicit recovery approval, never silent reset. |
| `/admin` displayed the Container Apps welcome page after stage two | Both infrastructure stages still used the hello-world image; a Capability Host is not application code. | Deploy the real image together with 8000 `/health`, tenant/UAMI/Hosted configuration and API protection. Do not enable Admin/Archive without Entra and an allow-list. |
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
