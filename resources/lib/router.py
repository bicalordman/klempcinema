# -*- coding: utf-8 -*-
"""
router.py
---------
Centrální směrovač akcí pluginu KlempCinema.

Podporované akce:
    action=root                    - hlavní menu
    action=list_movies             - Filmy (sort=recent, grouped, play_pick)
    action=list_movies_new_dub     - Nově dabované filmy
    action=list_series             - Seriály (grouped by series name)
    action=list_series_new_dub     - Nově dabované seriály
    action=list_series_episodes    - epizody konkrétního seriálu (?name=...)
    action=list_kids               - Pohádky CZ/SK
    action=list_latest             - Novinky (flat seznam)
    action=list_my_files           - Moje soubory (Webshare file_list)
    action=search                  - vyhledávání
    action=play                    - přímé přehrání (?id=..., ?type=...)
    action=play_pick               - výběr kvality + přehrání (?base=..., ?mode=...)
"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import Any, Dict, List

import xbmcaddon  # type: ignore
import xbmcgui  # type: ignore
import xbmcplugin  # type: ignore

from . import api_webshare
from . import clean_title as _ct
from . import csfd
from . import player_tracker
from . import prefetch
from . import shutdown as _shutdown
from . import subtitles
from . import tmdb
from . import tv_program
from . import ui
from . import voyo as _voyo  # v0.0.67: Voyo SK discovery
from . import watched


log = logging.getLogger("klempcinema.router")

# Maximální počet stránek, do kterého ukazujeme "Další strana…".
# Webshare search vrací max 50 stránek * 200 položek = bohatě dost.
MAX_PAGE = 50


# ---------------------------------------------------------------------------
# Pomocné věci
# ---------------------------------------------------------------------------

def _addon() -> xbmcaddon.Addon:
    return xbmcaddon.Addon()


def _tr(string_id: int) -> str:
    return _addon().getLocalizedString(string_id)


def _extract_year_from_base(base: str):
    """Z 'Inception 2010' vrátí (clean_title, year)."""
    if not base:
        return "", None
    import re
    m = re.search(r"\b((?:19|20)\d{2})\b", base)
    year = int(m.group(1)) if m else None
    clean = re.sub(r"\b(?:19|20)\d{2}\b", "", base)
    clean = re.sub(r"\s+", " ", clean).strip(" -.")
    return clean, year


def _extract_episode_from_base(base: str):
    """Z 'Game of Thrones S01E02' vrátí (series_name, season, episode)."""
    if not base:
        return "", None, None
    import re
    m = re.search(r"(.*?)\s*S(\d{1,2})\s*[EX]\s*(\d{1,3})", base, re.I)
    if not m:
        return base, None, None
    return m.group(1).strip(" -."), int(m.group(2)), int(m.group(3))


def _attach_subtitles(li: xbmcgui.ListItem,
                       title: str,
                       year=None,
                       mode: str = "movie",
                       season=None,
                       episode=None,
                       imdb_id: str = "") -> None:
    """
    Pokud je auto-subs zapnuté, vyhledá CZ titulky (přes TMDB->IMDB->OpenSubs)
    a přidá je k ListItemu. Neselhává – chybu jen zaloguje.
    """
    if not subtitles.is_enabled():
        return

    try:
        # Pokud nemáme IMDB ID, najdi ho přes TMDB.
        if not imdb_id and title:
            meta = tmdb.search_tv(title) if mode == "tv" else tmdb.search_movie(title, year)
            if meta and meta.get("tmdb_id"):
                kind = "tv" if mode in ("series", "episode", "tv") else "movie"
                imdb_id = tmdb.get_imdb_id(meta["tmdb_id"], kind=kind) or ""

        srt = subtitles.fetch_for_title(
            title=title,
            year=year,
            imdb_id=imdb_id or None,
            mode="episode" if mode in ("episode", "tv") else "movie",
            season=season,
            episode=episode,
        )
        if srt:
            li.setSubtitles([srt])
            log.info("attach_subtitles: přidán titulek %s", srt)
            # v0.0.79: notifikace odebrana - zobrazovala se TESNE pred
            # setResolvedUrl a flashla na obrazovce mezi pluginem a video
            # playerem. Async cesta (subtitles.attach_async) ji uz nezobrazuje.
    except Exception as exc:  # noqa: BLE001
        log.exception("attach_subtitles selhalo: %s", exc)


def _auto_pick_quality_enabled() -> bool:
    """
    Setting 'auto_pick_quality' (v0.0.51) - pokud zapnuto, klik na film
    rovnou prehraje nejvyssi dostupnou kvalitu bez quality picker dialogu.
    Default OFF - klasicky picker UX.
    """
    try:
        addon = _addon()
        raw = (addon.getSetting("auto_pick_quality") or "false").lower()
        return raw in ("true", "1")
    except Exception:  # noqa: BLE001
        return False


def _ensure_login() -> str:
    """Zajistí Webshare token; při chybě zobrazí notifikaci."""
    token = api_webshare.get_token()
    if token:
        return token

    # Pokud token chybí, nejde o chybějící credentials (máme builtin
    # fallback v api_webshare._read_credentials), ale o selhání loginu
    # (špatné heslo, rate limit, síť). Ukážeme generický login error.
    ui.show_notification(_tr(30021))
    return ""


# ---------------------------------------------------------------------------
# Společné renderery
# ---------------------------------------------------------------------------

def _add_next_page(handle, base_url, list_action, sort, page, has_more=True, **extra):
    """
    Přidá 'Další strana…' pokud:
      - jsme pod MAX_PAGE
      - has_more je True (pagination ví, že je víc obsahu)
    """
    if page >= MAX_PAGE:
        return
    if not has_more:
        return
    ui.add_next_page_item(
        handle=handle, base_url=base_url, action=list_action,
        sort=sort, current_page=page, label=_tr(30005), **extra,
    )


def _split_result(result):
    """
    Příjem z api_webshare.get_* funkcí.
    Podporuje:
      - tuple (items, has_more) - nový formát
      - list items              - zpětná kompatibilita
    Vrací: (items, has_more)
    """
    if isinstance(result, tuple) and len(result) == 2:
        items, has_more = result
        return (items or []), bool(has_more)
    return (result or []), True  # legacy: vždy předpokládej víc


# v0.0.63: lookup zda dany list_action podporuje refresh (mapping v _RUBRIC_CACHE_PREFIXES).
# Definovano nize, takze pouzivame string check pri renderu.
_REFRESHABLE_ACTIONS = {
    "list_movies", "list_movies_new_dub", "list_kids", "list_series",
    "list_series_new_dub", "list_latest", "list_4k", "list_bluray",
    "list_animated",  # v0.0.69
}


def _add_refresh_button(handle: int, base_url: str, list_action: str,
                         sort: str = "", query: str = "") -> None:
    """v0.0.63: prida '>>> Aktualizovat' button na vrch rubriky.

    Klik -> refresh_rubrika action -> smaze cache klic + redirect zpet
    na rubriku s page=1. Tim user dostane cerstva data z Webshare
    namisto cekani 30 min na expiraci TTL.

    Volat JEN na page 1 - na dalsich strankach by to byl spam.
    Volat JEN pro rubric ktere maji refresh mapping.
    """
    if list_action not in _REFRESHABLE_ACTIONS:
        return
    addon = _addon()
    icon = addon.getAddonInfo("icon")
    fanart = addon.getAddonInfo("fanart")
    extra = {}
    if sort:
        extra["sort"] = sort
    if query:
        extra["query"] = query
    url = ui.build_url(base_url, action="refresh_rubrika",
                       target=list_action, **extra)
    ui.add_dir_item(
        handle=handle,
        label="[COLOR FF66CCFF][I]>>> Aktualizovat "
              "(stahnout cerstva data z Webshare)[/I][/COLOR]",
        url=url, icon=icon, fanart=fanart,
    )


def _render_movie_list(
    handle: int,
    base_url: str,
    result,
    list_action: str,
    page: int,
    sort: str,
    content: str = "movies",
    **next_extra,
) -> None:
    """
    Vykreslí seznam filmů/pohádek. Když result=(items, has_more),
    'Další strana' se přidá jen pokud has_more=True.
    """
    items, has_more = _split_result(result)

    # v0.0.63: refresh button na vrchu page 1 (force fresh fetch z Webshare).
    if page == 1:
        _add_refresh_button(handle, base_url, list_action, sort=sort)

    for item in items:
        if item.get("base_title"):
            url = ui.build_url(base_url, action="play_pick",
                               base=item["base_title"], mode="movie")
        else:
            url = ui.build_url(base_url, action="play",
                               id=item.get("id", ""), type="movie")
        ui.add_video_item(handle, item, url, is_folder=False)

    # Notifikaci 'obsah nenalezen' zobraz JEN pokud jsme na page 1.
    # Na vyšších stránkách to ruší - user už něco viděl předtím.
    if not items and page == 1:
        ui.show_notification(_tr(30023))

    _add_next_page(handle, base_url, list_action, sort, page,
                   has_more=has_more, **next_extra)

    xbmcplugin.setContent(handle, content)
    xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)


def _render_series_list(
    handle: int,
    base_url: str,
    result,
    list_action: str,
    page: int,
    sort: str,
    **next_extra,
) -> None:
    """Vykreslí seznam seriálů – každý jako složka -> epizody."""
    items, has_more = _split_result(result)

    # v0.0.63: refresh button na vrchu page 1 pro serialove rubric.
    if page == 1:
        _add_refresh_button(handle, base_url, list_action, sort=sort)

    skipped = 0
    for item in items:
        sname = (item.get("series_name") or "").strip()
        item_id = (item.get("id") or "").strip()
        if sname:
            # Klik na seriál -> seznam sezón (S01/S02/...). Když nemáme
            # rozumný TMDB lookup, view_list_series_seasons fallback
            # na ploché epizody automaticky.
            url = ui.build_url(base_url, action="list_series_seasons",
                               name=sname)
            ui.add_video_item(handle, item, url, is_folder=True)
        elif item_id:
            # Single-file fallback (např. neidentifikovaná epizoda mimo grupy).
            url = ui.build_url(base_url, action="play",
                               id=item_id, type="series")
            ui.add_video_item(handle, item, url, is_folder=False)
        else:
            # Item bez series_name i bez id - nepřidávat (klik by nešel).
            log.warning("_render_series_list: skip item bez series_name+id: %s",
                        {k: item.get(k) for k in ("title", "base_title", "type")})
            skipped += 1
    if skipped:
        log.info("_render_series_list: preskoceno %d items bez URL", skipped)

    if not items and page == 1:
        ui.show_notification(_tr(30023))

    _add_next_page(handle, base_url, list_action, sort, page,
                   has_more=has_more, **next_extra)

    xbmcplugin.setContent(handle, "tvshows")
    xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)


def _render_flat_list(
    handle: int,
    base_url: str,
    result,
    list_action: str,
    page: int,
    sort: str,
    content: str = "movies",
    **next_extra,
) -> None:
    """
    Flat seznam (Novinky, Moje soubory, Hledání).

    Items můžou mít dva tvary:
      1) base_title + variant_idents  -> quality picker (action=play_pick)
      2) id (přímý Webshare ident)    -> přímý play (action=play)

    Rozhodne se per-item: pokud item nese base_title (vznikl přes
    _movies_from_groups / _series_from_groups), jdeme přes picker.
    Jinak fallback na přímý play s id.
    """
    items, has_more = _split_result(result)

    # v0.0.63: refresh button na vrchu page 1 pro list_latest, list_my_files.
    # 'search' rubric ma vlastni '>>> Nove hledani' button, nedavame
    # refresh, query: na vyhledani neda smysl klika obnovit cache.
    query_param = next_extra.get("query", "")
    if page == 1 and not query_param:
        _add_refresh_button(handle, base_url, list_action, sort=sort)

    for item in items:
        item_type = item.get("type") or "movie"

        if item.get("base_title"):
            # Film z grouped listing - varianty kvality jsou v cache
            mode = "episode" if item_type == "episode" else "movie"
            url = ui.build_url(
                base_url,
                action="play_pick",
                base=item["base_title"],
                mode=mode,
            )
        elif item.get("series_name"):
            # Seriál - otevři sezóny
            url = ui.build_url(
                base_url,
                action="list_series_seasons",
                name=item["series_name"],
            )
            ui.add_video_item(handle, item, url, is_folder=True)
            continue
        else:
            # Klasický flat soubor - přímý play přes ident
            url = ui.build_url(
                base_url,
                action="play",
                id=item.get("id", ""),
                type=item_type,
                dubbed="1" if item.get("dubbed") else "0",
                title=item.get("title_localized") or item.get("title") or "",
                year=str(item["year"]) if item.get("year") else "",
            )
        ui.add_video_item(handle, item, url, is_folder=False)

    if not items and page == 1:
        ui.show_notification(_tr(30023))

    _add_next_page(handle, base_url, list_action, sort, page,
                   has_more=has_more, **next_extra)

    xbmcplugin.setContent(handle, content)
    xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)


# ---------------------------------------------------------------------------
# Views – hlavní menu + rubriky
# ---------------------------------------------------------------------------

# v0.0.62: throttling - diagnostiku spustime max 1x za 10 minut
# (drive: kazde otevreni root menu = self_test, na Xboxu pomale).
_LAST_DIAG_TS: float = 0.0
_DIAG_INTERVAL = 600.0  # 10 min


def _run_diagnostics_silently() -> None:
    """
    v0.0.62: SPOUSTI SE NA POZADI - root menu se otevre okamzite,
    diagnostika probehne v background threadu. Pokud nejaky test
    selze, notifikace se ukaze az kdyz dobehne (typicky <2s).

    Drive (v0.0.61): synchronni 3-5s blokovani pri kazdem otevreni
    root menu na Xboxu. Ted: 0ms blocking.

    Throttle: 1x za 10 min (a pak vyuziva 5min cache v self_test funkcich).
    """
    import threading
    global _LAST_DIAG_TS

    now = time.time()
    if (now - _LAST_DIAG_TS) < _DIAG_INTERVAL:
        log.debug("diagnostics: throttle skip (last %.0fs ago)",
                  now - _LAST_DIAG_TS)
        return
    _LAST_DIAG_TS = now

    t = threading.Thread(
        target=_diagnostics_worker,
        name="klempcinema-diag",
        daemon=True,
    )
    t.start()


def _diagnostics_worker() -> None:
    """Background diagnostika - nikdy neblokuje UI."""
    failures: List[str] = []

    try:
        ws_test = api_webshare.login_diagnose()
        if ws_test.get("ok"):
            log.info("Webshare login: OK (%s)", ws_test.get("message"))
        else:
            code = ws_test.get("code", "?") or "?"
            msg = (ws_test.get("message") or "").strip() or "neznáma chyba"
            mapped = {
                "MISSING_CREDENTIALS":          "Webshare: chybi jmeno nebo heslo",
                "AUTH_LOGIN_INVALID_USER":      "Webshare: neexistujici uzivatel",
                "AUTH_LOGIN_INVALID_PASSWORD":  "Webshare: SPATNE HESLO",
                "AUTH_LOGIN_TOO_MANY_ATTEMPTS": "Webshare: prilis mnoho pokusu",
                "NETWORK":                       "Webshare: nedostupny (sit)",
            }.get(code, f"Webshare: {msg}")
            failures.append(mapped)
            log.warning("Webshare login FAIL: code=%s msg=%s", code, msg)
    except Exception:  # noqa: BLE001
        log.exception("Webshare diagnose crash")

    try:
        tmdb_test = tmdb.self_test()
        if tmdb_test.get("ok"):
            log.info("TMDB self-test: OK (key_type=%s)", tmdb_test.get("key_type"))
        else:
            reason = tmdb_test.get("reason", "neznáma chyba")
            failures.append(f"TMDB: {reason}")
            log.warning("TMDB self-test: FAIL (%s)", reason)
    except Exception:  # noqa: BLE001
        log.exception("TMDB self-test crash")

    try:
        csfd_test = csfd.self_test()
        status = csfd_test.get("status", "unknown")
        if csfd_test.get("ok"):
            log.info("CSFD self-test: OK")
        elif status == "disabled":
            log.info("CSFD: vypnute v nastaveni")
        elif status == "blocked":
            log.warning("CSFD blocked (Cloudflare)")
        else:
            log.warning("CSFD: %s (%s)", status, csfd_test.get("reason"))
    except Exception:  # noqa: BLE001
        log.exception("CSFD self-test crash")

    if failures:
        summary = " | ".join(failures[:2])
        try:
            import xbmcgui  # type: ignore
            xbmcgui.Dialog().notification(
                "KlempCinema", summary,
                xbmcgui.NOTIFICATION_ERROR, 5000
            )
        except Exception:  # noqa: BLE001
            ui.show_notification(summary, time_ms=5000)


def _addon_icon_for(name: str) -> str:
    """Vrati cestu k custom ikone, fallback na default addon icon."""
    addon = _addon()
    path = addon.getAddonInfo("path")
    candidate = os.path.join(path, "resources", "icons", name)
    return candidate if os.path.exists(candidate) else addon.getAddonInfo("icon")


def _render_menu(handle: int, base_url: str, menu) -> None:
    """Sjednocene vykresleni statickeho menu (list of tuples).

    Podporuje 4-tuple (label_id, action, params, icon) i rozsireny
    5-tuple (label_id, action, params, icon, custom_label) - kde
    custom_label preda jiz hotovy/formatovany string (napr. s BBCode
    barvami pro zvyrazneni dulezitych polozek).
    """
    addon = _addon()
    icon_default = addon.getAddonInfo("icon")
    fanart = addon.getAddonInfo("fanart")
    for entry in menu:
        if len(entry) >= 5:
            label_id, action, params, item_icon, custom_label = entry[:5]
            label = custom_label if custom_label else _tr(label_id)
        else:
            label_id, action, params, item_icon = entry
            label = _tr(label_id)
        url = ui.build_url(base_url, action=action, **params)
        ui.add_dir_item(handle=handle, label=label,
                        url=url, icon=item_icon or icon_default, fanart=fanart)
    xbmcplugin.setContent(handle, "files")
    xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)


# ---------------------------------------------------------------------------
# Welcome setup (v0.0.72): při prvním spuštění bez credentials provedeme
# uživatele zadáním Webshare účtu (jméno → heslo → test loginu).
# ---------------------------------------------------------------------------

def _has_webshare_credentials() -> bool:
    """True pokud user už má vyplněn ws_user i ws_pass v settings."""
    try:
        addon = _addon()
        u = (addon.getSetting("ws_user") or "").strip()
        p = addon.getSetting("ws_pass") or ""
        return bool(u and p)
    except Exception:  # noqa: BLE001
        return False


def _ask_input(heading: str, hidden: bool = False, default: str = "") -> str:
    """
    Otevře Kodi keyboard. Vrátí zadaný text nebo "" pokud user zrušil.

    hidden=True - heslo (skryté znaky).
    """
    try:
        import xbmc  # type: ignore
        kb = xbmc.Keyboard(default, heading, hidden)
    except Exception:  # noqa: BLE001
        kb = None
    if kb is None:
        try:
            return xbmcgui.Dialog().input(
                heading=heading,
                defaultt=default,
                type=xbmcgui.INPUT_ALPHANUM,
                option=(xbmcgui.ALPHANUM_HIDE_INPUT if hidden else 0),
            ) or ""
        except Exception:  # noqa: BLE001
            return ""
    kb.doModal()
    if not kb.isConfirmed():
        return ""
    return kb.getText() or ""


def _welcome_setup_webshare() -> bool:
    """
    První-spuštění welcome flow: vyzve uživatele aby zadal Webshare jméno
    a heslo, uloží do settings a otestuje přihlášení.

    Vrátí True pokud login uspěl (nebo user už má vyplněno),
    False pokud user dialog zrušil nebo login selhal.
    """
    if _has_webshare_credentials():
        return True

    addon = _addon()

    try:
        dlg = xbmcgui.Dialog()
        cont = dlg.yesno(
            heading=_tr_safe(30200, "Welcome to KlempCinema"),
            message=_tr_safe(
                30201,
                "Webshare account is required to play videos. Enter your "
                "Webshare username and password now. You can skip this dialog "
                "and fill credentials later in addon settings."
            ),
            yeslabel=_tr_safe(30202, "Enter account"),
            nolabel=_tr_safe(30203, "Skip"),
        )
    except Exception:  # noqa: BLE001
        cont = False

    if not cont:
        return False

    # ---- Username ---------------------------------------------------------
    user = _ask_input(_tr_safe(30204, "Webshare username"), hidden=False)
    if not user.strip():
        try:
            ui.show_notification(
                _tr_safe(30205, "Cancelled - fill the account in addon settings."),
                time_ms=4000,
            )
        except Exception:  # noqa: BLE001
            pass
        return False
    user = user.strip()

    # ---- Heslo ------------------------------------------------------------
    # v0.0.76: pred zadanim hesla se zeptame, zda chce user heslo videt nebo
    # mit skryte (***). Skryte heslo je bezpecnejsi pred pohledem, ale pri
    # preklepu se chyba nezobrazi - radej se na to zeptame.
    try:
        hide_pwd = xbmcgui.Dialog().yesno(
            _tr_safe(30200, "Welcome to KlempCinema"),
            _tr_safe(
                30212,
                "Enter password visibly or hidden? We recommend VISIBLE - "
                "you can see what you are typing and avoid typos."
            ),
            yeslabel=_tr_safe(30213, "Visible"),
            nolabel=_tr_safe(30214, "Hidden"),
        )
        hidden_input = not hide_pwd
    except Exception:  # noqa: BLE001
        hidden_input = False

    pwd = _ask_input(_tr_safe(30206, "Webshare password"), hidden=hidden_input)
    if not pwd:
        try:
            ui.show_notification(
                _tr_safe(30205, "Cancelled - fill the account in addon settings."),
                time_ms=4000,
            )
        except Exception:  # noqa: BLE001
            pass
        return False

    # ---- Uložení do settings ---------------------------------------------
    try:
        addon.setSetting("ws_user", user)
        addon.setSetting("ws_pass", pwd)
        # Vynulujeme případný cached token aby se zkusil čerstvý login.
        addon.setSetting("ws_token", "")
    except Exception as exc:  # noqa: BLE001
        log.warning("Nepodařilo se uložit credentials: %s", exc)
        try:
            ui.show_notification(
                _tr_safe(30207, "Save failed - fill the account in addon settings."),
                time_ms=5000,
            )
        except Exception:  # noqa: BLE001
            pass
        return False

    # ---- Test loginu ------------------------------------------------------
    try:
        api_webshare._invalidate_token()
    except Exception:  # noqa: BLE001
        pass

    try:
        progress = xbmcgui.DialogProgress()
        progress.create(
            _tr_safe(30200, "Welcome to KlempCinema"),
            _tr_safe(30208, "Verifying Webshare login..."),
        )
    except Exception:  # noqa: BLE001
        progress = None

    token = None
    try:
        token = api_webshare.login(user, pwd)
    except Exception as exc:  # noqa: BLE001
        log.warning("Welcome login selhal: %s", exc)

    if progress is not None:
        try:
            progress.close()
        except Exception:  # noqa: BLE001
            pass

    if token:
        try:
            addon.setSetting("ws_token", token)
        except Exception:  # noqa: BLE001
            pass
        try:
            ui.show_notification(
                _tr_safe(30209, "Webshare login OK."),
                time_ms=3500,
            )
        except Exception:  # noqa: BLE001
            pass
        return True

    # Login selhal - ziskej presnou chybu z Webshare a ukaz ji userovi.
    try:
        diag = api_webshare.login_diagnose(force=True)
    except Exception:  # noqa: BLE001
        diag = {"code": "?", "message": "neznama chyba"}

    code = (diag.get("code") or "?")
    message = (diag.get("message") or "")
    if code == "AUTH_LOGIN_INVALID_PASSWORD":
        diag_text = (
            _tr_safe(30260, "Webshare: WRONG PASSWORD") + "\n\n"
            + _tr_safe(30261, "Password does not match account '{u}'.").format(u=user)
            + "\n\n"
            + _tr_safe(
                30262,
                "Possible causes: password typo, password changed on "
                "webshare.cz, extra/missing space or Caps Lock."
            )
        )
    elif code == "AUTH_LOGIN_INVALID_USER":
        diag_text = (
            _tr_safe(30263, "Webshare: USER DOES NOT EXIST") + "\n\n"
            + _tr_safe(
                30264,
                "Account '{u}' does not exist on Webshare. Check the username "
                "- it must match exactly the one used to log in at webshare.cz "
                "(including diacritics and case)."
            ).format(u=user)
        )
    elif code == "MISSING_CREDENTIALS":
        diag_text = _tr_safe(
            30265,
            "Missing username or password in settings. Run setup again "
            "(Addons > KlempCinema)."
        )
    else:
        diag_text = (
            _tr_safe(30266, "Webshare did not respond as expected.") + "\n\n"
            + f"code:    {code}\n"
            + f"message: {message}\n\n"
            + _tr_safe(
                30267,
                "Check internet connection and try again via Tools > "
                "Test Webshare login."
            )
        )

    try:
        xbmcgui.Dialog().ok(
            _tr_safe(30210, "Webshare: login failed"),
            diag_text,
        )
    except Exception:  # noqa: BLE001
        pass
    return False


def _tr_safe(string_id: int, fallback: str) -> str:
    """Vrátí lokalizovaný string, nebo fallback pokud chybí překlad."""
    try:
        s = _tr(string_id)
    except Exception:  # noqa: BLE001
        return fallback
    if not s or s == str(string_id):
        return fallback
    return s


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


def _prefetch_next(action: str, fetcher, sort: str, page: int, has_more: bool, **extra):
    """Spustí na pozadí dofetch další stránky."""
    key = f"{action}:{sort}:{page + 1}:{':'.join(f'{k}={v}' for k, v in extra.items())}"
    prefetch.schedule(
        cache_key=key,
        fetcher=lambda: fetcher(sort=sort, page=page + 1, **extra),
        page=page,
        has_more=has_more,
    )


def view_list_movies(handle, base_url, params):
    _ensure_login()
    sort = params.get("sort", "recent")
    page = int(params.get("page", "1") or 1)
    result = api_webshare.get_movies(sort=sort, page=page)
    _, has_more = _split_result(result)
    _render_movie_list(handle, base_url, result, "list_movies", page, sort)
    _prefetch_next("list_movies", api_webshare.get_movies, sort, page, has_more)


def view_list_movies_new_dub(handle, base_url, params):
    _ensure_login()
    sort = params.get("sort", "recent")
    page = int(params.get("page", "1") or 1)
    result = api_webshare.get_movies_new_dub(sort=sort, page=page)
    _, has_more = _split_result(result)
    _render_movie_list(handle, base_url, result, "list_movies_new_dub", page, sort)
    _prefetch_next("list_movies_new_dub", api_webshare.get_movies_new_dub,
                   sort, page, has_more)


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


def _render_with_search_top(handle, base_url, result, list_action: str,
                             search_action: str, page: int, sort: str,
                             search_label_id: int = 30092,
                             query: str = ""):
    """
    Vykreslí seznam filmů s vyhledávacím polem JAKO PRVNÍ položkou (jen na page 1).
    Klik na "Hledat..." otevře klávesnici a pak filtruje obsah rubriky.

    v0.0.62:
    - ">>> Hledat..." viditelne i kdyz uz mame aktivni query (user muze
      rovnou refinovat dotaz bez navratu Back).
    - Pri aktivnim query a 0 vysledcich: zobrazi "Nic nenalezeno pro <q>"
      notifikaci PLUS "<<< Vsechny filmy" tlacitko pro reset.
    """
    items, has_more = _split_result(result)

    # První položka na page 1 - vyhledávací pole. v0.0.62: i kdyz uz query.
    if page == 1:
        addon = _addon()
        icon = addon.getAddonInfo("icon")
        fanart = addon.getAddonInfo("fanart")
        url = ui.build_url(base_url, action=search_action)
        if query:
            label = (f"[COLOR FFFFA500][B]>>> Zmenit dotaz "
                     f"({query[:25]}{'...' if len(query) > 25 else ''})...[/B][/COLOR]")
        else:
            label = f"[COLOR FFFFA500][B]>>> {_tr(search_label_id)}[/B][/COLOR]"
        ui.add_dir_item(handle=handle, label=label, url=url,
                        icon=icon, fanart=fanart)

        # v0.0.63: refresh button - jen pri default listingu (bez query),
        # u search nedava smysl obnovovat cache (kazda query je jiny klic
        # a uz se invaliduje pri jine fraze).
        if not query:
            _add_refresh_button(handle, base_url, list_action, sort=sort)

        # v0.0.62: pri aktivnim query taky "<<< Zpet na vsechny" reset
        if query:
            reset_url = ui.build_url(base_url, action=list_action,
                                      sort=sort, page=1)
            ui.add_dir_item(
                handle=handle,
                label="[COLOR FF888888][I]<<< Vsechny filmy (zrusit filtr)[/I][/COLOR]",
                url=reset_url, icon=icon, fanart=fanart,
            )

    for item in items:
        if item.get("series_name"):
            url = ui.build_url(base_url, action="list_series_seasons",
                               name=item["series_name"])
            ui.add_video_item(handle, item, url, is_folder=True)
        elif item.get("base_title"):
            url = ui.build_url(base_url, action="play_pick",
                               base=item["base_title"], mode="movie")
            ui.add_video_item(handle, item, url, is_folder=False)
        elif item.get("id"):
            url = ui.build_url(base_url, action="play",
                               id=item["id"], type="movie")
            ui.add_video_item(handle, item, url, is_folder=False)

    # v0.0.62: notifikace VZDY pri prazdnych vysledcich
    # (drive jen kdyz nebyl query - user co hledal videl jen prazdnou
    # obrazovku bez zpetne vazby, vypadalo to jako "vratilo se to zpet").
    if not items and page == 1:
        if query:
            try:
                import xbmcgui  # type: ignore
                xbmcgui.Dialog().notification(
                    "KlempCinema",
                    f"V teto rubrice nic - zkus 'Hledat ve vsech filmech'",
                    xbmcgui.NOTIFICATION_INFO, 5000,
                )
            except Exception:  # noqa: BLE001
                ui.show_notification(
                    f"Pro '{query[:30]}' v rubrice nic", time_ms=5000)
            # v0.0.62: pri prazdnem rubric search - PROMINENTNI fallback
            # button na obecny search ve vsech filmech (NE jen v rubrice).
            # Drive user musel rucne Back -> Home -> Hledat -> retype.
            addon2 = _addon()
            icon2 = addon2.getAddonInfo("icon")
            fanart2 = addon2.getAddonInfo("fanart")
            fallback_url = ui.build_url(base_url, action="search",
                                         query=query, page=1)
            ui.add_dir_item(
                handle=handle,
                label=(f"[COLOR FF00FF88][B]>>> Hledat '{query[:30]}' "
                       f"ve VSECH filmech (mimo rubriku)...[/B][/COLOR]"),
                url=fallback_url, icon=icon2, fanart=fanart2,
            )
        else:
            ui.show_notification(_tr(30023))

    extra = {"query": query} if query else {}
    _add_next_page(handle, base_url, list_action, sort, page,
                   has_more=has_more, **extra)

    xbmcplugin.setContent(handle, "movies")
    xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)


def view_list_4k(handle, base_url, params):
    """Filmy v 4K kvalitě (s vyhledávacím polem jako první položkou)."""
    _ensure_login()
    sort = params.get("sort", "recent")
    page = int(params.get("page", "1") or 1)
    query = params.get("query", "") or ""
    result = api_webshare.get_4k(sort=sort, page=page,
                                 query_override=query or None)
    _, has_more = _split_result(result)
    _render_with_search_top(handle, base_url, result, "list_4k",
                             "search_4k", page, sort,
                             search_label_id=30093, query=query)
    if not query:
        _prefetch_next("list_4k", api_webshare.get_4k, sort, page, has_more)


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
    result = api_webshare.get_movies_animated(sort=sort, page=page,
                                               query_override=query or None)
    _, has_more = _split_result(result)
    _render_with_search_top(handle, base_url, result, "list_animated",
                             "search_animated", page, sort,
                             search_label_id=30096, query=query)
    if not query:
        _prefetch_next("list_animated", api_webshare.get_movies_animated,
                       sort, page, has_more)


def view_search_animated(handle, base_url, params):
    """v0.0.69: Klavesnice -> filtrovane hledani v Animovanych filmech."""
    _ensure_login()
    q = ui.ask_keyboard(_tr(30096))
    if not q:
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return
    cleaned = _clean_search_query(q)
    if not cleaned:
        ui.show_notification("Prazdny dotaz po vycisteni - zadej nazev filmu.",
                             time_ms=4000)
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return
    xbmc_url = ui.build_url(base_url, action="list_animated",
                            query=cleaned, page=1, sort="recent")
    import xbmc  # type: ignore
    xbmc.executebuiltin(f"Container.Update({xbmc_url},replace)")
    xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)


def _clean_search_query(raw: str) -> str:
    """
    v0.0.62: vycisti user input z klavesnice pres clean_title.

    Drive: user napsal "Avatar 2009 4K CZ" a do Webshare se poslalo to cele,
    rubrikove markery se k tomu pripojily: "Avatar 2009 4K CZ 2160p" / "Avatar
    2009 4K CZ 4K" - duplikatni "4K" a CZ, redundantni dotazy.
    Ted: clean_title osekne rok, jazyk, quality, scene tagy -> "Avatar".
    Tim se z dotazu odstrani vsechno, co by ho zbytecne svazovalo.
    """
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        cleaned = _ct.clean_title(raw) or raw
    except Exception:  # noqa: BLE001
        cleaned = raw
    return cleaned.strip()


def view_search_4k(handle, base_url, params):
    """Klávesnice -> filtrované hledání v 4K rubrice."""
    _ensure_login()
    q = ui.ask_keyboard(_tr(30093))
    if not q:
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return
    cleaned = _clean_search_query(q)
    if not cleaned:
        ui.show_notification("Prazdny dotaz po vycisteni - zadej nazev filmu.",
                             time_ms=4000)
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return
    # Přesměrujeme na list_4k s vycistenym query parametrem.
    # v0.0.62: 'replace' modifier - search_4k se v breadcrumb nahradi
    # list_4k, takze Back nevede na prazdnou search_4k obrazovku ale rovnou
    # na predchozi rubriku. Plus endOfDirectory(False) misto True = Kodi
    # nezobrazi mezitim "prazdnou" search_4k slozku.
    xbmc_url = ui.build_url(base_url, action="list_4k",
                            query=cleaned, page=1, sort="recent")
    import xbmc  # type: ignore
    xbmc.executebuiltin(f"Container.Update({xbmc_url},replace)")
    xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)


def view_list_bluray(handle, base_url, params):
    """Filmy z BluRay (s vyhledávacím polem jako první položkou)."""
    _ensure_login()
    sort = params.get("sort", "recent")
    page = int(params.get("page", "1") or 1)
    query = params.get("query", "") or ""
    result = api_webshare.get_bluray(sort=sort, page=page,
                                     query_override=query or None)
    _, has_more = _split_result(result)
    _render_with_search_top(handle, base_url, result, "list_bluray",
                             "search_bluray", page, sort,
                             search_label_id=30094, query=query)
    if not query:
        _prefetch_next("list_bluray", api_webshare.get_bluray, sort, page, has_more)


def view_search_bluray(handle, base_url, params):
    """Klávesnice -> filtrované hledání v BluRay rubrice."""
    _ensure_login()
    q = ui.ask_keyboard(_tr(30094))
    if not q:
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return
    cleaned = _clean_search_query(q)
    if not cleaned:
        ui.show_notification("Prazdny dotaz po vycisteni - zadej nazev filmu.",
                             time_ms=4000)
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return
    xbmc_url = ui.build_url(base_url, action="list_bluray",
                            query=cleaned, page=1, sort="recent")
    import xbmc  # type: ignore
    xbmc.executebuiltin(f"Container.Update({xbmc_url},replace)")
    xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)


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


def _render_episodes_flat(handle, base_url, name: str, season=None,
                           force_refresh: bool = False):
    """Vykreslí epizody (volitelně 1 sezóny) bez season folderingu.

    v0.0.68: pridana podpora force_refresh + Aktualizovat tlacitko nahore.
    """
    log.info("_render_episodes_flat: name=%r season=%s refresh=%s",
             name, season, force_refresh)
    try:
        items = api_webshare.get_series_episodes(name, season=season,
                                                  force_refresh=force_refresh)
    except Exception as exc:  # noqa: BLE001
        log.exception("get_series_episodes(%r, season=%s) selhalo: %s",
                      name, season, exc)
        ui.show_notification(f"Chyba pri nacitani epizod: {exc}", time_ms=6000)
        items = []

    log.info("_render_episodes_flat: %d epizod pro %r (season=%s)",
             len(items), name, season)

    addon = _addon()
    icon = addon.getAddonInfo("icon")
    fanart = addon.getAddonInfo("fanart")

    # v0.0.68: Aktualizovat tlacitko nahore (nad epizodami) - smaze cache
    # a forcne fresh fetch z Webshare. Najde premierove / cerstve nahrane dily.
    refresh_params = {"action": "list_series_episodes", "name": name,
                       "refresh": "1"}
    if season is not None:
        refresh_params["season"] = str(season)
    refresh_url = ui.build_url(base_url, **refresh_params)
    ui.add_dir_item(
        handle=handle,
        label=("[COLOR FF00BFFF][B]>>> Aktualizovat - hledat nejnovejsi "
               "dily na Webshare[/B][/COLOR]"),
        url=refresh_url, icon=icon, fanart=fanart,
    )

    for item in items:
        base_t = item.get("base_title") or ""
        if not base_t:
            log.warning("_render_episodes_flat: epizoda bez base_title: %s", item)
            continue
        url = ui.build_url(base_url, action="play_pick",
                           base=base_t, mode="episode")
        ui.add_video_item(handle, item, url, is_folder=False)

    if not items:
        # v0.0.67: Pridan fallback button pro pripad, ze Webshare nema SxxEyy
        # uploady (typicke pro niche reality TV s Voyo: nekdo uploadne "Ruza
        # pre nevestu epizoda 1" bez SxxEyy markeru). Click na fallback ->
        # tmdb_play_movie spusti Webshare full-text search nazvu a vyrobi
        # quality picker s vsemi variantami. User aspon dostane neco.
        fallback_url = ui.build_url(base_url, action="tmdb_play_movie",
                                     title=name, year="")
        ui.add_dir_item(
            handle=handle,
            label=(f"[COLOR FFFFA500][B]>>> Hledat '{name[:50]}' na Webshare "
                   "(bez epizodniho rozdeleni)[/B][/COLOR]"),
            url=fallback_url, icon=icon, fanart=fanart,
        )
        ui.show_notification(
            f"Zadne epizody (SxxEyy) pro '{name[:40]}' - "
            "zkus fallback search nahore.",
            time_ms=6000,
        )

    xbmcplugin.setContent(handle, "episodes")
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
    result = api_webshare.get_latest(sort=sort, page=page)
    _, has_more = _split_result(result)
    _render_flat_list(handle, base_url, result, "list_latest", page, sort)
    _prefetch_next("list_latest", api_webshare.get_latest, sort, page, has_more)


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
            from . import search_history
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
        # v0.0.62: vycistit user input pres clean_title - sjednoceno
        # napric vsemi rubrikami (4K/BluRay/search). Odstrani rok, quality,
        # scene tagy, jazyk - sance najit titul vyrazne vyssi.
        query = _clean_search_query(raw_q) or raw_q.strip()
    elif force_keyboard:
        # User klikl na "Nove hledani" - vzdy klavesnice
        raw_q = ui.ask_keyboard(_tr(30006)) or ""
        if not raw_q:
            xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
            return
        query = _clean_search_query(raw_q) or raw_q.strip()

    # v0.0.52: ulozit dotaz do historie (jen pri page 1, aby nedoslo k
    # ulozeni pro kazdou stranku)
    if page == 1 and query:
        try:
            from . import search_history
            search_history.add(query)
        except Exception:  # noqa: BLE001
            pass

    result = api_webshare.search(query=query, page=page)
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
            from . import search_history
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
        from . import search_history
        n = search_history.clear()
        ui.show_notification(f"Smazano {n} dotazu", time_ms=3000)
    except Exception:  # noqa: BLE001
        log.exception("search_history clear failed")
    try:
        import xbmc  # type: ignore
        xbmc.executebuiltin("Container.Refresh")
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Views – přehrávání
# ---------------------------------------------------------------------------

def _apply_resume(li: xbmcgui.ListItem, file_id: str,
                   base_title: str = "", mode: str = "movie") -> float:
    """
    Pokud máme uloženou pozici, nastav ji jako ResumeTime. Vrátí pos.

    v0.0.52: cross-variant resume - pokud per-ident progress neexistuje,
    zkusi se najit per-base_title (= user prepnul kvalitu).
    """
    pos = watched.get_resume_position(file_id)
    src = "ident"
    if pos <= 0 and base_title:
        pos = watched.get_resume_for_base(base_title, mode=mode)
        src = "base"
    if pos > 0:
        try:
            li.setProperty("ResumeTime", str(pos))
            li.setProperty("StartOffset", str(pos))
            log.info("apply_resume: %s (%s) -> %.1fs", file_id, src, pos)
        except Exception:  # noqa: BLE001
            pass
    return pos


def view_play(handle, params):
    """Přímé přehrání podle Webshare identu (+ auto CZ titulky pokud nedabováno)."""
    file_id = params.get("id", "")
    item_type = params.get("type", "movie")
    dubbed = (params.get("dubbed", "1") == "1")
    title = (params.get("title") or "").strip()
    year_raw = params.get("year") or ""

    if not file_id:
        ui.show_notification(_tr(30022))
        xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem())
        return

    token = _ensure_login()
    stream_url = api_webshare.get_stream_url(token or None, file_id, item_type)

    if not stream_url:
        ui.show_notification(_tr(30022))
        xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem())
        return

    li = xbmcgui.ListItem(path=stream_url)
    li.setProperty("IsPlayable", "true")

    _apply_resume(li, file_id)

    # v0.0.79: subtitle fetch presunut do background (viz view_play_pick)
    try:
        year = int(year_raw) if year_raw.isdigit() else None
    except ValueError:
        year = None

    # Pred setResolvedUrl zavri vsechny dialogy v UI stacku.
    try:
        import xbmc  # type: ignore
        xbmc.executebuiltin('Dialog.Close(all,true)')
    except Exception:  # noqa: BLE001
        pass

    xbmcplugin.setResolvedUrl(handle, True, li)

    player_tracker.start_tracking(file_id, {
        "title":  title,
        "year":   year,
        "type":   item_type,
        "dubbed": dubbed,
    })

    # Async subtitle attach - bezi PO startu video playera
    if title and (not dubbed or not subtitles.auto_for_undubbed_only()):
        def _resolve_imdb(t, y, m):
            try:
                meta = tmdb.search_tv(t) if m == "tv" else tmdb.search_movie(t, y)
                if meta and meta.get("tmdb_id"):
                    kind = "tv" if m in ("series", "episode", "tv") else "movie"
                    return tmdb.get_imdb_id(meta["tmdb_id"], kind=kind) or ""
            except Exception:  # noqa: BLE001
                pass
            return ""
        subtitles.attach_async(
            title=title,
            year=year,
            mode="movie" if item_type == "movie" else "tv",
            imdb_resolver=_resolve_imdb,
        )


def view_tmdb_play_movie(handle, params):
    """
    Vyhledá konkrétní film na Webshare podle TMDB title + year, vytvoří
    varianty kvality, uloží do cache a deleguje na view_play_pick.

    URL: action=tmdb_play_movie&title=...&year=...&tmdb_id=...
    """
    title = (params.get("title") or "").strip()
    year_raw = (params.get("year") or "").strip()
    try:
        year = int(year_raw) if year_raw.isdigit() else None
    except ValueError:
        year = None

    if not title:
        ui.show_notification("Chybí název filmu")
        xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem())
        return

    _ensure_login()

    # 1) Search Webshare - zkusíme více variant query, abychom našli
    #    co nejvíc match-ů (česká i originální verze názvu).
    queries = [title]
    # Některé filmy mají v TMDB cs-CZ titulu jen česky, originál v en.
    # Pojďme zkusit i přidat rok do query - občas to pomůže.
    if year:
        queries.append(f"{title} {year}")

    all_files = []
    seen_idents = set()
    for q in queries:
        files = api_webshare.search_videos(query=q, sort="rating", page=1)
        if not files:
            continue
        for f in files:
            ident = f.get("ident") or f.get("id") or ""
            if ident and ident not in seen_idents:
                seen_idents.add(ident)
                all_files.append(f)

    if not all_files:
        ui.show_notification(f"Film nenalezen na Webshare: {title}",
                             time_ms=6000)
        xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem())
        return

    # 2) Vyloučit epizody seriálů (pro film je nechceme)
    all_files = api_webshare._exclude_series(all_files)
    if not all_files:
        ui.show_notification(f"Webshare má jen epizody seriálu pro: {title}",
                             time_ms=6000)
        xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem())
        return

    # 3) Match podle title + (volitelně) year
    target_norm = api_webshare._norm_compare(title)
    matching_year = []
    matching_only_title = []
    for f in all_files:
        nm = api_webshare._norm_compare(f.get("name") or "")
        if target_norm not in nm:
            continue
        if year and str(year) in (f.get("name") or ""):
            matching_year.append(f)
        else:
            matching_only_title.append(f)

    # Preferuj match s rokem (pokud máme), jinak alespoň podle názvu
    if matching_year:
        matching = matching_year
    elif matching_only_title:
        matching = matching_only_title
    else:
        ui.show_notification(f"Film '{title}' nemá přesnou shodu na Webshare",
                             time_ms=6000)
        xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem())
        return

    # 4) Klasifikuj kvalitu/dabing
    api_webshare.classify_files(matching)

    # 5) Vytvoř varianty kvality (pro picker)
    variants = api_webshare._files_to_variant_refs(matching)
    if not variants:
        ui.show_notification("Žádné varianty kvality")
        xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem())
        return

    log.info("tmdb_play_movie: title=%r year=%s -> %d variants",
             title, year, len(variants))

    # 6) Uloží varianty pod base_title=title, aby view_play_pick je našel.
    api_webshare._save_variants_cache(title, "movie", variants)

    # 7) Delegate na quality picker (vybere kvalitu nebo přehraje jednu).
    #    Předáme i year, ať _attach_subtitles ví, kde hledat CZ titulky.
    view_play_pick(handle, {
        "base": title,
        "mode": "movie",
        "year": str(year) if year else "",
    })


def view_play_pick(handle, params):
    """
    Najde varianty kvality pro base_title a nabídne uživateli výběr.
    Pokud existuje jen 1 varianta, hraje rovnou.
    Po výběru detekuje, zda je zvolená varianta dabovaná. Pokud NE
    (a auto-subs jsou zapnuté), stáhne CZ titulky a přilepí je k playbacku.
    """
    base = (params.get("base") or "").strip()
    mode = params.get("mode", "movie")

    log.info("view_play_pick: base=%r mode=%s", base, mode)

    if not base:
        ui.show_notification("Chyba: prazdny base_title v URL", time_ms=6000)
        log.error("view_play_pick: base je prazdny, params=%s", params)
        xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem())
        return

    token = _ensure_login()
    if not token:
        ui.show_notification("Webshare login selhal - zkontroluj heslo v nastaveni",
                             time_ms=8000)
        log.error("view_play_pick: token je prazdny")
        xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem())
        return

    variants = api_webshare.get_quality_variants(base, mode=mode)

    if not variants:
        # Diagnostická notifikace s tipem
        if mode == "episode":
            msg = f"Epizoda nenalezena: {base[:50]}\n(zkus Smazat cache v hlavnim menu)"
        else:
            msg = f"Film nenalezen: {base[:50]}\n(zkus Smazat cache)"
        ui.show_notification(msg, time_ms=8000)
        log.error("view_play_pick: zadne varianty pro base=%r mode=%s", base, mode)
        xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem())
        return

    if len(variants) == 1:
        chosen = variants[0]
    elif _auto_pick_quality_enabled():
        # Auto-pick: variants jsou uz seradene best-first dle quality_score,
        # vezmeme [0]. Loguje co se vybralo pro pripadnou diagnostiku.
        chosen = variants[0]
        log.info("auto-pick quality: %s (z %d variant)",
                 chosen.get("name", "")[:80], len(variants))
    else:
        labels = [api_webshare.format_variant_label(v) for v in variants]
        idx = xbmcgui.Dialog().select(_tr(30030), labels)
        if idx < 0:
            xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem())
            return
        chosen = variants[idx]

    file_id = chosen.get("ident", "")
    chosen_name = chosen.get("name", "")

    if not file_id:
        ui.show_notification("Chybi Webshare ident u varianty", time_ms=6000)
        log.error("view_play_pick: chosen variant nema ident: %r", chosen)
        xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem())
        return

    log.info("view_play_pick: prehravam ident=%s name=%r", file_id, chosen_name)
    stream_url = api_webshare.get_stream_url(token or None, file_id, mode)
    if not stream_url:
        ui.show_notification(f"Webshare nedal stream URL pro {chosen_name[:40]}",
                             time_ms=8000)
        log.error("view_play_pick: get_stream_url vratil prazdne (ident=%s, mode=%s)",
                  file_id, mode)
        xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem())
        return

    li = xbmcgui.ListItem(path=stream_url)
    li.setProperty("IsPlayable", "true")

    # v0.0.52: predame base_title + mode pro cross-variant resume
    _apply_resume(li, file_id, base_title=base, mode=mode)

    # ---- Auto CZ titulky pro nedabované varianty ----
    # v0.0.79: ZMENA - subtitle fetch je nyni ASYNCHRONNI (background thread).
    # Drive (v0.0.78 a starsi) blokovalo 5-15s pred setResolvedUrl
    # → user videl "loading" pauzu → mouse eventy se hromadily → pri startu
    # videa Kodi spustil OSD a kurzor zustal omezeny na spodni polovinu.
    # Ted setResolvedUrl probehne IHNED a thread v subtitles.attach_async
    # ceka az player zacne hrat a pak teprve dohleda titulky.
    is_dubbed = api_webshare._detect_dubbed(chosen_name)
    should_fetch = subtitles.is_enabled() and (
        not is_dubbed or not subtitles.auto_for_undubbed_only()
    )

    extra_meta: Dict[str, Any] = {"type": mode, "dubbed": is_dubbed,
                                   "base_title": base, "mode": mode}
    if mode == "episode":
        series_name, season, episode = _extract_episode_from_base(base)
        extra_meta["title"] = series_name or base
        sub_title = series_name or base
        sub_mode = "tv"
        sub_year = None
        sub_season = season
        sub_episode = episode
    else:
        clean_title, year = _extract_year_from_base(base)
        extra_meta["title"] = clean_title or base
        extra_meta["year"] = year
        sub_title = clean_title or base
        sub_mode = "movie"
        sub_year = year
        sub_season = None
        sub_episode = None

    # v0.0.79: Bezpecnostni - pred setResolvedUrl zavri vsechny dialogy
    # ktere mohly zustat v UI stacku (quality picker, notifikace, ...).
    # 'Dialog.Close(all,true)' force-zavre a neresetuje focus na video.
    try:
        import xbmc  # type: ignore
        xbmc.executebuiltin('Dialog.Close(all,true)')
    except Exception:  # noqa: BLE001
        pass

    xbmcplugin.setResolvedUrl(handle, True, li)

    # Tracker pro Pokračovat ve sledování (background daemon)
    player_tracker.start_tracking(file_id, extra_meta)

    # v0.0.79: Async subtitle attach - bezi PO startu video playera
    if should_fetch:
        def _resolve_imdb(t, y, m):
            """IMDB resolver pres TMDB - dela router-side aby subtitles modul
            nemusel importovat tmdb (a porusit dependency layout)."""
            try:
                meta = tmdb.search_tv(t) if m == "tv" else tmdb.search_movie(t, y)
                if meta and meta.get("tmdb_id"):
                    kind = "tv" if m in ("series", "episode", "tv") else "movie"
                    return tmdb.get_imdb_id(meta["tmdb_id"], kind=kind) or ""
            except Exception:  # noqa: BLE001
                pass
            return ""
        subtitles.attach_async(
            title=sub_title,
            year=sub_year,
            mode=sub_mode,
            season=sub_season,
            episode=sub_episode,
            imdb_resolver=_resolve_imdb,
        )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def _render_tmdb_discover_list(handle, base_url, items, content="movies",
                                close: bool = True):
    """
    Zobrazí TMDB položky (z trending/discover).

    - Film    -> action=tmdb_play_movie (vyhledá na WS, ukáže quality picker)
    - Seriál  -> action=list_series_seasons (sezóny + epizody)

    :param close: pokud True, uzavře directory hned (xbmcplugin.endOfDirectory).
                  False = volající přidá ještě "Další strana..." a uzavře sám.
    """
    for meta in items:
        title = meta.get("title") or ""
        year = meta.get("year")
        item_type = meta.get("type") or "movie"
        if not title:
            continue

        if item_type == "series":
            # Klik na seriál -> seznam sezón (přes existing flow)
            url = ui.build_url(base_url, action="list_series_seasons",
                               name=title)
            is_folder = True
        else:
            # Klik na film -> přímý play přes WS lookup + quality picker
            url = ui.build_url(base_url, action="tmdb_play_movie",
                               title=title,
                               year=str(year) if year else "",
                               tmdb_id=str(meta.get("tmdb_id") or ""))
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
            "dubbed":          False,
            "tmdb_id":         meta.get("tmdb_id"),
        }
        ui.add_video_item(handle, item_for_ui, url, is_folder=is_folder)

    xbmcplugin.setContent(handle, content)
    if close:
        xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)


def view_trending(handle, base_url, params):
    """Trending menu - filmy/seriály týden/den."""
    from . import tmdb_discover
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
    xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)


def view_trending_movies(handle, base_url, params):
    from . import tmdb_discover
    _ensure_login()
    window = params.get("window", "week")
    page = int(params.get("page", "1") or 1)
    items = tmdb_discover.trending_movies(window=window, page=page)
    _render_tmdb_discover_list(handle, base_url, items, content="movies",
                                close=False)
    if items:
        _add_next_page(handle, base_url, "trending_movies", sort=window,
                       page=page, has_more=True, window=window)
    xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)


def view_trending_tv(handle, base_url, params):
    from . import tmdb_discover
    _ensure_login()
    window = params.get("window", "week")
    page = int(params.get("page", "1") or 1)
    items = tmdb_discover.trending_tv(window=window, page=page)
    _render_tmdb_discover_list(handle, base_url, items, content="tvshows",
                                close=False)
    if items:
        _add_next_page(handle, base_url, "trending_tv", sort=window,
                       page=page, has_more=True, window=window)
    xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)


def view_genres_movies(handle, base_url, params):
    """Menu žánrů filmů."""
    from . import tmdb_discover
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
    xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)


def view_genres_tv(handle, base_url, params):
    """Menu žánrů seriálů."""
    from . import tmdb_discover
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
    xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)


def view_menu_platforms(handle, base_url, params=None):
    """v0.0.65: hlavni menu streamovacich platforem.

    Klik na platformu -> view_platform (submenu Filmy / Serialy).
    Datovy zdroj: TMDB /discover s with_watch_providers + watch_region=CZ.
    """
    from . import tmdb_discover
    if not tmdb.is_enabled():
        ui.show_notification(_tr(30084))
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return

    addon = _addon()
    default_icon = addon.getAddonInfo("icon")
    fanart = addon.getAddonInfo("fanart")

    for p in tmdb_discover.PLATFORMS:
        # Icon mapping: media/<name>.png pokud existuje, jinak addon icon.
        icon_name = p.get("icon") or ""
        icon = _addon_icon_for(icon_name) if icon_name else default_icon
        if not icon or icon == default_icon:
            icon = default_icon
        url = ui.build_url(base_url, action="platform",
                            platform_id=str(p["id"]))
        li_label = f"[B]{p['name']}[/B]"
        ui.add_dir_item(handle=handle, label=li_label, url=url,
                         icon=icon, fanart=fanart)

    xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)


def view_platform(handle, base_url, params):
    """v0.0.65: Submenu jedne platformy - Filmy / Serialy / Top hodnocene."""
    from . import tmdb_discover
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
    ]
    for label, action, p in submenu:
        url = ui.build_url(base_url, action=action, **p)
        ui.add_dir_item(handle=handle, label=label, url=url,
                         icon=icon, fanart=fanart)

    xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)


def view_platform_movies(handle, base_url, params):
    """v0.0.65: Filmy z dane platformy s pagingem."""
    from . import tmdb_discover
    _ensure_login()
    try:
        pid = int(params.get("platform_id") or 0)
    except ValueError:
        pid = 0
    page = int(params.get("page", "1") or 1)
    sort = params.get("sort") or "popularity.desc"
    if not pid:
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return
    items = tmdb_discover.platform_movies(pid, page=page, sort_by=sort)
    _render_tmdb_discover_list(handle, base_url, items, content="movies",
                                close=False)
    if items:
        _add_next_page(handle, base_url, "platform_movies", sort=sort,
                       page=page, has_more=True,
                       platform_id=str(pid))
    if not items:
        ui.show_notification(
            "Zadny obsah nenalezen pro danou platformu/region", time_ms=5000)
    xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)


def view_platform_tv(handle, base_url, params):
    """v0.0.65: Serialy z dane platformy s pagingem."""
    from . import tmdb_discover
    _ensure_login()
    try:
        pid = int(params.get("platform_id") or 0)
    except ValueError:
        pid = 0
    page = int(params.get("page", "1") or 1)
    sort = params.get("sort") or "popularity.desc"
    if not pid:
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return
    items = tmdb_discover.platform_tv(pid, page=page, sort_by=sort)
    _render_tmdb_discover_list(handle, base_url, items, content="tvshows",
                                close=False)
    if items:
        _add_next_page(handle, base_url, "platform_tv", sort=sort,
                       page=page, has_more=True,
                       platform_id=str(pid))
    if not items:
        ui.show_notification(
            "Zadny obsah nenalezen pro danou platformu/region", time_ms=5000)
    xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)


def view_discover_movies(handle, base_url, params):
    from . import tmdb_discover
    _ensure_login()
    genre_id = int(params.get("genre_id") or 0)
    page = int(params.get("page", "1") or 1)
    if not genre_id:
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return
    items = tmdb_discover.discover_movies(genre_id=genre_id, page=page)
    _render_tmdb_discover_list(handle, base_url, items, content="movies",
                                close=False)
    if items:
        _add_next_page(handle, base_url, "discover_movies", sort="popularity",
                       page=page, has_more=True,
                       genre_id=str(genre_id),
                       genre_name=params.get("genre_name", ""))
    xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)


def view_discover_tv(handle, base_url, params):
    from . import tmdb_discover
    _ensure_login()
    genre_id = int(params.get("genre_id") or 0)
    page = int(params.get("page", "1") or 1)
    if not genre_id:
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return
    items = tmdb_discover.discover_tv(genre_id=genre_id, page=page)
    _render_tmdb_discover_list(handle, base_url, items, content="tvshows",
                                close=False)
    if items:
        _add_next_page(handle, base_url, "discover_tv", sort="popularity",
                       page=page, has_more=True,
                       genre_id=str(genre_id),
                       genre_name=params.get("genre_name", ""))
    xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)


def view_continue_watching(handle, base_url, params):
    """Seznam rozkoukaných filmů/epizod (z watched.json)."""
    _ensure_login()
    items = watched.get_continue_watching(limit=50)
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
                               mode=it.get("mode") or "movie")
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
        watched.forget(file_id)
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
        watched.mark_watched(file_id)
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
        count = watched.clear_all()
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


def view_clear_cache(handle, base_url, params):
    """
    Smaže cache TMDB / OpenSubtitles / metadata (lokální .json soubory).
    Volá se z hlavního menu položkou 30070.

    Mažu:
        cache/    - obecná MD5-keyed cache (TMDB, ČSFD, varianty, agregované buffery)
        metadata/ - per-title JSON metadata cache (z metadata_resolver)
    """
    from . import cache as _cache_mod
    from . import metadata_cache as _mc

    n_cache = _cache_mod.cache_clear()
    n_meta = _mc.clear()
    total = n_cache + n_meta

    log.info("clear_cache: cache=%d, metadata=%d (celkem %d)",
             n_cache, n_meta, total)
    try:
        msg = _tr(30071)
        ui.show_notification(msg.format(n=total) if "{n}" in msg
                             else f"{msg} ({total})", time_ms=4000)
    except Exception:  # noqa: BLE001
        ui.show_notification(f"Cache cleared ({total} files)")
    xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)


def view_test_login(handle, base_url, params):
    """
    Otevře dialog s podrobným výpisem Webshare diagnostiky.
    User vidí přesný status / code / message / raw XML.
    """
    # Smaž cached token + diagnose cache, ať se vždy logujeme čerstvě.
    try:
        api_webshare._invalidate_token()
    except Exception:  # noqa: BLE001
        pass

    addon = _addon()
    user_setting = (addon.getSetting("ws_user") or "").strip()
    pwd_setting = addon.getSetting("ws_pass") or ""

    if user_setting and pwd_setting:
        source = "USER SETTINGS"
        active_user = user_setting
        pwd_info = (f"délka {len(pwd_setting)}, prvních 2 = "
                    f"{pwd_setting[:2]!r}")
    else:
        # v0.0.72: builtin credentials byly odstraněny z public buildu.
        source = "CHYBÍ (žádné credentials v settings)"
        active_user = ""
        pwd_info = "(neuvedeno)"

    # force=True - obejde 5-minutový cache (user záměrně testuje teď).
    result = api_webshare.login_diagnose(force=True)

    lines = [
        "=== KlempCinema Webshare Login Test ===",
        "",
        f"zdroj credentials:   {source}",
        f"username:            {active_user!r}",
        f"password:            {pwd_info}",
        "",
        f"výsledek OK:         {result.get('ok')}",
        f"fáze:                {result.get('stage')}",
        f"kód:                 {result.get('code')}",
        f"zpráva:              {result.get('message')}",
        "",
        "raw Webshare XML response (prvních 500 znaků):",
        "---",
        (result.get("raw") or "(žádné raw data - úspěch nebo síťová chyba)"),
        "---",
        "",
    ]
    if result.get("ok"):
        lines.append("✅ Login se podařil, token uložen.")
        lines.append("Pokud filmy stále nejedou, problém je jinde (rate limit, cache).")
    else:
        lines.append("❌ Login se nezdařil. Podle výše uvedeného kódu/zprávy:")
        lines.append("")
        code = result.get("code") or ""
        if code == "MISSING_CREDENTIALS":
            lines.append("→ Vestavěné credentials jsou nedostupné. Vyplň")
            lines.append("  ws_user a ws_pass v Settings → Webshare account.")
        elif code == "AUTH_LOGIN_INVALID_USER":
            lines.append("→ Webshare to jméno/email nezná.")
            lines.append("→ Zkus přesný tvar z webshare.cz/profil")
        elif code == "AUTH_LOGIN_INVALID_PASSWORD":
            lines.append("→ Webshare říká, že heslo je špatné.")
            lines.append("→ Přihlas se nejdřív na webshare.cz manuálně.")
            lines.append("→ Pokud heslo má speciální znaky (+ % & # =),")
            lines.append("  smaž a vyplň znova RUČNĚ (ne paste).")
        elif code == "AUTH_LOGIN_TOO_MANY_ATTEMPTS":
            lines.append("→ Webshare dočasně blokuje. Počkej 10-30 min.")
        elif code == "NETWORK":
            lines.append("→ Plugin nedosáhne na webshare.cz.")
            lines.append("→ Zkontroluj síť / VPN / firewall.")
        else:
            lines.append(f"→ Neznámý kód {code!r}.")
            lines.append("→ Pošli mi RAW XML výše, vyřešíme.")

    text = "\n".join(lines)
    log.info("test_login result: %s", result)

    try:
        xbmcgui.Dialog().textviewer("KlempCinema – Webshare test", text)
    except Exception:  # noqa: BLE001
        # Fallback – kdyby textviewer chyběl, ukaž alespoň notification.
        ui.show_notification(f"Login: {result.get('code')} {result.get('message')}",
                             time_ms=10000)

    xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)


def view_subs_test(handle, base_url, params):
    """
    v0.0.62: Otestuje OpenSubtitles login + search a ukaze podrobny
    diagnosticky vypis. Volane z Nastroje -> "Test OpenSubtitles".
    """
    result = subtitles.self_test()
    lines = [
        "=== KlempCinema - Test OpenSubtitles ===",
        "",
        f"User-Agent:    {result.get('user_agent')!r}",
        f"Uzivatel:      {result.get('user')}",
        f"Jazyk:         {result.get('lang')}",
        "",
        f"Vysledek OK:   {result.get('ok')}",
        f"Faze:          {result.get('stage')}",
        f"Token OK:      {result.get('token_ok')}",
        f"Zprava:        {result.get('message')}",
        "",
    ]
    if result.get("ok"):
        lines.append("OK - OpenSubtitles funguje.")
        lines.append("Pokud se titulky stale neprilepuji k filmu:")
        lines.append(" - filmy s [CZ] dabingem maji titulky vypnute (setting")
        lines.append("   'subs_only_undubbed' = true).")
        lines.append(" - zkontroluj v Settings, ze 'subs_enabled' je zapnute.")
    else:
        stage = result.get("stage") or ""
        lines.append("Chyba.")
        if stage == "config":
            lines.append("-> Zapni OpenSubtitles v Settings (subs_enabled).")
        elif stage == "login":
            lines.append("-> OpenSubtitles odmitlo LogIn:")
            lines.append("   * zkus jiny User-Agent v Settings (napr. 'trailers.to-UA')")
            lines.append("   * pokud pouzivas user/pass, zkontroluj heslo na opensubtitles.org")
            lines.append("   * 401 obvykle = neregistrovany UA")
        elif stage == "search":
            lines.append("-> Login proslo, ale Search selhal:")
            lines.append("   * docasny problem na strane OpenSubtitles")
            lines.append("   * zkus za par minut znovu")
    text = "\n".join(lines)
    log.info("subs.self_test result: %s", result)
    try:
        xbmcgui.Dialog().textviewer("KlempCinema - OpenSubtitles test", text)
    except Exception:  # noqa: BLE001
        ui.show_notification(f"Subs: {result.get('message')}", time_ms=10000)
    xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)


def view_subs_download(handle, base_url, params):
    """
    v0.0.62: Manualni stazeni titulku z context menu polozky.
    Zobrazi notifikaci kde se titulky ulozily, nebo ze nic nenalezeno.

    Params:
        title: nazev filmu/serialu
        year:  rok (volitelne)
        mode:  "movie" | "tv" (default movie)
        season, episode: pro serialy
    """
    title = (params.get("title") or "").strip()
    if not title:
        ui.show_notification("Chybi nazev pro hledani titulku.", time_ms=4000)
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return

    if not subtitles.is_enabled():
        ui.show_notification(
            "OpenSubtitles je vypnuto. Zapni v Nastaveni.", time_ms=5000)
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return

    year = None
    try:
        y = (params.get("year") or "").strip()
        if y:
            year = int(y)
    except (TypeError, ValueError):
        year = None

    mode = (params.get("mode") or "movie").strip()
    season = None
    episode = None
    try:
        s = (params.get("season") or "").strip()
        e = (params.get("episode") or "").strip()
        if s:
            season = int(s)
        if e:
            episode = int(e)
    except (TypeError, ValueError):
        pass

    ui.show_notification("Hledam titulky na OpenSubtitles...", time_ms=2500)

    # Pokus o IMDB id pres TMDB pro presnejsi search
    imdb_id = ""
    try:
        kind = "tv" if mode in ("tv", "episode", "series") else "movie"
        meta = (tmdb.search_tv(title) if kind == "tv"
                else tmdb.search_movie(title, year))
        if meta and meta.get("tmdb_id"):
            imdb_id = tmdb.get_imdb_id(meta["tmdb_id"], kind=kind) or ""
    except Exception as exc:  # noqa: BLE001
        log.debug("subs_download: TMDB imdb lookup selhal: %s", exc)

    # v0.0.71: pouzij rozsireny fetch s diagnostikou
    srt = None
    diag: Dict[str, Any] = {}
    try:
        srt, diag = subtitles.fetch_for_title(
            imdb_id=imdb_id or None,
            title=title,
            year=year,
            season=season,
            episode=episode,
            mode="episode" if mode in ("tv", "episode", "series") else "movie",
            return_diagnostics=True,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("subs_download: fetch crash: %s", exc)
        srt = None
        diag = {"error": f"Crash: {exc}"}

    # v0.0.71: detailni dialog box s vysledkem misto strucne notifikace.
    # User vidi PRESNE proc to selhalo (login fail / no candidates / ...).
    if srt:
        msg = f"Titulky stazeny do: {os.path.basename(srt)}"
        log.info("subs_download OK: %s", srt)
        try:
            xbmcgui.Dialog().notification(
                "KlempCinema", msg,
                xbmcgui.NOTIFICATION_INFO, 5000,
            )
        except Exception:  # noqa: BLE001
            ui.show_notification(msg, time_ms=5000)
    else:
        # Detailni diag - typicky vec ze user chce videt 'proc'
        lines = [f"Titulky pro \"{title[:60]}\" NENALEZENY.\n"]
        lines.append(f"Login na OpenSubtitles.org: "
                     f"{'OK' if diag.get('login_ok') else 'SELHAL'}")
        lines.append(f"IMDB ID z TMDB: {diag.get('imdb_id') or '(zadne)'}")
        lines.append(f"Jazyk titulku: {diag.get('lang') or '?'}")
        attempts = diag.get("attempts") or []
        if attempts:
            lines.append("\nPokusy o vyhledani:")
            for a in attempts:
                lines.append(f"  - {a.get('kind')}: \"{a.get('input')}\" "
                             f"-> {a.get('count')} vysledku")
        if diag.get("error"):
            lines.append(f"\nChyba: {diag['error']}")
        if not diag.get("login_ok"):
            lines.append("\nTip: Nastroje -> Otestovat OpenSubtitles "
                         "= detailni diagnostika prihlaseni.")
        full_msg = "\n".join(lines)
        log.warning("subs_download FAIL: title=%r year=%s mode=%s diag=%s",
                    title, year, mode, diag)
        try:
            xbmcgui.Dialog().textviewer("KlempCinema - titulky", full_msg)
        except Exception:  # noqa: BLE001
            ui.show_notification(f"Titulky pro \"{title[:40]}\" nenalezeny",
                                  time_ms=5000)

    xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)


# v0.0.63: mapping list_action -> cache_key prefix pro refresh_rubrika.
# Kdyz user klikne ">>> Aktualizovat" v rubrice, vime ktere klice smazat.
_RUBRIC_CACHE_PREFIXES = {
    "list_movies":           "rubrika:movies:",
    "list_movies_new_dub":   "rubrika:newdub:",
    "list_kids":             "rubrika:kids",
    "list_series":           "rubrika:series:",
    "list_series_new_dub":   "rubrika:seriesnewdub:",
    "list_latest":           "rubrika:latest:",
    "list_4k":               "rubrika:4k:",
    "list_bluray":           "rubrika:bluray:",
    "list_animated":         "rubrika:animated:",  # v0.0.69
}


def _render_tv_item(handle: int, base_url: str, item: Dict[str, Any],
                    show_channel_in_label: bool = True) -> None:
    """v0.0.63: Spolecny render TV show karty (iDNES TV program).

    Format labelu: "[B]ČT1[/B] • [oranzove]20:00[/oranzove] • Avatar (2009)"
    Klik na film -> Webshare search (tmdb_play_movie).
    Klik na serial/dokument -> Webshare search taky (vetsina jde najit).
    """
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

    # v0.0.71: pouzij TMDB plakat/fanart kdyz je k dispozici (hezci nez
    # iDNES low-res thumbnail). Fallback chain:
    #   TMDB poster -> iDNES thumb -> bez plakatu
    tmdb_poster = (item.get("tmdb_poster") or "").strip()
    tmdb_fanart = (item.get("tmdb_fanart") or "").strip()
    tmdb_plot = (item.get("tmdb_plot") or "").strip()
    tmdb_year = item.get("tmdb_year")
    tmdb_rating = item.get("tmdb_rating")
    if tmdb_year and not year:
        year = int(tmdb_year)
    poster_final = tmdb_poster or thumb
    fanart_final = tmdb_fanart or ""

    # Label format: kanal • čas • titul (year)
    parts = []
    if show_channel_in_label and channel:
        parts.append(f"[B]{channel}[/B]")
    if airtime:
        parts.append(f"[COLOR FFFFA500]{airtime}[/COLOR]")
    title_with_year = title + (f" ({year})" if year else "")
    # v0.0.71: TMDB hodnoceni vedle nazvu kdyz je dostupne (Sosac-style)
    if tmdb_rating and tmdb_rating > 0:
        title_with_year += f" [COLOR FFFFD700]\u2605 {tmdb_rating:.1f}[/COLOR]"
    if parts:
        label = " • ".join(parts) + f"  •  {title_with_year}"
    else:
        label = title_with_year

    # Plot: prefix "V TV: kanal v cas" + popisek
    plot_lines = []
    if channel and airtime:
        plot_lines.append(f"[B]V TV:[/B] {channel} v {airtime}")
    elif channel:
        plot_lines.append(f"[B]V TV:[/B] {channel}")
    # v0.0.71: preferuj delsi TMDB popis (typicky 200+ znaku) pred
    # iDNES strucnym popisem (typicky 50-80 znaku). Pokud TMDB chybi,
    # pouzije se iDNES.
    final_plot = tmdb_plot or plot
    if final_plot:
        plot_lines.append(final_plot)
    if idnes_url:
        plot_lines.append(f"iDNES: {idnes_url}")
    plot_full = "\n".join(plot_lines)

    # Akce: film -> tmdb_play_movie (Webshare quality picker).
    # Pro seriály / dokumenty bohuzel nelze idealne mapovat na sezona/epizoda,
    # tak posílám taky tmdb_play_movie - Webshare často najde i seriály
    # podle názvu (vrátí seznam variant + user vybere).
    play_url = ui.build_url(
        base_url, action="tmdb_play_movie",
        title=title, year=str(year) if year else "",
    )

    item_for_ui = {
        "title":  title,
        "year":   int(year) if year else None,
        "plot":   plot_full,
        "poster": poster_final,
        "fanart": fanart_final,
        "rating": float(tmdb_rating) if tmdb_rating else 0.0,
        "type":   "movie" if kind == "film" else "series" if kind == "series" else "movie",
        "dubbed": False,
    }
    ui.add_video_item(handle, item_for_ui, play_url, is_folder=False,
                       label_override=label)


def view_list_tv_program(handle, base_url, params):
    """v0.0.63: TV program dnes - hlavni view.

    Zdroj: tvprogram.idnes.cz (CSFD je nedostupne kvuli Anubis CF challenge).
    Zobrazi:
      - filmy z prime time (>= 18:00) chronologicky napric kanaly
      - folder "Vsechny filmy dnes" (od pulnoci)
      - folder "Serialy a dokumenty dnes vecer"
      - per-channel folders (CT1, Nova, Prima, ...)
    """
    force_refresh = params.get("refresh") == "1"
    items = tv_program.fetch_today(force_refresh=force_refresh)

    addon = _addon()
    icon = addon.getAddonInfo("icon")
    fanart = addon.getAddonInfo("fanart")

    # Aktualizovat nahore
    refresh_url = ui.build_url(base_url, action="list_tv_program", refresh="1")
    ui.add_dir_item(
        handle=handle,
        label="[COLOR FF66CCFF][I]>>> Aktualizovat "
              "(stahnout cerstvy TV program)[/I][/COLOR]",
        url=refresh_url, icon=icon, fanart=fanart,
    )

    if not items:
        ui.show_notification(
            "Nepodarilo se stahnout TV program. Zkus 'Aktualizovat' za par minut.",
            time_ms=7000)
        # Fallback odkaz na iDNES
        browser_url = ui.build_url(base_url, action="open_csfd",
                                    url="https://tvprogram.idnes.cz/")
        ui.add_dir_item(
            handle=handle,
            label="[COLOR FFFFA500][B]>>> Otevrit iDNES TV program "
                  "v prohlizeci[/B][/COLOR]",
            url=browser_url, icon=icon, fanart=fanart,
        )
        xbmcplugin.setContent(handle, "movies")
        xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)
        return

    # Sub-folders
    folder_all_films = ui.build_url(base_url, action="tv_program_films",
                                     scope="all")
    ui.add_dir_item(
        handle=handle,
        label="[COLOR FFFFD700][B]Vsechny filmy dnes (cely den)[/B][/COLOR]",
        url=folder_all_films, icon=icon, fanart=fanart,
    )

    folder_series = ui.build_url(base_url, action="tv_program_films",
                                  scope="series")
    ui.add_dir_item(
        handle=handle,
        label="[COLOR FFFFD700][B]Serialy a dokumenty dnes[/B][/COLOR]",
        url=folder_series, icon=icon, fanart=fanart,
    )

    # Per-channel folders
    ui.add_dir_item(
        handle=handle,
        label="[COLOR FF888888]--- Podle kanalu ---[/COLOR]",
        url=ui.build_url(base_url, action="list_tv_program"),  # no-op
        icon=icon, fanart=fanart,
    )
    channels = tv_program.get_channels()
    for cid, cname in channels:
        folder_url = ui.build_url(base_url, action="tv_program_channel",
                                   channel_id=cid)
        ui.add_dir_item(
            handle=handle, label=f"[B]{cname}[/B] - dnesni program",
            url=folder_url, icon=icon, fanart=fanart,
        )

    # Hlavni obsah: filmy z prime time (18:00+), future only.
    prime_films = tv_program.filter_films(items, only_future=True,
                                           prime_time_only=True)
    if prime_films:
        ui.add_dir_item(
            handle=handle,
            label="[COLOR FF888888]--- Filmy dnes vecer (od 18:00) ---[/COLOR]",
            url=ui.build_url(base_url, action="list_tv_program"),  # no-op
            icon=icon, fanart=fanart,
        )
        for film in prime_films:
            _render_tv_item(handle, base_url, film)

    xbmcplugin.setContent(handle, "movies")
    xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)


def view_tv_program_films(handle, base_url, params):
    """v0.0.63: Sub-view pro 'Vsechny filmy dnes' / 'Serialy a dokumenty'.

    scope=all     -> filmy cely den (future only)
    scope=series  -> serialy + dokumenty (future only)
    """
    scope = params.get("scope") or "all"
    items = tv_program.fetch_today()

    if scope == "series":
        # Serialy + dokumenty
        out = []
        for it in items:
            if it.get("kind") in ("series", "documentary"):
                if not it.get("is_past"):
                    out.append(it)
        out.sort(key=lambda x: (x.get("start_min") or 0,
                                 x.get("channel") or ""))
        title_label = "Serialy a dokumenty dnes"
    else:
        out = tv_program.filter_films(items, only_future=True,
                                       prime_time_only=False)
        title_label = "Vsechny filmy dnes"

    if not out:
        ui.show_notification(
            f"Zadne polozky pro '{title_label}'", time_ms=5000)
        xbmcplugin.setContent(handle, "movies")
        xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)
        return

    for it in out:
        _render_tv_item(handle, base_url, it)

    xbmcplugin.setContent(handle, "movies")
    xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)


def view_tv_program_channel(handle, base_url, params):
    """v0.0.63: Program jednoho kanalu na dnes (filmy+serialy+dokumenty,
    razene chronologicky).
    """
    cid = params.get("channel_id") or ""
    if not cid:
        ui.show_notification("Chybi channel_id", time_ms=3000)
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return

    items = tv_program.get_channel_today(cid, only_future=True)
    if not items:
        ui.show_notification("Kanal nema dnes zadny dalsi obsah "
                              "(nebo neni v cache).", time_ms=5000)
        xbmcplugin.setContent(handle, "movies")
        xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)
        return

    for it in items:
        # V channel view nemusime opakovat kanal v kazdem labelu - je nahore.
        _render_tv_item(handle, base_url, it, show_channel_in_label=False)

    xbmcplugin.setContent(handle, "movies")
    xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)


# ---------------------------------------------------------------------------
# Voyo (SK) - discovery + Webshare search bridge (v0.0.67)
# ---------------------------------------------------------------------------

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

    for tile in tiles:
        _render_voyo_tile(handle, base_url, tile, section)

    content_type = "tvshows" if section in ("serialy", "relacie") else "movies"
    xbmcplugin.setContent(handle, content_type)
    xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)


def view_refresh_rubrika(handle, base_url, params):
    """v0.0.63: smaze rubric cache + redirect zpet na rubric (force fresh fetch).

    URL format: action=refresh_rubrika&target=list_4k[&sort=recent][&query=Avatar]

    Strategie:
      1) Zjisti cache prefix podle 'target' (list_4k -> 'rubrika:4k:').
      2) cache.cache_clear_prefix(prefix) - smaze vsechny varianty
         (default listing, search varianty, ...).
      3) Notifikuj usera "Aktualizuji rubriku..."
      4) Container.Update zpet na puvodni rubric s page=1 + replace -
         search_action se v breadcrumb nahradi -> Back funguje normalne.
    """
    target = (params.get("target") or "").strip()
    if not target or target not in _RUBRIC_CACHE_PREFIXES:
        ui.show_notification(f"Neznamy refresh target: {target}", time_ms=4000)
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return

    from . import cache as _cache
    prefix = _RUBRIC_CACHE_PREFIXES[target]
    n = _cache.cache_clear_prefix(prefix)
    log.info("refresh_rubrika: target=%s, prefix=%s, smazano %d klicu",
             target, prefix, n)

    ui.show_notification(
        f"Aktualizuji rubriku ({n} cache klicu smazano)...", time_ms=3000)

    # Zpet na rubric s zachovanim sort/query.
    redirect_params = {"action": target, "page": 1}
    sort = (params.get("sort") or "").strip()
    query = (params.get("query") or "").strip()
    if sort:
        redirect_params["sort"] = sort
    if query:
        redirect_params["query"] = query

    redirect_url = ui.build_url(base_url, **redirect_params)
    import xbmc  # type: ignore
    xbmc.executebuiltin(f"Container.Update({redirect_url},replace)")
    xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)


_ACTIONS = {
    "root":                  lambda h, b, p: view_root(h, b),
    "tools":                 lambda h, b, p: view_tools(h, b),
    "menu_movies":           lambda h, b, p: view_menu_movies(h, b),
    "menu_series":           lambda h, b, p: view_menu_series(h, b),
    "menu_discover":         lambda h, b, p: view_menu_discover(h, b),
    "menu_library":          lambda h, b, p: view_menu_library(h, b),
    "open_settings":         view_open_settings,
    "open_csfd":             view_open_csfd,
    "list_movies":           view_list_movies,
    "list_movies_new_dub":   view_list_movies_new_dub,
    "list_kids":             view_list_kids,
    "list_series":           view_list_series,
    "list_series_new_dub":   view_list_series_new_dub,
    "list_series_seasons":   view_list_series_seasons,
    "list_series_episodes":  view_list_series_episodes,
    "list_latest":           view_list_latest,
    "list_4k":               view_list_4k,
    "list_bluray":           view_list_bluray,
    "list_animated":         view_list_animated,    # v0.0.69
    "search_4k":             view_search_4k,
    "search_bluray":         view_search_bluray,
    "search_animated":       view_search_animated,  # v0.0.69
    "list_my_files":         view_list_my_files,
    "search":                view_search,
    "search_history_forget": view_search_history_forget,
    "search_history_clear":  view_search_history_clear,
    "play_trailer":          view_play_trailer,
    "continue_watching":     view_continue_watching,
    "watched_forget":        view_watched_forget,
    "watched_mark":          view_watched_mark,
    "watched_clear":         view_watched_clear,
    "trending":              view_trending,
    "trending_movies":       view_trending_movies,
    "trending_tv":           view_trending_tv,
    "genres_movies":         view_genres_movies,
    "genres_tv":             view_genres_tv,
    "discover_movies":       view_discover_movies,
    "discover_tv":           view_discover_tv,
    "clear_cache":           view_clear_cache,
    "test_login":            view_test_login,
    "test_subs":             view_subs_test,       # v0.0.62
    "subs_download":         view_subs_download,   # v0.0.62
    "refresh_rubrika":       view_refresh_rubrika, # v0.0.63
    "list_tv_program":       view_list_tv_program,    # v0.0.63
    "tv_program_films":      view_tv_program_films,   # v0.0.63
    "tv_program_channel":    view_tv_program_channel, # v0.0.63
    "menu_platforms":        view_menu_platforms,     # v0.0.65
    "platform":              view_platform,           # v0.0.65
    "platform_movies":       view_platform_movies,    # v0.0.65
    "platform_tv":           view_platform_tv,        # v0.0.65
    "menu_voyo":             view_menu_voyo,          # v0.0.67
    "voyo_section":          view_voyo_section,       # v0.0.67
    "voyo_category":         view_voyo_category,      # v0.0.67
    "donate":                view_donate,             # v0.0.73
    "refresh_icons":         view_refresh_icons,      # v0.0.79
}


def _check_post_upgrade() -> None:
    """v0.0.79: Pri prvnim spusteni nove verze pluginu vynuti Kodi
    aby refresh interni texture cache (icon, fanart) a addon metadata.

    Problem (user report v0.0.79):
        Po upgrade pluginu Kodi cachuje stary icon.png v Texture DB
        (Textures13.db). Pri zobrazeni addonu Kodi pouzije cached
        bitmapu mistgo aby precetl novy soubor z disku. User vidi
        starou ikonu i kdyz ZIP obsahuje novou.

    Reseni:
        Detekce upgrade pomoci 'last_seen_version' settings. Kdyz
        aktualni verze != ulozena, plugin zavola Kodi builtins ktere
        vynuti reload addon metadata + skin (= cache invalidace pro
        ikony tohoto pluginu).

    Idempotentni - bezi jen jednou na verzi.
    """
    try:
        addon = _addon()
        current_ver = addon.getAddonInfo("version") or ""
        last_ver = addon.getSetting("last_seen_version") or ""
        if not current_ver or current_ver == last_ver:
            return

        log.info("Detekovan upgrade pluginu %s -> %s, refreshuji Kodi cache",
                 last_ver or "(prvni spusteni)", current_ver)

        try:
            import xbmc  # type: ignore
            # UpdateLocalAddons: Kodi znovu nacte addon manifest z disku,
            # vcetne icon/fanart cest. Texture cache pro tento addon
            # je invalidovana.
            xbmc.executebuiltin('UpdateLocalAddons')
        except Exception as exc:  # noqa: BLE001
            log.debug("UpdateLocalAddons selhalo: %s", exc)

        try:
            addon.setSetting("last_seen_version", current_ver)
        except Exception as exc:  # noqa: BLE001
            log.debug("setSetting last_seen_version selhalo: %s", exc)
    except Exception as exc:  # noqa: BLE001
        log.debug("_check_post_upgrade selhalo: %s", exc)


def route(params: Dict[str, str]) -> None:
    handle = int(sys.argv[1])
    base_url = sys.argv[0]

    # v0.0.64: Spustime shutdown watcher (idempotent) - daemon thread
    # sleduje xbmc.Monitor a pri Kodi abortu signaluje vsem moduly aby
    # se ukoncily ihned bez cekani na network timeouty.
    _shutdown.start()

    # v0.0.79: Pri prvnim spusteni nove verze refresh Kodi texture cache,
    # aby user videl spravne icon/fanart hned po upgrade (bez nutnosti
    # uninstall + reinstall).
    _check_post_upgrade()

    action = (params.get("action") or "root").lower()
    log.debug("router.route(action=%s, params=%s)", action, params)

    try:
        if action == "play":
            view_play(handle, params)
            return
        if action == "play_pick":
            view_play_pick(handle, params)
            return
        if action == "tmdb_play_movie":
            view_tmdb_play_movie(handle, params)
            return

        view_fn = _ACTIONS.get(action)
        if view_fn is None:
            log.warning("Neznámá akce: %s – vracím root.", action)
            view_root(handle, base_url)
        else:
            view_fn(handle, base_url, params)
    except Exception as exc:  # noqa: BLE001
        log.exception("router.route() selhalo: %s", exc)
        ui.show_notification(str(exc) or "Error")
        try:
            xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        except Exception:  # noqa: BLE001
            pass
