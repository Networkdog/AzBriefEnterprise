"""Configuration management for AzBrief Enterprise."""

import json

# Load .env file and export to os.environ for Azure SDK
# This is needed because DefaultAzureCredential reads from os.environ directly
# Use override=False so platform-set env vars (Container Apps) take precedence
import os as _os
from functools import lru_cache
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.i18n import DEFAULT_LANGUAGE, is_supported, normalize_language, supported_language_codes

load_dotenv(override=not _os.environ.get("CONTAINER_APP_NAME"))

# LLM roles with their own optional deployment. Each role reads the
# azure_openai_<role>_* fields and falls back to the primary ones.
LLM_ROLES = ("primary", "codex", "fast")


class Subscriber(BaseModel):
    """Subscriber profile for personalized reports.

    Example JSON::

        [{"email": "admin@co.com", "name": "Alice Kim", "role": "Azure Infra Management (VM, Network)", "language": "ko"},
         {"email": "sec@co.com",   "name": "Bob Park", "role": "Security/Compliance",
          "subscriptions": ["sub-id-1"], "focus_services": ["Key Vault", "Defender"], "alert_level": "important_and_above"},
         {"email": "eng@co.com",   "name": "John",   "role": "Cloud Architect", "language": "en"}]
    """

    email: str
    name: str
    role: str = ""
    language: str = DEFAULT_LANGUAGE
    subscriptions: list[str] = (
        []
    )  # Subscription IDs this subscriber is responsible for (empty = all)
    resource_groups: list[str] = []  # Resource group names (empty = all)
    focus_services: list[str] = []  # Azure services of interest (empty = all)
    alert_level: str = "all"  # "critical_only" | "important_and_above" | "all"

    @field_validator("language")
    @classmethod
    def normalize_subscriber_language(cls, v: str) -> str:
        """Normalize a subscriber's language tag (``ko-KR`` -> ``ko``).

        Unregistered codes are kept rather than rejected: a subscriber may ask
        for a language that has no curated style guide yet, and the registry
        degrades to a generic guide plus fallback labels for it.
        """
        return normalize_language(v)


class FoundryMcpServer(BaseModel):
    """Remote MCP server attached to the Foundry enrichment agent.

    Used only when ``llm_backend='foundry'``. Each entry is passed to the
    Foundry Agent Service as an MCP tool connection so the enrichment agent
    can call the server's tools (e.g. Azure MCP, Microsoft Learn MCP).

    Example JSON::

        [{"label": "azure", "url": "https://<host>/runtime/webhooks/mcp",
          "allowed_tools": ["query_azure_resource_graph"]},
         {"label": "learn", "url": "https://learn.microsoft.com/api/mcp"}]
    """

    label: str
    url: str
    allowed_tools: list[str] = []  # empty = all tools exposed by the server
    require_approval: str = "never"  # "always" | "never" (background job → never)


# Stages of the Foundry hosted multi-agent pipeline, in execution order.
# 'research' and 'impact' are independent and run concurrently; 'action'
# consumes both; 'review' audits the merged context.
FOUNDRY_AGENT_STAGES = ("research", "impact", "action", "review")


class FoundryAgentSpec(BaseModel):
    """One hosted Foundry agent participating in the multi-agent pipeline.

    Each entry names an agent that already exists in the Foundry project, so
    its tools, model and guardrails stay governed in Foundry rather than
    hard-coded here.

    Example JSON::

        [{"name": "azbrief-research", "stage": "research"},
         {"name": "azbrief-impact",   "stage": "impact"},
         {"name": "azbrief-action",   "stage": "action"}]
    """

    name: str
    stage: str = "research"
    version: str = "latest"
    instructions: str = ""  # appended to the stage prompt when set

    @field_validator("stage")
    @classmethod
    def validate_stage(cls, v: str) -> str:
        """Restrict the stage to the known pipeline positions."""
        v_lower = v.strip().lower()
        if v_lower not in FOUNDRY_AGENT_STAGES:
            raise ValueError(f"stage must be one of {FOUNDRY_AGENT_STAGES}, got '{v}'")
        return v_lower


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Azure Identity
    azure_client_id: Optional[str] = Field(default=None, description="Managed Identity Client ID")
    azure_tenant_id: str = Field(..., description="Azure Tenant ID")
    azure_subscription_id: Optional[str] = Field(
        default=None,
        description="Azure Subscription ID (optional - if not set, use all accessible subscriptions in tenant)",
    )

    # Azure OpenAI
    azure_openai_endpoint: Optional[str] = Field(default=None, description="Azure OpenAI endpoint")
    azure_openai_api_key: Optional[str] = Field(default=None, description="Azure OpenAI API Key")
    azure_openai_api_version: str = Field(
        default="2024-02-15-preview", description="Azure OpenAI API version"
    )
    azure_openai_deployment_name: str = Field(
        default="gpt-4o", description="Azure OpenAI deployment name"
    )
    azure_openai_codex_endpoint: Optional[str] = Field(
        default=None,
        description="Azure OpenAI endpoint for Codex model (all KQL work: Resource Graph / Log Analytics query generation and fixing)",
    )
    azure_openai_codex_api_key: Optional[str] = Field(
        default=None,
        description="Azure OpenAI API key for Codex model (all KQL work: Resource Graph / Log Analytics query generation and fixing)",
    )
    azure_openai_codex_api_version: Optional[str] = Field(
        default=None,
        description="Azure OpenAI API version for Codex model (all KQL work: Resource Graph / Log Analytics query generation and fixing)",
    )
    azure_openai_codex_deployment_name: Optional[str] = Field(
        default=None,
        description="Azure OpenAI Codex deployment name for all KQL work (Resource Graph / Log Analytics)",
    )

    # Azure OpenAI Fast Model (lightweight tasks: task revision, subscriber
    # customization). Never used for KQL — that always goes to the Codex model.
    azure_openai_fast_endpoint: Optional[str] = Field(
        default=None,
        description="Azure OpenAI endpoint for fast model. Falls back to main endpoint if not set.",
    )
    azure_openai_fast_api_key: Optional[str] = Field(
        default=None,
        description="Azure OpenAI API key for fast model. Falls back to main API key if not set.",
    )
    azure_openai_fast_api_version: Optional[str] = Field(
        default=None,
        description="Azure OpenAI API version for fast model. Falls back to main API version if not set.",
    )
    azure_openai_fast_deployment_name: Optional[str] = Field(
        default=None,
        description="Azure OpenAI fast model deployment name (e.g., gpt-4o-mini). Falls back to main deployment if not set.",
    )

    def llm_profile(self, role: str = "primary") -> dict[str, Optional[str]]:
        """Azure OpenAI connection settings for an LLM role.

        Role-specific fields fall back to the primary ones when unset, so a role
        that has no separate deployment transparently shares the main model.

        Args:
            role: One of LLM_ROLES ("primary", "codex", "fast")

        Returns:
            Dict with endpoint, api_key, api_version and deployment
        """
        if role not in LLM_ROLES:
            raise ValueError(f"Unknown LLM role '{role}'. Expected one of {LLM_ROLES}.")
        prefix = "" if role == "primary" else f"{role}_"
        return {
            "endpoint": getattr(self, f"azure_openai_{prefix}endpoint")
            or self.azure_openai_endpoint,
            "api_key": getattr(self, f"azure_openai_{prefix}api_key") or self.azure_openai_api_key,
            "api_version": getattr(self, f"azure_openai_{prefix}api_version")
            or self.azure_openai_api_version,
            "deployment": getattr(self, f"azure_openai_{prefix}deployment_name")
            or self.azure_openai_deployment_name,
        }

    # OpenAI (fallback)
    openai_api_key: Optional[str] = Field(default=None, description="OpenAI API Key")

    # Azure Communication Services
    communication_services_connection_string: Optional[str] = Field(
        default=None,
        description="Azure Communication Services connection string (optional - if not set, output to console)",
    )
    communication_services_endpoint: Optional[str] = Field(
        default=None,
        description=(
            "Azure Communication Services resource endpoint, e.g. "
            "https://<name>.communication.azure.com. Used with Managed Identity "
            "when no connection string is configured, so the enterprise profile "
            "can run without a stored ACS secret."
        ),
    )
    email_sender_address: Optional[str] = Field(default=None, description="Email sender address")
    email_recipient_address: Optional[str] = Field(
        default=None, description="Email recipient address"
    )

    # Log Analytics
    log_analytics_workspace_id: Optional[str] = Field(
        default=None, description="Log Analytics workspace ID for querying logs (optional)"
    )

    # Custom Prompt (injected from Automation Account Variable, etc.)
    custom_system_prompt: Optional[str] = Field(
        default=None,
        description="Additional system prompt (injected via CUSTOM_SYSTEM_PROMPT env var)",
    )

    # Subscribers (subscriber list — JSON array string)
    subscribers: Optional[str] = Field(
        default=None,
        description='Subscriber list JSON (e.g., [{"email":"a@co.com","name":"Alice","role":"Infra Management","language":"ko"}])',
    )

    # Application Settings
    log_level: str = Field(
        default="INFO", description="Logging level (DEBUG|INFO|WARNING|ERROR|CRITICAL)"
    )
    log_file_enabled: bool = Field(default=True, description="Enable file logging")
    log_file_dir: str = Field(default="logs", description="Log file directory")
    log_console_level: Optional[str] = Field(
        default=None,
        description="Console log level override (default: same as LOG_LEVEL)",
    )

    # Azure Monitor Log Ingestion (optional — send structured logs to Log Analytics)
    azure_monitor_ingestion_endpoint: Optional[str] = Field(
        default=None,
        description="Azure Monitor Data Collection Endpoint (DCE) URL",
    )
    azure_monitor_dcr_rule_id: Optional[str] = Field(
        default=None,
        description="Data Collection Rule (DCR) immutable ID (dcr-...)",
    )
    azure_monitor_dcr_stream_name: str = Field(
        default="Custom-AzBrief_CL",
        description="DCR stream name for log ingestion",
    )

    # OpenTelemetry distributed tracing (optional — requires azbrief[telemetry] extra)
    otel_enabled: bool = Field(
        default=False,
        description=(
            "Enable OpenTelemetry tracing of the analysis transaction and tool calls. "
            "Requires the 'telemetry' extra (azure-monitor-opentelemetry) and "
            "APPLICATIONINSIGHTS_CONNECTION_STRING. No-op when either is absent."
        ),
    )
    applicationinsights_connection_string: Optional[str] = Field(
        default=None,
        description="Application Insights connection string for OpenTelemetry span export",
    )

    report_language: str = Field(
        default=DEFAULT_LANGUAGE,
        description=(
            "Default report language. Must be one of the codes registered in src/i18n. "
            "Subscribers can override with their own language setting."
        ),
    )

    @field_validator("report_language")
    @classmethod
    def validate_report_language(cls, v: str) -> str:
        """Normalize and restrict report_language to registered languages.

        Unlike a subscriber's language, the tenant-wide default is a system
        boundary — a typo here would mislocalize every report, so an
        unregistered code fails fast instead of degrading.
        """
        code = normalize_language(v)
        if not is_supported(code):
            supported = ", ".join(supported_language_codes())
            raise ValueError(f"report_language must be one of [{supported}], got '{v}'")
        return code

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Restrict log_level to valid Python logging levels."""
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        v_upper = v.upper()
        if v_upper not in allowed:
            raise ValueError(f"log_level must be one of {allowed}, got '{v}'")
        return v_upper

    # API Security
    api_key: Optional[str] = Field(
        default=None,
        description="API key for authenticating /api/* endpoints. If not set, endpoints are open.",
    )

    # Batch Analysis Concurrency
    max_concurrent_analyses: int = Field(
        default=3,
        description="Maximum number of updates to analyze concurrently in batch mode (1=sequential)",
    )

    # Wall-clock run budget
    run_time_budget_s: int = Field(
        default=39600,
        description=(
            "Wall-clock budget in seconds for one end-to-end run. A Container Apps Job kills the "
            "replica at replicaTimeout (12h by default), so the default leaves an hour of headroom "
            "(39600s = 11h) to finish in-flight work and commit the checkpoint. Set to 0 to disable."
        ),
    )

    # Report delivery filtering
    report_filtering_enabled: bool = Field(
        default=False,
        description=(
            "When True, updates classified as not_relevant (should_notify=False) are suppressed "
            "from email notifications to reduce noise. When False (default), every analyzed update "
            "is delivered — no report is omitted by relevance filtering."
        ),
    )

    # Controlled autonomy: human-in-the-loop approval before email dispatch
    require_approval_before_send: bool = Field(
        default=False,
        description=(
            "When True, analysis reports are NEVER auto-dispatched by email. The rendered "
            "report is saved to out/ and logged as pending approval for a human to review and "
            "send manually. Applies to single, subscriber, and digest delivery. Default False "
            "(fully autonomous). AzBrief only ever sends email — it never changes Azure resources."
        ),
    )

    # Practitioner commentary (Azure Weekly digest)
    community_insights_enabled: bool = Field(
        default=True,
        description=(
            "Enrich analyses with practitioner commentary crawled from the Azure Weekly digest "
            "(azureweekly.info, robots.txt allows all). Supplies real-world caveats and conflicts "
            "that official documentation does not carry. Read-only, cached weekly, and treated as "
            "untrusted third-party text in the prompt. Degrades silently when unreachable."
        ),
    )
    community_insights_issues: int = Field(
        default=8,
        description="Number of recent Azure Weekly issues to crawl when refreshing the cache",
    )
    community_insights_full_text: int = Field(
        default=2,
        ge=0,
        le=4,
        description=(
            "For this many top-ranked matches, fetch the full article body from the Microsoft "
            "Tech Community board feed and extract stated constraints. The digest blurb is only "
            "~200 characters and never carries prerequisites; the article body does. Set 0 to "
            "disable full-text retrieval."
        ),
    )

    # G-Eval report quality judge (LLM-as-a-Judge)
    geval_enabled: bool = Field(
        default=True,
        description="Enable the G-Eval LLM-as-a-Judge quality evaluator in the report loop",
    )
    geval_target_score: float = Field(
        default=4.5,
        description=(
            "G-Eval passing threshold on the 1-5 scale (5 is an unreachable ideal, "
            "4 is production-excellent). The self-improvement loop stops at this score."
        ),
    )
    geval_logprob_normalization: bool = Field(
        default=True,
        description=(
            "Refine integer G-Eval scores into continuous values via token log-probabilities. "
            "Auto-disabled for o-series reasoning models that do not expose logprobs."
        ),
    )
    geval_max_iterations: int = Field(
        default=3,
        description="Maximum generate→evaluate→improve iterations in the quality loop",
    )
    geval_runtime_enabled: bool = Field(
        default=False,
        description=(
            "Run the G-Eval judge inside analyze_update and let it drive one report "
            "rewrite when the score misses geval_target_score. Off by default: it adds "
            "roughly two LLM calls per update, which matters against the Automation "
            "3-hour fair-share limit. Requires geval_enabled."
        ),
    )

    # Agent trajectory / tool-call accuracy evaluation (rule-based, process quality)
    trajectory_eval_enabled: bool = Field(
        default=True,
        description=(
            "Evaluate the agent's execution trajectory (tool-call accuracy, retry "
            "burden, KQL failure rate, revision churn) after each analysis. "
            "Rule-based and cheap — complements the G-Eval report-quality judge."
        ),
    )

    # ── Action-item safety verification (multi-layer gate) ──────
    # Action items are the only part of a report a reader may execute verbatim
    # against a production subscription, so they are gated separately from
    # report-quality evaluation.
    action_verification_enabled: bool = Field(
        default=True,
        description=(
            "Run the multi-layer safety gate over action items before delivery: "
            "static pattern checks (destructive commands, unresolved placeholders, "
            "ungrounded resource names, fabricated deadlines), an adversarial LLM "
            "cross-check, and a policy gate. Disabling it ships unverified items."
        ),
    )
    action_verification_cross_check: bool = Field(
        default=True,
        description=(
            "Layer 2: re-review every action item with an independent adversarial "
            "LLM pass over the same evidence. When disabled or unavailable, items "
            "are labelled 'unverified' rather than 'verified'."
        ),
    )
    action_verification_withhold_commands: bool = Field(
        default=True,
        description=(
            "Layer 3: strip the CLI command from an action item that failed "
            "verification so it can never be copy-pasted into a production shell. "
            "The task and the defect explanation are still delivered."
        ),
    )

    # ── Microsoft Foundry backend ────────────────────────────
    # Foundry is this build's primary backend: hosted agents supply the staged
    # multi-agent pipeline plus Bing/Web search, Azure MCP, Microsoft Learn MCP
    # and memory. The core Plan-Execute-Evaluate loop, KQL determinism, and
    # G-Eval quality pipeline are unchanged, and an unreachable Foundry project
    # degrades to Azure OpenAI rather than failing the run.
    llm_backend: str = Field(
        default="foundry",
        description=(
            "LLM/agent backend: 'foundry' (Microsoft Foundry Agent Service, default) "
            "or 'openai' (Azure OpenAI / OpenAI). 'foundry' falls back to 'openai' "
            "gracefully when the SDK or endpoint is unavailable."
        ),
    )
    foundry_project_endpoint: Optional[str] = Field(
        default=None,
        description=(
            "Microsoft Foundry project endpoint, e.g. "
            "https://<resource>.services.ai.azure.com/api/projects/<project>"
        ),
    )
    foundry_model_deployment: Optional[str] = Field(
        default=None,
        description=(
            "Foundry chat model deployment name. Falls back to "
            "azure_openai_deployment_name when not set."
        ),
    )
    foundry_api_version: Optional[str] = Field(
        default=None,
        description="Foundry inference API version (optional; SDK default when unset).",
    )
    foundry_enrichment_agent_name: Optional[str] = Field(
        default=None,
        description=(
            "Name of a pre-configured Foundry Agent Service agent used as a "
            "LangGraph enrichment node. Configure its tools (Bing/Web search, "
            "Azure MCP, Microsoft Learn MCP, memory) in the Foundry portal/SDK. "
            "When unset, no enrichment node is added (core loop unchanged)."
        ),
    )
    foundry_enable_web_search: bool = Field(
        default=True,
        description=(
            "Allow the Foundry enrichment agent to use Web/Bing grounding for "
            "real-time public context. Effective only with the foundry backend."
        ),
    )
    foundry_bing_connection_id: Optional[str] = Field(
        default=None,
        description=(
            "Foundry project connection ID for a Grounding with Bing Search "
            "resource. Optional; the hosted Web search tool needs no connection."
        ),
    )
    foundry_mcp_servers: Optional[str] = Field(
        default=None,
        description=(
            "Remote MCP servers for the Foundry enrichment agent — JSON array, "
            'e.g. [{"label":"azure","url":"https://.../mcp"},'
            '{"label":"learn","url":"https://learn.microsoft.com/api/mcp"}]'
        ),
    )
    foundry_enable_memory: bool = Field(
        default=False,
        description=(
            "Allow the Foundry enrichment agent to use a Foundry-managed memory "
            "store (preview) for cross-run analysis context. Off by default."
        ),
    )
    foundry_agents: Optional[str] = Field(
        default=None,
        description=(
            "Hosted Foundry agents forming the multi-agent pipeline — JSON array, "
            'e.g. [{"name":"azbrief-research","stage":"research"},'
            '{"name":"azbrief-impact","stage":"impact"},'
            '{"name":"azbrief-action","stage":"action"}]. When set, this replaces '
            "the single enrichment agent with a staged pipeline."
        ),
    )
    foundry_agent_timeout_s: int = Field(
        default=180,
        description="Per-agent timeout for a hosted Foundry agent invocation, in seconds.",
    )

    # ── Scheduling & durable state ──────────────────────────────
    # A Container Apps Job runs the scheduled digest and the Container App
    # serves the orchestrator API and admin page, both driving the Foundry
    # multi-agent pipeline.
    checkpoint_blob_url: Optional[str] = Field(
        default=None,
        description=(
            "HTTPS URL of the blob holding the digest checkpoint, e.g. https://"
            "<account>.blob.core.windows.net/azbrief-state/checkpoint.json. Read "
            "and written with the workload's managed identity."
        ),
    )
    checkpoint_file_path: Optional[str] = Field(
        default=None,
        description=(
            "Local file holding the digest checkpoint. Development fallback used "
            "only when checkpoint_blob_url is unset."
        ),
    )
    orchestrator_endpoint: Optional[str] = Field(
        default=None,
        description=(
            "Base URL of the Container App orchestrator, e.g. https://ca-azbrief."
            "<region>.azurecontainerapps.io. Used by an external scheduler that "
            "drives runs over HTTP instead of the built-in Container Apps Job."
        ),
    )
    orchestrator_api_key: Optional[str] = Field(
        default=None,
        description=(
            "API key an external scheduler presents to the orchestrator endpoint. "
            "Matches the Container App's API_KEY setting."
        ),
    )
    orchestrator_timeout_s: int = Field(
        default=120,
        description=(
            "HTTP timeout for an external scheduler's call to the orchestrator. The "
            "call only starts the run, so this does not bound the analysis itself."
        ),
    )

    # ── Admin page (enterprise profile) ─────────────────────────
    admin_ui_enabled: bool = Field(
        default=False,
        description=(
            "Serve the /admin page and /api/admin endpoints. Off by default — the "
            "ARM template only turns it on once Entra sign-in and an explicit "
            "principal allow-list are both configured."
        ),
    )
    admin_require_auth: bool = Field(
        default=True,
        description=(
            "Require an authenticated principal (Container Apps Entra ID sign-in) "
            "for every admin route. Disable only for local development."
        ),
    )
    admin_allowed_principals: Optional[str] = Field(
        default=None,
        description=(
            "Comma-separated allow-list of admin principals (UPN/email or object "
            "ID). An empty list denies everyone, so the page fails closed."
        ),
    )

    @field_validator("llm_backend")
    @classmethod
    def validate_llm_backend(cls, v: str) -> str:
        """Restrict llm_backend to supported values."""
        allowed = {"openai", "foundry"}
        v_lower = v.lower()
        if v_lower not in allowed:
            raise ValueError(f"llm_backend must be one of {allowed}, got '{v}'")
        return v_lower

    @field_validator("geval_target_score")
    @classmethod
    def validate_geval_target_score(cls, v: float) -> float:
        """Keep the G-Eval target within the meaningful 1.0-5.0 band."""
        if not 1.0 <= v <= 5.0:
            raise ValueError(f"geval_target_score must be between 1.0 and 5.0, got {v}")
        return v

    @property
    def use_azure_openai(self) -> bool:
        """Check if Azure OpenAI should be used."""
        return self.azure_openai_endpoint is not None

    @property
    def use_foundry(self) -> bool:
        """Check if the Microsoft Foundry backend is requested and configured.

        True only when llm_backend='foundry' AND a project endpoint is set.
        Even when True, callers must degrade gracefully to Azure OpenAI if the
        'foundry' extra (langchain-azure-ai) is not installed.
        """
        return self.llm_backend == "foundry" and self.foundry_project_endpoint is not None

    def get_foundry_mcp_servers(self) -> list[FoundryMcpServer]:
        """Parse FOUNDRY_MCP_SERVERS JSON into a list of FoundryMcpServer.

        Returns an empty list on missing or malformed input (graceful degrade),
        so a bad MCP config never blocks the analysis run.
        """
        if not self.foundry_mcp_servers:
            return []
        try:
            raw = json.loads(self.foundry_mcp_servers)
            if not isinstance(raw, list):
                return []
            return [FoundryMcpServer(**item) for item in raw]
        except (json.JSONDecodeError, TypeError, ValueError):
            return []

    def get_foundry_agents(self) -> list[FoundryAgentSpec]:
        """Parse FOUNDRY_AGENTS JSON into a list of FoundryAgentSpec.

        Returns an empty list on missing or malformed input so a bad roster
        degrades to the single-agent (or plain LLM) path instead of failing
        the run. Entries are de-duplicated by stage, last one winning.
        """
        if not self.foundry_agents:
            return []
        try:
            raw = json.loads(self.foundry_agents)
            if not isinstance(raw, list):
                return []
            parsed: dict[str, FoundryAgentSpec] = {}
            for item in raw:
                try:
                    spec = FoundryAgentSpec(**item)
                except (TypeError, ValueError):
                    continue
                parsed[spec.stage] = spec
            return list(parsed.values())
        except (json.JSONDecodeError, TypeError, ValueError):
            return []

    def get_admin_allowed_principals(self) -> set[str]:
        """Parse ADMIN_ALLOWED_PRINCIPALS into a lowercase set.

        An empty result means nobody is allowed — the admin surface fails
        closed rather than defaulting to open access.
        """
        if not self.admin_allowed_principals:
            return set()
        return {
            part.strip().lower()
            for part in self.admin_allowed_principals.split(",")
            if part.strip()
        }

    @property
    def use_email(self) -> bool:
        """Check if email should be used (vs console output).

        An empty value counts as unset. The deployment template always defines
        these variables, so an ``is not None`` check reads an unconfigured
        recipient as configured and sends to "" — which the transport rejects
        while the console fallback is skipped, losing the digest entirely.
        """
        has_transport = bool(
            self.communication_services_connection_string or self.communication_services_endpoint
        )
        return bool(
            has_transport
            and self.email_sender_address
            and (self.email_recipient_address or self.get_subscribers())
        )

    def get_subscribers(self) -> list[Subscriber]:
        """Parse SUBSCRIBERS JSON string into a list of Subscriber objects.

        Deduplicates by email address (last entry wins). If duplicate emails
        are found, a warning is logged because one email address should have
        exactly one language/role configuration — duplicates cause redundant
        LLM customization calls and double email delivery.

        Returns:
            List of unique subscribers, or empty list if not configured
        """
        if not self.subscribers:
            return []
        try:
            raw = json.loads(self.subscribers)
            if not isinstance(raw, list):
                return []
            parsed = [Subscriber(**item) for item in raw]

            # Deduplicate by email (last wins)
            seen: dict[str, Subscriber] = {}
            for sub in parsed:
                if sub.email in seen:
                    import structlog

                    structlog.get_logger().warning(
                        "duplicate_subscriber_email",
                        email=sub.email,
                        kept_name=sub.name,
                        kept_language=sub.language,
                        replaced_name=seen[sub.email].name,
                        replaced_language=seen[sub.email].language,
                    )
                seen[sub.email] = sub
            return list(seen.values())
        except (json.JSONDecodeError, TypeError, ValueError):
            return []


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


def get_azure_credential():
    """Create DefaultAzureCredential with proper Managed Identity configuration.

    When using User Assigned Managed Identity, the azure_client_id setting value
    is passed as managed_identity_client_id. If not set, System Assigned MI is used.

    Returns:
        Configured DefaultAzureCredential instance
    """
    from azure.identity import DefaultAzureCredential

    settings = get_settings()
    kwargs = {}
    if settings.azure_client_id:
        kwargs["managed_identity_client_id"] = settings.azure_client_id
    return DefaultAzureCredential(**kwargs)
