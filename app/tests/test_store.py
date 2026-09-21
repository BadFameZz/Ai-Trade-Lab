# app/tests/test_store.py
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from aitra import db, money, store


def _conn(tmp_path: Path):
    conn = db.connect(tmp_path / "a.db")
    db.migrate(conn)
    return conn


def _row(i: int) -> store.CandleRow:
    day_ms = 86_400_000
    return store.CandleRow(
        symbol="BTCUSDC", interval="1d", open_time=i * day_ms, close_time=i * day_ms + day_ms - 1,
        open=Decimal("100"), high=Decimal("110"), low=Decimal("90"), close=Decimal("105"),
        volume=Decimal("1.5"), source="fixture", fetched_at="2026-01-01T00:00:00Z",
    )


def test_candles_geld_steht_als_text_a13(tmp_path):
    conn = _conn(tmp_path)
    store.upsert_candles(conn, [_row(0)])
    types = conn.execute(
        "SELECT typeof(open), typeof(high), typeof(low), typeof(close) FROM candles"
    ).fetchall()
    assert len(types) == 1
    assert tuple(types[0]) == ("text", "text", "text", "text")


def test_candles_upsert_ist_idempotent_und_liest_decimal_zurueck(tmp_path):
    conn = _conn(tmp_path)
    store.upsert_candles(conn, [_row(0), _row(1)])
    store.upsert_candles(conn, [_row(0)])  # gleicher Primaerschluessel, kein Duplikat
    rows = store.get_candles(conn, "BTCUSDC", "1d")
    assert len(rows) == 2
    assert rows[0].open_time == 0
    assert rows[0].open == Decimal("100")
    assert isinstance(rows[0].open, Decimal)


def test_prune_candles_a16(tmp_path):
    conn = _conn(tmp_path)
    store.upsert_candles(conn, [_row(i) for i in range(500)])  # Tag 0..499
    deleted = store.prune_candles(conn, "BTCUSDC", "1d", retention_days=400)
    remaining = store.get_candles(conn, "BTCUSDC", "1d", limit=1000)
    assert deleted == 100
    assert len(remaining) == 400
    assert remaining[0].open_time == 100 * 86_400_000


def test_fills_geld_steht_als_text_a13(tmp_path):
    conn = _conn(tmp_path)
    store.create_run(conn, "run-1", "replay", "2026-01-01T00:00:00Z", "0.3.0")
    fid = store.insert_fill(
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
    store.create_run(conn, "run-1", "replay", "2026-01-01T00:00:00Z", "0.3.0")
    store.upsert_position(
        conn, run_id="run-1", symbol="BTCUSDC", qty=Decimal("0.01"),
        avg_price=Decimal("81287.04"), realized_pnl=Decimal("0"),
        updated_at="2026-01-01T00:15:00Z",
    )
    positions = store.get_positions(conn, "run-1")
    assert positions["BTCUSDC"]["qty"] == Decimal("0.01")
    store.append_equity_point(
        conn, run_id="run-1", ts_ms=900_000, equity=Decimal("10000"), cash=Decimal("9186.32"),
        benchmark_equity=Decimal("10000"), exposure_pct=8.13,
    )
    curve = store.get_equity_curve(conn, "run-1")
    assert len(curve) == 1
    assert curve[0]["equity"] == Decimal("10000")


def test_pending_decision_lebenszyklus(tmp_path):
    conn = _conn(tmp_path)
    did = db.add_decision(conn, symbol="BTCUSDC", action="BUY", reason="test", approved=1,
                           requested_position_pct=8)
    store.mark_decision_pending(conn, did, pending_since_ms=1_000)
    pending = store.get_pending_decisions(conn)
    assert len(pending) == 1
    assert pending[0]["id"] == did
    store.resolve_decision(conn, did, fill_id=42)
    assert store.get_pending_decisions(conn) == []
    row = conn.execute("SELECT fill_id FROM decisions WHERE id=?", (did,)).fetchone()
    assert row["fill_id"] == 42


def test_symbol_spec_roundtrip(tmp_path):
    conn = _conn(tmp_path)
    spec = money.BUILTIN_SPECS["BTCUSDC"]
    store.upsert_symbol_spec(conn, spec, source="builtin", fetched_at="2026-01-01T00:00:00Z")
    back = store.get_symbol_spec(conn, "BTCUSDC")
    assert back == spec


def test_get_fills_reihenfolge_geld_exakt_und_run_isolation(tmp_path):
    conn = _conn(tmp_path)
    store.create_run(conn, "run-1", "replay", "2026-01-01T00:00:00Z", "0.3.0")
    store.create_run(conn, "run-2", "replay", "2026-01-01T00:00:00Z", "0.3.0")
    first_id = store.insert_fill(
        conn, run_id="run-1", decision_id=None, symbol="BTCUSDC", side="BUY",
        candle_open_time=900_000, price=Decimal("81287.04"), qty=Decimal("0.01"),
        gross_quote=Decimal("812.8704"), fee=Decimal("0.81287040"),
        net_quote=Decimal("813.68327040"), cash_after=Decimal("9186.31672960"),
        fee_bps=10.0, slippage_bps=5.0, ts="2026-01-01T00:15:00Z",
    )
    second_id = store.insert_fill(
        conn, run_id="run-1", decision_id=None, symbol="BTCUSDC", side="SELL",
        candle_open_time=1_800_000, price=Decimal("81300.12345678"), qty=Decimal("0.00500001"),
        gross_quote=Decimal("406.50123456"), fee=Decimal("0.40650123"),
        net_quote=Decimal("406.09473333"), cash_after=Decimal("9592.41146293"),
        fee_bps=10.0, slippage_bps=5.0, ts="2026-01-01T00:30:00Z",
    )
    store.insert_fill(
        conn, run_id="run-2", decision_id=None, symbol="ETHUSDC", side="BUY",
        candle_open_time=900_000, price=Decimal("2000"), qty=Decimal("1"),
        gross_quote=Decimal("2000"), fee=Decimal("2"), net_quote=Decimal("2002"),
        cash_after=Decimal("7998"), fee_bps=10.0, slippage_bps=5.0, ts="2026-01-01T00:15:00Z",
    )
    fills = store.get_fills(conn, "run-1")
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
    store.create_run(conn, "run-1", "replay", "2026-01-01T00:00:00Z", "0.3.0")
    before = conn.execute(
        "SELECT finished_at FROM runs WHERE run_id = ?", ("run-1",)
    ).fetchone()
    assert before["finished_at"] is None
    store.finish_run(conn, "run-1", "2026-01-01T01:00:00Z")
    after = conn.execute(
        "SELECT finished_at FROM runs WHERE run_id = ?", ("run-1",)
    ).fetchone()
    assert after["finished_at"] == "2026-01-01T01:00:00Z"


def test_expire_decision_entfernt_aus_pending(tmp_path):
    conn = _conn(tmp_path)
    did = db.add_decision(conn, symbol="BTCUSDC", action="BUY", reason="test", approved=1,
                           requested_position_pct=8)
    store.mark_decision_pending(conn, did, pending_since_ms=1_000)
    assert len(store.get_pending_decisions(conn)) == 1
    store.expire_decision(conn, did)
    assert store.get_pending_decisions(conn) == []
    row = conn.execute(
        "SELECT pending_since_ms, risk_code FROM decisions WHERE id = ?", (did,)
    ).fetchone()
    assert row["pending_since_ms"] is None
    assert row["risk_code"] == "PENDING_EXPIRED"
