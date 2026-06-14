# -*- coding: utf-8 -*-
"""
watched.py
----------
Sledování shlédnutého obsahu a pokračování ve sledování.

Uložiště:
    userdata/addon_data/plugin.video.klempcinema/watched.json
    {
      "items": {
        "<file_id>": {
           "title": str,
           "year": int|None,
           "type": "movie"|"episode"|"series",
           "poster": str,
           "fanart": str,
           "plot": str,
           "position": float,   # sekundy
           "duration": float,   # sekundy
           "progress": float,   # 0.0 - 1.0
           "watched": bool,     # True pokud progress > WATCHED_THRESHOLD
           "ts_updated": float, # epoch
           "base_title": str,   # pro play_pick
           "mode": str,         # "movie"|"episode"
        },
        ...
      }
    }

API:
    save_progress(file_id, position, duration, **meta) - uloží pozici
    get_resume_position(file_id) -> float | 0          - pozice pro resume
    get_continue_watching(limit=20) -> list of items   - pro hlavní menu
    mark_watched(file_id)                              - mark done
    forget(file_id)                                    - remove item
    clear_all()                                        - reset
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

log = logging.getLogger("klempcinema.watched")

# Pokud progress > 90 %, považujeme za shlédnuté (a nezobrazujeme v Pokračovat).
WATCHED_THRESHOLD = 0.90
# Pokud progress < 2 %, ani neukládáme - user jen prošel filmem.
MIN_SAVE_PROGRESS = 0.02
# Maximální položek v "Pokračovat" - víc už nikoho nezajímá.
DEFAULT_CONTINUE_LIMIT = 30

_lock = threading.Lock()


def _profile_dir() -> str:
    try:
        import xbmcaddon  # type: ignore
        import xbmcvfs    # type: ignore
        addon = xbmcaddon.Addon()
        return xbmcvfs.translatePath(addon.getAddonInfo("profile"))
    except Exception:  # noqa: BLE001
        return os.path.join(os.path.expanduser("~"), ".klempcinema")


def _state_path() -> str:
    d = _profile_dir()
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return os.path.join(d, "watched.json")


def _load() -> Dict[str, Any]:
    path = _state_path()
    if not os.path.exists(path):
        return {"items": {}}
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        if not isinstance(data, dict) or "items" not in data:
            return {"items": {}}
        if not isinstance(data["items"], dict):
            data["items"] = {}
        return data
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        log.warning("watched.json load chyba: %s", exc)
        return {"items": {}}


def _save(data: Dict[str, Any]) -> None:
    path = _state_path()
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(data, fp, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("watched.json save chyba: %s", exc)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


def save_progress(
    file_id: str,
    position: float,
    duration: float,
    **meta: Any,
) -> None:
    """
    Uloží aktuální pozici sledování. Volá se z PlayerHook-u.

    :param meta: title, year, type, poster, fanart, plot, base_title, mode
    """
    if not file_id:
        return
    if duration <= 0:
        return
    progress = min(1.0, max(0.0, position / duration))
    if progress < MIN_SAVE_PROGRESS:
        return  # too early

    with _lock:
        data = _load()
        items = data["items"]
        existing = items.get(file_id) or {}
        entry = {
            **existing,
            "position":   float(position),
            "duration":   float(duration),
            "progress":   float(progress),
            "watched":    bool(progress >= WATCHED_THRESHOLD),
            "ts_updated": time.time(),
        }
        # Naplníme metadata jen, pokud jsou nová (přepíšeme prázdné).
        for k in ("title", "year", "type", "poster", "fanart", "plot",
                  "base_title", "mode", "dubbed"):
            v = meta.get(k)
            if v is not None and v != "":
                entry[k] = v
        items[file_id] = entry
        _save(data)
    log.info("watched: save %s progress=%.1f%% (%s)",
             file_id, progress * 100, meta.get("title") or "")


def get_resume_position(file_id: str) -> float:
    """Vrátí pozici v sekundách pro resume, nebo 0 pokud nic / je shlédnuté."""
    if not file_id:
        return 0.0
    with _lock:
        data = _load()
        entry = data["items"].get(file_id)
    if not entry:
        return 0.0
    if entry.get("watched"):
        return 0.0
    progress = float(entry.get("progress") or 0)
    if progress < MIN_SAVE_PROGRESS:
        return 0.0
    return float(entry.get("position") or 0)


def get_resume_for_base(base_title: str, mode: str = "movie") -> float:
    """
    v0.0.52: Cross-variant resume - najde rozkoukany progress podle
    base_title+mode (ne podle Webshare file_id). To umoznuje pokracovat
    ve sledovani i kdyz user prepne kvalitu (4K -> 1080p) - jiny file_id,
    ale stejne dilo.

    Vrati pozici nejnovejsi varianty stejneho base_title (max ts_updated),
    nebo 0 pokud nic / vse shlednute.
    """
    if not base_title:
        return 0.0
    norm = base_title.strip().lower()
    with _lock:
        data = _load()
    best_ts = 0.0
    best_pos = 0.0
    for entry in data["items"].values():
        if entry.get("watched"):
            continue
        if (entry.get("base_title") or "").strip().lower() != norm:
            continue
        if (entry.get("mode") or "movie") != mode:
            continue
        ts = float(entry.get("ts_updated") or 0)
        if ts > best_ts:
            best_ts = ts
            best_pos = float(entry.get("position") or 0)
    return best_pos if best_pos >= 1.0 else 0.0


def get_continue_watching(limit: int = DEFAULT_CONTINUE_LIMIT) -> List[Dict[str, Any]]:
    """
    Vrátí list položek pro "Pokračovat ve sledování" seřazený podle
    nejnovější aktivity. Vyřazuje plně shlédnuté.
    """
    with _lock:
        data = _load()
    items = []
    for fid, entry in data["items"].items():
        if entry.get("watched"):
            continue
        if float(entry.get("progress") or 0) < MIN_SAVE_PROGRESS:
            continue
        items.append({
            "id":          fid,
            "title":       entry.get("title") or "",
            "year":        entry.get("year"),
            "type":        entry.get("type") or "movie",
            "poster":      entry.get("poster") or "",
            "fanart":      entry.get("fanart") or "",
            "plot":        entry.get("plot") or "",
            "position":    float(entry.get("position") or 0),
            "duration":    float(entry.get("duration") or 0),
            "progress":    float(entry.get("progress") or 0),
            "base_title":  entry.get("base_title") or "",
            "mode":        entry.get("mode") or "movie",
            "dubbed":      bool(entry.get("dubbed")),
            "ts_updated":  float(entry.get("ts_updated") or 0),
        })
    items.sort(key=lambda x: -x["ts_updated"])
    return items[:limit]


def mark_watched(file_id: str) -> None:
    """Označí položku jako celou shlédnutou (vyřadí z Pokračovat)."""
    if not file_id:
        return
    with _lock:
        data = _load()
        entry = data["items"].get(file_id)
        if not entry:
            return
        entry["watched"] = True
        entry["progress"] = 1.0
        entry["ts_updated"] = time.time()
        _save(data)


def forget(file_id: str) -> None:
    """Smaže záznam o jednom souboru."""
    if not file_id:
        return
    with _lock:
        data = _load()
        if file_id in data["items"]:
            data["items"].pop(file_id, None)
            _save(data)


def clear_all() -> int:
    """Smaže celou historii. Vrátí počet smazaných."""
    with _lock:
        data = _load()
        n = len(data["items"])
        data["items"] = {}
        _save(data)
    return n
