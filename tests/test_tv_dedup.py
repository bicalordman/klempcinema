# -*- coding: utf-8 -*-
"""Testy deduplikace TV programu."""

from __future__ import annotations

import unittest

from support import ensure_resources_path, install_kodi_stubs

install_kodi_stubs()
ensure_resources_path()
from lib import tv_program as tp  # noqa: E402


class TestTvDedup(unittest.TestCase):
    def test_dedupe_same_show_id(self):
        items = [
            {"channel_id": "x:hbo-2", "show_id": "42", "start_min": 40,
             "title": "Miniaturní manželka"},
            {"channel_id": "x:hbo-2", "show_id": "42", "start_min": 40,
             "title": "Miniaturní manželka"},
        ]
        out = tp._dedupe_tv_items(items)
        self.assertEqual(len(out), 1)

    def test_dedupe_same_time_title_without_show_id(self):
        items = [
            {"channel_id": "x:hbo-2", "channel": "HBO 2", "start_min": 80,
             "title": "Slečna bestie"},
            {"channel_id": "x:hbo-2", "channel": "HBO 2", "start_min": 80,
             "title": "Slečna bestie"},
        ]
        out = tp._dedupe_tv_items(items)
        self.assertEqual(len(out), 1)

    def test_strip_premium_from_base(self):
        base = [
            {"channel": "Nova", "title": "Film A"},
            {"channel": "HBO 2", "title": "Film B"},
        ]
        extra = [
            {"channel": "HBO 2", "channel_id": "x:hbo-2", "premium": True,
             "title": "Film B"},
        ]
        merged = tp._strip_premium_channels_from_base(base)
        merged.extend(extra)
        merged = tp._dedupe_tv_items(merged)
        hbo = [it for it in merged if it.get("channel") == "HBO 2"]
        self.assertEqual(len(hbo), 1)
        self.assertTrue(hbo[0].get("premium"))


if __name__ == "__main__":
    unittest.main()
