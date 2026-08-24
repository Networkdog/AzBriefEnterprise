"""Prompt templates for the Azure Update Analyzer Agent.

This package provides phase-specific prompt assembly to minimize context
window usage. Instead of sending all ~31K tokens of instructions to every
LLM call, each phase receives only the sections it needs.

## Architecture

System prompt is assembled from modules:
- core.py:       Identity, mission, accuracy (~3K chars) — ALL phases
- analysis.py:   Assessment axes, quality standards (~6K chars) — Plan, Evaluate, Report
- tools.py:      Tool descriptions, KQL tips (~3K chars) — Plan, Execute only
- writing.py:    Report writing standards (~3K chars) — Report only
- languages/:    Language-specific style guide (~3-13K chars) — Report only, one language
- workflow.py:   Brief workflow overview (~0.5K chars) — Plan only

Report prompt is assembled from:
- report/base.py:        Before/after category sections
- report/categories.py:  Category templates (only one selected per report)

## Usage

    from src.agent.prompts import build_system_prompt, build_report_prompt

    # Planning phase — includes tools, excludes writing/language guides
    system = build_system_prompt(phase="planning")

    # Report phase — includes writing, language guide, excludes tools
    system = build_system_prompt(phase="report", language="ko")

    # Report prompt — includes only the relevant category template
    report = build_report_prompt(category="retirement", **format_args)

Backward-compatible constants (SYSTEM_PROMPT, REPORT_PROMPT, etc.) are still
exported for any code that hasn't migrated to the dynamic API.
"""

from src.agent.prompts.analysis import ANALYSIS_PERSPECTIVES_PROMPT
from src.agent.prompts.core import CORE_PROMPT
from src.agent.prompts.languages import get_style_guide, get_translation_notes
from src.agent.prompts.languages.en import ENGLISH_STYLE_GUIDE
from src.agent.prompts.languages.ja import JAPANESE_STYLE_GUIDE
from src.agent.prompts.languages.ko import KOREAN_STYLE_GUIDE
from src.agent.prompts.phases import (
    ANALYSIS_PROMPT,
    EVALUATION_PROMPT,
    EXECUTION_RETRY_PROMPT,
    PLANNING_PROMPT,
    REVISE_TASKS_PROMPT,
)
from src.agent.prompts.report.base import REPORT_AFTER, REPORT_BEFORE
from src.agent.prompts.report.categories import CATEGORY_INTRO, CATEGORY_TEMPLATES
from src.agent.prompts.subscriber import SUBSCRIBER_CUSTOMIZATION_PROMPT
from src.agent.prompts.tools import TOOLS_PROMPT
from src.agent.prompts.workflow import WORKFLOW_PROMPT
from src.agent.prompts.writing import WRITING_PROMPT

# ── Language guide registry ────────────────────────────────────


def get_language_guide(language: str) -> str:
    """Get the style guide for a specific language.

    Args:
        language: Language code or tag (``ko``, ``ko-KR``, ``fr`` ...). Codes
            without a curated module get a generic guide built from the
            :mod:`src.i18n` registry entry.

    Returns:
        The language-specific style guide text. Never empty.
    """
    return get_style_guide(language)


# ── Phase-specific system prompt builder ───────────────────────


def build_system_prompt(
    phase: str = "full",
    language: str = "ko",
    custom_suffix: str = "",
) -> str:
    """Build a phase-optimized system prompt.

    Args:
        phase: One of "planning", "execution", "evaluation", "report", "full".
        language: Report language code for language-specific style guide.
        custom_suffix: Optional custom system prompt suffix from settings.

    Returns:
        Assembled system prompt string with only relevant sections.

    Phase content matrix:
        Section          | planning | execution | evaluation | report | full
        -----------------+----------+-----------+------------+--------+-----
        core             |    ✓     |     ✓     |     ✓      |   ✓    |  ✓
        analysis         |    ✓     |           |     ✓      |   ✓    |  ✓
        tools            |    ✓     |     ✓     |            |        |  ✓
        writing          |          |           |            |   ✓    |  ✓
        language guide   |          |           |            |   ✓    |  ✓
        workflow         |    ✓     |           |            |        |  ✓
    """
    parts: list[str] = [CORE_PROMPT]

    if phase in ("planning", "evaluation", "report", "full"):
        parts.append(ANALYSIS_PERSPECTIVES_PROMPT)

    if phase in ("planning", "execution", "full"):
        parts.append(TOOLS_PROMPT)

    if phase in ("report", "full"):
        parts.append(WRITING_PROMPT)
        lang_guide = get_language_guide(language)
        if lang_guide:
            parts.append(lang_guide)

    if phase in ("planning", "full"):
        parts.append(WORKFLOW_PROMPT)

    result = "\n\n".join(parts)

    if custom_suffix:
        result += "\n\n## Additional Context (provided by administrator)\n" + custom_suffix

    return result


# ── Report prompt builder ──────────────────────────────────────


def build_report_prompt(
    category: str = "",
    **format_kwargs,
) -> str:
    """Build a report prompt with only the relevant category template.

    Args:
        category: Update category (retirement, new_feature, preview, etc.).
            If empty, all category templates are included (backward compat).
        **format_kwargs: Format variables (update_context, resource_summary,
            task_results_summary, report_language).

    Returns:
        Formatted report prompt string.
    """
    # Build category section
    if category and category in CATEGORY_TEMPLATES:
        cat_section = CATEGORY_INTRO + "\n\n---\n\n" + CATEGORY_TEMPLATES[category]
    else:
        # Fallback: include all categories (backward compatible)
        cat_section = (
            CATEGORY_INTRO + "\n\n---\n\n" + "\n\n---\n\n".join(CATEGORY_TEMPLATES.values())
        )

    raw_prompt = REPORT_BEFORE + "\n\n" + cat_section + "\n\n" + REPORT_AFTER

    if format_kwargs:
        return raw_prompt.format(**format_kwargs)
    return raw_prompt


# ── Backward-compatible constants ──────────────────────────────
# These reconstruct the full prompts for any code that hasn't migrated.

SYSTEM_PROMPT = build_system_prompt(phase="full", language="ko")

REPORT_PROMPT = (
    REPORT_BEFORE
    + "\n\n"
    + CATEGORY_INTRO
    + "\n\n---\n\n"
    + "\n\n---\n\n".join(CATEGORY_TEMPLATES.values())
    + "\n\n"
    + REPORT_AFTER
)

__all__ = [
    # Dynamic builders (preferred)
    "build_system_prompt",
    "build_report_prompt",
    "get_language_guide",
    "get_translation_notes",
    # Phase prompts
    "ANALYSIS_PROMPT",
    "PLANNING_PROMPT",
    "EXECUTION_RETRY_PROMPT",
    "EVALUATION_PROMPT",
    "REVISE_TASKS_PROMPT",
    "SUBSCRIBER_CUSTOMIZATION_PROMPT",
    # Backward-compatible full prompts
    "SYSTEM_PROMPT",
    "REPORT_PROMPT",
]
