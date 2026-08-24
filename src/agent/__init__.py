"""AI Agent module for Azure Update analysis."""


def __getattr__(name: str):
    """Lazy import to avoid heavy langchain/openai imports at package load time."""
    if name in ("AzureUpdateAnalyzer", "UpdateAnalyzer", "AnalysisResult"):
        from src.agent.analyzer import (
            AnalysisResult,
            AzureUpdateAnalyzer,
            UpdateAnalyzer,
        )

        _exports = {
            "AzureUpdateAnalyzer": AzureUpdateAnalyzer,
            "UpdateAnalyzer": UpdateAnalyzer,
            "AnalysisResult": AnalysisResult,
        }
        # Cache in module namespace to avoid repeated lazy lookup
        globals().update(_exports)
        return _exports[name]

    if name in (
        "CircuitBreaker",
        "DiminishingReturnsTracker",
        "ModelFallbackError",
        "retry_with_backoff",
        "parse_json_resilient",
    ):
        from src.agent.resilience import (
            CircuitBreaker,
            DiminishingReturnsTracker,
            ModelFallbackError,
            parse_json_resilient,
            retry_with_backoff,
        )

        _exports = {
            "CircuitBreaker": CircuitBreaker,
            "DiminishingReturnsTracker": DiminishingReturnsTracker,
            "ModelFallbackError": ModelFallbackError,
            "retry_with_backoff": retry_with_backoff,
            "parse_json_resilient": parse_json_resilient,
        }
        globals().update(_exports)
        return _exports[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AzureUpdateAnalyzer",
    "AnalysisResult",
    "UpdateAnalyzer",
    "CircuitBreaker",
    "DiminishingReturnsTracker",
    "ModelFallbackError",
    "retry_with_backoff",
    "parse_json_resilient",
]
