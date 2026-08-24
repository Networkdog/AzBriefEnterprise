"""Tests for src/agent/resilience module.

Tests circuit breaker, exponential backoff, diminishing returns detection,
tool result budget enforcement, model fallback, stale connection detection,
tool concurrency partitioning, multi-strategy JSON parsing, error withholding,
and SSRF URL validation.
"""

import asyncio
import time

import pytest

from src.agent.resilience import (
    MAX_CONSECUTIVE_OVERLOAD_ERRORS,
    MAX_OUTPUT_RECOVERY_ATTEMPTS,
    OUTPUT_RECOVERY_MESSAGE,
    TERMINAL_TRANSITIONS,
    TOOL_RESULT_BUDGET_CHARS,
    CircuitBreaker,
    DiminishingReturnsTracker,
    ModelFallbackError,
    RunDeadline,
    Transition,
    TransitionType,
    _is_stale_connection_error,
    calculate_backoff,
    parse_json_resilient,
    partition_tool_calls,
    retry_with_backoff,
    truncate_tool_result,
    withhold_and_recover,
)

# ---------------------------------------------------------------------------
# TransitionType & Transition
# ---------------------------------------------------------------------------


class TestTransition:
    def test_terminal_transitions(self):
        for tt in TERMINAL_TRANSITIONS:
            t = Transition(type=tt, reason="test")
            assert t.is_terminal is True

    def test_continue_transitions(self):
        for tt in (
            TransitionType.TOOL_USE,
            TransitionType.OUTPUT_RECOVERY,
            TransitionType.COMPACT_RETRY,
        ):
            t = Transition(type=tt, reason="test")
            assert t.is_terminal is False

    def test_transition_immutable(self):
        t = Transition(type=TransitionType.COMPLETED, reason="done")
        with pytest.raises(AttributeError):
            t.type = TransitionType.ABORTED  # type: ignore[misc]


# ---------------------------------------------------------------------------
# calculate_backoff
# ---------------------------------------------------------------------------


class TestCalculateBackoff:
    def test_exponential_growth(self):
        d0 = calculate_backoff(0, base_delay=1.0, max_delay=100.0, jitter_fraction=0)
        d1 = calculate_backoff(1, base_delay=1.0, max_delay=100.0, jitter_fraction=0)
        d2 = calculate_backoff(2, base_delay=1.0, max_delay=100.0, jitter_fraction=0)
        assert d0 == pytest.approx(1.0)
        assert d1 == pytest.approx(2.0)
        assert d2 == pytest.approx(4.0)

    def test_max_delay_cap(self):
        d = calculate_backoff(10, base_delay=1.0, max_delay=32.0, jitter_fraction=0)
        assert d == pytest.approx(32.0)

    def test_jitter_adds_randomness(self):
        delays = {calculate_backoff(2, jitter_fraction=0.25) for _ in range(20)}
        # With jitter, we should get variation
        assert len(delays) > 1

    def test_retry_after_overrides(self):
        d = calculate_backoff(0, retry_after=60.0)
        assert d == pytest.approx(60.0)


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    def test_starts_closed(self):
        cb = CircuitBreaker(failure_threshold=3)
        assert cb.is_open is False

    def test_opens_after_threshold(self):
        cb = CircuitBreaker(failure_threshold=3, reset_timeout=60)
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open is False
        cb.record_failure()
        assert cb.is_open is True

    def test_success_resets_counter(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open is False

    def test_reset_closes_circuit(self):
        cb = CircuitBreaker(failure_threshold=2)
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open is True
        cb.reset()
        assert cb.is_open is False

    def test_auto_reset_after_timeout(self):
        cb = CircuitBreaker(failure_threshold=2, reset_timeout=0.01)
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open is True
        time.sleep(0.02)
        assert cb.is_open is False


# ---------------------------------------------------------------------------
# DiminishingReturnsTracker
# ---------------------------------------------------------------------------


class TestDiminishingReturnsTracker:
    def test_not_triggered_early(self):
        tracker = DiminishingReturnsTracker(min_delta_chars=500, lookback_window=3)
        tracker.record_iteration(100)
        tracker.record_iteration(100)
        assert tracker.should_stop is False

    def test_triggered_after_window(self):
        tracker = DiminishingReturnsTracker(min_delta_chars=500, lookback_window=3)
        tracker.record_iteration(100)
        tracker.record_iteration(200)
        tracker.record_iteration(300)
        assert tracker.should_stop is True

    def test_not_triggered_with_sufficient_content(self):
        tracker = DiminishingReturnsTracker(min_delta_chars=500, lookback_window=3)
        tracker.record_iteration(600)
        tracker.record_iteration(700)
        tracker.record_iteration(800)
        assert tracker.should_stop is False

    def test_mixed_iterations(self):
        tracker = DiminishingReturnsTracker(min_delta_chars=500, lookback_window=3)
        tracker.record_iteration(1000)
        tracker.record_iteration(100)  # low
        tracker.record_iteration(200)  # low
        tracker.record_iteration(50)  # low
        assert tracker.should_stop is True

    def test_iteration_count(self):
        tracker = DiminishingReturnsTracker()
        assert tracker.iteration_count == 0
        tracker.record_iteration(100)
        tracker.record_iteration(200)
        assert tracker.iteration_count == 2


# ---------------------------------------------------------------------------
# RunDeadline
# ---------------------------------------------------------------------------


class TestRunDeadline:
    def test_fresh_deadline_has_budget(self):
        deadline = RunDeadline(budget_s=100)
        assert deadline.enabled is True
        assert deadline.expired is False
        assert deadline.has_budget_for(50) is True
        assert 0 <= deadline.elapsed_s() < 1

    def test_rejects_work_larger_than_remaining(self):
        deadline = RunDeadline(budget_s=10)
        assert deadline.has_budget_for(5) is True
        assert deadline.has_budget_for(1000) is False

    def test_expired_when_budget_consumed(self, monkeypatch):
        deadline = RunDeadline(budget_s=10)
        base = time.monotonic()
        monkeypatch.setattr(time, "monotonic", lambda: base + 11)
        assert deadline.expired is True
        assert deadline.remaining_s() < 0
        assert deadline.has_budget_for(0) is False

    def test_zero_budget_disables_deadline(self):
        deadline = RunDeadline(budget_s=0)
        assert deadline.enabled is False
        assert deadline.expired is False
        assert deadline.remaining_s() == float("inf")
        assert deadline.has_budget_for(10**9) is True

    def test_negative_budget_disables_deadline(self):
        assert RunDeadline(budget_s=-1).has_budget_for(10**9) is True

    def test_first_unit_of_work_allowed_with_zero_estimate(self):
        # The runbook has no duration sample before the first update completes.
        deadline = RunDeadline(budget_s=1)
        assert deadline.has_budget_for(0) is True


# ---------------------------------------------------------------------------
# truncate_tool_result
# ---------------------------------------------------------------------------


class TestTruncateToolResult:
    def test_short_result_unchanged(self):
        result = "short"
        assert truncate_tool_result(result) == "short"

    def test_long_result_truncated(self):
        result = "x" * 5000
        truncated = truncate_tool_result(result, budget=100)
        assert len(truncated) < 200
        assert "truncated" in truncated

    def test_exact_budget_unchanged(self):
        result = "x" * TOOL_RESULT_BUDGET_CHARS
        assert truncate_tool_result(result) == result


# ---------------------------------------------------------------------------
# retry_with_backoff
# ---------------------------------------------------------------------------


class TestRetryWithBackoff:
    @pytest.mark.asyncio
    async def test_success_on_first_try(self):
        async def ok():
            return "ok"

        result = await retry_with_backoff(ok, max_retries=3)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_retries_on_transient_error(self):
        attempts = {"count": 0}

        async def fail_then_ok():
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise RuntimeError("429 rate limited")
            return "ok"

        result = await retry_with_backoff(
            fail_then_ok,
            max_retries=5,
            retryable_errors=(429,),
            base_delay=0.01,
        )
        assert result == "ok"
        assert attempts["count"] == 3

    @pytest.mark.asyncio
    async def test_non_retryable_error_raises_immediately(self):
        async def fail():
            raise ValueError("bad input")

        with pytest.raises(ValueError):
            await retry_with_backoff(fail, max_retries=3)

    @pytest.mark.asyncio
    async def test_background_fails_immediately_on_overload(self):
        async def overloaded():
            raise RuntimeError("529 overloaded")

        with pytest.raises(RuntimeError, match="529"):
            await retry_with_backoff(
                overloaded,
                max_retries=3,
                is_foreground=False,
            )

    @pytest.mark.asyncio
    async def test_circuit_breaker_blocks_call(self):
        cb = CircuitBreaker(failure_threshold=1)
        cb.record_failure()  # Open the circuit

        async def should_not_run():
            return "ok"

        with pytest.raises(RuntimeError, match="Circuit breaker open"):
            await retry_with_backoff(
                should_not_run,
                circuit_breaker=cb,
            )

    @pytest.mark.asyncio
    async def test_circuit_breaker_records_on_retry_exhaust(self):
        cb = CircuitBreaker(failure_threshold=5)

        async def always_fail():
            raise RuntimeError("429 rate limited")

        with pytest.raises(RuntimeError):
            await retry_with_backoff(
                always_fail,
                max_retries=2,
                retryable_errors=(429,),
                base_delay=0.01,
                circuit_breaker=cb,
            )
        # Circuit breaker records failure when retries are exhausted
        assert cb._consecutive_failures >= 1

    @pytest.mark.asyncio
    async def test_non_retryable_does_not_trip_breaker(self):
        """Non-retryable errors should NOT trip circuit breaker."""
        cb = CircuitBreaker(failure_threshold=2)

        async def bad_input():
            raise ValueError("invalid argument")

        with pytest.raises(ValueError):
            await retry_with_backoff(
                bad_input,
                max_retries=3,
                circuit_breaker=cb,
            )
        # Non-retryable error should not record failure
        assert cb._consecutive_failures == 0


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_tool_result_budget(self):
        assert TOOL_RESULT_BUDGET_CHARS == 8000

    def test_output_recovery_message(self):
        assert "Resume" in OUTPUT_RECOVERY_MESSAGE
        assert "apology" in OUTPUT_RECOVERY_MESSAGE.lower()

    def test_max_output_recovery(self):
        assert MAX_OUTPUT_RECOVERY_ATTEMPTS == 3

    def test_max_consecutive_overload(self):
        assert MAX_CONSECUTIVE_OVERLOAD_ERRORS == 3


# ---------------------------------------------------------------------------
# ModelFallbackError
# ---------------------------------------------------------------------------


class TestModelFallbackError:
    def test_creation(self):
        err = ModelFallbackError("gpt-4o", 3)
        assert err.original_model == "gpt-4o"
        assert err.consecutive_errors == 3
        assert "gpt-4o" in str(err)

    @pytest.mark.asyncio
    async def test_triggered_by_consecutive_overloads(self):
        attempts = {"count": 0}

        async def always_529():
            attempts["count"] += 1
            raise RuntimeError("529 overloaded server")

        with pytest.raises(ModelFallbackError) as exc_info:
            await retry_with_backoff(
                always_529,
                max_retries=10,
                retryable_errors=(529,),
                base_delay=0.01,
            )
        assert exc_info.value.consecutive_errors == MAX_CONSECUTIVE_OVERLOAD_ERRORS


# ---------------------------------------------------------------------------
# Stale connection detection
# ---------------------------------------------------------------------------


class TestStaleConnectionDetection:
    def test_econnreset_detected(self):
        err = ConnectionError("ECONNRESET: connection reset by peer")
        assert _is_stale_connection_error(err) is True

    def test_epipe_detected(self):
        err = BrokenPipeError("EPIPE: broken pipe")
        assert _is_stale_connection_error(err) is True

    def test_normal_error_not_detected(self):
        err = ValueError("invalid argument")
        assert _is_stale_connection_error(err) is False

    @pytest.mark.asyncio
    async def test_stale_connection_retried(self):
        attempts = {"count": 0}

        async def fail_then_ok():
            attempts["count"] += 1
            if attempts["count"] < 2:
                raise ConnectionError("Connection reset by peer")
            return "ok"

        result = await retry_with_backoff(
            fail_then_ok,
            max_retries=3,
            base_delay=0.01,
        )
        assert result == "ok"
        assert attempts["count"] == 2


# ---------------------------------------------------------------------------
# parse_json_resilient
# ---------------------------------------------------------------------------


class TestParseJsonResilient:
    def test_clean_json(self):
        result = parse_json_resilient('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_in_markdown(self):
        raw = '```json\n{"key": "value"}\n```'
        result = parse_json_resilient(raw)
        assert result == {"key": "value"}

    def test_trailing_comma(self):
        raw = '{"key": "value",}'
        result = parse_json_resilient(raw)
        assert result == {"key": "value"}

    def test_escaped_single_quote(self):
        raw = """{"key": "it\\'s fine"}"""
        result = parse_json_resilient(raw)
        assert result is not None
        assert "fine" in result["key"]

    def test_incomplete_json_closed(self):
        raw = '{"key": "value", "nested": {"a": 1'
        result = parse_json_resilient(raw)
        assert result is not None
        assert result["key"] == "value"

    def test_text_before_json(self):
        raw = 'Here is the analysis:\n{"relevance": "relevant"}'
        result = parse_json_resilient(raw)
        assert result == {"relevance": "relevant"}

    def test_no_json_returns_none(self):
        result = parse_json_resilient("no json here")
        assert result is None

    def test_control_chars_in_strings(self):
        raw = '{"key": "line1\\nline2"}'
        result = parse_json_resilient(raw)
        assert result is not None


# ---------------------------------------------------------------------------
# partition_tool_calls
# ---------------------------------------------------------------------------


class TestPartitionToolCalls:
    def test_all_safe_single_batch(self):
        class ReadOnlyTool:
            is_read_only = True

        tools = {"search": ReadOnlyTool(), "grep": ReadOnlyTool()}
        calls = [{"name": "search"}, {"name": "grep"}]
        batches = partition_tool_calls(calls, tools)
        assert len(batches) == 1
        assert batches[0]["is_parallel"] is True
        assert len(batches[0]["calls"]) == 2

    def test_all_unsafe_serial(self):
        class WriteTool:
            is_read_only = False

        tools = {"write1": WriteTool(), "write2": WriteTool()}
        calls = [{"name": "write1"}, {"name": "write2"}]
        batches = partition_tool_calls(calls, tools)
        assert len(batches) == 2
        assert all(not b["is_parallel"] for b in batches)

    def test_mixed_partitioned(self):
        class ReadTool:
            is_read_only = True

        class WriteTool:
            is_read_only = False

        tools = {"read": ReadTool(), "write": WriteTool(), "read2": ReadTool()}
        calls = [{"name": "read"}, {"name": "write"}, {"name": "read2"}]
        batches = partition_tool_calls(calls, tools)
        assert len(batches) == 3
        assert batches[0]["is_parallel"] is True
        assert batches[1]["is_parallel"] is False
        assert batches[2]["is_parallel"] is True

    def test_unknown_tool_treated_as_unsafe(self):
        calls = [{"name": "unknown_tool"}]
        batches = partition_tool_calls(calls, {})
        assert len(batches) == 1
        assert batches[0]["is_parallel"] is False

    def test_exception_in_check_treated_as_unsafe(self):
        class BrokenTool:
            @property
            def is_read_only(self):
                raise RuntimeError("broken")

        tools = {"broken": BrokenTool()}
        calls = [{"name": "broken"}]
        batches = partition_tool_calls(calls, tools)
        assert batches[0]["is_parallel"] is False


# ---------------------------------------------------------------------------
# withhold_and_recover
# ---------------------------------------------------------------------------


class TestWithholdAndRecover:
    @pytest.mark.asyncio
    async def test_success_no_recovery_needed(self):
        async def op():
            return "ok"

        async def recover(err):
            return "recovered"

        result, recovered = await withhold_and_recover(op, recover)
        assert result == "ok"
        assert recovered is False

    @pytest.mark.asyncio
    async def test_recovery_on_failure(self):
        async def op():
            raise RuntimeError("failed")

        async def recover(err):
            return "recovered"

        result, recovered = await withhold_and_recover(op, recover)
        assert result == "recovered"
        assert recovered is True

    @pytest.mark.asyncio
    async def test_fallback_when_both_fail(self):
        async def op():
            raise RuntimeError("op failed")

        async def recover(err):
            raise RuntimeError("recover failed")

        result, recovered = await withhold_and_recover(op, recover, fallback_result="fallback")
        assert result == "fallback"
        assert recovered is True

    @pytest.mark.asyncio
    async def test_raises_when_no_fallback(self):
        async def op():
            raise ValueError("op failed")

        async def recover(err):
            raise RuntimeError("recover failed")

        with pytest.raises(ValueError, match="op failed"):
            await withhold_and_recover(op, recover)

    @pytest.mark.asyncio
    async def test_recovery_receives_original_error(self):
        captured = {}

        async def op():
            raise ValueError("specific error")

        async def recover(err):
            captured["error"] = err
            return "fixed"

        await withhold_and_recover(op, recover)
        assert isinstance(captured["error"], ValueError)
        assert "specific" in str(captured["error"])


# ---------------------------------------------------------------------------
# SSRF URL validation
# ---------------------------------------------------------------------------


class TestSSRFProtection:
    def test_allowed_domains(self):
        from src.services.microsoft_learn import _is_allowed_url

        assert _is_allowed_url("https://learn.microsoft.com/docs") is True
        assert _is_allowed_url("https://azure.microsoft.com/updates") is True
        assert _is_allowed_url("https://github.com/Azure/repo") is True

    def test_blocked_domains(self):
        from src.services.microsoft_learn import _is_allowed_url

        assert _is_allowed_url("https://evil.com/steal") is False
        assert _is_allowed_url("https://attacker.microsoft.com.evil.com/") is False
        assert _is_allowed_url("http://169.254.169.254/metadata") is False

    def test_non_http_schemes_blocked(self):
        from src.services.microsoft_learn import _is_allowed_url

        assert _is_allowed_url("ftp://learn.microsoft.com/file") is False
        assert _is_allowed_url("file:///etc/passwd") is False
        assert _is_allowed_url("javascript:alert(1)") is False

    def test_malformed_urls(self):
        from src.services.microsoft_learn import _is_allowed_url

        assert _is_allowed_url("") is False
        assert _is_allowed_url("not-a-url") is False
