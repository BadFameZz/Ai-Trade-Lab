# E-006 — Gefüllt wird zum Open der Folgekerze, auch live

- **Datum:** 2026-09-21 (Fassung 3: Zahlen für 15m; Fassung 2 rechnete mit 1h)
- **Status:** **entschieden** (F-7)
- **Betrifft:** `app/aitra/ledger.py`, `replay.py`, `poller.py`, `execute.py`, `static/index.html`
- **Spec:** Abschnitte 4.2, 6.1 und 9.1; Kriterien A-7 und A-7b

## Entscheidung

Eine Entscheidung, die auf Kerze `t` getroffen wird, wird zum **`open` der Kerze `t+1`** gefüllt
— nie zum `close` von `t`, nie zu `high` oder `low` irgendeiner Kerze. **Das gilt auch live.**

`decide_fn` bekommt `candles[:t]`, also eine Folge, deren letztes Element `open_time == t` hat,
und sieht `candles[t+1]` nie. `Ledger.apply(order, candle)` nimmt aus der übergebenen Kerze
**ausschließlich** `candle.open` und `candle.open_time`.

Kriterium A-7 misst das über 8.640 Kerzen (90 Tage 15m), A-7b misst es im Live-Pfad.

## Begründung

**Zum Close derselben Kerze zu füllen ist ein Blick in die Zukunft.** Der Close einer Kerze steht
erst fest, wenn sie vorbei ist; eine Strategie, die ihn zur Entscheidung nutzt *und* zu ihm
kauft, handelt zu einem Preis, den sie im Moment der Entscheidung nicht kennen konnte. Das ist
die häufigste stille Lüge in selbstgebauten Backtests, und sie macht praktisch jede Strategie
profitabel.

**High und Low zu nutzen wäre schlimmer.** Sie sagen nichts über die Reihenfolge innerhalb der
Kerze. Wer zum Low kauft und zum High verkauft, hat kein Modell, sondern einen Wunsch.

**`open` der Folgekerze ist die konservative, prüfbare Wahl:** Er existiert nach der Entscheidung,
er ist eindeutig, und er ist im Zeitraffer und live derselbe Begriff — was E-001 erst möglich
macht. Live anders zu füllen als im Replay hätte E-001 an genau einer Stelle gebrochen; dann
wären alle Backtest-Zahlen nur noch ungefähr auf den Live-Betrieb übertragbar, und niemand
hätte eine Messung dafür, wie ungefähr.

## Die Kosten im Live-Betrieb — beziffert

Live existiert die Folgekerze zum Entscheidungszeitpunkt noch nicht. Umsetzung:
Der Vorschlag wird als **schwebend** in `decisions` abgelegt (`approved=1`, `fill_id=NULL`,
`pending_since_ms` gesetzt) und im nächsten Poll-Zyklus mit der dann vorliegenden Kerze
ausgeführt (`execute.resolve_pending()`).

Mit der entschiedenen Konfiguration (`MARKET_INTERVAL=15m`, `MARKET_POLL_S=60`):

| Größe | Wert bei 15m | zum Vergleich: 1h |
|---|---|---|
| Wartezeit auf die Folgekerze | bis 15 min | bis 60 min |
| plus Poll-Abstand | bis 60 s | bis 60 s |
| **Klick bis Fill** | **bis ~16 Minuten** | bis ~61 Minuten |
| **Verfall eines schwebenden Vorschlags** (`2 · interval_s`) | **30 Minuten** | 2 Stunden |

Diese Minutenzahl war der Grund für den Wechsel von 1h auf 15m: 61 Minuten sind für ein Labor,
in dem jemand ausprobiert, unbrauchbar; 16 Minuten sind vertretbar.

Die Verzögerung wird nicht versteckt:

- `POST /api/risk/check` antwortet mit `status: "pending_fill"` und `expected_fill_after_ms`.
- Das Dashboard zeigt schwebende Vorschläge im Decision Journal als
  „schwebend seit …, Fill erwartet ab …".
- Ein schwebender Vorschlag **verfällt nach 30 Minuten** (`risk_code = "PENDING_EXPIRED"`),
  damit nichts unbemerkt liegen bleibt. A-7b misst die Grenze an 1.799 s und 1.801 s.
- Wer beim Ausprobieren nicht warten will, setzt `MARKET_INTERVAL=1m` — dann sind es ≤ 2 min.
  Genau das tut auch der Rauchlauf A-19a.

## Alternativen, die verworfen wurden

| Alternative | Warum nicht |
|---|---|
| Fill zum `close` derselben Kerze | Blick in die Zukunft. Live wäre es bequemer (sofortiger Fill), aber Live und Replay wichen dann systematisch ab |
| Live zum letzten bekannten `close`, im Replay zum nächsten `open` | Zwei Modelle, ein Ledger — der Widerspruch, den E-001 ausschließt. A-8 (drei Quellen, ein Hash) würde sofort rot |
| Fill zum Mittel aus `open` und `close` der Folgekerze | Statistisch vielleicht realistischer, aber nicht beobachtbar: Diesen Preis konnte niemand handeln |
| Kürzeres Intervall nur für die Ausführung, längeres für die Entscheidung | Zwei Zeitraster im selben Lauf. Die Verzögerung schrumpft, aber jede Aussage über „die Kerze" wird mehrdeutig |
| Konfigurierbare Regel mit mehreren Optionen | YAGNI. `FILL_PRICE_RULE` steht in `runs.params_json`, hat aber genau einen erlaubten Wert |

## Kosten bei Irrtum

**Wenn die Regel zu konservativ ist:** Backtest-Ergebnisse sind schlechter als die Realität,
weil zwischen Signal und Fill ein volles Intervall liegt. Bei 15m sind das 15 Minuten
Kursbewegung, die die Strategie nicht bekommt. Das ist der *gute* Irrtum: Er verwirft zu viel,
statt zu viel zu glauben. Gegenmittel, falls nötig: ein kürzeres Intervall, nicht ein
aggressiveres Füllmodell.

**Wenn die Regel leckt** (irgendwo doch `candles[:t+1]` an `decide_fn`): Jede Messung an jeder
Strategie von B und C wird wertlos, und zwar unsichtbar — die Zahlen sehen nur besser aus.
Das ist der teure Irrtum. Deshalb sind A-7 und A-7b Kriterien mit Rot-Nachweis.

**Wenn die 16 Minuten im Alltag doch stören:** Der billige Weg ist ein kürzeres Intervall
(bei `5m` sind es ≤ 6 min, bei `1m` ≤ 2 min) — dabei fällt keine Entscheidung, nur die
Datenmenge steigt (bei 1m: 525.600 Kerzen im Jahr statt 35.040, Faktor 15, was A-9 und A-16b
neu berechnet erfordert). Der teure Weg wäre, live zum letzten `close` zu füllen und E-001
aufzugeben — inklusive eines neuen Dauerwächters, der die Abweichung zwischen Live- und
Replay-Fills **beziffert**, statt sie auszuschließen.

**Wenn schwebende Vorschläge nicht verfallen:** Bei einem Datenausfall über Stunden würden nach
der Rückkehr alte Vorschläge zu Preisen gefüllt, die mit der ursprünglichen Absicht nichts mehr
zu tun haben. Der Verfall nach 30 Minuten ist die Absicherung; ihn wegzulassen wäre der
stillste Weg, aus einer Verzögerung einen Fehlhandel zu machen. Der zweite Rot-Nachweis von
A-7b prüft genau das.

**Für Teilprojekt B mitzudenken:** Zwischen Vorschlag und Fill liegen bis zu 16 Minuten
Kursbewegung. Ein LLM-Analyst, der im Minutentakt nachlegt, produziert einen Stau schwebender
Vorschläge. B muss die Schwebe kennen — sie steht deshalb in der API-Antwort, nicht nur im Log.
