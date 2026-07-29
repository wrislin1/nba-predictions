import datetime

import pandas as pd
import pytest

from nba_data import build


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
        match="non-neutral 2020..2022 venue missing capacity",
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
