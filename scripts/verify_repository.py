#!/usr/bin/env python3
"""Verify the repository's portable, analysis-first layout."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


REQUIRED_ARCHIVE_FILES = {
    "archive/dashboard/start.py",
    "archive/dashboard/templates/dashboard.html",
    "archive/notebooks/Data Retrieval and Transformation.ipynb",
    "archive/notebooks/NBA Data Automated.ipynb",
    "archive/notebooks/NBA ML.ipynb",
    "PROJECT_REVIEW.md",
    "PROJECT_DATA_FINDINGS.md",
}

REQUIRED_DASHBOARD_IMAGES = {
    f"archive/dashboard/static/img/{name}_era_averages.png"
    for name in (
        "ATTENDANCE",
        "AWAY_FG3A",
        "AWAY_FG3M",
        "AWAY_FGA",
        "AWAY_FGM",
        "AWAY_FTA",
        "AWAY_FTM",
        "AWAY_PF",
        "AWAY_PTS",
        "HOME_FG3A",
        "HOME_FG3M",
        "HOME_FGA",
        "HOME_FGM",
        "HOME_FTA",
        "HOME_FTM",
        "HOME_PF",
        "HOME_PTS",
    )
}

REQUIRED_GITIGNORE_RULES = {
    ".venv/",
    "venv/",
    "data/raw/",
    "data/.parquet-publication.lock",
    "data/parquet-large/",
    "nba-stats/",
    "*.duckdb",
    ".serena/",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-git-mib", type=int, default=100)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    errors: list[str] = []

    fsck = subprocess.run(
        ["git", "-C", root, "fsck", "--full"],
        check=False,
        capture_output=True,
        text=True,
    )
    if fsck.returncode != 0:
        detail = fsck.stderr.strip() or fsck.stdout.strip() or "no diagnostic output"
        errors.append(f"git fsck --full failed: {detail}")

    ls_files = subprocess.run(
        ["git", "-C", root, "ls-files"],
        check=False,
        capture_output=True,
        text=True,
    )
    tracked = ls_files.stdout.splitlines()
    if ls_files.returncode != 0:
        detail = ls_files.stderr.strip() or "no diagnostic output"
        errors.append(f"git ls-files failed: {detail}")
    else:
        for path in tracked:
            if path == "venv" or path.startswith("venv/"):
                errors.append(
                    f"tracked generated environment path: {path}; "
                    "remove venv/ from the index and history"
                )
            if path.startswith("data/raw/") or path.startswith("data/parquet-large/"):
                errors.append(
                    f"tracked generated data path: {path}; "
                    "remove data/raw and data/parquet-large from the index"
                )

    required_artifacts = REQUIRED_ARCHIVE_FILES | REQUIRED_DASHBOARD_IMAGES
    for relative_path in sorted(required_artifacts):
        if not (root / relative_path).is_file():
            errors.append(
                f"missing required preserved artifact: {relative_path}; "
                "restore it from the verified archive"
            )

    requirements_path = root / "requirements.txt"
    try:
        requirements_raw = requirements_path.read_bytes()
    except OSError as exc:
        errors.append(f"cannot read requirements.txt: {exc}")
    else:
        if b"\x00" in requirements_raw:
            errors.append(
                "requirements.txt contains NUL bytes; regenerate it as UTF-8 text"
            )
        try:
            requirements_raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            errors.append(
                f"requirements.txt is not valid UTF-8: {exc}; regenerate it with uv"
            )

    gitignore_path = root / ".gitignore"
    try:
        gitignore_rules = set(gitignore_path.read_text(encoding="utf-8").splitlines())
    except (OSError, UnicodeDecodeError) as exc:
        errors.append(f"cannot read .gitignore as UTF-8: {exc}")
    else:
        for rule in sorted(REQUIRED_GITIGNORE_RULES - gitignore_rules):
            errors.append(
                f"missing required .gitignore rule: {rule}; add the rule before committing"
            )

    git_common = subprocess.run(
        [
            "git",
            "-C",
            root,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if git_common.returncode != 0:
        detail = git_common.stderr.strip() or "no diagnostic output"
        errors.append(f"cannot resolve the Git common directory: {detail}")
        git_bytes = 0
    else:
        git_common_dir = Path(git_common.stdout.strip())
        git_bytes = sum(
            path.stat().st_size for path in git_common_dir.rglob("*") if path.is_file()
        )
    max_git_bytes = args.max_git_mib * 1024 * 1024
    if git_bytes > max_git_bytes:
        errors.append(
            f".git exceeds {args.max_git_mib} MiB "
            f"({git_bytes / (1024 * 1024):.2f} MiB); inspect retained Git objects"
        )

    print(f"Tracked files: {len(tracked)}")
    print(f".git size: {git_bytes / (1024 * 1024):.2f} MiB")
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
