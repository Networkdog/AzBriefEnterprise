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

AzBrief is a Container Apps application and Job that run a local LangGraph harness. It invokes persisted Foundry **Prompt Agents** through the classic `AIProjectClient(...).agents` threads/runs data plane. It is not itself a Foundry Hosted Agent container.

Keep that topology unless a measured requirement justifies migration. A Hosted Agent migration must account for the scheduler, email delivery, admin API, checkpoint ownership, managed identity RBAC, VNet access, and long-running batch behavior.

## Procedure

1. Read the [current assessment](./references/assessment.md) and the current official Microsoft Foundry Agent documentation.
2. Identify the controlling path, not only configuration wiring:
   - `src/agent/foundry_backend.py`
   - `src/agent/analyzer.py`
   - `scripts/provision_foundry_agents.py`
   - `infra/enterprise/main.bicep`
3. Classify every agent as a runtime phase role or an optional enrichment stage. One agent should own one clear responsibility.
4. Verify local tool calls are executable. Prompt Agent client-side `tools=`/`bind_tools()` are not automatically honored; AzBrief uses an allow-listed JSON bridge for planning tools.
5. Require structured, evidence-addressable outputs between enrichment agents. Review rejection must remove the rejected claim and actions that depend on it.
6. Keep loop state per analysis. Never store diminishing-return counters, evidence, or rewrite feedback in shared analyzer fields used by concurrent runs.
7. Fail closed when planning/evaluation/report Agent calls fail. Never translate an evaluator error into `sufficient`.
8. Preserve transient error details so outer retry and circuit-breaker logic can classify 429/503/529 correctly.
9. Run `python -m scripts.provision_foundry_agents --check` before enabling enrichment. Research and impact require server-side tools; stale instructions or missing agents fail the check.
10. Re-score with the rubric below. Make one focused improvement, validate it, and repeat until the remaining gaps require a platform migration, preview dependency, or live resource change.

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
python -c "import src"
az bicep build --file infra\enterprise\main.bicep --outfile infra\azbrief-enterprise-deploy.json
python -m scripts.provision_foundry_agents --dry-run
python -m scripts.provision_foundry_agents --check
```

`--check` is read-only but requires Foundry data-plane access. Do not run create/update/delete operations as part of a unit test. Tests must override every `FOUNDRY_*` environment variable with an explicit value, including an empty string, so `.env` cannot leak into the test process.

## Skills and Toolbox

Foundry Skills and toolbox skill discovery are public preview. Do not make them a production prerequisite without explicit approval and a rollback path. When adopted, use the Foundry versioned Skills API and toolbox MCP resource discovery; do not copy the full skill body into every system prompt. Pin immutable versions for production and promote a tested version to default.
