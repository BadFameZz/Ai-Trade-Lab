"""Der einzige Netzzugang des Projekts: Binance Spot, oeffentlich und nur lesend.

E-005 und Spec 11.1. Jede Haertung steht hier, weil es keine zweite Stelle gibt,
an der sie stehen koennte:

- Host-Allowlist, verglichen auf EXAKTE Gleichheit des Hostnamens. Ein
  Praefixvergleich liesse https://api.binance.com.evil.example durch (A-17).
- Nur HTTPS; Zertifikatspruefung ueber den System-Truststore, nie abgeschaltet.
- Keine Weiterleitungen: ein eigener Handler macht jede 3xx zum Fehler. Ohne ihn
  waeren 169.254.169.254 und 127.0.0.1 gueltige Umleitungsziele, obwohl die
  Allowlist nur die ERSTE URL prueft (A-17b).
- 10 s Timeout, hoechstens 2 MiB gelesen, dann Abbruch.
- Gesendet werden ausschliesslich symbol, interval, limit, startTime, endTime.
  Kein Token, kein Hostname, keine Kennung. API-Schluessel gibt es im Projekt
  nicht, also kann auch keiner abfliessen.
- Antwortkoerper landen nie in einer Fehlermeldung und nie im Log.

Zahlen aus JSON entstehen ausschliesslich ueber Decimal(str) — nie ueber float
(E-002). Ein float-Feld in der Antwort ist ein Fehler, kein Wert.
"""
from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode, urlsplit

from . import money
from .config import ALLOWED_BINANCE_HOSTS
from .marketdata import INTERVALS, Candle

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
TIMEOUT_S = 10.0
WEIGHT_KLINES = 2          # Spec 2.2, aus der Binance-Doku
WEIGHT_TIME = 1
WEIGHT_EXCHANGE_INFO = 20

_ERLAUBTE_PARAMETER = frozenset({"symbol", "interval", "limit", "startTime", "endTime"})


class BinanceError(RuntimeError):
    """Oberklasse. Traegt nie einen Antwortkoerper und nie eine volle URL."""


class BinanceMalformed(BinanceError):
    """Antwort ist kein JSON, hat die falsche Struktur oder unplausible Werte."""


class BinanceTooLarge(BinanceError):
    """Antwort ueber MAX_RESPONSE_BYTES — Verbindung abgebrochen, Inhalt verworfen."""


class BinanceHTTPError(BinanceError):
    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status


class BinanceRateLimited(BinanceHTTPError):
    """429 oder 418. retry_after_s ist 0, wenn Binance keinen Wert schickt oder
    er negativ/unlesbar ist, und auf 3600 (eine Stunde) gedeckelt, wenn Binance
    einen unplausibel hohen Wert schickt. Die Mindestwartezeit setzt trotzdem
    der Poller (Spec 8.3: mindestens 60 s) — dieser Wert ist nur nach oben
    begrenzt, nicht nach unten erhoeht."""

    def __init__(self, status: int, retry_after_s: int) -> None:
        super().__init__(status)
        self.retry_after_s = retry_after_s


class _KeineWeiterleitung(urllib.request.HTTPRedirectHandler):
    """Macht jede 3xx zum Fehler (A-17b)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BinanceError(f"Weiterleitung {code} wird nicht verfolgt")


def build_opener() -> urllib.request.OpenerDirector:
    """Der Opener des Projekts. build_opener() ersetzt den Standard-Handler,
    weil _KeineWeiterleitung von ihm erbt."""
    return urllib.request.build_opener(_KeineWeiterleitung())


def _pruefe_ziel(url: str) -> None:
    """Nur HTTPS, nur die beiden bekannten Hosts, Hostname exakt verglichen."""
    teile = urlsplit(url)
    if teile.scheme != "https":
        raise BinanceError("Nur HTTPS erlaubt")
    if teile.hostname not in ALLOWED_BINANCE_HOSTS:
        raise BinanceError(f"Host nicht in der Allowlist: {teile.hostname!r}")


def _dec(wert: Any, feld: str) -> Decimal:
    """JSON-Wert zu Decimal. float und bool sind Fehler, keine Werte (E-002).

    Die beiden Teile der ersten Bedingung sind NICHT redundant zur zweiten
    Pruefung, auch wenn beide float ablehnen:
    - `bool` ist in Python eine Unterklasse von `int` (`isinstance(True, int)`
      ist wahr). Ohne die eigene bool-Pruefung hier wuerde die zweite Zeile
      (`isinstance(wert, (str, int))`) `True`/`False` klaglos durchlassen und
      `Decimal(str(True))` wuerde scheitern - mit einer schlechteren
      Fehlermeldung als hier. Fuer bool ist diese Zeile also die EINZIGE
      wirksame Schicht.
    - `float` ist weder `str` noch `int`, faellt also auch ohne diese Zeile
      schon durch die zweite Pruefung. Hier ist sie nur ein praeziserer
      Fehlertext ("float/bool statt Zeichenkette" statt "unerwarteter Typ").
    Beide Zeilen bleiben deshalb stehen - beim naechsten Aufraeumen nicht als
    Duplikat kuerzen (Sicherheits-Review, Fixrunde 1, Aufgabe 3).
    """
    if isinstance(wert, bool) or isinstance(wert, float):
        raise BinanceMalformed(f"Feld {feld}: float/bool statt Zeichenkette")
    if not isinstance(wert, (str, int)):
        raise BinanceMalformed(f"Feld {feld}: unerwarteter Typ {type(wert).__name__}")
    try:
        return Decimal(str(wert))
    except InvalidOperation as e:
        raise BinanceMalformed(f"Feld {feld}: nicht numerisch") from e


def _int(wert: Any, feld: str) -> int:
    if isinstance(wert, bool) or not isinstance(wert, int):
        raise BinanceMalformed(f"Feld {feld}: kein ganzzahliger Wert")
    return wert


def _retry_after(e: urllib.error.HTTPError) -> int:
    """Retry-After plausibilisiert: negativ/fehlend wird 0, ueber einer Stunde
    wird auf 3600 gedeckelt. Ein Server mit Retry-After: 999999999 soll den
    Poller nicht beliebig lange lahmlegen koennen."""
    roh = (e.headers or {}).get("Retry-After", "")
    try:
        return min(3600, max(0, int(str(roh).strip())))
    except (TypeError, ValueError):
        return 0


class BinanceClient:
    """Duenner, gehaerteter Lesezugriff. opener ist einspeisbar, damit kein Test
    ins Netz geht (Spec 11.3)."""

    def __init__(self, base_url: str, *, timeout_s: float = TIMEOUT_S,
                 max_bytes: int = MAX_RESPONSE_BYTES, opener=None) -> None:
        _pruefe_ziel(base_url)
        self._base = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._max_bytes = max_bytes
        self._opener = opener if opener is not None else build_opener()
        self._weight = 0

    @property
    def weight_used(self) -> int:
        """Verbrauchtes Ratengewicht seit dem letzten reset_weight() (A-17c)."""
        return self._weight

    def reset_weight(self) -> None:
        self._weight = 0

    def _hole(self, pfad: str, params: dict, gewicht: int) -> Any:
        unerlaubt = set(params) - _ERLAUBTE_PARAMETER
        if unerlaubt:
            raise BinanceError(f"Unerlaubte Parameter: {sorted(unerlaubt)}")
        url = f"{self._base}{pfad}?{urlencode(params)}" if params else f"{self._base}{pfad}"
        _pruefe_ziel(url)
        req = urllib.request.Request(url, method="GET")
        req.add_header("Accept", "application/json")
        self._weight += gewicht
        try:
            with self._opener.open(req, timeout=self._timeout_s) as resp:
                roh = resp.read(self._max_bytes + 1)
        except urllib.error.HTTPError as e:
            if e.code in (418, 429):
                raise BinanceRateLimited(e.code, _retry_after(e)) from None
            raise BinanceHTTPError(e.code) from None
        except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
            # URLError wrappt nur den Verbindungsaufbau. Alles danach - das
            # Kopfzeilen-Parsen durch http.client und resp.read() selbst -
            # laeuft daran vorbei: ein TCP-Reset (ConnectionResetError), ein
            # Haenger nach den Kopfzeilen (TimeoutError) oder eine Antwort mit
            # zu vielen Kopfzeilen (http.client.HTTPException) sind OSError-
            # bzw. HTTPException-Faelle, keine URLError-Faelle. Ohne diesen
            # erweiterten Fangzweig risse die rohe Ausnahme bis zum Poller
            # durch und naehme den Thread mit der Veraltet-Erkennung mit
            # (Fixrunde 1, Befund 1). HTTPError steht als Unterklasse von
            # URLError bereits im Zweig darueber und wird dort abgefangen;
            # URLError selbst ist wiederum eine OSError-Unterklasse, das
            # Ueberschneiden im selben Tupel ist unschaedlich.
            grund = type(e.reason).__name__ if isinstance(e, urllib.error.URLError) else type(e).__name__
            raise BinanceError(f"Verbindungsfehler: {grund}") from None
        if len(roh) > self._max_bytes:
            raise BinanceTooLarge(f"Antwort über {self._max_bytes} Bytes – verworfen")
        try:
            return json.loads(roh)
        except (ValueError, UnicodeDecodeError) as e:
            # Bewusst ohne roh: der Koerper darf nie in eine Meldung geraten.
            raise BinanceMalformed(f"Antwort ist kein JSON ({type(e).__name__})") from None

    def server_time(self) -> int:
        """Binance-Serverzeit in ms. Grundlage des Uhrversatzes (Spec 8.1)."""
        daten = self._hole("/api/v3/time", {}, WEIGHT_TIME)
        if not isinstance(daten, dict):
            raise BinanceMalformed("time: Antwort ist kein Objekt")
        return _int(daten.get("serverTime"), "serverTime")

    def klines(self, symbol: str, interval: str, *, server_time_ms: int,
                limit: int = 500, start_ms: int | None = None,
                end_ms: int | None = None) -> list[Candle]:
        """Ausschliesslich ABGESCHLOSSENE Kerzen.

        Binance liefert die laufende Kerze mit. Sie traegt einen vorlaeufigen
        close, der beim naechsten Abruf ein anderer ist — ein Fill auf ihr waere
        ein Fill auf einem Preis, den es so nie gab. Verworfen wird anhand
        close_time < server_time_ms, also gegen die SERVERZEIT und nicht gegen
        die Containeruhr: ein LXC mit Versatz reichte sonst offene Kerzen als
        geschlossen durch (Spec 8.1).
        """
        if interval not in INTERVALS:
            raise BinanceError(f"Unbekanntes Intervall {interval!r}")
        params: dict = {"symbol": symbol, "interval": interval,
                         "limit": min(max(int(limit), 1), 1000)}
        if start_ms is not None:
            params["startTime"] = int(start_ms)
        if end_ms is not None:
            params["endTime"] = int(end_ms)

        daten = self._hole("/api/v3/klines", params, WEIGHT_KLINES)
        if not isinstance(daten, list):
            raise BinanceMalformed("klines: Antwort ist kein Array")

        kerzen: list[Candle] = []
        for zeile in daten:
            if not isinstance(zeile, list) or len(zeile) < 12:
                raise BinanceMalformed("klines: Zeile hat nicht die 12 Felder aus Spec 2.2")
            open_time = _int(zeile[0], "openTime")
            close_time = _int(zeile[6], "closeTime")
            o, h, t, c = (_dec(zeile[1], "open"), _dec(zeile[2], "high"),
                           _dec(zeile[3], "low"), _dec(zeile[4], "close"))
            v = _dec(zeile[5], "volume")
            if h < t or t <= 0 or c <= 0 or o <= 0:
                raise BinanceMalformed(
                    "klines: unplausible Kerze (high<low, high/low<=0 oder Preis<=0)")
            if close_time >= server_time_ms:
                continue  # laufende Kerze — nie weitergeben
            kerzen.append(Candle(symbol=symbol, interval=interval, open_time=open_time,
                                  close_time=close_time, open=o, high=h, low=t, close=c,
                                  volume=v, closed=True))
        return kerzen

    def exchange_info(self, symbol: str) -> money.SymbolSpec:
        """Losgroessen eines Symbols, frisch von Binance.

        Ein Symbol je Aufruf: Spec 11.1 erlaubt als Parameter ausschliesslich
        symbol, interval, limit, startTime, endTime. Der Sammelparameter
        'symbols' steht nicht darauf, und eine Ausnahme fuer Bequemlichkeit
        waere die erste Bresche in einer Liste, die genau deshalb kurz ist.
        """
        daten = self._hole("/api/v3/exchangeInfo", {"symbol": symbol}, WEIGHT_EXCHANGE_INFO)
        if not isinstance(daten, dict) or not isinstance(daten.get("symbols"), list) \
                or not daten["symbols"]:
            raise BinanceMalformed("exchangeInfo: kein symbols-Array")
        s = daten["symbols"][0]
        if s.get("status") != "TRADING":
            raise BinanceMalformed(f"exchangeInfo: {symbol} steht nicht auf TRADING")
        filt = {f.get("filterType"): f for f in s.get("filters", []) if isinstance(f, dict)}
        try:
            preis, lot = filt["PRICE_FILTER"], filt["LOT_SIZE"]
            notional = filt.get("NOTIONAL") or filt["MIN_NOTIONAL"]
        except KeyError as e:
            raise BinanceMalformed(f"exchangeInfo: Filter {e} fehlt") from e
        return money.SymbolSpec(
            symbol=str(s["symbol"]), base=str(s["baseAsset"]), quote=str(s["quoteAsset"]),
            tick_size=_dec(preis["tickSize"], "tickSize"),
            step_size=_dec(lot["stepSize"], "stepSize"),
            min_qty=_dec(lot["minQty"], "minQty"),
            min_notional=_dec(notional["minNotional"], "minNotional"),
            base_precision=_int(s["baseAssetPrecision"], "baseAssetPrecision"),
            quote_precision=_int(s["quoteAssetPrecision"], "quoteAssetPrecision"),
        )
