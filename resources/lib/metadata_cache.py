# -*- coding: utf-8 -*-
"""
metadata_cache.py
-----------------
Per-title JSON cache pro metadata (plakát, popis, hodnocení, fanart).

Soubory jsou v:
    special://profile/addon_data/plugin.video.klempcinema/metadata/

Každý titul má vlastní .json soubor (jméno = slug z titulu).
Tím se cache snadno čte/maže ručně i mimo Kodi (např. v explorerovi).

Tento modul je DOPLNĚK k existující cache.py (MD5-keyed):
    cache.py             - jemnozrný cache pro TMDB/ČSFD raw odpovědi
    metadata_cache.py    - hrubozrnný cache "vyřešeného" metadat-balíku
                           po průchodu celým fallback řetězcem

Veřejné API:
    cache_path(title)           -> str (absolutní cesta)
    load(title)                 -> dict | None
    save(title, data)           -> None
    clear()                     -> int (počet smazaných souborů)
    list_titles()               -> list[str]

Datový formát uloženého JSONu:
    {
      "title":       str,
      "year":        int | "",
      "rating":      float | "",
      "plot":        str,
      "poster":      str url | None,
      "fanart":      str url | None,
      "source":      "tmdb" | "csfd" | "none",
      "saved_at":    float (epoch),
    }
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

log = logging.getLogger("klempcinema.metadata_cache")

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _profile_dir() -> str:
    """Vrátí addon profile dir (cross-platform, funguje i mimo Kodi)."""
    try:
        import xbmcaddon  # type: ignore
        import xbmcvfs    # type: ignore
        addon = xbmcaddon.Addon()
        return xbmcvfs.translatePath(addon.getAddonInfo("profile"))
    except Exception:  # noqa: BLE001
        return os.path.join(os.path.expanduser("~"), ".klempcinema")


def _metadata_dir() -> str:
    d = os.path.join(_profile_dir(), "metadata")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def _slug(title: str) -> str:
    """Sluggify titul pro použití jako filename. 'Tři oříšky' -> 'tri-orisky'."""
    if not title:
        return "_empty_"

    # Diakritika -> ASCII (jednoduchý transliterátor)
    s = title.lower().strip()
    repl = {
        "á": "a", "č": "c", "ď": "d", "é": "e", "ě": "e", "í": "i",
        "ň": "n", "ó": "o", "ř": "r", "š": "s", "ť": "t", "ú": "u",
        "ů": "u", "ý": "y", "ž": "z", "ä": "a", "ö": "o", "ü": "u",
        "ß": "ss",
    }
    for k, v in repl.items():
        s = s.replace(k, v)

    s = _SLUG_RE.sub("-", s).strip("-")
    if not s:
        s = "_empty_"
    # Strop délky filename
    if len(s) > 120:
        s = s[:120].rstrip("-")
    return s


def cache_path(title: str) -> str:
    """Vrátí absolutní cestu k cache souboru pro daný titul."""
    return os.path.join(_metadata_dir(), f"{_slug(title)}.json")


def load(title: str) -> Optional[Dict[str, Any]]:
    """Načte uložená metadata. Vrátí None pokud neexistují / jsou poškozená."""
    path = cache_path(title)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        log.debug("metadata_cache.load(%r) chyba: %s", title, exc)
        return None


def save(title: str, data: Dict[str, Any]) -> None:
    """Uloží metadata na disk (přepíše). Doplní 'saved_at' epoch."""
    if not title or not isinstance(data, dict):
        return
    payload = dict(data)
    payload.setdefault("title", title)
    payload["saved_at"] = time.time()

    path = cache_path(title)
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as exc:
        log.debug("metadata_cache.save(%r) chyba: %s", title, exc)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


def clear() -> int:
    """Smaže VŠECHNY uložené metadata JSONy. Vrací počet smazaných souborů."""
    d = _metadata_dir()
    n = 0
    try:
        for name in os.listdir(d):
            if name.endswith(".json"):
                try:
                    os.remove(os.path.join(d, name))
                    n += 1
                except OSError:
                    pass
    except OSError:
        pass
    return n


def list_titles() -> List[str]:
    """Vrátí slugy všech cachovaných titulů (debug nástroj)."""
    d = _metadata_dir()
    try:
        return sorted(
            n[:-5] for n in os.listdir(d) if n.endswith(".json")
        )
    except OSError:
        return []
