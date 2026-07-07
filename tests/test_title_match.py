# -*- coding: utf-8 -*-
"""Testy title_match.py — fuzzy porovnani titulu."""

from __future__ import annotations

import unittest

from support import ensure_resources_path, install_kodi_stubs

install_kodi_stubs()
ensure_resources_path()
from lib import title_match as tm  # noqa: E402


class TestTitleMatch(unittest.TestCase):
    def test_normalize_strips_diacritics(self):
        self.assertEqual(tm.normalize_title("Ďábel nosí Pradu"), "dabelnosipradu")

    def test_typo_similarity(self):
        sim = tm.title_similarity("Dabek nosi pradu", "Ďábel nosí Pradu")
        self.assertGreater(sim, 0.75)

    def test_exact_match(self):
        self.assertEqual(tm.title_similarity("Joker", "Joker"), 1.0)

    def test_different_titles_low(self):
        sim = tm.title_similarity("Michael", "George Michael")
        self.assertLess(sim, 0.75)

    def test_typo_pradu_pravdu(self):
        fixed = tm.apply_typo_fixes("Ďabek nosí pradu")
        self.assertIn("pravdu", fixed.lower())

    def test_devil_wears_prada_sequel_hint_with_two(self):
        hints = tm.extra_search_queries("Ďabek nosí pradu 2", 2026)
        self.assertTrue(any("Prada 2" in h for h in hints))

    def test_devil_wears_prada_no_hint_without_sequel_marker(self):
        hints = tm.extra_search_queries("Ďabek nosí pradu", 2026)
        self.assertFalse(any("Prada 2" in h for h in hints))

    def test_sequel_mismatch_rejected(self):
        self.assertFalse(tm.metadata_title_compatible(
            "Nahy zabijak", "Ďábel nosí Pradu 2", "The Devil Wears Prada 2",
        ))

    def test_prada2_compatible_with_typo(self):
        self.assertTrue(tm.metadata_title_compatible(
            "Dabek nosi pradu 2",
            "Ďábel nosí Pradu 2",
            "The Devil Wears Prada 2",
        ))


if __name__ == "__main__":
    unittest.main()
