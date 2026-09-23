# E-012 — Kerzenvertrag: die jüngsten N, aufsteigend ausgeliefert

- **Datum:** 2026-09-23
- **Status:** entschieden (Orchestrator), Rot-Nachweis erbracht, Umsetzung läuft
- **Betrifft:** `app/aitra/store.py`, `app/aitra/store_run.py`
- **Befund:** B-D1 aus der Spec Teilprojekt D

## Der Befund

`store.get_candles()` sortierte `ORDER BY open_time ASC LIMIT ?` (`store.py:71`) und lieferte
damit die **ältesten** N Zeilen. Vier Aufrufer nahmen `rows[-1]` und meinten damit „die neueste":

| Stelle | Wofür der Wert dient |
|---|---|
| `dashboard.py:121-123` | Bewertungspreis der gesamten Live-Equity |
| `web.py:194,197-198` | `ref_price` und `ts_ms` für `sizing.size_order()` — die **Ordermenge** |
| `web.py:230` | `GET /api/market/candles` |
| `web.py:242` | `GET /api/equity-curve` (über `store_run.py:146-148`, `ts_ms ASC LIMIT ?`) |

Bei `limit=1` ist das Ergebnis die **allererste je gespeicherte** Kerze.

**Gemessen auf CT 107 am 2026-09-23**, nach dem Backfill von 38.399 Kerzen je Symbol:

```
GET /api/market/candles?symbol=BTCUSDC&interval=15m&limit=3
→ 2025-08-19 13:00, 13:15, 13:30 UTC — close 115560.01
Jüngste Kerze tatsächlich: 2026-09-23 12:30 UTC — close 85454.11
```

Rund **35 %** daneben, dreizehn Monate alt.

## Warum es dreizehn Monate unentdeckt blieb

In der Datenbank lag **eine einzige Kerze** — da sind älteste und neueste dieselbe Zeile. Der
Test dazu (`test_api.py:169-176`) legte ebenfalls genau eine Kerze an und prüfte
`assert len(body) == 1`. Eine Prüffläche ohne Reihenfolge kann keine Reihenfolge prüfen. Auch
der Rauchlauf A-19a wäre grün gelaufen.

**Der Backfill vom 2026-09-23 hat den Fehler scharf gemacht**, nicht verursacht.

## Was er angerichtet hätte

Im Rot-Nachweis quantifiziert (1 BTC zum veralteten Preis 115.560,01, Kasse danach 84.439,99):

```
Equity 200000.00000000 statt 169894.10000000 — pnl gemeldet: 0.00000000
```

Ein Buchverlust von **30.105,90 USDC** wird als Null gemeldet. Der Wert speist über
`to_portfolio_state()` die `daily_loss_pct()` — **die Tagesverlustgrenze und damit der Kill
Switch hätten den Verlust nie gesehen.** Das ist die schwerste Folge: nicht eine falsche Anzeige,
sondern eine blinde Sicherung.

Dazu, differentiell gemessen: Ordermenge `0.13824000` statt `0.18695000` — **26 % zu klein**.

Kein realer Schaden entstanden: zum Zeitpunkt des Fundes `positions: []`, `trades_total: 0`,
`live_locked: true`. Gemessen, nicht angenommen.

## Die Entscheidung

Der Vertrag lautet: **„die jüngsten N, aufsteigend ausgeliefert."**

```sql
SELECT * FROM ( <bisherige Abfrage> ORDER BY open_time DESC LIMIT ? ) ORDER BY open_time ASC
```

Analog für `store_run.get_equity_curve()` mit `ts_ms`.

**Warum nicht einfach `DESC`:** Es gibt zwei Nutzungen derselben Funktion. Limit-getrieben
(dashboard, web) heißt „die jüngsten N". Zeitraum-getrieben über `start_ms`/`end_ms` (Replay,
Backfill, Benchmark) heißt „vollständig und chronologisch". Ein reines `DESC` hätte den
Zeitraffer zerstört. Der `test-engineer` hat die DESC-Mutation absichtlich eingebaut, die rote
Ausgabe abgeholt und zurückgebaut — zwei Wächtertests halten das jetzt fest.

Der kanonische Fix wurde vor der Entscheidung testweise gemessen: **298 passed**, kein
bestehender Test bricht. Die Tests sind also nicht überspezifiziert.

## Kosten bei Irrtum

**Wenn der Vertrag falsch gewählt ist** (etwa weil ein Aufrufer doch „die ältesten N" braucht):
Das fiele beim nächsten Replay-Lauf sofort und laut auf — die Wächtertests decken beide
Nutzungen ab. **Kosten: eine Korrekturrunde.**

**Wenn wir ihn nicht korrigiert hätten:** Ab dem ersten Trade wäre jede Live-Equity, jede
Ordermenge und jede Auslösung der Tagesverlustgrenze falsch gewesen — und zwar stillschweigend,
mit grüner Suite. In Teilprojekt D3 handelt ein Agent selbsttätig, in D5 mit echtem Geld.
**Kosten: der gesamte Zweck der Risk Engine.**

## Offen — gemeldet, nicht behoben

1. **`marketdata.ListSource` und `SqliteSource` laufen unter dem neuen Vertrag auseinander.**
   `ListSource.candles()` (`marketdata.py:82-87`) macht `out[:limit]` nach dem `start_ms`-Filter,
   liefert also die ältesten N ab `start_ms`. Der Paritätstest
   (`test_marketdata.py:70-71`) fährt `limit=100` über **5** Kerzen — das Limit bindet nie, der
   Test ist für genau diese Divergenz blind. Entschärft nur dadurch, dass `.candles()` im
   Produktivcode **keinen Aufrufer** hat. Bewusst nicht in dieser Runde angefasst: eine Änderung
   ohne Test wäre genau das, was diese Firma nicht tut.
2. **`/api/risk/check` verspricht Orders, die nie füllen können.** Gemessen: `approved: true,
   status: pending_fill` — beim Auflösen scheitert dieselbe Order an `MIN_NOTIONAL`
   (4,88 USDC unter der 5-USDC-Grenze), weil die Menge am veralteten Preis bemessen wurde
   (E-010 Weg A bemisst beim Auflösen bewusst nicht neu). Der Nutzer sieht erst ein Ja, dann
   stillschweigend `approved = 0`.
3. ~~**Das Dashboard friert nach 5,2 Tagen ein.**~~ **Durch diese Behebung erledigt.**
   `static/index.html:284` holt `?limit=500` und bekommt seit dem neuen Vertrag die **jüngsten**
   500 Punkte statt der ältesten — das Einfrieren tritt nicht mehr auf (gemessen im Review vom
   2026-09-23). Übrig bleibt nur noch, dass die Punkte **nach Index** statt nach `ts_ms`
   aufgetragen werden, die Zeitachse also bei ungleichen Abständen verzerrt. Das gehört zu
   D-Dash, nicht hierher.
4. **`store_run.get_fills()` ist heute korrekt, aber eine latente Falle:** `ORDER BY id ASC`
   **ohne** `LIMIT`, und `fills[-1]["cash_after"]` ist die geführte Kasse
   (`dashboard.py:110`, `poller.py:204`). Wer dort je ein `LIMIT` ergänzt, dreht die Kasse
   still auf den ältesten Stand.
5. **`db.list_rows()` folgt der umgekehrten Konvention** (`ORDER BY id DESC LIMIT ?`,
   `db.py:227`) — für Journallisten richtig, aber die zwei Konventionen stehen unkommentiert
   nebeneinander.

Siehe [[E-011-aitra-lauscht-nur-auf-ipv4]] für den zweiten Befund desselben Tages.
