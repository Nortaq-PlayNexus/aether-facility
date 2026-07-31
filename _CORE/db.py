"""
AETHER FACILITY - Core Database Layer
SQLite-backed master index. All items (media, documents, sources,
classification events) are tracked here. SHA-256 fingerprints prevent
duplicate intake and verify integrity.
"""

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Database:
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'web',
        url TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT 'general',
        priority INTEGER NOT NULL DEFAULT 5,
        enabled INTEGER NOT NULL DEFAULT 1,
        last_poll TEXT,
        last_status TEXT,
        added_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sha256 TEXT UNIQUE,
        title TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'document',
        status TEXT NOT NULL DEFAULT 'intake',
        category TEXT,
        source_id INTEGER,
        source_url TEXT,
        origin TEXT,
        recorded_at TEXT,
        file_path TEXT,
        file_size INTEGER,
        ext TEXT,
        notes TEXT,
        processed_at TEXT,
        verified INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id INTEGER,
        action TEXT NOT NULL,
        detail TEXT,
        created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
    CREATE INDEX IF NOT EXISTS idx_items_kind ON items(kind);
    CREATE INDEX IF NOT EXISTS idx_items_sha ON items(sha256);
    """

    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(self.SCHEMA)
        self.conn.commit()

    def log(self, action, item_id=None, detail=None):
        self.conn.execute(
            "INSERT INTO events (item_id, action, detail, created_at) VALUES (?,?,?,?)",
            (item_id, action, detail, utcnow()),
        )
        self.conn.commit()

    # ---- sources ----
    def upsert_source(self, name, url, kind="web", category="general", priority=5, enabled=1):
        cur = self.conn.execute(
            "SELECT id FROM sources WHERE url = ?", (url,)
        )
        row = cur.fetchone()
        if row:
            self.conn.execute(
                "UPDATE sources SET name=?, kind=?, category=?, priority=?, enabled=? WHERE id=?",
                (name, kind, category, priority, enabled, row["id"]),
            )
            sid = row["id"]
        else:
            cur = self.conn.execute(
                "INSERT INTO sources (name, kind, url, category, priority, enabled, added_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (name, kind, url, category, priority, enabled, utcnow()),
            )
            sid = cur.lastrowid
        self.conn.commit()
        return sid

    def list_sources(self, enabled_only=True):
        q = "SELECT * FROM sources"
        if enabled_only:
            q += " WHERE enabled = 1"
        q += " ORDER BY priority, name"
        return [dict(r) for r in self.conn.execute(q)]

    def touch_source(self, sid, status):
        self.conn.execute(
            "UPDATE sources SET last_poll=?, last_status=? WHERE id=?",
            (utcnow(), status, sid),
        )
        self.conn.commit()

    # ---- items ----
    def item_by_sha(self, sha256):
        row = self.conn.execute(
            "SELECT * FROM items WHERE sha256 = ?", (sha256,)
        ).fetchone()
        return dict(row) if row else None

    def item_by_path(self, file_path):
        row = self.conn.execute(
            "SELECT * FROM items WHERE file_path = ?", (str(file_path),)
        ).fetchone()
        return dict(row) if row else None

    def item_by_url(self, source_url):
        row = self.conn.execute(
            "SELECT * FROM items WHERE source_url = ? ORDER BY id LIMIT 1", (source_url,)
        ).fetchone()
        return dict(row) if row else None

    def upsert_item(self, sha256, title, kind, status, category=None,
                    source_id=None, source_url=None, origin=None,
                    recorded_at=None, file_path=None, file_size=None,
                    ext=None, notes=None, verified=0):
        existing = self.item_by_sha(sha256)
        if existing:
            updates = []
            params = []
            for col, val in (("title", title), ("status", status), ("category", category),
                             ("source_id", source_id), ("source_url", source_url),
                             ("origin", origin), ("recorded_at", recorded_at),
                             ("file_path", file_path), ("file_size", file_size),
                             ("ext", ext), ("notes", notes), ("verified", verified)):
                if val is not None:
                    updates.append(f"{col}=?")
                    params.append(val)
            if updates:
                params.append(existing["id"])
                self.conn.execute(
                    f"UPDATE items SET {', '.join(updates)} WHERE id=?",
                    params,
                )
                self.conn.commit()
            return existing["id"], False
        cur = self.conn.execute(
            "INSERT INTO items (sha256, title, kind, status, category, source_id, source_url, "
            "origin, recorded_at, file_path, file_size, ext, notes, verified, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sha256, title, kind, status, category, source_id, source_url, origin,
             recorded_at, file_path, file_size, ext, notes, verified, utcnow()),
        )
        self.conn.commit()
        return cur.lastrowid, True

    def set_status(self, item_id, status):
        self.conn.execute(
            "UPDATE items SET status=?, processed_at=? WHERE id=?",
            (status, utcnow(), item_id),
        )
        self.conn.commit()
        self.log("status_change", item_id, status)

    def set_verified(self, item_id, verified=1):
        self.conn.execute(
            "UPDATE items SET verified=?, processed_at=? WHERE id=?",
            (verified, utcnow(), item_id),
        )
        self.conn.commit()

    def stats(self):
        counts = {
            "items": self.conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"],
            "sources": self.conn.execute("SELECT COUNT(*) c FROM sources").fetchone()["c"],
        }
        for kind in ("video", "document", "image", "transcript", "analysis"):
            c = self.conn.execute(
                "SELECT COUNT(*) c FROM items WHERE kind=?", (kind,)
            ).fetchone()["c"]
            counts[f"kind_{kind}"] = c
        for status in ("intake", "archived", "quarantine", "verified"):
            c = self.conn.execute(
                "SELECT COUNT(*) c FROM items WHERE status=?", (status,)
            ).fetchone()["c"]
            counts[f"status_{status}"] = c
        return counts

    def close(self):
        self.conn.commit()
        self.conn.close()
