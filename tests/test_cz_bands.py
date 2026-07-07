# -*- coding: utf-8 -*-
"""Testy databaze ceskych kapel."""

from __future__ import annotations

import unittest

from support import ensure_resources_path, install_kodi_stubs

install_kodi_stubs()
ensure_resources_path()
from lib.modules import cz_bands as cb  # noqa: E402


class TestCzBands(unittest.TestCase):
    def test_all_names_not_empty(self):
        names = cb.all_cz_sk_band_names()
        self.assertGreater(len(names), 50)

    def test_ws_queries_for_band(self):
        q = cb.ws_queries_for_band("Lucie")
        self.assertIn("Lucie live", q)
        self.assertIn("Lucie koncert", q)

    def test_is_known_artist(self):
        self.assertTrue(cb.is_known_cz_sk_artist("Lucie koncert Praha"))
        self.assertFalse(cb.is_known_cz_sk_artist("Random Foreign Band"))

    def test_genre_rock_has_bands(self):
        names = cb.band_names_for_genre("rock")
        self.assertIn("Lucie", names)
        self.assertIn("Kabát", names)


if __name__ == "__main__":
    unittest.main()
