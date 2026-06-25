"""
NexCall AI — Service Configuration dynamique.

Resout la valeur effective d'un parametre : la BDD (table configurations)
est prioritaire, avec repli sur les variables d'environnement (settings).

Permet aussi d'appliquer a chaud les cles sauvegardees aux services en cours
d'execution (RingoverService, AIAgentService) sans redemarrer le serveur.
"""
import logging
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.configuration import Configuration
from app.config import settings

logger = logging.getLogger(__name__)

# Correspondance cle BDD -> attribut settings (repli)
_SETTINGS_FALLBACK = {
    "vapi_api_key":          "VAPI_API_KEY",
    "vapi_phone_number_id":  "VAPI_PHONE_NUMBER_ID",
    "vapi_assistant_id":     "VAPI_ASSISTANT_ID",
    "vapi_webhook_secret":   "VAPI_WEBHOOK_SECRET",
    "transfer_number":       "TRANSFER_NUMBER",
    "openai_api_key":        "OPENAI_API_KEY",
    "openai_model":          "OPENAI_MODEL",
    "tts_voice":             "OPENAI_TTS_VOICE",
    "stt_model":             "OPENAI_STT_MODEL",
    "agent_name":            "AI_AGENT_NAME",
    "company_name":          "AI_COMPANY_NAME",
    "language":              "AI_LANGUAGE",
    "temperature":           "AI_TEMPERATURE",
    "lead_score_threshold":  "LEAD_SCORE_THRESHOLD",
}


class ConfigService:

    async def get_all(self, db: AsyncSession) -> dict[str, str]:
        """Renvoie toutes les valeurs en BDD sous forme {cle: valeur}."""
        result = await db.execute(select(Configuration))
        return {c.key: c.value for c in result.scalars().all() if c.value}

    async def get_value(self, db: AsyncSession, key: str) -> Optional[str]:
        """Valeur effective d'une cle : BDD prioritaire, sinon settings."""
        result = await db.execute(
            select(Configuration).where(Configuration.key == key)
        )
        row = result.scalar_one_or_none()
        if row and row.value not in (None, "", "***"):
            return row.value
        attr = _SETTINGS_FALLBACK.get(key)
        if attr:
            val = getattr(settings, attr, None)
            return str(val) if val not in (None, "") else None
        return None

    async def is_openai_configured(self, db: AsyncSession) -> bool:
        return bool(await self.get_value(db, "openai_api_key"))

    async def is_vapi_configured(self, db: AsyncSession) -> bool:
        return bool(
            await self.get_value(db, "vapi_api_key")
            and await self.get_value(db, "vapi_phone_number_id")
            and await self.get_value(db, "vapi_assistant_id")
        )

    async def apply_to_services(self, db: AsyncSession) -> None:
        """Pousse les cles actives (BDD > settings) dans les services en cours
        d'execution, pour qu'ils prennent effet immediatement sans redemarrage."""
        from app.services.vapi_service import vapi_service
        from app.services.ai_agent import ai_agent

        vapi_key       = await self.get_value(db, "vapi_api_key")
        vapi_phone_id  = await self.get_value(db, "vapi_phone_number_id")
        vapi_assistant = await self.get_value(db, "vapi_assistant_id")
        openai_key     = await self.get_value(db, "openai_api_key")

        try:
            vapi_service.set_api_key(vapi_key)
            vapi_service.set_phone_number_id(vapi_phone_id)
            vapi_service.set_assistant_id(vapi_assistant)
        except Exception as e:
            logger.warning(f"[config] maj Vapi: {e}")

        try:
            ai_agent.set_api_key(openai_key)
        except Exception as e:
            logger.warning(f"[config] maj IA: {e}")

        logger.info("[config] Cles appliquees (Vapi=%s, OpenAI=%s)",
                    bool(vapi_key), bool(openai_key))


config_service = ConfigService()
