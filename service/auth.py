from __future__ import annotations
import secrets
from typing import Optional
from fastapi import Cookie, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_db
from .models import User
from . import crud


def generate_token(length: int = 32) -> str:
    return secrets.token_hex(length)


async def get_current_user(
    session_token: Optional[str] = Cookie(default=None, alias="adagent_session"),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    if not session_token:
        return None
    s = await crud.get_session(db, session_token)
    if not s:
        return None
    return await crud.get_user_by_id(db, s.user_id)


async def get_current_user_subscribed(
    user: Optional[User] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> bool:
    if not user:
        return False
    return await crud.is_subscribed(db, user.id)
