"""Cliente da API oficial do WhatsApp Business (Meta Cloud API)."""
from __future__ import annotations

import logging
import time
from typing import Any

import requests

from config import Config

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class WhatsAppClient:
    def __init__(self, token: str | None = None, phone_number_id: str | None = None, dry_run: bool = False):
        self.token = token if token is not None else Config.WHATSAPP_TOKEN
        self.phone_number_id = phone_number_id if phone_number_id is not None else Config.WHATSAPP_PHONE_NUMBER_ID
        self.api_url = Config.WHATSAPP_API_URL
        self.dry_run = dry_run
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            }
        )

    @property
    def configured(self) -> bool:
        return bool(self.token and self.phone_number_id)

    # ------------------------------------------------------------------ interno
    def _post(self, payload: dict, retries: int = 3) -> dict[str, Any]:
        if self.dry_run:
            logger.info("[DRY-RUN] POST payload=%s", payload)
            return {"success": True, "dry_run": True, "data": {"messages": [{"id": "wamid.DRYRUN"}]}}

        if not self.configured:
            return {"success": False, "error": "whatsapp_not_configured"}

        url = f"{self.api_url}/{self.phone_number_id}/messages"
        last_error = None

        for attempt in range(1, retries + 1):
            try:
                response = self.session.post(url, json=payload, timeout=20)

                if response.status_code in RETRYABLE_STATUS and attempt < retries:
                    wait = 2 ** (attempt - 1)
                    logger.warning(
                        "WhatsApp API status %s — retentando em %ss (%s/%s)",
                        response.status_code, wait, attempt, retries,
                    )
                    time.sleep(wait)
                    continue

                if response.status_code >= 400:
                    try:
                        body = response.json()
                    except ValueError:
                        body = {"raw": response.text[:500]}
                    logger.error("WhatsApp API erro %s: %s", response.status_code, body)
                    return {
                        "success": False,
                        "error": f"http_{response.status_code}",
                        "status_code": response.status_code,
                        "details": body,
                    }

                return {"success": True, "data": response.json()}

            except requests.exceptions.Timeout:
                last_error = "timeout"
                logger.warning("Timeout na WhatsApp API (%s/%s)", attempt, retries)
            except requests.exceptions.RequestException as exc:
                last_error = str(exc)
                logger.warning("Erro de rede na WhatsApp API (%s/%s): %s", attempt, retries, exc)

            if attempt < retries:
                time.sleep(2 ** (attempt - 1))

        return {"success": False, "error": last_error or "unknown_error"}

    # ------------------------------------------------------------------ público
    def send_text(self, to: str, body: str, preview_url: bool = False) -> dict[str, Any]:
        """Envia mensagem de texto. Faz split automático em blocos de ~4000 caracteres."""
        chunks = self._split_message(body)
        results = []
        for chunk in chunks:
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"preview_url": preview_url, "body": chunk},
            }
            result = self._post(payload)
            results.append(result)
            if not result["success"]:
                return result
            if len(chunks) > 1:
                time.sleep(0.4)
        return results[-1] if results else {"success": False, "error": "empty_message"}

    @staticmethod
    def _split_message(body: str, limit: int = 4000) -> list[str]:
        if not body:
            return []
        if len(body) <= limit:
            return [body]

        chunks, current = [], ""
        for line in body.split("\n"):
            if len(current) + len(line) + 1 > limit:
                if current:
                    chunks.append(current.rstrip())
                while len(line) > limit:
                    chunks.append(line[:limit])
                    line = line[limit:]
                current = line + "\n"
            else:
                current += line + "\n"
        if current.strip():
            chunks.append(current.rstrip())
        return chunks

    def send_buttons(self, to: str, body: str, buttons: list[dict]) -> dict[str, Any]:
        """Envia mensagem interativa com até 3 botões de resposta rápida."""
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body[:1024]},
                "action": {
                    "buttons": [
                        {
                            "type": "reply",
                            "reply": {"id": btn["id"][:256], "title": btn["title"][:20]},
                        }
                        for btn in buttons[:3]
                    ]
                },
            },
        }
        return self._post(payload)

    def send_list(self, to: str, body: str, button_text: str, sections: list[dict]) -> dict[str, Any]:
        """Envia lista interativa (até 10 linhas por seção)."""
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "body": {"text": body[:1024]},
                "action": {
                    "button": button_text[:20],
                    "sections": [
                        {
                            "title": section.get("title", "")[:24],
                            "rows": [
                                {
                                    "id": row["id"][:200],
                                    "title": row["title"][:24],
                                    "description": row.get("description", "")[:72],
                                }
                                for row in section.get("rows", [])[:10]
                            ],
                        }
                        for section in sections[:10]
                    ],
                },
            },
        }
        return self._post(payload)

    def send_template(
        self,
        to: str,
        template_name: str,
        language_code: str = "pt_BR",
        components: list | None = None,
    ) -> dict[str, Any]:
        """Envia template aprovado (necessário para iniciar conversa fora da janela de 24h)."""
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "template",
            "template": {"name": template_name, "language": {"code": language_code}},
        }
        if components:
            payload["template"]["components"] = components
        return self._post(payload)

    def mark_as_read(self, message_id: str) -> dict[str, Any]:
        if self.dry_run:
            return {"success": True, "dry_run": True}
        if not self.configured:
            return {"success": False, "error": "whatsapp_not_configured"}
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }
        return self._post(payload, retries=1)

    def get_phone_number_info(self) -> dict[str, Any]:
        if not self.configured:
            return {"success": False, "error": "whatsapp_not_configured"}
        url = f"{self.api_url}/{self.phone_number_id}"
        try:
            response = self.session.get(url, timeout=20)
            response.raise_for_status()
            return {"success": True, "data": response.json()}
        except requests.exceptions.RequestException as exc:
            return {"success": False, "error": str(exc)}
