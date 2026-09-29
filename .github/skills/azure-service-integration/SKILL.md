---
name: azure-service-integration
description: 'Add new Azure service integration to AzBrief. Use when: new service, add service, create service class, ResourceGraphService pattern, CostManagementService, BillingService, Microsoft.Billing, LogAnalyticsService, MicrosoftLearnService, lazy initialization, _get_client pattern, Azure SDK integration.'
---

# Azure Service Integration

This is a GitHub Copilot developer skill, not an AzBrief Agent runtime instruction.
Foundry operational guidance is maintained in
[foundry_instructions.py](../../../src/agent/foundry_instructions.py), independently of this file.

## When to Use

- Adding a new Azure SDK service class in `src/services/`
- Adding a corresponding LangChain tool in `src/agent/tools.py`
- Modifying existing service patterns
- Understanding the service architecture

## Service Class Pattern

All services in `src/services/` follow the same structure:

```python
"""Azure <ServiceName> Service using Azure SDK."""

import asyncio
from typing import Any, Optional

from structlog import get_logger

from src.config import get_settings

logger = get_logger()


class <ServiceName>Service:
    """Service for Azure <ServiceName> queries."""

    def __init__(self, subscription_id: Optional[str] = None):
        """Initialize <ServiceName> service.

        Args:
            subscription_id: Azure subscription ID (uses config if not provided)
        """
        settings = get_settings()
        self.subscription_id = subscription_id or settings.azure_subscription_id
        self._client: Optional[<AzureClient>] = None
        self._credential = None

    def _get_client(self) -> <AzureClient>:
        """Get or create <ServiceName> client."""
        if self._client is None:
            from src.config import get_azure_credential
            self._credential = get_azure_credential()
            self._client = <AzureClient>(self._credential)
        return self._client

    async def <primary_method>(self, ...) -> dict[str, Any]:
        """Execute a <ServiceName> operation.

        Args:
            ...

        Returns:
            Operation results dictionary
        """
        try:
            client = self._get_client()
            # Wrap sync SDK call in asyncio.to_thread if needed
            result = await asyncio.to_thread(client.some_method, ...)
            return {"success": True, "data": result}
        except Exception as e:
            logger.error("<service>_query_failed", error=str(e))
            return {"success": False, "error": str(e), "data": []}
```

### Key Rules

1. **Lazy initialization**: `_get_client()` creates the client on first use
2. **Credential from config**: Always use `from src.config import get_azure_credential`
3. **Return dict**: Return `{"success": bool, "data": ..., "error": str}` — never raise to caller
4. **Async wrapping**: Use `asyncio.to_thread()` for sync SDK methods
5. **structlog**: Use `logger = get_logger()` for all logging
6. **Google-style docstrings**: Include `Args:` and `Returns:` sections
7. **Type hints**: Python 3.10 style (`dict[str, Any]`, `list[str]`, `Optional[X]`)

## Adding a New Service — Step by Step

### 1. Create Service Class

Create `src/services/<service_name>.py` following the pattern above.

### 2. Create LangChain Tool

Add a `BaseTool` subclass in `src/agent/tools.py`:

```python
class <ServiceName>Tool(BaseTool):
    """Tool for querying Azure <ServiceName>."""

    name: str = "<service_name>_tool"
    description: str = "Query Azure <ServiceName> for ..."

    async def _arun(self, query: str) -> str:
        service = <ServiceName>Service()
        result = await service.<primary_method>(query)
        if result["success"]:
            return json.dumps(result["data"], indent=2, ensure_ascii=False)
        return f"Error: {result['error']}"
```

### 3. Register Tool in Agent

In `src/agent/analyzer.py`, add the tool to `AzureUpdateAnalyzer`'s tool list.

### 4. Add Dependency

If a new Azure SDK package is needed:
- Add to `requirements.txt`
- Add to `pyproject.toml` `[project] dependencies`
- Both files **must stay in sync**

### 5. Test

```bash
python -c "import src"                     # Import check
python -m scripts.test_local resources     # Integration test
```

## Existing Services

| Service | File | SDK Package | Purpose |
|---------|------|-------------|---------|
| `ResourceGraphService` | `resource_graph.py` | `azure-mgmt-resourcegraph` | Query resources across tenant |
| `CostManagementService` | `cost_management.py` | `azure-mgmt-costmanagement` | Cost data by service/period |
| `BillingService` | `billing.py` | `httpx` via `AzureRestClient` | Accessible billing accounts/profiles through Microsoft.Billing `2024-04-01` |
| `LogAnalyticsService` | `log_analytics.py` | `azure-monitor-query` | Read-only Log Analytics workspace queries |
| `MicrosoftLearnService` | `microsoft_learn.py` | `httpx` (REST API) | Search Microsoft Learn docs |
| `AzureRestService` | `azure_rest.py` | `httpx` (REST API) | Direct ARM REST calls (`call_api` for paginated `value` lists; `get_resource` for single-object endpoints like `providers/{namespace}`) |
| `ArchiveStore` / `BlobArchiveStore` | `archive.py` | `httpx` (Blob REST) | Immutable canonical analysis versions plus metadata-only list projection; control-plane data access, not an Agent evidence tool |
| `RuntimeInventoryService` | `runtime_inventory.py` | `httpx` + `azure-ai-projects` | Admin readiness용 병렬 ARM 조회, thread-safe lazy credential, safe Agent latest-version kind/status projection |

Failure-event ingestion is intentionally not an Agent evidence service. `src/logging_config.py`
uses `azure-monitor-ingestion` to send only Error/Critical records, captured exceptions,
failure-suffixed events, failed/partial statuses, false success results, and positive failure
counters to the `AzBriefFailures_CL` custom table through a Direct DCR. The enterprise template
creates the table/DCR and grants the App/Job UAMI DCR-scoped `Monitoring Metrics Publisher`; the
dedicated Hosted Agent identity receives that role separately after publication. Keep ordinary
successful INFO/WARNING records out of this table, redact every payload, and make exporter failure
non-recursive and non-fatal to the primary operation.

Cross-tenant image distribution is deployment configuration, not an Agent evidence service.
`containerRegistryAuthMode=Credentials` uses a publisher-issued repository pull token stored in
the customer Key Vault, with matching App/Job secret references. Preserve the ManagedIdentity
default for same-tenant registries. Do not add an ACR SDK service/tool, publisher credentials to
`Settings`, a cross-tenant identity grant, or a tenant switch. Customer setup and
`deploy_dev.ps1 -PrebuiltImage` validate existing bindings without reading passwords; prebuilt
digest rollout retains health/smoke/rollback gates and requires separate artifact provenance
and fresh-pull/network acceptance. See [the customer guide](../../../infra/CUSTOMER_DEPLOYMENT.md#external-tenant-registry).

`MicrosoftLearnService.fetch_documentation_page()` returns a typed `success/data/error`
envelope with the full article body, final/requested URL, headings, body links, code blocks,
and descriptive visuals. HTTPS/host validation runs before every redirect request; shorteners
are redirect-only, and non-HTML/oversized responses fail explicitly. Keep technical warnings,
relative links and functional version/query parameters. Link extraction bounds are disclosed.
Learn can split its title and body into sibling `div.content` regions: retain all outer regions
without duplicating nested content, and never accept a title-only response as article evidence.
`fetch_page_content()` remains a bounded-preview compatibility adapter; do not use it as the
full-evidence source for recursive analysis. Traversal decisions, source depth, shared budgets,
ref storage and follow-up authorization belong to `src/agent/documentation.py`, not the service.
The coordinator's explicit `FOUNDRY_COORDINATOR_LEARN_TRANSPORT=hosted` provisioning mode reuses
these existing public-document tools when managed Learn MCP discovery is unavailable. Preserve
the official-source/URL restrictions, real tool results and evidence gaps. Do not add a model
fallback or reuse Azure MCP credentials/tenant tools for public documentation.

`AzureRestClient` resolves a subscription only when the path contains `{subscriptionId}`.
Tenant-scope endpoints such as `/providers/Microsoft.Billing/billingAccounts` must not fail merely
because `AZURE_SUBSCRIPTION_ID` is unset. Billing APIs return only scopes visible to the current
identity; permission failures and empty visibility never prove that the tenant has no billing data.
Subscription Reader is insufficient for billing hierarchy access: the Hosted Agent identity needs
Billing Reader or equivalent read permission at the relevant billing account scope.

`CostManagementService` queries one explicit, configured, or uniquely discoverable subscription.
It never picks the first of multiple subscriptions. Its tools accept `subscription_id` and an exact
`resource_type` or verified Cost Management `service_name` filter, returning JSON evidence with
`ActualCost`, scope, period, currency, filter, and `has_cost_data`. All rows contribute to the total
before sorting/display limits. Named-column parsing rejects missing currency, mixed currencies,
non-finite amounts, and extra result pages; adapters propagate failures rather than return success
strings. Bounded subscriber scopes remain blocked because these tools cannot enforce their full
hierarchy. Cost Management Reader at the query scope is separate from Billing hierarchy permission.
Tests in `tests/test_services.py` and `tests/test_analyzer.py` cover this contract without Azure calls.

Cost query 429 recovery uses `retry_with_backoff` with three retries, `retry_total=0` on the SDK
call to prevent nested attempts, and a 10/20/40-second jittered fallback. Server retry hints take
priority: standard seconds/HTTP dates, millisecond headers, the documented consumption retry-after
header, and costmanagement retry-after limits. Use the longest valid hint and reject invalid,
negative or non-finite values. Do not shorten a server delay, sleep after exhaustion, retry 403,
or turn a throttled query into empty successful cost evidence. Existing runtime timeouts still apply.

## Resilience Patterns for Services

All services must implement these patterns from `src/agent/resilience.py`:

### Exponential Backoff + Jitter
```python
from src.agent.resilience import retry_with_backoff

# Wrap transient API calls
result = await retry_with_backoff(
    lambda: service.query_resources(kql),
    max_retries=3,
    retryable_errors=(429, 503, 529),
)
```

### Circuit Breaker
```python
from src.agent.resilience import CircuitBreaker

# Per-service circuit breaker
breaker = CircuitBreaker(failure_threshold=3, reset_timeout=60)

async def query_with_breaker(kql):
    if breaker.is_open:
        return {"success": False, "error": "Circuit open — service unavailable"}
    try:
        result = await service.query_resources(kql)
        breaker.record_success()
        return result
    except Exception as e:
        breaker.record_failure()
        raise
```

### Differential Retry Strategy
- **Foreground** (user-facing `/api/analyze`): Retry with backoff (up to 3 retries)
- **Background** (subscriber customization, batch): Fail immediately on 429/529
  - Reason: Background retries amplify gateway congestion (3-10× more calls)

### Graceful Degradation
```python
# If Resource Graph fails, continue with reduced confidence
try:
    resource_summary = await service.get_resource_types_summary()
except Exception:
    resource_summary = "Resource query failed"
    relevance = RelevanceStatus.UNKNOWN  # Signal reduced confidence
```

## Tool Concurrency Safety

When adding tools, declare concurrency attributes:

```python
class MyTool(BaseTool):
    # ...
    
    @property
    def is_read_only(self) -> bool:
        """Read-only tools can be parallelized during planning phase."""
        return True  # Set False for mutation tools
```

- Planning-phase tools: `is_read_only=True` → run in parallel
- Execution-phase tools: Run via `asyncio.gather` with per-task error isolation
- Default: serial execution (fail-closed)

## Architecture Rule

- **Services** (`src/services/`): Data-access only — fetch data from Azure APIs
- **Business logic**: Belongs in `src/agent/` (analyzer, tools)
- **Tools** (`src/agent/tools.py`): Bridge between LangGraph agent and services
- **Resilience** (`src/agent/resilience.py`): Retry, backoff, circuit breaker utilities
