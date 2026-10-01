"""Camada de IA: geração de respostas com contexto (RAG leve sobre o FAQ).

Provedores suportados:
  - none    : desabilitado (usa apenas FAQ)
  - openai  : qualquer endpoint compatível com /chat/completions
  - ollama  : LLM local via /api/chat
"""
from __future__ import annotations

import logging
from typing import Any

import requests

from config import Config

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Você é {bot_name}, o assistente virtual de atendimento da {empresa} no WhatsApp.

REGRAS:
1. Responda SEMPRE em português do Brasil, de forma cordial, objetiva e com no máximo 3 parágrafos curtos.
2. Use apenas as informações do CONTEXTO fornecido. Nunca invente preços, prazos, políticas ou dados.
3. Se o contexto não tiver a resposta, diga que não tem essa informação e ofereça transferir para um atendente humano.
4. Nunca peça dados sensíveis (senha, número completo de cartão, CPF, código de verificação).
5. Não use formatação markdown complexa; o WhatsApp entende *negrito* e _itálico_.
6. Não escreva saudações repetidas se a conversa já começou."""


class AIClient:
    """Cliente unificado para geração de respostas."""

    def __init__(self, provider: str | None = None):
        self.provider = (provider or Config.AI_PROVIDER or "none").lower()
        self.timeout = Config.AI_TIMEOUT

    @property
    def enabled(self) -> bool:
        if self.provider == "openai":
            return bool(Config.OPENAI_API_KEY)
        if self.provider == "ollama":
            return True
        return False

    # ------------------------------------------------------------------ helpers
    def _build_messages(self, user_message: str, context: list[dict], history: list[dict]) -> list[dict]:
        system = SYSTEM_PROMPT.format(bot_name=Config.BOT_NAME, empresa=Config.COMPANY_NAME)

        if context:
            block = "\n\n".join(
                f"[{item['topic']}]\nP: {item['question']}\nR: {item['answer']}" for item in context
            )
            system += f"\n\nCONTEXTO (base de conhecimento oficial):\n{block}"
        else:
            system += "\n\nCONTEXTO: (nenhum trecho relevante encontrado na base de conhecimento)"

        messages = [{"role": "system", "content": system}]

        for entry in history[-Config.AI_MAX_HISTORY :]:
            if entry.get("direction") == "in" and entry.get("body"):
                messages.append({"role": "user", "content": entry["body"]})
            elif entry.get("direction") == "out" and entry.get("body"):
                messages.append({"role": "assistant", "content": entry["body"]})

        messages.append({"role": "user", "content": user_message})
        return messages

    # ---------------------------------------------------------------- providers
    def _call_openai(self, messages: list[dict]) -> str:
        url = f"{Config.OPENAI_BASE_URL}/chat/completions"
        headers = {
            "Authorization": f"Bearer {Config.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": Config.OPENAI_MODEL,
            "messages": messages,
            "temperature": Config.AI_TEMPERATURE,
            "max_tokens": 500,
        }
        response = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()

    def _call_ollama(self, messages: list[dict]) -> str:
        url = f"{Config.OLLAMA_URL}/api/chat"
        payload = {
            "model": Config.OLLAMA_MODEL,
            "messages": messages,
            "stream": False,
            "options": {"temperature": Config.AI_TEMPERATURE},
        }
        response = requests.post(url, json=payload, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        return (data.get("message", {}).get("content") or "").strip()

    # ------------------------------------------------------------------- public
    def generate(self, user_message: str, context: list[dict] | None = None, history: list[dict] | None = None) -> dict[str, Any]:
        """Gera uma resposta. Retorna {'success', 'answer', 'provider', 'error'}."""
        if not self.enabled:
            return {"success": False, "answer": None, "provider": self.provider, "error": "ai_disabled"}

        messages = self._build_messages(user_message, context or [], history or [])

        try:
            if self.provider == "openai":
                answer = self._call_openai(messages)
            elif self.provider == "ollama":
                answer = self._call_ollama(messages)
            else:
                return {"success": False, "answer": None, "provider": self.provider, "error": "unknown_provider"}

            if not answer:
                return {"success": False, "answer": None, "provider": self.provider, "error": "empty_response"}

            return {"success": True, "answer": answer, "provider": self.provider, "error": None}

        except requests.exceptions.Timeout:
            logger.warning("Timeout na chamada de IA (%s)", self.provider)
            return {"success": False, "answer": None, "provider": self.provider, "error": "timeout"}
        except requests.exceptions.RequestException as exc:
            logger.warning("Erro na chamada de IA (%s): %s", self.provider, exc)
            return {"success": False, "answer": None, "provider": self.provider, "error": str(exc)}
        except (KeyError, IndexError, ValueError) as exc:
            logger.warning("Resposta inesperada da IA (%s): %s", self.provider, exc)
            return {"success": False, "answer": None, "provider": self.provider, "error": "malformed_response"}

    def health(self) -> dict:
        """Checagem rápida de disponibilidade do provedor."""
        if self.provider == "none":
            return {"provider": "none", "enabled": False, "reachable": False}
        if self.provider == "openai":
            return {
                "provider": "openai",
                "enabled": self.enabled,
                "reachable": None,
                "model": Config.OPENAI_MODEL,
            }
        # ollama
        try:
            response = requests.get(f"{Config.OLLAMA_URL}/api/tags", timeout=5)
            response.raise_for_status()
            models = [m.get("name") for m in response.json().get("models", [])]
            return {
                "provider": "ollama",
                "enabled": True,
                "reachable": True,
                "model": Config.OLLAMA_MODEL,
                "model_available": Config.OLLAMA_MODEL in models,
                "models": models[:20],
            }
        except requests.exceptions.RequestException as exc:
            return {"provider": "ollama", "enabled": True, "reachable": False, "error": str(exc)}
