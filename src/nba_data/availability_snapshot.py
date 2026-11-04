"""Offline validation of the immutable availability snapshot.

The snapshot holds the four raw trees the availability build reads
(``raw/stats_nba/box_rosters``, ``raw/nba_injury_reports``,
``raw/sportsdataverse/nba_stats_injury_report``, ``raw/nba_gamebooks``) and
the publication ``parquet-large/availability``; the ``player_games``
publication it was built from is not inside it and is restored first from
the D1 snapshot named in the manifest. ``validate_layout`` proves one such
layout in four independent gates:

1. every lstat-visible entry of the four raw subtrees is a real directory
   or regular file, and every body/sidecar pair verifies through
   ``cache.read_attested`` with the owning module's validator (box rosters by
   endpoint, report PDFs by signature, every inventory generation of every
   month -- not only the highest -- by the generation validator, derivative
   assets by column set, gamebooks by signature, comparison records as JSON
   objects); an orphan body, an orphan sidecar, a stray ``.part`` file or an
   unrecognised name fails;
2. ``box_rosters.validate_cache_inventory`` passes for the exact worklist of
   the ``player_games`` publication;
3. ``availability_build.validate_publication`` recomputes the publication
   from the raw trees, the ``player_games`` publication and the ledger and
   compares it with the stored manifest key by key;
4. the manifest's own SHA-256 equals the optional trusted digest.

``scripts/archive_availability_snapshot.sh`` and
``scripts/restore_availability_snapshot.sh`` own the filesystem side
(staging, ``rsync``, ``SHA256SUMS``, capacity, atomic renames) and delegate
every semantic question to the ``validate-layout`` subcommand defined here.
Nothing in this module performs network I/O.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
import stat
import sys
from pathlib import Path

from nba_data import (
    availability_build,
    availability_resolve,
    box_rosters,
    cache,
    gamebooks,
    injury_derivative,
    injury_reports,
)


class AvailabilitySnapshotError(ValueError):
    """Raised when an availability snapshot layout violates its contract."""


_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SIDECAR_SUFFIX = ".meta.json"
_SEASON_DIR_PATTERN = re.compile(r"^season=(\d{4})$")
_BOX_BODY_PATTERN = re.compile(r"^(\d{10})\.(traditional|summary)\.json$")
_PDF_BODY_PATTERN = re.compile(r"^(Injury-Report_[0-9_A-Z-]+)\.pdf$")
_DERIVATIVE_BODY_PATTERN = re.compile(r"^injury_report_(\d{4})\.parquet$")
_GAMEBOOK_BODY_PATTERN = re.compile(r"^(\d{10})\.book\.pdf$")
_COMPARISON_BODY_PATTERN = re.compile(r"^(\d{10})\.json$")

# The four raw subtrees, relative to ``raw_root``, taken from the owning
# modules' ``RAW_SUBDIR`` constants (each starts with ``raw/``).
RAW_SUBTREES = {
    "box_rosters": Path(box_rosters.RAW_SUBDIR).relative_to("raw"),
    "injury_reports": Path(injury_reports.RAW_SUBDIR).relative_to("raw"),
    "derivative": Path(injury_derivative.RAW_SUBDIR).relative_to("raw"),
    "gamebooks": Path(gamebooks.RAW_SUBDIR).relative_to("raw"),
}

# Exceptions raised by the source caches, the raw validators and the
# publication validator that this module normalises into
# ``AvailabilitySnapshotError`` so the CLI and both scripts see one contract.
_NORMALIZED_EXCEPTIONS = (
    OSError,
    UnicodeDecodeError,
    json.JSONDecodeError,
    cache.CacheIntegrityError,
    box_rosters.BoxRosterValidationError,
    injury_reports.InjuryReportError,
    injury_derivative.InjuryDerivativeError,
    gamebooks.GamebookError,
    availability_resolve.AvailabilityResolveError,
    availability_build.AvailabilityBuildError,
)


def _require_valid_digest(value: object, *, label: str) -> None:
    if not isinstance(value, str) or not _DIGEST_PATTERN.fullmatch(value):
        raise AvailabilitySnapshotError(
            f"{label} must be a 64-character lowercase hex SHA-256 digest"
        )


def _walk_regular_files(root: Path, *, label: str) -> dict[str, Path]:
    """Walk ``root`` with ``lstat`` and return every regular file keyed by
    its POSIX path relative to ``root``.

    Uses ``lstat`` rather than ``stat`` so a symlink is judged by its own
    inode type, never by whatever it happens to point at, and raises for
    any other inode type (symlink, FIFO, socket, device).
    """
    root = Path(root)
    try:
        root_info = root.lstat()
    except FileNotFoundError as exc:
        raise AvailabilitySnapshotError(f"missing raw subtree {label}: {root}") from exc
    if not stat.S_ISDIR(root_info.st_mode):
        raise AvailabilitySnapshotError(
            f"raw subtree {label} is not a real directory: {root}"
        )
    result: dict[str, Path] = {}
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            children = sorted(current.iterdir())
        except FileNotFoundError as exc:
            raise AvailabilitySnapshotError(
                f"tree disappeared during walk: {current}"
            ) from exc
        for child in children:
            info = child.lstat()
            relative = child.relative_to(root).as_posix()
            if stat.S_ISDIR(info.st_mode):
                stack.append(child)
            elif stat.S_ISREG(info.st_mode):
                result[relative] = child
            else:
                raise AvailabilitySnapshotError(
                    f"unsafe non-regular entry in raw subtree {label}: {relative}"
                )
    return result


def _pair_bodies(files: dict[str, Path], *, label: str) -> list[str]:
    """Every body of ``files`` whose sidecar is present; an orphan fails."""
    bodies = [name for name in files if not name.endswith(_SIDECAR_SUFFIX)]
    sidecars = {name for name in files if name.endswith(_SIDECAR_SUFFIX)}
    for name in sorted(bodies):
        if name + _SIDECAR_SUFFIX not in sidecars:
            raise AvailabilitySnapshotError(
                f"orphan raw body without sidecar in {label}: {name}"
            )
    for name in sorted(sidecars):
        if name[: -len(_SIDECAR_SUFFIX)] not in files:
            raise AvailabilitySnapshotError(
                f"orphan raw sidecar without body in {label}: {name}"
            )
    return sorted(bodies)


def _unexpected(label: str, name: str) -> AvailabilitySnapshotError:
    return AvailabilitySnapshotError(f"unexpected raw entry in {label}: {name}")


def _attested(path: Path, validator, *, label: str) -> tuple[bytes, dict]:
    attestation = cache.read_attested(path, validator)
    if attestation is None:   # pragma: no cover - the walk saw both files
        raise AvailabilitySnapshotError(f"raw pair vanished while attesting {label}: {path}")
    return attestation


def _require_identity(metadata: dict, expected: dict, path: Path) -> None:
    for key, value in expected.items():
        if key not in metadata or metadata[key] != value:
            raise AvailabilitySnapshotError(
                f"raw sidecar identity mismatch for {path}: {key}"
            )


def _attest_box_rosters(root: Path, files: dict[str, Path]) -> int:
    label = "box_rosters"
    count = 0
    for name in _pair_bodies(files, label=label):
        parts = name.split("/")
        if len(parts) != 2:
            raise _unexpected(label, name)
        season_match = _SEASON_DIR_PATTERN.match(parts[0])
        body_match = _BOX_BODY_PATTERN.match(parts[1])
        if season_match is None or body_match is None:
            raise _unexpected(label, name)
        season = int(season_match.group(1))
        game_id, endpoint = body_match.group(1), body_match.group(2)
        validator = (
            box_rosters.validate_traditional if endpoint == "traditional"
            else box_rosters.validate_summary
        )
        path = files[name]
        payload, metadata = _attested(
            path, lambda candidate: validator(candidate, game_id=game_id), label=label,
        )
        _require_identity(
            metadata,
            {
                "provider": "stats_nba",
                "resource": "box_rosters",
                "endpoint": box_rosters.ENDPOINT_CLASSES[endpoint],
                "game_id": game_id,
                "season": season,
                "content_length": len(payload),
            },
            path,
        )
        count += 1
    return count


def _attest_injury_reports(data_dir: Path, root: Path, files: dict[str, Path]) -> tuple[int, int]:
    label = "injury_reports"
    pdfs = 0
    generation_months: dict[str, int] = {}
    for name in _pair_bodies(files, label=label):
        parts = name.split("/")
        if len(parts) != 3:
            raise _unexpected(label, name)
        kind, month, file_name = parts
        try:
            injury_reports.validate_month(month)
        except injury_reports.InjuryReportError as exc:
            raise _unexpected(label, name) from exc
        path = files[name]
        if kind == "pdfs":
            match = _PDF_BODY_PATTERN.match(file_name)
            if match is None:
                raise _unexpected(label, name)
            slot = injury_reports.slot_from_report_id(match.group(1))
            if slot.month != month or slot.file_name != file_name:
                raise _unexpected(label, name)
            payload, metadata = _attested(path, injury_reports.validate_pdf, label=label)
            _require_identity(
                metadata,
                {
                    "provider": "nba_official",
                    "resource": "injury_report",
                    "report_id": slot.report_id,
                    "content_length": len(payload),
                },
                path,
            )
            pdfs += 1
        elif kind == "inventory":
            if injury_reports.GENERATION_PATTERN.match(file_name) is None:
                raise _unexpected(label, name)
            generation_months[month] = generation_months.get(month, 0) + 1
        else:
            raise _unexpected(label, name)
    generations = 0
    for month in sorted(generation_months):
        current, damaged = injury_reports.read_current_inventory(data_dir, month)
        if current is None or damaged:
            names = sorted(path.name for path in damaged) or ["(none verify)"]
            raise AvailabilitySnapshotError(
                f"inventory month {month}: generations do not verify: {', '.join(names)}"
            )
        generations += generation_months[month]
    return pdfs, generations


def _attest_derivative(data_dir: Path, root: Path, files: dict[str, Path]) -> int:
    label = "derivative"
    count = 0
    for name in _pair_bodies(files, label=label):
        match = _DERIVATIVE_BODY_PATTERN.match(name)
        if match is None:
            raise _unexpected(label, name)
        season = int(match.group(1))
        if season not in injury_derivative.SEASONS:
            raise _unexpected(label, name)
        if injury_derivative.read_cached(data_dir, season) is None:   # pragma: no cover
            raise AvailabilitySnapshotError(f"raw pair vanished while attesting {label}: {name}")
        count += 1
    return count


def _attest_gamebooks(data_dir: Path, root: Path, files: dict[str, Path]) -> tuple[int, int]:
    label = "gamebooks"
    pdfs = 0
    comparisons = 0
    for name in _pair_bodies(files, label=label):
        parts = name.split("/")
        if len(parts) == 1:
            match = _GAMEBOOK_BODY_PATTERN.match(parts[0])
            if match is None:
                raise _unexpected(label, name)
            if gamebooks.read_cached_gamebook(data_dir, match.group(1)) is None:   # pragma: no cover
                raise AvailabilitySnapshotError(f"raw pair vanished while attesting {label}: {name}")
            pdfs += 1
        elif len(parts) == 2 and parts[0] == gamebooks.COMPARISONS_SUBDIR:
            match = _COMPARISON_BODY_PATTERN.match(parts[1])
            if match is None:
                raise _unexpected(label, name)
            if gamebooks.read_cached_comparison(data_dir, match.group(1)) is None:   # pragma: no cover
                raise AvailabilitySnapshotError(f"raw pair vanished while attesting {label}: {name}")
            comparisons += 1
        else:
            raise _unexpected(label, name)
    return pdfs, comparisons


def _attest_raw_trees(raw_root: Path) -> dict[str, int]:
    """Gate 1: walk and attest every pair of the four raw subtrees."""
    data_dir = raw_root.parent
    trees = {
        label: _walk_regular_files(raw_root / relative, label=label)
        for label, relative in RAW_SUBTREES.items()
    }
    box_pairs = _attest_box_rosters(raw_root / RAW_SUBTREES["box_rosters"], trees["box_rosters"])
    pdfs, generations = _attest_injury_reports(
        data_dir, raw_root / RAW_SUBTREES["injury_reports"], trees["injury_reports"],
    )
    derivative = _attest_derivative(
        data_dir, raw_root / RAW_SUBTREES["derivative"], trees["derivative"],
    )
    gamebook_pdfs, comparisons = _attest_gamebooks(
        data_dir, raw_root / RAW_SUBTREES["gamebooks"], trees["gamebooks"],
    )
    return {
        "box_rosters": box_pairs,
        "injury_report_pdfs": pdfs,
        "injury_report_generations": generations,
        "derivative": derivative,
        "gamebook_pdfs": gamebook_pdfs,
        "gamebook_comparisons": comparisons,
    }


def validate_layout(
    raw_root: Path,
    publication_root: Path,
    player_games_root: Path,
    manifest_sha256: str | None = None,
) -> dict:
    """Prove the four raw subtrees and the publication, offline.

    ``raw_root`` is ``data_dir / "raw"`` (the raw modules address pairs from
    ``data_dir``). Every pair under the four raw subtrees is attested, the
    box-roster inventory is checked against the exact ``player_games``
    worklist, the publication is recomputed and compared with its stored
    manifest, and the manifest's own SHA-256 is compared with the optional
    trusted digest. Returns the recomputed manifest with the counts.
    """
    raw_root = Path(raw_root)
    publication_root = Path(publication_root)
    player_games_root = Path(player_games_root)
    if manifest_sha256 is not None:
        _require_valid_digest(manifest_sha256, label="--manifest-sha256")
    if raw_root.name != "raw":
        raise AvailabilitySnapshotError(
            f"raw root must be a directory named 'raw' (data_dir / 'raw'): {raw_root}"
        )
    try:
        raw_pairs = _attest_raw_trees(raw_root)
        index = box_rosters.load_player_games_index(player_games_root)
        bodies = box_rosters.validate_cache_inventory(raw_root.parent, index.games)
        manifest = availability_build.validate_publication(
            raw_root,
            player_games_root,
            publication_root,
            exceptions_path=availability_build.EXCEPTIONS_PATH,
        )
        observed_manifest_sha256 = hashlib.sha256(
            (publication_root / "_manifest.json").read_bytes()
        ).hexdigest()
    except AvailabilitySnapshotError:
        raise
    except _NORMALIZED_EXCEPTIONS as exc:
        raise AvailabilitySnapshotError(str(exc)) from exc
    if manifest_sha256 is not None and not hmac.compare_digest(
        manifest_sha256, observed_manifest_sha256
    ):
        raise AvailabilitySnapshotError("snapshot manifest SHA-256 mismatch")
    return {
        "status": "ok",
        "manifest_sha256": observed_manifest_sha256,
        "raw_pairs": raw_pairs,
        "box_roster_pairs": len(bodies),
        "publication_partitions": sum(
            len(entries) for entries in manifest["partitions"].values()
        ),
        "manifest": manifest,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m nba_data.availability_snapshot"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    layout = commands.add_parser("validate-layout")
    layout.add_argument("--raw-root", type=Path, required=True)
    layout.add_argument("--publication-root", type=Path, required=True)
    layout.add_argument("--player-games-root", type=Path, required=True)
    layout.add_argument("--manifest-sha256", default=None)

    return parser


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    if args.command == "validate-layout":
        try:
            report = validate_layout(
                args.raw_root,
                args.publication_root,
                args.player_games_root,
                args.manifest_sha256,
            )
        except AvailabilitySnapshotError as exc:
            print(f"validate-layout failed: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
    else:  # pragma: no cover - argparse enforces valid choices
        raise AssertionError(f"unknown command {args.command!r}")
    # The recomputed manifest binds every raw pair (tens of thousands at full
    # scale); the scripts need only the summary, so it is not printed.
    summary = {key: value for key, value in report.items() if key != "manifest"}
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
