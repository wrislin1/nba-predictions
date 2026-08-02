# Play-by-Play Archive Implementation Plan

> **Status: COMPLETE (2026-08-02).** All ten tasks executed and reviewed. Full
> acquisition: 38,023 games / 18,303,908 events (ending seasons 1997–2026);
> two byte-identical builds; immutable snapshot `pbp-archive-2026-e6c0a2518673`
> (manifest SHA-256 `e6c0a2518673ea14ce15caf3184dc9d11b26b1ac1c4a6e1f2a881fe3df06b0de`)
> with independently verified restore. Acceptance record:
> `docs/audits/2026-07-31-play-by-play-archive.md`. Four reviewed amendments
> landed during acquisition: positional MATCHUP home/away derivation
> (`8d7b0be`), reviewed terminal-score-mismatch allowlist (`75c0716`,
> populated `6b71ecc`), implicit 0-0 running-score start (`9c814b6`), and any
> first numeric score pair (`4299948`).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Acquire, normalize, validate, publish, and recovery-test every NBA regular-season, playoff, and play-in `PlayByPlayV3` response for ending seasons 1997–2026.

**Architecture:** A network-owning `pbp` module builds exact LeagueGameFinder worklists, cross-validates them against the integrated regular-season and playoff publications, and fetches transport-valid raw PBP pairs through a serialized retrying client. An offline `pbp_build` module parses the fixed V3 projection one season at a time, derives running scores, writes deterministic Parquet partitions and a manifest into a staged tree, and transactionally publishes the tree. Dedicated scripts archive and restore-verify the raw cache and publication as one immutable NAS recovery unit.

**Tech Stack:** Python 3.12, pandas, NumPy, PyArrow/Parquet with Zstandard compression, `nba_api` endpoint parameter definitions and browser headers, requests, pytest, Bash, rsync, SHA-256, Git.

## Global Constraints

- Execute only after the reviewed `playoff-game-results` branch is integrated and `data/parquet/playoff_games.parquet`, `data/parquet/playoff_games_manifest.json`, and `src/nba_data/publication.py` exist.
- At execution time, create or enter an isolated worktree with `superpowers:using-git-worktrees`. Do not implement this plan directly on `master`.
- Approval of this plan is not permission to merge or push. Finish with committed changes on an unmerged branch ready for separate review. Do not push, merge, deploy, or tear down the worktree without explicit direction.
- Python support remains `>=3.12`; use only existing direct dependencies. Do not add another package.
- Deterministic rebuild and restore verification run from the repository's
  pinned `requirements.txt`; package/Python versions must equal the manifest
  before byte comparison.
- Ending seasons are exactly the integers `1997..2026`.
- Game types are exactly `regular`, `playoff`, and `play_in`.
- `fetch-pbp` is the only network path. `build-pbp`, unit tests, integration tests, audit rendering, archive validation, and restore validation are offline.
- The only source request host is `stats.nba.com`, derived from
  `NBAStatsHTTP.base_url`; this workflow never requests `stats-prod.nba.com`.
- Fetch all 67 dedicated worklists. Do not seed or modify the older `data/raw/stats_nba/leaguegamefinder_<season>.json` pairs because their sidecars lack exact request-parameter metadata.
- Worklist requests are Regular Season and Playoffs for all 30 seasons plus PlayIn for ending seasons 2020–2026.
- PBP request starts are at least 1.5 seconds apart and never parallel.
- Transient request failures receive five total attempts with retry delays 5, 10, 20, and 40 seconds. Honor a larger numeric `Retry-After` up to 120 seconds; stop when it exceeds 120.
- Cache bodies and sidecars use the existing `nba_data.cache` atomic publication contract. Complete valid pairs are never silently overwritten or re-fetched.
- Fetch-time transport validation preserves a nonempty, correct-game JSON envelope before fixed-schema parsing. Schema drift remains cached raw evidence but blocks publication.
- The event primary key is `(game_id, action_id)`. `action_number` duplicates and non-monotonic values are valid source behavior.
- Preserve the exact source score strings in `score_home_raw` and `score_away_raw`; derive non-null running scores separately.
- The Parquet projection is fixed. A missing or additional V3 action key fails until the design, parser, and fixtures are reviewed together.
- A PBP terminal-score mismatch against the worklist (a known, narrow class of early-source error: points misattributed between teams, or events dropped/truncated by the source) does not stop `fetch-pbp`, which logs it and continues; publishing it still requires a human-reviewed entry in the committed `src/nba_data/reference/pbp_score_mismatches.json` allowlist recording both the worklist and PBP-derived pairs. Schema drift (a missing or additional V3 action key) still blocks both fetch continuation assumptions and publication exactly as before.
- Write one deterministic `events.parquet` file for every `season=1997` through `season=2026`, with rows sorted by `game_id, action_id`.
- `data/raw/stats_nba/pbp/` and `data/parquet-large/` remain gitignored. No raw response, PBP manifest, or PBP Parquet file is committed.
- The repository's fresh-clone Git size remains below 100 MiB.
- Before game fetching, require `available_local_bytes >= uncached_game_count * 1_048_576 + 10_737_418_240`.
- Before archive or restore copying, require `destination_available_bytes >= source_allocated_bytes + destination_total_bytes * 0.20`.
- The immutable snapshot root is `/mnt/nas/llm/documents/nba-predictions-archive/pbp-archive-2026-<manifest-sha12>/`.
- Unit tests use committed small fixtures and never access the network. Integration tests read checksum-valid local caches only.
- Do not start the bulk fetch until Tasks 1–8 pass.
- Derived pace, clutch, foul-timing, scoring-run, timeout, and garbage-time features remain out of scope.

---

## File Structure

### Create

`src/nba_data/pbp.py`
    Worklist identities and paths, LeagueGameFinder normalization and cross-validation, exact raw-pair metadata, inventory validation, request pacing/retries, capacity checks, and resumable worklist/PBP acquisition.

`src/nba_data/pbp_build.py`
    Fixed action schema, clock and running-score normalization, per-game validation, per-season streaming build, deterministic Parquet writing, source provenance, manifest/report assembly, publication validation, and audit rendering.

`src/nba_data/pbp_snapshot.py`
    Offline snapshot layout/checksum/manifest validation and raw-to-publication rebuild comparison used by both recovery scripts.

`tests/fixtures/pbp/worklist_regular_1997.json`
`tests/fixtures/pbp/worklist_playoff_1997.json`
`tests/fixtures/pbp/worklist_play_in_2020.json`
    Two-team LeagueGameFinder result sets for the verified representative games.

`tests/fixtures/pbp/early_score_sentinel.json`
`tests/fixtures/pbp/modern_blank_score.json`
    Small V3 action extracts with the exact 23-key action contract, duplicate `actionNumber`, historical `0`/`0` sentinels, modern blanks, and overtime periods.

`tests/test_pbp.py`
    Worklist, cache, inventory, request, retry, pacing, capacity, and resumable-fetch contracts.

`tests/test_pbp_build.py`
    Event schema, normalization, validation, partitions, manifest, reproducibility, publication, and audit rendering.

`tests/test_pbp_raw_snapshot.py`
    Archive/restore inverse, manifest pin, checksum, structure, race, capacity, and two-destination publication tests.

`scripts/archive_pbp_snapshot.sh`
`scripts/restore_pbp_snapshot.sh`
    Immutable combined snapshot and exact absent-target restore.

`docs/audits/2026-07-31-play-by-play-archive.md`
    Generated only after full acquisition, two reproducible builds, immutable archive publication, and independent restore verification.

### Modify

`src/nba_data/publication.py`
    Retain the integrated `publish_pair`/`PairPublicationError` behavior
    unchanged; add safe transactional publication of one directory tree.

`tests/test_publication.py`
    Add tree replacement, rollback, non-regular entry, and retained-recovery coverage.

`src/nba_data/__main__.py`
    Add `fetch-pbp [--season END_YEAR]` and offline `build-pbp`.

`tests/test_cli.py`
    Verify one-season/full-range PBP dispatch, range rejection, and offline build dispatch.

`.gitignore`
    Add the exact `data/parquet-large/` rule.

`scripts/verify_repository.py`
`tests/test_verify_repository.py`
    Require the `data/parquet-large/` ignore rule.

`tests/test_repository_layout.py`
    Protect the large-data paths, require final recovery/audit artifacts, and verify the PBP data-dictionary schema and commands.

`docs/data-dictionary.md`
    Document the logical event schema, source/derived field distinction, worklist gates, manifest, commands, and recovery.

`README.md`
    Add the PBP archive to repository layout/status and link the data dictionary and final audit.

### Generated but never committed

```text
data/raw/stats_nba/pbp/worklists/<season>/<game_type>.json
data/raw/stats_nba/pbp/worklists/<season>/<game_type>.json.meta.json
data/raw/stats_nba/pbp/games/<season>/<game_id>.json
data/raw/stats_nba/pbp/games/<season>/<game_id>.json.meta.json
data/parquet-large/pbp/_manifest.json
data/parquet-large/pbp/season=<season>/events.parquet
```

## Shared Contracts

### Constants and paths

Define these names in `src/nba_data/pbp.py`:

```python
SEASONS = tuple(range(1997, 2027))
PLAY_IN_SEASONS = tuple(range(2020, 2027))
GAME_TYPES = ("regular", "playoff", "play_in")
SEASON_TYPES = {
    "regular": "Regular Season",
    "playoff": "Playoffs",
    "play_in": "PlayIn",
}
ID_PREFIXES = {
    "regular": "002",
    "playoff": "004",
    "play_in": "005",
}
WORKLIST_COLUMNS = [
    "game_id",
    "season",
    "game_type",
    "game_date",
    "away_franchise_id",
    "home_franchise_id",
    "away_pts",
    "home_pts",
]
WORKLIST_REQUIRED_HEADERS = {
    "SEASON_ID",
    "TEAM_ID",
    "TEAM_NAME",
    "TEAM_ABBREVIATION",
    "GAME_ID",
    "GAME_DATE",
    "MATCHUP",
    "WL",
    "PTS",
}
SOURCE_SPIKE_GAME_IDS = (
    "0029600001",
    "0049600001",
    "0051900111",
    "0022500001",
)
MIN_REQUEST_INTERVAL_S = 1.5
RETRY_DELAYS_S = (5.0, 10.0, 20.0, 40.0)
MAX_RETRY_AFTER_S = 120.0
BYTES_PER_UNCACHED_GAME = 1_048_576
FETCH_STAGING_HEADROOM_BYTES = 10_737_418_240
```

Use this season-label function:

```python
def season_label(season: int) -> str:
    if season not in SEASONS:
        raise ValueError(f"PBP ending season must be 1997..2026: {season}")
    return f"{season - 1}-{season % 100:02d}"
```

Path functions consume the repository `data/` directory:

```python
def worklist_path(data_dir: Path, season: int, game_type: str) -> Path:
    return (
        data_dir
        / "raw/stats_nba/pbp/worklists"
        / str(season)
        / f"{game_type}.json"
    )


def game_path(data_dir: Path, season: int, game_id: str) -> Path:
    return (
        data_dir
        / "raw/stats_nba/pbp/games"
        / str(season)
        / f"{game_id}.json"
    )
```

### Worklist and fetch interfaces

`src/nba_data/pbp.py` produces:

```python
class PBPValidationError(ValueError):
    pass


@dataclass(frozen=True)
class CacheInventory:
    worklist_bodies: Sequence[Path]
    game_bodies: Sequence[Path]
    worklist: pd.DataFrame


@dataclass
class RequestPacer:
    minimum_interval_s: float = MIN_REQUEST_INTERVAL_S
    now: Callable[[], float] = time.monotonic
    sleeper: Callable[[float], None] = time.sleep
    last_started: float | None = None

    def wait(self) -> None:
        current = self.now()
        if self.last_started is not None:
            delay = self.last_started + self.minimum_interval_s - current
            if delay > 0:
                self.sleeper(delay)
                current = self.now()
        self.last_started = current
```

Public functions have these exact signatures:

- `normalize_worklist_payload(payload: bytes, *, season: int, game_type: str) -> pd.DataFrame`
- `load_worklist(data_dir: Path, seasons: Iterable[int], *, require_complete: bool) -> pd.DataFrame`
- `cross_validate_worklist(worklist: pd.DataFrame, data_dir: Path, seasons: Iterable[int]) -> None`
- `publish_worklist_payload(destination: Path, payload: bytes, metadata: Mapping[str, object]) -> Path`
- `publish_game_payload(destination: Path, payload: bytes, metadata: Mapping[str, object]) -> Path`
- `validate_cache_tree(raw_root: Path, *, required_seasons: Iterable[int], require_complete: bool) -> CacheInventory`
- `validate_cache_inventory(data_dir: Path, *, required_seasons: Iterable[int], require_complete: bool) -> CacheInventory`
- `fetch_worklists(data_dir: Path, seasons: Iterable[int], *, session: requests.Session | None = None, pacer: RequestPacer | None = None) -> pd.DataFrame`
- `fetch_games(data_dir: Path, worklist: pd.DataFrame, *, session: requests.Session | None = None, pacer: RequestPacer | None = None) -> None`
- `fetch(data_dir: Path, seasons: Iterable[int] = SEASONS) -> None`

`validate_cache_inventory` is the repository-layout wrapper around
`validate_cache_tree` with `data_dir / "raw/stats_nba/pbp"` and the same
season/completeness arguments. Cache-tree validation is deliberately
self-contained; `fetch` separately invokes
`cross_validate_worklist` against the regular and postseason publications
before requesting a PBP game.

### Event schema

Define in `src/nba_data/pbp_build.py`:

```python
ACTION_SOURCE_KEYS = frozenset({
    "actionNumber",
    "clock",
    "period",
    "teamId",
    "teamTricode",
    "personId",
    "playerName",
    "playerNameI",
    "xLegacy",
    "yLegacy",
    "shotDistance",
    "shotResult",
    "isFieldGoal",
    "scoreHome",
    "scoreAway",
    "pointsTotal",
    "location",
    "description",
    "actionType",
    "subType",
    "videoAvailable",
    "shotValue",
    "actionId",
})

EVENT_COLUMNS = [
    "game_id",
    "action_id",
    "action_number",
    "season",
    "game_type",
    "clock",
    "clock_seconds",
    "period",
    "team_id",
    "team_tricode",
    "person_id",
    "player_name",
    "player_name_i",
    "x_legacy",
    "y_legacy",
    "shot_distance",
    "shot_result",
    "is_field_goal",
    "shot_value",
    "score_home_raw",
    "score_away_raw",
    "score_home",
    "score_away",
    "points_total",
    "location",
    "description",
    "action_type",
    "sub_type",
    "video_available",
]

PBP_ARROW_SCHEMA = pa.schema([
    pa.field("game_id", pa.large_string(), nullable=False),
    pa.field("action_id", pa.int64(), nullable=False),
    pa.field("action_number", pa.int64(), nullable=False),
    pa.field("season", pa.int64(), nullable=False),
    pa.field("game_type", pa.large_string(), nullable=False),
    pa.field("clock", pa.large_string(), nullable=False),
    pa.field("clock_seconds", pa.float64(), nullable=False),
    pa.field("period", pa.int64(), nullable=False),
    pa.field("team_id", pa.int64()),
    pa.field("team_tricode", pa.large_string()),
    pa.field("person_id", pa.int64()),
    pa.field("player_name", pa.large_string()),
    pa.field("player_name_i", pa.large_string()),
    pa.field("x_legacy", pa.int64()),
    pa.field("y_legacy", pa.int64()),
    pa.field("shot_distance", pa.int64()),
    pa.field("shot_result", pa.large_string()),
    pa.field("is_field_goal", pa.int64()),
    pa.field("shot_value", pa.int64()),
    pa.field("score_home_raw", pa.large_string(), nullable=False),
    pa.field("score_away_raw", pa.large_string(), nullable=False),
    pa.field("score_home", pa.int64(), nullable=False),
    pa.field("score_away", pa.int64(), nullable=False),
    pa.field("points_total", pa.int64()),
    pa.field("location", pa.large_string()),
    pa.field("description", pa.large_string()),
    pa.field("action_type", pa.large_string()),
    pa.field("sub_type", pa.large_string()),
    pa.field("video_available", pa.int64()),
])
```

Also define:

```python
@dataclass(frozen=True)
class AuditEvidence:
    source_spike: Sequence[Mapping[str, object]]
    cross_source_validated: bool
    reproducible_build: bool
    snapshot_checksums_verified: bool
    restore_verified: bool
```

Public builder interfaces have these exact signatures:

- `parse_clock(clock: str, period: int) -> float`
- `derive_running_scores(score_home_raw: Iterable[str], score_away_raw: Iterable[str]) -> tuple[list[int], list[int]]`
- `parse_game_payload(payload: bytes, worklist_row: Mapping[str, object]) -> tuple[pd.DataFrame, dict]`
- `build_tree(raw_root: Path, stage_dir: Path, *, seasons: Iterable[int]) -> dict`
- `validate_publication(raw_root: Path, publication_dir: Path) -> dict`
- `run(data_dir: Path) -> None`
- `render_audit(manifest: dict, evidence: AuditEvidence, *, manifest_sha256: str, snapshot_name: str) -> str`

`run` derives `raw_root = data_dir / "raw/stats_nba/pbp"`. Accepting the raw
root directly in the offline build/validation interfaces makes the combined
snapshot independently rebuildable at any restore location.

### Manifest shape

`_manifest.json` has this deterministic top-level structure:

```json
{
  "schema_version": 1,
  "scope": {
    "ending_seasons": [
      1997, 1998, 1999, 2000, 2001, 2002, 2003, 2004, 2005, 2006,
      2007, 2008, 2009, 2010, 2011, 2012, 2013, 2014, 2015, 2016,
      2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026
    ],
    "game_types": ["play_in", "playoff", "regular"]
  },
  "schema": [],
  "sources": {
    "worklists": [],
    "games": []
  },
  "versions": {},
  "partitions": {},
  "validation": {}
}
```

Source lists are sorted by relative path. Partition keys are ending-season
strings. Absolute paths, build times, hostnames, and volatile execution data
must not appear. `scope.ending_seasons` lists every built partition
explicitly; production contains all 30 values, while small unit builds list
only their requested fixture seasons.

## Task Dependency Order

```text
Task 1  Event parsing and score normalization
  ↓
Task 2  Worklist construction and cross-validation
  ↓
Task 3  Exact cache identities and inventory
  ↓
Task 4  Serialized retrying acquisition and capacity
  ↓
Task 5  Transactional tree publication
  ↓
Task 6  Streaming deterministic build and manifest
  ↓
Task 7  CLI and repository protections
  ↓
Task 8  Snapshot and restore implementation
  ↓
Task 9  Source spike, full acquisition, reproducible publication
  ↓
Task 10 Immutable archive, restore verification, audit, and final acceptance
```

Tasks 1–8 are code-and-test work. Task 9 is the long, resumable data operation
and intentionally commits no ignored data. Task 10 creates the only committed
acquisition artifact: the final audit and its documentation links.

### Task 1: Parse the fixed V3 event contract and derive running scores

**Files:**
- Create: `src/nba_data/pbp_build.py`
- Create: `tests/test_pbp_build.py`
- Create: `tests/fixtures/pbp/early_score_sentinel.json`
- Create: `tests/fixtures/pbp/modern_blank_score.json`

**Interfaces:**
- Consumes: `ACTION_SOURCE_KEYS`, `EVENT_COLUMNS`, and `PBP_ARROW_SCHEMA` from Shared Contracts.
- Produces: `parse_clock`, `derive_running_scores`, and
  `parse_game_payload` with the exact Shared Contracts signatures.

- [x] **Step 1: Add exact representative fixture extracts**

Create each JSON body with outer shape:

```json
{
  "game": {
    "gameId": "0029600001",
    "actions": []
  }
}
```

Every action object contains all 23 keys from `ACTION_SOURCE_KEYS`. Use these
ordered event facts in `early_score_sentinel.json`:

```text
actionId actionNumber period clock          scoreHome scoreAway actionType
1        1            1      PT12M00.00S    0         0         period
2        4            1      PT11M39.00S    0         2         Made Shot
3        10           1      PT10M34.00S    0         0         Turnover
4        10           1      PT10M34.00S    0         0         ""
5        120          2      PT00M00.00S    25        30        period
6        240          3      PT00M00.00S    50        60        period
7        480          4      PT00M01.00S    98        108       Made Shot
8        481          4      PT00M00.00S    98        107       period
```

Use `0` for unused numeric source fields and `""` for unused string source
fields. The score correction from 98–108 to 98–107 is intentional. Use these
facts in `modern_blank_score.json` with outer game ID `0022500001`:

```text
actionId actionNumber period clock          scoreHome scoreAway actionType
1        2            1      PT12M00.00S    0         0         period
2        4            1      PT12M00.00S    ""        ""        Jump Ball
3        200          2      PT00M00.00S    30        32        period
4        400          3      PT00M00.00S    60        61        period
5        600          4      PT00M00.00S    100       100       period
6        700          5      PT00M00.00S    112       112       period
7        871          6      PT00M00.00S    125       124       period
```

Set early team fields on the made shot to Chicago's `1610612741`/`CHI`; set
modern team fields on the final score to Oklahoma City's
`1610612760`/`OKC`. This preserves realistic scalar types without committing
the full source responses.

- [x] **Step 2: Write failing clock and score-normalization tests**

Add:

```python
@pytest.mark.parametrize(
    ("clock", "period", "expected"),
    [
        ("PT11M35.00S", 1, 695.0),
        ("PT00M07.30S", 4, 7.3),
        ("PT00M00.00S", 4, 0.0),
        ("PT05M00.00S", 5, 300.0),
    ],
)
def test_parse_clock(clock, period, expected):
    assert pbp_build.parse_clock(clock, period) == expected


@pytest.mark.parametrize(
    ("clock", "period"),
    [
        ("11:35", 1),
        ("PT12M00.01S", 5),
        ("PT05M00.01S", 6),
        ("PT-1M00S", 1),
    ],
)
def test_parse_clock_rejects_invalid_values(clock, period):
    with pytest.raises(pbp_build.PBPBuildError, match="invalid V3 clock"):
        pbp_build.parse_clock(clock, period)


def test_running_scores_preserve_early_zero_sentinel_and_correction():
    home, away = pbp_build.derive_running_scores(
        ["0", "0", "0", "98", "98"],
        ["0", "2", "0", "108", "107"],
    )
    assert home == [0, 0, 0, 98, 98]
    assert away == [0, 2, 2, 108, 107]


def test_running_scores_carry_modern_blanks():
    home, away = pbp_build.derive_running_scores(
        ["0", "", "2"],
        ["0", "", "0"],
    )
    assert home == [0, 0, 2]
    assert away == [0, 0, 0]
```

Also test one-sided blanks, negative scores, non-integer scores, leading
blank pairs followed by an arbitrary first numeric pair, a mostly-blank game
shape (blanks interleaved with real scoring, no explicit `"0"`/`"0"` pair
anywhere), a game whose first action already carries an arbitrary numeric
pair with no leading blanks (e.g. `"5"`/`"3"`), and a missing-opener-row
shape (first action `"0"`/`"1"`). Each rejection raises `PBPBuildError` with
the game-independent message fragment `invalid raw score pair`; the
leading-blank, mostly-blank, arbitrary-first-pair, and missing-opener cases
are all accepted, not rejected.

- [x] **Step 3: Run the narrow tests to verify failure**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_pbp_build.py::test_parse_clock \
  tests/test_pbp_build.py::test_running_scores_preserve_early_zero_sentinel_and_correction \
  -q
```

Expected: FAIL during import because `nba_data.pbp_build` does not exist.

- [x] **Step 4: Implement clock and running-score normalization**

Create `PBPBuildError(ValueError)`. Parse clocks with:

```python
CLOCK_PATTERN = re.compile(
    r"^PT(?P<minutes>\d{2})M(?P<seconds>\d{2}(?:\.\d+)?)S$"
)


def parse_clock(clock: str, period: int) -> float:
    if type(clock) is not str or type(period) is not int or period < 1:
        raise PBPBuildError(f"invalid V3 clock {clock!r} for period {period!r}")
    match = CLOCK_PATTERN.fullmatch(clock)
    if match is None:
        raise PBPBuildError(f"invalid V3 clock {clock!r} for period {period}")
    value = (
        int(match.group("minutes")) * 60
        + float(match.group("seconds"))
    )
    maximum = 720.0 if period <= 4 else 300.0
    if value < 0 or value > maximum:
        raise PBPBuildError(f"invalid V3 clock {clock!r} for period {period}")
    return value
```

Implement score derivation as an explicit state machine. Reject booleans and
whitespace variants rather than coercing them. Running scores start
implicitly at 0-0; any number of leading (or later interleaved) blank pairs
carry that implicit start forward. The first numeric pair in the sequence may
be any value, with no 0-0 requirement even when it is the game's very first
action — full-corpus evidence found only 2 of 31,438 real games with a
non-0-0 first numeric pair (a missing opener row and a start-truncated
source response), and downstream period-contiguity and terminal-score checks
(the latter routing into the reviewed allowlist) already catch truncated
games without this guard. Treat paired `"0"`/`"0"` as missing only after
either current score is nonzero. Allow later numeric pairs to decrease.

- [x] **Step 5: Write failing fixed-schema and per-game tests**

Load both fixtures as bytes and add:

```python
def worklist_row(**changes):
    row = {
        "game_id": "0029600001",
        "season": 1997,
        "game_type": "regular",
        "game_date": date(1996, 11, 1),
        "away_franchise_id": "CHI",
        "home_franchise_id": "BOS",
        "away_pts": 107,
        "home_pts": 98,
    }
    row.update(changes)
    return row


def test_parse_game_uses_action_id_primary_key_and_preserves_action_number():
    frame, report = pbp_build.parse_game_payload(
        (FIXTURES / "early_score_sentinel.json").read_bytes(),
        worklist_row(),
    )
    assert frame["action_id"].tolist() == list(range(1, 9))
    assert frame["action_number"].tolist().count(10) == 2
    assert frame[["score_home", "score_away"]].iloc[-1].tolist() == [98, 107]
    assert frame.columns.tolist() == pbp_build.EVENT_COLUMNS
    assert report["events"] == 8
    assert report["historical_zero_sentinels"] == 2


def test_parse_game_accepts_modern_blanks_and_double_overtime():
    frame, report = pbp_build.parse_game_payload(
        (FIXTURES / "modern_blank_score.json").read_bytes(),
        worklist_row(
            game_id="0022500001",
            season=2026,
            game_date=date(2025, 10, 21),
            away_franchise_id="HOU",
            home_franchise_id="OKC",
            away_pts=124,
            home_pts=125,
        ),
    )
    assert frame["period"].unique().tolist() == [1, 2, 3, 4, 5, 6]
    assert frame.loc[1, ["score_home", "score_away"]].tolist() == [0, 0]
    assert report["max_period"] == 6
```

Add parameterized failures for:

```text
wrong outer gameId
missing action key
additional action key
boolean in an integer source field
non-string in a string source field
duplicate actionId
zero/negative actionId
out-of-order actionId
periods not equal to 1..max(period)
terminal score unequal to worklist
```

Each failure asserts a specific `PBPBuildError` fragment naming the violated
contract.

- [x] **Step 6: Run the parser tests to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_pbp_build.py -q
```

Expected: normalization tests pass; parser tests FAIL because
`parse_game_payload` and schema constants are absent.

- [x] **Step 7: Implement fixed-schema parsing**

Decode UTF-8 JSON with `allow_nan` irrelevant because JSON parsing rejects
nonstandard values. Require outer keys `game` and `game.gameId`, and require
`game.actions` to be a nonempty list.

For each action:

1. require `set(action) == ACTION_SOURCE_KEYS`;
2. validate exact non-boolean integer and string/nullable scalar families;
3. collect action IDs in response order and require positive uniqueness and
   strict increase;
4. rename every source key to the `EVENT_COLUMNS` name;
5. attach worklist season/type and outer game ID;
6. derive clocks and running scores;
7. require periods to equal `list(range(1, max_period + 1))`;
8. require terminal scores to equal the worklist row;
9. enforce pandas nullable integer/string columns; and
10. return a report containing event count, max period, terminal scores,
    historical sentinel count, blank score-pair count, and per-action-field
    null/blank counts.

Do not sort before validating source order. After validation, sort by
`game_id, action_id` with `kind="stable"` and reset the index.

- [x] **Step 8: Run Task 1 tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_pbp_build.py -q
```

Expected: PASS.

- [x] **Step 9: Commit Task 1**

```bash
git add \
  src/nba_data/pbp_build.py \
  tests/test_pbp_build.py \
  tests/fixtures/pbp/early_score_sentinel.json \
  tests/fixtures/pbp/modern_blank_score.json
git commit -m "feat: normalize play-by-play events"
```

### Task 2: Construct and exactly cross-validate the game worklist

**Files:**
- Create: `src/nba_data/pbp.py`
- Create: `tests/test_pbp.py`
- Create: `tests/fixtures/pbp/worklist_regular_1997.json`
- Create: `tests/fixtures/pbp/worklist_playoff_1997.json`
- Create: `tests/fixtures/pbp/worklist_play_in_2020.json`

**Interfaces:**
- Consumes: `franchises.resolve(name: str, season: int) -> str`,
  `data/parquet/games.parquet`, and post-integration
  `data/parquet/playoff_games.parquet`.
- Produces: path/constants contracts, `normalize_worklist_payload`,
  `load_worklist`, and `cross_validate_worklist`.

- [x] **Step 1: Add exact two-team worklist fixtures**

Each fixture has:

```json
{
  "resultSets": [
    {
      "name": "LeagueGameFinderResults",
      "headers": [
        "SEASON_ID",
        "TEAM_ID",
        "TEAM_NAME",
        "TEAM_ABBREVIATION",
        "GAME_ID",
        "GAME_DATE",
        "MATCHUP",
        "WL",
        "PTS"
      ],
      "rowSet": []
    }
  ]
}
```

Use these literal rows:

```python
REGULAR_1997_ROWS = [
    ["21996", 1610612741, "Chicago Bulls", "CHI", "0029600001",
     "1996-11-01", "CHI @ BOS", "W", 107],
    ["21996", 1610612738, "Boston Celtics", "BOS", "0029600001",
     "1996-11-01", "BOS vs. CHI", "L", 98],
]
PLAYOFF_1997_ROWS = [
    ["41996", 1610612766, "Charlotte Hornets", "CHH", "0049600001",
     "1997-04-24", "CHH @ NYK", "L", 99],
    ["41996", 1610612752, "New York Knicks", "NYK", "0049600001",
     "1997-04-24", "NYK vs. CHH", "W", 109],
]
PLAY_IN_2020_ROWS = [
    ["52019", 1610612757, "Portland Trail Blazers", "POR", "0051900111",
     "2020-08-15", "POR vs. MEM", "W", 126],
    ["52019", 1610612763, "Memphis Grizzlies", "MEM", "0051900111",
     "2020-08-15", "MEM @ POR", "L", 122],
]
```

- [x] **Step 2: Write failing normalization and identity tests**

Add:

```python
@pytest.mark.parametrize(
    ("fixture", "season", "game_type", "expected"),
    [
        (
            "worklist_regular_1997.json",
            1997,
            "regular",
            ["0029600001", "CHI", "BOS", 107, 98],
        ),
        (
            "worklist_playoff_1997.json",
            1997,
            "playoff",
            ["0049600001", "NOP", "NYK", 99, 109],
        ),
        (
            "worklist_play_in_2020.json",
            2020,
            "play_in",
            ["0051900111", "MEM", "POR", 122, 126],
        ),
    ],
)
def test_normalize_worklist_pairs_home_and_away(
    fixture, season, game_type, expected
):
    frame = pbp.normalize_worklist_payload(
        (FIXTURES / fixture).read_bytes(),
        season=season,
        game_type=game_type,
    )
    row = frame.iloc[0]
    assert [
        row["game_id"],
        row["away_franchise_id"],
        row["home_franchise_id"],
        row["away_pts"],
        row["home_pts"],
    ] == expected
    assert frame.columns.tolist() == pbp.WORKLIST_COLUMNS
```

The source abbreviation remains `CHH`, while
`franchises.resolve("Charlotte Hornets", 1997)` returns the repository's
canonical lineage ID `NOP`. Use `NOP` in the normalized worklist and
postseason reference fixture.

Add failures for wrong result-set name, missing required header, malformed
row width, one/three team rows, two home rows, no home row, invalid PTS,
same franchise on both sides, wrong ending season, wrong ID prefix, unknown
game type, and duplicate game IDs.

- [x] **Step 3: Run normalization tests to verify failure**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_pbp.py::test_normalize_worklist_pairs_home_and_away \
  -q
```

Expected: FAIL because `nba_data.pbp` does not exist.

- [x] **Step 4: Implement worklist normalization**

Create the constants and path helpers from Shared Contracts. Decode exactly
one `LeagueGameFinderResults` result set and require
`WORKLIST_REQUIRED_HEADERS`.

Normalize with these rules:

```python
ending_season = (pd.to_numeric(raw["SEASON_ID"], errors="raise") % 10_000) + 1
game_id = raw["GAME_ID"].astype(str).str.zfill(10)
points = pd.to_numeric(raw["PTS"], errors="raise")
```

`SEASON_ID` is one season-type digit (`2` regular, `4` playoffs, `5`
play-in) followed by the four-digit starting year, so `% 10_000` recovers the
starting year for all three request types; require the leading digit to agree
with the requested game type.

Derive each row's home/away role positionally rather than from MATCHUP text
alone: parse `MATCHUP` as `<AWAY> @ <HOME>` or `<HOME> vs. <AWAY>`, and take
whichever side equals that row's own `TEAM_ABBREVIATION`. Reject a `MATCHUP`
matching neither format, and a row whose own abbreviation matches neither
side. Require both rows of a game to agree on the unordered team-abbreviation
pair before applying the existing "two home rows"/"no home row" pairing
checks. NBA Cup finals and neutral-site (Paris/Mexico City) games starting in
ending season 2025 can report **both** team rows in away form (e.g. the home
team's own row also reads `"AWAY @ HOME"`), so plain `MATCHUP.str.contains("vs.")`
no longer reliably identifies the home row; bidirectional cross-validation
against the `games.parquet`/`playoff_games.parquet` publications
(`cross_validate_worklist`) remains the correctness net regardless of how
this positional parsing behaves.

Reject booleans and fractional/negative points before converting to `int64`.
Resolve franchises from `TEAM_NAME` and ending season. Pair exactly one home
and one away row per game and return stable ordering by
`season, game_date, game_type, game_id`.

- [x] **Step 5: Write failing cross-source equality tests**

Create temporary regular and playoff Parquet fixtures with the exact columns
consumed by the comparator. Add:

```python
def test_cross_validate_worklist_accepts_exact_regular_and_postseason(
    tmp_path
):
    data_dir, worklist = seed_cross_source_publications(tmp_path)
    pbp.cross_validate_worklist(worklist, data_dir, [1997, 2020])


@pytest.mark.parametrize(
    ("publication", "column", "replacement", "message"),
    [
        ("regular", "home_pts", 97, "regular worklist mismatch"),
        ("regular", "game_id", "0029600999", "regular worklist mismatch"),
        ("postseason", "away_pts", 98, "postseason worklist mismatch"),
        ("postseason", "game_type", "play_in", "postseason worklist mismatch"),
    ],
)
def test_cross_validate_worklist_rejects_bidirectional_mismatch(
    tmp_path, publication, column, replacement, message
):
    data_dir, worklist = seed_cross_source_publications(tmp_path)
    mutate_publication(data_dir, publication, column, replacement)
    with pytest.raises(pbp.PBPValidationError, match=message):
        pbp.cross_validate_worklist(worklist, data_dir, [1997, 2020])
```

Add tests that extra publication rows fail, that `tiebreaker`/`voided`
postseason rows are excluded from the expected PBP set, and that play-in
counts are exactly
`{2020: 1, 2021: 6, 2022: 6, 2023: 6, 2024: 6, 2025: 6, 2026: 6}`
when those seasons are selected.

- [x] **Step 6: Run cross-source tests to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_pbp.py -q
```

Expected: normalization tests pass; cross-source tests FAIL because the
comparison functions are absent.

- [x] **Step 7: Implement bidirectional equality**

For regular seasons, compare:

```python
[
    "game_id",
    "season",
    "game_date",
    "away_franchise_id",
    "home_franchise_id",
    "away_pts",
    "home_pts",
]
```

For postseason, filter `playoff_games.parquet` to scoped seasons and
`game_type.isin(["playoff", "play_in"])`, then compare:

```python
[
    "season",
    "game_date",
    "game_type",
    "away_franchise_id",
    "home_franchise_id",
    "away_pts",
    "home_pts",
]
```

Use outer merges with `indicator=True`, deterministic five-row mismatch
samples, and a message that states which side contains each unmatched row.
Require global worklist game-ID uniqueness and no cross-type duplication
before either publication comparison.

- [x] **Step 8: Run Task 2 tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_pbp.py -q
```

Expected: PASS.

- [x] **Step 9: Commit Task 2**

```bash
git add \
  src/nba_data/pbp.py \
  tests/test_pbp.py \
  tests/fixtures/pbp/worklist_regular_1997.json \
  tests/fixtures/pbp/worklist_playoff_1997.json \
  tests/fixtures/pbp/worklist_play_in_2020.json
git commit -m "feat: build exact play-by-play worklists"
```

### Task 3: Enforce exact cache identities and complete inventories

**Files:**
- Modify: `src/nba_data/pbp.py`
- Modify: `tests/test_pbp.py`

**Interfaces:**
- Consumes: `cache.read_valid`, `cache.require_valid`,
  `cache.publish_bytes`, worklist path contracts, and normalized worklists.
- Produces: exact metadata readers, transport validators,
  `publish_worklist_payload`, `publish_game_payload`, `CacheInventory`,
  `load_worklist`, `validate_cache_tree`, and `validate_cache_inventory`
  with the exact Shared Contracts signatures.

- [x] **Step 1: Write failing exact-metadata tests**

Create a helper the later tests reuse:

```python
def expected_game_metadata(season, game_type, game_id):
    return {
        "provider": "stats_nba",
        "resource": "play_by_play",
        "endpoint": "PlayByPlayV3",
        "parameters": {
            "GameID": game_id,
            "StartPeriod": 0,
            "EndPeriod": 0,
        },
        "season": season,
        "season_label": pbp.season_label(season),
        "game_type": game_type,
        "game_id": game_id,
    }
```

Publish test pairs only through `pbp.publish_worklist_payload` and
`pbp.publish_game_payload`. Test worklist metadata requires exact values and
exact JSON-compatible types:

```python
{
    "provider": "stats_nba",
    "resource": "pbp_worklist",
    "endpoint": "LeagueGameFinder",
    "parameters": endpoint.parameters,
    "season": 1997,
    "season_label": "1996-97",
    "game_type": "regular",
}
```

Test game metadata requires:

```python
{
    "provider": "stats_nba",
    "resource": "play_by_play",
    "endpoint": "PlayByPlayV3",
    "parameters": {
        "GameID": "0029600001",
        "StartPeriod": 0,
        "EndPeriod": 0,
    },
    "season": 1997,
    "season_label": "1996-97",
    "game_type": "regular",
    "game_id": "0029600001",
}
```

For each identity field, mutate the value and its type separately and assert
`cache.CacheIntegrityError` with `PBP cache metadata mismatch`.

- [x] **Step 2: Write failing transport/schema-separation tests**

Add:

```python
def test_transport_valid_schema_drift_remains_cached(tmp_path):
    destination = pbp.game_path(
        tmp_path, 1997, "0029600001"
    )
    payload = json.dumps({
        "game": {
            "gameId": "0029600001",
            "actions": [{"newUpstreamField": 1}],
        }
    }).encode()
    pbp.publish_game_payload(
        destination,
        payload,
        expected_game_metadata(1997, "regular", "0029600001"),
    )
    assert destination.read_bytes() == payload
    with pytest.raises(
        pbp_build.PBPBuildError,
        match="V3 action schema drift",
    ):
        pbp_build.parse_game_payload(payload, worklist_row())
```

Transport validation rejects malformed JSON, a missing/non-object `game`,
wrong `gameId`, a missing/non-list `actions`, empty actions, or non-object
action elements before the body becomes a complete cache pair.

- [x] **Step 3: Write failing recursive inventory tests**

Seed two valid worklists and two valid game pairs. Parameterize mutations:

```text
missing body
missing sidecar
orphan unrelated JSON
orphan unrelated sidecar
stale .part
symlinked file
FIFO
unexpected season directory
unexpected game ID
game pair absent from its normalized worklist
wrong game under a season directory
missing required pair when require_complete=True
```

Assert `validate_cache_tree(raw_root, required_seasons=pbp.SEASONS,
require_complete=False)` accepts a valid subset and rejects every unrelated
or unsafe entry. Assert `require_complete=True` requires all 67 worklists and
every game named by the full normalized worklist. Run the same valid case through
`validate_cache_inventory` with `data_dir`, the same season arguments, and
the same completeness flag; require an equal result.

- [x] **Step 4: Run the new tests to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_pbp.py -q
```

Expected: prior tests pass; exact cache/inventory tests FAIL.

- [x] **Step 5: Implement transport validators and exact sidecars**

Create:

```python
def validate_worklist_transport(
    path: Path,
    *,
    season: int,
    game_type: str,
) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    normalize_worklist_payload(
        json.dumps(payload).encode("utf-8"),
        season=season,
        game_type=game_type,
    )


def validate_game_transport(path: Path, *, game_id: str) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    game = payload.get("game")
    if not isinstance(game, dict) or game.get("gameId") != game_id:
        raise PBPValidationError(f"wrong outer game ID for {path}")
    actions = game.get("actions")
    if (
        not isinstance(actions, list)
        or not actions
        or any(not isinstance(action, dict) for action in actions)
    ):
        raise PBPValidationError(f"invalid action envelope for {path}")
```

Implement `publish_worklist_payload` and `publish_game_payload` as thin
wrappers over `cache.publish_bytes(destination, payload, metadata,
validator)`. `cache.publish_bytes` calls `validator(part_path)` with one
positional argument, so each wrapper binds its identity fields from
`metadata` into a closure around the matching transport validator before
delegating. All fetch-time publication goes through these two wrappers.

Read sidecars only after checksum/transport validation. Compare the exact
identity subset and require `type(observed[key]) is type(expected[key])`.
Ignore only cache-managed `fetched_at`, `content_length`, and `sha256`.

- [x] **Step 6: Implement safe recursive inventory validation**

Use `Path.lstat()` plus `stat.S_ISREG`/`stat.S_ISDIR`. Never follow symlinks.
Derive all valid paths from:

1. the fixed season/type worklist contracts;
2. each checksum-valid normalized worklist; and
3. each worklist row's season/game ID.

Reject a path before opening it when its relative path has no exact contract.
Return sorted body sequences and the concatenated, internally validated
worklist in `CacheInventory`. `validate_cache_tree` accepts the physical PBP
raw root directly; `validate_cache_inventory` derives that root from the
repository data directory. Neither function depends on the regular or
postseason Parquet publications.

For `require_complete=False`, missing known pairs are allowed, but existing
pairs must be complete and exact. For `require_complete=True`, required
seasons must have all worklist types and every expected game pair.

- [x] **Step 7: Run Task 3 tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_pbp.py tests/test_cache.py -q
```

Expected: PASS.

- [x] **Step 8: Commit Task 3**

```bash
git add src/nba_data/pbp.py tests/test_pbp.py
git commit -m "feat: validate play-by-play cache inventory"
```

### Task 4: Add serialized retries, capacity gates, and resumable acquisition

**Files:**
- Modify: `src/nba_data/pbp.py`
- Modify: `tests/test_pbp.py`

**Interfaces:**
- Consumes: exact cache publication/inventory, `LeagueGameFinder` and
  `PlayByPlayV3` parameter dictionaries constructed with
  `get_request=False`,
  `NBAStatsHTTP.base_url`, and `NBAStatsHTTP.headers`.
- Produces: `RequestPacer`, `_request_bytes`, `fetch_worklists`,
  `fetch_games`, and `fetch`.

- [x] **Step 1: Write failing request-pacer tests**

Use a deterministic clock:

```python
class FakeClock:
    def __init__(self):
        self.value = 0.0
        self.sleeps = []

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += seconds


def test_request_pacer_enforces_start_to_start_spacing():
    clock = FakeClock()
    pacer = pbp.RequestPacer(
        minimum_interval_s=1.5,
        now=clock.now,
        sleeper=clock.sleep,
    )
    pacer.wait()
    clock.value += 0.4
    pacer.wait()
    assert clock.sleeps == [1.1]
    assert pacer.last_started == 1.5
```

Reject intervals below 1.5 seconds at construction.

- [x] **Step 2: Write failing retry-classification tests**

Create fake responses with `status_code`, `content`, and `headers`, plus a fake
session whose `get` returns queued responses or raises queued
`requests.RequestException` instances.

Cover:

```python
def test_transient_failures_use_all_bounded_delays():
    # Four transient failures followed by 200.
    assert sleeper.calls == [5.0, 10.0, 20.0, 40.0]


def test_retry_after_replaces_smaller_backoff():
    # 429 Retry-After: 30 on attempt 1.
    assert sleeper.calls == [30.0]


def test_retry_after_above_ceiling_stops():
    # 429 Retry-After: 121.
    with pytest.raises(
        cache.SourceAccessError,
        match="Retry-After 121 exceeds 120",
    ):
        request()


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_non_retryable_4xx_stops_after_one_request(status):
    session = FakeSession([FakeResponse(status, b"", {})])
    sleeper = Mock()
    with pytest.raises(cache.SourceAccessError, match=f"HTTP {status}"):
        request(session=session, sleeper=sleeper)
    assert session.calls == 1
    sleeper.assert_not_called()
```

The last parameterized test asserts one session call and no retry sleeps.
Test 500, 502, 503, 504, 429, connection errors, and timeouts as transient.
Test a 200 malformed body fails in `cache.publish_bytes` without a retry.
Assert the terminal exhausted-retry failure message names the season, game
type, game ID (for game requests), attempt count, and final status or
exception class.

- [x] **Step 3: Write failing capacity tests**

Expose `available_bytes(path: Path) -> int` and
`require_fetch_capacity(data_dir: Path, uncached_game_count: int) -> None`.
The first function calls `stats = os.statvfs(path)` once and returns
`stats.f_bavail * stats.f_frsize`.

Monkeypatch `available_bytes` and assert the exact boundary:

```python
required = (
    3 * pbp.BYTES_PER_UNCACHED_GAME
    + pbp.FETCH_STAGING_HEADROOM_BYTES
)
```

`required - 1` raises `PBPValidationError` containing required and available
bytes; `required` passes.

- [x] **Step 4: Write failing worklist-first and resume tests**

Test:

1. `fetch_worklists` constructs `LeagueGameFinder` with
   `get_request=False`,
   preserves the endpoint's complete `parameters` dictionary in metadata,
   and sends exactly one request for each missing dedicated contract.
2. all selected worklists are normalized and cross-validated before
   `fetch_games` receives a row;
3. a worklist mismatch produces zero PlayByPlay requests;
4. capacity failure produces zero PlayByPlay requests;
5. a complete valid game pair produces zero requests;
6. two missing games produce two serialized requests and exact sidecars;
7. a schema-drift response is cached and then stops the fetch, while a
   terminal-score-only mismatch is cached, logged, does not stop the
   fetch, and is reported in an end-of-run summary alongside subsequent
   games still being requested; and
8. rerunning after one completed game requests only the remaining game.

Monkeypatch `stats_nba.preflight_dns` and assert it is called once before the
first missing request in each batch, and that a preflight failure produces
zero session calls.
Assert every fake-session URL parses to hostname `stats.nba.com` and no URL,
redirect target, or preflight references `stats-prod.nba.com`.

- [x] **Step 5: Run acquisition tests to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_pbp.py -q
```

Expected: cache/inventory tests pass; pacing/retry/fetch tests FAIL.

- [x] **Step 6: Implement the raw HTTP wrapper**

Use endpoint classes only to construct canonical parameters:

```python
finder = LeagueGameFinder(
    player_or_team_abbreviation="T",
    league_id_nullable="00",
    season_nullable=season_label(season),
    season_type_nullable=SEASON_TYPES[game_type],
    timeout=90,
    get_request=False,
)

play = PlayByPlayV3(
    game_id=game_id,
    start_period=0,
    end_period=0,
    timeout=90,
    get_request=False,
)
```

Send with `requests.Session.get` to:

```python
NBAStatsHTTP.base_url.format(endpoint=endpoint.endpoint)
```

Pass a sorted parameter list and a copied
`dict(NBAStatsHTTP.headers)`. This retains nba_api's canonical endpoint,
parameter, and browser-header contracts while exposing status and
`Retry-After`. Disable redirects and reject any effective response URL whose
hostname is not exactly `stats.nba.com`.

Before every attempt, call the shared `RequestPacer.wait()`. Classify status
before reading the body. Parse `Retry-After` only as a nonnegative decimal
integer; an invalid value stops with `SourceAccessError`. Every terminal
`SourceAccessError` names the season, game type, game ID (for game
requests), attempt count, and the final HTTP status or exception class.

- [x] **Step 7: Implement worklist-first orchestration**

`fetch(data_dir, seasons)` performs:

```text
normalize and validate selected season arguments
validate every existing PBP cache entry without requiring completeness
preflight DNS
fetch every missing selected worklist
load and cross-validate the complete selected worklist
count missing game pairs without treating invalid pairs as missing
enforce the byte capacity predicate
preflight DNS again if worklist requests occurred and game requests remain
fetch selected missing games in stable season/date/type/game_id order
run the fixed parser immediately after each transport-valid body publishes,
    logging and continuing past a terminal-score-only mismatch (a
    structured PBPTerminalScoreError) while still stopping on schema
    drift or any other parse failure, and reporting a summary of every
    collected mismatch at the end of the run
revalidate the selected inventory
```

Use one pacer across worklist and game requests so the boundary between the
two phases also respects 1.5 seconds.

- [x] **Step 8: Run Task 4 tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_pbp.py \
  tests/test_stats_nba.py \
  -q
```

Expected: PASS.

- [x] **Step 9: Commit Task 4**

```bash
git add src/nba_data/pbp.py tests/test_pbp.py
git commit -m "feat: fetch resumable play-by-play archive"
```

### Task 5: Publish a staged directory tree transactionally

**Files:**
- Modify: `src/nba_data/publication.py`
- Modify: `tests/test_publication.py`

**Interfaces:**
- Preserves the existing `publish_pair` interface and behavior.
- Produces: `TreePublicationError(RuntimeError)` and
  `publish_tree(stage_dir: Path, output_dir: Path, *, label: str) -> Path | None`.
- Returns `None` after a successful publication and cleanup, or the retained
  recovery directory when publication succeeded but old-tree cleanup failed.

- [x] **Step 1: Write failing first-publication and replacement tests**

Add helpers that create a staged tree containing `_manifest.json` and two
season partitions. Verify:

```python
def test_publish_tree_moves_first_stage_into_place(tmp_path):
    stage, output = seed_tree_publication(tmp_path, with_existing=False)
    assert publication.publish_tree(
        stage, output, label="PBP publication"
    ) is None
    assert not stage.exists()
    assert (output / "_manifest.json").read_text() == "new manifest"


def test_publish_tree_replaces_existing_tree_and_removes_rollback(tmp_path):
    stage, output = seed_tree_publication(tmp_path, with_existing=True)
    assert publication.publish_tree(
        stage, output, label="PBP publication"
    ) is None
    assert (output / "_manifest.json").read_text() == "new manifest"
    assert not any(tmp_path.glob(".pbp-publication.rollback-*"))
```

`seed_tree_publication` creates the stage at `tmp_path / "stage"` and the
output at `tmp_path / "pbp-publication"`, so the rollback glob above matches
the implementation's `.{output_dir.name}.rollback-<hex>` naming. When
`with_existing=True`, seed an old output tree whose `_manifest.json` reads
`old manifest`.

Assert the operation uses renames within one parent filesystem, not
file-by-file replacement.

- [x] **Step 2: Write failing safety and rollback tests**

Cover each precondition and failure point:

```text
stage missing
stage not a directory
output is a symlink
stage contains a symlink
stage contains a FIFO
stage and output have different parents
rollback destination already exists
moving the old output to rollback fails
moving stage to output fails before old output was moved
moving stage to output fails after old output was moved
restoring the old output fails
removing a successfully replaced old tree fails
```

Patch `Path.replace` and `shutil.rmtree` at the individual operation under
test. Assert:

- any existing publication remains byte-for-byte unchanged when replacement
  does not complete;
- the previous publication is restored after a failed new-tree rename;
- a failed restoration raises `TreePublicationError` naming both the expected
  output and retained rollback location;
- a cleanup failure leaves the new publication active, returns the retained
  rollback path, and never deletes it; and
- no error path follows or removes a symlink.

- [x] **Step 3: Run the tree-publication tests to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_publication.py -q
```

Expected: existing pair-publication tests pass; new tests FAIL because
`publish_tree` and `TreePublicationError` do not exist.

- [x] **Step 4: Implement safe tree inspection and replacement**

Add a private recursive lstat walker. Accept only regular files and real
directories; reject symlinks and every special inode before the first rename.
Require `stage_dir.parent == output_dir.parent`, distinct resolved names, and
an absent output or a real directory.

Use a collision-checked rollback sibling named:

```python
rollback = output_dir.parent / (
    f".{output_dir.name}.rollback-{uuid.uuid4().hex}"
)
```

Implement this state machine:

```text
validate stage, output, and rollback paths
if output exists: rename output -> rollback
rename stage -> output
if second rename fails and rollback exists: rename rollback -> output
if publication succeeds and rollback exists: recursively remove rollback
```

Wrap filesystem errors in `TreePublicationError` with the label, operation,
and relevant paths. Do not catch `BaseException`. Preserve the original
exception as `__cause__`.

- [x] **Step 5: Run Task 5 tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_publication.py -q
```

Expected: PASS, including all pre-existing `publish_pair` tests.

- [x] **Step 6: Commit Task 5**

```bash
git add src/nba_data/publication.py tests/test_publication.py
git commit -m "feat: publish data directory trees transactionally"
```

### Task 6: Build deterministic season partitions and a complete manifest

**Files:**
- Modify: `src/nba_data/pbp_build.py`
- Modify: `tests/test_pbp_build.py`

**Interfaces:**
- Consumes: `pbp.validate_cache_tree(raw_root, require_complete=True)`,
  `cache.require_valid`, `publication.publish_tree`, and the fixed event
  parser from Task 1.
- Produces: `build_tree`, `validate_publication`, `run`, and `render_audit`
  from Shared Contracts.

- [x] **Step 1: Write failing deterministic partition tests**

Seed a complete two-season test inventory with the committed raw fixtures and
matching exact sidecars. Call:

```python
manifest = pbp_build.build_tree(
    raw_root,
    stage_dir,
    seasons=[1997, 2026],
)
```

Assert:

- only `season=1997/events.parquet`,
  `season=2026/events.parquet`, and `_manifest.json` exist;
- each partition has the exact `PBP_ARROW_SCHEMA`;
- rows are ordered by `game_id, action_id`;
- each season is parsed and written before the next season is loaded;
- Zstandard compression is present in every Parquet row group;
- manifest partition counts, min/max game IDs, max periods, score-sentinel
  counts, and SHA-256 values match files reread from disk; and
- two builds into distinct empty stage directories are byte-identical for
  every relative path.

Instrument `parse_game_payload` in the streaming test and fail if a 2026 game
is parsed before the 1997 table has been written and released.

- [x] **Step 2: Write failing manifest provenance tests**

Assert the manifest contains:

```json
{
  "schema_version": 1,
  "scope": {
    "ending_seasons": [1997, 2026],
    "game_types": ["play_in", "playoff", "regular"]
  },
  "schema": [
    {
      "name": "game_id",
      "arrow_type": "large_string",
      "nullable": false,
      "origin": "derived"
    }
  ],
  "sources": {
    "worklists": [
      {
        "body_path": "raw/stats_nba/pbp/worklists/1997/regular.json",
        "body_sha256": "64 lowercase hexadecimal characters",
        "body_content_length": 123,
        "sidecar_path": "raw/stats_nba/pbp/worklists/1997/regular.json.meta.json",
        "sidecar_sha256": "64 lowercase hexadecimal characters",
        "sidecar_content_length": 456
      }
    ],
    "games": []
  },
  "versions": {
    "nba_api": "installed version",
    "numpy": "installed version",
    "pandas": "installed version",
    "pyarrow": "installed version",
    "python": "major.minor.micro"
  },
  "partitions": {},
  "validation": {}
}
```

The test uses concrete computed values, not the descriptive strings above.
Require every worklist and game body/sidecar pair exactly once. Each source
entry has the six keys shown above; paths are repository-data-relative POSIX
paths, body hash/length agree with the cache sidecar, and the independently
computed sidecar hash/length bind the metadata file itself. Require sorted
source lists and schema rows in `EVENT_COLUMNS` order.

Require this deterministic content:

- `validation.worklist_counts[season][game_type]`;
- `validation.game_counts[season][game_type]`;
- `validation.event_counts[season][game_type]`;
- `validation.period_counts[season][period]`;
- `validation.event_count_distributions[season][game_type]` with count, min,
  25th/50th/75th percentiles, max, Tukey fences/outliers, and three exact
  low/high examples;
- `validation.action_fields[source_key]` with null and blank counts;
- `validation.games[game_id]` with season, game type, event count, max period,
  terminal home/away score, early-zero-sentinel count, and blank-score count;
- `validation.terminal_mismatches[game_id]` with the allowlisted worklist and
  PBP-derived score pairs for each accepted terminal-score mismatch (empty
  object when none);
- `validation.gates` with one explicit `true` value for every build gate; and
- each `partitions[season]` entry's relative path, row count, game count, byte
  size, maximum period, Parquet SHA-256, and logical `row_sha256`.

Reject absolute paths, timestamps, hostnames, UUIDs, process IDs, and any
sidecar whose declared checksum or content length differs from the body.

- [x] **Step 3: Write failing publication-validation tests**

For a valid staged publication, assert:

```python
report = pbp_build.validate_publication(raw_root, stage_dir)
assert report == json.loads(
    (stage_dir / "_manifest.json").read_text(encoding="utf-8")
)["validation"]
```

Parameterize mutations for:

```text
missing manifest
additional path
missing season partition
wrong Arrow schema or nullability
unsorted primary keys
duplicate (game_id, action_id)
game absent from worklist
game in the wrong ending-season partition
per-game event count mismatch
terminal score mismatch
partition byte checksum mismatch
partition row count mismatch
manifest source path/checksum mismatch
manifest validation total mismatch
```

Each mutation raises `PBPBuildError` naming the failed invariant.

- [x] **Step 4: Write failing anomaly-summary and audit tests**

For each game, compute event count. For each `(season, game_type)` group with
at least four games, define outliers by Tukey fences:

```python
q1, q3 = np.percentile(counts, [25, 75], method="linear")
iqr = q3 - q1
lower = q1 - 1.5 * iqr
upper = q3 + 1.5 * iqr
```

For smaller groups, report no Tukey outliers. The manifest must still list the
three smallest and three largest games per group, ordered by
`event_count, game_id`; when a group has fewer than three games, list all of
them once in each extreme list.

Test `render_audit` with a concrete manifest and assert the Markdown contains:

```text
ending-season range and game types
worklist/game/event totals
one row per season with games, events, max period, and partition SHA-256
source schema version and package versions
anomaly summary and explicit no-outlier groups
action-field availability and early-zero-sentinel totals
source score mismatches (allowlisted), one line per accepted game with both
    score pairs, and an explicit none line when empty
all four source-spike IDs, response bytes, event counts, periods, and finals
one passed row for every validation gate and execution-evidence flag
full manifest SHA-256
immutable snapshot name
restore verification: passed
```

Pass an `AuditEvidence` containing exactly the four source-spike rows with
`game_id`, response bytes, event count, max period, terminal home score, and
terminal away score. The renderer cross-checks those values against manifest
source lengths and per-game validation records. It rejects a missing/extra
spike ID or any false evidence flag, so the final audit cannot claim an
unverified cross-source gate, reproducibility gate, snapshot, or restore.

- [x] **Step 5: Run build tests to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_pbp_build.py -q
```

Expected: parser tests pass; partition, manifest, validation, and audit tests
FAIL because their implementations are absent.

- [x] **Step 6: Implement one-season-at-a-time deterministic building**

At the start of `build_tree`:

1. require an empty, existing, real `stage_dir`;
2. validate the complete scoped raw inventory;
3. sort the worklist by `season, game_date, game_type, game_id`; and
4. precompute immutable source provenance from checksum-valid pairs.

For each ending season:

1. parse only that season's games;
2. concatenate only that season's frames;
3. sort stably by `game_id, action_id`;
4. convert using `pa.Table.from_pandas(season_frame,
   schema=PBP_ARROW_SCHEMA, preserve_index=False, safe=True)` and call
   `replace_schema_metadata(None)`;
5. write `events.parquet` with `compression="zstd"`,
   `compression_level=9`, `use_dictionary=False`, `write_statistics=True`,
   `row_group_size=65_536`, `data_page_version="2.0"`, and
   `version="2.6"`;
6. reread the partition and validate schema, ordering, keys, worklist
   membership, terminal scores, and counts;
7. record statistics, the file SHA-256, and the logical row SHA-256; and
8. drop season-local frames/tables before advancing.

Define the logical row hash by removing Arrow schema metadata, combining
chunks, hashing `PBP_ARROW_SCHEMA.serialize().to_pybytes()`, then for record
batches of at most 65,536 rows in primary-key order hashing an unsigned
eight-byte big-endian serialized-batch length followed by
`batch.serialize().to_pybytes()`. `validate_publication` recomputes the same
hash from the reread Parquet table. Record the PyArrow version because this
logical serialization contract is version-bound.

Use JSON serialization with `sort_keys=True`, `indent=2`, UTF-8, one trailing
newline, and `allow_nan=False`. Create `_manifest.json` only after every
partition passes validation. Never include a stage/output path or current
time.

- [x] **Step 7: Implement whole-publication validation and publication**

`validate_publication` independently walks the publication without following
symlinks, requires exactly one manifest plus one partition for every season
listed in `scope.ending_seasons`, rereads every Parquet file,
recomputes every checksum/statistic, verifies source provenance against the
raw cache, and returns the recomputed validation report.

`run` additionally requires the production scope `pbp.SEASONS`, so the active
publication has exactly 31 regular files and 30 expected season directories.

`run(data_dir)`:

1. sets output to `data_dir / "parquet-large/pbp"`;
2. creates a sibling temporary stage under `data_dir / "parquet-large"`;
3. derives `raw_root = data_dir / "raw/stats_nba/pbp"` and calls
   `build_tree(raw_root, stage_dir, seasons=pbp.SEASONS)`;
4. calls `validate_publication`;
5. calls `publication.publish_tree`;
6. validates the active output again; and
7. reports any retained rollback directory as a fatal
   `PBPBuildError` requiring operator cleanup.

On failures before publication, remove only the exact temporary stage after
verifying it is a real directory created by this run.

- [x] **Step 8: Run Task 6 tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_pbp_build.py \
  tests/test_publication.py \
  -q
```

Expected: PASS.

- [x] **Step 9: Commit Task 6**

```bash
git add src/nba_data/pbp_build.py tests/test_pbp_build.py
git commit -m "feat: build deterministic play-by-play publication"
```

### Task 7: Expose the CLI and protect generated large-data paths

**Files:**
- Modify: `src/nba_data/__main__.py`
- Modify: `tests/test_cli.py`
- Modify: `.gitignore`
- Modify: `scripts/verify_repository.py`
- Modify: `tests/test_verify_repository.py`
- Modify: `tests/test_repository_layout.py`

**Interfaces:**
- Produces: `python -m nba_data fetch-pbp [--season END_YEAR]` and
  `python -m nba_data build-pbp`.
- Requires the exact `.gitignore` entry `data/parquet-large/`.

- [x] **Step 1: Write failing CLI dispatch tests**

Add tests equivalent to:

```python
def test_fetch_pbp_defaults_to_all_seasons(monkeypatch, tmp_path):
    cli = load_cli()
    called = []
    monkeypatch.setattr(cli.pbp, "fetch", lambda data, seasons: called.append(
        (data, tuple(seasons))
    ))
    monkeypatch.setattr(
        sys,
        "argv",
        ["nba_data", "--data-dir", str(tmp_path), "fetch-pbp"],
    )
    cli.main()
    assert called == [(tmp_path, cli.pbp.SEASONS)]


def test_fetch_pbp_accepts_multiple_seasons(monkeypatch, tmp_path):
    cli = load_cli()
    called = []
    monkeypatch.setattr(cli.pbp, "fetch", lambda data, seasons: called.append(
        tuple(seasons)
    ))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "nba_data",
            "--data-dir",
            str(tmp_path),
            "fetch-pbp",
            "--season",
            "1997",
            "--season",
            "2026",
        ],
    )
    cli.main()
    assert called == [(1997, 2026)]


def test_build_pbp_dispatches_offline_builder(monkeypatch, tmp_path):
    cli = load_cli()
    called = []
    monkeypatch.setattr(cli.pbp_build, "run", called.append)
    monkeypatch.setattr(
        sys,
        "argv",
        ["nba_data", "--data-dir", str(tmp_path), "build-pbp"],
    )
    cli.main()
    assert called == [tmp_path]
```

Assert argparse rejects 1996, 2027, nonintegers, and `--season` on
`build-pbp`. Assert duplicate season flags normalize to stable unique order.
Patch every network entry point in the build test to raise if invoked.

- [x] **Step 2: Write failing repository-protection tests**

Add `data/parquet-large/` to the expected exact ignore-rule set in
`tests/test_verify_repository.py`. In `tests/test_repository_layout.py`,
assert both generated roots are gitignored:

```python
[
    "data/raw/stats_nba/pbp/example.json",
    "data/parquet-large/pbp/season=2026/events.parquet",
]
```

Assert neither path is tracked by `git ls-files`. Do not require the final
audit until Task 10.

- [x] **Step 3: Run the new tests to verify failure**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_cli.py \
  tests/test_verify_repository.py \
  tests/test_repository_layout.py \
  -q
```

Expected: FAIL because the commands and large-Parquet ignore rule are absent.

- [x] **Step 4: Implement CLI dispatch and ignore protections**

Import `pbp` and `pbp_build` in `src/nba_data/__main__.py`. Add subparsers:

```text
fetch-pbp --season {1997..2026}
build-pbp
```

Use `action="append"` for `--season`; when absent pass `pbp.SEASONS`, and
when present preserve the first occurrence of each selected season in command
order. Keep all exceptions on the CLI's existing error-reporting path.

Add exactly:

```gitignore
data/parquet-large/
```

Update `REQUIRED_GITIGNORE_RULES` in `scripts/verify_repository.py`; do not
weaken the existing fresh-clone, large-file, or generated-data checks.

- [x] **Step 5: Run Task 7 tests and repository verification**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_cli.py \
  tests/test_verify_repository.py \
  tests/test_repository_layout.py \
  -q
.venv/bin/python scripts/verify_repository.py --max-git-mib 100
```

Expected: both commands PASS.

- [x] **Step 6: Commit Task 7**

```bash
git add \
  .gitignore \
  src/nba_data/__main__.py \
  scripts/verify_repository.py \
  tests/test_cli.py \
  tests/test_verify_repository.py \
  tests/test_repository_layout.py
git commit -m "feat: expose play-by-play archive commands"
```

### Task 8: Create and restore one immutable raw-plus-Parquet snapshot

**Files:**
- Create: `src/nba_data/pbp_snapshot.py`
- Create: `scripts/archive_pbp_snapshot.sh`
- Create: `scripts/restore_pbp_snapshot.sh`
- Create: `tests/test_pbp_raw_snapshot.py`

**Interfaces:**
- Archive defaults:
  - `NBA_PBP_DATA_DIR=$REPOSITORY_ROOT/data`
  - `NBA_PBP_ARCHIVE_ROOT=/mnt/nas/llm/documents/nba-predictions-archive`
  - raw source `$NBA_PBP_DATA_DIR/raw/stats_nba/pbp`
  - publication source `$NBA_PBP_DATA_DIR/parquet-large/pbp`
- Restore requires `NBA_PBP_SNAPSHOT` as an exact existing snapshot path and
  `NBA_PBP_MANIFEST_SHA256` as the trusted full 64-character manifest digest.
- Restore destinations default to the two source locations above and may be
  overridden with `NBA_PBP_RAW_DESTINATION` and
  `NBA_PBP_PUBLICATION_DESTINATION`.
- `python -m nba_data.pbp_snapshot validate-layout --raw-root PATH
  --publication-root PATH [--manifest-sha256 SHA256]` performs semantic,
  self-contained offline validation.
- `python -m nba_data.pbp_snapshot rebuild-compare --raw-root PATH
  --publication-root PATH --stage-parent PATH` rebuilds into a unique real
  child of `stage-parent`, byte-compares the complete trees, and removes only
  that verified temporary child.
- `python -m nba_data.pbp_snapshot write-audit --data-dir PATH
  --snapshot PATH --manifest-sha256 SHA256 --output PATH
  --reproducible-build-verified --restore-verified` revalidates all local and
  snapshot facts, derives the source-spike evidence, and writes the final
  deterministic Markdown.

- [x] **Step 1: Write failing archive-script tests**

Use subprocess tests with temporary data/archive roots and a programmatically
generated all-30-season complete fixture inventory/publication. Keep each
season/type response and PBP body minimal so the test remains small. Cover:

```text
successful immutable snapshot creation
snapshot name pbp-archive-2026-<manifest-sha12>
exact raw/stats_nba/pbp/ and parquet-large/pbp/ subtrees
sorted SHA256SUMS covering every regular file except SHA256SUMS itself
idempotent success when the same exact snapshot already exists
rejection when an existing same-name snapshot differs
rejection of incomplete or invalid source inventory/publication
rejection of symlink, FIFO, socket, or stale staging entry
capacity required minus one fails; exact required bytes passes
concurrent destination appearance before final rename fails closed
stage cleanup after pre-publication failure
```

The test passes a command-scoped `PATH` containing deterministic `df`, `du`,
and `rsync` shims only where exact boundary/error injection is required.

- [x] **Step 2: Write failing restore and inverse tests**

Cover:

```text
successful restore into two absent destinations
trusted full digest differs from snapshot manifest
malformed or tampered SHA256SUMS
checksum-valid but unsafe path
snapshot structure missing or containing an additional path
either destination already exists
capacity required minus one fails; exact required bytes passes
first destination rename succeeds and second fails
rollback after second-destination failure
rollback failure retains named recovery paths
offline rebuild from restored raw bytes is byte-identical to restored publication
rebuild-compare rejects installed package or Python versions unequal to the manifest
post-publication revalidation covers both restored destinations
audit command rejects either missing execution-evidence flag
audit command rejects source-spike or cross-source mismatches
```

For the success case:

1. archive a fixture source;
2. restore to two absent temporary destinations;
3. run `pbp.validate_cache_tree(restored_raw, required_seasons=pbp.SEASONS,
   require_complete=True)`;
4. run `pbp_build.validate_publication(restored_raw, restored_publication)`;
5. run `pbp_snapshot.rebuild_compare` into a third empty stage; and
6. compare every publication file byte-for-byte.

Patch DNS and all request methods to raise throughout restore tests.

- [x] **Step 3: Run script tests to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_pbp_raw_snapshot.py -q
```

Expected: FAIL because the scripts do not exist.

- [x] **Step 4: Implement archive preflight and immutable publication**

In `scripts/archive_pbp_snapshot.sh`, use `set -euo pipefail`, resolve the
repository root from the script path, set `LC_ALL=C` and `umask 077`, and
delegate semantic validation to:

```bash
.venv/bin/python -m nba_data.pbp_snapshot validate-layout \
  --raw-root "$NBA_PBP_DATA_DIR/raw/stats_nba/pbp" \
  --publication-root "$NBA_PBP_DATA_DIR/parquet-large/pbp"
```

Implement all three `pbp_snapshot` subcommands as a dedicated offline module.
`validate-layout` calls `pbp.validate_cache_tree` for all 30 seasons and
`pbp_build.validate_publication`, then optionally compares the manifest digest
with the exact lowercase trusted argument. `rebuild-compare` first requires
the installed Python and package versions to equal the publication
manifest's `versions` object — the pinned `requirements.txt` environment —
and stops on any mismatch before comparing bytes; it then calls
`pbp_build.build_tree(raw_root, temporary_stage, seasons=pbp.SEASONS)`,
validates it, and requires an exact relative-path and byte comparison with the
given publication. `write-audit` verifies the snapshot checksum inventory and
manifest pin, revalidates the live cache/publication and cross-source equality,
derives the four source-spike rows, then calls `render_audit`; both explicit
execution-evidence flags are required. The shell scripts must not duplicate
JSON/Parquet semantics.

Before copying:

1. lstat-walk both sources and reject non-regular entries;
2. compute `source_allocated_bytes` with `du -sB1`;
3. compute destination total/available bytes with `df -B1`;
4. require
   `available >= source_allocated_bytes + floor(total / 5)`; and
5. create a unique real staging directory under the archive root.

Copy with `rsync -a --checksum` into
`raw/stats_nba/pbp/` and `parquet-large/pbp/`. Revalidate the staged copy
offline. Create a POSIX-relative, byte-sorted `SHA256SUMS`, verify it with
`sha256sum --check`, compute the full digest of
`parquet-large/pbp/_manifest.json`, and derive the exact final snapshot name.

If the final path exists, validate its structure, checksums, and manifest
digest. Return success only when it matches the stage exactly by
`rsync -a --checksum --delete --dry-run`; otherwise fail without changing it.
For a new snapshot, recheck destination absence and atomically rename the
stage. Never modify a published snapshot.

- [x] **Step 5: Implement trusted, absent-target restore**

In `scripts/restore_pbp_snapshot.sh`:

1. require an exact `NBA_PBP_SNAPSHOT` directory and trusted full manifest
   digest;
2. reject anything except the exact `raw/stats_nba/pbp/`,
   `parquet-large/pbp/`, and `SHA256SUMS` snapshot layout;
3. parse `SHA256SUMS` before checksum access, requiring each line to be
   `<64 lowercase hex><two spaces><normalized POSIX relative path>`, rejecting
   absolute paths, empty/dot/dot-dot components, backslashes, duplicates, and
   any entry-set difference from the lstat-derived regular-file inventory;
4. run `sha256sum --check` from the exact snapshot root;
5. verify the manifest digest with constant string equality;
6. semantically validate both snapshot trees offline;
7. require both final destinations to be absent real paths;
8. require each destination parent to be a real directory, group destination
   parents by filesystem device ID, sum the allocated source-tree bytes
   landing on each device, and enforce
   `available >= grouped_source_allocated + floor(total / 5)` once per
   device;
9. copy into two unique sibling staging directories;
10. validate the copies;
11. rebuild from staged raw into a third sibling stage and require exact
    byte equality with staged publication;
12. publish the two destinations with a rollback state machine; and
13. revalidate both published destinations offline — complete cache-tree
    validation of the restored raw root and `validate_publication` of the
    restored publication — and fail on any mismatch.

If the second rename fails, rename the first destination back to its staging
name so neither final destination remains. If rollback fails, stop and print
every retained path; never delete ambiguous recovery data. Cleanup traps may
remove only exact staging paths created by the current process and only after
lstat confirms real directories.

- [x] **Step 6: Validate shell syntax and run Task 8 tests**

Run:

```bash
bash -n scripts/archive_pbp_snapshot.sh
bash -n scripts/restore_pbp_snapshot.sh
.venv/bin/python -m pytest \
  tests/test_pbp_raw_snapshot.py \
  tests/test_pbp_build.py \
  tests/test_pbp.py \
  -q
```

Expected: PASS.

- [x] **Step 7: Commit Task 8**

```bash
chmod +x scripts/archive_pbp_snapshot.sh scripts/restore_pbp_snapshot.sh
git add \
  scripts/archive_pbp_snapshot.sh \
  scripts/restore_pbp_snapshot.sh \
  src/nba_data/pbp_snapshot.py \
  tests/test_pbp_raw_snapshot.py
git commit -m "feat: archive and restore play-by-play snapshots"
```

### Task 9: Run the source spike, complete acquisition, and reproducibility gate

**Files:**
- Generate, never commit:
  `data/raw/stats_nba/pbp/`
  and `data/parquet-large/pbp/`.

**Interfaces:**
- Consumes all Task 1–8 tests and commands.
- Produces the complete local raw inventory and a twice-reproduced active
  publication.

- [x] **Step 1: Confirm prerequisites before any NBA request**

Run:

```bash
git status --short
test -f data/parquet/games.parquet
test -f data/parquet/playoff_games.parquet
test -f data/parquet/playoff_games_manifest.json
.venv/bin/python -m pytest \
  tests/test_pbp.py \
  tests/test_pbp_build.py \
  tests/test_publication.py \
  tests/test_pbp_raw_snapshot.py \
  tests/test_cli.py \
  -q
.venv/bin/python scripts/verify_repository.py --max-git-mib 100
```

Expected: status is clean and every test/check passes. Stop if the
post-integration artifacts or publication module are absent.

- [x] **Step 2: Fetch all dedicated worklists before the PBP source spike**

Run:

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
from nba_data import pbp

data_dir = Path("data")
worklist = pbp.fetch_worklists(data_dir, pbp.SEASONS)
pbp.cross_validate_worklist(worklist, data_dir, pbp.SEASONS)
assert set(worklist.loc[worklist.game_type == "regular", "season"]) == set(
    pbp.SEASONS
)
assert set(worklist.loc[worklist.game_type == "playoff", "season"]) == set(
    pbp.SEASONS
)
assert (
    worklist.loc[worklist.game_type == "play_in"]
    .groupby("season")
    .size()
    .to_dict()
) == {2020: 1, 2021: 6, 2022: 6, 2023: 6, 2024: 6, 2025: 6, 2026: 6}
inventory = pbp.validate_cache_inventory(
    data_dir,
    required_seasons=pbp.SEASONS,
    require_complete=False,
)
assert len(inventory.worklist_bodies) == 67
print(worklist.groupby(["season", "game_type"]).size().to_string())
PY
```

Expected:

```text
67 checksum-valid worklist pairs
regular and playoff rows for all 30 ending seasons
play_in rows of 1 in 2020 and 6 in each 2021..2026 season
no unmatched regular or postseason publication rows
```

Do not fetch any game body if cross-validation fails.

- [x] **Step 3: Fetch and parse the four representative games**

Select exactly `pbp.SOURCE_SPIKE_GAME_IDS` from the cross-validated worklist,
call `pbp.fetch_games`, then parse each cached body with its worklist row.
Require:

```text
0029600001  500 events  periods 1..4  terminal home 98 away 107
0049600001  404 events  periods 1..4  terminal home 109 away 99
0051900111  510 events  periods 1..4  terminal home 126 away 122
0022500001  611 events  periods 1..6  terminal home 125 away 124
```

Also require the early response to exercise the paired `"0"`/`"0"`
historical sentinel and the modern response to exercise paired blanks.
Stop for design review on any mismatch or additional/missing action key.

Run:

```bash
.venv/bin/python - <<'PY'
from pathlib import Path

from nba_data import pbp, pbp_build

data_dir = Path("data")
worklist = pbp.load_worklist(
    data_dir,
    pbp.SEASONS,
    require_complete=True,
)
spike = worklist[
    worklist.game_id.isin(pbp.SOURCE_SPIKE_GAME_IDS)
].copy()
assert tuple(spike.game_id.sort_values()) == tuple(
    sorted(pbp.SOURCE_SPIKE_GAME_IDS)
)
missing = sum(
    not pbp.game_path(data_dir, int(row.season), row.game_id).exists()
    for row in spike.itertuples()
)
pbp.require_fetch_capacity(data_dir, missing)
pbp.fetch_games(data_dir, spike)
expected = {
    "0029600001": (500, 4, 98, 107),
    "0049600001": (404, 4, 109, 99),
    "0051900111": (510, 4, 126, 122),
    "0022500001": (611, 6, 125, 124),
}
reports = {}
for row in spike.to_dict(orient="records"):
    game_id = str(row["game_id"])
    payload = pbp.game_path(
        data_dir, int(row["season"]), game_id
    ).read_bytes()
    frame, report = pbp_build.parse_game_payload(payload, row)
    events, max_period, home, away = expected[game_id]
    assert (len(frame), report["max_period"]) == (events, max_period)
    assert (
        int(frame.iloc[-1]["score_home"]),
        int(frame.iloc[-1]["score_away"]),
    ) == (home, away)
    assert frame["period"].drop_duplicates().tolist() == list(
        range(1, max_period + 1)
    )
    reports[game_id] = report
assert reports["0029600001"]["historical_zero_sentinels"] > 0
assert reports["0022500001"]["blank_score_pairs"] > 0
print(reports)
PY
```

- [x] **Step 4: Run the long resumable acquisition**

Run:

```bash
.venv/bin/python -m nba_data fetch-pbp
```

Expected runtime is approximately 20–26 hours at the mandatory serialized
pace. Keep only one fetch process running. If interrupted, rerun the same
command; valid complete pairs must be skipped. On any failure, preserve the
raw body/sidecar evidence and diagnose before continuing.

- [x] **Step 5: Validate the complete inventory offline**

Run:

```bash
.venv/bin/python - <<'PY'
from pathlib import Path

from nba_data import pbp

inventory = pbp.validate_cache_inventory(
    Path("data"),
    required_seasons=pbp.SEASONS,
    require_complete=True,
)
assert len(inventory.worklist_bodies) == 67
assert len(inventory.game_bodies) == len(inventory.worklist)
pbp.cross_validate_worklist(
    inventory.worklist,
    Path("data"),
    pbp.SEASONS,
)
print(
    inventory.worklist.groupby(["season", "game_type"])
    .size()
    .to_string()
)
PY
```

Print total games by season/type and compare the worklist bidirectionally
again. Expected: no missing, orphaned, unsafe, or mismatched cache pair.

- [x] **Step 6: Build twice and prove byte reproducibility**

Run:

```bash
.venv/bin/python -m nba_data build-pbp
```

Create an exact hard-linked baseline of `data/parquet-large/pbp` in a unique
temporary directory on the same filesystem, then run the build command again.
Compare baseline and active trees with:

```bash
PBP_BASELINE_PARENT="$(
  mktemp -d -p data/parquet-large .pbp-baseline-XXXXXXXX
)"
cp -al data/parquet-large/pbp "$PBP_BASELINE_PARENT/pbp"
.venv/bin/python -m nba_data build-pbp
rsync -a --checksum --delete --dry-run \
  "$PBP_BASELINE_PARENT/pbp/" data/parquet-large/pbp/
cmp "$PBP_BASELINE_PARENT/pbp/_manifest.json" \
  data/parquet-large/pbp/_manifest.json
```

Expected: `rsync` prints no changes and `cmp` exits 0. Remove only the exact
temporary baseline after successful comparison.

- [x] **Step 7: Checkpoint Task 9**

Run:

```bash
git status --short
```

Expected: clean; all raw and large Parquet outputs are ignored. Task 9 has no
commit.

### Task 10: Publish the immutable archive, verify restore, and record acceptance

**Files:**
- Create: `docs/audits/2026-07-31-play-by-play-archive.md`
- Modify: `docs/data-dictionary.md`
- Modify: `README.md`
- Modify: `tests/test_repository_layout.py`

**Interfaces:**
- Consumes the twice-reproduced publication from Task 9 and Task 8 scripts.
- Produces one immutable NAS snapshot, an independently verified restore, and
  the committed audit/documentation record.

- [x] **Step 1: Archive the complete recovery unit**

Run:

```bash
scripts/archive_pbp_snapshot.sh
```

Capture the script's exact final snapshot path and full manifest SHA-256.
Require:

```text
snapshot basename pbp-archive-2026-<first 12 manifest SHA-256 characters>
SHA256SUMS verifies every raw and publication regular file
staged semantic validation passed
published snapshot is immutable by process convention
```

Do not edit or replace an existing snapshot. If a same-name snapshot differs,
stop for operator review.

- [x] **Step 2: Independently restore into absent temporary destinations**

Create a unique temporary parent on a filesystem that passes the restore
capacity predicate. Set:

```bash
export NBA_PBP_SNAPSHOT="$SNAPSHOT_PATH"
export NBA_PBP_MANIFEST_SHA256="$FULL_MANIFEST_SHA256"
export NBA_PBP_RESTORE_PARENT="$(
  mktemp -d -p data/parquet-large .pbp-restore-XXXXXXXX
)"
export NBA_PBP_RAW_DESTINATION="$NBA_PBP_RESTORE_PARENT/raw"
export NBA_PBP_PUBLICATION_DESTINATION="$NBA_PBP_RESTORE_PARENT/publication"
scripts/restore_pbp_snapshot.sh
```

Require the restore script's checksum verification, semantic validation,
offline rebuild, and byte comparison all to pass. Independently compare:

```bash
rsync -a --checksum --delete --dry-run \
  data/raw/stats_nba/pbp/ "$NBA_PBP_RAW_DESTINATION/"
rsync -a --checksum --delete --dry-run \
  data/parquet-large/pbp/ "$NBA_PBP_PUBLICATION_DESTINATION/"
```

Expected: both commands print no changes. Remove only this exact verified
temporary restore tree.

- [x] **Step 3: Render the final audit from validated facts**

Run:

```bash
.venv/bin/python -m nba_data.pbp_snapshot write-audit \
  --data-dir data \
  --snapshot "$NBA_PBP_SNAPSHOT" \
  --manifest-sha256 "$NBA_PBP_MANIFEST_SHA256" \
  --output docs/audits/2026-07-31-play-by-play-archive.md \
  --reproducible-build-verified \
  --restore-verified
```

The command validates the active raw inventory/publication, reruns
`cross_validate_worklist` against the integrated regular and postseason
publications, recomputes and pins the manifest digest, derives and
cross-checks the four source-spike rows, verifies the snapshot checksum
inventory, and only then passes a fully true `AuditEvidence` to
`render_audit`.

The committed audit contains no credentials, local absolute source paths, raw
responses, or transient staging paths.

- [x] **Step 4: Write failing documentation/layout tests**

Extend `tests/test_repository_layout.py` to require:

```text
executable archive and restore scripts
final audit path
README links to the PBP data dictionary section and audit
data dictionary lists all EVENT_COLUMNS in exact order
data dictionary distinguishes raw score strings from derived running scores
data dictionary states (game_id, action_id) as the key
data dictionary lists all three game types and ending seasons 1997..2026
data dictionary documents fetch/build/archive/restore commands
generated data paths remain ignored and untracked
```

Run:

```bash
.venv/bin/python -m pytest tests/test_repository_layout.py -q
```

Expected: FAIL until README and data dictionary are updated.

- [x] **Step 5: Document the completed archive**

In `docs/data-dictionary.md`, add:

- the 29-column fixed logical schema in `EVENT_COLUMNS` order with Arrow
  type, nullability, and source/derived origin;
- source 23-key drift behavior;
- score-sentinel and running-score semantics;
- worklist construction/cross-validation;
- game types, season scope, key, ordering, and partition layout;
- manifest/provenance guarantees;
- fetch, build, archive, and restore commands; and
- the immutable snapshot/recovery verification contract.

In `README.md`, add the ignored large-data layout, current archive status, and
links to the data dictionary and final audit. State that a clone does not
contain raw PBP or large Parquet data.

- [x] **Step 6: Run the full acceptance suite**

Run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -m integration -q
.venv/bin/python scripts/verify_repository.py --max-git-mib 100
bash -n scripts/archive_pbp_snapshot.sh
bash -n scripts/restore_pbp_snapshot.sh
git diff --check
git status --short
```

Expected:

```text
all unit tests pass
all local-cache integration tests pass without network
repository verification reports Git data below 100 MiB
both scripts pass syntax validation
git diff --check prints nothing
only README, data dictionary, final audit, and layout-test changes are pending
```

- [x] **Step 7: Commit the acceptance record**

```bash
git add \
  README.md \
  docs/data-dictionary.md \
  docs/audits/2026-07-31-play-by-play-archive.md \
  tests/test_repository_layout.py
git commit -m "docs: record verified play-by-play archive"
```

- [x] **Step 8: Leave the branch ready for review**

Run:

```bash
git status --short
git log --oneline --decorate -12
```

Expected: clean worktree with the implementation commits visible. Do not
push, merge, deploy, or remove the worktree. Report the branch/worktree path,
commit list, test results, snapshot basename, full manifest SHA-256, and any
retained recovery directory.

## Spec Coverage Matrix

| Approved design requirement | Implemented by |
|---|---|
| Post-playoff integration dependency | Global Constraints; Task 9 Step 1 |
| 67 fresh dedicated worklists | Tasks 2–4; Task 9 Step 2 |
| Exact raw body/sidecar cache and no silent overwrite | Tasks 3–4 |
| `stats.nba.com` serialized pacing and bounded retries | Task 4 |
| Fixed 23-key V3 projection and 29-column event schema | Tasks 1 and 6 |
| `(game_id, action_id)` key; duplicate action number allowed | Task 1 |
| Historical score sentinel and modern blank handling | Task 1 |
| Worklist equality against regular and postseason publications | Task 2 |
| Fetch capacity predicate | Task 4 |
| One deterministic Parquet partition per ending season | Task 6 |
| Deterministic manifest, provenance, and anomaly report | Task 6 |
| Transactional directory publication | Task 5 and Task 6 |
| Ignored raw/large data and fresh Git clone below 100 MiB | Task 7 and Task 10 |
| Immutable combined raw-plus-Parquet NAS snapshot | Task 8 and Task 10 |
| Archive/restore 20% capacity predicate | Task 8 |
| Trusted checksum pin and absent-target restore | Task 8 |
| Offline rebuild proves raw-to-publication recovery | Task 8 and Task 10 |
| Representative early/playoff/play-in/overtime spike | Task 9 |
| Full acquisition and two byte-identical builds | Task 9 |
| Final audit only after independent restore | Task 6 and Task 10 |
| Unmerged, unpushed branch handoff | Global Constraints; Task 10 Step 8 |

## Completion Definition

The work is complete only when Tasks 1–10 are checked, the complete raw
inventory and active publication validate offline, two consecutive builds are
byte-identical, the combined immutable snapshot validates, an independent
absent-target restore and rebuild comparison pass, the audit records those
facts, all tests and repository checks pass, and the implementation remains
on a clean unmerged branch awaiting explicit review direction.
