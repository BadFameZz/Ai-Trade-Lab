"""execute_proposal(): das Nadeloehr (E-003).

Die einzige Funktion im Paket, die Ledger.apply() aufruft (A-6b) — verbindet
RiskEngine.check() -> sizing.size_order() -> Ledger.apply() -> Journal (decisions/fills).
Verwaltet zusaetzlich schwebende Vorschlaege (E-006): Ohne next_candle wird der
Vorschlag als pending_fill abgelegt, bis resolve_pending() eine Folgekerze liefert
oder expire_stale_pending() ihn nach pending_expiry_ms verwerfen laesst.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

from . import db, money, store_run
from .ledger import Fill, Ledger, Rejection
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
        # E-006, live: Folgekerze liegt noch nicht vor -> schwebend
        store_run.mark_decision_pending(ctx.conn, decision_id, pending_since_ms=ts_ms, ref_price=ref_price)
        return ExecutionResult(decision_id, True, "OK", "Order schwebt bis zur Folgekerze", status="pending_fill")

    fill = ctx.ledger.apply(order, next_candle)
    if isinstance(fill, Rejection):
        store_run.reject_decision(ctx.conn, decision_id, fill.code, fill.reason)
        return ExecutionResult(decision_id, False, fill.code, fill.reason, status="rejected")

    _journal_fill(ctx, decision_id, fill)
    return ExecutionResult(decision_id, True, "OK", "Gefüllt", status="filled", fill=fill)


def resolve_pending(ctx: ExecutionContext, candle: Candle) -> list[Fill]:
    """Fuellt schwebende Vorschlaege fuer candle.symbol mit der nun vorliegenden Kerze.

    Bemessen wird mit dem beim Vorschlag gespeicherten pending_ref_price, nicht
    mit candle.open: candle.open IST der Fuellpreis, und wer die Menge gegen den
    Fuellpreis bemisst, laesst die Entscheidung den eigenen Ausgang kennen.
    Genau dieses Muster wurde in benchmark.py bereits als Critical
    zurueckgenommen. Replay bemisst gegen current.close; nur mit dem
    gespeicherten ref_price liefern live und Replay dieselbe Menge (E-001).
    """
    expire_stale_pending(ctx)
    filled: list[Fill] = []
    for row in store_run.get_pending_decisions(ctx.conn, ctx.run_id):
        if row["symbol"] != candle.symbol:
            continue
        spec = ctx.specs.get(row["symbol"])
        if spec is None:
            store_run.reject_decision(ctx.conn, row["id"], "NO_SPEC",
                                   f"Keine SymbolSpec für {row['symbol']}")
            continue
        roh_ref = row["pending_ref_price"]
        if roh_ref is None:
            # Zeile aus einer DB vor Migration 3: der Vorschlagspreis fehlt.
            # Verwerfen ist richtig — mit candle.open weiterzurechnen waere
            # genau der Look-ahead, den Migration 3 beseitigt. Eigener Code,
            # nicht PENDING_EXPIRED: der Vorschlag ist nicht verfallen,
            # sondern nicht mehr bemessbar.
            store_run.reject_decision(ctx.conn, row["id"], "NO_REF_PRICE",
                                   "Kein gespeicherter Vorschlagspreis (DB vor Migration 3)")
            continue
        ref_price = money.from_text(roh_ref)
        held = ctx.ledger.position(row["symbol"]).qty
        proposal = Proposal(symbol=row["symbol"], action=row["action"],
                             position_pct=row["requested_position_pct"] or 0.0)
        # Nur fuer candle.symbol liegt ein frischer Preis vor. Alle uebrigen
        # gehaltenen Positionen werden ausdruecklich mit ihrem zuletzt
        # bekannten Kurs bewertet: wuerden sie fehlen, fielen sie aus der
        # Equity und erzeugten einen Scheinverlust, der ueber
        # to_portfolio_state() bis in die Tagesverlustgrenze durchschlaegt.
        marks = {**ctx.ledger.last_marks, candle.symbol: candle.open}
        valuation = ctx.ledger.mark(marks, ts_ms=ctx.clock.now_ms())
        order = size_order(proposal, valuation, spec, ref_price, ctx.fee_bps, ctx.slippage_bps, held)
        if isinstance(order, Rejection):
            # Frueher PENDING_EXPIRED: ein INSUFFICIENT_CASH wurde als
            # "verfallen" etikettiert und die Ursache war aus dem Journal
            # nicht mehr lesbar.
            store_run.reject_decision(ctx.conn, row["id"], order.code, order.reason)
            continue
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
