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
    "route:read": "View road routes (distance, drive time, vehicles a road takes)",
    "route:manage": "Manage road routes",
    "transport_tariff:read": "View transfer tariffs and line-haul fares",
    "transport_tariff:manage": "Manage transfer tariffs and line-haul fares",
    "supplier_doc:read": "View uploaded supplier rate sheets and their proposed rows",
    "supplier_doc:manage": "Upload supplier rate sheets and run extraction",
    "supplier_doc:confirm": "Confirm extracted rows into stored rates",
    "media:read": "View property and destination imagery",
    "media:manage": "Upload and remove property and destination imagery",
    "narrative:read": "View proposal copy and its review history",
    "narrative:manage": "Write or generate proposal copy (drafts only)",
    # Its own permission because it is the outward-facing act: approval is what
    # lets a sentence reach a client. Writing copy and publishing it are
    # different levels of trust, exactly as with issuing a quotation.
    "narrative:approve": "Approve proposal copy for use on client documents",
    "pricing:read": "View pricing configuration (markup/discount/tax defaults)",
    "pricing:manage": "Manage pricing configuration (markup/discount/tax defaults)",
    # --- Stage 2.7: clients + quote domain ---
    "client:read": "View clients",
    "client:manage": "Create and edit clients",
    "quote:create": "Create and assemble quotes",
    "quote:read": "View quotes (client-facing figures)",
    "quote:read_cost": "View internal cost and margin on quotes",
    "quote:price_override": "Override a quote's price/markup",
    # Its own permission because it is the outward-facing act: it freezes an
    # immutable version and puts a price in front of a client. Assembling a quote
    # and sending one are different levels of trust.
    "quote:issue": "Issue a quotation to a client (freezes an immutable version)",
    "quote:approve_discount": "Approve discounts beyond the standard threshold",
    # Its own permission because it is the act that decides what the business
    # believes about itself: the win rate, the pipeline value and every report
    # built on them come from these two endpoints, and a quote marked accepted
    # by mistake is a booking somebody expects to happen.
    "quote:record_outcome": "Record that a client accepted or declined a quote",
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
    "route:read",
    "transport_tariff:read",
    "pricing:read",
    "media:read",
    "narrative:read",
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
        "permissions": [
            "user:read",
            "role:read",
            *_REFERENCE_READ,
            "client:read",
            "client:manage",
            "media:manage",
            "quote:create",
            "quote:read",
            "quote:issue",
        ],
    },
    "operations": {
        "name": "Operations",
        "description": "Trip operations and fleet (extended in Stage 8).",
        "permissions": ["user:read"],
    },
    "finance": {
        "name": "Finance",
        "description": "Payments and financial reporting (extended in Stage 7).",
        "permissions": [
            "user:read",
            "audit:read",
            "pricing:read",
            "pricing:manage",
            "client:read",
            "quote:read",
            "quote:read_cost",
            "quote:price_override",
            "quote:approve_discount",
        ],
    },
    "viewer": {
        "name": "Viewer",
        "description": "Read-only access.",
        "permissions": [
            "user:read",
            "role:read",
            "audit:read",
            "settings:read",
            *_REFERENCE_READ,
            "client:read",
            "quote:read",
        ],
    },
}
