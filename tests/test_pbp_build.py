"""Tests for the fixed V3 play-by-play event contract parser and the
deterministic season-partitioned build/publish/audit pipeline."""

from datetime import date
import hashlib
import json
from pathlib import Path
import re
import shutil
import socket
import sys

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from nba_data import cache, pbp, pbp_build


FIXTURES = Path(__file__).resolve().parent / "fixtures/pbp"


@pytest.mark.parametrize(
    ("clock", "period", "expected"),
    [
        ("PT11M35.00S", 1, 695.0),
        ("PT00M07.30S", 4, 7.3),
        ("PT00M00.00S", 4, 0.0),
        ("PT05M00.00S", 5, 300.0),
    ],
)
def test_parse_clock(clock, period, expected):
    assert pbp_build.parse_clock(clock, period) == expected


@pytest.mark.parametrize(
    ("clock", "period"),
    [
        ("11:35", 1),
        ("PT12M00.01S", 5),
        ("PT05M00.01S", 6),
        ("PT-1M00S", 1),
    ],
)
def test_parse_clock_rejects_invalid_values(clock, period):
    with pytest.raises(pbp_build.PBPBuildError, match="invalid V3 clock"):
        pbp_build.parse_clock(clock, period)


def test_running_scores_preserve_early_zero_sentinel_and_correction():
    home, away = pbp_build.derive_running_scores(
        ["0", "0", "0", "98", "98"],
        ["0", "2", "0", "108", "107"],
    )
    assert home == [0, 0, 0, 98, 98]
    assert away == [0, 2, 2, 108, 107]


def test_running_scores_carry_modern_blanks():
    home, away = pbp_build.derive_running_scores(
        ["0", "", "2"],
        ["0", "", "0"],
    )
    assert home == [0, 0, 2]
    assert away == [0, 0, 0]


@pytest.mark.parametrize(
    ("home_raw", "away_raw"),
    [
        (["0", ""], ["0", "5"]),  # one-sided blank (home blank, away not)
        (["0", "5"], ["0", ""]),  # one-sided blank (away blank, home not)
        (["0", "-5"], ["0", "3"]),  # negative score
        (["0", "12.5"], ["0", "9"]),  # non-integer score
        (["5"], ["3"]),  # first valid pair other than 0-0
    ],
    ids=[
        "one-sided-blank-home",
        "one-sided-blank-away",
        "negative-score",
        "non-integer-score",
        "first-pair-not-zero-zero",
    ],
)
def test_running_scores_reject_invalid_pairs(home_raw, away_raw):
    with pytest.raises(pbp_build.PBPBuildError, match="invalid raw score pair"):
        pbp_build.derive_running_scores(home_raw, away_raw)


def worklist_row(**changes):
    row = {
        "game_id": "0029600001",
        "season": 1997,
        "game_type": "regular",
        "game_date": date(1996, 11, 1),
        "away_franchise_id": "CHI",
        "home_franchise_id": "BOS",
        "away_pts": 107,
        "home_pts": 98,
    }
    row.update(changes)
    return row


def test_parse_game_uses_action_id_primary_key_and_preserves_action_number():
    frame, report = pbp_build.parse_game_payload(
        (FIXTURES / "early_score_sentinel.json").read_bytes(),
        worklist_row(),
    )
    assert frame["action_id"].tolist() == list(range(1, 9))
    assert frame["action_number"].tolist().count(10) == 2
    assert frame[["score_home", "score_away"]].iloc[-1].tolist() == [98, 107]
    assert frame.columns.tolist() == pbp_build.EVENT_COLUMNS
    assert report["events"] == 8
    assert report["historical_zero_sentinels"] == 2


def test_parse_game_accepts_modern_blanks_and_double_overtime():
    frame, report = pbp_build.parse_game_payload(
        (FIXTURES / "modern_blank_score.json").read_bytes(),
        worklist_row(
            game_id="0022500001",
            season=2026,
            game_date=date(2025, 10, 21),
            away_franchise_id="HOU",
            home_franchise_id="OKC",
            away_pts=124,
            home_pts=125,
        ),
    )
    assert frame["period"].unique().tolist() == [1, 2, 3, 4, 5, 6]
    assert frame.loc[1, ["score_home", "score_away"]].tolist() == [0, 0]
    assert report["max_period"] == 6


def test_parse_game_preserves_exact_raw_score_strings():
    # Global constraint: "Preserve the exact source score strings in
    # score_home_raw and score_away_raw; derive non-null running scores
    # separately." This must hold even where the derived score_home/
    # score_away differ from the raw string (historical zero sentinels)
    # or where the raw string is blank (modern carried-forward blanks).
    sentinel_frame, _ = pbp_build.parse_game_payload(
        (FIXTURES / "early_score_sentinel.json").read_bytes(),
        worklist_row(),
    )
    assert sentinel_frame["score_home_raw"].tolist() == [
        "0", "0", "0", "0", "25", "50", "98", "98",
    ]
    assert sentinel_frame["score_away_raw"].tolist() == [
        "0", "2", "0", "0", "30", "60", "108", "107",
    ]

    blank_frame, _ = pbp_build.parse_game_payload(
        (FIXTURES / "modern_blank_score.json").read_bytes(),
        worklist_row(
            game_id="0022500001",
            season=2026,
            game_date=date(2025, 10, 21),
            away_franchise_id="HOU",
            home_franchise_id="OKC",
            away_pts=124,
            home_pts=125,
        ),
    )
    assert blank_frame["score_home_raw"].tolist() == [
        "0", "", "30", "60", "100", "112", "125",
    ]
    assert blank_frame["score_away_raw"].tolist() == [
        "0", "", "32", "61", "100", "112", "124",
    ]
    # The blank pair must stay the empty string, not be cast to NaN/null
    # by the nullable "string" dtype.
    assert blank_frame.loc[1, "score_home_raw"] == ""
    assert blank_frame.loc[1, "score_away_raw"] == ""
    assert blank_frame["score_home_raw"].notna().all()
    assert blank_frame["score_away_raw"].notna().all()


def _document_bytes(mutator):
    document = json.loads(
        (FIXTURES / "early_score_sentinel.json").read_bytes()
    )
    mutator(document)
    return json.dumps(document).encode("utf-8")


def _wrong_outer_game_id(document):
    document["game"]["gameId"] = "0000000000"


def _missing_action_key(document):
    del document["game"]["actions"][0]["location"]


def _additional_action_key(document):
    document["game"]["actions"][0]["extraKey"] = 1


def _boolean_in_integer_field(document):
    document["game"]["actions"][0]["actionNumber"] = True


def _non_string_in_string_field(document):
    document["game"]["actions"][0]["actionType"] = 5


def _duplicate_action_id(document):
    document["game"]["actions"][1]["actionId"] = (
        document["game"]["actions"][0]["actionId"]
    )


def _zero_action_id(document):
    document["game"]["actions"][0]["actionId"] = 0


def _out_of_order_action_id(document):
    actions = document["game"]["actions"]
    actions[0]["actionId"], actions[1]["actionId"] = (
        actions[1]["actionId"],
        actions[0]["actionId"],
    )


def _period_gap(document):
    # Row index 4 is the period-2 boundary action; skipping straight to 3
    # breaks the required 1..max(period) coverage.
    document["game"]["actions"][4]["period"] = 3


def _terminal_score_mismatch(document):
    document["game"]["actions"][-1]["scoreHome"] = "99"


@pytest.mark.parametrize(
    ("mutator", "match"),
    [
        (_wrong_outer_game_id, "gameId"),
        (_missing_action_key, "missing key"),
        (_additional_action_key, "unexpected key"),
        (_boolean_in_integer_field, "non-boolean integer"),
        (_non_string_in_string_field, "string or null"),
        (_duplicate_action_id, "duplicate actionId"),
        (_zero_action_id, "actionId must be positive"),
        (_out_of_order_action_id, "actionId must strictly increase"),
        (_period_gap, "periods must run"),
        (_terminal_score_mismatch, "terminal score"),
    ],
    ids=[
        "wrong-outer-game-id",
        "missing-action-key",
        "additional-action-key",
        "boolean-in-integer-field",
        "non-string-in-string-field",
        "duplicate-action-id",
        "zero-negative-action-id",
        "out-of-order-action-id",
        "period-coverage-gap",
        "terminal-score-mismatch",
    ],
)
def test_parse_game_payload_rejects_contract_violations(mutator, match):
    with pytest.raises(pbp_build.PBPBuildError, match=match):
        pbp_build.parse_game_payload(_document_bytes(mutator), worklist_row())


# ---------------------------------------------------------------------------
# Task 6: deterministic season partitions, manifest, publication validation,
# and audit rendering.
# ---------------------------------------------------------------------------

_SEASON_ID_DIGIT = {"regular": "2", "playoff": "4", "play_in": "5"}
_WORKLIST_HEADERS = [
    "SEASON_ID", "TEAM_ID", "TEAM_NAME", "TEAM_ABBREVIATION",
    "GAME_ID", "GAME_DATE", "MATCHUP", "WL", "PTS",
]


def _season_id(season, game_type):
    return f"{_SEASON_ID_DIGIT[game_type]}{season - 1}"


def _worklist_document(rows):
    return {
        "resultSets": [
            {
                "name": "LeagueGameFinderResults",
                "headers": _WORKLIST_HEADERS,
                "rowSet": rows,
            }
        ]
    }


def _game_rows(
    season, game_type, game_id, game_date,
    away_name, away_abbr, home_name, home_abbr, away_pts, home_pts,
):
    season_id = _season_id(season, game_type)
    return [
        [
            season_id, 1, away_name, away_abbr, game_id, game_date,
            f"{away_abbr} @ {home_abbr}",
            "W" if away_pts > home_pts else "L", away_pts,
        ],
        [
            season_id, 2, home_name, home_abbr, game_id, game_date,
            f"{home_abbr} vs. {away_abbr}",
            "W" if home_pts > away_pts else "L", home_pts,
        ],
    ]


def _worklist_metadata(season, game_type):
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


def _game_metadata(season, game_type, game_id):
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


def _publish_worklist(data_dir, season, game_type, rows):
    payload = json.dumps(_worklist_document(rows)).encode("utf-8")
    pbp.publish_worklist_payload(
        pbp.worklist_path(data_dir, season, game_type),
        payload,
        _worklist_metadata(season, game_type),
    )


def _publish_game(data_dir, season, game_type, game_id, payload):
    pbp.publish_game_payload(
        pbp.game_path(data_dir, season, game_id),
        payload,
        _game_metadata(season, game_type, game_id),
    )


def seed_two_season_publication(data_dir: Path) -> Path:
    """Seed a complete, minimal raw cache tree for ending seasons 1997/2026.

    Reuses the two committed full V3 fixtures (``early_score_sentinel.json``
    for the single 1997 regular-season game, ``modern_blank_score.json`` for
    the single 2026 regular-season game) with matching exact sidecars, plus
    empty (but present) worklists for every other required
    ``(season, game_type)`` combination so ``require_complete=True`` is
    satisfied with the smallest possible fixture set.
    """
    _publish_worklist(
        data_dir, 1997, "regular",
        _game_rows(
            1997, "regular", "0029600001", "1996-11-01",
            "Chicago Bulls", "CHI", "Boston Celtics", "BOS", 107, 98,
        ),
    )
    _publish_worklist(data_dir, 1997, "playoff", [])
    _publish_worklist(
        data_dir, 2026, "regular",
        _game_rows(
            2026, "regular", "0022500001", "2025-10-21",
            "Houston Rockets", "HOU", "Oklahoma City Thunder", "OKC",
            124, 125,
        ),
    )
    _publish_worklist(data_dir, 2026, "playoff", [])
    _publish_worklist(data_dir, 2026, "play_in", [])

    _publish_game(
        data_dir, 1997, "regular", "0029600001",
        (FIXTURES / "early_score_sentinel.json").read_bytes(),
    )
    _publish_game(
        data_dir, 2026, "regular", "0022500001",
        (FIXTURES / "modern_blank_score.json").read_bytes(),
    )
    return data_dir / "raw/stats_nba/pbp"


def _partition_path(stage_dir, season):
    return stage_dir / f"season={season}" / "events.parquet"


def _rewrite_partition(path, table):
    pq.write_table(
        table,
        path,
        compression="zstd",
        compression_level=9,
        use_dictionary=False,
        write_statistics=True,
        row_group_size=65_536,
        data_page_version="2.0",
        version="2.6",
    )


def _load_manifest(stage_dir):
    return json.loads((stage_dir / "_manifest.json").read_text(encoding="utf-8"))


def _save_manifest(stage_dir, manifest):
    (stage_dir / "_manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Step 1: deterministic season partitions
# ---------------------------------------------------------------------------


def test_build_tree_writes_deterministic_season_partitions(tmp_path):
    data_dir = tmp_path / "data"
    raw_root = seed_two_season_publication(data_dir)
    stage_dir = tmp_path / "stage"
    stage_dir.mkdir()

    manifest = pbp_build.build_tree(raw_root, stage_dir, seasons=[1997, 2026])

    produced = sorted(
        p.relative_to(stage_dir).as_posix()
        for p in stage_dir.rglob("*") if p.is_file()
    )
    assert produced == [
        "_manifest.json",
        "season=1997/events.parquet",
        "season=2026/events.parquet",
    ]

    for season in (1997, 2026):
        path = _partition_path(stage_dir, season)
        table = pq.read_table(path)
        assert table.schema.equals(
            pbp_build.PBP_ARROW_SCHEMA, check_metadata=False
        )
        keys = list(
            zip(
                table.column("game_id").to_pylist(),
                table.column("action_id").to_pylist(),
            )
        )
        assert keys == sorted(keys)
        assert len(set(keys)) == len(keys)

        parquet_metadata = pq.ParquetFile(path).metadata
        assert parquet_metadata.num_row_groups >= 1
        for rg_index in range(parquet_metadata.num_row_groups):
            row_group = parquet_metadata.row_group(rg_index)
            for col_index in range(row_group.num_columns):
                assert row_group.column(col_index).compression == "ZSTD"

        entry = manifest["partitions"][str(season)]
        assert entry["sha256"] == cache.sha256_file(path)
        assert entry["bytes"] == path.stat().st_size
        assert entry["rows"] == table.num_rows
        assert entry["games"] == 1
        assert entry["max_period"] == int(
            max(table.column("period").to_pylist())
        )

    # Ground truth for the two known fixtures, computed independently via
    # the already-tested parser rather than hand-copied magic numbers.
    frame_97, report_97 = pbp_build.parse_game_payload(
        (FIXTURES / "early_score_sentinel.json").read_bytes(), worklist_row()
    )
    frame_26, report_26 = pbp_build.parse_game_payload(
        (FIXTURES / "modern_blank_score.json").read_bytes(),
        worklist_row(
            game_id="0022500001", season=2026,
            game_date=date(2025, 10, 21),
            away_franchise_id="HOU", home_franchise_id="OKC",
            away_pts=124, home_pts=125,
        ),
    )
    games = manifest["validation"]["games"]
    assert games["0029600001"]["event_count"] == report_97["events"]
    assert games["0029600001"]["max_period"] == report_97["max_period"]
    assert games["0029600001"]["early_zero_sentinels"] == (
        report_97["historical_zero_sentinels"]
    )
    assert games["0029600001"]["blank_score_pairs"] == (
        report_97["blank_score_pairs"]
    )
    assert games["0022500001"]["event_count"] == report_26["events"]
    assert games["0022500001"]["max_period"] == report_26["max_period"]
    assert games["0022500001"]["blank_score_pairs"] == (
        report_26["blank_score_pairs"]
    )
    assert manifest["scope"]["ending_seasons"] == [1997, 2026]


def test_build_tree_two_builds_are_byte_identical(tmp_path):
    data_dir = tmp_path / "data"
    raw_root = seed_two_season_publication(data_dir)
    stage_a = tmp_path / "stage_a"
    stage_a.mkdir()
    stage_b = tmp_path / "stage_b"
    stage_b.mkdir()

    pbp_build.build_tree(raw_root, stage_a, seasons=[1997, 2026])
    pbp_build.build_tree(raw_root, stage_b, seasons=[1997, 2026])

    relative_a = sorted(
        p.relative_to(stage_a).as_posix() for p in stage_a.rglob("*") if p.is_file()
    )
    relative_b = sorted(
        p.relative_to(stage_b).as_posix() for p in stage_b.rglob("*") if p.is_file()
    )
    assert relative_a == relative_b
    assert relative_a  # sanity: not vacuously empty
    for relative in relative_a:
        assert (stage_a / relative).read_bytes() == (stage_b / relative).read_bytes()


def test_build_tree_writes_one_season_at_a_time(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    raw_root = seed_two_season_publication(data_dir)
    stage_dir = tmp_path / "stage"
    stage_dir.mkdir()

    real_parse = pbp_build.parse_game_payload
    violations = []

    def instrumented(payload, row):
        if int(row["season"]) == 2026 and not _partition_path(
            stage_dir, 1997
        ).exists():
            violations.append(row["game_id"])
        return real_parse(payload, row)

    monkeypatch.setattr(pbp_build, "parse_game_payload", instrumented)
    pbp_build.build_tree(raw_root, stage_dir, seasons=[1997, 2026])
    assert violations == []


def test_build_tree_rejects_nonempty_stage(tmp_path):
    data_dir = tmp_path / "data"
    raw_root = seed_two_season_publication(data_dir)
    stage_dir = tmp_path / "stage"
    stage_dir.mkdir()
    (stage_dir / "stray.txt").write_text("x")
    with pytest.raises(pbp_build.PBPBuildError, match="empty"):
        pbp_build.build_tree(raw_root, stage_dir, seasons=[1997, 2026])


def test_build_tree_rejects_missing_stage(tmp_path):
    data_dir = tmp_path / "data"
    raw_root = seed_two_season_publication(data_dir)
    stage_dir = tmp_path / "does-not-exist"
    with pytest.raises(pbp_build.PBPBuildError, match="does not exist"):
        pbp_build.build_tree(raw_root, stage_dir, seasons=[1997, 2026])


# ---------------------------------------------------------------------------
# Step 2: manifest provenance
# ---------------------------------------------------------------------------


def test_build_tree_manifest_shape_and_provenance(tmp_path):
    data_dir = tmp_path / "data"
    raw_root = seed_two_season_publication(data_dir)
    stage_dir = tmp_path / "stage"
    stage_dir.mkdir()
    manifest = pbp_build.build_tree(raw_root, stage_dir, seasons=[1997, 2026])

    assert manifest["schema_version"] == 1
    assert manifest["scope"] == {
        "ending_seasons": [1997, 2026],
        "game_types": ["play_in", "playoff", "regular"],
    }
    assert [row["name"] for row in manifest["schema"]] == pbp_build.EVENT_COLUMNS
    for row in manifest["schema"]:
        assert set(row) == {"name", "arrow_type", "nullable", "origin"}
        assert row["origin"] in {"source", "derived"}
    assert manifest["schema"][0] == {
        "name": "game_id", "arrow_type": "large_string",
        "nullable": False, "origin": "derived",
    }

    worklists = manifest["sources"]["worklists"]
    games = manifest["sources"]["games"]
    assert [entry["body_path"] for entry in worklists] == sorted(
        entry["body_path"] for entry in worklists
    )
    assert [entry["body_path"] for entry in games] == sorted(
        entry["body_path"] for entry in games
    )
    assert {entry["body_path"] for entry in worklists} == {
        "raw/stats_nba/pbp/worklists/1997/regular.json",
        "raw/stats_nba/pbp/worklists/1997/playoff.json",
        "raw/stats_nba/pbp/worklists/2026/regular.json",
        "raw/stats_nba/pbp/worklists/2026/playoff.json",
        "raw/stats_nba/pbp/worklists/2026/play_in.json",
    }
    assert {entry["body_path"] for entry in games} == {
        "raw/stats_nba/pbp/games/1997/0029600001.json",
        "raw/stats_nba/pbp/games/2026/0022500001.json",
    }
    for entry in worklists + games:
        assert set(entry) == {
            "body_path", "body_sha256", "body_content_length",
            "sidecar_path", "sidecar_sha256", "sidecar_content_length",
        }
        assert not entry["body_path"].startswith("/")
        assert not entry["sidecar_path"].startswith("/")
        body = data_dir / entry["body_path"]
        sidecar = data_dir / entry["sidecar_path"]
        assert cache.sha256_file(body) == entry["body_sha256"]
        assert body.stat().st_size == entry["body_content_length"]
        sidecar_bytes = sidecar.read_bytes()
        assert hashlib.sha256(sidecar_bytes).hexdigest() == entry["sidecar_sha256"]
        assert len(sidecar_bytes) == entry["sidecar_content_length"]

    assert manifest["versions"]["python"] == (
        f"{sys.version_info.major}.{sys.version_info.minor}."
        f"{sys.version_info.micro}"
    )
    assert manifest["versions"]["pyarrow"] == pa.__version__

    validation = manifest["validation"]
    assert validation["worklist_counts"] == {
        "1997": {"regular": 1, "playoff": 0},
        "2026": {"regular": 1, "playoff": 0, "play_in": 0},
    }
    assert validation["game_counts"] == validation["worklist_counts"]
    assert validation["event_counts"]["1997"]["regular"] == (
        validation["games"]["0029600001"]["event_count"]
    )
    assert validation["event_counts"]["2026"]["regular"] == (
        validation["games"]["0022500001"]["event_count"]
    )
    assert sum(validation["period_counts"]["1997"].values()) == (
        validation["games"]["0029600001"]["event_count"]
    )
    assert set(validation["action_fields"]) == pbp_build.ACTION_SOURCE_KEYS
    for counts in validation["action_fields"].values():
        assert set(counts) == {"null", "blank"}
    assert validation["gates"]
    assert all(validation["gates"].values())

    manifest_text = (stage_dir / "_manifest.json").read_text(encoding="utf-8")
    reloaded = json.loads(manifest_text)
    assert (
        json.dumps(reloaded, sort_keys=True, indent=2, allow_nan=False) + "\n"
        == manifest_text
    )


def test_build_tree_rejects_sidecar_content_length_mismatch(tmp_path):
    data_dir = tmp_path / "data"
    raw_root = seed_two_season_publication(data_dir)
    sidecar = raw_root / "worklists/1997/regular.json.meta.json"
    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    metadata["content_length"] = metadata["content_length"] + 1
    sidecar.write_text(json.dumps(metadata), encoding="utf-8")
    stage_dir = tmp_path / "stage"
    stage_dir.mkdir()
    with pytest.raises(pbp_build.PBPBuildError, match="checksum/length"):
        pbp_build.build_tree(raw_root, stage_dir, seasons=[1997, 2026])


def test_build_tree_manifest_excludes_volatile_data(tmp_path):
    data_dir = tmp_path / "data"
    raw_root = seed_two_season_publication(data_dir)
    stage_dir = tmp_path / "stage"
    stage_dir.mkdir()
    pbp_build.build_tree(raw_root, stage_dir, seasons=[1997, 2026])
    text = (stage_dir / "_manifest.json").read_text(encoding="utf-8")

    assert str(tmp_path) not in text
    assert str(stage_dir) not in text
    assert str(raw_root) not in text
    assert socket.gethostname() not in text
    assert "fetched_at" not in text
    assert not re.search(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text
    )


# ---------------------------------------------------------------------------
# Step 3: whole-publication validation, all 14 mutations
# ---------------------------------------------------------------------------


def _build_valid_publication(tmp_path):
    data_dir = tmp_path / "data"
    raw_root = seed_two_season_publication(data_dir)
    stage_dir = tmp_path / "stage"
    stage_dir.mkdir()
    manifest = pbp_build.build_tree(raw_root, stage_dir, seasons=[1997, 2026])
    return raw_root, stage_dir, manifest


def test_validate_publication_accepts_valid_build(tmp_path):
    raw_root, stage_dir, manifest = _build_valid_publication(tmp_path)
    report = pbp_build.validate_publication(raw_root, stage_dir)
    assert report == json.loads(
        (stage_dir / "_manifest.json").read_text(encoding="utf-8")
    )["validation"]
    assert report == manifest["validation"]


def _mutate_missing_manifest(raw_root, stage_dir):
    (stage_dir / "_manifest.json").unlink()


def _mutate_additional_path(raw_root, stage_dir):
    (stage_dir / "extra.txt").write_text("x")


def _mutate_missing_season_partition(raw_root, stage_dir):
    shutil.rmtree(stage_dir / "season=1997")


def _mutate_wrong_schema(raw_root, stage_dir):
    path = _partition_path(stage_dir, 1997)
    table = pq.read_table(path)
    fields = [
        pa.field(f.name, f.type, nullable=True) if f.name == "game_id" else f
        for f in table.schema
    ]
    table = table.cast(pa.schema(fields))
    _rewrite_partition(path, table)


def _mutate_unsorted_keys(raw_root, stage_dir):
    path = _partition_path(stage_dir, 1997)
    table = pq.read_table(path)
    order = pa.array(list(reversed(range(table.num_rows))))
    _rewrite_partition(path, table.take(order))


def _mutate_duplicate_key(raw_root, stage_dir):
    path = _partition_path(stage_dir, 1997)
    table = pq.read_table(path)
    order = pa.array([0, *range(table.num_rows)])
    _rewrite_partition(path, table.take(order))


def _mutate_unknown_game(raw_root, stage_dir):
    path = _partition_path(stage_dir, 1997)
    table = pq.read_table(path)
    index = table.schema.get_field_index("game_id")
    values = pa.array(["9999999999"] * table.num_rows, type=pa.large_string())
    _rewrite_partition(path, table.set_column(index, table.schema.field(index), values))


def _mutate_wrong_season_partition(raw_root, stage_dir):
    path = _partition_path(stage_dir, 1997)
    table = pq.read_table(path)
    index = table.schema.get_field_index("game_id")
    values = pa.array(["0022500001"] * table.num_rows, type=pa.large_string())
    _rewrite_partition(path, table.set_column(index, table.schema.field(index), values))


def _mutate_event_count_manifest(raw_root, stage_dir):
    manifest = _load_manifest(stage_dir)
    manifest["validation"]["games"]["0029600001"]["event_count"] += 1
    _save_manifest(stage_dir, manifest)


def _mutate_terminal_score(raw_root, stage_dir):
    path = _partition_path(stage_dir, 1997)
    table = pq.read_table(path)
    index = table.schema.get_field_index("score_home")
    values = table.column("score_home").to_pylist()
    values[-1] = values[-1] + 1
    _rewrite_partition(
        path,
        table.set_column(
            index, table.schema.field(index), pa.array(values, type=pa.int64())
        ),
    )


def _mutate_partition_sha(raw_root, stage_dir):
    manifest = _load_manifest(stage_dir)
    manifest["partitions"]["1997"]["sha256"] = "0" * 64
    _save_manifest(stage_dir, manifest)


def _mutate_partition_rows(raw_root, stage_dir):
    manifest = _load_manifest(stage_dir)
    manifest["partitions"]["1997"]["rows"] += 1
    _save_manifest(stage_dir, manifest)


def _mutate_source_checksum(raw_root, stage_dir):
    manifest = _load_manifest(stage_dir)
    manifest["sources"]["worklists"][0]["body_sha256"] = "0" * 64
    _save_manifest(stage_dir, manifest)


def _mutate_validation_total(raw_root, stage_dir):
    manifest = _load_manifest(stage_dir)
    manifest["validation"]["worklist_counts"]["1997"]["regular"] += 1
    _save_manifest(stage_dir, manifest)


_MUTATIONS = {
    "missing manifest": _mutate_missing_manifest,
    "additional path": _mutate_additional_path,
    "missing season partition": _mutate_missing_season_partition,
    "wrong Arrow schema or nullability": _mutate_wrong_schema,
    "unsorted primary keys": _mutate_unsorted_keys,
    "duplicate (game_id, action_id)": _mutate_duplicate_key,
    "game absent from worklist": _mutate_unknown_game,
    "game in the wrong ending-season partition": _mutate_wrong_season_partition,
    "per-game event count mismatch": _mutate_event_count_manifest,
    "terminal score mismatch": _mutate_terminal_score,
    "partition byte checksum mismatch": _mutate_partition_sha,
    "partition row count mismatch": _mutate_partition_rows,
    "manifest source path/checksum mismatch": _mutate_source_checksum,
    "manifest validation total mismatch": _mutate_validation_total,
}


@pytest.mark.parametrize("name", sorted(_MUTATIONS))
def test_validate_publication_rejects_each_mutation(tmp_path, name):
    raw_root, stage_dir, _manifest = _build_valid_publication(tmp_path)
    _MUTATIONS[name](raw_root, stage_dir)
    with pytest.raises(pbp_build.PBPBuildError):
        pbp_build.validate_publication(raw_root, stage_dir)


# ---------------------------------------------------------------------------
# Step 4: anomaly summary (Tukey fences) and audit rendering
# ---------------------------------------------------------------------------


def test_event_count_distribution_reports_tukey_outliers_for_large_groups():
    games = [("g1", 10), ("g2", 11), ("g3", 12), ("g4", 13), ("g5", 100)]
    distribution = pbp_build._event_count_distribution(games)
    assert distribution["count"] == 5
    assert distribution["min"] == 10
    assert distribution["max"] == 100
    assert distribution["tukey_fences"] is not None
    assert distribution["outliers"] == ["g5"]
    assert distribution["smallest"] == [
        {"game_id": "g1", "event_count": 10},
        {"game_id": "g2", "event_count": 11},
        {"game_id": "g3", "event_count": 12},
    ]
    assert distribution["largest"][-1] == {"game_id": "g5", "event_count": 100}


def test_event_count_distribution_reports_no_outliers_for_small_groups():
    games = [("g1", 5), ("g2", 500)]
    distribution = pbp_build._event_count_distribution(games)
    assert distribution["tukey_fences"] is None
    assert distribution["outliers"] == []
    expected = [
        {"game_id": "g1", "event_count": 5},
        {"game_id": "g2", "event_count": 500},
    ]
    assert distribution["smallest"] == expected
    assert distribution["largest"] == expected


def test_event_count_distribution_matches_manual_tukey_fences():
    counts = [10, 12, 12, 13, 14, 15, 100]
    games = [(f"g{i}", count) for i, count in enumerate(counts)]
    distribution = pbp_build._event_count_distribution(games)
    q1, q3 = np.percentile(counts, [25, 75], method="linear")
    iqr = q3 - q1
    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr
    assert distribution["tukey_fences"] == {
        "lower": float(lower), "upper": float(upper),
    }
    assert distribution["outliers"] == ["g6"]


def _synthetic_manifest():
    spike_ids = list(pbp.SOURCE_SPIKE_GAME_IDS)
    games = {
        spike_ids[0]: {
            "season": 1997, "game_type": "regular", "event_count": 8,
            "max_period": 4, "terminal_home_score": 98,
            "terminal_away_score": 107, "early_zero_sentinels": 2,
            "blank_score_pairs": 0,
        },
        spike_ids[1]: {
            "season": 1997, "game_type": "playoff", "event_count": 6,
            "max_period": 4, "terminal_home_score": 100,
            "terminal_away_score": 90, "early_zero_sentinels": 0,
            "blank_score_pairs": 0,
        },
        spike_ids[2]: {
            "season": 2019, "game_type": "playoff", "event_count": 500,
            "max_period": 5, "terminal_home_score": 120,
            "terminal_away_score": 118, "early_zero_sentinels": 1,
            "blank_score_pairs": 2,
        },
        spike_ids[3]: {
            "season": 2026, "game_type": "regular", "event_count": 7,
            "max_period": 6, "terminal_home_score": 125,
            "terminal_away_score": 124, "early_zero_sentinels": 0,
            "blank_score_pairs": 1,
        },
    }
    sources_games = [
        {
            "body_path": (
                f"raw/stats_nba/pbp/games/{games[game_id]['season']}/"
                f"{game_id}.json"
            ),
            "body_sha256": format(0xA, "064x"),
            "body_content_length": 1000 + index,
            "sidecar_path": (
                f"raw/stats_nba/pbp/games/{games[game_id]['season']}/"
                f"{game_id}.json.meta.json"
            ),
            "sidecar_sha256": format(0xB, "064x"),
            "sidecar_content_length": 200,
        }
        for index, game_id in enumerate(spike_ids)
    ]
    schema_rows = [
        {
            "name": field.name, "arrow_type": str(field.type),
            "nullable": bool(field.nullable),
            "origin": (
                "derived" if field.name in pbp_build._DERIVED_EVENT_COLUMNS
                else "source"
            ),
        }
        for field in pbp_build.PBP_ARROW_SCHEMA
    ]
    seasons = sorted({record["season"] for record in games.values()})
    distributions = {}
    for season in seasons:
        by_type = {}
        for game_id, record in games.items():
            if record["season"] != season:
                continue
            by_type.setdefault(record["game_type"], []).append(
                (game_id, record["event_count"])
            )
        distributions[str(season)] = {
            game_type: pbp_build._event_count_distribution(pairs)
            for game_type, pairs in by_type.items()
        }
    worklist_counts = {}
    game_counts = {}
    event_counts = {}
    for season in seasons:
        applicable = pbp_build._applicable_game_types(season)
        season_games = {
            game_type: [
                gid for gid, record in games.items()
                if record["season"] == season and record["game_type"] == game_type
            ]
            for game_type in applicable
        }
        worklist_counts[str(season)] = {
            game_type: len(ids) for game_type, ids in season_games.items()
        }
        game_counts[str(season)] = dict(worklist_counts[str(season)])
        event_counts[str(season)] = {
            game_type: sum(games[gid]["event_count"] for gid in ids)
            for game_type, ids in season_games.items()
        }
    return {
        "schema_version": 1,
        "scope": {
            "ending_seasons": seasons,
            "game_types": ["play_in", "playoff", "regular"],
        },
        "schema": schema_rows,
        "sources": {"worklists": [], "games": sources_games},
        "versions": {
            "python": "3.12.3", "pandas": "2.2.0", "numpy": "1.26.0",
            "pyarrow": "16.0.0", "nba_api": "1.4.0",
        },
        "partitions": {
            str(season): {
                "path": f"season={season}/events.parquet",
                "rows": sum(
                    record["event_count"] for record in games.values()
                    if record["season"] == season
                ),
                "games": sum(
                    1 for record in games.values()
                    if record["season"] == season
                ),
                "bytes": 4096,
                "max_period": max(
                    record["max_period"] for record in games.values()
                    if record["season"] == season
                ),
                "sha256": format(0xC, "064x"),
                "row_sha256": format(0xD, "064x"),
            }
            for season in seasons
        },
        "validation": {
            "worklist_counts": worklist_counts,
            "game_counts": game_counts,
            "event_counts": event_counts,
            "period_counts": {str(season): {"1": 1} for season in seasons},
            "event_count_distributions": distributions,
            "action_fields": {
                key: {"null": 0, "blank": 0}
                for key in pbp_build.ACTION_SOURCE_KEYS
            },
            "games": games,
            "gates": {name: True for name in pbp_build._BUILD_GATES},
        },
    }


def _evidence_rows(manifest):
    spike_ids = list(pbp.SOURCE_SPIKE_GAME_IDS)
    games = manifest["validation"]["games"]
    sources_by_id = {
        Path(entry["body_path"]).stem: entry
        for entry in manifest["sources"]["games"]
    }
    return [
        {
            "game_id": game_id,
            "response_bytes": sources_by_id[game_id]["body_content_length"],
            "event_count": games[game_id]["event_count"],
            "max_period": games[game_id]["max_period"],
            "terminal_home_score": games[game_id]["terminal_home_score"],
            "terminal_away_score": games[game_id]["terminal_away_score"],
        }
        for game_id in spike_ids
    ]


def test_render_audit_success_contains_every_required_section():
    manifest = _synthetic_manifest()
    evidence = pbp_build.AuditEvidence(
        source_spike=_evidence_rows(manifest),
        cross_source_validated=True,
        reproducible_build=True,
        snapshot_checksums_verified=True,
        restore_verified=True,
    )
    text = pbp_build.render_audit(
        manifest, evidence,
        manifest_sha256="f" * 64,
        snapshot_name="pbp-archive-2026-abc123def456",
    )

    assert "1997-2026" in text
    assert "play_in, playoff, regular" in text
    assert "worklists:" in text and "games:" in text and "events:" in text
    assert "| 1997 |" in text and "| 2019 |" in text and "| 2026 |" in text
    assert "Source schema version: 1" in text
    for name, value in manifest["versions"].items():
        assert f"{name}: {value}" in text
    assert "no Tukey outliers" in text
    assert "null=" in text and "blank=" in text
    assert "Early-zero-sentinel total:" in text
    for game_id in pbp.SOURCE_SPIKE_GAME_IDS:
        assert game_id in text
    for name in pbp_build._BUILD_GATES:
        assert f"{name}: passed" in text
    for name in (
        "cross_source_validated", "reproducible_build",
        "snapshot_checksums_verified", "restore_verified",
    ):
        assert f"{name}: passed" in text
    assert f"Full manifest SHA-256: {'f' * 64}" in text
    assert "Immutable snapshot: pbp-archive-2026-abc123def456" in text
    assert "Restore verification: passed" in text


def test_render_audit_rejects_missing_spike_id():
    manifest = _synthetic_manifest()
    rows = _evidence_rows(manifest)[:-1]
    evidence = pbp_build.AuditEvidence(
        source_spike=rows,
        cross_source_validated=True, reproducible_build=True,
        snapshot_checksums_verified=True, restore_verified=True,
    )
    with pytest.raises(pbp_build.PBPBuildError, match="source-spike"):
        pbp_build.render_audit(
            manifest, evidence, manifest_sha256="f" * 64, snapshot_name="x"
        )


def test_render_audit_rejects_extra_spike_id():
    manifest = _synthetic_manifest()
    rows = _evidence_rows(manifest) + [{
        "game_id": "0000000000", "response_bytes": 1, "event_count": 1,
        "max_period": 1, "terminal_home_score": 0, "terminal_away_score": 0,
    }]
    evidence = pbp_build.AuditEvidence(
        source_spike=rows,
        cross_source_validated=True, reproducible_build=True,
        snapshot_checksums_verified=True, restore_verified=True,
    )
    with pytest.raises(pbp_build.PBPBuildError, match="source-spike"):
        pbp_build.render_audit(
            manifest, evidence, manifest_sha256="f" * 64, snapshot_name="x"
        )


def test_render_audit_rejects_mismatched_evidence_value():
    manifest = _synthetic_manifest()
    rows = _evidence_rows(manifest)
    rows[0] = {**rows[0], "event_count": rows[0]["event_count"] + 1}
    evidence = pbp_build.AuditEvidence(
        source_spike=rows,
        cross_source_validated=True, reproducible_build=True,
        snapshot_checksums_verified=True, restore_verified=True,
    )
    with pytest.raises(pbp_build.PBPBuildError, match="does not match"):
        pbp_build.render_audit(
            manifest, evidence, manifest_sha256="f" * 64, snapshot_name="x"
        )


@pytest.mark.parametrize(
    "field",
    [
        "cross_source_validated", "reproducible_build",
        "snapshot_checksums_verified", "restore_verified",
    ],
)
def test_render_audit_rejects_any_false_evidence_flag(field):
    manifest = _synthetic_manifest()
    kwargs = dict(
        cross_source_validated=True, reproducible_build=True,
        snapshot_checksums_verified=True, restore_verified=True,
    )
    kwargs[field] = False
    evidence = pbp_build.AuditEvidence(
        source_spike=_evidence_rows(manifest), **kwargs
    )
    with pytest.raises(pbp_build.PBPBuildError, match="unverified"):
        pbp_build.render_audit(
            manifest, evidence, manifest_sha256="f" * 64, snapshot_name="x"
        )


# ---------------------------------------------------------------------------
# ``run`` (production entry point, exercised against a monkeypatched scope)
# ---------------------------------------------------------------------------


def test_run_builds_validates_and_publishes(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    seed_two_season_publication(data_dir)
    monkeypatch.setattr(pbp_build.pbp, "SEASONS", (1997, 2026))

    pbp_build.run(data_dir)

    output_dir = data_dir / "parquet-large/pbp"
    assert (output_dir / "_manifest.json").exists()
    assert (output_dir / "season=1997/events.parquet").exists()
    assert (output_dir / "season=2026/events.parquet").exists()
    leftovers = [
        p for p in (data_dir / "parquet-large").iterdir() if p.name != "pbp"
    ]
    assert leftovers == []


def test_run_cleans_up_temp_stage_on_build_failure(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    seed_two_season_publication(data_dir)
    (data_dir / "raw/stats_nba/pbp/games/1997/0029600001.json").write_bytes(
        b"not the original bytes"
    )
    monkeypatch.setattr(pbp_build.pbp, "SEASONS", (1997, 2026))

    with pytest.raises(Exception):
        pbp_build.run(data_dir)

    assert not (data_dir / "parquet-large/pbp").exists()
    staging_root = data_dir / "parquet-large"
    if staging_root.exists():
        assert list(staging_root.iterdir()) == []
