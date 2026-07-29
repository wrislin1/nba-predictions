import pandas as pd
import pytest
from nba_data import franchises


def test_charlotte_organizational_lineage():
    assert franchises.resolve("Charlotte Hornets", 1995) == "NOP"
    assert franchises.resolve("Charlotte Bobcats", 2010) == "CHA"
    assert franchises.resolve("Charlotte Hornets", 2020) == "CHA"


def test_unknown_exhibition_team_fails():
    with pytest.raises(franchises.UnknownTeamError):
        franchises.resolve("Team LeBron", 2021)


def test_all_reference_boundaries_resolve_once():
    ref = franchises.load_reference()
    assert len(frozenset(ref["franchise_id"])) == 30
    for row in ref.itertuples(index=False):
        first = 1984 if pd.isna(row.first_season) else int(row.first_season)
        last = 2026 if pd.isna(row.last_season) else int(row.last_season)
        assert franchises.resolve(row.alias, first) == row.franchise_id
        assert franchises.resolve(row.alias, last) == row.franchise_id
