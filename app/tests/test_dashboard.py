from __future__ import annotations

import re
from pathlib import Path

HTML = Path(__file__).resolve().parent.parent / "static" / "index.html"


def _text() -> str:
    return HTML.read_text()


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
    assert "s.positions" in t or "status.positions" in t or ".positions" in t


def test_kpi_kacheln_trefferquote_und_max_drawdown_sind_verdrahtet():
    t = _text()
    assert 'id="k-hitrate"' in t
    assert 'id="k-drawdown"' in t
    assert t.count('<div class="v muted">—</div>') == 0, (
        "die beiden festen Platzhalterkacheln muessen durch echte IDs ersetzt sein"
    )
    assert "hit_rate_pct" in t
    assert "max_drawdown_pct" in t


def test_schwebende_vorschlaege_werden_angezeigt():
    t = _text()
    assert 'id="pending"' in t
    assert "pending_since_ms" in t


def test_luecke_l1_ist_im_quelltext_dokumentiert():
    """Kein funktionaler Test - eine Erinnerung, die verhindert, dass die
    Lücke beim naechsten Redesign stillschweigend verschwindet."""
    t = _text()
    assert "L-1" in t or "Kein Browsertest" in t or "kein automatisierter Test" in t
