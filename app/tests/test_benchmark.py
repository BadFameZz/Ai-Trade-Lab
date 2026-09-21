from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from aitra import db, money, store_run
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
    store_run.create_run(conn, "bench-run-1", "benchmark", "2026-01-01T00:00:00Z", "0.3.0")
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
    assert store_run.get_fills(bench._ctx.conn, "bench-run-1").__len__() == 1


def test_equity_folgt_dem_kurs_nach_dem_kauf(tmp_path):
    bench = _bench(tmp_path)
    k0 = _candle(0, "81287.03")
    k1 = _candle(900_000, "81287.03")
    bench.on_candle(k0, prev_candle=None)
    bench.on_candle(k1, prev_candle=k0)
    e_tief = bench.equity({"BTCUSDC": Decimal("70000")}, ts_ms=1_800_000)
    e_hoch = bench.equity({"BTCUSDC": Decimal("90000")}, ts_ms=1_800_000)
    assert e_hoch > e_tief


def test_kauft_trotz_grosser_kursluecke_notfalls_eine_kerze_spaeter(tmp_path):
    """Fix-Runde 1, Punkt 3+4: schlaegt der erste Versuch an der Kassenmarge
    fehl (grosse Luecke zwischen Schlusskurs und Eroeffnung), bleibt `bought`
    False und der naechste Aufruf versucht es erneut — Buy & Hold ist eine
    Kerze Verzoegerung gleichgueltig."""
    bench = _bench(tmp_path)
    k0 = _candle(0, "81287.03")
    k1 = _candle(900_000, "85351.38")  # +5 % Spruenge zwischen k0.close und k1.open
    k2 = _candle(1_800_000, "85351.38")  # k2.open == k1.close: keine Luecke mehr

    bench.on_candle(k0, prev_candle=None)
    bench.on_candle(k1, prev_candle=k0)
    assert bench.bought is False  # Marge reicht bei dieser Luecke nicht
    assert store_run.get_fills(bench._ctx.conn, "bench-run-1") == []

    bench.on_candle(k2, prev_candle=k1)
    assert bench.bought is True
    assert len(store_run.get_fills(bench._ctx.conn, "bench-run-1")) == 1


def test_look_ahead_ref_price_ist_niemals_die_fuellkerze(tmp_path):
    """Rot-Nachweis-Sicherung (Fix-Runde 1): ref_price darf nur aus
    prev_candle stammen, nie aus candle (E-001/E-006) — genau das Muster
    (ref_price=candle.open), das korrigiert wurde. k1 traegt bewusst einen
    ganz anderen Preis als k0, damit eine Verwechslung sofort auffaellt."""
    bench = _bench(tmp_path)
    k0 = _candle(0, "81287.03")
    k1 = _candle(900_000, "99999.00")
    bench.on_candle(k0, prev_candle=None)

    captured: dict = {}
    from aitra.benchmark import execute_proposal as _real_execute_proposal

    def spy(proposal, ctx, **kwargs):
        captured["ref_price"] = kwargs["ref_price"]
        return _real_execute_proposal(proposal, ctx, **kwargs)

    with patch("aitra.benchmark.execute_proposal", side_effect=spy):
        bench.on_candle(k1, prev_candle=k0)

    assert captured["ref_price"] == k0.close
    assert captured["ref_price"] != k1.open


def test_kein_zweiter_aufrufer_von_apply_in_benchmark():
    text = Path(__file__).resolve().parent.parent.joinpath("aitra", "benchmark.py").read_text()
    assert ".apply(" not in text


def test_a8b_keine_versteckte_uhr_kein_versteckter_zufall():
    text = Path(__file__).resolve().parent.parent.joinpath("aitra", "benchmark.py").read_text()
    assert re.search(r"time\.time|datetime\.(now|utcnow)|random\.", text) is None
