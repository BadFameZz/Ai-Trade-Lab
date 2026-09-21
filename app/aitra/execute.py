"""execute_proposal(): das Nadeloehr (E-003).

Die einzige Funktion im Paket, die Ledger.apply() aufruft (A-6b) — verbindet
RiskEngine.check() -> sizing.size_order() -> Ledger.apply() -> Journal (decisions/fills).
Verwaltet zusaetzlich schwebende Vorschlaege (E-006): Ohne next_candle wird der
Vorschlag als pending_fill abgelegt, bis resolve_pending() die gespeicherte Order
mit einer Folgekerze bucht (E-010, Weg A), oder expire_stale_pending() ihn nach
pending_expiry_ms verwerfen laesst.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

from . import db, money, store_run
from .ledger import Fill, Ledger, Order, Rejection
from .marketdata import Candle, Clock
from .risk import Proposal, RiskEngine
from .sizing import size_order

PENDING_EXPIRY_MS_DEFAULT = 1_800_000  # 2 * interval_s bei 15m (E-006)


@dataclass
class ExecutionContext:
    """Nicht frozen: kill_switch (und engine, in Tests) werden vom Aufrufer je
    nach Betriebsart aktualisiert, ohne den Kontext neu aufzubauen."""
    conn: sqlite3.Connection
    run_id: str
    ledger: Ledger
    engine: RiskEngine
    specs: Mapping[str, money.SymbolSpec]
    fee_bps: float
    slippage_bps: float
    clock: Clock
    kill_switch: bool = False
    pending_expiry_ms: int = PENDING_EXPIRY_MS_DEFAULT


@dataclass(frozen=True)
class ExecutionResult:
    decision_id: int
    approved: bool
    code: str
    reason: str
    status: str  # "filled" | "rejected" | "pending_fill" | "no_order"
    fill: Fill | None = None


def _journal_fill(ctx: ExecutionContext, decision_id: int, fill: Fill) -> None:
    """Fill, Entscheidungsverknuepfung und Positionsschnappschuss in EINER Transaktion.

    Die drei Schreibvorgaenge gehoeren zusammen: ein Fill ohne die zugehoerige
    fill_id in decisions oder ohne den Positionsschnappschuss waere ein halb
    gebuchter Zustand. Frueher committete jeder einzeln (drei Commits je Fill,
    gemessen 11.292 von 116.421 eines Jahreslaufs) — das war nicht nur teuer,
    sondern liess auch ein Fenster offen, in dem genau dieser halbe Zustand
    auf der Platte stand.

    Scheitert einer der drei Schreibvorgaenge, wird die Transaktion
    zurueckgerollt (nicht nur die Exception durchgereicht): sonst bliebe die
    Verbindung mit bereits geschriebenen, aber uncommitteten Zeilen offen, und
    ein spaeterer, voellig unverwandter commit() an derselben Verbindung
    wuerde den halb gebuchten Fill doch noch persistieren.
    """
    try:
        fill_id = store_run.insert_fill(
            ctx.conn, run_id=ctx.run_id, decision_id=decision_id, symbol=fill.symbol,
            side=fill.side, candle_open_time=fill.candle_open_time, price=fill.price,
            qty=fill.qty, gross_quote=fill.gross_quote, fee=fill.fee, net_quote=fill.net_quote,
            cash_after=fill.cash_after, fee_bps=fill.fee_bps, slippage_bps=fill.slippage_bps,
            ts=db.now(), commit=False,
        )
        store_run.resolve_decision(ctx.conn, decision_id, fill_id, commit=False)
        pos = ctx.ledger.position(fill.symbol)
        store_run.upsert_position(ctx.conn, run_id=ctx.run_id, symbol=fill.symbol, qty=pos.qty,
                               avg_price=pos.avg_price, realized_pnl=pos.realized_pnl,
                               updated_at=db.now(), commit=False)
    except BaseException:
        ctx.conn.rollback()
        raise
    ctx.conn.commit()


def execute_proposal(
    proposal: Proposal,
    ctx: ExecutionContext,
    *,
    marks: Mapping[str, Decimal],
    ts_ms: int,
    ref_price: Decimal,
    start_of_day_equity: Decimal,
    strategy_version: str = "manual",
    reason: str = "",
    next_candle: Candle | None = None,
) -> ExecutionResult:
    """Das Nadeloehr: RiskEngine.check() -> sizing.size_order() -> Ledger.apply().

    Jede Entscheidung wird protokolliert (auch Ablehnungen, A-6). Nur ein
    genehmigter, erfolgreich bemessener Vorschlag erreicht Ledger.apply() — und
    zwar ausschliesslich hier (A-6b). ref_price ist der Preis der aktuellen
    (Entscheidungs-)Kerze; next_candle liefert, falls bekannt, die Folgekerze,
    zu deren open tatsaechlich gefuellt wird (E-006).
    """
    valuation = ctx.ledger.mark(marks, ts_ms)
    pf = ctx.ledger.to_portfolio_state(valuation, start_of_day_equity)
    decision = ctx.engine.check(proposal, pf, ctx.kill_switch)

    decision_id = db.add_decision(
        ctx.conn, strategy_version=strategy_version, symbol=proposal.symbol,
        action=proposal.action, confidence=proposal.confidence, reason=reason,
        requested_position_pct=proposal.position_pct, approved=int(decision.approved),
        risk_code=decision.code, risk_reason=decision.reason, run_id=ctx.run_id,
    )

    if proposal.action.upper() == "WAIT":
        return ExecutionResult(decision_id, decision.approved, decision.code, decision.reason, status="no_order")
    if not decision.approved:
        return ExecutionResult(decision_id, False, decision.code, decision.reason, status="rejected")

    spec = ctx.specs.get(proposal.symbol)
    if spec is None:
        grund = f"Keine SymbolSpec für {proposal.symbol}"
        store_run.reject_decision(ctx.conn, decision_id, "NO_SPEC", grund)
        return ExecutionResult(decision_id, False, "NO_SPEC", grund, status="rejected")

    held = ctx.ledger.position(proposal.symbol).qty
    order = size_order(proposal, valuation, spec, ref_price, ctx.fee_bps, ctx.slippage_bps, held)
    if isinstance(order, Rejection):
        store_run.reject_decision(ctx.conn, decision_id, order.code, order.reason)
        return ExecutionResult(decision_id, False, order.code, order.reason, status="rejected")

    if next_candle is None:
        # E-006/E-010: Folgekerze liegt noch nicht vor -> die FERTIG BEMESSENE
        # Order wird abgelegt, nicht nur der Referenzpreis. resolve_pending()
        # bucht sie spaeter unveraendert.
        store_run.mark_decision_pending(
            ctx.conn, decision_id, pending_since_ms=ts_ms,
            ref_price=ref_price, base_qty=order.base_qty,
        )
        return ExecutionResult(decision_id, True, "OK",
                                "Order schwebt bis zur Folgekerze", status="pending_fill")

    fill = ctx.ledger.apply(order, next_candle)
    if isinstance(fill, Rejection):
        store_run.reject_decision(ctx.conn, decision_id, fill.code, fill.reason)
        return ExecutionResult(decision_id, False, fill.code, fill.reason, status="rejected")

    _journal_fill(ctx, decision_id, fill)
    return ExecutionResult(decision_id, True, "OK", "Gefüllt", status="filled", fill=fill)


def resolve_pending(ctx: ExecutionContext, candle: Candle) -> list[Fill]:
    """Bucht schwebende Vorschlaege fuer candle.symbol mit der nun vorliegenden Kerze.

    E-010 ist am 2026-09-21 ueber Weg A aufgeloest: Beim Aufloesen wird **nicht
    neu bewertet und nicht neu bemessen**. Die Menge wurde zum
    Vorschlagszeitpunkt berechnet und steht als decisions.pending_base_qty in
    der Zeile; hier wird nur noch der Kill Switch geprueft und dann gebucht.

    Warum: run_replay() bewertet, prueft und bemisst alles bei t und bucht auf
    t+1 — in einem Aufruf. Bemaesse der Livepfad beim Aufloesen neu, haengen
    equity (ueber target_quote) und cash (ueber INSUFFICIENT_CASH) an der
    Fuellkerze, also an einem Preis, den die Entscheidung nicht kennen konnte.
    Dieselbe Entscheidung ergaebe live eine andere Menge als im Replay, und A-8
    ("drei Quellen, ein Hash") waere konstruktionsbedingt unerreichbar.

    Zweite Wirkung, ausdruecklich gewollt: Ledger.mark() wird hier gar nicht
    mehr aufgerufen. Damit verschwindet der zweite Befund aus E-010 — ein frisch
    gestarteter Prozess hat ein leeres _last_marks, und mark() haette fuer jede
    aus dem Journal rekonstruierte Position sofort geworfen.

    Die Kill-Switch-Pruefung ist die einzige Ausnahme von der Paritaet, und sie
    ist eine Sicherheitsfunktion: ein Vorschlag, den der Kill Switch zwischen
    Entscheidung und Ausfuehrung einholt, darf nicht mehr fuellen. Im Zeitraffer
    gibt es diese Luecke nicht, weil Entscheidung und Ausfuehrung in derselben
    Iteration liegen.

    Kosten bei Irrtum (aus E-010 uebernommen): Schrumpft die Kasse zwischen t
    und t+1 durch einen Fill in einem anderen Symbol, lehnt Ledger.apply() mit
    INSUFFICIENT_CASH ab, statt die Order zu verkleinern. Richtig, aber der
    Vorschlag ist dann verloren. Tritt das gehaeuft auf, wird beim Aufloesen auf
    die verfuegbare Kasse gedeckelt.
    """
    expire_stale_pending(ctx)
    filled: list[Fill] = []
    for row in store_run.get_pending_decisions(ctx.conn, ctx.run_id):
        if row["symbol"] != candle.symbol:
            continue
        if ctx.kill_switch:
            store_run.reject_decision(
                ctx.conn, row["id"], "KILL_SWITCH",
                "Kill Switch aktiv – schwebender Vorschlag nicht gebucht",
            )
            continue
        if row["symbol"] not in ctx.specs:
            store_run.reject_decision(ctx.conn, row["id"], "NO_SPEC",
                                       f"Keine SymbolSpec für {row['symbol']}")
            continue
        roh_menge = row["pending_base_qty"]
        if roh_menge is None:
            # Zeile aus einer DB vor Migration 4. Die Menge nachtraeglich zu
            # berechnen waere genau die Neubemessung, die E-010 beseitigt.
            store_run.reject_decision(
                ctx.conn, row["id"], "NO_BASE_QTY",
                "Keine gespeicherte Ordermenge (DB vor Migration 4)",
            )
            continue
        order = Order(symbol=row["symbol"], side=str(row["action"]).upper(),
                       base_qty=money.from_text(roh_menge))
        fill = ctx.ledger.apply(order, candle)
        if isinstance(fill, Rejection):
            store_run.reject_decision(ctx.conn, row["id"], fill.code, fill.reason)
            continue
        _journal_fill(ctx, row["id"], fill)
        filled.append(fill)
    return filled


def expire_stale_pending(ctx: ExecutionContext) -> list[int]:
    """Laesst schwebende Vorschlaege verfallen, die laenger als pending_expiry_ms
    schweben — unabhaengig davon, ob je eine passende Kerze eintrifft (E-006)."""
    now = ctx.clock.now_ms()
    expired: list[int] = []
    for row in store_run.get_pending_decisions(ctx.conn, ctx.run_id):
        if now - row["pending_since_ms"] > ctx.pending_expiry_ms:
            store_run.expire_decision(ctx.conn, row["id"])
            expired.append(row["id"])
    return expired
