"""Lese-/Schreibzugriff auf die in Migration 2 angelegten Tabellen.

Geld wandert ausschliesslich als TEXT durch SQLite (E-007). Diese Datei ist die
einzige Stelle, die zwischen Decimal und TEXT wandelt (money.to_text/from_text).
Keine Uhr: jeder Zeitstempel (ts, fetched_at, started_at, finished_at, updated_at,
ts_ms) kommt als Parameter vom Aufrufer.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from . import money

DP = 8


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
    sql = "SELECT * FROM candles WHERE symbol = ? AND interval = ?"
    args: list = [symbol, interval]
    if start_ms is not None:
        sql += " AND open_time >= ?"
        args.append(start_ms)
    if end_ms is not None:
        sql += " AND open_time <= ?"
        args.append(end_ms)
    sql += " ORDER BY open_time ASC LIMIT ?"
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


def create_run(conn: sqlite3.Connection, run_id: str, kind: str, started_at: str,
               code_version: str, params_json: str = "{}") -> None:
    conn.execute(
        "INSERT INTO runs (run_id, kind, started_at, code_version, params_json) VALUES (?, ?, ?, ?, ?)",
        (run_id, kind, started_at, code_version, params_json),
    )
    conn.commit()


def finish_run(conn: sqlite3.Connection, run_id: str, finished_at: str) -> None:
    conn.execute("UPDATE runs SET finished_at = ? WHERE run_id = ?", (finished_at, run_id))
    conn.commit()


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


def insert_fill(conn: sqlite3.Connection, *, run_id: str, decision_id: int | None, symbol: str,
                 side: str, candle_open_time: int, price: Decimal, qty: Decimal,
                 gross_quote: Decimal, fee: Decimal, net_quote: Decimal, cash_after: Decimal,
                 fee_bps: float, slippage_bps: float, ts: str, commit: bool = True) -> int:
    cur = conn.execute(
        """INSERT INTO fills (ts, run_id, decision_id, symbol, side, candle_open_time, price, qty,
                               gross_quote, fee, net_quote, cash_after, fee_bps, slippage_bps)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (ts, run_id, decision_id, symbol, side, candle_open_time,
         money.to_text(price, DP), money.to_text(qty, DP), money.to_text(gross_quote, DP),
         money.to_text(fee, DP), money.to_text(net_quote, DP), money.to_text(cash_after, DP),
         fee_bps, slippage_bps),
    )
    if commit:
        conn.commit()
    return cur.lastrowid


def get_fills(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM fills WHERE run_id = ? ORDER BY id ASC", (run_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("price", "qty", "gross_quote", "fee", "net_quote", "cash_after"):
            d[k] = money.from_text(d[k])
        out.append(d)
    return out


def upsert_position(conn: sqlite3.Connection, *, run_id: str, symbol: str, qty: Decimal,
                     avg_price: Decimal, realized_pnl: Decimal, updated_at: str,
                     commit: bool = True) -> None:
    conn.execute(
        """INSERT INTO positions (run_id, symbol, qty, avg_price, realized_pnl, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(run_id, symbol) DO UPDATE SET
               qty=excluded.qty, avg_price=excluded.avg_price,
               realized_pnl=excluded.realized_pnl, updated_at=excluded.updated_at""",
        (run_id, symbol, money.to_text(qty, DP), money.to_text(avg_price, DP),
         money.to_text(realized_pnl, DP), updated_at),
    )
    if commit:
        conn.commit()


def get_positions(conn: sqlite3.Connection, run_id: str) -> dict[str, dict]:
    rows = conn.execute("SELECT * FROM positions WHERE run_id = ?", (run_id,)).fetchall()
    out = {}
    for r in rows:
        out[r["symbol"]] = {
            "qty": money.from_text(r["qty"]), "avg_price": money.from_text(r["avg_price"]),
            "realized_pnl": money.from_text(r["realized_pnl"]), "updated_at": r["updated_at"],
        }
    return out


@dataclass(frozen=True)
class EquityPoint:
    """Ein Punkt der Equity-Kurve (Spec 4.4/9.2)."""
    run_id: str
    ts_ms: int
    equity: Decimal
    cash: Decimal
    benchmark_equity: Decimal | None
    exposure_pct: float


_EQUITY_SQL = """INSERT INTO equity_curve (run_id, ts_ms, equity, cash, benchmark_equity, exposure_pct)
       VALUES (?, ?, ?, ?, ?, ?)
       ON CONFLICT(run_id, ts_ms) DO UPDATE SET
           equity=excluded.equity, cash=excluded.cash,
           benchmark_equity=excluded.benchmark_equity, exposure_pct=excluded.exposure_pct"""


def _equity_row(p: EquityPoint) -> tuple:
    return (p.run_id, p.ts_ms, money.to_text(p.equity, DP), money.to_text(p.cash, DP),
            None if p.benchmark_equity is None else money.to_text(p.benchmark_equity, DP),
            p.exposure_pct)


def append_equity_points(conn: sqlite3.Connection, punkte: Sequence[EquityPoint]) -> None:
    """Schreibt mehrere Punkte der Equity-Kurve in EINER Transaktion.

    Ein Commit je Punkt kostet auf einer dateibasierten Datenbank ein
    Vielfaches des Schreibens selbst (gemessen: 35.039 von 116.421 Commits
    eines Jahreslaufs). Genau diese Datenbank benutzt ein Trainingslauf in
    Teilprojekt C, nicht :memory:.
    """
    if not punkte:
        return
    conn.executemany(_EQUITY_SQL, [_equity_row(p) for p in punkte])
    conn.commit()


def append_equity_point(conn: sqlite3.Connection, *, run_id: str, ts_ms: int, equity: Decimal,
                         cash: Decimal, benchmark_equity: Decimal | None, exposure_pct: float) -> None:
    """Einzelner Punkt — Bequemlichkeitshuelle um append_equity_points()."""
    append_equity_points(conn, [EquityPoint(run_id, ts_ms, equity, cash, benchmark_equity, exposure_pct)])


def get_equity_curve(conn: sqlite3.Connection, run_id: str, limit: int = 500) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM equity_curve WHERE run_id = ? ORDER BY ts_ms ASC LIMIT ?", (run_id, limit),
    ).fetchall()
    out = []
    for r in rows:
        out.append({
            "ts_ms": r["ts_ms"], "equity": money.from_text(r["equity"]),
            "cash": money.from_text(r["cash"]),
            "benchmark_equity": None if r["benchmark_equity"] is None else money.from_text(r["benchmark_equity"]),
            "exposure_pct": r["exposure_pct"],
        })
    return out


def mark_decision_pending(conn: sqlite3.Connection, decision_id: int, pending_since_ms: int,
                           ref_price: Decimal) -> None:
    """Legt einen genehmigten Vorschlag schwebend ab (E-006).

    ref_price ist der zum *Vorschlagszeitpunkt* gueltige Referenzpreis. Er wird
    mitgespeichert, weil resolve_pending() die Order sonst gegen die Fuellkerze
    bemisst — also gegen einen Preis, den die Entscheidung noch nicht kennen
    konnte. Replay bemisst gegen current.close; ohne diese Spalte lieferten live
    und Replay verschiedene Mengen (E-001).
    """
    conn.execute(
        "UPDATE decisions SET pending_since_ms = ?, pending_ref_price = ? WHERE id = ?",
        (pending_since_ms, money.to_text(ref_price, DP), decision_id),
    )
    conn.commit()


def resolve_decision(conn: sqlite3.Connection, decision_id: int, fill_id: int,
                      commit: bool = True) -> None:
    conn.execute(
        "UPDATE decisions SET fill_id = ?, pending_since_ms = NULL, pending_ref_price = NULL "
        "WHERE id = ?",
        (fill_id, decision_id),
    )
    if commit:
        conn.commit()


def reject_decision(conn: sqlite3.Connection, decision_id: int, code: str, reason: str) -> None:
    """Etikettiert eine bereits protokollierte Entscheidung nachtraeglich als abgelehnt.

    Gegenstueck zu expire_decision(): dort verfaellt ein Vorschlag mangels
    Folgekerze, hier scheitert er an einer Pruefung nach der Risk Engine
    (Groessenbemessung oder Ledger). Ohne diesen Aufruf bliebe die Zeile auf
    approved = 1 / risk_code = 'OK' stehen, obwohl nie ein Fill entstand -- das
    Journal wuerde eine Ablehnung als Genehmigung ausweisen (A-6).
    """
    conn.execute(
        "UPDATE decisions SET approved = 0, risk_code = ?, risk_reason = ?, "
        "pending_since_ms = NULL, pending_ref_price = NULL WHERE id = ?",
        (code, reason, decision_id),
    )
    conn.commit()


def expire_decision(conn: sqlite3.Connection, decision_id: int) -> None:
    conn.execute(
        "UPDATE decisions SET pending_since_ms = NULL, pending_ref_price = NULL, "
        "risk_code = 'PENDING_EXPIRED' WHERE id = ?",
        (decision_id,),
    )
    conn.commit()


def get_pending_decisions(conn: sqlite3.Connection, run_id: str | None = None) -> list[dict]:
    sql = "SELECT * FROM decisions WHERE pending_since_ms IS NOT NULL"
    args: list = []
    if run_id is not None:
        sql += " AND run_id = ?"
        args.append(run_id)
    sql += " ORDER BY id ASC"
    return [dict(r) for r in conn.execute(sql, args).fetchall()]
