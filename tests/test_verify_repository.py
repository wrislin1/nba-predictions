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

REQUIRED_GITIGNORE_RULES = {
    ".venv/",
    "venv/",
    "data/raw/",
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

    for relative_path in REQUIRED_ARCHIVE_FILES:
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


def test_missing_archive_artifact_is_reported(valid_repository: Path) -> None:
    missing = valid_repository / "archive/dashboard/start.py"
    missing.unlink()

    result = run_verifier(valid_repository)

    assert result.returncode == 1
    assert "missing required preserved artifact: archive/dashboard/start.py" in result.stderr


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
