# -*- coding: utf-8 -*-
"""Testy clean_title.py — bez Kodi."""

from __future__ import annotations

import unittest

from support import load_modules

_, ct, _ = load_modules()


class TestCleanTitle(unittest.TestCase):
    def test_simple_movie(self):
        self.assertEqual(
            ct.clean_title("Joker.2019.1080p.BluRay.x264.CZ.mkv"),
            "Joker",
        )

    def test_series_marker_kept_when_requested(self):
        out = ct.clean_title(
            "The.Mandalorian.S02E05.720p.WEB-DL.mkv",
            keep_series_marker=True,
        )
        self.assertIn("Mandalorian", out)

    def test_czech_diacritics_preserved(self):
        self.assertEqual(
            ct.clean_title("Vyuzeni.2024.1080p.CZ.dabing.mkv"),
            "Vyuzeni",
        )

    def test_extract_year_from_filename(self):
        self.assertEqual(ct.extract_year("Michael.2026.1080p.WEBRip.mkv"), 2026)
        self.assertEqual(ct.extract_year("Inception.2010.1080p.mkv"), 2010)
        self.assertIsNone(ct.extract_year("Bez.Roku.1080p.mkv"))

    def test_trailing_genre_words_stripped(self):
        # v0.0.132: uploaderi lepi zanry za nazev - sabotuji TMDB match.
        self.assertEqual(ct.clean_title("Maly bratr komedie"), "Maly bratr")
        self.assertEqual(
            ct.clean_title("Mortal Kombat 2 akcni,dobrodruzny,fantasy"),
            "Mortal Kombat 2",
        )
        self.assertEqual(
            ct.clean_title("Nekdo to rad v Plzni NEW"),
            "Nekdo to rad v Plzni",
        )

    def test_sequel_number_preserved(self):
        # Sekvencni cislo se NESMI ztratit (spatny TMDB match jinak).
        self.assertEqual(ct.clean_title("Rocky 4 CZ dabing"), "Rocky 4")
        self.assertEqual(ct.clean_title("Top Gun 2"), "Top Gun 2")

    def test_genre_word_alone_kept(self):
        # Legitimni jednoslovny titul co je zaroven zanr - neztratit.
        self.assertEqual(ct.clean_title("Drama"), "Drama")
        self.assertEqual(ct.clean_title("Western"), "Western")

    def test_audio_channels_not_confused_with_sequel(self):
        # "5.1" audio nesmi zustat jako "5" sekvencni cislo.
        self.assertEqual(ct.clean_title("Vysehrad 5.1 CZ"), "Vysehrad")


if __name__ == "__main__":
    unittest.main()
