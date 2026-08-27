---
description: "Copilot instructions for AzBrief — Azure Update Intelligence Agent."
---

# AzBrief — Copilot Instructions

**MANDATORY: Always activate the virtual environment before running ANY terminal command.**
Before executing `python`, `pytest`, `pip`, or any project script, you MUST run:
- Windows: `& .\.venv\Scripts\Activate.ps1`
- Linux/Mac: `source .venv/bin/activate`
Never run Python commands without the venv activated. No exceptions.

**Any code you commit MUST pass `python -c "import src"` without import errors.**
Do not commit if build/import fails. If you were unable to verify, you MUST report that.
Do not claim success — it is only success if you actually verified it.

## Project Overview

AzBrief Enterprise is the enterprise edition of AzBrief and shares the same product identity, analysis core, and mission. It is an **Azure Update Intelligence Agent** for Azure administrators. Its complete custom LangGraph harness and subscriber customization run as a Microsoft Foundry **Hosted Agent**. Container Apps is the control plane: FastAPI/Admin/MCP, RSS selection, digest checkpointing, scheduling, and email delivery. It invokes the Hosted Agent through a strict versioned Responses contract and never constructs the analyzer locally.
It analyzes Azure Update RSS feeds, queries the administrator's actual Azure resources via Resource Graph to assess relevance, evaluates each update on three independent axes — importance (update's inherent significance), impact (effect on the admin's resource environment), and job relevance (fit to the subscriber's role) — generates impact analysis and action items via AI Agent (LangChain/LangGraph), and delivers a consolidated daily digest email. All updates are analyzed without pre-filtering — the email summary displays a compact table with columns for 중요성, 영향도, and 직무연관성 (높음/보통/낮음 badges), and each title links to its detailed analysis below. It aims to provide practical help to Azure administrators who manage diverse roles.

### Product Identity and Direction

- **Mission**: Translate every generic Azure announcement into what it means for the administrator's actual environment and what the responsible operator should do next.
- **Vision**: Make Azure change intelligence a routine operational capability so every Azure team knows what changed, where and why it matters, and what comes next for its own environment.
- **Evidence before inference**: Ground conclusions in actual tenant resources, configuration, health, policy, cost, and regional availability. AzBrief is environment-aware decision intelligence, not a generic update summarizer.
- **Action over notification**: Turn findings into safe, specific procedures, commands, deadlines, and risk warnings instead of merely restating announcements.
- **Coverage without silent filtering**: Analyze every collected update before deciding its importance, impact, or job relevance so risks and opportunities are not discarded prematurely.
- **One investigation, role-specific delivery**: Reuse the same evidence while adapting the briefing to each subscriber's responsibility and language.
- **Enterprise extension, not product divergence**: A Foundry Hosted Agent, governed Prompt Agents, Entra-only access, Container Apps control surfaces, private networking, observability, and durable state extend the original AzBrief mission into regulated environments; they do not replace or redefine it.
- **Trust before autonomy**: Keep evidence traceable, validate executable actions, and fail closed when identity, permissions, or model capabilities are unclear.

> This is a Korean-language project. Email templates and comments are written in **Korean**.
> Code (variable names, function names, class names, docstrings) is written in **English**. Prompts are written in **English** to save tokens, but final user-facing output must be in **Korean**.
> When code is added or changed, project documentation must also be updated.
> Specifically: `README.md`, `.github/copilot-instructions.md`, and relevant `.github/skills/*/SKILL.md` files must reflect the current code state.

---

## Agentic AI Design Principles

AzBrief follows advanced Agentic AI design principles for reliability, resilience, and quality.

### Core Philosophy

- **Fail-Closed by Default**: Undeclared capabilities default to the safest option. If a tool's concurrency safety is unknown, execute serially. If permissions are unclear, require explicit approval.
- **Minimal Viable Change**: Implement only what is requested. No extra features, no unrequested refactoring, no comments on unchanged code, no abstractions for one-time operations.
- **Progressive Complexity**: Start simple, add complexity only when needed: single tool → tool loop → multi-phase agent → parallel execution.
- **Observability First**: Every LLM call, tool execution, and state transition is logged with trace_id, elapsed time, token usage, and decision rationale via structlog.

### Agent Loop Architecture

AzBrief uses a **Plan-Execute-Evaluate state machine** (LangGraph) with explicit typed transitions:

```
Plan → Execute → Evaluate → (sufficient → Report | partial → Revise → Execute | insufficient → Plan)
```

**State machine rules:**
- Each agent loop iteration produces a new immutable state dict (no in-place mutation of `AgentState`)
- Terminal transitions: `completed`, `model_error`, `max_turns`, `prompt_too_long`, `aborted`
- Continue transitions: `tool_use`, `output_recovery`, `compact_retry`
- Loop termination guardrails: `max_iterations=5`, `task_revision_count≥3`, `plan_revision_count≥2`
- **Diminishing returns detection**: If 3+ iterations produce < 500 tokens of new content each, force termination

### Resilience & Error Recovery

- **Differential retry strategy**: Foreground (user-facing) calls retry with exponential backoff + jitter; background tasks (subscriber customization) fail immediately on overload to prevent gateway amplification
- **Circuit breaker**: Track consecutive failures; after 3 consecutive failures, fall back to alternative model or abort gracefully. Auto-reset after timeout (half-open state)
- **Model fallback**: After `MAX_CONSECUTIVE_OVERLOAD_ERRORS` (3) consecutive 529 errors, raise `ModelFallbackError` to trigger model switch. Cleanly separates retry exhaustion from model switching logic
- **Stale connection detection**: Detect ECONNRESET/EPIPE for targeted recovery (disable keep-alive pooling + reconnect) instead of generic retry
- **LLM-assisted tool repair**: On tool failure, use LLM (codex model for KQL, fast model for others) to fix tool arguments and retry up to `max_retries`. Circuit breaker on fixer to avoid infinite loops
- **Multi-turn output recovery**: If LLM hits output token limit, inject meta-message ("Resume directly — no apology, no recap") and retry up to 3 times
- **Error withholding**: Recoverable errors (prompt-too-long, max-output-tokens) are not surfaced to callers until recovery is attempted. Surface only if recovery fails
- **Graceful degradation**: If Resource Graph query fails, continue analysis with reduced confidence (set `relevance=UNKNOWN`); if codex model fails, fall back to primary LLM
- **Wall-clock run budget**: `RunDeadline` (`run_time_budget_s`, default 39600s = 11h) stays an hour under the Container Apps Job `replicaTimeout`, so the control-plane run stops dispatching new Hosted Agent requests, defers leftover updates, and commits only the contiguous completed watermark. Set to `0` to disable
- **Incremental checkpoint**: analyses complete out of order under concurrency, so only the **contiguous prefix** of finished updates may be committed (`_WatermarkCursor` in `src/orchestrator.py`) — committing the newest finished update would permanently skip the unfinished gaps behind it. The watermark is persisted to a blob (`src/services/checkpoint.py`) once the run completes

### Context Management

- **Tool result budget**: Results exceeding 8,000 characters are not discarded. The full text is kept in `src/agent/context_store.py` and the prompt receives a preview plus a `[ref=Rn]` handle; the agent reaches the remainder with the `query_tool_result` tool. Applied at storage time, not display time
- **Structured compression**: When building task results summary, include status, method, purpose, and truncated results per task
- **Prompt architecture**: Static system prompt (cacheable) + dynamic update context (per-analysis). System prompt includes role identity, tool usage guides, output format rules
- **KQL knowledge base**: Persisted schema discoveries and successful queries avoid redundant exploratory calls
- **JSON parsing resilience**: Multi-strategy fallback: direct parse → `strict=False` → brace-balancing closure. Never crashes on malformed LLM output

### Safety & Validation

- **SSRF protection**: Allowed domains whitelist for URL fetching (`azure.microsoft.com`, `learn.microsoft.com`)
- **Input validation**: Pydantic schemas on all tool inputs; JSON parsing with multi-strategy fallback
- **Prompt injection defense**: Tool results from external sources are treated as untrusted; system prompt instructs the agent to flag suspicious patterns
- **API key validation**: Optional compatibility behavior for `/api/*`; `/mcp` always fails closed when `API_KEY` is unset and validates it before parsing MCP payloads
- **Rate limiting**: Per-IP rate limiting middleware to prevent abuse

### Tool System Design & Concurrency

Tools follow a self-contained module pattern:

- Each tool inherits `BaseTool` from LangChain with `name`, `description`, `args_schema`
- Tools are the bridge between LangGraph agent and Azure SDK services
- Read-only tools (doc search, resource queries) can run in parallel during planning phase
- Write/mutation tools are executed serially
- Tool concurrency: `partition_tool_calls()` groups consecutive safe tools into parallel batches, unsafe tools into serial batches. If `isConcurrencySafe()` throws → fail-closed (serial)
- Tool concurrency: planning-phase tools run independently; execution-phase tools run via `asyncio.gather` with error isolation per task

### Multi-Model Strategy

| Agent Role | Purpose | Used In |
|------------|---------|---------|
| **Primary** (`llm`) | Judging, action verification, optional-role fallback | G-Eval, `ActionItemVerifier`, query-fixer fallback |
| **Planner** (`llm_planner`) | Evidence-plan generation + local planning-tool requests | `_planning_node` |
| **Evaluator** (`llm_evaluator`) | Independent evidence-completeness verdict | `_evaluation_node` |
| **Reporter** (`llm_reporter`) | Final report generation + output recovery | `_report_node` |
| **Codex** (`llm_codex`) | KQL query generation/fixing | `_fix_tool_args` (KQL), `ResourceGraphQueryFixer` |
| **Fast** (`llm_fast`) | Task revision, subscriber customization | `_revise_tasks_node`, `customize_for_subscriber` |

Each role resolves to a Foundry Agent Service name through `Settings.foundry_agent_for_role(role)` (`LLM_ROLES = ("primary", "planner", "evaluator", "reporter", "codex", "fast")`). `FOUNDRY_PRIMARY_AGENT_NAME` is required; every unset optional role falls back to it. Agent definitions use `AIProjectClient.agents`; runtime invocation uses the project-scoped Responses API. The app never constructs a direct Azure OpenAI/OpenAI chat-completions client.

### Memory & Caching

- **Session memoization**: `get_settings()` via `@lru_cache`, `get_resource_summary()` with 5-min TTL thread-safe cache
- **KQL knowledge persistence**: Discovered schemas and successful queries stored in `kql_knowledge_base.json` and loaded lazily
- **Hosted writable state**: The code package under `/app` is read-only. `src/hosted_agent.py` sets `AZBRIEF_DATA_DIR=$HOME/.azbrief` before loading history/pattern stores; those optimization writes are best-effort and must never discard a completed analysis
- **Lazy module loading**: Heavy imports (`langchain`, `openai`, Azure SDKs) deferred via `__getattr__` in `__init__.py`

---

## Tech Stack

| Area | Technology |
|------|-----------|
| Language | Python 3.10+ |
| AI Framework | `langchain-core`, `langgraph`, `azure-ai-projects` 2.5+ |
| AI Runtime | Microsoft Foundry Hosted Agent + persisted Prompt Agents |
| Web/MCP Framework | FastAPI + Uvicorn + MCP Python SDK v2 |
| Settings | pydantic-settings (`.env` → `Settings` class) |
| Logging | structlog (JSON structured logging) |
| Azure SDKs | `azure-identity`, `azure-mgmt-resourcegraph`, `azure-mgmt-costmanagement`, `azure-communication-email`, `azure-monitor-query` |
| HTTP | httpx (async) |
| HTML Parsing | BeautifulSoup4 with `html.parser` (stdlib, **NOT** lxml) |
| IaC | Bicep (`infra/main.bicep`) |
| CI/CD | GitHub Actions |
| Container | Container Apps: Docker `python:3.11-slim`; Hosted Agent: `python_3_13` remote build |

---

## Project Structure

```
AzBrief/
├── src/                          # Main application package
│   ├── __init__.py               # Version
│   ├── config.py                 # pydantic-settings (env → Settings)
│   ├── main.py                   # Container Apps control plane (API + /admin + /mcp)
│   ├── mcp_server.py             # Authenticated MCP Streamable HTTP tools
│   ├── hosted_agent.py           # Foundry Hosted Agent entry point; full analysis runtime
│   ├── scheduler.py              # Container Apps Job control-plane entry point
│   ├── agent/                    # LangGraph agent, tools, prompts
│   │   ├── analyzer.py           # Plan-Execute-Evaluate state machine
│   │   ├── hosted_client.py      # Container Apps → Hosted Agent proxy
│   │   ├── hosted_contract.py    # Strict versioned analysis/customization contract
│   │   ├── tools.py              # Tool definitions (LangChain BaseTool)
│   │   ├── context_store.py      # Addressable store for oversized tool results
│   │   ├── prompts/              # Phase-specific prompt assembly package
│   │   │   ├── __init__.py       # build_system_prompt(), build_report_prompt()
│   │   │   ├── core.py           # Identity, mission, accuracy principles
│   │   │   ├── analysis.py       # Assessment axes, quality standards
│   │   │   ├── tools.py          # Tool descriptions, KQL tips
│   │   │   ├── writing.py        # Report writing standards
│   │   │   ├── phases.py         # Planning, evaluation, execution prompts
│   │   │   ├── languages/        # Style guide + translation notes per language
│   │   │   └── report/           # Report prompt components + category templates
│   │   ├── kql_knowledge.py      # KQL schema discovery cache
│   │   ├── resilience.py         # Retry, circuit breaker, backoff utilities
│   │   └── geval.py              # G-Eval LLM-as-a-Judge report quality evaluator
│   ├── i18n/                     # Language registry (single source of truth)
│   │   ├── __init__.py           # LanguageSpec, register_language(), fallback chain
│   │   └── labels/               # UI label bundles, one module per language
│   ├── rss/                      # Azure Update RSS parser
│   ├── email/                    # EmailService + HTML templates
│   ├── admin/                    # Admin console
│   │   ├── auth.py               # EasyAuth principal parsing + allow-list (fail-closed)
│   │   ├── page.py               # Server-rendered console HTML (nonce CSP)
│   │   └── router.py             # /admin + /api/admin/* routes
│   ├── orchestrator.py           # Orchestrated digest runs
│   ├── services/                 # Azure SDK service classes (data access only)
│   │   └── checkpoint.py         # Durable digest checkpoint (blob / file / inert)
├── scripts/                      # Local test CLI + Foundry agent provisioning
├── infra/                        # IaC
│   ├── azbrief-enterprise-deploy.json # ARM template (Deploy button) — compiled output
│   ├── enterprise/main.bicep          # Source of truth — edit here, then compile
│   └── enterprise/modules/            # Bicep modules inlined into the compiled template
├── .github/
│   ├── workflows/                # CI (lint/test/bicep drift) + Container App CD
│   └── skills/                   # Domain-specific Copilot skills
│       ├── kql-resource-graph/   # KQL query writing & debugging
│       ├── azure-service-integration/  # Adding new Azure services
│       ├── email-template/       # HTML email template editing
│       ├── report-quality/       # Rule-based report scoring & improvement
│       ├── report-evaluation/    # G-Eval LLM-as-a-Judge methodology
│       └── language-naturalness/ # Per-language (ko/en/ja) phrasing audit
├── hosted_agent_main.py         # Root bootstrap referenced by azure.yaml
├── Dockerfile
├── azure.yaml                    # Hosted Agent direct-code deployment
├── .agentignore                  # Hosted Agent package exclusions
├── pyproject.toml
├── requirements.txt
└── .env.example
```

---

## Deployment Topology

This repository ships **one** topology. There is no Automation Account, no Function App and
no fat wheel. Analysis runs in a Foundry Hosted Agent; Container Apps hosts only control-plane
surfaces and the scheduled digest driver.

```
Container Apps Job (cron)  ──  python -m src.scheduler
  -> Foundry Hosted Agent (one full analysis per update)
    -> Azure Communication Services
Foundry Hosted Agent  ──  python hosted_agent_main.py → src/hosted_agent.py
  -> LangGraph Plan-Execute-Evaluate-Report
  -> persisted Prompt Agents
    -> Microsoft Learn MCP first, Web Search only as supplementary research
    -> read-only Azure MCP Server on a separate Container App for tenant evidence
Container App  ──  uvicorn src.main:app  (orchestrator API + /admin + /mcp)
```

Template: `infra/azbrief-enterprise-deploy.json`, **compiled from**
`infra/enterprise/main.bicep` — never hand-edit the JSON:

```bash
az bicep build --file infra/enterprise/main.bicep --outfile infra/azbrief-enterprise-deploy.json
```

CI fails when the compiled template drifts from the Bicep source, because the Deploy button
points at the JSON.

The job runs the **same control-plane image** as the app with a different entry point. Neither
constructs `AzureUpdateAnalyzer`; both use `HostedAgentAnalyzer`, which requires
`FOUNDRY_PROJECT_ENDPOINT` + `FOUNDRY_HOSTED_AGENT_NAME` and has no local fallback. A run is
bounded by `replicaTimeout` (12 h default, 7 days max), while each remote analysis has
`FOUNDRY_HOSTED_AGENT_TIMEOUT_S` (1800 s default). `RUN_TIME_BUDGET_S` stays an hour below the
job timeout so leftover updates are deferred. `replicaRetryLimit` is **0** because a failed
execution did not advance the checkpoint and the next schedule safely re-covers the window.

The checkpoint lives in `src/services/checkpoint.py`: a blob in the state storage account,
read at run start and advanced **after** a run completes. Two invariants keep it safe — only
the contiguous-prefix watermark is ever stored, and `advance()` refuses to move backwards
(ETag `If-Match` guards the race). A failure to read or write is swallowed: not advancing
repeats a window, while failing the run would lose the digest.

Blob access goes through the REST API over httpx with an Entra token rather than
`azure-storage-blob` — the store touches the blob twice per run, which does not justify the
SDK and its transitive dependencies.

#### Network isolation (`networkIsolationMode`)

| Mode | What it does |
|------|--------------|
| `vnetInjection` (**default**) | Foundry `networkInjections` (`scenario: 'agent'`) into a `/24` subnet delegated to `Microsoft.App/environments`, Container Apps workload-profile environment on a second delegated subnet, private endpoints + private DNS for Foundry, Key Vault and the state storage account, all switched to `publicNetworkAccess: 'Disabled'` |
| `perimeter` | Network Security Perimeter around the Foundry account, Key Vault, Log Analytics and the state storage account, plus an `NSPAccessLogs` diagnostic setting. Defaults to `Learning` (Transition) mode |
| `public` | Public endpoints; Entra auth, the API key and `allowedIpRanges` are the only boundary. Evaluation use only |

Constraints that are easy to get wrong:
- **Network injection is create-time only.** A Foundry account deployed as `public` cannot be
  moved to `vnetInjection` — the account has to be deleted *and purged* first. That
  irreversibility is why `vnetInjection` is the default.
- The agent subnet must be RFC1918 and **exclusive to one Foundry account**.
- `internalIngressOnly: true` does not break the schedule (the job calls Foundry directly
  instead of the app ingress), but it makes `/admin`, `/api/*`, and `/mcp` VNet-only.
- Container Apps and Communication Services are **not** NSP-onboarded, so `perimeter` mode
  leaves them on ingress IP restrictions plus the API key.

### Foundry Agent Service runtime

The Container App and scheduler use `Settings.use_hosted_agent`, which is true only when both
`FOUNDRY_PROJECT_ENDPOINT` and `FOUNDRY_HOSTED_AGENT_NAME` are set. Missing configuration,
an inactive Hosted Agent version, an invalid contract, or an endpoint failure fails closed;
the control plane never imports a local analysis fallback. Inside `src/hosted_agent.py`,
non-reserved `AZBRIEF_PROMPT_*` aliases populate the Prompt Agent roles and explicitly clear
`foundry_hosted_agent_name` to prevent recursive self-invocation.

The Foundry project endpoint does not serve chat completions. Never point an inference client
at it and never add an `.openai.azure.com` endpoint to the application settings. Persisted
models, standing instructions, strict output formats, FunctionTool declarations, optional
managed tools, guardrails, and memory belong to Prompt Agent definitions. The Plan-Execute-
Evaluate state machine and FunctionTool implementations are application-owned **inside the
Hosted Agent sandbox**. Planning tools use an allow-listed JSON request bridge; enrichment
tools use the native Responses function-call loop. Client-side `bind_tools()` alone is not a
Prompt Agent tool attachment.

Prompt Agent definitions use immutable Agent versions (`AIProjectClient.agents.create_version`),
and runtime calls use the project-scoped OpenAI client (`get_openai_client()` →
`responses.create(..., extra_body={"agent_reference": ...})`). AzBrief is one-shot per analysis,
so Hosted Agent proxy requests set `store=false` and do not require the resilient task subsystem
or a conversation; all required context is serialized into the request.
Responses metadata (ID, status, model, token usage, incomplete reason) is preserved on the
LangChain `AIMessage`, and `max_output_tokens` partial responses enter the existing recovery loop.
Never reintroduce `azure-ai-agents`, threads/runs, mutable `create_agent`/`update_agent`,
`langchain-azure-ai`, or a direct Azure OpenAI/OpenAI endpoint fallback.

Research and impact have explicit evidence precedence. Research must query Microsoft Learn
MCP first and may use Web Search only when Learn cannot establish a needed fact or a newer
public announcement must be confirmed. Never send tenant resource payloads, secrets, or
personal data to Web Search. Impact must query the Entra-authenticated Azure MCP Server first
for live tenant state; it may use app-owned FunctionTools only to fill a specific gap and must
never use Web Search as tenant evidence.

The Azure MCP Server is a separate Container App defined under `infra/azure-mcp-server`.
It runs the official image in `single` mode with `--read-only`, keeps incoming Entra
authentication enabled, and uses its own managed identity with subscription `Reader` only.
Never add a dangerous authentication or elicitation bypass, Contributor, Key Vault secret,
or storage data-plane role to this server. The Foundry project managed identity receives only
the MCP Entra application role needed to call it.

Hosted Agent source deploys separately through `azure.yaml` with `codeConfiguration` and
`azd deploy azbrief-analysis-hosted --no-prompt`; Foundry performs the remote build and creates
an immutable version. ARM/Bicep creates the account, project, model, and Container Apps control
plane but cannot create these data-plane versions. The Hosted Agent has its own Entra identity;
grant tenant/subscription evidence permissions to that principal, not the Container Apps UAMI.

The Container App mounts an MCP Python SDK v2 stateless Streamable HTTP server at `/mcp`.
It exposes only recent-update listing, full Hosted Agent analysis, and recent digest status.
MCP validates `X-API-Key` before parsing requests and returns 503 when `API_KEY` is unset.

---

## Coding Conventions

### Python Style
- **Line length**: 100 characters (configured in `[tool.black]`)
- **Formatter**: Black | **Import sorting**: isort (profile: black)
- **Type hints**: Python 3.10 style (`dict[str, Any]`, `list[str]`, `Optional[X]`)
- **Async**: Service methods are `async def`, use `await` for I/O

### Naming
- Classes: `PascalCase` | Functions/methods: `snake_case` | Constants: `UPPER_SNAKE_CASE`
- Private methods: prefix with `_`

### Docstrings
- Google style, English text, with `Args:` and `Returns:` sections

### Good Practices

- When validation guarantees a dict key exists, prefer direct key access (`data["key"]`) over `.get("key")` so contract violations surface immediately.
- When looking for code examples, check existing tool implementations in `src/agent/tools.py` — they follow the tested pattern.
- Prefer concrete types in test function parameters over `Any`.

### Error Handling
- Services return `dict` with `"success": bool` and `"error": str` on failure
- Use `structlog` logger for all logging
- Retry with exponential backoff + jitter for transient API errors (429, 529, network)
- Circuit breaker pattern: track consecutive failures, abort after threshold

### Configuration
- All config via environment variables → `pydantic-settings` (`src/config.py`)
- Access via `get_settings()` (cached with `@lru_cache`)
- Optional services degrade gracefully

---

## Testing

### Local Test CLI
```bash
python -m scripts.test_local list                    # List recent updates
python -m scripts.test_local analyze --latest        # Analyze latest update
python -m scripts.test_local analyze --url "URL"     # Analyze specific update
python -m scripts.test_local analyze --latest --jsonl results.jsonl  # Export to local JSONL (no email)
python -m scripts.test_local analyze --from 2026-02-01 --to 2026-02-10 --jsonl results.jsonl
python -m scripts.test_local resources               # View resource summary
```

> **`--jsonl FILE`** (analyze command): writes a self-contained per-update analysis record
> (update metadata + full `AnalysisResult`) as one JSON object per line and **skips all email
> delivery** (no subscriber customization, no digest). Works for `--latest`, `--url`, and
> `--from`/`--to`. Records are **appended** (JSONL convention), so re-runs accumulate.

> **Historical date ranges** (`--from`/`--to`): the live RSS feed only returns a rolling
> window of the most recent ~200 items, so older months age out of it.
> `AzureUpdateParser.get_updates_by_date_range()` automatically merges the locally crawled
> history archive (`data/azure_updates_history.jsonl`, de-duplicated against the live feed by
> canonical id) so historical periods are covered. Refresh the archive with
> `python -m scripts.crawl_azure_updates`. Pass `include_history=False` to disable the merge.

### Foundry agent provisioning

The ARM template configures the primary and phase Agent names but cannot create the Agents —
they live in the project's data plane. Enrichment defaults off until app-owned FunctionTools,
strict stage schemas, and instructions pass the read-only roster check.
`scripts/provision_foundry_agents.py` closes that gap:

```bash
python -m scripts.provision_foundry_agents --dry-run   # print instructions, no project needed
python -m scripts.provision_foundry_agents             # create/update runtime + enrichment agents
python -m scripts.provision_foundry_agents --runtime-roles primary codex
python -m scripts.provision_foundry_agents --check      # names + instructions + required tools
python -m scripts.provision_foundry_agents --delete    # tear the roster down
```

Foundry normalizes persisted MCP definitions by adding a trailing slash to the
server URL and serializing `allowed_tools` as `{"tool_names": [...]}`. Roster
drift checks canonicalize those service representations before comparing them;
do not replace that semantic comparison with raw payload equality.

Runtime instructions live in `RUNTIME_AGENT_INSTRUCTIONS`; enrichment instructions are
derived from `STAGE_PROMPTS` by cutting at the runtime context marker. Research and impact
FunctionTools are generated from the live LangChain Pydantic schemas and executed locally
through a bounded Responses function-call loop; strict JSON response schemas are stored on
all four stage versions. `--check` verifies exact functions, rejects retired app functions,
and detects instruction/schema drift. Non-app-owned Foundry tools are preserved. Review
rejection removes rejected claims and dependent actions. A missing enrichment stage is
isolated, but required runtime Agents fail closed.


### Required Environment Variables
Copy `.env.example` to `.env` and fill in:
- `AZURE_TENANT_ID` (required)
- `FOUNDRY_PROJECT_ENDPOINT` (required)
- `FOUNDRY_PRIMARY_AGENT_NAME` (required)
- `FOUNDRY_PLANNER_AGENT_NAME` / `FOUNDRY_EVALUATOR_AGENT_NAME` / `FOUNDRY_REPORTER_AGENT_NAME` (optional; primary fallback)
- `FOUNDRY_CODEX_AGENT_NAME` / `FOUNDRY_FAST_AGENT_NAME` (optional; primary fallback)
- `FOUNDRY_MODEL_DEPLOYMENT` (provisioning only)
- `AZURE_SUBSCRIPTION_ID` (optional — omit for tenant-wide query)

---

## Log-Based Troubleshooting (Self-Healing Workflow)

When asked to troubleshoot, improve, or "fix errors from logs", follow this structured workflow.
AzBrief logs every LLM call, tool execution, and state transition as JSON in `logs/*.log`.
Use these logs to diagnose issues, implement fixes, re-run, and verify — iterating until clean.

### Step 1: Discover & Triage

Scan the most recent log file(s) for error patterns:

```powershell
# Find latest log
$latest = Get-ChildItem logs\*.log | Sort-Object LastWriteTime -Descending | Select-Object -First 1

# Count errors by type
Select-String -Path $latest.FullName -Pattern "ParserFailure|InvalidQuery|kql_query_failed|kql_fix_llm_failed|task_failed|ERROR" | Measure-Object

# Summarize analyses
Select-String -Path $latest.FullName -Pattern '"event": "analysis_complete"' | ForEach-Object {
  $raw = $_.Line -replace '^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} \[INFO\] [^:]+: ', ''
  $j = $raw | ConvertFrom-Json
  "$($j.update_id) | rel=$($j.relevance) | $([math]::Round($j.total_elapsed_s))s | $($j.title.Substring(0, [Math]::Min(60, $j.title.Length)))"
}
```

### Step 2: Classify Issues

Common error patterns in AzBrief logs and their root causes:

| Log Pattern | Root Cause | Fix Location |
|-------------|-----------|--------------|
| `kql_query_failed` + `ParserFailure` | LLM generated invalid KQL (join, let, unsupported syntax) | `src/agent/prompts/tools.py` KQL tips, `src/agent/tools.py` rule-based fix |
| `kql_fix_llm_failed` repeated N times | Codex model returns 400 but error not cached → retries forever | `src/agent/tools.py` `_llm_unavailable` cache conditions |
| `task_failed` after max retries | Tool execution failure not recoverable | Check tool args in plan, add fallback in `_rule_based_fix` |
| `llm_circuit_breaker_open` | 3+ consecutive LLM failures | Check model deployment, API key, rate limits |
| `output_recovery_attempt` | Report hit output token limit | Consider reducing prompt size or raising `max_output_tokens` |
| `429` / `529` errors | Rate limiting / model overload | Backoff is automatic; check if request volume is too high |
| `ECONNRESET` / `EPIPE` | Stale connection | Automatic reconnect; check network stability |
| `model_error` transition | Unrecoverable LLM error | Check Azure OpenAI deployment status |

### Step 3: Fix

Apply fixes based on the classified issue:

1. **KQL syntax errors** → Add rules to `src/agent/prompts/tools.py` (Custom KQL Writing Tips section) and/or `src/agent/tools.py` (`_rule_based_fix` method)
2. **LLM error caching** → Update `src/agent/tools.py` `_llm_unavailable` conditions or circuit breaker thresholds
3. **Prompt quality** → Update `src/agent/prompts/` package modules (core.py, writing.py, languages/*.py, report/categories.py, phases.py)
4. **Report quality** → Use `python -m scripts.evaluate_report --latest --with-html` to score and iterate

### Step 4: Verify

After fixing, re-run the same analysis and compare error counts:

```powershell
# Re-run the analysis
python -m scripts.test_local analyze --from YYYY-MM-DD --to YYYY-MM-DD

# Compare error counts (should be lower or zero)
$new_log = Get-ChildItem logs\*.log | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Select-String -Path $new_log.FullName -Pattern "ParserFailure|InvalidQuery|kql_query_failed|task_failed|ERROR" | Measure-Object
```

### Step 5: Iterate

If errors remain, repeat Steps 1-4. The goal is **zero errors** in the log.
After achieving clean logs, run `python -c "import src"` and `python -m pytest tests/ -o "addopts=" -x` to verify no regressions.

### Key Log Events Reference

| Event | Level | Meaning |
|-------|-------|---------|
| `planning_phase_done` | INFO | Plan created with N tasks |
| `task_succeeded` | INFO | Tool execution OK (check `elapsed_s`, `result_chars`) |
| `kql_query_failed` | WARNING | KQL query failed (check `attempt`, `error`, `query`) |
| `kql_fix_by_llm` | INFO | LLM successfully fixed a KQL query |
| `kql_fix_llm_failed` | WARNING | LLM-based KQL fix failed |
| `kql_fix_removed_join` | INFO | Rule-based fix removed join clause |
| `evaluation_phase_done` | INFO | Evaluation verdict (sufficient/partial/insufficient) |
| `report_phase_done` | INFO | Report generated (check `report_chars`, token usage) |
| `analysis_complete` | INFO | Full analysis summary (relevance, urgency, elapsed) |
| `subscriber_customization_started` | INFO | Per-subscriber report customization |

---

## GitHub Issue-Driven Development

This project manages code change requests via GitHub Issues (`Networkdog/AzBrief`).
Copilot Coding Agent follows this workflow:

1. **Check Issues**: Before starting work, check `Networkdog/AzBrief` for **open Issues with no assignee**.
2. **Evaluate fit**: Determine if the Issue aligns with the project's purpose (Azure Update Intelligence Agent) and tech stack (Python, LangChain/LangGraph, Azure SDKs).
3. **Reject unfit Issues**: If the Issue does not align with the project direction:
   - Post a **Comment explaining why** (e.g., "This request is outside AzBrief's scope (Azure Update analysis).")
   - **Close** the Issue.
4. **Implement fit Issues**: If the Issue aligns with the project:
   - Post a **Comment announcing work has started** (e.g., "Reviewed this Issue. Starting implementation.")
   - **Implement** the requirements in code, following all conventions and rules in this document.
   - After implementation, post a **Comment summarizing changes** (e.g., changed files list, key modifications)
   - **Close** the Issue.
5. **Commit messages**: Include `Closes #<issue_number>` or `Fixes #<issue_number>` to auto-close Issues.
6. **Skip conditions**: Skip Issues that:
   - Already have an assignee
   - Have `wontfix`, `duplicate`, or `invalid` labels

---

## Git Workflow Rules

1. **No squash, force push, or rebase** unless explicitly requested.
2. **Do not chain commit and push in one command** — commit first, report changes, then wait for push instruction.
3. **Prefer new commits over amend** — never amend already-pushed commits.
4. **Import check before commit** — `python -c "import src"` must succeed before committing.
5. **Never commit auto-generated files** — `__pycache__/`, `*.egg-info/`, `build/`, `logs/` directories.

---

## Never Change (Without Explicit Request)

Do **NOT** modify these files unless explicitly asked:

- `.env` / `.env.*` (contains secrets — only `.env.example` may be edited)
- `global.json` (if present)
- `wheels/` directory contents (managed by CI)

---

## Important Rules

0. **Virtual Environment**: Always use a virtual environment for local development. Do not install dependencies globally.
1. **Do not add `lxml`** as a dependency. Use `html.parser` everywhere.
2. **Keep the Container App and scheduler Job in step as control-plane entry points** — they share one image, but neither may construct `AzureUpdateAnalyzer`; all analysis and subscriber customization must cross the strict Hosted Agent contract.
3. **Korean for user-facing content** (prompts, emails, comments), **English for code**.
4. **`langchain-core`** is the correct package — not `langchain` (full).
5. **Services in `src/services/`** are data-access only. Business logic belongs in `agent/`.
6. **Tools in `src/agent/tools.py`** are the bridge between LangGraph agent and services.
7. **pyproject.toml and requirements.txt must stay in sync** for dependencies.
8. **Python 3.10 minimum** — do not use 3.11+ features (e.g., `ExceptionGroup`, `tomllib`).
9. **Resource Graph queries are tenant-scoped** — do not assume single subscription.
10. **Never commit `.env`** — only `.env.example` with empty values.
11. **Update docs on every code change** — `README.md`, `.github/copilot-instructions.md`, and relevant `.github/skills/*/SKILL.md` must reflect the current code (new/changed functions, labels, features, deployment modes).
12. **MCP stays a control-plane surface** — expose only bounded tools through the official MCP SDK, require `X-API-Key`, and delegate model-mediated analysis to `HostedAgentAnalyzer`.

---

## Anti-Patterns

### ❌ NEVER Do

- ❌ Import `langchain` (full package) — use `langchain-core`
- ❌ Use `lxml` — use `html.parser` everywhere
- ❌ Use Python 3.11+ features (`ExceptionGroup`, `tomllib`, `TaskGroup`)
- ❌ Mutate `AgentState` in-place — each iteration produces a new state dict
- ❌ Retry background tasks on overload — fail-fast to avoid gateway amplification
- ❌ Surface recoverable errors to callers — withhold until recovery fails
- ❌ Assume single subscription — Resource Graph queries are tenant-scoped
- ❌ Add business logic to `src/services/` — services are data-access only
- ❌ Chain `git commit` + `git push` in one command — commit first, report, then push
- ❌ Install dependencies globally — always use the virtual environment

### ✅ ALWAYS Do

- ✅ Activate venv before any Python command (`& .\.venv\Scripts\Activate.ps1`)
- ✅ Run `python -c "import src"` before committing
- ✅ Run `python -m pytest tests/ -o "addopts=" -x` to verify no regressions
- ✅ Update `README.md`, `copilot-instructions.md`, and relevant `SKILL.md` after code changes
- ✅ Add new UI label keys to `src/i18n/labels/ko.py` (the canonical key set), then translate in the other bundles
- ✅ Use `structlog` for all logging (never `print()` or stdlib `logging`)
- ✅ Keep `pyproject.toml` and `requirements.txt` in sync
- ✅ Validate tool inputs via Pydantic schemas
- ✅ Escape literal `{}` braces in HTML email templates with `_escape_braces()`

---

## ⚠️ Silent Failure Risks

These issues compile fine but cause subtle runtime bugs.

| Area | Risk | What Happens | Prevention |
|------|------|-------------|------------|
| Email labels | New key added only to a non-`ko` bundle | Key is invisible to `label_keys()` and untested | Add it to `src/i18n/labels/ko.py` first |
| HTML template | Literal `{` in template | `KeyError` in `str.format()` | Use `_escape_braces()` |
| Tool definition | `args_schema` mismatch | Agent silently passes wrong args | Match Pydantic schema to tool signature |
| KQL query | Using `join`, `let`, or `mv-expand` | `ParserFailure` from Resource Graph | Stick to single-table queries with `where`/`project`/`summarize` |
| Settings | New env var not in `Settings` class | Value silently `None` | Add to `src/config.py` with default |
| Settings | Env var present but **empty** checked with `is not None` | The template always defines it, so unconfigured reads as configured | Check truthiness, not `is not None` |
| Email | Debug artefact written before delivery | An unwritable `out/` suppressed the whole digest | Keep pre-delivery side effects best-effort |
| Orchestrator | Returning a hardcoded `True` for a delivery | `email_sent` reports success for a failed send | Propagate the transport's own result |
| Settings | New setting added but no code reads it | Config looks right, behaviour never changes | Assert the *resolved* value end-to-end, not just that the field parses |
| Container image | New dependency not in `requirements.txt` | `ImportError` at container start | Keep `pyproject.toml` and `requirements.txt` in sync |
| Service class | Returning data without `"success": True` | Caller treats result as failure | Always return `{"success": bool, ...}` |

---

## Recommended Workflow

When implementing a feature or fixing a bug:

1. **Understand** — Read the relevant code files before making changes
2. **Plan** — For multi-file changes, identify all files that need updating
3. **Implement** — Make changes following coding conventions
4. **Verify import** — `python -c "import src"` must succeed
5. **Run tests** — `python -m pytest tests/ -o "addopts=" -x`
6. **Update docs** — `README.md`, `copilot-instructions.md`, relevant `SKILL.md`
7. **Commit** — One logical change per commit with descriptive message

🚨 **Steps 4-6 are MANDATORY before declaring work complete.**

---

## Learnings

Past mistakes and workarounds discovered during development.

- **A single-file commit with an auto-generated message is how a stale-buffer clobber looks in `git log` (found during the Enterprise split, 2026-08).** `tests/test_email.py` had been uncollectable for weeks (`ImportError: _split_procedure`), which hid 17 further failures. Bisecting by symbol (`git log -S "<symbol>" -- <path>`) pinned commit `b8deccb` "Implement code changes to enhance functionality and improve performance": **377 insertions, 516 deletions, `src/email/templates.py` alone, no test touched**. It silently deleted the markdown pipe-table renderer, the action-item `reference_url` link, the safety-gate verification badge and findings block, and the affected-resource table layout fixes (uniform-type header, `unknown_scope` placeholder, middle-aligned reason cell) — every one of which still had passing-by-design tests, live i18n labels (`verify_*`, `unknown_scope`, `action_reference`) and live `ActionItem` model fields. Four later commits kept improving the same file without noticing. Recovery was **not** a revert (that would have dropped the responsive layout and type-scale work): extract `git show <parent>:<path>`, diff function-by-function with `ast`, and re-apply only the lost blocks on top of the current version with an assertion-guarded script. Detection rule: **a test file that cannot even be collected is not "one broken import" — it is an unknown number of unverified behaviours.** Prevention: never let a collection error sit; CI's `pytest -x` masks it because collection failure looks like a single error.
- **A test can outlive the feature it tests, and only a green suite reveals it.** `TestDigestServiceImpact` asserted on `EmailService._build_service_impact_html`, which `0c91370` had removed **together with its 43 lines of tests** in a deliberate refactor. `a6de0ef` then reintroduced the test class alone. Because the file was uncollectable, it never failed. When restoring lost work, check whether the *test* is the stale side: `git log -S "<TestClass>" -- tests/...` against `git log -S "<symbol>" -- src/...` tells you which one moved last and why.

- **Azure sandbox hard limits shape every runbook design decision** (verified on Learn, 2026-08): runtime **3 hours** (fair share — Python runbooks are *stopped and not restarted*, job status `Stopped`), memory **400 MB**, disk 1 GB, network sockets **1,000**, and **subprocess/executable invocation is forbidden**. The last one rules out `multiprocessing`/`ProcessPoolExecutor` outright — any in-runbook parallelism must be asyncio, or fan out to child runbook *jobs*. An Automation Variable holds up to 1,048,576 chars, which is enough to checkpoint progress without an external store.
- **Committing the newest finished item is the wrong checkpoint under concurrency.** `max_concurrent_analyses` lets update #5 finish before #2; saving #5's timestamp would make the next run start after #5 and silently skip #2 forever. `_CheckpointCursor` only advances across the **contiguous prefix** of finished updates. Failures count as finished (otherwise one permanently broken update pins the checkpoint forever), but deadline-deferred and circuit-breaker-aborted updates do not.
- **A documented, configurable setting that no code reads is invisible until you check the wiring** (found 2026-08). `azure_openai_fast_*` (endpoint/key/api-version/deployment) existed in `src/config.py`, `.env.example` and `README.md`, and was set to a mini deployment in a real `.env` — but `_create_llm()` hard-coded `azure_openai_deployment_name`, so `llm_fast` had always run on the **primary** model. Subscriber customization and task revision were silently paying full-model prices, and the only visible symptom was the bill. Fixed by routing every role through `Settings.llm_profile(role)`. Lesson: when a setting exists to change behaviour, assert the *resolved* value end-to-end (instantiate the client and read back its deployment), not just that the field parses.
- `html.parser` is the only allowed HTML parser. Adding `lxml` breaks the fat wheel build on Azure Automation (Linux sandbox has no system `libxml2`).
- KQL `join` is not supported in Azure Resource Graph. All queries must be single-table. The agent generates `join` frequently — `_rule_based_fix()` strips it.
- `f-string` with `["key"]` inside `py -3 -c "..."` causes `SyntaxError` on Windows PowerShell. Always write to a `.py` file for complex inline scripts.
- UI labels live in `src/i18n/labels/<code>.py`, not in `src/email/templates.py`. `ko.py` defines the canonical key set; other languages may be partial because `get_labels()` backfills through the registry fallback chain, so a missing key can no longer raise `KeyError` at render time.
- `str.format()` on HTML templates fails on any literal `{` or `}` — every brace in the template HTML must be doubled or escaped via `_escape_braces()`.
- Background subscriber customization must fail-fast on 429/529 errors. Retrying amplifies the overload and causes cascading failures across all subscribers.
- `get_settings()` is cached with `@lru_cache`. In tests, use `get_settings.cache_clear()` to reset.
- Azure Advisor REST API (`2023-01-01`) provides richer data than the KQL `advisorresources` table: remediation actions, learn-more links, potential benefits, risk level, and solution text. The `GetAdvisorRecommendationsTool` supports both modes — set `use_rest_api=True` for detailed data. REST API is subscription-scoped (not tenant-scoped like Resource Graph), so multi-subscription environments require iterating subscriptions. On REST API failure, the tool automatically falls back to the KQL mode.
- Resource Health REST API (`2023-07-01-preview`) provides availability statuses (Available/Unavailable/Degraded) for resources. The `GetResourceHealthTool` calls `/providers/Microsoft.ResourceHealth/availabilityStatuses`. Essential for impact analysis of retirement/breaking-change updates.
- Policy Insights REST API (`2024-10-01`) provides compliance summary via `GetPolicyComplianceTool`. Uses POST to `/providers/Microsoft.PolicyInsights/policyStates/latest/summarize`. Shows non-compliant resource counts by policy assignment.
- Service Health Events REST API (`2024-02-01`) provides detailed health events via `GetServiceHealthEventsTool`. Richer than the KQL `servicehealthresources` table — includes affected services/regions, recommended actions, and FAQ links.
- Service Region Availability via the ARM providers API (`GET /subscriptions/{sub}/providers/{namespace}`, api-version `2021-04-01`) is the authoritative way to answer "is service X available in region Y?". The `GetServiceRegionAvailabilityTool` (`get_service_region_availability`) parses each resourceType's `locations` array and matches it against the admin's primary regions (auto-detected from their resource footprint when `regions` is omitted). This replaces unreliable documentation search for GA/preview/new-service/region-expansion updates and prevents vague "availability could not be verified" conclusions. Note: the providers endpoint returns a single object (not a paginated `value` array), so `AzureRestClient.get_resource()` is used instead of `call_api()`. Region display names ("Korea Central") are normalized to canonical form ("koreacentral") before matching.
- Auto-enrichment mechanism in `_inject_enrichment_tasks()` (analyzer.py) automatically adds Resource Health, Policy Compliance, Service Health Events, Advisor REST API, and Service Region Availability tasks to the execution plan when the LLM doesn't explicitly plan them. Triggered based on update type and title keywords (Service Region Availability injects for GA/preview/region-expansion/new-service updates when a provider namespace is resolvable). Runs only on the first execution pass (no task_results yet). This ensures every analysis has rich impact data without depending on LLM planning quality.
- Report quality is scored by a two-layer system: a fast **rule-based** mechanical evaluator (`scripts/evaluate_report.py` `ReportQualityEvaluator`, regex heuristics, 100-pt) as a pre-filter, plus a **G-Eval LLM-as-a-Judge** (`src/agent/geval.py` `GEvalJudge`) that scores five orthogonal 1-5 dimensions (actionability, faithfulness, job_relevance, structure, architectural_depth). The judge uses Chain-of-Thought form-filling (reasoning before score) and refines integer scores into continuous values via token log-probabilities (weighted sum over score tokens {1..5}). 5.0 is defined as an *unreachable* ideal and 4.0 as production-excellent to prevent score saturation. Dimensions run in parallel (`asyncio.gather`) with per-dimension error isolation; logprob normalization auto-disables for o-series reasoning models and degrades gracefully. `GEvalJudge.build_feedback_prompt()` produces weakest-first rewrite instructions that the `--iterate` loop injects into `settings.custom_system_prompt` to regenerate. Config knobs: `geval_enabled`, `geval_target_score` (default 4.5), `geval_logprob_normalization`, `geval_max_iterations`. `geval_runtime_enabled` (default **False**) additionally runs the judge inside `analyze_update` (`_critic_pass`): score → if below target or any critical flaw, inject `build_feedback_prompt()` and regenerate **once**, keeping the rewrite only when the score improves. Rewrite instructions travel through the `report_feedback` **state** key, never `settings.custom_system_prompt` — the settings object is a process-wide singleton and concurrent analyses would cross-contaminate each other's feedback. The judge is fed `build_evidence_context()`, which uses the analyzer's own `TOOL_RESULT_BUDGET_CHARS`. See the `report-evaluation` skill for the full methodology.
- Report delivery filtering is gated by the `report_filtering_enabled` setting (default **False** = no filtering, every analyzed update is delivered). When True, `not_relevant` updates (`should_notify=False`) are suppressed from the single-update email paths (`EmailService.send_analysis_report`, `send_to_subscribers`, `main.py`, `test_local.py`) to reduce noise. The **digest** email (`send_digest_report` / `build_digest_content`) never omits — it always shows every analyzed update, classified into high/medium/low importance tiers. The `should_notify` value itself (derived from `relevance != not_relevant`) is unchanged and still drives digest counters, badges, and logging; only the email-omission checks are gated on the flag. Per-subscriber `alert_level` (critical_only / important_and_above) is a separate, opt-in preference (default `all`) and is NOT affected by this flag.
- The affected-resources display (`format_affected_resources_html` in `src/email/templates.py`, and the judge's `render_report_markdown`) groups resources that share the **same non-empty impact reason** into a single row (resource names stacked with a dashed divider + a group-size badge; reason shown once). Resources with empty reasons are never merged. This is a display-layer grouping, complementary to the prompt-level rule that forbids duplicate `affected_resources` data entries.
- The live Azure Update RSS feed (`.../releasecommunications/api/v2/azure/rss`) is a **rolling window capped at ~200 items**, ordered by modification recency, so older months age out entirely (e.g., on 2026-07-18 the feed only spanned ~June–July with a few stray re-surfaced items — March and May returned zero). This is *not* a date-filter bug. `AzureUpdateParser.get_updates_by_date_range()` therefore **merges the locally crawled history archive** (`data/azure_updates_history.jsonl`, produced by `scripts/crawl_azure_updates.py`, ~9,755 records back to 2013) with the live feed, de-duplicated by canonical id (`_canonical_id()` extracts numeric/slug id from guid/link/URL). History records are converted via `_history_record_to_update()` (products → `azure_services`, `status` → `update_type` fallback, `created` → `published_date` via `_parse_iso_date()` which handles the API's 7-digit fractional seconds + trailing `Z` that Python 3.10's `fromisoformat` rejects). Only the date-range path merges history; `get_updates()` (used by the runbook/digest/`--latest` paths) stays live-feed-only. The full-history API is *not* newest-first, so early-stopping pagination is impossible — the pre-crawled local archive is the pragmatic source. Refresh it with `python -m scripts.crawl_azure_updates` when historical months are missing.
- **Report-quality hardening (3-month report audit, 2026-07).** A review of `results_2026-0{3,4,6}.jsonl` surfaced recurring commercial-grade defects that were fixed at the prompt/code layer:
  - **"CSA 사전 검토가 필요합니다" hedge crutch** appeared in ~90% of `additional_checks` — circular when the reader IS a CSA. `core.py` accuracy principle 2 and `writing.py` principle 5 now require **self-serviceable** checks (name WHAT/WHERE/WHY) and forbid the generic hand-off; `base.py` bans it in the output-format description + self-check.
  - **Opportunities were dead-ends**: `opportunity`-relevance updates that trigger a notification (e.g. user delegation SAS with 9 candidate accounts) shipped with `action_items: []`. `base.py` now mandates **exactly one scoped evaluation action** (named candidates, real go/no-go criteria, empty `deadline` — never fabricated), reconciled in `categories.py` `new_feature`.
  - **Region punting**: reports hedged "koreacentral 지원 여부 별도 검증" even when `get_service_region_availability`/SKU REST already answered. `base.py` region section now forbids re-raising a tool-answered question.
  - **Digest service-impact double-counting** (`EmailService._build_service_impact_html`): summed per-update `affected_resources` counts, so 7 AKS updates hitting one cluster showed "7 resources" instead of the true **unique** 1. Fixed to de-dup by `(name, resourceGroup)`.
  - **SafeLinks / tracking URLs** (`nam06.safelinks.protection.outlook.com/?url=…`, `?ocid=…`) leaked into `reference_docs`. New `src/rss/parser.py::clean_url()` unwraps SafeLinks (recursively) and strips tracking params (utm_*, ocid, msclkid, SafeLinks bookkeeping) while preserving functional params (`?view=`, `?tabs=`, fragments). Applied at RSS parse time (`learn_more_links`) **and** at report-build time (`analyzer._normalize_reference_urls`) as a belt-and-suspenders guarantee. `base.py` also tells the LLM to prefer Learn docs over the announcement's own URL.
  - **Retirement countdown ordering** (`history.get_retirement_countdown`): an already-**overdue** (breached, D+N) retirement with an open migration was sorted *after* far-future ones. Now overdue leads (most-overdue first) → soonest upcoming → undated last.
  - Verification note: prompt-behavior changes are validated by prompt-assembly + unit tests (`import src` OK, 413 tests pass), but their effect on *generated* report text can only be scored live via `scripts/evaluate_report.py --iterate` with Azure/OpenAI credentials — the JSONL audit files were produced by `--jsonl` (no subscriber customization), which is why `job_relevance`/`recommendations` are empty there by design, not a bug.
- **Resource Graph completeness — "query before you defer" (report audit follow-up, 2026-07).** The agent repeatedly punted *queryable* ARM facts to `additional_checks`/CSA review instead of querying them: AKS advanced-networking/ACNS status (`properties.networkProfile.networkDataplane/.networkPolicy/.advancedNetworking`, `addonProfiles`), Point-to-Site VPN existence (`microsoft.network/p2svpngateways`, `vpnserverconfigurations`, `virtualnetworkgateways.properties.vpnClientConfiguration`), Recovery Services vault presence (`microsoft.recoveryservices/vaults`), and Cosmos backup mode (`properties.backupPolicy.type` — Periodic vs Continuous; in one report the tool already returned `Periodic` yet the report still hedged "Continuous Backup 충족 여부 점검"). Fixed by adding a **"Resource Graph Completeness — query the answer instead of deferring it"** table to `src/agent/prompts/tools.py` (planning/execution phases) with the exact property paths + a single-table example, and a matching rule in `base.py` `additional_checks` (forbid deferring a fact that is itself an ARM resource/property; only defer genuinely non-queryable things — in-cluster K8s manifests, app/SDK code, data-plane usage). Rule of thumb baked into the prompt: *if a fact is an ARM resource or a resource property, it is queryable now — do not leave it for manual review.*
- **Per-language style parity (report audit follow-up, 2026-07).** `languages/ko.py` had detailed **주술 호응 (subject-predicate agreement)** rules and a **concept-box quality** section, but `languages/en.py` and `languages/ja.py` had neither. Added to both: a *subject-complement category-match* rule (an *announcement* is not a *feature*; "the reason is because…" is malformed) and a *Concept Boxes* section (position at first mention — never grouped at report end; calibrate depth — one crisp line for ubiquitous infra terms so a senior architect is not lectured; add the "why here" angle). `base.py`'s shared concept-box section also gained explicit **positioning + depth-calibration + a 2-4 box cap**.
- **KQL processing hardening — directly observed via a diagnostic harness against the real accumulated queries (`kql_knowledge_base.json`, 56 recorded) + realistic failure scenarios (2026-07).** Three defects in the sanitize/`_rule_based_fix` pipeline were *producing queries that could never succeed*, so the retry loop wasted every attempt and hit `kql_query_exhausted`:
  - **`let` dangling reference**: `sanitize_kql` step 5 stripped `let NAME = VALUE;` but left downstream `== NAME` references → unresolved identifier → the re-query failed forever. Fixed by **inlining** each let value into `\bNAME\b` references *before* removing the declaration (new `_RE_LET_DECL`), so `let minTls='TLS1_0'; … == minTls` becomes `… == 'TLS1_0'`.
  - **`extend` orphaning**: the project-inline mover rewrites `project name, kind=tostring(kind)` into `| extend kindValue=tostring(kind) | project name, kindValue`, but the unidentifiable-ParserFailure fallback then ran a blunt `_RE_EXTEND_BLOCK.sub("", query)` that stripped the just-added extend, leaving `project name, kindValue` with `kindValue` undefined. Fixed with `_strip_unreferenced_extends()` — it drops computed extends **only when their alias is not referenced downstream**, preserving intent (the `kindValue` extend survives because the projection still uses it).
  - **Missing pipe before `project`/`extend`**: `_RE_MISSING_PIPE` only covered `order by|summarize|limit|take`, so `… 'storageaccounts' project name` kept `project` with no leading pipe. Extended the alternation to include `project|extend|mv-expand|distinct` (small, accepted risk of a false positive if an operator keyword sits inside a string literal — net-positive vs. a guaranteed-broken query).
  - **Builder-query coverage gaps** (`src/services/resource_graph.py`): the AKS detail query was missing **ACNS** (`properties.networkProfile.advancedNetworking.observability/.security.enabled`) — the root cause of reports repeatedly hedging "ACNS 활성화 여부 점검"; the Cosmos detail query was missing `properties.backupPolicy.type`, `properties.enableAnalyticalStorage`, and `properties.disableLocalAuth` — the root cause of reports unable to confirm Continuous Backup / Synapse Link prerequisites. Both queries now project these fields.
  - Verification: re-ran the harness after each fix to *directly observe* the corrected sanitize output (`== 'TLS1_0'`; `extend kindValue … | project name, kindValue`; `| project name | order by name`), plus 6 new regression tests (`test_kql_sanitize.py`, `test_kql_retry.py`, `test_security.py`) — **419 tests pass**. Live-Azure query *execution* still can't be tested without credentials, but the deterministic sanitize/rule-based-fix pipeline and the builder queries were exercised end-to-end and verified.
- **KQL degradation — the dominant defect, found only by auditing ALL 3 months of real processing (2026-07).** A throwaway audit harness cross-referenced the entire `kql_knowledge_base.json` (56 recorded queries) with all 231 analysis records in `results_2026-0{3,4,6}.jsonl`. Key finding that synthetic scenarios completely missed: **32 of 56 recorded queries (57%) had degraded to intent-lost raw-properties dumps** (`| project name, type, resourceGroup, subscriptionId, location, sku, properties | limit 100`) — the fixer's last-resort fallback. The correlation was the smoking gun: `private endpoint` (deferred 9×), `public network access` (3×), and `TLS` hedges in reports lined up with **degraded** storage/keyvault queries even though the schema had those paths. i.e. the degradation *directly caused* reports to hedge on facts that were queryable. Root cause: `_rule_based_fix`'s `attempt<=6/<=10` branches degraded to a generic dump even for resource types that have a hand-written **builder** query. Fix: added `ResourceGraphQueryBuilder.get_query_for_resource_type(type)` (a type→builder map for storage, VM, AKS, Cosmos, KeyVault, LogAnalytics, VNet, NSG, publicIP, ACR, SQL, CognitiveServices, ContainerApps) and rewrote the fixer's post-attempt-3 cascade to **prefer the builder query** (which preserves domain projections) before falling back to a raw dump; only types with *no* builder degrade. Directly observed via the harness: all **6 degraded-with-builder types (storage/VM/AKS/KeyVault/VNet/LogAnalytics) now recover a rich query** (storage → `minimumTlsVersion`+`publicNetworkAccess`+`privateEndpoints`; AKS → `advancedNetworking`+`kubernetesVersion`). Also added the missing `publicNetworkAccess` projection to the storage builder (deferred 3× in reports). 3 new regression tests; **422 tests pass**. Separately, the audit's #1 deferral by far was **region availability (hedged 111×** vs. 9× for the runner-up) — but that is a *tool-orchestration/prompt* issue (feature-level regional rollout genuinely isn't in the ARM providers API), not a KQL-pipeline defect, so it's the top remaining opportunity for a live-verified prompt/tool change, not fixed here.
- **Run-to-run non-determinism of "affected resources" — root-caused live from logs, not guessed (2026-07).** A live G-Eval re-evaluation of the same storage-retirement update sometimes reported **1 affected account and sometimes 0**. Cross-referencing the run logs (free, no re-run) located the cause precisely: the affected account name appeared in the tool results of the "found-1" run but was **absent entirely** from the "found-0" run — and *both runs executed the identical KQL queries*. So the variance was **not** LLM report temperature (the usual suspect) and **not** query generation; it was **tool-result truncation**. `truncate_tool_result` hard-cut results at `TOOL_RESULT_BUDGET_CHARS = 3000` (`result[:budget]`), and the environment has 26 storage accounts whose full enumeration exceeds 3000 chars — so the specific affected account (`config…871`) landed before or after the cutoff depending on result ordering (the LLM's custom query lacked a stable `order by`), non-deterministically dropping the needle. Confirmed by `result_chars=3016` (exactly the budget + the "… (truncated)" marker) on the storage tasks in *both* logs. Fix: raised `TOOL_RESULT_BUDGET_CHARS` to **8000** so a typical single-type enumeration fits. Live-verified by running the same update **twice** post-fix: both runs now consistently include the account (`acctHits=1`, `result_chars=8016`) and both reports return `affected=[config…871]` (was `[1]` vs `[0]`). Honest residual limit: 8000 still truncates very large environments (>~30 resources of one type); the deeper fix (row-aware truncation preserving complete rows + a stable `order by`, or server-side filtering to the affected subset) is a larger change. Lesson: when a multi-step agent gives inconsistent results, **diff the run logs to find where the evidence diverges before touching temperature/seed** — the culprit was a silent character-budget cut, not model sampling.
- **Result-driven (semantic) query improvement + codex→primary LLM fallback (2026-07).** The KQL retry loop only fixed *syntactic* failures (ParserFailure/InvalidQuery); a query that ran successfully but returned an **empty** result from an over-strict / wrong filter (e.g. `kind =~ 'Storage'` when the real value is `BlobStorage`) was accepted as-is, so the report saw nothing. Added a **semantic** improvement layer in `execute_kql_with_retry`: on an empty result from a *property-filtered* query (`_query_has_property_filter`), a cheap type-only probe (`_build_type_probe_query`) checks whether the resource type actually has resources; if it does, the fixer's new `improve_query_for_empty_result` sends the query + a sample of the REAL data to the LLM, which corrects the filter against the actual property values, and the improved query is re-executed (bounded by `MAX_RESULT_IMPROVEMENTS=2`). Successful improvements are persisted via `record_successful_query` (purpose `"Result-improved query (was empty)"`) and reused through `build_context_for_prompt`. **Live-verified**: an intentionally over-strict query (`kind =~ 'NoSuchKindXYZ'`, 0 rows) was probed, the LLM rewrote the filter to `kind =~ 'StorageV2'` (a real value from the probe sample), and re-execution returned 21 rows. Also added a **codex→primary LLM fallback** (`_ainvoke_with_fallback` + `_is_availability_error`): the codex deployment can be absent (404 `DeploymentNotFound`) in some environments, which silently disabled ALL LLM-assisted query fixing; the fixer now falls back to the already-working primary LLM on an availability error, keeping both error-driven and result-driven fixing functional. The analyzer injects the primary as fallback via `get_query_fixer(llm=self.llm_codex, fallback_llm=self.llm)`. (Discovered live: this environment's codex deployment returns 404, so without the fallback the semantic improvement could not run.)
- **Autonomous report-improvement loop — 3 verified source fixes + the single-sample-noise lesson (2026-07).** A self-improvement session (`self-improve-reports` prompt) driven by an enterprise-email research brief produced three generalizable source fixes, each import- + `pytest`- (433 passing) + live-verified:
  - **Enterprise email client rendering hardening** (`src/email/templates.py`): the body `font-family` had macOS Korean (`Apple SD Gothic Neo`) but **no Windows Korean system font**, and there were **no Outlook cell-spacing resets** — a rendering defect for the primary audience (Korean **Windows Outlook**). Added `'Malgun Gothic'` to the stack and a new `_CLIENT_COMPAT_STYLE` head `<style>` (`table { mso-table-lspace/rspace: 0pt }`, `img` resets, `word-break` for `.azb-cli`/`.azb-code`). Windows Outlook honors `<head>` styles (Gmail strips them but needs no `mso-*`). Locked in by `test_email_enterprise_client_rendering_hardening`.
  - **`ko.py` anti-hedge self-contradiction** (`src/agent/prompts/languages/ko.py` §7): the monotony-avoidance section told the model *"instead of 'CSA 검토를 권장합니다', use … 'CSA 사전 검토가 필요합니다'"* — legitimizing and re-suggesting the exact hedge that `core.py`/`writing.py`/`base.py` **ban**. Contradictory instructions make the LLM inconsistent, which explained persistent Korean-output hedging. Replaced with guidance that bans the CSA hand-off (reader is often the CSA) and points to self-serviceable phrasing, aligning ko with en/ja (which never had the bug). **Live-confirmed** the current output is already hedge-free & self-serviceable — the alarming JSONL hedge counts (`csa_review` 165×, `region_verify` 114× across `results_2026-0{3,4,6}.jsonl`) were **stale** (produced before the 2026-07 anti-hedge fixes), a reminder to validate audit-file signals against *current* live output before acting.
  - **Region faithfulness — provider-level ≠ feature-level for previews** (`src/agent/prompts/report/base.py`): the anti-region-punt rule ("state a definitive ✅/❌") over-corrected into an *over-claim* for preview features. A live G-Eval flagged it: the report used `get_service_region_availability` (which answers at **resource-provider/resource-type** granularity for `Microsoft.Network`) as evidence that a DDoS-custom-policy *preview feature* had "no regional restriction in koreacentral." Added a balancing rule: provider presence is the right evidence for GA/SKU/new-service/region-expansion, but for a **preview feature layered on an already-used provider** it does NOT prove the feature's per-region rollout — scope it precisely and cite the preview doc / Portal region dropdown (not a punt). Anti-punt and anti-over-claim rules now coexist. **Live-verified by report-text diff**: the regenerated report replaced the over-claim with "…공급자 수준 정보만으로는 이 preview 기능이 koreacentral에 실제 롤아웃되었는지 확정할 수 없습니다."
  - **Lesson — attribute prompt changes by report-text diff, not the aggregate G-Eval score.** Two single-pass live runs of the same `not_relevant` preview update scored 3.00 then 2.76; the dip came from an *unrelated* stochastic faithfulness slip (naming an AKS-managed public IP `kubernetes-<hash>` without showing its query evidence), **not** the region fix — which was demonstrably correct in the report text. Single-sample G-Eval on a low-relevance update is noisy and score-capped (4.0 is the deliberately-hard production ceiling); chasing it risks reward-hacking/verbosity. Resource-grounding rules (`writing.py` §5 confirmed-vs-candidate, §6 negative-space) are already strong, so the stochastic AKS-IP slip is a diminishing-returns target, not a missing rule.
- **G-Eval judge was starved of evidence → false faithfulness critical flaws (2026-07).** The recurring "delete references to `<named resource>` unless you cite the query output" faithfulness penalty (flagged live on both an AKS-storage report and the DDoS preview) was **not** a report hallucination — it was an **evaluation-harness bug**. `scripts/evaluate_report.py` builds the judge's `evidence_context` from `analyzer._last_task_results` but re-truncated each task result to **3000 chars** (`str(res)[:3000]`), while the analyzer builds the report from results truncated to `TOOL_RESULT_BUDGET_CHARS = 8000` (`src/agent/resilience.py`). So in a 26-storage-account / 29-VNet estate the specific *grounded* affected resource lived past char 3000 and was **invisible to the judge**, which then (correctly, given its inputs) flagged the claim as unverified. The in-code comment even warned "truncating too aggressively causes false faithfulness penalties" — the 3000 cap *was* that too-aggressive value. Fix: feed the judge the **same** budget the report was grounded in — `snippet = str(res)[:TOOL_RESULT_BUDGET_CHARS]` (import the constant). This is **fair-evaluation, not reward-hacking**: it doesn't touch the rubric/target/weights; it removes a false-negative bias while still catching real hallucinations (claims absent from the *full* 8000-char evidence). **Live-verified**: same AKS update re-scored **2.76 (D, faithfulness int 2 + critical flaw) → 3.41 (C, faithfulness int 4, critical_flaws NONE)**; DDoS holdout showed **no regression** (faithfulness clean, int 3, stable at its not_relevant ceiling). Lesson: when the judge repeatedly flags *grounded* resource names as unverified, suspect the **evidence pipeline** (what the judge is shown) before "fixing" the report or the prompt.
- **Concept boxes can now link out to docs — but the renderer had to catch up first (2026-07).** A request to let concept boxes (`>` blockquote glossary entries) carry a technical-doc link surfaced a latent gap: `markdown_to_html()`'s `_inline_format()` in `src/email/templates.py` only handled `**bold**` and `` `code` `` — a `[text](url)` link in `detailed_analysis` (→ `relevance_reason`) rendered as **raw markdown text**, not a clickable anchor. So changing only the prompt would have shipped ugly literal `([Microsoft Learn](https://…))` strings into the email. Fix (two coordinated parts): (1) **renderer** — added `_RE_MD_LINK` + `_linkify_md()` and made `_inline_format` linkify **first** (before bold/code so the URL is not mangled), producing an `.azb-link` `<a>`; **XSS guard**: only `https://`, `http://`, and in-page `#` anchors become anchors — any other scheme (`javascript:`, `data:`) falls back to the link text alone (URL dropped), since the anchor text comes from LLM output and could be prompt-injected. (2) **prompt** — `src/agent/prompts/report/base.py` "How to write a concept box" + example + self-check, and the per-language concept-box sections (`languages/ko.py` §8, `en.py` §5, `ja.py` §7) now tell the model to append a compact `([Microsoft Learn](URL))` link **only when a real doc URL is on hand** (from doc-search tool results or the update's links; prefer Learn) and **never to fabricate** one — omit silently otherwise. The example deliberately links only the first of its two boxes to model "optional." Verified: `import src` OK, `[Microsoft Learn](https://…)` renders as an anchor, `javascript:` is stripped to text, `#`-anchor still linkifies, **454 tests pass**. Lesson: a "just tweak the prompt" formatting request often has a **rendering-pipeline** prerequisite — check that the output channel (here, the email markdown converter) actually supports the syntax before instructing the model to emit it.
- **Learn doc-search had silently returned zero results — the server-side `$filter` was broken (2026-07).** While live-verifying the new concept-box doc links, a generated report's concept boxes carried **no** links even though the feature was correct. Diagnosis from the run log: every `learn_search_ok` event logged `count: 0` despite HTTP 200. Root cause found by probing the raw API directly (same query, with vs. without each param): `MicrosoftLearnService.search_azure_docs()` always passes `filter_products=["azure"]`, which `search_docs()` turned into the OData query param `$filter=products/any(p: p eq 'azure')` — but the Learn search API (`learn.microsoft.com/api/search`) **no longer returns a `products` field**, so that filter matches nothing and drops **ALL** results (directly observed: 3 results without the filter, 0 with it). This silently disabled all documentation search across the app → empty `reference_docs` (only the update's own link survived) AND starved the concept-box links of any URL to attach — i.e. the concept-box-link feature could never fire in practice. Fix (`src/services/microsoft_learn.py`): removed the broken server-side `$filter`, over-fetch `top*3` when a product filter is requested, and apply a **client-side soft filter on the result URL** (prefer results whose URL contains the product keyword, but fall back to all results when none match so a valid search never collapses to zero). Live-verified end-to-end: doc search now returns real Learn URLs, and an AKS Gateway-API GA report rendered a concept box with a clickable `<a href="https://learn.microsoft.com/en-us/azure/aks/app-routing">` while a second box (no fitting URL) correctly stayed link-free; **454 tests pass** (the existing Learn tests only cover `close()`/`_fallback_search`, so no test regressed). Lesson: a tool logging `success` with `count:0` does not look "broken" in logs — when a *downstream* feature (doc links, references) is starved of data, probe the raw upstream API **with and without each parameter** to catch a silent server-contract change (here, a dropped response field).

- **`ko.py` style guide consolidated: principle + substitution table beats a per-phrase ban list (2026-08).** The Korean style guide had grown by appending one bullet per newly-observed bad phrase (`~한 셈입니다`, `~방향의`, `~에 해당합니다`, `~로 보는 것이 맞습니다`, …), each with 2-6 BAD/GOOD lines (357 lines at its peak, 213 after a first dedup pass). Two costs: the negative-sentence rule was stated **twice** with the same example, and the list only covered phrases someone had already seen — a near-identical variant was, by construction, unlisted. Final pass took it to **167 lines** (24,982 → 19,103 bytes) with no rule dropped: §7 is now (1) a **frequency-limit table** (같은 종결어미·부정 서술 3연속 금지, 전환구·"다만"·"핵심"·"여지" 보고서당 1회), (2) additional_checks 자립성 + the CSA hand-off ban, (3) **one principle + a BAD→GOOD substitution table** covering every 우회·분류 표현, (4) the structured-field rules. The generalization lever is stated once at the top: *"열거된 사례는 대표 예시일 뿐이다 — 같은 유형이면 표에 없는 표현도 같은 방식으로 고친다"*, plus the tie-breaker *"규칙끼리 부딪히면 소리 내어 읽었을 때 자연스러운가로 판단한다"*. Section numbers (§3 causative/분류어, §7 CSA, §8 concept box) are unchanged so existing doc references still resolve; `report-quality/SKILL.md`'s `§7 "종결어미 다양화"` pointer was retargeted to `§7 (1)`. Verified: `import src` OK, prompt assembles, **526 tests pass** (`tests/test_email.py` is separately red from an unrelated in-progress `src/i18n/` refactor — `_LABELS` no longer defined in `src/email/templates.py`). Honest limit: this is a *prompt-text* change, so no quality claim is made without a live scored run.
  - **Editor buffer vs. disk can diverge, and the edit tools follow the buffer.** `read_file`/`grep_search`/`replace_string_in_file` all saw the **old** 357-line version while `git` and PowerShell saw the 213-line file on disk, so every edit failed with "could not find matching text" and a blind rewrite would have silently reverted the prior session's work. Detect it by reading the file through the terminal (`python -c "open(...).read()"`) when an edit inexplicably fails to match; fix it with `workbench.action.files.revert` on the active editor — but first confirm the buffer holds nothing unique (here it was the same content as `git show HEAD:<path>`), and back the file up before touching anything.

- **Language support is now registry-driven — `src/i18n/` is the single source of truth (2026-08).** Adding a language used to mean editing five hardcoded lists (`_LABELS` in `templates.py`, `_LANGUAGE_GUIDES` in `prompts/__init__.py`, `allowed = {"ko","en","ja"}` in `config.py`, `_LANGUAGE_NAMES` + `_FINDING_MESSAGES` lookups in `action_verification.py`, and per-language `if` branches inside the subscriber translation prompt). All five now read the registry:
  - `src/i18n/__init__.py` holds `LanguageSpec` + `register_language()`, `normalize_language()` (`ko-KR`→`ko`), `fallback_chain()` (always terminates at `DEFAULT_LANGUAGE`), and `resolve()` for `{code: value}` bundles. `get_language()` **never returns None** — an unregistered code synthesizes a spec (English name from an ISO table) so a report is still written in that language instead of crashing.
  - `src/i18n/labels/<code>.py` holds the UI labels; `get_labels()` merges the fallback chain, so a **partial** translation can no longer raise `KeyError` at render time. `ko.py` is the canonical key set; `missing_label_keys(code)` is the review helper.
  - `src/agent/prompts/languages/__init__.py` loads `STYLE_GUIDE` / `TRANSLATION_NOTES` per language and **synthesizes a generic style guide** from the registry entry when a language has no module — so `register_language(...)` alone yields a usable language.
  - `register_cache_clearer()` lets the label and style-guide caches invalidate when a language is registered at runtime (tests, plugins).
  - Net effect: adding a language = **one registry line**; label and style-guide files are optional refinements. Verified with 29 new tests in `tests/test_i18n.py` (609 total pass), including rendering email helpers in an unregistered language.
  - Cleanup done here: the dead 300-line `_LABELS` dict was removed from `src/email/templates.py`, where `get_labels` had already been left referencing a renamed symbol (`NameError` on **every** email render).
  - **The stale-buffer trap is worse than "edits fail to match" — it silently CLOBBERS the file.** A failed match is the *lucky* case. When the stale buffer happens to contain the anchor text, `replace_string_in_file` succeeds and writes the **whole stale buffer** back to disk, reverting every unrelated change in that file. This session lost committed content in `languages/en.py`, `languages/ja.py` and `src/email/service.py` that way (all restored from `git`). Two rules: (1) after **every** edit to a file you have not already written this session, run `git diff --numstat <path>` and confirm the size matches your intent; (2) before editing, sanity-check freshness by comparing a `read_file` line number against `Select-String` on disk. When a file is stale and the loss would be large (`git diff --numstat <old-commit> HEAD -- <path>` to size it), do **not** use the edit tool — apply the change with a small assertion-guarded Python script (assert each anchor is unique, `ast.parse()` the result, preserve CRLF, keep a `.bak`) and verify with `git diff`.

- **"공지가 아니라 사실을 서술한다" — the announcement-frame defect, generalized from two reader corrections (2026-08).** A reader flagged two sentences (`이번 공지는 … 은퇴한다는 내용입니다`, `이번 GA로 … 정식으로 사용할 수 있습니다`). Per the `language-naturalness` skill, the corrections were measured before being promoted: a 332-doc corpus scan showed they are two *surfaces of one defect* — the report narrating **the announcement** instead of **what changed**. Surface A (공지 = 주어 + 분류어/명사화 서술어) hit **13 docs** in the `~하는 내용/공지입니다` form alone (on top of the 49 already covered by the release-stage rule), and every instance was a **retirement/종료** announcement — a shape the existing rule missed because all of its examples were feature *additions*. Surface B (공지 = 원인 부사구, `이번 GA로 … 사용할 수 있습니다`) hit 5 docs; low, but promoted anyway because the corpus predates the current prompt and the reader saw it in fresh output. Fix: `languages/ko.py` §3's bullet was rewritten from "공지를 주어로 쓰지 않기" to **"공지가 아니라 사실을 서술하기"** with one rewrite recipe — *문장의 출발점을 (1) 시점 부사 "이제"·"{날짜}부터" 또는 (2) 실제로 추가·변경·종료되는 대상으로 바꾼다* — plus the retirement verb (`{날짜}부터 제공이 종료됩니다`, "은퇴"는 retire 직역) and a stage-fidelity line (preview를 "정식으로"로 의역하거나 GA로 승격 금지). Mirrored into `en.py` / `ja.py`. Critically, an **explicit carve-out** was added so the rule does not contradict §2's legitimate `이 업데이트로 TLS 1.0 연결이 차단됩니다` — banning the *availability* frame while allowing the *effect* frame (contradictory prompt rules were the root cause of the earlier ko anti-hedge inconsistency). Mechanical net in `scripts/evaluate_report.py`: 3 regexes (`~하는 내용/공지입니다`, 공지-원인-부사구, 은퇴 동사형), each validated **both ways** — they flag all 19 corpus instances with **0 false positives**, and none of the recommended rewrites trip them. `정식으로 사용` was deliberately **rejected** as a regex (4 docs, and it is correct Korean for a real GA — the defect there is faithfulness, not phrasing). 4 new tests; 557 pass.
  - Blocker found on the way: `tests/test_quality_evaluator.py`, `tests/test_url_validation.py` and `src/main.py` were all uncollectable/unimportable at HEAD because `src/email/service.py` imported `FONT_STACK_SANS`/`FONT_STACK_MONO` that an in-progress refactor had not yet added to `src/email/templates.py`. The committed tests fully specify both stacks, so extracting the constants was a mechanical unblock. `tests/test_email.py` remains red: it also imports `_split_procedure`, which **does not exist anywhere in `src/`** — genuinely unimplemented TDD work, deliberately left alone.
  - **The same half-applied i18n refactor was also silently breaking the digest email at runtime** (found only by actually sending one, 2026-08). Three defects, each invisible to `import src` and to the green test suite: (1) `src/email/service.py` called `markdown_to_html(..., strip_headings=True)` but `templates.py` had no such parameter → `TypeError` at digest-build time; (2) `templates.py` still contained a **stale duplicate `_LABELS` table (83 keys)** plus its own `get_labels()`, so the whole email layer read that instead of the canonical `src/i18n/labels` registry (92 keys) → `KeyError: 'retirement_countdown'`. Both `copilot-instructions.md` and the `email-template` skill already documented the intended end state ("`templates.py` re-exports `get_labels`", "`_LABELS` no longer defined in `src/email/templates.py`") — the code had simply not caught up, most likely a stale-buffer revert. Fix: implement `strip_headings` (drop heading lines; collapse the doubled spacer a stripped heading leaves behind) and replace the 286-line stale table with `from src.i18n.labels import get_labels`. Lesson: **a green suite plus `import src` does not prove the delivery path works** — the only check that caught these was rendering `build_email_content()` **and** `build_digest_content()` against a real result. Do that smoke render before claiming the email path is healthy.
  - Encoding trap while verifying: PowerShell 5.1 `>` redirection writes **UTF-16LE**, so `open(path, encoding="utf-8")` on a redirected log yields garbage and every Korean regex reports a false `clean`. Have the Python script write its own UTF-8 file instead of redirecting, and read it back with `read_file` rather than printing Korean to the cp949 console.

- **A phrase blacklist relocates a phrasing defect instead of removing it (2026-08).** After the announcement-frame and `GA되다` bans shipped, a reader flagged four *new* sentences — `~했다는 점입니다`, `달라지는 지점은`, `~보내는 방식입니다`, `~되었다는 의미이며` — and reported the reports had gotten *worse*. Measurement said otherwise and pointed at the real cause. (1) **No regression**: `방식입니다` ran **0.78/1k Korean chars in the July corpus** vs **0.51/1k** in the post-change digest, and `점입니다` 0.41 vs 0.51 — today's output is at or below the pre-change baseline, and with only 7 occurrences in a 4-update batch the difference is noise either way. The defect was always there; removing the *other* defects just made it the most visible one left. (2) **Real cause — the ban lists never contained these four nouns.** `ko.py` banned 업데이트/기능/성격/항목/수준 (§2) and 성격/형태/변화/구조/셈/해당 (§7(3)) but never 점·지점·방식·의미, so the model migrated to the nearest unbanned nominalizer. (3) **Second cause — §7(3)'s 12 banned expressions had zero mechanical coverage**; a check confirmed none of them appear in `translation_patterns`, and the already-banned `구조입니다` shipped in that same digest. Fix: replaced §2's phrase list with **one constructional test** — *서술어가 의존명사 + 입니다(점·지점·방식·의미·내용·구조·성격·형태·부분·측면·셈·것) 꼴이면 다시 쓰고, 명사 안에 갇힌 동사를 서술어로 끌어올린다* — with four worked rewrites and an explicit **carve-out for definition sentences** (concept boxes are *required* by §8 to end with `~입니다`), plus §7(3) relabelled as a case book for that principle. The Layer-2 net is shape-based rather than phrase-based and mirrors the carve-out (skips `>` blockquote lines). Threshold calibrated against the corpus, not guessed: per-report median is **0**, p90 **1**, max **3**, so `>=2` marks the top ~9%. 561 tests pass. Lesson: **when a reader flags a "new" defect right after a prompt fix, measure the old corpus before believing the regression** — and prefer one test the model applies to every sentence over N phrase bans, because a blacklist only moves the defect to the nearest synonym.

- **Prompt dilution is real and measurable — and the autonomous optimizer reward-hacks (2026-08).** A reader asked whether the recurring Korean phrasing defects came from a mini model, an over-long system prompt, or a weakly-Korean LLM. All three were tested instead of guessed: the report phase runs on **`gpt-5.4`** (not the `gpt-5.4-mini` fast deployment — `_report_node` uses `self.llm`), `_report_node` **does** pass the full `build_system_prompt(language="ko")` so the style guide reaches the model, and subscriber customization (the one path that *does* use the mini model with only the 447-char `TRANSLATION_NOTES`) had **not run** in any of the flagged runs. That left prompt length, which turned out to be the real, quantified effect. New `scripts/optimize_prompt.py` picks a fixed sample of real updates, generates reports, scores them with a deterministic 10-pattern Korean defect metric (`per_1k` Korean chars), asks the primary LLM to rewrite one style-guide section, re-measures, and keeps or reverts. Findings:
  - **Noise floor first.** Three runs of the *identical* prompt on the same 6-update sample gave `per_1k` 1.884 / 2.202 / 2.272 — spread **0.388**. Any A/B delta below ~0.4 on this sample size is meaningless; `--margin` now defaults to 0.4. The loop's accepted change (2.189 → 1.013, delta **1.176**) was 3× the noise, so that one was real signal.
  - **Length costs quality.** Same rules, different length: ko guide 9,492 chars → mean 1.03; 10,304 → 1.64; 11,120 → 2.12. Adding ~1,600 chars of *correct but unrelated* rules cost about **+1.1 defects per 1,000 Korean chars**. Compress an existing rule rather than appending a new one.
  - **Goodhart's law bites immediately.** Told to "keep or shorten the length; delete anything redundant", the LLM's §7 rewrite silently deleted the CSA hand-off ban and every structured-field rule (`additional_checks`, `affected_resources.reason`, `action_items.task`, `relevance_evidence`) — none of which the 10-pattern metric measures, so deleting them was free score. Fixed with a `REQUIRED_ANCHORS` guard that reverts any rewrite dropping an unmeasured rule, plus an explicit instruction not to delete unrelated rules. **Any metric-driven prompt optimizer needs an anchor guard for everything the metric cannot see.**
  - Second bug found by reading the loop's own log: `REVERT` restored the round-0 backup instead of the best-so-far, discarding two accepted improvements. Keep a `ko.py.best` snapshot and revert to that.

- **Truncation, not context rot, was the real context defect — measured before designing the fix (2026-08).** Asked whether adopting Recursive Language Models (RLM, Zhang & Khattab 2025) would help, the answer came from the repo's own logs rather than the paper: across 42 log files / 155 analyses / 1,205 tool executions, the report prompt was a median **99,658 chars (~25-35k tokens)** — well below the 132k-263k **token** band where RLM's 2× gains were demonstrated — while **12.2% of tool results (147/1205) hit the 8,000-char budget ceiling** and **76% of runs (32/42)** contained at least one. Per tool: `find_related_resources` **49/49 (100%)**, `get_service_region_availability` 43/95, `query_azure_resources` 45/206. So AzBrief was not losing information *inside* the model (context rot) but *before* it (a hard cut). Fixes, each **directly observed** with a harness on realistic data before/after:
  - **`find_related_resources` (100% → 0% truncated).** The builder query projected `tags, sku, properties` — raw JSON blobs — and the tool returned `str(result)`, the Python dict repr. 60 storage accounts = **47,804 chars**; the 60th account was invisible in the preview. Now projects identity fields only with `order by type asc, name asc` (stable ordering — the same root cause as the earlier run-to-run non-determinism) and renders grouped by type: **2,809 chars**, needle visible.
  - **`get_service_region_availability`: the verdict was last, so truncation ate the answer.** With 250 resource types the old layout put `### Verdict` past char 8,000 (observed: verdict-last = not in preview; verdict-first = in preview). The verdict also enumerated every resource-type × region pair, growing O(types); it is now a per-region rollup (`koreacentral: ✅ 1/250 resource types available` + the explicit ❌ list, capped at 20) — a constant ~420 chars regardless of provider size, which alone dropped a 90-type provider from 8,116 to 5,619 chars (below the budget entirely).
  - **`src/agent/context_store.py` — RLM's core idea without the REPL.** Results over budget are stored whole and the prompt gets a preview + `[ref=Rn]` + the instruction *"Do NOT conclude that a resource is absent based on this preview alone"*; the new `query_tool_result` tool searches the full text (`search`/`head`/`tail`/`stats`, literal by default, opt-in `regex`). A no-match answer from it is a **confirmed absence**, which is what the report prompt now requires before deferring anything to `additional_checks`. Verified end-to-end through `_execution_node`: a needle past the cutoff is absent from the handle yet retrievable by ref.
  - **Why not the full RLM.** Its canonical instantiation is a model-written Python REPL. The Automation sandbox forbids subprocesses, so the only option would be in-process `exec()` on LLM output — in a process holding tenant-wide `DefaultAzureCredential` and already ingesting untrusted RSS/web content, that is a prompt-injection → RCE chain. The paper also states it has "no strong guarantees about total API cost or runtime", which collides with `run_time_budget_s`. If a real REPL is ever needed, use the managed Foundry Agent Service Code Interpreter (Container App mode only).
  - Store bounds are set for the sandbox's 400 MB ceiling (2 M chars/entry, 16 M total, oldest-first eviction) and `analyze_update` calls `clear_trace(trace_id)` when it finishes. Refs are globally unique so concurrent analyses sharing the singleton store cannot read each other's entries.

- **Live-testing the context store found four defects that unit tests and a synthetic harness both missed (2026-08).** Running `scripts/test_local analyze` against a real 438-resource tenant (12 analyses over 3 runs) exposed:
  - **The store's line-based search was useless for the most-used tool.** `ResourceGraphQueryTool` returned `str(result)` — a Python dict repr on **one line** — so a 1.5 M-char result stored as a single "line". `query_tool_result` matched that line and then re-truncated it to 8 000 chars, i.e. the retrieval promise silently failed. Fixed twice over: `format_rg_result()` now renders **one compact JSON object per line** (so a budget cut lands *between* rows and the result is greppable), and `query_tool_result` handles blob content by returning a **±200-char window around each hit** (`line@offset:`) instead of the whole line, with character-sliced `head`/`tail`/`stats`.
  - **The store lied about completeness.** `MAX_ENTRY_CHARS = 400_000` silently capped a 1 542 887-char result at 26% and `query_tool_result` still answered *"confirmed absence — not a truncation artifact"*. That is worse than the original truncation because it manufactures false confidence. `StoredResult` now records `original_chars` and exposes `is_partial`; a capped entry reports *"absence is NOT confirmed"*. Caps raised to 2 M/entry, 16 M total — sized against the observed real-world maximum, still ~4% of the sandbox's 400 MB.
  - **Pydantic ate the class constants.** `BaseTool` is a Pydantic model, so a leading-underscore class attribute resolves to a `ModelPrivateAttr` — `cls._MAX_LINE_CHARS` in a `@classmethod` raised `TypeError: '<=' not supported between 'int' and 'ModelPrivateAttr'` (it works via `self.` only). Tool tuning constants belong at **module level**, not on a `BaseTool` subclass.
  - **The agent never called `query_tool_result` — 0 times in 12 live analyses.** Truncation happens during *execution*, and the only phase that can act on it is evaluation → `_revise_tasks_node`; the report phase has no tools. But `EVALUATION_PROMPT` carries a hard "bias toward sufficient", and every evaluation returned `sufficient`, so the revision path never opened.
  - **A prose exception loses to a repeated structural instruction.** The first fix added a well-argued paragraph ("judging absence from a preview is a factual error, not a judgement call") plus a `context` method on the `AnalysisTask` Literal and a `query_tool_result` recipe in `REVISE_TASKS_PROMPT`. It changed nothing: an adversarial live probe — a TLS 1.0 retirement whose single non-compliant account sat past the cut while every visible account was compliant — still returned `sufficient`, reasoning that "resource identification was completed ... and included the relevant configuration field `minimumTlsVersion`". The sufficiency bias is stated three times and the model weighted it higher. What worked was moving the rule into the **structured artifact the model must emit**: an `Evidence Completeness` row in the Evaluation Criteria table plus an `evidence_complete` key in the `coverage` output. Same probe after that change: verdict `partial`, `missing_aspects: ['evidence_complete']`, suggestions naming `query_tool_result(ref="R1", pattern="TLS1_0")` → revision emitted 3 sub-query tasks → execution **retrieved the hidden `stlegacyauth0871` account** and separately reported TLS1_1 as a *confirmed* absence. **Rule of thumb: to change an LLM's decision, edit the structured field it has to fill in, not the prose around it.**
  - **Measured cost of that criterion** (same 4 updates, 2026-08-04, before vs after): evaluations `4 sufficient` → `4 sufficient + 2 partial`; revisions 0 → 2; tool calls 31 → 43 (10 of them `query_tool_result`, 198-865 chars each, no Azure call); LLM calls 13 → 17; average analysis **94s → 104s (+11%)**. Both `partial` verdicts were substantively right (confirm a `VirtualNetworkApplianceSubnet` exists in a truncated subnet inventory; confirm `privateEndpointVNetPolicies` across truncated VNets), each fired **once** and then resolved to `sufficient` — bounded, not a loop. Affected-resource counts were unchanged on those four, so the gain there is a *verified* conclusion rather than an assumed one; the case where it changes the answer is the TLS probe above.
  - `TrajectoryEvaluator` now counts KQL tasks by `tool_name in KQL_TOOL_NAMES` instead of the LLM-supplied `method` label — revision tasks come back labelled `"kql"` whatever tool they actually call, which inflated `kql_tasks` and diluted `kql_failure_rate`.
  - Because retrieval depends on the agent choosing to retrieve, the durable fix is to make the **preview itself lossless at the summary level**. `find_related_resources` now emits a complete **type-distribution table before the detail rows**, the same "verdict first" principle that fixed region availability: on the real tenant, `["microsoft"]` returns 438 resources / 35 714 chars, and all **78** resource-type counts sit inside the 8 000-char preview.
  - Live before/after on the real tenant: `find_related_resources(["storage"])` 57 377 → 1 613 chars (last account invisible → visible); `["network"]` 756 871 → 16 788; `["compute"]` 65 094 → 3 553. `get_service_region_availability` verdict now lands at char ~155 for every provider (Microsoft.Network: 199 resource types, 28 337 chars, verdict `koreacentral: ✅ 133/199 resource types available`). Across 91 tool calls, 19 oversized results were stored and **0** were capped.
  - Tooling trap hit while doing this: `Get-Content -Raw` + `[System.IO.File]::WriteAllText` **corrupted every non-ASCII character** in `prompts/phases.py` (`—`, `✅/❌`, box-drawing `├──` → `??`) and converted CRLF→LF, because `Get-Content` decodes with the console codepage (cp949). Caught by reading `git diff` after the edit. Recovered with `git checkout --` and re-applied through an assertion-guarded Python script (explicit UTF-8, `ast.parse()`, CRLF restored). **Never round-trip a UTF-8 source file through PowerShell text cmdlets.**

- **Category is the frame: a new capability cannot have "no operational impact" (2026-08).** A reader flagged that reports for new features and new services argued about *operational impact* at all — a category error, since the useful question for a **new capability** is which opportunity it creates for the reader's role. Measured across 267 corpus reports before changing anything: **235/237 (99%)** Capability-category reports (`new_feature`, `new_service`, `region_expansion`, `preview`, `sdk_tooling`) filled `operational_impact`, and a calibrated regex found the absence-of-impact tautology in **156/240 (65%)** of them ("기존 운영에 영향은 없습니다", "지금 검토하지 않아도 운영 리스크는 없습니다", "미도입 자체가 운영 위험으로 이어지지는 않습니다"). Three causes, all in-repo:
  - **The prompt taught it.** `report/base.py` analysis-body section 3 listed "이 업데이트로 기존 운영에 영향은 없습니다" as a recommended no-action phrasing, and section 4 mandated a "risk of inaction" half-paragraph that, for a new capability, can only be filled with the tautology. Same contradictory-instruction failure mode as the earlier ko anti-hedge bug.
  - **The evaluator rewarded it.** `scripts/evaluate_report.py` §2.2 scored `impact_summary` as `min(4, 1 + filled)` — pure quantity — so filling `operational_impact` with "영향 없음" was free score. Now hollow dimensions score nothing for Capability categories; an "unaffected" statement stays legitimate for Change categories, where it is the report's whole point.
  - **The renderer reinforced it.** The email section and the G-Eval judge's rendered report both titled the block "영향 분석" regardless of category.
  Fix: a `Report Frame Follows the Category` block in `REPORT_BEFORE` splitting the eight categories into **Change** (`retirement`/`feature_change`/`pricing` → impact and risk) and **Capability** (→ opportunity: what becomes possible, for which named candidates, at what adoption cost, and which operational responsibility owns it); analysis-body sections 3-4 rewritten per family; each Capability category's `impact_summary` guidance rewritten as *gains*, with an explicit "an empty string beats 영향 없음"; `subscriber.py` STEP 2 told to make the opportunity concrete for the role's remit without inventing facts; `format_impact_section_html()` and `render_report_markdown()` switch the heading to `활용 기회` (new `opportunity_analysis` label, ko/en/ja) for Capability categories. The Layer-2 net was folded into the existing `update_category` score item rather than added as a new one, so the evaluator keeps its 100-point total (two tests assert it). Calibrated both ways: 0 false positives on adoption-cost phrasings ("기존 구성 변경 없이 추가할 수 있습니다"), 0 misses on the six known-bad sentences, and 119/191 real reports flagged on re-run. 649 tests pass (`tests/test_email.py` stays red on the pre-existing missing `_split_procedure`). Honest limit: the prompt half is verified by assembly + unit tests only — no quality claim without a live scored run.

- **The 개요 opened with the environment's verdict because the prompt told it to (2026-08).** A reader flagged reports whose `detailed_analysis` began with "현재 환경에는 이 기능을 적용할 **ExpressRoute virtual network gateway**가 없습니다." — the reader learns the conclusion of an analysis whose subject has not been named yet. This was **not** model drift: `base.py`'s self-check literally prescribed it ("Applies to the `not_relevant` case too: *현재 환경에는 이 기능을 적용할 리소스가 없습니다*"), `ko.py` §3 sanctioned it ("영향받는 리소스가 없을 때만 … 로 열어도 됩니다"), and `REPORT_BEFORE`'s Capability rule supplied the ExpressRoute wording. All three contradicted the `CRITICAL ORDERING RULE` ("Never present … before explaining the update itself") sitting 100 lines away — the same contradictory-instruction failure mode as the earlier ko anti-hedge bug. Measured before fixing: **27/332 corpus docs (8.1%)** open with an environment verdict, of which 13 are `not_relevant` and 14 `opportunity` (where "즉시 조치할 항목이 없습니다" is also the already-banned Capability tautology). Fix: deleted the three sanctioning passages, stated the positive rule once in the ordering block, and pinned the Capability precondition sentence to the environment paragraph. Layer-2 net added to `translation_avoidance` next to the existing announcement-frame check — the regex requires **both** an environment-scope subject *and* an absence predicate, calibrated both ways: it flags all 28 corpus instances (including the "기준으로는" variant the first draft missed) and the reader's live sentence, with 0 hits on the recommended rewrites. A broader "any 환경-subject opening" variant was rejected: +1 real catch but it false-positives on the legitimate "현재 환경의 Storage Account 22개 중 3개가 TLS 1.0을 허용합니다". 649 tests pass. Honest limit: prompt-text changes are verified by assembly + unit tests only; no quality claim without a live scored run.

- **The email report is now responsive — hybrid layout, not media queries alone (2026-08).** The card was a hardcoded `width="640"` table, so on a phone the whole report scaled down (unreadable 6-7px text) or scrolled sideways. Fixed with the three-layer hybrid that email HTML requires, because no single mechanism works everywhere: (1) **fluid card** `width="100%"` + `max-width: 640px` — the only layer that survives clients which strip `<head><style>` (Gmail app with a non-Gmail account), (2) **`_RESPONSIVE_STYLE`** `@media` blocks at 640px/400px in `<head>` for the layout changes fluid width cannot express, and (3) an **MSO ghost table** (`<!--[if mso]><table width="640">`) around the card, because Windows Outlook's Word engine ignores *both* `@media` and `max-width` and would otherwise stretch the card across a maximized window. Media queries must key off **classes** (`azb-pad`, `azb-stack`/`azb-stack-tail`, `azb-col-metric`, `azb-col-reason`, `azb-qd`, `azb-outer`) — `!important` cannot override an inline `style=""` attribute without a selector, and every section `<td>` in this codebase carries its padding inline. The digest also gained `_CLIENT_COMPAT_STYLE`, which it had never included despite embedding the same section formatters (so `.azb-cli` long commands had no `word-break` there). **Live-verified in a real browser** at 375px (card 348px, `scrollWidth` 360 < 375 → no horizontal scroll, gutters 12px, quick-decision cells `display:block`, summary badge stacked) and 1200px (card exactly 640px, gutters back to 32px, quick-decision back to `table-cell` → no desktop regression). Lesson: for email, verify the *rendered* box metrics at several widths — asserting the CSS text is present only proves the rule shipped, not that the layout reflows.
  - `tests/test_email.py` is still uncollectable at HEAD (it imports `_split_procedure`, which does not exist — pre-existing TDD work), so the new responsive tests were validated by running identical bodies against the real `conftest` fixtures in a throwaway test file (3 passed) before being written into `test_email.py`; the rest of the suite is green (649 passed with `--ignore=tests/test_email.py`).
  - **Sending a real test email found a defect the box-metric assertions missed.** Below 400px the impact-dimension label rendered as a vertical strip (보/안) because `.azb-impact-label { display: block }` on a single `<td>` triggers CSS anonymous-table-cell fixup: the sibling stays `table-cell`, so the block cell shrinks to zero-width and the Korean label wraps per character. Never make one `<td>` in a row `display: block` — stack **all** cells in the row (as `.azb-qd` does) or keep the row a table and just let the column shrink (`width: 1%` + `white-space: nowrap`, which is what shipped). Only a rendered screenshot caught it; every numeric assertion still passed.
  - **Responsive is not only about shrinking.** `min-width` queries grow the card (640 → 760 @800px → 900 @1100px) so desktop width goes to content instead of the gray backdrop, and at 1100px the `azb-impact` rows pair up 2×2 (`display: inline-table` on `<tr>` — safe here because it is progressive enhancement: clients that ignore it keep the stacked rows). Measured on the digest: document height 3828px @640 → 3597px @1280. The **900px cap is a readability limit, not a technical one** — Korean glyphs are full-width, so 640px ≈ 44 chars/line and 900px ≈ 62; past that the line length costs more than the recovered space is worth.

- **"Before this existed you had to X" is now a mandatory part of a Capability-family report (2026-08).** A reader asked that reports introducing a new feature or service say what an administrator had to do to get the same outcome while the capability did not exist, and what replaces it now. The rule was already in `report/base.py` section 1 as one soft bullet ("If this is a new capability, explain what was impossible before") — soft enough that reports routinely skipped it. Promoted to MANDATORY, scoped to `new_feature` / `new_service` / `preview`, with the "before" enumerated concretely (a self-operated component, a manual procedure, a third-party product, an accepted limitation), a no-fabrication clause (take it from the docs or update text; when the prior workaround is genuinely unknown, name the limitation the capability removes instead of guessing), and an explicit **anti-template** clause — position and wording must vary, because a fixed "previously X, now Y" sentence in every report is the same monotony defect the ko style guide already fights. Mirrored as the problem statement in `report/categories.py` (`new_feature`/`new_service` item 1, `preview` item 2) and as a phrasing rule with worked GOOD/BAD examples in `languages/{ko,en,ja}.py` — one place per file, not repeated, given the measured dilution cost (~+1.1 defects/1k Korean chars per ~1.6K prompt chars). Honest limit: this is a prompt-text change, verified only by assembly (`build_system_prompt` per language + `build_report_prompt(category=...)`) and 649 passing tests; no quality claim without a live scored run.

- **Second deployment profile: the checkpoint is the whole design (2026-08).** The enterprise topology moved the analysis out of the Automation sandbox, which raised the question of who owns the "analysed up to" watermark. The first answer kept the **caller** as owner — the runbook passed `since`, polled, and advanced its Automation Variable only on a `completed` status — which worked but bought a poll loop, a 404-on-restart failure mode and an Automation Account nobody otherwise needed. The scheduler is now a **Container Apps Job** running the same image (`python -m src.scheduler`), and the checkpoint is a **blob** the run itself commits, so the handshake disappeared without weakening the guarantee:
  - Only the **contiguous prefix** watermark is ever stored (`_WatermarkCursor`) — the newest finished update is never safe under concurrency.
  - `advance()` refuses to move backwards and guards the write with an ETag (`If-Match`, or `If-None-Match: *` when creating), so two runs racing cannot rewind the window.
  - A read or write failure is **swallowed**: not advancing repeats a window, while failing the run would lose the digest. Same reasoning as before, applied to a different owner.
  - The `RunStore` stays deliberately non-durable. Losing it now only means a poller stops seeing a run; the checkpoint is elsewhere.
  - `replicaRetryLimit: 0` on the job is deliberate — a failed execution did not advance the checkpoint, so the *next schedule* re-covers the window rather than paying twice in one night.
  - Blob access uses the **REST API over httpx** with an Entra token, not `azure-storage-blob`: the fat wheel that ships to the Automation sandbox stays unchanged, and the store touches the blob twice per run.
  - The identity gets `Storage Blob Data Contributor` **scoped to the state account only**. A checkpoint is not a secret and must not earn write access to the vault that holds the real ones.
  - `automation/runbook_python.py` keeps its `enterprise` branch (stdlib-only, `https`-only base URL) for driving the topology from an existing Automation Account, but the template no longer creates one.
- **Bicep catches what hand-written ARM JSON cannot, but only for what it type-checks (2026-08).** `infra/enterprise/main.bicep` compiles to `infra/azbrief-enterprise-deploy.json`; **never hand-edit the JSON**. Two errors surfaced only at compile time and would have failed a live deployment: `BCP178` twice, because a `for` loop's source array cannot reference a runtime value (`containerApp.properties…fqdn`, `managedIdentity.properties.clientId`) — copy loops over Automation Variables had to be unrolled into individual resources; and `BCP318` on `secretAdminClientSecret.properties.secretUri`, since a conditional resource is null-typed (fixed with the `!` null-forgiving operator inside the branch that already guarantees it exists). Two things Bicep does **not** check: built-in role GUIDs (verified against the Learn built-in-roles page — note *Foundry User* is the renamed *Azure AI User*, ID `53ca6127-db72-4b80-b1b0-d745d6d5456d`, unchanged by the rename) and `enablePurgeProtection: false`, which ARM rejects outright — the property must be `true` or absent, hence `enableKeyVaultPurgeProtection ? true : null`.
  - Honest limit: `az deployment group validate` could not be run in this environment (the subscription requires interactive MFA), so the template is **statically** validated only. Run a preflight before the first real deployment.
- **Fail-closed beats a configurable default for an admin surface (2026-08).** `/admin` returns **404**, not 403, whenever `ADMIN_UI_ENABLED` is false — a disabled console should not advertise that it exists. Turning it on requires *two* independent things in the template (`adminEntraClientId` + secret **and** `adminAllowedPrincipals`); satisfying only one leaves it off, because an Entra-authenticated stranger is still not an administrator and an allow-list without sign-in is decoration. Identity comes from the Container Apps EasyAuth sidecar (`X-MS-CLIENT-PRINCIPAL*`), which strips inbound copies of those headers — that guarantee holds only while ingress is the sole path to the container, which is why `ADMIN_REQUIRE_AUTH=false` is documented as local-development-only. The page itself is server-rendered with **zero external references** so it works behind a locked-down egress policy, and its inline `<style>`/`<script>` are bound to a per-request CSP nonce rather than `unsafe-inline`.

| Problem | Cause | Fix |
|---------|-------|-----|
| `ImportError` after adding dependency | Not in `requirements.txt` | Add to both `pyproject.toml` and `requirements.txt` |
| `SyntaxError` in PowerShell inline Python | f-string with `["key"]` in `-c` | Write to a `.py` file instead of inline |
| Tests pass locally, fail in the container | Missing dependency in the image | Add it to `requirements.txt` and rebuild |
| `KeyError` rendering email | Label key missing from `src/i18n/labels/ko.py` | Add it to `ko.py` (canonical set); other languages backfill automatically |
| KQL `ParserFailure` | Agent used `join`/`let`/`mv-expand` | Add pattern to `_rule_based_fix()` in `tools.py` |
| `429` / `529` overload | Too many concurrent LLM calls | Automatic backoff handles this; reduce batch size if persistent |
| `ECONNRESET` on Azure OpenAI | Stale keep-alive connection | Automatic reconnect; disable connection pooling if persistent |
| Email not delivered | No `COMMUNICATION_SERVICES_CONNECTION_STRING` | Expected — falls back to console output |
| `get_settings()` returns stale values | `@lru_cache` not cleared | Call `get_settings.cache_clear()` in test setup |
| Docker build fails | `lxml` dependency added | Remove it — use `html.parser` only |

---

## Quality Assurance Checklist

When implementing changes, verify against this checklist:

```
□ Architecture
  □ State transitions are explicit and typed (no implicit mutation)
  □ Each loop iteration produces new state (immutable transitions)
  □ Agent loop has termination guardrails (max_iterations, revision limits)
  □ Diminishing returns detection prevents wasteful iterations

□ Resilience
  □ Transient API errors use exponential backoff + jitter
  □ Circuit breaker tracks consecutive failures
  □ Background tasks fail-fast on overload (no retry amplification)
  □ LLM-assisted tool repair with model fallback chain
  □ Multi-turn output recovery for token limit hits
  □ Graceful degradation when services are unavailable
  □ Model fallback on consecutive 529 overload errors
  □ Stale connection detection (ECONNRESET/EPIPE → reconnect)
  □ Error withholding: recoverable errors not surfaced until recovery fails

□ Context Management
  □ Tool results over budget (8,000 chars) are stored whole and exposed via a queryable ref
  □ Prompt has static (cacheable) / dynamic (per-analysis) separation
  □ KQL knowledge base is consulted before exploratory queries
  □ JSON parsing uses multi-strategy fallback (never crashes on malformed output)

□ Safety
  □ External URLs validated against allowed domains whitelist
  □ Tool inputs validated via Pydantic schemas
  □ Prompt injection warnings in system prompt

□ Observability
  □ Every LLM call logged with phase, elapsed_s, token counts, model
  □ Every tool execution logged with tool name, attempt, elapsed_s, result_chars
  □ State transitions logged with trace_id
  □ Total analysis time and token usage tracked

□ Tool Concurrency
  □ Tool calls partitioned into safe (parallel) and unsafe (serial) batches
  □ isConcurrencySafe() failure treated as unsafe (fail-closed)
  □ Results returned in original order regardless of completion order
```
