# -*- coding: utf-8 -*-
"""
cz_bands.py
-----------
Kurátorovaná databáze českých a slovenských kapel / interpretů pro rubriku
Koncerty.

Zdroj: veřejně známé kapely (Wikipedia, ČSFD, hudební portály). Není to
externí API – statický seznam v pluginu, který se rotuje jako WS dotazy
("Lucie live", "Kabát koncert", …). Doplňuje obecné dotazy typu "koncert cz",
které vrací málo relevantních výsledků.

Žánry odpovídají menu Koncerty -> Žánry (concerts_genres.GENRE_MENU_ORDER).
"""

from __future__ import annotations

from typing import Dict, List, Set

# ---------------------------------------------------------------------------
# České / slovenské kapely a sólisté podle žánru
# ---------------------------------------------------------------------------

CZ_SK_BANDS: Dict[str, List[str]] = {
    "rock": [
        # Klasický & alternativní rock
        "Lucie", "Kabát", "Olympic", "Chinaski", "Mandrage", "Slza",
        "Mirai", "Xindl X", "Kryštof", "No Name", "Divokej Bill",
        "Tomáš Klus", "Tomas Klus", "Ewa Farna", "Lucie Bílá",
        "Elán", "Elan", "Škwor", "Skwor", "Tři sestry", "Tri sestry",
        "Pražský výběr", "Prazsky vyber", "Buty", "Čechomor", "Cechomor",
        "Wohnout", "Support Lesbiens", "Tata Bojs", "Monkey Business",
        "Visací zámek", "Visaci zamek", "Plexis", "Energit",
        "Karel Gott", "Karel Zich", "Mig 21", "MIG 21",
        "J.A.R.", "JAR", "Ready Kirken", "Garage", "Progres 2",
        "Blue Effect", "Modrá hvezda", "Modra hvezda",
        # Slovenské rock
        "Elán", "Elán SK", "IMT Smile", "Hex", "Desmod",
        "Peter Nagy", "Team", "Kristína", "Kristina",
    ],
    "metal": [
        "Kabát", "Kabat", "Škwor", "Skwor", "Doga", "Arakain",
        "Krabathor", "Master's Hammer", "Masters Hammer",
        "Root", "Hypnos", "Silent Stream of Godless Elegy",
        "Forgotten Silence", "Cradle Filth CZ",
        "Hammerfall CZ",  # často cover/live na WS
        "Lordi CZ live",
        # Slovenské metal
        "Signum Regis", "Aligator", "Metalinda",
    ],
    "hardrock": [
        "Kabát", "Kabat", "Škwor", "Skwor", "Doga", "Arakain",
        "Plexis", "Energit", "Olympic", "Pražský výběr",
        "Visací zámek", "Tři sestry",
    ],
    "pop": [
        "Ewa Farna", "Lucie Bílá", "Lucie Bila", "Karel Gott",
        "Kryštof", "Slza", "Mirai", "Mandrage", "Chinaski",
        "Xindl X", "Tomáš Klus", "Tomas Klus", "No Name",
        "Aneta Langerová", "Aneta Langerova", "Martina Bárta",
        "Ben Cristovao", "Ben Cristovao live",
        "Monika Bagárová", "Monika Bagarova",
        "Marek Ztracený", "Marek Ztraceny", "David Kraus",
        "Olga Lounová", "Olga Lounova", "Lenka Filipová",
        "Helena Vondráčková", "Helena Vondrackova",
        "Karel Zich", "Petr Kotvald", "Hana Zagorová",
        # Slovenské pop
        "Kristína", "Kristina", "Peter Nagy", "Team",
        "Marika Gombitová", "Marika Gombitova", "Miro Žbirka",
        "Miro Zbirka", "IMT Smile", "Hex",
    ],
    "folk": [
        "Čechomor", "Cechomor", "Hradišťan", "Hradistan",
        "Spirituál kvintet", "Spiritual kvintet",
        "Brontosauři", "Brontosauri", "Kamelot",
        "Kryštof unplugged", "Tomáš Klus unplugged",
        "Jarek Nohavica", "Nohavica", "Jaromír Nohavica",
        "Jaromir Nohavica", "Karel Plíhal", "Karel Plihal",
        "Wabi Daněk", "Wabi Danek", "Spirituál",
        "Greenhorns", "Zelenáči", "Zelenaci",
        "Vlasta Redl", "Redl", "Kukulín", "Kukulin",
        "Členové", "Clenove", "Folk Team",
        # Slovenské folk
        "Elán unplugged", "IMT Smile unplugged",
        "Miro Žbirka unplugged", "Miro Zbirka unplugged",
    ],
    "country": [
        "Greenhorns", "Zelenáči", "Zelenaci",
        "Wabi Daněk", "Wabi Danek", "Spirituál kvintet",
        "Karel Plíhal", "Karel Plihal",
    ],
    "rap": [
        "Pokáč", "Pokac", "Mirai", "Calin",
        "Ektor", "Radek Banga", "Banga",
        "Prago Union", "Pragounion", "Gipsy.cz", "Gipsy cz",
        "Chinaski", "Xindl X",
        "Separ", "Rytmus", "Kontrafakt",
        "Gleb", "Calin", "Yzomandias",
        "Pio Squad", "Pio", "Rest",
        # Slovenské rap
        "Kontrafakt", "Rytmus", "Separ",
    ],
    "electronic": [
        "Boris Brejcha", "Paul Kalkbrenner CZ",
        "Kryštof live", "Monkey Business",
        "Tata Bojs", "Midi Lidi", "Midi Lidi live",
        "Floex", "Floex live",
        "Kryštof electronic",
        # DJ / klubové
        "Lucca", "Lucca live", "DJ Lucca",
    ],
}

# Všechny unikátní jména (pro obecný CZ/SK seznam dotazů).
_ALL_NAMES: List[str] = []


def _build_all_names() -> List[str]:
    global _ALL_NAMES
    if _ALL_NAMES:
        return _ALL_NAMES
    seen: Set[str] = set()
    out: List[str] = []
    for names in CZ_SK_BANDS.values():
        for n in names:
            key = n.lower().strip()
            if key and key not in seen:
                seen.add(key)
                out.append(n)
    _ALL_NAMES = out
    return out


def band_names_for_genre(genre: str) -> List[str]:
    """Vrátí seznam kapel pro daný žánr (rock, metal, folk, …)."""
    g = (genre or "").lower().strip()
    if g == "hardrock":
        return list(CZ_SK_BANDS.get("hardrock", []))
    return list(CZ_SK_BANDS.get(g, []))


def all_cz_sk_band_names() -> List[str]:
    """Všechny unikátní české/slovenské kapely ze všech žánrů."""
    return _build_all_names()


def ws_queries_for_band(name: str) -> List[str]:
    """WS dotazy pro jednu kapelu – rotace live/koncert."""
    n = (name or "").strip()
    if not n:
        return []
    return [f"{n} live", f"{n} koncert"]


def ws_queries_for_cz_sk(
    max_bands: int = 40,
    *,
    genre: str = "",
) -> List[str]:
    """
    Sestaví rotaci WS dotazů pro CZ/SK koncerty.

    :param max_bands: kolik kapel max (kazda = 2 dotazy live+koncert)
    :param genre: volitelny zanr (rock, metal, folk, …) – jen kapely z nej
    """
    if genre:
        names = band_names_for_genre(genre)[:max_bands]
    else:
        names = all_cz_sk_band_names()[:max_bands]

    queries: List[str] = []
    for name in names:
        queries.extend(ws_queries_for_band(name))
    return queries


def is_known_cz_sk_artist(text: str) -> bool:
    """True pokud text obsahuje jméno známé CZ/SK kapely (pro filtr region)."""
    if not text:
        return False
    tl = text.lower()
    for name in all_cz_sk_band_names():
        if len(name) >= 3 and name.lower() in tl:
            return True
    return False
