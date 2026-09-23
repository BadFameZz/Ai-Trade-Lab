# E-011 — Aitra lauscht nur auf IPv4

- **Datum:** 2026-09-23
- **Status:** entschieden (Orchestrator), auf CT 107 angewendet und gemessen; **noch nicht im Repo**
- **Betrifft:** `app/docker-compose.yml`
- **Spec:** Teilprojekt D, Schritt D0

## Entscheidung

Die veröffentlichte Portfreigabe wird an den IPv4-Platzhalter gebunden:

```yaml
ports:
  - "0.0.0.0:8787:8787"     # vorher: "8787:8787"
```

Ohne Host-Adresse legt Docker **zwei** Listener an — `0.0.0.0:8787` und `[::]:8787`. Der zweite
umfasst jede IPv6-Adresse des Containers, einschließlich der global routbaren SLAAC-Adresse.
Mit gesetzter Host-Adresse entsteht nur noch der IPv4-Listener.

## Begründung

Das Dashboard hat **keinen Login**. Der Admin-Token schützt nur schreibende Aufrufe; `GET /`,
`/api/status`, `/api/market/candles`, `/api/equity-curve`, `/api/events` und `/api/decisions`
antworten ohne jede Prüfung. Gemessen am 2026-09-23 auf CT 107:

| | Vorher | Nachher |
|---|---|---|
| `http://[2003:…:fef3:c482]:8787/api/version` | **HTTP 200 in 0,011 s** | keine Verbindung |
| `http://192.168.178.110:8787/api/status` | HTTP 200 | HTTP 200 (unverändert) |
| Listener laut `ss -ltn` | `0.0.0.0:8787` **und** `[::]:8787` | nur `0.0.0.0:8787` |

Der Aufruf über die globale Adresse ist im Zugriffsprotokoll der App nachweisbar angekommen:

```
172.18.0.1 - - [23/Sep/2026:13:07:39 +0000] "GET /api/version?test=ipv6-global HTTP/1.1" 200 20
```

Vorher stand zwischen dem offenen Internet und einem Handelssystem ohne Login **allein die
Firewall des Routers**. Das ist eine Einstellung, die jemand ändern kann — und deren Zustand
zum Zeitpunkt der Entscheidung **unbekannt** war: der Test vom Mobilfunk aus scheiterte daran,
dass der Anschluss selbst kein IPv6 hat (auch `ipv6.google.com` lud nicht). Ein Dienst, der
nicht auf IPv6 lauscht, ist keine Einstellung, sondern eine Eigenschaft.

`EnableIPv6=false` am Docker-Bridge-Netz ist **keine** Absicherung dagegen — es betrifft nur das
interne Netz, nicht die veröffentlichte Portfreigabe. Das wurde gemessen, nicht angenommen.

## Kosten bei Irrtum

**Wenn die Entscheidung falsch ist** (jemand braucht den Zugriff über IPv6): Das Dashboard ist
aus dem LAN über IPv4 unverändert erreichbar, gemessen. Ein IPv6-only-Gerät im Haushalt käme
nicht mehr heran. Rücknahme ist eine Zeile und ein `docker compose up -d`; eine Sicherung liegt
als `docker-compose.yml.bak-*` im Container. **Kosten: Minuten.**

**Wenn wir sie nicht getroffen hätten** und die Router-Firewall offen ist oder später geöffnet
wird: Kontostand, Positionen, Handelshistorie und die vollständige Kerzenhistorie sind ohne
Zugangsschutz aus dem Internet lesbar. Sobald Teilprojekt D5 echtes Geld anbindet, steht dort
ein System mit Zugriff auf ein Binance-Konto. **Kosten: nicht bezifferbar, nicht rücknehmbar.**

Die Asymmetrie trägt die Entscheidung — nicht die Wahrscheinlichkeit, dass die Firewall offen
ist. Die kennen wir nämlich immer noch nicht.

## Offen, ausdrücklich

- ~~Die Änderung liegt **nur auf CT 107**, nicht im Repo.~~ **Vom Security-Gate am 2026-09-23
  als Blocker H-1 eingestuft** — und der Befund ist schärfer, als diese Zeile ihn beschrieb:
  `build.sh:8-9` packt `docker-compose.yml` **mit ins Auslieferungsbündel**,
  `proxmox/installer.sh:530` entpackt es über das Zielverzeichnis, und `_update_rollback()`
  (`installer.sh:672-680`) stellt ebenfalls den Repo-Stand her. **Jedes `--update` und jeder
  Rollback setzt die Absicherung still zurück** — bei grünem Healthcheck, weil `wait_healthy`
  über `127.0.0.1` prüft und den `[::]`-Listener nie zu sehen bekommt. Der Nutzer merkt nichts.

  Das ist der Fall, den die Firmenregel meint: *Abschalten heißt löschen, nicht umbenennen.*
  Eine Absicherung, die nur auf dem Zielsystem liegt, ist keine Absicherung, sondern eine
  Leitung, die der nächste Lauf wieder anschließt. Behebung läuft: `- "0.0.0.0:8787:8787"` in
  `app/docker-compose.yml`, mit einem Test, der den Listener festnagelt (Rot-Nachweis ist das
  Zurücksetzen auf `"8787:8787"`).
- **`sshd` auf CT 107 lauscht weiterhin auf IPv6** (`*:22`), ebenso Weboberfläche und SSH des
  Proxmox-Hosts. Außerhalb des Auftrags — gemeldet, nicht behoben.
- Der Zustand der Router-Firewall ist **weiterhin ungemessen**. Verlässlich zu klären über
  FritzBox → Internet → Freigaben → IPv6.

Siehe [[E-012-kerzenvertrag-juengste-n-aufsteigend]] für den zweiten Befund desselben Tages.
