"""Geldarithmetik fuer Aitra - Decimal statt float, ueberall.

Gruende in docs/entscheidungen/E-002. Kurz: 0.1 + 0.2 ist in Binaerarithmetik
nicht 0.3, und ueber tausende Fills driftet der Saldo. prec=34 entspricht
IEEE decimal128 und macht jede Operation des Fuellmodells exakt (Spec 6.4).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Context, Decimal, ROUND_DOWN, ROUND_UP

CTX = Context(prec=34)
DP = 8  # kanonische Nachkommastellen fuer die Textform


def dec(value: str | int | Decimal) -> Decimal:
    """Wandelt zu Decimal. float ist verboten und wirft."""
    if isinstance(value, float):
        raise TypeError(
            "Geld nie aus float erzeugen - Zeichenkette, int oder Decimal verwenden"
        )
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _quant(value: Decimal, unit: Decimal, rounding: str) -> Decimal:
    if unit <= 0:
        raise ValueError(f"Schrittweite muss positiv sein, war {unit}")
    faktor = CTX.divide(value, unit).to_integral_value(rounding=rounding)
    return CTX.multiply(faktor, unit)


def step_down(value: Decimal, step: Decimal) -> Decimal:
    """Menge auf ein Vielfaches der Schrittweite abrunden. Immer abwaerts."""
    return _quant(value, step, ROUND_DOWN)


def tick_down(value: Decimal, tick: Decimal) -> Decimal:
    """Preis auf die Tickgroesse abrunden."""
    return _quant(value, tick, ROUND_DOWN)


def tick_up(value: Decimal, tick: Decimal) -> Decimal:
    """Preis auf die Tickgroesse aufrunden."""
    return _quant(value, tick, ROUND_UP)


def to_text(d: Decimal, dp: int = DP) -> str:
    """Kanonische Textform fuer Datenbank und JSON: feste Nachkommastellen."""
    return f"{d:.{dp}f}"


def from_text(s: str) -> Decimal:
    return Decimal(s)


@dataclass(frozen=True)
class SymbolSpec:
    symbol: str
    base: str
    quote: str
    tick_size: Decimal
    step_size: Decimal
    min_qty: Decimal
    min_notional: Decimal
    base_precision: int
    quote_precision: int

    def effective_min_notional(self, price: Decimal) -> Decimal:
        """Die Ordergroesse, die nach dem Abrunden garantiert noch durchgeht (K-3).

        min_notional allein genuegt nicht: eine exakt auf min_notional gezielte
        Order faellt nach dem Abrunden auf step_size darunter und wird abgelehnt.
        Fuer BTCUSDC bei 81287,04 sind das 5,8128704 statt 5,00 USDC.
        """
        return CTX.add(self.min_notional, CTX.multiply(self.step_size, price))


# BTCUSDC am 2026-09-20, BNBUSDC am 2026-09-21 von
# api.binance.com/api/v3/exchangeInfo abgelesen.
# binance.py (Aufgabe 3) ueberschreibt sie zur Laufzeit ueber store.upsert_symbol_spec;
# hier stehen sie, damit die Engine vollstaendig ohne Netz testbar bleibt.
BUILTIN_SPECS: dict[str, SymbolSpec] = {
    "BTCUSDC": SymbolSpec(
        symbol="BTCUSDC", base="BTC", quote="USDC",
        tick_size=Decimal("0.01"), step_size=Decimal("0.00001"),
        min_qty=Decimal("0.00001"), min_notional=Decimal("5"),
        base_precision=8, quote_precision=8,
    ),
    "BNBUSDC": SymbolSpec(
        symbol="BNBUSDC", base="BNB", quote="USDC",
        tick_size=Decimal("0.01"), step_size=Decimal("0.001"),
        min_qty=Decimal("0.001"), min_notional=Decimal("5"),
        base_precision=8, quote_precision=8,
    ),
}
