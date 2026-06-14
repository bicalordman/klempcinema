# -*- coding: utf-8 -*-
"""
kodi_compat.py
--------------
Izoluje zavislost na xbmc* modulech. Mimo Kodi (unit testy, lokalni
spousteni) padaji volani hezky na fallback misto crashe.

Pouziti:
    from .kodi_compat import addon_safe, profile_dir, get_setting

PUBLIC API:
    addon_safe()             -> xbmcaddon.Addon | None
    profile_dir()            -> str (cesta k addon profile)
    addon_path()             -> str (cesta k addon installu)
    translate(path)          -> str (xbmcvfs.translatePath fallback)

    get_setting(key, default="")     -> str
    get_setting_int(key, default)    -> int
    get_setting_bool(key, default)   -> bool

    notify(msg, time_ms=4000, level="info")
    log_info(msg) / log_warning(msg) / log_error(msg)
"""

from __future__ import annotations

import logging
import os
from typing import Optional

log = logging.getLogger("klempcinema.kodi_compat")


# ---------------------------------------------------------------------------
# Addon objekt + cesty
# ---------------------------------------------------------------------------

def addon_safe():
    """Vrati xbmcaddon.Addon nebo None mimo Kodi."""
    try:
        import xbmcaddon  # type: ignore
        return xbmcaddon.Addon()
    except Exception:  # noqa: BLE001
        return None


def translate(path: str) -> str:
    """xbmcvfs.translatePath fallback - vraci path bezezmen mimo Kodi."""
    if not path:
        return ""
    try:
        import xbmcvfs  # type: ignore
        return xbmcvfs.translatePath(path)
    except Exception:  # noqa: BLE001
        return path


def profile_dir() -> str:
    """Cesta k user-data adresari pluginu (cache, watched, history)."""
    a = addon_safe()
    if a is not None:
        try:
            return translate(a.getAddonInfo("profile"))
        except Exception:  # noqa: BLE001
            pass
    return os.path.join(os.path.expanduser("~"), ".klempcinema")


def addon_path() -> str:
    """Cesta k install adresari pluginu (resources/, icons/)."""
    a = addon_safe()
    if a is not None:
        try:
            return translate(a.getAddonInfo("path"))
        except Exception:  # noqa: BLE001
            pass
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# Settings - typove safe wrappery
# ---------------------------------------------------------------------------

def get_setting(key: str, default: str = "") -> str:
    """Setting jako string. Whitespace ostrihnuty, prazdne = default."""
    a = addon_safe()
    if a is None:
        return default
    try:
        raw = (a.getSetting(key) or "").strip()
        return raw if raw else default
    except Exception:  # noqa: BLE001
        return default


def get_setting_int(key: str, default: int) -> int:
    """Setting jako int. Pri parse error vraci default."""
    raw = get_setting(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def get_setting_bool(key: str, default: bool = False) -> bool:
    """Setting jako bool. true/1 = True, false/0 = False, jinak default."""
    raw = get_setting(key, "").lower()
    if raw in ("true", "1"):
        return True
    if raw in ("false", "0"):
        return False
    return default


def set_setting(key: str, value: str) -> bool:
    """Nastavi setting. Vraci True pri uspechu."""
    a = addon_safe()
    if a is None:
        return False
    try:
        a.setSetting(key, value)
        return True
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def notify(message: str, time_ms: int = 4000, level: str = "info",
           title: str = "KlempCinema") -> None:
    """
    Notifikace v rohu obrazovky.

    :param level: "info" / "warning" / "error"
    """
    try:
        import xbmcgui  # type: ignore
        icon_map = {
            "info":    xbmcgui.NOTIFICATION_INFO,
            "warning": xbmcgui.NOTIFICATION_WARNING,
            "error":   xbmcgui.NOTIFICATION_ERROR,
        }
        icon = icon_map.get(level, xbmcgui.NOTIFICATION_INFO)
        xbmcgui.Dialog().notification(title, message, icon, time_ms)
    except Exception as exc:  # noqa: BLE001
        log.info("notify(%s): %s", level, message)
        log.debug("notify backend failed: %s", exc)
