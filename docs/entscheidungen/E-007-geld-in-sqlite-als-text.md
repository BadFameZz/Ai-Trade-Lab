# E-007 — Geld steht in SQLite als TEXT, nie als REAL

- **Datum:** 2026-09-20
- **Status:** **vorgeschlagen** — gilt als entschieden mit Freigabe der Spec
- **Betrifft:** `app/aitra/db.py` (Migration 2), `store.py`, `money.py`, `web.py`
- **Spec:** Abschnitt 5; Kriterien A-13 und A-14

## Entscheidung

Jede Geldspalte und jede Mengenspalte in den neuen Tabellen (`candles`, `fills`, `positions`,
`equity_curve`) hat den Typ **TEXT**. Kein `REAL`, kein skaliertes `INTEGER`.

- Kanonische Form: feste Nachkommastellenzahl (`quote_precision` bzw. `base_precision`, beide 8),
  erzeugt von `money.to_text(d, dp)`, gelesen von `money.from_text(s)`.
- `store.py` ist die **einzige** Stelle, die diese Umwandlung vornimmt.
- Auf Geldspalten wird nie sortiert und nie gerechnet — SQL sieht sie nur als Blobs zum
  Hin- und Herreichen. Aggregationen (Summen, Drawdown) rechnet Python mit `Decimal`.
- Im JSON der API ist Geld ebenfalls ein **String**. Das Frontend wandelt mit `Number()` nur
  zur Anzeige; die bestehende Formatierungsfunktion `fmt()` in `static/index.html:182` nimmt
  bereits beliebige Eingaben und rundet auf zwei Stellen.

Messung (Kriterium A-13): Nach 1.000 Fills liefert
`SELECT DISTINCT typeof(price), typeof(qty), typeof(fee), typeof(cash_after) FROM fills`
**genau eine** Zeile mit viermal `'text'`.

## Begründung

SQLite kennt keinen Dezimaltyp. `REAL` ist IEEE-754-Double — damit wäre E-002 an der Grenze
zur Datenbank wieder aufgehoben: `Decimal("0.1")` ginge exakt hinein und käme als
`0.1000000000000000055…` zurück. Das ist kein theoretisches Problem, sondern genau der Fehler,
den Spec-Befund B-4 bereits in `config._num()` gefunden hat.

Besonders heimtückisch ist SQLites **Typaffinität**: Eine Spalte mit der Deklaration `REAL`
wandelt einen eingefügten Text still in eine Fließkommazahl um. Der Fehler wäre also nicht
einmal sichtbar — die Zeile sähe beim `SELECT` fast richtig aus. Deshalb prüft A-13 `typeof()`
und nicht den Wert.

Und es ist die Voraussetzung für Kriterium A-14: Kasse und Positionen lassen sich aus den
`fills`-Zeilen **exakt** (`Decimal("0")` Differenz) nachrechnen. Mit `REAL` ginge nur
„ungefähr" — und damit ließe sich ein Buchungsfehler nie von einem Rundungsrest unterscheiden.

## Alternativen, die verworfen wurden

| Alternative | Warum nicht |
|---|---|
| `REAL` | Siehe oben. Typaffinität macht den Fehler unsichtbar |
| `INTEGER` in kleinster Einheit (Satoshi/Cent) | Exakt und kompakt, aber jede Spalte bräuchte ihren Skalierungsfaktor, und der müsste bei jedem neuen Symbol mitgepflegt werden. Konsistent mit E-002, das dieselbe Alternative aus demselben Grund verworfen hat |
| `NUMERIC`-Affinität mit Textwerten | SQLite würde den Text „nach Möglichkeit" in INTEGER oder REAL wandeln — dieselbe stille Umwandlung, nur schwerer vorhersagbar |
| Eine Erweiterung wie `decimal.so` | Neue Abhängigkeit und ein Kompilierschritt im Slim-Image. E-004 |

## Kosten bei Irrtum

**Platz.** Ein `REAL` belegt 8 Bytes, ein Text wie `"81287.04000000"` rund 15. Bei fünf
Geldspalten pro Kerze sind das ~35 Bytes Mehrbedarf pro Zeile; hochgerechnet auf ein Jahr
1m-Kerzen eines Symbols (525.600 Zeilen) rund **18 MB extra**. Kriterium A-16b deckelt den
Gesamtbedarf bei ≤ 250 Bytes/Zeile und misst ihn, statt ihn zu schätzen.

**Tempo.** Jedes Lesen erzeugt ein `Decimal` aus einem String statt ein `float` aus 8 Bytes.
Bei einem Replay über 8.760 Kerzen aus der Datenbank sind das ~44.000 Umwandlungen — spürbar,
aber im Rahmen von Kriterium A-9. Für den Zeitraffer von Teilprojekt C ist die relevante
Betriebsart ohnehin die In-Memory-Quelle ohne Datenbankzugriff.

**Keine SQL-Aggregation über Geld.** `SELECT sum(fee) FROM fills` ist verboten (es würde stille
Fließkomma-Addition erzeugen). Summen rechnet Python. Das kostet bei großen Läufen Speicher und
Zeit, ist aber der Preis für Exaktheit. Falls das je zum Engpass wird, ist die Rücknahme
*mittelteuer*: eine zusätzliche, bewusst gerundete `REAL`-Spalte **neben** der TEXT-Spalte,
ausschließlich für Auswertungen — niemals als Buchungsgrundlage.

**Wenn wir es nicht tun:** E-002 wäre an der Datenbankgrenze wirkungslos, die Kriterien A-1 und
A-14 könnten keine exakte Null fordern, und beide müssten auf eine Toleranz aufgeweicht
werden — bis sie nichts mehr messen.
