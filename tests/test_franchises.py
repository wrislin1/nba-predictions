from pathlib import Path

import pandas as pd
import pytest
from nba_data import franchises
from nba_data import historical_bref


def test_charlotte_organizational_lineage():
    assert franchises.resolve("Charlotte Hornets", 1995) == "NOP"
    assert franchises.resolve("Charlotte Bobcats", 2010) == "CHA"
    assert franchises.resolve("Charlotte Hornets", 2020) == "CHA"


def test_unknown_exhibition_team_fails():
    with pytest.raises(franchises.UnknownTeamError):
        franchises.resolve("Team LeBron", 2021)


def test_all_reference_boundaries_resolve_once():
    ref = franchises.load_reference()
    assert len(frozenset(ref["franchise_id"])) == 45
    for row in ref.itertuples(index=False):
        first = 1984 if pd.isna(row.first_season) else int(row.first_season)
        last = 2026 if pd.isna(row.last_season) else int(row.last_season)
        assert franchises.resolve(row.alias, first) == row.franchise_id
        assert franchises.resolve(row.alias, last) == row.franchise_id


@pytest.mark.parametrize(
    ("alias", "season", "franchise_id"),
    [
        ("Philadelphia Warriors", 1947, "GSW"),
        ("Minneapolis Lakers", 1949, "LAL"),
        ("Tri-Cities Blackhawks", 1950, "ATL"),
        ("Syracuse Nationals", 1950, "PHI"),
        ("Rochester Royals", 1950, "SAC"),
        ("Fort Wayne Pistons", 1950, "DET"),
        ("San Diego Rockets", 1970, "HOU"),
        ("Buffalo Braves", 1975, "LAC"),
        ("New Orleans Jazz", 1978, "UTA"),
        ("Baltimore Bullets", 1950, "BAL_1947"),
        ("Baltimore Bullets", 1970, "WAS"),
        ("Denver Nuggets", 1950, "DEN_1949"),
        ("Denver Nuggets", 1980, "DEN"),
        ("Toronto Huskies", 1947, "TOR_1946"),
    ],
)
def test_historical_lineages(alias, season, franchise_id):
    assert franchises.resolve(alias, season) == franchise_id


def test_reference_contains_current_and_defunct_organizations():
    ref = franchises.load_reference()
    assert len(frozenset(ref["franchise_id"])) == 45


def test_new_york_nets_resolve_only_in_their_1977_nba_season():
    assert franchises.resolve("New York Nets", 1977) == "BKN"

    for season in (1976, 1978):
        with pytest.raises(franchises.UnknownTeamError):
            franchises.resolve("New York Nets", season)


@pytest.mark.integration
@pytest.mark.parametrize("season", [1947, 1960, 1983])
def test_every_spike_source_name_resolves_once(season):
    frame = historical_bref.load_season(Path("data/raw"), season)
    names = sorted(set(frame["away_team_name"]) | set(frame["home_team_name"]))
    assert {
        name: franchises.resolve(name, season)
        for name in names
    }
