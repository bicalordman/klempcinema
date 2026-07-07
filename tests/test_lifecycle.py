# -*- coding: utf-8 -*-
"""Testy lifecycle.py - hlida ze modul jde naimportovat a uklid probehne.

Regrese v0.0.133: chybejici 'import threading' zpusobil NameError pri
importu -> veskery plugin-exit uklid se tise preskakoval (try/except v
plugin.py a image_cache.py), coz vedlo k zaseknutemu vypinani Kodi.
"""

from __future__ import annotations

import unittest

from support import ensure_resources_path, install_kodi_stubs


class TestLifecycle(unittest.TestCase):
    def setUp(self):
        install_kodi_stubs()
        ensure_resources_path()

    def test_import_and_flags(self):
        from lib import lifecycle
        self.assertFalse(lifecycle.is_plugin_exiting())

    def test_on_plugin_exit_runs_handlers(self):
        from lib import lifecycle
        calls = []
        lifecycle.register_plugin_exit(lambda: calls.append(1))
        lifecycle.on_plugin_exit()
        self.assertEqual(calls, [1])
        # Po dobehnuti musi byt flag zase vypnuty (uklid je jednorazovy).
        self.assertFalse(lifecycle.is_plugin_exiting())


if __name__ == "__main__":
    unittest.main()
