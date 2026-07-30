from pathlib import Path

import pandas as pd
import pytest

from nba_data import historical_build


COUNT_COLUMNS = [
    "season", "league", "expected_games", "source_url", "source_note"
]
CORRECTION_COLUMNS = ["game_id", "site_status", "source_url", "note"]


def write_counts(tmp_path, rows):
    path = tmp_path / "counts.csv"
    pd.DataFrame(rows, columns=COUNT_COLUMNS).to_csv(path, index=False)
    return path


def valid_count_rows():
    return [
        {
            "season": season,
            "league": "BAA" if season <= 1949 else "NBA",
            "expected_games": 1,
            "source_url": f"https://example.org/{season}",
            "source_note": "Independent summary total.",
        }
        for season in range(1947, 1984)
    ]


def write_corrections(tmp_path, rows):
    path = tmp_path / "corrections.csv"
    pd.DataFrame(rows, columns=CORRECTION_COLUMNS).to_csv(path, index=False)
    return path


def test_expected_counts_cover_exact_historical_seasons():
    counts = historical_build.load_expected_counts(
        Path("data/reference/historical_season_counts.csv")
    )
    assert tuple(counts) == tuple(range(1947, 1984))
    assert counts[1947] == 331
    assert counts[1960] == 300
    assert counts[1983] == 943


def test_site_correction_changes_only_status():
    games = pd.DataFrame([
        {
            "game_id": "bref-before",
            "site_status": "home",
            "home_pts": 99,
            "away_pts": 88,
            "attendance": 1000,
        },
        {
            "game_id": "bref-194611010TRH",
            "site_status": "unknown",
            "home_pts": 66,
            "away_pts": 68,
            "attendance": 4329,
        },
        {
            "game_id": "bref-after",
            "site_status": "neutral",
            "home_pts": 77,
            "away_pts": 76,
            "attendance": pd.NA,
        },
    ])
    corrections = pd.DataFrame([{
        "game_id": "bref-194611010TRH",
        "site_status": "neutral",
        "source_url": "https://example.org/documented-neutral-game",
        "note": "Contemporary source identifies a neutral venue.",
    }])

    result = historical_build.apply_site_corrections(games, corrections)

    expected = games.copy()
    expected.loc[1, "site_status"] = "neutral"
    pd.testing.assert_frame_equal(result, expected)


def test_counts_reject_missing_ending_season(tmp_path):
    rows = valid_count_rows()
    rows.pop()

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="historical counts must cover exactly 1947..1983",
    ):
        historical_build.load_expected_counts(write_counts(tmp_path, rows))


def test_counts_reject_duplicate_season(tmp_path):
    rows = valid_count_rows()
    rows[-1]["season"] = 1982

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="duplicate historical count season",
    ):
        historical_build.load_expected_counts(write_counts(tmp_path, rows))


def test_counts_reject_wrong_league(tmp_path):
    rows = valid_count_rows()
    rows[0]["league"] = "NBA"

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="historical count league/season mismatch",
    ):
        historical_build.load_expected_counts(write_counts(tmp_path, rows))


def test_counts_reject_blank_provenance(tmp_path):
    rows = valid_count_rows()
    rows[0]["source_note"] = " "

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="historical counts require source URL and note",
    ):
        historical_build.load_expected_counts(write_counts(tmp_path, rows))


def test_corrections_reject_duplicate_game_id(tmp_path):
    rows = [
        {
            "game_id": "bref-one",
            "site_status": "home",
            "source_url": "https://example.org/one",
            "note": "Documented home venue.",
        },
        {
            "game_id": "bref-one",
            "site_status": "neutral",
            "source_url": "https://example.org/two",
            "note": "Documented neutral venue.",
        },
    ]

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="duplicate historical site correction game_id",
    ):
        historical_build.load_site_corrections(write_corrections(tmp_path, rows))


def test_corrections_reject_unknown_as_selected_value(tmp_path):
    rows = [{
        "game_id": "bref-one",
        "site_status": "unknown",
        "source_url": "https://example.org/one",
        "note": "Not an allowed selected value.",
    }]

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="site correction must be home or neutral",
    ):
        historical_build.load_site_corrections(write_corrections(tmp_path, rows))


@pytest.mark.parametrize("status", [None, " "])
def test_corrections_reject_missing_or_blank_selected_status(tmp_path, status):
    rows = [{
        "game_id": "bref-one",
        "site_status": status,
        "source_url": "https://example.org/one",
        "note": "A correction needs a selected status.",
    }]

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="site correction must be home or neutral",
    ):
        historical_build.load_site_corrections(write_corrections(tmp_path, rows))


@pytest.mark.parametrize("column", ["source_url", "note"])
def test_corrections_reject_blank_source_or_note(tmp_path, column):
    rows = [{
        "game_id": "bref-one",
        "site_status": "home",
        "source_url": "https://example.org/one",
        "note": "Documented home venue.",
    }]
    rows[0][column] = " "

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match=f"site correction requires {column}",
    ):
        historical_build.load_site_corrections(write_corrections(tmp_path, rows))


def test_apply_corrections_rejects_orphan_game_id():
    games = pd.DataFrame([{"game_id": "bref-one", "site_status": "unknown"}])
    corrections = pd.DataFrame([{
        "game_id": "bref-missing",
        "site_status": "neutral",
        "source_url": "https://example.org/missing",
        "note": "Documented neutral venue.",
    }])

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match=r"site correction must resolve exactly one game: \['bref-missing'\]",
    ):
        historical_build.apply_site_corrections(games, corrections)


def test_apply_corrections_rejects_duplicate_game_id_target():
    games = pd.DataFrame([
        {"game_id": "bref-duplicate", "site_status": "unknown"},
        {"game_id": "bref-duplicate", "site_status": "unknown"},
    ])
    corrections = pd.DataFrame([{
        "game_id": "bref-duplicate",
        "site_status": "neutral",
        "source_url": "https://example.org/duplicate",
        "note": "Documented neutral venue.",
    }])

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match=r"site correction must resolve exactly one game: \['bref-duplicate'\]",
    ):
        historical_build.apply_site_corrections(games, corrections)


def test_empty_correction_ledger_loads(tmp_path):
    path = tmp_path / "corrections.csv"
    path.write_text(",".join(CORRECTION_COLUMNS) + "\n")

    corrections = historical_build.load_site_corrections(path)

    assert corrections.empty
    assert corrections.columns.tolist() == CORRECTION_COLUMNS


def source_game(
    *,
    game_id="bref-194611010TRH",
    season=1947,
    league="BAA",
    away="New York Knicks",
    home="Toronto Huskies",
    away_pts=68,
    home_pts=66,
    overtime_marker="",
    overtime_periods=0,
    site_status="home",
):
    return {
        "game_id": game_id,
        "season": season,
        "league": league,
        "game_date": pd.Timestamp("1946-11-01").date(),
        "away_team_name": away,
        "home_team_name": home,
        "away_pts": away_pts,
        "home_pts": home_pts,
        "overtime_marker": overtime_marker,
        "overtime_periods": overtime_periods,
        "site_status": site_status,
        "venue_name": pd.NA,
        "attendance": pd.NA,
        "attendance_status": "missing",
        "boxscore_url": (
            "https://www.basketball-reference.com/boxscores/"
            "194611010TRH.html"
        ),
        "schedule_url": (
            "https://www.basketball-reference.com/leagues/"
            "BAA_1947_games-november.html"
        ),
        "game_remarks": "",
    }


def seed_historical_references(data_dir, counts):
    reference = data_dir / "reference"
    reference.mkdir(parents=True, exist_ok=True)
    lines = ["season,league,expected_games,source_url,source_note"]
    for season, games in sorted(counts.items()):
        league = historical_build.historical_bref.league_for_season(season)
        lines.append(
            f"{season},{league},{games},"
            "https://www.basketball-reference.com/leagues/"
            f"{league}_{season}.html,Curated test count."
        )
    (reference / "historical_season_counts.csv").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    (reference / "historical_site_corrections.csv").write_text(
        "game_id,site_status,source_url,note\n", encoding="utf-8"
    )


def assembled_game_frame():
    frame = pd.DataFrame([{
        **source_game(),
        "away_franchise_id": "NYK",
        "home_franchise_id": "TOR_1946",
        "home_margin": -2,
    }])
    frame = frame.loc[:, historical_build.HISTORICAL_COLUMNS]
    return frame.astype(historical_build.HISTORICAL_DTYPES)


def test_assemble_resolves_lineage_and_orients_margin(monkeypatch, tmp_path):
    row = pd.DataFrame([source_game()])
    monkeypatch.setattr(
        historical_build.historical_bref,
        "HISTORICAL_SEASONS",
        (1947,),
    )
    monkeypatch.setattr(
        historical_build.historical_bref,
        "load_season",
        lambda raw_dir, season: row,
    )
    seed_historical_references(tmp_path, {1947: 1})

    games, report = historical_build.assemble(tmp_path)

    assert games.loc[0, "away_franchise_id"] == "NYK"
    assert games.loc[0, "home_franchise_id"] == "TOR_1946"
    assert games.loc[0, "home_margin"] == -2
    assert games.columns.tolist() == historical_build.HISTORICAL_COLUMNS
    assert report["games_by_season"] == {1947: 1}


def test_validation_rejects_wrong_season_count():
    games = assembled_game_frame()

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="historical game counts",
    ):
        historical_build.validate(games, {1947: 2})


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("game_id", pd.NA, "null game_id"),
        ("away_pts", -1, "negative score"),
        ("home_margin", 99, "home_margin disagreement"),
        ("overtime_periods", -1, "negative overtime"),
        ("site_status", "road", "unsupported site_status"),
        ("attendance", -1, "negative attendance"),
    ],
)
def test_validation_rejects_invalid_value(column, value, message):
    games = assembled_game_frame()
    games.loc[0, column] = value

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match=message,
    ):
        historical_build.validate(games, {1947: 1})


def test_validation_rejects_duplicate_game_id():
    games = pd.concat([assembled_game_frame(), assembled_game_frame()])

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="duplicate game_id",
    ):
        historical_build.validate(games, {1947: 2})


def test_validation_rejects_league_boundary():
    games = assembled_game_frame()
    games.loc[0, "league"] = "NBA"

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="league/season disagreement",
    ):
        historical_build.validate(games, {1947: 1})


def test_validation_rejects_equal_source_teams():
    games = assembled_game_frame()
    games.loc[0, "home_team_name"] = "New York Knicks"

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="identical source teams",
    ):
        historical_build.validate(games, {1947: 1})


def test_validation_rejects_attendance_status_disagreement():
    games = assembled_game_frame()
    games.loc[0, "attendance"] = 1

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="attendance/status disagreement",
    ):
        historical_build.validate(games, {1947: 1})


def test_validation_rejects_overtime_marker_disagreement():
    games = assembled_game_frame()
    evidence = pd.DataFrame([{
        "game_id": games.loc[0, "game_id"],
        "overtime_marker": "2OT",
        "overtime_periods": 0,
        "game_remarks": "",
    }])

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="overtime marker disagreement",
    ):
        historical_build.validate(games, {1947: 1}, evidence=evidence)


def test_validation_accumulates_multiple_diagnostics():
    games = assembled_game_frame()
    games.loc[0, "away_pts"] = -1
    games.loc[0, "site_status"] = "road"

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="negative score.*unsupported site_status",
    ):
        historical_build.validate(games, {1947: 1})


def test_validation_reports_incomplete_schema_without_crashing():
    games = pd.DataFrame({"game_id": ["bref-incomplete"]})

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="publication schema",
    ):
        historical_build.validate(games, {1947: 1})


def test_validation_reports_invalid_dtype_without_crashing():
    games = assembled_game_frame()
    games["season"] = games["season"].astype("string")

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="publication schema",
    ):
        historical_build.validate(games, {1947: 1})


def test_validation_reports_duplicate_columns_without_crashing():
    games = assembled_game_frame()
    games = pd.concat([games, games[["season"]]], axis=1)

    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="publication schema",
    ):
        historical_build.validate(games, {1947: 1})
