"""Addressable store for tool results that exceed the prompt budget.

Oversized tool results used to be hard-cut to ``TOOL_RESULT_BUDGET_CHARS``
before the model ever saw them, so rows past the cutoff were unrecoverable and
the model could not even tell they existed — the same query would report a
resource as affected or unaffected depending on row ordering.

The full result is retained here instead. The prompt gets a preview plus a
``ref`` the model can query, which turns an irreversible truncation into a
retrievable one. This is the context-centric decomposition idea from Recursive
Language Models (Zhang & Khattab, 2025) without a code-executing REPL: this
process holds tenant-wide credentials and ingests untrusted RSS and web content,
so ``exec`` on model output would be a prompt-injection to RCE chain. If a real
REPL is ever needed, use the managed Foundry Code Interpreter instead.
"""

from __future__ import annotations

import itertools
import threading
from collections import OrderedDict
from dataclasses import dataclass

from structlog import get_logger

from src.agent.resilience import TOOL_RESULT_BUDGET_CHARS

logger = get_logger()

# Bounded so a long run cannot grow the store without limit; entries are evicted
# oldest-first. Sized against a real tenant: a 438-resource enumeration projecting
# `properties` renders to ~1.5 M chars, and an entry cut below that turns the store's
# "searched in full" guarantee into a false claim.
MAX_ENTRY_CHARS = 2_000_000
MAX_TOTAL_CHARS = 16_000_000


@dataclass
class StoredResult:
    """A full tool result held outside the prompt, addressable by ``ref``."""

    ref: str
    tool: str
    content: str
    trace_id: str = ""
    task_id: str = ""
    original_chars: int = 0

    @property
    def char_count(self) -> int:
        return len(self.content)

    @property
    def line_count(self) -> int:
        return self.content.count("\n") + 1

    @property
    def is_partial(self) -> bool:
        """True when the entry itself was capped, so absence cannot be confirmed."""
        return self.original_chars > len(self.content)


class ToolResultStore:
    """Bounded, ref-addressable store of full tool results.

    Refs are unique per store instance, so concurrent analyses sharing one
    store cannot read each other's entries.
    """

    def __init__(
        self,
        max_entry_chars: int = MAX_ENTRY_CHARS,
        max_total_chars: int = MAX_TOTAL_CHARS,
    ):
        self._entries: "OrderedDict[str, StoredResult]" = OrderedDict()
        self._lock = threading.Lock()
        self._counter = itertools.count(1)
        self._max_entry_chars = max_entry_chars
        self._max_total_chars = max_total_chars
        self._total_chars = 0

    def put(
        self,
        *,
        tool: str,
        result: str,
        trace_id: str = "",
        task_id: str = "",
    ) -> StoredResult:
        """Store a full result and return its handle.

        Args:
            tool: Name of the tool that produced the result.
            result: Full, untruncated result text.
            trace_id: Analysis trace this result belongs to.
            task_id: Task within the plan, when the result came from execution.

        Returns:
            The stored entry, including its generated ``ref``.
        """
        content = result
        if len(content) > self._max_entry_chars:
            content = content[: self._max_entry_chars] + "\n... (exceeded store capacity)"
            logger.warning(
                "tool_result_capped",
                tool=tool,
                task_id=task_id,
                original_chars=len(result),
                kept_chars=len(content),
            )

        with self._lock:
            ref = f"R{next(self._counter)}"
            entry = StoredResult(
                ref=ref,
                tool=tool,
                content=content,
                trace_id=trace_id,
                task_id=task_id,
                original_chars=len(result),
            )
            self._entries[ref] = entry
            self._total_chars += len(content)
            self._evict_locked()
        return entry

    def get(self, ref: str) -> StoredResult | None:
        """Look up a stored result by ref, or None if it is unknown or evicted."""
        with self._lock:
            return self._entries.get(str(ref).strip())

    def clear_trace(self, trace_id: str) -> int:
        """Drop every entry belonging to one analysis. Returns the count removed."""
        if not trace_id:
            return 0
        with self._lock:
            refs = [r for r, e in self._entries.items() if e.trace_id == trace_id]
            for ref in refs:
                self._total_chars -= len(self._entries.pop(ref).content)
        return len(refs)

    @property
    def total_chars(self) -> int:
        return self._total_chars

    def __len__(self) -> int:
        return len(self._entries)

    def _evict_locked(self) -> None:
        while self._total_chars > self._max_total_chars and len(self._entries) > 1:
            _, evicted = self._entries.popitem(last=False)
            self._total_chars -= len(evicted.content)
            logger.warning(
                "tool_result_evicted",
                ref=evicted.ref,
                tool=evicted.tool,
                chars=len(evicted.content),
            )


_store: ToolResultStore | None = None
_store_lock = threading.Lock()


def get_result_store() -> ToolResultStore:
    """Get the process-wide tool result store, creating it on first use."""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = ToolResultStore()
    return _store


def build_result_handle(
    stored: StoredResult,
    budget: int = TOOL_RESULT_BUDGET_CHARS,
) -> str:
    """Render a preview of a stored result plus instructions for reaching the rest.

    Args:
        stored: The stored full result.
        budget: Maximum preview characters to inline into the prompt.

    Returns:
        The content unchanged when it fits, otherwise a preview with a ref hint.
    """
    content = stored.content
    if len(content) <= budget:
        return content

    preview = content[:budget]
    # Cut at a line boundary so a resource row is never shown half-formed.
    cut = preview.rfind("\n")
    if cut > budget // 2:
        preview = preview[:cut]
    shown_lines = preview.count("\n") + 1

    return (
        f"{preview}\n"
        f"... [TRUNCATED PREVIEW — showing {len(preview):,} of {stored.char_count:,} chars, "
        f"{shown_lines:,} of {stored.line_count:,} lines]\n"
        f"[ref={stored.ref}] The FULL result is retained and searchable. Call "
        f'query_tool_result(ref="{stored.ref}", pattern="<text>") to search every line, '
        f'or mode="tail"/"stats" to inspect the rest. '
        f"Do NOT conclude that a resource is absent based on this preview alone."
    )


def store_and_handle(
    *,
    tool: str,
    result: str,
    trace_id: str = "",
    task_id: str = "",
    budget: int = TOOL_RESULT_BUDGET_CHARS,
) -> str:
    """Budget a tool result for the prompt, keeping the overflow retrievable.

    Results within budget are passed through unstored, so only genuine
    overflow consumes memory.

    Args:
        tool: Name of the tool that produced the result.
        result: Full, untruncated result text.
        trace_id: Analysis trace this result belongs to.
        task_id: Task within the plan, when the result came from execution.
        budget: Maximum characters to inline into the prompt.

    Returns:
        The result itself, or a preview carrying a queryable ref.
    """
    if len(result) <= budget:
        return result

    stored = get_result_store().put(tool=tool, result=result, trace_id=trace_id, task_id=task_id)
    logger.info(
        "tool_result_stored",
        ref=stored.ref,
        tool=tool,
        task_id=task_id,
        full_chars=stored.char_count,
        full_lines=stored.line_count,
        budget=budget,
    )
    return build_result_handle(stored, budget)
