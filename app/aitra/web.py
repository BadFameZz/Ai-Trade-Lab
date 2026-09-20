"""HTTP-API und Dashboard."""
from __future__ import annotations

import hmac
import logging
import shutil
import time
from pathlib import Path

from flask import Flask, g, jsonify, request

from . import VERSION, db
from .config import Config, load
from .risk import PortfolioState, Proposal, RiskEngine

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

    def portfolio() -> PortfolioState:
        # v0.2: noch kein Paper-Ledger → statischer Startzustand
        return PortfolioState(equity=cfg.starting_balance, start_of_day_equity=cfg.starting_balance, exposure_pct=0)

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
        pf = portfolio()
        ks = kill_switch()
        return jsonify(
            version=VERSION,
            mode=cfg.trading_mode,
            live_locked=cfg.live_locked,
            equity=pf.equity,
            cash=pf.equity * (1 - pf.exposure_pct / 100),
            starting_balance=cfg.starting_balance,
            pnl=round(pf.equity - cfg.starting_balance, 8),
            daily_pnl=round(pf.equity - pf.start_of_day_equity, 8),
            daily_loss_pct=round(engine.daily_loss_pct(pf), 4),
            exposure_pct=pf.exposure_pct,
            risk="BLOCKED" if ks else "NORMAL",
            kill_switch=ks,
            positions=[],
            trades_total=0,
            limits=dict(
                max_position_pct=cfg.max_position_pct,
                max_daily_loss_pct=cfg.max_daily_loss_pct,
                max_total_exposure_pct=cfg.max_total_exposure_pct,
            ),
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
        checks["paper_engine"] = "idle"      # noch kein Ledger
        checks["market_data"] = "not_configured"
        healthy = checks["database"] == "ok" and checks["disk"] == "ok"
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
        res = engine.check(p, portfolio(), kill_switch())
        if res.code == "DAILY_LOSS":
            db.set_state(conn(), "kill_switch", "1")
            db.log_event(conn(), "RISK_ENGINE", "WARN", "KILL_SWITCH_ENGAGED", "Tagesverlustlimit erreicht")
        did = db.add_decision(
            conn(),
            strategy_version=str(data.get("strategy_version", "manual"))[:40],
            symbol=p.symbol or "?", action=p.action or "?", confidence=p.confidence,
            reason=str(data.get("reason", ""))[:500], requested_position_pct=p.position_pct,
            approved=int(res.approved), risk_code=res.code, risk_reason=res.reason,
        )
        if not res.approved:
            db.log_event(conn(), "RISK_ENGINE", "WARN", "ORDER_REJECTED", f"{p.symbol} {res.code}")
        return jsonify(ok=True, decision_id=did, approved=res.approved, code=res.code, reason=res.reason)

    return app
