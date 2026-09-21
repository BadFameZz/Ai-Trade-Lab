# E-002 — Geld ist Decimal, nicht float

- **Datum:** 2026-09-20
- **Status:** entschieden (vom Auftraggeber vorgegeben)
- **Betrifft:** `app/aitra/money.py` (neu), `config.py`, `risk.py`, `ledger.py`, `store.py`
- **Spec:** Abschnitte 6.4 und 15 (Befund B-4)

## Entscheidung

Alle Geldbeträge und alle Asset-Mengen sind `decimal.Decimal`, mit festen Schrittweiten je
Symbol (`tick_size` für Preise, `step_size` für Mengen, `min_notional` für Ordergrößen), so wie
die Börse sie vorgibt.

**Die Grenze zwischen Decimal und float ist scharf gezogen:**

| Typ | Wofür | Beispiele |
|---|---|---|
| `Decimal` | Geldbeträge, Preise, Asset-Mengen, Gebühren | `cash`, `equity`, `price`, `qty`, `fee`, `starting_balance` |
| `float` | Verhältnisse, Prozente, Konfidenzen, Basispunkte | `exposure_pct`, `position_pct`, `daily_loss_pct`, `confidence`, `fee_bps` |
| `int` | Zeitstempel in Millisekunden, Zähler | `open_time`, `close_time`, `trades_total` |

Decimal-Kontext: `prec=34`, Rundung je nach Richtung explizit
(`ROUND_FLOOR` für Mengen, `ROUND_CEILING` für Kaufpreise und Gebühren, `ROUND_FLOOR`
für Verkaufspreise) — nie der Standard, immer gegen den Händler.

**Korrektur an der Quelle:** Der Wert wird direkt aus dem Umgebungsstring gelesen
(`Decimal(raw)`), **nie** über `float`. `STARTING_BALANCE=0.1` ergibt heute
`0.1000000000000000055511151231257827`; das ist keine Anzeigefrage, sondern ein Fehler
in `config._num()`, und er wird dort behoben, nicht im Ledger abgefangen.

## Begründung

Später soll echtes Geld laufen. Aber auch vorher gilt: Mit `float` ist die Buchhaltungsidentität
`equity == cash + Σ qty·preis` nur ungefähr wahr, und „ungefähr" ist kein Zustand, in dem man
einen Fehler von einem Rundungsrest unterscheiden kann. Mit Decimal bei `prec=34` sind alle
Operationen des Füllmodells exakt (Nachweis in Spec 6.4) — und deshalb kann Kriterium A-1
eine Differenz von **exakt null** fordern statt einer Toleranz. Eine Toleranz von 0,01 USDC
würde bei 10.000 Fills einen systematischen Gebührenfehler von bis zu 100 USDC durchlassen.

## Alternativen, die verworfen wurden

| Alternative | Warum nicht |
|---|---|
| Ganzzahlige „Satoshi"-Arithmetik (`int` in kleinster Einheit) | Schneller und ebenfalls exakt, aber jede Stelle im Code müsste ihren Skalierungsfaktor kennen; die Börse liefert Dezimalstrings, die Umrechnung an jeder Grenze wäre eine neue Fehlerquelle |
| `float` behalten und in der Anzeige runden | Verschiebt das Problem dorthin, wo es unsichtbar wird. Und das Vorhaben „später echtes Geld" wäre damit erledigt |
| `Fraction` | Exakt, aber unbegrenzt wachsende Nenner bei jeder Division — im Zeitraffer über 10⁴ Schritte ein Speicher- und Tempoproblem |

## Kosten bei Irrtum

**Tempo:** `Decimal` ist in CPython rund 3- bis 10-mal langsamer als `float`. Das schlägt direkt
auf Kriterium A-9 (1 Jahr Stundenkerzen in < 3,0 s) durch. Sollte A-9 an Decimal scheitern, ist
die Rücknahme *teuer*: entweder auf Ganzzahl-Arithmetik wechseln (berührt jede Zeile in
`money.py`, `ledger.py`, `sizing.py`, `store.py`) oder `float` im Zeitraffer und `Decimal` live
— was E-001 bricht. Deshalb ist A-9 früh zu messen, in Bauschritt 6, nicht am Ende.

**Umstellungsschaden im Bestand:** `PortfolioState` und `Config` wechseln den Typ. Die
Bestandstests konstruieren beide **positional** (`test_risk.py:11`, `test_api.py:11,48`) und
übergeben Python-`int`s — die sind mit Decimal verträglich. Ein übersehenes gemischtes
`float`/`Decimal`-Rechenwerk wirft jedoch `TypeError` erst zur Laufzeit. Gegenmittel: `money.py`
ist die einzige Stelle, die Werte in Decimal wandelt, und tut das an der Systemgrenze
(Umgebung, HTTP-JSON, Datenbank).

**Wenn wir es *nicht* tun:** Die Buchhaltung ließe sich nie exakt prüfen. Jeder echte Fehler
könnte sich als Rundungsrest tarnen, und das Kriterium müsste so lange aufgeweicht werden, bis
es nichts mehr aussagt.
