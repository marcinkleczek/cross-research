"""Zapis wyniku w standardowych formatach plików krzyżówek.

* **.jpz** – XML Crossword Compiler (schemat http://crossword.info/xml/rectangular-puzzle,
  https://crossword.info/xml/rectangular-puzzle.xsd). Jedyny rozpowszechniony format z natywnymi
  kratkami opisów (`<cell type="clue">` z elementami `<clue>`) i strzałkami (`<arrow from= to=>`),
  czyli z pełnym układem krzyżówki szwedzkiej. Czytają go Crossword Compiler, Crossword Solver,
  XWord, aplety internetowe (Amuse Labs, Crossword Nexus).
* **.ipuz** – otwarty format JSON (http://ipuz.org, wersja v2), rodzaj
  ``http://ipuz.org/crossword/arrowword#1``. Czytają go GNOME Crosswords, Puzzazz, biblioteki ipuz.

Treści opisów nie znamy (brak OCR), więc pola tekstowe opisów są puste, a przy każdym opisie
podany jest plik z wycinkiem ramki. Litery rozwiązania również są nieznane.
"""
from __future__ import annotations

import json
import os
from xml.etree import ElementTree as ET
from xml.dom import minidom

NS = "http://crossword.info/xml/rectangular-puzzle"
NS_CC = "http://crossword.info/xml/crossword-compiler"

# kierunek hasła -> (dr, dc) i nazwa strony w schemacie jpz, w którą wskazuje strzałka
_DIR = {
    "poziomo": ((0, 1), "right"),
    "pionowo": ((1, 0), "bottom"),
    "poziomo_wstecz": ((0, -1), "left"),
    "pionowo_wstecz": ((-1, 0), "top"),
}
_OPPOSITE = {"right": "left", "left": "right", "top": "bottom", "bottom": "top"}


def _clue_side(word: dict, box: dict | None, cell: int) -> str:
    """Strona kratki startowej, przy której leży ramka opisu (left/right/top/bottom)."""
    if box is None:
        return _OPPOSITE[_DIR[word["kierunek"]][1]]
    r, c = word["start"]["wiersz"], word["start"]["kolumna"]
    x0, y0, x1, y1 = box["prostokat_px"]
    bx, by = (x0 + x1) / 2 - (c + 0.5) * cell, (y0 + y1) / 2 - (r + 0.5) * cell
    if abs(bx) >= abs(by):
        return "left" if bx < 0 else "right"
    return "top" if by < 0 else "bottom"


def _word_range(word: dict) -> tuple[str, str]:
    cells = word["litery"]
    (r0, c0), (r1, c1) = (cells[0]["wiersz"], cells[0]["kolumna"]), (cells[-1]["wiersz"], cells[-1]["kolumna"])
    x = f"{c0 + 1}-{c1 + 1}" if c0 != c1 else str(c0 + 1)
    y = f"{r0 + 1}-{r1 + 1}" if r0 != r1 else str(r0 + 1)
    return x, y


def to_jpz(result: dict, title: str | None = None) -> bytes:
    rows, cols = result["siatka"]["wiersze"], result["siatka"]["kolumny"]
    cell = result["siatka"]["bok_komorki_px"]
    types = result["komorki"]
    boxes = {b["id"]: b for b in result["opisy"]}
    words = [w for w in result["hasla"] if w["dlugosc"] >= 2]

    ET.register_namespace("", NS)
    root = ET.Element(f"{{{NS_CC}}}crossword-compiler")
    rp = ET.SubElement(root, f"{{{NS}}}rectangular-puzzle", {"alphabet": "AĄBCĆDEĘFGHIJKLŁMNŃOÓPQRSŚTUVWXYZŹŻ"})
    md = ET.SubElement(rp, f"{{{NS}}}metadata")
    ET.SubElement(md, f"{{{NS}}}title").text = title or os.path.splitext(result["plik"])[0]
    ET.SubElement(md, f"{{{NS}}}created").text = ""
    ET.SubElement(md, f"{{{NS}}}creator").text = "cross-research (ekstrakcja ze zdjęcia)"
    for tag in ("editor", "rights", "copyright", "publisher", "identifier"):
        ET.SubElement(md, f"{{{NS}}}{tag}").text = ""
    ET.SubElement(md, f"{{{NS}}}description").text = (
        "Układ krzyżówki szwedzkiej odczytany ze zdjęcia. Treści opisów i litery nie są odczytane; "
        "każdy opis ma wycinek obrazu (katalog opisy/). Strzałki leżą w pierwszej kratce hasła: "
        "from = strona, z której dochodzi (strona ramki opisu), to = kierunek hasła.")
    cw = ET.SubElement(rp, f"{{{NS}}}crossword")
    grid = ET.SubElement(cw, f"{{{NS}}}grid", {"width": str(cols), "height": str(rows)})
    ET.SubElement(grid, f"{{{NS}}}grid-look", {"numbering-scheme": "normal", "clue-square-divider-width": "1",
                                               "grid-line-color": "#000000", "block-color": "#000000",
                                               "font-color": "#000000", "number-color": "#000000"})

    # ramki opisów -> komórki (ramka może pokrywać kilka komórek; opis wpisujemy w pierwszą)
    clues_in_cell: dict[tuple[int, int], list[dict]] = {}
    for b in result["opisy"]:
        cells = [(c["wiersz"], c["kolumna"]) for c in b["komorki"]]
        if cells:
            clues_in_cell.setdefault(min(cells), []).append(b)
    numbers_at = {(v["wiersz"], v["kolumna"]): int(k) for k, v in result["numery"].items()}
    arrows_at: dict[tuple[int, int], list[dict]] = {}
    for w in words:
        key = (w["start"]["wiersz"], w["start"]["kolumna"])
        arrows_at.setdefault(key, []).append(w)

    for r in range(rows):
        for c in range(cols):
            t = types[r][c]
            attrs = {"x": str(c + 1), "y": str(r + 1)}
            if t == "O":
                attrs["type"] = "clue"
            elif t == "R":
                attrs["type"] = "block"
                attrs["background-color"] = "#DDDDDD"
            elif t == "X":
                attrs["type"] = "void"
            else:
                n = numbers_at.get((r, c))
                if n is not None:
                    attrs["bottom-right-number"] = str(n)
            ce = ET.SubElement(grid, f"{{{NS}}}cell", attrs)
            if t == "O":
                for b in clues_in_cell.get((r, c), []):
                    for wid in b["hasla"]:
                        ET.SubElement(ce, f"{{{NS}}}clue", {"word": str(wid)}).text = ""
                    if not b["hasla"]:
                        ce.append(ET.Comment(f" ramka opisu {b['id']} bez przypisanego hasła "))
            if t == "L":
                for w in arrows_at.get((r, c), []):
                    side = _clue_side(w, boxes.get(w["opis"]), cell)
                    to = _DIR[w["kierunek"]][1]
                    if side == to:  # ramka "przed" grotem nie zachodzi w poprawnym układzie; strzałka prosta
                        side = _OPPOSITE[to]
                    ET.SubElement(ce, f"{{{NS}}}arrow", {"from": side, "to": to})
    for w in words:
        x, y = _word_range(w)
        ET.SubElement(cw, f"{{{NS}}}word", {"id": str(w["id"]), "x": x, "y": y})
    clues = ET.SubElement(cw, f"{{{NS}}}clues", {"ordering": "by-position"})
    ET.SubElement(clues, f"{{{NS}}}title").text = "Opisy (w kratkach)"
    for w in words:
        b = boxes.get(w["opis"])
        txt = f"[opis {b['id']}: {b.get('plik', '')}]" if b else "[brak ramki opisu]"
        ET.SubElement(clues, f"{{{NS}}}clue", {"word": str(w["id"]), "number": str(w["id"])}).text = txt
    raw = ET.tostring(root, encoding="utf-8")
    pretty = minidom.parseString(raw).toprettyxml(indent=" ", encoding="utf-8")
    return pretty


def to_ipuz(result: dict, title: str | None = None) -> dict:
    rows, cols = result["siatka"]["wiersze"], result["siatka"]["kolumny"]
    cell = result["siatka"]["bok_komorki_px"]
    types = result["komorki"]
    boxes = {b["id"]: b for b in result["opisy"]}
    words = [w for w in result["hasla"] if w["dlugosc"] >= 2]
    numbers_at = {(v["wiersz"], v["kolumna"]): int(k) for k, v in result["numery"].items()}
    ext = "pl.leanmate.krzyzowka:"
    puzzle, solution = [], []
    for r in range(rows):
        prow, srow = [], []
        for c in range(cols):
            t = types[r][c]
            if t == "L":
                n = numbers_at.get((r, c))
                prow.append({"cell": 0, "style": {"mark": {"BR": str(n)}}} if n else 0)
                srow.append(None)  # litera nieznana
            elif t == "O":
                prow.append({"cell": "#", "style": {"color": "F3E6F8"}})
                srow.append("#")
            elif t == "R":
                prow.append({"cell": "#", "style": {"color": "DDDDDD"}})
                srow.append("#")
            else:
                prow.append(None)
                srow.append(None)
        puzzle.append(prow); solution.append(srow)
    clue_list, words_ext = [], []
    for w in words:
        b = boxes.get(w["opis"])
        side = _clue_side(w, b, cell)
        clue_list.append({
            "number": w["id"],
            "clue": "",
            "location": [w["start"]["kolumna"], w["start"]["wiersz"]],
            "enumeration": str(w["dlugosc"]),
        })
        words_ext.append({
            "numer": w["id"],
            "kierunek": {"poziomo": "Across", "pionowo": "Down", "poziomo_wstecz": "AcrossReversed", "pionowo_wstecz": "DownReversed"}[w["kierunek"]],
            "komorki": [[l["kolumna"], l["wiersz"]] for l in w["litery"]],
            "ramka_opisu": None if not b else {
                "id": b["id"], "komorki": [[c["kolumna"], c["wiersz"]] for c in b["komorki"]],
                "plik": b.get("plik"), "strona_od_startu": side},
            "strzalka": {"komorka": [w["strzalka"]["kolumna"], w["strzalka"]["wiersz"]], "pewnosc": w["strzalka"]["pewnosc"]},
        })
    out = {
        "version": "http://ipuz.org/v2",
        "kind": ["http://ipuz.org/crossword/arrowword#1"],
        "title": title or os.path.splitext(result["plik"])[0],
        "author": "cross-research (ekstrakcja ze zdjęcia)",
        "notes": "Treści opisów i litery nieodczytane; wycinki ramek w katalogu opisy/. Współrzędne komórek: [kolumna, wiersz], od 0.",
        "origin": result["plik"],
        "dimensions": {"width": cols, "height": rows},
        "block": "#",
        "empty": 0,
        "puzzle": puzzle,
        "solution": solution,
        "clues": {"Clues": clue_list},
        ext + "hasla": words_ext,
        ext + "komorki": types,
        ext + "zagadka": result.get("zagadka"),
        ext + "pasek_rozwiazania": result.get("pasek_rozwiazania"),
        ext + "ostrzezenia": result.get("ostrzezenia"),
    }
    return out


def write_formats(result: dict, out_dir: str) -> dict[str, str]:
    stem = os.path.splitext(result["plik"])[0]
    files = {}
    p = os.path.join(out_dir, f"{stem}.jpz")
    with open(p, "wb") as f:
        f.write(to_jpz(result))
    files["jpz"] = os.path.basename(p)
    p = os.path.join(out_dir, f"{stem}.ipuz")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(to_ipuz(result), f, ensure_ascii=False, indent=1)
    files["ipuz"] = os.path.basename(p)
    return files
