# web/auth.py
"""Password hashing and auth dependency for multi-athlete session auth."""

import bcrypt
from fastapi import Depends, HTTPException, Request
from web.deps import get_db

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


# COACH-2: a coach works on one linked athlete at a time. The session keeps the
# logged-in account in "athlete_id" and the athlete being coached here.
ACTING_ID_KEY = "acting_athlete_id"
ACTING_NAME_KEY = "acting_athlete_name"


def get_account_id(request: Request) -> int:
    """FastAPI dependency: the logged-in account — for what belongs to the
    login itself (password, username, coach links), never the coached athlete.

    AuthMiddleware already redirects unauthenticated browser requests to /login,
    so this is a safety net for any route that bypasses the middleware.
    """
    athlete_id = request.session.get("athlete_id")
    if not athlete_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return int(athlete_id)


def stop_acting(session) -> None:
    session.pop(ACTING_ID_KEY, None)
    session.pop(ACTING_NAME_KEY, None)


async def get_current_athlete_id(request: Request, conn=Depends(get_db)) -> int:
    """FastAPI dependency: the athlete whose data the request reads and writes —
    the coached athlete while a coach is acting as one (COACH-2), else the
    logged-in account. The link is re-checked on every request, so a revoked
    link (or a deleted athlete) drops the coach back to their own account at
    once rather than at the next login."""
    account = get_account_id(request)
    acting = request.session.get(ACTING_ID_KEY)
    if not acting or int(acting) == account:
        return account
    from web.queries.coach import is_linked
    if await is_linked(conn, account, int(acting)):
        return int(acting)
    stop_acting(request.session)
    return account
