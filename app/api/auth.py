"""Shared lightweight authentication dependency for HTTP routers."""

from typing import Annotated
import secrets

from fastapi import Header, HTTPException

from app.config import settings


def identity(authorization: Annotated[str | None, Header()] = None):
    if not authorization or not secrets.compare_digest(authorization, f"Bearer {settings.demo_token}"):
        raise HTTPException(401, "unauthorized")
    return "demo-user"
