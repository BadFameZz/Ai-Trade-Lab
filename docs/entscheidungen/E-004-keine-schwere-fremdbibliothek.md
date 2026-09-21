# E-004 — Keine schwere Fremdbibliothek

- **Datum:** 2026-09-21 (Fassung 2: Testanzahl an Spec-Fassung 3 angeglichen)
- **Status:** entschieden (vom Auftraggeber vorgegeben)
- **Betrifft:** `app/requirements.txt`, alle neuen Module
- **Spec:** Abschnitt 3.3, Kriterium A-18

## Entscheidung

Kein `freqtrade`, kein `backtrader`, kein `pandas`, kein `numpy`, kein `requests`, keine
Chart-Bibliothek, kein Headless-Browser. Teilprojekt A kommt mit der Standardbibliothek aus und
lässt `app/requirements.txt` bei genau zwei Zeilen: `flask==3.1.3`, `gunicorn==26.2.0`.

| Bedarf | Lösung in A |
|---|---|
| HTTP zu Binance | `urllib.request` — dasselbe, was der Healthcheck im Dockerfile (Zeile 21) schon nutzt |
| Exakte Geldarithmetik | `decimal` |
| Kerzen und Zeitreihen | eigene frozen dataclasses, Listen, SQLite |
| Backtest-Schleife | `replay.py`, rund 120 Zeilen |
| Chart im Dashboard | Inline-SVG mit zwei `<polyline>`, kein Build-Schritt, keine CDN-Anfrage |
| Tests | `pytest` bleibt reine Entwicklungsabhängigkeit, nicht im Laufzeit-Image |

Messung: `sha256sum app/requirements.txt` ist nach A identisch zu v0.2.1 (Kriterium A-18).

## Begründung

Drei Gründe, in dieser Reihenfolge:

1. **Angriffsfläche.** Jede Abhängigkeit ist Fremdcode in einem Container, der ab A Marktdaten
   aus dem Internet holt. `pandas` zieht `numpy` nach, `requests` zieht `urllib3`, `certifi`,
   `charset-normalizer` und `idna` nach — fünf Pakete für etwas, das `urllib` kann.
2. **Platz und Speicher.** Das Image soll klein bleiben; `pandas` + `numpy` allein sind rund
   120 MB installiert. Die tatsächliche Ausstattung von CT 107 ist noch offen (Spec-Frage F-1;
   die Installer-Vorgabe lautet 4 Kerne / 8192 MB / 32 GB, der Nutzer hat abweichend
   installiert). Die Entscheidung gilt unabhängig von der Antwort — sie fiele bei 8 GB nicht
   anders aus.
3. **Kontrolle über das Füllmodell.** Ein fremdes Backtest-Framework bringt sein eigenes
   Gebühren-, Slippage- und Orderrouting-Modell mit. Wenn A dessen Annahmen nicht kennt, kann
   A auch nicht beweisen, dass live und Zeitraffer dasselbe tun (E-001, Kriterium A-8).

## Alternativen, die verworfen wurden

| Alternative | Warum nicht |
|---|---|
| `freqtrade` | Bringt Strategie-Framework, Exchange-Anbindung, eigene Datenbank und ein Web-UI mit — ersetzt faktisch das gesamte Projekt und widerspricht dem Zweck eines Labors, in dem man versteht, was rechnet |
| `backtrader` | Leichter, aber seit Jahren kaum gepflegt; und sein Live-Pfad ist ein anderer als sein Backtest-Pfad — genau der Widerspruch, den E-001 ausschließt |
| `pandas` nur für die Zeitreihenrechnung | Der teuerste Teil (Import, Speicher) für den einfachsten Bedarf. A rechnet Summen und laufende Maxima über Listen |
| `requests` statt `urllib` | Bequemer, aber vier zusätzliche Pakete. Und `urllib` erlaubt es leichter, Weiterleitungen hart zu verbieten (Spec 11.1, Kriterium A-17b) |
| Playwright für einen Dashboard-Rauchtest | Größter Einzelposten von allen (Browser-Binaries). Die Folge ist die bewusst offen gelassene Lücke L-1 der Spec — dort benannt, nicht wegdefiniert |

## Kosten bei Irrtum

**Mehr eigener Code.** A schreibt rund 1.345 Zeilen, die teils in Bibliotheken existieren:
Kerzenmodell, Quantisierung, Drawdown-Rechnung, HTTP-Client, Chart. Dieser Code muss selbst
getestet und selbst gewartet werden. Rund 20 der geforderten **≥ 66** Tests existieren nur
deshalb.

**Eine Lücke, die wir nicht schließen.** L-1: Ob das Dashboard die beiden Polylinien wirklich
zeichnet, prüft in A niemand automatisch. Das ist der direkte, benannte Preis dieser
Entscheidung — und der Grund, warum die Spec dazu schreibt, dass ein Blick in Safari kein Test
ist. Die Lücke wird durch eine spätere Aufgabe geschlossen (Playwright in einem separaten
Entwicklungs-Image, nie im Laufzeit-Image).

**Fehlende Bequemlichkeit später.** Wenn Teilprojekt C ein RL-Framework mitbringt, das `numpy`
verlangt (praktisch alle tun das), fällt diese Entscheidung ohnehin — aber dann für C, nicht für
A. Wichtig ist, dass A selbst dann lauffähig bleibt, ohne `numpy` zu importieren; die
Zeitraffer-Schnittstelle gibt Python-Listen und `Decimal` zurück, C wandelt an seiner Grenze.
Dieser Übergang kostet C eine Umwandlungsschicht — bekannte, eingeplante Kosten.

**Der teure Fall, und er ist mit 15m näher gerückt:** Kriterium A-9 verlangt 35.040
Entscheidungen in < 12,0 s (Entwicklungsrechner) bzw. < 40,0 s (Container) — viermal so viel
Arbeit wie in der 1h-Fassung. Scheitert reines Python mit Decimal daran, bleiben drei Wege:
Ganzzahl-Arithmetik (E-002, teuer), ein C-Modul (neue Abhängigkeit über die Hintertür) oder
die Schwelle senken (unzulässig: das Kriterium an den Code anzupassen verstößt gegen Prime
Directive 4). Deshalb wird A-9 in Bauschritt 6 gemessen, solange eine Umkehr noch billig ist.
