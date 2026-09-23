"""Lese-/Schreibzugriff auf die Marktdaten-Tabellen (candles, symbol_specs).

Die Naht zu store_run.py stammt aus Spec 3.1b: Marktdaten hier, alles
Laufbezogene (runs, fills, positions, equity_curve, decisions) dort. Vor der
Teilung lag store.py mit 318 Zeilen ueber der harten 300er-Marke.

Geld wandert ausschliesslich als TEXT durch SQLite (E-007). store.py und
store_run.py sind die einzigen Stellen, die zwischen Decimal und TEXT wandeln
(money.to_text/from_text). Keine Uhr: jeder Zeitstempel kommt als Parameter.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from . import money

DP = money.DP


@dataclass(frozen=True)
class CandleRow:
    """Eine gespeicherte, abgeschlossene Kerze (offene Kerzen werden nie gespeichert)."""
    symbol: str
    interval: str
    open_time: int
    close_time: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    source: str
    fetched_at: str


def upsert_candles(conn: sqlite3.Connection, rows: Sequence[CandleRow]) -> None:
    conn.executemany(
        """INSERT INTO candles (symbol, interval, open_time, close_time, open, high, low,
                                 close, volume, source, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(symbol, interval, open_time) DO UPDATE SET
               close_time=excluded.close_time, open=excluded.open, high=excluded.high,
               low=excluded.low, close=excluded.close, volume=excluded.volume,
               source=excluded.source, fetched_at=excluded.fetched_at""",
        [
            (r.symbol, r.interval, r.open_time, r.close_time,
             money.to_text(r.open, DP), money.to_text(r.high, DP),
             money.to_text(r.low, DP), money.to_text(r.close, DP),
             money.to_text(r.volume, DP), r.source, r.fetched_at)
            for r in rows
        ],
    )
    conn.commit()


def get_candles(
    conn: sqlite3.Connection, symbol: str, interval: str,
    start_ms: int | None = None, end_ms: int | None = None, limit: int = 500,
) -> list[CandleRow]:
    """Die JUENGSTEN `limit` Kerzen des Fensters, chronologisch aufsteigend (B-D1).

    Der Vertrag hat zwei Haelften, und beide werden gebraucht:

    (1) Ein bindendes LIMIT schneidet die AELTESTEN Zeilen weg. Alle
        limit-getriebenen Aufrufer (dashboard.last_prices, web.risk_check,
        GET /api/market/candles) lesen rows[-1] als "die juengste Kerze".
    (2) Das Ergebnis ist IMMER aufsteigend - auch wenn das LIMIT gebissen hat.
        replay._load_candles_from_db() laeuft candles[0..n-1] in Zeitrichtung
        ab; ein blosses ORDER BY ... DESC liesse den Zeitraffer rueckwaerts
        durch den Markt laufen.

    Vorher stand hier ORDER BY open_time ASC LIMIT ?. Das lieferte die
    AELTESTEN N. Auf CT 107 gemessen (2026-09-23, 38.399 Kerzen je Symbol):
    GET /api/market/candles gab den Stand vom 2025-08-19 (115.560,01) statt
    vom 2026-09-23 (85.454,11) - rund 35 % daneben auf dem Preis, der die
    gesamte Live-Equity bewertet. Ein Buchverlust von 30.105,90 USDC wurde als
    pnl 0,00 gemeldet, und ueber to_portfolio_state() sah die
    Tagesverlustgrenze - und damit der Kill Switch - ihn nie.

    Mit start_ms/end_ms und grosszuegigem limit bindet das LIMIT nicht; dort
    ist das Ergebnis unveraendert das komplette Fenster in Zeitrichtung. Der
    einzige produktive Aufrufer dieses Pfades ist replay._load_candles_from_db()
    (limit=1_000_000). Backfill und Benchmark rufen get_candles() NICHT auf —
    backfill.py liest ueber BinanceClient.klines(), der Benchmark bekommt seine
    Kerzen vom Poller gereicht.

    ACHTUNG fuer kuenftige Aufrufer: bindet das LIMIT innerhalb eines
    Zeitfensters doch einmal, schneidet es jetzt VORNE ab (die aeltesten
    Zeilen fallen weg), nicht mehr hinten.
    """
    sql = "SELECT * FROM candles WHERE symbol = ? AND interval = ?"
    args: list = [symbol, interval]
    if start_ms is not None:
        sql += " AND open_time >= ?"
        args.append(start_ms)
    if end_ms is not None:
        sql += " AND open_time <= ?"
        args.append(end_ms)
    sql = f"SELECT * FROM ({sql} ORDER BY open_time DESC LIMIT ?) ORDER BY open_time ASC"
    args.append(limit)
    out = []
    for r in conn.execute(sql, args).fetchall():
        out.append(CandleRow(
            symbol=r["symbol"], interval=r["interval"],
            open_time=r["open_time"], close_time=r["close_time"],
            open=money.from_text(r["open"]), high=money.from_text(r["high"]),
            low=money.from_text(r["low"]), close=money.from_text(r["close"]),
            volume=money.from_text(r["volume"]), source=r["source"], fetched_at=r["fetched_at"],
        ))
    return out


def prune_candles(conn: sqlite3.Connection, symbol: str, interval: str, retention_days: int) -> int:
    """Loescht Kerzen, deren close_time <= (neueste close_time - retention_days) ist.

    A-16: Die Grenze ist inklusiv (<=). Mit < wuerde die aelteste noch zu loeschende
    Zeile ueberleben und die Zaehlung um 1 verschieben.
    """
    row = conn.execute(
        "SELECT MAX(close_time) m FROM candles WHERE symbol = ? AND interval = ?",
        (symbol, interval),
    ).fetchone()
    if row is None or row["m"] is None:
        return 0
    threshold = row["m"] - retention_days * 86_400_000
    cur = conn.execute(
        "DELETE FROM candles WHERE symbol = ? AND interval = ? AND close_time <= ?",
        (symbol, interval, threshold),
    )
    conn.commit()
    return cur.rowcount


def upsert_symbol_spec(conn: sqlite3.Connection, spec: money.SymbolSpec, source: str, fetched_at: str) -> None:
    conn.execute(
        """INSERT INTO symbol_specs (symbol, base, quote, tick_size, step_size, min_qty,
                                      min_notional, base_precision, quote_precision, source, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(symbol) DO UPDATE SET
               base=excluded.base, quote=excluded.quote, tick_size=excluded.tick_size,
               step_size=excluded.step_size, min_qty=excluded.min_qty,
               min_notional=excluded.min_notional, base_precision=excluded.base_precision,
               quote_precision=excluded.quote_precision, source=excluded.source,
               fetched_at=excluded.fetched_at""",
        (spec.symbol, spec.base, spec.quote, money.to_text(spec.tick_size, DP),
         money.to_text(spec.step_size, DP), money.to_text(spec.min_qty, DP),
         money.to_text(spec.min_notional, DP), spec.base_precision, spec.quote_precision,
         source, fetched_at),
    )
    conn.commit()


def get_symbol_spec(conn: sqlite3.Connection, symbol: str) -> money.SymbolSpec | None:
    r = conn.execute("SELECT * FROM symbol_specs WHERE symbol = ?", (symbol,)).fetchone()
    if r is None:
        return None
    return money.SymbolSpec(
        symbol=r["symbol"], base=r["base"], quote=r["quote"],
        tick_size=money.from_text(r["tick_size"]), step_size=money.from_text(r["step_size"]),
        min_qty=money.from_text(r["min_qty"]), min_notional=money.from_text(r["min_notional"]),
        base_precision=r["base_precision"], quote_precision=r["quote_precision"],
    )
