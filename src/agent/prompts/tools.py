"""Tool descriptions and KQL query strategy.

Included in: Planning, Execution phases.
NOT included in: Report phase (no tools available).
"""

TOOLS_PROMPT = """## Available Tools

### Microsoft Learn Documentation Search
- `search_update_related_docs`: Comprehensive update-related doc search (recommended)
- `search_azure_docs`: Azure documentation keyword search
- `get_service_documentation`: Service-specific documentation lookup

### Azure Resource Graph
- `get_service_resource_details`: Optimized detail query per service (recommended)
- `get_resource_configurations`: **Configuration profiling** — shows actual config values (K8s version, TLS version, SKU, feature flags) with distribution summary (e.g., "3/5 on 1.28, 2/5 on 1.30"). Use when you need to assess which resources are affected by a version/config change
- `get_resource_dependencies`: **Dependency mapping** — traces VNet integrations, Private Endpoints, cross-service references. Use for blast radius analysis of core infrastructure updates (Storage, VNet, Key Vault, SQL)
- `query_azure_resources`: Execute custom KQL queries
- `find_related_resources`: Keyword-based resource search
- `get_security_posture`: Security posture analysis
- `explore_resource_schema`: Discover properties schema for a resource type (use when predefined queries lack needed fields)

### Service Region Availability (authoritative)
- `get_service_region_availability`: **DEFINITIVE region check** — confirms whether an Azure service/feature
  is available in the admin's regions using the ARM providers API (`/providers/{namespace}`). Prefer this
  OVER documentation search for GA, preview, new-service, or region-expansion updates. Never conclude
  "availability could not be verified" without calling this first.
  Input: `provider_namespace` (e.g., "Microsoft.Databricks"), optional `resource_type` (e.g., "workspaces"),
  optional `regions` (comma-separated; omit to auto-detect the admin's primary regions from their resources).
  Returns a per-region ✅/❌ matrix and a concise verdict.
  Example: Databricks in Korea Central → provider_namespace="Microsoft.Databricks", regions="koreacentral".

### Azure Management REST API (general-purpose)
- `call_azure_rest_api`: Call any Azure Management REST API to check resource availability, SKUs, capabilities, or region support.
  Use when the update announces new resource types, SKU changes, region expansions, or capability changes
  that cannot be verified through Resource Graph alone.
  Input: `path` (API path with {subscriptionId} placeholder), `api_version`, `filter_expression`, `max_results`.
  Common patterns:
  - VM SKU availability: path="/subscriptions/{subscriptionId}/providers/Microsoft.Compute/skus", filter_expression="location eq 'koreacentral'"
  - Available regions: path="/subscriptions/{subscriptionId}/locations"
  - Storage SKUs: path="/subscriptions/{subscriptionId}/providers/Microsoft.Storage/skus", api_version="2023-05-01"

### Truncated Tool Results — search them, never assume absence
A large tool result is shown to you as a PREVIEW ending with
`... [TRUNCATED PREVIEW — showing N of M chars] [ref=R7] ...`.
The unshown rows are NOT lost: the full result is retained and searchable.

- `query_tool_result`: Search the full text of a truncated result.
  Input: `ref` (from the preview), `pattern` (literal text, case-insensitive), optional `mode`
  (`search` | `head` | `tail` | `stats`) and `regex`.

**Rule**: if a result was truncated, you MUST call `query_tool_result` before claiming a resource
does not exist, a property is unverified, or a check needs manual review. "It was not in the preview"
is not evidence — the preview stops at an arbitrary row. A `no match` answer from `query_tool_result`
IS evidence, because it searched the entire result.

### Resource Graph Completeness — query the answer instead of deferring it
**Before you leave any fact for "manual review" / `additional_checks`, ask: "Is this an ARM resource or a resource property?"**
If yes, it is queryable through Resource Graph NOW — plan a query for it instead of punting it to the reader.
Deferring a queryable fact to CSA review is a quality failure. The facts below are frequently (and wrongly)
left unqueried — always query them when the update touches the relevant service:

| Update topic | Do NOT defer — query this | KQL property path |
|--------------|---------------------------|-------------------|
| AKS advanced networking / ACNS / Cilium / container network logs/metrics | Whether ACNS/advanced networking is on, plus dataplane/policy | `microsoft.containerservice/managedclusters` -> `properties.networkProfile.networkDataplane`, `.networkPolicy`, `.advancedNetworking`, `properties.addonProfiles` |
| Point-to-Site VPN / Azure VPN Client retirement | Whether a P2S gateway / VPN server config actually exists | `microsoft.network/p2svpngateways`, `microsoft.network/vpnserverconfigurations`, `microsoft.network/virtualnetworkgateways` -> `properties.vpnClientConfiguration` |
| Azure Site Recovery / DR | Whether a Recovery Services vault exists (and its items) | `microsoft.recoveryservices/vaults`; replication items via the `recoveryservicesresources` table |
| Cosmos DB backup / Fabric mirroring prerequisites | The backup mode already answers "Continuous Backup?" | `microsoft.documentdb/databaseaccounts` -> `properties.backupPolicy.type` (`Periodic` vs `Continuous`), `properties.enableAnalyticalStorage` |
| Storage auth model (SAS, Shared Key) | Shared-Key / public-access / TLS posture | `microsoft.storage/storageaccounts` -> `properties.allowSharedKeyAccess`, `.allowBlobPublicAccess`, `.minimumTlsVersion`, `.publicNetworkAccess` |
| "Does the admin even use service X?" | Presence/count of that resource type | `resources | where type =~ '<provider>/<type>' | summarize count()` |

Example (AKS networking — single-table, follows the KQL constraints below):
```
resources
| where type =~ 'microsoft.containerservice/managedclusters'
| project name, resourceGroup,
    dataplane = tostring(properties.networkProfile.networkDataplane),
    policy = tostring(properties.networkProfile.networkPolicy),
    acns = tostring(properties.networkProfile.advancedNetworking)
```
**Only defer to `additional_checks` what is genuinely NOT in Resource Graph**: in-cluster Kubernetes manifests
(sidecars, Helm values, `ContainerNetworkLog` custom resources), application/SDK code, data-plane usage patterns,
per-blob tier distribution, and organization-external assets. When a queried property already answers a
prerequisite (e.g. `backupPolicy.type == 'Periodic'` means Continuous Backup is NOT met), state that
conclusion in the report — do not re-raise the same question as an unresolved check.

### KQL Query Strategy (Advanced)
When predefined queries cannot provide needed information, use this strategy:

1. **Schema exploration first**: If the update mentions a specific setting/property not covered by predefined queries, use `explore_resource_schema` to discover actual property keys.
   - Example: "Azure SQL TLS version" → `explore_resource_schema(resource_type='Microsoft.Sql/servers', focus_area='tls')`
2. **Progressive approach**: Schema exploration → field discovery → detailed query with discovered fields → execution
3. **Knowledge accumulation**: Successful queries and schemas are automatically saved to an internal knowledge base for future analyses.

### Common Pitfalls
- Resource Graph uses **KQL subset**, not full Kusto. No `let`, `render`, `datatable`, `externaldata`.
- `type` values are **lowercase** in data. Always use `=~` (case-insensitive) or `==` with lowercase.
- Wrap `properties.*` in `tostring()` before using in `summarize` or `==` comparisons.
- Use `project-away` (not `project-except`) to remove join duplicate columns.
- `servicehealthresources`: Avoid `extend` + `project` combination; filter directly with `where tostring(properties.X)`.
- `join` default is `innerunique`; explicitly specify `kind=leftouter` or `kind=inner`.
- Max 1000 rows per page; use `top` or `take` to limit.
- For Function Apps vs Web Apps: both are `microsoft.web/sites`; distinguish by `kind contains 'functionapp'`.
- `sku` is a top-level field (not under `properties`): access as `sku.name`, `sku.tier`.
- `tags` is top-level; access as `tags['keyName']` or `tags.keyName`.
"""
