"""Assemble and validate the availability publication.

This module currently holds the exception ledger section (Task 13): the
packaged ledger at :data:`EXCEPTIONS_PATH`, :func:`load_exceptions`, which
parses it under the same discipline as the D1 ledgers in
``player_games_build`` (duplicate members, non-standard JSON constants,
extra or missing sections and fields, type drift and malformed composite
keys all fail closed), and :func:`validate_exceptions`, which requires the
disagreements a build observes and the entries a reviewer approved to be
equal in both directions, ``note`` aside.

Ledger sections, keys and fields (every approved entry also carries a
non-empty ``note``; an observed entry is the same object without it):

``appearance_consistency``
    key ``season|game_id|person_id``; ``comment_raw`` (str or null),
    ``minutes_raw`` (str or null), ``appearance`` (``credited`` / ``absent``).
``report_versions``
    key ``report_id``; ``action`` (``exclude`` / ``accept_title``), ``sha256``
    of the body, ``failed_check`` (non-empty str for ``exclude``, null for
    ``accept_title``), ``title_printed_et`` (naive ISO string or null),
    ``slot_label`` (must be the slot's own label).
``team_resolution``
    key ``report_id|row_number``; ``team_raw`` (str), ``nba_team_id`` (int).
    An *observed* entry carries ``nba_team_id`` ``None`` when no approval was
    applied (``availability_resolve.team_resolution_observed``: the build
    cannot know the intended team), and :func:`validate_exceptions` reports
    every such entry under ``team_resolution missing approvals`` whether or
    not the ledger holds its key; approved entries always carry an int.
``schedule_dates``
    key ``game_id``; ``game_et_raw`` (str), ``game_code_raw``
    (``YYYYMMDD/AAAHHH``), ``game_date`` (ISO date string).

The build, resolution and publication sections follow in Task 14.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

from nba_data.box_rosters import GAME_CODE_PATTERN
from nba_data.injury_reports import InjuryReportError, slot_from_report_id


ROSTER_SEASONS = tuple(range(2012, 2027))
REPORT_SEASONS = tuple(range(2019, 2027))
EXCEPTIONS_PATH = Path(__file__).resolve().parent / "reference/availability_exceptions.json"
PUBLICATION_SUBDIR = "parquet-large/availability"
PERSON_RESOLUTION_FLOOR = 0.98

LEDGER_SCHEMA_VERSION = 1
LEDGER_SECTIONS = (
    "appearance_consistency",
    "report_versions",
    "schedule_dates",
    "team_resolution",
)
_LEDGER_TOP_LEVEL = frozenset(LEDGER_SECTIONS) | {"schema_version"}

_EXCEPTION_FIELDS = {
    "appearance_consistency": {"comment_raw", "minutes_raw", "appearance", "note"},
    "report_versions": {
        "action", "sha256", "failed_check", "title_printed_et", "slot_label", "note",
    },
    "team_resolution": {"team_raw", "nba_team_id", "note"},
    "schedule_dates": {"game_et_raw", "game_code_raw", "game_date", "note"},
}
_STRING_FIELDS = {
    "appearance", "action", "sha256", "slot_label", "team_raw",
    "game_et_raw", "game_code_raw", "game_date", "note",
}
_NULLABLE_STRING_FIELDS = {"comment_raw", "minutes_raw", "failed_check", "title_printed_et"}
_INTEGER_FIELDS = {"nba_team_id"}
_APPEARANCES = ("credited", "absent")
_ACTIONS = ("exclude", "accept_title")

_SEASON_TOKEN = re.compile(r"^[0-9]{4}$")
_GAME_ID_TOKEN = re.compile(r"^[0-9]{10}$")
_CANONICAL_INTEGER = re.compile(r"^(0|[1-9][0-9]*)$")
_POSITIVE_INTEGER = re.compile(r"^[1-9][0-9]*$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class AvailabilityBuildError(ValueError):
    """Raised when the availability build, its ledger or its publication violates its contract."""


# --------------------------------------------------------------------------
# exception ledger
# --------------------------------------------------------------------------


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise AvailabilityBuildError(
                f"duplicate semantic identity in exception ledger: {key!r}"
            )
        result[key] = value
    return result


def _malformed_key(section: str, key: object) -> AvailabilityBuildError:
    return AvailabilityBuildError(
        f"{section} exception has malformed composite key: {key!r}"
    )


def _slot_for_report_id(section: str, key: str, report_id: str):
    try:
        return slot_from_report_id(report_id)
    except InjuryReportError:
        raise _malformed_key(section, key) from None


def _validate_key(section: str, key: object) -> None:
    if not isinstance(key, str):
        raise _malformed_key(section, key)
    parts = key.split("|")
    if section == "appearance_consistency":
        if (
            len(parts) != 3
            or not _SEASON_TOKEN.match(parts[0])
            or not _GAME_ID_TOKEN.match(parts[1])
            or not _CANONICAL_INTEGER.match(parts[2])
        ):
            raise _malformed_key(section, key)
    elif section == "report_versions":
        if len(parts) != 1:
            raise _malformed_key(section, key)
        _slot_for_report_id(section, key, parts[0])
    elif section == "team_resolution":
        if len(parts) != 2 or not _POSITIVE_INTEGER.match(parts[1]):
            raise _malformed_key(section, key)
        _slot_for_report_id(section, key, parts[0])
    elif section == "schedule_dates":
        if len(parts) != 1 or not _GAME_ID_TOKEN.match(parts[0]):
            raise _malformed_key(section, key)
    else:  # pragma: no cover - sections are fixed above
        raise AvailabilityBuildError(f"unknown exception ledger section: {section!r}")


def _is_canonical_naive_iso_datetime(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is None and parsed.isoformat() == value


def _is_canonical_iso_date(value: str) -> bool:
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _validate_exception_entry(
    section: str, key: str, entry: object, *, observed: bool = False,
) -> None:
    _validate_key(section, key)
    if not isinstance(entry, dict):
        raise AvailabilityBuildError(
            f"{section} exception {key!r} must be an object"
        )
    fields = _EXCEPTION_FIELDS[section]
    if set(entry) != fields:
        raise AvailabilityBuildError(
            f"{section} exception {key!r} has invalid fields: {sorted(entry)}"
        )

    def wrong_type(field: str) -> AvailabilityBuildError:
        return AvailabilityBuildError(
            f"{section} exception {key!r} field {field!r} has wrong type"
        )

    for field in sorted(_STRING_FIELDS & fields):
        if type(entry[field]) is not str:
            raise wrong_type(field)
    for field in sorted(_NULLABLE_STRING_FIELDS & fields):
        if entry[field] is not None and type(entry[field]) is not str:
            raise wrong_type(field)
    for field in sorted(_INTEGER_FIELDS & fields):
        nullable = observed and section == "team_resolution" and field == "nba_team_id"
        if nullable and entry[field] is None:
            continue
        if type(entry[field]) is not int:
            raise wrong_type(field)
    if not entry["note"].strip():
        raise AvailabilityBuildError(
            f"{section} exception {key!r} requires a non-empty note"
        )

    if section == "appearance_consistency":
        if entry["appearance"] not in _APPEARANCES:
            raise AvailabilityBuildError(
                f"{section} exception {key!r} appearance must be one of "
                f"{list(_APPEARANCES)}: {entry['appearance']!r}"
            )
    elif section == "report_versions":
        action = entry["action"]
        if action not in _ACTIONS:
            raise AvailabilityBuildError(
                f"{section} exception {key!r} action must be one of "
                f"{list(_ACTIONS)}: {action!r}"
            )
        if not _SHA256_PATTERN.match(entry["sha256"]):
            raise AvailabilityBuildError(
                f"{section} exception {key!r} sha256 must be 64 lowercase hex "
                f"characters: {entry['sha256']!r}"
            )
        failed_check = entry["failed_check"]
        if action == "exclude" and (failed_check is None or not failed_check.strip()):
            raise AvailabilityBuildError(
                f"{section} exception {key!r} with action 'exclude' requires a "
                f"non-empty failed_check: {failed_check!r}"
            )
        if action == "accept_title" and failed_check is not None:
            raise AvailabilityBuildError(
                f"{section} exception {key!r} with action 'accept_title' requires "
                f"a null failed_check: {failed_check!r}"
            )
        printed = entry["title_printed_et"]
        if printed is not None and not _is_canonical_naive_iso_datetime(printed):
            raise AvailabilityBuildError(
                f"{section} exception {key!r} title_printed_et must be a naive "
                f"ISO datetime string or null: {printed!r}"
            )
        slot = _slot_for_report_id(section, key, key)
        if entry["slot_label"] != slot.slot_label:
            raise AvailabilityBuildError(
                f"{section} exception {key!r} slot_label must equal the slot's "
                f"label {slot.slot_label!r}: {entry['slot_label']!r}"
            )
    elif section == "schedule_dates":
        if not GAME_CODE_PATTERN.match(entry["game_code_raw"]):
            raise AvailabilityBuildError(
                f"{section} exception {key!r} game_code_raw must match "
                f"YYYYMMDD/AAAHHH: {entry['game_code_raw']!r}"
            )
        if not _is_canonical_iso_date(entry["game_date"]):
            raise AvailabilityBuildError(
                f"{section} exception {key!r} game_date must be an ISO date "
                f"string: {entry['game_date']!r}"
            )


def _validate_ledger_document(document: object, label: str) -> None:
    if not isinstance(document, dict):
        raise AvailabilityBuildError(f"{label} root must be an object")
    if set(document) != _LEDGER_TOP_LEVEL:
        raise AvailabilityBuildError(
            f"{label} top-level keys must be exactly {sorted(_LEDGER_TOP_LEVEL)}: "
            f"{sorted(document)}"
        )
    version = document["schema_version"]
    if type(version) is not int or version != LEDGER_SCHEMA_VERSION:
        raise AvailabilityBuildError(
            f"{label} schema_version must be {LEDGER_SCHEMA_VERSION}: {version!r}"
        )
    for section in LEDGER_SECTIONS:
        entries = document[section]
        if not isinstance(entries, dict):
            raise AvailabilityBuildError(f"{label} {section} must be an object")
        for key, entry in entries.items():
            _validate_exception_entry(section, key, entry)


def load_exceptions(path: Path = EXCEPTIONS_PATH) -> dict:
    """Load and validate the availability exception ledger."""
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON numeric constant: {value}")

    try:
        document = json.loads(
            Path(path).read_text(encoding="utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=reject_constant,
        )
    except AvailabilityBuildError:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError) as exc:
        raise AvailabilityBuildError(
            f"invalid JSON in exception ledger {path}: {exc}"
        ) from exc

    _validate_ledger_document(document, "exception ledger")
    return document


def _validate_observed_entry(section: str, key: str, entry: object) -> None:
    if not isinstance(entry, dict):
        raise AvailabilityBuildError(
            f"observed {section} discrepancy {key!r} must be an object"
        )
    expected = _EXCEPTION_FIELDS[section] - {"note"}
    if set(entry) != expected:
        raise AvailabilityBuildError(
            f"observed {section} discrepancy {key!r} has invalid fields: "
            f"{sorted(entry)}"
        )
    _validate_exception_entry(
        section, key, {**entry, "note": "observed"}, observed=True,
    )


def _without_note(entry: dict) -> dict:
    return {key: value for key, value in entry.items() if key != "note"}


def validate_exceptions(observed: dict, approved: dict) -> None:
    """Require exact two-way equality between observed and approved entries.

    ``approved`` is a ledger document as :func:`load_exceptions` returns it
    (the four sections and ``schema_version``); ``observed`` carries exactly
    the four sections, each keyed like the ledger with entries of the
    approved shape minus ``note``. Every section is compared in both
    directions: an observed entry with no approval, an approved entry that
    was not observed, and a shared key whose fields differ each fail, and
    all failures are reported together.
    """
    _validate_ledger_document(approved, "approved exceptions")
    if not isinstance(observed, dict) or set(observed) != set(LEDGER_SECTIONS):
        raise AvailabilityBuildError(
            "observed exceptions top-level keys must be exactly "
            f"{sorted(LEDGER_SECTIONS)}"
        )
    for section in LEDGER_SECTIONS:
        if not isinstance(observed[section], dict):
            raise AvailabilityBuildError(
                f"observed exceptions {section} must be an object"
            )
        for key, entry in observed[section].items():
            _validate_observed_entry(section, key, entry)

    errors = []
    for section in LEDGER_SECTIONS:
        observed_entries = observed[section]
        approved_entries = approved[section]
        observed_keys = set(observed_entries)
        approved_keys = set(approved_entries)
        # An observed team_resolution entry with a null nba_team_id had no
        # approval applied, so it is unapproved even when the ledger now
        # holds its key: it is never a drift.
        unapproved = {
            key
            for key, entry in observed_entries.items()
            if section == "team_resolution" and entry["nba_team_id"] is None
        }
        missing = sorted((observed_keys - approved_keys) | unapproved)
        stale = sorted(approved_keys - observed_keys)
        drift = sorted(
            key
            for key in (observed_keys & approved_keys) - unapproved
            if observed_entries[key] != _without_note(approved_entries[key])
        )
        if missing:
            errors.append(
                f"{section} missing approvals="
                + repr({key: observed_entries[key] for key in missing})
            )
        if stale:
            errors.append(
                f"{section} stale approvals="
                + repr({key: approved_entries[key] for key in stale})
            )
        if drift:
            errors.append(
                f"{section} drift="
                + repr(
                    {
                        key: {
                            "observed": observed_entries[key],
                            "approved": _without_note(approved_entries[key]),
                        }
                        for key in drift
                    }
                )
            )
    if errors:
        raise AvailabilityBuildError(
            "exception ledger mismatch: " + "; ".join(errors)
        )
