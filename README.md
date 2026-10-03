# cross-research – ekstrakcja struktury krzyżówek szwedzkich ze zdjęć

Dwa elementy:

1. **`app/`** – serwer HTTP (biblioteka standardowa Pythona) z formularzem dla aparatu telefonu. Zdjęcia zapisuje w katalogu `input/`, pozwala uruchomić analizę i obejrzeć wynik.
2. **`krzyzowka/`** – potok wyciągający ze zdjęcia strukturę krzyżówki: siatkę, typy komórek, ramki opisów, strzałki i kierunki haseł, położenie liter każdego hasła, numery kratek, pasek rozwiązania oraz obszar zagadki (rysunek z dymkiem). Wyłącznie metody deterministyczne (morfologia, dopasowanie prostych, algorytm węgierski, dopasowanie szablonów). Żadnych modeli uczonych.

## Instalacja

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

Zależności: `numpy`, `opencv-python-headless` (klasyczne przetwarzanie obrazu), `Pillow` (EXIF, rendering szablonów cyfr z czcionek systemowych), `scipy` (algorytm węgierski).

## Etap 1 – wgrywanie zdjęć z telefonu

```bash
python -m app.server --port 8000 --katalog input --wyniki output
```

Na telefonie w tej samej sieci: `http://ADRES_IP_KOMPUTERA:8000/`. Przycisk otwiera aparat (`<input type="file" accept="image/*" capture="environment">`, działa bez HTTPS). Plik trafia do `input/` jako `RRRRMMDD_GGMMSS_nazwa.jpeg`. Lista wgranych zdjęć ma przycisk **Analizuj**; wynik (JSON, nakładka kontrolna, wycinki) pojawia się w `output/<nazwa>/` i pod adresami `/wyniki/<nazwa>/...`.

Interfejs API: `POST /api/wgraj` (multipart, pole `zdjecie`), `GET /api/lista`, `POST /api/analizuj/<plik>`, `GET /api/stan/<plik>`, `GET /zdjecia/<plik>`, `GET /wyniki/<ścieżka>`.

## Etap 2 – ekstrakcja struktury

```bash
python -m krzyzowka.cli input -o output            # wszystkie zdjęcia z katalogu
python -m krzyzowka.cli input/IMG_1803.jpeg -o output
```

Dla każdego zdjęcia powstaje katalog `output/<nazwa>/`:

| plik | zawartość |
|---|---|
| `wynik.json` | pełna struktura (opis niżej) |
| `naklad.jpg` | nakładka kontrolna: ramki opisów `O<n>`, strzałki, przebieg haseł `H<n>/O<m>`, numery kratek, rysunek, separatory faliste |
| `siatka.jpg` | zdjęcie po rektyfikacji (każda komórka ma dokładnie 128 × 128 px) |
| `krzywe.jpg` | wykryte krzywe siatki (wiersze i kolumny) naniesione na oryginalne zdjęcie, obrys siatki i obszar rysunku |
| `opisy/opis_NN.png` | wycinek każdej ramki opisu w pełnej rozdzielczości zdjęcia |
| `zagadka.png` | wycinek obszaru rysunku/zagadki |
| `pasek.png`, `podpis.png` | pasek rozwiązania i podpis nad nim |
| `<nazwa>.jpz` | krzyżówka w formacie Crossword Compiler XML (kratki opisów, strzałki, hasła) |
| `<nazwa>.ipuz` | krzyżówka w otwartym formacie ipuz, rodzaj `crossword/arrowword` |

### Przebieg

1. **Obrys siatki** – maska cienkich ciemnych struktur (black-hat), długie odcinki poziome i pionowe, największa spójna kratownica, cztery skrajne proste dopasowane odpornie (RANSAC) do obwiedni kratownicy, homografia. Z pikseli znalezionej kratownicy odczytywany jest kolor tuszu ramki (`siatka.tusz_ramki` w JSON) i z niego próg tuszu dla dalszych etapów – ramka zewnętrzna i linie wewnętrzne mają ten sam tusz, a stały próg zawodziłby przy innym druku lub oświetleniu.
2. **Kratownica** – liczba wierszy i kolumn z autokorelacji masek linii; każda linia pozioma i pionowa śledzona jako krzywa (próbki położenia co ćwierć komórki); globalne pole deformacji (wielomian 2D, RANSAC z hipotezą kwadratową), potem lokalne doskakiwanie węzłów do linii. Obraz jest przepróbkowany do idealnie regularnej siatki (dwuliniowo w obrębie komórki), co usuwa zagięcie strony. Mediana błędu położenia linii na 13 zdjęciach: 1 px, 90. percentyl: 6 px (przy komórce 128 px).
3. **Krawędzie** – dla każdej krawędzi komórki: brak / linia prosta / separator falisty (zygzak kończący wyraz: oscylacja środka ciężkości tuszu po usunięciu trendu).
4. **Typy komórek** – litera / opis / rysunek / poza kadrem. Barwa tła (udziały RGB, odporne na cienie), udział tuszu, rozrzut koloru; rysunek jako prostokąt o maksymalnej sumie „rysunkowości”, rozszerzany o dymki i pasy z tekstem zagadki nadrukowanym na kratkach.
5. **Ramki opisów** – obszar komórek typu opis po odjęciu „ścian” (długie proste odcinki tuszu i granice z komórkami innego typu); składowe spójne = ramki. Układ nie musi trzymać się siatki (kolumna opisów dzielona na N równych ramek, ramki na dwie kolumny itd.).
6. **Strzałki** – wypełnione trójkąty w komórkach literowych (dopasowanie IoU do wzorca trójkąta w czterech orientacjach); ogonki strzałek łamanych. Każda strzałka dostaje najbliższą ramkę (odległość od prostokąta ramki, ogonek, położenie względem grotu). Jedna ramka może mieć dwie strzałki – w tych krzyżówkach ten sam wyraz bywa wpisywany poziomo i pionowo.
7. **Hasła** – od komórki ze strzałką w jej kierunku, aż do komórki nieliterowej, krawędzi siatki lub separatora falistego.
8. **Numery kratek** – plamy w prawym dolnym rogu komórek literowych; podobieństwo do cyfr 0–9 z szablonów renderowanych z czcionek bezszeryfowych pogrubionych (Liberation/FreeSans/DejaVu); przypisanie unikatowych wartości 1..N algorytmem węgierskim, gdzie N to liczba pól paska rozwiązania.
9. **Pasek rozwiązania** – rzędy pól pod siatką (pasma między liniami poziomymi, kreski pionowe), liczba pól, podział na słowa po pogrubionych kreskach.

### Pliki w formatach krzyżówkowych

Dwa rozpowszechnione formaty plików krzyżówek obsługują układ szwedzki (opisy w kratkach, strzałki):

* **`.jpz`** – XML programu Crossword Compiler, schemat [rectangular-puzzle.xsd](https://crossword.info/xml/rectangular-puzzle.xsd) (przestrzeń nazw `http://crossword.info/xml/rectangular-puzzle`). Kratka opisu to `<cell type="clue">` z elementami `<clue word="ID">` (kilka opisów w jednej kratce = przegroda), strzałka to `<arrow from="…" to="…">` w pierwszej kratce hasła: `from` to strona, od której dochodzi (strona ramki opisu), `to` to kierunek hasła; `<word id x="5-9" y="3">` wiąże kratki hasła, zakres malejący oznacza hasło wsteczne. Numery z kratek trafiają do `bottom-right-number`, obszar rysunku to `type="block"`. Plik przechodzi walidację schematem (po zdjęciu przestrzeni nazw z elementów potomnych, bo schemat deklaruje elementy niekwalifikowane, a pliki Crossword Compilera używają domyślnej przestrzeni nazw).
* **`.ipuz`** – otwarty format JSON ([ipuz.org](https://libipuz.org/ipuz-spec.html), `"version": "http://ipuz.org/v2"`, `"kind": ["http://ipuz.org/crossword/arrowword#1"]`). Siatka w `puzzle` (kratki opisów i rysunku jako bloki `#` z kolorem, numery jako `style.mark.BR`), opisy w `clues.Clues` z `location` i `enumeration`. Szczegóły spoza specyfikacji (kratki hasła, kierunek, ramka opisu, strzałka, zagadka, pasek) są w kluczach rozszerzeń `pl.leanmate.krzyzowka:*`, zgodnie z konwencją ipuz. Plik przechodzi walidację biblioteką `ipuz` (PyPI).

W obu plikach treści opisów są puste (brak OCR), a przy każdym opisie jest odwołanie do wycinka `opisy/opis_NN.png`; litery rozwiązania są nieznane. Po uzupełnieniu treści plik otwiera się w Crossword Compilerze, Crossword Solverze, XWordzie, GNOME Crosswords lub apletach sieciowych.

### Format `wynik.json`

```
plik, wersja
siatka: wiersze, kolumny, bok_komorki_px, narozniki_na_zdjeciu [4×(x,y)], wezly_na_zdjeciu [(wiersze+1)×(kolumny+1)×(x,y)]
komorki: lista napisów po jednym na wiersz; L litera, O opis, R rysunek/zagadka, X poza kadrem
opisy[]: id, komorki[{wiersz,kolumna}], prostokat_px [x0,y0,x1,y1] (w siatka.jpg), hasla[id], plik
hasla[]: id, opis (id ramki lub null), start{wiersz,kolumna}, kierunek (poziomo|pionowo|poziomo_wstecz|pionowo_wstecz),
         dlugosc, litery[{wiersz,kolumna,numer|null}], strzalka{wiersz,kolumna,x_px,y_px,pewnosc}, uwaga
numery: {"1": {wiersz,kolumna,pewnosc}, ...}
pasek_rozwiazania: liczba_pol, wiersze_paska[], dlugosci_slow[], plik, podpis_plik
zagadka: typ, opis, wiersz, kolumna, wysokosc, szerokosc, obrys_na_zdjeciu, plik   (null, gdy brak rysunku)
ostrzezenia[]: ramki bez strzałki, brakujące numery, komórki poza kadrem itp.
```

Współrzędne `*_px` odnoszą się do obrazu zrektyfikowanego (`siatka.jpg`, komórka = 128 px), a `*_na_zdjeciu` do oryginalnego zdjęcia po uwzględnieniu orientacji EXIF.

### Wyniki na 13 przykładowych zdjęciach

Siatka 16×11 (17×11 dla uciętego IMG_1800) rozpoznana na wszystkich; obszar rysunku poprawny na wszystkich (brak w IMG_1811, który go nie ma); liczba pól paska zgodna na wszystkich z wyjątkiem IMG_1800 (zdjęcie ucina górny rząd); numery kratek komplet na 9 zdjęciach, na pozostałych brakuje jednego. Ramki opisów bez przypisanej strzałki: 1–5 na zdjęcie (część to rzeczywiste podwójne strzałki, część to przeoczone trójkąty) – wszystkie są wymienione w `ostrzezenia`.

### Czego nie ma

* **Treści opisów.** Odczyt tekstu ze zdjęcia bez modelu uczonego nie jest realny; potok zapisuje wycinek każdej ramki (`opisy/opis_NN.png`) z pozycją w siatce, więc OCR (lub człowiek) można podpiąć osobno.
* Numery cyfr są rozpoznawane szablonowo; przy słabym zdjęciu pojedyncze wartości mogą być zamienione – `pewnosc` i `ostrzezenia` to sygnalizują.

## Test

```bash
python tests/test_smoke.py      # lub: python -m pytest -q
```
