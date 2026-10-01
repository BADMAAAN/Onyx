from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import re
from threading import local, Lock
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryDatabase:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._local = local()
        self._connections = []
        self._connection_lock = Lock()
        self._migrate()

    @property
    def connection(self):
        if not hasattr(self._local, 'connection'):
            conn = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._local.connection = conn
            with self._connection_lock:
                self._connections.append(conn)
        return self._local.connection

    def _migrate(self) -> None:
        self.connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user','assistant','system','tool')),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT NOT NULL UNIQUE,
                value TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'preference',
                confidence REAL NOT NULL DEFAULT 1.0,
                source TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tool_name TEXT NOT NULL,
                arguments_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                success INTEGER NOT NULL,
                permission_level TEXT NOT NULL,
                duration_ms INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS skills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL,
                steps_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                success_count INTEGER NOT NULL DEFAULT 0,
                failure_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_conversations_session ON conversations(session_id, id);
            CREATE INDEX IF NOT EXISTS idx_actions_tool ON actions(tool_name, id);
            """
        )
        self.connection.commit()
        columns = {row[1] for row in self.connection.execute('PRAGMA table_info(memories)')}
        for name, declaration in [('usage_count', 'INTEGER NOT NULL DEFAULT 0'), ('last_confirmed', 'TEXT')]:
            if name not in columns:
                self.connection.execute(f'ALTER TABLE memories ADD COLUMN {name} {declaration}')
        self.connection.executescript('''
            CREATE TABLE IF NOT EXISTS corrections (
                id INTEGER PRIMARY KEY, application TEXT, original_command TEXT,
                wrong_target TEXT, correct_target TEXT, ui_metadata TEXT,
                confidence REAL NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
        ''')
        self.fts = True
        try:
            self.connection.executescript('''
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(key, value, content='memories', content_rowid='id');
                CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                  INSERT INTO memories_fts(rowid,key,value) VALUES(new.id,new.key,new.value); END;
                CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                  INSERT INTO memories_fts(memories_fts,rowid,key,value) VALUES('delete',old.id,old.key,old.value); END;
                CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                  INSERT INTO memories_fts(memories_fts,rowid,key,value) VALUES('delete',old.id,old.key,old.value);
                  INSERT INTO memories_fts(rowid,key,value) VALUES(new.id,new.key,new.value); END;
                INSERT INTO memories_fts(memories_fts) VALUES('rebuild');
            ''')
        except sqlite3.OperationalError:
            self.fts = False
        self.connection.commit()

    def add_conversation(self, session_id: str, role: str, content: str) -> int:
        cursor = self.connection.execute(
            "INSERT INTO conversations(session_id, role, content, created_at) VALUES(?,?,?,?)",
            (session_id, role, content, utc_now()),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def recent_conversation(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT role, content, created_at FROM conversations WHERE session_id=? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def remember(self, key: str, value: str, category: str = "preference", source: str = "user") -> None:
        now = utc_now()
        self.connection.execute(
            """INSERT INTO memories(key,value,category,confidence,source,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, category=excluded.category,
               confidence=excluded.confidence, source=excluded.source, updated_at=excluded.updated_at""",
            (key, value, category, 1.0, source, now, now),
        )
        self.connection.execute('UPDATE memories SET last_confirmed=? WHERE key=?',(now,key))
        self.connection.commit()

    def recall(self, query: str = "", limit: int = 10) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 50))
        terms = re.findall(r'\w+', query, flags=re.UNICODE)[:12]
        rows = []
        if query and terms and self.fts:
            match = ' OR '.join('"' + term + '"*' for term in terms)
            rows = self.connection.execute('''SELECT m.*, bm25(memories_fts) AS relevance
                FROM memories_fts JOIN memories m ON m.id=memories_fts.rowid
                WHERE memories_fts MATCH ? ORDER BY
                (relevance - .3*confidence - .01*min(usage_count,50)
                 - .1/(1+max(0,julianday('now')-julianday(updated_at)))) ASC LIMIT ?''',
                (match, limit)).fetchall()
        if not rows:
            if query:
                words = terms or [query]
                clauses = ' OR '.join('(key LIKE ? OR value LIKE ?)' for _ in words)
                args = [f'%{word}%' for word in words for _ in range(2)]
                rows = self.connection.execute(f'SELECT * FROM memories WHERE {clauses} ORDER BY confidence DESC, updated_at DESC, usage_count DESC LIMIT ?', (*args, limit)).fetchall()
            else:
                rows = self.connection.execute('SELECT * FROM memories ORDER BY updated_at DESC LIMIT ?', (limit,)).fetchall()
        self.connection.executemany('UPDATE memories SET usage_count=usage_count+1 WHERE id=?', [(r['id'],) for r in rows])
        self.connection.commit()
        return [dict(row) for row in rows]

    def get(self, key: str) -> dict[str, Any] | None:
        """Exact operational preference lookup without broad history retrieval."""
        row = self.connection.execute('SELECT * FROM memories WHERE key=?', (key,)).fetchone()
        return dict(row) if row else None

    def add_correction(self, application, original_command, wrong_target, correct_target, ui_metadata):
        cursor = self.connection.execute('''INSERT INTO corrections(application,original_command,wrong_target,correct_target,ui_metadata,created_at)
            VALUES(?,?,?,?,?,?)''', (application, original_command, wrong_target, correct_target,
            json.dumps(ui_metadata, ensure_ascii=False), utc_now()))
        self.connection.commit()
        return int(cursor.lastrowid)

    def relevant_corrections(self, application, query, limit=5):
        terms = re.findall(r'\w+', query)[:12]
        if not application or not terms:
            return []
        clauses = ' OR '.join('original_command LIKE ?' for _ in terms)
        rows = self.connection.execute(f'SELECT * FROM corrections WHERE application=? AND ({clauses}) ORDER BY id DESC LIMIT ?',
            (application, *[f'%{w}%' for w in terms], min(limit, 10))).fetchall()
        return [dict(r) for r in rows]

    def forget(self, key: str) -> bool:
        cursor = self.connection.execute("DELETE FROM memories WHERE key=?", (key,))
        self.connection.commit()
        return cursor.rowcount > 0

    def record_action(self, tool_name: str, arguments: Any, result: Any, success: bool, permission: str, duration_ms: int) -> None:
        self.connection.execute(
            "INSERT INTO actions(tool_name,arguments_json,result_json,success,permission_level,duration_ms,created_at) VALUES(?,?,?,?,?,?,?)",
            (tool_name, json.dumps(arguments, ensure_ascii=False), json.dumps(result, ensure_ascii=False, default=str), int(success), permission, duration_ms, utc_now()),
        )
        self.connection.commit()

    def recent_actions(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT tool_name,success,permission_level,duration_ms,created_at FROM actions ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def save_skill(self, name: str, description: str, steps: list[dict[str, Any]], status: str = "pending") -> None:
        now = utc_now()
        self.connection.execute(
            """INSERT INTO skills(name,description,steps_json,status,created_at,updated_at)
               VALUES(?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET description=excluded.description,
               steps_json=excluded.steps_json,status=excluded.status,updated_at=excluded.updated_at""",
            (name, description, json.dumps(steps, ensure_ascii=False), status, now, now),
        )
        self.connection.commit()

    def counts(self) -> dict[str, int]:
        return {table: self.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in ("conversations", "memories", "actions", "skills")}

    def close(self) -> None:
        with self._connection_lock:
            for conn in self._connections:
                conn.close()
            self._connections.clear()
