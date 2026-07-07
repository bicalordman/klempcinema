# -*- coding: utf-8 -*-
"""Pokracovat ve sledovani a historie."""

from __future__ import annotations

import logging

import xbmc  # type: ignore
import xbmcgui  # type: ignore
import xbmcplugin  # type: ignore

from .. import tmdb
from .. import ui
from .. import watched as watched_store
from ..router_common import (
    _ensure_login,
    _tr,
)

log = logging.getLogger("klempcinema.views.history")

def view_continue_watching(handle, base_url, params):
    """Seznam rozkoukaných filmů/epizod (z watched.json)."""
    _ensure_login()
    items = watched_store.get_continue_watching(limit=50)
    if not items:
        ui.show_notification(_tr(30023))
        xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)
        return

    for it in items:
        progress = float(it.get("progress") or 0) * 100
        title = it.get("title") or "?"
        label = f"{title}  [{progress:.0f}%]"

        # Pokud máme base_title - jdeme přes play_pick (kvalita), jinak přímý play.
        if it.get("base_title"):
            url = ui.build_url(base_url, action="play_pick",
                               base=it["base_title"],
                               mode=it.get("mode") or "movie",
                               year=str(it["year"]) if it.get("year") else "")
        else:
            url = ui.build_url(base_url, action="play",
                               id=it["id"],
                               type=it.get("type") or "movie",
                               dubbed="1" if it.get("dubbed") else "0",
                               title=title,
                               year=str(it["year"]) if it.get("year") else "")

        item_for_ui = {
            "title":            label,
            "title_localized":  label,
            "year":             it.get("year"),
            "plot":             it.get("plot") or "",
            "poster":           it.get("poster") or "",
            "fanart":           it.get("fanart") or "",
            "type":             it.get("type") or "movie",
            "dubbed":           bool(it.get("dubbed")),
            "rating":           0,
            "votes":            0,
        }

        # v0.0.51: Context menu pro spravu historie sledovani
        # User klikne pravym -> "Zapomenout" / "Oznacit jako sledovane"
        file_id = it.get("id") or ""
        ctx = []
        if file_id:
            forget_url = ui.build_url(base_url, action="watched_forget",
                                       id=file_id)
            mark_url = ui.build_url(base_url, action="watched_mark",
                                     id=file_id)
            ctx = [
                ("Odstranit z Pokracovat",  f"RunPlugin({forget_url})"),
                ("Oznacit jako shlednute",   f"RunPlugin({mark_url})"),
            ]
        ui.add_video_item(handle, item_for_ui, url, is_folder=False,
                          extra_context_items=ctx)

    xbmcplugin.setContent(handle, "movies")
    xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)


def view_watched_forget(handle, base_url, params):
    """Odstrani polozku z Continue Watching (context menu akce)."""
    file_id = (params.get("id") or "").strip()
    if not file_id:
        return
    try:
        watched_store.forget(file_id)
        ui.show_notification("Odstraneno z Pokracovat", time_ms=2000)
    except Exception as exc:  # noqa: BLE001
        log.error("watched.forget(%s) selhalo: %s", file_id, exc)
        ui.show_notification("Chyba pri odstranovani", time_ms=4000)
    # Refresh aktualniho seznamu
    try:
        import xbmc  # type: ignore
        xbmc.executebuiltin("Container.Refresh")
    except Exception:  # noqa: BLE001
        pass


def view_watched_mark(handle, base_url, params):
    """Oznaci polozku jako shlednutou (vyradi z Continue Watching)."""
    file_id = (params.get("id") or "").strip()
    if not file_id:
        return
    try:
        watched_store.mark_watched(file_id)
        ui.show_notification("Oznaceno jako shlednute", time_ms=2000)
    except Exception as exc:  # noqa: BLE001
        log.error("watched.mark_watched(%s) selhalo: %s", file_id, exc)
        ui.show_notification("Chyba pri oznacovani", time_ms=4000)
    try:
        import xbmc  # type: ignore
        xbmc.executebuiltin("Container.Refresh")
    except Exception:  # noqa: BLE001
        pass


def view_watched_clear(handle, base_url, params):
    """Vyprazdni celou Continue Watching historii (z Nastroju)."""
    try:
        import xbmcgui  # type: ignore
        confirm = xbmcgui.Dialog().yesno(
            "Smazat historii sledovani",
            "Chces opravdu smazat VSECHNY zaznamy o rozkoukanych filmech a serialech?",
        )
        if not confirm:
            return
        count = watched_store.clear_all()
        ui.show_notification(f"Smazano {count} zaznamu", time_ms=3000)
    except Exception as exc:  # noqa: BLE001
        log.exception("watched.clear_all selhalo: %s", exc)
        ui.show_notification("Chyba pri mazani historie", time_ms=4000)


def view_play_trailer(handle, base_url, params):
    """
    Pustí YouTube trailer přes plugin.video.youtube.

    v0.0.52: podporuje 2 rezimy:
      A) tmdb_id zadane -> direct lookup (instantni)
      B) title + year zadane -> on-demand TMDB search, pak trailer
         (funguje i pro polozky bez enrichmentu)
    """
    tmdb_id_raw = params.get("tmdb_id") or ""
    kind = params.get("kind", "movie")
    title = (params.get("title") or "").strip()
    year_raw = (params.get("year") or "").strip()

    try:
        tmdb_id = int(tmdb_id_raw)
    except ValueError:
        tmdb_id = 0

    # On-demand lookup pres title+year
    if not tmdb_id and title:
        try:
            year = int(year_raw) if year_raw else None
        except ValueError:
            year = None
        try:
            if kind == "tv":
                meta = tmdb.search_tv(title)
            else:
                meta = tmdb.search_movie(title, year=year)
            tmdb_id = int(meta.get("tmdb_id") or 0) if meta else 0
            if tmdb_id:
                log.info("trailer on-demand lookup: %r (%s) -> tmdb_id=%d",
                         title, year, tmdb_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("trailer on-demand lookup selhal: %s", exc)

    if not tmdb_id:
        ui.show_notification("Trailer není dostupný (nepodařilo se najít film)",
                              time_ms=4000)
        return

    yt_key = tmdb.get_trailer_youtube_key(tmdb_id, kind=kind)
    if not yt_key:
        ui.show_notification("Trailer nenalezen na YouTube", time_ms=4000)
        return

    yt_url = f"plugin://plugin.video.youtube/play/?video_id={yt_key}"
    try:
        import xbmc  # type: ignore
        xbmc.executebuiltin(f"PlayMedia({yt_url})")
        log.info("trailer: spuštěn YouTube key=%s", yt_key)
    except Exception as exc:  # noqa: BLE001
        log.warning("play_trailer fail: %s", exc)
        ui.show_notification(f"YouTube plugin nenalezen: https://youtu.be/{yt_key}",
                             time_ms=10000)

