import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from nba_data import cache, stats_nba


RAW = Path("data/raw")


def fixture_leaguegamefinder_payload():
    headers = [
        "SEASON_ID", "TEAM_ID", "TEAM_NAME", "GAME_ID", "GAME_DATE",
        "MATCHUP", "WL", "PTS", "FGM", "FGA", "FG3M", "FG3A", "FTM",
        "FTA", "OREB", "DREB", "REB", "AST", "STL", "BLK", "TOV", "PF",
    ]
    home = {
        "SEASON_ID": "22020", "TEAM_ID": 1610612751,
        "TEAM_NAME": "Brooklyn Nets", "GAME_ID": "0022000001",
        "GAME_DATE": "2020-12-22", "MATCHUP": "BKN vs. GSW", "WL": "W",
        "PTS": 125, "FGM": 45, "FGA": 90, "FG3M": 15, "FG3A": 40,
        "FTM": 20, "FTA": 25, "OREB": 10, "DREB": 30, "REB": 40,
        "AST": 25, "STL": 7, "BLK": 5, "TOV": 12, "PF": 18,
    }
    away = {
        **home, "TEAM_ID": 1610612744, "TEAM_NAME": "Golden State Warriors",
        "MATCHUP": "GSW @ BKN", "WL": "L", "PTS": 99,
    }
    return {
        "resource": "leaguegamefinder",
        "parameters": {},
        "resultSets": [{
            "name": "LeagueGameFinderResults",
            "headers": headers,
            "rowSet": [[row[column] for column in headers] for row in [home, away]],
        }],
    }


def publish_fixture(tmp_path, payload=None):
    destination = tmp_path / "stats_nba/leaguegamefinder_2021.json"
    cache.publish_bytes(
        destination,
        json.dumps(payload or fixture_leaguegamefinder_payload()).encode(),
        {"provider": "stats_nba", "season": 2021},
        stats_nba.validate_payload,
    )
    return destination


def test_dns_preflight_rejects_adguard_sinkhole(monkeypatch):
    """Would fail if sinkhole DNS responses were accepted."""
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("0.0.0.0", 443))
    ])
    with pytest.raises(stats_nba.SourceAccessError, match="AdGuard"):
        stats_nba.preflight_dns()


def test_load_season_normalizes_cached_leaguegamefinder(tmp_path):
    """Would fail if valid cached rows were not normalized as NBA Stats rows."""
    publish_fixture(tmp_path)

    result = stats_nba.load_season(tmp_path, 2021, expected_games=1)

    assert len(result) == 2
    assert result.iloc[0]["game_id"] == "0022000001"
    assert set(result["source"]) == {"nba_stats"}


def test_load_season_reports_missing_cache_entry(tmp_path):
    """Would fail if an absent cache leaked a pathlib FileNotFoundError."""
    with pytest.raises(
        cache.CacheIntegrityError,
        match="missing cache entry",
    ):
        stats_nba.load_season(tmp_path, 2021, expected_games=1)


def test_load_season_decodes_cached_json_as_utf8(tmp_path):
    """Would fail if locale-default ASCII decoded a valid UTF-8 cache."""
    payload = fixture_leaguegamefinder_payload()
    payload["parameters"] = {"source_note": "café"}
    destination = tmp_path / "stats_nba/leaguegamefinder_2021.json"
    cache.publish_bytes(
        destination,
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        {"provider": "stats_nba", "season": 2021},
        stats_nba.validate_payload,
    )
    environment = {
        **os.environ,
        "LANG": "C",
        "LC_ALL": "C",
        "PYTHONCOERCECLOCALE": "0",
        "PYTHONUTF8": "0",
    }
    command = (
        "from pathlib import Path; "
        "from nba_data import stats_nba; "
        f"stats_nba.load_season(Path({str(tmp_path)!r}), 2021, "
        "expected_games=1)"
    )

    completed = subprocess.run(
        [sys.executable, "-c", command],
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr


def test_load_season_rejects_cache_with_another_season(tmp_path):
    """Would fail if a swapped, valid season cache were accepted."""
    payload = fixture_leaguegamefinder_payload()
    season_index = payload["resultSets"][0]["headers"].index("SEASON_ID")
    for row in payload["resultSets"][0]["rowSet"]:
        row[season_index] = "22021"
    publish_fixture(tmp_path, payload)

    with pytest.raises(
        ValueError, match=r"season 2021: cached rows have seasons \[2022\]"
    ):
        stats_nba.load_season(tmp_path, 2021, expected_games=1)


@pytest.mark.integration
@pytest.mark.parametrize("season, games", sorted(stats_nba.EXPECTED_GAMES.items()))
def test_cached_season_counts(season, games):
    path = RAW / "stats_nba" / f"leaguegamefinder_{season}.json"
    if not path.exists():
        pytest.skip("NBA Stats cache absent")
    result = stats_nba.load_season(RAW, season)
    assert len(result) == games * 2
    assert result["game_id"].nunique() == games
