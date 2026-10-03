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
        # pliki w formatach krzyżówkowych
        from xml.etree import ElementTree as ET
        jpz = ET.parse(os.path.join(d, "IMG_1803.jpz")).getroot()
        ns = "{http://crossword.info/xml/rectangular-puzzle}"
        cells = jpz.findall(f".//{ns}cell")
        assert len(cells) == 16 * 11
        assert sum(1 for c in cells if c.get("type") == "clue") >= 30
        assert len(jpz.findall(f".//{ns}arrow")) >= 30 and len(jpz.findall(f".//{ns}word")) >= 30
        ipz = json.load(open(os.path.join(d, "IMG_1803.ipuz"), encoding="utf-8"))
        assert ipz["kind"] == ["http://ipuz.org/crossword/arrowword#1"]
        assert ipz["dimensions"] == {"width": 11, "height": 16} and len(ipz["puzzle"]) == 16
        assert len(ipz["clues"]["Clues"]) >= 30


if __name__ == "__main__":
    test_pipeline_on_sample()
    print("OK")
