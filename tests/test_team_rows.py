import pandas as pd

from nba_data.team_rows import normalize_team_rows


def test_normalizes_two_nba_stats_rows():
    raw = pd.DataFrame({
        "SEASON_ID": [22020, 22020],
        "TEAM_NAME": ["Brooklyn Nets", "Golden State Warriors"],
        "GAME_ID": ["0022000001", "0022000001"],
        "GAME_DATE": ["2020-12-22", "2020-12-22"],
        "MATCHUP": ["BKN vs. GSW", "GSW @ BKN"],
        "WL": ["W", "L"],
        **{name: [value, value - 1] for name, value in {
            "PTS": 125, "FGM": 45, "FGA": 90, "FG3M": 15, "FG3A": 40,
            "FTM": 20, "FTA": 25, "OREB": 10, "DREB": 30, "REB": 40,
            "AST": 25, "STL": 7, "BLK": 5, "TOV": 12, "PF": 18,
        }.items()},
    })
    result = normalize_team_rows(raw, source="nba_stats")
    assert len(result) == 2
    assert result["season"].unique().tolist() == [2021]
    assert result[result["is_home"]].iloc[0]["franchise_id"] == "BKN"
    assert result[~result["is_home"]].iloc[0]["opponent_franchise_id"] == "BKN"
    assert result["wl"].tolist() == [1, 0]


def test_normalized_rows_have_a_stable_canonical_order():
    raw = pd.DataFrame({
        "SEASON_ID": [22020, 22020, 22020, 22020],
        "TEAM_NAME": [
            "Golden State Warriors", "Brooklyn Nets", "Brooklyn Nets",
            "Golden State Warriors",
        ],
        "GAME_ID": ["0022000002", "0022000001", "0022000002", "0022000001"],
        "GAME_DATE": ["2020-12-23", "2020-12-22", "2020-12-23", "2020-12-22"],
        "MATCHUP": ["GSW @ BKN", "BKN vs. GSW", "BKN vs. GSW", "GSW @ BKN"],
        "WL": ["L", "W", "W", "L"],
        **{name: [1, 1, 1, 1] for name in (
            "PTS", "FGM", "FGA", "FG3M", "FG3A", "FTM", "FTA", "OREB", "DREB",
            "REB", "AST", "STL", "BLK", "TOV", "PF",
        )},
    })

    result = normalize_team_rows(raw, source="nba_stats")

    assert result[["season", "game_date", "game_id", "is_home"]].to_dict(
        "records"
    ) == [
        {"season": 2021, "game_date": pd.Timestamp("2020-12-22").date(),
         "game_id": "0022000001", "is_home": True},
        {"season": 2021, "game_date": pd.Timestamp("2020-12-22").date(),
         "game_id": "0022000001", "is_home": False},
        {"season": 2021, "game_date": pd.Timestamp("2020-12-23").date(),
         "game_id": "0022000002", "is_home": True},
        {"season": 2021, "game_date": pd.Timestamp("2020-12-23").date(),
         "game_id": "0022000002", "is_home": False},
    ]
