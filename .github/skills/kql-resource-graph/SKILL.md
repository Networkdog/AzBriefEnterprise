---
name: kql-resource-graph
description: 'Write and debug KQL queries for Azure Resource Graph. Use when: KQL query, Resource Graph query, query_resources, ResourceGraphQueryBuilder, get_query_for_update_service, resource type filtering, tenant-scoped query, KQL constraints, query auto-fix.'
---

# KQL Resource Graph Queries

## Foundry Runtime Guidance

- As the Resource Graph specialist, own KQL authoring, schema probing, result
  interpretation, and KQL repair. Do not hand this work to another specialist.
- Start with the update's applicability question, not a predefined service template.
  Select nested projections, typed predicates, distributions, array expansion or ID-based
  relationships according to the evidence needed. Builders are optional examples, not limits.
- Resource Graph supports project expressions, join/union and mv-expand within documented
  limits. No let, render, datatable, externaldata, toscalar or custom join strategies.
  Use at most three join/union operations combined and three mv-expand operators; observe
  cross-table/right-table reuse restrictions. mv-expand supports arrays and documented bag
  expansion, defaults to 128 elements and allows at most 2000; set its limit explicitly.
- Compare types with =~ or in~, retain scalar id/subscriptionId and order enumerations stably.
  Cast by meaning and distinguish null/missing from false/zero. Project expressions are valid,
  but the live service rejects kind=tostring(kind); project kind directly or use resourceKind.
  Keep all downstream alias references consistent. There is no five-extend restriction.
- Avoid broad raw properties/tags/sku dumps, but allow bounded 1-5 resource/parent-bag samples
  for discovery. Inspect nested objects and arrays across variants; cached schemas and samples
  are hints, not exhaustive evidence. Do not guess a dependent query before its probe returns.
- Keep similarly named AKS properties semantically distinct: Azure Files/Disk CSI state comes
  from `storageProfile.fileCSIDriver` / `diskCSIDriver`; the Key Vault secrets provider under
  `addonProfiles.azureKeyvaultSecretsProvider` is not a storage CSI signal.
- Query tenant-wide accessible subscriptions by default and cite exact IDs. When the runtime supplies
  a Management Group/Subscription/Resource Group scope, treat it as a hard boundary and never query
  or report outside it. App-injected Resource Group or intersected Management Group/subscription
  predicates prohibit join/union: use separate scoped queries and correlate exact IDs instead.
  Try the relevant ARG table/path before deferring; unsupported/masked ARM fields remain gaps.
- Pass purpose and expected_columns to query_azure_resources. On syntax failure, off-topic rows,
  an empty filtered result or missing required values, use observed errors/schema to rewrite
  and re-execute without changing scope, identities, thresholds or the original question.
  Zero rows can be correct; never relax an eligibility/security condition merely to find rows.
- Inspect executed_query, query_status and evidence_gaps, not just HTTP success. The inner loop
  has at most eight attempts and two result rewrites, stops repeated queries, and returns gaps
  when no supported correction exists. Never substitute a builder/count for a failed question.
  Use the next native tool round or evaluation/revision pass for a different, evidence-led query.
- Complete enumerations must not contain take/limit; retain scalar IDs for SDK paging. The service
  collects at most ten 1000-row pages. For result_truncated=true, narrow or partition KQL. A local
  [ref=Rn] search can recover a stored preview, not uncollected Azure pages or capped storage.

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
| `toscalar()` | Not supported |

Supported operators are not an excuse to add complexity without an information need. Joins support
`innerunique`, `inner`, `leftouter` and `fullouter`; the default is `innerunique`. SDK limits are three
join/union operations combined and three mv-expand operators. Cross-table joins/right-table reuse
have additional restrictions. Array expansion defaults to 128 items (maximum 2000). Bag expansion is
supported, including `mv-expand bagexpansion=array tags`; track empty arrays and row multiplication.
See [query language](https://learn.microsoft.com/azure/governance/resource-graph/concepts/query-language)
and [pagination](https://learn.microsoft.com/azure/governance/resource-graph/concepts/work-with-data).

### Mandatory Patterns

- **Type comparisons**: Always use `=~` (case-insensitive): `where type =~ "microsoft.compute/virtualmachines"`
- **Scope-aware**: Queries run across all accessible subscriptions by default. A bounded analysis
  sets Management Groups/subscriptions on SDK `QueryRequest` and injects the Resource Group predicate
  immediately after the first Resource Graph table. Fields are intersected; values within a field
  are ORed. Never remove or broaden those runtime filters during KQL repair.
- **Exact Resource Group IDs**: A full ARM ID keeps its parent subscription and becomes an exact
  `(subscriptionId AND resourceGroup)` predicate. The app rejects join/union when it must inject
  Resource Group or intersected MG/subscription predicates. SDK-only subscription/MG boundaries
  can use supported joins. Never read or persist shared KQL knowledge in a bounded analysis.
- **`subscriptionId` column**: Always available — use it for subscription-level grouping
- **Property access**: Use `properties.X` dot notation, e.g., `properties.storageProfile.osDisk.osType`

### Query Shapes Are Examples, Not Templates

Choose the minimum query that discriminates the update's actual applicability. Known paths can be
queried directly; unknown or inconsistent shapes need bounded exploration followed by a new query.
For instance, a TLS retirement needs a distribution plus exact affected identities, not VM inventory:

```kql
Resources
| where type =~ 'microsoft.storage/storageaccounts'
| summarize resources=count(),
  unknownTls=countif(isnull(properties.minimumTlsVersion)),
  legacyTls=countif(tostring(properties.minimumTlsVersion) in~ ('TLS1_0', 'TLS1_1'))
```

Array-level configuration must inspect the relevant elements rather than index zero alone:

```kql
Resources
| where type =~ 'microsoft.containerservice/managedclusters'
| mv-expand pool=properties.agentPoolProfiles limit 2000
| project id, subscriptionId, poolName=tostring(pool.name), osSKU=tostring(pool.osSKU)
| order by id asc, poolName asc
```

A nested-property query can use a direct project expression or extend for reuse:

```kql
Resources
| where type =~ "microsoft.compute/virtualmachines"
| extend status = tostring(properties.extended.instanceView.powerState.displayStatus)
| project id, name, resourceGroup, subscriptionId, location, status
| order by id asc
```

### Completeness — query the answer instead of deferring it

A 3-month report audit (2026-07) found the agent repeatedly punting **queryable** ARM facts to
`additional_checks`/CSA review. Try the documented ARG table and observed property path first;
not every ARM property is exposed in ARG. Unsupported/masked fields or missing permissions remain
explicit gaps, with ARM-only facts owned by the Azure API specialist. Common examples:

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
- `get_query_for_resource_type(resource_type)` maps an ARM resource type to an optional baseline,
  or None. Natural-language task normalization may use it as an initial query while preserving
  purpose/expected_columns; the fixer never substitutes it for a failed targeted query.
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

### Syntax Repair Preserves Meaning

`sanitize_kql` protects strings/comments before small lexical fixes. It does not move valid project
expressions, rename aliases behind downstream references, split mv-expand's limit into another
operator, or remove unsupported datatable expressions to produce unrelated resource inventory.
Legacy scalar let inlining remains a compatibility repair, not permission to author let statements.
The live service rejects `kind=tostring(kind)` while `resourceKind=tostring(kind)` works: repair that
specific alias contract, not every computed projection.

`_rule_based_fix` now applies only lexical repair. The former join/array stripping, field deletion,
builder substitution and final count fallback lost the original question and are removed. A
specialist must repair with supporting error/schema evidence, otherwise the failure remains a gap.
Repeated or unchanged corrections stop before executing the same failed query again.

### Result-Driven Rewrite Loop

`query_azure_resources(query, purpose="", expected_columns=[])` supplies an information requirement.
The coordinator carries task purpose into execution even when the optional tool field is omitted.
The inner `execute_kql_with_retry` loop has eight main attempts and at most two result rewrites:

1. Inspect the returned rows and required projected values, not just request success. False and 0
  are valid; missing/null/empty required values remain unknown.
2. For an empty filtered result or missing fields, make a stable five-row diagnostic probe only
  when a single table/type is unambiguous. Keep the original table and SDK/runtime scope. Never
  replace join/union intent with a one-type probe or return probe rows as affected resources.
3. `improve_query_for_result` receives the original question/query, required columns, issue and
  bounded observed data. It may correct paths, casts, predicates, array shape or relationships;
  it may not relax applicability/security thresholds merely to obtain rows. Type existence does
  not imply a wrong filter. An unchanged query can confirm a justified zero; missing evidence
  returns no correction and preserves a gap. The empty-result method delegates to this reviewer.
4. Re-execute a new query, then inspect its result again. Stop cycles, absent corrections, or the
  shared attempt/result-rewrite budget. The final successful request still returns its real rows
  with executed_query, query_attempts, result_rewrites, query_status and evidence_gaps.
5. Evaluation's query_intent criterion checks whether the decisive question was answered. A false
  criterion cannot pass as sufficient before the existing revision limit; exhausted limits retain
  the missing criterion and reason. The next native tool round/revision can request a new targeted
  query based on actual schema outputs, rather than guessing dependent work in parallel.

### Schema and Result Completeness

`explore_resource_schema` reads five live samples even when cached paths exist. It traverses nested
objects/arrays to depth eight, up to 250 paths and eight elements per sampled array, disclosing caps.
Multiword focus matches individual keywords. Array `[]` paths are discovery notation, requiring
mv-expand for all elements. Only paths, not sampled values, enter the existing runtime knowledge store.

`ResourceGraphService` requests objectArray data and follows up to ten 1000-row pages. It preserves
query/scope on each page and marks missing/repeated tokens or exhausted budgets as incomplete.
Service details retain every received row and null value, instead of discarding everything after
row 20. JSON metadata stays on one header line. Query-tool refs can recover stored previews, not
pages never fetched; partition/narrow KQL when result_truncated is true.

### Specialist failure boundary

The analyzer injects only the Resource Graph specialist into `ResourceGraphQueryFixer`.
An availability error is not sent to the coordinator, report writer, or quality reviewer.
The retry pipeline applies deterministic lexical repair only; if it cannot preserve the query
intent, the failure remains an explicit evidence gap rather than an unrelated successful builder.

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
  b. If the specialist fails → apply only meaning-preserving lexical sanitization
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

`src/agent/kql_knowledge_base.json` is the checked-in, tenant-neutral schema/query seed:

- `schemas`: resource type → discovered property paths
- `queries`: successful query patterns for reuse
- Loaded lazily by `src/agent/kql_knowledge.py`
- Never commit runtime `failed_queries`, correlation IDs, tenant resource names, or request errors
- Runtime discoveries are written to `AZBRIEF_DATA_DIR/kql_knowledge_base.json`, or the ignored
  local `data/kql_knowledge_base.json` when that environment variable is unset

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| `BadRequest` with `let` | Used `let` statement | Inline the value |
| Empty results | Case-sensitive `type ==` | Use `type =~` |
| `properties.X` returns null | Wrong path, unset/masked field, or unsupported data | Inspect a bounded parent-bag/schema sample; keep unknown distinct from false |
| Timeout on large tenants | Unfiltered `Resources` | Always add `where type =~` filter |
