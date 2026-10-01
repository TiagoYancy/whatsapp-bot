"""Segurança: verificação de assinatura do webhook e rate limiting."""
from __future__ import annotations

import hashlib
import hmac
import threading
import time
from collections import defaultdict, deque


def verify_signature(payload: bytes, header: str | None, app_secret: str) -> bool:
    """Valida o header X-Hub-Signature-256 enviado pela Meta."""
    if not app_secret:
        return False
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    received = header.split("=", 1)[1]
    return hmac.compare_digest(expected, received)


def sign_payload(payload: bytes, app_secret: str) -> str:
    """Gera a assinatura (usado nos testes)."""
    return "sha256=" + hmac.new(app_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


class RateLimiter:
    """Token bucket simples em memória, por chave (wa_id)."""

    def __init__(self, per_minute: int = 20):
        self.per_minute = max(1, per_minute)
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            queue = self._hits[key]
            while queue and now - queue[0] > 60:
                queue.popleft()
            if len(queue) >= self.per_minute:
                return False
            queue.append(now)
            return True

    def remaining(self, key: str) -> int:
        now = time.monotonic()
        with self._lock:
            queue = self._hits[key]
            while queue and now - queue[0] > 60:
                queue.popleft()
            return max(0, self.per_minute - len(queue))
