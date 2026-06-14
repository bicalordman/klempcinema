# -*- coding: utf-8 -*-
"""
search_history.py
-----------------
Historie vyhledavanych dotazu (v0.0.52). Uklada poslednich N hledani do
addon profile dir, aby user mohl jedním kliknutim opakovat predchozi search.

PUBLIC API:
    add(query)               - prida (nebo posune nahoru) dotaz do historie
    get_all(limit=10)        - vrati posledni N dotazu (newest first)
    remove(query)            - smaze konkretni dotaz
    clear()                  - smaze vsechnu historii
    is_enabled()             - default True, lze vypnout v settings

Soubor: <profile>/search_history.json
Format: {"items": [{"q": "...", "ts": 1234567890.0}, ...], "version": 1}
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

log = logging.getLogger("klempcinema.search_history")

MAX_ITEMS = 20  # nehromadit hlubji nez 20 polozek (klavesnice jen 10)
_lock = threading.Lock()


def _profile_dir() -> str:
    try:
        import xbmcaddon  # type: ignore
        import xbmcvfs  # type: ignore
        a = xbmcaddon.Addon()
        return xbmcvfs.translatePath(a.getAddonInfo("profile"))
    except Exception:  # noqa: BLE001
        return os.path.join(os.path.expanduser("~"), ".klempcinema")


def _file() -> str:
    d = _profile_dir()
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return os.path.join(d, "search_history.json")


def is_enabled() -> bool:
    """Lze vypnout v settings 'search_history_enabled' (default True)."""
    try:
        import xbmcaddon  # type: ignore
        raw = (xbmcaddon.Addon().getSetting("search_history_enabled") or "true").lower()
        return raw in ("true", "1")
    except Exception:  # noqa: BLE001
        return True


def _load() -> Dict[str, Any]:
    path = _file()
    if not os.path.exists(path):
        return {"items": [], "version": 1}
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        if not isinstance(data, dict) or "items" not in data:
            return {"items": [], "version": 1}
        return data
    except (OSError, ValueError) as exc:
        log.warning("search_history load failed: %s", exc)
        return {"items": [], "version": 1}


def _save(data: Dict[str, Any]) -> None:
    path = _file()
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(data, fp, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("search_history save failed: %s", exc)
        try:
            os.remove(tmp)
        except OSError:
            pass


def add(query: str) -> None:
    """Prida dotaz na vrch historie (nebo posune existujici nahoru)."""
    q = (query or "").strip()
    if not q or len(q) < 2:
        return
    if not is_enabled():
        return
    norm = q.lower()
    with _lock:
        data = _load()
        items = data["items"]
        # Vyhodit duplicit (case-insensitive)
        items = [it for it in items
                 if (it.get("q") or "").strip().lower() != norm]
        items.insert(0, {"q": q, "ts": time.time()})
        # Ostrihni na MAX_ITEMS
        data["items"] = items[:MAX_ITEMS]
        _save(data)
    log.debug("search_history.add(%r)", q)


def get_all(limit: int = 10) -> List[str]:
    """Vrati posledni N dotazu (newest first)."""
    if not is_enabled():
        return []
    with _lock:
        data = _load()
    items = data.get("items") or []
    return [it.get("q") for it in items[:limit] if it.get("q")]


def remove(query: str) -> None:
    """Smaze konkretni dotaz z historie."""
    if not query:
        return
    norm = query.strip().lower()
    with _lock:
        data = _load()
        data["items"] = [it for it in (data.get("items") or [])
                         if (it.get("q") or "").strip().lower() != norm]
        _save(data)


def clear() -> int:
    """Smaze celou historii. Vrati pocet smazanych zaznamu."""
    with _lock:
        data = _load()
        n = len(data.get("items") or [])
        data["items"] = []
        _save(data)
    return n
