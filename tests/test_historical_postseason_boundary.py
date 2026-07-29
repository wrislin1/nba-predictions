from pathlib import Path

import pytest

from nba_data import cache, historical_bref


FIXTURES = Path(__file__).parent / "fixtures/historical_postseason_boundary"


def publish_fixture_season(tmp_path, season):
    league = historical_bref.league_for_season(season)
    cache_dir = tmp_path / "historical_bref"
    index = FIXTURES / f"{league}_{season}_games.html"
    cache.publish_bytes(
        cache_dir / index.name,
        index.read_bytes(),
        {},
        lambda path: historical_bref.validate_index(path, season),
    )
    for page in historical_bref.discover_month_pages(index.read_bytes(), season):
        source = FIXTURES / page.filename
        cache.publish_bytes(
            cache_dir / page.filename,
            source.read_bytes(),
            {},
            lambda path, value=season, url=page.url: historical_bref.validate_month(
                path, value, url
            ),
        )


def test_load_season_excludes_rows_after_mixed_month_playoffs_separator(tmp_path):
    publish_fixture_season(tmp_path, 1960)

    result = historical_bref.load_season(tmp_path, 1960)

    assert result["game_id"].tolist() == [
        "bref-195910170AAA",
        "bref-196003010AAA",
    ]


def test_load_season_excludes_every_later_month_after_playoffs_separator(tmp_path):
    publish_fixture_season(tmp_path, 1960)

    result = historical_bref.load_season(tmp_path, 1960)

    assert "bref-196004010CCC" not in result["game_id"].tolist()


def test_load_season_still_requires_later_cached_months(tmp_path):
    publish_fixture_season(tmp_path, 1960)
    later_page = tmp_path / "historical_bref/NBA_1960_games-april.html"
    later_page.unlink()
    later_page.with_name(later_page.name + ".meta.json").unlink()

    with pytest.raises(cache.CacheIntegrityError, match="missing cache entry"):
        historical_bref.load_season(tmp_path, 1960)


def test_load_season_uses_1947_curated_playoff_boundary(tmp_path):
    publish_fixture_season(tmp_path, 1947)

    result = historical_bref.load_season(tmp_path, 1947)

    assert result["game_id"].tolist() == ["bref-194703310AAA"]


def test_load_season_fails_without_separator_or_curated_boundary(tmp_path):
    publish_fixture_season(tmp_path, 1961)

    with pytest.raises(ValueError, match="no postseason boundary"):
        historical_bref.load_season(tmp_path, 1961)
