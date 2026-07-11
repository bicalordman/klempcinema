# KlempCinema

Kodi video doplněk pro procházení a přehrávání filmů a seriálů z Webshare.

**Aktuální verze: 0.0.137**

---

## Instalace

### Požadavky

| Co | Detail |
|----|--------|
| **Kodi** | verze **19 (Matrix)** nebo novější |
| **Webshare** | vlastní účet na [webshare.cz](https://webshare.cz) (VIP předplatné u Webshare) |
| **TMDB API klíč** | zdarma na [themoviedb.org](https://www.themoviedb.org/settings/api) — pro plakáty a metadata |
| **Síť** | připojení k internetu |

Doplněk funguje bez reklam a bez sběru dat. Obsah se stahuje přímo z Webshare pod tvým vlastním účtem.

---

### 1. Stažení ZIP souboru

1. Otevři stránku **Releases** na GitHubu:  
   [github.com/bicalordman/klempcinema/releases](https://github.com/bicalordman/klempcinema/releases)
2. U nejnovější verze (např. **v0.0.137**) klikni na **`plugin.video.klempcinema-0.0.137.zip`**
3. ZIP se stáhne do složky Stažené soubory

> Stahuj vždy **nejnovější release** — ne starší ZIP z disku, pokud nevíš, že je aktuální.

---

### 2. Instalace v Kodi

#### Windows / Linux / macOS

1. Spusť **Kodi**
2. V levém menu: **Doplňky** (ikona puzzle)
3. Klikni na **ikonu krabice** (nahoře vlevo) → **Nainstalovat ze souboru ZIP**
4. Najdi a vyber stažený soubor `plugin.video.klempcinema-0.0.137.zip`
5. Počkej na hlášku **„Doplněk nainstalován"**

#### Android TV / Android box

1. ZIP zkopíruj na zařízení (USB, cloud, e-mail…)
2. V Kodi: **Doplňky → Nainstalovat ze souboru ZIP**
3. Projdí k ZIPu (např. `Download/` nebo `Internal storage/Download/`)
4. Vyber ZIP a potvrď instalaci

#### Aktualizace starší verze

Pokud už máš starší KlempCinema (např. 0.0.84):

1. **Doplňky → Moje doplňky → Videodoplňky → KlempCinema → Odinstalovat**
2. **Restartuj Kodi** (úplně ukončit a znovu spustit)
3. Nainstaluj nový ZIP podle kroku 2 výše

> Přihlašovací údaje k Webshare zůstanou zachované (uložené v `userdata/addon_data`).

---

### 3. První spuštění — nastavení

Po instalaci otevři **KlempCinema** v menu Doplňky. Při prvním spuštění:

#### Webshare účet

Doplněk zobrazí dialog pro zadání přihlašovacích údajů:

- **Uživatelské jméno** — tvůj login na webshare.cz
- **Heslo** — tvé heslo na webshare.cz

Účet si musíš založit sám na [webshare.cz](https://webshare.cz). Doplněk žádný účet neposkytuje.

Údaje lze kdykoli změnit v: **Doplňky → Moje doplňky → KlempCinema → Nastavení**

#### TMDB API klíč (doporučeno)

Bez TMDB klíče nebudou plakáty ani hodnocení filmů.

1. Zaregistruj se na [themoviedb.org](https://www.themoviedb.org/signup)
2. Jdi do **Nastavení → API** a vyžádej **API klíč** (typ: Developer / Personal)
3. V Kodi: **Doplňky → Moje doplňky → KlempCinema → Nastavení → TMDB**
4. Vlož API klíč a ulož

---

### 4. Ověření, že vše funguje

1. Otevři **KlempCinema** z menu Doplňky
2. Zkus rubriku **Filmy** nebo **Novinky dabované**
3. Měly by se zobrazit položky s plakáty (po pár sekundách)
4. Klikni na film → vyber kvalitu → přehrání by mělo začít

---

### Řešení problémů

| Problém | Řešení |
|---------|--------|
| „Nepodařilo se nainstalovat doplněk ze ZIP" | Odinstaluj starou verzi, restartuj Kodi, nainstaluj znovu. Stáhni ZIP znovu z GitHub Releases. |
| Prázdné seznamy filmů | Zkontroluj Webshare přihlášení v Nastavení doplňku. |
| Filmy bez plakátů | Zadej TMDB API klíč v Nastavení. |
| Pomalé načítání | Normální při prvním otevření rubriky (~10 s). Druhé otevření je okamžité (cache). |
| Kodi se pomalu vypíná | Aktualizuj na nejnovější verzi (0.0.133+). |

---

## Funkce

- Procházení filmů a seriálů (Webshare + TMDB metadata, žánry v popisku)
- Trending, žánry, streamovací platformy (Netflix/HBO/Disney+...)
- Seriály s rozdělením na sezóny a díly
- TV program dnes (zdroj iDNES + TMDB plakáty)
- Voyo (SK) pořady
- Animované CZ/SK filmy
- Pohádky CZ/SK
- Koncerty (CZ/SK i zahraniční, filtrování podle žánru)
- Dokumentární filmy (oddělené od koncertů)
- České titulky (OpenSubtitles.org)
- Pokračovat ve sledování / historie
- Kontextové menu (trailer, ČSFD, obnovit metadata, titulky…)

---

## Jazyky / Languages

Doplněk je dostupný v těchto jazycích podle aktuálního jazyka Kodi:

- **Čeština** (`cs_cz`) — kompletní překlad
- **English** (`en_gb`) — full translation (default Kodi installs use English)
- **Ostatní jazyky** — anglický fallback (přes Kodi `msgid` mechanismus)

Pokud chceš jiný jazyk, lokalizační soubor je v `resources/language/resource.language.XX_xx/strings.po` — pull requesty vítány.

---

## Výkon / Performance

Plugin stahuje metadata (plakáty, hodnocení) z TMDB a ČSFD. První otevření rubriky může trvat několik sekund — **druhé otevření stejné rubriky je okamžité** (cache 30 minut).

### Tipy pro rychlejší prohlížení

| Nastavení | Kde | Efekt |
|-----------|-----|-------|
| **Přeskočit enrich** | Nastavení doplňku → TMDB | Nejrychlejší režim — seznamy bez plakátů a hodnocení |
| **Položek na stránku** | Nastavení doplňku → Přehrávání | Snížit na 20 pro slabší zařízení (Xbox, RPi) |
| **Vypnout ČSFD** | Nastavení doplňku → ČSFD | Méně síťových dotazů (ČSFD se na seznamech volá jen jako fallback) |

### Co se děje na pozadí

- **Časový limit enrich** — seznam se zobrazí do několika sekund, i když metadata ještě nedoběhla pro všechny položky
- **ČSFD jen fallback** — pokud TMDB vrátí plakát a hodnocení, ČSFD se na seznamu nevolá
- **Kratší timeouty** — při vypínání Kodi plugin nečeká dlouho na síťové odpovědi (neblokující ukončení thread poolů)
- **Prefetch** — další stránka se načítá na pozadí, zatímco prohlížíš aktuální

---

## Dar autorovi (dobrovolný)

V hlavním menu je položka **„Poslat autorovi dar (dobrovolné)"**. Po jejím otevření se zobrazí jednoduchý dialog s IBANem a QR kódem pro CZ bankovní platbu (formát SPD).

### Právní výklad

KlempCinema je **zdarma**. Tlačítko „Poslat autorovi dar" slouží pouze k tomu, aby spokojení uživatelé mohli dobrovolně přispět na čas vývojáře. Platba je:

- **DOBROVOLNÁ** — žádný uživatel nemusí přispět; plugin funguje stejně pro všechny
- **BEZ PROTIHODNOTY** — uživatel za dar nezískává žádný speciální obsah, prioritu, lepší kvalitu, ani víc rubrik
- **Označená jako dar** — QR zpráva i ruční zadání má text „Dar KlempCinema"
- **Příjemce: fyzická osoba** (autor doplňku Bicalorman)

### Daňový režim (CZ)

Dar peněžních prostředků od jedné fyzické osoby druhé fyzické osobě je **osvobozen od daně z příjmu** podle **§10 odst. 3 písm. c) zákona č. 586/1992 Sb., o daních z příjmů**, pokud souhrn darů od JEDNOHO dárce příjemci za jeden kalendářní rok **nepřevýší 50 000 Kč**.

To znamená:
- Autor přiznává v DPFO pouze tu část darů, která od jednoho dárce za rok přesáhne 50 000 Kč.
- Pokud žádný dárce za rok nedaruje více než 50 000 Kč, autor nemusí dary do DPFO uvádět (jsou osvobozeny).

### Co dar NENÍ

- **Nejde o platbu za obsah.** Obsah poskytuje třetí strana (Webshare.cz). Uživatel si sjednává vlastní VIP předplatné u Webshare nezávisle na doplňku.
- **Nejde o úplatu za službu.** Doplněk je veřejně dostupný a funkční bez jakéhokoli daru.
- **Nejde o předplatné.** Žádné automatické platby, žádné měsíční fakturace.

### Údaje pro ruční platbu

```
IBAN:    CZ95 5500 0000 0010 2685 1852
Banka:   Raiffeisenbank
Měna:    CZK
Zpráva:  Dar KlempCinema    (důležité - nech tam slovo „Dar")
```

### Jak poslat dar přes QR kód

1. V hlavním menu otevři **Poslat autorovi dar (dobrovolné)**
2. Zvol **Zobrazit QR**
3. V **bankovní aplikaci** zvol Platit → Naskenovat QR (ne prohlížeč — QR je ve formátu SPD pro banky)
4. Zadej částku a odešli

---

## Kontakt

Autor: **Bicalorman**

Pro hlášení chyb a návrhy — kontaktuj kanál odkud jsi doplněk získal.

---

## Změny

Kompletní historie od poslední veřejné verze **0.0.84** po aktuální **0.0.137**.

### Souhrn 0.0.84 → 0.0.137

| Oblast | Hlavní změny |
|--------|----------------|
| Instalace | Oprava ZIP a `addon.xml`, spolehlivé `build_zip.ps1` |
| Architektura | Refaktor routeru do `views/` + `router_common.py` |
| Seriály / Voyo | Oprava prázdných seznamů, epizody bez SxxEyy, slovenské markery |
| TV program | Nové sekce, HBO/placené kanály, plakáty, deduplikace |
| Koncerty | Nová rubrika, hledání, CZ/SK kapely, vlastní plakáty, stránkování |
| Filmy / dabing | Hledání s rokem, detekce CZ audia, picker kvality, novinky |
| Metadata | Sdílená cache, auto-heal překlepů, ČSFD záchrana plakátů |
| Výkon | Globální strop ~12 s, RAM cache, rychlé vypínání Kodi |
| Nové rubriky | Dokumentární filmy CZ/SK (odděleně od koncertů) |

---

### v0.0.137 — Plynulejší listování koncertů

- Kratší časový rozpočet načítání + prefetch další stránky stihne doběhnout

### v0.0.136 — Rychlejší vypínání Kodi + CZ koncerty

- Neblokující ukončení thread poolů
- Databáze CZ/SK kapel (rock, metal, folk, country, pop, rap) pro vyhledávání na Webshare

### v0.0.135 — Stabilní stránkování koncertů

- Filtrování před rozdělením na stránky (30 položek/stránku), plakáty nahoře, plná cache

### v0.0.134 — Vlastní plakáty koncertů

- Placeholder plakát pro koncerty; dokumenty vyřazeny z koncertní rubriky

### v0.0.133 — Rychlé vypínání Kodi

- Oprava `lifecycle` úklidu (chybějící import `threading`)

### v0.0.132 — Víc plakátů

- Čištění názvů souborů od žánrů a technických značek pro lepší TMDB match

### v0.0.131 — ČSFD záchrana plakátů

- Cílené doplnění plakátů pro české tituly, které TMDB nenajde (max 5 položek, 3 s)

### v0.0.130 — Globální časový strop

- Načítání rubriky max ~12 s místo 40 s+; rychlejší i další stránky

### v0.0.128–0.0.129 — Zrychlení filmových rubrik

- Bez ČSFD brzdy při prvním načtení; jen TMDB plakáty; verze 129 = stejné + oprava instalace

### v0.0.127 — Přísnější shoda WS ↔ TMDB

- Sequel hint jen s číslem dílu v názvu; kontrola kompatibility názvu souboru a metadat

### v0.0.126 — Stabilita při dlouhém používání

- Úklid po navigaci, limit RAM cache, image workery, jeden TV program fetch na pozadí

### v0.0.125 — Dokumentární filmy + koncerty

- Nová rubrika **Filmy dokumentární CZ/SK**; dokumenty vyřazeny z koncertů; oprava TMDB žánru Hudba

### v0.0.124 — TV program bez duplicit

- Deduplikace položek (kanál + čas + titul) u placených kanálů

### v0.0.123 — Rychlejší plakáty (TV program, platformy)

- TMDB plakáty hned při otevření; platformy zobrazí celý TMDB seznam s plakáty

### v0.0.122 — Auto-heal metadat

- Automatické opravy překlepů a sequel hintů bez ručního „Obnovit metadata“

### v0.0.121 — Obnovit metadata + překlepy

- Rok se posílá i při obnově; opravy překlepů v názvech; přísnější shoda epizod

### v0.0.119–0.0.120 — Voyo epizody

- Rozpoznání `epizoda 1`, `díl 5`, `1. diel` bez SxxEyy; volnější shoda názvu seriálu

### v0.0.118 — Obnovit metadata, testy

- Context menu **Obnovit metadata**; fuzzy match; unit testy (`run_tests.ps1`)

### v0.0.114–0.0.117 — Plakáty a přehrávání

- RAM cache, paralelní stahování plakátů; WS thumb ≠ plakát; rok jen po TMDB matchi

### v0.0.108–0.0.113 — Picker kvality

- Filtr podle roku; kratší řádky; zobrazení zvuku (5.1, DTS, 6CH…)

### v0.0.100–0.0.107 — Hledání a dabing

- Oprava deduplikace; detekce `CZ.dub` a `.CZ.5.1`; hledání v rubrikách; CZ-only picker z dabovaných

### v0.0.98–0.0.99 — Hledání s rokem

- Rok z klávesnice; oprava Novinek a Novinek dabovaných; hledání v rubrikách Filmy/Novinky

### v0.0.92–0.0.97 — Koncerty

- Nová rubrika (CZ/SK, zahraniční, žánry, hledání); vlastní pipeline hledání; víc českých koncertů

### v0.0.88–0.0.91 — Seriály, Voyo, TV program

- Striktní shoda epizod; Voyo celý katalog; TV program se sekcemi a HBO na pozadí

### v0.0.85–0.0.87 — Instalace a seriály

- Refaktor routeru; Kodi-kompatibilní ZIP; oprava prázdných Seriálů (`variant_idents`)

### v0.0.84 — Oprava instalace

- Zkrácený changelog v `addon.xml` (některá Kodi buildy neuměly parsovat velký manifest)

### Starší verze (0.0.76–0.0.83)

- Donate + welcome flow; rychlejší shutdown; jen tituly na Webshare; žánry v popisku; stabilní stránkování

---

## Licence

GPL-3.0-or-later — viz `LICENSE` (pokud není zahrnut, viz [www.gnu.org/licenses/gpl-3.0.html](https://www.gnu.org/licenses/gpl-3.0.html))
