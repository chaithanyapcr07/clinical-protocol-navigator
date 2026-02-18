from __future__ import annotations

from typing import Dict, Set

from fastapi import HTTPException

from app.config import Settings


ROLE_PERMISSIONS: Dict[str, Set[str]] = {
    "viewer": {"read"},
    "analyst": {"read", "query"},
    "ingestor": {"read", "ingest"},
    "admin": {"read", "query", "ingest", "admin"},
}


class RBACAuthorizer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def ensure(self, permission: str, role_value: str) -> str:
        if not self.settings.rbac_enabled:
            return role_value or "rbac_disabled"

        role = (role_value or self.settings.rbac_default_role).strip().lower()
        permissions = ROLE_PERMISSIONS.get(role)
        if not permissions:
            raise HTTPException(status_code=403, detail="Unknown role: %s" % role)

        if permission not in permissions and "admin" not in permissions:
            raise HTTPException(status_code=403, detail="Role '%s' lacks permission '%s'" % (role, permission))

        return role
