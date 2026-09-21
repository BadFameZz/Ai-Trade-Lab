from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal
from pathlib import Path

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
    """A-3: 1.000 Ordergroessen von 6 bis 1.000 USDC ueber drei Groessenordnungen.

    Prüft, dass size_order() korrekt quantisiert und die Notional nie überschritten wird.

    Jede Stichprobe bekommt ihr eigenes, frisch aufgesetztes Ledger mit reichlich Kasse
    (siehe FRESH_CASH). Fix-Runde 3 hatte noch EIN Ledger mit festem Startkapital über
    alle 1.000 Durchläufe geteilt; Rejections gegen Ende der Reihe waren dann ein Artefakt
    einer über die Iterationen schrumpfenden Kasse, keine Eigenschaft der geprüften
    Rundungslogik. Mit frischer Kasse je Stichprobe kann dieses Artefakt strukturell nicht
    mehr auftreten (Rot-Nachweis siehe Fix-Bericht Runde 4).

    Herleitung der Untergrenze (nicht aus dem Messwert gegriffen):
    - target_usdc läuft von 6 bis 1.000 USDC (siehe Formel unten).
    - exec_price = tick_up(price * (1 + slippage)) ist für den festen price dieses
      Tests ≈ 81.327,68 USDC; der maximale Quantisierungsverlust pro Order ist
      höchstens step_size * exec_price ≈ 0,00001 * 81.327,68 ≈ 0,813 USDC (siehe A-5).
    - Selbst am unteren Rand (target_usdc = 6 USDC) bleibt die tatsächliche Notional
      damit über min_notional = 5 USDC (6 - 0,813 ≈ 5,19 > 5) → MIN_NOTIONAL/MIN_QTY
      können strukturell nie greifen.
    - FRESH_CASH ist > 9.000-mal so groß wie die teuerste einzelne Order (≈ 1.001 USDC
      bei target_usdc = 1.000) → INSUFFICIENT_CASH ist strukturell ausgeschlossen.
    Damit müssen alle 1.000 Stichproben angenommen werden; die Grenze wird exakt bei
    1.000 gezogen, weil der Ablauf vollständig deterministisch ist (keine Uhr, kein
    Zufall) und jede Abweichung von 1.000 eine echte Regression wäre, keine Streuung.
    """
    price = Decimal("81287.03")
    verletzungen_qty = verletzungen_tick = verletzungen_notional = 0
    gepruefte_stichproben = 0
    rejections_sizing = rejections_ledger = 0
    rejection_codes: Counter[str] = Counter()

    # Deckt die teuerste Order (~1.001 USDC bei target_usdc=1000) um mehr als das
    # 9.000-fache -- Kassenknappheit ist damit je Stichprobe strukturell ausgeschlossen.
    FRESH_CASH = Decimal("10000000")

    for i in range(1000):
        target_usdc = Decimal(6) * (Decimal(1000) / Decimal(6)) ** (Decimal(i) / Decimal(999))
        pct = target_usdc / Decimal("1000000") * Decimal(100)
        p = Proposal("BTCUSDC", "BUY", position_pct=float(pct))
        v = _valuation(equity="1000000")
        result = size_order(p, v, BTC, price, FEE_BPS, SLIP_BPS, held_qty=Decimal(0))
        if isinstance(result, Rejection):
            rejections_sizing += 1
            rejection_codes[f"sizing:{result.code}"] += 1
            continue
        if result.base_qty % BTC.step_size != 0:
            verletzungen_qty += 1
        # Frisches Ledger je Stichprobe: keine geteilte, über die Iterationen
        # schrumpfende Kasse (siehe Docstring).
        ledger = Ledger(starting_cash=FRESH_CASH, specs={"BTCUSDC": BTC},
                         fee_bps=FEE_BPS, slippage_bps=SLIP_BPS)
        candle_ts = 900_000 + i * 900_000
        candle = Candle(symbol="BTCUSDC", interval="15m", open_time=candle_ts,
                         close_time=candle_ts + 899_999, open=price, high=price, low=price,
                         close=price, volume=Decimal("1"), closed=True)
        fill = ledger.apply(result, candle)
        if isinstance(fill, Rejection):
            rejections_ledger += 1
            rejection_codes[f"ledger:{fill.code}"] += 1
            continue
        if fill.price % BTC.tick_size != 0:
            verletzungen_tick += 1
        if fill.gross_quote > target_usdc:
            verletzungen_notional += 1
        gepruefte_stichproben += 1

    # Prüffläche muss bei/nahe 1.000 liegen; jede Abweichung wird mit den
    # Ablehnungscodes je Ursache gemeldet, nicht stillschweigend hingenommen.
    assert gepruefte_stichproben == 1000, (
        f"Prüffläche zu klein: nur {gepruefte_stichproben}/1000 Stichproben geprüft "
        f"(Rejections: sizing={rejections_sizing}, ledger={rejections_ledger}, "
        f"Codes={dict(rejection_codes)})"
    )

    assert verletzungen_qty == 0, f"{verletzungen_qty} Quantisierungsverletzungen"
    assert verletzungen_tick == 0, f"{verletzungen_tick} Tick-Verletzungen"
    assert verletzungen_notional == 0, f"{verletzungen_notional} Notional-Verletzungen"


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
    """A-5: size_order() mit Abwärtsrundung garantiert 0 <= Rest < step*preis.

    Prüft die Invariante an den **tatsächlichen Rückgaben** von size_order(),
    nicht an einer Parallelrechnung mit money.step_down(). Mit falscher Schrittweite
    wird die Invariante verletzt.

    Jede Stichprobe bekommt ihr eigenes, frisch aufgesetztes Ledger mit reichlich Kasse
    (siehe FRESH_CASH) — dasselbe Muster wie in test_a3_quantisierung_1000_stichproben
    (Fix-Runde 4). Vorher teilten sich alle 1.000 Durchläufe EIN Ledger mit
    starting_cash=1.000.000 USDC, während die Summe der Zielbeträge (1.000 × 1.000 USDC)
    rechnerisch exakt dort landet: gemessen blieben am Ende nur ≈408 USDC Kasse übrig
    (0,04 % Marge) — die Prüffläche stand strukturell auf der Kante, nicht mit
    Sicherheitsabstand. Ein Fehler, der den Verlust je Order geringfügig erhöht hätte,
    wäre nicht als Verletzung der Invariante, sondern als zufällige INSUFFICIENT_CASH-
    Rejection gegen Ende der Reihe sichtbar geworden — bzw. bei noch knapperer Kasse gar
    nicht mehr, weil die Prüffläche vorher schon unter die alte Grenze (>= 500) gefallen
    wäre, ohne dass jemand nach der Ursache gesucht hätte.

    Herleitung der neuen Untergrenze (aus dem Aufbau, nicht aus dem Messwert):
    - target_quote ist über alle 1.000 Durchläufe konstant 1.000 USDC, fee_bps=
      slippage_bps=0 → exec_price == price (price ist bereits ein Vielfaches von
      tick_size, tick_up ändert nichts).
    - Der maximale Quantisierungsverlust pro Order ist höchstens
      step_size × price ≈ 0,00001 × 81.786 ≈ 0,818 USDC (Preis läuft von 80.787 bis
      81.786) → gross_quote liegt immer zwischen ≈999,18 und 1.000 USDC.
    - 1.000 USDC liegt weit über min_notional=5 USDC und effective_min_notional
      (≈5,82 USDC) → MIN_NOTIONAL/MIN_QTY können strukturell nie greifen.
    - FRESH_CASH deckt die teuerste Einzelorder (≈1.000 USDC) um mehr als das
      9.000-fache ab → INSUFFICIENT_CASH ist strukturell ausgeschlossen.
    Damit müssen alle 1.000 Stichproben angenommen werden; die Grenze wird exakt bei
    1.000 gezogen, weil der Ablauf vollständig deterministisch ist (keine Uhr, kein
    Zufall) und jede Abweichung von 1.000 eine echte Regression wäre, keine Streuung.
    """
    max_rest = Decimal(0)
    max_rest_pct = Decimal(0)
    akzeptiert = 0
    rejections_sizing = rejections_ledger = 0
    rejection_codes: Counter[str] = Counter()

    # Deckt die teuerste Order (~1.000 USDC) um mehr als das 9.000-fache --
    # Kassenknappheit ist damit je Stichprobe strukturell ausgeschlossen.
    FRESH_CASH = Decimal("10000000")

    for i in range(1000):
        price = Decimal("80787") + Decimal(i) * Decimal("1")
        target_quote = Decimal("1000")
        equity_for_pct = target_quote * Decimal(100)  # position_pct=1 → genau target_quote

        p = Proposal("BTCUSDC", "BUY", position_pct=1)
        v = _valuation(equity=str(equity_for_pct), cash=str(equity_for_pct * Decimal(2)))

        result = size_order(p, v, BTC, price, fee_bps=0.0, slippage_bps=0.0, held_qty=Decimal(0))
        if isinstance(result, Rejection):
            rejections_sizing += 1
            rejection_codes[f"sizing:{result.code}"] += 1
            continue

        # Frisches Ledger je Stichprobe: keine geteilte, über die Iterationen
        # auf die Kante laufende Kasse (siehe Docstring).
        ledger = Ledger(starting_cash=FRESH_CASH, specs={"BTCUSDC": BTC},
                         fee_bps=0.0, slippage_bps=0.0)
        candle_ts = 900_000 + i * 900_000
        candle = Candle(symbol="BTCUSDC", interval="15m", open_time=candle_ts,
                         close_time=candle_ts + 899_999, open=price, high=price, low=price,
                         close=price, volume=Decimal("1"), closed=True)
        fill = ledger.apply(result, candle)
        if isinstance(fill, Rejection):
            rejections_ledger += 1
            rejection_codes[f"ledger:{fill.code}"] += 1
            continue

        # Invariante an tatsächlichen Fill-Werten
        rest = target_quote - fill.gross_quote
        assert rest >= Decimal(0), f"Rest negativ: {rest} bei Preis {fill.price}, qty={fill.qty}"

        max_allowed = BTC.step_size * fill.price
        assert rest < max_allowed, f"Rest {rest} >= {max_allowed} (step*price) bei Preis {fill.price}"

        max_rest = max(max_rest, rest)
        rest_pct = rest / target_quote * Decimal(100)
        max_rest_pct = max(max_rest_pct, rest_pct)
        akzeptiert += 1

    # Prüffläche muss bei/nahe 1.000 liegen; jede Abweichung wird mit den
    # Ablehnungscodes je Ursache gemeldet, nicht stillschweigend hingenommen.
    assert akzeptiert == 1000, (
        f"Prüffläche zu klein: nur {akzeptiert}/1000 Orders akzeptiert "
        f"(Rejections: sizing={rejections_sizing}, ledger={rejections_ledger}, "
        f"Codes={dict(rejection_codes)})"
    )
    assert max_rest_pct < Decimal("0.1"), f"Prozentualer Verlust {max_rest_pct}% > 0.1%"

    # UNTERE SCHRANKE (Fix-Welle, Review-Befund 11).
    # max_rest wurde bisher berechnet und nie geprueft. Mit der frueher
    # entfernten magischen Obergrenze (0.8129) ist damals auch die Untergrenze
    # der Spec verschwunden (A-5: max(rest) > 0.70). A-5 waere seither auch bei
    # einem Losgroessenverlust von exakt null gruen gewesen -- das Kriterium
    # heisst aber "der Verlust ist BEZIFFERT", nicht "der Verlust ist klein".
    #
    # Herleitung der Schranke aus dem Aufbau (keine Zahl aus dem Lauf):
    # - fee_bps = slippage_bps = 0 -> exec_price == price (price ist bereits ein
    #   Vielfaches von tick_size).
    # - qty = step_down(1000 / price, step), also
    #   rest = 1000 - qty * price = frac * step * price, wobei frac der
    #   Nachkommaanteil von (1000 / price) / step ist, frac aus [0, 1).
    # - Die 1.000 Preise laufen in 1-USDC-Schritten von 80.787 bis 81.786.
    #   (1000/p)/step aendert sich dabei um
    #   1000 * (1/80787 - 1/81786) / 0,00001 = 15,1 -- die Stichproben
    #   ueberstreichen also gut 15 volle Step-Intervalle mit rund 66 Punkten je
    #   Intervall. frac wird damit dicht und mehrfach voll durchlaufen; ein
    #   maximales frac unterhalb von 0,5 ist nur moeglich, wenn die
    #   Quantisierung nicht mehr abrundet.
    # - Die Schranke nimmt deshalb die Haelfte des kleinstmoeglichen
    #   step * price im Feld, also beim niedrigsten Preis 80.787:
    #   step_size * 80787 / 2. Das ist bewusst konservativ (die Spec nennt
    #   0,70; hergeleitet sind hier 0,4039) -- aber es ist hergeleitet.
    untere_schranke = BTC.step_size * Decimal("80787") / 2
    assert max_rest > untere_schranke, (
        f"Losgroessenverlust nicht beziffert: max(rest)={max_rest} <= {untere_schranke}. "
        f"A-5 verlangt einen messbaren Verlust, keinen verschwundenen."
    )


def test_a8b_keine_versteckte_uhr_kein_versteckter_zufall():
    text = Path(__file__).resolve().parent.parent.joinpath("aitra", "sizing.py").read_text()
    assert re.search(r"time\.time|datetime\.(now|utcnow)|random\.", text) is None
