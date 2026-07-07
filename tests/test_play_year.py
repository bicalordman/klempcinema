# -*- coding: utf-8 -*-
"""Testy roku pri prehravani (_item_play_year)."""

from __future__ import annotations

import unittest

from support import load_modules

_, _, rc = load_modules()


class TestPlayYear(unittest.TestCase):
    def test_no_tmdb_id_no_play_year_filter(self):
        """Upload tag 2026 bez TMDB matchi nesmi filtrovat varianty."""
        item = {"year": 2026, "title": "Dabek nosi pradu"}
        self.assertIsNone(rc._item_play_year(item))

    def test_tmdb_id_uses_year(self):
        item = {"tmdb_id": 123, "year": 2006, "title": "Devil Wears Prada"}
        self.assertEqual(rc._item_play_year(item), 2006)

    def test_michael_2026_with_tmdb(self):
        item = {"tmdb_id": 999, "year": 2026, "title": "Michael"}
        self.assertEqual(rc._item_play_year(item), 2026)


if __name__ == "__main__":
    unittest.main()
