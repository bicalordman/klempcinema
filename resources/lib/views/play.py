# -*- coding: utf-8 -*-
"""Prehravani: play, play_pick, tmdb_play_movie."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import xbmc  # type: ignore
import xbmcgui  # type: ignore
import xbmcplugin  # type: ignore

from .. import api_webshare
from .. import player_tracker
from .. import subtitles
from .. import tmdb
from .. import ui
from .. import watched
from ..router_common import (
    _ensure_login,
    _extract_episode_from_base,
    _extract_year_from_base,
    _tr,
)

log = logging.getLogger("klempcinema.views.play")

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
    cz_only = (params.get("cz_only", "0") == "1")
    year: Optional[int] = None
    year_raw = (params.get("year") or "").strip()
    if year_raw:
        try:
            year = int(year_raw)
        except (TypeError, ValueError):
            year = None
    if year is None and base:
        year = api_webshare._guess_year(base)

    log.info("view_play_pick: base=%r mode=%s cz_only=%s year=%s",
             base, mode, cz_only, year)

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

    variants = api_webshare.get_quality_variants(
        base, mode=mode, cz_only=cz_only, year=year,
    )

    if not variants:
        # Diagnostická notifikace s tipem
        if cz_only:
            msg = (f"Zadne CZ varianty pro: {base[:40]}\n"
                   "(dabing/titulky na Webshare)")
        elif year:
            msg = (f"Zadne varianty pro {base[:30]} ({year})\n"
                   "(jiny rok ve jmene souboru)")
        elif mode == "episode":
            msg = f"Epizoda nenalezena: {base[:50]}\n(zkus Smazat cache v hlavnim menu)"
        else:
            msg = f"Film nenalezen: {base[:50]}\n(zkus Smazat cache)"
        ui.show_notification(msg, time_ms=8000)
        log.error("view_play_pick: zadne varianty pro base=%r mode=%s", base, mode)
        xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem())
        return

    if len(variants) == 1:
        chosen = variants[0]
    else:
        # v0.0.104: pri vice variantach VZDY picker (i kdyz auto_pick zapnuto).
        labels = api_webshare.build_variant_picker_labels(variants)
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

