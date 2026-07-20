# -*- coding: utf-8 -*-
"""Hlavni menu, submenu a welcome flow."""

from __future__ import annotations

import logging
import os

import xbmc  # type: ignore
import xbmcgui  # type: ignore
import xbmcplugin  # type: ignore

from .. import ui
from .. import watched
from ..router_common import (
    _addon,
    _addon_icon_for,
    _has_webshare_credentials,
    _render_menu,
    _tr,
    _tr_safe,
    _welcome_setup_webshare,
)

log = logging.getLogger("klempcinema.views.menu")


def view_root(handle: int, base_url: str) -> None:
    """
    Hlavni menu (v0.0.54: hierarchicke).

    Drive: 14 polozek v jednom seznamu (chaos, prerolovat).
    Ted: 6 sekci + Pokracovat. Filmy/Serialy/Objevuj/Knihovna se rozbalujou.
    """
    # v0.0.63: AUTO-DIAGNOSTIKA UPLNE ODSTRANENA z root menu.
    #
    # Drive (v0.0.62): spustela diagnostiku 'na pozadi' ale s 3 sitovymi
    # dotazy (Webshare 15s + TMDB 6s + CSFD 6s timeouty). Module-level
    # throttle nefungoval, protoze kazda navigace v Kodi = novy Python
    # proces = throttle se resetoval.
    # Dusledek: kazda navigace v rubrice spustila daemon thread se 3
    # network calls. Threadu nelze prerusit syscall socket.recv -
    # plugin proces de facto zustaval naopak az 27s "polozivy".
    # Na Xbox One (slabsi CPU, dohledny network) to vedlo k uplnemu
    # zatuhnuti pluginu. Na PC pri shutdown Kodi cekal na ukonceni
    # techto thready a take zatuhl.
    #
    # Diagnostika je ted dostupna POUZE manualne pres Nastroje ->
    # "Diagnostika" - tam si user ji spusti kdyz neco nefunguje.
    # Kontrola login pri kazde rubrice probehne v _ensure_login()
    # (cachovany token z settings, bez network calls dokud nevyprsi).

    # v0.0.72: Welcome flow - pri prvnim spusteni (chybi ws_user/ws_pass)
    # provedeme uzivatele zadanim Webshare uctu. Pokud user dialog zrusi,
    # menu se vykresli normalne - login si muze vyplnit pozdeji v Settings.
    if not _has_webshare_credentials():
        try:
            _welcome_setup_webshare()
        except Exception as exc:  # noqa: BLE001
            log.warning("Welcome setup selhal: %s", exc)

    addon = _addon()
    fanart = addon.getAddonInfo("fanart")

    # Pokracovat ve sledovani - vzdy nahore pokud je co
    try:
        cont = watched.get_continue_watching(limit=1)
    except Exception:  # noqa: BLE001
        cont = []
    if cont:
        url = ui.build_url(base_url, action="continue_watching")
        ui.add_dir_item(
            handle=handle,
            label=_tr(30080),
            url=url,
            icon=_addon_icon_for("menu/continue.png"),
            fanart=fanart,
        )

    donate_label = (
        "[B][COLOR FFFFD700]"
        "\u2665 Poslat autorovi dar (dobrovolne) \u2665"
        "[/COLOR][/B]"
    )
    donate_icon = _addon_icon_for("donate.png")

    def _mi(name: str) -> str:
        return _addon_icon_for(f"menu/{name}.png")

    # v0.0.149: pictogramy + CMenu (508) jako Sosáč — bez textovych znacek
    menu = [
        (30220, "donate",          {}, donate_icon, donate_label),
        (30004, "search",          {}, _mi("search")),
        (30002, "menu_movies",     {}, _mi("movies")),
        (30003, "menu_series",     {}, _mi("series")),
        (30150, "menu_platforms",  {}, _mi("platforms")),
        (30160, "menu_voyo",       {}, _mi("voyo")),
        (30110, "menu_discover",   {}, _mi("discover")),
        (30140, "list_tv_program", {}, _mi("tv")),
        (30300, "menu_concerts",   {}, _mi("concerts")),
        (30111, "menu_library",    {}, _mi("library")),
        (30100, "tools",           {}, _mi("tools")),
    ]
    _render_menu(handle, base_url, menu)


def view_menu_movies(handle: int, base_url: str) -> None:
    """Submenu Filmy - vsechny filmove rubriky."""
    def _mi(name: str) -> str:
        return _addon_icon_for(f"menu/{name}.png")

    menu = [
        (30002, "list_movies",          {"sort": "recent", "page": 1}, _mi("movies_all")),
        (30040, "list_movies_new_dub",  {"sort": "recent", "page": 1}, _mi("movies_dub")),
        (30090, "list_4k",              {"sort": "recent", "page": 1}, _addon_icon_for("4k.png")),
        (30091, "list_bluray",          {"sort": "recent", "page": 1}, _addon_icon_for("bluray.png")),
        (30095, "list_animated",        {"sort": "recent", "page": 1}, _mi("movies_animated")),
        (30324, "list_documentary",     {"sort": "recent", "page": 1}, _mi("movies_docs")),
        (30042, "list_kids",            {"sort": "rating", "page": 1}, _mi("movies_kids")),
        (30007, "list_latest",          {"sort": "recent", "page": 1}, _mi("movies_latest")),
    ]
    _render_menu(handle, base_url, menu)


def view_menu_series(handle: int, base_url: str) -> None:
    """Submenu Serialy."""
    def _mi(name: str) -> str:
        return _addon_icon_for(f"menu/{name}.png")

    menu = [
        (30003, "list_series",         {"sort": "rating", "page": 1}, _mi("series_all")),
        (30041, "list_series_new_dub", {"sort": "recent", "page": 1}, _mi("series_dub")),
    ]
    _render_menu(handle, base_url, menu)


def view_menu_discover(handle: int, base_url: str) -> None:
    """Submenu Objevuj (TMDB Discover)."""
    def _mi(name: str) -> str:
        return _addon_icon_for(f"menu/{name}.png")

    menu = [
        (30112, "trending_movies", {}, _mi("trending_movies")),
        (30113, "trending_tv",     {}, _mi("trending_tv")),
        (30082, "genres_movies",   {}, _mi("genres_movies")),
        (30083, "genres_tv",       {}, _mi("genres_tv")),
    ]
    _render_menu(handle, base_url, menu)


def view_menu_library(handle: int, base_url: str) -> None:
    """Submenu Knihovna - vlastni soubory + historie."""
    def _mi(name: str) -> str:
        return _addon_icon_for(f"menu/{name}.png")

    menu = [
        (30080, "continue_watching", {}, _mi("continue")),
        (30009, "list_my_files",     {"page": 1}, _mi("my_files")),
        (30004, "search",            {}, _mi("search")),
    ]
    _render_menu(handle, base_url, menu)


def view_tools(handle: int, base_url: str) -> None:
    """Submenu Nastroje - diagnostika a udrzba."""
    tools_icon = _addon_icon_for("menu/tools.png")
    menu = [
        (30114, "open_settings",  {}, tools_icon),
        (30072, "test_login",     {}, tools_icon),
        (30130, "test_subs",      {}, tools_icon),
        (30070, "clear_cache",    {}, tools_icon),
        (30101, "watched_clear",  {}, tools_icon),
        (30115, "search_history_clear", {}, tools_icon),
        (30230, "refresh_icons",  {}, tools_icon),
    ]
    _render_menu(handle, base_url, menu)


def view_refresh_icons(handle: int, base_url: str, params: dict) -> None:
    """v0.0.79: Vynuti Kodi aby invalidoval texture cache pro tento addon.

    Pouziti: kdyz user vidi starou ikonu po upgrade (Kodi cachuje
    icon.png v Textures DB) a auto-refresh v _check_post_upgrade
    nepomohl. Volat z Nastroje > Obnovit ikony pluginu.
    """
    try:
        import xbmc  # type: ignore
        # Nejprve invaliduj last_seen_version aby _check_post_upgrade
        # priste znovu refreshnul (= force re-trigger).
        try:
            _addon().setSetting("last_seen_version", "")
        except Exception:  # noqa: BLE001
            pass
        # UpdateLocalAddons: znovu nacte addon manifest a invaliduje
        # texture cache. ReloadSkin force-redraw UI.
        xbmc.executebuiltin('UpdateLocalAddons')
        xbmc.sleep(200)
        xbmc.executebuiltin('ReloadSkin()')
        try:
            ui.show_notification(
                _tr_safe(30231,
                         "Ikony obnoveny - pokud nestaci, odinstaluj a znovu nainstaluj plugin."),
                time_ms=5000,
            )
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        log.warning("refresh_icons selhal: %s", exc)
    try:
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
    except Exception:  # noqa: BLE001
        pass


def view_donate(handle: int, base_url: str, params: dict) -> None:
    """
    Zobrazi dialog s DAREM (CZ QR SPD format) a vysvetlujicim textem.

    Pravni ramec (proc je to bezpecne pro autora i uzivatele):
    - Platba je oznacena jako "Dar" - bezuplatne nabyti dle obc. zakoniku
    - Dar od fyzicke osoby fyzicke osobe do 50 000 Kc/rok od jednoho darce
      je osvobozen od dane z prijmu (§10 odst. 3 pism. c) zakona c.
      586/1992 Sb. o danich z prijmu).
    - Uzivatel nezskava zadnou protihodnotu (obsah, sluzbu, prioritu).
      Plugin funguje stejne pro vsechny.
    - Obsah neposkytuje autor pluginu - obsah poskytuje tret strana
      (Webshare.cz), ke ktere si uzivatel sjednava vlastni predplatne.
    """
    addon = _addon()
    addon_path = addon.getAddonInfo("path")
    qr_path = os.path.join(addon_path, "resources", "media", "donate_qr.png")

    iban_pretty = "CZ95 5500 0000 0010 2685 1852"

    # v0.0.80: lokalizovany text - drive hardcoded v cestine, ted bere
    # preklady ze strings.po (cz pro CZ Kodi, EN pro anglicky Kodi atd.)
    info_lines = [
        _tr_safe(30240, "GIFT FOR THE AUTHOR"),
        "",
        f"{_tr_safe(30241, 'IBAN')}:    {iban_pretty}",
        f"{_tr_safe(30242, 'Currency')}:    CZK",
        f"{_tr_safe(30243, 'Message')}:    {_tr_safe(30244, 'Gift KlempCinema')}",
        "",
        _tr_safe(30245, "How to send:"),
        _tr_safe(30246, "  1) Open your banking app"),
        _tr_safe(30247, "  2) Pay -> Scan QR code"),
        _tr_safe(30248, "  3) Scan the QR from Kodi screen"),
        _tr_safe(30249, "  4) Enter amount and send"),
        "",
        _tr_safe(30250, "Thanks! - Bicalorman"),
    ]
    info_text = "\n".join(info_lines)

    # v0.0.79 - DULEZITE: poradí akci je upraveno aby nedoslo k UI race
    # ShowPicture musi byt volan AZ PO endOfDirectory, jinak Kodi
    # otevre picture viewer s jeste otevrenym plugin handle → flicker
    # a mouse focus uvazne (uzivatelsky report v0.0.78).
    show_qr_requested = False
    try:
        dlg = xbmcgui.Dialog()
        dlg.textviewer(
            _tr_safe(30220, "Send a gift to the author (voluntary)"),
            info_text,
        )
        if os.path.exists(qr_path):
            show_qr_requested = bool(dlg.yesno(
                _tr_safe(30221, "Show QR code"),
                _tr_safe(
                    30222,
                    "Show QR code for scanning in your banking app?"
                ),
                yeslabel=_tr_safe(30223, "Show QR"),
                nolabel=_tr_safe(30224, "Close"),
            ))
    except Exception as exc:  # noqa: BLE001
        log.warning("view_donate dialog selhal: %s", exc)

    # Nejprve zavri plugin handle, AZ POTOM otevri picture viewer.
    try:
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
    except Exception:  # noqa: BLE001
        pass

    if show_qr_requested and os.path.exists(qr_path):
        try:
            import xbmc  # type: ignore
            # Drobne pockame nez Kodi zpracuje endOfDirectory a UI se ustali.
            xbmc.sleep(150)
            # wait=True (druhy argument) - skript pocka nez se picture viewer
            # uzavre, takze plugin proces nezustane v hybrid state.
            xbmc.executebuiltin(f'ShowPicture({qr_path})', True)
        except Exception as exc:  # noqa: BLE001
            log.warning("ShowPicture selhal: %s", exc)
            try:
                xbmcgui.Dialog().ok(
                    _tr_safe(30220, "Send a gift to the author (voluntary)"),
                    f"QR: {qr_path}",
                )
            except Exception:  # noqa: BLE001
                pass


def view_open_csfd(handle: int, base_url: str, params: dict) -> None:
    """
    Otevre ČSFD URL ve vychozim browseru.

    Pouziti z context menu:
        action=open_csfd&url=https://csfd.cz/film/...
        action=open_csfd&query=Joker  -> hledat na ČSFD
    """
    from urllib.parse import quote_plus
    url = (params.get("url") or "").strip()
    query = (params.get("query") or "").strip()
    if not url and query:
        url = f"https://www.csfd.cz/hledat/?q={quote_plus(query)}"
    if not url:
        ui.show_notification("Chybi URL nebo nazev")
        return
    try:
        # Windows / macOS / Linux univerzalni open-in-browser
        import webbrowser
        webbrowser.open(url)
        ui.show_notification(f"Otevirám ČSFD: {url[:50]}", time_ms=2500)
    except Exception:  # noqa: BLE001
        # Fallback - jen ukaz URL v dialogu, user si ho otevre rucne
        try:
            import xbmcgui  # type: ignore
            xbmcgui.Dialog().textviewer("ČSFD link", url)
        except Exception:  # noqa: BLE001
            ui.show_notification(url[:50])


def view_open_settings(handle: int, base_url: str, params: dict) -> None:
    """Otevre Settings dialog pluginu (alternativa k pravomu kliknuti)."""
    try:
        _addon().openSettings()
    except Exception:  # noqa: BLE001
        log.exception("openSettings selhalo")
        ui.show_notification("Nelze otevrit nastaveni")
    # Po zavreni Settings dialogu vykreslime Nastroje znovu
    view_tools(handle, base_url)

