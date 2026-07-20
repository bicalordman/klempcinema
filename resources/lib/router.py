# -*- coding: utf-8 -*-
"""
router.py
---------
Centrální směrovač akcí pluginu KlempCinema.

View handlery jsou v balíčku ``views/``; sdílené helpery v ``router_common.py``.
Import view modulů je lazy – při startu Kodi se nenačítá celý strom najednou.
"""

from __future__ import annotations

import logging
import sys
import threading
from typing import Any, Callable, Dict, Optional, Tuple

import xbmcplugin  # type: ignore

from . import shutdown as _shutdown
from . import ui
from .router_common import _addon

log = logging.getLogger("klempcinema.router")

# Lazy cache: (menu, webshare_lists, play, discover, history, tools, tv_program, voyo, concerts)
_views_cache: Optional[Tuple[Any, ...]] = None


def _load_views() -> Tuple[Any, ...]:
    """Načte view moduly až při prvním volání route() – bezpečné pro Kodi."""
    global _views_cache
    if _views_cache is not None:
        return _views_cache

    from .views import concerts
    from .views import discover
    from .views import history
    from .views import menu
    from .views import play
    from .views import tools
    from .views import tv_program
    from .views import voyo
    from .views import webshare_lists

    _views_cache = (
        menu,
        webshare_lists,
        play,
        discover,
        history,
        tools,
        tv_program,
        voyo,
        concerts,
    )
    return _views_cache


def _v():
    m, ws, p, d, h, t, tv, vy, co = _load_views()
    return m, ws, p, d, h, t, tv, vy, co


def _actions() -> Dict[str, Callable]:
    """Slovník akcí – vytvořen až po načtení view modulů."""
    m, ws, p, d, h, t, tv, vy, co = _v()
    return {
        "root":                  lambda h, b, p: m.view_root(h, b),
        "tools":                 lambda h, b, p: m.view_tools(h, b),
        "menu_movies":           lambda h, b, p: m.view_menu_movies(h, b),
        "menu_series":           lambda h, b, p: m.view_menu_series(h, b),
        "menu_discover":         lambda h, b, p: m.view_menu_discover(h, b),
        "menu_library":          lambda h, b, p: m.view_menu_library(h, b),
        "open_settings":         m.view_open_settings,
        "open_csfd":             m.view_open_csfd,
        "list_movies":           ws.view_list_movies,
        "list_movies_new_dub":   ws.view_list_movies_new_dub,
        "search_movies":         ws.view_search_movies,
        "search_movies_new_dub": ws.view_search_movies_new_dub,
        "search_latest":         ws.view_search_latest,
        "list_kids":             ws.view_list_kids,
        "list_series":           ws.view_list_series,
        "list_series_new_dub":   ws.view_list_series_new_dub,
        "list_series_seasons":   ws.view_list_series_seasons,
        "list_series_episodes":  ws.view_list_series_episodes,
        "list_latest":           ws.view_list_latest,
        "list_4k":               ws.view_list_4k,
        "list_bluray":           ws.view_list_bluray,
        "list_animated":         ws.view_list_animated,
        "list_documentary":      ws.view_list_documentary,
        "search_4k":             ws.view_search_4k,
        "search_bluray":         ws.view_search_bluray,
        "search_animated":       ws.view_search_animated,
        "search_documentary":    ws.view_search_documentary,
        "list_my_files":         ws.view_list_my_files,
        "search":                ws.view_search,
        "search_history_forget": ws.view_search_history_forget,
        "search_history_clear":  ws.view_search_history_clear,
        "play_trailer":          h.view_play_trailer,
        "continue_watching":     h.view_continue_watching,
        "watched_forget":        h.view_watched_forget,
        "watched_mark":          h.view_watched_mark,
        "watched_clear":         h.view_watched_clear,
        "trending":              d.view_trending,
        "trending_movies":       d.view_trending_movies,
        "trending_tv":           d.view_trending_tv,
        "genres_movies":         d.view_genres_movies,
        "genres_tv":             d.view_genres_tv,
        "discover_movies":       d.view_discover_movies,
        "discover_tv":           d.view_discover_tv,
        "clear_cache":           t.view_clear_cache,
        "test_login":            t.view_test_login,
        "test_subs":             t.view_subs_test,
        "subs_download":         t.view_subs_download,
        "refresh_rubrika":       t.view_refresh_rubrika,
        "refresh_metadata":      t.view_refresh_metadata,
        "list_tv_program":       tv.view_list_tv_program,
        "tv_program_scope":      tv.view_tv_program_scope,
        "tv_program_films":      tv.view_tv_program_films,
        "tv_program_channel":    tv.view_tv_program_channel,
        "menu_platforms":        d.view_menu_platforms,
        "platform":              d.view_platform,
        "platform_movies":       d.view_platform_movies,
        "platform_tv":           d.view_platform_tv,
        "platform_genres_movies": d.view_platform_genres_movies,
        "platform_genres_tv":    d.view_platform_genres_tv,
        "menu_voyo":             vy.view_menu_voyo,
        "voyo_section":          vy.view_voyo_section,
        "voyo_category":         vy.view_voyo_category,
        "menu_concerts":         lambda h, b, p: co.view_menu_concerts(h, b),
        "menu_concerts_genres":  lambda h, b, p: co.view_menu_concerts_genres(h, b),
        "menu_concerts_quality": lambda h, b, p: co.view_menu_concerts_quality(h, b),
        "list_concerts_foreign": co.view_list_concerts_foreign,
        "list_concerts_cz_sk":   co.view_list_concerts_cz_sk,
        "list_concerts_newest":  co.view_list_concerts_newest,
        "list_concerts_best":    co.view_list_concerts_best,
        "list_concerts_genre":   co.view_list_concerts_genre,
        "list_concerts_quality": co.view_list_concerts_quality,
        "list_concerts_legendary": co.view_list_concerts_legendary,
        "search_concerts":       co.view_search_concerts,
        "list_concerts_search":  co.view_list_concerts_search,
        "refresh_concerts_all":  co.view_refresh_concerts_all,
        "donate":                m.view_donate,
        "refresh_icons":         m.view_refresh_icons,
    }


def _check_post_upgrade() -> None:
    """v0.0.79: Při prvním spuštění nové verze refresh Kodi texture cache."""
    try:
        addon = _addon()
        current_ver = addon.getAddonInfo("version") or ""
        last_ver = addon.getSetting("last_seen_version") or ""
        if not current_ver or current_ver == last_ver:
            return

        log.info(
            "Detekovan upgrade pluginu %s -> %s, refreshuji Kodi cache",
            last_ver or "(prvni spusteni)",
            current_ver,
        )

        try:
            addon.setSetting("last_seen_version", current_ver)
        except Exception as exc:  # noqa: BLE001
            log.debug("setSetting last_seen_version selhalo: %s", exc)

        def _refresh_icons_bg() -> None:
            try:
                import xbmc  # type: ignore
                xbmc.executebuiltin("UpdateLocalAddons")
            except Exception as exc:  # noqa: BLE001
                log.debug("UpdateLocalAddons selhalo: %s", exc)

        threading.Thread(
            target=_refresh_icons_bg,
            name="klempcinema-post-upgrade",
            daemon=True,
        ).start()
    except Exception as exc:  # noqa: BLE001
        log.debug("_check_post_upgrade selhalo: %s", exc)


def route(params: Dict[str, str]) -> None:
    handle = int(sys.argv[1])
    base_url = sys.argv[0]

    _shutdown.start()
    _check_post_upgrade()

    action = (params.get("action") or "root").lower()
    log.debug("router.route(action=%s, params=%s)", action, params)

    try:
        m, ws, p, d, h, t, tv, vy, co = _v()

        if action == "play":
            p.view_play(handle, params)
            return
        if action == "play_pick":
            p.view_play_pick(handle, params)
            return
        if action == "tmdb_play_movie":
            p.view_tmdb_play_movie(handle, params)
            return

        view_fn = _actions().get(action)
        if view_fn is None:
            log.warning("Neznámá akce: %s – vracím root.", action)
            m.view_root(handle, base_url)
        else:
            view_fn(handle, base_url, params)
    except Exception as exc:  # noqa: BLE001
        log.exception("router.route() selhalo: %s", exc)
        ui.show_notification(str(exc) or "Error")
        try:
            xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        except Exception:  # noqa: BLE001
            pass
