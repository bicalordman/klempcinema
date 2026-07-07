# -*- coding: utf-8 -*-
"""Testy detekce CZ dabingu — kritické pro rubriky Novinky dabované."""

from __future__ import annotations

import unittest

from support import load_modules

aws, _, _ = load_modules()


class TestCzDubDetection(unittest.TestCase):
    def test_explicit_cz_dab(self):
        self.assertTrue(aws._detect_dubbed("Avatar.2024.1080p.CZ.dabing.mkv"))

    def test_cz_audio_track_5_1(self):
        self.assertTrue(aws._detect_dubbed("Michael.2026.1080p.WEB-DL.CZ.5.1.H264.mkv"))

    def test_cz_titulky_not_dub(self):
        self.assertFalse(aws._detect_dubbed("Film.2020.1080p.CZ.titulky.mkv"))

    def test_spanish_dub_not_cz(self):
        self.assertFalse(aws._detect_dubbed("Pelicula.2024.1080p.ES.5.1.dubbed.mkv"))

    def test_bare_dubbed_without_cz(self):
        self.assertFalse(aws._detect_dubbed("Movie.2024.1080p.dubbed.ENG.mkv"))

    def test_polish_only(self):
        self.assertFalse(aws._detect_dubbed("Film.2020.PL.lektor.mkv"))


if __name__ == "__main__":
    unittest.main()
