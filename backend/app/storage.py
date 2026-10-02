from __future__ import annotations
import asyncio, hashlib, json, secrets, sqlite3
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
            CREATE TABLE IF NOT EXISTS identities (id TEXT PRIMARY KEY, student_id TEXT UNIQUE, display_name TEXT,
              kind TEXT NOT NULL, created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, identity_id TEXT NOT NULL,
              expires TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, title TEXT NOT NULL,
              created TEXT NOT NULL, updated TEXT NOT NULL);
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

    def create_identity(self, kind: str="guest", student_id: str|None=None, display_name: str|None=None) -> dict[str,Any]:
        identity_id=f"{kind}_{secrets.token_hex(12)}" if not student_id else f"student_{student_id}"
        now=datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("INSERT INTO identities VALUES(?,?,?,?,?) ON CONFLICT(student_id) DO UPDATE SET display_name=excluded.display_name",
                       (identity_id,student_id,display_name or ("游客" if kind=="guest" else student_id),kind,now))
            if student_id:
                identity_id=db.execute("SELECT id FROM identities WHERE student_id=?",(student_id,)).fetchone()[0]
            row=db.execute("SELECT id,student_id,display_name,kind FROM identities WHERE id=?",(identity_id,)).fetchone()
        return dict(zip(("id","student_id","display_name","kind"),row))

    def create_session(self, identity_id: str, days: int=7) -> str:
        from datetime import timedelta
        token=secrets.token_urlsafe(32); digest=hashlib.sha256(token.encode()).hexdigest()
        expires=(datetime.now(timezone.utc)+timedelta(days=days)).isoformat()
        with self._connect() as db: db.execute("INSERT INTO sessions VALUES(?,?,?)",(digest,identity_id,expires))
        return token

    def session_identity(self, token: str|None) -> dict[str,Any]|None:
        if not token: return None
        digest=hashlib.sha256(token.encode()).hexdigest(); now=datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            row=db.execute("SELECT i.id,i.student_id,i.display_name,i.kind FROM sessions s JOIN identities i ON i.id=s.identity_id WHERE s.token_hash=? AND s.expires>?",(digest,now)).fetchone()
        return dict(zip(("id","student_id","display_name","kind"),row)) if row else None

    def delete_session(self, token: str|None):
        if not token: return
        with self._connect() as db: db.execute("DELETE FROM sessions WHERE token_hash=?",(hashlib.sha256(token.encode()).hexdigest(),))

    def create_conversation(self,owner_id: str,title: str="新会话") -> dict[str,Any]:
        conversation_id=secrets.token_hex(16); now=datetime.now(timezone.utc).isoformat()
        with self._connect() as db: db.execute("INSERT INTO conversations VALUES(?,?,?,?,?)",(conversation_id,owner_id,title,now,now))
        return {"conversation_id":conversation_id,"title":title,"updated":now,"task_id":None,"status":"empty"}

    def conversation_owner(self,conversation_id: str) -> str|None:
        with self._connect() as db: row=db.execute("SELECT owner_id FROM conversations WHERE id=?",(conversation_id,)).fetchone()
        return row[0] if row else None

    def touch_conversation(self,conversation_id: str,owner_id: str,title: str):
        now=datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("INSERT INTO conversations VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=CASE WHEN conversations.title='新会话' THEN excluded.title ELSE conversations.title END,updated=excluded.updated",
                       (conversation_id,owner_id,title[:60] or "新会话",now,now))

    def rename_conversation(self,conversation_id: str,owner_id: str,title: str) -> bool:
        with self._connect() as db:
            result=db.execute("UPDATE conversations SET title=?,updated=? WHERE id=? AND owner_id=?",
                              (title.strip(),datetime.now(timezone.utc).isoformat(),conversation_id,owner_id))
        return result.rowcount>0

    def delete_conversation(self,conversation_id: str,owner_id: str) -> bool:
        if self.conversation_owner(conversation_id)!=owner_id: return False
        with self._connect() as db:
            rows=db.execute("SELECT id,state FROM tasks").fetchall()
            task_ids=[task_id for task_id,body in rows if json.loads(body).get("conversation_id")==conversation_id and json.loads(body).get("owner_id")==owner_id]
            for task_id in task_ids:
                db.execute("DELETE FROM events WHERE task_id=?",(task_id,)); db.execute("DELETE FROM tasks WHERE id=?",(task_id,))
            db.execute("DELETE FROM conversations WHERE id=? AND owner_id=?",(conversation_id,owner_id))
        return True

    def list_conversations(self, owner_id: str) -> list[dict[str,Any]]:
        with self._connect() as db:
            rows=db.execute("SELECT state,updated FROM tasks ORDER BY updated DESC").fetchall()
            conversation_rows=db.execute("SELECT id,title,updated FROM conversations WHERE owner_id=? ORDER BY updated DESC",(owner_id,)).fetchall()
        latest: dict[str,dict[str,Any]]={cid:{"conversation_id":cid,"task_id":None,"title":title,"status":"empty","updated":updated} for cid,title,updated in conversation_rows}
        for body,updated in rows:
            state=json.loads(body)
            if state.get("owner_id")!=owner_id: continue
            cid=state.get("conversation_id") or state.get("task_id")
            existing=latest.get(cid)
            if not existing or not existing.get("task_id"):
                latest[cid]={"conversation_id":cid,"task_id":state.get("task_id"),"title":existing.get("title") if existing else state.get("user_query","新会话")[:60],"status":state.get("status"),"updated":updated}
        return sorted(latest.values(),key=lambda item:item["updated"],reverse=True)

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
