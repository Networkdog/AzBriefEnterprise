"""Tool descriptions and KQL query strategy.

Included in: Planning, Execution phases.
NOT included in: Report phase (no tools available).
"""

TOOLS_PROMPT = """## Available Tools

### Microsoft Learn Documentation Search
- `search_update_related_docs`: Comprehensive update-related doc search (recommended)
- `search_azure_docs`: Azure documentation keyword search. For GA/Preview regional verification,
  set `include_content=true` and pass the primary Region names in `focus_terms`; this fetches the
  official pages and returns source excerpts around those Regions or an all-Regions statement.
- `get_service_documentation`: Service-specific documentation lookup

### Azure Resource Graph
- `get_service_resource_details`: Optional service baseline; use only when its fields answer the question
- `get_resource_configurations`: **Configuration profiling** — shows actual config values (K8s version, TLS version, SKU, feature flags) with distribution summary (e.g., "3/5 on 1.28, 2/5 on 1.30"). Use when you need to assess which resources are affected by a version/config change
- `get_resource_dependencies`: **Dependency mapping** — traces VNet integrations, Private Endpoints, cross-service references. Use for blast radius analysis of core infrastructure updates (Storage, VNet, Key Vault, SQL)
- `query_azure_resources`: Custom KQL with purpose and expected_columns for bounded result review/rewrite
- `find_related_resources`: Keyword-based resource search. Use exactly
  `{"keyword": ["storage", "blob"]}`; never pass a `query` key.
- `get_security_posture`: Security posture analysis
- `explore_resource_schema`: Inspect nested objects/arrays across five live samples; optional table and multiword focus_area

### Service Resource-Type Region Availability (authoritative within its scope)
- `get_service_region_availability`: Confirms whether an exact ARM resource type can be deployed in
  the admin's regions using the ARM providers API (`/providers/{namespace}`). It is definitive for
  that resource type, NOT for a new feature layered on an existing type. For every GA/Preview feature,
  pair it with feature-level evidence from the Azure Update detail or `search_azure_docs` page content.
  Input: `provider_namespace` (e.g., "Microsoft.Databricks"), optional `resource_type` (e.g., "workspaces"),
  optional `regions` (comma-separated; omit to auto-detect the admin's primary regions from their resources).
  Always pass `resource_type` when known; a provider-wide ratio is not a feature verdict.
  Returns a per-region resource-type matrix and a concise verdict.
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

### Cost Management and Azure Billing
- `get_cost_by_resource_type`: ActualCost totals grouped by ARM resource type for a bounded period.
- `get_cost_by_service`: ActualCost totals grouped by Azure service for a bounded period.
- `list_billing_accounts`: Billing accounts visible to the current identity via Microsoft.Billing `2024-04-01`.
- `list_billing_profiles`: Billing profiles under an exact account name returned above. Supported for
  Microsoft Customer Agreement and Microsoft Partner Agreement accounts.

Cost Management values and Billing hierarchy answer different questions; do not substitute one for the
other. Always state scope, time window, and currency. A 403, unsupported agreement type, or empty visible
account list is an evidence gap, not proof of zero cost or no billing account.

### Truncated Tool Results — search them, never assume absence
A large tool result is shown to you as a PREVIEW ending with
`... [TRUNCATED PREVIEW — showing N of M chars] [ref=R7] ...`.
The unshown rows are NOT lost: the full result is retained and searchable.

- `query_tool_result`: Search the full text of a truncated result.
  Input: `ref` (from the preview), `pattern` (literal text, case-insensitive), optional `mode`
  (`search` | `head` | `tail` | `stats`) and `regex`.

**Rule**: search stored previews before inferring absence. A no-match establishes absence only within
the fully stored, fully collected query result and its actual predicate. Partial storage, SDK
result_truncated=true, a limited sample or an unverified property path cannot prove tenant absence.
Rows never received from Azure require a narrower/partitioned KQL query, not a local ref search.

### Resource Graph Completeness — query the answer instead of deferring it
**Before you leave any fact for "manual review" / `additional_checks`, ask: "Is this an ARM resource or a resource property?"**
If yes, first try the appropriate Resource Graph table and observed property path. ARM membership does
not guarantee that Resource Graph exposes every property: unsupported/masked fields, missing access
and stale data remain explicit gaps. ARM-only facts belong to the Azure API specialist, not guessed
Resource Graph results. The facts below are examples, not the allowed set of investigations:

| Update topic | Do NOT defer — query this | KQL property path |
|--------------|---------------------------|-------------------|
| AKS advanced networking / ACNS / Cilium / container network logs/metrics | Whether ACNS/advanced networking is on, plus dataplane/policy | `microsoft.containerservice/managedclusters` -> `properties.networkProfile.networkDataplane`, `.networkPolicy`, `.advancedNetworking`, `properties.addonProfiles` |
| AKS Azure Files / Azure Disk CSI | The actual storage CSI driver state | `properties.storageProfile.fileCSIDriver.enabled`, `.diskCSIDriver.enabled`. `properties.addonProfiles.azureKeyvaultSecretsProvider.enabled` is the **Key Vault secrets provider**, not evidence for either storage CSI driver; never substitute it. |
| Point-to-Site VPN / Azure VPN Client retirement | Whether a P2S gateway / VPN server config actually exists | `microsoft.network/p2svpngateways`, `microsoft.network/vpnserverconfigurations`, `microsoft.network/virtualnetworkgateways` -> `properties.vpnClientConfiguration` |
| Azure Site Recovery / DR | Whether a Recovery Services vault exists (and its items) | `microsoft.recoveryservices/vaults`; replication items via the `recoveryservicesresources` table |
| Cosmos DB backup / Fabric mirroring prerequisites | The backup mode already answers "Continuous Backup?" | `microsoft.documentdb/databaseaccounts` -> `properties.backupPolicy.type` (`Periodic` vs `Continuous`), `properties.enableAnalyticalStorage` |
| Storage auth model (SAS, Shared Key) | Shared-Key / public-access / TLS posture | `microsoft.storage/storageaccounts` -> `properties.allowSharedKeyAccess`, `.allowBlobPublicAccess`, `.minimumTlsVersion`, `.publicNetworkAccess` |
| "Does the admin even use service X?" | Presence/count of that resource type | `resources | where type =~ '<provider>/<type>' | summarize count()` |

Example (AKS networking — single-table, follows the KQL constraints below):
```
resources
| where type =~ 'microsoft.containerservice/managedclusters'
| project id, name, subscriptionId, resourceGroup,
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
Choose the query shape from the evidence question, never from a fixed service template.

1. State the original purpose, eligibility conditions and required result columns. If the path is
  known, query directly. Otherwise inspect a small type/parent-bag sample or explore_resource_schema;
  cached schemas are hypotheses. Inspect other versions/SKUs/regions when shape varies.
2. Use nested projections, conditional distributions, null checks, array expansion or ID-based
  joins when they answer the question. Configuration presence alone is not applicability; retain
  an affected/unaffected/unknown denominator. False and zero are values, not missing evidence.
3. Pass purpose and expected_columns to query_azure_resources. Inspect executed_query and evidence_gaps:
  syntax failures, empty/off-topic results and missing required values warrant evidence-based
  rewrites, not removing thresholds or returning a generic builder. A correct zero is acceptable.
4. Follow a schema probe with a query based on its actual output in the next tool round/revision.
  Independent queries can run together; do not guess dependent queries in the same batch.
5. Stop once the question is answered or bounded attempts expose an unresolved gap. Repeating an
  equivalent failure is not exploration. Successful equivalent queries should be reused.

### Common Pitfalls
- Resource Graph uses **KQL subset**, not full Kusto. No `let`, `render`, `datatable`, `externaldata`.
- `type` values are **lowercase** in data. Use `=~`/`in~` or exact lowercase comparisons.
- Cast dynamic fields by meaning: tostring for labels, tobool for flags, numbers/datetime for ordering.
- Project expressions are valid. Project kind directly or use resourceKind=tostring(kind);
  the reserved kind=tostring(kind) assignment fails in the live service. No five-extend restriction.
- Use `project-away` (not `project-except`) to remove join duplicate columns.
- Joins support innerunique, inner, leftouter and fullouter; normalize ARM ID keys on both sides.
  Limit join/union to three combined, observe cross-table/right-table restrictions, and filter early.
  App-injected Resource Group or intersected MG/subscription boundaries prohibit join/union there:
  use separate scoped ID-based queries without broadening the boundary.
- mv-expand supports arrays and documented property-bag expansion. At most three expansions;
  choose an explicit per-array limit up to 2000 (default 128). Preserve empty-array cases and avoid
  multiplying the resource denominator. More complex operators are useful only when justified.
- Complete enumerations retain scalar id/subscriptionId and stable ordering, without take/limit.
  The service follows 1000-row pages up to ten pages and reports remaining truncation explicitly.
  Small raw-property samples are allowed for discovery; broad raw properties/tags/sku dumps are not.
- For Function Apps vs Web Apps: both are `microsoft.web/sites`; distinguish by `kind contains 'functionapp'`.
- `sku` is a top-level field (not under `properties`): access as `sku.name`, `sku.tier`.
- `tags` is top-level; access as `tags['keyName']` or `tags.keyName`.
"""
