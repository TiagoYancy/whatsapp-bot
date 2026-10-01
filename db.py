"""Camada de persistência em SQLite (conversas, mensagens, FAQ, eventos)."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from config import Config

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    wa_id              TEXT PRIMARY KEY,
    name               TEXT,
    state              TEXT NOT NULL DEFAULT 'bot',
    human_requested_at TEXT,
    message_count      INTEGER NOT NULL DEFAULT 0,
    last_message_at    TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    wa_id         TEXT NOT NULL,
    direction     TEXT NOT NULL,
    msg_type      TEXT NOT NULL DEFAULT 'text',
    body          TEXT,
    source        TEXT,
    faq_id        TEXT,
    confidence    REAL,
    latency_ms    INTEGER,
    wa_message_id TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_wa      ON messages(wa_id, id);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);

CREATE TABLE IF NOT EXISTS processed_messages (
    wa_message_id TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS faq_items (
    id         TEXT PRIMARY KEY,
    topic      TEXT NOT NULL,
    keywords   TEXT NOT NULL,
    question   TEXT NOT NULL,
    answer     TEXT NOT NULL,
    priority   INTEGER NOT NULL DEFAULT 100,
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    wa_id      TEXT,
    event_type TEXT NOT NULL,
    payload    TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type, id);
"""


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Database:
    """Wrapper fino sobre sqlite3. Uma conexão por operação (thread-safe)."""

    def __init__(self, path: str | None = None):
        self.path = path or Config.DB_PATH
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def conn(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _init_schema(self) -> None:
        with self.conn() as c:
            c.executescript(SCHEMA)

    # ------------------------------------------------------------------ dedupe
    def mark_processed(self, wa_message_id: str) -> bool:
        """Retorna True se a mensagem é inédita, False se já foi processada."""
        if not wa_message_id:
            return True
        with self.conn() as c:
            try:
                c.execute(
                    "INSERT INTO processed_messages (wa_message_id, created_at) VALUES (?, ?)",
                    (wa_message_id, now_iso()),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def purge_processed(self, days: int = 7) -> int:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
        with self.conn() as c:
            cur = c.execute("DELETE FROM processed_messages WHERE created_at < ?", (cutoff,))
            return cur.rowcount

    # ------------------------------------------------------------ conversations
    def upsert_conversation(self, wa_id: str, name: str | None = None) -> None:
        ts = now_iso()
        with self.conn() as c:
            c.execute(
                """
                INSERT INTO conversations (wa_id, name, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(wa_id) DO UPDATE SET
                    name = COALESCE(excluded.name, conversations.name),
                    updated_at = excluded.updated_at
                """,
                (wa_id, name, ts, ts),
            )

    def get_conversation(self, wa_id: str) -> dict | None:
        with self.conn() as c:
            row = c.execute("SELECT * FROM conversations WHERE wa_id = ?", (wa_id,)).fetchone()
            return dict(row) if row else None

    def set_state(self, wa_id: str, state: str) -> None:
        ts = now_iso()
        with self.conn() as c:
            if state == "human":
                c.execute(
                    "UPDATE conversations SET state = ?, human_requested_at = ?, updated_at = ? WHERE wa_id = ?",
                    (state, ts, ts, wa_id),
                )
            else:
                c.execute(
                    "UPDATE conversations SET state = ?, human_requested_at = NULL, updated_at = ? WHERE wa_id = ?",
                    (state, ts, wa_id),
                )

    def touch_conversation(self, wa_id: str) -> None:
        with self.conn() as c:
            c.execute(
                "UPDATE conversations SET message_count = message_count + 1, last_message_at = ?, updated_at = ? WHERE wa_id = ?",
                (now_iso(), now_iso(), wa_id),
            )

    def list_conversations(self, limit: int = 50, offset: int = 0, state: str | None = None) -> list[dict]:
        query = "SELECT * FROM conversations"
        params: list = []
        if state:
            query += " WHERE state = ?"
            params.append(state)
        query += " ORDER BY COALESCE(last_message_at, created_at) DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self.conn() as c:
            return [dict(r) for r in c.execute(query, params).fetchall()]

    # ----------------------------------------------------------------- messages
    def save_message(
        self,
        wa_id: str,
        direction: str,
        body: str | None,
        msg_type: str = "text",
        source: str | None = None,
        faq_id: str | None = None,
        confidence: float | None = None,
        latency_ms: int | None = None,
        wa_message_id: str | None = None,
    ) -> int:
        with self.conn() as c:
            cur = c.execute(
                """
                INSERT INTO messages
                    (wa_id, direction, msg_type, body, source, faq_id, confidence, latency_ms, wa_message_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (wa_id, direction, msg_type, body, source, faq_id, confidence, latency_ms, wa_message_id, now_iso()),
            )
            return cur.lastrowid

    def get_history(self, wa_id: str, limit: int = 10) -> list[dict]:
        """Últimas N mensagens em ordem cronológica."""
        with self.conn() as c:
            rows = c.execute(
                "SELECT * FROM messages WHERE wa_id = ? ORDER BY id DESC LIMIT ?", (wa_id, limit)
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    # -------------------------------------------------------------------- stats
    def stats(self) -> dict:
        with self.conn() as c:
            total_messages = c.execute("SELECT COUNT(*) FROM messages WHERE direction = 'in'").fetchone()[0]
            total_out = c.execute("SELECT COUNT(*) FROM messages WHERE direction = 'out'").fetchone()[0]
            by_source = {
                row["source"] or "unknown": row["n"]
                for row in c.execute(
                    "SELECT source, COUNT(*) AS n FROM messages WHERE direction = 'out' GROUP BY source"
                ).fetchall()
            }
            conversations = c.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
            human_queue = c.execute("SELECT COUNT(*) FROM conversations WHERE state = 'human'").fetchone()[0]
            avg_latency = c.execute(
                "SELECT AVG(latency_ms) FROM messages WHERE latency_ms IS NOT NULL"
            ).fetchone()[0]
            last_24h = c.execute(
                "SELECT COUNT(*) FROM messages WHERE direction = 'in' AND created_at >= ?",
                ((datetime.now(UTC) - timedelta(hours=24)).isoformat(timespec="seconds"),),
            ).fetchone()[0]
            avg_conf = c.execute(
                "SELECT AVG(confidence) FROM messages WHERE confidence IS NOT NULL"
            ).fetchone()[0]

        automated = sum(v for k, v in by_source.items() if k in {"faq", "ai", "fallback", "system"})
        return {
            "total_conversations": conversations,
            "conversations_waiting_human": human_queue,
            "messages_in": total_messages,
            "messages_out": total_out,
            "messages_in_last_24h": last_24h,
            "responses_by_source": by_source,
            "automation_rate": round(automated / total_out, 4) if total_out else 0.0,
            "avg_response_latency_ms": round(avg_latency, 1) if avg_latency else None,
            "avg_faq_confidence": round(avg_conf, 4) if avg_conf else None,
        }

    def daily_stats(self, days: int = 7) -> list[dict]:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
        with self.conn() as c:
            rows = c.execute(
                """
                SELECT substr(created_at, 1, 10) AS day,
                       SUM(CASE WHEN direction = 'in'  THEN 1 ELSE 0 END) AS received,
                       SUM(CASE WHEN direction = 'out' THEN 1 ELSE 0 END) AS sent
                FROM messages WHERE created_at >= ?
                GROUP BY day ORDER BY day
                """,
                (cutoff,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------- faq items
    def faq_all(self, only_enabled: bool = True) -> list[dict]:
        query = "SELECT * FROM faq_items"
        if only_enabled:
            query += " WHERE enabled = 1"
        query += " ORDER BY priority ASC, id ASC"
        with self.conn() as c:
            rows = c.execute(query).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            try:
                item["keywords"] = json.loads(item["keywords"])
            except (TypeError, json.JSONDecodeError):
                item["keywords"] = []
            items.append(item)
        return items

    def faq_get(self, item_id: str) -> dict | None:
        with self.conn() as c:
            row = c.execute("SELECT * FROM faq_items WHERE id = ?", (item_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["keywords"] = json.loads(item["keywords"])
        return item

    def faq_upsert(
        self,
        item_id: str,
        topic: str,
        keywords: list[str],
        question: str,
        answer: str,
        priority: int = 100,
    ) -> str:
        ts = now_iso()
        with self.conn() as c:
            c.execute(
                """
                INSERT INTO faq_items (id, topic, keywords, question, answer, priority, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    topic = excluded.topic,
                    keywords = excluded.keywords,
                    question = excluded.question,
                    answer = excluded.answer,
                    priority = excluded.priority,
                    enabled = 1,
                    updated_at = excluded.updated_at
                """,
                (item_id, topic, json.dumps(keywords, ensure_ascii=False), question, answer, priority, ts, ts),
            )
        return item_id

    def faq_delete(self, item_id: str) -> bool:
        with self.conn() as c:
            cur = c.execute("DELETE FROM faq_items WHERE id = ?", (item_id,))
            return cur.rowcount > 0

    def faq_count(self) -> int:
        with self.conn() as c:
            return c.execute("SELECT COUNT(*) FROM faq_items").fetchone()[0]

    # ------------------------------------------------------------------- events
    def add_event(self, event_type: str, wa_id: str | None = None, payload: dict | None = None) -> int:
        with self.conn() as c:
            cur = c.execute(
                "INSERT INTO events (wa_id, event_type, payload, created_at) VALUES (?, ?, ?, ?)",
                (wa_id, event_type, json.dumps(payload or {}, ensure_ascii=False), now_iso()),
            )
            return cur.lastrowid

    def recent_events(self, limit: int = 50, event_type: str | None = None) -> list[dict]:
        query = "SELECT * FROM events"
        params: list = []
        if event_type:
            query += " WHERE event_type = ?"
            params.append(event_type)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self.conn() as c:
            rows = c.execute(query, params).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["payload"] = json.loads(item["payload"] or "{}")
            except json.JSONDecodeError:
                pass
            out.append(item)
        return out
