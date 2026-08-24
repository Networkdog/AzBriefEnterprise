"""Category-specific report templates.

Only the relevant category template is injected into REPORT_PROMPT,
saving ~8-9K tokens per report generation.
"""

CATEGORY_INTRO = """### Category-Specific Report Templates

Each category has a tailored report structure. Follow the template for the classified category.

---
"""

CATEGORY_TEMPLATES: dict[str, str] = {
    "retirement": """#### CATEGORY: `retirement`
**Tone**: Urgent, action-oriented. This is a "you must do something" report.
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
- `affected_resources`: **MANDATORY** — list ALL resources that must be changed. `reason` required — must include the actual Resource Graph property value that proves this resource is affected (e.g., 'nodeImageVersion: AKSUbuntu-2204gen2containerd — 지원 종료 대상'). `action_required` = true.
- `action_items`: **MANDATORY** — concrete migration steps with the retirement date as `deadline`.
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
- `affected_resources`: **MANDATORY** — resources whose behavior may change. `reason` must include the actual queried property value (e.g., 'nodeImageVersion: AKSUbuntu-2204gen2containerd — 기본값 변경 영향', 'minimumTlsVersion: TLS1_0 — 차단 예정').
- `action_items`: **MANDATORY** — verification and remediation steps. Step 1 should always be "verify impact" before making changes.
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
- `action_items`: Depends on `relevance`. If `relevance` = `opportunity` (a feature that benefits named existing resources), you MUST include **exactly one** scoped evaluation action per the "Opportunity must never be a dead-end" rule — name the real candidate resources and the real go/no-go criteria, with an empty `deadline`. If `relevance` = `not_relevant` (the admin owns nothing that benefits), use empty `[]`. Do NOT fabricate urgent migration steps, CLI commands, or deadlines for a GA feature — the single evaluation action is the ceiling unless the update states explicit opt-in steps.
- `impact_summary`: This is an **opportunity summary, not an impact assessment**. Each dimension states what the administrator GAINS by adopting:
  - `cost_impact`: the saving or cost-optimization this unlocks
  - `security_impact`: the security posture improvement available
  - `performance_impact`: the performance or reliability gain possible
  - `operational_impact`: the operational work that disappears — a component no longer operated, a manual step automated, a standard that can be enforced
  Leave a dimension as an empty string when there is no concrete gain. An empty string is CORRECT and is required instead of "영향 없음" / "운영 변경 없음" / "도입하지 않아도 리스크 없음" — a new feature never changes existing behaviour, so stating its absence is a forbidden tautology.
- `additional_checks`: If region availability for the admin's primary regions cannot be confirmed from the update or doc search results, add: "[Feature name]의 [primary region(s)] 리전 지원 여부를 확인해야 합니다." (translate per report language).""",
    "new_service": """#### CATEGORY: `new_service`
**Tone**: Educational, advisory. "Here's a new tool in your toolbox."
**one_line_summary pattern**: "[Service name] now GA — [primary use case in one phrase]"
**detailed_analysis structure**:
1. What the service does, and how the same job was handled without it (a self-operated stack, a third-party product, a manual process) — that contrast IS the problem statement
2. Target audience — which teams or workloads benefit most, and which operational responsibility (network / security / cost / platform / data / application) would own the adoption decision
3. Comparison to existing alternatives (if any)
4. Key capabilities and limitations
5. How to get started (but NOT as an action item)
6. **Region availability**: Whether this new service is available in the admin's primary resource regions. Check the update text and doc search results for region information.

**Sections:**
- `affected_resources`: **MUST be empty `[]`**. New service = no existing resources.
- `action_items`: **MUST be empty `[]`**. Do NOT fabricate "try this service" actions.
- `impact_summary`: State only the dimension where this service would produce a concrete gain for a workload the administrator actually runs (most often `cost_impact` when it replaces a paid or self-operated component, or `operational_impact` when it removes work). All other dimensions MUST be empty strings. Never write "영향 없음" or "운영 변경 없음" — a brand-new service cannot affect existing operations, so its absence is not worth a sentence.
- `additional_checks`: If region availability for the admin's primary regions cannot be confirmed, add the region verification check item.""",
    "region_expansion": """#### CATEGORY: `region_expansion`
**Tone**: Brief, factual. "Now available closer to you."
**one_line_summary pattern**: "[Service] now available in [region(s)]"
**detailed_analysis structure**:
1. Which service expanded to which regions/AZs
2. Data residency or compliance benefits of the new region
3. Latency/DR improvements for workloads near the new region

**Sections:**
- `affected_resources`: **MUST be empty `[]`**.
- `action_items`: **MUST be empty `[]`**.
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
- `action_items`: **MUST be empty `[]`**.
- `impact_summary`: Only the dimension where this preview would produce a concrete gain once adopted (typically cost or security). Every other dimension MUST be an empty string. Never write that the preview has no impact or that skipping it carries no risk — that is true of every preview and tells the reader nothing.
- `additional_checks`: If region availability for the admin's primary regions cannot be confirmed, add the region verification check item.
- Set `relevance` to `opportunity` if the feature is relevant to the admin's service stack, `not_relevant` otherwise.""",
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
- `action_items`: Only if old version is being deprecated and migration is needed. Otherwise empty `[]`.
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
