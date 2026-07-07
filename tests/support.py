# -*- coding: utf-8 -*-
"""Bootstrap pro unit testy mimo Kodi (mock xbmc, sys.path)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "resources"


def install_kodi_stubs() -> None:
    for name in ("xbmc", "xbmcaddon", "xbmcgui", "xbmcplugin", "xbmcvfs"):
        if name not in sys.modules:
            sys.modules[name] = MagicMock()


def ensure_resources_path() -> None:
    p = str(RES)
    if p not in sys.path:
        sys.path.insert(0, p)


def load_modules():
    """Vrati (api_webshare, clean_title, router_common) po bootstrapu."""
    install_kodi_stubs()
    ensure_resources_path()
    from lib import api_webshare
    from lib import clean_title
    from lib import router_common
    return api_webshare, clean_title, router_common
