# -*- coding: utf-8 -*-
"""Regrese hledani — franchise (Hobit) vs osoba (Michael Jordan)."""

from __future__ import annotations

import unittest

from support import load_modules

api_webshare, _, _ = load_modules()


class TestSearchTitleMatch(unittest.TestCase):
    def test_hobit_franchise_parts(self):
        q = "Hobit"
        self.assertTrue(api_webshare._file_matches_search_title(
            "Hobit.Neocekavana.cesta.2012.1080p.BluRay.CZ.mkv", q))
        self.assertTrue(api_webshare._file_matches_search_title(
            "Hobit - Skodesna cesta 2013 CZ dabing.mkv", q))
        self.assertTrue(api_webshare._file_matches_search_title(
            "Hobit 2014 Bitva peti armad.mkv", q))

    def test_hobit_matches_english_hobbit(self):
        self.assertTrue(api_webshare._file_matches_search_title(
            "The.Hobbit.An.Unexpected.Journey.2012.mkv", "Hobit"))

    def test_michael_exact_ok(self):
        self.assertTrue(api_webshare._file_matches_search_title(
            "Michael.2026.1080p.BluRay.mkv", "Michael"))

    def test_michael_jordan_rejected(self):
        self.assertFalse(api_webshare._file_matches_search_title(
            "Michael.Jordan.documentary.2010.mkv", "Michael"))

    def test_unrelated_rejected(self):
        self.assertFalse(api_webshare._file_matches_search_title(
            "Random.Movie.About.Else.2020.mkv", "Hobit"))

    def test_harry_potter_multiword(self):
        self.assertTrue(api_webshare._file_matches_search_title(
            "Harry.Potter.a.Kamen.mudrcu.2001.mkv", "Harry Potter"))

    def test_alt_queries_bare_first(self):
        alts = api_webshare._search_alt_queries("Hobit")
        self.assertTrue(alts)
        self.assertEqual(alts[0], "Hobit")
        # Roky az pozdeji, ne pred bare dotazem
        self.assertFalse(alts[0].endswith("2026"))


class TestSearchRelevanceSort(unittest.TestCase):
    def test_simple_title_beats_complex(self):
        q = "Hobit"
        simple = {"base_title": "Hobit", "quality_score": 10}
        complex_ = {
            "base_title": "The Hobbit An Unexpected Journey Extended Edition",
            "quality_score": 90,
        }
        k_simple = api_webshare._search_sort_key(simple, query=q)
        k_complex = api_webshare._search_sort_key(complex_, query=q)
        self.assertLess(k_simple, k_complex)

    def test_relevance_exact_higher(self):
        q = "Mach a Šebestová"
        exact = {"base_title": "Mach a Šebestová"}
        long_ = {"base_title": "Mach a Šebestová na prázdninách komplet"}
        self.assertGreater(
            api_webshare._search_title_relevance(q, exact),
            api_webshare._search_title_relevance(q, long_),
        )


if __name__ == "__main__":
    unittest.main()
