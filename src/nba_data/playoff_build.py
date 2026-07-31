"""Typed playoff reference loaders and authoritative site rules."""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd


COUNT_COLUMNS = [
    "season",
    "playoff_games",
    "play_in_games",
    "tiebreaker_games",
    "voided_games",
    "total_games",
    "first_postseason_date",
    "last_postseason_date",
    "source_url",
    "note",
]
SITE_RULE_COLUMNS = [
    "season",
    "site_status",
    "expected_matches",
    "source_url",
    "note",
]
TYPE_COUNT_COLUMNS = [
    "playoff_games",
    "play_in_games",
    "tiebreaker_games",
    "voided_games",
]
FIXED_TYPE_COUNTS = {
    1948: {"tiebreaker_games": 2},
    1950: {"tiebreaker_games": 2},
    1953: {"voided_games": 1},
    1956: {"tiebreaker_games": 2},
    1957: {"tiebreaker_games": 2},
    2020: {"play_in_games": 1},
    **{
        season: {"play_in_games": 6}
        for season in range(2021, 2027)
    },
}


class PlayoffValidationError(ValueError):
    """Raised when a playoff reference or site rule is invalid."""


@dataclass(frozen=True)
class ExpectedSeason:
    season: int
    playoff_games: int
    play_in_games: int
    tiebreaker_games: int
    voided_games: int
    total_games: int
    first_postseason_date: date
    last_postseason_date: date
    source_url: str
    note: str


@dataclass(frozen=True)
class SiteRule:
    season: int
    site_status: str
    expected_matches: int
    source_url: str
    note: str


def _read_exact_csv(
    path: Path, columns: list[str], label: str
) -> pd.DataFrame:
    try:
        frame = pd.read_csv(path, dtype="string")
    except (OSError, pd.errors.ParserError) as error:
        raise PlayoffValidationError(
            f"could not read {label}: {error}"
        ) from error
    if frame.columns.tolist() != columns:
        raise PlayoffValidationError(f"{label} columns must be {columns}")
    return frame


def _parse_integer_columns(
    frame: pd.DataFrame, columns: list[str], label: str
) -> pd.DataFrame:
    parsed = frame.copy()
    try:
        for column in columns:
            values = pd.to_numeric(parsed[column], errors="raise")
            if values.isna().any() or values.mod(1).ne(0).any():
                raise ValueError(f"{column} contains a non-integer")
            parsed[column] = values.astype("int64")
    except (TypeError, ValueError) as error:
        raise PlayoffValidationError(label) from error
    return parsed


def _has_blank(series: pd.Series) -> bool:
    return bool(series.isna().any() or series.str.strip().eq("").any())


def load_expected_counts(path: Path) -> dict[int, ExpectedSeason]:
    """Load the exact independently curated 1947--2026 ledger."""
    frame = _read_exact_csv(path, COUNT_COLUMNS, "count")
    if _has_blank(frame["source_url"]) or _has_blank(frame["note"]):
        raise PlayoffValidationError(
            "counts require nonblank source URL and note"
        )
    if not frame["source_url"].str.startswith("https://").all():
        raise PlayoffValidationError("count source URLs must use HTTPS")

    numeric_columns = ["season", *TYPE_COUNT_COLUMNS, "total_games"]
    frame = _parse_integer_columns(
        frame, numeric_columns, "count fields must be integers"
    )
    if (
        len(frame) != 80
        or frame["season"].duplicated(keep=False).any()
        or frame["season"].tolist() != list(range(1947, 2027))
    ):
        raise PlayoffValidationError(
            "counts must cover exactly 1947..2026 in ascending order"
        )
    if frame[TYPE_COUNT_COLUMNS].lt(0).any().any():
        raise PlayoffValidationError("type counts cannot be negative")
    if (
        frame["playoff_games"].le(0).any()
        or frame["total_games"].le(0).any()
    ):
        raise PlayoffValidationError(
            "playoff_games and total_games must be positive"
        )
    if not frame[TYPE_COUNT_COLUMNS].sum(axis=1).equals(
        frame["total_games"]
    ):
        raise PlayoffValidationError(
            "type counts must sum to total_games"
        )

    for season in range(1947, 2027):
        fixed = FIXED_TYPE_COUNTS.get(season, {})
        row = frame.loc[frame["season"].eq(season)].iloc[0]
        for column in [
            "play_in_games",
            "tiebreaker_games",
            "voided_games",
        ]:
            expected_value = fixed.get(column, 0)
            if int(row[column]) != expected_value:
                raise PlayoffValidationError(
                    f"{season} {column} must be {expected_value}"
                )

    first_dates: list[date] = []
    last_dates: list[date] = []
    try:
        for first_value, last_value in zip(
            frame["first_postseason_date"],
            frame["last_postseason_date"],
            strict=True,
        ):
            first_dates.append(date.fromisoformat(first_value))
            last_dates.append(date.fromisoformat(last_value))
    except (TypeError, ValueError) as error:
        raise PlayoffValidationError(
            "postseason dates must use ISO format"
        ) from error
    if any(
        first > last
        for first, last in zip(first_dates, last_dates, strict=True)
    ):
        raise PlayoffValidationError(
            "first_postseason_date must not follow last_postseason_date"
        )

    expected: dict[int, ExpectedSeason] = {}
    for row, first, last in zip(
        frame.to_dict("records"), first_dates, last_dates, strict=True
    ):
        season = int(row["season"])
        expected[season] = ExpectedSeason(
            season=season,
            playoff_games=int(row["playoff_games"]),
            play_in_games=int(row["play_in_games"]),
            tiebreaker_games=int(row["tiebreaker_games"]),
            voided_games=int(row["voided_games"]),
            total_games=int(row["total_games"]),
            first_postseason_date=first,
            last_postseason_date=last,
            source_url=str(row["source_url"]),
            note=str(row["note"]),
        )
    return expected


def load_site_rules(path: Path) -> dict[int, SiteRule]:
    """Load the single approved 2020 neutral-site rule."""
    frame = _read_exact_csv(path, SITE_RULE_COLUMNS, "site rule")
    if len(frame) != 1:
        raise PlayoffValidationError(
            "site rules must contain exactly one row"
        )
    if _has_blank(frame["source_url"]) or _has_blank(frame["note"]):
        raise PlayoffValidationError(
            "site rules require nonblank source URL and note"
        )
    if _has_blank(frame["site_status"]):
        raise PlayoffValidationError(
            "site rule status must be neutral"
        )
    if not frame["source_url"].str.startswith("https://").all():
        raise PlayoffValidationError(
            "site rule source URLs must use HTTPS"
        )
    frame = _parse_integer_columns(
        frame,
        ["season", "expected_matches"],
        "site rule numeric fields must be integers",
    )
    row = frame.iloc[0]
    if int(row["season"]) != 2020:
        raise PlayoffValidationError(
            "site rules must contain only season 2020"
        )
    if row["site_status"] != "neutral":
        raise PlayoffValidationError("site rule status must be neutral")
    if int(row["expected_matches"]) != 84:
        raise PlayoffValidationError(
            "2020 site rule must match 84 games"
        )

    rule = SiteRule(
        season=2020,
        site_status="neutral",
        expected_matches=84,
        source_url=str(row["source_url"]),
        note=str(row["note"]),
    )
    return {rule.season: rule}


def apply_site_rules(
    games: pd.DataFrame, rules: dict[int, SiteRule]
) -> tuple[pd.DataFrame, dict[int, int]]:
    """Apply authoritative season-wide site classifications."""
    if "season" not in games or "site_status" not in games:
        raise PlayoffValidationError(
            "games require season and site_status columns"
        )
    output = games.copy()
    matches = {}
    for season, rule in sorted(rules.items()):
        mask = output["season"].eq(season)
        actual = int(mask.sum())
        if actual != rule.expected_matches:
            raise PlayoffValidationError(
                f"site rule {season} matched {actual}, "
                f"expected {rule.expected_matches}"
            )
        statuses = output.loc[mask, "site_status"]
        if (
            statuses.isna().any()
            or not statuses.isin({"home", "neutral", "unknown"}).all()
        ):
            raise PlayoffValidationError(
                f"site rule {season} selected invalid site_status"
            )
        output.loc[mask, "site_status"] = rule.site_status
        matches[season] = actual
    return output, matches
