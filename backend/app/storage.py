from __future__ import annotations
import asyncio, json, sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from .config import settings

class Store:
    def __init__(self, path: Path | None = None):
        self.path = path or settings.database
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        with self._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, state TEXT NOT NULL, updated TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (task_id TEXT, sequence INTEGER, body TEXT NOT NULL,
              PRIMARY KEY(task_id, sequence));
            """)

    def _connect(self):
        return sqlite3.connect(self.path)

    def save(self, task_id: str, state: dict[str, Any]):
        body = json.dumps(state, ensure_ascii=False, default=str)
        with self._connect() as db:
            db.execute("INSERT INTO tasks VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state, updated=excluded.updated",
                       (task_id, body, datetime.now(timezone.utc).isoformat()))

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT state FROM tasks WHERE id=?", (task_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def events(self, task_id: str, after: int = 0) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT body FROM events WHERE task_id=? AND sequence>? ORDER BY sequence", (task_id, after)).fetchall()
        return [json.loads(r[0]) for r in rows]

    async def emit(self, task_id: str, event_type: str, node: str | None, title: str, summary: str,
                   payload: dict[str, Any] | None = None, duration_ms: float | None = None):
        with self._connect() as db:
            seq = db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM events WHERE task_id=?", (task_id,)).fetchone()[0]
            event = {"task_id": task_id, "sequence": seq, "timestamp": datetime.now(timezone.utc).isoformat(),
                     "event_type": event_type, "node": node, "title": title, "summary": summary,
                     "payload": payload or {}, "duration_ms": duration_ms}
            db.execute("INSERT INTO events VALUES(?,?,?)", (task_id, seq, json.dumps(event, ensure_ascii=False, default=str)))
        for queue in self._subscribers.get(task_id, []):
            await queue.put(event)
        return event

    def subscribe(self, task_id: str):
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(task_id, []).append(queue)
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue):
        if queue in self._subscribers.get(task_id, []): self._subscribers[task_id].remove(queue)

store = Store()
