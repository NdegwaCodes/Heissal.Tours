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
    AccommodationSupplement,
    MealPlan,
    RoomType,
)
from app.modules.activities.models import (  # noqa: F401
    Activity,
    ActivityPriceTier,
    ActivityRate,
)
from app.modules.audit.models import AuditLog  # noqa: F401
from app.modules.auth.models import RefreshToken  # noqa: F401
from app.modules.bookings.models import (  # noqa: F401
    Booking,
    BookingCounter,
    BookingInstalment,
    Payment,
)
from app.modules.clients.models import Client  # noqa: F401
from app.modules.comms.models import Communication  # noqa: F401
from app.modules.currency.models import Currency, ExchangeRate  # noqa: F401
from app.modules.destinations.models import Destination  # noqa: F401
from app.modules.leads.models import (  # noqa: F401
    Lead,
    LeadStage,
    LeadStageEvent,
)
from app.modules.media.models import DestinationImage, PropertyImage  # noqa: F401
from app.modules.narratives.models import Narrative  # noqa: F401
from app.modules.operations.models import (  # noqa: F401
    CrewMember,
    FuelFill,
    SupplierBooking,
    TripAssignment,
    TripLog,
)
from app.modules.park_fees.models import ParkFee  # noqa: F401
from app.modules.portal.models import BookingAccessGrant  # noqa: F401
from app.modules.quotes.models import (  # noqa: F401
    Quote,
    QuoteAccommodation,
    QuoteActivity,
    QuoteCounter,
    QuoteItem,
    QuoteLeg,
    QuoteOption,
    QuoteRejectedCandidate,
    QuoteTransport,
    QuoteTransportSegment,
    QuoteTraveller,
    QuoteVersion,
    QuoteVersionOption,
)
from app.modules.rbac.models import (  # noqa: F401
    Permission,
    Role,
    role_permissions,
    user_roles,
)
from app.modules.residence.models import ResidenceCategory  # noqa: F401
from app.modules.settings.models import AppSetting  # noqa: F401
from app.modules.supplier_docs.models import (  # noqa: F401
    SupplierDocument,
    SupplierDocumentExtraction,
)
from app.modules.suppliers.models import Supplier  # noqa: F401
from app.modules.transport.models import (  # noqa: F401
    DestinationTransportMode,
    Route,
    TransferRate,
)
from app.modules.users.models import User  # noqa: F401
from app.modules.vehicles.models import FuelPrice, Vehicle  # noqa: F401

__all__ = [
    "Base",
    "Activity",
    "ActivityPriceTier",
    "ActivityRate",
    "Accommodation",
    "AccommodationRate",
    "AccommodationSupplement",
    "MealPlan",
    "RoomType",
    "Booking",
    "BookingCounter",
    "BookingInstalment",
    "Payment",
    "Lead",
    "LeadStage",
    "LeadStageEvent",
    "Narrative",
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
    "QuoteOption",
    "QuoteRejectedCandidate",
    "QuoteTransport",
    "QuoteTransportSegment",
    "QuoteTraveller",
    "QuoteVersion",
    "QuoteVersionOption",
    "Permission",
    "Role",
    "role_permissions",
    "user_roles",
    "ResidenceCategory",
    "AppSetting",
    "Supplier",
    "SupplierDocument",
    "SupplierDocumentExtraction",
    "DestinationTransportMode",
    "Route",
    "TransferRate",
    "PropertyImage",
    "DestinationImage",
    "User",
    "Vehicle",
    "FuelPrice",
]
