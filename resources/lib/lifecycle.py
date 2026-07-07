# -*- coding: utf-8 -*-
"""
lifecycle.py
------------
Uklid po kazde invokaci pluginu (Kodi casto drzi jeden Python interpreter).

Bez uklidu zustavaji image-cache workery a roste RAM cache -> po desitkach
minut muze Kodi spadnout nebo se vypnout.
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger("klempcinema.lifecycle")

_plugin_exit_handlers = []


def register_plugin_exit(handler) -> None:
    if handler and handler not in _plugin_exit_handlers:
        _plugin_exit_handlers.append(handler)


_exiting = threading.Event()


def is_plugin_exiting() -> bool:
    return _exiting.is_set()


def on_plugin_exit() -> None:
    """Rychly uklid (<200ms) pred ukoncenim plugin.py – nevolat sit."""
    _exiting.set()
    try:
        for h in list(_plugin_exit_handlers):
            try:
                h()
            except Exception as exc:  # noqa: BLE001
                log.debug("plugin exit handler %s: %s", h, exc)
        try:
            from . import cache as _cache
            _cache.trim_memory_cache()
        except Exception as exc:  # noqa: BLE001
            log.debug("trim_memory_cache: %s", exc)
    finally:
        _exiting.clear()
