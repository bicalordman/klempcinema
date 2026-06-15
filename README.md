# KlempCinema

Kodi video doplněk pro procházení a přehrávání filmů a seriálů z Webshare.

---

## Instalace

1. V Kodi: **Doplňky → Nainstalovat ze ZIPu**
2. Vyber `plugin.video.klempcinema-X.Y.Z.zip`
3. Při prvním spuštění doplněk vyzve k zadání **Webshare účtu** (vlastní jméno + heslo). Účet si je třeba pořídit zvlášť na [webshare.cz](https://webshare.cz).

Doplněk funguje bez reklam, bez sběru dat. Veškerý obsah je stahován přímo z Webshare, ke kterému si uživatel sjednává vlastní VIP předplatné.

---

## Funkce

- Procházení filmů a seriálů (Webshare + TMDB metadata)
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

---

## Kontakt

Autor: **Bicalorman**

Pro hlášení chyb a návrhy — kontaktuj kanál odkud jsi doplněk získal.

---

## Změny

### v0.0.80 (15. 6. 2026) — i18n: English fallback pro welcome + donate

- Přidán kompletní **anglický překlad** (`resource.language.en_gb/strings.po`, 159 klíčů). Uživatelé s Kodi v angličtině teď uvidí všechny texty anglicky.
- **Donate dialog** plně lokalizovaný — dříve byly IBAN řádky a návod na poslání daru hardcoded v češtině. Nyní 13 řádek dialogu prochází přes `_tr_safe()` (klíče 30240–30250).
- **Welcome flow** fallbacky změněny z čestiny na angličtinu (matchují `msgid` v `strings.po`), takže jakýkoli neznámý jazyk v Kodi dostane anglický fallback místo nečitelné češtiny.
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
