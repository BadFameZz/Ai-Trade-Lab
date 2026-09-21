# E-008 — Der Kill Switch löst sich im Replay an der UTC-Tagesgrenze, live nie

- **Datum:** 2026-09-20
- **Status:** entschieden (F-8)
- **Betrifft:** `app/aitra/replay.py`, `execute.py`, `web.py`
- **Spec:** Abschnitte 4.4 und 9.2; Kriterium A-12b

## Entscheidung

Der Kill Switch, den das **Tagesverlustlimit** auslöst, verhält sich in den Betriebsarten
unterschiedlich:

| Betriebsart | Verhalten |
|---|---|
| **live** | Er bleibt aktiv, bis ein Mensch ihn mit Admin-Token freigibt. Unverändert seit v0.2.0 |
| **db-Replay / Zeitraffer** | Er ist **lauf-lokal** (kein Eintrag in `state`) und löst sich beim ersten Kerzenwechsel über eine UTC-Tagesgrenze automatisch |

**Das ist die einzige bewusste Verhaltensabweichung zwischen den Betriebsarten im gesamten
Teilprojekt A.** Alles andere — Füllmodell, Gebühren, Rundung, Sizing, Risk-Prüfung — ist
identisch (E-001, Kriterium A-8).

Die Abweichung betrifft ausdrücklich **nur** den Tagesverlust-Kill. Der Kill Switch aus
**veralteten Marktdaten** (E-005) existiert im Replay gar nicht, weil die `SimClock` das
Datenalter strukturell auf 0 hält.

## Begründung

Ein Backtest über ein Jahr, der nach dem ersten Tag mit 2 % Verlust stillsteht, misst nichts.
Er misst insbesondere nicht das, wofür er da ist: wie sich eine Strategie über viele Tage
schlägt. Ohne diese Ausnahme wäre jeder Jahreslauf faktisch ein Ein-Tages-Lauf mit 8.700
Leerschritten — und Teilprojekt C könnte gar nicht trainieren.

Live ist die Lage umgekehrt: Dort ist der Kill Switch kein Messinstrument, sondern eine Bremse.
Wenn er greift, ist etwas passiert, das ein Mensch ansehen soll. Sich nach Mitternacht UTC von
selbst zu lösen, wäre das genaue Gegenteil dessen, wofür er gebaut wurde.

Die Tagesgrenze ist die richtige Marke, weil das Limit selbst tagesbezogen ist:
`RiskEngine.daily_loss_pct()` rechnet gegen `start_of_day_equity`. Mit dem Tageswechsel wird
diese Bezugsgröße ohnehin neu gesetzt — der Zustand, der den Kill ausgelöst hat, existiert dann
nicht mehr. Ein Kill, dessen Anlass weg ist, im Replay weiterzuführen, wäre inkonsistent.

## Umsetzung

```
# replay.py, je Kerze:
if utc_date(clock.now_ms()) != kill_switch_tag:
    kill_switch_lokal = False
    sod_equity        = ledger.mark(...).equity     # dieselbe Grenze, dieselbe Zeile
```

Der lauf-lokale Kill Switch ist eine einfache Variable in `run_replay()`. Er wird **nie** nach
`state.kill_switch` geschrieben — ein Replay darf den Live-Betrieb nicht anhalten, und ein
aktiver Live-Kill darf einen Replay nicht blockieren. Die beiden Zustände berühren sich nicht.

## Alternativen, die verworfen wurden

| Alternative | Warum nicht |
|---|---|
| Kill Switch auch im Replay bis zum Laufende | Jeder Jahreslauf endet nach dem ersten schlechten Tag. C kann nicht trainieren, Backtests messen nichts |
| Tagesverlustlimit im Replay ganz abschalten | Dann prüft der Backtest eine andere Risk Engine als die live laufende — ein weit größerer Bruch von E-001 als der Tagesreset. Die Regel bleibt aktiv, nur ihre Nachwirkung endet mit dem Tag |
| Auch live an der Tagesgrenze lösen | Der Kill Switch wäre keine Bremse mehr, sondern eine Pause. Genau der Fall, für den man ihn gebaut hat, würde sich nach Stunden von selbst erledigen |
| Rollierendes 24-Stunden-Fenster statt Kalendertag | Konsistenter gedacht, aber `daily_loss_pct()` rechnet gegen `start_of_day_equity`, also gegen einen Kalendertag. Zwei verschiedene Tagesbegriffe im selben System wären schlimmer als ein unperfekter |

## Kosten bei Irrtum

**Die Abweichung ist die Kosten.** E-001 verspricht, dass live und Zeitraffer dasselbe tun;
hier tun sie es nachweislich nicht. Der Preis ist, dass ein Replay-Ergebnis *optimistischer*
ist als der Live-Betrieb: Live hätte nach dem ersten 2-%-Tag ein Mensch eingreifen müssen, im
Replay läuft die Strategie weiter. Eine Strategie, die im Backtest nach mehreren
Verlusttagen erholt aussieht, wäre live vielleicht nach dem ersten gestoppt worden.

**Das ist zu dokumentieren, nicht zu verstecken.** Jeder Replay-Bericht muss die Zahl
„Kill-Switch-Auslösungen im Lauf" nennen. Ein Lauf mit 40 Auslösungen ist kein guter Lauf,
auch wenn seine Endzahl gut aussieht — er beschreibt eine Strategie, die live 40-mal angehalten
worden wäre. Diese Kennzahl gehört in die Ausgabe von `run_replay()` und später in die
Bewertung von B und C.

**Wenn die Abweichung unbemerkt größer wird** — etwa, indem später weitere Regeln „nur im
Replay" gelockert werden — verliert E-001 seinen Wert schleichend. Gegenmittel: Es bleibt bei
**genau einer** Abweichung, sie steht hier, und Kriterium A-12b misst beide Seiten (Replay
läuft an Tag 2 weiter, live nicht). Eine zweite Abweichung braucht eine eigene
Entscheidungsnotiz — oder sie ist ein Fehler.

**Wenn wir es nicht tun:** Teilprojekt C ist nicht baubar. Das ist die teuerste aller Varianten.
