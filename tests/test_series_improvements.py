# -*- coding: utf-8 -*-
"""v0.0.164: continuous dily + series search helpers."""

from __future__ import annotations

import unittest

from support import load_modules

api_webshare, _, _ = load_modules()


class TestContinuousDily(unittest.TestCase):
    def test_ordinace_style_is_continuous(self):
        files = [
            {"name": f"Ordinace v ruzove zahrade dil {n} CZ.mkv"}
            for n in (1, 50, 100, 200, 400, 847)
        ]
        self.assertTrue(
            api_webshare._is_continuous_dily_show(files, "Ordinace v ruzove zahrade")
        )

    def test_sxxeyy_show_not_continuous(self):
        files = [
            {"name": "Breaking Bad S01E01.mkv"},
            {"name": "Breaking Bad S01E02.mkv"},
            {"name": "Breaking Bad S02E01.mkv"},
            {"name": "Breaking Bad S02E02.mkv"},
            {"name": "Breaking Bad S03E01.mkv"},
        ]
        self.assertFalse(
            api_webshare._is_continuous_dily_show(files, "Breaking Bad")
        )


class TestEpisodeAltStillS01(unittest.TestCase):
    def test_dil_high_number(self):
        s, e = api_webshare._parse_episode_alt(
            "Ordinace dil 847 CZ.mkv", "Ordinace")
        self.assertEqual(s, 1)
        self.assertEqual(e, 847)


if __name__ == "__main__":
    unittest.main()
