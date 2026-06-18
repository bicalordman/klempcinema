# -*- coding: utf-8 -*-
"""
pagination.py
-------------
Centrální pagination logika pro KlempCinema.

v0.0.83: FROZEN PAGE SLICES - kazda stranka se pri prvnim otevreni
setri z neprirazenych polozek a uz se nemeni (fix opakovani).
Zaroven globalni sort v ramci kazde nove stranky (Michael 2026
nezmizi jen proto, ze prisel v pozdejsim WS batchi).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import cache

log = logging.getLogger("klempcinema.pagination")

UI_PAGE_SIZE_DEFAULT = 30
AGG_TTL = 30 * 60
DEFAULT_MAX_WS_PAGES = 4


def _read_ui_page_size() -> int:
    try:
        import xbmcaddon  # type: ignore
        addon = xbmcaddon.Addon()
        raw = addon.getSetting("items_per_page") or str(UI_PAGE_SIZE_DEFAULT)
        n = int(raw)
        if n < 10:
            return 10
        if n > 100:
            return 100
        return n
    except Exception:  # noqa: BLE001
        return UI_PAGE_SIZE_DEFAULT


UI_PAGE_SIZE = UI_PAGE_SIZE_DEFAULT


def paginate(items: List[Any], ui_page: int) -> List[Any]:
    if not items:
        return []
    size = _read_ui_page_size()
    start = max(0, (max(1, ui_page) - 1) * size)
    end = start + size
    return items[start:end]


def has_more_pages(total_items: int, ui_page: int) -> bool:
    return total_items > ui_page * _read_ui_page_size()


def _assigned_ids(page_slices: Dict[str, List[str]]) -> set:
    out: set = set()
    for ids in page_slices.values():
        out.update(ids)
    return out


def _unassigned_ids(items_by_id: Dict[str, Any],
                    assigned: set) -> List[str]:
    return [sid for sid in items_by_id if sid not in assigned]


def paginate_with_fetcher(
    cache_key: str,
    fetcher: Callable[[int], Optional[List[Any]]],
    ui_page: int,
    max_ws_pages: int = DEFAULT_MAX_WS_PAGES,
    sort_key: Optional[Callable[[Any], Any]] = None,
    ttl_override: Optional[int] = None,
) -> Tuple[List[Any], bool]:
    """
    Vrátí výsek pro UI stránku 'ui_page' a flag 'has_more'.

    v0.0.83: page_slices[str(page)] = frozen list stable IDs.
    Pri prvnim otevreni stranky N se z neprirazenych polozek vybere
    top page_size dle sort_key. Drivejsi stranky se nemeni.
    """
    page_size = _read_ui_page_size()
    effective_ttl = ttl_override if ttl_override is not None else AGG_TTL
    needed_assigned = ui_page * page_size

    state = cache.cache_get(cache_key, ttl=effective_ttl) or {}
    items_by_id: Dict[str, Any] = dict(state.get("items_by_id") or {})
    page_slices: Dict[str, List[str]] = {
        str(k): list(v) for k, v in (state.get("page_slices") or {}).items()
    }
    known_keys: set = set(state.get("known_keys") or [])
    next_ws_page: int = int(state.get("next_ws_page") or 1)
    exhausted: bool = bool(state.get("exhausted"))

    # Migrace ze stareho formatu
    if not items_by_id and state.get("items"):
        pool: List[str] = []
        for it in state["items"]:
            _register_item(it, items_by_id, pool, known_keys)
        _rebuild_slices_from_pool(items_by_id, pool, page_slices,
                                  page_size, sort_key)

    assigned = _assigned_ids(page_slices)
    fetched_now = 0

    def _pool_size() -> int:
        return len(items_by_id)

    while len(assigned) < needed_assigned and not exhausted and fetched_now < max_ws_pages:
        log.debug("paginate(%s): fetch WS page %d (pool=%d, assigned=%d, need=%d)",
                  cache_key, next_ws_page, _pool_size(), len(assigned),
                  needed_assigned)
        try:
            new_items = fetcher(next_ws_page)
        except Exception as exc:  # noqa: BLE001
            log.exception("paginate fetcher selhal: %s", exc)
            new_items = None

        if new_items is None:
            exhausted = True
            break

        next_ws_page += 1
        fetched_now += 1

        if new_items:
            batch = _filter_new_items(new_items, items_by_id, known_keys)
            for it in batch:
                _register_item(it, items_by_id, [], known_keys)
            log.debug("paginate(%s): WS page %d -> +%d (pool=%d)",
                      cache_key, next_ws_page - 1, len(batch), _pool_size())

        assigned = _assigned_ids(page_slices)

    # Sestav chybejici stranky 1..ui_page
    assigned = _assigned_ids(page_slices)
    for p in range(1, ui_page + 1):
        pk = str(p)
        if pk in page_slices:
            continue
        unassigned = _unassigned_ids(items_by_id, assigned)
        if not unassigned:
            page_slices[pk] = []
            continue
        if sort_key:
            try:
                unassigned.sort(
                    key=lambda sid: sort_key(items_by_id[sid]))
            except Exception as exc:  # noqa: BLE001
                log.debug("paginate page sort selhal: %s", exc)
        take = unassigned[:page_size]
        page_slices[pk] = take
        assigned = _assigned_ids(page_slices)

    all_items = [items_by_id[sid] for sid in items_by_id]
    cache.cache_set(cache_key, {
        "items_by_id": items_by_id,
        "page_slices": page_slices,
        "known_keys": list(known_keys),
        "items": all_items,
        "next_ws_page": next_ws_page,
        "exhausted": exhausted,
    })

    page_ids = page_slices.get(str(ui_page), [])
    page_items = [items_by_id[sid] for sid in page_ids if sid in items_by_id]
    assigned_all = _assigned_ids(page_slices)
    unassigned_left = len(_unassigned_ids(items_by_id, assigned_all))
    next_page = str(ui_page + 1)
    has_more = (
        unassigned_left > 0
        or not exhausted
        or bool(page_slices.get(next_page))
    )

    log.info("paginate(%s): ui_page=%d -> %d items, has_more=%s "
             "(pool=%d, pages=%d, fetched_now=%d)",
             cache_key, ui_page, len(page_items), has_more,
             len(items_by_id), len(page_slices), fetched_now)
    return page_items, has_more


def _rebuild_slices_from_pool(
    items_by_id: Dict[str, Any],
    pool: List[str],
    page_slices: Dict[str, List[str]],
    page_size: int,
    sort_key: Optional[Callable[[Any], Any]],
) -> None:
    """Migrace: rozdeli pool do stranek (best-effort)."""
    ids = list(pool)
    if sort_key and ids:
        try:
            ids.sort(key=lambda sid: sort_key(items_by_id[sid]))
        except Exception:  # noqa: BLE001
            pass
    p = 1
    while ids:
        page_slices[str(p)] = ids[:page_size]
        ids = ids[page_size:]
        p += 1


def invalidate(cache_key: str) -> None:
    cache.cache_set(cache_key, None)


def _norm(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", s.lower()).strip()


def _stable_item_id(it: Dict[str, Any]) -> str:
    tid = it.get("tmdb_id")
    if tid:
        return f"tmdb:{tid}"

    year = it.get("year") or ""
    base = _norm(
        it.get("base_title") or it.get("series_name")
        or it.get("title_localized") or it.get("title") or ""
    )
    if base:
        return f"title:{base}|{year}"

    for ident in (it.get("variant_idents") or []):
        if ident:
            return f"ident:{ident}"
    return f"anon:{id(it)}"


def _register_item(
    it: Dict[str, Any],
    items_by_id: Dict[str, Any],
    display_order: List[str],
    known_keys: set,
) -> None:
    sid = _stable_item_id(it)
    if sid in items_by_id:
        return
    items_by_id[sid] = it
    if display_order is not None:
        display_order.append(sid)
    for k in _item_keys(it):
        known_keys.add(k)
    known_keys.add(sid)


def _filter_new_items(
    incoming: List[Any],
    items_by_id: Dict[str, Any],
    known_keys: set,
) -> List[Any]:
    out = []
    for it in incoming:
        if not isinstance(it, dict):
            continue
        sid = _stable_item_id(it)
        if sid in items_by_id:
            continue
        keys = _item_keys(it)
        if keys and any(k in known_keys for k in keys):
            continue
        if not keys and sid in known_keys:
            continue
        out.append(it)
    return out


def _dedup_against_existing(existing: List[Any], incoming: List[Any]) -> List[Any]:
    if not existing:
        return list(incoming)
    known_keys: set = set()
    items_by_id: Dict[str, Any] = {}
    for it in existing:
        if isinstance(it, dict):
            _register_item(it, items_by_id, [], known_keys)
    return _filter_new_items(incoming, items_by_id, known_keys)


def _item_keys(it: Dict[str, Any]) -> List[str]:
    if not isinstance(it, dict):
        return []
    keys: List[str] = []

    tid = it.get("tmdb_id")
    if tid:
        keys.append(f"tmdb:{tid}")

    year = it.get("year")
    orig = _norm(it.get("original_title") or "")
    if orig:
        keys.append(f"orig:{orig}|{year or ''}")

    base = _norm(it.get("base_title") or "")
    if base:
        keys.append(f"base:{base}|{year or ''}")
        keys.append(f"base:{base}|")

    sname = _norm(it.get("series_name") or "")
    if sname:
        keys.append(f"series:{sname}")

    loc = _norm(it.get("title_localized") or "")
    if loc and loc != base:
        keys.append(f"loc:{loc}|{year or ''}")

    if not keys:
        title = _norm(it.get("title") or "")
        if title:
            keys.append(f"fb:{title}|{year or ''}")

    return keys


def _item_key(it: Dict[str, Any]) -> str:
    keys = _item_keys(it)
    return keys[0] if keys else ""
