# app/tests/test_binance.py
from __future__ import annotations

import json
import socket
import subprocess
import threading
import urllib.error
import urllib.request
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from aitra import binance, money
from aitra.binance import (BinanceClient, BinanceError, BinanceMalformed,
                            BinanceRateLimited, BinanceTooLarge)

BASIS = "https://api.binance.com"


class FakeAntwort:
    """Antwortobjekt der Attrappe - genau so viel, wie binance.py benutzt."""

    def __init__(self, body: bytes, status: int = 200, headers: dict | None = None) -> None:
        self._body = body
        self._pos = 0
        self.status = status
        self.headers = headers or {}

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            d = self._body[self._pos:]
            self._pos = len(self._body)
            return d
        d = self._body[self._pos:self._pos + n]
        self._pos += len(d)
        return d

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


class FakeOpener:
    """Ersetzt urllib vollstaendig. KEIN Test in dieser Datei geht ins Netz.

    antworten: Pfad -> FakeAntwort, Exception oder Callable(url) -> FakeAntwort.
    """

    def __init__(self, antworten: dict) -> None:
        self.antworten = antworten
        self.aufrufe: list[str] = []
        self.requests: list[urllib.request.Request] = []
        self.timeout = None

    def open(self, req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        self.aufrufe.append(url)
        self.requests.append(req)
        self.timeout = timeout
        a = self.antworten[urlsplit(url).path]
        if isinstance(a, BaseException):
            raise a
        if callable(a):
            a = a(url)
        return a


def _kerze(open_time: int, close_time: int, close: str = "81287.03") -> list:
    """Eine Binance-Kerzenzeile: 12 Felder in der Reihenfolge aus Spec 2.2."""
    return [open_time, "81287.00", "81300.00", "81200.00", close, "12.345",
            close_time, "1003000.0", 2500, "6.0", "500000.0", "0"]


def _body(obj) -> bytes:
    return json.dumps(obj).encode()


def _client(antworten: dict, **kw) -> tuple[BinanceClient, FakeOpener]:
    opener = FakeOpener(antworten)
    return BinanceClient(BASIS, opener=opener, **kw), opener


# ---------------------------------------------------------------- Allowlist

@pytest.mark.parametrize("url,erlaubt", [
    ("https://api.binance.com", True),
    ("https://data-api.binance.vision", True),
    ("http://api.binance.com", False),              # kein TLS
    ("https://api.binance.com.evil.example", False),  # Praefix-Falle
    ("https://evil.example.com", False),
    ("http://169.254.169.254", False),              # Cloud-Metadaten
])
def test_a17_allowlist_wird_exakt_verglichen(url, erlaubt):
    """A-17, zweite Schicht: dieselbe Pruefung wie in config.load(), aber im Client.

    Zweite Zusicherung: die Pruefung greift VOR jedem Verbindungsversuch. Ohne
    sie waere eine Umsetzung gruen, die erst nach dem Verbindungsaufbau prueft -
    der Port waere dann bereits angefasst.
    """
    opener = FakeOpener({})
    if erlaubt:
        BinanceClient(url, opener=opener)
    else:
        with pytest.raises(BinanceError):
            BinanceClient(url, opener=opener)
    assert opener.aufrufe == []


def test_allowlist_greift_auch_pro_anfrage_nicht_nur_beim_bau():
    """Eine Allowlist, die nur im Konstruktor prueft, waere zu umgehen, indem
    jemand _base nachtraeglich setzt. Die Pruefung liegt deshalb auch im
    Anfragepfad."""
    c, opener = _client({"/api/v3/time": FakeAntwort(_body({"serverTime": 1}))})
    c._base = "https://evil.example.com"
    with pytest.raises(BinanceError):
        c.server_time()
    assert opener.aufrufe == []


# ------------------------------------------------------- A-17b Weiterleitung

class _Umleiter(BaseHTTPRequestHandler):
    treffer = {"ziel": 0}

    def do_GET(self):
        if self.path == "/start":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/ziel")
            self.end_headers()
        else:
            _Umleiter.treffer["ziel"] += 1
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


@pytest.fixture
def umleitungsserver():
    _Umleiter.treffer["ziel"] = 0
    srv = HTTPServer(("127.0.0.1", 0), _Umleiter)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def test_a17b_keine_weiterleitung_wird_verfolgt(umleitungsserver):
    """A-17b: der Opener aus binance.py folgt keiner 3xx.

    Gemessen wird der ZAEHLER des Umleitungsziels, nicht nur die Ausnahme. Eine
    Ausnahme allein bewiese nicht, dass die zweite Anfrage unterblieben ist.

    Der Testserver spricht http auf 127.0.0.1 - das ist Loopback, kein Netz, und
    KEIN Widerspruch zur Allowlist: geprueft wird hier die Umleitungsschicht.
    Ueber die oeffentliche Client-Schnittstelle ist dieser Fall gar nicht
    erreichbar, weil die Allowlist vorher greift (siehe Test darueber).
    """
    start = f"http://127.0.0.1:{umleitungsserver.server_port}/start"
    opener = binance.build_opener()
    with pytest.raises(BinanceError):
        opener.open(start, timeout=5)
    assert _Umleiter.treffer["ziel"] == 0

    # Gegenprobe: mit dem Standardhandler WIRD die Umleitung verfolgt. Ohne sie
    # waere der Test auch dann gruen, wenn der Server gar nicht umleitete.
    urllib.request.build_opener().open(start, timeout=5).read()
    assert _Umleiter.treffer["ziel"] == 1


# ------------------------------------------------------------ Groessenlimit

def test_groessenlimit_greift_beidseitig():
    """Exakt an der Grenze noch gueltig, ein Byte darueber verworfen.

    Die Fuellung ist Leerraum am Ende des JSON - gueltiges JSON, aber sie
    verschiebt die Laenge um genau ein Byte.
    """
    kern = _body([_kerze(0, 899_999)])
    grenze = len(kern) + 50
    genau = kern + b" " * (grenze - len(kern))
    zuviel = kern + b" " * (grenze - len(kern) + 1)
    assert len(genau) == grenze and len(zuviel) == grenze + 1

    c, _ = _client({"/api/v3/klines": FakeAntwort(genau)}, max_bytes=grenze)
    assert len(c.klines("BTCUSDC", "15m", server_time_ms=900_000)) == 1

    c, _ = _client({"/api/v3/klines": FakeAntwort(zuviel)}, max_bytes=grenze)
    with pytest.raises(BinanceTooLarge):
        c.klines("BTCUSDC", "15m", server_time_ms=900_000)


def test_zwei_mebibyte_ist_die_vorgabe():
    assert binance.MAX_RESPONSE_BYTES == 2 * 1024 * 1024


# --------------------------------------------------- laufende Kerze verwerfen

def test_offene_kerze_wird_verworfen_und_nie_weitergegeben():
    """Beidseitig an der Grenze: close_time == server_time gilt als offen,
    close_time == server_time - 1 als geschlossen.

    Die laufende Kerze traegt einen vorlaeufigen close. Ein Fill auf ihr waere
    ein Fill auf einem Preis, den es so nie gab.
    """
    st = 1_800_000
    zeilen = [_kerze(0, st - 1, "81287.03"), _kerze(900_000, st, "99999.00")]
    c, _ = _client({"/api/v3/klines": FakeAntwort(_body(zeilen))})
    kerzen = c.klines("BTCUSDC", "15m", server_time_ms=st)

    assert len(kerzen) == 1
    assert kerzen[0].close_time == st - 1
    assert kerzen[0].close == Decimal("81287.03")
    assert all(k.closed for k in kerzen)
    assert Decimal("99999.00") not in [k.close for k in kerzen]


# -------------------------------------------------------- kaputte Antworten

@pytest.mark.parametrize("roh,grund", [
    (b"kein json", "kein JSON"),
    (_body({"code": -1121, "msg": "Invalid symbol."}), "Objekt statt Array"),
    (_body([[0, "1", "2"]]), "zu wenige Felder"),
    (_body([[0, "81287.00", "81200.00", "81300.00", "81250.00", "1", 899_999,
             "0", 1, "0", "0", "0"]]), "high < low"),
    (_body([[0, "81287.00", "81300.00", "81200.00", "0", "1", 899_999,
             "0", 1, "0", "0", "0"]]), "close <= 0"),
    (_body([[0, 81287.0, "81300.00", "81200.00", "81250.00", "1", 899_999,
             "0", 1, "0", "0", "0"]]), "float statt Zeichenkette"),
    (_body([[0, "abc", "81300.00", "81200.00", "81250.00", "1", 899_999,
             "0", 1, "0", "0", "0"]]), "nicht numerisch"),
    (_body([[0, "81287.00", "81300.00", "81200.00", "81250.00", "1", "899999",
             "0", 1, "0", "0", "0"]]), "closeTime als Zeichenkette"),
])
def test_kaputte_antworten_werfen_malformed(roh, grund):
    c, _ = _client({"/api/v3/klines": FakeAntwort(roh)})
    with pytest.raises(BinanceMalformed):
        c.klines("BTCUSDC", "15m", server_time_ms=900_000)


def test_eine_gueltige_antwort_wirft_nicht():
    """Gegenprobe zur Parametrisierung darueber: ein Waechter, der ALLES ablehnt,
    waere dort ebenfalls gruen."""
    c, _ = _client({"/api/v3/klines": FakeAntwort(_body([_kerze(0, 899_999)]))})
    assert len(c.klines("BTCUSDC", "15m", server_time_ms=900_000)) == 1


def test_fehlermeldungen_tragen_nie_den_antwortkoerper():
    """Spec 11.1: Antwortkoerper landen nie im Log. Der Ausnahmetext ist die
    Stelle, an der ein Koerper am leichtesten dorthin durchrutscht."""
    geheim = b'{"leak":"ADMIN-TOKEN-abcdef0123456789"}'
    c, _ = _client({"/api/v3/klines": FakeAntwort(geheim)})
    with pytest.raises(BinanceMalformed) as e:
        c.klines("BTCUSDC", "15m", server_time_ms=900_000)
    assert "ADMIN-TOKEN" not in str(e.value)
    assert "leak" not in str(e.value)


# -------------------------------------------------------------- Ratenlimits

@pytest.mark.parametrize("status", [429, 418])
def test_429_und_418_werden_zu_rate_limited(status):
    fehler = urllib.error.HTTPError(
        f"{BASIS}/api/v3/klines", status, "Too Many Requests", {"Retry-After": "7"}, None)
    c, _ = _client({"/api/v3/klines": fehler})
    with pytest.raises(BinanceRateLimited) as e:
        c.klines("BTCUSDC", "15m", server_time_ms=900_000)
    assert e.value.status == status
    assert e.value.retry_after_s == 7


@pytest.mark.parametrize("kopf", [{}, {"Retry-After": "bald"}, {"Retry-After": "-5"}])
def test_retry_after_fehlt_oder_ist_unsinn(kopf):
    """Kein Retry-After heisst 0 - der Poller macht daraus seine Mindestwartezeit
    von 60 s (Spec 8.3). Ein negativer Wert darf nicht zu 'sofort nochmal' werden."""
    fehler = urllib.error.HTTPError(f"{BASIS}/api/v3/klines", 429, "x", kopf, None)
    c, _ = _client({"/api/v3/klines": fehler})
    with pytest.raises(BinanceRateLimited) as e:
        c.klines("BTCUSDC", "15m", server_time_ms=900_000)
    assert e.value.retry_after_s == 0


def test_andere_http_fehler_bleiben_unterscheidbar():
    fehler = urllib.error.HTTPError(f"{BASIS}/api/v3/klines", 503, "x", {}, None)
    c, _ = _client({"/api/v3/klines": fehler})
    with pytest.raises(binance.BinanceHTTPError) as e:
        c.klines("BTCUSDC", "15m", server_time_ms=900_000)
    assert e.value.status == 503
    assert not isinstance(e.value, BinanceRateLimited)


# ------------------------------------------------- Gewicht, Parameter, Kopf

def test_gewichtszaehler_zaehlt_je_aufruf():
    """Grundlage fuer A-17c. Der Zaehler sitzt im Client, weil nur er weiss,
    welcher Endpunkt welches Gewicht kostet."""
    c, _ = _client({
        "/api/v3/klines": lambda url: FakeAntwort(_body([_kerze(0, 899_999)])),
        "/api/v3/time": lambda url: FakeAntwort(_body({"serverTime": 1_789_933_036_347})),
    })
    assert c.weight_used == 0
    c.server_time()
    assert c.weight_used == binance.WEIGHT_TIME == 1
    c.klines("BTCUSDC", "15m", server_time_ms=900_000)
    assert c.weight_used == 1 + binance.WEIGHT_KLINES == 3
    c.reset_weight()
    assert c.weight_used == 0


def test_gesendet_werden_nur_die_fuenf_erlaubten_parameter():
    """Spec 11.1: symbol, interval, limit, startTime, endTime - mehr nicht."""
    c, opener = _client({"/api/v3/klines": FakeAntwort(_body([_kerze(0, 899_999)]))})
    c.klines("BTCUSDC", "15m", limit=2, start_ms=0, end_ms=899_999, server_time_ms=900_000)
    assert len(opener.aufrufe) == 1
    schluessel = set(parse_qs(urlsplit(opener.aufrufe[0]).query))
    assert schluessel == {"symbol", "interval", "limit", "startTime", "endTime"}


def test_keine_kennung_in_den_kopfzeilen():
    """Spec 11.1: kein Token, kein Hostname, keine Kennung. Es gibt keine
    API-Schluessel im Projekt - aber der Hostname reicht schon, um einen
    Heimanschluss wiederzuerkennen."""
    c, opener = _client({"/api/v3/time": FakeAntwort(_body({"serverTime": 1}))})
    c.server_time()
    werte = " ".join(f"{k}: {v}" for k, v in opener.requests[0].header_items()).lower()
    assert socket.gethostname().lower() not in werte
    assert "aitra" not in werte
    assert "token" not in werte
    assert "authorization" not in werte
    assert "apikey" not in werte


def test_timeout_wird_an_urllib_durchgereicht():
    c, opener = _client({"/api/v3/time": FakeAntwort(_body({"serverTime": 1}))})
    c.server_time()
    assert opener.timeout == binance.TIMEOUT_S == 10.0


def test_unbekanntes_intervall_wird_abgelehnt_ohne_anfrage():
    c, opener = _client({"/api/v3/klines": FakeAntwort(b"[]")})
    with pytest.raises(BinanceError):
        c.klines("BTCUSDC", "7m", server_time_ms=900_000)
    assert opener.aufrufe == []


# ------------------------------------------------------------ exchangeInfo

def _exchange_info_body(symbol="BNBUSDC", status="TRADING", filter_weglassen=None) -> bytes:
    filters = [
        {"filterType": "PRICE_FILTER", "tickSize": "0.01000000"},
        {"filterType": "LOT_SIZE", "stepSize": "0.00100000", "minQty": "0.00100000"},
        {"filterType": "NOTIONAL", "minNotional": "5.00000000"},
    ]
    if filter_weglassen:
        filters = [f for f in filters if f["filterType"] != filter_weglassen]
    return _body({"symbols": [{
        "symbol": symbol, "status": status, "baseAsset": "BNB", "quoteAsset": "USDC",
        "baseAssetPrecision": 8, "quoteAssetPrecision": 8, "filters": filters,
    }]})


def test_exchange_info_liefert_eine_symbolspec_die_zur_eingebauten_passt():
    """Die eingebaute Tabelle und die abgefragte Wahrheit muessen uebereinstimmen.

    Weichen sie ab, bemisst der Replay (builtin) anders als der Livebetrieb
    (binance) - und A-8 faellt, ohne dass irgendwo ein Fehler steht.
    """
    c, _ = _client({"/api/v3/exchangeInfo": FakeAntwort(_exchange_info_body())})
    spec = c.exchange_info("BNBUSDC")
    assert isinstance(spec, money.SymbolSpec)
    assert spec.tick_size == Decimal("0.01")
    assert spec.step_size == Decimal("0.001")
    assert spec.min_qty == Decimal("0.001")
    assert spec.min_notional == Decimal("5")
    assert spec == money.BUILTIN_SPECS["BNBUSDC"]


@pytest.mark.parametrize("kwargs", [
    {"status": "BREAK"},
    {"filter_weglassen": "LOT_SIZE"},
    {"filter_weglassen": "PRICE_FILTER"},
    {"filter_weglassen": "NOTIONAL"},
])
def test_exchange_info_lehnt_unbrauchbare_antworten_ab(kwargs):
    c, _ = _client({"/api/v3/exchangeInfo": FakeAntwort(_exchange_info_body(**kwargs))})
    with pytest.raises(BinanceMalformed):
        c.exchange_info("BNBUSDC")


# ------------------------------------------- zweite Schicht: wer darf ins Netz

def test_nur_binance_py_benutzt_urllib_request():
    """Zweite Schicht. Ein Waechter, der nur den Client prueft, saehe einen
    zweiten Netzzugang in poller.py oder web.py ueberhaupt nicht.

    urllib.parse ist erlaubt (config.py benutzt urlsplit) - gesucht wird nur,
    was tatsaechlich eine Verbindung aufbauen kann.
    """
    aitra = Path(__file__).resolve().parent.parent / "aitra"
    treffer = subprocess.run(
        ["grep", "-rlnE", "--include=*.py",
         r"urllib\.request|urllib\.error|http\.client|socket\.socket|import requests",
         str(aitra)],
        capture_output=True, text=True).stdout.splitlines()
    assert any(Path(z).name == "binance.py" for z in treffer), (
        "Pruefflaeche leer: der grep findet nicht einmal binance.py - Muster pruefen"
    )
    andere = [z for z in treffer if Path(z).name != "binance.py"]
    assert andere == [], f"Netzzugriff ausserhalb von binance.py: {andere}"
