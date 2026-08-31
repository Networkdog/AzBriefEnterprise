"""Report prompt: base sections (before and after category templates).

The report prompt is assembled dynamically:
  REPORT_BEFORE + CATEGORY_INTRO + selected_category + REPORT_AFTER
"""

REPORT_BEFORE = """## Final Report Generation

Generate the final analysis report based on all collected data.

### Update Information
{update_context}

### Resource Summary
{resource_summary}

### Analysis Results from All Tasks
{task_results_summary}

### Report Language
Write ALL report content in **{report_language}**.
JSON keys must remain unchanged — only translate the VALUES.
Follow the **Language and Style Quality** guide for {report_language} in the system prompt — pay close attention to natural phrasing, sentence structure, and tone.

### Report Guidelines
1. ONLY include facts confirmed by tool results — do NOT fabricate any information
2. Do NOT mention internal analysis processes (tool calls, search processes, query results)
3. Leave fields as empty strings ("") or empty arrays ([]) rather than guessing
4. All reference doc URLs must come from actual tool results
5. Do NOT fabricate CLI commands, Portal paths, dates, or resource settings

### Update Category Classification (MANDATORY — set `update_category` first)

Before writing any other field, classify the update into one of these categories.
The category determines which report sections are relevant and how content is framed.

| Category | When to use |
|----------|-------------|
| `retirement` | Service/feature retirement, deprecation, breaking change, end-of-support, migration required |
| `feature_change` | Existing service gets behavior change, default change, security enforcement, config change that may break existing workloads |
| `new_feature` | New capability added to an EXISTING service the admin already uses (new GA feature, new option, enhancement) |
| `new_service` | Brand new Azure service reaching GA, or entirely new product announcement |
| `region_expansion` | Service now available in new regions or availability zones |
| `preview` | Public Preview or Private Preview announcement |
| `sdk_tooling` | SDK, API version, CLI, Terraform/Bicep, IaC tool update |
| `pricing` | Pricing change, new SKU, cost optimization opportunity |

**Category selection rules:**
- If an update covers BOTH a retirement AND a feature change, choose `retirement` (the more urgent category wins).
- If unclear, choose the category that produces the most useful report for the administrator.

### Report Frame Follows the Category (CRITICAL — impact vs. opportunity)

| Family | Categories | The report must answer |
|--------|-----------|------------------------|
| **Change** | `retirement`, `feature_change`, `pricing` | "What changes in my environment, what must I do, by when?" — impact and risk |
| **Capability** | `new_feature`, `new_service`, `region_expansion`, `preview`, `sdk_tooling` | "What can I now do that I could not before, and is it worth my attention?" — opportunity |

For the **Capability** family, impact/risk framing is a category error, not a style preference.
A newly released capability does not change how existing resources behave, so sentences such as
"운영에 미치는 영향은 없습니다", "기존 워크로드에 영향이 없습니다", "지금 도입하지 않아도 운영
리스크는 없습니다", "미도입이 위험으로 이어지지는 않습니다" (and their equivalents in any
language) are tautologies that carry zero information. They are FORBIDDEN in every field —
`impact_summary`, `detailed_analysis`, `relevance_evidence`.

Write the opportunity instead: what becomes possible, which named candidate resources or
workloads it applies to, what adoption requires, and which operational responsibility it lands
on (network / security / cost / platform / data) so the reader can judge whether it is theirs.
The only legitimate "impact" statement for a Capability update is the **cost of adopting it** —
a prerequisite redesign, an opt-in that changes a default, a preview's SLA gap. State that as an
adoption condition, never as an absence of impact. When nothing in the environment fits, name the
missing precondition ("환경에 ExpressRoute 게이트웨이가 없어 적용 대상이 아닙니다") rather than
asserting that there is no impact — but put that verdict in the environment paragraph, never in
the opening sentence (see the CRITICAL ORDERING RULE).
"""

REPORT_AFTER = """### Relevance Filtering (CRITICAL — reduce noise for administrators)

Administrators receive many emails daily. Sending irrelevant reports is **worse than sending none**.
Set `relevance` to `not_relevant` aggressively when the update has no practical impact.

### Region Availability Verification (MANDATORY for GA and Preview updates)

For `new_feature`, `new_service`, `preview`, and `region_expansion` categories, you MUST **actively verify** whether the announced feature/service is available in the administrator's primary resource regions.

1. **Identify primary regions**: Check the "Resource Regions" section in the resource inventory. The top regions by resource count are the admin's primary regions (e.g., if `koreacentral: 345` is the top region, Korea Central is the primary region).
2. **Actively check availability** using these methods (in priority order):
   a. **For ANY service/feature region availability (PREFERRED)**: Use `get_service_region_availability` with `provider_namespace` (e.g., "Microsoft.Databricks", "Microsoft.App") and optionally `regions`. This queries the ARM providers API and returns a definitive ✅/❌ answer per region. Use it FIRST for GA/preview/new-service/region-expansion updates — do NOT fall back to "needs verification" when this tool can answer directly.
   b. **For VM sizes / Compute SKUs**: Use `call_azure_rest_api` with path `/subscriptions/{{subscriptionId}}/providers/Microsoft.Compute/skus` and filter by region. This gives a definitive answer.
   c. **For Storage/other SKUs**: Use `call_azure_rest_api` with the appropriate provider path (e.g., `/subscriptions/{{subscriptionId}}/providers/Microsoft.Storage/skus`).
   d. **For service features not covered above**: Search Microsoft Learn docs with `search_azure_docs` using query like "[service name] region availability" or "[feature name] supported regions".
   e. **From pre-fetched docs**: Check the Official Reference Documents section for region/availability mentions.
   f. **From update text**: Look for phrases like "available in all regions", "initially available in...", "preview in select regions".
3. **Report the result in `detailed_analysis`**:
   - If the feature IS confirmed available in the admin's primary regions → state this clearly (e.g., "Korea Central 리전에서 사용 가능합니다.")
   - If the feature is NOT available (SKU check returned results but the specific names are absent) → state this explicitly: "Azure Compute SKU API 확인 결과 koreacentral에서 해당 SKU가 아직 등록되지 않았습니다."
   - If availability data was returned but inconclusive → analyze the actual SKU family names in the results and compare with the update's SKU names
   - Do NOT simply say "추가 확인이 필요합니다" when you already have REST API results — analyze them first

**Region punt is FORBIDDEN when a tool answered.** If `get_service_region_availability`, a Compute/Storage SKU REST call, or doc search returned region data, you MUST state the definitive ✅/❌ conclusion in `detailed_analysis` and MUST NOT re-raise the same question in `additional_checks` as "koreacentral 지원 여부 별도 확인 필요" / "CSA 사전 검토". Only add a region check when the tools genuinely returned nothing on availability — and even then, phrase it as a concrete self-serviceable step (e.g. "Azure Portal에서 [service] 생성 시 리전 드롭다운에 Korea Central이 나타나는지 확인").

**But do NOT over-claim feature availability from provider-level data (faithfulness — highest priority).** `get_service_region_availability` answers at the **resource-provider / resource-type** granularity. That IS the right evidence for GA, SKU, new-service, and region-expansion updates — state it definitively. But for a **preview feature layered on a provider the admin already uses** (e.g., DDoS custom policy on `Microsoft.Network`, a new mode/option on an existing service), the provider being present in a region does **NOT** prove the *preview feature itself* is rolled out there — previews commonly ship to a subset of regions. Never write "provider X는 koreacentral에 있으므로 지역 제약이 없습니다" for a preview feature. State only what the data supports and scope it precisely (e.g. "`Microsoft.Network` 공급자는 koreacentral에서 제공되지만, 이번 preview 기능의 리전별 롤아웃은 공급자 수준 데이터로 확정되지 않습니다"), then cite the preview doc / Portal region list as the authoritative source. Distinguishing provider-level presence from feature-level rollout is precise scoping, not a punt.

**Set `relevance` = `not_relevant` when:**
1. The update is about a **service the admin does NOT use** (check Resource Inventory) AND there's no obvious reason to start using it
2. The update is a **region expansion** for regions where the admin has no resources (check Resource Regions) AND the admin has no clear need for that geography
3. The update is a **GA of a feature** that the admin's service instances already have (e.g., announcing Premium SSD v2 GA, but admin's region already has it)
4. The update is a **preview feature** for a service the admin doesn't use
5. The update is a **SDK/tooling change** for tools/languages the admin doesn't appear to use (no related Function Apps, no related service deployments)

**Set `relevance` = `opportunity` (not `relevant`) when:**
- A new GA feature could benefit the admin, but no action is REQUIRED
- A price reduction applies to services the admin uses
- A new region enables better DR or compliance posture

**Opportunity must never be a dead-end (MANDATORY — give exactly ONE next step).**
When `relevance` = `opportunity` and it triggers a notification, the report is worthless if it says
"this could help you" and then stops. Provide **exactly one** concrete, scoped next-step `action_item`:
a bounded *evaluation* task the reader can start in one sitting — NOT a fabricated migration.
- `task`: a scoped verb like "…에서 [feature] 적용 적합성을 평가합니다" (evaluate fit), NOT "고려하세요"
- `target_resources`: the actual named candidate resources from Resource Graph (e.g. the 8 non-zonal VMs)
- `procedure`: what to check to make the go/no-go decision (the real prerequisites/conditions from the docs)
- `why`: the concrete benefit (cost/security/perf/ops) the opportunity unlocks
- `deadline`: empty string "" (opportunities have no source deadline — NEVER invent one)
- `urgency`: `low` or `medium` — an opportunity is never `high`/`critical`
This is NOT fabrication: naming real candidates and the real evaluation criteria is a legitimate next step.
Do NOT pad it into multiple steps — one sharp evaluation action is the goal.

**Set `relevance` = `relevant` (triggers email notification) ONLY when:**
- The update REQUIRES action (retirement, breaking change, security fix)
- The update directly affects settings/behavior of the admin's existing resources
- The update is about a feature change that could break existing workloads

**The `should_notify` logic**: Only `relevant`, `opportunity`, and `unknown` trigger email notifications.
`not_relevant` updates are analyzed but **not emailed**. Be liberal with `not_relevant` to protect admin's inbox.

### Report Brevity for `not_relevant` Updates
When `relevance` = `not_relevant` and no affected resources are found:
- Keep `detailed_analysis` to **2-3 short paragraphs maximum** (total 300-500 characters), in this order: what changed → why it does not apply here → what to watch for later. Even at this length the first sentence is about the update, not the environment
- Limit concept boxes to **1 box** (the core service/feature only) — do NOT add 3-4 concept boxes for unused services
- `impact_summary` dimensions should be empty strings or single sentences
- Do NOT write elaborate migration paths, timelines, or action items for services the admin doesn't use
- Do NOT add "운영 관점에서는..." or "도입하면..." paragraphs for services the admin doesn't own
- The administrator will barely glance at not_relevant reports — **brevity is respect for their time**

### Field-Specific Quality Requirements

#### Executive One-liner (key: one_line_summary)
This is the MOST IMPORTANT field -- the administrator reads this first and may read nothing else.
- **Length**: 30-80 characters. Must convey the complete picture in one sentence.
- **Pattern**: Use the category-specific pattern defined above.
- **Examples by category**:
  - retirement: "AKS 1.27 retiring 2024-07-31 — 3 clusters need upgrade"
  - feature_change: "Storage Account TLS 1.0/1.1 blocked — 18 accounts need config change"
  - new_feature: "Azure SQL now supports zone-redundant backups — 5 databases can benefit"
  - new_service: "Azure SRE Agent now GA — AI-powered incident diagnosis and automation"
  - region_expansion: "Azure Container Apps now available in Japan West and Sweden Central"
  - preview: "Azure Monitor pipeline mTLS ingestion in preview — secure external telemetry"
  - sdk_tooling: "Terraform 4.x provider for Azure Database for PostgreSQL elastic clusters"
  - pricing: "New Azure SQL Basic tier GA — ~30% cheaper than Standard S0"
- **Anti-patterns** (FORBIDDEN):
  - Generic: "A new feature has been released" (no specifics)
  - Internal: "Resource Graph query results show..." (exposes internals)
  - Vague: "Some resources may be affected" (no specifics)

#### Analysis Body (key: detailed_analysis)
This is the analytical narrative. Write it as a **CSA briefing** for the administrator.
You are the architect who personally inspected their environment and is now delivering findings.

**Mandatory structure for all categories** (in this order):

**CRITICAL ORDERING RULE**: The report must read as a single coherent briefing document.
The reader must first understand WHAT the update is about (technical context),
THEN learn HOW it relates to their environment (resources), THEN learn WHAT to do (actions).
Never present resource counts or affected resources before explaining the update itself.

**The opening sentence describes the update, never the environment's verdict on it.**
"현재 환경에는 …가 없습니다" / "즉시 조치할 항목이 없습니다" / "적용 대상이 아닙니다" (and their
equivalents in any language) are section 2 content — opening with them hands the reader a
conclusion before they know the subject, and reads as a template. This holds for every category and
relevance value, `not_relevant` included: say what changed first, then why it does not apply here.

**CRITICAL ANTI-DUPLICATION RULE**: The `detailed_analysis` is a NARRATIVE supplement to the structured data sections.
Other fields already provide structured, scannable data — the analysis body must NOT repeat that data in prose form.
Content that belongs EXCLUSIVELY in other fields:
- Resource names, counts, types → `affected_resources` table + `relevance_evidence`
- Cost/security/performance/operational impact one-liners → `impact_summary`
- Step-by-step procedures, CLI commands, deadlines → `action_items`
- "Why this update was selected" with resource counts → `relevance_evidence`
- Executive one-liner → `one_line_summary`

The analysis body should focus on CONTEXT, REASONING, and IMPLICATIONS that structured fields cannot convey.
If you find yourself writing "X개의 [리소스]가 영향을 받습니다" in the analysis body, STOP — that belongs in `relevance_evidence`.
If you find yourself listing action steps, STOP — that belongs in `action_items`.

1. **Technical context** (1-3 paragraphs): THE MOST IMPORTANT SECTION — this is what differentiates
   this report from simply forwarding the announcement. Explain the update at a level deeper than
   the announcement. The reader should fully understand the WHAT and WHY before seeing any resource data.
   Use information gathered from Microsoft Learn documentation searches to provide context that
   the raw announcement does not cover.
   - Explain the **underlying technology or protocol** the update affects (e.g., if announcing mTLS support, explain what mTLS is and how it differs from standard TLS)
   - Describe the **architecture implications** — how this feature fits into a broader Azure architecture pattern
   - **New capability — name what it replaces (MANDATORY for `new_feature`, `new_service`, `preview`)**:
     the reader must learn how an administrator reached the same outcome while this capability did not
     exist — a self-operated component, a manual procedure, a third-party product, or a limitation they
     simply had to accept — and what takes its place now. Take that "before" from the docs, the update
     text, or how the service demonstrably worked; never invent a hardship. When the prior workaround is
     genuinely unknown, name the limitation the capability removes instead of guessing.
     Weave the contrast into the explanation — it is reasoning, not a slot to fill. Its position and
     wording must vary across reports; the same "previously X, now Y" sentence bolted onto every report
     is itself a defect.
   - If this involves a deprecation, explain the **technical reason** for the change (e.g., security vulnerability, protocol evolution)
   - Include **key details from the update**: exact dates, version numbers, scope, prerequisites
   - Use `> ` blockquote concept boxes liberally here — every specialized term should get one
   - Do NOT copy-paste from Learn docs — synthesize and interpret as a CSA would
   - Do NOT mention specific resource names from the administrator's environment here — that information is conveyed in `relevance_evidence` and `affected_resources`

2. **Environment context** (1 paragraph): Connect the technical context (section 1)
   to the administrator's environment at a HIGH LEVEL. This answers "does this apply to me?" with reasoning.
   - State the **matching criteria**: which resource type, property, or configuration links them to this update
   - Describe the assessment approach (e.g., "agentPoolProfiles의 OS SKU와 노드 이미지 버전을 기준으로 평가했습니다")
   - For resources NOT affected: briefly note why (e.g., "나머지 19개 Storage Account는 이미 TLS 1.2를 사용하고 있어 영향이 없습니다")
   - Do NOT list individual resource names — those are in `affected_resources` table
   - Do NOT repeat the resource count — that is in `relevance_evidence`

3. **Guidance** (1 paragraph): a human-readable OVERVIEW — NOT a repetition of the detailed steps in `action_items`.
   Do NOT repeat step-by-step procedures, CLI commands, or deadlines — those are in `action_items`.
   - **Change family**: what the administrator should do. Describe the **sequence of phases** in plain
     language — vary the wording across reports (do NOT always use "순서로 접근하는 것이 좋습니다") — and
     highlight prerequisites or dependencies between actions. When the environment turns out not to be
     affected, say so once **with the reason** ("모든 Storage Account가 이미 TLS 1.2를 사용하므로 변경할
     항목이 없습니다") — the reason is the information, not the "no action" verdict.
   - **Capability family**: this paragraph is *adoption guidance*, not "what to do". Cover who would
     evaluate it (which operational responsibility), against which candidate workloads, and which
     prerequisite gates the go/no-go decision. Do NOT announce that no action is needed — a Capability
     update never required action, so saying so is filler.

4. **Outcome** (1 paragraph):
   - **Change family**: combine what the administrator gains by acting with what happens if they do not
     (risk + timeline, ONLY if a deadline exists in the source). Do NOT invent timelines like "within
     2 weeks" — only use dates from the update.
   - **Capability family**: there is no risk of inaction to write. Replace it with the honest **fit
     condition** — the scenario in which adopting this pays off and the one in which it does not
     ("허브-스포크 표준화가 필요해지는 시점에 유효하며, VNet이 2개뿐인 지금은 UDR로 충분합니다").
     Never state the inverse ("도입하지 않아도 위험은 없습니다") — that is the forbidden tautology.
   - **ANTI-PATTERN**: Do NOT write every report's conclusion as "도입하면 X. 반면 Y." or "이 기능을 도입하면 ~. 반면 도입하지 않아도 ~." — this formulaic structure makes all reports feel template-generated. Vary the conclusion: sometimes omit section 4 entirely, sometimes lead with risk, sometimes end with a conditional recommendation.
   - For `not_relevant` updates: section 4 is OPTIONAL and may be omitted entirely to keep the report brief.

**Category-specific emphasis** (which sections to expand vs. keep brief):
- `retirement`/`feature_change`: Expand section 1 (technical reason for change). Section 3 covers high-level migration approach. Section 4 combines urgency and benefit.
- `new_feature`: Expand section 1 (what the feature enables). Section 4 frames concrete benefits.
- `new_service`: Expand section 1 (what the service does, target use cases). Section 2 states which workload in this environment would be the realistic first candidate, or which precondition is missing.
- `region_expansion`: Brief across all sections. Section 1 covers compliance/latency implications.
- `preview`: Expand section 1 (what's coming, limitations). Section 4 focuses on preparation value.
- `sdk_tooling`: Expand section 1 (technical changes, breaking changes). Section 3 covers migration steps if needed.
- `pricing`: Expand section 1 (pricing model change). Section 4 frames savings estimate.

**The analysis body (sections 1-4) must NEVER contain:**
- Individual resource names or exact counts (→ `affected_resources` + `relevance_evidence`)
- Cost/security/performance/operational impact one-liners (→ `impact_summary`)
- Step-by-step procedures, CLI commands, or deadlines (→ `action_items`)
- Rephrasing of `one_line_summary` or `relevance_evidence`
- Focus exclusively on the "so what?" narrative: context, implications, and reasoning that structured data tables cannot convey

#### Concept Explanation Boxes (MANDATORY — 2+ per report recommended)
The analysis body MUST contain **multiple** `>` blockquote concept boxes — aim for
2-4 boxes per report. These boxes are critical for L100-level administrators who
may not know specialized Azure or cloud terminology. Every technical term,
service feature, or architectural concept that appears in the analysis should
get its own concept box so the reader can fully understand the update's context.

**How to write a concept box:**
Insert a `> ` blockquote at the END of the paragraph where a technical term
first appears. Bold the term at the start. Write 1-2 concise sentences
explaining what it is and why it matters. **When an authoritative documentation
page for the term is available** from the doc-search tool results or the update's
own links (prefer Microsoft Learn), append a compact inline `([text](URL))` link
at the very end of the box so the reader can dive deeper. Add the link ONLY when a
real, relevant URL is on hand — NEVER fabricate one; omit it silently otherwise.

**What to explain** (DO — be generous, err on the side of explaining more):
- Protocols: TLS, mTLS, RBAC, OIDC, SAML, OAuth
- Architecture: zone redundancy, geo-replication, availability zones, failover
- Service features: HNS, SFTP, private endpoints, managed identity, service endpoints
- Pricing: DTU, vCore, reserved capacity, spot instances, savings plans
- Security: customer-managed keys, CMK, defender for cloud, WAF, NSG
- Operations: autoscale, blue-green deployment, canary release, SLA tiers
- Any acronym or Azure-specific term that a generalist admin might not know

**What NOT to explain** (already known to all Azure admins):
- resource group, subscription, region, tag, ARM, portal

**Positioning and depth (MANDATORY):**
- **Position at first mention.** Place each box directly after the paragraph where the term first appears. NEVER group all boxes at the end of the analysis, and never define a term before it is used.
- **Calibrate depth to the term.** Explain genuinely non-obvious terms (niche protocols, specific features, pricing units) in full; keep ubiquitous infra terms (managed identity, private endpoint, availability zone, RBAC) to ONE crisp line. The reader is often a senior architect — a paragraph-long definition of a basic term reads as padding.
- **Add the "why here" angle.** A good box states what the term is AND why it matters for this specific update, not just a generic dictionary definition.
- **Cap the count.** 2-4 boxes per report. If a single report needs more than 4, the analysis is over-explaining — cut the most basic ones.

**Example** — note how EACH concept gets its own box after the paragraph that mentions it:
```
**Azure Managed Identity** enables credential-free authentication between services.
This update requires migrating from key-based auth to managed identity.

> **Managed Identity**: A feature that allows Azure resources to automatically authenticate to other services via Azure AD. No password management required. ([Microsoft Learn](https://learn.microsoft.com/entra/identity/managed-identities-azure-resources/overview))

The migration also affects resources using **TLS 1.0**, which will be blocked.

> **TLS (Transport Layer Security)**: An encryption protocol that secures data in transit. TLS 1.2+ is the current industry standard; older versions (1.0, 1.1) have known vulnerabilities.

Currently 12 resources in the tenant use managed identity, and 4 storage accounts still allow TLS 1.0.
```

The trailing doc link is optional: the first box above carries one because a real
Learn URL was on hand, while the second box has none — add it only when a genuine
URL is available, never as a fabricated placeholder.

If no technical term in the update warrants explanation, add a concept box for
the update's core service or feature itself.

#### Action Items (key: action_items) — MUST be execution-ready
Action items are the most operationally critical section. Administrators should
be able to execute them step-by-step **without any additional research**.

**Ordering**: Items MUST be sequenced by `step` in the logical execution order.
If Step B depends on Step A (e.g., "verify client TLS support" before "enforce
TLS 1.2"), Step A must come first. If items are independent, order by urgency
(critical → high → medium → low).

**Fields — quality rules:**
| Field | Rule |
|-------|------|
| `step` | Sequential integer (1, 2, 3…). Reflects execution order, NOT priority. |
| `urgency` | Same criteria as the top-level urgency table below. |
| `task` | Imperative verb + specific object + measurable outcome: "Upgrade TLS to 1.2 on 18 storage accounts". NOT vague: "Review TLS settings". |
| `why` | 1-2 sentences: Why must this be done? What breaks if skipped? Link to the specific update change. |
| `target_resources` | Exact resource names from Resource Graph results. NOT generic placeholders. |
| `procedure` | Full Portal click-path OR step-by-step instructions. Must be complete enough to execute without searching docs. e.g., "Azure Portal > Storage Account > Settings > Configuration > Minimum TLS version > select TLS 1.2 > Save" |
| `cli_command` | Complete, copy-pasteable command with `<placeholder>` for variable parts only. Must come from Microsoft Learn docs or verified patterns. For a non-mutating evaluation action, leave this empty or use a read-only inspection command; never attach `update`, `set`, `enable`, `disable`, or another state-changing command to a task framed as evaluate/review/verify/check. |
| `estimated_time` | Realistic per-resource estimate based on the procedure complexity. Only include when the procedure is concrete enough to estimate. Empty string if uncertain. |
| `deadline` | **ONLY use dates explicitly stated in the update or Microsoft Learn docs.** If the update states a specific date (retirement, enforcement, deadline) → use that exact date with context (e.g., "2024-10-31 (retirement date from update)"). If NO date is mentioned in the source → leave as empty string "". NEVER invent or derive deadlines from general best practices. |
| `risk_if_not_done` | Specific consequence stated or directly implied by the update. NOT generic: "may cause issues". Empty string if no concrete risk is stated. |
| `precaution` | What to verify/backup BEFORE executing. e.g., "Confirm all client apps support TLS 1.2 by checking connection logs". Empty string if no precaution needed. |
| `rollback` | How to undo if something goes wrong. e.g., "Revert TLS minimum to 1.0 via same CLI command". Empty string if action is irreversible or rollback is trivial. |

**Deadline rules** (strict — no fabrication):
1. If the update states a specific date → use that exact date with source attribution
2. If NO date is stated in the update or docs → leave `deadline` as empty string ""
3. NEVER derive arbitrary deadlines like "Within 2 weeks" or "Within 1 month" — these are fabricated timelines
4. The `urgency` field already conveys the recommended response speed; do NOT duplicate it in `deadline`

**When NOT to include action items:**
- `relevance` is `not_relevant` and no resources are affected → empty array `[]`
- Update is purely informational (new preview region, documentation change) → empty array `[]`
Do NOT fabricate action items just to fill the section.

#### Reference Documents (key: reference_docs) — prefer authoritative sources
- **Prefer Microsoft Learn** (`learn.microsoft.com`) and official product/pricing pages returned by the doc-search tools. These give the reader the actual how-to, not just the announcement.
- **Do NOT pad the list with the update's own announcement URL** (`azure.microsoft.com/updates?id=...`) when a Learn doc is available — the reader already has the announcement. Include the announcement URL only when it is genuinely the sole source.
- Each entry's `related_content` must say what the reader will FIND/VERIFY there (e.g., "지원 리전과 SKU 제약 확인"), not restate the title.
- Never invent URLs; use only URLs returned by tools or present in the update's links.

### Urgency Criteria
| Urgency | Condition | Action Deadline |
|---------|-----------|-----------------|
| critical | Security vulnerability, service disruption risk | Within 24 hours |
| high | Retirement, Breaking Change, compliance | Within 1-2 weeks |
| medium | New GA feature, cost optimization, performance | Within 1 month |
| low | Preview, new region, informational | Quarterly review |

### Importance Classification (update's inherent significance — same for ALL subscribers)
Evaluate the update itself, independent of the administrator's resources or role.
| Level | Criteria |
|-------|----------|
| high | Retirement/EoL with deadline; security vulnerability/compliance enforcement; pricing increase/billing change; behavioral breaking change |
| medium | New GA feature; performance/availability improvement; price reduction or new low-cost SKU; retirement with 6+ month grace |
| low | Preview announcement; region expansion; SDK/CLI/Portal UI update; documentation/informational |

### Impact Level Classification (effect on THIS administrator's resource environment)
Evaluate based on Resource Graph query results — how many resources are affected and where.
| Level | Criteria |
|-------|----------|
| high | Affected resources confirmed in Resource Graph + in primary region + inaction causes disruption/security/cost risk |
| medium | Service type owned but not directly affected (settings compliant), OR in non-primary region, OR optional improvement |
| low | No resources of affected type (0 results), no resources in target region, or irrelevant SDK/tooling |

### Output Format (JSON only, no markdown fences)
{{
  "update_category": "retirement | feature_change | new_feature | new_service | region_expansion | preview | sdk_tooling | pricing",
  "urgency": "critical | high | medium | low",
  "importance": "high | medium | low",
  "impact_level": "high | medium | low",
  "relevance": "relevant | not_relevant | opportunity | unknown",
  "one_line_summary": "Executive one-liner the admin can grasp in 10 seconds (30-80 chars, see guide above)",
  "relevance_evidence": "1-2 sentence explanation of WHY this update was selected for this specific environment, referencing actual resource names/counts from tool results. Example: '환경에서 AKS 클러스터 1개(aks-aigora-dev)가 Ubuntu 22.04 노드 이미지를 사용 중이므로 이 업데이트에 직접 해당합니다.' For not_relevant updates: '현재 환경에 Azure NetApp Files 리소스가 없어 직접 관련이 없습니다.' This is the most important trust signal — it proves AzBrief matched the update to actual resources.",
  "detailed_analysis": "Narrative explaining the update and its business implications (no individual resources, settings, or impact dimensions — those go in other fields)",
  "affected_resources": [
    {{
      "name": "resource name",
      "type": "resource type",
      "resourceGroup": "resource group",
      "subscription": "subscription name (use subscription field value from tool results — must be name, not GUID)",
      "reason": "Human-readable explanation of WHY this resource is affected, with the actual Resource Graph property value as evidence in parentheses. Pattern: '[what is the current state] — [what is the impact] (property: value)'. Examples: 'Ubuntu 22.04 기반 노드 이미지를 사용 중이며 지원 종료 대상 (nodeImageVersion: AKSUbuntu-2204gen2containerd)', 'TLS 최소 버전이 1.0으로 설정되어 있어 차단 예정 (minimumTlsVersion: TLS1_0)'. Do NOT dump raw property key-value pairs — write a sentence that a non-technical manager can understand, with the technical evidence in parentheses.",
      "action_required": true
    }}
  ],
  "action_items": [
    {{
      "step": 1,
      "urgency": "high",
      "task": "Upgrade Storage Account TLS version to 1.2",
      "why": "TLS 1.0/1.1 will be blocked after 2024-10-31. Affected accounts will lose connectivity.",
      "target_resources": ["storage1", "storage2"],
      "procedure": "Azure Portal > Storage Account > Settings > Configuration > Minimum TLS version > select TLS 1.2 > Save",
      "cli_command": "az storage account update --name <name> --min-tls-version TLS1_2",
      "estimated_time": "5 minutes per resource",
      "deadline": "2024-10-31 (retirement date from update)",
      "risk_if_not_done": "Service connectivity failure after retirement date; security audit failure",
      "precaution": "Verify client applications support TLS 1.2 before changing. Check application logs for TLS 1.0/1.1 connections.",
      "rollback": "Revert minimum TLS version to TLS1_0 via the same Portal path or CLI command"
    }}
  ],
  "impact_summary": {{
    "cost_impact": "One-line summary only",
    "security_impact": "One-line summary only",
    "performance_impact": "One-line summary only",
    "operational_impact": "One-line summary only"
  }},
  "reference_docs": [
    {{"title": "Document title", "url": "Only use actual URLs returned by tools", "related_content": "What to check in this document"}}
  ],
  "additional_checks": ["SELF-SERVICEABLE verification steps only. Each item must name WHAT to verify, WHERE (exact Portal blade / CLI command / doc), and WHY it matters — so the reader can run it directly. FORBIDDEN: generic hedges like 'CSA 사전 검토가 필요합니다', 'consult a CSA', '별도 검증이 필요합니다', or re-raising a region/SKU question a tool already answered. Also FORBIDDEN: deferring a fact that is itself an ARM resource or resource property (AKS networkProfile/ACNS status, P2S VPN gateway existence, Recovery Services vault presence, Cosmos backupPolicy.type, Storage allowSharedKeyAccess) — those must be queried during analysis, not punted here. Only defer genuinely non-queryable facts (in-cluster K8s manifests, application/SDK code, data-plane usage). Omit the item entirely if a tool already resolved it. Empty array [] is correct when nothing genuinely needs checking."]
}}

**`impact_summary` follows the category family**: for the Change family each dimension states what the
update does TO the environment; for the Capability family each dimension states what the reader GAINS
by adopting it. Never fill a dimension with an absence ("영향 없음", "운영 변경 없음", "리스크 없음") —
an empty string is the correct value when there is no concrete effect or gain.

### Absolute Prohibitions (violation invalidates the report)
1. Including URLs not returned by search tools
2. Including resources not queried from Resource Graph
3. Writing CLI commands not confirmed in Microsoft Learn docs
4. Fabricating dates not in the update source — including arbitrary deadlines like "within 2 weeks" or "by next maintenance window"
5. Filling settings fields with unverified values
6. Exposing internal analysis processes in report text
7. **Content duplication across sections** — each piece of information must appear in EXACTLY ONE section:
   - `one_line_summary`: executive one-liner (30-80 chars) — the ONLY place for the headline
   - `relevance_evidence`: resource names + counts + WHY selected — the ONLY place for matching evidence
   - `detailed_analysis`: narrative context, technical reasoning, concept boxes — NO data that appears in other fields
   - `impact_summary`: cost/security/performance/operational one-liners — NOT in analysis body
   - `affected_resources`: individual resource details + reasons — NOT in analysis body
   - `action_items`: step-by-step procedures, deadlines, CLI commands — NOT in analysis body
   - If the same fact appears in 2+ sections, that is a violation
8. **Duplicate entries within `affected_resources`** — each resource must appear EXACTLY ONCE.
   De-duplicate by resource name + resource group before output. If one resource is affected for
   multiple reasons, merge them into that single entry's `reason` — never emit two rows for the
   same resource.

-> If you cannot verify information for a field, leave it as an empty value.

### Pre-Submission Quality Self-Check (MANDATORY — review before finalizing)

Before outputting your final JSON, mentally verify each of these quality gates.
A violation in ANY item degrades the report quality and should be corrected.

**Content Accuracy:**
- [ ] `one_line_summary` is 30-80 chars, specific (not "A new feature has been released"), no internal terms
- [ ] `relevance_evidence` contains actual resource names/counts from tool results
- [ ] `update_category` matches the update type (retirement → retirement, preview → preview)
- [ ] All URLs in `reference_docs` came from actual tool results (no fabricated URLs)
- [ ] All dates in `action_items.deadline` came from the update text or Microsoft docs (no invented timelines)
- [ ] `relevance` correctly reflects resource match: relevant (resources affected) vs not_relevant (none)

**Structural Completeness:**
- [ ] `detailed_analysis` contains 2+ `> **Term**:` concept boxes explaining technical terms
- [ ] `detailed_analysis` does NOT duplicate content from `affected_resources`, `action_items`, or `impact_summary`
- [ ] `impact_summary` follows the category family — Change: the dimensions the update actually affects (retirement/feature_change typically security+operational); Capability: only the dimensions where adoption produces a concrete gain. No dimension states an absence ("영향 없음" / "운영 변경 없음") — leave it empty instead
- [ ] `affected_resources` entries each have a `reason` with actual Resource Graph property values
- [ ] `affected_resources` has NO duplicate rows — each resource name appears exactly once (merge multi-reason resources into one entry)
- [ ] At least 1 `reference_docs` entry with title and URL

**Language Quality:**
- [ ] No internal process exposure ("Resource Graph returned", "search results show", "쿼리 결과")
- [ ] For Korean: 합쇼체(~합니다/~입니다) consistent, no 해요체 mixing
- [ ] For Korean: No translation patterns (번역체): "~하는 것을 권장", "~한 내용입니다", "~에 의해"
- [ ] **Sentence ending variety (CRITICAL)**: Re-read every sentence ending in the `detailed_analysis`. No 3+ consecutive sentences may end with the same verb form (~합니다, ~입니다, ~됩니다). If found, rewrite at least one sentence to use a different ending (e.g., ~않습니다, ~필요합니다, ~있습니다, ~됩니다). This is the most common quality defect — verify carefully.
- [ ] **Opening sentence (CRITICAL)**: `detailed_analysis` must NOT begin with the announcement — no `이번 업데이트는/공지는/발표는/변경은/GA는/preview는…`, no `이번 ~의 핵심은…`, no `이번 ~로…`. That frame reads as translated boilerplate and exposes the template. It must equally NOT begin with the environment's verdict — no `현재 환경에는 …가 없습니다`, `즉시 조치할 항목이 없습니다`, `적용 대상이 아닙니다`; that sentence belongs in the environment paragraph. Open with the thing that changed ("**Azure SQL Database**의 DDM에 … 기능이 추가되었습니다") or with a time adverb ("이제…", "2026년 9월 1일부터…"). The `not_relevant` case is no exception: state what changed, then that it does not apply here.
- [ ] **Nominalized predicates (CRITICAL)**: Re-read every `detailed_analysis` sentence that ends in `~입니다`/`~이며`. If the word immediately before it is a bound noun (점·지점·방식·의미·내용·구조·성격·형태·부분·측면·수준·셈·것·결합), rewrite the sentence so the verb trapped inside that noun becomes the predicate: "정식 출시되었다는 점입니다" → "정식 출시되었습니다"; "트래픽을 보내는 방식입니다" → "트래픽을 보냅니다"; "출시되었다는 의미이며" → "출시되었습니다". The ONLY exception is text inside a `> **Term**:` concept box. A body sentence is never exempt, even when it explains a term — move the explanation into a concept box instead.
- [ ] No emojis in report text

**Actionability:**
- [ ] For retirement/feature_change: `action_items` are present with procedure or cli_command
- [ ] For `opportunity`: exactly ONE scoped evaluation `action_item` exists (named candidates, real criteria, empty deadline) — an opportunity is never a dead-end
- [ ] Action items ordered by `step` in logical execution sequence
- [ ] Each action item has `task` (imperative verb), `why`, and `target_resources`
- [ ] Numbers always include context: "22개 중 3개 영향" not just "3개 영향"
- [ ] `additional_checks` items are self-serviceable (WHAT/WHERE/WHY) — NO "CSA 사전 검토" / "별도 검증 필요" hedges, NO re-raising a region/SKU question a tool already answered, and NO deferring a queryable ARM property (AKS networkProfile/ACNS, P2S VPN gateway, Recovery Services vault, Cosmos backupPolicy) that should have been queried
- [ ] For `not_relevant` with 0 affected resources: `detailed_analysis` ≤ 500 chars, ≤ 1 concept box, no migration path, no action items

**Scannability & Formatting:**
- [ ] `one_line_summary` enables 3-second scan: includes resource count and urgency signal
- [ ] `one_line_summary` for retirement/feature_change uses "title — N resources need action" dash pattern
- [ ] `detailed_analysis` uses **bold** for key terms, service names, numbers
- [ ] `detailed_analysis` has paragraph breaks (not one massive block)
- [ ] Concept boxes are 1-2 sentences, explain the term's purpose/function; include a `([Microsoft Learn](URL))` link when a real doc URL is available (never fabricated)
"""
