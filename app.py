"""Aplicação Flask: webhook do WhatsApp + API de administração."""
from __future__ import annotations

import logging
import os
import sys

from flask import Flask, jsonify, render_template, request

from ai_client import AIClient
from bot import BotHandler
from config import Config
from db import Database
from faq_engine import FAQEngine
from security import verify_signature
from whatsapp_client import WhatsAppClient


def setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, Config.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        stream=sys.stdout,
    )


def create_app(dry_run: bool | None = None) -> Flask:
    setup_logging()
    logger = logging.getLogger("app")

    if dry_run is None:
        dry_run = os.getenv("DRY_RUN", "false").lower() in {"1", "true", "yes", "sim"}

    app = Flask(__name__)
    app.config["DRY_RUN"] = dry_run

    db = Database()
    faq = FAQEngine(db)
    ai = AIClient()
    wa = WhatsAppClient(dry_run=dry_run)
    handler = BotHandler(db=db, faq=faq, ai=ai, wa=wa, dry_run=dry_run)

    app.extensions["db"] = db
    app.extensions["faq"] = faq
    app.extensions["ai"] = ai
    app.extensions["wa"] = wa
    app.extensions["handler"] = handler

    logger.info(
        "Bot iniciado | IA=%s | WhatsApp configurado=%s | dry_run=%s | DB=%s",
        ai.provider if ai.enabled else "desativada",
        wa.configured,
        dry_run,
        Config.DB_PATH,
    )

    # ------------------------------------------------------------------ auth
    def _admin_ok() -> bool:
        if not Config.ADMIN_TOKEN:
            return True  # sem token configurado: aberto (apenas dev)
        header = request.headers.get("X-Admin-Token", "")
        if header == Config.ADMIN_TOKEN:
            return True
        auth = request.headers.get("Authorization", "")
        return auth.startswith("Bearer ") and auth[7:] == Config.ADMIN_TOKEN

    def _guard():
        if not _admin_ok():
            return jsonify({"error": "unauthorized"}), 401
        return None

    # --------------------------------------------------------------- básicos
    @app.get("/")
    def home():
        return jsonify(
            {
                "service": "WhatsApp FAQ Bot",
                "version": "2.0.0",
                "status": "online",
                "ai_provider": ai.provider if ai.enabled else "disabled",
                "whatsapp_configured": wa.configured,
                "dry_run": dry_run,
            }
        )

    @app.get("/health")
    def health():
        ai_health = ai.health()
        status = "healthy"
        if ai.enabled and ai_health.get("reachable") is False:
            status = "degraded"
        return jsonify(
            {
                "status": status,
                "database": {"path": Config.DB_PATH, "faq_items": db.faq_count()},
                "ai": ai_health,
                "whatsapp": {
                    "configured": wa.configured,
                    "phone_number_id_set": bool(Config.WHATSAPP_PHONE_NUMBER_ID),
                },
                "out_of_hours": handler.is_out_of_hours(),
            }
        )

    # --------------------------------------------------------------- webhook
    @app.get("/webhook")
    def verify_webhook():
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge", "")

        if mode == "subscribe" and token == Config.VERIFY_TOKEN:
            logger.info("Webhook verificado com sucesso")
            return challenge, 200

        logger.warning("Falha na verificação do webhook (mode=%s)", mode)
        return jsonify({"error": "verificação falhou"}), 403

    @app.post("/webhook")
    def webhook():
        raw = request.get_data()

        if Config.VERIFY_SIGNATURE:
            signature = request.headers.get("X-Hub-Signature-256")
            if not verify_signature(raw, signature, Config.WHATSAPP_APP_SECRET):
                logger.warning("Assinatura do webhook inválida")
                return jsonify({"error": "assinatura inválida"}), 401

        data = request.get_json(silent=True) or {}

        if data.get("object") != "whatsapp_business_account":
            return jsonify({"status": "ignored"}), 200

        processed = []
        try:
            for entry in data.get("entry", []):
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    contacts = {
                        c.get("wa_id"): c.get("profile", {}).get("name")
                        for c in value.get("contacts", [])
                    }

                    for message in value.get("messages", []):
                        result = _handle_message(handler, message, contacts)
                        if result and result.get("status") != "duplicate":
                            processed.append(result)

                    for status in value.get("statuses", []):
                        db.add_event("status", status.get("recipient_id"), {"status": status.get("status")})
        except Exception as exc:  # nunca devolver 5xx para a Meta
            logger.exception("Erro ao processar webhook: %s", exc)
            db.add_event("webhook_error", None, {"error": str(exc)})

        return jsonify({"status": "ok", "processed": len(processed)}), 200

    # --------------------------------------------------------------- API
    @app.post("/api/send")
    def api_send():
        guard = _guard()
        if guard:
            return guard
        data = request.get_json(silent=True) or {}
        to, message = data.get("to"), data.get("message")
        if not to or not message:
            return jsonify({"error": "campos 'to' e 'message' são obrigatórios"}), 400
        result = wa.send_text(to, message)
        db.save_message(to, "out", message, source="manual")
        return jsonify(result), (200 if result.get("success") else 502)

    @app.post("/api/simulate")
    def api_simulate():
        """Simula uma mensagem recebida, sem chamar a API do WhatsApp."""
        guard = _guard()
        if guard:
            return guard
        data = request.get_json(silent=True) or {}
        text = data.get("message", "")
        wa_id = data.get("from", "5511999999999")
        if not text:
            return jsonify({"error": "campo 'message' é obrigatório"}), 400

        saved_dry = handler.wa.dry_run
        handler.wa.dry_run = True
        try:
            result = handler.process(
                wa_id=wa_id,
                text=text,
                name=data.get("name", "Teste"),
                wa_message_id=data.get("message_id"),
            )
        finally:
            handler.wa.dry_run = saved_dry

        history = db.get_history(wa_id, limit=2)
        return jsonify({"result": result, "last_messages": history})

    @app.post("/api/faq/test")
    def api_faq_test():
        guard = _guard()
        if guard:
            return guard
        data = request.get_json(silent=True) or {}
        message = data.get("message", "")
        if not message:
            return jsonify({"error": "campo 'message' é obrigatório"}), 400
        return jsonify(
            {
                "input": message,
                "response": faq.get_response(message),
                "ranking": faq.rank(message, limit=3),
            }
        )

    @app.get("/api/faq")
    def api_faq_list():
        guard = _guard()
        if guard:
            return guard
        items = db.faq_all()
        return jsonify({"total": len(items), "items": items})

    @app.post("/api/faq")
    def api_faq_create():
        guard = _guard()
        if guard:
            return guard
        data = request.get_json(silent=True) or {}
        required = {"id", "topic", "keywords", "question", "answer"}
        missing = required - set(data)
        if missing:
            return jsonify({"error": f"campos obrigatórios: {sorted(missing)}"}), 400
        if not isinstance(data["keywords"], list) or not data["keywords"]:
            return jsonify({"error": "'keywords' deve ser uma lista não vazia"}), 400
        faq.add_item(
            data["id"],
            data["topic"],
            data["keywords"],
            data["question"],
            data["answer"],
            int(data.get("priority", 100)),
        )
        return jsonify({"status": "created", "id": data["id"]}), 201

    @app.delete("/api/faq/<item_id>")
    def api_faq_delete(item_id: str):
        guard = _guard()
        if guard:
            return guard
        if not faq.delete_item(item_id):
            return jsonify({"error": "item não encontrado"}), 404
        return jsonify({"status": "deleted", "id": item_id})

    @app.get("/api/conversations")
    def api_conversations():
        guard = _guard()
        if guard:
            return guard
        limit = min(int(request.args.get("limit", 50)), 200)
        offset = int(request.args.get("offset", 0))
        state = request.args.get("state")
        items = db.list_conversations(limit=limit, offset=offset, state=state)
        return jsonify({"total": len(items), "conversations": items})

    @app.get("/api/conversations/<wa_id>/messages")
    def api_conversation_messages(wa_id: str):
        guard = _guard()
        if guard:
            return guard
        limit = min(int(request.args.get("limit", 50)), 500)
        return jsonify({"wa_id": wa_id, "messages": db.get_history(wa_id, limit=limit)})

    @app.post("/api/conversations/<wa_id>/release")
    def api_release(wa_id: str):
        guard = _guard()
        if guard:
            return guard
        if not handler.release_to_bot(wa_id):
            return jsonify({"error": "conversa não encontrada"}), 404
        return jsonify({"status": "released", "wa_id": wa_id})

    @app.get("/api/stats")
    def api_stats():
        guard = _guard()
        if guard:
            return guard
        return jsonify({"stats": db.stats(), "daily": db.daily_stats(days=7)})

    @app.get("/api/events")
    def api_events():
        guard = _guard()
        if guard:
            return guard
        limit = min(int(request.args.get("limit", 50)), 200)
        return jsonify({"events": db.recent_events(limit=limit, event_type=request.args.get("type"))})

    @app.get("/api/whatsapp/info")
    def api_wa_info():
        guard = _guard()
        if guard:
            return guard
        return jsonify(wa.get_phone_number_info())

    @app.get("/dashboard")
    def dashboard():
        return render_template("dashboard.html")

    return app


def _handle_message(handler: BotHandler, message: dict, contacts: dict) -> dict | None:
    """Extrai o texto de qualquer tipo de mensagem suportado e processa."""
    msg_type = message.get("type")
    wa_id = message.get("from")
    if not wa_id:
        return None

    text = ""
    if msg_type == "text":
        text = message.get("text", {}).get("body", "")
    elif msg_type == "button":
        text = message.get("button", {}).get("text", "")
    elif msg_type == "interactive":
        interactive = message.get("interactive", {})
        kind = interactive.get("type")
        if kind == "button_reply":
            text = interactive.get("button_reply", {}).get("title", "")
        elif kind == "list_reply":
            text = interactive.get("list_reply", {}).get("title", "")
    elif msg_type in {"image", "audio", "video", "document", "sticker"}:
        text = "midia"
    elif msg_type == "location":
        text = "localizacao"
    else:
        return None

    if not text:
        return None

    return handler.process(
        wa_id=wa_id,
        text=text,
        name=contacts.get(wa_id),
        msg_type=msg_type,
        wa_message_id=message.get("id"),
    )


app = create_app()


if __name__ == "__main__":
    # use_reloader=False: evita processo filho órfão segurando a porta
    app.run(
        host=Config.HOST,
        port=Config.FLASK_PORT,
        debug=Config.FLASK_ENV == "development",
        use_reloader=False,
    )
