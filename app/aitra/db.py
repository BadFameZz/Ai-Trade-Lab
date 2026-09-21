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
    # 2 – Marktdaten, Ledger, Benchmark
    """
    CREATE TABLE candles (
        symbol     TEXT    NOT NULL,
        interval   TEXT    NOT NULL,
        open_time  INTEGER NOT NULL,          -- ms UTC
        close_time INTEGER NOT NULL,
        open TEXT NOT NULL, high TEXT NOT NULL, low TEXT NOT NULL, close TEXT NOT NULL,
        volume TEXT NOT NULL,
        source     TEXT    NOT NULL,          -- 'binance' | 'backfill' | 'fixture'
        fetched_at TEXT    NOT NULL,
        PRIMARY KEY (symbol, interval, open_time)
    ) WITHOUT ROWID;
    CREATE INDEX idx_candles_time ON candles(symbol, interval, close_time);

    CREATE TABLE runs (
        run_id      TEXT PRIMARY KEY,         -- 'live' | 'replay-<utc>' | 'bench-<run_id>'
        kind        TEXT NOT NULL,            -- live | replay | benchmark
        started_at  TEXT NOT NULL,
        finished_at TEXT,
        code_version TEXT NOT NULL,
        params_json TEXT NOT NULL DEFAULT '{}' -- Symbole, Intervall, Gebühr, Slippage,
                                               -- Startkapital, Herkunft der SymbolSpecs
    );

    CREATE TABLE symbol_specs (
        symbol TEXT PRIMARY KEY,
        base TEXT NOT NULL, quote TEXT NOT NULL,
        tick_size TEXT NOT NULL, step_size TEXT NOT NULL,
        min_qty TEXT NOT NULL, min_notional TEXT NOT NULL,
        base_precision INTEGER NOT NULL, quote_precision INTEGER NOT NULL,
        source TEXT NOT NULL,                 -- 'builtin' | 'binance'
        fetched_at TEXT NOT NULL
    );

    CREATE TABLE fills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        run_id TEXT NOT NULL REFERENCES runs(run_id),
        decision_id INTEGER REFERENCES decisions(id),
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,                   -- BUY | SELL
        candle_open_time INTEGER NOT NULL,
        price TEXT NOT NULL, qty TEXT NOT NULL,
        gross_quote TEXT NOT NULL, fee TEXT NOT NULL, net_quote TEXT NOT NULL,
        cash_after TEXT NOT NULL,
        fee_bps REAL NOT NULL, slippage_bps REAL NOT NULL
    );
    CREATE INDEX idx_fills_run ON fills(run_id, id);

    CREATE TABLE positions (
        run_id TEXT NOT NULL REFERENCES runs(run_id),
        symbol TEXT NOT NULL,
        qty TEXT NOT NULL,
        avg_price TEXT NOT NULL,
        realized_pnl TEXT NOT NULL DEFAULT '0',
        updated_at TEXT NOT NULL,
        PRIMARY KEY (run_id, symbol)
    );

    CREATE TABLE equity_curve (
        run_id TEXT NOT NULL REFERENCES runs(run_id),
        ts_ms INTEGER NOT NULL,
        equity TEXT NOT NULL,
        cash TEXT NOT NULL,
        benchmark_equity TEXT,
        exposure_pct REAL NOT NULL,
        PRIMARY KEY (run_id, ts_ms)
    ) WITHOUT ROWID;

    ALTER TABLE decisions ADD COLUMN run_id TEXT;
    ALTER TABLE decisions ADD COLUMN fill_id INTEGER;
    ALTER TABLE decisions ADD COLUMN pending_since_ms INTEGER;  -- schwebende Vorschläge, E-006
    """,
    # 3 – ref_price des Vorschlagszeitpunkts an der schwebenden Entscheidung
    #     Ohne diese Spalte bemisst resolve_pending() die Order gegen die Füllkerze
    #     (candle.open) und damit gegen den Preis, der erst durch die Füllung
    #     entsteht. Replay bemisst gegen current.close — zwei verschiedene Mengen
    #     für dieselbe Entscheidung, entgegen E-001. Geld steht als TEXT (E-007).
    #     Rücknahme: ALTER TABLE decisions DROP COLUMN pending_ref_price;
    #     (SQLite ≥ 3.35). Die Spalte ist nullbar, Bestandszeilen bleiben gültig.
    """
    ALTER TABLE decisions ADD COLUMN pending_ref_price TEXT;
    """,
    # 4 – die fertig bemessene Menge an der schwebenden Entscheidung (E-010, Weg A)
    #     Ohne diese Spalte muesste resolve_pending() beim Aufloesen neu bemessen und
    #     dafuer neu bewerten. equity und cash haengen dann an der Fuellkerze — an einem
    #     Preis, den die Entscheidung nicht kennen konnte. run_replay() bemisst dagegen
    #     bei t. Zwei verschiedene Mengen fuer dieselbe Entscheidung, entgegen E-001,
    #     und A-8 ("drei Quellen, ein Hash") waere unerreichbar.
    #     Geld steht als TEXT (E-007). Die Spalte ist nullbar; Zeilen aus einer aelteren
    #     DB behalten NULL und werden von resolve_pending() mit NO_BASE_QTY abgelehnt.
    #     Ruecknahme: ALTER TABLE decisions DROP COLUMN pending_base_qty; (SQLite >= 3.35)
    """
    ALTER TABLE decisions ADD COLUMN pending_base_qty TEXT;
    """,
]


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: Path) -> sqlite3.Connection:
    """Oeffnet eine SQLite-Verbindung.

    check_same_thread=False (Fixrunde 1, Aufgabe 5/poller.py, gemessen): der
    Live-Poller wird in einem eigenen Thread gestartet, dem der Aufrufer eine
    bereits offene Verbindung UEBERGIBT (poller.start(conn, ...)). Mit dem
    Python-Standard (check_same_thread=True) wirft jeder einzelne
    db.get_state()/db.log_event()-Aufruf aus diesem Thread sofort
    sqlite3.ProgrammingError - und run_forever() faengt das ab und geht in
    Backoff, wodurch der Poller niemals einen einzigen erfolgreichen Zyklus
    faehrt, aber auch nie sichtbar abstuerzt (stiller Totalausfall). Sicher,
    weil jede Verbindung dieser Funktion in genau EINEM Thread benutzt wird
    (dem, der sie haelt) - nur eben nicht zwingend demselben, der sie erzeugt
    hat; SQLite selbst ist im Standard-Threading-Modus (serialized) fuer
    mehrere Verbindungen auf dieselbe Datei ohnehin ausgelegt (WAL-Modus).
    """
    conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
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
    """Schreibt eine Entscheidung ins Journal; mit run_id in EINER Transaktion.

    Der Aufrufer trug run_id frueher mit einem zweiten UPDATE plus eigenem
    Commit nach. Das waren zwei Commits je Entscheidung und im Zeitraffer der
    groesste Einzelposten der Schreiblast (gemessen: 70.078 von 116.421 Commits
    bei 35.039 Kerzen, dateibasierte DB). Jetzt laeuft das UPDATE in derselben
    Transaktion wie das INSERT, und es gibt genau einen Commit.

    Die Spalte run_id kommt erst mit Migration 2. Das UPDATE wird deshalb nur
    ausgefuehrt, wenn ein run_id uebergeben wurde — auf einem Schema der
    Version 1 (A-15) bleibt add_decision damit lauffaehig wie zuvor.
    """
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
    run_id = d.get("run_id")
    if run_id is not None:
        conn.execute("UPDATE decisions SET run_id = ? WHERE id = ?", (run_id, cur.lastrowid))
    conn.commit()
    return cur.lastrowid


def list_rows(conn, table: str, limit: int = 50) -> list[dict]:
    assert table in {"decisions", "events"}
    rows = conn.execute(f"SELECT * FROM {table} ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]
