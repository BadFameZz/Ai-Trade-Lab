# E-005 — Binance Spot, öffentliche Endpunkte, nur lesend

- **Datum:** 2026-09-21 (Fassung 3: Intervall 15m; Fassung 2 hatte 1h)
- **Status:** entschieden (vom Auftraggeber vorgegeben; F-4 und F-9 beantwortet)
- **Betrifft:** `app/aitra/binance.py` (neu), `poller.py`, `marketdata.py`, `config.py`, `README.md`
- **Spec:** Abschnitte 2.2, 2.3 (K-5), 8 und 11.1; Kriterien A-11, A-11b, A-12, A-17, A-17b, A-17c, A-21

## Entscheidung

Marktdaten kommen von den **öffentlichen, unauthentifizierten** REST-Endpunkten von Binance Spot:
`GET /api/v3/klines`, `GET /api/v3/exchangeInfo`, `GET /api/v3/time`. Keine API-Schlüssel, keine
privaten Endpunkte, keine Orderplatzierung — es existiert kein Schlüssel im Projekt, also kann
auch keiner abfließen.

Verfolgt werden **BTCUSDC und ETHUSDC im 15m-Intervall**, Abruf alle 60 s.

## Veraltet-Erkennung: intervallrelativ

Eine feste 300-s-Schwelle würde bei jedem Intervall oberhalb von 1m im Normalbetrieb dauernd
auslösen, weil die neueste geschlossene Kerze regelmäßig fast ein ganzes Intervall alt ist —
bei 15m bis zu 900 s.

```
warn_s = max(MARKET_STALE_WARN_S, ceil(1.5 * interval_s))   # Untergrenze 150
kill_s = max(MARKET_STALE_KILL_S, 3   * interval_s)         # Untergrenze 300
```

| Intervall | `warn_s` | `kill_s` | entspricht |
|---|---|---|---|
| `1m` (60 s) | 150 (Untergrenze greift) | 300 (Untergrenze greift) | 2,5 / 5 Kerzen |
| **`15m` (900 s)** | **1.350** (22,5 min) | **2.700** (45 min) | 1,5 / 3 Kerzen |
| `1h` (3.600 s) | 5.400 (90 min) | 10.800 (3 h) | 1,5 / 3 Kerzen |

| Zustand | Bedingung | Reaktion |
|---|---|---|
| `ok` | Datenalter ≤ `warn_s`, \|Uhrversatz\| ≤ 5 s, Abrufalter ≤ 5·Poll | — |
| `warn` | einer darüber, keiner über der Kill-Schwelle | Health `warn`, HTTP 200, Event `MARKET_DATA_LAGGING` |
| `stale` | Datenalter > `kill_s` **oder** \|Uhrversatz\| > 30 s | **Kill Switch an**, Event `MARKET_DATA_STALE`, Health `stale`, HTTP 503 |

**Das Abrufalter löst bewusst nicht aus**, es bleibt Frühwarnung. Wenn Abrufe scheitern, altern
die Daten ohnehin; ein zweiter Kill-Pfad mit eigener Uhr würde denselben Sachverhalt doppelt
messen und mit dem Backoff kollidieren (ein legitimer Backoff erreicht 600 s).

**Der Uhrversatz gehört dazu** und bleibt absolut (5 s / 30 s) — er hat nichts mit dem
Kerzenintervall zu tun. Ohne ihn ist das Datenalter nur so gut wie die Containeruhr:
Ein LXC mit zehn Minuten Versatz meldet frische Daten als uralt oder uralte als frisch.

**Der Kill Switch löst sich nicht von selbst** (F-9): Freigabe nur mit Admin-Token, wie jede
andere Freigabe auch. Automatisch wieder anzulaufen, weil sich die Datenlage scheinbar erholt
hat, ist genau der Fall, für den es den Kill Switch gibt.

**Im Zeitraffer ist die Prüfung strukturell inert**, nicht abgeschaltet: die `SimClock` liefert
die `close_time` der gerade verarbeiteten Kerze, das Datenalter ist damit immer 0. Kriterium
A-12 misst, dass ein Replay über 35.040 Kerzen aus 2024 null `MARKET_DATA_STALE`-Ereignisse
erzeugt.

## Härtung des einzigen Netzzugangs

`binance.py` ist die einzige Datei mit ausgehendem Netzverkehr:
Host-Allowlist (`api.binance.com`, `data-api.binance.vision`, geprüft auf **exakte Gleichheit
des Hostnamens**, nicht per Präfix — `api.binance.com.evil.example` muss fallen), nur HTTPS mit
Zertifikatsprüfung, **keine** Weiterleitungen (eigener Handler, der jede 3xx zum Fehler macht
— sonst wäre `169.254.169.254` oder `127.0.0.1` ein Umleitungsziel), 10 s Timeouts, höchstens
2 MiB Antwort, gesendet werden ausschließlich `symbol`, `interval`, `limit`, `startTime`,
`endTime`. Antwortkörper landen nie im Log.

## Gemessene Grundlage (2026-09-20, live abgefragt)

`klines` hat Gewicht 2, das IP-Limit liegt bei `REQUEST_WEIGHT 6000/min`. Mit der entschiedenen
Konfiguration — **2 Symbole, Poll alle 60 s** — sind das 2 Aufrufe/min × 2 = 4 Gewicht/min,
plus `time` alle 15 min (Gewicht 1) = 0,07/min. Summe **4,07 Gewicht/min = 0,068 %** des
Budgets. **Vom Kerzenintervall unabhängig** — es bestimmt nur, wie oft ein Abruf etwas Neues
liefert, nicht wie oft abgerufen wird. Überschreitung quittiert Binance mit HTTP 429
(`Retry-After`), anhaltende Verstöße mit 418 und IP-Bann von 2 Minuten bis 3 Tagen — Grund
genug, das Budget zu deckeln (A-17c: ≤ 10/min).

## Alternativen, die verworfen wurden

| Alternative | Warum nicht |
|---|---|
| WebSocket-Streams | Geringere Latenz, aber Verbindungszustand, Reconnect-Logik und eine zweite Veraltet-Semantik. Bei 15m-Kerzen ohne jeden Nutzen |
| Mehrere Börsen als Quelle | Mehr Robustheit, aber mehr Symbol-Specs, abweichende Preise und die Frage, welcher der wahre ist |
| Feste Veraltet-Schwellen, unabhängig vom Intervall | Bei 15m löst der Kill Switch dauernd aus. A-19b misst genau diesen Betriebsfall |
| Kein Kill Switch, nur eine Warnung | Eine Warnung, die niemand liest, ist keine Sicherung. Der Kill Switch ist in v0.2.0 bereits die Antwort auf das Tagesverlustlimit — dieselbe Antwort auf blinde Daten zu geben, ist konsistent |
| Träges Nachladen bei HTTP-Anfragen statt Poller | Ohne Besucher keine Prüfung, also kein Kill Switch. Damit fiele genau diese Entscheidung aus (Spec 13, Ansatz 3) |

## Kosten bei Irrtum

**Falsche Schwellen, zu eng:** Bei einem kurzen Netzhänger geht der Kill Switch an, und weil er
sich nicht selbst löst, steht das Labor, bis jemand mit Token kommt. Rücknahme *billig*:
`MARKET_STALE_KILL_S` ist konfigurierbar (60 … 86400) — allerdings nur als **Untergrenze**;
bei 15m bestimmt das Intervall die Schwelle (2.700 s), nicht der Wert.

**Falsche Schwellen, zu weit:** Das Labor handelt auf Kursen, die 45 Minuten alt sind, und
merkt es nicht. Bei 15m-Kerzen sind das drei verpasste Kerzen — vertretbar, aber es ist eine
Wahl, keine Naturkonstante. Genau deshalb misst A-11 die Schwelle an **sechs** Punkten
(149/299/301 s bei 1m, 1.349/2.699/2.701 s bei 15m) und nicht nur „löst irgendwann aus".

**Die intervallrelative Formel selbst ist die Fehlerquelle, die zählt.** Wird sie entfernt
oder falsch geklammert, ist das Ergebnis nicht „etwas zu früh", sondern ein Kill Switch, der
im Normalbetrieb dauernd feuert und den Betrieb unmöglich macht. Das ist der Rot-Nachweis von
A-11 und A-19b. Dass diese Formel überhaupt nötig ist, fiel erst beim Durchrechnen der
1h-Entscheidung auf — sie wäre sonst still zur schlimmsten Art Fehler geworden: einer, der
sich als „die Marktdaten sind halt oft veraltet" tarnt.

**Binance ändert etwas** (Feldreihenfolge der Kerzen, Rate Limits, Filterwerte, Symbolstatus):
Die 12 Kerzenfelder und die Filterwerte sind in Spec 2.2 mit Abfragedatum festgehalten. Ändert
Binance sie, schlägt die Typprüfung in `binance.py` fehl und es gibt ein
`MARKET_DATA_MALFORMED`-Ereignis statt stiller Falschdaten. Die eingebaute
`SymbolSpec`-Tabelle kann dabei veralten; deshalb steht die verwendete Quelle (`builtin` oder
`binance`) in `runs.params_json`, damit ein alter Replay erklärbar bleibt.

**Der Container hat ab jetzt ausgehenden Netzverkehr.** Bis v0.2.1 hatte er keinen, und das
README behauptet „komplett lokal – ohne Cloud-Dienste" (Zeile 4). Diese Aussage wird mit A
falsch und muss im README korrigiert werden (Spec-Befund B-2, Kriterium A-21). Sie stehen zu
lassen wäre kein Schönheitsfehler, sondern eine unwahre Sicherheitsaussage gegenüber dem Nutzer.
