# -*- coding: utf-8 -*-
"""Základní smoke test — ověří bootstrap a import api_webshare."""

from __future__ import annotations

import unittest

from support import load_modules


class TestExample(unittest.TestCase):
    def test_api_webshare_imports(self):
        api_webshare, _clean_title, _router_common = load_modules()
        self.assertTrue(hasattr(api_webshare, "get_token"))
        self.assertTrue(hasattr(api_webshare, "get_movies"))


if __name__ == "__main__":
    unittest.main()
