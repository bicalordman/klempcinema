# -*- coding: utf-8 -*-
"""
plugin.py
---------
Hlavní vstupní bod Kodi pluginu KlempCinema.

Kodi spouští plugin s parametry:
    sys.argv[0] = "plugin://plugin.video.klempcinema/"
    sys.argv[1] = handle (int)
    sys.argv[2] = "?action=...&id=..." (query string)

Tento soubor pouze:
    1) přidá resources/lib do sys.path,
    2) rozparsuje query string z sys.argv[2],
    3) předá parametry routeru (resources.lib.router.route).
"""

from __future__ import annotations

import logging
import os
import sys
from urllib.parse import parse_qsl

import xbmcaddon  # type: ignore


# ---------------------------------------------------------------------------
# Bootstrap – import path pro resources/lib
# ---------------------------------------------------------------------------

_ADDON = xbmcaddon.Addon()
_ADDON_PATH = _ADDON.getAddonInfo("path")
_LIB_PATH = os.path.join(_ADDON_PATH, "resources", "lib")
# Kořen addonu musí být na sys.path kvůli ``from resources.lib import …``.
if _ADDON_PATH not in sys.path:
    sys.path.insert(0, _ADDON_PATH)
if _LIB_PATH not in sys.path:
    sys.path.insert(0, _LIB_PATH)


# ---------------------------------------------------------------------------
# Logging – nasměrujeme do Kodi logu
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="[KlempCinema] %(name)s: %(message)s",
)
log = logging.getLogger("klempcinema.plugin")


# ---------------------------------------------------------------------------
# Parsování parametrů
# ---------------------------------------------------------------------------

def _parse_params(qs: str) -> dict:
    """Z '?action=root&id=42' udělá {'action': 'root', 'id': '42'}."""
    if qs.startswith("?"):
        qs = qs[1:]
    return dict(parse_qsl(qs, keep_blank_values=True))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    qs = sys.argv[2] if len(sys.argv) > 2 else ""
    params = _parse_params(qs)
    log.debug("Spouštím s parametry: %s", params)

    # Periodický cleanup stale cache (1x denně, no-op jinak).
    # Smaže soubory starší než 30 dnů - odstraní sirotky po bump
    # cache klíčů (např. tmdb:movie: -> tmdb:movie:v2:).
    try:
        from resources.lib import cache as _cache
        _cache.maybe_cleanup_expired(min_interval_hours=24, max_age_days=30)
    except Exception as exc:  # noqa: BLE001
        log.debug("cache cleanup selhal: %s", exc)

    try:
        from resources.lib import router
        router.route(params)
    except Exception as exc:  # noqa: BLE001
        log.exception("KlempCinema plugin selhal: %s", exc)
        try:
            import xbmcgui  # type: ignore
            xbmcgui.Dialog().ok(
                "KlempCinema",
                f"Chyba pluginu:\n{exc}\n\nZkontroluj kodi.log (KlempCinema).",
            )
        except Exception:  # noqa: BLE001
            pass
    finally:
        try:
            from resources.lib import lifecycle as _lifecycle
            _lifecycle.on_plugin_exit()
        except Exception as exc:  # noqa: BLE001
            log.debug("plugin exit cleanup: %s", exc)


if __name__ == "__main__":
    main()
