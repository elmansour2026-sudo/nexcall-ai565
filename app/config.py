"""
NexCall AI — Configuration centralisée
Toutes les variables sont chargées depuis .env via pydantic-settings
"""
from functools import lru_cache
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    APP_NAME: str = "NexCall AI"
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8000
    DEBUG: bool = True
    SECRET_KEY: str = "nexcall-dev-secret-key-change-in-production"

    # Base de données
    DATABASE_URL: str = "sqlite+aiosqlite:///./nexcall.db"

    # ── Vapi.ai (appels IA sortants) ─────────────────────────────────────────
    VAPI_API_KEY: Optional[str] = None
    VAPI_API_URL: str = "https://api.vapi.ai"
    VAPI_PHONE_NUMBER_ID: Optional[str] = None   # UUID du numero Vapi (from)
    VAPI_ASSISTANT_ID: Optional[str] = None      # UUID de l'assistant IA
    VAPI_WEBHOOK_SECRET: Optional[str] = None

    # Numero de transfert humain (generique, hors Vapi)
    TRANSFER_NUMBER: Optional[str] = None

    # ── Authentification (JWT en cookie HTTPOnly) ────────────────────────────
    JWT_SECRET: str = "change-this-jwt-secret-in-production-please-32chars"
    JWT_EXPIRE_HOURS: int = 168          # 7 jours
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "admin123"     # cree au demarrage si aucun utilisateur

    # OpenAI
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODEL: str = "gpt-4o"
    OPENAI_TTS_MODEL: str = "tts-1"
    OPENAI_TTS_VOICE: str = "nova"
    OPENAI_STT_MODEL: str = "whisper-1"
    OPENAI_MAX_TOKENS: int = 600

    # Autres fournisseurs IA (optionnels — la plateforme n'en force aucun)
    ANTHROPIC_API_KEY: Optional[str] = None
    MISTRAL_API_KEY: Optional[str] = None
    CUSTOM_AI_BASE_URL: Optional[str] = None
    CUSTOM_AI_API_KEY: Optional[str] = None

    # Agent IA
    AI_AGENT_NAME: str = "Sophie"
    AI_COMPANY_NAME: str = "AssurancePro"
    AI_LANGUAGE: str = "fr"
    AI_TEMPERATURE: float = 0.7

    # IVR
    IVR_GREETING: str = (
        "Bonjour et bienvenue. "
        "Pour l'assurance auto, tapez 1. "
        "Pour l'assurance santé, tapez 2. "
        "Pour parler à un conseiller, tapez 3."
    )

    # Leads
    LEAD_SCORE_THRESHOLD: int = 70

    @property
    def is_vapi_configured(self) -> bool:
        return bool(self.VAPI_API_KEY and self.VAPI_PHONE_NUMBER_ID and self.VAPI_ASSISTANT_ID)

    @property
    def is_openai_configured(self) -> bool:
        return bool(self.OPENAI_API_KEY)


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
