import json
from pathlib import Path

import pandas as pd
import pytest

from nba_data import cache, franchises, hoopr


def schedule_fixture() -> pd.DataFrame:
    """Return representative rows from a hoopR NBA schedule parquet."""
    return pd.DataFrame([
        {
            "game_id": 401000001,
            "season_type": 2,
            "status_type_completed": True,
            "type_abbreviation": "REG",
            "game_date": "2023-10-24T23:30Z",
            "home_team_name": "Denver Nuggets",
            "away_team_name": "Los Angeles Lakers",
            "neutral_site": False,
            "venue_id": 1234,
            "venue_full_name": "Ball Arena",
            "venue_address_city": "Denver",
            "attendance": "19842",
        },
        {
            "game_id": 401000002,
            "season_type": 2,
            "status_type_completed": True,
            "type_abbreviation": "REG",
            "game_date": "2023-10-24T23:30Z",
            "home_team_name": "Golden State Warriors",
            "away_team_name": "Phoenix Suns",
            "neutral_site": False,
            "venue_id": 2345,
            "venue_full_name": "Chase Center",
            "venue_address_city": "San Francisco",
            "attendance": "18064",
        },
        {
            "game_id": 401000003,
            "season_type": 2,
            "status_type_completed": True,
            "type_abbreviation": "ALLSTAR",
            "game_date": "2024-02-18T01:00Z",
            "home_team_name": "Team LeBron",
            "away_team_name": "Team Giannis",
            "neutral_site": True,
            "venue_id": 3456,
            "venue_full_name": "Gainbridge Fieldhouse",
            "venue_address_city": "Indianapolis",
            "attendance": "16855",
        },
        {
            "game_id": 401000004,
            "season_type": 2,
            "status_type_completed": True,
            "type_abbreviation": "CC",
            "game_date": "2023-12-09T01:30Z",
            "home_team_name": "Los Angeles Lakers",
            "away_team_name": "Indiana Pacers",
            "neutral_site": True,
            "venue_id": 4567,
            "venue_full_name": "T-Mobile Arena",
            "venue_address_city": "Las Vegas",
            "attendance": "17456",
        },
        {
            "game_id": 401000005,
            "season_type": 2,
            "status_type_completed": False,
            "type_abbreviation": "REG",
            "game_date": "2024-04-14T19:30Z",
            "home_team_name": "Boston Celtics",
            "away_team_name": "Washington Wizards",
            "neutral_site": False,
            "venue_id": 5678,
            "venue_full_name": "TD Garden",
            "venue_address_city": "Boston",
            "attendance": "19156",
        },
    ])


def publish_schedule_fixture(
    tmp_path: Path, season: int = 2024, frame: pd.DataFrame | None = None
) -> Path:
    """Publish an actual Parquet cache entry for the schedule loader."""
    source = tmp_path / "fixture.parquet"
    (schedule_fixture() if frame is None else frame).to_parquet(source, index=False)
    destination = tmp_path / "hoopr" / f"nba_schedule_{season}.parquet"
    cache.publish_bytes(
        destination,
        source.read_bytes(),
        {"provider": "hoopr", "season": season},
        lambda path: hoopr.pq.ParquetFile(path),
    )
    return destination


def test_load_season_filters_exhibitions_cup_and_incomplete_rows(tmp_path):
    """Would fail if non-canonical completed schedule rows were retained."""
    publish_schedule_fixture(tmp_path)

    schedule, excluded = hoopr.load_season(tmp_path, 2024, expected_games=2)

    assert len(schedule) == 2
    assert set(excluded["reason"]) == {"exhibition", "cup_championship"}
    assert schedule["espn_game_id"].str.startswith("espn-").all()
    assert schedule["venue_id"].str.startswith("espn-").all()
    assert schedule["espn_attendance"].dtype == "Int64"


def test_load_season_rejects_unknown_ordinary_entrants(tmp_path):
    """Would fail if an unrecognized non-exhibition team were retained."""
    frame = schedule_fixture()
    frame.loc[1, "home_team_name"] = "Unknown NBA Club"
    publish_schedule_fixture(tmp_path, frame=frame)

    with pytest.raises(franchises.UnknownTeamError, match="Unknown NBA Club"):
        hoopr.load_season(tmp_path, 2024, expected_games=2)


class FakeResponse:
    def __init__(self, content: bytes, status_code: int = 200):
        self.content = content
        self.status_code = status_code


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def get(self, url, *, timeout, verify):
        self.calls.append((url, timeout, verify))
        if self.error is not None:
            raise self.error
        return self.response


def schedule_parquet_bytes(tmp_path: Path) -> bytes:
    source = tmp_path / "fetched.parquet"
    schedule_fixture().to_parquet(source, index=False)
    return source.read_bytes()


def test_fetch_caches_the_pinned_schedule_parquet(tmp_path, monkeypatch):
    """Would fail if a fetched schedule was not cached from the pinned snapshot."""
    payload = schedule_parquet_bytes(tmp_path)
    session = FakeSession(FakeResponse(payload))
    monkeypatch.setattr(hoopr.requests, "Session", lambda: session)

    hoopr.fetch(tmp_path, [2024])

    destination = tmp_path / "hoopr/nba_schedule_2024.parquet"
    assert cache.read_valid(destination, hoopr.validate_payload) == payload
    metadata = json.loads(destination.with_name(destination.name + ".meta.json").read_text())
    assert metadata["provider"] == "hoopr"
    assert metadata["upstream_commit"] == "50fe7e429c6b5bcbe62e7a56d19afe5a80e6e143"
    assert session.calls == [(
        "https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-data/"
        "50fe7e429c6b5bcbe62e7a56d19afe5a80e6e143/"
        "nba/schedules/parquet/nba_schedule_2024.parquet",
        120,
        True,
    )]


def test_fetch_wraps_request_errors_with_type_and_url(tmp_path, monkeypatch):
    """Would fail if source request failures lost their type or pinned URL."""
    session = FakeSession(error=hoopr.requests.Timeout("timed out"))
    monkeypatch.setattr(hoopr.requests, "Session", lambda: session)

    with pytest.raises(cache.SourceAccessError, match="Timeout") as error:
        hoopr.fetch(tmp_path, [2024])

    assert "https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-data/" in str(error.value)
