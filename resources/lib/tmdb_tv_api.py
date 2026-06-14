# -*- coding: utf-8 -*-
"""
tmdb_tv_api.py
--------------
Veřejné API pro vyhledávání SERIÁLŮ v TMDB - tenký wrapper nad
modulem tmdb (který sdílí API klíč, cache, session-disable a
v3/v4 token autodetect s movie endpointem).

Existuje schválně jako samostatný modul (žádá ho design pluginu
ve stylu Stream Cinema), aby kód, který pracuje výhradně se seriály,
měl jeden jasný vstupní bod.

Veřejné rozhraní:
    tmdb_lookup_tv(title)         -> list[dict]  - všechny výsledky
    tmdb_lookup_tv_first(title)   -> dict | None - první/nejlepší kandidát
    enrich_tv_item(item)          -> dict        - in-place obohacení
    poster_url(path), fanart_url(path)           - pomocné URL buildery

Návratový dict (kompatibilní se Stream Cinema):
    {
        "tmdb_id":    int,
        "title":      str,            # lokalizovaný název
        "original":   str,
        "year":       int | None,
        "plot":       str,
        "rating":     float,          # vote_average (0-10)
        "votes":      int,            # vote_count
        "popularity": float,
        "poster":     str,            # absolutní URL (w500)
        "fanart":     str,            # absolutní URL (w1280)
    }
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from . import cache
from . import tmdb as _tmdb
from .clean_title_tv import clean_title_tv

log = logging.getLogger("klempcinema.tmdb_tv_api")

# Cacheovací TTL (stejné jako v tmdb.py).
HIT_TTL = 7 * 86400      # 7 dní pro úspěšné nálezy
EMPTY_TTL = 3600         # 1 hodina pro prázdný výsledek


# ---------------------------------------------------------------------------
# URL helpery (přebíráme základy z tmdb modulu)
# ---------------------------------------------------------------------------

def poster_url(path: Optional[str]) -> str:
    """Vrátí absolutní URL na poster (w500), nebo prázdný string."""
    return _tmdb._poster_url(path, _tmdb.POSTER_SIZE)


def fanart_url(path: Optional[str]) -> str:
    """Vrátí absolutní URL na fanart (w1280), nebo prázdný string."""
    return _tmdb._poster_url(path, _tmdb.FANART_SIZE)


def _result_to_meta(r: Dict[str, Any], fallback_title: str) -> Dict[str, Any]:
    """
    Z TMDB raw výsledku /search/tv udělá náš standardizovaný meta-dict.

    Pokud cs-CZ verze nemá poster/fanart, doplníme z best-available
    jazyka (cs -> en -> null) přes _get_best_images.
    """
    tmdb_id = r.get("id")
    poster_path = r.get("poster_path")
    fanart_path = r.get("backdrop_path")

    if tmdb_id and (not poster_path or not fanart_path):
        try:
            best_p, best_f = _tmdb._get_best_images(tmdb_id, kind="tv")
            if not poster_path and best_p:
                poster_path = best_p
            if not fanart_path and best_f:
                fanart_path = best_f
        except Exception as exc:  # noqa: BLE001
            log.debug("image fallback tv/%s selhal: %s", tmdb_id, exc)

    return {
        "tmdb_id":    tmdb_id,
        "title":      r.get("name") or r.get("original_name") or fallback_title,
        "original":   r.get("original_name") or "",
        "year":       _tmdb._date_year(r.get("first_air_date")),
        "plot":       r.get("overview") or "",
        "rating":     float(r.get("vote_average") or 0),
        "votes":      int(r.get("vote_count") or 0),
        "popularity": float(r.get("popularity") or 0),
        "poster":     poster_url(poster_path),
        "fanart":     fanart_url(fanart_path),
    }


# ---------------------------------------------------------------------------
# Hlavní vyhledávací API
# ---------------------------------------------------------------------------

def tmdb_lookup_tv(title: str) -> List[Dict[str, Any]]:
    """
    Vrátí VŠECHNY výsledky z TMDB /search/tv pro daný titul.

    Vstup smí být cokoli z Webshare (filename) - název se interně
    vyčistí přes clean_title_tv. Výsledek je list standardizovaných
    metadata-dictů (viz horní docstring).

    Pokud TMDB:
      - není zapnuto / nemá klíč / je v session-disabled stavu -> []
      - vrátí prázdno -> []
      - selže -> [] (chybu jen logujeme, ne pop-up)
    """
    if not _tmdb.is_enabled():
        return []
    if not title:
        return []

    clean = clean_title_tv(title)
    if not clean:
        return []

    key = f"tmdb_tv_list:{clean.lower()}"
    cached = cache.cache_get(key, ttl=HIT_TTL)
    if cached is not None:
        return list(cached or [])

    log.info("tmdb_lookup_tv: query=%r", clean)
    try:
        data = _tmdb._http_get("/search/tv", query=clean)
    except Exception as exc:  # noqa: BLE001
        log.warning("tmdb_lookup_tv(%r) selhalo: %s", clean, exc)
        return []

    results_raw = (data or {}).get("results") or []
    out = [_result_to_meta(r, clean) for r in results_raw]

    cache.cache_set(key, out)
    if not out:
        log.info("tmdb_lookup_tv(%r): 0 výsledků z TMDB.", clean)
    return out


def tmdb_lookup_tv_first(title: str) -> Optional[Dict[str, Any]]:
    """
    Vrátí nejlepšího (prvního) kandidáta z /search/tv, nebo None.
    Stejné chování jako tmdb.search_tv, ale procesí přes
    tmdb_lookup_tv (= konzistentní cache).
    """
    items = tmdb_lookup_tv(title)
    return items[0] if items else None


# ---------------------------------------------------------------------------
# In-place enrichment - kompatibilní s api_webshare flow
# ---------------------------------------------------------------------------

def get_tv_details(tmdb_id: int) -> Optional[Dict[str, Any]]:
    """
    Vrátí podrobnosti seriálu z TMDB /tv/{id} - obsahuje 'seasons' s počty
    epizod, plakáty sezón atd. Cache TTL 7 dní.
    """
    if not _tmdb.is_enabled() or not tmdb_id:
        return None
    key = f"tmdb_tv_details:{tmdb_id}"
    cached = cache.cache_get(key, ttl=HIT_TTL)
    if cached is not None:
        return cached or None
    try:
        data = _tmdb._http_get(f"/tv/{tmdb_id}")
    except Exception as exc:  # noqa: BLE001
        log.warning("get_tv_details(%s) selhalo: %s", tmdb_id, exc)
        return None
    if data:
        cache.cache_set(key, data)
    return data


def get_seasons(tmdb_id: int) -> List[Dict[str, Any]]:
    """
    Vrátí list sezón seriálu. Filtruje sezónu 0 (specials) - většinou nudné.

    Položka:
        {
          "season_number": int,
          "name":         str,
          "overview":     str,
          "episode_count": int,
          "air_date":     str,
          "poster":       str (URL),
        }
    """
    details = get_tv_details(tmdb_id)
    if not details:
        return []
    out: List[Dict[str, Any]] = []
    for s in details.get("seasons") or []:
        num = int(s.get("season_number") or 0)
        if num == 0:
            continue  # specials
        out.append({
            "season_number": num,
            "name":          s.get("name") or f"Season {num}",
            "overview":      s.get("overview") or "",
            "episode_count": int(s.get("episode_count") or 0),
            "air_date":      s.get("air_date") or "",
            "poster":        poster_url(s.get("poster_path")),
        })
    return out


def get_season_episodes(tmdb_id: int, season_number: int) -> List[Dict[str, Any]]:
    """
    Vrátí list epizod konkrétní sezóny.

    Položka:
        {
          "episode_number": int,
          "name":           str,
          "overview":       str,
          "air_date":       str,
          "still":          str (URL),       # screenshot
          "rating":         float,
          "votes":          int,
        }
    """
    if not _tmdb.is_enabled() or not tmdb_id:
        return []
    key = f"tmdb_tv_season:{tmdb_id}:{season_number}"
    cached = cache.cache_get(key, ttl=HIT_TTL)
    if cached is not None:
        return list(cached or [])
    try:
        data = _tmdb._http_get(f"/tv/{tmdb_id}/season/{season_number}")
    except Exception as exc:  # noqa: BLE001
        log.warning("get_season_episodes(%s, %s) selhalo: %s",
                    tmdb_id, season_number, exc)
        return []
    if not data:
        return []
    out: List[Dict[str, Any]] = []
    for ep in data.get("episodes") or []:
        out.append({
            "episode_number": int(ep.get("episode_number") or 0),
            "name":           ep.get("name") or "",
            "overview":       ep.get("overview") or "",
            "air_date":       ep.get("air_date") or "",
            "still":          _tmdb._poster_url(ep.get("still_path"), _tmdb.FANART_SIZE),
            "rating":         float(ep.get("vote_average") or 0),
            "votes":          int(ep.get("vote_count") or 0),
        })
    cache.cache_set(key, out)
    return out


def enrich_episode(ep_item: Dict[str, Any],
                   tmdb_id: int,
                   season: int,
                   episode: int) -> Dict[str, Any]:
    """
    Doplní metadata jedné epizody (název, plot, screenshot, air date, rating).
    In-place + return.
    """
    if not tmdb_id or not season or not episode:
        return ep_item
    episodes = get_season_episodes(tmdb_id, season)
    match = next((e for e in episodes if e.get("episode_number") == episode), None)
    if not match:
        return ep_item

    name = match.get("name") or ""
    if name:
        ep_item["episode_title"] = name
    if match.get("overview"):
        ep_item["plot"] = match["overview"]
    if match.get("still"):
        # Screenshot epizody = thumb pro UI
        ep_item["fanart"] = match["still"]
        if not ep_item.get("poster"):
            ep_item["poster"] = match["still"]
    if match.get("rating"):
        ep_item["rating"] = match["rating"]
    if match.get("votes"):
        ep_item["votes"] = match["votes"]
    if match.get("air_date"):
        ep_item["air_date"] = match["air_date"]
    return ep_item


def enrich_tv_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Doplní seriál metadaty z TMDB. Pracuje IN-PLACE a vrací stejnou
    referenci (kvůli ThreadPoolExecutor.map idiomu).

    Pole, která se naplní (pokud TMDB něco vrátí):
        title_localized, year, plot, poster, fanart, rating, votes,
        popularity, tmdb_id
    """
    if not _tmdb.is_enabled():
        return item

    title = item.get("title") or item.get("title_raw") or ""
    if not title:
        return item

    meta = tmdb_lookup_tv_first(title)
    if not meta:
        return item

    if meta.get("title"):
        item["title_localized"] = meta["title"]
    if meta.get("year") and not item.get("year"):
        item["year"] = meta["year"]
    if meta.get("plot"):
        item["plot"] = meta["plot"]
    if meta.get("poster"):
        item["poster"] = meta["poster"]
    if meta.get("fanart"):
        item["fanart"] = meta["fanart"]
    if meta.get("rating") is not None:
        item["rating"] = float(meta["rating"])
    if meta.get("votes") is not None:
        item["votes"] = int(meta["votes"])
    if meta.get("popularity") is not None:
        item["popularity"] = float(meta["popularity"])
    if meta.get("tmdb_id"):
        item["tmdb_id"] = meta["tmdb_id"]
    return item
