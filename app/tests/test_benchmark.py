from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

from aitra import db, money, store
from aitra.benchmark import BuyAndHold
from aitra.config import Config
from aitra.marketdata import Candle, SimClock

BTC = money.BUILTIN_SPECS["BTCUSDC"]


def _candle(open_time: int, price: str) -> Candle:
    return Candle(symbol="BTCUSDC", interval="15m", open_time=open_time, close_time=open_time + 899_999,
                  open=Decimal(price), high=Decimal(price), low=Decimal(price), close=Decimal(price),
                  volume=Decimal("1"), closed=True)


def _bench(tmp_path: Path) -> BuyAndHold:
    conn = db.connect(tmp_path / "a.db")
    db.migrate(conn)
    store.create_run(conn, "bench-run-1", "benchmark", "2026-01-01T00:00:00Z", "0.3.0")
    cfg = Config(Decimal("10000"), 10, 2, 50, tmp_path, "x" * 32)
    return BuyAndHold(cfg, conn, "bench-run-1", "BTCUSDC", BTC, fee_bps=10.0, slippage_bps=5.0,
                       clock=SimClock(0))


def test_kauft_nicht_ohne_vorgaengerkerze(tmp_path):
    bench = _bench(tmp_path)
    bench.on_candle(_candle(0, "81287.03"), prev_candle=None)
    assert bench.bought is False
    assert bench.equity({"BTCUSDC": Decimal("81287.03")}, ts_ms=0) == Decimal("10000")


def test_kauft_genau_einmal_auf_der_zweiten_kerze(tmp_path):
    bench = _bench(tmp_path)
    k0 = _candle(0, "81287.03")
    k1 = _candle(900_000, "81300.00")
    k2 = _candle(1_800_000, "81400.00")
    bench.on_candle(k0, prev_candle=None)
    bench.on_candle(k1, prev_candle=k0)
    assert bench.bought is True
    equity_nach_kauf = bench.equity({"BTCUSDC": Decimal("81300.00")}, ts_ms=900_000)
    assert equity_nach_kauf < Decimal("10000")  # Gebuehr + Slippage kosten etwas
    assert equity_nach_kauf > Decimal("9950")

    # zweiter Aufruf darf keinen weiteren Kauf ausloesen
    bench.on_candle(k2, prev_candle=k1)
    assert store.get_fills(bench._ctx.conn, "bench-run-1").__len__() == 1


def test_equity_folgt_dem_kurs_nach_dem_kauf(tmp_path):
    bench = _bench(tmp_path)
    k0 = _candle(0, "81287.03")
    k1 = _candle(900_000, "81287.03")
    bench.on_candle(k0, prev_candle=None)
    bench.on_candle(k1, prev_candle=k0)
    e_tief = bench.equity({"BTCUSDC": Decimal("70000")}, ts_ms=1_800_000)
    e_hoch = bench.equity({"BTCUSDC": Decimal("90000")}, ts_ms=1_800_000)
    assert e_hoch > e_tief


def test_kein_zweiter_aufrufer_von_apply_in_benchmark():
    text = Path(__file__).resolve().parent.parent.joinpath("aitra", "benchmark.py").read_text()
    assert ".apply(" not in text


def test_a8b_keine_versteckte_uhr_kein_versteckter_zufall():
    text = Path(__file__).resolve().parent.parent.joinpath("aitra", "benchmark.py").read_text()
    assert re.search(r"time\.time|datetime\.(now|utcnow)|random\.", text) is None
