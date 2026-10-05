from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import (COOKIE, create_session, current_user, delete_session, hash_password, login_limiter,
                               verify_password)
from app.db.base import get_db
from app.db.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])

THEMES = {"aurora", "midnight", "daylight", "sunset", "ocean", "forest", "cyberpunk", "mono"}


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=200)


class Register(Credentials):
    display_name: str = Field(min_length=1, max_length=120)


PREF_DEFAULTS = {"motion": "full", "celebrate": True, "default_budget_usd": 20.0}


class UserOut(BaseModel):
    id: str
    username: str
    display_name: str
    theme: str
    prefs: dict = {}
    created_at: str | None = None


def prefs_of(u: User) -> dict:
    return {**PREF_DEFAULTS, **(u.prefs or {})}


def _out(u: User) -> UserOut:
    return UserOut(id=u.id, username=u.username, display_name=u.display_name, theme=u.theme, prefs=prefs_of(u),
                   created_at=u.created_at.isoformat() if u.created_at else None)


def _set_cookie(resp: Response, token: str) -> None:
    s = get_settings()
    resp.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=s.cookie_secure,
                    max_age=s.session_ttl_hours * 3600, path="/")


@router.post("/register", response_model=UserOut, status_code=201)
async def register(body: Register, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    login_limiter.check(request.client.host if request.client else "?")
    username = body.username.strip().lower()
    if not re.fullmatch(r"[a-z0-9._-]+", username):
        raise HTTPException(422, "Username may contain letters, digits, dot, dash and underscore")
    if (await db.execute(select(User).where(User.username == username))).scalar_one_or_none():
        raise HTTPException(409, "That username is taken")
    user = User(username=username, display_name=body.display_name.strip(), password_hash=hash_password(body.password))
    db.add(user)
    await db.commit()
    _set_cookie(response, await create_session(db, user.id))
    return _out(user)


@router.post("/login", response_model=UserOut)
async def login(body: Credentials, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    login_limiter.check(request.client.host if request.client else "?")
    user = (await db.execute(select(User).where(User.username == body.username.strip().lower()))).scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong username or password")
    _set_cookie(response, await create_session(db, user.id))
    return _out(user)


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    if token := request.cookies.get(COOKIE):
        await delete_session(db, token)
    response.delete_cookie(COOKIE, path="/")


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(current_user)):
    return _out(user)


class PrefsIn(BaseModel):
    motion: Literal["full", "calm"] | None = None
    celebrate: bool | None = None
    default_budget_usd: float | None = Field(default=None, ge=1, le=1000)


class Prefs(BaseModel):
    theme: str | None = None
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    prefs: PrefsIn | None = None


@router.patch("/me", response_model=UserOut)
async def update_prefs(body: Prefs, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if body.theme is not None and body.theme not in THEMES:
        raise HTTPException(422, "Unknown theme")
    user = await db.merge(user)
    if body.theme is not None:
        user.theme = body.theme
    if body.display_name is not None:
        user.display_name = body.display_name.strip()
    if body.prefs is not None:
        user.prefs = {**(user.prefs or {}), **body.prefs.model_dump(exclude_none=True)}
    await db.commit()
    return _out(user)


class PasswordIn(BaseModel):
    current: str = Field(min_length=1, max_length=200)
    new: str = Field(min_length=8, max_length=200)


@router.post("/password", status_code=204)
async def change_password(body: PasswordIn, request: Request, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    login_limiter.check(request.client.host if request.client else "?")
    if not verify_password(body.current, user.password_hash):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your current password isn't right")
    user = await db.merge(user)
    user.password_hash = hash_password(body.new)
    await db.commit()
