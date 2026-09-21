"""Harte Risiko-Engine.

Deterministisch und bewusst getrennt von jeder Strategie- oder KI-Logik:
Eine Strategie darf nur *vorschlagen*, diese Engine entscheidet.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Mapping

from .config import Config

SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,12}(USDC|USDT|EUR|BTC)$")
SPOT_ACTIONS = {"BUY", "SELL", "WAIT"}


@dataclass(frozen=True)
class Proposal:
    symbol: str
    action: str               # BUY | SELL | WAIT
    position_pct: float = 0   # Anteil am Portfolio in %
    confidence: float | None = None


@dataclass(frozen=True)
class PortfolioState:
    equity: float
    start_of_day_equity: float
    exposure_pct: float       # aktuell investierter Anteil in %
    position_pct_by_symbol: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    code: str
    reason: str


class RiskEngine:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def daily_loss_pct(self, pf: PortfolioState) -> float:
        # equity/start_of_day_equity koennen float (Bestandstests) oder Decimal
        # (Ledger.to_portfolio_state) sein. Decimal(str(x)) normalisiert beides,
        # ohne ueber float neu zu erzeugen, was aus einem echten Decimal-Geldwert
        # Praezision herausschneiden wuerde.
        sod = Decimal(str(pf.start_of_day_equity))
        eq = Decimal(str(pf.equity))
        if sod <= 0:
            return 0.0
        return float(max(Decimal(0), (sod - eq) / sod * Decimal(100)))

    def check(self, p: Proposal, pf: PortfolioState, kill_switch: bool) -> RiskDecision:
        action = p.action.upper()

        if action not in SPOT_ACTIONS:
            return RiskDecision(False, "NOT_SPOT", f"Aktion {p.action!r} nicht erlaubt – nur Spot BUY/SELL/WAIT")
        if not SYMBOL_RE.match(p.symbol):
            return RiskDecision(False, "BAD_SYMBOL", f"Symbol {p.symbol!r} ungültig")
        if action == "WAIT":
            return RiskDecision(True, "NO_ORDER", "Keine Order – Entscheidung wird nur protokolliert")

        if kill_switch:
            return RiskDecision(False, "KILL_SWITCH", "Kill Switch aktiv – neue Orders blockiert")
        if self.cfg.trading_mode != "PAPER" or not self.cfg.live_locked:
            return RiskDecision(False, "MODE", "Nur Paper-Trading erlaubt")

        loss = self.daily_loss_pct(pf)
        if loss >= self.cfg.max_daily_loss_pct:
            return RiskDecision(
                False, "DAILY_LOSS",
                f"Tagesverlust {loss:.2f} % ≥ Limit {self.cfg.max_daily_loss_pct:g} %",
            )

        if not (0 < p.position_pct <= 100):
            return RiskDecision(False, "BAD_SIZE", "Positionsgröße muss zwischen 0 und 100 % liegen")
        if action == "BUY" and p.position_pct > self.cfg.max_position_pct:
            return RiskDecision(
                False, "MAX_POSITION",
                f"Position {p.position_pct:g} % > Limit {self.cfg.max_position_pct:g} %",
            )
        if action == "BUY" and pf.exposure_pct + p.position_pct > self.cfg.max_total_exposure_pct:
            return RiskDecision(
                False, "MAX_EXPOSURE",
                f"Gesamtrisiko {pf.exposure_pct + p.position_pct:g} % > Limit {self.cfg.max_total_exposure_pct:g} %",
            )
        if action == "SELL":
            # Befund B-1: gegen die Position IM SYMBOL pruefen, nicht gegen die
            # Gesamtexposition. pf.exposure_pct waere hier falsch: ein Verkauf in
            # BTCUSDC darf nicht durchgehen, nur weil ETHUSDC genug Gesamtrisiko traegt.
            held_pct = pf.position_pct_by_symbol.get(p.symbol, 0.0)
            if p.position_pct > held_pct:
                return RiskDecision(False, "NO_POSITION", "Verkauf größer als vorhandene Position (kein Short)")

        return RiskDecision(True, "OK", "Alle Risikoregeln erfüllt")
