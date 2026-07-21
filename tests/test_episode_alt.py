# -*- coding: utf-8 -*-
"""Testy alt parsování epizod bez SxxEyy (Voyo / reality)."""

from __future__ import annotations

import unittest

from support import load_modules

aws, _, _ = load_modules()


class TestEpisodeAltParse(unittest.TestCase):
    def test_sxxeyy_unchanged(self):
        name = "Game.of.Thrones.S01E02.720p.mkv"
        self.assertEqual(aws._parse_episode(name, "Game of Thrones"), (1, 2))

    def test_epizoda_pattern(self):
        name = "Ruza.pre.nevestu.epizoda.1.1080p.mkv"
        series = "Ruža pre nevestu"
        self.assertEqual(aws._parse_episode(name, series), (1, 1))

    def test_epizoda_2(self):
        name = "Ruza pre nevestu epizoda 2 CZ.mkv"
        series = "Ruza pre nevestu"
        self.assertEqual(aws._parse_episode(name, series), (1, 2))

    def test_dil_pattern(self):
        name = "Survivor.dil.5.720p.mkv"
        series = "Survivor"
        self.assertEqual(aws._parse_episode(name, series), (1, 5))

    def test_survivor_slovensko_sxxeyy(self):
        name = "Survivor.Slovensko.S03E05.1080p.mkv"
        series = "Survivor"
        self.assertEqual(aws._parse_episode(name, series), (3, 5))
        det = aws._series_name(name)
        self.assertTrue(aws._series_title_match_for_episodes(series, det))

    def test_sk_diel_before_number(self):
        name = "Ruza pre nevestu 1.diel.mkv"
        series = "Ruža pre nevestu"
        self.assertEqual(aws._parse_episode(name, series), (1, 1))

    def test_number_before_dil(self):
        name = "Ruza pre nevestu 1. dil CZ.mkv"
        series = "Ruza pre nevestu"
        self.assertEqual(aws._parse_episode(name, series), (1, 1))

    def test_survivor_sk_5_dil(self):
        name = "Survivor SK 5 dil.mkv"
        series = "Survivor"
        self.assertEqual(aws._parse_episode(name, series), (1, 5))

    def test_without_series_name_no_alt(self):
        name = "Show epizoda 3.mkv"
        self.assertEqual(aws._parse_episode(name), (None, None))

    def test_wrong_series_rejected(self):
        name = "Jiny.serial epizoda 1.mkv"
        self.assertEqual(aws._parse_episode(name, "Survivor"), (None, None))

    def test_bachelor_not_paradise(self):
        self.assertFalse(aws._series_title_match_for_episodes(
            "Bachelor", "Bachelor in Paradise"))

    def test_survivor_slovensko_still_ok(self):
        self.assertTrue(aws._series_title_match_for_episodes(
            "Survivor", "Survivor Slovensko"))

    def test_synthetic_base_title(self):
        base = aws._episode_base_for_series(
            "Ruza pre nevestu", 1, 1,
            "Ruza pre nevestu epizoda 1.mkv",
        )
        self.assertEqual(base, "Ruza pre nevestu S01E01")

    def test_sxxeyy_base_from_filename(self):
        name = "GoT.S01E03.1080p.mkv"
        base = aws._episode_base_for_series("Game of Thrones", 1, 3, name)
        self.assertIn("S01E03", base)

    def test_ordinace_abbrev_title_match(self):
        series = "Ordinace v růžové zahradě"
        self.assertTrue(aws._series_title_match_for_episodes(series, "Ordinace"))
        self.assertTrue(aws._series_title_match_for_episodes(
            series, "Ordinace.v.ruzove.zahrade"))

    def test_ordinace_high_episode_number(self):
        series = "Ordinace v růžové zahradě"
        name = "Ordinace dil 847 CZ.mkv"
        self.assertEqual(aws._parse_episode(name, series), (1, 847))

    def test_ordinace_ascii_epizoda(self):
        series = "Ordinace v růžové zahradě"
        name = "Ordinace.v.ruzove.zahrade.epizoda.120.1080p.mkv"
        self.assertEqual(aws._parse_episode(name, series), (1, 120))

    def test_trailing_year_not_episode(self):
        series = "Ordinace v růžové zahradě"
        name = "Ordinace 2024.mkv"
        self.assertEqual(aws._parse_episode(name, series), (None, None))

    def test_abbrev_does_not_shorten_english_spinoff(self):
        # Opačný směr než Bachelor->Paradise: dlouhý EN název nesmí spadnout na zkratku
        self.assertFalse(aws._series_title_match_for_episodes(
            "Bachelor in Paradise", "Bachelor"))


if __name__ == "__main__":
    unittest.main()
