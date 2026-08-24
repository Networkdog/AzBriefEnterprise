"""Language registry — the single source of truth for supported report languages.

Adding a language
-----------------
1. Add one :func:`register_language` call below.
2. Add ``src/i18n/labels/<code>.py`` with a ``LABELS`` dict (optional — missing
   keys fall back through the chain, so a partial translation is safe).
3. Add ``src/agent/prompts/languages/<code>.py`` with a ``STYLE_GUIDE`` string
   (optional — a generic guide is generated from the registry entry otherwise).

Nothing else in the codebase enumerates language codes: labels, prompt style
guides, action-item findings and the config validator all read this registry.
"""

from dataclasses import dataclass
from typing import Callable, Mapping, Optional, TypeVar

DEFAULT_LANGUAGE = "ko"

T = TypeVar("T")


@dataclass(frozen=True)
class LanguageSpec:
    """Metadata for one report language.

    Attributes:
        code: BCP 47 / ISO 639-1 code used everywhere in the codebase (``ko``).
        english_name: Name shown to the LLM in prompts (``Korean``).
        native_name: Name shown to readers (``한국어``).
        fallback: Code to borrow missing UI labels from. ``None`` for the root.
        html_lang: Value for the ``<html lang>`` attribute. Defaults to ``code``.
        registered: False for a code synthesized on demand (unknown language).
    """

    code: str
    english_name: str
    native_name: str
    fallback: Optional[str] = DEFAULT_LANGUAGE
    html_lang: str = ""
    registered: bool = True

    @property
    def lang_attr(self) -> str:
        """Value for the HTML ``lang`` attribute."""
        return self.html_lang or self.code

    @property
    def display_name(self) -> str:
        """Prompt-facing name, e.g. ``Korean (한국어)``."""
        if self.native_name and self.native_name != self.english_name:
            return f"{self.english_name} ({self.native_name})"
        return self.english_name


# ── Registry ───────────────────────────────────────────────────

_REGISTRY: dict[str, LanguageSpec] = {}
_CACHE_CLEARERS: list[Callable[[], None]] = []


def register_language(spec: LanguageSpec) -> None:
    """Register (or replace) a language and invalidate dependent caches."""
    _REGISTRY[spec.code] = spec
    for clear in _CACHE_CLEARERS:
        clear()


def register_cache_clearer(clear: Callable[[], None]) -> None:
    """Register a cache reset hook, called whenever the registry changes.

    Used by the label and style-guide loaders so a language registered at
    runtime (tests, plugins) is picked up immediately.
    """
    _CACHE_CLEARERS.append(clear)


register_language(
    LanguageSpec(code="ko", english_name="Korean", native_name="한국어", fallback=None)
)
register_language(LanguageSpec(code="en", english_name="English", native_name="English"))
register_language(LanguageSpec(code="ja", english_name="Japanese", native_name="日本語"))


# English names for codes that are not registered yet. Lets an unregistered
# code still produce a meaningful prompt instruction ("Write in French")
# instead of a bare code the model has to guess at.
_ISO_NAMES: dict[str, str] = {
    "de": "German",
    "es": "Spanish",
    "fr": "French",
    "hi": "Hindi",
    "id": "Indonesian",
    "it": "Italian",
    "ms": "Malay",
    "pt": "Portuguese",
    "ru": "Russian",
    "th": "Thai",
    "vi": "Vietnamese",
    "zh": "Chinese",
}


# ── Lookup ─────────────────────────────────────────────────────


def normalize_language(language: str) -> str:
    """Normalize a language tag to a registry key.

    ``ko-KR`` / ``ko_KR`` / ``KO`` all resolve to ``ko``. A regional tag is kept
    only when it is registered in its own right (e.g. a future ``zh-hant``).

    Args:
        language: Raw language tag from settings, a subscriber profile or a CLI.

    Returns:
        Normalized code. Empty input yields :data:`DEFAULT_LANGUAGE`.
    """
    tag = (language or "").strip().lower().replace("_", "-")
    if not tag:
        return DEFAULT_LANGUAGE
    if tag in _REGISTRY:
        return tag
    return tag.split("-", 1)[0]


def get_language(language: str) -> LanguageSpec:
    """Return the spec for a language, synthesizing one for unknown codes.

    Never returns ``None`` — an unknown code degrades to a spec that falls back
    to :data:`DEFAULT_LANGUAGE` for UI labels while keeping its own code, so an
    unregistered language still produces a coherent report instead of crashing.
    """
    code = normalize_language(language)
    spec = _REGISTRY.get(code)
    if spec is not None:
        return spec
    return LanguageSpec(
        code=code,
        english_name=_ISO_NAMES.get(code, code),
        native_name=_ISO_NAMES.get(code, code),
        fallback=DEFAULT_LANGUAGE,
        registered=False,
    )


def is_supported(language: str) -> bool:
    """True when the language is registered (has a curated style guide/labels)."""
    return normalize_language(language) in _REGISTRY


def supported_languages() -> tuple[LanguageSpec, ...]:
    """All registered language specs, in registration order."""
    return tuple(_REGISTRY.values())


def supported_language_codes() -> tuple[str, ...]:
    """All registered language codes, in registration order."""
    return tuple(_REGISTRY.keys())


def language_name(language: str) -> str:
    """English name of a language, for LLM prompts."""
    return get_language(language).english_name


def language_display(language: str) -> str:
    """Prompt-facing name including the native form, e.g. ``Korean (한국어)``."""
    return get_language(language).display_name


def fallback_chain(language: str) -> tuple[str, ...]:
    """Codes to try in order when resolving a per-language value.

    The chain always ends at :data:`DEFAULT_LANGUAGE` so a lookup can never
    come back empty because of a partial translation.
    """
    chain: list[str] = []
    code: Optional[str] = normalize_language(language)
    while code and code not in chain:
        chain.append(code)
        spec = _REGISTRY.get(code)
        code = spec.fallback if spec else DEFAULT_LANGUAGE
    if DEFAULT_LANGUAGE not in chain:
        chain.append(DEFAULT_LANGUAGE)
    return tuple(chain)


def resolve(bundle: Mapping[str, T], language: str, default: Optional[T] = None) -> Optional[T]:
    """Pick a language's entry from a ``{code: value}`` bundle via the fallback chain.

    Args:
        bundle: Per-language values, e.g. finding message templates.
        language: Requested language code.
        default: Returned when no code in the chain is present.

    Returns:
        The first matching entry, or ``default``.
    """
    for code in fallback_chain(language):
        if code in bundle:
            return bundle[code]
    return default


__all__ = [
    "DEFAULT_LANGUAGE",
    "LanguageSpec",
    "fallback_chain",
    "get_language",
    "is_supported",
    "language_display",
    "language_name",
    "normalize_language",
    "register_cache_clearer",
    "register_language",
    "resolve",
    "supported_language_codes",
    "supported_languages",
]
