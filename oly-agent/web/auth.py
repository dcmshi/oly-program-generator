# web/auth.py
"""Password hashing and auth dependency for multi-athlete session auth."""

import bcrypt
from fastapi import HTTPException, Request


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def get_current_athlete_id(request: Request) -> int:
    """FastAPI dependency: returns athlete_id from session.

    AuthMiddleware already redirects unauthenticated browser requests to /login,
    so this is a safety net for any route that bypasses the middleware.
    """
    athlete_id = request.session.get("athlete_id")
    if not athlete_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return int(athlete_id)
