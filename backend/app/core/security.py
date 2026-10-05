from __future__ import annotations

import hashlib
import secrets
import time
from collections import defaultdict, deque
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.base import get_db, utcnow
from app.db.models import Session, User

COOKIE = "orkestra_session"
_hasher = PasswordHasher()


def hash_password(pw: str) -> str:
    return _hasher.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, pw)
    except (VerifyMismatchError, InvalidHashError):
        return False


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create_session(db: AsyncSession, user_id: str) -> str:
    """Returns the raw token (goes in the httpOnly cookie); only its hash is stored."""
    token = secrets.token_urlsafe(32)
    ttl = timedelta(hours=get_settings().session_ttl_hours)
    db.add(Session(token_hash=_digest(token), user_id=user_id, expires_at=utcnow() + ttl))
    await db.commit()
    return token


async def delete_session(db: AsyncSession, token: str) -> None:
    sess = await db.get(Session, _digest(token))
    if sess:
        await db.delete(sess)
        await db.commit()


async def current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    token = request.cookies.get(COOKIE)
    if token:
        sess = await db.get(Session, _digest(token))
        if sess and sess.expires_at.replace(tzinfo=utcnow().tzinfo) > utcnow():
            user = await db.get(User, sess.user_id)
            if user:
                return user
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")


class RateLimiter:
    """Sliding-window limiter keyed by client IP (single-process POC, so in-memory is enough)."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now, window = time.monotonic(), self.hits[key]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= self.per_minute:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts — wait a minute and try again")
        window.append(now)


login_limiter = RateLimiter(get_settings().login_attempts_per_minute)
