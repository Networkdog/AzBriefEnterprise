---
name: foundry-agent-architecture
description: 'Audit and improve AzBrief Microsoft Foundry architecture. Use when: Hosted Agent, Prompt Agent, multi-agent, agent loop, agent harness, Foundry skills, toolbox, agent instructions, roster validation, planning evaluation reporting agents, classic Agents API migration, unnecessary agent implementation.'
---

# Foundry Agent Architecture

## When to Use

- Auditing whether AzBrief uses Prompt Agent or Hosted Agent capabilities correctly
- Changing `src/agent/foundry_backend.py`, the Plan-Execute-Evaluate graph, runtime agent roles, or the enrichment roster
- Adding Foundry tools, toolbox skills, standing instructions, or Agent Service evaluation
- Reviewing agent-loop reliability, concurrency isolation, or unnecessary orchestration

## Architecture Truth

AzBrief's complete LangGraph Plan-Execute-Evaluate-Report harness and subscriber customization run in the `azbrief-analysis-hosted` Microsoft Foundry **Hosted Agent**. The Hosted Agent manages immutable **Prompt Agent** versions through `azure-ai-projects` 2.5+ and invokes them through the project-scoped Responses API with `agent_reference`.

Container Apps is the control plane only: FastAPI/Admin, authenticated MCP, RSS selection, scheduler, forward-only checkpoint, and email delivery. `src/main.py` and `src/scheduler.py` instantiate `HostedAgentAnalyzer`, never `AzureUpdateAnalyzer`. Missing Hosted Agent configuration fails closed; do not reintroduce an in-process fallback.

The two runtimes have separate identities. The Container Apps UAMI owns Key Vault, checkpoint, email, Admin, and MCP control-plane access. The Hosted Agent's automatically created identity owns Azure evidence queries and Prompt Agent/model access. Never grant tenant evidence permissions to the wrong identity merely because the Container App previously ran the graph.

## Procedure

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
3. Classify every agent as a runtime phase role or an optional enrichment stage. One agent should own one clear responsibility.
4. Verify local tool calls are executable. Prompt Agent client-side `tools=`/`bind_tools()` are not automatically honored; AzBrief uses an allow-listed JSON bridge for planning tools.
5. Require structured, evidence-addressable outputs between enrichment agents. Review rejection must remove the rejected claim and actions that depend on it.
6. Keep loop state per analysis. Never store diminishing-return counters, evidence, or rewrite feedback in shared analyzer fields used by concurrent runs.
7. Fail closed when planning/evaluation/report Agent calls fail. Never translate an evaluator error into `sufficient`.
8. Preserve transient error details so outer retry and circuit-breaker logic can classify 429/503/529 correctly.
9. Run `python -m scripts.provision_foundry_agents --check` before enabling enrichment. Provisioning owns exact research/impact FunctionTools and strict stage schemas; missing, stale, or retired app-owned definitions fail the check. Non-app-owned managed tools are preserved.
   Foundry adds a trailing slash to persisted MCP URLs and wraps allowed tool names in `allowed_tools.tool_names`; canonicalize these service forms before drift comparison.
10. Enforce evidence precedence in managed tools. Research uses Microsoft Learn MCP first and Web Search only as a supplement. Impact uses the Entra-authenticated, read-only Azure MCP Server first and local FunctionTools only for a specific gap. Web Search is never tenant-state evidence.
11. Keep Azure MCP isolated in its own Container App and identity. Use the official image, HTTPS, incoming Entra authentication, `single` mode, and `--read-only`; grant only subscription Reader. Never enable dangerous auth or elicitation bypasses.
12. Keep current Agent Service contracts: immutable Prompt Agent `create_version`, Hosted Agent direct-code deployment through `azure.yaml`, `responses.create`, and one-shot analysis requests. Preserve unrelated managed tools when publishing a new instruction/model version and replace app-managed server tools when their URL, connection, or policy drifts.
13. Keep the wire contract strict and versioned. `HostedAnalysisRequest` and `HostedCustomizationRequest` carry complete domain payloads; responses must match both `trace_id` and `operation`. Invoke the dedicated Responses endpoint with `?api-version=v1` and `store=false`: AzBrief is one-shot and does not need the resilient task subsystem. Never send Python object reprs across the boundary.
14. Prevent recursion. `src/hosted_agent.py` maps non-reserved `AZBRIEF_PROMPT_*` aliases and clears `foundry_hosted_agent_name` before constructing `AzureUpdateAnalyzer`.
15. Keep the AzBrief `/mcp` control-plane surface distinct from the Azure MCP evidence server. `/mcp` uses official SDK v2 Streamable HTTP, validates `X-API-Key` before payload parsing, exposes bounded AzBrief tools, and delegates full analysis to the Hosted Agent.
16. Treat `/app` as read-only in Hosted Agent code deploys. Persist optional history/pattern state under `$HOME`, and keep every optimization write best-effort so a storage problem cannot invalidate a finished report.
17. Re-score with the rubric below. Make one focused improvement, validate it, and repeat until remaining gaps require a preview dependency or live resource change.

## Impossible-Perfect Rubric

Use a 1-5 anchored scale for each dimension. `5` is a theoretical ideal with live proof across failures, scale, security, and upgrades; `4` is production-excellent. Never award 5 while any known gap exists.

- Runtime/type fit and terminology
- Role specialization and standing instructions
- Tool and skill utilization
- Multi-agent contracts and provenance
- Harness/loop correctness and fail-closed behavior
- Concurrency and tenant-evidence isolation
- Retry, timeout, cleanup, and roster refresh behavior
- Tracing, trajectory evaluation, and quality evaluation
- Deployment readiness and configuration drift detection
- Upgrade path to current Agent Service, Hosted Agents, and toolbox Skills

Record both the score and concrete evidence. A score change without a test, trace, diff, or live read-only check is not an improvement.

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
python -m scripts.provision_foundry_agents --check
```

`--check` is read-only but requires Foundry data-plane access. Do not run create/update/delete operations as part of a unit test. Tests must override every `FOUNDRY_*` environment variable with an explicit value, including an empty string, so `.env` cannot leak into the test process.

## Skills and Toolbox

Foundry Skills and toolbox skill discovery are public preview. Do not make them a production prerequisite without explicit approval and a rollback path. When adopted, use the Foundry versioned Skills API and toolbox MCP resource discovery; do not copy the full skill body into every system prompt. Pin immutable versions for production and promote a tested version to default.
