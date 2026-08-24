"""Phase-specific prompts: planning, evaluation, execution retry, revision.

These prompts are used as HumanMessage content (not system prompts).
"""

ANALYSIS_PROMPT = """## Azure Update Information
**Title**: {title}
**Description**: {description}
**Update Type**: {update_type}
**Related Services**: {azure_services}
**Published Date**: {published_date}
**Detail Link**: {link}
{learn_more_section}
## Administrator's Azure Resource Inventory (Overview)
{resource_summary}

## Resource Query Status
**Query Success**: {resource_query_status}

{kql_knowledge_context}

> The resource type summary above is already provided.
> Do NOT call `get_resource_type_summary` again. Start directly with `search_update_related_docs` and `get_service_resource_details`.


"""

PLANNING_PROMPT = """## Analysis Planning Instructions

Based on the Azure Update information provided above, create a structured analysis plan.

### Pre-Check: Resource Inventory Relevance
**Before designing tasks**, check the Resource Inventory above. If the update is about a service
that has NO matching resource type in the inventory (e.g., "Batch" retirement but no `microsoft.batch/*`
resources exist), the analysis should still proceed but should be **minimal**:
- Include only 1 resource verification task (to confirm absence) and 1 documentation task
- Skip schema exploration, cost analysis, and detailed queries for absent resource types
- The report will correctly mark the update as `not_relevant`
This saves significant time and tokens for updates about unused services.

### CSA-Level Resource Analysis (when service IS in inventory)
When the update IS relevant to resources in the inventory, plan **deep configuration analysis**:
- **Configuration gap query**: Design a custom KQL query that checks the specific setting/property
  mentioned in the update against actual resource values. Example: if the update retires TLS 1.0,
  query `properties.minimumTlsVersion` to find resources still on 1.0.
- **Dependency analysis**: If the update affects a core service (Storage, VNet, Key Vault),
  consider querying dependent resources. Example: Storage Account TLS change → check Private Endpoints
  and linked services.
- **Quantification**: Always aim to produce "X out of Y resources affected" numbers, not just "some resources".
  Use `summarize count() by field` queries to get distributions.

### Pre-Check: Pre-fetched Reference Documents
If the update context above includes a **"Official Reference Documents"** section, these documents
have already been fetched from the update's Learn More links. When pre-fetched documents are available:
- **Reduce or skip `search_update_related_docs` / `search_azure_docs` tasks** — the primary reference is already provided
- **Still include at least 1 KQL task** for resource identification
- **Focus doc search tasks on gaps** only (e.g., migration guides, CLI commands not covered by the pre-fetched docs)

### IMPORTANT: Emit the plan DIRECTLY
**Do NOT call search tools during planning.** The update context above already contains enough information to create a plan.
Include doc search tasks (`search_update_related_docs`, `search_azure_docs`, etc.) as execution tasks in your plan instead — they will be executed in parallel with other tasks during the Execution phase, which is faster.

Only call a planning-phase search tool if the update title/description is genuinely ambiguous and you cannot determine which Azure service or resource type is involved.

### Create the Analysis Plan
Design specific analysis tasks based on the update context.

#### Available Tools by Method
1. **kql** (Azure Resource Graph):
   - `get_service_resource_details` -- Optimized predefined query per service (FAST, covers common fields)
   - `get_resource_configurations` -- Configuration profiling with distribution summary (RECOMMENDED for version/config impact analysis)
   - `get_resource_dependencies` -- Dependency mapping for blast radius analysis (RECOMMENDED for core infrastructure updates)
   - `query_azure_resources` -- Custom KQL query you write yourself (FLEXIBLE, for specific fields)
   - `get_security_posture` -- Security configuration analysis
   - `find_related_resources` -- Keyword-based resource search
   - `explore_resource_schema` -- Discover properties schema for a resource type
2. **cost_api** (Cost Management API):
   - `get_cost_by_resource_type` -- Cost breakdown by resource type
   - `get_cost_by_service` -- Cost breakdown by service name
3. **log_analytics** (Log Analytics):
   - `query_log_analytics` -- Custom KQL query on logs
   - `get_recent_errors` -- Recent error summary
   - `get_activity_log_summary` -- Activity log summary
4. **advisor** (Azure Advisor):
   - `get_advisor_recommendations` -- Advisor recommendations by category. Set use_rest_api=True for detailed data including remediation actions, learn-more links, potential benefits, risk level, and solution text
5. **service_health** (Service Health):
   - `get_service_health` -- Current service health status (KQL-based, fast)
   - `get_service_health_events` -- Detailed health events via REST API with affected services/regions, recommended actions, and FAQ links
6. **resource_health** (Resource Health):
   - `get_resource_health` -- Availability status (Available/Unavailable/Degraded) of resources. Essential for impact analysis
7. **policy** (Azure Policy):
   - `get_policy_compliance` -- Policy compliance summary. Non-compliant resource counts by policy assignment
8. **learn_search** (Microsoft Learn):
   - `search_update_related_docs` -- Comprehensive update-related doc search
   - `search_azure_docs` -- Azure documentation keyword search
   - `get_service_documentation` -- Service-specific documentation
   - `search_resource_graph_docs` -- Resource Graph KQL documentation
7. **azure_rest** (Azure Management REST API):
   - `call_azure_rest_api` -- Call any Azure ARM API for SKU/availability/capability checks
9. **context** (already-collected results):
   - `query_tool_result` -- Search the full text of an earlier result shown to you truncated

#### Impact Analysis Task Planning (RECOMMENDED)

For thorough impact analysis, consider adding these tasks:

- **Resource Health check**: Add a `resource_health` method task with `get_resource_health` to verify
  current health state of affected resource types. Shows if resources are Available, Degraded, or Unavailable.
  - Especially valuable for Retirement/Breaking Change and Security updates.

- **Policy Compliance check**: Add a `policy` method task with `get_policy_compliance` when the update
  relates to security, governance, or configuration changes. Shows how many resources are non-compliant
  with relevant policies.
  - Especially valuable for Security updates and new compliance features.

- **Detailed Service Health**: Add a `service_health` method task with `get_service_health_events` when
  the update might be related to active incidents or planned maintenance. Returns affected services,
  regions, recommended actions, and FAQ links.

#### Region & Resource Availability Task Planning (MANDATORY for new_feature, preview, new_service, region_expansion)

When the update announces new resource types, features, or SKUs,
**always include an availability verification task** in the plan:

- **For new VM sizes** (e.g., "Dlsv7/Dsv7/Esv7"): Add a `call_azure_rest_api` task with:
  - `path`: "/subscriptions/{subscriptionId}/providers/Microsoft.Compute/skus"
  - `filter_expression`: "location eq 'koreacentral'" (admin's primary region)
  - `api_version`: "2021-07-01"
  - Then filter the results in the report for the specific VM size names

- **For Storage/Network/other resource SKUs**: Add a `call_azure_rest_api` task with:
  - `path`: "/subscriptions/{subscriptionId}/providers/{ResourceProvider}/skus"
  - The resource provider namespace matches the update's service (e.g., Microsoft.Storage, Microsoft.Network)

- **For general service region availability (PREFERRED)**: Add a `get_service_region_availability` task with:
  - `provider_namespace`: the update's service namespace (e.g., "Microsoft.Databricks", "Microsoft.App")
  - Optionally `regions`: comma-separated regions to check; omit to auto-detect the admin's primary regions
  - This queries the ARM providers API for a definitive ✅/❌ answer — use INSTEAD of doc search whenever possible

- **For features not resolvable via the providers API**: Add a `search_azure_docs` task with:
  - `query`: "[feature name] supported regions" or "[feature name] availability"

#### KQL Tool Selection Decision Tree (FOLLOW THIS)

```
Is a predefined query available for this service?
├── YES (Storage, VM, AKS, Function Apps, App Service, SQL, Cosmos DB,
│        Container Apps, Key Vault, Container Registry, VNet, NSG, Public IP,
│        Log Analytics, Cognitive Services)
│   │
│   ├── Does the predefined query already include the field mentioned in the update?
│   │   ├── YES → Use `get_service_resource_details` (Task 1). Done.
│   │   └── NO  → Use BOTH:
│   │       Task 1: `get_service_resource_details` (get baseline inventory)
│   │       Task 2: `explore_resource_schema` (discover the missing property path)
│   │       Task 3: `query_azure_resources` (custom KQL targeting the discovered field)
│   │
│   └── Unsure whether the field is included?
│       → Use `get_service_resource_details` first. The Evaluation phase will
│         detect if the needed field is missing and add a follow-up task.
│
└── NO (service not in the predefined list)
    │
    ├── Resource type is known (e.g., Microsoft.Network/applicationGateways)?
    │   Task 1: `explore_resource_schema` (discover properties structure)
    │   Task 2: `query_azure_resources` (custom KQL based on discovered schema)
    │
    └── Resource type is unknown?
        Task 1: `find_related_resources` (keyword search to identify resource type)
        Task 2: Based on results, either `explore_resource_schema` or `query_azure_resources`
```

**Predefined query coverage** (fields already included — no custom KQL needed):
- **Storage**: SKU, HNS, SFTP, TLS version, public access, shared key, private endpoints, encryption
- **VM**: size, OS, security type, Trusted Launch, encryption at host, disks, availability zones
- **AKS**: K8s version, addons (8 types), RBAC, AAD, auto-upgrade, network plugin/policy/dataplane, private FQDN
- **Function Apps**: all runtime versions (Python, Node, Java, .NET, PowerShell), TLS, HTTPS, VNet integration
- **App Service**: runtime, TLS, HTTPS, HTTP/2, VNet integration, always-on
- **SQL Database**: SKU, zone redundancy, read scale, backup redundancy, ledger, license type
- **Key Vault**: soft delete, purge protection, RBAC auth, private endpoints, network ACLs
- **Container Apps**: replicas, scaling rules, Dapr, ingress transport, revision mode

If the update mentions a field NOT in the above list, you MUST plan a `explore_resource_schema` + `query_azure_resources` combo.

#### Custom KQL Writing Tips
When writing custom KQL for `query_azure_resources`:
- Resource Graph uses KQL subset: NO `let`, `render`, `datatable`, `externaldata`
- **AVOID `join` queries** — Resource Graph's KQL subset has very limited `join` support. Complex join + mv-expand combinations almost always fail with ParserFailure. Instead of joining two resource types, write **separate queries** for each resource type and let the report correlate the results.
  - BAD: `Resources | where type =~ 'microsoft.compute/virtualmachines' | join kind=leftouter (Resources | where type =~ 'microsoft.compute/disks') on ...`
  - GOOD: Write two separate tasks — one for VMs, one for Disks — and analyze the relationship in the report.
- **Keep `mv-expand` queries simple** — When using `mv-expand` to expand array properties (e.g., `agentPoolProfiles`), limit the number of subsequent `extend` statements to 5 or fewer. If you need more fields, use `project name, type, resourceGroup, subscriptionId, location, sku, properties` instead and let the report extract what it needs from the raw `properties` bag.
  - BAD: `| mv-expand pool = properties.agentPoolProfiles | extend a = ... | extend b = ... | extend c = ... | extend d = ... | extend e = ... | extend f = ...` (6+ extends after mv-expand)
  - GOOD: `| mv-expand pool = properties.agentPoolProfiles | extend poolName = tostring(pool.name) | extend osSKU = tostring(pool.osSKU) | project name, poolName, osSKU, properties`
- `type` values are lowercase in data — always use `=~` (case-insensitive) or lowercase string
- Wrap `properties.*` nested values in `tostring()` before `summarize` or `==`
- Use `project-away` (NOT `project-except`) to remove join duplicate columns
- Max 1000 rows per page; use `take N` or `limit N` (NOT `top N` without `by`)
- Access `sku` and `tags` as top-level fields: `sku.name`, `tags['keyName']`
- Use `extend` for computed columns BEFORE `project` — do NOT put expressions inside `project`
  - BAD:  `| project name, foo=tostring(properties.bar)`
  - GOOD: `| extend foo=tostring(properties.bar) | project name, foo`
- `kind` is a reserved top-level field — do NOT alias it (e.g., `kind=tostring(kind)` fails)
- Do NOT use trailing semicolons

#### Planning Guidelines
- Adjust analysis scope based on update type:
  - **Retirement/Breaking Change**: Precise affected resource identification + migration path docs + resource health check (CRITICAL)
    - **MUST include** `get_resource_configurations` task to profile current versions/settings of affected resources
    - **SHOULD include** `get_resource_dependencies` task for core services (Storage, VNet, Key Vault, SQL, AKS) to assess blast radius
  - **New GA Feature**: Inventory of eligible resources + cost impact analysis
    - **SHOULD include** `get_resource_configurations` to identify resources that could benefit from the new feature
  - **Security Update**: Security posture analysis + affected resource identification + policy compliance check
    - **MUST include** `get_resource_configurations` task to find resources with insecure config values
  - **Preview Feature**: Basic resource inventory + documentation reference
- For **version-specific updates** (K8s version, TLS version, runtime version), always include a `get_resource_configurations` task to get the version distribution across all resources
- For **infrastructure updates** affecting Storage, VNet, Key Vault, SQL, or AKS, always include a `get_resource_dependencies` task to map the blast radius
- Each task must specify exact `tool_name` and `tool_args` (matching the tool's input schema)
- Include 3-8 tasks for thorough analysis
- Always include at least one `kql` method task for resource identification
- Always include at least one `learn_search` method task for documentation evidence
- For Retirement/Breaking Change updates, include a `resource_health` task
- For Security updates, include a `policy` task for compliance state

### Output Format
After gathering context through doc search, output the analysis plan as JSON.
Do NOT wrap in markdown code fences. Output raw JSON only.

{
  "plan_id": "plan_v1",
  "update_summary": "Brief summary of the update",
  "analysis_goal": "What this analysis aims to achieve",
  "tasks": [
    {
      "task_id": "task_1",
      "description": "What this task analyzes",
      "method": "kql | cost_api | log_analytics | learn_search | advisor | service_health | resource_health | policy | azure_rest | context",
      "tool_name": "exact_tool_name_from_list_above",
      "tool_args": {"arg_name": "arg_value"},
      "purpose": "Why this analysis is needed"
    }
  ]
}
"""

EXECUTION_RETRY_PROMPT = """A tool call failed during analysis execution. Fix the tool arguments.

## Failed Tool Call
- **Tool**: {tool_name}
- **Original Arguments**: {tool_args}
- **Error**: {error}

## Reference Documentation
{docs_context}

## Instructions
Analyze the error and provide corrected tool arguments.
- For KQL queries: fix syntax, table names, column names, or operators
- For search tools: adjust keywords or parameters
- For cost/log tools: adjust time range or filters

Respond with ONLY the corrected tool_args as a JSON object. No explanation, no markdown fences.
"""

EVALUATION_PROMPT = """## Analysis Result Evaluation

Evaluate the completeness and quality of the collected analysis results.
**Prefer "sufficient" over "partial"** — only return "partial" if a CRITICAL piece of information is clearly missing and can be obtained by additional tool calls.

### Update Context
{update_context}

### Collected Task Results
{task_results_summary}

### Evaluation Criteria
| Aspect | Required | Criterion |
|--------|----------|-----------|
| Resource Identification | Required | Related resources queried (found or confirmed absent). **If the service has no predefined query and a custom KQL was attempted, this is met even if results are empty.** |
| Configuration Gap Analysis | Conditional | Only if the update is about a retirement, breaking change, or feature_change that requires config migration. NOT needed for new_feature, preview, region_expansion, new_service, sdk_tooling. |
| Cost Impact | Conditional | Only if the update explicitly changes pricing. NOT needed for feature/preview announcements. |
| Security Impact | Conditional | Only if the update is about a security enforcement or vulnerability. |
| Documentation Evidence | Required | At least 1 Microsoft Learn doc URL obtained from tool results. **If search was attempted but returned no results, this is met.** |
| Actionability | Conditional | Only for retirement/feature_change categories. NOT required for preview, new_service, region_expansion — these categories intentionally have no action items. |
| Evidence Completeness | Required | **UNMET** whenever a task result ends in `[TRUNCATED PREVIEW — showing N of M chars] [ref=Rn]` AND the update's key question (which resources are affected, which values are non-compliant, whether any resource has property X) could be answered by the rows you were not shown. A preview is a sample, not an enumeration — counting or concluding absence from it is a factual error. Met when nothing was truncated, or when the unshown rows cannot change the answer. |

**IMPORTANT: Bias toward "sufficient"**
- If all required criteria are met and the task results contain enough information to write a meaningful report, return **sufficient**.
- Do NOT return "partial" just because additional optional information could theoretically be collected.
- Additional KQL queries for minor details (e.g., cost data for a non-pricing update) are NOT worth an extra iteration.

**The one exception — Evidence Completeness**
The sufficiency bias does NOT override the Evidence Completeness criterion. Before setting
`evidence_complete: true`, re-read each task result and check whether it ends in
`[TRUNCATED PREVIEW — ... ] [ref=Rn]`.
- If one does and the update's key question could be answered by the unshown rows, set
  `evidence_complete: false`, return **partial**, and suggest
  `query_tool_result(ref="Rn", pattern="<the value you need to find>")`.
- Searching a stored result costs no Azure call and no query time, so this is never the
  "not worth an extra iteration" case.
- Example: a TLS retirement where the storage enumeration is truncated and every account in
  the preview is compliant — you cannot report "no affected resources", because the
  non-compliant one may be in the rows you were not shown.

### Verdict Classification
- **sufficient**: Required criteria met. Enough data to generate a useful report. **This should be the default verdict.**
- **partial**: A critical required criterion is clearly unmet AND a specific tool call would fix it. Use sparingly.
- **insufficient**: Analysis approach is fundamentally wrong (wrong resource type, completely off-topic). Very rare.

### Output Format
Respond with ONLY JSON (no markdown fences):
{{
  "verdict": "sufficient | partial | insufficient",
  "coverage": {{
    "resource_identification": true,
    "config_gap_analysis": true,
    "cost_impact": false,
    "security_impact": false,
    "documentation_evidence": true,
    "actionability": true,
    "evidence_complete": true
  }},
  "missing_aspects": ["aspect1", "aspect2"],
  "suggestions": ["Add a KQL query for ...", "Search docs for ..."],
  "reason": "Explanation of the verdict"
}}
"""

REVISE_TASKS_PROMPT = """## Task Revision Instructions

The analysis evaluation found gaps. Revise the analysis plan to address them.

### Evaluation Result
{evaluation_result}

### Current Plan
{current_plan}

### Existing Task Results
{task_results_summary}

### Instructions
Based on the evaluation's `missing_aspects` and `suggestions`:
1. Create NEW tasks to fill the gaps (use task IDs like "task_r1", "task_r2", etc.)
2. Each new task must have a specific `tool_name` and `tool_args`
3. Focus on the missing aspects identified in the evaluation
4. Do NOT duplicate tasks that already completed successfully
5. When an existing result was TRUNCATED (`[ref=Rn]` in its preview) and the missing fact could
   be in the unshown rows, search it before querying Azure again:
   `{{"method": "context", "tool_name": "query_tool_result", "tool_args": {{"ref": "Rn", "pattern": "<resource name or property>"}}}}`

### Output Format
Respond with ONLY a JSON array of NEW tasks (no markdown fences):

[
  {{
    "task_id": "task_r1",
    "description": "What this task analyzes",
    "method": "kql | cost_api | log_analytics | learn_search | advisor | service_health | resource_health | policy | azure_rest | context",
    "tool_name": "exact_tool_name",
    "tool_args": {{"arg_name": "arg_value"}},
    "purpose": "Why this analysis is needed"
  }}
]
"""
