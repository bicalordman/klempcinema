# -*- coding: utf-8 -*-
"""
ui.py
-----
Pomocné funkce pro tvorbu Kodi UI – seznamů, položek a stránkování.

Veřejné funkce:
    build_url(base_url, **params)              -> str
    add_dir_item(handle, label, url, ...)      -> None
    add_video_item(handle, item, url, ...)     -> None
    add_next_page_item(handle, base_url, action, sort, current_page, **extra) -> None
    show_notification(message, ...)            -> None
    ask_keyboard(heading)                      -> str | None
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import urlencode

import xbmc  # type: ignore
import xbmcaddon  # type: ignore
import xbmcgui  # type: ignore
import xbmcplugin  # type: ignore

from . import image_cache
from . import poster_placeholder


def _addon_assets():
    """Vrátí (icon, fanart) cesty addonu pro fallback art."""
    try:
        addon = xbmcaddon.Addon()
        return addon.getAddonInfo("icon"), addon.getAddonInfo("fanart")
    except Exception:  # noqa: BLE001
        return "", ""


def _genres_display(item: Dict[str, Any]) -> str:
    """v0.0.83: Lokalizované žánry z TMDB (max 4 pro přehlednost řádku)."""
    names = item.get("genre_names")
    if names:
        parts = [str(n) for n in names[:4] if n]
        return ", ".join(parts)
    gids = item.get("genre_ids") or []
    if not gids:
        return ""
    try:
        from . import tmdb as _tmdb
        kind = ("tv" if (item.get("type") or "movie")
                in ("series", "tvshow", "episode") else "movie")
        resolved = _tmdb.genre_names_for_ids(gids, kind)
        if resolved:
            item["genre_names"] = resolved
            return ", ".join(resolved[:4])
    except Exception:  # noqa: BLE001
        pass
    return ""


def _placeholder_poster_for(item: Dict[str, Any]) -> str:
    """
    v0.0.62: Vrati hezky per-item placeholder s NAZVEM uvnitr (pokud
    je PIL k dispozici). Fallback na staticky typovy placeholder.

    Drive (do v0.0.61): vsechny polozky bez plakatu mely stejny generic
    obrazek -> uzivatelsky lehky chaos. Ted: kazdy film/serial dostane
    vlastni vygenerovany placeholder s typem (FILM/TV), nazvem, rokem
    a brandem KlempCinema.
    """
    item_type = item.get("type") or "movie"
    title = (item.get("title_localized")
             or item.get("title")
             or item.get("base_title") or "")
    year = item.get("year")
    genres = _genres_display(item)
    return poster_placeholder.get_placeholder(title, year, item_type, genres=genres or None)


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def build_url(base_url: str, **params: Any) -> str:
    """Sestaví URL ve tvaru plugin://.../?action=...&id=..."""
    clean = {k: v for k, v in params.items() if v is not None}
    return f"{base_url}?{urlencode(clean)}"


# ---------------------------------------------------------------------------
# Položky seznamu
# ---------------------------------------------------------------------------

def add_dir_item(
    handle: int,
    label: str,
    url: str,
    icon: Optional[str] = None,
    fanart: Optional[str] = None,
    is_folder: bool = True,
) -> None:
    """Přidá obyčejnou položku/složku (např. v hlavním menu)."""
    li = xbmcgui.ListItem(label=label)
    art: Dict[str, str] = {}
    if icon:
        art["icon"] = icon
        art["thumb"] = icon
    if fanart:
        art["fanart"] = fanart
    if art:
        li.setArt(art)

    xbmcplugin.addDirectoryItem(
        handle=handle,
        url=url,
        listitem=li,
        isFolder=is_folder,
    )


def add_video_item(
    handle: int,
    item: Dict[str, Any],
    url: str,
    is_folder: bool = False,
    extra_context_items: Optional[list] = None,
    label_override: Optional[str] = None,
) -> None:
    """
    Přidá videopoložku (film/epizoda/seriál) s metadaty, artworkem,
    hvězdičkou s hodnocením a indikátorem dabingu.

    Očekává item ve formátu z api_webshare._movies_from_groups / file_to_video_item.
    Pokud byla položka obohacena přes TMDB, použijí se TMDB pole:
        title_localized, year, plot, poster, fanart, rating, votes, popularity

    :param label_override: v0.0.63 - pokud zadan, pouzije se misto vygenerovaneho
        labelu. Pouziva csfd_tv kde label ma vlastni format (Kanal • CAS • Titul).
        Year/rating/badges suffixy se v tomto pripade NEPRIDAVAJI.
    """
    # Lokalizovaný titul (z TMDB cs-CZ) má přednost před názvem ze souboru.
    raw_title = item.get("title_localized") or item.get("title") or ""
    year = item.get("year")
    plot = item.get("plot") or ""
    # v0.0.57: Pokud TMDB nedoplnil poster, zkus ČSFD poster jako
    # primary art. Drive ČSFD plakat byl uplne ignorovan i kdyz TMDB
    # poster chybel - user videl jen placeholder. Ted se cely fallback
    # retez vyuziva: TMDB -> ČSFD -> WS thumb -> placeholder.
    poster = item.get("poster") or item.get("csfd_poster") or ""
    fanart = item.get("fanart") or item.get("csfd_poster") or ""
    item_type = item.get("type") or "movie"
    dubbed = bool(item.get("dubbed", False))
    subs_cz = bool(item.get("subs_cz", False))
    rating = float(item.get("rating") or 0)
    votes = int(item.get("votes") or 0)
    csfd_rating = float(item.get("csfd_rating") or 0)
    csfd_pct = int(item.get("csfd_rating_pct") or 0)
    badges = item.get("badges") or []
    genre_str = _genres_display(item)

    # ---- Label ----
    if label_override is not None:
        # v0.0.63 - caller dodal kompletni label (napr. csfd_tv).
        # Pouzij ho beze zmen, nepridavej rating/badges suffixy.
        label = label_override
    else:
        label = raw_title
        if year:
            label = f"{label} ({year})"
        if rating > 0:
            label = f"{label}  [COLOR FFFFD700]\u2605 {rating:.1f}[/COLOR]"
        # ČSFD rating jako BONUS vedle TMDB (v0.0.56) - cervene "ČSFD 78%"
        # (Sosáč-style). Zobrazi se jen pokud ČSFD scrap uspěl - jinak nic.
        if csfd_pct > 0:
            label = f"{label}  [COLOR FFE50914]ČSFD {csfd_pct}%[/COLOR]"
        elif csfd_rating > 0:
            # fallback pokud mame 0..10 ale ne pct
            label = f"{label}  [COLOR FFE50914]ČSFD {int(csfd_rating*10)}%[/COLOR]"
        # Dabing má prioritu (oranžová), titulky jako fallback (modrozelená).
        if dubbed:
            label = f"{label}  [COLOR FFFFA500][CZ][/COLOR]"
        elif subs_cz:
            label = f"{label}  [COLOR FF66CCFF][CZ tit][/COLOR]"
        # Technické badges (1080p, HEVC, HDR, IMAX...) - barevně tlumeně
        if badges:
            badges_str = " ".join(f"[{b}]" for b in badges)
            label = f"{label}  [COLOR FF888888]{badges_str}[/COLOR]"

    li = xbmcgui.ListItem(label=label)

    # ---- Plot s prefixy ----
    plot_parts = []
    if dubbed:
        plot_parts.append("[B][CZ/SK dabing][/B]")
    elif subs_cz:
        plot_parts.append("[B][CZ titulky][/B]")
    if genre_str:
        plot_parts.append(f"[B]Žánr:[/B] {genre_str}")
    # TMDB + ČSFD hodnoceni vedle sebe (Sosáč-style) - v0.0.56.
    if rating > 0 and csfd_pct > 0:
        plot_parts.append(
            f"[B]TMDB:[/B] {rating:.1f}/10 ({votes} hlasů)  "
            f"[B]ČSFD:[/B] {csfd_pct} %"
        )
    elif rating > 0:
        plot_parts.append(f"[B]TMDB:[/B] {rating:.1f}/10 ({votes} hlasů)")
    elif csfd_pct > 0:
        plot_parts.append(f"[B]ČSFD:[/B] {csfd_pct} %")
    if plot:
        plot_parts.append(plot)
    plot_full = "\n".join(plot_parts) if plot_parts else plot

    # ---- Info ----
    info: Dict[str, Any] = {
        "title": raw_title,
        "plot":  plot_full,
    }
    if year:
        info["year"] = int(year)
    if rating > 0:
        info["rating"] = rating
    if votes > 0:
        info["votes"] = str(votes)
    if genre_str:
        info["genre"] = genre_str

    if item_type == "series":
        info["mediatype"] = "tvshow"
    elif item_type == "episode":
        info["mediatype"] = "episode"
    else:
        info["mediatype"] = "movie"

    li.setInfo("video", info)

    # ---- Art ----
    # Pro http(s) URL projdeme přes lokální image cache - když je už stažený,
    # vrátí lokální cestu (Kodi zobrazí instantně bez síťového kola).
    # Když není, async naplánuje stažení na pozadí pro příští otevření.
    fb_icon, fb_fanart = _addon_assets()
    # v0.0.62: per-item generovany placeholder s nazvem UVNITR plakatu
    # (FILM/TV badge nahore, titul uprostred, rok dole). Pokud PIL chybi,
    # fallback na staticky placeholder (jako drive).
    type_fallback = _placeholder_poster_for(item)
    final_poster = image_cache.cached_image_path(poster) if poster else type_fallback
    final_fanart = image_cache.cached_image_path(fanart) if fanart else fb_fanart

    art: Dict[str, str] = {}
    if final_poster:
        art["poster"] = final_poster
        art["thumb"]  = final_poster
        art["icon"]   = final_poster
    if final_fanart:
        art["fanart"]  = final_fanart
        art["landscape"] = final_fanart
    if art:
        li.setArt(art)

    if not is_folder:
        li.setProperty("IsPlayable", "true")

    # ---- Context menu (Trailer / ČSFD / custom items per view) ----
    ctx_items = []
    tmdb_id = item.get("tmdb_id")
    csfd_url = item.get("csfd_url") or ""
    kind = "tv" if item_type in ("series", "tvshow", "episode") else "movie"
    if tmdb_id:
        # Mame TMDB ID - direct lookup, instantni.
        trailer_url = build_url(_base_url(), action="play_trailer",
                                 tmdb_id=str(tmdb_id), kind=kind)
        ctx_items.append(("Přehrát trailer", f"RunPlugin({trailer_url})"))
    elif raw_title:
        # v0.0.52: Trailer i bez tmdb_id - on-demand TMDB lookup pres title+year.
        # Posleme title+year do play_trailer, ten si na pozadi najde tmdb_id
        # a pak prehraje YT trailer. Funguje i pro polozky bez enrichmentu.
        year_str = str(year) if year else ""
        trailer_url = build_url(_base_url(), action="play_trailer",
                                 title=raw_title, year=year_str, kind=kind)
        ctx_items.append(("Přehrát trailer", f"RunPlugin({trailer_url})"))

    # ČSFD link v context menu (v0.0.56). Otevre vychozi browser
    # s detailem na ČSFD. Pokud nemame csfd_url (ČSFD blokuje?),
    # otevre se search rovnou pro VYCISTENY nazev (v0.0.57).
    # Drive: posilali jsme raw_title vc. "+ forced" atd - ČSFD nenasel.
    if csfd_url:
        ctx_items.append(("Otevřít na ČSFD",
                          f"RunPlugin({build_url(_base_url(), action='open_csfd', url=csfd_url)})"))
    elif raw_title:
        # Pouzij clean_title (bez "+forced", quality tagu, atd.) - jinak
        # ČSFD search vraci 0 vysledku. Plus pridame rok pro lepsi presnost.
        try:
            from . import clean_title as _ct
            search_q = _ct.clean_title(raw_title) or raw_title
        except Exception:  # noqa: BLE001
            search_q = raw_title
        if year:
            search_q = f"{search_q} {year}"
        ctx_items.append(("Hledat na ČSFD",
                          f"RunPlugin({build_url(_base_url(), action='open_csfd', query=search_q)})"))

    # v0.0.62: Stahnout CZ titulky pres OpenSubtitles - manualni trigger
    # (jinak se titulky stahuji automaticky pri kliknuti na nedabovanou
    # variantu pres _attach_subtitles). User to muze rovnou zkusit pro
    # diagnostiku nebo pro vsechny filmy bez ohledu na dabing.
    if raw_title:
        try:
            from . import clean_title as _ct
            subs_title = _ct.clean_title(raw_title) or raw_title
        except Exception:  # noqa: BLE001
            subs_title = raw_title
        # mode 'tv' jen pro serial polozky (folder s base_title kind tv).
        subs_mode = "tv" if item_type in ("series", "tvshow", "episode") else "movie"
        subs_params = {
            "action": "subs_download",
            "title": subs_title,
            "mode": subs_mode,
        }
        if year:
            subs_params["year"] = str(year)
        ctx_items.append(("Stáhnout CZ titulky",
                          f"RunPlugin({build_url(_base_url(), **subs_params)})"))

    # Custom items per view (napr. 'Zapomenout' / 'Mark watched' v Continue Watching)
    if extra_context_items:
        ctx_items.extend(extra_context_items)

    if ctx_items:
        try:
            li.addContextMenuItems(ctx_items, replaceItems=False)
        except Exception:  # noqa: BLE001
            pass

    xbmcplugin.addDirectoryItem(
        handle=handle,
        url=url,
        listitem=li,
        isFolder=is_folder,
    )


def _base_url() -> str:
    """Vrátí 'plugin://plugin.video.klempcinema/' - pro stavbu URL v ctx menu."""
    try:
        addon = xbmcaddon.Addon()
        addon_id = addon.getAddonInfo("id")
        return f"plugin://{addon_id}/"
    except Exception:  # noqa: BLE001
        return "plugin://plugin.video.klempcinema/"


def add_next_page_item(
    handle: int,
    base_url: str,
    action: str,
    sort: str,
    current_page: int,
    label: str = "Next page...",
    **extra: Any,
) -> None:
    """Přidá na konec seznamu položku 'Další strana...'."""
    url = build_url(
        base_url,
        action=action,
        sort=sort,
        page=current_page + 1,
        **extra,
    )
    li = xbmcgui.ListItem(label=label)
    li.setArt({"icon": "DefaultFolder.png"})
    xbmcplugin.addDirectoryItem(
        handle=handle,
        url=url,
        listitem=li,
        isFolder=True,
    )


# ---------------------------------------------------------------------------
# Dialogy / notifikace
# ---------------------------------------------------------------------------

def show_notification(message: str, heading: str = "KlempCinema", time_ms: int = 4000) -> None:
    """Zobrazí krátkou notifikaci v rohu obrazovky."""
    xbmcgui.Dialog().notification(heading, message, xbmcgui.NOTIFICATION_INFO, time_ms)


def ask_keyboard(heading: str) -> Optional[str]:
    """Otevře vestavěnou klávesnici a vrátí zadaný text (nebo None při zrušení)."""
    kb = xbmc.Keyboard("", heading)
    kb.doModal()
    if not kb.isConfirmed():
        return None
    text = kb.getText()
    return text or None
