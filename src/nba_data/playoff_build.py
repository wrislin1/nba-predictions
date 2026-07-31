"""Typed playoff reference loaders and authoritative site rules."""

from dataclasses import dataclass
from datetime import date
import hashlib
from pathlib import Path

import pandas as pd

from nba_data import bref_schedule, franchises, hoopr, playoff_bref


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
PLAYOFF_COLUMNS = [
    "game_id",
    "season",
    "league",
    "game_date",
    "game_type",
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
PLAYOFF_DTYPES = {
    "game_id": "string",
    "season": "int64",
    "league": "string",
    "game_type": "string",
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
CROSS_SOURCE_KEY = [
    "season",
    "game_date",
    "game_type",
    "away_franchise_id",
    "home_franchise_id",
    "away_pts",
    "home_pts",
]
REGULAR_OVERLAP_KEY = [
    "season",
    "game_date",
    "away_franchise_id",
    "home_franchise_id",
    "away_pts",
    "home_pts",
]
_EXPECTED_GAME_TYPES = {"playoff", "play_in", "tiebreaker", "voided"}
_TYPE_TO_COUNT_FIELD = {
    "playoff": "playoff_games",
    "play_in": "play_in_games",
    "tiebreaker": "tiebreaker_games",
    "voided": "voided_games",
}
_VOIDED_IDENTITY = {
    "game_id": "bref-195211280MLH",
    "season": 1953,
    "game_date": date(1952, 11, 28),
    "game_type": "voided",
}
_SOURCE_INTEGER_COLUMNS = [
    "season",
    "away_pts",
    "home_pts",
    "overtime_periods",
]


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


def _require_source_integers(
    frame: pd.DataFrame, columns: list[str]
) -> None:
    invalid: dict[str, list] = {}
    for column in columns:
        values = frame[column]
        boolean = values.map(
            lambda value: (
                False if pd.isna(value)
                else pd.api.types.is_bool(value)
            )
        )
        try:
            numeric = pd.to_numeric(values, errors="raise")
            malformed = (
                boolean
                | numeric.isna()
                | numeric.mod(1).ne(0)
            )
        except (TypeError, ValueError):
            malformed = pd.Series(True, index=values.index)
        if bool(malformed.any()):
            invalid[column] = values.loc[malformed].head(3).tolist()
    if invalid:
        raise PlayoffValidationError(
            f"source integer values are malformed: {invalid}"
        )


def _enforce_schema(frame: pd.DataFrame) -> pd.DataFrame:
    """Publish the exact ordered and typed playoff-game contract."""
    output = frame.loc[:, PLAYOFF_COLUMNS].copy()
    _require_source_integers(
        output,
        [*_SOURCE_INTEGER_COLUMNS, "home_margin"],
    )
    output["game_date"] = pd.to_datetime(
        output["game_date"], errors="raise"
    ).dt.date
    for column, dtype in PLAYOFF_DTYPES.items():
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


def _has_any(values: pd.Series | pd.DataFrame) -> bool:
    result = values.any()
    if isinstance(result, pd.Series):
        return bool(result.fillna(False).any())
    return bool(False if pd.isna(result) else result)


def _normalize_regular_keys(frame: pd.DataFrame) -> pd.DataFrame:
    missing = [
        column for column in REGULAR_OVERLAP_KEY
        if column not in frame.columns
    ]
    if missing:
        raise PlayoffValidationError(
            f"regular publication missing overlap columns: {missing}"
        )
    normalized = frame.loc[:, REGULAR_OVERLAP_KEY].copy()
    normalized["season"] = pd.to_numeric(
        normalized["season"], errors="coerce"
    )
    normalized["game_date"] = pd.to_datetime(
        normalized["game_date"], errors="coerce"
    ).dt.date
    for column in ["away_pts", "home_pts"]:
        normalized[column] = pd.to_numeric(
            normalized[column], errors="coerce"
        )
    return normalized


def _mismatch_sample(frame: pd.DataFrame, side: str) -> list[dict]:
    rows = frame.loc[frame["_merge"].eq(side), CROSS_SOURCE_KEY].copy()
    if rows.empty:
        return []
    rows = rows.sort_values(
        [column for column in CROSS_SOURCE_KEY if column in rows],
        kind="stable",
    )
    return rows.head(3).to_dict("records")


def validate(
    games: pd.DataFrame,
    expected_counts: dict[int, ExpectedSeason],
    *,
    evidence: pd.DataFrame,
    hoopr_games: pd.DataFrame,
    modern_regular: pd.DataFrame,
    historical_regular: pd.DataFrame,
    site_rule_matches: dict[int, int],
) -> dict:
    """Enforce all playoff publication gates and return an audit report."""
    errors: list[str] = []
    unique_columns = games.loc[
        :, ~games.columns.duplicated(keep="first")
    ]
    schema_ok = (
        not games.columns.has_duplicates
        and games.columns.tolist() == PLAYOFF_COLUMNS
        and str(games["game_date"].dtype) == "object"
        and all(
            column in games
            and str(games[column].dtype) == dtype
            for column, dtype in PLAYOFF_DTYPES.items()
        )
    )
    if not schema_ok:
        errors.append("playoff columns and dtypes differ from publication schema")

    frame = unique_columns.reindex(columns=PLAYOFF_COLUMNS).copy()
    for column in [
        "season",
        "away_pts",
        "home_pts",
        "home_margin",
        "overtime_periods",
        "attendance",
    ]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    expected_seasons = list(range(1947, 2027))
    actual_seasons = sorted(
        int(value) for value in frame["season"].dropna().unique()
        if float(value).is_integer()
    )
    if actual_seasons != expected_seasons:
        errors.append("seasons must be exactly 1947..2026")

    expected_league = frame["season"].map(
        lambda season: (
            pd.NA if pd.isna(season)
            else "BAA" if season <= 1949 else "NBA"
        )
    )
    if _has_any(
        frame["league"].astype("string")
        != expected_league.astype("string")
    ):
        errors.append("league/season mismatch")

    if _has_any(
        ~frame["game_type"].isin(_EXPECTED_GAME_TYPES)
        | frame["game_type"].isna()
    ):
        errors.append("unsupported game_type")

    if (
        _has_any(frame["game_id"].isna())
        or not frame["game_id"].dropna().is_unique
    ):
        errors.append("game_id must be globally unique and non-null")

    nullable = {"venue_name", "attendance", "boxscore_url"}
    required = [
        column for column in PLAYOFF_COLUMNS if column not in nullable
    ]
    if any(_has_any(frame[column].isna()) for column in required):
        errors.append("required playoff value is null")
    if not frame["game_date"].dropna().map(
        lambda value: type(value) is date
    ).all():
        errors.append("game_date values must be datetime.date")

    same_source_team = frame["away_team_name"].eq(
        frame["home_team_name"]
    ).fillna(False)
    same_franchise = frame["away_franchise_id"].eq(
        frame["home_franchise_id"]
    ).fillna(False)
    if _has_any(same_source_team | same_franchise):
        errors.append("teams and franchises must differ")

    away_scores = frame["away_pts"]
    home_scores = frame["home_pts"]
    noninteger_scores = (
        away_scores.dropna().mod(1).ne(0).any()
        or home_scores.dropna().mod(1).ne(0).any()
    )
    if (
        noninteger_scores
        or _has_any(away_scores.lt(0) | home_scores.lt(0))
    ):
        errors.append("scores must be nonnegative integers")
    if _has_any(away_scores.eq(home_scores).fillna(False)):
        errors.append("final scores must be non-tied")
    if _has_any(
        frame["home_margin"].ne(home_scores - away_scores).fillna(False)
    ):
        errors.append("home_margin mismatch")

    overtime = frame["overtime_periods"]
    if (
        overtime.dropna().mod(1).ne(0).any()
        or _has_any(overtime.lt(0))
    ):
        errors.append("overtime periods must be nonnegative integers")

    evidence_ok = (
        not evidence.columns.has_duplicates
        and {"game_id", "overtime_marker"} <= set(evidence.columns)
    )
    if evidence_ok:
        overtime_evidence = evidence.loc[
            :, ["game_id", "overtime_marker"]
        ].copy()
        aligned = (
            overtime_evidence["game_id"].notna().all()
            and overtime_evidence["game_id"].is_unique
            and frame["game_id"].notna().all()
            and frame["game_id"].is_unique
            and len(overtime_evidence) == len(frame)
            and set(overtime_evidence["game_id"])
            == set(frame["game_id"])
        )
        if aligned:
            decoded = []
            for marker in overtime_evidence["overtime_marker"]:
                try:
                    decoded.append(
                        bref_schedule.overtime_periods(str(marker))
                    )
                except (TypeError, ValueError):
                    decoded.append(pd.NA)
            decoded_by_id = pd.Series(
                decoded,
                index=overtime_evidence["game_id"],
                dtype="Int64",
            )
            published = frame["game_id"].map(decoded_by_id)
            if _has_any(
                published.ne(frame["overtime_periods"]).fillna(True)
            ):
                errors.append("overtime marker mismatch")
        else:
            errors.append("overtime marker mismatch: evidence alignment")
    else:
        errors.append("overtime marker mismatch: evidence columns")

    missing_attendance = frame["attendance"].isna()
    zero_attendance = frame["attendance"].eq(0).fillna(False)
    positive_attendance = frame["attendance"].gt(0).fillna(False)
    attendance_ok = (
        (
            missing_attendance
            & frame["attendance_status"].eq("missing")
        )
        | (
            zero_attendance
            & frame["attendance_status"].eq("reported_zero")
        )
        | (
            positive_attendance
            & frame["attendance_status"].eq("reported_positive")
        )
    )
    if _has_any(~attendance_ok.fillna(False)):
        errors.append("attendance/status mismatch")

    valid_sites = {"home", "neutral", "unknown"}
    if _has_any(
        ~frame["site_status"].isin(valid_sites)
        | frame["site_status"].isna()
    ):
        errors.append("unsupported site_status")

    actual_counts: dict[int, dict[str, int]] = {}
    for season in expected_seasons:
        season_rows = frame.loc[frame["season"].eq(season)]
        type_counts = season_rows["game_type"].value_counts()
        actual = {
            game_type: int(type_counts.get(game_type, 0))
            for game_type in _TYPE_TO_COUNT_FIELD
        }
        actual_counts[season] = actual
        expected = expected_counts.get(season)
        if expected is None or (
            any(
                actual[game_type]
                != getattr(expected, field)
                for game_type, field in _TYPE_TO_COUNT_FIELD.items()
            )
            or len(season_rows) != expected.total_games
        ):
            errors.append(
                f"season/type count mismatch for {season}: actual={actual}"
            )

    outside_window = []
    for row in frame.loc[
        frame["game_type"].isin(
            {"playoff", "play_in", "tiebreaker"}
        ),
        ["game_id", "season", "game_date", "game_type"],
    ].itertuples(index=False):
        expected = expected_counts.get(row.season)
        if (
            expected is None
            or type(row.game_date) is not date
            or not (
                expected.first_postseason_date
                <= row.game_date
                <= expected.last_postseason_date
            )
        ):
            outside_window.append(row.game_id)
    if outside_window:
        errors.append(
            "outside curated postseason window: "
            f"{sorted(str(value) for value in outside_window)[:3]}"
        )

    voided = frame.loc[
        frame["game_type"].eq("voided"),
        list(_VOIDED_IDENTITY),
    ]
    valid_voided = (
        len(voided) == 1
        and voided.iloc[0].to_dict() == _VOIDED_IDENTITY
    )
    if not valid_voided:
        errors.append("invalid voided-game exemption")

    overlap_rows = 0
    try:
        regular_keys = pd.concat(
            [
                _normalize_regular_keys(modern_regular),
                _normalize_regular_keys(historical_regular),
            ],
            ignore_index=True,
        ).drop_duplicates()
        playoff_keys = _normalize_regular_keys(frame)
        overlap = playoff_keys.merge(
            regular_keys,
            on=REGULAR_OVERLAP_KEY,
            how="inner",
        )
        overlap_rows = len(overlap)
        if overlap_rows:
            errors.append(
                f"{overlap_rows} playoff rows overlaps regular publication"
            )
    except (KeyError, TypeError, ValueError) as error:
        errors.append(f"regular overlap validation failed: {error}")

    season_2020 = frame["season"].eq(2020)
    neutral_2020_rows = int(
        (season_2020 & frame["site_status"].eq("neutral")).sum()
    )
    if (
        site_rule_matches != {2020: 84}
        or int(season_2020.sum()) != 84
        or neutral_2020_rows != 84
    ):
        errors.append(
            "2020 neutral-site rule mismatch: "
            f"matches={site_rule_matches}, rows={int(season_2020.sum())}, "
            f"neutral={neutral_2020_rows}"
        )

    matched_rows = 0
    try:
        bref_keys = frame.loc[
            frame["season"].between(2020, 2026)
            & frame["game_type"].isin(["playoff", "play_in"]),
            CROSS_SOURCE_KEY,
        ].copy()
        missing_hoopr = [
            column for column in CROSS_SOURCE_KEY
            if column not in hoopr_games.columns
        ]
        if missing_hoopr:
            raise ValueError(
                f"missing hoopR key columns {missing_hoopr}"
            )
        hoopr_keys = hoopr_games.loc[:, CROSS_SOURCE_KEY].copy()
        if (
            bref_keys.duplicated(CROSS_SOURCE_KEY).any()
            or hoopr_keys.duplicated(CROSS_SOURCE_KEY).any()
        ):
            raise ValueError("duplicate cross-source key")
        comparison = bref_keys.merge(
            hoopr_keys,
            on=CROSS_SOURCE_KEY,
            how="outer",
            indicator=True,
            validate="one_to_one",
        )
        matched_rows = int(comparison["_merge"].eq("both").sum())
        if (
            len(comparison) != 627
            or not comparison["_merge"].eq("both").all()
        ):
            errors.append(
                "BREF/hoopR postseason mismatch: "
                f"left_only={_mismatch_sample(comparison, 'left_only')}, "
                f"right_only={_mismatch_sample(comparison, 'right_only')}"
            )
    except (KeyError, TypeError, ValueError, pd.errors.MergeError) as error:
        errors.append(f"BREF/hoopR postseason mismatch: {error}")

    if (
        not games.columns.has_duplicates
        and games.columns.tolist() == PLAYOFF_COLUMNS
    ):
        sorted_games = games.sort_values(
            ["season", "game_date", "game_id"], kind="stable"
        ).reset_index(drop=True)
        if not sorted_games.equals(games.reset_index(drop=True)):
            errors.append("playoff rows must be deterministically sorted")

    if errors:
        raise PlayoffValidationError("; ".join(errors))

    games_by_season = {
        str(int(season)): int(count)
        for season, count in games.groupby(
            "season", sort=True
        ).size().items()
    }
    games_by_type = {
        str(game_type): int(count)
        for game_type, count in games.groupby(
            "game_type", sort=True
        ).size().items()
    }
    season_sha256 = {
        str(season): _season_sha256(
            games.loc[games["season"].eq(season)]
        )
        for season in expected_seasons
    }
    return {
        "season_range": [1947, 2026],
        "total_games": int(len(games)),
        "games_by_type": games_by_type,
        "games_by_season": games_by_season,
        "cross_source": {
            "season_range": [2020, 2026],
            "expected_rows": 627,
            "matched_rows": matched_rows,
        },
        "regular_overlap_rows": overlap_rows,
        "neutral_2020_rows": neutral_2020_rows,
        "coverage": {
            "boxscore_url": {
                "present": int(games["boxscore_url"].notna().sum()),
                "missing": int(games["boxscore_url"].isna().sum()),
            },
            "venue_name": {
                "present": int(games["venue_name"].notna().sum()),
                "missing": int(games["venue_name"].isna().sum()),
            },
            "attendance": {
                "reported_positive": int(
                    games["attendance_status"].eq(
                        "reported_positive"
                    ).sum()
                ),
                "reported_zero": int(
                    games["attendance_status"].eq(
                        "reported_zero"
                    ).sum()
                ),
                "missing": int(
                    games["attendance_status"].eq("missing").sum()
                ),
            },
            "site_status": {
                status: int(games["site_status"].eq(status).sum())
                for status in ["home", "neutral", "unknown"]
            },
        },
        "season_sha256": season_sha256,
    }


def assemble(data_dir: Path) -> tuple[pd.DataFrame, dict, dict]:
    """Assemble cached BREF playoff rows and all validation evidence."""
    raw_dir = data_dir / "raw"
    frames = [
        playoff_bref.load_season(raw_dir, season)
        for season in range(1947, 2027)
    ]
    source = pd.concat(frames, ignore_index=True)
    _require_source_integers(source, _SOURCE_INTEGER_COLUMNS)
    source["away_franchise_id"] = [
        franchises.resolve(str(name), int(season))
        for name, season in zip(
            source["away_team_name"],
            source["season"],
            strict=True,
        )
    ]
    source["home_franchise_id"] = [
        franchises.resolve(str(name), int(season))
        for name, season in zip(
            source["home_team_name"],
            source["season"],
            strict=True,
        )
    ]
    source, site_rule_matches = apply_site_rules(
        source,
        load_site_rules(
            data_dir / "reference/playoff_site_rules.csv"
        ),
    )
    source["home_margin"] = source["home_pts"] - source["away_pts"]
    source_rows = source[["game_id", "overtime_marker"]].copy()
    games = _enforce_schema(source)

    modern_regular = pd.read_parquet(
        data_dir / "parquet/games.parquet",
        columns=REGULAR_OVERLAP_KEY,
    )
    historical_regular = pd.read_parquet(
        data_dir / "parquet/historical_games.parquet",
        columns=REGULAR_OVERLAP_KEY,
    )
    hoopr_games = pd.concat(
        [
            hoopr.load_postseason(raw_dir, season)
            for season in range(2020, 2027)
        ],
        ignore_index=True,
    )
    expected_counts = load_expected_counts(
        data_dir / "reference/playoff_season_counts.csv"
    )
    report = validate(
        games,
        expected_counts,
        evidence=source_rows,
        hoopr_games=hoopr_games,
        modern_regular=modern_regular,
        historical_regular=historical_regular,
        site_rule_matches=site_rule_matches,
    )
    evidence_bundle = {
        "source_rows": source_rows,
        "hoopr_games": hoopr_games,
        "modern_regular": modern_regular,
        "historical_regular": historical_regular,
        "site_rule_matches": site_rule_matches,
    }
    return games, report, evidence_bundle
