"""Tests for the availability build: the exception ledger (Task 13).

The ledger is validated like the D1 ledgers in ``player_games_build``:
duplicate members, extra or missing sections and fields, type drift,
malformed composite keys and stale or unlisted entries all fail closed.
"""

from __future__ import annotations

import json

import pytest

from nba_data import availability_build


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
