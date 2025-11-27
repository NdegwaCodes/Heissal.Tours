import uuid
import enum
from datetime import datetime, date

from sqlalchemy import (
    String,
    Integer,
    Numeric,
    DateTime,
    Date,
    Enum as SAEnum,
    ForeignKey,
    Boolean,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base
from ..users.models import User
