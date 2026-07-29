# Slice 2A Game-Data Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** Slice 2A — implemented + hardened on `slice2a-game-data`;
verified with 202 passing tests, deterministic artifact hashes, and the
recoverable NAS raw snapshot.

**Landed commits:** `6536143`, `24a77b3`, `3094ab2`, `93dfe10`, `e90dbe8`,
`5c8ebfd`, `6209c14`, `8996cb9`, `b6960e7`, `69f3564`, `38352ae`,
`f5190c9`, `0b85823`, `646ee06`, `111074a`, `473ad12`, `a801e83`,
`7d6b5fc`, `6d047a3`, `ea341ed`, `aa44f96`, `06e5d18`, `57e6336`,
`c8657f6`, `c4b449f`, `48b52b6`, and `de97a0b`.

**Goal:** Build validated `team_games`, `games`, `franchises`, and `venues` Parquet tables covering 1983-84 through 2025-26, using NBA Stats for canonical box scores, hoopR for schedule/venue enrichment, and basketball-reference for attendance.

**Architecture:** Normalize the archived and newly fetched NBA Stats rows through one shared team-game adapter so all 43 seasons retain the same schema and canonical NBA `GAME_ID`. Enrich the seven modern seasons by joining a pinned hoopR schedule and politely cached basketball-reference pages on `season + game_date + home_franchise_id + away_franchise_id`. Raw responses are checksummed and gitignored; only validated, deterministically sorted Parquet tables, curated references, and a build manifest are committed.

**Tech Stack:** Python 3.12, pandas 3.0.5, pyarrow, requests 2.34.2, nba_api 1.11.4, lxml, pytest 9.

## Global Constraints

- Repository: `/home/riz/dev/nba-predictions-revived`; run every command from this root.
- Implementation branch: `slice2a-game-data`; merge and push occur only after
  the completed plan passes final verification.
- CPU only; no task in this plan allocates GPU memory.
- Season is always the ending year: `2021` means 2020-21.
- Raw provider files live under gitignored `data/raw/`; curated Parquet lives under committed `data/parquet/`.
- NBA Stats access requires a DNS preflight. `stats.nba.com` resolving to `0.0.0.0`, loopback, or `::` is a local DNS/AdGuard failure and must stop before requesting.
- Fetch NBA Stats once per season with `LeagueGameFinder`, wait at least 12 seconds between real requests, cache the raw JSON and metadata, and never bypass TLS or retry-hammer.
- Pin hoopR schedules to commit `50fe7e429c6b5bcbe62e7a56d19afe5a80e6e143`; never fetch mutable `main`.
- basketball-reference: at most one request per 12 seconds, ordinary certificate verification, browser User-Agent, cache every page before parsing, and stop on HTTP/TLS/schema failure.
- basketball-reference is attendance source of record. A matched blank remains missing; ESPN is used only when no b-ref row matches.
- Known modern regular-season game counts are hard gates, including missing seasons: `{2020: 1059, 2021: 1080, 2022: 1230, 2023: 1230, 2024: 1230, 2025: 1230, 2026: 1230}`.
- Legacy hard gates: 40,828 games and 81,656 team-game rows.
- Complete output hard gates: 49,117 games and 98,234 team-game rows.
- Unit tests never perform network I/O. Tests marked `integration` consume existing cache files only.
- Output ordering is deterministic: `season, game_date, game_id`; `team_games` adds `is_home` descending.
- Capacity must resolve for every non-neutral venue used in ending seasons 2020–2022; neutral bubble venues may remain without capacity.

---

## File Structure

```text
pyproject.toml                         # installable src package + pytest marker
data/reference/franchises.csv         # source-name era -> stable franchise
data/reference/hoopr_snapshot.json    # pinned upstream schedule snapshot
data/reference/venue_capacities.csv   # venue/team/season capacity provenance
src/nba_data/cache.py                 # checksummed atomic raw cache
src/nba_data/franchises.py            # season-aware identity resolver
src/nba_data/team_rows.py             # shared NBA Stats row normalization
src/nba_data/legacy.py                # archived CSV loaders
src/nba_data/stats_nba.py             # LeagueGameFinder fetch/load
src/nba_data/hoopr.py                 # schedule/neutral/venue enrichment
src/nba_data/bref.py                  # attendance fetch/load
src/nba_data/build.py                 # assemble, validate, publish
src/nba_data/__main__.py              # fetch / inspect-venues / build CLI
scripts/restore_raw.sh                # verified NAS restore
scripts/archive_raw_snapshot.sh       # recoverable NAS cache snapshot
tests/test_franchises.py
tests/test_team_rows.py
tests/test_legacy.py
tests/test_cache.py
tests/test_stats_nba.py
tests/test_hoopr.py
tests/test_bref.py
tests/test_build_gates.py
tests/test_cli.py
docs/data-dictionary.md
data/parquet/team_games.parquet
data/parquet/games.parquet
data/parquet/franchises.parquet
data/parquet/venues.parquet
data/parquet/build_manifest.json
```

### Output contracts

`team_games`:

```text
game_id string, season int64, source string, franchise_id string,
opponent_franchise_id string, is_home bool, game_date date, wl int64,
pts/fga/fgm/fg3a/fg3m/fta/ftm/oreb/dreb/reb/ast/stl/blk/tov/pf nullable Int64
```

`games`:

```text
game_id string, espn_game_id nullable string, season int64, source string,
game_date date, home_franchise_id string, away_franchise_id string,
home_pts int64, away_pts int64, neutral_site bool,
neutral_site_source string, venue_id/name/city nullable string,
venue_source string, attendance_bref/attendance_espn/attendance nullable Int64,
bref_row_matched bool, attendance_status string, attendance_source string
```

`attendance_status` is exactly one of `reported_zero`, `reported_positive`,
`missing`. `attendance_source` is exactly one of `bref`, `espn`, `legacy`.
The broader design reserves `not_applicable`, but this table contains only
completed regular-season games, so Slice 2A never emits it.

Rosters and team-season leaders are independent historical-data products and
remain deferred to Slice 2B. They are not inputs to this game-data build.

---

### Task 0: Installable package and implementation branch

**Files:**
- Create: `pyproject.toml`
- Modify: `requirements.in`
- Modify: `requirements.txt`

**Interfaces:**
- Produces: importable `nba_data` package and registered `integration` pytest marker.

- [x] **Step 1: Verify the clean baseline**

Run:

```bash
git status --short
.venv/bin/python -m pytest -q
```

Expected: clean after this plan is committed; `15 passed`.

- [x] **Step 2: Create the branch**

Run: `git switch -c slice2a-game-data`

- [x] **Step 3: Add the package configuration**

Add `setuptools` to `requirements.in`. Create:

```toml
[build-system]
requires = ["setuptools>=80,<81"]
build-backend = "setuptools.build_meta"

[project]
name = "nba-data"
version = "0.1.0"
requires-python = ">=3.12"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
markers = [
  "integration: reads existing raw caches and never performs network I/O",
]
```

- [x] **Step 4: Regenerate and install**

```bash
/home/riz/.local/bin/uv pip compile --upgrade --python-version 3.12 --universal requirements.in --output-file requirements.txt
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
```

- [x] **Step 5: Verify and commit**

```bash
.venv/bin/python -c "import nba_data; print(nba_data.__file__)"
.venv/bin/python -m pytest -q
git add pyproject.toml requirements.in requirements.txt
git commit -m "build: make nba_data an installable src package"
```

---

### Task 1: Season-aware franchise identity

**Files:**
- Create: `data/reference/franchises.csv`
- Create: `src/nba_data/franchises.py`
- Test: `tests/test_franchises.py`

**Interfaces:**
- Produces: `load_reference() -> pd.DataFrame`;
  `resolve(name: str, season: int) -> str`;
  `UnknownTeamError`; `AmbiguousTeamError`;
  `FRANCHISE_IDS: frozenset[str]`.

- [x] **Step 1: Write the reference**

Create the CSV with these rows:

```csv
alias,franchise_id,first_season,last_season
Atlanta Hawks,ATL,,
Boston Celtics,BOS,,
New Jersey Nets,BKN,,2012
Brooklyn Nets,BKN,2013,
Charlotte Hornets,NOP,,2002
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

- [x] **Step 2: Write the failing tests**

```python
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
```

- [x] **Step 3: Verify failure**

Run: `.venv/bin/python -m pytest tests/test_franchises.py -q`
Expected: FAIL importing `nba_data.franchises`.

- [x] **Step 4: Implement**

```python
"""Season-aware source team names mapped to stable franchise IDs."""
from functools import lru_cache
from pathlib import Path
import pandas as pd

REFERENCE = Path(__file__).resolve().parents[2] / "data/reference/franchises.csv"


class UnknownTeamError(ValueError):
    pass


class AmbiguousTeamError(ValueError):
    pass


@lru_cache(maxsize=1)
def load_reference() -> pd.DataFrame:
    frame = pd.read_csv(REFERENCE, dtype={"alias": str, "franchise_id": str})
    frame["first_season"] = frame["first_season"].astype("Int64")
    frame["last_season"] = frame["last_season"].astype("Int64")
    return frame


FRANCHISE_IDS = frozenset(load_reference()["franchise_id"])


def resolve(name: str, season: int) -> str:
    ref = load_reference()
    rows = ref[
        (ref["alias"] == name.strip())
        & (ref["first_season"].isna() | (ref["first_season"] <= season))
        & (ref["last_season"].isna() | (ref["last_season"] >= season))
    ]
    if len(rows) == 1:
        return str(rows.iloc[0]["franchise_id"])
    if rows.empty:
        raise UnknownTeamError(f"no franchise for {name!r} in season {season}")
    raise AmbiguousTeamError(f"multiple franchise eras for {name!r} in {season}")
```

- [x] **Step 5: Verify and commit**

```bash
.venv/bin/python -m pytest tests/test_franchises.py -q
git add data/reference/franchises.csv src/nba_data/franchises.py tests/test_franchises.py
git commit -m "feat: add season-aware franchise identity"
```

---

### Task 2: Shared NBA Stats row normalizer and legacy restore

**Files:**
- Create: `src/nba_data/team_rows.py`
- Create: `src/nba_data/legacy.py`
- Create: `scripts/restore_raw.sh`
- Test: `tests/test_team_rows.py`
- Test: `tests/test_legacy.py`

**Interfaces:**
- Consumes: `franchises.resolve(name: str, season: int) -> str`.
- Produces: `normalize_team_rows(raw: pd.DataFrame, source: str) -> pd.DataFrame`;
  `legacy.load_team_games(path) -> pd.DataFrame`;
  `legacy.load_attendance(path) -> pd.DataFrame`.

- [x] **Step 1: Write the failing normalizer test**

```python
import pandas as pd
from nba_data.team_rows import normalize_team_rows


def test_normalizes_two_nba_stats_rows():
    raw = pd.DataFrame({
        "SEASON_ID": [22020, 22020],
        "TEAM_NAME": ["Brooklyn Nets", "Golden State Warriors"],
        "GAME_ID": ["0022000001", "0022000001"],
        "GAME_DATE": ["2020-12-22", "2020-12-22"],
        "MATCHUP": ["BKN vs. GSW", "GSW @ BKN"],
        "WL": ["W", "L"],
        **{name: [value, value - 1] for name, value in {
            "PTS": 125, "FGM": 45, "FGA": 90, "FG3M": 15, "FG3A": 40,
            "FTM": 20, "FTA": 25, "OREB": 10, "DREB": 30, "REB": 40,
            "AST": 25, "STL": 7, "BLK": 5, "TOV": 12, "PF": 18,
        }.items()},
    })
    result = normalize_team_rows(raw, source="nba_stats")
    assert len(result) == 2
    assert result["season"].unique().tolist() == [2021]
    assert result[result["is_home"]].iloc[0]["franchise_id"] == "BKN"
    assert result[~result["is_home"]].iloc[0]["opponent_franchise_id"] == "BKN"
    assert result["wl"].tolist() == [1, 0]
```

- [x] **Step 2: Verify failure**

Run: `.venv/bin/python -m pytest tests/test_team_rows.py -q`
Expected: FAIL importing `nba_data.team_rows`.

- [x] **Step 3: Implement the normalizer**

```python
"""Normalize the shared archived/live NBA Stats team-game schema."""
import pandas as pd
from nba_data import franchises

STATS = ("PTS", "FGM", "FGA", "FG3M", "FG3A", "FTM", "FTA",
         "OREB", "DREB", "REB", "AST", "STL", "BLK", "TOV", "PF")


def normalize_team_rows(raw: pd.DataFrame, source: str) -> pd.DataFrame:
    season = (pd.to_numeric(raw["SEASON_ID"]) % 20_000) + 1
    output = pd.DataFrame({
        "game_id": raw["GAME_ID"].astype(str).str.zfill(10),
        "season": season.astype("int64"),
        "source": source,
        "franchise_id": [
            franchises.resolve(name, int(year))
            for name, year in zip(raw["TEAM_NAME"], season)
        ],
        "is_home": raw["MATCHUP"].str.contains(r"\svs\.\s", regex=True),
        "game_date": pd.to_datetime(raw["GAME_DATE"]).dt.date,
        "wl": raw["WL"].replace({"W": 1, "L": 0}).astype("int64"),
    })
    for column in STATS:
        output[column.lower()] = pd.to_numeric(
            raw[column], errors="coerce"
        ).astype("Int64")
    counts = output.groupby("game_id").size()
    if not counts.eq(2).all():
        raise ValueError(f"games without two team rows: {counts[counts.ne(2)].index[:5].tolist()}")
    opponents = output[["game_id", "franchise_id"]].rename(
        columns={"franchise_id": "opponent_franchise_id"}
    )
    output = output.merge(opponents, on="game_id", how="left")
    output = output[
        output["franchise_id"] != output["opponent_franchise_id"]
    ].reset_index(drop=True)
    if len(output) != len(raw):
        raise ValueError("opponent pairing did not preserve row count")
    return output
```

- [x] **Step 4: Add the exact raw-snapshot restore**

The shipped `scripts/restore_raw.sh` is the inverse of
`scripts/archive_raw_snapshot.sh`, not a legacy-only bootstrap. It derives the
snapshot name from the committed manifest's ending season and SHA-256,
requires the archived manifest to match, verifies the complete `SHA256SUMS`
inventory, stages the whole raw tree, and refuses to replace an existing
`data/raw/`. This restores NBA Stats, hoopR, b-ref schedules, the two legacy
CSVs, and all 23 historical reconciliation body/sidecar pairs.

- [x] **Step 5: Write the failing legacy tests**

```python
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
```

- [x] **Step 6: Implement the legacy loaders**

```python
"""Load the verified archived NBA Stats CSVs."""
import pandas as pd
from nba_data.team_rows import normalize_team_rows


def load_team_games(path) -> pd.DataFrame:
    raw = pd.read_csv(path, index_col=0, dtype={"GAME_ID": str})
    return normalize_team_rows(raw, source="nba_stats")


def load_attendance(path) -> pd.DataFrame:
    raw = pd.read_csv(path, index_col=0, dtype={"GAME_ID": str})
    attendance = pd.to_numeric(raw["ATTENDANCE"], errors="coerce").mask(
        lambda values: values <= 0
    )
    return pd.DataFrame({
        "game_id": raw["GAME_ID"].str.zfill(10),
        "attendance": attendance.astype("Int64"),
    })
```

- [x] **Step 7: Verify and commit**

```bash
.venv/bin/python -m pytest \
  tests/test_team_rows.py \
  tests/test_legacy.py \
  tests/test_repository_layout.py -q
git add src/nba_data/team_rows.py src/nba_data/legacy.py \
  scripts/restore_raw.sh tests/test_team_rows.py tests/test_legacy.py \
  tests/test_repository_layout.py
git commit -m "feat: normalize and restore verified NBA data"
```

---

### Task 3: Checksummed raw cache

**Files:**
- Create: `src/nba_data/cache.py`
- Test: `tests/test_cache.py`

**Interfaces:**
- Produces: `CacheIntegrityError`; `SourceAccessError`;
  `sha256_file(path: Path) -> str`;
  `read_valid(path: Path, validator: Callable[[Path], None]) -> bytes | None`;
  `publish_bytes(path: Path, payload: bytes, metadata: dict, validator) -> Path`.

- [x] **Step 1: Write the failing tests**

```python
def test_publish_and_read_checksummed_cache(tmp_path):
    path = tmp_path / "response.json"
    cache.publish_bytes(path, b'{"ok": true}', {"provider": "test"}, lambda _: None)
    assert cache.read_valid(path, lambda _: None) == b'{"ok": true}'
    meta = json.loads((tmp_path / "response.json.meta.json").read_text())
    assert meta["sha256"] == hashlib.sha256(b'{"ok": true}').hexdigest()


def test_changed_cache_fails_without_repair(tmp_path):
    path = tmp_path / "response.json"
    cache.publish_bytes(path, b"original", {}, lambda _: None)
    path.write_bytes(b"changed")
    with pytest.raises(cache.CacheIntegrityError, match="SHA-256"):
        cache.read_valid(path, lambda _: None)
```

- [x] **Step 2: Verify failure**

Run: `.venv/bin/python -m pytest tests/test_cache.py -q`
Expected: FAIL importing `nba_data.cache`.

- [x] **Step 3: Implement**

```python
"""Atomic body/metadata cache with SHA-256 verification."""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


class CacheIntegrityError(RuntimeError):
    pass


class SourceAccessError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_valid(path: Path, validator) -> bytes | None:
    sidecar = path.with_name(path.name + ".meta.json")
    if not path.exists() and not sidecar.exists():
        return None
    if not path.exists() or not sidecar.exists():
        raise CacheIntegrityError(f"incomplete cache pair for {path}")
    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    actual = sha256_file(path)
    if actual != metadata.get("sha256"):
        raise CacheIntegrityError(f"SHA-256 mismatch for {path}")
    validator(path)
    return path.read_bytes()


def publish_bytes(path: Path, payload: bytes, metadata: dict, validator) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    body_part = path.with_name(path.name + ".part")
    meta_path = path.with_name(path.name + ".meta.json")
    meta_part = meta_path.with_name(meta_path.name + ".part")
    body_part.write_bytes(payload)
    validator(body_part)
    record = {
        **metadata,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "content_length": len(payload),
        "sha256": sha256_file(body_part),
    }
    meta_part.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    os.replace(body_part, path)
    os.replace(meta_part, meta_path)
    return path
```

- [x] **Step 4: Verify and commit**

```bash
.venv/bin/python -m pytest tests/test_cache.py -q
git add src/nba_data/cache.py tests/test_cache.py
git commit -m "feat: add checksummed raw source cache"
```

---

### Task 4: NBA Stats canonical modern box scores

**Files:**
- Create: `src/nba_data/stats_nba.py`
- Test: `tests/test_stats_nba.py`

**Interfaces:**
- Consumes: `cache.read_valid`, `cache.publish_bytes`,
  `team_rows.normalize_team_rows`.
- Produces: `SEASONS: dict[int, str]`;
  `preflight_dns() -> tuple[str, ...]`;
  `fetch(raw_dir: Path, seasons: Iterable[int], sleep_s: float = 12) -> None`;
  `load_season(raw_dir: Path, season: int, expected_games: int | None = None) -> pd.DataFrame`.

- [x] **Step 1: Write the failing DNS test**

```python
def test_dns_preflight_rejects_adguard_sinkhole(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("0.0.0.0", 443))
    ])
    with pytest.raises(stats_nba.SourceAccessError, match="AdGuard"):
        stats_nba.preflight_dns()
```

- [x] **Step 2: Write the failing payload test**

```python
def fixture_leaguegamefinder_payload():
    headers = [
        "SEASON_ID", "TEAM_ID", "TEAM_NAME", "GAME_ID", "GAME_DATE",
        "MATCHUP", "WL", "PTS", "FGM", "FGA", "FG3M", "FG3A", "FTM",
        "FTA", "OREB", "DREB", "REB", "AST", "STL", "BLK", "TOV", "PF",
    ]
    home = {
        "SEASON_ID": "22020", "TEAM_ID": 1610612751,
        "TEAM_NAME": "Brooklyn Nets", "GAME_ID": "0022000001",
        "GAME_DATE": "2020-12-22", "MATCHUP": "BKN vs. GSW", "WL": "W",
        "PTS": 125, "FGM": 45, "FGA": 90, "FG3M": 15, "FG3A": 40,
        "FTM": 20, "FTA": 25, "OREB": 10, "DREB": 30, "REB": 40,
        "AST": 25, "STL": 7, "BLK": 5, "TOV": 12, "PF": 18,
    }
    away = {
        **home, "TEAM_ID": 1610612744, "TEAM_NAME": "Golden State Warriors",
        "MATCHUP": "GSW @ BKN", "WL": "L", "PTS": 99,
    }
    return {
        "resource": "leaguegamefinder",
        "parameters": {},
        "resultSets": [{
            "name": "LeagueGameFinderResults",
            "headers": headers,
            "rowSet": [[row[column] for column in headers] for row in [home, away]],
        }],
    }


def test_load_season_normalizes_cached_leaguegamefinder(tmp_path):
    destination = tmp_path / "stats_nba/leaguegamefinder_2021.json"
    payload = fixture_leaguegamefinder_payload()  # two rows from Task 2 sample
    cache.publish_bytes(
        destination,
        json.dumps(payload).encode(),
        {"provider": "stats_nba", "season": 2021},
        stats_nba.validate_payload,
    )
    result = stats_nba.load_season(tmp_path, 2021, expected_games=1)
    assert len(result) == 2
    assert result.iloc[0]["game_id"] == "0022000001"
    assert set(result["source"]) == {"nba_stats"}
```

- [x] **Step 3: Verify failure**

Run: `.venv/bin/python -m pytest tests/test_stats_nba.py -q`
Expected: FAIL importing `nba_data.stats_nba`.

- [x] **Step 4: Implement DNS and payload validation**

```python
SEASONS = {
    2020: "2019-20", 2021: "2020-21", 2022: "2021-22",
    2023: "2022-23", 2024: "2023-24", 2025: "2024-25", 2026: "2025-26",
}
EXPECTED_GAMES = {
    2020: 1059, 2021: 1080, 2022: 1230, 2023: 1230,
    2024: 1230, 2025: 1230, 2026: 1230,
}
REQUIRED = {
    "SEASON_ID", "TEAM_ID", "TEAM_NAME", "GAME_ID", "GAME_DATE", "MATCHUP",
    "WL", "PTS", "FGM", "FGA", "FG3M", "FG3A", "FTM", "FTA", "OREB",
    "DREB", "REB", "AST", "STL", "BLK", "TOV", "PF",
}


def preflight_dns() -> tuple[str, ...]:
    addresses = tuple(sorted({
        result[4][0] for result in socket.getaddrinfo(
            "stats.nba.com", 443, type=socket.SOCK_STREAM
        )
    }))
    bad = [
        address for address in addresses
        if ipaddress.ip_address(address).is_unspecified
        or ipaddress.ip_address(address).is_loopback
    ]
    if not addresses or bad:
        raise cache.SourceAccessError(
            f"stats.nba.com resolved to {addresses}; check the AdGuard query log "
            "and allowlist @@||stats.nba.com^"
        )
    return addresses


def validate_payload(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    result = payload["resultSets"][0]
    if result["name"] != "LeagueGameFinderResults":
        raise ValueError(f"unexpected result set {result['name']!r}")
    missing = REQUIRED - set(result["headers"])
    if missing:
        raise ValueError(f"missing LeagueGameFinder columns: {sorted(missing)}")
```

- [x] **Step 5: Implement fetch and load**

```python
def fetch(raw_dir: Path, seasons=SEASONS, sleep_s: float = 12.0) -> None:
    preflight_dns()
    requested = list(seasons)
    for index, season in enumerate(requested):
        destination = raw_dir / "stats_nba" / f"leaguegamefinder_{season}.json"
        if cache.read_valid(destination, validate_payload) is not None:
            continue
        try:
            endpoint = LeagueGameFinder(
                player_or_team_abbreviation="T",
                league_id_nullable="00",
                season_nullable=SEASONS[season],
                season_type_nullable="Regular Season",
                timeout=90,
            )
        except requests.RequestException as exc:
            raise cache.SourceAccessError(
                f"LeagueGameFinder {season} failed: {type(exc).__name__}: {exc}"
            ) from exc
        payload = endpoint.get_json().encode("utf-8")
        cache.publish_bytes(
            destination, payload,
            {
                "provider": "stats_nba",
                "endpoint": "LeagueGameFinder",
                "season": season,
                "season_label": SEASONS[season],
            },
            validate_payload,
        )
        if index < len(requested) - 1:
            time.sleep(sleep_s)


def load_season(
    raw_dir: Path, season: int, expected_games: int | None = None
) -> pd.DataFrame:
    path = raw_dir / "stats_nba" / f"leaguegamefinder_{season}.json"
    cache.read_valid(path, validate_payload)
    result = json.loads(path.read_text())["resultSets"][0]
    raw = pd.DataFrame(result["rowSet"], columns=result["headers"])
    output = normalize_team_rows(raw, source="nba_stats")
    games = output["game_id"].nunique()
    expected = EXPECTED_GAMES[season] if expected_games is None else expected_games
    if games != expected:
        raise ValueError(f"season {season}: {games} games, expected {expected}")
    if not output["game_id"].str.startswith("002").all():
        raise ValueError(f"season {season}: non-regular NBA GAME_ID found")
    return output
```

- [x] **Step 6: Add cached integration tests**

```python
@pytest.mark.integration
@pytest.mark.parametrize("season, games", sorted(stats_nba.EXPECTED_GAMES.items()))
def test_cached_season_counts(season, games):
    path = RAW / "stats_nba" / f"leaguegamefinder_{season}.json"
    if not path.exists():
        pytest.skip("NBA Stats cache absent")
    result = stats_nba.load_season(RAW, season)
    assert len(result) == games * 2
    assert result["game_id"].nunique() == games
```

- [x] **Step 7: Verify and commit**

```bash
.venv/bin/python -m pytest tests/test_stats_nba.py -q
git add src/nba_data/stats_nba.py tests/test_stats_nba.py
git commit -m "feat: fetch canonical modern NBA Stats box scores"
```

---

### Task 5: Pinned hoopR schedule enrichment

**Files:**
- Create: `data/reference/hoopr_snapshot.json`
- Create: `src/nba_data/hoopr.py`
- Test: `tests/test_hoopr.py`

**Interfaces:**
- Produces: `fetch(raw_dir: Path, seasons) -> None`;
  `load_season(raw_dir: Path, season: int, expected_games: int | None = None)
  -> tuple[pd.DataFrame, pd.DataFrame]`
  returning schedule rows and exclusion rows.

- [x] **Step 1: Create the snapshot**

```json
{
  "repository": "sportsdataverse/hoopR-nba-data",
  "commit": "50fe7e429c6b5bcbe62e7a56d19afe5a80e6e143",
  "seasons": [2020, 2021, 2022, 2023, 2024, 2025, 2026]
}
```

- [x] **Step 2: Write the failing fixture test**

Construct a temporary schedule parquet containing two completed NBA games, an
All-Star row, an incomplete game, and a real-franchise row with
`type_abbreviation="CC"`.

```python
schedule, excluded = hoopr.load_season(tmp_path, 2024, expected_games=2)
assert len(schedule) == 2
assert set(excluded["reason"]) == {"exhibition", "cup_championship"}
assert schedule["espn_game_id"].str.startswith("espn-").all()
assert schedule["venue_id"].str.startswith("espn-").all()
assert schedule["espn_attendance"].dtype == "Int64"
```

- [x] **Step 3: Verify failure**

Run: `.venv/bin/python -m pytest tests/test_hoopr.py -q`
Expected: FAIL importing `nba_data.hoopr`.

- [x] **Step 4: Implement fetch**

Use Task 3 around a single schedule URL per season:

```python
if cache.read_valid(destination, lambda path: pq.ParquetFile(path)) is not None:
    continue
url = (
    "https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-data/"
    f"{SNAPSHOT['commit']}/nba/schedules/parquet/nba_schedule_{season}.parquet"
)
response = session.get(url, timeout=120, verify=True)
if response.status_code != 200:
    raise cache.SourceAccessError(f"{url} returned HTTP {response.status_code}")
cache.publish_bytes(
    destination,
    response.content,
    {"provider": "hoopr", "upstream_commit": SNAPSHOT["commit"], "season": season},
    lambda path: pq.ParquetFile(path),
)
```

Catch `requests.RequestException` and wrap its exact type/URL in
`SourceAccessError`; do not retry.

- [x] **Step 5: Implement normalization**

Filter completed `season_type == 2`. Classify `ALLSTAR` and exact
case-normalized `RISING STARS` as exhibition; classify `CC` as Cup
championship. Resolve every remaining team name, raising on an unknown
ordinary entrant. Exclude the classified rows and require the hard season
count (`expected_games` overrides the production map only for fixture tests).
Return:

```python
pd.DataFrame({
    "espn_game_id": "espn-" + retained["game_id"].astype(str),
    "season": season,
    "game_date": retained["game_date"],
    "home_franchise_id": home_ids,
    "away_franchise_id": away_ids,
    "neutral_site": retained["neutral_site"].astype(bool),
    "venue_id": retained["venue_id"].map(
        lambda value: pd.NA if pd.isna(value) else f"espn-{int(value)}"
    ),
    "venue_name_espn": retained["venue_full_name"].astype("string"),
    "venue_city": retained["venue_address_city"].astype("string"),
    "espn_attendance": pd.to_numeric(
        retained["attendance"], errors="coerce"
    ).astype("Int64"),
})
```

- [x] **Step 6: Verify and commit**

```bash
.venv/bin/python -m pytest tests/test_hoopr.py -q
git add data/reference/hoopr_snapshot.json src/nba_data/hoopr.py tests/test_hoopr.py
git commit -m "feat: add pinned hoopR schedule enrichment"
```

---

### Task 6: basketball-reference attendance

**Files:**
- Create: `src/nba_data/bref.py`
- Test: `tests/test_bref.py`

**Interfaces:**
- Produces: `MONTHS_BY_SEASON`;
  `fetch(raw_dir: Path, seasons, sleep_s=12) -> None`;
  `load_attendance(raw_dir: Path, season: int, months=None) -> pd.DataFrame`.

- [x] **Step 1: Write the failing parser test**

Use a 12-column HTML fixture with explicit zero, positive, blank, and
postponed rows:

```python
result = bref.load_attendance(tmp_path, 2021, months=["december"])
assert len(result) == 3
assert result["attendance"].tolist()[:2] == [0, 300]
assert pd.isna(result.iloc[2]["attendance"])
assert result["game_date"].map(type).eq(datetime.date).all()
```

- [x] **Step 2: Verify failure**

Run: `.venv/bin/python -m pytest tests/test_bref.py -q`
Expected: FAIL importing `nba_data.bref`.

- [x] **Step 3: Implement month and schema contracts**

```python
STANDARD = ("october", "november", "december", "january",
            "february", "march", "april")
MONTHS_BY_SEASON = {
    2020: ("october", "november", "december", "january",
           "february", "march", "july", "august"),
    2021: ("december", "january", "february", "march", "april", "may"),
    **{season: STANDARD for season in range(2022, 2027)},
}
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
```

Parse exactly one table with `id="schedule"` and exactly 12 columns. Assign
`date,start,away,away_pts,home,home_pts,box,ot,attendance,duration,arena,notes`.
Keep rows where both scores parse; parse attendance with `errors="coerce"` and
never call `fillna(0)`.

- [x] **Step 4: Implement polite fetch**

For each missing page, call `requests.Session.get(url, timeout=30,
verify=True)`, require HTTP 200, validate the table, then publish through Task
3. Sleep 12 seconds after a real request before another real request. A valid
cache hit performs neither network nor sleep.

- [x] **Step 5: Verify the real spike and commit**

```python
@pytest.mark.integration
def test_spike_december_2020_semantics():
    result = bref.load_attendance(RAW / "spike", 2021, ["december"])
    assert len(result) == 67
    assert result["attendance"].eq(0).sum() == 56
    assert result["attendance"].gt(0).sum() == 11
```

```bash
.venv/bin/python -m pytest tests/test_bref.py -q
git add src/nba_data/bref.py tests/test_bref.py
git commit -m "feat: add cached basketball-reference attendance"
```

---

### Task 7: Assembly and hard validation gates

**Files:**
- Create: `src/nba_data/build.py`
- Test: `tests/test_build_gates.py`

**Interfaces:**
- Consumes: all provider loaders.
- Produces: `pair_team_games(team_games) -> pd.DataFrame`;
  `enrich_modern_games(nba_games, schedule, bref) -> pd.DataFrame`;
  `assemble(raw_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict]`;
  `validate(team_games, games, expected_counts, expected_legacy) -> dict`;
  `write_venue_inventory(data_dir: Path) -> Path`;
  `ValidationError`.

- [x] **Step 1: Write the failing canonical crosswalk test**

```python
def one_nba_game(game_id="0022000001"):
    return pd.DataFrame([{
        "game_id": game_id, "season": 2021, "source": "nba_stats",
        "game_date": datetime.date(2020, 12, 22),
        "home_franchise_id": "BKN", "away_franchise_id": "GSW",
        "home_pts": 125, "away_pts": 99,
    }])


def one_schedule_game(espn_game_id="espn-401300001", espn_attendance=0):
    return pd.DataFrame([{
        "espn_game_id": espn_game_id, "season": 2021,
        "game_date": datetime.date(2020, 12, 22),
        "home_franchise_id": "BKN", "away_franchise_id": "GSW",
        "neutral_site": False, "venue_id": "espn-17",
        "venue_name_espn": "Barclays Center", "venue_city": "Brooklyn",
        "espn_attendance": espn_attendance,
    }])


def one_bref_game(attendance=0):
    return pd.DataFrame([{
        "season": 2021, "game_date": datetime.date(2020, 12, 22),
        "home_franchise_id": "BKN", "away_franchise_id": "GSW",
        "attendance": attendance, "arena": "Barclays Center",
    }])


def test_nba_id_remains_canonical_after_espn_join():
    nba = one_nba_game(game_id="0022000001")
    schedule = one_schedule_game(espn_game_id="espn-401300001")
    result = build.enrich_modern_games(nba, schedule, bref=pd.DataFrame())
    assert result.iloc[0]["game_id"] == "0022000001"
    assert result.iloc[0]["espn_game_id"] == "espn-401300001"
```

- [x] **Step 2: Write failing attendance precedence tests**

```python
def test_matched_bref_blank_does_not_fall_back_to_espn():
    nba = one_nba_game()
    schedule = one_schedule_game(espn_attendance=1500)
    bref = one_bref_game(attendance=pd.NA)
    game = build.enrich_modern_games(nba, schedule, bref).iloc[0]
    assert game["bref_row_matched"]
    assert pd.isna(game["attendance"])
    assert game["attendance_status"] == "missing"
    assert game["attendance_source"] == "bref"


def test_unmatched_bref_uses_espn():
    game = build.enrich_modern_games(
        one_nba_game(), one_schedule_game(espn_attendance=1500), pd.DataFrame()
    ).iloc[0]
    assert game["attendance"] == 1500
    assert game["attendance_source"] == "espn"
```

- [x] **Step 3: Write one failing test per gate**

Tests must seed and assert diagnostics for:

```text
duplicate game_id
games/team_games ID-set mismatch
not exactly two team rows or not one home/one away
team IDs/scores/date/season disagree with games
unknown franchise
missing expected season
wrong per-season or legacy count
score outside 40..200 or tie
missing box statistic
reported_zero outside 2020..2022
b-ref join coverage below 95% in any modern season
selected known-attendance coverage below 95% in any modern season
non-neutral 2020..2022 venue missing capacity
```

Each unit test passes a one-season `expected_counts` mapping and
`expected_legacy=0`; the production call passes all seven seasons and 40,828.

- [x] **Step 4: Verify failure**

Run: `.venv/bin/python -m pytest tests/test_build_gates.py -q`
Expected: FAIL importing `nba_data.build`.

- [x] **Step 5: Implement pairing and crosswalk**

Pair home/away NBA rows by `game_id`; reject non-unique sides before
constructing games:

```python
def pair_team_games(team_games: pd.DataFrame) -> pd.DataFrame:
    counts = team_games.groupby("game_id")["is_home"].agg(["size", "sum"])
    bad = counts[(counts["size"] != 2) | (counts["sum"] != 1)]
    if not bad.empty:
        raise ValidationError(
            f"games without one home and one away row: {bad.index[:5].tolist()}"
        )
    home = team_games[team_games["is_home"]].set_index("game_id")
    away = team_games[~team_games["is_home"]].set_index("game_id")
    away = away.reindex(home.index)
    return pd.DataFrame({
        "game_id": home.index,
        "season": home["season"].to_numpy(),
        "source": home["source"].to_numpy(),
        "game_date": home["game_date"].to_numpy(),
        "home_franchise_id": home["franchise_id"].to_numpy(),
        "away_franchise_id": away["franchise_id"].to_numpy(),
        "home_pts": home["pts"].to_numpy(),
        "away_pts": away["pts"].to_numpy(),
    }).reset_index(drop=True)
```

Join modern games to hoopR with:

```python
JOIN_KEY = [
    "season", "game_date", "home_franchise_id", "away_franchise_id"
]
enriched = nba_games.merge(
    schedule,
    on=JOIN_KEY,
    how="left",
    validate="one_to_one",
    indicator="schedule_join",
)
missing = enriched["schedule_join"].ne("both")
if missing.any():
    raise ValidationError(
        f"NBA games without hoopR schedule: "
        f"{enriched.loc[missing, 'game_id'].head().tolist()}"
    )
```

If b-ref is empty, replace it with an empty frame containing `JOIN_KEY +
["attendance", "arena"]`. Rename b-ref `attendance -> attendance_bref` and
`arena -> venue_name_bref`, then join with the same key,
`validate="one_to_one"`, and a second indicator.
Select b-ref based on the indicator—not `attendance.notna()`:

```python
matched = enriched["bref_join"].eq("both")
enriched["bref_row_matched"] = matched
enriched["attendance"] = enriched["attendance_bref"].where(
    matched, enriched["attendance_espn"]
).astype("Int64")
enriched["attendance_source"] = matched.map({True: "bref", False: "espn"})
enriched["attendance_status"] = enriched["attendance"].map(
    lambda value: "missing" if pd.isna(value)
    else ("reported_zero" if value == 0 else "reported_positive")
)
```

When b-ref matches and its arena is nonblank, use it as `venue_name` and set
`venue_source="bref"`; retain the ESPN `venue_id` and city. Otherwise use the
ESPN venue name and `venue_source="espn"`.

For legacy games, set:

```python
legacy_games = legacy_games.assign(
    espn_game_id=pd.NA,
    neutral_site=False,
    neutral_site_source="legacy_home_designation_assumed",
    venue_id=pd.NA,
    venue_name=pd.NA,
    venue_city=pd.NA,
    venue_source="legacy_unknown",
    attendance_bref=pd.NA,
    attendance_espn=pd.NA,
    bref_row_matched=False,
    attendance_source="legacy",
)
legacy_games["attendance_status"] = legacy_games["attendance"].map(
    lambda value: "missing" if pd.isna(value) else "reported_positive"
)
```

- [x] **Step 6: Implement validation report**

Accumulate every gate failure and raise one `ValidationError("; ".join(errors))`
before writing. Return per-season counts for games/team rows, b-ref join
coverage, zero/positive/missing attendance, selected coverage, and b-ref/ESPN
agreement. Require full production totals 49,117 and 98,234.

- [x] **Step 7: Implement the venue inventory writer**

```python
def write_venue_inventory(data_dir: Path) -> Path:
    _, games, _ = assemble(data_dir / "raw")
    post = games[games["venue_id"].notna()].copy()
    normal = post[
        ~post["neutral_site"]
        & ~post["season"].isin([2020, 2021])
        & post["attendance"].gt(0)
    ]
    observed = normal.groupby(
        ["venue_id", "venue_name", "venue_city", "home_franchise_id"],
        dropna=False,
    )["attendance"].agg(
        observed_non_covid_max="max",
        observed_non_covid_p99=lambda values: int(values.quantile(0.99)),
    )
    eras = post.groupby(
        ["venue_id", "venue_name", "venue_city", "home_franchise_id"],
        dropna=False,
    )["season"].agg(first_season="min", last_season="max", games="size")
    output = eras.join(observed, how="left").reset_index()
    destination = data_dir / "raw/reports/venue_inventory.csv"
    destination.parent.mkdir(parents=True, exist_ok=True)
    output.sort_values(
        ["home_franchise_id", "first_season", "venue_id"]
    ).to_csv(destination, index=False)
    return destination
```

- [x] **Step 8: Verify and commit**

```bash
.venv/bin/python -m pytest tests/test_build_gates.py -q
git add src/nba_data/build.py tests/test_build_gates.py
git commit -m "feat: assemble and validate the canonical game panel"
```

---

### Task 8: CLI, live fetch, and cached integration verification

**Files:**
- Create: `src/nba_data/__main__.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `python -m nba_data fetch [--season ENDING_YEAR]` and
  `python -m nba_data inspect-venues`. Task 10 adds `build` after publication
  exists.

- [x] **Step 1: Write failing CLI tests**

Monkeypatch provider functions and assert `fetch` calls NBA Stats, hoopR, then
b-ref; `inspect-venues` calls no fetch function; invalid seasons exit before
network.

- [x] **Step 2: Implement argparse dispatch**

```python
def main() -> None:
    root = Path(__file__).resolve().parents[2]
    data = root / "data"
    raw = data / "raw"
    parser = argparse.ArgumentParser(prog="nba_data")
    commands = parser.add_subparsers(dest="command", required=True)
    fetch_parser = commands.add_parser("fetch")
    fetch_parser.add_argument("--season", type=int, choices=range(2020, 2027))
    commands.add_parser("inspect-venues")
    args = parser.parse_args()
    seasons = [args.season] if getattr(args, "season", None) else range(2020, 2027)
    if args.command == "fetch":
        stats_nba.fetch(raw, seasons)
        hoopr.fetch(raw, seasons)
        bref.fetch(raw, seasons)
    elif args.command == "inspect-venues":
        build.write_venue_inventory(data)
```

- [x] **Step 3: Verify offline behavior and commit**

```bash
.venv/bin/python -m pytest tests/test_cli.py -q
.venv/bin/python -m nba_data --help
git add src/nba_data/__main__.py tests/test_cli.py
git commit -m "feat: add explicit fetch inspect and build commands"
```

- [x] **Step 4: Fetch all seven seasons**

Run: `.venv/bin/python -m nba_data fetch`

Expected:

```text
7 NBA Stats JSON files
7 pinned hoopR schedule parquet files
49 basketball-reference HTML pages
one .meta.json sidecar per raw body
```

On any failure, stop and report the provider/URL/exception. Do not change
provider, TLS settings, or request cadence.

- [x] **Step 5: Run cached integration tests**

```bash
.venv/bin/python -m pytest -m integration -q -rs
```

Expected: no skipped integration tests; all seven NBA Stats counts and all
provider fixtures pass.

---

### Task 9: Venue-capacity reference

**Files:**
- Create: `data/reference/venue_capacities.csv`
- Modify: `src/nba_data/build.py`
- Test: extend `tests/test_build_gates.py`

**Interfaces:**
- Produces: `venue_inventory(games) -> pd.DataFrame`;
  `load_venue_capacities() -> pd.DataFrame`;
  final `venues` contract.

- [x] **Step 1: Generate the inventory**

Run: `.venv/bin/python -m nba_data inspect-venues`

Expected file:
`data/raw/reports/venue_inventory.csv` with venue ID/name/city, franchise,
first/last season, games, non-COVID max attendance, and non-COVID p99.

- [x] **Step 2: Create the capacity reference**

Use this exact header:

```csv
venue_id,franchise_id,first_season,last_season,capacity,capacity_source_url,capacity_note
```

For each non-neutral venue/team era appearing in 2020–2022, record basketball
capacity effective for that era and a direct source URL. Split season ranges
when configuration changed. Do not infer capacity solely from observed
attendance. Neutral bubble venues may be absent.

- [x] **Step 3: Add the failing coverage test**

```python
def test_primary_window_non_neutral_venues_have_capacity():
    primary = pd.DataFrame([{
        "game_id": "0022000001", "season": 2021, "venue_id": "espn-17",
        "home_franchise_id": "BKN", "neutral_site": False,
        "attendance": 0,
    }])
    venues = pd.DataFrame([{
        "venue_id": "espn-17", "franchise_id": "BKN",
        "first_season": 2020, "last_season": 2022, "capacity": 17732,
        "capacity_source_url": "https://example.test/barclays-capacity",
        "capacity_note": pd.NA,
    }])
    joined = build.attach_capacity(primary, venues)
    assert joined["capacity"].notna().all()
    assert joined["capacity_source_url"].str.startswith(("http://", "https://")).all()
    assert (joined["capacity"] > 0).all()
```

- [x] **Step 4: Implement the season-aware join**

Join on `venue_id + home_franchise_id`, then filter inclusive
`first_season <= season <= last_season`. Raise if a game resolves zero or
multiple capacity rows. Report positive attendance above 105% of capacity;
allow it only when `capacity_note` documents the configuration exception.

- [x] **Step 5: Verify and commit**

```bash
.venv/bin/python -m pytest tests/test_build_gates.py -q
git add data/reference/venue_capacities.csv src/nba_data/build.py tests/test_build_gates.py
git commit -m "data: add provenance-backed venue capacities"
```

---

### Task 10: Staged Parquet publication and build manifest

**Files:**
- Modify: `src/nba_data/build.py`
- Modify: `src/nba_data/__main__.py`
- Create: `data/parquet/team_games.parquet`
- Create: `data/parquet/games.parquet`
- Create: `data/parquet/franchises.parquet`
- Create: `data/parquet/venues.parquet`
- Create: `data/parquet/build_manifest.json`
- Test: extend `tests/test_build_gates.py`
- Test: extend `tests/test_cli.py`

**Interfaces:**
- Produces: `run(data_dir: Path) -> None`; deterministic five-file publication.

- [x] **Step 1: Write the failing publication test**

In a temporary data tree, retain sentinel output files, seed a validation
failure, call `run`, and assert the sentinels remain byte-identical. Then seed
passing fixtures and assert four parquet files plus the manifest are created.

- [x] **Step 2: Implement staging**

Build frames in memory, coerce exact column order/dtypes, sort deterministically,
and call the full validator. Write to a temporary sibling directory, re-read
all Parquet files, and repeat schema/row-count checks before replacing final
files. Preserve prior final files in a rollback directory until all five new
files are published.

- [x] **Step 3: Implement the deterministic manifest**

Write sorted-key JSON with:

```text
schema_version
NBA Stats endpoint/season labels and raw SHA-256 values
hoopR repository/commit and raw SHA-256 values
b-ref page paths/URLs and raw SHA-256 values
reference-file SHA-256 values
Python/direct dependency versions
table row counts and season bounds
validation/coverage/exclusion report
Parquet SHA-256 values
```

Exclude build time, absolute paths, random IDs, and cache fetch timestamps.

- [x] **Step 4: Build and verify determinism**

Before running the build, add the final parser branch:

```python
commands.add_parser("build")
# after parsing:
elif args.command == "build":
    build.run(data)
```

```bash
.venv/bin/python -m nba_data build
du -sh data/parquet
sha256sum data/parquet/* > /tmp/nba-parquet-before.sha256
.venv/bin/python -m nba_data build
sha256sum data/parquet/* > /tmp/nba-parquet-after.sha256
cmp /tmp/nba-parquet-before.sha256 /tmp/nba-parquet-after.sha256
```

Expected: 49,117 games, 98,234 team rows, single-digit MiB, identical hashes.

- [x] **Step 5: Run all verification and commit**

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/verify_repository.py
git add src/nba_data/build.py src/nba_data/__main__.py tests/test_build_gates.py tests/test_cli.py data/parquet
git commit -m "data: publish validated NBA game panel through 2025-26"
```

---

### Task 11: Data dictionary and recoverable raw snapshot

**Files:**
- Modify: `README.md`
- Create: `docs/data-dictionary.md`
- Create: `scripts/archive_raw_snapshot.sh`
- Modify: `tests/test_repository_layout.py`

**Interfaces:**
- Produces: user-facing source/field documentation and verified NAS recovery copy.

- [x] **Step 1: Document the final contract**

Document every table/column/type, ending-year seasons, canonical NBA versus
ESPN IDs, franchise lineage, neutral-site semantics, attendance precedence,
blank-versus-zero behavior, capacity provenance, all source pins/hashes, and
all three CLI commands. Link the design, corrected source audit, and manifest.
State that the original failure was an AdGuard DNS sinkhole and document the
preflight diagnostic without suggesting TLS bypass.

- [x] **Step 2: Write the raw archive script**

```bash
#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
manifest="$root/data/parquet/build_manifest.json"
test -f "$manifest" || {
  printf 'missing build manifest: %s\n' "$manifest" >&2
  exit 1
}
ending_season="$(jq -er '.tables.games.max_season' "$manifest")"
manifest_sha="$(sha256sum "$manifest" | cut -d' ' -f1)"
archive_root=/mnt/nas/llm/documents/nba-predictions-archive
destination="$archive_root/slice2a-raw-${ending_season}-${manifest_sha:0:12}"
test ! -e "$destination" || {
  printf 'refusing to overwrite existing snapshot: %s\n' "$destination" >&2
  exit 1
}

mkdir -p "$destination/raw"
cp "$manifest" "$destination/build_manifest.json"
rsync -a --checksum "$root/data/raw/" "$destination/raw/"
(
  cd "$destination"
  find raw -type f -print0 \
    | LC_ALL=C sort -z \
    | xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)

changes="$(
  rsync -a --checksum --dry-run --itemize-changes \
    "$root/data/raw/" "$destination/raw/"
)"
test -z "$changes" || {
  printf 'archive differs from source:\n%s\n' "$changes" >&2
  exit 1
}
printf 'verified raw snapshot: %s\n' "$destination"
```

- [x] **Step 3: Add layout assertions**

Assert the data dictionary, manifest, four Parquet files, and archive script
exist; assert no path under `data/raw/` is tracked; assert the manifest has no
absolute `/home/` paths or `/main/` hoopR URL.

- [x] **Step 4: Verify, archive, and commit**

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/verify_repository.py
bash scripts/archive_raw_snapshot.sh
git add README.md docs/data-dictionary.md scripts/archive_raw_snapshot.sh tests/test_repository_layout.py
git commit -m "docs: document and preserve the game-data pipeline"
```

---

## Final Verification

- [x] `git status --short` is clean.
- [x] `.venv/bin/python -m pytest -q` passes with no skipped integration tests.
- [x] `.venv/bin/python scripts/verify_repository.py` exits zero.
- [x] `games` has 49,117 rows and `team_games` has 98,234.
- [x] Each modern season has its exact required game count and every game has two team rows.
- [x] Every modern NBA game joins exactly one hoopR schedule row.
- [x] b-ref row-join coverage is at least 95% per modern season.
- [x] Selected known-attendance coverage is at least 95% after removing only
  the 87 enumerated missing-attendance 2020 no-fan bubble games; raw numeric
  coverage, eligible denominator, and exemption count remain separately
  reported in the manifest.
- [x] Blank b-ref attendance remains missing and explicit zero is never inferred.
- [x] Every non-neutral primary-window venue resolves one provenance-backed capacity.
- [x] The manifest and second build have identical hashes.
- [x] No raw file, absolute workstation path, mutable hoopR URL, or TLS bypass is tracked.
- [x] The verified raw-cache snapshot exists on NAS.
- [x] Branch `slice2a-game-data` is ready for separate-session review and has not been merged or pushed.
