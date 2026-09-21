# E-003 — Die Risk Engine bleibt das Nadelöhr

- **Datum:** 2026-09-20
- **Status:** entschieden (vom Auftraggeber vorgegeben)
- **Betrifft:** `app/aitra/execute.py` (neu), `risk.py`, `ledger.py`, `web.py`
- **Spec:** Abschnitte 9.1 und 16; Kriterien A-6, A-6b, A-6c

## Entscheidung

Jeder `Proposal` — ob von einem Menschen über `POST /api/risk/check`, von Teilprojekt B (LLM)
oder von Teilprojekt C (RL-Agent) — geht durch `RiskEngine.check()`, bevor irgendetwas gefüllt wird.

Durchgesetzt wird das strukturell, nicht durch Disziplin:

1. Es gibt **genau eine** Funktion, die `Ledger.apply()` aufruft: `execute.execute_proposal()`
   (inklusive ihres Helfers `resolve_pending()` für schwebende Vorschläge, E-006).
2. Ihre Reihenfolge ist fest: Risk Engine → Sizing → Ledger → Journal.
3. Kriterium **A-6b** misst das statisch:
   `grep -rn "\.apply(" app/aitra --include=*.py | grep -v "^app/aitra/execute.py"` muss
   **0 Zeilen** liefern.
4. Kriterium **A-6** misst es fachlich: für alle 9 Ablehnungscodes aus `risk.py` entsteht je
   1 Zeile in `decisions` und **0** Zeilen in `fills`.

## Begründung

Die Trennung „Strategie schlägt vor, Engine entscheidet" steht bereits im Docstring von
`risk.py` und war die Kernidee von v0.2.0. Sobald es ein Ledger gibt, ist sie nicht mehr nur
eine Architekturidee, sondern die einzige Sicherung. Ein RL-Agent optimiert gnadenlos gegen das,
was er darf — wenn er einen Weg am Nadelöhr vorbei findet, nimmt er ihn, und zwar in Schritt
40.000, den niemand ansieht.

## Was diese Entscheidung zusätzlich verlangt — und was daraus wurde

Sie deckt einen Fehler auf, der heute folgenlos ist (Spec-Befund **B-1**): `risk.py:82-83`
prüft SELL gegen das **Gesamt**risiko statt gegen die Position im jeweiligen Symbol. Mit einem
echten Ledger lässt diese Prüfung genau den Short durch, den sie verhindern soll. Ein Nadelöhr,
das falsch misst, ist kein Nadelöhr.

**Die Korrektur ist Teil von Teilprojekt A** (F-10 entschieden). `PortfolioState` bekommt
`position_pct_by_symbol` mit Vorgabewert (damit die positional konstruierten Bestandstests
gültig bleiben), die Prüfung nutzt `pf.position_pct_by_symbol.get(p.symbol, 0.0)`.

Kriterium **A-6c** misst sie an drei Punkten, inklusive Gegenprobe, damit es nicht durch
pauschales Ablehnen erfüllt werden kann:

| Portfolio | Vorschlag | erwartet |
|---|---|---|
| 0 % BTC, 20 % ETH | `SELL BTCUSDC` 5 % | `NO_POSITION` |
| 8 % BTC, 20 % ETH | `SELL BTCUSDC` 5 % | **genehmigt** |
| 8 % BTC, 20 % ETH | `SELL BTCUSDC` 12 % | `NO_POSITION` |

Der Bestandstest `test_no_short` bleibt grün. **A-6c ist heute rot** — und soll es sein: Es
benennt echte Schuld im Bestand, statt sie zu beschreiben.

## Alternativen, die verworfen wurden

| Alternative | Warum nicht |
|---|---|
| Prüfung im Ledger selbst | Das Ledger wäre dann nicht mehr rein (E-001) und müsste `Config` kennen. Und es gäbe zwei Orte mit Regeln |
| Prüfung per Decorator / Middleware | Unsichtbarer als ein expliziter Aufruf, und statisch schlechter prüfbar |
| Konvention plus Code-Review | Genau das, was Prime Directive 3 als Attrappe kennt. Eine Regel ohne Messung ist eine Behauptung |

## Kosten bei Irrtum

**Wenn das Nadelöhr zu eng ist:** B oder C können Handlungen nicht ausdrücken, die sinnvoll
wären (etwa gestaffelte Teilkäufe oder ein Positionswechsel in einem Schritt). Die Rücknahme
ist *billig*: Die Regeln in `risk.py` werden erweitert, das Nadelöhr bleibt. Genau dafür ist
es an einer einzigen Stelle.

**Wenn das Nadelöhr leckt** (ein zweiter Aufrufer entsteht, etwa „nur für den Backtest"):
Backtest-Ergebnisse spiegeln dann Regeln wider, die live nicht gelten. C lernt eine Strategie,
die live an der Risk Engine zerschellt — und die Messungen, mit denen man sie vorher bewertet
hat, sind wertlos. Der Schaden ist still und fällt erst auf, wenn jemand die Live-Ablehnungen
zählt. Deshalb ist A-6b ein Wächter und keine Anmerkung.

**Wenn B-1 nicht korrigiert würde:** Das Nadelöhr genehmigte Verkäufe von Positionen, die es
nicht gibt. Das Ledger wehrte sich zwar (`Rejection(NO_POSITION)` in `sizing.py`), aber dann
stünde die eigentliche Sicherung im Ledger statt in der Engine — die Entscheidung wäre faktisch
gekippt, ohne dass jemand sie zurückgenommen hätte.
