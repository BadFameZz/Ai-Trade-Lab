# app/tests/test_backfill.py
from __future__ import annotations

import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from aitra import backfill, db, store
from aitra.backfill import backfill_symbol
from aitra.binance import BinanceClient

BASIS = "https://api.binance.com"


class FakeAntwort:
    """Wortgleich zum Muster aus test_binance.py (Aufgabe 3) — bewusst hier
    erneut ausgeschrieben, kein Test geht ins Netz und keiner verweist auf
    eine andere Testdatei."""

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
    """Ersetzt urllib vollstaendig. antworten: Pfad -> FakeAntwort, Exception
    oder Callable(url) -> FakeAntwort. Ein Callable darf selbst eine Exception
    werfen (fuer zustandsbehaftete Sequenzen, siehe _AntwortSequenz unten)."""

    def __init__(self, antworten: dict) -> None:
        self.antworten = antworten
        self.aufrufe: list[str] = []

    def open(self, req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        self.aufrufe.append(url)
        a = self.antworten[urlsplit(url).path]
        if isinstance(a, BaseException):
            raise a
        if callable(a):
            a = a(url)
        return a


class _AntwortSequenz:
    """Liefert bei jedem Aufruf den naechsten Eintrag, haengt am letzten fest."""

    def __init__(self, eintraege: list) -> None:
        self._eintraege = eintraege
        self._i = 0

    def __call__(self, url):
        e = self._eintraege[min(self._i, len(self._eintraege) - 1)]
        self._i += 1
        if isinstance(e, BaseException):
            raise e
        return e


def _kerze(open_time: int, interval_s: int, close: str = "81287.03") -> list:
    close_time = open_time + interval_s * 1000 - 1
    return [open_time, "81287.00", "81300.00", "81200.00", close, "12.345",
            close_time, "1003000.0", 2500, "6.0", "500000.0", "0"]


def _body(obj) -> bytes:
    import json
    return json.dumps(obj).encode()


def _universum(n: int, interval_s: int, start: int = 0) -> list:
    return [_kerze(start + i * interval_s * 1000, interval_s) for i in range(n)]


def _opener_fuer(universum: list, server_time_ms: int) -> FakeOpener:
    def klines(url):
        qs = parse_qs(urlsplit(url).query)
        start = int(qs.get("startTime", ["0"])[0])
        limit = int(qs.get("limit", ["500"])[0])
        slice_ = [z for z in universum if z[0] >= start][:limit]
        return FakeAntwort(_body(slice_))

    def zeit(url):
        return FakeAntwort(_body({"serverTime": server_time_ms}))

    return FakeOpener({"/api/v3/klines": klines, "/api/v3/time": zeit})


def test_backfill_holt_mehrere_bloecke_und_speichert_sie(tmp_path):
    """1.440 Kerzen (1 Tag bei 1m), block_limit=500 -> 3 Bloecke (500+500+440).

    Pruefflaeche: fetched UND die tatsaechlich in der DB stehenden Zeilen
    werden geprueft, nicht nur der Rueckgabewert - store.upsert_candles ist
    idempotent, ein Bug in der Bloecke-Weiterschaltung wuerde denselben Block
    mehrfach schreiben, ohne dass 'fetched' allein das zeigen wuerde.
    """
    universum = _universum(1440, 60)
    server_time_ms = universum[-1][6] + 1
    opener = _opener_fuer(universum, server_time_ms)
    client = BinanceClient(BASIS, opener=opener)
    conn = db.connect(tmp_path / "a.db")
    db.migrate(conn)

    result = backfill_symbol(client, conn, "BTCUSDC", "1m", days=1,
                              block_limit=500, sleep_fn=lambda s: None)

    assert result.requests == 3, "3 Bloecke erwartet: 500+500+440"
    assert result.fetched == 1440
    assert result.gaps == 0
    # Fixrunde 1, Befund 4: weight_used wird gefuellt, aber bislang von keinem Test
    # gemessen. Der erwartete Wert steht als Zahl da, nicht als Formel, die dieselbe
    # Rechnung wie der geprueften Code parallel nachvollzieht: 1 Aufruf server_time()
    # (Gewicht 1, WEIGHT_TIME in binance.py) + 3 Aufrufe klines() (Gewicht 2 je Aufruf,
    # WEIGHT_KLINES, Spec 2.2) = 1 + 3*2 = 7.
    assert result.weight_used == 7, f"1 (server_time) + 3*2 (drei klines-Aufrufe) = 7, gemessen: {result.weight_used}"
    rows = store.get_candles(conn, "BTCUSDC", "1m", limit=2000)
    assert len(rows) == 1440, f"Pruefflaeche: {len(rows)} von 1440 Kerzen in der DB"
    assert rows[0].open_time == 0 and rows[-1].open_time == 1439 * 60_000


def test_backfill_meldet_luecken_in_der_reihe(tmp_path):
    """Eine kuenstliche Luecke von genau einem fehlenden Intervall bei Index 100."""
    universum = _universum(200, 900)
    del universum[100]  # Luecke: Index 99 und (ehem.) 101 werden Nachbarn
    server_time_ms = universum[-1][6] + 1
    opener = _opener_fuer(universum, server_time_ms)
    client = BinanceClient(BASIS, opener=opener)
    conn = db.connect(tmp_path / "a.db")
    db.migrate(conn)

    result = backfill_symbol(client, conn, "BTCUSDC", "15m", days=3, sleep_fn=lambda s: None)

    assert result.fetched == 199
    assert result.gaps == 1, f"genau eine Luecke erwartet, gemessen: {result.gaps}"
    treffer = conn.execute(
        "SELECT detail FROM events WHERE event = 'CANDLE_GAP'"
    ).fetchall()
    assert len(treffer) == 1, "genau ein CANDLE_GAP-Ereignis erwartet"
    assert "BTCUSDC" in treffer[0]["detail"]


def test_backfill_wiederholt_bei_429_mit_retry_after(tmp_path):
    """Ein 429 mit Retry-After=3 wird einmal wiederholt, nicht durchgereicht."""
    universum = _universum(4, 900)
    server_time_ms = universum[-1][6] + 1
    fehler = urllib.error.HTTPError(f"{BASIS}/api/v3/klines", 429, "Too Many Requests",
                                     {"Retry-After": "3"}, None)
    sequenz = _AntwortSequenz([fehler, FakeAntwort(_body(universum))])
    opener = FakeOpener({
        "/api/v3/klines": sequenz,
        "/api/v3/time": FakeAntwort(_body({"serverTime": server_time_ms})),
    })
    client = BinanceClient(BASIS, opener=opener)
    conn = db.connect(tmp_path / "a.db")
    db.migrate(conn)
    geschlafen: list[float] = []

    result = backfill_symbol(client, conn, "BTCUSDC", "15m", days=1, sleep_fn=geschlafen.append)

    assert result.fetched == 4
    assert geschlafen == [3], f"erwartet genau ein Schlaf von 3s, gemessen: {geschlafen}"


def test_a16b_platzbedarf_pro_kerze(tmp_path):
    """A-16b: 10.000 15m-Kerzen ueber genau den Pfad schreiben, den backfill.py
    operativ benutzt (Bloecke von 1.000 ueber store.upsert_candles), dann
    VACUUM und Dateigroesse messen. Schwelle maschinenunabhaengig: <= 250 B/Zeile.
    Hochrechnung Betriebskonfiguration (76.800 Zeilen, K-5): siehe docs/abnahme/.
    """
    n = 10_000
    universum = _universum(n, 900)
    server_time_ms = universum[-1][6] + 1
    opener = _opener_fuer(universum, server_time_ms)
    client = BinanceClient(BASIS, opener=opener)
    db_path = tmp_path / "a.db"
    conn = db.connect(db_path)
    db.migrate(conn)
    days = (n * 900 * 1000) // 86_400_000 + 1

    result = backfill_symbol(client, conn, "BTCUSDC", "15m", days=days,
                              block_limit=1000, sleep_fn=lambda s: None)
    assert result.fetched == n, f"Pruefflaeche: erwartet {n}, geschrieben {result.fetched}"

    conn.execute("VACUUM")
    conn.close()
    bytes_je_zeile = db_path.stat().st_size / n
    assert bytes_je_zeile <= 250, f"{bytes_je_zeile:.1f} B/Zeile > 250 B/Zeile (A-16b)"


# --- Fixrunde 1 (Befund 1): main() echt aufrufen, nicht nur backfill_symbol() ---

def test_backfill_main_liest_cli_argumente_und_normalisiert_symbol(tmp_path, monkeypatch):
    """Ruft main() echt auf. --symbol (klein geschrieben uebergeben), --interval,
    --days und --db muessen tatsaechlich ankommen - nicht nur ein Rueckgabewert 0.

    days=1 grenzt hier GENAU einen Tag von zwei moeglichen Tagen ab (Blockgrenze):
    kaeme --days nicht an, wuerden entweder 0 oder alle 2.880 Kerzen landen, nie
    genau 1.440. symbol wird klein uebergeben und muss GROSS gespeichert werden -
    Binance-Symbole sind immer Grossbuchstaben (Fixrunde 1, Befund 1).
    """
    n = 2_880  # 2 Tage bei 1m
    universum = _universum(n, 60)
    server_time_ms = universum[-1][6] + 1
    opener = _opener_fuer(universum, server_time_ms)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(backfill, "BinanceClient",
                         lambda base_url: BinanceClient(base_url, opener=opener))
    db_path = tmp_path / "cli.db"

    rc = backfill.main(["--symbol", "bnbusdc", "--interval", "1m", "--days", "1",
                         "--db", str(db_path)])

    assert rc == 0
    conn = db.connect(db_path)
    rows = store.get_candles(conn, "BNBUSDC", "1m", limit=2000)
    assert len(rows) == 1440, f"Pruefflaeche: --days=1 muss genau einen Tag liefern, gemessen: {len(rows)}"
    assert rows[0].open_time == universum[1440][0], "Pruefflaeche: --days bestimmt den Startzeitpunkt"
    assert all(r.symbol == "BNBUSDC" for r in rows), \
        "Pruefflaeche: --symbol bnbusdc muss GROSS gespeichert werden"


# --- Fixrunde 1 (Befund 2): der Binance-Filter (laufende Kerze verwerfen) muss echt greifen ---

def test_backfill_verwirft_laufende_kerze_ueber_den_binance_filter(tmp_path):
    """binance.klines() verwirft jede Kerze mit close_time >= server_time_ms - das ist
    die Design-Begruendung im Modul-Docstring von backfill.py selbst. In den anderen
    Tests liegt server_time_ms exakt eine Millisekunde HINTER der letzten Kerze; der
    Filter wird dort nie ausgeloest, der kurze Schlussblock entsteht nur, weil dem
    simulierten Universum die Daten ausgehen (Fixrunde 1, Befund 2).

    Hier liegt server_time_ms MITTEN in Kerze 5: Kerzen 5..9 laufen noch und muessen
    verworfen werden, bevor sie backfill_symbol() je erreichen.
    """
    n = 10
    interval_s = 900
    universum = _universum(n, interval_s)
    server_time_ms = universum[5][0] + interval_s * 1000 // 2  # mitten in Kerze 5
    opener = _opener_fuer(universum, server_time_ms)
    client = BinanceClient(BASIS, opener=opener)
    conn = db.connect(tmp_path / "a.db")
    db.migrate(conn)

    result = backfill_symbol(client, conn, "BTCUSDC", "15m", days=1, sleep_fn=lambda s: None)

    assert result.fetched == 5, f"Pruefflaeche: nur die 5 abgeschlossenen Kerzen 0..4, gemessen: {result.fetched}"
    assert result.gaps == 0, "ein vom Binance-Filter weggefilterter Rest ist keine Luecke"
    rows = store.get_candles(conn, "BTCUSDC", "15m", limit=50)
    assert len(rows) == 5, "die laufende Kerze darf nicht in der DB landen"
    assert rows[-1].open_time == universum[4][0]


# --- Fixrunde 1 (Befund 3): main() faengt BinanceError ab, backfill_symbol() nicht ---

def test_backfill_main_faengt_http_fehler_ab_und_behaelt_bereits_geschriebene_bloecke(tmp_path, monkeypatch):
    """Ein HTTP-500 mitten im Lauf darf main() nicht als Traceback verlassen:
    Rueckgabewert 1, ein BACKFILL_FAILED-Ereignis im Journal, und der zuvor
    erfolgreich geschriebene erste Block bleibt in der Datenbank - upsert_candles
    ist idempotent, ein erneuter Lauf wuerde fortsetzen, nicht verdoppeln."""
    n = 1000
    universum = _universum(n, 60)
    server_time_ms = universum[-1][6] + 1
    erster_block = universum[:500]
    fehler = urllib.error.HTTPError(f"{BASIS}/api/v3/klines", 500, "Internal Server Error", {}, None)
    sequenz = _AntwortSequenz([FakeAntwort(_body(erster_block)), fehler])
    opener = FakeOpener({
        "/api/v3/klines": sequenz,
        "/api/v3/time": FakeAntwort(_body({"serverTime": server_time_ms})),
    })
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(backfill, "BinanceClient",
                         lambda base_url: BinanceClient(base_url, opener=opener))
    db_path = tmp_path / "cli.db"

    rc = backfill.main(["--symbol", "BTCUSDC", "--interval", "1m", "--days", "1", "--db", str(db_path)])

    assert rc == 1, "Pruefflaeche: BinanceHTTPError(500) darf keinen Traceback nach draussen lassen"
    conn = db.connect(db_path)
    rows = store.get_candles(conn, "BTCUSDC", "1m", limit=2000)
    assert len(rows) == 500, f"Pruefflaeche: der erste Block bleibt erhalten, gemessen: {len(rows)}"
    treffer = conn.execute("SELECT detail FROM events WHERE event = 'BACKFILL_FAILED'").fetchall()
    assert len(treffer) == 1, "genau ein BACKFILL_FAILED-Ereignis erwartet"
    assert "BinanceHTTPError" in treffer[0]["detail"]
    assert "BTCUSDC" in treffer[0]["detail"]


def test_backfill_main_faengt_malformed_ab_und_behaelt_bereits_geschriebene_bloecke(tmp_path, monkeypatch):
    """Wie der HTTP-500-Fall, aber mit einer strukturell kaputten Antwort
    (kein Array) statt eines HTTP-Fehlers - beide Ausnahmepfade muessen abgefangen
    werden, nicht nur einer."""
    n = 1000
    universum = _universum(n, 60)
    server_time_ms = universum[-1][6] + 1
    erster_block = universum[:500]
    sequenz = _AntwortSequenz([FakeAntwort(_body(erster_block)), FakeAntwort(_body({"kein": "array"}))])
    opener = FakeOpener({
        "/api/v3/klines": sequenz,
        "/api/v3/time": FakeAntwort(_body({"serverTime": server_time_ms})),
    })
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(backfill, "BinanceClient",
                         lambda base_url: BinanceClient(base_url, opener=opener))
    db_path = tmp_path / "cli.db"

    rc = backfill.main(["--symbol", "BTCUSDC", "--interval", "1m", "--days", "1", "--db", str(db_path)])

    assert rc == 1, "Pruefflaeche: BinanceMalformed darf keinen Traceback nach draussen lassen"
    conn = db.connect(db_path)
    rows = store.get_candles(conn, "BTCUSDC", "1m", limit=2000)
    assert len(rows) == 500, f"Pruefflaeche: der erste Block bleibt erhalten, gemessen: {len(rows)}"
    treffer = conn.execute("SELECT detail FROM events WHERE event = 'BACKFILL_FAILED'").fetchall()
    assert len(treffer) == 1, "genau ein BACKFILL_FAILED-Ereignis erwartet"
    assert "BinanceMalformed" in treffer[0]["detail"]


# --- Fixrunde 1 (Befund 5): Luecken auch mehrfach und an der Blockgrenze ---

def test_backfill_meldet_mehrere_aufeinanderfolgende_luecken(tmp_path):
    """Drei aufeinanderfolgende fehlende Kerzen zaehlen als drei Luecken, nicht als
    eine - anders als die bestehende Luecken-Pruefung mit genau einer fehlenden
    Kerze (Fixrunde 1, Befund 5)."""
    universum = _universum(200, 900)
    del universum[100:103]  # drei fehlende Intervalle hintereinander
    server_time_ms = universum[-1][6] + 1
    opener = _opener_fuer(universum, server_time_ms)
    client = BinanceClient(BASIS, opener=opener)
    conn = db.connect(tmp_path / "a.db")
    db.migrate(conn)

    result = backfill_symbol(client, conn, "BTCUSDC", "15m", days=3, sleep_fn=lambda s: None)

    assert result.fetched == 197
    assert result.gaps == 3, f"drei aufeinanderfolgende Luecken erwartet, gemessen: {result.gaps}"


def test_backfill_meldet_luecke_an_der_blockgrenze(tmp_path):
    """Eine fehlende Kerze GENAU an der Grenze zwischen zwei Abrufbloecken - der
    wichtigere Fall aus Fixrunde 1/Befund 5: ein Versatz beim Verketten der Bloecke
    (cursor-Fortschaltung) waere hier sichtbar, nicht innerhalb eines einzelnen
    Blocks wie in der bestehenden Luecken-Pruefung."""
    universum = _universum(1000, 60)
    del universum[500]  # Luecke genau an der Blockgrenze bei block_limit=500
    server_time_ms = universum[-1][6] + 1
    opener = _opener_fuer(universum, server_time_ms)
    client = BinanceClient(BASIS, opener=opener)
    conn = db.connect(tmp_path / "a.db")
    db.migrate(conn)

    result = backfill_symbol(client, conn, "BTCUSDC", "1m", days=1,
                              block_limit=500, sleep_fn=lambda s: None)

    assert result.fetched == 999, f"Pruefflaeche: 500 + 499 Kerzen ohne Ueberlappung, gemessen: {result.fetched}"
    assert result.gaps == 1, f"genau eine Luecke an der Blockgrenze erwartet, gemessen: {result.gaps}"
    assert result.requests == 2, "Pruefflaeche: kein Versatz durch die Luecke beim Verketten der Bloecke"
