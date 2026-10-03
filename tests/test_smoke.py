"""Test dymny: pełny potok na jednym przykładowym zdjęciu. Uruchomienie: python -m pytest -q  lub  python tests/test_smoke.py"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from krzyzowka.extract import analyze  # noqa: E402

SAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "input", "IMG_1803.jpeg")


def test_pipeline_on_sample():
    with tempfile.TemporaryDirectory() as d:
        r = analyze(SAMPLE, d)
        assert r["siatka"]["wiersze"] == 16 and r["siatka"]["kolumny"] == 11
        assert r["zagadka"] and r["zagadka"]["wysokosc"] == 6 and r["zagadka"]["szerokosc"] == 6
        assert r["pasek_rozwiazania"]["liczba_pol"] == 40
        assert 35 <= len(r["opisy"]) <= 40 and 34 <= len(r["hasla"]) <= 40
        assert len(r["numery"]) >= 36
        assert os.path.exists(os.path.join(d, "wynik.json")) and os.path.exists(os.path.join(d, "naklad.jpg"))
        assert all(h["dlugosc"] >= 2 for h in r["hasla"] if h["uwaga"] is None)
        json.load(open(os.path.join(d, "wynik.json"), encoding="utf-8"))


if __name__ == "__main__":
    test_pipeline_on_sample()
    print("OK")
