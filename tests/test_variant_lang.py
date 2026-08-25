# -*- coding: utf-8 -*-
"""Jazykove tagy ve quality pickeru — nesmi lhát [EN]."""

from __future__ import annotations

import unittest

from support import load_modules

aws, _, _ = load_modules()


class TestVariantLangTag(unittest.TestCase):
    def test_unknown_uhd_not_en(self):
        # Typicky Odhaleni UHD bez jazyka v nazvu — muze byt CZ dab.
        tag = aws._variant_lang_tag(
            "Odhaleni.2024.2160p.UHD.BluRay.x265.HDR.Atmos.mkv"
        )
        self.assertEqual(tag, "?")
        self.assertNotEqual(tag, "EN")

    def test_explicit_en(self):
        self.assertEqual(
            aws._variant_lang_tag("Film.2024.2160p.UHD.EN.mkv"), "EN")
        self.assertEqual(
            aws._variant_lang_tag("Film.2024.1080p.ENG.TrueHD.mkv"), "EN")

    def test_cz_dab(self):
        self.assertEqual(
            aws._variant_lang_tag("Film.2024.1080p.CZ.dabing.mkv"), "CZ dab")

    def test_cz_tit(self):
        self.assertEqual(
            aws._variant_lang_tag("Film.2024.1080p.CZ.titulky.mkv"), "CZ tit")

    def test_dual_multi(self):
        self.assertEqual(
            aws._variant_lang_tag("Film.2024.1080p.Dual.Audio.mkv"), "Dual")
        self.assertEqual(
            aws._variant_lang_tag("Film.2024.1080p.MULTI.mkv"), "Multi")

    def test_eng_with_cz_subs_prefers_tit(self):
        self.assertEqual(
            aws._variant_lang_tag("Film.2024.1080p.ENG.CZ.titulky.mkv"),
            "CZ tit",
        )


if __name__ == "__main__":
    unittest.main()
