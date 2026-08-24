"""UI label bundles, one module per language.

A language module exposes a ``LABELS: dict[str, str]``. Missing keys are
backfilled from the language's fallback chain (see :mod:`src.i18n`), so a new
language can ship a partial translation without ever raising ``KeyError`` at
email-render time.
"""

import importlib

from src.i18n import DEFAULT_LANGUAGE, fallback_chain, normalize_language, register_cache_clearer

_BUNDLE_CACHE: dict[str, dict[str, str]] = {}
_MERGED_CACHE: dict[str, dict[str, str]] = {}


def _clear_caches() -> None:
    _BUNDLE_CACHE.clear()
    _MERGED_CACHE.clear()


register_cache_clearer(_clear_caches)


def _load_bundle(code: str) -> dict[str, str]:
    """Load one language module's raw LABELS dict (empty when absent)."""
    if code in _BUNDLE_CACHE:
        return _BUNDLE_CACHE[code]
    try:
        module = importlib.import_module(f"{__name__}.{code.replace('-', '_')}")
        labels = dict(getattr(module, "LABELS", {}))
    except ImportError:
        labels = {}
    _BUNDLE_CACHE[code] = labels
    return labels


def get_labels(language: str = DEFAULT_LANGUAGE) -> dict[str, str]:
    """Return UI labels for a language, backfilled from its fallback chain.

    Args:
        language: Language code. Unknown codes fall back to the default language.

    Returns:
        A complete label dict — every key of the default language is present.
    """
    code = normalize_language(language)
    if code in _MERGED_CACHE:
        return _MERGED_CACHE[code]

    merged: dict[str, str] = {}
    for fallback_code in reversed(fallback_chain(code)):
        merged.update(_load_bundle(fallback_code))
    _MERGED_CACHE[code] = merged
    return merged


def label_keys() -> frozenset[str]:
    """The canonical label key set (the default language's keys)."""
    return frozenset(_load_bundle(DEFAULT_LANGUAGE))


def missing_label_keys(language: str) -> frozenset[str]:
    """Keys a language does not translate itself (they render in the fallback).

    Diagnostic helper for tests and for reviewing a newly added language.
    """
    return label_keys() - frozenset(_load_bundle(normalize_language(language)))


__all__ = ["get_labels", "label_keys", "missing_label_keys"]
