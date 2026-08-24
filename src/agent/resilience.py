"""Resilience utilities for AzBrief agent — retry, backoff, circuit breaker.

Implements standard resilience patterns for agentic AI systems:
- Differential retry strategy (foreground vs background)
- Exponential backoff with jitter + server retry-after support
- Circuit breaker with auto-reset timeout
- Diminishing returns detection for agent loop iterations
- Wall-clock run budget (Azure Automation 3-hour fair-share limit)
- Multi-turn output recovery for token limit hits
- Model fallback on consecutive overload errors
- Connection error recovery (stale connection detection)
- Tool result budget enforcement
- Error withholding pattern (don't surface recoverable errors)
- Tool concurrency partitioning (safe=parallel, unsafe=serial)
"""

import asyncio
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from structlog import get_logger

logger = get_logger()


# ---------------------------------------------------------------------------
# Transition types for agent state machine
# ---------------------------------------------------------------------------


class TransitionType(str, Enum):
    """Explicit state machine transition types.

    Terminal transitions end the agent loop.
    Continue transitions allow the loop to proceed.

    Design: Each transition is a typed enum, not a string. This prevents
    typos from silently breaking control flow and makes all possible
    transitions discoverable via IDE autocomplete.
    """

    # Terminal transitions — the query loop returned
    COMPLETED = "completed"
    MODEL_ERROR = "model_error"
    MAX_TURNS = "max_turns"
    PROMPT_TOO_LONG = "prompt_too_long"
    ABORTED = "aborted"

    # Continue transitions — the loop will iterate again
    TOOL_USE = "tool_use"
    OUTPUT_RECOVERY = "output_recovery"
    COMPACT_RETRY = "compact_retry"
    STOP_HOOK_BLOCKING = "stop_hook_blocking"


TERMINAL_TRANSITIONS = frozenset(
    {
        TransitionType.COMPLETED,
        TransitionType.MODEL_ERROR,
        TransitionType.MAX_TURNS,
        TransitionType.PROMPT_TOO_LONG,
        TransitionType.ABORTED,
    }
)


@dataclass(frozen=True)
class Transition:
    """Immutable state transition descriptor."""

    type: TransitionType
    reason: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.type in TERMINAL_TRANSITIONS


# ---------------------------------------------------------------------------
# Exponential backoff with jitter
# ---------------------------------------------------------------------------


def calculate_backoff(
    attempt: int,
    base_delay: float = 1.0,
    max_delay: float = 32.0,
    jitter_fraction: float = 0.25,
    retry_after: Optional[float] = None,
) -> float:
    """Calculate exponential backoff delay with jitter.

    Pattern: delay = min(base * 2^attempt, max) * (1 + random(0, jitter))
    Server's retry-after header overrides calculation when present.

    Args:
        attempt: Attempt number (0-based)
        base_delay: Base delay in seconds
        max_delay: Maximum delay cap in seconds (default: 32s)
        jitter_fraction: Random jitter as fraction of delay (0-1)
        retry_after: Server-provided retry-after value (overrides calculation)

    Returns:
        Delay in seconds
    """
    if retry_after is not None and retry_after > 0:
        return retry_after

    delay = base_delay * (2**attempt)
    delay = min(delay, max_delay)
    jitter = delay * random.uniform(0, jitter_fraction)
    return delay + jitter


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------


@dataclass
class CircuitBreaker:
    """Circuit breaker to prevent cascading failures.

    Tracks consecutive failures and opens the circuit after threshold,
    preventing further calls until reset_timeout elapses.

    Rationale: without a breaker, a persistently failing dependency can
    trigger unbounded retry loops that waste API quota and amplify load on
    an already-degraded service. A low threshold (default 3) fails fast and
    transitions to a half-open state after a cool-down.
    """

    failure_threshold: int = 3
    reset_timeout: float = 60.0
    _consecutive_failures: int = field(default=0, init=False, repr=False)
    _last_failure_time: float = field(default=0.0, init=False, repr=False)
    _total_failures: int = field(default=0, init=False, repr=False)

    @property
    def is_open(self) -> bool:
        """Check if circuit is open (blocking calls)."""
        if self._consecutive_failures < self.failure_threshold:
            return False
        # Allow retry after reset_timeout (half-open state)
        if time.time() - self._last_failure_time > self.reset_timeout:
            return False
        return True

    def record_success(self) -> None:
        """Reset consecutive failure counter on success."""
        if self._consecutive_failures > 0:
            logger.debug(
                "circuit_breaker_reset",
                previous_failures=self._consecutive_failures,
            )
        self._consecutive_failures = 0

    def record_failure(self) -> None:
        """Increment failure counter."""
        self._consecutive_failures += 1
        self._total_failures += 1
        self._last_failure_time = time.time()
        logger.warning(
            "circuit_breaker_failure",
            consecutive_failures=self._consecutive_failures,
            threshold=self.failure_threshold,
            is_open=self.is_open,
        )

    def reset(self) -> None:
        """Manually reset the circuit breaker."""
        self._consecutive_failures = 0
        self._last_failure_time = 0.0


# ---------------------------------------------------------------------------
# Retry with backoff
# ---------------------------------------------------------------------------


def _is_retryable_status(error: Exception, retryable_codes: tuple[int, ...]) -> bool:
    """Check if an error has a retryable HTTP status code."""
    # Check common Azure SDK / OpenAI error patterns
    error_str = str(error)
    for code in retryable_codes:
        if str(code) in error_str:
            return True
    # Check for status_code attribute (Azure SDK pattern)
    status = getattr(error, "status_code", None)
    if status and status in retryable_codes:
        return True
    return False


def _is_stale_connection_error(error: Exception) -> bool:
    """Detect stale keep-alive connections (ECONNRESET/EPIPE).

    Stale HTTP keep-alive sockets cause intermittent connection failures.
    Detecting them allows targeted recovery (disable pooling + reconnect)
    instead of a generic retry.
    """
    error_str = str(error).lower()
    return any(
        marker in error_str for marker in ("econnreset", "epipe", "connection reset", "broken pipe")
    )


def _extract_retry_after(error: Exception) -> Optional[float]:
    """Extract retry-after value from error headers if available."""
    # Try to get from Azure SDK / OpenAI error response
    response = getattr(error, "response", None)
    if response is not None:
        headers = getattr(response, "headers", {})
        retry_after = headers.get("Retry-After") or headers.get("retry-after")
        if retry_after:
            try:
                return float(retry_after)
            except (ValueError, TypeError):
                pass
    return None


class ModelFallbackError(Exception):
    """Raised when consecutive overload errors trigger model fallback.

    After MAX_CONSECUTIVE_OVERLOAD_ERRORS consecutive 529 errors, this is
    raised to signal the caller to switch to a fallback model. It cleanly
    separates retry exhaustion from model-switching logic.
    """

    def __init__(self, original_model: str, consecutive_errors: int):
        self.original_model = original_model
        self.consecutive_errors = consecutive_errors
        super().__init__(
            f"Model fallback triggered after {consecutive_errors} consecutive "
            f"overload errors on {original_model}"
        )


# Maximum consecutive 529 errors before triggering model fallback
MAX_CONSECUTIVE_OVERLOAD_ERRORS = 3


async def retry_with_backoff(
    func: Callable,
    *,
    max_retries: int = 3,
    retryable_errors: tuple[int, ...] = (429, 503, 529),
    base_delay: float = 1.0,
    max_delay: float = 32.0,
    is_foreground: bool = True,
    circuit_breaker: Optional[CircuitBreaker] = None,
) -> Any:
    """Execute an async function with differential retry strategy.

    Strategy:
    - Foreground calls: retry with exponential backoff + jitter
    - Background calls: fail immediately on overload (prevent gateway amplification)
    - Track consecutive 529s for model fallback trigger
    - Detect stale connections for targeted recovery
    - Non-retryable errors don't trip circuit breaker

    Args:
        func: Async callable to execute
        max_retries: Maximum retry attempts
        retryable_errors: HTTP status codes to retry on
        base_delay: Base delay for backoff calculation
        max_delay: Maximum delay cap
        is_foreground: True for user-facing calls, False for background tasks
        circuit_breaker: Optional circuit breaker instance

    Returns:
        Result of the function call

    Raises:
        Exception: The last error if all retries are exhausted
        ModelFallbackError: If consecutive overload errors exceed threshold
    """
    last_error: Optional[Exception] = None
    consecutive_overload: int = 0

    for attempt in range(max_retries + 1):
        # Check circuit breaker
        if circuit_breaker and circuit_breaker.is_open:
            raise RuntimeError(
                f"Circuit breaker open after {circuit_breaker.failure_threshold} "
                f"consecutive failures"
            )

        try:
            result = await func()
            if circuit_breaker:
                circuit_breaker.record_success()
            consecutive_overload = 0  # Reset on success
            return result

        except Exception as e:
            last_error = e

            # Stale connection: log for targeted recovery
            if _is_stale_connection_error(e):
                logger.warning(
                    "stale_connection_detected",
                    error=str(e)[:200],
                    attempt=attempt + 1,
                )
                # Allow retry without counting as retryable error
                if attempt < max_retries:
                    await asyncio.sleep(calculate_backoff(attempt, base_delay=0.5))
                    continue
                raise

            if not _is_retryable_status(e, retryable_errors):
                # Non-retryable errors (400 Bad Request, auth errors) indicate
                # a caller bug, not a service outage — don't trip circuit breaker
                raise

            # Track consecutive overload (529) errors for model fallback
            if "529" in str(e) or "overloaded" in str(e).lower():
                consecutive_overload += 1
                if consecutive_overload >= MAX_CONSECUTIVE_OVERLOAD_ERRORS:
                    if circuit_breaker:
                        circuit_breaker.record_failure()
                    raise ModelFallbackError(
                        original_model="current",
                        consecutive_errors=consecutive_overload,
                    )

            # Background tasks fail immediately on overload
            # (prevents gateway amplification when the service is degraded)
            if not is_foreground:
                logger.warning(
                    "background_task_overload_fail",
                    error=str(e),
                    reason="Background tasks do not retry on overload",
                )
                if circuit_breaker:
                    circuit_breaker.record_failure()
                raise

            if attempt >= max_retries:
                if circuit_breaker:
                    circuit_breaker.record_failure()
                raise

            # Calculate delay with server retry-after support
            retry_after = _extract_retry_after(e)
            delay = calculate_backoff(
                attempt,
                base_delay=base_delay,
                max_delay=max_delay,
                retry_after=retry_after,
            )

            logger.info(
                "retry_with_backoff",
                attempt=attempt + 1,
                max_retries=max_retries,
                delay_s=round(delay, 2),
                error=str(e)[:200],
                retry_after=retry_after,
                consecutive_overload=consecutive_overload,
            )
            await asyncio.sleep(delay)

    # Should not reach here, but satisfy type checker
    if last_error:
        raise last_error
    raise RuntimeError("retry_with_backoff exhausted without error")


# ---------------------------------------------------------------------------
# Error withholding pattern
# ---------------------------------------------------------------------------


async def withhold_and_recover(
    operation: Callable,
    recovery: Callable,
    *,
    fallback_result: Any = None,
    error_label: str = "operation",
) -> tuple[Any, bool]:
    """Execute an operation, withholding errors until recovery is attempted.

    Recoverable errors (prompt-too-long, max-output-tokens) are NOT
    surfaced to callers until recovery is attempted. They are surfaced
    only if recovery fails.

    This prevents intermediate errors from terminating sessions when
    recovery could have succeeded.

    Args:
        operation: Async callable to execute
        recovery: Async callable to attempt on failure (receives the error)
        fallback_result: Value to return if both operation and recovery fail
        error_label: Label for logging

    Returns:
        Tuple of (result, recovered). recovered=True if recovery was needed.
    """
    try:
        result = await operation()
        return result, False
    except Exception as original_error:
        logger.info(
            "error_withheld_attempting_recovery",
            error_label=error_label,
            error=str(original_error)[:200],
        )
        try:
            recovery_result = await recovery(original_error)
            logger.info(
                "error_recovery_succeeded",
                error_label=error_label,
            )
            return recovery_result, True
        except Exception as recovery_error:
            logger.warning(
                "error_recovery_failed",
                error_label=error_label,
                original_error=str(original_error)[:200],
                recovery_error=str(recovery_error)[:200],
            )
            if fallback_result is not None:
                return fallback_result, True
            raise original_error from recovery_error


# ---------------------------------------------------------------------------
# Diminishing Returns Tracker
# ---------------------------------------------------------------------------


@dataclass
class DiminishingReturnsTracker:
    """Detect when agent loop iterations produce insufficient new content.

    Tracks the delta (not cumulative) output per iteration. If several
    consecutive iterations each add less than a minimum threshold of new
    content, the loop is judged to have plateaued and is stopped to avoid
    wasteful token consumption on unfixable queries.
    """

    min_delta_chars: int = 500
    lookback_window: int = 3
    _deltas: list[int] = field(default_factory=list, init=False)

    def record_iteration(self, new_content_chars: int) -> None:
        """Record the output size of an iteration."""
        self._deltas.append(new_content_chars)

    @property
    def should_stop(self) -> bool:
        """Check if diminishing returns threshold is reached."""
        if len(self._deltas) < self.lookback_window:
            return False
        recent = self._deltas[-self.lookback_window :]
        return all(d < self.min_delta_chars for d in recent)

    @property
    def iteration_count(self) -> int:
        return len(self._deltas)


# ---------------------------------------------------------------------------
# Wall-clock run budget
# ---------------------------------------------------------------------------


@dataclass
class RunDeadline:
    """Wall-clock budget for a single end-to-end run.

    A Container Apps Job kills the replica at ``replicaTimeout`` and work in
    flight is lost outright. Callers check the remaining budget before starting
    each unit of work and stop cleanly while they still have time to commit the
    checkpoint.

    A non-positive budget disables the deadline (always has budget).
    """

    budget_s: float
    _started: float = field(default_factory=time.monotonic, init=False)

    @property
    def enabled(self) -> bool:
        return self.budget_s > 0

    def elapsed_s(self) -> float:
        return time.monotonic() - self._started

    def remaining_s(self) -> float:
        """Seconds left, or ``float('inf')`` when the deadline is disabled."""
        if not self.enabled:
            return float("inf")
        return self.budget_s - self.elapsed_s()

    @property
    def expired(self) -> bool:
        return self.remaining_s() <= 0

    def has_budget_for(self, estimate_s: float) -> bool:
        """Check whether ``estimate_s`` of work still fits in the budget."""
        return self.remaining_s() >= estimate_s


# ---------------------------------------------------------------------------
# Multi-turn output recovery
# ---------------------------------------------------------------------------

# When the model hits its output token limit, inject a meta-message asking
# it to continue without apology or recap. This allows multi-turn recovery
# for long outputs that exceed the single-response token limit.
OUTPUT_RECOVERY_MESSAGE = (
    "Output token limit hit. Resume directly — no apology, no recap of "
    "what you were doing. Pick up mid-thought if that is where the cut "
    "happened. Break remaining work into smaller pieces."
)

MAX_OUTPUT_RECOVERY_ATTEMPTS = 3


# ---------------------------------------------------------------------------
# Tool result budget
# ---------------------------------------------------------------------------

# Enforce a per-message budget on aggregate tool result size. Prevents large
# tool results from consuming the entire context window.
#
# Sized to fit a typical administrator's full resource enumeration for one
# resource type (e.g. ~25-30 storage accounts with rich projections). A budget
# that is too small silently drops resources past the cutoff, which caused
# non-deterministic "affected resource" results run-to-run: the specific
# affected account could land before or after the boundary depending on result
# ordering, so the same update sometimes reported 1 affected and sometimes 0.
TOOL_RESULT_BUDGET_CHARS = 8000


def truncate_tool_result(result: str, budget: int = TOOL_RESULT_BUDGET_CHARS) -> str:
    """Truncate tool result to budget, preserving useful content.

    Large tool results that exceed the budget are truncated with an
    indicator appended. AzBrief truncates in place rather than persisting
    to disk, since results are not referenceable by file path.

    Args:
        result: Raw tool result string
        budget: Maximum characters to keep

    Returns:
        Truncated result with indicator if truncated
    """
    if len(result) <= budget:
        return result
    return result[:budget] + "\n... (truncated)"


# ---------------------------------------------------------------------------
# Tool concurrency partitioning
# ---------------------------------------------------------------------------


def partition_tool_calls(
    tool_calls: list[dict[str, Any]],
    tools_by_name: dict[str, Any],
) -> list[dict[str, Any]]:
    """Partition tool calls into parallel and serial batches.

    General-purpose partitioner for an **LLM tool-calling loop**, where the model
    returns a heterogeneous list of tool calls and each tool object declares its
    own ``is_read_only`` attribute. AzBrief's ``AzureUpdateAnalyzer._execution_node``
    runs a *task-based* executor instead and applies the same safe=parallel /
    unsafe=serial policy via ``tools.WRITE_TOOL_NAMES`` (no per-tool attribute
    needed because every current tool is read-only). This helper stays here as the
    tested primitive for a future tool-calling execution path.

    Rules:
    - Consecutive concurrency-safe (read-only) tools → one parallel batch
    - Non-concurrency-safe tools → serial execution (one per batch)
    - If the safety check throws OR the tool is unknown → treat as False (fail-closed)

    Args:
        tool_calls: List of tool call dicts with 'name' and 'args'
        tools_by_name: Dict mapping tool names to tool objects

    Returns:
        List of batch dicts: {is_parallel: bool, calls: [...]}
    """
    batches: list[dict[str, Any]] = []
    current_safe: list[dict[str, Any]] = []

    for call in tool_calls:
        tool = tools_by_name.get(call.get("name", ""))
        try:
            # Check if tool declares itself as concurrency-safe
            safe = bool(getattr(tool, "is_read_only", False))
        except Exception:
            safe = False  # Fail-closed: if check throws, treat as unsafe

        if safe:
            current_safe.append(call)
        else:
            if current_safe:
                batches.append({"is_parallel": True, "calls": current_safe})
                current_safe = []
            batches.append({"is_parallel": False, "calls": [call]})

    if current_safe:
        batches.append({"is_parallel": True, "calls": current_safe})

    return batches


# ---------------------------------------------------------------------------
# JSON parsing with multi-strategy fallback
# ---------------------------------------------------------------------------


def parse_json_resilient(raw: str) -> Optional[dict[str, Any]]:
    """Parse JSON from LLM response using multi-strategy fallback.

    Strategy:
    1. Direct parse after cleaning
    2. strict=False to allow control characters
    3. Progressive truncation to find valid JSON

    Args:
        raw: Raw text potentially containing JSON

    Returns:
        Parsed dict or None if all strategies fail
    """
    import json
    import re

    # Strip markdown fences
    json_match = re.search(r"```(?:json)?\s*(\{.*)", raw, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
        if "```" in json_str:
            json_str = json_str[: json_str.rfind("```")]
    else:
        json_str = raw

    # Find JSON object start
    start = json_str.find("{")
    if start < 0:
        return None
    json_str = json_str[start:]

    # Clean common LLM artifacts
    json_str = json_str.replace("\\'", "'")
    json_str = re.sub(r",\s*([}\]])", r"\1", json_str)

    # Strategy 1: Direct parse
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass

    # Strategy 2: strict=False (allow control characters in strings)
    try:
        return json.loads(json_str, strict=False)
    except (json.JSONDecodeError, ValueError):
        pass

    # Strategy 3: Balance braces and close
    try:
        brace_count = 0
        in_string = False
        escape_next = False
        for i, char in enumerate(json_str):
            if escape_next:
                escape_next = False
                continue
            if char == "\\":
                escape_next = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == "{":
                brace_count += 1
            elif char == "}":
                brace_count -= 1
                if brace_count == 0:
                    return json.loads(json_str[: i + 1], strict=False)
        # Try closing open braces
        if brace_count > 0:
            candidate = json_str + "}" * brace_count
            return json.loads(candidate, strict=False)
    except (json.JSONDecodeError, ValueError):
        pass

    return None
