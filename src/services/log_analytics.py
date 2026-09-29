"""Azure Log Analytics Service using Azure SDK."""

import asyncio
import re
from datetime import timedelta
from typing import Any, Optional

from azure.monitor.query import LogsQueryClient, LogsQueryStatus
from structlog import get_logger

from src.config import get_settings

logger = get_logger()

FAILURE_RUN_ID_PATTERN = r"^(?:[a-fA-F0-9]{32})?$"


class LogAnalyticsService:
    """Service for Azure Log Analytics queries."""

    def __init__(self, workspace_id: Optional[str] = None):
        """Initialize Log Analytics service.

        Args:
            workspace_id: Log Analytics workspace ID (uses config if not provided)
        """
        settings = get_settings()
        self.workspace_id = workspace_id or getattr(settings, "log_analytics_workspace_id", None)
        self._client: Optional[LogsQueryClient] = None
        self._credential = None

    def _get_client(self) -> LogsQueryClient:
        """Get or create Log Analytics client."""
        if self._client is None:
            from src.config import get_azure_credential

            self._credential = get_azure_credential()
            self._client = LogsQueryClient(self._credential)
        return self._client

    async def query_logs(self, query: str, timespan: Optional[timedelta] = None) -> dict[str, Any]:
        """Execute a Log Analytics query.

        Args:
            query: KQL query string
            timespan: Time range for query (default: 24 hours)

        Returns:
            Query results
        """
        if not self.workspace_id:
            logger.warning("log_analytics_workspace_not_configured")
            return {
                "success": False,
                "error": "Log Analytics workspace ID not configured. Set LOG_ANALYTICS_WORKSPACE_ID in environment.",
                "data": [],
            }

        if timespan is None:
            timespan = timedelta(days=1)

        try:
            client = self._get_client()

            import time as _time

            _t0 = _time.time()
            logger.info(
                "log_analytics_query_start",
                workspace_id=self.workspace_id[:20] + "...",
                query=query[:200],
                timespan_hours=round(timespan.total_seconds() / 3600, 1),
            )

            response = await asyncio.to_thread(
                client.query_workspace,
                workspace_id=self.workspace_id,
                query=query,
                timespan=timespan,
            )
            _elapsed = _time.time() - _t0

            if response.status == LogsQueryStatus.SUCCESS:
                data = []
                for table in response.tables:
                    columns = table.columns
                    for row in table.rows:
                        data.append(dict(zip(columns, row)))

                logger.info(
                    "log_analytics_query_ok",
                    row_count=len(data),
                    table_count=len(response.tables),
                    elapsed_s=round(_elapsed, 2),
                )

                return {"success": True, "row_count": len(data), "data": data}
            else:
                logger.warning(
                    "log_analytics_query_status",
                    status=str(response.status),
                    elapsed_s=round(_elapsed, 2),
                )
                return {
                    "success": False,
                    "error": f"Query failed with status: {response.status}",
                    "data": [],
                }

        except Exception as e:
            logger.error("log_analytics_query_error", error=str(e), query=query[:200])
            return {"success": False, "error": str(e), "data": []}

    async def get_failure_events(
        self, hours: int = 24, limit: int = 50, run_id: str = ""
    ) -> dict[str, Any]:
        """Read bounded AzBrief failure events, optionally including a run's Hosted traces.

        Args:
            hours: Lookback period, from one hour to thirty days.
            limit: Maximum displayed events, from one to one hundred.
            run_id: Optional exact orchestrator run ID.

        Returns:
            Query envelope with up to limit + 1 rows for truncation detection.
        """
        if (
            type(hours) is not int
            or not 1 <= hours <= 720
            or type(limit) is not int
            or not 1 <= limit <= 100
            or not isinstance(run_id, str)
            or re.fullmatch(FAILURE_RUN_ID_PATTERN, run_id) is None
        ):
            logger.warning("admin_failure_query_invalid")
            return {"success": False, "error": "Invalid failure-history filters.", "data": []}

        query = (
            "let recent_failures = AzBriefFailures_CL\n"
            f"| where TimeGenerated > ago({hours}h)\n"
            "| where Source == 'AzBrief';\n"
        )
        if run_id:
            run_id = run_id.lower()
            query += (
                "let run_traces = recent_failures\n"
                f"| where RunId == '{run_id}'\n"
                "| where isnotempty(TraceId)\n"
                "| distinct TraceId;\n"
                "recent_failures\n"
                f"| where RunId == '{run_id}' or TraceId in (run_traces)\n"
            )
        else:
            query += "recent_failures\n"
        query += (
            "| project TimeGenerated, Level, Runtime, Event, Status, "
            "RunId, UpdateId, TraceId, TaskId, Phase, Operation, AgentRole, "
            "ErrorType, ErrorMessage = substring(ErrorMessage, 0, 4000), "
            "FailedCount, ArchiveFailedCount, PendingCount, DeferredCount\n"
            "| order by TimeGenerated desc, Event asc, RunId asc, TraceId asc\n"
            f"| take {limit + 1}"
        )
        return await self.query_logs(query, timedelta(hours=hours))

    async def close(self) -> None:
        """Release the SDK transport and this service's credential."""
        try:
            if self._client is not None:
                await asyncio.to_thread(self._client.close)
                self._client = None
        finally:
            if self._credential is not None:
                await asyncio.to_thread(self._credential.close)
                self._credential = None

    async def get_recent_errors(self, hours: int = 24, top: int = 50) -> dict[str, Any]:
        """Get recent errors from logs.

        Args:
            hours: Hours to look back
            top: Maximum number of errors to return

        Returns:
            Recent error logs
        """
        query = f"""
        union *
        | where TimeGenerated > ago({hours}h)
        | where Level == "Error" or Level == "Critical" 
            or ResultType == "Failed" or ResultType == "Failure"
            or isnotempty(ExceptionType)
        | summarize 
            ErrorCount = count(),
            LastOccurrence = max(TimeGenerated),
            FirstOccurrence = min(TimeGenerated)
            by Type, 
            ErrorMessage = coalesce(ExceptionMessage, Message, ResultDescription, "Unknown Error")
        | order by ErrorCount desc, LastOccurrence desc
        | limit {top}
        """

        result = await self.query_logs(query, timedelta(hours=hours))

        if result["success"]:
            return {
                "success": True,
                "period_hours": hours,
                "total_error_types": len(result["data"]),
                "errors": result["data"],
            }
        return result

    async def get_activity_log_summary(self, hours: int = 24) -> dict[str, Any]:
        """Get Azure Activity Log summary.

        Args:
            hours: Hours to look back

        Returns:
            Activity log summary with operations and callers
        """
        query = f"""
        AzureActivity
        | where TimeGenerated > ago({hours}h)
        | summarize 
            OperationCount = count(),
            SuccessCount = countif(ActivityStatusValue == "Success" or ActivityStatusValue == "Succeeded"),
            FailCount = countif(ActivityStatusValue == "Failed" or ActivityStatusValue == "Failure")
            by OperationName, Caller, ResourceGroup
        | order by OperationCount desc
        | limit 100
        """

        result = await self.query_logs(query, timedelta(hours=hours))

        if result["success"]:
            operations = result["data"]
            total_ops = sum(o.get("OperationCount", 0) for o in operations)
            total_failures = sum(o.get("FailCount", 0) for o in operations)

            return {
                "success": True,
                "period_hours": hours,
                "total_operations": total_ops,
                "total_failures": total_failures,
                "failure_rate": round(total_failures / max(total_ops, 1) * 100, 2),
                "operations": operations,
            }
        return result
