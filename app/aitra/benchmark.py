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
    """Kauft genau einmal, mit dem gesamten Startkapital, und haelt danach nur."""

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

    @property
    def bought(self) -> bool:
        """Ob der einmalige Kauf bereits ausgefuehrt wurde."""
        return self._bought

    def on_candle(self, candle: Candle, prev_candle: Candle | None) -> None:
        """Kauft genau einmal, auf der zweiten Kerze des Laufs (Spec 7.1).

        prev_candle is None heisst: erste Kerze des Laufs, es gibt noch keine
        Folgekerze zum Fuellen. Jeder weitere Aufruf nach dem Kauf ist ein Noop.
        """
        if self._bought or prev_candle is None:
            return
        # ref_price = candle.open (die Kerze, gegen die auch gefuellt wird, nicht
        # prev_candle.close): bei position_pct=100 bleibt keine Kassenmarge fuer
        # eine Preisdifferenz zwischen Entscheidungs- und Fuellpreis. Mit
        # prev_candle.close als ref_price kollidiert das Sizing (das von diesem
        # Preis ausgeht) mit dem tatsaechlichen Fuellpreis aus candle.open beim
        # Fuellen und fuehrt bei steigendem Kurs zu INSUFFICIENT_CASH. candle
        # liegt hier vollstaendig vor (geschlossene Kerze, kein Vorgriff auf die
        # Zukunft) - der Benchmark kauft ohnehin sofort, ohne die
        # Entscheidungsverzoegerung realer Strategien zu simulieren.
        equity_now = self._ctx.ledger.mark({}, ts_ms=prev_candle.close_time).equity
        execute_proposal(
            Proposal(symbol=self._symbol, action="BUY", position_pct=100),
            self._ctx,
            marks={}, ts_ms=prev_candle.close_time, ref_price=candle.open,
            start_of_day_equity=equity_now, next_candle=candle,
            strategy_version="benchmark-buy-and-hold",
            reason="BTC Buy & Hold Referenz (Spec 7.1)",
        )
        self._bought = True

    def equity(self, marks: Mapping[str, Decimal], ts_ms: int) -> Decimal:
        """Bewertet Kasse + Position zu den gegebenen Marktpreisen."""
        return self._ctx.ledger.mark(marks, ts_ms).equity
