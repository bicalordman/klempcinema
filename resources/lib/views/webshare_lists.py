# -*- coding: utf-8 -*-
"""Webshare rubriky, sezony/epizody a vyhledavani."""

from __future__ import annotations

import logging

import xbmc  # type: ignore
import xbmcgui  # type: ignore
import xbmcplugin  # type: ignore

from .. import api_webshare
from .. import prefetch
from .. import ui
from ..router_common import (
    _addon,
    _parse_user_search,
    _ensure_login,
    _prefetch_next,
    _render_episodes_flat,
    _render_flat_list,
    _render_movie_list,
    _render_series_list,
    _render_with_search_top,
    _split_result,
    _tr,
)

log = logging.getLogger("klempcinema.views.webshare_lists")


def _apply_rubric_search(handle, base_url, list_view, raw_q: str, params) -> None:
    """
    v0.0.102: Klavesnice -> rovnou vykresli rubriku (bez Container.Update).

    Container.Update + endOfDirectory(False) na Android TV casto skoci
    zpet do menu misto prazdneho seznamu.
    """
    title, search_year, _ = _parse_user_search(raw_q)
    if not title:
        title = (raw_q or "").strip()
    if not title:
        ui.show_notification("Prazdny dotaz po vycisteni - zadej nazev filmu.",
                             time_ms=4000)
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return
    p = dict(params or {})
    p["query"] = title
    p["page"] = "1"
    p.setdefault("sort", "recent")
    if search_year:
        p["year"] = str(search_year)
    list_view(handle, base_url, p)


def _search_year_from_params(params) -> int | None:
    y = params.get("year", "") or ""
    try:
        return int(y) if y else None
    except (TypeError, ValueError):
        return None


def view_list_movies(handle, base_url, params):
    _ensure_login()
    sort = params.get("sort", "recent")
    page = int(params.get("page", "1") or 1)
    query = params.get("query", "") or ""
    search_year = _search_year_from_params(params)
    result = api_webshare.get_movies(
        sort=sort, page=page,
        query_override=query or None,
        search_year=search_year,
    )
    _, has_more = _split_result(result)
    _render_with_search_top(handle, base_url, result, "list_movies",
                             "search_movies", page, sort,
                             search_label_id=30097, query=query,
                             search_year=search_year)
    if not query:
        _prefetch_next("list_movies", api_webshare.get_movies, sort, page, has_more)
    else:
        _prefetch_next("list_movies", api_webshare.get_movies, sort, page, has_more,
                       query_override=query, search_year=search_year)


def view_search_movies(handle, base_url, params):
    """Klavesnice -> volne hledani v rubrice Filmy."""
    _ensure_login()
    q = ui.ask_keyboard(_tr(30097))
    if not q:
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return
    _apply_rubric_search(handle, base_url, view_list_movies, q, params)


def view_list_movies_new_dub(handle, base_url, params):
    _ensure_login()
    sort = params.get("sort", "recent")
    page = int(params.get("page", "1") or 1)
    query = params.get("query", "") or ""
    search_year = _search_year_from_params(params)
    result = api_webshare.get_movies_new_dub(
        sort=sort, page=page,
        query_override=query or None,
        search_year=search_year,
    )
    _, has_more = _split_result(result)
    _render_with_search_top(handle, base_url, result, "list_movies_new_dub",
                             "search_movies_new_dub", page, sort,
                             search_label_id=30098, query=query,
                             search_year=search_year, cz_only=True)
    if not query:
        _prefetch_next("list_movies_new_dub", api_webshare.get_movies_new_dub,
                       sort, page, has_more)
    else:
        _prefetch_next("list_movies_new_dub", api_webshare.get_movies_new_dub,
                       sort, page, has_more,
                       query_override=query, search_year=search_year)


def view_search_movies_new_dub(handle, base_url, params):
    """Klavesnice -> volne hledani v Novinkach dabovanych."""
    _ensure_login()
    q = ui.ask_keyboard(_tr(30098))
    if not q:
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return
    _apply_rubric_search(handle, base_url, view_list_movies_new_dub, q, params)


def view_list_kids(handle, base_url, params):
    _ensure_login()
    sort = params.get("sort", "rating")
    page = int(params.get("page", "1") or 1)
    result = api_webshare.get_kids(sort=sort, page=page)
    _, has_more = _split_result(result)
    _render_movie_list(handle, base_url, result, "list_kids", page, sort)
    _prefetch_next("list_kids", api_webshare.get_kids, sort, page, has_more)


def view_list_series(handle, base_url, params):
    _ensure_login()
    sort = params.get("sort", "rating")
    page = int(params.get("page", "1") or 1)
    result = api_webshare.get_series(sort=sort, page=page)
    _, has_more = _split_result(result)
    _render_series_list(handle, base_url, result, "list_series", page, sort)
    _prefetch_next("list_series", api_webshare.get_series, sort, page, has_more)


def view_list_4k(handle, base_url, params):
    """Filmy v 4K kvalitě (s vyhledávacím polem jako první položkou)."""
    _ensure_login()
    sort = params.get("sort", "recent")
    page = int(params.get("page", "1") or 1)
    query = params.get("query", "") or ""
    search_year = _search_year_from_params(params)
    result = api_webshare.get_4k(
        sort=sort, page=page,
        query_override=query or None,
        search_year=search_year,
    )
    _, has_more = _split_result(result)
    _render_with_search_top(handle, base_url, result, "list_4k",
                             "search_4k", page, sort,
                             search_label_id=30093, query=query,
                             search_year=search_year)
    if not query:
        _prefetch_next("list_4k", api_webshare.get_4k, sort, page, has_more)
    else:
        _prefetch_next("list_4k", api_webshare.get_4k, sort, page, has_more,
                       query_override=query, search_year=search_year)


def view_list_animated(handle, base_url, params):
    """v0.0.69: Animovane filmy CZ/SK (s vyhledavacim polem jako prvni polozkou).

    Stejny pattern jako 4K/BluRay - search top, refresh button, pagination,
    quality picker pri kliku. Filtrovani na TMDB genre 16 (Animation) +
    min. kvalita (configurable, default 1080p) + CZ dab/tit.
    """
    _ensure_login()
    sort = params.get("sort", "recent")
    page = int(params.get("page", "1") or 1)
    query = params.get("query", "") or ""
    search_year = _search_year_from_params(params)
    result = api_webshare.get_movies_animated(
        sort=sort, page=page,
        query_override=query or None,
        search_year=search_year,
    )
    _, has_more = _split_result(result)
    _render_with_search_top(handle, base_url, result, "list_animated",
                             "search_animated", page, sort,
                             search_label_id=30096, query=query,
                             search_year=search_year)
    if not query:
        _prefetch_next("list_animated", api_webshare.get_movies_animated,
                       sort, page, has_more)
    else:
        _prefetch_next("list_animated", api_webshare.get_movies_animated,
                       sort, page, has_more,
                       query_override=query, search_year=search_year)


def view_search_animated(handle, base_url, params):
    """v0.0.69: Klavesnice -> filtrovane hledani v Animovanych filmech."""
    _ensure_login()
    q = ui.ask_keyboard(_tr(30096))
    if not q:
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return
    _apply_rubric_search(handle, base_url, view_list_animated, q, params)


def view_list_documentary(handle, base_url, params):
    """Dokumentární filmy CZ/SK."""
    _ensure_login()
    sort = params.get("sort", "recent")
    page = int(params.get("page", "1") or 1)
    query = params.get("query", "") or ""
    search_year = _search_year_from_params(params)
    result = api_webshare.get_movies_documentary(
        sort=sort, page=page,
        query_override=query or None,
        search_year=search_year,
    )
    _, has_more = _split_result(result)
    _render_with_search_top(handle, base_url, result, "list_documentary",
                             "search_documentary", page, sort,
                             search_label_id=30325, query=query,
                             search_year=search_year)
    if not query:
        _prefetch_next("list_documentary", api_webshare.get_movies_documentary,
                       sort, page, has_more)
    else:
        _prefetch_next("list_documentary", api_webshare.get_movies_documentary,
                       sort, page, has_more,
                       query_override=query, search_year=search_year)


def view_search_documentary(handle, base_url, params):
    _ensure_login()
    q = ui.ask_keyboard(_tr(30325))
    if not q:
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return
    _apply_rubric_search(handle, base_url, view_list_documentary, q, params)


def view_search_4k(handle, base_url, params):
    """Klávesnice -> filtrované hledání v 4K rubrice."""
    _ensure_login()
    q = ui.ask_keyboard(_tr(30093))
    if not q:
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return
    _apply_rubric_search(handle, base_url, view_list_4k, q, params)


def view_list_bluray(handle, base_url, params):
    """Filmy z BluRay (s vyhledávacím polem jako první položkou)."""
    _ensure_login()
    sort = params.get("sort", "recent")
    page = int(params.get("page", "1") or 1)
    query = params.get("query", "") or ""
    search_year = _search_year_from_params(params)
    result = api_webshare.get_bluray(
        sort=sort, page=page,
        query_override=query or None,
        search_year=search_year,
    )
    _, has_more = _split_result(result)
    _render_with_search_top(handle, base_url, result, "list_bluray",
                             "search_bluray", page, sort,
                             search_label_id=30094, query=query,
                             search_year=search_year)
    if not query:
        _prefetch_next("list_bluray", api_webshare.get_bluray, sort, page, has_more)
    else:
        _prefetch_next("list_bluray", api_webshare.get_bluray, sort, page, has_more,
                       query_override=query, search_year=search_year)


def view_search_bluray(handle, base_url, params):
    """Klávesnice -> filtrované hledání v BluRay rubrice."""
    _ensure_login()
    q = ui.ask_keyboard(_tr(30094))
    if not q:
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return
    _apply_rubric_search(handle, base_url, view_list_bluray, q, params)


def view_list_series_new_dub(handle, base_url, params):
    _ensure_login()
    sort = params.get("sort", "recent")
    page = int(params.get("page", "1") or 1)
    result = api_webshare.get_series_new_dub(sort=sort, page=page)
    _, has_more = _split_result(result)
    _render_series_list(handle, base_url, result, "list_series_new_dub", page, sort)
    _prefetch_next("list_series_new_dub", api_webshare.get_series_new_dub,
                   sort, page, has_more)


def view_list_series_seasons(handle, base_url, params):
    """
    Seznam sezón daného seriálu (po klikknutí na seriál v rubrice).
    Klik na sezónu -> view_list_series_episodes_for_season.
    Pokud TMDB nenajde žádné sezóny, fallback na ploché epizody.

    v0.0.68: podpora ?refresh=1 - vynuti fresh fetch z Webshare
        (smaze cache). Pouziva se z "Aktualizovat" tlacitka pri hledani
        premierovych/cerstve nahranych dilu.
    """
    _ensure_login()
    name = (params.get("name") or "").strip()
    if not name:
        ui.show_notification("Chybi nazev serialu v URL", time_ms=6000)
        log.error("view_list_series_seasons: prazdny name, params=%s", params)
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return

    force_refresh = (params.get("refresh") or "") == "1"
    if force_refresh:
        ui.show_notification(
            f"Aktualizuji '{name[:40]}' - hledam nove dily na Webshare...",
            time_ms=4000,
        )

    log.info("view_list_series_seasons: name=%r refresh=%s", name, force_refresh)
    try:
        info = api_webshare.get_series_seasons(name, force_refresh=force_refresh)
    except Exception as exc:  # noqa: BLE001
        log.exception("get_series_seasons(%r) selhalo: %s", name, exc)
        ui.show_notification(f"Chyba pri nacitani serialu: {exc}", time_ms=6000)
        info = {"seasons": []}

    seasons = info.get("seasons") or []

    # Pokud nejsou sezóny detekované (např. seriál bez SxxEyy nebo malá
    # data z WS), fallback na všechny epizody bez foldering.
    if not seasons:
        log.info("view_list_series_seasons: %s - zadne sezony, fallback flat", name)
        _render_episodes_flat(handle, base_url, name, season=None,
                              force_refresh=force_refresh)
        return

    fanart = info.get("fanart") or ""
    poster_show = info.get("poster") or ""

    # v0.0.68: Refresh button nahore - smaze cache a fetchne fresh.
    # Pouziti: kdyz chce user nove premierove epizody (Voyo, Oneplay).
    addon = _addon()
    icon = addon.getAddonInfo("icon")
    refresh_url = ui.build_url(base_url, action="list_series_seasons",
                                name=name, refresh="1")
    ui.add_dir_item(
        handle=handle,
        label=("[COLOR FF00BFFF][B]>>> Aktualizovat - hledat nejnovejsi "
               "dily na Webshare[/B][/COLOR]"),
        url=refresh_url, icon=icon, fanart=fanart,
    )

    for s in seasons:
        s_num = int(s.get("season_number") or 0)
        ws_count = int(s.get("ws_episode_count") or 0)
        tmdb_count = int(s.get("tmdb_episode_count") or 0)

        # ZÁMĚRNĚ ukázat sezóny i s ws_count==0:
        # 1) Webshare full-text může selhat na celém jménu - sezona se najde
        #    on-demand až po kliku přes _render_episodes_flat (širší queries).
        # 2) Lepší ukázat "Sezona 1 [0/10 ep]" a user klikne, než prázdný
        #    listing bez jakékoliv info.
        label = s.get("name") or f"Sezóna {s_num}"
        if tmdb_count > 0 and ws_count > 0:
            label = f"{label}  [{ws_count}/{tmdb_count} epizod]"
        elif tmdb_count > 0:
            label = f"{label}  [0/{tmdb_count} epizod - klik zkusi nacist]"
        elif ws_count > 0:
            label = f"{label}  [{ws_count} epizod]"

        url = ui.build_url(base_url, action="list_series_episodes",
                           name=name, season=str(s_num))

        item = {
            "title":  label,
            "plot":   s.get("overview") or info.get("plot") or "",
            "year":   None,
            "poster": s.get("poster") or poster_show,
            "fanart": fanart,
            "type":   "series",
            "rating": 0,
            "votes":  0,
            "dubbed": False,
        }
        ui.add_video_item(handle, item, url, is_folder=True)

    xbmcplugin.setContent(handle, "seasons")
    xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)


def view_list_series_episodes(handle, base_url, params):
    """
    Epizody konkrétního seriálu (volitelně 1 sezóny).
    Pokud je předán parametr 'season', zobrazí jen epizody dané sezóny.

    v0.0.68: ?refresh=1 vynuti fresh fetch z Webshare pro nove epizody.
    """
    _ensure_login()
    name = (params.get("name") or "").strip()
    if not name:
        ui.show_notification(_tr(30023))
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return

    season_raw = params.get("season") or ""
    season = int(season_raw) if season_raw and season_raw.isdigit() else None
    force_refresh = (params.get("refresh") or "") == "1"
    if force_refresh:
        ui.show_notification(
            f"Aktualizuji epizody '{name[:40]}'...",
            time_ms=4000,
        )

    _render_episodes_flat(handle, base_url, name, season=season,
                           force_refresh=force_refresh)


def view_list_latest(handle, base_url, params):
    _ensure_login()
    sort = params.get("sort", "recent")
    page = int(params.get("page", "1") or 1)
    query = params.get("query", "") or ""
    search_year = _search_year_from_params(params)
    result = api_webshare.get_latest(
        sort=sort, page=page,
        query_override=query or None,
        search_year=search_year,
    )
    _, has_more = _split_result(result)
    _render_with_search_top(handle, base_url, result, "list_latest",
                             "search_latest", page, sort,
                             search_label_id=30099, query=query,
                             search_year=search_year, cz_only=True)
    if not query:
        _prefetch_next("list_latest", api_webshare.get_latest, sort, page, has_more)
    else:
        _prefetch_next("list_latest", api_webshare.get_latest, sort, page, has_more,
                       query_override=query, search_year=search_year)


def view_search_latest(handle, base_url, params):
    """Klavesnice -> volne hledani v rubrice Novinky."""
    _ensure_login()
    q = ui.ask_keyboard(_tr(30099))
    if not q:
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return
    _apply_rubric_search(handle, base_url, view_list_latest, q, params)


def view_list_my_files(handle, base_url, params):
    _ensure_login()
    sort = params.get("sort", "recent")
    page = int(params.get("page", "1") or 1)
    result = api_webshare.get_my_files(sort=sort, page=page)
    items, has_more = _split_result(result)
    if not items and page == 1:
        ui.show_notification(_tr(30025))
    _render_flat_list(handle, base_url, result, "list_my_files", page, sort)
    _prefetch_next("list_my_files", api_webshare.get_my_files, sort, page, has_more)


def view_search(handle, base_url, params):
    """
    Hlavni search.

    v0.0.52: pokud nemame query a page=1, zobrazi se HISTORIE poslednich
    hledani (10 polozek) + na vrchu '>>> Nove hledani...'. Klik na zaznam
    historie zopakuje search. Klik na 'Nove hledani' otevre klavesnici.
    """
    _ensure_login()
    query = params.get("query", "") or ""
    page = int(params.get("page", "1") or 1)
    force_keyboard = params.get("new", "") == "1"

    if not query and not force_keyboard:
        # v0.0.52: Zobraz historii hledani jako menu (pokud neni prazdna)
        try:
            from .. import search_history
            history = search_history.get_all(limit=10)
        except Exception:  # noqa: BLE001
            history = []

        if history:
            addon = _addon()
            icon = addon.getAddonInfo("icon")
            fanart = addon.getAddonInfo("fanart")

            # Nove hledani na vrchu (zlute)
            new_url = ui.build_url(base_url, action="search", new="1")
            ui.add_dir_item(
                handle=handle,
                label="[COLOR FFFFA500][B]>>> Nove hledani...[/B][/COLOR]",
                url=new_url, icon=icon, fanart=fanart,
            )

            # Vymazat historii
            clear_url = ui.build_url(base_url, action="search_history_clear")
            ui.add_dir_item(
                handle=handle,
                label="[COLOR FF888888][I]Vymazat historii hledani[/I][/COLOR]",
                url=clear_url, icon=icon, fanart=fanart,
            )

            # Posledni hledani
            for q in history:
                url = ui.build_url(base_url, action="search",
                                    query=q, page=1)
                # Context: smazat tenhle zaznam
                forget_url = ui.build_url(base_url,
                                           action="search_history_forget",
                                           query=q)
                ctx = [("Smazat zaznam", f"RunPlugin({forget_url})")]
                # Vyrobit listitem rucne kvuli context menu
                import xbmcgui  # type: ignore
                li = xbmcgui.ListItem(label=q)
                li.setArt({"icon": icon, "thumb": icon, "fanart": fanart})
                try:
                    li.addContextMenuItems(ctx, replaceItems=False)
                except Exception:  # noqa: BLE001
                    pass
                xbmcplugin.addDirectoryItem(handle=handle, url=url,
                                             listitem=li, isFolder=True)

            xbmcplugin.setContent(handle, "files")
            xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)
            return

        # Bez historie - rovnou klavesnice
        raw_q = ui.ask_keyboard(_tr(30006)) or ""
        if not raw_q:
            xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
            return
        query, search_year, history_label = _parse_user_search(raw_q)
        if not query:
            query = raw_q.strip()
            history_label = query
    elif force_keyboard:
        # User klikl na "Nove hledani" - vzdy klavesnice
        raw_q = ui.ask_keyboard(_tr(30006)) or ""
        if not raw_q:
            xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
            return
        query, search_year, history_label = _parse_user_search(raw_q)
        if not query:
            query = raw_q.strip()
            history_label = query
    else:
        query, search_year, history_label = _parse_user_search(query)
        if not query:
            query = (params.get("query", "") or "").strip()
            history_label = query

    # v0.0.52: ulozit dotaz do historie (jen pri page 1, aby nedoslo k
    # ulozeni pro kazdou stranku)
    if page == 1 and history_label:
        try:
            from .. import search_history
            search_history.add(history_label)
        except Exception:  # noqa: BLE001
            pass

    result = api_webshare.search(query=query, page=page, year=search_year)
    items, has_more = _split_result(result)
    # Lepsi UX: pokud nic nenalezeno na page 1, ukaz konkretni hint
    # s navrhem co zkusit (drive jen genericke "obsah nenalezen").
    if not items and page == 1:
        try:
            import xbmcgui  # type: ignore
            xbmcgui.Dialog().notification(
                "KlempCinema",
                f"Pro \"{query[:30]}\" nic nenalezeno - zkus jiny dotaz",
                xbmcgui.NOTIFICATION_INFO, 5000,
            )
        except Exception:  # noqa: BLE001
            ui.show_notification(f"Pro '{query[:30]}' nic nenalezeno",
                                 time_ms=5000)

    # v0.0.62: na vrchu page 1 zobraz ">>> Nove hledani..." + zaznam aktualniho
    # dotazu, aby user mohl rovnou refinovat bez Back.
    if page == 1 and query:
        addon = _addon()
        icon = addon.getAddonInfo("icon")
        fanart = addon.getAddonInfo("fanart")
        new_url = ui.build_url(base_url, action="search", new="1")
        label = (f"[COLOR FFFFA500][B]>>> Nove hledani "
                 f"(aktualni: '{query[:25]}{'...' if len(query) > 25 else ''}')..."
                 f"[/B][/COLOR]")
        ui.add_dir_item(handle=handle, label=label, url=new_url,
                        icon=icon, fanart=fanart)

    _render_flat_list(handle, base_url, result, "search", page,
                      sort="relevance", query=query)
    if has_more and page < 20:
        key = f"search:{query}:{page + 1}"
        prefetch.schedule(
            cache_key=key,
            fetcher=lambda: api_webshare.search(query=query, page=page + 1),
            page=page,
            has_more=has_more,
        )


def view_search_history_forget(handle, base_url, params):
    """Smaze jeden zaznam z historie hledani."""
    q = (params.get("query") or "").strip()
    if q:
        try:
            from .. import search_history
            search_history.remove(q)
            ui.show_notification(f"Smazano: {q[:30]}", time_ms=2000)
        except Exception:  # noqa: BLE001
            pass
    try:
        import xbmc  # type: ignore
        xbmc.executebuiltin("Container.Refresh")
    except Exception:  # noqa: BLE001
        pass


def view_search_history_clear(handle, base_url, params):
    """Vyprazdni celou historii hledani."""
    try:
        import xbmcgui  # type: ignore
        confirm = xbmcgui.Dialog().yesno(
            "Vymazat historii hledani",
            "Chces opravdu smazat vsechny ulozene dotazy?",
        )
        if not confirm:
            return
        from .. import search_history
        n = search_history.clear()
        ui.show_notification(f"Smazano {n} dotazu", time_ms=3000)
    except Exception:  # noqa: BLE001
        log.exception("search_history clear failed")
    try:
        import xbmc  # type: ignore
        xbmc.executebuiltin("Container.Refresh")
    except Exception:  # noqa: BLE001
        pass
