# migrations/env.py
"""Alembic migration environment.

Reads DATABASE_URL from shared.config.Settings (which reads from environment
variables / .env file). No URL is hardcoded in alembic.ini.

For PgBouncer compatibility the URL is rewritten to use the direct Postgres
port (5433) when the default 5432 is in use, because Alembic DDL statements
require a real connection (not a pooled transaction-mode connection).
Override with ALEMBIC_DATABASE_URL to use a specific URL for migrations.
"""

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

# Make shared/ importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _get_db_url() -> str:
    """Return the database URL for migrations (see migrations/db_url.py)."""
    sys.path.insert(0, str(Path(__file__).parent))
    from db_url import resolve_migration_url

    return resolve_migration_url()


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live DB connection (for review / CI)."""
    url = _get_db_url()
    context.configure(
        url=url,
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against a live database connection."""
    url = _get_db_url()
    connectable = create_engine(url, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
