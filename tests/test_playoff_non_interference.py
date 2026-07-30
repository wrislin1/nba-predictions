import hashlib
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = (
    REPO_ROOT / "tests/fixtures/playoff_non_interference_sha256.json"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def test_regular_publication_matches_locked_baseline():
    expected = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    actual = {
        relative: sha256_file(REPO_ROOT / relative)
        for relative in sorted(expected)
    }
    assert actual == expected
