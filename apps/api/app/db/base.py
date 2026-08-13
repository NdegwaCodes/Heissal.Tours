"""Import surface for Alembic autogenerate and mapper configuration.

Importing this module imports every ORM model, so `Base.metadata` is complete.
Alembic's env.py imports `Base` from here.
"""

from __future__ import annotations

from app.db.base_class import Base  # noqa: F401

# Import all models so they register on Base.metadata.
from app.modules.audit.models import AuditLog  # noqa: F401
from app.modules.auth.models import RefreshToken  # noqa: F401
from app.modules.rbac.models import (  # noqa: F401
    Permission,
    Role,
    role_permissions,
    user_roles,
)
from app.modules.settings.models import AppSetting  # noqa: F401
from app.modules.users.models import User  # noqa: F401

__all__ = [
    "Base",
    "AuditLog",
    "RefreshToken",
    "Permission",
    "Role",
    "role_permissions",
    "user_roles",
    "AppSetting",
    "User",
]
