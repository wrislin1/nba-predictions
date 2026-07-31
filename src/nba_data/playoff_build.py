"""Typed playoff reference loaders and authoritative site rules."""

from __future__ import annotations

from datetime import date
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from types import MappingProxyType
import warnings
from typing import Any, Mapping, NamedTuple, cast

import pandas as pd
import pyarrow.parquet as pq

from nba_data import (
    bref,
    bref_schedule,
    build,
    cache,
    franchises,
    historical_bref,
    historical_build,
    hoopr,
    playoff_bref,
    publication,
)


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
REFERENCE_FILES = (
    "franchises.csv",
    "hoopr_snapshot.json",
    "playoff_season_counts.csv",
    "playoff_site_rules.csv",
)
PLAYOFF_PRODUCTS = (
    "playoff_games.parquet",
    "playoff_games_manifest.json",
)

_SNAPSHOT_REFERENCE_FILES = REFERENCE_FILES
_SNAPSHOT_PARQUET_FILES = (
    "games.parquet",
    "historical_games.parquet",
    "historical_games_manifest.json",
)


class PlayoffBuildPolicy(NamedTuple):
    """One recursively immutable playoff parser and publication contract."""

    bref: bref.BrefPolicy
    bref_schedule: bref_schedule.BrefSchedulePolicy
    historical_bref: historical_bref.HistoricalBrefPolicy
    playoff_bref: playoff_bref.PlayoffBrefPolicy
    hoopr: hoopr.HooprPolicy
    historical_provenance: historical_build.HistoricalProvenancePolicy
    publication: publication.PublicationPolicy
    count_columns: tuple[str, ...]
    site_rule_columns: tuple[str, ...]
    type_count_columns: tuple[str, ...]
    fixed_type_counts: tuple[
        tuple[int, tuple[tuple[str, int], ...]], ...
    ]
    playoff_columns: tuple[str, ...]
    playoff_dtypes: tuple[tuple[str, str], ...]
    cross_source_key: tuple[str, ...]
    regular_overlap_key: tuple[str, ...]
    expected_game_types: frozenset[str]
    type_to_count_field: tuple[tuple[str, str], ...]
    voided_identity: tuple[tuple[str, Any], ...]
    source_integer_columns: tuple[str, ...]
    reference_files: tuple[str, ...]
    playoff_products: tuple[str, str]
    snapshot_reference_files: tuple[str, ...]
    snapshot_parquet_files: tuple[str, ...]


def capture_policy() -> PlayoffBuildPolicy:
    """Copy all authored build policy before any build input is read."""
    return PlayoffBuildPolicy(
        bref=bref.capture_policy(),
        bref_schedule=bref_schedule.capture_policy(),
        historical_bref=historical_bref.capture_policy(),
        playoff_bref=playoff_bref.capture_policy(),
        hoopr=hoopr.capture_policy(),
        historical_provenance=(
            historical_build.capture_provenance_policy()
        ),
        publication=publication.capture_policy(),
        count_columns=tuple(COUNT_COLUMNS),
        site_rule_columns=tuple(SITE_RULE_COLUMNS),
        type_count_columns=tuple(TYPE_COUNT_COLUMNS),
        fixed_type_counts=tuple(sorted(
            (
                int(season),
                tuple(sorted(
                    (str(name), int(value))
                    for name, value in counts.items()
                )),
            )
            for season, counts in FIXED_TYPE_COUNTS.items()
        )),
        playoff_columns=tuple(PLAYOFF_COLUMNS),
        playoff_dtypes=tuple(sorted(
            (str(name), str(dtype))
            for name, dtype in PLAYOFF_DTYPES.items()
        )),
        cross_source_key=tuple(CROSS_SOURCE_KEY),
        regular_overlap_key=tuple(REGULAR_OVERLAP_KEY),
        expected_game_types=frozenset(_EXPECTED_GAME_TYPES),
        type_to_count_field=tuple(sorted(
            (str(name), str(field_name))
            for name, field_name in _TYPE_TO_COUNT_FIELD.items()
        )),
        voided_identity=tuple(
            (name, _VOIDED_IDENTITY[name])
            for name in ("game_id", "season", "game_date", "game_type")
        ),
        source_integer_columns=tuple(_SOURCE_INTEGER_COLUMNS),
        reference_files=tuple(REFERENCE_FILES),
        playoff_products=(
            str(PLAYOFF_PRODUCTS[0]),
            str(PLAYOFF_PRODUCTS[1]),
        ),
        snapshot_reference_files=tuple(_SNAPSHOT_REFERENCE_FILES),
        snapshot_parquet_files=tuple(_SNAPSHOT_PARQUET_FILES),
    )


def _policy_or_current(
    policy: PlayoffBuildPolicy | None,
) -> PlayoffBuildPolicy:
    return capture_policy() if policy is None else policy


class PlayoffValidationError(ValueError):
    """Raised when a playoff reference or site rule is invalid."""


class ExpectedSeason(NamedTuple):
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


class SiteRule(NamedTuple):
    season: int
    site_status: str
    expected_matches: int
    source_url: str
    note: str


class _InputFileAttestation(NamedTuple):
    source: str
    relative_path: str
    identity: tuple[int, int, int]
    sha256: str
    payload: bytes


class _InputDirectoryAttestation(NamedTuple):
    source: str
    identity: tuple[int, int, int]
    entries: tuple[str, ...]


def _sha256_pinned_regular(
    path: Path,
    *,
    expected_identity: tuple[int, int, int] | None,
    message: str,
) -> str:
    """Hash one path without following it or losing its object binding."""
    file_fd = None
    try:
        observed = path.lstat()
        identity = (
            observed.st_dev,
            observed.st_ino,
            observed.st_mode,
        )
        if (
            not stat.S_ISREG(observed.st_mode)
            or (
                expected_identity is not None
                and identity != expected_identity
            )
        ):
            raise PlayoffValidationError(message)
        file_fd = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(file_fd)
        if (opened.st_dev, opened.st_ino, opened.st_mode) != identity:
            raise PlayoffValidationError(message)
        digest = hashlib.sha256()
        with os.fdopen(os.dup(file_fd), "rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        final_fd = os.fstat(file_fd)
        final_path = path.lstat()
        if (
            final_fd.st_dev,
            final_fd.st_ino,
            final_fd.st_mode,
        ) != identity or (
            final_path.st_dev,
            final_path.st_ino,
            final_path.st_mode,
        ) != identity:
            raise PlayoffValidationError(message)
        return digest.hexdigest()
    except PlayoffValidationError:
        raise
    except OSError as exc:
        raise PlayoffValidationError(message) from exc
    finally:
        if file_fd is not None:
            os.close(file_fd)


def _require_pinned_directory(
    path: Path,
    *,
    expected_identity: tuple[int, int, int],
    expected_entries: tuple[str, ...],
    message: str,
) -> None:
    """Verify one directory inventory through a pinned no-follow handle."""
    directory_fd = None
    try:
        observed = path.lstat()
        identity = (
            observed.st_dev,
            observed.st_ino,
            observed.st_mode,
        )
        if (
            not stat.S_ISDIR(observed.st_mode)
            or identity != expected_identity
        ):
            raise PlayoffValidationError(message)
        directory_fd = os.open(
            path,
            os.O_RDONLY
            | os.O_DIRECTORY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(directory_fd)
        if (
            opened.st_dev,
            opened.st_ino,
            opened.st_mode,
        ) != expected_identity:
            raise PlayoffValidationError(message)
        entries = tuple(sorted(os.listdir(directory_fd)))
        final_fd = os.fstat(directory_fd)
        final_path = path.lstat()
        if (
            entries != expected_entries
            or (
                final_fd.st_dev,
                final_fd.st_ino,
                final_fd.st_mode,
            ) != expected_identity
            or (
                final_path.st_dev,
                final_path.st_ino,
                final_path.st_mode,
            ) != expected_identity
        ):
            raise PlayoffValidationError(message)
    except PlayoffValidationError:
        raise
    except OSError as exc:
        raise PlayoffValidationError(message) from exc
    finally:
        if directory_fd is not None:
            os.close(directory_fd)


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({
            str(key): _freeze_value(item)
            for key, item in value.items()
        })
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_value(item) for item in value)
    return value


def _thaw_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _thaw_value(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return [_thaw_value(item) for item in value]
    if isinstance(value, frozenset):
        return sorted(_thaw_value(item) for item in value)
    return value


class CapturedPlayoffInputs(NamedTuple):
    """One immutable, path-independent playoff build input generation."""

    payloads: Mapping[str, bytes]
    attestations: tuple[_InputFileAttestation, ...]
    directories: tuple[_InputDirectoryAttestation, ...]
    franchise_resolver: franchises.FranchiseResolver
    hoopr_snapshot: Mapping[str, Any]
    versions: Mapping[str, Any]
    policy: PlayoffBuildPolicy

    def payload(self, relative_path: str) -> bytes:
        try:
            return self.payloads[relative_path]
        except KeyError as exc:
            raise PlayoffValidationError(
                f"captured playoff input is missing: {relative_path}"
            ) from exc

    def resolve_franchise(self, name: str, season: int) -> str:
        return self.franchise_resolver.resolve(name, season)

    def cache_pair(
        self,
        relative_path: str,
        validator,
    ) -> tuple[bytes, dict]:
        payload = self.payload(relative_path)
        metadata = cache.attest_bytes(
            payload,
            self.payload(relative_path + ".meta.json"),
            validator,
            label=relative_path,
        )
        return payload, metadata

    def verify_unchanged(self, *, message: str) -> None:
        try:
            for directory in self.directories:
                source = Path(directory.source)
                _require_pinned_directory(
                    source,
                    expected_identity=directory.identity,
                    expected_entries=directory.entries,
                    message=f"{message}: {source}",
                )
            for record in self.attestations:
                source = Path(record.source)
                if _sha256_pinned_regular(
                    source,
                    expected_identity=record.identity,
                    message=f"{message}: {source}",
                ) != record.sha256:
                    raise PlayoffValidationError(
                        f"{message}: {source}"
                    )
        except PlayoffValidationError:
            raise
        except OSError as exc:
            raise PlayoffValidationError(message) from exc


def _copy_input_file(
    source: Path, destination: Path
) -> _InputFileAttestation:
    """Capture and bind one regular input without following its path."""
    source_fd = None
    try:
        observed = source.lstat()
        if not stat.S_ISREG(observed.st_mode):
            raise PlayoffValidationError(
                f"playoff input must be a regular file: {source}"
            )
        identity = (
            observed.st_dev,
            observed.st_ino,
            observed.st_mode,
        )
        source_fd = os.open(
            source,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(source_fd)
        if (
            opened.st_dev,
            opened.st_ino,
            opened.st_mode,
        ) != identity:
            raise PlayoffValidationError(
                f"input changed while creating snapshot: {source}"
            )
        blocks = []
        digest = hashlib.sha256()
        with os.fdopen(os.dup(source_fd), "rb") as source_handle:
            for block in iter(
                lambda: source_handle.read(1024 * 1024), b""
            ):
                digest.update(block)
                blocks.append(block)
        payload = b"".join(blocks)
        captured_sha256 = digest.hexdigest()
        final_source = source.lstat()
        final_identity = (
            final_source.st_dev,
            final_source.st_ino,
            final_source.st_mode,
        )
        if (
            final_identity != identity
            or (
                os.fstat(source_fd).st_dev,
                os.fstat(source_fd).st_ino,
                os.fstat(source_fd).st_mode,
            ) != identity
            or _sha256_pinned_regular(
                source,
                expected_identity=identity,
                message=(
                    "input changed while creating snapshot: "
                    f"{source}"
                ),
            ) != captured_sha256
        ):
            raise PlayoffValidationError(
                f"input changed while creating snapshot: {source}"
            )
    except PlayoffValidationError:
        raise
    except OSError as exc:
        raise PlayoffValidationError(
            f"could not snapshot playoff input: {source}"
        ) from exc
    finally:
        if source_fd is not None:
            os.close(source_fd)
    return _InputFileAttestation(
        source=str(source),
        relative_path=destination.as_posix(),
        identity=identity,
        sha256=captured_sha256,
        payload=payload,
    )


def _snapshot_directory(
    files: list[_InputFileAttestation],
    directories: list[_InputDirectoryAttestation],
    source: Path,
    relative_dir: Path,
) -> None:
    try:
        observed = source.lstat()
        if not stat.S_ISDIR(observed.st_mode):
            raise PlayoffValidationError(
                f"playoff input must be a regular directory: {source}"
            )
        entries = tuple(sorted(os.listdir(source)))
        identity = (
            observed.st_dev,
            observed.st_ino,
            observed.st_mode,
        )
        for name in entries:
            files.append(
                _copy_input_file(source / name, relative_dir / name)
            )
        final = source.lstat()
        if (
            (final.st_dev, final.st_ino, final.st_mode) != identity
            or tuple(sorted(os.listdir(source))) != entries
        ):
            raise PlayoffValidationError(
                f"input changed while creating snapshot: {source}"
            )
        directories.append(
            _InputDirectoryAttestation(str(source), identity, entries)
        )
    except PlayoffValidationError:
        raise
    except OSError as exc:
        raise PlayoffValidationError(
            f"could not snapshot playoff input directory: {source}"
        ) from exc


def _capture_input_snapshot(data_dir: Path) -> CapturedPlayoffInputs:
    """Capture every playoff build input once as immutable bytes."""
    policy = capture_policy()
    data_dir.mkdir(parents=True, exist_ok=True)
    files: list[_InputFileAttestation] = []
    directories: list[_InputDirectoryAttestation] = []
    _snapshot_directory(
        files,
        directories,
        data_dir / "raw/historical_bref",
        Path("raw/historical_bref"),
    )
    _snapshot_directory(
        files,
        directories,
        data_dir / "raw/playoff_bref",
        Path("raw/playoff_bref"),
    )
    for season, month in policy.playoff_bref.play_in_months:
        name = f"NBA_{season}_games-{month}.html"
        for suffix in ("", ".meta.json"):
            relative = Path("raw/bref") / f"{name}{suffix}"
            files.append(_copy_input_file(
                data_dir / relative, relative
            ))
    for season in policy.hoopr.snapshot_seasons:
        name = f"nba_schedule_{season}.parquet"
        for suffix in ("", ".meta.json"):
            relative = Path("raw/hoopr") / f"{name}{suffix}"
            files.append(_copy_input_file(
                data_dir / relative, relative
            ))
    for name in policy.snapshot_reference_files:
        relative = Path("reference") / name
        files.append(_copy_input_file(data_dir / relative, relative))
    runtime_sources = {
        "franchises.csv": cast(Path, franchises.REFERENCE),
        "hoopr_snapshot.json": cast(Path, hoopr.SNAPSHOT_PATH),
    }
    for name, source in runtime_sources.items():
        files.append(_copy_input_file(
            source, Path("runtime_reference") / name
        ))
    with publication.publication_lock(
        data_dir / "parquet",
        exclusive=False,
        lock_filename=policy.publication.lock_filename,
    ):
        for name in policy.snapshot_parquet_files:
            relative = Path("parquet") / name
            files.append(_copy_input_file(
                data_dir / relative, relative
            ))

    payloads = MappingProxyType({
        record.relative_path: record.payload for record in files
    })
    if len(payloads) != len(files):
        raise PlayoffValidationError(
            "playoff input capture contains duplicate paths"
        )
    if (
        payloads["reference/franchises.csv"]
        != payloads["runtime_reference/franchises.csv"]
    ):
        raise PlayoffValidationError(
            "franchise reference differs from the packaged runtime reference"
        )
    if (
        payloads["reference/hoopr_snapshot.json"]
        != payloads["runtime_reference/hoopr_snapshot.json"]
    ):
        raise PlayoffValidationError(
            "hoopR snapshot reference differs from the packaged runtime reference"
        )
    resolver = franchises.FranchiseResolver.from_payload(
        payloads["runtime_reference/franchises.csv"]
    )
    parsed_franchises = franchises.load_reference_payload(
        payloads["runtime_reference/franchises.csv"]
    )
    runtime_franchises = franchises.load_reference().copy(deep=True)
    if not parsed_franchises.equals(runtime_franchises):
        raise PlayoffValidationError(
            "franchise runtime state differs from the packaged reference"
        )
    try:
        parsed_snapshot = json.loads(
            payloads["runtime_reference/hoopr_snapshot.json"].decode(
                "utf-8"
            )
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlayoffValidationError(
            "packaged hoopR snapshot reference is invalid"
        ) from exc
    if not isinstance(parsed_snapshot, dict):
        raise PlayoffValidationError(
            "packaged hoopR snapshot reference must contain an object"
        )
    captured_runtime_snapshot = _freeze_value({
        "repository": policy.hoopr.snapshot_repository,
        "commit": policy.hoopr.snapshot_commit,
        "seasons": list(policy.hoopr.snapshot_seasons),
    })
    if parsed_snapshot != _thaw_value(captured_runtime_snapshot):
        raise PlayoffValidationError(
            "hoopR runtime state differs from the packaged reference"
        )
    captured = CapturedPlayoffInputs(
        payloads=payloads,
        attestations=tuple(files),
        directories=tuple(directories),
        franchise_resolver=resolver,
        hoopr_snapshot=_freeze_value(parsed_snapshot),
        versions=_freeze_value(
            build._dependency_versions(data_dir.parent)
        ),
        policy=policy,
    )
    captured.verify_unchanged(
        message="input changed while creating snapshot"
    )
    return captured


def _read_exact_csv(
    path: Path | BytesIO, columns: list[str], label: str
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


def load_expected_counts(
    path: Path | BytesIO,
    *,
    policy: PlayoffBuildPolicy | None = None,
) -> dict[int, ExpectedSeason]:
    """Load the exact independently curated 1947--2026 ledger."""
    policy = _policy_or_current(policy)
    expected_seasons = list(policy.playoff_bref.playoff_seasons)
    type_count_columns = list(policy.type_count_columns)
    fixed_type_counts = {
        season: dict(counts)
        for season, counts in policy.fixed_type_counts
    }
    frame = _read_exact_csv(
        path, list(policy.count_columns), "count"
    )
    if _has_blank(frame["source_url"]) or _has_blank(frame["note"]):
        raise PlayoffValidationError(
            "counts require nonblank source URL and note"
        )
    if not frame["source_url"].str.startswith("https://").all():
        raise PlayoffValidationError("count source URLs must use HTTPS")

    numeric_columns = ["season", *type_count_columns, "total_games"]
    frame = _parse_integer_columns(
        frame, numeric_columns, "count fields must be integers"
    )
    if (
        len(frame) != len(expected_seasons)
        or frame["season"].duplicated(keep=False).any()
        or frame["season"].tolist() != expected_seasons
    ):
        raise PlayoffValidationError(
            "counts must cover exactly 1947..2026 in ascending order"
        )
    if frame[type_count_columns].lt(0).any().any():
        raise PlayoffValidationError("type counts cannot be negative")
    if (
        frame["playoff_games"].le(0).any()
        or frame["total_games"].le(0).any()
    ):
        raise PlayoffValidationError(
            "playoff_games and total_games must be positive"
        )
    if not frame[type_count_columns].sum(axis=1).equals(
        frame["total_games"]
    ):
        raise PlayoffValidationError(
            "type counts must sum to total_games"
        )

    for season in expected_seasons:
        fixed = fixed_type_counts.get(season, {})
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


def load_site_rules(
    path: Path | BytesIO,
    *,
    policy: PlayoffBuildPolicy | None = None,
) -> dict[int, SiteRule]:
    """Load the single approved 2020 neutral-site rule."""
    policy = _policy_or_current(policy)
    frame = _read_exact_csv(
        path, list(policy.site_rule_columns), "site rule"
    )
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


def _enforce_schema(
    frame: pd.DataFrame,
    *,
    policy: PlayoffBuildPolicy | None = None,
) -> pd.DataFrame:
    """Publish the exact ordered and typed playoff-game contract."""
    policy = _policy_or_current(policy)
    output = frame.loc[:, list(policy.playoff_columns)].copy()
    _require_source_integers(
        output,
        [*policy.source_integer_columns, "home_margin"],
    )
    output["game_date"] = pd.to_datetime(
        output["game_date"], errors="raise"
    ).dt.date
    for column, dtype in policy.playoff_dtypes:
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


def _normalize_regular_keys(
    frame: pd.DataFrame,
    *,
    policy: PlayoffBuildPolicy | None = None,
) -> pd.DataFrame:
    policy = _policy_or_current(policy)
    missing = [
        column for column in policy.regular_overlap_key
        if column not in frame.columns
    ]
    if missing:
        raise PlayoffValidationError(
            f"regular publication missing overlap columns: {missing}"
        )
    normalized = frame.loc[:, list(policy.regular_overlap_key)].copy()
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


def _mismatch_sample(
    frame: pd.DataFrame,
    side: str,
    *,
    policy: PlayoffBuildPolicy | None = None,
) -> list[dict]:
    policy = _policy_or_current(policy)
    rows = frame.loc[
        frame["_merge"].eq(side), list(policy.cross_source_key)
    ].copy()
    if rows.empty:
        return []
    rows = rows.sort_values(
        [
            column for column in policy.cross_source_key
            if column in rows
        ],
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
    policy: PlayoffBuildPolicy | None = None,
) -> dict:
    """Enforce all playoff publication gates and return an audit report."""
    policy = _policy_or_current(policy)
    playoff_columns = list(policy.playoff_columns)
    playoff_dtypes = dict(policy.playoff_dtypes)
    cross_source_key = list(policy.cross_source_key)
    regular_overlap_key = list(policy.regular_overlap_key)
    expected_seasons = list(policy.playoff_bref.playoff_seasons)
    type_to_count_field = dict(policy.type_to_count_field)
    voided_identity = dict(policy.voided_identity)
    errors: list[str] = []
    unique_columns = games.loc[
        :, ~games.columns.duplicated(keep="first")
    ]
    schema_ok = (
        not games.columns.has_duplicates
        and games.columns.tolist() == playoff_columns
        and str(games["game_date"].dtype) == "object"
        and all(
            column in games
            and str(games[column].dtype) == dtype
            for column, dtype in playoff_dtypes.items()
        )
    )
    if not schema_ok:
        errors.append("playoff columns and dtypes differ from publication schema")

    frame = unique_columns.reindex(columns=playoff_columns).copy()
    for column in [
        "season",
        "away_pts",
        "home_pts",
        "home_margin",
        "overtime_periods",
        "attendance",
    ]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

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
        ~frame["game_type"].isin(policy.expected_game_types)
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
        column for column in playoff_columns if column not in nullable
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
                        bref_schedule.overtime_periods(
                            str(marker), policy=policy.bref_schedule
                        )
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
            for game_type in type_to_count_field
        }
        actual_counts[season] = actual
        expected = expected_counts.get(season)
        if expected is None or (
            any(
                actual[game_type]
                != getattr(expected, field)
                for game_type, field in type_to_count_field.items()
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
        list(voided_identity),
    ]
    valid_voided = (
        len(voided) == 1
        and voided.iloc[0].to_dict() == voided_identity
    )
    if not valid_voided:
        errors.append("invalid voided-game exemption")

    overlap_rows = 0
    try:
        regular_keys = pd.concat(
            [
                _normalize_regular_keys(
                    modern_regular, policy=policy
                ),
                _normalize_regular_keys(
                    historical_regular, policy=policy
                ),
            ],
            ignore_index=True,
        ).drop_duplicates()
        playoff_keys = _normalize_regular_keys(frame, policy=policy)
        overlap = playoff_keys.merge(
            regular_keys,
            on=regular_overlap_key,
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
            cross_source_key,
        ].copy()
        missing_hoopr = [
            column for column in cross_source_key
            if column not in hoopr_games.columns
        ]
        if missing_hoopr:
            raise ValueError(
                f"missing hoopR key columns {missing_hoopr}"
            )
        hoopr_keys = hoopr_games.loc[:, cross_source_key].copy()
        if (
            bref_keys.duplicated(cross_source_key).any()
            or hoopr_keys.duplicated(cross_source_key).any()
        ):
            raise ValueError("duplicate cross-source key")
        comparison = bref_keys.merge(
            hoopr_keys,
            on=cross_source_key,
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
                f"left_only={_mismatch_sample(comparison, 'left_only', policy=policy)}, "
                f"right_only={_mismatch_sample(comparison, 'right_only', policy=policy)}"
            )
    except (KeyError, TypeError, ValueError, pd.errors.MergeError) as error:
        errors.append(f"BREF/hoopR postseason mismatch: {error}")

    if (
        not games.columns.has_duplicates
        and games.columns.tolist() == playoff_columns
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


def _captured_historical_payloads(
    inputs: CapturedPlayoffInputs,
    season: int,
) -> tuple[bytes, Mapping[str, bytes]]:
    policy = inputs.policy
    prefix = "raw/historical_bref/"
    index_name = historical_bref.index_filename(
        season, policy=policy.historical_bref
    )
    index_payload, _ = inputs.cache_pair(
        prefix + index_name,
        lambda payload: historical_bref.validate_index_bytes(
            payload, season, policy=policy.historical_bref
        ),
    )
    month_payloads = {}
    for page in historical_bref.discover_month_pages(
        index_payload,
        season,
        policy=policy.historical_bref,
    ):
        payload, _ = inputs.cache_pair(
            prefix + page.filename,
            lambda value, url=page.url: (
                historical_bref.validate_month_bytes(
                    value,
                    season,
                    url,
                    policy=policy.historical_bref,
                    schedule_policy=policy.bref_schedule,
                )
            ),
        )
        month_payloads[page.filename] = payload
    return index_payload, MappingProxyType(month_payloads)


def _captured_playoff_season(
    inputs: CapturedPlayoffInputs,
    season: int,
) -> pd.DataFrame:
    policy = inputs.policy
    if season <= 1983:
        index_payload, month_payloads = (
            _captured_historical_payloads(inputs, season)
        )
        return playoff_bref.load_historical_season_payloads(
            season=season,
            index_payload=index_payload,
            month_payloads=month_payloads,
            policy=policy.playoff_bref,
            historical_policy=policy.historical_bref,
            schedule_policy=policy.bref_schedule,
        )
    dedicated, _ = inputs.cache_pair(
        "raw/playoff_bref/" + playoff_bref.playoff_filename(
            season, policy=policy.playoff_bref
        ),
        lambda payload: playoff_bref.validate_dedicated_bytes(
            payload,
            season,
            policy=policy.playoff_bref,
            schedule_policy=policy.bref_schedule,
        ),
    )
    play_in = None
    play_in_months = dict(policy.playoff_bref.play_in_months)
    if season in play_in_months:
        month = play_in_months[season]
        play_in, _ = inputs.cache_pair(
            (
                "raw/playoff_bref/"
                f"NBA_{season}_games-{month}.html"
            ),
            lambda payload: playoff_bref.validate_seeded_play_in_bytes(
                payload,
                season,
                policy=policy.playoff_bref,
                schedule_policy=policy.bref_schedule,
            ),
        )
    return playoff_bref.load_modern_season_payloads(
        season=season,
        dedicated_payload=dedicated,
        play_in_payload=play_in,
        policy=policy.playoff_bref,
        schedule_policy=policy.bref_schedule,
    )


def _captured_hoopr_payload(
    inputs: CapturedPlayoffInputs,
    season: int,
) -> bytes:
    relative = f"raw/hoopr/nba_schedule_{season}.parquet"
    payload, metadata = inputs.cache_pair(
        relative,
        lambda value: hoopr.validate_payload_bytes(
            value, policy=inputs.policy.hoopr
        ),
    )
    expected = {
        "provider": "hoopr",
        "season": season,
        "upstream_commit": inputs.hoopr_snapshot["commit"],
    }
    observed = {key: metadata.get(key) for key in expected}
    if observed != expected or any(
        type(observed[key]) is not type(expected[key])
        for key in expected
    ):
        raise cache.CacheIntegrityError(
            f"hoopR cache metadata mismatch for {relative}: "
            f"expected {expected}, observed {observed}"
        )
    return payload


def assemble_from_inputs(
    inputs: CapturedPlayoffInputs,
) -> tuple[pd.DataFrame, dict, dict]:
    """Assemble playoff rows exclusively from one captured generation."""
    policy = inputs.policy
    frames = [
        _captured_playoff_season(inputs, season)
        for season in policy.playoff_bref.playoff_seasons
    ]
    source = pd.concat(frames, ignore_index=True)
    _require_source_integers(
        source, list(policy.source_integer_columns)
    )
    source["away_franchise_id"] = [
        inputs.resolve_franchise(str(name), int(season))
        for name, season in zip(
            source["away_team_name"],
            source["season"],
            strict=True,
        )
    ]
    source["home_franchise_id"] = [
        inputs.resolve_franchise(str(name), int(season))
        for name, season in zip(
            source["home_team_name"],
            source["season"],
            strict=True,
        )
    ]
    source, site_rule_matches = apply_site_rules(
        source,
        load_site_rules(
            BytesIO(inputs.payload(
                "reference/playoff_site_rules.csv"
            )),
            policy=policy,
        ),
    )
    source["home_margin"] = source["home_pts"] - source["away_pts"]
    source_rows = source[["game_id", "overtime_marker"]].copy()
    games = _enforce_schema(source, policy=policy)

    modern_regular = pd.read_parquet(
        BytesIO(inputs.payload("parquet/games.parquet")),
        columns=list(policy.regular_overlap_key),
    )
    historical_regular = pd.read_parquet(
        BytesIO(inputs.payload("parquet/historical_games.parquet")),
        columns=list(policy.regular_overlap_key),
    )
    hoopr_games = pd.concat(
        [
            hoopr.load_postseason_bytes(
                _captured_hoopr_payload(inputs, season),
                season,
                inputs.resolve_franchise,
                policy=policy.hoopr,
            )
            for season in policy.hoopr.snapshot_seasons
        ],
        ignore_index=True,
    )
    expected_counts = load_expected_counts(
        BytesIO(inputs.payload(
            "reference/playoff_season_counts.csv"
        )),
        policy=policy,
    )
    report = validate(
        games,
        expected_counts,
        evidence=source_rows,
        hoopr_games=hoopr_games,
        modern_regular=modern_regular,
        historical_regular=historical_regular,
        site_rule_matches=site_rule_matches,
        policy=policy,
    )
    evidence_bundle = {
        "source_rows": source_rows,
        "hoopr_games": hoopr_games,
        "modern_regular": modern_regular,
        "historical_regular": historical_regular,
        "site_rule_matches": site_rule_matches,
    }
    return games, report, evidence_bundle


def assemble(data_dir: Path) -> tuple[pd.DataFrame, dict, dict]:
    """Capture and assemble one coherent live playoff input generation."""
    return assemble_from_inputs(_capture_input_snapshot(data_dir))


def _raw_provenance_from_inputs(
    inputs: CapturedPlayoffInputs,
) -> dict:
    """Attest every source from the same immutable captured generation."""
    policy = inputs.policy
    play_in_months = dict(policy.playoff_bref.play_in_months)
    try:
        historical_manifest_payload = inputs.payload(
            "parquet/historical_games_manifest.json"
        )
        historical_manifest = json.loads(
            historical_manifest_payload.decode("utf-8")
        )
        if not isinstance(historical_manifest, dict):
            raise ValueError(
                "historical games manifest must contain an object"
            )
        historical_sources = (
            historical_build._raw_provenance_from_payloads(
                inputs.payloads,
                policy=policy.historical_provenance,
                historical_policy=policy.historical_bref,
                schedule_policy=policy.bref_schedule,
            )
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        historical_build.HistoricalValidationError,
    ) as exc:
        raise PlayoffValidationError(
            f"historical source provenance is invalid: {exc}"
        ) from exc
    if historical_manifest.get("sources") != historical_sources:
        raise PlayoffValidationError(
            "historical manifest sources differ from verified cache provenance"
        )
    historical_pages = list(
        historical_sources["basketball_reference"]["pages"]
    )

    expected_bodies = {
        *(
            playoff_bref.playoff_filename(
                season, policy=policy.playoff_bref
            )
            for season in policy.playoff_bref.dedicated_seasons
        ),
        *(
            f"NBA_{season}_games-{month}.html"
            for season, month in policy.playoff_bref.play_in_months
        ),
    }
    prefix = "raw/playoff_bref/"
    observed_entries = {
        name.removeprefix(prefix)
        for name in inputs.payloads
        if name.startswith(prefix)
    }
    expected_entries = {
        *(name for name in expected_bodies),
        *(name + ".meta.json" for name in expected_bodies),
    }
    if observed_entries != expected_entries:
        raise PlayoffValidationError(
            "playoff cache inventory is incomplete"
        )

    playoff_pages = []
    stable_page_fields = (
        "provider",
        "resource",
        "league",
        "season",
        "month",
        "url",
        "sha256",
    )
    for name in sorted(expected_bodies):
        dedicated = "_games-" not in name
        if dedicated:
            season = int(name.removeprefix("NBA_").split("_", 1)[0])
            validator = lambda value, year=season: (
                playoff_bref.validate_dedicated_bytes(
                    value,
                    year,
                    policy=policy.playoff_bref,
                    schedule_policy=policy.bref_schedule,
                )
            )
        else:
            season = int(name.removeprefix("NBA_").split("_", 1)[0])
            validator = lambda value, year=season: (
                playoff_bref.validate_seeded_play_in_bytes(
                    value,
                    year,
                    policy=policy.playoff_bref,
                    schedule_policy=policy.bref_schedule,
                )
            )
        try:
            payload, metadata = inputs.cache_pair(
                prefix + name, validator
            )
            expected = (
                {
                    "provider": "basketball_reference",
                    "resource": "playoff_schedule",
                    "league": "NBA",
                    "season": season,
                    "month": None,
                    "url": playoff_bref.playoff_url(
                        season, policy=policy.playoff_bref
                    ),
                }
                if dedicated else playoff_bref._seed_metadata(
                    season, payload, policy=policy.playoff_bref
                )
            )
            observed = {
                key: metadata.get(key) for key in expected
            }
            if observed != expected or any(
                type(observed[key]) is not type(expected[key])
                for key in expected
            ):
                raise PlayoffValidationError(
                    "playoff page metadata mismatch"
                )
            record = {
                "path": prefix + name,
                **{
                    field: metadata[field]
                    for field in stable_page_fields
                },
            }
            if not dedicated:
                for field in (
                    "seeded_from",
                    "seed_operation",
                    "source_sha256",
                ):
                    record[field] = metadata[field]
                source_relative = metadata["seeded_from"]
                source_payload, source_metadata = inputs.cache_pair(
                    source_relative,
                    lambda value: bref.validate_payload_bytes(
                        value, policy=policy.bref
                    ),
                )
                source_expected = {
                    "provider": "basketball_reference",
                    "season": season,
                    "month": play_in_months[season],
                }
                source_observed = {
                    key: source_metadata.get(key)
                    for key in source_expected
                }
                source_sha256 = hashlib.sha256(
                    source_payload
                ).hexdigest()
                if (
                    source_observed != source_expected
                    or source_sha256 != metadata["source_sha256"]
                    or source_sha256 != metadata["sha256"]
                ):
                    raise PlayoffValidationError(
                        "seeded playoff source hash mismatch"
                    )
            playoff_pages.append(record)
        except PlayoffValidationError:
            raise
        except (
            KeyError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValueError,
            cache.CacheIntegrityError,
        ) as exc:
            raise PlayoffValidationError(
                f"playoff page provenance is invalid for {name}: {exc}"
            ) from exc

    commit = inputs.hoopr_snapshot["commit"]
    repository = inputs.hoopr_snapshot["repository"]
    hoopr_schedules = []
    for season in policy.hoopr.snapshot_seasons:
        payload = _captured_hoopr_payload(inputs, season)
        hoopr_schedules.append({
            "path": f"raw/hoopr/nba_schedule_{season}.parquet",
            "provider": "hoopr",
            "resource": "pinned_schedule",
            "season": season,
            "url": (
                "https://raw.githubusercontent.com/"
                f"{repository}/"
                f"{commit}/nba/schedules/parquet/"
                f"nba_schedule_{season}.parquet"
            ),
            "upstream_commit": commit,
            "sha256": hashlib.sha256(payload).hexdigest(),
        })
    return {
        "basketball_reference": {
            "historical_manifest": {
                "path": "parquet/historical_games_manifest.json",
                "sha256": hashlib.sha256(
                    historical_manifest_payload
                ).hexdigest(),
            },
            "historical_pages": historical_pages,
            "playoff_pages": playoff_pages,
        },
        "hoopr": {
            "upstream_commit": commit,
            "schedules": hoopr_schedules,
        },
    }


def _raw_provenance_path(data_dir: Path) -> dict:
    """Compatibility alias for captured path provenance."""
    return _raw_provenance(data_dir)


def _raw_provenance(
    source: Path | CapturedPlayoffInputs,
) -> dict:
    """Attest a captured generation, capturing a live Path just once."""
    inputs = (
        source if isinstance(source, CapturedPlayoffInputs)
        else _capture_input_snapshot(source)
    )
    return _raw_provenance_from_inputs(inputs)


def _validate_runtime_reference_binding(
    data_dir: Path,
    *,
    runtime_reference_dir: Path | None = None,
) -> None:
    reference_dir = data_dir / "reference"
    runtime_dir = runtime_reference_dir
    runtime_paths = {
        "franchises.csv": (
            runtime_dir / "franchises.csv"
            if runtime_dir is not None
            else cast(Path, franchises.REFERENCE)
        ),
        "hoopr_snapshot.json": (
            runtime_dir / "hoopr_snapshot.json"
            if runtime_dir is not None
            else cast(Path, hoopr.SNAPSHOT_PATH)
        ),
    }
    labels = {
        "franchises.csv": "franchise reference",
        "hoopr_snapshot.json": "hoopR snapshot reference",
    }
    payloads = {}
    for name, runtime_path in runtime_paths.items():
        try:
            checkout_path = reference_dir / name
            if (
                checkout_path.is_symlink()
                or not checkout_path.is_file()
                or runtime_path.is_symlink()
                or not runtime_path.is_file()
            ):
                raise PlayoffValidationError(
                    f"{labels[name]} must be a regular file"
                )
            checkout_payload = checkout_path.read_bytes()
            runtime_payload = runtime_path.read_bytes()
        except OSError as exc:
            raise PlayoffValidationError(
                f"could not read {labels[name]}"
            ) from exc
        if checkout_payload != runtime_payload:
            raise PlayoffValidationError(
                f"{labels[name]} differs from the packaged runtime reference"
            )
        payloads[name] = runtime_payload
    try:
        runtime_franchises = pd.read_csv(
            Path(runtime_paths["franchises.csv"]),
            dtype={"alias": str, "franchise_id": str},
        )
        runtime_franchises["first_season"] = runtime_franchises[
            "first_season"
        ].astype("Int64")
        runtime_franchises["last_season"] = runtime_franchises[
            "last_season"
        ].astype("Int64")
        if not runtime_franchises.equals(franchises.load_reference()):
            raise PlayoffValidationError(
                "franchise runtime state differs from the packaged reference"
            )
        runtime_snapshot = json.loads(
            payloads["hoopr_snapshot.json"].decode("utf-8")
        )
        if runtime_snapshot != hoopr.SNAPSHOT:
            raise PlayoffValidationError(
                "hoopR runtime state differs from the packaged reference"
            )
    except PlayoffValidationError:
        raise
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise PlayoffValidationError(
            "packaged runtime reference is invalid"
        ) from exc


def _reference_provenance_from_inputs(
    inputs: CapturedPlayoffInputs,
) -> dict:
    """Attest deterministic references from immutable captured bytes."""
    if (
        inputs.payload("reference/franchises.csv")
        != inputs.payload("runtime_reference/franchises.csv")
    ):
        raise PlayoffValidationError(
            "franchise reference differs from the packaged runtime reference"
        )
    if (
        inputs.payload("reference/hoopr_snapshot.json")
        != inputs.payload("runtime_reference/hoopr_snapshot.json")
    ):
        raise PlayoffValidationError(
            "hoopR snapshot reference differs from the packaged runtime reference"
        )
    return {
        name: {
            "path": f"reference/{name}",
            "sha256": hashlib.sha256(
                inputs.payload(f"reference/{name}")
            ).hexdigest(),
        }
        for name in inputs.policy.reference_files
    }


def _reference_provenance(
    data_dir: Path,
    *,
    runtime_reference_dir: Path | None = None,
) -> dict:
    """Attest the exact deterministic references used by the build."""
    _validate_runtime_reference_binding(
        data_dir,
        runtime_reference_dir=runtime_reference_dir,
    )
    references = {}
    for name in REFERENCE_FILES:
        path = data_dir / "reference" / name
        if path.is_symlink() or not path.is_file():
            raise PlayoffValidationError(
                f"reference file must be regular: {name}"
            )
        references[name] = {
            "path": path.relative_to(data_dir).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    return references


def _validate_captured_runtime_state(
    inputs: CapturedPlayoffInputs,
) -> None:
    if capture_policy() != inputs.policy:
        raise PlayoffValidationError(
            "playoff runtime policy changed before publication"
        )
    expected_frame = franchises.load_reference_payload(
        inputs.payload("runtime_reference/franchises.csv")
    )
    if not expected_frame.equals(
        franchises.load_reference().copy(deep=True)
    ):
        raise PlayoffValidationError(
            "franchise runtime state differs from the packaged reference"
        )
    if _thaw_value(inputs.hoopr_snapshot) != _thaw_value(
        _freeze_value(hoopr.SNAPSHOT)
    ):
        raise PlayoffValidationError(
            "hoopR runtime state differs from the packaged reference"
        )


def run(data_dir: Path) -> None:
    """Build and transactionally publish the attested playoff pair."""
    inputs = _capture_input_snapshot(data_dir)
    stage_dir = None
    try:
        expected_counts = load_expected_counts(
            BytesIO(inputs.payload(
                "reference/playoff_season_counts.csv"
            )),
            policy=inputs.policy,
        )
        games, report, evidence = assemble_from_inputs(inputs)
        provenance = _raw_provenance_from_inputs(inputs)
        references = _reference_provenance_from_inputs(inputs)
        stage_dir = Path(
            tempfile.mkdtemp(prefix=".playoff-stage-", dir=data_dir)
        )
        parquet_path = stage_dir / "playoff_games.parquet"
        games.to_parquet(
            parquet_path,
            engine="pyarrow",
            compression="zstd",
            index=False,
        )
        reread = pd.read_parquet(parquet_path)
        build._assert_staged_frame(reread, games, "playoff_games")
        pd.testing.assert_frame_equal(reread, games, check_like=False)
        reread_report = validate(
            reread,
            expected_counts,
            evidence=evidence["source_rows"],
            hoopr_games=evidence["hoopr_games"],
            modern_regular=evidence["modern_regular"],
            historical_regular=evidence["historical_regular"],
            site_rule_matches=evidence["site_rule_matches"],
            policy=inputs.policy,
        )
        if reread_report != report:
            raise PlayoffValidationError(
                "playoff validation changed after Parquet re-read"
            )
        table_manifest = build._table_manifest(parquet_path, reread)
        table_manifest["row_count"] = table_manifest.pop("rows")
        table_manifest["schema"] = [
            {"name": field.name, "arrow_type": str(field.type)}
            for field in pq.read_schema(parquet_path)
        ]
        manifest = {
            "schema_version": 1,
            "sources": provenance,
            "references": references,
            "versions": _thaw_value(inputs.versions),
            "tables": {"playoff_games": table_manifest},
            "validation": reread_report,
        }
        (stage_dir / "playoff_games_manifest.json").write_text(
            json.dumps(
                manifest,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        def validate_precommit_inputs() -> None:
            inputs.verify_unchanged(
                message="playoff input changed before publication"
            )
            _validate_captured_runtime_state(inputs)

        retained_rollback = publication.publish_pair(
            stage_dir,
            data_dir / "parquet",
            inputs.policy.playoff_products,
            label="playoff",
            manifest_table="playoff_games",
            precommit=validate_precommit_inputs,
            lock_filename=inputs.policy.publication.lock_filename,
        )
        if retained_rollback is not None:
            warnings.warn(
                "playoff publication committed; prior pair retained at "
                f"{retained_rollback}",
                RuntimeWarning,
                stacklevel=2,
            )
    finally:
        if stage_dir is not None and stage_dir.exists():
            shutil.rmtree(stage_dir)
