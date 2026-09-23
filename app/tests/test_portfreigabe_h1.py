# app/tests/test_portfreigabe_h1.py
"""H-1 (Security-Blocker): die Portfreigabe im Repo MUSS an IPv4 gebunden sein.

WARUM DAS KEINE KOSMETIK IST -- bitte vor dem "Aufraeumen" lesen.
=================================================================

`ports: - "8787:8787"` ohne Host-Adresse laesst Docker ZWEI Listener anlegen:
`0.0.0.0:8787` UND `[::]:8787`. Der zweite umfasst jede IPv6-Adresse des
Hosts, einschliesslich der global routbaren SLAAC-Adresse -- also eine
Adresse aus dem oeffentlichen Internet.

Dahinter steht ein Dashboard OHNE LOGIN. Der Admin-Token schuetzt nur
schreibende Aufrufe; `GET /`, `/api/status`, `/api/market/candles`,
`/api/equity-curve`, `/api/events` und `/api/decisions` antworten ungeprueft
-- mit Kontostand, Positionen und vollstaendiger Handelshistorie.

Gemessen auf CT 107 am 2026-09-23 (E-011, nicht geraten):

    http://[2003:...:fef3:c482]:8787/api/version   vorher HTTP 200 in 0,011 s
                                                   nachher keine Verbindung
    http://192.168.178.110:8787/api/status         vorher/nachher HTTP 200
    ss -ltn                                        vorher 0.0.0.0:8787 UND
                                                   [::]:8787, nachher nur IPv4

Der Aufruf ueber die globale Adresse ist im Zugriffsprotokoll der App
angekommen -- die Erreichbarkeit war real, nicht theoretisch.

WARUM EIN TEST UND NICHT NUR EINE ZEILE IN DER DATEI
====================================================

Weil die Absicherung auf der Anlage sonst bei jedem Update still verschwindet:

    build.sh:8-9            packt docker-compose.yml mit ins Bundle
    proxmox/installer.sh    entpackt es ueber das Zielverzeichnis
    _update_rollback()      stellt ebenfalls den Repo-Stand her

Jedes `--update` und jeder Rollback setzt den Repo-Stand durch. Der
Healthcheck merkt nichts davon, weil er ueber `127.0.0.1` prueft und damit
per Bauart nie auf die IPv6-Seite schaut. Der Repo-Stand IST der
ausgelieferte Stand -- nur hier laesst sich die Eigenschaft festnageln.

DIE PRUEFFLAECHE DIESES TESTS
=============================

Ein Test, der ueber eine Liste laeuft, die leer sein kann, prueft nichts. Und
eine Auswahl trifft nicht immer, was sie meint. Deshalb:

* `test_h1_parser_sieht_beide_formen` fuehrt dem Parser beide Faelle vor
  (gebunden/ungebunden, Block- und Flow-Stil). Ein Parser, der immer `[]`
  liefert, faellt dort auf, bevor irgendjemand dem gruenen Haken glaubt.
* Der eigentliche Test zaehlt seine Auswahl (`== 1`), statt nur "vorhanden"
  zu pruefen -- zwei Freigaben auf denselben Containerport waeren genau die
  Verdopplung, die ein blosses "es gibt eine mit 0.0.0.0" durchliesse.
* Jeder Eintrag, den der Parser nicht sicher versteht, laesst den Test
  SCHEITERN statt ihn stillschweigend zu ueberspringen.

WENN DU DIESEN TEST AENDERN WILLST
==================================

`127.0.0.1:8787:8787` waere strenger (nur noch lokal, kein LAN-Zugriff mehr)
und wuerde die IPv6-Seite ebenfalls schliessen -- aber es nimmt dem
Auftraggeber das Dashboard im Haushalt weg. Das ist eine ENTSCHEIDUNG, keine
Formatfrage: sie gehoert in ein neues Dokument unter docs/entscheidungen/
und erst danach in diesen Test. Die Zusicherung ersatzlos zu lockern heisst,
das Handelssystem wieder ans offene Netz zu haengen.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

COMPOSE = Path(__file__).resolve().parent.parent / "docker-compose.yml"
CONTAINER_PORT = "8787"
ERWARTETE_HOST_IP = "0.0.0.0"  # E-011


@dataclass(frozen=True)
class Freigabe:
    """Eine veroeffentlichte Portfreigabe, unabhaengig von der Schreibweise."""
    host_ip: str | None
    host_port: str | None
    container_port: str
    roh: str


# Kurzform:  [HOST_IP:][HOST_PORT:]CONTAINER_PORT[/PROTO]
# IPv6-Host-Adressen stehen in eckigen Klammern ("[::1]:8787:8787").
_KURZFORM = re.compile(
    r"""^
    (?: (?P<ip> \[[0-9A-Fa-f:.]+\] | \d{1,3}(?:\.\d{1,3}){3} ) : )?
    (?: (?P<host> \d+(?:-\d+)? ) : )?
    (?P<ziel> \d+(?:-\d+)? )
    (?: / (?P<proto> [A-Za-z]+ ) )?
    $""",
    re.VERBOSE,
)


def _eintrag(roh) -> Freigabe:
    """Ein einzelner ports-Eintrag -> Freigabe. Wirft bei allem, was nicht
    zweifelsfrei verstanden wird -- lieber laut scheitern als still gruen."""
    if isinstance(roh, dict):  # Langform (nur ueber PyYAML erreichbar)
        ziel = str(roh.get("target", "")).strip()
        assert ziel, f"ports-Langform ohne 'target': {roh!r}"
        hp = roh.get("published")
        return Freigabe(
            host_ip=(str(roh["host_ip"]).strip() if roh.get("host_ip") else None),
            host_port=(str(hp).strip() if hp is not None else None),
            container_port=ziel, roh=str(roh),
        )
    text = str(roh).strip().strip("'\"").strip()
    assert ":" not in text or not re.match(r"^[A-Za-z_]+\s*:", text), (
        f"ports-Eintrag {text!r} sieht nach der Langform (target:/published:/host_ip:) "
        "aus, die dieser Test ohne PyYAML nicht sicher lesen kann. Erweitere "
        "_eintrag(), statt die Zusicherung fallen zu lassen -- sonst bliebe H-1 "
        "unbewacht, waehrend der Test gruen meldet."
    )
    m = _KURZFORM.match(text)
    assert m, (
        f"ports-Eintrag {text!r} in {COMPOSE} ist fuer diesen Test unlesbar. "
        "Der Test scheitert hier bewusst, statt den Eintrag zu ueberspringen: "
        "ein uebersprungener Eintrag koennte genau der ungebundene sein."
    )
    ip = m.group("ip")
    return Freigabe(host_ip=(ip[1:-1] if ip and ip.startswith("[") else ip),
                    host_port=m.group("host"), container_port=m.group("ziel"), roh=text)


def _ports_zeilenweise(text: str) -> list:
    """Rueckfallweg ohne PyYAML -- robust gegen Einzug, Anfuehrungszeichen,
    Kommentare, Leerzeilen und Flow-Stil (`ports: ["0.0.0.0:8787:8787"]`)."""
    zeilen = text.splitlines()
    roh: list[str] = []
    for i, z in enumerate(zeilen):
        kopf = re.match(r"^(\s*)ports:\s*(.*?)\s*$", z)
        if not kopf:
            continue
        einzug, rest = len(kopf.group(1)), re.sub(r"\s+#.*$", "", kopf.group(2)).strip()
        if rest.startswith("["):  # Flow-Stil
            inhalt = rest[1:rest.rindex("]")]
            roh += [s.strip() for s in inhalt.split(",") if s.strip()]
            continue
        assert rest in ("", "|", ">"), f"unerwartetes ports: {rest!r} in {COMPOSE}"
        for w in zeilen[i + 1:]:
            if not w.strip() or w.lstrip().startswith("#"):
                continue
            if len(w) - len(w.lstrip()) <= einzug:
                break
            punkt = re.match(r"^\s*-\s*(.*?)\s*$", w)
            assert punkt, (
                f"Zeile {w!r} unter ports: ist kein Listenpunkt. Dieser Test bricht "
                "hier ab, statt den Block stillschweigend fuer zu Ende zu halten."
            )
            roh.append(re.sub(r"\s+#.*$", "", punkt.group(1)).strip())
    return roh


def freigaben(text: str) -> list[Freigabe]:
    """Alle veroeffentlichten Ports aller Dienste. Nimmt PyYAML, wenn es da
    ist (im Testcontainer ist es das nicht -- gemessen, nicht vermutet), sonst
    den zeilenweisen Weg. Beide Wege liefern dieselbe Freigabe-Liste; genau
    das prueft test_h1_parser_sieht_beide_formen nach."""
    try:
        import yaml  # type: ignore
    except ImportError:
        yaml = None
    if yaml is not None:
        daten = yaml.safe_load(text) or {}
        roh = [p
               for dienst in (daten.get("services") or {}).values()
               for p in ((dienst or {}).get("ports") or [])]
    else:
        roh = _ports_zeilenweise(text)
    return [_eintrag(r) for r in roh]


# --------------------------------------------------------------------------
# Prueffläche fuer die Auswahl selbst: der Parser MUSS den Unterschied sehen
# koennen, den der eigentliche Test zusichert. Ohne das waere ein Parser, der
# immer [] liefert, unauffaellig -- und der Wachposten leer.
# --------------------------------------------------------------------------
_OHNE_IP = 'services:\n  a:\n    image: x\n    ports:\n      - "8787:8787"\n'
_MIT_IP = 'services:\n  a:\n    image: x\n    ports:\n      - "0.0.0.0:8787:8787"\n'
_FLOW = 'services:\n  a:\n    ports: ["0.0.0.0:8787:8787"]\n    image: x\n'
_KOMMENTAR = ('services:\n  a:\n    ports:\n      # Kommentar\n'
              "      - 0.0.0.0:8787:8787   # E-011\n\n    image: x\n")
_IPV6 = 'services:\n  a:\n    image: x\n    ports:\n      - "[::]:8787:8787"\n'


def test_h1_parser_sieht_beide_formen():
    """Erst zaehlen, dann glauben: jede Vorlage muss GENAU EINE Freigabe
    ergeben, und die ungebundene Form muss als ungebunden erkannt werden."""
    for name, quelle, erwartet_ip in [
        ("ohne Host-IP", _OHNE_IP, None),
        ("mit 0.0.0.0", _MIT_IP, "0.0.0.0"),
        ("Flow-Stil", _FLOW, "0.0.0.0"),
        ("mit Kommentar/Leerzeile", _KOMMENTAR, "0.0.0.0"),
        ("IPv6-Bindung", _IPV6, "::"),
    ]:
        gefunden = freigaben(quelle)
        assert len(gefunden) == 1, f"{name}: {len(gefunden)} Freigaben statt 1 -> {gefunden}"
        f = gefunden[0]
        assert f.host_ip == erwartet_ip, f"{name}: host_ip={f.host_ip!r}, erwartet {erwartet_ip!r}"
        assert f.container_port == CONTAINER_PORT, f"{name}: Containerport {f.container_port!r}"


def test_h1_compose_veroeffentlicht_8787_nur_ueber_ipv4():
    """H-1: app/docker-compose.yml muss 8787 an 0.0.0.0 binden (E-011).

    Ohne Host-Adresse entsteht zusaetzlich ein [::]:8787-Listener, und damit
    steht ein Dashboard ohne Login auf einer global routbaren IPv6-Adresse.
    Siehe den Modul-Docstring -- diese Zusicherung ist der einzige Ort, an dem
    `--update` und Rollback die Absicherung nicht wieder abraeumen koennen.
    """
    assert COMPOSE.exists(), f"docker-compose.yml nicht gefunden: {COMPOSE}"
    text = COMPOSE.read_text(encoding="utf-8")

    alle = freigaben(text)
    assert len(alle) >= 1, (
        f"Prueffläche leer: in {COMPOSE} wurde keine einzige Portfreigabe gelesen. "
        "Ein Test ueber eine leere Liste ist immer gruen -- hier ist er deshalb rot. "
        f"Dateiinhalt:\n{text}"
    )

    auf_8787 = [f for f in alle if f.container_port == CONTAINER_PORT]
    assert len(auf_8787) == 1, (
        f"Erwartet genau EINE Freigabe auf Containerport {CONTAINER_PORT}, gefunden "
        f"{len(auf_8787)}: {[f.roh for f in auf_8787]}. Eine zweite Freigabe koennte "
        "ungebunden sein und den [::]-Listener zurueckbringen, waehrend die erste "
        "korrekt aussieht."
    )

    f = auf_8787[0]
    assert f.host_ip == ERWARTETE_HOST_IP, (
        f"SICHERHEITSZUSICHERUNG VERLETZT (H-1 / E-011).\n"
        f"  {COMPOSE}\n"
        f"  gemessen:  ports: - {f.roh!r}   (host_ip={f.host_ip!r})\n"
        f"  erwartet:  ports: - '{ERWARTETE_HOST_IP}:{CONTAINER_PORT}:{CONTAINER_PORT}'\n"
        "\n"
        "Ohne Host-Adresse legt Docker ZWEI Listener an: 0.0.0.0:8787 UND [::]:8787. "
        "Der zweite umfasst die global routbare IPv6-Adresse des Hosts. Dahinter "
        "antwortet ein Dashboard OHNE LOGIN mit Kontostand, Positionen und "
        "Handelshistorie (GET /, /api/status, /api/market/candles, /api/equity-curve, "
        "/api/events, /api/decisions -- alle ungeprueft).\n"
        "Auf CT 107 am 2026-09-23 gemessen: ueber die globale IPv6-Adresse kam "
        "HTTP 200 in 0,011 s zurueck, nachweisbar im Zugriffsprotokoll der App. "
        "Der Healthcheck sieht das nie, weil er ueber 127.0.0.1 prueft.\n"
        "Das ist keine Formatfrage. Siehe docs/entscheidungen/"
        "E-011-aitra-lauscht-nur-auf-ipv4.md."
    )
    assert f.host_port == CONTAINER_PORT, (
        f"Hostport {f.host_port!r} statt {CONTAINER_PORT!r}: {f.roh!r} -- der "
        "Installer und der Healthcheck erwarten 8787."
    )
