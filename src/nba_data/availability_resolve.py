"""Resolve official injury-report listings to games, teams and persons.

Listings are matched against the box-score index built from Part A
(``box_rosters.NormalizedGame`` rosters and schedule rows):

* **Game**: ``(game_date, away_tricode, home_tricode)`` against the keys
  derived from each schedule row's ``game_code_raw`` (``YYYYMMDD/AAAHHH``),
  cross-checked at index time against ``game_date`` and the roster
  tricodes. No match is ``no_box_score_game``; two games on one key cannot
  build an index.
* **Team**: ``team_raw`` must equal exactly one of the game's
  ``teamCity + " " + teamName`` strings. A resolved game whose team string
  matches neither team is *not* raised here: the row keeps a null
  ``nba_team_id`` and :func:`team_resolution_observed` exposes it as an
  observed ``team_resolution`` ledger entry so the build (Task 14) can fail
  closed against the approved ledger and the audit can list candidates.
* **Person**: names are normalised (NFKD, combining marks stripped,
  casefolded, ``.`` ``-`` ``'`` ``’`` and whitespace removed) and compared as
  ``family|given`` against the game roster of the resolved team, then the
  season roster of that ``nba_team_id``. Two or more candidates at either
  step are ``unresolved_ambiguous``, never a silent first match.

:func:`resolution_metrics` measures resolution on listing rows and on
distinct listed identities ``(game_id, nba_team_id, normalised name)``,
because hourly re-publication repeats rows and can hide a player who stays
unresolved all season behind an excellent row rate.
"""

from __future__ import annotations

import unicodedata
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Hashable

import pandas as pd

from nba_data.box_rosters import GAME_CODE_PATTERN
from nba_data.injury_reports import WINDOW


FIRST_SLOT_DATE = WINDOW[0]
_NAME_DROP = frozenset(".-'’")
_LISTING_SPLIT = ", "

LISTING_INPUT_COLUMNS = (
    "report_id", "season", "row_number", "game_date", "away_tricode",
    "home_tricode", "team_raw", "is_placeholder", "player_name_raw",
)
RESOLUTION_COLUMNS = (
    "game_id", "game_resolution", "nba_team_id", "franchise_id",
    "person_id", "person_resolution",
)
_RESOLUTION_DTYPES = {
    "game_id": "string", "game_resolution": "string", "nba_team_id": "Int64",
    "franchise_id": "string", "person_id": "Int64", "person_resolution": "string",
}
METRIC_INPUT_COLUMNS = (
    "season", "report_id", "row_number", "report_slot_et", "away_tricode",
    "home_tricode", "is_placeholder", "player_name_raw", "current_status_raw",
    "game_id", "game_resolution", "nba_team_id", "person_id", "person_resolution",
)
METRIC_KEYS = (
    "rows", "rows_resolved", "row_rate", "identities", "identities_resolved",
    "identity_rate", "identity_rate_by_team", "identity_rate_by_month",
    "identity_rate_by_status", "unresolved_names", "listed_not_rostered",
    "unresolved_matchups",
)
_SCHEDULE_COLUMNS = ("game_id", "season", "game_date", "game_code_raw")
_ROSTER_COLUMNS = (
    "game_id", "person_id", "season", "nba_team_id", "team_tricode_raw",
    "franchise_id", "is_home", "first_name_raw", "family_name_raw",
)


class AvailabilityResolveError(ValueError):
    """Raised when an index, a listing frame or a ledger entry breaks its contract."""


# --------------------------------------------------------------------------
# version season and names
# --------------------------------------------------------------------------


def version_season(slot_date: date) -> int:
    """Return the ending season a report slot date belongs to.

    2019 covers 2018-12-17 .. 2019-09-30; 2020 covers 2019-10-01 .. 2020-11-30
    (the bubble Finals ended 2020-10-11); 2021 covers 2020-12-01 ..
    2021-09-30; afterwards 1 October .. 30 September is the ending season.
    """
    if isinstance(slot_date, datetime):
        slot_date = slot_date.date()
    if not isinstance(slot_date, date):
        raise AvailabilityResolveError(f"slot date must be a date: {slot_date!r}")
    if slot_date < FIRST_SLOT_DATE:
        raise AvailabilityResolveError(
            f"slot date {slot_date.isoformat()} is before the first injury-report "
            f"slot {FIRST_SLOT_DATE.isoformat()}"
        )
    if slot_date <= date(2019, 9, 30):
        return 2019
    if slot_date <= date(2020, 11, 30):
        return 2020
    if slot_date <= date(2021, 9, 30):
        return 2021
    return slot_date.year + 1 if slot_date.month >= 10 else slot_date.year


def normalize_name(value: str) -> str:
    """NFKD, strip combining marks, casefold, drop ``.-'’`` and whitespace."""
    if not isinstance(value, str):
        raise AvailabilityResolveError(f"name must be a string: {value!r}")
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return "".join(
        ch for ch in stripped.casefold()
        if ch not in _NAME_DROP and not ch.isspace()
    )


def listing_name_key(player_name_raw: str) -> str:
    """``"Family, Given"`` -> ``normalize(family) + "|" + normalize(given)``.

    Only the first ``", "`` splits; a name without one is all family name
    with an empty given name.
    """
    if not isinstance(player_name_raw, str):
        raise AvailabilityResolveError(
            f"player_name_raw must be a string: {player_name_raw!r}"
        )
    family, _, given = player_name_raw.partition(_LISTING_SPLIT)
    return f"{normalize_name(family)}|{normalize_name(given)}"


def roster_name_key(family_name_raw: str, first_name_raw: str) -> str:
    """Roster-side counterpart of :func:`listing_name_key`."""
    return f"{normalize_name(family_name_raw)}|{normalize_name(first_name_raw)}"


# --------------------------------------------------------------------------
# game index
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GameIndex:
    game_by_key: Mapping[tuple[date, str, str], str]
    teams: Mapping[str, tuple[int, int]]
    team_strings: Mapping[tuple[str, int], str]
    tricodes: Mapping[tuple[str, int], str]
    game_roster: Mapping[tuple[str, int], Mapping[str, frozenset[int]]]
    season_roster: Mapping[tuple[int, int], Mapping[str, frozenset[int]]]
    franchise: Mapping[tuple[str, int], str]


def _is_null(value: object) -> bool:
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _as_date(value: object, label: str) -> date:
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.date()
    if isinstance(value, date):
        return value
    raise AvailabilityResolveError(f"{label} must be a date: {value!r}")


def _as_int(value: object, label: str) -> int:
    if isinstance(value, bool) or _is_null(value):
        raise AvailabilityResolveError(f"{label} must be an integer: {value!r}")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise AvailabilityResolveError(f"{label} must be an integer: {value!r}") from exc
    if number != value:
        raise AvailabilityResolveError(f"{label} must be an integer: {value!r}")
    return number


def _as_str(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise AvailabilityResolveError(f"{label} must be a string: {value!r}")
    return value


def _require_columns(frame: pd.DataFrame, columns: tuple[str, ...], label: str) -> None:
    if not isinstance(frame, pd.DataFrame):
        raise AvailabilityResolveError(f"{label} must be a DataFrame")
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise AvailabilityResolveError(f"{label} is missing column(s) {missing}")


def _parse_game_code(code: object, game_id: str) -> tuple[date, str, str]:
    if not isinstance(code, str) or not GAME_CODE_PATTERN.match(code):
        raise AvailabilityResolveError(
            f"game {game_id}: game_code_raw {code!r} is not YYYYMMDD/AAAHHH"
        )
    stamp, _, tricodes = code.partition("/")
    try:
        code_date = datetime.strptime(stamp, "%Y%m%d").date()
    except ValueError as exc:
        raise AvailabilityResolveError(
            f"game {game_id}: game_code_raw {code!r} has no valid date"
        ) from exc
    return code_date, tricodes[:3], tricodes[3:]


def _validate_schedule_exceptions(
    schedule_exceptions: Mapping[str, dict] | None,
) -> Mapping[str, dict]:
    if schedule_exceptions is None:
        return {}
    if not isinstance(schedule_exceptions, Mapping):
        raise AvailabilityResolveError("schedule_exceptions must be a mapping")
    for key, entry in schedule_exceptions.items():
        _as_str(key, "schedule_dates key")
        if not isinstance(entry, Mapping) or not {"game_code_raw", "game_date"} <= set(entry):
            raise AvailabilityResolveError(
                f"schedule_dates entry {key!r} must carry game_code_raw and game_date"
            )
        _as_str(entry["game_code_raw"], f"schedule_dates entry {key!r} game_code_raw")
        _as_str(entry["game_date"], f"schedule_dates entry {key!r} game_date")
    return schedule_exceptions


def build_game_index(
    schedule: pd.DataFrame,
    rosters: pd.DataFrame,
    team_strings: Mapping[tuple[str, int], str],
    tricodes: Mapping[tuple[str, int], str],
    *,
    schedule_exceptions: Mapping[str, dict] | None = None,
) -> GameIndex:
    """Build the resolution index from Part A frames.

    ``schedule`` holds one ``game_schedule`` row per game (the keys of
    ``NormalizedGame.schedule`` as columns); ``rosters`` holds
    ``game_rosters`` rows; ``team_strings`` and ``tricodes`` are keyed
    ``(game_id, nba_team_id)``, the concatenation of every game's
    ``NormalizedGame.team_strings`` / ``tricodes``. Every schedule game needs
    exactly one away and one home team in ``rosters`` and entries in both
    mappings; the game code's date must equal ``game_date`` and its tricodes
    the roster's away/home tricodes; two games sharing a key raise.

    ``schedule_exceptions`` is the approved ``schedule_dates`` ledger section,
    keyed by ``game_id`` with ``game_code_raw``, ``game_date`` (ISO) and
    ``game_et_raw``. A game in it may carry a game code whose date differs
    from ``game_date``: the entry's ``game_code_raw`` and ``game_date`` must
    equal the row's exactly (drift raises), the date-mismatch check is
    skipped and the game is keyed by the row's ``game_date``, the
    ``player_games`` date. Tricode cross-checks are never relaxed.
    """
    _require_columns(schedule, _SCHEDULE_COLUMNS, "schedule")
    _require_columns(rosters, _ROSTER_COLUMNS, "rosters")
    schedule_exceptions = _validate_schedule_exceptions(schedule_exceptions)

    sides: dict[str, dict[bool, int]] = defaultdict(dict)
    roster_tricodes: dict[tuple[str, int], str] = {}
    franchise: dict[tuple[str, int], str] = {}
    game_roster: dict[tuple[str, int], dict[str, set[int]]] = defaultdict(
        lambda: defaultdict(set)
    )
    season_roster: dict[tuple[int, int], dict[str, set[int]]] = defaultdict(
        lambda: defaultdict(set)
    )
    for row in rosters[list(_ROSTER_COLUMNS)].itertuples(index=False):
        game_id = _as_str(row.game_id, "rosters.game_id")
        team_id = _as_int(row.nba_team_id, f"game {game_id}: rosters.nba_team_id")
        season = _as_int(row.season, f"game {game_id}: rosters.season")
        person_id = _as_int(row.person_id, f"game {game_id}: rosters.person_id")
        if _is_null(row.is_home):
            raise AvailabilityResolveError(f"game {game_id}: rosters.is_home is null")
        is_home = bool(row.is_home)
        tricode = _as_str(row.team_tricode_raw, f"game {game_id}: team_tricode_raw")
        franchise_id = _as_str(row.franchise_id, f"game {game_id}: franchise_id")
        existing = sides[game_id].get(is_home)
        if existing is not None and existing != team_id:
            raise AvailabilityResolveError(
                f"game {game_id}: two {'home' if is_home else 'away'} teams "
                f"({existing} and {team_id}) in rosters"
            )
        sides[game_id][is_home] = team_id
        key = (game_id, team_id)
        if roster_tricodes.setdefault(key, tricode) != tricode:
            raise AvailabilityResolveError(
                f"game {game_id}: team {team_id} has two tricodes in rosters"
            )
        if franchise.setdefault(key, franchise_id) != franchise_id:
            raise AvailabilityResolveError(
                f"game {game_id}: team {team_id} has two franchise ids in rosters"
            )
        name_key = roster_name_key(
            _as_str(row.family_name_raw, f"game {game_id}: family_name_raw"),
            _as_str(row.first_name_raw, f"game {game_id}: first_name_raw"),
        )
        game_roster[key][name_key].add(person_id)
        season_roster[(season, team_id)][name_key].add(person_id)

    game_by_key: dict[tuple[date, str, str], str] = {}
    teams: dict[str, tuple[int, int]] = {}
    for row in schedule[list(_SCHEDULE_COLUMNS)].itertuples(index=False):
        game_id = _as_str(row.game_id, "schedule.game_id")
        if game_id in teams:
            raise AvailabilityResolveError(f"game {game_id}: appears twice in schedule")
        game_date = _as_date(row.game_date, f"game {game_id}: schedule.game_date")
        code_date, code_away, code_home = _parse_game_code(row.game_code_raw, game_id)
        exception = schedule_exceptions.get(game_id)
        if exception is not None:
            if exception["game_code_raw"] != row.game_code_raw:
                raise AvailabilityResolveError(
                    f"schedule_dates entry {game_id!r} game_code_raw "
                    f"{exception['game_code_raw']!r} differs from the schedule's "
                    f"{row.game_code_raw!r}"
                )
            if exception["game_date"] != game_date.isoformat():
                raise AvailabilityResolveError(
                    f"schedule_dates entry {game_id!r} game_date "
                    f"{exception['game_date']!r} differs from the schedule's "
                    f"{game_date.isoformat()!r}"
                )
        elif code_date != game_date:
            raise AvailabilityResolveError(
                f"game {game_id}: game_code_raw date {code_date.isoformat()} differs "
                f"from game_date {game_date.isoformat()}"
            )
        game_sides = sides.get(game_id, {})
        if set(game_sides) != {False, True}:
            raise AvailabilityResolveError(
                f"game {game_id}: rosters must hold exactly one away and one home team"
            )
        away_id, home_id = game_sides[False], game_sides[True]
        for team_id, code_tricode, side in ((away_id, code_away, "away"), (home_id, code_home, "home")):
            key = (game_id, team_id)
            roster_tricode = roster_tricodes[key]
            if key not in tricodes:
                raise AvailabilityResolveError(f"game {game_id}: team {team_id} missing from tricodes")
            if key not in team_strings:
                raise AvailabilityResolveError(
                    f"game {game_id}: team {team_id} missing from team_strings"
                )
            if not (code_tricode == roster_tricode == tricodes[key]):
                raise AvailabilityResolveError(
                    f"game {game_id}: {side} tricode disagrees between game_code_raw "
                    f"({code_tricode}), rosters ({roster_tricode}) and tricodes ({tricodes[key]})"
                )
            _as_str(team_strings[key], f"game {game_id}: team_strings[{key}]")
        match_key = (game_date, code_away, code_home)
        if match_key in game_by_key:
            raise AvailabilityResolveError(
                f"key {game_date.isoformat()} {code_away}@{code_home} matches two games: "
                f"{game_by_key[match_key]} and {game_id}"
            )
        game_by_key[match_key] = game_id
        teams[game_id] = (away_id, home_id)

    unscheduled = sorted(set(sides) - set(teams))
    if unscheduled:
        raise AvailabilityResolveError(
            f"roster games without a schedule row: {unscheduled[:5]}"
        )

    scheduled_keys = {(game_id, team_id) for game_id, pair in teams.items() for team_id in pair}
    return GameIndex(
        game_by_key=game_by_key,
        teams=teams,
        team_strings={key: team_strings[key] for key in scheduled_keys},
        tricodes={key: tricodes[key] for key in scheduled_keys},
        game_roster={
            key: {name: frozenset(ids) for name, ids in names.items()}
            for key, names in game_roster.items()
        },
        season_roster={
            key: {name: frozenset(ids) for name, ids in names.items()}
            for key, names in season_roster.items()
        },
        franchise=dict(franchise),
    )


# --------------------------------------------------------------------------
# the three resolvers
# --------------------------------------------------------------------------


def resolve_game(
    index: GameIndex, game_date: date, away: str, home: str
) -> tuple[str | None, str]:
    """Return ``(game_id, "resolved")`` or ``(None, "no_box_score_game")``.

    Both tricodes must belong to the matched game's two teams; an index
    whose key disagrees with its own teams raises.
    """
    game_id = index.game_by_key.get((game_date, away, home))
    if game_id is None:
        return None, "no_box_score_game"
    if not isinstance(game_id, str):
        raise AvailabilityResolveError(
            f"key {game_date.isoformat()} {away}@{home} matches two games: {game_id!r}"
        )
    try:
        away_id, home_id = index.teams[game_id]
    except KeyError as exc:
        raise AvailabilityResolveError(f"game {game_id}: missing from index.teams") from exc
    if (index.tricodes.get((game_id, away_id)), index.tricodes.get((game_id, home_id))) != (away, home):
        raise AvailabilityResolveError(
            f"game {game_id}: tricodes {away}@{home} do not belong to its teams"
        )
    return game_id, "resolved"


def resolve_team(index: GameIndex, game_id: str, team_raw: str) -> int | None:
    """Return the team whose ``City Name`` string equals ``team_raw`` exactly."""
    try:
        pair = index.teams[game_id]
    except KeyError as exc:
        raise AvailabilityResolveError(f"unknown game {game_id!r}") from exc
    matches = [
        team_id for team_id in pair if index.team_strings.get((game_id, team_id)) == team_raw
    ]
    if len(matches) > 1:
        raise AvailabilityResolveError(
            f"game {game_id}: both teams carry the string {team_raw!r}"
        )
    return matches[0] if matches else None


def _candidates(
    candidates: frozenset[int] | None, source: str
) -> tuple[int | None, str] | None:
    if not candidates:
        return None
    if len(candidates) == 1:
        return next(iter(candidates)), source
    return None, "unresolved_ambiguous"


def resolve_person(
    index: GameIndex,
    *,
    game_id: str,
    nba_team_id: int,
    season: int,
    player_name_raw: str,
) -> tuple[int | None, str]:
    """Game roster of the team, then its season roster; ambiguity never resolves."""
    if game_id not in index.teams or nba_team_id not in index.teams[game_id]:
        raise AvailabilityResolveError(
            f"unknown game/team pair {game_id!r}/{nba_team_id!r}"
        )
    name_key = listing_name_key(player_name_raw)
    outcome = _candidates(
        index.game_roster.get((game_id, nba_team_id), {}).get(name_key), "game_roster"
    )
    if outcome is None:
        outcome = _candidates(
            index.season_roster.get((season, nba_team_id), {}).get(name_key), "season_roster"
        )
    return outcome if outcome is not None else (None, "unresolved_no_match")


# --------------------------------------------------------------------------
# frame-level resolution
# --------------------------------------------------------------------------


def _validate_team_exceptions(team_exceptions: Mapping[str, dict]) -> None:
    if not isinstance(team_exceptions, Mapping):
        raise AvailabilityResolveError("team_exceptions must be a mapping")
    for key, entry in team_exceptions.items():
        if not isinstance(key, str) or key.count("|") != 1 or not key.split("|")[1].isdigit():
            raise AvailabilityResolveError(
                f"team_resolution key must be 'report_id|row_number': {key!r}"
            )
        if not isinstance(entry, Mapping) or not {"team_raw", "nba_team_id"} <= set(entry):
            raise AvailabilityResolveError(
                f"team_resolution entry {key!r} must carry team_raw and nba_team_id"
            )
        _as_str(entry["team_raw"], f"team_resolution entry {key!r} team_raw")
        _as_int(entry["nba_team_id"], f"team_resolution entry {key!r} nba_team_id")


def resolve_listings(
    listings: pd.DataFrame,
    index: GameIndex,
    team_exceptions: Mapping[str, dict],
) -> pd.DataFrame:
    """Return ``listings`` with the six resolution columns appended.

    Per row, exactly the outcomes of :func:`resolve_game`,
    :func:`resolve_team` and :func:`resolve_person` (distinct game keys,
    team strings and identities are resolved once and broadcast):

    * unresolved game: null ``game_id``/``nba_team_id``/``franchise_id``/
      ``person_id``, ``game_resolution`` ``no_box_score_game`` and
      ``person_resolution`` ``unresolved_game`` (``placeholder`` for a
      placeholder row);
    * placeholder rows of resolved games: team resolved, ``person_id`` null,
      ``person_resolution`` ``placeholder``;
    * a resolved game whose ``team_raw`` matches neither team string and has
      no ``team_exceptions`` entry (keyed ``report_id|row_number`` with
      ``team_raw`` and ``nba_team_id``): null team, franchise and person,
      ``person_resolution`` ``unresolved_no_match`` for a player row. These
      rows are the observed ``team_resolution`` ledger entries that
      :func:`team_resolution_observed` collects; the build gate, not this
      function, fails closed on them.

    An exception whose ``team_raw`` differs from the row's, whose team is not
    one of the game's two, that names a row of an unresolved game or that
    disagrees with an exact match raises ``AvailabilityResolveError``.
    """
    _require_columns(listings, LISTING_INPUT_COLUMNS, "listings")
    _validate_team_exceptions(team_exceptions)

    game_cache: dict[tuple, tuple[str | None, str]] = {}
    team_cache: dict[tuple[str, str], int | None] = {}
    person_cache: dict[tuple, tuple[int | None, str]] = {}

    game_ids: list[str | None] = []
    game_resolutions: list[str] = []
    team_ids: list[int | None] = []
    franchise_ids: list[str | None] = []
    person_ids: list[int | None] = []
    person_resolutions: list[str] = []

    columns = list(LISTING_INPUT_COLUMNS)
    for row in listings[columns].itertuples(index=False):
        report_id = _as_str(row.report_id, "listings.report_id")
        row_number = _as_int(row.row_number, f"{report_id}: row_number")
        label = f"{report_id}|{row_number}"
        game_key = (
            _as_date(row.game_date, f"{label}: game_date"),
            _as_str(row.away_tricode, f"{label}: away_tricode"),
            _as_str(row.home_tricode, f"{label}: home_tricode"),
        )
        if game_key not in game_cache:
            game_cache[game_key] = resolve_game(index, *game_key)
        game_id, game_resolution = game_cache[game_key]
        team_raw = _as_str(row.team_raw, f"{label}: team_raw")
        is_placeholder = bool(row.is_placeholder)
        exception = team_exceptions.get(label)

        team_id: int | None = None
        person_id: int | None = None
        if game_id is None:
            if exception is not None:
                raise AvailabilityResolveError(
                    f"team_resolution entry {label!r} names a listing whose game is unresolved"
                )
            person_resolution = "placeholder" if is_placeholder else "unresolved_game"
        else:
            team_key = (game_id, team_raw)
            if team_key not in team_cache:
                team_cache[team_key] = resolve_team(index, game_id, team_raw)
            team_id = team_cache[team_key]
            if exception is not None:
                if exception["team_raw"] != team_raw:
                    raise AvailabilityResolveError(
                        f"team_resolution entry {label!r} team_raw {exception['team_raw']!r} "
                        f"differs from the listing's {team_raw!r}"
                    )
                approved_id = int(exception["nba_team_id"])
                if approved_id not in index.teams[game_id]:
                    raise AvailabilityResolveError(
                        f"team_resolution entry {label!r} names team {approved_id}, "
                        f"not one of game {game_id}'s teams {index.teams[game_id]}"
                    )
                if team_id is not None and team_id != approved_id:
                    raise AvailabilityResolveError(
                        f"team_resolution entry {label!r} names team {approved_id} but "
                        f"{team_raw!r} matches team {team_id} exactly"
                    )
                team_id = approved_id
            if is_placeholder:
                person_resolution = "placeholder"
            elif team_id is None:
                person_resolution = "unresolved_no_match"
            else:
                season = _as_int(row.season, f"{label}: season")
                name = _as_str(row.player_name_raw, f"{label}: player_name_raw")
                person_key = (game_id, team_id, season, name)
                if person_key not in person_cache:
                    person_cache[person_key] = resolve_person(
                        index, game_id=game_id, nba_team_id=team_id,
                        season=season, player_name_raw=name,
                    )
                person_id, person_resolution = person_cache[person_key]

        game_ids.append(game_id)
        game_resolutions.append(game_resolution)
        team_ids.append(team_id)
        franchise_ids.append(
            None if team_id is None else index.franchise[(game_id, team_id)]
        )
        person_ids.append(person_id)
        person_resolutions.append(person_resolution)

    resolved = listings.copy()
    values = {
        "game_id": game_ids, "game_resolution": game_resolutions,
        "nba_team_id": team_ids, "franchise_id": franchise_ids,
        "person_id": person_ids, "person_resolution": person_resolutions,
    }
    for column in RESOLUTION_COLUMNS:
        resolved[column] = pd.Series(
            values[column], index=resolved.index, dtype=_RESOLUTION_DTYPES[column]
        )
    return resolved


def team_resolution_observed(listings: pd.DataFrame, index: GameIndex) -> dict[str, dict]:
    """Observed ``team_resolution`` ledger entries from a resolved frame.

    One entry per row of a resolved game whose ``team_raw`` equals neither of
    the game's team strings, keyed ``report_id|row_number`` with ``team_raw``
    and the ``nba_team_id`` the row carries (the approved exception's team,
    or ``None`` when no exception applied). Rows that matched exactly never
    appear, so the build can compare this set with the approved section in
    both directions and the audit can list the candidates.
    """
    _require_columns(
        listings, ("report_id", "row_number", "team_raw", "game_id", "game_resolution", "nba_team_id"),
        "listings",
    )
    observed: dict[str, dict] = {}
    frame = listings[listings["game_resolution"] == "resolved"]
    for row in frame[["report_id", "row_number", "team_raw", "game_id", "nba_team_id"]].itertuples(index=False):
        team_raw = _as_str(row.team_raw, "listings.team_raw")
        if resolve_team(index, str(row.game_id), team_raw) is not None:
            continue
        key = f"{row.report_id}|{int(row.row_number)}"
        observed[key] = {
            "team_raw": team_raw,
            "nba_team_id": None if _is_null(row.nba_team_id) else int(row.nba_team_id),
        }
    return dict(sorted(observed.items()))


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------


def _rate(resolved: int, total: int) -> float | None:
    return None if total == 0 else resolved / total


def _month(value: object, label: str) -> str:
    if _is_null(value):
        raise AvailabilityResolveError(f"{label}: report_slot_et is null")
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise AvailabilityResolveError(f"{label}: report_slot_et {value!r} is not a time") from exc
    return f"{stamp.year:04d}-{stamp.month:02d}"


def _rates_by(groups: Mapping[Hashable, tuple[set, set]]) -> dict:
    """``(total identities, unresolved identities)`` -> rate; an identity
    with any unresolved row is unresolved, matching the top-level rule."""
    return {
        key: _rate(len(total) - len(unresolved), len(total))
        for key, (total, unresolved) in sorted(groups.items())
    }


def _season_metrics(frame: pd.DataFrame) -> dict:
    resolved_games = frame[frame["game_resolution"] == "resolved"]
    population = resolved_games[~resolved_games["is_placeholder"].astype(bool)]

    rows = 0
    rows_resolved = 0
    identity_rows: Counter = Counter()
    identity_resolved: dict[tuple, bool] = {}
    by_team: dict[int, tuple[set, set]] = defaultdict(lambda: (set(), set()))
    by_month: dict[str, tuple[set, set]] = defaultdict(lambda: (set(), set()))
    by_status: dict[str, tuple[set, set]] = defaultdict(lambda: (set(), set()))
    unresolved_rows: Counter = Counter()
    unresolved_identities: dict[str, set] = defaultdict(set)
    lnr_rows = 0
    lnr_identities: set = set()
    lnr_by_team: dict[int, tuple[list, set]] = defaultdict(lambda: ([0], set()))
    lnr_by_status: dict[str, tuple[list, set]] = defaultdict(lambda: ([0], set()))
    not_out_rows: list[tuple] = []

    columns = [
        "report_id", "row_number", "report_slot_et", "game_id", "nba_team_id",
        "person_id", "person_resolution", "player_name_raw", "current_status_raw",
    ]
    for row in population[columns].itertuples(index=False):
        label = f"{row.report_id}|{row.row_number}"
        game_id = str(row.game_id)
        team_id = None if _is_null(row.nba_team_id) else int(row.nba_team_id)
        name_raw = _as_str(row.player_name_raw, f"{label}: player_name_raw")
        identity = (game_id, team_id, listing_name_key(name_raw))
        resolved = not _is_null(row.person_id)
        month = _month(row.report_slot_et, label)
        status = None if _is_null(row.current_status_raw) else str(row.current_status_raw)

        rows += 1
        rows_resolved += resolved
        identity_rows[identity] += 1
        identity_resolved[identity] = identity_resolved.get(identity, True) and resolved
        for groups, key in ((by_team, team_id), (by_month, month), (by_status, status)):
            if key is None:
                continue
            total, unresolved = groups[key]
            total.add(identity)
            if not resolved:
                unresolved.add(identity)
        if not resolved:
            unresolved_rows[name_raw] += 1
            unresolved_identities[name_raw].add(identity)
        if row.person_resolution == "season_roster":
            lnr_rows += 1
            lnr_identities.add(identity)
            for groups, key in ((lnr_by_team, team_id), (lnr_by_status, status)):
                if key is None:
                    continue
                count, members = groups[key]
                count[0] += 1
                members.add(identity)
            if status != "Out":
                not_out_rows.append(
                    (str(row.report_id), int(row.row_number), game_id, name_raw, status)
                )

    identities = len(identity_rows)
    identities_resolved = sum(1 for done in identity_resolved.values() if done)

    unresolved_frame = frame[frame["game_resolution"] == "no_box_score_game"]
    matchups = Counter(
        f"{away}@{home}"
        for away, home in zip(unresolved_frame["away_tricode"], unresolved_frame["home_tricode"])
    )

    return {
        "rows": rows,
        "rows_resolved": rows_resolved,
        "row_rate": _rate(rows_resolved, rows),
        "identities": identities,
        "identities_resolved": identities_resolved,
        "identity_rate": _rate(identities_resolved, identities),
        "identity_rate_by_team": _rates_by(by_team),
        "identity_rate_by_month": _rates_by(by_month),
        "identity_rate_by_status": _rates_by(by_status),
        "unresolved_names": {
            name: {"rows": count, "identities": len(unresolved_identities[name])}
            for name, count in sorted(unresolved_rows.items())
        },
        "listed_not_rostered": {
            "rows": lnr_rows,
            "identities": len(lnr_identities),
            "by_team": {
                key: {"rows": count[0], "identities": len(members)}
                for key, (count, members) in sorted(lnr_by_team.items())
            },
            "by_status": {
                key: {"rows": count[0], "identities": len(members)}
                for key, (count, members) in sorted(lnr_by_status.items())
            },
            "not_out_rows": sorted(not_out_rows, key=lambda entry: (entry[0], entry[1])),
        },
        "unresolved_matchups": dict(sorted(matchups.items())),
    }


def resolution_metrics(listings: pd.DataFrame) -> dict:
    """Per-season resolution metrics of a resolved listing frame.

    The row population is the non-placeholder rows of resolved games; an
    identity is ``(game_id, nba_team_id, normalised name)`` and counts as
    resolved only when every one of its rows carries a ``person_id``. Rates
    are ``None`` when the denominator is zero. ``identity_rate_by_team`` is
    keyed by ``nba_team_id`` (identities whose team is unmatched carry no
    team and are left out of that one breakdown), ``identity_rate_by_month``
    by the ``report_slot_et`` calendar month and ``identity_rate_by_status``
    by ``current_status_raw``. ``listed_not_rostered`` covers
    ``season_roster`` rows; ``unresolved_matchups`` counts every row,
    placeholders included, of unresolved games per ``AWAY@HOME`` pair. Keys
    are sorted at every level.
    """
    _require_columns(listings, METRIC_INPUT_COLUMNS, "listings")
    metrics: dict[int, dict] = {}
    for season, frame in listings.groupby("season", sort=True):
        metrics[_as_int(season, "listings.season")] = _season_metrics(frame)
    return metrics
