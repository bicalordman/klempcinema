# -*- coding: utf-8 -*-
"""
clean_title_tv.py
-----------------
Veřejné API pro čištění názvů SERIÁLŮ - tenký wrapper nad
centralizovaným modulem clean_title.

Existuje schválně jako samostatný modul (žádá ho design pluginu
ve stylu Stream Cinema), aby kód, který pracuje výhradně se seriály,
nemusel sahat do "generic" cleaneru a aby byl intent v kódu okamžitě
jasný.

Použití:

    from resources.lib.clean_title_tv import (
        clean_title_tv, episode_title, season_episode, is_series
    )

    name = "The.Mandalorian.S02E05.720p.WEB-DL.CZ.dabing.mkv"
    clean_title_tv(name)    # -> "The Mandalorian"
    episode_title(name)     # -> "The Mandalorian S02E05"
    season_episode(name)    # -> (2, 5)
    is_series(name)         # -> True
"""

from __future__ import annotations

from typing import Optional, Tuple

from . import clean_title as _ct


def clean_title_tv(name: str) -> str:
    """
    Vyčistí název souboru na čistý NÁZEV SERIÁLU.

    Odstraní:
      - SxxEyy / NxNN
      - rozlišení a kvalitu (1080p, BluRay, x264, ...)
      - jazyk/dabing markery (CZ, SK, dabing, titulky, ...)
      - release groups (YIFY, RARBG, EVO, ...)
      - tečky, podtržítka, pomlčky, závorky
      - vícenásobné mezery

    "The.Mandalorian.S02E05.720p.WEB-DL.CZ.dabing.mkv" -> "The Mandalorian"
    """
    return _ct.clean_series_name(name)


def episode_title(name: str) -> str:
    """
    Vrátí název seriálu VČETNĚ epizodního markeru SxxEyy
    (vhodné jako label epizody v seznamu).

    "The.Mandalorian.S02E05.720p.WEB-DL.CZ.mkv" -> "The Mandalorian S02E05"
    """
    return _ct.episode_base_title(name)


def season_episode(name: str) -> Tuple[Optional[int], Optional[int]]:
    """
    Vrátí dvojici (season, episode) jako int, nebo (None, None)
    pokud název neobsahuje SxxEyy / NxNN.
    """
    return _ct.extract_season_episode(name)


def is_series(name: str) -> bool:
    """True pokud název obsahuje SxxEyy / NxNN marker."""
    return _ct.is_series_name(name)
