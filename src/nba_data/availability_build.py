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
section's mismatch at once (Task 15's ``build_from_roots``).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from nba_data import availability_resolve, box_rosters, cache, injury_report_parse, injury_reports
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
