import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import fcntl

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
ARCHIVE_SCRIPT = SCRIPTS / "archive_historical_raw_snapshot.sh"
RESTORE_SCRIPT = SCRIPTS / "restore_historical_raw.sh"


def wait_for_shared_flock_waiter(
    lock_path: Path,
    process: subprocess.Popen[str],
    forbidden_marker: Path,
    timeout: float = 5,
) -> None:
    inode = lock_path.stat().st_ino
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        locks = Path("/proc/locks").read_text(encoding="utf-8")
        if any(
            "->" in line
            and " FLOCK " in line
            and " READ " in line
            and f":{inode} " in line
            for line in locks.splitlines()
        ):
            return
        if forbidden_marker.exists():
            raise AssertionError("historical reader ran before publisher exit")
        if process.poll() is not None:
            raise AssertionError("historical reader exited before taking lock")
        time.sleep(0.01)
    raise AssertionError("historical shared-lock waiter did not appear")


def wait_for_file(path: Path, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {path}")


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
    bodies = {
        "BAA_1947_games.html": b"<html>index</html>",
        "BAA_1947_games-november.html": b"<html>month</html>",
    }
    pages = []
    for name, payload in bodies.items():
        (cache_dir / name).write_bytes(payload)
        month = "november" if "-november" in name else None
        resource = (
            "historical_schedule_month"
            if month is not None
            else "historical_schedule_index"
        )
        sha256 = hashlib.sha256(payload).hexdigest()
        url = f"https://www.basketball-reference.com/leagues/{name}"
        metadata = {
            "provider": "basketball_reference",
            "resource": resource,
            "league": "BAA",
            "season": 1947,
            "url": url,
            "sha256": sha256,
        }
        if month is not None:
            metadata["month"] = month
        (cache_dir / f"{name}.meta.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        pages.append({
            "path": f"raw/historical_bref/{name}",
            **metadata,
            "month": month,
        })
    manifest = repo / "data/parquet/historical_games_manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "sources": {"basketball_reference": {"pages": pages}},
                "tables": {"historical_games": {"max_season": 1983}},
            },
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


def read_manifest(repo: Path) -> dict:
    path = repo / "data/parquet/historical_games_manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def write_manifest(repo: Path, manifest: dict) -> None:
    path = repo / "data/parquet/historical_games_manifest.json"
    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def rewrite_snapshot_checksums(snapshot: Path) -> None:
    lines = []
    for path in sorted((snapshot / "historical_bref").iterdir()):
        relative = path.relative_to(snapshot).as_posix()
        lines.append(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {relative}\n"
        )
    (snapshot / "SHA256SUMS").write_text("".join(lines), encoding="utf-8")


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


def restore(
    repo: Path,
    archive_root: Path,
    overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = historical_environment(repo, archive_root)
    environment.update(overrides or {})
    return subprocess.run(
        ["bash", str(RESTORE_SCRIPT)],
        cwd=repo,
        env=environment,
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


@pytest.mark.parametrize("operation", ["archive", "restore"])
def test_historical_shell_reader_holds_shared_publication_lock(
    tmp_path: Path,
    operation: str,
) -> None:
    """Would fail if either historical script read outside its shared lock."""
    repo, archive_root, _ = seed_historical_snapshot_fixture(tmp_path)
    if operation == "restore":
        archived = archive(repo, archive_root)
        assert archived.returncode == 0, archived.stderr

    fake_bin = tmp_path / "lock-test-bin"
    fake_bin.mkdir()
    pause_marker = tmp_path / "historical-rsync-paused"
    pause_gate = tmp_path / "historical-rsync-continue"
    fake_rsync = fake_bin / "rsync"
    fake_rsync.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if test ! -e "$PAUSE_MARKER"; then
  : > "$PAUSE_MARKER"
  while test ! -e "$PAUSE_GATE"; do sleep 0.01; done
fi
exec "$REAL_RSYNC" "$@"
""",
        encoding="utf-8",
    )
    fake_rsync.chmod(0o755)
    environment = historical_environment(repo, archive_root)
    environment.update({
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "PAUSE_GATE": str(pause_gate),
        "PAUSE_MARKER": str(pause_marker),
        "REAL_RSYNC": shutil.which("rsync") or "rsync",
    })
    script = ARCHIVE_SCRIPT if operation == "archive" else RESTORE_SCRIPT
    lock_path = repo / "data/.parquet-publication.lock"
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
    process = subprocess.Popen(
        ["bash", str(script)],
        cwd=repo,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout = ""
    stderr = ""
    try:
        wait_for_shared_flock_waiter(lock_path, process, pause_marker)
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        wait_for_file(pause_marker)
        with pytest.raises(BlockingIOError):
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        pause_gate.touch()
        stdout, stderr = process.communicate(timeout=10)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
        pause_gate.touch(exist_ok=True)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    assert process.returncode == 0, f"stdout={stdout!r}\nstderr={stderr!r}"


def test_archive_rejects_body_bytes_that_differ_from_manifest_sha(
    tmp_path: Path,
) -> None:
    """Would fail if archive trusted pairing without the manifest body hash."""
    repo, archive_root, _ = seed_historical_snapshot_fixture(tmp_path)
    (
        repo / "data/raw/historical_bref/BAA_1947_games.html"
    ).write_bytes(b"<html>tampered index</html>")

    archived = archive(repo, archive_root)

    assert archived.returncode != 0
    assert not snapshot_path(repo, archive_root).exists()
    assert list(archive_root.glob(".historical-archive.*")) == []


@pytest.mark.parametrize(
    "mismatch",
    ["empty_manifest", "missing_pair", "extra_pair"],
)
def test_archive_requires_exact_manifest_body_inventory(
    tmp_path: Path,
    mismatch: str,
) -> None:
    """Would fail if archive did not require the manifest's exact body set."""
    repo, archive_root, _ = seed_historical_snapshot_fixture(tmp_path)
    manifest = read_manifest(repo)
    cache_dir = repo / "data/raw/historical_bref"
    if mismatch == "empty_manifest":
        manifest["sources"]["basketball_reference"]["pages"] = []
        write_manifest(repo, manifest)
    elif mismatch == "missing_pair":
        (cache_dir / "BAA_1947_games-november.html").unlink()
        (cache_dir / "BAA_1947_games-november.html.meta.json").unlink()
    else:
        (cache_dir / "BAA_1947_games-december.html").write_bytes(
            b"<html>extra month</html>"
        )
        (cache_dir / "BAA_1947_games-december.html.meta.json").write_text(
            "{}\n", encoding="utf-8"
        )

    archived = archive(repo, archive_root)

    assert archived.returncode != 0
    assert not snapshot_path(repo, archive_root).exists()
    assert list(archive_root.glob(".historical-archive.*")) == []


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("provider", "untrusted_provider"),
        ("url", "https://attacker.invalid/boxscores.html"),
        ("resource", "historical_schedule_index"),
        ("league", "NBA"),
        ("season", 1948),
        ("month", "december"),
        ("sha256", "0" * 64),
    ],
)
def test_archive_rejects_sidecar_identity_that_differs_from_manifest(
    tmp_path: Path,
    field: str,
    wrong_value: str | int,
) -> None:
    """Would fail if archive treated a structurally paired sidecar as valid."""
    repo, archive_root, _ = seed_historical_snapshot_fixture(tmp_path)
    sidecar = (
        repo
        / "data/raw/historical_bref/"
        "BAA_1947_games-november.html.meta.json"
    )
    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    metadata[field] = wrong_value
    sidecar.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    archived = archive(repo, archive_root)

    assert archived.returncode != 0
    assert not snapshot_path(repo, archive_root).exists()
    assert list(archive_root.glob(".historical-archive.*")) == []


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


def test_restore_rejects_manifest_divergent_tampering_with_recomputed_checksums(
    tmp_path: Path,
) -> None:
    """Would fail if restore trusted a self-consistent snapshot inventory."""
    repo, archive_root, _ = seed_historical_snapshot_fixture(tmp_path)
    assert archive(repo, archive_root).returncode == 0
    snapshot = snapshot_path(repo, archive_root)
    (snapshot / "historical_bref/BAA_1947_games.html").write_bytes(
        b"<html>self-consistent tampering</html>"
    )
    rewrite_snapshot_checksums(snapshot)

    restored = restore(repo, archive_root)

    assert restored.returncode != 0
    assert not (repo / "restored/historical_bref").exists()


def test_restore_rejects_snapshot_changed_consistently_during_copy(
    tmp_path: Path,
) -> None:
    """Would fail if staged output were anchored to a mutable snapshot."""
    repo, archive_root, _ = seed_historical_snapshot_fixture(tmp_path)
    assert archive(repo, archive_root).returncode == 0
    snapshot = snapshot_path(repo, archive_root)
    replacement = tmp_path / "race-replacement"
    shutil.copytree(snapshot, replacement)
    body_name = "BAA_1947_games.html"
    body = replacement / "historical_bref" / body_name
    body.write_bytes(b"<html>copy-time replacement</html>")
    replacement_sha = hashlib.sha256(body.read_bytes()).hexdigest()
    sidecar = body.with_name(f"{body_name}.meta.json")
    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    metadata["sha256"] = replacement_sha
    sidecar.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    replacement_manifest = json.loads(
        (replacement / "historical_games_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    pages = replacement_manifest["sources"]["basketball_reference"]["pages"]
    next(
        page for page in pages if page["path"].endswith(f"/{body_name}")
    )["sha256"] = replacement_sha
    (replacement / "historical_games_manifest.json").write_text(
        json.dumps(replacement_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    rewrite_snapshot_checksums(replacement)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_rsync = fake_bin / "rsync"
    fake_rsync.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
dry_run=false
for argument in "$@"; do
  if test "$argument" = "--dry-run"; then
    dry_run=true
  fi
done
if test "$dry_run" = false && test ! -e "$RACE_MARKER"; then
  : > "$RACE_MARKER"
  cp -- "$RACE_REPLACEMENT/historical_games_manifest.json" \
    "$RACE_SNAPSHOT/historical_games_manifest.json"
  cp -- "$RACE_REPLACEMENT/SHA256SUMS" "$RACE_SNAPSHOT/SHA256SUMS"
  cp -a -- "$RACE_REPLACEMENT/historical_bref/." \
    "$RACE_SNAPSHOT/historical_bref/"
fi
exec "$REAL_RSYNC" "$@"
""",
        encoding="utf-8",
    )
    fake_rsync.chmod(0o755)

    restored = restore(
        repo,
        archive_root,
        {
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "RACE_MARKER": str(tmp_path / "race-triggered"),
            "RACE_REPLACEMENT": str(replacement),
            "RACE_SNAPSHOT": str(snapshot),
            "REAL_RSYNC": shutil.which("rsync") or "rsync",
        },
    )

    assert restored.returncode != 0
    assert not (repo / "restored/historical_bref").exists()
    assert list((repo / "restored").glob(".historical-restore.*")) == []


@pytest.mark.parametrize("mutation_phase", ["copy", "dry_run"])
def test_restore_rejects_live_manifest_changed_during_restore(
    tmp_path: Path,
    mutation_phase: str,
) -> None:
    """Would fail if restore trusted a manifest that could change mid-run."""
    repo, archive_root, source_files = seed_historical_snapshot_fixture(
        tmp_path
    )
    assert archive(repo, archive_root).returncode == 0
    snapshot = snapshot_path(repo, archive_root)
    snapshot_files = inventory(snapshot)
    replacement_manifest = tmp_path / "replacement-manifest.json"
    replacement = read_manifest(repo)
    replacement["restore_race_mutation"] = mutation_phase
    replacement_manifest.write_text(
        json.dumps(replacement, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_rsync = fake_bin / "rsync"
    fake_rsync.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
dry_run=false
for argument in "$@"; do
  if test "$argument" = "--dry-run"; then
    dry_run=true
  fi
done
should_mutate=false
if test "$MANIFEST_MUTATION_PHASE" = "copy" && test "$dry_run" = false; then
  should_mutate=true
fi
if test "$MANIFEST_MUTATION_PHASE" = "dry_run" && test "$dry_run" = true; then
  should_mutate=true
fi
if test "$should_mutate" = true && test ! -e "$RACE_MARKER"; then
  : > "$RACE_MARKER"
  cp -- "$REPLACEMENT_MANIFEST" "$NBA_HISTORICAL_RAW_MANIFEST"
fi
exec "$REAL_RSYNC" "$@"
""",
        encoding="utf-8",
    )
    fake_rsync.chmod(0o755)
    pin_tmp = tmp_path / "pin-tmp"
    pin_tmp.mkdir()

    restored = restore(
        repo,
        archive_root,
        {
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "MANIFEST_MUTATION_PHASE": mutation_phase,
            "RACE_MARKER": str(tmp_path / "race-triggered"),
            "REAL_RSYNC": shutil.which("rsync") or "rsync",
            "REPLACEMENT_MANIFEST": str(replacement_manifest),
            "TMPDIR": str(pin_tmp),
        },
    )

    assert restored.returncode != 0
    assert not (repo / "restored/historical_bref").exists()
    assert list((repo / "restored").glob(".historical-restore.*")) == []
    assert list(pin_tmp.glob(".historical-restore-manifest.*")) == []
    assert inventory(repo / "data/raw/historical_bref") == source_files
    assert inventory(snapshot) == snapshot_files


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


def test_archive_rejects_staged_manifest_changed_during_cache_copy(
    tmp_path: Path,
) -> None:
    """Would fail if publication trusted a copy-time manifest replacement."""
    repo, archive_root, _ = seed_historical_snapshot_fixture(tmp_path)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_rsync = fake_bin / "rsync"
    fake_rsync.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
dry_run=false
for argument in "$@"; do
  if test "$argument" = "--dry-run"; then
    dry_run=true
  fi
done
if test "$dry_run" = false && test ! -e "$RACE_MARKER"; then
  : > "$RACE_MARKER"
  destination="${!#}"
  staging_cache="${destination%/}"
  staged_manifest="$(
    dirname "$staging_cache"
  )/historical_games_manifest.json"
  jq '.copy_time_replacement = true' "$staged_manifest" \
    > "$staged_manifest.replacement"
  mv -- "$staged_manifest.replacement" "$staged_manifest"
fi
exec "$REAL_RSYNC" "$@"
""",
        encoding="utf-8",
    )
    fake_rsync.chmod(0o755)

    archived = archive(
        repo,
        archive_root,
        {
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "RACE_MARKER": str(tmp_path / "race-triggered"),
            "REAL_RSYNC": shutil.which("rsync") or "rsync",
        },
    )

    assert archived.returncode != 0
    assert not snapshot_path(repo, archive_root).exists()
    assert list(archive_root.glob(".historical-archive.*")) == []


def test_archive_rejects_two_phase_cache_manifest_switch(
    tmp_path: Path,
) -> None:
    """Would fail if post-copy checks trusted the mutable live manifest."""
    repo, archive_root, _ = seed_historical_snapshot_fixture(tmp_path)
    source = repo / "data/raw/historical_bref"
    live_manifest = repo / "data/parquet/historical_games_manifest.json"
    destination = snapshot_path(repo, archive_root)
    original_manifest = tmp_path / "manifest-a.json"
    shutil.copy2(live_manifest, original_manifest)
    replacement_cache = tmp_path / "cache-b"
    shutil.copytree(source, replacement_cache)
    body_name = "BAA_1947_games.html"
    body = replacement_cache / body_name
    body.write_bytes(b"<html>copy-time cache B</html>")
    replacement_sha = hashlib.sha256(body.read_bytes()).hexdigest()
    sidecar = body.with_name(f"{body_name}.meta.json")
    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    metadata["sha256"] = replacement_sha
    sidecar.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    replacement_manifest = tmp_path / "manifest-b.json"
    manifest_b = read_manifest(repo)
    pages = manifest_b["sources"]["basketball_reference"]["pages"]
    next(
        page for page in pages if page["path"].endswith(f"/{body_name}")
    )["sha256"] = replacement_sha
    replacement_manifest.write_text(
        json.dumps(manifest_b, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_rsync = fake_bin / "rsync"
    fake_rsync.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
dry_run=false
for argument in "$@"; do
  if test "$argument" = "--dry-run"; then
    dry_run=true
  fi
done
if test "$dry_run" = false && test ! -e "$COPY_MARKER"; then
  : > "$COPY_MARKER"
  cp -a -- "$REPLACEMENT_CACHE/." "$NBA_HISTORICAL_RAW_SOURCE/"
  cp -- "$REPLACEMENT_MANIFEST" "$NBA_HISTORICAL_RAW_MANIFEST"
elif test "$dry_run" = true && test ! -e "$DRY_RUN_MARKER"; then
  : > "$DRY_RUN_MARKER"
  cp -- "$ORIGINAL_MANIFEST" "$NBA_HISTORICAL_RAW_MANIFEST"
fi
exec "$REAL_RSYNC" "$@"
""",
        encoding="utf-8",
    )
    fake_rsync.chmod(0o755)
    copy_marker = tmp_path / "copy-switched-to-b"
    dry_run_marker = tmp_path / "dry-run-restored-a"
    pin_tmp = tmp_path / "pin-tmp"
    pin_tmp.mkdir()

    archived = archive(
        repo,
        archive_root,
        {
            "COPY_MARKER": str(copy_marker),
            "DRY_RUN_MARKER": str(dry_run_marker),
            "ORIGINAL_MANIFEST": str(original_manifest),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "REAL_RSYNC": shutil.which("rsync") or "rsync",
            "REPLACEMENT_CACHE": str(replacement_cache),
            "REPLACEMENT_MANIFEST": str(replacement_manifest),
            "TMPDIR": str(pin_tmp),
        },
    )

    assert copy_marker.exists()
    assert archived.returncode != 0, (
        f"copy_switch={copy_marker.exists()} "
        f"dry_run_switch={dry_run_marker.exists()}"
    )
    assert not destination.exists()
    assert list(archive_root.glob(".historical-archive.*")) == []
    assert list(pin_tmp.glob(".historical-archive-manifest.*")) == []


def test_archive_rejects_sidecar_changed_during_final_dry_run(
    tmp_path: Path,
) -> None:
    """Would fail if final checks accepted stale staged SHA256SUMS."""
    repo, archive_root, _ = seed_historical_snapshot_fixture(tmp_path)
    destination = snapshot_path(repo, archive_root)
    sidecar_name = "BAA_1947_games.html.meta.json"
    sidecar = (
        repo / "data/raw/historical_bref" / sidecar_name
    )
    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    metadata["fetched_at"] = "2099-01-01T00:00:00Z"
    replacement_sidecar = tmp_path / sidecar_name
    replacement_sidecar.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_rsync = fake_bin / "rsync"
    fake_rsync.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
dry_run=false
for argument in "$@"; do
  if test "$argument" = "--dry-run"; then
    dry_run=true
  fi
done
if test "$dry_run" = true && test ! -e "$RACE_MARKER"; then
  : > "$RACE_MARKER"
  destination="${!#}"
  staging_cache="${destination%/}"
  cp -- "$REPLACEMENT_SIDECAR" \
    "$NBA_HISTORICAL_RAW_SOURCE/$SIDECAR_NAME"
  cp -- "$REPLACEMENT_SIDECAR" "$staging_cache/$SIDECAR_NAME"
fi
exec "$REAL_RSYNC" "$@"
""",
        encoding="utf-8",
    )
    fake_rsync.chmod(0o755)
    race_marker = tmp_path / "late-sidecar-mutation"
    pin_tmp = tmp_path / "pin-tmp"
    pin_tmp.mkdir()

    archived = archive(
        repo,
        archive_root,
        {
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "RACE_MARKER": str(race_marker),
            "REAL_RSYNC": shutil.which("rsync") or "rsync",
            "REPLACEMENT_SIDECAR": str(replacement_sidecar),
            "SIDECAR_NAME": sidecar_name,
            "TMPDIR": str(pin_tmp),
        },
    )
    checksum_status = None
    if destination.exists():
        checksum_status = subprocess.run(
            ["sha256sum", "-c", "--status", "SHA256SUMS"],
            cwd=destination,
            check=False,
        ).returncode

    assert race_marker.exists()
    assert archived.returncode != 0, (
        f"published_checksum_status={checksum_status}"
    )
    assert not destination.exists()
    assert list(archive_root.glob(".historical-archive.*")) == []
    assert list(pin_tmp.glob(".historical-archive-manifest.*")) == []


@pytest.mark.parametrize(
    "terminal_mutation",
    ["missing_final_newline", "additional_final_newline"],
)
def test_archive_rejects_noncanonical_checksum_terminal_bytes(
    tmp_path: Path,
    terminal_mutation: str,
) -> None:
    """Would fail if checksum comparison stripped terminal newlines."""
    repo, archive_root, _ = seed_historical_snapshot_fixture(tmp_path)
    destination = snapshot_path(repo, archive_root)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_rsync = fake_bin / "rsync"
    fake_rsync.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
dry_run=false
for argument in "$@"; do
  if test "$argument" = "--dry-run"; then
    dry_run=true
  fi
done
if test "$dry_run" = true && test ! -e "$RACE_MARKER"; then
  : > "$RACE_MARKER"
  destination="${!#}"
  staging_cache="${destination%/}"
  staged_checksums="$(dirname "$staging_cache")/SHA256SUMS"
  if test "$TERMINAL_MUTATION" = "missing_final_newline"; then
    truncate -s -1 -- "$staged_checksums"
  else
    printf '\\n' >> "$staged_checksums"
  fi
fi
exec "$REAL_RSYNC" "$@"
""",
        encoding="utf-8",
    )
    fake_rsync.chmod(0o755)
    race_marker = tmp_path / "checksum-terminal-mutation"
    pin_tmp = tmp_path / "pin-tmp"
    pin_tmp.mkdir()

    archived = archive(
        repo,
        archive_root,
        {
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "RACE_MARKER": str(race_marker),
            "REAL_RSYNC": shutil.which("rsync") or "rsync",
            "TERMINAL_MUTATION": terminal_mutation,
            "TMPDIR": str(pin_tmp),
        },
    )
    published_terminal = None
    checksum_status = None
    if destination.exists():
        published_terminal = (
            destination / "SHA256SUMS"
        ).read_bytes()[-2:]
        checksum_status = subprocess.run(
            ["sha256sum", "-c", "--status", "SHA256SUMS"],
            cwd=destination,
            check=False,
        ).returncode

    assert race_marker.exists()
    assert archived.returncode != 0, (
        f"published_terminal={published_terminal!r} "
        f"checksum_status={checksum_status}"
    )
    assert not destination.exists()
    assert list(archive_root.glob(".historical-archive.*")) == []
    assert list(pin_tmp.glob(".historical-archive-manifest.*")) == []


def test_archive_rejects_symlinked_live_manifest(tmp_path: Path) -> None:
    """Would fail if pinning normalized a previously rejected manifest."""
    repo, archive_root, _ = seed_historical_snapshot_fixture(tmp_path)
    manifest = repo / "data/parquet/historical_games_manifest.json"
    destination = snapshot_path(repo, archive_root)
    target = tmp_path / "historical-games-manifest-target.json"
    manifest.replace(target)
    manifest.symlink_to(target)

    archived = archive(repo, archive_root)

    assert archived.returncode != 0
    assert not destination.exists()
    assert list(archive_root.glob(".historical-archive.*")) == []


def test_restore_rejects_symlinked_live_manifest(tmp_path: Path) -> None:
    repo, archive_root, _ = seed_historical_snapshot_fixture(tmp_path)
    archived = archive(repo, archive_root)
    assert archived.returncode == 0, archived.stderr
    manifest = repo / "data/parquet/historical_games_manifest.json"
    target = tmp_path / "historical-games-manifest-target.json"
    manifest.replace(target)
    manifest.symlink_to(target)

    restored = restore(repo, archive_root)

    assert restored.returncode != 0
    assert "missing or unsafe historical manifest" in restored.stderr
    assert not (repo / "restored/historical_bref").exists()


def test_restore_rejects_manifest_swapped_to_symlink_while_waiting(
    tmp_path: Path,
) -> None:
    """Would fail if restore reopened a pre-lock manifest pathname."""
    repo, archive_root, _ = seed_historical_snapshot_fixture(tmp_path)
    archived = archive(repo, archive_root)
    assert archived.returncode == 0, archived.stderr
    manifest = repo / "data/parquet/historical_games_manifest.json"
    lock_path = repo / "data/.parquet-publication.lock"
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
    pin_tmp = tmp_path / "manifest-tmp"
    pin_tmp.mkdir()
    environment = historical_environment(repo, archive_root)
    environment["TMPDIR"] = str(pin_tmp)
    process = subprocess.Popen(
        ["bash", str(RESTORE_SCRIPT)],
        cwd=repo,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout = ""
    stderr = ""
    try:
        wait_for_shared_flock_waiter(
            lock_path,
            process,
            tmp_path / "forbidden-historical-progress",
        )
        target = tmp_path / "historical-manifest-target.json"
        manifest.replace(target)
        manifest.symlink_to(target)
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        stdout, stderr = process.communicate(timeout=10)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    assert process.returncode != 0, stdout
    assert "missing or unsafe historical manifest" in stderr
    assert not (repo / "restored/historical_bref").exists()
    assert list(pin_tmp.iterdir()) == []


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
