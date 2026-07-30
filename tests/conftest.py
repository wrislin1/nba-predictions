from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def repo_data_dir() -> Path:
    return REPO_ROOT / "data"
