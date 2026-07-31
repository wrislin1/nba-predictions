from datetime import date

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
    assert set(rules) == {2020}
