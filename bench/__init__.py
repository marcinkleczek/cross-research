"""Benchmark rozwiązywania polskich krzyżówek przez małe modele językowe: baza SQLite, OCR opisów,
dostawcy modeli (Ollama, OpenRouter, Claude Code CLI), warianty zadań, kolejka uruchomień, metryki, pulpit."""

import os as _os

def _load_env():
    """Wczytuje zmienne z pliku .env w katalogu projektu (klucze API), nie nadpisując już ustawionych."""
    p = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), ".env")
    if not _os.path.isfile(p):
        return
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        _os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env()
