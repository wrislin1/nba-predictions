from datetime import date
import hashlib
from io import BytesIO
import json
from pathlib import Path

import pandas as pd
import pytest
import pyarrow.parquet as pq

from nba_data import (
    bref,
    cache,
    franchises,
    historical_bref,
    historical_build,
    hoopr,
    playoff_bref,
    playoff_build,
    publication,
)


COUNT_COLUMNS = [
    "season",
    "playoff_games",
    "play_in_games",
    "tiebreaker_games",
    "voided_games",
    "total_games",
    "first_postseason_date",
    "last_postseason_date",
    "source_url",
    "note",
]
SITE_RULE_COLUMNS = [
    "season",
    "site_status",
    "expected_matches",
    "source_url",
    "note",
]
FIXED_TYPE_COUNTS = {
    1948: {"tiebreaker_games": 2},
    1950: {"tiebreaker_games": 2},
    1953: {"voided_games": 1},
    1956: {"tiebreaker_games": 2},
    1957: {"tiebreaker_games": 2},
    2020: {"play_in_games": 1},
    **{
        season: {"play_in_games": 6}
        for season in range(2021, 2027)
    },
}
EXPECTED_PLAYOFF_COLUMNS = [
    "game_id",
    "season",
    "league",
    "game_date",
    "game_type",
    "away_team_name",
    "home_team_name",
    "away_franchise_id",
    "home_franchise_id",
    "away_pts",
    "home_pts",
    "home_margin",
    "overtime_periods",
    "site_status",
    "venue_name",
    "attendance",
    "attendance_status",
    "boxscore_url",
    "schedule_url",
]
EXPECTED_PLAYOFF_DTYPES = {
    "game_id": "string",
    "season": "int64",
    "league": "string",
    "game_type": "string",
    "away_team_name": "string",
    "home_team_name": "string",
    "away_franchise_id": "string",
    "home_franchise_id": "string",
    "away_pts": "int64",
    "home_pts": "int64",
    "home_margin": "int64",
    "overtime_periods": "int64",
    "site_status": "string",
    "venue_name": "string",
    "attendance": "Int64",
    "attendance_status": "string",
    "boxscore_url": "string",
    "schedule_url": "string",
}
EXPECTED_CROSS_SOURCE_KEY = [
    "season",
    "game_date",
    "game_type",
    "away_franchise_id",
    "home_franchise_id",
    "away_pts",
    "home_pts",
]
EXPECTED_REGULAR_OVERLAP_KEY = [
    "season",
    "game_date",
    "away_franchise_id",
    "home_franchise_id",
    "away_pts",
    "home_pts",
]


@pytest.fixture
def valid_count_frame():
    rows = []
    for season in range(1947, 2027):
        fixed = FIXED_TYPE_COUNTS.get(season, {})
        playoff_games = 10
        play_in_games = fixed.get("play_in_games", 0)
        tiebreaker_games = fixed.get("tiebreaker_games", 0)
        voided_games = fixed.get("voided_games", 0)
        rows.append(
            {
                "season": season,
                "playoff_games": playoff_games,
                "play_in_games": play_in_games,
                "tiebreaker_games": tiebreaker_games,
                "voided_games": voided_games,
                "total_games": (
                    playoff_games
                    + play_in_games
                    + tiebreaker_games
                    + voided_games
                ),
                "first_postseason_date": f"{season}-04-01",
                "last_postseason_date": f"{season}-06-01",
                "source_url": (
                    "https://www.basketball-reference.com/playoffs/"
                    f"{'BAA' if season <= 1949 else 'NBA'}_{season}.html"
                ),
                "note": "Independently transcribed league summary.",
            }
        )
    return pd.DataFrame(rows, columns=COUNT_COLUMNS)


@pytest.fixture
def valid_site_rule_frame():
    return pd.DataFrame(
        [
            {
                "season": 2020,
                "site_status": "neutral",
                "expected_matches": 84,
                "source_url": (
                    "https://pr.nba.com/"
                    "nba-nbpa-finalize-plan-2019-20-season-restart/"
                ),
                "note": (
                    "All remaining 2019-20 games were played at the three "
                    "ESPN Wide World of Sports venues at Walt Disney World."
                ),
            }
        ],
        columns=SITE_RULE_COLUMNS,
    )


def _write_csv(tmp_path, name, frame):
    path = tmp_path / name
    frame.to_csv(path, index=False)
    return path


def test_load_expected_counts_requires_exact_contract(
    tmp_path, valid_count_frame
):
    expected = playoff_build.load_expected_counts(
        _write_csv(tmp_path, "counts.csv", valid_count_frame)
    )

    assert tuple(expected) == tuple(range(1947, 2027))
    assert expected[2020].play_in_games == 1
    assert expected[2021].play_in_games == 6
    assert expected[1953].voided_games == 1
    assert expected[1947] == playoff_build.ExpectedSeason(
        season=1947,
        playoff_games=10,
        play_in_games=0,
        tiebreaker_games=0,
        voided_games=0,
        total_games=10,
        first_postseason_date=date(1947, 4, 1),
        last_postseason_date=date(1947, 6, 1),
        source_url=(
            "https://www.basketball-reference.com/playoffs/BAA_1947.html"
        ),
        note="Independently transcribed league summary.",
    )


def test_load_expected_counts_rejects_bad_sum(
    tmp_path, valid_count_frame
):
    valid_count_frame.loc[
        valid_count_frame["season"].eq(1984), "total_games"
    ] += 1

    with pytest.raises(
        playoff_build.PlayoffValidationError,
        match="type counts must sum to total_games",
    ):
        playoff_build.load_expected_counts(
            _write_csv(tmp_path, "counts.csv", valid_count_frame)
        )


def test_load_expected_counts_rejects_wrong_column_order(
    tmp_path, valid_count_frame
):
    columns = COUNT_COLUMNS.copy()
    columns[0], columns[1] = columns[1], columns[0]

    with pytest.raises(
        playoff_build.PlayoffValidationError,
        match="count columns must be",
    ):
        playoff_build.load_expected_counts(
            _write_csv(
                tmp_path, "counts.csv", valid_count_frame.loc[:, columns]
            )
        )


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "extra"])
def test_load_expected_counts_rejects_wrong_season_coverage(
    tmp_path, valid_count_frame, mutation
):
    if mutation == "duplicate":
        valid_count_frame.loc[
            valid_count_frame["season"].eq(2026), "season"
        ] = 2025
    elif mutation == "missing":
        valid_count_frame = valid_count_frame[
            ~valid_count_frame["season"].eq(2026)
        ]
    else:
        extra = valid_count_frame.iloc[[-1]].copy()
        extra["season"] = 2027
        valid_count_frame = pd.concat(
            [valid_count_frame, extra], ignore_index=True
        )

    with pytest.raises(
        playoff_build.PlayoffValidationError,
        match="counts must cover exactly 1947..2026",
    ):
        playoff_build.load_expected_counts(
            _write_csv(tmp_path, "counts.csv", valid_count_frame)
        )


@pytest.mark.parametrize("bad_value", ["1.5", "not-an-integer"])
def test_load_expected_counts_rejects_non_integer_counts(
    tmp_path, valid_count_frame, bad_value
):
    valid_count_frame["playoff_games"] = valid_count_frame[
        "playoff_games"
    ].astype("object")
    valid_count_frame.loc[
        valid_count_frame["season"].eq(1984), "playoff_games"
    ] = bad_value

    with pytest.raises(
        playoff_build.PlayoffValidationError,
        match="count fields must be integers",
    ):
        playoff_build.load_expected_counts(
            _write_csv(tmp_path, "counts.csv", valid_count_frame)
        )


def test_load_expected_counts_rejects_negative_counts(
    tmp_path, valid_count_frame
):
    valid_count_frame.loc[
        valid_count_frame["season"].eq(1984), "play_in_games"
    ] = -1

    with pytest.raises(
        playoff_build.PlayoffValidationError,
        match="type counts cannot be negative",
    ):
        playoff_build.load_expected_counts(
            _write_csv(tmp_path, "counts.csv", valid_count_frame)
        )


@pytest.mark.parametrize("column", ["playoff_games", "total_games"])
def test_load_expected_counts_requires_positive_game_totals(
    tmp_path, valid_count_frame, column
):
    valid_count_frame.loc[
        valid_count_frame["season"].eq(1984), column
    ] = 0

    with pytest.raises(
        playoff_build.PlayoffValidationError,
        match="playoff_games and total_games must be positive",
    ):
        playoff_build.load_expected_counts(
            _write_csv(tmp_path, "counts.csv", valid_count_frame)
        )


@pytest.mark.parametrize("column", ["source_url", "note"])
@pytest.mark.parametrize("bad_value", [None, " "])
def test_load_expected_counts_rejects_missing_provenance(
    tmp_path, valid_count_frame, column, bad_value
):
    valid_count_frame.loc[
        valid_count_frame["season"].eq(1984), column
    ] = bad_value

    with pytest.raises(
        playoff_build.PlayoffValidationError,
        match="counts require nonblank source URL and note",
    ):
        playoff_build.load_expected_counts(
            _write_csv(tmp_path, "counts.csv", valid_count_frame)
        )


def test_load_expected_counts_rejects_non_https_url(
    tmp_path, valid_count_frame
):
    valid_count_frame.loc[
        valid_count_frame["season"].eq(1984), "source_url"
    ] = "http://example.org/1984"

    with pytest.raises(
        playoff_build.PlayoffValidationError,
        match="count source URLs must use HTTPS",
    ):
        playoff_build.load_expected_counts(
            _write_csv(tmp_path, "counts.csv", valid_count_frame)
        )


def test_load_expected_counts_rejects_invalid_date(
    tmp_path, valid_count_frame
):
    valid_count_frame.loc[
        valid_count_frame["season"].eq(1984), "first_postseason_date"
    ] = "1984-02-30"

    with pytest.raises(
        playoff_build.PlayoffValidationError,
        match="postseason dates must use ISO format",
    ):
        playoff_build.load_expected_counts(
            _write_csv(tmp_path, "counts.csv", valid_count_frame)
        )


def test_load_expected_counts_rejects_reversed_date_window(
    tmp_path, valid_count_frame
):
    valid_count_frame.loc[
        valid_count_frame["season"].eq(1984), "first_postseason_date"
    ] = "1984-06-02"

    with pytest.raises(
        playoff_build.PlayoffValidationError,
        match="first_postseason_date must not follow last_postseason_date",
    ):
        playoff_build.load_expected_counts(
            _write_csv(tmp_path, "counts.csv", valid_count_frame)
        )


@pytest.mark.parametrize(
    ("season", "column"),
    [
        (1948, "tiebreaker_games"),
        (1950, "tiebreaker_games"),
        (1953, "voided_games"),
        (1956, "tiebreaker_games"),
        (1957, "tiebreaker_games"),
        (2020, "play_in_games"),
        (2021, "play_in_games"),
        (2022, "play_in_games"),
        (2023, "play_in_games"),
        (2024, "play_in_games"),
        (2025, "play_in_games"),
        (2026, "play_in_games"),
    ],
)
def test_load_expected_counts_rejects_each_fixed_edge_count_violation(
    tmp_path, valid_count_frame, season, column
):
    row = valid_count_frame["season"].eq(season)
    valid_count_frame.loc[row, column] += 1
    valid_count_frame.loc[row, "total_games"] += 1

    with pytest.raises(
        playoff_build.PlayoffValidationError,
        match=f"{season} {column} must be",
    ):
        playoff_build.load_expected_counts(
            _write_csv(tmp_path, "counts.csv", valid_count_frame)
        )


@pytest.mark.parametrize(
    ("season", "column"),
    [
        (1947, "tiebreaker_games"),
        (1953, "tiebreaker_games"),
        (1947, "voided_games"),
        (2020, "voided_games"),
        (1947, "play_in_games"),
        (2019, "play_in_games"),
    ],
)
def test_load_expected_counts_rejects_unspecified_edge_count(
    tmp_path, valid_count_frame, season, column
):
    row = valid_count_frame["season"].eq(season)
    valid_count_frame.loc[row, column] = 1
    valid_count_frame.loc[row, "total_games"] += 1

    with pytest.raises(
        playoff_build.PlayoffValidationError,
        match=f"{season} {column} must be 0",
    ):
        playoff_build.load_expected_counts(
            _write_csv(tmp_path, "counts.csv", valid_count_frame)
        )


def test_load_site_rules_requires_exact_contract(
    tmp_path, valid_site_rule_frame
):
    rules = playoff_build.load_site_rules(
        _write_csv(tmp_path, "rules.csv", valid_site_rule_frame)
    )

    assert rules == {
        2020: playoff_build.SiteRule(
            season=2020,
            site_status="neutral",
            expected_matches=84,
            source_url=(
                "https://pr.nba.com/"
                "nba-nbpa-finalize-plan-2019-20-season-restart/"
            ),
            note=(
                "All remaining 2019-20 games were played at the three "
                "ESPN Wide World of Sports venues at Walt Disney World."
            ),
        )
    }


def test_load_site_rules_rejects_wrong_column_order(
    tmp_path, valid_site_rule_frame
):
    columns = SITE_RULE_COLUMNS.copy()
    columns[0], columns[1] = columns[1], columns[0]

    with pytest.raises(
        playoff_build.PlayoffValidationError,
        match="site rule columns must be",
    ):
        playoff_build.load_site_rules(
            _write_csv(
                tmp_path, "rules.csv", valid_site_rule_frame.loc[:, columns]
            )
        )


@pytest.mark.parametrize(
    ("column", "bad_value", "message"),
    [
        ("season", 2021, "site rules must contain only season 2020"),
        ("site_status", "home", "site rule status must be neutral"),
        ("expected_matches", 83, "2020 site rule must match 84 games"),
    ],
)
def test_load_site_rules_rejects_wrong_fixed_value(
    tmp_path, valid_site_rule_frame, column, bad_value, message
):
    valid_site_rule_frame.loc[0, column] = bad_value

    with pytest.raises(playoff_build.PlayoffValidationError, match=message):
        playoff_build.load_site_rules(
            _write_csv(tmp_path, "rules.csv", valid_site_rule_frame)
        )


def test_load_site_rules_rejects_duplicate_rows(
    tmp_path, valid_site_rule_frame
):
    valid_site_rule_frame = pd.concat(
        [valid_site_rule_frame, valid_site_rule_frame], ignore_index=True
    )

    with pytest.raises(
        playoff_build.PlayoffValidationError,
        match="site rules must contain exactly one row",
    ):
        playoff_build.load_site_rules(
            _write_csv(tmp_path, "rules.csv", valid_site_rule_frame)
        )


@pytest.mark.parametrize("bad_value", [None, " "])
def test_load_site_rules_rejects_missing_or_blank_status(
    tmp_path, valid_site_rule_frame, bad_value
):
    valid_site_rule_frame.loc[0, "site_status"] = bad_value

    with pytest.raises(
        playoff_build.PlayoffValidationError,
        match="site rule status must be neutral",
    ):
        playoff_build.load_site_rules(
            _write_csv(tmp_path, "rules.csv", valid_site_rule_frame)
        )


@pytest.mark.parametrize("column", ["source_url", "note"])
@pytest.mark.parametrize("bad_value", [None, " "])
def test_load_site_rules_rejects_missing_provenance(
    tmp_path, valid_site_rule_frame, column, bad_value
):
    valid_site_rule_frame.loc[0, column] = bad_value

    with pytest.raises(
        playoff_build.PlayoffValidationError,
        match="site rules require nonblank source URL and note",
    ):
        playoff_build.load_site_rules(
            _write_csv(tmp_path, "rules.csv", valid_site_rule_frame)
        )


def test_load_site_rules_rejects_non_https_url(
    tmp_path, valid_site_rule_frame
):
    valid_site_rule_frame.loc[0, "source_url"] = "http://example.org/rule"

    with pytest.raises(
        playoff_build.PlayoffValidationError,
        match="site rule source URLs must use HTTPS",
    ):
        playoff_build.load_site_rules(
            _write_csv(tmp_path, "rules.csv", valid_site_rule_frame)
        )


def test_apply_site_rules_changes_only_site_status():
    games = pd.DataFrame(
        [
            {
                "season": 2020,
                "site_status": "home",
                "game_id": "one",
                "margin": 5,
            },
            {
                "season": 2020,
                "site_status": "unknown",
                "game_id": "two",
                "margin": -3,
            },
            {
                "season": 2021,
                "site_status": "home",
                "game_id": "three",
                "margin": 1,
            },
        ]
    )
    rules = {
        2020: playoff_build.SiteRule(
            season=2020,
            site_status="neutral",
            expected_matches=2,
            source_url="https://example.org/rule",
            note="Authoritative neutral-site rule.",
        )
    }

    result, matches = playoff_build.apply_site_rules(games, rules)

    expected = games.copy()
    expected.loc[[0, 1], "site_status"] = "neutral"
    pd.testing.assert_frame_equal(result, expected)
    pd.testing.assert_frame_equal(
        games,
        pd.DataFrame(
            [
                {
                    "season": 2020,
                    "site_status": "home",
                    "game_id": "one",
                    "margin": 5,
                },
                {
                    "season": 2020,
                    "site_status": "unknown",
                    "game_id": "two",
                    "margin": -3,
                },
                {
                    "season": 2021,
                    "site_status": "home",
                    "game_id": "three",
                    "margin": 1,
                },
            ]
        ),
    )
    assert matches == {2020: 2}


def test_apply_site_rules_rejects_wrong_match_count():
    games = pd.DataFrame(
        [{"season": 2020, "site_status": "home", "game_id": "one"}]
    )
    rules = {
        2020: playoff_build.SiteRule(
            season=2020,
            site_status="neutral",
            expected_matches=2,
            source_url="https://example.org/rule",
            note="Authoritative neutral-site rule.",
        )
    }

    with pytest.raises(
        playoff_build.PlayoffValidationError,
        match="site rule 2020 matched 1, expected 2",
    ):
        playoff_build.apply_site_rules(games, rules)


@pytest.mark.parametrize("status", ["away", "", None])
def test_apply_site_rules_rejects_invalid_source_status(status):
    games = pd.DataFrame(
        [{"season": 2020, "site_status": status, "game_id": "one"}]
    )
    rules = {
        2020: playoff_build.SiteRule(
            season=2020,
            site_status="neutral",
            expected_matches=1,
            source_url="https://example.org/rule",
            note="Authoritative neutral-site rule.",
        )
    }

    with pytest.raises(
        playoff_build.PlayoffValidationError,
        match="site rule 2020 selected invalid site_status",
    ):
        playoff_build.apply_site_rules(games, rules)


def test_committed_playoff_references_are_valid(repo_data_dir):
    counts = playoff_build.load_expected_counts(
        repo_data_dir / "reference/playoff_season_counts.csv"
    )
    rules = playoff_build.load_site_rules(
        repo_data_dir / "reference/playoff_site_rules.csv"
    )
    assert tuple(counts) == tuple(range(1947, 2027))
    assert counts[1954].playoff_games == 23
    assert counts[1954].first_postseason_date == date(1954, 3, 16)
    assert set(rules) == {2020}


def _empty_regular_publication() -> pd.DataFrame:
    return pd.DataFrame(columns=EXPECTED_REGULAR_OVERLAP_KEY)


def _synthetic_source_rows(
    expected_counts: dict[int, playoff_build.ExpectedSeason],
) -> dict[int, pd.DataFrame]:
    rows_by_season: dict[int, pd.DataFrame] = {}
    sequence = 0
    for season, expected in expected_counts.items():
        rows = []
        type_counts = {
            "playoff": expected.playoff_games,
            "play_in": expected.play_in_games,
            "tiebreaker": expected.tiebreaker_games,
            "voided": expected.voided_games,
        }
        for game_type, count in type_counts.items():
            for type_index in range(count):
                sequence += 1
                is_voided = game_type == "voided"
                game_id = (
                    "bref-195211280MLH"
                    if is_voided
                    else f"synthetic-{season}-{game_type}-{type_index:03d}"
                )
                rows.append({
                    "game_id": game_id,
                    "season": season,
                    "league": "BAA" if season <= 1949 else "NBA",
                    "game_date": (
                        date(1952, 11, 28)
                        if is_voided
                        else expected.first_postseason_date
                    ),
                    "game_type": game_type,
                    "away_team_name": "New York Knicks",
                    "home_team_name": "Boston Celtics",
                    "away_franchise_id": "NYK",
                    "home_franchise_id": "BOS",
                    "away_pts": 70 + sequence,
                    "home_pts": 80 + sequence,
                    "home_margin": 10,
                    "overtime_marker": "",
                    "overtime_periods": 0,
                    "site_status": "neutral" if season == 2020 else "home",
                    "venue_name": "Synthetic Arena",
                    "attendance": 10_000,
                    "attendance_status": "reported_positive",
                    "boxscore_url": (
                        pd.NA
                        if is_voided
                        else "https://www.basketball-reference.com/"
                        f"boxscores/{season}01010AAA.html"
                    ),
                    "schedule_url": (
                        "https://www.basketball-reference.com/"
                        f"leagues/NBA_{season}_games.html"
                    ),
                    "game_remarks": "",
                    "source_row": type_index + 1,
                })
        rows_by_season[season] = pd.DataFrame(rows)
    return rows_by_season


def _typed_games(source_rows: dict[int, pd.DataFrame]) -> pd.DataFrame:
    source = pd.concat(source_rows.values(), ignore_index=True)
    games = source.loc[:, EXPECTED_PLAYOFF_COLUMNS].copy()
    for column, dtype in EXPECTED_PLAYOFF_DTYPES.items():
        games[column] = games[column].astype(dtype)
    return games.sort_values(
        ["season", "game_date", "game_id"], kind="stable"
    ).reset_index(drop=True)


def _validation_arguments(
    expected_counts: dict[int, playoff_build.ExpectedSeason],
    source_rows: dict[int, pd.DataFrame],
) -> dict:
    games = _typed_games(source_rows)
    source = pd.concat(source_rows.values(), ignore_index=True)
    hoopr_games = games.loc[
        games["season"].between(2020, 2026)
        & games["game_type"].isin(["playoff", "play_in"]),
        EXPECTED_CROSS_SOURCE_KEY,
    ].copy()
    return {
        "games": games,
        "expected_counts": expected_counts,
        "evidence": source[["game_id", "overtime_marker"]].copy(),
        "hoopr_games": hoopr_games,
        "modern_regular": _empty_regular_publication(),
        "historical_regular": _empty_regular_publication(),
        "site_rule_matches": {2020: 84},
    }


@pytest.fixture
def complete_validation_case(repo_data_dir):
    expected = playoff_build.load_expected_counts(
        repo_data_dir / "reference/playoff_season_counts.csv"
    )
    source_rows = _synthetic_source_rows(expected)
    return _validation_arguments(expected, source_rows)


def _copy_validation_arguments(arguments: dict) -> dict:
    return {
        key: (
            value.copy()
            if isinstance(value, (pd.DataFrame, dict))
            else value
        )
        for key, value in arguments.items()
    }


def mutate_validation_case(arguments: dict, mutation: str) -> dict:
    mutated = _copy_validation_arguments(arguments)
    games = mutated["games"]
    evidence = mutated["evidence"]
    historical_index = games.index[games["season"].eq(1947)][0]
    voided_index = games.index[games["game_type"].eq("voided")][0]

    if mutation == "duplicate_column":
        mutated["games"] = pd.concat(
            [games, games[["game_id"]]], axis=1
        )
    elif mutation == "wrong_dtype":
        games["season"] = games["season"].astype("Int64")
    elif mutation == "missing_season":
        removed_ids = games.loc[
            games["season"].eq(1947), "game_id"
        ]
        mutated["games"] = games.loc[~games["season"].eq(1947)].copy()
        mutated["evidence"] = evidence.loc[
            ~evidence["game_id"].isin(removed_ids)
        ].copy()
    elif mutation == "wrong_league":
        games.loc[historical_index, "league"] = "NBA"
    elif mutation == "unknown_game_type":
        games.loc[historical_index, "game_type"] = "preseason"
    elif mutation == "duplicate_game_id":
        games.loc[historical_index + 1, "game_id"] = games.loc[
            historical_index, "game_id"
        ]
    elif mutation == "null_required":
        games.loc[historical_index, "schedule_url"] = pd.NA
    elif mutation == "same_source_team":
        games.loc[historical_index, "home_team_name"] = games.loc[
            historical_index, "away_team_name"
        ]
    elif mutation == "same_franchise":
        games.loc[historical_index, "home_franchise_id"] = games.loc[
            historical_index, "away_franchise_id"
        ]
    elif mutation == "negative_score":
        games.loc[historical_index, "away_pts"] = -1
        games.loc[historical_index, "home_margin"] = (
            games.loc[historical_index, "home_pts"] + 1
        )
    elif mutation == "tied_score":
        games.loc[historical_index, "home_pts"] = games.loc[
            historical_index, "away_pts"
        ]
        games.loc[historical_index, "home_margin"] = 0
    elif mutation == "bad_margin":
        games.loc[historical_index, "home_margin"] += 1
    elif mutation == "negative_overtime":
        games.loc[historical_index, "overtime_periods"] = -1
    elif mutation == "marker_mismatch":
        game_id = games.loc[historical_index, "game_id"]
        evidence.loc[evidence["game_id"].eq(game_id), "overtime_marker"] = "5OT"
    elif mutation == "bad_attendance_status":
        games.loc[historical_index, "attendance_status"] = "missing"
    elif mutation == "bad_site_status":
        games.loc[historical_index, "site_status"] = "away"
    elif mutation == "bad_count":
        games.loc[historical_index, "game_type"] = "tiebreaker"
    elif mutation == "outside_window":
        games.loc[historical_index, "game_date"] = date(1947, 1, 1)
    elif mutation == "bad_voided_exemption":
        old_id = games.loc[voided_index, "game_id"]
        games.loc[voided_index, "game_id"] = "bref-wrong-voided"
        evidence.loc[
            evidence["game_id"].eq(old_id), "game_id"
        ] = "bref-wrong-voided"
    elif mutation in {
        "modern_regular_overlap",
        "historical_regular_overlap",
    }:
        overlap = games.loc[[historical_index],
                            EXPECTED_REGULAR_OVERLAP_KEY].copy()
        overlap["game_id"] = games.loc[historical_index, "game_id"]
        publication = mutation.removesuffix("_overlap")
        mutated[publication] = overlap
    elif mutation == "evidence_missing":
        mutated["evidence"] = evidence.iloc[1:].copy()
    elif mutation == "evidence_extra":
        extra = pd.DataFrame([{
            "game_id": "synthetic-evidence-extra",
            "overtime_marker": "",
        }])
        mutated["evidence"] = pd.concat(
            [evidence, extra], ignore_index=True
        )
    elif mutation == "evidence_duplicate":
        mutated["evidence"] = pd.concat(
            [evidence, evidence.iloc[[0]]], ignore_index=True
        )
    elif mutation == "hoopr_missing":
        mutated["hoopr_games"] = mutated["hoopr_games"].iloc[1:].copy()
    elif mutation == "hoopr_extra":
        extra = mutated["hoopr_games"].iloc[[0]].copy()
        extra["away_pts"] += 1_000_000
        mutated["hoopr_games"] = pd.concat(
            [mutated["hoopr_games"], extra], ignore_index=True
        )
    elif mutation == "wrong_2020_site_matches":
        mutated["site_rule_matches"] = {2020: 83}
    elif mutation == "wrong_2020_neutral_row":
        index_2020 = games.index[games["season"].eq(2020)][0]
        games.loc[index_2020, "site_status"] = "home"
    elif mutation == "wrong_2020_row_count":
        index_2020 = games.index[games["season"].eq(2020)][0]
        games.loc[index_2020, "season"] = 2019
    else:
        raise AssertionError(f"unknown mutation {mutation}")
    return mutated


def _seed_assembly_inputs(data_dir: Path) -> None:
    parquet_dir = data_dir / "parquet"
    parquet_dir.mkdir(parents=True)
    empty = _empty_regular_publication()
    empty.to_parquet(parquet_dir / "games.parquet", index=False)
    empty.to_parquet(
        parquet_dir / "historical_games.parquet", index=False
    )


@pytest.mark.parametrize(
    ("column", "malformed"),
    [
        ("season", 1947.5),
        ("season", True),
        ("away_pts", 91.5),
        ("away_pts", True),
        ("home_pts", 101.5),
        ("home_pts", False),
        ("home_margin", 10.5),
        ("home_margin", True),
        ("overtime_periods", 1.5),
        ("overtime_periods", False),
    ],
)
def test_enforce_schema_rejects_malformed_source_integers(
    complete_validation_case, column, malformed
):
    source = complete_validation_case["games"].iloc[[0]].copy()
    source[column] = malformed

    with pytest.raises(
        playoff_build.PlayoffValidationError,
        match="source integer",
    ):
        playoff_build._enforce_schema(source)


def test_assemble_resolves_franchises_and_enforces_schema(
    tmp_path, monkeypatch, repo_data_dir
):
    expected = playoff_build.load_expected_counts(
        repo_data_dir / "reference/playoff_season_counts.csv"
    )
    source_rows = _synthetic_source_rows(expected)
    _seed_assembly_inputs(tmp_path)
    reference_dir = tmp_path / "reference"
    reference_dir.mkdir()
    for name in [
        "playoff_season_counts.csv",
        "playoff_site_rules.csv",
    ]:
        (reference_dir / name).write_bytes(
            (repo_data_dir / "reference" / name).read_bytes()
        )
    monkeypatch.setattr(
        playoff_build.playoff_bref,
        "load_season",
        lambda raw_dir, season: source_rows[season].copy(),
    )

    def fake_hoopr_loader(raw_dir, season):
        return _typed_games(source_rows).loc[
            lambda frame: frame["season"].eq(season)
            & frame["game_type"].isin(["playoff", "play_in"]),
            EXPECTED_CROSS_SOURCE_KEY,
        ].reset_index(drop=True)

    monkeypatch.setattr(
        playoff_build.hoopr, "load_postseason", fake_hoopr_loader
    )

    games, report, evidence = playoff_build.assemble(tmp_path)

    assert games.columns.tolist() == EXPECTED_PLAYOFF_COLUMNS
    assert games["home_margin"].equals(
        games["home_pts"] - games["away_pts"]
    )
    assert games.sort_values(
        ["season", "game_date", "game_id"], kind="stable"
    ).reset_index(drop=True).equals(games)
    assert games["away_team_name"].eq("New York Knicks").all()
    assert games["home_team_name"].eq("Boston Celtics").all()
    assert games["away_franchise_id"].eq("NYK").all()
    assert games["home_franchise_id"].eq("BOS").all()
    assert games["game_date"].map(lambda value: type(value) is date).all()
    for column, dtype in EXPECTED_PLAYOFF_DTYPES.items():
        assert str(games[column].dtype) == dtype
    assert report["season_range"] == [1947, 2026]
    assert set(evidence) == {
        "source_rows",
        "hoopr_games",
        "modern_regular",
        "historical_regular",
        "site_rule_matches",
    }


def test_assemble_rejects_malformed_season_before_franchise_resolution(
    tmp_path, monkeypatch, repo_data_dir
):
    expected = playoff_build.load_expected_counts(
        repo_data_dir / "reference/playoff_season_counts.csv"
    )
    source_rows = _synthetic_source_rows(expected)
    source_rows[1947]["season"] = source_rows[1947]["season"].astype(
        "object"
    )
    source_rows[1947].loc[0, "season"] = True
    monkeypatch.setattr(
        playoff_build.playoff_bref,
        "load_season",
        lambda raw_dir, season: source_rows[season].copy(),
    )

    with pytest.raises(
        playoff_build.PlayoffValidationError,
        match="source integer",
    ):
        playoff_build.assemble(tmp_path)


def test_assemble_propagates_franchise_lookup_context(
    tmp_path, monkeypatch, repo_data_dir
):
    expected = playoff_build.load_expected_counts(
        repo_data_dir / "reference/playoff_season_counts.csv"
    )
    source_rows = _synthetic_source_rows(expected)
    source_rows[1947].loc[0, "away_team_name"] = "Unknown Wanderers"
    _seed_assembly_inputs(tmp_path)
    reference_dir = tmp_path / "reference"
    reference_dir.mkdir()
    for name in [
        "playoff_season_counts.csv",
        "playoff_site_rules.csv",
    ]:
        (reference_dir / name).write_bytes(
            (repo_data_dir / "reference" / name).read_bytes()
        )
    monkeypatch.setattr(
        playoff_build.playoff_bref,
        "load_season",
        lambda raw_dir, season: source_rows[season].copy(),
    )

    with pytest.raises(
        franchises.UnknownTeamError,
        match="Unknown Wanderers.*season 1947",
    ):
        playoff_build.assemble(tmp_path)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("duplicate_column", "columns and dtypes"),
        ("wrong_dtype", "columns and dtypes"),
        ("missing_season", "seasons must be exactly 1947..2026"),
        ("wrong_league", "league/season mismatch"),
        ("unknown_game_type", "unsupported game_type"),
        ("duplicate_game_id", "game_id must be globally unique"),
        ("null_required", "required playoff value is null"),
        ("same_source_team", "teams and franchises must differ"),
        ("same_franchise", "teams and franchises must differ"),
        ("negative_score", "scores must be nonnegative"),
        ("tied_score", "final scores must be non-tied"),
        ("bad_margin", "home_margin mismatch"),
        ("negative_overtime", "overtime periods must be nonnegative"),
        ("marker_mismatch", "overtime marker mismatch"),
        ("bad_attendance_status", "attendance/status mismatch"),
        ("bad_site_status", "unsupported site_status"),
        ("bad_count", "season/type count mismatch"),
        ("outside_window", "outside curated postseason window"),
        ("bad_voided_exemption", "invalid voided-game exemption"),
        ("modern_regular_overlap", "overlaps regular publication"),
        ("historical_regular_overlap", "overlaps regular publication"),
        ("evidence_missing", "overtime marker mismatch"),
        ("evidence_extra", "overtime marker mismatch"),
        ("evidence_duplicate", "overtime marker mismatch"),
        ("hoopr_missing", "BREF/hoopR postseason mismatch"),
        ("hoopr_extra", "BREF/hoopR postseason mismatch"),
        ("wrong_2020_site_matches", "2020 neutral-site rule mismatch"),
        ("wrong_2020_neutral_row", "2020 neutral-site rule mismatch"),
        ("wrong_2020_row_count", "2020 neutral-site rule mismatch"),
    ],
)
def test_validate_rejects_each_gate(
    complete_validation_case, mutation, message
):
    arguments = mutate_validation_case(
        complete_validation_case, mutation
    )
    with pytest.raises(
        playoff_build.PlayoffValidationError,
        match=message,
    ):
        playoff_build.validate(**arguments)


def test_validate_accepts_nullable_and_extreme_positive_edges(
    complete_validation_case,
):
    arguments = _copy_validation_arguments(complete_validation_case)
    games = arguments["games"]
    evidence = arguments["evidence"]
    first_three = games.index[games["season"].eq(1947)][:3]
    games.loc[first_three[0], "venue_name"] = pd.NA
    games.loc[first_three[0], "attendance"] = 0
    games.loc[first_three[0], "attendance_status"] = "reported_zero"
    games.loc[first_three[1], "attendance"] = pd.NA
    games.loc[first_three[1], "attendance_status"] = "missing"
    games.loc[first_three[2], "overtime_periods"] = 5
    game_id = games.loc[first_three[2], "game_id"]
    evidence.loc[evidence["game_id"].eq(game_id), "overtime_marker"] = "5OT"
    voided = games["game_type"].eq("voided")
    assert games.loc[voided, "boxscore_url"].isna().all()

    report = playoff_build.validate(**arguments)

    assert report["coverage"]["venue_name"]["missing"] == 1
    assert report["coverage"]["attendance"] == {
        "reported_positive": len(games) - 2,
        "reported_zero": 1,
        "missing": 1,
    }


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("season", 2019),
        ("game_date", date(2019, 1, 1)),
        ("game_type", "tiebreaker"),
        ("away_franchise_id", "OTHER_AWAY"),
        ("home_franchise_id", "OTHER_HOME"),
        ("away_pts", 1_000_000),
        ("home_pts", 1_000_001),
    ],
)
def test_validate_cross_source_key_uses_each_literal_field(
    complete_validation_case, field, replacement
):
    arguments = _copy_validation_arguments(complete_validation_case)
    arguments["hoopr_games"].loc[0, field] = replacement

    with pytest.raises(
        playoff_build.PlayoffValidationError,
        match="BREF/hoopR postseason mismatch",
    ):
        playoff_build.validate(**arguments)


def test_validate_never_uses_game_id_for_regular_overlap(
    complete_validation_case,
):
    arguments = _copy_validation_arguments(complete_validation_case)
    game = arguments["games"].iloc[0]
    nonoverlap = pd.DataFrame([{
        "game_id": game["game_id"],
        "season": game["season"],
        "game_date": game["game_date"],
        "away_franchise_id": game["away_franchise_id"],
        "home_franchise_id": game["home_franchise_id"],
        "away_pts": game["away_pts"] + 1,
        "home_pts": game["home_pts"],
    }])
    arguments["historical_regular"] = nonoverlap

    report = playoff_build.validate(**arguments)

    assert report["regular_overlap_rows"] == 0


def test_validate_returns_complete_deterministic_report(
    complete_validation_case,
):
    report = playoff_build.validate(**complete_validation_case)

    assert report == {
        "season_range": [1947, 2026],
        "total_games": 4652,
        "games_by_type": {
            "play_in": 37,
            "playoff": 4606,
            "tiebreaker": 8,
            "voided": 1,
        },
        "games_by_season": {
            "1947": 19,
            "1948": 23,
            "1949": 20,
            "1950": 32,
            "1951": 26,
            "1952": 25,
            "1953": 24,
            "1954": 23,
            "1955": 21,
            "1956": 23,
            "1957": 19,
            "1958": 21,
            "1959": 22,
            "1960": 25,
            "1961": 25,
            "1962": 29,
            "1963": 29,
            "1964": 27,
            "1965": 26,
            "1966": 27,
            "1967": 31,
            "1968": 40,
            "1969": 39,
            "1970": 40,
            "1971": 40,
            "1972": 37,
            "1973": 41,
            "1974": 41,
            "1975": 47,
            "1976": 50,
            "1977": 53,
            "1978": 51,
            "1979": 53,
            "1980": 48,
            "1981": 53,
            "1982": 47,
            "1983": 43,
            "1984": 79,
            "1985": 68,
            "1986": 68,
            "1987": 71,
            "1988": 80,
            "1989": 62,
            "1990": 72,
            "1991": 68,
            "1992": 73,
            "1993": 76,
            "1994": 77,
            "1995": 73,
            "1996": 68,
            "1997": 72,
            "1998": 71,
            "1999": 66,
            "2000": 75,
            "2001": 71,
            "2002": 71,
            "2003": 88,
            "2004": 82,
            "2005": 84,
            "2006": 89,
            "2007": 79,
            "2008": 86,
            "2009": 85,
            "2010": 82,
            "2011": 81,
            "2012": 84,
            "2013": 85,
            "2014": 89,
            "2015": 81,
            "2016": 86,
            "2017": 79,
            "2018": 82,
            "2019": 82,
            "2020": 84,
            "2021": 91,
            "2022": 93,
            "2023": 90,
            "2024": 88,
            "2025": 90,
            "2026": 91,
        },
        "cross_source": {
            "season_range": [2020, 2026],
            "expected_rows": 627,
            "matched_rows": 627,
        },
        "regular_overlap_rows": 0,
        "neutral_2020_rows": 84,
        "coverage": {
            "boxscore_url": {"present": 4651, "missing": 1},
            "venue_name": {"present": 4652, "missing": 0},
            "attendance": {
                "reported_positive": 4652,
                "reported_zero": 0,
                "missing": 0,
            },
            "site_status": {
                "home": 4568,
                "neutral": 84,
                "unknown": 0,
            },
        },
        "season_sha256": {
            "1947": "67232aebb693a04d8df494122b5d6732be3c73726cc83517c2def00fc8849045",
            "1948": "0555c6f5350e2fd88ee611da2484517778dfb7c10036f5cc6c08acf87963ad94",
            "1949": "0d2a2cb14e8fcec1293076167d56500a7b2184a6e9e6b89f3d124737511deec5",
            "1950": "2853fce698fd1508ecc75b08fcf8e74b7715a6f62ddcd8e735554a8eb367c2db",
            "1951": "244453d5b1ec762a1224212fa2657766fa2db16f6640343fb03e1997854efd43",
            "1952": "f3db446c3958838c3455b42bbbc8e700f6816093a685cc9b1a0ca2213df2996a",
            "1953": "8adada2829f985dc018a72737c34a94e58f36df6c54b19c2250de8009e83db9e",
            "1954": "944a1188a848df5d4d49ed8618caeb1d91fa4541c33f6a3e2c555b5fd7728c37",
            "1955": "4e0e84f70916c71744a6890483eebbcb6c40a73e47d38d5bed2dafc0da5526de",
            "1956": "5292e063b6461a1c3d5b2f1d04ce3859ecf9070a5e1f52e29f2b50c9959bd84f",
            "1957": "5ec51ee0c427233a12ac60ca22d80e6d41ae09fb8fa477a2281b956bd3c8fda5",
            "1958": "f590bacdc506d9c5f9cc33a82adc8d2941cacb820bbae59b79b579e82d313b02",
            "1959": "74cea1d45ea85218383e84993d0fa1c5ee055c56dfdb0f5005cd9f3476cec45a",
            "1960": "e5bab1aa729ed685817cc1d0e86c58efe57e6ebdb4899cce7991d5038bd350fe",
            "1961": "4d2671880280eb1c35a0a0f18b341a37f67a4047aec038d4c14938ae92a0aebe",
            "1962": "9d785a8e6ea09865b0a0147575ff9524e95dbac02f7ea468001b105214e1284e",
            "1963": "7f45346d77d2c5a43acc85e5244a589e61de23f54565b51a8e8a2191abd01761",
            "1964": "e64b30ef4c396e44378e2a64fc9cc9b777830e3952e286ffb02a1200483208a0",
            "1965": "1c2d453c0728043dc243124894699a319df2619c46d6fba9ba1b7a722913d318",
            "1966": "343385b398ea7d5718f002d3adaeaf99f614c2fa3802ebf17430b8b8f7f4787c",
            "1967": "0e8730cc1a7c933f0623a6185beb3ae8b19ec36cb39e7f4374a8c52db4e64b23",
            "1968": "921958558357b460263fa6d3bd1dbfc4b9a721a5597442d9ef5c69bda3b1c0d4",
            "1969": "5d03c9a1dd9b9bd7530c763875de4cd1e51427e045cc2fc758407a01dab4a8bf",
            "1970": "2afb9fd322af36e044b4796a8fab87fe6963290cfbf89bff2e312bc827004ff5",
            "1971": "61ba03c6d207277be16ef1feebfca387ee594afe36260592e7b8dfdfb7d1fb4c",
            "1972": "9ad2cf12d9a0c188d22200af7e34d0a9192262dafe0c4ce0efbbf10606ce352e",
            "1973": "60a907f539c6398cffeca1080fa5fcb21d23e6bf80e368ea0030e6f36b717b76",
            "1974": "bcb100f80788516043f04b03ed427d72e6c62d175c45bc90940f99259cb492ed",
            "1975": "bb0940e580ef744ae6087830aec359c8a675208854f42c675bf83e5d31822bfe",
            "1976": "7eb40b73bd7fb834d6a23f1bcc6fdc6e6576b802f77bda38d16d76e14346c478",
            "1977": "a84186f448c877aa72a292f5b18e876d64b55c8db332e6ea3343f424fcda6ad6",
            "1978": "ad49a2906aba8516a41082ae684230aefef9797987678eb41aec3c313b7cd5fc",
            "1979": "585288f9d901b0162d56718b962c8fa606c44074425fded25880fccb5a0b78b5",
            "1980": "54a8f1b6b762f9be967ac4529822cedc1b0f5338b61fb9892a8a09860b62ae3b",
            "1981": "5d956942e5bb9235c4b027ac595360d960df079861d597d5b1db510983a763eb",
            "1982": "ffe7e7646951047ae9cb60d079939a9759bef6eca711417c27aa39d7198acc13",
            "1983": "bcb947f664e9ec2de9bd69bf7dd354a574b81a912e2697f1923eb8d6af083895",
            "1984": "b199ec8754b8e837eabe6cd9a19cb49cb77aa577c6f4b4e09b6fdce09ec04100",
            "1985": "48015185d084bcee9ea2db9880c04234110cd46fc5167b7a57d538b52a2e8526",
            "1986": "32396765e85dbc40f17ae6317d7cc0682a8cc5293db9f074d9796b8f79e859fe",
            "1987": "afecad2be4495d48b96f0c29db0b908af900c85e6732671a1284f48158eec898",
            "1988": "34719a64a16dbb58cd732e953dbdde6a1ac13b17c33ded29935108f44ae0ca4f",
            "1989": "1d97ac1de892a14b820ddfb4ed089cc18eae1a297fa8993383f562b0f999aa36",
            "1990": "a5cd90d1986b10be25a2523a8b7a4a0972b8f2f88ec821e1b0daedfdc3f8cdbe",
            "1991": "c6fc7998312c43d06b34424f08472a63aa9c5060fb72ce9eeb1d401ce81f3f8e",
            "1992": "51efd6f56672bc642159e3d2418e78e6bf8ca02ba1fcdf56a15618dad4580752",
            "1993": "ef2a58c0af911bb8c241d4072ee385e4519595ad33d1e67affe20ad6cdd28433",
            "1994": "4f67888a000fb9447425efde17fcb2c87548b1206ab3c254c1c55ce34a952a48",
            "1995": "dfcce39d6710e1995bca16639b95bd3fd0b75091c8d243c09df993ce8fbfcddc",
            "1996": "bd334fd2794e59b32794102ae62006d640eb453c7b16617518e2f8746e22d571",
            "1997": "fe8ae1de46b4a8971a416b9996bf0e8127ee8cff6d8a21cc16927eabcfa7e78a",
            "1998": "d1cdf511a4f70f81dcaab057ee4346e0651afc924a41ed4f941a57f52f821521",
            "1999": "7089c3fc5d810fa395911b5094c5167d527a4ce45365fc3e74444cd0d418a5d0",
            "2000": "198e9e1f2f794225a000bd96e17e1edf04552c647d82ddacda6952772701615b",
            "2001": "b610886ebced0103b91b1b1783ceef82cf157b6acfc5fe2afff791cec1987513",
            "2002": "0985ee701355edd188e9d0814dd613cd20d9a6dd6addcd8823eadf93aea39d9f",
            "2003": "98237ced461c29217f8c9f0c3e035710761bb5218018eac3af801150097725a8",
            "2004": "0ce0183e73a96bb09594fd02019740f2b32feda356e5e55651f5e46442b30011",
            "2005": "a1b27942b0b3300206c04255c748717a65779bd6da63d0196600ce1851f2691e",
            "2006": "edc4c91748d6494fc4256d00bc4746dcb6209b9e964f7fee238d5472eeaf9df1",
            "2007": "59839bf4e67e871284e1ac3c1b54bf744e9ac6d207c32a809edb28992840eedf",
            "2008": "c1dad7af81cf91a5e19921e7c5d67a3a72cdaf7ee6a95d1411d4261caf39d7af",
            "2009": "4c416b2067202a534463b5125af91a69d2a85e449701f6dadedbfddf2960a9a0",
            "2010": "7af097e369ea1af4a0b6e31824f501f79e7181f4ef9cd85f7c89a31c57668b49",
            "2011": "637c671b50ff9d09c3e54e1b90098fed2bfa13b14cee7b9984bd6cbe4f33c081",
            "2012": "d9f9303cceb2f400ecec580e1011a0aa165a675f5b07547835c304cc019de965",
            "2013": "ad30dcec88d0c8aaa791b3326dfa16a204987ebf8c195d3ba01716b6d261f852",
            "2014": "89f00046069dcdd3f174a0219d452f331cd8c3697728ff11d23ca2985cf33708",
            "2015": "faf1d806d61be1f8997e2d5110d900c2ab37f418165c4378ef95258a5dda4b41",
            "2016": "77bcc1d9056be2a7d5ff270f4dbba093cabd5aacde4fb600d7748b2afc2a5e65",
            "2017": "5fd7e858a2da7a2b9cfd9708b9d9c9789603904dcd442526c27e126b6c7e1533",
            "2018": "a46675c02da529f548d7df1ce5abecd6ee987a5e24f3e6ecde1f46e27c2e8005",
            "2019": "6dba05e4ec2127ba30c752e493990169a9de9af2dd894ac4b28bccfd9641ec46",
            "2020": "9c96ae1e9f65bbfff38d634edf2435bb84bb1562d0778b43601afa3224645cc9",
            "2021": "15c597d1f70a40bbdccd54dc8f619bf09aecbaeddf9b2e01971889f2ff3ce2a6",
            "2022": "b4734dfda2e05337ad911ac292fcb0175428bfa7124346045cbf335a7fca7c1b",
            "2023": "82122a27a87e25e5d8ebb44d8a7dbef5016c2363b2a07fcaf61002c7a43393af",
            "2024": "41272143451df2c6a9b9f0315170d37b665b314e2971584c13eab597f20a3526",
            "2025": "57bb7a8e4514c3029676106eaa14cd139ab7fc08a6157d0a2b2d90bf5493a74a",
            "2026": "9d33411631bc237441305498ef3b39fffd7c9e635e32682f5c02eec546525123",
        },
    }


PLAYOFF_FIXTURES = Path(__file__).parent / "fixtures/playoff_bref"
HISTORICAL_MONTH_FIXTURE = (
    Path(__file__).parent
    / "fixtures/historical_bref/BAA_1947_games-november.html"
)
HISTORICAL_INDEX_PAYLOAD = (
    b"<html><body>"
    b'<a href="/leagues/BAA_1947_games-november.html">November</a>'
    b"</body></html>"
)
HISTORICAL_MONTH_URL = (
    "https://www.basketball-reference.com/leagues/"
    "BAA_1947_games-november.html"
)
PAGE_FIELDS = {
    "path",
    "provider",
    "resource",
    "league",
    "season",
    "month",
    "url",
    "sha256",
}
SEEDED_PAGE_FIELDS = PAGE_FIELDS | {
    "seeded_from",
    "seed_operation",
    "source_sha256",
}
REFERENCE_FILES = (
    "franchises.csv",
    "hoopr_snapshot.json",
    "playoff_season_counts.csv",
    "playoff_site_rules.csv",
)


def _rewrite_metadata(path: Path, **changes) -> None:
    metadata = json.loads(path.read_text(encoding="utf-8"))
    metadata.update(changes)
    path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _seed_small_verified_historical_cache(data_dir: Path) -> None:
    cache_dir = data_dir / "raw/historical_bref"
    cache.publish_bytes(
        cache_dir / "BAA_1947_games.html",
        HISTORICAL_INDEX_PAYLOAD,
        {
            "provider": "basketball_reference",
            "resource": "historical_schedule_index",
            "league": "BAA",
            "season": 1947,
            "url": historical_bref.index_url(1947),
        },
        lambda path: historical_bref.validate_index(path, 1947),
    )
    cache.publish_bytes(
        cache_dir / "BAA_1947_games-november.html",
        HISTORICAL_MONTH_FIXTURE.read_bytes(),
        {
            "provider": "basketball_reference",
            "resource": "historical_schedule_month",
            "league": "BAA",
            "season": 1947,
            "month": "november",
            "url": HISTORICAL_MONTH_URL,
        },
        lambda path: historical_bref.validate_month(
            path, 1947, HISTORICAL_MONTH_URL
        ),
    )


def _seed_verified_provenance_data(data_dir: Path) -> None:
    parquet_dir = data_dir / "parquet"
    reference_dir = data_dir / "reference"
    parquet_dir.mkdir(parents=True)
    reference_dir.mkdir()
    _seed_small_verified_historical_cache(data_dir)
    historical_sources = historical_build._raw_provenance(data_dir)
    (parquet_dir / "historical_games_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sources": historical_sources,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    dedicated_payload = (
        PLAYOFF_FIXTURES / "NBA_1984_games.html"
    ).read_bytes()
    for season in playoff_bref.DEDICATED_SEASONS:
        path = (
            data_dir
            / "raw/playoff_bref"
            / playoff_bref.playoff_filename(season)
        )
        cache.publish_bytes(
            path,
            dedicated_payload,
            {
                "provider": "basketball_reference",
                "resource": "playoff_schedule",
                "league": "NBA",
                "season": season,
                "month": None,
                "url": playoff_bref.playoff_url(season),
            },
            lambda value, year=season: (
                playoff_bref.validate_dedicated_page(value, year)
            ),
        )

    seeded_payload = (
        PLAYOFF_FIXTURES / "NBA_2020_games-august.html"
    ).read_bytes()
    for season, month in playoff_bref.PLAY_IN_MONTHS.items():
        source = (
            data_dir / "raw/bref" / f"NBA_{season}_games-{month}.html"
        )
        cache.publish_bytes(
            source,
            seeded_payload,
            {
                "provider": "basketball_reference",
                "season": season,
                "month": month,
            },
            bref.validate_payload,
        )
        playoff_bref.seed_play_in_page(data_dir / "raw", season)

    buffer = BytesIO()
    pd.DataFrame({"game_id": [1]}).to_parquet(buffer, index=False)
    hoopr_payload = buffer.getvalue()
    for season in range(2020, 2027):
        cache.publish_bytes(
            data_dir / f"raw/hoopr/nba_schedule_{season}.parquet",
            hoopr_payload,
            {
                "provider": "hoopr",
                "season": season,
                "upstream_commit": hoopr.SNAPSHOT["commit"],
            },
            hoopr.validate_payload,
        )

    for name in REFERENCE_FILES:
        path = reference_dir / name
        if not path.exists():
            path.write_text(f"verified {name}\n", encoding="utf-8")


@pytest.fixture
def verified_provenance_data(tmp_path):
    _seed_verified_provenance_data(tmp_path)
    return tmp_path


def test_raw_provenance_contains_only_stable_source_identity(
    verified_provenance_data,
):
    provenance = playoff_build._raw_provenance(verified_provenance_data)

    assert provenance.keys() == {"basketball_reference", "hoopr"}
    bref_sources = provenance["basketball_reference"]
    assert bref_sources["historical_manifest"] == {
        "path": "parquet/historical_games_manifest.json",
        "sha256": hashlib.sha256(
            (
                verified_provenance_data
                / "parquet/historical_games_manifest.json"
            ).read_bytes()
        ).hexdigest(),
    }
    historical_manifest = json.loads(
        (
            verified_provenance_data
            / "parquet/historical_games_manifest.json"
        ).read_text(encoding="utf-8")
    )
    expected_historical_pages = historical_manifest[
        "sources"
    ]["basketball_reference"]["pages"]
    assert len(expected_historical_pages) == 2
    assert bref_sources["historical_pages"] == expected_historical_pages
    assert all(
        set(record) == PAGE_FIELDS
        for record in bref_sources["historical_pages"]
    )
    assert len({
        (
            record["resource"],
            record["league"],
            record["season"],
            record["month"],
        )
        for record in bref_sources["historical_pages"]
    }) == 2
    assert len(bref_sources["playoff_pages"]) == 50
    for record in bref_sources["playoff_pages"]:
        expected_fields = (
            SEEDED_PAGE_FIELDS
            if record["resource"] == "play_in_schedule_month"
            else PAGE_FIELDS
        )
        assert record.keys() == expected_fields
    assert provenance["hoopr"]["upstream_commit"] == hoopr.SNAPSHOT["commit"]
    assert len(provenance["hoopr"]["schedules"]) == 7
    assert all(
        set(record) == {
            "path",
            "provider",
            "resource",
            "season",
            "url",
            "upstream_commit",
            "sha256",
        }
        for record in provenance["hoopr"]["schedules"]
    )
    serialized = json.dumps(provenance, sort_keys=True)
    assert "fetched_at" not in serialized
    assert str(verified_provenance_data) not in serialized


def test_raw_provenance_rejects_historical_manifest_source_drift(
    verified_provenance_data,
):
    manifest_path = (
        verified_provenance_data
        / "parquet/historical_games_manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sources"]["basketball_reference"]["pages"].append(
        {"path": "raw/historical_bref/unverified.html"}
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(
        playoff_build.PlayoffValidationError,
        match="historical manifest sources",
    ):
        playoff_build._raw_provenance(verified_provenance_data)


@pytest.mark.parametrize("payload", ["[]", "null", "1", '"scalar"'])
def test_raw_provenance_rejects_non_object_historical_manifest(
    verified_provenance_data, payload
):
    manifest_path = (
        verified_provenance_data
        / "parquet/historical_games_manifest.json"
    )
    manifest_path.write_text(payload + "\n", encoding="utf-8")

    with pytest.raises(
        playoff_build.PlayoffValidationError,
        match="historical source provenance is invalid",
    ) as caught:
        playoff_build._raw_provenance(verified_provenance_data)

    assert isinstance(caught.value.__cause__, ValueError)


def test_raw_provenance_rebinds_playoff_page_after_inventory_validation(
    monkeypatch, verified_provenance_data
):
    real_validate = playoff_bref.validate_cache_inventory

    def mutate_sidecar_after_validation(raw_dir, *, complete):
        paths = real_validate(raw_dir, complete=complete)
        sidecar = (
            verified_provenance_data
            / "raw/playoff_bref/NBA_1984_games.html.meta.json"
        )
        _rewrite_metadata(sidecar, sha256="0" * 64)
        return paths

    monkeypatch.setattr(
        playoff_bref,
        "validate_cache_inventory",
        mutate_sidecar_after_validation,
    )

    with pytest.raises(
        playoff_build.PlayoffValidationError,
        match="playoff page provenance is invalid",
    ):
        playoff_build._raw_provenance(verified_provenance_data)


def test_raw_provenance_rejects_historical_manifest_changed_while_attesting(
    monkeypatch, verified_provenance_data
):
    manifest_path = (
        verified_provenance_data
        / "parquet/historical_games_manifest.json"
    )
    real_raw_provenance = historical_build._raw_provenance

    def mutate_after_cache_attestation(data_dir):
        sources = real_raw_provenance(data_dir)
        manifest_path.write_bytes(manifest_path.read_bytes() + b" ")
        return sources

    monkeypatch.setattr(
        historical_build,
        "_raw_provenance",
        mutate_after_cache_attestation,
    )

    with pytest.raises(
        playoff_build.PlayoffValidationError,
        match="historical manifest changed while attesting",
    ):
        playoff_build._raw_provenance(verified_provenance_data)


@pytest.mark.parametrize(
    "defect",
    [
        "missing_playoff_body",
        "extra_playoff_body",
        "sidecar_identity",
        "wrong_seed_source_hash",
        "wrong_hoopr_pin",
        "unreferenced_playoff_file",
        "absolute_seed_path",
    ],
)
def test_raw_provenance_rejects_unattestable_inputs(
    verified_provenance_data, defect
):
    data_dir = verified_provenance_data
    playoff_dir = data_dir / "raw/playoff_bref"
    if defect == "missing_playoff_body":
        (playoff_dir / "NBA_1984_games.html").unlink()
    elif defect in {"extra_playoff_body", "unreferenced_playoff_file"}:
        name = (
            "NBA_1983_games.html"
            if defect == "extra_playoff_body"
            else "unreferenced.txt"
        )
        (playoff_dir / name).write_bytes(b"unverified")
    elif defect == "sidecar_identity":
        _rewrite_metadata(
            playoff_dir / "NBA_1984_games.html.meta.json",
            season=1985,
        )
    elif defect in {"wrong_seed_source_hash", "absolute_seed_path"}:
        sidecar = (
            playoff_dir / "NBA_2020_games-august.html.meta.json"
        )
        changes = (
            {"source_sha256": "0" * 64}
            if defect == "wrong_seed_source_hash"
            else {"seeded_from": "/tmp/unattested.html"}
        )
        _rewrite_metadata(sidecar, **changes)
    else:
        _rewrite_metadata(
            data_dir / "raw/hoopr/nba_schedule_2020.parquet.meta.json",
            upstream_commit="wrong-pin",
        )

    with pytest.raises(
        (playoff_build.PlayoffValidationError, cache.CacheIntegrityError)
    ):
        playoff_build._raw_provenance(data_dir)


def test_raw_provenance_revalidates_seeded_source_hash(
    verified_provenance_data,
):
    source = (
        verified_provenance_data
        / "raw/bref/NBA_2020_games-august.html"
    )
    source.write_bytes(source.read_bytes() + b"\n<!-- changed -->")
    _rewrite_metadata(
        source.with_name(source.name + ".meta.json"),
        sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        content_length=len(source.read_bytes()),
    )

    with pytest.raises(
        playoff_build.PlayoffValidationError,
        match="seeded playoff source hash",
    ):
        playoff_build._raw_provenance(verified_provenance_data)


def test_reference_provenance_attests_exact_regular_files(
    verified_provenance_data,
):
    provenance = playoff_build._reference_provenance(
        verified_provenance_data
    )

    assert provenance.keys() == set(REFERENCE_FILES)
    for name, record in provenance.items():
        assert record.keys() == {"path", "sha256"}
        assert not Path(record["path"]).is_absolute()
        assert record["path"] == f"reference/{name}"
        assert record["sha256"] == hashlib.sha256(
            (verified_provenance_data / record["path"]).read_bytes()
        ).hexdigest()
        assert len(record["sha256"]) == 64
        assert record["sha256"] == record["sha256"].lower()


@pytest.mark.parametrize("kind", ["directory", "symlink"])
def test_reference_provenance_rejects_non_regular_file(
    verified_provenance_data, kind
):
    target = (
        verified_provenance_data / "reference/playoff_site_rules.csv"
    )
    target.unlink()
    if kind == "directory":
        target.mkdir()
    else:
        replacement = verified_provenance_data / "replacement-reference"
        replacement.write_bytes(b"unverified")
        target.symlink_to(replacement)

    with pytest.raises(
        playoff_build.PlayoffValidationError,
        match="reference file must be regular",
    ):
        playoff_build._reference_provenance(verified_provenance_data)


def _install_synthetic_assembly(
    monkeypatch, validation_case
) -> dict:
    report = playoff_build.validate(**validation_case)
    evidence = {
        key: validation_case[key]
        for key in (
            "evidence",
            "hoopr_games",
            "modern_regular",
            "historical_regular",
            "site_rule_matches",
        )
    }
    evidence["source_rows"] = evidence.pop("evidence")
    monkeypatch.setattr(
        playoff_build,
        "assemble",
        lambda data_dir: (
            validation_case["games"].copy(),
            report.copy(),
            evidence,
        ),
    )
    return report


def test_run_publishes_deterministic_attested_manifest(
    monkeypatch,
    verified_provenance_data,
    complete_validation_case,
    repo_data_dir,
):
    data_dir = verified_provenance_data
    (data_dir / "reference/playoff_season_counts.csv").write_bytes(
        (
            repo_data_dir / "reference/playoff_season_counts.csv"
        ).read_bytes()
    )
    expected_report = _install_synthetic_assembly(
        monkeypatch, complete_validation_case
    )

    playoff_build.run(data_dir)

    parquet_path = data_dir / "parquet/playoff_games.parquet"
    manifest_path = data_dir / "parquet/playoff_games_manifest.json"
    first_parquet = parquet_path.read_bytes()
    first_manifest = manifest_path.read_bytes()
    manifest = json.loads(first_manifest)
    assert manifest.keys() == {
        "schema_version",
        "sources",
        "references",
        "versions",
        "tables",
        "validation",
    }
    assert manifest["schema_version"] == 1
    assert manifest["sources"].keys() == {
        "basketball_reference",
        "hoopr",
    }
    assert manifest["references"].keys() == set(REFERENCE_FILES)
    assert manifest["tables"].keys() == {"playoff_games"}
    assert manifest["tables"]["playoff_games"]["schema"] == [
        {"name": field.name, "arrow_type": str(field.type)}
        for field in pq.read_schema(parquet_path)
    ]
    assert manifest["validation"] == expected_report
    assert b"fetched_at" not in first_manifest
    assert str(data_dir).encode() not in first_manifest
    assert first_manifest.endswith(b"\n")
    assert not list(data_dir.glob(".playoff-stage-*"))

    playoff_build.run(data_dir)

    assert parquet_path.read_bytes() == first_parquet
    assert manifest_path.read_bytes() == first_manifest


def test_run_rejects_changed_post_reread_validation_and_cleans_stage(
    monkeypatch,
    verified_provenance_data,
    complete_validation_case,
    repo_data_dir,
):
    data_dir = verified_provenance_data
    (data_dir / "reference/playoff_season_counts.csv").write_bytes(
        (
            repo_data_dir / "reference/playoff_season_counts.csv"
        ).read_bytes()
    )
    _install_synthetic_assembly(monkeypatch, complete_validation_case)
    monkeypatch.setattr(
        playoff_build,
        "validate",
        lambda *args, **kwargs: {"changed": True},
    )

    with pytest.raises(
        playoff_build.PlayoffValidationError,
        match="validation changed after Parquet re-read",
    ):
        playoff_build.run(data_dir)

    assert not list(data_dir.glob(".playoff-stage-*"))


def test_run_warns_when_committed_prior_pair_cleanup_is_retained(
    monkeypatch,
    verified_provenance_data,
    complete_validation_case,
    repo_data_dir,
):
    data_dir = verified_provenance_data
    (data_dir / "reference/playoff_season_counts.csv").write_bytes(
        (
            repo_data_dir / "reference/playoff_season_counts.csv"
        ).read_bytes()
    )
    _install_synthetic_assembly(monkeypatch, complete_validation_case)
    retained = data_dir / ".playoff-rollback-retained"
    real_publish = publication.publish_pair

    def publish_and_retain(*args, **kwargs):
        real_publish(*args, **kwargs)
        return retained

    monkeypatch.setattr(
        publication, "publish_pair", publish_and_retain
    )

    with pytest.warns(
        RuntimeWarning,
        match="playoff publication committed; prior pair retained",
    ):
        playoff_build.run(data_dir)

    assert not list(data_dir.glob(".playoff-stage-*"))
