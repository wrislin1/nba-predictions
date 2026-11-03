#!/usr/bin/env python3
"""Render offline player-log source review or final publication evidence."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import stat

from nba_data import player_games_build


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="audit_player_logs.py")
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--final-report", type=Path)
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--snapshot-name")
    parser.add_argument("--restore-verified", action="store_true")
    return parser


def _write_final_report(path: Path, rendered: str) -> None:
    try:
        observed = path.lstat()
    except FileNotFoundError:
        observed = None
    if observed is not None and not stat.S_ISREG(observed.st_mode):
        raise player_games_build.PlayerGamesBuildError(
            f"final report target must be an absent or regular file: {path}"
        )

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    nonblock = getattr(os, "O_NONBLOCK", 0)
    if observed is None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow
    else:
        flags = os.O_WRONLY | nofollow | nonblock
    try:
        descriptor = os.open(path, flags, 0o644)
    except OSError as exc:
        raise player_games_build.PlayerGamesBuildError(
            f"final report target could not be opened safely: {path}"
        ) from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise player_games_build.PlayerGamesBuildError(
                f"final report target must be a regular file: {path}"
            )
        os.ftruncate(descriptor, 0)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            stream.write(rendered)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def main(argv: list[str] | None = None) -> None:
    parser = _parser()
    args = parser.parse_args(argv)
    data_dir = args.data_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser()
    final_values = (
        args.final_report,
        args.manifest_sha256,
        args.snapshot_name,
        args.restore_verified,
    )
    final_requested = any(bool(value) for value in final_values)
    if final_requested and not all(bool(value) for value in final_values):
        parser.error(
            "--final-report, --manifest-sha256, --snapshot-name, and "
            "--restore-verified must be supplied together"
        )

    if not final_requested:
        audit = player_games_build.audit_source(data_dir)
        player_games_build.write_source_review(output_dir, audit)
        return

    publication_dir = data_dir / "parquet-large/player_games"
    manifest = player_games_build.validate_publication(
        data_dir / "raw/stats_nba/player_logs",
        publication_dir,
        data_dir / "parquet",
    )
    manifest_path = publication_dir / "_manifest.json"
    observed_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    if observed_sha256 != args.manifest_sha256:
        raise player_games_build.PlayerGamesBuildError(
            "final manifest/hash disagreement: "
            f"expected {args.manifest_sha256}, observed {observed_sha256}"
        )
    rendered = player_games_build.render_final_audit(
        manifest,
        manifest_sha256=args.manifest_sha256,
        snapshot_name=args.snapshot_name,
        restore_verified=args.restore_verified,
    )
    final_report = args.final_report.expanduser()
    _write_final_report(final_report, rendered)


if __name__ == "__main__":
    main()
