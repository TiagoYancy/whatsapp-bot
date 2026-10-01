"""Testes de integração da camada de IA contra um servidor HTTP mock real.

Sobe um servidor compatível com OpenAI/Ollama em localhost e exercita
AIClient e BotHandler com chamadas HTTP de verdade.
"""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
INT_DB = Path(__file__).resolve().parent / "_test_integration.db"
MOCK_PORT = 8099
MOCK_URL = f"http://127.0.0.1:{MOCK_PORT}"


def _port_free(port: int) -> bool:
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) != 0


@pytest.fixture(scope="module")
def mock_llm():
    if not _port_free(MOCK_PORT):
        pytest.skip(f"porta {MOCK_PORT} já em uso")
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "tests" / "mock_llm_server.py"), str(MOCK_PORT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    # espera ficar pronto
    for _ in range(50):
        try:
            with urlopen(f"{MOCK_URL}/api/tags", timeout=0.5) as response:
                if response.status == 200:
                    break
        except Exception:
            time.sleep(0.1)
    else:
        proc.terminate()
        pytest.fail("mock LLM não subiu")

    yield MOCK_URL
    proc.terminate()
    proc.wait(timeout=5)


@pytest.fixture
def ai_openai(mock_llm, monkeypatch):
    from ai_client import AIClient
    from config import Config

    monkeypatch.setattr(Config, "AI_PROVIDER", "openai")
    monkeypatch.setattr(Config, "OPENAI_API_KEY", "chave_teste")
    monkeypatch.setattr(Config, "OPENAI_BASE_URL", f"{mock_llm}/v1")
    monkeypatch.setattr(Config, "OPENAI_MODEL", "mock-model")
    return AIClient("openai")


@pytest.fixture
def ai_ollama(mock_llm, monkeypatch):
    from ai_client import AIClient
    from config import Config

    monkeypatch.setattr(Config, "AI_PROVIDER", "ollama")
    monkeypatch.setattr(Config, "OLLAMA_URL", mock_llm)
    monkeypatch.setattr(Config, "OLLAMA_MODEL", "llama3.1:latest")
    return AIClient("ollama")


@pytest.fixture
def integration_db():
    if INT_DB.exists():
        INT_DB.unlink()
    from db import Database

    db = Database(str(INT_DB))
    yield db
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(INT_DB) + suffix)
        if path.exists():
            path.unlink()


# ---------------------------------------------------------------- AIClient
def test_ai_client_openai_roundtrip(ai_openai):
    result = ai_openai.generate("qual o prazo de entrega?", context=[])
    assert result["success"] is True, result
    assert "[MOCK-OPENAI]" in result["answer"]
    assert "prazo de entrega" in result["answer"]


def test_ai_client_ollama_roundtrip(ai_ollama):
    result = ai_ollama.generate("vocês aceitam pix?")
    assert result["success"] is True, result
    assert "[MOCK-OLLAMA]" in result["answer"]


def test_ai_client_sends_faq_context_in_prompt(ai_openai, mock_llm, integration_db):
    from faq_engine import FAQEngine

    faq = FAQEngine(integration_db)
    context = faq.search("qual o prazo de entrega?", limit=3)
    assert context, "esperava contexto recuperado do FAQ"

    ai_openai.generate("e o prazo?", context=context)

    with urlopen(f"{mock_llm}/__captured", timeout=5) as response:
        calls = json.load(response)["calls"]

    last = calls[-1]["payload"]
    system_prompt = next(m["content"] for m in last["messages"] if m["role"] == "system")
    assert "CONTEXTO" in system_prompt
    assert "Entrega e prazos" in system_prompt, "o tópico do FAQ deveria estar no prompt"


def test_ai_client_includes_conversation_history(ai_openai, mock_llm):
    history = [
        {"direction": "in", "body": "oi"},
        {"direction": "out", "body": "olá! como posso ajudar?"},
        {"direction": "in", "body": "quero saber do frete"},
    ]
    ai_openai.generate("e o prazo?", context=[], history=history)

    with urlopen(f"{mock_llm}/__captured", timeout=5) as response:
        calls = json.load(response)["calls"]

    roles = [m["role"] for m in calls[-1]["payload"]["messages"]]
    assert roles.count("user") >= 3
    assert "assistant" in roles


def test_ai_client_reports_disabled():
    from ai_client import AIClient

    client = AIClient("none")
    assert client.enabled is False
    result = client.generate("oi")
    assert result["success"] is False
    assert result["error"] == "ai_disabled"


def test_ai_client_handles_unreachable_provider(monkeypatch):
    from ai_client import AIClient
    from config import Config

    monkeypatch.setattr(Config, "AI_PROVIDER", "ollama")
    monkeypatch.setattr(Config, "OLLAMA_URL", "http://127.0.0.1:9")  # porta fechada
    monkeypatch.setattr(Config, "AI_TIMEOUT", 2)

    result = AIClient("ollama").generate("oi")
    assert result["success"] is False
    assert result["error"]


def test_ai_health_reports_ollama_models(ai_ollama):
    health = ai_ollama.health()
    assert health["reachable"] is True
    assert "llama3.1:latest" in health["models"]


# ------------------------------------------------------------ BotHandler + IA
def test_handler_uses_ai_when_faq_misses(mock_llm, monkeypatch, integration_db):
    from ai_client import AIClient
    from bot import BotHandler
    from config import Config
    from faq_engine import FAQEngine
    from whatsapp_client import WhatsAppClient

    monkeypatch.setattr(Config, "AI_PROVIDER", "openai")
    monkeypatch.setattr(Config, "OPENAI_API_KEY", "chave_teste")
    monkeypatch.setattr(Config, "OPENAI_BASE_URL", f"{mock_llm}/v1")
    monkeypatch.setattr(Config, "OPENAI_MODEL", "mock-model")

    faq = FAQEngine(integration_db)
    handler = BotHandler(
        db=integration_db,
        faq=faq,
        ai=AIClient("openai"),
        wa=WhatsAppClient(dry_run=True),
        dry_run=True,
    )

    # Mensagem fora do FAQ → deve cair na IA
    result = handler.process("5511900000200", "vocês têm loja física em Campinas?", wa_message_id="wamid.AI1")
    assert result["status"] == "ai", result
    assert result["provider"] == "openai"

    history = integration_db.get_history("5511900000200")
    assert history[-1]["source"] == "ai"
    assert "[MOCK-OPENAI]" in history[-1]["body"]


def test_handler_prefers_faq_over_ai(mock_llm, monkeypatch, integration_db):
    from ai_client import AIClient
    from bot import BotHandler
    from config import Config
    from faq_engine import FAQEngine
    from whatsapp_client import WhatsAppClient

    monkeypatch.setattr(Config, "AI_PROVIDER", "openai")
    monkeypatch.setattr(Config, "OPENAI_API_KEY", "chave_teste")
    monkeypatch.setattr(Config, "OPENAI_BASE_URL", f"{mock_llm}/v1")

    faq = FAQEngine(integration_db)
    handler = BotHandler(
        db=integration_db,
        faq=faq,
        ai=AIClient("openai"),
        wa=WhatsAppClient(dry_run=True),
        dry_run=True,
    )

    result = handler.process("5511900000201", "qual o horario de funcionamento?", wa_message_id="wamid.AI2")
    assert result["status"] == "faq", "FAQ exato deve vencer a IA"
    assert result["faq_id"] == "horario"


def test_handler_falls_back_when_ai_fails(monkeypatch, integration_db):
    """IA habilitada mas provedor inacessível → fallback gracioso."""
    from ai_client import AIClient
    from bot import BotHandler
    from config import Config
    from faq_engine import FAQEngine
    from whatsapp_client import WhatsAppClient

    monkeypatch.setattr(Config, "AI_PROVIDER", "ollama")
    monkeypatch.setattr(Config, "OLLAMA_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(Config, "AI_TIMEOUT", 2)

    faq = FAQEngine(integration_db)
    handler = BotHandler(
        db=integration_db,
        faq=faq,
        ai=AIClient("ollama"),
        wa=WhatsAppClient(dry_run=True),
        dry_run=True,
    )

    result = handler.process("5511900000202", "pergunta totalmente fora do faq", wa_message_id="wamid.AI3")
    assert result["status"] == "fallback"
    assert integration_db.get_history("5511900000202")[-1]["source"] == "fallback"
