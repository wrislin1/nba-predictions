"""Load the verified archived NBA Stats CSVs."""

import pandas as pd

from nba_data.team_rows import normalize_team_rows


def load_team_games(path) -> pd.DataFrame:
    raw = pd.read_csv(path, index_col=0, dtype={"GAME_ID": str})
    return normalize_team_rows(raw, source="nba_stats")


def load_attendance(path) -> pd.DataFrame:
    raw = pd.read_csv(path, index_col=0, dtype={"GAME_ID": str})
    attendance = pd.to_numeric(raw["ATTENDANCE"], errors="coerce").mask(
        lambda values: values <= 0
    )
    return pd.DataFrame({
        "game_id": raw["GAME_ID"].str.zfill(10),
        "attendance": attendance.astype("Int64"),
    })
