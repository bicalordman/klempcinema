# -*- coding: utf-8 -*-
"""TMDB discover: trending, zanry, platformy."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import xbmcplugin  # type: ignore

from .. import api_webshare
from .. import tmdb
from .. import ui
from ..router_common import (
    _add_next_page,
    _addon,
    _addon_icon_for,
    _ensure_login,
    _tr,
    _tr_safe,
)

log = logging.getLogger("klempcinema.views.discover")

DISCOVER_WS_MAX_WAIT = 3.0  # v0.0.139: 6->3s (rychlejsi quit pri Platformach)


def _merge_tmdb_ws_display(
    tmdb_items: List[Dict[str, Any]],
    ws_items: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Zachova TMDB poradi a plakaty; WS-overene polozky maji varianty."""
    by_tid: Dict[Any, Dict[str, Any]] = {}
    for w in ws_items:
        tid = w.get("tmdb_id")
        if tid:
            by_tid[tid] = w
    out: List[Dict[str, Any]] = []
    for m in tmdb_items:
        tid = m.get("tmdb_id")
        out.append(by_tid.get(tid, m))
    return out


def _filter_movies_for_display(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    from .. import image_cache
    if items:
        try:
            image_cache.warm_items_posters(
                items, max_urls=30, total_timeout=1.5)
        except Exception as exc:  # noqa: BLE001
            log.debug("warm posters (movies) selhalo: %s", exc)
    ws = api_webshare.filter_tmdb_movies_on_webshare(
        items, max_wait=DISCOVER_WS_MAX_WAIT)
    return _merge_tmdb_ws_display(items, ws)


def _filter_series_for_display(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    from .. import image_cache
    if items:
        try:
            image_cache.warm_items_posters(
                items, max_urls=30, total_timeout=1.5)
        except Exception as exc:  # noqa: BLE001
            log.debug("warm posters (series) selhalo: %s", exc)
    ws = api_webshare.filter_tmdb_series_on_webshare(
        items, max_wait=DISCOVER_WS_MAX_WAIT)
    return _merge_tmdb_ws_display(items, ws)


def _add_discover_item(handle, base_url, meta: Dict[str, Any]) -> None:
    """Prida jednu WS-overenou discovery polozku (film/serial)."""
    title = meta.get("title") or meta.get("title_localized") or ""
    year = meta.get("year")
    item_type = meta.get("type") or "movie"
    if not title:
        return

    if item_type == "series":
        url = ui.build_url(base_url, action="list_series_seasons", name=title)
        is_folder = True
    elif meta.get("base_title") and meta.get("variant_idents"):
        url = ui.build_url(
            base_url, action="play_pick",
            base=meta["base_title"], mode="movie",
            year=str(year) if year else "",
        )
        is_folder = False
    else:
        url = ui.build_url(
            base_url, action="tmdb_play_movie",
            title=title,
            year=str(year) if year else "",
            tmdb_id=str(meta.get("tmdb_id") or ""),
        )
        is_folder = False

    item_for_ui = {
        "title":           title,
        "title_localized": title,
        "year":            year,
        "plot":            meta.get("plot") or "",
        "poster":          meta.get("poster") or "",
        "fanart":          meta.get("fanart") or "",
        "type":            item_type,
        "rating":          float(meta.get("rating") or 0),
        "votes":           int(meta.get("votes") or 0),
        "popularity":      float(meta.get("popularity") or 0),
        "dubbed":          bool(meta.get("dubbed")),
        "base_title":      meta.get("base_title") or "",
        "variant_idents":  meta.get("variant_idents") or [],
        "tmdb_id":         meta.get("tmdb_id"),
    }
    ui.add_video_item(handle, item_for_ui, url, is_folder=is_folder)


def _filter_tv_on_webshare(tv_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """v0.0.83: TV program / Voyo styl - jen polozky na Webshare."""
    movie_entries: List[Dict[str, Any]] = []
    series_entries: List[Dict[str, Any]] = []
    for it in tv_items:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        channel = (it.get("channel") or "").strip()
        airtime = (it.get("time") or "").strip()
        if channel and airtime:
            extra = f"[B]V TV:[/B] {channel} v {airtime}"
        elif channel:
            extra = f"[B]V TV:[/B] {channel}"
        else:
            extra = ""
        kind = it.get("kind") or "other"
        entry: Dict[str, Any] = {
            "title": title,
            "year": it.get("year") or it.get("tmdb_year"),
            "poster": it.get("tmdb_poster") or it.get("thumb") or "",
            "fanart": it.get("tmdb_fanart") or "",
            "plot": it.get("tmdb_plot") or it.get("plot") or "",
            "rating": it.get("tmdb_rating") or 0,
            "_extra_plot": extra,
        }
        if kind == "film":
            movie_entries.append(entry)
        elif kind in ("series", "documentary", "entertainment"):
            series_entries.append(entry)

    out: List[Dict[str, Any]] = []
    if movie_entries:
        out.extend(api_webshare.filter_discovery_titles_on_webshare(
            movie_entries, kind="movie"))
    if series_entries:
        out.extend(api_webshare.filter_discovery_titles_on_webshare(
            series_entries, kind="series"))
    return out


def _render_tmdb_discover_list(handle, base_url, items, content="movies",
                                close: bool = True):
    """
    Zobrazí položky z TMDB discover/trending PO WS filtru (v0.0.82).

    - Film s WS soubory -> play_pick (varianty uz v cache)
    - Seriál s WS epizodami -> list_series_seasons
    """
    for meta in items:
        _add_discover_item(handle, base_url, meta)

    if close:
        ui.end_directory(handle, content=content)
    else:
        try:
            xbmcplugin.setContent(handle, content)
        except Exception:  # noqa: BLE001
            pass


def view_trending(handle, base_url, params):
    """Trending menu - filmy/seriály týden/den."""
    from .. import tmdb_discover
    if not tmdb.is_enabled():
        ui.show_notification(_tr(30084))
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return

    addon = _addon()
    icon = addon.getAddonInfo("icon")
    fanart = addon.getAddonInfo("fanart")

    submenu = [
        (30085, "trending_movies", {"window": "week"}),
        (30086, "trending_movies", {"window": "day"}),
        (30087, "trending_tv",     {"window": "week"}),
        (30088, "trending_tv",     {"window": "day"}),
    ]
    for label_id, action, p in submenu:
        url = ui.build_url(base_url, action=action, **p)
        ui.add_dir_item(handle=handle, label=_tr(label_id),
                        url=url, icon=icon, fanart=fanart)
    ui.end_directory(handle)


def view_trending_movies(handle, base_url, params):
    from .. import tmdb_discover
    _ensure_login()
    window = params.get("window", "week")
    page = int(params.get("page", "1") or 1)
    items = tmdb_discover.trending_movies(window=window, page=page)
    items = _filter_movies_for_display(items)
    _render_tmdb_discover_list(handle, base_url, items, content="movies",
                                close=False)
    if items:
        _add_next_page(handle, base_url, "trending_movies", sort=window,
                       page=page, has_more=True, window=window)
    ui.end_directory(handle)


def view_trending_tv(handle, base_url, params):
    from .. import tmdb_discover
    _ensure_login()
    window = params.get("window", "week")
    page = int(params.get("page", "1") or 1)
    items = tmdb_discover.trending_tv(window=window, page=page)
    items = _filter_series_for_display(items)
    _render_tmdb_discover_list(handle, base_url, items, content="tvshows",
                                close=False)
    if items:
        _add_next_page(handle, base_url, "trending_tv", sort=window,
                       page=page, has_more=True, window=window)
    ui.end_directory(handle)


def view_genres_movies(handle, base_url, params):
    """Menu žánrů filmů."""
    from .. import tmdb_discover
    if not tmdb.is_enabled():
        ui.show_notification(_tr(30084))
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return

    addon = _addon()
    icon = addon.getAddonInfo("icon")
    fanart = addon.getAddonInfo("fanart")

    genres = tmdb_discover.list_movie_genres()
    if not genres:
        ui.show_notification(_tr(30084))
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return

    for g in genres:
        url = ui.build_url(base_url, action="discover_movies",
                           genre_id=str(g["id"]), genre_name=g["name"])
        ui.add_dir_item(handle=handle, label=g["name"],
                        url=url, icon=icon, fanart=fanart)
    ui.end_directory(handle)


def view_genres_tv(handle, base_url, params):
    """Menu žánrů seriálů."""
    from .. import tmdb_discover
    if not tmdb.is_enabled():
        ui.show_notification(_tr(30084))
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return

    addon = _addon()
    icon = addon.getAddonInfo("icon")
    fanart = addon.getAddonInfo("fanart")

    genres = tmdb_discover.list_tv_genres()
    if not genres:
        ui.show_notification(_tr(30084))
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return

    for g in genres:
        url = ui.build_url(base_url, action="discover_tv",
                           genre_id=str(g["id"]), genre_name=g["name"])
        ui.add_dir_item(handle=handle, label=g["name"],
                        url=url, icon=icon, fanart=fanart)
    ui.end_directory(handle)


def view_menu_platforms(handle, base_url, params=None):
    """Hlavni menu streamovacich platforem.

    Klik na platformu -> view_platform (submenu Filmy / Serialy / Zanry).
    Datovy zdroj: TMDB /discover s with_watch_providers + flatrate
    + watch_region ze settings (CZ/SK).
    """
    from .. import tmdb_discover
    if not tmdb.is_enabled():
        ui.show_notification(_tr(30084))
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return

    addon = _addon()
    default_icon = addon.getAddonInfo("icon")
    fanart = addon.getAddonInfo("fanart")

    region = tmdb_discover.get_watch_region()
    region_label = _tr_safe(
        30153,
        "Watch region: {region} (change in Settings → TMDB)",
    ).format(region=region)
    # Info-only radek (klik = znovu otevre menu platforem, bez skoku do Nastroju).
    ui.add_dir_item(
        handle=handle,
        label=f"[I]{region_label}[/I]",
        url=ui.build_url(base_url, action="menu_platforms"),
        icon=default_icon,
        fanart=fanart,
    )

    for p in tmdb_discover.PLATFORMS:
        # Icon mapping: resources/icons/<name>.png pokud existuje.
        icon_name = p.get("icon") or ""
        icon = _addon_icon_for(icon_name) if icon_name else default_icon
        if not icon or icon == default_icon:
            icon = default_icon
        url = ui.build_url(base_url, action="platform",
                            platform_id=str(p["id"]))
        li_label = f"[B]{p['name']}[/B]"
        ui.add_dir_item(handle=handle, label=li_label, url=url,
                         icon=icon, fanart=fanart)

    ui.end_icon_menu(handle)


def view_platform(handle, base_url, params):
    """Submenu jedne platformy - Filmy / Serialy / Zanry."""
    from .. import tmdb_discover
    pid_raw = params.get("platform_id") or ""
    try:
        pid = int(pid_raw)
    except ValueError:
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return
    platform = tmdb_discover.get_platform(pid)
    pname = platform.get("name") if platform else f"Provider {pid}"

    addon = _addon()
    default_icon = addon.getAddonInfo("icon")
    icon_name = (platform or {}).get("icon") or ""
    icon = _addon_icon_for(icon_name) if icon_name else default_icon
    if not icon:
        icon = default_icon
    fanart = addon.getAddonInfo("fanart")

    submenu = [
        (f"{pname} - Filmy (nejpopularnejsi)", "platform_movies",
         {"platform_id": str(pid), "sort": "popularity.desc"}),
        (f"{pname} - Filmy (nejnovejsi)", "platform_movies",
         {"platform_id": str(pid), "sort": "primary_release_date.desc"}),
        (f"{pname} - Filmy (nejlepe hodnocene)", "platform_movies",
         {"platform_id": str(pid), "sort": "vote_average.desc"}),
        (f"{pname} - Serialy (nejpopularnejsi)", "platform_tv",
         {"platform_id": str(pid), "sort": "popularity.desc"}),
        (f"{pname} - Serialy (nejlepe hodnocene)", "platform_tv",
         {"platform_id": str(pid), "sort": "vote_average.desc"}),
        (f"{pname} - {_tr_safe(30151, 'Movie genres')}",
         "platform_genres_movies", {"platform_id": str(pid)}),
        (f"{pname} - {_tr_safe(30152, 'TV genres')}",
         "platform_genres_tv", {"platform_id": str(pid)}),
    ]
    for label, action, p in submenu:
        url = ui.build_url(base_url, action=action, **p)
        ui.add_dir_item(handle=handle, label=label, url=url,
                         icon=icon, fanart=fanart)

    ui.end_icon_menu(handle)


def _platform_id_from_params(params) -> int:
    try:
        return int(params.get("platform_id") or 0)
    except ValueError:
        return 0


def _genre_id_from_params(params) -> Optional[int]:
    raw = params.get("genre_id") or ""
    if not raw:
        return None
    try:
        gid = int(raw)
        return gid if gid > 0 else None
    except ValueError:
        return None


def view_platform_genres_movies(handle, base_url, params):
    """Zanry filmu uvnitr jedne platformy."""
    from .. import tmdb_discover
    if not tmdb.is_enabled():
        ui.show_notification(_tr(30084))
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return

    pid = _platform_id_from_params(params)
    if not pid:
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return

    addon = _addon()
    icon = addon.getAddonInfo("icon")
    fanart = addon.getAddonInfo("fanart")
    platform = tmdb_discover.get_platform(pid)
    icon_name = (platform or {}).get("icon") or ""
    if icon_name:
        icon = _addon_icon_for(icon_name) or icon

    genres = tmdb_discover.list_movie_genres()
    if not genres:
        ui.show_notification(_tr(30084))
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return

    for g in genres:
        url = ui.build_url(
            base_url, action="platform_movies",
            platform_id=str(pid),
            genre_id=str(g["id"]),
            genre_name=g["name"],
            sort="popularity.desc",
        )
        ui.add_dir_item(handle=handle, label=g["name"],
                        url=url, icon=icon, fanart=fanart)
    ui.end_icon_menu(handle)


def view_platform_genres_tv(handle, base_url, params):
    """Zanry serialu uvnitr jedne platformy."""
    from .. import tmdb_discover
    if not tmdb.is_enabled():
        ui.show_notification(_tr(30084))
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return

    pid = _platform_id_from_params(params)
    if not pid:
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return

    addon = _addon()
    icon = addon.getAddonInfo("icon")
    fanart = addon.getAddonInfo("fanart")
    platform = tmdb_discover.get_platform(pid)
    icon_name = (platform or {}).get("icon") or ""
    if icon_name:
        icon = _addon_icon_for(icon_name) or icon

    genres = tmdb_discover.list_tv_genres()
    if not genres:
        ui.show_notification(_tr(30084))
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return

    for g in genres:
        url = ui.build_url(
            base_url, action="platform_tv",
            platform_id=str(pid),
            genre_id=str(g["id"]),
            genre_name=g["name"],
            sort="popularity.desc",
        )
        ui.add_dir_item(handle=handle, label=g["name"],
                        url=url, icon=icon, fanart=fanart)
    ui.end_icon_menu(handle)


def view_platform_movies(handle, base_url, params):
    """Filmy z dane platformy s pagingem (volitelne zanr)."""
    from .. import tmdb_discover
    _ensure_login()
    pid = _platform_id_from_params(params)
    page = int(params.get("page", "1") or 1)
    sort = params.get("sort") or "popularity.desc"
    genre_id = _genre_id_from_params(params)
    if not pid:
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return
    items = tmdb_discover.platform_movies(
        pid, page=page, sort_by=sort, genre_id=genre_id)
    items = _filter_movies_for_display(items)
    _render_tmdb_discover_list(handle, base_url, items, content="movies",
                                close=False)
    if items:
        extra = {"platform_id": str(pid)}
        if genre_id:
            extra["genre_id"] = str(genre_id)
            if params.get("genre_name"):
                extra["genre_name"] = params.get("genre_name")
        _add_next_page(handle, base_url, "platform_movies", sort=sort,
                       page=page, has_more=True, **extra)
    if not items:
        ui.show_notification(
            "Zadny obsah nenalezen pro danou platformu/region", time_ms=5000)
    ui.end_directory(handle)


def view_platform_tv(handle, base_url, params):
    """Serialy z dane platformy s pagingem (volitelne zanr)."""
    from .. import tmdb_discover
    _ensure_login()
    pid = _platform_id_from_params(params)
    page = int(params.get("page", "1") or 1)
    sort = params.get("sort") or "popularity.desc"
    genre_id = _genre_id_from_params(params)
    if not pid:
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return
    items = tmdb_discover.platform_tv(
        pid, page=page, sort_by=sort, genre_id=genre_id)
    items = _filter_series_for_display(items)
    _render_tmdb_discover_list(handle, base_url, items, content="tvshows",
                                close=False)
    if items:
        extra = {"platform_id": str(pid)}
        if genre_id:
            extra["genre_id"] = str(genre_id)
            if params.get("genre_name"):
                extra["genre_name"] = params.get("genre_name")
        _add_next_page(handle, base_url, "platform_tv", sort=sort,
                       page=page, has_more=True, **extra)
    if not items:
        ui.show_notification(
            "Zadny obsah nenalezen pro danou platformu/region", time_ms=5000)
    ui.end_directory(handle)


def view_discover_movies(handle, base_url, params):
    from .. import tmdb_discover
    _ensure_login()
    genre_id = int(params.get("genre_id") or 0)
    page = int(params.get("page", "1") or 1)
    if not genre_id:
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return
    items = tmdb_discover.discover_movies(genre_id=genre_id, page=page)
    items = _filter_movies_for_display(items)
    _render_tmdb_discover_list(handle, base_url, items, content="movies",
                                close=False)
    if items:
        _add_next_page(handle, base_url, "discover_movies", sort="popularity",
                       page=page, has_more=True,
                       genre_id=str(genre_id),
                       genre_name=params.get("genre_name", ""))
    ui.end_directory(handle)


def view_discover_tv(handle, base_url, params):
    from .. import tmdb_discover
    _ensure_login()
    genre_id = int(params.get("genre_id") or 0)
    page = int(params.get("page", "1") or 1)
    if not genre_id:
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return
    items = tmdb_discover.discover_tv(genre_id=genre_id, page=page)
    items = _filter_series_for_display(items)
    _render_tmdb_discover_list(handle, base_url, items, content="tvshows",
                                close=False)
    if items:
        _add_next_page(handle, base_url, "discover_tv", sort="popularity",
                       page=page, has_more=True,
                       genre_id=str(genre_id),
                       genre_name=params.get("genre_name", ""))
    ui.end_directory(handle)
