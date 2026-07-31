"""Assemble and validate the canonical game and team-game panels."""

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import stat
import tempfile
from pathlib import Path

import pandas as pd

from nba_data import (
    bref,
    cache,
    franchises,
    hoopr,
    legacy,
    publication,
    stats_nba,
)


JOIN_KEY = [
    "season",
    "game_date",
    "home_franchise_id",
    "away_franchise_id",
]
EXPECTED_LEGACY_GAMES = 40_828
TEAM_GAME_COLUMNS = [
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
BOX_STATS = TEAM_GAME_COLUMNS[8:]
GAME_COLUMNS = [
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
]
TEAM_GAME_DTYPES = {
    "game_id": "string",
    "season": "int64",
    "source": "string",
    "franchise_id": "string",
    "opponent_franchise_id": "string",
    "is_home": "bool",
    "wl": "int64",
    **{column: "Int64" for column in BOX_STATS},
}
GAME_DTYPES = {
    "game_id": "string",
    "espn_game_id": "string",
    "season": "int64",
    "source": "string",
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
}
PUBLISHED_GAME_COLUMNS = GAME_COLUMNS + [
    "capacity",
    "capacity_source_url",
    "capacity_note",
]
PUBLISHED_GAME_DTYPES = {
    **GAME_DTYPES,
    "capacity": "Int64",
    "capacity_source_url": "string",
    "capacity_note": "string",
}
ATTENDANCE_STATUSES = {
    "reported_zero",
    "reported_positive",
    "missing",
}
ATTENDANCE_SOURCES = {"bref", "espn", "legacy"}
CORRECTION_COLUMNS = [
    "game_id",
    "franchise_id",
    "field",
    "corrected_value",
    "source_url",
    "note",
    "attendance_coverage_exemption_reason",
]
TEAM_CORRECTION_FIELDS = set(BOX_STATS) | {"pts"}
GAME_CORRECTION_FIELDS = {"attendance", "neutral_site"}
CAPACITY_COLUMNS = [
    "venue_id",
    "franchise_id",
    "first_season",
    "last_season",
    "capacity",
    "capacity_source_url",
    "capacity_note",
]
CAPACITY_EXCEPTION_PREFIX = "ATTENDANCE_EXCEPTION: "
PUBLISHED_FILES = (
    "team_games.parquet",
    "games.parquet",
    "franchises.parquet",
    "venues.parquet",
    "build_manifest.json",
)
PRESERVED_HISTORICAL_FILES = (
    "historical_games.parquet",
    "historical_games_manifest.json",
)
PRESERVED_PLAYOFF_FILES = (
    "playoff_games.parquet",
    "playoff_games_manifest.json",
)
PRESERVED_PUBLICATION_PAIRS = {
    "historical": PRESERVED_HISTORICAL_FILES,
    "playoff": PRESERVED_PLAYOFF_FILES,
}
MODERN_REFERENCE_FILES = (
    "franchises.csv",
    "game_corrections.csv",
    "hoopr_snapshot.json",
    "venue_capacities.csv",
)
FRANCHISE_COLUMNS = [
    "alias",
    "franchise_id",
    "first_season",
    "last_season",
]
FRANCHISE_DTYPES = {
    "alias": "string",
    "franchise_id": "string",
    "first_season": "Int64",
    "last_season": "Int64",
}
VENUE_DTYPES = {
    "venue_id": "string",
    "franchise_id": "string",
    "first_season": "int64",
    "last_season": "int64",
    "capacity": "int64",
    "capacity_source_url": "string",
    "capacity_note": "string",
}
ATTENDANCE_COVERAGE_EXEMPTION_REASON = (
    "no_fan_bubble_missing_attendance"
)
ATTENDANCE_COVERAGE_EXEMPT_GAME_IDS = frozenset(
    f"002190{sequence:04d}"
    for sequence in range(1231, 1319)
    if sequence != 1233
)


class ValidationError(ValueError):
    """The assembled panel failed one or more publication gates."""


def load_game_corrections(path: Path) -> pd.DataFrame:
    """Load and validate auditable source-backed game corrections."""
    corrections = pd.read_csv(path, dtype="string")
    if corrections.columns.tolist() != CORRECTION_COLUMNS:
        raise ValidationError(
            "game correction columns must be "
            f"{CORRECTION_COLUMNS}"
        )
    duplicate = corrections.duplicated(
        ["game_id", "franchise_id", "field"], keep=False
    )
    if duplicate.any():
        raise ValidationError(
            "duplicate game correction: "
            f"{corrections.loc[duplicate, 'game_id'].head().tolist()}"
        )
    valid_url = corrections["source_url"].str.startswith(
        ("http://", "https://"), na=False
    )
    if not valid_url.all():
        raise ValidationError("invalid correction source URL")
    valid_note = corrections["note"].str.strip().ne("").fillna(False)
    if not valid_note.all():
        raise ValidationError("missing game correction note")
    team_scope = corrections["franchise_id"].notna()
    invalid_team = team_scope & ~corrections["field"].isin(
        TEAM_CORRECTION_FIELDS
    )
    invalid_game = ~team_scope & ~corrections["field"].isin(
        GAME_CORRECTION_FIELDS
    )
    if (invalid_team | invalid_game).any():
        raise ValidationError("invalid game correction field or scope")
    box_corrections = team_scope
    valid_box_value = corrections["corrected_value"].str.fullmatch(
        r"(?:0|[1-9][0-9]*)", na=False
    )
    if (box_corrections & ~valid_box_value).any():
        raise ValidationError("invalid box correction value")
    attendance_corrections = corrections["field"].eq("attendance")
    if (
        attendance_corrections
        & corrections["corrected_value"].notna()
    ).any():
        raise ValidationError("attendance correction value must be blank")
    boolean_corrections = corrections["field"].eq("neutral_site")
    valid_boolean_value = corrections["corrected_value"].isin(
        {"true", "false"}
    )
    if (boolean_corrections & ~valid_boolean_value).any():
        raise ValidationError("invalid boolean correction value")
    exemption_reason = corrections[
        "attendance_coverage_exemption_reason"
    ]
    has_exemption = exemption_reason.notna()
    valid_exemption = (
        exemption_reason.eq(
            ATTENDANCE_COVERAGE_EXEMPTION_REASON
        ).fillna(False)
        & ~team_scope
        & corrections["field"].eq("neutral_site")
        & corrections["corrected_value"].eq("true")
        & corrections["game_id"].isin(
            ATTENDANCE_COVERAGE_EXEMPT_GAME_IDS
        )
    )
    if (has_exemption & ~valid_exemption).any():
        raise ValidationError(
            "invalid attendance coverage exemption assignment"
        )
    return corrections


def load_venue_capacities(path: Path | None = None) -> pd.DataFrame:
    """Load the curated season-aware basketball capacity reference."""
    if path is None:
        path = (
            Path(__file__).resolve().parents[2]
            / "data/reference/venue_capacities.csv"
        )
    venues = pd.read_csv(
        path,
        dtype={
            "venue_id": "string",
            "franchise_id": "string",
            "capacity_source_url": "string",
            "capacity_note": "string",
        },
    )
    if venues.columns.tolist() != CAPACITY_COLUMNS:
        raise ValidationError(
            f"venue capacity columns must be {CAPACITY_COLUMNS}"
        )
    for column in ("first_season", "last_season", "capacity"):
        venues[column] = pd.to_numeric(
            venues[column], errors="raise"
        ).astype("int64")
    valid_url = venues["capacity_source_url"].str.startswith(
        ("http://", "https://"), na=False
    )
    valid = (
        venues["venue_id"].notna()
        & venues["franchise_id"].notna()
        & venues["capacity"].gt(0)
        & venues["first_season"].le(venues["last_season"])
        & valid_url
    )
    if not valid.all():
        raise ValidationError(
            "venue rows require positive capacity and direct source URL"
        )
    return venues


def attach_capacity(
    games: pd.DataFrame,
    venues: pd.DataFrame,
) -> pd.DataFrame:
    """Attach exactly one inclusive season-aware capacity to primary games."""
    attached = games.copy()
    for column in (
        "capacity",
        "capacity_source_url",
        "capacity_note",
    ):
        attached[column] = pd.NA
    primary = (
        attached["season"].between(2020, 2022)
        & ~attached["neutral_site"]
    )
    targets = (
        attached.loc[primary]
        .drop(
            columns=[
                "capacity",
                "capacity_source_url",
                "capacity_note",
            ]
        )
        .reset_index(names="_capacity_game_index")
    )
    candidates = targets.merge(
        venues,
        left_on=["venue_id", "home_franchise_id"],
        right_on=["venue_id", "franchise_id"],
        how="left",
    )
    in_era = (
        candidates["first_season"].notna()
        & candidates["season"].ge(candidates["first_season"])
        & candidates["season"].le(candidates["last_season"])
    )
    resolved = candidates[in_era]
    counts = (
        resolved.groupby("_capacity_game_index")
        .size()
        .reindex(targets["_capacity_game_index"], fill_value=0)
    )
    bad = counts[counts.ne(1)]
    if not bad.empty:
        count = int(bad.iloc[0])
        game_index = bad.index[0]
        game_id = attached.loc[game_index, "game_id"]
        raise ValidationError(
            f"capacity reference resolved {count} rows: {game_id}"
        )
    selected = resolved.set_index("_capacity_game_index")
    for column in (
        "capacity",
        "capacity_source_url",
        "capacity_note",
    ):
        attached.loc[selected.index, column] = selected[column]
    attached["capacity"] = attached["capacity"].astype("Int64")
    attached["capacity_source_url"] = attached[
        "capacity_source_url"
    ].astype("string")
    attached["capacity_note"] = attached["capacity_note"].astype("string")

    over = (
        primary
        & attached["attendance"].notna()
        & attached["attendance"].gt(attached["capacity"] * 1.05)
    )
    undocumented = over & ~attached["capacity_note"].str.startswith(
        CAPACITY_EXCEPTION_PREFIX,
        na=False,
    )
    if undocumented.any():
        raise ValidationError(
            "attendance above 105% of capacity without explicit "
            "configuration exception: "
            f"{attached.loc[undocumented, 'game_id'].head().tolist()}"
        )
    return attached


def apply_team_game_corrections(
    team_games: pd.DataFrame,
    corrections: pd.DataFrame,
) -> pd.DataFrame:
    """Apply exact franchise-scoped corrections without mutating inputs."""
    corrected = team_games.copy()
    scoped = corrections[corrections["franchise_id"].notna()]
    for correction in scoped.itertuples(index=False):
        match = (
            corrected["game_id"].eq(correction.game_id)
            & corrected["franchise_id"].eq(correction.franchise_id)
        )
        if int(match.sum()) != 1:
            raise ValidationError(
                "team game correction resolved "
                f"{int(match.sum())} rows: "
                f"{correction.game_id}/{correction.franchise_id}"
            )
        corrected.loc[match, correction.field] = int(
            correction.corrected_value
        )
    return corrected


def apply_game_corrections(
    games: pd.DataFrame,
    corrections: pd.DataFrame,
) -> pd.DataFrame:
    """Apply exact game-scoped corrections without mutating inputs."""
    corrected = games.copy()
    exemption_column = "attendance_coverage_exemption_reason"
    if exemption_column not in corrected:
        corrected[exemption_column] = pd.NA
    scoped = corrections[corrections["franchise_id"].isna()]
    for correction in scoped.itertuples(index=False):
        match = corrected["game_id"].eq(correction.game_id)
        if int(match.sum()) != 1:
            raise ValidationError(
                "game correction resolved "
                f"{int(match.sum())} rows: {correction.game_id}"
            )
        if correction.field == "attendance":
            value = (
                pd.NA
                if pd.isna(correction.corrected_value)
                else int(correction.corrected_value)
            )
            corrected.loc[match, "attendance"] = value
            corrected.loc[match, "attendance_status"] = (
                "missing"
                if pd.isna(value)
                else (
                    "reported_zero"
                    if value == 0
                    else "reported_positive"
                )
            )
        else:
            value = correction.corrected_value == "true"
            corrected.loc[match, "neutral_site"] = value
            corrected.loc[match, "neutral_site_source"] = (
                "game_correction:neutral_site"
            )
            corrected.loc[match, exemption_column] = getattr(
                correction,
                exemption_column,
                pd.NA,
            )
    corrected[exemption_column] = corrected[exemption_column].astype("string")
    return corrected


def _enforce_schema(
    frame: pd.DataFrame,
    columns: list[str],
    dtypes: dict[str, str],
) -> pd.DataFrame:
    output = frame.loc[:, columns].copy()
    output["game_date"] = pd.to_datetime(
        output["game_date"], errors="raise"
    ).dt.date
    for column, dtype in dtypes.items():
        output[column] = output[column].astype(dtype)
    return output


def pair_team_games(team_games: pd.DataFrame) -> pd.DataFrame:
    """Pair canonical home and away team rows into one row per game."""
    counts = team_games.groupby("game_id")["is_home"].agg(["size", "sum"])
    bad = counts[(counts["size"] != 2) | (counts["sum"] != 1)]
    if not bad.empty:
        raise ValidationError(
            f"games without one home and one away row: {bad.index[:5].tolist()}"
        )
    home = team_games[team_games["is_home"]].set_index("game_id")
    away = team_games[~team_games["is_home"]].set_index("game_id")
    away = away.reindex(home.index)
    return pd.DataFrame({
        "game_id": home.index,
        "season": home["season"].to_numpy(),
        "source": home["source"].to_numpy(),
        "game_date": home["game_date"].to_numpy(),
        "home_franchise_id": home["franchise_id"].to_numpy(),
        "away_franchise_id": away["franchise_id"].to_numpy(),
        "home_pts": home["pts"].to_numpy(),
        "away_pts": away["pts"].to_numpy(),
    }).reset_index(drop=True)


def _apply_schedule_home_designations(
    team_games: pd.DataFrame,
    schedule: pd.DataFrame,
) -> pd.DataFrame:
    """Repair neutral games whose NBA rows omit a home designation."""
    corrected = team_games.copy()
    counts = corrected.groupby("game_id")["is_home"].agg(["size", "sum"])
    invalid_ids = counts[
        (counts["size"] == 2) & (counts["sum"] != 1)
    ].index
    for game_id in invalid_ids:
        rows = corrected[corrected["game_id"].eq(game_id)]
        first = rows.iloc[0]
        franchises = set(rows["franchise_id"])
        candidates = schedule[
            schedule["season"].eq(first["season"])
            & schedule["game_date"].eq(first["game_date"])
            & schedule["home_franchise_id"].isin(franchises)
            & schedule["away_franchise_id"].isin(franchises)
        ]
        if len(candidates) == 1:
            home = candidates.iloc[0]["home_franchise_id"]
            corrected.loc[rows.index, "is_home"] = rows[
                "franchise_id"
            ].eq(home)
    return corrected


def _reconcile_next_day_schedule_dates(
    nba_games: pd.DataFrame,
    schedule: pd.DataFrame,
) -> pd.DataFrame:
    """Align uniquely identifiable next-day ESPN dates to NBA game dates."""
    aligned = schedule.copy()
    nba_key_counts = (
        nba_games.groupby(JOIN_KEY, dropna=False)
        .size()
        .rename("_nba_key_count")
        .reset_index()
    )
    exact_candidates = (
        aligned.reset_index(names="_schedule_index")
        .merge(nba_key_counts, on=JOIN_KEY, how="inner")
        .sort_values(
            JOIN_KEY + ["espn_game_id"],
            ascending=[True] * len(JOIN_KEY) + [False],
            kind="stable",
        )
    )
    reserved = (
        exact_candidates.groupby(JOIN_KEY, dropna=False).cumcount()
        < exact_candidates["_nba_key_count"]
    )
    claimed = set(
        exact_candidates.loc[reserved, "_schedule_index"].tolist()
    )
    ordered_games = nba_games.sort_values(
        JOIN_KEY + ["game_id"],
        kind="stable",
    )
    for game in ordered_games.itertuples(index=False):
        exact = pd.Series(True, index=aligned.index)
        for column in JOIN_KEY:
            exact &= aligned[column].eq(getattr(game, column))
        if exact.any():
            continue
        candidate = (
            aligned["season"].eq(game.season)
            & aligned["home_franchise_id"].eq(game.home_franchise_id)
            & aligned["away_franchise_id"].eq(game.away_franchise_id)
            & pd.to_datetime(aligned["game_date"]).eq(
                pd.Timestamp(game.game_date) + pd.Timedelta(days=1)
            )
            & ~aligned.index.isin(claimed)
        )
        matches = aligned.index[candidate]
        if len(matches) == 1:
            aligned.loc[matches[0], "game_date"] = game.game_date
            claimed.add(matches[0])
    return aligned


def enrich_modern_games(
    nba_games: pd.DataFrame,
    schedule: pd.DataFrame,
    bref: pd.DataFrame,
) -> pd.DataFrame:
    """Add hoopR venue metadata and b-ref-first attendance to NBA games."""
    schedule = _reconcile_next_day_schedule_dates(nba_games, schedule)
    enriched = nba_games.merge(
        schedule.rename(columns={"espn_attendance": "attendance_espn"}),
        on=JOIN_KEY,
        how="left",
        validate="one_to_one",
        indicator="schedule_join",
    )
    missing = enriched["schedule_join"].ne("both")
    if missing.any():
        raise ValidationError(
            "NBA games without hoopR schedule: "
            f"{enriched.loc[missing, 'game_id'].head().tolist()}"
        )

    if bref.empty:
        bref = pd.DataFrame(
            columns=JOIN_KEY + ["attendance", "arena"]
        )
    bref = bref.rename(columns={
        "attendance": "attendance_bref",
        "arena": "venue_name_bref",
    })
    enriched = enriched.merge(
        bref,
        on=JOIN_KEY,
        how="left",
        validate="one_to_one",
        indicator="bref_join",
    )
    matched = enriched["bref_join"].eq("both")
    enriched["bref_row_matched"] = matched
    enriched["attendance"] = enriched["attendance_bref"].where(
        matched, enriched["attendance_espn"]
    ).astype("Int64")
    enriched["attendance_source"] = matched.map({
        True: "bref",
        False: "espn",
    })
    enriched["attendance_status"] = enriched["attendance"].map(
        lambda value: (
            "missing"
            if pd.isna(value)
            else ("reported_zero" if value == 0 else "reported_positive")
        )
    )

    bref_arena_known = matched & enriched["venue_name_bref"].notna()
    bref_arena_known &= enriched["venue_name_bref"].astype(
        "string"
    ).str.strip().ne("")
    enriched["venue_name"] = enriched["venue_name_bref"].where(
        bref_arena_known, enriched["venue_name_espn"]
    )
    enriched["venue_source"] = bref_arena_known.map({
        True: "bref",
        False: "espn",
    })
    enriched["neutral_site_source"] = "hoopr"
    return enriched.drop(columns=[
        "schedule_join",
        "bref_join",
        "venue_name_bref",
        "venue_name_espn",
    ])


def _validate(
    team_games: pd.DataFrame,
    games: pd.DataFrame,
    expected_counts: dict[int, int],
    expected_legacy: int,
    *,
    enforce_capacity: bool,
) -> dict:
    errors = []
    games_by_season = {
        int(season): int(count)
        for season, count in games.groupby("season").size().items()
    }
    team_games_by_season = {
        int(season): int(count)
        for season, count in team_games.groupby("season").size().items()
    }
    bref_join_coverage = {}
    selected_coverage = {}
    raw_selected_coverage = {}
    selected_eligible_denominator = {}
    selected_exemptions = {}
    agreement = {}
    for season in expected_counts:
        modern = games[games["season"].eq(season)]
        bref_join_coverage[season] = (
            float(modern["bref_row_matched"].mean()) if len(modern) else 0.0
        )
        raw_selected_coverage[season] = (
            float(modern["attendance"].notna().mean()) if len(modern) else 0.0
        )
        exemption_reason = modern.get(
            "attendance_coverage_exemption_reason",
            pd.Series(pd.NA, index=modern.index, dtype="string"),
        )
        exempt = (
            exemption_reason.eq(
                ATTENDANCE_COVERAGE_EXEMPTION_REASON
            ).fillna(False)
            & modern["game_id"].isin(
                ATTENDANCE_COVERAGE_EXEMPT_GAME_IDS
            )
            & modern["attendance_source"].eq("bref")
            & modern["attendance"].isna()
        )
        eligible = modern[~exempt]
        selected_eligible_denominator[season] = len(eligible)
        selected_exemptions[season] = int(exempt.sum())
        selected_coverage[season] = (
            float(eligible["attendance"].notna().mean())
            if len(eligible)
            else 0.0
        )
        compared = modern[
            modern["attendance_bref"].notna()
            & modern["attendance_espn"].notna()
        ]
        agreement[season] = (
            float(
                compared["attendance_bref"].eq(
                    compared["attendance_espn"]
                ).mean()
            )
            if len(compared)
            else None
        )
    attendance_counts = {}
    for season, season_games in games.groupby("season"):
        statuses = season_games["attendance_status"].value_counts()
        attendance_counts[int(season)] = {
            "reported_zero": int(statuses.get("reported_zero", 0)),
            "reported_positive": int(statuses.get("reported_positive", 0)),
            "missing": int(statuses.get("missing", 0)),
        }
    report = {
        "totals": {
            "games": len(games),
            "team_games": len(team_games),
        },
        "games_by_season": games_by_season,
        "team_games_by_season": team_games_by_season,
        "bref_join_coverage": bref_join_coverage,
        "attendance_counts": attendance_counts,
        "selected_attendance_raw_coverage": raw_selected_coverage,
        "selected_attendance_eligible_denominator": (
            selected_eligible_denominator
        ),
        "selected_attendance_exemptions": selected_exemptions,
        "selected_attendance_coverage": selected_coverage,
        "bref_espn_agreement": agreement,
    }

    duplicate_ids = sorted(
        games.loc[games["game_id"].duplicated(keep=False), "game_id"]
        .astype(str)
        .unique()
        .tolist()
    )
    if duplicate_ids:
        errors.append(f"duplicate game_id: {duplicate_ids[:5]}")

    game_ids = set(games["game_id"])
    team_game_ids = set(team_games["game_id"])
    if game_ids != team_game_ids:
        errors.append(
            "games/team_games ID-set mismatch: "
            f"only_games={sorted(game_ids - team_game_ids)[:5]}, "
            f"only_team_games={sorted(team_game_ids - game_ids)[:5]}"
        )

    sides = team_games.groupby("game_id")["is_home"].agg(["size", "sum"])
    bad_sides = sides[(sides["size"] != 2) | (sides["sum"] != 1)]
    if not bad_sides.empty:
        errors.append(
            "games without one home and one away row: "
            f"{bad_sides.index[:5].tolist()}"
        )

    invalid_wl = ~team_games["wl"].isin([0, 1])
    winner_counts = team_games.groupby("game_id")["wl"].agg(
        ["size", "sum"]
    )
    bad_winners = winner_counts[
        (winner_counts["size"] != 2)
        | (winner_counts["sum"] != 1)
    ].index.union(
        team_games.loc[invalid_wl, "game_id"].unique()
    )
    if len(bad_winners):
        errors.append(
            "game without exactly one winner: "
            f"{bad_winners[:5].tolist()}"
        )

    unique_games = games[~games["game_id"].duplicated(keep=False)]
    comparable = team_games[
        ~team_games["game_id"].isin(bad_sides.index)
    ].merge(
        unique_games[[
            "game_id",
            "season",
            "game_date",
            "home_franchise_id",
            "away_franchise_id",
            "home_pts",
            "away_pts",
        ]],
        on="game_id",
        how="inner",
        suffixes=("_team", "_game"),
        validate="many_to_one",
    )
    if not comparable.empty:
        expected_franchise = comparable["home_franchise_id"].where(
            comparable["is_home"], comparable["away_franchise_id"]
        )
        expected_opponent = comparable["away_franchise_id"].where(
            comparable["is_home"], comparable["home_franchise_id"]
        )
        expected_pts = comparable["home_pts"].where(
            comparable["is_home"], comparable["away_pts"]
        )
        disagreement = (
            comparable["franchise_id"].ne(expected_franchise)
            | comparable["opponent_franchise_id"].ne(expected_opponent)
            | comparable["pts"].ne(expected_pts)
            | comparable["season_team"].ne(comparable["season_game"])
            | comparable["game_date_team"].ne(comparable["game_date_game"])
        ).fillna(True)
        if disagreement.any():
            errors.append(
                "team/game disagreement: "
                f"{comparable.loc[disagreement, 'game_id'].head().tolist()}"
            )
        expected_wl = comparable["home_pts"].gt(
            comparable["away_pts"]
        ).where(
            comparable["is_home"],
            comparable["away_pts"].gt(comparable["home_pts"]),
        ).astype("int64")
        wl_disagreement = comparable["wl"].ne(expected_wl).fillna(True)
        if wl_disagreement.any():
            errors.append(
                "wl disagrees with final score: "
                f"{comparable.loc[wl_disagreement, 'game_id'].head().tolist()}"
            )

    referenced_franchises = set()
    for frame, columns in (
        (
            team_games,
            ["franchise_id", "opponent_franchise_id"],
        ),
        (
            games,
            ["home_franchise_id", "away_franchise_id"],
        ),
    ):
        for column in columns:
            referenced_franchises.update(frame[column].dropna().astype(str))
    unknown = sorted(referenced_franchises - franchises.FRANCHISE_IDS)
    if unknown:
        errors.append(f"unknown franchise: {unknown[:5]}")

    expected_seasons = {
        season for season, expected in expected_counts.items() if expected > 0
    }
    missing_seasons = sorted(expected_seasons - set(games["season"]))
    if missing_seasons:
        errors.append(f"missing expected season: {missing_seasons}")

    for season, expected in expected_counts.items():
        actual_games = games_by_season.get(season, 0)
        actual_team_games = team_games_by_season.get(season, 0)
        if actual_games != expected:
            errors.append(
                f"season {season} games: {actual_games}, expected {expected}"
            )
        if actual_team_games != expected * 2:
            errors.append(
                f"season {season} team_games: {actual_team_games}, "
                f"expected {expected * 2}"
            )

    modern_seasons = set(expected_counts)
    actual_legacy_games = int((~games["season"].isin(modern_seasons)).sum())
    actual_legacy_team_games = int(
        (~team_games["season"].isin(modern_seasons)).sum()
    )
    if actual_legacy_games != expected_legacy:
        errors.append(
            f"legacy games: {actual_legacy_games}, expected {expected_legacy}"
        )
    if actual_legacy_team_games != expected_legacy * 2:
        errors.append(
            "legacy team_games: "
            f"{actual_legacy_team_games}, expected {expected_legacy * 2}"
        )

    expected_total = expected_legacy + sum(expected_counts.values())
    if len(games) != expected_total:
        errors.append(f"total games: {len(games)}, expected {expected_total}")
    if len(team_games) != expected_total * 2:
        errors.append(
            f"total team_games: {len(team_games)}, expected {expected_total * 2}"
        )

    invalid_score = (
        ~games["home_pts"].between(40, 200)
        | ~games["away_pts"].between(40, 200)
        | games["home_pts"].eq(games["away_pts"])
    ).fillna(True)
    if invalid_score.any():
        errors.append(
            "invalid score or tie: "
            f"{games.loc[invalid_score, 'game_id'].head().tolist()}"
        )

    missing_stat_columns = [
        column for column in BOX_STATS if column not in team_games
    ]
    if missing_stat_columns:
        errors.append(
            f"missing box statistic columns: {missing_stat_columns}"
        )
    else:
        missing_stats = team_games[BOX_STATS].isna().any(axis=1)
        if missing_stats.any():
            errors.append(
                "missing box statistic: "
                f"{team_games.loc[missing_stats, 'game_id'].head().tolist()}"
            )
        negative_stats = team_games[BOX_STATS].lt(0).any(axis=1)
        if negative_stats.any():
            errors.append(
                "negative box statistic: "
                f"{team_games.loc[negative_stats, 'game_id'].head().tolist()}"
            )
        makes_above_attempts = (
            team_games["fgm"].gt(team_games["fga"])
            | team_games["fg3m"].gt(team_games["fg3a"])
            | team_games["ftm"].gt(team_games["fta"])
        ).fillna(False)
        if makes_above_attempts.any():
            errors.append(
                "makes exceed attempts: "
                f"{team_games.loc[makes_above_attempts, 'game_id'].head().tolist()}"
            )
        rebound_disagreement = (
            team_games["oreb"]
            .add(team_games["dreb"])
            .ne(team_games["reb"])
        ).fillna(False)
        if rebound_disagreement.any():
            errors.append(
                "rebound component disagreement: "
                f"{team_games.loc[rebound_disagreement, 'game_id'].head().tolist()}"
            )
        point_disagreement = (
            team_games["fgm"]
            .mul(2)
            .add(team_games["fg3m"])
            .add(team_games["ftm"])
            .ne(team_games["pts"])
        ).fillna(False)
        if point_disagreement.any():
            errors.append(
                "point accounting disagreement: "
                f"{team_games.loc[point_disagreement, 'game_id'].head().tolist()}"
            )

    bad_zero = games["attendance_status"].eq("reported_zero") & ~games[
        "season"
    ].between(2020, 2022)
    if bad_zero.any():
        errors.append(
            "reported_zero outside 2020..2022: "
            f"{games.loc[bad_zero, 'game_id'].head().tolist()}"
        )

    unsupported_status = ~games["attendance_status"].isin(
        ATTENDANCE_STATUSES
    )
    if unsupported_status.any():
        values = sorted(
            games.loc[unsupported_status, "attendance_status"]
            .astype("string")
            .fillna("<missing>")
            .unique()
            .tolist()
        )
        errors.append(f"unsupported attendance_status: {values}")

    unsupported_source = ~games["attendance_source"].isin(
        ATTENDANCE_SOURCES
    )
    if unsupported_source.any():
        values = sorted(
            games.loc[unsupported_source, "attendance_source"]
            .astype("string")
            .fillna("<missing>")
            .unique()
            .tolist()
        )
        errors.append(f"unsupported attendance_source: {values}")

    expected_status = games["attendance"].map(
        lambda value: (
            "missing"
            if pd.isna(value)
            else ("reported_zero" if value == 0 else "reported_positive")
        )
    )
    inconsistent_status = games["attendance_status"].ne(
        expected_status
    ).fillna(True)
    if inconsistent_status.any():
        errors.append(
            "attendance status/value disagreement: "
            f"{games.loc[inconsistent_status, 'game_id'].head().tolist()}"
        )

    low_bref = {
        season: coverage
        for season, coverage in bref_join_coverage.items()
        if coverage < 0.95
    }
    if low_bref:
        errors.append(f"b-ref join coverage below 95%: {low_bref}")

    low_selected = {
        season: coverage
        for season, coverage in selected_coverage.items()
        if coverage < 0.95
    }
    if low_selected:
        errors.append(
            "selected known-attendance coverage below 95%: "
            f"{low_selected}"
        )

    if enforce_capacity:
        primary_non_neutral = (
            games["season"].between(2020, 2022)
            & ~games["neutral_site"]
        )
        missing_capacity = primary_non_neutral
        if "capacity" in games:
            missing_capacity &= games["capacity"].isna()
        if missing_capacity.any():
            errors.append(
                "non-neutral 2020..2022 venue missing capacity: "
                f"{games.loc[missing_capacity, 'game_id'].head().tolist()}"
            )

    if errors:
        raise ValidationError("; ".join(errors))
    return report


def validate(
    team_games: pd.DataFrame,
    games: pd.DataFrame,
    expected_counts: dict[int, int],
    expected_legacy: int,
) -> dict:
    """Validate publication invariants and return a deterministic coverage report."""
    return _validate(
        team_games,
        games,
        expected_counts,
        expected_legacy,
        enforce_capacity=True,
    )


def _assemble_frames(
    raw_dir: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    corrections = load_game_corrections(
        raw_dir.parent / "reference/game_corrections.csv"
    )
    team_corrections = corrections[
        corrections["franchise_id"].notna()
    ]
    applied_team_corrections = 0
    legacy_path = (
        raw_dir / "legacy" / "nba-teams-1984-2019-ordered.csv"
    )
    legacy_attendance_path = (
        raw_dir / "legacy" / "league_boxscores_extra.csv"
    )
    legacy_team_games = legacy.load_team_games(legacy_path)
    scoped = team_corrections[
        team_corrections["game_id"].isin(legacy_team_games["game_id"])
    ]
    legacy_team_games = apply_team_game_corrections(
        legacy_team_games, scoped
    )
    applied_team_corrections += len(scoped)
    legacy_games = pair_team_games(legacy_team_games).merge(
        legacy.load_attendance(legacy_attendance_path),
        on="game_id",
        how="left",
        validate="one_to_one",
    )
    legacy_games = legacy_games.assign(
        espn_game_id=pd.NA,
        neutral_site=False,
        neutral_site_source="legacy_home_designation_assumed",
        venue_id=pd.NA,
        venue_name=pd.NA,
        venue_city=pd.NA,
        venue_source="legacy_unknown",
        attendance_bref=pd.NA,
        attendance_espn=pd.NA,
        bref_row_matched=False,
        attendance_source="legacy",
    )
    legacy_games["attendance_status"] = legacy_games["attendance"].map(
        lambda value: (
            "missing" if pd.isna(value) else "reported_positive"
        )
    )

    modern_team_frames = []
    modern_game_frames = []
    schedule_exclusions = {}
    for season in stats_nba.SEASONS:
        season_team_games = stats_nba.load_season(raw_dir, season)
        schedule, excluded = hoopr.load_season(raw_dir, season)
        reasons = (
            excluded["reason"].astype("string").value_counts().sort_index()
            if len(excluded)
            else pd.Series(dtype="int64")
        )
        schedule_exclusions[season] = {
            "total": len(excluded),
            "by_reason": {
                str(reason): int(count)
                for reason, count in reasons.items()
            },
            "espn_game_ids": sorted(
                f"espn-{int(game_id)}"
                for game_id in excluded.get(
                    "game_id", pd.Series(dtype="int64")
                )
            ),
        }
        scoped = team_corrections[
            team_corrections["game_id"].isin(
                season_team_games["game_id"]
            )
        ]
        season_team_games = apply_team_game_corrections(
            season_team_games, scoped
        )
        applied_team_corrections += len(scoped)
        season_team_games = _apply_schedule_home_designations(
            season_team_games, schedule
        )
        attendance = bref.load_attendance(raw_dir, season)
        modern_team_frames.append(season_team_games)
        modern_game_frames.append(
            enrich_modern_games(
                pair_team_games(season_team_games),
                schedule,
                attendance,
            )
        )

    if applied_team_corrections != len(team_corrections):
        raise ValidationError(
            "team game correction resolved 0 rows: "
            "one or more reference targets are absent"
        )
    team_games = _enforce_schema(
        pd.concat(
            [legacy_team_games, *modern_team_frames],
            ignore_index=True,
        ),
        TEAM_GAME_COLUMNS,
        TEAM_GAME_DTYPES,
    ).sort_values(
        ["season", "game_date", "game_id", "is_home"],
        ascending=[True, True, True, False],
        kind="stable",
    ).reset_index(drop=True)
    combined_games = apply_game_corrections(
        pd.concat(
            [legacy_games, *modern_game_frames], ignore_index=True
        ),
        corrections[corrections["franchise_id"].isna()],
    )
    games = _enforce_schema(
        combined_games,
        GAME_COLUMNS,
        GAME_DTYPES,
    ).sort_values(
        ["season", "game_date", "game_id"],
        kind="stable",
    ).reset_index(drop=True)
    return team_games, games, schedule_exclusions


def assemble(raw_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Load, enrich, combine, and strictly validate the publication panels."""
    team_games, games, schedule_exclusions = _assemble_frames(raw_dir)
    games = attach_capacity(
        games,
        load_venue_capacities(
            raw_dir.parent / "reference/venue_capacities.csv"
        ),
    )
    games = _enforce_schema(
        games,
        PUBLISHED_GAME_COLUMNS,
        PUBLISHED_GAME_DTYPES,
    )
    report = validate(
        team_games,
        games,
        expected_counts=stats_nba.EXPECTED_GAMES,
        expected_legacy=EXPECTED_LEGACY_GAMES,
    )
    report["schedule_exclusions"] = schedule_exclusions
    return team_games, games, report


def _assemble_for_venue_inventory(
    raw_dir: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Assemble inventory inputs while omitting only the capacity gate."""
    team_games, games, schedule_exclusions = _assemble_frames(raw_dir)
    report = _validate(
        team_games,
        games,
        stats_nba.EXPECTED_GAMES,
        EXPECTED_LEGACY_GAMES,
        enforce_capacity=False,
    )
    report["schedule_exclusions"] = schedule_exclusions
    return team_games, games, report


def venue_inventory(games: pd.DataFrame) -> pd.DataFrame:
    """Summarize observed venue eras without treating attendance as capacity."""
    post = games[games["venue_id"].notna()].copy()
    normal = post[
        ~post["neutral_site"]
        & ~post["season"].isin([2020, 2021])
        & post["attendance"].gt(0)
    ]
    observed = normal.groupby(
        ["venue_id", "venue_name", "venue_city", "home_franchise_id"],
        dropna=False,
    )["attendance"].agg(
        observed_non_covid_max="max",
        observed_non_covid_p99=lambda values: int(values.quantile(0.99)),
    )
    eras = post.groupby(
        ["venue_id", "venue_name", "venue_city", "home_franchise_id"],
        dropna=False,
    )["season"].agg(
        first_season="min",
        last_season="max",
        games="size",
    )
    output = eras.join(observed, how="left").reset_index()
    return output.sort_values(
        ["home_franchise_id", "first_season", "venue_id"]
    ).reset_index(drop=True)


def write_venue_inventory(data_dir: Path) -> Path:
    """Write the venue-era observations needed to curate capacity data."""
    _, games, _ = _assemble_for_venue_inventory(data_dir / "raw")
    output = venue_inventory(games)
    destination = data_dir / "raw/reports/venue_inventory.csv"
    destination.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(destination, index=False)
    return destination


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_metadata(path: Path, content_path: Path) -> dict:
    if not path.is_file():
        raise ValidationError(
            f"missing raw cache metadata for {content_path}"
        )
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValidationError(
            f"invalid raw cache metadata for {content_path}"
        ) from exc
    if not isinstance(metadata, dict):
        raise ValidationError(
            f"invalid raw cache metadata for {content_path}"
        )
    digest = _sha256(content_path)
    expected = metadata.get("sha256")
    if not isinstance(expected, str):
        raise ValidationError(
            f"missing SHA-256 in raw cache metadata for {content_path}"
        )
    if expected != digest:
        raise ValidationError(
            f"raw SHA-256 mismatch for {content_path.name}: "
            f"{digest}, expected {expected}"
        )
    return metadata


def _paired_raw_paths(
    directory: Path,
    body_pattern: str,
    sidecar_pattern: str,
) -> list[Path]:
    bodies = {
        path
        for path in directory.glob(body_pattern)
        if path.is_file() and not path.name.endswith(".meta.json")
    }
    sidecars = {
        path
        for path in directory.glob(sidecar_pattern)
        if path.is_file()
    }
    expected_sidecars = {
        path.with_name(path.name + ".meta.json")
        for path in bodies
    }
    if sidecars != expected_sidecars:
        raise ValidationError(
            f"incomplete raw cache pair in {directory}"
        )
    return sorted(bodies)


def _relative_path(path: Path, data_dir: Path) -> str:
    return path.relative_to(data_dir).as_posix()


def _historical_reconciliation_provenance(
    data_dir: Path,
) -> list[dict]:
    raw_dir = data_dir / "raw/reconciliation/bref"
    raw_paths = sorted(
        raw_dir.glob("*.html")
    )
    sidecar_paths = set(raw_dir.glob("*.html.meta.json"))
    expected_sidecar_paths = {
        path.with_name(path.name + ".meta.json")
        for path in raw_paths
    }
    if sidecar_paths != expected_sidecar_paths:
        raise ValidationError(
            "incomplete historical box-score cache pair"
        )
    correction_path = data_dir / "reference/game_corrections.csv"
    if not correction_path.exists():
        if raw_paths:
            raise ValidationError(
                "historical box-score correction ledger is missing"
            )
        return []

    corrections = load_game_corrections(correction_path)
    expected_rows = (
        corrections.loc[
            corrections["franchise_id"].notna(),
            ["game_id", "source_url"],
        ]
        .drop_duplicates()
    )
    if (
        expected_rows["game_id"].duplicated(keep=False).any()
        or expected_rows["source_url"].duplicated(keep=False).any()
    ):
        raise ValidationError(
            "historical box-score correction ledger is cross-labeled"
        )
    expected_pairs = set(expected_rows.itertuples(index=False, name=None))

    historical_boxscores = []
    actual_pairs = set()
    actual_game_ids = set()
    actual_urls = set()
    for path in raw_paths:
        sidecar = path.with_name(path.name + ".meta.json")
        try:
            cached = cache.read_valid(path, lambda _: None)
            metadata = json.loads(sidecar.read_text(encoding="utf-8"))
        except (
            cache.CacheIntegrityError,
            json.JSONDecodeError,
            OSError,
        ) as exc:
            raise ValidationError(
                "historical box-score cache integrity failure for "
                f"{path.name}"
            ) from exc
        if cached is None or (
            metadata.get("provider") != "basketball_reference"
            or metadata.get("resource") != "historical_boxscore"
            or not isinstance(metadata.get("sha256"), str)
            or not isinstance(metadata.get("game_id"), str)
            or not isinstance(metadata.get("url"), str)
        ):
            raise ValidationError(
                "historical box-score cache metadata mismatch for "
                f"{path.name}"
            )

        expected_url = (
            "https://www.basketball-reference.com/boxscores/"
            f"{path.name}"
        )
        game_id = metadata["game_id"]
        url = metadata["url"]
        pair = (game_id, url)
        if url != expected_url:
            raise ValidationError(
                "historical box-score URL/filename mismatch for "
                f"{path.name}"
            )
        if game_id in actual_game_ids or url in actual_urls:
            raise ValidationError(
                "duplicate historical box-score game or URL for "
                f"{path.name}"
            )
        if pair not in expected_pairs:
            raise ValidationError(
                "historical box-score page is not bound to a team "
                f"correction: {path.name}"
            )

        actual_pairs.add(pair)
        actual_game_ids.add(game_id)
        actual_urls.add(url)
        historical_boxscores.append({
            "path": _relative_path(path, data_dir),
            "game_id": game_id,
            "url": url,
            "sha256": metadata["sha256"],
        })

    if actual_pairs != expected_pairs:
        raise ValidationError(
            "historical box-score page set does not match team "
            "corrections"
        )
    return historical_boxscores


def _require_packaged_reference_match(
    checkout_path: Path,
    packaged_reference,
    label: str,
) -> bytes:
    try:
        checkout_bytes = checkout_path.read_bytes()
    except FileNotFoundError as exc:
        raise ValidationError(
            f"{label} is missing: {checkout_path}"
        ) from exc
    except OSError as exc:
        raise ValidationError(
            f"{label} is unreadable: {checkout_path}: {exc}"
        ) from exc

    packaged_bytes = packaged_reference.read_bytes()
    if checkout_bytes != packaged_bytes:
        raise ValidationError(
            f"{label} differs from the packaged runtime reference"
        )
    return checkout_bytes


def _validate_runtime_reference_copies(data_dir: Path) -> None:
    reference_dir = data_dir / "reference"
    _require_packaged_reference_match(
        reference_dir / "franchises.csv",
        franchises.REFERENCE,
        "franchise reference",
    )
    _require_packaged_reference_match(
        reference_dir / "hoopr_snapshot.json",
        hoopr.SNAPSHOT_PATH,
        "hoopR snapshot reference",
    )


def _raw_provenance(data_dir: Path) -> dict:
    raw_dir = data_dir / "raw"
    stats = []
    stats_paths = _paired_raw_paths(
        raw_dir / "stats_nba",
        "leaguegamefinder_*.json",
        "leaguegamefinder_*.json.meta.json",
    )
    for path in stats_paths:
        metadata = _load_metadata(
            path.with_name(path.name + ".meta.json"), path
        )
        stats.append({
            "path": _relative_path(path, data_dir),
            "endpoint": metadata.get("endpoint", "LeagueGameFinder"),
            "season": metadata.get("season"),
            "season_label": metadata.get("season_label"),
            "sha256": _sha256(path),
        })

    snapshot_path = data_dir / "reference/hoopr_snapshot.json"
    if snapshot_path.exists():
        _require_packaged_reference_match(
            snapshot_path,
            hoopr.SNAPSHOT_PATH,
            "hoopR snapshot reference",
        )
    snapshot = hoopr.SNAPSHOT
    hoopr_seasons = []
    hoopr_paths = _paired_raw_paths(
        raw_dir / "hoopr",
        "nba_schedule_*.parquet",
        "nba_schedule_*.parquet.meta.json",
    )
    for path in hoopr_paths:
        season = int(path.stem.removeprefix("nba_schedule_"))
        hoopr._read_pinned_cache(path, season)
        metadata = _load_metadata(
            path.with_name(path.name + ".meta.json"), path
        )
        hoopr_seasons.append({
            "path": _relative_path(path, data_dir),
            "provider": metadata["provider"],
            "season": metadata["season"],
            "upstream_commit": metadata["upstream_commit"],
            "sha256": _sha256(path),
        })

    bref_pages = []
    bref_paths = _paired_raw_paths(
        raw_dir / "bref",
        "NBA_*_games-*.html",
        "NBA_*_games-*.html.meta.json",
    )
    for path in bref_paths:
        metadata = _load_metadata(
            path.with_name(path.name + ".meta.json"), path
        )
        bref_pages.append({
            "path": _relative_path(path, data_dir),
            "url": (
                "https://www.basketball-reference.com/leagues/"
                f"{path.name}"
            ),
            "season": metadata.get("season"),
            "month": metadata.get("month"),
            "sha256": _sha256(path),
        })

    historical_boxscores = _historical_reconciliation_provenance(
        data_dir
    )

    legacy_files = [
        {
            "path": _relative_path(path, data_dir),
            "sha256": _sha256(path),
        }
        for path in sorted((raw_dir / "legacy").glob("*"))
        if path.is_file() and not path.name.endswith(".meta.json")
    ]
    return {
        "stats_nba": {
            "endpoint": "LeagueGameFinder",
            "seasons": stats,
        },
        "hoopr": {
            "repository": snapshot.get("repository"),
            "commit": snapshot.get("commit"),
            "seasons": hoopr_seasons,
        },
        "basketball_reference": {
            "pages": bref_pages,
            "historical_boxscores": historical_boxscores,
        },
        "legacy": {"files": legacy_files},
    }


def _reference_provenance(data_dir: Path) -> dict:
    reference_dir = data_dir / "reference"
    return {
        _relative_path(reference_dir / name, data_dir): _sha256(
            reference_dir / name
        )
        for name in MODERN_REFERENCE_FILES
    }


def _copy_independent_publications(
    output_dir: Path,
    stage_dir: Path,
) -> set[str]:
    with publication.publication_lock(output_dir, exclusive=False):
        return _copy_independent_publications_locked(
            output_dir, stage_dir
        )


def _write_preserved_product(
    payload: bytes, destination: Path, *, mode: int
) -> None:
    destination_fd = os.open(
        destination,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        os.fchmod(destination_fd, mode)
        with os.fdopen(os.dup(destination_fd), "wb") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
    finally:
        os.close(destination_fd)


def _copy_independent_publications_locked(
    output_dir: Path,
    stage_dir: Path,
) -> set[str]:
    """Copy coherent independent pairs while the publication lock is held."""
    if not output_dir.exists():
        return set()
    try:
        output_fd = os.open(
            output_dir,
            os.O_RDONLY
            | os.O_DIRECTORY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise ValidationError(
            "modern publication output must be a regular directory"
        ) from exc
    preserved = set()
    created = []
    try:
        for label, products in PRESERVED_PUBLICATION_PAIRS.items():
            observed = {}
            for name in products:
                try:
                    observed[name] = os.stat(
                        name,
                        dir_fd=output_fd,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    observed[name] = None
            present = {
                name for name, value in observed.items()
                if value is not None
            }
            if present and present != set(products):
                raise ValidationError(
                    f"{label} publication pair is incomplete"
                )
            if not present:
                continue
            if any(
                not stat.S_ISREG(observed[name].st_mode)
                for name in products
                if observed[name] is not None
            ):
                raise ValidationError(
                    f"non-regular {label} publication product"
                )
            identities = {
                name: (
                    observed[name].st_dev,
                    observed[name].st_ino,
                    observed[name].st_mode,
                )
                for name in products
                if observed[name] is not None
            }
            source_fds = {}
            try:
                for name in products:
                    source_fd = os.open(
                        name,
                        os.O_RDONLY
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=output_fd,
                    )
                    source_fds[name] = source_fd
                    value = os.fstat(source_fd)
                    identity = (value.st_dev, value.st_ino, value.st_mode)
                    if identity != identities[name]:
                        raise ValidationError(
                            f"{label} publication changed during preservation"
                        )
                payloads = {}
                for name in products:
                    with os.fdopen(
                        os.dup(source_fds[name]), "rb"
                    ) as source:
                        payload = source.read()
                    payloads[name] = payload
                    destination = stage_dir / name
                    _write_preserved_product(
                        payload,
                        destination,
                        mode=stat.S_IMODE(identities[name][2]),
                    )
                    created.append(destination)
                for name in products:
                    value = os.fstat(source_fds[name])
                    identity = (value.st_dev, value.st_ino, value.st_mode)
                    try:
                        path_value = os.stat(
                            name,
                            dir_fd=output_fd,
                            follow_symlinks=False,
                        )
                    except FileNotFoundError as exc:
                        raise ValidationError(
                            f"{label} publication changed during preservation"
                        ) from exc
                    path_identity = (
                        path_value.st_dev,
                        path_value.st_ino,
                        path_value.st_mode,
                    )
                    os.lseek(source_fds[name], 0, os.SEEK_SET)
                    with os.fdopen(
                        os.dup(source_fds[name]), "rb"
                    ) as source:
                        rebound = source.read()
                    if (
                        identity != identities[name]
                        or path_identity != identities[name]
                        or rebound != payloads[name]
                    ):
                        raise ValidationError(
                            f"{label} publication changed during preservation"
                        )
                parquet_name, manifest_name = products
                try:
                    manifest = json.loads(
                        payloads[manifest_name].decode("utf-8")
                    )
                    expected_sha256 = manifest["tables"][
                        Path(parquet_name).stem
                    ]["sha256"]
                except (
                    KeyError,
                    TypeError,
                    UnicodeDecodeError,
                    json.JSONDecodeError,
                ) as exc:
                    raise ValidationError(
                        f"{label} manifest does not bind preserved Parquet"
                    ) from exc
                actual_sha256 = hashlib.sha256(
                    payloads[parquet_name]
                ).hexdigest()
                if (
                    not isinstance(expected_sha256, str)
                    or expected_sha256 != actual_sha256
                ):
                    raise ValidationError(
                        f"{label} manifest does not bind preserved Parquet"
                    )
                preserved.update(products)
            finally:
                for source_fd in source_fds.values():
                    os.close(source_fd)
        return preserved
    except (OSError, ValidationError):
        for path in created:
            path.unlink(missing_ok=True)
        raise
    finally:
        os.close(output_fd)


def _dependency_versions(project_dir: Path) -> dict:
    versions = {}
    requirements = project_dir / "requirements.in"
    if requirements.exists():
        for line in requirements.read_text(encoding="utf-8").splitlines():
            requirement = line.split("#", 1)[0].strip()
            if not requirement:
                continue
            match = re.match(r"([A-Za-z0-9_.-]+)", requirement)
            if match is None:
                continue
            name = match.group(1)
            try:
                versions[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError as exc:
                raise ValidationError(
                    f"direct dependency is not installed: {name}"
                ) from exc
    return {
        "python": platform.python_version(),
        "dependencies": dict(sorted(versions.items())),
    }


def _load_franchise_dimension(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype="string")
    if frame.columns.tolist() != FRANCHISE_COLUMNS:
        raise ValidationError(
            f"franchise columns must be {FRANCHISE_COLUMNS}"
        )
    for column in ("first_season", "last_season"):
        frame[column] = pd.to_numeric(
            frame[column], errors="raise"
        ).astype("Int64")
    frame = frame.astype(FRANCHISE_DTYPES)
    return frame.sort_values(
        ["franchise_id", "first_season", "last_season", "alias"],
        na_position="first",
        kind="stable",
    ).reset_index(drop=True)


def _load_venue_dimension(path: Path) -> pd.DataFrame:
    frame = load_venue_capacities(path).astype(VENUE_DTYPES)
    return frame.sort_values(
        ["venue_id", "franchise_id", "first_season", "last_season"],
        kind="stable",
    ).reset_index(drop=True)


def _assert_staged_frame(
    actual: pd.DataFrame,
    expected: pd.DataFrame,
    table: str,
) -> None:
    if actual.columns.tolist() != expected.columns.tolist():
        raise ValidationError(
            f"staged {table} columns changed after Parquet re-read"
        )
    if actual.dtypes.astype(str).to_dict() != (
        expected.dtypes.astype(str).to_dict()
    ):
        raise ValidationError(
            f"staged {table} dtypes changed after Parquet re-read"
        )
    if len(actual) != len(expected):
        raise ValidationError(
            f"staged {table} rows: {len(actual)}, expected {len(expected)}"
        )


def _table_manifest(path: Path, frame: pd.DataFrame) -> dict:
    table = {
        "rows": len(frame),
        "sha256": _sha256(path),
    }
    if "season" in frame:
        table["min_season"] = int(frame["season"].min())
        table["max_season"] = int(frame["season"].max())
    else:
        bounds = pd.concat(
            [
                frame.get("first_season", pd.Series(dtype="Int64")),
                frame.get("last_season", pd.Series(dtype="Int64")),
            ]
        ).dropna()
        table["min_season"] = int(bounds.min()) if len(bounds) else None
        table["max_season"] = int(bounds.max()) if len(bounds) else None
    return table


def _publish_stage(stage_dir: Path, output_dir: Path) -> None:
    """Atomically swap the stage, restoring or retaining the prior directory."""
    rollback_dir = Path(
        tempfile.mkdtemp(
            prefix=".parquet-rollback-",
            dir=output_dir.parent,
        )
    )
    rollback_dir.rmdir()
    prior_exists = output_dir.exists()
    if prior_exists:
        os.replace(output_dir, rollback_dir)
    try:
        os.replace(stage_dir, output_dir)
    except BaseException:
        if prior_exists and rollback_dir.exists():
            os.replace(rollback_dir, output_dir)
        raise
    if rollback_dir.exists():
        try:
            shutil.rmtree(rollback_dir)
        except OSError:
            # The atomic stage swap already committed a complete publication.
            # Retaining a complete rollback is safer than reporting failure
            # while the new publication is live.
            pass


def run(data_dir: Path) -> None:
    """Build, validate, stage, and publish all deterministic data products."""
    team_games, games, report = assemble(data_dir / "raw")
    _validate_runtime_reference_copies(data_dir)
    franchises_frame = _load_franchise_dimension(
        data_dir / "reference/franchises.csv"
    )
    venues_frame = _load_venue_dimension(
        data_dir / "reference/venue_capacities.csv"
    )
    frames = {
        "team_games": team_games,
        "games": games,
        "franchises": franchises_frame,
        "venues": venues_frame,
    }

    data_dir.mkdir(parents=True, exist_ok=True)
    stage_dir = Path(
        tempfile.mkdtemp(prefix=".parquet-stage-", dir=data_dir)
    )
    try:
        (stage_dir / ".gitkeep").write_bytes(b"\n")
        for table, frame in frames.items():
            frame.to_parquet(
                stage_dir / f"{table}.parquet",
                engine="pyarrow",
                compression="zstd",
                index=False,
            )

        reread = {
            table: pd.read_parquet(stage_dir / f"{table}.parquet")
            for table in frames
        }
        for table, expected in frames.items():
            _assert_staged_frame(reread[table], expected, table)
        reread_report = validate(
            reread["team_games"],
            reread["games"],
            expected_counts=stats_nba.EXPECTED_GAMES,
            expected_legacy=EXPECTED_LEGACY_GAMES,
        )
        reread_report["schedule_exclusions"] = report[
            "schedule_exclusions"
        ]
        if reread_report != report:
            raise ValidationError(
                "staged validation report changed after Parquet re-read"
            )

        manifest = {
            "schema_version": 1,
            "sources": _raw_provenance(data_dir),
            "references": _reference_provenance(data_dir),
            "versions": _dependency_versions(data_dir.parent),
            "tables": {
                table: _table_manifest(
                    stage_dir / f"{table}.parquet",
                    reread[table],
                )
                for table in frames
            },
            "validation": reread_report,
        }
        (stage_dir / "build_manifest.json").write_text(
            json.dumps(
                manifest,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        output_dir = data_dir / "parquet"
        with publication.publication_lock(
            output_dir, exclusive=True
        ):
            preserved = _copy_independent_publications_locked(
                output_dir, stage_dir
            )
            staged_products = {
                path.name
                for path in stage_dir.iterdir()
                if not path.name.startswith(".")
            }
            if staged_products != set(PUBLISHED_FILES) | preserved:
                raise ValidationError(
                    "staged publication file set is incomplete"
                )
            _publish_stage(stage_dir, output_dir)
    finally:
        if stage_dir.exists():
            shutil.rmtree(stage_dir)
