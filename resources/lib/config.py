# -*- coding: utf-8 -*-
"""
config.py
---------
Centralni konfigurace KlempCinema (v0.0.53). Jediny zdroj pravdy pro
vsechny konstanty driv rozhazene v 7+ modulech.

Sekce:
    HTTP      - timeouty, user-agent
    CACHE     - TTL pro ruzne typy dat
    ENRICH    - workers pro paralelni TMDB/CSFD enrich
    PAGE      - velikosti stranek, paginace limity
    QUALITY   - prahove skore pro quality filtry

Pouziti:
    from .config import HTTP, CACHE, ENRICH, PAGE, QUALITY
    timeout = HTTP.webshare_timeout
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HttpConfig:
    """HTTP timeouty (sekundy)."""
    webshare_timeout: int = 15
    tmdb_timeout: int = 6
    csfd_timeout: int = 6
    opensubs_timeout: int = 25
    image_dl_timeout: int = 10
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )


@dataclass(frozen=True)
class CacheConfig:
    """TTL v sekundach pro ruzne cache vrstvy."""
    # TMDB
    tmdb_hit_ttl: int = 30 * 86400          # 30 dni - meta filmu se meni ridke
    tmdb_empty_ttl: int = 3 * 3600          # 3h - mene znovu-hledani pro misses
    tmdb_details_ttl: int = 30 * 86400
    tmdb_self_test_ttl: int = 5 * 60        # 5 min - quick re-test po site obnovi

    # CSFD
    csfd_hit_ttl: int = 7 * 86400
    csfd_blocked_ttl: int = 5 * 60          # cloudflare cooldown

    # Aggregator (rubrika buffer)
    aggregator_ttl: int = 30 * 60           # 30 min - druhe otevreni instantni

    # Images
    images_max_bytes: int = 200 * 1024 * 1024
    image_cache_cleanup_every_n: int = 50

    # Disk cleanup
    cleanup_min_interval_hours: int = 24
    cleanup_max_age_days: int = 30


@dataclass(frozen=True)
class EnrichConfig:
    """Paralelizace TMDB/CSFD enrichmentu."""
    workers_tmdb: int = 6           # bezpecne pod TMDB rate-limitem 40/s
    workers_csfd: int = 2           # CSFD anti-bot citlivy
    max_failures_session: int = 3   # po N selhanich TMDB / CSFD se vypne


@dataclass(frozen=True)
class PaginationConfig:
    """Velikosti UI stranek a paginace limity."""
    ui_page_default: int = 50
    ui_page_min: int = 10
    ui_page_max: int = 100

    max_ws_pages_default: int = 5   # bezne rubriky
    max_ws_pages_filtered: int = 8  # 4K/BluRay/newdub/latest (po pre-filter rychle)
    max_prefetch_page: int = 20
    max_router_page: int = 50

    webshare_page_limit: int = 200  # WS API max items per request


@dataclass(frozen=True)
class QualityConfig:
    """Prahove skore pro quality filtry (z _quality_score)."""
    min_4k: int = 1000              # 2160p / UHD / 4K
    min_1080p: int = 800            # FullHD a vys
    min_720p: int = 400             # HD a vys


# Singleton instance - import sem misto vyrabeni stale dokola
HTTP = HttpConfig()
CACHE = CacheConfig()
ENRICH = EnrichConfig()
PAGE = PaginationConfig()
QUALITY = QualityConfig()
