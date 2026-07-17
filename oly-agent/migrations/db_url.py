# migrations/db_url.py
"""Migration DB-URL resolution.

Lives outside env.py so it is importable (and tested) without an Alembic
runtime — env.py touches `context.config` at module scope and can only be
imported mid-migration.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# The compose stack's PgBouncer listens on local 5432; Postgres direct is 5433.
_LOCAL_PGBOUNCER_HOSTS = ("@localhost:5432/", "@127.0.0.1:5432/")


def resolve_migration_url() -> str:
    """Return the database URL for migrations.

    Precedence:
      1. ALEMBIC_DATABASE_URL env var (explicit override — passes through untouched)
      2. DATABASE_URL / shared config, rewritten to the direct Postgres port
         ONLY for the local compose stack: PgBouncer's transaction mode is
         incompatible with DDL, but a non-local DB on the standard port must
         not be silently redirected to port 5433 on that host (INF-M3).
         Production deployments set ALEMBIC_DATABASE_URL to a direct URL.
    """
    override = os.getenv("ALEMBIC_DATABASE_URL", "")
    if override:
        return override

    from shared.config import Settings
    url = Settings().database_url

    if any(host in url for host in _LOCAL_PGBOUNCER_HOSTS):
        url = url.replace(":5432/", ":5433/")

    # asyncpg URLs need the psycopg2 dialect for Alembic
    return url.replace("postgresql+asyncpg://", "postgresql://")
