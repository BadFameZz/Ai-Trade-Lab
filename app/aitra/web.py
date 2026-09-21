"""HTTP-API und Dashboard."""
from __future__ import annotations

import hmac
import logging
import threading
import time
from decimal import Decimal
from pathlib import Path

from flask import Flask, g, jsonify, request

from . import VERSION, dashboard, db, money, poller, store, store_run
from .config import Config, load
from .execute import ExecutionContext, execute_proposal
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

    init_conn = db.connect(db_path)
    try:
        db.migrate(init_conn)
        if db.get_state(init_conn, "initialized") is None:
            db.set_state(init_conn, "initialized", db.now())
            db.set_state(init_conn, "kill_switch", "0")
            db.add_decision(init_conn, symbol="SYSTEM", action="WAIT", confidence=100,
                            reason="Paper-Engine initialisiert; wartet auf validierte Marktdaten.",
                            approved=1, risk_code="NO_ORDER", risk_reason="Keine Order")
        db.log_event(init_conn, "SYSTEM", "INFO", "STARTUP", f"v{VERSION}, mode=PAPER")
        if cfg.narrow_trading_window:
            db.log_event(
                init_conn, "RISK_ENGINE", "WARN", "NARROW_TRADING_WINDOW",
                f"STARTING_BALANCE={money.to_text(cfg.starting_balance)} mit "
                f"MAX_POSITION_PCT={cfg.max_position_pct}% ergibt ein zu enges "
                f"Handelsfenster (Schwelle 1.200 USDC, K-1)",
            )
    finally:
        # Fixrunde 1, Punkt 3 (Koordinator/Reviewer): `with db.connect(...) as conn:`
        # sieht wie ein automatisch schliessender Context-Manager aus, ist es bei
        # sqlite3.Connection aber NICHT - `__exit__` committet/rollt nur die
        # offene Transaktion zurueck, schliesst die Verbindung selbst jedoch nicht
        # (anders als z.B. bei Dateien). Ohne dieses ausdrueckliche close() blieb
        # die Verbindung fuer die Lebensdauer des Prozesses offen.
        init_conn.close()

    specs_conn = db.connect(db_path)
    try:
        specs = {s: store.get_symbol_spec(specs_conn, s) or money.BUILTIN_SPECS.get(s)
                 for s in cfg.market_symbols}
    finally:
        # Dieselbe Falle wie oben, hier zusaetzlich verschaerft: die Verbindung
        # stand vorher direkt in der Dict-Comprehension, also einmal PRO SYMBOL
        # (2 bei den Standardsymbolen) und nie geschlossen. Jetzt eine einzige,
        # wiederverwendete Verbindung, die danach schliesst.
        specs_conn.close()
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
        # Rechnung in dashboard.py (Ruling des Koordinators): web.py sammelt nur
        # noch Anfragekontext ein und liefert aus, was dort berechnet wird.
        return jsonify(**dashboard.build_status(conn(), cfg))

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
        body, code = dashboard.build_health(conn(), cfg, app.config.get("AITRA_POLLER_THREAD"), STARTED)
        return jsonify(body), code

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
        ledger = dashboard.build_live_ledger(conn(), cfg)
        ex_ctx = ExecutionContext(conn=conn(), run_id="live", ledger=ledger, engine=engine,
                                   specs=specs, fee_bps=cfg.fee_bps, slippage_bps=cfg.slippage_bps,
                                   clock=poller.WallClock(), kill_switch=kill_switch())
        sod = Decimal(db.get_state(conn(), "sod_equity", str(cfg.starting_balance)))
        marks = dashboard.last_prices(conn(), cfg)
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
