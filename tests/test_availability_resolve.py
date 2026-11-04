"""Tests for injury-listing resolution against box-score rosters (Task 12).

The index is built from frames shaped exactly like ``box_rosters``'
``NormalizedGame`` outputs (``GAME_ROSTERS_COLUMNS`` rows and schedule
dicts) for three hand-authored games: two on the same date (the committed
fixture Finals game ``0042400401`` and a DAL@DEN game) and a later OKC game
in the same season, so the season roster spans two games. Listing frames
are shaped like ``injury_report_listings`` before the resolution columns.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest

from nba_data import availability_resolve as resolve
from nba_data.box_rosters import GAME_ROSTERS_COLUMNS


IND = 1610612754
OKC = 1610612760
DAL = 1610612742
DEN = 1610612743
LAC = 1610612746

FINALS = "0042400401"      # IND @ OKC, 2025-06-05 (committed fixture game)
DAL_DEN = "0042400999"     # DAL @ DEN, 2025-06-05 (same date, hand-authored)
LATER = "0042400402"       # IND @ OKC, 2025-06-08 (season roster spans two games)

TEAM_STRINGS = {
    (FINALS, IND): "Indiana Pacers", (FINALS, OKC): "Oklahoma City Thunder",
    (DAL_DEN, DAL): "Dallas Mavericks", (DAL_DEN, DEN): "Denver Nuggets",
    (LATER, IND): "Indiana Pacers", (LATER, OKC): "Oklahoma City Thunder",
}
TRICODES = {
    (FINALS, IND): "IND", (FINALS, OKC): "OKC",
    (DAL_DEN, DAL): "DAL", (DAL_DEN, DEN): "DEN",
    (LATER, IND): "IND", (LATER, OKC): "OKC",
}
FRANCHISES = {IND: "IND", OKC: "OKC", DAL: "DAL", DEN: "DEN"}
HOME = {FINALS: OKC, DAL_DEN: DEN, LATER: OKC}


def schedule_row(game_id: str, game_date: date, code: str, *, season=2025) -> dict:
    return {
        "game_id": game_id,
        "season": season,
        "game_type": "playoff",
        "game_date": game_date,
        "scheduled_start_utc": datetime(2025, 6, 6, 0, 30),
        "scheduled_start_et_raw": f"{game_date.isoformat()}T20:30:00Z",
        "game_code_raw": code,
    }


def roster_row(
    game_id: str, person_id: int, team: int, family: str, first: str,
    *, status: str = "played", season: int = 2025, game_date: date = date(2025, 6, 5),
) -> dict:
    return {
        "game_id": game_id, "person_id": person_id, "season": season,
        "game_type": "playoff", "game_date": game_date, "nba_team_id": team,
        "team_tricode_raw": TRICODES[(game_id, team)],
        "franchise_id": FRANCHISES[team], "is_home": HOME[game_id] == team,
        "roster_status": status, "first_name_raw": first, "family_name_raw": family,
        "jersey_num": None, "starter_position_raw": None, "comment_raw": None,
        "comment_code": None, "minutes_raw": None,
    }


SCHEDULE_ROWS = [
    schedule_row(FINALS, date(2025, 6, 5), "20250605/INDOKC"),
    schedule_row(DAL_DEN, date(2025, 6, 5), "20250605/DALDEN"),
    schedule_row(LATER, date(2025, 6, 8), "20250608/INDOKC"),
]
ROSTER_ROWS = [
    # Finals game: the fixture identities, with Jalen Williams on both teams.
    roster_row(FINALS, 1630169, IND, "Haliburton", "Tyrese"),
    roster_row(FINALS, 1626167, IND, "Turner", "Myles"),
    roster_row(FINALS, 1642500, IND, "Williams", "Jalen", status="inactive"),
    roster_row(FINALS, 1628983, OKC, "Gilgeous-Alexander", "Shai"),
    roster_row(FINALS, 1631114, OKC, "Williams", "Jalen"),
    roster_row(FINALS, 1642261, OKC, "Topić", "Nikola", status="inactive"),
    # Same-date second game: suffix and diacritic identities.
    roster_row(DAL_DEN, 1629029, DAL, "Doncic", "Luka"),
    roster_row(DAL_DEN, 1627827, DAL, "Finney-Smith", "Dorian"),
    roster_row(DAL_DEN, 1629008, DEN, "Porter Jr.", "Michael"),
    roster_row(DAL_DEN, 1627750, DEN, "O'Neale", "Royce", status="inactive"),
    # Later OKC game: Dort appears only here (season roster), and two
    # distinct Jaylin Williamses share one normalised name on one team.
    roster_row(LATER, 1629652, OKC, "Dort", "Luguentz", game_date=date(2025, 6, 8)),
    roster_row(LATER, 1631114, OKC, "Williams", "Jalen", game_date=date(2025, 6, 8)),
    roster_row(LATER, 1641800, OKC, "Williams", "Jaylin", game_date=date(2025, 6, 8)),
    roster_row(LATER, 1641801, OKC, "Williams", "Jaylin", game_date=date(2025, 6, 8)),
    roster_row(LATER, 1630169, IND, "Haliburton", "Tyrese", game_date=date(2025, 6, 8)),
]


def rosters_frame(rows=ROSTER_ROWS) -> pd.DataFrame:
    frame = pd.DataFrame.from_records(rows, columns=list(GAME_ROSTERS_COLUMNS))
    return frame.astype({
        "game_id": "string", "person_id": "Int64", "season": "Int64",
        "nba_team_id": "Int64", "team_tricode_raw": "string",
        "franchise_id": "string", "is_home": "boolean", "roster_status": "string",
        "first_name_raw": "string", "family_name_raw": "string",
    })


def schedule_frame(rows=SCHEDULE_ROWS) -> pd.DataFrame:
    return pd.DataFrame.from_records(rows)


@pytest.fixture
def index() -> resolve.GameIndex:
    return resolve.build_game_index(schedule_frame(), rosters_frame(), TEAM_STRINGS, TRICODES)


def listing(
    row_number: int,
    *,
    report_id: str = "Injury-Report_2025-06-05_05PM",
    season: int = 2025,
    game_date: date = date(2025, 6, 5),
    matchup: str = "IND@OKC",
    team_raw: str = "Oklahoma City Thunder",
    player: str | None = "Williams, Jalen",
    status: str | None = "Out",
    placeholder: str | None = None,
    slot_et: datetime = datetime(2025, 6, 5, 17, 0),
) -> dict:
    away, home = matchup.split("@")
    return {
        "report_id": report_id, "season": season, "row_number": row_number,
        "page": 1, "game_date": game_date, "game_time_raw": "08:30 (ET)",
        "matchup_raw": matchup, "away_tricode": away, "home_tricode": home,
        "team_raw": team_raw, "is_placeholder": placeholder is not None,
        "placeholder_raw": placeholder,
        "player_name_raw": None if placeholder else player,
        "current_status_raw": None if placeholder else status,
        "reason_raw": None if placeholder else "Injury/Illness - Right Ankle; Sprain",
        "category_raw": None, "previous_status_raw": None,
        "report_slot_et": slot_et,
    }


def listings_frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame.from_records(rows)


# --------------------------------------------------------------------------
# version season and names
# --------------------------------------------------------------------------


@pytest.mark.parametrize("slot_date, season", [
    (date(2018, 12, 17), 2019), (date(2019, 9, 30), 2019), (date(2019, 10, 1), 2020),
    (date(2020, 10, 11), 2020), (date(2020, 11, 30), 2020), (date(2020, 12, 1), 2021),
    (date(2021, 9, 30), 2021), (date(2021, 10, 1), 2022), (date(2026, 9, 30), 2026),
])
def test_version_season_boundaries(slot_date, season):
    """Would fail if the bubble season or an October boundary drifted."""
    assert resolve.version_season(slot_date) == season


def test_version_season_rejects_dates_before_the_first_slot():
    with pytest.raises(resolve.AvailabilityResolveError, match="before"):
        resolve.version_season(date(2018, 12, 16))


def test_normalize_name_and_name_keys():
    """Would fail if diacritics, punctuation or name order were kept."""
    assert resolve.normalize_name("Dončić") == "doncic"
    assert resolve.normalize_name("Finney-Smith") == "finneysmith"
    assert resolve.normalize_name("O'Neale") == "oneale"
    assert resolve.normalize_name("O’Neale") == "oneale"
    assert resolve.normalize_name("  Porter Jr. ") == "porterjr"
    assert resolve.normalize_name("MCCOLLUM") == "mccollum"

    assert resolve.listing_name_key("Porter Jr., Michael") == "porterjr|michael"
    assert resolve.roster_name_key("Porter Jr.", "Michael") == "porterjr|michael"
    assert resolve.listing_name_key("Dončić, Luka") == resolve.roster_name_key("Doncic", "Luka")
    assert resolve.listing_name_key("Williams, C.J.") == "williams|cj"
    # Only the first ", " splits; a name without one is all family name.
    assert resolve.listing_name_key("A, B, C") == "a|b,c"
    assert resolve.listing_name_key("Nene") == "nene|"
    with pytest.raises(resolve.AvailabilityResolveError):
        resolve.normalize_name(None)


# --------------------------------------------------------------------------
# game index and the three resolvers
# --------------------------------------------------------------------------


def test_build_game_index_cross_checks_game_code_against_date_and_tricodes():
    """Would fail if the index trusted the game code without cross-checks."""
    index = resolve.build_game_index(schedule_frame(), rosters_frame(), TEAM_STRINGS, TRICODES)
    assert index.game_by_key == {
        (date(2025, 6, 5), "IND", "OKC"): FINALS,
        (date(2025, 6, 5), "DAL", "DEN"): DAL_DEN,
        (date(2025, 6, 8), "IND", "OKC"): LATER,
    }
    assert index.teams == {FINALS: (IND, OKC), DAL_DEN: (DAL, DEN), LATER: (IND, OKC)}
    assert index.team_strings == TEAM_STRINGS
    assert index.tricodes == TRICODES
    assert index.franchise[(FINALS, IND)] == "IND"
    assert index.game_roster[(FINALS, IND)]["williams|jalen"] == frozenset({1642500})
    assert index.game_roster[(FINALS, OKC)]["williams|jalen"] == frozenset({1631114})
    assert index.season_roster[(2025, OKC)]["williams|jaylin"] == frozenset({1641800, 1641801})
    assert index.season_roster[(2025, OKC)]["dort|luguentz"] == frozenset({1629652})
    assert (2025, LAC) not in index.season_roster

    # The game code's date must agree with game_date ...
    bad_date = schedule_frame([
        schedule_row(FINALS, date(2025, 6, 4), "20250605/INDOKC"),
        *SCHEDULE_ROWS[1:],
    ])
    with pytest.raises(resolve.AvailabilityResolveError, match="game_date"):
        resolve.build_game_index(bad_date, rosters_frame(), TEAM_STRINGS, TRICODES)
    # ... unless the ledger's schedule_dates section accepts that exact
    # disagreement; the game is then keyed by the player_games game_date.
    accepted = {FINALS: {
        "game_et_raw": "2025-06-04T20:30:00Z", "game_code_raw": "20250605/INDOKC",
        "game_date": "2025-06-04", "note": "code carries the original date",
    }}
    with_exception = resolve.build_game_index(
        bad_date, rosters_frame(), TEAM_STRINGS, TRICODES, schedule_exceptions=accepted,
    )
    assert with_exception.game_by_key[(date(2025, 6, 4), "IND", "OKC")] == FINALS
    assert (date(2025, 6, 5), "IND", "OKC") not in with_exception.game_by_key
    assert resolve.resolve_game(with_exception, date(2025, 6, 4), "IND", "OKC") == (FINALS, "resolved")
    for drifted in (
        {**accepted[FINALS], "game_code_raw": "20250604/INDOKC"},
        {**accepted[FINALS], "game_date": "2025-06-05"},
        {"game_et_raw": "2025-06-04T20:30:00Z", "game_date": "2025-06-04"},
    ):
        with pytest.raises(resolve.AvailabilityResolveError, match="schedule_dates"):
            resolve.build_game_index(
                bad_date, rosters_frame(), TEAM_STRINGS, TRICODES, schedule_exceptions={FINALS: drifted},
            )
    # An accepted entry never relaxes the tricode cross-check.
    with pytest.raises(resolve.AvailabilityResolveError, match="tricode"):
        resolve.build_game_index(
            schedule_frame([schedule_row(FINALS, date(2025, 6, 4), "20250605/OKCIND"), *SCHEDULE_ROWS[1:]]),
            rosters_frame(), TEAM_STRINGS, TRICODES,
            schedule_exceptions={FINALS: {**accepted[FINALS], "game_code_raw": "20250605/OKCIND"}},
        )
    # ... and its tricodes with the roster's away and home tricodes.
    bad_code = schedule_frame([
        schedule_row(FINALS, date(2025, 6, 5), "20250605/OKCIND"),
        *SCHEDULE_ROWS[1:],
    ])
    with pytest.raises(resolve.AvailabilityResolveError, match="tricode"):
        resolve.build_game_index(bad_code, rosters_frame(), TEAM_STRINGS, TRICODES)
    # A malformed code and a roster game without a schedule row fail too.
    malformed = schedule_frame([
        schedule_row(FINALS, date(2025, 6, 5), "2025-06-05/INDOKC"), *SCHEDULE_ROWS[1:],
    ])
    with pytest.raises(resolve.AvailabilityResolveError, match="game_code_raw"):
        resolve.build_game_index(malformed, rosters_frame(), TEAM_STRINGS, TRICODES)
    with pytest.raises(resolve.AvailabilityResolveError, match="schedule"):
        resolve.build_game_index(schedule_frame(SCHEDULE_ROWS[:2]), rosters_frame(), TEAM_STRINGS, TRICODES)
    with pytest.raises(resolve.AvailabilityResolveError, match="team_strings"):
        resolve.build_game_index(
            schedule_frame(), rosters_frame(), {k: v for k, v in TEAM_STRINGS.items() if k[0] != LATER}, TRICODES,
        )


def test_resolve_game_requires_a_unique_key_and_tricode_membership(index):
    """Would fail if a swapped matchup matched or a duplicate key picked one game."""
    assert resolve.resolve_game(index, date(2025, 6, 5), "IND", "OKC") == (FINALS, "resolved")
    assert resolve.resolve_game(index, date(2025, 6, 5), "DAL", "DEN") == (DAL_DEN, "resolved")
    assert resolve.resolve_game(index, date(2025, 6, 8), "IND", "OKC") == (LATER, "resolved")
    # Swapped tricodes, the wrong date and a league alias all miss.
    assert resolve.resolve_game(index, date(2025, 6, 5), "OKC", "IND") == (None, "no_box_score_game")
    assert resolve.resolve_game(index, date(2025, 6, 6), "IND", "OKC") == (None, "no_box_score_game")
    assert resolve.resolve_game(index, date(2025, 6, 5), "IND", "OKL") == (None, "no_box_score_game")

    # Two schedule rows that share a key cannot build an index.
    twin = "0042400998"
    duplicate = schedule_frame([*SCHEDULE_ROWS, schedule_row(twin, date(2025, 6, 5), "20250605/INDOKC")])
    rows = [*ROSTER_ROWS, *({**row, "game_id": twin} for row in ROSTER_ROWS if row["game_id"] == FINALS)]
    strings = {**TEAM_STRINGS, (twin, IND): "Indiana Pacers", (twin, OKC): "Oklahoma City Thunder"}
    codes = {**TRICODES, (twin, IND): "IND", (twin, OKC): "OKC"}
    with pytest.raises(resolve.AvailabilityResolveError, match="two games"):
        resolve.build_game_index(duplicate, rosters_frame(rows), strings, codes)

    # An index whose key disagrees with the matched game's tricodes raises
    # rather than resolving: both tricodes must belong to the matched game.
    broken = resolve.GameIndex(
        game_by_key={(date(2025, 6, 5), "PHX", "OKC"): FINALS},
        teams=index.teams, team_strings=index.team_strings, tricodes=index.tricodes,
        game_roster=index.game_roster, season_roster=index.season_roster, franchise=index.franchise,
    )
    with pytest.raises(resolve.AvailabilityResolveError, match="belong"):
        resolve.resolve_game(broken, date(2025, 6, 5), "PHX", "OKC")


def test_resolve_team_matches_city_plus_name_exactly(index):
    """Would fail if team matching were fuzzy or ignored the game."""
    clippers_index = resolve.build_game_index(
        schedule_frame([schedule_row("0022400001", date(2025, 6, 5), "20250605/LACDEN")]),
        rosters_frame([
            {**roster_row(DAL_DEN, 1627750, DEN, "Porter Jr.", "Michael"),
             "game_id": "0022400001", "is_home": True},
            {**roster_row(DAL_DEN, 1626157, DAL, "Zubac", "Ivica"),
             "game_id": "0022400001", "nba_team_id": LAC, "team_tricode_raw": "LAC",
             "franchise_id": "LAC", "is_home": False},
        ]),
        {("0022400001", LAC): "LA Clippers", ("0022400001", DEN): "Denver Nuggets"},
        {("0022400001", LAC): "LAC", ("0022400001", DEN): "DEN"},
    )
    assert resolve.resolve_team(clippers_index, "0022400001", "LA Clippers") == LAC
    assert resolve.resolve_team(clippers_index, "0022400001", "Denver Nuggets") == DEN
    assert resolve.resolve_team(clippers_index, "0022400001", "Los Angeles Clippers") is None
    assert resolve.resolve_team(clippers_index, "0022400001", "la clippers") is None
    assert resolve.resolve_team(clippers_index, "0022400001", "LA Clippers ") is None
    # The string must belong to this game, not merely to the league.
    assert resolve.resolve_team(index, FINALS, "Denver Nuggets") is None
    assert resolve.resolve_team(index, FINALS, "Indiana Pacers") == IND
    with pytest.raises(resolve.AvailabilityResolveError, match="unknown game"):
        resolve.resolve_team(index, "0000000000", "Indiana Pacers")


def test_resolve_person_prefers_game_roster_then_season_roster_then_unresolved(index):
    """Would fail if a shared name silently resolved to the first candidate."""
    def person(game_id, team, name, season=2025):
        return resolve.resolve_person(
            index, game_id=game_id, nba_team_id=team, season=season, player_name_raw=name,
        )

    # Game roster first, team-scoped: the two Jalen Williamses of 0042400401.
    assert person(FINALS, OKC, "Williams, Jalen") == (1631114, "game_roster")
    assert person(FINALS, IND, "Williams, Jalen") == (1642500, "game_roster")
    # Suffixes, diacritics, hyphens and apostrophes resolve (Review Focus 3).
    assert person(DAL_DEN, DEN, "Porter Jr., Michael") == (1629008, "game_roster")
    assert person(DAL_DEN, DAL, "Dončić, Luka") == (1629029, "game_roster")
    assert person(DAL_DEN, DAL, "Finney-Smith, Dorian") == (1627827, "game_roster")
    assert person(DAL_DEN, DEN, "O’Neale, Royce") == (1627750, "game_roster")
    assert person(FINALS, OKC, "Topic, Nikola") == (1642261, "game_roster")
    # Absent from the game roster but on the season roster of that team.
    assert person(FINALS, OKC, "Dort, Luguentz") == (1629652, "season_roster")
    # The season roster is team-scoped and season-scoped.
    assert person(FINALS, IND, "Dort, Luguentz") == (None, "unresolved_no_match")
    assert person(FINALS, OKC, "Dort, Luguentz", season=2024) == (None, "unresolved_no_match")
    assert person(FINALS, OKC, "Doncic, Luka") == (None, "unresolved_no_match")
    # Two roster players sharing a normalised name on one team are ambiguous,
    # never a silent first match: in the game roster ...
    assert person(LATER, OKC, "Williams, Jaylin") == (None, "unresolved_ambiguous")
    # ... and through the season roster.
    assert person(FINALS, OKC, "Williams, Jaylin") == (None, "unresolved_ambiguous")
    with pytest.raises(resolve.AvailabilityResolveError, match="unknown"):
        person(FINALS, DEN, "Williams, Jalen")


# --------------------------------------------------------------------------
# resolve_listings
# --------------------------------------------------------------------------


RESOLUTION_COLUMNS = (
    "game_id", "game_resolution", "nba_team_id", "franchise_id", "person_id", "person_resolution",
)


def test_resolve_listings_adds_resolution_columns_and_applies_team_exceptions(index):
    """Would fail if any resolver outcome, null rule or exception drifted."""
    rows = [
        listing(1),                                                        # game roster
        listing(2, team_raw="Indiana Pacers", player="Williams, Jalen"),    # other team's Jalen
        listing(3, player="Dort, Luguentz", status="Questionable"),        # season roster
        listing(4, player="Williams, Jaylin"),                             # ambiguous
        listing(5, player="Nobody, Some"),                                 # no match
        listing(6, placeholder="NOT YET SUBMITTED", team_raw="Indiana Pacers"),
        listing(7, matchup="PHO@LAL", team_raw="Phoenix Suns", player="Booker, Devin"),
        listing(8, matchup="PHO@LAL", team_raw="Los Angeles Lakers", placeholder="NOT YET SUBMITTED"),
        listing(9, matchup="DAL@DEN", team_raw="Dallas Mavs", player="Dončić, Luka"),   # unmatched team
        listing(10, matchup="DAL@DEN", team_raw="Denver Nuggets", player="Porter Jr., Michael"),
    ]
    frame = listings_frame(rows)
    before = frame.copy()
    exceptions = {
        "Injury-Report_2025-06-05_05PM|9": {
            "team_raw": "Dallas Mavs", "nba_team_id": DAL, "note": "typo in the printed team",
        },
    }

    resolved = resolve.resolve_listings(frame, index, {})
    pd.testing.assert_frame_equal(frame, before)                 # input untouched
    assert list(resolved.columns) == [*frame.columns, *RESOLUTION_COLUMNS]
    assert len(resolved) == len(frame)
    assert list(resolved["row_number"]) == list(range(1, 11))

    def col(name):
        return [None if pd.isna(v) else v for v in resolved[name]]

    assert col("game_id") == [FINALS] * 6 + [None, None, DAL_DEN, DAL_DEN]
    assert col("game_resolution") == ["resolved"] * 6 + ["no_box_score_game"] * 2 + ["resolved"] * 2
    assert col("nba_team_id") == [OKC, IND, OKC, OKC, OKC, IND, None, None, None, DEN]
    assert col("franchise_id") == ["OKC", "IND", "OKC", "OKC", "OKC", "IND", None, None, None, "DEN"]
    assert col("person_id") == [1631114, 1642500, 1629652, None, None, None, None, None, None, 1629008]
    assert col("person_resolution") == [
        "game_roster", "game_roster", "season_roster", "unresolved_ambiguous",
        "unresolved_no_match", "placeholder", "unresolved_game", "placeholder",
        "unresolved_no_match", "game_roster",
    ]
    assert str(resolved["nba_team_id"].dtype) == "Int64"
    assert str(resolved["person_id"].dtype) == "Int64"
    assert str(resolved["game_resolution"].dtype) == "string"

    # Without an exception the unmatched team string is exposed for the
    # ledger; with the exception the row resolves through it.
    assert resolve.team_resolution_observed(resolved, index) == {
        "Injury-Report_2025-06-05_05PM|9": {"team_raw": "Dallas Mavs", "nba_team_id": None},
    }
    with_exception = resolve.resolve_listings(frame, index, exceptions)
    row = with_exception.iloc[8]
    assert (row["nba_team_id"], row["franchise_id"], row["person_id"], row["person_resolution"]) == (
        DAL, "DAL", 1629029, "game_roster",
    )
    assert resolve.team_resolution_observed(with_exception, index) == {
        "Injury-Report_2025-06-05_05PM|9": {"team_raw": "Dallas Mavs", "nba_team_id": DAL},
    }

    # Exceptions are exact: a drifted team_raw, a team outside the game, an
    # unresolved game and a disagreement with an exact match all fail closed.
    for bad in (
        {"Injury-Report_2025-06-05_05PM|9": {"team_raw": "Dallas Mavericks", "nba_team_id": DAL}},
        {"Injury-Report_2025-06-05_05PM|9": {"team_raw": "Dallas Mavs", "nba_team_id": OKC}},
        {"Injury-Report_2025-06-05_05PM|7": {"team_raw": "Phoenix Suns", "nba_team_id": OKC}},
        {"Injury-Report_2025-06-05_05PM|10": {"team_raw": "Denver Nuggets", "nba_team_id": DAL}},
        {"Injury-Report_2025-06-05_05PM|9": {"team_raw": "Dallas Mavs", "nba_team_id": "DAL"}},
        {"Injury-Report_2025-06-05_05PM|9": {"nba_team_id": DAL}},
    ):
        with pytest.raises(resolve.AvailabilityResolveError):
            resolve.resolve_listings(frame, index, bad)
    # A redundant exception that agrees with the exact match is harmless.
    agreeing = {"Injury-Report_2025-06-05_05PM|10": {"team_raw": "Denver Nuggets", "nba_team_id": DEN}}
    assert resolve.resolve_listings(frame, index, agreeing).iloc[9]["nba_team_id"] == DEN

    with pytest.raises(resolve.AvailabilityResolveError, match="column"):
        resolve.resolve_listings(frame.drop(columns=["team_raw"]), index, {})


# --------------------------------------------------------------------------
# resolution_metrics
# --------------------------------------------------------------------------


def _resolved(index, rows):
    return resolve.resolve_listings(listings_frame(rows), index, {})


def test_resolution_metrics_measures_rows_and_identities_separately(index):
    """Would fail if repeated unresolved rows hid behind the row rate."""
    rows = []
    # One identity (OKC, "Nobody, Some") repeated across 10 hourly versions.
    for hour in range(10):
        rows.append(listing(
            1, report_id=f"Injury-Report_2025-06-05_{hour:02d}AM", player="Nobody, Some",
            status="Questionable", slot_et=datetime(2025, 6, 5, hour),
        ))
    # Ten distinct resolved identities across two teams, two months, two statuses.
    resolved_names = [
        ("Oklahoma City Thunder", "Williams, Jalen", "Out", "IND@OKC", date(2025, 6, 5)),
        ("Oklahoma City Thunder", "Gilgeous-Alexander, Shai", "Out", "IND@OKC", date(2025, 6, 5)),
        ("Oklahoma City Thunder", "Topić, Nikola", "Out", "IND@OKC", date(2025, 6, 5)),
        ("Oklahoma City Thunder", "Dort, Luguentz", "Questionable", "IND@OKC", date(2025, 6, 5)),
        ("Indiana Pacers", "Haliburton, Tyrese", "Questionable", "IND@OKC", date(2025, 6, 5)),
        ("Indiana Pacers", "Turner, Myles", "Out", "IND@OKC", date(2025, 6, 5)),
        ("Indiana Pacers", "Williams, Jalen", "Out", "IND@OKC", date(2025, 6, 5)),
        ("Dallas Mavericks", "Dončić, Luka", "Out", "DAL@DEN", date(2025, 6, 5)),
        ("Denver Nuggets", "Porter Jr., Michael", "Out", "DAL@DEN", date(2025, 6, 5)),
        ("Oklahoma City Thunder", "Dort, Luguentz", "Out", "IND@OKC", date(2025, 6, 8)),
    ]
    for number, (team, name, status, matchup, game_date) in enumerate(resolved_names, start=2):
        slot = datetime(2025, 7, 1, 17) if number == 11 else datetime(2025, 6, 5, 17)
        rows.append(listing(
            number, team_raw=team, player=name, status=status, matchup=matchup,
            game_date=game_date, slot_et=slot,
        ))
    # Placeholder rows are outside the population; an unresolved game too.
    rows.append(listing(12, placeholder="ALL PLAYERS AVAILABLE"))
    rows.append(listing(13, matchup="PHO@LAL", team_raw="Phoenix Suns", player="Booker, Devin"))

    metrics = resolve.resolution_metrics(_resolved(index, rows))
    assert list(metrics) == [2025]
    season = metrics[2025]
    assert list(season) == [
        "rows", "rows_resolved", "row_rate", "identities", "identities_resolved", "identity_rate",
        "identity_rate_by_team", "identity_rate_by_month", "identity_rate_by_status",
        "unresolved_names", "listed_not_rostered", "unresolved_matchups",
    ]
    assert (season["rows"], season["rows_resolved"], season["row_rate"]) == (20, 10, 0.5)
    assert (season["identities"], season["identities_resolved"]) == (11, 10)
    assert season["identity_rate"] == pytest.approx(10 / 11)
    assert season["identity_rate_by_team"] == {
        IND: 1.0, OKC: pytest.approx(5 / 6), DAL: 1.0, DEN: 1.0,
    }
    assert season["identity_rate_by_month"] == {
        "2025-06": pytest.approx(9 / 10), "2025-07": 1.0,
    }
    assert season["identity_rate_by_status"] == {
        "Out": 1.0, "Questionable": pytest.approx(2 / 3),
    }
    assert season["unresolved_names"] == {"Nobody, Some": {"rows": 10, "identities": 1}}
    assert type(season["rows"]) is int and type(season["identities_resolved"]) is int
    assert all(type(key) is int for key in season["identity_rate_by_team"])


def test_resolution_metrics_breakdowns_are_order_independent_and_skip_null_status(index):
    """Would fail if a later resolved row re-resolved an identity in a breakdown."""
    base = dict(player="Williams, Jaylin", status="Questionable")      # ambiguous -> unresolved
    mixed = [listing(1, **base), listing(2, player="Williams, Jalen", status="Questionable")]
    frame = _resolved(index, mixed)
    # Force the same identity to look resolved on one row and unresolved on another.
    frame.loc[frame.index[0], "player_name_raw"] = "Williams, Jalen"
    forward = resolve.resolution_metrics(frame)[2025]
    backward = resolve.resolution_metrics(frame.iloc[::-1])[2025]
    assert forward["identities"] == backward["identities"] == 1
    assert forward["identities_resolved"] == backward["identities_resolved"] == 0
    assert forward["identity_rate_by_team"] == backward["identity_rate_by_team"] == {OKC: 0.0}
    assert forward["identity_rate_by_status"] == backward["identity_rate_by_status"] == {"Questionable": 0.0}
    assert forward["identity_rate_by_month"] == backward["identity_rate_by_month"] == {"2025-06": 0.0}

    # A null status on a season_roster row neither crashes nor keys a breakdown.
    rows = [listing(1, player="Dort, Luguentz", status="Out"), listing(2, player="Dort, Luguentz", status=None)]
    season = resolve.resolution_metrics(_resolved(index, rows))[2025]
    assert season["identity_rate_by_status"] == {"Out": 1.0}
    assert season["listed_not_rostered"]["by_status"] == {"Out": {"rows": 1, "identities": 1}}
    assert season["listed_not_rostered"]["rows"] == 2
    assert season["listed_not_rostered"]["not_out_rows"] == [
        ("Injury-Report_2025-06-05_05PM", 2, FINALS, "Dort, Luguentz", None),
    ]


def test_resolution_metrics_enumerates_listed_not_rostered_and_unresolved_matchups(index):
    """Would fail if a systematic alias or a non-Out season_roster row hid."""
    rows = [
        listing(1, player="Dort, Luguentz", status="Out"),
        listing(2, player="Dort, Luguentz", status="Questionable",
                report_id="Injury-Report_2025-06-05_06PM", slot_et=datetime(2025, 6, 5, 18)),
        listing(3, player="Williams, Jalen", status="Out"),
        listing(4, team_raw="Indiana Pacers", player="Haliburton, Tyrese", status="Available",
                game_date=date(2025, 6, 8), slot_et=datetime(2025, 6, 8, 17),
                report_id="Injury-Report_2025-06-08_05PM"),
    ]
    rows[3]["player_name_raw"] = "Turner, Myles"      # not on the later game's IND roster
    for number in range(5, 12):
        rows.append(listing(number, matchup="PHO@LAL", team_raw="Phoenix Suns", player="Booker, Devin"))
    rows.append(listing(12, matchup="UTH@SAC", team_raw="Utah Jazz", placeholder="NOT YET SUBMITTED"))
    rows.append(listing(13, matchup="UTH@SAC", team_raw="Sacramento Kings", player="Fox, De'Aaron"))
    # A second season keeps its own metrics.
    rows.append(listing(
        14, season=2026, report_id="Injury-Report_2025-10-21_05PM", matchup="HOU@OKC",
        game_date=date(2025, 10, 21), team_raw="Houston Rockets", player="Smith Jr., Jabari",
        slot_et=datetime(2025, 10, 21, 17),
    ))

    metrics = resolve.resolution_metrics(_resolved(index, rows))
    assert list(metrics) == [2025, 2026]
    season = metrics[2025]
    assert season["listed_not_rostered"] == {
        "rows": 3,
        "identities": 2,
        "by_team": {IND: {"rows": 1, "identities": 1}, OKC: {"rows": 2, "identities": 1}},
        "by_status": {
            "Available": {"rows": 1, "identities": 1},
            "Out": {"rows": 1, "identities": 1},
            "Questionable": {"rows": 1, "identities": 1},
        },
        "not_out_rows": [
            ("Injury-Report_2025-06-05_06PM", 2, FINALS, "Dort, Luguentz", "Questionable"),
            ("Injury-Report_2025-06-08_05PM", 4, LATER, "Turner, Myles", "Available"),
        ],
    }
    # Review Focus 2: the alias is visible in one line per tricode pair, and
    # placeholder rows of unresolved games count toward the pair.
    assert season["unresolved_matchups"] == {"PHO@LAL": 7, "UTH@SAC": 2}
    assert (season["rows"], season["rows_resolved"]) == (4, 4)

    other = metrics[2026]
    assert (other["rows"], other["rows_resolved"], other["row_rate"]) == (0, 0, None)
    assert (other["identities"], other["identity_rate"]) == (0, None)
    assert other["unresolved_matchups"] == {"HOU@OKC": 1}
    assert other["listed_not_rostered"]["not_out_rows"] == []

    with pytest.raises(resolve.AvailabilityResolveError, match="column"):
        resolve.resolution_metrics(_resolved(index, rows).drop(columns=["report_slot_et"]))
