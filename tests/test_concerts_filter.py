# -*- coding: utf-8 -*-
"""Testy filtru koncertu."""

from __future__ import annotations

import unittest

from support import ensure_resources_path, install_kodi_stubs

install_kodi_stubs()
ensure_resources_path()
from lib.modules import concerts_utils as cu  # noqa: E402


class TestConcertsFilter(unittest.TestCase):
    def test_tmdb_music_genre_id(self):
        self.assertEqual(cu.TMDB_MUSIC, 10402)

    def test_documentary_excluded_from_concerts(self):
        item = {
            "title": "Mrakodrap naživo",
            "genre_ids": [99, 12],
            "tmdb_id": 123,
        }
        self.assertTrue(cu.is_excluded_non_concert("Mrakodrap naživo", item))

    def test_music_concert_not_excluded(self):
        item = {
            "title": "Queen Live at Wembley",
            "genre_ids": [10402],
            "tmdb_id": 456,
        }
        self.assertFalse(cu.is_excluded_non_concert("Queen Live at Wembley", item))

    def test_cz_tag_alone_not_cz_sk_concert(self):
        item = {"title": "Skyscraper Live", "ws_names": "Skyscraper.Live.2026.CZ.1080p"}
        self.assertFalse(cu.filter_region_cz_sk(item))

    def test_czech_concert_passes(self):
        item = {"title": "Lucie koncert Praha live", "ws_names": "Lucie.koncert.Praha.CZ"}
        self.assertTrue(cu.filter_region_cz_sk(item))

    def test_known_artist_without_live_marker(self):
        # v0.0.136: znama kapela z databaze projde i bez explicitniho 'live'
        item = {"title": "Kabát", "ws_names": "Kabat.2024.CZ.1080p"}
        self.assertTrue(cu.filter_region_cz_sk(item))

    def test_teleshopping_hard_excluded(self):
        self.assertTrue(cu.is_hard_excluded("Mediashop Teleshopping TV Barrandov CZ"))


if __name__ == "__main__":
    unittest.main()
