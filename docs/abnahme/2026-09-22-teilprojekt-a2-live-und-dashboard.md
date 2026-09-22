# Abnahme Teilprojekt A2 — Marktdaten live, Poller, Dashboard, Auslieferung

**Stand:** 2026-09-22 · Branch `feature/teilprojekt-a2`, 27 Commits · Version 0.3.0

Jede Zahl hier ist gemessen, nicht geschätzt. Was nicht gemessen ist, steht als **offen** —
nicht als Haken.

## Testsuite

| | Wert |
|---|---|
| Volle Suite im Container `python:3.12-slim` | **285 passed, 0 failed** (39,66 s) |
| Stand am Ende von A1 | 157 |
| Zuwachs durch A2 | **+128** |
| Vom Orchestrator selbst nachgemessen | ja, bei jeder Aufgabe und jeder Fixrunde |

## F-1 — Ausstattung des Zielcontainers (beantwortet 2026-09-22)

Abgelesen im Proxmox-Webinterface, CT `ai-trade-lab` auf Node `pve`, unprivilegiert:

| | Wert |
|---|---|
| RAM | **2,00 GiB** (2.048 MiB), belegt 177,30 MiB = 8,66 % |
| Bootdisk | **15,58 GiB**, belegt 1,46 GiB = 9,39 % |
| Kerne | 2 (Auslastung 0,04 %) |
| Swap | 512 MiB (belegt 44 KiB) |
| IPv4 | 192.168.178.110 |
| IPv6 | global routbar (`2003:…`) |
| Uptime bei der Messung | 1 d 10:37 |

## A-16b · Platzbedarf pro Kerze — **erfüllt**

| | Wert |
|---|---|
| Gemessen (10.000 Kerzen, frische DB, `VACUUM`) | **174,08 B/Zeile** |
| Schwelle | ≤ 250 B/Zeile |
| Hochrechnung Betriebskonfiguration (2 × 15m × 400 Tage = 76.800 Zeilen) | **13,37 MB** (12,75 MiB) |
| Schwelle | ≤ 19,20 MB |
| 10 % der Container-Disk (jetzt einsetzbar: 15,58 GiB) | 1.673 MB |
| **Unterschritten um Faktor** | **125** |

Die Spec schätzte Faktor 83. Der rechnete mit der Schwelle 19,2 MB statt mit der gemessenen
Größe — gemessen fällt es günstiger aus. Der freie Platz (14,12 GiB) trüge rechnerisch
**1.243 Jahre** Kerzen, und seit V-7 greift zusätzlich die Aufbewahrungsgrenze
(`CANDLE_RETENTION_DAYS` steuerte bis zum Gesamtreview nichts).

## A-10b · Speicher im Dauerbetrieb — **Schwelle gesetzt, Messung offen**

Mit F-1 ist die Schwelle bestimmt: 25 % von 2.048 MiB = **< 512 MiB RSS**. Das ist genau der
Fall, den die Spec vorsah („bei 2.048 MB → < 512 MB").
**Offen:** die Messung braucht 24 h Live-Betrieb mit zwei Symbolen
(`docker stats --no-stream --format '{{.MemUsage}}' ai-trade-lab`) und damit F-3.

## Weiter offen — ausdrücklich keine Haken

| Punkt | Warum offen |
|---|---|
| **F-3 — Egress zu `api.binance.com`** | Nicht gemessen. Der gesamte Livebetrieb hängt daran. |
| **A-10b Messung** | Braucht 24 h Live-Betrieb, also F-3. |
| **A-19a / A-19b Rauchlauf** | Kein Lauf gegen die echte Hardware für 0.3.0. Netz, Uhr und Container sind in allen 285 Tests Attrappen. |
| **L-1 — kein Browsertest** | Bewusst. A-19b prüft Datenlage am Endpunkt und Verdrahtung im HTML, **nicht die Darstellung**. |
| **N-3** | Im Lieferzustand (`MARKET_DATA_ENABLED=false`) verfällt ein schwebender Vorschlag nie — `expire_stale_pending()` hängt allein am Poller. |
| **N-5** | `pollstate.py` (Livepfad) importiert `replay._utc_date` (Zeitrafferpfad). Bewusst: eine Regel, nicht zwei Auslegungen. |
| **V-6 Teil 2** | Aggregat für `max_drawdown_pct` bräuchte `CAST(… AS REAL)` — Geld durch `float`. Abgelehnt. Weg ohne `float` ist beschrieben. |
| K-1, K-3 … K-6 | Benannte Beobachtungen aus dem Gesamtreview. |

## Was der Gesamtreview gefunden hat

Nach acht einzeln geprüften und freigegebenen Aufgaben fand er **drei Blocker**:

- **B-1** Der Poller schaltete sich im Lieferzustand nach 60 s selbst ab. Die alle 15 min
  geholte Serverzeit wurde gecacht, aber jeden Zyklus gegen die aktuelle Uhr gerechnet — der
  gemeldete Uhrversatz wuchs um 1 s pro Sekunde, Schwelle 30 s, Poll-Takt 60 s. Bei synchroner
  Uhr. **Ein Test beschrieb das Symptom wörtlich und deutete es als Artefakt der Testuhr**;
  die daraufhin eingebaute Umgehung (`market_clock_skew_kill_s=100_000`) hat den Blocker
  zugedeckt.
- **B-2** Der Livepfad füllte auf einer Kerze, die **vor** der Entscheidung öffnete (E-006).
- **B-3** Der Kill Switch löste aus, und im selben Zyklus wurde trotzdem gebucht.

Alle drei behoben, jeder mit Rot-Nachweis, jeder vom Reviewer gegen **seine eigenen**
Szenarien nachgestellt. Dazu sieben Punkte „vor Echtgeld" (V-1 bis V-7) und K-2.

**Positiv festgehalten:** Im gesamten Geldweg wurde **keine Stelle** gefunden, an der ein
Betrag als `float` durchrutscht, verloren geht oder sich verdoppelt. `money.CTX` gilt überall,
`TEXT` in SQLite lückenlos, `_journal_fill()` ist eine echte Transaktion mit Rollback.
Die Fehler lagen sämtlich in der Zustandsführung, nicht in der Arithmetik.

## Urteil

**Papierbetrieb: einsatzbereit.** Vor dem Gesamtreview war das nicht so — da war das System im
Auslieferungszustand nach 60 Sekunden tot.

**Echtgeld: noch nicht.** Offen sind vier Punkte, und keiner ist eine Korrektur; jeder ist ein
fehlender Beleg oder Wächter. Der einzige mit echter Unsicherheit ist der Lauf gegen die echte
Hardware.
