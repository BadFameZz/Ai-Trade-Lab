"""HTTP-API und Dashboard."""
from __future__ import annotations

import hmac
import logging
import shutil
import threading
import time
from decimal import Decimal
from pathlib import Path

from flask import Flask, g, jsonify, request

from . import VERSION, db, money, poller, store, store_run
from .config import Config, load
from .execute import ExecutionContext, execute_proposal
from .ledger import Ledger, Position, realized_pnl_per_sell
from .risk import Proposal, RiskEngine

log = logging.getLogger("aitra.web")
STARTED = time.time()


def create_app(cfg: Config | None = None) -> Flask:
    cfg = cfg or load()
    static = Path(__file__).resolve().parent.parent / "static"
    app = Flask(__name__, static_folder=str(static), static_url_path="")
    app.config["AITRA"] = cfg
    db_path = cfg.data_dir / "aitra.db"
    engine = RiskEngine(cfg)

    with db.connect(db_path) as conn:
        db.migrate(conn)
        if db.get_state(conn, "initialized") is None:
            db.set_state(conn, "initialized", db.now())
            db.set_state(conn, "kill_switch", "0")
            db.add_decision(conn, symbol="SYSTEM", action="WAIT", confidence=100,
                            reason="Paper-Engine initialisiert; wartet auf validierte Marktdaten.",
                            approved=1, risk_code="NO_ORDER", risk_reason="Keine Order")
        db.log_event(conn, "SYSTEM", "INFO", "STARTUP", f"v{VERSION}, mode=PAPER")

    specs = {s: store.get_symbol_spec(db.connect(db_path), s) or money.BUILTIN_SPECS.get(s)
             for s in cfg.market_symbols}
    specs = {s: sp for s, sp in specs.items() if sp is not None}
    poller_thread: threading.Thread | None = None
    if cfg.market_data_enabled:
        poller_thread, _ = poller.start(db.connect(db_path), cfg, specs)
    app.config["AITRA_POLLER_THREAD"] = poller_thread
    app.config["AITRA_SPECS"] = specs

    def conn():
        if "db" not in g:
            g.db = db.connect(db_path)
        return g.db

    @app.teardown_appcontext
    def _close(_exc):
        c = g.pop("db", None)
        if c is not None:
            c.close()

    def kill_switch() -> bool:
        return db.get_state(conn(), "kill_switch", "0") == "1"

    def _live_ledger() -> Ledger:
        """Rekonstruiert den Live-Ledger aus dem Journal (A-14) - bei jedem
        Request neu, absichtlich: es gibt keinen mit dem Poller-Thread
        geteilten Ledger-Zustand, um Threading-Kollisionen zwischen dem
        Poller (poller.py) und gunicorn-Request-Threads zu vermeiden."""
        ledger = Ledger(starting_cash=cfg.starting_balance, specs=app.config["AITRA_SPECS"],
                         fee_bps=cfg.fee_bps, slippage_bps=cfg.slippage_bps)
        positions = {s: Position(s, p["qty"], p["avg_price"], p["realized_pnl"])
                     for s, p in store_run.get_positions(conn(), "live").items()}
        fills = store_run.get_fills(conn(), "live")
        cash = fills[-1]["cash_after"] if fills else cfg.starting_balance
        ledger.restore(positions, cash)
        return ledger

    def _last_prices() -> dict:
        """Letzter bekannter Schlusskurs je konfiguriertem Symbol - deckt jede
        gehaltene Position ab, weil Positionen nur in konfigurierten Symbolen
        entstehen koennen (execute_proposal lehnt alles andere mit NO_SPEC ab)."""
        preise = {}
        for sym in app.config["AITRA_SPECS"]:
            rows = store.get_candles(conn(), sym, cfg.market_interval, limit=1)
            if rows:
                preise[sym] = rows[-1].close
        return preise

    def authorized() -> bool:
        token = request.headers.get("X-Admin-Token", "")
        return bool(token) and hmac.compare_digest(token, cfg.admin_token)

    def need_json():
        if not request.is_json:
            return jsonify(ok=False, error="Content-Type muss application/json sein"), 415
        return None

    @app.after_request
    def _headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        )
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.errorhandler(404)
    def _404(_e):
        if request.path.startswith("/api/"):
            return jsonify(ok=False, error="Nicht gefunden"), 404
        return app.send_static_file("index.html")

    @app.errorhandler(Exception)
    def _err(e):
        log.exception("Unbehandelter Fehler")
        try:
            db.log_event(conn(), "API", "ERROR", "EXCEPTION", type(e).__name__)
        except Exception:
            pass
        return jsonify(ok=False, error="Interner Fehler"), 500

    # ---------- Seiten ----------

    @app.get("/")
    def home():
        return app.send_static_file("index.html")

    # ---------- Lesende API ----------

    @app.get("/api/version")
    def version():
        return jsonify(version=VERSION)

    @app.get("/api/status")
    def status():
        ledger = _live_ledger()
        marks = _last_prices()
        v = ledger.mark(marks, ts_ms=int(time.time() * 1000))
        pf = ledger.to_portfolio_state(v, Decimal(db.get_state(conn(), "sod_equity", str(cfg.starting_balance))))
        ks = kill_switch()
        fills = store_run.get_fills(conn(), "live")
        positions = [
            {"symbol": s, "qty": money.to_text(p["qty"]), "avg_price": money.to_text(p["avg_price"]),
             "realized_pnl": money.to_text(p["realized_pnl"])}
            for s, p in store_run.get_positions(conn(), "live").items() if p["qty"] != 0
        ]
        curve = store_run.get_equity_curve(conn(), "live", limit=100_000)
        bench_equity = curve[-1]["benchmark_equity"] if curve else None
        max_dd = Decimal(0)
        peak = curve[0]["equity"] if curve else None
        for pt in curve:
            peak = max(peak, pt["equity"])
            if peak > 0:
                max_dd = max(max_dd, (peak - pt["equity"]) / peak * 100)
        sells = [f for f in fills if f["side"] == "SELL"]
        pnls = realized_pnl_per_sell(fills)
        hit_rate = round(100 * sum(1 for p in pnls if p > 0) / len(pnls), 2) if pnls else None
        return jsonify(
            version=VERSION, mode=cfg.trading_mode, live_locked=cfg.live_locked,
            # E-007/Randbedingung dieser Aufgabe: Geld geht als Zeichenkette raus, nie als
            # JSON-Zahl. Abweichung vom Brief (gemeldet): equity/pnl/daily_pnl standen dort
            # als rohes Decimal, das Flasks JSON-Provider zwar ebenfalls in einen String
            # verwandelt (str(Decimal(...))), aber ohne die kanonischen 8 Nachkommastellen -
            # inkonsistent zu cash/positions/benchmark, die bereits money.to_text() nutzen.
            equity=money.to_text(v.equity), cash=money.to_text(ledger.cash),
            starting_balance=cfg.starting_balance,
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

    @app.get("/api/decisions")
    def decisions():
        limit = min(max(request.args.get("limit", 50, type=int), 1), 500)
        return jsonify(db.list_rows(conn(), "decisions", limit))

    @app.get("/api/events")
    def events():
        limit = min(max(request.args.get("limit", 50, type=int), 1), 500)
        return jsonify(db.list_rows(conn(), "events", limit))

    @app.get("/api/health")
    def health():
        checks: dict[str, str] = {}
        try:
            conn().execute("SELECT 1").fetchone()
            checks["database"] = "ok"
        except Exception:
            checks["database"] = "error"
        free_gb = shutil.disk_usage(cfg.data_dir).free / 1e9
        checks["disk"] = "ok" if free_gb > 1 else "low"
        checks["risk_engine"] = "ok"
        thread = app.config.get("AITRA_POLLER_THREAD")
        if not cfg.market_data_enabled:
            checks["paper_engine"] = "idle"
        else:
            checks["paper_engine"] = "ok" if thread is not None and thread.is_alive() else "error"
        checks["market_data"] = db.get_state(conn(), "market_data_status", "not_configured")
        healthy = (checks["database"] == "ok" and checks["disk"] == "ok"
                   and checks["market_data"] != "stale" and checks["paper_engine"] != "error")
        body = dict(
            status="healthy" if healthy else "degraded",
            version=VERSION,
            trading_mode=cfg.trading_mode,
            kill_switch=kill_switch() if checks["database"] == "ok" else None,
            uptime_s=int(time.time() - STARTED),
            disk_free_gb=round(free_gb, 1),
            server_time=db.now(),
            **checks,
        )
        return jsonify(body), 200 if healthy else 503

    # ---------- Kill Switch ----------

    @app.post("/api/kill-switch/engage")
    def ks_engage():
        # Sicherheitsaktion: bewusst ohne Token erlaubt
        db.set_state(conn(), "kill_switch", "1")
        db.log_event(conn(), "RISK_ENGINE", "WARN", "KILL_SWITCH_ENGAGED", request.remote_addr or "")
        return jsonify(ok=True, kill_switch=True)

    @app.post("/api/kill-switch/release")
    def ks_release():
        if not authorized():
            db.log_event(conn(), "RISK_ENGINE", "WARN", "KILL_SWITCH_RELEASE_DENIED", request.remote_addr or "")
            return jsonify(ok=False, error="Admin-Token erforderlich"), 401
        db.set_state(conn(), "kill_switch", "0")
        db.log_event(conn(), "RISK_ENGINE", "INFO", "KILL_SWITCH_RELEASED", request.remote_addr or "")
        return jsonify(ok=True, kill_switch=False)

    # ---------- Risiko-Prüfung (Trockenlauf, wird im Journal protokolliert) ----------

    @app.post("/api/risk/check")
    def risk_check():
        if not authorized():
            return jsonify(ok=False, error="Admin-Token erforderlich"), 401
        if (err := need_json()) is not None:
            return err
        data = request.get_json(silent=True) or {}
        try:
            p = Proposal(
                symbol=str(data.get("symbol", "")).upper().strip(),
                action=str(data.get("action", "")).upper().strip(),
                position_pct=float(data.get("position_pct", 0) or 0),
                confidence=None if data.get("confidence") is None else float(data["confidence"]),
            )
        except (TypeError, ValueError):
            return jsonify(ok=False, error="Ungültige Werte"), 422

        rows = store.get_candles(conn(), p.symbol, cfg.market_interval, limit=1) if p.symbol else []
        if not rows and p.action != "WAIT":
            return jsonify(ok=False, error=f"Keine Marktdaten für {p.symbol}"), 503
        ref_price = rows[-1].close if rows else Decimal(0)
        ts_ms = rows[-1].close_time if rows else int(time.time() * 1000)

        specs = app.config["AITRA_SPECS"]
        ledger = _live_ledger()
        ex_ctx = ExecutionContext(conn=conn(), run_id="live", ledger=ledger, engine=engine,
                                   specs=specs, fee_bps=cfg.fee_bps, slippage_bps=cfg.slippage_bps,
                                   clock=poller.WallClock(), kill_switch=kill_switch())
        sod = Decimal(db.get_state(conn(), "sod_equity", str(cfg.starting_balance)))
        marks = _last_prices()
        result = execute_proposal(
            p, ex_ctx, marks=marks, ts_ms=ts_ms, ref_price=ref_price, start_of_day_equity=sod,
            strategy_version=str(data.get("strategy_version", "manual"))[:40],
            reason=str(data.get("reason", ""))[:500], next_candle=None,
        )
        if result.code == "DAILY_LOSS":
            db.set_state(conn(), "kill_switch", "1")
            db.log_event(conn(), "RISK_ENGINE", "WARN", "KILL_SWITCH_ENGAGED", "Tagesverlustlimit erreicht")
        if result.status == "rejected":
            db.log_event(conn(), "RISK_ENGINE", "WARN", "ORDER_REJECTED", f"{p.symbol} {result.code}")
        return jsonify(
            ok=True, decision_id=result.decision_id, approved=result.approved,
            code=result.code, reason=result.reason, status=result.status,
            expected_fill_after_ms=(rows[-1].close_time + 1 if rows else None),
        )

    # ---------- Marktdaten und Equity-Kurve ----------

    @app.get("/api/market/candles")
    def market_candles():
        symbol = request.args.get("symbol", "").upper().strip()
        interval = request.args.get("interval", cfg.market_interval)
        limit = min(max(request.args.get("limit", 500, type=int), 1), 1000)
        rows = store.get_candles(conn(), symbol, interval, limit=limit)
        return jsonify([
            dict(open_time=r.open_time, close_time=r.close_time, open=money.to_text(r.open),
                 high=money.to_text(r.high), low=money.to_text(r.low), close=money.to_text(r.close),
                 volume=money.to_text(r.volume))
            for r in rows
        ])

    @app.get("/api/equity-curve")
    def equity_curve():
        run_id = request.args.get("run_id", "live")
        limit = min(max(request.args.get("limit", 500, type=int), 1), 5000)
        points = store_run.get_equity_curve(conn(), run_id, limit=limit)
        return jsonify(run_id=run_id, points=[
            dict(ts_ms=p["ts_ms"], equity=money.to_text(p["equity"]), cash=money.to_text(p["cash"]),
                 benchmark=(money.to_text(p["benchmark_equity"]) if p["benchmark_equity"] is not None else None),
                 exposure_pct=p["exposure_pct"])
            for p in points
        ])

    return app
