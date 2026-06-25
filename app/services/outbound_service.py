"""
NexCall AI v2 — Service d'appels sortants
Gere le lancement des campagnes outbound via Ringover API.
"""
import csv
import io
import json
import logging
from datetime import datetime
from typing import Any, Optional
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.prospect import Prospect, ProspectStatus
from app.models.campaign import Campaign, CampaignStatus
from app.models.call import Call, CallStatus, CallDirection
from app.models.qualification import Qualification, QualificationIntent
from app.models.lead import Lead

logger = logging.getLogger(__name__)


class OutboundService:

    async def import_csv(
        self,
        db: AsyncSession,
        campaign_id: int,
        csv_content: str,
        delimiter: str = ",",
    ) -> dict[str, Any]:
        """
        Importe une liste de prospects depuis un CSV.
        Colonnes attendues (insensibles a la casse) :
          nom | prenom | telephone | date_naissance | ville | info
        """
        reader = csv.DictReader(io.StringIO(csv_content), delimiter=delimiter)
        # Normaliser les en-tetes
        def norm(k: str) -> str:
            return k.strip().lower().replace(" ", "_").replace("é","e").replace("è","e")

        imported = 0
        errors = []
        for i, row in enumerate(reader, start=2):  # ligne 2 = premiere ligne de donnees
            row_norm = {norm(k): v.strip() for k, v in row.items() if k}

            phone = (
                row_norm.get("telephone")
                or row_norm.get("tel")
                or row_norm.get("phone")
                or ""
            ).strip()

            if not phone:
                errors.append(f"Ligne {i}: numero de telephone manquant")
                continue

            # Normaliser le numero (ajouter +33 si necessaire)
            phone = self._normalize_phone(phone)

            prospect = Prospect(
                campaign_id  = campaign_id,
                phone        = phone,
                first_name   = row_norm.get("prenom") or row_norm.get("firstname") or None,
                last_name    = row_norm.get("nom") or row_norm.get("lastname") or None,
                email        = row_norm.get("email") or None,
                birth_date   = row_norm.get("date_naissance") or row_norm.get("naissance") or None,
                city         = row_norm.get("ville") or row_norm.get("city") or None,
                extra_info   = row_norm.get("informations") or row_norm.get("info") or row_norm.get("notes") or None,
                status       = ProspectStatus.PENDING.value,
            )
            db.add(prospect)
            imported += 1

        # Mettre a jour le compteur de prospects dans la campagne
        if imported > 0:
            result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
            campaign = result.scalar_one_or_none()
            if campaign:
                campaign.total_prospects = campaign.total_prospects + imported
                campaign.updated_at = datetime.utcnow()

        await db.flush()
        return {"imported": imported, "errors": errors}

    def _normalize_phone(self, phone: str) -> str:
        """Normalise un numero francais en E.164."""
        p = "".join(c for c in phone if c.isdigit() or c == "+")
        if p.startswith("0") and len(p) == 10:
            p = "+33" + p[1:]
        elif p.startswith("33") and not p.startswith("+"):
            p = "+" + p
        return p

    async def get_next_prospects(
        self,
        db: AsyncSession,
        campaign_id: int,
        limit: int = 3,
    ) -> list[Prospect]:
        """Retourne les prochains prospects a appeler (statuts appelables)."""
        callable_statuses = [
            ProspectStatus.NOUVEAU.value,
            ProspectStatus.EN_ATTENTE.value,
            ProspectStatus.A_RAPPELER.value,
            ProspectStatus.NE_REPOND_PAS.value,  # retry
            ProspectStatus.PENDING.value,         # compat v2
        ]
        result = await db.execute(
            select(Prospect)
            .where(
                Prospect.campaign_id == campaign_id,
                Prospect.status.in_(callable_statuses),
                Prospect.is_archived == False,
            )
            .order_by(Prospect.id)
            .limit(limit)
        )
        return result.scalars().all()

    async def launch_campaign(
        self,
        db: AsyncSession,
        campaign_id: int,
    ) -> dict[str, Any]:
        """Lance une campagne outbound (change le statut, appelle Vapi pour les N premiers)."""
        from app.services.vapi_service import vapi_service
        from app.models.agent import Agent

        result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
        campaign = result.scalar_one_or_none()
        if not campaign:
            return {"success": False, "error": "Campagne non trouvee"}

        # Charger explicitement l'agent (evite le lazy-load relationnel async).
        agent = None
        if campaign.agent_id:
            agent_result = await db.execute(select(Agent).where(Agent.id == campaign.agent_id))
            agent = agent_result.scalar_one_or_none()

        campaign.status = CampaignStatus.ACTIVE.value
        campaign.is_active = True
        campaign.started_at = campaign.started_at or datetime.utcnow()
        campaign.updated_at = datetime.utcnow()

        # Lancer les N premiers appels
        prospects = await self.get_next_prospects(db, campaign_id, limit=campaign.max_concurrent)
        launched = 0
        errors = []
        blocked = 0

        from app.services.blacklist_service import blacklist_service
        blacklisted = await blacklist_service.get_all_phones(db)
        max_attempts = campaign.max_attempts or 3

        if not vapi_service._is_ready():
            errors.append("Vapi non configure (VAPI_API_KEY / VAPI_PHONE_NUMBER_ID / VAPI_ASSISTANT_ID)")
            prospects = []

        for prospect in prospects:
            if prospect.phone in blacklisted:
                prospect.status = ProspectStatus.DO_NOT_CALL.value
                blocked += 1
                continue
            if prospect.attempt_count >= max_attempts:
                prospect.status = ProspectStatus.NE_REPOND_PAS.value
                continue

            # 1-2. VAPI D'ABORD, on attend la reponse
            res = await vapi_service.make_outbound_call(
                to_number=prospect.phone,
                customer_name=prospect.full_name,
            )

            # 3. Succes confirme -> seulement maintenant on cree le Call,
            #    on change le statut et on incremente le compteur.
            if res.get("success"):
                call = Call(
                    ringover_call_id = res.get("call_id"),   # = id de l'appel Vapi
                    caller_number    = "vapi",
                    called_number    = prospect.phone,
                    status           = CallStatus.RINGING.value,
                    direction        = CallDirection.OUTBOUND.value,
                    campaign_id      = campaign.id,
                    agent_id         = agent.id if agent else None,
                    prospect_id      = prospect.id,
                )
                db.add(call)
                prospect.status = ProspectStatus.CALLING.value
                prospect.last_attempt_at = datetime.utcnow()
                prospect.attempt_count += 1
                campaign.total_calls = (campaign.total_calls or 0) + 1
                launched += 1
            else:
                # 4. Echec Vapi -> on ne compte pas, on ne change pas le statut.
                errors.append(f"Prospect {prospect.phone}: {res.get('error', 'erreur inconnue')}")

        await db.flush()
        return {"success": True, "launched": launched, "blocked": blocked, "errors": errors}

    async def launch_test_call(
        self,
        db: AsyncSession,
        campaign_id: int,
        test_phone: Optional[str] = None,
    ) -> dict[str, Any]:
        """Lance un VRAI appel de test via Vapi (aucune simulation).

        Ordre impose :
          1. Appeler Vapi (make_outbound_call)
          2. Attendre la reponse
          3. Si succes -> creer la ligne Call avec l'id d'appel Vapi
          4. Si echec  -> ne rien creer, renvoyer l'erreur exacte
        """
        from app.services.vapi_service import vapi_service
        from app.models.agent import Agent

        result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
        campaign = result.scalar_one_or_none()
        if not campaign:
            return {"success": False, "error": "Campagne non trouvee"}

        phone = (test_phone or campaign.test_phone_number or "").strip()
        if not phone:
            return {"success": False, "error": "Aucun numero de test fourni. Renseignez le numero de telephone de test."}
        phone = self._normalize_phone(phone)

        agent = None
        if campaign.agent_id:
            ar = await db.execute(select(Agent).where(Agent.id == campaign.agent_id))
            agent = ar.scalar_one_or_none()
        agent_name = agent.name if agent else "Agent IA"

        # Vapi doit etre pret (cle + phoneNumberId + assistantId)
        if not vapi_service._is_ready():
            return {"success": False, "error": ("Vapi non configure. Renseignez VAPI_API_KEY, "
                                                 "VAPI_PHONE_NUMBER_ID et VAPI_ASSISTANT_ID.")}

        # 1-2. APPEL VAPI D'ABORD, on attend la reponse
        res = await vapi_service.make_outbound_call(to_number=phone, customer_name=agent_name)

        # 4. Echec -> aucune ligne Call, aucun changement
        if not res.get("success"):
            return {
                "success": False,
                "phone": phone,
                "error": res.get("error", "Echec de l'appel Vapi"),
            }

        # 3. Succes confirme par Vapi -> on cree la ligne Call SEULEMENT maintenant
        rid = res.get("call_id")
        call = Call(
            ringover_call_id = rid,           # = id de l'appel Vapi
            caller_number    = "vapi",
            called_number    = phone,
            status           = CallStatus.RINGING.value,
            direction        = CallDirection.OUTBOUND.value,
            campaign_id      = campaign.id,
            agent_id         = agent.id if agent else None,
        )
        db.add(call)
        await db.flush()
        logger.info(f"[CALL OK] Appel de test Vapi lance vers {phone} (call_id={call.id}, vapi_id={rid})")
        return {
            "success": True,
            "call_id": call.id,
            "vapi_call_id": rid,
            "phone": phone,
            "agent": agent_name,
            "message": f"Appel IA lance via Vapi vers {phone}. Votre telephone va sonner.",
        }

    async def launch_next(self, db: AsyncSession, campaign_id: int) -> dict[str, Any]:
        """Lance l'appel du prochain prospect appelable (enchainement automatique).
        Respecte la liste rouge et le nombre maximum de tentatives."""
        from app.services.vapi_service import vapi_service
        from app.services.blacklist_service import blacklist_service
        from app.models.agent import Agent

        result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
        campaign = result.scalar_one_or_none()
        if not campaign or campaign.status != "active":
            return {"success": False, "launched": 0}

        agent = None
        if campaign.agent_id:
            ar = await db.execute(select(Agent).where(Agent.id == campaign.agent_id))
            agent = ar.scalar_one_or_none()
        if not vapi_service._is_ready():
            return {"success": False, "launched": 0, "error": "Vapi non configure"}

        blacklisted = await blacklist_service.get_all_phones(db)
        max_attempts = campaign.max_attempts or 3

        prospects = await self.get_next_prospects(db, campaign_id, limit=10)
        for prospect in prospects:
            if prospect.phone in blacklisted:
                prospect.status = ProspectStatus.DO_NOT_CALL.value
                continue
            if prospect.attempt_count >= max_attempts:
                prospect.status = ProspectStatus.NE_REPOND_PAS.value
                continue
            res = await vapi_service.make_outbound_call(
                to_number=prospect.phone, customer_name=prospect.full_name,
            )
            if res.get("success"):
                call = Call(
                    ringover_call_id = res.get("call_id"),
                    caller_number    = "vapi",
                    called_number    = prospect.phone,
                    status           = CallStatus.RINGING.value,
                    direction        = CallDirection.OUTBOUND.value,
                    campaign_id      = campaign.id,
                    agent_id         = agent.id if agent else None,
                    prospect_id      = prospect.id,
                )
                db.add(call)
                prospect.status = ProspectStatus.CALLING.value
                prospect.last_attempt_at = datetime.utcnow()
                prospect.attempt_count += 1
                campaign.total_calls = (campaign.total_calls or 0) + 1
                await db.flush()
                return {"success": True, "launched": 1, "phone": prospect.phone}

        # Plus aucun prospect a appeler -> la campagne est terminee
        campaign.status = "completed"
        campaign.ended_at = datetime.utcnow()
        await db.flush()
        return {"success": True, "launched": 0, "completed": True}

    async def create_qualification(
        self,
        db: AsyncSession,
        call_id: int,
        agent_id: Optional[int],
        prospect_id: Optional[int],
        lead_id: Optional[int],
        qual_data: dict[str, Any],
    ) -> Qualification:
        """Cree ou met a jour la qualification d'un appel."""
        # Verifier si une qualification existe deja
        result = await db.execute(
            select(Qualification).where(Qualification.call_id == call_id)
        )
        qual = result.scalar_one_or_none()

        if qual:
            for k, v in qual_data.items():
                if hasattr(qual, k) and v is not None:
                    setattr(qual, k, v)
        else:
            qual = Qualification(
                call_id     = call_id,
                agent_id    = agent_id,
                prospect_id = prospect_id,
                lead_id     = lead_id,
                **{k: v for k, v in qual_data.items() if hasattr(Qualification, k)},
            )
            db.add(qual)

        await db.flush()
        return qual

    async def get_campaign_stats(self, db: AsyncSession, campaign_id: int) -> dict:
        total     = await db.scalar(select(func.count(Prospect.id)).where(Prospect.campaign_id == campaign_id)) or 0
        pending   = await db.scalar(select(func.count(Prospect.id)).where(Prospect.campaign_id == campaign_id, Prospect.status == "pending")) or 0
        reached   = await db.scalar(select(func.count(Prospect.id)).where(Prospect.campaign_id == campaign_id, Prospect.status == "reached")) or 0
        converted = await db.scalar(select(func.count(Prospect.id)).where(Prospect.campaign_id == campaign_id, Prospect.status == "converted")) or 0
        return {
            "total": total, "pending": pending,
            "reached": reached, "converted": converted,
            "progress_pct": round((total - pending) / total * 100, 1) if total > 0 else 0,
        }


outbound_service = OutboundService()
