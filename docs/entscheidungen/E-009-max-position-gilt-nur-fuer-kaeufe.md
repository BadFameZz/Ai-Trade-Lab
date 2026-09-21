# E-009 — `MAX_POSITION` gilt nur noch für Käufe

- **Datum:** 2026-09-21
- **Status:** entschieden (Orchestrator), geprüft und bestätigt im Code-Review
- **Betrifft:** `app/aitra/risk.py`
- **Spec:** Kriterium A-6c

## Entscheidung

`RiskEngine.check()` wendet die `MAX_POSITION`-Prüfung (`p.position_pct > self.cfg.max_position_pct`)
künftig nur noch auf `action == "BUY"` an:

```python
if action == "BUY" and p.position_pct > self.cfg.max_position_pct:
    return RiskDecision(False, "MAX_POSITION", ...)
```

Das ist analog zur `MAX_EXPOSURE`-Prüfung zwei Zeilen darunter, die im Bestand bereits so
eingeschränkt war (`if action == "BUY" and pf.exposure_pct + p.position_pct > ...`).

Die Entscheidung kam vom Orchestrator, im Rahmen der Umsetzung von Aufgabe 7 (Befund B-1,
Kriterium A-6c). Sie wurde anschließend im Code-Review geprüft und bestätigt.

## Begründung

**Die tragende Begründung ist die des Prüfers, nicht die ursprüngliche des Orchestrators** (siehe
unten, verworfenes Argument): Kriterium A-6c — bereits in E-003 tabellarisch festgehalten —
verlangt wörtlich, dass bei `max_position_pct = 10`, 8 % gehaltener BTC-Position, ein Verkauf
von 12 % den Code `NO_POSITION` liefert:

| Portfolio | Vorschlag | erwartet |
|---|---|---|
| 8 % BTC, 20 % ETH | `SELL BTCUSDC` 12 % | `NO_POSITION` |

Solange `MAX_POSITION` unbedingt auch für SELL gilt, ist dieser Messpunkt mathematisch
unerfüllbar: `12 > max_position_pct(10)` triggert `MAX_POSITION`, bevor die symbolbezogene
Positionsprüfung (Befund B-1, Kriterium A-6c) überhaupt erreicht wird — das Ergebnis wäre immer
`MAX_POSITION`, nie `NO_POSITION`. Die Spec erzwingt die Ausnahme also selbst, unabhängig von
jeder weiteren Erwägung.

**Die ergänzende Begründung:** Ein Verkauf senkt das Risiko. Eine Obergrenze für Positions*größen*
an einem Verkauf anzulegen, kehrt ihren Zweck um. Dass `MAX_EXPOSURE` zwei Zeilen tiefer bereits
`BUY`-only war, zeigt, dass derselbe Gedanke im Bestand schon zu Ende gedacht wurde — bei
`MAX_POSITION` fehlte dieselbe Einschränkung offenbar nur versehentlich.

## Ein verworfenes Argument, ausdrücklich festgehalten

Die ursprüngliche Begründung des Orchestrators lautete: Ohne die Korrektur ließe sich eine durch
Kursgewinne über `max_position_pct` gewachsene Position „gar nicht mehr verkaufen". **Das ist so
nicht richtig und wurde im Review korrigiert:** Ein Verkauf bis genau zur Grenze
(`position_pct == max_position_pct`) wäre auch mit der unbedingten Prüfung möglich gewesen
(`p.position_pct > self.cfg.max_position_pct` ist bei Gleichheit `False`), und eine größere
Position ließe sich in mehreren Tranchen unterhalb der Grenze abbauen. Die Position wäre also
nicht dauerhaft blockiert, nur umständlicher zu reduzieren. Diese Notiz nennt das verworfene
Argument bewusst mit, statt es stillschweigend durch die stärkere Begründung zu ersetzen.

## Alternativen, die verworfen wurden

| Alternative | Warum nicht |
|---|---|
| `MAX_POSITION` unverändert unbedingt lassen, Testwert in A-6c anpassen | A-6c ist Spec-Text (E-003), kein Testdetail — der Test wird nicht an den Code angepasst |
| Prüfungsreihenfolge ändern (SELL-Check vor MAX_POSITION), `MAX_POSITION` unbedingt lassen | Löst denselben Fall, aber verschleiert die eigentliche Aussage: MAX_POSITION soll SELL fachlich gar nicht betreffen, nicht nur zeitlich nachrangig sein |
| Nur den konkreten A-6c-Fall per Sonderfall behandeln | Uneinheitlich zu `MAX_EXPOSURE`, das bereits dieselbe Sachfrage strukturell löst |

## Kosten bei Irrtum

Ein Verkauf größer als `max_position_pct` ist ab jetzt erlaubt. Er bleibt aber durch die
symbolbezogene Positionsprüfung (Befund B-1) gedeckelt: `p.position_pct > held_pct` verhindert
weiterhin jeden Verkauf über die tatsächlich gehaltene Position hinaus. Im Spot-Handel ohne
Hebel kann `position_pct_by_symbol` die reale Position strukturell nicht überschreiten — es gibt
also keinen Weg, über diese Änderung mehr zu verkaufen, als man besitzt.

**Wenn die Entscheidung falsch ist:** Umkehrbar durch eine einzige Bedingung in `risk.py`
(`action == "BUY" and` vor der `MAX_POSITION`-Prüfung entfernen). Kein Datenmodell, keine
Migration betroffen.

**Wenn wir es nicht täten:** Kriterium A-6c wäre nicht erfüllbar, ohne entweder die Spec oder den
Test zu verbiegen — beides gegen die Prime Directives. Das Nadelöhr (E-003) würde an genau der
Stelle blockieren, die es korrigieren soll.
