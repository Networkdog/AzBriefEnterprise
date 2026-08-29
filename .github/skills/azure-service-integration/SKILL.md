---
name: azure-service-integration
description: 'Add new Azure service integration to AzBrief. Use when: new service, add service, create service class, ResourceGraphService pattern, CostManagementService, LogAnalyticsService, MicrosoftLearnService, lazy initialization, _get_client pattern, Azure SDK integration.'
---

# Azure Service Integration

## Foundry Runtime Guidance

- Treat services and FunctionTools as evidence providers, not decision makers. Accept a
    result as evidence only when its explicit success indicator is true.
- Prefer live read-only evidence with exact tenant and subscription scope; never treat one
    subscription as the whole tenant.
- Make the minimum calls needed to close a named gap. Execute serially when concurrency
    safety is undeclared.
- Preserve service errors and lower confidence. Missing evidence never proves absence.

<!-- End Foundry Runtime Guidance -->

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
| `LogAnalyticsService` | `log_analytics.py` | `azure-monitor-query` | Log Analytics workspace queries |
| `MicrosoftLearnService` | `microsoft_learn.py` | `httpx` (REST API) | Search Microsoft Learn docs |
| `AzureRestService` | `azure_rest.py` | `httpx` (REST API) | Direct ARM REST calls (`call_api` for paginated `value` lists; `get_resource` for single-object endpoints like `providers/{namespace}`) |

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
