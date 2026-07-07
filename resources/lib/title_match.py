# -*- coding: utf-8 -*-
"""
title_match.py
----------------
Normalizace a fuzzy porovnani titulu (WS preklep vs TMDB/CSFD nazev).
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Optional


def normalize_title(title: str) -> str:
    """Lowercase ASCII-ish retezec pro porovnani (bez diakritiky, interpunkce)."""
    if not title:
        return ""
    s = unicodedata.normalize("NFKD", title)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def title_similarity(a: str, b: str) -> float:
    """0.0–1.0 podobnost dvou titulu (SequenceMatcher na normalizovanych retezcich)."""
    na = normalize_title(a)
    nb = normalize_title(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def best_title_similarity(query: str, *candidates: Optional[str]) -> float:
    """Nejvyssi podobnost query vuci libovolnemu kandidatovi."""
    best = 0.0
    for c in candidates:
        if not c:
            continue
        best = max(best, title_similarity(query, c))
    return best


def fuzzy_title_bonus(query: str, *candidates: Optional[str]) -> float:
    """Skore navic pro TMDB/CSFD ranking (0–400 podle podobnosti)."""
    sim = best_title_similarity(query, *candidates)
    if sim >= 0.92:
        return 380.0
    if sim >= 0.80:
        return 250.0 * sim
    if sim >= 0.65:
        return 150.0 * sim
    return 0.0


_TYPO_REPLACEMENTS = (
    (re.compile(r"\bpradu\b", re.I), "pravdu"),
    (re.compile(r"\bdabek\b", re.I), "dábel"),
    (re.compile(r"\bdabel\b", re.I), "dábel"),
)

_SEQUEL_MARKER_RE = re.compile(
    r"(?:\b2\b|\bii\b|\biii\b|\biv\b|\b3\b|"
    r"\bdruh(?:y|a|ou)?\b|\btreti\b|\bsequel\b|"
    r"\bpart\s*2\b|\b2\.\s*(?:cast|dil)\b)",
    re.I,
)


def apply_typo_fixes(title: str) -> str:
    """Bezne WS preklepy v ceskych nazvech (pradu->pravdu, dabek->dabel)."""
    if not title:
        return ""
    s = title
    for pat, repl in _TYPO_REPLACEMENTS:
        s = pat.sub(repl, s)
    return s.strip()


def has_sequel_marker(title: str) -> bool:
    """True pokud nazev explicitne obsahuje cislo dilu / sequel."""
    return bool(_SEQUEL_MARKER_RE.search(title or ""))


def metadata_title_compatible(
    ws_title: str,
    meta_title: str,
    meta_original: str = "",
) -> bool:
    """WS nazev souboru musi odpovidat TMDB titulu (vcetne sequel cisla)."""
    ws = apply_typo_fixes(ws_title or "")
    if not ws or not (meta_title or meta_original):
        return False
    sim = best_title_similarity(ws, meta_title, meta_original)
    ws_seq = has_sequel_marker(ws)
    meta_seq = has_sequel_marker(meta_title) or has_sequel_marker(meta_original)
    if meta_seq and not ws_seq:
        return False
    if ws_seq and not meta_seq and sim < 0.68:
        return False
    if sim >= 0.72:
        return True
    if sim >= 0.55 and ws_seq == meta_seq:
        return True
    return False


def title_search_compatible(
    ws_title: str,
    query_used: str,
    meta_title: str,
    meta_original: str = "",
) -> bool:
    """Overeni TMDB kandidata vuci puvodnimu WS titulu."""
    ws = apply_typo_fixes(ws_title or "")
    qu = apply_typo_fixes(query_used or "")
    if not metadata_title_compatible(ws, meta_title, meta_original):
        return False
    if normalize_title(ws) != normalize_title(qu):
        if has_sequel_marker(qu) and not has_sequel_marker(ws):
            return False
    return True


def extra_search_queries(title: str, year: Optional[int] = None) -> list[str]:
    """Doplnkove TMDB/CSFD dotazy (sequel, EN nazev) pro nove uploady."""
    out: list[str] = []
    n = normalize_title(title)
    try:
        y = int(year) if year is not None else None
    except (TypeError, ValueError):
        y = None
    if y is not None and y >= 2025 and has_sequel_marker(title):
        if any(x in n for x in ("pradu", "pravdu", "prada", "dabel", "dabek")):
            out.extend([
                "The Devil Wears Prada 2",
                "Devil Wears Prada 2",
                "Ďábel nosí Pradu 2",
            ])
    return out


def title_search_variants(title: str, year: Optional[int] = None) -> list[str]:
    """Varianty titulu pro metadata search (preklep, ASCII, sequel hint)."""
    if not title:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for t in [title, apply_typo_fixes(title), *extra_search_queries(title, year)]:
        k = (t or "").strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(t.strip())
    return out
