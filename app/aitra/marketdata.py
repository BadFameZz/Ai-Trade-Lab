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

# Die sechs erlaubten Kerzenintervalle und ihre Laenge in Sekunden (Spec 11.2).
# Feste Menge statt Parsen: '1M' waere ein Monat, '1m' eine Minute — ein Parser
# ueber Ziffer+Buchstabe verwechselt beides still.
INTERVALS: dict[str, int] = {
    "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400,
}


def interval_seconds(interval: str) -> int:
    """Laenge einer Kerze in Sekunden. Grundlage der intervallrelativen
    Veraltet-Schwellen aus Spec 8.2."""
    try:
        return INTERVALS[interval]
    except KeyError as e:
        raise ValueError(
            f"Unbekanntes Intervall {interval!r}; erlaubt: {sorted(INTERVALS)}"
        ) from e


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


def projizierte_serverzeit(clock: Clock, last_server_time_ms: int | None,
                            last_time_check_ms: int | None) -> int | None:
    """Die zuletzt gemessene Serverzeit, fortgeschrieben um die seither
    vergangene Zeit - NICHT der rohe Cache-Wert.

    WARUM (B-1 aus dem Gesamtreview A2, Blocker): server_time() wird nach Spec
    11.3 hoechstens alle 15 min geholt. staleness() rechnet
    clock_skew_s = abs(now - server_time_ms) gegen die AKTUELLE Uhr. Geht der
    gecachte Wert dort hinein, waechst der gemeldete Uhrversatz um eine
    Sekunde pro Sekunde: mit MARKET_CLOCK_SKEW_KILL_S=30 und MARKET_POLL_S=60
    war der Lieferzustand nach 60 s 'stale', der Kill Switch gesetzt und -
    weil _apply_staleness() ihn nur setzt - nie wieder geloest. Ab da lehnte
    die RiskEngine jede Order ab. Gemessen vor der Behebung: "Zyklus 1
    (t=60s): status='stale', Datenalter 260s, Uhrversatz 60s" - bei
    synchroner Uhr und puenktlichen Kerzen.

    Mit der Fortschreibung ist abs(now - projiziert) genau der Versatz, der
    bei der LETZTEN Messung festgestellt wurde. Das ist eine ehrliche Aussage:
    zwischen zwei Abfragen wird kein neuer Versatz erfunden. Wer das hier
    spaeter zu `return pc.last_server_time_ms` "vereinfacht", baut B-1 wieder
    ein.

    Zweite Wirkung, ausdruecklich gewollt: derselbe Wert geht an
    BinanceClient.klines(), das die laufende Kerze ueber
    `close_time >= server_time_ms` verwirft. Mit dem rohen Cache-Wert galt
    dort eine bis zu 15 min ALTE Grenze - jede in diesem Fenster geschlossene
    Kerze wurde als "laufend" weggeworfen und erst nach der naechsten
    Zeitabfrage uebernommen. Die Fortschreibung ist auch dafuer die richtige
    Grenze (Serverzeit + lokal vergangene Zeit), nicht die Containeruhr:
    Spec 8.1 bleibt gewahrt.

    Kosten bei Irrtum: driftet die Containeruhr zwischen zwei Abfragen wirklich
    weg, merken wir es bis zu 15 min spaeter, und die klines-Grenze ist um
    genau diese Drift daneben. Das ist der Preis des Ratenbudgets aus Spec
    11.3 und bewusst akzeptiert.
    """
    if last_server_time_ms is None or last_time_check_ms is None:
        return None
    return last_server_time_ms + (clock.now_ms() - last_time_check_ms)


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
