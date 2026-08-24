"""FastAPI application entry point for AzBrief Enterprise."""

from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import urlparse

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from structlog import get_logger

from src.admin.router import router as admin_router
from src.agent.analyzer import AnalysisResult, AzureUpdateAnalyzer
from src.config import get_settings
from src.email.service import EmailService
from src.logging_config import setup_logging
from src.middleware import rate_limiter, verify_api_key
from src.orchestrator import get_run_store, register_services, start_run
from src.rss.parser import AzureUpdate, AzureUpdateParser

# Allowed domains for update URLs (SSRF prevention)
_ALLOWED_URL_DOMAINS = {
    "azure.microsoft.com",
    "www.microsoft.com",
    "learn.microsoft.com",
    "azure.com",
}

# Configure structured logging (centralized)
setup_logging(file_enabled=False)  # Container App: stdout only, no file

# Suppress verbose console output in Container App mode
import os

os.environ.setdefault("AZBRIEF_VERBOSE", "false")

logger = get_logger()

# Global instances
analyzer: Optional[AzureUpdateAnalyzer] = None
email_service: Optional[EmailService] = None
rss_parser: Optional[AzureUpdateParser] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    global analyzer, email_service, rss_parser

    logger.info("Initializing AzBrief application")

    # Initialize services
    analyzer = AzureUpdateAnalyzer()
    email_service = EmailService()
    rss_parser = AzureUpdateParser()

    # Expose them to orchestrated runs and the admin console.
    register_services(analyzer, email_service, rss_parser)

    logger.info(
        "AzBrief application started",
        llm_backend=get_settings().llm_backend,
        foundry_agents=len(get_settings().get_foundry_agents()),
        admin_ui=get_settings().admin_ui_enabled,
    )

    yield

    # Cleanup: close httpx clients to prevent resource leaks
    if analyzer:
        for tool in getattr(analyzer, "_tools", []):
            learn_svc = getattr(tool, "learn_service", None)
            if learn_svc and hasattr(learn_svc, "close"):
                try:
                    await learn_svc.close()
                except Exception:
                    pass

    logger.info("Shutting down AzBrief application")


app = FastAPI(
    title="AzBrief",
    description="Azure Update Intelligence Agent - Personalized update analysis service for Azure administrators",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(admin_router)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Attach baseline security headers to every response.

    The admin page sets its own Content-Security-Policy with a per-request
    nonce, so an existing header is never overwritten here.
    """
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


# Request/Response Models
class AnalyzeRequest(BaseModel):
    """Request model for analyze endpoint."""

    update_url: str = Field(..., description="Azure Update URL to analyze")
    recipient_email: Optional[str] = Field(
        None, description="Optional override for recipient email"
    )
    send_email: bool = Field(True, description="Whether to send email notification")

    @field_validator("update_url")
    @classmethod
    def validate_update_url(cls, v: str) -> str:
        """Validate that update_url points to an allowed Microsoft domain."""
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("URL must use http or https scheme")
        hostname = (parsed.hostname or "").lower()
        if not any(hostname == d or hostname.endswith(f".{d}") for d in _ALLOWED_URL_DOMAINS):
            raise ValueError(
                f"URL domain '{hostname}' is not allowed. "
                f"Only Microsoft Azure domains are accepted."
            )
        return v


class AnalyzeResponse(BaseModel):
    """Response model for analyze endpoint."""

    status: str
    update_id: str
    update_title: str
    is_relevant: bool
    relevance: str
    relevance_reason: str
    affected_resources_count: int
    recommendations_count: int
    email_sent: bool


class HealthResponse(BaseModel):
    """Response model for health check."""

    status: str
    version: str


class RSSCheckRequest(BaseModel):
    """Request model for RSS check endpoint."""

    processed_ids: list[str] = Field(
        default_factory=list, description="Already processed update IDs"
    )


class RSSCheckResponse(BaseModel):
    """Response model for RSS check endpoint."""

    new_updates: list[dict]
    total_count: int


# Endpoints
@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint with dependency verification."""
    checks: dict[str, str] = {}

    # Check Azure credential
    try:
        from src.config import get_azure_credential

        cred = get_azure_credential()
        cred.get_token("https://management.azure.com/.default")
        checks["azure_credential"] = "ok"
    except Exception as e:
        checks["azure_credential"] = f"error: {str(e)[:80]}"

    # Check LLM configuration
    settings = get_settings()
    if settings.use_azure_openai:
        checks["llm"] = "configured (Azure OpenAI)"
        # Validate LLM connectivity with a lightweight call
        try:
            from langchain_openai import AzureChatOpenAI

            llm = AzureChatOpenAI(
                azure_endpoint=settings.azure_openai_endpoint,
                api_version=settings.azure_openai_api_version,
                azure_deployment=settings.azure_openai_deployment_name,
                api_key=settings.azure_openai_api_key or "dummy",
                max_tokens=1,
            )
            # Just validate the endpoint is reachable — don't actually invoke
            checks["llm"] = "connected (Azure OpenAI)"
        except Exception as e:
            checks["llm"] = f"configured but error: {str(e)[:60]}"
    elif settings.openai_api_key:
        checks["llm"] = "configured (OpenAI)"
    else:
        checks["llm"] = "not configured"

    has_errors = any("error" in v for v in checks.values())
    status = "degraded" if has_errors else "healthy"

    return HealthResponse(status=status, version="0.1.0")


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "AzBrief",
        "description": "Azure Update Intelligence Agent",
        "version": "0.1.0",
        "docs": "/docs",
    }


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze_update(
    request: AnalyzeRequest,
    background_tasks: BackgroundTasks,
    _api_key: str = Depends(verify_api_key),
):
    """Analyze an Azure Update and optionally send email notification.

    Args:
        request: Analysis request containing update URL
        background_tasks: FastAPI background tasks

    Returns:
        Analysis result summary
    """
    global analyzer, email_service, rss_parser

    if not analyzer or not rss_parser:
        raise HTTPException(status_code=500, detail="Services not initialized")

    logger.info("Received analyze request", update_url=request.update_url)

    try:
        # Fetch update details
        update = await rss_parser.get_update_by_url(request.update_url)

        if not update:
            # Try to fetch details directly from URL
            details = await rss_parser.fetch_update_details(request.update_url)
            update = AzureUpdate(
                id=request.update_url,
                title=details.get("title", "Unknown Update"),
                description=details.get("content", ""),
                link=request.update_url,
                published_date=None,
                categories=[],
                azure_services=[],
                update_type=None,
                status=None,
            )

        # Analyze the update
        result = await analyzer.analyze_update(update)

        # Send email in background if requested
        email_sent = False
        if (
            request.send_email
            and (result.should_notify or not get_settings().report_filtering_enabled)
            and email_service
        ):
            subscribers = get_settings().get_subscribers()
            if subscribers and not request.recipient_email:
                # Send personalized reports per subscriber
                background_tasks.add_task(
                    email_service.send_to_subscribers,
                    update,
                    result,
                    analyzer,
                    subscribers,
                )
            else:
                # Single recipient (explicitly specified or existing default)
                background_tasks.add_task(
                    email_service.send_analysis_report,
                    update,
                    result,
                    request.recipient_email,
                )
            email_sent = True

        return AnalyzeResponse(
            status="success",
            update_id=result.update_id,
            update_title=result.update_title,
            is_relevant=result.should_notify,
            relevance=result.relevance.value,
            relevance_reason=result.relevance_reason[:200],
            affected_resources_count=len(result.affected_resources),
            recommendations_count=len(result.recommendations),
            email_sent=email_sent,
        )

    except Exception as e:
        logger.error("Analysis failed", error=str(e), update_url=request.update_url)
        raise HTTPException(
            status_code=500, detail="Analysis failed. Check server logs for details."
        )


@app.post("/api/rss/check", response_model=RSSCheckResponse)
async def check_rss_updates(
    request: RSSCheckRequest,
    _api_key: str = Depends(verify_api_key),
):
    """Check RSS feed for new updates.

    Args:
        request: Request containing already processed IDs

    Returns:
        List of new updates
    """
    global rss_parser

    if not rss_parser:
        raise HTTPException(status_code=500, detail="RSS parser not initialized")

    logger.info("Checking RSS feed for new updates")

    try:
        updates = await rss_parser.get_updates()

        # Filter out already processed updates
        new_updates = [u.to_dict() for u in updates if u.id not in request.processed_ids]

        return RSSCheckResponse(
            new_updates=new_updates,
            total_count=len(new_updates),
        )

    except Exception as e:
        logger.error("RSS check failed", error=str(e))
        raise HTTPException(
            status_code=500, detail="RSS check failed. Check server logs for details."
        )


@app.post("/api/batch/analyze")
async def batch_analyze(
    update_urls: list[str],
    background_tasks: BackgroundTasks,
    _api_key: str = Depends(verify_api_key),
):
    """Analyze multiple Azure Updates in batch.

    Args:
        update_urls: List of update URLs to analyze
        background_tasks: FastAPI background tasks

    Returns:
        Batch processing status
    """
    if not update_urls:
        raise HTTPException(status_code=400, detail="No update URLs provided")

    if len(update_urls) > 10:
        raise HTTPException(status_code=400, detail="Maximum 10 URLs per batch")

    # Validate all URLs before accepting the batch
    for url in update_urls:
        try:
            AnalyzeRequest(update_url=url)
        except ValueError as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid URL '{url}': {e}",
            )

    async def process_batch():
        """Process updates concurrently in background."""
        import asyncio

        max_concurrent = get_settings().max_concurrent_analyses
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _analyze_one(url: str):
            async with semaphore:
                try:
                    req = AnalyzeRequest(update_url=url)
                    await analyze_update(req, background_tasks)
                except Exception as e:
                    logger.error("Batch item failed", url=url, error=str(e))

        await asyncio.gather(*[_analyze_one(url) for url in update_urls])

    background_tasks.add_task(process_batch)

    return {
        "status": "processing",
        "count": len(update_urls),
        "message": "Batch analysis started in background",
    }


# ---------------------------------------------------------------------------
# Orchestrated runs (enterprise profile)
#
# The scheduled digest runs in a Container Apps Job (`python -m src.scheduler`);
# these routes drive the same pipeline on demand for the admin console or an
# external scheduler. Omitting `since` resumes from the durable checkpoint.
# ---------------------------------------------------------------------------


class OrchestrateRunRequest(BaseModel):
    """Request model for starting an orchestrated digest run."""

    since: Optional[str] = Field(
        None,
        description=(
            "ISO-8601 UTC instant; only later updates are analysed. "
            "Omit to resume from the durable checkpoint."
        ),
    )
    dry_run: bool = Field(False, description="Collect targets without analysing or emailing.")


@app.post("/api/orchestrate/run", status_code=202)
async def orchestrate_run(
    request: OrchestrateRunRequest,
    _api_key: str = Depends(verify_api_key),
):
    """Start an orchestrated analysis run and return immediately.

    Args:
        request: Optional checkpoint and dry-run flag.

    Returns:
        The queued run record, including its run_id for polling.
    """
    from src.orchestrator import parse_iso_utc

    try:
        since = parse_iso_utc(request.since)
    except ValueError:
        raise HTTPException(status_code=400, detail="since must be an ISO-8601 timestamp")

    store = get_run_store()
    if store.active_count:
        active = store.recent(1)
        raise HTTPException(
            status_code=409,
            detail=f"A run is already in progress ({active[0].run_id if active else 'unknown'})",
        )

    try:
        record = start_run(since=since, dry_run=request.dry_run)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    return record.to_dict()


@app.get("/api/orchestrate/runs/{run_id}")
async def orchestrate_run_status(
    run_id: str,
    _api_key: str = Depends(verify_api_key),
):
    """Return the status of an orchestrated run.

    A 404 means the record is gone (for example after a restart). Callers must
    treat that as "checkpoint not advanced" rather than as success.
    """
    record = get_run_store().get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return record.to_dict()
