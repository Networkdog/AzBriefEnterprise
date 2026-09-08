"""Admin console package for the AzBrief enterprise deployment profile."""

from __future__ import annotations

from typing import Any

__all__ = ["AdminPrincipal", "extract_principal", "require_admin", "router"]


def __getattr__(name: str) -> Any:
    """Keep public imports without eagerly loading the router and orchestrator."""
    if name in {"AdminPrincipal", "extract_principal", "require_admin"}:
        from src.admin import auth

        return getattr(auth, name)
    if name == "router":
        from src.admin.router import router

        return router
    raise AttributeError(name)
