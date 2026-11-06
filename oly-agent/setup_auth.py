#!/usr/bin/env python
"""Set or update login credentials for an athlete.

Usage:
    cd oly-agent
    PYTHONUTF8=1 uv run python setup_auth.py --athlete-id 1 --username david --password secret

Run once per athlete after `make migrate` (the auth columns are in the Alembic chain).
"""

import argparse
import sys

from web.auth import hash_password

from shared.config import Settings
from shared.db import execute, fetch_one, get_connection


def main():
    parser = argparse.ArgumentParser(description="Set athlete login credentials")
    parser.add_argument("--athlete-id", type=int, required=True, help="Athlete row ID")
    parser.add_argument("--username", required=True, help="Login username")
    parser.add_argument("--password", required=True, help="Plain-text password (will be hashed)")
    args = parser.parse_args()

    settings = Settings()
    conn = get_connection(settings.database_url)
    try:
        row = fetch_one(conn, "SELECT id, name FROM athletes WHERE id = %s", (args.athlete_id,))
        if not row:
            print(f"Error: no athlete with id={args.athlete_id}")
            sys.exit(1)

        execute(
            conn,
            "UPDATE athletes SET username = %s, password_hash = %s WHERE id = %s",
            (args.username, hash_password(args.password), args.athlete_id),
        )
        conn.commit()
        print(f"Credentials set: athlete_id={args.athlete_id} ({row['name']}), username={args.username}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
