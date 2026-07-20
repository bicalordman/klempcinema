# -*- coding: utf-8 -*-
"""Nastroje: cache, diagnostika, titulky, refresh rubriky."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict

import xbmc  # type: ignore
import xbmcgui  # type: ignore
import xbmcplugin  # type: ignore

from .. import api_webshare
from .. import subtitles
from .. import tmdb
from .. import ui
from ..router_common import (
    RUBRIC_CACHE_PREFIXES,
    _addon,
    _tr,
    _tr_safe,
)

log = logging.getLogger("klempcinema.views.tools")

def view_clear_cache(handle, base_url, params):
    """
    Smaže cache TMDB / OpenSubtitles / metadata (lokální .json soubory).
    Volá se z hlavního menu položkou 30070.

    Mažu:
        cache/    - obecná MD5-keyed cache (TMDB, ČSFD, varianty, agregované buffery)
        metadata/ - per-title JSON metadata cache (z metadata_resolver)
    """
    from .. import cache as _cache_mod
    from .. import metadata_cache as _mc

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
    ui.end_directory(handle)


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

    ui.end_directory(handle)


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
    ui.end_directory(handle)


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

    ui.end_directory(handle)


def view_refresh_metadata(handle, base_url, params):
    """v0.0.118: Obnovi TMDB/CSFD metadata pro jeden titul z context menu."""
    title = (params.get("title") or "").strip()
    if not title:
        ui.show_notification("Chybi nazev titulu.", time_ms=4000)
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return

    year = None
    try:
        y = (params.get("year") or "").strip()
        if y:
            year = int(y)
    except (TypeError, ValueError):
        year = None

    kind_param = (params.get("kind") or "movie").strip().lower()
    kind = "series" if kind_param in ("tv", "series", "episode", "tvshow") else "movie"

    ui.show_notification(
        _tr_safe(30320, "Obnovuji metadata..."), time_ms=2500)

    try:
        result = api_webshare.refresh_title_metadata(title, year, kind)
    except Exception as exc:  # noqa: BLE001
        log.exception("refresh_metadata fail: %s", exc)
        ui.show_notification(
            _tr_safe(30322, "Obnoveni metadat selhalo."), time_ms=5000)
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return

    ok = bool(
        result.get("tmdb_id")
        or result.get("csfd_id")
        or api_webshare._item_has_display_poster(result)
    )
    if ok:
        label = (result.get("title_localized") or result.get("title") or title)
        msg = _tr_safe(30321, "Metadata obnovena: {title}").format(title=label)
        log.info("refresh_metadata OK: %r -> tmdb_id=%s poster=%s",
                 title, result.get("tmdb_id"), bool(result.get("poster")))
    else:
        msg = _tr_safe(30323, "Metadata pro \"{title}\" nenalezena.").format(
            title=title[:50])

    try:
        xbmcgui.Dialog().notification(
            "KlempCinema", msg, xbmcgui.NOTIFICATION_INFO, 4000)
    except Exception:  # noqa: BLE001
        ui.show_notification(msg, time_ms=4000)

    try:
        xbmc.executebuiltin("Container.Refresh")
    except Exception:  # noqa: BLE001
        pass
    xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)


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
    if not target or target not in RUBRIC_CACHE_PREFIXES:
        ui.show_notification(f"Neznamy refresh target: {target}", time_ms=4000)
        xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        return

    from .. import cache as _cache
    prefix = RUBRIC_CACHE_PREFIXES[target]
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
    xbmc.executebuiltin(f'Container.Update("{redirect_url}",replace)')
    xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
