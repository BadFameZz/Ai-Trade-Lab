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
