from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

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


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


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
    return subprocess.run(
        ["bash", str(ARCHIVE_SCRIPT)],
        cwd=fixture.repo,
        env=playoff_environment(fixture, overrides),
        capture_output=True,
        text=True,
    )


def run_restore(
    fixture: PlayoffSnapshotFixture,
    overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    assert RESTORE_SCRIPT.is_file()
    return subprocess.run(
        ["bash", str(RESTORE_SCRIPT)],
        cwd=fixture.repo,
        env=playoff_environment(fixture, overrides),
        capture_output=True,
        text=True,
    )


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

    assert_archive_rejected(fixture)


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

    assert_archive_rejected(fixture)


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
    assert_archive_rejected(fixture)


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
