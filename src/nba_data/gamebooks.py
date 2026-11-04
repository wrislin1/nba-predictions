"""Official gamebooks for the roster-completeness sample.

The appearance checks in ``box_rosters`` prove that nobody who played is
missing from ``game_rosters``; they cannot see a player who never appeared
and was left off both lists. The spec's second completeness check compares a
sample of at least 30 games line by line with the league's official
gamebooks (``statsdmz.nba.com/pdfs/YYYYMMDD/YYYYMMDD_AWAYHOME_book.pdf``).
That host redirects to ``www.nba.com``, which does not answer this machine,
so the sample PDFs are downloaded by the project owner in a browser and
imported offline with :func:`import_gamebooks`, which checks the ``%PDF``
signature, derives the ``game_id`` from the file name's date and tricodes
against the Part A worklist keys (``box_rosters.game_keys``) and publishes
``raw/nba_gamebooks/<game_id>.book.pdf`` through ``cache.publish_bytes`` with
a sidecar recording the URL, the retrieval date and ``retrieved_by:
browser``. Nothing is written unless every file in the source directory
imports.

:func:`compare_rosters` takes the two teams' active and inactive names as
the gamebook prints them and compares them with ``game_rosters`` per team
and per list; :func:`write_comparison` publishes the record (with the
reviewer's note) as a bound pair under ``raw/nba_gamebooks/comparisons/``;
:func:`select_gamebook_sample` picks the deterministic part of the sample
and leaves the three owner-selected cases as placeholders. The build binds
and re-attests every pair with :func:`validate_pdf` and
:func:`validate_comparison`.

TODO(task-21): ``extract_box_score_names(payload: bytes) -> dict[str,
dict[str, list[str]]]`` reads the names from the gamebook's box-score page
with ``pdfplumber`` on the owner's machine once the sample is downloaded;
until then :func:`compare_rosters` is fed names typed as printed.
"""

from __future__ import annotations

import json
import re
import stat
from collections import Counter
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from nba_data import availability_resolve, cache
from nba_data.box_rosters import GAME_CODE_PATTERN, GAME_ID_PATTERN


RAW_SUBDIR = "raw/nba_gamebooks"
COMPARISONS_SUBDIR = "comparisons"
FILE_NAME_PATTERN = re.compile(r"^(\d{8})_([A-Z]{3})([A-Z]{3})_book\.pdf$")
GAMEBOOK_BASE_URL = "https://statsdmz.nba.com/pdfs/"
PDF_SIGNATURE = b"%PDF"
PROVIDER = "nba_official"
RESOURCE = "gamebook"
COMPARISON_RESOURCE = "gamebook_comparison"
RETRIEVED_BY = "browser"

ROSTER_LISTS = ("active", "inactive")
ACTIVE_STATUSES = frozenset({"played", "active_not_played"})
INACTIVE_STATUSES = frozenset({"inactive"})
COMPARISON_KEYS = frozenset({"game_id", "teams", "differences", "reviewer_note"})
TEAM_COMPARISON_KEYS = frozenset({
    "gamebook_active", "gamebook_inactive", "roster_active", "roster_inactive",
    "only_in_gamebook", "only_in_roster",
})

# The deterministic sample: two games per season, the targeted cases the
# tree can select, and the three cases only the owner can name.
SEASON_CASES = ("earliest_regular", "latest_playoff")
TARGETED_CASES = ("empty_inactive_list", "bubble_2020")
OWNER_SELECTED_CASES = ("two_way", "trade_within_two_days", "season_long_absence")
BUBBLE_SEASON = 2020
BUBBLE_START = date(2020, 7, 30)
SAMPLE_KEYS = ("case", "season", "game_id", "game_date", "game_code_raw", "nba_team_id")

_SCHEDULE_COLUMNS = ("game_id", "season", "game_type", "game_date", "game_code_raw")
_ROSTER_COUNT_COLUMNS = ("game_id", "nba_team_id", "inactive")
_ROSTER_COLUMNS = (
    "game_id", "team_tricode_raw", "roster_status", "first_name_raw", "family_name_raw",
)


class GamebookError(ValueError):
    """Raised when a gamebook file, record or sample input violates its contract."""


# --------------------------------------------------------------------------
# paths, URL and validators
# --------------------------------------------------------------------------


def gamebook_url(game_code_raw: str) -> str:
    """``"20250605/INDOKC"`` -> the official gamebook URL for that game."""
    if not isinstance(game_code_raw, str) or not GAME_CODE_PATTERN.match(game_code_raw):
        raise GamebookError(f"game code must match YYYYMMDD/AAAHHH: {game_code_raw!r}")
    day, teams = game_code_raw.split("/")
    return f"{GAMEBOOK_BASE_URL}{day}/{day}_{teams}_book.pdf"


def gamebook_path(data_dir: Path, game_id: str) -> Path:
    return Path(data_dir) / RAW_SUBDIR / f"{game_id}.book.pdf"


def comparison_path(data_dir: Path, game_id: str) -> Path:
    return Path(data_dir) / RAW_SUBDIR / COMPARISONS_SUBDIR / f"{game_id}.json"


def validate_pdf(path: Path) -> None:
    """Accept a gamebook body only when it begins with the ``%PDF`` signature."""
    try:
        with Path(path).open("rb") as stream:
            head = stream.read(len(PDF_SIGNATURE))
    except OSError as exc:
        raise GamebookError(f"cannot read gamebook body {path}: {exc}") from exc
    if head != PDF_SIGNATURE:
        raise GamebookError(f"gamebook body does not begin with %PDF: {path}")


def _decode_object(payload: bytes, label: str) -> dict:
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GamebookError(f"{label} is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise GamebookError(f"{label} must be a JSON object")
    return document


def validate_comparison(path: Path) -> None:
    """Accept a comparison record body only when it is a UTF-8 JSON object."""
    try:
        payload = Path(path).read_bytes()
    except OSError as exc:
        raise GamebookError(f"cannot read comparison record {path}: {exc}") from exc
    _decode_object(payload, f"comparison record {path}")


def _require_identity(metadata: Mapping, expected: Mapping[str, object], path: Path) -> None:
    if any(key not in metadata or metadata[key] != value for key, value in expected.items()):
        raise cache.CacheIntegrityError(f"gamebook cache metadata mismatch for {path}")


def read_cached_gamebook(data_dir: Path, game_id: str) -> tuple[bytes, dict] | None:
    """Return the attested gamebook pair for ``game_id``, or ``None`` when absent."""
    path = gamebook_path(data_dir, game_id)
    attested = cache.read_attested(path, validate_pdf)
    if attested is None:
        return None
    payload, metadata = attested
    _require_identity(
        metadata,
        {"provider": PROVIDER, "resource": RESOURCE, "game_id": game_id, "content_length": len(payload)},
        path,
    )
    return payload, metadata


def read_cached_comparison(data_dir: Path, game_id: str) -> tuple[dict, dict] | None:
    """Return ``(record, metadata)`` of the attested comparison pair, or ``None`` when absent."""
    path = comparison_path(data_dir, game_id)
    attested = cache.read_attested(path, validate_comparison)
    if attested is None:
        return None
    payload, metadata = attested
    _require_identity(
        metadata,
        {
            "provider": PROVIDER, "resource": COMPARISON_RESOURCE, "game_id": game_id,
            "content_length": len(payload),
        },
        path,
    )
    return _decode_object(payload, f"comparison record {path}"), metadata


# --------------------------------------------------------------------------
# import
# --------------------------------------------------------------------------


def _require_date(value: object, label: str) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise GamebookError(f"{label} must be a datetime.date: {value!r}")
    return value


def import_gamebooks(
    data_dir: Path,
    source_dir: Path,
    *,
    game_keys: Mapping[tuple[date, str, str], str],
    retrieved_on: date,
) -> tuple[Path, ...]:
    """Import every browser-downloaded gamebook in ``source_dir``.

    Each regular file must be named ``YYYYMMDD_AWAYHOME_book.pdf``, begin
    with ``%PDF`` and map through ``game_keys`` (``(game_date, away, home)``
    -> ``game_id``) to a worklist game; the file is published at
    ``data_dir / raw/nba_gamebooks/<game_id>.book.pdf`` with a sidecar
    carrying ``provider``, ``resource``, ``game_id``, ``game_code``, ``url``,
    ``retrieved_on`` (ISO) and ``retrieved_by``. Every file is checked before
    the first is written, so a non-PDF, an unknown key, a malformed name, two
    files for one game, or an existing pair with a different body all raise
    and write nothing; an existing pair with the identical body is kept.
    Returns the published paths in source-file order.
    """
    data_dir = Path(data_dir)
    source_dir = Path(source_dir)
    retrieved_on = _require_date(retrieved_on, "retrieved_on")
    if not isinstance(game_keys, Mapping):
        raise GamebookError("game_keys must be a mapping of (game_date, away, home) to game_id")
    try:
        info = source_dir.lstat()
    except FileNotFoundError:
        raise GamebookError(f"gamebook source directory does not exist: {source_dir}") from None
    if not stat.S_ISDIR(info.st_mode):
        raise GamebookError(f"gamebook source must be a directory: {source_dir}")

    planned: list[tuple[Path, bytes, dict, bool]] = []
    seen: dict[str, str] = {}
    for entry in sorted(source_dir.iterdir(), key=lambda path: path.name):
        if not stat.S_ISREG(entry.lstat().st_mode):
            raise GamebookError(f"gamebook source holds a non-regular file: {entry.name}")
        match = FILE_NAME_PATTERN.match(entry.name)
        if match is None:
            raise GamebookError(
                f"gamebook file name must be YYYYMMDD_AWAYHOME_book.pdf: {entry.name}"
            )
        day, away, home = match.groups()
        try:
            game_date = datetime.strptime(day, "%Y%m%d").date()
        except ValueError:
            raise GamebookError(f"gamebook file name carries an invalid date: {entry.name}") from None
        payload = entry.read_bytes()
        if not payload.startswith(PDF_SIGNATURE):
            raise GamebookError(f"gamebook body does not begin with %PDF: {entry.name}")
        game_id = game_keys.get((game_date, away, home))
        if game_id is None:
            raise GamebookError(
                f"no worklist game on {game_date.isoformat()} for {away}@{home}: {entry.name}"
            )
        if game_id in seen:
            raise GamebookError(
                f"two source files name game {game_id}: {seen[game_id]} and {entry.name}"
            )
        seen[game_id] = entry.name
        game_code = f"{day}/{away}{home}"
        metadata = {
            "provider": PROVIDER,
            "resource": RESOURCE,
            "game_id": game_id,
            "game_code": game_code,
            "url": gamebook_url(game_code),
            "retrieved_on": retrieved_on.isoformat(),
            "retrieved_by": RETRIEVED_BY,
        }
        destination = gamebook_path(data_dir, game_id)
        existing = read_cached_gamebook(data_dir, game_id)
        if existing is not None and existing[0] != payload:
            raise GamebookError(
                f"gamebook pair for game {game_id} already exists with a different body "
                f"({destination}); complete pairs are never replaced"
            )
        planned.append((destination, payload, metadata, existing is None))
    if not planned:
        raise GamebookError(f"no gamebook files to import in {source_dir}")

    written: list[Path] = []
    for destination, payload, metadata, publish in planned:
        if publish:
            cache.publish_bytes(destination, payload, metadata, validate_pdf)
        written.append(destination)
    return tuple(written)


# --------------------------------------------------------------------------
# comparison
# --------------------------------------------------------------------------


def _is_null(value: object) -> bool:
    try:
        result = pd.isna(value)
    except (TypeError, ValueError):
        return False
    return result is True or (isinstance(result, bool) and result)


def _require_frame(frame: object, columns: tuple[str, ...], label: str) -> None:
    if not isinstance(frame, pd.DataFrame):
        raise GamebookError(f"{label} must be a DataFrame")
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise GamebookError(f"{label} is missing column(s) {missing}")


def _name_list(values: object, label: str) -> list[str]:
    if not isinstance(values, (list, tuple)):
        raise GamebookError(f"{label} must be a list of names as printed")
    names = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise GamebookError(f"{label} holds a name that is not a non-empty string: {value!r}")
        names.append(value)
    return names


def _printed_names(gamebook_names: object) -> dict[str, dict[str, list[str]]]:
    if not isinstance(gamebook_names, Mapping) or not gamebook_names:
        raise GamebookError("gamebook_names must map each tricode to its active and inactive lists")
    result: dict[str, dict[str, list[str]]] = {}
    for tricode, lists in gamebook_names.items():
        if not isinstance(tricode, str) or not tricode:
            raise GamebookError(f"gamebook tricode must be a non-empty string: {tricode!r}")
        if not isinstance(lists, Mapping) or set(lists) != set(ROSTER_LISTS):
            raise GamebookError(
                f"gamebook_names[{tricode!r}] must carry exactly the keys {list(ROSTER_LISTS)}"
            )
        result[tricode] = {
            kind: _name_list(lists[kind], f"gamebook_names[{tricode!r}][{kind!r}]")
            for kind in ROSTER_LISTS
        }
    return result


def _roster_name(first: object, family: object) -> str:
    parts = [str(part).strip() for part in (first, family) if not _is_null(part) and str(part).strip()]
    return " ".join(parts)


def _normalize(name: str) -> str:
    return availability_resolve.normalize_name(name)


def _only_in(left: list[str], right: list[str]) -> list[str]:
    """Names of ``left`` (as printed, in order) with no normalised match left in ``right``."""
    remaining = Counter(_normalize(name) for name in right)
    only: list[str] = []
    for name in left:
        key = _normalize(name)
        if remaining[key] > 0:
            remaining[key] -= 1
        else:
            only.append(name)
    return only


def _roster_lists(game_id: str, rosters: pd.DataFrame) -> dict[str, dict[str, list[str]]]:
    _require_frame(rosters, _ROSTER_COLUMNS, "rosters")
    if not isinstance(game_id, str) or not GAME_ID_PATTERN.match(game_id):
        raise GamebookError(f"game_id must be a ten-digit string: {game_id!r}")
    game = rosters[rosters["game_id"].astype(str) == game_id]
    if game.empty:
        raise GamebookError(f"rosters hold no rows for game {game_id}")
    lists: dict[str, dict[str, list[str]]] = {}
    for row in game[list(_ROSTER_COLUMNS)].itertuples(index=False):
        status = str(row.roster_status)
        if status in ACTIVE_STATUSES:
            kind = "active"
        elif status in INACTIVE_STATUSES:
            kind = "inactive"
        else:
            raise GamebookError(f"game {game_id} carries unknown roster_status {status!r}")
        team = lists.setdefault(str(row.team_tricode_raw), {kind: [] for kind in ROSTER_LISTS})
        team[kind].append(_roster_name(row.first_name_raw, row.family_name_raw))
    for team in lists.values():
        for kind in ROSTER_LISTS:
            team[kind] = sorted(team[kind], key=lambda name: (_normalize(name), name))
    return lists


def compare_rosters(
    game_id: str,
    gamebook_names: Mapping[str, Mapping[str, list[str]]],
    rosters: pd.DataFrame,
) -> dict:
    """Compare the gamebook's printed names with ``game_rosters`` per team and list.

    ``gamebook_names`` maps each tricode to ``{"active": [...], "inactive":
    [...]}`` exactly as the gamebook prints the names; ``rosters`` holds
    ``game_rosters`` rows (``played`` and ``active_not_played`` are the active
    list, ``inactive`` the inactive list, names rendered ``first family``).
    The tricodes must be the game's two. Names match when equal under
    ``availability_resolve.normalize_name`` (diacritics, punctuation, case
    and spacing never count); every other name is reported as printed under
    ``only_in_gamebook`` / ``only_in_roster`` per list, and ``differences``
    counts them all. ``reviewer_note`` starts ``None`` for the reviewer.
    """
    printed = _printed_names(gamebook_names)
    roster = _roster_lists(game_id, rosters)
    if set(printed) != set(roster):
        raise GamebookError(
            f"gamebook tricodes {sorted(printed)} differ from the game's roster tricodes "
            f"{sorted(roster)} for game {game_id}"
        )
    teams: dict[str, dict] = {}
    differences = 0
    for tricode in sorted(roster):
        only_in_gamebook = {
            kind: _only_in(printed[tricode][kind], roster[tricode][kind]) for kind in ROSTER_LISTS
        }
        only_in_roster = {
            kind: _only_in(roster[tricode][kind], printed[tricode][kind]) for kind in ROSTER_LISTS
        }
        differences += sum(len(names) for names in only_in_gamebook.values())
        differences += sum(len(names) for names in only_in_roster.values())
        teams[tricode] = {
            "gamebook_active": list(printed[tricode]["active"]),
            "gamebook_inactive": list(printed[tricode]["inactive"]),
            "roster_active": list(roster[tricode]["active"]),
            "roster_inactive": list(roster[tricode]["inactive"]),
            "only_in_gamebook": only_in_gamebook,
            "only_in_roster": only_in_roster,
        }
    return {"game_id": game_id, "teams": teams, "differences": differences, "reviewer_note": None}


def _validate_record(record: object) -> dict:
    if not isinstance(record, dict) or set(record) != COMPARISON_KEYS:
        raise GamebookError(
            f"comparison record must carry exactly the keys {sorted(COMPARISON_KEYS)}"
        )
    game_id = record["game_id"]
    if not isinstance(game_id, str) or not GAME_ID_PATTERN.match(game_id):
        raise GamebookError(f"comparison record game_id must be a ten-digit string: {game_id!r}")
    teams = record["teams"]
    if not isinstance(teams, dict) or not teams:
        raise GamebookError("comparison record teams must be a non-empty object")
    differences = 0
    for tricode, team in teams.items():
        if not isinstance(tricode, str) or not isinstance(team, dict) or set(team) != TEAM_COMPARISON_KEYS:
            raise GamebookError(
                f"comparison record team {tricode!r} must carry exactly the keys "
                f"{sorted(TEAM_COMPARISON_KEYS)}"
            )
        for key in ("gamebook_active", "gamebook_inactive", "roster_active", "roster_inactive"):
            _name_list(team[key], f"teams[{tricode!r}][{key!r}]")
        for key in ("only_in_gamebook", "only_in_roster"):
            lists = team[key]
            if not isinstance(lists, dict) or set(lists) != set(ROSTER_LISTS):
                raise GamebookError(
                    f"teams[{tricode!r}][{key!r}] must carry exactly the keys {list(ROSTER_LISTS)}"
                )
            for kind in ROSTER_LISTS:
                differences += len(_name_list(lists[kind], f"teams[{tricode!r}][{key!r}][{kind!r}]"))
    if type(record["differences"]) is not int or record["differences"] != differences:
        raise GamebookError(
            f"comparison record differences must equal the listed differences ({differences}): "
            f"{record['differences']!r}"
        )
    note = record["reviewer_note"]
    if note is not None and (not isinstance(note, str) or not note.strip()):
        raise GamebookError("comparison record reviewer_note must be null or a non-empty string")
    return record


def comparison_bytes(record: dict) -> bytes:
    """The canonical record encoding: sorted keys, two-space indent, verbatim names."""
    return (
        json.dumps(record, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode("utf-8")


def write_comparison(data_dir: Path, record: dict) -> Path:
    """Publish one validated comparison record as a bound pair; returns its path."""
    record = _validate_record(record)
    path = comparison_path(data_dir, record["game_id"])
    cache.publish_bytes(
        path,
        comparison_bytes(record),
        {"provider": PROVIDER, "resource": COMPARISON_RESOURCE, "game_id": record["game_id"]},
        validate_comparison,
    )
    return path


# --------------------------------------------------------------------------
# sample selection
# --------------------------------------------------------------------------


def _as_date(value: object, label: str) -> date:
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise GamebookError(f"{label} must be a date: {value!r}")


def _sample_entry(
    case: str, game: Mapping | None, team_id: int | None = None, *, season: int | None = None,
) -> dict:
    if game is None:
        return {"case": case, "season": season, "game_id": None, "game_date": None,
                "game_code_raw": None, "nba_team_id": team_id}
    return {
        "case": case,
        "season": game["season"],
        "game_id": game["game_id"],
        "game_date": game["game_date"].isoformat(),
        "game_code_raw": game["game_code_raw"],
        "nba_team_id": team_id,
    }


def select_gamebook_sample(schedule: pd.DataFrame, roster_counts: pd.DataFrame) -> list[dict]:
    """The deterministic gamebook sample plus the owner-selected placeholders.

    Per season of ``schedule`` (``game_schedule`` rows): the earliest
    regular-season game and the latest playoff game, ordered by
    ``(game_date, game_id)``. Then the earliest team-game of ``roster_counts``
    with an empty inactive list and the earliest 2020 bubble game
    (``game_date >= 2020-07-30``); each targeted case takes the earliest
    qualifying game not already sampled, so the distinct-game count is the
    largest the tree allows, and falls back to the earliest qualifying game
    when every one is sampled already. A case the tree cannot fill carries
    ``game_id`` ``None``, as do the three owner-selected placeholders
    (``two_way``, ``trade_within_two_days``, ``season_long_absence``). The
    result never depends on input order.
    """
    _require_frame(schedule, _SCHEDULE_COLUMNS, "schedule")
    _require_frame(roster_counts, _ROSTER_COUNT_COLUMNS, "roster_counts")
    games: dict[str, dict] = {}
    for row in schedule[list(_SCHEDULE_COLUMNS)].itertuples(index=False):
        game_id = str(row.game_id)
        if game_id in games:
            raise GamebookError(f"schedule repeats game {game_id}")
        games[game_id] = {
            "game_id": game_id,
            "season": int(row.season),
            "game_type": str(row.game_type),
            "game_date": _as_date(row.game_date, f"schedule game_date of {game_id}"),
            "game_code_raw": str(row.game_code_raw),
        }
    ordered = sorted(games.values(), key=lambda game: (game["game_date"], game["game_id"]))

    sample: list[dict] = []
    sampled: set[str] = set()

    def take(case: str, game: dict | None, team_id: int | None = None, *, season=None) -> None:
        sample.append(_sample_entry(case, game, team_id, season=season))
        if game is not None:
            sampled.add(game["game_id"])

    for season in sorted({game["season"] for game in ordered}):
        regular = [g for g in ordered if g["season"] == season and g["game_type"] == "regular"]
        playoff = [g for g in ordered if g["season"] == season and g["game_type"] == "playoff"]
        take("earliest_regular", regular[0] if regular else None, season=season)
        take("latest_playoff", playoff[-1] if playoff else None, season=season)

    def first_unsampled(candidates: list) -> object:
        for candidate in candidates:
            if candidate[0]["game_id"] not in sampled:
                return candidate
        return candidates[0] if candidates else None

    empty: list[tuple[dict, int]] = []
    for row in roster_counts[list(_ROSTER_COUNT_COLUMNS)].itertuples(index=False):
        if _is_null(row.inactive) or int(row.inactive) != 0:
            continue
        game = games.get(str(row.game_id))
        if game is None:
            raise GamebookError(f"roster_counts name game {row.game_id} the schedule lacks")
        empty.append((game, int(row.nba_team_id)))
    empty.sort(key=lambda item: (item[0]["game_date"], item[0]["game_id"], item[1]))
    chosen = first_unsampled(empty)
    take("empty_inactive_list", *(chosen if chosen is not None else (None, None)))

    bubble = [
        (game,) for game in ordered
        if game["season"] == BUBBLE_SEASON and game["game_date"] >= BUBBLE_START
    ]
    chosen = first_unsampled(bubble)
    take("bubble_2020", None if chosen is None else chosen[0], season=BUBBLE_SEASON)

    for case in OWNER_SELECTED_CASES:
        take(case, None)
    return sample
