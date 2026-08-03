"""Offline semantic validation and independent rebuild verification for the
immutable raw-plus-Parquet player-game snapshot.

This module owns every semantic check involved in archiving and restoring
the player-game snapshot: proving the complete 93-pair raw archive and the
trusted publication as two independent gates (``validate_layout``), and
proving that the publication can be deterministically rebuilt byte-for-byte
from raw evidence alone (``verify_rebuild``). Task 5's
``scripts/archive_player_games_snapshot.sh`` and
``scripts/restore_player_games_snapshot.sh`` own filesystem orchestration
only (staging, ``rsync``, checksum files, capacity gates, atomic renames)
and delegate every semantic question to the ``validate-layout`` and
``verify-rebuild`` subcommands defined here. Nothing in this module
performs network I/O.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
import shutil
import stat
import tempfile
from pathlib import Path

from nba_data import cache, player_games_build, player_logs


class PlayerGamesSnapshotError(ValueError):
    """Raised when a player-games archive or rebuild violates its contract."""


_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")

# Exception types raised by the source cache and publication validators that
# this module normalizes into ``PlayerGamesSnapshotError`` so the CLI and
# both archive/restore scripts see one stable contract.
_NORMALIZED_EXCEPTIONS = (
    OSError,
    cache.CacheIntegrityError,
    player_logs.PlayerLogValidationError,
    player_games_build.PlayerGamesBuildError,
)


def _require_valid_digest(value: object, *, label: str) -> None:
    if not isinstance(value, str) or not _DIGEST_PATTERN.fullmatch(value):
        raise PlayerGamesSnapshotError(
            f"{label} must be a 64-character lowercase hex SHA-256 digest"
        )


def _require_regular_tree(root: Path) -> None:
    """Walk ``root`` with ``lstat``, permitting only real directories and
    regular files.

    Uses ``lstat`` rather than ``stat`` so a symlink is judged by its own
    inode type, never by whatever it happens to point at, and raises with
    the offending entry's path relative to ``root`` for any other inode
    type (symlink, FIFO, socket, device).
    """
    root = Path(root)
    try:
        root_info = root.lstat()
    except FileNotFoundError as exc:
        raise PlayerGamesSnapshotError(f"missing tree root: {root}") from exc
    if not stat.S_ISDIR(root_info.st_mode):
        raise PlayerGamesSnapshotError(
            f"tree root is not a real directory: {root}"
        )

    stack = [root]
    while stack:
        current = stack.pop()
        try:
            children = sorted(current.iterdir())
        except FileNotFoundError as exc:
            raise PlayerGamesSnapshotError(
                f"tree disappeared during walk: {current}"
            ) from exc
        for child in children:
            info = child.lstat()
            relative = child.relative_to(root)
            if stat.S_ISDIR(info.st_mode):
                stack.append(child)
            elif stat.S_ISREG(info.st_mode):
                continue
            else:
                raise PlayerGamesSnapshotError(
                    f"unsafe non-regular entry in tree {root}: {relative}"
                )


# ---------------------------------------------------------------------------
# validate-layout
# ---------------------------------------------------------------------------


def validate_layout(
    raw_root: Path,
    publication_root: Path,
    reference_dir: Path,
    manifest_sha256: str | None = None,
) -> dict:
    """Prove the complete raw archive and the trusted publication as two
    independent gates, offline and self-contained.

    Fully revalidates the exact raw player-log cache inventory at
    ``raw_root`` -- every identity in the source season scope, not merely
    the identities the publication happened to consume -- and independently
    recomputes and validates the publication at ``publication_root`` from
    ``raw_root`` and ``reference_dir``. Optionally requires the published
    manifest's own SHA-256 to equal an exact, already-lowercase trusted
    digest.
    """
    raw_root = Path(raw_root)
    publication_root = Path(publication_root)
    reference_dir = Path(reference_dir)
    try:
        _require_regular_tree(raw_root)
        _require_regular_tree(publication_root)
        bodies = player_logs.validate_cache_inventory_root(raw_root)
        manifest_path = publication_root / "_manifest.json"
        observed_manifest_sha256 = hashlib.sha256(
            manifest_path.read_bytes()
        ).hexdigest()
        if manifest_sha256 is not None:
            _require_valid_digest(manifest_sha256, label="--manifest-sha256")
            if not hmac.compare_digest(
                manifest_sha256, observed_manifest_sha256
            ):
                raise PlayerGamesSnapshotError(
                    "snapshot manifest SHA-256 mismatch"
                )
        manifest = player_games_build.validate_publication(
            raw_root,
            publication_root,
            reference_dir,
        )
    except PlayerGamesSnapshotError:
        raise
    except _NORMALIZED_EXCEPTIONS as exc:
        raise PlayerGamesSnapshotError(str(exc)) from exc
    return {
        "status": "ok",
        "archive_cache_entries": len(bodies),
        "publication_cache_entries": len(manifest["sources"]["player_logs"]),
        "publication_partitions": len(manifest["partitions"]),
        "manifest_sha256": observed_manifest_sha256,
    }


# ---------------------------------------------------------------------------
# verify-rebuild
# ---------------------------------------------------------------------------


def _dir_identity(path: Path) -> tuple[int, int]:
    info = path.lstat()
    return info.st_dev, info.st_ino


def _remove_owned_stage(path: Path, expected_identity: tuple[int, int]) -> None:
    """Remove ``path`` only if it is still the exact real directory this
    process created (matched by device/inode), never anything else."""
    try:
        current = path.lstat()
    except FileNotFoundError:
        return
    if (
        stat.S_ISDIR(current.st_mode)
        and (current.st_dev, current.st_ino) == expected_identity
    ):
        shutil.rmtree(path)


def _walk_regular_files(root: Path) -> dict[str, Path]:
    """Return every regular file under ``root`` keyed by its POSIX path
    relative to ``root``, without following symlinks and rejecting any
    other special inode."""
    root = Path(root)
    result: dict[str, Path] = {}
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            children = sorted(current.iterdir())
        except FileNotFoundError as exc:
            raise PlayerGamesSnapshotError(
                f"tree disappeared during walk: {current}"
            ) from exc
        for child in children:
            info = child.lstat()
            if stat.S_ISDIR(info.st_mode):
                stack.append(child)
            elif stat.S_ISREG(info.st_mode):
                result[child.relative_to(root).as_posix()] = child
            else:
                raise PlayerGamesSnapshotError(
                    "unsafe non-regular entry while walking "
                    f"{root}: {child.relative_to(root)}"
                )
    return result


def _sorted_inventory(root: Path) -> tuple[tuple[str, str], ...]:
    files = _walk_regular_files(root)
    return tuple(
        sorted(
            (relative_path, cache.sha256_file(path))
            for relative_path, path in files.items()
        )
    )


def verify_rebuild(
    raw_root: Path,
    publication_root: Path,
    reference_dir: Path,
) -> dict:
    """Rebuild the publication from raw evidence in a temp stage and prove
    byte identity with the already-published tree.

    Builds into a uniquely owned temporary directory created as a sibling
    of ``publication_root`` (never inside it), compares the complete sorted
    ``(relative_path, sha256)`` inventory of the rebuild against the
    published tree, and removes only that owned stage in ``finally`` --
    whether the comparison passes or fails.
    """
    raw_root = Path(raw_root)
    publication_root = Path(publication_root)
    reference_dir = Path(reference_dir)

    try:
        stage_dir = Path(
            tempfile.mkdtemp(
                prefix=".player-games-verify-rebuild-",
                dir=publication_root.parent,
            )
        )
    except OSError as exc:
        raise PlayerGamesSnapshotError(
            f"could not create rebuild stage next to {publication_root}: {exc}"
        ) from exc

    identity = _dir_identity(stage_dir)
    try:
        try:
            player_games_build.build_from_roots(
                raw_root, reference_dir, stage_dir
            )
            rebuilt_inventory = _sorted_inventory(stage_dir)
            published_inventory = _sorted_inventory(publication_root)
        except PlayerGamesSnapshotError:
            raise
        except _NORMALIZED_EXCEPTIONS as exc:
            raise PlayerGamesSnapshotError(str(exc)) from exc

        if rebuilt_inventory != published_inventory:
            rebuilt_map = dict(rebuilt_inventory)
            published_map = dict(published_inventory)
            missing = sorted(set(published_map) - set(rebuilt_map))
            extra = sorted(set(rebuilt_map) - set(published_map))
            drifted = sorted(
                path
                for path in set(rebuilt_map) & set(published_map)
                if rebuilt_map[path] != published_map[path]
            )
            raise PlayerGamesSnapshotError(
                "rebuilt publication differs from the published tree: "
                f"missing={missing}, extra={extra}, drifted={drifted}"
            )
    finally:
        _remove_owned_stage(stage_dir, identity)

    return {
        "status": "ok",
        "publication_files": len(rebuilt_inventory),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m nba_data.player_games_snapshot"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    layout = commands.add_parser("validate-layout")
    layout.add_argument("--raw-root", type=Path, required=True)
    layout.add_argument("--publication-root", type=Path, required=True)
    layout.add_argument("--reference-dir", type=Path, required=True)
    layout.add_argument("--manifest-sha256", default=None)

    rebuild = commands.add_parser("verify-rebuild")
    rebuild.add_argument("--raw-root", type=Path, required=True)
    rebuild.add_argument("--publication-root", type=Path, required=True)
    rebuild.add_argument("--reference-dir", type=Path, required=True)

    return parser


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    if args.command == "validate-layout":
        report = validate_layout(
            args.raw_root,
            args.publication_root,
            args.reference_dir,
            args.manifest_sha256,
        )
    elif args.command == "verify-rebuild":
        report = verify_rebuild(
            args.raw_root,
            args.publication_root,
            args.reference_dir,
        )
    else:  # pragma: no cover - argparse enforces valid choices
        raise AssertionError(f"unknown command {args.command!r}")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
