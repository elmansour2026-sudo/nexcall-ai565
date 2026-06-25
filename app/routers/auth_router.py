"""
NexCall AI — Router Authentification
"""
import logging
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.auth import authenticate, create_token, COOKIE_NAME
from app.config import settings

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="app/templates")

# Pages (HTML)
pages = APIRouter(tags=["auth-pages"])
# API
router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str
    password: str


@pages.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@router.post("/login")
async def login(body: LoginBody, response: Response, db: AsyncSession = Depends(get_db)):
    user = await authenticate(db, body.username.strip(), body.password)
    if not user:
        return Response(content='{"detail":"Identifiants invalides"}',
                        media_type="application/json", status_code=401)
    token = create_token(user.username)
    # secure=True en production (HTTPS Railway), desactive en debug local (HTTP)
    response.set_cookie(
        key=COOKIE_NAME, value=token, httponly=True, samesite="lax",
        secure=not settings.DEBUG, max_age=settings.JWT_EXPIRE_HOURS * 3600, path="/",
    )
    return {"success": True, "user": user.to_dict()}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(key=COOKIE_NAME, path="/")
    return {"success": True}


@router.get("/me")
async def me(request: Request, db: AsyncSession = Depends(get_db)):
    from app.models.user import User
    from sqlalchemy import select
    username = getattr(request.state, "username", None)
    if not username:
        return {"authenticated": False}
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    return {"authenticated": bool(user), "user": user.to_dict() if user else None}
