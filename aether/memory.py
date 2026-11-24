from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import ensure_dirs, get, resolve

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);

CREATE TABLE IF NOT EXISTS memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL DEFAULT 'note',
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  source TEXT,
  tags TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
  title, content, tags, memory_id UNINDEXED, tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  path TEXT NOT NULL UNIQUE,
  mtime REAL NOT NULL,
  size INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  indexed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  ord INTEGER NOT NULL,
  heading TEXT,
  text TEXT NOT NULL,
  path TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  text, heading, path, chunk_id UNINDEXED, doc_id UNINDEXED,
  tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  priority TEXT NOT NULL DEFAULT 'normal',
  due TEXT,
  notes TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS emails (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  uid TEXT UNIQUE,
  folder TEXT,
  from_addr TEXT,
  to_addr TEXT,
  subject TEXT,
  date TEXT,
  body TEXT,
  path TEXT,
  thread_id TEXT,
  imported_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS emails_fts USING fts5(
  subject, from_addr, to_addr, body, email_id UNINDEXED, tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS calendar (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  uid TEXT UNIQUE,
  summary TEXT NOT NULL,
  dtstart TEXT,
  dtend TEXT,
  description TEXT,
  location TEXT,
  source TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drafts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  subject TEXT,
  recipients TEXT,
  body TEXT NOT NULL,
  path TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS learned_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scope TEXT NOT NULL DEFAULT 'global',
  rule_key TEXT NOT NULL UNIQUE,
  rule TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'chat',
  confidence REAL NOT NULL DEFAULT 0.5,
  hits INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_seen REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_learned_rules_scope ON learned_rules(scope, confidence);

CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT,
  signal TEXT NOT NULL,
  detail TEXT,
  created_at TEXT NOT NULL
);
"""

_TOKEN = re.compile(r"[\w\u0080-\uffff]{2,}", re.UNICODE)
_STOP = {"the", "and", "for", "with", "this", "that", "from", "what", "how", "are", "you", "your", "can", "please", "about", "into", "when", "where", "which", "will", "would", "should", "there", "their", "have", "has", "was", "were", "not", "but", "all", "any", "use", "using"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _terms(query: str, limit: int = 24) -> list[str]:
    seen: list[str] = []
    for m in _TOKEN.findall(query.lower()):
        if m in _STOP or len(m) < 2:
            continue
        if m not in seen:
            seen.append(m)
    return seen[:limit]


class Memory:
    def __init__(self, db_path: str | Path | None = None):
        ensure_dirs()
        self.path = Path(db_path) if db_path else resolve(get("memory.db", "data/aether.db"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    # ---------- chat history ----------
    def add_message(self, session_id: str, role: str, content: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO messages(session_id, role, content, ts) VALUES (?,?,?,?)",
                (session_id, role, content, time.time()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def recent_messages(self, session_id: str, limit: int = 12) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content, ts FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def sessions(self, limit: int = 30) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT session_id, MIN(ts) first_ts, MAX(ts) last_ts, COUNT(*) n,
                          (SELECT content FROM messages m2 WHERE m2.session_id=m.session_id AND role='user' ORDER BY id DESC LIMIT 1) preview
                   FROM messages m GROUP BY session_id ORDER BY last_ts DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------- long-term memories ----------
    def add_memory(self, title: str, content: str, kind: str = "note", source: str = "",
                   tags: str = "") -> int:
        now = _now()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO memories(kind,title,content,source,tags,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (kind, title, content, source, tags, now, now),
            )
            mid = int(cur.lastrowid)
            self._conn.execute(
                "INSERT INTO memories_fts(title,content,tags,memory_id) VALUES (?,?,?,?)",
                (title, content, tags, mid),
            )
            self._conn.commit()
            return mid

    def search_memories(self, query: str, limit: int = 8) -> list[dict]:
        terms = _terms(query)
        if not terms:
            return []
        fts_q = " OR ".join(f'"{t}"*' for t in terms)
        with self._lock:
            try:
                rows = self._conn.execute(
                    """SELECT m.*, bm25(memories_fts) score
                       FROM memories_fts JOIN memories m ON m.id=memories_fts.memory_id
                       WHERE memories_fts MATCH ? ORDER BY score LIMIT ?""",
                    (fts_q, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            if not rows:
                like = "%" + terms[0] + "%"
                rows = self._conn.execute(
                    "SELECT *, 0.0 score FROM memories WHERE title LIKE ? OR content LIKE ? OR tags LIKE ? ORDER BY id DESC LIMIT ?",
                    (like, like, like, limit),
                ).fetchall()
        return [dict(r) for r in rows]

    def list_memories(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM memories ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ---------- adaptive learning ----------
    def upsert_learned_rule(self, rule: str, scope: str = "global", source: str = "chat",
                            confidence: float = 0.5) -> tuple[int, bool]:
        key = re.sub(r"\W+", " ", rule.strip().lower()).strip()[:240]
        now = _now()
        with self._lock:
            row = self._conn.execute("SELECT id, confidence FROM learned_rules WHERE rule_key=?", (key,)).fetchone()
            if row:
                new_conf = min(1.0, float(row["confidence"]) + 0.15)
                self._conn.execute(
                    "UPDATE learned_rules SET rule=?, scope=?, source=?, confidence=?, hits=hits+1, updated_at=?, last_seen=? WHERE id=?",
                    (rule.strip(), scope, source, new_conf, now, time.time(), row["id"]),
                )
                self._conn.commit()
                return int(row["id"]), False
            cur = self._conn.execute(
                """INSERT INTO learned_rules(scope,rule_key,rule,source,confidence,hits,created_at,updated_at,last_seen)
                   VALUES (?,?,?,?,?,1,?,?,?)""",
                (scope, key, rule.strip(), source, confidence, now, now, time.time()),
            )
            self._conn.commit()
            return int(cur.lastrowid), True

    def list_learned_rules(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM learned_rules ORDER BY confidence DESC, last_seen DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def top_learned_rules(self, module: str = "chat", limit: int = 12,
                          min_confidence: float = 0.35) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM learned_rules
                   WHERE confidence >= ? AND (scope='global' OR scope=?)
                   ORDER BY confidence DESC, last_seen DESC LIMIT ?""",
                (min_confidence, module, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def adjust_learned_rule(self, rule_id: int, delta: float) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT confidence FROM learned_rules WHERE id=?", (rule_id,)).fetchone()
            if not row:
                return False
            value = max(0.0, min(1.0, float(row["confidence"]) + delta))
            self._conn.execute(
                "UPDATE learned_rules SET confidence=?, updated_at=? WHERE id=?", (value, _now(), rule_id)
            )
            self._conn.commit()
            return True

    def delete_learned_rule(self, rule_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM learned_rules WHERE id=?", (rule_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def add_feedback(self, session_id: str, signal: str, detail: str = "") -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO feedback(session_id,signal,detail,created_at) VALUES (?,?,?,?)",
                (session_id, signal, detail[:1000], _now()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def recent_feedback(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM feedback ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------- documents / retrieval ----------
    def upsert_document(self, path: str, mtime: float, size: int, sha256: str,
                        chunks: list[dict]) -> int:
        now = _now()
        with self._lock:
            old = self._conn.execute("SELECT id FROM documents WHERE path=?", (path,)).fetchone()
            if old:
                doc_id = int(old["id"])
                self._conn.execute("DELETE FROM chunks_fts WHERE doc_id=?", (doc_id,))
                self._conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
                self._conn.execute(
                    "UPDATE documents SET mtime=?,size=?,sha256=?,indexed_at=? WHERE id=?",
                    (mtime, size, sha256, now, doc_id),
                )
            else:
                cur = self._conn.execute(
                    "INSERT INTO documents(path,mtime,size,sha256,indexed_at) VALUES (?,?,?,?,?)",
                    (path, mtime, size, sha256, now),
                )
                doc_id = int(cur.lastrowid)
            for i, ch in enumerate(chunks):
                cur = self._conn.execute(
                    "INSERT INTO chunks(doc_id,ord,heading,text,path) VALUES (?,?,?,?,?)",
                    (doc_id, i, ch.get("heading", ""), ch["text"], path),
                )
                cid = int(cur.lastrowid)
                self._conn.execute(
                    "INSERT INTO chunks_fts(text,heading,path,chunk_id,doc_id) VALUES (?,?,?,?,?)",
                    (ch["text"], ch.get("heading", ""), path, cid, doc_id),
                )
            self._conn.commit()
            return doc_id

    def remove_missing_documents(self, existing: set[str]) -> int:
        removed = 0
        with self._lock:
            rows = self._conn.execute("SELECT id, path FROM documents").fetchall()
            for r in rows:
                if r["path"] not in existing:
                    self._conn.execute("DELETE FROM chunks_fts WHERE doc_id=?", (r["id"],))
                    self._conn.execute("DELETE FROM documents WHERE id=?", (r["id"],))
                    removed += 1
            self._conn.commit()
        return removed

    def document_state(self) -> dict[str, dict]:
        with self._lock:
            rows = self._conn.execute("SELECT path,mtime,size,sha256 FROM documents").fetchall()
        return {r["path"]: dict(r) for r in rows}

    def search_chunks(self, query: str, limit: int = 6) -> list[dict]:
        terms = _terms(query)
        if not terms:
            return []
        fts_q = " OR ".join(f'"{t}"*' for t in terms)
        with self._lock:
            try:
                rows = self._conn.execute(
                    """SELECT c.id, c.doc_id, c.ord, c.heading, c.text, c.path,
                              bm25(chunks_fts) score
                       FROM chunks_fts JOIN chunks c ON c.id=chunks_fts.chunk_id
                       WHERE chunks_fts MATCH ? ORDER BY score LIMIT ?""",
                    (fts_q, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            if not rows:
                like = "%" + terms[0] + "%"
                rows = self._conn.execute(
                    "SELECT id,doc_id,ord,heading,text,path,0.0 score FROM chunks WHERE text LIKE ? OR heading LIKE ? OR path LIKE ? LIMIT ?",
                    (like, like, like, limit),
                ).fetchall()
        return [dict(r) for r in rows]

    def index_stats(self) -> dict:
        with self._lock:
            docs = self._conn.execute("SELECT COUNT(*) n, COALESCE(SUM(size),0) b FROM documents").fetchone()
            chunks = self._conn.execute("SELECT COUNT(*) n FROM chunks").fetchone()
            mems = self._conn.execute("SELECT COUNT(*) n FROM memories").fetchone()
        return {"documents": docs["n"], "bytes": docs["b"], "chunks": chunks["n"], "memories": mems["n"]}

    # ---------- tasks ----------
    def add_task(self, title: str, priority: str = "normal", due: str = "", notes: str = "") -> int:
        now = _now()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO tasks(title,status,priority,due,notes,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (title, "open", priority, due, notes, now, now),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def update_task(self, task_id: int, status: str | None = None, priority: str | None = None,
                    due: str | None = None, notes: str | None = None) -> bool:
        fields, vals = [], []
        for key, val in (("status", status), ("priority", priority), ("due", due), ("notes", notes)):
            if val is not None:
                fields.append(f"{key}=?")
                vals.append(val)
        if not fields:
            return False
        fields.append("updated_at=?")
        vals.extend([_now(), task_id])
        with self._lock:
            cur = self._conn.execute(f"UPDATE tasks SET {','.join(fields)} WHERE id=?", vals)
            self._conn.commit()
            return cur.rowcount > 0

    def list_tasks(self, status: str | None = None, limit: int = 100) -> list[dict]:
        with self._lock:
            if status:
                rows = self._conn.execute("SELECT * FROM tasks WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit)).fetchall()
            else:
                rows = self._conn.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ---------- emails (local import, never send/delete) ----------
    def add_email(self, uid: str, folder: str, from_addr: str, to_addr: str, subject: str,
                  date: str, body: str, path: str = "", thread_id: str = "") -> int:
        now = _now()
        with self._lock:
            old = self._conn.execute("SELECT id FROM emails WHERE uid=?", (uid,)).fetchone()
            if old:
                eid = int(old["id"])
                self._conn.execute(
                    "UPDATE emails SET folder=?,from_addr=?,to_addr=?,subject=?,date=?,body=?,path=?,thread_id=?,imported_at=? WHERE id=?",
                    (folder, from_addr, to_addr, subject, date, body, path, thread_id, now, eid),
                )
                self._conn.execute("DELETE FROM emails_fts WHERE email_id=?", (eid,))
            else:
                cur = self._conn.execute(
                    "INSERT INTO emails(uid,folder,from_addr,to_addr,subject,date,body,path,thread_id,imported_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (uid, folder, from_addr, to_addr, subject, date, body, path, thread_id, now),
                )
                eid = int(cur.lastrowid)
            self._conn.execute(
                "INSERT INTO emails_fts(subject,from_addr,to_addr,body,email_id) VALUES (?,?,?,?,?)",
                (subject, from_addr, to_addr, body, eid),
            )
            self._conn.commit()
            return eid

    def search_emails(self, query: str, limit: int = 20) -> list[dict]:
        terms = _terms(query)
        if not terms:
            with self._lock:
                rows = self._conn.execute("SELECT * FROM emails ORDER BY date DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]
        fts_q = " OR ".join(f'"{t}"*' for t in terms)
        with self._lock:
            try:
                rows = self._conn.execute(
                    """SELECT e.*, bm25(emails_fts) score FROM emails_fts
                       JOIN emails e ON e.id=emails_fts.email_id
                       WHERE emails_fts MATCH ? ORDER BY score LIMIT ?""",
                    (fts_q, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            if not rows:
                like = "%" + terms[0] + "%"
                rows = self._conn.execute(
                    "SELECT *,0.0 score FROM emails WHERE subject LIKE ? OR body LIKE ? OR from_addr LIKE ? ORDER BY date DESC LIMIT ?",
                    (like, like, like, limit),
                ).fetchall()
        return [dict(r) for r in rows]

    def email_thread(self, thread_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM emails WHERE thread_id=? ORDER BY date", (thread_id,)).fetchall()
        return [dict(r) for r in rows]

    # ---------- calendar ----------
    def add_event(self, uid: str, summary: str, dtstart: str, dtend: str = "",
                  description: str = "", location: str = "", source: str = "local") -> int:
        with self._lock:
            old = self._conn.execute("SELECT id FROM calendar WHERE uid=?", (uid,)).fetchone()
            if old:
                self._conn.execute(
                    "UPDATE calendar SET summary=?,dtstart=?,dtend=?,description=?,location=?,source=? WHERE id=?",
                    (summary, dtstart, dtend, description, location, source, old["id"]),
                )
                self._conn.commit()
                return int(old["id"])
            cur = self._conn.execute(
                "INSERT INTO calendar(uid,summary,dtstart,dtend,description,location,source,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (uid, summary, dtstart, dtend, description, location, source, _now()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_events(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM calendar ORDER BY dtstart LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ---------- drafts ----------
    def add_draft(self, kind: str, subject: str, recipients: str, body: str, path: str = "") -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO drafts(kind,subject,recipients,body,path,created_at) VALUES (?,?,?,?,?,?)",
                (kind, subject, recipients, body, path, _now()),
            )
            self._conn.commit()
            return int(cur.lastrowid)


_default: Memory | None = None
_default_lock = threading.Lock()


def default() -> Memory:
    global _default
    with _default_lock:
        if _default is None:
            _default = Memory()
        return _default
