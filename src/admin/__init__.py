"""Admin console package for the AzBrief enterprise deployment profile."""

from src.admin.auth import AdminPrincipal, extract_principal, require_admin
from src.admin.router import router

__all__ = ["AdminPrincipal", "extract_principal", "require_admin", "router"]
