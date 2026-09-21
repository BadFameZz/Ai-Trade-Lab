"""Positionsgroessenbestimmung: Prozent -> quantisierte Menge (Spec 6.2).

Rein (E-001/A-8b): kein Netz, keine DB, keine Uhr, kein Zufall. ref_price kommt
immer als Parameter vom Aufrufer (execute.py nimmt dafuer candle.open der aktuellen,
nicht der Folgekerze — siehe E-006).
"""
from __future__ import annotations

import decimal
from decimal import Decimal

from . import money
from .ledger import Order, Rejection, Valuation
from .risk import Proposal

_BPS = Decimal(10_000)
_ONE = Decimal(1)


def size_order(
    proposal: Proposal,
    valuation: Valuation,
    spec: money.SymbolSpec,
    ref_price: Decimal,
    fee_bps: float,
    slippage_bps: float,
    held_qty: Decimal,
) -> Order | Rejection:
    """Berechnet eine quantisierte Order aus einem Positionsvorschlag.

    Berechnet die Menge basierend auf:
    - Ziel-Quote = Equity × position_pct / 100
    - Ausführungspreis mit Slippage und Tick-Quantisierung
    - Menge mit Gebühr und Step-Quantisierung (immer abgerundet)

    Prüft Mindest- und Maximal-Beschränkungen.
    """
    with decimal.localcontext(money.CTX):
        s = Decimal(str(slippage_bps)) / _BPS
        f = Decimal(str(fee_bps)) / _BPS
        target_quote = valuation.equity * Decimal(str(proposal.position_pct)) / Decimal(100)

        if proposal.action == "BUY":
            exec_price = money.tick_up(ref_price * (_ONE + s), spec.tick_size)
            raw_qty = target_quote / (exec_price * (_ONE + f))
        elif proposal.action == "SELL":
            if held_qty <= 0:
                return Rejection("NO_POSITION", f"Keine Position in {proposal.symbol}")
            exec_price = money.tick_down(ref_price * (_ONE - s), spec.tick_size)
            raw_qty = min(target_quote / exec_price, held_qty)
        else:
            raise ValueError(f"size_order erwartet BUY oder SELL, nicht {proposal.action!r}")

        qty = money.step_down(raw_qty, spec.step_size)
        if qty < spec.min_qty:
            return Rejection("MIN_QTY", f"Menge {qty} unter min_qty {spec.min_qty}")

        notional = qty * exec_price
        if notional < spec.min_notional:
            eff = spec.effective_min_notional(exec_price)
            return Rejection(
                "MIN_NOTIONAL",
                f"{notional} USDC unter min_notional {spec.min_notional} USDC; "
                f"garantiert ab {eff} USDC",
            )

        if proposal.action == "BUY":
            cost = notional * (_ONE + f)
            if cost > valuation.cash:
                return Rejection(
                    "INSUFFICIENT_CASH", f"{cost} USDC benötigt, {valuation.cash} USDC verfügbar"
                )

        return Order(symbol=proposal.symbol, side=proposal.action, base_qty=qty)
