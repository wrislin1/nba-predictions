# Playoff Game Results Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a provenance-complete, independently validated BAA/NBA postseason-results table for ending seasons 1947–2026 without changing any existing regular-season artifact.

**Architecture:** A shared Basketball Reference schedule parser feeds the unchanged historical regular-season selector and a new playoff adapter. The playoff adapter takes the complement of that proven historical selector for 1947–1983, reads dedicated playoff pages plus verified play-in cache copies for 1984–2026, and passes one labeled frame to a separate builder that resolves franchises, reconciles hoopR, validates curated counts, and atomically publishes a Parquet/manifest pair. The modern publisher and snapshot scripts gain only pair-preservation/exclusion seams; playoff raw recovery remains an independently attested snapshot family.

**Tech Stack:** Python 3.12, pandas 3.0.5, pyarrow 25.0.0, requests 2.34.2, lxml 6.1.1, pytest 9.1.1, Bash, jq, rsync, SHA-256, git worktrees.

## Global Constraints

- Repository root is `/home/riz/dev/nba-predictions-revived`; run commands there unless a worktree path is explicitly shown.
- Before implementation, invoke `superpowers:using-git-worktrees` and create `.worktrees/playoff-game-results` on branch `playoff-game-results` from the commit containing this plan. Determine that base with `git rev-parse HEAD`; do not merge or push during implementation.
- Invoke `superpowers:test-driven-development` for every production-code task, `superpowers:systematic-debugging` for any unexpected failure, and `superpowers:verification-before-completion` before claiming the branch is ready.
- Season always means ending season: `1947` is 1946–47 and `2026` is 2025–26.
- Scope is BAA/NBA `playoff`, `play_in`, `tiebreaker`, and `voided` games only. ABA, exhibitions, All-Star games, NBA Cup games, round/seed/series metadata, and box-score statistics are excluded.
- Published products are exactly `data/parquet/playoff_games.parquet` and `data/parquet/playoff_games_manifest.json`.
- Historical input under `data/raw/historical_bref/` is read-only and byte-identical. No task refetches, copies over, or rewrites that directory.
- Newly fetched and seeded bodies live only in gitignored `data/raw/playoff_bref/`. They never enter `data/raw/bref/`.
- `fetch-playoffs` is the only new network path. Tests, audits after acquisition, and `build-playoffs` are offline.
- Basketball Reference access uses ordinary TLS verification, the existing browser-identifying User-Agent, a minimum 12-second interval between network requests, no parallel requests, and immediate failure on HTTP, TLS, rate-limit, block-page, or schema errors.
- Run a four-season source spike for 1984, 2002, 2020, and 2026 before the remaining 39 dedicated-page requests.
- Existing regular artifacts, schemas, row sets, ordering, and hashes remain unchanged. The fixed baseline is:

```text
c7518a6286256e200e9df8d38c3e1d196a38531d527a8c21b4fe4b02f591611c  data/parquet/build_manifest.json
8da6f15b126e0617d870bc65026f5096119a31abc908282dd9ac01fbdb72967d  data/parquet/team_games.parquet
cbbf167aa36a7b908f59c20243ae3cf8a5b91944c53cd5539fb838674afbf8e6  data/parquet/games.parquet
65c5c41e51806d8ca5c06f22ce44362a4d23df1ae8eb74d5ba7aca0ef568208d  data/parquet/franchises.parquet
3d99a0727242b1cede114755fde08c360e882da65686a62be7157f1f7ab33b1a  data/parquet/venues.parquet
2c545d90435f2dc0f7fed1369d02a5033602d1544e2734dde92a8b7ed1eb9269  data/parquet/historical_games.parquet
2be1f3ccb999bab64a344b28b6b25a45c318e994b74b1b99e9e4c9035b0dc010  data/parquet/historical_games_manifest.json
```

- The historical selector in `historical_bref.load_season()` remains the authority for postseason boundaries. The new adapter parses the same verified pages and selects their `(schedule_url, source_row)` complement; it does not reproduce the fragile separator state machine.
- Historical explicit-remark precedence is `voided`, then `tiebreaker`, then remaining selector complement as `playoff`. Modern precedence is exact `Play-In Game` from an approved seeded monthly page, then every completed dedicated-page row as `playoff`.
- The 59 verified historical non-type annotations use the approved exact
  location/format/nickname allowlists. Location remarks are neutral-site
  signals; format and nickname remarks are non-site annotations. Any new
  nonempty complement remark fails closed.
- `game_type` is exactly one of `playoff`, `play_in`, `tiebreaker`, or `voided`.
- Source visitor/home columns determine score orientation. `game_type` remarks are not site signals.
- All 84 scoped 2020 rows are `neutral` through the single provenance-backed cohort rule. No other cohort rule is permitted by this plan.
- Blank venue and attendance stay null. Reported attendance zero stays zero and has `attendance_status == "reported_zero"`.
- Every source alias resolves through the existing season-aware franchise reference. Do not silently change franchise identity.
- Row order is exactly `season, game_date, game_id`.
- Unit tests use committed small fixtures and never access the network. Tests that read the full local cache carry the `integration` marker.
- A complete build requires ending seasons 1947–2026, 43 dedicated pages for 1984–2026, the seven seeded play-in pages, all verified historical pages consumed by the historical selector, and pinned hoopR schedules for 2020–2026.
- Cross-source equality is bidirectional on `(season, game_date, game_type, away_franchise_id, home_franchise_id, away_pts, home_pts)` and must contain exactly 627 rows for 2020–2026.
- Regular overlap is checked against both regular publications on `(season, game_date, away_franchise_id, home_franchise_id, away_pts, home_pts)`; game IDs are insufficient because namespaces differ.
- Two consecutive offline builds must reproduce both playoff artifacts byte-for-byte.
- The playoff manifest contains no fetch timestamps, absolute checkout paths, temporary paths, or other machine-specific values.
- The playoff raw snapshot is named `playoff-raw-2026-<manifest-sha12>` and contains only `playoff_bref/`, `playoff_games_manifest.json`, and `SHA256SUMS`.
- Archive and restore pin the trusted manifest, validate before and after copying, reject unsafe structures and incomplete pairs, and verify the historical dependency without overwriting it.

---

## File Structure

### Create

```text
tests/conftest.py
    Shared pytest fixtures: repo_data_dir (Task 1), plus
    historical_boundary_cache and seeded_playoff_cache (Task 4).

src/nba_data/bref_schedule.py
    Source-neutral parsing, normalization, overtime decoding, and stable BREF
    game-ID assignment for one schedule table.

src/nba_data/playoff_bref.py
    Playoff URL/cache identity, verified play-in seeding, polite fetch, strict
    cache inventory, historical-complement selection, and game classification.

src/nba_data/playoff_build.py
    Reference loading, franchise resolution, site rules, schema enforcement,
    validation, provenance, deterministic manifest, and offline build entry.

src/nba_data/publication.py
    Reusable transactional publication of an exact two-file artifact pair.

data/reference/playoff_season_counts.csv
    Independently curated count/date-window contract for exactly 1947..2026.

data/reference/playoff_site_rules.csv
    The one source-backed 2020 neutral-site cohort rule.

tests/fixtures/playoff_bref/NBA_1984_games.html
tests/fixtures/playoff_bref/NBA_2020_games.html
tests/fixtures/playoff_bref/NBA_2020_games-august.html
    Small sanitized dedicated/play-in schedule-table fixtures.

tests/fixtures/historical_postseason_boundary/BAA_1948_games.html
tests/fixtures/historical_postseason_boundary/BAA_1948_games-march.html
tests/fixtures/historical_postseason_boundary/BAA_1948_games-april.html
    Sanitized duplicate-separator season with tiebreakers between the two
    Playoffs separator rows (Task 4).

tests/fixtures/playoff_non_interference_sha256.json
    Fixed seven-artifact pre-feature hash baseline.

tests/test_bref_schedule.py
    Shared parser schema, value, null, overtime, and stable-ID tests.

tests/test_playoff_bref.py
    URL, source validation, seeding, fetch cadence/resume, inventory,
    complement, classification, and union tests.

tests/test_playoff_build.py
    Reference, schema, validation, provenance, determinism, and rollback tests.

tests/test_publication.py
    Generic pair publisher’s stage, rollback, symlink, and cleanup contracts.

tests/test_playoff_audit.py
    Deterministic offline source-audit rendering tests.

tests/test_playoff_non_interference.py
    Fixed-hash and rebuild non-interference gates.

tests/test_playoff_raw_snapshot.py
    Archive/restore inverse, tamper, race, pin, and structure gates.

scripts/audit_playoff_source.py
    Offline spike/full-cache inventory, field coverage, and classifier audit.

scripts/archive_playoff_raw_snapshot.sh
scripts/restore_playoff_raw.sh
    Exact playoff-cache snapshot and restore.

docs/audits/2026-07-30-playoff-bref-source-spike.md
    Generated four-season source-contract evidence.
```

### Modify

```text
src/nba_data/historical_bref.py
    Retain public compatibility wrappers and the entire selector/fetch state
    machine while delegating row parsing and ID assignment.

src/nba_data/historical_build.py
    Call the generic pair publisher through a compatibility wrapper; keep
    historical error types, output bytes, and manifest bytes unchanged.

src/nba_data/hoopr.py
    Add an offline postseason-evidence loader without changing load_season().

src/nba_data/build.py
    Preserve both complete independent publication pairs during modern atomic
    directory replacement.

src/nba_data/__main__.py
    Add fetch-playoffs and build-playoffs commands.

scripts/archive_raw_snapshot.sh
    Exclude /playoff_bref/ alongside /historical_bref/.

README.md
docs/data-dictionary.md
    Document sources, schema, commands, deterministic rebuild, and recovery.

tests/test_historical_bref.py
tests/test_historical_build.py
tests/test_hoopr.py
tests/test_build_gates.py
tests/test_cli.py
tests/test_repository_layout.py
    Regression and integration coverage at the existing seams.
```

### Generated and committed in the data-acquisition task

```text
data/parquet/playoff_games.parquet
data/parquet/playoff_games_manifest.json
```

## Shared Contracts

### Published columns and pandas dtypes

```python
PLAYOFF_COLUMNS = [
    "game_id", "season", "league", "game_date", "game_type",
    "away_team_name", "home_team_name",
    "away_franchise_id", "home_franchise_id",
    "away_pts", "home_pts", "home_margin", "overtime_periods",
    "site_status", "venue_name", "attendance", "attendance_status",
    "boxscore_url", "schedule_url",
]

PLAYOFF_DTYPES = {
    "game_id": "string",
    "season": "int64",
    "league": "string",
    "game_type": "string",
    "away_team_name": "string",
    "home_team_name": "string",
    "away_franchise_id": "string",
    "home_franchise_id": "string",
    "away_pts": "int64",
    "home_pts": "int64",
    "home_margin": "int64",
    "overtime_periods": "int64",
    "site_status": "string",
    "venue_name": "string",
    "attendance": "Int64",
    "attendance_status": "string",
    "boxscore_url": "string",
    "schedule_url": "string",
}
```

`game_date` is an `object` column containing `datetime.date`, matching the
historical publication. `venue_name` and `boxscore_url` are nullable pandas
`string`; all other string columns are non-null after validation.

### Natural keys

```python
CROSS_SOURCE_KEY = [
    "season", "game_date", "game_type",
    "away_franchise_id", "home_franchise_id",
    "away_pts", "home_pts",
]

REGULAR_OVERLAP_KEY = [
    "season", "game_date",
    "away_franchise_id", "home_franchise_id",
    "away_pts", "home_pts",
]

SOURCE_ROW_KEY = ["schedule_url", "source_row"]
```

### Reference types

```python
@dataclass(frozen=True)
class ExpectedSeason:
    season: int
    playoff_games: int
    play_in_games: int
    tiebreaker_games: int
    voided_games: int
    total_games: int
    first_postseason_date: date
    last_postseason_date: date
    source_url: str
    note: str

@dataclass(frozen=True)
class SiteRule:
    season: int
    site_status: str
    expected_matches: int
    source_url: str
    note: str
```

### Deterministic validation report

`playoff_build.validate()` returns only JSON-stable values in this shape:

```json
{
  "season_range": [1947, 2026],
  "total_games": 0,
  "games_by_type": {},
  "games_by_season": {},
  "cross_source": {
    "season_range": [2020, 2026],
    "expected_rows": 627,
    "matched_rows": 627
  },
  "regular_overlap_rows": 0,
  "neutral_2020_rows": 84,
  "coverage": {
    "boxscore_url": {"present": 0, "missing": 0},
    "venue_name": {"present": 0, "missing": 0},
    "attendance": {
      "reported_positive": 0,
      "reported_zero": 0,
      "missing": 0
    },
    "site_status": {"home": 0, "neutral": 0, "unknown": 0}
  },
  "season_sha256": {}
}
```

The zeros above describe the value types, not acceptance values. Tests compare
the actual complete dictionary and the manifest serializes it with sorted
keys.

## Task Dependency Order

1. Lock the baseline and fixtures.
2. Extract the shared parser.
3. Add cache acquisition and verified seeding.
4. Add source selection and classification.
5. Load independent count/window/site references.
6. Expose pinned hoopR postseason evidence.
7. Assemble and validate the playoff frame.
8. Add generic pair publication, manifest creation, and CLI dispatch.
9. Add modern non-interference plumbing.
10. Run the spike/full acquisition, reconcile, and publish.
11. Add and exercise independent raw recovery.
12. Document and run the whole-branch acceptance gate.

Do not start Task 9 until Tasks 1–8 pass. Do not create the NAS snapshot until
the final playoff manifest is committed and two offline builds are identical.

---

### Task 1: Lock the non-interference baseline and representative fixtures

**Files:**

- Create: `tests/conftest.py`
- Create: `tests/fixtures/playoff_non_interference_sha256.json`
- Create: `tests/fixtures/playoff_bref/NBA_1984_games.html`
- Create: `tests/fixtures/playoff_bref/NBA_2020_games.html`
- Create: `tests/fixtures/playoff_bref/NBA_2020_games-august.html`
- Create: `tests/test_playoff_non_interference.py`
- Modify: `tests/test_repository_layout.py:289-324`

**Interfaces:**

- Consumes: the seven fixed SHA-256 values in Global Constraints.
- Produces: `BASELINE_PATH: Path`,
  `sha256_file(path: Path) -> str`, the shared `repo_data_dir` pytest
  fixture, and committed fixture pages used by Tasks 2–4.

- [ ] **Step 1: Create the exact baseline ledger**

Write `tests/fixtures/playoff_non_interference_sha256.json`:

```json
{
  "data/parquet/build_manifest.json": "c7518a6286256e200e9df8d38c3e1d196a38531d527a8c21b4fe4b02f591611c",
  "data/parquet/franchises.parquet": "65c5c41e51806d8ca5c06f22ce44362a4d23df1ae8eb74d5ba7aca0ef568208d",
  "data/parquet/games.parquet": "cbbf167aa36a7b908f59c20243ae3cf8a5b91944c53cd5539fb838674afbf8e6",
  "data/parquet/historical_games.parquet": "2c545d90435f2dc0f7fed1369d02a5033602d1544e2734dde92a8b7ed1eb9269",
  "data/parquet/historical_games_manifest.json": "2be1f3ccb999bab64a344b28b6b25a45c318e994b74b1b99e9e4c9035b0dc010",
  "data/parquet/team_games.parquet": "8da6f15b126e0617d870bc65026f5096119a31abc908282dd9ac01fbdb72967d",
  "data/parquet/venues.parquet": "3d99a0727242b1cede114755fde08c360e882da65686a62be7157f1f7ab33b1a"
}
```

- [ ] **Step 2: Write the shared conftest and the failing fixed-hash and layout tests**

There is no `tests/conftest.py` today. Create it with exactly:

```python
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def repo_data_dir() -> Path:
    return REPO_ROOT / "data"
```

Integration tests in Tasks 4–7 receive the verified repository cache through
`repo_data_dir`; Task 4 later extends this file with two cache-seeding
fixtures.

Add this core to `tests/test_playoff_non_interference.py`:

```python
import hashlib
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = (
    REPO_ROOT / "tests/fixtures/playoff_non_interference_sha256.json"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def test_regular_publication_matches_locked_baseline():
    expected = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    actual = {
        relative: sha256_file(REPO_ROOT / relative)
        for relative in sorted(expected)
    }
    assert actual == expected
```

Add a repository-layout assertion that the three playoff fixture files are
tracked and that `git ls-files data/raw/playoff_bref` returns no paths.

- [ ] **Step 3: Run the baseline tests before adding fixtures**

Run:

```bash
pytest tests/test_playoff_non_interference.py \
  tests/test_repository_layout.py -q
```

Expected: the fixed-hash test passes and the new fixture/layout assertion
fails because `tests/fixtures/playoff_bref/` does not exist.

- [ ] **Step 4: Add three sanitized fixture pages**

Each fixture must contain one `<table id="schedule">` with the exact
`data-stat` cells used by the parser:

```html
<table id="schedule">
  <tbody>
    <tr>
      <th data-stat="date_game"><a href="/boxscores/index.fcgi?month=4&amp;day=17&amp;year=1984">Tue, Apr 17, 1984</a></th>
      <td data-stat="visitor_team_name">Kansas City Kings</td>
      <td data-stat="visitor_pts">113</td>
      <td data-stat="home_team_name">Los Angeles Lakers</td>
      <td data-stat="home_pts">116</td>
      <td data-stat="box_score_text"><a href="/boxscores/198404170LAL.html">Box Score</a></td>
      <td data-stat="overtimes"></td>
      <td data-stat="attendance">15,298</td>
      <td data-stat="game_duration">2:12</td>
      <td data-stat="arena_name">The Forum</td>
      <td data-stat="game_remarks"></td>
    </tr>
  </tbody>
</table>
```

Insert `<td data-stat="game_start_time">10:30p</td>` immediately after the
date cell so the row has the same 12-column shape as the source. Use that exact
row in `NBA_1984_games.html`.

Use the same 12-cell structure for the other fixtures with these exact
values:

```text
NBA_2020_games.html
date_game=Mon, Aug 17, 2020
game_start_time=1:30p
visitor_team_name=Utah Jazz
visitor_pts=125
home_team_name=Denver Nuggets
home_pts=135
box_score_text=/boxscores/202008170DEN.html
overtimes=OT
attendance=<blank>
game_duration=2:41
arena_name=AdventHealth Arena
game_remarks=<blank>

NBA_2020_games-august.html
date_game=Sat, Aug 15, 2020
game_start_time=2:30p
visitor_team_name=Memphis Grizzlies
visitor_pts=122
home_team_name=Portland Trail Blazers
home_pts=126
box_score_text=/boxscores/202008150POR.html
overtimes=<blank>
attendance=<blank>
game_duration=2:25
arena_name=HP Field House
game_remarks=Play-In Game
```

Keep each fixture under 4 KiB and retain no navigation, advertising, scripts,
comments, or unrelated rows.

- [ ] **Step 5: Run the focused tests**

Run:

```bash
pytest tests/test_playoff_non_interference.py \
  tests/test_repository_layout.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/conftest.py \
  tests/fixtures/playoff_non_interference_sha256.json \
  tests/fixtures/playoff_bref \
  tests/test_playoff_non_interference.py \
  tests/test_repository_layout.py
git commit -m "test: lock playoff non-interference baseline"
```

---

### Task 2: Extract the shared Basketball Reference schedule parser

**Files:**

- Create: `src/nba_data/bref_schedule.py`
- Create: `tests/test_bref_schedule.py`
- Modify: `src/nba_data/historical_bref.py:16-38,102-270,445-521`
- Modify: `src/nba_data/historical_build.py:347-359`
- Modify: `tests/test_historical_bref.py`
- Modify: `tests/test_historical_build.py`

**Interfaces:**

- Consumes: fixture pages and the locked historical artifact hashes from Task
  1.
- Produces:
  `parse_schedule_page(payload: bytes, *, season: int, league: str,
  schedule_url: str) -> pd.DataFrame`,
  `assign_game_ids(frame: pd.DataFrame) -> pd.DataFrame`, and
  `overtime_periods(marker: str) -> int`.
- Compatibility: `historical_bref.parse_schedule_page`,
  `historical_bref._assign_game_ids`, and
  `historical_bref._overtime_periods` remain callable with their current
  signatures and delegate to the new module.

- [ ] **Step 1: Write failing source-neutral parser tests**

Create `tests/test_bref_schedule.py` with focused tests for:

```python
from pathlib import Path

import pandas as pd
import pytest

from nba_data import bref_schedule

FIXTURE = (
    Path(__file__).parent
    / "fixtures/playoff_bref/NBA_1984_games.html"
)


def test_parse_schedule_page_accepts_explicit_league():
    frame = bref_schedule.parse_schedule_page(
        FIXTURE.read_bytes(),
        season=1984,
        league="NBA",
        schedule_url=(
            "https://www.basketball-reference.com/playoffs/"
            "NBA_1984_games.html"
        ),
    )
    row = frame.iloc[0]
    assert row["game_id"] == "bref-198404170LAL"
    assert row["league"] == "NBA"
    assert row["game_date"].isoformat() == "1984-04-17"
    assert row["away_pts"] == 113
    assert row["home_pts"] == 116
    assert row["overtime_periods"] == 0
    assert row["attendance"] == 15298
    assert row["attendance_status"] == "reported_positive"


@pytest.mark.parametrize(
    ("marker", "expected"),
    [("", 0), ("OT", 1), ("2OT", 2), ("5OT", 5)],
)
def test_overtime_periods(marker, expected):
    assert bref_schedule.overtime_periods(marker) == expected


def test_overtime_periods_rejects_unknown_marker():
    with pytest.raises(ValueError, match="unsupported overtime marker"):
        bref_schedule.overtime_periods("postponed")
```

Also copy existing tests for duplicate IDs, fallback IDs, null venue,
reported-zero attendance, missing attendance, unsupported league, malformed
box-score link, duplicate schedule tables, and non-final scores into this
module. Keep historical wrapper tests in `test_historical_bref.py`.

- [ ] **Step 2: Run the new tests and verify the missing module**

Run:

```bash
pytest tests/test_bref_schedule.py -q
```

Expected: collection fails because `nba_data.bref_schedule` does not exist.

- [ ] **Step 3: Move parser primitives without changing behavior**

Create `src/nba_data/bref_schedule.py`. Move these exact current symbols from
`historical_bref.py` without changing their statements:

```text
REQUIRED_STATS
OVERTIME_PATTERN
BOXSCORE_PATTERN
EXPLICIT_NEUTRAL_REMARKS
_document
_cell_text
_cell_href
_overtime_periods
_slug
_attendance
parse_schedule_page
_assign_game_ids
```

Rename `_overtime_periods` to `overtime_periods` and `_assign_game_ids` to
`assign_game_ids` in the destination. Change only the league boundary in
`parse_schedule_page`: remove its call to `league_for_season()` and use the
validated argument below.

```python
VALID_LEAGUES = frozenset({"BAA", "NBA"})


def parse_schedule_page(
    payload: bytes,
    *,
    season: int,
    league: str,
    schedule_url: str,
) -> pd.DataFrame:
    if league not in VALID_LEAGUES:
        raise ValueError(f"unsupported league: {league}")
```

Keep the existing stat-keyed row loop, stable sort, slug/fallback ordinal,
dtype normalization, and output columns byte-for-byte. The new module must
not import `historical_bref`.

- [ ] **Step 4: Add historical compatibility wrappers**

Define these historical compatibility wrappers:

```python
def _overtime_periods(marker: str) -> int:
    return bref_schedule.overtime_periods(marker)


def parse_schedule_page(
    payload: bytes,
    *,
    season: int,
    schedule_url: str,
) -> pd.DataFrame:
    return bref_schedule.parse_schedule_page(
        payload,
        season=season,
        league=league_for_season(season),
        schedule_url=schedule_url,
    )


def _assign_game_ids(frame: pd.DataFrame) -> pd.DataFrame:
    return bref_schedule.assign_game_ids(frame)
```

Import `bref_schedule` in `historical_bref.py`. Update
`historical_build.validate()` to call `bref_schedule.overtime_periods`
directly while retaining the historical wrapper for compatibility tests.

- [ ] **Step 5: Run parser and historical regression tests**

Run:

```bash
pytest tests/test_bref_schedule.py \
  tests/test_historical_bref.py \
  tests/test_historical_postseason_boundary.py \
  tests/test_historical_build.py -q
```

Expected: PASS.

- [ ] **Step 6: Prove historical byte identity**

Run:

```bash
python -m nba_data build-historical
pytest tests/test_playoff_non_interference.py -q
git status --short data/parquet/historical_games.parquet \
  data/parquet/historical_games_manifest.json
```

Expected: build succeeds, the fixed-hash test passes, and `git status`
prints nothing for both historical products.

- [ ] **Step 7: Commit**

```bash
git add src/nba_data/bref_schedule.py \
  src/nba_data/historical_bref.py \
  src/nba_data/historical_build.py \
  tests/test_bref_schedule.py \
  tests/test_historical_bref.py \
  tests/test_historical_build.py
git commit -m "refactor: share basketball reference schedule parser"
```

---

### Task 3: Add dedicated playoff acquisition and verified play-in seeding

**Files:**

- Create: `src/nba_data/playoff_bref.py`
- Create: `tests/test_playoff_bref.py`
- Modify: `tests/test_cache.py`

**Interfaces:**

- Consumes:
  `bref_schedule.parse_schedule_page(...)`,
  `cache.read_valid(...)`, `cache.require_valid(...)`, and
  `cache.publish_bytes(...)`.
- Produces:
  `PLAYOFF_SEASONS: tuple[int, ...]`,
  `DEDICATED_SEASONS: tuple[int, ...]`,
  `PLAY_IN_MONTHS: dict[int, str]`,
  `playoff_url(season: int) -> str`,
  `playoff_filename(season: int) -> str`,
  `validate_dedicated_page(path: Path, season: int) -> None`,
  `validate_seeded_play_in_page(path: Path, season: int) -> None`,
  `seed_play_in_page(raw_dir: Path, season: int) -> Path | None`,
  `validate_cache_inventory(raw_dir: Path, *, complete: bool) -> tuple[Path, ...]`,
  and
  `fetch(raw_dir: Path, seasons: Iterable[int] = DEDICATED_SEASONS,
  sleep_s: float = 12) -> None`.

- [ ] **Step 1: Write failing URL, validator, and seeding tests**

Start `tests/test_playoff_bref.py` with:

```python
import hashlib
import json
from pathlib import Path

import pytest

from nba_data import bref, cache, playoff_bref

FIXTURES = Path(__file__).parent / "fixtures/playoff_bref"


def test_playoff_identity_is_deterministic():
    assert playoff_bref.playoff_url(1984) == (
        "https://www.basketball-reference.com/playoffs/"
        "NBA_1984_games.html"
    )
    assert playoff_bref.playoff_filename(1984) == "NBA_1984_games.html"
    with pytest.raises(ValueError, match="1984..2026"):
        playoff_bref.playoff_url(1983)


def test_seed_play_in_page_requires_and_preserves_verified_source(tmp_path):
    raw_dir = tmp_path / "raw"
    source = raw_dir / "bref/NBA_2020_games-august.html"
    payload = (
        FIXTURES / "NBA_2020_games-august.html"
    ).read_bytes()
    cache.publish_bytes(
        source,
        payload,
        {
            "provider": "basketball_reference",
            "season": 2020,
            "month": "august",
        },
        bref.validate_payload,
    )

    destination = playoff_bref.seed_play_in_page(raw_dir, 2020)

    assert destination == (
        raw_dir / "playoff_bref/NBA_2020_games-august.html"
    )
    assert destination.read_bytes() == payload
    metadata = json.loads(
        destination.with_name(
            destination.name + ".meta.json"
        ).read_text(encoding="utf-8")
    )
    assert metadata["provider"] == "basketball_reference"
    assert metadata["resource"] == "play_in_schedule_month"
    assert metadata["league"] == "NBA"
    assert metadata["season"] == 2020
    assert metadata["month"] == "august"
    assert metadata["url"].endswith(
        "/leagues/NBA_2020_games-august.html"
    )
    assert metadata["seeded_from"] == (
        "raw/bref/NBA_2020_games-august.html"
    )
    assert metadata["seed_operation"] == "verified_cache_copy"
    assert metadata["source_sha256"] == hashlib.sha256(
        payload
    ).hexdigest()
```

Add rejection tests for a missing source body, missing source sidecar,
checksum mismatch, wrong source season/month metadata, destination symlink,
or an already-present destination whose metadata is not identical.

- [ ] **Step 2: Write failing fetch cadence and resumability tests**

Use a fake session and monkeypatched `time.sleep`:

```python
def test_fetch_resumes_and_sleeps_only_between_requests(
    tmp_path, monkeypatch
):
    raw_dir = tmp_path / "raw"
    seed_verified_play_in_source(raw_dir, season=2020)
    responses = [
        FakeResponse(
            (FIXTURES / name).read_bytes()
        )
        for name in ("NBA_1984_games.html", "NBA_2020_games.html")
    ]
    session = FakeSession(responses)
    sleeps = []
    monkeypatch.setattr(
        playoff_bref.requests, "Session", lambda: session
    )
    monkeypatch.setattr(
        playoff_bref.time, "sleep", sleeps.append
    )

    playoff_bref.fetch(
        raw_dir, seasons=(1984, 2020), sleep_s=12
    )
    playoff_bref.fetch(
        raw_dir, seasons=(1984, 2020), sleep_s=12
    )

    assert [call["verify"] for call in session.calls] == [True, True]
    assert [call["timeout"] for call in session.calls] == [30, 30]
    assert sleeps == [12]
    assert len(session.calls) == 2
```

`seed_verified_play_in_source`, `FakeResponse`, and `FakeSession` are local
test helpers with explicit payload/status/call recording. Also test immediate
failure and no second request for HTTP 403, HTTP 429, request exceptions,
invalid HTML, a missing schedule table, a non-final row, and an invalid
box-score URL.

- [ ] **Step 3: Run the new adapter tests**

Run:

```bash
pytest tests/test_playoff_bref.py -q
```

Expected: collection fails because `nba_data.playoff_bref` does not exist.

- [ ] **Step 4: Implement constants, identities, and page validators**

Create the module with these exact identities:

```python
BASE_URL = "https://www.basketball-reference.com"
PLAYOFF_SEASONS = tuple(range(1947, 2027))
DEDICATED_SEASONS = tuple(range(1984, 2027))
PLAY_IN_MONTHS = {
    2020: "august",
    2021: "may",
    **{season: "april" for season in range(2022, 2027)},
}


def playoff_url(season: int) -> str:
    if season not in DEDICATED_SEASONS:
        raise ValueError(
            f"dedicated playoff season must be 1984..2026: {season}"
        )
    return (
        f"{BASE_URL}/playoffs/NBA_{season}_games.html"
    )


def playoff_filename(season: int) -> str:
    playoff_url(season)
    return f"NBA_{season}_games.html"
```

`validate_dedicated_page()` reads the body and calls
`bref_schedule.parse_schedule_page(payload, season=season, league="NBA",
schedule_url=playoff_url(season))`. It then requires at least one completed
row, no missing score, and a valid `bref-` ID backed by a box-score URL for
every row. `validate_seeded_play_in_page()` parses with the deterministic
regular-month URL and requires at least one completed row whose normalized
remark is exactly `play-in game`.

- [ ] **Step 5: Implement provenance-preserving seeding**

Use `cache.require_valid(source, bref.validate_payload)` to validate the
original body/sidecar checksum pair, then explicitly validate these original
sidecar fields before copying:

```python
expected_source = {
    "provider": "basketball_reference",
    "season": season,
    "month": PLAY_IN_MONTHS[season],
}
```

Publish the same bytes through `cache.publish_bytes()` with:

```python
seed_metadata = {
    "provider": "basketball_reference",
    "resource": "play_in_schedule_month",
    "league": "NBA",
    "season": season,
    "month": month,
    "url": (
        f"{BASE_URL}/leagues/"
        f"NBA_{season}_games-{month}.html"
    ),
    "seeded_from": (
        f"raw/bref/NBA_{season}_games-{month}.html"
    ),
    "seed_operation": "verified_cache_copy",
    "source_sha256": hashlib.sha256(payload).hexdigest(),
}
```

Return `None` for seasons outside `PLAY_IN_MONTHS`. For an existing valid
destination, require every identity field above plus the destination checksum
to match and return without rewriting either file.

- [ ] **Step 6: Implement strict inventory validation and polite fetch**

`validate_cache_inventory(..., complete=False)` accepts only regular
non-symlink `.html` bodies and `.html.meta.json` sidecars whose filenames,
resource identities, URLs, and checksums match either the dedicated or seeded
contracts. It rejects `.part`, orphan, duplicate, special, directory, symlink,
and unrelated entries. With `complete=True`, require exactly:

```python
expected_bodies = {
    *(playoff_filename(season) for season in DEDICATED_SEASONS),
    *(
        f"NBA_{season}_games-{month}.html"
        for season, month in PLAY_IN_MONTHS.items()
    ),
}
assert len(expected_bodies) == 50
```

Implement `fetch()` with the existing `historical_bref.fetch()` acquire
pattern: validate the cache root, seed the applicable play-in page before a
network request, reuse one `requests.Session` with `bref.USER_AGENT`, call
`session.get(url, timeout=30, verify=True)`, sleep only between actual
requests, validate before atomic publication, remove `.part` files on any
publication exception, and stop on the first failure. Dedicated metadata is:

```python
{
    "provider": "basketball_reference",
    "resource": "playoff_schedule",
    "league": "NBA",
    "season": season,
    "month": None,
    "url": playoff_url(season),
}
```

- [ ] **Step 7: Run adapter and cache tests**

Run:

```bash
pytest tests/test_playoff_bref.py tests/test_cache.py -q
```

Expected: PASS, with no network access.

- [ ] **Step 8: Commit**

```bash
git add src/nba_data/playoff_bref.py \
  tests/test_playoff_bref.py tests/test_cache.py
git commit -m "feat: acquire verified playoff schedule cache"
```

---

### Task 4: Select and classify all four scoped game types

**Files:**

- Create: `tests/fixtures/historical_postseason_boundary/BAA_1948_games.html`
- Create: `tests/fixtures/historical_postseason_boundary/BAA_1948_games-march.html`
- Create: `tests/fixtures/historical_postseason_boundary/BAA_1948_games-april.html`
- Modify: `src/nba_data/playoff_bref.py`
- Modify: `tests/conftest.py`
- Modify: `tests/test_playoff_bref.py`
- Modify: `tests/test_historical_postseason_boundary.py`

**Interfaces:**

- Consumes:
  `historical_bref.load_season(raw_dir: Path, season: int)`,
  `historical_bref.discover_month_pages(...)`,
  `bref_schedule.parse_schedule_page(...)`, and verified cache pairs from
  Task 3.
- Produces:
  `classify_historical_complement(frame: pd.DataFrame) -> pd.DataFrame`,
  `load_historical_season(raw_dir: Path, season: int) -> pd.DataFrame`,
  `load_modern_season(raw_dir: Path, season: int) -> pd.DataFrame`,
  `load_season(raw_dir: Path, season: int) -> pd.DataFrame`, and the
  shared `historical_boundary_cache` and `seeded_playoff_cache` conftest
  fixtures.
- Internal frames retain `overtime_marker`, `game_remarks`, `source_row`, and
  `boxscore_slug` as validation evidence; these are not publication columns.

- [ ] **Step 0: Add the duplicate-separator fixture season and shared cache fixtures**

Derive the three 1948 fixture pages from the verified originals in
`data/raw/historical_bref/`, following the sanitization style of the existing
1947/1960/1961 boundary fixtures: trim the index page so it links only the
March and April month pages (the 1947 fixture index shows this pattern), keep
each month page's single `<table id="schedule">` including both `Playoffs`
separator rows and both `Tiebreaker` remark rows in March, and strip
navigation, scripts, comments, and advertising. The March page must retain
rows on both sides of the first separator and the tiebreakers between the two
separators; the April page carries only playoff rows.

Extend `tests/conftest.py` with exactly:

```python
@pytest.fixture
def historical_boundary_cache(tmp_path):
    from nba_data import cache, historical_bref

    fixtures = REPO_ROOT / "tests/fixtures/historical_postseason_boundary"
    for season in (1947, 1948, 1960, 1961):
        league = historical_bref.league_for_season(season)
        index = fixtures / f"{league}_{season}_games.html"
        cache_dir = tmp_path / "historical_bref"
        cache.publish_bytes(
            cache_dir / index.name,
            index.read_bytes(),
            {},
            lambda path, value=season: historical_bref.validate_index(
                path, value
            ),
        )
        for page in historical_bref.discover_month_pages(
            index.read_bytes(), season
        ):
            source = fixtures / page.filename
            cache.publish_bytes(
                cache_dir / page.filename,
                source.read_bytes(),
                {},
                lambda path, value=season, url=page.url: (
                    historical_bref.validate_month(path, value, url)
                ),
            )
    return tmp_path


@pytest.fixture
def seeded_playoff_cache(tmp_path):
    import hashlib

    from nba_data import cache, playoff_bref

    fixtures = REPO_ROOT / "tests/fixtures/playoff_bref"
    raw_dir = tmp_path / "raw"
    for season in (1984, 2020):
        name = playoff_bref.playoff_filename(season)
        cache.publish_bytes(
            raw_dir / "playoff_bref" / name,
            (fixtures / name).read_bytes(),
            {
                "provider": "basketball_reference",
                "resource": "playoff_schedule",
                "league": "NBA",
                "season": season,
                "month": None,
                "url": playoff_bref.playoff_url(season),
            },
            lambda path, value=season: (
                playoff_bref.validate_dedicated_page(path, value)
            ),
        )
    play_in_payload = (fixtures / "NBA_2020_games-august.html").read_bytes()
    cache.publish_bytes(
        raw_dir / "playoff_bref/NBA_2020_games-august.html",
        play_in_payload,
        {
            "provider": "basketball_reference",
            "resource": "play_in_schedule_month",
            "league": "NBA",
            "season": 2020,
            "month": "august",
            "url": (
                "https://www.basketball-reference.com/leagues/"
                "NBA_2020_games-august.html"
            ),
            "seeded_from": "raw/bref/NBA_2020_games-august.html",
            "seed_operation": "verified_cache_copy",
            "source_sha256": hashlib.sha256(play_in_payload).hexdigest(),
        },
        lambda path: playoff_bref.validate_seeded_play_in_page(path, 2020),
    )
    return raw_dir
```

The imports live inside the fixture bodies so collecting the suite before
Task 3's module exists never fails. `historical_boundary_cache` returns the
directory containing `historical_bref/`; `seeded_playoff_cache` returns the
`raw` directory containing `playoff_bref/` — the value Task 10's audit test
passes indirectly by handing `tmp_path` to `audit_cache()` as the data
directory.

- [ ] **Step 1: Write failing historical-complement tests**

Add a fixture-level test that builds a verified historical cache from the
existing boundary fixtures and asserts that the new adapter returns only the
selector complement:

```python
def test_historical_rows_are_exact_regular_selector_complement(
    historical_boundary_cache
):
    raw_dir = historical_boundary_cache
    regular = historical_bref.load_season(raw_dir, 1960)
    postseason = playoff_bref.load_historical_season(
        raw_dir, 1960
    )
    regular_keys = set(
        regular[["schedule_url", "source_row"]]
        .itertuples(index=False, name=None)
    )
    postseason_keys = set(
        postseason[["schedule_url", "source_row"]]
        .itertuples(index=False, name=None)
    )
    assert regular_keys.isdisjoint(postseason_keys)
    assert set(postseason["game_type"]) == {"playoff"}
```

Add a 1948 test on `historical_boundary_cache` proving the duplicate-separator
layout: the two March tiebreakers land in the complement as `tiebreaker`, the
remaining complement rows are `playoff`, and no regular selector row enters
the result.

Add unit tests for `classify_historical_complement()` on small synthetic
frames (one row per case, built with the internal-frame columns) proving:

- exact `Protest; does not count toward record` is `voided` and exact
  `Tiebreaker` is `tiebreaker`, both case-insensitively;
- every approved location remark is `playoff` with `site_status == "neutral"`;
- `Round Robin` and the four approved game nicknames are `playoff` without
  changing a valid home-site result to unknown;
- blank and null remarks are `playoff` with site status untouched; and
- an unrecognized nonempty complement remark raises
  `ValueError("unrecognized scoped game remark")`.

The 1953 voided-game identity (`bref-195211280MLH`) and carryover regions are
asserted against the real verified cache by the Step 7 integration census; no
1953 fixture season is committed.

- [ ] **Step 2: Write failing modern classification tests**

```python
def test_modern_union_labels_dedicated_and_play_in_rows(
    seeded_playoff_cache
):
    frame = playoff_bref.load_modern_season(
        seeded_playoff_cache, 2020
    )
    assert frame["game_type"].value_counts().to_dict() == {
        "playoff": 1,
        "play_in": 1,
    }
    assert set(frame["game_id"]) == {
        "bref-202008150POR",
        "bref-202008170DEN",
    }
    assert frame["game_id"].is_unique
```

Also assert that a `Play-In Game` remark is normalized to `site_status ==
"home"` before the later 2020 cohort override, that a dedicated-page remark
does not create a play-in, and that duplicate natural results across inputs
fail.

- [ ] **Step 3: Run the focused tests**

Run:

```bash
pytest tests/test_playoff_bref.py \
  tests/test_historical_postseason_boundary.py -q
```

Expected: FAIL because the three loader functions do not exist.

- [ ] **Step 4: Implement historical complement selection**

For 1947–1983:

1. Call `historical_bref.load_season(raw_dir, season)` first. Do not catch or
   weaken any boundary error.
2. Validate the same historical index body, discover every month page, parse
   every verified body through the shared parser, concatenate in discovered
   order, drop the page-local `game_id`, and call
   `bref_schedule.assign_game_ids()` once.
3. Build `regular_keys` from the proven selector’s
   `(schedule_url, source_row)` values.
4. Select rows whose source key is absent from `regular_keys`.
5. Require unique source keys in both frames and assert the two sets are
   disjoint.
6. Implement steps 6–8 in the produced helper
   `classify_historical_complement(frame)`, which returns a copy with
   `game_type` set and `site_status` adjusted. It normalizes `game_remarks`
   with `.astype("string").str.strip().str.casefold()`.
7. Define these exact audited non-type allowlists:

```python
HISTORICAL_NEUTRAL_REMARKS = frozenset({
    "neutral site",
    "at saint paul, mn",
    "at 69th regiment armory",
    "at madison, wi",
    "at indianapolis, in",
    "at oakland, ca",
    "at chicago, il (chicago arena)",
    "at washington, dc (national guard armory)",
    "at daly city, ca",
    "at kingdome",
    "at elkhart, in",
    "at detroit, mi (university of detroit fieldhouse)",
    "at tempe, az",
})
HISTORICAL_NONSITE_REMARKS = frozenset({
    "round robin",
    "havlicek stole the ball",
    "the willis reed game",
    "the greatest game ever played",
    "mother's day massacre",
})
```

8. Classify exact `protest; does not count toward record` as `voided`, exact
   `tiebreaker` as `tiebreaker`, and blank/null or either audited allowlist as
   `playoff`. Set `site_status = "neutral"` for the neutral allowlist. For the
   non-site allowlist and type remarks, change parser-produced `unknown` to
   `home`. Reject every other nonempty remark with
   `ValueError("unrecognized scoped game remark: ...")`.
9. Re-run `assign_game_ids()` on the selected rows so fallback ordinals depend
   only on the published panel.

Use source-row keys, not `game_id`, for the complement. This is necessary
because fallback ID ordinals may differ when regular and excluded rows are
assigned separately.

- [ ] **Step 5: Implement modern classification**

For 1984–2026, require and validate the dedicated body, parse it, label every
completed row `playoff`, and normalize exact type remarks away from site
classification:

```python
TYPE_REMARKS = frozenset({
    "play-in game",
    "tiebreaker",
    "protest; does not count toward record",
})

normalized = frame["game_remarks"].astype("string").str.strip().str.casefold()
frame.loc[
    normalized.isin(TYPE_REMARKS)
    & frame["site_status"].eq("unknown"),
    "site_status",
] = "home"
```

For 2020–2026, require the approved seeded page, parse it, retain only exact
`play-in game` rows, label them `play_in`, and apply the same non-site remark
normalization. Concatenate the dedicated and play-in frames, assign IDs once,
and reject duplicate source keys, game IDs, or result identities. Do not
publish any other row from the seeded monthly page.

`load_season()` dispatches 1947–1983 to the historical loader, 1984–2026 to
the modern loader, and rejects other seasons.

- [ ] **Step 6: Run unit classification tests**

Run:

```bash
pytest tests/test_playoff_bref.py \
  tests/test_historical_postseason_boundary.py -q
```

Expected: PASS.

- [ ] **Step 7: Run the full historical complement integration check**

Add an `integration` test over the existing local cache:

```python
@pytest.mark.integration
def test_full_historical_complement_inventory(repo_data_dir):
    frames = [
        playoff_bref.load_historical_season(
            repo_data_dir / "raw", season
        )
        for season in range(1947, 1984)
    ]
    games = pd.concat(frames, ignore_index=True)
    assert len(games) == 1240
    assert games["game_type"].value_counts().to_dict() == {
        "playoff": 1231,
        "tiebreaker": 8,
        "voided": 1,
    }
    assert int(games["game_remarks"].fillna("").ne("").sum()) == 68
    assert int(
        games["game_remarks"]
        .fillna("")
        .str.casefold()
        .str.startswith("at ")
        .sum()
    ) == 44
    assert set(
        games.loc[
            games["game_type"].eq("tiebreaker"), "season"
        ].value_counts().to_dict().items()
    ) == {(1948, 2), (1950, 2), (1956, 2), (1957, 2)}
    voided = games.loc[games["game_type"].eq("voided")].iloc[0]
    assert voided["game_id"] == "bref-195211280MLH"
    assert voided["game_date"].isoformat() == "1952-11-28"
    assert voided["season"] == 1953
```

Run:

```bash
pytest tests/test_playoff_bref.py -m integration -q
```

Expected: PASS with 1,240 rows and exact type counts.

- [ ] **Step 8: Commit**

```bash
git add src/nba_data/playoff_bref.py \
  tests/conftest.py \
  tests/fixtures/historical_postseason_boundary \
  tests/test_playoff_bref.py \
  tests/test_historical_postseason_boundary.py
git commit -m "feat: classify historical and modern postseason games"
```

---

### Task 5: Add independently curated counts, windows, and the 2020 site rule

**Files:**

- Create: `data/reference/playoff_season_counts.csv`
- Create: `data/reference/playoff_site_rules.csv`
- Create: `src/nba_data/playoff_build.py`
- Create: `tests/test_playoff_build.py`

**Interfaces:**

- Consumes: the `ExpectedSeason` and `SiteRule` contracts in Shared
  Contracts.
- Produces:
  `ExpectedSeason`, `SiteRule`,
  `load_expected_counts(path: Path) -> dict[int, ExpectedSeason]`,
  `load_site_rules(path: Path) -> dict[int, SiteRule]`, and
  `apply_site_rules(games: pd.DataFrame,
  rules: dict[int, SiteRule]) -> tuple[pd.DataFrame, dict[int, int]]`.

- [ ] **Step 1: Write failing reference-loader tests**

Create `tests/test_playoff_build.py`. Generate an 80-row valid count frame in
a helper, then mutate one property per test:

```python
COUNT_COLUMNS = [
    "season", "playoff_games", "play_in_games",
    "tiebreaker_games", "voided_games", "total_games",
    "first_postseason_date", "last_postseason_date",
    "source_url", "note",
]
SITE_RULE_COLUMNS = [
    "season", "site_status", "expected_matches",
    "source_url", "note",
]


def test_load_expected_counts_requires_exact_contract(
    tmp_path, valid_count_frame
):
    path = tmp_path / "counts.csv"
    valid_count_frame.to_csv(path, index=False)
    expected = playoff_build.load_expected_counts(path)
    assert tuple(expected) == tuple(range(1947, 2027))
    assert expected[2020].play_in_games == 1
    assert expected[2021].play_in_games == 6
    assert expected[1953].voided_games == 1


def test_load_expected_counts_rejects_bad_sum(
    tmp_path, valid_count_frame
):
    valid_count_frame.loc[
        valid_count_frame["season"].eq(1984), "total_games"
    ] += 1
    path = tmp_path / "counts.csv"
    valid_count_frame.to_csv(path, index=False)
    with pytest.raises(
        playoff_build.PlayoffValidationError,
        match="type counts must sum to total_games",
    ):
        playoff_build.load_expected_counts(path)
```

Add one test each for wrong column order, duplicate/missing/extra season,
non-integer/negative counts, empty source URL/note, non-HTTPS URL, reversed
date window, and every fixed edge-count violation.

For site rules, require exactly one row and assert:

```python
assert rules == {
    2020: playoff_build.SiteRule(
        season=2020,
        site_status="neutral",
        expected_matches=84,
        source_url=(
            "https://pr.nba.com/"
            "nba-nbpa-finalize-plan-2019-20-season-restart/"
        ),
        note=(
            "All remaining 2019-20 games were played at the three "
            "ESPN Wide World of Sports venues at Walt Disney World."
        ),
    )
}
```

Add rejection tests for another season, status other than `neutral`,
`expected_matches != 84`, duplicate rows, and missing provenance.

- [ ] **Step 2: Run loader tests**

Run:

```bash
pytest tests/test_playoff_build.py -q
```

Expected: collection fails because `nba_data.playoff_build` does not exist.

- [ ] **Step 3: Implement exact typed reference loaders**

Create `PlayoffValidationError(ValueError)`, the two frozen dataclasses, and
the two exact column constants. Read CSVs with `dtype="string"`, validate
column order before conversion, reject null/blank strings, use
`pd.to_numeric(..., errors="raise")`, and use
`date.fromisoformat()` for both date fields.

The count loader enforces:

```python
FIXED_TYPE_COUNTS = {
    1948: {"tiebreaker_games": 2},
    1950: {"tiebreaker_games": 2},
    1953: {"voided_games": 1},
    1956: {"tiebreaker_games": 2},
    1957: {"tiebreaker_games": 2},
    2020: {"play_in_games": 1},
    **{
        season: {"play_in_games": 6}
        for season in range(2021, 2027)
    },
}
```

All unspecified `tiebreaker_games`, `voided_games`, and pre-2020
`play_in_games` must be zero. Every `playoff_games` and `total_games` value
must be positive; other type counts may be zero.

- [ ] **Step 4: Implement the exact-match site-rule applier**

`apply_site_rules()` selects every game in the named season, requires the
match count to equal `expected_matches`, changes only `site_status`, and
returns the copied frame plus `{season: actual_matches}`. It rejects a rule
whose selected rows already span multiple unsupported statuses only if a row
has an invalid status; the rule is authoritative over valid `home`,
`neutral`, or `unknown` source values.

```python
def apply_site_rules(games, rules):
    output = games.copy()
    matches = {}
    for season, rule in sorted(rules.items()):
        mask = output["season"].eq(season)
        actual = int(mask.sum())
        if actual != rule.expected_matches:
            raise PlayoffValidationError(
                f"site rule {season} matched {actual}, "
                f"expected {rule.expected_matches}"
            )
        output.loc[mask, "site_status"] = rule.site_status
        matches[season] = actual
    return output, matches
```

- [ ] **Step 5: Curate all 80 independent season rows**

Write `data/reference/playoff_season_counts.csv` with the exact header:

```text
season,playoff_games,play_in_games,tiebreaker_games,voided_games,total_games,first_postseason_date,last_postseason_date,source_url,note
```

For each ending season:

- use the league playoff summary page, not the schedule page, as
  `source_url`;
- use `https://www.basketball-reference.com/playoffs/BAA_<season>.html` for
  1947–1949 and
  `https://www.basketball-reference.com/playoffs/NBA_<season>.html` for
  1950–2026;
- manually sum the series game records for `playoff_games`;
- record the first and last playoff dates shown in the series summary;
- add the fixed separately documented play-in/tiebreaker/voided counts;
- let the window cover all scoped `playoff`, `play_in`, and `tiebreaker`
  dates, but do not move the 1953 first date backward to include the voided
  November game; and
- state the evidence calculation in `note`, for example
  `Series-result game totals and first/last dates transcribed from the
  league playoff summary; play-in count independently documented.`

Before committing, independently recompute these fixed totals from the
curated CSV:

```bash
python - <<'PY'
import pandas as pd

frame = pd.read_csv("data/reference/playoff_season_counts.csv")
assert frame["season"].tolist() == list(range(1947, 2027))
parts = [
    "playoff_games", "play_in_games",
    "tiebreaker_games", "voided_games",
]
assert frame[parts].sum(axis=1).equals(frame["total_games"])
assert frame.loc[frame.season == 2020, "play_in_games"].item() == 1
assert (
    frame.loc[
        frame.season.between(2021, 2026), "play_in_games"
    ].tolist()
    == [6, 6, 6, 6, 6, 6]
)
assert frame["tiebreaker_games"].sum() == 8
assert frame["voided_games"].sum() == 1
PY
```

Expected: no output and exit 0.

- [ ] **Step 6: Add the one approved site rule**

Write `data/reference/playoff_site_rules.csv` as:

```csv
season,site_status,expected_matches,source_url,note
2020,neutral,84,https://pr.nba.com/nba-nbpa-finalize-plan-2019-20-season-restart/,All remaining 2019-20 games were played at the three ESPN Wide World of Sports venues at Walt Disney World.
```

- [ ] **Step 7: Validate the committed references**

Add:

```python
def test_committed_playoff_references_are_valid(repo_data_dir):
    counts = playoff_build.load_expected_counts(
        repo_data_dir
        / "reference/playoff_season_counts.csv"
    )
    rules = playoff_build.load_site_rules(
        repo_data_dir
        / "reference/playoff_site_rules.csv"
    )
    assert tuple(counts) == tuple(range(1947, 2027))
    assert set(rules) == {2020}
```

Run:

```bash
pytest tests/test_playoff_build.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add data/reference/playoff_season_counts.csv \
  data/reference/playoff_site_rules.csv \
  src/nba_data/playoff_build.py \
  tests/test_playoff_build.py
git commit -m "feat: add playoff validation references"
```

---

### Task 6: Expose pinned hoopR postseason evidence

**Files:**

- Modify: `src/nba_data/hoopr.py:17-48,91-140`
- Modify: `tests/test_hoopr.py`

**Interfaces:**

- Consumes:
  `hoopr._read_pinned_cache(path: Path, season: int) -> bytes | None` and
  `franchises.resolve(name: str, season: int) -> str`.
- Produces:
  `POSTSEASON_COLUMNS: tuple[str, ...]` and
  `load_postseason(raw_dir: Path, season: int) -> pd.DataFrame`.
- Compatibility: `hoopr.load_season()` and every existing modern build result
  remain unchanged.

- [ ] **Step 1: Write failing postseason normalization tests**

Extend `tests/test_hoopr.py`:

```python
def test_load_postseason_normalizes_type_identity_and_scores(
    tmp_path
):
    raw_dir = tmp_path / "raw"
    seed_hoopr_schedule(
        raw_dir,
        season=2020,
        rows=[
            completed_row(
                season_type=3,
                game_date="2020-08-17",
                away_display_name="Utah Jazz",
                home_display_name="Denver Nuggets",
                away_score=125,
                home_score=135,
            ),
            completed_row(
                season_type=5,
                game_date="2020-08-15",
                away_display_name="Memphis Grizzlies",
                home_display_name="Portland Trail Blazers",
                away_score=122,
                home_score=126,
            ),
            completed_row(season_type=2),
            incomplete_row(season_type=3),
        ],
    )

    frame = hoopr.load_postseason(raw_dir, 2020)

    assert frame.columns.tolist() == list(
        hoopr.POSTSEASON_COLUMNS
    )
    assert frame["game_type"].tolist() == [
        "play_in", "playoff"
    ]
    assert frame["away_franchise_id"].tolist() == [
        "MEM", "UTA"
    ]
    assert frame["home_franchise_id"].tolist() == [
        "POR", "DEN"
    ]
```

Add tests for missing/tampered cache, wrong pin metadata, season outside
2020–2026, unknown source team, missing/non-integer/negative scores, equal
teams, equal scores, and invalid date.

- [ ] **Step 2: Run the focused test**

Run:

```bash
pytest tests/test_hoopr.py -q
```

Expected: FAIL with `AttributeError` for `load_postseason`.

- [ ] **Step 3: Implement the evidence-only loader**

Add:

```python
POSTSEASON_COLUMNS = (
    "season",
    "game_date",
    "game_type",
    "away_franchise_id",
    "home_franchise_id",
    "away_pts",
    "home_pts",
)


def load_postseason(raw_dir: Path, season: int) -> pd.DataFrame:
    if season not in range(2020, 2027):
        raise ValueError(
            f"hoopR postseason season must be 2020..2026: {season}"
        )
    path = raw_dir / "hoopr" / f"nba_schedule_{season}.parquet"
    payload = _read_pinned_cache(path, season)
    if payload is None:
        raise cache.CacheIntegrityError(
            f"missing cache entry for {path}"
        )
    raw = pd.read_parquet(BytesIO(payload))
    completed = raw.loc[
        raw["status_type_completed"].astype(bool)
        & raw["season_type"].isin([3, 5])
    ].copy()
    game_type = completed["season_type"].map(
        {3: "playoff", 5: "play_in"}
    )
    frame = pd.DataFrame({
        "season": season,
        "game_date": pd.to_datetime(
            completed["game_date"], errors="raise"
        ).dt.date,
        "game_type": game_type.astype("string"),
        "away_franchise_id": completed[
            "away_display_name"
        ].map(lambda name: franchises.resolve(str(name), season)),
        "home_franchise_id": completed[
            "home_display_name"
        ].map(lambda name: franchises.resolve(str(name), season)),
        "away_pts": pd.to_numeric(
            completed["away_score"], errors="raise"
        ).astype("int64"),
        "home_pts": pd.to_numeric(
            completed["home_score"], errors="raise"
        ).astype("int64"),
    })
    return frame.sort_values(
        ["game_date", "game_type", "away_franchise_id",
         "home_franchise_id"],
        kind="stable",
    ).reset_index(drop=True)
```

Before returning, require all published fields non-null, different
franchises, nonnegative scores, non-tied finals, and no duplicate
`CROSS_SOURCE_KEY` values.

- [ ] **Step 4: Run unit and pinned-cache integration tests**

Add an integration test:

```python
@pytest.mark.integration
def test_full_pinned_postseason_evidence(repo_data_dir):
    evidence = pd.concat(
        [
            hoopr.load_postseason(
                repo_data_dir / "raw", season
            )
            for season in range(2020, 2027)
        ],
        ignore_index=True,
    )
    assert len(evidence) == 627
    assert evidence["game_type"].value_counts().to_dict() == {
        "playoff": 590,
        "play_in": 37,
    }
```

Run:

```bash
pytest tests/test_hoopr.py -q
pytest tests/test_hoopr.py -m integration -q
```

Expected: both commands PASS; integration reports 627 rows.

- [ ] **Step 5: Prove the regular hoopR adapter did not change**

Run:

```bash
pytest tests/test_build_gates.py tests/test_playoff_non_interference.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/nba_data/hoopr.py tests/test_hoopr.py
git commit -m "feat: expose hoopr postseason evidence"
```

---

### Task 7: Assemble the typed table and enforce every acceptance gate

**Files:**

- Modify: `src/nba_data/playoff_build.py`
- Modify: `tests/test_playoff_build.py`

**Interfaces:**

- Consumes:
  `playoff_bref.load_season(raw_dir: Path, season: int)`,
  `franchises.resolve(name: str, season: int)`,
  `hoopr.load_postseason(raw_dir: Path, season: int)`,
  the Task 5 references, `data/parquet/games.parquet`, and
  `data/parquet/historical_games.parquet`.
- Produces:
  `PLAYOFF_COLUMNS`, `PLAYOFF_DTYPES`, `CROSS_SOURCE_KEY`,
  `REGULAR_OVERLAP_KEY`,
  `_enforce_schema(frame: pd.DataFrame) -> pd.DataFrame`,
  `validate(games: pd.DataFrame, expected_counts:
  dict[int, ExpectedSeason], *, evidence: pd.DataFrame,
  hoopr_games: pd.DataFrame, modern_regular: pd.DataFrame,
  historical_regular: pd.DataFrame,
  site_rule_matches: dict[int, int]) -> dict`, and
  `assemble(data_dir: Path) -> tuple[pd.DataFrame, dict, dict]`.
- The third `assemble()` return is an evidence bundle with keys
  `source_rows`, `hoopr_games`, `modern_regular`, `historical_regular`, and
  `site_rule_matches`; Task 8 reuses it for the Parquet reread check.

- [ ] **Step 1: Write failing assembly-orientation and schema tests**

Add a test that monkeypatches the 80 source loaders to return one valid row
per season and checks the exact ordered contract:

```python
def test_assemble_resolves_franchises_and_enforces_schema(
    tmp_path, monkeypatch, valid_source_rows, valid_references
):
    seed_regular_publications(tmp_path, valid_source_rows)
    monkeypatch.setattr(
        playoff_build.playoff_bref,
        "load_season",
        lambda raw_dir, season: valid_source_rows[season].copy(),
    )
    monkeypatch.setattr(
        playoff_build.hoopr,
        "load_postseason",
        fake_hoopr_loader(valid_source_rows),
    )

    games, report, evidence = playoff_build.assemble(tmp_path)

    assert games.columns.tolist() == playoff_build.PLAYOFF_COLUMNS
    assert games["home_margin"].equals(
        games["home_pts"] - games["away_pts"]
    )
    assert games.sort_values(
        ["season", "game_date", "game_id"], kind="stable"
    ).reset_index(drop=True).equals(games)
    assert report["season_range"] == [1947, 2026]
    assert set(evidence) == {
        "source_rows",
        "hoopr_games",
        "modern_regular",
        "historical_regular",
        "site_rule_matches",
    }
```

Assert every dtype in `PLAYOFF_DTYPES`, `game_date` values are
`datetime.date`, away/home orientation is unchanged, and a franchise lookup
failure propagates with the team name and season.

- [ ] **Step 2: Write one failing test per validation family**

Use a valid complete synthetic frame and mutate exactly one property in each
parametrized case:

```python
@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("duplicate_column", "columns and dtypes"),
        ("wrong_dtype", "columns and dtypes"),
        ("missing_season", "seasons must be exactly 1947..2026"),
        ("wrong_league", "league/season mismatch"),
        ("unknown_game_type", "unsupported game_type"),
        ("duplicate_game_id", "game_id must be globally unique"),
        ("null_required", "required playoff value is null"),
        ("same_team", "teams and franchises must differ"),
        ("negative_score", "scores must be nonnegative"),
        ("tied_score", "final scores must be non-tied"),
        ("bad_margin", "home_margin mismatch"),
        ("negative_overtime", "overtime periods must be nonnegative"),
        ("marker_mismatch", "overtime marker mismatch"),
        ("bad_attendance_status", "attendance/status mismatch"),
        ("bad_site_status", "unsupported site_status"),
        ("bad_count", "season/type count mismatch"),
        ("outside_window", "outside curated postseason window"),
        ("bad_voided_exemption", "invalid voided-game exemption"),
        ("regular_overlap", "overlaps regular publication"),
        ("hoopr_missing", "BREF/hoopR postseason mismatch"),
        ("hoopr_extra", "BREF/hoopR postseason mismatch"),
        ("wrong_2020_site_matches", "2020 neutral-site rule mismatch"),
    ],
)
def test_validate_rejects_each_gate(
    complete_validation_case, mutation, message
):
    arguments = mutate_validation_case(
        complete_validation_case, mutation
    )
    with pytest.raises(
        playoff_build.PlayoffValidationError,
        match=message,
    ):
        playoff_build.validate(**arguments)
```

Add positive edge tests for null venue, null box-score URL on a historical
fallback ID, reported attendance zero, missing attendance, five overtime
periods, the exact 1953 voided exemption, and different game-ID namespaces
sharing no canonical overlap.

- [ ] **Step 3: Run the new builder tests**

Run:

```bash
pytest tests/test_playoff_build.py -q
```

Expected: FAIL because `_enforce_schema`, `validate`, and `assemble` are not
defined.

- [ ] **Step 4: Implement schema enforcement and deterministic season hashes**

Implement:

```python
def _enforce_schema(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.loc[:, PLAYOFF_COLUMNS].copy()
    output["game_date"] = pd.to_datetime(
        output["game_date"], errors="raise"
    ).dt.date
    for column, dtype in PLAYOFF_DTYPES.items():
        output[column] = output[column].astype(dtype)
    return output.sort_values(
        ["season", "game_date", "game_id"], kind="stable"
    ).reset_index(drop=True)


def _season_sha256(frame: pd.DataFrame) -> str:
    payload = frame.to_csv(
        index=False,
        lineterminator="\n",
        na_rep="",
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
```

Before `_enforce_schema()`, resolve both franchise columns, apply the site
rules, and calculate `home_margin`. Save the parser-only evidence by `game_id`
before dropping internal columns.

- [ ] **Step 5: Implement structural, arithmetic, and source-evidence gates**

`validate()` first checks exact ordered columns and dtypes without allowing
duplicate-column masking. Then collect all errors and raise one
semicolon-separated `PlayoffValidationError`.

Require:

```python
expected_seasons = list(range(1947, 2027))
expected_types = {
    "playoff", "play_in", "tiebreaker", "voided"
}
expected_league = games["season"].map(
    lambda season: "BAA" if season <= 1949 else "NBA"
)
```

Non-null columns are every published column except `venue_name`,
`attendance`, and `boxscore_url`. Enforce globally unique non-null game IDs,
different source teams and franchises, nonnegative integer scores,
non-tied finals, exact margin arithmetic, nonnegative overtime, and exact
overtime re-decoding from the one-to-one evidence row:

```python
decoded = evidence["overtime_marker"].map(
    bref_schedule.overtime_periods
)
```

Attendance rules are:

```python
missing = games["attendance"].isna()
zero = games["attendance"].eq(0).fillna(False)
positive = games["attendance"].gt(0).fillna(False)
attendance_ok = (
    (missing & games["attendance_status"].eq("missing"))
    | (zero & games["attendance_status"].eq("reported_zero"))
    | (
        positive
        & games["attendance_status"].eq("reported_positive")
    )
)
```

Require `site_status` in `{"home", "neutral", "unknown"}` and
`site_rule_matches == {2020: 84}`. Then require all 84 season-2020 rows to be
neutral and no fewer/no more than 84 rows exist for that season.

- [ ] **Step 6: Implement count, window, voided, and overlap gates**

For every expected season, compare the actual four type counts and total
against the `ExpectedSeason` fields. For `playoff`, `play_in`, and
`tiebreaker`, require:

```python
expected.first_postseason_date <= row.game_date
row.game_date <= expected.last_postseason_date
```

The only row allowed outside its window is:

```python
{
    "game_id": "bref-195211280MLH",
    "season": 1953,
    "game_date": date(1952, 11, 28),
    "game_type": "voided",
}
```

Require exactly one `voided` row and exact equality to that identity. Normalize
both regular tables to `REGULAR_OVERLAP_KEY`, concatenate them, and fail if an
inner merge with the playoff frame returns any row. Do not compare game IDs.

- [ ] **Step 7: Implement bidirectional hoopR reconciliation**

Filter BREF games to 2020–2026 and `playoff`/`play_in`. Select
`CROSS_SOURCE_KEY`, assert uniqueness in both frames, then use an outer merge:

```python
comparison = bref_keys.merge(
    hoopr_keys,
    on=CROSS_SOURCE_KEY,
    how="outer",
    indicator=True,
    validate="one_to_one",
)
if len(comparison) != 627 or not comparison["_merge"].eq("both").all():
    raise PlayoffValidationError(
        "BREF/hoopR postseason mismatch"
    )
```

Include sorted `left_only` and `right_only` key samples in the exception so a
source mismatch is diagnosable without bypassing the gate.

- [ ] **Step 8: Build the deterministic report and assembly function**

Create report maps with string season keys for JSON stability:

```python
games_by_season = {
    str(int(season)): int(count)
    for season, count in games.groupby(
        "season", sort=True
    ).size().items()
}
games_by_type = {
    str(game_type): int(count)
    for game_type, count in games.groupby(
        "game_type", sort=True
    ).size().items()
}
season_sha256 = {
    str(season): _season_sha256(
        games.loc[games["season"].eq(season)]
    )
    for season in range(1947, 2027)
}
```

Build the complete report shape from Shared Contracts, using integer values
only. `assemble()` loads all 80 BREF frames, resolves franchises, applies site
rules, enforces schema, reads both regular publications with only overlap
columns, loads and concatenates seven hoopR evidence frames, validates, and
returns `(games, report, evidence_bundle)`.

- [ ] **Step 9: Run the builder tests**

Run:

```bash
pytest tests/test_playoff_build.py -q
```

Expected: PASS.

- [ ] **Step 10: Run all source and identity regressions**

Run:

```bash
pytest tests/test_bref_schedule.py \
  tests/test_playoff_bref.py \
  tests/test_playoff_build.py \
  tests/test_franchises.py \
  tests/test_hoopr.py -q
```

Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add src/nba_data/playoff_build.py tests/test_playoff_build.py
git commit -m "feat: validate complete playoff results panel"
```

---

### Task 8: Publish an attested pair and expose the CLI

**Files:**

- Create: `src/nba_data/publication.py`
- Create: `tests/test_publication.py`
- Modify: `src/nba_data/historical_build.py:615-743,746-811`
- Modify: `src/nba_data/playoff_build.py`
- Modify: `src/nba_data/__main__.py:1-71`
- Modify: `tests/test_historical_build.py:1030-1130`
- Modify: `tests/test_playoff_build.py`
- Modify: `tests/test_cli.py:170-225`

**Interfaces:**

- Consumes:
  `playoff_build.assemble(data_dir)`,
  `historical_build._raw_provenance(data_dir)`,
  `playoff_bref.validate_cache_inventory(raw_dir, complete=True)`,
  `build._table_manifest(path, frame)`, and
  `build._dependency_versions(project_dir)`.
- Produces:
  `publication.PairPublicationError`,
  `publication.publish_pair(stage_dir: Path, output_dir: Path,
  products: tuple[str, str], *, label: str) -> Path | None`,
  `playoff_build._raw_provenance(data_dir: Path) -> dict`,
  `playoff_build._reference_provenance(data_dir: Path) -> dict`,
  `playoff_build.run(data_dir: Path) -> None`, and CLI commands
  `fetch-playoffs`/`build-playoffs`.

- [ ] **Step 1: Write failing generic publisher tests**

Create `tests/test_publication.py` by extracting the behavioral cases from
the historical publisher tests. Use neutral product names:

```python
PRODUCTS = ("games.parquet", "manifest.json")


def test_publish_pair_replaces_both_products(tmp_path):
    stage = tmp_path / "stage"
    output = tmp_path / "output"
    stage.mkdir()
    output.mkdir()
    for name in PRODUCTS:
        (stage / name).write_text(f"new-{name}", encoding="utf-8")
        (output / name).write_text(f"old-{name}", encoding="utf-8")

    retained = publication.publish_pair(
        stage, output, PRODUCTS, label="playoff"
    )

    assert retained is None
    assert {
        name: (output / name).read_text(encoding="utf-8")
        for name in PRODUCTS
    } == {name: f"new-{name}" for name in PRODUCTS}
```

Add tests for an incomplete/extra stage, stage symlink/special file,
incomplete prior pair, prior symlink/special file, failure moving the first
prior product, failure moving the second prior product, failure publishing
the first new product, failure publishing the second new product, successful
rollback, failed rollback with retained recovery path, and committed pair with
rollback-directory cleanup warning.

- [ ] **Step 2: Run publisher tests**

Run:

```bash
pytest tests/test_publication.py -q
```

Expected: collection fails because `nba_data.publication` does not exist.

- [ ] **Step 3: Extract the exact transactional publisher**

Move the exact bodies of `_restore_historical_products()` and
`_publish_historical_pair()` from `historical_build.py` into
`publication.py`. Rename the first `_restore_products`, rename the second
`publish_pair`, and make these mechanical substitutions throughout the moved
body:

```text
HISTORICAL_PRODUCTS -> products
HistoricalValidationError -> PairPublicationError
"historical" in user-facing errors/prefixes -> label
```

Prepend these argument checks to the moved publication body:

```python
def publish_pair(
    stage_dir: Path,
    output_dir: Path,
    products: tuple[str, str],
    *,
    label: str,
) -> Path | None:
    if (
        len(products) != 2
        or len(set(products)) != 2
        or any(Path(name).name != name for name in products)
    ):
        raise PairPublicationError(
            "publication requires two unique basenames"
        )
    staged = sorted(path.name for path in stage_dir.iterdir())
    if staged != sorted(products):
        raise PairPublicationError(
            f"{label} stage must contain exactly "
            f"{sorted(products)}"
        )
```

The moved body retains the existing regular-file checks, prior-pair checks,
rollback moves, staged publication moves, restoration, cleanup, and retained
rollback return in their current order. It raises `PairPublicationError` for contract violations,
propagates the original `OSError` after successful rollback, and raises
`PairPublicationError` chained from cleanup/rollback errors when recovery
data must be retained.

Keep this compatibility wrapper in `historical_build.py`:

```python
def _publish_historical_pair(
    stage_dir: Path, output_dir: Path
) -> Path | None:
    try:
        return publication.publish_pair(
            stage_dir,
            output_dir,
            HISTORICAL_PRODUCTS,
            label="historical",
        )
    except publication.PairPublicationError as exc:
        raise HistoricalValidationError(str(exc)) from exc
```

Update historical fault-injection tests to patch
`publication.os.replace`/`publication.shutil.rmtree`.

- [ ] **Step 4: Prove publisher compatibility and historical byte identity**

Run:

```bash
pytest tests/test_publication.py tests/test_historical_build.py -q
python -m nba_data build-historical
pytest tests/test_playoff_non_interference.py -q
```

Expected: PASS and all seven locked hashes remain exact.

- [ ] **Step 5: Write failing provenance and manifest tests**

Add tests to `tests/test_playoff_build.py` that seed small verified historical,
playoff, and hoopR caches plus reference files, then assert this top-level
manifest contract:

```python
assert manifest.keys() == {
    "schema_version",
    "sources",
    "references",
    "versions",
    "tables",
    "validation",
}
assert manifest["schema_version"] == 1
assert manifest["sources"].keys() == {
    "basketball_reference", "hoopr"
}
assert manifest["tables"].keys() == {"playoff_games"}
assert manifest["tables"]["playoff_games"]["schema"] == [
    {"name": field.name, "arrow_type": str(field.type)}
    for field in pq.read_schema(parquet_path)
]
```

Require source records to contain only deterministic identity fields:

```python
PAGE_FIELDS = {
    "path", "provider", "resource", "league",
    "season", "month", "url", "sha256",
}
SEEDED_PAGE_FIELDS = PAGE_FIELDS | {
    "seeded_from", "seed_operation", "source_sha256"
}
```

Test rejection of a changed historical manifest SHA, historical manifest
whose page records differ from current verified cache provenance, wrong
seeded source hash, extra/missing playoff body, sidecar identity mismatch,
wrong hoopR pin, unreferenced playoff file, absolute path, and fetch timestamp
leaking into the deterministic manifest.

- [ ] **Step 6: Implement exact raw and reference provenance**

`_raw_provenance()` performs these checks in order:

1. Read and SHA-256
   `data/parquet/historical_games_manifest.json`.
2. Call `historical_build._raw_provenance(data_dir)` and require exact
   equality to the historical manifest’s `sources`.
3. Record the historical manifest path/SHA and every consumed historical page
   record.
4. Call `playoff_bref.validate_cache_inventory(raw_dir, complete=True)`;
   read each sidecar and record only the stable identity fields above.
5. For every seeded page, validate the original `raw/bref` body/sidecar again
   and require its SHA to equal both `source_sha256` and the seeded body SHA.
6. For hoopR seasons 2020–2026, call `_read_pinned_cache`, validate the
   provider/season/upstream commit, and record relative path, provider,
   resource (`pinned_schedule`), season, deterministic raw-GitHub URL,
   upstream commit, and SHA-256. Derive the URL exactly as `hoopr.fetch()`
   does:

```python
url = (
    "https://raw.githubusercontent.com/"
    "sportsdataverse/hoopR-nba-data/"
    f"{hoopr.SNAPSHOT['commit']}/nba/schedules/parquet/"
    f"nba_schedule_{season}.parquet"
)
```

Return:

```python
{
    "basketball_reference": {
        "historical_manifest": {
            "path": "parquet/historical_games_manifest.json",
            "sha256": historical_manifest_sha,
        },
        "historical_pages": historical_pages,
        "playoff_pages": playoff_pages,
    },
    "hoopr": {
        "upstream_commit": hoopr.SNAPSHOT["commit"],
        "schedules": hoopr_schedules,
    },
}
```

`_reference_provenance()` attests exactly:

```python
REFERENCE_FILES = (
    "franchises.csv",
    "hoopr_snapshot.json",
    "playoff_season_counts.csv",
    "playoff_site_rules.csv",
)
```

Each value contains the relative `path` and lowercase 64-character `sha256`.
Reject symlinks and non-regular files.

- [ ] **Step 7: Implement deterministic staged build and pair publication**

Add:

```python
PLAYOFF_PRODUCTS = (
    "playoff_games.parquet",
    "playoff_games_manifest.json",
)
```

`run()` performs:

1. `expected_counts = load_expected_counts(data_dir /
   "reference/playoff_season_counts.csv")`, followed by
   `games, report, evidence = assemble(data_dir)`;
2. strict source and reference provenance before staging;
3. a stage directory under `data_dir`;
4. zstd Parquet write with pyarrow and no index;
5. Parquet reread plus `build._assert_staged_frame()` and
   `pd.testing.assert_frame_equal(check_like=False)`;
6. `validate(reread, expected_counts,
   evidence=evidence["source_rows"],
   hoopr_games=evidence["hoopr_games"],
   modern_regular=evidence["modern_regular"],
   historical_regular=evidence["historical_regular"],
   site_rule_matches=evidence["site_rule_matches"])`, requiring the exact
   same report;
7. `build._table_manifest()` plus the full ordered Arrow schema;
8. JSON serialization using
   `json.dumps(..., indent=2, sort_keys=True, allow_nan=False) + "\n"`; and
9. `publication.publish_pair(..., PLAYOFF_PRODUCTS, label="playoff")`.

If publication commits but rollback cleanup fails, emit the same
`RuntimeWarning` pattern as the historical builder. Always remove an
unpublished stage directory in `finally`.

- [ ] **Step 8: Write failing CLI dispatch tests**

Extend `tests/test_cli.py`:

```python
def test_fetch_playoffs_one_season(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        cli.playoff_bref,
        "fetch",
        lambda raw, seasons: calls.append((raw, tuple(seasons))),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "nba_data", "--data-dir", str(tmp_path),
            "fetch-playoffs", "--season", "1984",
        ],
    )
    cli.main()
    assert calls == [
        (tmp_path.resolve() / "raw", (1984,))
    ]


def test_build_playoffs_is_offline_dispatch(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        cli.playoff_build, "run", calls.append
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "nba_data", "--data-dir", str(tmp_path),
            "build-playoffs",
        ],
    )
    cli.main()
    assert calls == [tmp_path.resolve()]
```

Also test that omitted season passes all 43 seasons and that 1983/2027 are
rejected by argparse.

- [ ] **Step 9: Add CLI commands**

Import `playoff_bref` and `playoff_build`. Add:

```python
playoff_fetch = commands.add_parser("fetch-playoffs")
playoff_fetch.add_argument(
    "--season",
    type=int,
    action="append",
    choices=playoff_bref.DEDICATED_SEASONS,
)
commands.add_parser("build-playoffs")
```

Dispatch:

```python
elif args.command == "fetch-playoffs":
    selected = (
        tuple(args.season)
        if args.season
        else playoff_bref.DEDICATED_SEASONS
    )
    playoff_bref.fetch(raw, selected)
elif args.command == "build-playoffs":
    playoff_build.run(data)
```

- [ ] **Step 10: Run publication, builder, and CLI tests**

Run:

```bash
pytest tests/test_publication.py \
  tests/test_historical_build.py \
  tests/test_playoff_build.py \
  tests/test_cli.py -q
```

Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add src/nba_data/publication.py \
  src/nba_data/historical_build.py \
  src/nba_data/playoff_build.py \
  src/nba_data/__main__.py \
  tests/test_publication.py \
  tests/test_historical_build.py \
  tests/test_playoff_build.py \
  tests/test_cli.py
git commit -m "feat: publish attested playoff artifact pair"
```

---

### Task 9: Preserve independent products and exclude independent raw caches

**Files:**

- Modify: `src/nba_data/build.py:149-157,1609-1627,1750-1844`
- Modify: `scripts/archive_raw_snapshot.sh:17-37`
- Modify: `tests/test_build_gates.py:2165-2200`
- Modify: `tests/test_repository_layout.py:289-324,440-560`
- Modify: `tests/test_playoff_non_interference.py`

**Interfaces:**

- Consumes: existing modern atomic directory publication and the two
  independent two-file product contracts.
- Produces:
  `PRESERVED_PLAYOFF_FILES`,
  `_copy_independent_publications(output_dir: Path,
  stage_dir: Path) -> set[str]`, modern snapshot exclusion for both
  independent cache roots, and a full rebuild hash gate.

- [ ] **Step 1: Write failing complete-pair preservation tests**

Extend `tests/test_build_gates.py`:

```python
def test_modern_publish_preserves_both_independent_pairs(
    tmp_path, monkeypatch, valid_modern_build
):
    output = tmp_path / "parquet"
    output.mkdir(parents=True)
    products = {
        **{
            name: f"historical-{name}".encode()
            for name in build.PRESERVED_HISTORICAL_FILES
        },
        **{
            name: f"playoff-{name}".encode()
            for name in build.PRESERVED_PLAYOFF_FILES
        },
    }
    for name, payload in products.items():
        (output / name).write_bytes(payload)

    run_stubbed_modern_build(
        tmp_path, monkeypatch, valid_modern_build
    )

    assert {
        name: (output / name).read_bytes()
        for name in products
    } == products
```

Parametrize orphan and symlink tests over both labels:

```python
@pytest.mark.parametrize(
    ("label", "products"),
    [
        ("historical", build.PRESERVED_HISTORICAL_FILES),
        ("playoff", build.PRESERVED_PLAYOFF_FILES),
    ],
)
def test_modern_publish_rejects_incomplete_independent_pair(
    tmp_path, label, products
):
    output = tmp_path / "parquet"
    stage = tmp_path / "stage"
    output.mkdir()
    stage.mkdir()
    (output / products[0]).write_bytes(b"orphan")
    with pytest.raises(
        build.ValidationError,
        match=rf"{label} publication pair is incomplete",
    ):
        build._copy_independent_publications(output, stage)
```

Add the equivalent non-regular product test by creating both names, replacing
one with a symlink to a regular file outside `output`, and asserting
`non-regular <label> publication product`.

- [ ] **Step 2: Write failing snapshot-exclusion and provenance tests**

In `tests/test_repository_layout.py`, seed files under:

```text
raw/stats_nba/
raw/bref/
raw/hoopr/
raw/historical_bref/
raw/playoff_bref/
```

Run `scripts/archive_raw_snapshot.sh` against a temporary archive root and
assert the snapshot contains the first three and neither independent cache.
Then restore and assert neither `historical_bref` nor `playoff_bref` appears.

Add a build provenance test that creates a deliberately invalid
`data/raw/playoff_bref/unrelated.file` and proves
`build._raw_provenance(data_dir)` returns exactly the same dictionary as
before. The playoff builder, not the modern builder, owns validation of that
directory.

- [ ] **Step 3: Run the focused tests**

Run:

```bash
pytest tests/test_build_gates.py \
  tests/test_repository_layout.py -q
```

Expected: FAIL because playoff pairs are not preserved and the modern archive
still includes `playoff_bref`.

- [ ] **Step 4: Generalize independent pair preservation**

Keep the existing historical constant for compatibility and add:

```python
PRESERVED_PLAYOFF_FILES = (
    "playoff_games.parquet",
    "playoff_games_manifest.json",
)
PRESERVED_PUBLICATION_PAIRS = {
    "historical": PRESERVED_HISTORICAL_FILES,
    "playoff": PRESERVED_PLAYOFF_FILES,
}
```

Replace `_copy_historical_publication()` with:

```python
def _copy_independent_publications(
    output_dir: Path,
    stage_dir: Path,
) -> set[str]:
    preserved = set()
    for label, products in PRESERVED_PUBLICATION_PAIRS.items():
        present = {
            name
            for name in products
            if (
                (output_dir / name).exists()
                or (output_dir / name).is_symlink()
            )
        }
        if present and present != set(products):
            raise ValidationError(
                f"{label} publication pair is incomplete"
            )
        if any(
            (output_dir / name).is_symlink()
            or not (output_dir / name).is_file()
            for name in present
        ):
            raise ValidationError(
                f"non-regular {label} publication product"
            )
        for name in sorted(present):
            shutil.copyfile(
                output_dir / name, stage_dir / name
            )
        preserved.update(present)
    return preserved
```

Call this function from `build.run()`. Keep the staged-file-set equality as
`set(PUBLISHED_FILES) | preserved`, so an unknown output never enters the new
directory.

- [ ] **Step 5: Exclude playoff raw data from modern snapshots**

Change both rsync invocations in `scripts/archive_raw_snapshot.sh` to include
both anchored exclusions:

```bash
--exclude='/historical_bref/' \
--exclude='/playoff_bref/'
```

Do not change `restore_raw.sh`: the archived snapshot is already the
authoritative restored inventory and must not gain either directory.

- [ ] **Step 6: Run seam tests**

Run:

```bash
pytest tests/test_build_gates.py \
  tests/test_repository_layout.py -q
bash -n scripts/archive_raw_snapshot.sh scripts/restore_raw.sh
```

Expected: PASS and Bash syntax exit 0.

- [ ] **Step 7: Add the full rebuild non-interference test**

Extend `tests/test_playoff_non_interference.py`:

```python
@pytest.mark.integration
def test_modern_and_historical_rebuilds_preserve_baseline():
    expected = json.loads(
        BASELINE_PATH.read_text(encoding="utf-8")
    )
    subprocess.run(
        [sys.executable, "-m", "nba_data", "build"],
        cwd=REPO_ROOT,
        check=True,
    )
    subprocess.run(
        [sys.executable, "-m", "nba_data", "build-historical"],
        cwd=REPO_ROOT,
        check=True,
    )
    actual = {
        relative: sha256_file(REPO_ROOT / relative)
        for relative in sorted(expected)
    }
    assert actual == expected
```

This test intentionally operates only against the verified repository cache
and is never part of a fixture-only unit run.

- [ ] **Step 8: Run the integration non-interference gate**

Run:

```bash
pytest tests/test_playoff_non_interference.py -m integration -q
git status --short data/parquet
```

Expected: PASS; no tracked regular artifact is modified.

- [ ] **Step 9: Commit**

```bash
git add src/nba_data/build.py \
  scripts/archive_raw_snapshot.sh \
  tests/test_build_gates.py \
  tests/test_repository_layout.py \
  tests/test_playoff_non_interference.py
git commit -m "feat: preserve independent playoff publication"
```

---

### Task 10: Audit representative sources, complete acquisition, and publish

**Files:**

- Create: `scripts/audit_playoff_source.py`
- Create: `tests/test_playoff_audit.py`
- Create: `docs/audits/2026-07-30-playoff-bref-source-spike.md`
- Create: `data/parquet/playoff_games.parquet`
- Create: `data/parquet/playoff_games_manifest.json`
- Modify only if independently disproved:
  `data/reference/playoff_season_counts.csv`

**Interfaces:**

- Consumes: the completed CLI, verified cache loaders, curated references,
  and builder from Tasks 3–9.
- Produces:
  `audit_playoff_source.audit_cache(data_dir: Path,
  seasons: tuple[int, ...]) -> str`, a deterministic source-spike report, the
  complete 50-body raw cache, and both committed playoff artifacts.

- [ ] **Step 1: Write the failing deterministic audit test**

Create `tests/test_playoff_audit.py`:

```python
from pathlib import Path

from scripts import audit_playoff_source


def test_audit_cache_is_deterministic(
    tmp_path, seeded_playoff_cache
):
    first = audit_playoff_source.audit_cache(
        tmp_path, (1984, 2020)
    )
    second = audit_playoff_source.audit_cache(
        tmp_path, (1984, 2020)
    )
    assert first == second
    assert "# Playoff BREF Source Spike" in first
    assert "| 1984 | dedicated |" in first
    assert "| 2020 | dedicated |" in first
    assert "| 2020 | play_in_month |" in first
    assert "bref-202008150POR" in first
    assert "bref-202008170DEN" in first
    assert "Generated at" not in first
    assert str(tmp_path) not in first
```

Test rejection of a season without a dedicated body and confirm importing the
script performs no I/O.

- [ ] **Step 2: Run the audit test**

Run:

```bash
pytest tests/test_playoff_audit.py -q
```

Expected: collection fails because `scripts.audit_playoff_source` does not
exist.

- [ ] **Step 3: Implement the offline audit**

The script parses verified cache bodies through `playoff_bref`; for each
resource it emits one stable Markdown row with:

```text
season,resource,path,sha256,rows,first_date,last_date,
boxscore_links,venue_present,attendance_present,max_overtime
```

After the table, emit sorted classifier rows as:

```text
<game_id> | <game_date> | <game_type> | <away> <away_pts> |
<home> <home_pts> | <remark>
```

Use relative paths only, sort by `(season, resource, path)`, omit current time,
and end with one newline. CLI arguments are:

```python
parser.add_argument(
    "--data-dir", type=Path, default=Path("data")
)
parser.add_argument(
    "--season", type=int, action="append", required=True
)
parser.add_argument("--output", type=Path, required=True)
```

Write the output with UTF-8 only in `main()`; `audit_cache()` returns the
string and is pure apart from verified cache reads.

- [ ] **Step 4: Run audit unit tests**

Run:

```bash
pytest tests/test_playoff_audit.py -q
```

Expected: PASS with no network.

- [ ] **Step 5: Fetch the four representative dedicated pages**

Run one CLI process so the adapter enforces cadence across requests:

```bash
python -m nba_data fetch-playoffs \
  --season 1984 \
  --season 2002 \
  --season 2020 \
  --season 2026
```

Expected: four verified dedicated cache pairs plus the 2020 and 2026 seeded
play-in pairs. Any block page, HTTP error, schema mismatch, or invalid
box-score link stops the task; do not retry until the root cause and
Basketball Reference access policy are checked.

- [ ] **Step 6: Generate and inspect the four-season source spike**

Run:

```bash
python scripts/audit_playoff_source.py \
  --data-dir data \
  --season 1984 \
  --season 2002 \
  --season 2020 \
  --season 2026 \
  --output docs/audits/2026-07-30-playoff-bref-source-spike.md
```

Inspect and require all four dedicated pages to have one schedule table,
completed scores, valid box-score links, supported overtime markers, and
well-formed venue/attendance values. Require the 2020 and 2026 play-in
classifier rows to come only from seeded pages. Record any source anomaly and
its deterministic handling in the report before proceeding.

- [ ] **Step 7: Run the source-spike test gate**

Run:

```bash
pytest tests/test_bref_schedule.py \
  tests/test_playoff_bref.py \
  tests/test_playoff_audit.py -q
git diff --check
```

Expected: PASS and no whitespace errors.

- [ ] **Step 8: Commit the adapter audit before bulk acquisition**

```bash
git add scripts/audit_playoff_source.py \
  tests/test_playoff_audit.py \
  docs/audits/2026-07-30-playoff-bref-source-spike.md
git commit -m "docs: audit representative playoff sources"
```

- [ ] **Step 9: Complete the resumable rate-limited fetch**

Run:

```bash
python -m nba_data fetch-playoffs
```

Expected: the four cached spike pages are reused; the remaining 39 dedicated
pages are requested sequentially with at least 12 seconds between requests;
the remaining five play-in pages are seeded from verified existing cache
pairs.

- [ ] **Step 10: Validate the exact 50-body cache inventory**

Run:

```bash
python - <<'PY'
from pathlib import Path
from nba_data import playoff_bref

paths = playoff_bref.validate_cache_inventory(
    Path("data/raw"), complete=True
)
assert len(paths) == 50, len(paths)
assert sum(
    path.name.count("_games-") == 0 for path in paths
) == 43
assert sum(
    path.name.count("_games-") == 1 for path in paths
) == 7
print("verified playoff cache bodies:", len(paths))
PY
```

Expected: `verified playoff cache bodies: 50`.

- [ ] **Step 11: Build and reconcile the complete publication**

Run:

```bash
python -m nba_data build-playoffs
```

Expected: all 80 season/type count gates pass; BREF and hoopR match in both
directions for exactly 627 rows; the canonical regular overlap is zero; the
2020 neutral rule matches exactly 84; and both products publish together.

If a curated count/window disagrees, stop and inspect the league summary and
the raw classifier audit. Change the CSV only when the independent summary
was transcribed incorrectly; record the correction in that row’s note and
rerun the loader tests before rebuilding. Never derive a replacement count
solely from the schedule rows being validated.

- [ ] **Step 12: Inspect the manifest contract**

Run:

```bash
jq -e '
  .schema_version == 1
  and (.tables.playoff_games.row_count > 0)
  and (.tables.playoff_games.min_season == 1947)
  and (.tables.playoff_games.max_season == 2026)
  and (.validation.cross_source.expected_rows == 627)
  and (.validation.cross_source.matched_rows == 627)
  and (.validation.regular_overlap_rows == 0)
  and (.validation.neutral_2020_rows == 84)
  and (
    .sources.basketball_reference.playoff_pages
    | length
  ) == 50
  and (
    .sources.basketball_reference.historical_pages
    | length
  ) > 0
' data/parquet/playoff_games_manifest.json
```

Expected: `true`.

- [ ] **Step 13: Prove byte-for-byte offline reproducibility**

Run:

```bash
first="$(
  sha256sum \
    data/parquet/playoff_games.parquet \
    data/parquet/playoff_games_manifest.json
)"
python -m nba_data build-playoffs
second="$(
  sha256sum \
    data/parquet/playoff_games.parquet \
    data/parquet/playoff_games_manifest.json
)"
test "$first" = "$second"
printf '%s\n' "$second"
```

Expected: both SHA-256 lines print and the equality check exits 0.

- [ ] **Step 14: Prove regular non-interference after publication**

Run:

```bash
python -m nba_data build
python -m nba_data build-historical
pytest tests/test_playoff_non_interference.py -q
```

Expected: PASS. The modern rebuild preserves both independent pairs, and all
seven locked regular hashes remain unchanged.

- [ ] **Step 15: Confirm raw data remains untracked**

Run:

```bash
git check-ignore data/raw/playoff_bref/NBA_1984_games.html
git ls-files data/raw/playoff_bref
```

Expected: the first command prints the ignored path; the second prints
nothing.

- [ ] **Step 16: Commit references corrected by independent evidence and both artifacts**

```bash
git add data/reference/playoff_season_counts.csv \
  data/parquet/playoff_games.parquet \
  data/parquet/playoff_games_manifest.json
git commit -m "data: publish playoff game results"
```

---

### Task 11: Add exact playoff raw-cache archive and restore

**Files:**

- Create: `scripts/archive_playoff_raw_snapshot.sh`
- Create: `scripts/restore_playoff_raw.sh`
- Create: `tests/test_playoff_raw_snapshot.py`
- Modify: `tests/test_repository_layout.py`

**Interfaces:**

- Consumes:
  the committed playoff manifest, its pinned historical-manifest SHA, the
  verified 50-body `playoff_bref` cache, and the existing verified
  `historical_bref` cache.
- Produces:
  `playoff-raw-2026-<manifest-sha12>/` snapshots and an exact restore into an
  absent destination.
- Environment overrides:
  `NBA_PLAYOFF_RAW_MANIFEST`, `NBA_PLAYOFF_RAW_SOURCE`,
  `NBA_PLAYOFF_RAW_DESTINATION`, `NBA_PLAYOFF_RAW_ARCHIVE_ROOT`,
  `NBA_PLAYOFF_HISTORICAL_MANIFEST`, and
  `NBA_PLAYOFF_HISTORICAL_CACHE`.

- [ ] **Step 1: Write failing archive/restore inverse tests**

Create `tests/test_playoff_raw_snapshot.py` with a helper that seeds:

```text
data/raw/playoff_bref/                 verified dedicated + seeded pairs
data/raw/historical_bref/              verified historical pairs
data/parquet/playoff_games_manifest.json
data/parquet/historical_games_manifest.json
archive/
```

The manifests must attest every seeded body and pin the historical manifest
SHA. Run scripts with environment overrides and assert:

```python
def test_archive_and_restore_are_exact_inverses(
    playoff_snapshot_fixture
):
    fixture = playoff_snapshot_fixture
    run_archive(fixture)
    shutil.move(
        fixture.playoff_cache,
        fixture.saved_live_cache,
    )
    run_restore(fixture)
    assert tree_sha256(fixture.playoff_cache) == tree_sha256(
        fixture.saved_live_cache
    )
```

Add one independently named test for each rejection:

- source/destination/cache-root symlink;
- special file, nested directory, `.part`, unexpected file;
- orphan body or sidecar;
- body checksum mismatch;
- sidecar provider/resource/league/season/month/URL mismatch;
- duplicate resource identity or URL;
- missing/extra dedicated page;
- missing/extra seeded page;
- seeded `source_sha256` mismatch;
- playoff manifest changed before or during copy;
- source body changed during copy;
- snapshot body changed during restore;
- snapshot manifest mismatch;
- malformed or incomplete `SHA256SUMS`;
- missing, changed, or wrong historical manifest;
- historical cache missing or inconsistent with its manifest;
- existing restore destination;
- existing snapshot path with different content; and
- cross-device publication attempt that does not stage under the destination
  parent.

Use fake `rsync` wrappers in race tests to mutate the named file at the copy
boundary, matching the established historical snapshot test style.

- [ ] **Step 2: Run snapshot tests**

Run:

```bash
pytest tests/test_playoff_raw_snapshot.py -q
```

Expected: FAIL because both scripts are missing.

- [ ] **Step 3: Implement the shared shell validation contract in both scripts**

Both scripts start with:

```bash
#!/usr/bin/env bash
set -euo pipefail
```

They define `validate_playoff_cache`, which requires a non-symlink directory
containing only top-level regular `*.html` and `*.html.meta.json` files with
complete pairs. They define `validate_cache_manifest`, which uses `jq -er` to
extract `.sources.basketball_reference.playoff_pages`, requires 50 unique
records, 43 `playoff_schedule` and seven `play_in_schedule_month` resources,
recomputes deterministic filename/path/URL identities, compares every body
SHA, and checks the exact sidecar identity fields. For seeded pages it also
requires:

```jq
(.seeded_from
  == ("raw/bref/NBA_" + (.season | tostring)
      + "_games-" + .month + ".html"))
and (.seed_operation == "verified_cache_copy")
and (.source_sha256 == .sha256)
```

Both scripts define `validate_historical_dependency`, which:

1. extracts the pinned SHA from
   `.sources.basketball_reference.historical_manifest.sha256`;
2. requires that value to match `^[0-9a-f]{64}$`;
3. hashes the trusted historical manifest and requires equality;
4. calls
   `scripts/archive_historical_raw_snapshot.sh --validate-cache-manifest`
   against the current historical cache and manifest; and
5. rechecks the playoff and historical manifest hashes after validation.

- [ ] **Step 4: Implement immutable archive publication**

`archive_playoff_raw_snapshot.sh` defaults to:

```bash
root="$(cd "$(dirname "$0")/.." && pwd)"
manifest="${NBA_PLAYOFF_RAW_MANIFEST:-$root/data/parquet/playoff_games_manifest.json}"
source="${NBA_PLAYOFF_RAW_SOURCE:-$root/data/raw/playoff_bref}"
archive_root="${NBA_PLAYOFF_RAW_ARCHIVE_ROOT:-/mnt/nas/llm/documents/nba-predictions-archive}"
historical_manifest="${NBA_PLAYOFF_HISTORICAL_MANIFEST:-$root/data/parquet/historical_games_manifest.json}"
historical_cache="${NBA_PLAYOFF_HISTORICAL_CACHE:-$root/data/raw/historical_bref}"
```

Pin both manifests into read-only temporary files, derive:

```bash
manifest_sha="$(sha256sum "$trusted_manifest" | cut -d' ' -f1)"
snapshot="$archive_root/playoff-raw-2026-${manifest_sha:0:12}"
```

Validate source and historical dependency, stage with `mktemp -d` directly
under `archive_root`, rsync the cache into `playoff_bref/`, copy the trusted
manifest, generate canonical `SHA256SUMS` from sorted top-level cache files,
then validate source, stage, both manifest pins, and a checksum dry run again.
Publish with `mv -T -n`. If the immutable snapshot already exists, accept it
only after exact validation and print its verified path; never overwrite it.

- [ ] **Step 5: Implement absent-target atomic restore**

`restore_playoff_raw.sh` uses the same manifest SHA to select the snapshot,
requires the destination not to exist, validates the snapshot and historical
dependency, stages with `mktemp -d` under the destination parent, rsyncs
`snapshot/playoff_bref/` into the stage, and runs:

```bash
changes="$(
  rsync -a --checksum --dry-run --itemize-changes --delete \
    "$snapshot/playoff_bref/" "$staging/"
)"
test -z "$changes"
```

Revalidate the snapshot, staged cache, trusted playoff-manifest pin,
historical-manifest pin, and historical cache immediately before
`mv -T -n "$staging" "$destination"`. The restore never writes
`historical_bref` and never merges into an existing playoff destination.

- [ ] **Step 6: Run shell syntax and inverse tests**

Run:

```bash
bash -n scripts/archive_playoff_raw_snapshot.sh \
  scripts/restore_playoff_raw.sh
pytest tests/test_playoff_raw_snapshot.py -q
```

Expected: PASS.

- [ ] **Step 7: Add repository-layout recovery assertions**

Require both scripts to be tracked and executable, require their default
archive root to match the design.

Run:

```bash
pytest tests/test_repository_layout.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit the recovery implementation**

```bash
chmod +x scripts/archive_playoff_raw_snapshot.sh \
  scripts/restore_playoff_raw.sh
git add scripts/archive_playoff_raw_snapshot.sh \
  scripts/restore_playoff_raw.sh \
  tests/test_playoff_raw_snapshot.py \
  tests/test_repository_layout.py
git commit -m "feat: archive and restore playoff raw cache"
```

- [ ] **Step 9: Create or verify the immutable NAS snapshot**

Run:

```bash
bash scripts/archive_playoff_raw_snapshot.sh
```

Expected: prints one verified path matching
`/mnt/nas/llm/documents/nba-predictions-archive/playoff-raw-2026-<12 hex>`.

- [ ] **Step 10: Independently restore-verify without touching the live cache**

Run:

```bash
restore_parent="$(mktemp -d)"
trap 'rm -rf -- "$restore_parent"' EXIT
NBA_PLAYOFF_RAW_DESTINATION="$restore_parent/playoff_bref" \
  bash scripts/restore_playoff_raw.sh
(
  cd data/raw/playoff_bref
  find -P . -mindepth 1 -maxdepth 1 -type f -print0 \
    | LC_ALL=C sort -z \
    | xargs -0 sha256sum
) > "$restore_parent/live.sha256"
(
  cd "$restore_parent/playoff_bref"
  find -P . -mindepth 1 -maxdepth 1 -type f -print0 \
    | LC_ALL=C sort -z \
    | xargs -0 sha256sum
) > "$restore_parent/restored.sha256"
cmp -s "$restore_parent/live.sha256" \
  "$restore_parent/restored.sha256"
rm -rf -- "$restore_parent"
trap - EXIT
```

Expected: restore prints a verified destination and both comparisons exit 0.

---

### Task 12: Document operation and run whole-branch acceptance

**Files:**

- Modify: `README.md:28-96`
- Modify: `docs/data-dictionary.md:1-30,300-400`
- Modify: `tests/test_repository_layout.py`
- Modify: `docs/superpowers/plans/2026-07-30-playoff-game-results.md`

**Interfaces:**

- Consumes: all completed code, artifacts, audits, manifests, and recovery
  scripts.
- Produces: operator-facing documentation, a fully checked plan status, and a
  reviewed unmerged implementation branch.

- [ ] **Step 1: Write failing documentation assertions**

Assert README/data dictionary contain:

```text
playoff_games.parquet
playoff_games_manifest.json
fetch-playoffs
build-playoffs
archive_playoff_raw_snapshot.sh
restore_playoff_raw.sh
playoff-raw-2026-
game_type
```

Also assert the data dictionary lists all 19 ordered columns and four exact
`game_type` values.

- [ ] **Step 2: Run documentation tests**

Run:

```bash
pytest tests/test_repository_layout.py -q
```

Expected: FAIL because playoff documentation is incomplete.

- [ ] **Step 3: Update README operator flow**

Document, in order:

```bash
# One season or resumable full acquisition; networked and rate-limited.
python -m nba_data fetch-playoffs --season 1984
python -m nba_data fetch-playoffs

# Strictly offline deterministic publication.
python -m nba_data build-playoffs

# Independent immutable raw recovery.
bash scripts/archive_playoff_raw_snapshot.sh
bash scripts/restore_playoff_raw.sh
```

State that the table is separate from modeling inputs, covers BAA/NBA ending
seasons 1947–2026, uses BREF publication rows plus hoopR cross-validation,
and leaves both regular publications byte-identical.

- [ ] **Step 4: Update the data dictionary**

Add:

- all 19 fields in exact publication order with dtype/null semantics;
- definitions of the four `game_type` values;
- ending-season convention and BAA/NBA league boundary;
- away/home score orientation versus `site_status`;
- 2020’s exact 84-row neutral cohort;
- attendance status behavior, including zero versus missing;
- BREF historical/dedicated/seeded source ownership;
- hoopR’s validator-only role and exact 627-row equality key;
- canonical regular-overlap key;
- deterministic manifest/source/reference contents;
- offline rebuild and two-build byte identity;
- snapshot family, absent-destination restore, and historical restore
  prerequisite; and
- limitations from the spec’s Non-goals section.

- [ ] **Step 5: Run documentation and focused feature tests**

Run:

```bash
pytest tests/test_repository_layout.py \
  tests/test_bref_schedule.py \
  tests/test_playoff_bref.py \
  tests/test_playoff_build.py \
  tests/test_publication.py \
  tests/test_playoff_audit.py \
  tests/test_playoff_raw_snapshot.py \
  tests/test_playoff_non_interference.py -q
```

Expected: PASS.

- [ ] **Step 6: Run the complete default suite**

Run:

```bash
pytest -q
```

Expected: all tests PASS with zero unexpected skips or xfails.

- [ ] **Step 7: Run integration and repository verification**

Run:

```bash
pytest -m integration -q
python scripts/verify_repository.py
```

Expected: all integration tests PASS; repository verification exits 0.

- [ ] **Step 8: Repeat deterministic and non-interference gates**

Run:

```bash
first="$(
  sha256sum \
    data/parquet/playoff_games.parquet \
    data/parquet/playoff_games_manifest.json
)"
python -m nba_data build-playoffs
python -m nba_data build
python -m nba_data build-historical
second="$(
  sha256sum \
    data/parquet/playoff_games.parquet \
    data/parquet/playoff_games_manifest.json
)"
test "$first" = "$second"
pytest tests/test_playoff_non_interference.py -q
```

Expected: equality and fixed-hash gates both pass.

- [ ] **Step 9: Run static hygiene checks**

Run:

```bash
git diff --check
bash -n scripts/archive_raw_snapshot.sh \
  scripts/archive_playoff_raw_snapshot.sh \
  scripts/restore_playoff_raw.sh
rg -n "T[B]D|TO[D]O|FIX[M]E|skip\\(|xfail" \
  src tests scripts README.md docs/data-dictionary.md
git status --short
```

Expected: no whitespace errors, no unfinished markers or unexplained skips,
and only the intended documentation/plan changes remain.

- [ ] **Step 10: Request whole-branch code review**

Invoke `superpowers:requesting-code-review`. Give the reviewer the approved
spec, this plan, the branch base from `git merge-base master HEAD`, and the
current head. Require review of:

- historical complement correctness;
- cache/provenance identity and strict inventories;
- all validation keys and directionality;
- atomic rollback behavior;
- modern/non-interference seams;
- archive/restore race resistance; and
- absence of network access outside `fetch-playoffs`.

Resolve every substantive finding with TDD and rerun the affected focused
tests plus Steps 6–9.

- [ ] **Step 11: Mark the plan complete and commit documentation**

Add a status immediately below the title:

```markdown
## Status — 2026-07-30

**Complete.** Tasks 1–12 are implemented on `playoff-game-results`; default,
integration, deterministic-publication, non-interference, and independent
recovery gates pass. The branch remains unmerged and unpushed for user review.
```

Check every completed step, then:

```bash
git add README.md docs/data-dictionary.md \
  tests/test_repository_layout.py \
  docs/superpowers/plans/2026-07-30-playoff-game-results.md
git commit -m "docs: document playoff results operation"
```

- [ ] **Step 12: Final clean-state verification**

Invoke `superpowers:verification-before-completion`, then run:

```bash
git status --short
git log --oneline --decorate -12
```

Expected: clean worktree; intentional Task 1–12 commits on
`playoff-game-results`; no merge commit and no push performed.

---

## Spec Coverage Matrix

| Approved design requirement | Implemented by |
|---|---|
| Separate 19-column publication and four labels | Tasks 4, 7, 8 |
| Historical archived-page reuse and proven boundaries | Tasks 2, 4 |
| 43 dedicated pages and seven verified play-in copies | Tasks 3, 10 |
| TLS/User-Agent/12-second serial access policy | Task 3 |
| Four-season source spike | Task 10 |
| Stable IDs, overtime, attendance, venue semantics | Tasks 2, 7 |
| Franchise resolution and score orientation | Task 7 |
| Independent 80-season counts/windows | Task 5 |
| Exact 2020 neutral cohort | Tasks 5, 7 |
| Bidirectional 627-row hoopR reconciliation | Tasks 6, 7 |
| Canonical overlap against both regular tables | Task 7 |
| Full schema/count/window/source/cache gates | Tasks 3, 5, 7, 8 |
| Deterministic manifest and per-season hashes | Tasks 7, 8, 10 |
| Atomic pair and rollback | Task 8 |
| Exact CLI | Tasks 8, 10 |
| Modern pair preservation and raw exclusion | Task 9 |
| Seven-artifact byte non-interference | Tasks 1, 2, 9, 10, 12 |
| Exact raw snapshot/restore and historical dependency | Task 11 |
| Operator/schema/recovery documentation | Task 12 |
| Non-goals protected | Global Constraints and Tasks 7, 12 |

## Completion Definition

The implementation is complete only when all of the following are true:

- the worktree is clean on the unmerged `playoff-game-results` branch;
- the complete default and integration suites pass;
- the publication contains exactly the independently curated rows for ending
  seasons 1947–2026;
- the 2020–2026 BREF/hoopR key sets are exactly equal at 627 rows;
- neither regular table has a canonical overlap;
- consecutive offline builds reproduce both playoff artifact hashes;
- modern and historical rebuilds preserve the seven locked baseline hashes;
- the manifest attests the complete historical, playoff, hoopR, and reference
  inventories without machine-specific values;
- the immutable NAS snapshot validates and restores to a separate absent
  destination byte-for-byte; and
- no merge or push has occurred without explicit user instruction.
