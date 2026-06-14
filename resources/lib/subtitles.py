# -*- coding: utf-8 -*-
"""
subtitles.py
------------
Automatické stahování českých titulků pro nedabované filmy/epizody
přes **OpenSubtitles.org XML-RPC** API.

Proč XML-RPC a ne moderní REST .com?
    REST API na opensubtitles.com vyžaduje pro KAŽDÉHO klienta vlastní
    API key (registrace Consumera). XML-RPC na opensubtitles.org si
    vystačí jen s normálním přihlášením (jméno + heslo) – účty mezi
    .org a .com jsou propojené, takže stejné údaje fungují na obou.

Tok:
    1) Uživatel klikne na nedabovanou variantu (CZ marker v názvu chybí).
    2) Plugin si přes TMDB najde IMDB ID titulu.
    3) LogIn(user, pass, lang, UA)  -> token (1 hod platnost)
    4) SearchSubtitles(token, [{imdbid: ..., sublanguageid: 'cze'}])
       -> seznam .srt/.sub položek se SubDownloadLink (gz soubor).
    5) Stáhne .gz, rozbalí, uloží do addon profile dir/subtitles/,
       vrátí lokální cestu. Tu Kodi přilepí přes ListItem.setSubtitles().

User-Agent:
    OpenSubtitles vyžaduje UA registrovaný v jejich systému, jinak
    vrátí "401 Unauthorized". Default je "XBMC_Subtitles_v1" – oficiálně
    schválený UA Kodi pro stahování titulků. Pokud bys dostával 401,
    můžeš ho v settings přepnout (např. na "trailers.to-UA").

Veřejné rozhraní (kompatibilní s předchozí verzí, kterou volá router):
    is_enabled()                      -> bool
    auto_for_undubbed_only()          -> bool
    fetch_for_title(...)              -> str | None  (lokální .srt path)
"""

from __future__ import annotations

import gzip
import io
import logging
import os
import re
import xmlrpc.client
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import cache
from . import shutdown as _shutdown

log = logging.getLogger("klempcinema.subtitles")

ORG_XMLRPC_URL = "https://api.opensubtitles.org/xml-rpc"
# v0.0.64: 25 -> 8s. Pri Kodi shutdown ceka Python az urlopen() syscall
# dobehne. 25s timeout = az 25s wait na vypnuti Kodi v worst-case.
# 8s je dostatecne pro OpenSubtitles XML-RPC (normalne ~1-2s);
# pomalejsi response = uzivatel pocka pri stahnuti titulku, ne pri exit.
TIMEOUT = 8

# Oficiálně registrované UA, které OpenSubtitles akceptuje.
# XBMC_Subtitles_v1 je standardní UA Kodi pro stahování titulků.
DEFAULT_USER_AGENT = "XBMC_Subtitles_v1"


# ---------------------------------------------------------------------------
# Konfigurace ze settings
# ---------------------------------------------------------------------------

def _addon_safe():
    try:
        import xbmcaddon  # type: ignore
        return xbmcaddon.Addon()
    except Exception:  # noqa: BLE001
        return None


# Index v enum -> ISO 639-2/B kód, který OpenSubtitles používá
_LANG_OPTIONS = {"0": "cze", "1": "slo", "2": "eng"}
_LANG_NICE = {"cze": "cs", "slo": "sk", "eng": "en"}


def _config() -> Dict[str, Any]:
    addon = _addon_safe()
    if addon is None:
        return {
            "enabled": False,
            "undubbed_only": True,
            "user": "",
            "pass": "",
            "lang": "cze",
            "user_agent": DEFAULT_USER_AGENT,
        }

    lang_idx = (addon.getSetting("subs_lang") or "0").strip() or "0"
    lang = _LANG_OPTIONS.get(lang_idx, "cze")
    ua = (addon.getSetting("subs_user_agent") or "").strip() or DEFAULT_USER_AGENT

    return {
        "enabled":       (addon.getSetting("subs_enabled") or "false").lower() == "true",
        "undubbed_only": (addon.getSetting("subs_only_undubbed") or "true").lower() == "true",
        "user":          (addon.getSetting("subs_user") or "").strip(),
        "pass":          (addon.getSetting("subs_pass") or "").strip(),
        "lang":          lang,
        "user_agent":    ua,
    }


def is_enabled() -> bool:
    return bool(_config()["enabled"])


def auto_for_undubbed_only() -> bool:
    return bool(_config()["undubbed_only"])


# ---------------------------------------------------------------------------
# Self-test (v0.0.62) - pouzitelne z Nastroju
# ---------------------------------------------------------------------------

def self_test() -> Dict[str, Any]:
    """
    v0.0.62: Otestuje pripojeni k OpenSubtitles.org. Vraci dict:
        {
          "ok": bool,
          "stage": "config" | "login" | "search" | "ok",
          "user_agent": str,
          "user": str (nebo "(anonym)"),
          "message": str,
          "token_ok": bool,
        }
    """
    cfg = _config()
    out: Dict[str, Any] = {
        "ok": False,
        "stage": "config",
        "user_agent": cfg["user_agent"],
        "user": cfg["user"] or "(anonym)",
        "lang": cfg["lang"],
        "message": "",
        "token_ok": False,
    }

    if not cfg["enabled"]:
        out["message"] = "OpenSubtitles je vypnuty v nastaveni."
        return out

    # Smaz cachovany token - vzdy chceme fresh test
    try:
        cache.cache_set(f"os.org.token.v1.{cfg['user']}", None)
    except Exception:  # noqa: BLE001
        pass

    out["stage"] = "login"
    proxy = _make_proxy(cfg["user_agent"])
    try:
        res = proxy.LogIn(cfg["user"], cfg["pass"], cfg["lang"], cfg["user_agent"])
    except (xmlrpc.client.Fault, xmlrpc.client.ProtocolError, OSError) as exc:
        out["message"] = f"Login crash: {exc}"
        return out

    status = str(res.get("status") or "")
    token = res.get("token") or ""
    out["token_ok"] = bool(token)
    if not status.startswith("200") or not token:
        out["message"] = f"Login selhal (status={status!r}, UA={cfg['user_agent']!r})"
        return out

    # Try a small search to verify token works
    out["stage"] = "search"
    try:
        sres = proxy.SearchSubtitles(
            token,
            [{"imdbid": "0468569", "sublanguageid": cfg["lang"]}],  # The Dark Knight
            {"limit": 5},
        )
    except (xmlrpc.client.Fault, xmlrpc.client.ProtocolError, OSError) as exc:
        out["message"] = f"Search crash: {exc} (login OK)"
        return out

    sstatus = str(sres.get("status") or "")
    sdata = sres.get("data") or []
    if not sstatus.startswith("200"):
        out["message"] = f"Search selhal (status={sstatus!r}, login OK)"
        return out

    out["ok"] = True
    out["stage"] = "ok"
    n = len(sdata) if isinstance(sdata, list) else 0
    out["message"] = f"OK - login a search funguji. Test query: {n} kandidatu."
    return out


# ---------------------------------------------------------------------------
# Lokální cache souborů (profile dir)
# ---------------------------------------------------------------------------

def _profile_dir() -> str:
    try:
        import xbmcvfs  # type: ignore
        return xbmcvfs.translatePath("special://profile/addon_data/plugin.video.klempcinema/")
    except Exception:  # noqa: BLE001
        return os.path.join(os.path.expanduser("~"), ".kodi_klempcinema")


def _subs_dir() -> str:
    p = os.path.join(_profile_dir(), "subtitles")
    try:
        os.makedirs(p, exist_ok=True)
    except OSError:
        log.exception("subs_dir: nelze vytvořit %s", p)
    return p


def _safe_filename(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s or "")
    return s.strip("_")[:120] or "subtitle"


# ---------------------------------------------------------------------------
# OpenSubtitles.org XML-RPC klient
# ---------------------------------------------------------------------------

class _OSAuthError(Exception):
    """OpenSubtitles XML-RPC LogIn selhal."""


def _make_proxy(user_agent: str) -> xmlrpc.client.ServerProxy:
    """
    Vytvoří XML-RPC proxy s vlastní User-Agent hlavičkou.
    Bez registrovaného UA vrátí OpenSubtitles 401 Unauthorized.
    """

    class _UATransport(xmlrpc.client.SafeTransport):
        user_agent = user_agent  # noqa: F811

    return xmlrpc.client.ServerProxy(
        ORG_XMLRPC_URL,
        transport=_UATransport(use_builtin_types=True),
        allow_none=True,
    )


def _login_token() -> Optional[str]:
    """
    Přihlásí se k OpenSubtitles.org a vrátí session token.
    Token se cachuje v addon profile dir (TTL 50 min).

    Pokud user/pass nejsou vyplněné, zkusí anonymní LogIn (s prázdnými
    parametry) – to funguje, ale s nižším download limitem.
    """
    cfg = _config()
    cache_key = f"os.org.token.v1.{cfg['user']}"
    # FIX v0.0.51: cache.cache_get parametr je 'ttl', ne 'max_age'.
    # Drive tohle hodilo TypeError, ktery se mlcky polkl v _attach_subtitles -
    # auto-fetch CZ titulku NIKDY nefungoval. Po tomto fixu funguje.
    cached = cache.cache_get(cache_key, ttl=50 * 60)
    if cached and isinstance(cached, str):
        return cached

    proxy = _make_proxy(cfg["user_agent"])
    try:
        res = proxy.LogIn(cfg["user"], cfg["pass"], cfg["lang"], cfg["user_agent"])
    except (xmlrpc.client.Fault, xmlrpc.client.ProtocolError, OSError) as exc:
        log.error("OpenSubtitles LogIn selhal: %s", exc)
        return None

    status = str(res.get("status") or "")
    token = res.get("token") or ""

    if not status.startswith("200") or not token:
        log.error("OpenSubtitles LogIn status=%r token=%r ua=%r user=%r",
                  status, bool(token), cfg["user_agent"], cfg["user"] or "(anonym)")
        # 401 obvykle = nepovolený User-Agent (musí být registrovaný v OS).
        # 414 = password missing / wrong; 415 = user inactive.
        return None

    log.info("OpenSubtitles LogIn OK (user=%s, lang=%s, ua=%s)",
             cfg["user"] or "(anonym)", cfg["lang"], cfg["user_agent"])
    cache.cache_set(cache_key, token)
    return token


def _search_by_imdb(imdb_id: str, lang: str) -> List[Dict[str, Any]]:
    """
    SearchSubtitles podle IMDB ID. imdb_id ve formátu 'tt0944947' nebo
    '0944947'. OpenSubtitles chce číselné ID bez 'tt'.
    """
    if not imdb_id:
        return []

    token = _login_token()
    if not token:
        return []

    imdb_num = re.sub(r"[^\d]", "", imdb_id)
    if not imdb_num:
        return []

    cfg = _config()
    proxy = _make_proxy(cfg["user_agent"])
    try:
        res = proxy.SearchSubtitles(
            token,
            [{"imdbid": imdb_num, "sublanguageid": lang}],
            {"limit": 30},
        )
    except (xmlrpc.client.Fault, xmlrpc.client.ProtocolError, OSError) as exc:
        log.error("SearchSubtitles selhal: %s", exc)
        return []

    status = str(res.get("status") or "")
    if not status.startswith("200"):
        log.error("SearchSubtitles status=%r", status)
        return []

    data = res.get("data") or []
    if not isinstance(data, list):
        return []

    log.info("SearchSubtitles imdb=%s lang=%s -> %d výsledků",
             imdb_num, lang, len(data))
    return data


def _search_by_query(query: str, lang: str, season: Optional[int] = None,
                     episode: Optional[int] = None) -> List[Dict[str, Any]]:
    """Fallback: hledání podle textového dotazu (když nemáme IMDB ID)."""
    if not query:
        return []

    token = _login_token()
    if not token:
        return []

    cfg = _config()
    payload: Dict[str, Any] = {"query": query, "sublanguageid": lang}
    if season is not None and episode is not None:
        payload["season"] = str(season)
        payload["episode"] = str(episode)

    proxy = _make_proxy(cfg["user_agent"])
    try:
        res = proxy.SearchSubtitles(token, [payload], {"limit": 30})
    except (xmlrpc.client.Fault, xmlrpc.client.ProtocolError, OSError) as exc:
        log.error("SearchSubtitles(query) selhal: %s", exc)
        return []

    status = str(res.get("status") or "")
    if not status.startswith("200"):
        log.error("SearchSubtitles(query) status=%r", status)
        return []

    data = res.get("data") or []
    if not isinstance(data, list):
        return []

    log.info("SearchSubtitles q=%r lang=%s -> %d výsledků", query, lang, len(data))
    return data


# ---------------------------------------------------------------------------
# Výběr nejlepšího kandidáta + download
# ---------------------------------------------------------------------------

def _score_candidate(sub: Dict[str, Any], season: Optional[int] = None,
                     episode: Optional[int] = None) -> int:
    """
    Spočítá skóre kandidáta:
      - .srt formát   +50
      - vyšší download count
      - shoda epizody +200 (pokud zadáno)
      - shoda jazyka  +100
    """
    score = 0
    fmt = (sub.get("SubFormat") or "").lower()
    if fmt == "srt":
        score += 50

    try:
        score += int(sub.get("SubDownloadsCnt") or 0) // 100
    except (TypeError, ValueError):
        pass

    if season is not None and episode is not None:
        try:
            if int(sub.get("SeriesSeason") or 0) == int(season):
                score += 100
            if int(sub.get("SeriesEpisode") or 0) == int(episode):
                score += 100
        except (TypeError, ValueError):
            pass

    return score


def _download_subtitle(sub: Dict[str, Any], dst_path: str) -> Optional[str]:
    """
    Stáhne SubDownloadLink (.gz), rozbalí a uloží jako .srt.
    Vrací cestu nebo None.
    """
    url = sub.get("SubDownloadLink") or ""
    if not url:
        return None

    cfg = _config()
    req = Request(url, headers={"User-Agent": cfg["user_agent"]})

    # v0.0.79: Kodi se vypina - nezahajovat nove stahovani titulku
    if _shutdown.is_shutting_down():
        return None

    try:
        with urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
    except (HTTPError, URLError, OSError) as exc:
        log.error("Stahování titulků selhalo (%s): %s", url, exc)
        return None

    try:
        decoded = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    except (OSError, EOFError) as exc:
        log.error("Rozbalení .gz selhalo: %s", exc)
        return None

    # Pokus o detekci kódování – OpenSubtitles dává různé.
    enc_hint = (sub.get("SubEncoding") or "").lower()
    text: Optional[str] = None
    for enc in (enc_hint, "utf-8", "cp1250", "iso-8859-2", "latin1"):
        if not enc:
            continue
        try:
            text = decoded.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        text = decoded.decode("utf-8", errors="replace")

    try:
        with open(dst_path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError as exc:
        log.error("Zápis .srt selhal: %s", exc)
        return None

    return dst_path


# ---------------------------------------------------------------------------
# Veřejná funkce volaná z routeru
# ---------------------------------------------------------------------------

def _ascii_fold(s: str) -> str:
    """v0.0.71: rychly diacritic strip (Pelisky -> Pelisky, Vysehrad -> Vysehrad).

    OpenSubtitles fuzzy match nekdy nehleda s diakritikou - bez ni najde vic.
    """
    if not s:
        return s
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(c))


def fetch_for_title(
    imdb_id: Optional[str] = None,
    title: Optional[str] = None,
    year: Optional[int] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    mode: Optional[str] = None,  # akceptován kvůli zpětné kompatibilitě s routerem
    return_diagnostics: bool = False,
):
    """
    Najde a stáhne nejvhodnější .srt titulky.

    Strategie (v0.0.71 - viceurovnove pokusy):
      1) Pokud máme IMDB ID -> SearchSubtitles by imdbid.
      2) Query 'Title Year' (pokud znamy rok).
      3) Query 'Title' (bez roku).
      4) Query ASCII-folded 'Title' (bez diakritiky - "Pelisky").
      5) Query prvni 2-3 slova (pro slozite nazvy).
      6) Z výsledků vybere top podle _score_candidate.
      7) Stáhne, uloží do lokální cache, vrátí cestu.

    :param return_diagnostics: True = vraci (path | None, diag_dict)
                               s detaily o kazdem pokusu (pro debug UI).
                               False (default) = vraci jen path | None
                               (zpetna kompatibilita).
    """
    diag: Dict[str, Any] = {
        "imdb_id":    imdb_id or "",
        "title":      title or "",
        "year":       year,
        "lang":       "",
        "login_ok":   False,
        "attempts":   [],
        "candidates": 0,
        "selected":   "",
        "downloaded": "",
        "error":      "",
    }

    if not is_enabled():
        diag["error"] = "OpenSubtitles je vypnuto v Nastaveni"
        return (None, diag) if return_diagnostics else None

    cfg = _config()
    diag["lang"] = cfg["lang"]

    # ---- 1) Lokalni cache check ------------------------------------------
    fname_parts = [imdb_id or "", title or "", str(year or "")]
    if season is not None and episode is not None:
        fname_parts.append(f"S{int(season):02d}E{int(episode):02d}")
    fname_parts.append(cfg["lang"])
    safe = _safe_filename("_".join([p for p in fname_parts if p]))
    dst_path = os.path.join(_subs_dir(), f"{safe}.srt")

    if os.path.exists(dst_path) and os.path.getsize(dst_path) > 200:
        log.info("subtitles: hit local cache -> %s", dst_path)
        diag["downloaded"] = dst_path
        diag["selected"] = "(local cache)"
        diag["login_ok"] = True
        return (dst_path, diag) if return_diagnostics else dst_path

    # ---- 2) Pre-test loginu (lepsi UX kdyz heslo spatne) ------------------
    token_test = _login_token()
    if not token_test:
        diag["error"] = (
            "Login na OpenSubtitles.org SELHAL - zkontroluj uziv. jmeno + heslo "
            "v Nastaveni > Titulky. UA=" + (cfg["user_agent"] or "?")
        )
        return (None, diag) if return_diagnostics else None
    diag["login_ok"] = True

    # ---- 3) Multi-strategy search -----------------------------------------
    candidates: List[Dict[str, Any]] = []

    def _try_imdb():
        if not imdb_id:
            return
        res = _search_by_imdb(imdb_id, cfg["lang"])
        diag["attempts"].append({
            "kind":  "imdb",
            "input": imdb_id,
            "count": len(res),
        })
        candidates.extend(res)

    def _try_query(query: str, kind_label: str):
        if not query or len(query) < 2:
            return
        res = _search_by_query(query, cfg["lang"], season=season, episode=episode)
        diag["attempts"].append({
            "kind":  kind_label,
            "input": query,
            "count": len(res),
        })
        candidates.extend(res)

    _try_imdb()
    if not candidates and title:
        clean_title = title.strip()
        if year:
            _try_query(f"{clean_title} {year}", "query+year")
        if not candidates:
            _try_query(clean_title, "query")
        if not candidates:
            folded = _ascii_fold(clean_title)
            if folded and folded.lower() != clean_title.lower():
                _try_query(folded, "query+ascii")
        if not candidates:
            # Prvni 2-3 slova (pro "V uřkrytu Shelter" -> "V ukrytu")
            words = re.split(r"\s+", clean_title)
            if len(words) >= 3:
                short = " ".join(words[:2])
                _try_query(short, "query+short")

    diag["candidates"] = len(candidates)

    if not candidates:
        diag["error"] = (
            f"Webshare/TMDB Title=\"{title}\" Year={year} - "
            f"OpenSubtitles nenasel zadne titulky po {len(diag['attempts'])} "
            f"pokusech (vc. ASCII-fold + zkraceneho dotazu)."
        )
        log.info("subtitles: NO candidates (imdb=%s, title=%r, attempts=%s)",
                 imdb_id, title, diag["attempts"])
        return (None, diag) if return_diagnostics else None

    # ---- 4) Vyber nejlepsiho + dedup ident podle SubFileName ---------------
    candidates.sort(key=lambda s: _score_candidate(s, season, episode),
                     reverse=True)
    best = candidates[0]
    selected_name = (best.get("SubFileName") or best.get("MovieName") or "")
    diag["selected"] = selected_name
    log.info("subtitles: vybrán %s (downloads=%s, format=%s, total=%d)",
             selected_name, best.get("SubDownloadsCnt"),
             best.get("SubFormat"), len(candidates))

    # ---- 5) Download ------------------------------------------------------
    result = _download_subtitle(best, dst_path)
    if result:
        diag["downloaded"] = result
    else:
        diag["error"] = (
            f"Nalezene titulky pro \"{selected_name}\" se NEPODARILO stahnout "
            "(network chyba / corrupted gzip)."
        )
    return (result, diag) if return_diagnostics else result


# ---------------------------------------------------------------------------
# v0.0.79: Asynchronni subtitle attach
#
# Drive (v0.0.78 a starsi) plugin volal _attach_subtitles SYNCHRONNE PRED
# setResolvedUrl(). To znamenalo:
#   - quality picker dialog se zavre
#   - plugin proces ceka 5-15s na TMDB + OpenSubtitles
#   - notifikace "Titulky stazeny" se objevi
#   - setResolvedUrl spousti video player
#
# Vysledek (user report v0.0.78): "po spusteni filmu film problikne a mys to
# nepusti pod spodni polovinu". V pause 5-15s se hromadi mouse eventy, ktere
# pri startu playera spousti OSD a kurzor je omezen na OSD region.
#
# Tento helper presouva celou subtitle logiku do BACKGROUND THREADU:
#   1) setResolvedUrl probehne IHNED - video player startuje bez pause
#   2) Background thread caka az xbmc.Player().isPlayingVideo() == True
#   3) Az pak fetchuje titulky (volne, nezblokuje UI)
#   4) Volat xbmc.Player().setSubtitles(path) - Kodi titulky dynamicky pripoji
#
# Vyhody:
#   - zero blocking na play path
#   - zadne UI flash
#   - mys neuvazne v OSD modu
# Nevyhoda:
#   - titulky se objevi az s ~1-3s zpozdenim od startu videa (akceptovatelne)
# ---------------------------------------------------------------------------

# Max doba, kterou cekame na Player.isPlayingVideo()=True nez se vzdame.
_ATTACH_WAIT_PLAYER_MAX_S = 20
# Max doba, ktera muzeme stravit nad subtitles fetchem v background threadu.
# Kdyz OpenSubtitles + TMDB nedopovedou do techto sekund, ukoncime.
# (Pri shutdownu Kodi se thread sam vyhodi - kontrolujeme is_shutting_down.)
_ATTACH_FETCH_MAX_S = 30


def attach_async(title: str,
                  year: Optional[int] = None,
                  imdb_id: Optional[str] = None,
                  mode: str = "movie",
                  season: Optional[int] = None,
                  episode: Optional[int] = None,
                  imdb_resolver=None) -> None:
    """v0.0.79: Asynchronni titulkovy attach.

    Spustime daemon thread ktery:
      1) Pocka az Kodi opravdu zacne hrat video (Player.isPlayingVideo()).
      2) Fetchne CZ titulky pres OpenSubtitles.
      3) Pripoji je k bezicimu playeru.

    Plugin proces tak setResolvedUrl zavola IHNED a vraci se Kodi, ktere
    rovnou startuje video player. Zadna pause = zadny flicker = mys nezatuhne.

    :param imdb_resolver: volitelne callable(title, year, mode) -> imdb_id|""
                          (router-side, aby tento modul nemusel importovat
                          tmdb a tim porusit imports).
    """
    if not is_enabled():
        return

    try:
        import threading as _th
        t = _th.Thread(
            target=_attach_worker,
            args=(title, year, imdb_id, mode, season, episode, imdb_resolver),
            name=f"subtitles-attach-{(title or 'unknown')[:20]}",
            daemon=True,
        )
        t.start()
        log.debug("attach_async: thread spawned for %r", title)
    except Exception as exc:  # noqa: BLE001
        log.warning("attach_async spawn selhal: %s", exc)


def _attach_worker(title, year, imdb_id, mode, season, episode, imdb_resolver) -> None:
    """Worker: caka na player start, fetchne titulky, attachne."""
    try:
        import xbmc  # type: ignore
    except Exception:  # noqa: BLE001
        log.debug("attach_worker: xbmc unavailable, ending")
        return

    monitor = xbmc.Monitor()
    player = xbmc.Player()

    waited = 0
    while waited < _ATTACH_WAIT_PLAYER_MAX_S:
        if monitor.waitForAbort(1):
            return
        try:
            if player.isPlayingVideo():
                break
        except Exception:  # noqa: BLE001
            pass
        waited += 1
    else:
        log.debug("attach_worker: player nezacal hrat do %ds, koncim",
                  _ATTACH_WAIT_PLAYER_MAX_S)
        return

    # Player hraje. Krátká pauza, aby se Kodi UI ustálil před titulky fetch.
    if monitor.waitForAbort(1):
        return

    if _shutdown.is_shutting_down():
        return

    # Pokud nemame IMDB ID a mame router-side resolver, zkusime ho ziskat
    # (resolver typicky vola tmdb.search_* + tmdb.get_imdb_id).
    resolved_imdb = imdb_id or ""
    if not resolved_imdb and imdb_resolver is not None and title:
        try:
            resolved_imdb = imdb_resolver(title, year, mode) or ""
        except Exception as exc:  # noqa: BLE001
            log.debug("attach_worker: imdb_resolver selhal: %s", exc)

    if _shutdown.is_shutting_down():
        return

    try:
        srt = fetch_for_title(
            title=title,
            year=year,
            imdb_id=resolved_imdb or None,
            mode="episode" if mode in ("episode", "tv") else "movie",
            season=season,
            episode=episode,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("attach_worker fetch selhal: %s", exc)
        return

    if not srt:
        log.debug("attach_worker: zadne titulky najdene pro %r", title)
        return

    # Player jeste hraje? Kdyz user zavrel video mezi fetchem, neaplikujem.
    try:
        if not player.isPlayingVideo():
            log.debug("attach_worker: player uz nehraje, srt %s nikam nedavam", srt)
            return
        player.setSubtitles(srt)
        log.info("attach_worker: pripojil titulky %s", srt)
    except Exception as exc:  # noqa: BLE001
        log.warning("attach_worker setSubtitles selhal: %s", exc)
