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
