"""Servidor mock compatível com a API OpenAI e Ollama, para testar a camada de IA.

Uso:
    python tests/mock_llm_server.py 8099
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

CAPTURED: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silencia o log padrão
        pass

    def _json(self, payload: dict, status: int = 200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/tags"):
            self._json({"models": [{"name": "llama3.1:latest"}]})
        elif self.path == "/__captured":
            self._json({"calls": CAPTURED})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {}

        CAPTURED.append({"path": self.path, "payload": payload})

        if self.path.endswith("/chat/completions"):
            messages = payload.get("messages", [])
            system = next((m["content"] for m in messages if m["role"] == "system"), "")
            user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
            reply = (
                f"[MOCK-OPENAI] Recebi: {user!r}. "
                f"Contexto recebido: {'sim' if 'CONTEXTO' in system else 'não'} "
                f"({len(system)} chars de system)."
            )
            self._json(
                {
                    "id": "chatcmpl-mock",
                    "object": "chat.completion",
                    "model": payload.get("model", "mock"),
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
                }
            )
        elif self.path.endswith("/api/chat"):
            messages = payload.get("messages", [])
            user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
            reply = f"[MOCK-OLLAMA] Recebi: {user!r}."
            self._json({"model": payload.get("model", "mock"), "message": {"role": "assistant", "content": reply}, "done": True})
        else:
            self._json({"error": "not found"}, 404)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    print(f"Mock LLM ouvindo em http://127.0.0.1:{port}", flush=True)
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
