"""Tests for the sportsdataverse injury-report derivative pin (Task 11).

Unit tests never touch the network: the GitHub release API and the asset
downloads are served by ``FakeSession``, and every Parquet payload is built
in-test with the 17 known columns through ``derivative_parquet_bytes``,
which later tasks import to seed their own derivative trees.
"""

import io
import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from nba_data import cache, injury_derivative, injury_reports
from nba_data.stats_nba import SourceAccessError


ASSET_COLUMNS = (
    "season", "report_datetime", "game_date", "game_time", "report_matchup",
    "away_team_abbr", "home_team_abbr", "team", "report_player_name",
    "current_status", "reason", "category", "previous_status", "game_id",
    "team_id", "person_id", "match_method",
)

_ASSET_TYPES = {
    "season": pa.int32(),
    "report_datetime": pa.timestamp("us"),
    "game_date": pa.date32(),
    "team_id": pa.int64(),
    "person_id": pa.int64(),
}


def derivative_parquet_bytes(rows, *, columns=ASSET_COLUMNS) -> bytes:
    """Serialise ``rows`` (dicts keyed by column) as one Parquet payload.

    Every column in ``columns`` is present, in that order; a key a row omits
    is null. The default column set is the 17 known derivative columns with
    their natural Arrow types (naive microsecond ``report_datetime``,
    ``date32`` ``game_date``); any other column is a UTF-8 string, which is
    what the column-set tests need to add an unknown column.
    """
    schema = pa.schema(
        [(name, _ASSET_TYPES.get(name, pa.string())) for name in columns]
    )
    arrays = [
        pa.array([row.get(name) for row in rows], type=schema.field(name).type)
        for name in columns
    ]
    table = pa.Table.from_arrays(arrays, schema=schema)
    sink = io.BytesIO()
    pq.write_table(table, sink)
    return sink.getvalue()


def _player_row(**overrides):
    row = {
        "season": 2026,
        "report_datetime": datetime(2026, 1, 10, 17, 30),
        "game_date": date(2026, 1, 10),
        "game_time": "07:30 (ET)",
        "report_matchup": "BOS@NYK",
        "away_team_abbr": "BOS",
        "home_team_abbr": "NYK",
        "team": "Boston Celtics",
        "report_player_name": "Porter Jr., Michael",
        "current_status": "Out",
        "reason": "Injury/Illness - Left Ankle; Sprain",
        "category": "Injury/Illness",
        "previous_status": "Questionable",
        "game_id": "0022500500",
        "team_id": 1610612738,
        "person_id": 1629008,
        "match_method": "exact",
    }
    row.update(overrides)
    return row


class FakeResponse:
    def __init__(self, status_code, content, headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = dict(headers or {})


class FakeSession:
    """Serves responses by URL and records every request's keyword arguments."""

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.request_log = []

    def urls(self) -> list[str]:
        return [entry["url"] for entry in self.request_log]

    def get(self, url, **kwargs):
        self.request_log.append({"url": url, **kwargs})
        if url not in self.routes:
            raise AssertionError(f"FakeSession has no response for {url}")
        item = self.routes[url]
        if isinstance(item, BaseException):
            raise item
        return item


def _release_body(seasons, updated_at="2026-11-03T06:10:00Z") -> bytes:
    return json.dumps({
        "tag_name": injury_derivative.RELEASE_TAG,
        "assets": [
            {
                "name": f"injury_report_{season}.parquet",
                "updated_at": updated_at,
                "browser_download_url": injury_derivative.asset_url(season),
            }
            for season in seasons
        ],
    }).encode("utf-8")


# --------------------------------------------------------------------------
# constants, URLs and paths
# --------------------------------------------------------------------------


def test_asset_url_and_path(tmp_path):
    """Would fail if the pin pointed anywhere but the release asset and raw tree."""
    assert injury_derivative.REPOSITORY == "sportsdataverse/sportsdataverse-data"
    assert injury_derivative.RELEASE_TAG == "nba_stats_injury_report"
    assert injury_derivative.SEASONS == tuple(range(2019, 2027))
    assert injury_derivative.RAW_SUBDIR == "raw/sportsdataverse/nba_stats_injury_report"
    assert injury_derivative.RELEASE_API_URL == (
        "https://api.github.com/repos/sportsdataverse/sportsdataverse-data"
        "/releases/tags/nba_stats_injury_report"
    )
    assert injury_derivative.asset_url(2026) == (
        "https://github.com/sportsdataverse/sportsdataverse-data/releases/download"
        "/nba_stats_injury_report/injury_report_2026.parquet"
    )
    assert injury_derivative.asset_path(tmp_path, 2026) == (
        tmp_path / "raw/sportsdataverse/nba_stats_injury_report"
        / "injury_report_2026.parquet"
    )


# --------------------------------------------------------------------------
# validate_asset
# --------------------------------------------------------------------------


def test_validate_asset_requires_exactly_the_seventeen_columns(tmp_path):
    """Would fail if a drifted column set were accepted as the pinned derivative."""
    good = tmp_path / "good.parquet"
    good.write_bytes(derivative_parquet_bytes([_player_row()]))
    injury_derivative.validate_asset(good)

    missing = tmp_path / "missing.parquet"
    missing.write_bytes(
        derivative_parquet_bytes([{}], columns=ASSET_COLUMNS[:-1])
    )
    with pytest.raises(ValueError, match="match_method"):
        injury_derivative.validate_asset(missing)

    extra = tmp_path / "extra.parquet"
    extra.write_bytes(
        derivative_parquet_bytes([{}], columns=ASSET_COLUMNS + ("surprise",))
    )
    with pytest.raises(ValueError, match="surprise"):
        injury_derivative.validate_asset(extra)

    renamed = tmp_path / "renamed.parquet"
    renamed.write_bytes(
        derivative_parquet_bytes(
            [{}], columns=ASSET_COLUMNS[:-1] + ("match_methods",)
        )
    )
    with pytest.raises(ValueError):
        injury_derivative.validate_asset(renamed)

    not_parquet = tmp_path / "body.parquet"
    not_parquet.write_bytes(b"<html>rate limited</html>")
    with pytest.raises(ValueError):
        injury_derivative.validate_asset(not_parquet)


# --------------------------------------------------------------------------
# fetch
# --------------------------------------------------------------------------


def test_fetch_records_release_asset_updated_at_and_skips_valid_pairs(tmp_path):
    """Would fail if the sidecar lost the release binding or a valid pin were re-downloaded."""
    payload_2025 = derivative_parquet_bytes([_player_row(season=2025)])
    payload_2026 = derivative_parquet_bytes([_player_row(season=2026)])
    session = FakeSession({
        injury_derivative.RELEASE_API_URL: FakeResponse(
            200, _release_body(injury_derivative.SEASONS)
        ),
        injury_derivative.asset_url(2025): FakeResponse(200, payload_2025),
        injury_derivative.asset_url(2026): FakeResponse(200, payload_2026),
    })

    injury_derivative.fetch(tmp_path, [2026, 2025], session=session)

    assert session.urls() == [
        injury_derivative.RELEASE_API_URL,
        injury_derivative.asset_url(2025),
        injury_derivative.asset_url(2026),
    ]
    api_call = session.request_log[0]
    assert api_call["headers"] == {
        "Accept": "application/vnd.github+json",
        "User-Agent": injury_reports.USER_AGENT,
    }
    assert api_call["timeout"] == 60
    for asset_call in session.request_log[1:]:
        assert asset_call["allow_redirects"] is True
        assert asset_call["timeout"] == 60
        assert asset_call["headers"]["User-Agent"] == injury_reports.USER_AGENT

    for season, payload in ((2025, payload_2025), (2026, payload_2026)):
        path = injury_derivative.asset_path(tmp_path, season)
        assert path.read_bytes() == payload
        sidecar = json.loads(
            path.with_name(path.name + ".meta.json").read_text(encoding="utf-8")
        )
        assert sidecar["provider"] == "sportsdataverse"
        assert sidecar["resource"] == "nba_stats_injury_report"
        assert sidecar["season"] == season
        assert sidecar["url"] == injury_derivative.asset_url(season)
        assert sidecar["release_asset_updated_at"] == "2026-11-03T06:10:00Z"
        assert sidecar["content_length"] == len(payload)
        assert sidecar["sha256"] == cache.sha256_file(path)
        attested = cache.read_attested(path, injury_derivative.validate_asset)
        assert attested is not None and attested[0] == payload
    assert not list((tmp_path / injury_derivative.RAW_SUBDIR).glob("*.part"))

    # A second run over the same seasons has nothing pending: no request at all.
    second = FakeSession()
    injury_derivative.fetch(tmp_path, [2025, 2026], session=second)
    assert second.urls() == []

    # One more season pending: the release is read once and only that asset moves.
    payload_2024 = derivative_parquet_bytes([_player_row(season=2024)])
    third = FakeSession({
        injury_derivative.RELEASE_API_URL: FakeResponse(
            200, _release_body(injury_derivative.SEASONS)
        ),
        injury_derivative.asset_url(2024): FakeResponse(200, payload_2024),
    })
    injury_derivative.fetch(tmp_path, [2024, 2025, 2026], session=third)
    assert third.urls() == [
        injury_derivative.RELEASE_API_URL, injury_derivative.asset_url(2024),
    ]
    assert injury_derivative.asset_path(tmp_path, 2024).read_bytes() == payload_2024

    # A damaged pair is a hard failure before any request is sent.
    damaged = injury_derivative.asset_path(tmp_path, 2025)
    damaged.write_bytes(payload_2025 + b"\x00")
    fourth = FakeSession()
    with pytest.raises(cache.CacheIntegrityError):
        injury_derivative.fetch(tmp_path, [2025], session=fourth)
    assert fourth.urls() == []

    # A season outside the derivative scope never reaches the network.
    with pytest.raises(ValueError):
        injury_derivative.fetch(tmp_path, [2018], session=fourth)
    assert fourth.urls() == []


def test_fetch_fails_closed_when_an_asset_is_missing_from_the_release(tmp_path):
    """Would fail if a season silently vanished from the pin when the release lacked its asset."""
    payload_2026 = derivative_parquet_bytes([_player_row(season=2026)])
    session = FakeSession({
        injury_derivative.RELEASE_API_URL: FakeResponse(
            200, _release_body([2019, 2020, 2021, 2022, 2023, 2024, 2026])
        ),
        injury_derivative.asset_url(2026): FakeResponse(200, payload_2026),
    })

    with pytest.raises(SourceAccessError, match="injury_report_2025.parquet"):
        injury_derivative.fetch(tmp_path, [2026, 2025], session=session)

    # The release is checked before any asset download, so nothing was written.
    assert session.urls() == [injury_derivative.RELEASE_API_URL]
    assert not (tmp_path / injury_derivative.RAW_SUBDIR).exists()

    # A release API error, a non-200 asset body and a non-Parquet asset body
    # each fail closed too, and never leave a published pair behind.
    denied = FakeSession({
        injury_derivative.RELEASE_API_URL: FakeResponse(403, b'{"message":"rate limited"}'),
    })
    with pytest.raises(SourceAccessError, match="403"):
        injury_derivative.fetch(tmp_path, [2026], session=denied)

    not_found = FakeSession({
        injury_derivative.RELEASE_API_URL: FakeResponse(
            200, _release_body(injury_derivative.SEASONS)
        ),
        injury_derivative.asset_url(2026): FakeResponse(404, b"Not Found"),
    })
    with pytest.raises(SourceAccessError, match="404"):
        injury_derivative.fetch(tmp_path, [2026], session=not_found)

    html = FakeSession({
        injury_derivative.RELEASE_API_URL: FakeResponse(
            200, _release_body(injury_derivative.SEASONS)
        ),
        injury_derivative.asset_url(2026): FakeResponse(200, b"<html>login</html>"),
    })
    with pytest.raises(ValueError):
        injury_derivative.fetch(tmp_path, [2026], session=html)
    assert not injury_derivative.asset_path(tmp_path, 2026).exists()
    assert not injury_derivative.asset_path(tmp_path, 2026).with_name(
        "injury_report_2026.parquet.meta.json"
    ).exists()


# --------------------------------------------------------------------------
# comparison_rows
# --------------------------------------------------------------------------


def test_comparison_rows_maps_player_and_placeholder_rows():
    """Would fail if placeholder phrases, artifacts or whitespace reached the comparison raw."""
    payload = derivative_parquet_bytes([
        _player_row(
            team="Boston  Celtics",
            report_player_name="Porter Jr.,   Michael",
            reason="Injury/Illness - Left Ankle;\tSprain ",
            previous_status=None,
        ),
        _player_row(
            team="New York Knicks",
            report_player_name=None,
            current_status=None,
            reason="NOT YET  SUBMITTED",
            category=None,
            previous_status=None,
            person_id=None,
            match_method=None,
        ),
        _player_row(
            team="New York Knicks",
            report_player_name=None,
            current_status=None,
            reason=",",
            category=None,
            previous_status=None,
            person_id=None,
            match_method=None,
        ),
        _player_row(
            report_datetime=datetime(2021, 7, 5, 21, 55),
            game_date=date(2021, 7, 6),
            report_matchup="PHX@MIL",
            team="Phoenix Suns",
            report_player_name=None,
            current_status=None,
            reason="ALL PLAYERS AVAILABLE",
            category=None,
            previous_status=None,
            person_id=None,
            match_method=None,
        ),
        _player_row(
            team="Non-NBA Team",
            report_player_name=None,
            current_status=None,
            reason="Non-NBA Team",
            category=None,
            previous_status=None,
            person_id=None,
            match_method=None,
        ),
    ])

    rows = injury_derivative.comparison_rows(payload)

    assert isinstance(rows, pd.DataFrame)
    assert list(rows.columns) == [
        "report_datetime", "game_date", "matchup", "team", "kind", "player_name",
        "status", "reason", "category", "previous_status", "artifact",
    ]
    assert len(rows) == 5
    assert rows["report_datetime"].dt.tz is None
    assert rows["report_datetime"].tolist() == [
        pd.Timestamp("2026-01-10 17:30"),
        pd.Timestamp("2026-01-10 17:30"),
        pd.Timestamp("2026-01-10 17:30"),
        pd.Timestamp("2021-07-05 21:55"),
        pd.Timestamp("2026-01-10 17:30"),
    ]
    assert rows["game_date"].tolist() == [
        date(2026, 1, 10), date(2026, 1, 10), date(2026, 1, 10),
        date(2021, 7, 6), date(2026, 1, 10),
    ]
    assert rows["artifact"].dtype == bool
    assert rows["artifact"].tolist() == [False, False, True, False, False]

    player = rows.iloc[0]
    assert player["kind"] == "player"
    assert player["matchup"] == "BOS@NYK"
    assert player["team"] == "Boston Celtics"
    assert player["player_name"] == "Porter Jr., Michael"
    assert player["status"] == "Out"
    assert player["reason"] == "Injury/Illness - Left Ankle; Sprain"
    assert player["category"] == "Injury/Illness"
    assert player["previous_status"] is None

    not_yet = rows.iloc[1]
    assert not_yet["kind"] == "NOT YET SUBMITTED"
    assert not_yet["team"] == "New York Knicks"
    assert not_yet["player_name"] is None
    assert not_yet["status"] is None
    assert not_yet["reason"] is None
    assert not_yet["category"] is None

    comma = rows.iloc[2]
    assert comma["kind"] == "player"
    assert comma["player_name"] is None
    assert comma["status"] is None
    assert comma["reason"] == ","

    assert rows.iloc[3]["kind"] == "ALL PLAYERS AVAILABLE"
    assert rows.iloc[3]["matchup"] == "PHX@MIL"
    assert rows.iloc[4]["kind"] == "Non-NBA Team"
    assert rows.iloc[4]["team"] == "Non-NBA Team"
    assert rows.iloc[4]["reason"] is None

    # A payload with the wrong column set is refused rather than coerced.
    with pytest.raises(ValueError):
        injury_derivative.comparison_rows(
            derivative_parquet_bytes([{}], columns=ASSET_COLUMNS[:-1])
        )
