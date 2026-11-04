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

Table assembly (Task 14) follows the ledger section: the four Arrow schemas
of the publication, :func:`assemble_part_a` (box-roster pairs ->
``game_rosters`` / ``game_schedule`` rows plus the observed
``appearance_consistency`` and ``schedule_dates`` entries),
:func:`assemble_part_b` (inventory generations and PDF pairs ->
``injury_report_versions`` / ``injury_report_listings`` rows resolved through
``availability_resolve``, plus the observed ``report_versions`` and
``team_resolution`` entries), :func:`table_from_frame`,
:func:`write_partition` and :func:`logical_row_sha256`. Both assemblers take
``raw_root`` (``data_dir / "raw"``) and the approved ledger document; neither
applies :func:`validate_exceptions` itself, so a caller can report every
section's mismatch at once.

The build itself (Task 15) closes the module: the gates in order (coverage;
the ledger across all four sections at once; version-season agreement; team
resolution exact; the two person-resolution floors per season; the
46-partition layout), the derivative cross-check (:func:`compare_version`,
:func:`derivative_agreement`; never a gate), the deterministic manifest
(:func:`manifest_bytes`), :func:`build_from_roots` / :func:`build_tree`,
the independent :func:`validate_publication`, which recomputes the whole
manifest from the raw trees and compares it with the stored one key by key,
and :func:`run`, which stages beside the publication, rereads the stage,
publishes through ``publication.publish_tree`` and rereads the result.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
import tempfile
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from nba_data import (
    availability_resolve,
    box_rosters,
    cache,
    injury_derivative,
    injury_report_parse,
    injury_reports,
    player_games_build,
    publication,
)
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


# --------------------------------------------------------------------------
# Arrow schemas
# --------------------------------------------------------------------------


GAME_ROSTERS_SCHEMA = pa.schema([
    pa.field("game_id", pa.large_string(), nullable=False),
    pa.field("person_id", pa.int64(), nullable=False),
    pa.field("season", pa.int64(), nullable=False),
    pa.field("game_type", pa.large_string(), nullable=False),
    pa.field("game_date", pa.date32(), nullable=False),
    pa.field("nba_team_id", pa.int64(), nullable=False),
    pa.field("team_tricode_raw", pa.large_string(), nullable=False),
    pa.field("franchise_id", pa.large_string(), nullable=False),
    pa.field("is_home", pa.bool_(), nullable=False),
    pa.field("roster_status", pa.large_string(), nullable=False),
    pa.field("first_name_raw", pa.large_string(), nullable=False),
    pa.field("family_name_raw", pa.large_string(), nullable=False),
    pa.field("jersey_num", pa.large_string(), nullable=True),
    pa.field("starter_position_raw", pa.large_string(), nullable=True),
    pa.field("comment_raw", pa.large_string(), nullable=True),
    pa.field("comment_code", pa.large_string(), nullable=True),
    pa.field("minutes_raw", pa.large_string(), nullable=True),
])

GAME_SCHEDULE_SCHEMA = pa.schema([
    pa.field("game_id", pa.large_string(), nullable=False),
    pa.field("season", pa.int64(), nullable=False),
    pa.field("game_type", pa.large_string(), nullable=False),
    pa.field("game_date", pa.date32(), nullable=False),
    pa.field("scheduled_start_utc", pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("scheduled_start_et_raw", pa.large_string(), nullable=False),
    pa.field("game_code_raw", pa.large_string(), nullable=False),
])

INJURY_REPORT_VERSIONS_SCHEMA = pa.schema([
    pa.field("report_id", pa.large_string(), nullable=False),
    pa.field("season", pa.int64(), nullable=False),
    pa.field("grid", pa.large_string(), nullable=False),
    pa.field("slot_date", pa.date32(), nullable=False),
    pa.field("slot_label", pa.large_string(), nullable=False),
    pa.field("report_slot_et", pa.timestamp("us"), nullable=False),
    pa.field("report_time_printed_et", pa.timestamp("us"), nullable=True),
    pa.field("report_time_printed_utc", pa.timestamp("us", tz="UTC"), nullable=True),
    pa.field("report_time_printed_utc_ambiguous", pa.bool_(), nullable=False),
    pa.field("url", pa.large_string(), nullable=False),
    pa.field("sha256", pa.large_string(), nullable=False),
    pa.field("bytes", pa.int64(), nullable=False),
    pa.field("pages", pa.int64(), nullable=True),
    pa.field("listing_rows", pa.int64(), nullable=True),
    pa.field("placeholder_rows", pa.int64(), nullable=True),
    pa.field("games_listed", pa.int64(), nullable=True),
    pa.field("games_resolved", pa.int64(), nullable=True),
    pa.field("parse_status", pa.large_string(), nullable=False),
    pa.field("identical_listings_to_previous", pa.bool_(), nullable=False),
])

INJURY_REPORT_LISTINGS_SCHEMA = pa.schema([
    pa.field("report_id", pa.large_string(), nullable=False),
    pa.field("season", pa.int64(), nullable=False),
    pa.field("row_number", pa.int64(), nullable=False),
    pa.field("page", pa.int64(), nullable=False),
    pa.field("game_date", pa.date32(), nullable=False),
    pa.field("game_time_raw", pa.large_string(), nullable=False),
    pa.field("matchup_raw", pa.large_string(), nullable=False),
    pa.field("away_tricode", pa.large_string(), nullable=False),
    pa.field("home_tricode", pa.large_string(), nullable=False),
    pa.field("team_raw", pa.large_string(), nullable=False),
    pa.field("is_placeholder", pa.bool_(), nullable=False),
    pa.field("placeholder_raw", pa.large_string(), nullable=True),
    pa.field("player_name_raw", pa.large_string(), nullable=True),
    pa.field("current_status_raw", pa.large_string(), nullable=True),
    pa.field("reason_raw", pa.large_string(), nullable=True),
    pa.field("category_raw", pa.large_string(), nullable=True),
    pa.field("previous_status_raw", pa.large_string(), nullable=True),
    pa.field("game_id", pa.large_string(), nullable=True),
    pa.field("game_resolution", pa.large_string(), nullable=False),
    pa.field("nba_team_id", pa.int64(), nullable=True),
    pa.field("franchise_id", pa.large_string(), nullable=True),
    pa.field("person_id", pa.int64(), nullable=True),
    pa.field("person_resolution", pa.large_string(), nullable=False),
])

PARTITION_SCHEMAS = {
    "game_rosters": GAME_ROSTERS_SCHEMA,
    "game_schedule": GAME_SCHEDULE_SCHEMA,
    "injury_report_versions": INJURY_REPORT_VERSIONS_SCHEMA,
    "injury_report_listings": INJURY_REPORT_LISTINGS_SCHEMA,
}

# Working column ``PartB.listings`` carries after the published columns: the
# version's slot start, which ``availability_resolve.resolution_metrics``
# groups by and which fixes the publication order. ``table_from_frame``
# selects the schema's columns, so it never reaches a partition.
LISTINGS_SLOT_COLUMN = "report_slot_et"
# The verbatim columns whose multiset defines ``identical_listings_to_previous``
# (page and row numbers are layout, not content).
LISTING_IDENTITY_COLUMNS = (
    "game_date", "game_time_raw", "matchup_raw", "team_raw", "is_placeholder",
    "placeholder_raw", "player_name_raw", "current_status_raw", "reason_raw",
    "category_raw", "previous_status_raw",
)
PARSE_STATUSES = ("ok", "ok_title_accepted", "excluded")
TITLE_CHECKS = ("ok", "failed", "unparsed")
# ``failed_check`` values of an observed ``report_versions`` entry, in gate order.
FAILED_CHECK_OPENS = "opens"               # the body does not open as a PDF
FAILED_CHECK_PARSE = "parse"               # ``injury_report_parse.parse_report`` raised
FAILED_CHECK_TITLE_WINDOW = "title_window"  # the printed title lies outside the slot window
FAILED_CHECK_TITLE_GAP = "title_gap"       # the printed time falls in the skipped March hour

_SCHEDULE_DTYPES = {
    "game_id": "string", "season": "Int64", "game_type": "string",
    "scheduled_start_et_raw": "string", "game_code_raw": "string",
}
_ROSTER_COUNT_DTYPES = {
    "game_id": "string", "nba_team_id": "Int64", "active": "Int64", "inactive": "Int64",
}
_VERSION_DTYPES = {
    "report_id": "string", "season": "Int64", "grid": "string", "slot_label": "string",
    "report_slot_et": "datetime64[us]", "report_time_printed_et": "datetime64[us]",
    "report_time_printed_utc_ambiguous": "boolean", "url": "string", "sha256": "string",
    "bytes": "Int64", "pages": "Int64", "listing_rows": "Int64", "placeholder_rows": "Int64",
    "games_listed": "Int64", "games_resolved": "Int64", "parse_status": "string",
    "identical_listings_to_previous": "boolean",
}
_LISTING_DTYPES = {
    "report_id": "string", "season": "Int64", "row_number": "Int64", "page": "Int64",
    "game_time_raw": "string", "matchup_raw": "string", "away_tricode": "string",
    "home_tricode": "string", "team_raw": "string", "is_placeholder": "boolean",
    "placeholder_raw": "string", "player_name_raw": "string",
    "current_status_raw": "string", "reason_raw": "string", "category_raw": "string",
    "previous_status_raw": "string", LISTINGS_SLOT_COLUMN: "datetime64[us]",
}
_LISTING_SOURCE_COLUMNS = tuple(
    name for name in INJURY_REPORT_LISTINGS_SCHEMA.names
    if name not in availability_resolve.RESOLUTION_COLUMNS
)
_INVENTORY_ENTRY_KEYS = {
    "grid", "slot_date", "slot_label", "url", "status", "sha256", "bytes", "evidence",
}
_EVIDENCE_STAMP = "%Y-%m-%dT%H:%M:%SZ"
_ROSTER_ORDER = ["season", "game_date", "game_id", "nba_team_id", "roster_status", "person_id"]
_VERSION_ORDER = ["season", "report_slot_et", "report_id"]
_LISTING_ORDER = ["season", "report_slot_et", "report_id", "row_number"]


# --------------------------------------------------------------------------
# Part A: box-roster pairs -> game_rosters / game_schedule
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PartA:
    rosters: pd.DataFrame
    schedule: pd.DataFrame
    team_strings: dict[tuple[str, int], str]
    tricodes: dict[tuple[str, int], str]
    appearance_observed: dict[str, dict]
    schedule_observed: dict[str, dict]
    roster_counts: pd.DataFrame


def _data_dir(raw_root: Path) -> Path:
    """``raw_root`` is ``data_dir / "raw"``: the raw modules address pairs from ``data_dir``."""
    raw_root = Path(raw_root)
    if raw_root.name != "raw" or not raw_root.is_dir():
        raise AvailabilityBuildError(
            f"raw_root must be an existing directory named 'raw' (data_dir / 'raw'): {raw_root}"
        )
    return raw_root.parent


def assemble_part_a(
    raw_root: Path, index: box_rosters.PlayerGamesIndex, approved: dict,
) -> PartA:
    """Normalise every worklist game's cached pair into the Part A frames.

    Every game of ``index.games`` needs both attested pairs beneath
    ``raw_root / "stats_nba/box_rosters"`` (a missing one raises
    ``AvailabilityBuildError``; a damaged one raises ``CacheIntegrityError``
    from the cache layer). ``rosters`` is ordered ``season, game_date,
    game_id, nba_team_id, roster_status, person_id``; ``schedule`` and
    ``roster_counts`` follow the worklist order. Disagreements are reported
    as observed ledger entries (``appearance_observed`` keyed
    ``season|game_id|person_id``, ``schedule_observed`` keyed ``game_id``)
    and never resolved here; ``approved`` is validated as a ledger document
    and otherwise left to :func:`validate_exceptions`.
    """
    data_dir = _data_dir(raw_root)
    _validate_ledger_document(approved, "approved exceptions")
    if not index.games:
        raise AvailabilityBuildError("player_games worklist holds no games")

    frames: list[pd.DataFrame] = []
    schedule_rows: list[dict] = []
    count_rows: list[dict] = []
    team_strings: dict[tuple[str, int], str] = {}
    tricodes: dict[tuple[str, int], str] = {}
    appearance_observed: dict[str, dict] = {}
    schedule_observed: dict[str, dict] = {}
    for game in index.games:
        bodies: dict[str, bytes] = {}
        for endpoint in box_rosters.ENDPOINTS:
            cached = box_rosters.read_cached(data_dir, game, endpoint)
            if cached is None:
                raise AvailabilityBuildError(
                    f"missing box-roster {endpoint} pair for game {game.game_id} "
                    f"(season {game.season}): "
                    f"{box_rosters.cache_path(data_dir, game.season, game.game_id, endpoint)}"
                )
            bodies[endpoint] = cached[0]
        normalized = box_rosters.normalize_game(
            bodies["traditional"], bodies["summary"], game=game, index=index,
        )
        frames.append(normalized.rosters)
        schedule_rows.append(dict(normalized.schedule))
        for team_id, value in normalized.team_strings.items():
            team_strings[(game.game_id, team_id)] = value
        for team_id, value in normalized.tricodes.items():
            tricodes[(game.game_id, team_id)] = value
        for entry in normalized.appearance_disagreements:
            key = f"{entry['season']}|{entry['game_id']}|{entry['person_id']}"
            appearance_observed[key] = {
                "comment_raw": entry["comment_raw"],
                "minutes_raw": entry["minutes_raw"],
                "appearance": entry["appearance"],
            }
        disagreement = normalized.schedule_disagreement
        if disagreement is not None:
            schedule_observed[game.game_id] = {
                "game_et_raw": disagreement["game_et_raw"],
                "game_code_raw": disagreement["game_code_raw"],
                "game_date": disagreement["game_date"].isoformat(),
            }
        for team_id in sorted(normalized.roster_counts):
            counts = normalized.roster_counts[team_id]
            count_rows.append({
                "game_id": game.game_id, "nba_team_id": team_id,
                "active": counts["active"], "inactive": counts["inactive"],
            })

    rosters = pd.concat(frames, ignore_index=True)
    rosters = rosters[list(GAME_ROSTERS_SCHEMA.names)]
    rosters = rosters.sort_values(_ROSTER_ORDER, kind="stable").reset_index(drop=True)
    schedule = pd.DataFrame.from_records(
        schedule_rows, columns=list(GAME_SCHEDULE_SCHEMA.names),
    ).astype(_SCHEDULE_DTYPES)
    schedule = schedule.sort_values(
        ["season", "game_date", "game_id"], kind="stable",
    ).reset_index(drop=True)
    roster_counts = pd.DataFrame.from_records(
        count_rows, columns=list(_ROSTER_COUNT_DTYPES),
    ).astype(_ROSTER_COUNT_DTYPES)
    return PartA(
        rosters=rosters,
        schedule=schedule,
        team_strings=team_strings,
        tricodes=tricodes,
        appearance_observed=dict(sorted(appearance_observed.items())),
        schedule_observed=dict(sorted(schedule_observed.items())),
        roster_counts=roster_counts,
    )


# --------------------------------------------------------------------------
# Part B: inventory generations and PDF pairs -> versions / listings
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PartB:
    versions: pd.DataFrame
    listings: pd.DataFrame
    report_versions_observed: dict[str, dict]
    team_resolution_observed: dict[str, dict]
    inventories: list[dict]
    slot_totals: dict
    parse_counts: dict
    title_counts: dict
    placeholder_counts: dict


@dataclass(frozen=True)
class _PresentReport:
    """One present slot after the opens / parse / title gates.

    Only what the version row and counts need is kept: the body and the
    parsed listings are released as soon as the listing rows are extracted,
    so a whole-tree build never holds every PDF or ``Listing`` in memory.
    """

    slot: injury_reports.Slot
    season: int
    sha256: str
    bytes: int
    pages: int | None
    printed_et: datetime | None
    printed_utc: datetime | None
    ambiguous: bool
    title_check: str
    failed_check: str | None
    rechecked: bool


def _current_generation(data_dir: Path, month: str) -> tuple[dict, Path]:
    """The month's highest generation, which must verify."""
    current, damaged = injury_reports.read_current_inventory(data_dir, month)
    if current is None:
        raise AvailabilityBuildError(
            f"month {month} has no verifying inventory generation under "
            f"{injury_reports.inventory_dir(data_dir, month)}"
        )
    newest_damaged = max(
        (injury_reports.generation_number(path) for path in damaged), default=0,
    )
    if newest_damaged > current["generation"]:
        raise AvailabilityBuildError(
            f"month {month}: the newest inventory generation gen-{newest_damaged:04d}.json "
            f"does not verify (highest verifying is gen-{current['generation']:04d}.json); "
            "the build uses only the highest generation and never falls back"
        )
    path = injury_reports.inventory_dir(data_dir, month) / f"gen-{current['generation']:04d}.json"
    return current, path


def _evidence_dates(entry: dict, report_id: str) -> list[date]:
    evidence = entry.get("evidence")
    if not isinstance(evidence, list):
        raise AvailabilityBuildError(f"{report_id}: inventory evidence must be a list")
    dates: list[date] = []
    for item in evidence:
        try:
            dates.append(datetime.strptime(item["fetched_at"], _EVIDENCE_STAMP).date())
        except (KeyError, TypeError, ValueError):
            raise AvailabilityBuildError(
                f"{report_id}: inventory evidence carries a malformed fetched_at"
            ) from None
    return dates


def _slot_outcome(
    data_dir: Path, month: str, slot: injury_reports.Slot, entry: object,
) -> tuple[str, bytes | None, bool]:
    """Validate one slot's inventory entry: ``(status, body or None, rechecked)``."""
    report_id = slot.report_id
    if not isinstance(entry, dict) or not _INVENTORY_ENTRY_KEYS <= set(entry):
        raise AvailabilityBuildError(
            f"{report_id}: inventory {month} entry must carry {sorted(_INVENTORY_ENTRY_KEYS)}"
        )
    identity = {
        "grid": slot.grid, "slot_date": slot.slot_date.isoformat(),
        "slot_label": slot.slot_label, "url": slot.url,
    }
    if any(entry[key] != value for key, value in identity.items()):
        raise AvailabilityBuildError(
            f"{report_id}: inventory {month} entry disagrees with the slot: "
            f"{ {key: entry[key] for key in identity} } != {identity}"
        )
    status = entry["status"]
    pdf = injury_reports.pdf_path(data_dir, slot)
    sidecar = pdf.with_name(pdf.name + ".meta.json")
    dates = _evidence_dates(entry, report_id)
    if status == "present":
        try:
            attested = cache.read_attested(pdf, injury_reports.validate_pdf)
        except (cache.CacheIntegrityError, InjuryReportError) as exc:
            raise AvailabilityBuildError(
                f"{report_id}: PDF pair at {pdf} does not verify: {exc}"
            ) from exc
        if attested is None:
            raise AvailabilityBuildError(
                f"{report_id}: inventory {month} records the slot as present but no "
                f"PDF pair exists at {pdf}"
            )
        payload, _metadata = attested
        digest = hashlib.sha256(payload).hexdigest()
        if (entry["sha256"], entry["bytes"]) != (digest, len(payload)):
            raise AvailabilityBuildError(
                f"{report_id}: inventory {month} records sha256 {entry['sha256']} "
                f"({entry['bytes']} bytes) but the PDF pair on disk is sha256 {digest} "
                f"({len(payload)} bytes)"
            )
        return status, payload, bool(dates)
    if status == "unavailable":
        if len(set(dates)) < 2:
            raise AvailabilityBuildError(
                f"{report_id}: unavailable slot was not rechecked on a later UTC day "
                f"({len(dates)} evidence entries on {len(set(dates))} date(s)); "
                "every unavailable slot needs two probes on different days"
            )
        if pdf.exists() or sidecar.exists():
            raise AvailabilityBuildError(
                f"{report_id}: inventory {month} records the slot as unavailable but a "
                f"PDF pair exists at {pdf}; rerun fetch so the generation records it"
            )
        return status, None, True
    raise AvailabilityBuildError(
        f"{report_id}: inventory {month} carries unknown status {status!r}"
    )


def _gate_present(
    slot: injury_reports.Slot, payload: bytes, *, rechecked: bool,
) -> tuple[_PresentReport, tuple[injury_report_parse.Listing, ...]]:
    """Run the opens, parse and title gates on one present body.

    Returns the report and its parsed listings (empty when the body did not
    parse); the caller turns the listings into rows and drops them.
    """
    parsed: injury_report_parse.ParsedReport | None = None
    failed: str | None = None
    try:
        parsed = injury_report_parse.parse_report(payload, report_id=slot.report_id)
    except injury_report_parse.ReportParseError:
        inspection = injury_reports.inspect_pdf(payload)
        if not inspection.opens:
            failed, pages, printed = FAILED_CHECK_OPENS, None, None
        else:
            failed, pages, printed = FAILED_CHECK_PARSE, inspection.pages, inspection.title_printed_et
    else:
        pages, printed = parsed.pages, parsed.title_printed_et

    check = injury_reports.title_check(slot, printed)
    printed_utc: datetime | None = None
    ambiguous = False
    in_gap = False
    if printed is not None:
        try:
            printed_utc, ambiguous = injury_reports.printed_to_utc(printed)
        except InjuryReportError:
            in_gap = True
            check = "failed"
    if failed is None:
        if in_gap:
            failed = FAILED_CHECK_TITLE_GAP
        elif check == "failed":
            failed = FAILED_CHECK_TITLE_WINDOW
        elif check == "unparsed":   # pragma: no cover - parse_report requires a parsed title
            failed = FAILED_CHECK_PARSE
    report = _PresentReport(
        slot=slot,
        season=availability_resolve.version_season(slot.slot_date),
        sha256=hashlib.sha256(payload).hexdigest(),
        bytes=len(payload),
        pages=pages,
        printed_et=printed,
        printed_utc=printed_utc,
        ambiguous=ambiguous,
        title_check=check,
        failed_check=failed,
        rechecked=rechecked,
    )
    return report, (parsed.listings if parsed is not None else ())


def _apply_report_ledger(
    report: _PresentReport, approved_entry: dict | None,
) -> tuple[str, dict | None]:
    """``(parse_status, observed report_versions entry or None)`` for one report.

    A report that passes every gate is ``ok`` and observes nothing. A failed
    gate observes an ``exclude`` entry naming it and excludes the version,
    unless the ledger holds an ``accept_title`` entry that equals the
    observation exactly (same body digest, printed time and slot label) and
    the failed gate is the slot window: then the version is
    ``ok_title_accepted`` and the observed entry mirrors the approval. An
    approved ``exclude`` that differs from the observation still excludes the
    version; :func:`validate_exceptions` reports the drift.
    """
    if report.failed_check is None:
        return "ok", None
    printed = report.printed_et
    base = {
        "sha256": report.sha256,
        "title_printed_et": printed.isoformat() if printed is not None else None,
        "slot_label": report.slot.slot_label,
    }
    acceptance = {**base, "action": "accept_title", "failed_check": None}
    if (
        report.failed_check == FAILED_CHECK_TITLE_WINDOW
        and isinstance(approved_entry, dict)
        and _without_note(approved_entry) == acceptance
    ):
        return "ok_title_accepted", acceptance
    return "excluded", {**base, "action": "exclude", "failed_check": report.failed_check}


def _listing_rows(
    report: _PresentReport, listings: tuple[injury_report_parse.Listing, ...],
) -> list[dict]:
    rows = []
    for listing in listings:
        rows.append({
            "report_id": report.slot.report_id,
            "season": report.season,
            "row_number": listing.row_number,
            "page": listing.page,
            "game_date": listing.game_date,
            "game_time_raw": listing.game_time_raw,
            "matchup_raw": listing.matchup_raw,
            "away_tricode": listing.away_tricode,
            "home_tricode": listing.home_tricode,
            "team_raw": listing.team_raw,
            "is_placeholder": listing.is_placeholder,
            "placeholder_raw": listing.placeholder_raw,
            "player_name_raw": listing.player_name_raw,
            "current_status_raw": listing.current_status_raw,
            "reason_raw": listing.reason_raw,
            "category_raw": listing.category_raw,
            "previous_status_raw": listing.previous_status_raw,
            LISTINGS_SLOT_COLUMN: report.slot.slot_start_et,
        })
    return rows


def _listings_frame(rows: list[dict]) -> pd.DataFrame:
    columns = [*_LISTING_SOURCE_COLUMNS, LISTINGS_SLOT_COLUMN]
    frame = pd.DataFrame.from_records(rows, columns=columns)
    return frame.astype(_LISTING_DTYPES)


def _versions_frame(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame.from_records(
        rows, columns=list(INJURY_REPORT_VERSIONS_SCHEMA.names),
    )
    utc = pd.to_datetime(frame["report_time_printed_utc"], utc=True)
    frame = frame.astype(_VERSION_DTYPES)
    frame["report_time_printed_utc"] = utc.astype("datetime64[us, UTC]")
    return frame


def _plain(value: object) -> object:
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    return value


def identical_listings_to_previous(
    versions: pd.DataFrame, listings: pd.DataFrame,
) -> pd.Series:
    """Flag versions whose listing multiset equals the previous parsed version's.

    ``versions`` needs ``report_id``, ``season``, ``report_slot_et`` and
    ``parse_status``; ``listings`` needs ``report_id`` and
    ``LISTING_IDENTITY_COLUMNS``. Within each season, versions are visited in
    ``(report_slot_et, report_id)`` order; an excluded version is ``False``
    and leaves the chain untouched, the first parsed version of a season is
    ``False``, and a parsed version is ``True`` when the ``Counter`` of its
    listing tuples equals the previous parsed version's. The result is a
    nullable-boolean Series aligned to ``versions``' index.
    """
    for column in ("report_id", "season", "report_slot_et", "parse_status"):
        if column not in versions.columns:
            raise AvailabilityBuildError(f"versions is missing column {column!r}")
    for column in ("report_id", *LISTING_IDENTITY_COLUMNS):
        if column not in listings.columns:
            raise AvailabilityBuildError(f"listings is missing column {column!r}")
    multisets: dict[str, Counter] = {}
    identity = listings[["report_id", *LISTING_IDENTITY_COLUMNS]]
    for row in identity.itertuples(index=False, name=None):
        multisets.setdefault(str(row[0]), Counter())[tuple(_plain(v) for v in row[1:])] += 1

    flags: dict = {}
    previous: dict[int, Counter] = {}
    ordered = versions.sort_values(_VERSION_ORDER, kind="stable")
    for label, row in ordered.iterrows():
        status = str(row["parse_status"])
        if status not in PARSE_STATUSES:
            raise AvailabilityBuildError(
                f"{row['report_id']}: unknown parse_status {status!r}"
            )
        if status == "excluded":
            flags[label] = False
            continue
        season = int(row["season"])
        current = multisets.get(str(row["report_id"]), Counter())
        flags[label] = season in previous and previous[season] == current
        previous[season] = current
    return pd.Series(
        [flags[label] for label in versions.index], index=versions.index, dtype="boolean",
    )


def assemble_part_b(raw_root: Path, part_a: PartA, approved: dict) -> PartB:
    """Build the injury-report frames from the inventories and PDF pairs.

    Every slot of ``injury_reports.enumerate_slots()`` must have an outcome in
    its month's highest inventory generation (which must verify): ``present``
    with a PDF pair whose digest and length the generation recorded, or
    ``unavailable`` with evidence on at least two UTC dates; an entry the
    grids do not enumerate, a PDF pair the generation does not record and
    anything else fail closed. Each present body runs the opens, parse and
    title gates (:func:`_apply_report_ledger` says how the approved
    ``report_versions`` section applies); parsed listings are resolved through
    ``availability_resolve`` with the approved ``schedule_dates`` and
    ``team_resolution`` sections. ``versions`` is ordered ``season,
    report_slot_et, report_id`` and ``listings`` ``season, report_slot_et,
    report_id, row_number``; ``listings`` carries the published columns and
    then :data:`LISTINGS_SLOT_COLUMN`.

    Manifest inputs: ``inventories`` (``month``, ``generation`` and the
    generation body ``path`` relative to ``data_dir``), ``slot_totals`` per
    grid (``present``, ``unavailable`` and ``rechecked``, the slots probed on
    more than one occasion: every unavailable slot plus the present slots
    that carry earlier unavailable evidence), and per version season
    ``parse_counts`` (by ``parse_status``), ``title_counts`` (by title-check
    result) and ``placeholder_counts`` (rows by placeholder phrase).
    """
    data_dir = _data_dir(raw_root)
    _validate_ledger_document(approved, "approved exceptions")

    slots = injury_reports.enumerate_slots()
    by_month: dict[str, list[injury_reports.Slot]] = {}
    for slot in slots:
        by_month.setdefault(slot.month, []).append(slot)

    approved_reports = approved["report_versions"]
    inventories: list[dict] = []
    slot_totals = {
        grid: {"present": 0, "unavailable": 0, "rechecked": 0} for grid in injury_reports.GRIDS
    }
    reports: list[tuple[_PresentReport, str]] = []
    observed: dict[str, dict] = {}
    listing_rows: list[dict] = []
    # Each present body is gated and reduced to its version fields and listing
    # rows as soon as it is read, then released: a whole-tree build never
    # holds every body or every parsed Listing at once.
    for month in sorted(by_month):
        current, path = _current_generation(data_dir, month)
        inventories.append({
            "month": month,
            "generation": int(current["generation"]),
            "path": path.relative_to(data_dir).as_posix(),
        })
        entries = current["slots"]
        month_ids = {slot.report_id for slot in by_month[month]}
        stray = sorted(set(entries) - month_ids)
        if stray:
            raise AvailabilityBuildError(
                f"inventory {month} carries slots the grids do not enumerate: {stray}"
            )
        for slot in by_month[month]:
            if slot.report_id not in entries:
                raise AvailabilityBuildError(
                    f"{slot.report_id}: enumerated slot has no outcome in inventory "
                    f"{month} gen-{current['generation']:04d}.json"
                )
            status, payload, rechecked = _slot_outcome(
                data_dir, month, slot, entries[slot.report_id],
            )
            totals = slot_totals[slot.grid]
            totals[status] += 1
            totals["rechecked"] += rechecked
            if payload is None:
                continue
            report, parsed_listings = _gate_present(slot, payload, rechecked=rechecked)
            del payload
            status, entry = _apply_report_ledger(
                report, approved_reports.get(slot.report_id),
            )
            if entry is not None:
                observed[slot.report_id] = entry
            reports.append((report, status))
            if status != "excluded":
                listing_rows.extend(_listing_rows(report, parsed_listings))
            del parsed_listings

    index = availability_resolve.build_game_index(
        part_a.schedule, part_a.rosters, part_a.team_strings, part_a.tricodes,
        schedule_exceptions=approved["schedule_dates"],
    )
    resolved = availability_resolve.resolve_listings(
        _listings_frame(listing_rows), index, approved["team_resolution"],
    )
    team_observed = availability_resolve.team_resolution_observed(resolved, index)
    listings = resolved[[*INJURY_REPORT_LISTINGS_SCHEMA.names, LISTINGS_SLOT_COLUMN]]
    listings = listings.sort_values(_LISTING_ORDER, kind="stable").reset_index(drop=True)

    placeholder = listings["is_placeholder"].astype(bool)
    grouped = listings.groupby("report_id", sort=False)
    rows_per_report = grouped.size()
    placeholders_per_report = listings[placeholder].groupby("report_id", sort=False).size()
    games = listings.drop_duplicates(["report_id", "game_date", "matchup_raw"])
    games_per_report = games.groupby("report_id", sort=False).size()
    resolved_per_report = games[games["game_resolution"] == "resolved"].groupby(
        "report_id", sort=False,
    ).size()

    def count(series: pd.Series, report_id: str) -> int:
        return int(series.get(report_id, 0))

    version_rows: list[dict] = []
    parse_counts: dict[int, dict[str, int]] = {}
    title_counts: dict[int, dict[str, int]] = {}
    placeholder_counts: dict[int, dict[str, int]] = {}
    for report, status in reports:
        report_id = report.slot.report_id
        parse_counts.setdefault(report.season, dict.fromkeys(PARSE_STATUSES, 0))[status] += 1
        title_counts.setdefault(report.season, dict.fromkeys(TITLE_CHECKS, 0))[report.title_check] += 1
        placeholder_counts.setdefault(report.season, {})
        excluded = status == "excluded"
        version_rows.append({
            "report_id": report_id,
            "season": report.season,
            "grid": report.slot.grid,
            "slot_date": report.slot.slot_date,
            "slot_label": report.slot.slot_label,
            "report_slot_et": report.slot.slot_start_et,
            "report_time_printed_et": report.printed_et,
            "report_time_printed_utc": report.printed_utc,
            "report_time_printed_utc_ambiguous": report.ambiguous,
            "url": report.slot.url,
            "sha256": report.sha256,
            "bytes": report.bytes,
            "pages": report.pages,
            "listing_rows": None if excluded else (
                count(rows_per_report, report_id) - count(placeholders_per_report, report_id)
            ),
            "placeholder_rows": None if excluded else count(placeholders_per_report, report_id),
            "games_listed": None if excluded else count(games_per_report, report_id),
            "games_resolved": None if excluded else count(resolved_per_report, report_id),
            "parse_status": status,
            "identical_listings_to_previous": False,
        })
    for (season, phrase), rows in listings[placeholder].groupby(
        ["season", "placeholder_raw"], sort=True,
    ).size().items():
        placeholder_counts[int(season)][str(phrase)] = int(rows)

    versions = _versions_frame(version_rows)
    versions = versions.sort_values(_VERSION_ORDER, kind="stable").reset_index(drop=True)
    versions["identical_listings_to_previous"] = identical_listings_to_previous(versions, listings)

    return PartB(
        versions=versions,
        listings=listings,
        report_versions_observed=dict(sorted(observed.items())),
        team_resolution_observed=team_observed,
        inventories=inventories,
        slot_totals=slot_totals,
        parse_counts=dict(sorted(parse_counts.items())),
        title_counts=dict(sorted(title_counts.items())),
        placeholder_counts=dict(sorted(placeholder_counts.items())),
    )


# --------------------------------------------------------------------------
# partitions
# --------------------------------------------------------------------------


def table_from_frame(frame: pd.DataFrame, schema: pa.Schema) -> pa.Table:
    """Convert ``frame`` to an Arrow table of exactly ``schema`` (no metadata).

    The schema's columns are selected in schema order (extra frame columns
    such as :data:`LISTINGS_SLOT_COLUMN` are dropped); a missing column, a
    null in a non-nullable column or a failed conversion raises.
    """
    if not isinstance(frame, pd.DataFrame):
        raise AvailabilityBuildError("frame must be a DataFrame")
    missing = [name for name in schema.names if name not in frame.columns]
    if missing:
        raise AvailabilityBuildError(f"frame is missing column(s) {missing}")
    required = [field.name for field in schema if not field.nullable]
    null_columns = [name for name in required if frame[name].isna().any()]
    if null_columns:
        raise AvailabilityBuildError(f"required columns contain nulls: {null_columns}")
    try:
        table = pa.Table.from_pandas(
            frame[list(schema.names)], schema=schema, preserve_index=False, safe=True,
        )
    except (pa.ArrowException, TypeError, ValueError) as exc:
        raise AvailabilityBuildError(f"Arrow conversion failed: {exc}") from exc
    return table.replace_schema_metadata(None)


def _require_zstd_compression(path: Path) -> None:
    metadata = pq.ParquetFile(path).metadata
    for row_group_index in range(metadata.num_row_groups):
        row_group = metadata.row_group(row_group_index)
        for column_index in range(row_group.num_columns):
            compression = row_group.column(column_index).compression
            if compression != "ZSTD":
                raise AvailabilityBuildError(
                    f"partition {path} row group {row_group_index} column "
                    f"{column_index} is not ZSTD-compressed: {compression}"
                )


def _validate_partition(expected: pa.Table, path: Path) -> None:
    reread = pq.read_table(path)
    if not reread.schema.equals(expected.schema):
        raise AvailabilityBuildError(
            f"partition {path} schema does not match the assembled table"
        )
    if not reread.equals(expected):
        raise AvailabilityBuildError(
            f"partition {path} rows do not match the assembled rows"
        )
    _require_zstd_compression(path)


def write_partition(table: pa.Table, stage_dir: Path, season: int, name: str) -> Path:
    """Write ``stage_dir/season=<season>/<name>.parquet`` deterministically.

    Zstandard, no dictionary encoding, statistics on, then a reread that must
    equal the table exactly. The season directory is shared by the tables of
    one season, so it may exist; the file itself must not.
    """
    stage_dir = Path(stage_dir)
    if name not in PARTITION_SCHEMAS:
        raise AvailabilityBuildError(
            f"partition name must be one of {sorted(PARTITION_SCHEMAS)}: {name!r}"
        )
    if isinstance(season, bool) or not isinstance(season, int):
        raise AvailabilityBuildError(f"season must be an integer: {season!r}")
    if not table.schema.equals(PARTITION_SCHEMAS[name]):
        raise AvailabilityBuildError(
            f"table schema does not match the {name} schema"
        )
    if not stage_dir.is_dir():
        raise AvailabilityBuildError(f"stage directory does not exist: {stage_dir}")
    partition_dir = stage_dir / f"season={season}"
    if partition_dir.exists() and not partition_dir.is_dir():
        raise AvailabilityBuildError(
            f"season partition path is not a directory: {partition_dir}"
        )
    partition_dir.mkdir(exist_ok=True)
    path = partition_dir / f"{name}.parquet"
    try:
        path.lstat()
    except FileNotFoundError:
        pass
    else:
        raise AvailabilityBuildError(f"partition path already exists: {path}")
    pq.write_table(
        table,
        path,
        compression="zstd",
        use_dictionary=False,
        write_statistics=True,
    )
    _validate_partition(table, path)
    return path


def logical_row_sha256(table: pa.Table, schema: pa.Schema) -> str:
    """Hash typed logical rows under ``schema`` independently of Parquet encoding."""
    if not table.schema.equals(schema):
        raise AvailabilityBuildError("table schema does not match the given schema")
    table = table.combine_chunks()
    if table.schema.metadata:
        table = table.replace_schema_metadata(None)
    digest = hashlib.sha256()
    digest.update(schema.serialize().to_pybytes())
    for batch in table.to_batches(max_chunksize=65_536):
        if batch.schema.metadata:
            batch = batch.replace_schema_metadata(None)
        payload = batch.serialize().to_pybytes()
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# derivative agreement (cross-check only; never a gate)
# --------------------------------------------------------------------------


# The common row representation both sources are mapped into, in order.
COMPARISON_FIELDS = (
    "game_date", "matchup", "team", "kind", "player_name", "status", "reason",
    "category", "previous_status",
)
_OUR_COMPARISON_COLUMNS = (
    "report_id", "season", "game_date", "matchup_raw", "team_raw", "is_placeholder",
    "placeholder_raw", "player_name_raw", "current_status_raw", "reason_raw",
    "category_raw", "previous_status_raw",
)
_AGREEMENT_COUNTS = (
    "agree_all", "agree_except_reason", "only_ours", "only_theirs", "derivative_artifacts",
)
_WHITESPACE = re.compile(r"\s+")
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def layout_fields(season: int) -> tuple[str, ...]:
    """The comparison fields a version season's report layout carries.

    Reports from the 2021 season onwards print neither ``Category`` nor
    ``Previous Status``; the 2020 layout prints ``Previous Status`` only;
    2019 prints both.
    """
    if season >= 2021:
        return COMPARISON_FIELDS[:-2]
    if season == 2020:
        return tuple(field for field in COMPARISON_FIELDS if field != "category")
    return COMPARISON_FIELDS


def _is_null(value: object) -> bool:
    if value is None or value is pd.NA or value is pd.NaT:
        return True
    try:
        result = pd.isna(value)
    except (TypeError, ValueError):
        return False
    return result is True or (isinstance(result, bool) and result)


def _collapsed(value: object) -> str | None:
    if _is_null(value):
        return None
    text = _WHITESPACE.sub(" ", str(value)).strip()
    return text or None


def _iso_date(value: object) -> str | None:
    if _is_null(value):
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return _collapsed(value)


def _row_tuple(record: Mapping[str, object], fields: tuple[str, ...]) -> tuple:
    return tuple(
        _iso_date(record[field]) if field == "game_date" else _collapsed(record[field])
        for field in fields
    )


def _our_multiset(ours: pd.DataFrame, fields: tuple[str, ...]) -> Counter:
    counter: Counter = Counter()
    for row in ours.itertuples(index=False):
        placeholder = bool(row.is_placeholder)
        record = {
            "game_date": row.game_date,
            "matchup": row.matchup_raw,
            "team": row.team_raw,
            "kind": row.placeholder_raw if placeholder else "player",
            "player_name": None if placeholder else row.player_name_raw,
            "status": None if placeholder else row.current_status_raw,
            "reason": None if placeholder else row.reason_raw,
            "category": row.category_raw,
            "previous_status": row.previous_status_raw,
        }
        counter[_row_tuple(record, fields)] += 1
    return counter


def _their_multiset(theirs: pd.DataFrame, fields: tuple[str, ...]) -> tuple[Counter, int]:
    counter: Counter = Counter()
    artifacts = 0
    for row in theirs.itertuples(index=False):
        if bool(row.artifact):
            artifacts += 1
            continue
        record = {field: getattr(row, field) for field in COMPARISON_FIELDS}
        counter[_row_tuple(record, fields)] += 1
    return counter, artifacts


def _sort_key(values: tuple) -> tuple:
    return tuple((value is not None, "" if value is None else str(value)) for value in values)


def _describe(values: tuple, fields: tuple[str, ...]) -> str:
    return " ".join(f"{field}={value!r}" for field, value in zip(fields, values))


def _compare_rows(
    ours: pd.DataFrame, theirs: pd.DataFrame, *, report_id: str, season: int,
) -> dict:
    fields = layout_fields(season)
    ours_rows = _our_multiset(ours, fields)
    theirs_rows, artifacts = _their_multiset(theirs, fields)
    agree_all = sum((ours_rows & theirs_rows).values())
    reason_at = fields.index("reason")

    def without_reason(row: tuple) -> tuple:
        return row[:reason_at] + row[reason_at + 1:]

    def with_reason(key: tuple, reason: str | None) -> tuple:
        return key[:reason_at] + (reason,) + key[reason_at:]

    ours_by_key: dict[tuple, list] = {}
    for row, count in (ours_rows - theirs_rows).items():
        ours_by_key.setdefault(without_reason(row), []).extend([row[reason_at]] * count)
    theirs_by_key: dict[tuple, list] = {}
    for row, count in (theirs_rows - ours_rows).items():
        theirs_by_key.setdefault(without_reason(row), []).extend([row[reason_at]] * count)

    patterns: Counter = Counter()
    agree_except_reason = only_ours = only_theirs = 0
    for key in sorted(set(ours_by_key) | set(theirs_by_key), key=_sort_key):
        ours_reasons = sorted(ours_by_key.get(key, []), key=lambda reason: _sort_key((reason,)))
        theirs_reasons = sorted(theirs_by_key.get(key, []), key=lambda reason: _sort_key((reason,)))
        paired = min(len(ours_reasons), len(theirs_reasons))
        for ours_reason, theirs_reason in zip(ours_reasons[:paired], theirs_reasons[:paired]):
            agree_except_reason += 1
            patterns[f"reason: ours {ours_reason!r} / theirs {theirs_reason!r}"] += 1
        for reason in ours_reasons[paired:]:
            only_ours += 1
            patterns["only_ours: " + _describe(with_reason(key, reason), fields)] += 1
        for reason in theirs_reasons[paired:]:
            only_theirs += 1
            patterns["only_theirs: " + _describe(with_reason(key, reason), fields)] += 1
    return {
        "report_id": report_id,
        "season": season,
        "fields": list(fields),
        "agree_all": agree_all,
        "agree_except_reason": agree_except_reason,
        "only_ours": only_ours,
        "only_theirs": only_theirs,
        "derivative_artifacts": artifacts,
        "patterns": {
            pattern: {"count": count, "report_ids": [report_id]}
            for pattern, count in sorted(patterns.items())
        },
    }


def _require_columns(frame: pd.DataFrame, columns: tuple[str, ...], label: str) -> None:
    if not isinstance(frame, pd.DataFrame):
        raise AvailabilityBuildError(f"{label} must be a DataFrame")
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise AvailabilityBuildError(f"{label} is missing column(s) {missing}")


def compare_version(ours: pd.DataFrame, theirs: pd.DataFrame) -> dict:
    """Compare one version's listings with the derivative rows of the same instant.

    ``ours`` holds the published listing columns of exactly one ``report_id``
    (and so one ``season``); ``theirs`` holds ``injury_derivative.comparison_rows``
    rows of one ``report_datetime``. Both are mapped into
    :data:`COMPARISON_FIELDS` restricted to :func:`layout_fields` of the
    season, with whitespace collapsed and ``kind`` the placeholder phrase or
    ``player``, and compared as multisets. Rows equal on every field are
    ``agree_all``; leftovers equal on every field but ``reason`` are paired
    (per key, reasons sorted on each side) as ``agree_except_reason``; the
    rest are ``only_ours`` / ``only_theirs``. Derivative ``artifact`` rows
    are counted separately and never as a disagreement. ``patterns`` keys
    every disagreement by its differing field and normalised difference
    (``reason: ours '…' / theirs '…'``, ``only_ours: …``) with a count and
    the ``report_id``.
    """
    _require_columns(ours, _OUR_COMPARISON_COLUMNS, "ours")
    _require_columns(theirs, injury_derivative.COMPARISON_COLUMNS, "theirs")
    report_ids = sorted({str(value) for value in ours["report_id"]})
    if len(report_ids) != 1:
        raise AvailabilityBuildError(
            f"ours must hold exactly one report_id, found {report_ids}"
        )
    seasons = sorted({int(value) for value in ours["season"]})
    if len(seasons) != 1:
        raise AvailabilityBuildError(
            f"ours must hold exactly one season, found {seasons}"
        )
    return _compare_rows(ours, theirs, report_id=report_ids[0], season=seasons[0])


def _derivative_data_dir(derivative_root: Path) -> Path:
    root = Path(derivative_root)
    expected = Path(injury_derivative.RAW_SUBDIR).parts
    if root.parts[-len(expected):] != expected:
        raise AvailabilityBuildError(
            f"derivative_root must be data_dir / {injury_derivative.RAW_SUBDIR!r}: {root}"
        )
    return root.parents[len(expected) - 1]


def _require_regular_file(path: Path, label: str) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise AvailabilityBuildError(f"missing {label}: {path}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise AvailabilityBuildError(f"{label} must be a regular file: {path}")


def _pair_binding(body_path: Path, data_dir: Path, identity: Mapping[str, object]) -> dict:
    """Bind one raw body and its sidecar by path relative to ``data_dir``, digest and length."""
    body_path = Path(body_path)
    sidecar_path = body_path.with_name(body_path.name + ".meta.json")
    _require_regular_file(body_path, "raw body")
    _require_regular_file(sidecar_path, "raw sidecar")
    body = body_path.read_bytes()
    sidecar = sidecar_path.read_bytes()
    return {
        "body_path": body_path.relative_to(data_dir).as_posix(),
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "body_content_length": len(body),
        "sidecar_path": sidecar_path.relative_to(data_dir).as_posix(),
        "sidecar_sha256": hashlib.sha256(sidecar).hexdigest(),
        "sidecar_content_length": len(sidecar),
        "identity": dict(identity),
    }


def derivative_agreement(part_b: PartB, derivative_root: Path) -> dict:
    """Compare every parsed version with the pinned derivative, per season.

    ``derivative_root`` is ``data_dir / injury_derivative.RAW_SUBDIR``. For
    each version season the attested asset of that season (absent assets
    compare nothing and are reported as ``asset`` ``None``; a damaged one
    fails closed) is normalised with ``comparison_rows`` and grouped by
    ``report_datetime`` at minute precision; a parsed version (``parse_status``
    other than ``excluded``) joins the group equal to its
    ``report_time_printed_et`` and is compared with :func:`compare_version`.
    Versions matched on neither side are listed (``unmatched_ours`` by
    ``report_id``, ``unmatched_theirs`` by ISO minute), not treated as
    errors; several of our versions printing the same minute each compare
    with that group. Counts are summed per season and patterns merged with
    every affected ``report_id``. ``asset`` carries the pair binding for the
    manifest.
    """
    data_dir = _derivative_data_dir(derivative_root)
    versions = part_b.versions
    listings = part_b.listings
    _require_columns(
        versions, ("report_id", "season", "parse_status", "report_time_printed_et"), "versions",
    )
    _require_columns(listings, _OUR_COMPARISON_COLUMNS, "listings")
    by_report = {
        str(report_id): frame for report_id, frame in listings.groupby("report_id", sort=False)
    }
    empty = listings.iloc[0:0]

    result: dict[int, dict] = {}
    for season in sorted({int(value) for value in versions["season"]}):
        season_versions = versions[versions["season"] == season]
        parsed = season_versions[season_versions["parse_status"] != "excluded"]
        asset_binding = None
        by_minute: dict[datetime, pd.DataFrame] = {}
        try:
            cached = injury_derivative.read_cached(data_dir, season)
        except (cache.CacheIntegrityError, injury_derivative.InjuryDerivativeError) as exc:
            raise AvailabilityBuildError(
                f"derivative asset for season {season} does not verify: {exc}"
            ) from exc
        if cached is not None:
            payload, _metadata = cached
            try:
                theirs = injury_derivative.comparison_rows(payload)
            except injury_derivative.InjuryDerivativeError as exc:
                raise AvailabilityBuildError(
                    f"derivative asset for season {season} cannot be normalised: {exc}"
                ) from exc
            asset_binding = _pair_binding(
                injury_derivative.asset_path(data_dir, season), data_dir, {"season": season},
            )
            minutes = pd.to_datetime(theirs["report_datetime"]).dt.floor("min")
            for minute, frame in theirs.groupby(minutes, sort=True):
                by_minute[pd.Timestamp(minute).to_pydatetime()] = frame

        totals: Counter = Counter()
        patterns: dict[str, dict] = {}
        unmatched_ours: list[str] = []
        matched_minutes: set[datetime] = set()
        compared = disagreeing = 0
        for row in parsed[["report_id", "report_time_printed_et"]].itertuples(index=False):
            report_id = str(row.report_id)
            if _is_null(row.report_time_printed_et):
                unmatched_ours.append(report_id)
                continue
            minute = pd.Timestamp(row.report_time_printed_et).floor("min").to_pydatetime()
            theirs_rows = by_minute.get(minute)
            if theirs_rows is None:
                unmatched_ours.append(report_id)
                continue
            matched_minutes.add(minute)
            compared += 1
            comparison = _compare_rows(
                by_report.get(report_id, empty), theirs_rows, report_id=report_id, season=season,
            )
            for key in _AGREEMENT_COUNTS:
                totals[key] += comparison[key]
            if any(comparison[key] for key in ("agree_except_reason", "only_ours", "only_theirs")):
                disagreeing += 1
            for pattern, entry in comparison["patterns"].items():
                merged = patterns.setdefault(pattern, {"count": 0, "report_ids": set()})
                merged["count"] += entry["count"]
                merged["report_ids"].update(entry["report_ids"])
        result[season] = {
            "asset": asset_binding,
            "versions_ours": int(len(parsed)),
            "versions_excluded": int(len(season_versions) - len(parsed)),
            "versions_theirs": len(by_minute),
            "versions_compared": compared,
            "versions_disagreeing": disagreeing,
            **{key: int(totals[key]) for key in _AGREEMENT_COUNTS},
            "unmatched_ours": unmatched_ours,
            "unmatched_theirs": [
                minute.isoformat() for minute in sorted(by_minute) if minute not in matched_minutes
            ],
            "patterns": {
                pattern: {"count": entry["count"], "report_ids": sorted(entry["report_ids"])}
                for pattern, entry in sorted(patterns.items())
            },
        }
    return result


# --------------------------------------------------------------------------
# gates
# --------------------------------------------------------------------------


GATES = (
    "coverage", "exception_ledger", "version_season", "team_resolution",
    "person_resolution", "layout",
)
PARTITION_COUNT = len(ROSTER_SEASONS) * 2 + len(REPORT_SEASONS) * 2   # 46
_PARTITIONS_PER_SEASON = {
    season: tuple(
        name for name in PARTITION_SCHEMAS
        if name in ("game_rosters", "game_schedule") or season in REPORT_SEASONS
    )
    for season in ROSTER_SEASONS
}


def _check_roster_coverage(index: box_rosters.PlayerGamesIndex) -> None:
    seasons = {game.season for game in index.games}
    if seasons != set(ROSTER_SEASONS):
        raise AvailabilityBuildError(
            f"coverage: the player_games worklist covers seasons {sorted(seasons)} but the "
            f"publication needs exactly {list(ROSTER_SEASONS)}"
        )


def _check_report_coverage(versions: pd.DataFrame) -> None:
    seasons = {int(value) for value in versions["season"]}
    if seasons != set(REPORT_SEASONS):
        raise AvailabilityBuildError(
            f"coverage: present reports cover version seasons {sorted(seasons)} but the "
            f"publication needs exactly {list(REPORT_SEASONS)}"
        )


def _check_version_seasons(schedule: pd.DataFrame, listings: pd.DataFrame) -> None:
    """Every resolved game's schedule season must equal the listing's version season."""
    season_by_game = {
        str(game_id): int(season)
        for game_id, season in zip(schedule["game_id"], schedule["season"])
    }
    resolved = listings[listings["game_resolution"] == "resolved"]
    mismatches: set[tuple] = set()
    for row in resolved[["report_id", "game_id", "season"]].itertuples(index=False):
        game_season = season_by_game.get(str(row.game_id))
        if game_season is None:
            raise AvailabilityBuildError(
                f"version-season gate: resolved game {row.game_id} of {row.report_id} is not "
                "in the schedule"
            )
        if game_season != int(row.season):
            mismatches.add((str(row.report_id), str(row.game_id), game_season, int(row.season)))
    if mismatches:
        ordered = sorted(mismatches)
        raise AvailabilityBuildError(
            f"version-season disagreement: {len(ordered)} (report_id, game_id) pairs resolve "
            "to a game of another season than the version (report_id, game_id, game season, "
            f"version season): {ordered[:20]}"
        )


def _check_team_resolution(listings: pd.DataFrame) -> None:
    resolved = listings[listings["game_resolution"] == "resolved"]
    missing = resolved[resolved["nba_team_id"].isna()]
    if len(missing):
        examples = sorted(
            (str(row.report_id), int(row.row_number), str(row.team_raw))
            for row in missing[["report_id", "row_number", "team_raw"]].itertuples(index=False)
        )
        raise AvailabilityBuildError(
            f"team resolution: {len(missing)} listing rows of resolved games carry no "
            f"nba_team_id: {examples[:20]}"
        )


def _check_resolution_floors(metrics: Mapping[int, Mapping]) -> None:
    failures = []
    for season in sorted(metrics):
        for label, key in (("row", "row_rate"), ("identity", "identity_rate")):
            rate = metrics[season][key]
            if rate is not None and rate < PERSON_RESOLUTION_FLOOR:
                failures.append(
                    f"season {season}: person resolution {label} rate {rate:.4f} is below "
                    f"the floor {PERSON_RESOLUTION_FLOOR}"
                )
    if failures:
        raise AvailabilityBuildError("; ".join(failures))


# --------------------------------------------------------------------------
# manifest
# --------------------------------------------------------------------------


def manifest_bytes(manifest: dict) -> bytes:
    """The canonical manifest encoding: sorted keys, two-space indent, no NaN."""
    return (
        json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")


def _capture_file(path: Path, label: str) -> bytes:
    _require_regular_file(Path(path), label)
    return Path(path).read_bytes()


def _require_unchanged(path: Path, payload: bytes, label: str) -> None:
    try:
        current = Path(path).read_bytes()
    except OSError as exc:
        raise AvailabilityBuildError(f"{label} changed during the build") from exc
    if current != payload:
        raise AvailabilityBuildError(f"{label} changed during the build")


def _logical_path(path: Path) -> str:
    """A repository-relative POSIX path, or the file name for a file outside it."""
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(_REPOSITORY_ROOT).as_posix()
    except ValueError:
        return resolved.name


def _requirement_pins() -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in (_REPOSITORY_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        requirement = line.split(";", 1)[0].split("#", 1)[0].strip()
        if "==" in requirement:
            name, version = requirement.split("==", 1)
            pins[name.strip()] = version.strip()
    return pins


def _versions() -> dict:
    """``player_games_build._versions`` plus the PDF stack pins from ``requirements.txt``."""
    pins = _requirement_pins()
    versions = dict(player_games_build._versions())
    for name in ("pdfplumber", "pdfminer-six", "pypdfium2"):
        if name not in pins:
            raise AvailabilityBuildError(f"requirements.txt does not pin {name}")
        versions[name] = pins[name]
    return versions


def _schema_manifest() -> dict:
    return {
        name: [
            {"name": field.name, "arrow_type": str(field.type), "nullable": bool(field.nullable)}
            for field in schema
        ]
        for name, schema in PARTITION_SCHEMAS.items()
    }


def _resolution_summary(metrics: Mapping[int, Mapping]) -> dict:
    def rate(value: object) -> float | None:
        return None if value is None else float(value)

    return {
        str(season): {
            "rows": int(entry["rows"]),
            "rows_resolved": int(entry["rows_resolved"]),
            "row_rate": rate(entry["row_rate"]),
            "identities": int(entry["identities"]),
            "identities_resolved": int(entry["identities_resolved"]),
            "identity_rate": rate(entry["identity_rate"]),
            "listed_not_rostered": {
                "rows": int(entry["listed_not_rostered"]["rows"]),
                "identities": int(entry["listed_not_rostered"]["identities"]),
            },
            "unresolved_matchups": {
                str(pair): int(count)
                for pair, count in sorted(entry["unresolved_matchups"].items())
            },
        }
        for season, entry in sorted(metrics.items())
    }


def _roster_sizes(part_a: PartA) -> dict:
    """Active / inactive counts per season and the team-games outside 8..15 active or with no inactives."""
    counts = part_a.roster_counts.merge(part_a.schedule[["game_id", "season"]], on="game_id", how="left")
    if counts["season"].isna().any():
        raise AvailabilityBuildError("roster counts name a game the schedule lacks")
    by_season = {}
    for season, frame in counts.groupby("season", sort=True):
        by_season[str(int(season))] = {
            "team_games": int(len(frame)),
            "active_min": int(frame["active"].min()),
            "active_max": int(frame["active"].max()),
            "inactive_empty": int((frame["inactive"] == 0).sum()),
        }
    active = counts["active"].astype(int)
    inactive = counts["inactive"].astype(int)
    outliers = counts[(active < 8) | (active > 15) | (inactive == 0)]
    outliers = outliers.sort_values(["game_id", "nba_team_id"], kind="stable")
    return {
        "by_season": by_season,
        "outliers": [
            {
                "game_id": str(row.game_id), "nba_team_id": int(row.nba_team_id),
                "active": int(row.active), "inactive": int(row.inactive),
            }
            for row in outliers.itertuples(index=False)
        ],
    }


def _gamebook_bindings(raw_root: Path, data_dir: Path) -> dict:
    """Bind whatever gamebook pairs exist under ``raw/nba_gamebooks``.

    TODO(task-16): ``import-gamebooks`` writes ``<game_id>.book.pdf`` pairs
    here; TODO(task-21): the review writes ``comparisons/<game_id>.json``
    pairs. Until then both lists are empty.
    """
    root = raw_root / "nba_gamebooks"
    pdfs: list[dict] = []
    comparisons: list[dict] = []
    if root.is_dir():
        for path in sorted(root.glob("*.book.pdf")):
            pdfs.append(_pair_binding(path, data_dir, {"game_id": path.name[: -len(".book.pdf")]}))
        records = root / "comparisons"
        if records.is_dir():
            for path in sorted(records.glob("*.json")):
                if path.name.endswith(".meta.json"):
                    continue
                comparisons.append(_pair_binding(path, data_dir, {"game_id": path.stem}))
    return {"pdfs": pdfs, "comparisons": comparisons}


def _season_frame(frame: pd.DataFrame, season: int) -> pd.DataFrame:
    return frame[frame["season"] == season].reset_index(drop=True)


def _partition_entry(table: pa.Table, path: Path, season: int, name: str) -> dict:
    return {
        "path": f"season={season}/{name}.parquet",
        "rows": int(table.num_rows),
        "bytes": int(path.stat().st_size),
        "sha256": cache.sha256_file(path),
        "row_sha256": logical_row_sha256(table, PARTITION_SCHEMAS[name]),
    }


def _materialize_partitions(
    tables: Mapping[tuple[int, str], pa.Table], publication_dir: Path, *, write: bool,
) -> dict:
    partitions: dict[str, dict] = {}
    for (season, name), table in tables.items():
        if write:
            path = write_partition(table, publication_dir, season, name)
        else:
            path = publication_dir / f"season={season}" / f"{name}.parquet"
            try:
                _validate_partition(table, path)
            except (OSError, pa.ArrowException) as exc:
                raise AvailabilityBuildError(
                    f"partition {path} could not be read: {exc}"
                ) from exc
        partitions.setdefault(str(season), {})[name] = _partition_entry(table, path, season, name)
    return partitions


def _require_empty_stage(stage_dir: Path) -> None:
    try:
        info = stage_dir.lstat()
    except FileNotFoundError as exc:
        raise AvailabilityBuildError(f"stage directory does not exist: {stage_dir}") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise AvailabilityBuildError(f"stage must be a real directory: {stage_dir}")
    if any(stage_dir.iterdir()):
        raise AvailabilityBuildError(f"stage directory must be empty: {stage_dir}")


def _recompute_manifest(
    raw_root: Path,
    player_games_root: Path,
    publication_dir: Path,
    *,
    exceptions_path: Path,
    write_partitions: bool,
) -> dict:
    """Run every gate, write or reread the 46 partitions and compute the manifest."""
    raw_root = Path(raw_root)
    data_dir = _data_dir(raw_root)
    player_games_root = Path(player_games_root)
    publication_dir = Path(publication_dir)
    exceptions_path = Path(exceptions_path)

    ledger_bytes = _capture_file(exceptions_path, "exception ledger")
    approved = load_exceptions(exceptions_path)
    controls_bytes = _capture_file(injury_reports.CONTROLS_PATH, "controls file")
    try:
        injury_reports.load_controls(injury_reports.CONTROLS_PATH)
    except InjuryReportError as exc:
        raise AvailabilityBuildError(f"controls file is invalid: {exc}") from exc
    player_games_manifest_path = player_games_root / box_rosters.PLAYER_GAMES_MANIFEST_NAME
    player_games_manifest_bytes = _capture_file(player_games_manifest_path, "player_games manifest")
    try:
        index = box_rosters.load_player_games_index(player_games_root)
    except box_rosters.BoxRosterValidationError as exc:
        raise AvailabilityBuildError(f"player_games publication is invalid: {exc}") from exc

    # gate: coverage (both bodies per game, every slot's outcome rechecked or
    # present, every present PDF parsed or excluded, every season populated)
    _check_roster_coverage(index)
    try:
        part_a = assemble_part_a(raw_root, index, approved)
        part_b = assemble_part_b(raw_root, part_a, approved)
    except cache.CacheIntegrityError as exc:
        raise AvailabilityBuildError(f"raw pair does not verify: {exc}") from exc
    except availability_resolve.AvailabilityResolveError as exc:
        raise AvailabilityBuildError(f"resolution failed: {exc}") from exc
    _check_report_coverage(part_b.versions)
    # gate: the ledger, all four sections at once
    observed = {
        "appearance_consistency": part_a.appearance_observed,
        "schedule_dates": part_a.schedule_observed,
        "report_versions": part_b.report_versions_observed,
        "team_resolution": part_b.team_resolution_observed,
    }
    validate_exceptions(observed, approved)
    # gate: version-season agreement
    _check_version_seasons(part_a.schedule, part_b.listings)
    # gate: team resolution exact (the ledger gate covers it; this is the proof)
    _check_team_resolution(part_b.listings)
    # gate: the two person-resolution floors per season
    metrics = availability_resolve.resolution_metrics(part_b.listings)
    _check_resolution_floors(metrics)
    # cross-check, never a gate
    agreement = derivative_agreement(part_b, raw_root / "sportsdataverse/nba_stats_injury_report")
    # gate: exactly 46 partitions
    tables: dict[tuple[int, str], pa.Table] = {}
    for season in ROSTER_SEASONS:
        tables[(season, "game_rosters")] = table_from_frame(
            _season_frame(part_a.rosters, season), GAME_ROSTERS_SCHEMA,
        )
        tables[(season, "game_schedule")] = table_from_frame(
            _season_frame(part_a.schedule, season), GAME_SCHEDULE_SCHEMA,
        )
    for season in REPORT_SEASONS:
        tables[(season, "injury_report_versions")] = table_from_frame(
            _season_frame(part_b.versions, season), INJURY_REPORT_VERSIONS_SCHEMA,
        )
        tables[(season, "injury_report_listings")] = table_from_frame(
            _season_frame(part_b.listings, season), INJURY_REPORT_LISTINGS_SCHEMA,
        )
    if len(tables) != PARTITION_COUNT:   # pragma: no cover - fixed by the season tuples
        raise AvailabilityBuildError(f"layout: expected {PARTITION_COUNT} partitions, built {len(tables)}")
    partitions = _materialize_partitions(tables, publication_dir, write=write_partitions)

    # bindings of every consumed raw pair
    box_bindings = sorted(
        (
            _pair_binding(
                box_rosters.cache_path(data_dir, game.season, game.game_id, endpoint), data_dir,
                {"game_id": game.game_id, "endpoint": endpoint, "season": game.season},
            )
            for game in index.games for endpoint in box_rosters.ENDPOINTS
        ),
        key=lambda entry: entry["body_path"],
    )
    pdf_bindings = []
    for report_id in part_b.versions["report_id"]:
        slot = slot_from_report_id(str(report_id))
        pdf_bindings.append(_pair_binding(
            injury_reports.pdf_path(data_dir, slot), data_dir,
            {"report_id": slot.report_id, "grid": slot.grid, "month": slot.month},
        ))
    pdf_bindings.sort(key=lambda entry: entry["body_path"])
    inventories = []
    for entry in part_b.inventories:
        payload = _capture_file(data_dir / entry["path"], f"inventory generation {entry['month']}")
        inventories.append({
            "month": entry["month"], "generation": int(entry["generation"]), "path": entry["path"],
            "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload),
        })
    derivative_bindings = [
        agreement[season]["asset"] for season in sorted(agreement)
        if agreement[season]["asset"] is not None
    ]
    applied = {
        section: {key: _without_note(entry) for key, entry in sorted(approved[section].items())}
        for section in LEDGER_SECTIONS
    }
    ledger_sha256 = hashlib.sha256(ledger_bytes).hexdigest()
    parsed_versions = int((part_b.versions["parse_status"] != "excluded").sum())
    games_by_season = Counter(game.season for game in index.games)

    manifest = {
        "schema_version": 1,
        "scope": {
            "roster_seasons": list(ROSTER_SEASONS),
            "report_seasons": list(REPORT_SEASONS),
            "game_types": sorted({game.game_type for game in index.games}),
            "games": len(index.games),
            "slot_window": [injury_reports.WINDOW[0].isoformat(), injury_reports.WINDOW[1].isoformat()],
            "grids": {
                grid: {
                    "first_slot_date": first.isoformat(),
                    "last_slot_date": last.isoformat(),
                    "minutes_per_slot": minutes,
                }
                for grid, (first, last, minutes) in injury_reports.GRIDS.items()
            },
            "slots": sum(
                totals["present"] + totals["unavailable"] for totals in part_b.slot_totals.values()
            ),
        },
        "schema": _schema_manifest(),
        "versions": _versions(),
        "sources": {
            "box_rosters": box_bindings,
            "injury_reports": {"pdfs": pdf_bindings, "inventories": inventories},
            "derivative": derivative_bindings,
            "gamebooks": _gamebook_bindings(raw_root, data_dir),
        },
        "references": {
            "player_games_manifest": {
                **index.manifest_binding,
                # TODO(task-3): the D1 snapshot itself is produced on the owner's
                # machine by scripts/archive_player_games_snapshot.sh, which names
                # it exactly this way from the manifest digest; Task 22 proves it
                # exists and restores.
                "snapshot_name": "player-games-archive-2026-" + index.manifest_binding["sha256"][:12],
            },
            "controls": {
                "path": _logical_path(injury_reports.CONTROLS_PATH),
                "sha256": hashlib.sha256(controls_bytes).hexdigest(),
            },
            "exception_ledger": {"path": _logical_path(exceptions_path), "sha256": ledger_sha256},
        },
        "exception_ledger": {
            "path": _logical_path(exceptions_path),
            "sha256": ledger_sha256,
            "bytes": len(ledger_bytes),
            "applied": applied,
            "counts": {section: len(applied[section]) for section in LEDGER_SECTIONS},
        },
        "partitions": partitions,
        "validation": {
            "gates": list(GATES),
            "rows": {
                name: sum(table.num_rows for (_season, table_name), table in tables.items() if table_name == name)
                for name in PARTITION_SCHEMAS
            },
            "coverage": {
                "games": len(index.games),
                "box_roster_pairs": len(box_bindings),
                "games_by_season": {str(season): games_by_season[season] for season in ROSTER_SEASONS},
                "slots": {grid: dict(totals) for grid, totals in part_b.slot_totals.items()},
                "reports": {
                    "present": int(len(part_b.versions)),
                    "parsed": parsed_versions,
                    "excluded": int(len(part_b.versions)) - parsed_versions,
                },
            },
            "exceptions": {section: len(observed[section]) for section in LEDGER_SECTIONS},
            "parse_counts": {str(season): dict(counts) for season, counts in part_b.parse_counts.items()},
            "title_counts": {str(season): dict(counts) for season, counts in part_b.title_counts.items()},
            "placeholder_counts": {
                str(season): dict(counts) for season, counts in part_b.placeholder_counts.items()
            },
            "resolution": _resolution_summary(metrics),
            "roster_sizes": _roster_sizes(part_a),
            "derivative_agreement": {
                str(season): {key: value for key, value in entry.items() if key != "asset"}
                for season, entry in agreement.items()
            },
        },
    }
    _require_unchanged(exceptions_path, ledger_bytes, "exception ledger")
    _require_unchanged(injury_reports.CONTROLS_PATH, controls_bytes, "controls file")
    _require_unchanged(player_games_manifest_path, player_games_manifest_bytes, "player_games manifest")
    # The canonical encoding is the manifest: one round trip makes the
    # in-memory document identical to what a reader of ``_manifest.json`` sees.
    return json.loads(manifest_bytes(manifest).decode("utf-8"))


# --------------------------------------------------------------------------
# build, independent validation, publication
# --------------------------------------------------------------------------


def build_from_roots(
    raw_root: Path,
    player_games_root: Path,
    stage_dir: Path,
    *,
    exceptions_path: Path = EXCEPTIONS_PATH,
) -> dict:
    """Build the availability publication into the empty ``stage_dir``.

    ``raw_root`` is ``data_dir / "raw"``; ``player_games_root`` the committed
    ``player_games`` publication. Every gate runs in order; the 46
    partitions and ``_manifest.json`` are written; the manifest is returned.
    """
    stage_dir = Path(stage_dir)
    _require_empty_stage(stage_dir)
    manifest = _recompute_manifest(
        raw_root, player_games_root, stage_dir,
        exceptions_path=exceptions_path, write_partitions=True,
    )
    (stage_dir / "_manifest.json").write_bytes(manifest_bytes(manifest))
    return manifest


def build_tree(data_dir: Path, stage_dir: Path) -> dict:
    """Build from the repository data-directory layout with the packaged ledger."""
    data_dir = Path(data_dir)
    return build_from_roots(
        data_dir / "raw",
        data_dir / box_rosters.PLAYER_GAMES_SUBDIR,
        Path(stage_dir),
        exceptions_path=EXCEPTIONS_PATH,
    )


def _require_exact_publication_layout(publication_dir: Path) -> None:
    """Exactly ``season=YYYY`` directories with their partitions plus ``_manifest.json``; no symlinks."""
    try:
        root_info = publication_dir.lstat()
    except FileNotFoundError as exc:
        raise AvailabilityBuildError(
            f"publication directory does not exist: {publication_dir}"
        ) from exc
    if not stat.S_ISDIR(root_info.st_mode):
        raise AvailabilityBuildError(
            f"publication root must be a real directory: {publication_dir}"
        )
    expected = {"_manifest.json"} | {f"season={season}" for season in ROSTER_SEASONS}
    observed = {entry.name for entry in publication_dir.iterdir()}
    if observed != expected:
        raise AvailabilityBuildError(
            "publication layout mismatch: "
            f"missing={sorted(expected - observed)}, extra={sorted(observed - expected)}"
        )
    manifest_info = (publication_dir / "_manifest.json").lstat()
    if not stat.S_ISREG(manifest_info.st_mode):
        raise AvailabilityBuildError("publication layout: _manifest.json must be a regular file")
    for season in ROSTER_SEASONS:
        directory = publication_dir / f"season={season}"
        if not stat.S_ISDIR(directory.lstat().st_mode):
            raise AvailabilityBuildError(
                f"publication layout: season={season} must be a real directory"
            )
        expected_files = {f"{name}.parquet" for name in _PARTITIONS_PER_SEASON[season]}
        children = {entry.name: entry for entry in directory.iterdir()}
        if set(children) != expected_files:
            raise AvailabilityBuildError(
                f"publication layout mismatch in season={season}: "
                f"missing={sorted(expected_files - set(children))}, "
                f"extra={sorted(set(children) - expected_files)}"
            )
        for name in sorted(children):
            if not stat.S_ISREG(children[name].lstat().st_mode):
                raise AvailabilityBuildError(
                    f"publication layout: season={season}/{name} must be a regular file"
                )


def _read_publication_manifest(publication_dir: Path) -> dict:
    path = publication_dir / "_manifest.json"

    def reject_constant(value: str) -> None:
        raise ValueError(f"invalid numeric constant {value}")

    try:
        payload = path.read_bytes()
        manifest = json.loads(payload.decode("utf-8"), parse_constant=reject_constant)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise AvailabilityBuildError(f"publication manifest is invalid: {path}") from exc
    if not isinstance(manifest, dict):
        raise AvailabilityBuildError("publication manifest must be an object")
    if payload != manifest_bytes(manifest):
        raise AvailabilityBuildError(
            "publication manifest is not canonical deterministic JSON"
        )
    return manifest


MANIFEST_KEYS = (
    "schema_version", "scope", "schema", "versions", "sources", "references",
    "exception_ledger", "partitions", "validation",
)


def validate_publication(
    raw_root: Path,
    player_games_root: Path,
    publication_dir: Path,
    *,
    exceptions_path: Path = EXCEPTIONS_PATH,
) -> dict:
    """Independently recompute and validate a complete publication.

    Walks the published tree without following symlinks and requires exactly
    the 46 partitions plus ``_manifest.json``; reads the stored manifest
    (canonical bytes only); re-runs every gate from the raw trees, the
    ``player_games`` publication and the ledger, rereading every partition
    against the reassembled tables and re-attesting every bound raw pair;
    then compares the recomputed manifest with the stored one key by key.
    Returns the recomputed manifest.
    """
    raw_root = Path(raw_root)
    player_games_root = Path(player_games_root)
    publication_dir = Path(publication_dir)
    _require_exact_publication_layout(publication_dir)
    stored = _read_publication_manifest(publication_dir)
    if set(stored) != set(MANIFEST_KEYS):
        raise AvailabilityBuildError(
            f"manifest top-level keys mismatch: {sorted(stored)}"
        )
    recomputed = _recompute_manifest(
        raw_root, player_games_root, publication_dir,
        exceptions_path=exceptions_path, write_partitions=False,
    )
    for key in MANIFEST_KEYS:
        if stored[key] != recomputed[key]:
            raise AvailabilityBuildError(f"manifest {key} mismatch")
    return recomputed


def run(data_dir: Path) -> None:
    """Build, reread, validate and transactionally publish, offline.

    The stage is a ``tempfile.mkdtemp(prefix=".availability-build-")``
    sibling of ``data_dir / PUBLICATION_SUBDIR``; a failure before
    publication removes it and leaves any prior publication untouched.
    """
    data_dir = Path(data_dir)
    output_dir = data_dir / PUBLICATION_SUBDIR
    staging_root = output_dir.parent
    staging_root.mkdir(parents=True, exist_ok=True)
    stage_dir = Path(tempfile.mkdtemp(prefix=".availability-build-", dir=staging_root))
    stage_info = stage_dir.lstat()
    identity = (stage_info.st_dev, stage_info.st_ino)
    raw_root = data_dir / "raw"
    player_games_root = data_dir / box_rosters.PLAYER_GAMES_SUBDIR
    try:
        manifest = build_tree(data_dir, stage_dir)
        reread = validate_publication(
            raw_root, player_games_root, stage_dir, exceptions_path=EXCEPTIONS_PATH,
        )
        if reread != manifest:
            raise AvailabilityBuildError(
                "staged availability publication failed independent reread"
            )
    except BaseException:
        try:
            current = stage_dir.lstat()
        except FileNotFoundError:
            current = None
        if (
            current is not None
            and stat.S_ISDIR(current.st_mode)
            and (current.st_dev, current.st_ino) == identity
        ):
            shutil.rmtree(stage_dir)
        raise

    rollback = publication.publish_tree(
        stage_dir, output_dir, label="availability publication",
    )
    if rollback is not None:
        raise AvailabilityBuildError(
            "availability publication succeeded but rollback cleanup failed; "
            f"operator cleanup required at {rollback}"
        )
    active = validate_publication(
        raw_root, player_games_root, output_dir, exceptions_path=EXCEPTIONS_PATH,
    )
    if active != manifest:
        raise AvailabilityBuildError(
            "active availability publication failed independent reread"
        )
