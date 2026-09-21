"""Rechnet aus, was das Dashboard ueber sich selbst aussagt.

Gegenstueck zu web.py (Ruling des Koordinators, A2/Aufgabe 6, Folgeschritt nach
der Rueckfrage zur 300-Zeilen-Marke): web.py beantwortet HTTP, dieses Modul
rechnet die Zahlen aus. Freie Funktionen, kein Flask, kein `g`, kein
Anfragekontext - `conn` und `cfg` kommen als Parameter, damit build_status()
und build_health() ohne Testclient direkt gegen eine Datenbank pruefbar sind.

Bucht nirgends einen Fill (nur Ledger.mark()/.restore()/.position()) - der
A-6b-Waechter (execute.py ist die einzige Datei, die die Fuellmethode des
Ledgers aufruft) ist davon unberuehrt.
"""
from __future__ import annotations

import shutil
import sqlite3
import threading
import time
from decimal import Decimal

from . import VERSION, db, money, store, store_run
from .config import Config
from .ledger import Ledger, Position, realized_pnl_per_sell
from .risk import RiskEngine


def _specs(conn: sqlite3.Connection, cfg: Config) -> dict[str, money.SymbolSpec]:
    """Dieselbe Formel wie beim App-Start in web.py (DB-SymbolSpec bevorzugt,
    sonst eingebaute Werte) - hier je Aufruf neu ausgewertet statt einmalig
    gecacht. Heute ohne beobachtbaren Unterschied: nichts im Paket ruft
    store.upsert_symbol_spec() zur Laufzeit auf, symbol_specs bleibt leer, also
    liefert store.get_symbol_spec() immer None und money.BUILTIN_SPECS
    gewinnt - in Produktion wie im Test, vor und nach dieser Verschiebung."""
    specs = {s: store.get_symbol_spec(conn, s) or money.BUILTIN_SPECS.get(s) for s in cfg.market_symbols}
    return {s: sp for s, sp in specs.items() if sp is not None}


def _kill_switch(conn: sqlite3.Connection) -> bool:
    return db.get_state(conn, "kill_switch", "0") == "1"


def build_live_ledger(conn: sqlite3.Connection, cfg: Config) -> Ledger:
    """Rekonstruiert den Live-Ledger aus dem Journal (A-14) - bei jedem
    Request neu, absichtlich: es gibt keinen mit dem Poller-Thread geteilten
    Ledger-Zustand, um Threading-Kollisionen zwischen dem Poller (poller.py)
    und gunicorn-Request-Threads zu vermeiden."""
    ledger = Ledger(starting_cash=cfg.starting_balance, specs=_specs(conn, cfg),
                     fee_bps=cfg.fee_bps, slippage_bps=cfg.slippage_bps)
    positions = {s: Position(s, p["qty"], p["avg_price"], p["realized_pnl"])
                 for s, p in store_run.get_positions(conn, "live").items()}
    fills = store_run.get_fills(conn, "live")
    cash = fills[-1]["cash_after"] if fills else cfg.starting_balance
    ledger.restore(positions, cash)
    return ledger


def last_prices(conn: sqlite3.Connection, cfg: Config) -> dict[str, Decimal]:
    """Letzter bekannter Schlusskurs je konfiguriertem Symbol - deckt jede
    gehaltene Position ab, weil Positionen nur in konfigurierten Symbolen
    entstehen koennen (execute_proposal lehnt alles andere mit NO_SPEC ab)."""
    preise = {}
    for sym in _specs(conn, cfg):
        rows = store.get_candles(conn, sym, cfg.market_interval, limit=1)
        if rows:
            preise[sym] = rows[-1].close
    return preise


def build_status(conn: sqlite3.Connection, cfg: Config) -> dict:
    """Der komplette Inhalt von GET /api/status - Positionen, trades_total,
    geführte Kasse (B-3), Benchmark, Trefferquote, Max Drawdown. web.py
    jsonify()t nur noch, was hier zurueckkommt."""
    ledger = build_live_ledger(conn, cfg)
    marks = last_prices(conn, cfg)
    v = ledger.mark(marks, ts_ms=int(time.time() * 1000))
    pf = ledger.to_portfolio_state(v, Decimal(db.get_state(conn, "sod_equity", str(cfg.starting_balance))))
    ks = _kill_switch(conn)
    fills = store_run.get_fills(conn, "live")
    positions = [
        {"symbol": s, "qty": money.to_text(p["qty"]), "avg_price": money.to_text(p["avg_price"]),
         "realized_pnl": money.to_text(p["realized_pnl"])}
        for s, p in store_run.get_positions(conn, "live").items() if p["qty"] != 0
    ]
    curve = store_run.get_equity_curve(conn, "live", limit=100_000)
    bench_equity = curve[-1]["benchmark_equity"] if curve else None
    max_dd = Decimal(0)
    peak = curve[0]["equity"] if curve else None
    for pt in curve:
        peak = max(peak, pt["equity"])
        if peak > 0:
            max_dd = max(max_dd, (peak - pt["equity"]) / peak * 100)
    pnls = realized_pnl_per_sell(fills)
    hit_rate = round(100 * sum(1 for p in pnls if p > 0) / len(pnls), 2) if pnls else None
    engine = RiskEngine(cfg)  # zustandslos (nur self.cfg) - eine frische Instanz rechnet identisch
    return dict(
        version=VERSION, mode=cfg.trading_mode, live_locked=cfg.live_locked,
        # E-007: Geld geht als Zeichenkette raus, nie als JSON-Zahl. Fixrunde 1,
        # Punkt 2 (Koordinator/Reviewer): starting_balance stand hier zuvor roh
        # (cfg.starting_balance), obwohl equity direkt darueber schon money.to_text()
        # nutzte - derselbe Verstoss wie bei equity/pnl/daily_pnl, nur unbemerkt
        # geblieben, weil kein Test das Feld pruefte. Siehe
        # test_alle_geldfelder_in_status_sind_kanonischer_text (Wurzelbehebung:
        # prueft die FORM aller Geldfelder, nicht nur einzelne Werte).
        equity=money.to_text(v.equity), cash=money.to_text(ledger.cash),
        starting_balance=money.to_text(cfg.starting_balance),
        pnl=money.to_text(round(v.equity - cfg.starting_balance, 8)),
        daily_pnl=money.to_text(round(v.equity - pf.start_of_day_equity, 8)),
        daily_loss_pct=round(engine.daily_loss_pct(pf), 4),
        exposure_pct=v.exposure_pct, risk="BLOCKED" if ks else "NORMAL", kill_switch=ks,
        positions=positions, trades_total=len(fills),
        benchmark=dict(symbol=cfg.benchmark_symbol,
                        equity=(money.to_text(bench_equity) if bench_equity is not None else None)),
        alpha_pct=None, hit_rate_pct=hit_rate, max_drawdown_pct=float(max_dd),
        limits=dict(max_position_pct=cfg.max_position_pct, max_daily_loss_pct=cfg.max_daily_loss_pct,
                    max_total_exposure_pct=cfg.max_total_exposure_pct),
    )


def build_health(conn: sqlite3.Connection, cfg: Config, poller_thread: threading.Thread | None,
                  started: float) -> tuple[dict, int]:
    """Der komplette Inhalt von GET /api/health samt der Entscheidung 200
    gegen 503 (503 bei market_data == 'stale' oder paper_engine == 'error').

    started ist STARTED aus web.py (Prozessstart, time.time()) - als Parameter
    durchgereicht statt hier neu erfasst, damit uptime_s exakt dieselbe
    Bedeutung behaelt wie vor der Verschiebung (Sekunden seit dem tatsaechlichen
    App-Start, nicht seit dem Import dieses Moduls)."""
    checks: dict[str, str] = {}
    try:
        conn.execute("SELECT 1").fetchone()
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "error"
    free_gb = shutil.disk_usage(cfg.data_dir).free / 1e9
    checks["disk"] = "ok" if free_gb > 1 else "low"
    checks["risk_engine"] = "ok"
    if not cfg.market_data_enabled:
        checks["paper_engine"] = "idle"
    else:
        checks["paper_engine"] = "ok" if poller_thread is not None and poller_thread.is_alive() else "error"
    checks["market_data"] = db.get_state(conn, "market_data_status", "not_configured")
    healthy = (checks["database"] == "ok" and checks["disk"] == "ok"
               and checks["market_data"] != "stale" and checks["paper_engine"] != "error")
    body = dict(
        status="healthy" if healthy else "degraded",
        version=VERSION,
        trading_mode=cfg.trading_mode,
        kill_switch=_kill_switch(conn) if checks["database"] == "ok" else None,
        uptime_s=int(time.time() - started),
        disk_free_gb=round(free_gb, 1),
        server_time=db.now(),
        **checks,
    )
    return body, (200 if healthy else 503)
