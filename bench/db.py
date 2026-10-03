"""Schemat i dostęp do bazy SQLite (plik `bench.sqlite` w katalogu projektu lub BENCH_DB)."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

DB_PATH = os.environ.get("BENCH_DB", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bench.sqlite"))
_lock = threading.RLock()

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS krzyzowki (
  id TEXT PRIMARY KEY,                 -- nazwa pliku bez rozszerzenia
  plik TEXT, zrodlo TEXT, numer_wydania TEXT,
  wiersze INT, kolumny INT,
  katalog_wyniku TEXT, zaimportowano_at REAL,
  liczba_ramek INT DEFAULT 0, liczba_hasel INT DEFAULT 0,
  pasek_pol INT, zagadka INT DEFAULT 0, ostrzezenia TEXT,
  uwagi TEXT
);

CREATE TABLE IF NOT EXISTS ramki (                -- ramki opisów (kratki z tekstem)
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  krzyzowka_id TEXT REFERENCES krzyzowki(id) ON DELETE CASCADE,
  nr INT,                                        -- id ramki w wyniku ekstrakcji
  plik_wycinka TEXT, komorki TEXT, prostokat TEXT,
  tekst TEXT,                                    -- treść po OCR (po korekcie)
  tekst_ocr TEXT, ocr_model TEXT, ocr_status TEXT DEFAULT 'brak', ocr_surowe TEXT, ocr_at REAL,
  zweryfikowano INT DEFAULT 0,
  UNIQUE(krzyzowka_id, nr)
);

CREATE TABLE IF NOT EXISTS hasla (                -- jednostka benchmarku
  id TEXT PRIMARY KEY,                           -- krzyzowka_id/Hn
  krzyzowka_id TEXT REFERENCES krzyzowki(id) ON DELETE CASCADE,
  nr INT, ramka_id INTEGER REFERENCES ramki(id),
  opis TEXT,                                     -- treść opisu (kopia z ramki po OCR / ręczna)
  odpowiedz TEXT,                                -- klucz (wielkie litery, z polskimi znakami)
  dlugosc INT, kierunek TEXT, start_w INT, start_k INT, litery TEXT,
  kategoria TEXT, polskosc TEXT, typ_opisu TEXT,  -- etykiety
  zrodlo_odpowiedzi TEXT,                        -- 'klucz', 'reczna', 'probka_reczna'
  gotowe INT DEFAULT 0,                          -- 1 = opis i odpowiedź zweryfikowane, do użycia w benchmarku
  uwagi TEXT
);

CREATE TABLE IF NOT EXISTS modele (
  id TEXT PRIMARY KEY,                           -- dostawca:nazwa
  dostawca TEXT, nazwa TEXT, etykieta TEXT,
  parametry_mld REAL, darmowy INT DEFAULT 0, aktywny INT DEFAULT 1,
  cena_wej REAL, cena_wyj REAL, kontekst INT, uwagi TEXT, dodano_at REAL
);

CREATE TABLE IF NOT EXISTS uruchomienia (        -- kolejka
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  nazwa TEXT, model_id TEXT REFERENCES modele(id),
  wariant TEXT,                                  -- bez_dlugosci | z_dlugoscia | wzorzec25 | wzorzec50 | wybor5
  podzbior TEXT,                                 -- JSON: filtr haseł (krzyżówki, kategorie, limit, ziarno)
  parametry TEXT,                                -- JSON: temperatura, opoznienie_s, rownoleglosc, przyklady
  status TEXT DEFAULT 'w_kolejce',               -- w_kolejce | trwa | wstrzymane | zakonczone | blad
  priorytet INT DEFAULT 100,
  utworzono_at REAL, start_at REAL, koniec_at REAL,
  liczba_hasel INT DEFAULT 0, zrobione INT DEFAULT 0, bledy INT DEFAULT 0,
  koszt_usd REAL DEFAULT 0, tokeny_wej INT DEFAULT 0, tokeny_wyj INT DEFAULT 0,
  blad TEXT, uwagi TEXT
);

CREATE TABLE IF NOT EXISTS odpowiedzi (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  uruchomienie_id INTEGER REFERENCES uruchomienia(id) ON DELETE CASCADE,
  haslo_id TEXT REFERENCES hasla(id),
  prompt TEXT, surowe TEXT, odpowiedz TEXT, kandydaci TEXT, pewnosc REAL,
  poprawna INT, poprawna_luzno INT, w_top5 INT, dlugosc_ok INT, wzorzec_ok INT, format_ok INT,
  czas_ms INT, tokeny_wej INT, tokeny_wyj INT, koszt_usd REAL, blad TEXT, at REAL,
  UNIQUE(uruchomienie_id, haslo_id)
);

CREATE TABLE IF NOT EXISTS zdarzenia (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL, typ TEXT, tresc TEXT
);

CREATE TABLE IF NOT EXISTS ustawienia (klucz TEXT PRIMARY KEY, wartosc TEXT);
"""


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def init() -> sqlite3.Connection:
    con = connect()
    with _lock:
        con.executescript(SCHEMA)
    return con


def log(con, typ: str, tresc: str):
    with _lock:
        con.execute("INSERT INTO zdarzenia(at, typ, tresc) VALUES (?,?,?)", (time.time(), typ, tresc))
        con.commit()


def rows(con, sql, args=()):
    with _lock:
        return [dict(r) for r in con.execute(sql, args).fetchall()]


def one(con, sql, args=()):
    r = rows(con, sql, args)
    return r[0] if r else None


def execute(con, sql, args=()):
    with _lock:
        cur = con.execute(sql, args)
        con.commit()
        return cur


def setting(con, key, default=None):
    r = one(con, "SELECT wartosc FROM ustawienia WHERE klucz=?", (key,))
    return json.loads(r["wartosc"]) if r else default


def set_setting(con, key, value):
    execute(con, "INSERT OR REPLACE INTO ustawienia(klucz, wartosc) VALUES (?,?)", (key, json.dumps(value)))
