from pathlib import Path

import pandas as pd
import pytest

from nba_data import cache, historical_bref, playoff_bref


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


def test_historical_rows_are_exact_regular_selector_complement(
    historical_boundary_cache,
):
    raw_dir = historical_boundary_cache
    regular = historical_bref.load_season(raw_dir, 1960)
    postseason = playoff_bref.load_historical_season(raw_dir, 1960)
    regular_keys = set(
        regular[["schedule_url", "source_row"]]
        .itertuples(index=False, name=None)
    )
    postseason_keys = set(
        postseason[["schedule_url", "source_row"]]
        .itertuples(index=False, name=None)
    )
    assert regular_keys.isdisjoint(postseason_keys)
    assert set(postseason["game_type"]) == {"playoff"}


def test_historical_duplicate_separators_keep_tiebreakers_in_complement(
    historical_boundary_cache,
):
    raw_dir = historical_boundary_cache
    regular = historical_bref.load_season(raw_dir, 1948)
    postseason = playoff_bref.load_historical_season(raw_dir, 1948)

    assert postseason.loc[
        postseason["game_type"].eq("tiebreaker"), "game_id"
    ].tolist() == ["bref-194803230CHS", "bref-194803250CHS"]
    assert set(postseason.loc[
        postseason["game_type"].eq("playoff"), "game_id"
    ]) == {"bref-194803230STB", "bref-194803250STB", "bref-194804010BLB"}
    assert set(
        regular[["schedule_url", "source_row"]]
        .itertuples(index=False, name=None)
    ).isdisjoint(
        postseason[["schedule_url", "source_row"]]
        .itertuples(index=False, name=None)
    )


def _classification_frame(remarks, site_status="unknown"):
    return pd.DataFrame({
        "season": [1953],
        "league": ["NBA"],
        "game_date": [pd.Timestamp("1952-11-28").date()],
        "away_team_name": ["Away"],
        "home_team_name": ["Home"],
        "away_pts": [90],
        "home_pts": [91],
        "overtime_marker": [""],
        "overtime_periods": [0],
        "site_status": [site_status],
        "venue_name": [pd.NA],
        "attendance": [pd.NA],
        "attendance_status": ["unknown"],
        "boxscore_url": [pd.NA],
        "boxscore_slug": [pd.NA],
        "schedule_url": ["https://example.test/schedule"],
        "game_remarks": [remarks],
        "source_row": [1],
        "game_id": ["bref-fallback-19521128-away-at-home-01"],
    })


@pytest.mark.parametrize(
    ("remarks", "expected_type"),
    [
        (" Protest; Does Not Count Toward Record ", "voided"),
        ("tIeBrEaKeR", "tiebreaker"),
    ],
)
def test_historical_type_remarks_are_classified_case_insensitively(
    remarks, expected_type
):
    result = playoff_bref.classify_historical_complement(
        _classification_frame(remarks)
    )

    assert result.loc[0, "game_type"] == expected_type
    assert result.loc[0, "site_status"] == "home"


@pytest.mark.parametrize("remarks", sorted({
    "Neutral Site",
    "At Saint Paul, MN",
    "At 69th Regiment Armory",
    "At Madison, WI",
    "At Indianapolis, IN",
    "At Oakland, CA",
    "At Chicago, IL (Chicago Arena)",
    "At Washington, DC (National Guard Armory)",
    "At Daly City, CA",
    "At Kingdome",
    "At Elkhart, IN",
    "At Detroit, MI (University of Detroit Fieldhouse)",
    "At Tempe, AZ",
}))
def test_historical_neutral_remarks_set_neutral_site_status(remarks):
    result = playoff_bref.classify_historical_complement(
        _classification_frame(remarks)
    )

    assert result.loc[0, "game_type"] == "playoff"
    assert result.loc[0, "site_status"] == "neutral"


@pytest.mark.parametrize(("remarks", "site_status", "expected_site_status"), [
    ("Round Robin", "unknown", "home"),
    ("Havlicek Stole the Ball", "unknown", "home"),
    ("The Willis Reed Game", "unknown", "home"),
    ("The Greatest Game Ever Played", "unknown", "home"),
    ("Mother's Day Massacre", "unknown", "home"),
    ("Round Robin", "home", "home"),
    ("", "unknown", "unknown"),
    (None, "home", "home"),
])
def test_historical_non_site_and_blank_remarks_preserve_or_normalize_site(
    remarks, site_status, expected_site_status
):
    result = playoff_bref.classify_historical_complement(
        _classification_frame(remarks, site_status=site_status)
    )

    assert result.loc[0, "game_type"] == "playoff"
    assert result.loc[0, "site_status"] == expected_site_status


def test_historical_complement_rejects_unrecognized_remark():
    with pytest.raises(ValueError, match="unrecognized scoped game remark"):
        playoff_bref.classify_historical_complement(
            _classification_frame("Unexpected exhibition")
        )
