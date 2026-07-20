# -*- coding: utf-8 -*-
"""
api_webshare.py
---------------
Modul pro komunikaci s Webshare API.

Po přihlášení uživatele načítá jeho seznam souborů a klasifikuje je do rubrik:
    - FILMY    (movies)
    - SERIÁLY  (series)
    - NOVINKY  (latest – vše seřazené podle data přidání)

Veřejné rozhraní:
    login(username, password)            -> str | None  (token)
    get_token(force_refresh=False)       -> str         (cachovaný token)
    fetch_files(token, page=1)           -> list[dict]
    classify_files(files)                -> (movies, series, latest)
    file_to_video_item(f)                -> dict
    get_movies(sort, page)               -> list[dict]
    get_series(sort, page)               -> list[dict]
    get_latest(sort, page)               -> list[dict]
    search(query, page)                  -> list[dict]
    get_stream_url(token, file_id, ...)  -> str

Formát „video item" (vrací se z get_movies / get_series / get_latest / search):
    {
        "id":     "string",   # Webshare ident
        "title":  "Název",
        "year":   2024 | None,
        "plot":   "Popis nebo prázdné",
        "poster": "http://..." | None,
        "type":   "movie" | "series" | "episode",
        "dubbed": True | False,
    }

Konkrétní HTTP detaily Webshare API jsou označeny komentářem TODO, aby je
bylo snadné doladit (parsování XML, plnohodnotný md5_crypt hash, atd.).
"""

from __future__ import annotations

import gzip
import hashlib
import io
import logging
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import cache
from . import clean_title as _ct
from . import shutdown as _shutdown


log = logging.getLogger("klempcinema.api_webshare")


def _shutdown_pool(pool) -> None:
    """v0.0.136: Ukonci ThreadPoolExecutor bez blokujiciho joinu.

    Kdyz Kodi posle abort, cekat na doběhnutí workeru zaseklych v urlopen()
    zdrzovalo vypinani Kodi (i minutu, hlavne pri enrichu na pozadi). Daemon
    vlakna poolu umrou s interpreterem, takze join nepotrebujeme. Pouzivame
    cancel_futures (Py 3.9+) k zahozeni jeste nespustenych uloh.
    """
    try:
        pool.shutdown(wait=False, cancel_futures=True)
    except TypeError:
        # Starsi Python bez cancel_futures - aspon neblokujici shutdown.
        try:
            pool.shutdown(wait=False)
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass

# Max paralelních vláken pro TMDB/ČSFD enrich. Víc = rychlejší,
# ale příliš mnoho současných requestů ohrozí rate-limit a riskne ban.
ENRICH_WORKERS = 4  # v0.0.126: 6->4 (mene paralelni sit pri dlouhem pouziti)
# v0.0.81: max cekani na enrich pred zobrazenim seznamu. Po uplynuti
# budgetu se zobrazi polozky s tim co stihlo dobehnout (zbytek = WS thumb).
# v0.0.130: 6->4 (po odstraneni CSFD staci TMDB se 4 vlakny).
ENRICH_MAX_WAIT_SEC = 4

# v0.0.130: globalni wall-clock strop na cele nacteni rubriky. Bez nej se
# cas scital linearne (max_ws_pages * ENRICH_MAX_WAIT_SEC) -> 40s+.
# Stejny princip jako TMDB_WS_FILTER_MAX_WAIT u platforem.
RUBRIKA_FETCH_MAX_WAIT = 12.0


# ---------------------------------------------------------------------------
# Konstanty
# ---------------------------------------------------------------------------

WEBSHARE_API_BASE = "https://webshare.cz/api"

# Minimální hlavičky - bez Origin/Referer (ty občas triggerují CSRF/bot
# ochranu na /api/ endpointech). Jen běžný browser UA.
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/xml, */*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}
# Webshare endpointy preferují formulářová data s tímto Content-Type:
FORM_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}
# v0.0.63: 15 -> 8s. v0.0.81: 8 -> 5s (rychlejsi shutdown + first load).
DEFAULT_TIMEOUT = 5

# Kolik souborů načítáme z Webshare na jednu stránku.
PAGE_LIMIT = 200


# ---------------------------------------------------------------------------
# Modulový cache tokenu
# ---------------------------------------------------------------------------

_TOKEN_CACHE: Optional[str] = None


def _addon_safe():
    """Vrátí xbmcaddon.Addon() nebo None, pokud běžíme mimo Kodi (testy)."""
    try:
        import xbmcaddon  # type: ignore
        return xbmcaddon.Addon()
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Pomocné věci
# ---------------------------------------------------------------------------

class _Response:
    """Minimální napodobenina requests.Response (status_code + text)."""

    __slots__ = ("status_code", "text", "headers")

    def __init__(self, status_code: int, text: str, headers: Optional[Dict[str, str]] = None) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


def _read_body(raw: Any, content_encoding: str = "") -> str:
    """Načte tělo odpovědi (s případnou gzip dekompresí) jako UTF-8 string."""
    data = raw.read()
    if (content_encoding or "").lower() == "gzip":
        try:
            data = gzip.GzipFile(fileobj=io.BytesIO(data)).read()
        except Exception:  # noqa: BLE001
            log.warning("_read_body: gzip dekomprese selhala, vracím raw.")
    return data.decode("utf-8", errors="replace")


def _request(method: str, url: str, data: Optional[Dict[str, Any]] = None,
             headers: Optional[Dict[str, str]] = None,
             timeout: int = DEFAULT_TIMEOUT) -> Optional[_Response]:
    """
    Tenký wrapper kolem urllib.request – žádné externí závislosti.

    Vrací _Response s atributy status_code a text (kompatibilní s původním
    kódem, který používal `resp.status_code` / `resp.text`).
    """
    merged: Dict[str, str] = dict(DEFAULT_HEADERS)
    if (method or "").upper() == "POST" and data:
        merged.update(FORM_HEADERS)
    if headers:
        merged.update(headers)

    body: Optional[bytes] = None
    if data is not None:
        body = urlencode({k: ("" if v is None else v) for k, v in data.items()}).encode("utf-8")

    req = Request(url, data=body, headers=merged, method=(method or "GET").upper())

    # v0.0.79: Pri shutdown nezahajovat nove HTTP requesty (zbytecne
    # blokujici timeout). Vraci None - caller zachazi jako se site chybou.
    if _shutdown.is_shutting_down():
        return None

    try:
        with urlopen(req, timeout=timeout) as resp:
            text = _read_body(resp, resp.headers.get("Content-Encoding", ""))
            return _Response(getattr(resp, "status", 200), text, dict(resp.headers.items()))
    except HTTPError as exc:
        try:
            text = _read_body(exc, exc.headers.get("Content-Encoding", "") if exc.headers else "")
        except Exception:  # noqa: BLE001
            text = ""
        return _Response(exc.code, text, dict(exc.headers.items()) if exc.headers else {})
    except URLError as exc:
        log.error("HTTP %s %s – síťová chyba: %s", method, url, exc)
        return None
    except Exception as exc:  # noqa: BLE001
        log.exception("HTTP %s %s selhalo: %s", method, url, exc)
        return None


def _parse_xml(text: str) -> Optional[ET.Element]:
    """Pokusí se rozparsovat XML odpověď Webshare. Vrací root element nebo None."""
    if not text:
        return None
    try:
        return ET.fromstring(text)
    except ET.ParseError as exc:
        log.error("Nelze parsovat XML odpověď: %s", exc)
        return None


def _xml_text(root: Optional[ET.Element], tag: str) -> str:
    """Najde první element <tag> v rootu a vrátí jeho text (jinak '')."""
    if root is None:
        return ""
    el = root.find(tag)
    return (el.text or "").strip() if el is not None else ""


def _check_status(root: Optional[ET.Element], context: str) -> bool:
    """
    Webshare vrací v každé odpovědi <status>OK</status> nebo
    <status>FATAL</status>...<code>...</code><message>...</message>.
    Vrací True jen pokud status == OK; jinak loguje detail.
    """
    if root is None:
        log.error("%s: žádná XML odpověď.", context)
        return False
    status = _xml_text(root, "status").upper()
    if status == "OK":
        return True
    code = _xml_text(root, "code")
    message = _xml_text(root, "message") or _xml_text(root, "message_human")
    log.error("%s: Webshare status=%s code=%s message=%s",
              context, status or "?", code or "-", message or "-")
    return False


def _md5_crypt(password: str, salt: str) -> str:
    """
    Pure-Python implementace klasického Unix md5-crypt (FreeBSD/Apache).
    Vrací řetězec ve formátu '$1$<salt>$<22-char-hash>'.

    Webshare používá pro login:
        password_hash = sha1( md5_crypt(password, salt) )

    Salt přichází z Webshare typicky jako '$1$xxxxxxxx$' (celý prefix).
    Implementace si z něj sama vytáhne 8-znakový salt.
    """
    # ---- Extrakce salt body -----------------------------------------------
    raw_salt = salt or ""
    if raw_salt.startswith("$1$"):
        raw_salt = raw_salt[3:]
    raw_salt = raw_salt.rstrip("$")
    if "$" in raw_salt:
        raw_salt = raw_salt.split("$", 1)[0]
    raw_salt = raw_salt[:8]

    pw = password.encode("utf-8")
    sl = raw_salt.encode("ascii", errors="ignore")

    # ---- Hlavní + alternativní hash ---------------------------------------
    ctx = hashlib.md5(pw + b"$1$" + sl)
    alt = hashlib.md5(pw + sl + pw).digest()

    plen = len(pw)
    i = plen
    while i > 16:
        ctx.update(alt)
        i -= 16
    ctx.update(alt[:i])

    i = plen
    while i > 0:
        if i & 1:
            ctx.update(b"\x00")
        else:
            ctx.update(pw[:1])
        i >>= 1

    final = ctx.digest()

    # ---- 1000 rotačních kol -----------------------------------------------
    for r in range(1000):
        c = hashlib.md5()
        if r & 1:
            c.update(pw)
        else:
            c.update(final)
        if r % 3:
            c.update(sl)
        if r % 7:
            c.update(pw)
        if r & 1:
            c.update(final)
        else:
            c.update(pw)
        final = c.digest()

    # ---- Vlastní base64-like kódování md5-crypt ---------------------------
    itoa64 = b"./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

    def _to64(v: int, n: int) -> bytes:
        out = bytearray()
        for _ in range(n):
            out.append(itoa64[v & 0x3F])
            v >>= 6
        return bytes(out)

    enc = (
        _to64((final[0] << 16) | (final[6] << 8) | final[12], 4)
        + _to64((final[1] << 16) | (final[7] << 8) | final[13], 4)
        + _to64((final[2] << 16) | (final[8] << 8) | final[14], 4)
        + _to64((final[3] << 16) | (final[9] << 8) | final[15], 4)
        + _to64((final[4] << 16) | (final[10] << 8) | final[5], 4)
        + _to64(final[11], 2)
    )
    return "$1$" + raw_salt + "$" + enc.decode("ascii")


def _webshare_password_hash(password: str, salt: str) -> str:
    """sha1(md5_crypt(password, salt)) -> hex – formát, který očekává Webshare."""
    return hashlib.sha1(_md5_crypt(password, salt).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Webshare přihlašovací údaje
# ---------------------------------------------------------------------------
# v0.0.72: Vestavěné (builtin) credentials byly zcela ODSTRANĚNY z balíčku
# pro veřejnou distribuci. Plugin nyní vyžaduje, aby si každý uživatel
# zadal vlastní Webshare účet při prvním spuštění (welcome flow v routeru
# zobrazí dialogy pro jméno a heslo a uloží je do settings.xml).
#
# Pokud `ws_user` nebo `ws_pass` v settings chybí, login() vrátí None
# a router uživatele přesměruje na welcome flow / nastavení.

# Konstanty zachovány jako prázdné pro zpětnou kompatibilitu (diagnostika
# v router.view_test_login je ještě může referencovat, ale s prázdnými
# hodnotami).
BUILTIN_WS_USER: str = ""
BUILTIN_WS_PASS: str = ""


def _read_credentials(addon) -> Tuple[str, str]:
    """
    Vrátí (username, password) výhradně z uživatelských settings.

    v0.0.72: builtin fallback odstraněn - pokud user nevyplnil
    ws_user/ws_pass v Settings (resp. v úvodním welcome dialogu),
    vrátí ("", "") a login() pak vrátí None.
    """
    try:
        u = (addon.getSetting("ws_user") or "").strip()
        p = addon.getSetting("ws_pass") or ""
    except Exception:  # noqa: BLE001
        return ("", "")
    return (u, p)


# ---------------------------------------------------------------------------
# 1) LOGIN
# ---------------------------------------------------------------------------

def login(username: str, password: str) -> Optional[str]:
    """
    Přihlášení k Webshare, vrátí token (wst) nebo None.

    Postup:
        1) POST /api/salt/   { "username_or_email": ... }
           -> <salt>$1$xxxxxxxx$</salt>
        2) password_hash = sha1( md5_crypt(password, salt) )
        3) POST /api/login/  { "username_or_email": ...,
                              "password": password_hash,
                              "keep_logged_in": 1 }
           -> <token>...</token>

    Při selhání loguje konkrétní status/code/message z Webshare.
    """
    if not username or not password:
        log.info("login(): chybí username nebo password.")
        return None

    # ---- 1) získání saltu -------------------------------------------------
    salt_resp = _request(
        "POST",
        f"{WEBSHARE_API_BASE}/salt/",
        data={
            "username_or_email": username,
            "wst": "",
        },
    )
    if salt_resp is None:
        return None
    if salt_resp.status_code != 200:
        log.error("login(): /salt/ HTTP %s body=%r",
                  salt_resp.status_code, (salt_resp.text or "")[:200])
        return None

    salt_root = _parse_xml(salt_resp.text)
    if not _check_status(salt_root, "login()/salt"):
        # Webshare vrátí code=AUTH_LOGIN_INVALID_USER apod. -> už zalogováno.
        return None

    salt = _xml_text(salt_root, "salt")
    if not salt:
        log.error("login(): odpověď /salt/ neobsahuje <salt>: %r",
                  (salt_resp.text or "")[:200])
        return None

    log.debug("login(): získán salt délky %d.", len(salt))

    # ---- 2) hash hesla ----------------------------------------------------
    try:
        password_hash = _webshare_password_hash(password, salt)
    except Exception:  # noqa: BLE001
        log.exception("login(): chyba při výpočtu password hash.")
        return None

    # ---- 3) login ---------------------------------------------------------
    login_resp = _request(
        "POST",
        f"{WEBSHARE_API_BASE}/login/",
        data={
            "username_or_email": username,
            "password": password_hash,
            "keep_logged_in": 1,
            "wst": "",
        },
    )
    if login_resp is None:
        return None
    if login_resp.status_code != 200:
        log.error("login(): /login/ HTTP %s body=%r",
                  login_resp.status_code, (login_resp.text or "")[:200])
        return None

    login_root = _parse_xml(login_resp.text)
    if not _check_status(login_root, "login()/login"):
        return None

    token = _xml_text(login_root, "token")
    if not token:
        log.error("login(): odpověď /login/ neobsahuje <token>: %r",
                  (login_resp.text or "")[:200])
        return None

    log.info("login(): úspěšné přihlášení uživatele %s.", username)
    return token


def get_token(force_refresh: bool = False) -> str:
    """
    Vrátí platný Webshare token. Logika:
        1) cache v paměti (_TOKEN_CACHE),
        2) hodnota ws_token v addon settings,
        3) login pomocí ws_user / ws_pass + uložení do settings.

    Vrací prázdný string, pokud login selže nebo nejsou údaje.
    """
    global _TOKEN_CACHE

    if not force_refresh and _TOKEN_CACHE:
        return _TOKEN_CACHE

    addon = _addon_safe()
    if addon is None:
        return _TOKEN_CACHE or ""

    if not force_refresh:
        stored = addon.getSetting("ws_token") or ""
        if stored:
            _TOKEN_CACHE = stored
            return stored

    username, password = _read_credentials(addon)
    if not username or not password:
        log.warning("get_token(): chybí Webshare credentials (settings i builtin).")
        return ""

    new_token = login(username, password)
    if not new_token:
        # Smaž případně zaseknutý starý token, ať při dalším pokusu
        # opět projde celá login sekvence.
        _TOKEN_CACHE = None
        try:
            addon.setSetting("ws_token", "")
        except Exception:  # noqa: BLE001
            pass
        return ""

    _TOKEN_CACHE = new_token
    try:
        addon.setSetting("ws_token", new_token)
    except Exception:  # noqa: BLE001
        pass
    return new_token


def _invalidate_token() -> None:
    """Smaže cache + persistovaný token (např. při expirovaném wst)."""
    global _TOKEN_CACHE, _DIAGNOSE_CACHE, _DIAGNOSE_CACHE_TS
    _TOKEN_CACHE = None
    _DIAGNOSE_CACHE = None
    _DIAGNOSE_CACHE_TS = 0.0
    addon = _addon_safe()
    if addon is not None:
        try:
            addon.setSetting("ws_token", "")
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Self-test pro Webshare (diagnostika přihlášení)
# ---------------------------------------------------------------------------

# Cache výsledku login_diagnose - chrání proti rate-limitu z Webshare.
_DIAGNOSE_CACHE: Optional[Dict[str, Any]] = None
_DIAGNOSE_CACHE_TS: float = 0.0
_DIAGNOSE_TTL = 300  # 5 minut


def login_diagnose(force: bool = False) -> Dict[str, Any]:
    """
    Otestuje Webshare přihlášení. Výsledek se cachuje 5 minut, aby každé
    otevření menu nezpůsobilo nový login request (Webshare rate-limituje).

    Vrací dict:
        {
          "ok": bool,
          "code": str,
          "message": str,
          "stage": str,
        }
    """
    global _DIAGNOSE_CACHE, _DIAGNOSE_CACHE_TS

    if not force and _DIAGNOSE_CACHE is not None:
        if (time.time() - _DIAGNOSE_CACHE_TS) < _DIAGNOSE_TTL:
            return _DIAGNOSE_CACHE

    addon = _addon_safe()
    if addon is None:
        result = {"ok": False, "stage": "config",
                  "code": "NO_ADDON", "message": "No xbmcaddon"}
        _DIAGNOSE_CACHE = result
        _DIAGNOSE_CACHE_TS = time.time()
        return result

    username, password = _read_credentials(addon)

    if not username or not password:
        result = {"ok": False, "stage": "config",
                  "code": "MISSING_CREDENTIALS",
                  "message": "Chybí jméno nebo heslo (builtin i Settings)"}
        _DIAGNOSE_CACHE = result
        _DIAGNOSE_CACHE_TS = time.time()
        return result

    # OPTIMALIZACE: pokud máme cached token, otestuj ho lacino přes
    # /api/user_data/ místo fresh login - šetří login attempts.
    cached_token = _TOKEN_CACHE or (addon.getSetting("ws_token") or "").strip()
    if not force and cached_token:
        try:
            user_resp = _request(
                "POST",
                f"{WEBSHARE_API_BASE}/user_data/",
                data={"wst": cached_token},
            )
            if user_resp is not None and user_resp.status_code == 200:
                ud_root = _parse_xml(user_resp.text)
                if ud_root is not None and _xml_text(ud_root, "status").upper() == "OK":
                    log.info("login_diagnose: existující token funguje (user_data OK)")
                    result = {"ok": True, "stage": "ok", "code": "OK",
                              "message": f"Přihlášeno jako {username} (cached token)"}
                    _DIAGNOSE_CACHE = result
                    _DIAGNOSE_CACHE_TS = time.time()
                    return result
        except Exception as exc:  # noqa: BLE001
            log.debug("login_diagnose: test cached tokenu selhal: %s", exc)

    # Pokud cached token nefunguje (nebo není), proveď fresh login.
    result = _do_fresh_login(username, password)
    _DIAGNOSE_CACHE = result
    _DIAGNOSE_CACHE_TS = time.time()
    return result


def _do_fresh_login(username: str, password: str) -> Dict[str, Any]:
    """Skutečný 2-krokový login (salt + login). Volá se z login_diagnose."""

    # --- /salt/ -------------------------------------------------------------
    salt_resp = _request(
        "POST",
        f"{WEBSHARE_API_BASE}/salt/",
        data={"username_or_email": username, "wst": ""},
    )
    if salt_resp is None:
        return {"ok": False, "stage": "network",
                "code": "NETWORK",
                "message": "Webshare nedosažitelný (síť)"}
    if salt_resp.status_code != 200:
        return {"ok": False, "stage": "salt",
                "code": f"HTTP_{salt_resp.status_code}",
                "message": f"HTTP {salt_resp.status_code}"}

    log.info("login_diagnose: /salt/ status=%s body[:300]=%r",
             salt_resp.status_code, (salt_resp.text or "")[:300])

    salt_root = _parse_xml(salt_resp.text)
    if salt_root is None:
        return {"ok": False, "stage": "salt",
                "code": "BAD_XML", "message": "Nelze parsovat XML",
                "raw": (salt_resp.text or "")[:500]}

    status = _xml_text(salt_root, "status").upper()
    if status != "OK":
        return {
            "ok": False, "stage": "salt",
            "code": _xml_text(salt_root, "code") or "AUTH_LOGIN_INVALID_USER",
            "message": (_xml_text(salt_root, "message_human")
                        or _xml_text(salt_root, "message")
                        or "Neznámá chyba (salt fáze)"),
            "raw": (salt_resp.text or "")[:500],
        }

    salt = _xml_text(salt_root, "salt")
    if not salt:
        return {"ok": False, "stage": "salt",
                "code": "NO_SALT", "message": "Chybí <salt> v odpovědi"}

    # --- /login/ ------------------------------------------------------------
    try:
        password_hash = _webshare_password_hash(password, salt)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "stage": "login",
                "code": "HASH_ERROR", "message": f"Hash error: {exc}"}

    login_resp = _request(
        "POST",
        f"{WEBSHARE_API_BASE}/login/",
        data={
            "username_or_email": username,
            "password": password_hash,
            "keep_logged_in": 1,
            "wst": "",
        },
    )
    if login_resp is None:
        return {"ok": False, "stage": "network",
                "code": "NETWORK",
                "message": "Webshare nedosažitelný (login fáze)"}
    if login_resp.status_code != 200:
        return {"ok": False, "stage": "login",
                "code": f"HTTP_{login_resp.status_code}",
                "message": f"HTTP {login_resp.status_code}"}

    log.info("login_diagnose: /login/ status=%s body[:300]=%r",
             login_resp.status_code, (login_resp.text or "")[:300])

    login_root = _parse_xml(login_resp.text)
    if login_root is None:
        return {"ok": False, "stage": "login",
                "code": "BAD_XML", "message": "Nelze parsovat XML",
                "raw": (login_resp.text or "")[:500]}

    status = _xml_text(login_root, "status").upper()
    if status != "OK":
        return {
            "ok": False, "stage": "login",
            "code": _xml_text(login_root, "code") or "AUTH_LOGIN_FAILED",
            "message": (_xml_text(login_root, "message_human")
                        or _xml_text(login_root, "message")
                        or "Neznámá chyba (login fáze)"),
            "raw": (login_resp.text or "")[:500],
        }

    token = _xml_text(login_root, "token")
    if not token:
        return {"ok": False, "stage": "login",
                "code": "NO_TOKEN", "message": "Chybí <token> v odpovědi"}

    # --- OK -> ulož do cache i settings ------------------------------------
    global _TOKEN_CACHE
    _TOKEN_CACHE = token
    addon = _addon_safe()
    if addon is not None:
        try:
            addon.setSetting("ws_token", token)
        except Exception:  # noqa: BLE001
            pass

    return {"ok": True, "stage": "ok",
            "code": "OK", "message": f"Přihlášeno jako {username}"}


# ---------------------------------------------------------------------------
# 2) FETCH FILES (seznam souborů uživatele)
# ---------------------------------------------------------------------------

def _files_from_xml(root: Optional[ET.Element]) -> List[Dict[str, Any]]:
    """Z XML rootu vytáhne všechny <file>...</file> jako list dictů."""
    if root is None:
        return []
    out: List[Dict[str, Any]] = []
    for fe in root.iter("file"):
        f: Dict[str, Any] = {}
        for child in fe:
            if child.tag and child.text is not None:
                f[child.tag] = child.text.strip()
        if f.get("ident"):
            out.append(f)
    return out


def fetch_files(token: str, page: int = 1, _retry: bool = True) -> List[Dict[str, Any]]:
    """
    Načte seznam souborů z Webshare účtu pro danou stránku.

    Vrací list dictů (ident, name, added, size, img, ...).
    Pokud token expiroval, jednou se pokusí znovu přihlásit.
    """
    if not token:
        log.info("fetch_files(): chybí token.")
        return []

    page = max(1, int(page or 1))
    offset = (page - 1) * PAGE_LIMIT

    resp = _request(
        "POST",
        f"{WEBSHARE_API_BASE}/file_list/",
        data={
            "wst": token,
            "offset": offset,
            "limit": PAGE_LIMIT,
            "sort": "recent",
        },
    )
    if resp is None or resp.status_code != 200:
        log.error("fetch_files(): HTTP chyba (status=%s).",
                  getattr(resp, "status_code", "?"))
        return []

    root = _parse_xml(resp.text)
    if not _check_status(root, "fetch_files"):
        # Možná expirovaný token -> jeden retry s force loginem.
        if _retry:
            _invalidate_token()
            new_token = get_token(force_refresh=True)
            if new_token and new_token != token:
                return fetch_files(new_token, page=page, _retry=False)
        return []

    files = _files_from_xml(root)
    log.debug("fetch_files(page=%s) -> %d souborů", page, len(files))
    return files


# ---------------------------------------------------------------------------
# 3) KLASIFIKACE (FILMY / SERIÁLY / NOVINKY + dabing)
# ---------------------------------------------------------------------------

# Explicitní CZ/SK dabing - MUSI obsahovat cz/sk/czech/slovak u slova dab/dub.
# v0.0.107: NE pouhe "dubbed"/"dabing" (spanelsky/portugalsky upload = false CZ).
_EXPLICIT_CZ_SK_DUB_PATTERN = re.compile(
    r"(?:^|[\W_])(?:"
    r"cz[\W_]*dub(?:bed)?|"
    r"sk[\W_]*dub(?:bed)?|"
    r"cz[\W_]*dab(?:ing|ovan[ýé])?|"
    r"sk[\W_]*dab(?:ing|ovan[ýé])?|"
    r"cz[-_ ]?sk[-_ ]?dab|sk[-_ ]?cz[-_ ]?dab|"
    r"czech[-_ ]?dub|slovak[-_ ]?dub|"
    r"dubcz|dubsk"
    r")(?:[\W_]|$)",
    re.IGNORECASE,
)

# Zpetna kompatibilita (nepouzivat pro detekci CZ - prilis volne).
_EXPLICIT_DUB_PATTERN = _EXPLICIT_CZ_SK_DUB_PATTERN

# v0.0.104: bezne na WS - "Michael.WEB-DL.CZ.5.1" = CZ audio stopa (ne titulky).
# Drive v0.0.61 striktne .cz. bez slova dabing -> false -> film mimo Novinky dab.
_CZ_AUDIO_TRACK_RE = re.compile(
    r"[\W_](?:cz|sk)[\W_]+(?:"
    r"5\.1|7\.1|2\.0|"
    r"ddp\d?|dd5\.1|dd7\.1|aac|ac3|dts|atmos|truehd"
    r")(?:[\W_]|$)",
    re.IGNORECASE,
)

# Generic CZ/SK marker - může znamenat dabing nebo titulky (NE dual/multi).
_GENERIC_CZ_SK_PATTERN = re.compile(
    r"(?:^|[\W_])(?:cz|sk|czech|slovak|cz/sk|sk/cz)(?:[\W_]|$)",
    re.IGNORECASE,
)

# Cizi dabing v nazvu (ES/PT/FR/DE...) - bez CZ signalu neni cesky dab.
_FOREIGN_DUB_LANG_RE = re.compile(
    r"(?:^|[\W_])(?:"
    r"es|esp|spa|spanish|castellano|latino|"
    r"pt|portuguese|portugues|brasil|brazil|"
    r"fr|french|francais|"
    r"de|german|deutsch|ger|"
    r"it|italian|italiano|"
    r"ru|russian|ruski|"
    r"hu|hungarian|magyar|"
    r"jp|japanese|jap|"
    r"kr|korean|kor|"
    r"nl|dutch|nederlands|"
    r"tr|turkish"
    r")(?:[\W_]|$)",
    re.IGNORECASE,
)

# Cizi audio stopa typu .ES.5.1 / .PT.7.1 (jako CZ.5.1 u Michaela).
_FOREIGN_AUDIO_TRACK_RE = re.compile(
    r"[\W_](?:es|pt|fr|de|it|ru|hu|jp|kr|spa|lat)[\W_]+(?:"
    r"5\.1|7\.1|2\.0|"
    r"ddp\d?|dd5\.1|dd7\.1|aac|ac3|dts|atmos|truehd"
    r")(?:[\W_]|$)",
    re.IGNORECASE,
)

# Pouhe "dubbed"/"dabing" bez CZ = typicky cizi nebo EN upload.
_BARE_DUB_WORD_RE = re.compile(
    r"(?:^|[\W_])(?:dubbed|dabing|dub)(?:[\W_]|$)",
    re.IGNORECASE,
)

_DUAL_AUDIO_HINT = re.compile(r"\bdual\b", re.IGNORECASE)

# Zachováno pro zpětnou kompatibilitu (používá se v UI badges).
_DUB_PATTERN = re.compile(
    r"(?:^|[\W_])(?:cz|sk|cz[-_ ]?dab|sk[-_ ]?dab|dab(?:ing|ovan[ýé])?|"
    r"czech|slovak|dual|multi|cz/sk|sk/cz)(?:[\W_]|$)",
    re.IGNORECASE,
)

# Polské markery v názvu (lektor PL, dubbing PL, polski, napisy PL).
# Cílem je rozpoznat soubory, které jsou JEN polsky, aby se v CZ/SK
# rubrikách neobjevily jako údajný "dabing".
_POLISH_PATTERN = re.compile(
    r"(?:^|[\W_])(?:"
    r"lektor[-_ ]?pl|lektor|"
    r"dubbing[-_ ]?pl|dubbingpl|"
    r"polski|polsky|polish|"
    r"napisy[-_ ]?pl|napisypl|"
    r"pl[-_ ]?dub|pldub|"
    r"\.pl\b|_pl_|/pl/"
    r")(?:[\W_]|$)",
    re.IGNORECASE,
)

# Polské markery které samostatně nejsou jistotou, ale dohromady ano.
# Plus výskyt CZ markeru je výjimka (multi-dub soubor).
_CZ_OR_SK_PATTERN = re.compile(
    r"(?:^|[\W_])(?:cz|sk|czech|slovak|cest|sktit|cestit)(?:[\W_]|$)",
    re.IGNORECASE,
)
# České/slovenské titulky - hledáme explicitní markery v názvu souboru.
# Záměrně NEzahrnujeme samotné "CZ" / "SK" (to je v _DUB_PATTERN pro dabing).
# Sledujeme: tit, titulky, sub, subs, subtitles, forced subs, hardsubs.
_SUB_PATTERN = re.compile(
    r"(?:^|[\W_])(?:"
    r"cz[-_ ]?(?:tit|titulky|sub|subs|subtitles)|"
    r"sk[-_ ]?(?:tit|titulky|sub|subs|subtitles)|"
    r"(?:tit|titulky|sub|subs|subtitles)[-_ ]?cz|"
    r"(?:tit|titulky|sub|subs|subtitles)[-_ ]?sk|"
    r"forced[-_ ]?cz|hardsub[-_ ]?cz|"
    r"cestit|sktit"  # zkratky bez separator
    r")(?:[\W_]|$)",
    re.IGNORECASE,
)
_SERIES_PATTERN = re.compile(r"s\d{1,2}\s*[ex]\s*\d{1,3}", re.IGNORECASE)
_MOVIE_HINTS = re.compile(
    r"(?:19\d{2}|20\d{2}|1080p|720p|2160p|4k|bluray|brrip|webrip|web-dl|hdrip|dvdrip|"
    r"x264|x265|hevc|h\.?264|h\.?265)",
    re.IGNORECASE,
)


def _has_strong_cz_dub_marker(name: str) -> bool:
    """Spolehlivy CZ/SK dabing v nazvu (ne ciste EN/ES/PT 'dubbed')."""
    if not name:
        return False
    if _EXPLICIT_CZ_SK_DUB_PATTERN.search(name):
        return True
    if _CZ_AUDIO_TRACK_RE.search(name):
        return True
    if _GENERIC_CZ_SK_PATTERN.search(name):
        if _SUB_PATTERN.search(name):
            return False
        has_diacritic = bool(_CZ_DIACRITICS_RE.search(name))
        has_cz_word = bool(_CZ_WORDS_RE.search(name))
        cz_marker_count = len(_GENERIC_CZ_SK_PATTERN.findall(name))
        if has_diacritic or has_cz_word or cz_marker_count >= 2:
            return True
    return False


def _is_foreign_dub_only(name: str) -> bool:
    """
    True pokud nazev spis ukazuje na cizi dabing (ES/PT/FR...) nebo
    obecne 'dubbed' bez CZ - typicky false positive v Novinkach dab.
    """
    if not name:
        return False
    if _has_strong_cz_dub_marker(name):
        return False
    if _FOREIGN_DUB_LANG_RE.search(name):
        return True
    if _FOREIGN_AUDIO_TRACK_RE.search(name):
        return True
    if _BARE_DUB_WORD_RE.search(name):
        return True
    if _DUAL_AUDIO_HINT.search(name):
        # Dual bez CZ markeru = casto EN+ES, ne CZ dabing.
        return True
    return False


def _detect_dubbed(name: str) -> bool:
    """
    True pokud název obsahuje marker CZ/SK DABINGU (ne pouze titulků).

    Logika:
      1) Polské-only soubory -> False (PL lektor != CZ dabing).
      2) Explicitní dab marker ("CZ dab", "dabing", "dubbed") -> True.
      3) Generic CZ/SK marker + zároveň sub marker ("cz tit", "sub cz") -> False.
      4) v0.0.61: Generic CZ/SK marker BEZ jakehokoli jineho CZ signalu
         (no diakritiky, no ceskych slov, no "dub" keyword) -> False.
         User feedback: "in Adams interest.cz.mkv" mel bare ".cz." tag
         ale nebyl realne cesky. Tyhle pripady ted dropnou badge.
      5) Generic CZ/SK marker + Czech signal -> True.
      6) v0.0.107: Cizi dabing (ES/PT/FR...) nebo bare 'dubbed' bez CZ -> False.
    """
    if not name:
        return False
    if _is_polish_only(name):
        return False
    if _is_foreign_dub_only(name):
        return False
    return _has_strong_cz_dub_marker(name)


def _detect_subtitles(name: str) -> bool:
    """True pokud název obsahuje explicitní marker českých/slovenských titulků."""
    return bool(_SUB_PATTERN.search(name or ""))


def _is_polish_only(name: str) -> bool:
    """
    True pokud soubor je JEN polsky (má PL markery a NEMÁ CZ/SK markery).
    Vyhodnocuje se podle názvu souboru.

    Pomáhá vyhodit polské soubory z CZ/SK rubrik (např. polský lektor
    se objeví v "dabovaných filmech" jako falešné CZ).
    """
    if not name:
        return False
    has_pl = bool(_POLISH_PATTERN.search(name))
    if not has_pl:
        return False
    has_cz_sk = bool(_CZ_OR_SK_PATTERN.search(name))
    if has_cz_sk:
        return False  # multi-dub soubor s PL i CZ - to je OK
    return True


_OBFUSCATED_WORD_RE = re.compile(r"^[bcdfghjklmnpqrstvwxyz]{1,5}$", re.IGNORECASE)


def _looks_obfuscated(title: str) -> bool:
    """
    True pokud titul vypadá jako obfuskovaný (zašifrovaný) - např.
    "H H JNDH" nebo "X K LMN". Heuristika:
      - aspoň 2 'slova'
      - aspoň polovina slov nemá žádnou samohlásku (jen souhlásky 1-5 znaků)

    Tyto soubory bývají pirátské encryptované uploady, ne reálné filmy -
    nemají TMDB match, neukáží plakát, jen kazí UX.
    """
    if not title:
        return False
    words = [w for w in re.split(r"\s+", title.strip()) if w]
    if len(words) < 2:
        return False
    obf_count = sum(1 for w in words if _OBFUSCATED_WORD_RE.match(w))
    # Nadpoloviční většina slov bez samohlásek = obfuskace.
    return obf_count >= max(2, (len(words) + 1) // 2)


_TRAILING_NUM_RE = re.compile(r"^(.*?)\s+(\d{1,3})\s*$")


# Patterny pro detekci "ne-film" obsahu (prednasky, popisy v nazvu, atd.)
_LECTURE_HINTS = re.compile(
    r"\b(?:"
    # Prednaska / vyklad
    r"prednaska|prednášk\w*|lecture|"
    r"pjakin\w*|pjakina|"  # V. V. Pjakin - znamy ruski lektor
    # Datum v nazvu ("ze dne 16 02", "z 30.3.")
    r"ze\s+dne|"
    r"z\s+dne|"
    # Popisne texty v nazvu (uploader napsal popis)
    r"kdo\s+chce|"
    r"nutne|nutně|"
    r"povinne|povinně|"
    r"musite\s+videt|musíte\s+vidět|"
    r"otazka|otázka|"
    r"odpoved|odpověď|"
    r"komentar|komentář|"
    # Specificke neulpitelne tagy
    r"viz\b|"
    r"klikn\w*|"
    # WS spam markery
    r"pls\b|please\b"
    r")\b",
    re.IGNORECASE,
)

# Pattern pro samostatne datumy ("16 02 2026", "16.02.2026")
_DATE_IN_NAME_RE = re.compile(r"\b\d{1,2}[.\s_-]\d{1,2}[.\s_-]\d{2,4}\b")


def _looks_like_lecture_or_description(title: str) -> bool:
    """
    True pokud titul vypada jako prednaska, popisek WS uploaderu nebo
    obecne ne-film obsah. Vyhazi se z rubrik s filmy.

    Heuristika:
      - Obsahuje znama "lecture" slova (prednaska, Pjakin, ...)
      - Obsahuje popisne fraze v nazvu ("ze dne", "kdo chce", "nutne videt")
      - Obsahuje datum ve formatu DD.MM.YYYY
      - Ma > 8 slov (typicky popis, ne nazev filmu - filmy maji 1-7 slov)
    """
    if not title:
        return False
    if _LECTURE_HINTS.search(title):
        return True
    if _DATE_IN_NAME_RE.search(title):
        return True
    # Pocet slov - filmy maji obvykle do 7 slov v titulu.
    # ("The Lord of the Rings: The Return of the King" = 10 slov, hranicni)
    words = [w for w in re.split(r"\s+", title.strip()) if w]
    if len(words) > 10:
        return True
    return False


def _looks_like_serial_episode_by_number(name: str) -> bool:
    """
    True pokud název vypadá jako díl série podle ČÍSLA NA KONCI
    (např. "GOAT 01", "H H JNDH 05") - bez explicitního SxxEyy markeru.

    Tato heuristika je doplněk k is_series_name pro pirátské uploady,
    které číslují díly jen jako " 01", " 02", ...
    Aplikuje se jen na čerstvé Webshare výsledky pro rubriky Filmy.
    """
    if not name:
        return False
    cleaned = _ct.clean_title(name)
    m = _TRAILING_NUM_RE.match(cleaned)
    if not m:
        return False
    base = m.group(1).strip()
    num = int(m.group(2))
    # Pokud základ je krátký (< 4 znaky) a číslo je 1-50, vypadá to
    # jako série dílů. Pokud základ je delší ("Matrix 2", "Karate Kid 3"),
    # může to být sequel filmu - necháme projít.
    if len(base) <= 4 and 1 <= num <= 99:
        return True
    if _looks_obfuscated(base) and 1 <= num <= 99:
        return True
    return False


def _detect_type(name: str) -> Optional[str]:
    """
    Vrátí 'series' / 'movie' nebo None pokud nelze rozhodnout.

    Pro samotnou klasifikaci series/movie deleguje na
    clean_title.detect_media_type (SxxEyy / NxNN / "Season X").
    Když ani po tom není SxxEyy a název obsahuje quality/year markery,
    má smysl tvrdit "movie" (jinak by spousta filmů spadla pod None).
    """
    if not name:
        return None
    if _ct.is_series_name(name):
        return "series"
    if _MOVIE_HINTS.search(name):
        return "movie"
    # poslední fallback: detect_media_type rozhodne dle dalších heuristik
    return _ct.detect_media_type(name)


def classify_files(
    files: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Z přijatého seznamu souborů vytvoří tři seznamy:
        - movies: filmy
        - series: seriály
        - latest: všechny soubory, seřazené podle data přidání (sestupně)

    Zároveň do každého souboru doplní:
        - type:   "movie" / "series"
        - dubbed: True / False
    """
    movies: List[Dict[str, Any]] = []
    series: List[Dict[str, Any]] = []
    latest: List[Dict[str, Any]] = []

    for f in files:
        name = (f.get("name") or "").strip()
        f["dubbed"] = _detect_dubbed(name)
        f["subs_cz"] = _detect_subtitles(name)

        kind = _detect_type(name)
        if kind == "series":
            f["type"] = "series"
            series.append(f)
        elif kind == "movie":
            f["type"] = "movie"
            movies.append(f)
        else:
            # neurčeno – necháváme bez typu (= jen v Novinkách)
            f.setdefault("type", "movie")

        latest.append(f)

    latest = sorted(latest, key=lambda x: x.get("added") or "", reverse=True)
    return movies, series, latest


# ---------------------------------------------------------------------------
# 4) Převod Webshare souboru -> "video item" pro UI
# ---------------------------------------------------------------------------

# Pozn.: rok / quality / SxxEyy cleaning je centrálně v clean_title.py.
# Tady už zůstává jen _QUALITY_TIERS (kvalitnostní žebříček pro picker)
# a _SERIES_PATTERN (rychlý test, jestli název obsahuje SxxEyy).

# Žebříček kvality – pro řazení variant ve výběrovém dialogu.
_QUALITY_TIERS = [
    ("2160p", 100), ("4K", 100), ("UHD", 100),
    ("1080p", 80),
    ("BluRay", 70),
    ("WEB-DL", 65), ("WEBRip", 65),
    ("720p", 60),
    ("HDRip", 50),
    ("DVDRip", 45),
    ("480p", 40),
]

# Žebříček zvuku – preferujeme prostorový/objektový zvuk (Atmos / DTS:X).
# Vyšší = lepší. Detekce jen z názvu souboru (jiné info nemáme).
_AUDIO_TIERS = [
    # Object-based (Atmos / DTS:X) - nejvyssi priorita
    ("ATMOS",     r"\batmos\b", 60),
    ("DTS-X",     r"\bdts[-_.: ]?x\b", 60),
    # Lossless surround
    ("TRUEHD",    r"\b(?:true[-_.: ]?hd|truehd)\b", 50),
    ("DTS-HD MA", r"\bdts[-_.: ]?hd[-_.: ]?ma\b", 50),
    ("DTS-HD",    r"\bdts[-_.: ]?hd\b", 45),
    # Lossy surround
    ("DTS",       r"\bdts\b", 35),
    ("DD+",       r"\b(?:e[-_.]?ac[-_.]?3|eac3|dd\+|ddp|dolby[-_.: ]?digital[-_.: ]?plus)\b", 30),
    ("DD 5.1",    r"\b(?:ac[-_.]?3|dd5\.?1|dolby[-_.: ]?digital(?!\s*plus))\b", 22),
    # 6CH / 5CH - bez \b za ch (Python pocita _ jako \w -> 6CH_x264 jinak neprojde)
    ("5.1",       r"[\W_.-](?:5\.1|5ch|6ch|surround)(?:[\W_.-]|$)", 13),
    ("7.1",       r"[\W_.-](?:7\.1|7ch|8ch)(?:[\W_.-]|$)", 15),
    # Channel layout (může jit samostatně i ke kodeku)
    ("7.1",       r"\b(?:7\.1|7ch)\b", 15),
    ("5.1",       r"\b(?:5\.1|5ch|surround)\b", 12),
    # Lossless / hi-res music
    ("FLAC",      r"\bflac\b", 10),
    # Generic / lossy
    ("AAC",       r"\baac\b", 5),
    ("MP3",       r"\bmp3\b", 1),
]


def _normalize_title(name: str) -> str:
    """
    Z názvu souboru udělá 'čistý' titul (bez kvality, codec, source, jazyka, roku).
    Používá centrální clean_title modul.

    Příklad: 'Inception.2010.1080p.BluRay.CZ.x264.mkv' -> 'Inception'
    """
    return _ct.clean_title(name)


def _series_name(name: str) -> str:
    """Z 'Game.of.Thrones.S01E02.CZ.mkv' udělá 'Game of Thrones'."""
    return _ct.clean_series_name(name)


def _episode_base_title(name: str) -> str:
    """Pro epizody: titul až po SxxEyy včetně (bez quality info za ním)."""
    return _ct.episode_base_title(name)


# ---- Filtr "vyhoď seriálové epizody" --------------------------------------

# v0.0.60: Detekce ceskych TV dokumentu / dokumentarnich seriálu z ČT,
# které nemaji standardni SxxEyy format, ale jsou jasne epizody (ne filmy).
# Filename casto pouziva tecky/underscores misto mezer - regex pouziva
# [\s._-] separator class aby chytl vsechny varianty.
# Priklady ze screenshotu uzivatele:
#   "Skryté.skvosty.III.05.Valdštejn.doku.cyklus.ČT.mkv"
#   "13.komnata.Chantal.Poullain.dokument.ČT.mkv"
#   "Toulava_kamera_2026-01-05.mkv"
_SEP = r"[\s._\-]+"
_CZECH_TV_DOC_RE = re.compile(
    r"(?:"
    r"doku" + _SEP + r"cyklus|"
    r"dokument(?:[áa]rn[íi])?" + _SEP + r"[Čč][Tt]|"
    r"[Čč][Tt]" + _SEP + r"(?:doku|dokument)|"
    r"\d+" + _SEP + r"\.?\s*komnata|"      # "13 komnata" / "13. komnata"
    r"skryt[éeé]" + _SEP + r"skvosty|"     # "Skryté skvosty"
    r"toulav[áaá]" + _SEP + r"kamera|"     # "Toulavá kamera"
    r"kr[áa]sn[éeé]" + _SEP + r"[Čč]esko|" # "Krásné Česko"
    r"reportér[ři]" + _SEP + r"[Čč][Tt]|"  # "Reportéři ČT"
    r"168" + _SEP + r"hodin|"              # "168 hodin"
    r"udalosti" + _SEP + r"v" + _SEP + r"regionech|"  # ČT regionalni zprav
    r"branky" + _SEP + r"body" + _SEP + r"vteriny"    # ČT sport
    r")",
    re.IGNORECASE,
)

# Roman numeral (rocnik/serie) + episode number pattern - oddele tecka/space/_.
# Priklady: "Skryté skvosty III 05", "Babovrešky II 12", "Slunce II 03"
# Bezpecnost: pred romanskou cislici musi byt write (slovo) ne digit, aby
# se nematchovalo treba "2014" (X by se mohlo brat z "2014X").
_ROMAN_EPISODE_RE = re.compile(
    r"(?:^|[\s._\-])"
    r"(?:I{1,3}|IV|V|VI{0,3}|IX|X{1,3})"
    r"[\s._\-]+\d{1,2}"
    r"(?:[\s._\-]|$)"
)


def _is_series_file(name: str) -> bool:
    """
    True pokud filename vypada jako epizoda seriálu / dokumentu.
    v0.0.60: rozsireno o ceske TV dokumentarni patterny + Roman numeral
    epizody (Skryté skvosty III 05).
    """
    if not name:
        return False
    # 1) Klasicky SxxEyy / 1x02
    if _SERIES_PATTERN.search(name):
        return True
    # 2) Cesky TV dokument (ČT, doku cyklus, 13. komnata, ...)
    if _CZECH_TV_DOC_RE.search(name):
        return True
    # 3) Roman numeral + episode number ("III 05", "II 12")
    #    Bezpecnostni: vyzaduji aby tam byl 4+ pismenny "title" pred nim
    #    (jinak by se "X 12" v ramci scene rip noisi matchovalo). Take
    #    vyzaduji 4+ znaky abychom nematchovali kratke filmy jako "I 7"
    #    (Sammo Hung "Iron Monkey VII") nebo "II 5".
    rm = _ROMAN_EPISODE_RE.search(name)
    if rm:
        before = name[:rm.start()].strip()
        if re.search(r"[A-Za-z\u00c0-\u017f]{4,}", before):
            return True
    return False


def _exclude_series(files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Z listu Webshare souborů odstraní vše, co vypadá jako epizoda seriálu."""
    return [f for f in files if not _is_series_file(f.get("name") or "")]


# ---- Detekce češtiny -------------------------------------------------------
#
# _detect_dubbed (výše) hledá explicitní markery jako "CZ", "dabing".
# České originály (zejména pohádky jako "Tři oříšky pro Popelku") tyto
# markery NEMAJÍ – jsou rovnou česky. Pro pohádkový filtr potřebujeme
# širší heuristiku.

_CZ_DIACRITICS_RE = re.compile(r"[ěščřžýáíéůúďťňĚŠČŘŽÝÁÍÉŮÚĎŤŇ]")

_CZ_WORDS_RE = re.compile(
    r"\b(?:"
    r"poh[aá]dka|princezna|princ|kr[aá]l|kr[aá]lovna|"
    r"[čc]ert|vodn[ií]k|ka[šs]p[aá]rek|popelka|honza|"
    r"broučci|broucci|maková|makova|"
    r"o[řr][íi][šs]ky|sůl|sul|pyšn[aá]|pysn[aá]|"
    r"česk[ýy]|ceskoslovensk[ýy]|"
    r"ledov[éeé][_\s]*kr[aá]lovstv[ií]"
    r")\b",
    re.IGNORECASE,
)


def _is_czech_content(name: str) -> bool:
    """
    Heuristika: je tohle český obsah?
    True pokud:
      - název obsahuje českou diakritiku, NEBO
      - obsahuje typické české slovo (pohádka, princezna, čert...), NEBO
      - obsahuje explicitní CZ/SK/dabing marker (přes _detect_dubbed).
    """
    if not name:
        return False
    if _CZ_DIACRITICS_RE.search(name):
        return True
    if _CZ_WORDS_RE.search(name):
        return True
    return _detect_dubbed(name)


def _quality_label(name: str) -> str:
    """Lidsky čitelný štítek kvality (např. '1080p BluRay'). 'SD' pokud nic."""
    if not name:
        return ""
    parts: List[str] = []
    for token in ("2160p", "4K", "UHD", "1080p", "720p", "480p"):
        if re.search(rf"\b{re.escape(token)}\b", name, re.I):
            parts.append(token)
            break
    for token in ("BluRay", "WEB-DL", "WEBRip", "HDRip", "DVDRip", "HDTV"):
        if re.search(rf"\b{re.escape(token)}\b", name, re.I):
            parts.append(token)
            break
    return " ".join(parts) if parts else "SD"


def _quality_score(name: str) -> int:
    """
    Kombinované skóre video * 10 + audio (vyšší = lepší).
    Pro řazení variant v quality pickeru.

    Video je dominantní (váha 10x), audio jen jako tiebreaker. Příklady:
        4K(100) * 10 + Atmos(60)  = 1060   <- nejlepší
        4K(100) * 10 + stereo(0)  = 1000   <- pořád lepší než
        1080p(80) * 10 + Atmos(60) = 860
        1080p(80) * 10 + stereo(0) = 800

    Tj. uvnitř stejného rozlišení vyhraje Atmos/DTS:X. Mezi rozlišeními
    rozhoduje video (lidé chtějí spíš 4K než 1080p Atmos).
    """
    if not name:
        return 0
    video_score = 0
    for token, val in _QUALITY_TIERS:
        if re.search(rf"\b{re.escape(token)}\b", name, re.I):
            video_score = max(video_score, val)
    audio_score = _audio_score(name)
    return video_score * 10 + audio_score


def _audio_score(name: str) -> int:
    """Numerické skóre zvukové kvality (vyšší = lepší)."""
    if not name:
        return 0
    score = 0
    for _label, pattern, val in _AUDIO_TIERS:
        if re.search(pattern, name, re.I):
            score = max(score, val)
    return score


def detect_audio(name: str) -> str:
    """
    Vrátí nejlepší audio token nalezený v názvu (např. 'Atmos', 'DTS:X',
    'TrueHD', 'DTS-HD MA', '5.1', 'DD+', ...).
    Prázdný string pokud nic.
    """
    if not name:
        return ""
    best_val = -1
    best_label = ""
    for label, pattern, val in _AUDIO_TIERS:
        if re.search(pattern, name, re.I):
            if val > best_val:
                best_val = val
                best_label = label
    if best_label:
        return best_label
    return _detect_channel_layout(name)


def _detect_channel_layout(name: str) -> str:
    """
    Nch / 5.1 / 7.1 z nazvu - bez \\b za 'ch' (6CH_x264 jinak neprojde).
    """
    if not name:
        return ""
    m = re.search(r"(?:^|[\W_.-])(\d+)ch(?:[\W_.-]|$)", name, re.I)
    if m:
        ch = int(m.group(1))
        if ch >= 8:
            return "7.1"
        if ch >= 6:
            return "5.1"
        if ch >= 2:
            return "2.0"
    if re.search(r"[\W_.-]7\.1(?:[\W_.-]|$)", name, re.I):
        return "7.1"
    if re.search(r"[\W_.-]5\.1(?:[\W_.-]|$)", name, re.I):
        return "5.1"
    if re.search(r"[\W_.-]2\.0(?:[\W_.-]|$)", name, re.I):
        return "2.0"
    return ""


def detect_audio_for_picker(name: str) -> str:
    """
    Audio pro quality picker - sirsi nez detect_audio (6CH, CZ.5.1, stereo).
    """
    audio = detect_audio(name)
    if audio:
        return audio
    if not name:
        return ""
    if _CZ_AUDIO_TRACK_RE.search(name):
        return "5.1"
    if re.search(
        r"[\W_](?:en|cz|sk|spa|pt|fr|de)[\W_]+7\.1",
        name,
        re.I,
    ):
        return "7.1"
    if re.search(
        r"[\W_](?:en|cz|sk|spa|pt|fr|de)[\W_]+5\.1",
        name,
        re.I,
    ):
        return "5.1"
    if re.search(
        r"[\W_](?:en|cz|sk)[\W_]+2\.0",
        name,
        re.I,
    ):
        return "2.0"
    if re.search(r"[\W_.-]ac3(?:[\W_.-]|$)", name, re.I):
        return "DD 5.1"
    if re.search(r"[\W_.-]ddp(?:[\W_.-]|$)", name, re.I):
        return "DD+"
    return ""


# ---------------------------------------------------------------------------
# Detektor kodeku / HDR / Dolby Vision / IMAX / Extended
# ---------------------------------------------------------------------------
_CODEC_TOKENS = [
    ("HEVC",  r"\b(?:x265|hevc|h\.?265)\b"),
    ("AV1",   r"\bav1\b"),
    ("H264",  r"\b(?:x264|h\.?264)\b"),
]
_HDR_TOKENS = [
    ("DV",    r"\b(?:dolby[\s._-]?vision|dovi|\.dv\b)\b"),
    ("HDR10", r"\bhdr10\+?\b"),
    ("HDR",   r"\bhdr\b"),
]
_EXTRA_TOKENS = [
    ("IMAX",     r"\bimax\b"),
    ("EXTENDED", r"\b(?:extended|ext\.cut|director\.?s?\.?cut|dc)\b"),
    ("REMUX",    r"\bremux\b"),
]


def detect_quality_resolution(name: str) -> str:
    """Vrátí jen rozlišení jako 4K/1080p/720p/SD."""
    if not name:
        return ""
    if re.search(r"\b(?:2160p|4k|uhd)\b", name, re.I):
        return "4K"
    if re.search(r"\b1080p\b", name, re.I):
        return "1080p"
    if re.search(r"\b720p\b", name, re.I):
        return "720p"
    if re.search(r"\b480p\b", name, re.I):
        return "480p"
    return ""


def detect_badges(name: str) -> List[str]:
    """
    Vrátí seznam štítků pro daný název souboru:
    např. ['1080p', 'HEVC', 'HDR', 'Atmos'].

    Použité v UI labelu vedle titulu - dává uživateli okamžitý přehled.
    """
    if not name:
        return []
    out: List[str] = []

    res = detect_quality_resolution(name)
    if res:
        out.append(res)

    for label, pattern in _CODEC_TOKENS:
        if re.search(pattern, name, re.I):
            out.append(label)
            break

    for label, pattern in _HDR_TOKENS:
        if re.search(pattern, name, re.I):
            out.append(label)
            break

    # Audio: nejlepší dostupný (Atmos / DTS:X / TrueHD / 5.1 ...)
    audio = detect_audio_for_picker(name)
    if audio:
        out.append(audio)

    for label, pattern in _EXTRA_TOKENS:
        if re.search(pattern, name, re.I):
            out.append(label)

    return out


def _format_size(size_bytes: Any) -> str:
    """Lidsky čitelná velikost: '1.42 GB', '350 MB' apod."""
    try:
        b = int(size_bytes)
    except (TypeError, ValueError):
        return ""
    if b <= 0:
        return ""
    if b < 1024 ** 2:
        return f"{b/1024:.0f} KB"
    if b < 1024 ** 3:
        return f"{b/1024**2:.0f} MB"
    return f"{b/1024**3:.2f} GB"


def _norm_compare(s: str) -> str:
    """Normalizace pro porovnání titulů (lowercase, jediná mezera)."""
    if not s:
        return ""
    return re.sub(r"\s+", " ", s.lower()).strip()


# v0.0.78: Tokenized exact title match pro quality picker.
# Drive se pouzivalo `target in cand_norm or cand_norm in target`, coz
# pro kratke nazvy jako "Michael" matchlo i "Michael Jordan" / "George
# Michael" -> picker pak ukazoval cizi filmy. Ted porovname SADU slov
# (ignorujeme rok 1900-2099 a quality tagy).
_TITLE_YEAR_TOKEN_RE = re.compile(r"^(?:19|20)\d{2}$")
_TITLE_QUALITY_TOKEN_RE = re.compile(
    r"^(?:"
    r"480p|576p|720p|1080p|1440p|2160p|4k|"
    r"hd|fhd|uhd|sd|"
    r"bluray|brrip|bdrip|webrip|web|webdl|hdtv|dvdrip|camrip|cam|"
    r"x264|x265|h264|h265|hevc|avc|"
    r"aac|ac3|dts|atmos|truehd|"
    r"cz|sk|en|eng|cze|slo|"
    r"dab|dabing|tit|titulky|sub|subs|"
    r"5\.1|7\.1"
    r")$"
)


def _title_meaningful_tokens(s: str) -> set:
    """
    Rozdeli nazev na slova (lowercase), vyhodi rok a quality tagy.
    Vraci set 'vyznamnych' tokenu pro porovnani identity filmu.

    Priklady (lowercase):
      "Michael 2026"            -> {"michael"}
      "Michael 2026 BluRay 1080p CZ" -> {"michael"}
      "Michael Jordan"           -> {"michael", "jordan"}
      "George Michael Live"      -> {"george", "michael", "live"}
    """
    if not s:
        return set()
    tokens = re.findall(r"\w+", s.lower())
    out: set = set()
    for t in tokens:
        if _TITLE_YEAR_TOKEN_RE.match(t):
            continue
        if _TITLE_QUALITY_TOKEN_RE.match(t):
            continue
        out.add(t)
    return out


def _title_tokens_match(target: str, candidate: str) -> bool:
    """
    True pokud target a candidate jsou totozne filmy (po tokenizaci,
    ignorovani roku a quality tagu).

    Pouziva se v get_quality_variants pro filtrovani re-search vysledku,
    aby picker pro "Michael" neukazoval "Michael Jordan" / "George Michael".
    """
    t = _title_meaningful_tokens(target)
    c = _title_meaningful_tokens(candidate)
    if not t or not c:
        return False
    return t == c


def _series_title_match(requested: str, detected: str) -> bool:
    """
    Striktni shoda nazvu serialu - bez substring match (v0.0.78 u filmu).

    Drive ``target in det_norm`` v _collect_episodes_files matchovalo treba
    Zaklinace i pro jiny serial (popularni WS vysledky + kratke dotazy).
    """
    if not requested or not detected:
        return False

    def _variants(s: str) -> List[str]:
        n = _norm_compare(s)
        folded = _norm_compare(_ct.ascii_fold(s))
        out: List[str] = []
        for v in (n, folded):
            if v and v not in out:
                out.append(v)
            no_the = re.sub(r"^the\s+", "", v).strip()
            if no_the and no_the not in out:
                out.append(no_the)
        return out

    req_vars = _variants(requested)
    det_vars = _variants(detected)
    for rv in req_vars:
        for dv in det_vars:
            if rv == dv:
                return True
    if _title_tokens_match(requested, detected):
        return True
    rf = _ct.ascii_fold(requested)
    df = _ct.ascii_fold(detected)
    if rf and df and _title_tokens_match(rf, df):
        return True
    return False


_EPISODE_EXTRA_TOKENS = frozenset({
    "sk", "cz", "slovensko", "slovakia", "cesko", "ceska", "czech", "slovak",
    "markiza", "voyo", "tv", "skcz", "czsk", "hd",
})


def _series_title_match_for_episodes(requested: str, detected: str) -> bool:
    """Shoda pro epizody — Survivor SK tagy, ale ne spin-off (Bachelor in Paradise)."""
    if _series_title_match(requested, detected):
        return True
    rt = _title_meaningful_tokens(requested)
    dt = _title_meaningful_tokens(detected)
    if not rt or not dt:
        return False
    # Voyo katalog (kratky nazev) + WS tag (Survivor -> Survivor Slovensko)
    if rt.issubset(dt):
        extra = dt - rt
        if not extra:
            return True
        if len(extra) <= 2 and all(t in _EPISODE_EXTRA_TOKENS for t in extra):
            return True
    # Preklep / diakritika (Ruza vs Ruža) — jen vysoka podobnost
    try:
        from .title_match import title_similarity
        if title_similarity(requested, detected) >= 0.88:
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _variant_lang_tag(name: str) -> str:
    """Jazykovy tag pro picker - klasicky format jako drive."""
    if _detect_dubbed(name):
        if re.search(r"\bSK\b|slovensk\w*|\bdab\s+SK\b", name, re.IGNORECASE):
            return "SK dab"
        return "CZ dab"
    if _detect_subtitles(name):
        return "CZ tit"
    return "EN"


def _variant_short_hint(name: str) -> str:
    """Kratky rozlisovac (CAM, HEVC, 10bit) - bez celeho WS nazvu."""
    if not name:
        return ""
    hints: List[str] = []
    for pattern, label in (
        (r"\bhdcam\b", "HDCAM"),
        (r"\bhdts\b", "HDTS"),
        (r"\bcamrip\b|\bcam\b", "CAM"),
        (r"\b10.?bit\b", "10bit"),
        (r"\bhevc\b|\bx265\b", "HEVC"),
        (r"\bh\.?264\b|\bx264\b", "H264"),
        (r"\bdv\b", "DV"),
        (r"\bhdr\b", "HDR"),
    ):
        if re.search(pattern, name, re.I) and label not in hints:
            hints.append(label)
        if len(hints) >= 2:
            break
    return "+".join(hints)


def format_variant_label_compact(f: Dict[str, Any]) -> str:
    """
    Kratky popisek pro Kodi Dialog.select - puvodni [zavorky] styl,
    BEZ celeho WS nazvu (ten rolovat nechceme).

    Priklad: '[1080p WEBRip] [5.1] [CZ dab] 4.2 GB'
    """
    name = f.get("name") or ""
    quality = _quality_label(name) or "SD"
    audio = detect_audio_for_picker(name)
    size = _format_size(f.get("size"))
    lang = _variant_lang_tag(name)

    parts: List[str] = [f"[{quality}]"]
    if audio:
        parts.append(f"[{audio}]")
    parts.append(f"[{lang}]")
    if size:
        parts.append(size)

    line = "  ".join(parts)
    if len(line) > 62:
        short_q = (quality.split() or ["SD"])[0]
        parts = [f"[{short_q}]"]
        if audio:
            parts.append(f"[{audio}]")
        parts.append(f"[{lang}]")
        line = "  ".join(parts)
    return line


def build_variant_picker_labels(variants: List[Dict[str, Any]]) -> List[str]:
    """
    Popisky pro quality picker - citelne zavorky, kratke, bez rolovani.
    """
    labels: List[str] = []
    seen: Dict[str, int] = {}
    for v in variants:
        core = format_variant_label_compact(v)
        count = seen.get(core, 0)
        seen[core] = count + 1
        if count:
            name = v.get("name") or ""
            hint = _variant_short_hint(name)
            core = f"{core}  [{hint or f'#{count + 1}'}]"
        labels.append(core)
    return labels


def format_variant_label(f: Dict[str, Any]) -> str:
    """
    Popisek pro položku v select dialogu (Vyber kvalitu...).

    Příklad (v0.0.51): '[1080p BluRay] [Atmos] [CZ dab] 2.10 GB  •  Inception'

    Audio se zobrazuje pouze pokud je nadprůměrné (Atmos / DTS:X / TrueHD /
    DTS-HD / DTS / DD+ / 5.1 / 7.1) - prosté stereo nevypisujeme.

    Jazyk audio stopy:
        [CZ dab]   - cesky dabing
        [SK dab]   - slovensky dabing
        [CZ tit]   - puvodne titulky
        [EN]       - bez CZ/SK dabingu i titulku (asi puvodni)
    """
    name = f.get("name") or ""
    quality = _quality_label(name) or "?"
    audio = detect_audio_for_picker(name)
    size = _format_size(f.get("size"))
    raw = _strip_extension(name)
    if len(raw) > 60:
        raw = raw[:57] + "..."

    lang = _variant_lang_tag(name)

    parts = [f"[{quality}]"]
    if audio:
        parts.append(f"[{audio}]")
    parts.append(f"[{lang}]")
    if size:
        parts.append(size)
    parts.append(f"• {raw}")
    return "  ".join(parts)


def _strip_extension(name: str) -> str:
    if not name:
        return ""
    if "." in name and len(name) - name.rfind(".") <= 5:
        return name.rsplit(".", 1)[0]
    return name


def _guess_year(name: str) -> Optional[int]:
    """Najde rok 19xx/20xx v názvu (přes centrální clean_title)."""
    return _ct.extract_year(name)


def file_to_video_item(f: Dict[str, Any]) -> Dict[str, Any]:
    """
    Z Webshare souboru vytvoří standardizovaný dict pro UI:

        {
          "id":     ident,
          "title":  název bez přípony,
          "year":   odhad roku (nebo None),
          "plot":   "" (placeholder),
          "poster": None (TODO: případně TMDB lookup),
          "type":   "movie" / "series",
          "dubbed": True / False,
        }
    """
    name = f.get("name") or ""
    ident = f.get("ident") or f.get("id") or ""

    # title = vyčištěný titul (pro UI fallback, když TMDB nedoběhne)
    # title_raw = původní filename (pro debug)
    title_clean = _ct.clean_title(name) or _strip_extension(name)

    return {
        "id":        str(ident),
        "title":     title_clean,
        "title_raw": name,
        "year":      _guess_year(name),
        "plot":      "",
        "poster":    f.get("img") or None,
        "type":      f.get("type") or "movie",
        "dubbed":    bool(f.get("dubbed", False)),
    }


# ---------------------------------------------------------------------------
# 5) WEBSHARE SEARCH – jádro pro veřejné rubriky FILMY/SERIÁLY/NOVINKY
# ---------------------------------------------------------------------------
#
# DŮLEŽITÉ:
# Webshare API NEMÁ "veřejný katalog filmů". Endpoint /api/file_list/
# vrací JEN soubory přihlášeného uživatele – pokud nemáš nic ve své knihovně,
# vrátí prázdný seznam. Proto rubriky Filmy/Seriály/Novinky používají
# /api/search/ se strategickými dotazy (vrací miliony veřejných souborů).
#
# Klíčové slovo pro každou rubriku je nastavitelné v settings.xml:
#   q_movies   (default "1080p CZ")
#   q_series   (default "S01E01 CZ")
#   q_latest   (default "1080p")

DEFAULT_QUERIES = {
    "movies":         "1080p CZ",
    "movies_new_dub": "CZ dabing",
    "series":         "S01E01 CZ",
    "series_new_dub": "S01 CZ dabing",
    # Pro Pohádky se nepoužívá jeden dotaz, ale DEFAULT_KIDS_QUERIES
    # (cykluje přes víc CZ klíčových slov pro hodně stránek).
    "kids":           "",
    "latest":         "1080p",
}

# Pro Pohádky: každý dotaz = ~2-3 Webshare stránky (~50-100 unikátních pohádek).
# Celkem tedy 10 dotazů * 3 = ~30 user-pages, tj. stovky pohádek.
DEFAULT_KIDS_QUERIES = [
    "pohádka",
    "pohadka",
    "princezna",
    "princ pohádka",
    "čert pohádka",
    "popelka",
    "honza pohádka",
    "broučci",
    "ledové království",
    "disney CZ",
    "pixar CZ",
    "tři oříšky",
    "kašpárek",
    "vodník",
]

# Kolik Webshare-stránek načteme pro každý dotaz, než přepneme na další.
KIDS_PAGES_PER_QUERY = 3

# Webshare search sortovací klíče (oficiální):
#   rating   - top hodnocené
#   largest  - největší soubory
#   recent   - nejnovější uploady
SORT_MAP = {
    "popular": "rating",
    "rating":  "rating",
    "largest": "largest",
    "latest":  "recent",
    "recent":  "recent",
}


def _addon_query(setting_id: str, default: str) -> str:
    """Načte z addon settings hodnotu pro klíčové slovo, jinak default."""
    addon = _addon_safe()
    if addon is None:
        return default
    val = (addon.getSetting(setting_id) or "").strip()
    return val or default


def search_videos(
    query: str,
    sort: str = "rating",
    page: int = 1,
    _retry: bool = True,
) -> List[Dict[str, Any]]:
    """
    Vlastní wrapper okolo Webshare /api/search/ – vrací raw soubory (list dictů).
    Volá se jak z get_movies/series/latest, tak ze search().
    """
    if not query:
        log.info("search_videos(): prázdný query.")
        return []

    token = get_token()
    if not token:
        log.warning("search_videos(): nemám platný token.")
        return []

    page = max(1, int(page or 1))
    offset = (page - 1) * PAGE_LIMIT

    resp = _request(
        "POST",
        f"{WEBSHARE_API_BASE}/search/",
        data={
            "what": query,
            "category": "video",
            "sort": SORT_MAP.get(sort, "rating"),
            "limit": PAGE_LIMIT,
            "offset": offset,
            "wst": token,
        },
    )
    if resp is None or resp.status_code != 200:
        log.error("search_videos(): HTTP chyba (status=%s).",
                  getattr(resp, "status_code", "?"))
        return []

    root = _parse_xml(resp.text)
    if not _check_status(root, "search_videos"):
        if _retry:
            _invalidate_token()
            new_token = get_token(force_refresh=True)
            if new_token and new_token != token:
                return search_videos(query, sort=sort, page=page, _retry=False)
        return []

    files = _files_from_xml(root)
    log.info("search_videos(q=%r, sort=%s, page=%s) -> %d souborů",
             query, sort, page, len(files))
    return files


# ---------------------------------------------------------------------------
# 5a) GROUPING – seskupení Webshare výsledků podle titulu / seriálu
# ---------------------------------------------------------------------------

def _group_by_title(files: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Pro filmy: seskup podle _normalize_title."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for f in files:
        key = _normalize_title(f.get("name") or "")
        if not key:
            continue
        groups.setdefault(key, []).append(f)
    return groups


def _group_by_series(files: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Pro seriály: seskup podle _series_name (jméno seriálu)."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for f in files:
        sname = _series_name(f.get("name") or "")
        if not sname:
            continue
        groups.setdefault(sname, []).append(f)
    return groups


def _enrich_skip_enabled() -> bool:
    """Nastavení 'enrich_skip' - vypne všechen enrich pro rychlé procházení."""
    addon = _addon_safe()
    if addon is None:
        return False
    return (addon.getSetting("enrich_skip") or "false").lower() in ("true", "1")


def _needs_csfd_fallback(it: Dict[str, Any], tmdb_works: bool) -> bool:
    """v0.0.81: CSFD scrape je pomaly (1-3s/item). Volame jen kdyz
    TMDB neposkytl plakat - ne pro doplneni ratingu (v0.0.114 rychlost)."""
    if not tmdb_works:
        return True
    return not _item_has_display_poster(it)


# v0.0.112: cross-rubric snapshot - po enrichu ulozime vysledek, aby dalsi
# rubrika (Filmy -> Novinky) nemusela cekat na paralelni enrich znovu.
_ENRICH_SNAP_TTL = 30 * 86400
_ENRICH_SNAP_FIELDS = (
    "tmdb_id", "title_localized", "original_title", "year", "plot",
    "poster", "fanart", "rating", "votes", "popularity",
    "genre_ids", "genre_names",
    "csfd_id", "csfd_url", "csfd_rating", "csfd_rating_pct", "csfd_poster",
)


def _enrich_snap_key(it: Dict[str, Any], kind: str) -> str:
    from . import clean_title as _ct
    raw = (it.get("base_title") or it.get("title") or "").strip()
    if not raw:
        return ""
    year = it.get("year") or ""
    clean = _ct.clean_title(raw) or raw
    return f"enrich:snap:v2:{kind}:{clean.lower()}:{year or ''}"


def _enrich_snap_key_variants(it: Dict[str, Any], kind: str) -> List[str]:
    """Snapshot klice - s rokem i bez (rubriky mohou mit jiny year v itemu)."""
    keys: List[str] = []
    primary = _enrich_snap_key(it, kind)
    if primary:
        keys.append(primary)
    if it.get("year"):
        alt = dict(it)
        alt["year"] = ""
        k2 = _enrich_snap_key(alt, kind)
        if k2 and k2 not in keys:
            keys.append(k2)
    return keys


def _apply_enrich_snap(it: Dict[str, Any], snap: Dict[str, Any]) -> None:
    """Aplikuje snapshot; TMDB/CSFD plakat ma prednost pred WS thumb."""
    for field in _ENRICH_SNAP_FIELDS:
        val = snap.get(field)
        if val is None or val == "" or val == []:
            continue
        if field in ("poster", "fanart", "csfd_poster"):
            cur = (it.get(field) or "").strip()
            new = str(val).strip()
            if not new:
                continue
            if not cur or _is_quality_poster_url(new) or not _is_quality_poster_url(cur):
                it[field] = new
            continue
        if not it.get(field):
            it[field] = val
    _promote_csfd_poster(it)


def _is_quality_poster_url(url: str) -> bool:
    """TMDB / CSFD plakat - ne Webshare thumb."""
    if not url:
        return False
    u = url.lower()
    if "image.tmdb.org" in u:
        return True
    if "csfd" in u and u.startswith(("http://", "https://")):
        return True
    return False


def _promote_csfd_poster(it: Dict[str, Any]) -> None:
    """Kdyz TMDB poster chybi, pouzij csfd_poster jako poster pro UI."""
    if _is_quality_poster_url((it.get("poster") or "").strip()):
        return
    cp = (it.get("csfd_poster") or "").strip()
    if _is_quality_poster_url(cp):
        it["poster"] = cp


def _item_has_display_poster(it: Dict[str, Any]) -> bool:
    """Skutecny TMDB/CSFD plakat - WS thumb nepoctame (jinak se preskoci enrich)."""
    for field in ("poster", "csfd_poster"):
        if _is_quality_poster_url((it.get(field) or "").strip()):
            return True
    return False


def _prefill_enrich_snap(it: Dict[str, Any], kind: str) -> bool:
    for key in _enrich_snap_key_variants(it, kind):
        snap = cache.cache_get(key, ttl=_ENRICH_SNAP_TTL)
        if snap and isinstance(snap, dict):
            _apply_enrich_snap(it, snap)
    return _item_has_display_poster(it)


def _save_enrich_snap(it: Dict[str, Any], kind: str) -> None:
    if not _item_has_display_poster(it):
        return
    key = _enrich_snap_key(it, kind)
    if not key:
        return
    snap = {field: it.get(field) for field in _ENRICH_SNAP_FIELDS if it.get(field)}
    if snap:
        try:
            cache.cache_set(key, snap)
        except Exception as exc:  # noqa: BLE001
            log.debug("save_enrich_snap(%r) selhalo: %s", key, exc)
    try:
        from . import metadata_cache as _mc
        title = (
            (it.get("title_localized") or it.get("title") or it.get("base_title") or "")
            .strip()
        )
        if title:
            _mc.save(title, {
                "year": it.get("year") or "",
                "rating": it.get("rating") or it.get("csfd_rating") or "",
                "plot": it.get("plot") or "",
                "poster": it.get("poster"),
                "fanart": it.get("fanart"),
                "source": "tmdb" if it.get("tmdb_id") else (
                    "csfd" if it.get("csfd_id") else "none"
                ),
                "tmdb_id": it.get("tmdb_id"),
            })
    except Exception as exc:  # noqa: BLE001
        log.debug("metadata_cache.save z enrich_snap selhalo: %s", exc)


def invalidate_title_metadata(
    title: str,
    year: Optional[int] = None,
    kind: str = "movie",
) -> int:
    """Smaže cache metadat pro jeden titul (snapshot, TMDB, CSFD, metadata JSON)."""
    from . import clean_title as _ct
    from .title_match import apply_typo_fixes, title_search_variants

    raw = (title or "").strip()
    if not raw:
        return 0
    clean = (_ct.clean_title(raw) or raw).strip()
    n = 0
    titles_to_clear = set(title_search_variants(raw, year))
    titles_to_clear.add(raw)
    titles_to_clear.add(clean)
    titles_to_clear.add(apply_typo_fixes(clean))
    fake: Dict[str, Any] = {"title": raw, "base_title": raw, "year": year}
    for key in _enrich_snap_key_variants(fake, kind):
        if cache.cache_delete(key):
            n += 1

    low = clean.lower()
    year_vals: List[Any] = [""]
    if year is not None:
        year_vals.append(year)
    if kind == "series":
        if cache.cache_delete(f"tmdb:tv:v3:{low}"):
            n += 1
        if cache.cache_delete(f"csfd:v6:tv:{low}:"):
            n += 1
    else:
        for t in titles_to_clear:
            low = (_ct.clean_title(t) or t).strip().lower()
            if not low:
                continue
            for yv in year_vals:
                for ver in ("v8", "v9"):
                    tk = f"tmdb:movie:{ver}:{low}:{yv or ''}"
                    if cache.cache_delete(tk):
                        n += 1
                ck = f"csfd:v6:film:{low}:{yv or ''}"
                if cache.cache_delete(ck):
                    n += 1

    try:
        from . import metadata_cache as _mc
        for t in {raw, clean}:
            if _mc.delete(t):
                n += 1
    except Exception as exc:  # noqa: BLE001
        log.debug("invalidate_title_metadata metadata_cache: %s", exc)
    log.info("invalidate_title_metadata(%r, year=%s, kind=%s): %d klicu",
             clean, year, kind, n)
    return n


def refresh_title_metadata(
    title: str,
    year: Optional[int] = None,
    kind: str = "movie",
) -> Dict[str, Any]:
    """Znovu stáhne TMDB/CSFD metadata pro jeden titul (po smazání cache)."""
    from . import csfd
    from . import tmdb

    raw = (title or "").strip()
    invalidate_title_metadata(raw, year, kind)
    item: Dict[str, Any] = {
        "title": raw,
        "base_title": raw,
        "year": year,
    }
    for field in _ENRICH_SNAP_FIELDS:
        item.pop(field, None)

    tmdb_enabled = tmdb.is_enabled()
    csfd_on = csfd.is_enabled()
    tmdb_works = False
    if tmdb_enabled:
        test = tmdb.self_test()
        tmdb_works = bool(test.get("ok"))

    if tmdb_works:
        if kind == "series":
            tmdb.enrich_series_item(item)
        else:
            tmdb.enrich_movie_item(item)
    if csfd_on and _needs_csfd_fallback(item, tmdb_works):
        if kind == "series":
            csfd.enrich_series_item(item)
        else:
            csfd.enrich_movie_item(item)
        _retry_tmdb_after_csfd(item, kind)
    _auto_heal_item_metadata(item, kind)
    _promote_csfd_poster(item)
    _save_enrich_snap(item, kind)
    return item


def _prefill_enrich_from_cache(items: List[Dict[str, Any]], kind: str,
                               tmdb_enabled: bool, csfd_enabled: bool) -> int:
    """Synchronne doplni metadata z cache (snapshot + TMDB/CSFD raw cache)."""
    from . import tmdb
    from . import csfd

    prefilled = 0
    for it in items:
        had_poster = _item_has_display_poster(it)
        _prefill_enrich_snap(it, kind)
        if tmdb_enabled and not _item_has_display_poster(it):
            if kind == "series":
                tmdb.prefill_series_item_from_cache(it)
            else:
                tmdb.prefill_movie_item_from_cache(it)
        if csfd_enabled and not _item_has_display_poster(it):
            if kind == "series":
                csfd.prefill_series_item_from_cache(it)
            else:
                csfd.prefill_movie_item_from_cache(it)
        _promote_csfd_poster(it)
        if _item_has_display_poster(it) and not had_poster:
            prefilled += 1
    return prefilled


def _warm_item_posters(items: List[Dict[str, Any]]) -> None:
    """Lokalni image cache - stejny plakat URL = bez site pri dalsi rubrice."""
    if not items:
        return
    try:
        from . import image_cache
        image_cache.warm_items_posters(items)
    except Exception as exc:  # noqa: BLE001
        log.debug("warm_item_posters selhalo: %s", exc)


def _retry_tmdb_after_csfd(it: Dict[str, Any], kind: str) -> None:
    """CSFD casto opravi preklep v nazvu -> druhy pokus TMDB s celym titulem."""
    if kind != "movie":
        return
    from . import tmdb
    if not tmdb.is_enabled():
        return
    if it.get("tmdb_id") and _item_has_display_poster(it):
        return
    csfd_title = (it.get("title_localized") or "").strip()
    orig = (it.get("title") or it.get("base_title") or "").strip()
    if not csfd_title or csfd_title.lower() == orig.lower():
        return
    try:
        meta = tmdb.search_movie(csfd_title, it.get("year"))
        if meta:
            tmdb._merge_meta(it, meta, kind="movie")
            _promote_csfd_poster(it)
            log.info("retry_tmdb_after_csfd: %r -> %r (tmdb_id=%s)",
                     orig, csfd_title, it.get("tmdb_id"))
    except Exception as exc:  # noqa: BLE001
        log.debug("retry_tmdb_after_csfd(%r) selhalo: %s", orig, exc)


def _auto_heal_item_metadata(it: Dict[str, Any], kind: str) -> None:
    """v0.0.122: Automaticky zkusit varianty titulu (preklepy, sequel) bez menu."""
    if kind != "movie" or _item_has_display_poster(it):
        return
    from . import csfd
    from . import tmdb
    from .title_match import title_search_compatible, title_search_variants

    title = (it.get("base_title") or it.get("title") or "").strip()
    if not title:
        return
    year = it.get("year")
    variants = title_search_variants(title, year)
    if not variants:
        return

    if tmdb.is_enabled():
        for q in variants:
            if q.lower() == title.lower() and it.get("tmdb_id"):
                continue
            try:
                meta = tmdb.search_movie(q, year)
                if meta:
                    if not title_search_compatible(
                        title, q,
                        meta.get("title") or "",
                        meta.get("original") or "",
                    ):
                        continue
                    tmdb._merge_meta(it, meta, kind="movie")
                    _promote_csfd_poster(it)
                    if _item_has_display_poster(it):
                        log.info("auto_heal: TMDB %r pres %r (id=%s)",
                                 title, q, it.get("tmdb_id"))
                        return
            except Exception as exc:  # noqa: BLE001
                log.debug("auto_heal TMDB(%r) selhalo: %s", q, exc)

    if not csfd.is_enabled() or _item_has_display_poster(it):
        return
    for q in variants:
        try:
            meta = csfd.search_movie(q, year)
            if meta:
                csfd._merge_meta(it, meta)
                _retry_tmdb_after_csfd(it, kind)
                _promote_csfd_poster(it)
                if _item_has_display_poster(it):
                    log.info("auto_heal: CSFD %r pres %r", title, q)
                    return
        except Exception as exc:  # noqa: BLE001
            log.debug("auto_heal CSFD(%r) selhalo: %s", q, exc)


def _enrich_item_complete(it: Dict[str, Any], kind: str,
                          tmdb_works: bool, csfd_on: bool) -> bool:
    # v0.0.115: hotovo = ma skutecny plakat (ne jen tmdb_id bez obrazku)
    if _item_has_display_poster(it):
        return True
    if not tmdb_works and not csfd_on:
        return True
    return False


def _enrich_in_parallel(items: List[Dict[str, Any]], kind: str,
                          skip_csfd: bool = False) -> None:
    """
    Paralelně obohatí items přes TMDB a (jako fallback) přes ČSFD.

    :param kind: "movie" nebo "series"
    :param skip_csfd: v0.0.70 - vynech ČSFD enrich (default: False).
                      Pouziva se v rubrikach kde ma genre/discover filter
                      vetsi prioritu nez rating - typicky "Animovane CZ/SK".
                      ČSFD scraping na cold cache je dominantni bottleneck
                      (1-3s per movie kvuli Cloudflare anti-bot), takze
                      jeho preskoceni zrychli first-load 2-3x.

    Inteligentní volba providerů:
        1) Pokud user vypnul vše ('enrich_skip = true'), vrací hned.
        2) TMDB self_test() rozhodne, jestli vůbec stojí za to TMDB volat.
        3) Pokud TMDB funguje  -> paralelni TMDB, CSFD jen fallback.
        4) Pokud TMDB nefunguje -> 2 vlakna, CSFD-only (anti-bot).
        5) v0.0.81: casovy budget ENRICH_MAX_WAIT_SEC - seznam se
           zobrazi i kdyz enrich nedobehl pro vsechny polozky.
    """
    if not items:
        return

    if _enrich_skip_enabled():
        log.info("enrich: SKIPPED (enrich_skip=true v settings)")
        return

    from . import tmdb
    from . import csfd

    csfd_on = csfd.is_enabled() and not skip_csfd
    tmdb_enabled = tmdb.is_enabled()

    # v0.0.114: nejdriv cache (bez site), pak az network enrich pro zbytek
    n_prefill = _prefill_enrich_from_cache(
        items, kind, tmdb_enabled, csfd_on)
    for it in items:
        if _item_has_display_poster(it):
            _save_enrich_snap(it, kind)
    pending_items = [
        it for it in items
        if not _enrich_item_complete(it, kind, tmdb_enabled, csfd_on)
    ]
    if n_prefill:
        log.info("enrich: %d/%d polozek z cache (zbyva %d)",
                 n_prefill, len(items), len(pending_items))

    if not pending_items:
        _warm_item_posters(items)
        return

    # TMDB self_test az kdyz opravdu potrebujeme sit (cache nestacila)
    tmdb_test = (tmdb.self_test() if tmdb_enabled
                 else {"ok": False, "reason": "disabled"})
    tmdb_works = bool(tmdb_test.get("ok"))

    if not tmdb_works and not csfd_on:
        log.info("enrich: oba providery vypnuté/rozbité (tmdb=%s csfd=%s)",
                 tmdb_test.get("reason"), csfd_on)
        _warm_item_posters(items)
        return

    # Re-check pending po self_test (tmdb_works se muze lisit od tmdb_enabled)
    pending_items = [
        it for it in items
        if not _enrich_item_complete(it, kind, tmdb_works, csfd_on)
    ]
    if not pending_items:
        _warm_item_posters(items)
        return

    def _enrich_one(it: Dict[str, Any]) -> None:
        # v0.0.79: pri Kodi shutdown ihned vyskoc, nezacinat nove network I/O
        if _shutdown.is_shutting_down():
            return
        try:
            if tmdb_works:
                if kind == "series":
                    tmdb.enrich_series_item(it)
                else:
                    tmdb.enrich_movie_item(it)
            if _shutdown.is_shutting_down():
                return
            # v0.0.81: CSFD jen fallback (ne pro kazdou polozku s TMDB daty).
            if csfd_on and _needs_csfd_fallback(it, tmdb_works):
                if kind == "series":
                    csfd.enrich_series_item(it)
                else:
                    csfd.enrich_movie_item(it)
                _retry_tmdb_after_csfd(it, kind)
            _auto_heal_item_metadata(it, kind)
            _save_enrich_snap(it, kind)
        except Exception as exc:  # noqa: BLE001
            log.debug("enrich item %r selhal: %s", it.get("title"), exc)
        finally:
            _promote_csfd_poster(it)

    # Adaptivní počet workerů.
    if tmdb_works:
        workers = min(ENRICH_WORKERS, max(1, len(pending_items)))  # TMDB unese 8-16
    else:
        workers = min(2, max(1, len(pending_items)))               # CSFD anti-bot: max 2

    log.debug("enrich: %d items (%d pending), %d workers (tmdb_ok=%s, csfd=%s, kind=%s) reason=%s",
              len(items), len(pending_items), workers, tmdb_works, csfd_on, kind,
              tmdb_test.get("reason"))

    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = [pool.submit(_enrich_one, it) for it in pending_items]
        pending = set(futures)
        budget = 1.5 if len(pending_items) <= 5 else float(ENRICH_MAX_WAIT_SEC)
        while pending and budget > 0 and not _shutdown.is_shutting_down():
            _done, pending = wait(pending, timeout=min(0.5, budget))
            budget -= 0.5
        if pending:
            reason = "shutdown" if _shutdown.is_shutting_down() else "budget"
            log.info("enrich: %s - %d/%d polozek nedobehlo",
                     reason, len(pending), len(futures))
            for fut in pending:
                fut.cancel()
    finally:
        # v0.0.136: pri Kodi abortu NEBLOKOVAT join workeru zaseklych v
        # urlopen (drive 'with' cekal wait=True -> pomale vypinani Kodi,
        # hlavne pri enrichu na pozadi/prefetch). Daemon vlakna umrou s
        # interpreterem. Jinak (normal) uklidime bez cekani take - budget
        # uz probehl, zbytek je cancelled.
        _shutdown_pool(pool)

    _warm_item_posters(items)


# Kolik poster-less polozek smi dostat (pomalejsi) CSFD zachranu na 1 WS
# stranku a jak dlouho na ne cekat. Male cislo = ceske tituly co TMDB nezna
# (Imperium, Ignorace) dostanou plakat, ale cas zustava omezeny.
CSFD_RESCUE_MAX_ITEMS = 5
CSFD_RESCUE_BUDGET_SEC = 3.0


def _csfd_rescue_posters(items: List[Dict[str, Any]], kind: str,
                         max_items: int = CSFD_RESCUE_MAX_ITEMS,
                         budget_sec: float = CSFD_RESCUE_BUDGET_SEC) -> None:
    """
    v0.0.130: Cilena CSFD zachrana plakatu PO rychlem TMDB enrichu.

    Bezi jen na hrstce polozek, ktere po TMDB nemaji ZADNY plakat
    (typicky ceske tituly bez TMDB shody). TMDB (rychle) resi vetsinu,
    tohle jen doplni zbytek - levne a s tvrdym budgetem. Vysledek se
    uklada do snap cache, takze pri dalsim otevreni jsou plakaty hned.
    """
    if not items or _shutdown.is_shutting_down():
        return
    try:
        from . import csfd
    except Exception:  # noqa: BLE001
        return
    if not csfd.is_enabled():
        return

    pending = [it for it in items if not _item_has_display_poster(it)][:max_items]
    if not pending:
        return

    def _one(it: Dict[str, Any]) -> None:
        if _shutdown.is_shutting_down():
            return
        try:
            if kind == "series":
                csfd.enrich_series_item(it)
            else:
                csfd.enrich_movie_item(it)
            _retry_tmdb_after_csfd(it, kind)
            _save_enrich_snap(it, kind)
        except Exception as exc:  # noqa: BLE001
            log.debug("csfd rescue %r selhal: %s", it.get("title"), exc)
        finally:
            _promote_csfd_poster(it)

    log.debug("csfd_rescue: %d poster-less polozek (kind=%s)", len(pending), kind)
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        futs = set(pool.submit(_one, it) for it in pending)
        budget = float(budget_sec)
        while futs and budget > 0 and not _shutdown.is_shutting_down():
            _done, futs = wait(futs, timeout=min(0.5, budget))
            budget -= 0.5
        for f in futs:
            f.cancel()
    finally:
        _shutdown_pool(pool)  # v0.0.136: neblokovat vypinani Kodi
    _warm_item_posters(pending)


def _files_to_variant_refs(files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Z Webshare souborů udělá kompaktní list 'variant refs' pro cache."""
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for f in files:
        ident = f.get("ident") or f.get("id") or ""
        if not ident or ident in seen:
            continue
        seen.add(ident)
        out.append({
            "ident": ident,
            "name":  f.get("name") or "",
            "size":  f.get("size") or 0,
            "img":   f.get("img") or "",
        })
    out.sort(key=lambda v: _quality_score(v.get("name") or ""), reverse=True)
    return out


def _variants_cache_key(base_title: str, mode: str = "movie") -> str:
    """Cache key pro varianty: stejný napříč rubrikami (jeden zdroj pravdy).

    v0.0.78: bump klice z 'variants:' na 'variants:v2:' aby se invalidovaly
    starsi cache, ve kterych mohly byt cizi filmy (napr. pro 'Michael'
    mohly byt v cache i 'Michael Jordan' / 'George Michael' kvuli prilis
    lenientnimu tokenized matchi v re-search).
    """
    norm = _norm_compare(base_title)
    return f"variants:v2:{mode}:{norm}"


def _save_variants_cache(base_title: str, mode: str,
                         variants: List[Dict[str, Any]]) -> None:
    """Uloží varianty (ident/name/size) do cache pro pozdější play_pick."""
    if not base_title or not variants:
        return
    try:
        cache.cache_set(_variants_cache_key(base_title, mode), variants)
    except Exception as exc:  # noqa: BLE001
        log.debug("save_variants_cache(%r) selhalo: %s", base_title, exc)


def _load_variants_cache(base_title: str, mode: str = "movie",
                         ttl: int = 24 * 3600) -> List[Dict[str, Any]]:
    """Načte uložené varianty z cache (default TTL 24h)."""
    if not base_title:
        return []
    try:
        data = cache.cache_get(_variants_cache_key(base_title, mode), ttl=ttl)
        return list(data or [])
    except Exception as exc:  # noqa: BLE001
        log.debug("load_variants_cache(%r) selhalo: %s", base_title, exc)
        return []


def _only_with_poster_enabled() -> bool:
    """
    Setting 'only_with_poster' - skryje položky, které nemají TMDB/ČSFD
    poster (typicky neznámé anime ripy, fansuby, podivné názvy).

    Safety: pokud jsou TMDB i ČSFD vypnuté/rozbité, filtr se NEAPLIKUJE
    (jinak by user neviděl nic). Default: FALSE (od v0.0.47) - user chce
    videt VSECHEN obsah, polozky bez TMDB plakatu dostanou v UI hezky
    typovy placeholder (placeholder_movie.png / placeholder_series.png)
    misto skryti.
    """
    addon = _addon_safe()
    if addon is None:
        return False
    raw = (addon.getSetting("only_with_poster") or "false").lower()
    return raw in ("true", "1")


def _filter_with_poster(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Vrátí jen položky, které mají skutečný (TMDB/ČSFD) poster URL.
    Webshare 'img' i fallback addon ikona se ignorují - musí jít o URL
    začínající 'http' / 'https'.
    """
    if not items:
        return items
    if not _only_with_poster_enabled():
        return items

    # Safety net: pokud oba providery selhaly, filtr vypneme,
    # jinak by user neviděl nic.
    try:
        from . import tmdb
        from . import csfd
        tmdb_ok = tmdb.is_enabled() and bool(tmdb.self_test().get("ok"))
        csfd_ok = csfd.is_enabled()
        if not tmdb_ok and not csfd_ok:
            log.info("_filter_with_poster: TMDB i ČSFD nedostupné, filtr přeskočen.")
            return items
    except Exception:  # noqa: BLE001
        return items

    def has_real_poster(it: Dict[str, Any]) -> bool:
        p = (it.get("poster") or "").strip()
        if not p:
            return False
        return p.startswith("http://") or p.startswith("https://")

    out = [it for it in items if has_real_poster(it)]
    log.info("_filter_with_poster: %d -> %d items (only_with_poster=true)",
             len(items), len(out))
    return out


def _filter_with_webshare_files(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """v0.0.82: Zobraz jen polozky s realnymi WS soubory (variant_idents)."""
    if not items:
        return items
    out: List[Dict[str, Any]] = []
    for it in items:
        idents = [i for i in (it.get("variant_idents") or []) if i]
        if not idents:
            log.debug("_filter_with_webshare_files: skip %r (no variant_idents)",
                      it.get("title") or it.get("base_title"))
            continue
        it["variant_idents"] = idents
        it["variants_count"] = len(idents)
        out.append(it)
    if len(out) != len(items):
        log.info("_filter_with_webshare_files: %d -> %d items",
                 len(items), len(out))
    return out


def _movies_from_groups(
    groups: Dict[str, List[Dict[str, Any]]],
    pre_filter=None,
    skip_aggressive_filters: bool = False,
    skip_csfd: bool = False,
    skip_enrich: bool = False,
    skip_poster: bool = False,
    csfd_rescue: bool = False,
) -> List[Dict[str, Any]]:
    """
    Z dict[title -> files] vytvoří video-items pro UI (paralelní enrich).

    :param pre_filter: volitelný callable(items)->items aplikovany PRED
                       TMDB enrichmentem. Pouziva se v rubrikach 4K/BluRay/
                       newdub/latest k filtrovani na zaklade dat ze souboru
                       (quality_score, dubbed, subs_cz) - tim se HUGE setri
                       TMDB requesty (enrich jen na polozkach co projdou).
                       Filtry zalozene na TMDB metadatech (year) zustavaji
                       v post_filter v paginate_with_fetcher.

    :param skip_aggressive_filters: True = neaplikuj content filtry
                       (obfuscated/serial-by-number/polish-only/lecture).
                       Pouziva search() - user explicitne nehleda v rubrice,
                       a tyhle filtry odriznou legitimni pripady (napr. polsky
                       dabing pro polskeho kamarada, lectures pro studium).
    """
    items: List[Dict[str, Any]] = []
    skipped_obf = 0
    skipped_serial = 0
    skipped_pl = 0
    skipped_lect = 0
    for title, fs in groups.items():
        if not skip_aggressive_filters:
            # 1) Skip obfuskované tituly ("H H JNDH"-style nesmyslné názvy).
            if _looks_obfuscated(title):
                skipped_obf += 1
                continue
            # 2) Skip soubory číslované jako díly serie ("GOAT 01").
            if all(_looks_like_serial_episode_by_number(f.get("name") or "") for f in fs):
                skipped_serial += 1
                continue
            # 3) Skip polské-only soubory (lektor PL bez CZ markeru).
            fs = [f for f in fs if not _is_polish_only(f.get("name") or "")]
            if not fs:
                skipped_pl += 1
                continue
            # 4) Skip prednasky / popisne uploady.
            if _looks_like_lecture_or_description(title):
                skipped_lect += 1
                continue

        classify_files(fs)
        variants = _files_to_variant_refs(fs)
        if not variants:
            continue
        best = variants[0]  # nejvyšší kvalita (po _files_to_variant_refs sort)
        is_dubbed = any(_detect_dubbed(v.get("name") or "") for v in variants)
        has_subs = any(_detect_subtitles(v.get("name") or "") for v in variants)

        # Cache varianty pro play_pick - důležité pro dedup napříč rubrikami.
        _save_variants_cache(title, "movie", variants)

        # Badges - z nejlepší varianty (= nejvyšší video+audio score).
        # Pokud nejlepší varianta (#1 dle quality_score) nemá kvalitní audio,
        # ale některá z dalších ano (např. 1080p Atmos vs. 4K stereo),
        # přidáme audio badge té lepší. Uživatel vidí, že lepší zvuk existuje.
        best_audio = ""
        best_audio_score = 0
        for v in variants:
            sc = _audio_score(v.get("name") or "")
            if sc > best_audio_score:
                best_audio_score = sc
                best_audio = detect_audio(v.get("name") or "")

        badges = detect_badges(best.get("name") or "")
        if best_audio and best_audio not in badges:
            badges.append(best_audio)

        # Nejvyšší skóre kvality napříč VŠEMI variantami (ne jen best).
        # Důležité pro min-quality filtr v rubrikách (např. min 1080p).
        max_quality = max((_quality_score(v.get("name") or "") for v in variants),
                          default=0)

        # Nejnovější datum přidání na WS (sort kritérium pro rubriky).
        ws_added = max((v.get("added") or "" for v in variants), default="")

        # Webshare thumbnail jako fallback poster - projedeme VŠECHNY varianty
        # (ne jen best), protože ne každý soubor má img. To zajistí, ze
        # i kdyz TMDB selze, polozka ma aspon WS thumb. Pokud i ten chybi,
        # UI dostane typovy placeholder.
        ws_thumb = ""
        for v in variants:
            img = (v.get("img") or "").strip()
            if img and img.startswith(("http://", "https://")):
                ws_thumb = img
                break

        items.append({
            "id": "",
            "title": title,
            "year": _guess_year(best.get("name") or ""),
            "plot": f"Dostupné varianty: {len(variants)}",
            "poster": None,
            "ws_thumb": ws_thumb or None,
            "fanart": None,
            "type": "movie",
            "dubbed": is_dubbed,
            "subs_cz": has_subs,
            "base_title": title,
            "variant_idents": [v["ident"] for v in variants],
            "variants_count": len(variants),
            "ws_names": " ".join(v.get("name") or "" for v in variants),
            "quality_score": max_quality,
            "best_audio": best_audio,
            "badges": badges,
            "ws_added": ws_added,
            "rating": 0.0,
            "votes": 0,
            "popularity": 0.0,
        })

    if skipped_obf or skipped_serial or skipped_pl or skipped_lect:
        log.debug("_movies_from_groups: preskakuje obf=%d serial=%d pl=%d "
                  "lect=%d (zustalo %d skupin)",
                  skipped_obf, skipped_serial, skipped_pl, skipped_lect, len(items))

    # PRE-ENRICH FILTR: aplikujeme PRED TMDB requesty, abychom enrichovali
    # jen polozky co projdou (quality, dab/sub - vse z nazvu souboru).
    # Setri MIN 80% TMDB requestu v 4K/BluRay/newdub kategoriich.
    if pre_filter is not None and items:
        before = len(items)
        items = pre_filter(items)
        log.debug("_movies_from_groups: pre_filter %d -> %d items", before, len(items))

    if skip_enrich:
        items = _filter_with_webshare_files(items)
        return items

    _enrich_in_parallel(items, kind="movie", skip_csfd=skip_csfd)
    # v0.0.130: kdyz TMDB (rychle) nechalo par polozek bez plakatu, dopln
    # je cilenou CSFD zachranou (ceske tituly co TMDB nezna).
    if csfd_rescue and skip_csfd:
        _csfd_rescue_posters(items, kind="movie")
    items = _dedupe_after_enrich(items, mode="movie")
    if not skip_poster:
        items = _filter_with_poster(items)
    items = _filter_with_webshare_files(items)
    return items


def _series_from_groups(groups: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Z dict[series_name -> files] vytvoří složky pro seriály (paralelní enrich)."""
    items: List[Dict[str, Any]] = []
    for sname in sorted(groups.keys()):
        # Skip obfuskované série ("H H JNDH"-style).
        if _looks_obfuscated(sname):
            continue
        # Skip prednasky / popisne uploady (Pjakin "ze dne ...", komentare).
        if _looks_like_lecture_or_description(sname):
            continue
        fs = groups[sname]
        # Skip polské-only soubory.
        fs = [f for f in fs if not _is_polish_only(f.get("name") or "")]
        if not fs:
            continue
        variants = _files_to_variant_refs(fs)
        if not variants:
            continue
        _save_variants_cache(sname, "series", variants)
        is_dubbed = any(_detect_dubbed(v.get("name") or "") for v in variants)
        has_subs = any(_detect_subtitles(v.get("name") or "") for v in variants)
        # Pro seriály vezmeme nejlepší kvalitu napříč epizodami pro badges
        best_var = max(variants, key=lambda v: _quality_score(v.get("name") or ""))

        # Audio badge - nejlepší napříč všemi epizodami
        best_audio = ""
        best_audio_score = 0
        for v in variants:
            sc = _audio_score(v.get("name") or "")
            if sc > best_audio_score:
                best_audio_score = sc
                best_audio = detect_audio(v.get("name") or "")
        badges = detect_badges(best_var.get("name") or "")
        if best_audio and best_audio not in badges:
            badges.append(best_audio)

        # Webshare thumbnail jako fallback (projdeme všechny epizody).
        ws_thumb = ""
        for v in variants:
            img = (v.get("img") or "").strip()
            if img and img.startswith(("http://", "https://")):
                ws_thumb = img
                break

        items.append({
            "id": "",
            "title": sname,
            "year": None,
            "plot": f"Epizody nalezené v této stránce: {len(variants)}",
            "poster": None,
            "ws_thumb": ws_thumb or None,
            "fanart": None,
            "type": "series",
            "dubbed": is_dubbed,
            "subs_cz": has_subs,
            "series_name": sname,
            "variant_idents": [v["ident"] for v in variants],
            "variants_count": len(variants),
            "quality_score": max((_quality_score(v.get("name") or "") for v in variants), default=0),
            "best_audio": best_audio,
            "badges": badges,
            "rating": 0.0,
            "votes": 0,
            "popularity": 0.0,
        })

    _enrich_in_parallel(items, kind="series")
    items = _dedupe_after_enrich(items, mode="series")
    items = _filter_with_poster(items)
    items = _filter_with_webshare_files(items)
    return items


def _dedupe_after_enrich(items: List[Dict[str, Any]],
                         mode: str = "movie") -> List[Dict[str, Any]]:
    """
    Po TMDB enrich sloučí položky, které ukazují na stejné dílo.

    Sjednocovací klíč (v tomto pořadí):
        1) tmdb_id (nejsilnější)  - např. "Avatar" + "Avatar 2009" + "Avatar.4K"
        2) (lokalizovaný titul + rok) - když oba mají rok
        3) (titul bez roku) - když jeden z nich rok nemá

    Z duplicit ponechá tu s nejvyšší kvalitou + agreguje variant_idents.
    """
    if not items:
        return items

    def _norm(s: str) -> str:
        return _norm_compare(s or "")

    # --- 1. průchod: nejjemnější bucketing ---
    # Priorita klíče:
    #   a) tmdb_id (nejstabilnější)
    #   b) original_title + year (en název ze TMDB - stejný napříč cs/sk variantami)
    #   c) title_localized / title + year (fallback bez TMDB matche)
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    order: List[str] = []

    for it in items:
        tmdb_id = it.get("tmdb_id")
        original = it.get("original_title")
        year = it.get("year") or ""
        if tmdb_id:
            key = f"tmdb:{mode}:{tmdb_id}"
        elif original:
            key = f"orig:{mode}:{_norm(original)}|{year}"
        else:
            ttl = it.get("title_localized") or it.get("title") or it.get("series_name") or ""
            key = f"title:{mode}:{_norm(ttl)}|{year}"
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(it)

    # --- 2. průchod: fuzzy merge mezi 'title|<year>' a 'title|' ---
    # Když Webshare vrátí 'Avatar 2009 CZ' (year=2009) a 'Avatar CZ dab' (year=None),
    # první se uloží jako 'title:movie:avatar|2009', druhý 'title:movie:avatar|'.
    # Sloučit je: položky bez roku přidat do bucketu se stejným title, který rok má.
    # Riziko (dva filmy stejného názvu v jiných letech): nízké v rámci 1 listing.
    title_keys: Dict[str, str] = {}  # title_norm -> canonical key (preferuj ten s rokem)
    for k in order:
        if not k.startswith("title:"):
            continue
        rest = k[len("title:"):]  # "movie:<title>|<year>"
        if "|" not in rest:
            continue
        mode_title, year_part = rest.rsplit("|", 1)
        # mode_title je "movie:<title>" - vezmi jen <title> část
        if ":" in mode_title:
            _, title_only = mode_title.split(":", 1)
        else:
            title_only = mode_title
        if not title_only:
            continue
        # Preferuj key s rokem (delší year_part) jako kanonický
        existing = title_keys.get(title_only)
        if existing is None:
            title_keys[title_only] = k
        else:
            # Vyber ten s rokem
            existing_year = existing.rsplit("|", 1)[-1]
            new_year = year_part
            if new_year and not existing_year:
                title_keys[title_only] = k

    # Nyní přesunout položky z 'title|' buketů do 'title|<year>' buketů
    merged_into_canonical: set = set()
    for k in list(order):
        if not k.startswith("title:"):
            continue
        rest = k[len("title:"):]
        if "|" not in rest:
            continue
        mode_title, _ = rest.rsplit("|", 1)
        if ":" in mode_title:
            _, title_only = mode_title.split(":", 1)
        else:
            title_only = mode_title
        canonical = title_keys.get(title_only)
        if canonical and canonical != k:
            # Přesuň items do kanonického bucketu
            buckets[canonical].extend(buckets[k])
            merged_into_canonical.add(k)

    # Odstraň přesunuté buckety z order
    order = [k for k in order if k not in merged_into_canonical]
    for k in merged_into_canonical:
        buckets.pop(k, None)

    if all(len(buckets[k]) == 1 for k in order):
        return [buckets[k][0] for k in order]  # nic ke slučování

    merged: List[Dict[str, Any]] = []
    for key in order:
        group = buckets[key]
        if len(group) == 1:
            merged.append(group[0])
            continue

        # Vyber kanonickou položku: max popularity → quality → variants_count.
        def _score(it):
            return (
                float(it.get("popularity") or 0),
                int(it.get("quality_score") or 0),
                int(it.get("variants_count") or 0),
            )
        canonical = max(group, key=_score)

        # Agregace variant_idents (zachováme order = quality).
        seen_idents: set = set()
        all_variants: List[str] = []
        all_variant_refs: List[Dict[str, Any]] = []

        for it in group:
            for ident in (it.get("variant_idents") or []):
                if ident and ident not in seen_idents:
                    seen_idents.add(ident)
                    all_variants.append(ident)

        canonical["variant_idents"] = all_variants
        canonical["variants_count"] = len(all_variants)
        if all_variants:
            base = (canonical.get("base_title") or canonical.get("series_name")
                    or canonical.get("title") or "")
            if base:
                refs = _load_variants_cache(base, mode=mode, ttl=24 * 3600)
                if not refs:
                    refs = [{"ident": i, "name": "", "size": 0, "img": ""}
                            for i in all_variants if i]
                if refs:
                    _save_variants_cache(base, mode, refs)
        canonical["dubbed"] = any(item_has_cz_dub(it) for it in group)

        # Cache pro play_pick: musí obsahovat full variant refs.
        # Načteme všechny dílčí cache a sjednotíme.
        for it in group:
            sub_key = it.get("base_title") or it.get("series_name") or it.get("title")
            if sub_key:
                refs = _load_variants_cache(sub_key, mode=mode, ttl=24 * 3600)
                for r in refs:
                    if r.get("ident") and r["ident"] in seen_idents:
                        # pouze ty, co jsou ve sjednoceném identu setu
                        if not any(x.get("ident") == r["ident"] for x in all_variant_refs):
                            all_variant_refs.append(r)

        if all_variant_refs:
            all_variant_refs.sort(
                key=lambda v: _quality_score(v.get("name") or ""), reverse=True
            )
            base = canonical.get("base_title") or canonical.get("series_name") or canonical.get("title")
            if base:
                _save_variants_cache(base, mode, all_variant_refs)

        merged.append(canonical)

    log.debug("_dedupe_after_enrich(mode=%s): %d -> %d items", mode, len(items), len(merged))
    return merged


# ---------------------------------------------------------------------------
# 5b-ii) ŘAZENÍ – multi-key sorty pro rubriky
# ---------------------------------------------------------------------------

# Mapování indexu (z addon settings enum) na vnitřní sort mode.
SORT_MOVIES = [
    "popularity_year_quality",  # 0 - default (popularita -> rok -> kvalita)
    "year_desc",                # 1 - od nejnovějších
    "quality",                  # 2 - od nejlepší kvality
    "rating",                   # 3 - hodnocení (ČSFD-like přes TMDB)
    "webshare",                 # 4 - bez třídění (pořadí z Webshare)
]
SORT_SERIES = [
    "popularity",               # 0 - default (popularita TMDB ~ Netflix/IMDB)
    "rating",                   # 1 - hodnocení
    "year_desc",                # 2 - od nejnovějších
    "webshare",                 # 3
]
SORT_NEW_DUB = [
    "quality_popularity",       # 0 - default (kvalita + popularita)
    "popularity",               # 1
    "quality",                  # 2
    "year_desc",                # 3
    "rating",                   # 4
]
SORT_KIDS = [
    "rating",                   # 0 - default (ČSFD-like)
    "popularity",               # 1
    "year_desc",                # 2
]


def _read_sort(setting_id: str, modes: List[str]) -> str:
    """Načte sort mode z addon settings (enum index) -> string."""
    addon = _addon_safe()
    if addon is None:
        return modes[0]
    raw = addon.getSetting(setting_id) or "0"
    try:
        idx = int(raw)
    except ValueError:
        idx = 0
    if 0 <= idx < len(modes):
        return modes[idx]
    return modes[0]


def _read_bool(setting_id: str, default: bool = False) -> bool:
    addon = _addon_safe()
    if addon is None:
        return default
    raw = (addon.getSetting(setting_id) or "").lower()
    if raw in ("true", "1"):
        return True
    if raw in ("false", "0"):
        return False
    return default


def _sort_items(items: List[Dict[str, Any]], mode: str) -> List[Dict[str, Any]]:
    """Multi-key seřazení video-items podle zvoleného mode."""
    if not items or mode == "webshare":
        return items

    def k_pop(x):     return -float(x.get("popularity") or 0)
    def k_year(x):    return -int(x.get("year") or 0)
    def k_quality(x): return -int(x.get("quality_score") or 0)
    def k_rating(x):  return -float(x.get("rating") or 0)

    if mode == "popularity_year_quality":
        items.sort(key=lambda x: (k_pop(x), k_year(x), k_quality(x)))
    elif mode == "quality_popularity":
        items.sort(key=lambda x: (k_quality(x), k_pop(x)))
    elif mode == "year_desc":
        items.sort(key=k_year)
    elif mode == "quality":
        items.sort(key=k_quality)
    elif mode == "rating":
        items.sort(key=lambda x: (k_rating(x), k_pop(x)))
    elif mode == "popularity":
        items.sort(key=lambda x: (k_pop(x), k_rating(x)))
    return items


# ---------------------------------------------------------------------------
# 5b) Veřejné rubriky – jednotná funkce + konvenientní wrappery
# ---------------------------------------------------------------------------

def _category_grouped(
    setting_id: str,
    default_query: str,
    sort: str,
    page: int,
    mode: str,
    skip_csfd: bool = False,
    csfd_rescue: bool = False,
) -> Optional[List[Dict[str, Any]]]:
    """
    Stáhne JEDNU Webshare stránku kategorie.

    Návratový kontrakt pro paginate_with_fetcher:
      None  = Webshare už nemá víc souborů
      []    = WS dal soubory, ale po filtrech nic
      [its] = video-items pro UI

    :param skip_csfd: v0.0.128 - vynech ČSFD fallback enrich (rychlejsi
                      first-load). ČSFD scraping je kvuli Cloudflare
                      dominantni bottleneck (1-3s/film). TMDB staci pro
                      plakaty u vetsiny filmu; stejne jako 4K/BluRay rubriky.
    :param csfd_rescue: v0.0.130 - po TMDB dopln plakaty poster-less
                      polozkam cilenou (omezenou) CSFD zachranou.
    """
    query = _addon_query(setting_id, default_query)
    files = search_videos(query=query, sort=sort, page=page)
    if files is None or len(files) == 0:
        # Webshare opravdu nemá víc - signal exhausted
        return None
    if mode == "series":
        return _series_from_groups(_group_by_series(files))
    files = _exclude_series(files)
    return _movies_from_groups(_group_by_title(files), skip_csfd=skip_csfd,
                               csfd_rescue=csfd_rescue)


def _has_poster(it: Dict[str, Any]) -> bool:
    """True pokud položka má skutečný (http) plakát z TMDB/ČSFD."""
    p = (it.get("poster") or "").strip()
    return p.startswith("http://") or p.startswith("https://")


def _poster_first_sort_key(it: Dict[str, Any]):
    """
    Globální sort klíč pro listingy:
      1) položky s plakátem nahoru (priorita)
      2) hodnocení (vyšší první)
      3) popularita (vyšší první)
      4) rok (novější první)
      5) kvalita (vyšší první)
      6) abecedně podle titulu (tie-breaker)
    """
    has_poster = 0 if _has_poster(it) else 1  # 0 = první
    rating = -float(it.get("rating") or 0)
    pop = -float(it.get("popularity") or 0)
    year = -int(it.get("year") or 0)
    quality = -int(it.get("quality_score") or 0)
    title = (it.get("title_localized") or it.get("title") or "").lower()
    return (has_poster, rating, pop, year, quality, title)


def _recent_first_sort_key(it: Dict[str, Any]):
    """
    Sort klíč pro rubriku Novinky / Filmy novinky dabované:
      1) ROK filmu (novější první)         <-- hlavní kritérium
      2) DATUM PŘIDÁNÍ na WS (čerstvé nahoru) <-- tiebreaker stejného roku
      3) položky s plakátem nahoru
      4) popularita (TMDB - aktuálně populární)
      5) hodnocení
      6) kvalita (1080p / 4K nad 720p)
      7) abecedně podle titulu

    Filmy bez roku (year=0) padnou na konec, ale neztratí se -
    user je pořád uvidí po novějších.

    POZOR: ws_added je 'YYYY-MM-DD HH:MM:SS' string -> ASCII porovnání
    funguje DESC pokud invertujeme přes lambda klíče (řazení sestupně).
    """
    year_raw = int(it.get("year") or 0)
    year_sort = (0, -year_raw) if year_raw > 0 else (1, 0)
    # Pro ws_added: invertujeme řazením přes lambda srovnání obráceně.
    # Tuple sort jde ASC, takže pro string řazení DESC použijeme trik:
    # vrátíme negativní hash nebo (-len, reversed_string). Jednodušší:
    # uložíme přímo string a sort_key bude funkce vracející (záporný_rok,
    # záporné_přidání_jako_unix_ts). Konverze na unix ts:
    ws_added_str = (it.get("ws_added") or "").strip()
    added_ts = _added_to_ts(ws_added_str)
    has_poster = 0 if _has_poster(it) else 1
    pop = -float(it.get("popularity") or 0)
    rating = -float(it.get("rating") or 0)
    quality = -int(it.get("quality_score") or 0)
    title = (it.get("title_localized") or it.get("title") or "").lower()
    return (year_sort, -added_ts, has_poster, pop, rating, quality, title)


def _added_to_ts(added: str) -> float:
    """
    Z Webshare 'added' (YYYY-MM-DD HH:MM:SS) udělá unix timestamp.
    Vrací 0.0 pokud chybí / nečitelné.
    """
    if not added:
        return 0.0
    try:
        from datetime import datetime
        # Webshare může vracet různé varianty: "2026-05-15 10:23:11"
        # nebo jen "2026-05-15". Zkusíme oba.
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(added.strip(), fmt).timestamp()
            except ValueError:
                continue
    except Exception:  # noqa: BLE001
        pass
    return 0.0


# ---------------------------------------------------------------------------
# Filtry obsahu (kvalita / dabing / titulky) - sdílené napříč rubrikami
# ---------------------------------------------------------------------------

def _min_quality_filter(items: List[Dict[str, Any]],
                        min_score: int = 800) -> List[Dict[str, Any]]:
    """
    Vyhodí položky, jejichž max_quality_score < min_score.
    Default 800 = minimálně 1080p (4K=1000+, 1080p=800+, 720p=600+).

    Položky bez quality_score (= neuměli jsme detect rozlišení z názvu)
    PROJDOU - lepší šance že tam něco kvalitního je, než to skrýt natvrdo.
    """
    if min_score <= 0:
        return items
    out = []
    for it in items:
        qs = int(it.get("quality_score") or 0)
        if qs == 0 or qs >= min_score:
            out.append(it)
    log.debug("_min_quality_filter(min=%d): %d -> %d items",
             min_score, len(items), len(out))
    return out


def _dubbed_only_filter(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Jen položky s CZ/SK dabingem (dubbed == True nebo CZ stopa v ws_names)."""
    out = [it for it in items if item_has_cz_dub(it)]
    log.debug("_dubbed_only_filter: %d -> %d items (jen CZ/SK dabing)",
             len(items), len(out))
    return out


def _subs_only_filter(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Jen položky s CZ/SK titulky (subs_cz == True).
    Pozn: dabované filmy mohou mít taky subs_cz - to je OK, projdou.
    User chce 'novinky s EN dabingem + CZ titulky', tak hlavně subs_cz.
    """
    out = [it for it in items if it.get("subs_cz")]
    log.debug("_subs_only_filter: %d -> %d items (s CZ/SK titulky)",
             len(items), len(out))
    return out


def _paginate_rubrika(
    cache_key: str,
    ws_fetcher,
    ui_page: int,
    post_filter=None,
    sort_mode: Optional[str] = None,
    poster_first: bool = True,
    sort_key_override=None,
    max_ws_pages: int = 4,
    ttl_override: Optional[int] = None,
    max_wait_sec: Optional[float] = RUBRIKA_FETCH_MAX_WAIT,
) -> Tuple[List[Dict[str, Any]], bool]:
    """
    Společný wrapper: 50 položek na ui_page, plakát-první sort napříč
    celým bufferem.

    Vrací tuple (items, has_more) - router použije has_more pro rozhodnutí,
    jestli přidat 'Další stránka' tlačítko.

    :param ws_fetcher:  callable(ws_page) -> None|[]|[items]
                        (None = WS exhausted, [] = po filtru 0)
    :param post_filter: callable(items) -> items (filtr po fetchnutí)
    :param sort_mode:   sort mode z _sort_items (aplikuje se per fetch)
    :param poster_first: True = items s plakátem nahoru (globální sort bufferu)
    :param sort_key_override: vlastní sort funkce pro globální buffer
                              (priorita před poster_first - např. pro Novinky
                              kde chceme year DESC jako primární).
    :param max_ws_pages: kolik WS stránek max načíst pro 1 ui_page.
    """
    from . import pagination

    def _fetcher(ws_page: int) -> Optional[List[Dict[str, Any]]]:
        items = ws_fetcher(ws_page)
        if items is None:
            return None  # WS opravdu došel
        if not items:
            return []    # WS dal soubory, ale po filtru nic
        if post_filter is not None:
            items = post_filter(items)
        if sort_mode:
            items = _sort_items(list(items), sort_mode)
        return items

    if sort_key_override is not None:
        sort_key = sort_key_override
    elif poster_first:
        sort_key = _poster_first_sort_key
    else:
        sort_key = None

    return pagination.paginate_with_fetcher(
        cache_key=cache_key,
        fetcher=_fetcher,
        ui_page=ui_page,
        max_ws_pages=max_ws_pages,
        sort_key=sort_key,
        ttl_override=ttl_override,
        max_wait_sec=max_wait_sec,
    )


def _filter_year_range(items: List[Dict[str, Any]],
                       min_year: Optional[int] = None,
                       max_year: Optional[int] = None) -> List[Dict[str, Any]]:
    """Ponechá jen položky s rokem v zadaném rozsahu (jako filtr pro Filmy)."""
    if min_year is None and max_year is None:
        return items
    out = []
    for it in items:
        y = it.get("year") or 0
        if min_year is not None and y and y < min_year:
            continue
        if max_year is not None and y and y > max_year:
            continue
        out.append(it)
    return out


def _read_year_range() -> tuple:
    """Načti year_min/year_max z addon settings (default 2000..2026)."""
    addon = _addon_safe()
    if addon is None:
        return (2000, 2026)
    try:
        min_y = int(addon.getSetting("year_min") or "2000")
    except ValueError:
        min_y = 2000
    try:
        max_y = int(addon.getSetting("year_max") or "2026")
    except ValueError:
        max_y = 2026
    return (min_y, max_y)


def _search_sort_key(it: Dict[str, Any], want_year: Optional[int] = None):
    """
    v0.0.102: Razeni vysledku hledani - kvalita, rok, CZ dab, cerstvost.

    SD / stare verze stejneho nazvu padnou dolu (Michael 2026 nahoru).
    """
    quality = int(it.get("quality_score") or 0)
    year = _effective_release_year(it)
    cy = _current_year()
    year_rank = 0
    if want_year and year == int(want_year):
        year_rank = 3
    elif year in (cy, cy - 1):
        year_rank = 2
    elif year > 0:
        year_rank = 1
    dubbed = 1 if it.get("dubbed") else 0
    added = _added_to_ts(it.get("ws_added") or "")
    return (-quality, -year_rank, -year, -dubbed, -added)


def get_movies(
    sort: str = "recent",
    page: int = 1,
    query_override: Optional[str] = None,
    search_year: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Filmy – vrací (items, has_more).

    v0.0.99: query_override = volne hledani v rubrice (jako 4K) – bez
    year filtru a bez agresivnich filtru.
    """
    log.debug("get_movies(sort=%s, ui_page=%s, override=%r)",
              sort, page, query_override)
    if query_override:
        q = query_override.strip()
        queries = _build_rubric_search_queries(
            q, search_year, extras=[f"{q} CZ", f"{q} 1080p"])
        cache_key = f"rubrika:movies:search:v5:{q.lower()}:y{search_year or 0}"
        return _paginate_multi_query(
            cache_key=cache_key,
            queries=queries,
            ui_page=page,
            pre_filter=None,
            post_filter=None,
            sort_key=lambda it: _search_sort_key(it, want_year=search_year),
            max_ws_pages=12,
            skip_enrich=True,
            skip_poster=True,
            skip_aggressive_filters=True,
            trust_ws_query=True,
        )

    query = _addon_query("q_movies", DEFAULT_QUERIES["movies"])
    min_y, max_y = _read_year_range()
    sort_mode = _read_sort("sort_movies", SORT_MOVIES)

    def _ws_fetch(ws_page: int):
        # v0.0.128: skip_csfd=True - ČSFD fallback je hlavni brzda pri
        # nacitani Filmu (1-3s/film pres Cloudflare). TMDB da plakaty rychle,
        # stejne jako u 4K/BluRay/Animovanych rubrik. ČSFD rating se dopini
        # z cross-rubric snapshotu / pri prehravani.
        return _category_grouped("q_movies", DEFAULT_QUERIES["movies"],
                                 sort=sort, page=ws_page, mode="movie",
                                 skip_csfd=True, csfd_rescue=True)

    def _post(items):
        return _filter_year_range(items, min_year=min_y, max_year=max_y)

    cache_key = f"rubrika:movies:v3:{query}:{sort}:y{min_y}-{max_y}"
    return _paginate_rubrika(cache_key, _ws_fetch, ui_page=page,
                             post_filter=_post, sort_mode=sort_mode)


def _paginate_multi_query(
    cache_key: str,
    queries: List[str],
    ui_page: int,
    pre_filter=None,
    post_filter=None,
    sort_key=None,
    max_ws_pages: int = 6,
    grouping: str = "movie",
    skip_csfd: bool = False,
    skip_enrich: bool = False,
    skip_poster: bool = False,
    skip_aggressive_filters: bool = False,
    search_query: Optional[str] = None,
    search_year: Optional[int] = None,
    trust_ws_query: bool = False,
    csfd_rescue: bool = False,
    max_wait_sec: Optional[float] = RUBRIKA_FETCH_MAX_WAIT,
) -> Tuple[List[Dict[str, Any]], bool]:
    """
    Sjednoceny multi-query fetch + paginate (v0.0.53).

    Drive duplikovano 5x v get_4k, get_bluray, get_movies_new_dub, get_latest.
    Ted jedna funkce + thin wrappers - 250 radku usetreno.

    :param queries:     list dotazu, kterymi se rotuje pres WS stranky
    :param pre_filter:  callable(items)->items, PRED TMDB enrichmentem (rychle)
    :param post_filter: callable(items)->items, PO TMDB enrichmentu (pomale)
    :param sort_key:    sort funkce pro globalni buffer
    :param grouping:    "movie" -> _movies_from_groups, "series" -> _series_from_groups
    :param skip_csfd:   v0.0.70 - propaga do _movies_from_groups (jen movie mode).
                        True = vynechat CSFD enrich (rychlejsi first-load).
                        Pouziva animated rubrika kde ma genre filter
                        prioritu nad CSFD ratingem.
    :param skip_enrich: v0.0.92 - bez TMDB/CSFD enrich (rubrika Koncerty).
    :param skip_poster: v0.0.101 - bez only_with_poster filtru (explicitni hledani).
    :param skip_aggressive_filters: preskocit obf/serial/pl/lect filtry.
    :param search_query: volitelny filtr titulu (vypnuty pri trust_ws_query).
    :param trust_ws_query: v0.0.103 rubric search - ver WS dotazu, bez re-filtru.
    """
    def _ws_fetch(ws_page: int):
        idx = (ws_page - 1) % len(queries)
        q_ws_page = (ws_page - 1) // len(queries) + 1
        q = queries[idx]
        log.debug("multi_query[%s]: WS fetch q=%r q_page=%d",
                  cache_key, q, q_ws_page)
        files = search_videos(query=q, sort="recent", page=q_ws_page)
        if files is None or len(files) == 0:
            # Prvni rotace muze mit prazdne stranky - nepovazujeme to za konec
            return [] if ws_page < len(queries) * 3 else None
        if search_query and not trust_ws_query:
            files = _filter_files_for_search(files, search_query, search_year)
            if not files:
                return []
        if grouping == "movie":
            files = _exclude_series(files)
            return _movies_from_groups(_group_by_title(files),
                                       pre_filter=pre_filter,
                                       skip_csfd=skip_csfd,
                                       csfd_rescue=csfd_rescue,
                                       skip_enrich=skip_enrich,
                                       skip_poster=skip_poster,
                                       skip_aggressive_filters=skip_aggressive_filters)
        # series
        return _series_from_groups(_group_by_series(files))

    return _paginate_rubrika(
        cache_key, _ws_fetch, ui_page=ui_page,
        post_filter=post_filter,
        sort_key_override=sort_key,
        max_ws_pages=max_ws_pages,
        max_wait_sec=max_wait_sec,
    )


def _effective_release_year(it: Dict[str, Any]) -> int:
    """
    v0.0.83: Nejspolehlivejsi rok pro filtry - max z TMDB, nazvu souboru
    a WS variant. Oprava: TMDB obcas matchne jiny film (napr. stary
    'Michael' misto 'Michael 2026') a post_filter ho vyhodil z Novych dabingu.
    """
    years: List[int] = []
    y = int(it.get("year") or 0)
    if y > 0:
        years.append(y)
    for field in ("base_title", "title", "title_localized"):
        gy = _guess_year((it.get(field) or ""))
        if gy and gy > 0:
            years.append(int(gy))
    base = it.get("base_title") or it.get("title") or ""
    if base:
        try:
            refs = _load_variants_cache(base, mode="movie", ttl=24 * 3600)
            for v in refs:
                gy = _guess_year(v.get("name") or "")
                if gy and gy > 0:
                    years.append(int(gy))
        except Exception:  # noqa: BLE001
            pass
    return max(years) if years else 0


def _read_new_dub_min_year() -> Optional[int]:
    """
    Min. rok pro filtr v rubrice "Filmy novinky dabované CZ/SK".
    Default: aktuální rok - 2 (= 2024 v roce 2026 - zachytí čerstvé
    dabingy starších filmů, ale nezahltí to klasikou z 2010).
    Pokud user nastaví 0, filter se vypne (vše projde).
    """
    addon = _addon_safe()
    cy = _current_year()
    default = cy - 2
    if addon is None:
        return default
    raw = (addon.getSetting("new_dub_min_year") or "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
    except ValueError:
        return default
    if v <= 0:
        return None
    return v


def get_movies_new_dub(
    sort: str = "recent",
    page: int = 1,
    query_override: Optional[str] = None,
    search_year: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], bool]:
    """
    Filmy novinky dabované CZ/SK - vrací (items, has_more).

    v0.0.99: query_override = volne hledani v rubrice (bez year/dab prefiltru).
    """
    log.debug("get_movies_new_dub(ui_page=%s, override=%r)", page, query_override)
    if query_override:
        q = query_override.strip()
        queries = _build_rubric_search_queries(
            q, search_year,
            extras=[f"{q} CZ", f"{q} dabing", f"{q} CZ dabing"])
        cache_key = f"rubrika:newdub:search:v5:{q.lower()}:y{search_year or 0}"
        return _paginate_multi_query(
            cache_key=cache_key,
            queries=queries,
            ui_page=page,
            pre_filter=None,
            post_filter=None,
            sort_key=lambda it: _search_sort_key(it, want_year=search_year),
            max_ws_pages=12,
            skip_enrich=True,
            skip_poster=True,
            skip_aggressive_filters=True,
            trust_ws_query=True,
        )

    user_query = _addon_query("q_movies_new_dub", DEFAULT_QUERIES["movies_new_dub"])
    cy = _current_year()
    # v0.0.63: pridan 'CZ dub' query - cca 10% Webshare souboru pouziva
    # anglickou kratkou formu (Avatar.CZ.dub.mkv). Drive je catchla jen
    # nepresna "<rok> CZ" query, ted explicitne. _detect_dubbed v
    # _EXPLICIT_DUB_PATTERN uz oba tagy (dab, dub, dabbed) detekuje.
    queries = [
        f"{cy} CZ dabing", f"{cy} CZ dub", f"{cy} CZ", f"{cy} dabing",
        f"{cy-1} CZ dabing", f"{cy-1} CZ dub", f"{cy-1} CZ", f"{cy-1} dabing",
        f"{cy-2} CZ dabing", f"{cy-2} CZ dub", f"{cy-2} CZ",
        f"{cy-3} CZ",
        user_query,
    ]

    def _pre(its):
        its = _min_quality_filter(its, min_score=800)
        its = _dubbed_only_filter(its)
        return its

    def _post(its):
        min_y = _read_new_dub_min_year()
        if min_y is not None:
            kept = []
            for it in its:
                eff_y = _effective_release_year(it)
                if eff_y == 0 or eff_y >= min_y:
                    kept.append(it)
                else:
                    log.debug("new_dub post_filter: skip %r (eff_year=%d < %d)",
                              it.get("base_title") or it.get("title"),
                              eff_y, min_y)
            its = kept
        return its

    return _paginate_multi_query(
        cache_key=f"rubrika:newdub:v13:{cy}:{user_query}",
        queries=queries, ui_page=page,
        pre_filter=_pre, post_filter=_post,
        sort_key=_recent_first_sort_key,
        max_ws_pages=6,
        skip_csfd=True,  # v0.0.128: bez ČSFD brzdy (jen TMDB plakaty)
        csfd_rescue=True,  # v0.0.130: dopln plakaty ceskym titulum bez TMDB
    )


def item_has_cz_dub(it: Dict[str, Any]) -> bool:
    """True pokud polozka ma overeny CZ/SK dabing (flag z variant)."""
    return bool(it.get("dubbed"))


def variant_has_cz_lang(name: str) -> bool:
    """CZ/SK dabing nebo titulky - pro quality picker z CZ rubrik."""
    if not name:
        return False
    if _detect_dubbed(name):
        return True
    return bool(_detect_subtitles(name))


def filter_cz_variants(variants: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Jen varianty s CZ dabingem/titulky (bez ciste EN)."""
    out = [v for v in variants if variant_has_cz_lang(v.get("name") or "")]
    out.sort(key=lambda v: _quality_score(v.get("name") or ""), reverse=True)
    return out


def _variant_matches_year(name: str, want_year: Optional[int]) -> bool:
    """
    Filtr roku pro picker: Michael (TMDB 2026) nesmi nabrát Michael 1996.

    Soubor bez roku v nazvu projde (napr. 'Michael_cz dabing').
    Soubor s jinym rokem se vyřadí.
    """
    if not want_year:
        return True
    file_year = _guess_year(name)
    if file_year is None:
        return True
    return int(file_year) == int(want_year)


def filter_variants_by_year(
    variants: List[Dict[str, Any]],
    want_year: Optional[int],
) -> List[Dict[str, Any]]:
    if not want_year or not variants:
        return variants
    out = [v for v in variants if _variant_matches_year(v.get("name") or "", want_year)]
    # v0.0.117: spatny WS/TMDB rok nesmi zablokovat prehrani (0 variant).
    if not out:
        log.info("filter_variants_by_year(%s): 0 shod -> ponechavam %d variant",
                 want_year, len(variants))
        return variants
    return out


def _item_has_cz_lang(it: Dict[str, Any]) -> bool:
    """CZ/SK dabing, titulky, nebo Dual (= casto CZ+EN na WS)."""
    return item_has_cz_dub(it) or bool(it.get("subs_cz"))


def _dubbed_or_subs_filter(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Jen položky s CZ/SK dabingem, titulky, nebo Dual audio."""
    out = [it for it in items if _item_has_cz_lang(it)]
    log.debug("_dubbed_or_subs_filter: %d -> %d items (CZ dab/tit/dual)",
             len(items), len(out))
    return out


def _pre_filter_quality_dubsub(min_score: int):
    """Pre-filter factory: min quality + (CZ dab nebo CZ tit). Bez TMDB."""
    def _pre(its):
        its = _min_quality_filter(its, min_score=min_score)
        its = _dubbed_or_subs_filter(its)
        return its
    return _pre


def _pre_filter_quality_only(min_score: int):
    """
    Pre-filter factory: JEN min quality (BEZ CZ pozadavku).

    v0.0.62: pouziva se pri EXPLICITNIM search v rubrice. Kdyz user
    vyhleda "Avatar" v 4K, chce najit Avatara - i kdyz tam neni CZ
    dab. Default listing (browse rubriky) ma filtr s CZ - tam to dava
    smysl, protoze user prochazi "co je nabidnuto v 4K s CZ". Search
    je naopak cilene - user uz vi co chce.
    """
    def _pre(its):
        return _min_quality_filter(its, min_score=min_score)
    return _pre


def get_4k(sort: str = "recent", page: int = 1,
           query_override: Optional[str] = None,
           search_year: Optional[int] = None) -> Tuple[List[Dict[str, Any]], bool]:
    """Filmy v 4K (2160p / UHD). v0.0.53: refactor pres _paginate_multi_query.

    v0.0.62: pri search (query_override):
      - pridan BARE query (jen titul, bez rubric markeru) - Webshare najde
        i soubory ktere nemaji "4K" v nazvu, filter pak nechá jen ty s
        vysokou kvalitou.
      - filter relaxovany - jen quality, ne CZ pozadavek (user chce
        najit konkretni film).
    """
    log.debug("get_4k(ui_page=%s, override=%r)", page, query_override)
    if query_override:
        q = query_override.strip()
        queries = _build_rubric_search_queries(
            q, search_year, extras=[f"{q} 2160p", f"{q} 4K", f"{q} UHD"])
        cache_key = f"rubrika:4k:search:v5:{q.lower()}:y{search_year or 0}"
        pre_filter = _pre_filter_quality_only(min_score=1000)
    else:
        queries = ["2160p CZ", "2160p", "4K CZ", "UHD CZ",
                   "2160p dabing", "2160p titulky",
                   "4K dabing", "UHD dabing",
                   "4K", "UHD", "2160p 2026", "2160p 2025"]
        cache_key = "rubrika:4k:default:v3"
        pre_filter = _pre_filter_quality_dubsub(min_score=1000)

    return _paginate_multi_query(
        cache_key=cache_key, queries=queries, ui_page=page,
        pre_filter=pre_filter,
        sort_key=_recent_first_sort_key,
        max_ws_pages=8,
        skip_enrich=bool(query_override),
        skip_poster=bool(query_override),
        skip_aggressive_filters=bool(query_override),
        trust_ws_query=bool(query_override),
        search_year=search_year,
        skip_csfd=True,  # v0.0.128: bez ČSFD brzdy (jen TMDB plakaty)
        csfd_rescue=not bool(query_override),  # v0.0.130: dopln plakaty
    )
# TMDB pouziva stabilni IDs (viz /genre/movie/list):
#     16 = Animation
#     10751 = Family (casto u animovanych pro deti)
# Vystaci nam 16, protoze 10751 sam o sobe je rodinna (e.g. Home Alone)
# ne nutne animace. Vsichni Pixar/Disney/DreamWorks/anime maji 16.
_TMDB_GENRE_ANIMATION = 16
_TMDB_GENRE_DOCUMENTARY = 99

_DOC_FILENAME_HINTS = (
    "dokument", "documentary", "doku", "nature", "planet", "wild",
    "national geographic", "bbc earth", "čt", "ct ", "13. komnata",
)


# v0.0.69 + perf fix: high-signal filename hints pro animaci.
# Pokud filename obsahuje tyto klicaky, mame VYSOKOU jistotu, ze
# je to animovany film (Pixar/Disney/Illumination = ~95-100% animace).
# Tim padem post_filter muze takove polozky pustit BEZ TMDB genre check
# (ktery casto selhava nebo vrati prazdne genre_ids pri TMDB enrich
# selhani na uvodnich stejnich nebo niche releasech).
_ANIMATION_FILENAME_HINTS_RE = re.compile(
    r"\b("
    r"pixar|disney|dreamworks|illumination|ghibli|"
    r"animovan[ya]|animovan[éy]|animated|anime|"
    r"kreslen[yý]|kreslen[áé]|"
    r"cartoon|animation"
    r")\b",
    re.IGNORECASE,
)


def _has_animation_filename_hint(item: Dict[str, Any]) -> bool:
    """v0.0.69: rychly check - obsahuje base_title nebo title anim klicak?

    Pouziva se v _post_filter_animated jako bypass pro TMDB genre check:
    Pixar/Disney/anime/etc. jsou *prakticky vzdy* animace, takze i kdyz
    TMDB enrich selze (siti chyba) nebo TMDB nezna film, item se prijme.
    """
    parts = [
        item.get("base_title") or "",
        item.get("title") or "",
        item.get("original_title") or "",
    ]
    for p in parts:
        if p and _ANIMATION_FILENAME_HINTS_RE.search(p):
            return True
    return False


def _read_animated_min_quality() -> int:
    """v0.0.69: minimalni quality score pro Animovane CZ/SK rubric.

    Mapping:
        720p = 600
        1080p = 800  (default)
        4K / 2160p = 1000
    User v settings (animated_min_quality) muze nastavit cislo
    "720" / "1080" / "2160" pripadne primo skore.
    """
    addon = _addon_safe()
    if addon is None:
        return 800
    raw = (addon.getSetting("animated_min_quality") or "1080").strip()
    if not raw:
        return 800
    # User muze napsat "720" / "1080" / "2160" - prevedem na skore
    mapping = {
        "720": 600, "720p": 600,
        "1080": 800, "1080p": 800, "fhd": 800,
        "2160": 1000, "2160p": 1000, "4k": 1000, "uhd": 1000,
    }
    val = mapping.get(raw.lower())
    if val:
        return val
    # Fallback: predpoklad ze user dal primo skore (integer)
    try:
        return int(raw)
    except ValueError:
        return 800


def _post_filter_animated(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """v0.0.69: prijme polozky kde JEDNO z:
      (a) TMDB genre_ids obsahuje 16 (Animation), NEBO
      (b) filename ma znamy animation hint (Pixar/Disney/anime/...).

    (b) bypass je dulezity pro perf: nase Webshare queries cili
    na "Pixar CZ" / "Disney CZ" / "anime CZ" coz vraci ~95% animaci.
    Kdyby TMDB enrich selhal nebo film nemel TMDB zaznam, nemeli
    bychom je vyhazovat - filename to potvrzuje.

    Tim padem se snizuje retry-fetch loop v pagination engine
    (filter rejection rate z ~70% na ~20%) = rychlejsi load.
    """
    out = []
    accepted_by_tmdb = 0
    accepted_by_hint = 0
    for it in items:
        if _TMDB_GENRE_ANIMATION in (it.get("genre_ids") or []):
            out.append(it)
            accepted_by_tmdb += 1
        elif _has_animation_filename_hint(it):
            out.append(it)
            accepted_by_hint += 1
    log.debug("_post_filter_animated: %d -> %d items "
              "(tmdb=%d, filename_hint=%d)",
              len(items), len(out), accepted_by_tmdb, accepted_by_hint)
    return out


def _has_documentary_filename_hint(item: Dict[str, Any]) -> bool:
    names = " ".join([
        item.get("base_title") or "",
        item.get("title") or "",
        item.get("ws_names") or "",
    ]).lower()
    return any(h in names for h in _DOC_FILENAME_HINTS)


def _post_filter_documentary(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Prijme polozky s TMDB zanrem Dokument (99) nebo dokument hint v nazvu."""
    out = []
    for it in items:
        gids = {int(g) for g in (it.get("genre_ids") or [])}
        if _TMDB_GENRE_DOCUMENTARY in gids:
            out.append(it)
        elif _has_documentary_filename_hint(it):
            out.append(it)
    log.debug("_post_filter_documentary: %d -> %d", len(items), len(out))
    return out


def get_movies_documentary(sort: str = "recent", page: int = 1,
                           query_override: Optional[str] = None,
                           search_year: Optional[int] = None,
                           ) -> Tuple[List[Dict[str, Any]], bool]:
    """Dokumentární filmy CZ/SK – TMDB žánr 99 + WS dotazy."""
    log.debug("get_movies_documentary(ui_page=%s, override=%r)", page, query_override)
    min_quality = _read_animated_min_quality()

    if query_override:
        q = query_override.strip()
        queries = _build_rubric_search_queries(
            q, search_year, extras=[f"{q} dokument", f"{q} documentary", f"{q} CZ"])
        cache_key = (f"rubrika:documentary:search:v1:q{min_quality}:"
                     f"{q.lower()}:y{search_year or 0}")
        pre_filter = _pre_filter_quality_only(min_score=min_quality)
    else:
        queries = [
            "dokument CZ", "dokumentární CZ", "dokumentarni CZ",
            "documentary CZ", "doku CZ", "nature documentary CZ",
            "National Geographic CZ", "BBC Earth CZ",
            "dokument dabing", "dokument titulky",
            "dokument 2026", "dokument 2025",
        ]
        cache_key = f"rubrika:documentary:default:v1:q{min_quality}"
        pre_filter = _pre_filter_quality_dubsub(min_score=min_quality)

    return _paginate_multi_query(
        cache_key=cache_key, queries=queries, ui_page=page,
        pre_filter=pre_filter,
        post_filter=_post_filter_documentary,
        sort_key=_recent_first_sort_key,
        max_ws_pages=6,
        skip_csfd=True,
        skip_enrich=bool(query_override),
        skip_poster=bool(query_override),
        skip_aggressive_filters=bool(query_override),
        trust_ws_query=bool(query_override),
        search_year=search_year,
    )


def get_movies_animated(sort: str = "recent", page: int = 1,
                         query_override: Optional[str] = None,
                         search_year: Optional[int] = None,
                         ) -> Tuple[List[Dict[str, Any]], bool]:
    """Filmy animovane CZ/SK - vraci (items, has_more).

    v0.0.69: TMDB genre_ids filter (16 = Animation) + filename hint
    bypass (Pixar/Disney/anime/...). Pre-filter (min. kvalita + CZ)
    uz odriza ~80% Webshare souboru, post_filter pak prijima:
      - polozky se TMDB genre 16 (Animation), NEBO
      - polozky s anim hint v filename (vetsi tolerance, lepsi perf)

    Pri search (query_override) je quality filter relaxnut (jen
    min_quality bez CZ pozadavku) - user uz vi co hleda.

    Sort: rok DESC (novinky nahoru) + fallback ws_added DESC.
    """
    log.debug("get_movies_animated(ui_page=%s, override=%r)", page, query_override)
    min_quality = _read_animated_min_quality()

    if query_override:
        q = query_override.strip()
        queries = _build_rubric_search_queries(
            q, search_year, extras=[f"{q} CZ", f"{q} dabing", f"{q} animovany"])
        cache_key = (f"rubrika:animated:search:v4:q{min_quality}:"
                     f"{q.lower()}:y{search_year or 0}")
        pre_filter = _pre_filter_quality_only(min_score=min_quality)
    else:
        # v0.0.69 perf: tight, high-signal queries (~95% precision).
        # Drive jsme meli 19 queries vc. nejistych ("animated CZ" v EN, ...) -
        # ted 10 cilenych. Webshare full-text najde "animovaný" i pri zadani
        # "animovany" (diakritika tam neni striktni), takze diakritickou
        # variantu odebirame.
        queries = [
            "animovany CZ",       # primarni CZ
            "animovany dabing",   # primarni CZ (variant)
            "Pixar CZ",           # ~100% animace
            "Disney CZ",          # ~85% animace (vyjma live-action remakes)
            "DreamWorks CZ",      # ~95% animace
            "Illumination CZ",    # ~100% animace
            "kresleny CZ",        # ~95% animace (cesky kresleny)
            "anime CZ",           # ~100% animace
            "anime dabing",       # ~100% animace
            "Studio Ghibli CZ",   # ~100% animace (niche)
        ]
        cache_key = f"rubrika:animated:default:v2:q{min_quality}"
        pre_filter = _pre_filter_quality_dubsub(min_score=min_quality)

    return _paginate_multi_query(
        cache_key=cache_key, queries=queries, ui_page=page,
        pre_filter=pre_filter,
        post_filter=_post_filter_animated,
        sort_key=_recent_first_sort_key,
        max_ws_pages=6,
        skip_csfd=True,
        skip_enrich=bool(query_override),
        skip_poster=bool(query_override),
        skip_aggressive_filters=bool(query_override),
        trust_ws_query=bool(query_override),
        search_year=search_year,
    )


def get_bluray(sort: str = "recent", page: int = 1,
               query_override: Optional[str] = None,
               search_year: Optional[int] = None) -> Tuple[List[Dict[str, Any]], bool]:
    """Filmy BluRay. v0.0.53: refactor pres _paginate_multi_query.

    v0.0.62: stejne jako get_4k - bare query + relax filter pri search.
    """
    log.debug("get_bluray(ui_page=%s, override=%r)", page, query_override)
    if query_override:
        q = query_override.strip()
        queries = _build_rubric_search_queries(
            q, search_year, extras=[f"{q} BluRay", f"{q} BDRip", f"{q} BD"])
        cache_key = f"rubrika:bluray:search:v5:{q.lower()}:y{search_year or 0}"
        pre_filter = _pre_filter_quality_only(min_score=800)
    else:
        queries = ["BluRay CZ", "BluRay dabing", "BluRay titulky",
                   "Blu-ray CZ", "BDRip CZ", "BD CZ",
                   "BluRay 1080p CZ", "BluRay 2160p CZ",
                   "BluRay 2026", "BluRay 2025", "BluRay 2024", "BluRay"]
        cache_key = "rubrika:bluray:default:v3"
        pre_filter = _pre_filter_quality_dubsub(min_score=800)

    return _paginate_multi_query(
        cache_key=cache_key, queries=queries, ui_page=page,
        pre_filter=pre_filter,
        sort_key=_recent_first_sort_key,
        max_ws_pages=8,
        skip_enrich=bool(query_override),
        skip_poster=bool(query_override),
        skip_aggressive_filters=bool(query_override),
        trust_ws_query=bool(query_override),
        search_year=search_year,
        skip_csfd=True,  # v0.0.128: bez ČSFD brzdy (jen TMDB plakaty)
        csfd_rescue=not bool(query_override),  # v0.0.130: dopln plakaty
    )


def _resolve_kids_queries() -> List[str]:
    """
    Vrátí seznam dotazů pro Pohádky.

    - Pokud user v settings 'q_kids' vyplnil pipe-separated list (a|b|c)
      → použije se přesně ten seznam.
    - Pokud user vyplnil jediný dotaz (např. "pohádka")
      → použije se ten jediný dotaz + zbytek z DEFAULT_KIDS_QUERIES
        (jako další stránky), aby se dosáhlo hodně výsledků.
    - Pokud nic nevyplnil → použije se kompletní DEFAULT_KIDS_QUERIES.
    """
    raw = _addon_query("q_kids", "").strip()
    if not raw:
        return list(DEFAULT_KIDS_QUERIES)

    if "|" in raw:
        queries = [q.strip() for q in raw.split("|") if q.strip()]
        return queries or list(DEFAULT_KIDS_QUERIES)

    # Jeden dotaz - dáme ho na začátek, zbytek doplníme defaulty.
    primary = raw
    rest = [q for q in DEFAULT_KIDS_QUERIES if q.lower() != primary.lower()]
    return [primary] + rest


def get_kids(sort: str = "rating", page: int = 1) -> Tuple[List[Dict[str, Any]], bool]:
    """
    Pohádky CZ/SK – vrací (items, has_more).

    NOVÝ MODEL (v0.0.27):
    Hledá se POUZE v curated seznamu českých/slovenských pohádek
    (czech_fairy_tales.CZECH_FAIRY_TALES). Pro každý titul ze seznamu
    proběhne 1 fulltext search na Webshare:
      - Pokud WS najde aspoň 1 soubor → položka se přidá do rubriky.
      - Pokud WS nenajde nic → titul přeskočíme.

    Tím se rubrika čistě obsahuje JEN české/slovenské pohádky které
    máme dostupné. Žádné anglické filmy s podobným slovem, žádné
    seriály, žádné variace.

    Mapování pro pagination: každá WS "stránka" = N titulů ze seznamu
    (TITLES_PER_WS_PAGE). To umožní agregaci přes paginate_with_fetcher.
    """
    log.debug("get_kids(sort=%s, ui_page=%s)", sort, page)

    from . import czech_fairy_tales as cft
    fairy_tales = cft.CZECH_FAIRY_TALES
    if not fairy_tales:
        return [], False

    sort_mode = _read_sort("sort_kids", SORT_KIDS)

    # 10 titulů na jednu "WS stránku" → pagination si dofetchuje další
    TITLES_PER_WS_PAGE = 10

    def _search_one_title(entry):
        """v0.0.62: helper pro paralelni WS search per curated titul."""
        if _shutdown.is_shutting_down():
            return None
        title, year, _tags = entry
        try:
            files = search_videos(query=title, sort="rating", page=1)
            if not files:
                return None
            files = _exclude_series(files)
            if not files:
                return None
            target_norm = _norm_compare(title)
            matching = [f for f in files
                        if target_norm in _norm_compare(f.get("name") or "")]
            if not matching:
                return None
            return (title, year, matching)
        except Exception as exc:  # noqa: BLE001
            log.debug("kids search %r selhalo: %s", title, exc)
            return None

    def _ws_fetch(global_ws_page: int):
        # Slice seznamu pohádek pro tuto pseudo-WS-stránku
        start = (global_ws_page - 1) * TITLES_PER_WS_PAGE
        end = start + TITLES_PER_WS_PAGE
        slice_titles = fairy_tales[start:end]
        if not slice_titles:
            log.info("get_kids: seznam pohádek vyčerpán (page=%d)",
                     global_ws_page)
            return None  # exhausted

        # v0.0.62: PARALELNI WS search - drive 10 sekvencnich requestu
        # (= 5-10s na Xboxu), ted 5 workeru = ~1-2s.
        # Webshare unese paralel requesty bez rate limitu.
        ws_workers = min(5, len(slice_titles))
        results: List = []
        try:
            # v0.0.152: shutdown(wait=False) — Quit neceka na kids WS search
            pool = ThreadPoolExecutor(max_workers=ws_workers,
                                      thread_name_prefix="kids-ws")
            try:
                results = list(pool.map(_search_one_title, slice_titles))
            finally:
                try:
                    pool.shutdown(wait=False, cancel_futures=True)
                except TypeError:
                    pool.shutdown(wait=False)
        except Exception as exc:  # noqa: BLE001
            log.exception("kids paralel search selhal: %s", exc)
            results = [_search_one_title(t) for t in slice_titles]

        items_out: List[Dict[str, Any]] = []
        for res in results:
            if res is None:
                continue
            title, year, matching = res
            # KLÍČOVÁ ZMĚNA (v0.0.29):
            # NEdělíme matching files podle interpretace názvu ze souboru
            # (_group_by_title by udělal víc items kvůli variantám
            # "Pysna princezna 1952" vs "Pysna.princezna.1080p").
            # Místo toho VYNUTÍME 1 group = 1 item, kde KLÍČ je curated
            # titul ze seznamu. Všechny matching soubory pak slouží jen
            # jako varianty kvality pro quality picker.
            single_group = {title: matching}
            sub_items = _movies_from_groups(single_group)
            if not sub_items:
                continue

            # Před vrácením - vynutit titul a rok ze seznamu (curated data).
            for it in sub_items:
                it["title"] = title
                if year:
                    it["year"] = year
                it["base_title"] = title
                items_out.append(it)

        return items_out  # může být [] (žádná pohádka v tomto slice
                          # nenalezena na WS) - pagination jde dál

    cache_key = f"rubrika:kids_curated:v4:{sort}"
    return _paginate_rubrika(cache_key, _ws_fetch, ui_page=page,
                             sort_mode=sort_mode)


def get_series(sort: str = "rating", page: int = 1) -> Tuple[List[Dict[str, Any]], bool]:
    """Seriály – vrací (items, has_more)."""
    log.debug("get_series(sort=%s, ui_page=%s)", sort, page)
    query = _addon_query("q_series", DEFAULT_QUERIES["series"])
    sort_mode = _read_sort("sort_series", SORT_SERIES)

    def _ws_fetch(ws_page: int):
        return _category_grouped("q_series", DEFAULT_QUERIES["series"],
                                 sort=sort, page=ws_page, mode="series")

    cache_key = f"rubrika:series:v2:{query}:{sort}"
    return _paginate_rubrika(cache_key, _ws_fetch, ui_page=page, sort_mode=sort_mode)


def get_series_new_dub(sort: str = "recent", page: int = 1) -> Tuple[List[Dict[str, Any]], bool]:
    """Nově dabované seriály – vrací (items, has_more)."""
    log.debug("get_series_new_dub(sort=%s, ui_page=%s)", sort, page)
    query = _addon_query("q_series_new_dub", DEFAULT_QUERIES["series_new_dub"])
    sort_mode = _read_sort("sort_series", SORT_SERIES)

    def _ws_fetch(ws_page: int):
        return _category_grouped("q_series_new_dub", DEFAULT_QUERIES["series_new_dub"],
                                 sort=sort, page=ws_page, mode="series")

    cache_key = f"rubrika:seriesnewdub:v2:{query}:{sort}"
    return _paginate_rubrika(cache_key, _ws_fetch, ui_page=page, sort_mode=sort_mode)


def _current_year() -> int:
    """Aktuální rok podle systémových hodin (pro Novinky multi-query)."""
    try:
        from datetime import datetime
        return datetime.now().year
    except Exception:  # noqa: BLE001
        return 2026  # bezpečný fallback


def _read_latest_min_year() -> Optional[int]:
    """
    Min. rok pro filtr v Novinkách. Default: aktuální rok - 1.
    Pokud user nastaví 0, filter se vypne (vše projde).
    """
    addon = _addon_safe()
    if addon is None:
        return _current_year() - 1
    raw = (addon.getSetting("latest_min_year") or "").strip()
    if not raw:
        return _current_year() - 1
    try:
        v = int(raw)
    except ValueError:
        return _current_year() - 1
    if v <= 0:
        return None  # filter vypnutý
    return v


def get_latest(
    sort: str = "recent",
    page: int = 1,
    query_override: Optional[str] = None,
    search_year: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], bool]:
    """
    Novinky – primárně letošní / loňské filmy (dabing + titulky).

    v0.0.99: query_override = volne hledani v rubrice (jako 4K).
    """
    log.debug("get_latest(sort=%s, ui_page=%s, override=%r)", sort, page, query_override)
    if query_override:
        q = query_override.strip()
        queries = _build_rubric_search_queries(
            q, search_year,
            extras=[f"{q} CZ", f"{q} titulky", f"{q} dabing"])
        cache_key = f"rubrika:latest:search:v5:{q.lower()}:y{search_year or 0}"
        return _paginate_multi_query(
            cache_key=cache_key,
            queries=queries,
            ui_page=page,
            pre_filter=None,
            post_filter=None,
            sort_key=lambda it: _search_sort_key(it, want_year=search_year),
            max_ws_pages=12,
            skip_enrich=True,
            skip_poster=True,
            skip_aggressive_filters=True,
            trust_ws_query=True,
        )

    user_query = _addon_query("q_latest", DEFAULT_QUERIES["latest"])
    cy = _current_year()
    py = cy - 1

    # Rotace dotazu na Webshare (NE TMDB) - rok / CZ / dabing / titulky.
    queries = [
        str(cy),
        f"{cy} CZ",
        f"{cy} dabing",
        f"{cy} CZ dabing",
        f"{cy} titulky",
        f"{cy} CZ titulky",
        str(py),
        f"{py} CZ",
        f"{py} dabing",
        f"{py} titulky",
        user_query,
    ]

    def _pre_filter(items):
        """720p+ + CZ dab / titulky / Dual (v0.0.103)."""
        items = _min_quality_filter(items, min_score=600)
        items = _dubbed_or_subs_filter(items)
        return items

    def _ws_fetch(ws_page: int):
        """
        ws_page mapuje na queries[i], kde i = (ws_page-1) % len(queries),
        WS stránka v rámci dané query = (ws_page-1) // len(queries) + 1.
        """
        idx = (ws_page - 1) % len(queries)
        q_ws_page = (ws_page - 1) // len(queries) + 1
        q = queries[idx]
        log.debug("get_latest: WS fetch q=%r q_page=%d (ws_page=%d)",
                  q, q_ws_page, ws_page)
        files = search_videos(query=q, sort="recent", page=q_ws_page)
        if files is None or len(files) == 0:
            return [] if ws_page < len(queries) * 3 else None
        files = _exclude_series(files)
        # v0.0.128: skip_csfd=True - bez ČSFD brzdy (jen TMDB plakaty)
        # v0.0.130: csfd_rescue=True - dopln plakaty ceskym titulum bez TMDB
        return _movies_from_groups(_group_by_title(files),
                                   pre_filter=_pre_filter,
                                   skip_csfd=True,
                                   csfd_rescue=True)

    def _filter_combined(items):
        min_y = _read_latest_min_year()
        if min_y is not None:
            kept = []
            for it in items:
                eff = _effective_release_year(it)
                if eff == 0 or eff >= min_y:
                    kept.append(it)
            items = kept
        return items

    cache_key = f"rubrika:latest:v9:{cy}:{user_query}"
    # v0.0.63: kratsi TTL 10 min (defaultne 30 min) - "Novinky" je
    # semanticky o cerstvosti, ne o rychlosti opetovneho otevreni.
    return _paginate_rubrika(
        cache_key,
        _ws_fetch,
        ui_page=page,
        post_filter=_filter_combined,
        sort_key_override=_recent_first_sort_key,
        max_ws_pages=6,  # v0.0.62: 8 -> 6 (rychlejsi first load na Xbox)
        ttl_override=10 * 60,  # 10 minut pro novinky
    )


# ---------------------------------------------------------------------------
# 5c) EPIZODY seriálu + QUALITY VARIANTY (pro picker při přehrávání)
# ---------------------------------------------------------------------------

def _collect_episodes_files(series_name: str,
                            max_pages: int = 5,
                            force_refresh: bool = False) -> List[Dict[str, Any]]:
    """
    Stáhne všechny dostupné epizody seriálu z Webshare (do max_pages WS stránek).
    Vrátí matching soubory už po klasifikaci kvality/dabingu.

    v0.0.68: kombinuje sort="rating" + sort="recent" pro lepší pokrytí
        premiérových (nedávno nahraných) dílů. "rating" sort favorizuje
        nejstaženější soubory - novinky (málo stažení) jsou na konci
        a v 5 stránkách na ně nedosáhneme. "recent" je vidi nahoře.

    Pokud první search nevrátí 0 výsledků, zkusíme zkrácenou query
    (prvních 2-3 slova) - některá jména jsou natolik specifická, že
    Webshare full-text na celé jméno nematchuje (např. "House of the Dragon"
    nenajde, ale "House Dragon" ano).

    CACHE 1h - klik na seriál pak nečeká na další WS roundtripy.
    force_refresh=True smaze cache a fetchne cerstvě.
    """
    if not series_name:
        return []

    cache_key = f"episodes_files:v6:{_norm_compare(series_name)}"
    if force_refresh:
        try:
            cache.cache_delete(cache_key)
            log.info("_collect_episodes_files(%r): force_refresh - cache smazana",
                     series_name)
        except Exception as exc:  # noqa: BLE001
            log.debug("cache_delete %s: %s", cache_key, exc)
    else:
        cached = cache.cache_get(cache_key, ttl=3600)
        if cached is not None:
            log.info("_collect_episodes_files(%r): cache HIT (%d souboru)",
                     series_name, len(cached))
            return list(cached)

    all_files: List[Dict[str, Any]] = []

    # Vytvoříme seznam queries k vyzkoušení:
    # 1) Plný název ("Stranger Things")
    # 2) Bez "The" prefix ("The Witcher" -> "Witcher")
    # 3) První dvě slova ("Star Wars Visions" -> "Star Wars")
    # 4) První slovo (poslední pokus)
    queries: List[str] = [series_name]

    no_the = re.sub(r"^the\s+", "", series_name, flags=re.I).strip()
    if no_the and no_the != series_name:
        queries.append(no_the)

    words = re.split(r"\s+", series_name.strip())
    if len(words) >= 3:
        queries.append(" ".join(words[:2]))
    if len(words) >= 2 and words[0] not in queries:
        queries.append(words[0])

    # v0.0.68: dva sort modes per query
    # - rating: nejstaženější varianty (staré dily v hi-quality)
    # - recent: nedávno nahrané (premierové dily co teprve vychazi)
    # Tahle kombinace pokryje sirsi spektrum nez puvodni jen "rating".
    sort_modes = ["rating", "recent"]

    seen_idents: set = set()
    for qi, q in enumerate(queries):
        if not q or len(q) < 2:
            continue
        new_in_this_query = 0
        for sort_mode in sort_modes:
            log.info("_collect_episodes_files: query[%d]=%r sort=%s (target=%r)",
                     qi, q, sort_mode, series_name)
            for p in range(1, max_pages + 1):
                files = search_videos(query=q, sort=sort_mode, page=p)
                if not files:
                    break
                added = 0
                for f in files:
                    name = f.get("name") or ""
                    s, e = _parse_episode(name, series_name)
                    if s is None or e is None:
                        continue
                    ident = f.get("ident") or ""
                    if ident in seen_idents:
                        continue
                    if _parse_se(name)[0] is not None:
                        detected = _series_name(name)
                    else:
                        detected = _ct.clean_title(
                            re.split(
                                r"epizod[a]?|d[ií]l|diel|č[aá]st|cast",
                                name,
                                maxsplit=1,
                                flags=re.I,
                            )[0],
                        ) or _series_name(name)
                    if not _series_title_match_for_episodes(series_name, detected):
                        continue
                    all_files.append(f)
                    seen_idents.add(ident)
                    added += 1
                    new_in_this_query += 1
                if added == 0 and p > 1:
                    break
        log.info("_collect_episodes_files: query[%d]=%r -> %d novych souboru",
                 qi, q, new_in_this_query)
        # Pokud první query (= plný název) nevrátil nic, zkusíme další.
        # Pokud první VRÁTIL něco, ale ne moc, taky pokračujeme - širší query
        # může přinést další ripy se stejnou epizodou v jiné kvalitě.
        if qi == 0 and len(all_files) >= 30:
            # Máme dost - nemusíme zkoušet další (širší) queries.
            break

    log.info("_collect_episodes_files(%r): celkem %d souboru po vsech queries",
             series_name, len(all_files))

    classify_files(all_files)
    if all_files:
        cache.cache_set(cache_key, all_files)
    else:
        try:
            cache.cache_delete(cache_key)
        except Exception as exc:  # noqa: BLE001
            log.debug("cache_delete empty %s: %s", cache_key, exc)
    return all_files


def _parse_se(name: str) -> Tuple[Optional[int], Optional[int]]:
    """Z názvu vytáhne (season, episode) jako int, nebo (None, None)."""
    m = _SERIES_PATTERN.search(name or "")
    if not m:
        return None, None
    raw = m.group(0).upper()
    m2 = re.match(r"S(\d+)\s*[EX]\s*(\d+)", raw, re.I)
    if not m2:
        return None, None
    return int(m2.group(1)), int(m2.group(2))


# v0.0.119: Voyo / reality uploady bez SxxEyy ("Ruza pre nevestu epizoda 1").
# v0.0.120: SK "1. diel", "5 dil", cislo pred/po markerem.
_ALT_EP_PATTERNS = (
    re.compile(r"epizod[a]?[\W_]*(\d{1,2})", re.I),
    re.compile(r"(\d{1,2})[\W_]*epizod[a]?", re.I),
    re.compile(r"d[ií]l[\W_]*(\d{1,2})", re.I),
    re.compile(r"(\d{1,2})[\W_.]*d[ií]l", re.I),
    re.compile(r"diel[\W_]*(\d{1,2})", re.I),
    re.compile(r"(\d{1,2})[\W_.]*diel", re.I),
    re.compile(r"č[aá]st[\W_]*(\d{1,2})", re.I),
    re.compile(r"(\d{1,2})[\W_.]*č[aá]st", re.I),
    re.compile(r"cast[\W_]*(\d{1,2})", re.I),
    re.compile(r"(\d{1,2})[\W_.]*cast", re.I),
)


def _parse_episode_alt(
    name: str,
    series_name: str,
) -> Tuple[Optional[int], Optional[int]]:
    """Epizoda z epizoda/díl/část nebo trailing čísla — jen po shodě názvu seriálu."""
    if not name or not series_name:
        return None, None
    for pat in _ALT_EP_PATTERNS:
        m = pat.search(name)
        if not m:
            continue
        try:
            ep = int(m.group(1))
        except (TypeError, ValueError):
            continue
        if not (1 <= ep <= 99):
            continue
        prefix = _ct.clean_title(name[:m.start()])
        if prefix and _series_title_match_for_episodes(series_name, prefix):
            return 1, ep
    cleaned = _ct.clean_title(name)
    m = _TRAILING_NUM_RE.match(cleaned)
    if m:
        try:
            ep = int(m.group(2))
        except (TypeError, ValueError):
            return None, None
        prefix = m.group(1).strip()
        if 1 <= ep <= 99 and _series_title_match_for_episodes(series_name, prefix):
            return 1, ep
    return None, None


def _parse_episode(
    name: str,
    series_name: Optional[str] = None,
) -> Tuple[Optional[int], Optional[int]]:
    """SxxEyy nebo alt parser (epizoda/díl/část) s validací proti series_name."""
    s, e = _parse_se(name)
    if s is not None:
        return s, e
    if series_name:
        return _parse_episode_alt(name, series_name)
    return None, None


def _episode_base_for_series(
    series_name: str,
    season: int,
    episode: int,
    filename: str,
) -> str:
    """Stabilní base_title pro play_pick — SxxEyy z názvu nebo syntetický."""
    if _parse_se(filename)[0] is not None:
        return _episode_base_title(filename)
    return f"{series_name.strip()} S{int(season):02d}E{int(episode):02d}"


def get_series_seasons(series_name: str,
                       force_refresh: bool = False) -> Dict[str, Any]:
    """
    Vrátí seznam sezón konkrétního seriálu - dostupné z Webshare +
    enrichnuté z TMDB (poster, popis, počet TMDB epizod).

    CACHE 6h - klik na seriál je pak instant.

    v0.0.68: force_refresh=True smaze cache_key + cache pro
        _collect_episodes_files + per-season episode cache.
        Pouziva se z "Aktualizovat" tlacitka v series UI.

    Returns:
        {
          "tmdb_id": int | None,
          "title_localized": str,
          "poster": str,
          "fanart": str,
          "plot": str,
          "seasons": [
            {
              "season_number": int,
              "name": str,           # ze TMDB nebo "Sezóna X"
              "overview": str,
              "ws_episode_count": int,  # kolik epizod máme na WS
              "tmdb_episode_count": int,
              "poster": str,
              "air_date": str,
            }
          ]
        }
    """
    if not series_name:
        return {"seasons": []}

    seasons_cache_key = f"series_seasons:{_norm_compare(series_name)}"
    if force_refresh:
        # Smaze seasons cache + vsechny per-season eps caches pro tento seriál.
        try:
            cache.cache_delete(seasons_cache_key)
            # series_eps:{norm}:s1, s2, ... + sNone (flat fallback)
            cache.cache_clear_prefix(
                f"series_eps:{_norm_compare(series_name)}:")
        except Exception as exc:  # noqa: BLE001
            log.debug("cache cleanup %s: %s", seasons_cache_key, exc)
    else:
        cached = cache.cache_get(seasons_cache_key, ttl=6 * 3600)
        if cached is not None:
            log.info("get_series_seasons(%r): cache HIT", series_name)
            return cached

    # 1) WS - posbíráme všechny epizody a zjistíme jaké sezóny máme
    files = _collect_episodes_files(series_name, force_refresh=force_refresh)
    ws_season_counts: Dict[int, int] = {}
    ws_episodes_per_season: Dict[int, set] = {}
    for f in files:
        s, e = _parse_episode(f.get("name") or "", series_name)
        if s is None:
            continue
        ws_episodes_per_season.setdefault(s, set()).add(e)
    for s, eps in ws_episodes_per_season.items():
        ws_season_counts[s] = len(eps)

    # 2) TMDB - najdi seriál a dotáhni seasons
    tmdb_id = None
    tmdb_title = series_name
    tmdb_poster = ""
    tmdb_fanart = ""
    tmdb_plot = ""
    tmdb_seasons: List[Dict[str, Any]] = []
    try:
        from . import tmdb_tv_api
        meta = tmdb_tv_api.tmdb_lookup_tv_first(series_name)
        if meta:
            tmdb_id = meta.get("tmdb_id")
            tmdb_title = meta.get("title") or series_name
            tmdb_poster = meta.get("poster") or ""
            tmdb_fanart = meta.get("fanart") or ""
            tmdb_plot = meta.get("plot") or ""
        if tmdb_id:
            tmdb_seasons = tmdb_tv_api.get_seasons(tmdb_id)
    except Exception as exc:  # noqa: BLE001
        log.debug("get_series_seasons: TMDB lookup selhal: %s", exc)

    # 3) Sloučit - sezóny, které máme na WS, plus TMDB metadata pokud existují
    tmdb_seasons_map = {int(s.get("season_number")): s for s in tmdb_seasons}

    seasons_out: List[Dict[str, Any]] = []
    all_season_nums = sorted(set(list(ws_season_counts.keys()) +
                                 list(tmdb_seasons_map.keys())))
    for num in all_season_nums:
        if num == 0:
            continue
        ws_count = ws_season_counts.get(num, 0)
        tm = tmdb_seasons_map.get(num) or {}
        # Přeskočit jen pokud nemáme z TMDB ani WS opravdu NIC.
        # (Dřív jsme přeskakovali i ws_count==0, takže pokud Webshare nedal
        # nic, neviděl user ŽÁDNÉ sezóny - ani ty co TMDB znalo.)
        if ws_count == 0 and not tm:
            continue
        seasons_out.append({
            "season_number":      num,
            "name":               tm.get("name") or f"Sezóna {num}",
            "overview":           tm.get("overview") or "",
            "ws_episode_count":   ws_count,
            "tmdb_episode_count": int(tm.get("episode_count") or 0),
            "poster":             tm.get("poster") or tmdb_poster,
            "air_date":           tm.get("air_date") or "",
        })

    log.info("get_series_seasons(%r): %d sezon (TMDB=%s, WS=%d souboru)",
             series_name, len(seasons_out), bool(tmdb_id), len(files))

    result = {
        "tmdb_id":          tmdb_id,
        "title_localized":  tmdb_title,
        "poster":           tmdb_poster,
        "fanart":           tmdb_fanart,
        "plot":             tmdb_plot,
        "seasons":          seasons_out,
    }
    cache.cache_set(seasons_cache_key, result)
    return result


def get_series_episodes(series_name: str,
                         season: Optional[int] = None,
                         page: int = 1,
                         force_refresh: bool = False) -> List[Dict[str, Any]]:
    """
    Vrátí epizody konkrétního seriálu (volitelně jen jedné sezóny),
    seskupené podle SxxEyy. Při dostupném TMDB ID se k epizodám doplní
    název, plot, screenshot a air date přes /tv/{id}/season/{n}.

    :param season: pokud zadáno (int), vrátí jen epizody dané sezóny
                   (vhodné po klikknutí na S01 ve season folder).
                   None = všechny epizody (legacy chování).
    :param force_refresh: smaze cache + forcne fresh fetch z Webshare.
    """
    if not series_name:
        return []

    # CACHE 30 min - klik na sezónu pak nečeká na enrich epizod znovu
    ep_cache_key = f"series_eps:{_norm_compare(series_name)}:s{season}"
    if force_refresh:
        try:
            cache.cache_delete(ep_cache_key)
        except Exception as exc:  # noqa: BLE001
            log.debug("cache_delete %s: %s", ep_cache_key, exc)
    else:
        cached = cache.cache_get(ep_cache_key, ttl=30 * 60)
        if cached is not None:
            log.info("get_series_episodes(%r, season=%s): cache HIT (%d)",
                     series_name, season, len(cached))
            return list(cached)

    files = _collect_episodes_files(series_name, force_refresh=force_refresh)
    if not files:
        return []

    # Filter sezóny pokud requested
    if season is not None:
        season_int = int(season)
        files = [f for f in files
                 if _parse_episode(f.get("name") or "", series_name)[0] == season_int]

    # Seskup podle SxxEyy klíče
    by_ep: Dict[str, List[Dict[str, Any]]] = {}
    for f in files:
        s, e = _parse_episode(f.get("name") or "", series_name)
        if s is None or e is None:
            continue
        ep_key = f"S{s:02d}E{e:02d}"
        by_ep.setdefault(ep_key, []).append(f)

    # TMDB lookup seriálu pro enrichment epizod
    tmdb_id = None
    try:
        from . import tmdb_tv_api
        meta = tmdb_tv_api.tmdb_lookup_tv_first(series_name)
        if meta:
            tmdb_id = meta.get("tmdb_id")
    except Exception as exc:  # noqa: BLE001
        log.debug("get_series_episodes: TMDB lookup selhal: %s", exc)

    items: List[Dict[str, Any]] = []
    for ep_key in sorted(by_ep.keys()):
        fs = by_ep[ep_key]
        variants = _files_to_variant_refs(fs)
        if not variants:
            continue
        best = variants[0]
        is_dubbed = any(_detect_dubbed(v.get("name") or "") for v in variants)
        fname = best.get("name") or ""
        s_num, e_num = _parse_episode(fname, series_name)
        base = _episode_base_for_series(series_name, s_num or 1, e_num or 1, fname)

        _save_variants_cache(base, "episode", variants)

        item = {
            "id": "",
            "title": ep_key,
            "title_raw": ep_key,
            "year": None,
            "plot": f"{series_name} - {ep_key}. Dostupné varianty: {len(variants)}.",
            "poster": "",
            "fanart": "",
            "type": "episode",
            "dubbed": is_dubbed,
            "base_title": base,
            "variant_idents": [v["ident"] for v in variants],
            "variants_count": len(variants),
            "badges": detect_badges(best.get("name") or ""),
            "episode_key": ep_key,
            "season_number":  s_num,
            "episode_number": e_num,
        }

        # Enrich z TMDB
        if tmdb_id and s_num is not None and e_num is not None:
            try:
                from . import tmdb_tv_api
                tmdb_tv_api.enrich_episode(item, tmdb_id, s_num, e_num)
                if item.get("episode_title"):
                    # Zobrazit "S01E02 - Jméno epizody"
                    item["title"] = f"{ep_key} - {item['episode_title']}"
            except Exception as exc:  # noqa: BLE001
                log.debug("episode enrich %s selhal: %s", ep_key, exc)

        items.append(item)

    cache.cache_set(ep_cache_key, items)
    return items


# v0.0.63: prah pro "thin cache" - kdyz cache ma <= TENHLE_pocet variant,
# pri play_pick automaticky pridame re-search na Webshare. Driv tohle nebylo:
# rubrika "Novy dabing" matchla jen 1 soubor "CZ dabing" -> picker se
# nikdy nezobrazil (len==1 -> auto-play). User chtel videt i ostatni
# varianty (BluRay/WEB-DL/4K) at vybira mezi kvalitou a dabingem.
_VARIANTS_CACHE_MIN_EXPAND = 8


def _fetch_ws_files_for_title(
    base_title: str,
    max_pages: int = 2,
    year: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """v0.0.104: vice WS dotazu + stranek pro quality picker (Michael ~46 variant)."""
    clean = (_normalize_title(base_title) or base_title or "").strip()
    if not clean:
        return []
    if year is None:
        year = _guess_year(base_title) or _guess_year(clean)
    if not year:
        try:
            year = _ct.extract_year(base_title)
        except Exception:  # noqa: BLE001
            year = None
    queries = _search_alt_queries(clean, year=year)
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for q in queries[:8]:
        for page in range(1, max_pages + 1):
            files = search_videos(query=q, sort="rating", page=page)
            if not files:
                break
            for f in files:
                ident = f.get("ident") or f.get("id") or ""
                if ident and ident not in seen:
                    seen.add(ident)
                    out.append(f)
    return out


def get_quality_variants(
    base_title: str,
    mode: str = "movie",
    cz_only: bool = False,
    year: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Pro daný base_title vrátí všechny varianty (různé kvality).

    Strategie (v0.0.63 EXPANDED):
        1) CACHE-FIRST - varianty se ukládají při zobrazení listing
           (_movies_from_groups / _series_from_groups), takže klik
           na film z UI rovnou zná všechny ident/name/size bez nutnosti
           re-searche. Cache TTL = 24h.
        2) THIN-CACHE EXPAND (NOVE v0.0.63): pokud cache ma jen 1-2
           varianty (typicky kdyz rubrika hledala filtrovanym dotazem
           jako "2024 CZ dabing"), spustime DOPLNUJICI re-search na
           Webshare a vysledky mergeme. Tim user dostane VSECHNY
           dostupne varianty (CZ+EN, vsech kvalit) pri picker dialogu.
        3) Cache miss → full re-search (drivejsi chovani).

    :param mode: 'movie' / 'series' (klíč v cache) nebo 'episode'
                 (re-search podle SxxEyy).
    :param cz_only: True = jen CZ dab/tit/Dual (rubrika Novinky dabovane).
    :param year: TMDB/WS rok titulu - filtruje jine filmy se stejnym nazvem.
    """
    def _finalize(variants: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = variants
        if year and out:
            out = filter_variants_by_year(out, year)
            log.info("get_quality_variants(base=%r): year=%s -> %d variant",
                     base_title, year, len(out))
        if cz_only and out:
            out = filter_cz_variants(out)
            log.info("get_quality_variants(base=%r): cz_only -> %d variant",
                     base_title, len(out))
        return out

    if not base_title:
        log.warning("get_quality_variants: prazdny base_title")
        return []

    # 1) Cache lookup pro všechny módy (movie/series/episode).
    cached = _load_variants_cache(base_title, mode=mode, ttl=24 * 3600)

    # 2) Expand: pokud cache ma malo variant, doplnime plny WS re-search.
    if cached and len(cached) >= _VARIANTS_CACHE_MIN_EXPAND:
        log.info("get_quality_variants(base=%r, mode=%s): cache HIT (%d variant) "
                 "- dostatecne, neprovadime expand",
                 base_title, mode, len(cached))
        return _finalize(cached)

    if cached:
        log.info("get_quality_variants(base=%r, mode=%s): cache HIT (%d variant) "
                 "- malo, doplnujem re-searchem",
                 base_title, mode, len(cached))

    # 3) Re-search na Webshare (full miss nebo thin expand).
    # Pro epizodu: hledat 'Series Name SxxEyy' může vrátit málo výsledků
    # (Webshare full-text neumí dobře s tečkami/mezerami). Zkusíme proto
    # raději hledat jen 'Series Name' (= víc kandidátů) a pak je filtrujeme
    # lokálně podle SxxEyy markeru a porovnání base_title.
    target = _norm_compare(base_title)
    episode_series = ""
    episode_se: Optional[Tuple[int, int]] = None

    if mode == "episode":
        m = re.search(r"(.+?)\s+S(\d{1,2})\s*[EX]\s*(\d{1,3})", base_title, re.I)
        if m:
            episode_series = m.group(1).strip()
            episode_se = (int(m.group(2)), int(m.group(3)))
            log.info("get_quality_variants(episode): query='%s' (z base=%r)",
                     episode_series, base_title)
            files = search_videos(query=episode_series, sort="rating", page=1)
        else:
            episode_series = _series_name(base_title) or base_title
            files = search_videos(query=base_title, sort="rating", page=1)
    else:
        files = _fetch_ws_files_for_title(base_title, year=year)

    if not files:
        if cached:
            # Re-search nepomohol, vratime alespon stary cache.
            log.info("get_quality_variants(base=%r): expand re-search 0 souboru, "
                     "vracim %d cached", base_title, len(cached))
            return _finalize(cached)
        log.warning("get_quality_variants(base=%r, mode=%s): re-search vratil 0 souboru",
                    base_title, mode)
        return []

    matching: List[Dict[str, Any]] = []
    for f in files:
        name = f.get("name") or ""
        if mode == "episode":
            if not episode_series:
                episode_series = _series_name(base_title) or base_title
            s, e = _parse_episode(name, episode_series)
            if s is None or e is None:
                continue
            if episode_se is not None and (s, e) != episode_se:
                continue
            matching.append(f)
        else:
            if mode == "series":
                candidate = _series_name(name)
            else:
                candidate = _normalize_title(name)
            cand_norm = _norm_compare(candidate)
            if not cand_norm:
                continue
            if cand_norm == target or _title_tokens_match(base_title, candidate):
                if _variant_matches_year(name, year):
                    matching.append(f)

    fresh_variants = _files_to_variant_refs(matching)

    # v0.0.63: merge fresh + cached. Dedup po ident.
    # Cached prijde druhe = ma nizsi prioritu, ale ne ztrati se kdyz
    # re-search to v ws_files uz nevratil (napr. Webshare ranking se
    # zmenil mezi listing a klikem).
    if cached:
        seen_idents = {v.get("ident") for v in fresh_variants if v.get("ident")}
        for c in cached:
            cid = c.get("ident") or ""
            if cid and cid not in seen_idents:
                fresh_variants.append(c)
                seen_idents.add(cid)
        # Re-sort: nejvyssi kvalita prvni (po merge se poradi rozbije).
        fresh_variants.sort(
            key=lambda v: _quality_score(v.get("name") or ""), reverse=True)

    log.info("get_quality_variants(base=%r, mode=%s): re-search -> %d souboru "
             "-> %d matching -> %d final variant (cached bylo %d)",
             base_title, mode, len(files), len(matching),
             len(fresh_variants), len(cached) if cached else 0)

    if fresh_variants:
        _save_variants_cache(base_title, mode, fresh_variants)

    return _finalize(fresh_variants)


# ---------------------------------------------------------------------------
# 5c) TMDB discover -> Webshare filter (v0.0.82)
# ---------------------------------------------------------------------------

TMDB_WS_FILTER_WORKERS = 6
TMDB_WS_FILTER_MAX_WAIT = 8


def _ws_files_for_tmdb_title(title: str, year: Optional[int] = None) -> List[Dict[str, Any]]:
    """Rychly WS search pro TMDB titul - vrati matching movie soubory."""
    if not title or _shutdown.is_shutting_down():
        return []
    queries = [title]
    if year:
        queries.append(f"{title} {year}")
    all_files: List[Dict[str, Any]] = []
    seen: set = set()
    for q in queries:
        files = search_videos(query=q, sort="rating", page=1)
        if not files:
            continue
        for f in files:
            ident = f.get("ident") or f.get("id") or ""
            if ident and ident not in seen:
                seen.add(ident)
                all_files.append(f)
    if not all_files:
        return []
    all_files = _exclude_series(all_files)
    if not all_files:
        return []
    target = _norm_compare(title)
    matching: List[Dict[str, Any]] = []
    for f in all_files:
        cand = _normalize_title(f.get("name") or "")
        cand_norm = _norm_compare(cand)
        if not cand_norm:
            continue
        if cand_norm == target or _title_tokens_match(title, cand):
            if year and str(year) not in (f.get("name") or ""):
                gy = _guess_year(f.get("name") or "")
                if gy and int(gy) != int(year):
                    continue
            matching.append(f)
    return matching


def tmdb_movie_meta_to_ws_item(meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    v0.0.82: Pokud TMDB film existuje na Webshare, vrat play-ready item
    s variant_idents + TMDB plakatem. Jinak None (nezobrazovat).
    """
    title = (meta.get("title") or meta.get("original") or "").strip()
    if not title:
        return None
    year_raw = meta.get("year")
    try:
        year = int(year_raw) if year_raw else None
    except (TypeError, ValueError):
        year = None

    matching = _ws_files_for_tmdb_title(title, year=year)
    if not matching:
        return None

    classify_files(matching)
    variants = _files_to_variant_refs(matching)
    if not variants:
        return None

    base_title = _normalize_title(variants[0].get("name") or title)
    _save_variants_cache(base_title, "movie", variants)

    best = variants[0]
    is_dubbed = any(_detect_dubbed(v.get("name") or "") for v in variants)
    has_subs = any(_detect_subtitles(v.get("name") or "") for v in variants)
    ws_thumb = ""
    for v in variants:
        img = (v.get("img") or "").strip()
        if img.startswith(("http://", "https://")):
            ws_thumb = img
            break

    poster = meta.get("poster") or ws_thumb or None
    fanart = meta.get("fanart") or poster

    item = {
        "id": "",
        "title": title,
        "title_localized": title,
        "original_title": meta.get("original") or "",
        "year": year or _guess_year(best.get("name") or ""),
        "plot": meta.get("plot") or "",
        "poster": poster,
        "fanart": fanart,
        "type": "movie",
        "dubbed": is_dubbed,
        "subs_cz": has_subs,
        "base_title": base_title,
        "variant_idents": [v["ident"] for v in variants],
        "variants_count": len(variants),
        "quality_score": max((_quality_score(v.get("name") or "") for v in variants),
                             default=0),
        "tmdb_id": meta.get("tmdb_id"),
        "rating": float(meta.get("rating") or 0),
        "votes": int(meta.get("votes") or 0),
        "popularity": float(meta.get("popularity") or 0),
    }
    gids = list(meta.get("genre_ids") or [])
    if gids:
        from . import tmdb as _tmdb
        item["genre_ids"] = gids
        item["genre_names"] = _tmdb.genre_names_for_ids(gids, "movie")
    if meta.get("_extra_plot"):
        extra = str(meta["_extra_plot"]).strip()
        if extra:
            item["plot"] = extra + (
                ("\n" + item["plot"]) if item.get("plot") else "")
    return item


def tmdb_series_meta_to_ws_item(meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """v0.0.82: Seriál z TMDB jen pokud existuje alespoň 1 epizoda na WS."""
    title = (meta.get("title") or meta.get("original") or "").strip()
    if not title:
        return None
    queries = [title]
    folded = _ct.ascii_fold(title)
    if folded.lower() != title.lower():
        queries.append(folded)
    slug = (meta.get("voyo_slug") or "").strip()
    if slug and slug.lower() not in [q.lower() for q in queries]:
        queries.append(slug.replace("-", " "))

    best_fs: Optional[List[Dict[str, Any]]] = None
    for q in queries:
        files = search_videos(query=q, sort="rating", page=1)
        if not files:
            continue
        groups = _group_by_series(files)
        for sname, fs in groups.items():
            if not _series_title_match(title, sname):
                continue
            if best_fs is None or len(fs) > len(best_fs):
                best_fs = fs
        if best_fs:
            break

    if not best_fs:
        return None

    variants = _files_to_variant_refs(best_fs)
    if not variants:
        return None
    _save_variants_cache(title, "series", variants)
    item = {
        "id": "",
        "title": title,
        "title_localized": title,
        "year": meta.get("year"),
        "plot": meta.get("plot") or "",
        "poster": meta.get("poster") or None,
        "fanart": meta.get("fanart") or meta.get("poster"),
        "type": "series",
        "series_name": title,
        "variant_idents": [v["ident"] for v in variants],
        "variants_count": len(variants),
        "tmdb_id": meta.get("tmdb_id"),
        "rating": float(meta.get("rating") or 0),
        "votes": int(meta.get("votes") or 0),
        "popularity": float(meta.get("popularity") or 0),
    }
    gids = list(meta.get("genre_ids") or [])
    if gids:
        from . import tmdb as _tmdb
        item["genre_ids"] = gids
        item["genre_names"] = _tmdb.genre_names_for_ids(gids, "tv")
    if meta.get("_extra_plot"):
        extra = str(meta["_extra_plot"]).strip()
        if extra:
            item["plot"] = extra + (
                ("\n" + item["plot"]) if item.get("plot") else "")
    return item


def filter_tmdb_movies_on_webshare(
    metas: List[Dict[str, Any]],
    max_workers: int = TMDB_WS_FILTER_WORKERS,
    max_wait: float = TMDB_WS_FILTER_MAX_WAIT,
) -> List[Dict[str, Any]]:
    """Z TMDB seznamu vrati jen filmy dostupne na Webshare."""
    if not metas:
        return []
    results: List[Dict[str, Any]] = []
    pool = ThreadPoolExecutor(max_workers=max_workers)
    try:
        futures = [pool.submit(tmdb_movie_meta_to_ws_item, m) for m in metas]
        pending = set(futures)
        budget = float(max_wait)
        while pending and budget > 0 and not _shutdown.is_shutting_down():
            done, pending = wait(pending, timeout=min(0.5, budget))
            budget -= 0.5
            for fut in done:
                try:
                    item = fut.result()
                    if item:
                        results.append(item)
                except Exception as exc:  # noqa: BLE001
                    log.debug("tmdb_movie_meta_to_ws_item selhal: %s", exc)
    finally:
        _shutdown_pool(pool)  # v0.0.136: neblokovat vypinani Kodi
    log.info("filter_tmdb_movies_on_webshare: %d TMDB -> %d na WS",
             len(metas), len(results))
    return results


def filter_tmdb_series_on_webshare(
    metas: List[Dict[str, Any]],
    max_workers: int = TMDB_WS_FILTER_WORKERS,
    max_wait: float = TMDB_WS_FILTER_MAX_WAIT,
) -> List[Dict[str, Any]]:
    """Z TMDB seznamu vrati jen serialy dostupne na Webshare."""
    if not metas:
        return []
    results: List[Dict[str, Any]] = []
    pool = ThreadPoolExecutor(max_workers=max_workers)
    try:
        futures = [pool.submit(tmdb_series_meta_to_ws_item, m) for m in metas]
        pending = set(futures)
        budget = float(max_wait)
        while pending and budget > 0 and not _shutdown.is_shutting_down():
            done, pending = wait(pending, timeout=min(0.5, budget))
            budget -= 0.5
            for fut in done:
                try:
                    item = fut.result()
                    if item:
                        results.append(item)
                except Exception as exc:  # noqa: BLE001
                    log.debug("tmdb_series_meta_to_ws_item selhal: %s", exc)
    finally:
        _shutdown_pool(pool)  # v0.0.136: neblokovat vypinani Kodi
    log.info("filter_tmdb_series_on_webshare: %d TMDB -> %d na WS",
             len(metas), len(results))
    return results


def filter_discovery_titles_on_webshare(
    entries: List[Dict[str, Any]],
    kind: str = "movie",
    max_workers: int = TMDB_WS_FILTER_WORKERS,
    max_wait: float = TMDB_WS_FILTER_MAX_WAIT,
) -> List[Dict[str, Any]]:
    """
    v0.0.83: Obecny WS filtr pro discovery zdroje (Voyo, TV program, ...).
    Vrati jen polozky s WS soubory + variant_idents.

    :param entries: [{"title": str, "year": int|None, "poster": str, ...}]
    :param kind: "movie" nebo "series"
    """
    if not entries:
        return []
    if kind == "series":
        return filter_tmdb_series_on_webshare(entries, max_workers, max_wait)
    return filter_tmdb_movies_on_webshare(entries, max_workers, max_wait)


# ---------------------------------------------------------------------------
# 6) HLEDÁNÍ – uživatelský dotaz
# ---------------------------------------------------------------------------

def _build_rubric_search_queries(
    q: str,
    search_year: Optional[int] = None,
    extras: Optional[List[str]] = None,
) -> List[str]:
    """
    v0.0.100: Sestavi WS dotazy pro hledani v rubrice.

    U kratkych nazvu (Michael) davame rok NA PRVNI MISTO - bare query
    jinak zaplavi vysledky jinymi 'Michael' a pagination dedup je pak
  nevrati.
    """
    q = (q or "").strip()
    if not q:
        return []
    seen: set = set()
    out: List[str] = []

    def _add(s: str) -> None:
        k = re.sub(r"\s+", " ", (s or "").strip().lower())
        if k and k not in seen:
            seen.add(k)
            out.append(s.strip())

    cy = _current_year()
    if search_year:
        _add(f"{q} {search_year}")
        _add(f"{q} {search_year} CZ")
        _add(f"{q} {search_year} dabing")
    elif " " not in q and len(q) >= 3:
        _add(f"{q} {cy}")
        _add(f"{q} {cy} CZ")
        _add(f"{q} {cy} dabing")
        _add(f"{q} {cy - 1}")
    _add(q)
    if extras:
        for e in extras:
            _add(e)
    return out


def _file_matches_search_title(
    fname: str, query: str, year: Optional[int] = None,
) -> bool:
    """
    Shoda dotazu s nazvem souboru.

    v0.0.103: U WS vysledku akceptuj i cele slovo v raw nazvu (scene tagy
    v clean_title jinak rozbiji striktni token match).
    """
    q_tokens = _title_meaningful_tokens(query)
    if not q_tokens:
        return False
    norm = _normalize_title(fname)
    fname_l = (fname or "").lower()
    matched = False
    if norm:
        if len(q_tokens) == 1:
            if _title_tokens_match(query, norm):
                matched = True
            elif _title_meaningful_tokens(norm) == q_tokens:
                matched = True
        elif q_tokens.issubset(_title_meaningful_tokens(norm)):
            matched = True
    if not matched and len(q_tokens) == 1:
        qword = list(q_tokens)[0]
        if re.search(rf"(?<![\w]){re.escape(qword)}(?![\w])", fname_l):
            cand = _title_meaningful_tokens(norm) if norm else set()
            extra = cand - q_tokens
            if not extra or extra == q_tokens:
                matched = True
            elif len(extra) == 1 and list(extra)[0] not in (
                "jordan", "george", "jackson", "collins", "myers",
            ):
                matched = True
    if not matched:
        return False
    if year:
        fy = _guess_year(fname)
        ys = str(int(year))
        if fy and fy != int(year):
            return False
        if not fy and ys not in fname_l:
            return False
    return True


def _filter_files_for_search(
    files: List[Dict[str, Any]],
    query: str,
    year: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Nech jen WS soubory ktere odpovidaji uzivatelskemu dotazu."""
    q = (query or "").strip()
    if not q:
        return files
    out = [
        f for f in files
        if _file_matches_search_title(f.get("name") or "", q, year=year)
    ]
    log.debug("_filter_files_for_search(%r, year=%s): %d -> %d",
              q, year, len(files), len(out))
    return out


def _search_alt_queries(query: str, year: Optional[int] = None) -> List[str]:
    """
    Bilingvni vyhledavani: vrati seznam dotazu k odeslani na Webshare.

    v0.0.100: rokove varianty NA PRVNI MISTO (Michael 2026 pred bare Michael).
    """
    q = (query or "").strip()
    if not q:
        return []
    seen: set = set()
    out: List[str] = []

    def _add(s: str) -> None:
        k = re.sub(r"\s+", " ", (s or "").strip().lower())
        if k and k not in seen:
            seen.add(k)
            out.append(s.strip())

    cy = _current_year()
    if year:
        _add(f"{q} {year}")
        _add(f"{q} {year} CZ")
        _add(f"{q} {year} dabing")
    elif " " not in q and len(q) >= 3:
        _add(f"{q} {cy}")
        _add(f"{q} {cy} CZ")
        _add(f"{q} {cy} dabing")
        _add(f"{q} {cy - 1}")
    _add(q)
    try:
        from . import tmdb
        meta = tmdb.search_movie(q, year=year)
        if meta:
            orig = (meta.get("original_title") or "").strip()
            loc = (meta.get("title_localized")
                   or meta.get("title") or "").strip()
            if orig:
                if year:
                    _add(f"{orig} {year}")
                _add(orig)
            if loc and loc.lower() != (orig or "").lower():
                if year:
                    _add(f"{loc} {year}")
                _add(loc)
    except Exception as exc:  # noqa: BLE001
        log.debug("_search_alt_queries TMDB lookup selhal: %s", exc)
    log.debug("_search_alt_queries(%r, year=%s) -> %s", query, year, out)
    return out


def _filter_search_by_year(
    items: List[Dict[str, Any]], year: Optional[int],
) -> List[Dict[str, Any]]:
    """v0.0.102: Pri hledani s rokem jen shodny rok (z metadat nebo nazvu)."""
    if not year or not items:
        return items
    want = int(year)
    kept: List[Dict[str, Any]] = []
    for it in items:
        eff = _effective_release_year(it)
        names = " ".join([
            it.get("ws_names") or "",
            it.get("base_title") or "",
            it.get("title") or "",
        ]).lower()
        if eff == want or str(want) in names:
            kept.append(it)
        else:
            log.debug(
                "search year_filter: skip %r (eff_year=%d, want=%d)",
                it.get("base_title") or it.get("title"), eff, want,
            )
    return kept


def search(
    query: str,
    page: int = 1,
    year: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], bool]:
    """
    Fulltextové vyhledávání – vrací (items, has_more).

    v0.0.98: volitelny rok z klavesnice (Michael 2026) – presnejsi WS dotazy
    a filtr starych verzi stejneho nazvu.
    """
    log.debug("search(query=%r, year=%s, ui_page=%s)", query, year, page)
    if not query or not query.strip():
        return [], False

    queries = _search_alt_queries(query, year=year)

    def _ws_fetch(ws_page: int):
        idx = (ws_page - 1) % len(queries)
        q_ws_page = (ws_page - 1) // len(queries) + 1
        q = queries[idx]
        log.debug("search: WS q=%r q_page=%d (ws_page=%d)",
                  q, q_ws_page, ws_page)
        raw = search_videos(q, sort="recent", page=q_ws_page)
        if raw is None:
            return None
        if len(raw) == 0:
            return [] if ws_page < len(queries) * 2 else None

        movies_files: List[Dict[str, Any]] = []
        series_files: List[Dict[str, Any]] = []
        for f in raw:
            if _is_series_file(f.get("name") or ""):
                series_files.append(f)
            elif _file_matches_search_title(f.get("name") or "", query, year=year):
                movies_files.append(f)

        movie_items = (
            _movies_from_groups(_group_by_title(movies_files),
                                skip_aggressive_filters=True,
                                skip_poster=True)
            if movies_files else []
        )
        series_items = (
            _series_from_groups(_group_by_series(series_files))
            if series_files else []
        )
        items = movie_items + series_items
        if year:
            items = _filter_search_by_year(items, year)
        return items

    norm = re.sub(r"\s+", " ", (query or "").strip().lower())
    cache_key = f"rubrika:search:v8:{norm}:y{year or 0}"

    max_ws = 8 if year or len(query.split()) == 1 else 3
    sort_key = lambda it: _search_sort_key(it, want_year=year)
    return _paginate_rubrika(
        cache_key, _ws_fetch, ui_page=page,
        poster_first=False,
        sort_key_override=sort_key,
        max_ws_pages=max_ws,
    )


# ---------------------------------------------------------------------------
# 6b) MOJE SOUBORY – uživatelská knihovna (file_list, pro VIP)
# ---------------------------------------------------------------------------

def get_my_files(sort: str = "recent", page: int = 1) -> Tuple[List[Dict[str, Any]], bool]:
    """Moje soubory – vrací (items, has_more)."""
    log.debug("get_my_files(ui_page=%s)", page)
    token = get_token()
    if not token:
        return [], False

    def _ws_fetch(ws_page: int):
        raw = fetch_files(token, page=ws_page)
        if raw is None or len(raw) == 0:
            return None

        movies_files: List[Dict[str, Any]] = []
        series_files: List[Dict[str, Any]] = []
        for f in raw:
            if _is_series_file(f.get("name") or ""):
                series_files.append(f)
            else:
                movies_files.append(f)

        movie_items = _movies_from_groups(_group_by_title(movies_files)) if movies_files else []
        series_items = _series_from_groups(_group_by_series(series_files)) if series_files else []
        return movie_items + series_items

    cache_key = "rubrika:myfiles"
    return _paginate_rubrika(cache_key, _ws_fetch, ui_page=page)


# ---------------------------------------------------------------------------
# 7) STREAM URL
# ---------------------------------------------------------------------------

def get_stream_url(
    token: Optional[str],
    file_id: str,
    item_type: str = "movie",
    _retry: bool = True,
) -> str:
    """
    Vrátí přímý streamovací odkaz pro daný Webshare soubor.

        POST /api/file_link/  {"ident": file_id, "wst": token,
                               "download_type": "video_stream",
                               "device_uuid": "..."}
        -> <link>https://...</link>
    """
    if not file_id:
        return ""

    if not token:
        token = get_token()

    resp = _request(
        "POST",
        f"{WEBSHARE_API_BASE}/file_link/",
        data={
            "ident": file_id,
            "download_type": "video_stream",
            "device_uuid": "klempcinema",
            "wst": token or "",
        },
    )
    if resp is None or resp.status_code != 200:
        log.error("get_stream_url(): HTTP chyba (status=%s).",
                  getattr(resp, "status_code", "?"))
        return ""

    root = _parse_xml(resp.text)
    if not _check_status(root, "get_stream_url"):
        if _retry:
            _invalidate_token()
            new_token = get_token(force_refresh=True)
            if new_token:
                return get_stream_url(new_token, file_id, item_type, _retry=False)
        return ""

    return _xml_text(root, "link")


# ---------------------------------------------------------------------------
# 8) (Volitelné) Sezóny / epizody – pro budoucí rozšíření
# ---------------------------------------------------------------------------

def get_seasons(series_id: str) -> List[Dict[str, Any]]:
    """Tenký wrapper — seznam sezón přes get_series_seasons."""
    if not series_id:
        return []
    info = get_series_seasons(series_id)
    return list((info or {}).get("seasons") or [])


def get_episodes(series_id: str, season: int) -> List[Dict[str, Any]]:
    """Tenký wrapper — epizody sezóny přes get_series_episodes."""
    if not series_id:
        return []
    return get_series_episodes(series_id, season=season)
