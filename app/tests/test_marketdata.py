from __future__ import annotations

import re
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from aitra import db, store
from aitra.marketdata import Candle, ListSource, SimClock, SqliteSource, WallClock, staleness


def _candle(open_time: int, closed: bool = True) -> Candle:
    step = 900_000
    return Candle(
        symbol="BTCUSDC", interval="15m", open_time=open_time, close_time=open_time + step - 1,
        open=Decimal("81000"), high=Decimal("81100"), low=Decimal("80900"), close=Decimal("81050"),
        volume=Decimal("1.2"), closed=closed,
    )


def test_wallclock_liefert_ms_seit_epoche():
    ms = WallClock().now_ms()
    assert ms > 1_700_000_000_000  # nach 2023, grobe Plausibilitaet


def test_simclock_liefert_gesetzten_wert():
    clock = SimClock(1_000)
    assert clock.now_ms() == 1_000
    clock.set(2_000)
    assert clock.now_ms() == 2_000


def test_list_source_liefert_aufsteigend_und_gedeckelt():
    candles = [_candle(i * 900_000) for i in range(10)]
    src = ListSource(candles)
    out = src.candles("BTCUSDC", "15m", limit=3)
    assert [c.open_time for c in out] == [0, 900_000, 1_800_000]


def test_sqlite_source_liest_nur_gespeicherte_geschlossene_kerzen(tmp_path: Path):
    conn = db.connect(tmp_path / "a.db")
    db.migrate(conn)
    store.upsert_candles(conn, [
        store.CandleRow(symbol="BTCUSDC", interval="15m", open_time=0, close_time=899_999,
                         open=Decimal("81000"), high=Decimal("81100"), low=Decimal("80900"),
                         close=Decimal("81050"), volume=Decimal("1.2"), source="fixture",
                         fetched_at="2026-01-01T00:00:00Z"),
    ])
    src = SqliteSource(conn)
    out = src.candles("BTCUSDC", "15m")
    assert len(out) == 1
    assert out[0].closed is True
    assert out[0].open == Decimal("81000")
    assert isinstance(out[0].open, Decimal)


def test_list_und_sqlite_source_liefern_identische_kerzen(tmp_path: Path):
    candles = [_candle(i * 900_000) for i in range(5)]
    conn = db.connect(tmp_path / "a.db")
    db.migrate(conn)
    store.upsert_candles(conn, [
        store.CandleRow(symbol=c.symbol, interval=c.interval, open_time=c.open_time,
                         close_time=c.close_time, open=c.open, high=c.high, low=c.low,
                         close=c.close, volume=c.volume, source="fixture",
                         fetched_at="2026-01-01T00:00:00Z")
        for c in candles
    ])
    a = ListSource(candles).candles("BTCUSDC", "15m", limit=100)
    b = SqliteSource(conn).candles("BTCUSDC", "15m", limit=100)
    assert [(c.open_time, c.open, c.close) for c in a] == [(c.open_time, c.open, c.close) for c in b]


def test_list_source_filtert_offene_kerzen():
    """ListSource darf offene Kerzen nie weitergeben (Critical)."""
    closed_candles = [_candle(i * 900_000, closed=True) for i in range(3)]
    open_candles = [_candle(i * 900_000 + 450_000, closed=False) for i in range(3)]
    mixed = closed_candles + open_candles
    src = ListSource(mixed)
    out = src.candles("BTCUSDC", "15m")
    # Prüfen: nur geschlossene, und die richtigen (nicht bloß Anzahl)
    assert len(out) == 3
    assert [c.open_time for c in out] == [0, 900_000, 1_800_000]
    assert all(c.closed for c in out)


@pytest.mark.parametrize("interval_s,age_s,expected", [
    (60, 149, "ok"), (60, 150, "ok"), (60, 151, "warn"), (60, 299, "warn"), (60, 301, "stale"),
    (900, 1349, "ok"), (900, 1350, "ok"), (900, 1351, "warn"), (900, 2699, "warn"), (900, 2701, "stale"),
])
def test_staleness_intervallrelative_schwellen_a11(interval_s, age_s, expected):
    clock = SimClock(age_s * 1000)
    result = staleness(clock, latest_close_time_ms=0, server_time_ms=clock.now_ms(),
                        interval_s=interval_s)
    assert result.status == expected


@pytest.mark.parametrize("skew_s,expected", [(31, "stale"), (30, "warn"), (5, "ok"), (29, "warn"), (4, "ok"), (-31, "stale"), (-30, "warn"), (-5, "ok")])
def test_staleness_uhrversatz_a11b(skew_s, expected):
    clock = SimClock(1_000_000)
    result = staleness(clock, latest_close_time_ms=clock.now_ms(), server_time_ms=clock.now_ms() - skew_s * 1000,
                        interval_s=900)
    assert result.status == expected


def test_a8b_keine_versteckte_uhr_kein_versteckter_zufall():
    text = Path(__file__).resolve().parent.parent.joinpath("aitra", "marketdata.py").read_text()
    matches = list(re.finditer(r"time\.time|datetime\.(now|utcnow)|random\.", text))
    assert len(matches) == 1
    wallclock_start = text.index("class WallClock")
    tail = text[wallclock_start + len("class WallClock"):]
    next_top_level = re.search(r"\nclass |\ndef ", tail)
    wallclock_end = len(text) if next_top_level is None else wallclock_start + len("class WallClock") + next_top_level.start()
    assert wallclock_start < matches[0].start() < wallclock_end
