# -*- coding: utf-8 -*-
"""
pagination.py
-------------
Centrální pagination logika pro KlempCinema.

PROBLÉM:
    Webshare vrací max 200 souborů per request, ale po filtrech
    (exclude_series, year_range, dedupe, only_with_poster, kids_only_czech)
    zbude variabilní počet (5/10/4...).
    Uživatel pak vidí nekonzistentní listingy.

ŘEŠENÍ:
    UI_PAGE_SIZE = 20 - pevný target na UI stránku.

    Funkce paginate_category() spustí "fetcher" (např. get_movies_raw_page())
    opakovaně dokud nemá v bufferu aspoň ui_page * 20 + buffer položek
    nebo dokud nedojde Webshare obsah.

    Buffer agregovaných položek je cachovaný (per query+sort+rubrika)
    na 30 minut - umožňuje rychlé stránkování bez re-fetch každé stránky.

PUBLIC API:
    UI_PAGE_SIZE                                                -> int (20)
    paginate(items, ui_page)                                    -> list
    paginate_with_fetcher(cache_key, fetcher, ui_page,
                          max_ws_pages=10)                      -> (items, has_more)
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import cache

log = logging.getLogger("klempcinema.pagination")

# Default počet položek na UI stránku. Pokud user změní v Settings
# (items_per_page slider), tato hodnota se přepíše.
UI_PAGE_SIZE_DEFAULT = 50


def _read_ui_page_size() -> int:
    """Načti items_per_page z settings (default 50, range 10..100)."""
    try:
        import xbmcaddon  # type: ignore
        addon = xbmcaddon.Addon()
        raw = addon.getSetting("items_per_page") or str(UI_PAGE_SIZE_DEFAULT)
        n = int(raw)
        # Bezpečné meze
        if n < 10:
            return 10
        if n > 100:
            return 100
        return n
    except Exception:  # noqa: BLE001
        return UI_PAGE_SIZE_DEFAULT


# Vystavujeme i jako modul-level konstantu pro zpětnou kompatibilitu.
# Hodnota je dynamicky čtena při každém volání paginate_*.
UI_PAGE_SIZE = UI_PAGE_SIZE_DEFAULT

# Jak dlouho držet agregovaný buffer položek v cache.
# v0.0.48: 10min -> 30min. Po prvnim otevreni se rubrika cachuje na pul
# hodiny - dalsi otevreni je INSTANT (zadne WS+TMDB requesty). Trade-off:
# nove uploady na WS se objevi az po 30min. Pro user-friendly rychlost
# je to lepsi nez 10min cache + pomale opetovne nacitani.
AGG_TTL = 30 * 60  # 30 minut

# Bezpečnostní strop - kolik Webshare stránek max fetchnout pro jednu rubriku.
# v0.0.48: 10 -> 5 (rychlejsi first load).
# v0.0.62: 5 -> 4 (jeste rychlejsi first load na slabsich platformach
# jako Xbox. Pri Dalsi-stranka se fetchne dalsich 4. Plus uz mame v0.0.61
# prefetch.schedule = dalsi stranka casto cachovana driv nez user klikne).
DEFAULT_MAX_WS_PAGES = 4


def paginate(items: List[Any], ui_page: int) -> List[Any]:
    """Vrátí výsek items[(ui_page-1)*size : ui_page*size]."""
    if not items:
        return []
    size = _read_ui_page_size()
    start = max(0, (max(1, ui_page) - 1) * size)
    end = start + size
    return items[start:end]


def has_more_pages(total_items: int, ui_page: int) -> bool:
    """True pokud po této UI stránce zbývají další položky."""
    return total_items > ui_page * _read_ui_page_size()


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

    Strategie:
      1) Načti agregovaný buffer z cache (klíč 'cache_key').
      2) Pokud buffer < (ui_page+1) * page_size, dofetchni další WS stránky.
      3) Po každém fetchnutí setřiď celý buffer (pokud sort_key).
      4) Persist state. Vrať items[(ui_page-1)*size : ui_page*size] + has_more.

    Fetcher protokol:
      - vrátí None    -> Webshare opravdu došel (exhausted)
      - vrátí []      -> Webshare dal soubory, ale po filtrech 0 položek
                         (try next WS page, NEzastavovat)
      - vrátí [items] -> přidat do bufferu

    Tím se ošetří situace, kdy 1 WS strana (200 souborů) je celá vyfiltrovaná
    a uživatel by jinak viděl "obsah nenalezen" než se klikne na další stranu.

    :param sort_key: callable(item) -> klíč pro sort celého bufferu.
                     Když None, buffer zůstává v pořadí přidání (per-page sort).
    :param max_ws_pages: bezpečnostní strop fetchů (default 10).
    :param ttl_override: vlastní TTL v sekundách. None = AGG_TTL (30 min).
                         v0.0.63: rubrika 'Novinky' pouziva 600s (10 min),
                         protoze tam zalezi na cerstvosti vic nez na rychlosti.
    """
    page_size = _read_ui_page_size()
    effective_ttl = ttl_override if ttl_override is not None else AGG_TTL
    # v0.0.48: zmensen needed buffer - z (ui_page+1)*size na ui_page*size+10.
    # Drive jsme fetchovali navic CELOU jednu stranku do bufferu pro pripadne
    # Dalsi-stranka kliky. Ted: jen 10 polozek navic (rychlejsi first load).
    # Pokud user da Dalsi stranka, prefetch.schedule() uz fetchne dalsi
    # na pozadi, takze klik bude stejne instant.
    needed = ui_page * page_size + 10

    # 1) Načti uložený buffer + state
    state = cache.cache_get(cache_key, ttl=effective_ttl) or {}
    items: List[Any] = list(state.get("items") or [])
    next_ws_page: int = int(state.get("next_ws_page") or 1)
    exhausted: bool = bool(state.get("exhausted"))

    fetched_now = 0

    # 2) Dofetch dokud nemáme dost položek (nebo Webshare opravdu nedojde)
    while len(items) < needed and not exhausted and fetched_now < max_ws_pages:
        log.debug("paginate(%s): fetch WS page %d (have=%d, need=%d)",
                  cache_key, next_ws_page, len(items), needed)
        try:
            new_items = fetcher(next_ws_page)
        except Exception as exc:  # noqa: BLE001
            log.exception("paginate fetcher selhal: %s", exc)
            new_items = None

        # None = WS opravdu došel. Skutečně vyčerpáno.
        if new_items is None:
            exhausted = True
            log.debug("paginate(%s): WS exhausted at ws_page=%d (items=%d)",
                      cache_key, next_ws_page, len(items))
            break

        # [] = WS dal něco, ale po filtrech 0. Pokračujeme dál.
        next_ws_page += 1
        fetched_now += 1

        if new_items:
            # Dedup mezi WS stránkami (podle tmdb_id nebo title)
            added = _dedup_against_existing(items, new_items)
            items.extend(added)
            log.debug("paginate(%s): WS page %d -> +%d items (total=%d)",
                      cache_key, next_ws_page - 1, len(added), len(items))

    # 3) Globální sort celého bufferu (pokud sort_key zadán)
    if sort_key and items:
        try:
            items.sort(key=sort_key)
        except Exception as exc:  # noqa: BLE001
            log.debug("paginate sort selhal: %s", exc)

    # 4) Persist state
    cache.cache_set(cache_key, {
        "items": items,
        "next_ws_page": next_ws_page,
        "exhausted": exhausted,
    })

    # 5) Slice + has_more
    page_items = paginate(items, ui_page)
    has_more = (not exhausted) or len(items) > ui_page * page_size

    # Jeden info log per page request (ne per fetch) - pro orientacni
    # diagnostiku v Kodi logu, bez zahlceni.
    log.info("paginate(%s): ui_page=%d -> %d items, has_more=%s "
             "(buf=%d, fetched_now=%d)",
             cache_key, ui_page, len(page_items), has_more,
             len(items), fetched_now)
    return page_items, has_more


def invalidate(cache_key: str) -> None:
    """Smaž agregovaný buffer pro danou rubriku (např. po změně settings)."""
    cache.cache_set(cache_key, None)


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _dedup_against_existing(existing: List[Any], incoming: List[Any]) -> List[Any]:
    """
    Vrátí podmnožinu 'incoming', která ještě není v 'existing'.

    Položka může mít VÍC klíčů (tmdb_id + original_title+year + base_title)
    a všechny se ukládají do seen sady. Tím se odchytí situace, kdy stránka 1
    má položku jen s base_title a stránka 2 stejný film s tmdb_id - dedup
    by jinak je dva zachytil.
    """
    if not existing:
        return list(incoming)

    seen_keys: set = set()
    for it in existing:
        for k in _item_keys(it):
            seen_keys.add(k)

    out = []
    for it in incoming:
        keys = _item_keys(it)
        if not keys:
            out.append(it)
            continue
        if any(k in seen_keys for k in keys):
            continue
        for k in keys:
            seen_keys.add(k)
        out.append(it)
    return out


def _item_keys(it: Dict[str, Any]) -> List[str]:
    """
    Sada klíčů pro cross-page dedup. Vrací VÍCE klíčů per item, aby dedup
    sjednotil i situace, kdy položka nemá vždy stejný identifikátor.

    Priorita silnosti:
        1) tmdb_id              (nejstabilnější napříč WS variantami)
        2) original_title+year  (en title - stejný i pro cs varianty)
        3) base_title           (cleaned filename - může se lišit)
        4) series_name          (pro seriály)
        5) title_localized      (TMDB cs title - může se měnit po enrichi)
    """
    if not isinstance(it, dict):
        return []
    keys: List[str] = []

    tid = it.get("tmdb_id")
    if tid:
        keys.append(f"tmdb:{tid}")

    year = it.get("year")
    orig = (it.get("original_title") or "").strip().lower()
    if orig:
        if year:
            keys.append(f"orig:{orig}|{year}")
        else:
            keys.append(f"orig:{orig}")

    base = (it.get("base_title") or "").strip().lower()
    if base:
        if year:
            keys.append(f"base:{base}|{year}")
        else:
            keys.append(f"base:{base}")

    sname = (it.get("series_name") or "").strip().lower()
    if sname:
        keys.append(f"series:{sname}")

    loc = (it.get("title_localized") or "").strip().lower()
    if loc and loc != base:
        if year:
            keys.append(f"loc:{loc}|{year}")
        else:
            keys.append(f"loc:{loc}")

    return keys


# Zpětně kompatibilní helper (pokud někdo volá _item_key)
def _item_key(it: Dict[str, Any]) -> str:
    keys = _item_keys(it)
    return keys[0] if keys else ""
