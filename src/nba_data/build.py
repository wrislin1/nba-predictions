"""Assemble and validate the canonical game and team-game panels."""

from pathlib import Path

import pandas as pd

from nba_data import bref, franchises, hoopr, legacy, stats_nba


JOIN_KEY = [
    "season",
    "game_date",
    "home_franchise_id",
    "away_franchise_id",
]
EXPECTED_LEGACY_GAMES = 40_828
BOX_STATS = [
    "pts",
    "fgm",
    "fga",
    "fg3m",
    "fg3a",
    "ftm",
    "fta",
    "oreb",
    "dreb",
    "reb",
    "ast",
    "stl",
    "blk",
    "tov",
    "pf",
]
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
]


class ValidationError(ValueError):
    """The assembled panel failed one or more publication gates."""


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


def enrich_modern_games(
    nba_games: pd.DataFrame,
    schedule: pd.DataFrame,
    bref: pd.DataFrame,
) -> pd.DataFrame:
    """Add hoopR venue metadata and b-ref-first attendance to NBA games."""
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


def validate(
    team_games: pd.DataFrame,
    games: pd.DataFrame,
    expected_counts: dict[int, int],
    expected_legacy: int,
) -> dict:
    """Validate publication invariants and return a deterministic coverage report."""
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
    agreement = {}
    for season in expected_counts:
        modern = games[games["season"].eq(season)]
        bref_join_coverage[season] = (
            float(modern["bref_row_matched"].mean()) if len(modern) else 0.0
        )
        selected_coverage[season] = (
            float(modern["attendance"].notna().mean()) if len(modern) else 0.0
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

    bad_zero = games["attendance_status"].eq("reported_zero") & ~games[
        "season"
    ].between(2020, 2022)
    if bad_zero.any():
        errors.append(
            "reported_zero outside 2020..2022: "
            f"{games.loc[bad_zero, 'game_id'].head().tolist()}"
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

    if "capacity" in games:
        missing_capacity = (
            games["season"].between(2020, 2022)
            & ~games["neutral_site"]
            & games["capacity"].isna()
        )
        if missing_capacity.any():
            errors.append(
                "non-neutral 2020..2022 venue missing capacity: "
                f"{games.loc[missing_capacity, 'game_id'].head().tolist()}"
            )

    if errors:
        raise ValidationError("; ".join(errors))
    return report


def assemble(raw_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Load, enrich, combine, and validate the full legacy and modern panels."""
    legacy_path = (
        raw_dir / "legacy" / "nba-teams-1984-2019-ordered.csv"
    )
    legacy_team_games = legacy.load_team_games(legacy_path)
    legacy_games = pair_team_games(legacy_team_games).merge(
        legacy.load_attendance(legacy_path),
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
    for season in stats_nba.SEASONS:
        season_team_games = stats_nba.load_season(raw_dir, season)
        schedule, _ = hoopr.load_season(raw_dir, season)
        attendance = bref.load_attendance(raw_dir, season)
        modern_team_frames.append(season_team_games)
        modern_game_frames.append(
            enrich_modern_games(
                pair_team_games(season_team_games),
                schedule,
                attendance,
            )
        )

    team_games = pd.concat(
        [legacy_team_games, *modern_team_frames],
        ignore_index=True,
    ).sort_values(
        ["season", "game_date", "game_id", "is_home"],
        ascending=[True, True, True, False],
        kind="stable",
    ).reset_index(drop=True)
    games = pd.concat(
        [legacy_games, *modern_game_frames],
        ignore_index=True,
    )[GAME_COLUMNS].sort_values(
        ["season", "game_date", "game_id"],
        kind="stable",
    ).reset_index(drop=True)
    report = validate(
        team_games,
        games,
        expected_counts=stats_nba.EXPECTED_GAMES,
        expected_legacy=EXPECTED_LEGACY_GAMES,
    )
    return team_games, games, report


def write_venue_inventory(data_dir: Path) -> Path:
    """Write the venue-era observations needed to curate capacity data."""
    _, games, _ = assemble(data_dir / "raw")
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
    destination = data_dir / "raw/reports/venue_inventory.csv"
    destination.parent.mkdir(parents=True, exist_ok=True)
    output.sort_values(
        ["home_franchise_id", "first_season", "venue_id"]
    ).to_csv(destination, index=False)
    return destination
