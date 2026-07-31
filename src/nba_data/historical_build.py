"""Validation helpers for the historical publication pipeline."""

from datetime import date
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import Mapping
import warnings

import pandas as pd
import pyarrow.parquet as pq

from nba_data import (
    bref_schedule,
    build,
    cache,
    franchises,
    historical_bref,
    publication,
)


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
HISTORICAL_PRODUCTS = (
    "historical_games.parquet",
    "historical_games_manifest.json",
)
_HISTORICAL_CACHE_NAME = re.compile(
    r"(?P<league>BAA|NBA)_(?P<season>\d{4})_games"
    r"(?:-(?P<month>[a-z]+))?\.html$"
)


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
    output["game_date"] = pd.to_datetime(
        output["game_date"], errors="raise"
    ).dt.date
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
    unique_columns = games.loc[:, ~games.columns.duplicated(keep="first")]
    schema_ok = (
        not games.columns.has_duplicates
        and games.columns.tolist() == HISTORICAL_COLUMNS
        and all(
            column in games and str(games[column].dtype) == dtype
            for column, dtype in HISTORICAL_DTYPES.items()
        )
    )
    if not schema_ok:
        errors.append("historical columns and dtypes differ from publication schema")

    frame = unique_columns.reindex(columns=HISTORICAL_COLUMNS).copy()
    for column in (
        "season", "away_pts", "home_pts", "home_margin",
        "overtime_periods", "attendance",
    ):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    games_by_season = {
        int(season): int(count)
        for season, count in frame.groupby("season", sort=True).size().items()
    }
    if games_by_season != expected_counts:
        errors.append(
            "historical game counts: "
            f"actual={games_by_season}, expected={expected_counts}"
        )

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
    baa_outside_era = (
        frame["league"].eq("BAA")
        & ~frame["season"].between(1947, 1949, inclusive="both")
    )
    nba_outside_era = (
        frame["league"].eq("NBA")
        & ~frame["season"].between(1950, 1983, inclusive="both")
    )
    if _has_any(baa_outside_era | nba_outside_era):
        errors.append("league outside historical era")

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
    if not frame["game_date"].dropna().map(
        lambda value: type(value) is date
    ).all():
        errors.append("non-date game_date")
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
        evidence_columns = [
            "game_id", "overtime_marker", "overtime_periods", "game_remarks",
        ]
        if (
            evidence.columns.has_duplicates
            or not set(evidence_columns) <= set(evidence.columns)
        ):
            errors.append("overtime evidence columns")
        else:
            overtime_evidence = evidence.loc[:, evidence_columns].copy()
            evidence_ids = overtime_evidence["game_id"]
            game_ids = frame["game_id"]
            aligned = (
                evidence_ids.notna().all()
                and game_ids.notna().all()
                and evidence_ids.is_unique
                and game_ids.is_unique
                and len(evidence_ids) == len(game_ids)
                and set(evidence_ids) == set(game_ids)
            )
            if not aligned:
                errors.append("overtime evidence game_id alignment")
            else:
                overtime_evidence["overtime_periods"] = pd.to_numeric(
                    overtime_evidence["overtime_periods"], errors="coerce"
                )
                parsed_periods = []
                for marker in overtime_evidence["overtime_marker"]:
                    try:
                        parsed_periods.append(
                            bref_schedule.overtime_periods(str(marker))
                        )
                    except ValueError:
                        parsed_periods.append(None)
                if _has_any(
                    overtime_evidence["overtime_periods"]
                    != pd.Series(parsed_periods, index=overtime_evidence.index)
                ):
                    errors.append("overtime marker disagreement")
                evidence_periods = overtime_evidence.set_index("game_id")[
                    "overtime_periods"
                ]
                if _has_any(
                    frame["overtime_periods"]
                    != frame["game_id"].map(evidence_periods)
                ):
                    errors.append("overtime periods disagreement")

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


def _cache_error(exc: Exception) -> HistoricalValidationError:
    return HistoricalValidationError(str(exc))


def _raw_provenance_from_payloads(
    payloads: Mapping[str, bytes],
) -> dict:
    """Attest historical cache pairs from immutable captured bytes."""
    prefix = "raw/historical_bref/"
    entries = {
        name.removeprefix(prefix): payload
        for name, payload in payloads.items()
        if name.startswith(prefix)
    }
    if not entries:
        return {"basketball_reference": {"pages": []}}
    bodies = {
        name for name in entries
        if name.endswith(".html")
    }
    sidecars = {
        name.removesuffix(".meta.json")
        for name in entries
        if name.endswith(".html.meta.json")
    }
    if any(
        not (
            name.endswith(".html")
            or name.endswith(".html.meta.json")
        )
        for name in entries
    ):
        raise HistoricalValidationError(
            "unexpected historical cache entry"
        )
    if bodies != sidecars:
        raise HistoricalValidationError("incomplete historical cache pair")
    if any(
        _HISTORICAL_CACHE_NAME.fullmatch(name) is None
        for name in bodies
    ):
        raise HistoricalValidationError(
            "unexpected historical cache entry"
        )

    by_season: dict[int, list[str]] = {}
    for name in sorted(bodies):
        match = _HISTORICAL_CACHE_NAME.fullmatch(name)
        assert match is not None
        season = int(match["season"])
        try:
            expected_league = historical_bref.league_for_season(season)
        except ValueError as exc:
            raise _cache_error(exc) from exc
        if match["league"] != expected_league:
            raise HistoricalValidationError(
                "historical cache metadata mismatch"
            )
        by_season.setdefault(season, []).append(name)

    pages = []
    identities = set()
    urls = set()
    for season, names in sorted(by_season.items()):
        indexes = [name for name in names if "_games-" not in name]
        if len(indexes) != 1:
            raise HistoricalValidationError(
                "historical cache requires exactly one season index"
            )
        index_name = indexes[0]
        try:
            index_metadata = cache.attest_bytes(
                entries[index_name],
                entries[index_name + ".meta.json"],
                lambda value: historical_bref.validate_index_bytes(
                    value, season
                ),
                label=prefix + index_name,
            )
            discovered = historical_bref.discover_month_pages(
                entries[index_name], season
            )
        except (
            cache.CacheIntegrityError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise _cache_error(exc) from exc
        discovered_by_name = {
            page.filename: page for page in discovered
        }
        actual_month_names = {
            name for name in names if name != index_name
        }
        if actual_month_names != set(discovered_by_name):
            raise HistoricalValidationError(
                "historical cache month pages differ from discovered index"
            )

        for name in sorted(names):
            match = _HISTORICAL_CACHE_NAME.fullmatch(name)
            assert match is not None
            month = match["month"]
            is_index = month is None
            url = (
                historical_bref.index_url(season)
                if is_index else discovered_by_name[name].url
            )
            resource = (
                "historical_schedule_index"
                if is_index else "historical_schedule_month"
            )
            try:
                metadata = (
                    index_metadata if is_index else cache.attest_bytes(
                        entries[name],
                        entries[name + ".meta.json"],
                        lambda value, page_url=url: (
                            historical_bref.validate_month_bytes(
                                value, season, page_url
                            )
                        ),
                        label=prefix + name,
                    )
                )
            except (
                cache.CacheIntegrityError,
                ValueError,
                json.JSONDecodeError,
            ) as exc:
                raise _cache_error(exc) from exc
            expected = {
                "provider": "basketball_reference",
                "resource": resource,
                "league": match["league"],
                "season": season,
                "month": month,
                "url": url,
            }
            observed = {
                key: metadata.get(key) for key in expected
            }
            if observed != expected or any(
                type(observed[key]) is not type(expected[key])
                for key in expected
            ):
                raise HistoricalValidationError(
                    "historical cache metadata mismatch"
                )
            identity = (
                metadata.get("resource"),
                metadata.get("league"),
                metadata.get("season"),
                metadata.get("month"),
            )
            if identity in identities or metadata["url"] in urls:
                raise HistoricalValidationError(
                    "duplicate historical cache identity"
                )
            identities.add(identity)
            urls.add(metadata["url"])
            pages.append({
                "path": prefix + name,
                "provider": metadata["provider"],
                "resource": metadata["resource"],
                "league": metadata["league"],
                "season": metadata["season"],
                "month": metadata.get("month"),
                "url": metadata["url"],
                "sha256": metadata["sha256"],
            })
    return {"basketball_reference": {"pages": pages}}


def _raw_provenance(data_dir: Path) -> dict:
    """Attest every historical schedule cache body and its exact sidecar."""
    cache_dir = data_dir / "raw/historical_bref"
    if cache_dir.is_symlink() or (
        cache_dir.exists() and not cache_dir.is_dir()
    ):
        raise HistoricalValidationError(
            "historical cache root must be a regular directory"
        )
    if not cache_dir.exists():
        return {"basketball_reference": {"pages": []}}

    bodies = set()
    sidecars = set()
    for path in sorted(cache_dir.iterdir()):
        if path.is_symlink() or not path.is_file():
            raise HistoricalValidationError(
                f"non-regular historical cache entry: {path.name}"
            )
        if path.name.endswith(".part"):
            raise HistoricalValidationError(
                f"unexpected historical cache entry: {path.name}"
            )
        if path.name.endswith(".html"):
            name = path.name
            if _HISTORICAL_CACHE_NAME.fullmatch(name) is None:
                raise HistoricalValidationError(
                    f"unexpected historical cache entry: {path.name}"
                )
            bodies.add(name)
        elif path.name.endswith(".html.meta.json"):
            name = path.name.removesuffix(".meta.json")
            if _HISTORICAL_CACHE_NAME.fullmatch(name) is None:
                raise HistoricalValidationError(
                    f"unexpected historical cache entry: {path.name}"
                )
            sidecars.add(name)
        else:
            raise HistoricalValidationError(
                f"unexpected historical cache entry: {path.name}"
            )
    if bodies != sidecars:
        raise HistoricalValidationError("incomplete historical cache pair")

    by_season: dict[int, list[Path]] = {}
    for name in sorted(bodies):
        match = _HISTORICAL_CACHE_NAME.fullmatch(name)
        assert match is not None
        season = int(match["season"])
        try:
            expected_league = historical_bref.league_for_season(season)
        except ValueError as exc:
            raise _cache_error(exc) from exc
        if match["league"] != expected_league:
            raise HistoricalValidationError("historical cache metadata mismatch")
        by_season.setdefault(season, []).append(cache_dir / name)

    pages = []
    identities = set()
    urls = set()
    for season, paths in sorted(by_season.items()):
        indexes = [path for path in paths if "_games-" not in path.name]
        if len(indexes) != 1:
            raise HistoricalValidationError(
                "historical cache requires exactly one season index"
            )
        index_path = indexes[0]
        try:
            index_attestation = cache.read_attested(
                index_path,
                lambda path, value=season: historical_bref.validate_index(
                    path, value
                ),
            )
            assert index_attestation is not None
            index_payload, _ = index_attestation
            discovered = historical_bref.discover_month_pages(
                index_payload, season
            )
        except (cache.CacheIntegrityError, ValueError, json.JSONDecodeError) as exc:
            raise _cache_error(exc) from exc
        discovered_by_name = {page.filename: page for page in discovered}
        actual_month_names = {path.name for path in paths if path != index_path}
        if actual_month_names != set(discovered_by_name):
            raise HistoricalValidationError(
                "historical cache month pages differ from discovered index"
            )

        for path in sorted(paths):
            match = _HISTORICAL_CACHE_NAME.fullmatch(path.name)
            assert match is not None
            month = match["month"]
            is_index = month is None
            url = (
                historical_bref.index_url(season)
                if is_index else discovered_by_name[path.name].url
            )
            resource = (
                "historical_schedule_index"
                if is_index else "historical_schedule_month"
            )
            try:
                attestation = cache.read_attested(
                    path,
                    (
                        (lambda value, year=season: historical_bref.validate_index(
                            value, year
                        ))
                        if is_index
                        else (
                            lambda value, year=season, page_url=url:
                            historical_bref.validate_month(
                                value, year, page_url
                            )
                        )
                    ),
                )
                assert attestation is not None
                _, metadata = attestation
            except (cache.CacheIntegrityError, ValueError, json.JSONDecodeError) as exc:
                raise _cache_error(exc) from exc
            expected = {
                "provider": "basketball_reference",
                "resource": resource,
                "league": match["league"],
                "season": season,
                "month": month,
                "url": url,
            }
            identity = (
                metadata.get("resource"), metadata.get("league"),
                metadata.get("season"), metadata.get("month"),
            )
            if identity in identities or metadata.get("url") in urls:
                raise HistoricalValidationError("duplicate historical cache identity")
            if any(metadata.get(key) != value for key, value in expected.items()):
                raise HistoricalValidationError("historical cache metadata mismatch")
            identities.add(identity)
            urls.add(metadata["url"])
            pages.append({
                "path": path.relative_to(data_dir).as_posix(),
                "provider": metadata["provider"],
                "resource": metadata["resource"],
                "league": metadata["league"],
                "season": metadata["season"],
                "month": metadata.get("month"),
                "url": metadata["url"],
                "sha256": metadata["sha256"],
            })
    return {"basketball_reference": {"pages": pages}}


def _reference_provenance(data_dir: Path) -> dict:
    names = (
        "franchises.csv",
        "historical_season_counts.csv",
        "historical_site_corrections.csv",
    )
    reference_dir = data_dir / "reference"
    franchise_path = reference_dir / "franchises.csv"
    if franchise_path.read_bytes() != franchises.REFERENCE.read_bytes():
        raise HistoricalValidationError(
            "franchise reference differs from the packaged runtime reference"
        )
    return {
        path.relative_to(data_dir).as_posix(): cache.sha256_file(path)
        for path in (reference_dir / name for name in names)
    }


def _publish_historical_pair(
    stage_dir: Path, output_dir: Path
) -> Path | None:
    try:
        return publication.publish_pair(
            stage_dir,
            output_dir,
            HISTORICAL_PRODUCTS,
            label="historical",
            manifest_table="historical_games",
        )
    except publication.PairPublicationError as exc:
        raise HistoricalValidationError(str(exc)) from exc


def run(data_dir: Path) -> None:
    """Build and atomically publish the independently attested history."""
    games, report = assemble(data_dir)
    with publication.publication_lock(
        data_dir / "parquet", exclusive=False
    ):
        modern_seasons = pd.read_parquet(
            data_dir / "parquet/games.parquet", columns=["season"]
        )["season"]
    modern_min_season = int(modern_seasons.min())
    if modern_min_season != 1984:
        raise HistoricalValidationError(
            "modern publication must begin at ending season 1984"
        )
    report["modern_min_season"] = modern_min_season
    provenance = _raw_provenance(data_dir)
    references = _reference_provenance(data_dir)
    stage_dir = Path(tempfile.mkdtemp(prefix=".historical-stage-", dir=data_dir))
    try:
        parquet_path = stage_dir / "historical_games.parquet"
        games.to_parquet(
            parquet_path, engine="pyarrow", compression="zstd", index=False
        )
        reread = pd.read_parquet(parquet_path)
        build._assert_staged_frame(reread, games, "historical_games")
        pd.testing.assert_frame_equal(reread, games, check_like=False)
        reread_report = validate(
            reread,
            load_expected_counts(
                data_dir / "reference/historical_season_counts.csv"
            ),
        )
        reread_report["modern_min_season"] = modern_min_season
        if reread_report != report:
            raise HistoricalValidationError(
                "historical validation changed after Parquet re-read"
            )
        table_manifest = build._table_manifest(parquet_path, reread)
        table_manifest["schema"] = [
            {"name": field.name, "arrow_type": str(field.type)}
            for field in pq.read_schema(parquet_path)
        ]
        manifest = {
            "schema_version": 1,
            "sources": provenance,
            "references": references,
            "versions": build._dependency_versions(data_dir.parent),
            "tables": {"historical_games": table_manifest},
            "validation": reread_report,
        }
        (stage_dir / "historical_games_manifest.json").write_text(
            json.dumps(
                manifest, indent=2, sort_keys=True, allow_nan=False
            ) + "\n",
            encoding="utf-8",
        )
        retained_rollback = _publish_historical_pair(
            stage_dir,
            data_dir / "parquet",
        )
        if retained_rollback is not None:
            warnings.warn(
                "historical publication committed; prior pair retained at "
                f"{retained_rollback}",
                RuntimeWarning,
                stacklevel=2,
            )
    finally:
        if stage_dir.exists():
            shutil.rmtree(stage_dir)
