"""
NexCall AI — Authentification

- Hachage de mot de passe : PBKDF2-HMAC-SHA256 (stdlib, aucune dependance).
- Token : JWT HS256 signe maison (stdlib hmac/base64/json) -> aucune lib externe,
  donc zero risque d'installation manquante sur Railway.
- Le token est stocke dans un cookie HTTPOnly (`access_token`).
- Middleware : protege toutes les pages et toutes les routes /api, sauf la
  liste blanche (login, statics, health). Pages -> redirection /login,
  API -> 401 JSON.
- Au demarrage : creation d'un admin par defaut si la table users est vide.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select, func
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings

logger = logging.getLogger(__name__)

COOKIE_NAME = "access_token"

# Routes accessibles SANS authentification
_PUBLIC_PREFIXES = ("/static", "/login", "/api/auth/login", "/health", "/favicon", "/webhooks")


# ── Mot de passe : PBKDF2 ────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return f"pbkdf2${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt_hex, hash_hex = stored.split("$", 2)
        if algo != "pbkdf2":
            return False
        salt = bytes.fromhex(salt_hex)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


# ── JWT HS256 (stdlib) ───────────────────────────────────────────────────────
def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def create_token(username: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = {"sub": username, "iat": now, "exp": now + settings.JWT_EXPIRE_HOURS * 3600}
    h = _b64url(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{h}.{p}".encode()
    sig = hmac.new(settings.JWT_SECRET.encode(), signing_input, hashlib.sha256).digest()
    return f"{h}.{p}.{_b64url(sig)}"


def decode_token(token: str) -> Optional[dict]:
    try:
        h, p, s = token.split(".")
        signing_input = f"{h}.{p}".encode()
        expected = hmac.new(settings.JWT_SECRET.encode(), signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(_b64url(expected), s):
            return None
        payload = json.loads(_b64url_decode(p))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


# ── Authentification ─────────────────────────────────────────────────────────
async def authenticate(db, username: str, password: str):
    from app.models.user import User
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    user.last_login_at = datetime.utcnow()
    return user


async def create_default_admin(db) -> bool:
    """Cree l'admin par defaut si aucun utilisateur n'existe.
    Indispensable sur Railway ou la base SQLite se reinitialise au redeploiement."""
    from app.models.user import User
    count = await db.scalar(select(func.count(User.id))) or 0
    if count > 0:
        return False
    admin = User(
        username      = settings.ADMIN_USERNAME,
        password_hash = hash_password(settings.ADMIN_PASSWORD),
        is_active     = True,
        is_admin      = True,
    )
    db.add(admin)
    await db.flush()
    logger.info(f"[auth] Admin par defaut cree : '{settings.ADMIN_USERNAME}' "
                f"(changez ADMIN_PASSWORD en production !)")
    return True


# ── Middleware de protection ─────────────────────────────────────────────────
def _is_public(path: str) -> bool:
    if path == "/login":
        return True
    return any(path == p or path.startswith(p) for p in _PUBLIC_PREFIXES)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if _is_public(path):
            return await call_next(request)

        token = request.cookies.get(COOKIE_NAME)
        payload = decode_token(token) if token else None

        if not payload:
            # API -> 401 JSON ; Page -> redirection vers /login
            if path.startswith("/api/"):
                return JSONResponse({"detail": "Non authentifie"}, status_code=401)
            return RedirectResponse(url="/login", status_code=302)

        request.state.username = payload.get("sub")
        return await call_next(request)
