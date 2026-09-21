"""V-5 (Gesamtreview A2): der ausgelieferte Betriebszustand hatte null Pruefflaeche.

KEIN einziger Test rief create_app() mit market_data_enabled=True. Deshalb kam
B-1 durch - nichts fuhr das System so, wie es ausgeliefert wird: Poller-Thread
an, echter BinanceClient-Code, echte Veraltet-Erkennung, echtes /api/health.

Hier laeuft genau das, nur ohne Netz. Ersetzt sind ausschliesslich
- der urllib-Opener (_Opener) und
- die Wanduhr (SimClock, die je Zyklus um MARKET_POLL_S vorrueckt).
Der ganze Weg darueber - BinanceClient.klines() samt Verwerfen der laufenden
Kerze, poll_once(), staleness(), Kill Switch, Equity-Kurve, Flask - ist echt.

Warum die Uhr ZWISCHEN den Zyklen tickt und nicht im _Opener: ein erster
Entwurf rueckte sie im klines-Aufruf vor, also MITTEN im Zyklus. Damit lag
zwischen der Serverzeit (vor dem Aufruf bestimmt) und der Uhr in
staleness() (danach gelesen) ein Sprung von 60 s - und der Test meldete
'stale' bei einem Datenalter von 260 s. Das war ein Fehler des Testdoubles,
kein Befund; gemessen und hier festgehalten, damit ihn niemand ein zweites
Mal fuer einen Befund haelt (vgl. die aufgeraeumte SimClock-Fehldiagnose in
test_poller.py).
"""
from __future__ import annotations

import json
import threading
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import pytest

from aitra import db, poller, store, store_run
from aitra.binance import BinanceClient
from aitra.config import Config
from aitra.marketdata import SimClock
from aitra.web import create_app

TOKEN = "t" * 32
INTERVALL_MS = 900_000
POLL_SCHRITT_MS = 60_000   # der ausgelieferte MARKET_POLL_S, simuliert
ZYKLEN = 16                # mindestens 15 gefordert


class _Antwort:
    def __init__(self, body: bytes) -> None:
        self._b, self._p = body, 0

    def read(self, n: int = -1) -> bytes:
        d = self._b[self._p:] if n is None or n < 0 else self._b[self._p:self._p + n]
        self._p += len(d)
        return d

    def close(self): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _Opener:
    """Der einzige Ersatz fuer das Netz: synchrone Serverzeit, puenktliche
    Kerzen zum jeweiligen Uhrstand. Nach dem letzten gezaehlten Zyklus faellt
    er aus, damit der Poller-Thread in Backoff parkt und dem Test nicht mehr
    in den Zustand schreibt."""

    def __init__(self, clock: SimClock, fertig: threading.Event) -> None:
        self.clock, self.fertig = clock, fertig

    def _kerzen(self) -> list:
        grenze = (self.clock.now_ms() // INTERVALL_MS) * INTERVALL_MS
        return [[ot, "81287.00", "81300.00", "81200.00", "81287.03", "12.345",
                 ot + INTERVALL_MS - 1, "1", 1, "0", "0", "0"]
                for ot in (grenze - 2 * INTERVALL_MS, grenze - INTERVALL_MS)]

    def open(self, req, timeout=None):
        pfad = urlsplit(req.full_url).path
        if pfad == "/api/v3/time":
            return _Antwort(json.dumps({"serverTime": self.clock.now_ms()}).encode())
        if pfad != "/api/v3/klines":
            raise AssertionError(f"unerwarteter Pfad: {pfad}")
        if self.fertig.is_set():
            raise OSError("Testdouble: Lauf beendet, Poller darf in Backoff parken")
        parse_qs(urlsplit(req.full_url).query)["symbol"]  # Pruefflaeche: Parameter da
        return _Antwort(json.dumps(self._kerzen()).encode())


@pytest.fixture
def lieferzustand(tmp_path, monkeypatch):
    """create_app() mit MARKET_DATA_ENABLED=True gegen eine Attrappe.

    MARKET_POLL_S=0 betrifft nur die ECHTE Wartezeit des Threads (der Test
    soll nicht 16 Minuten schlafen); der simulierte Schritt ist der
    ausgelieferte Wert von 60 s."""
    cfg = Config(Decimal("10000"), 10, 2, 50, tmp_path, TOKEN,
                  market_data_enabled=True, market_poll_s=0)
    clock = SimClock(100 * INTERVALL_MS + 200_000)  # 200 s nach Kerzenschluss
    fertig = threading.Event()
    protokoll: list[dict] = []
    zaehler = {"n": 0}
    echtes_poll_once = poller.poll_once

    def tickendes_poll_once(pc):
        if zaehler["n"] > 0 and not fertig.is_set():
            clock.set(clock.now_ms() + POLL_SCHRITT_MS)
        zaehler["n"] += 1
        ergebnis = echtes_poll_once(pc)
        if not fertig.is_set():
            protokoll.append({
                "zyklus": zaehler["n"], "t_s": clock.now_ms() // 1000,
                "status": ergebnis.staleness.status if ergebnis.staleness else None,
                "age_s": None if ergebnis.staleness is None else round(ergebnis.staleness.data_age_s, 1),
                "skew_s": None if ergebnis.staleness is None else round(ergebnis.staleness.clock_skew_s, 1),
                "kill_switch": db.get_state(pc.conn, "kill_switch", "0"),
                "market_data": db.get_state(pc.conn, "market_data_status", "not_configured"),
            })
            if zaehler["n"] >= ZYKLEN:
                fertig.set()
        return ergebnis

    monkeypatch.setattr(poller, "WallClock", lambda: clock)
    monkeypatch.setattr(poller, "BinanceClient",
                        lambda base_url: BinanceClient(base_url, opener=_Opener(clock, fertig)))
    monkeypatch.setattr(poller, "poll_once", tickendes_poll_once)
    app = create_app(cfg)
    assert fertig.wait(timeout=60), f"Poller erreichte nur {zaehler['n']} von {ZYKLEN} Zyklen"
    return app, cfg, protokoll


def test_v5_lieferzustand_bleibt_ueber_15_zyklen_gesund(lieferzustand):
    """Der Test, der B-1 gefunden haette: 16 Zyklen a 60 s im Lieferzustand,
    synchrone Uhr, puenktliche Kerzen. kill_switch bleibt "0", market_data
    bleibt "ok", /api/health bleibt 200, und es entstehen Kerzen und
    Equity-Punkte."""
    app, cfg, protokoll = lieferzustand

    assert len(protokoll) >= 15, f"gemessen: {len(protokoll)} Zyklen"
    schlecht = [p for p in protokoll
                if p["kill_switch"] != "0" or p["market_data"] != "ok" or p["status"] != "ok"]
    assert schlecht == [], f"ungesunde Zyklen: {schlecht[:3]} (von {len(protokoll)})"

    conn = db.connect(cfg.data_dir / "aitra.db")
    assert db.get_state(conn, "kill_switch", "0") == "0", "Kill Switch am Ende gesetzt"
    assert db.get_state(conn, "market_data_status") == "ok", (
        f"gemessen: {db.get_state(conn, 'market_data_status')!r}, "
        f"Datenalter {db.get_state(conn, 'market_data_age_s')!r}"
    )
    for symbol in cfg.market_symbols:
        assert store.get_candles(conn, symbol, cfg.market_interval, limit=100), (
            f"keine Kerzen fuer {symbol} gespeichert"
        )
    assert store_run.get_equity_curve(conn, "live", limit=1000), "keine Equity-Punkte entstanden"
    conn.close()

    h = app.test_client().get("/api/health")
    assert h.status_code == 200, f"/api/health lieferte {h.status_code}: {h.get_json()}"
    body = h.get_json()
    assert body["status"] == "healthy" and body["market_data"] == "ok", f"gemessen: {body}"
    assert body["paper_engine"] == "ok", f"Poller-Thread nicht am Leben: {body}"


def test_v5_lieferzustand_schreibt_equity_punkte_je_kerze_nicht_je_poll(lieferzustand):
    """V-6 im Lieferzustand: 16 Zyklen a 60 s = 15 min simulierte Zeit. In
    dieser Spanne wird genau ein Kerzenwechsel sichtbar - vorher entstand ein
    Equity-Punkt JE POLL (gemessen: 14 Punkte bei 1 Kerze)."""
    app, cfg, protokoll = lieferzustand
    conn = db.connect(cfg.data_dir / "aitra.db")
    punkte = store_run.get_equity_curve(conn, "live", limit=1000)
    zeiten = sorted(p["ts_ms"] for p in punkte)
    assert len(punkte) <= 3, (
        f"{len(punkte)} Equity-Punkte in {len(protokoll)} Zyklen / 15 min - erwartet "
        f"hoechstens 3 (Startkerze + ein Kerzenwechsel), ts_ms={zeiten}"
    )
    assert all(t % INTERVALL_MS == INTERVALL_MS - 1 for t in zeiten), (
        f"ts_ms muss eine Kerzen-close_time sein, gemessen: {zeiten}"
    )
    conn.close()
