import datetime
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from nba_data import build, franchises, hoopr


CORRECTION_COLUMNS = [
    "game_id",
    "franchise_id",
    "field",
    "corrected_value",
    "source_url",
    "note",
    "attendance_coverage_exemption_reason",
]
CAPACITY_COLUMNS = [
    "venue_id",
    "franchise_id",
    "first_season",
    "last_season",
    "capacity",
    "capacity_source_url",
    "capacity_note",
]


def one_nba_game(game_id="0022000001", season=2023):
    return pd.DataFrame([{
        "game_id": game_id,
        "season": season,
        "source": "nba_stats",
        "game_date": datetime.date(2022, 10, 18),
        "home_franchise_id": "BKN",
        "away_franchise_id": "GSW",
        "home_pts": 125,
        "away_pts": 99,
    }])


def one_schedule_game(
    espn_game_id="espn-401300001", espn_attendance=18_000, season=2023
):
    return pd.DataFrame([{
        "espn_game_id": espn_game_id,
        "season": season,
        "game_date": datetime.date(2022, 10, 18),
        "home_franchise_id": "BKN",
        "away_franchise_id": "GSW",
        "neutral_site": False,
        "venue_id": "espn-17",
        "venue_name_espn": "Barclays Center",
        "venue_city": "Brooklyn",
        "espn_attendance": espn_attendance,
    }])


def one_bref_game(attendance=18_000, arena="Barclays Center", season=2023):
    return pd.DataFrame([{
        "season": season,
        "game_date": datetime.date(2022, 10, 18),
        "home_franchise_id": "BKN",
        "away_franchise_id": "GSW",
        "attendance": attendance,
        "arena": arena,
    }])


def one_team_game_panel(game_id="0022000001", season=2023):
    return pd.DataFrame([
        {
            "game_id": game_id,
            "season": season,
            "source": "nba_stats",
            "franchise_id": "BKN",
            "opponent_franchise_id": "GSW",
            "is_home": True,
            "game_date": datetime.date(2022, 10, 18),
            "wl": 1,
            "pts": 125,
            "fgm": 45,
            "fga": 90,
            "fg3m": 15,
            "fg3a": 40,
            "ftm": 20,
            "fta": 25,
            "oreb": 10,
            "dreb": 30,
            "reb": 40,
            "ast": 25,
            "stl": 7,
            "blk": 5,
            "tov": 12,
            "pf": 18,
        },
        {
            "game_id": game_id,
            "season": season,
            "source": "nba_stats",
            "franchise_id": "GSW",
            "opponent_franchise_id": "BKN",
            "is_home": False,
            "game_date": datetime.date(2022, 10, 18),
            "wl": 0,
            "pts": 99,
            "fgm": 36,
            "fga": 88,
            "fg3m": 10,
            "fg3a": 34,
            "ftm": 17,
            "fta": 22,
            "oreb": 8,
            "dreb": 29,
            "reb": 37,
            "ast": 21,
            "stl": 6,
            "blk": 4,
            "tov": 14,
            "pf": 20,
        },
    ])


def one_valid_game(game_id="0022000001", season=2023):
    return pd.DataFrame([{
        "game_id": game_id,
        "espn_game_id": "espn-401300001",
        "season": season,
        "source": "nba_stats",
        "game_date": datetime.date(2022, 10, 18),
        "home_franchise_id": "BKN",
        "away_franchise_id": "GSW",
        "home_pts": 125,
        "away_pts": 99,
        "neutral_site": False,
        "neutral_site_source": "hoopr",
        "venue_id": "espn-17",
        "venue_name": "Barclays Center",
        "venue_city": "Brooklyn",
        "venue_source": "bref",
        "attendance_bref": 18_000,
        "attendance_espn": 18_000,
        "attendance": 18_000,
        "bref_row_matched": True,
        "attendance_status": "reported_positive",
        "attendance_source": "bref",
        "capacity": 18_103,
    }])


def assert_invalid(team_games, games, expected, diagnostic, expected_legacy=0):
    with pytest.raises(build.ValidationError) as caught:
        build.validate(
            team_games,
            games,
            expected_counts=expected,
            expected_legacy=expected_legacy,
        )
    assert diagnostic in str(caught.value)


def test_load_game_corrections_requires_unique_provenance_backed_rows(
    tmp_path,
):
    path = tmp_path / "game_corrections.csv"
    duplicate = {
        "game_id": "0028300493",
        "franchise_id": "BKN",
        "field": "pts",
        "corrected_value": "115",
        "source_url": (
            "https://www.nba.com/game/njn-vs-uth-0028300493/box-score"
        ),
        "note": "Correct archived Nets score from official NBA box score.",
    }
    pd.DataFrame(
        [duplicate, duplicate], columns=CORRECTION_COLUMNS
    ).to_csv(path, index=False)

    with pytest.raises(
        build.ValidationError,
        match="duplicate game correction",
    ):
        build.load_game_corrections(path)

    no_provenance = duplicate | {"source_url": "nba.example/game"}
    pd.DataFrame(
        [no_provenance], columns=CORRECTION_COLUMNS
    ).to_csv(path, index=False)
    with pytest.raises(
        build.ValidationError,
        match="correction source URL",
    ):
        build.load_game_corrections(path)


def test_load_game_corrections_accepts_explicit_no_fan_exemption_reason(
    tmp_path,
):
    path = tmp_path / "game_corrections.csv"
    pd.DataFrame([{
        "game_id": "0021901231",
        "franchise_id": pd.NA,
        "field": "neutral_site",
        "corrected_value": "true",
        "source_url": "https://www.nba.com/news/restart",
        "note": "Official no-fan single-site bubble game.",
        "attendance_coverage_exemption_reason": (
            "no_fan_bubble_missing_attendance"
        ),
    }], columns=CORRECTION_COLUMNS).to_csv(path, index=False)

    loaded = build.load_game_corrections(path)

    assert loaded.iloc[0][
        "attendance_coverage_exemption_reason"
    ] == "no_fan_bubble_missing_attendance"


@pytest.mark.parametrize(
    "corrected_value",
    [
        pytest.param("-1", id="negative"),
        pytest.param("+1", id="signed"),
        pytest.param("1.0", id="decimal"),
        pytest.param(" 1", id="leading-whitespace"),
    ],
)
def test_load_game_corrections_rejects_noncanonical_box_scalars(
    tmp_path, corrected_value
):
    """Would fail if a box correction were not a nonnegative base-10 integer."""
    path = tmp_path / "game_corrections.csv"
    pd.DataFrame([{
        "game_id": "0028300493",
        "franchise_id": "BKN",
        "field": "pts",
        "corrected_value": corrected_value,
        "source_url": "https://www.basketball-reference.com/boxscores/example",
        "note": "Historical box-score reconciliation.",
    }], columns=CORRECTION_COLUMNS).to_csv(path, index=False)

    with pytest.raises(
        build.ValidationError,
        match="invalid box correction value",
    ):
        build.load_game_corrections(path)


@pytest.mark.parametrize(
    "corrected_value",
    [
        pytest.param("0", id="zero"),
        pytest.param("100", id="positive"),
        pytest.param(" ", id="whitespace"),
    ],
)
def test_load_game_corrections_requires_exact_blank_attendance_value(
    tmp_path, corrected_value
):
    """Would fail if attendance corrections could manufacture a numeric value."""
    path = tmp_path / "game_corrections.csv"
    pd.DataFrame([{
        "game_id": "0022200056",
        "franchise_id": pd.NA,
        "field": "attendance",
        "corrected_value": corrected_value,
        "source_url": "https://statsdmz.nba.com/gamebook.pdf",
        "note": "Official scorer report says attendance was not counted.",
    }], columns=CORRECTION_COLUMNS).to_csv(path, index=False)

    with pytest.raises(
        build.ValidationError,
        match="attendance correction value must be blank",
    ):
        build.load_game_corrections(path)


@pytest.mark.parametrize(
    "corrected_value",
    [
        pytest.param("TRUE", id="uppercase"),
        pytest.param("1", id="integer"),
        pytest.param("yes", id="word"),
        pytest.param(" true", id="whitespace"),
    ],
)
def test_load_game_corrections_requires_exact_boolean_literals(
    tmp_path, corrected_value
):
    """Would fail if a neutral-site correction accepted a fuzzy boolean."""
    path = tmp_path / "game_corrections.csv"
    pd.DataFrame([{
        "game_id": "0021901233",
        "franchise_id": pd.NA,
        "field": "neutral_site",
        "corrected_value": corrected_value,
        "source_url": "https://www.nba.com/news/restart",
        "note": "Official single-site restart game.",
    }], columns=CORRECTION_COLUMNS).to_csv(path, index=False)

    with pytest.raises(
        build.ValidationError,
        match="invalid boolean correction value",
    ):
        build.load_game_corrections(path)


@pytest.mark.parametrize(
    ("field", "corrected_value", "reason"),
    [
        pytest.param(
            "neutral_site",
            "true",
            "weather_delay",
            id="unsupported-reason",
        ),
        pytest.param(
            "attendance",
            pd.NA,
            "no_fan_bubble_missing_attendance",
            id="wrong-field",
        ),
        pytest.param(
            "neutral_site",
            "false",
            "no_fan_bubble_missing_attendance",
            id="non-neutral-value",
        ),
    ],
)
def test_load_game_corrections_rejects_invalid_exemption_assignment(
    tmp_path, field, corrected_value, reason
):
    path = tmp_path / "game_corrections.csv"
    pd.DataFrame([{
        "game_id": "0021901231",
        "franchise_id": pd.NA,
        "field": field,
        "corrected_value": corrected_value,
        "source_url": "https://www.nba.com/news/restart",
        "note": "Source-backed correction.",
        "attendance_coverage_exemption_reason": reason,
    }], columns=CORRECTION_COLUMNS).to_csv(path, index=False)

    with pytest.raises(
        build.ValidationError,
        match="invalid attendance coverage exemption assignment",
    ):
        build.load_game_corrections(path)


def test_load_game_corrections_rejects_unapproved_exemption_game(
    tmp_path,
):
    path = tmp_path / "game_corrections.csv"
    pd.DataFrame([{
        "game_id": "0021900002",
        "franchise_id": pd.NA,
        "field": "neutral_site",
        "corrected_value": "true",
        "source_url": "https://www.nba.com/news/neutral-game",
        "note": "Official unrelated neutral-site game.",
        "attendance_coverage_exemption_reason": (
            "no_fan_bubble_missing_attendance"
        ),
    }], columns=CORRECTION_COLUMNS).to_csv(path, index=False)

    with pytest.raises(
        build.ValidationError,
        match="invalid attendance coverage exemption assignment",
    ):
        build.load_game_corrections(path)


def test_apply_game_corrections_is_keyed_and_preserves_inputs():
    team_games = one_team_game_panel()
    games = one_valid_game()
    original_team_games = team_games.copy(deep=True)
    original_games = games.copy(deep=True)
    corrections = pd.DataFrame([
        {
            "game_id": "0022000001",
            "franchise_id": "GSW",
            "field": "pts",
            "corrected_value": "115",
            "source_url": "https://www.nba.com/game/example/box-score",
            "note": "Official NBA score correction.",
        },
        {
            "game_id": "0022000001",
            "franchise_id": pd.NA,
            "field": "attendance",
            "corrected_value": pd.NA,
            "source_url": "https://statsdmz.nba.com/gamebook.pdf",
            "note": "Official scorer report says attendance not counted.",
        },
        {
            "game_id": "0022000001",
            "franchise_id": pd.NA,
            "field": "neutral_site",
            "corrected_value": "true",
            "source_url": "https://www.nba.com/news/restart",
            "note": "Official no-fan single-site bubble game.",
            "attendance_coverage_exemption_reason": (
                "no_fan_bubble_missing_attendance"
            ),
        },
    ], columns=CORRECTION_COLUMNS)

    corrected_teams = build.apply_team_game_corrections(
        team_games, corrections
    )
    corrected_games = build.apply_game_corrections(
        games, corrections
    )

    assert corrected_teams.set_index("franchise_id").loc["GSW", "pts"] == 115
    corrected_game = corrected_games.iloc[0]
    assert pd.isna(corrected_game["attendance"])
    assert corrected_game["attendance_source"] == "bref"
    assert corrected_game["attendance_status"] == "missing"
    assert corrected_game["neutral_site"] == True
    assert corrected_game["neutral_site_source"] == (
        "game_correction:neutral_site"
    )
    assert corrected_game[
        "attendance_coverage_exemption_reason"
    ] == (
        "no_fan_bubble_missing_attendance"
    )
    pd.testing.assert_frame_equal(team_games, original_team_games)
    pd.testing.assert_frame_equal(games, original_games)


@pytest.mark.parametrize(
    ("frame", "expected_count"),
    [
        pytest.param(
            one_team_game_panel().query("franchise_id != 'GSW'"),
            0,
            id="zero",
        ),
        pytest.param(
            pd.concat([
                one_team_game_panel(),
                one_team_game_panel().query("franchise_id == 'GSW'"),
            ], ignore_index=True),
            2,
            id="multiple",
        ),
    ],
)
def test_team_game_correction_requires_one_exact_row(
    frame, expected_count
):
    corrections = pd.DataFrame([{
        "game_id": "0022000001",
        "franchise_id": "GSW",
        "field": "pts",
        "corrected_value": "115",
        "source_url": "https://www.nba.com/game/example/box-score",
        "note": "Official NBA score correction.",
    }], columns=CORRECTION_COLUMNS)

    with pytest.raises(
        build.ValidationError,
        match=f"resolved {expected_count} rows",
    ):
        build.apply_team_game_corrections(frame, corrections)


@pytest.mark.parametrize(
    ("frame", "expected_count"),
    [
        pytest.param(
            one_valid_game(game_id="0022000002"),
            0,
            id="zero",
        ),
        pytest.param(
            pd.concat(
                [one_valid_game(), one_valid_game()],
                ignore_index=True,
            ),
            2,
            id="multiple",
        ),
    ],
)
def test_game_correction_requires_one_exact_row(frame, expected_count):
    corrections = pd.DataFrame([{
        "game_id": "0022000001",
        "franchise_id": pd.NA,
        "field": "attendance",
        "corrected_value": pd.NA,
        "source_url": "https://statsdmz.nba.com/gamebook.pdf",
        "note": "Official scorer report says attendance not counted.",
    }], columns=CORRECTION_COLUMNS)

    with pytest.raises(
        build.ValidationError,
        match=f"resolved {expected_count} rows",
    ):
        build.apply_game_corrections(frame, corrections)


def test_primary_window_non_neutral_venues_have_capacity():
    primary = pd.DataFrame([{
        "game_id": "0022000001", "season": 2021, "venue_id": "espn-17",
        "home_franchise_id": "BKN", "neutral_site": False,
        "attendance": 0,
    }])
    venues = pd.DataFrame([{
        "venue_id": "espn-17", "franchise_id": "BKN",
        "first_season": 2020, "last_season": 2022, "capacity": 17732,
        "capacity_source_url": "https://example.test/barclays-capacity",
        "capacity_note": pd.NA,
    }])
    joined = build.attach_capacity(primary, venues)
    assert joined["capacity"].notna().all()
    assert joined["capacity_source_url"].str.startswith(
        ("http://", "https://")
    ).all()
    assert (joined["capacity"] > 0).all()


def test_attach_capacity_selects_exact_inclusive_season_era():
    game = pd.DataFrame([{
        "game_id": "0022000001",
        "season": 2021,
        "venue_id": "espn-17",
        "home_franchise_id": "BKN",
        "neutral_site": False,
        "attendance": 17_000,
    }])
    venues = pd.DataFrame([
        {
            "venue_id": "espn-17",
            "franchise_id": "BKN",
            "first_season": 2020,
            "last_season": 2020,
            "capacity": 18_103,
            "capacity_source_url": "https://example.test/old",
            "capacity_note": pd.NA,
        },
        {
            "venue_id": "espn-17",
            "franchise_id": "BKN",
            "first_season": 2021,
            "last_season": 2022,
            "capacity": 17_732,
            "capacity_source_url": "https://example.test/new",
            "capacity_note": pd.NA,
        },
    ])

    attached = build.attach_capacity(game, venues).iloc[0]

    assert attached[[
        "capacity",
        "capacity_source_url",
    ]].to_dict() == {
        "capacity": 17_732,
        "capacity_source_url": "https://example.test/new",
    }


@pytest.mark.parametrize(
    ("venues", "expected_count"),
    [
        pytest.param(
            pd.DataFrame(columns=CAPACITY_COLUMNS),
            0,
            id="zero",
        ),
        pytest.param(
            pd.DataFrame([
                {
                    "venue_id": "espn-17",
                    "franchise_id": "BKN",
                    "first_season": 2020,
                    "last_season": 2022,
                    "capacity": 17_732,
                    "capacity_source_url": "https://example.test/one",
                    "capacity_note": pd.NA,
                },
                {
                    "venue_id": "espn-17",
                    "franchise_id": "BKN",
                    "first_season": 2021,
                    "last_season": 2021,
                    "capacity": 17_732,
                    "capacity_source_url": "https://example.test/two",
                    "capacity_note": pd.NA,
                },
            ]),
            2,
            id="multiple",
        ),
    ],
)
def test_attach_capacity_requires_one_season_aware_match(
    venues, expected_count
):
    game = pd.DataFrame([{
        "game_id": "0022000001",
        "season": 2021,
        "venue_id": "espn-17",
        "home_franchise_id": "BKN",
        "neutral_site": False,
        "attendance": 17_000,
    }])

    with pytest.raises(
        build.ValidationError,
        match=f"capacity reference resolved {expected_count} rows",
    ):
        build.attach_capacity(game, venues)


def test_load_venue_capacities_requires_positive_provenance(tmp_path):
    path = tmp_path / "venue_capacities.csv"
    pd.DataFrame([{
        "venue_id": "espn-17",
        "franchise_id": "BKN",
        "first_season": 2020,
        "last_season": 2022,
        "capacity": 17_732,
        "capacity_source_url": "https://example.test/barclays",
        "capacity_note": pd.NA,
    }], columns=CAPACITY_COLUMNS).to_csv(path, index=False)

    loaded = build.load_venue_capacities(path)

    assert loaded.iloc[0][[
        "capacity",
        "capacity_source_url",
    ]].to_dict() == {
        "capacity": 17_732,
        "capacity_source_url": "https://example.test/barclays",
    }

    pd.DataFrame([{
        "venue_id": "espn-17",
        "franchise_id": "BKN",
        "first_season": 2020,
        "last_season": 2022,
        "capacity": 0,
        "capacity_source_url": "barclays.example/capacity",
        "capacity_note": pd.NA,
    }], columns=CAPACITY_COLUMNS).to_csv(path, index=False)
    with pytest.raises(
        build.ValidationError,
        match="positive capacity and direct source URL",
    ):
        build.load_venue_capacities(path)


def test_over_capacity_requires_documented_configuration_exception():
    game = pd.DataFrame([{
        "game_id": "0022000001",
        "season": 2021,
        "venue_id": "espn-17",
        "home_franchise_id": "BKN",
        "neutral_site": False,
        "attendance": 18_000,
    }])
    venue = {
        "venue_id": "espn-17",
        "franchise_id": "BKN",
        "first_season": 2020,
        "last_season": 2022,
        "capacity": 17_000,
        "capacity_source_url": "https://example.test/barclays",
        "capacity_note": pd.NA,
    }
    with pytest.raises(
        build.ValidationError,
        match="attendance above 105% of capacity",
    ):
        build.attach_capacity(game, pd.DataFrame([venue]))

    documented = venue | {
        "capacity_note": (
            "ATTENDANCE_EXCEPTION: Standing-room configuration "
            "documented for this game era."
        )
    }
    attached = build.attach_capacity(
        game, pd.DataFrame([documented])
    )
    assert attached.iloc[0]["capacity_note"] == (
        "ATTENDANCE_EXCEPTION: Standing-room configuration "
        "documented for this game era."
    )


def test_over_capacity_rejects_unrelated_nonblank_note():
    game = pd.DataFrame([{
        "game_id": "0022000001",
        "season": 2021,
        "venue_id": "espn-17",
        "home_franchise_id": "BKN",
        "neutral_site": False,
        "attendance": 18_000,
    }])
    venue = pd.DataFrame([{
        "venue_id": "espn-17",
        "franchise_id": "BKN",
        "first_season": 2020,
        "last_season": 2022,
        "capacity": 17_000,
        "capacity_source_url": "https://example.test/barclays",
        "capacity_note": "NBA.com basketball seating capacity.",
    }])

    with pytest.raises(
        build.ValidationError,
        match="explicit configuration exception",
    ):
        build.attach_capacity(game, venue)


@pytest.mark.integration
def test_real_primary_capacity_exceptions_are_explicit():
    data = Path(__file__).resolve().parents[1] / "data"
    _, attached, report = build.assemble(data / "raw")
    primary = (
        attached["season"].between(2020, 2022)
        & ~attached["neutral_site"]
    )
    over = (
        primary
        & attached["attendance"].notna()
        & attached["attendance"].gt(attached["capacity"] * 1.05)
    )

    assert int(over.sum()) == 61
    assert attached.loc[over, "capacity_note"].str.startswith(
        "ATTENDANCE_EXCEPTION: "
    ).all()
    bubble = attached[
        attached["game_id"].between("0021901231", "0021901318")
    ]
    explicit_zero = bubble[bubble["game_id"].eq("0021901233")].iloc[0]
    assert len(bubble) == 88
    assert bubble["neutral_site"].all()
    assert (
        bubble["attendance_coverage_exemption_reason"].notna().sum()
        == 87
    )
    assert report["selected_attendance_exemptions"][2020] == 87
    assert report["selected_attendance_eligible_denominator"][2020] == 972
    assert explicit_zero["attendance"] == 0
    assert pd.isna(
        explicit_zero["attendance_coverage_exemption_reason"]
    )


def test_nba_id_remains_canonical_after_espn_join():
    nba = one_nba_game(game_id="0022000001")
    schedule = one_schedule_game(espn_game_id="espn-401300001")

    result = build.enrich_modern_games(nba, schedule, bref=pd.DataFrame())

    assert result.iloc[0]["game_id"] == "0022000001"
    assert result.iloc[0]["espn_game_id"] == "espn-401300001"


def test_matched_bref_blank_does_not_fall_back_to_espn():
    game = build.enrich_modern_games(
        one_nba_game(),
        one_schedule_game(espn_attendance=1_500),
        one_bref_game(attendance=pd.NA),
    ).iloc[0]

    assert game["bref_row_matched"]
    assert pd.isna(game["attendance"])
    assert game["attendance_status"] == "missing"
    assert game["attendance_source"] == "bref"


def test_unmatched_bref_uses_espn():
    game = build.enrich_modern_games(
        one_nba_game(),
        one_schedule_game(espn_attendance=1_500),
        pd.DataFrame(),
    ).iloc[0]

    assert game["attendance"] == 1_500
    assert game["attendance_source"] == "espn"


def test_bref_arena_takes_precedence_only_when_nonblank():
    bref_game = build.enrich_modern_games(
        one_nba_game(),
        one_schedule_game(),
        one_bref_game(arena="B-Ref Arena"),
    ).iloc[0]
    blank_game = build.enrich_modern_games(
        one_nba_game(),
        one_schedule_game(),
        one_bref_game(arena="  "),
    ).iloc[0]

    assert (bref_game["venue_name"], bref_game["venue_source"]) == (
        "B-Ref Arena",
        "bref",
    )
    assert (blank_game["venue_name"], blank_game["venue_source"]) == (
        "Barclays Center",
        "espn",
    )


def test_missing_hoopr_crosswalk_is_rejected():
    assert_invalid_schedule = "NBA games without hoopR schedule"
    with pytest.raises(build.ValidationError) as caught:
        build.enrich_modern_games(
            one_nba_game(),
            one_schedule_game().assign(home_franchise_id="BOS"),
            pd.DataFrame(),
        )
    assert assert_invalid_schedule in str(caught.value)


def test_unique_next_day_hoopr_game_reconciles_to_nba_date():
    schedule = one_schedule_game().assign(
        game_date=datetime.date(2022, 10, 19)
    )

    game = build.enrich_modern_games(
        one_nba_game(),
        schedule,
        one_bref_game(),
    ).iloc[0]

    assert game[[
        "game_id",
        "espn_game_id",
        "game_date",
        "venue_id",
        "attendance",
    ]].to_dict() == {
        "game_id": "0022000001",
        "espn_game_id": "espn-401300001",
        "game_date": datetime.date(2022, 10, 18),
        "venue_id": "espn-17",
        "attendance": 18_000,
    }


def test_next_day_reconciliation_preserves_back_to_back_exact_game():
    """Would fail if duplicate exact keys claimed every schedule row."""
    nba_games = pd.concat([
        one_nba_game(game_id="0022000001"),
        one_nba_game(game_id="0022000002").assign(
            game_date=datetime.date(2022, 10, 19)
        ),
    ], ignore_index=True)
    schedule = pd.concat([
        one_schedule_game(espn_game_id="espn-401300001").assign(
            game_date=datetime.date(2022, 10, 19)
        ),
        one_schedule_game(espn_game_id="espn-401300002").assign(
            game_date=datetime.date(2022, 10, 19)
        ),
    ], ignore_index=True)
    bref = pd.concat([
        one_bref_game(),
        one_bref_game().assign(
            game_date=datetime.date(2022, 10, 19)
        ),
    ], ignore_index=True)

    enriched = build.enrich_modern_games(
        nba_games,
        schedule,
        bref,
    )

    assert enriched[[
        "game_id",
        "espn_game_id",
        "game_date",
    ]].to_dict("records") == [
        {
            "game_id": "0022000001",
            "espn_game_id": "espn-401300001",
            "game_date": datetime.date(2022, 10, 18),
        },
        {
            "game_id": "0022000002",
            "espn_game_id": "espn-401300002",
            "game_date": datetime.date(2022, 10, 19),
        },
    ]


@pytest.mark.parametrize(
    "team_games",
    [
        pytest.param(
            one_team_game_panel().iloc[[0]].copy(),
            id="only-one-row",
        ),
        pytest.param(
            one_team_game_panel().assign(is_home=True),
            id="two-home-rows",
        ),
    ],
)
def test_pairing_rejects_games_without_exactly_one_home_and_one_away(team_games):
    with pytest.raises(build.ValidationError, match="one home and one away"):
        build.pair_team_games(team_games)


def test_pairing_constructs_one_canonical_game():
    result = build.pair_team_games(one_team_game_panel())

    assert result.to_dict("records") == [{
        "game_id": "0022000001",
        "season": 2023,
        "source": "nba_stats",
        "game_date": datetime.date(2022, 10, 18),
        "home_franchise_id": "BKN",
        "away_franchise_id": "GSW",
        "home_pts": 125,
        "away_pts": 99,
    }]


def test_validation_rejects_duplicate_game_id():
    team_games = one_team_game_panel()
    games = pd.concat([one_valid_game(), one_valid_game()], ignore_index=True)

    assert_invalid(
        team_games, games, {2023: 1}, "duplicate game_id"
    )


def test_validation_rejects_games_team_games_id_set_mismatch():
    team_games = one_team_game_panel(game_id="0022000002")
    games = one_valid_game(game_id="0022000001")

    assert_invalid(
        team_games, games, {2023: 1}, "games/team_games ID-set mismatch"
    )


@pytest.mark.parametrize(
    "team_games",
    [
        pytest.param(one_team_game_panel().iloc[[0]].copy(), id="one-row"),
        pytest.param(
            one_team_game_panel().assign(is_home=True),
            id="not-one-home-one-away",
        ),
    ],
)
def test_validation_rejects_invalid_team_row_cardinality(team_games):
    assert_invalid(
        team_games,
        one_valid_game(),
        {2023: 1},
        "games without one home and one away row",
    )


@pytest.mark.parametrize(
    ("team_column", "bad_value"),
    [
        pytest.param("franchise_id", "BOS", id="team-id"),
        pytest.param("pts", 124, id="score"),
        pytest.param("game_date", datetime.date(2022, 10, 19), id="date"),
        pytest.param("season", 2024, id="season"),
    ],
)
def test_validation_rejects_team_game_disagreement(team_column, bad_value):
    team_games = one_team_game_panel()
    team_games.loc[team_games["is_home"], team_column] = bad_value

    assert_invalid(
        team_games, one_valid_game(), {2023: 1}, "team/game disagreement"
    )


def test_validation_rejects_unknown_franchise():
    team_games = one_team_game_panel()
    games = one_valid_game()
    team_games.loc[team_games["is_home"], "franchise_id"] = "XXX"
    games.loc[0, "home_franchise_id"] = "XXX"

    assert_invalid(
        team_games, games, {2023: 1}, "unknown franchise"
    )


def test_validation_rejects_missing_expected_season():
    assert_invalid(
        one_team_game_panel(),
        one_valid_game(),
        {2024: 1},
        "missing expected season",
    )


def test_validation_rejects_wrong_per_season_count():
    assert_invalid(
        one_team_game_panel(),
        one_valid_game(),
        {2023: 2},
        "season 2023 games: 1, expected 2",
    )


def test_validation_rejects_wrong_legacy_count():
    assert_invalid(
        one_team_game_panel(),
        one_valid_game(),
        {2024: 0},
        "legacy games: 1, expected 0",
    )


@pytest.mark.parametrize(
    ("home_pts", "away_pts"),
    [
        pytest.param(39, 99, id="below-range"),
        pytest.param(201, 99, id="above-range"),
        pytest.param(99, 99, id="tie"),
    ],
)
def test_validation_rejects_score_outside_range_or_tie(home_pts, away_pts):
    team_games = one_team_game_panel()
    games = one_valid_game()
    games.loc[0, ["home_pts", "away_pts"]] = [home_pts, away_pts]
    team_games.loc[team_games["is_home"], "pts"] = home_pts
    team_games.loc[~team_games["is_home"], "pts"] = away_pts

    assert_invalid(
        team_games, games, {2023: 1}, "invalid score or tie"
    )


def test_validation_rejects_wl_that_disagrees_with_score():
    """Would fail if a losing team could retain wl=1 after score correction."""
    team_games = one_team_game_panel()
    team_games.loc[team_games["is_home"], "wl"] = 0
    team_games.loc[~team_games["is_home"], "wl"] = 1

    assert_invalid(
        team_games,
        one_valid_game(),
        {2023: 1},
        "wl disagrees with final score",
    )


def test_validation_rejects_game_without_exactly_one_winner():
    """Would fail if both team rows could be published as winners."""
    team_games = one_team_game_panel().assign(wl=1)

    assert_invalid(
        team_games,
        one_valid_game(),
        {2023: 1},
        "game without exactly one winner",
    )


def test_validation_rejects_negative_box_statistic():
    """Would fail if a negative deterministic box value were accepted."""
    team_games = one_team_game_panel()
    team_games.loc[team_games["is_home"], "ast"] = -1

    assert_invalid(
        team_games,
        one_valid_game(),
        {2023: 1},
        "negative box statistic",
    )


@pytest.mark.parametrize(
    ("made", "attempted"),
    [
        pytest.param("fgm", "fga", id="field-goals"),
        pytest.param("fg3m", "fg3a", id="three-pointers"),
        pytest.param("ftm", "fta", id="free-throws"),
    ],
)
def test_validation_rejects_makes_above_attempts(made, attempted):
    """Would fail if a made-shot total could exceed its attempt total."""
    team_games = one_team_game_panel()
    home = team_games["is_home"]
    team_games.loc[home, made] = team_games.loc[home, attempted] + 1

    assert_invalid(
        team_games,
        one_valid_game(),
        {2023: 1},
        "makes exceed attempts",
    )


def test_validation_rejects_rebound_component_disagreement():
    """Would fail if offensive plus defensive rebounds differed from total."""
    team_games = one_team_game_panel()
    team_games.loc[team_games["is_home"], "dreb"] = 29

    assert_invalid(
        team_games,
        one_valid_game(),
        {2023: 1},
        "rebound component disagreement",
    )


def test_validation_rejects_point_accounting_disagreement():
    """Would fail if points differed from 2*FGM + FG3M + FTM."""
    team_games = one_team_game_panel()
    team_games.loc[team_games["is_home"], "fgm"] = 44

    assert_invalid(
        team_games,
        one_valid_game(),
        {2023: 1},
        "point accounting disagreement",
    )


def test_validation_rejects_missing_box_statistic():
    team_games = one_team_game_panel()
    team_games.loc[team_games["is_home"], "ast"] = pd.NA

    assert_invalid(
        team_games, one_valid_game(), {2023: 1}, "missing box statistic"
    )


def test_validation_rejects_reported_zero_outside_2020_through_2022():
    games = one_valid_game()
    games.loc[0, ["attendance_bref", "attendance", "attendance_status"]] = [
        0,
        0,
        "reported_zero",
    ]

    assert_invalid(
        one_team_game_panel(),
        games,
        {2023: 1},
        "reported_zero outside 2020..2022",
    )


def test_validation_rejects_low_bref_join_coverage():
    games = one_valid_game()
    games.loc[0, "bref_row_matched"] = False
    games.loc[0, "attendance_source"] = "espn"

    assert_invalid(
        one_team_game_panel(),
        games,
        {2023: 1},
        "b-ref join coverage below 95%",
    )


def test_validation_rejects_low_selected_known_attendance_coverage():
    games = one_valid_game()
    games.loc[0, ["attendance_bref", "attendance"]] = pd.NA
    games.loc[0, "attendance_status"] = "missing"

    assert_invalid(
        one_team_game_panel(),
        games,
        {2023: 1},
        "selected known-attendance coverage below 95%",
    )


def test_validation_exempts_only_missing_no_fan_bubble_attendance():
    missing_id = "0021901231"
    zero_id = "0021901233"
    team_games = pd.concat([
        one_team_game_panel(game_id=missing_id, season=2020),
        one_team_game_panel(game_id=zero_id, season=2020),
    ], ignore_index=True)
    missing = one_valid_game(game_id=missing_id, season=2020)
    missing.loc[0, [
        "neutral_site",
        "neutral_site_source",
        "attendance_coverage_exemption_reason",
        "attendance_bref",
        "attendance_espn",
        "attendance",
        "attendance_status",
    ]] = [
        True,
        "game_correction:neutral_site",
        "no_fan_bubble_missing_attendance",
        pd.NA,
        0,
        pd.NA,
        "missing",
    ]
    explicit_zero = one_valid_game(game_id=zero_id, season=2020)
    explicit_zero.loc[0, [
        "neutral_site",
        "neutral_site_source",
        "attendance_coverage_exemption_reason",
        "attendance_bref",
        "attendance_espn",
        "attendance",
        "attendance_status",
    ]] = [
        True,
        "game_correction:neutral_site",
        pd.NA,
        0,
        0,
        0,
        "reported_zero",
    ]
    games = pd.concat([missing, explicit_zero], ignore_index=True)

    report = build.validate(
        team_games,
        games,
        expected_counts={2020: 2},
        expected_legacy=0,
    )

    assert report["attendance_counts"][2020] == {
        "reported_zero": 1,
        "reported_positive": 0,
        "missing": 1,
    }
    assert report["selected_attendance_raw_coverage"][2020] == 0.5
    assert report["selected_attendance_eligible_denominator"][2020] == 1
    assert report["selected_attendance_exemptions"][2020] == 1
    assert report["selected_attendance_coverage"][2020] == 1.0


def test_unrelated_neutral_correction_is_not_coverage_exempt():
    known_id = "0021900001"
    neutral_id = "0021900002"
    team_games = pd.concat([
        one_team_game_panel(game_id=known_id, season=2020),
        one_team_game_panel(game_id=neutral_id, season=2020),
    ], ignore_index=True)
    known = one_valid_game(game_id=known_id, season=2020)
    missing = one_valid_game(game_id=neutral_id, season=2020)
    missing.loc[0, [
        "attendance_bref",
        "attendance",
        "attendance_status",
    ]] = [pd.NA, pd.NA, "missing"]
    corrections = pd.DataFrame([{
        "game_id": neutral_id,
        "franchise_id": pd.NA,
        "field": "neutral_site",
        "corrected_value": "true",
        "source_url": "https://www.nba.com/news/neutral-game",
        "note": "Official neutral-site game unrelated to the no-fan bubble.",
        "attendance_coverage_exemption_reason": (
            "no_fan_bubble_missing_attendance"
        ),
    }])
    games = build.apply_game_corrections(
        pd.concat([known, missing], ignore_index=True),
        corrections,
    )

    with pytest.raises(
        build.ValidationError,
        match="selected known-attendance coverage below 95%",
    ):
        build.validate(
            team_games,
            games,
            expected_counts={2020: 2},
            expected_legacy=0,
        )


def test_validation_rejects_non_neutral_primary_venue_without_capacity():
    team_games = one_team_game_panel(season=2021)
    games = one_valid_game(season=2021)
    games.loc[0, "capacity"] = pd.NA
    team_games.loc[:, "game_date"] = datetime.date(2020, 12, 22)
    games.loc[0, "game_date"] = datetime.date(2020, 12, 22)

    assert_invalid(
        team_games,
        games,
        {2021: 1},
        "non-neutral 2020..2022 venue missing capacity",
    )


def test_validation_rejects_primary_venue_when_capacity_column_is_absent():
    team_games = one_team_game_panel(season=2021)
    games = one_valid_game(season=2021).drop(columns="capacity")
    team_games.loc[:, "game_date"] = datetime.date(2020, 12, 22)
    games.loc[0, "game_date"] = datetime.date(2020, 12, 22)

    assert_invalid(
        team_games,
        games,
        {2021: 1},
        "non-neutral 2020..2022 venue missing capacity",
    )


def test_validation_rejects_unknown_attendance_status():
    games = one_valid_game()
    games.loc[0, "attendance_status"] = "bogus"

    assert_invalid(
        one_team_game_panel(),
        games,
        {2023: 1},
        "unsupported attendance_status",
    )


def test_validation_rejects_unknown_attendance_source():
    games = one_valid_game()
    games.loc[0, "attendance_source"] = "bogus"

    assert_invalid(
        one_team_game_panel(),
        games,
        {2023: 1},
        "unsupported attendance_source",
    )


@pytest.mark.parametrize(
    ("attendance", "status"),
    [
        pytest.param(0, "reported_positive", id="zero-as-positive"),
        pytest.param(100, "reported_zero", id="positive-as-zero"),
        pytest.param(pd.NA, "reported_positive", id="missing-as-positive"),
        pytest.param(100, "missing", id="positive-as-missing"),
    ],
)
def test_validation_rejects_attendance_status_value_disagreement(
    attendance, status
):
    team_games = one_team_game_panel(season=2021)
    games = one_valid_game(season=2021)
    games.loc[0, "attendance"] = attendance
    games.loc[0, "attendance_status"] = status

    assert_invalid(
        team_games,
        games,
        {2021: 1},
        "attendance status/value disagreement",
    )


def test_validation_accumulates_all_diagnostics_before_raising():
    games = pd.concat([one_valid_game(), one_valid_game()], ignore_index=True)
    games.loc[:, ["home_pts", "away_pts"]] = [99, 99]
    team_games = one_team_game_panel()
    team_games.loc[:, "pts"] = 99

    with pytest.raises(build.ValidationError) as caught:
        build.validate(
            team_games,
            games,
            expected_counts={2023: 1},
            expected_legacy=0,
        )

    assert "duplicate game_id" in str(caught.value)
    assert "invalid score or tie" in str(caught.value)


def test_validation_returns_literal_coverage_report():
    report = build.validate(
        one_team_game_panel(),
        one_valid_game(),
        expected_counts={2023: 1},
        expected_legacy=0,
    )

    assert report == {
        "totals": {"games": 1, "team_games": 2},
        "games_by_season": {2023: 1},
        "team_games_by_season": {2023: 2},
        "bref_join_coverage": {2023: 1.0},
        "attendance_counts": {
            2023: {
                "reported_zero": 0,
                "reported_positive": 1,
                "missing": 0,
            },
        },
        "selected_attendance_raw_coverage": {2023: 1.0},
        "selected_attendance_eligible_denominator": {2023: 1},
        "selected_attendance_exemptions": {2023: 0},
        "selected_attendance_coverage": {2023: 1.0},
        "bref_espn_agreement": {2023: 1.0},
    }


def stub_one_game_assembly(
    monkeypatch, season=2023, home_pts=125, away_pts=99
):
    legacy_team_games = one_team_game_panel(
        game_id="0021800001", season=2019
    )
    modern_team_games = one_team_game_panel(season=season)
    modern_team_games.loc[
        modern_team_games["is_home"], "pts"
    ] = home_pts
    modern_team_games.loc[
        ~modern_team_games["is_home"], "pts"
    ] = away_pts
    legacy_attendance = pd.DataFrame([{
        "game_id": "0021800001",
        "attendance": 17_000,
    }])
    monkeypatch.setattr(
        build.stats_nba, "SEASONS", {season: "fixture-season"}
    )
    monkeypatch.setattr(build.stats_nba, "EXPECTED_GAMES", {season: 1})
    monkeypatch.setattr(build, "EXPECTED_LEGACY_GAMES", 1)
    monkeypatch.setattr(
        build,
        "load_game_corrections",
        lambda path: pd.DataFrame(columns=CORRECTION_COLUMNS),
    )
    monkeypatch.setattr(
        build,
        "load_venue_capacities",
        lambda path: pd.DataFrame(columns=CAPACITY_COLUMNS),
    )
    monkeypatch.setattr(
        build.legacy, "load_team_games", lambda path: legacy_team_games
    )
    monkeypatch.setattr(
        build.legacy, "load_attendance", lambda path: legacy_attendance
    )
    monkeypatch.setattr(
        build.stats_nba,
        "load_season",
        lambda raw_dir, season: modern_team_games,
    )
    monkeypatch.setattr(
        build.hoopr,
        "load_season",
        lambda raw_dir, requested_season: (
            one_schedule_game(season=season),
            pd.DataFrame(),
        ),
    )
    monkeypatch.setattr(
        build.bref,
        "load_attendance",
        lambda raw_dir, requested_season: one_bref_game(season=season),
    )


def test_assemble_attaches_capacity_before_strict_validation(
    monkeypatch, tmp_path
):
    stub_one_game_assembly(monkeypatch, season=2021)
    monkeypatch.setattr(
        build,
        "load_venue_capacities",
        lambda path: pd.DataFrame([{
            "venue_id": "espn-17",
            "franchise_id": "BKN",
            "first_season": 2021,
            "last_season": 2021,
            "capacity": 18_064,
            "capacity_source_url": "https://example.com/chase-center",
            "capacity_note": "Basketball configuration.",
        }]),
    )

    _, games, _ = build.assemble(tmp_path / "raw")

    modern = games.loc[games["season"].eq(2021)].iloc[0]
    assert modern["capacity"] == 18_064
    assert modern["capacity_source_url"] == (
        "https://example.com/chase-center"
    )
    assert modern["capacity_note"] == "Basketball configuration."


def test_assemble_applies_corrections_before_publication_frames(
    monkeypatch, tmp_path
):
    stub_one_game_assembly(monkeypatch)
    corrections = pd.DataFrame([
        {
            "game_id": "0022000001",
            "franchise_id": "GSW",
            "field": "pts",
            "corrected_value": "115",
            "source_url": "https://www.nba.com/game/example/box-score",
            "note": "Official NBA score correction.",
        },
        {
            "game_id": "0022000001",
            "franchise_id": pd.NA,
            "field": "attendance",
            "corrected_value": pd.NA,
            "source_url": "https://statsdmz.nba.com/gamebook.pdf",
            "note": "Official attendance was not counted.",
        },
    ], columns=CORRECTION_COLUMNS)
    monkeypatch.setattr(
        build, "load_game_corrections", lambda path: corrections
    )

    team_games, games, _ = build._assemble_frames(tmp_path / "raw")
    corrected_team = team_games[
        team_games["game_id"].eq("0022000001")
        & team_games["franchise_id"].eq("GSW")
    ].iloc[0]
    corrected_game = games[games["game_id"].eq("0022000001")].iloc[0]

    assert corrected_team["pts"] == 115
    assert corrected_game["away_pts"] == 115
    assert pd.isna(corrected_game["attendance"])
    assert corrected_game["attendance_source"] == "bref"
    assert corrected_game["attendance_status"] == "missing"


def test_assemble_loads_legacy_attendance_from_game_cache(
    monkeypatch, tmp_path
):
    stub_one_game_assembly(monkeypatch)
    loaded_paths = {}
    team_games = one_team_game_panel(
        game_id="0021800001", season=2019
    )
    attendance = pd.DataFrame([{
        "game_id": "0021800001",
        "attendance": 17_000,
    }])

    def load_team_games(path):
        loaded_paths["team_games"] = path
        return team_games

    def load_attendance(path):
        loaded_paths["attendance"] = path
        return attendance

    monkeypatch.setattr(
        build.legacy, "load_team_games", load_team_games
    )
    monkeypatch.setattr(
        build.legacy, "load_attendance", load_attendance
    )

    build._assemble_frames(tmp_path / "raw")

    assert loaded_paths == {
        "team_games": (
            tmp_path
            / "raw/legacy/nba-teams-1984-2019-ordered.csv"
        ),
        "attendance": (
            tmp_path / "raw/legacy/league_boxscores_extra.csv"
        ),
    }


def test_assemble_uses_schedule_home_designation_for_neutral_game(
    monkeypatch, tmp_path
):
    stub_one_game_assembly(monkeypatch)
    neutral_team_games = one_team_game_panel().assign(is_home=False)
    monkeypatch.setattr(
        build.stats_nba,
        "load_season",
        lambda raw_dir, season: neutral_team_games,
    )
    monkeypatch.setattr(
        build.hoopr,
        "load_season",
        lambda raw_dir, season: (
            one_schedule_game().assign(neutral_site=True),
            pd.DataFrame(),
        ),
    )

    team_games, games, _ = build._assemble_frames(tmp_path / "raw")
    modern_teams = team_games[team_games["season"].eq(2023)]
    modern_game = games[games["season"].eq(2023)].iloc[0]

    assert modern_teams.set_index("franchise_id")[
        "is_home"
    ].to_dict() == {"BKN": True, "GSW": False}
    assert (
        modern_game["home_franchise_id"],
        modern_game["away_franchise_id"],
        modern_game["neutral_site"],
    ) == ("BKN", "GSW", True)


def test_assemble_combines_legacy_and_modern_panels(monkeypatch, tmp_path):
    stub_one_game_assembly(monkeypatch)

    team_games, games, report = build.assemble(tmp_path)

    assert team_games["game_id"].tolist() == [
        "0021800001",
        "0021800001",
        "0022000001",
        "0022000001",
    ]
    assert games[["game_id", "attendance_source"]].to_dict("records") == [
        {"game_id": "0021800001", "attendance_source": "legacy"},
        {"game_id": "0022000001", "attendance_source": "bref"},
    ]
    assert report["totals"] == {"games": 2, "team_games": 4}


def test_assemble_reports_deterministic_hoopr_exclusions(
    monkeypatch, tmp_path
):
    stub_one_game_assembly(monkeypatch)
    excluded = pd.DataFrame([
        {"game_id": 401000001, "reason": "exhibition"},
        {"game_id": 401000002, "reason": "exhibition"},
        {"game_id": 401000003, "reason": "cup_championship"},
    ])
    monkeypatch.setattr(
        build.hoopr,
        "load_season",
        lambda raw_dir, requested_season: (
            one_schedule_game(season=2023),
            excluded,
        ),
    )

    team_games, games, report = build.assemble(tmp_path / "raw")

    expected = {
        2023: {
            "total": 3,
            "by_reason": {
                "cup_championship": 1,
                "exhibition": 2,
            },
            "espn_game_ids": [
                "espn-401000001",
                "espn-401000002",
                "espn-401000003",
            ],
        }
    }
    assert report["schedule_exclusions"] == expected
    seed_publication_references(tmp_path)
    monkeypatch.setattr(
        build,
        "assemble",
        lambda raw_dir: (team_games, games, report),
    )

    build.run(tmp_path)

    manifest = json.loads(
        (tmp_path / "parquet/build_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["validation"]["schedule_exclusions"] == {
        str(season): value for season, value in expected.items()
    }


def test_assemble_enforces_exact_output_columns_and_dtypes(
    monkeypatch, tmp_path
):
    stub_one_game_assembly(monkeypatch)

    team_games, games, _ = build.assemble(tmp_path)

    assert team_games.columns.tolist() == [
        "game_id",
        "season",
        "source",
        "franchise_id",
        "opponent_franchise_id",
        "is_home",
        "game_date",
        "wl",
        "pts",
        "fga",
        "fgm",
        "fg3a",
        "fg3m",
        "fta",
        "ftm",
        "oreb",
        "dreb",
        "reb",
        "ast",
        "stl",
        "blk",
        "tov",
        "pf",
    ]
    assert team_games.dtypes.astype(str).to_dict() == {
        "game_id": "string",
        "season": "int64",
        "source": "string",
        "franchise_id": "string",
        "opponent_franchise_id": "string",
        "is_home": "bool",
        "game_date": "object",
        "wl": "int64",
        "pts": "Int64",
        "fga": "Int64",
        "fgm": "Int64",
        "fg3a": "Int64",
        "fg3m": "Int64",
        "fta": "Int64",
        "ftm": "Int64",
        "oreb": "Int64",
        "dreb": "Int64",
        "reb": "Int64",
        "ast": "Int64",
        "stl": "Int64",
        "blk": "Int64",
        "tov": "Int64",
        "pf": "Int64",
    }
    assert games.columns.tolist() == [
        "game_id",
        "espn_game_id",
        "season",
        "source",
        "game_date",
        "home_franchise_id",
        "away_franchise_id",
        "home_pts",
        "away_pts",
        "neutral_site",
        "neutral_site_source",
        "venue_id",
        "venue_name",
        "venue_city",
        "venue_source",
        "attendance_bref",
        "attendance_espn",
        "attendance",
        "bref_row_matched",
        "attendance_status",
        "attendance_source",
        "attendance_coverage_exemption_reason",
        "capacity",
        "capacity_source_url",
        "capacity_note",
    ]
    assert games.dtypes.astype(str).to_dict() == {
        "game_id": "string",
        "espn_game_id": "string",
        "season": "int64",
        "source": "string",
        "game_date": "object",
        "home_franchise_id": "string",
        "away_franchise_id": "string",
        "home_pts": "int64",
        "away_pts": "int64",
        "neutral_site": "bool",
        "neutral_site_source": "string",
        "venue_id": "string",
        "venue_name": "string",
        "venue_city": "string",
        "venue_source": "string",
        "attendance_bref": "Int64",
        "attendance_espn": "Int64",
        "attendance": "Int64",
        "bref_row_matched": "bool",
        "attendance_status": "string",
        "attendance_source": "string",
        "attendance_coverage_exemption_reason": "string",
        "capacity": "Int64",
        "capacity_source_url": "string",
        "capacity_note": "string",
    }
    assert all(
        isinstance(value, datetime.date)
        for value in team_games["game_date"]
    )
    assert all(
        isinstance(value, datetime.date) for value in games["game_date"]
    )


def test_write_venue_inventory_uses_capacity_only_validation_exception(
    monkeypatch, tmp_path
):
    stub_one_game_assembly(monkeypatch, season=2021)
    team_games = one_team_game_panel(season=2021)
    games = one_valid_game(season=2021).drop(columns="capacity")

    assert_invalid(
        team_games,
        games,
        {2021: 1},
        "non-neutral 2020..2022 venue missing capacity",
    )
    with pytest.raises(
        build.ValidationError,
        match="capacity reference resolved 0 rows",
    ):
        build.assemble(tmp_path / "raw")

    destination = build.write_venue_inventory(tmp_path)
    inventory = pd.read_csv(destination)

    assert destination == tmp_path / "raw/reports/venue_inventory.csv"
    assert inventory[[
        "venue_id",
        "home_franchise_id",
        "first_season",
        "last_season",
        "games",
    ]].to_dict("records") == [{
        "venue_id": "espn-17",
        "home_franchise_id": "BKN",
        "first_season": 2021,
        "last_season": 2021,
        "games": 1,
    }]


def test_write_venue_inventory_still_rejects_invalid_score(
    monkeypatch, tmp_path
):
    stub_one_game_assembly(
        monkeypatch,
        season=2021,
        home_pts=99,
        away_pts=99,
    )

    with pytest.raises(build.ValidationError) as caught:
        build.write_venue_inventory(tmp_path)

    assert "invalid score or tie" in str(caught.value)
    assert "venue missing capacity" not in str(caught.value)
    assert not (tmp_path / "raw/reports/venue_inventory.csv").exists()


def test_write_venue_inventory_reports_non_covid_observed_limits(
    monkeypatch, tmp_path
):
    games = pd.DataFrame([
        {
            "game_id": "0022100001",
            "season": 2022,
            "venue_id": "espn-17",
            "venue_name": "Barclays Center",
            "venue_city": "Brooklyn",
            "home_franchise_id": "BKN",
            "neutral_site": False,
            "attendance": 10_000,
        },
        {
            "game_id": "0022200001",
            "season": 2023,
            "venue_id": "espn-17",
            "venue_name": "Barclays Center",
            "venue_city": "Brooklyn",
            "home_franchise_id": "BKN",
            "neutral_site": False,
            "attendance": 17_000,
        },
        {
            "game_id": "0022300001",
            "season": 2024,
            "venue_id": "espn-17",
            "venue_name": "Barclays Center",
            "venue_city": "Brooklyn",
            "home_franchise_id": "BKN",
            "neutral_site": False,
            "attendance": 18_000,
        },
    ])
    direct_inventory = build.venue_inventory(games)
    assert direct_inventory.iloc[0].to_dict() == {
        "venue_id": "espn-17",
        "venue_name": "Barclays Center",
        "venue_city": "Brooklyn",
        "home_franchise_id": "BKN",
        "first_season": 2022,
        "last_season": 2024,
        "games": 3,
        "observed_non_covid_max": 18_000,
        "observed_non_covid_p99": 17_980,
    }
    assembled = lambda raw_dir: (pd.DataFrame(), games, {})
    monkeypatch.setattr(
        build,
        "_assemble_for_venue_inventory",
        assembled,
    )

    destination = build.write_venue_inventory(tmp_path)
    inventory = pd.read_csv(destination)

    assert destination == tmp_path / "raw/reports/venue_inventory.csv"
    assert inventory.to_dict("records") == [{
        "venue_id": "espn-17",
        "venue_name": "Barclays Center",
        "venue_city": "Brooklyn",
        "home_franchise_id": "BKN",
        "first_season": 2022,
        "last_season": 2024,
        "games": 3,
        "observed_non_covid_max": 18_000,
        "observed_non_covid_p99": 17_980,
    }]


def seed_publication_references(data_dir):
    reference_dir = data_dir / "reference"
    reference_dir.mkdir(parents=True)
    (reference_dir / "franchises.csv").write_bytes(
        franchises.REFERENCE.read_bytes()
    )
    (reference_dir / "venue_capacities.csv").write_text(
        "venue_id,franchise_id,first_season,last_season,capacity,"
        "capacity_source_url,capacity_note\n"
        "espn-1,ATL,2020,2026,18000,https://example.test/arena,"
        "Fixture capacity.\n",
        encoding="utf-8",
    )
    (reference_dir / "game_corrections.csv").write_text(
        ",".join(CORRECTION_COLUMNS) + "\n",
        encoding="utf-8",
    )
    (reference_dir / "hoopr_snapshot.json").write_bytes(
        hoopr.SNAPSHOT_PATH.read_bytes()
    )


def publication_fixture(monkeypatch, data_dir):
    stub_one_game_assembly(monkeypatch)
    team_games, games, report = build.assemble(data_dir / "raw")
    monkeypatch.setattr(
        build,
        "assemble",
        lambda raw_dir: (team_games.copy(), games.copy(), report.copy()),
    )
    seed_publication_references(data_dir)
    return team_games, games


def test_run_requires_checkout_hoopr_pin(monkeypatch, tmp_path):
    """Would fail if a build could attest a pin absent from its data tree."""
    data_dir = tmp_path / "data"
    publication_fixture(monkeypatch, data_dir)
    (data_dir / "reference/hoopr_snapshot.json").unlink()

    with pytest.raises(
        build.ValidationError,
        match="hoopR snapshot reference is missing",
    ):
        build.run(data_dir)


def test_raw_provenance_rejects_checkout_hoopr_pin_divergence(tmp_path):
    """Would fail if the manifest pin could differ from cache validation."""
    data_dir = tmp_path / "data"
    reference_dir = data_dir / "reference"
    reference_dir.mkdir(parents=True)
    (reference_dir / "hoopr_snapshot.json").write_text(
        json.dumps({
            "repository": "sportsdataverse/hoopR-nba-data",
            "commit": "different-commit",
            "seasons": [2020, 2021, 2022, 2023, 2024, 2025, 2026],
        }),
        encoding="utf-8",
    )

    with pytest.raises(
        build.ValidationError,
        match="hoopR snapshot reference differs",
    ):
        build._raw_provenance(data_dir)


def test_run_rejects_checkout_franchise_reference_divergence(
    monkeypatch,
    tmp_path,
):
    """Would fail if normalization and publication could use different aliases."""
    data_dir = tmp_path / "data"
    publication_fixture(monkeypatch, data_dir)
    reference = data_dir / "reference/franchises.csv"
    reference.write_text(
        reference.read_text(encoding="utf-8").replace(
            "Atlanta Hawks",
            "Atlanta Hawks Edited",
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        build.ValidationError,
        match="franchise reference differs from the packaged runtime reference",
    ):
        build.run(data_dir)


def test_run_preserves_prior_publication_when_validation_fails(
    monkeypatch, tmp_path
):
    output_dir = tmp_path / "data/parquet"
    output_dir.mkdir(parents=True)
    sentinels = {
        name: f"sentinel-{name}".encode()
        for name in (
            "team_games.parquet",
            "games.parquet",
            "franchises.parquet",
            "venues.parquet",
            "build_manifest.json",
        )
    }
    for name, content in sentinels.items():
        (output_dir / name).write_bytes(content)

    def fail_validation(raw_dir):
        raise build.ValidationError("seeded publication failure")

    monkeypatch.setattr(build, "assemble", fail_validation)

    with pytest.raises(
        build.ValidationError, match="seeded publication failure"
    ):
        build.run(tmp_path / "data")

    assert {
        path.name: path.read_bytes() for path in output_dir.iterdir()
    } == sentinels


def test_run_publishes_exact_tables_and_deterministic_manifest(
    monkeypatch, tmp_path
):
    data_dir = tmp_path / "data"
    team_games, games = publication_fixture(monkeypatch, data_dir)

    build.run(data_dir)

    output_dir = data_dir / "parquet"
    assert {
        path.name
        for path in output_dir.iterdir()
        if not path.name.startswith(".")
    } == {
        "team_games.parquet",
        "games.parquet",
        "franchises.parquet",
        "venues.parquet",
        "build_manifest.json",
    }
    assert pd.read_parquet(output_dir / "team_games.parquet").columns.tolist() == (
        team_games.columns.tolist()
    )
    assert pd.read_parquet(output_dir / "games.parquet").columns.tolist() == (
        games.columns.tolist()
    )
    franchises_frame = pd.read_parquet(output_dir / "franchises.parquet")
    assert franchises_frame.columns.tolist() == [
        "alias",
        "franchise_id",
        "first_season",
        "last_season",
    ]
    assert franchises_frame.dtypes.astype(str).to_dict() == {
        "alias": "string",
        "franchise_id": "string",
        "first_season": "Int64",
        "last_season": "Int64",
    }
    venues_frame = pd.read_parquet(output_dir / "venues.parquet")
    assert venues_frame.columns.tolist() == CAPACITY_COLUMNS
    assert venues_frame.dtypes.astype(str).to_dict() == {
        "venue_id": "string",
        "franchise_id": "string",
        "first_season": "int64",
        "last_season": "int64",
        "capacity": "int64",
        "capacity_source_url": "string",
        "capacity_note": "string",
    }
    manifest = json.loads(
        (output_dir / "build_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["schema_version"] == 1
    assert manifest["tables"]["games"] == {
        "rows": len(games),
        "min_season": 2019,
        "max_season": 2023,
        "sha256": hashlib.sha256(
            (output_dir / "games.parquet").read_bytes()
        ).hexdigest(),
    }
    assert manifest["tables"]["team_games"] == {
        "rows": len(team_games),
        "min_season": 2019,
        "max_season": 2023,
        "sha256": hashlib.sha256(
            (output_dir / "team_games.parquet").read_bytes()
        ).hexdigest(),
    }
    assert manifest["validation"]["totals"] == {
        "games": 2,
        "team_games": 4,
    }
    assert manifest["validation"]["selected_attendance_exemptions"] == {
        "2023": 0
    }
    assert "/home/" not in json.dumps(manifest)
    assert "fetched_at" not in json.dumps(manifest)


def test_modern_publish_preserves_both_independent_pairs(
    tmp_path, monkeypatch
):
    """Would fail if the modern directory swap discarded either product pair."""
    data_dir = tmp_path / "data"
    publication_fixture(monkeypatch, data_dir)
    output_dir = data_dir / "parquet"
    output_dir.mkdir()
    products = {
        **{
            name: f"historical-{name}".encode()
            for name in build.PRESERVED_HISTORICAL_FILES
        },
        **{
            name: f"playoff-{name}".encode()
            for name in build.PRESERVED_PLAYOFF_FILES
        },
    }
    for name, payload in products.items():
        (output_dir / name).write_bytes(payload)

    build.run(data_dir)

    assert {
        name: (output_dir / name).read_bytes()
        for name in products
    } == products


@pytest.mark.parametrize(
    ("label", "products"),
    [
        ("historical", build.PRESERVED_HISTORICAL_FILES),
        ("playoff", build.PRESERVED_PLAYOFF_FILES),
    ],
)
def test_modern_publish_rejects_incomplete_independent_pair(
    tmp_path, label, products
):
    """Would fail if a modern rebuild replaced an orphan independent product."""
    output = tmp_path / "parquet"
    stage = tmp_path / "stage"
    output.mkdir()
    stage.mkdir()
    (output / products[0]).write_bytes(b"orphan")

    with pytest.raises(
        build.ValidationError,
        match=rf"{label} publication pair is incomplete",
    ):
        build._copy_independent_publications(output, stage)


@pytest.mark.parametrize(
    ("label", "products"),
    [
        ("historical", build.PRESERVED_HISTORICAL_FILES),
        ("playoff", build.PRESERVED_PLAYOFF_FILES),
    ],
)
@pytest.mark.parametrize("kind", ["directory", "symlink"])
def test_modern_publish_rejects_non_regular_independent_product(
    tmp_path, label, products, kind
):
    """Would fail if a modern swap silently dropped a non-regular product."""
    output = tmp_path / "parquet"
    stage = tmp_path / "stage"
    output.mkdir()
    stage.mkdir()
    for name in products:
        (output / name).write_bytes(label.encode())
    target = output / products[0]
    target.unlink()
    if kind == "directory":
        target.mkdir()
    else:
        replacement = tmp_path / f"{label}-product"
        replacement.write_bytes(label.encode())
        target.symlink_to(replacement)

    with pytest.raises(
        build.ValidationError,
        match=rf"non-regular {label} publication product",
    ):
        build._copy_independent_publications(output, stage)


def test_modern_publish_excludes_unknown_prior_output(monkeypatch, tmp_path):
    """Would fail if an unknown prior output entered the modern publication."""
    data_dir = tmp_path / "data"
    publication_fixture(monkeypatch, data_dir)
    output = data_dir / "parquet"
    output.mkdir()
    (output / "unknown-product.bin").write_bytes(b"not independently owned")

    build.run(data_dir)

    assert not (output / "unknown-product.bin").exists()


def test_modern_manifest_omits_historical_only_references(monkeypatch, tmp_path):
    """Would fail if historical reference changes mutated modern attestation."""
    data_dir = tmp_path / "data"
    publication_fixture(monkeypatch, data_dir)
    (data_dir / "reference/historical_season_counts.csv").write_text(
        "historical only\n", encoding="utf-8"
    )

    build.run(data_dir)

    manifest = json.loads(
        (data_dir / "parquet/build_manifest.json").read_text(encoding="utf-8")
    )
    assert "reference/historical_season_counts.csv" not in manifest["references"]


def test_raw_provenance_excludes_cache_metadata_sidecars(tmp_path):
    data_dir = tmp_path / "data"
    raw_dir = data_dir / "raw/stats_nba"
    raw_dir.mkdir(parents=True)
    payload = raw_dir / "leaguegamefinder_2026.json"
    payload.write_bytes(b'{"resultSets":[]}')
    digest = hashlib.sha256(payload.read_bytes()).hexdigest()
    payload.with_name(payload.name + ".meta.json").write_text(
        json.dumps({
            "endpoint": "LeagueGameFinder",
            "season": 2026,
            "season_label": "2025-26",
            "sha256": digest,
            "fetched_at": "not-manifest-provenance",
        }),
        encoding="utf-8",
    )

    provenance = build._raw_provenance(data_dir)

    assert provenance["stats_nba"]["seasons"] == [{
        "path": "raw/stats_nba/leaguegamefinder_2026.json",
        "endpoint": "LeagueGameFinder",
        "season": 2026,
        "season_label": "2025-26",
        "sha256": digest,
    }]


def test_raw_provenance_does_not_own_playoff_cache(tmp_path):
    """Would fail if the modern builder traversed playoff-owned raw data."""
    data_dir = tmp_path / "data"
    before = build._raw_provenance(data_dir)
    playoff_cache = data_dir / "raw/playoff_bref"
    playoff_cache.mkdir(parents=True)
    (playoff_cache / "unrelated.file").write_bytes(b"deliberately invalid")

    after = build._raw_provenance(data_dir)

    assert after == before


@pytest.mark.parametrize(
    ("relative_body", "orphan"),
    [
        pytest.param(
            "stats_nba/leaguegamefinder_2026.json",
            "body",
            id="stats-body",
        ),
        pytest.param(
            "stats_nba/leaguegamefinder_2026.json",
            "sidecar",
            id="stats-sidecar",
        ),
        pytest.param(
            "hoopr/nba_schedule_2026.parquet",
            "body",
            id="hoopr-body",
        ),
        pytest.param(
            "hoopr/nba_schedule_2026.parquet",
            "sidecar",
            id="hoopr-sidecar",
        ),
        pytest.param(
            "bref/NBA_2026_games-april.html",
            "body",
            id="bref-body",
        ),
        pytest.param(
            "bref/NBA_2026_games-april.html",
            "sidecar",
            id="bref-sidecar",
        ),
    ],
)
def test_raw_provenance_rejects_incomplete_cache_pairs(
    tmp_path,
    relative_body,
    orphan,
):
    """Would fail if a body or sidecar could be attested without its pair."""
    data_dir = tmp_path / "data"
    body = data_dir / "raw" / relative_body
    body.parent.mkdir(parents=True)
    sidecar = body.with_name(body.name + ".meta.json")
    if orphan == "body":
        body.write_bytes(b"unverified")
    else:
        sidecar.write_text(
            json.dumps({"sha256": "0" * 64}),
            encoding="utf-8",
        )

    with pytest.raises(
        build.ValidationError,
        match="incomplete raw cache pair",
    ):
        build._raw_provenance(data_dir)


def test_raw_provenance_requires_sidecar_sha256(tmp_path):
    """Would fail if a sidecar-less checksum were freshly invented."""
    data_dir = tmp_path / "data"
    body = data_dir / "raw/stats_nba/leaguegamefinder_2026.json"
    body.parent.mkdir(parents=True)
    body.write_bytes(b'{"resultSets":[]}')
    body.with_name(body.name + ".meta.json").write_text(
        json.dumps({
            "endpoint": "LeagueGameFinder",
            "season": 2026,
            "season_label": "2025-26",
        }),
        encoding="utf-8",
    )

    with pytest.raises(
        build.ValidationError,
        match="missing SHA-256",
    ):
        build._raw_provenance(data_dir)


def test_raw_provenance_records_verified_hoopr_pin_per_season(tmp_path):
    """Would fail if the manifest attributed season bodies only to a global pin."""
    data_dir = tmp_path / "data"
    raw_dir = data_dir / "raw/hoopr"
    raw_dir.mkdir(parents=True)
    payload = raw_dir / "nba_schedule_2024.parquet"
    pd.DataFrame({"fixture": [1]}).to_parquet(payload, index=False)
    digest = hashlib.sha256(payload.read_bytes()).hexdigest()
    commit = "50fe7e429c6b5bcbe62e7a56d19afe5a80e6e143"
    payload.with_name(payload.name + ".meta.json").write_text(
        json.dumps({
            "provider": "hoopr",
            "season": 2024,
            "upstream_commit": commit,
            "sha256": digest,
        }),
        encoding="utf-8",
    )

    provenance = build._raw_provenance(data_dir)

    assert provenance["hoopr"]["seasons"] == [{
        "path": "raw/hoopr/nba_schedule_2024.parquet",
        "provider": "hoopr",
        "season": 2024,
        "upstream_commit": commit,
        "sha256": digest,
    }]


def write_historical_team_corrections(data_dir, game_urls):
    reference_dir = data_dir / "reference"
    reference_dir.mkdir(parents=True, exist_ok=True)
    rows = [{
        "game_id": game_id,
        "franchise_id": "SAC",
        "field": "fgm",
        "corrected_value": "40",
        "source_url": url,
        "note": "Exact historical team total.",
        "attendance_coverage_exemption_reason": pd.NA,
    } for game_id, url in game_urls]
    pd.DataFrame(rows, columns=CORRECTION_COLUMNS).to_csv(
        reference_dir / "game_corrections.csv",
        index=False,
    )


def write_historical_boxscore(
    data_dir,
    filename,
    game_id,
    *,
    metadata_overrides=None,
    missing_metadata=(),
):
    raw_dir = data_dir / "raw/reconciliation/bref"
    raw_dir.mkdir(parents=True, exist_ok=True)
    payload = raw_dir / filename
    payload.write_bytes(b"<html>historical box score</html>")
    digest = hashlib.sha256(payload.read_bytes()).hexdigest()
    url = (
        "https://www.basketball-reference.com/boxscores/"
        f"{filename}"
    )
    metadata = {
        "provider": "basketball_reference",
        "resource": "historical_boxscore",
        "game_id": game_id,
        "url": url,
        "sha256": digest,
        "fetched_at": "not-manifest-provenance",
    }
    metadata.update(metadata_overrides or {})
    for field in missing_metadata:
        metadata.pop(field)
    payload.with_name(payload.name + ".meta.json").write_text(
        json.dumps(metadata),
        encoding="utf-8",
    )
    return payload, url, digest


def test_raw_provenance_records_historical_reconciliation_boxscores(
    tmp_path,
):
    data_dir = tmp_path / "data"
    payload, url, digest = write_historical_boxscore(
        data_dir,
        "198311080CHI.html",
        "0028300064",
    )
    write_historical_team_corrections(
        data_dir,
        [("0028300064", url)],
    )

    provenance = build._raw_provenance(data_dir)

    assert provenance["basketball_reference"][
        "historical_boxscores"
    ] == [{
        "path": "raw/reconciliation/bref/198311080CHI.html",
        "game_id": "0028300064",
        "url": url,
        "sha256": digest,
    }]


def test_historical_reconciliation_requires_sidecar(tmp_path):
    data_dir = tmp_path / "data"
    payload, url, _ = write_historical_boxscore(
        data_dir,
        "198311080CHI.html",
        "0028300064",
    )
    write_historical_team_corrections(
        data_dir,
        [("0028300064", url)],
    )
    payload.with_name(payload.name + ".meta.json").unlink()

    with pytest.raises(build.ValidationError):
        build._raw_provenance(data_dir)


@pytest.mark.parametrize("ledger_exists", [False, True])
def test_historical_reconciliation_rejects_sidecar_without_body(
    tmp_path,
    ledger_exists,
):
    data_dir = tmp_path / "data"
    raw_dir = data_dir / "raw/reconciliation/bref"
    raw_dir.mkdir(parents=True)
    sidecar = raw_dir / "198311080CHI.html.meta.json"
    sidecar.write_text(
        json.dumps({
            "provider": "basketball_reference",
            "resource": "historical_boxscore",
            "game_id": "0028300064",
            "url": (
                "https://www.basketball-reference.com/boxscores/"
                "198311080CHI.html"
            ),
            "sha256": "0" * 64,
        }),
        encoding="utf-8",
    )
    if ledger_exists:
        write_historical_team_corrections(data_dir, [])

    with pytest.raises(build.ValidationError):
        build._raw_provenance(data_dir)


@pytest.mark.parametrize(
    ("field", "bad_value", "missing"),
    [
        ("sha256", None, True),
        ("sha256", "0" * 64, False),
        ("provider", None, True),
        ("provider", "basketball-reference", False),
        ("resource", None, True),
        ("resource", "boxscore", False),
        ("game_id", None, True),
        ("game_id", "0028309999", False),
        ("url", None, True),
        ("url", 198311080, False),
    ],
    ids=[
        "missing-sha",
        "wrong-sha",
        "missing-provider",
        "wrong-provider",
        "missing-resource",
        "wrong-resource",
        "missing-game-id",
        "wrong-game-id",
        "missing-url",
        "wrong-url",
    ],
)
def test_historical_reconciliation_rejects_invalid_sidecar_binding(
    tmp_path,
    field,
    bad_value,
    missing,
):
    data_dir = tmp_path / "data"
    payload, url, _ = write_historical_boxscore(
        data_dir,
        "198311080CHI.html",
        "0028300064",
        metadata_overrides=(
            {} if missing else {field: bad_value}
        ),
        missing_metadata=((field,) if missing else ()),
    )
    write_historical_team_corrections(
        data_dir,
        [("0028300064", url)],
    )

    with pytest.raises(build.ValidationError):
        build._raw_provenance(data_dir)


def test_historical_reconciliation_url_must_match_body_filename(tmp_path):
    data_dir = tmp_path / "data"
    wrong_url = (
        "https://www.basketball-reference.com/boxscores/"
        "198311100HOU.html"
    )
    write_historical_boxscore(
        data_dir,
        "198311080CHI.html",
        "0028300064",
        metadata_overrides={"url": wrong_url},
    )
    write_historical_team_corrections(
        data_dir,
        [("0028300064", wrong_url)],
    )

    with pytest.raises(build.ValidationError):
        build._raw_provenance(data_dir)


def test_historical_reconciliation_rejects_duplicate_game_ids(tmp_path):
    data_dir = tmp_path / "data"
    _, first_url, _ = write_historical_boxscore(
        data_dir,
        "198311080CHI.html",
        "0028300064",
    )
    _, second_url, _ = write_historical_boxscore(
        data_dir,
        "198311100HOU.html",
        "0028300064",
    )
    write_historical_team_corrections(
        data_dir,
        [
            ("0028300064", first_url),
            ("0028300074", second_url),
        ],
    )

    with pytest.raises(build.ValidationError):
        build._raw_provenance(data_dir)


def test_historical_reconciliation_rejects_cross_labeled_games(tmp_path):
    data_dir = tmp_path / "data"
    _, first_url, _ = write_historical_boxscore(
        data_dir,
        "198311080CHI.html",
        "0028300074",
    )
    _, second_url, _ = write_historical_boxscore(
        data_dir,
        "198311100HOU.html",
        "0028300064",
    )
    write_historical_team_corrections(
        data_dir,
        [
            ("0028300064", first_url),
            ("0028300074", second_url),
        ],
    )

    with pytest.raises(build.ValidationError):
        build._raw_provenance(data_dir)


def test_historical_reconciliation_rejects_missing_ledger_page(tmp_path):
    data_dir = tmp_path / "data"
    _, first_url, _ = write_historical_boxscore(
        data_dir,
        "198311080CHI.html",
        "0028300064",
    )
    second_url = (
        "https://www.basketball-reference.com/boxscores/"
        "198311100HOU.html"
    )
    write_historical_team_corrections(
        data_dir,
        [
            ("0028300064", first_url),
            ("0028300074", second_url),
        ],
    )

    with pytest.raises(build.ValidationError):
        build._raw_provenance(data_dir)


def test_historical_reconciliation_rejects_extra_unreferenced_page(
    tmp_path,
):
    data_dir = tmp_path / "data"
    _, first_url, _ = write_historical_boxscore(
        data_dir,
        "198311080CHI.html",
        "0028300064",
    )
    write_historical_boxscore(
        data_dir,
        "198311100HOU.html",
        "0028300074",
    )
    write_historical_team_corrections(
        data_dir,
        [("0028300064", first_url)],
    )

    with pytest.raises(build.ValidationError):
        build._raw_provenance(data_dir)


def test_run_restores_prior_publication_when_directory_swap_fails(
    monkeypatch, tmp_path
):
    data_dir = tmp_path / "data"
    publication_fixture(monkeypatch, data_dir)
    output_dir = data_dir / "parquet"
    output_dir.mkdir()
    sentinels = {
        name: f"sentinel-{name}".encode()
        for name in build.PUBLISHED_FILES
    }
    for name, content in sentinels.items():
        (output_dir / name).write_bytes(content)
    real_replace = build.os.replace

    def fail_stage_swap(source, destination):
        if (
            Path(source).name.startswith(".parquet-stage-")
            and Path(destination) == output_dir
        ):
            raise OSError("seeded stage swap failure")
        real_replace(source, destination)

    monkeypatch.setattr(build.os, "replace", fail_stage_swap)

    with pytest.raises(OSError, match="seeded stage swap failure"):
        build.run(data_dir)

    assert {
        path.name: path.read_bytes() for path in output_dir.iterdir()
    } == sentinels
    assert not any(
        path.name.startswith((".parquet-stage-", ".parquet-rollback-"))
        for path in data_dir.iterdir()
    )


def test_run_leaves_prior_publication_when_first_rename_fails(
    monkeypatch, tmp_path
):
    data_dir = tmp_path / "data"
    publication_fixture(monkeypatch, data_dir)
    output_dir = data_dir / "parquet"
    output_dir.mkdir()
    sentinels = {
        name: f"sentinel-{name}".encode()
        for name in build.PUBLISHED_FILES
    }
    for name, content in sentinels.items():
        (output_dir / name).write_bytes(content)
    real_replace = build.os.replace

    def fail_prior_move(source, destination):
        if Path(source) == output_dir:
            raise OSError("seeded prior move failure")
        real_replace(source, destination)

    monkeypatch.setattr(build.os, "replace", fail_prior_move)

    with pytest.raises(OSError, match="seeded prior move failure"):
        build.run(data_dir)

    assert {
        path.name: path.read_bytes() for path in output_dir.iterdir()
    } == sentinels
    assert not any(
        path.name.startswith((".parquet-stage-", ".parquet-rollback-"))
        for path in data_dir.iterdir()
    )


def test_run_reports_success_when_post_publication_cleanup_fails(
    monkeypatch, tmp_path
):
    data_dir = tmp_path / "data"
    publication_fixture(monkeypatch, data_dir)
    output_dir = data_dir / "parquet"
    output_dir.mkdir()
    sentinels = {
        name: f"sentinel-{name}".encode()
        for name in build.PUBLISHED_FILES
    }
    for name, content in sentinels.items():
        (output_dir / name).write_bytes(content)
    real_rmtree = build.shutil.rmtree

    def fail_rollback_cleanup(path):
        if Path(path).name.startswith(".parquet-rollback-"):
            raise OSError("seeded rollback cleanup failure")
        real_rmtree(path)

    monkeypatch.setattr(build.shutil, "rmtree", fail_rollback_cleanup)

    build.run(data_dir)

    assert pd.read_parquet(output_dir / "games.parquet").shape[0] == 2
    rollback_dirs = [
        path
        for path in data_dir.iterdir()
        if path.name.startswith(".parquet-rollback-")
    ]
    assert len(rollback_dirs) == 1
    assert {
        path.name: path.read_bytes()
        for path in rollback_dirs[0].iterdir()
    } == sentinels
