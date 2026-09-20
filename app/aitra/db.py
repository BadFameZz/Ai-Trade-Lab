"""SQLite-Persistenz mit einfachen, versionierten Migrationen."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

MIGRATIONS: list[str] = [
    # 1 – Grundschema
    """
    CREATE TABLE decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        strategy_version TEXT NOT NULL DEFAULT 'manual',
        symbol TEXT NOT NULL,
        action TEXT NOT NULL,
        confidence REAL,
        reason TEXT NOT NULL DEFAULT '',
        requested_position_pct REAL,
        approved INTEGER,
        risk_code TEXT,
        risk_reason TEXT
    );
    CREATE INDEX idx_decisions_ts ON decisions(ts);
    CREATE TABLE events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        component TEXT NOT NULL,
        severity TEXT NOT NULL,
        event TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX idx_events_ts ON events(ts);
    CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """,
]


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    current = row["version"] if row else 0
    for i, sql in enumerate(MIGRATIONS[current:], start=current + 1):
        conn.executescript(sql)
        conn.execute("DELETE FROM schema_version")
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (i,))
        conn.commit()
    return len(MIGRATIONS)


def log_event(conn, component: str, severity: str, event: str, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO events (ts, component, severity, event, detail) VALUES (?, ?, ?, ?, ?)",
        (now(), component, severity, event, detail),
    )
    conn.commit()


def get_state(conn, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_state(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def add_decision(conn, **d) -> int:
    cur = conn.execute(
        """INSERT INTO decisions (ts, strategy_version, symbol, action, confidence, reason,
                                  requested_position_pct, approved, risk_code, risk_reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            now(), d.get("strategy_version", "manual"), d["symbol"], d["action"], d.get("confidence"),
            d.get("reason", ""), d.get("requested_position_pct"), d.get("approved"),
            d.get("risk_code"), d.get("risk_reason"),
        ),
    )
    conn.commit()
    return cur.lastrowid


def list_rows(conn, table: str, limit: int = 50) -> list[dict]:
    assert table in {"decisions", "events"}
    rows = conn.execute(f"SELECT * FROM {table} ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]
