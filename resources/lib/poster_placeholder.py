# -*- coding: utf-8 -*-
"""
poster_placeholder.py
---------------------
Generator hezkych placeholder plakatu s nazvem filmu UVNITR obrazku.

v0.0.62: novy modul - drive jsme meli jen 2 staticke placeholdery
(placeholder_movie.png / placeholder_series.png) ktere vypadaly genericky.
Ted: kazdy film bez plakatu dostane vlastni vygenerovany plakat s nazvem.

Strategie:
    1) Pokud je k dispozici PIL/Pillow (script.module.pil dependency
       nebo systemove pip), vygeneruje se 600x900 plakat s:
       - gradientovym pozadim (typ-specific barva)
       - typovou ikonou nahore (film / TV)
       - nazvem filmu uprostred (auto-wrap)
       - rokem dole
       - addon brand 'KlempCinema'
    2) Pokud PIL chybi, vrati path na staticky placeholder
       (placeholder_movie.png / placeholder_series.png) - drivejsi chovani.

Cache: vygenerovany obrazky se ukladaji do addon profile/placeholders/
       s nazvem hash(title|year|type).png. Generuje se jen jednou per item.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from typing import Optional, Tuple

log = logging.getLogger("klempcinema.poster_placeholder")

# Detekce PIL - importujeme leniwe, abychom nezhavarovali pri startu
# pokud PIL neni k dispozici (typicky Kodi bez script.module.pil).
_PIL_AVAILABLE: Optional[bool] = None
_PIL_LOCK_CHECKED = False


def _pil_available() -> bool:
    """Lazy check + cache - testujeme PIL jen jednou per session."""
    global _PIL_AVAILABLE, _PIL_LOCK_CHECKED
    if _PIL_AVAILABLE is not None:
        return _PIL_AVAILABLE
    try:
        from PIL import Image, ImageDraw  # noqa: F401
        _PIL_AVAILABLE = True
        log.info("poster_placeholder: PIL k dispozici, generuji per-item plakaty")
    except Exception as exc:  # noqa: BLE001
        _PIL_AVAILABLE = False
        if not _PIL_LOCK_CHECKED:
            log.info("poster_placeholder: PIL nedostupne (%s), pouzivam staticke", exc)
            _PIL_LOCK_CHECKED = True
    return _PIL_AVAILABLE


def _profile_dir() -> str:
    """Vrati addon profile dir (cross-platform)."""
    try:
        import xbmcaddon  # type: ignore
        import xbmcvfs    # type: ignore
        addon = xbmcaddon.Addon()
        return xbmcvfs.translatePath(addon.getAddonInfo("profile"))
    except Exception:  # noqa: BLE001
        return os.path.join(os.path.expanduser("~"), ".klempcinema")


def _placeholder_dir() -> str:
    """Adresar pro per-item vygenerovane placeholdery."""
    d = os.path.join(_profile_dir(), "placeholders")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def _static_fallback(item_type: str) -> str:
    """Path na staticky placeholder (drivejsi chovani z ui.py)."""
    try:
        import xbmcaddon  # type: ignore
        addon = xbmcaddon.Addon()
        path = addon.getAddonInfo("path")
        if item_type in ("series", "tvshow", "episode"):
            cand = os.path.join(path, "resources", "icons",
                                "placeholder_series.png")
        else:
            cand = os.path.join(path, "resources", "icons",
                                "placeholder_movie.png")
        if os.path.exists(cand):
            return cand
        return addon.getAddonInfo("icon")
    except Exception:  # noqa: BLE001
        return ""


def _cache_key(title: str, year: Optional[int], item_type: str,
               genres: str = "") -> str:
    """Stabilni hash pro cache filename."""
    raw = f"{title}|{year or 0}|{item_type}|{genres or ''}".encode("utf-8")
    return hashlib.md5(raw).hexdigest()


def _sanitize_title(title: str, max_len: int = 60) -> str:
    """Vyhod multi-spaces, trim, prip. zkrat."""
    t = re.sub(r"\s+", " ", title or "").strip()
    if len(t) > max_len:
        t = t[: max_len - 1].rstrip() + "…"
    return t


# Barvy podle typu (gradient od stred-light po okraj-dark)
_COLORS = {
    "movie":  ((45, 30, 90),  (15, 8, 35)),    # fialovo-modra
    "series": ((30, 65, 90),  (10, 25, 40)),   # tealova
}


def _gradient_bg(w: int, h: int, color_in, color_out):
    """Vytvori radialni gradient (Image objekt). Lazy import PIL."""
    from PIL import Image  # type: ignore

    img = Image.new("RGB", (w, h), color_out)
    pixels = img.load()
    cx, cy = w / 2, h / 2
    max_d = (cx ** 2 + cy ** 2) ** 0.5

    # Optimalizace: redukujeme rozliseni gradientu - per-pixel je pomale.
    # Misto toho udelame 60x90 thumb gradient a pak resizujeme nahoru.
    small_w, small_h = 60, 90
    small = Image.new("RGB", (small_w, small_h))
    spx = small.load()
    s_cx, s_cy = small_w / 2, small_h / 2
    s_max = (s_cx ** 2 + s_cy ** 2) ** 0.5
    for y in range(small_h):
        for x in range(small_w):
            dist = ((x - s_cx) ** 2 + (y - s_cy) ** 2) ** 0.5
            t = dist / s_max
            r = int(color_in[0] * (1 - t) + color_out[0] * t)
            g = int(color_in[1] * (1 - t) + color_out[1] * t)
            b = int(color_in[2] * (1 - t) + color_out[2] * t)
            spx[x, y] = (r, g, b)
    return small.resize((w, h), Image.BILINEAR)


def _wrap_text(draw, text: str, font, max_width: int, max_lines: int = 4):
    """Jednoduchy word-wrap pro PIL ImageDraw."""
    words = text.split()
    if not words:
        return [text]
    lines: list = []
    cur: list = []
    for w in words:
        trial = " ".join(cur + [w])
        try:
            tw = draw.textlength(trial, font=font)
        except AttributeError:
            tw, _ = draw.textsize(trial, font=font)
        if tw <= max_width or not cur:
            cur.append(w)
        else:
            lines.append(" ".join(cur))
            cur = [w]
            if len(lines) >= max_lines:
                break
    if cur and len(lines) < max_lines:
        lines.append(" ".join(cur))
    # Pokud doslo k orezu, posledni radek zakonci tritečkou
    if len(lines) == max_lines and len(" ".join(lines).split()) < len(words):
        last = lines[-1]
        if not last.endswith("…"):
            lines[-1] = last.rstrip(".") + "…"
    return lines


def _get_font(size: int):
    """Najde nejaky pouzitelny TTF font. Fallback na default."""
    from PIL import ImageFont  # type: ignore
    # Zkus standardni systemove fonty (Linux/Win/Mac/Kodi)
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    # Kodi-bundled font (typicky pristupny)
    try:
        import xbmcaddon  # type: ignore
        import xbmcvfs    # type: ignore
        kodi_home = xbmcvfs.translatePath("special://xbmc/")
        candidates.append(os.path.join(kodi_home, "media", "Fonts",
                                       "arial.ttf"))
    except Exception:  # noqa: BLE001
        pass
    for path in candidates:
        try:
            if os.path.exists(path):
                return ImageFont.truetype(path, size)
        except Exception:  # noqa: BLE001
            continue
    try:
        return ImageFont.load_default()
    except Exception:  # noqa: BLE001
        return None


def _generate(title: str, year: Optional[int],
              item_type: str, dest_path: str,
              genres: Optional[str] = None) -> bool:
    """Vygeneruje placeholder s nazvem. Vrati True pri uspechu."""
    try:
        from PIL import Image, ImageDraw  # type: ignore
    except Exception:  # noqa: BLE001
        return False

    try:
        W, H = 600, 900
        c_in, c_out = _COLORS.get(
            "series" if item_type in ("series", "tvshow", "episode") else "movie",
            _COLORS["movie"],
        )
        img = _gradient_bg(W, H, c_in, c_out)
        draw = ImageDraw.Draw(img)

        # Nazev filmu - velky font (auto-fit pres adaptaci velikosti)
        clean = _sanitize_title(title)
        # Adaptivni velikost fontu - delsi text -> mensi font
        if len(clean) <= 18:
            font_size = 60
            max_lines = 3
        elif len(clean) <= 30:
            font_size = 48
            max_lines = 3
        else:
            font_size = 38
            max_lines = 4

        font_title = _get_font(font_size)
        font_meta = _get_font(28)
        font_brand = _get_font(22)

        # Wrap pres safe sirku (96% sirky)
        max_w = int(W * 0.92)
        lines = _wrap_text(draw, clean, font_title, max_w, max_lines)

        # Total vyska textu pro vertikalni centrovani
        try:
            line_h = font_title.getbbox("Aj")[3] if font_title else 60
        except Exception:  # noqa: BLE001
            line_h = font_size + 8
        line_h = int(line_h * 1.15)
        total_h = line_h * len(lines)
        y_start = (H - total_h) // 2 - 30

        # Kresleni textu s mirnym stinem pro citelnost
        for i, line in enumerate(lines):
            try:
                line_w = draw.textlength(line, font=font_title)
            except AttributeError:
                line_w, _ = draw.textsize(line, font=font_title)
            x = (W - line_w) // 2
            y = y_start + i * line_h
            # Tmavy stin
            draw.text((x + 2, y + 2), line, fill=(0, 0, 0, 180), font=font_title)
            # Hlavni text bily
            draw.text((x, y), line, fill=(255, 255, 255), font=font_title)

        # Rok + zanr pod nazvem
        meta_y = y_start + total_h + 12
        if genres:
            genre_line = genres if len(genres) <= 42 else genres[:39] + "…"
            try:
                gw = draw.textlength(genre_line, font=font_meta)
            except AttributeError:
                gw, _ = draw.textsize(genre_line, font=font_meta)
            draw.text(((W - gw) // 2 + 1, meta_y + 1), genre_line,
                      fill=(0, 0, 0, 160), font=font_meta)
            draw.text(((W - gw) // 2, meta_y), genre_line,
                      fill=(200, 200, 220), font=font_meta)
            meta_y += 36
        if year:
            year_str = f"({year})"
            try:
                yw = draw.textlength(year_str, font=font_meta)
            except AttributeError:
                yw, _ = draw.textsize(year_str, font=font_meta)
            y_year = meta_y
            draw.text(((W - yw) // 2 + 1, y_year + 1), year_str,
                      fill=(0, 0, 0, 160), font=font_meta)
            draw.text(((W - yw) // 2, y_year), year_str,
                      fill=(255, 215, 80), font=font_meta)

        # Typ icon nahore (film / TV) - jednoduchy symbol
        icon_text = "TV" if item_type in ("series", "tvshow", "episode") else "FILM"
        try:
            iw = draw.textlength(icon_text, font=font_meta)
        except AttributeError:
            iw, _ = draw.textsize(icon_text, font=font_meta)
        # Ramecek kolem typu (pill shape)
        pad_x, pad_y = 18, 8
        rect_w = int(iw) + pad_x * 2
        rect_h = 44
        rx = (W - rect_w) // 2
        ry = 60
        try:
            draw.rounded_rectangle(
                (rx, ry, rx + rect_w, ry + rect_h),
                radius=22, fill=(255, 255, 255, 40),
                outline=(255, 255, 255, 180), width=2,
            )
        except Exception:  # noqa: BLE001
            # Stara verze PIL bez rounded_rectangle
            draw.rectangle((rx, ry, rx + rect_w, ry + rect_h),
                           outline=(255, 255, 255), width=2)
        draw.text(((W - iw) // 2, ry + pad_y), icon_text,
                  fill=(255, 255, 255), font=font_meta)

        # Brand dole
        brand = "KlempCinema"
        try:
            bw = draw.textlength(brand, font=font_brand)
        except AttributeError:
            bw, _ = draw.textsize(brand, font=font_brand)
        draw.text(((W - bw) // 2, H - 55), brand,
                  fill=(180, 180, 200), font=font_brand)

        # Save - tmp + atomic rename
        import threading
        tmp = f"{dest_path}.{os.getpid()}.{threading.get_ident()}.tmp"
        img.save(tmp, "PNG", optimize=True)
        os.replace(tmp, dest_path)
        return True
    except Exception as exc:  # noqa: BLE001
        log.debug("poster_placeholder._generate failed: %s", exc)
        try:
            if os.path.exists(tmp):  # noqa: F821
                os.remove(tmp)
        except Exception:  # noqa: BLE001
            pass
        return False


def get_placeholder(title: str, year: Optional[int] = None,
                    item_type: str = "movie",
                    genres: Optional[str] = None) -> str:
    """
    Vrati cestu k placeholder posteru pro item:
      - vygenerovany per-item s nazvem uvnitr (pokud je PIL)
      - staticky typovy placeholder (pokud PIL chybi)
      - addon icon (uplny fallback)

    Vola se z ui.add_video_item kdyz item nema poster.
    """
    if not _pil_available() or not title:
        return _static_fallback(item_type)

    key = _cache_key(title, year, item_type, genres or "")
    dest = os.path.join(_placeholder_dir(), f"{key}.png")

    if os.path.exists(dest):
        try:
            if os.path.getsize(dest) > 0:
                return dest
        except OSError:
            pass

    ok = _generate(title, year, item_type, dest, genres=genres)
    if ok and os.path.exists(dest):
        return dest

    # Generation failed - static fallback
    return _static_fallback(item_type)
