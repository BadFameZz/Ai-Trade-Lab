"""Lese-/Schreibzugriff auf die lauf- und ledgerbezogenen Tabellen.

Gegenstueck zu store.py (Marktdaten). Hier liegen runs, fills, positions,
equity_curve und die Lauf-Spalten von decisions. Die Naht ist in Spec 3.1b
beschrieben; sie wurde gezogen, weil store.py mit 318 Zeilen ueber der harten
300er-Marke lag.

Geld wandert ausschliesslich als TEXT durch SQLite (E-007), gewandelt nur ueber
money.to_text/from_text. Keine Uhr: ts, started_at, finished_at und updated_at
kommen als Parameter vom Aufrufer.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from . import money

DP = money.DP


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
                           ref_price: Decimal, base_qty: Decimal) -> None:
    """Legt einen genehmigten, bereits bemessenen Vorschlag schwebend ab (E-006/E-010).

    base_qty ist die zum *Vorschlagszeitpunkt* berechnete Ordermenge. Sie ist der
    eigentliche Inhalt dieser Zeile: resolve_pending() bucht sie unveraendert und
    bemisst nicht neu (E-010, Weg A).

    ref_price ist der zugehoerige Referenzpreis. Er wird weiter mitgefuehrt,
    aber nur noch als Journal- und Anzeigewert ("Vorschlag bei 81.287,03,
    gefuellt bei 81.310,00"). Die Bemessung haengt nicht mehr an ihm.
    """
    conn.execute(
        "UPDATE decisions SET pending_since_ms = ?, pending_ref_price = ?, "
        "pending_base_qty = ? WHERE id = ?",
        (pending_since_ms, money.to_text(ref_price, DP), money.to_text(base_qty, DP), decision_id),
    )
    conn.commit()


def resolve_decision(conn: sqlite3.Connection, decision_id: int, fill_id: int,
                      commit: bool = True) -> None:
    conn.execute(
        "UPDATE decisions SET fill_id = ?, pending_since_ms = NULL, pending_ref_price = NULL, "
        "pending_base_qty = NULL WHERE id = ?",
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
        "pending_since_ms = NULL, pending_ref_price = NULL, pending_base_qty = NULL WHERE id = ?",
        (code, reason, decision_id),
    )
    conn.commit()


def expire_decision(conn: sqlite3.Connection, decision_id: int) -> None:
    conn.execute(
        "UPDATE decisions SET pending_since_ms = NULL, pending_ref_price = NULL, "
        "pending_base_qty = NULL, risk_code = 'PENDING_EXPIRED' WHERE id = ?",
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
