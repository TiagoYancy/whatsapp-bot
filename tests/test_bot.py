"""Suíte de testes do bot (pytest). Roda 100% offline com dry-run."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Banco isolado por execução de teste
TEST_DB = Path(__file__).resolve().parent / "_test_bot.db"
os.environ["DB_PATH"] = str(TEST_DB)
os.environ["AI_PROVIDER"] = "none"
os.environ["VERIFY_TOKEN"] = "token_teste"
os.environ["ADMIN_TOKEN"] = ""
os.environ["WHATSAPP_APP_SECRET"] = "segredo_teste"


@pytest.fixture(scope="session", autouse=True)
def _cleanup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    yield
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(TEST_DB) + suffix)
        if path.exists():
            path.unlink()


@pytest.fixture
def db():
    from db import Database

    return Database(str(TEST_DB))


@pytest.fixture
def faq(db):
    from faq_engine import FAQEngine

    return FAQEngine(db)


@pytest.fixture
def handler(db, faq):
    from ai_client import AIClient
    from bot import BotHandler
    from whatsapp_client import WhatsAppClient

    return BotHandler(
        db=db,
        faq=faq,
        ai=AIClient("none"),
        wa=WhatsAppClient(dry_run=True),
        dry_run=True,
    )


@pytest.fixture
def client(db):
    from app import create_app

    app = create_app(dry_run=True)
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


# --------------------------------------------------------------------- FAQ
@pytest.mark.parametrize(
    "message,expected_id",
    [
        ("oi, tudo bem?", "saudacao"),
        ("qual o horario de funcionamento?", "horario"),
        ("quais as formas de pagamento?", "pagamento"),
        ("qual o prazo de entrega?", "entrega"),
        ("quero trocar um produto", "troca"),
        ("quanto custa?", "preco"),
        ("obrigado pela ajuda", "obrigado"),
        ("tchau, ate mais", "despedida"),
        ("quero falar com um atendente", "atendente"),
    ],
)
def test_faq_matches_expected_intent(faq, message, expected_id):
    result = faq.get_response(message)
    assert result["type"] == "faq", f"{message!r} caiu em fallback"
    assert result["faq_id"] == expected_id
    assert result["confidence"] >= 0.85


@pytest.mark.parametrize(
    "message",
    ["asdfghjkl qwerty", "zzzzzz", "1234567890 !!!"],
)
def test_faq_falls_back_on_gibberish(faq, message):
    result = faq.get_response(message)
    assert result["type"] == "fallback"
    assert result["confidence"] == 0.0


def test_normalize_removes_accents_and_punctuation():
    from faq_engine import normalize

    assert normalize("Olá, TUDO bem?!") == "ola tudo bem"


def test_faq_crud(faq, db):
    faq.add_item("teste_item", "Teste", ["palavrao"], "Pergunta teste", "Resposta teste", priority=1)
    assert db.faq_get("teste_item") is not None
    result = faq.get_response("palavrao")
    assert result["faq_id"] == "teste_item"
    assert faq.delete_item("teste_item") is True
    assert db.faq_get("teste_item") is None


# --------------------------------------------------------------------- Bot
def test_handler_answers_faq(handler, db):
    result = handler.process("5511900000001", "qual o prazo de entrega?", wa_message_id="wamid.A1")
    assert result["status"] == "faq"
    assert result["faq_id"] == "entrega"
    history = db.get_history("5511900000001")
    assert len(history) == 2
    assert history[0]["direction"] == "in"
    assert history[1]["direction"] == "out"


def test_handler_deduplicates_messages(handler):
    first = handler.process("5511900000002", "oi", wa_message_id="wamid.DUP")
    second = handler.process("5511900000002", "oi", wa_message_id="wamid.DUP")
    assert first["status"] != "duplicate"
    assert second["status"] == "duplicate"


def test_handler_handoff_sets_human_state(handler, db):
    result = handler.process("5511900000003", "quero falar com atendente", wa_message_id="wamid.H1")
    assert result["status"] == "handoff"
    assert db.get_conversation("5511900000003")["state"] == "human"

    # próxima mensagem não é respondida automaticamente
    follow_up = handler.process("5511900000003", "alguem ai?", wa_message_id="wamid.H2")
    assert follow_up["status"] == "queued_for_human"


def test_handler_release_returns_to_bot(handler, db):
    handler.process("5511900000004", "atendente", wa_message_id="wamid.R1")
    assert db.get_conversation("5511900000004")["state"] == "human"
    assert handler.release_to_bot("5511900000004") is True
    assert db.get_conversation("5511900000004")["state"] == "bot"
    assert handler.process("5511900000004", "oi de novo", wa_message_id="wamid.R2")["status"] != "queued_for_human"


def test_handler_rate_limit(handler):
    from config import Config

    wa_id = "5511900000005"
    statuses = [
        handler.process(wa_id, "oi", wa_message_id=f"wamid.RL{i}").get("status")
        for i in range(Config.RATE_LIMIT_PER_MINUTE + 3)
    ]
    assert "rate_limited" in statuses


def test_handler_fallback_records_event(handler, db):
    result = handler.process("5511900000006", "xyzabc sem sentido", wa_message_id="wamid.F1")
    assert result["status"] == "fallback"
    events = db.recent_events(limit=20, event_type="fallback")
    assert any(e["wa_id"] == "5511900000006" for e in events)


def test_handler_menu(handler):
    result = handler.process("5511900000007", "menu", wa_message_id="wamid.M1")
    assert result["status"] == "menu"


# ------------------------------------------------------------------ WhatsApp
def test_split_message_respects_limit():
    from whatsapp_client import WhatsAppClient

    body = "\n".join(["linha " * 20] * 300)
    chunks = WhatsAppClient._split_message(body, limit=500)
    assert all(len(c) <= 500 for c in chunks)
    assert "".join(chunks).replace("\n", "").replace(" ", "") == body.replace("\n", "").replace(" ", "")


def test_whatsapp_client_requires_config():
    from whatsapp_client import WhatsAppClient

    client = WhatsAppClient(token="", phone_number_id="", dry_run=False)
    assert client.configured is False
    assert client.send_text("5511999999999", "oi")["success"] is False


def test_whatsapp_dry_run_succeeds():
    from whatsapp_client import WhatsAppClient

    client = WhatsAppClient(token="x", phone_number_id="y", dry_run=True)
    assert client.send_text("5511999999999", "oi")["success"] is True


# ------------------------------------------------------------------ Security
def test_signature_roundtrip():
    from security import sign_payload, verify_signature

    payload = b'{"object":"whatsapp_business_account"}'
    header = sign_payload(payload, "segredo_teste")
    assert verify_signature(payload, header, "segredo_teste") is True
    assert verify_signature(payload, header, "outro_segredo") is False
    assert verify_signature(payload, "sha256=deadbeef", "segredo_teste") is False
    assert verify_signature(payload, None, "segredo_teste") is False


def test_rate_limiter_window():
    from security import RateLimiter

    limiter = RateLimiter(per_minute=3)
    assert [limiter.allow("k") for _ in range(3)] == [True, True, True]
    assert limiter.allow("k") is False
    assert limiter.allow("outra") is True


# --------------------------------------------------------------------- API
def test_webhook_verification_success(client):
    response = client.get("/webhook?hub.mode=subscribe&hub.verify_token=token_teste&hub.challenge=abc123")
    assert response.status_code == 200
    assert response.data.decode() == "abc123"


def test_webhook_verification_failure(client):
    response = client.get("/webhook?hub.mode=subscribe&hub.verify_token=errado&hub.challenge=abc123")
    assert response.status_code == 403


def test_webhook_post_processes_message(client):
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"wa_id": "5511900000010", "profile": {"name": "Cliente Teste"}}],
                            "messages": [
                                {
                                    "id": "wamid.WEBHOOK1",
                                    "from": "5511900000010",
                                    "timestamp": "1700000000",
                                    "type": "text",
                                    "text": {"body": "qual o horario de funcionamento?"},
                                }
                            ],
                        }
                    }
                ]
            }
        ],
    }
    response = client.post("/webhook", json=payload)
    assert response.status_code == 200
    assert response.get_json()["processed"] == 1


def test_webhook_ignores_non_whatsapp_object(client):
    response = client.post("/webhook", json={"object": "page"})
    assert response.status_code == 200
    assert response.get_json()["status"] == "ignored"


def test_webhook_invalid_json_does_not_crash(client):
    response = client.post("/webhook", data="not json", content_type="application/json")
    assert response.status_code == 200


def test_api_faq_test_endpoint(client):
    response = client.post("/api/faq/test", json={"message": "formas de pagamento"})
    assert response.status_code == 200
    assert response.get_json()["response"]["faq_id"] == "pagamento"


def test_api_faq_test_requires_message(client):
    assert client.post("/api/faq/test", json={}).status_code == 400


def test_api_faq_create_and_delete(client):
    payload = {
        "id": "api_item",
        "topic": "API",
        "keywords": ["apitem"],
        "question": "Pergunta API",
        "answer": "Resposta API",
    }
    assert client.post("/api/faq", json=payload).status_code == 201
    assert client.delete("/api/faq/api_item").status_code == 200
    assert client.delete("/api/faq/api_item").status_code == 404


def test_api_simulate(client):
    response = client.post("/api/simulate", json={"message": "quero trocar", "from": "5511900000011"})
    assert response.status_code == 200
    assert response.get_json()["result"]["status"] == "faq"


def test_api_stats_and_health(client):
    assert client.get("/api/stats").status_code == 200
    health = client.get("/health").get_json()
    assert health["status"] in {"healthy", "degraded"}
    assert health["database"]["faq_items"] > 0


def test_api_events(client):
    assert client.get("/api/events?limit=5").status_code == 200


def test_home_reports_version(client):
    body = client.get("/").get_json()
    assert body["service"] == "WhatsApp FAQ Bot"
    assert body["dry_run"] is True


def test_admin_token_enforced(monkeypatch):
    from config import Config

    monkeypatch.setattr(Config, "ADMIN_TOKEN", "segredo_admin")
    from app import create_app

    app = create_app(dry_run=True)
    with app.test_client() as test_client:
        assert test_client.get("/api/stats").status_code == 401
        assert test_client.get("/api/stats", headers={"X-Admin-Token": "segredo_admin"}).status_code == 200
        assert test_client.get("/api/stats", headers={"Authorization": "Bearer segredo_admin"}).status_code == 200
        assert test_client.get("/api/stats", headers={"X-Admin-Token": "errado"}).status_code == 401


def test_dashboard_renders(client):
    response = client.get("/dashboard")
    assert response.status_code == 200
    body = response.data.decode()
    assert "WhatsApp Bot" in body
    assert "/api/stats" in body
    assert "Base de conhecimento" in body


def test_api_faq_list_shape(client):
    body = client.get("/api/faq").get_json()
    assert body["total"] > 0
    first = body["items"][0]
    for field in ("id", "topic", "keywords", "question", "answer"):
        assert field in first, f"campo {field} ausente"
    assert isinstance(first["keywords"], list)


def test_webhook_duplicate_not_counted_as_processed(client):
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"wa_id": "5511900000099", "profile": {"name": "Dup"}}],
                            "messages": [
                                {
                                    "id": "wamid.DUPTEST",
                                    "from": "5511900000099",
                                    "timestamp": "1700000000",
                                    "type": "text",
                                    "text": {"body": "oi"},
                                }
                            ],
                        }
                    }
                ]
            }
        ],
    }
    first = client.post("/webhook", json=payload).get_json()
    second = client.post("/webhook", json=payload).get_json()
    assert first["processed"] == 1
    assert second["processed"] == 0, "duplicata não deve contar como processada"


def test_webhook_rejects_bad_signature_when_enabled(monkeypatch):
    from config import Config

    monkeypatch.setattr(Config, "VERIFY_SIGNATURE", True)
    monkeypatch.setattr(Config, "WHATSAPP_APP_SECRET", "segredo_teste")
    from app import create_app

    app = create_app(dry_run=True)
    with app.test_client() as test_client:
        payload = b'{"object":"whatsapp_business_account","entry":[]}'
        assert test_client.post("/webhook", data=payload, content_type="application/json").status_code == 401

        from security import sign_payload

        ok = test_client.post(
            "/webhook",
            data=payload,
            content_type="application/json",
            headers={"X-Hub-Signature-256": sign_payload(payload, "segredo_teste")},
        )
        assert ok.status_code == 200
