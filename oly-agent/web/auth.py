# web/auth.py
"""Password hashing and auth dependency for multi-athlete session auth."""

import bcrypt
from fastapi import HTTPException, Request

# bcrypt 5.x raises ValueError above 72 bytes instead of silently truncating,
# so unchecked long passwords 500 at login/setup/password-change (WEB-M7).
_BCRYPT_MAX_BYTES = 72


def password_too_long(password: str) -> bool:
    """True when the password exceeds bcrypt's 72-byte input limit.

    Byte length, not character count — multibyte characters (e.g. emoji,
    CJK) hit the limit well before 72 characters.
    """
    return len(password.encode("utf-8")) > _BCRYPT_MAX_BYTES


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    if password_too_long(plain):
        return False  # no stored hash can match — fail closed instead of raising
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
