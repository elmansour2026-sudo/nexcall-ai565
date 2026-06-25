"""
NexCall AI — Router Configuration
"""
import logging
from typing import Dict, Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime

from app.database import get_db
from app.models.configuration import Configuration
from app.services.vapi_service import vapi_service
from app.services.config_service import config_service
from app.config import settings

router = APIRouter(prefix="/api/config", tags=["configuration"])
logger = logging.getLogger(__name__)

SECRET_KEYS = {"vapi_api_key", "openai_api_key", "vapi_webhook_secret"}

CATEGORY_MAP = {
    "vapi":         {"vapi_api_key", "vapi_phone_number_id", "vapi_assistant_id", "vapi_webhook_secret", "transfer_number"},
    "openai":       {"openai_api_key", "openai_model", "tts_voice", "stt_model"},
    "agent":        {"agent_name", "company_name", "language", "temperature"},
    "ivr":          {"ivr_greeting"},
    "leads":        {"lead_score_threshold"},
}


def _get_category(key: str) -> str:
    for category, keys in CATEGORY_MAP.items():
        if key in keys:
            return category
    return "general"


class ConfigSaveRequest(BaseModel):
    configs: Dict[str, str]


@router.get("")
async def get_configuration(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Configuration))
    configs = result.scalars().all()
    return {c.key: c.to_dict() for c in configs}


@router.post("")
async def save_configuration(body: ConfigSaveRequest, db: AsyncSession = Depends(get_db)):
    saved = []
    for key, value in body.configs.items():
        if not key or not isinstance(value, str):
            continue

        result = await db.execute(select(Configuration).where(Configuration.key == key))
        existing = result.scalar_one_or_none()

        if existing:
            # Ne pas écraser un secret avec "***" (masque envoyé par l'UI)
            if existing.is_secret and value == "***":
                continue
            existing.value = value
            existing.updated_at = datetime.utcnow()
        else:
            cfg = Configuration(
                key       = key,
                value     = value,
                category  = _get_category(key),
                is_secret = key in SECRET_KEYS,
            )
            db.add(cfg)
        saved.append(key)

    await db.flush()

    # Appliquer immédiatement les nouvelles clés aux services en cours d'exécution,
    # afin que le statut passe à "Configuré" sans redémarrage du serveur.
    try:
        await config_service.apply_to_services(db)
    except Exception as e:
        logger.warning(f"[config] application aux services: {e}")

    # Recalculer le statut tout de suite pour le renvoyer à l'UI
    status = await _build_status(db)
    return {
        "success": True,
        "saved": saved,
        "message": "Configuration sauvegardée avec succès",
        "status": status,
    }


async def _build_status(db: AsyncSession) -> dict:
    """Construit le statut en consultant la BDD en priorité, puis settings."""
    openai_key       = await config_service.get_value(db, "openai_api_key")
    vapi_key         = await config_service.get_value(db, "vapi_api_key")
    vapi_phone_id    = await config_service.get_value(db, "vapi_phone_number_id")
    vapi_assistant   = await config_service.get_value(db, "vapi_assistant_id")

    openai_model = await config_service.get_value(db, "openai_model") or settings.OPENAI_MODEL
    tts_voice    = await config_service.get_value(db, "tts_voice") or settings.OPENAI_TTS_VOICE
    transfer     = await config_service.get_value(db, "transfer_number") or settings.TRANSFER_NUMBER
    agent_name   = await config_service.get_value(db, "agent_name") or settings.AI_AGENT_NAME
    company      = await config_service.get_value(db, "company_name") or settings.AI_COMPANY_NAME
    language     = await config_service.get_value(db, "language") or settings.AI_LANGUAGE
    temperature  = await config_service.get_value(db, "temperature") or settings.AI_TEMPERATURE

    vapi_configured   = bool(vapi_key and vapi_phone_id and vapi_assistant)
    openai_configured = bool(openai_key)

    # Test de connexion seulement si la cle est presente
    vapi_connected = False
    if vapi_key:
        vapi_service.set_api_key(vapi_key)
        vapi_service.set_phone_number_id(vapi_phone_id)
        vapi_service.set_assistant_id(vapi_assistant)
        test = await vapi_service.test_connection()
        vapi_connected = test.get("connected", False)

    vapi_block = {
        "configured":      vapi_configured,
        "connected":       vapi_connected,
        "phone_number_id": vapi_phone_id,
        "assistant_id":    vapi_assistant,
        "transfer_number": transfer,
        "api_url":         settings.VAPI_API_URL,
    }

    return {
        # Bloc Vapi (nouveau)
        "vapi": vapi_block,
        # Alias de compatibilite pour les templates existants (sidebar, dashboard)
        "ringover": {
            "configured":   vapi_configured,
            "connected":    vapi_connected,
            "phone_number": vapi_phone_id,
            "transfer_number": transfer,
            "api_url":      settings.VAPI_API_URL,
        },
        "openai": {
            "configured": openai_configured,
            "model":      openai_model,
            "tts_model":  settings.OPENAI_TTS_MODEL,
            "tts_voice":  tts_voice,
            "stt_model":  settings.OPENAI_STT_MODEL,
        },
        "agent": {
            "name":        agent_name,
            "company":     company,
            "language":    language,
            "temperature": temperature,
        },
        "app": {
            "name":    settings.APP_NAME,
            "debug":   settings.DEBUG,
            "version": "2.0.0",
        },
    }


@router.get("/status")
async def get_status(db: AsyncSession = Depends(get_db)):
    """Statut des intégrations. La BDD (config sauvegardée via l'UI) est
    prioritaire ; repli sur les variables d'environnement (.env)."""
    return await _build_status(db)


@router.post("/test-vapi")
@router.post("/test-ringover")  # alias retro-compatible
async def test_vapi(db: AsyncSession = Depends(get_db)):
    key       = await config_service.get_value(db, "vapi_api_key")
    phone_id  = await config_service.get_value(db, "vapi_phone_number_id")
    assistant = await config_service.get_value(db, "vapi_assistant_id")
    if key:
        vapi_service.set_api_key(key)
        vapi_service.set_phone_number_id(phone_id)
        vapi_service.set_assistant_id(assistant)
    return await vapi_service.test_connection()


@router.get("/webhook-urls")
async def get_webhook_urls():
    """URL de webhook serveur a renseigner cote Vapi (Server URL de l'assistant)."""
    base = f"http://{settings.APP_HOST}:{settings.APP_PORT}"
    return {
        "server_url": f"{base}/webhooks/vapi",
        "note":       "Renseignez cette URL comme 'Server URL' de votre assistant Vapi (en HTTPS public en prod).",
    }
