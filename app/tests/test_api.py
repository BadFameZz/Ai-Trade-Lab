import re
from decimal import Decimal

import pytest

from aitra import db, money, store, store_run
from aitra.config import Config
from aitra.web import create_app

TOKEN = "t" * 32
KERZE_MS = 900_000  # Kerzenabstand der Fixtur, passend zum interval-Vorgabewert "15m"


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


def _mit_marktdaten(app, symbol="BTCUSDC", interval="15m", preis="81287.03",
                     vorgeschichte=("115560.01", "100000.00")):
    """Legt eine kurze, bereits geschlossene Kerzenhistorie fuer symbol an -
    Grundlage fuer jeden Test, der /api/risk/check oder /api/status mit echten
    Zahlen braucht. `preis` ist der Schlusskurs der JUENGSTEN Kerze.

    B-D1 (2026-09-23): Diese Funktion legte frueher GENAU EINE Kerze an. Bei
    einer Zeile sind aelteste und neueste dieselbe Zeile - store.get_candles()
    sortiert ORDER BY open_time ASC LIMIT ? und liefert damit die AELTESTEN N,
    aber jeder Aufrufer liest rows[-1] als "die neueste". Mit einer Kerze war
    diese Pruefflaeche leer, und der Fehler ueberlebte die gesamte Testsuite
    (auf CT 107 gemessen: /api/market/candles zeigte 2025-08-19 / 115560.01
    statt 2026-09-23 / 85454.11, rund 35 % daneben). Die Vorgeschichte hat
    deshalb bewusst DEUTLICH andere Preise als die juengste Kerze.

    Abweichung vom Brief (gemeldet, nicht stillschweigend behoben): der Brief
    sah hier zusaetzlich einen toten `with app.app_context(): ... conn.row_factory__
    = None`-Block vor. Der ist nicht nur unbenutzt, sondern wirft tatsaechlich
    `AttributeError: 'sqlite3.Connection' object has no attribute 'row_factory__'`,
    weil sqlite3.Connection keine beliebigen Attribute erlaubt - jeder Aufruf
    dieser Funktion waere damit rot, bevor er die eigentliche Kerze anlegt.
    Entfernt; die zweite, unbedingt ausgefuehrte conn-Zeile leistet die
    eigentliche Arbeit bereits.

    R3 (Reviewer-Befund, Fixrunde nach dem Gesamtreview): der Rueckgabewert -
    die open_time der JUENGSTEN angelegten Kerze - war tote Verkabelung. Alle
    sechs Aufrufstellen warfen ihn weg, waehrend zwei Tests dieselbe Zahl
    (1.800.000 bzw. 2.700.000) hart hineinschrieben. Entschieden wurde fuer
    VERWENDEN statt Streichen: die beiden Tests behaupten in ihrem eigenen
    Text, sie pruefen die JUENGSTE Kerze. Steht die Zahl hart drin, ist das bei
    einer laengeren `vorgeschichte` nicht mehr die juengste, sondern eine
    beliebige mittlere - und der Test prueft still etwas anderes, als er sagt.
    Genau die B-D1-Falle, nur eine Ebene hoeher. Der Rueckgabewert hat
    seinerseits eine eigene Prueffläche bekommen (siehe
    test_mit_marktdaten_liefert_die_open_time_der_juengsten_kerze)."""
    preise = [*vorgeschichte, preis]  # aeltest -> juengst
    conn = db.connect(app.config["AITRA"].data_dir / "aitra.db")
    store.upsert_candles(conn, [
        store.CandleRow(symbol=symbol, interval=interval, open_time=i * KERZE_MS,
                         close_time=i * KERZE_MS + KERZE_MS - 1,
                         open=Decimal(p), high=Decimal(p), low=Decimal(p),
                         close=Decimal(p), volume=Decimal("1"), source="fixture",
                         fetched_at="2026-01-01T00:00:00Z")
        for i, p in enumerate(preise)
    ])
    # Pruefflaeche: ohne mehrere, unterschiedlich bepreiste Kerzen koennte kein
    # Test dieser Datei eine Verwechslung von "aelteste" und "neueste" sehen.
    assert len(preise) >= 2 and len(set(preise)) == len(preise)
    conn.close()
    return (len(preise) - 1) * KERZE_MS  # open_time der juengsten Kerze


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
    juengste_open_time = _mit_marktdaten(app)
    h = {"X-Admin-Token": TOKEN}
    r = client.post("/api/risk/check", headers=h,
                     json={"symbol": "BTCUSDC", "action": "BUY", "position_pct": 8})
    body = r.get_json()
    assert body["status"] == "pending_fill", f"Pruefflaeche: {body.get('status')!r}"
    # B-D1: die JUENGSTE Kerze bestimmt die Fuellgrenze, nicht die aelteste der
    # drei aus _mit_marktdaten(). R3: die Grenze wird aus dem Rueckgabewert der
    # Fixtur abgeleitet (close_time + 1 = open_time + KERZE_MS) statt hart
    # hineingeschrieben - sonst zeigte die Zahl nach einer laengeren
    # `vorgeschichte` weiter auf eine mittlere Kerze, waehrend der Text oben
    # "die juengste" behauptet.
    assert juengste_open_time > 0, (
        "Pruefflaeche: die Fixtur muss MEHRERE Kerzen angelegt haben - bei einer "
        "einzigen waere open_time 0 und der Test blind fuer die Verwechslung von "
        "aeltester und juengster Kerze (B-D1)."
    )
    erwartet = juengste_open_time + KERZE_MS  # = close_time + 1 der juengsten Kerze
    assert body["expected_fill_after_ms"] == erwartet, (
        f"expected_fill_after_ms = {body['expected_fill_after_ms']}, erwartet {erwartet} "
        f"(close_time + 1 der juengsten Kerze, open_time {juengste_open_time})"
    )
    # Nadeloehr: kein sofortiger Fill (E-006) - trades_total bleibt 0
    assert client.get("/api/status").get_json()["trades_total"] == 0


def test_risk_check_ohne_marktdaten_liefert_503(client):
    h = {"X-Admin-Token": TOKEN}
    r = client.post("/api/risk/check", headers=h,
                     json={"symbol": "BTCUSDC", "action": "BUY", "position_pct": 5})
    assert r.status_code == 503


def test_market_candles_endpoint(client, app):
    """B-D1: war eine Attrappe. Vorher legte _mit_marktdaten() genau EINE Kerze
    an und der Test prueft `len(body) == 1` - bei einer Zeile sind aelteste und
    neueste dieselbe Zeile, der Endpunkt konnte die beiden gar nicht
    verwechseln. Jetzt drei Kerzen mit drei verschiedenen Preisen, und geprueft
    wird die REIHENFOLGE und WELCHE Kerzen ankommen."""
    juengste_open_time = _mit_marktdaten(app)
    r = client.get("/api/market/candles?symbol=BTCUSDC&interval=15m&limit=10")
    assert r.status_code == 200
    body = r.get_json()
    assert len(body) == 3, f"Pruefflaeche: {len(body)} Kerzen statt 3"
    # R3: der letzte Eintrag kommt aus dem Rueckgabewert der Fixtur, nicht als
    # harte Zahl - er ist genau die Behauptung dieses Tests ("die juengste
    # Kerze steht hinten").
    assert [k["open_time"] for k in body] == [0, KERZE_MS, juengste_open_time], (
        f"chronologisch aufsteigend erwartet, gemessen: {[k['open_time'] for k in body]}"
    )
    assert [k["close"] for k in body] == [
        "115560.01000000", "100000.00000000", "81287.03000000"
    ], f"gemessen: {[k['close'] for k in body]}"
    assert isinstance(body[0]["close"], str), "Geld als String (E-007)"

    # Das eigentliche Nadeloehr: ein bindendes LIMIT muss die AELTESTEN Kerzen
    # wegschneiden, nicht die juengsten (auf CT 107 kam mit limit=3 der Stand
    # vom 2025-08-19 statt vom 2026-09-23 zurueck).
    gedeckelt = client.get("/api/market/candles?symbol=BTCUSDC&interval=15m&limit=2").get_json()
    assert len(gedeckelt) == 2, f"Pruefflaeche: {len(gedeckelt)} Kerzen statt 2"
    assert [k["open_time"] for k in gedeckelt] == [KERZE_MS, juengste_open_time], (
        f"limit=2 muss die juengsten zwei Kerzen liefern, aufsteigend; gemessen: "
        f"{[k['open_time'] for k in gedeckelt]} mit Schluessen {[k['close'] for k in gedeckelt]}"
    )
    assert gedeckelt[-1]["close"] == "81287.03000000"


def test_mit_marktdaten_liefert_die_open_time_der_juengsten_kerze(client, app):
    """R3: die Prueffläche fuer den Rueckgabewert der Fixtur selbst.

    Zwei Tests dieser Datei leiten jetzt ihre Erwartung aus diesem Wert ab.
    Waere die Formel falsch, waeren beide still falsch - und weil sie ihre
    Erwartung aus derselben Quelle ziehen, wuerden sie es nicht bemerken.
    Deshalb wird der Wert hier gegen eine UNABHAENGIGE Quelle geprueft: die
    Kerzen, wie sie GET /api/market/candles zurueckgibt.

    Bewusst mit einer ANDEREN Fixturlaenge als der Vorgabewert (fuenf statt
    drei Kerzen) - eine Formel, die nur zufaellig fuer drei stimmt, faellt hier
    auf."""
    juengste = _mit_marktdaten(
        app, preis="70000.00",
        vorgeschichte=("115560.01", "100000.00", "90000.00", "80000.00"),
    )
    body = client.get("/api/market/candles?symbol=BTCUSDC&interval=15m&limit=10").get_json()
    assert len(body) == 5, f"Pruefflaeche: {len(body)} Kerzen statt 5"
    assert body[-1]["close"] == "70000.00000000", (
        f"Pruefflaeche: hinten muss die juengste Kerze stehen, gemessen {body[-1]['close']}"
    )
    assert juengste == body[-1]["open_time"], (
        f"_mit_marktdaten() meldet open_time {juengste} als juengste Kerze, der "
        f"Kerzenendpunkt liefert aber {body[-1]['open_time']} "
        f"(alle: {[k['open_time'] for k in body]}). Jeder Test, der seine Erwartung "
        "aus diesem Rueckgabewert ableitet, prueft damit die falsche Kerze."
    )
    assert juengste != body[0]["open_time"], (
        "Pruefflaeche: juengste und aelteste open_time duerfen nicht dieselbe Zahl "
        "sein, sonst unterscheidet dieser Test die beiden Deutungen nicht (B-D1)."
    )


def test_equity_curve_endpoint(client, app):
    """B-D1: dieselbe Attrappe wie bei den Kerzen - ein einziger Punkt kann
    nicht zeigen, ob der Endpunkt die aeltesten oder die juengsten liefert."""
    conn = db.connect(app.config["AITRA"].data_dir / "aitra.db")
    store_run.ensure_run(conn, "live", "live", db.now(), "0.3.0")
    store_run.append_equity_points(conn, [
        store_run.EquityPoint(
            run_id="live", ts_ms=(i + 1) * 900_000, equity=Decimal(str(10_000 + i)),
            cash=Decimal("9186.32"), benchmark_equity=Decimal(str(10_050 + i)),
            exposure_pct=8.1,
        )
        for i in range(3)
    ])
    conn.close()
    body = client.get("/api/equity-curve?run_id=live&limit=500").get_json()
    assert body["run_id"] == "live"
    assert len(body["points"]) == 3, f"Pruefflaeche: {len(body['points'])} Punkte statt 3"
    assert [p["ts_ms"] for p in body["points"]] == [900_000, 1_800_000, 2_700_000]
    assert isinstance(body["points"][0]["equity"], str)
    assert body["points"][-1]["benchmark"] == "10052.00000000"

    # Bindendes LIMIT: die juengsten Punkte muessen ueberleben, aufsteigend.
    gedeckelt = client.get("/api/equity-curve?run_id=live&limit=2").get_json()["points"]
    assert len(gedeckelt) == 2, f"Pruefflaeche: {len(gedeckelt)} Punkte statt 2"
    assert [p["ts_ms"] for p in gedeckelt] == [1_800_000, 2_700_000], (
        f"limit=2 muss die juengsten zwei Punkte liefern, aufsteigend; gemessen: "
        f"{[p['ts_ms'] for p in gedeckelt]} mit equity {[p['equity'] for p in gedeckelt]}"
    )
    assert gedeckelt[-1]["equity"] == "10002.00000000"


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


def test_a22_narrow_trading_window_erzeugt_genau_ein_ereignis(tmp_path):
    cfg = Config(Decimal("100"), 10, 2, 50, tmp_path, "t" * 32, narrow_trading_window=True)
    rows = create_app(cfg).test_client().get("/api/events?limit=200").get_json()
    treffer = [r for r in rows if r["event"] == "NARROW_TRADING_WINDOW"]
    assert len(treffer) == 1, f"Pruefflaeche: {len(treffer)} Ereignisse statt 1"
    assert "1200" in treffer[0]["detail"] or "1.200" in treffer[0]["detail"]


def test_a22_kein_ereignis_bei_ausreichendem_kapital(tmp_path):
    cfg = Config(Decimal("10000"), 10, 2, 50, tmp_path, "t" * 32, narrow_trading_window=False)
    rows = create_app(cfg).test_client().get("/api/events?limit=200").get_json()
    treffer = [r for r in rows if r["event"] == "NARROW_TRADING_WINDOW"]
    assert treffer == [], f"Pruefflaeche: {len(treffer)} Ereignisse statt 0"


def test_b2_expected_fill_after_ms_ist_genau_die_grenze_die_der_server_anwendet(client, app):
    """B-2, Nebenbefund aus dem Gesamtreview A2: web.py rechnet
    expected_fill_after_ms aus und verspricht es dem Client - der Server
    benutzte den Wert nie. Seit der B-2-Behebung gilt in resolve_pending()
    `candle.open_time > pending_since_ms`, und pending_since_ms ist genau die
    close_time, aus der web.py den Wert bildet. Dieser Test koppelt beide
    Seiten auf die Millisekunde: eine Kerze eine ms VOR der zugesagten Grenze
    darf nicht fuellen, eine Kerze GENAU an der Grenze muss."""
    from aitra import dashboard
    from aitra.execute import ExecutionContext, resolve_pending
    from aitra.marketdata import Candle, SimClock
    from aitra.risk import RiskEngine

    _mit_marktdaten(app)
    cfg = app.config["AITRA"]
    body = client.post("/api/risk/check", headers={"X-Admin-Token": TOKEN},
                        json={"symbol": "BTCUSDC", "action": "BUY", "position_pct": 8}).get_json()
    assert body["status"] == "pending_fill", f"Pruefflaeche: {body}"
    grenze = body["expected_fill_after_ms"]

    def kerze(open_time: int) -> Candle:
        return Candle(symbol="BTCUSDC", interval="15m", open_time=open_time,
                       close_time=open_time + 899_999, open=Decimal("81287.03"),
                       high=Decimal("81287.03"), low=Decimal("81287.03"),
                       close=Decimal("81287.03"), volume=Decimal("1"), closed=True)

    conn = db.connect(cfg.data_dir / "aitra.db")
    # Im Lieferzustand legt poller.build_context() den Lauf "live" an; hier
    # laeuft der Poller nicht (MARKET_DATA_ENABLED ist in der Fixture aus),
    # und fills.run_id hat einen Fremdschluessel auf runs.
    store_run.ensure_run(conn, "live", "live", db.now(), "0.3.0")
    ctx = ExecutionContext(conn=conn, run_id="live", ledger=dashboard.build_live_ledger(conn, cfg),
                            engine=RiskEngine(cfg), specs=app.config["AITRA_SPECS"],
                            fee_bps=cfg.fee_bps, slippage_bps=cfg.slippage_bps,
                            clock=SimClock(grenze + 60_000), kill_switch=False)
    # SimClock statt WallClock: mit der echten Wanduhr laege
    # now - pending_since_ms weit ueber pending_expiry_ms, expire_stale_pending()
    # raeumte die Zeile schon im ERSTEN Aufruf ab - die erste Zusicherung waere
    # dann aus dem falschen Grund gruen (gemessen: der zweite Aufruf fand 0
    # Fills, weil nichts mehr schwebte).
    zu_frueh = resolve_pending(ctx, kerze(grenze - 1))
    assert zu_frueh == [], (
        f"Kerze bei {grenze - 1} (eine ms vor der zugesagten Grenze {grenze}) hat gefuellt: "
        f"{[str(f.price) for f in zu_frueh]}"
    )
    ctx.ledger = dashboard.build_live_ledger(conn, cfg)
    genau = resolve_pending(ctx, kerze(grenze))
    assert len(genau) == 1, (
        f"Kerze genau an der zugesagten Grenze {grenze} hat nicht gefuellt (gemessen {len(genau)} Fills)"
    )
    conn.close()
