# app/tests/test_ledger.py
from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest

from aitra import money
from aitra.ledger import Fill, Ledger, Order, Rejection, Valuation
from aitra.marketdata import Candle

BTC = money.BUILTIN_SPECS["BTCUSDC"]
ETH = money.BUILTIN_SPECS["ETHUSDC"]
SPECS = {"BTCUSDC": BTC, "ETHUSDC": ETH}


def _candle(open_price: str, open_time: int = 900_000, high="999999", low="1", close="0") -> Candle:
    return Candle(
        symbol="BTCUSDC", interval="15m", open_time=open_time, close_time=open_time + 899_999,
        open=Decimal(open_price), high=Decimal(high), low=Decimal(low), close=Decimal(close),
        volume=Decimal("1"), closed=True,
    )


def _ledger(cash: str = "10000", fee_bps: float = 10.0, slippage_bps: float = 5.0) -> Ledger:
    return Ledger(starting_cash=Decimal(cash), specs=SPECS, fee_bps=fee_bps, slippage_bps=slippage_bps)


def test_buy_fill_rechnet_slippage_und_gebuehr_wie_in_spec_6_1():
    ledger = _ledger()
    f = ledger.apply(Order("BTCUSDC", "BUY", Decimal("0.01")), _candle("81287.03"))
    assert isinstance(f, Fill)
    # s=0.0005, f=0.0010: exec = tick_up(81287.03*1.0005) = tick_up(81327.6736...) = 81327.68
    assert f.price == Decimal("81327.68")
    assert f.gross_quote == Decimal("813.2768")
    assert f.fee == Decimal("0.81327680")
    assert f.net_quote == Decimal("814.09007680")
    assert f.cash_after == Decimal("9185.90992320")
    assert f.cash_after == Decimal("10000") - f.net_quote
    assert f.candle_open_time == 900_000


def test_sell_fill_rundet_gegen_den_haendler():
    ledger = _ledger()
    ledger.apply(Order("BTCUSDC", "BUY", Decimal("0.02")), _candle("81287.03"))
    f = ledger.apply(Order("BTCUSDC", "SELL", Decimal("0.01")), _candle("81400.00", open_time=1_800_000))
    assert isinstance(f, Fill)
    # s=0.0005: exec = tick_down(81400*0.9995) = tick_down(81359.30) = 81359.30
    assert f.price == Decimal("81359.30")
    assert f.side == "SELL"


def test_ledger_nutzt_nur_open_und_open_time_der_folgekerze():
    ledger = _ledger()
    hoch = _candle("81287.03", high="999999999", low="1", close="1")
    niedrig = _candle("81287.03", high="1", low="1", close="1")
    f1 = ledger.apply(Order("BTCUSDC", "BUY", Decimal("0.01")), hoch)
    ledger2 = _ledger()
    f2 = ledger2.apply(Order("BTCUSDC", "BUY", Decimal("0.01")), niedrig)
    assert isinstance(f1, Fill) and isinstance(f2, Fill)
    assert f1.price == f2.price == Decimal("81327.68")


def test_no_spec_ohne_symbolspec():
    ledger = _ledger()
    r = ledger.apply(Order("DOGEUSDC", "BUY", Decimal("100")), _candle("0.1", ))
    assert isinstance(r, Rejection) and r.code == "NO_SPEC"


def test_zero_qty_nach_quantisierung():
    ledger = _ledger()
    r = ledger.apply(Order("BTCUSDC", "BUY", Decimal("0.000001")), _candle("81287.03"))
    assert isinstance(r, Rejection) and r.code == "ZERO_QTY"


def test_min_notional_an_der_grenze_a4():
    ledger = _ledger()
    # 4,99 USDC-Ziel -> Rejection
    r = ledger.apply(Order("BTCUSDC", "BUY", Decimal("0.00006")), _candle("81287.04"))
    assert isinstance(r, Rejection) and r.code == "MIN_NOTIONAL"
    assert "garantiert ab" in r.reason
    # 5,82 USDC (K-3, effektive Grenze) -> Fill
    eff = BTC.effective_min_notional(Decimal("81287.04"))
    qty = money.step_down(eff / Decimal("81287.04") * Decimal("1.02"), BTC.step_size)
    f = ledger.apply(Order("BTCUSDC", "BUY", qty), _candle("81287.04"))
    assert isinstance(f, Fill)


def test_insufficient_cash():
    ledger = _ledger(cash="1")
    r = ledger.apply(Order("BTCUSDC", "BUY", Decimal("0.01")), _candle("81287.03"))
    assert isinstance(r, Rejection) and r.code == "INSUFFICIENT_CASH"
    assert ledger.cash == Decimal("1")  # Kasse bleibt unveraendert und nichtnegativ


def test_no_position_beim_verkauf_ohne_bestand():
    ledger = _ledger()
    r = ledger.apply(Order("BTCUSDC", "SELL", Decimal("0.01")), _candle("81287.03"))
    assert isinstance(r, Rejection) and r.code == "NO_POSITION"


def test_no_position_beim_verkauf_ueber_bestand_hinaus():
    ledger = _ledger()
    ledger.apply(Order("BTCUSDC", "BUY", Decimal("0.01")), _candle("81287.03"))
    r = ledger.apply(Order("BTCUSDC", "SELL", Decimal("0.02")), _candle("81287.03", open_time=1_800_000))
    assert isinstance(r, Rejection) and r.code == "NO_POSITION"


def test_mark_liefert_exakte_identitaet():
    ledger = _ledger()
    ledger.apply(Order("BTCUSDC", "BUY", Decimal("0.05")), _candle("81287.03"))
    v = ledger.mark({"BTCUSDC": Decimal("82000")}, ts_ms=1_800_000)
    assert isinstance(v, Valuation)
    berechnet = v.cash + ledger.position("BTCUSDC").qty * Decimal("82000")
    assert v.equity - berechnet == Decimal("0")


def test_apply_requantisiert_base_qty_immer_ab_nie_auf():
    """Defensive Requantisierung in apply() ist idempotentes Abrunden.

    0.000125 liegt bei step_size=0.00001 genau zwischen zwei Vielfachen:
    abgerundet 0.00012, aufgerundet (ROUND_HALF_UP) 0.00013 - beide Mengen
    liegen ueber min_notional, sodass allein die Rundungsrichtung geprueft
    wird. Ein Programmierfehler in sizing.py (Aufgabe 6), der eine nicht auf
    step_size ausgerichtete Menge liefert, darf hier nie zu einer groesseren
    als der angeforderten Menge fuehren.
    """
    ledger = _ledger()
    f = ledger.apply(Order("BTCUSDC", "BUY", Decimal("0.000125")), _candle("81287.03"))
    assert isinstance(f, Fill)
    assert f.qty == Decimal("0.00012")


def test_buchhaltung_identitaet_a1():
    """A-1: feste, deterministische Sequenz (kein Zufall), 10.000 Fillversuche.

    Orchestrator-Entscheidung nach dem ersten Lauf (3513/10000 Treffer, ganz
    ueberwiegend INSUFFICIENT_CASH): der Treiber war fehlerhaft, nicht die
    Schwelle. Die urspruengliche Fassung bemass die BUY-Groesse an der
    Gesamt-Equity statt an der verfuegbaren Kasse und liess die Kasse dadurch
    strukturell leerlaufen. Reparatur: die BUY-Groesse wird zusaetzlich auf
    95 % der aktuellen Kasse gedeckelt, damit der Kreislauf traegt. Die
    Identitaetspruefung selbst (exakte Gleichheit, keine Toleranz) bleibt
    unveraendert.
    """
    ledger = _ledger()
    ts = 900_000
    treffer = 0
    for i in range(10_000):
        symbol = "BTCUSDC" if i % 2 == 0 else "ETHUSDC"
        spec = SPECS[symbol]
        price = Decimal("81287.03") if symbol == "BTCUSDC" else Decimal("2631.77")
        price = price + Decimal(i % 50) * spec.tick_size
        side = "BUY" if (i % 3) != 2 else "SELL"
        pos = ledger.position(symbol)
        if side == "SELL" and pos.qty == 0:
            side = "BUY"
        equity_now = ledger.mark({"BTCUSDC": price, "ETHUSDC": price}, ts_ms=ts).equity
        target_quote = equity_now * Decimal("2") / Decimal(100)
        if side == "BUY":
            # Gedeckelt auf die tatsaechlich verfuegbare Kasse, sonst laeuft
            # sie bei ueberwiegend BUY-lastigen Sequenzen strukturell leer.
            cash_cap = ledger.cash * Decimal("0.95")
            raw_qty = min(target_quote, cash_cap) / price
        else:
            raw_qty = min(target_quote / price, pos.qty)
        qty = money.step_down(raw_qty, spec.step_size)
        candle = Candle(symbol=symbol, interval="15m", open_time=ts, close_time=ts + 899_999,
                         open=price, high=price, low=price, close=price, volume=Decimal("1"), closed=True)
        result = ledger.apply(Order(symbol, side, qty), candle)
        ts += 900_000
        if isinstance(result, Rejection):
            continue
        treffer += 1
        v = ledger.mark({"BTCUSDC": price, "ETHUSDC": price}, ts_ms=ts)
        held_value = sum(ledger.position(s).qty * price for s in ("BTCUSDC", "ETHUSDC"))
        assert v.equity - (v.cash + held_value) == Decimal("0")
    assert treffer >= 9000  # die Pruefflaeche darf nicht leer sein (gemessen: 9989/10000)
    assert ledger.cash >= Decimal("0")
    assert ledger.position("BTCUSDC").qty >= Decimal("0")
    assert ledger.position("ETHUSDC").qty >= Decimal("0")


def test_a8b_keine_versteckte_uhr_kein_versteckter_zufall():
    text = Path(__file__).resolve().parent.parent.joinpath("aitra", "ledger.py").read_text()
    assert re.search(r"time\.time|datetime\.(now|utcnow)|random\.", text) is None
