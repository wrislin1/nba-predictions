import datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
import requests

from nba_data import cache, historical_bref


FIXTURES = Path(__file__).parent / "fixtures/historical_bref"


@pytest.mark.parametrize(
    ("season", "league"),
    [(1947, "BAA"), (1949, "BAA"), (1950, "NBA"), (1983, "NBA")],
)
def test_league_for_season_uses_ending_year(season, league):
    assert historical_bref.league_for_season(season) == league


def test_discovery_accepts_month_pages_and_rejects_playoffs():
    pages = historical_bref.discover_month_pages(
        (FIXTURES / "BAA_1947_games.html").read_bytes(),
        1947,
    )

    assert [page.month for page in pages] == ["november", "december"]
    assert all("playoffs" not in page.url for page in pages)


def test_parser_preserves_missing_zero_and_overtime_semantics():
    result = historical_bref.parse_schedule_page(
        (FIXTURES / "BAA_1947_games-november.html").read_bytes(),
        season=1947,
        schedule_url=(
            "https://www.basketball-reference.com/leagues/"
            "BAA_1947_games-november.html"
        ),
    )

    assert pd.isna(result.iloc[0]["attendance"])
    assert result.iloc[1]["attendance"] == 4329
    assert result["attendance_status"].tolist() == [
        "missing", "reported_positive", "reported_zero"
    ]
    assert result["overtime_periods"].tolist() == [0, 1, 0]
    assert result["site_status"].tolist() == ["home", "home", "unknown"]
    assert result.iloc[0]["game_id"] == "bref-194611010TRH"
    assert result.iloc[2]["game_id"].startswith(
        "bref-fallback-19461112-boston-celtics-at-providence-steamrollers-"
    )


def test_parser_accepts_only_the_explicit_neutral_marker():
    result = historical_bref.parse_schedule_page(
        (FIXTURES / "NBA_1960_games-october.html").read_bytes(),
        season=1960,
        schedule_url=(
            "https://www.basketball-reference.com/leagues/"
            "NBA_1960_games-october.html"
        ),
    )

    assert result["site_status"].tolist() == ["home", "neutral"]


def test_parser_rejects_unknown_overtime_marker():
    payload = (FIXTURES / "NBA_1960_games-october.html").read_text()
    payload = payload.replace("2OT", "3 extra periods")

    with pytest.raises(ValueError, match="unsupported overtime marker"):
        historical_bref.parse_schedule_page(
            payload.encode(),
            season=1960,
            schedule_url="https://example.test/schedule.html",
        )


class FakeResponse:
    def __init__(self, content, status_code=200):
        self.content = content
        self.status_code = status_code


class FakeSession:
    def __init__(self, responses=None, error=None):
        self.responses = responses or {}
        self.error = error
        self.headers = {}
        self.calls = []

    def get(self, url, *, timeout, verify):
        self.calls.append((url, timeout, verify))
        if self.error is not None:
            raise self.error
        return self.responses[url]


def fixture_response_map(*seasons):
    responses = {}
    for season in seasons:
        league = historical_bref.league_for_season(season)
        responses[historical_bref.index_url(season)] = FakeResponse(
            (FIXTURES / f"{league}_{season}_games.html").read_bytes()
        )
        for page in historical_bref.discover_month_pages(
            (FIXTURES / f"{league}_{season}_games.html").read_bytes(), season
        ):
            responses[page.url] = FakeResponse(
                (FIXTURES / f"{league}_{season}_games-october.html").read_bytes()
                if page.month == "october"
                else (FIXTURES / "BAA_1947_games-november.html").read_bytes()
            )
    return responses


def install_fetch_doubles(monkeypatch, session, sleeps):
    monkeypatch.setattr(
        historical_bref,
        "requests",
        SimpleNamespace(
            Session=lambda: session,
            RequestException=requests.RequestException,
        ),
    )
    monkeypatch.setattr(
        historical_bref, "time", SimpleNamespace(sleep=sleeps.append)
    )


def test_fetch_caches_index_before_discovering_months(tmp_path, monkeypatch):
    session = FakeSession(fixture_response_map(1947))
    install_fetch_doubles(monkeypatch, session, [])

    historical_bref.fetch(tmp_path, [1947], sleep_s=0.25)

    cache_dir = tmp_path / "historical_bref"
    assert cache.read_valid(
        cache_dir / "BAA_1947_games.html",
        lambda path: historical_bref.validate_index(path, 1947),
    ) == (FIXTURES / "BAA_1947_games.html").read_bytes()
    assert session.calls[0][0] == historical_bref.index_url(1947)
    assert session.calls[0][1:] == (30, True)
    assert session.headers["User-Agent"] == historical_bref.bref.USER_AGENT


def test_fetch_sleeps_only_between_real_requests_across_seasons(
    tmp_path,
    monkeypatch,
):
    session = FakeSession(fixture_response_map(1947, 1960))
    sleeps = []
    install_fetch_doubles(monkeypatch, session, sleeps)

    historical_bref.fetch(tmp_path, [1947, 1960], sleep_s=0.25)

    assert len(session.calls) == 6
    assert sleeps == [0.25] * 5


def test_fetch_cache_hits_make_no_requests_and_do_not_sleep(tmp_path, monkeypatch):
    first_session = FakeSession(fixture_response_map(1947))
    install_fetch_doubles(monkeypatch, first_session, [])
    historical_bref.fetch(tmp_path, [1947])
    session = FakeSession()
    sleeps = []
    install_fetch_doubles(monkeypatch, session, sleeps)

    historical_bref.fetch(tmp_path, [1947])

    assert session.calls == []
    assert sleeps == []


def test_fetch_stops_on_tls_error_without_publishing(tmp_path, monkeypatch):
    session = FakeSession(error=requests.exceptions.SSLError("bad certificate"))
    install_fetch_doubles(monkeypatch, session, [])

    with pytest.raises(cache.SourceAccessError, match="SSLError"):
        historical_bref.fetch(tmp_path, [1947])

    cache_dir = tmp_path / "historical_bref"
    assert not list(cache_dir.glob("*")) if cache_dir.exists() else True


def test_fetch_rejects_block_page_without_publishing(tmp_path, monkeypatch):
    session = FakeSession({
        historical_bref.index_url(1947): FakeResponse(b"blocked")
    })
    install_fetch_doubles(monkeypatch, session, [])

    with pytest.raises(ValueError, match="no regular-season month links"):
        historical_bref.fetch(tmp_path, [1947])

    cache_dir = tmp_path / "historical_bref"
    assert not any(path.name.endswith(".part") for path in cache_dir.glob("*"))
    assert not (cache_dir / "BAA_1947_games.html").exists()


def publish_season(tmp_path, season, *, omit_month=False):
    league = historical_bref.league_for_season(season)
    cache_dir = tmp_path / "historical_bref"
    index = FIXTURES / f"{league}_{season}_games.html"
    cache.publish_bytes(
        cache_dir / index.name,
        index.read_bytes(),
        {},
        lambda path: historical_bref.validate_index(path, season),
    )
    if not omit_month:
        page = historical_bref.discover_month_pages(
            index.read_bytes(), season
        )[0]
        month_name = "october" if season != 1947 else "november"
        month = FIXTURES / f"{league}_{season}_games-{month_name}.html"
        cache.publish_bytes(
            cache_dir / page.filename,
            month.read_bytes(),
            {},
            lambda path: historical_bref.validate_month(path, season, page.url),
        )


def test_load_season_rejects_missing_month_pair(tmp_path):
    publish_season(tmp_path, 1947, omit_month=True)

    with pytest.raises(cache.CacheIntegrityError, match="missing cache entry"):
        historical_bref.load_season(tmp_path, 1947)


def test_load_season_rejects_checksum_mismatch(tmp_path):
    publish_season(tmp_path, 1947)
    path = tmp_path / "historical_bref/BAA_1947_games.html"
    path.write_bytes(b"changed")

    with pytest.raises(cache.CacheIntegrityError, match="SHA-256"):
        historical_bref.load_season(tmp_path, 1947)


def test_fallback_ids_are_stable_across_input_order():
    records = pd.DataFrame({
        "game_date": [datetime.date(1946, 11, 12)] * 2,
        "schedule_url": ["https://example.test/b", "https://example.test/a"],
        "source_row": [1, 1],
        "away_team_name": ["Boston Celtics"] * 2,
        "home_team_name": ["Providence Steamrollers"] * 2,
        "boxscore_slug": [pd.NA, pd.NA],
        "attendance": [pd.NA, pd.NA],
        "league": ["BAA"] * 2,
        "overtime_marker": [""] * 2,
        "site_status": ["unknown"] * 2,
        "venue_name": [pd.NA, pd.NA],
        "attendance_status": ["missing"] * 2,
        "boxscore_url": [pd.NA, pd.NA],
        "game_remarks": [""] * 2,
    })

    forward = historical_bref._assign_game_ids(records)
    reverse = historical_bref._assign_game_ids(records.iloc[::-1])

    assert forward[["schedule_url", "game_id"]].equals(
        reverse[["schedule_url", "game_id"]]
    )
