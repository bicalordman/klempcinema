# -*- coding: utf-8 -*-
"""
czech_fairy_tales.py
--------------------
Curated seznam českých (a slovenských) pohádek - klasické TV pohádky,
vánoční pohádky a populární filmové pohádky. Plugin pro rubriku
"Pohádky CZ/SK" pak hledá na Webshare JEN tituly z tohoto seznamu -
zaručí se tím, že rubrika není zaplavena cizími filmy s podobnými
slovy v názvu.

Zdroj: ČSFD - žebříčky českých pohádek + Vánoční TV programy +
klasické pohádky známé z dětství.

Položky:
    (title, year, tags)
    title: přesné znění pro WS query
    year:  rok výroby (pomáhá TMDB lookup a deduplikaci)
    tags:  ('classic', 'christmas', 'modern') - pro budoucí submenu

Pokud chceš přidat další pohádku, prostě ji připiš na konec seznamu.
"""

from __future__ import annotations

from typing import List, Tuple

# (title, year, tags)
CZECH_FAIRY_TALES: List[Tuple[str, int, Tuple[str, ...]]] = [
    # ---- VÁNOČNÍ KLASIKY (každoroční hit) ---------------------------------
    ("Tři oříšky pro Popelku",        1973, ("christmas", "classic")),
    ("Pyšná princezna",                1952, ("christmas", "classic")),
    ("Princezna se zlatou hvězdou",    1959, ("christmas", "classic")),
    ("Šíleně smutná princezna",        1968, ("christmas", "classic")),
    ("Princezna ze mlejna",            1994, ("christmas", "classic")),
    ("Princezna ze mlejna 2",          2001, ("christmas",)),
    ("S čerty nejsou žerty",           1985, ("christmas", "classic")),
    ("Anděl Páně",                     2005, ("christmas", "modern")),
    ("Anděl Páně 2",                   2016, ("christmas", "modern")),
    ("Sůl nad zlato",                  1982, ("christmas", "classic")),
    ("Z pekla štěstí",                 1999, ("christmas",)),
    ("Z pekla štěstí 2",               2001, ("christmas",)),
    ("Tajemství staré bambitky",       2011, ("christmas", "modern")),
    ("Tajemství staré bambitky 2",     2022, ("christmas", "modern")),
    ("Princ a Večernice",              1978, ("christmas", "classic")),
    ("Kouzelný měšec",                 1996, ("christmas",)),
    ("Princové jsou na draka",         1980, ("classic",)),
    ("Lotrando a Zubejda",             1997, ("classic",)),
    ("O statečném kováři",             1983, ("classic",)),
    ("Honza málem králem",             1976, ("classic",)),
    ("Šíleně smutná princezna",        1968, ("classic",)),
    ("Tři veteráni",                   1983, ("classic",)),
    ("Nesmrtelná teta",                1993, ("christmas",)),

    # ---- ZDEŇKA TROŠKY pohádky -------------------------------------------
    ("Princezna a žebrák",             2004, ("modern",)),
    ("Z pekla štěstí",                 1999, ("modern",)),
    ("Babovřesky",                     2013, ("modern",)),
    ("Slunce, seno",                   1983, ("classic",)),

    # ---- KLASICKÉ ČESKÉ POHÁDKY (Eržika, Tři bratři...) -------------------
    ("Tři bratři",                     2014, ("modern",)),
    ("Sedmero krkavců",                2015, ("modern",)),
    ("Když draka bolí hlava",          2018, ("modern",)),
    ("Čertoviny",                      2018, ("modern",)),
    ("Korunní princ",                  2015, ("modern",)),
    ("Pohádky pro Emu",                2016, ("modern",)),
    ("Pohádkář",                       2014, ("modern",)),
    ("Kdyby radši hořelo",             2022, ("modern",)),
    ("Princezna zakletá v čase",       2020, ("modern",)),
    ("Princezna zakletá v čase 2",     2022, ("modern",)),
    ("Tajemství staré bambitky",       2011, ("modern",)),
    ("Tři životy",                     2007, ("modern",)),
    ("O ztracené lásce",               2004, ("classic",)),
    ("Nejkrásnější hádanka",           2008, ("modern",)),

    # ---- HISTORICKÉ KOUSKY (Karel Zeman, Vorlíček, ...) ------------------
    ("Cesta do pravěku",               1955, ("classic",)),
    ("Vynález zkázy",                  1958, ("classic",)),
    ("Baron Prášil",                   1961, ("classic",)),
    ("Na komentě",                     1970, ("classic",)),
    ("Bajaja",                         1971, ("classic",)),
    ("Krabat - Čarodějův učeň",        1977, ("classic",)),
    ("Pohádky tisíce a jedné noci",    1974, ("classic",)),

    # ---- VEČERNÍČEK / TV ------------------------------------------------
    ("Pan Tau",                        1970, ("classic",)),
    ("Arabela",                        1979, ("classic",)),
    ("Arabela se vrací",               1993, ("classic",)),
    ("Návštěvníci",                    1983, ("classic",)),
    ("Krkonošské pohádky",             1973, ("classic",)),
    ("Pohádky pro Lucinku",            1985, ("classic",)),
    ("Lucie postrach ulice",           1980, ("classic",)),
    ("Třetí princ",                    1982, ("classic",)),
    ("Jak se budí princezny",          1977, ("classic",)),
    ("Šťastný Hans",                   1982, ("classic",)),
    ("Devět křesel",                   1990, ("classic",)),

    # ---- JEŽÍBABKY / ČERTI / KRÁLOVNY -----------------------------------
    ("Královna Koloběžka první",       1989, ("classic",)),
    ("O princezně Jasněnce a létajícím ševci", 1987, ("classic",)),
    ("Tajemství proutěného košíku",    1984, ("classic",)),
    ("Šíleně smutná princezna",        1968, ("classic",)),
    ("Dařbuján a Pandrhola",           1959, ("classic",)),
    ("Princ Bajaja",                   1971, ("classic",)),
    ("Korálky",                        2017, ("modern",)),
    ("O zatoulané princezně",          2011, ("modern",)),
    ("O medvědu Ondřejovi",            1959, ("classic",)),
    ("Hloupý Honza",                   1985, ("classic",)),
    ("Princezna Husopaska",            2008, ("modern",)),

    # ---- ANIMOVANÉ -------------------------------------------------------
    ("Krteček",                        2009, ("classic",)),
    ("Včelka Mája",                    2014, ("modern",)),
    ("Maxipes Fík",                    2007, ("classic",)),
    ("Bob a Bobek",                    1979, ("classic",)),
    ("Pat a Mat",                      1976, ("classic",)),
    ("Saxana",                         1972, ("classic",)),
    ("Saxana a Lexikon kouzel",        2011, ("modern",)),
    ("Tři chlapi v chalupě",           1963, ("classic",)),
    ("Lotrando a Zubejda",             1997, ("classic",)),
    ("Trautenberk",                    2016, ("modern",)),
    ("O Rusalce",                      1962, ("classic",)),

    # ---- SLOVENSKÉ POHÁDKY ----------------------------------------------
    ("Mahuliena, zlatá panna",         1986, ("classic",)),
    ("Soľ nad zlato",                  1982, ("classic",)),
    ("Plavčík a Vratko",               1981, ("classic",)),
    ("Perinbaba",                      1985, ("christmas", "classic")),
    ("Perinbaba 2",                    2023, ("christmas", "modern")),
    ("Fontána pre Zuzanu",             1986, ("classic",)),
    ("Tisícročná včela",               1983, ("classic",)),
    ("O dvanástich mesiačikoch",       2012, ("modern",)),

    # ---- MODERNÍ ČESKÉ POHÁDKY (po 2015) --------------------------------
    ("Anděl Páně 2",                   2016, ("modern", "christmas")),
    ("Dívka a kouzelník",              2008, ("modern",)),
    ("Sněhová královna",               2002, ("modern",)),
    ("Pohádky pod sněhem",             2010, ("modern", "christmas")),
    ("Sedm strun",                     2009, ("modern",)),
    ("Tygr a Vít",                     2006, ("modern",)),
    ("Královský slib",                 2001, ("modern",)),
]


def all_titles() -> List[str]:
    """Vrátí pouze názvy (pro WS search query)."""
    seen = set()
    out: List[str] = []
    for title, _year, _tags in CZECH_FAIRY_TALES:
        if title not in seen:
            seen.add(title)
            out.append(title)
    return out


def get_by_tag(tag: str) -> List[Tuple[str, int, Tuple[str, ...]]]:
    """Vrátí pohádky filtrované podle tagu."""
    return [t for t in CZECH_FAIRY_TALES if tag in t[2]]


def count() -> int:
    return len(all_titles())
