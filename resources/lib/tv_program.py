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
    - Jednou request = vse pro 18+ stanic = vse pro dnes.
    - Cache 2 hodiny (TV listingy se nemeni minutami, sport jen vyjimecne).
    - Manualni "Aktualizovat" tlacitko -> force_refresh=True smaze cache.

UI WORKFLOW:
    1) Klik na "TV program dnes" v hlavnim menu -> view_list_tv_program
    2) View ukaze flat list filmu z prime time (>= 18:00) ze vsech kanalu
    3) Klik na film -> tmdb_play_movie -> Webshare search + quality picker
    4) Filmy z minulosti (past=True) jsou skryte (uz neda smysl spustit)

v0.0.63: novy modul (predtim csfd_tv.py, vyrazene kvuli CSFD CF blocku).
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
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from . import cache

log = logging.getLogger("klempcinema.tv_program")

TV_URL = "https://tvprogram.idnes.cz/"

# Cache TTL: 2 hodiny. TV listingy se aktualizuji per den + nemenny po prime time.
TV_CACHE_TTL = 2 * 3600

# Cache klic bumpujem pri zmene parseru / struktury.
TV_CACHE_KEY = "tv_program:idnes:v1"

# Negativni cache (CF block / network down) - 10 min, neexpiruje rubric
# moc rychle ale i tak nas chrani pred network spamem.
TV_NEG_CACHE_TTL = 10 * 60
TV_NEG_CACHE_KEY = "tv_program:idnes:neg:v1"

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

        out.append({
            "channel_id":  cid,
            "channel":     channel,
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
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_today(force_refresh: bool = False) -> List[Dict[str, Any]]:
    """Stahne TV program na dnes z iDNES. Vraci VSECHNY pořady
    (filmy, serialy, zpravy, ...) - filtry resi UI.

    :param force_refresh: True = ignoruj cache.

    Polozka:
        {
          "channel_id":  "3",
          "channel":     "Nova",
          "title":       "Krokodyl Dundee",
          "time":        "17:30",
          "start_min":   1050,     # minuty od pulnoci
          "length_min":  120,
          "plot":        "Dobrodruzny film Austr. (1986). ...",
          "thumb":       "https://1gr.cz/...jpg",
          "url":         "https://tvprogram.idnes.cz/nova/so-17.30-...",
          "kind":        "film",
          "year":        1986,
          "is_past":     False,
        }
    """
    if not force_refresh:
        cached = cache.cache_get(TV_CACHE_KEY, ttl=TV_CACHE_TTL)
        if cached is not None:
            log.info("tv_program: cache HIT (%d polozek)", len(cached))
            return list(cached)
        # Pokud mame negativni cache (block) v platnosti, vrat prazdny.
        neg = cache.cache_get(TV_NEG_CACHE_KEY, ttl=TV_NEG_CACHE_TTL)
        if neg:
            log.info("tv_program: negativni cache HIT - skipuju fetch")
            return []

    html = _http_get(TV_URL)
    if not html:
        # Ulozim neg cache aby se nespamovaly requesty pri kazdem otevreni.
        cache.cache_set(TV_NEG_CACHE_KEY, {"ts": time.time()})
        return []

    try:
        channel_map = _parse_channel_map(html)
        items = _parse_shows(html, channel_map)
    except Exception as exc:  # noqa: BLE001
        log.exception("tv_program: parse selhal: %s", exc)
        cache.cache_set(TV_NEG_CACHE_KEY, {"ts": time.time()})
        return []

    if not items:
        log.warning("tv_program: parser nenasel ani jednu polozku - "
                    "HTML structure se mohla zmenit.")
        cache.cache_set(TV_NEG_CACHE_KEY, {"ts": time.time()})
        return []

    # v0.0.71: pred ulozenim do cache jeste obohatime TMDB plakaty
    # (jen pro filmy + serialy, ostatni kindy nemaji TMDB zaznam).
    # Tim padem se v UI ukaze hezky filmovy plakat misto iDNES thumbnailu.
    try:
        _enrich_with_tmdb_posters(items)
    except Exception as exc:  # noqa: BLE001
        log.warning("tv_program TMDB enrich selhal: %s", exc)

    cache.cache_set(TV_CACHE_KEY, items)
    log.info("tv_program: fetch + parse OK, %d polozek do cache", len(items))
    return items


def _enrich_with_tmdb_posters(items: List[Dict[str, Any]]) -> None:
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
    enrichable: List[Dict[str, Any]] = []
    for it in items:
        if it.get("is_past"):
            continue
        if it.get("kind") not in ("film", "series"):
            continue
        if not (it.get("title") or "").strip():
            continue
        enrichable.append(it)

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
            if kind == "series":
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
        from concurrent.futures import ThreadPoolExecutor
        workers = min(6, max(1, len(enrichable)))
        with ThreadPoolExecutor(max_workers=workers,
                                thread_name_prefix="tvprog-tmdb") as pool:
            list(pool.map(_enrich_one, enrichable))
    except Exception as exc:  # noqa: BLE001
        log.exception("tv_program enrich pool selhal: %s", exc)
        for it in enrichable:
            if _shutdown.is_shutting_down():
                break
            _enrich_one(it)

    n_with_poster = sum(1 for it in enrichable if it.get("tmdb_poster"))
    log.info("tv_program: TMDB enrich hotovo, %d/%d polozek ma plakat",
             n_with_poster, len(enrichable))


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
        kinds = ["film", "series", "documentary"]

    items = fetch_today(force_refresh=force_refresh)
    out = []
    for it in items:
        if str(it.get("channel_id")) != str(channel_id):
            continue
        if kinds and it.get("kind") not in kinds:
            continue
        if only_future and it.get("is_past"):
            continue
        out.append(it)
    out.sort(key=lambda x: x.get("start_min") or 0)
    return out


def get_channels(force_refresh: bool = False) -> List[Tuple[str, str]]:
    """Vrati [(channel_id, channel_name), ...] pro dnesni program,
    serazeno podle priority.
    """
    items = fetch_today(force_refresh=force_refresh)
    groups = group_by_channel(items)
    return [(cid, name) for (cid, name, _items) in groups]
