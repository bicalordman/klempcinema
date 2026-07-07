# -*- coding: utf-8 -*-
"""Keyword matching a WS dotazy pro zanrove filtry koncertu."""

from __future__ import annotations

from typing import Dict, List

GENRE_KEYWORDS: Dict[str, List[str]] = {
    "rock": [
        "rock", "metalcore", "hard rock", "alternative", "grunge",
        "punk", "indie rock", "classic rock", "progressive",
    ],
    "pop": [
        "pop", "synthpop", "dance pop", "teen pop", "electropop",
    ],
    "metal": [
        "metal", "metallica", "iron maiden", "slayer", "megadeth",
        "death metal", "black metal", "heavy metal", "thrash",
    ],
    "rap": [
        "rap", "hip hop", "hip-hop", "hiphop", "trap", "drill",
    ],
    "folk": [
        "folk", "country", "bluegrass", "acoustic", "celtic",
    ],
    "electronic": [
        "electronic", "elektron", "techno", "trance", "house",
        "edm", "dnb", "drum and bass", "dubstep",
    ],
}

GENRE_TMDb_NAMES: Dict[str, List[str]] = {
    "rock": ["rock"],
    "pop": ["pop"],
    "metal": ["metal", "heavy metal"],
    "rap": ["rap", "hip hop"],
    "folk": ["folk", "country"],
    "electronic": ["electronic", "techno", "house"],
}

GENRE_WS_QUERIES: Dict[str, List[str]] = {
    "rock": ["rock live", "rock koncert", "rock concert", "hard rock live"],
    "pop": ["pop live", "pop koncert", "pop concert"],
    "metal": ["metal live", "metal koncert", "heavy metal live"],
    "rap": ["rap live", "hip hop live", "hip hop koncert"],
    "folk": ["folk live", "folk koncert", "acoustic live"],
    "electronic": ["electronic live", "techno live", "edm live", "trance live"],
}

GENRE_MENU_ORDER = ["rock", "pop", "metal", "rap", "folk", "electronic"]


def matches_genre(title: str, genre: str) -> bool:
    """True pokud nazev obsahuje klicove slovo daneho zanru."""
    if not title or not genre:
        return False
    t = title.lower()
    keywords = GENRE_KEYWORDS.get(genre.lower(), [])
    return any(kw in t for kw in keywords)


def matches_genre_item(item: dict, genre: str) -> bool:
    """Match v nazvu souboru nebo TMDB zanrech (po enrich)."""
    title = (
        item.get("base_title")
        or item.get("title_localized")
        or item.get("title")
        or ""
    )
    if matches_genre(title, genre):
        return True

    names = [n.lower() for n in (item.get("genre_names") or [])]
    if not names:
        return False
    wanted = GENRE_TMDb_NAMES.get(genre.lower(), [genre.lower()])
    return any(w in n or n in w for n in names for w in wanted)
