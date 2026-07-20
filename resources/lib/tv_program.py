# -*- coding: utf-8 -*-
"""
tv_program.py
-------------
"TV program dnes" - rubrika ukazuje filmy / serialy ktere dnes hraji
v ceske TV. Spolehlivy zdroj: tvprogram.idnes.cz.

PROC NE CSFD:
    ČSFD ma agresivni Anubis anti-bot (proof-of-work JS challenge) na
    URL /televize/. Z Pythonu (urllib + UA) ho nelze pakovat. iDNES
    ma jen GDPR consent overlay, ktery lze obejit Googlebot UA hlavickou
    (iDNES whitelistuje Googlebot pro indexovani).

ZDROJ DAT:
    https://tvprogram.idnes.cz/ - jedna stranka obsahuje:
    1) Mapping data-channel-id -> nazev kanalu (<img class="tvlogo">)
    2) Show karty: <a data-channel="N" data-show="M" data-start="MIN"
       data-length="MIN" href="..."><div class="x-flm|x-ser|x-zpr">
       <h3>Title</h3><small>HH:MM</small><p>Popis</p>
       <img src="//1gr.cz/data/tvprogram/images/prev/..."></div></a>

KLASIFIKACE (CSS class na div):
    x-flm = film
    x-ser = serial
    x-zpr = zpravy
    x-zbv = zabava
    x-dok = dokument
    x-hud = hudba
    x-dts = deti
    x-spo = sport
    past  = uz probehlo (filtrujeme out, krome pripadu kdy chce user vse)

PERFORMANCE:
    - Hlavni stranka idnes.cz (~17 volnocasovych kanalu) = 1 request, rychle.
    - HBO/Cinemax/History atd. se stahuji NA POZADI (v0.0.91).
    - Cache 2 hodiny. Aktualizovat = rychly zaklad + pozadi pro premium.

UI WORKFLOW:
    1) Klik na "TV program dnes" v hlavnim menu -> view_list_tv_program
    2) View ukaze flat list filmu z prime time (>= 18:00) ze vsech kanalu
    3) Klik na film -> tmdb_play_movie -> Webshare search + quality picker
    4) Filmy z minulosti (past=True) jsou skryte (uz neda smysl spustit)

v0.0.63: novy modul (predtim csfd_tv.py, vyrazene kvuli CSFD CF blocku).
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import re
import threading
import time
import zlib
from html import unescape
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from . import cache

log = logging.getLogger("klempcinema.tv_program")

TV_URL = "https://tvprogram.idnes.cz/"

# Cache TTL: 2 hodiny. TV listingy se aktualizuji per den + nemenny po prime time.
TV_CACHE_TTL = 2 * 3600

# Cache klic bumpujem pri zmene parseru / struktury.
TV_CACHE_KEY = "tv_program:idnes:v3"
TV_CACHE_KEY_BASE = "tv_program:idnes:base:v1"

# Negativni cache (CF block / network down) - 10 min, neexpiruje rubric
# moc rychle ale i tak nas chrani pred network spamem.
TV_NEG_CACHE_TTL = 10 * 60
TV_NEG_CACHE_KEY = "tv_program:idnes:neg:v1"

# Placene / tematicke kanaly nejsou na hlavni strance idnes.cz - kazdy ma
# vlastni URL tvprogram.idnes.cz/{slug}. Slug overen proti iDNES (2026).
PREMIUM_CHANNEL_PAGES: Tuple[Tuple[str, str], ...] = (
    ("hbo", "HBO"),
    ("hbo-2", "HBO 2"),
    ("hbo3", "HBO 3"),
    ("cinemax", "Cinemax"),
    ("cinemax2", "Cinemax 2"),
    ("history-channel-hd", "History Channel"),
    ("amc", "AMC"),
    ("warner-tv", "Warner TV"),
    ("cs-film", "CS Film"),
    ("filmbox", "Filmbox"),
    ("filmbox-family", "Filmbox Family"),
    ("animal-planet", "Animal Planet"),
    ("national-geographic", "National Geographic"),
    ("nat-geo-wild", "Nat Geo Wild"),
    ("spektrum", "Spektrum"),
    ("travel-channel", "Travel Channel"),
    ("viasat-explorer", "Viasat Explorer"),
    ("nova-sport", "Nova Sport 1"),
    ("nova-sport-2", "Nova Sport 2"),
    ("oneplaysport-1", "Oneplay Sport 1"),
    ("oneplaysport-2", "Oneplay Sport 2"),
)

_PREMIUM_BY_SLUG: Dict[str, str] = dict(PREMIUM_CHANNEL_PAGES)
_PREMIUM_CHANNEL_NAMES: frozenset = frozenset(
    label.strip().lower() for _, label in PREMIUM_CHANNEL_PAGES
)

# Ikony kanalu: resources/icons/tv/<file>.png
# Klic = normalizovany nazev (bez diakritiky, lower).
_CHANNEL_ICON_ALIASES: Dict[str, str] = {
    # Ceske volnocasove
    "ct1": "ct1.png", "ct 1": "ct1.png", "c t1": "ct1.png",
    "ct2": "ct2.png", "ct 2": "ct2.png",
    "ct24": "ct24.png", "ct 24": "ct24.png",
    "ct sport": "ct-sport.png", "ct4": "ct-sport.png", "ct 4": "ct-sport.png",
    "ct :d": "ct-d.png", "ct : d": "ct-d.png", "ct d": "ct-d.png", "ct:d": "ct-d.png",
    "ct art": "ct-art.png",
    "nova": "nova.png",
    "nova cinema": "nova-cinema.png",
    "nova action": "nova-action.png",
    "nova fun": "nova-fun.png",
    "nova gold": "nova-gold.png",
    "nova sport 1": "nova-sport.png", "nova sport": "nova-sport.png",
    "nova sport 2": "nova-sport-2.png",
    "prima": "prima.png",
    "prima cool": "prima-cool.png",
    "prima krimi": "prima-krimi.png",
    "prima max": "prima-max.png", "prima maxx": "prima-max.png",
    "prima love": "prima-love.png",
    "cnn prima news": "cnn-prima-news.png", "cnn prima": "cnn-prima-news.png",
    "barrandov": "barrandov.png", "tv barrandov": "barrandov.png",
    "joj": "joj.png", "tv joj": "joj.png",
    "markiza": "markiza.png", "tv markiza": "markiza.png",
    # Premium / tematicke (PREMIUM_CHANNEL_PAGES)
    "hbo": "hbo.png",
    "hbo 2": "hbo-2.png", "hbo2": "hbo-2.png",
    "hbo 3": "hbo3.png", "hbo3": "hbo3.png",
    "cinemax": "cinemax.png",
    "cinemax 2": "cinemax2.png", "cinemax2": "cinemax2.png",
    "history channel": "history-channel-hd.png", "history": "history-channel-hd.png",
    "amc": "amc.png",
    "warner tv": "warner-tv.png", "warner": "warner-tv.png",
    "cs film": "cs-film.png", "csfilm": "cs-film.png",
    "filmbox": "filmbox.png",
    "filmbox family": "filmbox-family.png",
    "animal planet": "animal-planet.png",
    "national geographic": "national-geographic.png", "nat geo": "national-geographic.png",
    "nat geo wild": "nat-geo-wild.png", "national geographic wild": "nat-geo-wild.png",
    "spektrum": "spektrum.png",
    "travel channel": "travel-channel.png", "travel": "travel-channel.png",
    "viasat explorer": "viasat-explorer.png",
    "oneplay sport 1": "oneplaysport-1.png",
    "oneplay sport 2": "oneplaysport-2.png",
}

# slug z PREMIUM_CHANNEL_PAGES -> icon file
_PREMIUM_ICON_BY_SLUG: Dict[str, str] = {
    slug: f"{slug}.png" for slug, _ in PREMIUM_CHANNEL_PAGES
}


def _strip_diacritics(text: str) -> str:
    table = str.maketrans({
        "á": "a", "ä": "a", "č": "c", "ď": "d", "é": "e", "ě": "e",
        "í": "i", "ĺ": "l", "ľ": "l", "ň": "n", "ó": "o", "ô": "o",
        "ö": "o", "ř": "r", "š": "s", "ť": "t", "ú": "u", "ů": "u",
        "ü": "u", "ý": "y", "ž": "z",
        "Á": "a", "Ä": "a", "Č": "c", "Ď": "d", "É": "e", "Ě": "e",
        "Í": "i", "Ĺ": "l", "Ľ": "l", "Ň": "n", "Ó": "o", "Ô": "o",
        "Ö": "o", "Ř": "r", "Š": "s", "Ť": "t", "Ú": "u", "Ů": "u",
        "Ü": "u", "Ý": "y", "Ž": "z",
    })
    return (text or "").translate(table)


def _normalize_channel_key(name: str) -> str:
    s = _strip_diacritics(name).lower().strip()
    s = s.replace(":", " ").replace("+", " plus ").replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def channel_icon_relpath(channel_name: str = "",
                         channel_id: str = "") -> Optional[str]:
    """Vrati relativni cestu icons/tv/<file>.png nebo None.

    Pouziti: _addon_icon_for(channel_icon_relpath(...))
    """
    cid = str(channel_id or "").strip()
    if cid.startswith("x:"):
        slug = cid[2:].strip().lower()
        fname = _PREMIUM_ICON_BY_SLUG.get(slug) or f"{slug}.png"
        return f"tv/{fname}"

    key = _normalize_channel_key(channel_name)
    if not key:
        return None

    fname = _CHANNEL_ICON_ALIASES.get(key)
    if fname:
        return f"tv/{fname}"

    # Prima MAX / HBO Max style – zkus bez mezer
    compact = key.replace(" ", "")
    for alias, icon in _CHANNEL_ICON_ALIASES.items():
        if alias.replace(" ", "") == compact:
            return f"tv/{icon}"

    # heuristika: ct1 / nova-cinema z nazvu
    slug = key.replace(" ", "-")
    return f"tv/{slug}.png"

_bg_lock = threading.Lock()
_bg_running = False
_BG_LOCK_STALE_SEC = 20 * 60

# Timeout pro fetch - ne moc dlouhy, idnes je rychly.
# v0.0.64: 8 -> 5s pro lepsi shutdown responsiveness.
HTTP_TIMEOUT = 5

# Googlebot UA - iDNES (a vetsina ceskych webu) whitelistuje
# Googlebot pro SEO indexovani -> obchazi GDPR consent overlay.
# Bezpecne: nezvyseuje load (rate ~1 req / 2 hod), pouze pro
# verejna data (TV program).
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; Googlebot/2.1; "
        "+http://www.google.com/bot.html)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "cs-CZ,cs;q=0.9,en;q=0.6",
    "Accept-Encoding": "gzip, deflate",
}

# Regex pro mapping data-channel -> nazev kanalu.
# HTML: <img class="tvlogo" src="..." title="ČT1" alt="ČT1" data-channel="1">
_CHANNEL_LOGO_RE = re.compile(
    r'<img\s+class="tvlogo"\s+[^>]*'
    r'title="(?P<title>[^"]+)"\s+[^>]*'
    r'data-channel="(?P<id>\d+)"',
    re.IGNORECASE,
)
# Alternativni poradi atributu - title po data-channel.
_CHANNEL_LOGO_ALT_RE = re.compile(
    r'<img\s+class="tvlogo"\s+[^>]*'
    r'data-channel="(?P<id>\d+)"\s+[^>]*'
    r'title="(?P<title>[^"]+)"',
    re.IGNORECASE,
)
# I src URL slugu z URL muzeme pouzit jako fallback nazev.
_CHANNEL_LOGO_SRC_RE = re.compile(
    r'<img\s+class="tvlogo"\s+src="[^"]*/loga-m/(?P<slug>[a-z0-9\-]+)\.png"'
    r'[^>]*data-channel="(?P<id>\d+)"',
    re.IGNORECASE,
)

# Regex pro jednu show kartu (anchor + div s metadaty).
# DOTALL: '.' matchuje i \n.
# - druhy <p> obsahuje <i class="x-flm"></i>... ikony - pouzivame '.*?'
#   ne '[^<]*' protoze obsahuje '<'.
# - img je optional (nekterym pořadu chybi thumbnail).
_SHOW_RE = re.compile(
    r'<a\s+class="(?P<class>[^"]*)"\s+'
    r'data-channel="(?P<channel>\d+)"\s+'
    r'data-show="(?P<show>\d+)"\s+'
    r'data-series="(?P<series>\d+)"\s+'
    r'data-start="(?P<start>\d+)"\s+'
    r'data-length="(?P<length>\d+)"\s+'
    r'href="(?P<href>[^"]+)"\s*>'
    r'<div\s+class="(?P<divclass>[^"]*)">'
    r'<h3>(?P<title>[^<]+)</h3>'
    r'<small>(?P<timehm>[0-2]?\d:[0-5]\d)</small>'
    r'<p>(?P<plot>[^<]*)</p>'
    r'(?:\s*<p>.*?</p>)?'  # druhy <p> s <i> ikonami - skip non-greedy
    r'\s*(?:<img[^>]*src="(?P<thumb>[^"]+)"[^>]*>)?',
    re.IGNORECASE | re.DOTALL,
)

# Rok z popisu - "(2023)" nebo "(USA 2023)" nebo "(Čes. 2023)".
# Akceptujem cokoliv (krome ')') az 30 znaku pred rokem.
_YEAR_IN_PLOT_RE = re.compile(r"\([^)]{0,30}?(\d{4})\)")


_META_CHARSET_RE = re.compile(
    rb'<meta[^>]*charset\s*=\s*["\']?([A-Za-z0-9._-]+)',
    re.IGNORECASE,
)


def _detect_encoding(headers_ct: str, raw_bytes: bytes) -> str:
    """Detekuje encoding: 1) Content-Type charset 2) <meta charset> v HTML
    3) default utf-8.

    iDNES posila HTML ve windows-1250 s deklaraci v <meta charset>,
    ale Content-Type hlavicka casto charset nenese.
    """
    ct = (headers_ct or "").lower()
    if "charset=" in ct:
        enc = ct.split("charset=", 1)[1].split(";")[0].strip()
        if enc:
            return enc
    # Sniff prvni 4KB pro <meta charset="...">
    head = raw_bytes[:4096]
    m = _META_CHARSET_RE.search(head)
    if m:
        try:
            return m.group(1).decode("ascii", errors="ignore")
        except Exception:  # noqa: BLE001
            pass
    return "utf-8"


def _decode_response(resp: Any) -> str:
    """Z urlopen() response vrati dekomprimovany text. Spravuje
    gzip/deflate + charset detekce z Content-Type i <meta>."""
    data = resp.read()
    ce = (resp.headers.get("Content-Encoding") or "").lower()
    if "gzip" in ce:
        try:
            data = gzip.decompress(data)
        except Exception as exc:  # noqa: BLE001
            log.debug("tv_program gzip decode fail: %s", exc)
    elif "deflate" in ce:
        try:
            data = zlib.decompress(data)
        except Exception:  # noqa: BLE001
            try:
                data = zlib.decompress(data, -zlib.MAX_WBITS)
            except Exception as exc:  # noqa: BLE001
                log.debug("tv_program deflate decode fail: %s", exc)

    enc = _detect_encoding(resp.headers.get("Content-Type") or "", data)
    try:
        return data.decode(enc, errors="replace")
    except LookupError:
        log.warning("tv_program: neznamy charset '%s', fallback win-1250", enc)
        try:
            return data.decode("cp1250", errors="replace")
        except Exception:  # noqa: BLE001
            return data.decode("utf-8", errors="replace")


def _http_get(url: str) -> Optional[str]:
    """GET TV programu. Vraci HTML jako string. None pri chybe.

    Neagresivni - jedne IP poslem 1 request kazde ~2h (TTL cache).
    """
    req = Request(url, headers=HEADERS)
    try:
        with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            html = _decode_response(resp)
            # Sanity check: ocekavame > 50 KB pro plnou stranku.
            if not html or len(html) < 5000:
                log.warning("tv_program: idnes vratil podezrele malou stranku "
                            "(%d B) - mozna redirect na GDPR overlay.",
                            len(html or ""))
                return None
            return html
    except HTTPError as exc:
        log.warning("tv_program: HTTP %s pri fetch %s", exc.code, url)
        return None
    except URLError as exc:
        log.warning("tv_program: network err %s: %s", url, exc.reason)
        return None
    except Exception as exc:  # noqa: BLE001
        log.exception("tv_program: fetch fail %s: %s", url, exc)
        return None


def _clean_text(s: str) -> str:
    """Strip HTML tagy, unescape entities, normalizuj whitespace."""
    if not s:
        return ""
    s = unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _parse_channel_map(html: str) -> Dict[str, str]:
    """Z HTML vrati {channel_id: channel_name}.

    Tri regex strategie:
      1) title="..." po data-channel
      2) title="..." pred data-channel
      3) slug z URL loga (loga-m/SLUG.png) jako fallback nazev
    """
    out: Dict[str, str] = {}

    for m in _CHANNEL_LOGO_RE.finditer(html):
        cid = m.group("id")
        name = _clean_text(m.group("title"))
        if cid and name and cid not in out:
            out[cid] = name

    for m in _CHANNEL_LOGO_ALT_RE.finditer(html):
        cid = m.group("id")
        name = _clean_text(m.group("title"))
        if cid and name and cid not in out:
            out[cid] = name

    # Fallback: slug z URL
    for m in _CHANNEL_LOGO_SRC_RE.finditer(html):
        cid = m.group("id")
        if cid in out:
            continue
        slug = m.group("slug")
        # ct-1 -> ČT1, nova-fun -> Nova Fun, etc.
        pretty = _channel_slug_to_name(slug)
        if pretty:
            out[cid] = pretty

    log.info("tv_program: namapovano %d kanalu", len(out))
    return out


def _channel_slug_to_name(slug: str) -> str:
    """ct-1 -> ČT1, prima-cool -> Prima COOL, atd."""
    if not slug:
        return ""
    s = slug.lower()
    # ČT mapování
    ct_map = {
        "ct-1": "ČT1", "ct-2": "ČT2", "ct-24": "ČT24",
        "ct-4-sport": "ČT sport", "ct-d": "ČT :D",
        "ct-art": "ČT art",
    }
    if s in ct_map:
        return ct_map[s]
    # Prima/Nova varianty - capitalize first word, uppercase suffix
    parts = s.split("-")
    if not parts:
        return slug
    head = parts[0].capitalize()
    rest = " ".join(p.upper() if len(p) <= 4 else p.capitalize()
                    for p in parts[1:])
    return f"{head} {rest}".strip()


def _classify_show(divclass: str) -> str:
    """Z CSS tridy divu vrati typ obsahu.

    Returns: 'film' | 'series' | 'news' | 'entertainment' | 'sport'
             | 'documentary' | 'kids' | 'music' | 'other'
    """
    c = (divclass or "").lower()
    if "x-flm" in c:
        return "film"
    if "x-ser" in c:
        return "series"
    if "x-dok" in c:
        return "documentary"
    if "x-zpr" in c:
        return "news"
    if "x-zbv" in c:
        return "entertainment"
    if "x-spo" in c:
        return "sport"
    if "x-dts" in c:
        return "kids"
    if "x-hud" in c:
        return "music"
    return "other"


# Rubriky v UI (jako CSFD TV program)
SCOPE_KINDS: Dict[str, Tuple[str, ...]] = {
    "films":         ("film",),
    "series":        ("series",),
    "shows":         ("entertainment", "other"),
    "documentary":   ("documentary",),
    "all_watchable": ("film", "series", "documentary", "entertainment"),
}


def filter_today(
    items: List[Dict[str, Any]],
    kinds: Tuple[str, ...],
    only_future: bool = True,
    prime_time_only: bool = False,
) -> List[Dict[str, Any]]:
    """Vyfiltruje dnesni polozky podle typu (film/serial/dokument/...)."""
    out: List[Dict[str, Any]] = []
    min_start = 18 * 60 if prime_time_only else 0
    max_start = 24 * 60

    for it in items:
        if it.get("kind") not in kinds:
            continue
        if only_future and it.get("is_past"):
            continue
        sm = it.get("start_min") or 0
        if prime_time_only and (sm < min_start or sm > max_start):
            continue
        out.append(it)

    out.sort(key=lambda x: (x.get("start_min") or 0, x.get("channel") or ""))
    return out


def count_today(items: List[Dict[str, Any]], scope: str,
                only_future: bool = True) -> int:
    kinds = SCOPE_KINDS.get(scope, SCOPE_KINDS["all_watchable"])
    return len(filter_today(items, kinds, only_future=only_future))


def _is_past(anchor_class: str) -> bool:
    """True pokud anchor ma class 'past' (probehlo uz)."""
    return "past" in (anchor_class or "").lower().split()


def _normalize_thumb(url: str) -> str:
    """Doplni protokol na //1gr.cz/... -> https://1gr.cz/..."""
    if not url:
        return ""
    url = url.strip()
    if url.startswith("//"):
        return "https:" + url
    return url


def _extract_year_from_plot(plot: str) -> Optional[int]:
    """Z popisku '... USA (2023). ...' vrati 2023 nebo None."""
    if not plot:
        return None
    m = _YEAR_IN_PLOT_RE.search(plot)
    if m:
        try:
            y = int(m.group(1))
            if 1900 <= y <= 2100:
                return y
        except ValueError:
            pass
    return None


def _parse_shows(html: str, channel_map: Dict[str, str]) -> List[Dict[str, Any]]:
    """Z HTML vrati seznam show karet."""
    out: List[Dict[str, Any]] = []
    if not html:
        return out

    for m in _SHOW_RE.finditer(html):
        a_class = m.group("class") or ""
        cid = m.group("channel") or ""
        try:
            data_start = int(m.group("start") or 0)
            data_length = int(m.group("length") or 0)
        except (ValueError, TypeError):
            continue

        title = _clean_text(m.group("title"))
        if not title or len(title) < 2:
            continue
        timehm = (m.group("timehm") or "").strip()
        plot = _clean_text(m.group("plot"))
        thumb = _normalize_thumb(m.group("thumb") or "")
        href = m.group("href") or ""
        if href and not href.startswith("http"):
            href = urljoin("https://tvprogram.idnes.cz", href)

        kind = _classify_show(m.group("divclass") or "")
        past = _is_past(a_class)
        channel = channel_map.get(cid, f"Kanal {cid}")
        year = _extract_year_from_plot(plot)
        show_id = (m.group("show") or "").strip()

        out.append({
            "channel_id":  cid,
            "channel":     channel,
            "show_id":     show_id,
            "title":       title,
            "time":        timehm,
            "start_min":   data_start,
            "length_min":  data_length,
            "plot":        plot,
            "thumb":       thumb,
            "url":         href,
            "kind":        kind,
            "year":        year,
            "is_past":     past,
        })

    log.info("tv_program: naparsovano %d show karet", len(out))
    return _dedupe_tv_items(out)


def _fetch_single_extra_channel(slug: str, label: str) -> List[Dict[str, Any]]:
    """Stahne dnesni program jednoho placeneho kanalu (HBO, Cinemax, ...)."""
    url = urljoin(TV_URL, slug)
    html = _http_get(url)
    if not html or len(html) < 8000:
        log.debug("tv_program: extra %s - prazdna/kratka odpoved", slug)
        return []
    try:
        channel_map = _parse_channel_map(html)
        items = _parse_shows(html, channel_map)
    except Exception as exc:  # noqa: BLE001
        log.debug("tv_program: extra %s parse selhal: %s", slug, exc)
        return []

    display = label
    if channel_map:
        # Na strance kanalu je obvykle jeden nazev v tvlogo title.
        names = sorted(set(channel_map.values()))
        if len(names) == 1:
            display = names[0]
        elif not display and names:
            display = names[0]

    cid = f"x:{slug}"
    for it in items:
        it["channel_id"] = cid
        it["channel"] = display or slug
        it["premium"] = True
    log.info("tv_program: extra %s (%s) -> %d polozek", slug, display, len(items))
    return items


def _fetch_extra_channel_items() -> List[Dict[str, Any]]:
    """Paralelne stahne program placenych kanalu a slouci do jednoho seznamu."""
    try:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from . import shutdown as _shutdown
    except Exception:  # noqa: BLE001
        return []

    merged: List[Dict[str, Any]] = []
    workers = min(4, len(PREMIUM_CHANNEL_PAGES))
    # v0.0.152: shutdown(wait=False) — jinak Quit ceka na vsechny kanaly
    pool = ThreadPoolExecutor(max_workers=workers,
                              thread_name_prefix="tvprog-extra")
    try:
        futs = {
            pool.submit(_fetch_single_extra_channel, slug, label): slug
            for slug, label in PREMIUM_CHANNEL_PAGES
        }
        for fut in as_completed(futs):
            if _shutdown.is_shutting_down():
                break
            slug = futs[fut]
            try:
                batch = fut.result(timeout=0.5)
                if batch:
                    merged.extend(batch)
            except Exception as exc:  # noqa: BLE001
                log.debug("tv_program: extra %s selhal: %s", slug, exc)
    finally:
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            pool.shutdown(wait=False)
        except Exception:  # noqa: BLE001
            pass
    log.info("tv_program: premium kanaly celkem %d polozek", len(merged))
    return merged


def _tv_item_dedup_key(it: Dict[str, Any]) -> tuple:
    """Unikatni klic pro jednu vysilaci polozku."""
    show_id = str(it.get("show_id") or "").strip()
    if show_id:
        return (
            str(it.get("channel_id") or ""),
            show_id,
            int(it.get("start_min") or 0),
        )
    return (
        str(it.get("channel_id") or ""),
        (it.get("channel") or "").strip().lower(),
        int(it.get("start_min") or 0),
        (it.get("title") or "").strip().lower(),
    )


def _dedupe_tv_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Odstrani duplicitni polozky (iDNES HTML / slouceni base + premium)."""
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for it in items:
        key = _tv_item_dedup_key(it)
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _strip_premium_channels_from_base(
    base_items: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Hlavni stranka obcas obsahuje HBO/Cinemax – nechame jen premium fetch."""
    out: List[Dict[str, Any]] = []
    for it in base_items:
        ch = (it.get("channel") or "").strip().lower()
        if ch in _PREMIUM_CHANNEL_NAMES:
            continue
        out.append(it)
    return out


def _fetch_main_page_items() -> List[Dict[str, Any]]:
    """Jen hlavni stranka idnes.cz - volnocasove kanaly (~1 HTTP request)."""
    html = _http_get(TV_URL)
    if not html:
        cache.cache_set(TV_NEG_CACHE_KEY, {"ts": time.time()})
        return []
    try:
        channel_map = _parse_channel_map(html)
        items = _parse_shows(html, channel_map)
    except Exception as exc:  # noqa: BLE001
        log.exception("tv_program: parse hlavni stranky selhal: %s", exc)
        cache.cache_set(TV_NEG_CACHE_KEY, {"ts": time.time()})
        return []
    if not items:
        cache.cache_set(TV_NEG_CACHE_KEY, {"ts": time.time()})
    return items


def _bg_lock_path() -> str:
    return os.path.join(cache._profile_dir(), "tvprog_bg.lock")


def _acquire_bg_process_lock() -> bool:
    """Jen jeden bg fetch napric procesy (Kodi spousti vice Python instanci)."""
    path = _bg_lock_path()
    now = time.time()
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fp:
                info = json.load(fp)
            if now - float(info.get("ts", 0)) < _BG_LOCK_STALE_SEC:
                return False
        with open(path, "w", encoding="utf-8") as fp:
            json.dump({"ts": now, "pid": os.getpid()}, fp)
        return True
    except OSError:
        return True


def _release_bg_process_lock() -> None:
    try:
        os.remove(_bg_lock_path())
    except OSError:
        pass


def _build_full_program(base_items: List[Dict[str, Any]],
                        *, bg_mode: bool = False) -> List[Dict[str, Any]]:
    """Slouci zaklad + premium kanaly + TMDB enrich."""
    items = _strip_premium_channels_from_base(base_items)
    items.extend(_fetch_extra_channel_items())
    items = _dedupe_tv_items(items)
    try:
        _enrich_with_tmdb_posters(
            items,
            max_wait_sec=4.0 if bg_mode else None,
            csfd_fallback=False,
            max_items=45 if bg_mode else None,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("tv_program TMDB enrich selhal: %s", exc)
    return items


def is_background_fetch_running() -> bool:
    with _bg_lock:
        return _bg_running


def is_full_cache_ready() -> bool:
    """True pokud mame kompletni cache vcetne premium kanalu."""
    full = cache.cache_get(TV_CACHE_KEY, ttl=TV_CACHE_TTL)
    if not full:
        return False
    return any(it.get("premium") for it in full)


def schedule_full_fetch(base_items: Optional[List[Dict[str, Any]]] = None) -> None:
    """Spusti doplneni premium kanalu + enrich na pozadi (daemon thread)."""
    global _bg_running
    if not _acquire_bg_process_lock():
        log.debug("tv_program: background fetch lock obsazeny")
        return
    with _bg_lock:
        if _bg_running:
            _release_bg_process_lock()
            log.debug("tv_program: background fetch uz bezi")
            return
        _bg_running = True

    t = threading.Thread(
        target=_background_full_fetch,
        args=(base_items,),
        name="tvprog-bg-full",
        daemon=True,
    )
    t.start()
    log.info("tv_program: background fetch spusten")


def _background_full_fetch(base_items: Optional[List[Dict[str, Any]]]) -> None:
    global _bg_running
    try:
        from . import shutdown as _shutdown
        try:
            from . import lifecycle as _lifecycle
        except Exception:  # noqa: BLE001
            _lifecycle = None
        if _shutdown.is_shutting_down():
            return

        if base_items is None:
            base_items = cache.cache_get(TV_CACHE_KEY_BASE, ttl=TV_CACHE_TTL)
            if not base_items:
                base_items = _fetch_main_page_items()
        if not base_items:
            return

        log.info("tv_program: background fetch start (%d zakladnich polozek)",
                 len(base_items))
        full = _build_full_program(base_items, bg_mode=True)
        if _shutdown.is_shutting_down():
            return
        if _lifecycle and _lifecycle.is_plugin_exiting():
            return
        cache.cache_set(TV_CACHE_KEY, full)
        cache.cache_set(TV_CACHE_KEY_BASE, base_items)
        n_prem = sum(1 for it in full if it.get("premium"))
        log.info("tv_program: background fetch hotovo (%d polozek, %d premium)",
                 len(full), n_prem)

        try:
            import xbmcgui  # type: ignore
            xbmcgui.Dialog().notification(
                "KlempCinema",
                f"TV program: doplneno {n_prem} polozek z HBO/Cinemax/...",
                xbmcgui.NOTIFICATION_INFO,
                5000,
            )
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        log.exception("tv_program: background fetch selhal: %s", exc)
    finally:
        _release_bg_process_lock()
        with _bg_lock:
            _bg_running = False


def get_premium_channels(force_refresh: bool = False) -> List[Tuple[str, str]]:
    """Vrati [(channel_id, channel_name), ...] jen s dnesnim sledovatelnym obsahem.

    Sport/zpravy/prazdne kanaly se nezobrazuji. Bez fallbacku na cely
    PREMIUM_CHANNEL_PAGES seznam (drive ukazoval prazdne slozky HBO/Sport).
    """
    return _channels_with_watchable(force_refresh=force_refresh, premium=True)


def get_channels(force_refresh: bool = False) -> List[Tuple[str, str]]:
    """Vrati [(channel_id, channel_name), ...] s dnesnim sledovatelnym obsahem.

    Jen filmy/serialy/dokumenty/porady (ne sport/zpravy). Prazdne stanice pryč.
    """
    return _channels_with_watchable(force_refresh=force_refresh, premium=False)


def _channels_with_watchable(
    force_refresh: bool = False,
    premium: bool = False,
) -> List[Tuple[str, str]]:
    """Kanaly, ktere maji alespon 1 budouci watchable polozku."""
    items = fetch_today(force_refresh=force_refresh)
    kinds = set(SCOPE_KINDS["all_watchable"])
    seen: Dict[str, str] = {}
    for it in items:
        cid = str(it.get("channel_id") or "")
        if not cid:
            continue
        is_prem = cid.startswith("x:")
        if premium and not is_prem:
            continue
        if not premium and is_prem:
            continue
        if it.get("kind") not in kinds:
            continue
        if it.get("is_past"):
            continue
        seen[cid] = (it.get("channel") or cid).strip() or cid
    return sorted(seen.items(), key=lambda x: (x[1] or "").lower())

def fetch_today(force_refresh: bool = False,
                blocking: bool = False) -> List[Dict[str, Any]]:
    """Stahne TV program na dnes z iDNES.

    :param force_refresh: smaze cache a stahne znovu.
    :param blocking: True = stary synchronni rezim (vse najednou, pomale).
                       False = rychly zaklad + premium na pozadi (default).

    Polozka:
        {
          "channel_id":  "3",
          "channel":     "Nova",
          ...
        }
    """
    if force_refresh:
        try:
            cache.cache_delete(TV_CACHE_KEY)
            cache.cache_delete(TV_CACHE_KEY_BASE)
        except Exception:  # noqa: BLE001
            pass

    if not force_refresh and not blocking:
        full = cache.cache_get(TV_CACHE_KEY, ttl=TV_CACHE_TTL)
        if full is not None:
            log.info("tv_program: full cache HIT (%d polozek)", len(full))
            return _dedupe_tv_items(list(full))

        neg = cache.cache_get(TV_NEG_CACHE_KEY, ttl=TV_NEG_CACHE_TTL)
        if neg:
            log.info("tv_program: negativni cache HIT - skipuju fetch")
            return []

        base = cache.cache_get(TV_CACHE_KEY_BASE, ttl=TV_CACHE_TTL)
        if base is not None:
            log.info("tv_program: base cache HIT (%d), cekam na pozadi",
                     len(base))
            if _tv_items_need_poster_enrich(base):
                _enrich_with_tmdb_posters(
                    base, max_wait_sec=4.0, csfd_fallback=False)
                cache.cache_set(TV_CACHE_KEY_BASE, base)
            schedule_full_fetch(list(base))
            return _dedupe_tv_items(list(base))

    if blocking:
        base = _fetch_main_page_items()
        if not base:
            return []
        items = _build_full_program(base)
        cache.cache_set(TV_CACHE_KEY, items)
        cache.cache_set(TV_CACHE_KEY_BASE, base)
        log.info("tv_program: blocking fetch OK, %d polozek", len(items))
        return _dedupe_tv_items(items)

    # Rychly rezim: hlavni stranka sync, TMDB plakaty s limitem, premium na pozadi.
    base = _fetch_main_page_items()
    if not base:
        return []

    _enrich_with_tmdb_posters(base, max_wait_sec=5.0, csfd_fallback=False)
    _warm_tv_poster_cache(base)
    cache.cache_set(TV_CACHE_KEY_BASE, base)
    schedule_full_fetch(list(base))
    log.info("tv_program: rychly fetch OK (%d polozek), premium na pozadi",
             len(base))
    return _dedupe_tv_items(base)


def _warm_tv_poster_cache(items: List[Dict[str, Any]]) -> None:
    try:
        from . import image_cache
        urls: List[str] = []
        for it in items:
            p = (it.get("tmdb_poster") or it.get("csfd_poster")
                 or it.get("thumb") or "")
            if p and p.startswith(("http://", "https://")):
                urls.append(p)
        if urls:
            image_cache.warm_urls(urls, max_urls=30, total_timeout=2.5)
    except Exception:  # noqa: BLE001
        pass


def _tv_enrichable_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for it in items:
        if it.get("is_past"):
            continue
        if it.get("kind") not in ("film", "series", "documentary", "entertainment"):
            continue
        if not (it.get("title") or "").strip():
            continue
        out.append(it)
    return out


def _tv_items_need_poster_enrich(items: List[Dict[str, Any]]) -> bool:
    enrichable = _tv_enrichable_items(items)
    if not enrichable:
        return False
    with_poster = sum(
        1 for it in enrichable
        if it.get("tmdb_poster") or it.get("csfd_poster")
    )
    return with_poster < len(enrichable) // 2


def _enrich_with_tmdb_posters(items: List[Dict[str, Any]],
                              max_wait_sec: Optional[float] = None,
                              csfd_fallback: bool = True,
                              max_items: Optional[int] = None) -> None:
    """v0.0.71: doplni TMDB plakaty pro filmy/serialy v TV programu.

    Vola TMDB search per polozka (paralel ThreadPoolExecutor, 6 workers).
    Cache TMDB hitu pretrvava 30 dni, takze opakovane TV program fetches
    nevolaji TMDB znovu pro tytez tituly.

    In-place doplnuje pole:
        item["tmdb_poster"]
        item["tmdb_fanart"]
        item["tmdb_year"]      (pokud jsme rok nemeli z plotu)
        item["tmdb_plot"]      (alternativni cs popis pokud iDNES je strucny)
        item["tmdb_title"]     (oficialni cs nazev)
        item["tmdb_rating"]    (pro lepsi sort / info)

    Skipuje (a) past polozky, (b) ne-filmove kindy (zpravy/sport/zabava
    nemaji TMDB zaznam) a (c) shutdown signal.
    """
    if not items:
        return

    try:
        from . import tmdb as _tmdb
        from . import shutdown as _shutdown
    except Exception:  # noqa: BLE001
        return

    if not _tmdb.is_enabled():
        log.info("tv_program TMDB enrich: TMDB disabled, skipping")
        return

    # Filtruj polozky ktere maji smysl enrichovat (film/serial, ne minulost).
    # v0.0.89: obohatit i dokumenty a TV pořady (zábava).
    enrichable = _tv_enrichable_items(items)

    if max_items is not None and len(enrichable) > max_items:
        enrichable.sort(key=lambda x: x.get("start_min") or 0)
        enrichable = enrichable[:max_items]

    if not enrichable:
        return

    log.info("tv_program: TMDB enrich pro %d polozek (filmy+serialy)",
             len(enrichable))

    def _enrich_one(it: Dict[str, Any]) -> None:
        if _shutdown.is_shutting_down():
            return
        title = (it.get("title") or "").strip()
        year = it.get("year")
        kind = it.get("kind")
        try:
            if kind in ("series", "documentary", "entertainment"):
                meta = _tmdb.search_tv(title)
            else:
                meta = _tmdb.search_movie(title, year)
            if not meta:
                return
            poster = meta.get("poster") or ""
            fanart = meta.get("fanart") or ""
            if poster:
                it["tmdb_poster"] = poster
            if fanart:
                it["tmdb_fanart"] = fanart
            tmdb_year = meta.get("year")
            if tmdb_year and not it.get("year"):
                it["tmdb_year"] = int(tmdb_year)
            tmdb_plot = meta.get("plot") or ""
            if tmdb_plot:
                it["tmdb_plot"] = tmdb_plot
            tmdb_title = meta.get("title") or ""
            if tmdb_title:
                it["tmdb_title"] = tmdb_title
            tmdb_rating = meta.get("rating")
            if tmdb_rating:
                it["tmdb_rating"] = float(tmdb_rating)
        except Exception as exc:  # noqa: BLE001
            log.debug("tv_program enrich %r selhal: %s", title, exc)

    try:
        from concurrent.futures import ThreadPoolExecutor, wait
        workers = min(4, max(1, len(enrichable)))
        pool = ThreadPoolExecutor(max_workers=workers,
                                  thread_name_prefix="tvprog-tmdb")
        try:
            if max_wait_sec is not None and max_wait_sec > 0:
                futures = [pool.submit(_enrich_one, it) for it in enrichable]
                pending = set(futures)
                budget = float(max_wait_sec)
                while pending and budget > 0 and not _shutdown.is_shutting_down():
                    done, pending = wait(pending, timeout=min(0.5, budget))
                    budget -= 0.5
                if pending:
                    log.info("tv_program: TMDB enrich budget - %d/%d nedobehlo",
                             len(pending), len(futures))
            else:
                list(pool.map(_enrich_one, enrichable))
        finally:
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                pool.shutdown(wait=False)
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        log.exception("tv_program enrich pool selhal: %s", exc)
        for it in enrichable:
            if _shutdown.is_shutting_down():
                break
            _enrich_one(it)

    n_with_poster = sum(1 for it in enrichable if it.get("tmdb_poster"))
    log.info("tv_program: TMDB enrich hotovo, %d/%d polozek ma plakat",
             n_with_poster, len(enrichable))

    if csfd_fallback:
        _enrich_with_csfd_fallback(enrichable)


def _enrich_with_csfd_fallback(items: List[Dict[str, Any]]) -> None:
    """CSFD doplneni tam, kde TMDB neprineslo plakat nebo hodnoceni."""
    if not items:
        return
    try:
        from . import csfd as _csfd
        from . import shutdown as _shutdown
    except Exception:  # noqa: BLE001
        return
    if not _csfd.is_enabled():
        return

    need_csfd = [
        it for it in items
        if not it.get("tmdb_poster") or not it.get("tmdb_rating")
    ]
    if not need_csfd:
        return

    log.info("tv_program: CSFD fallback enrich pro %d polozek", len(need_csfd))

    def _one(it: Dict[str, Any]) -> None:
        if _shutdown.is_shutting_down():
            return
        title = (it.get("title") or "").strip()
        if not title:
            return
        kind = it.get("kind") or "film"
        try:
            if kind in ("series", "documentary", "entertainment"):
                meta = _csfd.search_tv(title)
            else:
                meta = _csfd.search_movie(title, it.get("year"))
            if not meta:
                return
            if meta.get("csfd_rating") is not None:
                it["csfd_rating"] = float(meta["csfd_rating"])
                it["csfd_rating_pct"] = int(meta.get("csfd_rating_pct") or 0)
            if meta.get("csfd_url"):
                it["csfd_url"] = meta["csfd_url"]
            if not it.get("tmdb_poster"):
                poster = meta.get("poster") or meta.get("csfd_poster") or ""
                if poster:
                    it["csfd_poster"] = poster
            if not it.get("tmdb_plot") and meta.get("plot"):
                it["csfd_plot"] = meta["plot"]
            if meta.get("title") and not it.get("tmdb_title"):
                it["csfd_title"] = meta["title"]
        except Exception as exc:  # noqa: BLE001
            log.debug("tv_program CSFD enrich %r: %s", title, exc)

    try:
        from concurrent.futures import ThreadPoolExecutor
        workers = min(4, max(1, len(need_csfd)))
        pool = ThreadPoolExecutor(max_workers=workers,
                                  thread_name_prefix="tvprog-csfd")
        try:
            list(pool.map(_one, need_csfd))
        finally:
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                pool.shutdown(wait=False)
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        log.exception("tv_program CSFD pool selhal: %s", exc)
        for it in need_csfd:
            if _shutdown.is_shutting_down():
                break
            _one(it)


def filter_films(items: List[Dict[str, Any]],
                 only_future: bool = True,
                 prime_time_only: bool = False,
                 min_start_min: int = 0,
                 max_start_min: int = 24 * 60) -> List[Dict[str, Any]]:
    """Vyfiltruje jen filmy (kind='film').

    :param only_future: True = skipni minulost (is_past=True)
    :param prime_time_only: True = jen 18:00-24:00
    :param min_start_min/max_start_min: dalsi filtr na cas (minuty od pulnoci)
    """
    out = []
    if prime_time_only:
        min_start_min = max(min_start_min, 18 * 60)
        max_start_min = min(max_start_min, 24 * 60)

    for it in items:
        if it.get("kind") != "film":
            continue
        if only_future and it.get("is_past"):
            continue
        sm = it.get("start_min") or 0
        if sm < min_start_min or sm > max_start_min:
            continue
        out.append(it)

    # Razeni: chronologicky vzestupne, pak kanal
    out.sort(key=lambda x: (x.get("start_min") or 0,
                             x.get("channel") or ""))
    return out


def group_by_channel(items: List[Dict[str, Any]]
                     ) -> List[Tuple[str, str, List[Dict[str, Any]]]]:
    """Vrati [(channel_id, channel_name, [items...]), ...] serazeno.

    Razeno podle "popularity" kanalu (CT1, CT2, Nova, Prima nahore;
    zbytek alfabeticky).
    """
    groups: Dict[str, List[Dict[str, Any]]] = {}
    names: Dict[str, str] = {}
    for it in items:
        cid = it.get("channel_id") or ""
        if not cid:
            continue
        groups.setdefault(cid, []).append(it)
        names[cid] = it.get("channel") or cid

    # Prioritni razeni
    priority = ["1", "2", "3", "4", "78", "92", "474", "89",  # main
                "18", "24", "560", "558", "559", "226", "331",
                "19", "94"]
    pset = set(priority)

    def sort_key(cid: str) -> Tuple[int, str]:
        if cid in pset:
            return (priority.index(cid), "")
        if str(cid).startswith("x:"):
            return (len(priority), names.get(cid, cid).lower())
        return (len(priority) + 1, names.get(cid, cid).lower())

    sorted_ids = sorted(groups.keys(), key=sort_key)
    out = []
    for cid in sorted_ids:
        items_sorted = sorted(groups[cid], key=lambda x: x.get("start_min", 0))
        out.append((cid, names[cid], items_sorted))
    return out


def get_channel_today(channel_id: str,
                      force_refresh: bool = False,
                      kinds: Optional[List[str]] = None,
                      only_future: bool = True) -> List[Dict[str, Any]]:
    """Vrati program pro jeden kanal na dnes. Filtruje na kinds
    (default jen filmy a serialy + dokumenty) a future.
    """
    if kinds is None:
        kinds = ["film", "series", "documentary", "entertainment"]

    items = fetch_today(force_refresh=force_refresh)
    out: List[Dict[str, Any]] = []
    for it in items:
        if str(it.get("channel_id")) != str(channel_id):
            continue
        if kinds and it.get("kind") not in kinds:
            continue
        if only_future and it.get("is_past"):
            continue
        out.append(it)
    if out:
        out.sort(key=lambda x: x.get("start_min") or 0)
        return _dedupe_tv_items(out)

    # Premium kanal jeste neni v cache (background fetch bezi) -> stahni jen tenhle.
    cid = str(channel_id)
    if cid.startswith("x:"):
        slug = cid[2:]
        label = _PREMIUM_BY_SLUG.get(slug, slug)
        log.info("tv_program: on-demand fetch kanalu %s", slug)
        batch = _fetch_single_extra_channel(slug, label)
        for it in batch:
            if kinds and it.get("kind") not in kinds:
                continue
            if only_future and it.get("is_past"):
                continue
            out.append(it)
        out.sort(key=lambda x: x.get("start_min") or 0)
    return _dedupe_tv_items(out)
