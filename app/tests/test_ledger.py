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
    buy = ledger.apply(Order("BTCUSDC", "BUY", Decimal("0.02")), _candle("81287.03"))
    assert isinstance(buy, Fill)
    f = ledger.apply(Order("BTCUSDC", "SELL", Decimal("0.01")), _candle("81400.00", open_time=1_800_000))
    assert isinstance(f, Fill)
    # s=0.0005: exec = tick_down(81400*0.9995) = tick_down(81359.30) = 81359.30
    assert f.price == Decimal("81359.30")
    assert f.side == "SELL"
    # gross = 81359.30*0.01 = 813.5930, fee = round_up(813.5930*0.0010, 8dp) = 0.81359300
    # net_quote = gross - fee (SELL: Gebuehr wird abgezogen, nicht addiert)
    assert f.gross_quote == Decimal("813.5930")
    assert f.fee == Decimal("0.81359300")
    assert f.net_quote == Decimal("812.77940700")
    assert f.cash_after == buy.cash_after + f.net_quote
    assert f.cash_after == Decimal("9184.59925340")


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

    Fix-Runde 1 (Spec Abschnitt 12, A-1): Pruefung 1 (v.equity == v.cash +
    Positionswert) liest beide Seiten aus denselben Objekten und ist damit
    tautologisch gegenueber Buchungsfehlern in apply() - ein solcher Fehler
    verschiebt cash und equity gleichermassen, die Gleichung haelt trotzdem.
    Pruefung 2 rekonstruiert die Endkasse unabhaengig aus den net_quote-Werten
    der von apply() zurueckgegebenen Fill-Objekte (nicht aus self._cash) und
    faengt damit auch Buchungsfehler und schleichende Rundungsabweichungen,
    die sich erst ueber tausende Fills aufsummieren. Pruefung 1 bleibt
    daneben stehen, sie wird nicht ersetzt.
    """
    ledger = _ledger()
    cash_start = ledger.cash
    summe_net_buy = Decimal("0")
    summe_net_sell = Decimal("0")
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
        if result.side == "BUY":
            summe_net_buy += result.net_quote
        else:
            summe_net_sell += result.net_quote
        v = ledger.mark({"BTCUSDC": price, "ETHUSDC": price}, ts_ms=ts)
        held_value = sum(ledger.position(s).qty * price for s in ("BTCUSDC", "ETHUSDC"))
        # Pruefung 1: tautologisch (beide Seiten aus mark()/self._cash), zeigt
        # nur, dass mark() sich selbst nicht widerspricht.
        assert v.equity - (v.cash + held_value) == Decimal("0")
        # A-2 (Fix-Welle, Review-Befund 4): gefordert ist min(cash) >= 0 und
        # min(qty) >= 0 IN DENSELBEN 10.000 Fills, nicht nur am Ende. Eine
        # zwischenzeitlich negative Kasse, die sich bis zum letzten Fill wieder
        # erholt, war vorher unsichtbar.
        #
        # EHRLICHE EINORDNUNG (gemessen, nicht vermutet): Diese beiden Zeilen
        # sind hier nicht rot zu bekommen. Der Treiber oben deckelt den Kauf
        # selbst auf 95 % der Kasse und den Verkauf selbst auf pos.qty; eine
        # negative Kasse bzw. Menge ist damit strukturell ausgeschlossen,
        # unabhaengig vom Produktivcode. Nachgemessen im Container:
        #   - beide INSUFFICIENT_CASH-Waechter (sizing.py + ledger.py) entfernt
        #     -> dieser Test bleibt gruen (1 passed)
        #   - der Bestandswaechter (qty > pos.qty) in ledger.py entfernt
        #     -> dieser Test bleibt gruen (1 passed)
        # Die belastbare A-2-Messung ist deshalb
        # test_execute.py::test_a2_kasse_und_mengen_nichtnegativ_ueber_execute_proposal,
        # wo sizing.py die Menge bestimmt: dort schlaegt dieselbe Entfernung mit
        # "Kasse -482.74576514 < 0 bei Schritt 55" fehl.
        assert ledger.cash >= Decimal("0"), f"Kasse negativ nach Fill {treffer}: {ledger.cash}"
        assert min(ledger.position(s).qty for s in ("BTCUSDC", "ETHUSDC")) >= Decimal("0"), (
            f"Menge negativ nach Fill {treffer}: "
            f"{[(s, ledger.position(s).qty) for s in ('BTCUSDC', 'ETHUSDC')]}"
        )
    assert treffer >= 9000  # die Pruefflaeche darf nicht leer sein (gemessen: 9989/10000)
    assert ledger.cash >= Decimal("0")
    assert ledger.position("BTCUSDC").qty >= Decimal("0")
    assert ledger.position("ETHUSDC").qty >= Decimal("0")
    # Pruefung 2 (Spec 12, A-1): Endkasse unabhaengig aus den Fill-net_quote-
    # Werten rekonstruiert, nicht aus self._cash - nicht tautologisch.
    rekonstruiert = cash_start - summe_net_buy + summe_net_sell
    assert ledger.cash - rekonstruiert == Decimal("0")


def test_a8b_keine_versteckte_uhr_kein_versteckter_zufall():
    text = Path(__file__).resolve().parent.parent.joinpath("aitra", "ledger.py").read_text()
    assert re.search(r"time\.time|datetime\.(now|utcnow)|random\.", text) is None


def test_to_portfolio_state_gibt_position_pct_by_symbol_weiter():
    ledger = _ledger()
    ledger.apply(Order("BTCUSDC", "BUY", Decimal("0.05")), _candle("81287.03"))
    v = ledger.mark({"BTCUSDC": Decimal("82000")}, ts_ms=1_800_000)
    pf = ledger.to_portfolio_state(v, start_of_day_equity=Decimal("10000"))
    assert pf.position_pct_by_symbol == v.position_pct_by_symbol
    assert pf.position_pct_by_symbol["BTCUSDC"] > 0


def test_mark_wirft_bei_gehaltener_position_ohne_marktpreis():
    """Fix-Welle, Review-Befund 5: mark() uebersprang Symbole ohne Mark
    stillschweigend. Dann gilt equity = cash + Summe(qty * mark) nicht mehr --
    die unbewertete Position faellt aus der Equity und erzeugt einen
    Scheinverlust. Jetzt wirft mark() statt still falsch zu rechnen."""
    ledger = _ledger()
    ledger.apply(Order("BTCUSDC", "BUY", Decimal("0.01")), _candle("81287.03"))

    with pytest.raises(ValueError, match="Kein Marktpreis für gehaltene Position BTCUSDC"):
        ledger.mark({}, ts_ms=1_800_000)
    with pytest.raises(ValueError, match="BTCUSDC"):
        ledger.mark({"ETHUSDC": Decimal("2631.77")}, ts_ms=1_800_000)

    # Mit Preis geht es unveraendert durch, und die Identitaet haelt.
    v = ledger.mark({"BTCUSDC": Decimal("82000")}, ts_ms=1_800_000)
    assert v.equity == v.cash + ledger.position("BTCUSDC").qty * Decimal("82000")


def test_mark_bleibt_still_bei_geschlossener_position():
    """Eine auf 0 verkaufte Position braucht keinen Marktpreis -- sie traegt
    nichts zur Equity bei. Sonst waere die neue Strenge eine Falle fuer jeden
    Lauf, der ein Symbol einmal gehandelt und wieder geschlossen hat."""
    ledger = _ledger()
    ledger.apply(Order("BTCUSDC", "BUY", Decimal("0.01")), _candle("81287.03"))
    ledger.apply(Order("BTCUSDC", "SELL", Decimal("0.01")), _candle("81287.03", open_time=1_800_000))
    assert ledger.position("BTCUSDC").qty == Decimal("0")
    v = ledger.mark({}, ts_ms=2_700_000)  # wirft nicht
    assert v.equity == v.cash


def test_last_marks_merkt_sich_fill_und_mark_preise():
    """last_marks ist die Grundlage dafuer, dass Aufrufer mit nur einem
    bekannten Preis (execute.resolve_pending()) die uebrigen Positionen
    ausdruecklich statt stillschweigend bewerten koennen."""
    ledger = _ledger()
    assert ledger.last_marks == {}
    ledger.apply(Order("BTCUSDC", "BUY", Decimal("0.01")), _candle("81287.03"))
    assert ledger.last_marks["BTCUSDC"] == Decimal("81287.03")  # candle.open, ohne Slippage
    ledger.mark({"BTCUSDC": Decimal("82000")}, ts_ms=1_800_000)
    assert ledger.last_marks["BTCUSDC"] == Decimal("82000")
    ledger.last_marks["BTCUSDC"] = Decimal("1")  # Kopie, kein Durchgriff
    assert ledger.last_marks["BTCUSDC"] == Decimal("82000")
