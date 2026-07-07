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
    icon = addon.getAddonInfo("icon")
    fanart = addon.getAddonInfo("fanart")

    # Pokracovat ve sledovani - vzdy nahore pokud je co
    try:
        cont = watched.get_continue_watching(limit=1)
    except Exception:  # noqa: BLE001
        cont = []
    if cont:
        url = ui.build_url(base_url, action="continue_watching")
        ui.add_dir_item(handle=handle, label=_tr(30080),
                        url=url, icon=icon, fanart=fanart)

    # v0.0.79: ZVYRAZNENI DARU - zlata barva + bold + srdicka + posun
    # NA VRCH menu (pod Continue Watching). User vidi polozku ihned a nelze
    # ji prehlednout. Drive byla na konci a snadno se prehledla.
    # Vlastni ikona donate.png (zlaty darek s ruzovou stuhou na tmavem
    # pozadi) - vynika v seznamu a okamzite identifikuje akci.
    donate_label = (
        "[B][COLOR FFFFD700]"
        "\u2665 Poslat autorovi dar (dobrovolne) \u2665"
        "[/COLOR][/B]"
    )
    donate_icon = _addon_icon_for("donate.png")

    menu = [
        # v0.0.79: Dobrovolny dar - zretelne na vrchu, zlate, bold, vlastni ikona
        (30220, "donate",          {}, donate_icon, donate_label),

        # Hledat (high-visibility, akce ne folder)
        (30004, "search",          {}, icon),

        # Hierarchicke sekce - folders
        (30002, "menu_movies",     {}, icon),                          # Filmy >
        (30003, "menu_series",     {}, icon),                          # Serialy >
        # v0.0.65: Streamovaci platformy (Netflix, HBO, Disney+, ...)
        (30150, "menu_platforms",  {}, icon),                          # Platformy >
        # v0.0.67: Voyo (SK) - reality, telenovely, niche obsah ktery TMDB
        # nezná (typu Ruža pre nevestu). Zdroj voyo.markiza.sk SSR.
        (30160, "menu_voyo",       {}, icon),                          # Voyo (SK) >
        (30110, "menu_discover",   {}, icon),                          # Objevuj >
        # v0.0.63: TV program dnes - co dnes hraje v ceske TV (zdroj iDNES)
        (30140, "list_tv_program", {}, icon),                          # TV program dnes
        (30300, "menu_concerts",     {}, icon),                          # Koncerty >
        (30111, "menu_library",    {}, icon),                          # Knihovna >

        # Nastroje
        (30100, "tools",           {}, icon),                          # Nastroje >
    ]
    _render_menu(handle, base_url, menu)


def view_menu_movies(handle: int, base_url: str) -> None:
    """Submenu Filmy - vsechny filmove rubriky."""
    icon = _addon().getAddonInfo("icon")
    menu = [
        (30002, "list_movies",          {"sort": "recent", "page": 1}, icon),
        (30040, "list_movies_new_dub",  {"sort": "recent", "page": 1}, icon),
        (30090, "list_4k",              {"sort": "recent", "page": 1},
            _addon_icon_for("4k.png")),
        (30091, "list_bluray",          {"sort": "recent", "page": 1},
            _addon_icon_for("bluray.png")),
        # v0.0.69: Animovane filmy CZ/SK - TMDB genre 16 filter
        (30095, "list_animated",        {"sort": "recent", "page": 1}, icon),
        (30324, "list_documentary",     {"sort": "recent", "page": 1}, icon),
        (30042, "list_kids",            {"sort": "rating", "page": 1}, icon),
        (30007, "list_latest",          {"sort": "recent", "page": 1}, icon),
    ]
    _render_menu(handle, base_url, menu)


def view_menu_series(handle: int, base_url: str) -> None:
    """Submenu Serialy."""
    icon = _addon().getAddonInfo("icon")
    menu = [
        (30003, "list_series",         {"sort": "rating", "page": 1}, icon),
        (30041, "list_series_new_dub", {"sort": "recent", "page": 1}, icon),
    ]
    _render_menu(handle, base_url, menu)


def view_menu_discover(handle: int, base_url: str) -> None:
    """Submenu Objevuj (TMDB Discover)."""
    icon = _addon().getAddonInfo("icon")
    menu = [
        (30112, "trending_movies", {}, icon),   # Trending filmy
        (30113, "trending_tv",     {}, icon),   # Trending serialy
        (30082, "genres_movies",   {}, icon),   # Zanry filmu
        (30083, "genres_tv",       {}, icon),   # Zanry serialu
    ]
    _render_menu(handle, base_url, menu)


def view_menu_library(handle: int, base_url: str) -> None:
    """Submenu Knihovna - vlastni soubory + historie."""
    icon = _addon().getAddonInfo("icon")
    menu = [
        (30080, "continue_watching", {}, icon),       # Pokracovat
        (30009, "list_my_files",     {"page": 1}, icon),  # Moje soubory
        (30004, "search",            {}, icon),       # Hledat (vc. historie)
    ]
    _render_menu(handle, base_url, menu)


def view_tools(handle: int, base_url: str) -> None:
    """Submenu Nastroje - diagnostika a udrzba."""
    icon = _addon().getAddonInfo("icon")
    # v0.0.62: pridana volba "Test OpenSubtitles" pro overeni titulkove
    # integrace (login + search). Stringy se nactou z language XML.
    # v0.0.77: polozka "donate" presunuta z Nastroju do hlavniho menu
    # (viditelnejsi pro uzivatele).
    menu = [
        (30114, "open_settings",  {}, icon),     # Nastaveni pluginu (RunPluginSettings)
        (30072, "test_login",     {}, icon),     # Test login Webshare
        (30130, "test_subs",      {}, icon),     # Test OpenSubtitles (v0.0.62)
        (30070, "clear_cache",    {}, icon),     # Smazat cache
        (30101, "watched_clear",  {}, icon),     # Smazat historii sledovani
        (30115, "search_history_clear", {}, icon),  # Smazat historii hledani
        # v0.0.79: Manualni refresh Kodi texture cache (icon/fanart)
        # pro pripady kdy auto-refresh po upgrade nestaci.
        (30230, "refresh_icons",  {}, icon),     # Obnovit ikony pluginu
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

