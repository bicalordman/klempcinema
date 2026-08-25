# -*- coding: utf-8 -*-
"""Probe jazyka ze zacatku kontejneru (MKV Language)."""

from __future__ import annotations

import unittest

from support import load_modules

aws, _, _ = load_modules()


class TestContainerLangProbe(unittest.TestCase):
    def test_mkv_language_cze(self):
        # Synthetic head with Matroska Language element + cze
        data = b"\x1aE\xdf\xa3" + b"xxxx\x22\xb5\x9c\x83cze\x00yyyy"
        langs = aws._langs_from_container_head(data)
        self.assertIn("cze", langs)
        self.assertEqual(aws._tag_from_probed_langs(langs), "CZ")

    def test_dual_cz_en(self):
        data = b"Language\x00cze\x00Language\x00eng\x00"
        langs = aws._langs_from_container_head(data)
        self.assertEqual(aws._tag_from_probed_langs(langs), "Dual")

    def test_empty(self):
        self.assertEqual(aws._langs_from_container_head(b""), set())
        self.assertEqual(aws._tag_from_probed_langs(set()), "")

    def test_probed_overrides_question(self):
        self.assertEqual(aws._variant_lang_tag("Film.2024.UHD.mkv", "CZ"), "CZ")
        self.assertEqual(aws._variant_lang_tag("Film.2024.UHD.mkv", ""), "?")


if __name__ == "__main__":
    unittest.main()
