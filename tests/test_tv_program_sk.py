# -*- coding: utf-8 -*-
"""Testy SK TV programu a klasifikace zanru."""

from __future__ import annotations

import unittest

from support import load_modules

# load_modules loads api_webshare; import sk directly
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "resources"))

tv_sk = importlib.import_module("lib.tv_program_sk")


class TestTvProgramSk(unittest.TestCase):
    def test_classify_film(self):
        self.assertEqual(tv_sk._classify_genres(["film", "komédia"]), "film")

    def test_classify_series(self):
        self.assertEqual(tv_sk._classify_genres(["seriál", "dráma"]), "series")

    def test_classify_news(self):
        self.assertEqual(tv_sk._classify_genres("spravodajstvo"), "news")

    def test_clean_episode_suffix(self):
        self.assertEqual(
            tv_sk._clean_search_title("Druhá šanca III (10)"),
            "Druhá šanca",
        )
        self.assertEqual(
            tv_sk._clean_search_title("NCIS (12)"),
            "NCIS",
        )


if __name__ == "__main__":
    unittest.main()
