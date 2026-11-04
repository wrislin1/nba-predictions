import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/diff_manifest_allowlist.py"

_SPEC = importlib.util.spec_from_file_location("diff_manifest_allowlist", SCRIPT)
diff_manifest_allowlist = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(diff_manifest_allowlist)

flatten = diff_manifest_allowlist.flatten
differing_paths = diff_manifest_allowlist.differing_paths
check = diff_manifest_allowlist.check
load_document = diff_manifest_allowlist.load_document


def test_flatten_renders_nested_and_list_paths():
    assert flatten({"a": {"b": 1}, "c": [{"d": 2}]}) == {"a.b": 1, "c[0].d": 2}


def test_differing_paths_reports_changed_added_and_removed_leaves():
    before = {
        "versions": {"dependencies": {"pandas": "3.0.5"}},
        "partitions": {"2026": {"sha256": "x"}},
    }
    after = {
        "versions": {"dependencies": {"pandas": "3.0.5", "pdfplumber": "0.11.10"}},
        "partitions": {"2026": {"sha256": "y"}},
    }
    assert differing_paths(before, after) == [
        "partitions.2026.sha256",
        "versions.dependencies.pdfplumber",
    ]
    assert differing_paths(after, before) == [
        "partitions.2026.sha256",
        "versions.dependencies.pdfplumber",
    ]


def test_check_accepts_changes_under_allowed_prefixes_only():
    before = {
        "versions": {"dependencies": {}},
        "references": {"build_manifest.json": {"sha256": "a", "bytes": 1}},
        "partitions": {"2026": {"sha256": "x"}},
    }
    after = {
        "versions": {"dependencies": {"pdfplumber": "0.11.10"}},
        "references": {"build_manifest.json": {"sha256": "b", "bytes": 2}},
        "partitions": {"2026": {"sha256": "x"}},
    }
    assert check(
        before, after, ["versions.dependencies", "references.build_manifest.json"]
    ) == []
    assert check(before, after, ["versions.dependencies"]) == [
        "references.build_manifest.json.bytes",
        "references.build_manifest.json.sha256",
    ]


def test_prefix_must_match_on_a_path_boundary():
    assert check({"a": {"bc": 1}}, {"a": {"bc": 2}}, ["a.b"]) == ["a.bc"]
    assert check({"a": {"bc": 1}}, {"a": {"bc": 2}}, ["a.bc"]) == []
    assert check({"a": {"bc": 1}}, {"a": {"bc": 2}}, ["a"]) == []


def test_load_document_rejects_duplicate_keys():
    with pytest.raises(ValueError, match="duplicate"):
        load_document('{"a": 1, "a": 2}')


def _run(before: Path, after: Path, *allowed: str) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(SCRIPT), "--before", str(before), "--after", str(after)]
    for prefix in allowed:
        command.extend(["--allow", prefix])
    return subprocess.run(command, check=False, capture_output=True, text=True)


def test_cli_exit_codes(tmp_path):
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    before.write_text(
        json.dumps(
            {
                "versions": {"dependencies": {"pandas": "3.0.5"}},
                "partitions": {"2026": {"sha256": "x"}},
            }
        ),
        encoding="utf-8",
    )
    after.write_text(
        json.dumps(
            {
                "versions": {"dependencies": {"pandas": "3.0.5", "pdfplumber": "0.11.10"}},
                "partitions": {"2026": {"sha256": "x"}},
            }
        ),
        encoding="utf-8",
    )

    allowed = _run(before, after, "versions.dependencies")
    assert allowed.returncode == 0, allowed.stderr
    assert allowed.stdout.splitlines() == ["allowed: versions.dependencies.pdfplumber"]
    assert "disallowed:" not in allowed.stdout

    disallowed = _run(before, after)
    assert disallowed.returncode == 1
    assert disallowed.stdout.splitlines() == [
        "disallowed: versions.dependencies.pdfplumber"
    ]
    assert not any(
        line.startswith("allowed:") for line in disallowed.stdout.splitlines()
    )

    identical = _run(before, before)
    assert identical.returncode == 0, identical.stderr
    assert identical.stdout == ""
