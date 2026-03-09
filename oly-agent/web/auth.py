# web/auth.py
"""Password hashing and auth dependency for multi-athlete session auth."""

from fastapi import HTTPException, Request
from passlib.context import CryptContext

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


def get_current_athlete_id(request: Request) -> int:
    """FastAPI dependency: returns athlete_id from session.

    AuthMiddleware already redirects unauthenticated browser requests to /login,
    so this is a safety net for any route that bypasses the middleware.
    """
    athlete_id = request.session.get("athlete_id")
    if not athlete_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return int(athlete_id)
