"""User identity model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base, TimestampMixin, UUIDPKMixin
from app.modules.rbac.models import Role, user_roles


class User(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Read-only view of assigned roles; assignment is done explicitly in the
    # service layer (so it can be audited), not by mutating this collection.
    roles: Mapped[list[Role]] = relationship(
        "Role",
        secondary=user_roles,
        primaryjoin="User.id == user_roles.c.user_id",
        secondaryjoin="Role.id == user_roles.c.role_id",
        lazy="selectin",
        viewonly=True,
    )

    @property
    def permission_keys(self) -> set[str]:
        """Effective permission keys from all assigned roles."""
        if self.is_superuser:
            return {"*"}
        keys: set[str] = set()
        for role in self.roles:
            for perm in role.permissions:
                keys.add(perm.key)
        return keys
