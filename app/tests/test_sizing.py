from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest

from aitra import money
from aitra.ledger import Ledger, Order, Rejection, Valuation
from aitra.marketdata import Candle
from aitra.risk import Proposal
from aitra.sizing import size_order

BTC = money.BUILTIN_SPECS["BTCUSDC"]
FEE_BPS = 10.0
SLIP_BPS = 5.0


def _valuation(equity: str = "10000", cash: str | None = None) -> Valuation:
    cash_dec = Decimal(cash) if cash is not None else Decimal(equity)
    return Valuation(ts_ms=0, cash=cash_dec, position_value=Decimal(0), equity=Decimal(equity),
                      exposure_pct=0.0, position_pct_by_symbol={})


def test_buy_liefert_quantisierte_order():
    p = Proposal("BTCUSDC", "BUY", position_pct=10)
    o = size_order(p, _valuation(), BTC, Decimal("81287.03"), FEE_BPS, SLIP_BPS, held_qty=Decimal(0))
    assert isinstance(o, Order)
    assert o.base_qty % BTC.step_size == 0


def test_sell_ohne_bestand_wird_abgelehnt():
    p = Proposal("BTCUSDC", "SELL", position_pct=5)
    r = size_order(p, _valuation(), BTC, Decimal("81287.03"), FEE_BPS, SLIP_BPS, held_qty=Decimal(0))
    assert isinstance(r, Rejection) and r.code == "NO_POSITION"


def test_sell_deckelt_auf_bestand():
    p = Proposal("BTCUSDC", "SELL", position_pct=100)  # will mehr, als gehalten wird
    o = size_order(p, _valuation(), BTC, Decimal("81287.03"), FEE_BPS, SLIP_BPS, held_qty=Decimal("0.01"))
    assert isinstance(o, Order)
    assert o.base_qty <= Decimal("0.01")


def test_insufficient_cash_bei_knapper_kasse():
    p = Proposal("BTCUSDC", "BUY", position_pct=10)
    r = size_order(p, _valuation(equity="10000", cash="1"), BTC, Decimal("81287.03"),
                    FEE_BPS, SLIP_BPS, held_qty=Decimal(0))
    assert isinstance(r, Rejection) and r.code == "INSUFFICIENT_CASH"


def test_a3_quantisierung_1000_stichproben():
    """A-3: 1.000 Ordergroessen von 6 bis 1.000 USDC ueber drei Groessenordnungen."""
    ledger = Ledger(starting_cash=Decimal("1000000"), specs={"BTCUSDC": BTC},
                     fee_bps=FEE_BPS, slippage_bps=SLIP_BPS)
    price = Decimal("81287.03")
    verletzungen_qty = verletzungen_tick = verletzungen_notional = 0
    for i in range(1000):
        target_usdc = Decimal(6) * (Decimal(1000) / Decimal(6)) ** (Decimal(i) / Decimal(999))
        pct = target_usdc / Decimal("1000000") * Decimal(100)
        p = Proposal("BTCUSDC", "BUY", position_pct=float(pct))
        v = _valuation(equity="1000000")
        result = size_order(p, v, BTC, price, FEE_BPS, SLIP_BPS, held_qty=Decimal(0))
        if isinstance(result, Rejection):
            continue
        if result.base_qty % BTC.step_size != 0:
            verletzungen_qty += 1
        candle_ts = 900_000 + i * 900_000
        candle = Candle(symbol="BTCUSDC", interval="15m", open_time=candle_ts,
                         close_time=candle_ts + 899_999, open=price, high=price, low=price,
                         close=price, volume=Decimal("1"), closed=True)
        fill = ledger.apply(result, candle)
        if isinstance(fill, Rejection):
            continue
        if fill.price % BTC.tick_size != 0:
            verletzungen_tick += 1
        if fill.gross_quote > target_usdc:
            verletzungen_notional += 1
    assert verletzungen_qty == 0
    assert verletzungen_tick == 0
    assert verletzungen_notional == 0


def test_a4b_effektive_mindestordergroesse_ist_real():
    """A-4b, gegen sizing.size_order() selbst (nicht gegen ledger.apply()):
    Ziel-Notional = min_notional + step*preis (K-3) wird in 500/500 Faellen zu einer
    Order, Ziel-Notional = min_notional + 0,5*step*preis in mindestens 200/500 Faellen
    zu einer Rejection mit der effektiven Grenze in der Begruendung.
    fee_bps=slippage_bps=0, damit exec_price exakt price ist und target_quote exakt
    dem gewuenschten Ziel-Notional entspricht (equity so gewaehlt, dass position_pct=1
    genau das Ziel ergibt)."""
    gefuellt_bei_eff = 0
    abgelehnt_bei_halb = 0
    ablehnungstexte = []
    for i in range(500):
        price = Decimal("60000") + Decimal(i) * (Decimal("60000") / Decimal(499))
        price = money.tick_down(price, BTC.tick_size)  # exec_price = tick_up(price) bleibt price
        eff = BTC.effective_min_notional(price)

        v_eff = _valuation(equity=str(eff * Decimal(100)))
        o_eff = size_order(Proposal("BTCUSDC", "BUY", position_pct=1), v_eff, BTC, price,
                            fee_bps=0.0, slippage_bps=0.0, held_qty=Decimal(0))
        if isinstance(o_eff, Order):
            gefuellt_bei_eff += 1

        halb_target = BTC.min_notional + Decimal("0.5") * BTC.step_size * price
        v_halb = _valuation(equity=str(halb_target * Decimal(100)))
        r_halb = size_order(Proposal("BTCUSDC", "BUY", position_pct=1), v_halb, BTC, price,
                             fee_bps=0.0, slippage_bps=0.0, held_qty=Decimal(0))
        if isinstance(r_halb, Rejection):
            abgelehnt_bei_halb += 1
            assert "garantiert ab" in r_halb.reason
            ablehnungstexte.append(r_halb.reason)

    assert gefuellt_bei_eff == 500
    assert abgelehnt_bei_halb >= 200
    assert len(ablehnungstexte) == abgelehnt_bei_halb


def test_a5_losgroessenverlust_ist_beziffert():
    """A-5: bei step=0.00001 und 1.000 Preisstufen um 81.287 USDC liegt der
    Rundungsrest einer 1.000-USDC-Order zwischen 0 und step*preis."""
    reste = []
    for i in range(1000):
        price = Decimal("80787") + Decimal(i) * Decimal("1")  # 1.000 Stufen um 81.287
        raw_qty = Decimal("1000") / price
        qty = money.step_down(raw_qty, BTC.step_size)
        rest = Decimal("1000") - qty * price
        reste.append(rest)
    assert max(reste) < Decimal("0.8134")
    assert max(reste) > Decimal("0.70")
    assert max(reste) / Decimal(1000) < Decimal("0.001")
    assert min(reste) >= Decimal("0")


def test_a8b_keine_versteckte_uhr_kein_versteckter_zufall():
    text = Path(__file__).resolve().parent.parent.joinpath("aitra", "sizing.py").read_text()
    assert re.search(r"time\.time|datetime\.(now|utcnow)|random\.", text) is None
