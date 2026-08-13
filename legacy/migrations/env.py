# pyright: reportMissingImports=false

from logging.config import fileConfig
from typing import Any, Dict, cast
import os
import sys

from sqlalchemy import engine_from_config, pool
from alembic import context

# --- make sure we can import the FastAPI app code -------------------------

# This file is in <project_root>/migrations/env.py
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Your FastAPI backend lives in <project_root>/backend/api/app
PROJECT_ROOT = os.path.join(BASE_DIR, "backend", "api")
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from app.core.db import Base  # noqa: E402

# Import model modules for side effects (register tables on Base.metadata)
import app.users.models  # noqa: F401, E402
import app.locations.models  # noqa: F401, E402
import app.providers.models  # noqa: F401, E402
import app.activities.models  # noqa: F401, E402
import app.tours.models  # noqa: F401, E402
import app.bookings.models  # noqa: F401, E402
import app.reviews.models  # noqa: F401, E402
import app.wishlists.models  # noqa: F401, E402

# -------------------------------------------------------------------------

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section)
    if section is None:
        section = {}

    options: Dict[str, Any] = cast(Dict[str, Any], section)

    connectable = engine_from_config(
        options,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
