# -*- coding: utf-8 -*-
"""Testy quality pickeru, plakátů a roku variant."""

from __future__ import annotations

import unittest

from support import load_modules

aws, _, _ = load_modules()


class TestAudioPicker(unittest.TestCase):
    def test_6ch_with_underscore(self):
        name = "Michael.2026.1080p.WEBRip.6CH_x264.mkv"
        self.assertEqual(aws.detect_audio_for_picker(name), "5.1")

    def test_dts_in_name(self):
        self.assertEqual(
            aws.detect_audio_for_picker("Film.2010.1080p.BluRay.DTS.mkv"),
            "DTS",
        )

    def test_cz_5_1_track(self):
        self.assertEqual(
            aws.detect_audio_for_picker("Film.2024.WEB-DL.CZ.5.1.mkv"),
            "5.1",
        )


class TestPosterUrl(unittest.TestCase):
    def test_tmdb_is_quality(self):
        url = "https://image.tmdb.org/t/p/w500/abc.jpg"
        self.assertTrue(aws._is_quality_poster_url(url))

    def test_webshare_thumb_not_quality(self):
        url = "https://img.webshare.cz/thumb/123.jpg"
        self.assertFalse(aws._is_quality_poster_url(url))

    def test_ws_thumb_does_not_count_as_display_poster(self):
        it = {"poster": "https://img.webshare.cz/thumb/x.jpg"}
        self.assertFalse(aws._item_has_display_poster(it))

    def test_tmdb_poster_counts(self):
        it = {"poster": "https://image.tmdb.org/t/p/w500/x.jpg"}
        self.assertTrue(aws._item_has_display_poster(it))


class TestYearFilter(unittest.TestCase):
    def test_wrong_year_keeps_variants(self):
        """v0.0.117: spatny rok nesmi smazat vsechny varianty."""
        variants = [
            {"name": "Devil.Wears.Prada.2006.1080p.CZ.mkv", "ident": "a"},
        ]
        out = aws.filter_variants_by_year(variants, 2026)
        self.assertEqual(len(out), 1)

    def test_matching_year_filters(self):
        variants = [
            {"name": "Michael.2026.1080p.mkv", "ident": "a"},
            {"name": "Michael.1996.1080p.mkv", "ident": "b"},
        ]
        out = aws.filter_variants_by_year(variants, 2026)
        self.assertEqual(len(out), 1)
        self.assertIn("2026", out[0]["name"])


if __name__ == "__main__":
    unittest.main()
