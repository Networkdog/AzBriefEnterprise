---
name: kql-resource-graph
description: 'Write and debug KQL queries for Azure Resource Graph. Use when: KQL query, Resource Graph query, query_resources, ResourceGraphQueryBuilder, get_query_for_update_service, resource type filtering, tenant-scoped query, KQL constraints, query auto-fix.'
---

# KQL Resource Graph Queries

## Foundry Runtime Guidance

- As the Resource Graph specialist, own KQL authoring, schema probing, result
  interpretation, and KQL repair. Do not hand this work to another specialist.
- Use Resource Graph's restricted dialect: no `join`, `let`, `render`, `datatable`, or
  `toscalar()`; use `mv-expand` only on arrays.
- Compare types with `=~`, retain `subscriptionId`, project named fields, and order stably.
  Never return broad raw `properties`, `tags`, or `sku` bags.
- Keep similarly named AKS properties semantically distinct: Azure Files/Disk CSI state comes
  from `storageProfile.fileCSIDriver` / `diskCSIDriver`; the Key Vault secrets provider under
  `addonProfiles.azureKeyvaultSecretsProvider` is not a storage CSI signal.
- Query tenant-wide accessible subscriptions and cite exact IDs. Query ARM resources and
  properties now; defer only data-plane, application, or in-cluster state.
- An empty filtered result does not prove absence. Probe the type, correct filters against
  observed values, and preserve uncertainty when completeness is unresolved. On failure,
  prefer deterministic builder/rule recovery or emit a gap; never cross-fallback roles.

<!-- End Foundry Runtime Guidance -->

## When to Use

- Writing or modifying KQL queries in `src/services/resource_graph.py`
- Adding new service-specific queries in `ResourceGraphQueryBuilder`
- Working on `ResourceGraphQueryFixer` in `src/agent/tools.py`
- Debugging KQL query failures or auto-fix logic
- Adding entries to `src/agent/kql_knowledge_base.json`

## Azure Resource Graph KQL Constraints

Resource Graph KQL is a **subset** of full Kusto Query Language. These operations are **NOT supported**:

| Forbidden | Use Instead |
|-----------|-------------|
| `let` statements | Inline the value directly |
| `render` | Not supported — post-process in Python |
| `datatable` | Use `where` with literal values |
| `mv-expand` on nested bags | `mv-expand` on arrays only |
| `toscalar()` | Not supported |

### Mandatory Patterns

- **Type comparisons**: Always use `=~` (case-insensitive): `where type =~ "microsoft.compute/virtualmachines"`
- **Tenant-scoped**: Queries run across **all accessible subscriptions**, not a single one
- **`subscriptionId` column**: Always available — use it for subscription-level grouping
- **Property access**: Use `properties.X` dot notation, e.g., `properties.storageProfile.osDisk.osType`

### Query Structure Template

```kql
Resources
| where type =~ "microsoft.compute/virtualmachines"
| extend status = tostring(properties.extended.instanceView.powerState.displayStatus)
| project name, resourceGroup, subscriptionId, location, status
| order by name asc
```

### Completeness — query the answer instead of deferring it

A 3-month report audit (2026-07) found the agent repeatedly punting **queryable** ARM facts to
`additional_checks`/CSA review. Rule of thumb: *if a fact is an ARM resource or a resource property,
it is queryable NOW — plan a query, do not defer it.* Commonly under-queried facts and their paths:

| Update topic | Query this instead of deferring | Property path |
|--------------|--------------------------------|---------------|
| AKS advanced networking / ACNS / Cilium / container network logs/metrics | ACNS/advanced-networking on? dataplane/policy? | `microsoft.containerservice/managedclusters` → `properties.networkProfile.networkDataplane`, `.networkPolicy`, `.advancedNetworking`, `properties.addonProfiles` |
| AKS Azure Files / Azure Disk CSI | Is the corresponding storage CSI driver enabled? | `properties.storageProfile.fileCSIDriver.enabled`, `.diskCSIDriver.enabled`; do not infer either from `properties.addonProfiles.azureKeyvaultSecretsProvider.enabled` |
| Point-to-Site VPN / Azure VPN Client retirement | Does a P2S gateway / VPN server config exist? | `microsoft.network/p2svpngateways`, `microsoft.network/vpnserverconfigurations`, `microsoft.network/virtualnetworkgateways` → `properties.vpnClientConfiguration` |
| Azure Site Recovery / DR | Does a Recovery Services vault exist? | `microsoft.recoveryservices/vaults`; items via `recoveryservicesresources` |
| Cosmos DB backup / Fabric mirroring prerequisites | Backup mode already answers "Continuous Backup?" | `microsoft.documentdb/databaseaccounts` → `properties.backupPolicy.type` (`Periodic`/`Continuous`), `properties.enableAnalyticalStorage` |
| Storage auth model (SAS / Shared Key) | Shared-Key / public-access / TLS posture | `microsoft.storage/storageaccounts` → `properties.allowSharedKeyAccess`, `.allowBlobPublicAccess`, `.minimumTlsVersion`, `.publicNetworkAccess` |

Only defer what is genuinely NOT in Resource Graph: in-cluster K8s manifests (sidecars, Helm values,
`ContainerNetworkLog` CRs), application/SDK code, data-plane usage, per-blob tier distribution, and
org-external assets. This guidance lives in the planning prompt (`src/agent/prompts/tools.py`) and the
report `additional_checks` rule (`src/agent/prompts/report/base.py`).

## ResourceGraphQueryBuilder

Static methods in `src/services/resource_graph.py` that return KQL strings:

- `get_resource_summary()` — counts by type
- `get_resources_by_type(resource_type)` — list resources of a type
- `get_query_for_update_service(service_name)` — dispatcher: maps Azure service name → optimized detail query
- `get_query_for_resource_type(resource_type)` — maps an ARM **resource type** (lowercase) → its builder query, or `None`. Used as a recovery fallback by the fixer (see below).
- Each service-specific query projects relevant `properties.*` columns
- Detail queries must project the properties that reports actually reason about, e.g. the AKS
  query projects **ACNS** (`properties.networkProfile.advancedNetworking.observability/.security.enabled`)
  and storage CSI state (`properties.storageProfile.fileCSIDriver/.diskCSIDriver.enabled`)
  and the Cosmos query projects backup mode + analytical storage
  (`properties.backupPolicy.type`, `properties.enableAnalyticalStorage`, `properties.disableLocalAuth`) —
  missing fields force the report to hedge ("점검 필요") instead of answering definitively.
- **Never project raw `properties` / `tags` / `sku` blobs from a broad enumeration query.** They are
  large JSON objects: `find_related_resources` projected all three and its result hit the prompt
  budget on **100%** of calls (60 storage accounts = 47,804 chars), so resources past the cutoff
  were dropped before the model saw them. Project named columns, and always add a stable
  `order by` so a truncated result is at least deterministic.
- **Render results one row per line** (`format_rg_result()` in `src/agent/tools.py`). The old
  `str(result)` dict repr put an entire result on a single line, so a budget cut landed mid-row and
  the stored full result could not be searched line by line.
- **Put the answer before the evidence.** A summary that a report actually needs (a region verdict,
  a resource-type distribution) must precede the detail rows, because truncation always eats the
  tail. `get_service_region_availability` and `find_related_resources` both do this.

### Adding a New Service Query

1. Add a static method: `_query_<service_name>() -> str`
2. Register in `get_query_for_update_service()` dispatcher dict
3. Use `=~` for type comparison, project relevant properties
4. Test with `python -m scripts.test_local resources`

## ResourceGraphQueryFixer

Located in `src/agent/tools.py`:

- LLM-assisted KQL query auto-fix (up to `MAX_QUERY_RETRIES`)
- Intercepts query failures, sends error + query to LLM for correction
- Re-executes corrected query automatically
- Records successful patterns in `kql_knowledge_base.json` via `kql_knowledge.py`
- **Circuit breaker**: After 3 consecutive LLM fix failures, falls back to rule-based fixes
- **Agent routing**: Uses the required `FOUNDRY_RESOURCE_GRAPH_AGENT_NAME` for KQL fixes.
  Another specialist never substitutes for it; an unavailable Agent falls through to the
  deterministic rule/builder recovery path and otherwise remains an explicit evidence gap
- **Strict output contract**: the persisted specialist always returns `{status, claims, gaps}`.
  Repair calls set `tool_choice=none`; `_extract_kql_response()` accepts raw KQL for test/backward
  compatibility or extracts an executable table query from a claim. A gap-only or malformed JSON
  response falls through to deterministic recovery and is never prefixed with `Resources`.
- **Task argument guard**: before execution, a legacy `find_related_resources.query` value is
  normalized to `keyword`, and a natural-language `query_azure_resources.query` paired with
  `resource_type` is replaced by that type's rich builder (or a bounded identity projection).

### sanitize/`_rule_based_fix` hardening — never emit a guaranteed-broken query

The rule-based fallback must always produce an *executable* query; otherwise the retry
loop burns every attempt and hits `kql_query_exhausted`. Three subtle self-defeating
behaviors were fixed (verified by directly observing the pipeline against the 56 recorded
queries + realistic failure scenarios):

| Defect | Symptom | Fix |
|--------|---------|-----|
| **`let` dangling reference** | `let x='V'; … == x` → strip `let` leaves `== x` (unresolved) | `sanitize_kql` **inlines** the value into `\bx\b` refs *before* removing the `let` (`_RE_LET_DECL`) → `== 'V'` |
| **`extend` orphaning** | project-mover makes `extend kindValue=… \| project name, kindValue`, then a blunt "strip all extends" fallback orphans `kindValue` | `_strip_unreferenced_extends()` drops an extend **only if its alias is unused downstream**; a referenced alias is kept |
| **Missing pipe before `project`/`extend`** | `… 'storageaccounts' project name` left `project` with no `\|` | `_RE_MISSING_PIPE` alternation extended to `project\|extend\|mv-expand\|distinct` |

Rule of thumb: a fix that removes a clause must also fix (or preserve) everything that
*referenced* that clause — never leave a dangling identifier or an orphaned projection alias.

### Builder-fallback recovery — never degrade a type that has a builder

A 3-month audit (all of `kql_knowledge_base.json` cross-referenced with the `results_*.jsonl`
reports) found **57% of recorded fallback queries had degraded to a generic raw-properties
dump** (`| project name, type, resourceGroup, subscriptionId, location, sku, properties | limit 100`),
and that degradation *directly caused* reports to hedge on queryable facts (private endpoint,
public network access, TLS). Fix: when `_rule_based_fix` exhausts the targeted fixes (attempt > 3),
it now calls `ResourceGraphQueryBuilder.get_query_for_resource_type(<type>)` and, if a builder
exists for that type (storage, VM, AKS, Cosmos, KeyVault, LogAnalytics, VNet, NSG, publicIP,
ACR, SQL, CognitiveServices, ContainerApps), substitutes the **known-good builder query**
(which preserves domain projections) instead of degrading to a raw dump. Only types with *no*
builder fall back to the generic dump. When you add a new builder, register its type in
`ResourceGraphQueryBuilder._TYPE_TO_BUILDER` so the fixer can recover it.

### Result-driven (semantic) improvement — fix queries that run but return nothing

Error-driven fixing only handles queries that *fail*. A query can be syntactically valid
yet **semantically wrong** — it runs, returns 0 rows because its filter is too strict or
uses a wrong property value/path (e.g. `kind =~ 'Storage'` when the real value is
`BlobStorage`), and the report then sees no affected resources. `execute_kql_with_retry`
adds a bounded semantic layer (`MAX_RESULT_IMPROVEMENTS = 2`):

1. On an **empty** result from a *property-filtered* query (`_query_has_property_filter`),
   run a cheap **type-only probe** (`_build_type_probe_query`) — does the resource type
   have any resources at all?
2. If the type **exists** (probe non-empty) but the filter matched none, the filter is
   wrong. `ResourceGraphQueryFixer.improve_query_for_empty_result` sends the query + a
   sample of the **real** data to the LLM, which corrects the filter against the actual
   property values, and the improved query is **re-executed**.
3. If the type is genuinely **absent** (probe empty), the empty result is correct — accept it.
4. A successful improvement is persisted (`record_successful_query`, purpose
   `"Result-improved query (was empty)"`) and reused via `build_context_for_prompt`.

### Specialist failure boundary

The analyzer injects only the Resource Graph specialist into `ResourceGraphQueryFixer`.
An availability error is not sent to the coordinator, report writer, or quality reviewer.
The retry pipeline applies its deterministic sanitizer and registered builder fallback; if
those cannot preserve the query intent, the failure remains an explicit evidence gap.

## Resilience Patterns for KQL

### Retry with Backoff
```python
from src.agent.resilience import retry_with_backoff

# KQL queries use exponential backoff for transient errors
result = await retry_with_backoff(
    lambda: service.query_resources(kql),
    max_retries=MAX_QUERY_RETRIES,
    retryable_errors=(429, 503),
    base_delay=1.0,
    max_delay=32.0,
)
```

### Specialist-Assisted Repair with Deterministic Fallback
```
1. Sanitize KQL (common LLM errors: | top → | take, inline let, etc.)
2. Execute against Resource Graph
3. On failure:
  a. Use the Resource Graph specialist to fix the query
  b. If the specialist fails → apply rule-based sanitization and a registered builder query
  c. If deterministic recovery fails → preserve the error as a gap
4. Track consecutive failures (circuit breaker at threshold=3)
5. Record successful queries to knowledge base
```

### Diminishing Returns Detection
```
If 3+ KQL retry iterations produce the same error:
  → Stop retrying, return error with collected context
  → Prevents wasting tokens on unfixable queries
```

## KQL Knowledge Base

`src/agent/kql_knowledge_base.json` stores discovered schema information:

- `schemas`: resource type → discovered property paths
- `queries`: successful query patterns for reuse
- Loaded lazily by `src/agent/kql_knowledge.py`
- Auto-updated when agent discovers new property paths at runtime

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| `BadRequest` with `let` | Used `let` statement | Inline the value |
| Empty results | Case-sensitive `type ==` | Use `type =~` |
| `properties.X` returns null | Wrong property path | Check with exploratory `project properties` query first |
| Timeout on large tenants | Unfiltered `Resources` | Always add `where type =~` filter |
