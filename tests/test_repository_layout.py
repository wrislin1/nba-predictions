from pathlib import Path
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]


def wait_for_modern_shared_flock_waiter(
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
            raise AssertionError("modern raw reader ran before publisher exit")
        if process.poll() is not None:
            raise AssertionError("modern raw reader exited before taking lock")
        time.sleep(0.01)
    raise AssertionError("modern raw shared-lock waiter did not appear")


def wait_for_modern_file(path: Path, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {path}")

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
    assert {
        ".venv/",
        "venv/",
        "data/raw/",
        "data/.parquet-publication.lock",
        "data/parquet-large/",
        "nba-stats/",
        "*.duckdb",
        ".serena/",
    } <= rules


def test_pbp_generated_roots_are_gitignored() -> None:
    """Would fail if a PBP raw cache or large-Parquet path could be added."""
    paths = [
        "data/raw/stats_nba/pbp/example.json",
        "data/parquet-large/pbp/season=2026/events.parquet",
    ]

    checked = subprocess.run(
        ["git", "check-ignore", *paths],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert checked.returncode == 0, checked.stderr
    assert set(checked.stdout.splitlines()) == set(paths)

    tracked = subprocess.run(
        ["git", "ls-files", *paths],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert tracked == []


def test_requirements_are_utf8_and_pinned() -> None:
    raw = (ROOT / "requirements.txt").read_bytes()
    text = raw.decode("utf-8")
    assert b"\x00" not in raw
    stripped_lines = (line.strip() for line in text.splitlines())
    lines = [line for line in stripped_lines if line and not line.startswith("#")]
    assert all("==" in line or line.startswith(("-", "--")) for line in lines)
    names = {normalized_requirement_name(line) for line in lines if not line.startswith(("-", "--"))}
    assert REQUIRED_REQUIREMENTS <= names


def test_build_manifests_dependencies_match_direct_requirements() -> None:
    direct_names = set()
    for line in (ROOT / "requirements.in").read_text(
        encoding="utf-8"
    ).splitlines():
        requirement = line.split("#", 1)[0].strip()
        if not requirement:
            continue
        match = re.match(r"([A-Za-z0-9_.-]+)", requirement)
        if match is not None:
            direct_names.add(match.group(1))
    for manifest_name in (
        "build_manifest.json",
        "historical_games_manifest.json",
    ):
        manifest = json.loads(
            (ROOT / "data/parquet" / manifest_name).read_text(
                encoding="utf-8"
            )
        )

        assert set(manifest["versions"]["dependencies"]) == direct_names
        assert "numpy" in manifest["versions"]["dependencies"]


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
    normalized = " ".join(readme.lower().split())
    assert re.search(
        r"results-level baa/nba history.*1947–2026.*delivered"
        r".*two validated regular-season publications"
        r".*separate postseason publication",
        normalized,
    )
    assert re.search(
        r"rich historical team/player box scores.*future"
        r".*separately reviewed",
        normalized,
    )


def test_game_data_delivery_layout_is_recoverable_and_portable() -> None:
    required = {
        "analysis/covid_hca/data/home_court_by_season.csv",
        "analysis/covid_hca/figures/home_court_trend_1947_2026.png",
        "analysis/covid_hca/historical_trend.md",
        "data/parquet/historical_games.parquet",
        "data/parquet/historical_games_manifest.json",
        "data/reference/historical_season_counts.csv",
        "data/reference/historical_site_corrections.csv",
        "docs/data-dictionary.md",
        "docs/audits/2026-07-29-historical-bref-source-spike.md",
        "scripts/archive_raw_snapshot.sh",
        "scripts/archive_historical_raw_snapshot.sh",
        "scripts/archive_playoff_raw_snapshot.sh",
        "scripts/playoff_raw_snapshot_common.sh",
        "scripts/restore_historical_raw.sh",
        "scripts/restore_playoff_raw.sh",
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

    historical_manifest = json.loads(
        (ROOT / "data/parquet/historical_games_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    rendered_historical = json.dumps(historical_manifest, sort_keys=True)
    assert "/home/" not in rendered_historical
    assert "fetched_at" not in rendered_historical
    assert (
        len(historical_manifest["validation"]["games_by_season"])
        == 37
    )
    def collect_source_paths(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "path":
                    yield child
                yield from collect_source_paths(child)
        elif isinstance(value, list):
            for child in value:
                yield from collect_source_paths(child)

    source_paths = list(
        collect_source_paths(historical_manifest["sources"])
    )
    assert source_paths
    assert all(
        not Path(path).is_absolute() and ".." not in Path(path).parts
        for path in source_paths
    )

    tracked_historical = subprocess.run(
        ["git", "ls-files", "data/raw/historical_bref"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert tracked_historical == []

    playoff_fixtures = {
        "tests/fixtures/playoff_bref/NBA_1984_games.html",
        "tests/fixtures/playoff_bref/NBA_2020_games.html",
        "tests/fixtures/playoff_bref/NBA_2020_games-august.html",
    }
    tracked_playoff_fixtures = subprocess.run(
        ["git", "ls-files", *sorted(playoff_fixtures)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert set(tracked_playoff_fixtures) == playoff_fixtures

    tracked_playoff = subprocess.run(
        ["git", "ls-files", "data/raw/playoff_bref"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert tracked_playoff == []

    recovery_scripts = {
        "scripts/archive_playoff_raw_snapshot.sh",
        "scripts/playoff_raw_snapshot_common.sh",
        "scripts/restore_playoff_raw.sh",
    }
    tracked_recovery = subprocess.run(
        ["git", "ls-files", "-s", *sorted(recovery_scripts)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    tracked_modes = {
        fields[3]: fields[0]
        for line in tracked_recovery
        if len(fields := line.split(maxsplit=3)) == 4
    }
    assert set(tracked_modes) == recovery_scripts
    assert tracked_modes["scripts/archive_playoff_raw_snapshot.sh"] == "100755"
    assert tracked_modes["scripts/restore_playoff_raw.sh"] == "100755"
    assert tracked_modes["scripts/playoff_raw_snapshot_common.sh"] == "100644"

    default_archive_root = (
        "/mnt/nas/llm/documents/nba-predictions-archive"
    )
    expected_assignment = (
        'archive_root="${NBA_PLAYOFF_RAW_ARCHIVE_ROOT:-'
        f'{default_archive_root}}}"'
    )
    for relative in (
        "scripts/archive_playoff_raw_snapshot.sh",
        "scripts/restore_playoff_raw.sh",
    ):
        lines = (ROOT / relative).read_text(
            encoding="utf-8"
        ).splitlines()
        assert lines.count(expected_assignment) == 1


def test_historical_documentation_matches_the_delivered_contract() -> None:
    dictionary_path = ROOT / "docs/data-dictionary.md"
    dictionary = dictionary_path.read_text(encoding="utf-8")
    franchise_bytes = (ROOT / "data/reference/franchises.csv").read_bytes()
    franchise_sha = hashlib.sha256(franchise_bytes).hexdigest()
    modern_manifest = json.loads(
        (ROOT / "data/parquet/build_manifest.json").read_text(encoding="utf-8")
    )
    assert (
        modern_manifest["references"]["reference/franchises.csv"]
        == franchise_sha
    )
    assert (
        "aae69415cee458165e589355d8e6d34c248c9f1849586fb1c75ee3063c3028cb"
        not in dictionary
    )
    retained_franchise_hashes = re.findall(
        r"`reference/franchises\.csv`\s*`([0-9a-f]{64})`",
        dictionary,
    )
    assert set(retained_franchise_hashes) <= {franchise_sha}

    heading = "### `historical_games.parquet`"
    assert heading in dictionary
    section = dictionary.split(heading, maxsplit=1)[1]
    section = re.split(r"\n#{2,3} ", section, maxsplit=1)[0]
    documented_schema = {}
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip().strip("`") for cell in line.split("|")[1:-1]]
        if (
            len(cells) >= 2
            and cells[0] not in {"Column", "---"}
            and set(cells[0]) != {"-"}
        ):
            documented_schema[cells[0]] = cells[1]
    assert documented_schema == {
        "game_id": "large_string",
        "season": "int64",
        "league": "large_string",
        "game_date": "date32[day]",
        "away_team_name": "large_string",
        "home_team_name": "large_string",
        "away_franchise_id": "large_string",
        "home_franchise_id": "large_string",
        "away_pts": "int64",
        "home_pts": "int64",
        "home_margin": "int64",
        "overtime_periods": "int64",
        "site_status": "large_string",
        "venue_name": "large_string nullable",
        "attendance": "int64 nullable",
        "attendance_status": "large_string",
        "boxscore_url": "large_string nullable",
        "schedule_url": "large_string",
    }

    for command in (
        "python -m nba_data fetch-historical",
        "python -m nba_data build-historical",
        "bash scripts/archive_historical_raw_snapshot.sh",
        "bash scripts/restore_historical_raw.sh",
        "bash scripts/restore_raw.sh",
    ):
        assert command in dictionary

    modern_recovery = dictionary.split(
        "The modern recovery flow remains separate:", maxsplit=1
    )[1]
    assert "manifest binding" in modern_recovery
    assert "complete regular-file checksum inventory" in modern_recovery
    assert "complete manifest/checksum inventory" not in modern_recovery

    expected_links = {
        "../analysis/covid_hca/historical_trend.md",
        "../data/parquet/build_manifest.json",
        "../data/parquet/historical_games_manifest.json",
        "../data/reference/historical_season_counts.csv",
        "../data/reference/historical_site_corrections.csv",
        "audits/2026-07-29-historical-bref-source-spike.md",
        "superpowers/specs/2026-07-29-historical-game-results-design.md",
        "https://www.sports-reference.com/bot-traffic.html",
        "https://www.sports-reference.com/data_use.html",
    }
    documented_links = set(re.findall(r"\[[^]]+\]\(([^)]+)\)", dictionary))
    assert expected_links <= documented_links
    for link in expected_links:
        if link.startswith("https://"):
            continue
        assert (dictionary_path.parent / link).resolve().is_file()

    analysis_readme = (
        ROOT / "analysis/covid_hca/README.md"
    ).read_text(encoding="utf-8")
    assert (
        "MPLBACKEND=Agg .venv/bin/python "
        "analysis/covid_hca/build_historical_trend.py"
    ) in analysis_readme
    assert "fixed-effects" in analysis_readme
    assert "site_status" in analysis_readme
    assert "attendance_status" in analysis_readme


def test_playoff_documentation_matches_the_delivered_contract() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    dictionary = (ROOT / "docs/data-dictionary.md").read_text(
        encoding="utf-8"
    )
    required_terms = (
        "playoff_games.parquet",
        "playoff_games_manifest.json",
        "fetch-playoffs",
        "build-playoffs",
        "archive_playoff_raw_snapshot.sh",
        "restore_playoff_raw.sh",
        "playoff-raw-2026-",
        "game_type",
    )
    for document in (readme, dictionary):
        for term in required_terms:
            assert term in document
        for term in (
            "data/.parquet-publication.lock",
            "shared lock",
            "direct-path readers",
        ):
            assert term in document

    manifest_ledger_intro = (
        "The committed [historical manifest]"
        "(../data/parquet/historical_games_manifest.json),\n"
        "[modern build manifest](../data/parquet/build_manifest.json), and\n"
        "[playoff manifest]"
        "(../data/parquet/playoff_games_manifest.json) record"
    )
    assert manifest_ledger_intro in dictionary

    operator_flow = """```bash
# One season or resumable full acquisition; networked and rate-limited.
python -m nba_data fetch-playoffs --season 1984
python -m nba_data fetch-playoffs

# Strictly offline deterministic publication.
python -m nba_data build-playoffs

# Independent immutable raw recovery.
bash scripts/archive_playoff_raw_snapshot.sh
bash scripts/restore_playoff_raw.sh
```"""
    assert operator_flow in readme

    heading = "### `playoff_games.parquet`"
    assert heading in dictionary
    section = dictionary.split(heading, maxsplit=1)[1]
    section = re.split(r"\n#{2,3} ", section, maxsplit=1)[0]
    game_type_heading = "#### `game_type` values"
    assert game_type_heading in section
    schema_section = section.split(game_type_heading, maxsplit=1)[0]
    documented_schema = []
    for line in schema_section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip().strip("`") for cell in line.split("|")[1:-1]]
        if (
            len(cells) >= 2
            and cells[0] not in {"Column", "---"}
            and set(cells[0]) != {"-"}
        ):
            documented_schema.append((cells[0], cells[1]))
    assert documented_schema == [
        ("game_id", "large_string"),
        ("season", "int64"),
        ("league", "large_string"),
        ("game_date", "date32[day]"),
        ("game_type", "large_string"),
        ("away_team_name", "large_string"),
        ("home_team_name", "large_string"),
        ("away_franchise_id", "large_string"),
        ("home_franchise_id", "large_string"),
        ("away_pts", "int64"),
        ("home_pts", "int64"),
        ("home_margin", "int64"),
        ("overtime_periods", "int64"),
        ("site_status", "large_string"),
        ("venue_name", "large_string nullable"),
        ("attendance", "int64 nullable"),
        ("attendance_status", "large_string"),
        ("boxscore_url", "large_string nullable"),
        ("schedule_url", "large_string"),
    ]

    game_type_section = section.split(game_type_heading, maxsplit=1)[1]
    game_type_section = re.split(r"\n#### ", game_type_section, maxsplit=1)[0]
    documented_game_types = []
    for line in game_type_section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.split("|")[1:-1]]
        value = cells[0].strip("`")
        if value not in {"Value", "---"} and set(value) != {"-"}:
            documented_game_types.append((value, cells[1]))
    assert documented_game_types == [
        ("playoff", "A completed BAA/NBA playoff result"),
        (
            "play_in",
            "An exact `Play-In Game` row from one of the seven approved "
            "seeded monthly pages for 2020–2026",
        ),
        (
            "tiebreaker",
            "One of the eight historical rows whose exact source remark "
            "is `Tiebreaker`",
        ),
        (
            "voided",
            "The single 1953 result marked "
            "`Protest; does not count toward record`",
        ),
    ]


def seed_modern_raw_lock_fixture(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    for name in (
        "archive_raw_snapshot.sh",
        "publication_read_lock.sh",
        "restore_raw.sh",
    ):
        shutil.copy2(ROOT / "scripts" / name, scripts / name)
    manifest = repo / "data/parquet/build_manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps({"tables": {"games": {"max_season": 2026}}}) + "\n",
        encoding="utf-8",
    )
    raw_file = repo / "data/raw/stats_nba/leaguegamefinder_2026.json"
    raw_file.parent.mkdir(parents=True)
    raw_file.write_text('{"resultSets":[]}\n', encoding="utf-8")
    return repo, tmp_path / "archive"


@pytest.mark.parametrize("operation", ["archive", "restore"])
def test_modern_raw_shell_reader_holds_shared_publication_lock(
    tmp_path: Path,
    operation: str,
) -> None:
    """Would fail if either modern raw script read outside its shared lock."""
    repo, archive_root = seed_modern_raw_lock_fixture(tmp_path)
    environment = {
        **os.environ,
        "NBA_RAW_ARCHIVE_ROOT": str(archive_root),
        "NBA_RAW_DESTINATION": str(repo / "data/raw"),
        "NBA_RAW_MANIFEST": str(repo / "data/parquet/build_manifest.json"),
    }
    if operation == "restore":
        archived = subprocess.run(
            ["bash", "scripts/archive_raw_snapshot.sh"],
            cwd=repo,
            env=environment,
            capture_output=True,
            text=True,
        )
        assert archived.returncode == 0, archived.stderr
        shutil.rmtree(repo / "data/raw")

    fake_bin = tmp_path / "lock-test-bin"
    fake_bin.mkdir()
    pause_marker = tmp_path / "modern-rsync-paused"
    pause_gate = tmp_path / "modern-rsync-continue"
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
    environment.update({
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "PAUSE_GATE": str(pause_gate),
        "PAUSE_MARKER": str(pause_marker),
        "REAL_RSYNC": shutil.which("rsync") or "rsync",
    })
    script = (
        "scripts/archive_raw_snapshot.sh"
        if operation == "archive"
        else "scripts/restore_raw.sh"
    )
    lock_path = repo / "data/.parquet-publication.lock"
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
    process = subprocess.Popen(
        ["bash", script],
        cwd=repo,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout = ""
    stderr = ""
    try:
        wait_for_modern_shared_flock_waiter(
            lock_path,
            process,
            pause_marker,
        )
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        wait_for_modern_file(pause_marker)
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


@pytest.mark.parametrize("operation", ["archive", "restore"])
def test_modern_raw_scripts_reject_symlinked_live_manifest(
    tmp_path: Path,
    operation: str,
) -> None:
    repo, archive_root = seed_modern_raw_lock_fixture(tmp_path)
    manifest = repo / "data/parquet/build_manifest.json"
    pin_tmp = tmp_path / "manifest-tmp"
    pin_tmp.mkdir()
    environment = {
        **os.environ,
        "NBA_RAW_ARCHIVE_ROOT": str(archive_root),
        "NBA_RAW_DESTINATION": str(repo / "data/raw"),
        "NBA_RAW_MANIFEST": str(manifest),
        "TMPDIR": str(pin_tmp),
    }
    if operation == "restore":
        archived = subprocess.run(
            ["bash", "scripts/archive_raw_snapshot.sh"],
            cwd=repo,
            env=environment,
            capture_output=True,
            text=True,
        )
        assert archived.returncode == 0, archived.stderr
        shutil.rmtree(repo / "data/raw")
    target = tmp_path / "build-manifest-target.json"
    manifest.replace(target)
    manifest.symlink_to(target)
    script = (
        "scripts/archive_raw_snapshot.sh"
        if operation == "archive"
        else "scripts/restore_raw.sh"
    )

    completed = subprocess.run(
        ["bash", script],
        cwd=repo,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "missing or unsafe build manifest" in completed.stderr
    assert list(pin_tmp.iterdir()) == []


@pytest.mark.parametrize("operation", ["archive", "restore"])
def test_modern_raw_scripts_reject_manifest_swapped_to_symlink_while_waiting(
    tmp_path: Path,
    operation: str,
) -> None:
    """Would fail if a pre-lock check authorized a later path reopen."""
    repo, archive_root = seed_modern_raw_lock_fixture(tmp_path)
    manifest = repo / "data/parquet/build_manifest.json"
    pin_tmp = tmp_path / "manifest-tmp"
    pin_tmp.mkdir()
    environment = {
        **os.environ,
        "NBA_RAW_ARCHIVE_ROOT": str(archive_root),
        "NBA_RAW_DESTINATION": str(repo / "data/raw"),
        "NBA_RAW_MANIFEST": str(manifest),
        "TMPDIR": str(pin_tmp),
    }
    if operation == "restore":
        archived = subprocess.run(
            ["bash", "scripts/archive_raw_snapshot.sh"],
            cwd=repo,
            env=environment,
            capture_output=True,
            text=True,
        )
        assert archived.returncode == 0, archived.stderr
        shutil.rmtree(repo / "data/raw")

    lock_path = repo / "data/.parquet-publication.lock"
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
    script = (
        "scripts/archive_raw_snapshot.sh"
        if operation == "archive"
        else "scripts/restore_raw.sh"
    )
    process = subprocess.Popen(
        ["bash", script],
        cwd=repo,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout = ""
    stderr = ""
    try:
        wait_for_modern_shared_flock_waiter(
            lock_path,
            process,
            tmp_path / "forbidden-modern-progress",
        )
        target = tmp_path / f"{operation}-manifest-target.json"
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
    assert "missing or unsafe build manifest" in stderr
    assert list(pin_tmp.iterdir()) == []


def seed_raw_snapshot(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/restore_raw.sh", scripts)
    shutil.copy2(ROOT / "scripts/publication_read_lock.sh", scripts)
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


def test_modern_raw_snapshot_excludes_independent_caches(tmp_path) -> None:
    """Would fail if a modern snapshot archived either independent cache."""
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    for name in (
        "archive_raw_snapshot.sh",
        "publication_read_lock.sh",
        "restore_raw.sh",
    ):
        shutil.copy2(ROOT / "scripts" / name, scripts / name)
    manifest = repo / "data/parquet/build_manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps({"tables": {"games": {"max_season": 2026}}}) + "\n",
        encoding="utf-8",
    )
    raw_files = {
        "stats_nba/leaguegamefinder_2026.json": b'{"resultSets":[]}',
        "bref/NBA_2026_games.html": b"<html>schedule</html>",
        "hoopr/nba_schedule_2026.parquet": b"parquet fixture",
        "historical_bref/198501010BOS.html": b"<html>historical</html>",
        "playoff_bref/202604180BOS.html": b"<html>playoff</html>",
    }
    for relative, payload in raw_files.items():
        path = repo / "data/raw" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    archive_root = tmp_path / "archive"
    completed = subprocess.run(
        ["bash", "scripts/archive_raw_snapshot.sh"],
        cwd=repo,
        capture_output=True,
        text=True,
        env={**os.environ, "NBA_RAW_ARCHIVE_ROOT": str(archive_root)},
    )

    assert completed.returncode == 0, completed.stderr
    [snapshot] = archive_root.iterdir()
    expected = {
        "stats_nba/leaguegamefinder_2026.json",
        "bref/NBA_2026_games.html",
        "hoopr/nba_schedule_2026.parquet",
    }
    archived = {
        path.relative_to(snapshot / "raw").as_posix()
        for path in (snapshot / "raw").rglob("*")
        if path.is_file()
    }
    assert archived == expected
    assert not (snapshot / "raw/historical_bref").exists()
    assert not (snapshot / "raw/playoff_bref").exists()

    shutil.rmtree(repo / "data/raw")
    restored = run_raw_restore(repo, archive_root)

    assert restored.returncode == 0, restored.stderr
    restored_files = {
        path.relative_to(repo / "data/raw").as_posix()
        for path in (repo / "data/raw").rglob("*")
        if path.is_file()
    }
    assert restored_files == expected
    assert not (repo / "data/raw/historical_bref").exists()
    assert not (repo / "data/raw/playoff_bref").exists()


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


def test_pbp_archive_scripts_are_executable_and_tracked() -> None:
    """Would fail if the PBP archive/restore scripts were missing, untracked,
    or not marked executable in Git."""
    recovery_scripts = {
        "scripts/archive_pbp_snapshot.sh",
        "scripts/restore_pbp_snapshot.sh",
    }
    tracked = subprocess.run(
        ["git", "ls-files", "-s", *sorted(recovery_scripts)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    tracked_modes = {
        fields[3]: fields[0]
        for line in tracked
        if len(fields := line.split(maxsplit=3)) == 4
    }
    assert set(tracked_modes) == recovery_scripts
    assert all(mode == "100755" for mode in tracked_modes.values())


def test_pbp_final_audit_is_present() -> None:
    """Would fail if the committed final play-by-play audit were missing."""
    audit_path = ROOT / "docs/audits/2026-07-31-play-by-play-archive.md"
    assert audit_path.is_file()
    audit = audit_path.read_text(encoding="utf-8")
    assert "restore_verified: passed" in audit
    assert "reproducible_build: passed" in audit


def test_pbp_generated_data_paths_remain_ignored_and_untracked() -> None:
    """Would fail if a PBP raw cache or large-Parquet path could be added."""
    paths = [
        "data/raw/stats_nba/pbp/example.json",
        "data/parquet-large/pbp/season=2026/events.parquet",
    ]
    checked = subprocess.run(
        ["git", "check-ignore", *paths],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert checked.returncode == 0, checked.stderr
    assert set(checked.stdout.splitlines()) == set(paths)

    tracked = subprocess.run(
        ["git", "ls-files", *paths],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert tracked == []


def test_pbp_documentation_matches_the_delivered_contract() -> None:
    """Would fail if the README/data dictionary drifted from the actual
    ``EVENT_COLUMNS``/``PBP_ARROW_SCHEMA`` contract or omitted a required
    operational fact."""
    from nba_data import pbp_build

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    dictionary_path = ROOT / "docs/data-dictionary.md"
    dictionary = dictionary_path.read_text(encoding="utf-8")

    # README links to the PBP data-dictionary section and the final audit.
    assert "docs/data-dictionary.md#play-by-play-archive" in readme
    assert "docs/audits/2026-07-31-play-by-play-archive.md" in readme
    normalized_readme = " ".join(readme.lower().split())
    assert re.search(
        r"clone.*(does not|contains no).*(raw pbp|raw play-by-play)",
        normalized_readme,
    )
    assert "data/parquet-large/" in readme
    assert "data/raw/stats_nba/pbp" in readme

    shared_terms = (
        "events.parquet",
        "python -m nba_data fetch-pbp",
        "python -m nba_data build-pbp",
        "scripts/archive_pbp_snapshot.sh",
        "scripts/restore_pbp_snapshot.sh",
        "pbp-archive-2026-",
        "(game_id, action_id)",
    )
    for document in (readme, dictionary):
        for term in shared_terms:
            assert term in document

    # Data dictionary lists all EVENT_COLUMNS, in exact order, with the
    # exact Arrow type/nullability/origin from PBP_ARROW_SCHEMA.
    heading = "### `events.parquet`"
    next_heading = "### Commands"
    assert heading in dictionary
    assert next_heading in dictionary
    section = dictionary.split(heading, maxsplit=1)[1]
    section = section.split(next_heading, maxsplit=1)[0]
    documented_columns: list[str] = []
    documented_rows: dict[str, list[str]] = {}
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip().strip("`") for cell in line.split("|")[1:-1]]
        if (
            len(cells) >= 4
            and cells[0] not in {"Column", "---"}
            and set(cells[0]) != {"-"}
        ):
            documented_columns.append(cells[0])
            documented_rows[cells[0]] = cells

    assert documented_columns == pbp_build.EVENT_COLUMNS

    for field in pbp_build.PBP_ARROW_SCHEMA:
        row = documented_rows[field.name]
        assert row[1] == str(field.type), field.name
        expected_nullable = "yes" if field.nullable else "no"
        assert row[2] == expected_nullable, field.name
        expected_origin = (
            "derived"
            if field.name in pbp_build._DERIVED_EVENT_COLUMNS
            else "source"
        )
        assert row[3] == expected_origin, field.name

    # Raw source score strings are documented as distinct from the derived,
    # non-null running scores.
    assert "score_home_raw" in dictionary
    assert "score_away_raw" in dictionary
    normalized_dictionary = " ".join(dictionary.lower().split())
    assert re.search(
        r"score_home_raw.{0,200}exact source score string",
        normalized_dictionary,
    ) or re.search(
        r"exact source score string.{0,200}score_home_raw",
        normalized_dictionary,
    )
    assert "running score" in normalized_dictionary
    assert "pbp_score_mismatches.json" in dictionary

    # All three game types and the exact 1997..2026 ending-season scope.
    for game_type in ("regular", "playoff", "play_in"):
        assert f"`{game_type}`" in section or game_type in dictionary
    assert "1997" in dictionary and "2026" in dictionary
    assert "23-key" in dictionary or "23 required" in dictionary


def test_restore_raw_rejects_tampered_snapshot(tmp_path) -> None:
    """Would fail if restore copied a snapshot without verifying hashes."""
    repo, archive_root, snapshot = seed_raw_snapshot(tmp_path)
    (snapshot / "raw/reconciliation/bref/198501010BOS.html").write_bytes(
        b"tampered"
    )

    completed = run_raw_restore(repo, archive_root)

    assert completed.returncode != 0
    assert not (repo / "data/raw").exists()


def test_player_games_archive_scripts_are_executable_and_tracked() -> None:
    """Would fail if the player-games archive/restore scripts were missing,
    untracked, or not marked executable in Git."""
    recovery_scripts = {
        "scripts/archive_player_games_snapshot.sh",
        "scripts/restore_player_games_snapshot.sh",
    }
    tracked = subprocess.run(
        ["git", "ls-files", "-s", *sorted(recovery_scripts)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    tracked_modes = {
        fields[3]: fields[0]
        for line in tracked
        if len(fields := line.split(maxsplit=3)) == 4
    }
    assert set(tracked_modes) == recovery_scripts
    assert all(mode == "100755" for mode in tracked_modes.values())


def test_player_games_final_audit_is_present() -> None:
    """Would fail if the committed final player-games audit were missing or
    did not carry the rendered two-scope/restore evidence."""
    audit_path = ROOT / "docs/audits/2026-08-02-player-games-source-audit.md"
    assert audit_path.is_file()
    audit = audit_path.read_text(encoding="utf-8")
    assert "Restore verified: yes" in audit
    assert "Trusted publication: ending seasons 2012–2026" in audit
    assert "Historical quarantine: ending seasons 1984–2011" in audit


def test_player_games_generated_data_paths_remain_ignored_and_untracked() -> None:
    """Would fail if a player-games raw cache or large-Parquet path could be
    added."""
    paths = [
        "data/raw/stats_nba/player_logs/season=2026/regular.json",
        "data/parquet-large/player_games/season=2026/player_games.parquet",
    ]
    checked = subprocess.run(
        ["git", "check-ignore", *paths],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert checked.returncode == 0, checked.stderr
    assert set(checked.stdout.splitlines()) == set(paths)

    tracked = subprocess.run(
        ["git", "ls-files", *paths],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert tracked == []
