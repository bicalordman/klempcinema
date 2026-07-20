# -*- coding: utf-8 -*-
"""TV program dnes (iDNES + TMDB/CSFD + Webshare)."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

import xbmcplugin  # type: ignore

from .. import tv_program
from .. import ui
from ..router_common import (
    _addon,
    _addon_icon_for,
    _ensure_login,
)

log = logging.getLogger("klempcinema.views.tv_program")

_SCOPE_LABELS = {
    "films":       "Filmy dnes",
    "series":      "Serialy dnes",
    "shows":       "Porady dnes",
    "documentary": "Dokumenty dnes",
    "all_watchable": "Vsechno dnes (filmy, serialy, porady, dokumenty)",
    "prime_films": "Filmy dnes vecer (od 18:00)",
}


def _channel_icon(cname: str = "", cid: str = "") -> str:
    """Ikona kanalu (HBO, Nova, ...) nebo default addon icon."""
    rel = tv_program.channel_icon_relpath(cname, cid)
    if rel:
        return _addon_icon_for(rel)
    return _addon().getAddonInfo("icon")


def _play_url_for_item(base_url: str, item: Dict[str, Any]) -> str:
    """URL akce: Webshare hledani / sezony podle typu TV polozky."""
    title = (item.get("title") or "").strip()
    year = item.get("year") or item.get("tmdb_year")
    kind = item.get("kind") or "other"

    if kind == "film":
        return ui.build_url(
            base_url, action="tmdb_play_movie",
            title=title, year=str(year) if year else "",
        )
    if kind in ("series", "documentary", "entertainment"):
        return ui.build_url(
            base_url, action="list_series_seasons", name=title,
        )
    return ui.build_url(
        base_url, action="tmdb_play_movie",
        title=title, year=str(year) if year else "",
    )


def _render_tv_item(handle: int, base_url: str, item: Dict[str, Any],
                    show_channel_in_label: bool = True) -> None:
    """Vykresli jednu TV polozku s metadaty TMDB/CSFD a akci Webshare."""
    title = (item.get("title") or "").strip()
    if not title:
        return

    channel = (item.get("channel") or "").strip()
    airtime = (item.get("time") or "").strip()
    plot = (item.get("plot") or "").strip()
    thumb = (item.get("thumb") or "").strip()
    year = item.get("year")
    kind = item.get("kind") or "other"
    idnes_url = (item.get("url") or "").strip()

    tmdb_poster = (item.get("tmdb_poster") or "").strip()
    tmdb_fanart = (item.get("tmdb_fanart") or "").strip()
    tmdb_plot = (item.get("tmdb_plot") or item.get("csfd_plot") or "").strip()
    tmdb_year = item.get("tmdb_year")
    tmdb_rating = item.get("tmdb_rating")
    csfd_rating = item.get("csfd_rating")
    csfd_pct = item.get("csfd_rating_pct")

    display_title = (
        item.get("tmdb_title") or item.get("csfd_title") or title
    ).strip()
    if tmdb_year and not year:
        year = int(tmdb_year)
    poster_final = tmdb_poster or item.get("csfd_poster") or thumb
    fanart_final = tmdb_fanart or ""

    parts: List[str] = []
    if show_channel_in_label and channel:
        parts.append(f"[B]{channel}[/B]")
    if airtime:
        parts.append(f"[COLOR FFFFA500]{airtime}[/COLOR]")

    title_with_year = display_title + (f" ({year})" if year else "")
    ratings: List[str] = []
    if tmdb_rating and float(tmdb_rating) > 0:
        ratings.append(f"TMDB {float(tmdb_rating):.1f}")
    if csfd_rating and float(csfd_rating) > 0:
        if csfd_pct:
            ratings.append(f"CSFD {csfd_pct}%")
        else:
            ratings.append(f"CSFD {float(csfd_rating):.1f}")
    if ratings:
        title_with_year += f" [COLOR FFFFD700]{' | '.join(ratings)}[/COLOR]"

    if parts:
        label = " • ".join(parts) + f"  •  {title_with_year}"
    else:
        label = title_with_year

    plot_lines: List[str] = []
    if channel and airtime:
        plot_lines.append(f"[B]V TV:[/B] {channel} v {airtime}")
    elif channel:
        plot_lines.append(f"[B]V TV:[/B] {channel}")
    kind_label = {
        "film": "Film",
        "series": "Serial",
        "documentary": "Dokument",
        "entertainment": "Porad",
    }.get(kind, "TV")
    plot_lines.append(f"[B]Typ:[/B] {kind_label}")
    plot_lines.append(
        "[I]Klik -> hledat na Webshare (prehrat / vybrat epizodu)[/I]")
    final_plot = tmdb_plot or plot
    if final_plot:
        plot_lines.append(final_plot)
    if item.get("csfd_url"):
        plot_lines.append(f"CSFD: {item['csfd_url']}")
    if idnes_url:
        plot_lines.append(f"iDNES TV: {idnes_url}")

    play_url = _play_url_for_item(base_url, item)
    ui_type = "movie" if kind == "film" else "series"

    item_for_ui = {
        "title":  display_title,
        "year":   int(year) if year else None,
        "plot":   "\n".join(plot_lines),
        "poster": poster_final,
        "fanart": fanart_final,
        "rating": float(tmdb_rating or csfd_rating or 0),
        "type":   ui_type,
        "dubbed": False,
    }
    ui.add_video_item(handle, item_for_ui, play_url, is_folder=False,
                       label_override=label)


def _render_tv_list(handle: int, base_url: str,
                    items: List[Dict[str, Any]]) -> None:
    for it in items:
        _render_tv_item(handle, base_url, it)


def view_list_tv_program(handle, base_url, params):
    """TV program dnes - hlavni menu (jako CSFD televize).

    Zdroj dat: tvprogram.idnes.cz (CSFD /televize/ blokuje boty).
    Metadata: TMDB + CSFD fallback. Prehrani: Webshare po kliku.
    """
    _ensure_login()
    force_refresh = params.get("refresh") == "1"
    items = tv_program.fetch_today(force_refresh=force_refresh)

    if force_refresh and items:
        ui.show_notification(
            "Zaklad TV programu nacten. HBO/Cinemax se stahuji na pozadi...",
            time_ms=5000,
        )

    addon = _addon()
    fanart = addon.getAddonInfo("fanart")

    def _mi(name: str) -> str:
        return _addon_icon_for(f"menu/{name}.png")

    refresh_url = ui.build_url(base_url, action="list_tv_program", refresh="1")
    ui.add_dir_item(
        handle=handle,
        label="[COLOR FF66CCFF][I]>>> Aktualizovat "
              "(stahnout cerstvy TV program)[/I][/COLOR]",
        url=refresh_url, icon=_mi("tv_refresh"), fanart=fanart,
    )

    if not items:
        ui.show_notification(
            "Nepodarilo se stahnout TV program. Zkus 'Aktualizovat' za par minut.",
            time_ms=7000)
        browser_url = ui.build_url(base_url, action="open_csfd",
                                    url="https://tvprogram.idnes.cz/")
        ui.add_dir_item(
            handle=handle,
            label="[COLOR FFFFA500][B]>>> Otevrit TV program v prohlizeci[/B][/COLOR]",
            url=browser_url, icon=_mi("tv"), fanart=fanart,
        )
        ui.end_icon_menu(handle)
        return

    if not tv_program.is_full_cache_ready():
        if tv_program.is_background_fetch_running():
            bg_label = ("[COLOR FFFFA500][I]HBO, Cinemax, History... "
                        "se stahuji na pozadi (~1 min). "
                        "Obnov menu pro plny seznam stanic s obsahem.[/I][/COLOR]")
        else:
            bg_label = ("[COLOR FFFFA500][I]Placene kanaly jeste nejsou v cache. "
                        "Klikni Aktualizovat — prazdne stanice (sport) se nezobrazi.[/I][/COLOR]")
        ui.add_dir_item(
            handle=handle, label=bg_label,
            url=ui.build_url(base_url, action="list_tv_program"),
            icon=_mi("tv"), fanart=fanart,
        )

    ui.add_dir_item(
        handle=handle,
        label="[COLOR FF888888]--- Dnes v TV ---[/COLOR]",
        url=ui.build_url(base_url, action="list_tv_program"),
        icon=_mi("tv"), fanart=fanart,
    )

    sections = [
        ("films",       "[B][COLOR FFE50914]F[/COLOR][/B]  Filmy dnes", "tv_films"),
        ("series",      "[B][COLOR FF7B68EE]S[/COLOR][/B]  Serialy dnes", "tv_series"),
        ("shows",       "[B][COLOR FFFFD700]\u25cf[/COLOR][/B]  Porady dnes", "tv_shows"),
        ("documentary", "[B][COLOR FF8BC34A]D[/COLOR][/B]  Dokumenty dnes", "tv_docs"),
        ("all_watchable", "[B][COLOR FF00C853]\u2605[/COLOR][/B]  Vsechno dnes (chronologicky)", "tv_all"),
        ("prime_films", "[B][COLOR FFFF8C00]\u25a0[/COLOR][/B]  Filmy dnes vecer (od 18:00)", "tv_prime"),
    ]
    for scope, label, icon_name in sections:
        if scope == "prime_films":
            n = len(tv_program.filter_today(
                items, tv_program.SCOPE_KINDS["films"],
                only_future=True, prime_time_only=True))
        else:
            n = tv_program.count_today(items, scope, only_future=True)
        if n == 0:
            continue
        sec_url = ui.build_url(base_url, action="tv_program_scope", scope=scope)
        ui.add_dir_item(
            handle=handle,
            label=f"[COLOR FFFFD700][B]{label}[/B][/COLOR]  ({n})",
            url=sec_url, icon=_mi(icon_name), fanart=fanart,
        )

    free_channels = tv_program.get_channels()
    if free_channels:
        ui.add_dir_item(
            handle=handle,
            label="[COLOR FF888888]--- Podle kanalu ---[/COLOR]",
            url=ui.build_url(base_url, action="list_tv_program"),
            icon=_mi("tv"), fanart=fanart,
        )
        for cid, cname in free_channels:
            folder_url = ui.build_url(base_url, action="tv_program_channel",
                                       channel_id=cid)
            ui.add_dir_item(
                handle=handle, label=f"[B]{cname}[/B]",
                url=folder_url, icon=_channel_icon(cname, cid), fanart=fanart,
            )

    premium = tv_program.get_premium_channels()
    if premium:
        ui.add_dir_item(
            handle=handle,
            label="[COLOR FF888888]--- Placene kanaly "
                  "(HBO, Cinemax, History...) ---[/COLOR]",
            url=ui.build_url(base_url, action="list_tv_program"),
            icon=_mi("tv"), fanart=fanart,
        )
        for cid, cname in premium:
            folder_url = ui.build_url(base_url, action="tv_program_channel",
                                       channel_id=cid)
            ui.add_dir_item(
                handle=handle,
                label=f"[COLOR FF66CCFF][B]{cname}[/B][/COLOR]",
                url=folder_url, icon=_channel_icon(cname, cid), fanart=fanart,
            )

    ui.add_dir_item(
        handle=handle,
        label="[COLOR FF888888][I]Zdroj: tvprogram.idnes.cz | "
              "Jen stanice s filmy/serialy/porady dnes | "
              "Prehrani: Webshare[/I][/COLOR]",
        url=ui.build_url(base_url, action="list_tv_program"),
        icon=_mi("tv"), fanart=fanart,
    )

    ui.end_icon_menu(handle)


def view_tv_program_scope(handle, base_url, params):
    """Seznam polozek jedne TV rubriky (filmy/serialy/porady/dokumenty)."""
    _ensure_login()
    scope = (params.get("scope") or "all_watchable").strip()
    items = tv_program.fetch_today()

    if scope == "prime_films":
        out = tv_program.filter_today(
            items, tv_program.SCOPE_KINDS["films"],
            only_future=True, prime_time_only=True)
        content = "movies"
    else:
        kinds = tv_program.SCOPE_KINDS.get(scope, tv_program.SCOPE_KINDS["all_watchable"])
        out = tv_program.filter_today(items, kinds, only_future=True)
        content = "tvshows" if scope in ("series", "shows", "documentary") else "movies"
        if scope == "all_watchable":
            content = "videos"

    label = _SCOPE_LABELS.get(scope, scope)
    if not out:
        ui.show_notification(f"Zadne polozky: {label}", time_ms=5000)
        ui.end_directory(handle, content=content)
        return

    _render_tv_list(handle, base_url, out)
    ui.end_directory(handle, content=content)


def view_tv_program_films(handle, base_url, params):
    """Zpetna kompatibilita: stare URL tv_program_films -> scope."""
    scope = params.get("scope") or "all"
    if scope == "series":
        _ensure_login()
        items = tv_program.fetch_today()
        out = tv_program.filter_today(
            items, ("series", "documentary"),
            only_future=True)
        if not out:
            ui.show_notification("Zadne serialy/dokumenty dnes", time_ms=5000)
        else:
            _render_tv_list(handle, base_url, out)
        ui.end_directory(handle, content="tvshows")
        return
    new_params = dict(params)
    new_params["scope"] = "films" if scope == "all" else scope
    view_tv_program_scope(handle, base_url, new_params)


def view_tv_program_channel(handle, base_url, params):
    """Program jednoho kanalu (filmy + serialy + porady + dokumenty)."""
    _ensure_login()
    cid = params.get("channel_id") or ""
    if not cid:
        ui.show_notification("Chybi channel_id", time_ms=3000)
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return

    items = tv_program.get_channel_today(
        cid, only_future=True,
        kinds=list(tv_program.SCOPE_KINDS["all_watchable"]))
    if not items:
        ui.show_notification(
            "Kanal nema dnes dalsi sledovatelny obsah.", time_ms=5000)
        ui.end_directory(handle, content="videos")
        return

    _render_tv_list(handle, base_url, items)
    ui.end_directory(handle, content="videos")
