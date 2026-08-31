# Microsoft Foundry Agent Architecture Assessment

Date: 2026-08-30

## Classification

AzBrief is a Microsoft Foundry Hosted Agent application with a Container Apps control plane. `src/hosted_agent.py` owns the complete LangGraph Plan-Execute-Evaluate-Report harness, bounded report-quality correction, and subscriber customization. The Hosted Agent is the only orchestrator. It invokes six distinct immutable Prompt Agent roles through the project-scoped Responses API: coordinator, Resource Graph, Azure MCP, Azure API, report writer, and quality reviewer.

Resource Graph, Azure MCP, and Azure API specialists run concurrently and return strict role-prefixed claims plus explicit gaps. Their tool surfaces are disjoint. The report writer runs after evidence completeness; the quality reviewer owns evidence evaluation, G-Eval, one bounded rewrite request, and action safety. Hosted startup rejects an incomplete or duplicate specialist roster.

The Container App and scheduled Job no longer construct `AzureUpdateAnalyzer`. They own RSS selection, the immutable canonical analysis archive, the durable checkpoint, email delivery, Admin/Archive/API, and an authenticated MCP Streamable HTTP surface, and delegate analysis through `HostedAgentAnalyzer` using a strict versioned request/response contract.

This is an intentional two-identity boundary. The Container Apps UAMI owns control-plane resources; the Hosted Agent's dedicated Entra identity owns Prompt Agent/model invocation and tenant evidence access. The code migration, local contracts, remote build, endpoint invocation, and one full analysis are verified in the `hosted-dev` environment.

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
| Runtime/type fit and terminology | 2.0 | 4.6 | Complete graph runs behind a Hosted Agent contract; Container Apps is control-plane only |
| Role specialization and instructions | 2.5 | 4.7 | Six required roles with unique names, scoped standing instructions, and no cross-specialty fallback |
| Tool and skill utilization | 1.5 | 4.7 | Resource Graph and Azure API use disjoint Pydantic-derived FunctionTools; Azure MCP receives only the managed read-only MCP connection |
| Multi-agent contracts and provenance | 2.0 | 4.7 | Three parallel evidence contracts use role-prefixed IDs, evidence URIs, confidence, and explicit failure gaps |
| Harness and loop correctness | 2.0 | 4.6 | Evaluator failures terminate with `model_error`; bounded tool loops force final synthesis; context fills remove repair churn |
| Concurrency and evidence isolation | 2.0 | 4.5 | Diminishing-return history is in AgentState; evidence snapshots are attached privately to each AnalysisResult |
| Retry, cleanup, and discovery resilience | 2.5 | 4.5 | Transient errors propagate; conversation/Responses/project/credential clients close; partial outputs enter recovery |
| Observability and evaluation | 4.0 | 4.6 | Response usage/IDs, function fingerprints, stage normalization, review removals, trajectory, action verification, and G-Eval are logged |
| Deployment/readiness | 2.0 | 4.7 | Six-role roster is live and exact-check clean; Hosted v10 completed a grounded analysis; obsolete Agents were removed |
| Current-platform upgrade path | 2.0 | 4.6 | Classic Agents removed; Prompt and Hosted immutable versions use current Responses contracts |
| **Weighted mean** | **2.30/5 (46%)** | **4.55/5 (91.0%)** | Locally verified specialist code; previous Hosted/control-plane/Azure MCP path was remote-verified |

## Improvement Iterations

1. Restored the planning tool loop with an allow-listed JSON bridge because Prompt Agent client-side `bind_tools()` was a no-op.
2. Migrated classic threads/runs to the current Responses API, closed every client/credential, and preserved transient errors for outer retry logic.
3. Replaced free-text enrichment with structured, evidence-addressable claims. Review now removes rejected claims and dependent actions.
4. Added explicit `model_error` termination for evaluator outages and invalid JSON instead of forcing a report.
5. Added optional planner, evaluator, and reporter Prompt Agents with phase-specific standing instructions and primary fallback.
6. Moved diminishing-return history into AgentState and attached evidence snapshots to each AnalysisResult to stop concurrent cross-update contamination.
7. Added a read-only roster validator and disabled enrichment by default until research/impact tools and current instructions are verified.
8. Rejected one Agent name assigned to multiple purposes and reduced provisioning discovery to one roster read.
9. Migrated mutable Agent provisioning to idempotent immutable versions while preserving existing non-app-owned managed tools and model options.
10. Preserved Responses ID/status/model/token usage and routed `max_output_tokens` partial output into report recovery.
11. Published stage-specific native FunctionTools from the app's Pydantic schemas and executed them through a bounded, allow-listed Responses loop.
12. Added strict stage JSON schemas, forced final synthesis, semantic normalization/reason codes, and explicit review-removal audit logs.
13. Removed six redundant impact pre-analysis tools already owned by the downstream Plan-Execute loop and filled required `service_name` from immutable update context instead of invoking LLM repair.
14. Moved the complete analyzer and subscriber customization into a Foundry Hosted Agent Responses handler; the old enrichment-only boundary was removed.
15. Added a strict v2 wire contract with discriminated analysis/customization operations, full `AnalysisResult` payloads, and trace/operation matching.
16. Replaced local analyzers in FastAPI and the scheduler with a fail-closed `HostedAgentAnalyzer` proxy and separated Container Apps versus Hosted Agent identity responsibilities.
17. Added an authenticated MCP Python SDK v2 Streamable HTTP surface to the Container App; its analysis tool delegates to the Hosted Agent instead of recreating the graph.
18. Set Hosted proxy calls to `store=false` for the one-shot contract, added the required `api-version=v1`, and preserved differential retry semantics.
19. Moved history and pattern optimizations from read-only `/app/data` to session-persistent `$HOME/.azbrief`; directory creation failures are non-fatal.
20. Replaced Azure MCP `single` proxy routing with direct leaf tools in `all` mode, server-filtered to the `group`, `resourcehealth`, and `advisor` namespaces under `--read-only`.
21. Injected the exact tenant GUID and configured subscription GUID into every impact request and forbade the literal tenant value `default`, which remote Azure MCP otherwise resolves as a nonexistent tenant display name.
22. Replaced the broad research/impact/action/review enrichment roster with three parallel evidence specialists: Resource Graph, Azure MCP, and Azure API.
23. Split report writing from quality review. Runtime G-Eval is on by default; the reviewer may request one grounded rewrite, retained only when its score improves.
24. Added coordinator, report-writer, and quality-reviewer routing for every internal LangGraph call site and removed cross-specialty KQL fallback.
25. Made all six Prompt Agent names required and unique at Hosted startup; provisioning, `azure.yaml`, Bicep outputs, tests, and docs use the same role contract.

Every iteration has focused unit tests. The Bicep source compiles into `infra/azbrief-enterprise-deploy.json` without warnings.

## Current Live Verification

- `azbrief-analysis-hosted` version 10 is active and its deployed definition contains exactly the
	coordinator, Resource Graph, Azure MCP, Azure API, report-writer, and quality-reviewer aliases.
- The six Prompt Agent versions are coordinator v1, Resource Graph v2, Azure MCP v1, Azure API v1,
	report writer v3, and quality reviewer v3. The exact read-only roster check passes.
- Azure Update `570120` completed through Hosted v10 in 80.59 seconds with environment-grounded
	AKS evidence, one verified non-mutating evaluation action, two Learn references, and no open gap.
- Project inventory was reduced from 15 to the required seven logical Agents. The superseded
	`action`, `evaluator`, `impact`, `planner`, `primary`, `reporter`, `research`, and `review`
	Agents were unreferenced by source, IaC, `azure.yaml`, and the live Hosted v10 definition; all
	28 immutable versions were deleted. The corresponding stale local azd aliases were emptied.

## Previous Live Deployment Baseline

The facts below describe the 2026-08-27 deployment **before** the six-specialist refactor. They
remain historical evidence that the Hosted endpoint, identity, Responses API, and Azure MCP path
worked, but the superseded Agents described here were deleted on 2026-08-30 and must not be used
as the current architecture baseline.

A deployment and repeated Responses API smoke test on 2026-08-27 established:

- The current immutable versions are primary v1, research v5, impact v10, action v2,
  and review v2; the exact roster check passes.
- The deployed model is `gpt-4o` (`2024-11-20`, GlobalStandard, capacity 200) in
	Korea Central.
- Version, instruction, strict response-schema, and exact app-function checks pass for all five Agents.
- `azbrief-primary` returned the exact expected `AZBRIEF_AGENT_OK` response through
	the project-scoped Responses API and `agent_reference`.
- Research and impact each expose five app-owned native FunctionTools. The final live run
	called documentation tools for research and tenant Resource Graph tools for impact, retained
	all four stages, and produced 8 evidence-addressable claims plus 8 explicit gaps.
- Forced synthesis completed after the bounded research tool loop; impact completed in one
	tool round. No invalid-output, unknown-evidence, or round-limit event remained.
- Contextual argument filling removed both observed `service_name` validation failures and
	their LLM repair calls.

The previous Prompt Agent roster was deployed and callable as primary v1, research v5, impact
v10, action v2, and review v2. It no longer matches the repository contract.

## Hosted Migration Verification

- Contract, client, entry point, API, scheduler, orchestrator, Admin, and MCP focused tests pass.
- The official MCP Python SDK v2 in-memory client discovers and invokes the three bounded tools.
- MCP authentication returns 503 without configuration, 401 without a key, and 403 for a bad key.
- `azure.yaml` parses through `azd show`; its `codeConfiguration` produced active Hosted Agent version 6 through Foundry direct-code build.
- `azd ai agent run` starts the local Hosted runtime, and a direct `POST /responses` full
	`analyze_update` request completed with matching operation/trace ID and update ID `564806`.
- The first remote run exposed two deployment-only defects: stored Responses required an unavailable resilient-task path, and `/app/data` was read-only. `store=false` and `$HOME/.azbrief` fixed them.
- A control-plane request to active version 6 completed in 102.3 seconds with HTTP 200, `update_id=564806`, `relevance=not_relevant`, no email delivery, and a completed report. The matching Azure MCP log shows `group_list` completed with `IsError=False` after subscription-scoped ARM calls returned HTTP 200.
- `infra/enterprise/main.bicep` compiles without warnings to the tracked ARM JSON.

## Remaining Gaps

### Archive Deployment

The canonical Archive implementation, private container IaC, deterministic 10,000-version
evaluation, and desktop/mobile browser checks are locally verified. The updated control-plane
image and Bicep have not yet been deployed to Azure, so Blob data-plane access, EasyAuth reader
sign-in, private DNS, and email deep links still require a staging deployment smoke test.

### Foundry Skills

Versioned Foundry Skills and toolbox discovery are public preview. The production app does not depend on them. When the feature is approved, attach skills through a versioned toolbox and load them progressively through MCP resources. Do not duplicate skill bodies in each Agent instruction.

### Evaluation Data Generation

`azd ai agent eval generate --no-wait` returned HTTP 400 because Foundry evaluation data
generation is not supported in Korea Central. No suite or local `eval.yaml` was created. Run
generation from a supported evaluation region or register an existing dataset instead; this
does not affect the active Hosted Agent runtime.

### Session-Local Optimization State

Hosted Agent sandboxes persist `$HOME` per session, not as a tenant-global filesystem. Analysis
history and pattern-memory files now use `$HOME/.azbrief` and remain optimizations, not
correctness dependencies; a future shared-store migration is required if cross-session learning
must be deterministic.

## Validation Commands

```powershell
& .\.venv\Scripts\Activate.ps1
python -c "import src"
python -m pytest tests\ -o "addopts=" -x
python -m pytest tests\test_hosted_contract.py tests\test_hosted_client.py tests\test_hosted_agent.py tests\test_mcp_server.py -o "addopts=" -q
az bicep build --file infra\enterprise\main.bicep --outfile infra\azbrief-enterprise-deploy.json
$env:AZURE_DEV_USER_AGENT='microsoft_foundry_skill'
azd show
python -m scripts.provision_foundry_agents --dry-run
python -m scripts.provision_foundry_agents --roles resource_graph azure_mcp azure_api report_writer quality_reviewer coordinator
python -m scripts.provision_foundry_agents --check
```
