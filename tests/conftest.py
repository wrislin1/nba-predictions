from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def repo_data_dir() -> Path:
    return REPO_ROOT / "data"


@pytest.fixture
def historical_boundary_cache(tmp_path):
    from nba_data import cache, historical_bref

    fixtures = REPO_ROOT / "tests/fixtures/historical_postseason_boundary"
    for season in (1947, 1948, 1960, 1961):
        league = historical_bref.league_for_season(season)
        index = fixtures / f"{league}_{season}_games.html"
        cache_dir = tmp_path / "historical_bref"
        cache.publish_bytes(
            cache_dir / index.name,
            index.read_bytes(),
            {},
            lambda path, value=season: historical_bref.validate_index(
                path, value
            ),
        )
        for page in historical_bref.discover_month_pages(
            index.read_bytes(), season
        ):
            source = fixtures / page.filename
            cache.publish_bytes(
                cache_dir / page.filename,
                source.read_bytes(),
                {},
                lambda path, value=season, url=page.url: (
                    historical_bref.validate_month(path, value, url)
                ),
            )
    return tmp_path


@pytest.fixture
def seeded_playoff_cache(tmp_path):
    import hashlib

    from nba_data import cache, playoff_bref

    fixtures = REPO_ROOT / "tests/fixtures/playoff_bref"
    raw_dir = tmp_path / "raw"
    for season in (1984, 2020):
        name = playoff_bref.playoff_filename(season)
        cache.publish_bytes(
            raw_dir / "playoff_bref" / name,
            (fixtures / name).read_bytes(),
            {
                "provider": "basketball_reference",
                "resource": "playoff_schedule",
                "league": "NBA",
                "season": season,
                "month": None,
                "url": playoff_bref.playoff_url(season),
            },
            lambda path, value=season: (
                playoff_bref.validate_dedicated_page(path, value)
            ),
        )
    play_in_payload = (fixtures / "NBA_2020_games-august.html").read_bytes()
    cache.publish_bytes(
        raw_dir / "playoff_bref/NBA_2020_games-august.html",
        play_in_payload,
        {
            "provider": "basketball_reference",
            "resource": "play_in_schedule_month",
            "league": "NBA",
            "season": 2020,
            "month": "august",
            "url": (
                "https://www.basketball-reference.com/leagues/"
                "NBA_2020_games-august.html"
            ),
            "seeded_from": "raw/bref/NBA_2020_games-august.html",
            "seed_operation": "verified_cache_copy",
            "source_sha256": hashlib.sha256(play_in_payload).hexdigest(),
        },
        lambda path: playoff_bref.validate_seeded_play_in_page(path, 2020),
    )
    return raw_dir
