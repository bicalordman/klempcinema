# -*- coding: utf-8 -*-
"""View handlery pro rubriku Koncerty."""

from __future__ import annotations

import logging

import xbmcplugin  # type: ignore

from .. import ui
from ..modules import concerts
from ..modules.concerts_genres import GENRE_MENU_ORDER
from ..router_common import (
    _addon,
    _ensure_login,
    _prefetch_next,
    _render_menu,
    _render_movie_list,
    _split_result,
    _tr,
)

log = logging.getLogger("klempcinema.views.concerts")

_GENRE_STRING_IDS = {
    "rock": 30306,
    "pop": 30307,
    "metal": 30308,
    "rap": 30309,
    "folk": 30310,
    "electronic": 30311,
}


def view_menu_concerts(handle: int, base_url: str) -> None:
    icon = _addon().getAddonInfo("icon")
    menu = [
        (30301, "list_concerts_foreign",  {"page": 1}, icon),
        (30302, "list_concerts_cz_sk",    {"page": 1}, icon),
        (30303, "list_concerts_newest",   {"page": 1}, icon),
        (30304, "list_concerts_best",     {"page": 1}, icon),
        (30305, "menu_concerts_genres",   {}, icon),
        (30312, "menu_concerts_quality",   {}, icon),
        (30315, "list_concerts_legendary", {"page": 1}, icon),
        (30316, "search_concerts",        {}, icon),
        (30317, "refresh_concerts_all",    {}, icon),
    ]
    _render_menu(handle, base_url, menu)


def view_menu_concerts_genres(handle: int, base_url: str) -> None:
    icon = _addon().getAddonInfo("icon")
    menu = [
        (_GENRE_STRING_IDS[g], "list_concerts_genre",
         {"genre": g, "page": 1}, icon)
        for g in GENRE_MENU_ORDER
    ]
    _render_menu(handle, base_url, menu)


def view_menu_concerts_quality(handle: int, base_url: str) -> None:
    icon = _addon().getAddonInfo("icon")
    menu = [
        (30313, "list_concerts_quality", {"quality": "4k", "page": 1}, icon),
        (30314, "list_concerts_quality", {"quality": "1080p", "page": 1}, icon),
    ]
    _render_menu(handle, base_url, menu)


def _list_concerts(
    handle: int,
    base_url: str,
    params: dict,
    subsection: str,
    list_action: str,
    sort: str = "recent",
) -> None:
    _ensure_login()
    page = int(params.get("page", "1") or 1)
    genre = (params.get("genre") or "").strip()
    quality = (params.get("quality") or "").strip()
    query = (params.get("query") or "").strip()

    if subsection == "search" and query:
        result = concerts.fetch_concert_search(query, page=page)
    else:
        result = concerts.fetch_concerts(
            subsection=subsection,
            page=page,
            sort=sort,
            query=query,
            genre=genre,
            quality=quality,
        )
    _, has_more = _split_result(result)

    extra = {}
    if genre:
        extra["genre"] = genre
    if quality:
        extra["quality"] = quality
    if query:
        extra["query"] = query

    _render_movie_list(
        handle, base_url, result, list_action, page, sort,
        content="musicvideos", **extra,
    )

    def _fetch(**kwargs):
        p = int(kwargs.get("page", page + 1))
        if subsection == "search" and query:
            return concerts.fetch_concert_search(query, page=p)
        return concerts.fetch_concerts(
            subsection=subsection,
            page=p,
            sort=kwargs.get("sort", sort),
            query=query,
            genre=genre,
            quality=quality,
        )

    _prefetch_next(
        list_action, _fetch, sort, page, has_more,
        genre=genre, quality=quality, query=query,
    )


def view_list_concerts_foreign(handle, base_url, params):
    _list_concerts(handle, base_url, params, "foreign", "list_concerts_foreign")


def view_list_concerts_cz_sk(handle, base_url, params):
    _list_concerts(handle, base_url, params, "cz_sk", "list_concerts_cz_sk")


def view_list_concerts_newest(handle, base_url, params):
    _list_concerts(handle, base_url, params, "newest", "list_concerts_newest", sort="recent")


def view_list_concerts_best(handle, base_url, params):
    _list_concerts(handle, base_url, params, "best", "list_concerts_best", sort="rating")


def view_list_concerts_genre(handle, base_url, params):
    _list_concerts(handle, base_url, params, "genre", "list_concerts_genre")


def view_list_concerts_quality(handle, base_url, params):
    _list_concerts(handle, base_url, params, "quality", "list_concerts_quality")


def view_list_concerts_legendary(handle, base_url, params):
    _list_concerts(handle, base_url, params, "legendary", "list_concerts_legendary")


def view_search_concerts(handle, base_url, params):
    """
    Hledani koncertu.

    Bez query: polozka >>> Hledat... (spolehlivejsi nez okamzita klavesnice).
    S new=1: klavesnice a rovnou vykresleni vysledku (bez Container.Update).
    """
    _ensure_login()
    query = (params.get("query") or "").strip()
    force_keyboard = params.get("new", "") == "1"

    if not query and not force_keyboard:
        addon = _addon()
        icon = addon.getAddonInfo("icon")
        fanart = addon.getAddonInfo("fanart")
        new_url = ui.build_url(base_url, action="search_concerts", new="1")
        ui.add_dir_item(
            handle=handle,
            label=f"[COLOR FFFFA500][B]>>> {_tr(30316)}...[/B][/COLOR]",
            url=new_url, icon=icon, fanart=fanart,
        )
        xbmcplugin.setContent(handle, "files")
        xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)
        return

    if not query and force_keyboard:
        q = ui.ask_keyboard(_tr(30318))
        if not q or not q.strip():
            xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
            return
        query = q.strip()

    search_params = dict(params)
    search_params["query"] = query
    search_params["page"] = "1"
    _list_concerts(
        handle, base_url, search_params,
        "search", "list_concerts_search", sort="relevance",
    )


def view_list_concerts_search(handle, base_url, params):
    query = (params.get("query") or "").strip()
    if not query:
        view_search_concerts(handle, base_url, params)
        return
    _list_concerts(handle, base_url, params, "search", "list_concerts_search", sort="relevance")


def view_refresh_concerts_all(handle, base_url, params):
    n = concerts.clear_all_cache()
    log.info("refresh_concerts_all: smazano %d klicu", n)
    ui.show_notification(
        _tr(30317) + f" ({n} " + ("klic" if n == 1 else "klicu") + ")",
        time_ms=3000,
    )
    import xbmc  # type: ignore
    url = ui.build_url(base_url, action="menu_concerts")
    xbmc.executebuiltin(f'Container.Update("{url}",replace)')
    xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
