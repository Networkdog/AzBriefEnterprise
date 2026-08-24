"""Analysis perspectives, quality standards, and assessment axes.

Included in: Planning, Evaluation, Report phases.
"""

ANALYSIS_PERSPECTIVES_PROMPT = """## Analysis Perspectives (What Administrators Need Most)

### Three Independent Assessment Axes

AzBrief evaluates every update on three orthogonal axes. Each axis answers a different question
and must be assessed independently — a high score on one does NOT imply a high score on another.

#### Axis 1: Importance (업데이트 자체의 중대성)
"How significant is this update in the Azure ecosystem, regardless of my environment?"

| Level | Criteria | Examples |
|-------|----------|----------|
| high | Service retirement/EoL with deadline (breaking change); Security vulnerability or compliance enforcement; Pricing increase or billing model change; Behavioral change that can break existing workloads | AKS 1.27 EoL, TLS 1.0/1.1 blocking, Storage billing restructure |
| medium | New GA feature release; Performance/stability/availability improvement; Price reduction or new low-cost SKU; Retirement pre-announced with 6+ month grace period | Zone-redundant backup GA, Reserved Instance new options |
| low | Preview feature announcement; New region expansion; SDK/CLI/Portal UI update; Documentation change, informational notice | Container Apps Japan West expansion, Terraform Provider 4.x |

Guiding principle: "What is the worst outcome if ALL Azure users ignore this?"

#### Axis 2: Impact (내 리소스 환경에 대한 실질 영향)
"Does this update actually affect MY resources, and do I need to take action?"

| Level | Criteria | Examples |
|-------|----------|----------|
| high | Affected resources confirmed in Resource Graph; Affected resources in admin's PRIMARY region (top regions by resource count); Inaction causes service disruption, security exposure, or cost increase; ≥2 affected resources, or ≥1 production-grade resource | 3 Storage Accounts with TLS 1.0 in koreacentral, 1 AKS prod cluster on 1.27 |
| medium | Admin owns the service type but no directly affected resources (settings already compliant); Affected resources in NON-PRIMARY region; Action is optional — improvement opportunity; Indirect impact possible (dependency chain) | Storage Accounts exist but all already TLS 1.2; new SKU could reduce cost |
| low | No resources of the affected type (Resource Graph returns 0); No resources in the update's target region; Update targets SDK/tools the admin doesn't use | No Azure NetApp Files, no resources in Japan West |

Guiding principle: "Do I need to DO something because of this update?" — high=must act, medium=should review, low=safe to ignore.

**Usage-Weighted Impact Assessment**:
When Resource Graph shows affected resources, weight the impact by usage signals:
- **Production indicators**: Resources in primary regions (top regions by count), resources with availability zones, resources with high SKU tiers (Premium, Standard), resources with multiple replicas/instances
- **Active vs dormant**: If Activity Log data is available, resources with recent deployments/changes (within 30 days) carry higher impact weight than stale resources (no changes in 90+ days)
- **Dependency depth**: Resources with more dependencies (Private Endpoints, VNet integrations, linked services) have higher blast radius and thus higher impact
- **Quantification rule**: Always express impact as "X out of Y resources affected, Z in production" — never just "some resources"

#### Axis 3: Job Relevance (직무연관성)
"Is this update relevant to the subscriber's specific job role?"
(Evaluated per subscriber based on SUBSCRIBERS[].role)

| Level | Criteria | Examples (role: "Azure 총괄") |
|-------|----------|------|
| high | Update target is within the role's direct management/decision scope; This role is the action owner or approver if action is needed; Impacts the role's core KPIs (availability, cost, security) | Infrastructure-wide changes, security compliance, cost structure changes |
| medium | Update is indirectly related — awareness needed but execution is another team's job; Cross-functional area (e.g., security update ↔ infra admin); Informational value for the role, not actionable | Specific team's service feature change — 총괄 needs awareness only |
| low | Update target is outside the role's domain; Role has no decision or execution responsibility for this update; Requires different expertise domain | ML-specific update for a network engineer role |

Guiding principle: "Should this person read this update?" — high=must read, medium=nice to know, low=not their concern.

### Urgency Classification
- **Critical**: Service disruption, security vulnerability, immediate action required (within 24 hours)
- **High**: Feature retirement, breaking change, migration required (within 1-2 weeks)
- **Medium**: New GA feature, cost optimization opportunity, performance improvement (review within 1 month)
- **Low**: Preview feature, new region, informational only

### Business Impact Analysis
- **Cost**: Pricing changes, new SKUs, reserved instance options
- **Security**: Compliance, authentication changes, vulnerability patches
- **Performance**: Throughput, latency, availability improvements
- **Operations**: Management convenience, automation options, monitoring features

### Action Item Criteria
Every recommendation must include:
1. **What**: Specific task description
2. **Where**: Azure Portal path or CLI/PowerShell command
3. **When**: Recommended completion timeframe
4. **Why**: Risk of not taking action

## Analysis Quality Standards — CSA-Level Depth
- **Workload context**: Interpret resource configurations as workload patterns.
  - Example: "22 Storage Accounts with Standard_LRS indicate dev/test workloads; no geo-redundancy at risk."
  - Example: "AKS cluster with Azure CNI + Private Cluster + AAD RBAC indicates a regulated production environment."
- **Dependency awareness**: Identify how an update to one service affects connected resources.
  - Example: "TLS 1.2 enforcement on Storage also affects 6 Private Endpoints and any client using Azure SDK < 12.x."
- **Configuration gap analysis**: Compare the update's requirements against ACTUAL queried settings.
  - Not "check your TLS version" but "3 out of 22 Storage Accounts still use TLS 1.0: sthottierpoc, config1748010409871, alertbotdatast."
- Use ONLY accurate information based on official Microsoft Learn documentation
- Provide specific Azure Portal paths or CLI commands (only when confirmed in Microsoft Learn docs)
- Specify estimated work time and impact scope (only when verifiable)
- EXCLUDE speculative or unconfirmed information — leave unconfirmed fields empty
- EXCLUDE generic recommendations without specific context
- ABSOLUTELY NO fabrication of CLI commands, Portal paths, or URLs not in tool results
"""
