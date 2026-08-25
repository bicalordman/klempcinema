# -*- coding: utf-8 -*-
"""
czech_fairy_tales.py
--------------------
Curated seznam českých (a slovenských) pohádek pro rubriku "Pohádky CZ/SK".
Plugin hledá na Webshare JEN tituly z tohoto seznamu.

Pravidla:
  - 1 řádek = 1 unikátní titul (žádné duplicity)
  - Název bez zbytečné interpunkce (lepší WS fulltext)
  - Jen pohádky / kouzelné příběhy / klasický CZ večerníček —
    ne komedie (Babovřesky), muzikály, historická dramata

Tagy: christmas | classic | modern | kids (večerníček) | series (díly → epizody)
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

# (title, year, tags)
CZECH_FAIRY_TALES: List[Tuple[str, int, Tuple[str, ...]]] = [
    # ---- VÁNOČNÍ ---------------------------------------------------------
    ("Tři oříšky pro Popelku",        1973, ("christmas", "classic")),
    ("Pyšná princezna",                1952, ("christmas", "classic")),
    ("Princezna se zlatou hvězdou",    1959, ("christmas", "classic")),
    ("Šíleně smutná princezna",        1968, ("christmas", "classic")),
    ("Princezna ze mlejna",            1994, ("christmas", "classic")),
    ("Princezna ze mlejna 2",          2001, ("christmas", "modern")),
    ("S čerty nejsou žerty",           1985, ("christmas", "classic")),
    ("Anděl Páně",                     2005, ("christmas", "modern")),
    ("Anděl Páně 2",                   2016, ("christmas", "modern")),
    ("Sůl nad zlato",                  1982, ("christmas", "classic")),
    ("Z pekla štěstí",                 1999, ("christmas", "modern")),
    ("Z pekla štěstí 2",               2001, ("christmas", "modern")),
    ("Tajemství staré bambitky",       2011, ("christmas", "modern")),
    ("Tajemství staré bambitky 2",     2022, ("christmas", "modern")),
    ("Princ a Večernice",              1978, ("christmas", "classic")),
    ("Kouzelný měšec",                 1996, ("christmas", "classic")),
    ("Nesmrtelná teta",                1993, ("christmas", "classic")),
    ("Zlatovláska",                    1973, ("christmas", "classic")),
    ("O dvanácti měsíčkách",           1992, ("christmas", "classic")),
    ("O vánoční hvězdě",               2020, ("christmas", "modern")),
    ("Tonda Slávka a kouzelné světlo", 2023, ("christmas", "modern")),
    ("Pohádky pod sněhem",             2010, ("christmas", "modern")),
    ("Perinbaba",                      1985, ("christmas", "classic")),
    ("Perinbaba 2",                    2023, ("christmas", "modern")),

    # ---- KLASICKÉ FILMOVÉ POHÁDKY ----------------------------------------
    ("Obušku z pytle ven",             1956, ("classic",)),
    ("Byl jednou jeden král",          1954, ("classic",)),
    ("Hrátky s čertem",                1956, ("classic",)),
    ("Čert a Káča",                    1949, ("classic",)),
    ("Dařbuján a Pandrhola",           1959, ("classic",)),
    ("Princ Bajaja",                   1971, ("classic",)),
    ("Malá mořská víla",               1976, ("classic",)),
    ("Dlouhý Široký a Bystrozraký",    1951, ("classic",)),
    ("O chytré horákyni",              1980, ("classic",)),
    ("O Popelce",                      1969, ("classic",)),
    ("Šípková Růženka",                1990, ("classic",)),
    ("Král Drozdí brada",              1984, ("classic",)),
    ("Princové jsou na draka",         1980, ("classic",)),
    ("Lotrando a Zubejda",             1997, ("classic",)),
    ("O statečném kováři",             1983, ("classic",)),
    ("Honza málem králem",             1976, ("classic",)),
    ("Tři veteráni",                   1983, ("classic",)),
    ("Třetí princ",                    1982, ("classic",)),
    ("Jak se budí princezny",          1977, ("classic",)),
    ("Šťastný Hans",                   1982, ("classic",)),
    ("Královna Koloběžka první",       1989, ("classic",)),
    ("O princezně Jasněnce a létajícím ševci", 1987, ("classic",)),
    ("Tajemství proutěného košíku",    1984, ("classic",)),
    ("O medvědu Ondřejovi",            1959, ("classic",)),
    ("Hloupý Honza",                   1985, ("classic",)),
    ("O ševci Matějovi",               1973, ("classic",)),
    ("O Palečkovi",                    1986, ("classic",)),
    ("O Rusalce",                      1962, ("classic",)),
    ("Saxana",                         1972, ("classic",)),
    ("Ať žijí duchové",                1977, ("classic",)),
    ("Dívka na koštěti",               1971, ("classic",)),
    ("Fimfárum",                       2002, ("classic",)),
    ("Fimfárum 2",                     2006, ("classic",)),
    ("Princezna a žebrák",             2004, ("classic", "modern")),
    ("O ztracené lásce",               2004, ("classic",)),

    # ---- MODERNÍ ---------------------------------------------------------
    ("Tři bratři",                     2014, ("modern",)),
    ("Sedmero krkavců",                2015, ("modern",)),
    ("Když draka bolí hlava",          2018, ("modern",)),
    ("Čertoviny",                      2018, ("modern",)),
    ("Korunní princ",                  2015, ("modern",)),
    ("Pohádky pro Emu",                2016, ("modern",)),
    ("Princezna zakletá v čase",       2020, ("modern",)),
    ("Princezna zakletá v čase 2",     2022, ("modern",)),
    ("Tři životy",                     2007, ("modern",)),
    ("Nejkrásnější hádanka",           2008, ("modern",)),
    ("Peklo s princeznou",             2009, ("modern",)),
    ("Kouzla králů",                   2008, ("modern",)),
    ("Micimutr",                       2011, ("modern",)),
    ("Čertova nevěsta",                2011, ("modern",)),
    ("Zakleté pírko",                  2020, ("modern",)),
    ("Princezna Husopaska",            2008, ("modern",)),
    ("O zatoulané princezně",          2011, ("modern",)),
    ("Saxana a Lexikon kouzel",        2011, ("modern",)),
    ("Korálky",                        2017, ("modern",)),
    ("Dívka a kouzelník",              2008, ("modern",)),
    ("Sněhová královna",               2002, ("modern",)),
    ("Sedm strun",                     2009, ("modern",)),
    ("Tygr a Vít",                     2006, ("modern",)),
    ("Královský slib",                 2001, ("modern",)),
    ("Trautenberk",                    2016, ("modern",)),

    # ---- VEČERNÍČEK / TV POHÁDKY (series = výběr dílů jako u seriálů) ----
    # Zdroj: cs.wikipedia.org/wiki/Seznam_večerníčků (+ nejznámější klasika)
    ("Pan Tau",                        1970, ("classic", "kids", "series")),
    ("Arabela",                        1979, ("classic", "kids", "series")),
    ("Arabela se vrací",               1993, ("classic", "kids", "series")),
    ("Návštěvníci",                    1983, ("classic", "kids", "series")),
    ("Krkonošské pohádky",             1973, ("classic", "kids", "series")),
    ("Pohádky pro Lucinku",            1985, ("classic", "kids", "series")),
    ("Rumcajs",                        1971, ("classic", "kids", "series")),
    ("Devět křesel",                   1990, ("classic", "kids", "series")),
    ("Krteček",                        2009, ("classic", "kids", "series")),
    ("Maxipes Fík",                    1981, ("classic", "kids", "series")),
    ("Bob a Bobek",                    1979, ("classic", "kids", "series")),
    ("Pohádky tisíce a jedné noci",    1974, ("classic", "kids", "series")),
    # Klasické večerníčky (doplněno)
    ("Broučci",                        1967, ("classic", "kids", "series")),
    ("Mach a Šebestová",               1982, ("classic", "kids", "series")),
    ("Pojďte pane budeme si hrát",     1965, ("classic", "kids", "series")),
    ("Křemílek a Vochomůrka",          1968, ("classic", "kids", "series")),
    ("Pohádky z mechu a kapradí",      1968, ("classic", "kids", "series")),
    ("Rákosníček",                     1977, ("classic", "kids", "series")),
    ("O makové panence",               1972, ("classic", "kids", "series")),
    ("Říkání o víle Amálce",           1975, ("classic", "kids", "series")),
    ("Pat a Mat",                      1976, ("classic", "kids", "series")),
    ("Štaflík a Špagetka",             1971, ("classic", "kids", "series")),
    ("Malá čarodějnice",               1984, ("classic", "kids", "series")),
    ("Kosí bratři",                    1980, ("classic", "kids", "series")),
    ("Spejbl a Hurvínek",              1972, ("classic", "kids", "series")),
    ("Anička skřítek a Slaměný Hubert", 1983, ("classic", "kids", "series")),
    ("Bubáci a hastrmani",             1999, ("classic", "kids", "series")),
    ("Kubula a Kuba Kubikula",         1986, ("classic", "kids", "series")),
    ("O zvířátkách pana Krbce",        1980, ("classic", "kids", "series")),
    ("Pohádky ovčí babičky",           1966, ("classic", "kids", "series")),
    ("Kluk a kometa",                  1965, ("classic", "kids", "series")),
    ("Cipísek",                        1972, ("classic", "kids", "series")),
    ("Méďové",                         2001, ("classic", "kids", "series")),
    ("Matylda",                        2000, ("classic", "kids", "series")),
    ("Žížaláci",                       2009, ("classic", "kids", "series")),
    ("Bambulka a Bazilínek",           1994, ("classic", "kids", "series")),
    ("Gulík a Jepinka",                1969, ("classic", "kids", "series")),

    # ---- SLOVENSKÉ -------------------------------------------------------
    ("Mahuliena zlatá panna",          1986, ("classic",)),
    ("Soľ nad zlato",                  1982, ("classic", "christmas")),
    ("Plavčík a Vratko",               1981, ("classic",)),
    ("O dvanástich mesiačikoch",       2012, ("modern", "christmas")),
]


def unique_entries(
    tag: Optional[str] = None,
) -> List[Tuple[str, int, Tuple[str, ...]]]:
    """Unikátní tituly (první výskyt), volitelně podle tagu."""
    seen = set()
    out: List[Tuple[str, int, Tuple[str, ...]]] = []
    for title, year, tags in CZECH_FAIRY_TALES:
        if tag and tag not in tags:
            continue
        key = title.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append((title, year, tags))
    return out


def all_titles(tag: Optional[str] = None) -> List[str]:
    """Vrátí pouze názvy (pro WS search query)."""
    return [t[0] for t in unique_entries(tag)]


def get_by_tag(tag: str) -> List[Tuple[str, int, Tuple[str, ...]]]:
    """Vrátí pohádky filtrované podle tagu (bez duplicit)."""
    return unique_entries(tag)


def count(tag: Optional[str] = None) -> int:
    return len(unique_entries(tag))


# Popisky submenu v UI
KIDS_SECTIONS: Sequence[Tuple[str, str, str]] = (
    ("",          "Všechny pohádky", "movies_kids"),
    ("christmas", "Vánoční",         "movies_kids"),
    ("classic",   "Klasika",         "movies_kids"),
    ("modern",    "Moderní",         "movies_kids"),
    ("kids",      "Večerníček / TV", "movies_kids"),
)
