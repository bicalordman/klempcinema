# -*- coding: utf-8 -*-
"""
player_tracker.py
-----------------
Sledování přehrávání pro funkci "Pokračovat ve sledování". Spouští se po
setResolvedUrl jako daemon thread; každých SAVE_EVERY sekund uloží
pozici/duration do watched.json. Když user zastaví / dohraje, persistne
naposled.

Bez nutnosti samostatného service entry-pointu - vše se děje v rámci
běžícího pluginu (Kodi nás drží naživu dokud playback běží).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict

import xbmc  # type: ignore

from . import watched

log = logging.getLogger("klempcinema.player_tracker")

# Jak často ukládáme pozici během přehrávání.
SAVE_EVERY = 10  # sekund
# Kolik sekund čekáme po startu, než začneme sledovat (Kodi se rozjíždí).
INITIAL_WAIT = 5
# Maximum doba sledování bez začátku přehrávání - bezpečnostní brake.
MAX_WAIT_FOR_PLAYER = 30
# Když Player.isPlayingVideo() vrátí False víc cyklů, ukončíme sledování.
STOP_AFTER_N_NOT_PLAYING = 3

_active_lock = threading.Lock()
_active_id: str = ""


def start_tracking(file_id: str, meta: Dict[str, Any]) -> None:
    """
    Spustí daemon thread, který bude sledovat aktuální Kodi Player a každých
    SAVE_EVERY sekund ukládat pozici/duration pod file_id.

    Pokud už nějaký tracker běží, neuvozujeme nový (Kodi má vždy 1 playback).
    """
    global _active_id
    if not file_id:
        return
    with _active_lock:
        if _active_id:
            log.debug("tracker už běží pro %s, ignoruji %s", _active_id, file_id)
            return
        _active_id = file_id

    t = threading.Thread(
        target=_run,
        args=(file_id, dict(meta or {})),
        name=f"player-tracker-{file_id}",
        daemon=True,
    )
    t.start()
    log.info("player_tracker: spuštěn pro %s (%s)",
             file_id, meta.get("title") or "")


def _run(file_id: str, meta: Dict[str, Any]) -> None:
    global _active_id
    try:
        player = xbmc.Player()
        monitor = xbmc.Monitor()

        # 1) Čekáme, až Kodi opravdu začne hrát
        waited = 0
        while waited < MAX_WAIT_FOR_PLAYER:
            if monitor.waitForAbort(1):
                return
            if player.isPlayingVideo():
                break
            waited += 1
        else:
            log.debug("player_tracker: playback nezačal do %ds, končím",
                      MAX_WAIT_FOR_PLAYER)
            return

        # 2) Initial wait, aby Kodi nahlásil reálnou duration
        if monitor.waitForAbort(INITIAL_WAIT):
            return

        not_playing_count = 0
        last_position = 0.0
        last_duration = 0.0

        while True:
            if monitor.waitForAbort(SAVE_EVERY):
                # Kodi se zavírá - uložíme naposled a končíme.
                if last_duration > 0:
                    watched.save_progress(
                        file_id,
                        position=last_position,
                        duration=last_duration,
                        **meta,
                    )
                return

            try:
                playing = player.isPlayingVideo()
            except Exception:  # noqa: BLE001
                playing = False

            if not playing:
                not_playing_count += 1
                if not_playing_count >= STOP_AFTER_N_NOT_PLAYING:
                    # User skončil - uložíme finálku.
                    if last_duration > 0:
                        watched.save_progress(
                            file_id,
                            position=last_position,
                            duration=last_duration,
                            **meta,
                        )
                    log.info("player_tracker: konec (final pos=%.1f, dur=%.1f)",
                             last_position, last_duration)
                    return
                continue
            not_playing_count = 0

            try:
                pos = float(player.getTime() or 0)
                dur = float(player.getTotalTime() or 0)
            except Exception:  # noqa: BLE001
                continue

            if dur <= 0:
                continue
            last_position = pos
            last_duration = dur

            watched.save_progress(
                file_id,
                position=pos,
                duration=dur,
                **meta,
            )
    except Exception as exc:  # noqa: BLE001
        log.exception("player_tracker selhal: %s", exc)
    finally:
        with _active_lock:
            if _active_id == file_id:
                _active_id = ""
