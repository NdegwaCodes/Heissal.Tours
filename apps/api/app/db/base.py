"""Import surface for Alembic autogenerate and mapper configuration.

Importing this module imports every ORM model, so `Base.metadata` is complete.
Alembic's env.py imports `Base` from here.
"""

from __future__ import annotations

from app.db.base_class import Base  # noqa: F401

# Import all models so they register on Base.metadata.
from app.modules.accommodations.models import (  # noqa: F401
    Accommodation,
    AccommodationRate,
    MealPlan,
    RoomType,
)
from app.modules.activities.models import Activity, ActivityRate  # noqa: F401
from app.modules.audit.models import AuditLog  # noqa: F401
from app.modules.auth.models import RefreshToken  # noqa: F401
from app.modules.clients.models import Client  # noqa: F401
from app.modules.currency.models import Currency, ExchangeRate  # noqa: F401
from app.modules.destinations.models import Destination  # noqa: F401
from app.modules.park_fees.models import ParkFee  # noqa: F401
from app.modules.quotes.models import (  # noqa: F401
    Quote,
    QuoteAccommodation,
    QuoteActivity,
    QuoteCounter,
    QuoteItem,
    QuoteLeg,
    QuoteTransport,
    QuoteTraveller,
    QuoteVersion,
)
from app.modules.rbac.models import (  # noqa: F401
    Permission,
    Role,
    role_permissions,
    user_roles,
)
from app.modules.residence.models import ResidenceCategory  # noqa: F401
from app.modules.settings.models import AppSetting  # noqa: F401
from app.modules.suppliers.models import Supplier  # noqa: F401
from app.modules.users.models import User  # noqa: F401
from app.modules.vehicles.models import FuelPrice, Vehicle  # noqa: F401

__all__ = [
    "Base",
    "Activity",
    "ActivityRate",
    "Accommodation",
    "AccommodationRate",
    "MealPlan",
    "RoomType",
    "ParkFee",
    "AuditLog",
    "RefreshToken",
    "Client",
    "Currency",
    "ExchangeRate",
    "Destination",
    "Quote",
    "QuoteAccommodation",
    "QuoteActivity",
    "QuoteCounter",
    "QuoteItem",
    "QuoteLeg",
    "QuoteTransport",
    "QuoteTraveller",
    "QuoteVersion",
    "Permission",
    "Role",
    "role_permissions",
    "user_roles",
    "ResidenceCategory",
    "AppSetting",
    "Supplier",
    "User",
    "Vehicle",
    "FuelPrice",
]
