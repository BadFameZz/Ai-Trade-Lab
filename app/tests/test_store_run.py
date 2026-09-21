# app/tests/test_store_run.py
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from aitra import db, money, store_run


def _conn(tmp_path: Path):
    conn = db.connect(tmp_path / "a.db")
    db.migrate(conn)
    return conn


def test_fills_geld_steht_als_text_a13(tmp_path):
    conn = _conn(tmp_path)
    store_run.create_run(conn, "run-1", "replay", "2026-01-01T00:00:00Z", "0.3.0")
    fid = store_run.insert_fill(
        conn, run_id="run-1", decision_id=None, symbol="BTCUSDC", side="BUY",
        candle_open_time=900_000, price=Decimal("81287.04"), qty=Decimal("0.01"),
        gross_quote=Decimal("812.8704"), fee=Decimal("0.81287040"),
        net_quote=Decimal("813.68327040"), cash_after=Decimal("9186.31672960"),
        fee_bps=10.0, slippage_bps=5.0, ts="2026-01-01T00:15:00Z",
    )
    assert fid > 0
    types = conn.execute(
        "SELECT typeof(price), typeof(qty), typeof(fee), typeof(cash_after) FROM fills"
    ).fetchall()
    assert tuple(types[0]) == ("text", "text", "text", "text")


def test_positions_und_equity_curve_runtrip(tmp_path):
    conn = _conn(tmp_path)
    store_run.create_run(conn, "run-1", "replay", "2026-01-01T00:00:00Z", "0.3.0")
    store_run.upsert_position(
        conn, run_id="run-1", symbol="BTCUSDC", qty=Decimal("0.01"),
        avg_price=Decimal("81287.04"), realized_pnl=Decimal("0"),
        updated_at="2026-01-01T00:15:00Z",
    )
    positions = store_run.get_positions(conn, "run-1")
    assert positions["BTCUSDC"]["qty"] == Decimal("0.01")
    store_run.append_equity_points(conn, [store_run.EquityPoint(
        run_id="run-1", ts_ms=900_000, equity=Decimal("10000"), cash=Decimal("9186.32"),
        benchmark_equity=Decimal("10000"), exposure_pct=8.13,
    )])
    curve = store_run.get_equity_curve(conn, "run-1")
    assert len(curve) == 1
    assert curve[0]["equity"] == Decimal("10000")


def test_pending_decision_lebenszyklus(tmp_path):
    conn = _conn(tmp_path)
    did = db.add_decision(conn, symbol="BTCUSDC", action="BUY", reason="test", approved=1,
                           requested_position_pct=8)
    store_run.mark_decision_pending(conn, did, pending_since_ms=1_000, ref_price=Decimal("81287.03"),
                                     base_qty=Decimal("0.01"))
    pending = store_run.get_pending_decisions(conn)
    assert len(pending) == 1
    assert pending[0]["id"] == did
    # Migration 3: der Vorschlagspreis liegt kanonisch als TEXT bei (E-007) und
    # ist beim Aufloesen wieder exakt derselbe Decimal.
    assert pending[0]["pending_ref_price"] == "81287.03000000"
    assert money.from_text(pending[0]["pending_ref_price"]) == Decimal("81287.03")
    # Migration 4: die fertig bemessene Menge liegt ebenso kanonisch als TEXT bei (E-010)
    assert pending[0]["pending_base_qty"] == "0.01000000"
    assert money.from_text(pending[0]["pending_base_qty"]) == Decimal("0.01")
    store_run.resolve_decision(conn, did, fill_id=42)
    assert store_run.get_pending_decisions(conn) == []
    row = conn.execute("SELECT fill_id, pending_ref_price, pending_base_qty FROM decisions WHERE id=?",
                        (did,)).fetchone()
    assert row["fill_id"] == 42
    assert row["pending_base_qty"] is None  # mit dem Fill aufgeraeumt (E-010)
    assert row["pending_ref_price"] is None  # mit dem Fill aufgeraeumt


def test_get_fills_reihenfolge_geld_exakt_und_run_isolation(tmp_path):
    conn = _conn(tmp_path)
    store_run.create_run(conn, "run-1", "replay", "2026-01-01T00:00:00Z", "0.3.0")
    store_run.create_run(conn, "run-2", "replay", "2026-01-01T00:00:00Z", "0.3.0")
    first_id = store_run.insert_fill(
        conn, run_id="run-1", decision_id=None, symbol="BTCUSDC", side="BUY",
        candle_open_time=900_000, price=Decimal("81287.04"), qty=Decimal("0.01"),
        gross_quote=Decimal("812.8704"), fee=Decimal("0.81287040"),
        net_quote=Decimal("813.68327040"), cash_after=Decimal("9186.31672960"),
        fee_bps=10.0, slippage_bps=5.0, ts="2026-01-01T00:15:00Z",
    )
    second_id = store_run.insert_fill(
        conn, run_id="run-1", decision_id=None, symbol="BTCUSDC", side="SELL",
        candle_open_time=1_800_000, price=Decimal("81300.12345678"), qty=Decimal("0.00500001"),
        gross_quote=Decimal("406.50123456"), fee=Decimal("0.40650123"),
        net_quote=Decimal("406.09473333"), cash_after=Decimal("9592.41146293"),
        fee_bps=10.0, slippage_bps=5.0, ts="2026-01-01T00:30:00Z",
    )
    store_run.insert_fill(
        conn, run_id="run-2", decision_id=None, symbol="BNBUSDC", side="BUY",
        candle_open_time=900_000, price=Decimal("2000"), qty=Decimal("1"),
        gross_quote=Decimal("2000"), fee=Decimal("2"), net_quote=Decimal("2002"),
        cash_after=Decimal("7998"), fee_bps=10.0, slippage_bps=5.0, ts="2026-01-01T00:15:00Z",
    )
    fills = store_run.get_fills(conn, "run-1")
    assert [f["id"] for f in fills] == [first_id, second_id]
    assert all(f["run_id"] == "run-1" for f in fills)
    assert fills[1]["price"] == Decimal("81300.12345678")
    assert fills[1]["qty"] == Decimal("0.00500001")
    assert fills[1]["gross_quote"] == Decimal("406.50123456")
    assert fills[1]["fee"] == Decimal("0.40650123")
    assert fills[1]["net_quote"] == Decimal("406.09473333")
    assert fills[1]["cash_after"] == Decimal("9592.41146293")


def test_finish_run_setzt_finished_at(tmp_path):
    conn = _conn(tmp_path)
    store_run.create_run(conn, "run-1", "replay", "2026-01-01T00:00:00Z", "0.3.0")
    before = conn.execute(
        "SELECT finished_at FROM runs WHERE run_id = ?", ("run-1",)
    ).fetchone()
    assert before["finished_at"] is None
    store_run.finish_run(conn, "run-1", "2026-01-01T01:00:00Z")
    after = conn.execute(
        "SELECT finished_at FROM runs WHERE run_id = ?", ("run-1",)
    ).fetchone()
    assert after["finished_at"] == "2026-01-01T01:00:00Z"


def test_expire_decision_entfernt_aus_pending(tmp_path):
    conn = _conn(tmp_path)
    did = db.add_decision(conn, symbol="BTCUSDC", action="BUY", reason="test", approved=1,
                           requested_position_pct=8)
    store_run.mark_decision_pending(conn, did, pending_since_ms=1_000, ref_price=Decimal("81287.03"),
                                     base_qty=Decimal("0.01"))
    assert len(store_run.get_pending_decisions(conn)) == 1
    store_run.expire_decision(conn, did)
    assert store_run.get_pending_decisions(conn) == []
    row = conn.execute(
        "SELECT pending_since_ms, pending_ref_price, risk_code FROM decisions WHERE id = ?", (did,)
    ).fetchone()
    assert row["pending_since_ms"] is None
    assert row["pending_ref_price"] is None
    assert row["risk_code"] == "PENDING_EXPIRED"
