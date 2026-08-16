"""Permission catalogue and system-role definitions (seed source of truth).

Permissions are `resource:action` strings. New ones are added here and applied
by the seed/migration. Role→permission mappings below are the *initial* system
defaults; they are editable data in the admin UI afterwards.
"""

from __future__ import annotations

from typing import TypedDict


class RoleDefinition(TypedDict):
    name: str
    description: str
    permissions: list[str]


# --- Permission catalogue (Stage 1) ---
PERMISSIONS: dict[str, str] = {
    "user:read": "View users",
    "user:create": "Create users",
    "user:update": "Update users",
    "user:manage_roles": "Assign roles to users",
    "role:read": "View roles",
    "role:create": "Create roles",
    "role:update": "Edit role permissions",
    "audit:read": "View the audit log",
    "settings:read": "View application settings",
    "settings:update": "Change application settings",
    # --- Stage 2: reference / catalogue ---
    "residence:read": "View residence categories",
    "residence:manage": "Manage residence categories",
    "currency:read": "View currencies",
    "currency:manage": "Manage currencies",
    "fx:read": "View exchange rates",
    "fx:manage": "Manage exchange rates",
    "supplier:read": "View suppliers",
    "supplier:manage": "Manage suppliers",
    "destination:read": "View destinations",
    "destination:manage": "Manage destinations",
    "accommodation:read": "View accommodations, room types, meal plans and rates",
    "accommodation:manage": "Manage accommodations, room types, meal plans and rates",
    "park_fee:read": "View park and conservation fees",
    "park_fee:manage": "Manage park and conservation fees",
    "activity:read": "View activities and activity rates",
    "activity:manage": "Manage activities and activity rates",
    "vehicle:read": "View vehicles and fuel prices",
    "vehicle:manage": "Manage vehicles and fuel prices",
}

# Reference read/manage permission keys, grouped for role assignment.
_REFERENCE_READ = [
    "residence:read",
    "currency:read",
    "fx:read",
    "supplier:read",
    "destination:read",
    "accommodation:read",
    "park_fee:read",
    "activity:read",
    "vehicle:read",
]

# --- System roles and their initial permissions ---
# `admin` gets every Stage-1 permission. Others are least-privilege starting
# points that later stages extend (quote:*, lead:*, booking:* …).
ROLE_DEFINITIONS: dict[str, RoleDefinition] = {
    "admin": {
        "name": "Administrator",
        "description": "Full administrative access.",
        "permissions": list(PERMISSIONS.keys()),
    },
    "sales_agent": {
        "name": "Sales Agent",
        "description": "Creates quotes and manages clients; reads the catalogue.",
        "permissions": ["user:read", "role:read", *_REFERENCE_READ],
    },
    "operations": {
        "name": "Operations",
        "description": "Trip operations and fleet (extended in Stage 8).",
        "permissions": ["user:read"],
    },
    "finance": {
        "name": "Finance",
        "description": "Payments and financial reporting (extended in Stage 7).",
        "permissions": ["user:read", "audit:read"],
    },
    "viewer": {
        "name": "Viewer",
        "description": "Read-only access.",
        "permissions": ["user:read", "role:read", "audit:read", "settings:read", *_REFERENCE_READ],
    },
}
