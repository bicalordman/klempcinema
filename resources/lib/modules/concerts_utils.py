# -*- coding: utf-8 -*-
"""Filtry a detekce koncertu (bez filmu, dokumentu, aplikaci)."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .concerts_genres import matches_genre, matches_genre_item
from .cz_bands import is_known_cz_sk_artist

TMDB_MUSIC = 10402
TMDB_DOCUMENTARY = 99

TMDB_MOVIE_ONLY_GENRES = {28, 12, 16, 18, 53, 878, 10751}

TMDB_KEEP_GENRES = {TMDB_MUSIC}

MUSIC_MARKERS = [
    "koncert", "concert", "unplugged", "wembley", "festival",
    "tour", "live at", "full concert", "music live", "hudeb",
    "orchestr", "symfon", "rock live", "metal live", "jazz",
    "český koncert", "cesky koncert", "slovenský koncert",
]

TECH_TAG_RE = re.compile(
    r"\b(2160p|1080p|720p|480p|4k|uhd|hdr10?|dv|dolby\s*vision|"
    r"bluray|blu-ray|web-?dl|webrip|hdtv|x264|x265|hevc|h\.?264|h\.?265|"
    r"aac|dts|atmos|truehd|remux)\b",
    re.I,
)
RIP_TAG_RE = re.compile(
    r"\b(bdrip|brrip|hdrip|dvdrip|cam|ts|tc|scr|r5|webrip)\b",
    re.I,
)
YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
SERIES_RE = re.compile(r"\bS(\d{1,2})\s*[EX]\s*(\d{1,3})\b", re.I)
EP_RE = re.compile(r"\bep\s*(\d{1,3})\b", re.I)
APP_EXT_RE = re.compile(r"\.(apk|exe|msi|iso|img|dmg)\b", re.I)

CZ_SK_MARKERS = [
    " cz", " sk", ".cz", ".sk", "czech", "cesk", "česk", "slovak", "slovens",
    "czdab", "skdab", "cz dab", "sk dab",
    "titulky", "tit.", "dabing", "dab",
    " cz ", " sk ",
]

_CZ_DIACRITICS_RE = re.compile(r"[ěščřžýáíéůúďťňĚŠČŘŽÝÁÍÉŮÚĎŤŇ]")

LEGENDARY_MARKERS = [
    "wembley", "live aid", "unplugged", "legend", "classic",
    "queen", "pink floyd", "metallica", "u2", "rolling stones",
    "led zeppelin", "nirvana", "ac/dc", "iron maiden", "depeche mode",
    "david bowie", "michael jackson", "freddie mercury",
]


def is_concert(title: str) -> bool:
    t = (title or "").lower()

    include = [
        "live", "koncert", "concert", "tour",
        "wembley", "unplugged", "festival",
        "session", "arena", "stadium",
    ]
    if not any(w in t for w in include):
        return False

    exclude_anim = [
        "anim", "pixar", "disney", "pohádka", "pohadka",
        "fairytale", "kids", "děti", "deti",
    ]
    if any(w in t for w in exclude_anim):
        return False

    return True


def _strip_for_concert_match(title: str) -> str:
    t = title or ""
    t = TECH_TAG_RE.sub(" ", t)
    t = RIP_TAG_RE.sub(" ", t)
    t = YEAR_RE.sub(" ", t)
    t = re.sub(r"[\[\](){}]", " ", t)
    t = re.sub(r"[._-]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def matches_concert_name(title: str) -> bool:
    """Druha sance po odstraneni technickych tagu a roku."""
    cleaned = _strip_for_concert_match(title)
    if not cleaned:
        return False
    if is_concert(cleaned):
        return True
    parts = re.split(r"\s[-–—|]\s", cleaned, maxsplit=1)
    if len(parts) > 1 and is_concert(parts[-1]):
        return True
    return False


def _title_text(item: Dict[str, Any]) -> str:
    return (
        item.get("base_title")
        or item.get("title_localized")
        or item.get("title")
        or ""
    )


def _all_names_text(item: Dict[str, Any]) -> str:
    """Cisty titul + raw WS nazvy souboru (pro hledani)."""
    parts = [_title_text(item), item.get("ws_names") or ""]
    return " ".join(p for p in parts if p)


def is_hard_excluded(title: str) -> bool:
    """Tvrdy blacklist – bez TMDB (hledani nesmi zahazovat kvuli spatnemu matchi)."""
    t = (title or "").lower()

    if any(w in t for w in ("dokument", "reportáž", "reportaz", "documentary")):
        return True
    if any(w in t for w in (
        "teleshopping", "mediashop", "barrandov", "home shopping",
        "tv prodej", "nakupovat",
    )):
        return True
    if any(w in t for w in ("lecture", "talk", "seminář", "seminar", "prednaska", "přednáška")):
        return True
    if SERIES_RE.search(title or "") or EP_RE.search(t):
        return True
    if any(w in t for w in (" díl", "dil ", "část", "cast ")):
        return True
    if APP_EXT_RE.search(title or ""):
        return True
    return False


def is_excluded_non_concert(
    title: str,
    item: Optional[Dict[str, Any]] = None,
    *,
    check_tmdb: bool = True,
) -> bool:
    t = (title or "").lower()

    doc_markers = [
        "dokument", "reportáž", "reportaz", "history", "documentary",
        "naživo", "nazivo", "mrakodrap", "skyscraper",
        "teleshopping", "mediashop", "barrandov",
    ]
    if any(w in t for w in doc_markers):
        return True
    if genre_ids := (item.get("genre_ids") if item else None):
        gids = {int(g) for g in genre_ids}
        if TMDB_DOCUMENTARY in gids and TMDB_MUSIC not in gids:
            return True

    lecture_markers = ["lecture", "talk", "seminář", "seminar", "prednaska", "přednáška"]
    if any(w in t for w in lecture_markers):
        return True

    if SERIES_RE.search(title or "") or EP_RE.search(t):
        return True
    if any(w in t for w in (" díl", "dil ", "část", "cast ")):
        return True

    if APP_EXT_RE.search(title or ""):
        return True

    if check_tmdb and item is not None:
        genre_ids = item.get("genre_ids")
        tmdb_id = item.get("tmdb_id")
        if tmdb_id and genre_ids is not None:
            gids = {int(g) for g in genre_ids}
            if gids and TMDB_MUSIC not in gids:
                return True

    return False


def tmdb_suggests_movie_not_concert(item: Dict[str, Any]) -> bool:
    """Vyhodit polozky s ciste filmovymi TMDB zanry."""
    if not item.get("tmdb_id"):
        return False

    genre_ids = {int(g) for g in (item.get("genre_ids") or [])}
    names = [n.lower() for n in (item.get("genre_names") or [])]

    if "music video" in names:
        return False
    if genre_ids.intersection(TMDB_KEEP_GENRES):
        return False

    if genre_ids and genre_ids.issubset(TMDB_MOVIE_ONLY_GENRES):
        return True

    movie_names = {
        "action", "drama", "sci-fi", "science fiction", "animation",
        "family", "thriller", "adventure",
    }
    if names and all(n in movie_names for n in names):
        return True

    return False


def _normalize_for_match(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[._\-/\\]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def matches_search_query(title: str, query: str) -> bool:
    """
    Striktni shoda dotazu – kazde slovo dotazu musi byt v nazvu.
    Zabrani tomu, aby se na dalsich strankach objevily jine koncerty.
    """
    t = _normalize_for_match(title)
    q = _normalize_for_match(query)
    if not t or not q:
        return False
    if q in t:
        return True
    tokens = [tok for tok in q.split() if len(tok) >= 2]
    if not tokens:
        return len(q) >= 2 and q in t
    for token in tokens:
        pat = re.escape(token)
        if not re.search(rf"(?<![a-z0-9]){pat}(?![a-z0-9])", t):
            return False
    return True


def is_valid_search_item(item: Dict[str, Any], query: str) -> bool:
    """
    Hledani – POUZE polozky obsahujici hledany nazev (zadna smes jinych koncertu).
    """
    q = (query or "").strip()
    if not q:
        return False
    names = _all_names_text(item)
    if is_hard_excluded(names):
        return False
    return matches_search_query(names, q)


def is_valid_concert_item(item: Dict[str, Any], query: str = "") -> bool:
    """Pre-filter pred TMDB enrich."""
    title = _title_text(item)
    if not title:
        return False
    if is_excluded_non_concert(title, item, check_tmdb=False):
        return False
    if query.strip():
        if matches_search_query(title, query):
            return True
    return is_concert(title) or matches_concert_name(title)


def filter_region_foreign(item: Dict[str, Any]) -> bool:
    text = _all_names_text(item).lower()
    if any(m in text for m in CZ_SK_MARKERS):
        return False
    if _CZ_DIACRITICS_RE.search(text):
        return False
    return True


def _looks_like_music_concert(text: str) -> bool:
    """Hudebni kontext – samotne 'live' nestaci (dokumenty typu Skyscraper Live)."""
    t = (text or "").lower()
    return any(m in t for m in MUSIC_MARKERS)


def filter_region_cz_sk(item: Dict[str, Any]) -> bool:
    """CZ/SK koncert = region + hudebni kontext (ne jen tag [CZ] u dokumentu)."""
    text = _all_names_text(item)
    tl = text.lower()
    has_region = any(m in tl for m in CZ_SK_MARKERS)
    if not has_region and _CZ_DIACRITICS_RE.search(text):
        has_region = _looks_like_music_concert(text)
    if not has_region and is_known_cz_sk_artist(text):
        has_region = True
    if not has_region:
        cz_places = (
            "praha", "brno", "ostrava", "bratislava", "plzen", "plzeň",
            "liberec", "olomouc", "o2 arena", "tipsport arena",
        )
        has_region = any(p in tl for p in cz_places)
    if not has_region:
        return False
    return _looks_like_music_concert(text) or is_known_cz_sk_artist(text)


def filter_quality_4k(item: Dict[str, Any]) -> bool:
    qs = int(item.get("quality_score") or 0)
    return qs == 0 or qs >= 1000


def filter_quality_1080p(item: Dict[str, Any]) -> bool:
    qs = int(item.get("quality_score") or 0)
    if qs == 0:
        return True
    return 800 <= qs < 1000


def filter_legendary(item: Dict[str, Any]) -> bool:
    t = _title_text(item).lower()
    if any(m in t for m in LEGENDARY_MARKERS):
        return True
    rating = float(item.get("rating") or 0)
    votes = int(item.get("votes") or 0)
    return rating >= 7.0 and votes >= 100


def _apply_subsection_filter(
    item: Dict[str, Any],
    subsection: str,
    *,
    genre: str = "",
    quality: str = "",
    use_tmdb_genre: bool = False,
) -> bool:
    """True = polozka projde filtrem podrubriky."""
    sub = (subsection or "").lower()
    title = _title_text(item)

    if sub == "foreign" and not filter_region_foreign(item):
        return False
    if sub == "cz_sk" and not filter_region_cz_sk(item):
        return False
    if sub == "legendary" and not filter_legendary(item):
        return False
    if sub == "genre" and genre:
        if use_tmdb_genre:
            if not matches_genre_item(item, genre):
                return False
        elif not matches_genre(title, genre):
            return False
    if sub == "quality":
        q = (quality or "").lower()
        if q == "4k" and not filter_quality_4k(item):
            return False
        if q == "1080p" and not filter_quality_1080p(item):
            return False

    return True


def filter_search_items_pre(
    items: List[Dict[str, Any]], query: str,
) -> List[Dict[str, Any]]:
    return [it for it in items if is_valid_search_item(it, query)]


def filter_search_items_post(
    items: List[Dict[str, Any]], query: str,
) -> List[Dict[str, Any]]:
    """Po TMDB enrich – bez filmovych TMDB filtru, jen blacklist + dotaz."""
    return [it for it in items if is_valid_search_item(it, query)]


def filter_concert_items_pre(
    items: List[Dict[str, Any]],
    subsection: str,
    *,
    genre: str = "",
    quality: str = "",
    query: str = "",
) -> List[Dict[str, Any]]:
    """Pre-filter: validni koncert + pravidla podrubriky bez TMDB."""
    out: List[Dict[str, Any]] = []
    sub = (subsection or "").lower()
    for it in items:
        if not is_valid_concert_item(it, query=query if sub == "search" else ""):
            continue
        if not _apply_subsection_filter(it, subsection, genre=genre, quality=quality):
            continue
        out.append(it)
    return out


def filter_concert_items(
    items: List[Dict[str, Any]],
    subsection: str,
    *,
    genre: str = "",
    quality: str = "",
    query: str = "",
) -> List[Dict[str, Any]]:
    """Post-filter po TMDB enrich (jen aktualni stranka)."""
    out: List[Dict[str, Any]] = []
    sub = (subsection or "").lower()
    for it in items:
        title = _title_text(it)
        # Pri explicitnim hledani: TMDB Music filter jen kdyz nazev neni koncert
        # a neodpovida dotazu (jinak TMDB casto matchne film misto koncertu).
        if sub == "search" and query.strip():
            if is_excluded_non_concert(title, it, check_tmdb=False):
                continue
            if tmdb_suggests_movie_not_concert(it):
                if not (is_concert(title) or matches_concert_name(title)
                        or matches_search_query(title, query)):
                    continue
        else:
            if is_excluded_non_concert(title, it, check_tmdb=True):
                continue
            if tmdb_suggests_movie_not_concert(it):
                continue
        if not _apply_subsection_filter(
            it, subsection, genre=genre, quality=quality, use_tmdb_genre=True,
        ):
            continue
        out.append(it)
    return out
