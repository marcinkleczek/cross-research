"""Ręczna próbka odpowiedzi do testu potoku (zrodlo_odpowiedzi='probka_reczna').

Dopasowanie po fragmencie opisu (po OCR) i długości hasła w siatce; pozycje bez zgodnej długości są pomijane
i wypisywane. To NIE jest klucz z wydawnictwa; służy wyłącznie sprawdzeniu, że benchmark działa od końca do końca.
"""
from __future__ import annotations

from . import db

# (fragment opisu, odpowiedź, kategoria, polskość, typ opisu)
PROBKA = [
    ("Zachodni zaborca", "PRUSY", "historia", "polska", "definicja"),
    ("mucha afrykańska", "TSE", "przyroda", "swiat", "definicja"),
    ("Walor moralny", "CNOTA", "jezyk", "neutralne", "synonim"),
    ("Listwa stołu bilardowego", "BANDA", "sport", "neutralne", "definicja"),
    ("Amator eukaliptusa", "KOALA", "przyroda", "swiat", "definicja"),
    ("Naczynie na kakao", "KUBEK", "zycie_codzienne", "neutralne", "definicja"),
    ("Natury lub matki", "ŁONO", "jezyk", "neutralne", "dopelnienie"),
    ("Nabranie powietrza", "WDECH", "nauka", "neutralne", "definicja"),
    ("rola B. Lindy", "SARA", "kultura", "polska", "nazwa_wlasna"),
    ("Naprawa uszkodzenia", "REPERACJA", "jezyk", "neutralne", "synonim"),
    ("Sąsiad Indiany", "OHIO", "geografia", "swiat", "nazwa_wlasna"),
    ("skrzyzowanie", "RONDO", "zycie_codzienne", "neutralne", "definicja"),
    ("Dawniej uraza", "ANSA", "jezyk", "neutralne", "synonim"),
    ("Dworskie ukłony", "DYGI", "jezyk", "neutralne", "definicja"),
    ("Obrabia metale", "TOKARZ", "zycie_codzienne", "neutralne", "definicja"),
    ("Przyby", "OLIWIA", "kultura", "polska", "nazwa_wlasna"),
    ("Robin, łucznik", "HOOD", "kultura", "swiat", "nazwa_wlasna"),
    ("gejszy", "OBI", "kultura", "swiat", "definicja"),
    ("Miota bełtami", "KUSZA", "historia", "neutralne", "definicja"),
    ("Klej dla szklarza", "KIT", "zycie_codzienne", "neutralne", "definicja"),
    ("Kurosawy", "AKIRA", "kultura", "swiat", "nazwa_wlasna"),
    ("łańcucha DNA", "GEN", "nauka", "neutralne", "definicja"),
    ("Siostra Balladyny", "ALINA", "kultura", "polska", "nazwa_wlasna"),
    ("Żabi", "ABU", "geografia", "swiat", "dopelnienie"),
    ("Małpa z lasów Borneo", "ORANGUTAN", "przyroda", "swiat", "definicja"),
    ("Płynie przez Bawari", "IZARA", "geografia", "swiat", "definicja"),
]


def apply(con, krzyzowka: str = "IMG_1803") -> dict:
    ok, skipped = [], []
    hs = db.rows(con, "SELECT * FROM hasla WHERE krzyzowka_id=? AND opis IS NOT NULL", (krzyzowka,))
    for frag, ans, kat, pol, typ in PROBKA:
        cands = [h for h in hs if frag.lower() in (h["opis"] or "").lower() and h["dlugosc"] == len(ans)]
        if len(cands) != 1:
            skipped.append((frag, ans, [(h["nr"], h["dlugosc"]) for h in hs if frag.lower() in (h["opis"] or "").lower()]))
            continue
        h = cands[0]
        db.execute(con, "UPDATE hasla SET odpowiedz=?, kategoria=?, polskosc=?, typ_opisu=?, zrodlo_odpowiedzi='probka_reczna', gotowe=1 WHERE id=?",
                   (ans, kat, pol, typ, h["id"]))
        ok.append((h["id"], ans))
    db.log(con, "probka", f"{krzyzowka}: ustawiono {len(ok)} odpowiedzi próbnych, pominięto {len(skipped)}")
    return {"ustawiono": ok, "pominieto": skipped}


if __name__ == "__main__":
    import sys
    r = apply(db.init(), sys.argv[1] if len(sys.argv) > 1 else "IMG_1803")
    print("ustawiono", len(r["ustawiono"]))
    for s in r["pominieto"]:
        print("pominięto:", s)
