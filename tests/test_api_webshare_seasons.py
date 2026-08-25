# -*- coding: utf-8 -*-
"""Testy api_webshare.get_seasons / get_episodes."""

from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import MagicMock, patch

from support import load_modules

api_webshare, _, _ = load_modules()


class TestApiWebshareSeriesMethods(unittest.TestCase):
    def test_get_seasons_delegates_to_get_series_seasons(self):
        with patch.object(api_webshare, "get_series_seasons", return_value={"seasons": [{"season_number": 1}]}) as mocked:
            seasons = api_webshare.get_seasons("My Series")

        self.assertEqual(seasons, [{"season_number": 1}])
        mocked.assert_called_once_with("My Series")

    def test_get_episodes_delegates_to_get_series_episodes(self):
        with patch.object(api_webshare, "get_series_episodes", return_value=[{"episode_key": "S01E01"}]) as mocked:
            eps = api_webshare.get_episodes("My Series", 1)

        self.assertEqual(eps, [{"episode_key": "S01E01"}])
        mocked.assert_called_once_with("My Series", season=1)

    def test_get_seasons_returns_empty_for_empty_series_id(self):
        self.assertEqual(api_webshare.get_seasons(""), [])

    def test_get_episodes_returns_empty_for_empty_series_id(self):
        self.assertEqual(api_webshare.get_episodes("", 1), [])


class TestApiWebshareSeriesSeasonEpisodeFlow(unittest.TestCase):
    def test_get_series_seasons_uses_ws_and_tmdb(self):
        fake_lookup = MagicMock(return_value=[
            {"tmdb_id": 123, "title": "My Series", "poster": "http://p",
             "fanart": "http://f", "plot": "Plot", "year": 2024},
        ])
        fake_seasons = MagicMock(return_value=[
            {"season_number": 1, "name": "Season 1", "overview": "Overview",
             "episode_count": 10, "poster": "http://p1", "air_date": "2024-01-01"},
        ])

        with patch.object(api_webshare, "_collect_episodes_files", return_value=[{"name": "My Series S01E01"}]):
            with patch.object(api_webshare, "cache", autospec=True) as cache_mock:
                cache_mock.cache_get.return_value = None
                cache_mock.cache_set.return_value = None
                cache_mock.cache_delete.return_value = None
                cache_mock.cache_clear_prefix.return_value = None
                import lib.tmdb_tv_api as _tmdb_mod
                with patch.object(_tmdb_mod, "tmdb_lookup_tv", fake_lookup):
                    with patch.object(_tmdb_mod, "get_seasons", fake_seasons):
                        seasons_info = api_webshare.get_series_seasons(
                            "My Series", force_refresh=True
                        )

        self.assertEqual(seasons_info["tmdb_id"], 123)
        self.assertEqual(seasons_info["title_localized"], "My Series")
        self.assertEqual(len(seasons_info["seasons"]), 1)
        self.assertEqual(seasons_info["seasons"][0]["ws_episode_count"], 1)
        self.assertEqual(seasons_info["seasons"][0]["tmdb_episode_count"], 10)

    def test_get_series_episodes_filters_by_season(self):
        variant = {"name": "My Series S01E01", "ident": "abc"}

        with patch.object(api_webshare, "_collect_episodes_files", return_value=[{"name": "My Series S01E01"}, {"name": "My Series S02E01"}]):
            with patch.object(api_webshare, "_files_to_variant_refs", return_value=[variant]):
                with patch.object(api_webshare, "cache", autospec=True) as cache_mock:
                    cache_mock.cache_get.return_value = None
                    cache_mock.cache_set.return_value = None
                    cache_mock.cache_delete.return_value = None
                    cache_mock.cache_clear_prefix.return_value = None
                    episodes = api_webshare.get_series_episodes("My Series", season=1, force_refresh=True)

        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["episode_key"], "S01E01")
        self.assertEqual(episodes[0]["variants_count"], 1)
        self.assertEqual(episodes[0]["variant_idents"], ["abc"])


if __name__ == "__main__":
    unittest.main()
