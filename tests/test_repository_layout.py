from pathlib import Path
import re

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
    missing = [path for path in REQUIRED_ARCHIVE_FILES if not (ROOT / path).is_file()]
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
