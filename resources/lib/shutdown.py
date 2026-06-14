# -*- coding: utf-8 -*-
"""
shutdown.py
-----------
v0.0.64: Globalni Kodi shutdown coordinator.

PROC EXISTUJE:
    Pri vypinani Kodi (zavreni okna, Quit, restart) Kodi posila plugin
    procesum 'abort' signal. Python interpreter ale ceka, az dobehnou
    vsechny daemon threads. Kdyz jsou tyto thready zaseknute v
    urlopen() / socket.recv() syscallu, nelze je preskocit - Kodi
    pak zatuhne na dobu trvani longest pending HTTP timeout.

    Drive (v0.0.63) jsme rozliseni timeouts: image_cache 10s -> 5s,
    Webshare 15s -> 8s. To byl jen pulkruvny fix - na slabych zarizenich
    (Xbox One, RPi3) je i 5s na shutdown frustrujuce dlouhe.

JAK TO RESI:

    1) Background watcher thread cti xbmc.Monitor.waitForAbort(timeout=1).
       Jakmile Kodi posle abort signal, watcher set _shutdown_event a
       zavola vsechny registrovane handlery.

    2) Daemon threads (image workers, prefetch, ...) si pri kazde
       iteraci checknou is_shutting_down(). Kdyz True - return ihned,
       nezacinaji novy urlopen().

    3) Network timeouty jsou kratke (image 3s, Webshare default 8s,
       subtitles 8s misto puvodnich 25s) - i kdyz thread visi v urlopen()
       v okamzik abortu, nejvyse 3-8s wait.

POUZITI:

    from . import shutdown

    # V router.route() na zacatku:
    shutdown.start()

    # V daemon thread loopu:
    while not shutdown.is_shutting_down():
        ...

    # Modul s vlastnimi cleanup operacemi:
    shutdown.register(my_cleanup_function)
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, List

log = logging.getLogger("klempcinema.shutdown")

_shutdown_event = threading.Event()
_handlers: List[Callable[[], None]] = []
_handlers_lock = threading.Lock()
_started = False
_started_lock = threading.Lock()


def is_shutting_down() -> bool:
    """True pokud Kodi signaloval abort (vypina se)."""
    return _shutdown_event.is_set()


def register(handler: Callable[[], None]) -> None:
    """Zaregistruj cleanup callback. Bude zavolan az Kodi posle abort.

    Handler musi byt rychly (< 100ms) - nedelat zadne network I/O,
    jen prepnout flag. Vyjimka v handleru se zaloguje a ignoruje.
    """
    if handler is None:
        return
    with _handlers_lock:
        if handler not in _handlers:
            _handlers.append(handler)


def wait(timeout: float) -> bool:
    """Sleep do timeout sekund nebo do abort signalu (co prijde drive).

    Vraci True pokud byl abort signalovan (cyklus ma skoncit), jinak False.
    """
    return _shutdown_event.wait(timeout=timeout)


def start() -> None:
    """Spustime watcher thread (idempotent)."""
    global _started
    with _started_lock:
        if _started:
            return
        _started = True

    t = threading.Thread(
        target=_watcher_loop,
        name="klempcinema-shutdown-watcher",
        daemon=True,
    )
    t.start()
    log.debug("shutdown: watcher started")


def _watcher_loop() -> None:
    """Sleduje xbmc.Monitor.abortRequested. Pri abortu nastavuje
    _shutdown_event a vola handlery.
    """
    try:
        import xbmc  # type: ignore
        monitor = xbmc.Monitor()
    except Exception as exc:  # noqa: BLE001
        log.debug("shutdown: xbmc.Monitor unavailable (%s) - watcher off", exc)
        return

    # Cekame jednotlive 1-sekundove sloty - pri abortu waitForAbort vrati
    # True ihned a my muzeme oznamit shutdown.
    while True:
        try:
            aborted = monitor.waitForAbort(1)
        except Exception:  # noqa: BLE001
            # Monitor uz neexistuje (interpreter unloading) - chovejme se
            # jako abort, at moduly dostanou pulse.
            aborted = True
        if aborted:
            break

    log.info("shutdown: Kodi abort detected - notifying handlers")
    _shutdown_event.set()
    with _handlers_lock:
        handlers = list(_handlers)
    for h in handlers:
        try:
            h()
        except Exception as exc:  # noqa: BLE001
            log.debug("shutdown: handler %s failed: %s", h, exc)


def force_set() -> None:
    """v0.0.64: testing helper - manualne signaluj shutdown.
    NEPOUZIVAT v produkcnim kodu pluginu.
    """
    _shutdown_event.set()
