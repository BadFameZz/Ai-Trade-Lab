from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest

from aitra import db, store, store_run
from aitra.config import Config
from aitra.web import create_app

HTML = Path(__file__).resolve().parent.parent / "static" / "index.html"
TOKEN = "t" * 32


def _text() -> str:
    return HTML.read_text()


def _skript(t: str) -> str:
    """Nur der Inhalt von <script>...</script> - Grundlage fuer die
    funktionsgenaue Feldextraktion in test_html_felder_sind_teilmenge_der_echten_antworten."""
    return t[t.index("<script>"):t.index("</script>")]


def _funktionskoerper(skript: str, name: str) -> str:
    """Schneidet den Quelltext einer JS-Funktion `name` heraus (von der ersten
    oeffnenden bis zur dazu passenden schliessenden geschweiften Klammer,
    Klammertiefe gezaehlt statt Regex-Rateri, weil Template-Literale eigene
    '{'/'}'-Paare enthalten).

    Wozu ueberhaupt scopen, statt im gesamten Skript nach 's.', 'p.' oder 'r.'
    zu suchen: dieselben Einzelbuchstaben-Variablennamen tauchen mehrfach mit
    VOELLIG anderer Bedeutung wieder auf - 'p' ist in pill() ein DOM-Element
    (p.append, p.replaceChildren), 'r' ist in j() und den Kill-Switch-Handlern
    die rohe fetch-Response (r.ok, r.status, r.json). Eine ungezielte Suche
    haette diese als vermeintliche API-Felder eingesammelt; eine Ausnahmeliste
    dafuer waere genau die Art Momentaufnahme, an der in Aufgabe 6 zwei
    Waechter gescheitert sind. Stattdessen wird die Suche auf die Funktion
    beschraenkt, in der der Buchstabe tatsaechlich das gemeinte Objekt ist."""
    start = skript.index(f"function {name}(")
    klammer_auf = skript.index("{", start)
    tiefe = 0
    i = klammer_auf
    while True:
        if skript[i] == "{":
            tiefe += 1
        elif skript[i] == "}":
            tiefe -= 1
            if tiefe == 0:
                return skript[klammer_auf:i + 1]
        i += 1


def _felder(praefix: str, quelltext: str) -> set[str]:
    """Alle `praefix.name`-Zugriffe im gegebenen Quelltextausschnitt."""
    return set(re.findall(rf"\b{praefix}\.([a-zA-Z_][a-zA-Z0-9_]*)\b", quelltext))


def _alle_schluessel(wert) -> set[str]:
    """Rekursiver Schluessel-Scan (Gegenstueck zu
    _finde_nicht_kanonische_zahlenstrings in test_api.py, dort fuer Werte,
    hier fuer Schluessel): sammelt jeden dict-Schluessel, egal wie tief
    verschachtelt oder in welchem Listenelement - keine gepflegte Liste, die
    bei einem neuen/umbenannten Feld unbemerkt veraltet."""
    schluessel: set[str] = set()
    if isinstance(wert, dict):
        for k, v in wert.items():
            schluessel.add(k)
            schluessel |= _alle_schluessel(v)
    elif isinstance(wert, list):
        for v in wert:
            schluessel |= _alle_schluessel(v)
    return schluessel


def test_chart_ruft_die_equity_kurve_ab():
    """Grundlage von A-19a/A-19b: grep -c 'api/equity-curve' >= 1."""
    t = _text()
    assert t.count("api/equity-curve") >= 1, "Pruefflaeche leer: kein Aufruf gefunden"


def test_chart_legt_genau_zwei_polylinien_an():
    """toHaveCount(2), nicht nur 'vorhanden': eine fehlende zweite Linie waere
    sonst unbemerkt gruen, solange irgendeine Linie existiert."""
    t = _text()
    treffer = re.findall(r"createElementNS\([^)]*['\"]polyline['\"]\)", t)
    assert len(treffer) == 2, f"erwartet genau 2 Polylinien-Erzeugungen, gefunden {len(treffer)}"
    assert "poly-portfolio" in t and "poly-benchmark" in t
    assert t.count("'poly-portfolio'") + t.count('"poly-portfolio"') >= 1
    assert t.count("'poly-benchmark'") + t.count('"poly-benchmark"') >= 1


def test_alter_platzhalter_chart_ist_wirklich_ersetzt():
    """Nicht nur 'neuer Code da', sondern 'alter Code weg' - sonst waere ein
    Chart, der NEBEN dem Platzhalter existiert, ebenfalls gruen."""
    t = _text()
    assert "Noch keine Daten." not in t


def test_offene_positionen_werden_aus_der_api_befuellt():
    t = _text()
    assert 'id="positions"' in t
    assert '<div class="muted" style="font-size:14px">Keine offenen Positionen.</div>' not in t, (
        "die fest verdrahtete Zeile muss durch JS-Befuellung ersetzt sein"
    )
    # Fixrunde 1, Punkt 2 (Koordinator/Reviewer): die dritte Alternative
    # ".positions" ist eine Teilzeichenkette der ersten beiden - der ganze
    # Ausdruck war damit logisch gleichbedeutend mit ".positions" in t und
    # haette schon einem CSS-Selektor ".positions{...}" genuegt. Verschaerft
    # auf die tatsaechliche Bedingung, ohne Rueckfalloption.
    assert "s.positions" in t


def test_kpi_kacheln_trefferquote_und_max_drawdown_sind_verdrahtet():
    t = _text()
    assert 'id="k-hitrate"' in t
    assert 'id="k-drawdown"' in t
    assert t.count('<div class="v muted">—</div>') == 0, (
        "die beiden festen Platzhalterkacheln muessen durch echte IDs ersetzt sein"
    )
    # Fixrunde 1, Punkt 3 (Koordinator/Reviewer): 'id="k-hitrate"' und
    # 'hit_rate_pct' wurden bisher UNABHAENGIG voneinander geprueft - eine
    # Vertauschung der beiden Kacheln (Trefferquote zeigt Drawdown und
    # umgekehrt) haette beide Zeichenketten weiter im Dokument gelassen und
    # waere gruen geblieben. Jetzt ID und Feldname im selben Ausdruck
    # nachgewiesen (derselbe text()-Aufruf).
    assert re.search(r"text\(\$\('k-hitrate'\),\s*s\.hit_rate_pct\b", t), (
        "k-hitrate haengt nicht (mehr) an s.hit_rate_pct - Vertauschung mit k-drawdown?"
    )
    assert re.search(r"text\(\$\('k-drawdown'\),\s*s\.max_drawdown_pct\b", t), (
        "k-drawdown haengt nicht (mehr) an s.max_drawdown_pct - Vertauschung mit k-hitrate?"
    )


def test_schwebende_vorschlaege_werden_angezeigt():
    t = _text()
    assert 'id="pending"' in t
    assert "pending_since_ms" in t
    # Fixrunde 1, Punkt 3 (Koordinator/Reviewer): dieselbe Schwaeche wie bei
    # den KPI-Kacheln - 'id="pending"' und 'pending_since_ms' standen
    # unabhaengig voneinander im Dokument. Verschaerft auf denselben
    # Funktionskoerper (loadPending() ist die einzige Funktion, die sowohl den
    # #pending-Container anspricht als auch nach pending_since_ms filtert) -
    # eine Verkabelung an eine andere Karte wuerde diese Kopplung aufbrechen.
    skript = _skript(t)
    pending_koerper = _funktionskoerper(skript, "loadPending")
    assert "$('pending')" in pending_koerper or '$("pending")' in pending_koerper, (
        "loadPending() spricht #pending nicht (mehr) an"
    )
    assert "pending_since_ms" in pending_koerper, (
        "loadPending() filtert nicht (mehr) auf pending_since_ms"
    )


def test_luecke_l1_ist_im_quelltext_dokumentiert():
    """Kein funktionaler Test - eine Erinnerung, die verhindert, dass die
    Lücke beim naechsten Redesign stillschweigend verschwindet."""
    t = _text()
    assert "L-1" in t or "Kein Browsertest" in t or "kein automatisierter Test" in t


# --------------------------------------------------------------------------
# Fixrunde 1, Punkt 1 (Koordinator/Reviewer): der Brief behauptete, die
# Endpunktseite sei "durch Aufgabe 6 bereits gruen" - das stimmt fuer
# positions/benchmark (test_api.py prueft sie ueber den echten Testclient),
# aber NICHT fuer hit_rate_pct, max_drawdown_pct (kein Treffer ausserhalb
# dieser HTML-Textpruefungen) und pending_since_ms (nur auf DB-Ebene geprueft,
# nie ueber GET /api/decisions). Wuerde /api/decisions morgen von SELECT * auf
# eine kuratierte Spaltenliste umgestellt und pending_since_ms vergessen,
# bliebe die gesamte Suite gruen - die Karte "Schwebende Vorschlaege" waere
# dauerhaft leer, ohne dass ein Test das meldet.
#
# Statt einer handgepflegten Feldliste (siehe Fixrunde 2 zu Aufgabe 6: eine
# gepflegte Liste blieb bei einem neuen rohen Feld unbemerkt gruen) wird hier
# GEMESSEN: die im HTML tatsaechlich gelesenen Feldnamen werden aus dem
# Quelltext extrahiert und als Teilmenge gegen die echten Antworten der drei
# Endpunkte (echter Flask-Testclient, gefuellte Datenbank) geprueft.
# --------------------------------------------------------------------------

@pytest.fixture
def app(tmp_path):
    return create_app(Config(Decimal(10_000), 10, 2, 50, tmp_path, TOKEN))


@pytest.fixture
def client(app):
    return app.test_client()


def _gefuellte_db(app) -> None:
    """Baut eine Datenbank auf, in der alle drei Endpunkte etwas Substanzielles
    liefern: eine Kerze (Voraussetzung fuer Positionen/Kasse), eine offene
    Position, einen Kurvenpunkt mit Benchmark, und eine schwebende
    Entscheidung (E-006) - sonst wuerden leere Listen/None-Werte die
    betroffenen Schluessel gar nicht erst ins JSON bringen und der
    Teilmengen-Test wuerde falsch-gruen laufen."""
    conn = db.connect(app.config["AITRA"].data_dir / "aitra.db")
    store.upsert_candles(conn, [
        store.CandleRow(symbol="BTCUSDC", interval="15m", open_time=0, close_time=899_999,
                         open=Decimal("81287.03"), high=Decimal("81287.03"), low=Decimal("81287.03"),
                         close=Decimal("81287.03"), volume=Decimal("1"), source="fixture",
                         fetched_at="2026-01-01T00:00:00Z"),
    ])
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
    decision_id = db.add_decision(conn, run_id="live", symbol="BTCUSDC", action="BUY",
                                   confidence=71.0, reason="Testvorschlag", requested_position_pct=8.0,
                                   approved=True, risk_code=None, risk_reason=None)
    store_run.mark_decision_pending(conn, decision_id, pending_since_ms=900_000,
                                     ref_price=Decimal("81287.03"), base_qty=Decimal("0.01"))
    conn.close()


def test_html_felder_sind_teilmenge_der_echten_endpunkt_antworten(client, app):
    """Kopplungstest: keine Feldliste, sondern ein Regel-Scan. Extrahiert alle
    Feldnamen, die das HTML/JS tatsaechlich anspricht (funktionsgenau, siehe
    _funktionskoerper), und prueft sie als Teilmenge der Schluesselmengen der
    drei echten Endpunkt-Antworten (rekursiv, siehe _alle_schluessel)."""
    _gefuellte_db(app)

    skript = _skript(_text())
    status_felder = (
        _felder("s", _funktionskoerper(skript, "loadStatus"))
        | _felder("s", _funktionskoerper(skript, "loadPositionsAndKpis"))
        | _felder("p", _funktionskoerper(skript, "loadPositionsAndKpis"))
    )
    kurven_felder = _felder("p", _funktionskoerper(skript, "loadChart"))
    entscheidungs_felder = (
        _felder("r", _funktionskoerper(skript, "loadDecisions"))
        | _felder("r", _funktionskoerper(skript, "loadPending"))
    )

    status = client.get("/api/status").get_json()
    kurve = client.get("/api/equity-curve?run_id=live&limit=500").get_json()
    entscheidungen = client.get("/api/decisions?limit=100").get_json()

    # Die eigentliche Pruefung zuerst: eine fehlende/umbenannte Spalte soll mit
    # ihrem Namen genannt werden, statt hinter der Gegenprobe (unten) versteckt
    # zu bleiben - sonst meldet ein und derselbe Fehler zwei unterschiedliche,
    # verwirrende Ursachen je nachdem, welche Assertion zuerst greift.
    fehlend_status = status_felder - _alle_schluessel(status)
    fehlend_kurve = kurven_felder - _alle_schluessel(kurve)
    fehlend_entscheidung = entscheidungs_felder - _alle_schluessel(entscheidungen)

    assert fehlend_status == set(), (
        f"HTML erwartet Status-Felder, die /api/status nicht liefert: {sorted(fehlend_status)}"
    )
    assert fehlend_kurve == set(), (
        f"HTML erwartet Kurvenpunkt-Felder, die /api/equity-curve nicht liefert: {sorted(fehlend_kurve)}"
    )
    assert fehlend_entscheidung == set(), (
        f"HTML erwartet Entscheidungs-Felder, die /api/decisions nicht liefert: {sorted(fehlend_entscheidung)}"
    )

    # Gegenprobe der Fixture selbst, NACH den eigentlichen Pruefungen: ohne
    # diese drei waere der Teilmengen-Test oben unscharf (leere Container
    # liefern keine Unterschluessel, jede Teilmengenpruefung waere trivial
    # gruen). Bewusst zuletzt, damit eine echte Endpunkt-Regression (siehe
    # oben) nie hinter dieser Meldung versteckt bleibt.
    assert status["positions"], "Fixture liefert keine offene Position - Test waere unscharf"
    assert kurve["points"], "Fixture liefert keinen Kurvenpunkt - Test waere unscharf"
    assert any(r.get("pending_since_ms") is not None for r in entscheidungen), (
        "Fixture liefert keine schwebende Entscheidung - Test waere unscharf"
    )
