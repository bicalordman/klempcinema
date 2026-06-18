# KlempCinema

Kodi video doplněk pro procházení a přehrávání filmů a seriálů z Webshare.

**Aktuální verze: 0.0.84**

---

## Instalace

1. V Kodi: **Doplňky → Nainstalovat ze ZIPu**
2. Vyber `plugin.video.klempcinema-X.Y.Z.zip`
3. Při prvním spuštění doplněk vyzve k zadání **Webshare účtu** (vlastní jméno + heslo). Účet si je třeba pořídit zvlášť na [webshare.cz](https://webshare.cz).

Doplněk funguje bez reklam, bez sběru dat. Veškerý obsah je stahován přímo z Webshare, ke kterému si uživatel sjednává vlastní VIP předplatné.

---

## Funkce

- Procházení filmů a seriálů (Webshare + TMDB metadata, žánry v popisku)
- Trending, žánry, streamovací platformy (Netflix/HBO/Disney+...)
- Seriály s rozdělením na sezóny a díly
- TV program dnes (zdroj iDNES + TMDB plakáty)
- Voyo (SK) - Markíza pořady
- Animované CZ/SK filmy
- Pohádky CZ/SK
- České titulky (OpenSubtitles.org)
- Pokračovat ve sledování / historie
- Kontextové menu (skenování ČSFD, trailer, smazat...)

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
| **Vypnout ČSFD** | Nastavení doplňku → ČSFD | Méně síťových dotazů (od v0.0.81 se ČSFD na seznamech volá jen jako fallback) |

### Co se děje na pozadí (v0.0.81+)

- **Časový limit enrich** — seznam se zobrazí do ~6 sekund, i když metadata ještě nedoběhla pro všechny položky
- **ČSFD jen fallback** — pokud TMDB vrátí plakát a hodnocení, ČSFD se na seznamu nevolá (úspora 1–3 s na film)
- **Kratší timeouty** — při vypínání Kodi plugin nečeká tak dlouho na síťové odpovědi
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

### v0.0.84 — Oprava instalace (zkrácený changelog v addon.xml)

**Problém:** ZIP `0.0.83` nešel v některých Kodi nainstalovat (chyba „neplatná struktura“ nebo instalace se neukončí).

**Příčina:** Soubor `addon.xml` obsahoval changelog **~98 KB** (přes 2000 řádků v `<news>`). Některé Kodi buildy (Android TV, Xbox, starší verze) při parsování manifestu selžou.

**Oprava:**
- Changelog v `addon.xml` zkrácen na poslední verze (0.0.79–0.0.84)
- Kompletní historie změn zůstává v tomto **README.md**
- Funkčnost 0.0.83 beze změny (žánry v popisku, WS filtr, Michael, stránkování)

**Instalace:** Použij `plugin.video.klempcinema-0.0.84.zip`. Pokud máš rozbitou 0.0.83, nejdřív **odinstaluj** starý doplněk, pak nainstaluj 0.0.84 (přihlašovací údaje v `userdata/addon_data` zůstanou).

### v0.0.83 — Voyo + TV program WS filtr + Michael v Nových dabovaných + žánry v popisku

#### Webshare filtr i pro Voyo a TV program

V **v0.0.82** se ověření existence souboru na Webshare týkalo jen TMDB rubrik (Trendy, Žánry, Platformy). Po kliknutí ve **Voyo (SK)** nebo **TV program dnes** mohly zůstat položky bez souboru na Webshare.

**Oprava:** Stejný princip jako u TMDB rubrik — před zobrazením se každá položka ověří na Webshare. Zůstanou jen tituly s `variant_idents` (film → quality picker / přehrání, seriál → sezóny a díly).

#### Michael zmizel z rubriky Nově dabované

**Příčina 1 — špatný rok z TMDB:** Při enrichi TMDB někdy přiřadí jiný film se stejným názvem (např. starší „Michael“ místo dokumentu 2026). Post-filter s minimem roku 2024 tak správný titul vyhodil.

**Oprava:** `_effective_release_year` bere **maximum** roku z TMDB, názvu WS souboru a variant — novější rok má přednost.

**Příčina 2 — stránkování po dávkách:** Stránkování v0.0.82 řadilo jen aktuální dávku. Globálně nový film mohl skončit až na 5. stránce místo na správném místě.

**Oprava:** **Frozen `page_slices`** — každá stránka se při prvním otevření seřadí z **celého poolu**, ne jen z poslední dávky.

**Po upgrade:** V rubrice **Nově dabované** klikni **Aktualizovat** (cache `v5` → `v6`).

#### Žánr u každého filmu a seriálu

U všech seznamů včetně **vyhledávání**, rubrik a TMDB položek se po TMDB enrichi zobrazí žánr (české názvy z TMDB API).

| Kde | Co se zobrazí |
|-----|----------------|
| **Popisek (plot)** | řádek `Žánr: Drama, Akční, …` (nad hodnocením a popisem děje) |
| **Kodi info pole `genre`** | stejná hodnota pro info panel skinu |
| **Placeholder plakát** | žánr pod názvem (jen když TMDB plakát chybí a vygeneruje se vlastní obrázek) |

**V názvu řádku** (vedle plakátu) žánr **není** — název zůstává ve formátu: titul, rok, TMDB ★, ČSFD %, `[CZ]` / `[CZ tit]`, technické badges (`1080p`, `HEVC`…).

**Požadavek:** TMDB API klíč v nastavení doplňku (bez klíče žánry ani plakáty z TMDB nejsou).

**Technicky:** TMDB vrací `genre_ids` → plugin je mapuje přes `/genre/movie/list` a `/genre/tv/list` (cache 7 dní). Platí pro filmy i seriály včetně discover/trending položek po WS filtru.

### v0.0.82 — Rubriky: jen filmy na Webshare + fix opakování po str. 3

**Problém 1:** V rubrikách se zobrazovaly filmy bez souboru na Webshare (plakát z TMDB, ale po kliknutí „nenalezeno“).

**Příčina:** Rubriky Trendy / Žánry / Platformy braly seznam z TMDB bez ověření na Webshare. U WS rubrik mohly po enrich/dedup zůstat položky bez `variant_idents`.

**Opravy:**
- **TMDB rubriky** — před zobrazením se každý film/seriál ověří na Webshare; bez souboru se nezobrazí
- **WS rubriky** — filtr `_filter_with_webshare_files`: jen položky s `variant_idents`
- Nový upload na Webshare se objeví po **Aktualizovat** v rubrice nebo po vypršení cache (30 min)

**Problém 2:** Po 3. stránce se filmy v některých rubrikách opakovaly.

**Příčina:** Při načítání další stránky se celý buffer znovu seřadil (podle plakátu/hodnocení) — položky ze str. 1 „skočily“ na str. 3.

**Oprava:** Stabilní stránkování (`display_order` append-only) — dříve přiřazené položky se už nepřerovnávají.

### v0.0.81 (15. 6. 2026) — Rychlejší načítání + rychlejší shutdown Kodi

**Problém:** Rubriky se načítaly pomalu (desítky sekund). Kodi se po instalaci doplňku pomalu vypínalo.

**Příčina:** Plugin čekal na metadata (TMDB + ČSFD) pro každou položku *před* zobrazením seznamu. ČSFD scraping trvá 1–3 s na film; při 50 položkách to bylo extrémně pomalé.

**Opravy:**
- **ČSFD na seznamech jen jako fallback** — volá se jen když TMDB nevrátil plakát nebo hodnocení (dříve pro každou položku)
- **Časový limit enrich 6 s** — seznam se zobrazí i když metadata ještě nedoběhla pro všechny položky
- **Default 30 položek na stránku** (dříve 50) — rychlejší první načtení
- **Kratší síťové timeouty** — Webshare 5 s, OpenSubtitles 5 s, TMDB/ČSFD 4 s, obrázky 2 s → rychlejší vypínání Kodi
- **UpdateLocalAddons po upgrade na pozadí** — neblokuje otevření menu po instalaci nové verze

**Tip:** Druhé otevření stejné rubriky je okamžité (cache 30 min). Pro nejrychlejší režim zapni v Nastavení **Přeskočit enrich**.

### v0.0.80 (15. 6. 2026) — i18n: English fallback pro welcome + donate

- Přidán kompletní **anglický překlad** (`resource.language.en_gb/strings.po`, 159 klíčů). Uživatelé s Kodi v angličtině teď uvidí všechny texty anglicky.
- **Donate dialog** plně lokalizovaný — dříve byly IBAN řádky a návod na poslání daru hardcoded v češtině. Nyní 13 řádek dialogu prochází přes `_tr_safe()` (klíče 30240–30250).
- **Welcome flow** fallbacky změněny z češtiny na angličtinu (matchují `msgid` v `strings.po`), takže jakýkoli neznámý jazyk v Kodi dostane anglický fallback místo nečitelné češtiny.
- **Login error texty** (`WRONG PASSWORD`, `USER DOES NOT EXIST`, …) lokalizovány přes klíče 30260–30267.

### v0.0.79 — Rychlejší shutdown + UI bug u daru

- Sníženy síťové timeouty (Webshare 15s→8s, OpenSubtitles 25s→8s, image cache 10s→3s) — Kodi shutdown trvá max ~8s místo 30s.
- Shutdown-aware threading: žádné nové síťové requesty po `shutdown.is_shutting_down()`.
- Asynchronní fetch titulků (`subtitles.attach_async`) — video startuje ihned, titulky se připojí na pozadí. Fix race condition s flickerem a uvíznutou myší.
- Auto-refresh ikon pluginu po upgrade (`UpdateLocalAddons`) + manuální tlačítko **Nástroje → Obnovit ikony pluginu**.
- Donate položka v hlavním menu zvýrazněna (zlatá, tučně, ♥).

### v0.0.76–0.0.78 — Donate + welcome flow + nová ikona

- První-spuštění welcome dialog pro zadání Webshare účtu (bezpečné — žádné hardcoded credentials).
- Donate dialog s QR kódem (SPD formát) + textovým fallbackem.
- Nová vizuální identita: zlatá kamera ikona + červená divadelní fanart + custom dárek ikona pro donate.
- Author rebranding: „Honza" → **„Bicalorman"**.

### v0.0.74–0.0.75 — Quality picker fix

- Oprava bugu kde quality picker zobrazoval i nepřesné shody (např. „Michael Jordan" při hledání „Michael"). Nyní precizní tokenizované porovnání.

---

## Licence

GPL-3.0-or-later — viz `LICENSE` (pokud není zahrnut, viz [www.gnu.org/licenses/gpl-3.0.html](https://www.gnu.org/licenses/gpl-3.0.html))
