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


def test_symbol_spec_roundtrip(tmp_path):
    conn = _conn(tmp_path)
    spec = money.BUILTIN_SPECS["BTCUSDC"]
    store.upsert_symbol_spec(conn, spec, source="builtin", fetched_at="2026-01-01T00:00:00Z")
    back = store.get_symbol_spec(conn, "BTCUSDC")
    assert back == spec
