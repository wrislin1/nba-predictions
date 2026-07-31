import os
from pathlib import Path
import subprocess
import sys

import pytest

from nba_data import cache
from scripts import audit_playoff_source


def test_audit_cache_is_deterministic(tmp_path, seeded_playoff_cache):
    first = audit_playoff_source.audit_cache(
        tmp_path, (1984, 2020)
    )
    second = audit_playoff_source.audit_cache(
        tmp_path, (1984, 2020)
    )
    assert first == second
    assert "# Playoff BREF Source Spike" in first
    assert "| 1984 | dedicated |" in first
    assert "| 2020 | dedicated |" in first
    assert "| 2020 | play_in_month |" in first
    assert "bref-202008150POR" in first
    assert "bref-202008170DEN" in first
    assert "Generated at" not in first
    assert str(tmp_path) not in first
    assert all(line == line.rstrip() for line in first.splitlines())


def test_audit_cache_rejects_missing_dedicated_body(tmp_path):
    with pytest.raises(
        cache.CacheIntegrityError,
        match="missing playoff cache entry",
    ):
        audit_playoff_source.audit_cache(tmp_path, (1984,))


def test_import_performs_no_writes(tmp_path):
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    repo_root = Path(__file__).resolve().parents[1]
    env["PYTHONPATH"] = os.pathsep.join(
        [str(repo_root), env.get("PYTHONPATH", "")]
    )
    subprocess.run(
        [sys.executable, "-c", "import scripts.audit_playoff_source"],
        cwd=tmp_path,
        env=env,
        check=True,
    )
    assert list(tmp_path.iterdir()) == []
