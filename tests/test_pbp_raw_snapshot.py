"""Tests for the immutable raw-plus-Parquet play-by-play snapshot: the
``nba_data.pbp_snapshot`` semantic module plus ``scripts/archive_pbp_snapshot.sh``
and ``scripts/restore_pbp_snapshot.sh``.

The archive/restore scripts are exercised as real subprocesses (they are the
thing under test: filesystem orchestration, capacity gates, atomic renames,
and the restore rollback state machine). The ``pbp_snapshot`` module's three
subcommands are additionally exercised as direct in-process calls where a
subprocess adds nothing (``rebuild-compare`` version gating, ``write-audit``
evidence rejection). Every fixture is a programmatically generated,
committed-nowhere, all-30-season cache tree; nothing here touches the
network or ``/mnt/nas``.
"""

from __future__ import annotations

from datetime import date
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import time

import pandas as pd
import pytest
import requests

from nba_data import pbp, pbp_build, pbp_snapshot


REPO_ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_SCRIPT = REPO_ROOT / "scripts/archive_pbp_snapshot.sh"
RESTORE_SCRIPT = REPO_ROOT / "scripts/restore_pbp_snapshot.sh"
PYTHON_BIN = REPO_ROOT / ".venv/bin/python"


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    def _raise(*_args, **_kwargs):
        raise AssertionError(
            "network access attempted during offline PBP snapshot tests"
        )

    monkeypatch.setattr(socket, "getaddrinfo", _raise)
    monkeypatch.setattr(socket.socket, "connect", _raise)
    monkeypatch.setattr(requests.Session, "request", _raise)
    monkeypatch.setattr(requests.Session, "send", _raise)


# ---------------------------------------------------------------------------
# Programmatic all-30-season fixture
# ---------------------------------------------------------------------------

WORKLIST_HEADERS = [
    "SEASON_ID", "TEAM_ID", "TEAM_NAME", "TEAM_ABBREVIATION",
    "GAME_ID", "GAME_DATE", "MATCHUP", "WL", "PTS",
]
GAMES_COLUMNS = [
    "game_id", "season", "game_date",
    "away_franchise_id", "home_franchise_id", "away_pts", "home_pts",
]
PLAYOFF_GAMES_COLUMNS = [
    "game_id", "season", "game_date", "game_type",
    "away_franchise_id", "home_franchise_id", "away_pts", "home_pts",
]
# Stable across 1997-2026 (see tests/test_pbp.py STABLE_TEAMS).
AWAY_TEAM = ("Boston Celtics", "BOS")
HOME_TEAM = ("Los Angeles Lakers", "LAL")
AWAY_PTS = 90
HOME_PTS = 100

SPIKE_REGULAR_1997 = "0029600001"
SPIKE_PLAYOFF_1997 = "0049600001"
SPIKE_PLAY_IN_2020 = "0051900111"
SPIKE_REGULAR_2026 = "0022500001"


def _season_id(season: int, game_type: str) -> str:
    digit = pbp.ID_PREFIXES[game_type][-1]
    return f"{digit}{season - 1:04d}"


def _game_date(season: int) -> str:
    return f"{season - 1}-11-01"


def _worklist_payload(rows: list) -> bytes:
    document = {
        "resultSets": [
            {
                "name": "LeagueGameFinderResults",
                "headers": WORKLIST_HEADERS,
                "rowSet": rows,
            }
        ]
    }
    return json.dumps(document).encode("utf-8")


def _team_rows(season: int, game_type: str, game_id: str) -> list:
    season_id = _season_id(season, game_type)
    game_date = _game_date(season)
    away_name, away_abbr = AWAY_TEAM
    home_name, home_abbr = HOME_TEAM
    return [
        [
            season_id, 1, away_name, away_abbr, game_id, game_date,
            f"{away_abbr} @ {home_abbr}", "L", AWAY_PTS,
        ],
        [
            season_id, 2, home_name, home_abbr, game_id, game_date,
            f"{home_abbr} vs. {away_abbr}", "W", HOME_PTS,
        ],
    ]


def _worklist_metadata(season: int, game_type: str) -> dict:
    return {
        "provider": "stats_nba",
        "resource": "pbp_worklist",
        "endpoint": "LeagueGameFinder",
        "parameters": {
            "Season": pbp.season_label(season),
            "SeasonType": pbp.SEASON_TYPES[game_type],
        },
        "season": season,
        "season_label": pbp.season_label(season),
        "game_type": game_type,
    }


def _game_metadata(season: int, game_type: str, game_id: str) -> dict:
    return {
        "provider": "stats_nba",
        "resource": "play_by_play",
        "endpoint": "PlayByPlayV3",
        "parameters": {"GameID": game_id, "StartPeriod": 0, "EndPeriod": 0},
        "season": season,
        "season_label": pbp.season_label(season),
        "game_type": game_type,
        "game_id": game_id,
    }


def _game_action(action_id: int, score_home: str, score_away: str) -> dict:
    return {
        "actionNumber": action_id,
        "clock": "PT11M35.00S",
        "period": 1,
        "teamId": 0,
        "teamTricode": None,
        "personId": 0,
        "playerName": None,
        "playerNameI": None,
        "xLegacy": 0,
        "yLegacy": 0,
        "shotDistance": 0,
        "shotResult": None,
        "isFieldGoal": 0,
        "scoreHome": score_home,
        "scoreAway": score_away,
        "pointsTotal": 0,
        "location": None,
        "description": None,
        "actionType": "game",
        "subType": None,
        "videoAvailable": 0,
        "shotValue": 0,
        "actionId": action_id,
    }


def _game_payload(game_id: str) -> bytes:
    document = {
        "game": {
            "gameId": game_id,
            "actions": [
                _game_action(1, "0", "0"),
                _game_action(2, str(HOME_PTS), str(AWAY_PTS)),
            ],
        }
    }
    return json.dumps(document).encode("utf-8")


def _publish_worklist(data_dir: Path, season: int, game_type: str, rows: list) -> None:
    destination = pbp.worklist_path(data_dir, season, game_type)
    pbp.publish_worklist_payload(
        destination, _worklist_payload(rows), _worklist_metadata(season, game_type)
    )


def _publish_game(data_dir: Path, season: int, game_type: str, game_id: str) -> None:
    destination = pbp.game_path(data_dir, season, game_id)
    pbp.publish_game_payload(
        destination, _game_payload(game_id), _game_metadata(season, game_type, game_id)
    )


def build_full_cache_tree(data_dir: Path, *, spike_game_ids: bool = False) -> None:
    """Seed a complete, minimal raw cache covering all 30 PBP seasons plus
    the matching ``parquet/games.parquet``/``playoff_games.parquet``
    publications ``cross_validate_worklist`` checks against.

    Each season gets exactly one regular-season worklist game (so
    ``build_tree`` always has at least one game per partition) and an empty
    Playoffs worklist; PlayIn is seeded (empty) only for play-in-era
    seasons. With ``spike_game_ids=True``, the four
    ``SOURCE_SPIKE_GAME_IDS`` games are additionally injected at their real
    season/game-type slots (1997 regular + playoff, 2020 play-in, 2026
    regular).
    """
    regular_rows = []
    postseason_rows = []
    counter = 0
    for season in pbp.SEASONS:
        counter += 1
        regular_game_id = f"002{counter:07d}"
        if spike_game_ids and season == 1997:
            regular_game_id = SPIKE_REGULAR_1997
        if spike_game_ids and season == 2026:
            regular_game_id = SPIKE_REGULAR_2026

        _publish_worklist(data_dir, season, "regular", _team_rows(season, "regular", regular_game_id))
        _publish_game(data_dir, season, "regular", regular_game_id)
        regular_rows.append({
            "game_id": regular_game_id,
            "season": season,
            "game_date": date.fromisoformat(_game_date(season)),
            "away_franchise_id": AWAY_TEAM[1],
            "home_franchise_id": HOME_TEAM[1],
            "away_pts": AWAY_PTS,
            "home_pts": HOME_PTS,
        })

        if spike_game_ids and season == 1997:
            playoff_game_id = SPIKE_PLAYOFF_1997
            _publish_worklist(
                data_dir, season, "playoff", _team_rows(season, "playoff", playoff_game_id)
            )
            _publish_game(data_dir, season, "playoff", playoff_game_id)
            postseason_rows.append({
                "game_id": playoff_game_id,
                "season": season,
                "game_date": date.fromisoformat(_game_date(season)),
                "game_type": "playoff",
                "away_franchise_id": AWAY_TEAM[1],
                "home_franchise_id": HOME_TEAM[1],
                "away_pts": AWAY_PTS,
                "home_pts": HOME_PTS,
            })
        else:
            _publish_worklist(data_dir, season, "playoff", [])

        if season in pbp.PLAY_IN_SEASONS:
            if spike_game_ids and season == 2020:
                play_in_game_id = SPIKE_PLAY_IN_2020
                _publish_worklist(
                    data_dir, season, "play_in", _team_rows(season, "play_in", play_in_game_id)
                )
                _publish_game(data_dir, season, "play_in", play_in_game_id)
                postseason_rows.append({
                    "game_id": play_in_game_id,
                    "season": season,
                    "game_date": date.fromisoformat(_game_date(season)),
                    "game_type": "play_in",
                    "away_franchise_id": AWAY_TEAM[1],
                    "home_franchise_id": HOME_TEAM[1],
                    "away_pts": AWAY_PTS,
                    "home_pts": HOME_PTS,
                })
            else:
                _publish_worklist(data_dir, season, "play_in", [])

    games_df = pd.DataFrame(regular_rows, columns=GAMES_COLUMNS)
    playoff_games_df = pd.DataFrame(postseason_rows, columns=PLAYOFF_GAMES_COLUMNS)
    parquet_dir = data_dir / "parquet"
    parquet_dir.mkdir(parents=True, exist_ok=True)
    games_df.to_parquet(parquet_dir / "games.parquet")
    playoff_games_df.to_parquet(parquet_dir / "playoff_games.parquet")


def build_full_publication(data_dir: Path) -> None:
    raw_root = data_dir / "raw/stats_nba/pbp"
    publication_root = data_dir / "parquet-large/pbp"
    publication_root.mkdir(parents=True)
    pbp_build.build_tree(raw_root, publication_root, seasons=pbp.SEASONS)


@pytest.fixture(scope="module")
def golden_data_dir(tmp_path_factory) -> Path:
    base = tmp_path_factory.mktemp("golden-data") / "data"
    build_full_cache_tree(base)
    build_full_publication(base)
    return base


@pytest.fixture(scope="module")
def golden_manifest_sha256(golden_data_dir: Path) -> str:
    manifest_path = golden_data_dir / "parquet-large/pbp/_manifest.json"
    return hashlib.sha256(manifest_path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def golden_snapshot_name(golden_manifest_sha256: str) -> str:
    return f"pbp-archive-2026-{golden_manifest_sha256[:12]}"


@pytest.fixture(scope="module")
def spike_data_dir(tmp_path_factory) -> Path:
    base = tmp_path_factory.mktemp("spike-data") / "data"
    build_full_cache_tree(base, spike_game_ids=True)
    build_full_publication(base)
    return base


@pytest.fixture(scope="module")
def spike_snapshot(tmp_path_factory, spike_data_dir: Path):
    data_dir = tmp_path_factory.mktemp("spike-archive-data") / "data"
    shutil.copytree(spike_data_dir, data_dir)
    archive_root = tmp_path_factory.mktemp("spike-archive-root")
    result = run_archive(data_dir, archive_root)
    assert result.returncode == 0, result.stderr
    snapshot = Path(result.stdout.strip().splitlines()[-1].rsplit(" ", 1)[-1])
    manifest_sha256 = hashlib.sha256(
        (snapshot / "parquet-large/pbp/_manifest.json").read_bytes()
    ).hexdigest()
    return data_dir, snapshot, manifest_sha256


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------


def run_archive(data_dir, archive_root, *, extra_env=None, path_prefix=None):
    env = dict(os.environ)
    env["NBA_PBP_DATA_DIR"] = str(data_dir)
    env["NBA_PBP_ARCHIVE_ROOT"] = str(archive_root)
    if path_prefix is not None:
        env["PATH"] = f"{path_prefix}:{env.get('PATH', '')}"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(ARCHIVE_SCRIPT)],
        env=env,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )


def run_restore(
    data_dir,
    snapshot,
    manifest_sha256,
    *,
    raw_destination=None,
    publication_destination=None,
    extra_env=None,
    path_prefix=None,
):
    env = dict(os.environ)
    env["NBA_PBP_DATA_DIR"] = str(data_dir)
    env["NBA_PBP_SNAPSHOT"] = str(snapshot)
    env["NBA_PBP_MANIFEST_SHA256"] = manifest_sha256
    if raw_destination is not None:
        env["NBA_PBP_RAW_DESTINATION"] = str(raw_destination)
    if publication_destination is not None:
        env["NBA_PBP_PUBLICATION_DESTINATION"] = str(publication_destination)
    if path_prefix is not None:
        env["PATH"] = f"{path_prefix}:{env.get('PATH', '')}"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(RESTORE_SCRIPT)],
        env=env,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )


def _archive_golden(tmp_path, golden_data_dir, *, suffix=""):
    data_dir = tmp_path / f"data{suffix}"
    shutil.copytree(golden_data_dir, data_dir)
    archive_root = tmp_path / f"archive_root{suffix}"
    archive_root.mkdir()
    result = run_archive(data_dir, archive_root)
    assert result.returncode == 0, result.stderr
    return data_dir, archive_root, result


def _fresh_snapshot(tmp_path, golden_data_dir, *, suffix=""):
    _data_dir, _archive_root, result = _archive_golden(tmp_path, golden_data_dir, suffix=suffix)
    snapshot = Path(result.stdout.strip().splitlines()[-1].rsplit(" ", 1)[-1])
    manifest_sha256 = hashlib.sha256(
        (snapshot / "parquet-large/pbp/_manifest.json").read_bytes()
    ).hexdigest()
    return snapshot, manifest_sha256


def _make_capacity_shims(shim_dir: Path) -> Path:
    shim_dir.mkdir(parents=True, exist_ok=True)
    du_script = shim_dir / "du"
    du_script.write_text(
        "#!/usr/bin/env bash\n"
        "for arg in \"$@\"; do\n"
        "  case \"$arg\" in -*) continue ;; esac\n"
        "  printf '%d\\t%s\\n' \"${FAKE_DU_BYTES_PER_PATH:-0}\" \"$arg\"\n"
        "done\n"
    )
    du_script.chmod(0o755)
    df_script = shim_dir / "df"
    df_script.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'Size Avail\\n'\n"
        "printf '%d %d\\n' \"${FAKE_DF_TOTAL_BYTES:-0}\" \"${FAKE_DF_AVAIL_BYTES:-0}\"\n"
    )
    df_script.chmod(0o755)
    return shim_dir


# ---------------------------------------------------------------------------
# Module resolution: prove the worktree's src is what actually runs
# ---------------------------------------------------------------------------


def test_module_resolves_from_this_worktree_src():
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    result = subprocess.run(
        [
            str(PYTHON_BIN), "-c",
            "import nba_data.pbp_snapshot as m, pathlib; "
            "print(pathlib.Path(m.__file__).resolve())",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    resolved = Path(result.stdout.strip())
    expected = (REPO_ROOT / "src/nba_data/pbp_snapshot.py").resolve()
    assert resolved == expected


# ---------------------------------------------------------------------------
# Archive behaviors
# ---------------------------------------------------------------------------


def test_archive_creates_immutable_snapshot_with_expected_layout(
    tmp_path, golden_data_dir, golden_snapshot_name
):
    data_dir, archive_root, result = _archive_golden(tmp_path, golden_data_dir)
    snapshot = archive_root / golden_snapshot_name
    assert snapshot.is_dir()
    assert result.stdout.strip().endswith(str(snapshot))

    assert (snapshot / "raw/stats_nba/pbp").is_dir()
    assert (snapshot / "parquet-large/pbp").is_dir()
    assert (snapshot / "raw/stats_nba/pbp/worklists/1997/regular.json").is_file()
    assert (snapshot / "parquet-large/pbp/_manifest.json").is_file()

    sums_path = snapshot / "SHA256SUMS"
    lines = sums_path.read_text().splitlines()
    listed_paths = [line.split("  ", 1)[1] for line in lines]
    assert listed_paths == sorted(listed_paths)
    assert "SHA256SUMS" not in listed_paths

    expected_paths = sorted(
        str(path.relative_to(snapshot))
        for path in snapshot.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    )
    assert sorted(listed_paths) == expected_paths

    subprocess.run(
        ["sha256sum", "--check", "--strict", "SHA256SUMS"],
        cwd=snapshot, check=True, capture_output=True, text=True,
    )


def test_archive_is_idempotent_for_exact_existing_snapshot(tmp_path, golden_data_dir):
    data_dir = tmp_path / "data"
    shutil.copytree(golden_data_dir, data_dir)
    archive_root = tmp_path / "archive_root"
    archive_root.mkdir()

    first = run_archive(data_dir, archive_root)
    assert first.returncode == 0, first.stderr
    snapshot = Path(first.stdout.strip().splitlines()[-1].rsplit(" ", 1)[-1])
    mtime_before = snapshot.stat().st_mtime_ns

    second = run_archive(data_dir, archive_root)
    assert second.returncode == 0, second.stderr
    assert snapshot.stat().st_mtime_ns == mtime_before


def test_archive_rejects_when_existing_snapshot_differs(
    tmp_path, golden_data_dir, golden_snapshot_name
):
    data_dir, archive_root, first = _archive_golden(tmp_path, golden_data_dir)
    snapshot = archive_root / golden_snapshot_name

    target = snapshot / "raw/stats_nba/pbp/worklists/1997/regular.json"
    tampered = b'{"resultSets": []}'
    target.write_bytes(tampered)

    second = run_archive(data_dir, archive_root)
    assert second.returncode != 0
    # Never modify a published snapshot -- the tampering must survive.
    assert target.read_bytes() == tampered


def test_archive_rejects_incomplete_source_inventory(tmp_path, golden_data_dir):
    data_dir = tmp_path / "data"
    shutil.copytree(golden_data_dir, data_dir)
    archive_root = tmp_path / "archive_root"
    archive_root.mkdir()

    missing = data_dir / "raw/stats_nba/pbp/worklists/1997/regular.json"
    missing.unlink()
    missing.with_name(missing.name + ".meta.json").unlink()

    result = run_archive(data_dir, archive_root)
    assert result.returncode != 0
    assert not any(archive_root.iterdir())


def test_archive_rejects_invalid_publication(tmp_path, golden_data_dir):
    data_dir = tmp_path / "data"
    shutil.copytree(golden_data_dir, data_dir)
    archive_root = tmp_path / "archive_root"
    archive_root.mkdir()

    manifest_path = data_dir / "parquet-large/pbp/_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["scope"]["game_types"] = ["bogus"]
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")

    result = run_archive(data_dir, archive_root)
    assert result.returncode != 0
    assert not any(archive_root.iterdir())


def _make_dangling_symlink(raw_root: Path) -> None:
    (raw_root / "worklists/1997/dangling.json").symlink_to("/nonexistent-target")


def _make_fifo(raw_root: Path) -> None:
    os.mkfifo(raw_root / "worklists/1997/fifo_entry")


def _make_socket(raw_root: Path) -> None:
    import tempfile as tempfile_module

    fd, short_path = tempfile_module.mkstemp(prefix="s", dir="/tmp")
    os.close(fd)
    os.unlink(short_path)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.bind(short_path)
    finally:
        sock.close()
    shutil.move(short_path, raw_root / "worklists/1997/socket_entry")


@pytest.mark.parametrize(
    "make_entry", [_make_dangling_symlink, _make_fifo, _make_socket],
    ids=["dangling-symlink", "fifo", "socket"],
)
def test_archive_rejects_unsafe_source_entries(tmp_path, golden_data_dir, make_entry):
    data_dir = tmp_path / "data"
    shutil.copytree(golden_data_dir, data_dir)
    archive_root = tmp_path / "archive_root"
    archive_root.mkdir()

    make_entry(data_dir / "raw/stats_nba/pbp")

    result = run_archive(data_dir, archive_root)
    assert result.returncode != 0
    assert not any(archive_root.iterdir())


@pytest.mark.parametrize(("avail_delta", "should_pass"), [(-1, False), (0, True)])
def test_archive_capacity_boundary(tmp_path, golden_data_dir, avail_delta, should_pass):
    data_dir = tmp_path / "data"
    shutil.copytree(golden_data_dir, data_dir)
    archive_root = tmp_path / "archive_root"
    archive_root.mkdir()
    shim_dir = _make_capacity_shims(tmp_path / "shims")

    per_path = 500
    total = 500
    required = per_path * 2 + total // 5  # 1000 + 100 = 1100
    avail = required + avail_delta

    result = run_archive(
        data_dir, archive_root,
        path_prefix=str(shim_dir),
        extra_env={
            "FAKE_DU_BYTES_PER_PATH": str(per_path),
            "FAKE_DF_TOTAL_BYTES": str(total),
            "FAKE_DF_AVAIL_BYTES": str(avail),
        },
    )
    if should_pass:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0
        assert "capacity" in result.stderr.lower()
        assert not any(archive_root.iterdir())


def test_archive_fails_closed_on_concurrent_destination(
    tmp_path, golden_data_dir, golden_snapshot_name
):
    data_dir = tmp_path / "data"
    shutil.copytree(golden_data_dir, data_dir)
    archive_root = tmp_path / "archive_root"
    archive_root.mkdir()
    race_target = archive_root / golden_snapshot_name

    shim_dir = tmp_path / "shims"
    shim_dir.mkdir()
    real_du = shutil.which("du")
    du_script = shim_dir / "du"
    du_script.write_text(
        "#!/usr/bin/env bash\n"
        "for arg in \"$@\"; do\n"
        "  case \"$arg\" in\n"
        "    *\".pbp-archive.\"*) mkdir -p -- " + shlex.quote(str(race_target)) + " ;;\n"
        "  esac\n"
        "done\n"
        f"exec {shlex.quote(real_du)} \"$@\"\n"
    )
    du_script.chmod(0o755)

    result = run_archive(data_dir, archive_root, path_prefix=str(shim_dir))
    assert result.returncode != 0
    assert "concurrent" in result.stderr.lower()
    # The race directory must not have been silently absorbed/renamed over.
    assert race_target.exists()
    assert not (race_target / "SHA256SUMS").exists()


def test_archive_cleans_up_staging_after_prepublication_failure(tmp_path, golden_data_dir):
    data_dir = tmp_path / "data"
    shutil.copytree(golden_data_dir, data_dir)
    archive_root = tmp_path / "archive_root"
    archive_root.mkdir()

    shim_dir = tmp_path / "shims"
    shim_dir.mkdir()
    rsync_script = shim_dir / "rsync"
    rsync_script.write_text("#!/usr/bin/env bash\nexit 7\n")
    rsync_script.chmod(0o755)

    result = run_archive(data_dir, archive_root, path_prefix=str(shim_dir))
    assert result.returncode != 0
    assert list(archive_root.glob(".pbp-archive.*")) == []


def test_archive_rejects_stale_staging_entry(tmp_path, golden_data_dir):
    data_dir = tmp_path / "data"
    shutil.copytree(golden_data_dir, data_dir)
    archive_root = tmp_path / "archive_root"
    archive_root.mkdir()

    stale = archive_root / ".pbp-archive.stale123"
    stale.mkdir()
    (stale / "leftover.txt").write_text("from a crashed prior run\n")

    result = run_archive(data_dir, archive_root)
    assert result.returncode != 0
    assert "stale" in result.stderr.lower()
    # The stale entry is rejected, never silently absorbed or deleted.
    assert stale.is_dir()
    assert (stale / "leftover.txt").exists()
    # And no *new* snapshot was produced alongside it.
    assert [p.name for p in archive_root.iterdir()] == [stale.name]


# ---------------------------------------------------------------------------
# Restore behaviors
# ---------------------------------------------------------------------------


def test_restore_success_sequence(tmp_path, golden_data_dir):
    snapshot, manifest_sha256 = _fresh_snapshot(tmp_path, golden_data_dir)

    raw_destination = tmp_path / "restored/raw/stats_nba/pbp"
    publication_destination = tmp_path / "restored/parquet-large/pbp"
    raw_destination.parent.mkdir(parents=True)
    publication_destination.parent.mkdir(parents=True)

    result = run_restore(
        tmp_path / "unused_data_dir", snapshot, manifest_sha256,
        raw_destination=raw_destination,
        publication_destination=publication_destination,
    )
    assert result.returncode == 0, result.stderr
    assert raw_destination.is_dir()
    assert publication_destination.is_dir()

    # 3-4. complete cache-tree + publication validation of the restore.
    pbp.validate_cache_tree(
        raw_destination, required_seasons=pbp.SEASONS, require_complete=True
    )
    pbp_build.validate_publication(raw_destination, publication_destination)

    # 5. rebuild-compare into a third empty stage.
    rebuild_stage_parent = tmp_path / "rebuild_stage_parent"
    rebuild_stage_parent.mkdir()
    pbp_snapshot.rebuild_compare(
        raw_destination, publication_destination, rebuild_stage_parent
    )
    assert list(rebuild_stage_parent.iterdir()) == []

    # 6. independently compare every publication file byte-for-byte.
    manual_stage = tmp_path / "manual_rebuild"
    manual_stage.mkdir()
    pbp_build.build_tree(raw_destination, manual_stage, seasons=pbp.SEASONS)
    for season in pbp.SEASONS:
        rebuilt = manual_stage / f"season={season}/events.parquet"
        published = publication_destination / f"season={season}/events.parquet"
        assert rebuilt.read_bytes() == published.read_bytes()


def test_restore_rejects_wrong_trusted_digest(tmp_path, golden_data_dir):
    snapshot, manifest_sha256 = _fresh_snapshot(tmp_path, golden_data_dir)
    wrong_digest = ("0" if manifest_sha256[0] != "0" else "1") + manifest_sha256[1:]
    raw_destination = tmp_path / "raw_dest"
    publication_destination = tmp_path / "pub_dest"

    result = run_restore(
        tmp_path / "unused", snapshot, wrong_digest,
        raw_destination=raw_destination, publication_destination=publication_destination,
    )
    assert result.returncode != 0
    assert not raw_destination.exists()
    assert not publication_destination.exists()


def test_restore_rejects_malformed_sha256sums(tmp_path, golden_data_dir):
    snapshot, manifest_sha256 = _fresh_snapshot(tmp_path, golden_data_dir)
    sums = snapshot / "SHA256SUMS"
    sums.write_text(sums.read_text() + "not-a-valid-line\n")

    result = run_restore(
        tmp_path / "unused", snapshot, manifest_sha256,
        raw_destination=tmp_path / "raw_dest",
        publication_destination=tmp_path / "pub_dest",
    )
    assert result.returncode != 0
    assert not (tmp_path / "raw_dest").exists()


def test_restore_rejects_tampered_sha256sums_digest(tmp_path, golden_data_dir):
    snapshot, manifest_sha256 = _fresh_snapshot(tmp_path, golden_data_dir)
    sums = snapshot / "SHA256SUMS"
    lines = sums.read_text().splitlines()
    digest, path_field = lines[0].split("  ", 1)
    tampered = ("0" if digest[0] != "0" else "1") + digest[1:]
    lines[0] = f"{tampered}  {path_field}"
    sums.write_text("\n".join(lines) + "\n")

    result = run_restore(
        tmp_path / "unused", snapshot, manifest_sha256,
        raw_destination=tmp_path / "raw_dest",
        publication_destination=tmp_path / "pub_dest",
    )
    assert result.returncode != 0
    assert not (tmp_path / "raw_dest").exists()


@pytest.mark.parametrize("prefix", ["/", "../"])
def test_restore_rejects_checksum_valid_unsafe_path(tmp_path, golden_data_dir, prefix):
    snapshot, manifest_sha256 = _fresh_snapshot(tmp_path, golden_data_dir)
    sums = snapshot / "SHA256SUMS"
    lines = sums.read_text().splitlines()
    digest, path_field = lines[0].split("  ", 1)
    lines[0] = f"{digest}  {prefix}{path_field}"
    sums.write_text("\n".join(lines) + "\n")

    result = run_restore(
        tmp_path / "unused", snapshot, manifest_sha256,
        raw_destination=tmp_path / "raw_dest",
        publication_destination=tmp_path / "pub_dest",
    )
    assert result.returncode != 0
    assert not (tmp_path / "raw_dest").exists()


def test_restore_rejects_snapshot_missing_required_entry(tmp_path, golden_data_dir):
    snapshot, manifest_sha256 = _fresh_snapshot(tmp_path, golden_data_dir)
    shutil.rmtree(snapshot / "parquet-large")

    result = run_restore(
        tmp_path / "unused", snapshot, manifest_sha256,
        raw_destination=tmp_path / "raw_dest",
        publication_destination=tmp_path / "pub_dest",
    )
    assert result.returncode != 0


def test_restore_rejects_snapshot_with_additional_path(tmp_path, golden_data_dir):
    snapshot, manifest_sha256 = _fresh_snapshot(tmp_path, golden_data_dir)
    (snapshot / "unexpected_extra_dir").mkdir()

    result = run_restore(
        tmp_path / "unused", snapshot, manifest_sha256,
        raw_destination=tmp_path / "raw_dest",
        publication_destination=tmp_path / "pub_dest",
    )
    assert result.returncode != 0


@pytest.mark.parametrize("which", ["raw", "publication"])
def test_restore_rejects_existing_destination(tmp_path, golden_data_dir, which):
    snapshot, manifest_sha256 = _fresh_snapshot(tmp_path, golden_data_dir)
    raw_destination = tmp_path / "raw_dest"
    publication_destination = tmp_path / "pub_dest"
    if which == "raw":
        raw_destination.mkdir()
    else:
        publication_destination.mkdir()

    result = run_restore(
        tmp_path / "unused", snapshot, manifest_sha256,
        raw_destination=raw_destination, publication_destination=publication_destination,
    )
    assert result.returncode != 0


@pytest.mark.parametrize(("avail_delta", "should_pass"), [(-1, False), (0, True)])
def test_restore_capacity_boundary(tmp_path, golden_data_dir, avail_delta, should_pass):
    snapshot, manifest_sha256 = _fresh_snapshot(tmp_path, golden_data_dir)
    raw_destination = tmp_path / "raw_dest"
    publication_destination = tmp_path / "pub_dest"
    shim_dir = _make_capacity_shims(tmp_path / "shims")

    per_path = 500
    total = 500
    required = per_path * 2 + total // 5
    avail = required + avail_delta

    result = run_restore(
        tmp_path / "unused", snapshot, manifest_sha256,
        raw_destination=raw_destination, publication_destination=publication_destination,
        path_prefix=str(shim_dir),
        extra_env={
            "FAKE_DU_BYTES_PER_PATH": str(per_path),
            "FAKE_DF_TOTAL_BYTES": str(total),
            "FAKE_DF_AVAIL_BYTES": str(avail),
        },
    )
    if should_pass:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0
        assert "capacity" in result.stderr.lower()
        assert not raw_destination.exists()
        assert not publication_destination.exists()


def test_restore_rolls_back_raw_when_second_rename_fails(tmp_path, golden_data_dir):
    snapshot, manifest_sha256 = _fresh_snapshot(tmp_path, golden_data_dir)
    raw_parent = tmp_path / "raw_parent"
    publication_parent = tmp_path / "publication_parent"
    raw_parent.mkdir()
    publication_parent.mkdir()
    raw_destination = raw_parent / "raw"
    publication_destination = publication_parent / "publication"

    shim_dir = tmp_path / "shims"
    shim_dir.mkdir()
    real_rsync = shutil.which("rsync")
    rsync_script = shim_dir / "rsync"
    rsync_script.write_text(
        "#!/usr/bin/env bash\n"
        f"{shlex.quote(real_rsync)} \"$@\"\n"
        "rc=$?\n"
        "chmod 555 -- \"$PBP_TEST_LOCK_PUBLICATION_PARENT\"\n"
        "exit \"$rc\"\n"
    )
    rsync_script.chmod(0o755)

    try:
        result = run_restore(
            tmp_path / "unused", snapshot, manifest_sha256,
            raw_destination=raw_destination,
            publication_destination=publication_destination,
            path_prefix=str(shim_dir),
            extra_env={"PBP_TEST_LOCK_PUBLICATION_PARENT": str(publication_parent)},
        )
        assert result.returncode != 0
        assert "rolled back" in result.stderr.lower()
        assert not raw_destination.exists()
        assert not publication_destination.exists()
        assert list(raw_parent.glob(".pbp-restore-raw.*")) == []
    finally:
        publication_parent.chmod(0o755)


def test_restore_rollback_failure_retains_named_recovery_paths(tmp_path, golden_data_dir):
    """A failure in *any* part of the second-destination step -- here, the
    post-publish integrity probe on the raw destination itself failing
    (simulating ENOENT/EACCES/a stalled mount) -- must still drive the
    rollback attempt. When that rollback also fails (raw_parent is locked),
    every retained path is named and nothing is deleted."""
    snapshot, manifest_sha256 = _fresh_snapshot(tmp_path, golden_data_dir)
    raw_parent = tmp_path / "raw_parent"
    publication_parent = tmp_path / "publication_parent"
    raw_parent.mkdir()
    publication_parent.mkdir()
    raw_destination = raw_parent / "raw"
    publication_destination = publication_parent / "publication"

    shim_dir = tmp_path / "shims"
    shim_dir.mkdir()
    real_du = shutil.which("du")
    du_script = shim_dir / "du"
    du_script.write_text(
        "#!/usr/bin/env bash\n"
        "for arg in \"$@\"; do\n"
        "  if [ \"$arg\" = \"$PBP_TEST_RAW_DESTINATION\" ]; then\n"
        "    chmod 555 -- \"$PBP_TEST_RAW_PARENT\"\n"
        "    exit 1\n"
        "  fi\n"
        "done\n"
        f"exec {shlex.quote(real_du)} \"$@\"\n"
    )
    du_script.chmod(0o755)

    try:
        result = run_restore(
            tmp_path / "unused", snapshot, manifest_sha256,
            raw_destination=raw_destination,
            publication_destination=publication_destination,
            path_prefix=str(shim_dir),
            extra_env={
                "PBP_TEST_RAW_DESTINATION": str(raw_destination),
                "PBP_TEST_RAW_PARENT": str(raw_parent),
            },
        )
        assert result.returncode != 0
        assert "FATAL" in result.stderr
        assert "do not delete" in result.stderr
        assert str(raw_destination) in result.stderr
        assert str(publication_destination) in result.stderr
        # Nothing was deleted: the raw destination (published, then an
        # unrelated failure blocked rollback) is still exactly there.
        assert raw_destination.is_dir()
    finally:
        raw_parent.chmod(0o755)


def test_restore_probe_failure_alone_triggers_rollback_not_a_bare_abort(
    tmp_path, golden_data_dir
):
    """The narrowest regression case: the post-publish probe on the raw
    destination fails, raw_parent is otherwise perfectly writable, and
    nothing else is wrong. The rollback must still be attempted (and here,
    succeed cleanly) rather than the script aborting mid-flight under
    set -e with the raw destination left half-published and unguided."""
    snapshot, manifest_sha256 = _fresh_snapshot(tmp_path, golden_data_dir)
    raw_destination = tmp_path / "raw_dest"
    publication_destination = tmp_path / "pub_dest"

    shim_dir = tmp_path / "shims"
    shim_dir.mkdir()
    real_du = shutil.which("du")
    du_script = shim_dir / "du"
    du_script.write_text(
        "#!/usr/bin/env bash\n"
        "for arg in \"$@\"; do\n"
        "  if [ \"$arg\" = \"$PBP_TEST_RAW_DESTINATION\" ]; then\n"
        "    exit 1\n"
        "  fi\n"
        "done\n"
        f"exec {shlex.quote(real_du)} \"$@\"\n"
    )
    du_script.chmod(0o755)

    result = run_restore(
        tmp_path / "unused", snapshot, manifest_sha256,
        raw_destination=raw_destination,
        publication_destination=publication_destination,
        path_prefix=str(shim_dir),
        extra_env={"PBP_TEST_RAW_DESTINATION": str(raw_destination)},
    )
    assert result.returncode != 0
    assert "rolled back" in result.stderr.lower()
    assert not raw_destination.exists()
    assert not publication_destination.exists()


def test_rebuild_compare_rejects_version_mismatch(tmp_path, golden_data_dir):
    data_dir = tmp_path / "data"
    shutil.copytree(golden_data_dir, data_dir)
    raw_root = data_dir / "raw/stats_nba/pbp"
    publication_root = data_dir / "parquet-large/pbp"

    manifest_path = publication_root / "_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["versions"]["python"] = "0.0.0"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")

    stage_parent = tmp_path / "stage_parent"
    stage_parent.mkdir()
    with pytest.raises(pbp_snapshot.PBPSnapshotError, match="version"):
        pbp_snapshot.rebuild_compare(raw_root, publication_root, stage_parent)
    assert list(stage_parent.iterdir()) == []


def test_restore_post_publication_revalidation_catches_corruption(tmp_path, golden_data_dir):
    snapshot, manifest_sha256 = _fresh_snapshot(tmp_path, golden_data_dir)
    raw_parent = tmp_path / "raw_parent"
    publication_parent = tmp_path / "publication_parent"
    raw_parent.mkdir()
    publication_parent.mkdir()
    raw_destination = raw_parent / "raw"
    publication_destination = publication_parent / "publication"
    target_file = raw_destination / "worklists/1997/regular.json"

    shim_dir = tmp_path / "shims"
    shim_dir.mkdir()
    real_du = shutil.which("du")
    du_script = shim_dir / "du"
    du_script.write_text(
        "#!/usr/bin/env bash\n"
        "for arg in \"$@\"; do\n"
        "  if [ \"$arg\" = \"$PBP_TEST_RAW_DESTINATION\" ]; then\n"
        "    printf '{\"resultSets\": []}' > \"$PBP_TEST_TARGET_FILE\"\n"
        "  fi\n"
        "done\n"
        f"exec {shlex.quote(real_du)} \"$@\"\n"
    )
    du_script.chmod(0o755)

    result = run_restore(
        tmp_path / "unused", snapshot, manifest_sha256,
        raw_destination=raw_destination,
        publication_destination=publication_destination,
        path_prefix=str(shim_dir),
        extra_env={
            "PBP_TEST_RAW_DESTINATION": str(raw_destination),
            "PBP_TEST_TARGET_FILE": str(target_file),
        },
    )
    assert result.returncode != 0
    # Both renames had already committed by the time the post-publish
    # revalidation ran; this script makes no further rollback attempt.
    assert raw_destination.exists()
    assert publication_destination.exists()


# ---------------------------------------------------------------------------
# write-audit behaviors
# ---------------------------------------------------------------------------


def test_write_audit_success_renders_report(tmp_path, spike_snapshot):
    data_dir, snapshot, manifest_sha256 = spike_snapshot
    output = tmp_path / "audit.md"
    pbp_snapshot.write_audit(
        data_dir, snapshot, manifest_sha256, output,
        reproducible_build_verified=True, restore_verified=True,
    )
    text = output.read_text()
    assert "Play-by-play archive audit" in text
    assert manifest_sha256 in text
    assert snapshot.name in text
    assert "reproducible_build: passed" in text
    assert "restore_verified: passed" in text


@pytest.mark.parametrize(
    ("reproducible", "restored"),
    [(False, True), (True, False), (False, False)],
)
def test_write_audit_rejects_missing_evidence_flags(
    tmp_path, spike_snapshot, reproducible, restored
):
    data_dir, snapshot, manifest_sha256 = spike_snapshot
    output = tmp_path / "audit.md"
    with pytest.raises(pbp_build.PBPBuildError):
        pbp_snapshot.write_audit(
            data_dir, snapshot, manifest_sha256, output,
            reproducible_build_verified=reproducible, restore_verified=restored,
        )
    assert not output.exists()


def test_write_audit_cli_rejects_missing_evidence_flags(tmp_path, spike_snapshot):
    data_dir, snapshot, manifest_sha256 = spike_snapshot
    output = tmp_path / "audit.md"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    result = subprocess.run(
        [
            str(PYTHON_BIN), "-m", "nba_data.pbp_snapshot", "write-audit",
            "--data-dir", str(data_dir),
            "--snapshot", str(snapshot),
            "--manifest-sha256", manifest_sha256,
            "--output", str(output),
        ],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert not output.exists()


def test_write_audit_rejects_missing_source_spike_game(tmp_path, golden_data_dir):
    snapshot, manifest_sha256 = _fresh_snapshot(tmp_path, golden_data_dir)
    data_dir = tmp_path / "data"
    output = tmp_path / "audit.md"
    with pytest.raises(pbp_snapshot.PBPSnapshotError, match="source-spike"):
        pbp_snapshot.write_audit(
            data_dir, snapshot, manifest_sha256, output,
            reproducible_build_verified=True, restore_verified=True,
        )
    assert not output.exists()


def test_write_audit_rejects_cross_source_mismatch(tmp_path, spike_snapshot):
    # spike_snapshot is module-scoped and reused by other tests in this
    # file; copy its data_dir into this test's own tmp_path before
    # mutating anything so no other test ever observes the corruption.
    original_data_dir, snapshot, manifest_sha256 = spike_snapshot
    original_games_bytes = (original_data_dir / "parquet/games.parquet").read_bytes()
    data_dir = tmp_path / "data"
    shutil.copytree(original_data_dir, data_dir)

    games_path = data_dir / "parquet/games.parquet"
    frame = pd.read_parquet(games_path)
    frame.loc[frame.index[0], "home_pts"] = int(frame.loc[frame.index[0], "home_pts"]) + 1
    frame.to_parquet(games_path)

    output = tmp_path / "audit.md"
    with pytest.raises(pbp.PBPValidationError):
        pbp_snapshot.write_audit(
            data_dir, snapshot, manifest_sha256, output,
            reproducible_build_verified=True, restore_verified=True,
        )
    assert not output.exists()
    # The shared module-scoped fixture's own copy is provably untouched.
    assert (
        original_data_dir / "parquet/games.parquet"
    ).read_bytes() == original_games_bytes


# ---------------------------------------------------------------------------
# Performance regression guard: SHA256SUMS grammar validation must be
# linear, not quadratic, at real-archive scale (tens of thousands of
# files). This extracts the actual function body out of the live script
# (never a hand-duplicated copy, so it can't silently drift from what
# production runs) and times it standalone against a large synthetic
# SHA256SUMS.
# ---------------------------------------------------------------------------


def _extract_bash_function(script_path: Path, function_name: str) -> str:
    text = script_path.read_text()
    start = text.index(f"{function_name}() {{")
    depth = 0
    end = None
    for index in range(start, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    assert end is not None, f"could not find end of function {function_name!r}"
    return text[start:end]


def test_sha256sums_grammar_validation_is_linear(tmp_path):
    function_source = _extract_bash_function(
        RESTORE_SCRIPT, "validate_sha256sums_grammar"
    )

    snapshot = tmp_path / "bench_snapshot"
    snapshot.mkdir()
    count = 8000
    lines = []
    for index in range(count):
        relative = f"file_{index:06d}.bin"
        payload = str(index).encode("utf-8")
        (snapshot / relative).write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        lines.append(f"{digest}  {relative}")
    (snapshot / "SHA256SUMS").write_text("\n".join(lines) + "\n")

    harness = tmp_path / "harness.sh"
    harness.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "export LC_ALL=C\n"
        + function_source
        + "\n"
        'validate_sha256sums_grammar "$1"\n'
    )
    harness.chmod(0o755)

    start = time.monotonic()
    result = subprocess.run(
        ["bash", str(harness), str(snapshot)],
        capture_output=True, text=True, timeout=60,
    )
    elapsed = time.monotonic() - start
    assert result.returncode == 0, result.stderr
    # The reviewer benchmarked the pre-fix (quadratic-ish) implementation at
    # 4,000 lines -> 4.8s, 16,000 -> 38s (~n^1.9); extrapolated, 8,000 lines
    # would take roughly 18s there. The linear rewrite should clear this in
    # a couple of seconds. A generous-but-discriminating bound keeps this
    # from being flaky on a loaded machine while still catching a
    # regression back to quadratic behavior.
    assert elapsed < 12, (
        f"SHA256SUMS grammar validation took {elapsed:.1f}s for {count} "
        "lines -- expected linear-time behavior"
    )
