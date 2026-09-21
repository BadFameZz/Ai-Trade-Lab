"""Das Paper-Ledger: Fills, Kasse, Positionen, Bewertung.

Rein (E-001): kein Netz, keine DB, keine Uhr, kein Zufall - jeder Zeitstempel
ist ein Parameter. apply() entnimmt der Folgekerze ausschliesslich open und
open_time (E-006), high/low/close bleiben ungenutzt.
"""
from __future__ import annotations

import decimal
from dataclasses import dataclass
from decimal import ROUND_UP, Decimal
from typing import Mapping, Sequence

from . import money, risk
from .marketdata import Candle

_ONE = Decimal(1)
_BPS = Decimal(10_000)


@dataclass(frozen=True)
class Order:
    symbol: str
    side: str  # "BUY" | "SELL"
    base_qty: Decimal


@dataclass(frozen=True)
class Fill:
    symbol: str
    side: str
    price: Decimal
    qty: Decimal
    gross_quote: Decimal
    fee: Decimal
    net_quote: Decimal
    cash_after: Decimal
    candle_open_time: int
    fee_bps: float
    slippage_bps: float


@dataclass(frozen=True)
class Rejection:
    code: str
    reason: str


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: Decimal
    avg_price: Decimal
    realized_pnl: Decimal


@dataclass(frozen=True)
class Valuation:
    ts_ms: int
    cash: Decimal
    position_value: Decimal
    equity: Decimal
    exposure_pct: float
    position_pct_by_symbol: dict[str, float]


def _fee_round_up(amount: Decimal, quote_precision: int) -> Decimal:
    unit = Decimal(1).scaleb(-quote_precision)
    return amount.quantize(unit, rounding=ROUND_UP)


class Ledger:
    """Fuehrt Kasse und Positionen ueber apply()/mark()-Aufrufe; jeder Zeitstempel
    kommt vom Aufrufer (Candle.open_time bzw. der ts_ms-Parameter von mark())."""

    def __init__(
        self,
        starting_cash: Decimal,
        specs: Mapping[str, money.SymbolSpec],
        fee_bps: float,
        slippage_bps: float,
    ) -> None:
        self._cash = starting_cash
        self._specs = specs
        self._fee_bps = fee_bps
        self._slippage_bps = slippage_bps
        self._positions: dict[str, Position] = {}
        # Zuletzt gesehener Marktpreis je Symbol, aus mark() und aus jedem Fill.
        # mark() verlangt fuer jede gehaltene Position einen Preis; dieser
        # Speicher erlaubt Aufrufern, die nur den Preis EINES Symbols kennen
        # (z. B. execute.resolve_pending() beim Eintreffen einer Kerze), die
        # uebrigen Positionen ausdruecklich mit ihrem letzten bekannten Kurs zu
        # bewerten -- statt sie stillschweigend aus der Equity fallen zu lassen.
        self._last_marks: dict[str, Decimal] = {}

    @property
    def cash(self) -> Decimal:
        """Aktueller Kassenstand."""
        return self._cash

    @property
    def last_marks(self) -> dict[str, Decimal]:
        """Kopie der zuletzt gesehenen Marktpreise je Symbol (siehe __init__)."""
        return dict(self._last_marks)

    def position(self, symbol: str) -> Position:
        """Liefert die Position zu symbol, oder eine Nullposition falls keine gehalten wird."""
        return self._positions.get(symbol, Position(symbol, Decimal(0), Decimal(0), Decimal(0)))

    def apply(self, order: Order, candle_next: Candle) -> Fill | Rejection:
        """Fuehrt eine Order gegen candle_next.open/open_time aus (E-006); liefert
        entweder einen Fill oder eine Rejection, niemals beides, nie None."""
        with decimal.localcontext(money.CTX):
            spec = self._specs.get(order.symbol)
            if spec is None:
                return Rejection("NO_SPEC", f"Keine SymbolSpec für {order.symbol}")

            # Defensiv erneut quantisieren (idempotent): ein Fehler in sizing.py
            # fuehrt hier nur zur Ablehnung, nie zu einem falschen Fill.
            qty = money.step_down(order.base_qty, spec.step_size)
            if qty <= 0:
                return Rejection("ZERO_QTY", "Menge nach Quantisierung 0")

            s = Decimal(str(self._slippage_bps)) / _BPS
            f = Decimal(str(self._fee_bps)) / _BPS
            p = candle_next.open  # E-006: ausschliesslich open + open_time der Folgekerze
            self._last_marks[order.symbol] = p
            if order.side == "BUY":
                exec_price = money.tick_up(p * (_ONE + s), spec.tick_size)
            elif order.side == "SELL":
                exec_price = money.tick_down(p * (_ONE - s), spec.tick_size)
            else:
                raise ValueError(f"Unbekannte Seite {order.side!r}")

            if qty < spec.min_qty:
                return Rejection("MIN_QTY", f"Menge {qty} unter min_qty {spec.min_qty}")

            gross = exec_price * qty
            if gross < spec.min_notional:
                eff = spec.effective_min_notional(exec_price)
                msg = f"{gross} USDC unter min_notional {spec.min_notional} USDC; garantiert ab {eff} USDC"
                return Rejection("MIN_NOTIONAL", msg)

            fee = _fee_round_up(gross * f, spec.quote_precision)

            if order.side == "BUY":
                net = gross + fee
                if net > self._cash:
                    return Rejection("INSUFFICIENT_CASH", f"{net} USDC benötigt, {self._cash} USDC verfügbar")
                self._cash = self._cash - net
                self._book_buy(order.symbol, qty, exec_price)
            else:
                pos = self.position(order.symbol)
                if pos.qty <= 0:
                    return Rejection("NO_POSITION", f"Keine Position in {order.symbol}")
                if qty > pos.qty:
                    return Rejection("NO_POSITION", f"Verkauf {qty} > Bestand {pos.qty}")
                net = gross - fee
                self._cash = self._cash + net
                self._book_sell(order.symbol, qty, exec_price)

            return Fill(
                symbol=order.symbol, side=order.side, price=exec_price, qty=qty,
                gross_quote=gross, fee=fee, net_quote=net, cash_after=self._cash,
                candle_open_time=candle_next.open_time, fee_bps=self._fee_bps,
                slippage_bps=self._slippage_bps,
            )

    def _book_buy(self, symbol: str, qty: Decimal, price: Decimal) -> None:
        pos = self.position(symbol)
        new_qty = pos.qty + qty
        # mengengewichteter Durchschnittspreis (A-14)
        new_avg = (pos.qty * pos.avg_price + qty * price) / new_qty if new_qty > 0 else Decimal(0)
        self._positions[symbol] = Position(symbol, new_qty, new_avg, pos.realized_pnl)

    def _book_sell(self, symbol: str, qty: Decimal, price: Decimal) -> None:
        pos = self.position(symbol)
        realized = (price - pos.avg_price) * qty
        new_qty = pos.qty - qty
        new_avg = pos.avg_price if new_qty > 0 else Decimal(0)
        self._positions[symbol] = Position(symbol, new_qty, new_avg, pos.realized_pnl + realized)

    def mark(self, marks: Mapping[str, Decimal], ts_ms: int) -> Valuation:
        """Bewertet Kasse und Positionen zu den gegebenen Marktpreisen.

        Fehlt zu einer gehaltenen Position der Marktpreis, wirft mark() --
        frueher wurde die Position stillschweigend uebersprungen. Dann gilt die
        Identitaet equity = cash + Summe(qty * mark) nicht mehr: die
        unbewertete Position faellt aus der Equity und erzeugt einen
        Scheinverlust, der ueber to_portfolio_state() bis in die
        Tagesverlustgrenze durchschlaegt (Kill Switch aus dem Nichts). Wer nur
        den Preis eines Symbols kennt, ergaenzt die uebrigen ausdruecklich aus
        last_marks -- sichtbar an der Aufrufstelle statt still hier drin.
        """
        with decimal.localcontext(money.CTX):
            self._last_marks.update(marks)
            position_value = Decimal(0)
            values: dict[str, Decimal] = {}
            for symbol, pos in self._positions.items():
                if pos.qty == 0:
                    continue
                price = marks.get(symbol)
                if price is None:
                    raise ValueError(
                        f"Kein Marktpreis für gehaltene Position {symbol} (qty={pos.qty}); "
                        f"mark() erhielt Preise für {sorted(marks)}. Ohne Preis waere die "
                        f"Position aus der Equity gefallen (Scheinverlust)."
                    )
                value = pos.qty * price
                values[symbol] = value
                position_value += value
            equity = self._cash + position_value
            pct_by_symbol = {sym: float(v / equity * 100) if equity > 0 else 0.0 for sym, v in values.items()}
            exposure_pct = float(position_value / equity * 100) if equity > 0 else 0.0
            return Valuation(
                ts_ms=ts_ms, cash=self._cash, position_value=position_value, equity=equity,
                exposure_pct=exposure_pct, position_pct_by_symbol=pct_by_symbol,
            )

    def restore(self, positions: Mapping[str, Position], cash: Decimal) -> None:
        """Setzt Kasse und Positionen aus einer externen Quelle (dem Journal,
        A-14) statt sie ueber apply() zu erarbeiten — fuer den Start eines
        neuen Prozesses. last_marks bleibt bewusst leer: E-010/Weg A macht
        das fuer resolve_pending() ueberfluessig; ein POST /api/risk/check
        liefert vor dem naechsten mark()-Aufruf ohnehin frische Kurse."""
        self._positions = dict(positions)
        self._cash = cash

    def to_portfolio_state(self, v: Valuation, start_of_day_equity: Decimal) -> risk.PortfolioState:
        """Baut den PortfolioState fuer die Risiko-Engine."""
        return risk.PortfolioState(
            equity=v.equity,
            start_of_day_equity=start_of_day_equity,
            exposure_pct=v.exposure_pct,
            position_pct_by_symbol=v.position_pct_by_symbol,
        )


def realized_pnl_per_sell(fills: Sequence[Mapping]) -> list[Decimal]:
    """Repliziert die mengengewichtete Durchschnittspreis-Buchhaltung aus
    Ledger._book_buy()/_book_sell() ueber eine bereits gespeicherte Fill-Liste,
    chronologisch je Symbol, und liefert je SELL-Fill den realisierten Gewinn
    oder Verlust. Fuer web.py's Trefferquote (Spec 7.1) — kein neuer
    Datenbank-Zustand, da fills.realized_pnl je Fill nicht gespeichert wird
    (nur kumulativ in positions.realized_pnl).

    Laeuft bewusst unter money.CTX (prec=34), genau wie _book_buy()/_book_sell()
    (siehe apply()). Ohne das driftet das Ergebnis bei langen Ketten mengen-
    gewichteter Divisionen vom Standardkontext (prec=28) langsam vom echten
    Ledger weg -- gemessen im Kopplungstest
    test_realized_pnl_per_sell_stimmt_mit_dem_echten_ledger_ueberein: nach 1000
    Fills unterschied sich die neunzehnte Nachkommastelle. Die Funktion hiesse
    dann exakt wie die Spec es verlangt, waere es aber nicht.
    """
    with decimal.localcontext(money.CTX):
        avg_price: dict[str, Decimal] = {}
        qty: dict[str, Decimal] = {}
        ergebnis: list[Decimal] = []
        for f in sorted(fills, key=lambda f: f["id"]):
            sym = f["symbol"]
            if f["side"] == "BUY":
                alte_qty = qty.get(sym, Decimal(0))
                neue_qty = alte_qty + f["qty"]
                alter_avg = avg_price.get(sym, Decimal(0))
                avg_price[sym] = ((alte_qty * alter_avg + f["qty"] * f["price"]) / neue_qty
                                   if neue_qty > 0 else Decimal(0))
                qty[sym] = neue_qty
            else:
                realized = (f["price"] - avg_price.get(sym, Decimal(0))) * f["qty"]
                ergebnis.append(realized)
                qty[sym] = qty.get(sym, Decimal(0)) - f["qty"]
        return ergebnis
