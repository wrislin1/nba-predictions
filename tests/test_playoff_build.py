from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from nba_data import playoff_build


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
    return pd.DataFrame(columns=[
        "season",
        "game_date",
        "away_franchise_id",
        "home_franchise_id",
        "away_pts",
        "home_pts",
    ])


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
    return playoff_build._enforce_schema(source)


def _validation_arguments(
    expected_counts: dict[int, playoff_build.ExpectedSeason],
    source_rows: dict[int, pd.DataFrame],
) -> dict:
    games = _typed_games(source_rows)
    source = pd.concat(source_rows.values(), ignore_index=True)
    hoopr_games = games.loc[
        games["season"].between(2020, 2026)
        & games["game_type"].isin(["playoff", "play_in"]),
        playoff_build.CROSS_SOURCE_KEY,
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
    elif mutation == "same_team":
        games.loc[historical_index, "home_team_name"] = games.loc[
            historical_index, "away_team_name"
        ]
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
    elif mutation == "regular_overlap":
        overlap = games.loc[[historical_index],
                            playoff_build.REGULAR_OVERLAP_KEY].copy()
        overlap["game_id"] = games.loc[historical_index, "game_id"]
        mutated["historical_regular"] = overlap
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
            playoff_build.CROSS_SOURCE_KEY,
        ].reset_index(drop=True)

    monkeypatch.setattr(
        playoff_build.hoopr, "load_postseason", fake_hoopr_loader
    )

    games, report, evidence = playoff_build.assemble(tmp_path)

    assert games.columns.tolist() == playoff_build.PLAYOFF_COLUMNS
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
    for column, dtype in playoff_build.PLAYOFF_DTYPES.items():
        assert str(games[column].dtype) == dtype
    assert report["season_range"] == [1947, 2026]
    assert set(evidence) == {
        "source_rows",
        "hoopr_games",
        "modern_regular",
        "historical_regular",
        "site_rule_matches",
    }


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
        Exception, match="Unknown Wanderers.*season 1947"
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
        ("same_team", "teams and franchises must differ"),
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
        ("regular_overlap", "overlaps regular publication"),
        ("hoopr_missing", "BREF/hoopR postseason mismatch"),
        ("hoopr_extra", "BREF/hoopR postseason mismatch"),
        ("wrong_2020_site_matches", "2020 neutral-site rule mismatch"),
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

    assert report == playoff_build.validate(**complete_validation_case)
    assert report["season_range"] == [1947, 2026]
    assert report["total_games"] == 4652
    assert report["cross_source"] == {
        "season_range": [2020, 2026],
        "expected_rows": 627,
        "matched_rows": 627,
    }
    assert report["regular_overlap_rows"] == 0
    assert report["neutral_2020_rows"] == 84
    assert set(report["season_sha256"]) == {
        str(season) for season in range(1947, 2027)
    }
