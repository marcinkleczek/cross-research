"""Uruchomienie: python -m krzyzowka.cli ZDJĘCIE [ZDJĘCIE ...] -o KATALOG_WYJŚCIOWY"""
from __future__ import annotations

import argparse
import os
import sys
import time
import traceback

from .extract import analyze


def main(argv=None):
    ap = argparse.ArgumentParser(description="Ekstrakcja struktury krzyżówki szwedzkiej ze zdjęcia (metody klasyczne).")
    ap.add_argument("zdjecia", nargs="+", help="pliki zdjęć lub katalog")
    ap.add_argument("-o", "--out", default="output", help="katalog wyjściowy (podkatalog na każde zdjęcie)")
    ap.add_argument("--bok", type=int, default=128, help="bok komórki w obrazie zrektyfikowanym [px]")
    ap.add_argument("--bez-wycinkow", action="store_true", help="nie zapisuj wycinków ramek/zagadki/paska")
    args = ap.parse_args(argv)
    files = []
    for p in args.zdjecia:
        if os.path.isdir(p):
            files += sorted(os.path.join(p, f) for f in os.listdir(p) if f.lower().endswith((".jpg", ".jpeg", ".png", ".heic")))
        else:
            files.append(p)
    rc = 0
    for f in files:
        stem = os.path.splitext(os.path.basename(f))[0]
        out = os.path.join(args.out, stem)
        t = time.time()
        try:
            r = analyze(f, out, cell=args.bok, save_crops=not args.bez_wycinkow)
            s = r["siatka"]; ps = r["pasek_rozwiazania"]
            print(f"{stem}: {s['wiersze']}x{s['kolumny']}, opisy {len(r['opisy'])}, hasla {len(r['hasla'])}, numery {len(r['numery'])},"
                  f" pasek {ps['liczba_pol'] if ps else '-'} {ps['dlugosci_slow'] if ps else ''}, zagadka {'tak' if r['zagadka'] else 'nie'},"
                  f" ostrzezen {len(r['ostrzezenia'])}, {time.time() - t:.1f}s -> {out}")
        except Exception as e:  # noqa: BLE001
            rc = 1
            print(f"{stem}: BŁĄD {e}", file=sys.stderr)
            traceback.print_exc()
    return rc


if __name__ == "__main__":
    sys.exit(main())
