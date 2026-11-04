"""Tests for the availability build: the exception ledger (Task 13), table
assembly, Arrow schemas and partitions (Task 14), and the gates, derivative
agreement, manifest, independent validation and publication (Task 15).

The ledger is validated like the D1 ledgers in ``player_games_build``:
duplicate members, extra or missing sections and fields, type drift,
malformed composite keys and stale or unlisted entries all fail closed.

The assembly tests seed a tiny raw tree under ``tmp_path`` with the
module-level seed builders below: three box-roster games (the two committed
fixture games plus a hand-authored ``CHI@CHA`` game the 2024-01-31 fixture
report lists), a ``player_games`` publication for them, the two fixture PDFs
under their own months with one inventory generation each, and the
derivative assets for 2024 and 2026.

The Task 15 tests extend that seed to the whole publication scope
(``seed_full_tree``): one synthetic ``CHI@CHA`` game per season 2012-2023
beside the three seeded games, and one present report per version season
2019-2026. Seasons 2019, 2021, 2024 and 2026 use a fixture PDF at its own
slot; seasons 2020, 2022, 2023 and 2025 have no fixture, so the one-page
2019-02-15 fixture is published under a slot of each of those seasons. Its
printed title then lies outside the slot window, exactly as the real
fetcher's sidecar (``pdf_metadata``) and the build's title gate record it,
and the test ledger approves each with an ``accept_title`` entry. That keeps
``validate_publication`` honest: every body is the real fixture, every gate
runs for real, and the approvals are bound in the manifest like any other
ledger entry, instead of a monkeypatched parser inventing printed times.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import io
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import test_box_rosters as box_tests
import test_injury_derivative as derivative_tests
from nba_data import (
    availability_build,
    availability_resolve,
    box_rosters,
    cache,
    injury_derivative,
    injury_report_parse,
    injury_reports,
    publication,
)


SECTIONS = (
    "appearance_consistency", "report_versions", "schedule_dates", "team_resolution",
)
SHA = "e39fe95bff31019494b82c9ec019919e94d8f10494395bce643d2c1ba02be17c"
REPORT = "Injury-Report_2024-01-31_05PM"
QUARTER_REPORT = "Injury-Report_2025-12-22_05_30PM"


def _ledger(**sections):
    document = {section: {} for section in SECTIONS}
    document["schema_version"] = 1
    document.update(sections)
    return document


def _appearance():
    return {
        "comment_raw": "DNP - Coach's Decision",
        "minutes_raw": None,
        "appearance": "credited",
        "note": "Credited by the league after the box score was corrected.",
    }


def _exclude():
    return {
        "action": "exclude",
        "sha256": SHA,
        "failed_check": "header",
        "title_printed_et": None,
        "slot_label": "05PM",
        "note": "Body opens but carries no listing header.",
    }


def _accept_title():
    return {
        "action": "accept_title",
        "sha256": SHA,
        "failed_check": None,
        "title_printed_et": "2024-01-31T19:30:00",
        "slot_label": "05PM",
        "note": "Printed two hours after the slot; listings verified.",
    }


def _team():
    return {
        "team_raw": "LA Clippers",
        "nba_team_id": 1610612746,
        "note": "Report spells the city differently from the box score.",
    }


def _schedule():
    return {
        "game_et_raw": "2024-01-31T19:30:00",
        "game_code_raw": "20240201/BOSLAL",
        "game_date": "2024-01-31",
        "note": "Late tip crossed midnight Eastern in the game code.",
    }


def _without(entry, *fields):
    return {key: value for key, value in entry.items() if key not in fields}


def _save(path, payload):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    path.write_text(text, encoding="utf-8")


def test_packaged_ledger_is_exact_canonical_json_with_four_empty_sections():
    """Would fail if the committed ledger were pre-populated or non-canonical."""
    expected = {
        "appearance_consistency": {},
        "report_versions": {},
        "schedule_dates": {},
        "schema_version": 1,
        "team_resolution": {},
    }
    assert availability_build.EXCEPTIONS_PATH.read_bytes() == (
        json.dumps(expected, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    assert availability_build.load_exceptions() == expected
    assert availability_build.load_exceptions(availability_build.EXCEPTIONS_PATH) == expected


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        # document shape
        ("[]", "root must be an object"),
        ("{", "invalid JSON"),
        ('{"appearance_consistency": NaN}', "invalid JSON"),
        (_without(_ledger(), "team_resolution"), "top-level keys"),
        ({**_ledger(), "gamebooks": {}}, "top-level keys"),
        ({**_ledger(), "schema_version": 2}, "schema_version"),
        ({**_ledger(), "schema_version": "1"}, "schema_version"),
        ({**_ledger(), "schema_version": True}, "schema_version"),
        (_ledger(report_versions=[]), "report_versions must be an object"),
        (
            '{"appearance_consistency": {}, "report_versions": {}, "schedule_dates": {},'
            ' "schema_version": 1, "team_resolution": {'
            f'"{REPORT}|3": {json.dumps(_team())}, "{REPORT}|3": {json.dumps(_team())}'
            "}}",
            "duplicate semantic identity",
        ),
        # appearance_consistency
        (
            _ledger(appearance_consistency={"2024|0022300001": _appearance()}),
            "malformed composite key",
        ),
        (
            _ledger(appearance_consistency={"24|0022300001|1629029": _appearance()}),
            "malformed composite key",
        ),
        (
            _ledger(appearance_consistency={"2024|22300001|1629029": _appearance()}),
            "malformed composite key",
        ),
        (
            _ledger(appearance_consistency={"2024|0022300001|01629029": _appearance()}),
            "malformed composite key",
        ),
        (
            _ledger(appearance_consistency={"2024|0022300001|1629029": ["x"]}),
            "must be an object",
        ),
        (
            _ledger(appearance_consistency={
                "2024|0022300001|1629029": _without(_appearance(), "minutes_raw"),
            }),
            "invalid fields",
        ),
        (
            _ledger(appearance_consistency={
                "2024|0022300001|1629029": {**_appearance(), "season": 2024},
            }),
            "invalid fields",
        ),
        (
            _ledger(appearance_consistency={
                "2024|0022300001|1629029": {**_appearance(), "appearance": "played"},
            }),
            "appearance",
        ),
        (
            _ledger(appearance_consistency={
                "2024|0022300001|1629029": {**_appearance(), "comment_raw": 7},
            }),
            "wrong type",
        ),
        (
            _ledger(appearance_consistency={
                "2024|0022300001|1629029": {**_appearance(), "note": ""},
            }),
            "non-empty note",
        ),
        (
            _ledger(appearance_consistency={
                "2024|0022300001|1629029": {**_appearance(), "note": "   "},
            }),
            "non-empty note",
        ),
        (
            _ledger(appearance_consistency={
                "2024|0022300001|1629029": {**_appearance(), "note": None},
            }),
            "wrong type",
        ),
        # report_versions
        (_ledger(report_versions={"Injury-Report_2024-01-31": _exclude()}), "malformed composite key"),
        (_ledger(report_versions={"Injury-Report_2024-01-31_05_00PM": _exclude()}), "malformed composite key"),
        (_ledger(report_versions={REPORT: {**_exclude(), "action": "ignore"}}), "action"),
        (_ledger(report_versions={REPORT: {**_exclude(), "failed_check": None}}), "failed_check"),
        (_ledger(report_versions={REPORT: {**_exclude(), "failed_check": " "}}), "failed_check"),
        (
            _ledger(report_versions={REPORT: {**_accept_title(), "failed_check": "title"}}),
            "failed_check",
        ),
        (_ledger(report_versions={REPORT: {**_exclude(), "sha256": SHA[:63]}}), "sha256"),
        (_ledger(report_versions={REPORT: {**_exclude(), "sha256": SHA.upper()}}), "sha256"),
        (_ledger(report_versions={REPORT: {**_exclude(), "sha256": None}}), "wrong type"),
        (_ledger(report_versions={REPORT: {**_exclude(), "slot_label": "05_00PM"}}), "slot_label"),
        (
            _ledger(report_versions={QUARTER_REPORT: {**_exclude(), "slot_label": "05PM"}}),
            "slot_label",
        ),
        (
            _ledger(report_versions={REPORT: {**_accept_title(), "title_printed_et": "01/31/24 07:30 PM"}}),
            "title_printed_et",
        ),
        (
            _ledger(report_versions={REPORT: {**_accept_title(), "title_printed_et": "2024-01-31T19:30:00+00:00"}}),
            "title_printed_et",
        ),
        (
            _ledger(report_versions={REPORT: {**_accept_title(), "title_printed_et": 1706736600}}),
            "wrong type",
        ),
        (_ledger(report_versions={REPORT: _without(_exclude(), "slot_label")}), "invalid fields"),
        # team_resolution
        (_ledger(team_resolution={f"{REPORT}|3": {**_team(), "nba_team_id": "1610612746"}}), "wrong type"),
        (_ledger(team_resolution={f"{REPORT}|3": {**_team(), "nba_team_id": True}}), "wrong type"),
        (_ledger(team_resolution={f"{REPORT}|3": {**_team(), "nba_team_id": None}}), "wrong type"),
        (_ledger(team_resolution={f"{REPORT}|3": {**_team(), "team_raw": None}}), "wrong type"),
        (_ledger(team_resolution={f"{REPORT}|0": _team()}), "malformed composite key"),
        (_ledger(team_resolution={f"{REPORT}|03": _team()}), "malformed composite key"),
        (_ledger(team_resolution={f"{REPORT}|-3": _team()}), "malformed composite key"),
        (_ledger(team_resolution={REPORT: _team()}), "malformed composite key"),
        (_ledger(team_resolution={"Injury-Report_2024-01-31_13PM|3": _team()}), "malformed composite key"),
        (_ledger(team_resolution={f"{REPORT}|3": {**_team(), "franchise_id": "LAC"}}), "invalid fields"),
        # schedule_dates
        (_ledger(schedule_dates={"0022300001": {**_schedule(), "game_date": "01/31/2024"}}), "game_date"),
        (_ledger(schedule_dates={"0022300001": {**_schedule(), "game_date": "20240131"}}), "game_date"),
        (_ledger(schedule_dates={"0022300001": {**_schedule(), "game_date": "2024-02-30"}}), "game_date"),
        (_ledger(schedule_dates={"0022300001": {**_schedule(), "game_date": None}}), "wrong type"),
        (_ledger(schedule_dates={"0022300001": {**_schedule(), "game_code_raw": "2024-02-01/BOSLAL"}}), "game_code_raw"),
        (_ledger(schedule_dates={"0022300001": {**_schedule(), "game_et_raw": 20240131}}), "wrong type"),
        (_ledger(schedule_dates={"22300001": _schedule()}), "malformed composite key"),
        (_ledger(schedule_dates={"0022300001|BOS": _schedule()}), "malformed composite key"),
    ],
)
def test_load_exceptions_rejects_schema_drift(tmp_path, payload, match):
    """Would fail if a malformed ledger reached the build gates."""
    path = tmp_path / "availability_exceptions.json"
    _save(path, payload)

    with pytest.raises(availability_build.AvailabilityBuildError, match=match):
        availability_build.load_exceptions(path)


def test_load_exceptions_accepts_one_valid_entry_per_section(tmp_path):
    """Would fail if the grammar rejected the exact shapes the table contracts."""
    document = _ledger(
        appearance_consistency={"2024|0022300001|1629029": _appearance()},
        report_versions={REPORT: _exclude(), QUARTER_REPORT: {**_accept_title(), "slot_label": "05_30PM"}},
        team_resolution={f"{REPORT}|3": _team()},
        schedule_dates={"0022300001": _schedule()},
    )
    path = tmp_path / "availability_exceptions.json"
    _save(path, document)

    assert availability_build.load_exceptions(path) == document


def test_validate_exceptions_requires_exact_two_way_equality_ignoring_note():
    """Would fail if an unlisted, stale or drifted disagreement passed the gate."""
    observed = {
        "appearance_consistency": {"2024|0022300001|1629029": _without(_appearance(), "note")},
        "report_versions": {REPORT: _without(_exclude(), "note")},
        "team_resolution": {f"{REPORT}|3": _without(_team(), "note")},
        "schedule_dates": {"0022300001": _without(_schedule(), "note")},
    }
    approved = _ledger(
        appearance_consistency={"2024|0022300001|1629029": _appearance()},
        report_versions={REPORT: _exclude()},
        team_resolution={f"{REPORT}|3": _team()},
        schedule_dates={"0022300001": _schedule()},
    )
    availability_build.validate_exceptions(observed, approved)
    empty_observed = {section: {} for section in SECTIONS}
    availability_build.validate_exceptions(empty_observed, _ledger())

    # observed without approval, per section
    for section, key in (
        ("appearance_consistency", "2024|0022300001|1629029"),
        ("report_versions", REPORT),
        ("team_resolution", f"{REPORT}|3"),
        ("schedule_dates", "0022300001"),
    ):
        with pytest.raises(
            availability_build.AvailabilityBuildError,
            match=rf"{section} missing approvals.*{key.replace('|', r'\|')}",
        ):
            availability_build.validate_exceptions(observed, _without(approved, section) | {section: {}})

    # approved without observation, per section
    for section in SECTIONS:
        with pytest.raises(
            availability_build.AvailabilityBuildError,
            match=rf"{section} stale approvals",
        ):
            availability_build.validate_exceptions(
                {**observed, section: {}}, approved,
            )

    # same key, different fields (note alone never drifts)
    renoted = json.loads(json.dumps(approved))
    for section in SECTIONS:
        for entry in renoted[section].values():
            entry["note"] = "a different reviewer sentence"
    availability_build.validate_exceptions(observed, renoted)

    drifted = json.loads(json.dumps(approved))
    drifted["team_resolution"][f"{REPORT}|3"]["nba_team_id"] = 1610612747
    with pytest.raises(
        availability_build.AvailabilityBuildError,
        match=r"team_resolution drift.*1610612746.*1610612747",
    ):
        availability_build.validate_exceptions(observed, drifted)

    drifted = json.loads(json.dumps(approved))
    drifted["appearance_consistency"]["2024|0022300001|1629029"]["appearance"] = "absent"
    with pytest.raises(
        availability_build.AvailabilityBuildError,
        match=r"appearance_consistency drift",
    ):
        availability_build.validate_exceptions(observed, drifted)

    # observed entries follow the approved grammar minus note
    with pytest.raises(availability_build.AvailabilityBuildError, match="invalid fields"):
        availability_build.validate_exceptions(
            {**observed, "team_resolution": {f"{REPORT}|3": _team()}}, approved,
        )
    # an observed team_resolution entry with no approval applied carries a
    # null nba_team_id: unapproved against the empty ledger ...
    unapproved = {
        **empty_observed,
        "team_resolution": {f"{REPORT}|3": {"team_raw": "LA Clippers", "nba_team_id": None}},
    }
    with pytest.raises(
        availability_build.AvailabilityBuildError,
        match=r"team_resolution missing approvals.*LA Clippers",
    ):
        availability_build.validate_exceptions(unapproved, _ledger())
    # ... and still "missing approvals", never drift, when the ledger holds
    # that key with an int
    with pytest.raises(
        availability_build.AvailabilityBuildError,
        match=r"team_resolution missing approvals.*LA Clippers",
    ) as excinfo:
        availability_build.validate_exceptions(
            {**observed, "team_resolution": unapproved["team_resolution"]}, approved,
        )
    assert "drift" not in str(excinfo.value)
    # the approved side keeps the strict int rule
    with pytest.raises(availability_build.AvailabilityBuildError, match="wrong type"):
        availability_build.validate_exceptions(
            observed,
            {**approved, "team_resolution": {f"{REPORT}|3": {**_team(), "nba_team_id": None}}},
        )
    with pytest.raises(availability_build.AvailabilityBuildError, match="top-level keys"):
        availability_build.validate_exceptions({**observed, "schema_version": 1}, approved)
    with pytest.raises(availability_build.AvailabilityBuildError, match="top-level keys"):
        availability_build.validate_exceptions(observed, _without(approved, "schema_version"))
    with pytest.raises(availability_build.AvailabilityBuildError, match="schema_version"):
        availability_build.validate_exceptions(observed, {**approved, "schema_version": 2})


# ==========================================================================
# Task 14: table assembly, Arrow schemas and partitions
# ==========================================================================
#
# The seed builders below are module-level so Task 15 can extend the raw
# tree to every publication season. They publish exactly what the fetchers
# would: box-roster pairs through ``box_rosters.expected_metadata`` and the
# path validators, PDF pairs through ``injury_reports.pdf_metadata``, one
# inventory generation per month through ``write_inventory_generation`` and
# the derivative assets through ``injury_derivative.expected_metadata``.
# ``patch_enumerated_slots`` trims ``injury_reports.enumerate_slots`` to the
# seeded slots so each month enumerates only its three.


REPO_ROOT = Path(__file__).resolve().parents[1]
PDF_FIXTURES = REPO_ROOT / "tests/fixtures/injury_reports"

OKC, HOU, IND = box_tests.OKC, box_tests.HOU, box_tests.IND
CHI, CHA = 1610612741, 1610612766
OPENER, FINALS = box_tests.OPENER, box_tests.FINALS
# Hand-authored third game that the 2024-01-31 fixture report lists
# (``CHI@CHA``), so resolution has something to resolve.
BULLS_HORNETS = box_rosters.WorklistGame(
    game_id="0022300700", season=2024, game_type="regular", game_date=date(2024, 1, 31),
)
SEEDED_GAMES = (BULLS_HORNETS, FINALS, OPENER)
DORT, SMITH, DIENG = 1629652, 1630224, 1631096
SHAI, JALEN, HALIBURTON, TURNER = 1628983, 1631114, 1630169, 1626167
LONZO, COBY_WHITE, LAVINE = 1627832, 1629632, 203897
LAMELO, BRIDGES, MARK_WILLIAMS = 1630163, 1628970, 1631109
# The six other players the 2024-01-31 fixture lists for CHI@CHA; seeded as
# inactives only when ``full_roster=True`` (Task 15) so every listed row of
# the game resolves and the person-resolution floors hold.
TORREY_CRAIG, DALEN_TERRY, PATRICK_WILLIAMS = 1628470, 1631207, 1630172
AMARI_BAILEY, HAYWARD, LOWRY = 1641735, 202330, 200768
FULL_ROSTER_INACTIVES = {
    CHI: (
        (TORREY_CRAIG, "Torrey", "Craig", "13"),
        (DALEN_TERRY, "Dalen", "Terry", "25"),
        (PATRICK_WILLIAMS, "Patrick", "Williams", "44"),
    ),
    CHA: (
        (AMARI_BAILEY, "Amari", "Bailey", "9"),
        (HAYWARD, "Gordon", "Hayward", "20"),
        (LOWRY, "Kyle", "Lowry", "7"),
    ),
}


def synthetic_game(season: int) -> box_rosters.WorklistGame:
    """A regular-season ``CHI@CHA`` game on 15 January of ``season``."""
    return box_rosters.WorklistGame(
        game_id=f"002{season - 2001:02d}00900", season=season, game_type="regular",
        game_date=date(season, 1, 15),
    )


SEASON_GAMES = tuple(synthetic_game(season) for season in range(2012, 2024))

JANUARY_REPORT = "Injury-Report_2024-01-31_05PM"
QUARTER_REPORT_2026 = "Injury-Report_2026-01-10_12_00AM"
RECHECK_DATES = ("2026-11-09T10:00:00Z", "2026-11-10T10:00:00Z")
SEEDED_MONTHS = {
    "2024-01": {
        "present": {JANUARY_REPORT: JANUARY_REPORT + ".pdf"},
        "unavailable": ("Injury-Report_2024-01-31_03PM", "Injury-Report_2024-01-31_04PM"),
    },
    "2026-01": {
        "present": {QUARTER_REPORT_2026: QUARTER_REPORT_2026 + ".pdf"},
        "unavailable": ("Injury-Report_2026-01-10_12_15AM", "Injury-Report_2026-01-10_12_30AM"),
    },
}
UNOPENABLE_PDF = b"%PDF-1.4 garbage"

# Task 15: one present report per version season. The 2019, 2021, 2024 and
# 2026 fixtures sit at their own slots; the one-page 2019-02-15 fixture is
# reused under one slot of each season without a fixture (2020, 2022, 2023,
# 2025) and approved by an ``accept_title`` ledger entry (see module doc).
TINY_REPORT = "Injury-Report_2019-02-15_01PM"
TINY_PRINTED_ET = "2019-02-15T13:30:00"
REUSED_REPORTS = (
    "Injury-Report_2019-11-15_01PM",   # season 2020
    "Injury-Report_2021-11-15_01PM",   # season 2022
    "Injury-Report_2022-11-15_01PM",   # season 2023
    "Injury-Report_2024-11-15_01PM",   # season 2025
)
FULL_MONTHS = {
    **SEEDED_MONTHS,
    "2019-02": {"present": {TINY_REPORT: TINY_REPORT + ".pdf"}, "unavailable": ()},
    "2021-04": {
        "present": {"Injury-Report_2021-04-01_05PM": "Injury-Report_2021-04-01_05PM.pdf"},
        "unavailable": (),
    },
    **{
        f"{report_id[14:21]}": {"present": {report_id: TINY_REPORT + ".pdf"}, "unavailable": ()}
        for report_id in REUSED_REPORTS
    },
}
FULL_REPORTS = tuple(
    report_id for month in sorted(FULL_MONTHS) for report_id in FULL_MONTHS[month]["present"]
)


def _rows_for_game(game: box_rosters.WorklistGame) -> list[dict]:
    def row(person_id, team, franchise, is_home):
        return box_tests.player_games_row(
            game.game_id, person_id, season=game.season, game_type=game.game_type,
            game_date=game.game_date, nba_team_id=team, franchise_id=franchise,
            team_abbreviation_raw=franchise, is_home=is_home,
        )
    if game.game_id == FINALS.game_id:
        return [
            row(SHAI, OKC, "OKC", True), row(JALEN, OKC, "OKC", True),
            row(HALIBURTON, IND, "IND", False), row(TURNER, IND, "IND", False),
        ]
    if game.game_id == OPENER.game_id:
        return [row(DORT, OKC, "OKC", True), row(SMITH, HOU, "HOU", False)]
    # BULLS_HORNETS and every synthetic CHI@CHA game
    return [row(COBY_WHITE, CHI, "CHI", False), row(BRIDGES, CHA, "CHA", True)]


def seeded_player_games_rows(*, credited_extra=(), games=SEEDED_GAMES) -> list[dict]:
    """The ``player_games`` rows behind the seeded games (both teams each)."""
    rows = [row for game in games for row in _rows_for_game(game)]
    rows.extend(credited_extra)
    return rows


def credited_dieng_row() -> dict:
    """A ``player_games`` credit for a DNP player: one appearance disagreement."""
    return box_tests.player_games_row(
        OPENER.game_id, DIENG, season=2026, game_type="regular",
        game_date=OPENER.game_date, nba_team_id=OKC, franchise_id="OKC",
        team_abbreviation_raw="OKC", is_home=True,
    )


def _player(template: dict, person_id: int, first: str, family: str, *, comment="", minutes="", position="", jersey="0") -> dict:
    player = copy.deepcopy(template)
    player.update({
        "personId": person_id, "firstName": first, "familyName": family,
        "nameI": f"{first[0]}. {family}", "playerSlug": f"{first}-{family}".lower(),
        "position": position, "comment": comment, "jerseyNum": jersey,
    })
    player["statistics"]["minutes"] = minutes
    return player


def _inactive(person_id: int, first: str, family: str, jersey: str) -> dict:
    return {"personId": person_id, "firstName": first, "familyName": family, "jerseyNum": jersey}


def bulls_hornets_bodies(*, full_roster: bool = False) -> dict[str, bytes]:
    """Synthetic ``CHI@CHA`` bodies built from the opener fixture's shape.

    ``full_roster`` adds the six other players the 2024-01-31 fixture lists
    for the game as inactives, so every listed row resolves.
    """
    traditional = box_tests.fixture_document(OPENER.game_id, "traditional")
    root = traditional["boxScoreTraditional"]
    template = root["homeTeam"]["players"][0]
    root.update({"gameId": BULLS_HORNETS.game_id, "homeTeamId": CHA, "awayTeamId": CHI})
    root["homeTeam"].update({
        "teamId": CHA, "teamTricode": "CHA", "teamCity": "Charlotte", "teamName": "Hornets",
        "teamSlug": "hornets",
        "players": [
            _player(template, BRIDGES, "Miles", "Bridges", minutes="36:00", position="F", jersey="0"),
            _player(template, LAMELO, "LaMelo", "Ball", comment="DND - Injury/Illness - Right Ankle; Tendinopathy", jersey="1"),
        ],
    })
    root["awayTeam"].update({
        "teamId": CHI, "teamTricode": "CHI", "teamCity": "Chicago", "teamName": "Bulls",
        "teamSlug": "bulls",
        "players": [
            _player(template, COBY_WHITE, "Coby", "White", minutes="35:00", position="G", jersey="0"),
            _player(template, LONZO, "Lonzo", "Ball", comment="DND - Injury/Illness - Left Knee; Surgery", jersey="2"),
        ],
    })
    summary = box_tests.fixture_document(OPENER.game_id, "summary")
    sroot = summary["boxScoreSummary"]
    sroot.update({
        "gameId": BULLS_HORNETS.game_id, "gameCode": "20240131/CHICHA",
        "gameEt": "2024-01-31T19:00:00Z", "gameTimeUTC": "2024-02-01T00:00:00Z",
        "homeTeamId": CHA, "awayTeamId": CHI,
    })
    extra = FULL_ROSTER_INACTIVES if full_roster else {CHI: (), CHA: ()}
    sroot["homeTeam"].update({
        "teamId": CHA, "teamTricode": "CHA", "teamCity": "Charlotte", "teamName": "Hornets",
        "teamSlug": "hornets",
        "inactives": [
            _inactive(MARK_WILLIAMS, "Mark", "Williams", "5"),
            *(_inactive(*player) for player in extra[CHA]),
        ],
    })
    sroot["awayTeam"].update({
        "teamId": CHI, "teamTricode": "CHI", "teamCity": "Chicago", "teamName": "Bulls",
        "teamSlug": "bulls",
        "inactives": [
            _inactive(LAVINE, "Zach", "LaVine", "8"),
            *(_inactive(*player) for player in extra[CHI]),
        ],
    })
    return {"traditional": box_tests.encode(traditional), "summary": box_tests.encode(summary)}


def synthetic_bodies(game: box_rosters.WorklistGame) -> dict[str, bytes]:
    """The ``CHI@CHA`` bodies re-dated to one synthetic season game."""
    bodies = {name: json.loads(payload) for name, payload in bulls_hornets_bodies().items()}
    bodies["traditional"]["boxScoreTraditional"]["gameId"] = game.game_id
    played = game.game_date
    bodies["summary"]["boxScoreSummary"].update({
        "gameId": game.game_id,
        "gameCode": f"{played:%Y%m%d}/CHICHA",
        "gameEt": f"{played.isoformat()}T19:00:00Z",
        "gameTimeUTC": f"{(played + timedelta(days=1)).isoformat()}T00:00:00Z",
    })
    return {name: box_tests.encode(document) for name, document in bodies.items()}


def box_bodies(game: box_rosters.WorklistGame, *, full_roster: bool = False) -> dict[str, bytes]:
    if game.game_id == BULLS_HORNETS.game_id:
        return bulls_hornets_bodies(full_roster=full_roster)
    if game.game_id in (FINALS.game_id, OPENER.game_id):
        return {
            endpoint: box_tests.fixture_bytes(game.game_id, endpoint)
            for endpoint in box_rosters.ENDPOINTS
        }
    return synthetic_bodies(game)


def publish_box_pair(data_dir: Path, game: box_rosters.WorklistGame, endpoint: str, payload: bytes) -> Path:
    validator = box_rosters.validate_traditional if endpoint == "traditional" else box_rosters.validate_summary
    return cache.publish_bytes(
        box_rosters.cache_path(data_dir, game.season, game.game_id, endpoint),
        payload,
        box_rosters.expected_metadata(game, endpoint),
        functools.partial(validator, game_id=game.game_id),
    )


def seed_part_a(
    data_dir: Path, *, credited_extra=(), body_overrides=None, games=SEEDED_GAMES,
    full_roster: bool = False,
) -> tuple[Path, Path]:
    """Seed ``player_games`` and both box-roster pairs per game; return ``(raw_root, player_games_root)``."""
    player_games_root = data_dir / "parquet-large/player_games"
    box_tests.write_player_games_fixture(
        player_games_root, seeded_player_games_rows(credited_extra=credited_extra, games=games),
    )
    overrides = body_overrides or {}
    for game in games:
        for endpoint, payload in box_bodies(game, full_roster=full_roster).items():
            publish_box_pair(data_dir, game, endpoint, overrides.get((game.game_id, endpoint), payload))
    return data_dir / "raw", player_games_root


def report_payload(source) -> bytes:
    return source if isinstance(source, bytes) else (PDF_FIXTURES / source).read_bytes()


def publish_report_pdf(data_dir: Path, report_id: str, source) -> bytes:
    slot = injury_reports.slot_from_report_id(report_id)
    payload = report_payload(source)
    cache.publish_bytes(
        injury_reports.pdf_path(data_dir, slot),
        payload,
        injury_reports.pdf_metadata(slot, injury_reports.inspect_pdf(payload)),
        injury_reports.validate_pdf,
    )
    return payload


def evidence_entry(fetched_at: str) -> dict:
    body = b"<Error><Code>AccessDenied</Code></Error>"
    return {
        "fetched_at": fetched_at, "http_status": 403,
        "date_header": "Mon, 09 Nov 2026 14:02:11 GMT",
        "x_amz_request_id": "REQ", "x_amz_id_2": "ID2",
        "body_bytes": len(body), "body_sha256": hashlib.sha256(body).hexdigest(),
        "body_text": body.decode("utf-8"),
    }


def slot_entry(report_id: str, **overrides) -> dict:
    slot = injury_reports.slot_from_report_id(report_id)
    entry = {
        "grid": slot.grid, "slot_date": slot.slot_date.isoformat(),
        "slot_label": slot.slot_label, "url": slot.url,
        "status": "unavailable", "sha256": None, "bytes": None, "evidence": [],
    }
    entry.update(overrides)
    return entry


def unavailable_entry(report_id: str, fetched_at=RECHECK_DATES) -> dict:
    return slot_entry(report_id, evidence=[evidence_entry(stamp) for stamp in fetched_at])


def present_entry(report_id: str, payload: bytes, evidence=()) -> dict:
    return slot_entry(
        report_id, status="present", sha256=hashlib.sha256(payload).hexdigest(),
        bytes=len(payload), evidence=list(evidence),
    )


def write_generation(data_dir: Path, month: str, slots: dict) -> Path:
    control = {
        "checked_at": "2026-11-09T09:59:00Z",
        "file_name": "Injury-Report_2024-01-31_05PM.pdf",
        "sha256": "e39fe95bff31019494b82c9ec019919e94d8f10494395bce643d2c1ba02be17c",
        "ok": True,
    }
    return injury_reports.write_inventory_generation(
        data_dir, month, {"slots": slots, "controls": [control]},
    )


def patch_enumerated_slots(monkeypatch, report_ids) -> tuple[injury_reports.Slot, ...]:
    """Make ``injury_reports.enumerate_slots`` return only ``report_ids`` (in grid order)."""
    slots = tuple(sorted(
        (injury_reports.slot_from_report_id(report_id) for report_id in dict.fromkeys(report_ids)),
        key=lambda slot: (slot.slot_date, slot.slot_start_et, slot.grid),
    ))

    def fake_enumerate(months=None):
        if months is None:
            return slots
        selected = set(months)
        return tuple(slot for slot in slots if slot.month in selected)

    monkeypatch.setattr(injury_reports, "enumerate_slots", fake_enumerate)
    return slots


def month_state(data_dir: Path, month: str, spec: dict) -> dict:
    """Publish the month's present PDFs and return its generation ``slots`` object."""
    slots = {}
    for report_id, source in spec["present"].items():
        slots[report_id] = present_entry(report_id, publish_report_pdf(data_dir, report_id, source))
    for report_id in spec["unavailable"]:
        slots[report_id] = unavailable_entry(report_id)
    return slots


def seed_part_b(data_dir: Path, monkeypatch, months=SEEDED_MONTHS) -> dict[str, dict]:
    """Seed PDFs plus one generation per month and trim the enumerated slots to them."""
    states = {}
    for month, spec in months.items():
        states[month] = month_state(data_dir, month, spec)
        write_generation(data_dir, month, states[month])
    patch_enumerated_slots(
        monkeypatch, [report_id for state in states.values() for report_id in state],
    )
    return states


def seed_derivative(data_dir: Path, seasons=(2024, 2026)) -> None:
    for season in seasons:
        payload = derivative_tests.derivative_parquet_bytes([
            {
                "season": season, "report_datetime": datetime(season, 1, 10, 17, 30),
                "game_date": date(season, 1, 10), "game_time": "07:30 (ET)",
                "report_matchup": "BOS@NYK", "away_team_abbr": "BOS", "home_team_abbr": "NYK",
                "team": "Boston Celtics", "report_player_name": "Porter Jr., Michael",
                "current_status": "Out", "reason": "Injury/Illness - Left Ankle; Sprain",
                "category": None, "previous_status": None, "game_id": None,
                "team_id": None, "person_id": None, "match_method": None,
            },
        ])
        cache.publish_bytes(
            injury_derivative.asset_path(data_dir, season),
            payload,
            {**injury_derivative.expected_metadata(season), "release_asset_updated_at": "2026-11-03T06:10:00Z"},
            injury_derivative.validate_asset,
        )


def seed_raw_tree(tmp_path: Path, monkeypatch, *, months=SEEDED_MONTHS, **part_a) -> tuple[Path, Path]:
    """The whole tiny raw tree: returns ``(raw_root, player_games_root)``."""
    raw_root, player_games_root = seed_part_a(tmp_path, **part_a)
    seed_part_b(tmp_path, monkeypatch, months)
    seed_derivative(tmp_path)
    return raw_root, player_games_root


def accept_title_entry(report_id: str, source=TINY_REPORT + ".pdf", printed=TINY_PRINTED_ET) -> dict:
    """The ``accept_title`` approval for a fixture reused under ``report_id``."""
    return {
        "action": "accept_title",
        "sha256": hashlib.sha256(report_payload(source)).hexdigest(),
        "failed_check": None,
        "title_printed_et": printed,
        "slot_label": injury_reports.slot_from_report_id(report_id).slot_label,
        "note": "Test fixture reused under a slot of a season without a fixture.",
    }


def full_ledger(**sections) -> dict:
    """The test ledger approving the four reused reports, plus ``sections``."""
    document = _ledger(report_versions={rid: accept_title_entry(rid) for rid in REUSED_REPORTS})
    for section, entries in sections.items():
        document[section] = {**document[section], **entries}
    return document


def write_ledger(tmp_path: Path, document: dict) -> Path:
    path = tmp_path / "ledger.json"
    path.write_bytes((json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return path


def seed_full_tree(
    tmp_path: Path, monkeypatch, *, months=FULL_MONTHS, ledger=None,
    games=SEASON_GAMES + SEEDED_GAMES, full_roster=True, **part_a,
) -> tuple[Path, Path, Path]:
    """Seed every season 2012-2026 with one game and 2019-2026 with one report.

    Returns ``(raw_root, player_games_root, ledger_path)``; the ledger is
    :func:`full_ledger` unless ``ledger`` is given.
    """
    raw_root, player_games_root = seed_raw_tree(
        tmp_path, monkeypatch, months=months, games=games, full_roster=full_roster, **part_a,
    )
    ledger_path = write_ledger(tmp_path, full_ledger() if ledger is None else ledger)
    return raw_root, player_games_root, ledger_path


def _empty_ledger() -> dict:
    return _ledger()


def _col(frame, name):
    return [None if pd.isna(value) else value for value in frame[name]]


def _parts(tmp_path, monkeypatch, approved=None, **seed):
    raw_root, player_games_root = seed_raw_tree(tmp_path, monkeypatch, **seed)
    approved = approved if approved is not None else _empty_ledger()
    index = box_rosters.load_player_games_index(player_games_root)
    part_a = availability_build.assemble_part_a(raw_root, index, approved)
    part_b = availability_build.assemble_part_b(raw_root, part_a, approved)
    return part_a, part_b


# --------------------------------------------------------------------------
# schemas
# --------------------------------------------------------------------------


def _schema_rows(schema):
    return [(field.name, str(field.type), field.nullable) for field in schema]


def test_schemas_match_the_spec_column_by_column():
    """Would fail if a name, Arrow type or nullability drifted from the spec tables."""
    assert _schema_rows(availability_build.GAME_ROSTERS_SCHEMA) == [
        ("game_id", "large_string", False), ("person_id", "int64", False),
        ("season", "int64", False), ("game_type", "large_string", False),
        ("game_date", "date32[day]", False), ("nba_team_id", "int64", False),
        ("team_tricode_raw", "large_string", False), ("franchise_id", "large_string", False),
        ("is_home", "bool", False), ("roster_status", "large_string", False),
        ("first_name_raw", "large_string", False), ("family_name_raw", "large_string", False),
        ("jersey_num", "large_string", True), ("starter_position_raw", "large_string", True),
        ("comment_raw", "large_string", True), ("comment_code", "large_string", True),
        ("minutes_raw", "large_string", True),
    ]
    assert _schema_rows(availability_build.GAME_SCHEDULE_SCHEMA) == [
        ("game_id", "large_string", False), ("season", "int64", False),
        ("game_type", "large_string", False), ("game_date", "date32[day]", False),
        ("scheduled_start_utc", "timestamp[us, tz=UTC]", False),
        ("scheduled_start_et_raw", "large_string", False), ("game_code_raw", "large_string", False),
    ]
    assert _schema_rows(availability_build.INJURY_REPORT_VERSIONS_SCHEMA) == [
        ("report_id", "large_string", False), ("season", "int64", False),
        ("grid", "large_string", False), ("slot_date", "date32[day]", False),
        ("slot_label", "large_string", False), ("report_slot_et", "timestamp[us]", False),
        ("report_time_printed_et", "timestamp[us]", True),
        ("report_time_printed_utc", "timestamp[us, tz=UTC]", True),
        ("report_time_printed_utc_ambiguous", "bool", False),
        ("url", "large_string", False), ("sha256", "large_string", False), ("bytes", "int64", False),
        ("pages", "int64", True), ("listing_rows", "int64", True), ("placeholder_rows", "int64", True),
        ("games_listed", "int64", True), ("games_resolved", "int64", True),
        ("parse_status", "large_string", False), ("identical_listings_to_previous", "bool", False),
    ]
    assert _schema_rows(availability_build.INJURY_REPORT_LISTINGS_SCHEMA) == [
        ("report_id", "large_string", False), ("season", "int64", False),
        ("row_number", "int64", False), ("page", "int64", False),
        ("game_date", "date32[day]", False), ("game_time_raw", "large_string", False),
        ("matchup_raw", "large_string", False), ("away_tricode", "large_string", False),
        ("home_tricode", "large_string", False), ("team_raw", "large_string", False),
        ("is_placeholder", "bool", False), ("placeholder_raw", "large_string", True),
        ("player_name_raw", "large_string", True), ("current_status_raw", "large_string", True),
        ("reason_raw", "large_string", True), ("category_raw", "large_string", True),
        ("previous_status_raw", "large_string", True), ("game_id", "large_string", True),
        ("game_resolution", "large_string", False), ("nba_team_id", "int64", True),
        ("franchise_id", "large_string", True), ("person_id", "int64", True),
        ("person_resolution", "large_string", False),
    ]
    for schema in (
        availability_build.GAME_ROSTERS_SCHEMA, availability_build.GAME_SCHEDULE_SCHEMA,
        availability_build.INJURY_REPORT_VERSIONS_SCHEMA,
        availability_build.INJURY_REPORT_LISTINGS_SCHEMA,
    ):
        assert isinstance(schema, pa.Schema)
        assert schema.metadata is None


# --------------------------------------------------------------------------
# Part A
# --------------------------------------------------------------------------


def test_assemble_part_a_builds_rosters_schedule_and_observed_disagreements(tmp_path):
    """Would fail if rows, order, side outputs or observed entries drifted."""
    summary = box_tests.fixture_document(FINALS.game_id, "summary")
    summary["boxScoreSummary"]["gameEt"] = "2025-06-06T20:30:00Z"
    raw_root, player_games_root = seed_part_a(
        tmp_path, credited_extra=[credited_dieng_row()],
        body_overrides={(FINALS.game_id, "summary"): box_tests.encode(summary)},
    )
    index = box_rosters.load_player_games_index(player_games_root)

    part = availability_build.assemble_part_a(raw_root, index, _empty_ledger())

    assert isinstance(part, availability_build.PartA)
    rosters = part.rosters
    assert list(rosters.columns) == list(availability_build.GAME_ROSTERS_SCHEMA.names)
    assert len(rosters) == 6 + 6 + 5
    expected_order = sorted(
        zip(rosters["season"], rosters["game_date"], rosters["game_id"],
            rosters["nba_team_id"], rosters["roster_status"], rosters["person_id"]),
    )
    assert list(zip(
        rosters["season"], rosters["game_date"], rosters["game_id"],
        rosters["nba_team_id"], rosters["roster_status"], rosters["person_id"],
    )) == expected_order
    assert list(rosters.index) == list(range(len(rosters)))
    assert _col(rosters, "game_id")[:6] == [BULLS_HORNETS.game_id] * 6
    assert _col(rosters, "game_id")[-5:] == [OPENER.game_id] * 5
    bulls = rosters[rosters["nba_team_id"] == CHI]
    assert dict(zip(bulls["person_id"], bulls["roster_status"])) == {
        LAVINE: "inactive", LONZO: "active_not_played", COBY_WHITE: "played",
    }
    dieng = rosters[rosters["person_id"] == DIENG].iloc[0]
    assert dieng["roster_status"] == "played"      # credited by player_games; reported, not reclassified
    assert dieng["comment_code"] == "DNP"
    table = availability_build.table_from_frame(rosters, availability_build.GAME_ROSTERS_SCHEMA)
    assert table.schema == availability_build.GAME_ROSTERS_SCHEMA

    schedule = part.schedule
    assert list(schedule.columns) == list(availability_build.GAME_SCHEDULE_SCHEMA.names)
    assert _col(schedule, "game_id") == [BULLS_HORNETS.game_id, FINALS.game_id, OPENER.game_id]
    assert _col(schedule, "season") == [2024, 2025, 2026]
    assert _col(schedule, "game_code_raw") == ["20240131/CHICHA", "20250605/INDOKC", "20251021/HOUOKC"]
    assert schedule["scheduled_start_utc"].iloc[2] == pd.Timestamp("2025-10-21T23:30:00Z")
    assert availability_build.table_from_frame(
        schedule, availability_build.GAME_SCHEDULE_SCHEMA,
    ).schema == availability_build.GAME_SCHEDULE_SCHEMA

    assert part.team_strings == {
        (BULLS_HORNETS.game_id, CHI): "Chicago Bulls", (BULLS_HORNETS.game_id, CHA): "Charlotte Hornets",
        (FINALS.game_id, OKC): "Oklahoma City Thunder", (FINALS.game_id, IND): "Indiana Pacers",
        (OPENER.game_id, OKC): "Oklahoma City Thunder", (OPENER.game_id, HOU): "Houston Rockets",
    }
    assert part.tricodes == {
        (BULLS_HORNETS.game_id, CHI): "CHI", (BULLS_HORNETS.game_id, CHA): "CHA",
        (FINALS.game_id, OKC): "OKC", (FINALS.game_id, IND): "IND",
        (OPENER.game_id, OKC): "OKC", (OPENER.game_id, HOU): "HOU",
    }
    assert part.appearance_observed == {
        f"2026|{OPENER.game_id}|{DIENG}": {
            "comment_raw": "DNP - Coach's Decision", "minutes_raw": None, "appearance": "credited",
        },
    }
    assert part.schedule_observed == {
        FINALS.game_id: {
            "game_et_raw": "2025-06-06T20:30:00Z", "game_code_raw": "20250605/INDOKC",
            "game_date": "2025-06-05",
        },
    }
    counts = part.roster_counts
    assert list(counts.columns) == ["game_id", "nba_team_id", "active", "inactive"]
    assert [tuple(row) for row in counts.itertuples(index=False)] == [
        (BULLS_HORNETS.game_id, CHI, 2, 1), (BULLS_HORNETS.game_id, CHA, 2, 1),
        (FINALS.game_id, IND, 2, 1), (FINALS.game_id, OKC, 2, 1),
        (OPENER.game_id, HOU, 2, 0), (OPENER.game_id, OKC, 2, 1),
    ]
    # the observed entries are what validate_exceptions consumes
    observed = {
        "appearance_consistency": part.appearance_observed,
        "schedule_dates": part.schedule_observed,
        "report_versions": {}, "team_resolution": {},
    }
    with pytest.raises(availability_build.AvailabilityBuildError, match="missing approvals"):
        availability_build.validate_exceptions(observed, _empty_ledger())
    availability_build.validate_exceptions(observed, _ledger(
        appearance_consistency={
            key: {**entry, "note": "league credit"} for key, entry in part.appearance_observed.items()
        },
        schedule_dates={
            key: {**entry, "note": "gameEt a day late"} for key, entry in part.schedule_observed.items()
        },
    ))

    # a clean seed observes nothing
    clean_root, clean_games = seed_part_a(tmp_path / "clean")
    clean = availability_build.assemble_part_a(
        clean_root, box_rosters.load_player_games_index(clean_games), _empty_ledger(),
    )
    assert clean.appearance_observed == {} and clean.schedule_observed == {}
    assert len(clean.rosters) == 17


def test_assemble_part_a_requires_every_worklist_game_to_have_both_bodies(tmp_path):
    """Would fail if a game without a cached pair were skipped instead of failing."""
    raw_root, player_games_root = seed_part_a(tmp_path)
    index = box_rosters.load_player_games_index(player_games_root)
    summary = box_rosters.cache_path(tmp_path, FINALS.season, FINALS.game_id, "summary")
    summary.unlink()
    summary.with_name(summary.name + ".meta.json").unlink()

    with pytest.raises(availability_build.AvailabilityBuildError, match=rf"summary.*{FINALS.game_id}"):
        availability_build.assemble_part_a(raw_root, index, _empty_ledger())

    with pytest.raises(availability_build.AvailabilityBuildError, match="raw"):
        availability_build.assemble_part_a(tmp_path / "elsewhere", index, _empty_ledger())


# --------------------------------------------------------------------------
# Part B
# --------------------------------------------------------------------------


def test_assemble_part_b_builds_versions_and_listings_from_pdfs_and_generations(tmp_path, monkeypatch):
    """Would fail if versions, listings, ordering or counts drifted from the spec."""
    part_a, part = _parts(tmp_path, monkeypatch)

    assert isinstance(part, availability_build.PartB)
    versions = part.versions
    assert list(versions.columns) == list(availability_build.INJURY_REPORT_VERSIONS_SCHEMA.names)
    assert _col(versions, "report_id") == [JANUARY_REPORT, QUARTER_REPORT_2026]
    assert _col(versions, "season") == [2024, 2026]
    assert _col(versions, "grid") == ["hour", "quarter"]
    assert _col(versions, "slot_date") == [date(2024, 1, 31), date(2026, 1, 10)]
    assert _col(versions, "slot_label") == ["05PM", "12_00AM"]
    assert _col(versions, "report_slot_et") == [
        pd.Timestamp("2024-01-31T17:00:00"), pd.Timestamp("2026-01-10T00:00:00"),
    ]
    assert _col(versions, "report_time_printed_et") == [
        pd.Timestamp("2024-01-31T17:30:00"), pd.Timestamp("2026-01-10T00:00:00"),
    ]
    assert _col(versions, "report_time_printed_utc") == [
        pd.Timestamp("2024-01-31T22:30:00Z"), pd.Timestamp("2026-01-10T05:00:00Z"),
    ]
    assert _col(versions, "report_time_printed_utc_ambiguous") == [False, False]
    assert _col(versions, "url") == [
        injury_reports.PDF_BASE_URL + JANUARY_REPORT + ".pdf",
        injury_reports.PDF_BASE_URL + QUARTER_REPORT_2026 + ".pdf",
    ]
    january = report_payload(JANUARY_REPORT + ".pdf")
    assert _col(versions, "sha256")[0] == hashlib.sha256(january).hexdigest()
    assert _col(versions, "bytes") == [len(january), len(report_payload(QUARTER_REPORT_2026 + ".pdf"))]
    assert _col(versions, "pages") == [7, 5]
    assert _col(versions, "listing_rows") == [104, 65]
    assert _col(versions, "placeholder_rows") == [4, 22]
    assert _col(versions, "games_listed") == [14, 16]
    assert _col(versions, "games_resolved") == [1, 0]
    assert _col(versions, "parse_status") == ["ok", "ok"]
    assert _col(versions, "identical_listings_to_previous") == [False, False]
    assert availability_build.table_from_frame(
        versions, availability_build.INJURY_REPORT_VERSIONS_SCHEMA,
    ).schema == availability_build.INJURY_REPORT_VERSIONS_SCHEMA

    listings = part.listings
    published = list(availability_build.INJURY_REPORT_LISTINGS_SCHEMA.names)
    assert list(listings.columns) == published + ["report_slot_et"]
    assert len(listings) == 108 + 87
    keys = list(zip(listings["season"], listings["report_slot_et"], listings["report_id"], listings["row_number"]))
    assert keys == sorted(keys)
    assert list(listings.index) == list(range(len(listings)))
    for report_id, count in ((JANUARY_REPORT, 108), (QUARTER_REPORT_2026, 87)):
        rows = listings[listings["report_id"] == report_id]
        assert list(rows["row_number"]) == list(range(1, count + 1))
    first = listings.iloc[0]
    assert (first["report_id"], first["page"], first["game_date"], first["game_time_raw"], first["matchup_raw"]) == (
        JANUARY_REPORT, 1, date(2024, 1, 31), "07:00 (ET)", "CHI@CHA",
    )
    assert (first["away_tricode"], first["home_tricode"], first["team_raw"], first["player_name_raw"]) == (
        "CHI", "CHA", "Chicago Bulls", "Ball, Lonzo",
    )
    assert (first["current_status_raw"], first["reason_raw"]) == ("Out", "Injury/Illness - Left Knee; Surgery")
    assert first["is_placeholder"] is False or first["is_placeholder"] == False  # noqa: E712
    assert pd.isna(first["placeholder_raw"]) and pd.isna(first["category_raw"])
    # resolution against the seeded CHI@CHA game
    chi_cha = listings[listings["matchup_raw"] == "CHI@CHA"]
    assert set(chi_cha["game_id"]) == {BULLS_HORNETS.game_id}
    assert set(chi_cha["game_resolution"]) == {"resolved"}
    by_name = {row["player_name_raw"]: row for _, row in chi_cha.iterrows()}
    assert (by_name["Ball, Lonzo"]["nba_team_id"], by_name["Ball, Lonzo"]["franchise_id"], by_name["Ball, Lonzo"]["person_id"], by_name["Ball, Lonzo"]["person_resolution"]) == (CHI, "CHI", LONZO, "game_roster")
    assert (by_name["LaVine, Zach"]["person_id"], by_name["LaVine, Zach"]["person_resolution"]) == (LAVINE, "game_roster")
    assert (by_name["Bridges, Miles"]["nba_team_id"], by_name["Bridges, Miles"]["person_id"]) == (CHA, BRIDGES)
    assert pd.isna(by_name["Craig, Torrey"]["person_id"])
    assert by_name["Craig, Torrey"]["person_resolution"] == "unresolved_no_match"
    others = listings[listings["matchup_raw"] != "CHI@CHA"]
    assert set(others["game_resolution"]) == {"no_box_score_game"}
    assert others["game_id"].isna().all() and others["nba_team_id"].isna().all()
    assert set(others.loc[others["is_placeholder"].astype(bool), "person_resolution"]) == {"placeholder"}
    assert set(others.loc[~others["is_placeholder"].astype(bool), "person_resolution"]) == {"unresolved_game"}
    placeholders = listings[listings["is_placeholder"].astype(bool)]
    assert set(placeholders["placeholder_raw"]) == {"NOT YET SUBMITTED"}
    assert placeholders["player_name_raw"].isna().all()
    table = availability_build.table_from_frame(listings, availability_build.INJURY_REPORT_LISTINGS_SCHEMA)
    assert table.schema == availability_build.INJURY_REPORT_LISTINGS_SCHEMA
    assert table.num_rows == len(listings)

    assert part.report_versions_observed == {}
    assert part.team_resolution_observed == {}
    assert part.inventories == [
        {"month": "2024-01", "generation": 1, "path": "raw/nba_injury_reports/inventory/2024-01/gen-0001.json"},
        {"month": "2026-01", "generation": 1, "path": "raw/nba_injury_reports/inventory/2026-01/gen-0001.json"},
    ]
    assert part.slot_totals == {
        "hour": {"present": 1, "unavailable": 2, "rechecked": 2},
        "quarter": {"present": 1, "unavailable": 2, "rechecked": 2},
    }
    assert part.parse_counts == {
        2024: {"ok": 1, "ok_title_accepted": 0, "excluded": 0},
        2026: {"ok": 1, "ok_title_accepted": 0, "excluded": 0},
    }
    assert part.title_counts == {
        2024: {"ok": 1, "failed": 0, "unparsed": 0},
        2026: {"ok": 1, "failed": 0, "unparsed": 0},
    }
    assert part.placeholder_counts == {
        2024: {"NOT YET SUBMITTED": 4},
        2026: {"NOT YET SUBMITTED": 22},
    }
    # the frame feeds the resolution metrics unchanged
    metrics = availability_resolve.resolution_metrics(listings)
    assert set(metrics) == {2024, 2026}
    assert metrics[2024]["rows"] == 11 and metrics[2024]["rows_resolved"] == 5
    assert metrics[2024]["unresolved_matchups"]["CLE@MEM"] == 2

    # two builds from the same tree are identical
    again = availability_build.assemble_part_b(raw_root := tmp_path / "raw", part_a, _empty_ledger())
    pd.testing.assert_frame_equal(again.versions, versions)
    pd.testing.assert_frame_equal(again.listings, listings)
    assert raw_root.is_dir()


def test_identical_listings_to_previous_compares_multisets_in_slot_order():
    """Would fail if row order, excluded versions or the season boundary leaked into the flag."""
    def version(report_id, slot_et, status="ok", season=2024):
        return {"report_id": report_id, "season": season, "report_slot_et": slot_et, "parse_status": status}

    def row(report_id, row_number, *, reason="Injury/Illness - Left Knee; Surgery", player="Ball, Lonzo", placeholder=None):
        return {
            "report_id": report_id, "row_number": row_number, "page": 1,
            "game_date": date(2024, 1, 31), "game_time_raw": "07:00 (ET)", "matchup_raw": "CHI@CHA",
            "team_raw": "Chicago Bulls", "is_placeholder": placeholder is not None,
            "placeholder_raw": placeholder, "player_name_raw": None if placeholder else player,
            "current_status_raw": None if placeholder else "Out",
            "reason_raw": None if placeholder else reason, "category_raw": None, "previous_status_raw": None,
        }

    versions = pd.DataFrame.from_records([
        version("Injury-Report_2024-01-31_06PM", datetime(2024, 1, 31, 18)),   # out of slot order on purpose
        version("Injury-Report_2024-01-31_05PM", datetime(2024, 1, 31, 17)),
        version("Injury-Report_2024-01-31_07PM", datetime(2024, 1, 31, 19)),
        version("Injury-Report_2024-01-31_08PM", datetime(2024, 1, 31, 20), status="excluded"),
        version("Injury-Report_2024-01-31_09PM", datetime(2024, 1, 31, 21), status="ok_title_accepted"),
        version("Injury-Report_2024-10-01_05PM", datetime(2024, 10, 1, 17), season=2025),
    ])
    listings = pd.DataFrame.from_records([
        row("Injury-Report_2024-01-31_05PM", 1), row("Injury-Report_2024-01-31_05PM", 2, player="Craig, Torrey"),
        row("Injury-Report_2024-01-31_05PM", 3, placeholder="NOT YET SUBMITTED"),
        # same multiset, different page/row order
        row("Injury-Report_2024-01-31_06PM", 1, placeholder="NOT YET SUBMITTED"),
        row("Injury-Report_2024-01-31_06PM", 2, player="Craig, Torrey"), row("Injury-Report_2024-01-31_06PM", 3),
        # one reason differs
        row("Injury-Report_2024-01-31_07PM", 1, reason="Injury/Illness - Left Knee; Soreness"),
        row("Injury-Report_2024-01-31_07PM", 2, player="Craig, Torrey"),
        row("Injury-Report_2024-01-31_07PM", 3, placeholder="NOT YET SUBMITTED"),
        # the excluded version has no listings; the accepted one repeats 07PM exactly
        row("Injury-Report_2024-01-31_09PM", 1, reason="Injury/Illness - Left Knee; Soreness"),
        row("Injury-Report_2024-01-31_09PM", 2, player="Craig, Torrey"),
        row("Injury-Report_2024-01-31_09PM", 3, placeholder="NOT YET SUBMITTED"),
        # next season repeats the multiset of 09PM but is a season's first parsed version
        row("Injury-Report_2024-10-01_05PM", 1, reason="Injury/Illness - Left Knee; Soreness"),
        row("Injury-Report_2024-10-01_05PM", 2, player="Craig, Torrey"),
        row("Injury-Report_2024-10-01_05PM", 3, placeholder="NOT YET SUBMITTED"),
    ])

    flags = availability_build.identical_listings_to_previous(versions, listings)

    assert isinstance(flags, pd.Series)
    assert list(flags.index) == list(versions.index)
    assert dict(zip(versions["report_id"], flags)) == {
        "Injury-Report_2024-01-31_05PM": False,
        "Injury-Report_2024-01-31_06PM": True,
        "Injury-Report_2024-01-31_07PM": False,
        "Injury-Report_2024-01-31_08PM": False,
        "Injury-Report_2024-01-31_09PM": True,
        "Injury-Report_2024-10-01_05PM": False,
    }
    # a repeated row changes the multiset even though the set is unchanged
    doubled = pd.concat([listings, pd.DataFrame.from_records([row("Injury-Report_2024-01-31_06PM", 4)])], ignore_index=True)
    assert not bool(availability_build.identical_listings_to_previous(versions, doubled).iloc[0])


def test_assemble_part_b_requires_a_rechecked_outcome_for_every_enumerated_slot(tmp_path, monkeypatch):
    """Would fail if an unprobed or once-probed slot slipped into the publication."""
    raw_root, player_games_root = seed_raw_tree(tmp_path, monkeypatch)
    index = box_rosters.load_player_games_index(player_games_root)
    part_a = availability_build.assemble_part_a(raw_root, index, _empty_ledger())
    state, _ = injury_reports.read_current_inventory(tmp_path, "2024-01")
    missing_id, once_id = "Injury-Report_2024-01-31_03PM", "Injury-Report_2024-01-31_04PM"

    def rebuilt(slots):
        write_generation(tmp_path, "2024-01", slots)
        return availability_build.assemble_part_b(raw_root, part_a, _empty_ledger())

    # a slot missing from the generation
    without = {key: value for key, value in state["slots"].items() if key != missing_id}
    with pytest.raises(availability_build.AvailabilityBuildError, match=missing_id):
        rebuilt(without)
    # one evidence entry
    with pytest.raises(availability_build.AvailabilityBuildError, match=rf"{once_id}.*recheck"):
        rebuilt({**state["slots"], once_id: unavailable_entry(once_id, ("2026-11-09T10:00:00Z",))})
    # two entries on the same UTC date
    with pytest.raises(availability_build.AvailabilityBuildError, match=rf"{once_id}.*recheck"):
        rebuilt({**state["slots"], once_id: unavailable_entry(once_id, ("2026-11-09T10:00:00Z", "2026-11-09T23:59:59Z"))})
    # an unknown status
    with pytest.raises(availability_build.AvailabilityBuildError, match="status"):
        rebuilt({**state["slots"], once_id: slot_entry(once_id, status="pending")})
    # a present slot whose PDF pair is gone, or whose digest differs
    with pytest.raises(availability_build.AvailabilityBuildError, match=rf"{JANUARY_REPORT}.*sha256"):
        rebuilt({**state["slots"], JANUARY_REPORT: {**state["slots"][JANUARY_REPORT], "sha256": "0" * 64}})
    pdf = injury_reports.pdf_path(tmp_path, injury_reports.slot_from_report_id(JANUARY_REPORT))
    pdf.unlink()
    pdf.with_name(pdf.name + ".meta.json").unlink()
    with pytest.raises(availability_build.AvailabilityBuildError, match=rf"{JANUARY_REPORT}.*PDF"):
        rebuilt(state["slots"])
    # an entry the grids do not enumerate
    publish_report_pdf(tmp_path, JANUARY_REPORT, JANUARY_REPORT + ".pdf")
    stray = "Injury-Report_2024-01-30_05PM"
    with pytest.raises(availability_build.AvailabilityBuildError, match=stray):
        rebuilt({**state["slots"], stray: unavailable_entry(stray)})
    # restored, the month builds again
    assert len(rebuilt(state["slots"]).versions) == 2
    # a month with no generation at all
    (tmp_path / "raw/nba_injury_reports/inventory/2026-01").rename(tmp_path / "gone")
    with pytest.raises(availability_build.AvailabilityBuildError, match="2026-01"):
        availability_build.assemble_part_b(raw_root, part_a, _empty_ledger())


def test_assemble_part_b_uses_only_the_highest_verifying_generation_and_fails_when_it_is_damaged(tmp_path, monkeypatch):
    """Would fail if a stale generation were merged in or a torn newest one ignored."""
    raw_root, player_games_root = seed_part_a(tmp_path)
    index = box_rosters.load_player_games_index(player_games_root)
    part_a = availability_build.assemble_part_a(raw_root, index, _empty_ledger())
    seed_derivative(tmp_path)
    states = {month: month_state(tmp_path, month, spec) for month, spec in SEEDED_MONTHS.items()}
    patch_enumerated_slots(monkeypatch, [rid for state in states.values() for rid in state])
    write_generation(tmp_path, "2026-01", states["2026-01"])
    incomplete = {key: value for key, value in states["2024-01"].items() if key != "Injury-Report_2024-01-31_03PM"}
    first = write_generation(tmp_path, "2024-01", incomplete)      # gen-0001: incomplete
    second = write_generation(tmp_path, "2024-01", states["2024-01"])   # gen-0002: complete

    part = availability_build.assemble_part_b(raw_root, part_a, _empty_ledger())
    assert part.inventories[0] == {
        "month": "2024-01", "generation": 2, "path": "raw/nba_injury_reports/inventory/2024-01/gen-0002.json",
    }
    assert part.slot_totals["hour"] == {"present": 1, "unavailable": 2, "rechecked": 2}

    # gen-0002 torn: gen-0001 still verifies but the newest must never be skipped
    body = second.read_bytes()
    second.write_bytes(body[:-2])
    with pytest.raises(availability_build.AvailabilityBuildError, match=r"gen-0002"):
        availability_build.assemble_part_b(raw_root, part_a, _empty_ledger())
    second.write_bytes(body)
    assert len(availability_build.assemble_part_b(raw_root, part_a, _empty_ledger()).versions) == 2

    # a lower damaged generation does not block the build
    first.write_bytes(first.read_bytes()[:-2])
    assert len(availability_build.assemble_part_b(raw_root, part_a, _empty_ledger()).versions) == 2
    # an orphaned sidecar above the current generation is a damaged newest generation
    (second.parent / "gen-0003.json.meta.json").write_text("{}", encoding="utf-8")
    with pytest.raises(availability_build.AvailabilityBuildError, match=r"gen-0003"):
        availability_build.assemble_part_b(raw_root, part_a, _empty_ledger())


def test_assemble_part_b_excluded_and_accept_title_versions(tmp_path, monkeypatch):
    """Would fail if a failed gate were published, or an approval were applied loosely."""
    shifted = "Injury-Report_2024-01-31_06PM"        # the 05PM fixture printed 17:30 lands outside 18:00-18:59
    broken = "Injury-Report_2024-01-31_07PM"         # a %PDF body that does not open
    months = {
        **SEEDED_MONTHS,
        "2024-01": {
            "present": {
                JANUARY_REPORT: JANUARY_REPORT + ".pdf",
                shifted: JANUARY_REPORT + ".pdf",
                broken: UNOPENABLE_PDF,
            },
            "unavailable": SEEDED_MONTHS["2024-01"]["unavailable"],
        },
    }
    raw_root, player_games_root = seed_raw_tree(tmp_path, monkeypatch, months=months)
    index = box_rosters.load_player_games_index(player_games_root)
    part_a = availability_build.assemble_part_a(raw_root, index, _empty_ledger())
    sha = hashlib.sha256(report_payload(JANUARY_REPORT + ".pdf")).hexdigest()
    broken_sha = hashlib.sha256(UNOPENABLE_PDF).hexdigest()

    # without a ledger entry: observed exclusions, excluded rows, no listings
    part = availability_build.assemble_part_b(raw_root, part_a, _empty_ledger())
    assert part.report_versions_observed == {
        shifted: {
            "action": "exclude", "sha256": sha, "failed_check": "title_window",
            "title_printed_et": "2024-01-31T17:30:00", "slot_label": "06PM",
        },
        broken: {
            "action": "exclude", "sha256": broken_sha, "failed_check": "opens",
            "title_printed_et": None, "slot_label": "07PM",
        },
    }
    versions = part.versions.set_index("report_id")
    assert list(versions.index) == [JANUARY_REPORT, shifted, broken, QUARTER_REPORT_2026]
    row = versions.loc[shifted]
    assert row["parse_status"] == "excluded"
    assert row["report_time_printed_et"] == pd.Timestamp("2024-01-31T17:30:00")
    assert row["report_time_printed_utc"] == pd.Timestamp("2024-01-31T22:30:00Z")
    assert row["report_time_printed_utc_ambiguous"] == False  # noqa: E712
    assert row["pages"] == 7 and row["bytes"] == len(report_payload(JANUARY_REPORT + ".pdf"))
    assert all(pd.isna(row[name]) for name in ("listing_rows", "placeholder_rows", "games_listed", "games_resolved"))
    assert row["identical_listings_to_previous"] == False  # noqa: E712
    row = versions.loc[broken]
    assert row["parse_status"] == "excluded"
    assert pd.isna(row["pages"]) and pd.isna(row["report_time_printed_et"]) and pd.isna(row["report_time_printed_utc"])
    assert row["report_time_printed_utc_ambiguous"] == False  # noqa: E712
    assert row["sha256"] == broken_sha and row["bytes"] == len(UNOPENABLE_PDF)
    assert set(part.listings["report_id"]) == {JANUARY_REPORT, QUARTER_REPORT_2026}
    assert part.parse_counts[2024] == {"ok": 1, "ok_title_accepted": 0, "excluded": 2}
    assert part.title_counts[2024] == {"ok": 1, "failed": 1, "unparsed": 1}
    assert part.slot_totals["hour"] == {"present": 3, "unavailable": 2, "rechecked": 2}
    assert availability_build.table_from_frame(
        part.versions, availability_build.INJURY_REPORT_VERSIONS_SCHEMA,
    ).num_rows == 4
    observed = {
        "appearance_consistency": part_a.appearance_observed,
        "schedule_dates": part_a.schedule_observed,
        "report_versions": part.report_versions_observed,
        "team_resolution": part.team_resolution_observed,
    }
    with pytest.raises(availability_build.AvailabilityBuildError, match=rf"report_versions missing approvals.*{shifted}"):
        availability_build.validate_exceptions(observed, _empty_ledger())

    # accept_title publishes the listings as ok_title_accepted; exclude keeps the row excluded
    approved = _ledger(report_versions={
        shifted: {
            "action": "accept_title", "sha256": sha, "failed_check": None,
            "title_printed_et": "2024-01-31T17:30:00", "slot_label": "06PM",
            "note": "Generated half an hour before the slot; listings verified by eye.",
        },
        broken: {
            "action": "exclude", "sha256": broken_sha, "failed_check": "opens",
            "title_printed_et": None, "slot_label": "07PM",
            "note": "Truncated upload on the league's side.",
        },
    })
    accepted = availability_build.assemble_part_b(raw_root, part_a, approved)
    assert accepted.report_versions_observed == {
        shifted: {
            "action": "accept_title", "sha256": sha, "failed_check": None,
            "title_printed_et": "2024-01-31T17:30:00", "slot_label": "06PM",
        },
        broken: part.report_versions_observed[broken],
    }
    availability_build.validate_exceptions({**observed, "report_versions": accepted.report_versions_observed}, approved)
    versions = accepted.versions.set_index("report_id")
    row = versions.loc[shifted]
    assert row["parse_status"] == "ok_title_accepted"
    assert (row["listing_rows"], row["placeholder_rows"], row["games_listed"], row["games_resolved"]) == (104, 4, 14, 1)
    assert row["identical_listings_to_previous"] == True  # noqa: E712   (same multiset as 05PM, the previous parsed version)
    assert versions.loc[broken, "parse_status"] == "excluded"
    shifted_rows = accepted.listings[accepted.listings["report_id"] == shifted]
    assert len(shifted_rows) == 108
    assert list(shifted_rows["row_number"]) == list(range(1, 109))
    assert set(shifted_rows["report_slot_et"]) == {pd.Timestamp("2024-01-31T18:00:00")}
    assert accepted.parse_counts[2024] == {"ok": 1, "ok_title_accepted": 1, "excluded": 1}
    assert accepted.title_counts[2024] == {"ok": 1, "failed": 1, "unparsed": 1}

    # an approval must match the body: a different sha256 is not applied and shows as drift
    stale = json.loads(json.dumps(approved))
    stale["report_versions"][shifted]["sha256"] = "0" * 64
    not_applied = availability_build.assemble_part_b(raw_root, part_a, stale)
    assert not_applied.report_versions_observed[shifted]["action"] == "exclude"
    assert not_applied.versions.set_index("report_id").loc[shifted, "parse_status"] == "excluded"
    with pytest.raises(availability_build.AvailabilityBuildError, match="report_versions drift"):
        availability_build.validate_exceptions(
            {**observed, "report_versions": not_applied.report_versions_observed}, stale,
        )
    # accept_title can only accept a title-window failure
    wrong = json.loads(json.dumps(approved))
    wrong["report_versions"][broken] = {**wrong["report_versions"][shifted], "sha256": broken_sha, "slot_label": "07PM", "title_printed_et": None}
    still_excluded = availability_build.assemble_part_b(raw_root, part_a, wrong)
    assert still_excluded.report_versions_observed[broken]["action"] == "exclude"
    # an explicit exclude of the shifted report keeps its printed time
    excluded = json.loads(json.dumps(approved))
    excluded["report_versions"][shifted] = {
        "action": "exclude", "sha256": sha, "failed_check": "title_window",
        "title_printed_et": "2024-01-31T17:30:00", "slot_label": "06PM", "note": "Mislabelled upload.",
    }
    part = availability_build.assemble_part_b(raw_root, part_a, excluded)
    availability_build.validate_exceptions({**observed, "report_versions": part.report_versions_observed}, excluded)
    row = part.versions.set_index("report_id").loc[shifted]
    assert row["parse_status"] == "excluded"
    assert row["report_time_printed_et"] == pd.Timestamp("2024-01-31T17:30:00")
    assert shifted not in set(part.listings["report_id"])


# --------------------------------------------------------------------------
# partitions
# --------------------------------------------------------------------------


def _roster_table():
    frame = pd.DataFrame.from_records([
        {
            "game_id": "0022500001", "person_id": person_id, "season": 2026, "game_type": "regular",
            "game_date": date(2025, 10, 21), "nba_team_id": OKC, "team_tricode_raw": "OKC",
            "franchise_id": "OKC", "is_home": True, "roster_status": status,
            "first_name_raw": "A", "family_name_raw": "B", "jersey_num": None,
            "starter_position_raw": None, "comment_raw": comment,
            "comment_code": None if comment is None else comment.split(" - ")[0], "minutes_raw": minutes,
        }
        for person_id, status, comment, minutes in (
            (1, "played", None, "32:10"), (2, "active_not_played", "DNP - Coach's Decision", None),
            (3, "inactive", None, None),
        )
    ])
    return availability_build.table_from_frame(frame, availability_build.GAME_ROSTERS_SCHEMA), frame


def test_write_partition_is_deterministic_and_zstd(tmp_path):
    """Would fail if two writes of one table differed or compression drifted."""
    table, frame = _roster_table()
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()

    first = availability_build.write_partition(table, tmp_path / "one", 2026, "game_rosters")
    second = availability_build.write_partition(table, tmp_path / "two", 2026, "game_rosters")

    assert first == tmp_path / "one/season=2026/game_rosters.parquet"
    assert second == tmp_path / "two/season=2026/game_rosters.parquet"
    assert first.read_bytes() == second.read_bytes()
    metadata = pq.ParquetFile(first).metadata
    assert metadata.num_rows == 3
    for group in range(metadata.num_row_groups):
        for column in range(metadata.row_group(group).num_columns):
            assert metadata.row_group(group).column(column).compression == "ZSTD"
    reread = pq.read_table(first)
    assert reread.schema == availability_build.GAME_ROSTERS_SCHEMA
    assert reread.equals(table)
    # a second table lands beside the first in the same season directory
    schedule = availability_build.table_from_frame(
        pd.DataFrame.from_records([{
            "game_id": "0022500001", "season": 2026, "game_type": "regular", "game_date": date(2025, 10, 21),
            "scheduled_start_utc": datetime(2025, 10, 21, 23, 30, tzinfo=timezone.utc),
            "scheduled_start_et_raw": "2025-10-21T19:30:00Z", "game_code_raw": "20251021/HOUOKC",
        }]),
        availability_build.GAME_SCHEDULE_SCHEMA,
    )
    assert availability_build.write_partition(schedule, tmp_path / "one", 2026, "game_schedule") == (
        tmp_path / "one/season=2026/game_schedule.parquet"
    )
    # never overwrites
    with pytest.raises(availability_build.AvailabilityBuildError, match="exists"):
        availability_build.write_partition(table, tmp_path / "one", 2026, "game_rosters")
    # the stage directory must exist
    with pytest.raises(availability_build.AvailabilityBuildError, match="stage"):
        availability_build.write_partition(table, tmp_path / "missing", 2026, "game_rosters")
    # required columns reject nulls; extra frame columns are dropped, missing ones fail
    with pytest.raises(availability_build.AvailabilityBuildError, match="nulls"):
        availability_build.table_from_frame(
            frame.assign(game_type=None), availability_build.GAME_ROSTERS_SCHEMA,
        )
    assert availability_build.table_from_frame(
        frame.assign(extra=1), availability_build.GAME_ROSTERS_SCHEMA,
    ).schema == availability_build.GAME_ROSTERS_SCHEMA
    with pytest.raises(availability_build.AvailabilityBuildError, match="minutes_raw"):
        availability_build.table_from_frame(
            frame.drop(columns=["minutes_raw"]), availability_build.GAME_ROSTERS_SCHEMA,
        )
    with pytest.raises(availability_build.AvailabilityBuildError, match="Arrow"):
        availability_build.table_from_frame(
            frame.assign(person_id=["x", "y", "z"]), availability_build.GAME_ROSTERS_SCHEMA,
        )


def test_logical_row_sha256_ignores_parquet_encoding_but_not_schema():
    """Would fail if the logical digest tracked bytes instead of typed rows."""
    table, _frame = _roster_table()
    schema = availability_build.GAME_ROSTERS_SCHEMA

    digest = availability_build.logical_row_sha256(table, schema)
    assert len(digest) == 64

    def roundtrip(**options):
        sink = io.BytesIO()
        pq.write_table(table, sink, **options)
        return pq.read_table(io.BytesIO(sink.getvalue()))

    assert availability_build.logical_row_sha256(roundtrip(compression="snappy", use_dictionary=True), schema) == digest
    assert availability_build.logical_row_sha256(roundtrip(compression="zstd", use_dictionary=False), schema) == digest
    chunked = pa.concat_tables([table.slice(0, 1), table.slice(1)])
    assert chunked.column(0).num_chunks == 2
    assert availability_build.logical_row_sha256(chunked, schema) == digest
    assert availability_build.logical_row_sha256(
        table.replace_schema_metadata({b"pandas": b"{}"}), schema,
    ) == digest
    # rows, order and schema all matter
    assert availability_build.logical_row_sha256(table.slice(0, 2), schema) != digest
    reversed_rows = table.take(pa.array([2, 1, 0]))
    assert availability_build.logical_row_sha256(reversed_rows, schema) != digest
    narrowed = table.set_column(0, pa.field("game_id", pa.string(), nullable=False), table["game_id"].cast(pa.string()))
    assert availability_build.logical_row_sha256(narrowed, narrowed.schema) != digest
    # the schema argument must be the table's: a digest under another schema is refused
    for other_table, other_schema in ((narrowed, schema), (table, availability_build.INJURY_REPORT_LISTINGS_SCHEMA)):
        with pytest.raises(availability_build.AvailabilityBuildError, match="schema"):
            availability_build.logical_row_sha256(other_table, other_schema)


def test_assemble_part_b_flags_ambiguous_november_titles_and_fails_gap_titles(tmp_path, monkeypatch):
    """Would fail if a repeated-hour time were guessed or a skipped-hour time published."""
    ambiguous = "Injury-Report_2024-11-03_01AM"    # the repeated 1 AM hour of the November switch
    gap = "Injury-Report_2024-03-10_02AM"          # the skipped 2 AM hour of the March switch
    months = {
        "2024-03": {"present": {gap: JANUARY_REPORT + ".pdf"}, "unavailable": ()},
        "2024-11": {"present": {ambiguous: JANUARY_REPORT + ".pdf"}, "unavailable": ()},
    }
    raw_root, player_games_root = seed_raw_tree(tmp_path, monkeypatch, months=months)
    index = box_rosters.load_player_games_index(player_games_root)
    part_a = availability_build.assemble_part_a(raw_root, index, _empty_ledger())
    real_parse = injury_report_parse.parse_report
    printed_by_report = {ambiguous: datetime(2024, 11, 3, 1, 30), gap: datetime(2024, 3, 10, 2, 30)}

    def fake_parse(payload, *, report_id=None):
        parsed = real_parse(payload, report_id=report_id)
        return injury_report_parse.ParsedReport(
            pages=parsed.pages, title_text=parsed.title_text,
            title_printed_et=printed_by_report[report_id], listings=parsed.listings,
        )

    monkeypatch.setattr(injury_report_parse, "parse_report", fake_parse)
    part = availability_build.assemble_part_b(raw_root, part_a, _empty_ledger())

    versions = part.versions.set_index("report_id")
    row = versions.loc[ambiguous]
    assert row["parse_status"] == "ok"
    assert row["report_time_printed_et"] == pd.Timestamp("2024-11-03T01:30:00")
    assert pd.isna(row["report_time_printed_utc"])
    assert row["report_time_printed_utc_ambiguous"] == True  # noqa: E712
    assert row["listing_rows"] == 104
    row = versions.loc[gap]
    assert row["parse_status"] == "excluded"
    assert row["report_time_printed_et"] == pd.Timestamp("2024-03-10T02:30:00")
    assert pd.isna(row["report_time_printed_utc"]) and row["report_time_printed_utc_ambiguous"] == False  # noqa: E712
    assert part.report_versions_observed == {
        gap: {
            "action": "exclude", "sha256": versions.loc[gap, "sha256"], "failed_check": "title_gap",
            "title_printed_et": "2024-03-10T02:30:00", "slot_label": "02AM",
        },
    }
    # 2024-11-03 lies in version season 2025 (October to September)
    assert part.title_counts == {
        2024: {"ok": 0, "failed": 1, "unparsed": 0}, 2025: {"ok": 1, "failed": 0, "unparsed": 0},
    }
    assert part.parse_counts == {
        2024: {"ok": 0, "ok_title_accepted": 0, "excluded": 1},
        2025: {"ok": 1, "ok_title_accepted": 0, "excluded": 0},
    }
    assert _col(part.versions, "season") == [2024, 2025]
    assert set(part.listings["report_id"]) == {ambiguous}
    # accept_title cannot rescue a time that never happened
    approved = _ledger(report_versions={gap: {
        "action": "accept_title", "sha256": versions.loc[gap, "sha256"], "failed_check": None,
        "title_printed_et": "2024-03-10T02:30:00", "slot_label": "02AM", "note": "try",
    }})
    assert availability_build.assemble_part_b(raw_root, part_a, approved).report_versions_observed[gap]["action"] == "exclude"


# ==========================================================================
# Task 15: gates, derivative agreement, manifest, validation and publication
# ==========================================================================


MANIFEST_KEYS = {
    "schema_version", "scope", "schema", "versions", "sources", "references",
    "exception_ledger", "partitions", "validation",
}
EXPECTED_PARTITIONS = {
    f"season={season}/{name}.parquet"
    for season in range(2012, 2027) for name in ("game_rosters", "game_schedule")
} | {
    f"season={season}/{name}.parquet"
    for season in range(2019, 2027) for name in ("injury_report_versions", "injury_report_listings")
}
SIXPM_REPORT = "Injury-Report_2024-01-31_06PM"
SEVENPM_REPORT = "Injury-Report_2024-01-31_07PM"
LEFT_ANKLE = "Injury/Illness - Left Ankle; Sprain"
LEFT_ANKLE_CUT = "Injury/Illness - Left Ankle;"
RIGHT_ANKLE = "Injury/Illness - Right Ankle; Sprain"


def _our_listing(
    report_id, row_number, *, season=2024, player="Ball, Lonzo", status="Out", reason=LEFT_ANKLE,
    placeholder=None, team="Chicago Bulls", matchup="CHI@CHA", game_date=date(2024, 1, 31),
    category=None, previous=None, slot_et=datetime(2024, 1, 31, 17),
):
    away, home = matchup.split("@")
    return {
        "report_id": report_id, "season": season, "row_number": row_number, "page": 1,
        "game_date": game_date, "game_time_raw": "07:00 (ET)", "matchup_raw": matchup,
        "away_tricode": away, "home_tricode": home, "team_raw": team,
        "is_placeholder": placeholder is not None, "placeholder_raw": placeholder,
        "player_name_raw": None if placeholder else player,
        "current_status_raw": None if placeholder else status,
        "reason_raw": None if placeholder else reason,
        "category_raw": category, "previous_status_raw": previous,
        "game_id": None, "game_resolution": "no_box_score_game", "nba_team_id": None,
        "franchise_id": None, "person_id": None,
        "person_resolution": "placeholder" if placeholder else "unresolved_game",
        "report_slot_et": slot_et,
    }


def _their_row(
    *, when=datetime(2024, 1, 31, 17, 30, 15), player="Ball, Lonzo", status="Out", reason=LEFT_ANKLE,
    team="Chicago Bulls", matchup="CHI@CHA", game_date=date(2024, 1, 31), category=None, previous=None,
    season=2024,
):
    away, home = matchup.split("@")
    return derivative_tests._player_row(
        season=season, report_datetime=when, game_date=game_date, game_time="07:00 (ET)",
        report_matchup=matchup, away_team_abbr=away, home_team_abbr=home, team=team,
        report_player_name=player, current_status=status, reason=reason, category=category,
        previous_status=previous, game_id=None, team_id=None, person_id=None, match_method=None,
    )


def _their_frame(rows):
    return injury_derivative.comparison_rows(derivative_tests.derivative_parquet_bytes(rows))


def _publish_derivative(data_dir, season, rows):
    cache.publish_bytes(
        injury_derivative.asset_path(data_dir, season),
        derivative_tests.derivative_parquet_bytes(rows),
        {**injury_derivative.expected_metadata(season), "release_asset_updated_at": "2026-11-03T06:10:00Z"},
        injury_derivative.validate_asset,
    )


def _stage(root: Path, name: str = "stage") -> Path:
    stage = root / name
    stage.mkdir(parents=True)
    return stage


def _build(raw_root, player_games_root, stage, ledger):
    return availability_build.build_from_roots(raw_root, player_games_root, stage, exceptions_path=ledger)


def _tree_bytes(root: Path) -> dict:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def _paths_in(value, key_names=("path", "body_path", "sidecar_path")):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in key_names and isinstance(item, str):
                yield item
            yield from _paths_in(item, key_names)
    elif isinstance(value, list):
        for item in value:
            yield from _paths_in(item, key_names)


# --------------------------------------------------------------------------
# compare_version / derivative_agreement
# --------------------------------------------------------------------------


def test_compare_version_classifies_rows_and_normalises_placeholders():
    """Would fail if a stored placeholder phrase, whitespace or an artifact row read as a disagreement."""
    ours = pd.DataFrame.from_records([
        _our_listing(JANUARY_REPORT, 1),
        _our_listing(JANUARY_REPORT, 2, player="LaVine, Zach", reason=RIGHT_ANKLE, team="Chicago  Bulls"),
        _our_listing(JANUARY_REPORT, 3, placeholder="NOT YET SUBMITTED", team="Charlotte Hornets"),
    ])
    theirs = _their_frame([
        _their_row(reason=LEFT_ANKLE_CUT),
        _their_row(player="LaVine, Zach", reason=RIGHT_ANKLE + " "),
        # the derivative stores the placeholder phrase in ``reason``
        _their_row(player=None, status=None, reason="NOT YET SUBMITTED", team="Charlotte Hornets"),
        # a derivative artifact: null name and status, reason none of the phrases
        _their_row(player=None, status=None, reason=",", team="Charlotte Hornets"),
    ])

    result = availability_build.compare_version(ours, theirs)

    assert result["report_id"] == JANUARY_REPORT and result["season"] == 2024
    assert (result["agree_all"], result["agree_except_reason"], result["only_ours"], result["only_theirs"]) == (2, 1, 0, 0)
    assert result["derivative_artifacts"] == 1
    assert result["patterns"] == {
        f"reason: ours {LEFT_ANKLE!r} / theirs {LEFT_ANKLE_CUT!r}": {"count": 1, "report_ids": [JANUARY_REPORT]},
    }
    # a status difference is a row only in ours plus a row only in theirs, keyed by pattern
    changed = _their_frame([_their_row(status="Questionable"), _their_row(player="LaVine, Zach", reason=RIGHT_ANKLE)])
    result = availability_build.compare_version(ours.iloc[:2], changed)
    assert (result["agree_all"], result["agree_except_reason"], result["only_ours"], result["only_theirs"]) == (1, 0, 1, 1)
    assert sorted(result["patterns"]) == sorted([
        f"only_ours: game_date='2024-01-31' matchup='CHI@CHA' team='Chicago Bulls' kind='player' "
        f"player_name='Ball, Lonzo' status='Out' reason={LEFT_ANKLE!r}",
        f"only_theirs: game_date='2024-01-31' matchup='CHI@CHA' team='Chicago Bulls' kind='player' "
        f"player_name='Ball, Lonzo' status='Questionable' reason={LEFT_ANKLE!r}",
    ])
    assert all(entry == {"count": 1, "report_ids": [JANUARY_REPORT]} for entry in result["patterns"].values())
    # multisets: a repeated row on one side is a row only on that side
    doubled = pd.concat([ours.iloc[:2], ours.iloc[[1]]], ignore_index=True)
    result = availability_build.compare_version(doubled, theirs)
    assert (result["agree_all"], result["agree_except_reason"], result["only_ours"]) == (1, 1, 1)
    # one version per call
    with pytest.raises(availability_build.AvailabilityBuildError, match="one report_id"):
        availability_build.compare_version(
            pd.concat([ours, pd.DataFrame.from_records([_our_listing(SIXPM_REPORT, 1)])], ignore_index=True), theirs,
        )


def test_compare_version_drops_layout_columns_by_season():
    """Would fail if category / previous_status were compared for a layout that lacks them."""
    def ours(season, **overrides):
        return pd.DataFrame.from_records([_our_listing(JANUARY_REPORT, 1, season=season, **overrides)])

    theirs = _their_frame([_their_row(category="Injury/Illness", previous="Questionable")])
    # 2019 carries both columns: a differing category is a disagreement that is not reason-only
    result = availability_build.compare_version(ours(2019, category="Injury/Illness", previous="Doubtful"), theirs)
    assert (result["agree_all"], result["agree_except_reason"], result["only_ours"], result["only_theirs"]) == (0, 0, 1, 1)
    assert result["fields"] == [
        "game_date", "matchup", "team", "kind", "player_name", "status", "reason", "category", "previous_status",
    ]
    assert availability_build.compare_version(
        ours(2019, category="Injury/Illness", previous="Questionable"), theirs,
    )["agree_all"] == 1
    # 2020 drops category and keeps previous_status
    result = availability_build.compare_version(ours(2020, previous="Questionable"), theirs)
    assert result["agree_all"] == 1 and result["fields"][-1] == "previous_status" and "category" not in result["fields"]
    assert availability_build.compare_version(ours(2020, previous="Doubtful"), theirs)["only_ours"] == 1
    # 2021 onwards drops both
    for season in (2021, 2024, 2026):
        result = availability_build.compare_version(ours(season), theirs)
        assert result["agree_all"] == 1
        assert result["fields"] == ["game_date", "matchup", "team", "kind", "player_name", "status", "reason"]


def test_derivative_agreement_joins_on_printed_time_and_lists_unmatched_versions(tmp_path):
    """Would fail if the join used slots, seconds mattered, or an unmatched version were an error."""
    quarter_2026 = QUARTER_REPORT_2026
    versions = pd.DataFrame.from_records([
        {"report_id": JANUARY_REPORT, "season": 2024, "parse_status": "ok",
         "report_time_printed_et": pd.Timestamp("2024-01-31T17:30:00")},
        {"report_id": SIXPM_REPORT, "season": 2024, "parse_status": "ok_title_accepted",
         "report_time_printed_et": pd.Timestamp("2024-01-31T18:30:00")},
        {"report_id": SEVENPM_REPORT, "season": 2024, "parse_status": "excluded",
         "report_time_printed_et": pd.NaT},
        {"report_id": quarter_2026, "season": 2026, "parse_status": "ok",
         "report_time_printed_et": pd.Timestamp("2026-01-10T00:00:00")},
    ])
    listings = pd.DataFrame.from_records([
        _our_listing(JANUARY_REPORT, 1),
        _our_listing(JANUARY_REPORT, 2, player="LaVine, Zach", reason=RIGHT_ANKLE),
        _our_listing(JANUARY_REPORT, 3, placeholder="NOT YET SUBMITTED", team="Charlotte Hornets"),
        _our_listing(SIXPM_REPORT, 1, slot_et=datetime(2024, 1, 31, 18)),
        _our_listing(quarter_2026, 1, season=2026, game_date=date(2026, 1, 10), slot_et=datetime(2026, 1, 10)),
    ])
    part_b = availability_build.PartB(
        versions=versions, listings=listings, report_versions_observed={}, team_resolution_observed={},
        inventories=[], slot_totals={}, parse_counts={}, title_counts={}, placeholder_counts={},
    )
    _publish_derivative(tmp_path, 2024, [
        _their_row(reason=LEFT_ANKLE_CUT),                                   # 17:30:15 -> minute 17:30
        _their_row(player="LaVine, Zach", reason=RIGHT_ANKLE),
        _their_row(player=None, status=None, reason="NOT YET SUBMITTED", team="Charlotte Hornets"),
        _their_row(player=None, status=None, reason=",", team="Charlotte Hornets"),
        _their_row(when=datetime(2024, 1, 31, 21, 55)),                     # no version of ours prints 21:55
    ])
    derivative_root = tmp_path / "raw/sportsdataverse/nba_stats_injury_report"

    agreement = availability_build.derivative_agreement(part_b, derivative_root)

    assert set(agreement) == {2024, 2026}
    season = agreement[2024]
    asset = injury_derivative.asset_path(tmp_path, 2024)
    assert season["asset"]["body_path"] == "raw/sportsdataverse/nba_stats_injury_report/injury_report_2024.parquet"
    assert season["asset"]["body_sha256"] == hashlib.sha256(asset.read_bytes()).hexdigest()
    assert season["asset"]["sidecar_path"] == season["asset"]["body_path"] + ".meta.json"
    assert season["asset"]["identity"] == {"season": 2024}
    assert {key: season[key] for key in (
        "versions_ours", "versions_excluded", "versions_theirs", "versions_compared", "versions_disagreeing",
        "agree_all", "agree_except_reason", "only_ours", "only_theirs", "derivative_artifacts",
    )} == {
        "versions_ours": 2, "versions_excluded": 1, "versions_theirs": 2, "versions_compared": 1,
        "versions_disagreeing": 1, "agree_all": 2, "agree_except_reason": 1, "only_ours": 0,
        "only_theirs": 0, "derivative_artifacts": 1,
    }
    assert season["unmatched_ours"] == [SIXPM_REPORT]
    assert season["unmatched_theirs"] == ["2024-01-31T21:55:00"]
    assert season["patterns"] == {
        f"reason: ours {LEFT_ANKLE!r} / theirs {LEFT_ANKLE_CUT!r}": {"count": 1, "report_ids": [JANUARY_REPORT]},
    }
    # a season without a pinned asset compares nothing and lists its versions as unmatched
    assert agreement[2026]["asset"] is None
    assert agreement[2026]["versions_ours"] == 1 and agreement[2026]["versions_theirs"] == 0
    assert agreement[2026]["versions_compared"] == 0 and agreement[2026]["unmatched_ours"] == [quarter_2026]
    assert agreement[2026]["unmatched_theirs"] == [] and agreement[2026]["patterns"] == {}
    # JSON-ready: every value survives the manifest encoder
    availability_build.manifest_bytes({str(key): {k: v for k, v in value.items()} for key, value in agreement.items()})
    # the root must be the derivative raw directory; a damaged asset fails closed
    with pytest.raises(availability_build.AvailabilityBuildError, match="nba_stats_injury_report"):
        availability_build.derivative_agreement(part_b, tmp_path / "raw")
    asset.write_bytes(asset.read_bytes() + b"x")
    with pytest.raises(availability_build.AvailabilityBuildError, match="injury_report_2024"):
        availability_build.derivative_agreement(part_b, derivative_root)


# --------------------------------------------------------------------------
# gates and build
# --------------------------------------------------------------------------


def test_gates_fail_closed_and_pass_with_exact_ledger_entries(tmp_path, monkeypatch):
    """Would fail if a disagreement, a low rate or a season mismatch slipped past a gate."""
    # appearance disagreement without approval -> fail; with the exact entry -> applied
    root = tmp_path / "appearance"
    raw_root, player_games_root, ledger = seed_full_tree(root, monkeypatch, credited_extra=[credited_dieng_row()])
    with pytest.raises(
        availability_build.AvailabilityBuildError,
        match=rf"appearance_consistency missing approvals.*{DIENG}",
    ):
        _build(raw_root, player_games_root, _stage(root, "one"), ledger)
    assert not any((root / "one").iterdir())
    key = f"2026|{OPENER.game_id}|{DIENG}"
    entry = {"comment_raw": "DNP - Coach's Decision", "minutes_raw": None, "appearance": "credited"}
    ledger = write_ledger(root, full_ledger(appearance_consistency={key: {**entry, "note": "league credit"}}))
    manifest = _build(raw_root, player_games_root, _stage(root, "two"), ledger)
    applied = manifest["exception_ledger"]["applied"]
    assert applied["appearance_consistency"] == {key: entry}
    assert set(applied["report_versions"]) == set(REUSED_REPORTS)
    assert all("note" not in value for section in applied.values() for value in section.values())
    assert manifest["exception_ledger"]["counts"] == {
        "appearance_consistency": 1, "report_versions": 4, "schedule_dates": 0, "team_resolution": 0,
    }
    assert manifest["validation"]["parse_counts"]["2020"] == {"ok": 0, "ok_title_accepted": 1, "excluded": 0}
    # a stale approval fails too
    stale = write_ledger(root, full_ledger(appearance_consistency={
        key: {**entry, "note": "league credit"},
        f"2026|{OPENER.game_id}|{DORT}": {**entry, "note": "never observed"},
    }))
    with pytest.raises(availability_build.AvailabilityBuildError, match="stale approvals"):
        _build(raw_root, player_games_root, _stage(root, "three"), stale)

    # identity rate below the floor in one season -> error naming the season
    root = tmp_path / "rates"
    raw_root, player_games_root, ledger = seed_full_tree(root, monkeypatch, full_roster=False)
    with pytest.raises(availability_build.AvailabilityBuildError, match=r"season 2024.*identit") as excinfo:
        _build(raw_root, player_games_root, _stage(root, "one"), ledger)
    assert "0.98" in str(excinfo.value)
    # row rate fine but identity rate low -> still an error
    root = tmp_path / "identity"
    raw_root, player_games_root, ledger = seed_full_tree(root, monkeypatch)
    real_metrics = availability_resolve.resolution_metrics

    def lowered(listings):
        metrics = real_metrics(listings)
        metrics[2024]["row_rate"] = 0.99
        metrics[2024]["identity_rate"] = 0.5
        return metrics

    monkeypatch.setattr(availability_resolve, "resolution_metrics", lowered)
    with pytest.raises(availability_build.AvailabilityBuildError, match=r"season 2024.*identity rate 0\.5"):
        _build(raw_root, player_games_root, _stage(root, "one"), ledger)
    monkeypatch.setattr(availability_resolve, "resolution_metrics", real_metrics)
    assert _build(raw_root, player_games_root, _stage(root, "two"), ledger)["validation"]["resolution"]["2024"] == {
        "rows": 11, "rows_resolved": 11, "row_rate": 1.0, "identities": 11, "identities_resolved": 11,
        "identity_rate": 1.0, "listed_not_rostered": {"rows": 0, "identities": 0},
        "unresolved_matchups": manifest["validation"]["resolution"]["2024"]["unresolved_matchups"],
    }

    # version-season mismatch: the 2024-01-31 report (whose CHI@CHA game resolves
    # to season 2024) accepted under a season-2025 slot
    root = tmp_path / "season"
    shifted = "Injury-Report_2024-11-15_05PM"
    months = {**FULL_MONTHS, "2024-11": {"present": {shifted: JANUARY_REPORT + ".pdf"}, "unavailable": ()}}
    document = full_ledger()
    del document["report_versions"]["Injury-Report_2024-11-15_01PM"]
    document["report_versions"][shifted] = accept_title_entry(
        shifted, JANUARY_REPORT + ".pdf", "2024-01-31T17:30:00",
    )
    raw_root, player_games_root, ledger = seed_full_tree(root, monkeypatch, months=months, ledger=document)
    with pytest.raises(
        availability_build.AvailabilityBuildError,
        match=rf"version.season.*{shifted}.*{BULLS_HORNETS.game_id}",
    ):
        _build(raw_root, player_games_root, _stage(root, "one"), ledger)


def test_build_from_roots_writes_46_partitions_and_a_deterministic_manifest(tmp_path, monkeypatch):
    """Would fail if the layout, determinism or manifest shape drifted."""
    raw_root, player_games_root, ledger = seed_full_tree(tmp_path, monkeypatch)
    stage_a = _stage(tmp_path, "stage-a")

    manifest = _build(raw_root, player_games_root, stage_a, ledger)

    tree = _tree_bytes(stage_a)
    partitions = {name for name in tree if name.endswith(".parquet")}
    assert partitions == EXPECTED_PARTITIONS and len(partitions) == 46
    assert set(tree) == partitions | {"_manifest.json"}
    for name in sorted(partitions):
        assert pq.read_metadata(stage_a / name).num_rows > 0, name
    assert tree["_manifest.json"] == availability_build.manifest_bytes(manifest)
    assert json.loads(tree["_manifest.json"]) == manifest
    assert set(manifest) == MANIFEST_KEYS
    text = tree["_manifest.json"].decode("utf-8")
    assert str(tmp_path) not in text
    assert "fetched_at" not in text and "checked_at" not in text
    assert date.today().isoformat() not in text
    assert all(not path.startswith("/") and "\\" not in path for path in _paths_in(manifest))
    assert tree["_manifest.json"] == (
        json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")
    with pytest.raises(ValueError):
        availability_build.manifest_bytes({"rate": float("nan")})

    # a second build from the same trees and ledger is byte-identical
    stage_b = _stage(tmp_path, "stage-b")
    assert _build(raw_root, player_games_root, stage_b, ledger) == manifest
    assert _tree_bytes(stage_b) == tree

    # content checks
    assert manifest["schema_version"] == 1
    assert manifest["scope"]["roster_seasons"] == list(range(2012, 2027))
    assert manifest["scope"]["report_seasons"] == list(range(2019, 2027))
    assert manifest["scope"]["game_types"] == ["playoff", "regular"]
    assert manifest["scope"]["slot_window"] == ["2018-12-17", "2026-09-30"]
    assert set(manifest["scope"]["grids"]) == {"hour", "quarter"}
    assert set(manifest["schema"]) == set(availability_build.PARTITION_SCHEMAS)
    assert manifest["schema"]["game_schedule"][4] == {
        "name": "scheduled_start_utc", "arrow_type": "timestamp[us, tz=UTC]", "nullable": False,
    }
    assert manifest["versions"]["pdfplumber"] == "0.11.10"
    assert manifest["versions"]["pdfminer-six"] == "20260107"
    assert manifest["versions"]["pypdfium2"] == "5.14.0"
    assert {"nba_api", "pandas", "pyarrow", "python"} <= set(manifest["versions"])
    assert set(manifest["partitions"]) == {str(season) for season in range(2012, 2027)}
    rosters_2024 = manifest["partitions"]["2024"]["game_rosters"]
    assert set(rosters_2024) == {"path", "rows", "bytes", "sha256", "row_sha256"}
    assert rosters_2024["path"] == "season=2024/game_rosters.parquet" and rosters_2024["rows"] == 12
    assert rosters_2024["sha256"] == hashlib.sha256(tree[rosters_2024["path"]]).hexdigest()
    assert rosters_2024["row_sha256"] == availability_build.logical_row_sha256(
        pq.read_table(stage_a / rosters_2024["path"]), availability_build.GAME_ROSTERS_SCHEMA,
    )
    assert set(manifest["partitions"]["2012"]) == {"game_rosters", "game_schedule"}
    assert set(manifest["partitions"]["2019"]) == {
        "game_rosters", "game_schedule", "injury_report_versions", "injury_report_listings",
    }
    assert manifest["partitions"]["2020"]["injury_report_versions"]["rows"] == 1
    validation = manifest["validation"]
    assert validation["coverage"]["games"] == 15 and validation["coverage"]["box_roster_pairs"] == 30
    assert validation["coverage"]["slots"]["hour"] == {"present": 7, "unavailable": 2, "rechecked": 2}
    assert validation["coverage"]["slots"]["quarter"] == {"present": 1, "unavailable": 2, "rechecked": 2}
    assert validation["coverage"]["reports"] == {"present": 8, "parsed": 8, "excluded": 0}
    assert validation["rows"] == {
        name: sum(manifest["partitions"][season][name]["rows"]
                  for season in manifest["partitions"] if name in manifest["partitions"][season])
        for name in availability_build.PARTITION_SCHEMAS
    }
    assert validation["parse_counts"]["2024"] == {"ok": 1, "ok_title_accepted": 0, "excluded": 0}
    assert validation["title_counts"]["2025"] == {"ok": 0, "failed": 1, "unparsed": 0}
    assert validation["placeholder_counts"]["2026"] == {"NOT YET SUBMITTED": 22}
    assert validation["resolution"]["2024"]["identity_rate"] == 1.0
    assert validation["resolution"]["2024"]["unresolved_matchups"]["CLE@MEM"] == 2
    assert validation["gates"] == [
        "coverage", "exception_ledger", "version_season", "team_resolution", "person_resolution", "layout",
    ]
    sizes = validation["roster_sizes"]
    assert sizes["by_season"]["2024"] == {"team_games": 2, "active_min": 2, "active_max": 2, "inactive_empty": 0}
    assert sizes["by_season"]["2026"]["inactive_empty"] == 1
    assert {"game_id": OPENER.game_id, "nba_team_id": HOU, "active": 2, "inactive": 0} in sizes["outliers"]
    agreement = validation["derivative_agreement"]
    assert set(agreement) == {str(season) for season in range(2019, 2027)}
    assert "asset" not in agreement["2024"]
    assert agreement["2024"]["unmatched_ours"] == [JANUARY_REPORT]
    assert agreement["2024"]["unmatched_theirs"] == ["2024-01-10T17:30:00"]
    assert agreement["2020"]["unmatched_ours"] == ["Injury-Report_2019-11-15_01PM"]
    assert agreement["2020"]["versions_theirs"] == 0

    # the stage must be an existing empty directory
    with pytest.raises(availability_build.AvailabilityBuildError, match="stage"):
        _build(raw_root, player_games_root, stage_a, ledger)
    with pytest.raises(availability_build.AvailabilityBuildError, match="stage"):
        _build(raw_root, player_games_root, tmp_path / "missing", ledger)


def test_manifest_binds_every_raw_pair_the_player_games_manifest_controls_and_ledger(tmp_path, monkeypatch):
    """Would fail if a consumed body, the worklist reference, the controls or the ledger were unbound."""
    raw_root, player_games_root, ledger = seed_full_tree(tmp_path, monkeypatch)
    index = box_rosters.load_player_games_index(player_games_root)

    manifest = _build(raw_root, player_games_root, _stage(tmp_path), ledger)

    sources = manifest["sources"]
    assert set(sources) == {"box_rosters", "injury_reports", "derivative", "gamebooks"}
    bodies = sources["box_rosters"]
    assert len(bodies) == 2 * len(index.games) == 30
    assert [entry["body_path"] for entry in bodies] == sorted(entry["body_path"] for entry in bodies)
    for entry in bodies:
        assert set(entry) == {
            "body_path", "body_sha256", "body_content_length",
            "sidecar_path", "sidecar_sha256", "sidecar_content_length", "identity",
        }
        body = tmp_path / entry["body_path"]
        sidecar = tmp_path / entry["sidecar_path"]
        assert entry["body_path"].startswith("raw/stats_nba/box_rosters/season=")
        assert entry["sidecar_path"] == entry["body_path"] + ".meta.json"
        assert entry["body_sha256"] == hashlib.sha256(body.read_bytes()).hexdigest()
        assert entry["body_content_length"] == body.stat().st_size
        assert entry["sidecar_sha256"] == hashlib.sha256(sidecar.read_bytes()).hexdigest()
        assert entry["sidecar_content_length"] == sidecar.stat().st_size
        assert set(entry["identity"]) == {"game_id", "endpoint", "season"}
    assert {(e["identity"]["game_id"], e["identity"]["endpoint"]) for e in bodies} == {
        (game.game_id, endpoint) for game in index.games for endpoint in ("traditional", "summary")
    }

    reports = sources["injury_reports"]
    assert set(reports) == {"pdfs", "inventories"}
    assert len(reports["pdfs"]) == 8 == len(FULL_REPORTS)
    assert sorted(entry["identity"]["report_id"] for entry in reports["pdfs"]) == sorted(FULL_REPORTS)
    for entry in reports["pdfs"]:
        slot = injury_reports.slot_from_report_id(entry["identity"]["report_id"])
        assert entry["body_path"] == f"raw/nba_injury_reports/pdfs/{slot.month}/{slot.file_name}"
        assert entry["body_sha256"] == hashlib.sha256((tmp_path / entry["body_path"]).read_bytes()).hexdigest()
        assert entry["identity"] == {"report_id": slot.report_id, "grid": slot.grid, "month": slot.month}
    assert [entry["month"] for entry in reports["inventories"]] == sorted(FULL_MONTHS)
    for entry in reports["inventories"]:
        assert set(entry) == {"month", "generation", "path", "sha256", "bytes"}
        assert entry["generation"] == 1
        assert entry["path"] == f"raw/nba_injury_reports/inventory/{entry['month']}/gen-0001.json"
        payload = (tmp_path / entry["path"]).read_bytes()
        assert (entry["sha256"], entry["bytes"]) == (hashlib.sha256(payload).hexdigest(), len(payload))

    assert [entry["identity"] for entry in sources["derivative"]] == [{"season": 2024}, {"season": 2026}]
    for entry in sources["derivative"]:
        path = injury_derivative.asset_path(tmp_path, entry["identity"]["season"])
        assert entry["body_path"] == path.relative_to(tmp_path).as_posix()
        assert entry["body_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    # TODO(task-16)/TODO(task-21): gamebooks are imported later; nothing exists yet
    assert sources["gamebooks"] == {"pdfs": [], "comparisons": []}

    references = manifest["references"]
    assert set(references) == {"player_games_manifest", "controls", "exception_ledger"}
    manifest_path = player_games_root / "_manifest.json"
    assert references["player_games_manifest"] == {
        "path": "parquet-large/player_games/_manifest.json",
        "sha256": index.manifest_binding["sha256"],
        "bytes": index.manifest_binding["bytes"],
        "snapshot_name": "player-games-archive-2026-" + index.manifest_binding["sha256"][:12],
    }
    assert index.manifest_binding["sha256"] == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    controls = injury_reports.CONTROLS_PATH.read_bytes()
    assert references["controls"] == {
        "path": "src/nba_data/reference/injury_report_controls.json",
        "sha256": hashlib.sha256(controls).hexdigest(),
    }
    ledger_sha = hashlib.sha256(ledger.read_bytes()).hexdigest()
    assert references["exception_ledger"] == {"path": "ledger.json", "sha256": ledger_sha}
    assert manifest["exception_ledger"]["path"] == "ledger.json"
    assert manifest["exception_ledger"]["sha256"] == ledger_sha
    assert set(manifest["exception_ledger"]) == {"path", "sha256", "bytes", "applied", "counts"}

    # gamebook pairs that do exist are bound
    pdf = tmp_path / "raw/nba_gamebooks" / f"{BULLS_HORNETS.game_id}.book.pdf"
    cache.publish_bytes(pdf, b"%PDF-1.4 gamebook", {"provider": "nba_official"}, lambda path: None)
    record = tmp_path / "raw/nba_gamebooks/comparisons" / f"{BULLS_HORNETS.game_id}.json"
    cache.publish_bytes(record, b"{}\n", {"provider": "review"}, lambda path: None)
    bound = _build(raw_root, player_games_root, _stage(tmp_path, "with-gamebooks"), ledger)["sources"]["gamebooks"]
    assert [entry["body_path"] for entry in bound["pdfs"]] == [f"raw/nba_gamebooks/{BULLS_HORNETS.game_id}.book.pdf"]
    assert [entry["body_path"] for entry in bound["comparisons"]] == [
        f"raw/nba_gamebooks/comparisons/{BULLS_HORNETS.game_id}.json",
    ]
    assert bound["pdfs"][0]["identity"] == {"game_id": BULLS_HORNETS.game_id}
    assert bound["pdfs"][0]["body_sha256"] == hashlib.sha256(b"%PDF-1.4 gamebook").hexdigest()


def test_manifest_records_the_player_games_snapshot_name_derived_from_the_bound_digest(tmp_path, monkeypatch):
    """Would fail if the D1 snapshot name were supplied instead of derived from the bound manifest."""
    raw_root, player_games_root, ledger = seed_full_tree(tmp_path, monkeypatch)
    manifest_bytes_ = (player_games_root / "_manifest.json").read_bytes()
    digest = hashlib.sha256(manifest_bytes_).hexdigest()

    manifest = _build(raw_root, player_games_root, _stage(tmp_path), ledger)

    reference = manifest["references"]["player_games_manifest"]
    assert reference["sha256"] == digest and reference["bytes"] == len(manifest_bytes_)
    assert reference["snapshot_name"] == "player-games-archive-2026-" + digest[:12]
    # the name the restore script accepts (scripts/restore_player_games_snapshot.sh)
    assert re.fullmatch(r"player-games-archive-2026-[0-9a-f]{12}", reference["snapshot_name"])
    # TODO(task-3): the snapshot itself is produced on the owner's machine; a
    # different player_games manifest must yield a different name
    rewritten = player_games_root / "_manifest.json"
    document = json.loads(manifest_bytes_)
    document["partitions"] = dict(document["partitions"])
    rewritten.write_bytes((json.dumps(document, sort_keys=True, indent=4) + "\n").encode("utf-8"))
    other = _build(raw_root, player_games_root, _stage(tmp_path, "other"), ledger)
    assert other["references"]["player_games_manifest"]["snapshot_name"] != reference["snapshot_name"]
    assert other["references"]["player_games_manifest"]["snapshot_name"] == (
        "player-games-archive-2026-" + hashlib.sha256(rewritten.read_bytes()).hexdigest()[:12]
    )


# --------------------------------------------------------------------------
# validate_publication and run
# --------------------------------------------------------------------------


def test_validate_publication_recomputes_and_rejects_tampering(tmp_path, monkeypatch):
    """Would fail if any byte of the publication or its bound inputs could change unnoticed."""
    raw_root, player_games_root, ledger = seed_full_tree(tmp_path, monkeypatch)
    stage = _stage(tmp_path)
    manifest = _build(raw_root, player_games_root, stage, ledger)

    def validate():
        return availability_build.validate_publication(
            raw_root, player_games_root, stage, exceptions_path=ledger,
        )

    assert validate() == manifest

    def tampered(path: Path, mutate, match: str):
        original = path.read_bytes()
        path.write_bytes(mutate(original))
        with pytest.raises(availability_build.AvailabilityBuildError, match=match):
            validate()
        path.write_bytes(original)

    # a partition byte
    partition = stage / "season=2024/game_rosters.parquet"
    tampered(partition, lambda body: body[:-1] + bytes([body[-1] ^ 0xFF]), "partition")
    tampered(partition, lambda body: body[:-8] + b"\x00" + body[-7:], "partition")
    # a manifest value
    stored = json.loads((stage / "_manifest.json").read_text(encoding="utf-8"))
    for key, mutate in (
        ("validation", lambda doc: doc["validation"]["resolution"]["2024"].__setitem__("rows", 12)),
        ("partitions", lambda doc: doc["partitions"]["2024"]["game_rosters"].__setitem__("rows", 13)),
        ("sources", lambda doc: doc["sources"]["box_rosters"][0].__setitem__("body_sha256", "0" * 64)),
        ("references", lambda doc: doc["references"]["player_games_manifest"].__setitem__("snapshot_name", "x")),
        ("versions", lambda doc: doc["versions"].__setitem__("pdfplumber", "0.0.0")),
    ):
        document = json.loads(json.dumps(stored))
        mutate(document)
        tampered(
            stage / "_manifest.json", lambda _body, document=document: availability_build.manifest_bytes(document),
            key,
        )
    # non-canonical manifest bytes
    tampered(stage / "_manifest.json", lambda body: body + b"\n", "canonical")
    tampered(stage / "_manifest.json", lambda body: body[:-2] + b"}", "manifest")
    # a raw PDF, an inventory generation, a box-roster body, the player_games manifest
    pdf = injury_reports.pdf_path(tmp_path, injury_reports.slot_from_report_id(JANUARY_REPORT))
    tampered(pdf, lambda body: body + b"\n", JANUARY_REPORT)
    generation = tmp_path / "raw/nba_injury_reports/inventory/2024-01/gen-0001.json"
    tampered(generation, lambda body: body[:-2] + b" }", "2024-01")
    body = box_rosters.cache_path(tmp_path, FINALS.season, FINALS.game_id, "summary")
    tampered(body, lambda payload: payload.replace(b"20250605/INDOKC", b"20250605/INDOKX"), r"raw pair|sources|validation")
    tampered(player_games_root / "_manifest.json", lambda payload: payload + b"\n", "references")
    # the ledger: a byte change breaks the binding; a new entry is a stale approval
    tampered(ledger, lambda payload: payload.replace(b"reused", b"REUSED"), "references")
    key = f"2026|{OPENER.game_id}|{DORT}"
    document = full_ledger(appearance_consistency={key: _appearance()})
    tampered(ledger, lambda _payload: (json.dumps(document, indent=2, sort_keys=True) + "\n").encode(), "stale")
    # an approval that contradicts the body it names fails in the resolver, as a build error
    document = full_ledger(schedule_dates={BULLS_HORNETS.game_id: _schedule()})
    tampered(
        ledger, lambda _payload: (json.dumps(document, indent=2, sort_keys=True) + "\n").encode(),
        "schedule_dates",
    )

    # every input restored: the publication validates again
    assert validate() == manifest

    # layout: a missing partition, an extra file, a symlinked partition
    extra = stage / "season=2024/notes.txt"
    extra.write_text("x", encoding="utf-8")
    with pytest.raises(availability_build.AvailabilityBuildError, match="layout"):
        validate()
    extra.unlink()
    moved = partition.with_name("moved.parquet")
    partition.rename(moved)
    with pytest.raises(availability_build.AvailabilityBuildError, match="layout"):
        validate()
    partition.symlink_to(moved)
    with pytest.raises(availability_build.AvailabilityBuildError, match="layout|regular"):
        validate()
    partition.unlink()
    moved.rename(partition)
    assert validate() == manifest
    with pytest.raises(availability_build.AvailabilityBuildError, match="publication"):
        availability_build.validate_publication(raw_root, player_games_root, tmp_path / "nowhere", exceptions_path=ledger)


def test_run_stages_rereads_publishes_and_rolls_back_on_failure(tmp_path, monkeypatch):
    """Would fail if run published before rereading, or left a stage or partial output behind."""
    raw_root, player_games_root, ledger = seed_full_tree(tmp_path, monkeypatch)
    monkeypatch.setattr(availability_build, "EXCEPTIONS_PATH", ledger)
    staging_root = tmp_path / "parquet-large"
    output = staging_root / "availability"
    real_validate = availability_build.validate_publication
    validated = []

    # a forced reread mismatch: no partial output, no stage left behind
    def mismatch(*args, **kwargs):
        validated.append(Path(args[2]))
        return {**real_validate(*args, **kwargs), "schema_version": 2}

    monkeypatch.setattr(availability_build, "validate_publication", mismatch)
    with pytest.raises(availability_build.AvailabilityBuildError, match="reread"):
        availability_build.run(tmp_path)
    assert not output.exists()
    assert sorted(path.name for path in staging_root.iterdir()) == ["player_games"]
    assert len(validated) == 1 and validated[0].parent == staging_root
    assert validated[0].name.startswith(".availability-build-") and not validated[0].exists()

    # the success path: stage under parquet-large, validate the stage, publish, validate the output
    validated.clear()
    labels = []
    real_publish = publication.publish_tree

    def record_validate(raw, games, publication_dir, *, exceptions_path):
        validated.append(Path(publication_dir))
        assert Path(raw) == raw_root and Path(games) == player_games_root and Path(exceptions_path) == ledger
        return real_validate(raw, games, publication_dir, exceptions_path=exceptions_path)

    def record_publish(stage_dir, output_dir, *, label):
        labels.append(label)
        assert Path(stage_dir).parent == staging_root and Path(output_dir) == output
        return real_publish(stage_dir, output_dir, label=label)

    monkeypatch.setattr(availability_build, "validate_publication", record_validate)
    monkeypatch.setattr(publication, "publish_tree", record_publish)
    availability_build.run(tmp_path)
    assert labels == ["availability publication"]
    assert len(validated) == 2 and validated[0] != output and validated[1] == output
    assert validated[0].name.startswith(".availability-build-") and not validated[0].exists()
    tree = _tree_bytes(output)
    assert {name for name in tree if name.endswith(".parquet")} == EXPECTED_PARTITIONS
    assert sorted(path.name for path in staging_root.iterdir()) == ["availability", "player_games"]
    # a second run replaces the publication with identical bytes
    availability_build.run(tmp_path)
    assert _tree_bytes(output) == tree
    assert sorted(path.name for path in staging_root.iterdir()) == ["availability", "player_games"]
    # a failing rebuild leaves the previous publication in place
    monkeypatch.setattr(availability_build, "validate_publication", mismatch)
    with pytest.raises(availability_build.AvailabilityBuildError, match="reread"):
        availability_build.run(tmp_path)
    assert _tree_bytes(output) == tree
    assert sorted(path.name for path in staging_root.iterdir()) == ["availability", "player_games"]
    # a retained rollback directory is reported
    monkeypatch.setattr(availability_build, "validate_publication", real_validate)
    retained = staging_root / ".availability.rollback-retained"
    monkeypatch.setattr(publication, "publish_tree", lambda *args, **kwargs: retained)
    with pytest.raises(availability_build.AvailabilityBuildError, match=r"rollback.*rollback-retained"):
        availability_build.run(tmp_path)
