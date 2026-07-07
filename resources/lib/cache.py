# -*- coding: utf-8 -*-
"""
cache.py
--------
Jednoduchý on-disk cache (JSON soubory) pro TMDB lookupy a další.
Klíče se hashují (MD5) na filenames, hodnoty se serializují do JSON
s timestamp pro TTL.

Soubory leží v addon profile dir:
    special://profile/addon_data/plugin.video.klempcinema/cache/
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from typing import Any, Optional

log = logging.getLogger("klempcinema.cache")

DEFAULT_TTL = 7 * 24 * 3600  # 7 dní

# v0.0.114: in-memory cache nad diskem - opakovane otevirani rubrik
# (50+ cache_get na stranku) bylo pomale kvuli stovkam JSON open().
_mem_lock = threading.Lock()
_mem_cache: dict = {}  # key -> (expire_ts, value)
_MEM_TTL = 600  # 10 min RAM cache (cross-rubric reuse)
_MEM_CACHE_MAX = 400


def trim_memory_cache(max_entries: int = _MEM_CACHE_MAX) -> int:
    """Omez RAM cache – Kodi drzi interpreter mezi navigacemi."""
    now = time.time()
    removed = 0
    with _mem_lock:
        expired = [k for k, (exp, _) in _mem_cache.items() if now >= exp]
        for k in expired:
            _mem_cache.pop(k, None)
            removed += 1
        if len(_mem_cache) <= max_entries:
            return removed
        sorted_keys = sorted(_mem_cache, key=lambda k: _mem_cache[k][0])
        for k in sorted_keys[: len(_mem_cache) - max_entries]:
            _mem_cache.pop(k, None)
            removed += 1
    return removed


def _profile_dir() -> str:
    """Vrátí addon profile dir (cross-platform)."""
    try:
        import xbmcaddon  # type: ignore
        import xbmcvfs    # type: ignore
        addon = xbmcaddon.Addon()
        return xbmcvfs.translatePath(addon.getAddonInfo("profile"))
    except Exception:  # noqa: BLE001
        return os.path.join(os.path.expanduser("~"), ".klempcinema")


def _cache_dir() -> str:
    d = os.path.join(_profile_dir(), "cache")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def _key_path(key: str) -> str:
    h = hashlib.md5(key.encode("utf-8")).hexdigest()
    return os.path.join(_cache_dir(), f"{h}.json")


def cache_get(key: str, ttl: int = DEFAULT_TTL) -> Optional[Any]:
    """Vrátí hodnotu z cache nebo None (pokud chybí / expirovala)."""
    now = time.time()
    with _mem_lock:
        ent = _mem_cache.get(key)
        if ent is not None:
            expire_ts, val = ent
            if now < expire_ts:
                return val
            _mem_cache.pop(key, None)

    path = _key_path(key)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fp:
            blob = json.load(fp)
        if time.time() - float(blob.get("ts", 0)) > ttl:
            return None
        value = blob.get("value")
        with _mem_lock:
            _mem_cache[key] = (now + _MEM_TTL, value)
        return value
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        log.debug("cache_get(%r) chyba: %s", key, exc)
        return None


def cache_set(key: str, value: Any) -> None:
    """Uloží hodnotu do cache (přepíše, pokud existuje). Thread-safe.

    v0.0.63: ukladame i original 'key' (string) v JSON aby cache_clear_prefix
    mohl filtrovat dle prefixu (filename je MD5, neda se z neho odvodit).
    """
    path = _key_path(key)
    # Unikátní tmp soubor per-thread, aby paralelní zápisy nekolidovaly.
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump({"ts": time.time(), "key": key, "value": value}, fp,
                      ensure_ascii=False)
        os.replace(tmp, path)
        with _mem_lock:
            _mem_cache[key] = (time.time() + _MEM_TTL, value)
    except OSError as exc:
        log.debug("cache_set(%r) chyba: %s", key, exc)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


def cache_delete(key: str) -> bool:
    """v0.0.63: smaze jeden konkretni cache klic. True kdyz se neco smazalo."""
    with _mem_lock:
        _mem_cache.pop(key, None)
    path = _key_path(key)
    try:
        if os.path.exists(path):
            os.remove(path)
            return True
    except OSError as exc:
        log.debug("cache_delete(%r) chyba: %s", key, exc)
    return False


def cache_clear_prefix(prefix: str) -> int:
    """v0.0.63: smaze vsechny klice zacinajici 'prefix'. Vraci pocet smazanych.

    Filename je MD5, takze musime otevirat kazdy .json a podle 'key' field
    (ulozeneho v cache_set) rozhodnout. Akceptovatelne: cache adresar ma
    typicky <500 souboru.
    """
    if not prefix:
        return 0
    d = _cache_dir()
    n = 0
    try:
        for name in os.listdir(d):
            if not name.endswith(".json"):
                continue
            path = os.path.join(d, name)
            try:
                with open(path, "r", encoding="utf-8") as fp:
                    blob = json.load(fp)
                stored_key = blob.get("key") or ""
                if stored_key.startswith(prefix):
                    os.remove(path)
                    n += 1
            except (OSError, ValueError, json.JSONDecodeError):
                continue
    except OSError:
        pass
    if n:
        log.info("cache_clear_prefix(%r): smazano %d souboru", prefix, n)
    return n


def cache_clear() -> int:
    """Smaže všechny cachované soubory. Vrací počet smazaných."""
    with _mem_lock:
        _mem_cache.clear()
    d = _cache_dir()
    n = 0
    try:
        for name in os.listdir(d):
            if name.endswith(".json"):
                try:
                    os.remove(os.path.join(d, name))
                    n += 1
                except OSError:
                    pass
    except OSError:
        pass
    return n


def cache_cleanup_expired(max_age_days: int = 30) -> int:
    """
    Smaže cachované soubory starší než max_age_days (default 30 dní).
    Volat sporadicky (např. 1x denně při startu pluginu) abychom udrželi
    cache adresář malý a vyčistili stale klíče po update verze
    (bumping cache keys jako tmdb:movie -> tmdb:movie:v2 zanechává sirotky).

    Vrátí počet smazaných souborů.
    """
    d = _cache_dir()
    cutoff = time.time() - (max_age_days * 86400)
    n = 0
    try:
        for name in os.listdir(d):
            if not name.endswith(".json"):
                continue
            path = os.path.join(d, name)
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    n += 1
            except OSError:
                continue
    except OSError:
        pass
    if n:
        log.info("cache_cleanup_expired: smazano %d stale souboru "
                 "(> %d dni)", n, max_age_days)
    return n


def maybe_cleanup_expired(min_interval_hours: int = 24,
                          max_age_days: int = 30) -> int:
    """
    Spustí cleanup_expired NEJVÝŠE 1x za min_interval_hours hodin.
    Stav posledního běhu se ukládá do souboru .cleanup_marker
    v cache adresáři.

    Bezpečně volat na každém startu pluginu - většinou no-op.
    """
    d = _cache_dir()
    marker = os.path.join(d, ".cleanup_marker")
    now = time.time()
    last = 0.0
    try:
        if os.path.exists(marker):
            last = os.path.getmtime(marker)
    except OSError:
        pass
    if now - last < min_interval_hours * 3600:
        return 0
    n = cache_cleanup_expired(max_age_days=max_age_days)
    try:
        with open(marker, "w", encoding="utf-8") as fp:
            fp.write(str(int(now)))
    except OSError:
        pass
    return n
