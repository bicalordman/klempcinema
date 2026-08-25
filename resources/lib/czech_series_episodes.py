# -*- coding: utf-8 -*-
"""
czech_series_episodes.py
------------------------
Curated „CSFD fallback“ – oficiální názvy dílů pro klasické CZ/SK
pohádkové / dětské seriály.

Použití:
  - doplnit chybějící názvy (TMDB často nemá cs-CZ jméno)
  - omezit seznam na skutečný počet dílů (ne garbage z WS)
  - párovat WS soubory podle názvu dílu v filename
"""

from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

# season -> episode -> title
EpMap = Dict[int, Dict[int, str]]

# Klíč = ascii-fold lowercase bez diakritiky (viz series_key)
_EPISODES: Dict[str, EpMap] = {
    "krkonosske pohadky": {
        1: {
            1:  "Jak Trautenberk lovil v Krakonošově revíru",
            2:  "Jak Trautenberk chtěl peříčko z Krakonošovy sojky",
            3:  "Jak Trautenberk topil Krakonošovým dřevem",
            4:  "Jak Kuba utekl ke Krakonošovi",
            5:  "Jak Trautenberk vystrojil hostinu pro štěpanického barona",
            6:  "Jak šel Kuba ke Krakonošovi pro poklad",
            7:  "Jak chtěl Trautenberk Krakonošovo koření",
            8:  "Jak Trautenberk kradl zvířátkům zásoby na zimu",
            9:  "Jak Trautenberk vyměnil Krakonošovi fajfku",
            10: "Jak chtěl Trautenberk nový kožich",
            11: "Jak šel Trautenberk do hor pro poklad",
            12: "Jak Trautenberk sušil Krakonošovu louku",
            13: "Jak chtěl Trautenberk poslat Kubu na vojnu",
            14: "Jak si Trautenberk pochutnal na čerstvých pstruzích",
            15: "Jak Trautenberk pořádal vepřové hody",
            16: "Jak Trautenberk chytal ptáčky zpěváčky",
            17: "Jak Trautenberk otrávil strakatou kozu",
            18: "Jak Trautenberk odvedl horské prameny",
            19: "Jak se chtěl Trautenberk pomstít Krakonošovi",
            20: "Jak Trautenberk prodával vodu",
        },
    },
    "navstevnici": {
        1: {
            1:  "Země roku 2484",
            2:  "Výprava do minula",
            3:  "Návštěvníci přicházejí",
            4:  "Akce: Sešit 1.",
            5:  "Hlavně nenápadně",
            6:  "Tajemství velkého učitele",
            7:  "Půlnoční kolotoč",
            8:  "Génius v hladomorně",
            9:  "Sólo pro návštěvníky",
            10: "Stav nouze",
            11: "Stane se zítra",
            12: "Peníze z hvězd",
            13: "Prozrazení",
            14: "Po nás potopa",
            15: "Návrat do budoucnosti",
        },
    },
    "arabela": {
        1: {
            1:  "Jak pan Majer našel zvoneček",
            2:  "Rumburakova pomsta",
            3:  "Petr a princezna",
            4:  "Jezevčík Karel Majer",
            5:  "Arabela na útěku",
            6:  "Petrovo zmizení",
            7:  "Pohádky jdou do sběru",
            8:  "Jeníček a Mařenka",
            9:  "Civilizace si žádá své",
            10: "Rumburakova velká šance",
            11: "Příliš mnoho generálů",
            12: "Hrdlička zasahuje",
            13: "Zvonečkem to začalo, zvonečkem to končí",
        },
    },
}

# Alternativní názvy → kanonický klíč
_ALIASES: Dict[str, str] = {
    "krkonosska pohadka": "krkonosske pohadky",
    "krkonosska pohadky": "krkonosske pohadky",
    "krkonosske pohadka": "krkonosske pohadky",
    "die besucher": "navstevnici",
    "the visitors": "navstevnici",
    "expedition adam 84": "navstevnici",
}


def _fold(s: str) -> str:
    try:
        from . import clean_title as _ct
        return _ct.ascii_fold(s or "")
    except Exception:  # noqa: BLE001
        try:
            import clean_title as _ct  # type: ignore
            return _ct.ascii_fold(s or "")
        except Exception:  # noqa: BLE001
            return s or ""


def series_key(title: str) -> str:
    """Normalizovaný klíč bez diakritiky (pro lookup)."""
    if not title:
        return ""
    s = _fold(title).lower().strip()
    s = re.sub(r"[^\w\s]+", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def resolve_key(title: str) -> Optional[str]:
    key = series_key(title)
    if not key:
        return None
    if key in _EPISODES:
        return key
    if key in _ALIASES:
        return _ALIASES[key]
    # Prefix jen když je kanonický klíč delší než 8 a title začíná jím
    # (ne „arabela“ → „arabela se vraci“)
    for canon in sorted(_EPISODES.keys(), key=len, reverse=True):
        if key.startswith(canon + " ") and len(canon) >= 10:
            return canon
    return None


def is_curated(title: str) -> bool:
    return resolve_key(title) is not None


def episode_map(title: str) -> Optional[EpMap]:
    key = resolve_key(title)
    if not key:
        return None
    return _EPISODES.get(key)


def episode_title(title: str, season: int, episode: int) -> Optional[str]:
    emap = episode_map(title)
    if not emap:
        return None
    return (emap.get(int(season)) or {}).get(int(episode))


def max_episode(title: str, season: int = 1) -> Optional[int]:
    emap = episode_map(title)
    if not emap:
        return None
    eps = emap.get(int(season)) or {}
    return max(eps.keys()) if eps else None


def match_filename_to_episode(
    series_title: str, filename: str,
) -> Optional[Tuple[int, int]]:
    """
    Najde S/E podle názvu dílu v filename (když chybí SxxEyy).
    Vrací (season, episode) nebo None.
    """
    emap = episode_map(series_title)
    if not emap or not filename:
        return None
    loose = _fold(filename).lower()
    loose = re.sub(r"[._]+", " ", loose)
    # Delší názvy dřív (lepší unikátnost)
    candidates = []
    for s, eps in emap.items():
        for e, name in eps.items():
            n = _fold(name).lower()
            n = re.sub(r"[._]+", " ", n).strip()
            if len(n) >= 8:
                candidates.append((len(n), s, e, n))
    candidates.sort(reverse=True)
    for _ln, s, e, n in candidates:
        if n in loose:
            return int(s), int(e)
    return None
