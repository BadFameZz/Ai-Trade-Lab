# E-001 — Das Ledger ist quellenagnostisch

- **Datum:** 2026-09-20
- **Status:** entschieden (vom Auftraggeber vorgegeben, nicht neu aufzurollen)
- **Betrifft:** `app/aitra/ledger.py`, `marketdata.py`, `replay.py`, `poller.py`
- **Spec:** `docs/specs/2026-09-20-teilprojekt-a-marktdaten-ledger-benchmark.md`, Abschnitt 4

## Entscheidung

Das Paper-Ledger bekommt **Kerzen** und erzeugt **Fills**. Es weiß nicht und darf nicht wissen,
ob eine Kerze gerade live von Binance kam, aus der SQLite-Tabelle `candles` gelesen wurde oder
aus einer Liste im Speicher stammt. Eine Implementierung, drei Betriebsarten: live, aus der
Datenbank, im Zeitraffer.

Technische Folge, die diese Entscheidung erst durchsetzbar macht:
`ledger.py`, `money.py`, `sizing.py` und `benchmark.py` rufen **niemals** `time.time()`,
`datetime.now()` oder `random.*` auf. Jeder Zeitstempel ist ein Parameter, jede Uhr ist
injiziert (`WallClock` / `SimClock`).

## Begründung

Ein RL-Agent (Teilprojekt C) braucht Zehntausende Entscheidungen im Zeitraffer. Zwei getrennte
Ledger — eines für live, eines für Backtests — widersprechen sich unweigerlich: irgendwann
rundet das eine anders, zieht die Gebühr an anderer Stelle ab oder füllt zu einem anderen Preis.
Dann trainiert C gegen eine Welt, die es live nicht gibt, und niemand merkt es, bis Geld fließt.

## Alternativen, die verworfen wurden

| Alternative | Warum nicht |
|---|---|
| Getrenntes Backtest-Ledger, auf Geschwindigkeit optimiert | Genau der Widerspruch, den die Entscheidung verhindert. Der Geschwindigkeitsvorteil ist zudem nicht nötig: Kriterium A-9 fordert ≥ 876 Entscheidungen/s und die gemeinsame Implementierung liefert das |
| Fremdbibliothek (backtrader, freqtrade) mit eigenem Live- und Backtest-Modus | E-004: Speicher- und Plattenbudget, und die Frage würde nur an eine fremde Codebasis delegiert |
| Ledger nimmt einen Preis statt einer Kerze | Wäre noch abstrakter, verliert aber `open_time` — und damit die Prüfbarkeit, dass auf der Folgekerze gefüllt wurde (E-006, Kriterium A-7) |

## Kosten bei Irrtum

**Wenn die Entscheidung falsch ist** (also getrennte Ledger doch besser gewesen wären):
Der Preis ist Geschwindigkeit. Die gemeinsame Implementierung trägt im Zeitraffer Ballast mit,
den ein reiner Backtest-Pfad weglassen könnte — geschätzt Faktor 2 bis 5. Falls C später an
dieser Grenze scheitert, ist die Rücknahme *teuer*, aber machbar: ein spezialisierter
Zeitraffer-Pfad müsste gegen den gemeinsamen Pfad differenziell getestet werden
(Kriterium A-8 wird dann zum Dauerwächter statt zum Einmalbeweis).

**Wenn die Entscheidung richtig ist, aber die Umsetzung leckt** (irgendwo doch eine Wanduhr,
ein Zufallswert oder ein Live-Sonderfall im Ledger): Backtests und Live driften auseinander,
ohne dass irgendein Test rot wird. Das ist der eigentlich gefährliche Fall, weil er still ist.
Dagegen stehen drei Kriterien: A-8 (drei Quellen, ein Hash), A-8b (statische Prüfung auf Uhr
und Zufall), A-12 (ein Replay über Kerzen von 2024 löst null Veraltet-Ereignisse aus).

**Kosten, die Entscheidung gar nicht zu treffen:** Sie wäre implizit doch gefallen — durch das
erste `datetime.now()` im Ledger. Dann gäbe es zwei Welten und keinen Test, der sie vergleicht.
