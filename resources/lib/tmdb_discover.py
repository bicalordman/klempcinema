# -*- coding: utf-8 -*-
"""
tmdb_discover.py
----------------
Obal nad TMDB endpointy /trending a /discover. Vrací standardizované
meta-dicty kompatibilní s api_webshare item formátem - tj. mají
title/year/poster/fanart/plot/rating - aby je šlo rovnou rendrovat
jako video-items v UI.

Pro každou TMDB položku se pak (samostatně) na Webshare hledá konkrétní
soubor + varianty, takže klik na položku spustí standardní play_pick.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from . import cache
from . import tmdb as _tmdb

log = logging.getLogger("klempcinema.tmdb_discover")

DEFAULT_TTL = 6 * 3600   # 6h - trending list se nemění tak často

TMDB_GENRE_MUSIC = 10402
TMDB_GENRE_DOCUMENTARY = 99


def _fill_images_if_missing(tmdb_id, poster_path, fanart_path, kind, *,
                            lazy: bool = False):
    """Pokud cs-CZ verze nemá obrázky, vrátí (poster, fanart) z best-available.

    lazy=True: seznamy (platformy/trending) – bez extra TMDB dotazu na fanart.
    """
    if not tmdb_id:
        return (poster_path or "", fanart_path or "")
    if lazy:
        return (poster_path or "", fanart_path or "")
    if poster_path and fanart_path:
        return (poster_path, fanart_path)
    if poster_path and not fanart_path:
        return (poster_path or "", fanart_path or "")
    try:
        best_p, best_f = _tmdb._get_best_images(tmdb_id, kind=kind)
        return (poster_path or best_p, fanart_path or best_f)
    except Exception as exc:  # noqa: BLE001
        log.debug("image fallback %s/%s selhal: %s", kind, tmdb_id, exc)
        return (poster_path or "", fanart_path or "")


def _movie_to_meta(r: Dict[str, Any]) -> Dict[str, Any]:
    tmdb_id = r.get("id")
    poster_path, fanart_path = _fill_images_if_missing(
        tmdb_id, r.get("poster_path"), r.get("backdrop_path"),
        kind="movie", lazy=True)
    raw_genre_ids = [int(g) for g in (r.get("genre_ids") or [])
                     if isinstance(g, (int, float))]
    return {
        "tmdb_id":    tmdb_id,
        "title":      r.get("title") or r.get("original_title") or "",
        "original":   r.get("original_title") or "",
        "year":       _tmdb._date_year(r.get("release_date")),
        "plot":       r.get("overview") or "",
        "rating":     float(r.get("vote_average") or 0),
        "votes":      int(r.get("vote_count") or 0),
        "popularity": float(r.get("popularity") or 0),
        "poster":     _tmdb._poster_url(poster_path, _tmdb.POSTER_SIZE),
        "fanart":     _tmdb._poster_url(fanart_path, _tmdb.FANART_SIZE),
        "genre_ids":  raw_genre_ids,
        "type":       "movie",
    }


def _tv_to_meta(r: Dict[str, Any]) -> Dict[str, Any]:
    tmdb_id = r.get("id")
    poster_path, fanart_path = _fill_images_if_missing(
        tmdb_id, r.get("poster_path"), r.get("backdrop_path"),
        kind="tv", lazy=True)
    raw_genre_ids = [int(g) for g in (r.get("genre_ids") or [])
                     if isinstance(g, (int, float))]
    return {
        "tmdb_id":    tmdb_id,
        "title":      r.get("name") or r.get("original_name") or "",
        "original":   r.get("original_name") or "",
        "year":       _tmdb._date_year(r.get("first_air_date")),
        "plot":       r.get("overview") or "",
        "rating":     float(r.get("vote_average") or 0),
        "votes":      int(r.get("vote_count") or 0),
        "popularity": float(r.get("popularity") or 0),
        "poster":     _tmdb._poster_url(poster_path, _tmdb.POSTER_SIZE),
        "fanart":     _tmdb._poster_url(fanart_path, _tmdb.FANART_SIZE),
        "genre_ids":  raw_genre_ids,
        "type":       "series",
    }


# ---------------------------------------------------------------------------
# /trending
# ---------------------------------------------------------------------------

def trending_movies(window: str = "week", page: int = 1) -> List[Dict[str, Any]]:
    """
    Trending filmů. window = 'day' | 'week'.
    """
    if not _tmdb.is_enabled():
        return []
    key = f"tmdb_trending_movie:{window}:{page}"
    cached = cache.cache_get(key, ttl=DEFAULT_TTL)
    if cached is not None:
        return list(cached or [])
    try:
        data = _tmdb._http_get(f"/trending/movie/{window}", page=page)
    except Exception as exc:  # noqa: BLE001
        log.warning("trending_movies selhalo: %s", exc)
        return []
    if not data:
        return []
    items = [_movie_to_meta(r) for r in (data.get("results") or [])]
    cache.cache_set(key, items)
    return items


def trending_tv(window: str = "week", page: int = 1) -> List[Dict[str, Any]]:
    """Trending seriálů. window = 'day' | 'week'."""
    if not _tmdb.is_enabled():
        return []
    key = f"tmdb_trending_tv:{window}:{page}"
    cached = cache.cache_get(key, ttl=DEFAULT_TTL)
    if cached is not None:
        return list(cached or [])
    try:
        data = _tmdb._http_get(f"/trending/tv/{window}", page=page)
    except Exception as exc:  # noqa: BLE001
        log.warning("trending_tv selhalo: %s", exc)
        return []
    if not data:
        return []
    items = [_tv_to_meta(r) for r in (data.get("results") or [])]
    cache.cache_set(key, items)
    return items


# ---------------------------------------------------------------------------
# /genre + /discover
# ---------------------------------------------------------------------------

def list_movie_genres() -> List[Dict[str, Any]]:
    """Vrátí list žánrů pro filmy (id + jméno v cs-CZ)."""
    if not _tmdb.is_enabled():
        return []
    key = "tmdb_genres_movie"
    cached = cache.cache_get(key, ttl=7 * 86400)
    if cached is not None:
        return list(cached or [])
    try:
        data = _tmdb._http_get("/genre/movie/list")
    except Exception as exc:  # noqa: BLE001
        log.warning("list_movie_genres selhalo: %s", exc)
        return []
    if not data:
        return []
    out = [{"id": int(g["id"]), "name": g.get("name") or ""}
           for g in (data.get("genres") or [])]
    cache.cache_set(key, out)
    return out


def list_tv_genres() -> List[Dict[str, Any]]:
    """Vrátí list žánrů pro seriály."""
    if not _tmdb.is_enabled():
        return []
    key = "tmdb_genres_tv"
    cached = cache.cache_get(key, ttl=7 * 86400)
    if cached is not None:
        return list(cached or [])
    try:
        data = _tmdb._http_get("/genre/tv/list")
    except Exception as exc:  # noqa: BLE001
        log.warning("list_tv_genres selhalo: %s", exc)
        return []
    if not data:
        return []
    out = [{"id": int(g["id"]), "name": g.get("name") or ""}
           for g in (data.get("genres") or [])]
    cache.cache_set(key, out)
    return out


def discover_movies(genre_id: int,
                    page: int = 1,
                    sort_by: str = "popularity.desc",
                    year_from: Optional[int] = None) -> List[Dict[str, Any]]:
    """Vrátí filmy z TMDB discover pro daný žánr."""
    if not _tmdb.is_enabled():
        return []
    key = f"tmdb_discover_movie:{genre_id}:{sort_by}:{year_from}:{page}"
    cached = cache.cache_get(key, ttl=DEFAULT_TTL)
    if cached is not None:
        return list(cached or [])
    params: Dict[str, Any] = {
        "with_genres": genre_id,
        "sort_by":     sort_by,
        "page":        page,
        "vote_count.gte": 50,  # vyřadit obskurní filmy bez hodnocení
    }
    if year_from:
        params["primary_release_date.gte"] = f"{year_from}-01-01"
    try:
        data = _tmdb._http_get("/discover/movie", **params)
    except Exception as exc:  # noqa: BLE001
        log.warning("discover_movies selhalo: %s", exc)
        return []
    if not data:
        return []
    items = [_movie_to_meta(r) for r in (data.get("results") or [])]
    cache.cache_set(key, items)
    return items


def discover_concerts(
    page: int = 1,
    sort_by: str = "popularity.desc",
    origin_countries: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Koncertní filmy/záznamy z TMDB (žánr Hudba / Music)."""
    if not _tmdb.is_enabled():
        return []
    oc_key = "|".join(origin_countries or [])
    key = f"tmdb_discover_concerts:{TMDB_GENRE_MUSIC}:{sort_by}:{oc_key}:{page}"
    cached = cache.cache_get(key, ttl=DEFAULT_TTL)
    if cached is not None:
        return list(cached or [])
    params: Dict[str, Any] = {
        "with_genres":     TMDB_GENRE_MUSIC,
        "sort_by":         sort_by,
        "page":            page,
        "vote_count.gte":  3,
    }
    if origin_countries:
        params["with_origin_country"] = "|".join(origin_countries)
    try:
        data = _tmdb._http_get("/discover/movie", **params)
    except Exception as exc:  # noqa: BLE001
        log.warning("discover_concerts selhalo: %s", exc)
        return []
    if not data:
        return []
    items = [_movie_to_meta(r) for r in (data.get("results") or [])]
    cache.cache_set(key, items)
    return items


def discover_tv(genre_id: int,
                page: int = 1,
                sort_by: str = "popularity.desc") -> List[Dict[str, Any]]:
    """Seriály z TMDB discover pro žánr."""
    if not _tmdb.is_enabled():
        return []
    key = f"tmdb_discover_tv:{genre_id}:{sort_by}:{page}"
    cached = cache.cache_get(key, ttl=DEFAULT_TTL)
    if cached is not None:
        return list(cached or [])
    params: Dict[str, Any] = {
        "with_genres":     genre_id,
        "sort_by":         sort_by,
        "page":            page,
        "vote_count.gte":  20,
    }
    try:
        data = _tmdb._http_get("/discover/tv", **params)
    except Exception as exc:  # noqa: BLE001
        log.warning("discover_tv selhalo: %s", exc)
        return []
    if not data:
        return []
    items = [_tv_to_meta(r) for r in (data.get("results") or [])]
    cache.cache_set(key, items)
    return items


# ---------------------------------------------------------------------------
# v0.0.65: Streaming platformy (Netflix, HBO, Disney+, Apple TV+, ...)
# ---------------------------------------------------------------------------
#
# TMDB ma endpoint /discover/{movie|tv}?with_watch_providers=<id>&watch_region=CZ.
# 'watch_region' urcuje v jake zemi je obsah dostupny - bez nej TMDB vrati
# globalni katalog (vetsina filmu, ktere nejsou v CZ).
#
# Provider IDs jsou stabilni - TMDB je udrzuje na /watch/providers/movie.
# Vsechny nize overovany k 2026-06 pro CZ region.

# Region pro filtrovani dostupnosti. Vsechny platformy v PLATFORMS jsou
# overene proti TMDB API (/watch/providers/movie?watch_region=CZ).
# User muze v Nastaveni prepnout na SK (tmdb_watch_region).
DEFAULT_REGION = "CZ"
_REGION_OPTIONS = {"0": "CZ", "1": "SK"}

# TMDB: flatrate = predplatne (Netflix/Disney+...), ne rental/buy.
WATCH_MONETIZATION = "flatrate"


def get_watch_region() -> str:
    """ISO region pro with_watch_providers (CZ/SK) ze settings."""
    try:
        import xbmcaddon  # type: ignore
        raw = (xbmcaddon.Addon().getSetting("tmdb_watch_region") or "0").strip()
        return _REGION_OPTIONS.get(raw, DEFAULT_REGION)
    except Exception:  # noqa: BLE001
        return DEFAULT_REGION

# v0.0.66: PLATFORMY OVERENE PROTI TMDB API
#
# Vsechny ID + nazvy nize byly stazeny z:
#   GET /watch/providers/movie?watch_region=CZ
#   GET /watch/providers/tv?watch_region=CZ
# Provider_name odpovida TMDB notaci (napr. "HBO Max" ne "Max", "Disney Plus"
# ne "Disney+", "Apple TV" ne "Apple TV+"). icon = nazev souboru
# resources/icons/, fallback na addon ikonu kdyz neexistuje.
#
# ODSTRANEY oproti v0.0.65:
#   - Voyo (ID 130) - TMDB ho pro CZ neeviduje, vraci prazdny katalog.
#     Voyo neni v provider listu pro CZ region. Misto toho ceska
#     alternativa "Prima Plus" + "Oneplay".
#   - Paramount+ (ID 531) - v CZ jen pres SkyShowtime, ne samostatne.
#     Pokud user chce Paramount obsah, je v "SkyShowtime" rubrice.
#
# PRIDANY oproti v0.0.65 (vse overene v TMDB CZ list):
#   - Prima Plus (1928) - cesky Prima streamovaci servis
#   - Oneplay (2536) - nova ceska platforma
#   - Canal+ (381) - dostupne v CZ (Francie + lokalni katalog)
#
# vote_count_threshold per provider - male platformy (Crunchyroll, Canal+,
# ceske) maji v TMDB mene hlasovanych titulu. Pro ne snizujem prah
# aby user videl alespon neco; Netflix/Disney maji titulu hodne.
PLATFORMS: List[Dict[str, Any]] = [
    # Globalni mainstream
    {"id":    8, "name": "Netflix",
     "desc":  "Netflix originaly + svetovy katalog",
     "icon":  "netflix.png",      "min_votes_movie": 30, "min_votes_tv": 20},
    {"id": 1899, "name": "HBO Max",
     "desc":  "HBO Max - HBO Originals, Warner Bros, DC",
     "icon":  "hbomax.png",       "min_votes_movie": 30, "min_votes_tv": 20},
    {"id":  337, "name": "Disney Plus",
     "desc":  "Disney+: Pixar, Marvel, Star Wars, National Geographic",
     "icon":  "disneyplus.png",   "min_votes_movie": 30, "min_votes_tv": 20},
    {"id":  350, "name": "Apple TV",
     "desc":  "Apple TV+ originaly: Ted Lasso, Severance, Foundation",
     "icon":  "appletv.png",      "min_votes_movie": 20, "min_votes_tv": 10},
    {"id":  119, "name": "Amazon Prime Video",
     "desc":  "Prime Video: Reacher, The Boys, Wheel of Time",
     "icon":  "amazon.png",       "min_votes_movie": 30, "min_votes_tv": 20},
    {"id": 1773, "name": "SkyShowtime",
     "desc":  "SkyShowtime - Paramount + NBCUniversal CZ/SK",
     "icon":  "skyshowtime.png",  "min_votes_movie": 20, "min_votes_tv": 10},
    # Ceske platformy
    {"id": 1928, "name": "Prima Plus",
     "desc":  "Prima Plus - cesky streaming Prima",
     "icon":  "primaplus.png",    "min_votes_movie": 5,  "min_votes_tv": 5},
    {"id": 2536, "name": "Oneplay",
     "desc":  "Oneplay - nova ceska streamovaci platforma",
     "icon":  "oneplay.png",      "min_votes_movie": 5,  "min_votes_tv": 5},
    {"id":  381, "name": "Canal+",
     "desc":  "Canal+ - filmy a serialy z Francie a sveta",
     "icon":  "canalplus.png",    "min_votes_movie": 10, "min_votes_tv": 10},
    # Anime
    {"id":  283, "name": "Crunchyroll",
     "desc":  "Crunchyroll - anime z Japonska",
     "icon":  "crunchyroll.png",  "min_votes_movie": 5,  "min_votes_tv": 5},
]


def get_platform(provider_id: int) -> Optional[Dict[str, Any]]:
    """Vrati dict z PLATFORMS pro dane provider_id, None pokud nezname."""
    try:
        pid = int(provider_id)
    except (ValueError, TypeError):
        return None
    for p in PLATFORMS:
        if int(p["id"]) == pid:
            return p
    return None


def platform_movies(provider_id: int,
                    page: int = 1,
                    sort_by: str = "popularity.desc",
                    region: Optional[str] = None,
                    year_from: Optional[int] = None,
                    genre_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Filmy z TMDB Discover, dostupne na dane streaming platforme.

    v0.0.66: vote_count.gte je per-platforma z PLATFORMS configu. Pro
    male/nise platformy (Crunchyroll, ceske) snizena na 5 - jinak by
    discover vracelo prazdno (anime/regionalni obsah ma mene hlasovani).

    v0.0.138: flatrate only + region ze settings (CZ/SK) + zanry.

    :param provider_id: TMDB watch provider ID (8=Netflix, 337=Disney+, ...).
    :param sort_by: popularity.desc | vote_average.desc | primary_release_date.desc
    :param region: ISO-3166-1 region kod (None = ze settings)
    :param year_from: pokud zadany, filtruje filmy od tohoto roku
    :param genre_id: pokud zadany, druhy filtr by zanru (in-platform genre)
    """
    if not _tmdb.is_enabled():
        return []
    try:
        pid = int(provider_id)
    except (ValueError, TypeError):
        return []
    region = (region or get_watch_region() or DEFAULT_REGION).upper()
    platform = get_platform(pid)
    min_votes = int((platform or {}).get("min_votes_movie", 30))
    # v3 = flatrate + region ze settings
    key = (f"tmdb_platform_movie:{pid}:{region}:{sort_by}:"
           f"{year_from}:{genre_id}:{page}:v3")
    cached = cache.cache_get(key, ttl=DEFAULT_TTL)
    if cached is not None:
        return list(cached or [])
    params: Dict[str, Any] = {
        "with_watch_providers":           pid,
        "watch_region":                   region,
        "with_watch_monetization_types":  WATCH_MONETIZATION,
        "sort_by":                        sort_by,
        "page":                           page,
        "vote_count.gte":                 min_votes,
    }
    if year_from:
        params["primary_release_date.gte"] = f"{year_from}-01-01"
    if genre_id:
        params["with_genres"] = int(genre_id)
    try:
        data = _tmdb._http_get("/discover/movie", **params)
    except Exception as exc:  # noqa: BLE001
        log.warning("platform_movies(pid=%s) selhalo: %s", pid, exc)
        return []
    if not data:
        return []
    items = [_movie_to_meta(r) for r in (data.get("results") or [])]
    log.info(
        "platform_movies(pid=%s, region=%s, sort=%s, genre=%s, "
        "min_votes=%d, page=%d) -> %d polozek",
        pid, region, sort_by, genre_id, min_votes, page, len(items))
    cache.cache_set(key, items)
    return items


def platform_tv(provider_id: int,
                page: int = 1,
                sort_by: str = "popularity.desc",
                region: Optional[str] = None,
                genre_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Serialy dostupne na dane streaming platforme. Viz platform_movies."""
    if not _tmdb.is_enabled():
        return []
    try:
        pid = int(provider_id)
    except (ValueError, TypeError):
        return []
    region = (region or get_watch_region() or DEFAULT_REGION).upper()
    platform = get_platform(pid)
    min_votes = int((platform or {}).get("min_votes_tv", 20))
    key = (f"tmdb_platform_tv:{pid}:{region}:{sort_by}:"
           f"{genre_id}:{page}:v3")
    cached = cache.cache_get(key, ttl=DEFAULT_TTL)
    if cached is not None:
        return list(cached or [])
    params: Dict[str, Any] = {
        "with_watch_providers":           pid,
        "watch_region":                   region,
        "with_watch_monetization_types":  WATCH_MONETIZATION,
        "sort_by":                        sort_by,
        "page":                           page,
        "vote_count.gte":                 min_votes,
    }
    if genre_id:
        params["with_genres"] = int(genre_id)
    try:
        data = _tmdb._http_get("/discover/tv", **params)
    except Exception as exc:  # noqa: BLE001
        log.warning("platform_tv(pid=%s) selhalo: %s", pid, exc)
        return []
    if not data:
        return []
    items = [_tv_to_meta(r) for r in (data.get("results") or [])]
    log.info(
        "platform_tv(pid=%s, region=%s, sort=%s, genre=%s, "
        "min_votes=%d, page=%d) -> %d polozek",
        pid, region, sort_by, genre_id, min_votes, page, len(items))
    cache.cache_set(key, items)
    return items
