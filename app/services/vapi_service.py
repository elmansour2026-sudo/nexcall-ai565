"""
NexCall AI — Service Vapi.ai (appels IA sortants)

Remplace l'ancien service Ringover. Vapi gere l'agent vocal IA de bout en bout :
on lui demande simplement de composer un numero avec un assistant donne, depuis
un numero (phoneNumberId) du compte Vapi.

Contrat API (verifie) :
  POST https://api.vapi.ai/call/phone
  Authorization: Bearer <VAPI_API_KEY>
  body = {
    "phoneNumberId": "<uuid>",
    "assistantId":   "<uuid>",
    "customer": { "number": "+E164", "name": "..." },
    "assistantOverrides": { "variableValues": {...} }   # optionnel
  }
  -> 201 Created avec l'objet call (id, status: "queued", ...)
"""
import logging
from typing import Any, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)
VAPI_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class VapiService:
    def __init__(self):
        self._api_key         = self._clean(settings.VAPI_API_KEY)
        self._phone_number_id = self._clean(settings.VAPI_PHONE_NUMBER_ID)
        self._assistant_id    = self._clean(settings.VAPI_ASSISTANT_ID)
        self._base_url        = (settings.VAPI_API_URL or "https://api.vapi.ai").rstrip("/")

    # ── Helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def _clean(v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        s = str(v).strip().strip('"').strip("'").strip()
        return s or None

    @staticmethod
    def _to_e164(num: Any) -> str:
        s = str(num or "").strip()
        digits = "".join(c for c in s if c.isdigit())
        if not digits:
            return ""
        if s.startswith("+"):
            return "+" + digits
        if digits.startswith("00"):
            return "+" + digits[2:]
        if digits.startswith("0") and len(digits) == 10:   # numero FR national
            return "+33" + digits[1:]
        return "+" + digits

    def set_api_key(self, api_key: Optional[str]) -> None:
        self._api_key = self._clean(api_key)

    def set_phone_number_id(self, value: Optional[str]) -> None:
        self._phone_number_id = self._clean(value)

    def set_assistant_id(self, value: Optional[str]) -> None:
        self._assistant_id = self._clean(value)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key or ''}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        }

    def _is_ready(self) -> bool:
        """Pret a appeler : il faut au minimum la cle API, un phoneNumberId et un assistantId."""
        return bool(self._api_key and self._phone_number_id and self._assistant_id)

    # ── Diagnostic ───────────────────────────────────────────────────────────
    async def test_connection(self) -> dict:
        if not self._api_key:
            return {"success": False, "connected": False, "error": "Cle API Vapi manquante"}
        try:
            async with httpx.AsyncClient(timeout=VAPI_TIMEOUT) as client:
                r = await client.get(f"{self._base_url}/assistant", headers=self._headers())
                if 200 <= r.status_code < 300:
                    return {"success": True, "connected": True}
                return {"success": False, "connected": False, "error": f"HTTP {r.status_code}"}
        except Exception as e:
            return {"success": False, "connected": False, "error": str(e)}

    # ── Appel sortant ────────────────────────────────────────────────────────
    async def make_outbound_call(
        self,
        to_number: str,
        assistant_id: Optional[str] = None,
        customer_name: Optional[str] = None,
        variable_values: Optional[dict] = None,
        **_ignored,
    ) -> dict:
        """Lance un appel IA sortant via Vapi. Renvoie TOUJOURS un dict
        (aucune exception ne remonte, le worker ne peut pas crasher).

        Succes uniquement si Vapi repond 2xx (201 attendu)."""
        try:
            if not self._api_key:
                logger.error("[CALL ERROR] Cle API Vapi manquante")
                return {"success": False, "error": "Cle API Vapi manquante"}

            phone_number_id = self._phone_number_id
            assistant = self._clean(assistant_id) or self._assistant_id

            if not phone_number_id:
                logger.error("[CALL ERROR] VAPI_PHONE_NUMBER_ID manquant")
                return {"success": False, "error": "VAPI_PHONE_NUMBER_ID manquant"}
            if not assistant:
                logger.error("[CALL ERROR] VAPI_ASSISTANT_ID manquant")
                return {"success": False, "error": "VAPI_ASSISTANT_ID manquant"}

            target = self._to_e164(to_number)
            if not target:
                logger.error("[CALL ERROR] Numero cible invalide")
                return {"success": False, "error": "Numero cible invalide"}

            customer: dict[str, Any] = {"number": target}
            if customer_name:
                customer["name"] = customer_name

            payload: dict[str, Any] = {
                "phoneNumberId": phone_number_id,
                "assistantId":   assistant,
                "customer":      customer,
            }
            if variable_values:
                payload["assistantOverrides"] = {"variableValues": variable_values}

            url = f"{self._base_url}/call/phone"
            logger.info(f"[CALL START] Vapi -> {target} | phoneNumberId={phone_number_id} assistantId={assistant}")

            async with httpx.AsyncClient(timeout=VAPI_TIMEOUT) as client:
                try:
                    r = await client.post(url, headers=self._headers(), json=payload)
                except Exception as e:
                    logger.error(f"[CALL ERROR] Exception reseau Vapi: {e}")
                    return {"success": False, "error": f"Erreur reseau Vapi: {e}"}

                body_text = ""
                try:
                    body_text = (r.text or "")[:1000]
                except Exception:
                    pass
                logger.info(f"[CALL RESPONSE] status={r.status_code} body={body_text or '(vide)'}")

                if 200 <= r.status_code < 300:
                    data = {}
                    try:
                        data = r.json()
                    except Exception:
                        data = {}
                    call_id = data.get("id") if isinstance(data, dict) else None
                    status  = data.get("status") if isinstance(data, dict) else None
                    logger.info(f"[CALL OK] Vapi a accepte l'appel vers {target} call_id={call_id} status={status}")
                    return {"success": True, "status_code": r.status_code,
                            "call_id": str(call_id) if call_id else None, "data": data}

                hint = ""
                if r.status_code == 400:
                    hint = (" — Verifiez que VAPI_PHONE_NUMBER_ID et VAPI_ASSISTANT_ID sont des UUID "
                            "valides existant dans votre compte Vapi, et le numero en E.164.")
                elif r.status_code in (401, 403):
                    hint = " — Cle API Vapi invalide ou expiree."
                logger.error(f"[CALL FAIL] Vapi refuse: HTTP {r.status_code} {body_text or '(vide)'}")
                return {"success": False, "status_code": r.status_code,
                        "error": f"HTTP {r.status_code}: {body_text or 'reponse vide'}{hint}"}

        except Exception as e:
            logger.exception(f"[CALL ERROR] Exception inattendue make_outbound_call: {e}")
            return {"success": False, "error": f"Exception interne: {e}"}

    # ── Transfert (Vapi gere le transfert cote assistant) ────────────────────
    async def transfer_call(self, call_id: str, to_number: str) -> dict:
        """Avec Vapi, le transfert vers un humain se configure dans l'assistant
        (forwardingPhoneNumber / tool transferCall). Cote serveur on ne force
        rien ici : on journalise simplement la demande pour ne pas casser le flux."""
        logger.info(f"[TRANSFER] Demande de transfert call={call_id} vers {to_number} "
                    f"(gere par l'assistant Vapi)")
        return {"success": True, "note": "Transfert gere par l'assistant Vapi"}

    # ── Webhook Vapi (optionnel) ─────────────────────────────────────────────
    def validate_webhook_signature(self, payload: bytes, signature: str) -> bool:
        secret = getattr(settings, "VAPI_WEBHOOK_SECRET", None)
        if not secret:
            return True
        import hmac, hashlib
        try:
            expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
            return hmac.compare_digest(expected, signature or "")
        except Exception:
            return False


vapi_service = VapiService()
