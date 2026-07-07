# -*- coding: utf-8 -*-
"""
Rubrika Koncerty – fetch z Webshare, paginace, cache.

Pipeline (v0.0.135 - sjednoceno s filmy):
  WS multi-query (per podrubrika)
  -> pre_filter (is_valid_concert_item + filtr podrubriky)  [pred enrich]
  -> _movies_from_groups(skip_csfd=True)  [TMDB enrich UVNITR fetche]
  -> post_filter (TMDB zanr + mark art)  [pred slicingem]
  -> paginate (stabilni stranky 30/ks, cely vysledek cachovan)
Diky enrich+filtru pred slicingem jsou stranky konzistentni a dalsi/zpet
je instantni z cache (zadne znovu-nacitani).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from .. import api_webshare as ws
from .concerts_genres import GENRE_WS_QUERIES
from .cz_bands import is_known_cz_sk_artist, ws_queries_for_cz_sk
from .concerts_utils import (
    filter_concert_items,
    filter_concert_items_pre,
    filter_search_items_post,
    filter_search_items_pre,
)

log = logging.getLogger("klempcinema.concerts")

CACHE_VERSION = "v18"


def _mark_concert_art(items: List[Dict[str, Any]]) -> None:
    """v0.0.134: oznac polozky jako koncert -> vlastni koncertni placeholder
    (barva + 'KONCERT') misto genericheho filmoveho, kdyz chybi plakat."""
    from .. import clean_title as ct
    for it in items or []:
        if not isinstance(it, dict):
            continue
        it["art_kind"] = "concert"
        raw = (it.get("base_title") or it.get("title") or "").strip()
        clean = ct.clean_title(raw) if raw else ""
        if clean:
            it["placeholder_title"] = clean


def _current_year() -> int:
    try:
        return datetime.now().year
    except Exception:  # noqa: BLE001
        return 2026


def _base_queries() -> List[str]:
    cy = _current_year()
    py = cy - 1
    return [
        "live",
        "koncert",
        "concert",
        "live tour",
        f"live {cy}",
        f"live {py}",
        f"koncert {cy}",
        "unplugged",
        "wembley",
        "stadium live",
        "festival live",
        "music live",
        "band live",
    ]


def _queries_for_subsection(
    subsection: str,
    query: str = "",
    genre: str = "",
    quality: str = "",
) -> List[str]:
    sub = (subsection or "all").lower()
    cy = _current_year()

    if sub == "search" and query.strip():
        q = query.strip()
        return [
            q,
            f"{q} live",
            f"{q} koncert",
            f"{q} concert",
            f"{q} unplugged",
            f"{q} tour",
            f"live {q}",
        ]

    if sub == "cz_sk":
        cy = _current_year()
        # v0.0.136: primarne dotazy podle jmen ceskych/slovenskych kapel
        # (databaze cz_bands.py) + kratka zakladni rotace misto obecnych
        # "live cz" ktere vraci dokumenty a nesmysly.
        band_queries = ws_queries_for_cz_sk(max_bands=35)
        base = [
            f"koncert {cy} cz",
            f"live {cy} cz",
            "koncert Praha",
            "koncert O2 arena",
            "koncert Tipsport arena",
            "CZ koncert live",
        ]
        return band_queries + base

    if sub == "legendary":
        return [
            "wembley live", "unplugged", "live aid", "queen live",
            "pink floyd live", "metallica live", "u2 live",
            "led zeppelin live", "nirvana live", "ac dc live",
            "iron maiden live", "depeche mode live",
        ]

    if sub == "genre" and genre:
        g = genre.lower()
        extra = list(GENRE_WS_QUERIES.get(g, []))
        # v0.0.136: dopln CZ/SK kapely daneho zanru (rock, metal, folk, …)
        band_q = ws_queries_for_cz_sk(max_bands=12, genre=g)
        if g == "rock":
            band_q = band_q + ws_queries_for_cz_sk(max_bands=8, genre="hardrock")
        return extra + band_q + [f"{genre} live", f"{genre} koncert"]

    if sub == "quality":
        if (quality or "").lower() == "4k":
            return [
                "4K live", "2160p concert", "4K koncert", "uhd live",
                "4K unplugged", "2160p live",
            ]
        return [
            "1080p live", "1080p koncert", "1080p concert",
            "1080p unplugged", "1080p wembley",
        ]

    if sub == "newest":
        return [
            f"live {cy}",
            f"koncert {cy}",
            f"concert {cy}",
            str(cy),
            f"live {cy - 1}",
            f"koncert {cy - 1}",
        ] + _base_queries()[:4]

    if sub == "best":
        return _base_queries() + [
            "wembley", "unplugged", "live at", "full concert",
        ]

    # foreign, all, default
    return _base_queries()


CACHE_PREFIX = f"rubrika:concerts:{CACHE_VERSION}"


def _cache_key(
    subsection: str,
    sort: str = "recent",
    query: str = "",
    genre: str = "",
    quality: str = "",
) -> str:
    parts = [CACHE_PREFIX, subsection, sort]
    if query:
        parts.append(f"q:{query.lower().strip()}")
    if genre:
        parts.append(f"g:{genre.lower()}")
    if quality:
        parts.append(f"hq:{quality.lower()}")
    return ":".join(parts)


def _sort_key_newest(it: Dict[str, Any]) -> tuple:
    ws_added = it.get("ws_added") or ""
    return (ws_added, int(it.get("quality_score") or 0))


def _sort_key_best(it: Dict[str, Any]) -> tuple:
    rating = float(it.get("rating") or 0)
    votes = int(it.get("votes") or 0)
    return (rating, votes, it.get("ws_added") or "")


def _sort_key_recent(it: Dict[str, Any]) -> tuple:
    return (it.get("ws_added") or "", int(it.get("quality_score") or 0))


def _resolve_sort_key(subsection: str, sort: str) -> Optional[Callable]:
    sub = (subsection or "").lower()
    if sub == "best" or sort == "rating":
        return _sort_key_best
    if sub == "newest" or sort == "recent":
        return _sort_key_newest
    return _sort_key_recent


def fetch_concert_search(
    query: str,
    page: int = 1,
) -> Tuple[List[Dict[str, Any]], bool]:
    """
    Vyhledavani koncertu – vlastni pipeline, bez TMDB filmovych filtru.

    User zada interpreta (Metallica) -> WS vrati soubory -> zobrazime
    shody i bez slova 'live' v ocistenem titulu.
    """
    q = (query or "").strip()
    if not q:
        return [], False

    alt_queries = [
        q,
        f"{q} live",
        f"{q} koncert",
        f"{q} concert",
    ]
    # v0.0.135: bump klice - zmena pipeline (enrich+filtr uvnitr fetche).
    cache_key = f"{CACHE_PREFIX}:search:v2:{q.lower()}"

    def _ws_fetch(ws_page: int):
        idx = (ws_page - 1) % len(alt_queries)
        q_page = (ws_page - 1) // len(alt_queries) + 1
        q_ws = alt_queries[idx]
        log.debug("concert_search: WS q=%r page=%d (ws_page=%d)", q_ws, q_page, ws_page)
        files = ws.search_videos(query=q_ws, sort="recent", page=q_page)
        if files is None:
            return None
        if not files:
            return [] if ws_page < len(alt_queries) * 3 else None
        files = ws._exclude_series(files)

        def _pre(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            return filter_search_items_pre(items, q)

        # v0.0.135: enrich UVNITR fetche (skip_csfd=True) - jako u filmu.
        # Tim jsou plakaty/genre_ids k dispozici pred slicingem, stranky
        # jsou stabilni (30/stranku) a vysledek se cachuje -> dalsi/zpet
        # je instantni bez znovu-nacitani.
        grouped = ws._movies_from_groups(
            ws._group_by_title(files),
            pre_filter=_pre,
            skip_aggressive_filters=True,
            skip_csfd=True,
        )
        return grouped

    def _post(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = filter_search_items_post(items, q)
        _mark_concert_art(out)
        return out

    # poster_first=True -> koncerty s plakatem nahoru, pak zbytek.
    page_items, has_more = ws._paginate_rubrika(
        cache_key,
        _ws_fetch,
        ui_page=page,
        post_filter=_post,
        poster_first=True,
        max_ws_pages=9,
    )

    log.info(
        "fetch_concert_search(%r page=%d): %d items, has_more=%s",
        q, page, len(page_items), has_more,
    )
    return page_items, has_more


def fetch_concerts(
    subsection: str = "all",
    page: int = 1,
    sort: str = "recent",
    query: str = "",
    genre: str = "",
    quality: str = "",
) -> Tuple[List[Dict[str, Any]], bool]:
    sub = (subsection or "all").lower()

    if sub == "search" and query.strip():
        return fetch_concert_search(query.strip(), page=page)

    queries = _queries_for_subsection(sub, query=query, genre=genre, quality=quality)
    cache_key = _cache_key(sub, sort, query=query, genre=genre, quality=quality)
    sort_key = _resolve_sort_key(sub, sort)

    def _pre_filter(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return filter_concert_items_pre(
            items, sub, genre=genre, quality=quality, query=query,
        )

    def _post_filter(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        # v0.0.135: TMDB-zanr filtr + oznaceni PRED slicingem (uvnitr fetche),
        # aby byly stranky stabilni (30/stranku, ne 4) a cely vysledek se
        # cachoval -> dalsi/zpet instantne, jako u filmu.
        out = filter_concert_items(
            items, sub, genre=genre, quality=quality, query=query,
        )
        _mark_concert_art(out)
        return out

    # v0.0.135: skip_enrich=False (enrich uvnitr fetche, skip_csfd=True).
    # Drive skip_enrich=True + enrich az po slicingu -> stranka 30->4 a
    # znovu-nacitani pri kazdem otevreni. Ted stejny model jako filmy.
    page_items, has_more = ws._paginate_multi_query(
        cache_key=cache_key,
        queries=queries,
        ui_page=page,
        pre_filter=_pre_filter,
        post_filter=_post_filter,
        sort_key=sort_key,
        max_ws_pages=8,
        grouping="movie",
        skip_csfd=True,
        skip_aggressive_filters=True,
        # v0.0.137: kratsi strop nez filmy (12s). S ~76 dotazy a malo
        # koncerty na dotaz kazda stranka projizdi hodne WS stranek. Kratsi
        # rozpocet -> prefetch dalsi stranky stihne dobehnout drive nez na
        # ni user klikne, takze "dalsi strana" je plynula i za stranou 2.
        max_wait_sec=9.0,
    )

    log.info(
        "fetch_concerts(%s page=%d q=%d): %d items, has_more=%s",
        sub, page, len(queries), len(page_items), has_more,
    )
    return page_items, has_more


def clear_all_cache() -> int:
    from .. import cache
    return cache.cache_clear_prefix("rubrika:concerts:")
