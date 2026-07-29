"""Normalize the shared archived/live NBA Stats team-game schema."""

import pandas as pd

from nba_data import franchises


STATS = (
    "PTS", "FGM", "FGA", "FG3M", "FG3A", "FTM", "FTA", "OREB", "DREB",
    "REB", "AST", "STL", "BLK", "TOV", "PF",
)


def normalize_team_rows(raw: pd.DataFrame, source: str) -> pd.DataFrame:
    season = (pd.to_numeric(raw["SEASON_ID"]) % 20_000) + 1
    output = pd.DataFrame({
        "game_id": raw["GAME_ID"].astype(str).str.zfill(10),
        "season": season.astype("int64"),
        "source": source,
        "franchise_id": [
            franchises.resolve(name, int(year))
            for name, year in zip(raw["TEAM_NAME"], season)
        ],
        "is_home": raw["MATCHUP"].str.contains(r"\svs\.\s", regex=True),
        "game_date": pd.to_datetime(raw["GAME_DATE"]).dt.date,
        "wl": raw["WL"].replace({"W": 1, "L": 0}).astype("int64"),
    })
    for column in STATS:
        output[column.lower()] = pd.to_numeric(
            raw[column], errors="coerce"
        ).astype("Int64")
    counts = output.groupby("game_id").size()
    if not counts.eq(2).all():
        games = counts[counts.ne(2)].index[:5].tolist()
        raise ValueError(f"games without two team rows: {games}")
    opponents = output[["game_id", "franchise_id"]].rename(
        columns={"franchise_id": "opponent_franchise_id"}
    )
    output = output.merge(opponents, on="game_id", how="left")
    output = output[
        output["franchise_id"] != output["opponent_franchise_id"]
    ].reset_index(drop=True)
    if len(output) != len(raw):
        raise ValueError("opponent pairing did not preserve row count")
    return output
