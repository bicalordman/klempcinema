# -*- coding: utf-8 -*-
"""
csfd.py
-------
ČSFD (Česko-Slovenská filmová databáze) jako fallback zdroj metadat
pro KlempCinema. ČSFD nemá oficiální veřejné API, proto se HTML
stránky lehce scrapují přes urllib.

Funkce:
    is_enabled()                    -> bool
    search_movie(title, year)       -> dict | None
    search_tv(title)                -> dict | None
    enrich_movie_item(item)         -> dict (in-place)
    enrich_series_item(item)        -> dict (in-place)

Vrácený dict má stejný tvar jako u tmdb.py:
    {
      "title": str,         "year": int | None,
      "plot": str,          "rating": float (0..10),
      "votes": int,         "popularity": float,
      "poster": str url,    "fanart": str url,
      "csfd_id": int,       "csfd_url": str,
    }

Pozor:
    ČSFD má agresivní bot-ochranu. Plugin používá realistický
    User-Agent a posílá jen pomalé sériové requesty (nikdy nesmí
    zaplavit ČSFD). Plus všechny výsledky se cachují 7 dní.
"""

from __future__ import annotations

import json
import logging
import re
from html import unescape
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urljoin
from urllib.request import Request, urlopen

from . import cache
from . import clean_title as _ct

log = logging.getLogger("klempcinema.csfd")

BASE = "https://www.csfd.cz"
SEARCH_URL = "https://www.csfd.cz/hledat/?q={q}"
TIMEOUT = 4  # v0.0.81: 4s (drive 5s) - rychlejsi shutdown

HEADERS = {
    # Mobile Android Chrome UA - empirics: ČSFD Cloudflare pousti
    # mobile UA mnohem castěji nez desktop scraper UA (v0.0.56).
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "cs-CZ,cs;q=0.9,sk;q=0.7,en;q=0.6",
    "Accept-Encoding": "gzip, deflate",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "max-age=0",
}

HIT_TTL = 7 * 86400      # 7 dní – pozitivní nálezy
EMPTY_TTL = 6 * 3600     # 6 hod – záznamy "nenalezeno" (true negative)
BLOCKED_TTL = 5 * 60     # 5 min - cache pro self_test() když ČSFD blokuje

# Markery, podle kterých poznáme, že ČSFD vrátil Cloudflare anti-bot
# stránku místo skutečného obsahu. Bezpečně NEvytváříme pozitivní cache
# pro takové response - jinak by jeden "bot challenge" zablokoval
# výsledky na 6 hodin.
_BOT_CHALLENGE_MARKERS = (
    "making sure you're not a bot",
    "just a moment",
    "checking your browser",
    "cf-browser-verification",
    "cf-challenge-running",
    "cloudflare",
    "captcha-bypass",
)


def _looks_like_bot_challenge(html: Optional[str]) -> bool:
    """True pokud HTML vypadá jako Cloudflare/anti-bot challenge stránka."""
    if not html:
        return False
    sample = html[:4096].lower()
    return any(marker in sample for marker in _BOT_CHALLENGE_MARKERS)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def _addon_safe():
    try:
        import xbmcaddon  # type: ignore
        return xbmcaddon.Addon()
    except Exception:  # noqa: BLE001
        return None


def is_enabled() -> bool:
    addon = _addon_safe()
    if addon is None:
        return False
    return (addon.getSetting("csfd_enabled") or "true").lower() in ("true", "1")


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _http_get(url: str) -> Optional[str]:
    """
    GET stránky jako text. Při chybě (vč. 403/429) vrací None.
    Pokud ČSFD vrátí Cloudflare 'anti-bot' challenge stránku,
    vrací None a nastaví session-level flag _BLOCKED, aby se na
    pár minut přestalo ČSFD bombardovat.
    """
    global _BLOCKED_UNTIL
    if _BLOCKED_UNTIL and _BLOCKED_UNTIL > _now():
        return None

    req = Request(url, headers=HEADERS)
    try:
        with urlopen(req, timeout=TIMEOUT) as resp:
            data = resp.read()
            # Gzip dekomprese pokud server vraci komprimovany payload
            # (ten header Accept-Encoding: gzip nutime poslat, jinak nas
            # ČSFD rozezna jako bot - viz v0.0.56).
            ce = (resp.headers.get("Content-Encoding") or "").lower()
            if "gzip" in ce:
                import gzip as _gzip
                try:
                    data = _gzip.decompress(data)
                except Exception as exc:  # noqa: BLE001
                    log.debug("csfd gzip decompress fail: %s", exc)
            elif "deflate" in ce:
                import zlib as _zlib
                try:
                    data = _zlib.decompress(data)
                except Exception:  # noqa: BLE001
                    try:
                        data = _zlib.decompress(data, -_zlib.MAX_WBITS)
                    except Exception as exc:  # noqa: BLE001
                        log.debug("csfd deflate decompress fail: %s", exc)
            ct = (resp.headers.get("Content-Type") or "").lower()
            if "charset=" in ct:
                enc = ct.split("charset=", 1)[1].split(";")[0].strip() or "utf-8"
            else:
                enc = "utf-8"
            try:
                html = data.decode(enc, errors="replace")
            except LookupError:
                html = data.decode("utf-8", errors="replace")

            if _looks_like_bot_challenge(html):
                _BLOCKED_UNTIL = _now() + BLOCKED_TTL
                log.warning("ČSFD %s: Cloudflare bot challenge - "
                            "blokuji ČSFD na 5 minut.", url)
                return None
            return html
    except HTTPError as exc:
        if exc.code in (403, 429, 503):
            _BLOCKED_UNTIL = _now() + BLOCKED_TTL
            log.warning("ČSFD %s: HTTP %s -> blokuji ČSFD na 5 min.",
                        url, exc.code)
        else:
            log.warning("ČSFD %s HTTP %s", url, exc.code)
        return None
    except URLError as exc:
        log.warning("ČSFD %s síťová chyba: %s", url, exc)
        return None
    except Exception as exc:  # noqa: BLE001
        log.exception("ČSFD %s: %s", url, exc)
        return None


# Session-level "ČSFD blokuje" timer. Když nás Cloudflare odežene,
# 5 minut se ho nedotýkáme (šetříme čas i bot-skóre).
import time as _time
_BLOCKED_UNTIL: float = 0.0


def _now() -> float:
    return _time.time()


def self_test() -> Dict[str, Any]:
    """
    Otestuje, jestli ČSFD vůbec odpovídá smysluplným obsahem.
    Cacheováno 5 min, aby self-test neběžel při každém otevření menu.

    Vrací:
        {"ok": bool, "reason": str, "status": "ok"|"blocked"|"network"|"unknown"}
    """
    if not is_enabled():
        return {"ok": False, "reason": "ČSFD je v nastavení vypnuté.",
                "status": "disabled"}

    key = "csfd:self_test"
    cached = cache.cache_get(key, ttl=BLOCKED_TTL)
    if cached:
        return cached

    # Lehký request na hlavní stránku - krátký, neuvádíme search.
    test_url = "https://www.csfd.cz/"
    req = Request(test_url, headers=HEADERS)
    try:
        with urlopen(req, timeout=TIMEOUT) as resp:
            data = resp.read(8192)  # jen prvních 8KB stačí na detekci challenge
            try:
                html = data.decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                html = ""
            if _looks_like_bot_challenge(html):
                out = {"ok": False, "status": "blocked",
                       "reason": "ČSFD blokuje plugin přes Cloudflare anti-bot "
                                 "ochranu. Plakáty bude dodávat jen TMDB."}
            elif "csfd" in html.lower() or "<title" in html.lower():
                out = {"ok": True, "status": "ok",
                       "reason": "ČSFD odpovídá normálně."}
            else:
                out = {"ok": False, "status": "unknown",
                       "reason": "ČSFD vrátil podivnou odpověď."}
    except HTTPError as exc:
        out = {"ok": False, "status": "blocked",
               "reason": f"ČSFD HTTP {exc.code} (anti-bot)."}
    except URLError as exc:
        out = {"ok": False, "status": "network",
               "reason": f"ČSFD nedostupné: {exc.reason}"}
    except Exception as exc:  # noqa: BLE001
        out = {"ok": False, "status": "unknown",
               "reason": f"ČSFD self-test selhal: {exc}"}

    cache.cache_set(key, out)
    log.info("csfd.self_test() -> %s", out)
    return out


# ---------------------------------------------------------------------------
# HTML parsing helpers
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean_text(s: str) -> str:
    if not s:
        return ""
    s = _TAG_RE.sub(" ", s)
    s = unescape(s)
    s = _WS_RE.sub(" ", s).strip()
    return s


# První 'odkaz na film' v search výsledcích vypadá takto:
#   <article class="article article-poster-60 ..."> ... 
#     <a class="film-title-name" href="/film/123456-joker/">Joker</a>
# Search ale obsahuje i seriály - liší se sekcí ('Filmy' / 'Seriály').
_SEARCH_LINK_RE = re.compile(
    r'<a[^>]+class="[^"]*film-title-name[^"]*"[^>]+href="(?P<href>/film/[^"]+)"[^>]*>(?P<title>[^<]+)</a>',
    re.IGNORECASE,
)
_SEARCH_YEAR_RE = re.compile(
    r'<a[^>]+class="[^"]*film-title-name[^"]*"[^>]+href="/film/(?P<id>\d+)[^"]*"[^>]*>'
    r'[^<]+</a>\s*(?:<span[^>]*>)?\s*\((?P<year>\d{4})\)',
    re.IGNORECASE,
)
# Také hledáme typ (film / seriál) z thumbnailu
_SEARCH_KIND_RE = re.compile(
    r'<a[^>]+href="/film/(?P<id>\d+)[^"]*"[^>]*>[^<]*</a>[^<]*'
    r'<[^>]+class="[^"]*(?P<kind>tv-series|series)[^"]*"',
    re.IGNORECASE,
)


def _parse_search(html: str, want_tv: bool = False) -> List[Dict[str, Any]]:
    """Z HTML search stránky vrátí seznam kandidátů: {csfd_id, title, year, url}."""
    out: List[Dict[str, Any]] = []
    if not html:
        return out

    # Najdi všechny film-title-name odkazy
    for m in _SEARCH_LINK_RE.finditer(html):
        href = m.group("href")
        title = _clean_text(m.group("title"))
        id_match = re.search(r"/film/(\d+)", href)
        if not id_match:
            continue
        csfd_id = int(id_match.group(1))

        # Roky se obvykle objevují za odkazem v <span class="info">(2019)</span>
        year = None
        ym = re.search(
            rf'/film/{csfd_id}[^"]*"[^>]*>[^<]+</a>\s*<span[^>]*>\s*\((\d{{4}})\)',
            html,
        )
        if ym:
            try:
                year = int(ym.group(1))
            except ValueError:
                pass

        out.append({
            "csfd_id": csfd_id,
            "title":   title,
            "year":    year,
            "url":     urljoin(BASE, href),
        })

    return out


# Z detailní stránky filmu vytahujeme:
#   - rating: <div class="film-rating-average">85%</div>
#   - votes:  <a href="/film/123/hodnoceni/">12 345 hodnocení</a>
#   - plot:   <p class="..."> uvnitř <div class="plot-full">
#   - poster: <img class="film-poster" src="...">
#   - year:   <div class="origin"> 2019 ...</div>

_RATING_RE = re.compile(
    r'<div[^>]+class="[^"]*film-rating-average[^"]*"[^>]*>\s*(\d{1,3})\s*%',
    re.IGNORECASE,
)
# Fallback regexy - ČSFD HTML strukturu meni casto, takze mame
# několik variant. Hledame "(cislo)%" v ratings sekcich.
_RATING_RE_ALT = re.compile(
    r'<div[^>]+class="[^"]*(?:mobile-)?(?:film-)?rating(?:-average)?[^"]*"'
    r'[^>]*>(?:[^<]*<[^>]+>)*\s*(\d{1,3})\s*%',
    re.IGNORECASE | re.DOTALL,
)
# Schema.org microdata - ČSFD obcas vystavuje:
_RATING_RE_JSON = re.compile(
    r'"ratingValue"\s*:\s*"?(\d{1,3}(?:\.\d+)?)"?',
    re.IGNORECASE,
)
# OG meta - meta tagy pro share preview obcas obsahuji rating:
_RATING_RE_META = re.compile(
    r'<meta[^>]+(?:name|property)="[^"]*rating[^"]*"[^>]+content="(\d{1,3})',
    re.IGNORECASE,
)
_VOTES_RE = re.compile(
    r'href="[^"]*hodnoceni[^"]*"[^>]*>\s*([\d\s\u00a0]+)\s*hodnocen',
    re.IGNORECASE,
)
_POSTER_RE = re.compile(
    r'<img[^>]+class="[^"]*film-poster[^"]*"[^>]+src="([^"]+)"',
    re.IGNORECASE,
)
_POSTER_RE2 = re.compile(
    r'<picture[^>]*>\s*<source[^>]+srcset="([^"\s]+)',
    re.IGNORECASE,
)
# v0.0.57: OG meta poster (universal fallback - ČSFD ho ma vzdy
# pro share preview, i kdyz desktop poster regex selze).
_POSTER_RE_OG = re.compile(
    r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"',
    re.IGNORECASE,
)
# Mobile-CSFD specific
_POSTER_RE_MOB = re.compile(
    r'<img[^>]+(?:class="[^"]*(?:mobile-)?poster[^"]*"|alt="[^"]*poster[^"]*")'
    r'[^>]*src="([^"]+)"',
    re.IGNORECASE,
)
_PLOT_RE = re.compile(
    r'<div[^>]+class="[^"]*plot-full[^"]*"[^>]*>(.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)
_PLOT_SHORT_RE = re.compile(
    r'<div[^>]+class="[^"]*plot-preview[^"]*"[^>]*>(.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)
_YEAR_DETAIL_RE = re.compile(
    r'<div[^>]+class="[^"]*origin[^"]*"[^>]*>.*?(\d{4})',
    re.IGNORECASE | re.DOTALL,
)


def _parse_film(html: str) -> Dict[str, Any]:
    """Z HTML detailní stránky filmu vytáhne metadata."""
    out: Dict[str, Any] = {}
    if not html:
        return out

    # Fallback retez - vyzkousej 4 ruzne regexy v poradi spolehlivosti.
    pct = None
    for rx in (_RATING_RE, _RATING_RE_ALT, _RATING_RE_META):
        m = rx.search(html)
        if m:
            try:
                pct = int(m.group(1))
                if 0 <= pct <= 100:
                    break
                pct = None
            except ValueError:
                continue
    # JSON varianta vraci 0..10 ratingValue rovnou
    if pct is None:
        m = _RATING_RE_JSON.search(html)
        if m:
            try:
                val = float(m.group(1))
                if val > 10:  # ČSFD ratingValue jako procenta
                    pct = int(val)
                elif val > 0:  # ratingValue 0..10
                    out["rating_10"] = round(val, 1)
            except ValueError:
                pass
    if pct is not None:
        out["rating"] = round(pct / 10.0, 1)
        out["rating_pct"] = pct  # zachovat puvodni procenta pro UI ("78 %")

    m = _VOTES_RE.search(html)
    if m:
        try:
            digits = re.sub(r"\D", "", m.group(1))
            out["votes"] = int(digits) if digits else 0
        except ValueError:
            pass

    # v0.0.57: Vice fallback regexu - desktop, mobile, OG meta.
    # ČSFD HTML meni strukturu casto. OG meta je nejvic stabilni
    # (existuje pro share preview vzdy, i kdyz jednotlive class
    # selectory se prepisuji).
    m = (
        _POSTER_RE.search(html)
        or _POSTER_RE_MOB.search(html)
        or _POSTER_RE2.search(html)
        or _POSTER_RE_OG.search(html)
    )
    if m:
        src = m.group(1)
        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/"):
            src = urljoin(BASE, src)
        out["poster"] = src
        # ČSFD neposkytuje samostatný fanart; použijeme stejný plakát.
        out["fanart"] = src

    m = _PLOT_RE.search(html) or _PLOT_SHORT_RE.search(html)
    if m:
        plot = _clean_text(m.group(1))
        # ČSFD plot často končí "...(více)" nebo "(zdroj: ...)" – pryč s tím.
        plot = re.sub(r"\(zdroj[^\)]*\)\s*$", "", plot, flags=re.I).strip()
        plot = re.sub(r"\.\.\.\s*\(v[íi]ce\)\s*$", "...", plot, flags=re.I).strip()
        out["plot"] = plot

    m = _YEAR_DETAIL_RE.search(html)
    if m:
        try:
            out["year"] = int(m.group(1))
        except ValueError:
            pass

    return out


# ---------------------------------------------------------------------------
# Veřejné API: search + enrich
# ---------------------------------------------------------------------------

def _pick_candidate(
    candidates: List[Dict[str, Any]],
    target_title: str,
    target_year: Optional[int],
    *,
    strict: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Vybere nejvhodnejsiho kandidata podle podobnosti nazvu + shody roku.

    :param strict: v0.0.59 - kdyz True, vyzaduje shodu roku (+/-1) NEBO
                   silnou title-shodu (>=60 score). Pouziva se ve
                   split-fallback rezimu, kde search query je jen cast
                   nazvu (napr. "Shelter" z "V úkrytu Shelter") a hrozi
                   pick-of-wrong-film. Year-match je kotva proti tomu.
    """
    if not candidates:
        return None
    if not target_title:
        return candidates[0] if not strict else None

    target_norm = re.sub(r"\W+", "", target_title.lower())

    best = None
    best_score = -1
    best_title_score = 0
    best_year_score = 0
    for c in candidates:
        title_score = 0
        cand_norm = re.sub(r"\W+", "", (c.get("title") or "").lower())
        if cand_norm == target_norm:
            title_score = 100
        elif cand_norm.startswith(target_norm) or target_norm.startswith(cand_norm):
            title_score = 60
        elif target_norm in cand_norm or cand_norm in target_norm:
            title_score = 30

        year_score = 0
        if target_year and c.get("year") == target_year:
            year_score = 50
        elif target_year and c.get("year") and abs(c["year"] - target_year) <= 1:
            year_score = 20

        score = title_score + year_score
        if score > best_score:
            best_score = score
            best = c
            best_title_score = title_score
            best_year_score = year_score

    if best is None:
        return None

    # Strict mode (v0.0.61): VYZADUJ year-match (+/-1). Title-only match
    # je nedostacujici - "Toy Story" search vraci 5+ kandidatu se 100%
    # title-shodou, ale jen 1 ma spravny rok. Bez year-match by se
    # picknul nahodny Toy Story 1995 misto Toy Story That Time Forgot
    # 2014. Lepsi nic nez chybna data.
    if strict and best_year_score == 0:
        log.info("csfd: strict pick zamitnut (year mismatch) - title=%d, year=%d",
                 best_title_score, best_year_score)
        return None

    return best


def _csfd_search_once(q: str, want_tv: bool) -> List[Dict[str, Any]]:
    """
    Jeden ČSFD search request s daným dotazem. Vrací list kandidátů
    (může být prázdný). None signalizuje síťovou chybu / Cloudflare
    blokaci - rozdílné od "0 výsledků".
    """
    url = SEARCH_URL.format(q=quote_plus(q))
    log.info("csfd: search url=%s", url)
    html = _http_get(url)
    if html is None:
        return []
    return _parse_search(html, want_tv=want_tv)


def _search_csfd(title: str, year: Optional[int] = None,
                 want_tv: bool = False) -> Optional[Dict[str, Any]]:
    """
    Vyhledá film/seriál na ČSFD. Vrací dict s metadaty nebo None.
    Cacheováno: úspěšné nálezy 7 dní, prázdné 6 hodin.

    v0.0.58: MULTI-RETRY strategie pro Vyšehrad-style české filmy.
    Když 1. dotaz nic nenajde, postupně se zkouší slabší varianty:

        Try 1: clean_title + rok               ("Vyšehrad 2022")
        Try 2: clean_title bez roku            ("Vyšehrad")
        Try 3: ASCII-fold + rok                ("Vysehrad 2022")
        Try 4: letters-only fallback           ("Vysehrad")

    Dřív stačil "Vyšehrad Fylm 5 1" zbytek po neúplném čištění a ČSFD
    nenašlo nic. Teď nový clean_title vrátí "Vyšehrad Fylm" a první
    pokus uspěje; pokud i to selže, ASCII-fold + letters-only zachrání
    většinu zbytku.
    """
    if not title:
        return None

    if not year:
        year = _ct.extract_year(title)

    clean = _ct.clean_title(title)
    if not clean:
        return None

    # v6 cache key (v0.0.61) - rok ODEBRAN z ČSFD search query
    # (user feedback). Stara v5 cache by drzela negative-y. Bumpneme.
    key = f"csfd:v6:{'tv' if want_tv else 'film'}:{clean.lower()}:{year or ''}"
    cached = cache.cache_get(key, ttl=HIT_TTL)
    if cached:
        return cached

    # ---- Multi-retry search (v0.0.61: BEZ roku v query) ----
    # User feedback: "bylo by dobre aby pri hledani na csfd se za
    # nazvem neobjevovali cislovky. a bral se jen cesky nebo anglicky
    # nazev jeden z nich. Pokud to nenajde cesky tak to najde anglicky."
    # Rok v search query casto rozmaze ČSFD fuzzy match (zvlast kdyz
    # Webshare year je upload year a ne release year). Rok pouzivame
    # az v _pick_candidate pro disambiguaci kandidatu se stejnym nazvem.
    folded = _ct.ascii_fold(clean)
    letters = _ct.letters_only_title(clean)
    attempts: List[str] = []

    # 1) Cesky clean title (i s diakritikou) - FIRST try
    attempts.append(clean)
    # 2) ASCII-fold (bez diakritiky) - 2. pokus
    if folded.lower() != clean.lower():
        attempts.append(folded)
    # 3) Letters-only (zadne cislice / "+/-") - 3. pokus
    if (letters and letters.lower() != clean.lower()
            and letters.lower() != folded.lower() and len(letters) >= 3):
        attempts.append(letters)

    # Deduplicate
    seen: set = set()
    unique_attempts: List[str] = []
    for q in attempts:
        ql = q.lower().strip()
        if ql and ql not in seen:
            seen.add(ql)
            unique_attempts.append(q)

    candidates: List[Dict[str, Any]] = []
    used_query = ""
    used_pick_target = clean
    strict_pick = False
    for q in unique_attempts:
        results = _csfd_search_once(q, want_tv)
        if results:
            candidates = results
            used_query = q
            log.info("csfd: nalezeno %d kandidatu pro %r (po %d pokusech)",
                     len(results), q, unique_attempts.index(q) + 1)
            break

    # v0.0.61: SPLIT-FALLBACK bez roku v query.
    # Pro "V úkrytu Shelter": zkousi v poradi
    #   1) "V úkrytu"        (CZ cast - diacritic split)
    #   2) "Shelter"         (EN cast)
    #   3) trim varianty
    # Vse bez "2024" suffixu - cisty title.
    if not candidates:
        splits = _ct.title_split_variants(clean)
        for q in splits[:5]:
            results = _csfd_search_once(q, want_tv)
            if results:
                candidates = results
                used_query = q
                used_pick_target = q
                strict_pick = True
                log.info("csfd: split-fallback %r nasel %d kandidatu",
                         q, len(results))
                break

    if not candidates:
        log.info("csfd: zadne vysledky pro %r (zkousel jsem: %r)",
                 clean, unique_attempts)
        cache.cache_set(key, {})
        return None

    pick = _pick_candidate(candidates, used_pick_target, year, strict=strict_pick)
    if not pick:
        log.info("csfd: pick zamitnut (strict=%s) pro query=%r",
                 strict_pick, used_query)
        cache.cache_set(key, {})
        return None

    # Detailní stránka
    detail_html = _http_get(pick["url"])
    if detail_html is None:
        # Cloudflare blokuje detail - nevracíme prázdno do cache,
        # zkusíme příště.
        return None
    detail = _parse_film(detail_html)

    # Kompletni payload + samostatne csfd_rating/poster pro UI.
    csfd_rating = float(detail.get("rating") or 0.0)
    csfd_poster = detail.get("poster", "")
    result = {
        "csfd_id":     pick["csfd_id"],
        "csfd_url":    pick["url"],
        "title":       pick.get("title") or title,
        "year":        detail.get("year") or pick.get("year") or year,
        "plot":        detail.get("plot", ""),
        "rating":      csfd_rating,                    # 0..10 (kompat)
        "csfd_rating": csfd_rating,                    # 0..10 (samostatne)
        "csfd_rating_pct": int(detail.get("rating_pct") or 0),  # 0..100 pro UI
        "csfd_votes":  int(detail.get("votes") or 0),
        "votes":       int(detail.get("votes") or 0),
        "popularity":  float(detail.get("votes") or 0) / 1000.0,
        "poster":      csfd_poster,
        "csfd_poster": csfd_poster,  # v0.0.57: samostatne pro UI fallback
        "fanart":      detail.get("fanart", ""),
        "_query_used": used_query,   # debug: kterym dotazem jsme nasli
    }

    cache.cache_set(key, result)
    return result


def search_movie(title: str, year: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """ČSFD search pro film."""
    return _search_csfd(title, year, want_tv=False)


def search_tv(title: str) -> Optional[Dict[str, Any]]:
    """ČSFD search pro seriál."""
    return _search_csfd(title, None, want_tv=True)


def _merge_meta(item: Dict[str, Any], meta: Dict[str, Any]) -> None:
    """
    Doplň item ČSFD daty.

    v0.0.56: csfd_rating uloženo VŽDY (i kdyz TMDB rating uz je) jako
    samostatne pole - UI pak ukaze OBE hodnoceni vedle sebe.
    Ostatni pole jen jako fallback (TMDB ma prioritu).
    """
    # Bonus ČSFD rating - VZDY ulozit, ne jen jako fallback
    if meta.get("csfd_rating") is not None:
        item["csfd_rating"] = float(meta["csfd_rating"])
        item["csfd_rating_pct"] = int(meta.get("csfd_rating_pct") or 0)
        item["csfd_votes"] = int(meta.get("csfd_votes") or 0)
        item["csfd_url"] = meta.get("csfd_url") or ""
    # v0.0.57: csfd_poster jako separate field - i kdyz TMDB ma poster,
    # ulozime ČSFD jako alternativu (UI muze nabidnout 'Zmenit poster
    # na ČSFD' v context menu, nebo automaticky fallback kdyz TMDB poster
    # selhal).
    if meta.get("csfd_poster"):
        item["csfd_poster"] = meta["csfd_poster"]

    # v0.0.59: Cesky ČSFD nazev MA PREDNOST pred anglickym TMDB nazvem.
    # Drive: TMDB beziaci jako prvni mohl vratit en-US nazev (kdyz cs
    # preklad nebyl v TMDB) - to se ulozilo do title_localized. Pak
    # ČSFD vratilo cesky nazev, ale _merge_meta ho NEPREPSAL kvuli
    # `not item.get("title_localized")` check. User pak videl
    # "Shelter" misto "V úkrytu".
    #
    # Nove: pokud ČSFD nazev obsahuje ceskou diakritiku A current
    # title_localized ji neobsahuje, ČSFD vyhrava. Ceske filmy se
    # pak zobrazi se spravnym ceskym nazvem.
    csfd_title = (meta.get("title") or "").strip()
    if csfd_title:
        current = (item.get("title_localized") or "").strip()
        if not current:
            item["title_localized"] = csfd_title
        else:
            csfd_has_diacritic = bool(_ct._CZ_DIACRITIC_RE.search(csfd_title))
            current_has_diacritic = bool(_ct._CZ_DIACRITIC_RE.search(current))
            # ČSFD ma diakritiku, soucasny nazev (z TMDB) ne -> prebij
            if csfd_has_diacritic and not current_has_diacritic:
                item["title_localized"] = csfd_title
    if meta.get("year") and not item.get("year"):
        item["year"] = meta["year"]
    if meta.get("plot") and not item.get("plot"):
        item["plot"] = meta["plot"]
    if meta.get("poster") and not item.get("poster"):
        item["poster"] = meta["poster"]
    if meta.get("fanart") and not item.get("fanart"):
        item["fanart"] = meta["fanart"]
    if meta.get("rating") and not item.get("rating"):
        item["rating"] = float(meta["rating"])
    if meta.get("votes") and not item.get("votes"):
        item["votes"] = int(meta["votes"])
    if meta.get("popularity") and not item.get("popularity"):
        item["popularity"] = float(meta["popularity"])
    if meta.get("csfd_id"):
        item["csfd_id"] = meta["csfd_id"]


def enrich_movie_item(item: Dict[str, Any]) -> Dict[str, Any]:
    if not is_enabled():
        return item
    # v0.0.62: SHORTCUT - pokud uz mame csfd_url + csfd_rating, drive
    # bezel ČSFD lookup, neopakovat (setri Cloudflare bot detection).
    if item.get("csfd_url") and (item.get("csfd_rating") or item.get("csfd_poster")):
        return item
    try:
        meta = search_movie(item.get("title") or "", item.get("year"))
        if meta:
            _merge_meta(item, meta)
    except Exception as exc:  # noqa: BLE001
        log.debug("csfd.enrich_movie_item(%r) selhalo: %s", item.get("title"), exc)
    return item


def enrich_series_item(item: Dict[str, Any]) -> Dict[str, Any]:
    if not is_enabled():
        return item
    # v0.0.62: SHORTCUT - skip pokud uz mame CSFD data
    if item.get("csfd_url") and (item.get("csfd_rating") or item.get("csfd_poster")):
        return item
    try:
        meta = search_tv(item.get("title") or "")
        if meta:
            _merge_meta(item, meta)
    except Exception as exc:  # noqa: BLE001
        log.debug("csfd.enrich_series_item(%r) selhalo: %s", item.get("title"), exc)
    return item
