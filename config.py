"""Configurações centralizadas do bot (carregadas de variáveis de ambiente / .env)."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(os.getenv("DOTENV_PATH") or None)

BASE_DIR = Path(__file__).resolve().parent


def _env_bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).strip().lower() in {"1", "true", "yes", "sim", "on"}


def _env_list(key: str, default: str = "") -> list[str]:
    raw = os.getenv(key, default)
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


class Config:
    # ---------- WhatsApp Business API ----------
    WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
    WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    WHATSAPP_BUSINESS_ACCOUNT_ID = os.getenv("WHATSAPP_BUSINESS_ACCOUNT_ID", "")
    WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")
    GRAPH_VERSION = os.getenv("GRAPH_VERSION", "v21.0")
    WHATSAPP_API_URL = f"https://graph.facebook.com/{GRAPH_VERSION}"

    # ---------- Webhook ----------
    VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "meu_token_secreto")
    VERIFY_SIGNATURE = _env_bool("VERIFY_SIGNATURE", False)

    # ---------- Servidor ----------
    FLASK_ENV = os.getenv("FLASK_ENV", "development")
    FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))
    HOST = os.getenv("HOST", "0.0.0.0")
    WORKERS = int(os.getenv("WORKERS", "4"))
    ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

    # ---------- Banco de dados ----------
    DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "data" / "bot.db"))

    # ---------- FAQ ----------
    FAQ_THRESHOLD = float(os.getenv("FAQ_THRESHOLD", "0.85"))

    # ---------- IA ----------
    AI_PROVIDER = os.getenv("AI_PROVIDER", "none").strip().lower()  # none | openai | ollama
    AI_TIMEOUT = int(os.getenv("AI_TIMEOUT", "45"))
    AI_MAX_HISTORY = int(os.getenv("AI_MAX_HISTORY", "10"))
    AI_CONTEXT_ITEMS = int(os.getenv("AI_CONTEXT_ITEMS", "3"))
    AI_TEMPERATURE = float(os.getenv("AI_TEMPERATURE", "0.3"))

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
    OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:latest")

    # ---------- Comportamento ----------
    BOT_NAME = os.getenv("BOT_NAME", "Assistente Virtual")
    COMPANY_NAME = os.getenv("COMPANY_NAME", "Nossa Empresa")

    HUMAN_KEYWORDS = _env_list(
        "HUMAN_KEYWORDS",
        "atendente,humano,pessoa,operador,atendimento humano,falar com alguem,"
        "falar com alguém,suporte humano,gerente,supervisor",
    )

    ADMIN_WHATSAPP_NUMBER = os.getenv("ADMIN_WHATSAPP_NUMBER", "")
    NOTIFY_ADMIN_ON_HANDOFF = _env_bool("NOTIFY_ADMIN_ON_HANDOFF", True)

    RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "20"))

    BUSINESS_HOURS_ENABLED = _env_bool("BUSINESS_HOURS_ENABLED", False)
    TIMEZONE = os.getenv("TIMEZONE", "America/Sao_Paulo")
    # weekday (0=segunda) -> (hora_abertura, hora_fechamento) ou None se fechado
    BUSINESS_HOURS = {
        0: (9, 18),
        1: (9, 18),
        2: (9, 18),
        3: (9, 18),
        4: (9, 18),
        5: (9, 13),
        6: None,
    }

    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    # ---------- Menu interativo ----------
    MENU_KEYWORDS = _env_list("MENU_KEYWORDS", "menu,opcoes,opções,ajuda,help,começar,comecar,inicio,início")
