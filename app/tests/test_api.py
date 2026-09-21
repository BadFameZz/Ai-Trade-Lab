import re
from decimal import Decimal

import pytest

from aitra import db, money, store, store_run
from aitra.config import Config
from aitra.web import create_app

TOKEN = "t" * 32


@pytest.fixture
def app(tmp_path):
    return create_app(Config(100, 10, 2, 50, tmp_path, TOKEN))


@pytest.fixture
def client(app):
    return app.test_client()


def test_status_and_health(client):
    """Abweichung vom Brief (gemeldet, E-007): equity geht seit Aufgabe 6 als
    kanonische Zeichenkette raus (money.to_text), nicht mehr als JSON-Zahl -
    money darf den Browser nie als Fliesskommazahl erreichen."""
    s = client.get("/api/status").get_json()
    assert s["mode"] == "PAPER" and s["live_locked"] and s["equity"] == "100.00000000"
    h = client.get("/api/health")
    assert h.status_code == 200 and h.get_json()["database"] == "ok"


def test_initial_decision_persisted(client):
    d = client.get("/api/decisions").get_json()
    assert d[0]["symbol"] == "SYSTEM" and d[0]["action"] == "WAIT"


def test_risk_check_requires_token(client):
    r = client.post("/api/risk/check", json={"symbol": "BTCUSDC", "action": "BUY", "position_pct": 5})
    assert r.status_code == 401


def test_risk_check_journals(client, app):
    """Abweichung vom Brief (gemeldet): risk_check() braucht seit Aufgabe 6
    fuer jede Nicht-WAIT-Aktion eine gespeicherte Kerze (503 sonst, Spec dieser
    Aufgabe) - ohne _mit_marktdaten() waere schon der erste (approved) Aufruf
    503 statt eines JSON-Bodys mit 'approved', unabhaengig vom eigentlichen
    Pruefziel dieses Tests (Risk-Entscheidung wird journaliert)."""
    _mit_marktdaten(app)
    h = {"X-Admin-Token": TOKEN}
    ok = client.post("/api/risk/check", headers=h, json={"symbol": "BTCUSDC", "action": "BUY", "position_pct": 8, "confidence": 71}).get_json()
    bad = client.post("/api/risk/check", headers=h, json={"symbol": "BTCUSDC", "action": "BUY", "position_pct": 15}).get_json()
    assert ok["approved"] and not bad["approved"] and bad["code"] == "MAX_POSITION"
    assert len(client.get("/api/decisions").get_json()) == 3


def test_kill_switch_flow(client):
    assert client.post("/api/kill-switch/engage").get_json()["kill_switch"]
    assert client.get("/api/status").get_json()["risk"] == "BLOCKED"
    assert client.post("/api/kill-switch/release").status_code == 401
    assert not client.post("/api/kill-switch/release", headers={"X-Admin-Token": TOKEN}).get_json()["kill_switch"]


def test_persistence_across_restart(tmp_path):
    cfg = Config(100, 10, 2, 50, tmp_path, TOKEN)
    create_app(cfg).test_client().post("/api/kill-switch/engage")
    assert create_app(cfg).test_client().get("/api/status").get_json()["kill_switch"] is True


def _mit_marktdaten(app, symbol="BTCUSDC", interval="15m", preis="81287.03"):
    """Legt eine einzelne, bereits geschlossene Kerze fuer symbol an - Grundlage
    fuer jeden Test, der /api/risk/check oder /api/status mit echten Zahlen
    braucht.

    Abweichung vom Brief (gemeldet, nicht stillschweigend behoben): der Brief
    sah hier zusaetzlich einen toten `with app.app_context(): ... conn.row_factory__
    = None`-Block vor. Der ist nicht nur unbenutzt, sondern wirft tatsaechlich
    `AttributeError: 'sqlite3.Connection' object has no attribute 'row_factory__'`,
    weil sqlite3.Connection keine beliebigen Attribute erlaubt - jeder Aufruf
    dieser Funktion waere damit rot, bevor er die eigentliche Kerze anlegt.
    Entfernt; die zweite, unbedingt ausgefuehrte conn-Zeile leistet die
    eigentliche Arbeit bereits."""
    conn = db.connect(app.config["AITRA"].data_dir / "aitra.db")
    store.upsert_candles(conn, [
        store.CandleRow(symbol=symbol, interval=interval, open_time=0, close_time=899_999,
                         open=Decimal(preis), high=Decimal(preis), low=Decimal(preis),
                         close=Decimal(preis), volume=Decimal("1"), source="fixture",
                         fetched_at="2026-01-01T00:00:00Z"),
    ])
    conn.close()


def test_status_positionen_und_trades_total_sind_echt(client, app):
    _mit_marktdaten(app)
    conn = db.connect(app.config["AITRA"].data_dir / "aitra.db")
    store_run.ensure_run(conn, "live", "live", db.now(), "0.3.0")
    store_run.insert_fill(conn, run_id="live", decision_id=None, symbol="BTCUSDC", side="BUY",
                           candle_open_time=900_000, price=Decimal("81287.03"), qty=Decimal("0.01"),
                           gross_quote=Decimal("812.8703"), fee=Decimal("0.81"),
                           net_quote=Decimal("813.6803"), cash_after=Decimal("9186.3197"),
                           fee_bps=10.0, slippage_bps=5.0, ts=db.now())
    store_run.upsert_position(conn, run_id="live", symbol="BTCUSDC", qty=Decimal("0.01"),
                               avg_price=Decimal("81287.03"), realized_pnl=Decimal("0"),
                               updated_at=db.now())
    conn.close()

    s = client.get("/api/status").get_json()
    assert s["trades_total"] == 1, f"Pruefflaeche: {s['trades_total']} statt 1"
    assert len(s["positions"]) == 1
    assert s["positions"][0]["symbol"] == "BTCUSDC"
    assert s["positions"][0]["qty"] == "0.01000000"
    assert s["cash"] == "9186.31970000", "Kasse muss aus cash_after des letzten Fills stammen (B-3)"


def test_status_kasse_bleibt_startkapital_ohne_fills(client):
    s = client.get("/api/status").get_json()
    assert s["cash"] == "100.00000000"
    assert s["trades_total"] == 0
    assert s["positions"] == []


def test_health_meldet_market_data_wirklich(client, app):
    conn = db.connect(app.config["AITRA"].data_dir / "aitra.db")
    db.set_state(conn, "market_data_status", "warn")
    conn.close()
    h = client.get("/api/health").get_json()
    assert h["market_data"] == "warn", f"Pruefflaeche: {h['market_data']!r} statt 'warn'"


def test_health_gibt_503_bei_stale_market_data(client, app):
    conn = db.connect(app.config["AITRA"].data_dir / "aitra.db")
    db.set_state(conn, "market_data_status", "stale")
    conn.close()
    r = client.get("/api/health")
    assert r.status_code == 503


def test_health_paper_engine_idle_ohne_market_data_enabled(client):
    h = client.get("/api/health").get_json()
    assert h["paper_engine"] == "idle"


def test_risk_check_liefert_pending_fill_ueber_execute_proposal(client, app):
    """Abweichung vom Brief (gemeldet): position_pct=5 wie im Brief ergibt bei
    starting_balance=100 (Config(100, ...) der client/app-Fixture) nur 5 USDC
    Zielgroesse - nach Slippage/Gebuehr/Step-Abrundung 4,8796608 USDC, unter
    BTCUSDCs min_notional von 5 USDC (K-3). Die Order wuerde bereits an
    sizing.size_order() mit MIN_NOTIONAL scheitern, nie schwebend werden -
    gemessen, nicht geraten (siehe Bericht). 8 % bleibt unter max_position_pct
    (10) und liegt sicher ueber der Mindestgroesse."""
    _mit_marktdaten(app)
    h = {"X-Admin-Token": TOKEN}
    r = client.post("/api/risk/check", headers=h,
                     json={"symbol": "BTCUSDC", "action": "BUY", "position_pct": 8})
    body = r.get_json()
    assert body["status"] == "pending_fill", f"Pruefflaeche: {body.get('status')!r}"
    assert body["expected_fill_after_ms"] == 900_000
    # Nadeloehr: kein sofortiger Fill (E-006) - trades_total bleibt 0
    assert client.get("/api/status").get_json()["trades_total"] == 0


def test_risk_check_ohne_marktdaten_liefert_503(client):
    h = {"X-Admin-Token": TOKEN}
    r = client.post("/api/risk/check", headers=h,
                     json={"symbol": "BTCUSDC", "action": "BUY", "position_pct": 5})
    assert r.status_code == 503


def test_market_candles_endpoint(client, app):
    _mit_marktdaten(app)
    r = client.get("/api/market/candles?symbol=BTCUSDC&interval=15m&limit=10")
    assert r.status_code == 200
    body = r.get_json()
    assert len(body) == 1, f"Pruefflaeche: {len(body)} Kerzen statt 1"
    assert body[0]["close"] == "81287.03000000"
    assert isinstance(body[0]["close"], str), "Geld als String (E-007)"


def test_equity_curve_endpoint(client, app):
    conn = db.connect(app.config["AITRA"].data_dir / "aitra.db")
    store_run.ensure_run(conn, "live", "live", db.now(), "0.3.0")
    store_run.append_equity_points(conn, [store_run.EquityPoint(
        run_id="live", ts_ms=900_000, equity=Decimal("10000"), cash=Decimal("9186.32"),
        benchmark_equity=Decimal("10050"), exposure_pct=8.1,
    )])
    conn.close()
    body = client.get("/api/equity-curve?run_id=live&limit=500").get_json()
    assert body["run_id"] == "live"
    assert len(body["points"]) == 1, f"Pruefflaeche: {len(body['points'])} Punkte statt 1"
    assert isinstance(body["points"][0]["equity"], str)
    assert body["points"][0]["benchmark"] == "10050.00000000"


# Fixrunde 2, Punkt 1 (Koordinator/Reviewer): die vorherige Fassung klapperte eine
# fest eingetragene Feldliste ab und blieb bei jedem NEUEN rohen Feld (der Reviewer
# ergaenzte reserve_quote roh und der Test blieb gruen) unbemerkt gruen - selbst eine
# Momentaufnahme, nur laenger. Jetzt ein rekursiver Scan der GESAMTEN Antwort: jeder
# Zeichenkettenwert, der sich als Dezimalzahl LESEN LAESST (Ziffern, optional Punkt +
# Nachkommastellen, optional Exponent), muss in kanonischer Form stehen (money.to_text,
# exakt 8 Nachkommastellen). Versions-/Modus-/Symbolstrings wie "0.3.0", "PAPER",
# "BTCUSDC" lesen sich nicht als EINE Zahl und fallen automatisch heraus (kein
# Ausnahmefeld noetig). Prozentsaetze und Zeitstempel sind JSON-Zahlen (kein str)
# und werden vom Scan nie betrachtet, weil er nur str-Werte prueft.
_LIEST_SICH_ALS_ZAHL = re.compile(r"^-?\d+(\.\d+)?([eE][+-]?\d+)?$")
_KANONISCHE_GELDFORM = re.compile(r"^-?\d+\.\d{8}$")


def _finde_nicht_kanonische_zahlenstrings(wert, pfad="status"):
    """Geht rekursiv durch dicts/Listen und meldet jeden Zeichenkettenwert, der
    sich als Zahl liest, aber nicht die kanonische 8-Nachkommastellen-Form hat."""
    fehler = []
    if isinstance(wert, dict):
        for schluessel, teilwert in wert.items():
            fehler += _finde_nicht_kanonische_zahlenstrings(teilwert, f"{pfad}.{schluessel}")
    elif isinstance(wert, list):
        for i, teilwert in enumerate(wert):
            fehler += _finde_nicht_kanonische_zahlenstrings(teilwert, f"{pfad}[{i}]")
    elif isinstance(wert, str):
        if _LIEST_SICH_ALS_ZAHL.match(wert) and not _KANONISCHE_GELDFORM.match(wert):
            fehler.append(f"{pfad} = {wert!r} liest sich als Zahl, ist aber nicht kanonisch")
    return fehler


def test_alle_geldfelder_in_status_sind_kanonischer_text(client, app):
    """Rekursiver Formscan der gesamten /api/status-Antwort (siehe Modulkopf-
    Kommentar zu _finde_nicht_kanonische_zahlenstrings fuer die Regel/Begruendung)."""
    _mit_marktdaten(app)
    conn = db.connect(app.config["AITRA"].data_dir / "aitra.db")
    store_run.ensure_run(conn, "live", "live", db.now(), "0.3.0")
    store_run.insert_fill(conn, run_id="live", decision_id=None, symbol="BTCUSDC", side="BUY",
                           candle_open_time=900_000, price=Decimal("81287.03"), qty=Decimal("0.01"),
                           gross_quote=Decimal("812.8703"), fee=Decimal("0.81"),
                           net_quote=Decimal("813.6803"), cash_after=Decimal("9186.3197"),
                           fee_bps=10.0, slippage_bps=5.0, ts=db.now())
    store_run.upsert_position(conn, run_id="live", symbol="BTCUSDC", qty=Decimal("0.01"),
                               avg_price=Decimal("81287.03"), realized_pnl=Decimal("0"),
                               updated_at=db.now())
    store_run.append_equity_points(conn, [store_run.EquityPoint(
        run_id="live", ts_ms=900_000, equity=Decimal("9999.19"), cash=Decimal("9186.3197"),
        benchmark_equity=Decimal("10050"), exposure_pct=8.1,
    )])
    conn.close()

    s = client.get("/api/status").get_json()
    assert len(s["positions"]) == 1, "Pruefflaeche: Positionsliste darf hier nicht leer sein"
    assert s["benchmark"]["equity"] is not None, "Pruefflaeche: benchmark.equity darf hier nicht None sein"

    fehler = _finde_nicht_kanonische_zahlenstrings(s)
    assert fehler == [], "Nicht-kanonische Zahl(en) als Zeichenkette in /api/status:\n" + "\n".join(fehler)


def test_create_app_schliesst_alle_eigenen_verbindungen(tmp_path, monkeypatch):
    """Rot-Nachweis/Wurzelbehebung, Fixrunde 1 Punkt 3 (Koordinator/Reviewer):
    create_app() oeffnete drei eigene Verbindungen (Init-Block, specs-Aufbau -
    dort sogar einmal je Symbol -, Poller). Vor der Behebung wurde nur die des
    Pollers je geschlossen (dort bewusst, der Poller besitzt sie fuer seine
    Lebensdauer). Zaehlt echte sqlite3.Connection-Objekte, die aitra.db.connect()
    zurueckgibt, und prueft danach, wie viele noch offen sind. MARKET_DATA_ENABLED
    bleibt hier False (Vorgabewert), der Poller startet also gar nicht - jede
    verbleibende offene Verbindung ist ein Leck."""
    import aitra.db as db_mod

    echtes_connect = db_mod.connect
    geoeffnet: list = []

    def zaehlend(path):
        c = echtes_connect(path)
        geoeffnet.append(c)
        return c

    monkeypatch.setattr(db_mod, "connect", zaehlend)
    create_app(Config(100, 10, 2, 50, tmp_path, TOKEN))

    noch_offen = 0
    for c in geoeffnet:
        try:
            c.execute("SELECT 1")
            noch_offen += 1
        except Exception:
            pass  # sqlite3.ProgrammingError: Cannot operate on a closed database - korrekt geschlossen
    assert noch_offen == 0, (
        f"Pruefflaeche: {noch_offen} von {len(geoeffnet)} waehrend create_app() "
        f"geoeffneten Verbindungen sind noch offen"
    )
