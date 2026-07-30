"""Validation helpers for the historical publication pipeline."""

import hashlib
from pathlib import Path

import pandas as pd

from nba_data import franchises, historical_bref


COUNT_COLUMNS = [
    "season", "league", "expected_games", "source_url", "source_note"
]
CORRECTION_COLUMNS = ["game_id", "site_status", "source_url", "note"]
HISTORICAL_COLUMNS = [
    "game_id",
    "season",
    "league",
    "game_date",
    "away_team_name",
    "home_team_name",
    "away_franchise_id",
    "home_franchise_id",
    "away_pts",
    "home_pts",
    "home_margin",
    "overtime_periods",
    "site_status",
    "venue_name",
    "attendance",
    "attendance_status",
    "boxscore_url",
    "schedule_url",
]
HISTORICAL_DTYPES = {
    "game_id": "string",
    "season": "int64",
    "league": "string",
    "away_team_name": "string",
    "home_team_name": "string",
    "away_franchise_id": "string",
    "home_franchise_id": "string",
    "away_pts": "int64",
    "home_pts": "int64",
    "home_margin": "int64",
    "overtime_periods": "int64",
    "site_status": "string",
    "venue_name": "string",
    "attendance": "Int64",
    "attendance_status": "string",
    "boxscore_url": "string",
    "schedule_url": "string",
}


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
    if (
        frame["site_status"].isna().any()
        or not set(frame["site_status"].dropna()) <= {"home", "neutral"}
    ):
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


def _enforce_schema(frame: pd.DataFrame) -> pd.DataFrame:
    """Publish the ordered, typed historical-game contract only."""
    output = frame.loc[:, HISTORICAL_COLUMNS].copy()
    for column, dtype in HISTORICAL_DTYPES.items():
        output[column] = output[column].astype(dtype)
    return output.sort_values(
        ["season", "game_date", "game_id"], kind="stable"
    ).reset_index(drop=True)


def _season_sha256(frame: pd.DataFrame) -> str:
    payload = frame.to_csv(
        index=False,
        lineterminator="\n",
        na_rep="",
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def assemble(data_dir: Path) -> tuple[pd.DataFrame, dict]:
    """Assemble cached schedules into the validated publication frame."""
    raw_dir = data_dir / "raw"
    frames = [
        historical_bref.load_season(raw_dir, season)
        for season in historical_bref.HISTORICAL_SEASONS
    ]
    source = pd.concat(frames, ignore_index=True)
    source["away_franchise_id"] = [
        franchises.resolve(name, int(season))
        for name, season in zip(
            source["away_team_name"], source["season"], strict=True
        )
    ]
    source["home_franchise_id"] = [
        franchises.resolve(name, int(season))
        for name, season in zip(
            source["home_team_name"], source["season"], strict=True
        )
    ]
    source = apply_site_corrections(
        source,
        load_site_corrections(
            data_dir / "reference/historical_site_corrections.csv"
        ),
    )
    source["home_margin"] = source["home_pts"] - source["away_pts"]
    evidence = source[[
        "game_id", "overtime_marker", "overtime_periods", "game_remarks"
    ]].copy()
    games = _enforce_schema(source)
    report = validate(
        games,
        load_expected_counts(
            data_dir / "reference/historical_season_counts.csv"
        ),
        evidence=evidence,
    )
    return games, report


def _has_any(mask: pd.Series) -> bool:
    return bool(mask.fillna(False).any())


def validate(
    games: pd.DataFrame,
    expected_counts: dict[int, int],
    *,
    evidence: pd.DataFrame | None = None,
) -> dict:
    """Validate all publication gates and return a deterministic audit report."""
    errors = []
    if games.columns.has_duplicates:
        raise HistoricalValidationError(
            "historical columns and dtypes differ from publication schema"
        )

    schema_ok = games.columns.tolist() == HISTORICAL_COLUMNS and all(
        column in games and str(games[column].dtype) == dtype
        for column, dtype in HISTORICAL_DTYPES.items()
    )
    frame = games.reindex(columns=HISTORICAL_COLUMNS).copy()
    for column in (
        "season", "away_pts", "home_pts", "home_margin",
        "overtime_periods", "attendance",
    ):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    games_by_season = (
        {
            int(season): int(count)
            for season, count in frame.groupby("season", sort=True).size().items()
        }
    )
    if games_by_season != expected_counts:
        errors.append(
            "historical game counts: "
            f"actual={games_by_season}, expected={expected_counts}"
        )

    if not schema_ok:
        errors.append("historical columns and dtypes differ from publication schema")

    actual_seasons = set(frame["season"].dropna().tolist())
    expected_seasons = set(expected_counts)
    if actual_seasons != expected_seasons:
        errors.append("historical season set differs from expected counts")

    expected_leagues = frame["season"].map(
        lambda season: pd.NA if pd.isna(season)
        else "BAA" if season <= 1949 else "NBA"
    )
    if _has_any(
        frame["league"].astype("string")
        != expected_leagues.astype("string")
    ):
        errors.append("league/season disagreement")

    if _has_any(frame["game_id"].isna()):
        errors.append("null game_id")
    if _has_any(frame["game_id"].duplicated(keep=False)):
        errors.append("duplicate game_id")

    required = [
        "game_date", "league", "away_team_name", "home_team_name",
        "away_franchise_id", "home_franchise_id", "away_pts", "home_pts",
    ]
    if any(_has_any(frame[column].isna()) for column in required):
        errors.append("null required game value")
    if _has_any(frame["away_team_name"] == frame["home_team_name"]):
        errors.append("identical source teams")
    if _has_any(frame["away_franchise_id"] == frame["home_franchise_id"]):
        errors.append("identical franchise IDs")

    if _has_any((frame["away_pts"] < 0) | (frame["home_pts"] < 0)):
        errors.append("negative score")
    if _has_any(frame["home_margin"] == 0):
        errors.append("tied final margin")
    if _has_any(
        frame["home_margin"] != (frame["home_pts"] - frame["away_pts"])
    ):
        errors.append("home_margin disagreement")
    if _has_any(frame["overtime_periods"] < 0):
        errors.append("negative overtime")

    if evidence is not None:
        for row in evidence.itertuples(index=False):
            try:
                parsed = historical_bref._overtime_periods(row.overtime_marker)
            except ValueError:
                parsed = None
            if parsed != row.overtime_periods:
                errors.append("overtime marker disagreement")
                break

    if _has_any(frame["attendance"] < 0):
        errors.append("negative attendance")
    valid_attendance_statuses = {
        "missing", "reported_zero", "reported_positive"
    }
    if not frame["attendance_status"].dropna().isin(
        valid_attendance_statuses
    ).all() or _has_any(frame["attendance_status"].isna()):
        errors.append("unsupported attendance_status")
    attendance_matches_status = (
        (frame["attendance_status"] == "missing") & frame["attendance"].isna()
    ) | (
        (frame["attendance_status"] == "reported_zero")
        & frame["attendance"].eq(0)
    ) | (
        (frame["attendance_status"] == "reported_positive")
        & frame["attendance"].gt(0)
    )
    if _has_any(~attendance_matches_status):
        errors.append("attendance/status disagreement")

    if not frame["site_status"].dropna().isin(
        {"home", "neutral", "unknown"}
    ).all() or _has_any(frame["site_status"].isna()):
        errors.append("unsupported site_status")
    boxscore_urls = frame["boxscore_url"].astype("string")
    present_boxscore_urls = boxscore_urls.notna()
    if _has_any(
        present_boxscore_urls
        & ~boxscore_urls.str.fullmatch(
            r"https://www\.basketball-reference\.com/boxscores/[^/]+\.html",
            na=False,
        )
    ):
        errors.append("invalid boxscore URL")
    if not frame["schedule_url"].astype("string").str.fullmatch(
        r"https://www\.basketball-reference\.com/leagues/[^/]+\.html",
        na=False,
    ).all():
        errors.append("invalid schedule URL")

    if expected_counts:
        expected_minimum = min(expected_counts)
        expected_maximum = max(expected_counts)
        if frame.empty or (
            frame["season"].min() != expected_minimum
            or frame["season"].max() != expected_maximum
        ):
            errors.append("historical season span differs from expected counts")

    if errors:
        raise HistoricalValidationError("; ".join(errors))

    ordered = games.sort_values(
        ["season", "game_date", "game_id"], kind="stable"
    )
    season_rows = list(ordered.groupby("season", sort=True))
    return {
        "totals": {"games": len(games)},
        "games_by_season": games_by_season,
        "site_counts": {
            int(season): {
                status: int(count)
                for status, count in rows["site_status"].value_counts()
                .sort_index().items()
            }
            for season, rows in season_rows
        },
        "boxscore_link_coverage": {
            int(season): float(rows["boxscore_url"].notna().mean())
            for season, rows in season_rows
        },
        "venue_coverage": {
            int(season): float(rows["venue_name"].notna().mean())
            for season, rows in season_rows
        },
        "attendance_raw_coverage": {
            int(season): float(rows["attendance"].notna().mean())
            for season, rows in season_rows
        },
        "season_sha256": {
            int(season): _season_sha256(rows)
            for season, rows in season_rows
        },
    }


def run(data_dir: Path) -> None:
    raise RuntimeError(
        "historical publication is unavailable until its gates are installed"
    )
