# -*- coding: utf-8 -*-
"""
tv_program_sk.py
----------------
Slovensky TV program dnes - zdroj: ANTIK WebTV API (api.webtv.sk).

Stejny tvar polozky jako tv_program.py (iDNES CZ), aby fungoval spolecny
enrich (TMDB/CSFD) a prehrani na Webshare.

Kanaly: Jednotka, Dvojka, Markiza, JOJ, Doma, Dajto, ...
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import cache

log = logging.getLogger("klempcinema.tv_program_sk")

API_CHANNELS = "https://api.webtv.sk/channels"
API_EPG = "https://api.webtv.sk/epg/channel"

CACHE_KEY = "tv_program:sk:webtv:v1"
CACHE_TTL = 2 * 3600
HTTP_TIMEOUT = 8

# Hlavni SK stanice (bez +1 a sportu).
SK_CHANNEL_IDS: Tuple[str, ...] = (
    "stv_1",          # Jednotka
    "stv_2",          # Dvojka
    "stv_3",          # :24
    "markiza",
    "markiza_krimi",
    "markiza_klasik",
    "doma",
    "dajto",
    "joj",
    "joj_plus",
    "wau",            # JOJ Krimi
    "joj_cinema",
    "joj_24",
    "joj_svet",
    "ta_3",
    "prima_plus_hd",  # Prima SK
    "prima_cool_sk",
    "prima_love_sk",
    "prima_krimi_sk",
)

_DISPLAY_OVERRIDE = {
    "stv_1": "Jednotka",
    "stv_2": "Dvojka",
    "stv_3": ":24",
    "markiza": "Markíza",
    "markiza_krimi": "Markíza Krimi",
    "markiza_klasik": "Markíza Klasik",
    "doma": "Doma",
    "dajto": "Dajto",
    "joj": "JOJ",
    "joj_plus": "JOJ Plus",
    "wau": "JOJ Krimi",
    "joj_cinema": "JOJ Cinema",
    "joj_24": "JOJ 24",
    "joj_svet": "JOJ Svet",
    "ta_3": "TA3",
    "prima_plus_hd": "Prima SK",
    "prima_cool_sk": "Prima Cool SK",
    "prima_love_sk": "Prima Love SK",
    "prima_krimi_sk": "Prima Krimi SK",
}

HEADERS = {
    "User-Agent": "KlempCinema/1.0 (Kodi addon; TV EPG)",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

# Odstraneni " (10)" / " III (5)" z titulu pro TMDB/WS search.
_EP_SUFFIX_RE = re.compile(
    r"\s*(?:[IVXLC]+)?\s*\(\d{1,4}\)\s*$",
    re.I,
)


def _http_post_json(url: str, payload: Dict[str, Any]) -> Optional[Any]:
    raw = json.dumps(payload).encode("utf-8")
    req = Request(url, data=raw, headers=HEADERS, method="POST")
    try:
        with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            body = resp.read()
            if not body:
                return None
            return json.loads(body.decode("utf-8", errors="replace"))
    except HTTPError as exc:
        log.warning("tv_sk: HTTP %s %s", exc.code, url)
        return None
    except URLError as exc:
        log.warning("tv_sk: network %s: %s", url, exc.reason)
        return None
    except Exception as exc:  # noqa: BLE001
        log.warning("tv_sk: fail %s: %s", url, exc)
        return None


def _classify_genres(genres: Any) -> str:
    """Mapovani WebTV zanru -> kind (stejne jako iDNES)."""
    if not genres:
        return "other"
    if isinstance(genres, str):
        g = genres.lower()
    else:
        g = " ".join(str(x) for x in genres).lower()
    if any(x in g for x in ("film", "movie", "kino")):
        return "film"
    if any(x in g for x in ("seri", "series", "telenov", "soap")):
        return "series"
    if any(x in g for x in ("dokument", "document")):
        return "documentary"
    if any(x in g for x in ("spravodaj", "news", "zpráv", "zprav")):
        return "news"
    if any(x in g for x in ("šport", "sport")):
        return "sport"
    if any(x in g for x in ("deti", "děti", "kids", "child")):
        return "kids"
    if any(x in g for x in ("hudb", "music")):
        return "music"
    if any(x in g for x in ("zábav", "zabav", "reality", "show", "súťaž", "sutaz")):
        return "entertainment"
    return "other"


def _parse_iso(dt: str) -> Optional[datetime]:
    if not dt:
        return None
    s = dt.strip()
    try:
        # 2026-07-21T20:00:00+02:00
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _clean_search_title(title: str) -> str:
    t = (title or "").strip()
    if not t:
        return ""
    t = _EP_SUFFIX_RE.sub("", t).strip()
    return t or title.strip()


def _year_from_desc(desc: str) -> Optional[int]:
    if not desc:
        return None
    m = re.search(r"\b((?:19|20)\d{2})\b", desc)
    if not m:
        return None
    try:
        y = int(m.group(1))
    except ValueError:
        return None
    if 1900 <= y <= 2099:
        return y
    return None


def fetch_today_sk(force_refresh: bool = False) -> List[Dict[str, Any]]:
    """Vrati dnesni SK TV polozky ve formatu tv_program."""
    if force_refresh:
        try:
            cache.cache_delete(CACHE_KEY)
        except Exception:  # noqa: BLE001
            pass
    else:
        cached = cache.cache_get(CACHE_KEY, ttl=CACHE_TTL)
        if cached is not None:
            log.info("tv_sk: cache HIT (%d)", len(cached))
            return list(cached)

    items = _fetch_live()
    if items:
        cache.cache_set(CACHE_KEY, items)
    log.info("tv_sk: nacteno %d polozek", len(items))
    return items


def _fetch_live() -> List[Dict[str, Any]]:
    ch_data = _http_post_json(
        API_CHANNELS, {"type": "TV", "channels_content": None})
    if not isinstance(ch_data, dict) or not isinstance(ch_data.get("data"), dict):
        log.warning("tv_sk: channels API prazdne")
        return []

    names: Dict[str, str] = {}
    for cid, meta in ch_data["data"].items():
        if not isinstance(meta, dict):
            continue
        names[str(cid)] = (meta.get("name") or str(cid)).strip()

    # Local calendar day in Europe/Bratislava ≈ CET/CEST (+01/+02).
    # WebTV vraci Start s offsetem; filtrujeme podle lokalniho data.
    now_utc = datetime.now(timezone.utc)
    # Pouzijeme +02 jako default leto; z EPG polozek bereme realny offset.
    today_local = (now_utc + timedelta(hours=2)).date()
    date_payload = now_utc.strftime("%Y-%m-%dT00:00:00.000Z")

    out: List[Dict[str, Any]] = []

    def _one_channel(cid: str) -> List[Dict[str, Any]]:
        if cid not in names and cid not in _DISPLAY_OVERRIDE:
            return []
        epg = _http_post_json(
            API_EPG, {"channel_id": cid, "date": date_payload})
        if not isinstance(epg, dict):
            return []
        content = epg.get("content") or []
        if not isinstance(content, list):
            return []
        cname = _DISPLAY_OVERRIDE.get(cid) or names.get(cid) or cid
        local_items: List[Dict[str, Any]] = []
        for row in content:
            if not isinstance(row, dict):
                continue
            title = (row.get("Title") or "").strip()
            if not title:
                continue
            start = _parse_iso(str(row.get("Start") or ""))
            stop = _parse_iso(str(row.get("Stop") or ""))
            if not start:
                continue

            tz = start.tzinfo or timezone(timedelta(hours=2))
            day_start = datetime(
                today_local.year, today_local.month, today_local.day,
                tzinfo=tz,
            )
            day_end = day_start + timedelta(days=1)
            end_for_overlap = stop or (start + timedelta(minutes=30))
            if not (start < day_end and end_for_overlap > day_start):
                continue

            start_min = start.hour * 60 + start.minute
            now_local = now_utc.astimezone(tz)
            is_past = (stop or end_for_overlap) < now_local

            kind = _classify_genres(row.get("Genres"))
            plot = (row.get("Description") or "").strip()
            subtitle = (row.get("Subtitle") or "").strip()
            if subtitle:
                plot = (f"{subtitle}. {plot}").strip() if plot else subtitle

            search_title = _clean_search_title(title)
            year = _year_from_desc(plot)

            local_items.append({
                "channel_id": f"sk:{cid}",
                "channel": cname,
                "title": search_title,
                "title_raw": title,
                "time": start.strftime("%H:%M"),
                "start_min": start_min,
                "length_min": (
                    int((stop - start).total_seconds() // 60)
                    if stop else None
                ),
                "plot": plot,
                "thumb": "",
                "url": "",
                "kind": kind,
                "year": year,
                "is_past": bool(is_past),
                "premium": False,
                "country": "sk",
                "source": "webtv.sk",
            })
        return local_items

    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=6, thread_name_prefix="tvsk") as pool:
            for part in pool.map(_one_channel, SK_CHANNEL_IDS):
                out.extend(part)
    except Exception as exc:  # noqa: BLE001
        log.warning("tv_sk: parallel fetch fail, serial: %s", exc)
        for cid in SK_CHANNEL_IDS:
            out.extend(_one_channel(cid))

    out.sort(key=lambda x: (x.get("start_min") or 0, x.get("channel") or ""))
    return out


def get_sk_channels(items: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    """[(channel_id, name), ...] ze SK polozek se sledovatelnym obsahem."""
    kinds = {"film", "series", "documentary", "entertainment"}
    seen: Dict[str, str] = {}
    for it in items:
        if it.get("country") != "sk":
            continue
        if it.get("kind") not in kinds or it.get("is_past"):
            continue
        cid = str(it.get("channel_id") or "")
        if cid:
            seen[cid] = (it.get("channel") or cid).strip() or cid
    return sorted(seen.items(), key=lambda x: (x[1] or "").lower())
