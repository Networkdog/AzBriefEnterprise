"""Category-specific report templates.

Only the relevant category template is injected into REPORT_PROMPT,
saving ~8-9K tokens per report generation.
"""

CATEGORY_INTRO = """### Category-Specific Report Templates

Each category has a tailored report structure. Follow the template for the classified category.
Section requirements are conditional on evidence, not merely the category name. A change or
retirement needs actions only for confirmed applicability; never invent targets when absent or
unverified. A new capability needs useful value and adoption conditions, not a compulsory action.

---
"""

CATEGORY_TEMPLATES: dict[str, str] = {
    "retirement": """#### CATEGORY: `retirement`
**Tone**: Action-oriented when applicability is confirmed; otherwise concise about scoped absence or uncertainty.
**one_line_summary pattern**: "[Service/feature] retiring [date] — [N] resources need migration"
**detailed_analysis structure**:
1. What is being retired and the exact retirement date
2. What happens to resources after the deadline (service disruption? degraded? unsupported?)
3. Migration path — what replaces the retired feature (MUST include a `> **마이그레이션 경로**:` concept box)
4. Timeline: announcement date → end-of-support → hard cutoff
5. **Blast radius summary**: How many resources are affected, how many dependencies exist, which subscriptions are impacted
6. **Architectural trade-offs (not just steps)**: Beyond the migration procedure, explain the system-level consequences of the change for the admin's architecture — how the target state alters the **cost model, performance characteristics, feature availability, redundancy/DR, or integration points**, and any real decision the admin must weigh (e.g., a straight GPv2 upgrade vs. migrating a blob-only workload to a specialized alternative like BlockBlobStorage/FileStorage). Ground every trade-off in the retired-vs-replacement capabilities stated in the reference docs or tool evidence — do NOT invent trade-offs.

**Migration path concept box (MANDATORY for retirement)**:
Include a `> ` blockquote in the detailed_analysis that clearly states the migration destination:
```
> **마이그레이션 경로**: [retired feature] → [replacement]. [1-sentence migration method].
```
Example: `> **마이그레이션 경로**: AV36P/AV52 → AV48 또는 AV64 노드. HCX를 사용한 라이브 마이그레이션을 지원합니다.`

**Migration Playbook (MANDATORY when affected resources exist)**:
When `get_resource_configurations` results show affected resources, generate a concrete migration playbook:
1. **Current State**: List actual resource configurations from profiling (e.g., "prod-aks-01: K8s 1.28, dev-aks: K8s 1.30")
2. **Target State**: Required version/configuration after migration
3. **Pre-migration Checks**: Dependencies to verify (from `get_resource_dependencies`), backup procedures
4. **Step-by-step Migration**: Per-resource CLI commands with actual names, ordered by priority (production last)
5. **Validation**: How to verify successful migration
6. **Rollback Plan**: How to revert if migration fails

**Sections:**
- `affected_resources`: List ALL confirmed resources that must be changed, with their actual property evidence and `action_required` = true. Use `[]` for a scoped absence or a code/workflow-only target; explain applicability in `relevance_evidence`.
- `action_items`: Required ONLY for confirmed applicability — concrete migration steps with the real retirement date as `deadline`. A scoped absence does not require migration work; a material evidence gap does not justify a mutating command.
  **Key Dates extraction rule**: If the update text contains a "Key dates" section or lists multiple milestone dates (e.g., "June 30, 2026: Last day to buy 3-year RI", "June 30, 2029: Retirement"), each date MUST be captured as a separate `action_item` with that date as the `deadline` field. This ensures the timeline visualization in the email report correctly displays all milestones. Do NOT collapse multiple dates into a single action item.
  **Resource-specific commands**: CLI commands MUST use actual resource names from tool results, not placeholders. Example: `az aks upgrade --name prod-aks-01 --resource-group prod-rg --kubernetes-version 1.30` (NOT `--name <cluster-name>`)
  **Time estimation**: Calculate from actual resource count. Example: "리소스당 약 15분 × 3개 = 약 45분"
- `impact_summary`: Focus on `operational_impact` (service disruption risk) and `security_impact` (if end-of-support means no patches). Include blast radius context from dependencies.""",
    "feature_change": """#### CATEGORY: `feature_change`
**Tone**: Cautious, verification-focused. "Something changed — verify your workloads."
**one_line_summary pattern**: "[Service] [what changed] — [N] resources to verify ([risk])"
**detailed_analysis structure**:
1. What specific behavior/default/setting changed
2. How this might affect existing workloads (breaking scenarios)
3. Who is impacted — only users of feature X, or all users of the service?

**Sections:**
- `affected_resources`: List confirmed resources whose behavior changes with actual property evidence. Use `[]` when no ARM target applies; code/workflow/dependency evidence still belongs in `relevance_evidence`.
- `action_items`: Required for confirmed applicability — verification and remediation steps. Verify before making changes. Do not fabricate remediation for a scoped absence or material evidence gap.
- `impact_summary`: Focus on whichever dimensions are affected (security enforcement → `security_impact`; performance default change → `performance_impact`).""",
    "new_feature": """#### CATEGORY: `new_feature`
**Tone**: Opportunity-oriented, advisory. "Here's what you can now do — and what you gain."
The primary value of a GA report is NOT to create action items, but to inform the administrator what **opportunities and improvements** this feature unlocks for their environment.
**one_line_summary pattern**: "[Service] [feature] GA — [concrete benefit for admin's environment]"
**detailed_analysis structure**:
1. What the new feature does in plain terms, and how administrators reached that outcome before it existed (a component they operated themselves, a manual step, a third-party tool, or a limitation they accepted) — that contrast IS the problem statement
2. **Key benefits**: Concrete improvements this enables — cost savings, security posture, operational simplification, performance gains. Use numbers when possible (e.g., "geo-redundant backup으로 RPO를 24시간에서 0으로 단축 가능")
3. **Adoption considerations**: Whether opt-in is needed, prerequisites, limitations, and region availability
4. **Comparison to current state**: How the admin's existing resources relate to this feature — what they currently use vs. what becomes possible. This is the "before vs. after" framing that helps administrators decide whether to invest time
5. For features that could replace or enhance existing resources, describe the improvement path at a high level — NOT as mandatory action items, but as opportunities worth evaluating
6. **Whose opportunity this is**: name the operational responsibility the decision belongs to (network / security / cost / platform / data / application), so a reader can tell in one line whether it is theirs. Describe the responsibility, never the reader — write "네트워크 라우팅 설계를 맡은 쪽에서…", never "네트워크 담당자로서 당신은…".

**Sections:**
- `affected_resources`: **OPTIONAL** — list existing resources that could **benefit from** this new feature. Frame as opportunity, not impact. Set `action_required` = false. Use `reason` with the actual queried property that shows the current state (e.g., 'sku.name: Standard_LRS, encryption.type: PlatformKey — cross-tenant CMK 적용 가능').
- `action_items`: Value first; evaluation is optional. Include at most one non-mutating fit check for a known workload/workflow or supplied requirement with documented go/no-go criteria. Otherwise use `[]`. Ownership of the service is not required. Keep `deadline` empty and `cli_command` empty or read-only; never attach `update`, `set`, or `enable` to an evaluation task.
- `impact_summary`: This is an **opportunity summary, not an impact assessment**. Each dimension states what the administrator GAINS by adopting:
  - `cost_impact`: the saving or cost-optimization this unlocks
  - `security_impact`: the security posture improvement available
  - `performance_impact`: the performance or reliability gain possible
  - `operational_impact`: the operational work that disappears — a component no longer operated, a manual step automated, a standard that can be enforced
  Leave a dimension as an empty string when there is no concrete gain. An empty string is CORRECT and is required instead of "영향 없음" / "운영 변경 없음" / "도입하지 않아도 리스크 없음" — a new feature never changes existing behaviour, so stating its absence is a forbidden tautology.
- `additional_checks`: Only when exhaustive feature-level official evidence remains inconclusive,
  add one exact self-service check naming the feature, primary Region, Portal/API surface, and why.
  The headline must still say that availability is not officially confirmed.""",
    "new_service": """#### CATEGORY: `new_service`
**Tone**: Educational, advisory. "Here's a new tool in your toolbox."
**one_line_summary pattern**: "[Service name] now GA — [primary use case in one phrase]"
**detailed_analysis structure**:
1. What the service does, and how the same job was handled without it (a self-operated stack, a third-party product, a manual process) — that contrast IS the problem statement
2. Target audience — which teams or workloads benefit most, and which operational responsibility (network / security / cost / platform / data / application) would own the adoption decision
3. Comparison to existing alternatives (if any)
4. Key capabilities and limitations
5. Adoption conditions and trade-offs; do not turn a launch into compulsory deployment work
6. **Region availability**: Whether this new service is available in the admin's primary resource regions. Check the update text and doc search results for region information.

**Sections:**
- `affected_resources`: Use `[]` unless existing, evidenced resources are concrete adoption candidates. Never invent a deployment of the new service to justify its value.
- `action_items`: Optional single non-mutating fit check under the value-first rule; otherwise `[]`. Do NOT fabricate "try this service" actions.
- `impact_summary`: State only the dimension where this service would produce a concrete gain for a workload the administrator actually runs (most often `cost_impact` when it replaces a paid or self-operated component, or `operational_impact` when it removes work). All other dimensions MUST be empty strings. Never write "영향 없음" or "운영 변경 없음" — a brand-new service cannot affect existing operations, so its absence is not worth a sentence.
- `additional_checks`: Only when official feature-level evidence remains inconclusive, add one exact
    self-service Region check; keep the unconfirmed outcome visible in `one_line_summary`.""",
    "region_expansion": """#### CATEGORY: `region_expansion`
**Tone**: Brief, factual. "Now available closer to you."
**one_line_summary pattern**: "[Service] now available in [region(s)]"
**detailed_analysis structure**:
1. Which service expanded to which regions/AZs
2. Data residency or compliance benefits of the new region
3. Latency/DR improvements for workloads near the new region

**Sections:**
- `affected_resources`: **MUST be empty `[]`**.
- `action_items`: Optional single non-mutating fit check for a supported DR, residency, or latency requirement; otherwise `[]`. No existing deployment in the new region is required.
- `impact_summary`: `performance_impact` if latency improves for workloads the admin runs near the new region. `operational_impact` if it enables a DR or data-residency posture that was not available before. Every other dimension MUST be an empty string — do NOT write that there is no impact.""",
    "preview": """#### CATEGORY: `preview`
**Tone**: Forward-looking, informational. "Coming soon to Azure."
**one_line_summary pattern**: "[Feature/service] now in preview — [what it enables]"
**detailed_analysis structure**:
1. What the preview feature does
2. Why it matters — the workaround it would replace once adopted, and what that workaround costs today
3. Preview limitations (not for production, SLA, data guarantees)
4. Expected GA timeline if mentioned in the update
5. What advantages the preview feature offers over existing resources (cost, operations, performance, reliability, security)
6. **Region availability**: Whether this preview is available in the admin's primary resource regions. If region availability is unclear or limited, note this explicitly.

**Sections:**
- `affected_resources`: **OPTIONAL** — list existing resources that could be **replaced or improved** once this feature becomes GA, IF the preview feature is clearly superior in cost, operations, performance, reliability, or security. These help administrators plan ahead. Set `action_required` = false. Use `reason` with the actual queried property that shows the current state. If no clear advantage exists, use empty `[]`.
- `action_items`: Optional single non-mutating fit check under the value-first rule; otherwise `[]`. Never imply a preview requires production adoption.
- `impact_summary`: Only the dimension where this preview would produce a concrete gain once adopted (typically cost or security). Every other dimension MUST be an empty string. Never write that the preview has no impact or that skipping it carries no risk — that is true of every preview and tells the reader nothing.
- `additional_checks`: Only when official feature-level evidence remains inconclusive, add one exact
    self-service Region check; keep the unconfirmed outcome visible in `one_line_summary`.
- Set `relevance` from supported workload/requirement fit and evidence completeness, not ownership of the preview's service. Unresolved material fit is `unknown`, not automatic `not_relevant`.""",
    "sdk_tooling": """#### CATEGORY: `sdk_tooling`
**Tone**: Technical, developer-focused. "Toolchain update."
**one_line_summary pattern**: "[Tool] [version/feature] — [what changed]"
**detailed_analysis structure**:
1. What changed in the SDK/tool/API version
2. New capabilities or bug fixes
3. Breaking changes from previous version (if any)
4. Migration guide link (if applicable)

**Sections:**
- `affected_resources`: **MUST be empty `[]`** (SDK/tools are not Azure resources).
- `action_items`: Required for confirmed breaking/deprecated usage (classify as `feature_change`/`retirement`); for optional tooling gains, at most one grounded, non-mutating evaluation. Otherwise `[]`. Name known code/workflows in the task; keep `target_resources` empty when no Azure resources are involved.
- `impact_summary`: `operational_impact` only when the tooling change concretely improves a deployment or automation workflow the admin runs. All other dimensions empty — do not fill a dimension with an absence.""",
    "pricing": """#### CATEGORY: `pricing`
**Tone**: Cost-focused, analytical. "Here's what changes for your bill."
**one_line_summary pattern**: "[Service] [pricing change] — [estimated cost impact]"
**detailed_analysis structure**:
1. What pricing changed (new tier, SKU change, price increase/decrease)
2. Who is affected — which SKUs, tiers, or usage patterns
3. Cost comparison: before vs. after (if quantifiable)
4. How to optimize (migrate SKU, reserve capacity, etc.)

**Sections:**
- `affected_resources`: **OPTIONAL** — list resources on the affected SKU/tier if identifiable via Resource Graph.
- `action_items`: Only if SKU migration or reservation purchase is recommended.
- `impact_summary`: `cost_impact` is primary and should be specific (e.g., "~20% cost reduction by migrating from S0 to new Basic tier"). Other dimensions only if applicable.""",
}
