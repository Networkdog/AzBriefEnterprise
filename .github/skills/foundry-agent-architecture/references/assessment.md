# Microsoft Foundry Agent Architecture Assessment

Date: 2026-08-27

## Classification

AzBrief is a Container Apps application and scheduled Job that host a LangGraph Plan-Execute-Evaluate harness. The harness invokes persisted Microsoft Foundry Prompt Agents through the classic `azure-ai-projects` / `azure-ai-agents` threads-and-runs data plane.

It is not a Foundry Hosted Agent. A Hosted Agent packages this custom orchestration code into a Foundry-managed container with a managed endpoint, per-session sandbox, scaling, identity, and protocol-level tracing. AzBrief currently gets those runtime properties from Container Apps instead.

Keeping the external harness is deliberate for now: the scheduler, admin API, email transport, durable digest checkpoint, long run budget, and Azure SDK tools already share one image and identity. Moving only the analyzer into a Hosted Agent would leave those components in Container Apps while adding a second compute/runtime boundary and a second RBAC identity.

## Rubric

Each dimension uses a 1-5 anchored scale:

- 5: theoretical ideal, including live proof under failures, scale, security boundaries, and upgrades
- 4: production-excellent, with complete local evidence and no known material defect
- 3: adequate but with meaningful gaps
- 2: brittle or only partially used
- 1: harmful or misleading

The ceiling is intentionally unreachable while a known gap remains.

| Dimension | Before | After | Evidence after improvement |
|---|---:|---:|---|
| Runtime/type fit and terminology | 2.0 | 4.0 | Prompt Agent and Hosted Agent are distinguished in code and docs |
| Role specialization and instructions | 2.5 | 4.5 | Optional planner/evaluator/reporter/codex/fast roles, each with standing instructions and primary fallback |
| Tool and skill utilization | 1.5 | 3.5 | Planning local-tool bridge works; enrichment is gated on server tools; toolbox Skills remain preview and are not a production dependency |
| Multi-agent contracts and provenance | 2.0 | 4.5 | Structured status/claims/evidence/gaps outputs; stable claim IDs; review rejection cascades to dependent actions |
| Harness and loop correctness | 2.0 | 4.5 | Evaluator failures and malformed verdicts terminate with `model_error` instead of masquerading as `sufficient` |
| Concurrency and evidence isolation | 2.0 | 4.5 | Diminishing-return history is in AgentState; evidence snapshots are attached privately to each AnalysisResult |
| Retry, cleanup, and discovery resilience | 2.5 | 4.0 | Transient errors propagate, one-shot threads/clients close, roster refreshes once on cache miss |
| Observability and evaluation | 4.0 | 4.2 | Existing trace, trajectory, action-verification, and G-Eval layers retained; enrichment claim counts are logged |
| Deployment/readiness | 2.0 | 4.5 | Phase names wired to App and Job, Bicep compiles, `--check` verifies agents/instructions/tools, enrichment defaults off |
| Current-platform upgrade path | 2.0 | 3.0 | Classic API risk is documented; migration is still unimplemented |
| **Weighted mean** | **2.30/5 (46%)** | **4.12/5 (82.4%)** | Production-capable locally; live roster and current-platform migration remain |

## Improvement Iterations

1. Restored the planning tool loop with an allow-listed JSON bridge because Prompt Agent client-side `bind_tools()` was a no-op.
2. Deleted one-shot Agent threads and closed clients/credentials; preserved transient errors for outer retry logic; refreshed a stale roster on name miss.
3. Replaced free-text enrichment with structured, evidence-addressable claims. Review now removes rejected claims and dependent actions.
4. Added explicit `model_error` termination for evaluator outages and invalid JSON instead of forcing a report.
5. Added optional planner, evaluator, and reporter Prompt Agents with phase-specific standing instructions and primary fallback.
6. Moved diminishing-return history into AgentState and attached evidence snapshots to each AnalysisResult to stop concurrent cross-update contamination.
7. Added a read-only roster validator and disabled enrichment by default until research/impact tools and current instructions are verified.
8. Rejected one Agent name assigned to multiple purposes and reduced provisioning discovery to one roster read.

Every iteration has focused unit tests. The Bicep source compiles into `infra/azbrief-enterprise-deploy.json` without warnings.

## Live Read-Only Finding

A read-only SDK inspection on 2026-08-27 found:

- `azbrief-research`, `azbrief-impact`, `azbrief-action`, and `azbrief-review` exist.
- All four have zero server-side tools.
- The configured `azbrief-primary` Agent does not exist.

No Azure resource was changed during this inspection. The current live project is therefore not ready to run the revised application. Provision/update the roster, attach documentation tools to research and Azure resource tools to impact, run `python -m scripts.provision_foundry_agents --check`, and only then set `enableFoundryEnrichmentAgents=true`.

## Remaining Gaps

### Classic Agent API

The repository uses `azure-ai-projects==1.1.0b4` and `azure-ai-agents==1.2.0b6` with mutable `create_agent`/`update_agent` and `create_thread_and_process_run`. Current Foundry documentation distinguishes versioned Prompt Agents and code-based Hosted Agents, while classic Agents are on a retirement path.

A migration must be handled as a separate compatibility project: build a parity harness against the current Responses/Agent Framework surface, compare tool-call behavior and structured outputs, deploy a canary Agent version, and keep the existing runtime as rollback until the same evaluation dataset passes.

### Foundry Skills

Versioned Foundry Skills and toolbox discovery are public preview. The production app does not depend on them. When the feature is approved, attach skills through a versioned toolbox and load them progressively through MCP resources. Do not duplicate skill bodies in each Agent instruction.

### Hosted Agent

A Hosted Agent migration is not automatically an improvement. Adopt it only when measured requirements justify Foundry-managed per-session state, scaling, replay, or protocols and when the scheduler/checkpoint/email/admin boundaries have an explicit owner. Until then, Container Apps remains the simpler host for this batch-oriented harness.

## Validation Commands

```powershell
& .\.venv\Scripts\Activate.ps1
python -c "import src"
python -m pytest tests\ -o "addopts=" -x
az bicep build --file infra\enterprise\main.bicep --outfile infra\azbrief-enterprise-deploy.json
python -m scripts.provision_foundry_agents --dry-run
python -m scripts.provision_foundry_agents --check
```
