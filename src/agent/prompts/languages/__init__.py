"""Language-specific style guides — one module per language.

A language module (``<code>.py``) may expose:

* ``STYLE_GUIDE`` — report-writing rules injected during the report phase.
* ``TRANSLATION_NOTES`` — a few lines of per-language guidance injected into the
  subscriber translation prompt.

Both are optional. A language with no module at all still produces a coherent
report from a generic guide synthesized from its :class:`~src.i18n.LanguageSpec`,
so adding a language never requires writing a style guide up front.

Only the requested language's guide is injected per report, saving ~2-4K tokens.
"""

import importlib
from typing import Optional

from src.i18n import get_language, normalize_language, register_cache_clearer

# Older modules name the guide after the language; both spellings resolve.
_GUIDE_ATTRS = (
    "STYLE_GUIDE",
    "KOREAN_STYLE_GUIDE",
    "ENGLISH_STYLE_GUIDE",
    "JAPANESE_STYLE_GUIDE",
)

_GUIDE_CACHE: dict[str, str] = {}
_NOTES_CACHE: dict[str, str] = {}


def _clear_caches() -> None:
    _GUIDE_CACHE.clear()
    _NOTES_CACHE.clear()


register_cache_clearer(_clear_caches)


# ── Generic fallback guide ─────────────────────────────────────
# Written from the registry entry so a not-yet-curated language still gets real
# writing rules instead of an empty prompt section.

_GENERIC_STYLE_GUIDE = """### {display} — Style Guide

Write the entire report in **{display}**. A curated style guide for this language
does not exist yet, so apply these baseline rules.

**1. Native prose, not translation**
The report must read as if a senior cloud engineer wrote it in {english_name} from
scratch. Restructure sentences to fit {english_name} syntax instead of mirroring
English word order. If a literal rendering sounds unnatural, rewrite the sentence
rather than the words.

**2. One consistent register**
Use the formal-professional register of {english_name} technical documentation and
keep it identical from the first sentence to the last.

**3. Keep identifiers in English**
Azure service names, resource names, resource types, SKU names, CLI commands, KQL,
portal blade names and metric names stay in English exactly as Azure spells them.
Translating them makes the report unsearchable.

**4. Abbreviations**
On first use write the abbreviation followed by the full name in parentheses, then
use the abbreviation alone: `ARG(Azure Resource Graph)`.

**5. No translationese**
Avoid literal renderings of English function words and passive constructions that
{english_name} would not use. Prefer the active voice with a concrete subject. The
announcement is never the actor — the reader and their resources are.

**6. Vary sentence endings**
Do not end consecutive sentences with the same construction. Repetition is the
clearest signal of machine translation.

**7. Concept boxes**
Place a `> **Term**: explanation` box at the term's first mention, never grouped at
the end. Zero boxes is correct when nothing needs explaining; three is the maximum.
Never pad a report to fill a structure.
"""


def _synthesize_style_guide(code: str) -> str:
    """Build a baseline style guide for a language that has no module."""
    spec = get_language(code)
    return _GENERIC_STYLE_GUIDE.format(display=spec.display_name, english_name=spec.english_name)


# ── Module loading ─────────────────────────────────────────────


def _load_module(code: str):
    """Import a language module, or return None when it does not exist."""
    try:
        return importlib.import_module(f"{__name__}.{code.replace('-', '_')}")
    except ImportError:
        return None


def _read_attr(module, names: tuple[str, ...]) -> Optional[str]:
    for name in names:
        value = getattr(module, name, None)
        if isinstance(value, str) and value.strip():
            return value
    return None


def get_style_guide(language: str) -> str:
    """Return the report style guide for a language.

    Never returns an empty string: a language without a curated module falls back
    to a generic guide built from its registry entry, so an unsupported code still
    yields a report written in that language instead of one with no style rules.

    Args:
        language: Language code or tag (``ko``, ``ko-KR``, ``fr`` ...).

    Returns:
        Style guide text to append to the report-phase system prompt.
    """
    code = normalize_language(language)
    if code not in _GUIDE_CACHE:
        module = _load_module(code)
        guide = _read_attr(module, _GUIDE_ATTRS) if module else None
        _GUIDE_CACHE[code] = guide or _synthesize_style_guide(code)
    return _GUIDE_CACHE[code]


def get_translation_notes(language: str) -> str:
    """Return short per-language rules for the subscriber translation prompt.

    Empty when the language module defines no ``TRANSLATION_NOTES`` — the shared
    translation rules already stand on their own.
    """
    code = normalize_language(language)
    if code not in _NOTES_CACHE:
        module = _load_module(code)
        notes = _read_attr(module, ("TRANSLATION_NOTES",)) if module else None
        _NOTES_CACHE[code] = (notes or "").strip()
    return _NOTES_CACHE[code]


def has_curated_guide(language: str) -> bool:
    """True when the language ships its own style guide module."""
    module = _load_module(normalize_language(language))
    return bool(module and _read_attr(module, _GUIDE_ATTRS))


__all__ = ["get_style_guide", "get_translation_notes", "has_curated_guide"]
