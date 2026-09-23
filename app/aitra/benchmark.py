"""BTC Buy & Hold: die Referenzgroesse, gerechnet durch dasselbe Ledger (Spec 7.1).

Ruft die Ledger-Fuellung NICHT direkt auf (A-6b) — siehe die dokumentierte
Abweichung von Spec 3.1 im Plan (task-9-brief.md). Stattdessen wie jede
Strategie ueber execute.execute_proposal(), mit einer eigenen, durchlaessigen
RiskEngine, weil der Benchmark kein echtes Risiko traegt, sondern nur eine
Vergleichsgroesse berechnet.
"""
from __future__ import annotations

import sqlite3
from dataclasses import replace
from decimal import Decimal
from typing import Mapping

from . import money, store_run
from .config import Config
from .execute import ExecutionContext, execute_proposal
from .ledger import Ledger, Position
from .marketdata import Candle, Clock
from .risk import Proposal, RiskEngine


class BuyAndHold:
    """Kauft (fast) das gesamte Startkapital, und haelt danach nur noch.

    Kassenmarge (Fix-Runde 1): ref_price MUSS prev_candle.close sein, niemals
    candle.open/close/high/low — sonst kennt die Groessenbemessung den
    tatsaechlichen Fuellpreis, bevor er feststeht (Blick in die Zukunft,
    Verstoss gegen E-001/E-006; decide_fn darf t+1 nicht sehen). Bei
    position_pct=100 bleibt dann aber keine Kassenmarge fuer die
    Preisluecke zwischen der Entscheidungskerze (ref_price) und der
    tatsaechlichen Fuellkerze (candle.open, E-006): gemessen (nicht geraten)
    reicht ein Kursanstieg von nur 0,016 % zwischen den beiden Kerzen bereits,
    um mit position_pct=100 in INSUFFICIENT_CASH zu laufen — bei exakt
    gleichem Kurs (Luecke = 0) geht ein 100-%-Kauf dagegen glatt auf, siehe
    Herleitung im Plan-Bericht. Da die Luecke zwischen zwei Kerzen im Voraus
    nicht bekannt ist, reserviert BuyAndHold eine Marge, die sich an den
    beiden einzigen hier bekannten Kostengroessen orientiert: fee_bps und
    slippage_bps. _MARGIN_FACTOR=2 heisst: einmal deren Summe als Puffer fuer
    die Preisluecke selbst, ein zweites Mal als zusaetzliche Luft, weil beide
    Kerzen (Entscheidung und Fuellung) je eine eigene Slippage-Berechnung
    durchlaufen. Reicht die Marge fuer eine konkrete Kerze trotzdem nicht,
    bleibt `bought` False, und der naechste Aufruf von on_candle() versucht
    es erneut (Buy & Hold ist eine Kerze Verzoegerung gleichgueltig).
    """

    _MARGIN_FACTOR = 2

    def __init__(
        self,
        cfg: Config,
        conn: sqlite3.Connection,
        run_id: str,
        symbol: str,
        spec: money.SymbolSpec,
        fee_bps: float,
        slippage_bps: float,
        clock: Clock,
    ) -> None:
        permissive_cfg = replace(
            cfg, max_position_pct=100, max_daily_loss_pct=100, max_total_exposure_pct=100,
        )
        ledger = Ledger(
            starting_cash=cfg.starting_balance, specs={symbol: spec},
            fee_bps=fee_bps, slippage_bps=slippage_bps,
        )
        # B-D5: Zustand aus dem Journal, nicht aus dem Speicher (A-14, genau
        # wie poller.build_context() es fuer das Live-Ledger tut). Ohne das
        # fing der Benchmark nach jedem Prozessstart wieder bei
        # starting_balance an und kaufte ein zweites Mal — dreimal in der
        # laufenden Anlage gemessen (CT 107, 2026-09-23), Benchmark-Equity
        # 9.950,98 statt 9.817,04 (1,3643 % zu hoch). Alpha gegen einen
        # Vergleichsgegner, der sich bei jedem Neustart zurueckstellt, ist
        # bedeutungslos.
        #
        # Leeres Journal heisst Frischstart, und das ist der RICHTIGE Fall
        # fuer replay.run_replay(): jeder Zeitraffer ist ein eigener Lauf
        # (bench-<run_id>) und beginnt bei starting_balance. Beide Faelle
        # bedient derselbe Zweig, weil ein frischer Lauf keine Fills hat.
        fills = store_run.get_fills(conn, run_id)
        if fills:
            positionen = {s: Position(s, p["qty"], p["avg_price"], p["realized_pnl"])
                          for s, p in store_run.get_positions(conn, run_id).items()}
            ledger.restore(positionen, fills[-1]["cash_after"])
            # Ledger.restore() laesst last_marks bewusst leer (E-010/Weg A).
            # Fuer eine GEHALTENE Position ist das hier gefaehrlich: mark()
            # wirft, wenn zu ihr kein Preis vorliegt, und poller.poll_once()
            # reicht dem Benchmark nur die Preise durch, die es in dieser
            # Runde gibt — bleibt die Kerze des Benchmark-Symbols einmal aus
            # (Abruf-Fehlschlag, waehrend ein anderes Symbol liefert), risse
            # das den ganzen Zyklus mit. Deshalb ein Ausgangspreis aus dem
            # Journal: der avg_price der wiederhergestellten Position. Das ist
            # NICHT derselbe Wert, den apply() ablegt — apply() setzt
            # last_marks[symbol] auf candle_next.open, also VOR Slippage
            # (ledger.py:127), waehrend avg_price der Fuellpreis INKLUSIVE
            # Slippage ist. Gemessen (fee 10 bps, slippage 5 bps, Kauf auf
            # einer 100.000er-Kerze): 100.000,00 gegen 100.050,00, also rund
            # slippage_bps auseinander. Ein genauerer Ausgangspreis ist aus dem
            # Journal nicht rekonstruierbar, und er muss auch nicht genau sein:
            # er gilt hoechstens eine Runde. mark() ist rein (keine DB,
            # keine Uhr); der Rueckgabewert wird nicht gebraucht, gesetzt wird
            # nur der Ausgangspreis, den die erste echte Kerze ueberschreibt —
            # equity() laesst den frischen Preis immer vorgehen.
            ledger.mark({s: p.avg_price for s, p in positionen.items() if p.qty != 0},
                        ts_ms=fills[-1]["candle_open_time"])
        self._ctx = ExecutionContext(
            conn=conn, run_id=run_id, ledger=ledger, engine=RiskEngine(permissive_cfg),
            specs={symbol: spec}, fee_bps=fee_bps, slippage_bps=slippage_bps, clock=clock,
        )
        self._symbol = symbol
        self._bought = bool(fills)
        margin_pct = self._MARGIN_FACTOR * (fee_bps + slippage_bps) / 100.0
        self._position_pct = max(1.0, 100.0 - margin_pct)

    @property
    def bought(self) -> bool:
        """Ob der Kauf bereits ausgefuehrt wurde.

        Beim Bau aus dem Journal abgeleitet (B-D5), nicht aus dem Speicher:
        Buy & Hold verkauft nie, ein Fill unter dieser run_id heisst also
        gekauft. Ein neu gestarteter Prozess weiss damit dasselbe wie der
        alte (A-14).
        """
        return self._bought

    def on_candle(self, candle: Candle, prev_candle: Candle | None) -> None:
        """Versucht den einmaligen Kauf, fruehestens auf der zweiten Kerze des Laufs.

        prev_candle is None heisst: erste Kerze des Laufs, es gibt noch keine
        Vorgaengerkerze fuer ref_price. ref_price ist immer prev_candle.close
        (niemals ein Feld von candle) — die Groessenbemessung darf den
        Fuellpreis der aktuellen Kerze nicht kennen (E-001/E-006). Schlaegt
        der Kauf an einer Kerze fehl (Kassenmarge reicht nicht), bleibt
        `bought` False, und der naechste Aufruf versucht es erneut.
        """
        if self._bought or prev_candle is None:
            return
        equity_now = self._ctx.ledger.mark({}, ts_ms=prev_candle.close_time).equity
        result = execute_proposal(
            Proposal(symbol=self._symbol, action="BUY", position_pct=self._position_pct),
            self._ctx,
            marks={}, ts_ms=prev_candle.close_time, ref_price=prev_candle.close,
            start_of_day_equity=equity_now, next_candle=candle,
            strategy_version="benchmark-buy-and-hold",
            reason="BTC Buy & Hold Referenz (Spec 7.1)",
        )
        if result.status == "filled":
            self._bought = True

    def equity(self, marks: Mapping[str, Decimal], ts_ms: int) -> Decimal:
        """Bewertet Kasse + Position zu den gegebenen Marktpreisen.

        Fehlende Preise werden ausdruecklich aus last_marks ergaenzt — genau
        wie poller.poll_once() es fuer das Live-Ledger tut (ledger.mark()
        verlangt das an der Aufrufstelle, siehe dessen Docstring). Die
        uebergebenen Preise gehen immer vor; last_marks traegt nur den letzten
        bekannten Stand fuer eine Runde, in der zu diesem Symbol keine Kerze
        ankam.
        """
        return self._ctx.ledger.mark({**self._ctx.ledger.last_marks, **marks}, ts_ms).equity
