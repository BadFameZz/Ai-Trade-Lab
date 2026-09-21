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

from . import money
from .config import Config
from .execute import ExecutionContext, execute_proposal
from .ledger import Ledger
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
        self._ctx = ExecutionContext(
            conn=conn, run_id=run_id, ledger=ledger, engine=RiskEngine(permissive_cfg),
            specs={symbol: spec}, fee_bps=fee_bps, slippage_bps=slippage_bps, clock=clock,
        )
        self._symbol = symbol
        self._bought = False
        margin_pct = self._MARGIN_FACTOR * (fee_bps + slippage_bps) / 100.0
        self._position_pct = max(1.0, 100.0 - margin_pct)

    @property
    def bought(self) -> bool:
        """Ob der Kauf bereits ausgefuehrt wurde."""
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
        """Bewertet Kasse + Position zu den gegebenen Marktpreisen."""
        return self._ctx.ledger.mark(marks, ts_ms).equity
