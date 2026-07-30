import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
ARCHIVE_SCRIPT = SCRIPTS / "archive_historical_raw_snapshot.sh"
RESTORE_SCRIPT = SCRIPTS / "restore_historical_raw.sh"


def inventory(directory: Path) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def seed_historical_snapshot_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, dict[str, str]]:
    repo = tmp_path / "repo"
    archive_root = tmp_path / "nas"
    archive_root.mkdir()
    cache_dir = repo / "data/raw/historical_bref"
    cache_dir.mkdir(parents=True)
    for name, payload in {
        "BAA_1947_games.html": b"<html>index</html>",
        "BAA_1947_games.html.meta.json": b'{"sha256": "seeded-index"}\n',
        "BAA_1947_games-november.html": b"<html>month</html>",
        "BAA_1947_games-november.html.meta.json": (
            b'{"sha256": "seeded-month"}\n'
        ),
    }.items():
        (cache_dir / name).write_bytes(payload)
    manifest = repo / "data/parquet/historical_games_manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {"tables": {"historical_games": {"max_season": 1983}}},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return repo, archive_root, inventory(cache_dir)


def historical_environment(repo: Path, archive_root: Path) -> dict[str, str]:
    return {
        **os.environ,
        "NBA_HISTORICAL_RAW_ARCHIVE_ROOT": str(archive_root),
        "NBA_HISTORICAL_RAW_MANIFEST": str(
            repo / "data/parquet/historical_games_manifest.json"
        ),
        "NBA_HISTORICAL_RAW_SOURCE": str(
            repo / "data/raw/historical_bref"
        ),
        "NBA_HISTORICAL_RAW_DESTINATION": str(
            repo / "restored/historical_bref"
        ),
    }


def snapshot_path(repo: Path, archive_root: Path) -> Path:
    manifest = repo / "data/parquet/historical_games_manifest.json"
    sha12 = hashlib.sha256(manifest.read_bytes()).hexdigest()[:12]
    return archive_root / f"historical-raw-1983-{sha12}"


def archive(
    repo: Path,
    archive_root: Path,
    overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = historical_environment(repo, archive_root)
    environment.update(overrides or {})
    return subprocess.run(
        ["bash", str(ARCHIVE_SCRIPT)],
        cwd=repo,
        env=environment,
        capture_output=True,
        text=True,
    )


def restore(repo: Path, archive_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(RESTORE_SCRIPT)],
        cwd=repo,
        env=historical_environment(repo, archive_root),
        capture_output=True,
        text=True,
    )


def test_archive_and_restore_are_exact_inverses(tmp_path: Path) -> None:
    """Would fail if either script omitted or changed a historical cache file."""
    repo, archive_root, source_files = seed_historical_snapshot_fixture(tmp_path)

    archived = archive(repo, archive_root)
    assert archived.returncode == 0, archived.stderr
    restored = restore(repo, archive_root)

    assert restored.returncode == 0, restored.stderr
    assert inventory(repo / "restored/historical_bref") == source_files


def test_restore_rejects_tampered_body_without_partial_destination(
    tmp_path: Path,
) -> None:
    """Would fail if restore copied bytes before validating SHA256SUMS."""
    repo, archive_root, _ = seed_historical_snapshot_fixture(tmp_path)
    assert archive(repo, archive_root).returncode == 0
    (snapshot_path(repo, archive_root) / "historical_bref/BAA_1947_games.html").write_bytes(
        b"tampered"
    )

    restored = restore(repo, archive_root)

    assert restored.returncode != 0
    assert not (repo / "restored/historical_bref").exists()


def test_restore_rejects_deleted_sidecar_without_partial_destination(
    tmp_path: Path,
) -> None:
    """Would fail if restore accepted an incomplete checksum inventory."""
    repo, archive_root, _ = seed_historical_snapshot_fixture(tmp_path)
    assert archive(repo, archive_root).returncode == 0
    (snapshot_path(repo, archive_root) / "historical_bref/BAA_1947_games.html.meta.json").unlink()

    restored = restore(repo, archive_root)

    assert restored.returncode != 0
    assert not (repo / "restored/historical_bref").exists()


def test_restore_rejects_changed_snapshot_manifest_without_partial_destination(
    tmp_path: Path,
) -> None:
    """Would fail if restore did not compare archived manifest bytes."""
    repo, archive_root, _ = seed_historical_snapshot_fixture(tmp_path)
    assert archive(repo, archive_root).returncode == 0
    (snapshot_path(repo, archive_root) / "historical_games_manifest.json").write_text(
        "{}\n", encoding="utf-8"
    )

    restored = restore(repo, archive_root)

    assert restored.returncode != 0
    assert not (repo / "restored/historical_bref").exists()


def test_restore_refuses_existing_destination(tmp_path: Path) -> None:
    """Would fail if restore merged into an existing raw-cache destination."""
    repo, archive_root, _ = seed_historical_snapshot_fixture(tmp_path)
    assert archive(repo, archive_root).returncode == 0
    destination = repo / "restored/historical_bref"
    destination.mkdir(parents=True)
    (destination / "keep.txt").write_text("do not replace\n", encoding="utf-8")

    restored = restore(repo, archive_root)

    assert restored.returncode != 0
    assert (destination / "keep.txt").read_text(encoding="utf-8") == "do not replace\n"


def test_archive_refuses_existing_immutable_snapshot(tmp_path: Path) -> None:
    """Would fail if archive overwrote or merged an immutable snapshot target."""
    repo, archive_root, _ = seed_historical_snapshot_fixture(tmp_path)
    snapshot = snapshot_path(repo, archive_root)
    snapshot.mkdir()
    (snapshot / "keep.txt").write_text("do not replace\n", encoding="utf-8")

    archived = archive(repo, archive_root)

    assert archived.returncode != 0
    assert (snapshot / "keep.txt").read_text(encoding="utf-8") == "do not replace\n"


def test_restore_rejects_incomplete_snapshot_without_partial_destination(
    tmp_path: Path,
) -> None:
    """Would fail if restore accepted a snapshot missing its checksum inventory."""
    repo, archive_root, _ = seed_historical_snapshot_fixture(tmp_path)
    snapshot = snapshot_path(repo, archive_root)
    snapshot.mkdir()

    restored = restore(repo, archive_root)

    assert restored.returncode != 0
    assert not (repo / "restored/historical_bref").exists()


def test_archive_failure_removes_only_its_stage_without_publishing(
    tmp_path: Path,
) -> None:
    """Would fail if a failed copy left a retry-blocking final snapshot."""
    repo, archive_root, _ = seed_historical_snapshot_fixture(tmp_path)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    failing_rsync = fake_bin / "rsync"
    failing_rsync.write_text("#!/usr/bin/env bash\nexit 42\n", encoding="utf-8")
    failing_rsync.chmod(0o755)

    archived = archive(
        repo,
        archive_root,
        {"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
    )

    assert archived.returncode != 0
    assert not snapshot_path(repo, archive_root).exists()
    assert list(archive_root.glob(".historical-archive.*")) == []


@pytest.mark.parametrize("entry_kind", ["symlink", "nested_dir", "fifo"])
def test_archive_rejects_non_flat_non_regular_source_entries(
    tmp_path: Path,
    entry_kind: str,
) -> None:
    """Would fail if archive silently omitted non-regular cache entries."""
    repo, archive_root, _ = seed_historical_snapshot_fixture(tmp_path)
    cache_dir = repo / "data/raw/historical_bref"
    if entry_kind == "symlink":
        (cache_dir / "linked.html").symlink_to("BAA_1947_games.html")
    elif entry_kind == "nested_dir":
        (cache_dir / "unexpected").mkdir()
    else:
        os.mkfifo(cache_dir / "unexpected.fifo")

    archived = archive(repo, archive_root)

    assert archived.returncode != 0
    assert "invalid historical cache structure" in archived.stderr
    assert not snapshot_path(repo, archive_root).exists()
    assert list(archive_root.glob(".historical-archive.*")) == []


@pytest.mark.parametrize(
    "entry_kind", ["symlink", "empty_dir", "fifo", "extra_root"]
)
def test_restore_rejects_structurally_invalid_snapshot_without_destination(
    tmp_path: Path,
    entry_kind: str,
) -> None:
    """Would fail if restore accepted data absent from the checksum inventory."""
    repo, archive_root, _ = seed_historical_snapshot_fixture(tmp_path)
    assert archive(repo, archive_root).returncode == 0
    snapshot = snapshot_path(repo, archive_root)
    historical_bref = snapshot / "historical_bref"
    if entry_kind == "symlink":
        (historical_bref / "linked.html").symlink_to("BAA_1947_games.html")
    elif entry_kind == "empty_dir":
        (historical_bref / "unexpected").mkdir()
    elif entry_kind == "fifo":
        os.mkfifo(historical_bref / "unexpected.fifo")
    else:
        (snapshot / "unexpected.txt").write_text("extra\n", encoding="utf-8")

    restored = restore(repo, archive_root)

    assert restored.returncode != 0
    assert "invalid historical snapshot structure" in restored.stderr
    assert not (repo / "restored/historical_bref").exists()
