# -*- coding: utf-8 -*-
"""Voyo (SK) discovery."""

from __future__ import annotations

import logging
from typing import Any, Dict

import xbmcplugin  # type: ignore

from .. import voyo as _voyo
from .. import ui
from ..router_common import (
    _addon,
)
from .discover import _add_discover_item

log = logging.getLogger("klempcinema.views.voyo")

def _render_voyo_ws_item(handle: int, base_url: str,
                           item: Dict[str, Any], section: str) -> None:
    """v0.0.83: Voyo tile po WS filtru - jen polozky s variant_idents."""
    title = (item.get("title") or "").strip()
    if not title:
        return
    item_type = item.get("type") or ("movie" if section == "filmy" else "series")

    if item_type == "movie" and item.get("base_title") and item.get("variant_idents"):
        url = ui.build_url(
            base_url, action="play_pick",
            base=item["base_title"], mode="movie",
        )
        is_folder = False
    else:
        sname = item.get("series_name") or title
        url = ui.build_url(base_url, action="list_series_seasons", name=sname)
        is_folder = True

    ui.add_video_item(handle, item, url, is_folder=is_folder)


def _render_voyo_tile(handle: int, base_url: str, tile: Dict[str, Any],
                      section: str) -> None:
    """Vykresli jeden Voyo tile (show/serial/film karta).

    UI flow zalezi na typu obsahu:

      filmy   -> "movie" item, klik = tmdb_play_movie
                 (Webshare hleda titul, pak quality picker pres varianty).

      serialy -> "series" FOLDER, klik = list_series_seasons
                 (Webshare najde vsechny soubory pro daný název, sgrupuje
                 podle SxxEyy do sezon. Klik na sezonu -> episode list.
                 Klik na epizodu -> quality picker.)

      relacie -> "series" FOLDER, stejne jako serialy. Reality show typicky
                 maji SxxEyy (Survivor.S03E01...) nebo aspon datum/cislovku
                 v nazvu. Pokud Webshare uploady nemaji SxxEyy, fallback
                 zobrazi flat episode list.

    User pozadoval: "u kazdeho pořadu rozdeleni abych si pak moh vybrat jaky
    dil chci pustit ... raději bych měl u každeho podkategorii a pak kvalitu
    vyberu". To je presne tenhle pattern - browse podle sezony/epizody
    pred quality pickerem.
    """
    title = (tile.get("title") or "").strip()
    if not title:
        return
    image = (tile.get("image") or "").strip()
    voyo_url = (tile.get("url") or "").strip()
    carousel = (tile.get("carousel") or "").strip()

    plot_lines = []
    if carousel:
        plot_lines.append(f"[B]Voyo:[/B] {carousel}")

    if section == "filmy":
        # Movie flow - rovnou na quality picker.
        play_url = ui.build_url(
            base_url, action="tmdb_play_movie",
            title=title, year="",
        )
        plot_lines.append("Klik -> hledat na Webshare a prehrat film.")
        item_for_ui = {
            "title":  title,
            "year":   None,
            "plot":   "\n".join(plot_lines + (
                [f"voyo.markiza.sk: {voyo_url}"] if voyo_url else [])),
            "poster": image,
            "fanart": image,
            "type":   "movie",
            "dubbed": False,
        }
        ui.add_video_item(handle, item_for_ui, play_url, is_folder=False)
        return

    # Series / Relacie flow - klik OTEVRE folder se sezonami / epizodami.
    # list_series_seasons:
    #   1) Stahne z Webshare vsechny soubory matchujici title
    #   2) Sgrupuje podle SxxEyy -> sezony
    #   3) Vykresli folder list "Sezona 1 [N epizod]" / "Sezona 2 ..." 
    #   4) Pokud Webshare nedal SxxEyy soubory, fallback na flat episode list
    seasons_url = ui.build_url(
        base_url, action="list_series_seasons", name=title,
    )
    plot_lines.append(
        "Klik -> Webshare najde epizody, vyber sezonu a pak kvalitu.")
    if voyo_url:
        plot_lines.append(f"voyo.markiza.sk: {voyo_url}")

    item_for_ui = {
        "title":  title,
        "year":   None,
        "plot":   "\n".join(plot_lines),
        "poster": image,
        "fanart": image,
        "type":   "series",
        "dubbed": False,
    }
    # is_folder=True - tile je rozcestnik na seznam sezon/epizod
    ui.add_video_item(handle, item_for_ui, seasons_url, is_folder=True)


def view_menu_voyo(handle, base_url, params):
    """v0.0.67: Voyo (SK) hlavni menu - 3 sekce + Aktualizovat.

    Volitelne action=menu_voyo&refresh=1 -> smaze cache vsech sekci.
    """
    if params and params.get("refresh") == "1":
        n = _voyo.clear_cache()
        ui.show_notification(
            f"Voyo cache smazana ({n} klicu). Aktualizuji...", time_ms=2500)

    addon = _addon()
    icon = addon.getAddonInfo("icon")
    fanart = addon.getAddonInfo("fanart")

    # Aktualizovat (smaze vsechny Voyo cache klice).
    refresh_url = ui.build_url(base_url, action="menu_voyo", refresh="1")
    ui.add_dir_item(
        handle=handle,
        label="[COLOR FF66CCFF][I]>>> Aktualizovat "
              "(stahnout cerstvy katalog Voyo)[/I][/COLOR]",
        url=refresh_url, icon=icon, fanart=fanart,
    )

    # 3 sekce - pořady (relacie), seriály, filmy.
    sections = [
        ("relacie", "[B]Pořady / Relácie[/B]  (reality show, talk, soutěže)"),
        ("serialy", "[B]Seriály[/B]  (drama, krimi, telenovely, ...)"),
        ("filmy",   "[B]Filmy[/B]  (akční, komedie, rozprávky, ...)"),
    ]
    for sec, label in sections:
        sec_url = ui.build_url(base_url, action="voyo_section", section=sec)
        ui.add_dir_item(
            handle=handle, label=label,
            url=sec_url, icon=icon, fanart=fanart,
        )

    # Informacni text
    ui.add_dir_item(
        handle=handle,
        label="[COLOR FF888888]Zdroj: voyo.markiza.sk (CME, "
              "stejny fond jako CZ Voyo/Oneplay)[/COLOR]",
        url=ui.build_url(base_url, action="menu_voyo"),  # no-op
        icon=icon, fanart=fanart,
    )

    xbmcplugin.setContent(handle, "files")
    xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)


def view_voyo_section(handle, base_url, params):
    """v0.0.67: Sub-view jedne Voyo sekce - list kategorii (carouselov).

    URL: action=voyo_section&section=relacie|serialy|filmy

    Zobrazi:
      >>> Vsechno (flat list, deduplikovany)
      Kategorie 1 (N tiles) >
      Kategorie 2 (N tiles) >
      ...
    """
    section = (params.get("section") or "").strip()
    if section not in _voyo.SECTIONS:
        ui.show_notification(f"Neznama sekce: {section}", time_ms=4000)
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return

    grouped = _voyo.fetch_section(section)

    addon = _addon()
    icon = addon.getAddonInfo("icon")
    fanart = addon.getAddonInfo("fanart")

    if not grouped:
        ui.show_notification(
            "Voyo: nelze stahnout katalog. Zkus 'Aktualizovat' za par minut.",
            time_ms=6000)
        # Fallback - odkaz na Voyo SK web
        browser_url = ui.build_url(base_url, action="open_csfd",
                                    url=_voyo.BASE_URL + _voyo.SECTIONS[section][0])
        ui.add_dir_item(
            handle=handle,
            label="[COLOR FFFFA500][B]>>> Otevrit Voyo SK v prohlizeci[/B][/COLOR]",
            url=browser_url, icon=icon, fanart=fanart,
        )
        xbmcplugin.setContent(handle, "files")
        xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)
        return

    # "Vsechno" folder - flat dedupnuty list
    total = sum(len(items) for (_c, items) in grouped)
    all_url = ui.build_url(base_url, action="voyo_category",
                            section=section, carousel="__all__")
    sec_label = _voyo.get_section_label(section)
    ui.add_dir_item(
        handle=handle,
        label=f"[COLOR FFFFD700][B]>>> Vsechno ({total} polozek)[/B][/COLOR]",
        url=all_url, icon=icon, fanart=fanart,
    )

    # Kazda kategorie/carousel jako folder
    for (carousel, items) in grouped:
        cat_url = ui.build_url(base_url, action="voyo_category",
                                section=section, carousel=carousel)
        # Skipni "Najnovsie epizody" / "TOP 10..." kategorie ktere obsahuju
        # zriedka pouzitelne tipy (1 polozka, propagacne)
        if len(items) < 2:
            continue
        ui.add_dir_item(
            handle=handle,
            label=f"[B]{carousel}[/B]  ({len(items)} polozek)",
            url=cat_url, icon=icon, fanart=fanart,
        )

    xbmcplugin.setContent(handle, "files")
    xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)


def view_voyo_category(handle, base_url, params):
    """v0.0.67: List tiles v jednom carouselu (kategorii) Voyo sekce.

    URL: action=voyo_category&section=relacie&carousel=Reality Show
         (carousel='__all__' -> flat list vsech tiles v section)

    Tile click -> tmdb_play_movie -> Webshare quality picker.
    """
    section = (params.get("section") or "").strip()
    carousel = (params.get("carousel") or "").strip()

    if section not in _voyo.SECTIONS:
        ui.show_notification(f"Neznama sekce: {section}", time_ms=4000)
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return

    if carousel == "__all__":
        tiles = _voyo.get_all_tiles(section)
    else:
        tiles = _voyo.get_category_tiles(section, carousel)

    if not tiles:
        ui.show_notification(
            f"Voyo: kategoria '{carousel}' je prazdna alebo cache miss",
            time_ms=5000)
        xbmcplugin.setContent(handle, "movies")
        xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)
        return

    # v0.0.88: Zobrazit cely katalog Voyo - WS overeni az po kliku
    # (predfiltr v0.0.83 casoval a skryval vetsinu polozek).
    for tile in tiles:
        _render_voyo_tile(handle, base_url, tile, section)

    content_type = "tvshows" if section in ("serialy", "relacie") else "movies"
    xbmcplugin.setContent(handle, content_type)
    xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)
