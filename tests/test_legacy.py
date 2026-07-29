import io
from pathlib import Path

import pandas as pd
import pytest

from nba_data import legacy


LEGACY_PATH = Path("data/raw/legacy/nba-teams-1984-2019-ordered.csv")


def test_legacy_zero_attendance_becomes_missing():
    sample = io.StringIO(",GAME_ID,ATTENDANCE\n0,28300003,16167\n1,28300008,0\n")
    values = legacy.load_attendance(sample).set_index("game_id")["attendance"]
    assert values["0028300003"] == 16167
    assert pd.isna(values["0028300008"])


@pytest.mark.integration
@pytest.mark.skipif(not LEGACY_PATH.exists(), reason="legacy cache absent")
def test_complete_legacy_panel():
    result = legacy.load_team_games(LEGACY_PATH)
    assert len(result) == 81_656
    assert result["game_id"].nunique() == 40_828
