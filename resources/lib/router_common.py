# -*- coding: utf-8 -*-
"""
router_common.py
----------------
Sdilene helpery, renderery a konstanty pro router a views/*.
Extrahovano z router.py (refaktor v0.0.85).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

import xbmcaddon  # type: ignore
import xbmcgui  # type: ignore
import xbmcplugin  # type: ignore

from . import api_webshare
from . import clean_title as _ct
from . import csfd
from . import prefetch
from . import subtitles
from . import tmdb
from . import ui
from . import watched

log = logging.getLogger("klempcinema.router")

MAX_PAGE = 50

_REFRESHABLE_ACTIONS = {
    "list_movies", "list_movies_new_dub", "list_kids", "list_series",
    "list_series_new_dub", "list_latest", "list_4k", "list_bluray",
    "list_animated",
    "list_documentary",
}

RUBRIC_CACHE_PREFIXES = {
    "list_movies":           "rubrika:movies:",
    "list_movies_new_dub":   "rubrika:newdub:",
    "list_kids":             "rubrika:kids",
    "list_series":           "rubrika:series:",
    "list_series_new_dub":   "rubrika:seriesnewdub:",
    "list_latest":           "rubrika:latest:",
    "list_4k":               "rubrika:4k:",
    "list_bluray":           "rubrika:bluray:",
    "list_animated":         "rubrika:animated:",
    "list_documentary":      "rubrika:documentary:",
}


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


def _build_play_pick_url(
    base_url: str,
    base: str,
    mode: str = "movie",
    cz_only: bool = False,
    year: Optional[int] = None,
) -> str:
    """URL pro quality picker; cz_only=jen CZ dab/tit; year=TMDB rok titulu."""
    extra: Dict[str, str] = {}
    if cz_only:
        extra["cz_only"] = "1"
    if year:
        try:
            extra["year"] = str(int(year))
        except (TypeError, ValueError):
            pass
    return ui.build_url(base_url, action="play_pick", base=base, mode=mode, **extra)


def _item_play_year(item: Dict[str, Any]) -> Optional[int]:
    """Rok pro filtr variant pri prehravani - jen po overenem TMDB matchi.

    WS rok z nazvu souboru (2025/2026 upload tag) casto neni rok filmu
    a pri kliku by vyfiltroval vsechny varianty (napr. Diabel nosi pradu 2006).
    """
    if not item.get("tmdb_id"):
        return None
    ws = (item.get("base_title") or item.get("title") or "").strip()
    meta = (item.get("title_localized") or "").strip()
    if ws and meta:
        try:
            from .title_match import metadata_title_compatible
            if not metadata_title_compatible(
                ws, meta, item.get("original_title") or "",
            ):
                return None
        except Exception:  # noqa: BLE001
            pass
    try:
        y = item.get("year")
        return int(y) if y else None
    except (TypeError, ValueError):
        return None


def _render_movie_list(
    handle: int,
    base_url: str,
    result,
    list_action: str,
    page: int,
    sort: str,
    content: str = "movies",
    cz_only: bool = False,
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
            url = _build_play_pick_url(base_url, item["base_title"],
                                       mode="movie", cz_only=cz_only,
                                       year=_item_play_year(item))
        elif item.get("tmdb_id"):
            title = (
                item.get("title_localized") or item.get("title") or ""
            ).strip()
            year = item.get("year")
            url = ui.build_url(
                base_url, action="tmdb_play_movie",
                title=title,
                year=str(year) if year else "",
                tmdb_id=str(item.get("tmdb_id")),
            )
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

    ui.end_directory(handle, content=content)


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
        sname = (
            item.get("title_localized")
            or item.get("title")
            or item.get("series_name")
            or ""
        ).strip()
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

    ui.end_directory(handle, content="tvshows")


def _render_flat_list(
    handle: int,
    base_url: str,
    result,
    list_action: str,
    page: int,
    sort: str,
    content: str = "movies",
    cz_only: bool = False,
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
            url = _build_play_pick_url(base_url, item["base_title"],
                                       mode=mode, cz_only=cz_only,
                                       year=_item_play_year(item))
        elif item.get("series_name"):
            # Seriál - otevři sezóny (použij zobrazený název, ne WS group name)
            url = ui.build_url(
                base_url,
                action="list_series_seasons",
                name=(item.get("title") or item.get("series_name")),
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

    ui.end_directory(handle, content=content)

def _addon_icon_for(name: str) -> str:
    """Vrati absolutni FS cestu k ikone (jako Sosáč get_icon)."""
    addon = _addon()
    path = addon.getAddonInfo("path")
    # Podporuj i podadresare (tv/hbo.png, menu/movies.png).
    rel = str(name or "").replace("\\", "/").lstrip("/")
    candidate = os.path.normpath(os.path.join(path, "resources", "icons", *rel.split("/")))
    if not os.path.exists(candidate):
        return addon.getAddonInfo("icon")
    try:
        import xbmcvfs  # type: ignore

        return xbmcvfs.translatePath(candidate)
    except Exception:  # noqa: BLE001
        return candidate


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
    ui.end_icon_menu(handle)


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
def _prefetch_next(action: str, fetcher, sort: str, page: int, has_more: bool, **extra):
    """Spustí na pozadí dofetch další stránky."""
    key = f"{action}:{sort}:{page + 1}:{':'.join(f'{k}={v}' for k, v in extra.items())}"
    prefetch.schedule(
        cache_key=key,
        fetcher=lambda: fetcher(sort=sort, page=page + 1, **extra),
        page=page,
        has_more=has_more,
    )
def _render_with_search_top(handle, base_url, result, list_action: str,
                             search_action: str, page: int, sort: str,
                             search_label_id: int = 30092,
                             query: str = "",
                             search_year: Optional[int] = None,
                             cz_only: bool = False):
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
            url = _build_play_pick_url(base_url, item["base_title"],
                                       mode="movie", cz_only=cz_only,
                                       year=_item_play_year(item))
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
                                         query=query, page=1,
                                         **({"year": search_year}
                                            if search_year else {}))
            ui.add_dir_item(
                handle=handle,
                label=(f"[COLOR FF00FF88][B]>>> Hledat '{query[:30]}' "
                       f"ve VSECH filmech (mimo rubriku)...[/B][/COLOR]"),
                url=fallback_url, icon=icon2, fanart=fanart2,
            )
        else:
            ui.show_notification(_tr(30023))

    extra = {}
    if query:
        extra["query"] = query
    if search_year:
        extra["year"] = search_year
    _add_next_page(handle, base_url, list_action, sort, page,
                   has_more=has_more, **extra)

    ui.end_directory(handle, content="movies")


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


def _parse_user_search(raw: str):
    """
  0.0.98: Rozparsuje dotaz z klavesnice na (cisty_titul, rok, label).

    Rok se zachova pro WS/TMDB – clean_title ho z titulu odstranuje,
    ale pro 'Michael 2026' musime na Webshare hledat s rokem, jinak
    se ztrati mezi stovkami jinych 'Michael'.
    """
    from typing import Optional, Tuple
    raw = (raw or "").strip()
    if not raw:
        return "", None, ""
    try:
        year = _ct.extract_year(raw)
        clean = (_ct.clean_title(raw) or raw).strip()
    except Exception:  # noqa: BLE001
        year = None
        clean = raw.strip()
    if year and clean:
        label = f"{clean} {year}"
    else:
        label = clean or raw
    return clean, year, label
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
            f"Zadne epizody pro '{name[:40]}' - "
            "zkus fallback search nahore.",
            time_ms=6000,
        )

    ui.end_directory(handle, content="episodes")
