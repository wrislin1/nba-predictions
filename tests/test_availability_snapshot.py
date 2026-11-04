"""Tests for the availability snapshot validator and the archive/restore scripts (Task 17).

The tiny end-to-end tree of ``test_availability_build`` (``seed_full_tree``
plus ``seed_gamebooks``) is seeded once per module and built into
``data/parquet-large/availability``; every test works on its own copy. The
subprocess paths (the ``validate-layout`` CLI and the two shell scripts)
receive the same slot-grid trim and test ledger as the in-process tests
through a ``sitecustomize.py`` shim on ``PYTHONPATH``, exactly as
``test_player_games_snapshot`` narrows the season scope for its scripts.

The scripts run as root in this container, so no refusal here depends on
file permissions: every failure is driven through content or layout.
"""

from __future__ import annotations

import dataclasses
import functools
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import test_availability_build as build_tests
import test_player_games_snapshot as pg_tests
from nba_data import availability_build, availability_snapshot, injury_reports


REPO_ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_SCRIPT = REPO_ROOT / "scripts/archive_availability_snapshot.sh"
RESTORE_SCRIPT = REPO_ROOT / "scripts/restore_availability_snapshot.sh"

FULL_REPORT_IDS = [
    report_id
    for month in sorted(build_tests.FULL_MONTHS)
    for report_id in (
        *build_tests.FULL_MONTHS[month]["present"],
        *build_tests.FULL_MONTHS[month]["unavailable"],
    )
]
RAW_TREES = (
    "raw/stats_nba/box_rosters",
    "raw/nba_injury_reports",
    "raw/sportsdataverse/nba_stats_injury_report",
    "raw/nba_gamebooks",
)
SNAPSHOT_TREES = RAW_TREES + ("parquet-large/availability",)
JANUARY_PDF = Path("raw/nba_injury_reports/pdfs/2024-01") / (build_tests.JANUARY_REPORT + ".pdf")


@dataclasses.dataclass(frozen=True)
class Tree:
    data_dir: Path
    ledger: Path
    manifest_sha256: str
    shim_dir: Path

    @property
    def raw_root(self) -> Path:
        return self.data_dir / "raw"

    @property
    def publication_root(self) -> Path:
        return self.data_dir / "parquet-large/availability"

    @property
    def player_games_root(self) -> Path:
        return self.data_dir / "parquet-large/player_games"

    @property
    def snapshot_name(self) -> str:
        return f"availability-archive-2026-{self.manifest_sha256[:12]}"


@dataclasses.dataclass(frozen=True)
class Archived:
    tree: Tree
    archive_root: Path

    @property
    def snapshot(self) -> Path:
        return self.archive_root / self.tree.snapshot_name


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def _entries(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.iterdir())


def _write_shim(shim_dir: Path, ledger: Path) -> Path:
    """A ``sitecustomize.py`` that trims the slot grids and points the build at the test ledger."""
    shim_dir.mkdir(parents=True, exist_ok=True)
    (shim_dir / "sitecustomize.py").write_text(
        "from pathlib import Path\n"
        "from nba_data import availability_build, injury_reports\n"
        f"report_ids = {json.dumps(FULL_REPORT_IDS)}\n"
        "slots = tuple(sorted(\n"
        "    (injury_reports.slot_from_report_id(report_id) for report_id in report_ids),\n"
        "    key=lambda slot: (slot.slot_date, slot.slot_start_et, slot.grid),\n"
        "))\n"
        "def trimmed(months=None):\n"
        "    if months is None:\n"
        "        return slots\n"
        "    selected = set(months)\n"
        "    return tuple(slot for slot in slots if slot.month in selected)\n"
        "injury_reports.enumerate_slots = trimmed\n"
        f"availability_build.EXCEPTIONS_PATH = Path({str(ledger)!r})\n",
        encoding="utf-8",
    )
    return shim_dir


def _patch_process(monkeypatch, ledger: Path) -> None:
    build_tests.patch_enumerated_slots(monkeypatch, FULL_REPORT_IDS)
    monkeypatch.setattr(availability_build, "EXCEPTIONS_PATH", ledger)


@pytest.fixture(scope="module")
def seed(tmp_path_factory) -> Tree:
    """Seed and build the tiny end-to-end tree once; tests copy it."""
    base = tmp_path_factory.mktemp("availability-seed")
    data_dir = base / "data"
    data_dir.mkdir()
    with pytest.MonkeyPatch.context() as monkeypatch:
        raw_root, player_games_root, ledger = build_tests.seed_full_tree(data_dir, monkeypatch)
        build_tests.seed_gamebooks(data_dir)
        publication = data_dir / "parquet-large/availability"
        publication.mkdir()
        availability_build.build_from_roots(
            raw_root, player_games_root, publication, exceptions_path=ledger,
        )
    return Tree(
        data_dir=data_dir,
        ledger=ledger,
        manifest_sha256=_sha256(publication / "_manifest.json"),
        shim_dir=_write_shim(base / "shim", ledger),
    )


@pytest.fixture
def tree(seed: Tree, tmp_path, monkeypatch) -> Tree:
    data_dir = tmp_path / "data"
    shutil.copytree(seed.data_dir, data_dir, symlinks=True)
    _patch_process(monkeypatch, seed.ledger)
    return dataclasses.replace(seed, data_dir=data_dir)


def _validate(tree: Tree, manifest_sha256=None) -> dict:
    return availability_snapshot.validate_layout(
        tree.raw_root, tree.publication_root, tree.player_games_root, manifest_sha256,
    )


# --------------------------------------------------------------------------
# validate_layout
# --------------------------------------------------------------------------


def test_validate_layout_accepts_a_complete_tree_and_returns_the_manifest(tree):
    """Would fail if the validator missed a raw subtree, miscounted the attested
    pairs or returned anything but the recomputed manifest and its digest."""
    stored = json.loads((tree.publication_root / "_manifest.json").read_text(encoding="utf-8"))

    report = _validate(tree)

    assert report["status"] == "ok"
    assert report["manifest_sha256"] == tree.manifest_sha256
    assert report["manifest"] == stored
    assert report["publication_partitions"] == 46
    assert report["box_roster_pairs"] == 2 * len(build_tests.SEASON_GAMES + build_tests.SEEDED_GAMES)
    expected_pairs = {
        "box_rosters": len(list((tree.raw_root / "stats_nba/box_rosters").rglob("*.meta.json"))),
        "injury_report_pdfs": len(list((tree.raw_root / "nba_injury_reports/pdfs").rglob("*.meta.json"))),
        "injury_report_generations": len(
            list((tree.raw_root / "nba_injury_reports/inventory").rglob("*.meta.json"))
        ),
        "derivative": len(list((tree.raw_root / "sportsdataverse/nba_stats_injury_report").rglob("*.meta.json"))),
        "gamebook_pdfs": 1,
        "gamebook_comparisons": 1,
    }
    assert expected_pairs["box_rosters"] == report["box_roster_pairs"]
    assert expected_pairs["injury_report_pdfs"] == len(build_tests.FULL_REPORTS)
    assert expected_pairs["injury_report_generations"] == len(build_tests.FULL_MONTHS)
    assert expected_pairs["derivative"] == 2
    assert report["raw_pairs"] == expected_pairs
    # the optional digest pin accepts the exact digest only
    assert _validate(tree, tree.manifest_sha256)["manifest_sha256"] == tree.manifest_sha256


def _damage_lower_generation(tree: Tree) -> str:
    """Write a verifying gen-0002 above gen-0001 and then damage gen-0001 only."""
    inventory = tree.raw_root / "nba_injury_reports/inventory/2024-01"
    first = inventory / "gen-0001.json"
    slots = json.loads(first.read_text(encoding="utf-8"))["slots"]
    build_tests.write_generation(tree.data_dir, "2024-01", slots)
    assert (inventory / "gen-0002.json").is_file()
    body = first.read_bytes()
    first.write_bytes(body[:-2] + b" }")
    current, damaged = injury_reports.read_current_inventory(tree.data_dir, "2024-01")
    assert current["generation"] == 2 and [path.name for path in damaged] == ["gen-0001.json"]
    return "gen-0001"


def _missing_box_pair(tree: Tree) -> str:
    season_dir = tree.raw_root / "stats_nba/box_rosters/season=2024"
    body = season_dir / f"{build_tests.BULLS_HORNETS.game_id}.traditional.json"
    body.unlink()
    body.with_name(body.name + ".meta.json").unlink()
    return "missing box-roster"


def _orphan_box_body(tree: Tree) -> str:
    season_dir = tree.raw_root / "stats_nba/box_rosters/season=2024"
    (season_dir / f"{build_tests.BULLS_HORNETS.game_id}.summary.json.meta.json").unlink()
    return "orphan"


def _tampered_pdf(tree: Tree) -> str:
    pdf = tree.data_dir / JANUARY_PDF
    pdf.write_bytes(pdf.read_bytes() + b"\n")
    return build_tests.JANUARY_REPORT


def _missing_derivative_sidecar(tree: Tree) -> str:
    sidecar = tree.raw_root / "sportsdataverse/nba_stats_injury_report/injury_report_2024.parquet.meta.json"
    sidecar.unlink()
    return "orphan"


def _tampered_gamebook_comparison(tree: Tree) -> str:
    (record,) = (
        path for path in (tree.raw_root / "nba_gamebooks/comparisons").glob("*.json")
        if not path.name.endswith(".meta.json")
    )
    body = record.read_bytes()
    record.write_bytes(body[:-2] + b" }")
    return "comparisons"


def _symlink_in_raw_tree(tree: Tree) -> str:
    (pdf,) = (tree.raw_root / "nba_gamebooks").glob("*.book.pdf")
    (tree.raw_root / "nba_gamebooks/alias.book.pdf").symlink_to(pdf)
    return "unsafe non-regular"


def _symlinked_derivative_body(tree: Tree) -> str:
    body = tree.raw_root / "sportsdataverse/nba_stats_injury_report/injury_report_2026.parquet"
    aside = tree.data_dir / "aside.parquet"
    body.rename(aside)
    body.symlink_to(aside)
    return "unsafe non-regular"


def _stray_part_file(tree: Tree) -> str:
    (tree.raw_root / "nba_injury_reports/inventory/2024-01/gen-0002.json.part").write_bytes(b"{")
    return "orphan raw body|unexpected raw entry"


def _unrecognised_box_entry(tree: Tree) -> str:
    (tree.raw_root / "stats_nba/box_rosters/season=2024/notes.txt").write_text("x", encoding="utf-8")
    return "orphan raw body|unexpected raw entry"


def _missing_raw_subtree(tree: Tree) -> str:
    shutil.rmtree(tree.raw_root / "nba_gamebooks")
    return "missing raw subtree"


def _mis_signed_generation_sidecar(tree: Tree, field: str, value) -> str:
    """Rewrite one identity field of a generation sidecar; the body still hashes."""
    sidecar = tree.raw_root / "nba_injury_reports/inventory/2024-01/gen-0001.json.meta.json"
    document = json.loads(sidecar.read_text(encoding="utf-8"))
    assert document[field] != value
    document[field] = value
    sidecar.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    current, damaged = injury_reports.read_current_inventory(tree.data_dir, "2024-01")
    assert current["generation"] == 1 and damaged == ()
    return rf"raw sidecar identity mismatch .*gen-0001\.json: {field}"


RAW_DEFECTS = {
    "missing_box_pair": _missing_box_pair,
    "orphan_box_body": _orphan_box_body,
    "damaged_lower_generation": _damage_lower_generation,
    "tampered_pdf": _tampered_pdf,
    "missing_derivative_sidecar": _missing_derivative_sidecar,
    "tampered_gamebook_comparison": _tampered_gamebook_comparison,
    "symlink_in_raw_tree": _symlink_in_raw_tree,
    "symlinked_derivative_body": _symlinked_derivative_body,
    "stray_part_file": _stray_part_file,
    "unrecognised_box_entry": _unrecognised_box_entry,
    "missing_raw_subtree": _missing_raw_subtree,
    "generation_sidecar_wrong_generation": functools.partial(_mis_signed_generation_sidecar, field="generation", value=2),
    "generation_sidecar_wrong_month": functools.partial(_mis_signed_generation_sidecar, field="month", value="2024-02"),
    "generation_sidecar_wrong_resource": functools.partial(_mis_signed_generation_sidecar, field="resource", value="injury_report"),
}


@pytest.mark.parametrize("defect", sorted(RAW_DEFECTS))
def test_validate_layout_rejects_each_raw_defect(tree, defect):
    """Would fail if any raw pair, including a lower inventory generation,
    escaped attestation, or if an orphan, a stray file or a symlink were tolerated."""
    match = RAW_DEFECTS[defect](tree)

    with pytest.raises(availability_snapshot.AvailabilitySnapshotError, match=match):
        _validate(tree)


def test_validate_layout_rejects_publication_defects_and_wrong_pins(tree):
    """Would fail if a partition byte, a wrong digest pin or a changed
    ``player_games`` manifest passed the validator."""
    partition = tree.publication_root / "season=2024/game_rosters.parquet"
    original = partition.read_bytes()
    partition.write_bytes(original[:-1] + bytes([original[-1] ^ 0xFF]))
    with pytest.raises(availability_snapshot.AvailabilitySnapshotError, match="partition"):
        _validate(tree)
    partition.write_bytes(original)

    with pytest.raises(availability_snapshot.AvailabilitySnapshotError, match="mismatch"):
        _validate(tree, "0" * 64)
    with pytest.raises(availability_snapshot.AvailabilitySnapshotError, match="64-character"):
        _validate(tree, tree.manifest_sha256.upper())
    with pytest.raises(availability_snapshot.AvailabilitySnapshotError, match="64-character"):
        _validate(tree, "abc")

    manifest = tree.player_games_root / "_manifest.json"
    stored = manifest.read_bytes()
    manifest.write_bytes(stored + b"\n")
    with pytest.raises(availability_snapshot.AvailabilitySnapshotError, match="references"):
        _validate(tree)
    manifest.write_bytes(stored)

    assert _validate(tree, tree.manifest_sha256)["status"] == "ok"
    with pytest.raises(availability_snapshot.AvailabilitySnapshotError, match="publication"):
        availability_snapshot.validate_layout(
            tree.raw_root, tree.data_dir / "nowhere", tree.player_games_root,
        )
    with pytest.raises(availability_snapshot.AvailabilitySnapshotError, match="raw"):
        availability_snapshot.validate_layout(
            tree.data_dir, tree.publication_root, tree.player_games_root,
        )


def _run_cli(tree: Tree, *extra: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{REPO_ROOT / 'src'}:{tree.shim_dir}"
    return subprocess.run(
        [
            sys.executable, "-m", "nba_data.availability_snapshot", "validate-layout",
            "--raw-root", str(tree.raw_root),
            "--publication-root", str(tree.publication_root),
            "--player-games-root", str(tree.player_games_root),
            *extra,
        ],
        env=env, cwd=REPO_ROOT, capture_output=True, text=True, timeout=300,
    )


def test_cli_validate_layout_exit_codes(tree):
    ok = _run_cli(tree)
    assert ok.returncode == 0, ok.stderr
    report = json.loads(ok.stdout)
    assert report["status"] == "ok" and report["manifest_sha256"] == tree.manifest_sha256
    assert "manifest" not in report

    pinned = _run_cli(tree, "--manifest-sha256", tree.manifest_sha256)
    assert pinned.returncode == 0, pinned.stderr

    wrong_pin = _run_cli(tree, "--manifest-sha256", "0" * 64)
    assert wrong_pin.returncode == 1
    assert "mismatch" in wrong_pin.stderr

    _tampered_pdf(tree)
    damaged = _run_cli(tree)
    assert damaged.returncode == 1
    assert build_tests.JANUARY_REPORT in damaged.stderr

    usage = subprocess.run(
        [sys.executable, "-m", "nba_data.availability_snapshot", "validate-layout"],
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert usage.returncode == 2


# --------------------------------------------------------------------------
# scripts
# --------------------------------------------------------------------------


def _run_archive(tree: Tree, archive_root: Path, *, extra_env=None, path_prefix=None):
    env = dict(os.environ)
    env["NBA_AVAILABILITY_DATA_DIR"] = str(tree.data_dir)
    env["NBA_AVAILABILITY_ARCHIVE_ROOT"] = str(archive_root)
    env["PYTHONPATH"] = str(tree.shim_dir)
    default_capacity = pg_tests._default_capacity_shim(Path(archive_root).parent)
    prefixes = [str(path_prefix)] if path_prefix is not None else []
    prefixes.append(str(default_capacity))
    env["PATH"] = ":".join(prefixes) + f":{env.get('PATH', '')}"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(ARCHIVE_SCRIPT)], env=env, cwd=str(REPO_ROOT),
        capture_output=True, text=True, timeout=600,
    )


def _run_restore(archived: Archived, restore_root: Path, *, name=None, extra_env=None, path_prefix=None):
    env = dict(os.environ)
    env["NBA_AVAILABILITY_ARCHIVE_ROOT"] = str(archived.archive_root)
    env["NBA_AVAILABILITY_DATA_DIR"] = str(restore_root)
    env["PYTHONPATH"] = str(archived.tree.shim_dir)
    if path_prefix is not None:
        env["PATH"] = f"{path_prefix}:{env.get('PATH', '')}"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(RESTORE_SCRIPT), name or archived.tree.snapshot_name],
        env=env, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=600,
    )


@pytest.fixture(scope="module")
def archived_seed(seed: Tree) -> Path:
    """Archive the module seed once with the real script; restore tests copy the result."""
    archive_root = seed.data_dir.parent / "archive_root"
    archive_root.mkdir()
    result = _run_archive(seed, archive_root)
    assert result.returncode == 0, result.stderr
    assert (archive_root / seed.snapshot_name).is_dir()
    return archive_root


@pytest.fixture
def archived(tree: Tree, archived_seed: Path, tmp_path) -> Archived:
    archive_root = tmp_path / "archive_root"
    shutil.copytree(archived_seed, archive_root, symlinks=True)
    return Archived(tree=tree, archive_root=archive_root)


def _restore_root_with_player_games(archived: Archived, tmp_path) -> Path:
    """An absent restore target beside a restored D1 publication (recovery step 2)."""
    restore_root = tmp_path / "restore"
    shutil.copytree(
        archived.tree.player_games_root, restore_root / "parquet-large/player_games",
    )
    return restore_root


def _assert_nothing_restored(restore_root: Path) -> None:
    for relative in SNAPSHOT_TREES:
        assert not (restore_root / relative).exists(), relative
        assert not (restore_root / relative).is_symlink(), relative
    for parent in {(restore_root / relative).parent for relative in SNAPSHOT_TREES}:
        if parent.is_dir():
            assert list(parent.glob(".availability-restore*")) == []


def test_archive_snapshot_copies_the_five_trees_with_exact_layout(tree, tmp_path):
    """Would fail if the snapshot layout, name or checksum inventory drifted
    from the contract, or if the player_games publication leaked into it."""
    archive_root = tmp_path / "archive_root"
    archive_root.mkdir()

    result = _run_archive(tree, archive_root)

    assert result.returncode == 0, result.stderr
    assert tree.manifest_sha256 in result.stdout
    snapshot = archive_root / tree.snapshot_name
    assert snapshot.is_dir()
    assert _entries(archive_root) == [tree.snapshot_name]
    assert _entries(snapshot) == ["SHA256SUMS", "parquet-large", "raw"]
    assert _entries(snapshot / "raw") == [
        "nba_gamebooks", "nba_injury_reports", "sportsdataverse", "stats_nba",
    ]
    assert _entries(snapshot / "raw/stats_nba") == ["box_rosters"]
    assert _entries(snapshot / "raw/sportsdataverse") == ["nba_stats_injury_report"]
    assert _entries(snapshot / "parquet-large") == ["availability"]
    for relative in SNAPSHOT_TREES:
        assert _tree_bytes(snapshot / relative) == _tree_bytes(tree.data_dir / relative), relative
    assert not (snapshot / "parquet-large/player_games").exists()

    lines = (snapshot / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    listed = [line.split("  ", 1)[1] for line in lines]
    assert listed == sorted(listed) and "SHA256SUMS" not in listed
    expected = sorted(
        f"{_sha256(path)}  {path.relative_to(snapshot).as_posix()}"
        for path in snapshot.rglob("*") if path.is_file() and path.name != "SHA256SUMS"
    )
    assert sorted(lines) == expected
    subprocess.run(
        ["sha256sum", "--check", "--strict", "SHA256SUMS"],
        cwd=snapshot, check=True, capture_output=True, text=True,
    )


def test_archive_snapshot_is_idempotent_only_for_byte_identical_final(tree, tmp_path):
    archive_root = tmp_path / "archive_root"
    archive_root.mkdir()
    first = _run_archive(tree, archive_root)
    assert first.returncode == 0, first.stderr
    snapshot = archive_root / tree.snapshot_name
    mtime_before = snapshot.stat().st_mtime_ns

    second = _run_archive(tree, archive_root)

    assert second.returncode == 0, second.stderr
    assert snapshot.stat().st_mtime_ns == mtime_before
    assert _entries(archive_root) == [tree.snapshot_name]

    sums_path = snapshot / "SHA256SUMS"
    lines = sums_path.read_text(encoding="utf-8").splitlines()
    digest, rest = lines[0].split("  ", 1)
    lines[0] = f"{('0' if digest[0] != '0' else '1') + digest[1:]}  {rest}"
    tampered_text = "\n".join(lines) + "\n"
    sums_path.write_text(tampered_text, encoding="utf-8")

    third = _run_archive(tree, archive_root)

    assert third.returncode != 0
    assert sums_path.read_text(encoding="utf-8") == tampered_text
    assert _entries(archive_root) == [tree.snapshot_name]


def test_archive_snapshot_rejects_stale_stage(tree, tmp_path):
    archive_root = tmp_path / "archive_root"
    archive_root.mkdir()
    stale = archive_root / ".availability-archive.stale123"
    stale.mkdir()
    (stale / "leftover.txt").write_text("from a crashed prior run\n", encoding="utf-8")

    result = _run_archive(tree, archive_root)

    assert result.returncode != 0
    assert "stale" in result.stderr.lower()
    assert (stale / "leftover.txt").exists()
    assert _entries(archive_root) == [stale.name]


def test_archive_snapshot_checks_capacity_before_copy(tree, tmp_path):
    capacity_shim_dir = pg_tests._make_capacity_shims(tmp_path / "capacity_shims")
    per_path = 500
    total = 500
    required = per_path * len(SNAPSHOT_TREES) + total // 5   # 2500 + 100

    reject_root = tmp_path / "archive_root_reject"
    reject_root.mkdir()
    reject = _run_archive(
        tree, reject_root, path_prefix=str(capacity_shim_dir),
        extra_env={
            "FAKE_DU_BYTES_PER_PATH": str(per_path),
            "FAKE_DF_TOTAL_BYTES": str(total),
            "FAKE_DF_AVAIL_BYTES": str(required - 1),
        },
    )
    assert reject.returncode != 0
    assert "capacity" in reject.stderr.lower()
    assert not any(reject_root.iterdir())

    accept_root = tmp_path / "archive_root_accept"
    accept_root.mkdir()
    accept = _run_archive(
        tree, accept_root, path_prefix=str(capacity_shim_dir),
        extra_env={
            "FAKE_DU_BYTES_PER_PATH": str(per_path),
            "FAKE_DF_TOTAL_BYTES": str(total),
            "FAKE_DF_AVAIL_BYTES": str(required),
        },
    )
    assert accept.returncode == 0, accept.stderr
    assert (accept_root / tree.snapshot_name).is_dir()


def test_archive_snapshot_refuses_an_unverifiable_raw_pair(tree, tmp_path):
    """Would fail if the archive trusted the highest inventory generation and
    copied a month whose earlier generation no longer verifies."""
    archive_root = tmp_path / "archive_root"
    archive_root.mkdir()
    _damage_lower_generation(tree)

    result = _run_archive(tree, archive_root)

    assert result.returncode != 0
    assert "gen-0001" in result.stderr
    assert list(archive_root.iterdir()) == []


def test_archive_snapshot_detects_live_mutation_during_copy(tree, tmp_path):
    """Would fail if the archive trusted the bytes it already copied instead
    of revalidating the live source after the copy."""
    archive_root = tmp_path / "archive_root"
    archive_root.mkdir()
    target = tree.data_dir / JANUARY_PDF
    original = target.read_bytes()
    mutate_shim_dir = tmp_path / "mutate_shim"
    mutate_shim_dir.mkdir()
    real_sha256sum = shutil.which("sha256sum")
    # `sha256sum --check --strict SHA256SUMS` runs exactly once for a fresh
    # archive, right after the stage's checksum file is written and before the
    # live source is revalidated: a mutation there lands inside the finished copy.
    shim = mutate_shim_dir / "sha256sum"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$#\" -eq 3 ] && [ \"$1\" = \"--check\" ] && [ \"$2\" = \"--strict\" ] "
        "&& [ \"$3\" = \"SHA256SUMS\" ]; then\n"
        f"  printf '\\n' >> {shlex.quote(str(target))}\n"
        "fi\n"
        f"exec {shlex.quote(real_sha256sum)} \"$@\"\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)

    result = _run_archive(tree, archive_root, path_prefix=str(mutate_shim_dir))

    assert result.returncode != 0
    assert target.read_bytes() == original + b"\n"
    assert list(archive_root.iterdir()) == []


def test_restore_snapshot_places_the_five_trees_only_into_absent_targets(archived, tmp_path):
    restore_root = _restore_root_with_player_games(archived, tmp_path)
    player_games_before = _tree_bytes(restore_root / "parquet-large/player_games")

    result = _run_restore(archived, restore_root)

    assert result.returncode == 0, result.stderr
    for relative in SNAPSHOT_TREES:
        assert _tree_bytes(restore_root / relative) == _tree_bytes(archived.snapshot / relative), relative
    assert _tree_bytes(restore_root / "parquet-large/player_games") == player_games_before
    assert _entries(restore_root) == ["parquet-large", "raw"]
    assert _entries(restore_root / "parquet-large") == ["availability", "player_games"]
    assert _entries(restore_root / "raw") == [
        "nba_gamebooks", "nba_injury_reports", "sportsdataverse", "stats_nba",
    ]
    for parent in {(restore_root / relative).parent for relative in SNAPSHOT_TREES}:
        assert list(parent.glob(".availability-restore*")) == []
    assert archived.tree.manifest_sha256 in result.stdout

    again = _run_restore(archived, restore_root)
    assert again.returncode != 0
    assert "existing destination" in again.stderr


@pytest.mark.parametrize("which", SNAPSHOT_TREES)
def test_restore_snapshot_refuses_when_a_target_exists(archived, tmp_path, which):
    restore_root = _restore_root_with_player_games(archived, tmp_path)
    target = restore_root / which
    target.mkdir(parents=True)
    (target / "sentinel.txt").write_text("pre-existing\n", encoding="utf-8")

    result = _run_restore(archived, restore_root)

    assert result.returncode != 0
    assert (target / "sentinel.txt").read_text(encoding="utf-8") == "pre-existing\n"
    assert _entries(target) == ["sentinel.txt"]
    for relative in SNAPSHOT_TREES:
        if relative != which:
            assert not (restore_root / relative).exists(), relative


@pytest.mark.parametrize("which", ("raw/stats_nba/box_rosters", "parquet-large/availability"))
def test_restore_snapshot_refuses_symlink_targets(archived, tmp_path, which):
    restore_root = _restore_root_with_player_games(archived, tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    target = restore_root / which
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(elsewhere)

    result = _run_restore(archived, restore_root)

    assert result.returncode != 0
    assert target.is_symlink() and os.readlink(target) == str(elsewhere)
    assert list(elsewhere.iterdir()) == []
    for relative in SNAPSHOT_TREES:
        if relative != which:
            assert not (restore_root / relative).exists(), relative


def test_restore_snapshot_refuses_a_missing_or_mismatched_player_games_manifest(archived, tmp_path):
    """Would fail if the restore proceeded without the exact D1 publication the
    snapshot's manifest binds (recovery step 2 must precede step 3)."""
    missing_root = tmp_path / "restore_missing"
    missing_root.mkdir()
    missing = _run_restore(archived, missing_root)
    assert missing.returncode != 0
    assert "player_games" in missing.stderr
    _assert_nothing_restored(missing_root)

    mismatched_root = _restore_root_with_player_games(archived, tmp_path)
    manifest = mismatched_root / "parquet-large/player_games/_manifest.json"
    manifest.write_bytes(manifest.read_bytes() + b"\n")
    mismatched = _run_restore(archived, mismatched_root)
    assert mismatched.returncode != 0
    assert "player_games" in mismatched.stderr
    _assert_nothing_restored(mismatched_root)

    bound = json.loads(
        (archived.snapshot / "parquet-large/availability/_manifest.json").read_text(encoding="utf-8")
    )["references"]["player_games_manifest"]
    assert bound["sha256"] == _sha256(archived.tree.player_games_root / "_manifest.json")
    assert bound["snapshot_name"] == f"player-games-archive-2026-{bound['sha256'][:12]}"


def test_restore_snapshot_rejects_tampered_sha256sums(archived, tmp_path):
    restore_root = _restore_root_with_player_games(archived, tmp_path)
    sums_path = archived.snapshot / "SHA256SUMS"
    lines = sums_path.read_text(encoding="utf-8").splitlines()
    digest, rest = lines[-1].split("  ", 1)
    lines[-1] = f"{('0' if digest[0] != '0' else '1') + digest[1:]}  {rest}"
    sums_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = _run_restore(archived, restore_root)

    assert result.returncode != 0
    _assert_nothing_restored(restore_root)

    misnamed = _run_restore(archived, restore_root, name="availability-archive-2026-000000000000")
    assert misnamed.returncode != 0
    _assert_nothing_restored(restore_root)


def test_restore_snapshot_detects_snapshot_mutation_during_copy(archived, tmp_path):
    """Would fail if the restore trusted its pre-copy validation of the
    snapshot and never rechecked it after the copy."""
    restore_root = _restore_root_with_player_games(archived, tmp_path)
    target = archived.snapshot / JANUARY_PDF
    original = target.read_bytes()
    rsync_shim_dir = tmp_path / "rsync_shim"
    rsync_shim_dir.mkdir()
    marker = tmp_path / "mutated.marker"
    real_rsync = shutil.which("rsync")
    shim = rsync_shim_dir / "rsync"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        f"if [ ! -e {shlex.quote(str(marker))} ]; then\n"
        f"  : > {shlex.quote(str(marker))}\n"
        f"  printf '\\n' >> {shlex.quote(str(target))}\n"
        "fi\n"
        f"exec {shlex.quote(real_rsync)} \"$@\"\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)

    result = _run_restore(archived, restore_root, path_prefix=str(rsync_shim_dir))

    assert result.returncode != 0
    assert marker.exists()
    assert target.read_bytes() == original + b"\n"
    _assert_nothing_restored(restore_root)
