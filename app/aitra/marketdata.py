"""Kerzenquellen, Uhr und Veraltet-Erkennung.

Reine Datenhaltung und -abfrage: kein Netz. Nur WallClock.now_ms() ruft die Systemuhr
auf (E-001, A-8b) — alle anderen Uhren sind Werte, die der Aufrufer setzt.
"""
from __future__ import annotations

import math
import sqlite3
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, Sequence

from . import store


@dataclass(frozen=True)
class Candle:
    symbol: str
    interval: str
    open_time: int
    close_time: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    closed: bool


class CandleSource(Protocol):
    def candles(self, symbol: str, interval: str, start_ms: int | None = None, limit: int = 500) -> list[Candle]: ...


class Clock(Protocol):
    def now_ms(self) -> int: ...


class WallClock:
    """Die Uhr fuer den Live-Betrieb. Der einzige Aufruf der Systemuhr in diesem Paket."""
    def now_ms(self) -> int:
        return int(time.time() * 1000)


class SimClock:
    """Die Uhr fuer Replay und Zeitraffer: wird explizit auf die aktuelle Kerze gesetzt."""
    def __init__(self, ms: int) -> None:
        self._ms = ms

    def now_ms(self) -> int:
        return self._ms

    def set(self, ms: int) -> None:
        self._ms = ms


class ListSource:
    """Kerzenquelle aus dem Speicher, fuer den Zeitraffer (ausschliesslich geschlossene Kerzen)."""
    def __init__(self, candles: Sequence[Candle]) -> None:
        self._candles = sorted(
            (c for c in candles if c.closed), key=lambda c: (c.symbol, c.interval, c.open_time)
        )

    def candles(self, symbol: str, interval: str, start_ms: int | None = None, limit: int = 500) -> list[Candle]:
        out = [c for c in self._candles if c.symbol == symbol and c.interval == interval]
        if start_ms is not None:
            out = [c for c in out if c.open_time >= start_ms]
        return out[:limit]


class SqliteSource:
    """Kerzenquelle aus der Datenbank, fuer den db-Replay."""
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def candles(self, symbol: str, interval: str, start_ms: int | None = None, limit: int = 500) -> list[Candle]:
        rows = store.get_candles(self._conn, symbol, interval, start_ms=start_ms, limit=limit)
        return [
            Candle(symbol=r.symbol, interval=r.interval, open_time=r.open_time, close_time=r.close_time,
                   open=r.open, high=r.high, low=r.low, close=r.close, volume=r.volume, closed=True)
            for r in rows
        ]


@dataclass(frozen=True)
class Staleness:
    status: str  # "ok" | "warn" | "stale"
    data_age_s: float
    clock_skew_s: float


def staleness(
    clock: Clock,
    latest_close_time_ms: int | None,
    server_time_ms: int | None,
    interval_s: int,
    warn_s: int = 150,
    kill_s: int = 300,
    clock_skew_warn_s: int = 5,
    clock_skew_kill_s: int = 30,
) -> Staleness:
    """Veraltet-Erkennung mit intervallrelativen Schwellen (Spec 8.2).

    warn_s/kill_s sind Untergrenzen; wirksam ist max(wert, 1,5*interval_s) bzw.
    max(wert, 3*interval_s). Ohne diese Anhebung wuerde jedes Intervall oberhalb
    von 1m im Normalbetrieb dauernd ausloesen.
    """
    warn_eff = max(warn_s, math.ceil(1.5 * interval_s))
    kill_eff = max(kill_s, 3 * interval_s)

    now = clock.now_ms()
    data_age_s = math.inf if latest_close_time_ms is None else (now - latest_close_time_ms) / 1000
    clock_skew_s = 0.0 if server_time_ms is None else abs(now - server_time_ms) / 1000

    if data_age_s > kill_eff or clock_skew_s > clock_skew_kill_s:
        status = "stale"
    elif data_age_s > warn_eff or clock_skew_s > clock_skew_warn_s:
        status = "warn"
    else:
        status = "ok"
    return Staleness(status=status, data_age_s=data_age_s, clock_skew_s=clock_skew_s)
