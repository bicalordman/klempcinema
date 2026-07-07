# -*- coding: utf-8 -*-
"""Testy invalidate/refresh metadata cache."""

from __future__ import annotations

import unittest

from support import load_modules

aws, ct, _ = load_modules()


class TestInvalidateMetadata(unittest.TestCase):
    def test_auto_heal_skips_complete_poster(self):
        it = {
            "title": "Joker",
            "poster": "https://image.tmdb.org/t/p/w500/x.jpg",
            "year": 2019,
        }
        aws._auto_heal_item_metadata(it, "movie")
        self.assertEqual(it["title"], "Joker")

    def test_enrich_snap_key_stable(self):
        it = {"title": "Joker", "base_title": "Joker", "year": 2019}
        key = aws._enrich_snap_key(it, "movie")
        self.assertEqual(key, "enrich:snap:v2:movie:joker:2019")


if __name__ == "__main__":
    unittest.main()
