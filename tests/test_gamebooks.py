"""Tests for the gamebook import and roster-sample comparison (plan Task 16).

The gamebook host does not answer this machine, so the sample PDFs are
downloaded by the owner in a browser and imported offline: the import checks
the ``%PDF`` signature, derives the ``game_id`` from the file name's date and
tricodes against the Part A worklist keys, and publishes an attested pair
with a sidecar that records the URL, the retrieval date and
``retrieved_by: browser``. ``compare_rosters`` takes the two teams' names as
the gamebook prints them (the extractor is TODO(task-21)) and compares them
with ``game_rosters`` per team and list; ``write_comparison`` publishes the
record as a bound pair; ``select_gamebook_sample`` is deterministic.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from nba_data import cache, gamebooks


OPENER_ID, FINALS_ID = "0022500001", "0042400401"
OKC, HOU, IND = 1610612760, 1610612745, 1610612754
GAME_KEYS = {
    (date(2025, 10, 21), "HOU", "OKC"): OPENER_ID,
    (date(2025, 6, 5), "IND", "OKC"): FINALS_ID,
}
PDF = b"%PDF-1.7\n% a browser-downloaded gamebook\n"
RETRIEVED = date(2026, 11, 20)


def _sidecar(path: Path) -> dict:
    return json.loads(path.with_name(path.name + ".meta.json").read_text(encoding="utf-8"))


def _roster_row(game_id, team_id, tricode, status, first, family, person_id):
    return {
        "game_id": game_id, "person_id": person_id, "season": 2026, "game_type": "regular",
        "game_date": date(2025, 10, 21), "nba_team_id": team_id, "team_tricode_raw": tricode,
        "franchise_id": tricode, "is_home": tricode == "OKC", "roster_status": status,
        "first_name_raw": first, "family_name_raw": family, "jersey_num": None,
        "starter_position_raw": None, "comment_raw": None, "comment_code": None, "minutes_raw": None,
    }


def opener_rosters() -> pd.DataFrame:
    rows = [
        _roster_row(OPENER_ID, OKC, "OKC", "played", "Shai", "Gilgeous-Alexander", 1628983),
        _roster_row(OPENER_ID, OKC, "OKC", "active_not_played", "Ousmane", "Dieng", 1631096),
        _roster_row(OPENER_ID, OKC, "OKC", "inactive", "Nikola", "Topić", 1642260),
        _roster_row(OPENER_ID, HOU, "HOU", "played", "Alperen", "Şengün", 1630578),
        _roster_row(OPENER_ID, HOU, "HOU", "played", "Jabari", "Smith Jr.", 1631095),
        _roster_row(OPENER_ID, HOU, "HOU", "inactive", "Fred", "VanVleet", 1627832),
        # another game: never mixed into the opener's comparison
        _roster_row(FINALS_ID, IND, "IND", "played", "Tyrese", "Haliburton", 1630169),
    ]
    return pd.DataFrame.from_records(rows)


def test_gamebook_url_from_game_code():
    """Would fail if the URL did not repeat the date in the file name or accepted a malformed code."""
    assert gamebooks.gamebook_url("20250605/INDOKC") == (
        "https://statsdmz.nba.com/pdfs/20250605/20250605_INDOKC_book.pdf"
    )
    assert gamebooks.gamebook_url("20251021/HOUOKC") == (
        "https://statsdmz.nba.com/pdfs/20251021/20251021_HOUOKC_book.pdf"
    )
    for bad in ("20250605/INDOKC ", "2025065/INDOKC", "20250605-INDOKC", "20250605/indokc", "", None):
        with pytest.raises(gamebooks.GamebookError, match="game code"):
            gamebooks.gamebook_url(bad)
    assert gamebooks.FILE_NAME_PATTERN.match("20250605_INDOKC_book.pdf").groups() == (
        "20250605", "IND", "OKC",
    )
    assert gamebooks.FILE_NAME_PATTERN.match("20250605_INDOKC_book.PDF") is None
    assert gamebooks.RAW_SUBDIR == "raw/nba_gamebooks"


def test_import_gamebooks_derives_game_id_checks_signature_and_writes_sidecars(tmp_path):
    """Would fail if a non-PDF or an unknown key were written, or the sidecar lacked its provenance."""
    data = tmp_path / "data"
    source = tmp_path / "downloads"
    source.mkdir()
    (source / "20251021_HOUOKC_book.pdf").write_bytes(PDF)
    (source / "20250605_INDOKC_book.pdf").write_bytes(PDF + b"finals\n")

    written = gamebooks.import_gamebooks(data, source, game_keys=GAME_KEYS, retrieved_on=RETRIEVED)

    opener = data / "raw/nba_gamebooks" / f"{OPENER_ID}.book.pdf"
    finals = data / "raw/nba_gamebooks" / f"{FINALS_ID}.book.pdf"
    assert written == (finals, opener)
    assert opener.read_bytes() == PDF and finals.read_bytes() == PDF + b"finals\n"
    sidecar = _sidecar(opener)
    assert {key: sidecar[key] for key in (
        "provider", "resource", "game_id", "game_code", "url", "retrieved_on", "retrieved_by",
    )} == {
        "provider": "nba_official", "resource": "gamebook", "game_id": OPENER_ID,
        "game_code": "20251021/HOUOKC",
        "url": "https://statsdmz.nba.com/pdfs/20251021/20251021_HOUOKC_book.pdf",
        "retrieved_on": "2026-11-20", "retrieved_by": "browser",
    }
    assert sidecar["sha256"] == hashlib.sha256(PDF).hexdigest()
    assert sidecar["content_length"] == len(PDF)
    assert _sidecar(finals)["game_code"] == "20250605/INDOKC"
    # the pair re-attests through the gamebook validator with its identity
    payload, metadata = gamebooks.read_cached_gamebook(data, OPENER_ID)
    assert payload == PDF and metadata == sidecar
    assert gamebooks.read_cached_gamebook(data, "0022500002") is None
    # a re-import of the identical file keeps the pair; a different body for the same game is refused
    assert gamebooks.import_gamebooks(data, source, game_keys=GAME_KEYS, retrieved_on=RETRIEVED) == (finals, opener)
    (source / "20251021_HOUOKC_book.pdf").write_bytes(PDF + b"changed\n")
    with pytest.raises(gamebooks.GamebookError, match="already"):
        gamebooks.import_gamebooks(data, source, game_keys=GAME_KEYS, retrieved_on=RETRIEVED)
    assert opener.read_bytes() == PDF

    # a non-PDF: the whole import fails before anything is written
    other = tmp_path / "other"
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "20251021_HOUOKC_book.pdf").write_bytes(b"<html>Access Denied</html>")
    (bad / "20250605_INDOKC_book.pdf").write_bytes(PDF)
    with pytest.raises(gamebooks.GamebookError, match="%PDF"):
        gamebooks.import_gamebooks(other, bad, game_keys=GAME_KEYS, retrieved_on=RETRIEVED)
    assert not other.exists()
    # an unknown key (swapped tricodes), a date outside the worklist, a name outside the pattern
    for name in ("20251021_OKCHOU_book.pdf", "20251022_HOUOKC_book.pdf", "HOUOKC_book.pdf", "notes.txt"):
        unknown = tmp_path / ("unknown-" + name)
        unknown.mkdir()
        (unknown / "20250605_INDOKC_book.pdf").write_bytes(PDF)
        (unknown / name).write_bytes(PDF)
        with pytest.raises(gamebooks.GamebookError, match=name):
            gamebooks.import_gamebooks(other, unknown, game_keys=GAME_KEYS, retrieved_on=RETRIEVED)
        assert not other.exists()
    # an empty or missing source directory imports nothing, loudly
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(gamebooks.GamebookError, match="no gamebook"):
        gamebooks.import_gamebooks(other, empty, game_keys=GAME_KEYS, retrieved_on=RETRIEVED)
    with pytest.raises(gamebooks.GamebookError, match="directory"):
        gamebooks.import_gamebooks(other, tmp_path / "missing", game_keys=GAME_KEYS, retrieved_on=RETRIEVED)
    with pytest.raises(gamebooks.GamebookError, match="retrieved_on"):
        gamebooks.import_gamebooks(other, source, game_keys=GAME_KEYS, retrieved_on=datetime(2026, 11, 20))
    assert not other.exists()


def test_compare_rosters_reports_differences_per_team_and_list():
    """Would fail if differences were pooled across teams or lists, or a printed name were rewritten."""
    rosters = opener_rosters()
    # TODO(task-21): ``extract_box_score_names`` will produce this mapping from
    # the gamebook's box-score page; ``compare_rosters`` takes the names
    # exactly as printed and keeps them as printed in the record.
    names = {
        "OKC": {
            "active": ["Shai Gilgeous-Alexander", "Ajay Mitchell"],
            "inactive": ["Nikola Topic"],
        },
        "HOU": {
            "active": ["Alperen Sengun", "Jabari Smith Jr."],
            "inactive": ["Fred VanVleet", "Dorian Finney-Smith"],
        },
    }

    record = gamebooks.compare_rosters(OPENER_ID, names, rosters)

    assert set(record) == {"game_id", "teams", "differences", "reviewer_note"}
    assert record["game_id"] == OPENER_ID and record["reviewer_note"] is None
    assert list(record["teams"]) == ["HOU", "OKC"]
    okc = record["teams"]["OKC"]
    assert set(okc) == {
        "gamebook_active", "gamebook_inactive", "roster_active", "roster_inactive",
        "only_in_gamebook", "only_in_roster",
    }
    assert okc["gamebook_active"] == ["Shai Gilgeous-Alexander", "Ajay Mitchell"]
    assert okc["gamebook_inactive"] == ["Nikola Topic"]
    assert okc["roster_active"] == ["Ousmane Dieng", "Shai Gilgeous-Alexander"]
    assert okc["roster_inactive"] == ["Nikola Topić"]
    # diacritics never count as a difference; a missing or extra name does, per list
    assert okc["only_in_gamebook"] == {"active": ["Ajay Mitchell"], "inactive": []}
    assert okc["only_in_roster"] == {"active": ["Ousmane Dieng"], "inactive": []}
    hou = record["teams"]["HOU"]
    assert hou["only_in_gamebook"] == {"active": [], "inactive": ["Dorian Finney-Smith"]}
    assert hou["only_in_roster"] == {"active": [], "inactive": []}
    assert record["differences"] == 3
    # an exact match is zero differences; a game absent from the rosters or a team mismatch fails
    exact = {
        "OKC": {"active": ["Shai Gilgeous-Alexander", "Ousmane Dieng"], "inactive": ["Nikola Topić"]},
        "HOU": {"active": ["Alperen Şengün", "Jabari Smith Jr."], "inactive": ["Fred VanVleet"]},
    }
    assert gamebooks.compare_rosters(OPENER_ID, exact, rosters)["differences"] == 0
    with pytest.raises(gamebooks.GamebookError, match="0022500009"):
        gamebooks.compare_rosters("0022500009", exact, rosters)
    with pytest.raises(gamebooks.GamebookError, match="tricode"):
        gamebooks.compare_rosters(OPENER_ID, {"OKC": exact["OKC"], "IND": exact["HOU"]}, rosters)
    with pytest.raises(gamebooks.GamebookError, match="active"):
        gamebooks.compare_rosters(OPENER_ID, {"OKC": {"active": []}, "HOU": exact["HOU"]}, rosters)


def test_write_comparison_publishes_a_bound_record(tmp_path):
    """Would fail if the record were written without a sidecar, reordered, or accepted malformed."""
    data = tmp_path / "data"
    names = {
        "OKC": {"active": ["Shai Gilgeous-Alexander"], "inactive": ["Nikola Topić"]},
        "HOU": {"active": ["Alperen Şengün", "Jabari Smith Jr."], "inactive": ["Fred VanVleet"]},
    }
    record = gamebooks.compare_rosters(OPENER_ID, names, opener_rosters())
    record["reviewer_note"] = "Dieng is a two-way player the gamebook lists on the second page."

    path = gamebooks.write_comparison(data, record)

    assert path == data / "raw/nba_gamebooks/comparisons" / f"{OPENER_ID}.json"
    body = path.read_bytes()
    assert body == (json.dumps(record, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    assert json.loads(body.decode("utf-8")) == record
    sidecar = _sidecar(path)
    assert {key: sidecar[key] for key in ("provider", "resource", "game_id")} == {
        "provider": "nba_official", "resource": "gamebook_comparison", "game_id": OPENER_ID,
    }
    assert sidecar["sha256"] == hashlib.sha256(body).hexdigest()
    assert gamebooks.read_cached_comparison(data, OPENER_ID) == (record, sidecar)
    assert gamebooks.read_cached_comparison(data, FINALS_ID) is None
    # the validators the build and the snapshot layout check use
    gamebooks.validate_comparison(path)
    broken = tmp_path / "broken.json"
    for payload in (b"[]\n", b"not json", b"\xff\xfe"):
        broken.write_bytes(payload)
        with pytest.raises(gamebooks.GamebookError):
            gamebooks.validate_comparison(broken)
    pdf = tmp_path / "x.book.pdf"
    pdf.write_bytes(PDF)
    gamebooks.validate_pdf(pdf)
    pdf.write_bytes(b"<html>")
    with pytest.raises(gamebooks.GamebookError, match="%PDF"):
        gamebooks.validate_pdf(pdf)
    # a malformed record is refused before anything is written
    other = tmp_path / "other"
    for mutate in (
        lambda doc: doc.pop("differences"),
        lambda doc: doc.__setitem__("differences", 7),
        lambda doc: doc.__setitem__("reviewer_note", ""),
        lambda doc: doc.__setitem__("extra", 1),
        lambda doc: doc["teams"]["OKC"].pop("only_in_roster"),
        lambda doc: doc.__setitem__("game_id", "22500001"),
    ):
        document = json.loads(json.dumps(record))
        mutate(document)
        with pytest.raises(gamebooks.GamebookError):
            gamebooks.write_comparison(other, document)
    assert not other.exists()
    # a tampered pair fails attestation
    path.write_bytes(body + b"\n")
    with pytest.raises(cache.CacheIntegrityError):
        gamebooks.read_cached_comparison(data, OPENER_ID)


def _schedule_row(game_id, season, game_type, game_date, matchup="HOUOKC"):
    return {
        "game_id": game_id, "season": season, "game_type": game_type, "game_date": game_date,
        "scheduled_start_utc": pd.Timestamp(datetime(game_date.year, game_date.month, game_date.day, 23), tz="UTC"),
        "scheduled_start_et_raw": f"{game_date.isoformat()}T19:00:00Z",
        "game_code_raw": f"{game_date:%Y%m%d}/{matchup}",
    }


def test_select_gamebook_sample_is_deterministic_and_covers_the_required_cases():
    """Would fail if the sample depended on input order or skipped a required case."""
    schedule = pd.DataFrame.from_records([
        _schedule_row("0021900002", 2020, "regular", date(2019, 10, 22)),
        _schedule_row("0021900001", 2020, "regular", date(2019, 10, 22)),
        _schedule_row("0021901231", 2020, "regular", date(2020, 7, 30)),     # earliest bubble game
        _schedule_row("0021901232", 2020, "regular", date(2020, 7, 30)),
        _schedule_row("0041900401", 2020, "playoff", date(2020, 9, 30)),
        _schedule_row("0041900406", 2020, "playoff", date(2020, 10, 11)),   # latest playoff game
        _schedule_row("0022000001", 2021, "regular", date(2020, 12, 22)),
        _schedule_row("0052000101", 2021, "play_in", date(2021, 5, 18)),     # never a case
        _schedule_row("0042000401", 2021, "playoff", date(2021, 7, 6)),
        _schedule_row("0022100001", 2022, "regular", date(2021, 10, 19)),   # no playoff game seeded
    ])
    roster_counts = pd.DataFrame.from_records([
        {"game_id": "0021900001", "nba_team_id": HOU, "active": 13, "inactive": 2},
        {"game_id": "0021900001", "nba_team_id": OKC, "active": 13, "inactive": 0},   # already sampled
        {"game_id": "0021900002", "nba_team_id": IND, "active": 12, "inactive": 0},   # the next game is taken
        {"game_id": "0022000001", "nba_team_id": OKC, "active": 13, "inactive": 0},
    ])

    sample = gamebooks.select_gamebook_sample(schedule, roster_counts)

    def entry(case, season, game_id, game_date, team=None):
        return {
            "case": case, "season": season, "game_id": game_id,
            "game_date": None if game_date is None else game_date.isoformat(),
            "game_code_raw": None if game_date is None else f"{game_date:%Y%m%d}/HOUOKC",
            "nba_team_id": team,
        }

    assert sample == [
        entry("earliest_regular", 2020, "0021900001", date(2019, 10, 22)),
        entry("latest_playoff", 2020, "0041900406", date(2020, 10, 11)),
        entry("earliest_regular", 2021, "0022000001", date(2020, 12, 22)),
        entry("latest_playoff", 2021, "0042000401", date(2021, 7, 6)),
        entry("earliest_regular", 2022, "0022100001", date(2021, 10, 19)),
        entry("latest_playoff", 2022, None, None),
        entry("empty_inactive_list", 2020, "0021900002", date(2019, 10, 22), IND),
        entry("bubble_2020", 2020, "0021901231", date(2020, 7, 30)),
        entry("two_way", None, None, None),
        entry("trade_within_two_days", None, None, None),
        entry("season_long_absence", None, None, None),
    ]
    # input order never matters
    shuffled = schedule.sample(frac=1, random_state=7).reset_index(drop=True)
    counts_shuffled = roster_counts.iloc[::-1].reset_index(drop=True)
    assert gamebooks.select_gamebook_sample(shuffled, counts_shuffled) == sample
    # JSON-ready and URL-addressable
    json.dumps(sample, sort_keys=True)
    assert gamebooks.gamebook_url(sample[0]["game_code_raw"]).endswith("20191022_HOUOKC_book.pdf")
    # a season without a regular-season game or a tree without a bubble game says so
    without_bubble = schedule[schedule["game_date"] < date(2020, 7, 30)]
    trimmed = gamebooks.select_gamebook_sample(without_bubble, roster_counts.iloc[:0])
    assert [e for e in trimmed if e["case"] == "bubble_2020"] == [entry("bubble_2020", 2020, None, None)]
    assert [e for e in trimmed if e["case"] == "empty_inactive_list"] == [
        entry("empty_inactive_list", None, None, None),
    ]
    with pytest.raises(gamebooks.GamebookError, match="game_code_raw"):
        gamebooks.select_gamebook_sample(schedule.drop(columns=["game_code_raw"]), roster_counts)


def test_validate_comparison_rejects_a_hand_edited_record_that_still_hashes(tmp_path):
    """Would fail if a comparison record with wrong keys or a differences count
    disagreeing with its lists could be bound once its sidecar is re-signed."""
    data = tmp_path / "data"
    names = {
        "OKC": {"active": ["Shai Gilgeous-Alexander"], "inactive": ["Nikola Topić"]},
        "HOU": {"active": ["Alperen Şengün", "Jabari Smith Jr."], "inactive": ["Fred VanVleet"]},
    }
    record = gamebooks.compare_rosters(OPENER_ID, names, opener_rosters())
    record["reviewer_note"] = "Dieng is a two-way player the gamebook lists on the second page."
    path = gamebooks.write_comparison(data, record)
    sidecar_path = path.with_name(path.name + ".meta.json")
    body, sidecar = path.read_bytes(), _sidecar(path)
    assert record["differences"] > 0

    def resign(document: dict) -> None:
        edited = gamebooks.comparison_bytes(document)
        path.write_bytes(edited)
        sidecar_path.write_text(json.dumps({
            **sidecar, "sha256": hashlib.sha256(edited).hexdigest(), "content_length": len(edited),
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    for mutate, match in (
        (lambda doc: doc.__setitem__("differences", 0), "differences"),
        (lambda doc: doc.__setitem__("differences", "6"), "differences"),
        (lambda doc: doc.__setitem__("extra", 1), "exactly the keys"),
        (lambda doc: doc.pop("reviewer_note"), "exactly the keys"),
        (lambda doc: doc["teams"]["OKC"].pop("only_in_roster"), "exactly the keys"),
        (lambda doc: doc["teams"]["OKC"]["only_in_roster"].pop("inactive"), "exactly the keys"),
        (lambda doc: doc.__setitem__("game_id", "22500001"), "game_id"),
        (lambda doc: doc.__setitem__("teams", {}), "teams"),
        (lambda doc: doc.__setitem__("reviewer_note", " "), "reviewer_note"),
    ):
        document = json.loads(json.dumps(record))
        mutate(document)
        resign(document)
        with pytest.raises(gamebooks.GamebookError, match=match):
            gamebooks.validate_comparison(path)
        with pytest.raises(gamebooks.GamebookError, match=match):
            gamebooks.read_cached_comparison(data, OPENER_ID)
    # the error names the record it refused
    with pytest.raises(gamebooks.GamebookError, match=re.escape(str(path))):
        gamebooks.validate_comparison(path)
    # restored byte for byte, the pair binds again
    path.write_bytes(body)
    sidecar_path.write_text(json.dumps(sidecar, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    gamebooks.validate_comparison(path)
    assert gamebooks.read_cached_comparison(data, OPENER_ID) == (record, sidecar)
