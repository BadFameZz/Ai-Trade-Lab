# app/tests/test_kerzenreihenfolge_b_d1.py
"""Waechter fuer B-D1: "die letzten N" muessen die JUENGSTEN N sein.

store.get_candles() sortiert ORDER BY open_time ASC LIMIT ? und liefert damit
die AELTESTEN N Zeilen. Vier Aufrufer behandeln das Ergebnis als "die
neuesten", indem sie rows[-1] nehmen (dashboard.last_prices, web.risk_check,
GET /api/market/candles, GET /api/equity-curve ueber
store_run.get_equity_curve). Auf CT 107 gemessen (2026-09-23, 38.399 Kerzen je
Symbol): /api/market/candles lieferte 2025-08-19 13:00 UTC / close 115560.01
statt der tatsaechlich juengsten Kerze 2026-09-23 12:30 UTC / close 85454.11 --
rund 35 % Abweichung auf dem Preis, der die gesamte Live-Equity bewertet.

Warum es niemandem auffiel: test_api.py legte GENAU EINE Kerze an. Bei einer
Zeile sind aelteste und neueste dieselbe Zeile, die Pruefflaeche war leer.
JEDER Test hier legt deshalb mehrere Kerzen mit deutlich unterschiedlichen
Preisen an.

Der Vertrag, den diese Datei festnagelt, hat ZWEI Haelften -- ein Fix, der
einfach auf ORDER BY ... DESC umstellt, erfuellt nur die erste und zerstoert
die zweite:

  (1) Ein bindendes LIMIT schneidet die AELTESTEN Zeilen weg, nicht die
      juengsten. rows[-1] ist die juengste Kerze.
  (2) Das Ergebnis ist IMMER chronologisch aufsteigend -- auch wenn das LIMIT
      gebissen hat. replay._load_candles_from_db() (Zeitraffer/Backfill/
      Benchmark) iteriert candles[0..n-1] vorwaerts und nimmt candles[0] als
      Anfang und candles[-1] als Ende des Laufs; dashboard.build_status()
      rechnet den Max Drawdown ueber die Kurve in Zeitrichtung.

Kanonische Form: SELECT * FROM (<...> ORDER BY open_time DESC LIMIT ?)
ORDER BY open_time ASC -- die juengsten N, aufsteigend ausgeliefert.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from aitra import dashboard, db, store, store_run
from aitra.config import Config
from aitra.web import create_app

TOKEN = "t" * 32
INTERVALL_MS = 900_000

# Die drei Preise stammen aus der Messung auf CT 107: der aelteste Wert, den
# der Fehler heute ausliefert, ein Zwischenwert, und der tatsaechlich juengste.
ALT = Decimal("115560.01")
MITTE = Decimal("100000.00")
NEU = Decimal("85454.11")


def _kerze(i: int, preis: Decimal, symbol: str = "BTCUSDC",
           interval: str = "15m") -> store.CandleRow:
    """Kerze Nummer i (open_time = i * 15 min) mit preis als OHLC."""
    ot = i * INTERVALL_MS
    return store.CandleRow(
        symbol=symbol, interval=interval, open_time=ot, close_time=ot + INTERVALL_MS - 1,
        open=preis, high=preis, low=preis, close=preis, volume=Decimal("1"),
        source="fixture", fetched_at="2026-01-01T00:00:00Z",
    )


def _conn(tmp_path: Path, name: str = "a.db"):
    conn = db.connect(tmp_path / name)
    db.migrate(conn)
    return conn


def _punkt(i: int, equity: Decimal, bench: Decimal | None = None) -> store_run.EquityPoint:
    return store_run.EquityPoint(
        run_id="live", ts_ms=i * INTERVALL_MS + INTERVALL_MS - 1, equity=equity,
        cash=Decimal("1000"), benchmark_equity=bench, exposure_pct=float(i),
    )


def _app_mit_kerzen(tmp_path: Path, name: str, preise: list[Decimal],
                    startkapital: Decimal = Decimal("200000")) -> tuple:
    """Eine frische App mit eigener Datenbank und den gegebenen Kerzenpreisen.

    preise[i] gehoert zu open_time i * 15 min -- preise[-1] ist damit IMMER die
    juengste Kerze.
    """
    d = tmp_path / name
    d.mkdir()
    cfg = Config(startkapital, 10, 2, 50, d, TOKEN)
    app = create_app(cfg)
    conn = db.connect(d / "aitra.db")
    store.upsert_candles(conn, [_kerze(i, p) for i, p in enumerate(preise)])
    conn.close()
    return app, cfg


# --------------------------------------------------------------------------
# Schicht 1: der Vertrag von store.get_candles() selbst
# --------------------------------------------------------------------------

def test_get_candles_mit_bindendem_limit_liefert_die_juengsten_kerzen(tmp_path):
    """Haelfte (1): das LIMIT schneidet vorne ab, nicht hinten."""
    conn = _conn(tmp_path)
    preise = [Decimal(str(100 + i)) for i in range(5)]  # 100 .. 104, streng steigend
    store.upsert_candles(conn, [_kerze(i, p) for i, p in enumerate(preise)])

    alle = store.get_candles(conn, "BTCUSDC", "15m", limit=1000)
    assert len(alle) == 5, f"Pruefflaeche leer/unvollstaendig: {len(alle)} Kerzen statt 5"

    rows = store.get_candles(conn, "BTCUSDC", "15m", limit=2)
    assert len(rows) == 2, f"Pruefflaeche: {len(rows)} Kerzen statt 2"
    assert [r.open_time for r in rows] == [3 * INTERVALL_MS, 4 * INTERVALL_MS], (
        "get_candles(limit=2) muss die JUENGSTEN zwei Kerzen liefern; gemessen: "
        f"{[r.open_time for r in rows]}, Schluesse {[str(r.close) for r in rows]}"
    )
    assert [r.close for r in rows] == [Decimal("103"), Decimal("104")]


def test_get_candles_letzte_zeile_ist_die_juengste_kerze(tmp_path):
    """Der Vertrag, auf den sich JEDER der vier Aufrufer mit rows[-1] verlaesst.

    Auch fuer limit=1 -- genau so ruft dashboard.last_prices() auf.
    """
    conn = _conn(tmp_path)
    store.upsert_candles(conn, [_kerze(0, ALT), _kerze(1, MITTE), _kerze(2, NEU)])

    for limit in (1, 2, 3, 500):
        rows = store.get_candles(conn, "BTCUSDC", "15m", limit=limit)
        assert rows, f"Pruefflaeche leer bei limit={limit}"
        assert rows[-1].close == NEU, (
            f"limit={limit}: rows[-1].close ist {rows[-1].close} "
            f"(open_time {rows[-1].open_time}), erwartet {NEU} (open_time {2 * INTERVALL_MS})"
        )
        assert rows[-1].open_time == 2 * INTERVALL_MS


def test_get_candles_bleibt_chronologisch_aufsteigend_auch_mit_limit(tmp_path):
    """Haelfte (2): ein blosses ORDER BY ... DESC ist KEIN Fix.

    replay._load_candles_from_db() und dashboard.build_status() laufen die
    Reihenfolge in Zeitrichtung ab. Kippt sie, laeuft der Zeitraffer rueckwaerts
    durch den Markt.
    """
    conn = _conn(tmp_path)
    preise = [Decimal(str(100 + i)) for i in range(10)]
    store.upsert_candles(conn, [_kerze(i, p) for i, p in enumerate(preise)])

    rows = store.get_candles(conn, "BTCUSDC", "15m", limit=4)
    assert len(rows) == 4, f"Pruefflaeche: {len(rows)} Kerzen statt 4"
    zeiten = [r.open_time for r in rows]
    assert zeiten == sorted(zeiten), f"nicht aufsteigend: {zeiten}"
    assert zeiten == [6 * INTERVALL_MS, 7 * INTERVALL_MS, 8 * INTERVALL_MS, 9 * INTERVALL_MS], (
        f"erwartet die juengsten vier, aufsteigend; gemessen: {zeiten}"
    )


def test_get_candles_zeitraum_liefert_das_ganze_fenster_aufsteigend(tmp_path):
    """Die zweite Nutzung: start_ms/end_ms mit grosszuegigem limit.

    So laedt replay._load_candles_from_db() (limit=1_000_000) den Zeitraffer.
    Hier darf sich durch die Behebung NICHTS aendern: vollstaendiges Fenster,
    chronologisch, Anfang zuerst.
    """
    conn = _conn(tmp_path)
    preise = [Decimal(str(100 + i)) for i in range(10)]
    store.upsert_candles(conn, [_kerze(i, p) for i, p in enumerate(preise)])

    rows = store.get_candles(conn, "BTCUSDC", "15m",
                             start_ms=2 * INTERVALL_MS, end_ms=6 * INTERVALL_MS,
                             limit=1_000_000)
    zeiten = [r.open_time for r in rows]
    assert len(rows) == 5, f"Pruefflaeche: {len(rows)} Kerzen statt 5 im Fenster [2..6]"
    assert zeiten == [i * INTERVALL_MS for i in range(2, 7)], (
        f"Zeitfenster muss vollstaendig und aufsteigend kommen; gemessen: {zeiten}"
    )
    assert rows[0].close == Decimal("102"), "candles[0] ist der Anfang des Laufs"
    assert rows[-1].close == Decimal("106"), "candles[-1] ist das Ende des Laufs"


def test_get_equity_curve_mit_bindendem_limit_liefert_die_juengsten_punkte(tmp_path):
    """Dasselbe Muster in store_run.get_equity_curve() (ORDER BY ts_ms ASC LIMIT ?)."""
    conn = _conn(tmp_path)
    store_run.create_run(conn, "live", "live", "2026-01-01T00:00:00Z", "0.3.0")
    store_run.append_equity_points(
        conn, [_punkt(i, Decimal(str(10_000 + i)), Decimal(str(20_000 + i))) for i in range(5)]
    )

    alle = store_run.get_equity_curve(conn, "live", limit=1000)
    assert len(alle) == 5, f"Pruefflaeche: {len(alle)} Punkte statt 5"

    punkte = store_run.get_equity_curve(conn, "live", limit=2)
    assert len(punkte) == 2, f"Pruefflaeche: {len(punkte)} Punkte statt 2"
    assert [p["equity"] for p in punkte] == [Decimal("10003"), Decimal("10004")], (
        "get_equity_curve(limit=2) muss die JUENGSTEN zwei Punkte liefern, aufsteigend; "
        f"gemessen: {[str(p['equity']) for p in punkte]} bei ts_ms {[p['ts_ms'] for p in punkte]}"
    )
    assert punkte[-1]["equity"] == Decimal("10004"), "curve[-1] ist der juengste Punkt"


def test_get_equity_curve_bleibt_chronologisch_aufsteigend_auch_mit_limit(tmp_path):
    """Guard gegen einen blossen DESC-Fix: build_status() rechnet den Max
    Drawdown ueber die Kurve in Zeitrichtung und liest curve[-1] als Benchmark."""
    conn = _conn(tmp_path)
    store_run.create_run(conn, "live", "live", "2026-01-01T00:00:00Z", "0.3.0")
    store_run.append_equity_points(
        conn, [_punkt(i, Decimal(str(10_000 + i))) for i in range(10)]
    )

    punkte = store_run.get_equity_curve(conn, "live", limit=4)
    assert len(punkte) == 4, f"Pruefflaeche: {len(punkte)} Punkte statt 4"
    zeiten = [p["ts_ms"] for p in punkte]
    assert zeiten == sorted(zeiten), f"nicht aufsteigend: {zeiten}"
    assert zeiten == [i * INTERVALL_MS + INTERVALL_MS - 1 for i in range(6, 10)], (
        f"erwartet die juengsten vier, aufsteigend; gemessen: {zeiten}"
    )


# --------------------------------------------------------------------------
# Aufrufer 1: dashboard.last_prices() -- der Preis, der die Live-Equity bewertet
# --------------------------------------------------------------------------

def test_last_prices_nimmt_den_juengsten_schlusskurs(tmp_path):
    """dashboard.py:121-123, Docstring "Letzter bekannter Schlusskurs"."""
    conn = _conn(tmp_path)
    store.upsert_candles(conn, [_kerze(0, ALT), _kerze(1, MITTE), _kerze(2, NEU)])
    cfg = Config(Decimal("200000"), 10, 2, 50, tmp_path, TOKEN)

    preise = dashboard.last_prices(conn, cfg)
    assert "BTCUSDC" in preise, f"Pruefflaeche leer: {preise}"
    assert preise["BTCUSDC"] == NEU, (
        f"last_prices() bewertet mit {preise['BTCUSDC']} statt mit der juengsten "
        f"Kerze {NEU} -- Abweichung "
        f"{round((preise['BTCUSDC'] - NEU) / NEU * 100, 2)} %"
    )


def test_status_equity_bewertet_die_position_mit_dem_juengsten_preis(tmp_path):
    """Der Weg bis zum Nutzer: GET /api/status.

    Startkapital 200.000, eine gekaufte Position von 1 BTC zu 115.560,01
    (Kasse danach 84.439,99). Bewertet mit der JUENGSTEN Kerze (85.454,11) ist
    die Equity 169.894,10 -- ein Buchverlust von 30.105,90. Bewertet mit der
    AELTESTEN Kerze, die get_candles() heute liefert, kommt exakt das
    Startkapital heraus: der Verlust ist unsichtbar.
    """
    app, cfg = _app_mit_kerzen(tmp_path, "equity", [ALT, MITTE, NEU])
    conn = db.connect(cfg.data_dir / "aitra.db")
    store_run.ensure_run(conn, "live", "live", db.now(), "0.3.0")
    store_run.insert_fill(conn, run_id="live", decision_id=None, symbol="BTCUSDC", side="BUY",
                          candle_open_time=0, price=ALT, qty=Decimal("1"),
                          gross_quote=ALT, fee=Decimal("0"), net_quote=ALT,
                          cash_after=Decimal("84439.99"), fee_bps=10.0, slippage_bps=5.0,
                          ts=db.now())
    store_run.upsert_position(conn, run_id="live", symbol="BTCUSDC", qty=Decimal("1"),
                              avg_price=ALT, realized_pnl=Decimal("0"), updated_at=db.now())
    conn.close()

    s = app.test_client().get("/api/status").get_json()
    assert s["cash"] == "84439.99000000", f"Pruefflaeche: Kasse {s['cash']}"
    assert len(s["positions"]) == 1, f"Pruefflaeche: {s['positions']}"
    assert s["equity"] == "169894.10000000", (
        f"Equity {s['equity']} statt 169894.10000000 -- die Position wird mit einer "
        f"alten Kerze bewertet (pnl gemeldet: {s['pnl']})"
    )
    assert s["pnl"] == "-30105.90000000", f"pnl gemeldet: {s['pnl']}"


# --------------------------------------------------------------------------
# Aufrufer 2: POST /api/risk/check -- ref_price, ts_ms und die ORDERMENGE
# --------------------------------------------------------------------------

def test_risk_check_referenzpreis_und_fuellzeit_stammen_aus_der_juengsten_kerze(tmp_path):
    """web.py:194,197-198 und 220."""
    app, cfg = _app_mit_kerzen(tmp_path, "ref", [ALT, MITTE, NEU])
    r = app.test_client().post(
        "/api/risk/check", headers={"X-Admin-Token": TOKEN},
        json={"symbol": "BTCUSDC", "action": "BUY", "position_pct": 8},
    )
    body = r.get_json()
    assert body["status"] == "pending_fill", f"Pruefflaeche: {body}"
    # close_time der juengsten Kerze (open_time 1.800.000) ist 2.699.999.
    assert body["expected_fill_after_ms"] == 2_700_000, (
        f"expected_fill_after_ms = {body['expected_fill_after_ms']}, erwartet 2700000 "
        "(close_time der juengsten Kerze + 1)"
    )

    zeilen = app.test_client().get("/api/decisions").get_json()
    offen = [z for z in zeilen if z["id"] == body["decision_id"]]
    assert len(offen) == 1, f"Pruefflaeche: decision_id {body['decision_id']} nicht im Journal"
    assert offen[0]["pending_ref_price"] == "85454.11000000", (
        f"pending_ref_price = {offen[0]['pending_ref_price']}, erwartet 85454.11000000 "
        "(Schlusskurs der juengsten Kerze)"
    )
    assert offen[0]["pending_since_ms"] == 2_699_999, (
        f"pending_since_ms = {offen[0]['pending_since_ms']}, erwartet 2699999"
    )


def test_risk_check_ordermenge_haengt_nur_am_juengsten_preis_nicht_an_der_historie(tmp_path):
    """Die Ordermenge selbst (sizing.size_order ueber ref_price).

    Drei Anlagen, dieselbe Anfrage, kein Fill und kein Startkapitalunterschied
    -- die einzige Variable ist der Kerzenbestand:

      nur_neu  : eine Kerze, 85.454,11  (der juengste Preis)
      historie : drei Kerzen, 115.560,01 / 100.000 / 85.454,11
      nur_alt  : eine Kerze, 115.560,01 (der aelteste Preis)

    Die Menge aus `historie` muss der aus `nur_neu` gleichen. Der Vergleich mit
    `nur_alt` ist der Riegel gegen eine leere Pruefflaeche: waeren alle drei
    gleich, wuerde der Preis die Menge gar nicht bestimmen und der Test nichts
    pruefen. Kein Nachbau der Bemessungsformel im Test.
    """
    def menge(name: str, preise: list[Decimal]) -> str:
        app, _ = _app_mit_kerzen(tmp_path, name, preise)
        c = app.test_client()
        body = c.post("/api/risk/check", headers={"X-Admin-Token": TOKEN},
                      json={"symbol": "BTCUSDC", "action": "BUY", "position_pct": 8}).get_json()
        assert body["status"] == "pending_fill", f"{name}: Pruefflaeche {body}"
        zeile = [z for z in c.get("/api/decisions").get_json()
                 if z["id"] == body["decision_id"]]
        assert len(zeile) == 1, f"{name}: Entscheidung nicht im Journal"
        return zeile[0]["pending_base_qty"]

    nur_neu = menge("nur_neu", [NEU])
    historie = menge("historie", [ALT, MITTE, NEU])
    nur_alt = menge("nur_alt", [ALT])

    assert nur_neu != nur_alt, (
        "Pruefflaeche leer: der Referenzpreis beeinflusst die Ordermenge nicht "
        f"({nur_neu} == {nur_alt}) -- dieser Test koennte den Fehler nicht sehen"
    )
    assert historie == nur_neu, (
        f"Ordermenge mit Historie {historie}, ohne Historie {nur_neu} "
        f"(Menge zum aeltesten Preis: {nur_alt}) -- bemessen wird mit einer alten Kerze"
    )


# --------------------------------------------------------------------------
# Aufrufer 3: GET /api/market/candles
# --------------------------------------------------------------------------

def test_api_market_candles_liefert_die_juengsten_kerzen_aufsteigend(tmp_path):
    """web.py:230 -- genau der Aufruf, der auf CT 107 den 2025er-Stand zeigte."""
    preise = [Decimal(str(100 + i)) for i in range(5)]
    app, _ = _app_mit_kerzen(tmp_path, "candles", preise)

    body = app.test_client().get(
        "/api/market/candles?symbol=BTCUSDC&interval=15m&limit=3").get_json()
    assert len(body) == 3, f"Pruefflaeche: {len(body)} Kerzen statt 3"
    zeiten = [k["open_time"] for k in body]
    assert zeiten == [2 * INTERVALL_MS, 3 * INTERVALL_MS, 4 * INTERVALL_MS], (
        f"erwartet die juengsten drei Kerzen, aufsteigend; gemessen: {zeiten} "
        f"mit Schluessen {[k['close'] for k in body]}"
    )
    assert [k["close"] for k in body] == ["102.00000000", "103.00000000", "104.00000000"]


# --------------------------------------------------------------------------
# Aufrufer 4: GET /api/equity-curve
# --------------------------------------------------------------------------

def test_api_equity_curve_liefert_die_juengsten_punkte_aufsteigend(tmp_path):
    """web.py:242 ueber store_run.get_equity_curve()."""
    app, cfg = _app_mit_kerzen(tmp_path, "kurve", [NEU])
    conn = db.connect(cfg.data_dir / "aitra.db")
    store_run.ensure_run(conn, "live", "live", db.now(), "0.3.0")
    store_run.append_equity_points(
        conn, [_punkt(i, Decimal(str(10_000 + i)), Decimal(str(20_000 + i))) for i in range(5)]
    )
    conn.close()

    body = app.test_client().get("/api/equity-curve?run_id=live&limit=3").get_json()
    punkte = body["points"]
    assert len(punkte) == 3, f"Pruefflaeche: {len(punkte)} Punkte statt 3"
    zeiten = [p["ts_ms"] for p in punkte]
    assert zeiten == sorted(zeiten), f"nicht aufsteigend: {zeiten}"
    assert [p["equity"] for p in punkte] == ["10002.00000000", "10003.00000000", "10004.00000000"], (
        f"erwartet die juengsten drei Punkte, aufsteigend; gemessen: "
        f"{[p['equity'] for p in punkte]} bei ts_ms {zeiten}"
    )


def test_status_benchmark_stammt_vom_juengsten_equity_punkt(tmp_path):
    """Guard: build_status() liest curve[-1]["benchmark_equity"] (dashboard.py:147).

    Heute gruen, weil limit=100_000 nie bindet. Kippt jemand get_equity_curve()
    zur Behebung von B-D1 auf reines DESC, meldet /api/status ploetzlich den
    AELTESTEN Benchmarkstand -- und der Max Drawdown rechnet rueckwaerts.
    """
    app, cfg = _app_mit_kerzen(tmp_path, "bench", [NEU])
    conn = db.connect(cfg.data_dir / "aitra.db")
    store_run.ensure_run(conn, "live", "live", db.now(), "0.3.0")
    store_run.append_equity_points(
        conn, [_punkt(i, Decimal(str(10_000 + i)), Decimal(str(20_000 + i))) for i in range(5)]
    )
    conn.close()

    s = app.test_client().get("/api/status").get_json()
    assert s["benchmark"]["equity"] == "20004.00000000", (
        f"Benchmark {s['benchmark']['equity']} statt 20004.00000000 -- "
        "build_status() liest nicht den juengsten Punkt der Kurve"
    )
