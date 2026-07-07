# -*- coding: utf-8 -*-
"""
prefetch.py
-----------
Background předfetch další stránky rubrik - jakmile user otevře page N,
spustíme thread, který naplní cache pro page N+1. Klik na "Další strana"
pak bude instant.

Bezpečnostní pravidla:
    - Spouštíme JEN, pokud has_more=True (jinak je další strana zbytečná).
    - Spouštíme JEN, pokud page < MAX_PREFETCH_PAGE (default 20).
    - Současně může běžet jen JEDEN prefetch task per cache_key
      (tracking přes _active set).
    - Daemon thread - nezdrží shutdown Kodi.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from . import shutdown as _shutdown

try:
    from . import lifecycle as _lifecycle
except Exception:  # noqa: BLE001
    _lifecycle = None  # type: ignore

log = logging.getLogger("klempcinema.prefetch")

MAX_PREFETCH_PAGE = 12  # omezeni pozadi pri dlouhem prochazeni

# Tracking aktivních prefetch tasků (cache_key -> True)
_active: dict = {}
_active_lock = threading.Lock()


def schedule(
    cache_key: str,
    fetcher: Callable[[], None],
    page: int,
    has_more: bool = True,
) -> None:
    """
    Naplánuj fetch další stránky na pozadí.

    :param cache_key: unikátní klíč (rubrika+sort+query+page) - prevent duplicate
    :param fetcher:   callable bez argumentů - typicky lambda volající
                      api_webshare.get_*(page=page+1)
    :param page:      aktuální page (next bude page+1)
    :param has_more:  pokud False, nic neděláme
    """
    if not has_more:
        return
    if page >= MAX_PREFETCH_PAGE:
        log.debug("prefetch skipped (page=%d >= %d)", page, MAX_PREFETCH_PAGE)
        return

    with _active_lock:
        if cache_key in _active:
            log.debug("prefetch už běží pro %s", cache_key)
            return
        _active[cache_key] = True

    t = threading.Thread(
        target=_run,
        args=(cache_key, fetcher),
        name=f"prefetch-{cache_key[:30]}",
        daemon=True,
    )
    t.start()
    log.info("prefetch: spuštěn pro %s (page=%d->%d)", cache_key, page, page + 1)


def _run(cache_key: str, fetcher: Callable[[], None]) -> None:
    """v0.0.64: pred fetcher() check shutdown. Pokud Kodi posle abort
    pred spustenim fetche, ihned konec - nepustime urlopen() ktery by
    blokoval Python shutdown.
    """
    try:
        if _shutdown.is_shutting_down():
            log.debug("prefetch %s skip - Kodi abort", cache_key)
            return
        if _lifecycle and _lifecycle.is_plugin_exiting():
            log.debug("prefetch %s skip - plugin exit", cache_key)
            return
        fetcher()
        log.info("prefetch: hotovo (%s)", cache_key)
    except Exception as exc:  # noqa: BLE001
        log.debug("prefetch %s selhal: %s", cache_key, exc)
    finally:
        with _active_lock:
            _active.pop(cache_key, None)
