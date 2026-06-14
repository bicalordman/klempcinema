# -*- coding: utf-8 -*-
"""
voyo.py
-------
v0.0.67: Discovery scraper pro VOYO (SK katalog - voyo.markiza.sk).

PROC TENTO ZDROJ:

    User chce discoverovat slovenske/ceske Voyo-exclusive pořady (typu
    'Ruža pre nevestu', 'Survivor', 'Love Island', 'Farmar hleda zenu')
    a hned je hledat na Webshare. TMDB tieto niche reality show vetsinou
    nezná (vote_count=0) nebo nezná providery (Voyo CZ uz neni v TMDB
    pro region CZ od r. 2024).

    Voyo.cz se v 03/2025 prejmenovalo na Oneplay.cz, ALE:
      - voyo.nova.cz redirectuje na oneplay.cz (JS-only app, neda se scrapovat)
      - oneplay.cz je Nuxt.js SPA - katalog je za XHR + auth tokenem
      - voyo.markiza.sk je STALE LIVE jako SSR (server-side render)
        s plnym HTML katalogem + Voyo brandingem.

    Voyo SK ma stejny obsahovy fond jako Voyo CZ (jeden CME holding),
    + pridava slovenske exklusivity (relacie Markizy). Pro nase ucely
    je to perfektni "discovery" zdroj.

ZDROJ DAT:

    https://voyo.markiza.sk/relacie  - pořady (reality, talk, sport, ...)
    https://voyo.markiza.sk/serialy  - seriály (drama, krimi, telenovely)
    https://voyo.markiza.sk/filmy    - filmy (per zánr)

    Kazda stranka obsahuje 5-20 carouselov, kazdy 5-50 tiles. Total ~500
    polozek per stranka. SSR HTML, ~500-2000 KB.

PARSER:

    Tile struktura v HTML:
        <div class="swiper-slide"
             data-tracking-tile-show-id="6046"
             data-tracking-tile-name="Ruža pre nevestu"
             data-tracking-carousel-name="Obľúbené na Voyo">
            <article class="c-video-box" data-resource="show.6046">
                <div class="img"><img data-src="..."></div>
                <div class="content"><h3 class="title">
                    <a href="https://voyo.markiza.sk/relacie/6046-ruza-pre-nevestu">
                        Ruža pre nevestu
                    </a>
                </h3></div>
            </article>
        </div>

    Vytahujem:
      - show_id   (data-tracking-tile-show-id, unique)
      - title     (data-tracking-tile-name, presny nazev)
      - carousel  (data-tracking-carousel-name, kategorie)
      - image     (img data-src, Voyo CDN URL)
      - voyo_url  (anchor href, optional)

PROC GOOGLEBOT UA:

    Voyo.markiza.sk pri normalnim browser UA pushe GDPR consent overlay.
    Googlebot UA obchazi consent (whitelist pro SEO indexovani).
    Stejna technika jako tv_program.py (iDNES). Bezpecne, scrapeme
    verejny katalog (1 req / 6h).

CACHE:

    - HIT cache: 6 hodin per section (relacie/serialy/filmy).
      Voyo katalog se aktualizuje vetsinu kazdym tyznem nebo i mensich
      vzdalenostech, 6h je rozumny kompromis.
    - NEG cache: 30 min pri network/parse fail - nespamujeme Voyo.
    - Manualni "Aktualizovat" tlacitko (UI) -> force_refresh=True
      smaze cache klic pro dany section.

USAGE PATTERN V PLUGINU:

    1) Main menu "Voyo (SK) ›" -> menu_voyo
    2) Menu Voyo:
        - Pořady (Relácie) >       -> voyo_section(section=relacie)
        - Seriály >                -> voyo_section(section=serialy)
        - Filmy >                  -> voyo_section(section=filmy)
        - Aktualizovat
    3) Voyo section: list of carousels (categories) as folders
        - Reality Show (15 polozek) >
        - Stand-up (24 polozek) >
        - ...
    4) Voyo category: list tiles in carousel
        - Ruža pre nevestu  (tile, click -> Webshare search)
        - Survivor          (tile, click -> Webshare search)
"""

from __future__ import annotations

import gzip
import logging
import re
import time
import zlib
from html import unescape
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import cache

log = logging.getLogger("klempcinema.voyo")

BASE_URL = "https://voyo.markiza.sk"
SECTIONS = {
    # section_key -> (url_path, ui_label_cs, ui_label_sk)
    "relacie": ("/relacie", "Pořady / Relácie", "Relácie"),
    "serialy": ("/serialy", "Seriály", "Seriály"),
    "filmy":   ("/filmy",   "Filmy", "Filmy"),
}

# Cache TTL: 6 hodin (Voyo katalog se mení tak raz/tyden).
CACHE_TTL = 6 * 3600

# Negativni cache (parse fail / blocked) - 30 min.
NEG_CACHE_TTL = 30 * 60

CACHE_KEY_PREFIX = "voyo:section:v1"

HTTP_TIMEOUT = 6

# Googlebot UA obchazi GDPR consent overlay (whitelist pro SEO bot).
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; Googlebot/2.1; "
        "+http://www.google.com/bot.html)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "sk-SK,sk;q=0.9,cs;q=0.8,en;q=0.6",
    "Accept-Encoding": "gzip, deflate",
}


# ---------------------------------------------------------------------------
# Parsing regexes
# ---------------------------------------------------------------------------

# Tile structure - capturing wrapper s tracking metadaty + article uvnitr.
# Pouzivame non-greedy '.*?' s re.DOTALL pro multi-line matching.
_TILE_RE = re.compile(
    r'<div\s+class="swiper-slide"\s+'
    r'(?:[^>]*?\s)?'
    r'data-tracking-tile-show-id="(?P<show_id>\d+)"\s+'
    r'(?:[^>]*?\s)?'
    r'data-tracking-tile-name="(?P<title>[^"]+)"\s+'
    r'(?:[^>]*?\s)?'
    r'data-tracking-carousel-name="(?P<carousel>[^"]+)"'
    r'[^>]*>'
    r'(?P<body>.*?)</article>',
    re.IGNORECASE | re.DOTALL,
)

# V tele tile vytahnem img + anchor:
_TILE_IMG_RE = re.compile(
    r'<img[^>]*?data-src="(?P<src>https?://[^"]+)"',
    re.IGNORECASE,
)
# Fallback - normalni 'src' atribut (kdyz lazy-load chybi).
_TILE_IMG_SRC_RE = re.compile(
    r'<img[^>]*?\bsrc="(?P<src>https?://[^"]+)"',
    re.IGNORECASE,
)
_TILE_HREF_RE = re.compile(
    r'<a\s+href="(?P<href>https?://voyo\.markiza\.sk/[^"]+)"',
    re.IGNORECASE,
)

# Charset detector (SK Voyo posiela UTF-8, ale defenziva nikdy nezasko-di).
_META_CHARSET_RE = re.compile(
    rb'<meta[^>]*charset\s*=\s*["\']?([A-Za-z0-9._-]+)',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# HTTP / decoding
# ---------------------------------------------------------------------------

def _detect_encoding(headers_ct: str, raw_bytes: bytes) -> str:
    """Detekuje encoding: Content-Type charset > <meta charset> > utf-8."""
    ct = (headers_ct or "").lower()
    if "charset=" in ct:
        enc = ct.split("charset=", 1)[1].split(";")[0].strip()
        if enc:
            return enc
    head = raw_bytes[:4096]
    m = _META_CHARSET_RE.search(head)
    if m:
        try:
            return m.group(1).decode("ascii", errors="ignore")
        except Exception:  # noqa: BLE001
            pass
    return "utf-8"


def _decode_response(resp: Any) -> str:
    data = resp.read()
    ce = (resp.headers.get("Content-Encoding") or "").lower()
    if "gzip" in ce:
        try:
            data = gzip.decompress(data)
        except Exception as exc:  # noqa: BLE001
            log.debug("voyo gzip decode fail: %s", exc)
    elif "deflate" in ce:
        try:
            data = zlib.decompress(data)
        except Exception:  # noqa: BLE001
            try:
                data = zlib.decompress(data, -zlib.MAX_WBITS)
            except Exception as exc:  # noqa: BLE001
                log.debug("voyo deflate decode fail: %s", exc)

    enc = _detect_encoding(resp.headers.get("Content-Type") or "", data)
    try:
        return data.decode(enc, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


def _http_get(url: str) -> Optional[str]:
    """GET Voyo stranky. None pri chybe (loguje warning)."""
    req = Request(url, headers=HEADERS)
    try:
        with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            html = _decode_response(resp)
            if not html or len(html) < 5000:
                log.warning(
                    "voyo: '%s' vratilo podezrele malou stranku (%d B) - "
                    "mozna redirect / GDPR overlay.",
                    url, len(html or ""))
                return None
            return html
    except HTTPError as exc:
        log.warning("voyo: HTTP %s pri fetch %s", exc.code, url)
        return None
    except URLError as exc:
        log.warning("voyo: network err %s: %s", url, exc.reason)
        return None
    except Exception as exc:  # noqa: BLE001
        log.exception("voyo: fetch fail %s: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _clean(s: str) -> str:
    if not s:
        return ""
    return unescape(re.sub(r"\s+", " ", s)).strip()


def _parse_tiles(html: str) -> List[Dict[str, Any]]:
    """Vrati flat list tile dict (s carousel grouping zachovanym v 'carousel'
    klici), v poradi v jakem se objevi v HTML.

    Tile dict:
        {
          "show_id":  "6046",
          "title":    "Ruža pre nevestu",
          "carousel": "Obľúbené na Voyo",
          "image":    "https://cmesk-ott-images...",
          "url":      "https://voyo.markiza.sk/relacie/6046-ruza-pre-nevestu",
        }
    """
    if not html:
        return []

    out: List[Dict[str, Any]] = []
    seen_show_ids_per_carousel: Dict[Tuple[str, str], bool] = {}

    for m in _TILE_RE.finditer(html):
        show_id = (m.group("show_id") or "").strip()
        title = _clean(m.group("title") or "")
        carousel = _clean(m.group("carousel") or "Voyo")
        body = m.group("body") or ""
        if not show_id or not title:
            continue

        # Dedup unikatni show_id v ramci jedneho carouselu.
        key = (carousel, show_id)
        if key in seen_show_ids_per_carousel:
            continue
        seen_show_ids_per_carousel[key] = True

        # Image (preferujem data-src protoze 'src' obsahuje lazy-placeholder).
        img_match = _TILE_IMG_RE.search(body) or _TILE_IMG_SRC_RE.search(body)
        image = img_match.group("src") if img_match else ""

        # URL (anchor v <h3 class="title">)
        href_match = _TILE_HREF_RE.search(body)
        voyo_url = href_match.group("href") if href_match else ""

        out.append({
            "show_id":  show_id,
            "title":    title,
            "carousel": carousel,
            "image":    image,
            "url":      voyo_url,
        })

    log.info("voyo: parsed %d tiles", len(out))
    return out


def _group_by_carousel(tiles: List[Dict[str, Any]]
                       ) -> List[Tuple[str, List[Dict[str, Any]]]]:
    """Zachovava poradi v jakem se carousely v HTML objevili.

    Returns: [(carousel_name, [tile, ...]), ...]
    """
    seen: List[str] = []
    by_car: Dict[str, List[Dict[str, Any]]] = {}
    for t in tiles:
        c = t.get("carousel") or "Voyo"
        if c not in by_car:
            by_car[c] = []
            seen.append(c)
        by_car[c].append(t)
    return [(c, by_car[c]) for c in seen]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_section(section: str,
                  force_refresh: bool = False
                  ) -> List[Tuple[str, List[Dict[str, Any]]]]:
    """Stahne sekciu Voyo (filmy/serialy/relacie) a vrati grupovany seznam
    carouselov.

    :param section: 'relacie' | 'serialy' | 'filmy'
    :param force_refresh: True = ignoruj cache.
    :returns: [(carousel_name, [tile, ...]), ...] - prazdny seznam pri chybe.
    """
    if section not in SECTIONS:
        log.error("voyo: neznama sekce '%s'", section)
        return []

    cache_key = f"{CACHE_KEY_PREFIX}:{section}"
    neg_cache_key = f"{CACHE_KEY_PREFIX}:neg:{section}"

    if not force_refresh:
        cached = cache.cache_get(cache_key, ttl=CACHE_TTL)
        if cached is not None:
            log.info("voyo: cache HIT %s (%d carouselu)",
                     section, len(cached))
            return [(c, list(items)) for (c, items) in cached]
        neg = cache.cache_get(neg_cache_key, ttl=NEG_CACHE_TTL)
        if neg:
            log.info("voyo: negativni cache HIT %s - skipuju fetch", section)
            return []

    url_path = SECTIONS[section][0]
    url = BASE_URL + url_path
    html = _http_get(url)
    if not html:
        cache.cache_set(neg_cache_key, {"ts": time.time()})
        return []

    try:
        tiles = _parse_tiles(html)
        grouped = _group_by_carousel(tiles)
    except Exception as exc:  # noqa: BLE001
        log.exception("voyo: parse fail %s: %s", section, exc)
        cache.cache_set(neg_cache_key, {"ts": time.time()})
        return []

    if not grouped:
        log.warning("voyo: parser nenasel ani jeden tile - HTML structure "
                    "se mohla zmenit.")
        cache.cache_set(neg_cache_key, {"ts": time.time()})
        return []

    cache.cache_set(cache_key, grouped)
    log.info("voyo: fetch %s OK, %d carouselov / %d tiles do cache",
             section, len(grouped), sum(len(items) for (_, items) in grouped))
    return grouped


def get_categories(section: str,
                   force_refresh: bool = False
                   ) -> List[Tuple[str, int]]:
    """Vrati [(carousel_name, tile_count), ...] pro UI menu.

    Pouziva se pri zobrazeni 'Voyo Relácie' / 'Voyo Filmy' / 'Voyo Seriály'
    jako folder list.
    """
    grouped = fetch_section(section, force_refresh=force_refresh)
    return [(c, len(items)) for (c, items) in grouped]


def get_category_tiles(section: str,
                       carousel: str,
                       force_refresh: bool = False
                       ) -> List[Dict[str, Any]]:
    """Vrati tile list pro jeden carousel v section.

    Match je case-insensitive - URL parametr je v UI snadno znicit.
    """
    grouped = fetch_section(section, force_refresh=force_refresh)
    target = (carousel or "").strip().lower()
    for (c, items) in grouped:
        if c.strip().lower() == target:
            return list(items)
    log.warning("voyo: carousel '%s' nenalezen v section '%s'",
                 carousel, section)
    return []


def get_all_tiles(section: str,
                  force_refresh: bool = False) -> List[Dict[str, Any]]:
    """Vrati VSECHNY tile pres carousely jako flat list, dedupnute podle
    show_id (kazdy show jen raz).

    Pouziti: "vsechno z Filmy" / "vsechno z Reality Show" view.
    """
    grouped = fetch_section(section, force_refresh=force_refresh)
    seen = set()
    out: List[Dict[str, Any]] = []
    for (_carousel, items) in grouped:
        for t in items:
            sid = t.get("show_id")
            if sid in seen:
                continue
            seen.add(sid)
            out.append(t)
    return out


def get_section_label(section: str) -> str:
    """Vrati UI label pro section ('Pořady / Relácie' / 'Seriály' / 'Filmy')."""
    return SECTIONS.get(section, (None, section, None))[1]


def clear_cache() -> int:
    """Smaze vsechny Voyo cache klice. Vraci pocet smazanych klicu.

    Pouziva se z "Aktualizovat" tlacitka v UI.
    """
    try:
        n = cache.cache_clear_prefix(CACHE_KEY_PREFIX)
        log.info("voyo: cache cleared (%d klicu)", n)
        return n
    except Exception as exc:  # noqa: BLE001
        log.exception("voyo: clear_cache failed: %s", exc)
        return 0
