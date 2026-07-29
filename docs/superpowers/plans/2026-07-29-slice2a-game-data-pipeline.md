# Slice 2A: Game-Data Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `team_games` / `games` / `franchises` / `venues` Parquet tables covering 1983-84 → 2025-26 from three verified sources (NAS-archived legacy data, hoopR parquet dumps, basketball-reference month pages), with hard validation gates.

**Architecture:** A small `src/nba_data` package with one module per source (legacy / hoopr / bref), a franchise-resolution reference table, and a `build.py` that assembles + validates + writes Parquet. Raw inputs are cached under gitignored `data/raw/`; only tidy Parquet is committed. Rosters/leaders parsing is deferred to a separate Slice 2B plan (nothing in Slice 3 needs them).

**Tech Stack:** Python 3.12 (`.venv` in repo), pandas 3.0.5, pyarrow, requests, lxml, pytest 9.

## Global Constraints

- Repo: `/home/riz/dev/nba-predictions-revived`. All commands run from repo root. The verified test invocation is `.venv/bin/python -m pytest -q` (baseline before this plan: **15 passed**).
- basketball-reference: ≤1 request per 12 s, browser User-Agent, cache every page + metadata to disk before parsing, **stop with diagnostics on any non-200 — never retry-hammer, never bypass** (spec constraint).
- stats.nba.com is TLS-blocked from this network (`docs/audits/2026-07-29-slice2-source-spike.md`) — do not add nba_api calls.
- Blank attendance is NEVER coerced to zero. `attendance_status` ∈ {`reported_zero`, `reported_positive`, `missing`}. `reported_zero` is only trusted for seasons 2020–2022 (COVID window); a zero in any other season is a validation error.
- Season convention everywhere: **ending year** (2021 = the 2020-21 season). hoopR files use the same convention.
- Known per-season completed regular-season game counts (hard gates): 2020 → 1,059; 2021 → 1,080; 2022–2026 → 1,230. Legacy total 1984–2019: 40,828 games / 81,656 team-rows.
- CPU-only while the GPU training task runs. Nothing here needs GPUs.
- Do not merge to master or push. End state: commits on branch `slice2a-game-data`, ready for the user's separate-session review.
- pandas 3 quirks: `game_date` columns from parquet are `datetime.date` objects — compare with `datetime.date`, not strings.

## File Structure

```
data/reference/franchises.csv        # hand-built alias → franchise_id (committed)
data/reference/venue_exceptions.csv  # curated venue overrides, seeded header-only
src/nba_data/franchises.py           # load + resolve(name, season)
src/nba_data/legacy.py               # NAS-restored 1983-2019 CSVs → team_games rows
src/nba_data/hoopr.py                # hoopR download + normalize (2020-2026)
src/nba_data/bref.py                 # b-ref month pages: fetch + parse attendance
src/nba_data/build.py                # assemble, validate (gates), write Parquet
src/nba_data/__main__.py             # CLI: fetch / build
scripts/restore_raw.sh               # NAS → data/raw/legacy restore (read-only source)
tests/test_franchises.py
tests/test_legacy.py
tests/test_hoopr.py
tests/test_bref.py
tests/test_build_gates.py
data/parquet/{team_games,games,franchises,venues}.parquet   # committed outputs
```

**Output schemas (the contract between all tasks):**

`team_games` — one row per franchise per game:
`game_id` (str), `season` (int, ending year), `source` (`nba_stats`|`espn`), `franchise_id` (str), `opponent_franchise_id` (str), `is_home` (bool), `game_date` (date), `wl` (int 1/0), `pts, fgm, fga, fg3m, fg3a, ftm, fta, oreb, dreb, reb, ast, stl, blk, tov, pf` (nullable Int64).

`games` — one row per game:
`game_id`, `season`, `source`, `game_date`, `home_franchise_id`, `away_franchise_id`, `home_pts`, `away_pts`, `neutral_site` (bool), `venue_name` (str|NA), `venue_city` (str|NA), `attendance` (nullable Int64), `attendance_status` (str), `attendance_source` (`bref`|`espn`|`legacy`|NA).

---

### Task 0: Branch setup

**Files:** none (git only)

- [ ] **Step 1: Confirm clean baseline**

Run: `git status --short && .venv/bin/python -m pytest -q`
Expected: no unstaged changes (plan file is committed), `15 passed`

- [ ] **Step 2: Create the work branch**

```bash
git checkout -b slice2a-game-data
```

---

### Task 1: Franchise reference table + resolver

**Files:**
- Create: `data/reference/franchises.csv`
- Create: `src/nba_data/franchises.py`
- Test: `tests/test_franchises.py`

**Interfaces:**
- Produces: `franchises.load_reference() -> pd.DataFrame` (columns `alias, franchise_id, first_season, last_season`); `franchises.resolve(name: str, season: int) -> str` raising `franchises.UnknownTeamError(ValueError)`; `franchises.FRANCHISE_IDS: set[str]` (30 ids).

- [ ] **Step 1: Write `data/reference/franchises.csv`**

```csv
alias,franchise_id,first_season,last_season
Atlanta Hawks,ATL,,
Boston Celtics,BOS,,
New Jersey Nets,BKN,,2012
Brooklyn Nets,BKN,2013,
Charlotte Hornets,CHA,,2002
Charlotte Bobcats,CHA,2005,2014
Charlotte Hornets,CHA,2015,
Chicago Bulls,CHI,,
Cleveland Cavaliers,CLE,,
Dallas Mavericks,DAL,,
Denver Nuggets,DEN,,
Detroit Pistons,DET,,
Golden State Warriors,GSW,,
Houston Rockets,HOU,,
Indiana Pacers,IND,,
San Diego Clippers,LAC,,1984
Los Angeles Clippers,LAC,1985,
LA Clippers,LAC,1985,
Los Angeles Lakers,LAL,,
Vancouver Grizzlies,MEM,,2001
Memphis Grizzlies,MEM,2002,
Miami Heat,MIA,,
Milwaukee Bucks,MIL,,
Minnesota Timberwolves,MIN,,
New Orleans Hornets,NOP,2003,2013
New Orleans/Oklahoma City Hornets,NOP,2006,2007
New Orleans Pelicans,NOP,2014,
New York Knicks,NYK,,
Seattle SuperSonics,OKC,,2008
Oklahoma City Thunder,OKC,2009,
Orlando Magic,ORL,,
Philadelphia 76ers,PHI,,
Phoenix Suns,PHX,,
Portland Trail Blazers,POR,,
Kansas City Kings,SAC,,1985
Sacramento Kings,SAC,1986,
San Antonio Spurs,SAS,,
Toronto Raptors,TOR,,
Utah Jazz,UTA,,
Washington Bullets,WAS,,1997
Washington Wizards,WAS,1998,
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_franchises.py
import pytest
from nba_data import franchises


def test_resolve_simple_and_alias():
    assert franchises.resolve("Atlanta Hawks", 1990) == "ATL"
    assert franchises.resolve("LA Clippers", 2022) == "LAC"
    assert franchises.resolve("Seattle SuperSonics", 2000) == "OKC"


def test_resolve_is_season_aware():
    assert franchises.resolve("Charlotte Hornets", 1995) == "CHA"
    assert franchises.resolve("New Orleans Hornets", 2010) == "NOP"
    assert franchises.resolve("Charlotte Hornets", 2020) == "CHA"
    assert franchises.resolve("Washington Bullets", 1990) == "WAS"


def test_unknown_name_raises():
    with pytest.raises(franchises.UnknownTeamError):
        franchises.resolve("Team LeBron", 2021)  # All-Star entrants must NOT resolve


def test_thirty_franchises():
    assert len(franchises.FRANCHISE_IDS) == 30
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_franchises.py -q`
Expected: FAIL (`ModuleNotFoundError` or `ImportError`)

- [ ] **Step 4: Implement `src/nba_data/franchises.py`**

```python
"""Franchise identity: alias + season -> stable franchise_id."""
from __future__ import annotations

import functools
from pathlib import Path

import pandas as pd

_REFERENCE_CSV = Path(__file__).resolve().parents[2] / "data" / "reference" / "franchises.csv"


class UnknownTeamError(ValueError):
    """A team name that maps to no franchise for the given season."""


@functools.lru_cache(maxsize=1)
def load_reference() -> pd.DataFrame:
    df = pd.read_csv(_REFERENCE_CSV, dtype={"alias": str, "franchise_id": str})
    df["first_season"] = df["first_season"].astype("Int64")
    df["last_season"] = df["last_season"].astype("Int64")
    return df


FRANCHISE_IDS: set[str] = set(pd.read_csv(_REFERENCE_CSV)["franchise_id"])


def resolve(name: str, season: int) -> str:
    ref = load_reference()
    rows = ref[ref["alias"] == name.strip()]
    for _, row in rows.iterrows():
        first = row["first_season"]
        last = row["last_season"]
        if (pd.isna(first) or season >= first) and (pd.isna(last) or season <= last):
            return row["franchise_id"]
    raise UnknownTeamError(f"no franchise for {name!r} in season {season}")
```

- [ ] **Step 5: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_franchises.py -q`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add data/reference/franchises.csv src/nba_data/franchises.py tests/test_franchises.py
git commit -m "feat: franchise reference table with season-aware resolver"
```

---

### Task 2: Restore legacy raw data from NAS

**Files:**
- Create: `scripts/restore_raw.sh`

**Interfaces:**
- Produces: `data/raw/legacy/nba-teams-1984-2019-ordered.csv` (81,657 lines incl. header) and `data/raw/legacy/league_boxscores_extra.csv` (40,829 lines) for Task 3.

- [ ] **Step 1: Write `scripts/restore_raw.sh`**

```bash
#!/usr/bin/env bash
# Restore the two legacy inputs the pipeline needs from the verified NAS archive.
# The archive is read-only source of truth; never write to it.
set -euo pipefail
ARCHIVE=/mnt/nas/llm/documents/nba-predictions-archive/pre-rewrite-e0f8e3a/raw/venv-static-data
DEST="$(dirname "$0")/../data/raw/legacy"
mkdir -p "$DEST"
for f in nba-teams-1984-2019-ordered.csv league_boxscores_extra.csv; do
  rsync -a --checksum "$ARCHIVE/$f" "$DEST/$f"
done
wc -l "$DEST"/*.csv
```

- [ ] **Step 2: Run it**

Run: `bash scripts/restore_raw.sh`
Expected: prints `81657 .../nba-teams-1984-2019-ordered.csv` and `40829 .../league_boxscores_extra.csv`

- [ ] **Step 3: Commit**

```bash
git add scripts/restore_raw.sh
git commit -m "feat: NAS restore script for legacy raw inputs"
```

---

### Task 3: Legacy loader (1983-84 → 2018-19)

**Files:**
- Create: `src/nba_data/legacy.py`
- Test: `tests/test_legacy.py`

**Interfaces:**
- Consumes: `franchises.resolve(name, season)`.
- Produces: `legacy.load_team_games(csv_path: Path) -> pd.DataFrame` in the **team_games schema** (Global contract); `legacy.load_attendance(csv_path: Path) -> pd.DataFrame` with columns `game_id, attendance` (attendance nullable Int64, 0 mapped to NA — a pre-COVID zero means unreported).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_legacy.py
import io
from pathlib import Path

import pandas as pd
import pytest

from nba_data import legacy

LEGACY_CSV = Path("data/raw/legacy/nba-teams-1984-2019-ordered.csv")

SAMPLE = """,SEASON_ID,TEAM_ID,TEAM_ABBREVIATION,TEAM_NAME,GAME_ID,GAME_DATE,MATCHUP,WL,MIN,PTS,FGM,FGA,FG_PCT,FG3M,FG3A,FG3_PCT,FTM,FTA,FT_PCT,OREB,DREB,REB,AST,STL,BLK,TOV,PF,PLUS_MINUS
0,22015,1610612748,MIA,Miami Heat,0021500008,2015-10-28,MIA vs. CHA,1,240,104,38,81,0.469,9,24,0.375,19,25,0.76,6,36,42,21,8,6,13,17,10
1,22015,1610612766,CHA,Charlotte Hornets,0021500008,2015-10-28,CHA @ MIA,0,240,94,35,84,0.417,9,29,0.31,15,19,0.789,7,34,41,22,7,3,10,19,-10
"""


def test_load_team_games_normalizes_sample():
    df = legacy.load_team_games(io.StringIO(SAMPLE))
    assert len(df) == 2
    home = df[df["is_home"]].iloc[0]
    away = df[~df["is_home"]].iloc[0]
    assert home["franchise_id"] == "MIA" and home["opponent_franchise_id"] == "CHA"
    assert away["franchise_id"] == "CHA" and away["opponent_franchise_id"] == "MIA"
    assert home["season"] == 2016  # SEASON_ID 22015 = 2015-16 = ending year 2016
    assert home["source"] == "nba_stats"
    assert home["game_id"] == "0021500008"
    assert home["wl"] == 1 and home["pts"] == 104 and home["pf"] == 17


def test_load_attendance_zero_becomes_missing():
    csv = ",GAME_ID,ATTENDANCE\n0,0021500008,19600\n1,0021500009,0\n2,0021500010,\n"
    df = legacy.load_attendance(io.StringIO(csv))
    by_id = df.set_index("game_id")["attendance"]
    assert by_id["0021500008"] == 19600
    assert pd.isna(by_id["0021500009"])
    assert pd.isna(by_id["0021500010"])


@pytest.mark.skipif(not LEGACY_CSV.exists(), reason="run scripts/restore_raw.sh first")
def test_full_legacy_file_loads():
    df = legacy.load_team_games(LEGACY_CSV)
    assert len(df) == 81656
    assert df["game_id"].nunique() == 40828
    assert set(df["franchise_id"]).issubset(legacy.franchises.FRANCHISE_IDS)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_legacy.py -q`
Expected: FAIL (module missing)

- [ ] **Step 3: Implement `src/nba_data/legacy.py`**

```python
"""Normalize the archived stats.nba.com data (1983-84 -> 2018-19)."""
from __future__ import annotations

import pandas as pd

from nba_data import franchises

_STATS = ["PTS", "FGM", "FGA", "FG3M", "FG3A", "FTM", "FTA",
          "OREB", "DREB", "REB", "AST", "STL", "BLK", "TOV", "PF"]


def load_team_games(csv_path) -> pd.DataFrame:
    raw = pd.read_csv(csv_path, index_col=0, dtype={"GAME_ID": str})
    season = (raw["SEASON_ID"].astype(int) % 20000) + 1  # 22015 -> ending year 2016
    is_home = raw["MATCHUP"].str.contains(" vs")
    fid = [franchises.resolve(n, s) for n, s in zip(raw["TEAM_NAME"], season)]

    out = pd.DataFrame({
        "game_id": raw["GAME_ID"].str.zfill(10),
        "season": season.astype(int),
        "source": "nba_stats",
        "franchise_id": fid,
        "is_home": is_home.to_numpy(),
        "game_date": pd.to_datetime(raw["GAME_DATE"]).dt.date,
        "wl": raw["WL"].astype(int),
    })
    for col in _STATS:
        out[col.lower()] = pd.to_numeric(raw[col], errors="coerce").astype("Int64")

    # opponent = the other franchise on the same game_id
    pair = out[["game_id", "franchise_id"]].merge(
        out[["game_id", "franchise_id"]], on="game_id", suffixes=("", "_opp"))
    pair = pair[pair["franchise_id"] != pair["franchise_id_opp"]]
    out = out.merge(pair.rename(columns={"franchise_id_opp": "opponent_franchise_id"}),
                    on=["game_id", "franchise_id"], how="left")
    return out.reset_index(drop=True)


def load_attendance(csv_path) -> pd.DataFrame:
    raw = pd.read_csv(csv_path, index_col=0, dtype={"GAME_ID": str})
    att = pd.to_numeric(raw["ATTENDANCE"], errors="coerce")
    att = att.mask(att <= 0)  # pre-COVID zero/blank both mean "not reported"
    return pd.DataFrame({
        "game_id": raw["GAME_ID"].str.zfill(10),
        "attendance": att.astype("Int64"),
    })
```

- [ ] **Step 4: Run to verify pass (incl. the full-file integration test)**

Run: `bash scripts/restore_raw.sh && .venv/bin/python -m pytest tests/test_legacy.py -q`
Expected: 3 passed (or 2 passed + 1 skipped if NAS unavailable — investigate before continuing)

- [ ] **Step 5: Commit**

```bash
git add src/nba_data/legacy.py tests/test_legacy.py
git commit -m "feat: legacy stats.nba.com loader with zero-attendance-as-missing rule"
```

---

### Task 4: hoopR provider (2019-20 → 2025-26)

**Files:**
- Create: `src/nba_data/hoopr.py`
- Test: `tests/test_hoopr.py`

**Interfaces:**
- Consumes: `franchises.resolve`, `franchises.UnknownTeamError`.
- Produces: `hoopr.SEASONS = range(2020, 2027)`; `hoopr.fetch(raw_dir: Path, seasons=SEASONS) -> None` (downloads `team_box_{s}.parquet` + `nba_schedule_{s}.parquet` into `raw_dir/hoopr/`, skipping files already present); `hoopr.load_season(raw_dir: Path, season: int) -> tuple[pd.DataFrame, pd.DataFrame]` returning (team_games rows, schedule_games rows). `schedule_games` columns: `game_id, season, game_date, home_franchise_id, away_franchise_id, home_pts, away_pts, neutral_site, venue_name, venue_city, espn_attendance` (nullable Int64).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hoopr.py
import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from nba_data import hoopr

RAW = Path("data/raw")


def _fixture_dir(tmp_path):
    d = tmp_path / "hoopr"
    d.mkdir()
    team_box = pd.DataFrame({
        "game_id": [401300001, 401300001, 401300099, 401300099],
        "season": [2021] * 4,
        "season_type": [2, 2, 2, 2],
        "game_date": [dt.date(2020, 12, 22)] * 2 + [dt.date(2021, 3, 7)] * 2,
        "team_display_name": ["Brooklyn Nets", "Golden State Warriors",
                              "Team LeBron", "Team Durant"],
        "team_home_away": ["home", "away", "home", "away"],
        "team_score": [125, 99, 170, 150],
        "team_winner": [True, False, True, False],
        "assists": [25, 20, 40, 38], "blocks": [5, 4, 1, 1],
        "defensive_rebounds": [30, 28, 30, 30],
        "field_goals_made": [45, 38, 70, 60],
        "field_goals_attempted": [90, 88, 120, 110],
        "fouls": [18, 22, 5, 5],
        "free_throws_made": [20, 15, 10, 12],
        "free_throws_attempted": [25, 20, 12, 15],
        "offensive_rebounds": [10, 9, 5, 5],
        "steals": [7, 6, 8, 8],
        "three_point_field_goals_made": [15, 8, 20, 18],
        "three_point_field_goals_attempted": [40, 30, 50, 45],
        "total_rebounds": [40, 37, 35, 35],
        "turnovers": [12, 14, 10, 10],
    })
    team_box.to_parquet(d / "team_box_2021.parquet")
    schedule = pd.DataFrame({
        "game_id": [401300001, 401300099, 401300500],
        "season": [2021] * 3,
        "season_type": [2, 2, 2],
        "game_date": [dt.date(2020, 12, 22), dt.date(2021, 3, 7), dt.date(2021, 4, 1)],
        "status_type_completed": [True, True, False],
        "home_display_name": ["Brooklyn Nets", "Team LeBron", "Chicago Bulls"],
        "away_display_name": ["Golden State Warriors", "Team Durant", "Miami Heat"],
        "home_score": [125, 170, 0], "away_score": [99, 150, 0],
        "neutral_site": [False, True, False],
        "attendance": [0, 1500, 0],
        "venue_full_name": ["Barclays Center", "State Farm Arena", "United Center"],
        "venue_address_city": ["Brooklyn", "Atlanta", "Chicago"],
    })
    schedule.to_parquet(d / "nba_schedule_2021.parquet")
    return tmp_path


def test_load_season_filters_and_normalizes(tmp_path):
    raw = _fixture_dir(tmp_path)
    team_games, sched = hoopr.load_season(raw, 2021)
    # All-Star rows (unmappable names) and uncompleted games are dropped
    assert set(sched["game_id"]) == {"espn-401300001"}
    assert len(team_games) == 2
    bkn = team_games[team_games["franchise_id"] == "BKN"].iloc[0]
    assert bkn["is_home"] and bkn["wl"] == 1 and bkn["pts"] == 125
    assert bkn["opponent_franchise_id"] == "GSW"
    assert bkn["source"] == "espn" and bkn["season"] == 2021
    row = sched.iloc[0]
    assert row["home_franchise_id"] == "BKN" and row["espn_attendance"] == 0
    assert not row["neutral_site"] and row["venue_name"] == "Barclays Center"


def test_reb_consistency_columns_exist(tmp_path):
    raw = _fixture_dir(tmp_path)
    team_games, _ = hoopr.load_season(raw, 2021)
    for col in ["fgm", "fga", "fg3m", "fg3a", "ftm", "fta",
                "oreb", "dreb", "reb", "ast", "stl", "blk", "tov", "pf"]:
        assert col in team_games.columns


@pytest.mark.skipif(not (RAW / "hoopr" / "team_box_2021.parquet").exists(),
                    reason="run `python -m nba_data fetch` first")
def test_real_2021_season_counts():
    team_games, sched = hoopr.load_season(RAW, 2021)
    assert len(sched) == 1080
    assert len(team_games) == 2160
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_hoopr.py -q`
Expected: FAIL (module missing)

- [ ] **Step 3: Implement `src/nba_data/hoopr.py`**

```python
"""Provider adapter for sportsdataverse/hoopR-nba-data parquet dumps (ESPN)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import requests

from nba_data import franchises

SEASONS = range(2020, 2027)  # ending-year convention: 2020 = 2019-20
_BASE = "https://github.com/sportsdataverse/hoopR-nba-data/raw/main/nba"
_FILES = {
    "team_box": "{base}/team_box/parquet/team_box_{season}.parquet",
    "schedule": "{base}/schedules/parquet/nba_schedule_{season}.parquet",
}
_STAT_MAP = {
    "field_goals_made": "fgm", "field_goals_attempted": "fga",
    "three_point_field_goals_made": "fg3m",
    "three_point_field_goals_attempted": "fg3a",
    "free_throws_made": "ftm", "free_throws_attempted": "fta",
    "offensive_rebounds": "oreb", "defensive_rebounds": "dreb",
    "total_rebounds": "reb", "assists": "ast", "steals": "stl",
    "blocks": "blk", "turnovers": "tov", "fouls": "pf",
}


def fetch(raw_dir: Path, seasons=SEASONS) -> None:
    dest = raw_dir / "hoopr"
    dest.mkdir(parents=True, exist_ok=True)
    for season in seasons:
        for kind, pattern in _FILES.items():
            name = pattern.format(base=_BASE, season=season).rsplit("/", 1)[-1]
            out = dest / name
            if out.exists():
                continue
            url = pattern.format(base=_BASE, season=season)
            resp = requests.get(url, timeout=120)
            resp.raise_for_status()
            out.write_bytes(resp.content)
            print(f"fetched {name} ({len(resp.content)} bytes)")


def _resolve_or_none(name: str, season: int):
    try:
        return franchises.resolve(name, season)
    except franchises.UnknownTeamError:
        return None  # All-Star teams, exhibition entrants


def load_season(raw_dir: Path, season: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    d = raw_dir / "hoopr"
    box = pd.read_parquet(d / f"team_box_{season}.parquet")
    sched = pd.read_parquet(d / f"nba_schedule_{season}.parquet")

    sched = sched[(sched["season_type"] == 2)
                  & (sched["status_type_completed"] == True)].copy()  # noqa: E712
    sched["home_franchise_id"] = [
        _resolve_or_none(n, season) for n in sched["home_display_name"]]
    sched["away_franchise_id"] = [
        _resolve_or_none(n, season) for n in sched["away_display_name"]]
    sched = sched.dropna(subset=["home_franchise_id", "away_franchise_id"])

    games = pd.DataFrame({
        "game_id": "espn-" + sched["game_id"].astype(str),
        "season": season,
        "source": "espn",
        "game_date": sched["game_date"],
        "home_franchise_id": sched["home_franchise_id"],
        "away_franchise_id": sched["away_franchise_id"],
        "home_pts": pd.to_numeric(sched["home_score"]).astype("Int64"),
        "away_pts": pd.to_numeric(sched["away_score"]).astype("Int64"),
        "neutral_site": sched["neutral_site"].astype(bool),
        "venue_name": sched["venue_full_name"],
        "venue_city": sched["venue_address_city"],
        "espn_attendance": pd.to_numeric(sched["attendance"], errors="coerce").astype("Int64"),
    }).reset_index(drop=True)

    keep_ids = set(games["game_id"])
    box = box[box["season_type"] == 2].copy()
    box["game_id"] = "espn-" + box["game_id"].astype(str)
    box = box[box["game_id"].isin(keep_ids)]
    box["franchise_id"] = [
        _resolve_or_none(n, season) for n in box["team_display_name"]]
    box = box.dropna(subset=["franchise_id"])

    team_games = pd.DataFrame({
        "game_id": box["game_id"],
        "season": season,
        "source": "espn",
        "franchise_id": box["franchise_id"],
        "is_home": (box["team_home_away"] == "home").to_numpy(),
        "game_date": box["game_date"],
        "wl": box["team_winner"].astype(bool).astype(int),
        "pts": pd.to_numeric(box["team_score"]).astype("Int64"),
    })
    for src, dst in _STAT_MAP.items():
        team_games[dst] = pd.to_numeric(box[src], errors="coerce").astype("Int64")

    pair = team_games[["game_id", "franchise_id"]].merge(
        team_games[["game_id", "franchise_id"]], on="game_id", suffixes=("", "_opp"))
    pair = pair[pair["franchise_id"] != pair["franchise_id_opp"]]
    team_games = team_games.merge(
        pair.rename(columns={"franchise_id_opp": "opponent_franchise_id"}),
        on=["game_id", "franchise_id"], how="left")
    return team_games.reset_index(drop=True), games
```

- [ ] **Step 4: Run to verify pass (fixture tests)**

Run: `.venv/bin/python -m pytest tests/test_hoopr.py -q`
Expected: 2 passed, 1 skipped

- [ ] **Step 5: Commit**

```bash
git add src/nba_data/hoopr.py tests/test_hoopr.py
git commit -m "feat: hoopR provider adapter with franchise filtering"
```

---

### Task 5: basketball-reference attendance provider

**Files:**
- Create: `src/nba_data/bref.py`
- Test: `tests/test_bref.py`

**Interfaces:**
- Consumes: `franchises.resolve`.
- Produces: `bref.MONTHS_BY_SEASON: dict[int, list[str]]`; `bref.fetch(raw_dir: Path, seasons) -> None` (12 s spacing, UA, cache, `bref.AccessBlocked(RuntimeError)` on non-200); `bref.load_attendance(raw_dir: Path, season: int) -> pd.DataFrame` with columns `game_date, home_franchise_id, away_franchise_id, attendance` (nullable Int64 — blank stays NA, zero stays 0), `arena` (str).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bref.py
from pathlib import Path

import pandas as pd
import pytest

from nba_data import bref

RAW = Path("data/raw")

MONTH_HTML = """<html><body><table id="schedule">
<thead><tr><th>Date</th><th>Start (ET)</th><th>Visitor/Neutral</th><th>PTS</th>
<th>Home/Neutral</th><th>PTS</th><th></th><th></th><th>Attend.</th><th>LOG</th>
<th>Arena</th><th>Notes</th></tr></thead>
<tbody>
<tr><td>Tue, Dec 22, 2020</td><td>7:00p</td><td>Golden State Warriors</td><td>99</td>
<td>Brooklyn Nets</td><td>125</td><td></td><td></td><td>0</td><td>2:14</td>
<td>Barclays Center</td><td></td></tr>
<tr><td>Wed, Dec 23, 2020</td><td>7:00p</td><td>Charlotte Hornets</td><td>114</td>
<td>Cleveland Cavaliers</td><td>121</td><td></td><td></td><td>300</td><td>2:11</td>
<td>Rocket Mortgage Fieldhouse</td><td></td></tr>
<tr><td>Sat, Dec 26, 2020</td><td>8:00p</td><td>Miami Heat</td><td></td>
<td>Dallas Mavericks</td><td></td><td></td><td></td><td></td><td></td>
<td>American Airlines Center</td><td>Postponed</td></tr>
</tbody></table></body></html>"""


def test_parse_month_semantics(tmp_path):
    d = tmp_path / "bref"
    d.mkdir(parents=True)
    (d / "NBA_2021_games-december.html").write_text(MONTH_HTML)
    df = bref.load_attendance(tmp_path, 2021, months=["december"])
    assert len(df) == 2  # postponed row (no scores) dropped
    zero = df[df["home_franchise_id"] == "BKN"].iloc[0]
    assert zero["attendance"] == 0                      # explicit zero survives
    pos = df[df["home_franchise_id"] == "CLE"].iloc[0]
    assert pos["attendance"] == 300
    assert pos["away_franchise_id"] == "CHA"
    assert str(pos["game_date"]) == "2020-12-23"
    assert pos["arena"] == "Rocket Mortgage Fieldhouse"


def test_months_by_season_covers_all_seven_seasons():
    assert set(bref.MONTHS_BY_SEASON) == set(range(2020, 2027))
    assert "july" in bref.MONTHS_BY_SEASON[2020]        # bubble seeding games
    assert bref.MONTHS_BY_SEASON[2021][0] == "december"  # late COVID start


@pytest.mark.skipif(not (RAW / "spike" / "bref" / "NBA_2021_games-december.html").exists(),
                    reason="spike cache absent")
def test_real_december_2020_counts():
    df = bref.load_attendance(RAW / "spike", 2021, months=["december"])
    assert len(df) == 67
    assert int((df["attendance"] == 0).sum()) == 56
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_bref.py -q`
Expected: FAIL (module missing)

- [ ] **Step 3: Implement `src/nba_data/bref.py`**

```python
"""basketball-reference month pages: polite cached fetch + attendance parse."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
import requests

from nba_data import franchises

USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_STANDARD = ["october", "november", "december", "january", "february", "march", "april"]
MONTHS_BY_SEASON: dict[int, list[str]] = {
    2020: ["october", "november", "december", "january", "february", "march",
           "july", "august"],                       # suspension + Orlando bubble
    2021: ["december", "january", "february", "march", "april", "may"],
    **{s: list(_STANDARD) for s in range(2022, 2027)},
}


class AccessBlocked(RuntimeError):
    """b-ref returned a non-200; stop, do not retry or bypass."""


def fetch(raw_dir: Path, seasons=range(2020, 2027), sleep_s: float = 12.0) -> None:
    dest = raw_dir / "bref"
    dest.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT,
                            "Accept-Language": "en-US,en;q=0.9"})
    for season in seasons:
        for month in MONTHS_BY_SEASON[season]:
            out = dest / f"NBA_{season}_games-{month}.html"
            if out.exists():
                continue
            url = f"https://www.basketball-reference.com/leagues/NBA_{season}_games-{month}.html"
            resp = session.get(url, timeout=30)
            if resp.status_code != 200:
                raise AccessBlocked(f"{url} -> HTTP {resp.status_code}")
            out.write_bytes(resp.content)
            (dest / f"{out.stem}.meta.json").write_text(json.dumps({
                "url": url, "status": resp.status_code,
                "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "content_length": len(resp.content)}))
            print(f"fetched {out.name}")
            time.sleep(sleep_s)


def load_attendance(raw_dir: Path, season: int, months=None) -> pd.DataFrame:
    months = months or MONTHS_BY_SEASON[season]
    frames = []
    for month in months:
        path = raw_dir / "bref" / f"NBA_{season}_games-{month}.html"
        table = pd.read_html(path)[0]
        table.columns = ["date", "start", "visitor", "away_pts", "home", "home_pts",
                         *[f"x{i}" for i in range(len(table.columns) - 10)],
                         "attend", "log", "arena", "notes"]
        # rows without scores are future/postponed/cancelled listings
        played = table[pd.to_numeric(table["home_pts"], errors="coerce").notna()
                       & pd.to_numeric(table["away_pts"], errors="coerce").notna()].copy()
        frames.append(pd.DataFrame({
            "game_date": pd.to_datetime(played["date"], format="%a, %b %d, %Y").dt.date,
            "home_franchise_id": [franchises.resolve(n, season) for n in played["home"]],
            "away_franchise_id": [franchises.resolve(n, season) for n in played["away"]],
            "attendance": pd.to_numeric(played["attend"], errors="coerce").astype("Int64"),
            "arena": played["arena"].astype(str),
        }))
    return pd.concat(frames, ignore_index=True)
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_bref.py -q`
Expected: 2 passed + 1 passed-or-skipped (the spike-cache test passes on this machine)

- [ ] **Step 5: Commit**

```bash
git add src/nba_data/bref.py tests/test_bref.py
git commit -m "feat: b-ref attendance provider with explicit-zero semantics"
```

---

### Task 6: Build + validation gates

**Files:**
- Create: `src/nba_data/build.py`
- Create: `data/reference/venue_exceptions.csv` (header only: `venue_name,venue_city,franchise_id,first_season,last_season,capacity,provenance`)
- Test: `tests/test_build_gates.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `build.SEASON_GAME_COUNTS = {2020: 1059, 2021: 1080, 2022: 1230, 2023: 1230, 2024: 1230, 2025: 1230, 2026: 1230}`; `build.ValidationError(RuntimeError)`; `build.assemble_games(schedule_games, bref_attendance=None, legacy_games=None, legacy_attendance=None) -> pd.DataFrame` (games schema); `build.validate(team_games, games) -> list[str]` (report lines; raises `ValidationError` on gate failure); `build.run(data_dir: Path) -> None` writing the four Parquet files.

**Attendance resolution order (encode exactly):** for post-2019 games, b-ref value (matched on `game_date + home_franchise_id + away_franchise_id`) is the source of record → `attendance_source="bref"`; unmatched games fall back to `espn_attendance` → `"espn"`; legacy games use `legacy.load_attendance` → `"legacy"`. Status: value 0 → `reported_zero` (only valid seasons 2020–2022), value > 0 → `reported_positive`, NA → `missing`.

**Gates (each raises ValidationError):**
1. No duplicate `game_id` in `games`; every game has exactly 2 `team_games` rows (one `is_home=True`, one `is_home=False` — ESPN designates a home side even for neutral-site bubble games).
2. Per-season game counts equal `SEASON_GAME_COUNTS` for 2020–2026; legacy total == 40,828.
3. All `franchise_id`/`opponent_franchise_id` values ∈ `franchises.FRANCHISE_IDS`.
4. `attendance_status == "reported_zero"` only in seasons 2020–2022.
5. Scores within 40–200; `home_pts != away_pts` (no ties in NBA).
6. Attendance coverage (`status != "missing"`) ≥ 95% per season for 2020–2026 (report actual % per season).
7. Cross-validation report (non-gate, printed): % of post-2019 games where b-ref and ESPN attendance disagree by > 10%.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_build_gates.py
import datetime as dt

import pandas as pd
import pytest

from nba_data import build


def _games(rows):
    base = {
        "game_id": "espn-1", "season": 2021, "source": "espn",
        "game_date": dt.date(2021, 1, 15),
        "home_franchise_id": "BKN", "away_franchise_id": "GSW",
        "home_pts": 120, "away_pts": 110, "neutral_site": False,
        "venue_name": "Barclays Center", "venue_city": "Brooklyn",
        "attendance": 0, "attendance_status": "reported_zero",
        "attendance_source": "bref",
    }
    return pd.DataFrame([{**base, **r} for r in rows])


def _team_games_for(games):
    rows = []
    for _, g in games.iterrows():
        for fid, opp, home, pts in [
            (g["home_franchise_id"], g["away_franchise_id"], True, g["home_pts"]),
            (g["away_franchise_id"], g["home_franchise_id"], False, g["away_pts"]),
        ]:
            rows.append({
                "game_id": g["game_id"], "season": g["season"], "source": g["source"],
                "franchise_id": fid, "opponent_franchise_id": opp, "is_home": home,
                "game_date": g["game_date"],
                "wl": int(pts == max(g["home_pts"], g["away_pts"])), "pts": pts,
            })
    return pd.DataFrame(rows)


def _passing_2021():
    games = _games(
        [{"game_id": f"espn-{i}"} for i in range(build.SEASON_GAME_COUNTS[2021])])
    return _team_games_for(games), games


def test_valid_frame_passes():
    team_games, games = _passing_2021()
    report = build.validate(team_games, games)
    assert any("2021" in line for line in report)


def test_duplicate_game_id_fails():
    team_games, games = _passing_2021()
    games.loc[1, "game_id"] = games.loc[0, "game_id"]
    with pytest.raises(build.ValidationError, match="duplicate"):
        build.validate(team_games, games)


def test_wrong_season_count_fails():
    team_games, games = _passing_2021()
    games = games.iloc[:-1]
    team_games = team_games.iloc[:-2]
    with pytest.raises(build.ValidationError, match="1080"):
        build.validate(team_games, games)


def test_reported_zero_outside_covid_window_fails():
    team_games, games = _passing_2021()
    games["season"] = 2024
    games["game_date"] = dt.date(2024, 1, 15)
    team_games["season"] = 2024
    with pytest.raises(build.ValidationError):
        build.validate(team_games, games)


def test_attendance_resolution_prefers_bref_and_never_coerces_blank():
    schedule = pd.DataFrame([{
        "game_id": "espn-1", "season": 2021, "game_date": dt.date(2021, 1, 15),
        "home_franchise_id": "BKN", "away_franchise_id": "GSW",
        "home_pts": 120, "away_pts": 110, "neutral_site": False,
        "venue_name": "Barclays Center", "venue_city": "Brooklyn",
        "espn_attendance": 1500,
    }, {
        "game_id": "espn-2", "season": 2021, "game_date": dt.date(2021, 1, 16),
        "home_franchise_id": "MIA", "away_franchise_id": "DAL",
        "home_pts": 100, "away_pts": 90, "neutral_site": False,
        "venue_name": "Kaseya Center", "venue_city": "Miami",
        "espn_attendance": pd.NA,
    }])
    bref_att = pd.DataFrame([{
        "game_date": dt.date(2021, 1, 15), "home_franchise_id": "BKN",
        "away_franchise_id": "GSW", "attendance": 0, "arena": "Barclays Center",
    }])
    games = build.assemble_games(schedule_games=schedule, bref_attendance=bref_att)
    g1 = games[games["game_id"] == "espn-1"].iloc[0]
    assert g1["attendance"] == 0 and g1["attendance_status"] == "reported_zero"
    assert g1["attendance_source"] == "bref"       # b-ref beats ESPN's 1500
    g2 = games[games["game_id"] == "espn-2"].iloc[0]
    assert pd.isna(g2["attendance"]) and g2["attendance_status"] == "missing"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_build_gates.py -q`
Expected: FAIL (module missing)

- [ ] **Step 3: Implement `src/nba_data/build.py`**

```python
"""Assemble, validate, and write the tidy Parquet tables."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from nba_data import bref, franchises, hoopr, legacy

SEASON_GAME_COUNTS = {2020: 1059, 2021: 1080, 2022: 1230, 2023: 1230,
                      2024: 1230, 2025: 1230, 2026: 1230}
LEGACY_TOTAL_GAMES = 40828
ZERO_OK_SEASONS = {2020, 2021, 2022}


class ValidationError(RuntimeError):
    """A hard data gate failed; nothing is written."""


def _status(att) -> str:
    if pd.isna(att):
        return "missing"
    return "reported_zero" if att == 0 else "reported_positive"


def assemble_games(schedule_games: pd.DataFrame,
                   bref_attendance: pd.DataFrame | None = None,
                   legacy_games: pd.DataFrame | None = None,
                   legacy_attendance: pd.DataFrame | None = None) -> pd.DataFrame:
    games = schedule_games.copy()
    games["attendance"] = games["espn_attendance"]
    games["attendance_source"] = "espn"
    if bref_attendance is not None and len(bref_attendance):
        b = bref_attendance.rename(columns={"attendance": "bref_attendance"})
        games = games.merge(
            b[["game_date", "home_franchise_id", "away_franchise_id", "bref_attendance"]],
            on=["game_date", "home_franchise_id", "away_franchise_id"], how="left")
        matched = games["bref_attendance"].notna()
        games.loc[matched, "attendance"] = games.loc[matched, "bref_attendance"]
        games.loc[matched, "attendance_source"] = "bref"
        games = games.drop(columns=["bref_attendance"])
    games["attendance"] = games["attendance"].astype("Int64")
    games["attendance_status"] = [_status(a) for a in games["attendance"]]
    games = games.drop(columns=["espn_attendance"])

    if legacy_games is not None:
        lg = legacy_games.copy()
        if legacy_attendance is not None:
            lg = lg.merge(legacy_attendance, on="game_id", how="left")
        else:
            lg["attendance"] = pd.NA
        lg["attendance"] = lg["attendance"].astype("Int64")
        lg["attendance_status"] = [_status(a) for a in lg["attendance"]]
        lg["attendance_source"] = "legacy"
        lg["neutral_site"] = False
        lg["venue_name"] = pd.NA
        lg["venue_city"] = pd.NA
        games = pd.concat([lg[games.columns], games], ignore_index=True)
    return games


def validate(team_games: pd.DataFrame, games: pd.DataFrame) -> list[str]:
    errors, report = [], []
    dup = games["game_id"].duplicated()
    if dup.any():
        errors.append(f"duplicate game_id(s): {sorted(games.loc[dup, 'game_id'])[:5]}")

    per_side = team_games.groupby("game_id")["is_home"].agg(["count", "sum"])
    bad_pairs = per_side[(per_side["count"] != 2) | (per_side["sum"] != 1)]
    if len(bad_pairs):
        errors.append(f"{len(bad_pairs)} game(s) without exactly one home + one away row")

    for season, expected in SEASON_GAME_COUNTS.items():
        got = int((games["season"] == season).sum())
        if got and got != expected:
            errors.append(f"season {season}: {got} games, expected {expected}")
        if got:
            cov = (games.loc[games["season"] == season, "attendance_status"]
                   != "missing").mean()
            report.append(f"season {season}: {got} games, attendance coverage {cov:.1%}")
            if cov < 0.95:
                errors.append(f"season {season}: attendance coverage {cov:.1%} < 95%")
    legacy_n = int((games["source"] == "nba_stats").sum())
    if legacy_n and legacy_n != LEGACY_TOTAL_GAMES:
        errors.append(f"legacy games {legacy_n} != {LEGACY_TOTAL_GAMES}")

    ids = set(team_games["franchise_id"]) | set(team_games["opponent_franchise_id"])
    unknown = ids - franchises.FRANCHISE_IDS
    if unknown:
        errors.append(f"unknown franchise ids: {sorted(unknown)}")

    zero_bad = games[(games["attendance_status"] == "reported_zero")
                     & (~games["season"].isin(ZERO_OK_SEASONS))]
    if len(zero_bad):
        errors.append(f"reported_zero outside 2020-2022: {len(zero_bad)} game(s)")

    scores = games[["home_pts", "away_pts"]].dropna()
    if ((scores < 40) | (scores > 200)).any().any():
        errors.append("score outside sanity range 40-200")
    if (games["home_pts"] == games["away_pts"]).any():
        errors.append("tie score found (impossible in NBA)")

    if errors:
        raise ValidationError("; ".join(errors))
    return report


def run(data_dir: Path) -> None:
    raw = data_dir / "raw"
    out = data_dir / "parquet"
    out.mkdir(parents=True, exist_ok=True)

    legacy_tg = legacy.load_team_games(raw / "legacy" / "nba-teams-1984-2019-ordered.csv")
    legacy_att = legacy.load_attendance(raw / "legacy" / "league_boxscores_extra.csv")
    legacy_games = _pair_legacy(legacy_tg)

    tg_frames, sched_frames, bref_frames = [legacy_tg], [], []
    for season in hoopr.SEASONS:
        tg, sched = hoopr.load_season(raw, season)
        tg_frames.append(tg)
        sched_frames.append(sched)
        bref_frames.append(bref.load_attendance(raw, season))

    team_games = pd.concat(tg_frames, ignore_index=True)
    games = assemble_games(
        schedule_games=pd.concat(sched_frames, ignore_index=True),
        bref_attendance=pd.concat(bref_frames, ignore_index=True),
        legacy_games=legacy_games, legacy_attendance=legacy_att)

    for line in validate(team_games, games):
        print(line)

    team_games.to_parquet(out / "team_games.parquet", index=False)
    games.to_parquet(out / "games.parquet", index=False)
    franchises.load_reference().to_parquet(out / "franchises.parquet", index=False)
    _venues(games, data_dir).to_parquet(out / "venues.parquet", index=False)
    print(f"wrote 4 tables to {out}")


def _pair_legacy(team_games: pd.DataFrame) -> pd.DataFrame:
    home = team_games[team_games["is_home"]].set_index("game_id")
    away = team_games[~team_games["is_home"]].set_index("game_id")
    return pd.DataFrame({
        "game_id": home.index,
        "season": home["season"], "source": home["source"],
        "game_date": home["game_date"],
        "home_franchise_id": home["franchise_id"],
        "away_franchise_id": home["opponent_franchise_id"],
        "home_pts": home["pts"], "away_pts": away.loc[home.index, "pts"],
    }).reset_index(drop=True)


def _venues(games: pd.DataFrame, data_dir: Path) -> pd.DataFrame:
    post = games[games["venue_name"].notna()]
    venues = (post.groupby(["venue_name", "venue_city", "home_franchise_id", "season"])
              .agg(games=("game_id", "count"),
                   max_attendance=("attendance", "max"))
              .reset_index())
    exceptions = pd.read_csv(data_dir / "reference" / "venue_exceptions.csv")
    venues["provenance"] = "observed"
    if len(exceptions):
        venues = pd.concat([venues, exceptions.assign(games=pd.NA)], ignore_index=True)
    return venues
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_build_gates.py -q`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add src/nba_data/build.py data/reference/venue_exceptions.csv tests/test_build_gates.py
git commit -m "feat: build pipeline with hard validation gates"
```

---

### Task 7: CLI

**Files:**
- Create: `src/nba_data/__main__.py`

**Interfaces:**
- Produces: `python -m nba_data fetch` (hoopR downloads + b-ref pages, both cached/idempotent) and `python -m nba_data build` (runs `build.run`, prints the validation report).

- [ ] **Step 1: Implement**

```python
"""CLI: python -m nba_data {fetch,build}"""
import argparse
from pathlib import Path

from nba_data import bref, build, hoopr

RAW = Path(__file__).resolve().parents[2] / "data" / "raw"


def main() -> None:
    parser = argparse.ArgumentParser(prog="nba_data")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("fetch", help="download hoopR parquet + b-ref month pages (cached)")
    sub.add_parser("build", help="assemble, validate, and write data/parquet tables")
    args = parser.parse_args()
    if args.command == "fetch":
        hoopr.fetch(RAW)
        bref.fetch(RAW)
    elif args.command == "build":
        build.run(RAW.parent)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-test help**

Run: `.venv/bin/python -m nba_data --help`
Expected: usage text listing `fetch` and `build`

- [ ] **Step 3: Commit**

```bash
git add src/nba_data/__main__.py
git commit -m "feat: nba_data CLI (fetch/build)"
```

---

### Task 8: Full fetch + build + commit the Parquet tables

- [ ] **Step 1: Fetch everything**

Run: `.venv/bin/python -m nba_data fetch`
Expected: 14 hoopR files (7 seasons × 2) fast; then ~49 b-ref pages at 12 s spacing (~11 min). Re-running prints nothing new (cache hit). If `AccessBlocked` is raised, STOP and report — do not work around.

- [ ] **Step 2: Build**

Run: `.venv/bin/python -m nba_data build`
Expected: validation report printed; per-season counts exactly `1059/1080/1230×5`; legacy 40,828; attendance coverage ≥95% for every season 2020–2026; four Parquet files written to `data/parquet/`.

If a count gate fails (e.g. the known 1,081-vs-1,080 hoopR off-by-one for 2021), diagnose the extra row (`sched[~sched.game_id.isin(...)]`), document the exclusion rule in `build.py` with a comment naming the specific game, and re-run. Do NOT loosen the gate.

- [ ] **Step 3: Verify total sizes are git-friendly**

Run: `du -sh data/parquet/ && git status --short`
Expected: single-digit MB total

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/python -m pytest -q && .venv/bin/python scripts/verify_repository.py`
Expected: all tests pass (including the previously-skipped integration tests), verify script exits 0

- [ ] **Step 5: Commit the data**

```bash
git add data/parquet/
git commit -m "data: team_games/games/franchises/venues parquet, 1983-84 through 2025-26"
```

---

### Task 9: README data dictionary + wrap-up

- [ ] **Step 1: Update README** — add a "Data tables" section documenting the four Parquet tables' schemas (copy the Global contract schemas), the attendance_status semantics, the season ending-year convention, and `python -m nba_data fetch/build` usage.

- [ ] **Step 2: Full verification**

Run: `.venv/bin/python -m pytest -q && .venv/bin/python scripts/verify_repository.py && git log --oneline master..HEAD`
Expected: all green; commit list shows one commit per task

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: data dictionary for the game-data pipeline"
```

**End state:** branch `slice2a-game-data` ready for the user's separate-session review. Do not merge or push.
