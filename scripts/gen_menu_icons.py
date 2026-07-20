# -*- coding: utf-8 -*-
"""Bile pictogramy pro menu — podobne Sosaci, ale jine tvary (klapka, monitor…)."""
from __future__ import annotations

import math
import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "resources", "icons", "menu")
SIZE = 256
W = (255, 255, 255, 255)


def blank():
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def punch(img: Image.Image, draw_fn) -> None:
    mask = Image.new("L", (SIZE, SIZE), 0)
    draw_fn(ImageDraw.Draw(mask))
    px, mp = img.load(), mask.load()
    for y in range(SIZE):
        for x in range(SIZE):
            if mp[x, y] > 128:
                px[x, y] = (0, 0, 0, 0)


def save(img: Image.Image, *names: str) -> None:
    for name in names:
        path = os.path.join(OUT, name)
        img.save(path, "PNG")
        print("wrote", name)


def movies():
    img, d = blank()
    d.rounded_rectangle((50, 112, 206, 214), radius=12, fill=W)
    d.polygon([(50, 112), (206, 78), (206, 112), (50, 146)], fill=W)
    punch(img, lambda m: [
        m.polygon([(72, 98), (92, 90), (92, 122), (72, 130)], fill=255),
        m.polygon([(112, 90), (132, 82), (132, 114), (112, 122)], fill=255),
        m.polygon([(152, 82), (172, 74), (172, 106), (152, 114)], fill=255),
        m.polygon([(108, 142), (108, 192), (158, 167)], fill=255),
    ])
    save(img, "movies.png", "movies_all.png")


def series():
    img, d = blank()
    d.rounded_rectangle((46, 56, 210, 168), radius=14, fill=W)
    punch(img, lambda m: m.rounded_rectangle((64, 74, 192, 148), radius=8, fill=255))
    d = ImageDraw.Draw(img)
    d.polygon([(108, 92), (108, 130), (148, 111)], fill=W)
    d.rectangle((112, 168, 144, 186), fill=W)
    d.rounded_rectangle((80, 186, 176, 202), radius=6, fill=W)
    save(img, "series.png", "series_all.png")


def search():
    img, d = blank()
    d.ellipse((48, 40, 172, 164), outline=W, width=18)
    d.line((156, 152, 214, 210), fill=W, width=20)
    save(img, "search.png")


def platforms():
    img, d = blank()
    d.rounded_rectangle((48, 68, 208, 198), radius=18, fill=W)
    cx, cy, r = 128, 128, 46

    def star(m):
        pts = []
        for i in range(10):
            ang = -math.pi / 2 + i * math.pi / 5
            rr = r if i % 2 == 0 else r * 0.4
            pts.append((cx + rr * math.cos(ang), cy + rr * math.sin(ang)))
        m.polygon(pts, fill=255)

    punch(img, star)
    save(img, "platforms.png")


def voyo():
    img, d = blank()
    d.ellipse((44, 44, 212, 212), outline=W, width=16)
    d.polygon([(104, 84), (104, 172), (180, 128)], fill=W)
    save(img, "voyo.png")


def discover():
    img, d = blank()
    d.ellipse((48, 48, 208, 208), outline=W, width=14)
    d.ellipse((94, 94, 162, 162), outline=W, width=10)
    d.ellipse((116, 116, 140, 140), fill=W)
    d.polygon([(128, 56), (142, 108), (128, 98), (114, 108)], fill=W)
    save(img, "discover.png")


def tv():
    img, d = blank()
    d.line((86, 66, 128, 102), fill=W, width=10)
    d.line((170, 66, 128, 102), fill=W, width=10)
    d.ellipse((78, 54, 96, 72), fill=W)
    d.ellipse((160, 54, 178, 72), fill=W)
    d.rounded_rectangle((52, 102, 204, 198), radius=16, fill=W)
    punch(img, lambda m: m.rounded_rectangle((70, 120, 186, 172), radius=8, fill=255))
    d = ImageDraw.Draw(img)
    d.rectangle((112, 198, 144, 212), fill=W)
    save(img, "tv.png", "tv_all.png", "tv_films.png", "tv_series.png", "tv_shows.png", "tv_prime.png")


def concerts():
    img, d = blank()
    d.ellipse((86, 36, 170, 120), fill=W)
    d.rounded_rectangle((112, 108, 144, 168), radius=8, fill=W)
    d.arc((70, 128, 186, 212), start=15, end=165, fill=W, width=12)
    d.rectangle((120, 198, 136, 218), fill=W)
    d.ellipse((98, 214, 158, 232), fill=W)
    save(img, "concerts.png")


def library():
    img, d = blank()
    d.rounded_rectangle((46, 56, 98, 198), radius=8, fill=W)
    d.rounded_rectangle((106, 72, 154, 198), radius=8, fill=W)
    d.rounded_rectangle((162, 48, 210, 198), radius=8, fill=W)
    d.rectangle((46, 198, 210, 216), fill=W)
    save(img, "library.png")


def tools():
    img, d = blank()
    cx, cy = 128, 128
    pts = []
    for i in range(16):
        ang = i * math.pi / 8 - math.pi / 2
        r = 88 if i % 2 == 0 else 58
        if i % 2 == 0:
            for da in (-0.11, 0.11):
                pts.append((cx + r * math.cos(ang + da), cy + r * math.sin(ang + da)))
        else:
            pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    d.polygon(pts, fill=W)
    punch(img, lambda m: m.ellipse((128 - 28, 128 - 28, 128 + 28, 128 + 28), fill=255))
    save(img, "tools.png")


def continue_ic():
    img, d = blank()
    d.ellipse((44, 44, 212, 212), fill=W)
    punch(img, lambda m: m.polygon([(102, 84), (102, 172), (178, 128)], fill=255))
    save(img, "continue.png")


def dub():
    img, d = blank()
    for i, h in enumerate((48, 84, 130, 84, 48)):
        x = 58 + i * 32
        y0 = 128 - h // 2
        d.rounded_rectangle((x, y0, x + 20, y0 + h), radius=8, fill=W)
    save(img, "movies_dub.png", "series_dub.png")


def docs():
    img, d = blank()
    d.rounded_rectangle((70, 40, 186, 216), radius=10, fill=W)
    punch(img, lambda m: [
        m.rectangle((92, 78, 164, 92), fill=255),
        m.rectangle((92, 110, 164, 124), fill=255),
        m.rectangle((92, 142, 164, 156), fill=255),
        m.rectangle((92, 174, 140, 188), fill=255),
    ])
    save(img, "movies_docs.png", "tv_docs.png")


def kids():
    img, d = blank()
    d.ellipse((76, 44, 180, 148), fill=W)
    d.polygon([(86, 204), (128, 138), (170, 204)], fill=W)
    punch(img, lambda m: [
        m.ellipse((100, 78, 120, 98), fill=255),
        m.ellipse((136, 78, 156, 98), fill=255),
    ])
    save(img, "movies_kids.png")


def animated():
    img, d = blank()
    cx, cy, r = 128, 128, 72
    pts = []
    for i in range(8):
        ang = -math.pi / 2 + i * math.pi / 4
        rr = r if i % 2 == 0 else r * 0.34
        pts.append((cx + rr * math.cos(ang), cy + rr * math.sin(ang)))
    d.polygon(pts, fill=W)
    save(img, "movies_animated.png")


def latest():
    img, d = blank()
    d.ellipse((48, 48, 208, 208), outline=W, width=14)
    d.rectangle((120, 76, 136, 130), fill=W)
    d.rectangle((120, 130, 172, 146), fill=W)
    save(img, "movies_latest.png")


def my_files():
    img, d = blank()
    d.rounded_rectangle((54, 78, 202, 204), radius=12, fill=W)
    d.polygon([(54, 104), (102, 104), (120, 70), (54, 70)], fill=W)
    save(img, "my_files.png")


def trending():
    img, d = blank()
    d.line([(58, 176), (100, 108), (132, 140), (196, 64)], fill=W, width=16)
    d.ellipse((184, 52, 208, 76), fill=W)
    save(img, "trending_movies.png", "trending_tv.png")


def genres():
    img, d = blank()
    for box in ((50, 50, 122, 122), (134, 50, 206, 122), (50, 134, 122, 206), (134, 134, 206, 206)):
        d.rounded_rectangle(box, radius=12, fill=W)
    save(img, "genres_movies.png", "genres_tv.png")


def tv_refresh():
    img, d = blank()
    d.arc((56, 56, 200, 200), start=40, end=300, fill=W, width=16)
    d.polygon([(170, 46), (214, 72), (168, 102)], fill=W)
    save(img, "tv_refresh.png")


def main():
    os.makedirs(OUT, exist_ok=True)
    movies()
    series()
    search()
    platforms()
    voyo()
    discover()
    tv()
    concerts()
    library()
    tools()
    continue_ic()
    dub()
    docs()
    kids()
    animated()
    latest()
    my_files()
    trending()
    genres()
    tv_refresh()
    print("OK")


if __name__ == "__main__":
    main()
