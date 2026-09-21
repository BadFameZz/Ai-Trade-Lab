# app/tests/test_db.py
from __future__ import annotations

from pathlib import Path

from aitra import db


def test_migration_2_erzeugt_alle_neuen_tabellen(tmp_path: Path):
    conn = db.connect(tmp_path / "a.db")
    version = db.migrate(conn)
    assert version == 2
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert {"candles", "runs", "symbol_specs", "fills", "positions", "equity_curve"} <= tables
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(decisions)").fetchall()}
    assert {"run_id", "fill_id", "pending_since_ms"} <= cols


def test_migration_bewahrt_bestehende_decisions_a15(tmp_path: Path):
    conn = db.connect(tmp_path / "a.db")
    conn.executescript(db.MIGRATIONS[0])
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version (version) VALUES (1)")
    conn.commit()
    for i in range(3):
        db.add_decision(conn, symbol="BTCUSDC", action="WAIT", reason=f"r{i}", approved=1)
    before = sorted(
        (r["symbol"], r["action"], r["reason"])
        for r in conn.execute("SELECT symbol, action, reason FROM decisions").fetchall()
    )
    version = db.migrate(conn)
    assert version == 2
    assert conn.execute("SELECT COUNT(*) c FROM decisions").fetchone()["c"] == 3
    after = sorted(
        (r["symbol"], r["action"], r["reason"])
        for r in conn.execute("SELECT symbol, action, reason FROM decisions").fetchall()
    )
    assert before == after
