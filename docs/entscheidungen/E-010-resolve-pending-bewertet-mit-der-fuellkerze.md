# E-010 — `resolve_pending()` bewertet mit der Füllkerze (offen, Blocker für A2)

- **Datum:** 2026-09-21
- **Status:** **bewusst offen gelassen in A1** (Orchestrator) — **Blocker für Teilprojekt A2**
- **Betrifft:** `app/aitra/execute.py` (`resolve_pending`), `app/aitra/replay.py` (`run_replay`)
- **Spec:** E-001 (quellenagnostisches Ledger), E-006 (Fill auf der Folgekerze), Kriterium A-8
- **Vorgeschichte:** Migration 3 / `pending_ref_price` (Fix-Welle A1, Commit `afbf80f`)

## Worum es geht

Ein schwebender Vorschlag wird an zwei Stellen mit Preisen gefüttert, wenn die Folgekerze
eintrifft:

| | live (`resolve_pending`) | Replay (`run_replay`) |
|---|---|---|
| **Bemessung** (`ref_price` → `exec_price` → `raw_qty`) | `pending_ref_price` (Vorschlagszeit) ✅ | `current.close` (Vorschlagszeit) ✅ |
| **Bewertung** (`valuation` → `equity` → `target_quote`, `cash` → `INSUFFICIENT_CASH`) | **`candle.open` (Füllzeit)** ❌ | `current.close` (Vorschlagszeit) |
| Füllpreis | `candle.open` | `candles[t].open` |

Die **Bemessung** ist in der Fix-Welle A1 korrigiert worden: Der zum Vorschlagszeitpunkt gültige
`ref_price` wird in `decisions.pending_ref_price` gespeichert (Migration 3) und beim Auflösen
wiederverwendet.

Die **Bewertung** ist es nicht. `resolve_pending()` ruft weiterhin

```python
marks = {**ctx.ledger.last_marks, candle.symbol: candle.open}
valuation = ctx.ledger.mark(marks, ts_ms=ctx.clock.now_ms())
```

auf. `valuation.equity` bestimmt über `target_quote = equity * position_pct / 100` die Ordergröße,
`valuation.cash` entscheidet über `INSUFFICIENT_CASH`. Beide hängen damit an der Füllkerze, also
an einem Preis, den die Entscheidung noch nicht kennen konnte. Für die Parität zwischen live und
Replay zählt das genauso wie der `ref_price`.

## Entscheidung

**In A1 wird nichts mehr geändert.** Der Befund wird hier festgehalten statt behoben.

## Begründung — warum das in A1 folgenlos ist

Das ist prüfbar, nicht behauptet: **A1 baut keinen Livepfad.**

- `run_replay()` übergibt `execute_proposal()` immer ein `next_candle` (`candles[t]`) und füllt
  damit sofort. Der Zweig `next_candle is None` → `mark_decision_pending()` wird im Replay nie
  betreten.
- `resolve_pending()` hat im Produktivcode **keinen Aufrufer**. Der einzige vorgesehene Aufrufer
  ist `poller.py` — und `poller.py` existiert in A1 nicht (siehe Modultabelle Abschnitt 3.1:
  „Live-Betriebsart: Thread, Backoff, … schwebende Fills ausführen"; das Modul ist A2).
- Nachprüfbar mit `grep -rn "resolve_pending" app/aitra/*.py` (am 2026-09-21 ausgeführt):
  fünf Treffer, davon **vier Kommentare/Docstrings** (`db.py:111`, `ledger.py:91`,
  `execute.py:6`, `store.py:268`) und **ein** Codetreffer — die Definition selbst,
  `execute.py:138`. Kein Aufruf. `ls app/aitra/poller.py` → `No such file or directory`.
  `grep -n "next_candle" app/aitra/replay.py` → genau eine Zeile, `next_candle=candles[t]`.

Die Asymmetrie kann also **kein A1-Ergebnis verfälschen**. Kein Abnahmekriterium aus A1 misst sie,
und keines wird durch sie falsch.

## Warum es vor dem Poller in A2 gelöst sein muss

Sobald `poller.py` entsteht, ist `resolve_pending()` der reguläre Live-Füllweg. Ab diesem Moment:

- Dieselbe Entscheidung ergibt live eine andere Menge als im Replay, sobald `candle.open` von
  `current.close` abweicht — also praktisch immer.
- **Kriterium A-8 („drei Quellen, ein Hash") wird unerreichbar.** A-8 vergleicht die Fill-Listen
  aus `ListSource`, `SqliteSource` und dem Livepfad über SHA-256. Unterschiedliche Mengen
  erzeugen unterschiedliche `qty`, `gross_quote`, `fee`, `net_quote`, `cash_after` — der Hash
  läuft auseinander, und zwar nicht durch einen Fehler, sondern durch Konstruktion.
- Das ist genau der Fall, vor dem E-001 unter „Kosten bei Irrtum" warnt: *„Backtests und Live
  driften auseinander, ohne dass irgendein Test rot wird. Das ist der eigentlich gefährliche
  Fall, weil er still ist."* Hier ist er nicht mehr still — er ist hiermit aufgeschrieben.

**→ Dieses Dokument ist ein Blocker für Teilprojekt A2: zu lösen, bevor `poller.py`
`resolve_pending()` aufruft.**

## Die zwei Wege

| Weg | Was zu tun ist | Preis |
|---|---|---|
| **A — die zum Vorschlagszeitpunkt berechnete Order mitspeichern** | Nicht nur `pending_ref_price`, sondern die fertig bemessene `base_qty` (und damit implizit `equity`/`cash` der Vorschlagszeit) an der schwebenden Entscheidung ablegen. `resolve_pending()` ruft `size_order()` dann gar nicht mehr auf, sondern reicht die gespeicherte Order direkt an `Ledger.apply()` weiter. Braucht eine Migration 4 (`pending_base_qty TEXT`). | Die Kassenprüfung wandert vollständig in `Ledger.apply()` (dort steht sie bereits, siehe A-2-Befund der Fix-Welle: es sind zwei Wächter). Eine zwischen Vorschlag und Füllung geschrumpfte Kasse führt dann zu `INSUFFICIENT_CASH` im Ledger statt zu einer kleineren Order — fachlich die ehrlichere Antwort, aber eine Verhaltensänderung. |
| **B — A-8 abschwächen** | A-8 vergleicht Livepfad und Replay nicht mehr über einen identischen Hash, sondern nur noch über eine Toleranz oder gar nicht. | Gibt die Kernzusage von E-001 auf. Damit trainiert Teilprojekt C gegen eine Welt, die es live nicht gibt — genau das, was E-001 verhindern soll. **Nicht empfohlen.** |

Der Orchestrator entscheidet in A2. Aus Sicht von E-001 ist **Weg A** der einzige, der die
Entscheidung trägt.

## Kosten bei Irrtum

**Wenn die Einschätzung „in A1 folgenlos" falsch ist** (es gäbe doch einen A1-Pfad, der
`resolve_pending()` erreicht): Dann wären die Ordergrößen dieses Pfads gegen die Füllkerze
bemessen. Der Fehler wäre klein (er skaliert mit der Lücke zwischen Schluss- und
Eröffnungskurs, gemessen in der Größenordnung von Zehntelprozent je Kerze), aber systematisch in
eine Richtung, sobald der Markt trendet. Erkennbar: der obige `grep` müsste einen zweiten
Aufrufer zeigen. Kosten der Prüfung: ein Befehl.

**Wenn wir es in A1 doch noch geändert hätten:** Eine Migration 4 und eine Verhaltensänderung an
der Kassenprüfung in einer Fix-Welle, die unmittelbar vor der Nachprüfung und dem Push steht —
ohne dass ein einziges A1-Kriterium davon profitiert. Das Risiko läge vollständig auf der
Änderung, der Nutzen wäre null.

**Wenn wir es gar nicht aufschrieben:** Der Befund verschwände. In A2 fiele er als
unerklärlicher A-8-Fehlschlag wieder an — dann aber ohne den Kontext, dass er bekannt, bewertet
und bewusst vertagt war.
