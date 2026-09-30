---
name: foundry-agent-architecture
description: 'Audit and improve AzBrief Microsoft Foundry architecture. Use when: Hosted Agent, Prompt Agent, multi-agent, agent loop, agent harness, Foundry skills, toolbox, agent instructions, roster validation, planning evaluation reporting agents, classic Agents API migration, unnecessary agent implementation.'
---

# Foundry Agent Architecture

This is a GitHub Copilot developer skill, not an AzBrief Agent runtime instruction.
Foundry operational guidance is maintained in
[foundry_instructions.py](../../../src/agent/foundry_instructions.py), independently of this file.

## When to Use

- Auditing whether AzBrief uses Prompt Agent or Hosted Agent capabilities correctly
- Changing `src/agent/foundry_backend.py`, the Plan-Execute-Evaluate graph, specialist roles, or roster validation
- Adding Foundry tools, toolbox skills, standing instructions, or Agent Service evaluation
- Reviewing agent-loop reliability, concurrency isolation, or unnecessary orchestration

## Architecture Truth

AzBrief's complete LangGraph Plan-Execute-Evaluate-Report harness, quality-correction loop, and subscriber customization run in the `azbrief-analysis-hosted` Microsoft Foundry **Hosted Agent**. It is the only orchestrator. Six distinct immutable **Prompt Agent** roles provide coordinator, Resource Graph, Azure MCP, Azure API, report-writer, and quality-reviewer expertise through the project-scoped Responses API with `agent_reference`.

The three evidence specialists run concurrently. Resource Graph owns KQL authoring, schema probing, result interpretation, and KQL repair. Azure MCP owns the authenticated read-only MCP connection and has no local ARM fallback. Azure API owns read-only ARM, Health, Policy, Advisor, Activity Log, Cost Management, and Billing FunctionTools. The report writer receives validated evidence; the quality reviewer owns evidence sufficiency, semantic G-Eval, bounded correction feedback, and action safety. Missing roles or duplicate Agent names fail closed.

Container Apps is the control plane only: FastAPI/Admin/Archive, authenticated MCP, RSS selection, scheduler, immutable canonical analysis storage, forward-only checkpoint, and email delivery. `src/main.py` and `src/scheduler.py` instantiate `HostedAgentAnalyzer`, never `AzureUpdateAnalyzer`. Missing Hosted Agent configuration fails closed; do not reintroduce an in-process fallback.

`relevance_evidence` remains a stable field for category-aware environment applicability/value.
Subscriber customization carries that original evidence and assesses role/focus even when the
canonical result has no resource rows; only a missing role/focus profile with no translation can
skip the call. Keep its overload behavior fail-fast. Label changes ship with the control plane;
generation changes require Hosted code plus new versions for modified compiled runtime guidance.
Do not rewrite immutable Archive text or store subscriber job relevance there.

Hosted contract v3 carries an optional `AnalysisScope`. Bounded subscribers receive a separate
email-only analysis per unique normalized Management Group/Subscription/Resource Group scope. The
scope is bound with `ContextVar` across the full Hosted graph. Resource Graph enforces it at the SDK
and KQL boundaries; specialists/tools that cannot enforce it return explicit gaps. Scoped failures
never reuse the unscoped canonical result, and scoped variants never enter the shared Archive.
The v3 Hosted runtime accepts legacy v2 unbounded requests and preserves v2 in the response so a
rolling upgrade can deploy Hosted first and the control plane second. Never reverse that order.
Bounded analysis also excludes canonical KQL knowledge, history, pattern memory, and retirement
state. Tool-result refs require the same trace owner, and full Resource Group ARM IDs preserve their
parent subscription as an exact pair.

The two runtimes have separate identities. The Container Apps UAMI owns Key Vault, checkpoint, canonical archive, email, Admin, Archive UI, and MCP control-plane access. The Hosted Agent's automatically created identity owns Azure evidence queries and Prompt Agent/model access. Never grant tenant evidence permissions to the wrong identity merely because the Container App previously ran the graph.

## Procedure

For the separate [KT infrastructure profile](../../../infra/kt/README.md), distinguish platform
bootstrap from application readiness. The existing VNet is never recreated; subnet names are
operator-selected rather than fixed. Distinct Foundry and Container Apps subnets are /27-minimum
`Microsoft.App/environments` delegations, while the /28-minimum private-endpoint subnet has no
service delegation. Use a project Capability Host with AAD Blob/Cosmos/Search bindings after private
DNS and provisioning roles, then grant generated-container data roles. Network injection
already creates the account host; do not create a second one or auto-delete a conflicting host.
The Container Apps Environment must include `publicNetworkAccess: Disabled` in its initial PUT
because KT deny policy blocks a public or unspecified create request; a later patch is not valid.
Portal Resource Graph discovery and CLI preflight reuse any required Private DNS zone already linked
to the selected VNet, while Bicep creates only missing namespaces. Explicit cross-subscription IDs
may override matching discovery but conflicts fail closed. Never create another overlapping VNet
link or manually merge Private Endpoint-managed records.
No Application Insights is created, and the standard customerSetup/MCP workflow is not
compatible without KT adaptation. Preserve the six-role Hosted architecture during the
subsequent application handoff. Bootstrap scale-to-zero is not safe for long in-process
Manual Runs. Optional Log Analytics omission must pass `null` to the managed-environment AVM;
its 0.16.0 discriminator accepts only `azure-monitor` and `log-analytics`, never `none`.
These are developer deployment rules, not model-facing instructions.
New resource names use editable literal KT-profile defaults, not a verified customer corporate
standard. Keep the existing network inputs explicit and the compiled ARM, parameter example and
CLI-resolved names aligned; preserve operator overrides and check global name availability.
The KT README button pairs its own ARM and CreateUIDefinition, never the standard UI.
The guided form reads the selected VNet's subnet inventory, maps arbitrary existing names to the
three roles, derives and displays CIDR without a separate address input, preserves editable resource
defaults, and emits false subnet-create flags. Its foundation-only default and explicit
completion-stage acknowledgement do not replace CLI ownership/IP checks or host readiness waits.
Keep the prerequisites and actual validation limits visible in both language READMEs.

Provisioning uses two model tiers without changing the six distinct Agent identities. Core roles
(coordinator, Resource Graph, Azure API, report writer and quality reviewer) default to `gpt-5-terra`
with `medium` reasoning; Azure MCP defaults to `gpt-5-luna` with reasoning omitted. Subscriber
customization remains core work. Tier deployment aliases and core effort are configurable through
`FOUNDRY_CORE_MODEL_DEPLOYMENT`, `FOUNDRY_SIMPLE_MODEL_DEPLOYMENT`, and
`FOUNDRY_CORE_REASONING_EFFORT`. `--model`, then `FOUNDRY_MODEL_DEPLOYMENT`, retain legacy global
override precedence. Same-model legacy options are preserved; a model change drops inherited
sampling/reasoning. Managed tiers clear sampling options and `--check` enforces model/effort policy
alongside the existing tool/instruction/schema checks. Customer setup v2/v3 carries both deployments;
v1 remains single-model. Verify availability, supported options, tool/schema behavior, quota,
latency, cost, and paired report quality before live promotion. Offline checks are not that proof.
Keep this deployment policy outside model-facing instructions; do not mutate `.env` implicitly.

For a new customer installation, follow [the customer deployment guide](../../../infra/CUSTOMER_DEPLOYMENT.md)
and `scripts/setup_customer.ps1`, not a maintainer's local `.env` or default azd environment.
The non-secret ARM `customerSetup` contract binds the exact tenant, project, Hosted name and
six specialist aliases. Configure/Mcp/Agents/Application/Verify/EnableSchedule are distinct
stages; SDK/CLI targets must agree, and Mcp uses its own deployment region. Hosted publication
requires a clean source tree, import/full tests and an exact roster check. Dedicated Hosted
evidence permissions, private connectivity and operational acceptance remain explicit customer
gates. Never treat foundation success or a bootstrap image as a completed installation.
The real application and scheduler must share an immutable digest before scheduling is enabled.
These are deployment procedures, not new Prompt Agent runtime instructions.

Customer setup v3 also binds the shared Container Apps environment/profile and existing Application
Insights; current outputs optionally add the `AzBriefFailures_CL` Direct DCR endpoint, immutable ID,
resource ID and stream while older v3 outputs remain valid. The separate MCP app keeps its own
identity and Reader, but creates no second environment or monitoring resources. Its pinned exporter
supplies no TokenCredential: keep Insights Entra-only, disable direct MCP traces/metrics and
Microsoft telemetry, and collect console logs in the shared workspace. Mcp validates shared targets
before/after provisioning; Verify/EnableSchedule repeat the checks. Legacy MCP apps in another
environment require an approved recreation and connection URL/Agent refresh, never automatic
deletion. Preserve historical telemetry and verify private Foundry connectivity separately.

Local preparation also checks the CI workflow schema, its self-change triggers, full-source
Black/isort/Flake8, imports, and full pytest with the 40% coverage gate. Passing these checks does
not prove the production-runtime build, hosted CI, or customer-specific operational acceptance.
Do not bypass the paired App/Job deployment test gate for a missing synthetic-preview endpoint
or terminal-formatted PowerShell error. Keep the preview's typed error-history API aligned with
the Admin page and make the mocked CLI's output deterministic; production safety guards remain
unchanged.

1. Read the [current assessment](./references/assessment.md) and the current official Microsoft Foundry Agent documentation.
2. Identify the controlling path, not only configuration wiring:
   - `src/hosted_agent.py`
   - `src/agent/hosted_contract.py`
   - `src/agent/hosted_client.py`
   - `src/agent/foundry_backend.py`
   - `src/agent/analyzer.py`
   - `src/mcp_server.py`
   - `scripts/provision_foundry_agents.py`
   - `azure.yaml`
   - `infra/enterprise/main.bicep`
3. Classify every Prompt Agent as exactly one of the six specialist roles. Never reuse one Agent name across roles.
4. Verify local tool calls are executable and role-scoped. Prompt Agent client-side `tools=`/`bind_tools()` are not automatically honored; AzBrief uses an allow-listed JSON bridge for coordinator planning and a bounded native Responses function loop for evidence specialists.
5. Require structured, evidence-addressable outputs from Resource Graph, Azure MCP, and Azure API specialists. Every claim has a role-prefixed ID, evidence URI, confidence, and explicit gaps. A failed specialist becomes a `partial` gap, never an empty success.
6. Keep loop state per analysis. Never store diminishing-return counters, evidence, or rewrite feedback in shared analyzer fields used by concurrent runs.
   Keep resource scope in async context as well; global mutable service fields would leak scope across
   concurrently analyzed subscribers.
7. Fail closed when coordinator, evidence-completeness, report-writer, or quality-reviewer calls fail. Never translate a reviewer error into `sufficient` or a specialist error into confirmed absence.
8. Preserve transient error details so outer retry and circuit-breaker logic can classify 429/503/529 correctly. The initial user-facing `report_writer:report` call may retry 429 three times, honoring `Retry-After` or waiting 10/20/40 seconds plus jitter; subscriber customization remains fail-fast. Do not cross-fallback from one specialty to another.
9. Run `python -m scripts.provision_foundry_agents --check` before deploying the Hosted Agent. Use `scripts/deploy_hosted_agent.ps1` for the guarded import/test/roster/doctor/package/deploy/smoke sequence; it rejects dirty runtime inputs by default and accepts a reviewed source ZIP through `-FromPackage`. That mode expands the ZIP into a temporary clean staging directory because the current Foundry azd extension does not accept a direct-code ZIP through `azd deploy --from-package`, then synchronizes the new version output back to the root azd environment. The equivalent raw command is `azd deploy azbrief-analysis-hosted --environment hosted-dev --no-prompt`. Smoke through `scripts.smoke_hosted_agent`, which uses `HostedAgentAnalyzer`; `scripts.test_local` constructs the local analyzer and cannot validate a deployed Hosted version. Provisioning owns unique names, exact role-scoped FunctionTools/MCP tools, and strict evidence schemas; missing, stale, duplicate, or retired app-owned definitions fail the check. Non-app-owned managed tools are preserved only outside app-owned role boundaries.
   Use `scripts/deploy_dev.ps1` only after a changed Hosted contract is active. It deploys one immutable ACR digest to the existing control-plane App and scheduler Job, verifies the new App revision and an execution-only package-initializer smoke, and restores both previous images on failure; it does not publish a Hosted Agent version. Legacy direct-OpenAI or retired Foundry runtime variables outside current IaC fail closed unless `-AllowLegacyRuntimeSettings` is explicitly reviewed and supplied.
   Foundry adds a trailing slash to persisted MCP URLs and wraps allowed tool names in `allowed_tools.tool_names`; canonicalize these service forms before drift comparison.
10. Enforce evidence ownership. Coordinator uses Microsoft Learn first and Web Search only as a public supplement. The default `FOUNDRY_COORDINATOR_LEARN_TRANSPORT=managed_mcp` attaches Learn MCP; explicit `hosted` uses the existing allow-listed public-document tools through `local_tool_calls`. Publish the changed coordinator version and verify `--check` under the same setting: `tool_choice=none` and per-call `tools=[]` do not bypass persisted MCP discovery. Never swap models, disable the Azure MCP specialist, broaden scope or fabricate documentation to recover a managed Learn proxy outage. Resource Graph uses only KQL/schema/result tools. Azure MCP uses only the Entra-authenticated read-only MCP Server. Azure API uses only read-only management/commercial APIs. Web Search is never tenant-state evidence.
   For bounded subscriber analyses, call only a specialist/tool that can enforce the full requested
   hierarchy scope. An unavailable scope representation is a gap, not permission to query wider.
   Derive the top primary Regions from Resource Graph, not from Azure MCP's intentionally bounded
   group/Resource Health/Advisor surface. A GA/Preview report must distinguish feature rollout
   evidence from provider/resource-type deployability and expose an explicit outcome per Region.
   Public MCP discovery retries belong in `_invoke_foundry_agent`, not model instructions or the
   global HTTP classifier. The explicit allow-list currently contains only HTTPS Learn `/api/mcp`.
   Match structured 400 `tool_user_error` discovery errors with upstream 408/429/500/502/503/504/529;
   allow three additional attempts, honoring server hints or 10/20/40-second jittered backoff inside
   one original timeout. Preserve cancellation and the final exception, avoid another sleep after
   exhaustion, and never replay native local tools or extend this policy to tenant MCP/auth errors.
   Test the observed wrapped error, exclusion boundaries and outer retry interaction. This runtime
   change needs Hosted publication; unchanged Prompt Agent definitions need no policy rewrite.
11. Keep Azure MCP isolated in its own Container App and identity. Pin the verified official image through `azureMcpImage` (never production `latest`) and upgrade only after a direct-schema and live-inventory smoke test. Use HTTPS, incoming Entra authentication, `--mode all` restricted to the `group`, `resourcehealth`, and `advisor` namespaces, and `--read-only`; grant only subscription Reader. The Azure MCP specialist must call direct tools rather than an `azure` proxy. Inject the exact tenant GUID and configured subscription GUID into each request, and forbid the literal tenant value `default`. Never enable dangerous auth or elicitation bypasses.
12. Keep current Agent Service contracts: immutable Prompt Agent `create_version`, Hosted Agent direct-code deployment through `azure.yaml`, `responses.create`, and one-shot analysis requests. Preserve unrelated managed tools when publishing a new instruction/model version and replace app-managed server tools when their URL, connection, or policy drifts.
13. Keep the wire contract strict and versioned. Contract v3 `HostedAnalysisRequest` carries the
   complete update plus optional `AnalysisScope`; `HostedCustomizationRequest` carries the complete
   domain payload. Responses must match both `trace_id` and `operation`. Invoke the dedicated
   Responses endpoint with `?api-version=v1` and `store=false`: AzBrief is one-shot and does not need
   the resilient task subsystem. Never send Python object reprs across the boundary.
   `HostedEvaluationRequest` is a pre-release-only operation on the same contract. It returns the
   canonical analysis plus bounded G-Eval, trajectory, and action-verification summaries. It must
   not return raw tenant evidence or private judge reasoning.
14. Prevent recursion. `src/hosted_agent.py` maps the six non-reserved `AZBRIEF_PROMPT_*` specialist aliases, validates a complete distinct roster, and clears `foundry_hosted_agent_name` before constructing `AzureUpdateAnalyzer`.
15. Keep the AzBrief `/mcp` control-plane surface distinct from the Azure MCP evidence server. `/mcp` uses official SDK v2 Streamable HTTP, validates `X-API-Key` before payload parsing, exposes bounded AzBrief tools, and delegates full analysis to the Hosted Agent.
16. Treat `/app` as read-only in Hosted Agent code deploys. Persist optional history/pattern state under `$HOME`, and keep every optimization write best-effort so a storage problem cannot invalidate a finished report.
17. Re-score with the rubric below. Make one focused improvement, validate it, and repeat until remaining gaps require a preview dependency or live resource change.
   For release work, use `scripts/quality_campaign.py`: freeze the period and holdout, establish A/A
   noise, compare paired runs, and require a full-period deployed Hosted run before approval.
18. Keep analysis archival outside the Hosted Agent. Persist the canonical pre-subscriber result in the Container App/Job before digest delivery or checkpoint progress; a configured archive failure fails closed. Never archive subscriber PII or job relevance, which belongs only to personalized email delivery, and never mistake `$HOME/.azbrief` planning memory for the durable browser archive.
19. Before deleting obsolete Foundry Agents, compare project inventory with `azure.yaml`, the six-role roster, and the active Hosted version's environment variables. Block cleanup if a required name is missing or an unexpected name is present; delete only the explicit obsolete allow-list and rerun `--check` afterward.

## Impossible-Perfect Rubric

Use a 1-5 anchored scale for each dimension. `5` is a theoretical ideal with live proof across failures, scale, security, and upgrades; `4` is production-excellent. Never award 5 while any known gap exists.

- Runtime/type fit and terminology
- Six-role specialization, unique names, and standing instructions
- Tool and skill utilization
- Specialist evidence contracts and provenance
- Harness/loop correctness and fail-closed behavior
- Concurrency and tenant-evidence isolation
- Retry, timeout, cleanup, and roster refresh behavior
- Tracing, trajectory evaluation, and bounded report quality correction
- Deployment readiness and configuration drift detection
- Upgrade path to current Agent Service, Hosted Agents, and toolbox Skills

Record both the score and concrete evidence. A score change without a test, trace, diff, or live read-only check is not an improvement.

Every Prompt Agent invocation logs a trace-correlated lifecycle without chain-of-thought: Agent and
response IDs, role/task, prompt/output fingerprints and sizes, model/status, token usage, latency,
tool argument fingerprints, and validated specialist claim/evidence/gap summaries. The Hosted request,
G-Eval, action verification, trajectory, and final report events must carry the same `trace_id`.

The persisted Quality Reviewer format is JSON-object mode because evidence evaluation, G-Eval
and action review all request JSON but use different schemas. Keep runtime schema validation,
evidence and scoring rules unchanged. `--check` rejects a reviewer without that format; repair
the producer instead of treating malformed evaluation text as `sufficient`. Foundry's disabled
logprob normalization means this Agent does not need a plain single-digit output mode.

Detailed failures share `src/error_logging.py` and `setup_logging()` across App/Job/Hosted.
The Responses server uses `configure_observability=None` to retain that application-owned setup.
Do not let SDK automatic observability replace the Entra exporter/redaction or flood the bounded
session log with console metrics; application `OTEL_ENABLED` remains enabled for the real sink.
Connect the existing shared AppInsights resource at project level with ProjectManagedIdentity and
the service-required ApplicationInsightsConnectionString metadata. The standard environment variable
is platform-reserved and cannot be supplied in a Hosted definition. Verify actual log ingestion,
not just the saved definition. Project and Hosted identities each need component-scoped telemetry
publishing; Hosted also needs project-scoped Foundry User for conversation creation/deletion.
Use measured token capacity, not just HTTP request limits, when increasing analysis concurrency.
Serial update analysis can still exceed TPM because evidence specialists and the five judge
dimensions share one deployment. Read the actual deployment rate limits and regional allocation
before resizing; preserve the model/version/SKU and quality gates. Distinguish an authorized
capacity allocation from a quota-increase request or a paid provisioned-throughput commitment.
An azd environment can retain an old Hosted version even when the service serves a newer one.
Correlate the actual response session/version and Agent Service latest-version metadata before
attributing a failure to stale code; do not infer the deployed version from azd state alone.
Retain bounded, redacted messages/cause stacks and HTTP/service request metadata before JSON
rendering, with no request bodies, validation input values, source lines or locals. The isolated
`azbrief.errors` logger exports application warnings/errors to workspace-based Insights using
Entra, not root/SDK logs; Hosted task events inherit request trace context. Preserve safe wire
errors and existing retry/partial-run semantics. Bind Hosted to the existing App/Job destination
through customer Configure and grant only component-scoped Monitoring Metrics Publisher to its
dedicated identity. Failure events additionally enter `AzBriefFailures_CL` through the Direct DCR:
App/Job get DCR-scoped Monitoring Metrics Publisher from Bicep, while the dedicated Hosted identity
gets the same narrow role after publication. Export only errors, captured exceptions,
failure-suffixed events, failed/partial statuses and positive failure counters; successful
INFO/WARNING records stay out. Deploy both runtimes, then verify AppExceptions/AppTraces and custom
table ingestion separately; offline sink tests and `OTEL_SDK_DISABLED=true` are not live telemetry
evidence.

Native async FunctionTools must execute on the caller's analysis loop, not a newly created and
closed worker-thread loop. Shared HTTP clients retain loop affinity across updates. Keep the
synchronous Responses SDK in its worker, bridge tools with `run_coroutine_threadsafe`, and verify
trace/scope ContextVars and repeated invocations without closing the owner loop.

`_parse_report_with_recovery` gives invalid resource selections or Pydantic report fields one
Report Writer regeneration with unchanged evidence and existing feedback. Recovery logs expose
field paths/error types, not rejected values. Strictly reparse the replacement; unknown/stale
references, invented metadata, unrelated parser failures, and a second invalid report stay errors.
This is runtime-code recovery, not a quality-score improvement or a relaxation of Archive v1.
Critic revisions use this same parser. Merge the critique feedback into the revision state before
schema recovery so a repaired candidate retains both the critique and the evidence. Only a valid
candidate is rescored, and a second malformed candidate still fails.
Planning and evidence evaluation use bounded foreground retry for 429/503/529; planning retries
do not consume tool turns, and evaluation stays `model_error` after exhaustion. Foreground retry
prefers server delay hints over the 10/20/40-second jittered fallback. The Hosted HTTP proxy also
honors `Retry-After`, without adding retries to subscriber customization.
Report generation errors and open circuits propagate to the run's failure count;
never replace them with a successful `AnalysisResult` carrying an error message. Keep subscriber
customization fail-fast, and do not treat a successful delivery of partial results as full coverage.

For deployed run incidents, start with the Admin run ID and counters, then follow
`orchestrator_update_failed` through `foundry_hosted_analysis_failed` to Hosted
`hosted_analysis_failed`. Preserve update and trace IDs on failure, not only success. The Hosted
wire error may include the exception type but never its private message or traceback. Daily digest
events correlate release date/count/outcome with the same run ID. A `partial` run is not fully
successful, even when completed reports were emailed. Read diagnostic command errors as well as
exit codes; operator CLI/MCP authentication failures do not diagnose the Hosted managed identity.
Compare App/Job image and Hosted version separately, and verify a repaired update without email
before explicitly authorized multi-day delivery. Keep this procedure outside runtime guidance.
Use `hosted_agent_http_response` to join a trace/update to the exact Foundry session, HTTP status,
attempt, latency and service request ID before payload validation. Preserve this metadata on error,
but never headers wholesale, credentials or bodies. `orchestrator_analysis_halted` identifies the
consecutive-failure guard separately from deadline deferral. Pending after termination is not an
active queue or automatic retry. The dispatch gate waits for in-flight analyses before abandoning
remaining targets: late success resets the failure streak; sustained failures still stop new work.
Recheck the deadline after waiting without raising concurrency or marking unexecuted targets done.
Verify the same large selection without email before declaring a
batch incident resolved; one successful update is only a smoke check.

## Validation

Always activate the virtual environment first.

```powershell
& .\.venv\Scripts\Activate.ps1
python -m pytest tests\test_foundry_backend.py tests\test_foundry_multi_agent.py tests\test_analyzer.py tests\test_critic.py tests\test_provision_foundry_agents.py -o "addopts=" -q
python -m pytest tests\test_hosted_contract.py tests\test_hosted_client.py tests\test_hosted_agent.py tests\test_mcp_server.py tests\test_api.py tests\test_scheduler.py tests\test_orchestrator.py -o "addopts=" -q
python -c "import src"
az bicep build --file infra\enterprise\main.bicep --outfile infra\azbrief-enterprise-deploy.json
az bicep build --file infra\azure-mcp-server\infra\main.bicep --stdout
$env:AZURE_DEV_USER_AGENT='microsoft_foundry_skill'
azd show
python -m scripts.provision_foundry_agents --dry-run
python -m scripts.provision_foundry_agents --roles resource_graph azure_api
python -m scripts.provision_foundry_agents --check
```

`--check` is read-only but requires Foundry data-plane access. Do not run create/update/delete operations as part of a unit test. Tests must override every `FOUNDRY_*` environment variable with an explicit value, including an empty string, so `.env` cannot leak into the test process.

## Skills and Toolbox

The repository Skill documents are GitHub Copilot development guidance only. They are never
read by provisioning or injected into AzBrief Agents. `src/agent/foundry_instructions.py` owns
model-facing operational topics and their six-role mapping; `src/agent/foundry_backend.py` owns
base role contracts and `src/agent/prompts/` owns per-request prompts.
`scripts/provision_foundry_agents.py` compiles only application-owned sources into immutable
Prompt Agent instructions. Run `--check` against an authorized target after a runtime instruction
change; drift requires new Agent versions. Copilot skill edits alone must not cause Agent drift.
The report writer and quality reviewer must remain distinct, and the reviewer may request at most
one evidence-preserving rewrite that is retained only when the score improves.

Native Foundry Skills and toolbox MCP discovery are a separate public-preview delivery mechanism.
Do not make them a production prerequisite without explicit approval and a rollback path. When
adopted, use the versioned Skills API, pin tested versions, and load them progressively through
toolbox MCP resources. Keep deterministic compiled guidance as the fallback until Skills support
meets the deployment's networking and availability requirements.
