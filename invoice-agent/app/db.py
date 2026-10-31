from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


class IdempotencyConflict(Exception):
    pass


class TaskConflict(Exception):
    pass


class Database:
    def __init__(self) -> None:
        configured = os.getenv("DATABASE_PATH")
        self.path = Path(configured) if configured else Path("/tmp/invoice-agent.sqlite3")
        self.lock = threading.RLock()
        self._init()

    def conn(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, principal TEXT NOT NULL, state TEXT NOT NULL,
                    context_id TEXT NOT NULL, history_json TEXT NOT NULL,
                    artifacts_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS idempotency (
                    principal TEXT NOT NULL, message_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL, task_id TEXT NOT NULL,
                    PRIMARY KEY (principal, message_id)
                );
                CREATE TABLE IF NOT EXISTS decisions (
                    content_hash TEXT PRIMARY KEY, decision_json TEXT NOT NULL
                );
                """
            )

    def _task(self, row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        return {
            "id": row["id"],
            "contextId": row["context_id"],
            "state": row["state"],
            "history": json.loads(row["history_json"]),
            "artifacts": json.loads(row["artifacts_json"]),
        }

    def idempotent(self, principal: str, message_id: str, content_hash: str) -> dict | None:
        with self.conn() as c:
            row = c.execute(
                "SELECT content_hash, task_id FROM idempotency WHERE principal=? AND message_id=?",
                (principal, message_id),
            ).fetchone()
            if not row:
                return None
            if row["content_hash"] != content_hash:
                raise IdempotencyConflict
            return self.get(row["task_id"], principal)

    def create(self, task: dict, principal: str, message_id: str, content_hash: str) -> dict:
        with self.lock, self.conn() as c:
            try:
                c.execute(
                    "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        task["id"], principal, task["state"], task["contextId"],
                        json.dumps(task["history"]), json.dumps(task["artifacts"]),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                c.execute(
                    "INSERT INTO idempotency VALUES (?, ?, ?, ?)",
                    (principal, message_id, content_hash, task["id"]),
                )
            except sqlite3.IntegrityError:
                existing = self.idempotent(principal, message_id, content_hash)
                if existing:
                    return existing
                raise IdempotencyConflict
        return task

    def get(self, task_id: str, principal: str) -> dict | None:
        with self.conn() as c:
            return self._task(c.execute(
                "SELECT * FROM tasks WHERE id=? AND principal=?", (task_id, principal)
            ).fetchone())

    def list(self, principal: str) -> list[dict]:
        with self.conn() as c:
            return [self._task(row) for row in c.execute(
                "SELECT * FROM tasks WHERE principal=? ORDER BY created_at DESC", (principal,)
            ).fetchall()]

    def complete(
        self,
        task_id: str,
        principal: str,
        history: list,
        artifacts: list,
        message_id: str,
        content_hash: str,
    ) -> dict:
        with self.lock, self.conn() as c:
            row = c.execute(
                "SELECT state FROM tasks WHERE id=? AND principal=?", (task_id, principal)
            ).fetchone()
            if not row:
                raise KeyError
            if row["state"] in ("TASK_STATE_COMPLETED", "TASK_STATE_CANCELED"):
                raise TaskConflict
            c.execute(
                "UPDATE tasks SET state=?, history_json=?, artifacts_json=? "
                "WHERE id=? AND principal=? AND state='TASK_STATE_INPUT_REQUIRED'",
                ("TASK_STATE_COMPLETED", json.dumps(history), json.dumps(artifacts), task_id, principal),
            )
            if c.execute("SELECT changes()").fetchone()[0] != 1:
                raise TaskConflict
            c.execute(
                "INSERT INTO idempotency (principal, message_id, content_hash, task_id) VALUES (?, ?, ?, ?)",
                (principal, message_id, content_hash, task_id),
            )
        return self.get(task_id, principal)

    def cancel(self, task_id: str, principal: str) -> dict:
        with self.lock, self.conn() as c:
            row = c.execute(
                "SELECT state FROM tasks WHERE id=? AND principal=?", (task_id, principal)
            ).fetchone()
            if not row:
                raise KeyError
            if row["state"] in ("TASK_STATE_COMPLETED", "TASK_STATE_CANCELED"):
                raise TaskConflict
            c.execute(
                "UPDATE tasks SET state=? WHERE id=? AND principal=? AND state='TASK_STATE_INPUT_REQUIRED'",
                ("TASK_STATE_CANCELED", task_id, principal),
            )
            if c.execute("SELECT changes()").fetchone()[0] != 1:
                raise TaskConflict
        return self.get(task_id, principal)

    def cached(self, content_hash: str) -> dict | None:
        with self.conn() as c:
            row = c.execute("SELECT decision_json FROM decisions WHERE content_hash=?", (content_hash,)).fetchone()
            return json.loads(row["decision_json"]) if row else None

    def cache(self, content_hash: str, decision: dict) -> None:
        with self.conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO decisions VALUES (?, ?)",
                (content_hash, json.dumps(decision)),
            )


db = Database()
