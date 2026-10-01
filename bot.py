"""Orquestrador do bot: decide a resposta e envia (FAQ → IA → fallback)."""
from __future__ import annotations

import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from ai_client import AIClient
from config import Config
from db import Database
from faq_engine import FAQEngine
from security import RateLimiter
from whatsapp_client import WhatsAppClient

logger = logging.getLogger(__name__)

GREETING_FOR_HANDOFF = (
    "👤 Pronto! Registrei seu pedido de atendimento humano.\n\n"
    "Um de nossos atendentes vai continuar a conversa por aqui. "
    "Enquanto isso, pode deixar mais detalhes sobre sua dúvida."
)

HANDOFF_ALREADY = "👤 Você já está na fila de atendimento humano. Um atendente responderá em breve!"

MENU_BODY = "Como posso ajudar? Escolha uma opção abaixo 👇"

MENU_BUTTONS = [
    {"id": "faq_horario", "title": "Horário"},
    {"id": "faq_entrega", "title": "Entrega"},
    {"id": "handoff", "title": "Falar c/ atendente"},
]

RATE_LIMIT_MESSAGE = "⚠️ Você enviou muitas mensagens em pouco tempo. Aguarde um instante e tente novamente."

OUT_OF_HOURS_SUFFIX = "\n\n_Estamos fora do horário de atendimento — responderei assim que possível._"


class BotHandler:
    def __init__(
        self,
        db: Database | None = None,
        faq: FAQEngine | None = None,
        ai: AIClient | None = None,
        wa: WhatsAppClient | None = None,
        dry_run: bool = False,
    ):
        self.db = db or Database()
        self.faq = faq or FAQEngine(self.db)
        self.ai = ai or AIClient()
        self.wa = wa or WhatsAppClient(dry_run=dry_run)
        self.limiter = RateLimiter(Config.RATE_LIMIT_PER_MINUTE)
        self.dry_run = dry_run

    # -------------------------------------------------------------- utilitários
    def is_out_of_hours(self) -> bool:
        if not Config.BUSINESS_HOURS_ENABLED:
            return False
        try:
            now = datetime.now(ZoneInfo(Config.TIMEZONE))
        except Exception:  # tzdata ausente
            now = datetime.now()
        window = Config.BUSINESS_HOURS.get(now.weekday())
        if window is None:
            return True
        return not (window[0] <= now.hour < window[1])

    @staticmethod
    def is_human_request(text: str) -> bool:
        from faq_engine import normalize

        normalized = normalize(text)
        for keyword in Config.HUMAN_KEYWORDS:
            if normalize(keyword) and normalize(keyword) in normalized:
                return True
        return False

    @staticmethod
    def is_menu_request(text: str) -> bool:
        from faq_engine import normalize

        normalized = normalize(text)
        return any(normalize(k) == normalized for k in Config.MENU_KEYWORDS)

    # ------------------------------------------------------------------- envio
    def _send(self, to: str, body: str) -> dict:
        if self.is_out_of_hours() and Config.BUSINESS_HOURS_ENABLED:
            body = body + OUT_OF_HOURS_SUFFIX
        return self.wa.send_text(to, body)

    # -------------------------------------------------------------- processamento
    def process(
        self,
        wa_id: str,
        text: str,
        name: str | None = None,
        msg_type: str = "text",
        wa_message_id: str | None = None,
    ) -> dict:
        """Processa uma mensagem recebida e devolve metadados da decisão."""
        started = time.perf_counter()

        # 1) dedupe
        if not self.db.mark_processed(wa_message_id or ""):
            logger.info("Mensagem duplicada ignorada: %s", wa_message_id)
            return {"status": "duplicate", "wa_id": wa_id}

        self.db.upsert_conversation(wa_id, name)
        conversation = self.db.get_conversation(wa_id) or {}
        state = conversation.get("state", "bot")

        self.db.save_message(wa_id, "in", text, msg_type=msg_type, wa_message_id=wa_message_id)
        self.db.touch_conversation(wa_id)

        # 2) rate limit
        if not self.limiter.allow(wa_id):
            logger.warning("Rate limit atingido para %s", wa_id)
            self.db.add_event("rate_limited", wa_id, {"text": text[:120]})
            self._send(wa_id, RATE_LIMIT_MESSAGE)
            self.db.save_message(wa_id, "out", RATE_LIMIT_MESSAGE, source="system")
            return {"status": "rate_limited", "wa_id": wa_id}

        # 3) estado humano: não responde automaticamente
        if state == "human":
            self.db.add_event("message_in_human_queue", wa_id, {"text": text[:200]})
            return {"status": "queued_for_human", "wa_id": wa_id}

        # 4) pedido de atendente humano
        if self.is_human_request(text):
            self.db.set_state(wa_id, "human")
            self.db.add_event("handoff", wa_id, {"text": text[:200]})
            self._send(wa_id, GREETING_FOR_HANDOFF)
            self.db.save_message(wa_id, "out", GREETING_FOR_HANDOFF, source="system")
            self._notify_admin(wa_id, text, name)
            return {"status": "handoff", "wa_id": wa_id}

        # 5) menu
        if self.is_menu_request(text):
            result = self._send_menu(wa_id)
            return {"status": "menu", "wa_id": wa_id, "sent": result.get("success", False)}

        # 6) FAQ
        faq_response = self.faq.get_response(text)
        if faq_response["type"] == "faq":
            latency = int((time.perf_counter() - started) * 1000)
            result = self._send(wa_id, faq_response["answer"])
            self.db.save_message(
                wa_id, "out", faq_response["answer"], source="faq",
                faq_id=faq_response["faq_id"], confidence=faq_response["confidence"], latency_ms=latency,
            )
            return {
                "status": "faq",
                "wa_id": wa_id,
                "faq_id": faq_response["faq_id"],
                "confidence": faq_response["confidence"],
                "latency_ms": latency,
                "sent": result.get("success", False),
            }

        # 7) IA (se habilitada)
        if self.ai.enabled:
            context = self.faq.search(text, limit=Config.AI_CONTEXT_ITEMS)
            history = self.db.get_history(wa_id, limit=Config.AI_MAX_HISTORY)
            ai_result = self.ai.generate(text, context=context, history=history)

            if ai_result["success"]:
                latency = int((time.perf_counter() - started) * 1000)
                self._send(wa_id, ai_result["answer"])
                self.db.save_message(
                    wa_id, "out", ai_result["answer"], source="ai", latency_ms=latency,
                )
                return {
                    "status": "ai",
                    "wa_id": wa_id,
                    "provider": ai_result["provider"],
                    "context_items": len(context),
                    "latency_ms": latency,
                }
            logger.info("IA indisponível (%s) — usando fallback", ai_result.get("error"))

        # 8) fallback
        latency = int((time.perf_counter() - started) * 1000)
        self._send(wa_id, faq_response["answer"])
        self.db.save_message(wa_id, "out", faq_response["answer"], source="fallback", latency_ms=latency)
        self.db.add_event("fallback", wa_id, {"text": text[:200]})
        return {"status": "fallback", "wa_id": wa_id, "latency_ms": latency}

    # ------------------------------------------------------------------- extras
    def _send_menu(self, wa_id: str) -> dict:
        result = self.wa.send_buttons(wa_id, MENU_BODY, MENU_BUTTONS)
        if not result.get("success"):
            # Fallback textual (ex.: dry-run ou erro de API)
            text_menu = (
                f"{MENU_BODY}\n\n"
                "1️⃣ Horário de funcionamento\n"
                "2️⃣ Entrega e prazos\n"
                "3️⃣ Falar com atendente"
            )
            result = self._send(wa_id, text_menu)
            self.db.save_message(wa_id, "out", text_menu, source="system")
            return result
        self.db.save_message(wa_id, "out", MENU_BODY, source="system")
        return result

    def _notify_admin(self, wa_id: str, text: str, name: str | None) -> None:
        if not (Config.NOTIFY_ADMIN_ON_HANDOFF and Config.ADMIN_WHATSAPP_NUMBER):
            return
        message = (
            f"🔔 *Novo pedido de atendimento humano*\n\n"
            f"👤 Contato: {name or 'Sem nome'} ({wa_id})\n"
            f"💬 Mensagem: {text[:300]}\n\n"
            f"Responda diretamente para continuar a conversa."
        )
        result = self.wa.send_text(Config.ADMIN_WHATSAPP_NUMBER, message)
        self.db.add_event("admin_notified", wa_id, {"success": result.get("success", False)})

    # ------------------------------------------------------------- handoff ops
    def release_to_bot(self, wa_id: str) -> bool:
        """Devolve a conversa ao bot."""
        conversation = self.db.get_conversation(wa_id)
        if not conversation:
            return False
        self.db.set_state(wa_id, "bot")
        self.db.add_event("released_to_bot", wa_id, {})
        return True
