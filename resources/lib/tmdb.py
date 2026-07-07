# -*- coding: utf-8 -*-
"""
tmdb.py
-------
TMDB (The Movie Database) integrace pro získání:
    - posterů (IMDB-style)
    - hodnocení (vote_average, vote_count)
    - popularity (vote / view based)
    - lokalizovaných názvů a popisů
    - roku a fanartu

Uživatel si v nastavení vyplní vlastní TMDB API klíč (zdarma na
https://www.themoviedb.org/settings/api). Bez klíče plugin pracuje
dál, jen bez plakátů/hodnocení.

Veřejné rozhraní:
    is_enabled()                    -> bool
    search_movie(title, year)       -> dict | None
    search_tv(title)                -> dict | None
    enrich_movie_item(item)         -> dict  (in-place doplnění)
    enrich_series_item(item)        -> dict
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import cache
from . import clean_title as _ct

# Prázdný TMDB výsledek se cachuje jen krátce, aby se po vyplnění klíče
# (nebo po síťovém výpadku) brzo zkusilo znovu.
EMPTY_TTL = 3 * 3600  # 3 hodiny (drive 1h; mene znovu-hledani po misses)
HIT_TTL = 30 * 86400  # 30 dni (drive 7; meta filmu se meni ridke,
                      # 30dni je bezpecne a UI klikani je instant)

log = logging.getLogger("klempcinema.tmdb")

TMDB_BASE = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p"
POSTER_SIZE = "w500"
FANART_SIZE = "w1280"

DEFAULT_LANG = "cs-CZ"
TIMEOUT = 4  # v0.0.81: 4s - rychlejsi shutdown

# Session-level "TMDB is broken" flag. Po MAX_FAILURES selháních
# (401/403/network) přestaneme TMDB volat až do restartu pluginu -
# šetříme čas a nebombardujeme API.
MAX_FAILURES = 3
_failure_counter = 0
_session_disabled = False


# ---------------------------------------------------------------------------
# Konfigurace ze settings
# ---------------------------------------------------------------------------

def _addon_safe():
    try:
        import xbmcaddon  # type: ignore
        return xbmcaddon.Addon()
    except Exception:  # noqa: BLE001
        return None


# v0.0.79: User-Agent je nyni dynamicky - cte verzi z addon.xml.
# Pri kazdem bumpu verze (0.0.79 -> 0.0.80) se UA aktualizuje sam,
# zadne hledani po hardcoded "0.0.X" strings.
def _user_agent() -> str:
    addon = _addon_safe()
    ver = "unknown"
    if addon is not None:
        try:
            ver = addon.getAddonInfo("version") or "unknown"
        except Exception:  # noqa: BLE001
            pass
    return f"KlempCinema/{ver} (+Kodi)"


_LANG_OPTIONS = {"0": "cs-CZ", "1": "en-US", "2": "sk-SK"}

# Vestavěný TMDB v3 API klíč - plugin funguje out-of-the-box i bez
# vyplnění v Settings. User si může v Settings přepsat vlastním
# (např. když chce vlastní quota / V4 Bearer token).
#
# POZN: Tento klíč je 32-hex (v3). Pokud bys chtěl V4 Bearer,
# nahraď v _config() v podmínce pod tím.
BUILTIN_TMDB_API_KEY = "9c9da6743b17c29df8df86c7821d5473"


def _config() -> Dict[str, Any]:
    addon = _addon_safe()
    if addon is None:
        return {"api_key": BUILTIN_TMDB_API_KEY, "lang": DEFAULT_LANG, "enabled": True}
    enabled_raw = (addon.getSetting("tmdb_enabled") or "true").lower()
    lang_raw = addon.getSetting("tmdb_lang") or "0"
    # Pokud user vyplnil vlastní klíč, použijeme jeho. Jinak fallback
    # na vestavěný klíč - plugin tak funguje hned po instalaci.
    user_key = (addon.getSetting("tmdb_api_key") or "").strip()
    api_key = user_key or BUILTIN_TMDB_API_KEY
    return {
        "api_key": api_key,
        "lang":    _LANG_OPTIONS.get(lang_raw, DEFAULT_LANG),
        "enabled": enabled_raw in ("true", "1"),
    }


def is_enabled() -> bool:
    """
    TMDB je 'zapnuté', pokud je v settings zapnuté A je nějaký klíč A
    nebyl v této session vypnuto kvůli opakovaným chybám.
    """
    if _session_disabled:
        return False
    cfg = _config()
    return bool(cfg["enabled"] and cfg["api_key"])


def _is_v4_token(key: str) -> bool:
    """
    TMDB má dva typy autorizace:
        - v3 API Key  = 32 hex znaků (např. 'a1b2c3d4e5f6...')
        - v4 Bearer   = ~200 znaků JWT (např. 'eyJhbGciOiJIUzI1NiJ9...')

    Uživatelé je často zaměňují. Klíč >= 64 znaků s tečkou je téměř
    jistě JWT v4 Bearer token.
    """
    if not key:
        return False
    return len(key) >= 64 and key.count(".") >= 2 and key.startswith("ey")


def _bump_failure(why: str) -> None:
    """Zaznamenej selhání TMDB. Po MAX_FAILURES vypneme TMDB pro session."""
    global _failure_counter, _session_disabled
    _failure_counter += 1
    log.warning("TMDB failure #%d (%s)", _failure_counter, why)
    if _failure_counter >= MAX_FAILURES and not _session_disabled:
        _session_disabled = True
        log.error("TMDB vypnuto pro tuto session - %d selhání. "
                  "Plugin spadne na ČSFD fallback.", _failure_counter)


def _reset_failures() -> None:
    global _failure_counter
    if _failure_counter:
        _failure_counter = 0


def session_disabled() -> bool:
    return _session_disabled


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _http_get(path: str, **params: Any) -> Optional[Dict[str, Any]]:
    cfg = _config()
    if not cfg["api_key"] or _session_disabled:
        return None

    params.setdefault("language", cfg["lang"])
    params.setdefault("include_adult", "false")

    headers: Dict[str, str] = {
        "Accept": "application/json",
        "User-Agent": _user_agent(),
    }

    # Auto-detekce v3 vs v4 - klíč se k API předá adekvátně.
    if _is_v4_token(cfg["api_key"]):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    else:
        params.setdefault("api_key", cfg["api_key"])

    url = (
        f"{TMDB_BASE}{path}"
        f"?{urlencode({k: v for k, v in params.items() if v not in (None, '')})}"
    )
    req = Request(url, headers=headers)

    try:
        with urlopen(req, timeout=TIMEOUT) as resp:
            _reset_failures()
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:  # noqa: BLE001
            pass
        log.error("TMDB %s HTTP %s: %s", path, exc.code, body)
        # 401/403 = špatný API key/token - okamžitě vypni TMDB pro session.
        if exc.code in (401, 403):
            _bump_failure(f"HTTP {exc.code}")
            _bump_failure("force-off")
            _bump_failure("force-off")
        else:
            _bump_failure(f"HTTP {exc.code}")
        return None
    except URLError as exc:
        log.error("TMDB %s síťová chyba: %s", path, exc)
        _bump_failure(f"net: {exc}")
        return None
    except Exception as exc:  # noqa: BLE001
        log.exception("TMDB %s: %s", path, exc)
        _bump_failure(f"exc: {exc}")
        return None


# ---------------------------------------------------------------------------
# Self-test (na start pluginu)
# ---------------------------------------------------------------------------

_SELF_TEST_TTL = 300  # 5 minut


def self_test() -> Dict[str, Any]:
    """
    Otestuje TMDB klíč jediným requestem na /configuration.
    Vrací dict {ok, reason, key_type}.

    Výsledek se cachuje 5 minut, ať nezdržujeme každé otevření menu.
    """
    cfg = _config()
    if not cfg["enabled"]:
        return {"ok": False, "reason": "TMDB disabled in settings", "key_type": "none"}
    if not cfg["api_key"]:
        return {"ok": False, "reason": "TMDB API key is empty", "key_type": "none"}

    key_type = "v4" if _is_v4_token(cfg["api_key"]) else "v3"
    cache_key = f"tmdb:selftest:{key_type}:{len(cfg['api_key'])}"

    cached = cache.cache_get(cache_key, ttl=_SELF_TEST_TTL)
    if cached:
        return cached

    # Reset session flag pro test - chceme zkusit znovu i po předchozím selhání.
    global _session_disabled, _failure_counter
    saved_disabled = _session_disabled
    saved_failures = _failure_counter
    _session_disabled = False
    _failure_counter = 0

    data = _http_get("/configuration")

    if data and data.get("images"):
        result = {"ok": True, "reason": "OK", "key_type": key_type}
    else:
        # Vrať session flagy zpět - chceme, aby se TMDB pro tuto session
        # už nevolal, když self-test selhal.
        _session_disabled = True
        _failure_counter = MAX_FAILURES
        result = {
            "ok": False,
            "reason": f"TMDB ({key_type}) klíč odmítnut - zkontroluj v Settings",
            "key_type": key_type,
        }

    # Pokud byl předtím session_disabled, ale teď self-test prošel,
    # nech ho vypnutý (jen 1 self-test ho neoživuje pro session - chce restart).
    if result["ok"]:
        _session_disabled = False
        _failure_counter = 0
    else:
        # zachovej původní stav, ale s novými hodnotami nastavenými výše
        pass

    cache.cache_set(cache_key, result)
    return result


def _poster_url(rel: Optional[str], size: str = POSTER_SIZE) -> str:
    if not rel:
        return ""
    return f"{IMAGE_BASE}/{size}{rel}"


# ---------------------------------------------------------------------------
# SEARCH – film / seriál
# ---------------------------------------------------------------------------

# Cleaning titles - delegujeme na centralizovaný clean_title modul.
# Tady jsou jen tenké wrappery, kvůli zpětné kompatibilitě jmen.

def _extract_year(t: str) -> Optional[int]:
    """Najde rok 19xx/20xx v textu, vrátí int nebo None."""
    return _ct.extract_year(t)


def _strip_title(t: str) -> str:
    """
    Vyčistí titul pro TMDB search přes centrální clean_title.
    Smaže rok, quality, jazyk markery, release groups, závorky atd.

    Vstup:  "Joker 2019 CZ x264"
    Výstup: "Joker"
    """
    return _ct.clean_title(t)


def search_movie(title: str, year: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """
    Vrátí TMDB metadata pro film. Cacheuje úspěšné nálezy 7 dní,
    prázdné výsledky jen 1 hodinu (aby se rychle zkusilo znovu).

    Strategie (s "originál first" politikou pro lepší match):
        1) Extrahuj rok z titulu (pokud parametr 'year' chybí).
        2) cs-CZ search s rokem -> match
        3) cs-CZ search bez roku -> match
        4) en-US search s rokem (čištění titulu) -> match
        5) en-US search bez roku -> match
        6) region=CZ -> match

    Po nalezení TMDB ID se vždy zavolá get_movie_details(tmdb_id, "cs-CZ")
    pro dotaažení lokalizovaných polí (cs title, plot, plakát). Tím:
      * dostaneme spolehlivý ČESKÝ překlad (pokud TMDB má)
      * dostaneme stabilní originální název (original_title z en)
      * plakáty sedí s originálem (přes _get_best_images fallback)
    """
    if not is_enabled():
        log.debug("search_movie(%r): TMDB disabled (key=%s, enabled=%s)",
                  title, bool(_config()["api_key"]), _config()["enabled"])
        return None

    if not title:
        return None

    if not year:
        year = _extract_year(title)

    clean = _strip_title(title)
    if not clean:
        log.debug("search_movie(%r): po čištění prázdný titul.", title)
        return None

    # v7 cache key (v0.0.61) - split-fallback ted vyzaduje skutecny
    # year-match (ne jen vote-based score). Stara v6 cache by drzela
    # wrong-year matches jako "Toy Story 4" pro "Toy Story Historicky"
    # 2026. Bumpneme.
    # v0.0.69 bump v7 -> v8 - meta dict ted obsahuje genre_ids pole
    # (pro rubrika "Animovane CZ/SK" filtrovani na genre_id 16).
    # Stara cache nema genre_ids, takze by filter selhal na vsem.
    key = f"tmdb:movie:v10:{clean.lower()}:{year or ''}"
    cached = cache.cache_get(key, ttl=HIT_TTL)
    if cached:
        return cached

    log.info("search_movie: query=%r year=%s", clean, year)

    from .title_match import title_search_compatible, title_search_variants
    found = None
    for q in title_search_variants(clean, year):
        cand = _find_movie_tmdb_id(q, year)
        if not cand:
            continue
        _tid, raw = cand
        mt = raw.get("title") or ""
        mo = raw.get("original_title") or ""
        if title_search_compatible(clean, q, mt, mo):
            found = cand
            log.info("search_movie: match pres variantu %r", q)
            break
        log.debug("search_movie: variant %r -> %r nekompatibilni s WS %r",
                  q, mt or mo, clean)
    if not found and year:
        try:
            if int(year) >= datetime.now().year - 1:
                for q in title_search_variants(clean, year):
                    cand = _find_movie_tmdb_id(q, None)
                    if not cand:
                        continue
                    _tid, raw = cand
                    mt = raw.get("title") or ""
                    mo = raw.get("original_title") or ""
                    if title_search_compatible(clean, q, mt, mo):
                        found = cand
                        log.info("search_movie: match bez roku pres %r", q)
                        break
        except (TypeError, ValueError):
            pass
    if not found:
        cache.cache_set(key, {})
        log.warning("search_movie(%r, year=%s): 0 výsledků z TMDB.", clean, year)
        return None

    tmdb_id, raw = found

    # OPTIMIZED (v0.0.40): pokud search vrátil kompletní data (title + overview),
    # použij je rovnou - ušetříme extra /movie/{id} request.
    # Pouze pokud něco chybí (typicky overview pro některé en-US searche),
    # spadneme na _fetch_movie_details_localized.
    raw_title = raw.get("title") or ""
    raw_plot = raw.get("overview") or ""
    raw_poster = raw.get("poster_path") or ""
    raw_fanart = raw.get("backdrop_path") or ""

    if raw_title and raw_plot:
        # Máme vše ze search - jen doplnit obrázky pokud chybí
        if (not raw_poster) or (not raw_fanart):
            best_p, best_f = _get_best_images(tmdb_id, kind="movie")
            if not raw_poster and best_p:
                raw_poster = best_p
            if not raw_fanart and best_f:
                raw_fanart = best_f

        # v0.0.69: ukladame TMDB genre_ids pro per-rubric genre filter
        # (napr. rubrika Animovane CZ/SK filtruje na genre_id 16).
        # TMDB /search/movie vraci 'genre_ids' jako list integeru.
        raw_genre_ids = [int(g) for g in (raw.get("genre_ids") or [])
                         if isinstance(g, (int, float))]

        result = {
            "tmdb_id":    tmdb_id,
            "title":      raw_title,
            "original":   raw.get("original_title") or "",
            "year":       _date_year(raw.get("release_date")) or year,
            "plot":       raw_plot,
            "rating":     float(raw.get("vote_average") or 0),
            "votes":      int(raw.get("vote_count") or 0),
            "popularity": float(raw.get("popularity") or 0),
            "poster":     _poster_url(raw_poster, POSTER_SIZE),
            "fanart":     _poster_url(raw_fanart, FANART_SIZE),
            "genre_ids":  raw_genre_ids,
        }
    else:
        # Search vrátil neúplná data (pravděpodobně en-US search, nebo
        # nový film bez prekladu). Dotáhneme detail v cs-CZ.
        result = _fetch_movie_details_localized(tmdb_id, fallback_title=clean,
                                                fallback_year=year)

    if result:
        cache.cache_set(key, result)
    else:
        cache.cache_set(key, {})
    return result or None


def _find_movie_tmdb_id(query: str, year: Optional[int]) -> Optional[Tuple[int, Dict[str, Any]]]:
    """
    Najde TMDB ID pro film. Vrací (tmdb_id, raw_result_dict) z nejlepšího
    matche, nebo None.

    Strategie v0.0.49 - 'best-of-both' s ranou validaci:
        0) Pre-validace: titul musi byt >= 2 znaky a obsahovat aspon
           jedno pismeno (jinak skip - typicky smetky jako "01" / "x").
        1) cs + year (1 request)
           Pokud skore >= GREAT_SCORE (= rok match + vote_count >= 200),
           hned vratit - velmi pravdepodobne perfect match.
        2) en + year (1 request) - VZDY zkusime i en. Soubory na WS
           casto maji EN nazev a cs API search nemusi byt nejlepsi.
           Vratime ten s vyssim skore.
        3) Pokud po krocich 1+2 jeste nemame nic se skorem >= GOOD_SCORE,
           zkusime jeste cs bez roku + en bez roku jako fallback.
        4) region=CZ - posledni pokus, jen pokud porad nic.

    Tim pokryjeme:
        - cs prelozeny soubor ("Joker 2019 CZ") -> cs search najde
        - en originalni soubor ("Joker 2019 1080p") -> en search najde
        - novinky bez cs prekladu ("New Movie 2026") -> en search najde
        - obskurni rip ("Some.Film.2010.x264") -> oba fallbacky probehnou

    Skore kandidatu:
        +1000 za presny rok, +500 za +/-1 rok, -50*diff jinak,
        + vote_count (popularita).
    """
    GREAT_SCORE = 1200.0  # rok match (1000) + vote_count >= 200
    GOOD_SCORE = 600.0    # rok match nebo vote_count >= 600

    # PRE-VALIDACE: titul musi byt smysluplny
    if not query or len(query) < 2:
        return None
    # Aspon jedno pismeno (jinak je to "2019" nebo "x264")
    if not re.search(r"[A-Za-z\u00c0-\u017f]", query):
        return None

    def _best_from(data: Optional[Dict[str, Any]]) -> Optional[Tuple[float, int, Dict[str, Any]]]:
        if not data or not data.get("results"):
            return None
        results = data["results"][:5]
        scored = []
        from .title_match import fuzzy_title_bonus
        for r in results:
            tid = r.get("id")
            if not tid:
                continue
            score = float(r.get("vote_count") or 0)
            r_year = _date_year(r.get("release_date") or "")
            if year and r_year:
                diff = abs(r_year - year)
                if diff == 0:
                    score += 1000
                elif diff == 1:
                    score += 500
                else:
                    score -= diff * 50
            score += fuzzy_title_bonus(
                query,
                r.get("title"),
                r.get("original_title"),
            )
            scored.append((score, tid, r))
        if not scored:
            return None
        scored.sort(key=lambda x: -x[0])
        return scored[0]

    def _try_better(current, cand):
        """Vrati lepsi kandidat (vyssi skore)."""
        if cand is None:
            return current
        if current is None:
            return cand
        return cand if cand[0] > current[0] else current

    best: Optional[Tuple[float, int, Dict[str, Any]]] = None

    # 1) cs-CZ + year
    params: Dict[str, Any] = {"query": query}
    if year:
        params["year"] = year
    best = _best_from(_http_get("/search/movie", **params))

    # Velmi dobry cs match - nehledame dal.
    if best and best[0] >= GREAT_SCORE:
        return (best[1], best[2])

    # 2) en-US + year - VZDY zkusime, soubory na WS casto maji en nazev.
    # Pri novinkach (2026) cs preklad nemusi existovat -> en je spolehlivejsi.
    params_en: Dict[str, Any] = {"query": query, "language": "en-US"}
    if year:
        params_en["year"] = year
    cand_en = _best_from(_http_get("/search/movie", **params_en))
    best = _try_better(best, cand_en)

    if best and best[0] >= GOOD_SCORE:
        return (best[1], best[2])

    # 3) Fallback bez roku (jen pokud porad nic dobreho)
    if year:
        cand = _best_from(_http_get("/search/movie", query=query))
        best = _try_better(best, cand)
        if best and best[0] >= GOOD_SCORE:
            return (best[1], best[2])

        # en bez roku - posledni pokus pro novinky
        if not best or best[0] < GOOD_SCORE:
            cand = _best_from(_http_get("/search/movie", query=query, language="en-US"))
            best = _try_better(best, cand)

    # 4) region=CZ - posledni pokus
    if not best:
        cand = _best_from(_http_get("/search/movie", query=query, region="CZ"))
        best = _try_better(best, cand)

    # 5) v0.0.58: ASCII-fold fallback - kdyz UTF-8 query nic nenaslo,
    #    zkus diakritiku-free verzi. TMDB obcas ma cesky film indexovany
    #    pod ASCII variantou (zvlast u starsich filmu pred 2010).
    #    Priklad: "Vyšehrad" se nenajde v cs API, ale "Vysehrad" ano.
    folded = _ct.ascii_fold(query)
    if not best and folded.lower() != query.lower():
        log.info("tmdb: query %r 0 vysledku, zkousim ASCII-fold %r",
                 query, folded)
        params_f: Dict[str, Any] = {"query": folded, "language": "cs-CZ"}
        if year:
            params_f["year"] = year
        cand = _best_from(_http_get("/search/movie", **params_f))
        best = _try_better(best, cand)
        if not best:
            # en + ASCII
            cand = _best_from(_http_get("/search/movie",
                                        query=folded, language="en-US"))
            best = _try_better(best, cand)

    # 6) v0.0.58: Letters-only nejagresivnejsi fallback - kdyz porad nic
    #    nemame, posli jen pismena (zadne cislice, zadne separator). Tim
    #    zachranime soubory s reziduy jako "Vyšehrad 5 1" (audio 5.1 ktery
    #    prosakl pres _QUALITY_RE).
    letters = _ct.letters_only_title(query)
    if not best and letters and letters.lower() != query.lower() \
            and letters.lower() != folded.lower() and len(letters) >= 2:
        log.info("tmdb: query %r porad 0, zkousim letters-only %r",
                 query, letters)
        params_l: Dict[str, Any] = {"query": letters}
        if year:
            params_l["year"] = year
        cand = _best_from(_http_get("/search/movie", **params_l))
        best = _try_better(best, cand)

    # 7) v0.0.59: SPLIT-FALLBACK - pro slepene CZ+EN tituly ("V úkrytu
    #    Shelter") nebo vicewordove neexistujici nazvy ("Toy Story
    #    Historicky"). Postupne zkousime kratsi varianty (diacritic
    #    split + progressive trim z konce/zacatku).
    #
    #    v0.0.61: BEZPECNOST - vyzaduje SKUTECNY year-match (+/-1) a NE
    #    jen score >= 500 (ktery jeste mohl byt z vote_count bez year
    #    match). Bez toho by Toy Story 4 (2019, 5000 votes, year_diff=7
    #    -> score 4650) prosel pro "Toy Story Historicky" 2026 - spatne.
    def _has_year_match(raw_result: Dict[str, Any]) -> bool:
        if not year:
            return False
        r_year = _date_year(raw_result.get("release_date") or "")
        return bool(r_year and abs(r_year - year) <= 1)

    if (not best or best[0] < GOOD_SCORE) and year:
        splits = _ct.title_split_variants(query)
        for split_q in splits[:5]:
            log.info("tmdb: split-fallback zkousim %r (z %r)", split_q, query)
            params_s: Dict[str, Any] = {"query": split_q, "year": year}
            cand = _best_from(_http_get("/search/movie", **params_s))
            if cand and _has_year_match(cand[2]):
                best = _try_better(best, cand)
                if best and best[0] >= GREAT_SCORE:
                    break
            cand_en = _best_from(_http_get("/search/movie",
                                           query=split_q, year=year,
                                           language="en-US"))
            if cand_en and _has_year_match(cand_en[2]):
                best = _try_better(best, cand_en)
                if best and best[0] >= GREAT_SCORE:
                    break

    return (best[1], best[2]) if best else None


def _fetch_movie_details_localized(
    tmdb_id: int,
    fallback_title: str = "",
    fallback_year: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Načte /movie/{tmdb_id}?language=cs-CZ pro lokalizovaný překlad.
    Pokud cs nemá název / popis, fallback na en-US dotaz.
    """
    if not tmdb_id:
        return {}

    data = _http_get(f"/movie/{tmdb_id}")
    if not data or not data.get("id"):
        return {}

    cs_title = data.get("title") or ""
    cs_plot = data.get("overview") or ""
    original_title = data.get("original_title") or ""

    # Pokud cs-CZ vrátil prázdný titul/popis, doplň z en-US
    if not cs_title or not cs_plot:
        en_data = _http_get(f"/movie/{tmdb_id}", language="en-US")
        if en_data:
            if not cs_title:
                cs_title = en_data.get("title") or original_title or fallback_title
            if not cs_plot:
                cs_plot = en_data.get("overview") or ""

    poster_path = data.get("poster_path") or ""
    fanart_path = data.get("backdrop_path") or ""

    # Image fallback (cs->en->no-lang)
    if not poster_path or not fanart_path:
        best_poster, best_fanart = _get_best_images(tmdb_id, kind="movie")
        if not poster_path and best_poster:
            poster_path = best_poster
        if not fanart_path and best_fanart:
            fanart_path = best_fanart

    # v0.0.69: /movie/{id} vraci 'genres' jako list objektu {id, name}.
    detail_genre_ids = [int(g["id"]) for g in (data.get("genres") or [])
                        if isinstance(g, dict) and "id" in g]

    return {
        "tmdb_id":    tmdb_id,
        "title":      cs_title or original_title or fallback_title,
        "original":   original_title,
        "year":       _date_year(data.get("release_date")) or fallback_year,
        "plot":       cs_plot,
        "rating":     float(data.get("vote_average") or 0),
        "votes":      int(data.get("vote_count") or 0),
        "popularity": float(data.get("popularity") or 0),
        "poster":     _poster_url(poster_path, POSTER_SIZE),
        "fanart":     _poster_url(fanart_path, FANART_SIZE),
        "genre_ids":  detail_genre_ids,
    }


def _movie_payload(data: Optional[Dict[str, Any]], fallback_title: str,
                   fallback_year: Optional[int]) -> Dict[str, Any]:
    """Z TMDB odpovědi vytvoří náš dict, nebo {} pokud nic."""
    if not data or not data.get("results"):
        return {}
    r = data["results"][0]
    tmdb_id = r.get("id")
    poster_path = r.get("poster_path")
    fanart_path = r.get("backdrop_path")

    # FALLBACK: pokud cs-CZ search nevrátil obrázky, dotáhneme nejlepší
    # dostupné z /movie/{id}/images (preferujeme cs -> en -> bez jazyka).
    if tmdb_id and (not poster_path or not fanart_path):
        best_poster, best_fanart = _get_best_images(tmdb_id, kind="movie")
        if not poster_path and best_poster:
            poster_path = best_poster
        if not fanart_path and best_fanart:
            fanart_path = best_fanart

    # v0.0.69: genre_ids z /search/movie raw result
    raw_genre_ids = [int(g) for g in (r.get("genre_ids") or [])
                     if isinstance(g, (int, float))]

    return {
        "tmdb_id":    tmdb_id,
        "title":      r.get("title") or r.get("original_title") or fallback_title,
        "original":   r.get("original_title"),
        "year":       _date_year(r.get("release_date")) or fallback_year,
        "plot":       r.get("overview") or "",
        "rating":     float(r.get("vote_average") or 0),
        "votes":      int(r.get("vote_count") or 0),
        "popularity": float(r.get("popularity") or 0),
        "poster":     _poster_url(poster_path, POSTER_SIZE),
        "fanart":     _poster_url(fanart_path, FANART_SIZE),
        "genre_ids":  raw_genre_ids,
    }


# ---------------------------------------------------------------------------
# Image fallback - pokud cs-CZ verze nemá poster/fanart, dotáhne se nejlepší
# z dostupných jazykových mutací (priorita: cs > en > bez jazyka).
# ---------------------------------------------------------------------------

def _get_best_images(tmdb_id: int, kind: str = "movie") -> Tuple[str, str]:
    """
    Vrátí (poster_path, fanart_path) - nejlepší dostupné obrázky pro daný
    TMDB ID. Pokud existuje cs verze, použije ji; jinak en; jinak bez
    jazyka. Cachuje na 30 dní.

    Returns:
        (poster_path, fanart_path) - oba mohou být "" pokud TMDB
        nemá žádný obrázek.
    """
    if not tmdb_id or not is_enabled():
        return ("", "")

    cache_key = f"tmdb:images:{kind}:{tmdb_id}"
    cached = cache.cache_get(cache_key, ttl=30 * 86400)
    if cached is not None:
        # Cache může být dict {"poster": "...", "fanart": "..."} nebo dvojice
        if isinstance(cached, dict):
            return (cached.get("poster") or "", cached.get("fanart") or "")
        if isinstance(cached, (list, tuple)) and len(cached) == 2:
            return (cached[0] or "", cached[1] or "")
        return ("", "")

    base = "tv" if kind == "tv" else "movie"
    path = f"/{base}/{tmdb_id}/images"
    # include_image_language=cs,en,null vrátí VŠECHNY obrázky bez ohledu
    # na global language setting (default by jinak filtroval).
    try:
        data = _http_get(path, include_image_language="cs,en,null")
    except Exception as exc:  # noqa: BLE001
        log.debug("_get_best_images(%s) selhalo: %s", tmdb_id, exc)
        return ("", "")

    if not data:
        cache.cache_set(cache_key, {"poster": "", "fanart": ""})
        return ("", "")

    def _pick_best(images: List[Dict[str, Any]]) -> str:
        """Vrátí file_path nejlepšího obrázku (priorita cs > en > null)."""
        if not images:
            return ""
        def _score(img: Dict[str, Any]) -> Tuple[int, float, float]:
            lang = (img.get("iso_639_1") or "").lower()
            if lang == "cs":
                lang_pri = 3
            elif lang == "en":
                lang_pri = 2
            elif lang == "":
                lang_pri = 1  # bez jazyka (jen logo / generic)
            else:
                lang_pri = 0  # jiný jazyk
            return (
                lang_pri,
                float(img.get("vote_average") or 0),
                float(img.get("vote_count") or 0),
            )
        sorted_imgs = sorted(images, key=_score, reverse=True)
        return sorted_imgs[0].get("file_path") or ""

    poster_path = _pick_best(data.get("posters") or [])
    fanart_path = _pick_best(data.get("backdrops") or [])

    cache.cache_set(cache_key, {"poster": poster_path, "fanart": fanart_path})
    return (poster_path, fanart_path)


def search_tv(title: str) -> Optional[Dict[str, Any]]:
    """
    Vrátí TMDB metadata pro seriál.
    Pro čištění používáme clean_series_name (odřízne SxxEyy, kvalitu,
    jazyk, release groups), takže 'Game.of.Thrones.S01E02.CZ.x264.mkv'
    se na TMDB hledá jako 'Game of Thrones'.
    """
    if not is_enabled():
        return None
    if not title:
        return None

    clean = _ct.clean_series_name(title)
    if not clean:
        return None

    # v0.0.58: cache key bump v2 - cut-at-first-marker zmenil clean
    # formu nazvu seriálu. Stara cache by drzela false-negative-y.
    # v0.0.83: bump v3 - search_tv vraci genre_ids pro zobrazeni zanru v UI
    key = f"tmdb:tv:v3:{clean.lower()}"
    cached = cache.cache_get(key, ttl=HIT_TTL)
    if cached:
        return cached

    log.info("search_tv: query=%r", clean)

    # v0.0.58: Multi-retry - cs query, en query, ASCII-fold, letters-only.
    # Stejna logika jako u filmu pro shodne pokryti ceskych serialu.
    data = _http_get("/search/tv", query=clean)
    if not (data and data.get("results")):
        data = _http_get("/search/tv", query=clean, language="en-US")
    if not (data and data.get("results")):
        folded = _ct.ascii_fold(clean)
        if folded.lower() != clean.lower():
            data = _http_get("/search/tv", query=folded)
            if not (data and data.get("results")):
                data = _http_get("/search/tv", query=folded, language="en-US")
    if not (data and data.get("results")):
        letters = _ct.letters_only_title(clean)
        if letters and letters.lower() != clean.lower() and len(letters) >= 2:
            data = _http_get("/search/tv", query=letters)

    result: Dict[str, Any] = {}
    if data and data.get("results"):
        r = data["results"][0]
        tmdb_id = r.get("id")
        poster_path = r.get("poster_path")
        fanart_path = r.get("backdrop_path")

        # FALLBACK: pokud cs-CZ verze nemá obrázky, dotáhneme nejlepší
        # z dostupných jazyků (cs -> en -> null).
        if tmdb_id and (not poster_path or not fanart_path):
            best_p, best_f = _get_best_images(tmdb_id, kind="tv")
            if not poster_path and best_p:
                poster_path = best_p
            if not fanart_path and best_f:
                fanart_path = best_f

        raw_genre_ids = [int(g) for g in (r.get("genre_ids") or [])
                         if isinstance(g, (int, float))]

        result = {
            "tmdb_id":    tmdb_id,
            "title":      r.get("name") or r.get("original_name") or clean,
            "original":   r.get("original_name"),
            "year":       _date_year(r.get("first_air_date")),
            "plot":       r.get("overview") or "",
            "rating":     float(r.get("vote_average") or 0),
            "votes":      int(r.get("vote_count") or 0),
            "popularity": float(r.get("popularity") or 0),
            "poster":     _poster_url(poster_path, POSTER_SIZE),
            "fanart":     _poster_url(fanart_path, FANART_SIZE),
            "genre_ids":  raw_genre_ids,
        }

    if result:
        cache.cache_set(key, result)
    else:
        cache.cache_set(key, {})
        log.warning("search_tv(%r): 0 výsledků z TMDB.", clean)
    return result or None


def _date_year(date_str: Optional[str]) -> Optional[int]:
    if not date_str or len(date_str) < 4:
        return None
    try:
        return int(date_str[:4])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# ENRICHMENT – doplnění metadat do našeho video-itemu
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Žánry – genre_ids -> lokalizované názvy (cs-CZ z TMDB API)
# ---------------------------------------------------------------------------

_GENRE_ID_MAP: Dict[str, Dict[int, str]] = {}


def _genre_id_map(kind: str = "movie") -> Dict[int, str]:
    """Vrátí mapu TMDB genre_id -> název (cachuje 7 dní)."""
    k = "tv" if kind == "tv" else "movie"
    if k in _GENRE_ID_MAP:
        return _GENRE_ID_MAP[k]
    cache_key = f"tmdb_genres_{k}"
    cached = cache.cache_get(cache_key, ttl=7 * 86400)
    if cached is not None:
        out = {int(g["id"]): (g.get("name") or "")
               for g in (cached or []) if isinstance(g, dict) and "id" in g}
        _GENRE_ID_MAP[k] = out
        return out
    try:
        data = _http_get(f"/genre/{k}/list")
    except Exception as exc:  # noqa: BLE001
        log.debug("_genre_id_map(%s) selhalo: %s", k, exc)
        _GENRE_ID_MAP[k] = {}
        return {}
    genres = [{"id": int(g["id"]), "name": g.get("name") or ""}
              for g in ((data or {}).get("genres") or [])]
    cache.cache_set(cache_key, genres)
    out = {g["id"]: g["name"] for g in genres}
    _GENRE_ID_MAP[k] = out
    return out


def genre_names_for_ids(genre_ids: List[int],
                        kind: str = "movie") -> List[str]:
    """Přeloží TMDB genre_ids na čitelné názvy žánrů."""
    if not genre_ids:
        return []
    id_map = _genre_id_map(kind)
    names: List[str] = []
    for gid in genre_ids:
        try:
            gid_int = int(gid)
        except (TypeError, ValueError):
            continue
        name = id_map.get(gid_int) or ""
        if name and name not in names:
            names.append(name)
    return names


def _merge_meta(item: Dict[str, Any], meta: Dict[str, Any],
                kind: str = "movie") -> None:
    """In-place doplnění metadat z TMDB do interního video-itemu."""
    if meta.get("title"):
        item["title_localized"] = meta["title"]
    # Originální (anglický) název - pro cross-page dedup. Stabilnější
    # než cs překlad, protože různé WS soubory mohou mít různé cs varianty
    # ("Frozen" / "Ledové království" / "Lední království"), ale TMDB má
    # vždy stejný original_title.
    if meta.get("original"):
        item["original_title"] = meta["original"]
    if meta.get("year"):
        item["year"] = meta["year"]
    if meta.get("plot"):
        item["plot"] = meta["plot"]
    if meta.get("poster"):
        item["poster"] = meta["poster"]
    if meta.get("fanart"):
        item["fanart"] = meta["fanart"]
    if meta.get("rating") is not None:
        item["rating"] = float(meta["rating"])
    if meta.get("votes") is not None:
        item["votes"] = int(meta["votes"])
    if meta.get("popularity") is not None:
        item["popularity"] = float(meta["popularity"])
    if meta.get("tmdb_id"):
        item["tmdb_id"] = meta["tmdb_id"]
    # v0.0.69: genre_ids pouzivane per-rubric genre filtrem
    # (napr. Animovane CZ/SK filtruje na 16). Zachovavame i prazdny list,
    # at vime, ze enrich probehl a item nema zadny genre (= zahodit).
    if "genre_ids" in meta:
        gids = list(meta.get("genre_ids") or [])
        item["genre_ids"] = gids
        item["genre_names"] = list(meta.get("genre_names") or []) or genre_names_for_ids(gids, kind)


def get_imdb_id(tmdb_id: int, kind: str = "movie") -> Optional[str]:
    """
    Lazy lookup IMDB ID pro daný TMDB ID (přes /movie/{id}/external_ids).
    Cachuje na 30 dní. Vrací 'tt0123456' nebo None.
    """
    if not tmdb_id or not is_enabled():
        return None
    key = f"tmdb:imdb:{kind}:{tmdb_id}"
    cached = cache.cache_get(key, ttl=30 * 86400)
    if cached is not None:
        return cached or None

    if kind == "tv":
        path = f"/tv/{tmdb_id}/external_ids"
    else:
        path = f"/movie/{tmdb_id}/external_ids"

    data = _http_get(path)
    imdb = (data or {}).get("imdb_id") or ""
    cache.cache_set(key, imdb)
    return imdb or None


def get_trailer_youtube_key(tmdb_id: int, kind: str = "movie") -> Optional[str]:
    """
    Vrátí YouTube key trailer-u (nebo None) pro daný TMDB ID.
    Preferuje 'Trailer' typ a Czech jazykovou verzi (pokud existuje),
    jinak fallback na english/anything.
    """
    if not tmdb_id or not is_enabled():
        return None
    key_cache = f"tmdb:trailer:{kind}:{tmdb_id}"
    cached = cache.cache_get(key_cache, ttl=7 * 86400)
    if cached is not None:
        return cached or None

    path = f"/{'tv' if kind == 'tv' else 'movie'}/{tmdb_id}/videos"
    try:
        data = _http_get(path)
    except Exception as exc:  # noqa: BLE001
        log.debug("get_trailer videos selhal: %s", exc)
        return None

    results = (data or {}).get("results") or []

    def _score(v: Dict[str, Any]) -> int:
        s = 0
        if (v.get("site") or "").lower() != "youtube":
            return -1  # nepodporujeme jiné servery
        if (v.get("type") or "").lower() == "trailer":
            s += 10
        if (v.get("official")):
            s += 5
        # Czech preference
        lang = (v.get("iso_639_1") or "").lower()
        if lang == "cs":
            s += 3
        elif lang == "en":
            s += 1
        # Novější verze první
        if v.get("published_at"):
            s += 1
        return s

    scored = [(v, _score(v)) for v in results]
    scored = [(v, s) for v, s in scored if s >= 0]
    scored.sort(key=lambda x: -x[1])
    if not scored:
        cache.cache_set(key_cache, "")
        return None

    yt_key = scored[0][0].get("key") or ""
    cache.cache_set(key_cache, yt_key)
    return yt_key or None


def _tmdb_movie_cache_keys(title: str, year: Any) -> List[str]:
    """Vsechny rozumne cache klice pro film (rok z itemu, z titulu, bez roku)."""
    clean = _strip_title(title)
    if not clean:
        return []
    years: List[Any] = []
    if year:
        years.append(year)
    gy = _extract_year(title)
    if gy and gy not in years:
        years.append(gy)
    years.append("")
    keys: List[str] = []
    seen: set = set()
    for y in years:
        k = f"tmdb:movie:v9:{clean.lower()}:{y or ''}"
        if k not in seen:
            seen.add(k)
            keys.append(k)
    return keys


def prefill_movie_item_from_cache(item: Dict[str, Any]) -> bool:
    """v0.0.112: Hydratuje item z diskove TMDB cache bez site."""
    if not is_enabled():
        return False
    if item.get("tmdb_id") and item.get("poster") and item.get("title_localized"):
        return True
    title = item.get("base_title") or item.get("title") or ""
    if not title:
        return False
    year = item.get("year")
    if not year:
        year = _extract_year(title)
    try:
        from .title_match import title_search_variants
        titles = title_search_variants(title, year)
    except Exception:  # noqa: BLE001
        titles = [title]
    seen_keys: set = set()
    for t in titles:
        for key in _tmdb_movie_cache_keys(t, year):
            if key in seen_keys:
                continue
            seen_keys.add(key)
            cached = cache.cache_get(key, ttl=HIT_TTL)
            if not cached or not isinstance(cached, dict) or not cached:
                continue
            _merge_meta(item, cached, kind="movie")
            if item.get("poster") or item.get("tmdb_id"):
                return bool(item.get("poster"))
    return bool(item.get("poster"))


def prefill_series_item_from_cache(item: Dict[str, Any]) -> bool:
    """v0.0.112: Hydratuje serial z diskove TMDB cache bez site."""
    if not is_enabled():
        return False
    if item.get("tmdb_id") and item.get("poster") and item.get("title_localized"):
        return True
    title = item.get("title") or ""
    if not title:
        return False
    clean = _strip_title(title)
    if not clean:
        return False
    key = f"tmdb:tv:v3:{clean.lower()}"
    cached = cache.cache_get(key, ttl=HIT_TTL)
    if not cached or not isinstance(cached, dict):
        return False
    _merge_meta(item, cached, kind="tv")
    return bool(item.get("poster") or item.get("tmdb_id"))


def enrich_movie_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Doplní film metadata z TMDB. Neselhává – pokud TMDB nedostupné, vrátí item beze změny."""
    if not is_enabled():
        return item
    # v0.0.62: SHORTCUT - pokud item uz ma kompletni TMDB data (z drivejsi
    # enrichu, treba z bufferu z cache), nedelej dalsi TMDB volani.
    # 'tmdb_id' + 'poster' + 'title_localized' = mame enrich hotov.
    if item.get("tmdb_id") and item.get("poster") and item.get("title_localized"):
        if item.get("genre_ids") and not item.get("genre_names"):
            item["genre_names"] = genre_names_for_ids(item["genre_ids"], "movie")
        return item
    try:
        meta = search_movie(
            item.get("base_title") or item.get("title") or "",
            item.get("year"),
        )
        if meta:
            from .title_match import metadata_title_compatible
            ws = (item.get("base_title") or item.get("title") or "").strip()
            if metadata_title_compatible(
                ws, meta.get("title") or "", meta.get("original") or "",
            ):
                _merge_meta(item, meta, kind="movie")
            else:
                log.info("enrich_movie_item: TMDB %r neodpovida WS %r",
                         meta.get("title"), ws)
    except Exception as exc:  # noqa: BLE001
        log.debug("enrich_movie_item(%r) selhalo: %s", item.get("title"), exc)
    return item


def enrich_series_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Doplní seriál metadata z TMDB."""
    if not is_enabled():
        return item
    # v0.0.62: SHORTCUT - skip pokud uz mame TMDB data
    if item.get("tmdb_id") and item.get("poster") and item.get("title_localized"):
        if item.get("genre_ids") and not item.get("genre_names"):
            item["genre_names"] = genre_names_for_ids(item["genre_ids"], "tv")
        return item
    try:
        meta = search_tv(item.get("title") or "")
        if meta:
            _merge_meta(item, meta, kind="tv")
    except Exception as exc:  # noqa: BLE001
        log.debug("enrich_series_item(%r) selhalo: %s", item.get("title"), exc)
    return item
