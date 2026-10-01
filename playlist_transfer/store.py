"""Local persistence: settings/credentials (JSON) and plans, syncs, match cache (SQLite)."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

DATA_DIR = Path(os.environ.get("PLT_DATA_DIR", Path.home() / ".playlist-transfer"))
_lock = threading.RLock()


def _ensure_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- settings
def _settings_path() -> Path:
    return DATA_DIR / "settings.json"


def load_settings() -> dict[str, Any]:
    with _lock:
        p = _settings_path()
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text("utf-8"))
        except json.JSONDecodeError:
            return {}


def save_settings(data: dict[str, Any]) -> None:
    with _lock:
        _ensure_dir()
        p = _settings_path()
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), "utf-8")
        tmp.replace(p)


def get_section(name: str) -> dict[str, Any]:
    return load_settings().get(name, {})


def update_section(name: str, **values: Any) -> dict[str, Any]:
    with _lock:
        s = load_settings()
        sec = s.setdefault(name, {})
        for k, v in values.items():
            if v is None:
                sec.pop(k, None)
            else:
                sec[k] = v
        save_settings(s)
        return sec


def clear_section(name: str, keep: tuple[str, ...] = ()) -> None:
    with _lock:
        s = load_settings()
        sec = s.get(name, {})
        s[name] = {k: v for k, v in sec.items() if k in keep}
        save_settings(s)


# ---------------------------------------------------------------- sqlite
SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY, created_at REAL, updated_at REAL, status TEXT, data TEXT
);
CREATE TABLE IF NOT EXISTS syncs (
    id TEXT PRIMARY KEY, created_at REAL, data TEXT
);
CREATE TABLE IF NOT EXISTS history (
    id TEXT PRIMARY KEY, at REAL, plan_id TEXT, data TEXT
);
CREATE TABLE IF NOT EXISTS match_cache (
    src_service TEXT, src_id TEXT, tgt_service TEXT, item_type TEXT,
    target TEXT, confidence REAL, method TEXT, manual INTEGER, updated_at REAL,
    PRIMARY KEY (src_service, src_id, tgt_service, item_type)
);
"""


def connect() -> sqlite3.Connection:
    _ensure_dir()
    conn = sqlite3.connect(DATA_DIR / "data.db", timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def new_id() -> str:
    return uuid.uuid4().hex[:12]


# plans
def save_plan(plan: dict[str, Any]) -> None:
    with _lock, connect() as c:
        now = time.time()
        plan.setdefault("created_at", now)
        plan["updated_at"] = now
        c.execute(
            "INSERT INTO plans(id, created_at, updated_at, status, data) VALUES (?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at, status=excluded.status, data=excluded.data",
            (plan["id"], plan["created_at"], now, plan.get("status", ""), json.dumps(plan)),
        )


def get_plan(plan_id: str) -> dict[str, Any] | None:
    with connect() as c:
        row = c.execute("SELECT data FROM plans WHERE id=?", (plan_id,)).fetchone()
        return json.loads(row["data"]) if row else None


def list_plans(limit: int = 200) -> list[dict[str, Any]]:
    with connect() as c:
        rows = c.execute("SELECT data FROM plans ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(r["data"]) for r in rows]


def delete_plan(plan_id: str) -> None:
    with _lock, connect() as c:
        c.execute("DELETE FROM plans WHERE id=?", (plan_id,))


# syncs
def save_sync(sync: dict[str, Any]) -> None:
    with _lock, connect() as c:
        sync.setdefault("created_at", time.time())
        c.execute(
            "INSERT INTO syncs(id, created_at, data) VALUES (?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
            (sync["id"], sync["created_at"], json.dumps(sync)),
        )


def get_sync(sync_id: str) -> dict[str, Any] | None:
    with connect() as c:
        row = c.execute("SELECT data FROM syncs WHERE id=?", (sync_id,)).fetchone()
        return json.loads(row["data"]) if row else None


def list_syncs() -> list[dict[str, Any]]:
    with connect() as c:
        return [json.loads(r["data"]) for r in c.execute("SELECT data FROM syncs ORDER BY created_at").fetchall()]


def delete_sync(sync_id: str) -> None:
    with _lock, connect() as c:
        c.execute("DELETE FROM syncs WHERE id=?", (sync_id,))


# history
def add_history(plan_id: str, data: dict[str, Any]) -> None:
    with _lock, connect() as c:
        c.execute(
            "INSERT INTO history(id, at, plan_id, data) VALUES (?,?,?,?)",
            (new_id(), time.time(), plan_id, json.dumps(data)),
        )


def list_history(limit: int = 200) -> list[dict[str, Any]]:
    with connect() as c:
        rows = c.execute("SELECT * FROM history ORDER BY at DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": r["id"], "at": r["at"], "plan_id": r["plan_id"], **json.loads(r["data"])} for r in rows]


# match cache
def cache_get(src_service: str, src_id: str, tgt_service: str, item_type: str) -> dict[str, Any] | None:
    if not src_id:
        return None
    with connect() as c:
        row = c.execute(
            "SELECT * FROM match_cache WHERE src_service=? AND src_id=? AND tgt_service=? AND item_type=?",
            (src_service, src_id, tgt_service, item_type),
        ).fetchone()
        if not row:
            return None
        return {
            "target": json.loads(row["target"]) if row["target"] else None,
            "confidence": row["confidence"],
            "method": row["method"],
            "manual": bool(row["manual"]),
        }


def cache_put(
    src_service: str,
    src_id: str,
    tgt_service: str,
    item_type: str,
    target: dict[str, Any] | None,
    confidence: float,
    method: str,
    manual: bool = False,
) -> None:
    if not src_id:
        return
    with _lock, connect() as c:
        c.execute(
            "INSERT OR REPLACE INTO match_cache VALUES (?,?,?,?,?,?,?,?,?)",
            (src_service, src_id, tgt_service, item_type, json.dumps(target) if target else None,
             confidence, method, int(manual), time.time()),
        )
