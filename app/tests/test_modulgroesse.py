# app/tests/test_modulgroesse.py
from __future__ import annotations

from pathlib import Path

AITRA = Path(__file__).resolve().parent.parent / "aitra"
MARKE = 300  # harte Marke aus Spec 3.1b; 200 Zeilen sind nur der Richtwert


def test_kein_modul_ueber_der_300_zeilen_marke():
    """Spec 3.1b: 200 Zeilen sind Richtwert, ab 300 wird geteilt.

    Prueffläche: Die Schleife laeuft ueber ein Verzeichnis. Waere das Muster
    falsch oder das Verzeichnis leer, liefe sie leer durch und waere immer
    gruen - deshalb wird die Zahl der geprueften Dateien selbst zugesichert.
    """
    module = sorted(p for p in AITRA.glob("*.py") if p.name != "__init__.py")
    assert len(module) >= 11, f"Pruefflaeche zu klein: nur {len(module)} Module gefunden"

    zu_gross = {
        p.name: len(p.read_text().splitlines())
        for p in module
        if len(p.read_text().splitlines()) > MARKE
    }
    assert zu_gross == {}, f"ueber der 300-Zeilen-Marke: {zu_gross}"


SPEC_31B = (
    Path(__file__).resolve().parent.parent.parent
    / "docs/specs/2026-09-20-teilprojekt-a-marktdaten-ledger-benchmark.md"
)


def _spec_31b_abschnitt() -> str:
    """Nur der Text zwischen der 3.1b-Ueberschrift und der naechsten
    Ueberschrift - sonst wuerde ein Modulname, der irgendwo sonst im
    Dokument in Backticks auftaucht, den Test faelschlich gruen halten."""
    text = SPEC_31B.read_text()
    start = text.index("### 3.1b")
    ende = text.index("\n### ", start + 1)
    return text[start:ende]


def test_jedes_modul_ueber_200_zeilen_hat_eine_begruendungszeile_in_spec_3_1b():
    """Fixrunde 1 zu A2/Aufgabe 5 (Reviewer-Befund): die Tabelle in Spec 3.1b
    war flaechendeckend veraltet - zwei Module (config.py, poller.py) fehlten
    ganz, fuenf weitere hatten falsche Zeilenzahlen. Dieser Test sichert nur
    die EXISTENZ einer Begruendungszeile je Modul ueber dem 200er-Richtwert
    zu, nicht die exakte Zeilenzahl - sonst braeche der Test bei jeder
    Kleinigkeit und wuerde irgendwann abgeschaltet (Ruling des Koordinators)."""
    assert SPEC_31B.exists(), f"Spec-Datei nicht gefunden: {SPEC_31B}"
    abschnitt = _spec_31b_abschnitt()

    ueber_200 = sorted(
        p.name for p in AITRA.glob("*.py")
        if p.name != "__init__.py" and len(p.read_text().splitlines()) > 200
    )
    assert len(ueber_200) >= 1, "Pruefflaeche zu klein: kein Modul ueber 200 Zeilen gefunden"

    fehlend = [name for name in ueber_200 if f"`{name}`" not in abschnitt]
    assert fehlend == [], f"Module ueber 200 Zeilen ohne Begruendungszeile in Spec 3.1b: {fehlend}"


def test_store_und_store_run_sind_wirklich_getrennt():
    """Die Naht aus Spec 3.1b: Marktdaten hier, Lauf und Ledger dort.

    Nicht nur 'store_run existiert', sondern auch: die Laufzugriffe sind aus
    store.py *verschwunden*. Ohne die zweite Zusicherung waere ein Copy-Paste,
    das beide Module mit allem fuellt, ebenfalls gruen.
    """
    from aitra import store, store_run

    marktdaten = ["CandleRow", "upsert_candles", "get_candles", "prune_candles",
                  "upsert_symbol_spec", "get_symbol_spec"]
    lauf = ["create_run", "finish_run", "insert_fill", "get_fills", "upsert_position",
            "get_positions", "EquityPoint", "append_equity_points", "get_equity_curve",
            "mark_decision_pending", "resolve_decision", "reject_decision",
            "expire_decision", "get_pending_decisions"]

    fehlend_markt = [n for n in marktdaten if not hasattr(store, n)]
    fehlend_lauf = [n for n in lauf if not hasattr(store_run, n)]
    assert fehlend_markt == [], f"fehlt in store.py: {fehlend_markt}"
    assert fehlend_lauf == [], f"fehlt in store_run.py: {fehlend_lauf}"

    uebriggeblieben = [n for n in lauf if hasattr(store, n)]
    verirrt = [n for n in marktdaten if hasattr(store_run, n)]
    assert uebriggeblieben == [], f"noch in store.py statt store_run.py: {uebriggeblieben}"
    assert verirrt == [], f"in store_run.py statt store.py: {verirrt}"
