"""Validation helpers for the historical publication pipeline."""

from pathlib import Path

import pandas as pd

from nba_data import historical_bref


COUNT_COLUMNS = [
    "season", "league", "expected_games", "source_url", "source_note"
]
CORRECTION_COLUMNS = ["game_id", "site_status", "source_url", "note"]


class HistoricalValidationError(ValueError):
    """Raised when a historical reference artifact violates its contract."""


def load_expected_counts(path: Path) -> dict[int, int]:
    """Load independently curated regular-season game totals."""
    frame = pd.read_csv(path, dtype="string")
    if frame.columns.tolist() != COUNT_COLUMNS:
        raise HistoricalValidationError(
            f"count columns must be {COUNT_COLUMNS}"
        )
    errors = []
    if frame["season"].duplicated(keep=False).any():
        errors.append("duplicate historical count season")
    seasons = pd.to_numeric(frame["season"], errors="raise").astype(int)
    games = pd.to_numeric(
        frame["expected_games"], errors="raise"
    ).astype(int)
    expected_seasons = list(historical_bref.HISTORICAL_SEASONS)
    if sorted(seasons.tolist()) != expected_seasons:
        errors.append("historical counts must cover exactly 1947..1983")
    expected_leagues = [
        historical_bref.league_for_season(int(season))
        for season in seasons
    ]
    if frame["league"].tolist() != expected_leagues:
        errors.append("historical count league/season mismatch")
    if games.le(0).any():
        errors.append("historical expected games must be positive")
    if (
        frame["source_url"].isna().any()
        or not frame["source_url"].str.startswith("https://").all()
        or frame["source_note"].isna().any()
        or frame["source_note"].str.strip().eq("").any()
    ):
        errors.append("historical counts require source URL and note")
    if errors:
        raise HistoricalValidationError("; ".join(errors))
    return dict(zip(seasons.tolist(), games.tolist(), strict=True))


def load_site_corrections(path: Path) -> pd.DataFrame:
    """Load documented site-status overrides, including an empty ledger."""
    frame = pd.read_csv(path, dtype="string")
    if frame.columns.tolist() != CORRECTION_COLUMNS:
        raise HistoricalValidationError(
            f"correction columns must be {CORRECTION_COLUMNS}"
        )
    errors = []
    if frame["game_id"].duplicated(keep=False).any():
        errors.append("duplicate historical site correction game_id")
    if not set(frame["site_status"].dropna()) <= {"home", "neutral"}:
        errors.append("site correction must be home or neutral")
    for column in ("game_id", "source_url", "note"):
        if frame[column].isna().any() or frame[column].str.strip().eq("").any():
            errors.append(f"site correction requires {column}")
    if len(frame) and not frame["source_url"].str.startswith("https://").all():
        errors.append("site correction source_url must use https")
    if errors:
        raise HistoricalValidationError("; ".join(errors))
    return frame


def apply_site_corrections(
    games: pd.DataFrame,
    corrections: pd.DataFrame,
) -> pd.DataFrame:
    """Apply documented site-status overrides without changing game values."""
    output = games.copy()
    if corrections.empty:
        return output
    counts = output["game_id"].value_counts()
    bad_targets = [
        game_id
        for game_id in corrections["game_id"]
        if int(counts.get(game_id, 0)) != 1
    ]
    if bad_targets:
        raise HistoricalValidationError(
            "site correction must resolve exactly one game: "
            f"{sorted(bad_targets)}"
        )
    selected = corrections.set_index("game_id")["site_status"]
    mask = output["game_id"].isin(selected.index)
    output.loc[mask, "site_status"] = output.loc[
        mask, "game_id"
    ].map(selected)
    return output


def run(data_dir: Path) -> None:
    raise RuntimeError(
        "historical publication is unavailable until its gates are installed"
    )
