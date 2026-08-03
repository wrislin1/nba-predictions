from pathlib import Path
import shutil
import subprocess
import sys

import pytest


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

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/verify_repository.py"


@pytest.fixture
def valid_repository(tmp_path: Path) -> Path:
    if not SCRIPT.is_file():
        pytest.skip("verifier script has not been implemented yet")

    root = tmp_path / "repository"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(SCRIPT, root / "scripts/verify_repository.py")

    for relative_path in REQUIRED_ARCHIVE_FILES | REQUIRED_DASHBOARD_IMAGES:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("preserved\n", encoding="utf-8")

    (root / "requirements.txt").write_text("pytest==9.1.1\n", encoding="utf-8")
    (root / ".gitignore").write_text(
        "\n".join(sorted(REQUIRED_GITIGNORE_RULES)) + "\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", root], check=True)
    subprocess.run(["git", "-C", root, "add", "."], check=True)
    return root


def run_verifier(root: Path, max_git_mib: int = 100) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            root / "scripts/verify_repository.py",
            "--max-git-mib",
            str(max_git_mib),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_current_repository_passes_with_summary() -> None:
    result = subprocess.run(
        [sys.executable, SCRIPT, "--max-git-mib", "600"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Tracked files:" in result.stdout
    assert ".git size:" in result.stdout


def test_valid_repository_passes_with_summary(valid_repository: Path) -> None:
    result = run_verifier(valid_repository)

    assert result.returncode == 0, result.stderr
    assert "Tracked files:" in result.stdout
    assert ".git size:" in result.stdout


def test_tracked_venv_path_is_rejected(valid_repository: Path) -> None:
    generated_file = valid_repository / "venv/generated.txt"
    generated_file.parent.mkdir()
    generated_file.write_text("generated\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", valid_repository, "add", "-f", "venv/generated.txt"],
        check=True,
    )

    result = run_verifier(valid_repository)

    assert result.returncode == 1
    assert "tracked generated environment path: venv/generated.txt" in result.stderr


def test_tracked_generated_data_path_is_rejected(valid_repository: Path) -> None:
    generated_file = (
        valid_repository / "data/parquet-large/player_games/_manifest.json"
    )
    generated_file.parent.mkdir(parents=True)
    generated_file.write_text("{}\n", encoding="utf-8")
    subprocess.run(
        [
            "git",
            "-C",
            valid_repository,
            "add",
            "-f",
            "data/parquet-large/player_games/_manifest.json",
        ],
        check=True,
    )

    result = run_verifier(valid_repository)

    assert result.returncode == 1
    assert (
        "tracked generated data path: "
        "data/parquet-large/player_games/_manifest.json" in result.stderr
    )


def test_missing_archive_artifact_is_reported(valid_repository: Path) -> None:
    missing = valid_repository / "archive/dashboard/start.py"
    missing.unlink()

    result = run_verifier(valid_repository)

    assert result.returncode == 1
    assert "missing required preserved artifact: archive/dashboard/start.py" in result.stderr


def test_missing_dashboard_image_is_reported(valid_repository: Path) -> None:
    relative_path = "archive/dashboard/static/img/ATTENDANCE_era_averages.png"
    (valid_repository / relative_path).unlink()

    result = run_verifier(valid_repository)

    assert result.returncode == 1
    assert f"missing required preserved artifact: {relative_path}" in result.stderr


def test_nul_byte_in_requirements_is_rejected(valid_repository: Path) -> None:
    (valid_repository / "requirements.txt").write_bytes(b"pytest==9.1.1\x00\n")

    result = run_verifier(valid_repository)

    assert result.returncode == 1
    assert "requirements.txt contains NUL bytes" in result.stderr


def test_missing_gitignore_rule_is_reported(valid_repository: Path) -> None:
    gitignore = valid_repository / ".gitignore"
    gitignore.write_text(
        gitignore.read_text(encoding="utf-8").replace("nba-stats/\n", ""),
        encoding="utf-8",
    )

    result = run_verifier(valid_repository)

    assert result.returncode == 1
    assert "missing required .gitignore rule: nba-stats/" in result.stderr


def test_git_size_limit_is_enforced(valid_repository: Path) -> None:
    result = run_verifier(valid_repository, max_git_mib=0)

    assert result.returncode == 1
    assert ".git exceeds 0 MiB" in result.stderr


def test_linked_worktree_enforces_common_git_directory_size(
    valid_repository: Path,
    tmp_path: Path,
) -> None:
    subprocess.run(
        [
            "git",
            "-C",
            valid_repository,
            "-c",
            "user.name=Repository Test",
            "-c",
            "user.email=repository-test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    linked_worktree = tmp_path / "linked-worktree"
    subprocess.run(
        [
            "git",
            "-C",
            valid_repository,
            "worktree",
            "add",
            "--detach",
            linked_worktree,
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    result = run_verifier(linked_worktree, max_git_mib=0)

    assert result.returncode == 1
    assert ".git exceeds 0 MiB" in result.stderr
