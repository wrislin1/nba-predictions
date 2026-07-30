import datetime
import importlib.util
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pandas as pd
import pytest
import requests

from nba_data import cache, franchises, historical_bref, historical_build


FIXTURES = Path(__file__).parent / "fixtures/historical_bref"
_AUDIT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts/audit_historical_source.py"
)
_AUDIT_SPEC = importlib.util.spec_from_file_location(
    "audit_historical_source", _AUDIT_PATH
)
audit_historical_source = importlib.util.module_from_spec(_AUDIT_SPEC)
_AUDIT_SPEC.loader.exec_module(audit_historical_source)


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


FIXTURE_SPIKE_MONTH = {1947: "november", 1960: "october", 1983: "october"}


def publish_historical_fixture_season(raw_dir, *, season):
    league = historical_bref.league_for_season(season)
    month = FIXTURE_SPIKE_MONTH[season]
    cache_dir = raw_dir / "historical_bref"
    month_name = f"{league}_{season}_games-{month}.html"
    index_payload = (
        f'<html><body><a href="/leagues/{month_name}">'
        f"{month.title()}</a></body></html>"
    ).encode()
    cache.publish_bytes(
        cache_dir / historical_bref.index_filename(season),
        index_payload,
        {
            "provider": "basketball_reference",
            "resource": "historical_schedule_index",
            "league": league,
            "season": season,
            "url": historical_bref.index_url(season),
        },
        lambda path: historical_bref.validate_index(path, season),
    )
    month_url = f"{historical_bref.BASE_URL}/leagues/{month_name}"
    month_payload = (FIXTURES / month_name).read_bytes().replace(
        b"</tbody>",
        b"<tr class='thead'><th>Playoffs</th></tr></tbody>",
    )
    cache.publish_bytes(
        cache_dir / month_name,
        month_payload,
        {
            "provider": "basketball_reference",
            "resource": "historical_schedule_month",
            "league": league,
            "season": season,
            "month": month,
            "url": month_url,
        },
        lambda path: historical_bref.validate_month(path, season, month_url),
    )


def test_audit_reports_source_shape_and_ambiguities(tmp_path):
    publish_historical_fixture_season(tmp_path, season=1947)

    audit = audit_historical_source.audit_season(tmp_path, 1947)

    assert audit["season"] == 1947
    assert audit["league"] == "BAA"
    assert audit["games"] == 3
    assert audit["boxscore_links"] == 2
    assert audit["attendance"]["missing"] == 1
    assert audit["site_status"]["unknown"] == 1
    assert audit["teams"] == sorted(audit["teams"])


def test_audit_renderer_documents_source_boundaries_and_missingness():
    audits = [{
        "season": 1947,
        "league": "BAA",
        "games": 331,
        "date_min": "1946-11-01",
        "date_max": "1947-04-01",
        "teams": ["Boston Celtics", "New York Knicks"],
        "boxscore_links": 330,
        "venue_reported": 0,
        "attendance": {"missing": 331},
        "site_status": {"home": 331},
        "overtime_markers": {"": 300, "OT": 31},
        "nonblank_remarks": [],
        "schedule_urls": [
            "https://www.basketball-reference.com/leagues/BAA_1947_games-november.html"
        ],
    }]

    rendered = audit_historical_source.render_markdown(audits)

    assert "games-playoffs.html` is excluded" in rendered
    assert "exact `Playoffs` separator" in rendered
    assert "1947 and 1980 use a documented first-playoff-game fallback" in rendered
    assert "### 1947 BAA" in rendered
    assert "- Games: 331" in rendered
    assert "## Schema and statistic keys" in rendered
    assert "blank attendance | null + `missing`" in rendered
    assert "other remarks | `home` / `neutral` / `unknown`" in rendered


def test_audit_cli_renders_the_cached_spike_without_network(tmp_path):
    for season in (1947, 1960, 1983):
        publish_historical_fixture_season(tmp_path, season=season)
    output = tmp_path / "audit.md"

    subprocess.run([
        sys.executable,
        str(_AUDIT_PATH),
        "--raw-dir",
        str(tmp_path),
        "--output",
        str(output),
    ], check=True)

    rendered = output.read_text(encoding="utf-8")
    assert "### 1947 BAA" in rendered
    assert "### 1960 NBA" in rendered
    assert "### 1983 NBA" in rendered


@pytest.mark.integration
@pytest.mark.parametrize(
    ("season", "expected_games", "expected_teams"),
    [
        (
            1947,
            331,
            [
                "Boston Celtics", "Chicago Stags", "Cleveland Rebels",
                "Detroit Falcons", "New York Knicks", "Philadelphia Warriors",
                "Pittsburgh Ironmen", "Providence Steamrollers",
                "St. Louis Bombers", "Toronto Huskies", "Washington Capitols",
            ],
        ),
        (
            1960,
            300,
            [
                "Boston Celtics", "Cincinnati Royals", "Detroit Pistons",
                "Minneapolis Lakers", "New York Knicks", "Philadelphia Warriors",
                "St. Louis Hawks", "Syracuse Nationals",
            ],
        ),
        (
            1983,
            943,
            [
                "Atlanta Hawks", "Boston Celtics", "Chicago Bulls",
                "Cleveland Cavaliers", "Dallas Mavericks", "Denver Nuggets",
                "Detroit Pistons", "Golden State Warriors", "Houston Rockets",
                "Indiana Pacers", "Kansas City Kings", "Los Angeles Lakers",
                "Milwaukee Bucks", "New Jersey Nets", "New York Knicks",
                "Philadelphia 76ers", "Phoenix Suns", "Portland Trail Blazers",
                "San Antonio Spurs", "San Diego Clippers", "Seattle SuperSonics",
                "Utah Jazz", "Washington Bullets",
            ],
        ),
    ],
)
def test_spike_season_game_counts_and_source_team_names(
    season, expected_games, expected_teams
):
    frame = historical_bref.load_season(Path("data/raw"), season)

    assert len(frame) == expected_games
    assert frame["season"].eq(season).all()
    assert frame["game_id"].is_unique
    assert sorted(
        set(frame["away_team_name"]) | set(frame["home_team_name"])
    ) == expected_teams


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


@pytest.mark.integration
@pytest.mark.parametrize("season", historical_bref.HISTORICAL_SEASONS)
def test_every_historical_cached_season_matches_curated_count(season):
    counts = historical_build.load_expected_counts(
        Path("data/reference/historical_season_counts.csv")
    )

    frame = historical_bref.load_season(Path("data/raw"), season)

    assert len(frame) == counts[season]
    assert frame["game_id"].notna().all()
    assert frame["game_id"].is_unique
    assert frame["away_pts"].notna().all()
    assert frame["home_pts"].notna().all()


@pytest.mark.integration
def test_every_historical_source_team_name_resolves_through_franchise_table():
    pairs = set()
    for season in historical_bref.HISTORICAL_SEASONS:
        frame = historical_bref.load_season(Path("data/raw"), season)
        pairs.update((season, name) for name in frame["away_team_name"])
        pairs.update((season, name) for name in frame["home_team_name"])

    for season, name in sorted(pairs):
        assert franchises.resolve(name, season) in franchises.FRANCHISE_IDS


@pytest.mark.integration
@pytest.mark.parametrize(
    ("season", "game_id"),
    [
        (1948, "bref-194803230CHS"),
        (1948, "bref-194803250CHS"),
        (1950, "bref-195003200FTW"),
        (1950, "bref-195003210ROC"),
        (1956, "bref-195603150SYR"),
        (1956, "bref-195603160STL"),
        (1957, "bref-195703140STL"),
        (1957, "bref-195703160STL"),
    ],
)
def test_cached_historical_tiebreakers_are_excluded(season, game_id):
    frame = historical_bref.load_season(Path("data/raw"), season)

    assert game_id not in set(frame["game_id"])


@pytest.mark.integration
def test_cached_1953_voided_game_is_excluded_but_replay_is_retained():
    frame = historical_bref.load_season(Path("data/raw"), 1953)

    assert "bref-195211280MLH" not in set(frame["game_id"])
    assert "bref-195303110MLH" in set(frame["game_id"])


@pytest.mark.integration
def test_cached_1953_duplicate_separator_keeps_only_verified_carryovers():
    frame = historical_bref.load_season(Path("data/raw"), 1953)

    assert "bref-195303170SYR" in set(frame["game_id"])
    assert "bref-195303180MLH" in set(frame["game_id"])
    assert "bref-195303170NYK" not in set(frame["game_id"])


@pytest.mark.integration
def test_cached_1980_month_boundary_excludes_first_playoff_game():
    frame = historical_bref.load_season(Path("data/raw"), 1980)

    assert "bref-198004020PHI" not in set(frame["game_id"])


@pytest.mark.integration
@pytest.mark.parametrize(
    ("filename", "expected_rows"),
    [
        ("BAA_1948_games-march.html", (29, 33)),
        ("NBA_1950_games-march.html", (81, 85)),
        ("NBA_1953_games-march.html", (47, 51)),
        ("NBA_1957_games-march.html", (32, 35)),
    ],
)
def test_cached_verified_duplicate_separator_layouts_are_exact(
    filename, expected_rows
):
    payload = (Path("data/raw/historical_bref") / filename).read_bytes()
    schedule_url = f"{historical_bref.BASE_URL}/leagues/{filename}"

    assert historical_bref._playoff_separator_source_rows(
        payload, schedule_url
    ) == expected_rows


@pytest.mark.integration
def test_duplicate_separator_validation_rejects_unknown_missing_moved_and_new_rows():
    filename = "BAA_1948_games-march.html"
    payload = (Path("data/raw/historical_bref") / filename).read_bytes()
    schedule_url = f"{historical_bref.BASE_URL}/leagues/{filename}"
    separator = b"<tr class='thead'><th colspan='11'>Playoffs</th></tr>"

    with pytest.raises(ValueError, match="unverified duplicate Playoffs"):
        historical_bref._playoff_separator_source_rows(
            payload,
            "https://example.test/unverified-duplicate.html",
        )
    with pytest.raises(ValueError, match="verified duplicate Playoffs"):
        historical_bref._playoff_separator_source_rows(
            payload.replace(separator, b"", 1), schedule_url
        )
    with pytest.raises(ValueError, match="verified duplicate Playoffs"):
        historical_bref._playoff_separator_source_rows(
            payload.replace(
                b"<tbody>",
                b"<tbody><tr class='thead'><th>Header</th></tr>",
                1,
            ),
            schedule_url,
        )
    with pytest.raises(ValueError, match="verified duplicate Playoffs"):
        historical_bref._playoff_separator_source_rows(
            payload.replace(separator, separator + separator, 1),
            schedule_url,
        )


def test_audit_renderer_documents_verified_postseason_edge_cases():
    rendered = audit_historical_source.render_markdown([])

    assert "verified duplicate `Playoffs` layouts" in rendered
    assert "Tiebreaker" in rendered
    assert "does not count toward record" in rendered
    assert "1953 carryovers" in rendered
    assert "1947 and 1980 use a documented first-playoff-game fallback" in rendered
