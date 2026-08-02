"""Offline semantic validation, rebuild-comparison, and audit rendering for
the immutable raw-plus-Parquet play-by-play snapshot.

This module owns every JSON/Parquet semantic check involved in archiving and
restoring the play-by-play snapshot. ``scripts/archive_pbp_snapshot.sh`` and
``scripts/restore_pbp_snapshot.sh`` own filesystem orchestration only
(staging, ``rsync``, checksum files, capacity gates, atomic renames) and
delegate every semantic question to the three subcommands defined here:
``validate-layout``, ``rebuild-compare``, and ``write-audit``. Nothing in
this module performs network I/O.
"""

from __future__ import annotations

import argparse
import hmac
import json
import re
import shutil
import stat
import tempfile
from pathlib import Path

from nba_data import cache, pbp, pbp_build


class PBPSnapshotError(ValueError):
    """Raised when snapshot layout, rebuild comparison, or audit evidence
    violates its contract."""


_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _require_valid_digest(value: object, *, label: str) -> None:
    if not isinstance(value, str) or not _DIGEST_PATTERN.fullmatch(value):
        raise PBPSnapshotError(
            f"{label} must be a 64-character lowercase hex SHA-256 digest"
        )


def _read_manifest(publication_root: Path) -> dict:
    manifest_path = Path(publication_root) / "_manifest.json"
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise PBPSnapshotError(f"missing manifest: {manifest_path}") from exc
    try:
        manifest = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PBPSnapshotError(
            f"publication manifest is not valid JSON: {manifest_path}"
        ) from exc
    if not isinstance(manifest, dict):
        raise PBPSnapshotError(
            f"publication manifest must contain an object: {manifest_path}"
        )
    return manifest


# ---------------------------------------------------------------------------
# validate-layout
# ---------------------------------------------------------------------------


def validate_layout(
    raw_root: Path, publication_root: Path, *, manifest_sha256: str | None
) -> None:
    """Fully revalidate one raw/publication pair, offline and self-contained.

    Runs the same complete cache-tree and publication checks used to build
    the manifest in the first place, so a raw-plus-Parquet pair at any
    location (the live repository data directory, a freshly staged archive
    copy, a staged restore copy, or an already-published snapshot) is
    revalidated identically. Optionally requires the publication manifest's
    own SHA-256 to equal an exact, already-lowercase trusted digest.
    """
    raw_root = Path(raw_root)
    publication_root = Path(publication_root)
    pbp.validate_cache_tree(
        raw_root, required_seasons=pbp.SEASONS, require_complete=True
    )
    pbp_build.validate_publication(raw_root, publication_root)
    if manifest_sha256 is not None:
        _require_valid_digest(manifest_sha256, label="--manifest-sha256")
        manifest_path = publication_root / "_manifest.json"
        actual = cache.sha256_file(manifest_path)
        if not hmac.compare_digest(actual, manifest_sha256):
            raise PBPSnapshotError(
                "publication manifest digest does not match the trusted "
                f"pin: {manifest_path}"
            )


# ---------------------------------------------------------------------------
# rebuild-compare
# ---------------------------------------------------------------------------


def _dir_identity(path: Path) -> tuple[int, int]:
    info = path.lstat()
    return info.st_dev, info.st_ino


def _remove_verified_temp_dir(path: Path, expected_identity: tuple[int, int]) -> None:
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
    """Return every regular file under ``root`` keyed by POSIX-relative
    path, without following symlinks and rejecting any special inode."""
    root = Path(root)
    result: dict[str, Path] = {}
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            children = sorted(current.iterdir())
        except FileNotFoundError as exc:
            raise PBPSnapshotError(f"tree disappeared during walk: {current}") from exc
        for child in children:
            info = child.lstat()
            if stat.S_ISDIR(info.st_mode):
                stack.append(child)
            elif stat.S_ISREG(info.st_mode):
                result[child.relative_to(root).as_posix()] = child
            else:
                raise PBPSnapshotError(
                    f"unexpected non-regular entry while walking {root}: {child}"
                )
    return result


def _require_identical_trees(rebuilt_root: Path, publication_root: Path) -> None:
    rebuilt_files = _walk_regular_files(rebuilt_root)
    published_files = _walk_regular_files(publication_root)
    if set(rebuilt_files) != set(published_files):
        missing = sorted(set(published_files) - set(rebuilt_files))
        extra = sorted(set(rebuilt_files) - set(published_files))
        raise PBPSnapshotError(
            "rebuild produced a different file set than the publication: "
            f"missing={missing}, extra={extra}"
        )
    mismatched = []
    for relative_path in sorted(rebuilt_files):
        rebuilt_bytes = rebuilt_files[relative_path].read_bytes()
        published_bytes = published_files[relative_path].read_bytes()
        if rebuilt_bytes != published_bytes:
            mismatched.append(relative_path)
    if mismatched:
        raise PBPSnapshotError(
            f"rebuild is not byte-identical to the publication at: {mismatched}"
        )


def _require_versions_match(manifest_versions: object) -> None:
    if not isinstance(manifest_versions, dict):
        raise PBPSnapshotError(
            "publication manifest is missing a 'versions' object"
        )
    installed = pbp_build._versions()
    if installed != manifest_versions:
        raise PBPSnapshotError(
            "installed Python/package versions do not match the pinned "
            f"manifest versions: installed={installed}, "
            f"manifest={manifest_versions}"
        )


def rebuild_compare(raw_root: Path, publication_root: Path, stage_parent: Path) -> None:
    """Deterministically rebuild ``raw_root`` and require an exact,
    byte-for-byte match with the already-published ``publication_root``.

    Requires the installed Python and package versions to equal the
    publication manifest's pinned ``versions`` object before any bytes are
    compared -- a version mismatch makes a rebuild diff meaningless. Builds
    into a unique real child of ``stage_parent`` and removes only that
    verified temporary child, whether the comparison passes or fails.
    """
    raw_root = Path(raw_root)
    publication_root = Path(publication_root)
    stage_parent = Path(stage_parent)

    manifest = _read_manifest(publication_root)
    _require_versions_match(manifest.get("versions"))

    temp_child = Path(
        tempfile.mkdtemp(prefix=".pbp-rebuild-compare-", dir=stage_parent)
    )
    identity = _dir_identity(temp_child)
    try:
        pbp_build.build_tree(raw_root, temp_child, seasons=pbp.SEASONS)
        pbp_build.validate_publication(raw_root, temp_child)
        _require_identical_trees(temp_child, publication_root)
    finally:
        _remove_verified_temp_dir(temp_child, identity)


# ---------------------------------------------------------------------------
# write-audit
# ---------------------------------------------------------------------------


_SHA256SUMS_LINE = re.compile(r"^(?P<digest>[0-9a-f]{64})  (?P<path>.+)$")


def _parse_sha256sums(sums_path: Path) -> dict[str, str]:
    """Parse a ``SHA256SUMS`` file under the exact grammar the archive and
    restore scripts require: ``<64 lowercase hex><two spaces><normalized
    POSIX relative path>``, one entry per regular file, no duplicates, no
    absolute paths, and no empty/dot/dot-dot/backslash path components."""
    text = sums_path.read_text(encoding="utf-8")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    entries: dict[str, str] = {}
    for line_number, line in enumerate(lines, start=1):
        match = _SHA256SUMS_LINE.fullmatch(line)
        if match is None:
            raise PBPSnapshotError(
                f"malformed SHA256SUMS line {line_number}: {line!r}"
            )
        digest = match.group("digest")
        path = match.group("path")
        if path.startswith("/") or "\\" in path:
            raise PBPSnapshotError(
                f"unsafe SHA256SUMS path at line {line_number}: {path!r}"
            )
        parts = path.split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise PBPSnapshotError(
                f"unsafe SHA256SUMS path at line {line_number}: {path!r}"
            )
        if path in entries:
            raise PBPSnapshotError(f"duplicate SHA256SUMS entry: {path!r}")
        entries[path] = digest
    return entries


def _verify_snapshot_checksum_inventory(snapshot: Path) -> None:
    snapshot = Path(snapshot)
    sums_path = snapshot / "SHA256SUMS"
    try:
        info = sums_path.lstat()
    except FileNotFoundError as exc:
        raise PBPSnapshotError(f"missing SHA256SUMS: {sums_path}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise PBPSnapshotError(f"SHA256SUMS must be a regular file: {sums_path}")

    entries = _parse_sha256sums(sums_path)
    inventory = _walk_regular_files(snapshot)
    inventory.pop("SHA256SUMS", None)
    if set(entries) != set(inventory):
        missing = sorted(set(inventory) - set(entries))
        extra = sorted(set(entries) - set(inventory))
        raise PBPSnapshotError(
            "SHA256SUMS entries do not match the snapshot's on-disk "
            f"inventory: missing={missing}, extra={extra}"
        )
    for relative_path, expected_digest in entries.items():
        actual_digest = cache.sha256_file(inventory[relative_path])
        if not hmac.compare_digest(actual_digest, expected_digest):
            raise PBPSnapshotError(
                f"SHA256SUMS checksum mismatch for {relative_path}"
            )


def _derive_source_spike_rows(raw_root: Path, worklist) -> list[dict]:
    raw_root = Path(raw_root)
    by_game_id = {str(row["game_id"]): row for _, row in worklist.iterrows()}
    rows: list[dict] = []
    for game_id in pbp.SOURCE_SPIKE_GAME_IDS:
        worklist_row = by_game_id.get(game_id)
        if worklist_row is None:
            raise PBPSnapshotError(
                f"source-spike game is absent from the live worklist: {game_id}"
            )
        season = int(worklist_row["season"])
        body_path = raw_root / "games" / str(season) / f"{game_id}.json"
        payload = cache.require_valid(
            body_path,
            lambda path, gid=game_id: pbp.validate_game_transport(path, game_id=gid),
        )
        _frame, report = pbp_build.parse_game_payload(
            payload, worklist_row.to_dict()
        )
        rows.append({
            "game_id": game_id,
            "response_bytes": len(payload),
            "event_count": report["events"],
            "max_period": report["max_period"],
            "terminal_home_score": report["terminal_scores"]["home"],
            "terminal_away_score": report["terminal_scores"]["away"],
        })
    return rows


def write_audit(
    data_dir: Path,
    snapshot: Path,
    manifest_sha256: str,
    output: Path,
    *,
    reproducible_build_verified: bool,
    restore_verified: bool,
) -> None:
    """Independently reverify every claim an audit is about to make, then
    render the final deterministic Markdown audit.

    Verifies the archived snapshot's checksum inventory and manifest pin,
    revalidates the live repository cache/publication and its cross-source
    equality with the published game tables, derives the four source-spike
    evidence rows straight from the live raw cache, and requires both
    execution-evidence flags before ``pbp_build.render_audit`` is allowed to
    claim reproducibility or a verified restore.
    """
    data_dir = Path(data_dir)
    snapshot = Path(snapshot)
    output = Path(output)
    _require_valid_digest(manifest_sha256, label="--manifest-sha256")

    _verify_snapshot_checksum_inventory(snapshot)
    snapshot_manifest_path = snapshot / "parquet-large" / "pbp" / "_manifest.json"
    try:
        snapshot_manifest_sha256 = cache.sha256_file(snapshot_manifest_path)
    except FileNotFoundError as exc:
        raise PBPSnapshotError(
            f"missing snapshot manifest: {snapshot_manifest_path}"
        ) from exc
    if not hmac.compare_digest(snapshot_manifest_sha256, manifest_sha256):
        raise PBPSnapshotError(
            "trusted manifest digest does not match the snapshot manifest"
        )

    raw_root = data_dir / "raw/stats_nba/pbp"
    publication_root = data_dir / "parquet-large/pbp"
    inventory = pbp.validate_cache_tree(
        raw_root, required_seasons=pbp.SEASONS, require_complete=True
    )
    pbp_build.validate_publication(raw_root, publication_root)
    pbp.cross_validate_worklist(inventory.worklist, data_dir, pbp.SEASONS)

    manifest = _read_manifest(publication_root)
    source_spike = _derive_source_spike_rows(raw_root, inventory.worklist)

    evidence = pbp_build.AuditEvidence(
        source_spike=source_spike,
        cross_source_validated=True,
        reproducible_build=reproducible_build_verified,
        snapshot_checksums_verified=True,
        restore_verified=restore_verified,
    )
    rendered = pbp_build.render_audit(
        manifest,
        evidence,
        manifest_sha256=manifest_sha256,
        snapshot_name=snapshot.name,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m nba_data.pbp_snapshot")
    commands = parser.add_subparsers(dest="command", required=True)

    layout = commands.add_parser("validate-layout")
    layout.add_argument("--raw-root", type=Path, required=True)
    layout.add_argument("--publication-root", type=Path, required=True)
    layout.add_argument("--manifest-sha256", default=None)

    rebuild = commands.add_parser("rebuild-compare")
    rebuild.add_argument("--raw-root", type=Path, required=True)
    rebuild.add_argument("--publication-root", type=Path, required=True)
    rebuild.add_argument("--stage-parent", type=Path, required=True)

    audit = commands.add_parser("write-audit")
    audit.add_argument("--data-dir", type=Path, required=True)
    audit.add_argument("--snapshot", type=Path, required=True)
    audit.add_argument("--manifest-sha256", required=True)
    audit.add_argument("--output", type=Path, required=True)
    audit.add_argument(
        "--reproducible-build-verified", action="store_true", default=False
    )
    audit.add_argument(
        "--restore-verified", action="store_true", default=False
    )

    return parser


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    if args.command == "validate-layout":
        validate_layout(
            args.raw_root,
            args.publication_root,
            manifest_sha256=args.manifest_sha256,
        )
    elif args.command == "rebuild-compare":
        rebuild_compare(args.raw_root, args.publication_root, args.stage_parent)
    elif args.command == "write-audit":
        write_audit(
            args.data_dir,
            args.snapshot,
            args.manifest_sha256,
            args.output,
            reproducible_build_verified=args.reproducible_build_verified,
            restore_verified=args.restore_verified,
        )
    else:  # pragma: no cover - argparse enforces valid choices
        raise AssertionError(f"unknown command {args.command!r}")


if __name__ == "__main__":
    main()
