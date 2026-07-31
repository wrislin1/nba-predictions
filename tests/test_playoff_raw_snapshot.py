from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
ARCHIVE_SCRIPT = SCRIPTS / "archive_playoff_raw_snapshot.sh"
RESTORE_SCRIPT = SCRIPTS / "restore_playoff_raw.sh"
PLAY_IN_MONTHS = {
    2020: "august",
    2021: "may",
    **{season: "april" for season in range(2022, 2027)},
}


def wait_for_playoff_shared_flock_waiter(
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
            raise AssertionError("playoff reader ran before publisher exit")
        if process.poll() is not None:
            raise AssertionError("playoff reader exited before taking lock")
        time.sleep(0.01)
    raise AssertionError("playoff shared-lock waiter did not appear")


def wait_for_playoff_file(path: Path, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {path}")


@dataclass(frozen=True)
class PlayoffSnapshotFixture:
    repo: Path
    archive_root: Path
    playoff_cache: Path
    historical_cache: Path
    playoff_manifest: Path
    historical_manifest: Path
    destination: Path
    saved_live_cache: Path


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def tree_sha256(directory: Path) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): sha256_bytes(
            path.read_bytes()
        )
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_json_documents(path: Path, values: list[object]) -> None:
    path.write_text(
        "".join(
            json.dumps(value, indent=2, sort_keys=True) + "\n"
            for value in values
        ),
        encoding="utf-8",
    )


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def refresh_historical_manifest_pin(
    fixture: PlayoffSnapshotFixture,
) -> None:
    manifest = read_json(fixture.playoff_manifest)
    manifest["sources"]["basketball_reference"][
        "historical_manifest"
    ]["sha256"] = sha256_bytes(
        fixture.historical_manifest.read_bytes()
    )
    write_json(fixture.playoff_manifest, manifest)


def seed_historical_dependency(fixture: PlayoffSnapshotFixture) -> None:
    pages = []
    for name, month in (
        ("BAA_1947_games.html", None),
        ("BAA_1947_games-november.html", "november"),
    ):
        payload = f"<html>{name}</html>\n".encode()
        body = fixture.historical_cache / name
        body.write_bytes(payload)
        sha256 = sha256_bytes(payload)
        resource = (
            "historical_schedule_index"
            if month is None
            else "historical_schedule_month"
        )
        metadata = {
            "provider": "basketball_reference",
            "resource": resource,
            "league": "BAA",
            "season": 1947,
            "month": month,
            "url": (
                "https://www.basketball-reference.com/leagues/"
                f"{name}"
            ),
            "sha256": sha256,
        }
        write_json(body.with_name(f"{name}.meta.json"), metadata)
        pages.append({
            "path": f"raw/historical_bref/{name}",
            **metadata,
        })
    write_json(
        fixture.historical_manifest,
        {
            "sources": {
                "basketball_reference": {
                    "pages": pages,
                },
            },
            "tables": {
                "historical_games": {
                    "max_season": 1983,
                },
            },
        },
    )


def playoff_record(
    *,
    season: int,
    month: str | None,
    payload: bytes,
) -> dict:
    if month is None:
        filename = f"NBA_{season}_games.html"
        resource = "playoff_schedule"
        url = (
            "https://www.basketball-reference.com/playoffs/"
            f"{filename}"
        )
    else:
        filename = f"NBA_{season}_games-{month}.html"
        resource = "play_in_schedule_month"
        url = (
            "https://www.basketball-reference.com/leagues/"
            f"{filename}"
        )
    sha256 = sha256_bytes(payload)
    record = {
        "provider": "basketball_reference",
        "resource": resource,
        "league": "NBA",
        "season": season,
        "month": month,
        "url": url,
        "path": f"raw/playoff_bref/{filename}",
        "sha256": sha256,
    }
    if month is not None:
        record.update({
            "seeded_from": (
                f"raw/bref/NBA_{season}_games-{month}.html"
            ),
            "seed_operation": "verified_cache_copy",
            "source_sha256": sha256,
        })
    return record


def seed_playoff_cache(fixture: PlayoffSnapshotFixture) -> None:
    records = []
    identities = [
        *((season, None) for season in range(1984, 2027)),
        *PLAY_IN_MONTHS.items(),
    ]
    for season, month in identities:
        suffix = "" if month is None else f"-{month}"
        filename = f"NBA_{season}_games{suffix}.html"
        payload = f"<html>{filename}</html>\n".encode()
        record = playoff_record(
            season=season,
            month=month,
            payload=payload,
        )
        body = fixture.playoff_cache / filename
        body.write_bytes(payload)
        metadata = {
            key: value
            for key, value in record.items()
            if key != "path"
        }
        metadata["content_length"] = len(payload)
        metadata["fetched_at"] = "2026-07-31T00:00:00+00:00"
        write_json(body.with_name(f"{filename}.meta.json"), metadata)
        records.append(record)

    historical_sha = sha256_bytes(
        fixture.historical_manifest.read_bytes()
    )
    write_json(
        fixture.playoff_manifest,
        {
            "sources": {
                "basketball_reference": {
                    "historical_manifest": {
                        "path": (
                            "parquet/"
                            "historical_games_manifest.json"
                        ),
                        "sha256": historical_sha,
                    },
                    "playoff_pages": sorted(
                        records,
                        key=lambda record: record["path"],
                    ),
                },
            },
            "tables": {
                "playoff_games": {
                    "max_season": 2026,
                },
            },
        },
    )


@pytest.fixture
def playoff_snapshot_fixture(
    tmp_path: Path,
) -> PlayoffSnapshotFixture:
    repo = tmp_path / "repo"
    fixture = PlayoffSnapshotFixture(
        repo=repo,
        archive_root=tmp_path / "archive",
        playoff_cache=repo / "data/raw/playoff_bref",
        historical_cache=repo / "data/raw/historical_bref",
        playoff_manifest=(
            repo / "data/parquet/playoff_games_manifest.json"
        ),
        historical_manifest=(
            repo / "data/parquet/historical_games_manifest.json"
        ),
        destination=repo / "data/raw/playoff_bref",
        saved_live_cache=repo / "saved/playoff_bref",
    )
    fixture.archive_root.mkdir()
    fixture.playoff_cache.mkdir(parents=True)
    fixture.historical_cache.mkdir(parents=True)
    fixture.playoff_manifest.parent.mkdir(parents=True)
    fixture.saved_live_cache.parent.mkdir(parents=True)
    seed_historical_dependency(fixture)
    seed_playoff_cache(fixture)
    return fixture


def playoff_environment(
    fixture: PlayoffSnapshotFixture,
    overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    environment = {
        **os.environ,
        "NBA_PLAYOFF_RAW_ARCHIVE_ROOT": str(fixture.archive_root),
        "NBA_PLAYOFF_RAW_MANIFEST": str(fixture.playoff_manifest),
        "NBA_PLAYOFF_RAW_SOURCE": str(fixture.playoff_cache),
        "NBA_PLAYOFF_RAW_DESTINATION": str(fixture.destination),
        "NBA_PLAYOFF_HISTORICAL_MANIFEST": str(
            fixture.historical_manifest
        ),
        "NBA_PLAYOFF_HISTORICAL_CACHE": str(
            fixture.historical_cache
        ),
    }
    environment.update(overrides or {})
    return environment


def run_archive(
    fixture: PlayoffSnapshotFixture,
    overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    assert ARCHIVE_SCRIPT.is_file()
    historical_before = (
        tree_sha256(fixture.historical_cache)
        if fixture.historical_cache.is_dir()
        else None
    )
    result = subprocess.run(
        ["bash", str(ARCHIVE_SCRIPT)],
        cwd=fixture.repo,
        env=playoff_environment(fixture, overrides),
        capture_output=True,
        text=True,
    )
    historical_after = (
        tree_sha256(fixture.historical_cache)
        if fixture.historical_cache.is_dir()
        else None
    )
    assert historical_after == historical_before
    return result


def run_restore(
    fixture: PlayoffSnapshotFixture,
    overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    assert RESTORE_SCRIPT.is_file()
    historical_before = (
        tree_sha256(fixture.historical_cache)
        if fixture.historical_cache.is_dir()
        else None
    )
    result = subprocess.run(
        ["bash", str(RESTORE_SCRIPT)],
        cwd=fixture.repo,
        env=playoff_environment(fixture, overrides),
        capture_output=True,
        text=True,
    )
    historical_after = (
        tree_sha256(fixture.historical_cache)
        if fixture.historical_cache.is_dir()
        else None
    )
    assert historical_after == historical_before
    return result


@pytest.mark.parametrize("operation", ["archive", "restore"])
@pytest.mark.parametrize("lock_root", ["playoff", "historical"])
def test_playoff_shell_reader_holds_all_shared_publication_locks(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
    operation: str,
    lock_root: str,
) -> None:
    """Would fail if either playoff script omitted or released a data lock."""
    fixture = playoff_snapshot_fixture
    if operation == "restore":
        archived = run_archive(fixture)
        assert archived.returncode == 0, archived.stderr
        make_restore_destination_absent(fixture)

    second_manifest = (
        fixture.repo.parent
        / "historical-repo/data/parquet/historical_games_manifest.json"
    )
    second_manifest.parent.mkdir(parents=True)
    shutil.copy2(fixture.historical_manifest, second_manifest)
    second_lock = second_manifest.parents[1] / ".parquet-publication.lock"
    first_lock = (
        fixture.playoff_manifest.parents[1]
        / ".parquet-publication.lock"
    )
    contended_lock = (
        first_lock if lock_root == "playoff" else second_lock
    )
    fake_bin, _ = install_rsync_wrapper(
        fixture.repo.parent,
        """#!/usr/bin/env bash
set -euo pipefail
if test ! -e "$PAUSE_MARKER"; then
  : > "$PAUSE_MARKER"
  while test ! -e "$PAUSE_GATE"; do sleep 0.01; done
fi
exec "$REAL_RSYNC" "$@"
""",
    )
    pause_marker = fixture.repo.parent / "playoff-rsync-paused"
    pause_gate = fixture.repo.parent / "playoff-rsync-continue"
    environment = playoff_environment(
        fixture,
        {
            "NBA_PLAYOFF_HISTORICAL_MANIFEST": str(second_manifest),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "PAUSE_GATE": str(pause_gate),
            "PAUSE_MARKER": str(pause_marker),
            "REAL_RSYNC": shutil.which("rsync") or "rsync",
        },
    )
    script = ARCHIVE_SCRIPT if operation == "archive" else RESTORE_SCRIPT
    lock_fd = os.open(contended_lock, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
    process = subprocess.Popen(
        ["bash", str(script)],
        cwd=fixture.repo,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout = ""
    stderr = ""
    try:
        wait_for_playoff_shared_flock_waiter(
            contended_lock,
            process,
            pause_marker,
        )
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        wait_for_playoff_file(pause_marker)
        with pytest.raises(BlockingIOError):
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        pause_gate.touch()
        stdout, stderr = process.communicate(timeout=15)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
        pause_gate.touch(exist_ok=True)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    assert process.returncode == 0, f"stdout={stdout!r}\nstderr={stderr!r}"


def snapshot_path(fixture: PlayoffSnapshotFixture) -> Path:
    sha12 = sha256_bytes(fixture.playoff_manifest.read_bytes())[:12]
    return (
        fixture.archive_root / f"playoff-raw-2026-{sha12}"
    )


def assert_archive_rejected(
    fixture: PlayoffSnapshotFixture,
) -> subprocess.CompletedProcess[str]:
    archived = run_archive(fixture)
    assert archived.returncode != 0
    assert not snapshot_path(fixture).exists()
    assert list(
        fixture.archive_root.glob(".playoff-archive.*")
    ) == []
    return archived


def remove_pair(cache: Path, filename: str) -> None:
    (cache / filename).unlink()
    (cache / f"{filename}.meta.json").unlink()


def make_restore_destination_absent(
    fixture: PlayoffSnapshotFixture,
) -> None:
    shutil.move(
        fixture.playoff_cache,
        fixture.saved_live_cache,
    )


def append_manifest_record(
    fixture: PlayoffSnapshotFixture,
    record: dict,
) -> None:
    manifest = read_json(fixture.playoff_manifest)
    manifest["sources"]["basketball_reference"][
        "playoff_pages"
    ].append(record)
    write_json(fixture.playoff_manifest, manifest)


def mutate_sidecar(
    fixture: PlayoffSnapshotFixture,
    filename: str,
    field: str,
    value: object,
) -> None:
    path = fixture.playoff_cache / f"{filename}.meta.json"
    metadata = read_json(path)
    metadata[field] = value
    write_json(path, metadata)


def rewrite_snapshot_checksums(snapshot: Path) -> None:
    lines = []
    for path in sorted((snapshot / "playoff_bref").iterdir()):
        relative = path.relative_to(snapshot).as_posix()
        lines.append(
            f"{sha256_bytes(path.read_bytes())}  {relative}\n"
        )
    (snapshot / "SHA256SUMS").write_text(
        "".join(lines),
        encoding="utf-8",
    )


def install_rsync_wrapper(
    tmp_path: Path,
    source: str,
) -> tuple[Path, Path]:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_rsync = fake_bin / "rsync"
    fake_rsync.write_text(source, encoding="utf-8")
    fake_rsync.chmod(0o755)
    return fake_bin, fake_rsync


def install_accepted_hash_mutator(
    tmp_path: Path,
    *,
    target: Path,
    trigger_count: int,
) -> tuple[Path, Path]:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    backup = tmp_path / "accepted-hash-backup"
    shutil.copy2(target, backup)
    fake_sha256sum = fake_bin / "sha256sum"
    fake_sha256sum.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
"$REAL_SHA256SUM" "$@"
status=$?
if test "$#" -eq 1 && test "$1" = "$MUTATION_TARGET"; then
  count=0
  if test -e "$MUTATION_COUNT_FILE"; then
    read -r count < "$MUTATION_COUNT_FILE"
  fi
  count=$((count + 1))
  printf '%s\\n' "$count" > "$MUTATION_COUNT_FILE"
  if test "$count" -eq "$MUTATION_TRIGGER_COUNT"; then
    printf 'boundary mutation' >> "$MUTATION_TARGET"
    cp -p -- "$MUTATION_BACKUP" "$MUTATION_TARGET"
    : > "$MUTATION_MARKER"
  fi
fi
exit "$status"
""",
        encoding="utf-8",
    )
    fake_sha256sum.chmod(0o755)
    return fake_bin, backup


def accepted_hash_environment(
    tmp_path: Path,
    *,
    target: Path,
    trigger_count: int,
) -> dict[str, str]:
    fake_bin, backup = install_accepted_hash_mutator(
        tmp_path,
        target=target,
        trigger_count=trigger_count,
    )
    return {
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "REAL_SHA256SUM": shutil.which("sha256sum") or "sha256sum",
        "MUTATION_BACKUP": str(backup),
        "MUTATION_COUNT_FILE": str(tmp_path / "mutation-count"),
        "MUTATION_MARKER": str(tmp_path / "mutation-marker"),
        "MUTATION_TARGET": str(target),
        "MUTATION_TRIGGER_COUNT": str(trigger_count),
    }


def test_archive_and_restore_are_exact_inverses(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if either entry point omitted or changed a cache file."""
    fixture = playoff_snapshot_fixture
    archived = run_archive(fixture)
    assert archived.returncode == 0, archived.stderr
    shutil.move(
        fixture.playoff_cache,
        fixture.saved_live_cache,
    )

    restored = run_restore(fixture)

    assert restored.returncode == 0, restored.stderr
    assert tree_sha256(fixture.playoff_cache) == tree_sha256(
        fixture.saved_live_cache
    )
    assert tree_sha256(fixture.historical_cache)


def test_archive_accepts_an_existing_exact_immutable_snapshot(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if a safe retry rejected the already verified snapshot."""
    fixture = playoff_snapshot_fixture
    first = run_archive(fixture)
    second = run_archive(fixture)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert str(snapshot_path(fixture)) in second.stdout


def test_archive_existing_snapshot_equivalence_compares_unpinned_metadata(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if retry equivalence compared only manifest-pinned bytes."""
    fixture = playoff_snapshot_fixture
    assert run_archive(fixture).returncode == 0
    snapshot_before = tree_sha256(snapshot_path(fixture))
    mutate_sidecar(
        fixture,
        "NBA_1984_games.html",
        "fetched_at",
        "2099-01-01T00:00:00+00:00",
    )

    retried = run_archive(fixture)

    assert retried.returncode != 0
    assert "existing playoff snapshot differs from source" in (
        retried.stderr
    )
    assert tree_sha256(snapshot_path(fixture)) == snapshot_before


def test_archive_rejects_symlinked_source(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if rsync followed a redirected live cache root."""
    fixture = playoff_snapshot_fixture
    real_cache = fixture.playoff_cache.with_name("real-playoff")
    fixture.playoff_cache.rename(real_cache)
    fixture.playoff_cache.symlink_to(real_cache, target_is_directory=True)

    assert_archive_rejected(fixture)


def test_archive_rejects_symlinked_archive_root(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if immutable publication followed a redirected root."""
    fixture = playoff_snapshot_fixture
    real_archive = fixture.archive_root.with_name("real-archive")
    fixture.archive_root.rename(real_archive)
    fixture.archive_root.symlink_to(real_archive, target_is_directory=True)

    archived = run_archive(fixture)

    assert archived.returncode != 0
    assert list(real_archive.iterdir()) == []


def test_restore_rejects_symlinked_destination(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if restore merged through an existing destination link."""
    fixture = playoff_snapshot_fixture
    assert run_archive(fixture).returncode == 0
    make_restore_destination_absent(fixture)
    fixture.destination.symlink_to(
        fixture.saved_live_cache,
        target_is_directory=True,
    )

    restored = run_restore(fixture)

    assert restored.returncode != 0
    assert fixture.destination.is_symlink()


@pytest.mark.parametrize(
    ("protected_name", "through_symlink"),
    [
        ("source", False),
        ("source", True),
        ("historical", False),
        ("historical", True),
        ("snapshot", False),
        ("snapshot", True),
    ],
)
def test_restore_rejects_destination_below_protected_tree_before_staging(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
    protected_name: str,
    through_symlink: bool,
) -> None:
    """Would fail if restore staged inside any protected input tree."""
    fixture = playoff_snapshot_fixture
    assert run_archive(fixture).returncode == 0
    protected = {
        "source": fixture.playoff_cache,
        "historical": fixture.historical_cache,
        "snapshot": snapshot_path(fixture),
    }[protected_name]
    protected_before = tree_sha256(protected)
    destination_parent = protected
    if through_symlink:
        destination_parent = fixture.repo / f"{protected_name}-alias"
        destination_parent.symlink_to(
            protected,
            target_is_directory=True,
        )
    destination = destination_parent / "restored-playoff"
    fake_bin = fixture.repo.parent / "fake-bin"
    fake_bin.mkdir()
    fake_mktemp = fake_bin / "mktemp"
    fake_mktemp.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
for argument in "$@"; do
  if [[ "$argument" == *".playoff-restore."* ]]; then
    : > "$RESTORE_STAGE_MARKER"
  fi
done
exec "$REAL_MKTEMP" "$@"
""",
        encoding="utf-8",
    )
    fake_mktemp.chmod(0o755)
    stage_marker = fixture.repo.parent / "restore-stage-attempted"

    restored = run_restore(
        fixture,
        {
            "NBA_PLAYOFF_RAW_DESTINATION": str(destination),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "REAL_MKTEMP": shutil.which("mktemp") or "mktemp",
            "RESTORE_STAGE_MARKER": str(stage_marker),
        },
    )

    assert restored.returncode != 0
    assert "protected playoff recovery tree" in restored.stderr
    assert not stage_marker.exists()
    assert not destination.exists()
    assert tree_sha256(protected) == protected_before


@pytest.mark.parametrize(
    ("protected_name", "through_symlink"),
    [
        ("source", False),
        ("source", True),
        ("historical", False),
        ("historical", True),
        ("snapshot", False),
        ("snapshot", True),
    ],
)
def test_restore_rejects_destination_equal_to_protected_tree_without_writes(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
    protected_name: str,
    through_symlink: bool,
) -> None:
    """Would fail if an equal protected path were touched before rejection."""
    fixture = playoff_snapshot_fixture
    assert run_archive(fixture).returncode == 0
    protected = {
        "source": fixture.playoff_cache,
        "historical": fixture.historical_cache,
        "snapshot": snapshot_path(fixture),
    }[protected_name]
    protected_before = tree_sha256(protected)
    destination = protected
    if through_symlink:
        destination = fixture.repo / f"{protected_name}-equal-alias"
        destination.symlink_to(
            protected,
            target_is_directory=True,
        )
    fake_bin = fixture.repo.parent / "fake-bin"
    fake_bin.mkdir()
    fake_mktemp = fake_bin / "mktemp"
    fake_mktemp.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
for argument in "$@"; do
  if [[ "$argument" == *".playoff-restore."* ]]; then
    : > "$RESTORE_STAGE_MARKER"
  fi
done
exec "$REAL_MKTEMP" "$@"
""",
        encoding="utf-8",
    )
    fake_mktemp.chmod(0o755)
    stage_marker = fixture.repo.parent / "restore-stage-attempted"

    restored = run_restore(
        fixture,
        {
            "NBA_PLAYOFF_RAW_DESTINATION": str(destination),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "REAL_MKTEMP": shutil.which("mktemp") or "mktemp",
            "RESTORE_STAGE_MARKER": str(stage_marker),
        },
    )

    assert restored.returncode != 0
    assert not stage_marker.exists()
    assert tree_sha256(protected) == protected_before


def test_restore_requires_existing_parent_without_creating_residue(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if restore used mkdir -p before validating its target."""
    fixture = playoff_snapshot_fixture
    assert run_archive(fixture).returncode == 0
    missing_parent = fixture.repo / "absent/nested"
    destination = missing_parent / "playoff_bref"

    restored = run_restore(
        fixture,
        {"NBA_PLAYOFF_RAW_DESTINATION": str(destination)},
    )

    assert restored.returncode != 0
    assert "restore parent must already exist" in restored.stderr
    assert not (fixture.repo / "absent").exists()


def test_archive_rejects_special_file(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if a FIFO could be silently omitted from the snapshot."""
    fixture = playoff_snapshot_fixture
    os.mkfifo(fixture.playoff_cache / "unexpected.fifo")

    assert_archive_rejected(fixture)


def test_archive_rejects_nested_directory(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if nested source data escaped the flat inventory."""
    fixture = playoff_snapshot_fixture
    (fixture.playoff_cache / "unexpected").mkdir()

    assert_archive_rejected(fixture)


def test_archive_rejects_part_file(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if an interrupted publication file were archived."""
    fixture = playoff_snapshot_fixture
    (fixture.playoff_cache / "NBA_2026_games.html.part").write_bytes(
        b"partial"
    )

    assert_archive_rejected(fixture)


def test_archive_rejects_unexpected_file(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if unrelated top-level data entered the snapshot."""
    fixture = playoff_snapshot_fixture
    (fixture.playoff_cache / "README.txt").write_text(
        "unexpected\n",
        encoding="utf-8",
    )

    assert_archive_rejected(fixture)


def test_archive_rejects_orphan_body(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if a body without its attesting sidecar were accepted."""
    fixture = playoff_snapshot_fixture
    (
        fixture.playoff_cache
        / "NBA_1984_games.html.meta.json"
    ).unlink()

    assert_archive_rejected(fixture)


def test_archive_rejects_orphan_sidecar(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if a sidecar without its body were accepted."""
    fixture = playoff_snapshot_fixture
    (fixture.playoff_cache / "NBA_1984_games.html").unlink()

    assert_archive_rejected(fixture)


def test_archive_rejects_body_checksum_mismatch(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if validation trusted metadata instead of body bytes."""
    fixture = playoff_snapshot_fixture
    (fixture.playoff_cache / "NBA_1984_games.html").write_bytes(
        b"tampered"
    )

    assert_archive_rejected(fixture)


def test_archive_rejects_sidecar_provider_mismatch(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    fixture = playoff_snapshot_fixture
    mutate_sidecar(
        fixture,
        "NBA_1984_games.html",
        "provider",
        "untrusted",
    )
    assert_archive_rejected(fixture)


def test_archive_rejects_sidecar_resource_mismatch(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    fixture = playoff_snapshot_fixture
    mutate_sidecar(
        fixture,
        "NBA_1984_games.html",
        "resource",
        "play_in_schedule_month",
    )
    assert_archive_rejected(fixture)


def test_archive_rejects_sidecar_league_mismatch(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    fixture = playoff_snapshot_fixture
    mutate_sidecar(
        fixture,
        "NBA_1984_games.html",
        "league",
        "ABA",
    )
    assert_archive_rejected(fixture)


def test_archive_rejects_sidecar_season_mismatch(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    fixture = playoff_snapshot_fixture
    mutate_sidecar(
        fixture,
        "NBA_1984_games.html",
        "season",
        1985,
    )
    assert_archive_rejected(fixture)


def test_archive_rejects_sidecar_month_mismatch(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    fixture = playoff_snapshot_fixture
    mutate_sidecar(
        fixture,
        "NBA_2020_games-august.html",
        "month",
        "july",
    )
    assert_archive_rejected(fixture)


def test_archive_rejects_sidecar_url_mismatch(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    fixture = playoff_snapshot_fixture
    mutate_sidecar(
        fixture,
        "NBA_1984_games.html",
        "url",
        "https://attacker.invalid/NBA_1984_games.html",
    )
    assert_archive_rejected(fixture)


def test_archive_rejects_sidecar_sha256_mismatch(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    fixture = playoff_snapshot_fixture
    mutate_sidecar(
        fixture,
        "NBA_1984_games.html",
        "sha256",
        "0" * 64,
    )
    rejected = assert_archive_rejected(fixture)
    assert "sidecar NBA_1984_games.html" in rejected.stderr


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("seeded_from", "raw/bref/NBA_2020_games-july.html"),
        ("seed_operation", "unverified_copy"),
    ],
)
def test_archive_rejects_seeded_sidecar_provenance_mismatch(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
    field: str,
    value: str,
) -> None:
    fixture = playoff_snapshot_fixture
    mutate_sidecar(
        fixture,
        "NBA_2020_games-august.html",
        field,
        value,
    )
    rejected = assert_archive_rejected(fixture)
    assert "sidecar NBA_2020_games-august.html" in rejected.stderr


@pytest.mark.parametrize(
    "stream_case",
    ["valid_valid", "invalid_valid"],
)
def test_archive_rejects_streamed_playoff_sidecar_documents(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
    stream_case: str,
) -> None:
    """Would fail if sidecar validation trusted jq's final stream value."""
    fixture = playoff_snapshot_fixture
    sidecar = (
        fixture.playoff_cache / "NBA_1984_games.html.meta.json"
    )
    valid_document = read_json(sidecar)
    first_document = (
        valid_document
        if stream_case == "valid_valid"
        else []
    )
    write_json_documents(
        sidecar,
        [first_document, valid_document],
    )

    rejected = assert_archive_rejected(fixture)

    assert "sidecar NBA_1984_games.html" in rejected.stderr


@pytest.mark.parametrize(
    "stream_case",
    ["valid_valid", "invalid_valid"],
)
def test_archive_rejects_streamed_playoff_manifest_documents(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
    stream_case: str,
) -> None:
    """Would fail if manifest parsing accepted jq's final stream value."""
    fixture = playoff_snapshot_fixture
    valid_document = read_json(fixture.playoff_manifest)
    first_document = (
        valid_document
        if stream_case == "valid_valid"
        else {}
    )
    write_json_documents(
        fixture.playoff_manifest,
        [first_document, valid_document],
    )

    rejected = assert_archive_rejected(fixture)

    assert (
        "playoff manifest must contain exactly one JSON document"
        in rejected.stderr
    )


def test_archive_rejects_duplicate_resource_identity(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if two records attested the same logical source."""
    fixture = playoff_snapshot_fixture
    manifest = read_json(fixture.playoff_manifest)
    pages = manifest["sources"]["basketball_reference"][
        "playoff_pages"
    ]
    pages[-1]["resource"] = pages[0]["resource"]
    pages[-1]["season"] = pages[0]["season"]
    pages[-1]["month"] = pages[0]["month"]
    write_json(fixture.playoff_manifest, manifest)

    rejected = assert_archive_rejected(fixture)
    assert (
        "playoff resource identities must be unique"
        in rejected.stderr
    )


def test_archive_rejects_duplicate_url(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if distinct records could alias one remote resource."""
    fixture = playoff_snapshot_fixture
    manifest = read_json(fixture.playoff_manifest)
    pages = manifest["sources"]["basketball_reference"][
        "playoff_pages"
    ]
    pages[-1]["url"] = pages[0]["url"]
    write_json(fixture.playoff_manifest, manifest)

    rejected = assert_archive_rejected(fixture)
    assert "playoff page URLs must be unique" in rejected.stderr


def test_archive_rejects_non_exact_dedicated_cardinality(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if the exact 43-record gate were unreachable."""
    fixture = playoff_snapshot_fixture
    manifest = read_json(fixture.playoff_manifest)
    pages = manifest["sources"]["basketball_reference"][
        "playoff_pages"
    ]
    dedicated = next(
        page
        for page in pages
        if page["resource"] == "playoff_schedule"
    )
    dedicated["resource"] = "play_in_schedule_month"
    write_json(fixture.playoff_manifest, manifest)

    rejected = assert_archive_rejected(fixture)

    assert "playoff pages must contain 43 dedicated records" in (
        rejected.stderr
    )


def test_archive_rejects_non_exact_seeded_cardinality(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if the exact seven-record gate were unreachable."""
    fixture = playoff_snapshot_fixture
    manifest = read_json(fixture.playoff_manifest)
    pages = manifest["sources"]["basketball_reference"][
        "playoff_pages"
    ]
    seeded = next(
        page
        for page in pages
        if page["resource"] == "play_in_schedule_month"
    )
    seeded["resource"] = "other_schedule"
    write_json(fixture.playoff_manifest, manifest)

    rejected = assert_archive_rejected(fixture)

    assert "playoff pages must contain seven seeded records" in (
        rejected.stderr
    )


def test_archive_rejects_51_otherwise_well_formed_records_at_exact_gate(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if only the exact-50 production gate were removed."""
    fixture = playoff_snapshot_fixture
    manifest = read_json(fixture.playoff_manifest)
    pages = manifest["sources"]["basketball_reference"][
        "playoff_pages"
    ]
    pages.append(dict(pages[0]))
    write_json(fixture.playoff_manifest, manifest)

    rejected = assert_archive_rejected(fixture)

    assert (
        "playoff pages must contain exactly 50 records"
        in rejected.stderr
    )


def test_archive_rejects_count_preserving_out_of_range_seed_identity(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if null play_in_month admitted an eighth identity."""
    fixture = playoff_snapshot_fixture
    old_name = "NBA_2026_games-april.html"
    new_name = "NBA_2027_games.html"
    old_body = fixture.playoff_cache / old_name
    new_body = fixture.playoff_cache / new_name
    old_sidecar = old_body.with_name(f"{old_name}.meta.json")
    new_sidecar = new_body.with_name(f"{new_name}.meta.json")
    old_body.rename(new_body)
    old_sidecar.rename(new_sidecar)
    metadata = read_json(new_sidecar)
    metadata.update({
        "season": 2027,
        "month": None,
        "url": (
            "https://www.basketball-reference.com/leagues/"
            f"{new_name}"
        ),
        "seeded_from": "raw/bref/NBA_2027_games-.html",
    })
    write_json(new_sidecar, metadata)
    manifest = read_json(fixture.playoff_manifest)
    pages = manifest["sources"]["basketball_reference"][
        "playoff_pages"
    ]
    record = next(
        page for page in pages if page["path"].endswith(f"/{old_name}")
    )
    record.update({
        "season": 2027,
        "month": None,
        "url": metadata["url"],
        "path": f"raw/playoff_bref/{new_name}",
        "seeded_from": metadata["seeded_from"],
    })
    write_json(fixture.playoff_manifest, manifest)

    rejected = assert_archive_rejected(fixture)

    assert "playoff page identity is invalid" in rejected.stderr


def test_archive_rejects_missing_dedicated_page(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    fixture = playoff_snapshot_fixture
    remove_pair(fixture.playoff_cache, "NBA_1984_games.html")
    assert_archive_rejected(fixture)


def test_archive_rejects_extra_dedicated_page(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    fixture = playoff_snapshot_fixture
    filename = "NBA_1983_games.html"
    payload = b"<html>extra dedicated</html>\n"
    record = playoff_record(season=1983, month=None, payload=payload)
    body = fixture.playoff_cache / filename
    body.write_bytes(payload)
    write_json(
        body.with_name(f"{filename}.meta.json"),
        {key: value for key, value in record.items() if key != "path"},
    )
    append_manifest_record(fixture, record)

    assert_archive_rejected(fixture)


def test_archive_rejects_missing_seeded_page(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    fixture = playoff_snapshot_fixture
    remove_pair(
        fixture.playoff_cache,
        "NBA_2020_games-august.html",
    )
    assert_archive_rejected(fixture)


def test_archive_rejects_extra_seeded_page(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    fixture = playoff_snapshot_fixture
    filename = "NBA_2019_games-april.html"
    payload = b"<html>extra seeded</html>\n"
    record = playoff_record(season=2019, month="april", payload=payload)
    body = fixture.playoff_cache / filename
    body.write_bytes(payload)
    write_json(
        body.with_name(f"{filename}.meta.json"),
        {key: value for key, value in record.items() if key != "path"},
    )
    append_manifest_record(fixture, record)

    assert_archive_rejected(fixture)


def test_archive_rejects_seeded_source_sha256_mismatch(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    fixture = playoff_snapshot_fixture
    mutate_sidecar(
        fixture,
        "NBA_2020_games-august.html",
        "source_sha256",
        "0" * 64,
    )
    rejected = assert_archive_rejected(fixture)
    assert "sidecar NBA_2020_games-august.html" in rejected.stderr


def test_archive_rejects_source_mutated_after_final_accepted_hash(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if a byte-restored accepted-hash race could publish."""
    fixture = playoff_snapshot_fixture
    source_body = (
        fixture.playoff_cache / "NBA_1984_games.html"
    )
    source_before = tree_sha256(fixture.playoff_cache)
    mutation_root = fixture.repo.parent / "source-hash-race"
    mutation_root.mkdir()
    environment = accepted_hash_environment(
        mutation_root,
        target=source_body,
        trigger_count=3,
    )

    archived = run_archive(fixture, environment)

    assert (mutation_root / "mutation-marker").exists()
    assert archived.returncode != 0
    assert "playoff recovery input changed during operation" in (
        archived.stderr
    )
    assert not snapshot_path(fixture).exists()
    assert tree_sha256(fixture.playoff_cache) == source_before


def test_existing_snapshot_rejects_source_mutated_after_accepted_hash(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if immutable retry accepted a byte-restored source race."""
    fixture = playoff_snapshot_fixture
    assert run_archive(fixture).returncode == 0
    snapshot_before = tree_sha256(snapshot_path(fixture))
    source_before = tree_sha256(fixture.playoff_cache)
    mutation_root = fixture.repo.parent / "existing-source-hash-race"
    mutation_root.mkdir()
    environment = accepted_hash_environment(
        mutation_root,
        target=(
            fixture.playoff_cache / "NBA_1984_games.html"
        ),
        trigger_count=2,
    )

    retried = run_archive(fixture, environment)

    assert (mutation_root / "mutation-marker").exists()
    assert retried.returncode != 0
    assert "playoff recovery input changed during operation" in (
        retried.stderr
    )
    assert tree_sha256(fixture.playoff_cache) == source_before
    assert tree_sha256(snapshot_path(fixture)) == snapshot_before


@pytest.mark.parametrize(
    ("target_kind", "trigger_count"),
    [
        ("historical_manifest", 3),
        ("historical_cache", 2),
    ],
)
def test_existing_snapshot_rechecks_historical_boundary_state(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
    target_kind: str,
    trigger_count: int,
) -> None:
    """Would fail if immutable retry skipped final dependency stability."""
    fixture = playoff_snapshot_fixture
    assert run_archive(fixture).returncode == 0
    snapshot_before = tree_sha256(snapshot_path(fixture))
    historical_before = tree_sha256(fixture.historical_cache)
    historical_manifest_before = (
        fixture.historical_manifest.read_bytes()
    )
    target = (
        fixture.historical_manifest
        if target_kind == "historical_manifest"
        else fixture.historical_cache / "BAA_1947_games.html"
    )
    mutation_root = (
        fixture.repo.parent / f"existing-{target_kind}-race"
    )
    mutation_root.mkdir()
    environment = accepted_hash_environment(
        mutation_root,
        target=target,
        trigger_count=trigger_count,
    )

    retried = run_archive(fixture, environment)

    assert (mutation_root / "mutation-marker").exists()
    assert retried.returncode != 0
    assert "playoff recovery input changed during operation" in (
        retried.stderr
    )
    assert tree_sha256(snapshot_path(fixture)) == snapshot_before
    assert tree_sha256(fixture.historical_cache) == historical_before
    assert (
        fixture.historical_manifest.read_bytes()
        == historical_manifest_before
    )


def test_archive_rejects_playoff_manifest_changed_before_copy(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if the live manifest could diverge from its trusted pin."""
    fixture = playoff_snapshot_fixture
    fake_bin, _ = install_rsync_wrapper(
        fixture.repo.parent,
        """#!/usr/bin/env bash
set -euo pipefail
if test ! -e "$RACE_MARKER"; then
  : > "$RACE_MARKER"
  printf '\\n' >> "$NBA_PLAYOFF_RAW_MANIFEST"
fi
exec "$REAL_RSYNC" "$@"
""",
    )
    race_marker = fixture.repo.parent / "manifest-race"

    archived = run_archive(
        fixture,
        {
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "RACE_MARKER": str(race_marker),
            "REAL_RSYNC": shutil.which("rsync") or "rsync",
        },
    )

    assert race_marker.exists()
    assert archived.returncode != 0
    assert list(
        fixture.archive_root.glob("playoff-raw-*")
    ) == []


def test_archive_rejects_source_body_changed_during_copy(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if archive validated only the pre-copy body bytes."""
    fixture = playoff_snapshot_fixture
    fake_bin, _ = install_rsync_wrapper(
        fixture.repo.parent,
        """#!/usr/bin/env bash
set -euo pipefail
"$REAL_RSYNC" "$@"
dry_run=false
for argument in "$@"; do
  if test "$argument" = "--dry-run"; then dry_run=true; fi
done
if test "$dry_run" = false && test ! -e "$RACE_MARKER"; then
  : > "$RACE_MARKER"
  printf 'changed' >> "$SOURCE_BODY"
fi
""",
    )
    race_marker = fixture.repo.parent / "body-race"

    archived = run_archive(
        fixture,
        {
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "RACE_MARKER": str(race_marker),
            "REAL_RSYNC": shutil.which("rsync") or "rsync",
            "SOURCE_BODY": str(
                fixture.playoff_cache / "NBA_1984_games.html"
            ),
        },
    )

    assert race_marker.exists()
    assert archived.returncode != 0
    assert list(
        fixture.archive_root.glob("playoff-raw-*")
    ) == []


def test_archive_rejects_playoff_manifest_changed_during_copy(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if the post-copy manifest pin check were omitted."""
    fixture = playoff_snapshot_fixture
    fake_bin, _ = install_rsync_wrapper(
        fixture.repo.parent,
        """#!/usr/bin/env bash
set -euo pipefail
"$REAL_RSYNC" "$@"
dry_run=false
for argument in "$@"; do
  if test "$argument" = "--dry-run"; then dry_run=true; fi
done
if test "$dry_run" = false && test ! -e "$RACE_MARKER"; then
  : > "$RACE_MARKER"
  printf '\\n' >> "$NBA_PLAYOFF_RAW_MANIFEST"
fi
""",
    )
    race_marker = fixture.repo.parent / "manifest-during-copy-race"

    archived = run_archive(
        fixture,
        {
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "RACE_MARKER": str(race_marker),
            "REAL_RSYNC": shutil.which("rsync") or "rsync",
        },
    )

    assert race_marker.exists()
    assert archived.returncode != 0
    assert list(
        fixture.archive_root.glob("playoff-raw-*")
    ) == []


def test_restore_rejects_snapshot_body_changed_during_copy(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if restore did not revalidate snapshot bytes post-copy."""
    fixture = playoff_snapshot_fixture
    assert run_archive(fixture).returncode == 0
    make_restore_destination_absent(fixture)
    fake_bin, _ = install_rsync_wrapper(
        fixture.repo.parent,
        """#!/usr/bin/env bash
set -euo pipefail
"$REAL_RSYNC" "$@"
dry_run=false
for argument in "$@"; do
  if test "$argument" = "--dry-run"; then dry_run=true; fi
done
if test "$dry_run" = false && test ! -e "$RACE_MARKER"; then
  : > "$RACE_MARKER"
  printf 'changed' >> "$SNAPSHOT_BODY"
fi
""",
    )
    race_marker = fixture.repo.parent / "snapshot-body-race"

    restored = run_restore(
        fixture,
        {
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "RACE_MARKER": str(race_marker),
            "REAL_RSYNC": shutil.which("rsync") or "rsync",
            "SNAPSHOT_BODY": str(
                snapshot_path(fixture)
                / "playoff_bref/NBA_1984_games.html"
            ),
        },
    )

    assert race_marker.exists()
    assert restored.returncode != 0
    assert not fixture.destination.exists()


def test_restore_rejects_snapshot_mutated_after_final_accepted_hash(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if a byte-restored snapshot race could publish."""
    fixture = playoff_snapshot_fixture
    assert run_archive(fixture).returncode == 0
    make_restore_destination_absent(fixture)
    snapshot = snapshot_path(fixture)
    snapshot_before = tree_sha256(snapshot)
    mutation_root = fixture.repo.parent / "snapshot-hash-race"
    mutation_root.mkdir()
    environment = accepted_hash_environment(
        mutation_root,
        target=(
            snapshot / "playoff_bref/NBA_1984_games.html"
        ),
        trigger_count=3,
    )

    restored = run_restore(fixture, environment)

    assert (mutation_root / "mutation-marker").exists()
    assert restored.returncode != 0
    assert "playoff recovery input changed during operation" in (
        restored.stderr
    )
    assert not fixture.destination.exists()
    assert tree_sha256(snapshot) == snapshot_before


def test_restore_rejects_snapshot_manifest_mismatch(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if snapshot identity were inferred only from its path."""
    fixture = playoff_snapshot_fixture
    assert run_archive(fixture).returncode == 0
    make_restore_destination_absent(fixture)
    snapshot_manifest = (
        snapshot_path(fixture) / "playoff_games_manifest.json"
    )
    snapshot_manifest.write_text("{}\n", encoding="utf-8")

    restored = run_restore(fixture)

    assert restored.returncode != 0
    assert not fixture.destination.exists()


def test_restore_rejects_malformed_sha256sums(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if checksum syntax were accepted without canonical bytes."""
    fixture = playoff_snapshot_fixture
    assert run_archive(fixture).returncode == 0
    make_restore_destination_absent(fixture)
    (snapshot_path(fixture) / "SHA256SUMS").write_text(
        "not a checksum\n",
        encoding="utf-8",
    )

    restored = run_restore(fixture)

    assert restored.returncode != 0
    assert not fixture.destination.exists()


def test_restore_rejects_incomplete_sha256sums(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if a checksum inventory could omit one cache file."""
    fixture = playoff_snapshot_fixture
    assert run_archive(fixture).returncode == 0
    make_restore_destination_absent(fixture)
    checksums = snapshot_path(fixture) / "SHA256SUMS"
    lines = checksums.read_text(encoding="utf-8").splitlines(keepends=True)
    checksums.write_text("".join(lines[:-1]), encoding="utf-8")

    restored = run_restore(fixture)

    assert restored.returncode != 0
    assert not fixture.destination.exists()


def test_restore_rejects_missing_historical_manifest(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if restore ignored the manifest dependency entirely."""
    fixture = playoff_snapshot_fixture
    assert run_archive(fixture).returncode == 0
    make_restore_destination_absent(fixture)
    fixture.historical_manifest.unlink()

    restored = run_restore(fixture)

    assert restored.returncode != 0
    assert not fixture.destination.exists()


def test_restore_rejects_changed_historical_manifest(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if the pinned historical SHA were not enforced."""
    fixture = playoff_snapshot_fixture
    assert run_archive(fixture).returncode == 0
    make_restore_destination_absent(fixture)
    fixture.historical_manifest.write_text("{}\n", encoding="utf-8")

    restored = run_restore(fixture)

    assert restored.returncode != 0
    assert not fixture.destination.exists()


@pytest.mark.parametrize(
    "stream_case",
    ["valid_valid", "invalid_valid"],
)
def test_archive_rejects_streamed_historical_manifest_documents(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
    stream_case: str,
) -> None:
    """Would fail if delegated validation trusted the final JSON value."""
    fixture = playoff_snapshot_fixture
    valid_document = read_json(fixture.historical_manifest)
    first_document = (
        valid_document
        if stream_case == "valid_valid"
        else {}
    )
    write_json_documents(
        fixture.historical_manifest,
        [first_document, valid_document],
    )
    refresh_historical_manifest_pin(fixture)

    rejected = assert_archive_rejected(fixture)

    assert (
        "historical manifest must contain exactly one JSON document"
        in rejected.stderr
    )


@pytest.mark.parametrize(
    "stream_case",
    ["valid_valid", "invalid_valid"],
)
def test_archive_rejects_streamed_historical_sidecar_documents(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
    stream_case: str,
) -> None:
    """Would fail if delegated sidecar jq used last-result semantics."""
    fixture = playoff_snapshot_fixture
    sidecar = (
        fixture.historical_cache
        / "BAA_1947_games.html.meta.json"
    )
    valid_document = read_json(sidecar)
    first_document = (
        valid_document
        if stream_case == "valid_valid"
        else []
    )
    write_json_documents(
        sidecar,
        [first_document, valid_document],
    )

    rejected = assert_archive_rejected(fixture)

    assert (
        "historical sidecar must contain exactly one JSON document"
        in rejected.stderr
    )


def test_archive_rejects_wrong_historical_manifest_pin(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if the playoff manifest's dependency SHA were untrusted."""
    fixture = playoff_snapshot_fixture
    manifest = read_json(fixture.playoff_manifest)
    manifest["sources"]["basketball_reference"][
        "historical_manifest"
    ]["sha256"] = "0" * 64
    write_json(fixture.playoff_manifest, manifest)

    assert_archive_rejected(fixture)


def test_archive_rejects_missing_historical_cache(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if recovery could outlive its required source cache."""
    fixture = playoff_snapshot_fixture
    shutil.rmtree(fixture.historical_cache)

    assert_archive_rejected(fixture)


def test_archive_rejects_historical_cache_manifest_inconsistency(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if historical verification stopped at the manifest hash."""
    fixture = playoff_snapshot_fixture
    (
        fixture.historical_cache / "BAA_1947_games.html"
    ).write_bytes(b"tampered historical")

    assert_archive_rejected(fixture)


def test_archive_rejects_historical_manifest_boundary_mutation(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if a byte-restored historical manifest race published."""
    fixture = playoff_snapshot_fixture
    historical_before = fixture.historical_manifest.read_bytes()
    mutation_root = fixture.repo.parent / "archive-historical-manifest-race"
    mutation_root.mkdir()
    environment = accepted_hash_environment(
        mutation_root,
        target=fixture.historical_manifest,
        trigger_count=4,
    )

    archived = run_archive(fixture, environment)

    assert (mutation_root / "mutation-marker").exists()
    assert archived.returncode != 0
    assert "playoff recovery input changed during operation" in (
        archived.stderr
    )
    assert not snapshot_path(fixture).exists()
    assert fixture.historical_manifest.read_bytes() == historical_before


def test_archive_rejects_historical_cache_boundary_mutation(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if a byte-restored historical body race published."""
    fixture = playoff_snapshot_fixture
    historical_before = tree_sha256(fixture.historical_cache)
    mutation_root = fixture.repo.parent / "archive-historical-cache-race"
    mutation_root.mkdir()
    environment = accepted_hash_environment(
        mutation_root,
        target=(
            fixture.historical_cache / "BAA_1947_games.html"
        ),
        trigger_count=3,
    )

    archived = run_archive(fixture, environment)

    assert (mutation_root / "mutation-marker").exists()
    assert archived.returncode != 0
    assert "playoff recovery input changed during operation" in (
        archived.stderr
    )
    assert not snapshot_path(fixture).exists()
    assert tree_sha256(fixture.historical_cache) == historical_before


def test_restore_rejects_historical_manifest_boundary_mutation(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if restore accepted a byte-restored dependency race."""
    fixture = playoff_snapshot_fixture
    assert run_archive(fixture).returncode == 0
    make_restore_destination_absent(fixture)
    historical_before = fixture.historical_manifest.read_bytes()
    mutation_root = fixture.repo.parent / "restore-historical-manifest-race"
    mutation_root.mkdir()
    environment = accepted_hash_environment(
        mutation_root,
        target=fixture.historical_manifest,
        trigger_count=3,
    )

    restored = run_restore(fixture, environment)

    assert (mutation_root / "mutation-marker").exists()
    assert restored.returncode != 0
    assert "playoff recovery input changed during operation" in (
        restored.stderr
    )
    assert not fixture.destination.exists()
    assert fixture.historical_manifest.read_bytes() == historical_before


def test_restore_rejects_historical_cache_boundary_mutation(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if restore accepted a byte-restored historical body race."""
    fixture = playoff_snapshot_fixture
    assert run_archive(fixture).returncode == 0
    make_restore_destination_absent(fixture)
    historical_before = tree_sha256(fixture.historical_cache)
    mutation_root = fixture.repo.parent / "restore-historical-cache-race"
    mutation_root.mkdir()
    environment = accepted_hash_environment(
        mutation_root,
        target=(
            fixture.historical_cache / "BAA_1947_games.html"
        ),
        trigger_count=2,
    )

    restored = run_restore(fixture, environment)

    assert (mutation_root / "mutation-marker").exists()
    assert restored.returncode != 0
    assert "playoff recovery input changed during operation" in (
        restored.stderr
    )
    assert not fixture.destination.exists()
    assert tree_sha256(fixture.historical_cache) == historical_before


def test_restore_refuses_existing_destination(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if restore merged into or replaced live cache state."""
    fixture = playoff_snapshot_fixture
    assert run_archive(fixture).returncode == 0
    sentinel = fixture.destination / "sentinel"
    sentinel.write_text("keep\n", encoding="utf-8")

    restored = run_restore(fixture)

    assert restored.returncode != 0
    assert sentinel.read_text(encoding="utf-8") == "keep\n"


def test_archive_rejects_existing_snapshot_with_different_content(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if a retry blessed tampered immutable content."""
    fixture = playoff_snapshot_fixture
    assert run_archive(fixture).returncode == 0
    body = (
        snapshot_path(fixture)
        / "playoff_bref/NBA_1984_games.html"
    )
    body.write_bytes(b"tampered snapshot")

    archived = run_archive(fixture)

    assert archived.returncode != 0
    assert body.read_bytes() == b"tampered snapshot"


def test_archive_rejects_cross_device_publication_attempt(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if publication tolerated a stage outside archive_root."""
    fixture = playoff_snapshot_fixture
    fake_bin = fixture.repo.parent / "fake-bin"
    fake_bin.mkdir()
    external_stage = fixture.repo.parent / "external-stage"
    fake_mktemp = fake_bin / "mktemp"
    fake_mktemp.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if test "${1:-}" = "-d" && [[ "${2:-}" == *".playoff-archive."* ]]; then
  mkdir "$EXTERNAL_STAGE"
  printf '%s\\n' "$EXTERNAL_STAGE"
else
  exec "$REAL_MKTEMP" "$@"
fi
""",
        encoding="utf-8",
    )
    fake_mktemp.chmod(0o755)
    fake_mv = fake_bin / "mv"
    fake_mv.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if test "${1:-}" = "-T" && test "${3:-}" = "$SNAPSHOT"; then
  printf 'cross-device publication rejected\\n' >&2
  exit 18
fi
exec "$REAL_MV" "$@"
""",
        encoding="utf-8",
    )
    fake_mv.chmod(0o755)

    archived = run_archive(
        fixture,
        {
            "EXTERNAL_STAGE": str(external_stage),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "REAL_MKTEMP": shutil.which("mktemp") or "mktemp",
            "REAL_MV": shutil.which("mv") or "mv",
            "SNAPSHOT": str(snapshot_path(fixture)),
        },
    )

    assert archived.returncode != 0
    assert "cross-device publication rejected" in archived.stderr
    assert not snapshot_path(fixture).exists()
    assert not external_stage.exists()


def test_restore_rejects_cross_device_publication_attempt(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if restore tolerated a stage outside destination parent."""
    fixture = playoff_snapshot_fixture
    assert run_archive(fixture).returncode == 0
    make_restore_destination_absent(fixture)
    fake_bin = fixture.repo.parent / "fake-bin"
    fake_bin.mkdir()
    external_stage = fixture.repo.parent / "external-restore-stage"
    fake_mktemp = fake_bin / "mktemp"
    fake_mktemp.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if test "${1:-}" = "-d" && [[ "${2:-}" == *".playoff-restore."* ]]; then
  mkdir "$EXTERNAL_STAGE"
  printf '%s\\n' "$EXTERNAL_STAGE"
else
  exec "$REAL_MKTEMP" "$@"
fi
""",
        encoding="utf-8",
    )
    fake_mktemp.chmod(0o755)
    fake_mv = fake_bin / "mv"
    fake_mv.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if test "${1:-}" = "-T" && test "${3:-}" = "$DESTINATION"; then
  printf 'cross-device restore rejected\\n' >&2
  exit 18
fi
exec "$REAL_MV" "$@"
""",
        encoding="utf-8",
    )
    fake_mv.chmod(0o755)

    restored = run_restore(
        fixture,
        {
            "DESTINATION": str(fixture.destination),
            "EXTERNAL_STAGE": str(external_stage),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "REAL_MKTEMP": shutil.which("mktemp") or "mktemp",
            "REAL_MV": shutil.which("mv") or "mv",
        },
    )

    assert restored.returncode != 0
    assert "cross-device restore rejected" in restored.stderr
    assert not fixture.destination.exists()
    assert not external_stage.exists()


def test_restore_rejects_structurally_invalid_snapshot(
    playoff_snapshot_fixture: PlayoffSnapshotFixture,
) -> None:
    """Would fail if root entries outside the exact snapshot family survived."""
    fixture = playoff_snapshot_fixture
    assert run_archive(fixture).returncode == 0
    make_restore_destination_absent(fixture)
    snapshot = snapshot_path(fixture)
    (snapshot / "unexpected.txt").write_text(
        "unexpected\n",
        encoding="utf-8",
    )
    rewrite_snapshot_checksums(snapshot)

    restored = run_restore(fixture)

    assert restored.returncode != 0
    assert not fixture.destination.exists()
