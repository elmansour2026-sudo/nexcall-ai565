"""
NexCall AI v2 — Centre d'appels IA multi-agents
Demarrage : python -m uvicorn main:app --reload
Dashboard  : http://127.0.0.1:8000
"""
import sys
import logging
from pathlib import Path
from contextlib import asynccontextmanager

_ROOT = Path(__file__).parent.resolve()
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(dotenv_path=_ROOT / ".env", override=True)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db
from app.auth import AuthMiddleware, create_default_admin
from app.routers import (
    pages_router, calls_router, leads_router,
    campaigns_router, config_router, webhooks_router,
    agents_router, prospects_router,
    contacts_router, blacklist_router, scripts_router, analytics_router,
)
from app.routers.auth_router import router as auth_router, pages as auth_pages_router

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s - %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("nexcall")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 55)
    logger.info("  NexCall AI v2 - Demarrage")
    logger.info("=" * 55)
    await init_db()

    # Seeder les 6 agents par defaut si necessaire
    from app.database import AsyncSessionLocal
    from app.services.agent_service import agent_service
    from app.services.config_service import config_service
    async with AsyncSessionLocal() as db:
        created = await agent_service.seed_default_agents(db)
        await db.commit()
        if created > 0:
            logger.info(f"  Agents IA -> {created} agents par defaut crees")

        # Creer l'admin par defaut si la table users est vide (crucial sur Railway
        # ou la base SQLite se reinitialise a chaque redeploiement).
        try:
            admin_created = await create_default_admin(db)
            await db.commit()
            if admin_created:
                logger.info(f"  Admin      -> '{settings.ADMIN_USERNAME}' cree (changez le mot de passe !)")
        except Exception as e:
            logger.warning(f"  Admin par defaut non cree: {e}")

        # Charger la configuration sauvegardee en BDD dans les services
        # (cles saisies via l'interface), avec repli sur les variables d'env.
        try:
            await config_service.apply_to_services(db)
            ro = await config_service.is_vapi_configured(db)
            oa = await config_service.is_openai_configured(db)
        except Exception as e:
            logger.warning(f"  Config BDD non chargee: {e}")
            ro = settings.is_vapi_configured
            oa = settings.is_openai_configured

    logger.info(f"  Dashboard  -> http://127.0.0.1:8000")
    logger.info(f"  Vapi       -> {'OK' if ro else 'Non configure'}")
    logger.info(f"  OpenAI     -> {'OK' if oa else 'Non configure'}")
    logger.info("=" * 55)
    yield
    logger.info("NexCall AI v2 - Arret")


app = FastAPI(
    title="NexCall AI v2",
    description="Plateforme SaaS multi-agents IA pour centres d'appels",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(CORSMiddleware, allow_origins=["*"],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# Middleware d'authentification : protege toutes les pages et routes /api
# (sauf /login, /static, /health, /webhooks). Doit etre ajoute APRES CORS.
app.add_middleware(AuthMiddleware)

app.mount("/static", StaticFiles(directory=str(_ROOT / "static")), name="static")

# Authentification (page /login + endpoints /api/auth/*)
app.include_router(auth_pages_router)
app.include_router(auth_router)

app.include_router(pages_router)
app.include_router(calls_router)
app.include_router(leads_router)
app.include_router(campaigns_router)
app.include_router(config_router)
app.include_router(webhooks_router)
app.include_router(agents_router)
app.include_router(prospects_router)
app.include_router(contacts_router)
app.include_router(blacklist_router)
app.include_router(scripts_router)
app.include_router(analytics_router)


@app.get("/health", tags=["system"])
async def health():
    return {
        "status": "ok", "version": "2.0.0",
        "openai": settings.is_openai_configured,
        "vapi": settings.is_vapi_configured,
    }
