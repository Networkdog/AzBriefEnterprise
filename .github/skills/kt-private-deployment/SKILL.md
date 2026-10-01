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
- Bicep source: [`infra/kt/main.bicep`](../../../infra/kt/main.bicep)
- Compiled Portal artifact: [`infra/kt/azuredeploy.json`](../../../infra/kt/azuredeploy.json)
- Portal UI: [`infra/kt/createUiDefinition.json`](../../../infra/kt/createUiDefinition.json)
- Read-only preflight/deploy CLI: [`scripts/deploy_kt.py`](../../../scripts/deploy_kt.py)
- Contract tests: [`tests/test_kt_deployment.py`](../../../tests/test_kt_deployment.py)

Never hand-edit the compiled ARM JSON. Change Bicep, compile with the pinned compiler, and keep
the ARM/UI pair from the same source revision.

## Non-Negotiable KT Contract

| Area | Required behavior |
|---|---|
| Existing network | Reuse the approved VNet. Never replace it or PUT its full subnet collection. |
| Subnet identity | Names are not fixed. Select explicit role mappings and preserve the actual names. |
| Private Endpoint subnet | RFC1918 IPv4, `/28` or larger, no service delegation, enough verified free IPs. |
| Foundry subnet | Separate `/27` or larger subnet delegated to `Microsoft.App/environments`; one Foundry account only. |
| Container Apps subnet | Separate `/27` or larger subnet delegated to `Microsoft.App/environments`; never share it with Foundry. |
| Container Apps Environment | `vnetConfiguration.internal: true` and `publicNetworkAccess: Disabled` must both be in the initial `Microsoft.App/managedEnvironments` PUT. |
| Private DNS | Reuse a required zone already linked to the VNet. Create only missing namespaces. Never link a second overlapping zone. |
| Private Endpoint records | Point the new Private Endpoint DNS zone group at the canonical existing zone. Let Azure manage its A record; do not copy managed records between zones. |
| Optional ACA logs | Pass `null` when no Log Analytics workspace is selected. AVM `0.16.0` accepts only `azure-monitor` or `log-analytics`, not `none`. |
| Resource exposure | Disable public network access on Foundry, storage, Cosmos DB, Search, and the Container Apps Environment at creation. |
| Bootstrap | The hello-world Container App proves only foundation/network readiness. It is not application readiness. |
| Application image | Use an approved immutable digest and matching registry authentication. Do not deploy `latest`. |
| Identities | Keep Container Apps control-plane, Foundry project, Hosted Agent, and Azure MCP identities distinct. |
| Storage | One `storageAccountName` and one Blob PE. Keep app state/archive in separate containers with container-scoped App/Job grants. Foundry's required account roles share the trust boundary; do not claim hard isolation. |

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
   the form before ARM validation and require a reviewed recreation. Do not combine
   `or(empty(matches), first(matches)...)`: CreateUiDefinition may evaluate `first()` for an empty
   result. Filter same-name **noncompliant** rows and validate that the filtered array is empty.

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
- Use one Storage Account AVM instance for Foundry and the two application containers. Keep five
  private endpoints with an eight-IP preflight budget. Legacy storage output aliases point to the
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
7. Prevent a foundation rerun from replacing a promoted application image with bootstrap.

Never infer that a same-name zone in the deployment resource group is the linked canonical zone.
Compare full ARM IDs.

## Failure Patterns and Lessons

| Observed failure | Root cause | Preventive rule |
|---|---|---|
| Subnet field showed `/subscriptions/.../subnets/...` and CIDR warnings | The form queried fixed child names and reused an ARM result as an address input. | Select arbitrary existing subnets by role; derive CIDR from the selected VNet and do not expose duplicate address inputs. |
| `appLogsConfiguration` discriminator rejected `destination: none` | The managed-environment AVM union allows only `azure-monitor` and `log-analytics`. | Pass `null` when logging is disabled. Test the compiled module parameter, not only Bicep source text. |
| KT Policy denied Container Apps Environment even with `publicNetworkAccess: Disabled` | Policy `d074ddf8-01a5-4b5e-a2b8-964aed452c0a` checks `vnetConfiguration.internal`, not the PNA field named in its title. | Set both `internal: true` and PNA `Disabled` at creation; assert both compiled leaf mappings. |
| Older API readback showed PNA as `null` while `2026-01-01` showed `Disabled` | The older response projection omits the newer property; `null` was not proof that Azure enabled it. | Verify with the deployment API contract and inspect the actual Policy aliases before changing the template. |
| Existing-Environment warning remained after entering a new name | Validation used `or(empty(matches), first(matches)...)`; `first()` on the empty result still invalidated the control. | Avoid relying on logical short-circuiting. Filter only same-name noncompliant rows and assert that result is empty. |
| `A virtual network cannot be linked to multiple zones with overlapping namespaces` | A different zone with the same namespace was already linked to the VNet. | Discover and reuse the linked zone ID; create only missing namespaces. |
| Portal validation passed but deployment failed | CreateUiDefinition validates input shape, not every nested AVM discriminator, Policy rule, live link, quota, or permission. | Pair UI checks with compiled-template tests, CLI preflight, ARM validate/what-if, and live acceptance. |
| GitHub source changed but deployed runtime did not | Git is source control, not a deployment trigger in the isolated environment. | Transfer reviewed immutable artifacts and deploy manually from an approved private-network host. |

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

## Isolated Release and Update

GitHub Actions is not required, but a controlled deployment path is.

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
