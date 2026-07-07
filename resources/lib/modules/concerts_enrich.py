# -*- coding: utf-8 -*-
"""TMDB enrich pro rubriku Koncerty (jen aktualni stranka)."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

log = logging.getLogger("klempcinema.concerts.enrich")


def enrich_concert_items(items: List[Dict[str, Any]]) -> None:
    """Obohati jen predanou stranku – bez CSFD (rychle, stabilni)."""
    if not items:
        return

    try:
        from .. import api_webshare as ws
        ws._enrich_in_parallel(items, kind="movie", skip_csfd=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("enrich_concert_items selhalo: %s", exc)
