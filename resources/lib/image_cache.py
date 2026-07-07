# -*- coding: utf-8 -*-
"""
image_cache.py
--------------
Lokální disk-cache pro TMDB plakáty / fanart, aby Kodi nestahoval
obrázky znovu při každém scrollu seznamu.

Strategie:
    1) cached_image_path(url) -> local path (or original URL if not cached yet)
    2) Pokud lokální verze existuje, vrátí 'special://' cestu (Kodi ji
       zobrazí instantně bez síťového kola).
    3) Pokud neexistuje, naplánuje stáhnutí na pozadí (worker queue).
       Při příštím otevření už bude k dispozici.

Limity:
    - max_total_bytes (default 200 MB) - LRU cleanup
    - max_workers (default 4) - paralelní stahování bez zahlcení
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from queue import Queue, Empty
from typing import Any, Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, wait
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from . import shutdown as _shutdown

log = logging.getLogger("klempcinema.image_cache")

# Konfigurace
MAX_TOTAL_BYTES = 200 * 1024 * 1024  # 200 MB
# v0.0.64: 4 -> 2 workers. Pri Kodi shutdown ceka Python na vsechny
# pending urlopen() syscall. 2 paralelni stahovani je dostatecne pro
# UI scroll (TMDB CDN je rychle), a snizuje na pulku worst-case shutdown
# wait time. Pomalejsi zarizeni (Xbox One, RPi3) tezi nejvic.
MAX_WORKERS = 2
# v0.0.81: 2s timeout - plakaty jsou male, shutdown max wait 2s.
DOWNLOAD_TIMEOUT = 2
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/118.0.0.0 Safari/537.36"
)

# Cleanup spouštíme jen občas (každý N-tý zápis), ne při každém získání souboru
_cleanup_counter = 0
_cleanup_lock = threading.Lock()
CLEANUP_EVERY_N = 50

# Globální queue pro async downloads (init lazy)
_download_queue: Optional[Queue] = None
_workers_started = False
_queue_lock = threading.Lock()

# Set URL, které jsou už ve frontě (aby se nestahovaly duplikátně)
_pending_urls: set = set()
_pending_lock = threading.Lock()

# v0.0.62: in-memory cache stat() vysledku - drive kazde otevreni rubriky
# delalo os.path.exists() + os.path.getsize() pro KAZDY z 50 plakatu +
# 50 fanartu = 100 stat calls/page. Na Xboxu s SD karto / pomalym diskem
# to byly stovky ms. Ted: 1x stat per URL, pak hit v RAM dict az do
# expirace (60s pro existujici, 5s pro chybejici).
# Klic: URL -> (local_path_nebo_prazdno, expire_ts)
_path_cache: Dict[str, tuple] = {}
_path_cache_lock = threading.Lock()
_PATH_CACHE_TTL_HIT = 300.0   # 5 min - mame stazene, nemeni se
_PATH_CACHE_TTL_MISS = 5.0    # 5 sec - nemame, mozna se za chvili dotahne
_MAX_PATH_CACHE = 500
_MAX_QUEUE_PENDING = 40
_MAX_IMAGE_BYTES = 4 * 1024 * 1024
_WORKER_IDLE_SEC = 25

_plugin_exit = threading.Event()


def _profile_dir() -> str:
    try:
        import xbmcaddon  # type: ignore
        import xbmcvfs    # type: ignore
        addon = xbmcaddon.Addon()
        return xbmcvfs.translatePath(addon.getAddonInfo("profile"))
    except Exception:  # noqa: BLE001
        return os.path.join(os.path.expanduser("~"), ".klempcinema")


def _images_dir() -> str:
    d = os.path.join(_profile_dir(), "images")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def _local_path_for(url: str) -> str:
    """Stabilní lokální cesta pro daný URL."""
    h = hashlib.md5(url.encode("utf-8")).hexdigest()
    # Přidat příponu podle URL (pro Kodi to není nutné, ale pomáhá při debug)
    ext = ".jpg"
    try:
        path = urlparse(url).path.lower()
        if path.endswith(".png"):
            ext = ".png"
        elif path.endswith(".webp"):
            ext = ".webp"
    except Exception:  # noqa: BLE001
        pass
    return os.path.join(_images_dir(), f"{h}{ext}")


# v0.0.63: lokalni shutdown flag (zachovan pro back-compat).
# v0.0.64: nyni driven globalnim xbmc.Monitor pres shutdown.py - kdyz
# Kodi posle abort, _shutdown_event se set automaticky bez nutnosti
# rucniho volani z routeru.
_shutdown_event = threading.Event()


def _on_global_shutdown() -> None:
    """v0.0.64: callback z shutdown.py watcheru - signaluj workerum exit."""
    _shutdown_event.set()
    log.debug("image_cache: global shutdown signal received")


# Registrace ihned na import - watcher si nas zavola
_shutdown.register(_on_global_shutdown)


def on_plugin_exit() -> None:
    """Ukonci image workery po kazde navigaci (perzistentni Python)."""
    _plugin_exit.set()
    with _pending_lock:
        _pending_urls.clear()
    if _download_queue is not None:
        try:
            while True:
                _download_queue.get_nowait()
        except Empty:
            pass
    with _path_cache_lock:
        if len(_path_cache) > _MAX_PATH_CACHE:
            keep = sorted(
                _path_cache.items(),
                key=lambda x: x[1][1],
                reverse=True,
            )[:_MAX_PATH_CACHE]
            _path_cache.clear()
            _path_cache.update(dict(keep))
    global _workers_started
    with _queue_lock:
        _workers_started = False
    _plugin_exit.clear()


try:
    from . import lifecycle as _lifecycle
    _lifecycle.register_plugin_exit(on_plugin_exit)
except Exception:  # noqa: BLE001
    pass


def shutdown() -> None:
    """Manual shutdown signal (back-compat). V Kodi se obvykle vola
    automaticky pres shutdown.py - tato funkce je k dispozici jako
    fallback / testing helper.
    """
    _shutdown_event.set()
    log.debug("image_cache: shutdown signaled")


def _ensure_workers() -> None:
    """Lazy start worker threadů."""
    global _download_queue, _workers_started
    with _queue_lock:
        if _workers_started:
            return
        _download_queue = Queue()
        for i in range(MAX_WORKERS):
            t = threading.Thread(
                target=_worker_loop,
                name=f"img-cache-{i}",
                daemon=True,
            )
            t.start()
        _workers_started = True
        log.debug("image_cache: spuštěno %d worker threadů", MAX_WORKERS)


def _worker_loop() -> None:
    """Worker thread - bere URL z fronty a stahuje.

    v0.0.64: pred kazdym urlopen() check shutdown - kdyz Kodi posle
    abort, ihned konec bez novych HTTP requests. Plus check globalniho
    shutdown.is_shutting_down() v cyklu.

    v0.0.63: krátký queue.get timeout (1s misto 60s) umozni rychlou
    reakci na shutdown signal.
    """
    assert _download_queue is not None
    idle_ticks = 0
    while (not _plugin_exit.is_set()
           and not _shutdown_event.is_set()
           and not _shutdown.is_shutting_down()):
        try:
            url = _download_queue.get(timeout=1)
            idle_ticks = 0
        except Empty:
            idle_ticks += 1
            if idle_ticks >= _WORKER_IDLE_SEC:
                break
            continue
        # Check zda nas Kodi nezavolal mezi get() a stahnutim
        if _shutdown_event.is_set() or _shutdown.is_shutting_down() or _plugin_exit.is_set():
            try:
                _download_queue.task_done()
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            _download_image(url)
        except Exception as exc:  # noqa: BLE001
            log.debug("image_cache download fail (%s): %s", url, exc)
        finally:
            with _pending_lock:
                _pending_urls.discard(url)
            try:
                _download_queue.task_done()
            except Exception:  # noqa: BLE001
                pass
    log.debug("image_cache worker exit (shutdown)")


def _download_image(url: str) -> bool:
    """Stáhne URL do lokálního cache souboru. True při úspěchu.

    v0.0.64: pred zacatkem stazeni check shutdown - kdyz Kodi posle abort
    v intervalu mezi queue.get() a tady, return False bez urlopen().
    """
    if not url or not url.startswith(("http://", "https://")):
        return False
    if _shutdown_event.is_set() or _shutdown.is_shutting_down() or _plugin_exit.is_set():
        return False
    dest = _local_path_for(url)
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return True  # už máme

    try:
        req = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
            if resp.status != 200:
                log.debug("image_cache: HTTP %s pro %s", resp.status, url)
                return False
            data = bytearray()
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > _MAX_IMAGE_BYTES:
                    log.debug("image_cache: obrazek prilis velky %s", url)
                    return False
            data = bytes(data)
    except Exception as exc:  # noqa: BLE001
        log.debug("image_cache: download error %s: %s", url, exc)
        return False

    if not data:
        return False

    tmp = f"{dest}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(tmp, "wb") as fp:
            fp.write(data)
        os.replace(tmp, dest)
    except OSError as exc:
        log.debug("image_cache: write error: %s", exc)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False

    # v0.0.62: invalidovat MISS cache - dalsi cached_image_path()
    # provede stat a zacacheuje HIT misto cekani na expiraci.
    with _path_cache_lock:
        _path_cache.pop(url, None)

    _maybe_cleanup()
    return True


def _maybe_cleanup() -> None:
    """Občas spustit LRU cleanup."""
    global _cleanup_counter
    with _cleanup_lock:
        _cleanup_counter += 1
        if _cleanup_counter < CLEANUP_EVERY_N:
            return
        _cleanup_counter = 0
    _cleanup_lru()


def _cleanup_lru() -> None:
    """Pokud cache překračuje MAX_TOTAL_BYTES, smaž nejstarší (podle mtime)."""
    d = _images_dir()
    try:
        files = []
        total = 0
        for name in os.listdir(d):
            path = os.path.join(d, name)
            try:
                st = os.stat(path)
                files.append((st.st_mtime, st.st_size, path))
                total += st.st_size
            except OSError:
                continue
        if total <= MAX_TOTAL_BYTES:
            return
        # Smaž od nejstarších, dokud nejsme pod limit (s 10% rezervou)
        target = int(MAX_TOTAL_BYTES * 0.9)
        files.sort(key=lambda x: x[0])  # nejstarší první
        freed = 0
        for _mt, sz, path in files:
            if total - freed <= target:
                break
            try:
                os.remove(path)
                freed += sz
            except OSError:
                pass
        log.info("image_cache cleanup: smazáno %d B (z %d B)", freed, total)
    except OSError as exc:
        log.debug("image_cache cleanup chyba: %s", exc)


def _local_file_ok(path: str) -> bool:
    try:
        return bool(path) and os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def cached_image_path(url: str) -> str:
    """
    Vrátí cestu, kterou má Kodi použít jako art:
      - lokální soubor, pokud už máme stažený (= rychlý zobrazování)
      - originální URL jinak (Kodi si stáhne sám)

    Současně naplánuje stažení na pozadí, aby příště byl v cache.

    v0.0.62: in-memory cache stat() vysledku - drive kazde otevreni rubriky
    delalo 50-100 os.path.exists() volani (stovky ms na Xboxu).
    """
    if not url or not url.startswith(("http://", "https://")):
        return url

    # 1) In-memory cache hit (rychly path, zadne disk I/O)
    now = time.time()
    with _path_cache_lock:
        cached_entry = _path_cache.get(url)
    if cached_entry is not None:
        local_path, expire_ts = cached_entry
        if now < expire_ts:
            if local_path and _local_file_ok(local_path):
                return local_path
            if local_path:
                with _path_cache_lock:
                    _path_cache.pop(url, None)
            return url

    # 2) Cache miss/expired - stat na disku
    dest = _local_path_for(url)
    try:
        st = os.stat(dest)
        exists = st.st_size > 0
    except OSError:
        exists = False

    if exists:
        # Update mtime pro LRU (best effort, neslouzi k cache)
        try:
            os.utime(dest, None)
        except OSError:
            pass
        with _path_cache_lock:
            _path_cache[url] = (dest, now + _PATH_CACHE_TTL_HIT)
        return dest

    # 3) Nestazene - naplanuj na pozadi + cache MISS pro 5s
    with _path_cache_lock:
        _path_cache[url] = ("", now + _PATH_CACHE_TTL_MISS)

    _ensure_workers()
    with _pending_lock:
        if url in _pending_urls:
            return url
        if len(_pending_urls) >= _MAX_QUEUE_PENDING:
            return url
        _pending_urls.add(url)
    assert _download_queue is not None
    try:
        _download_queue.put_nowait(url)
    except Exception:  # noqa: BLE001
        with _pending_lock:
            _pending_urls.discard(url)

    return url


def prefetch_image(url: str) -> None:
    """Naplánuj stáhnutí URL bez vracení cesty (čisté warm-up)."""
    if not url:
        return
    cached_image_path(url)


def warm_urls(urls: List[str], max_workers: int = 4,
              max_urls: int = 40, total_timeout: float = 3.0) -> int:
    """
    v0.0.114: Paralelne stahne chybejici plakaty/fanart do lok. cache.
    Pri prechodu Filmy -> Novinky stejne URL = okamzite zobrazeni bez site.
    Vraci pocet uspesne stazenych.
    """
    if _shutdown_event.is_set() or _shutdown.is_shutting_down() or _plugin_exit.is_set():
        return 0
    todo: List[str] = []
    seen: set = set()
    for url in urls:
        if not url or not url.startswith(("http://", "https://")):
            continue
        if url in seen:
            continue
        seen.add(url)
        dest = _local_path_for(url)
        try:
            if os.path.exists(dest) and os.path.getsize(dest) > 0:
                with _path_cache_lock:
                    _path_cache[url] = (dest, time.time() + _PATH_CACHE_TTL_HIT)
                continue
        except OSError:
            pass
        todo.append(url)
        if len(todo) >= max_urls:
            break
    if not todo:
        return 0
    workers = min(max_workers, len(todo))
    done = 0
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_download_image, u) for u in todo]
            _done, pending = wait(futures, timeout=total_timeout)
            done = len(_done)
            for fut in pending:
                fut.cancel()
    except Exception as exc:  # noqa: BLE001
        log.debug("image_cache.warm_urls selhalo: %s", exc)
    if done:
        log.debug("image_cache.warm_urls: %d/%d stazeno", done, len(todo))
    return done


def warm_items_posters(items: List[Dict[str, Any]], max_urls: int = 40) -> int:
    """Stahne plakaty/fanart pro seznam polozek (cross-rubric reuse)."""
    urls: List[str] = []
    for it in items:
        for field in ("poster", "csfd_poster", "fanart"):
            u = (it.get(field) or "").strip()
            if u:
                urls.append(u)
    return warm_urls(urls, max_urls=max_urls)


def stats() -> dict:
    """Vrátí přehled velikosti a počtu souborů v cache."""
    d = _images_dir()
    count = 0
    total = 0
    try:
        for name in os.listdir(d):
            path = os.path.join(d, name)
            try:
                total += os.path.getsize(path)
                count += 1
            except OSError:
                continue
    except OSError:
        pass
    return {
        "count": count,
        "bytes": total,
        "max_bytes": MAX_TOTAL_BYTES,
        "dir": d,
    }


def cache_clear() -> int:
    """Smaže všechny lokální obrázky. Vrací počet smazaných."""
    d = _images_dir()
    n = 0
    try:
        for name in os.listdir(d):
            try:
                os.remove(os.path.join(d, name))
                n += 1
            except OSError:
                pass
    except OSError:
        pass
    return n
