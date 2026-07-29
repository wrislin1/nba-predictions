from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]

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

REQUIRED_REQUIREMENTS = {
    "beautifulsoup4",
    "duckdb",
    "jupyter",
    "linearmodels",
    "lxml",
    "matplotlib",
    "nba-api",
    "openpyxl",
    "pandas",
    "pyarrow",
    "pytest",
    "requests",
    "seaborn",
    "statsmodels",
}


def normalized_requirement_name(line: str) -> str:
    return re.split(r"[<=>!~\[]", line, maxsplit=1)[0].lower().replace("_", "-")


def test_generated_environment_is_absent() -> None:
    assert not (ROOT / "venv").exists()


def test_authored_artifacts_are_preserved() -> None:
    required = REQUIRED_ARCHIVE_FILES | REQUIRED_DASHBOARD_IMAGES
    missing = [path for path in required if not (ROOT / path).is_file()]
    assert missing == []


def test_analysis_first_layout_exists() -> None:
    assert (ROOT / "data/parquet/.gitkeep").is_file()
    assert (ROOT / "src/nba_data/__init__.py").is_file()
    assert (ROOT / "analysis/covid_hca/README.md").is_file()
    assert (ROOT / "scripts/verify_repository.py").is_file()


def test_gitignore_protects_generated_and_raw_state() -> None:
    rules = set((ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())
    assert {".venv/", "venv/", "data/raw/", "nba-stats/", "*.duckdb", ".serena/"} <= rules


def test_requirements_are_utf8_and_pinned() -> None:
    raw = (ROOT / "requirements.txt").read_bytes()
    text = raw.decode("utf-8")
    assert b"\x00" not in raw
    stripped_lines = (line.strip() for line in text.splitlines())
    lines = [line for line in stripped_lines if line and not line.startswith("#")]
    assert all("==" in line or line.startswith(("-", "--")) for line in lines)
    names = {normalized_requirement_name(line) for line in lines if not line.startswith(("-", "--"))}
    assert REQUIRED_REQUIREMENTS <= names


def test_packaged_reference_assets_match_publication_reference() -> None:
    """Would fail if installed and repository builds resolved different IDs."""
    for name in ("franchises.csv", "hoopr_snapshot.json"):
        assert (
            ROOT / "src/nba_data/reference" / name
        ).read_bytes() == (
            ROOT / "data/reference" / name
        ).read_bytes()


def test_wheel_declares_runtime_dependencies_and_imports_off_repo(
    tmp_path,
) -> None:
    """Would fail if a normal wheel relied on repo-relative reference data."""
    source = tmp_path / "source"
    source.mkdir()
    shutil.copy2(ROOT / "pyproject.toml", source)
    shutil.copytree(ROOT / "src", source / "src")
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    built = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
            str(source),
        ],
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stderr
    wheels = list(wheel_dir.glob("nba_data-*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as archive:
        metadata_name = next(
            name
            for name in archive.namelist()
            if name.endswith(".dist-info/METADATA")
        )
        metadata = archive.read(metadata_name).decode("utf-8")
    declared = {
        normalized_requirement_name(
            line.split(":", maxsplit=1)[1].strip()
        )
        for line in metadata.splitlines()
        if line.startswith("Requires-Dist:")
    }
    assert {
        "lxml",
        "nba-api",
        "pandas",
        "pyarrow",
        "requests",
    } <= declared

    target = tmp_path / "installed"
    installed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--target",
            str(target),
            str(wheels[0]),
        ],
        capture_output=True,
        text=True,
    )
    assert installed.returncode == 0, installed.stderr
    program = (
        "import sys; "
        f"sys.path.insert(0, {str(target)!r}); "
        "from nba_data import franchises, hoopr; "
        "assert franchises.resolve('Brooklyn Nets', 2024) == 'BKN'; "
        "assert hoopr.SNAPSHOT['commit'] == "
        "'50fe7e429c6b5bcbe62e7a56d19afe5a80e6e143'"
    )
    imported = subprocess.run(
        [sys.executable, "-c", program],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert imported.returncode == 0, imported.stderr


def test_readme_documents_the_new_contract() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for heading in (
        "## Project status",
        "## Repository layout",
        "## Environment setup",
        "## Data policy",
        "## Revival roadmap",
    ):
        assert heading in readme


def test_game_data_delivery_layout_is_recoverable_and_portable() -> None:
    required = {
        "docs/data-dictionary.md",
        "scripts/archive_raw_snapshot.sh",
        "data/parquet/build_manifest.json",
        "data/parquet/franchises.parquet",
        "data/parquet/games.parquet",
        "data/parquet/team_games.parquet",
        "data/parquet/venues.parquet",
    }
    missing = [path for path in required if not (ROOT / path).is_file()]
    assert missing == []

    tracked = subprocess.run(
        ["git", "ls-files", "data/raw"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert tracked == []

    manifest = json.loads(
        (ROOT / "data/parquet/build_manifest.json").read_text(encoding="utf-8")
    )
    rendered = json.dumps(manifest, sort_keys=True)
    assert "/home/" not in rendered
    assert "/main/" not in rendered


def seed_raw_snapshot(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/restore_raw.sh", scripts)
    manifest = repo / "data/parquet/build_manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps({"tables": {"games": {"max_season": 2026}}}) + "\n",
        encoding="utf-8",
    )
    manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
    archive_root = tmp_path / "archive"
    snapshot = (
        archive_root
        / f"slice2a-raw-2026-{manifest_sha[:12]}"
    )
    raw_files = {
        "stats_nba/leaguegamefinder_2026.json": b'{"resultSets":[]}',
        "stats_nba/leaguegamefinder_2026.json.meta.json": b'{"sha256":"fixture"}',
        "reconciliation/bref/198501010BOS.html": b"<html>box</html>",
        "reconciliation/bref/198501010BOS.html.meta.json": b'{"sha256":"fixture"}',
    }
    for relative, payload in raw_files.items():
        path = snapshot / "raw" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    shutil.copy2(manifest, snapshot / "build_manifest.json")
    checksums = []
    for path in sorted((snapshot / "raw").rglob("*")):
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            checksums.append(f"{digest}  {path.relative_to(snapshot)}")
    (snapshot / "SHA256SUMS").write_text(
        "\n".join(checksums) + "\n",
        encoding="utf-8",
    )
    return repo, archive_root, snapshot


def run_raw_restore(
    repo: Path,
    archive_root: Path,
) -> subprocess.CompletedProcess:
    environment = {
        **os.environ,
        "NBA_RAW_ARCHIVE_ROOT": str(archive_root),
        "NBA_RAW_DESTINATION": str(repo / "data/raw"),
        "NBA_RAW_MANIFEST": str(
            repo / "data/parquet/build_manifest.json"
        ),
    }
    return subprocess.run(
        ["bash", "scripts/restore_raw.sh"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=environment,
    )


def test_restore_raw_restores_verified_full_snapshot(tmp_path) -> None:
    """Would fail if recovery restored only the two legacy inputs."""
    repo, archive_root, snapshot = seed_raw_snapshot(tmp_path)

    completed = run_raw_restore(repo, archive_root)

    assert completed.returncode == 0, completed.stderr
    restored = sorted(
        path.relative_to(repo / "data/raw").as_posix()
        for path in (repo / "data/raw").rglob("*")
        if path.is_file()
    )
    archived = sorted(
        path.relative_to(snapshot / "raw").as_posix()
        for path in (snapshot / "raw").rglob("*")
        if path.is_file()
    )
    assert restored == archived


def test_restore_raw_rejects_tampered_snapshot(tmp_path) -> None:
    """Would fail if restore copied a snapshot without verifying hashes."""
    repo, archive_root, snapshot = seed_raw_snapshot(tmp_path)
    (snapshot / "raw/reconciliation/bref/198501010BOS.html").write_bytes(
        b"tampered"
    )

    completed = run_raw_restore(repo, archive_root)

    assert completed.returncode != 0
    assert not (repo / "data/raw").exists()
