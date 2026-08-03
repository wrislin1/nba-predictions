# Player Games & Workload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Acquire, normalize, validate, publish, and recovery-test one NBA player-appearance/workload row for every regular-season, playoff, and play-in game in completed ending seasons 1984–2026.

**Architecture:** A network-owning `player_logs` module defines the 93 immutable `LeagueGameLog` cache identities, validates body/sidecar pairs, serializes requests, and normalizes one response. An offline `player_games_build` module validates the complete cache against the existing regular/postseason publications, enforces a human-reviewed exact exception ledger, writes one deterministic Parquet partition per season plus a manifest, and transactionally publishes the tree. A small audit command creates the mandatory source-review artifacts, while a snapshot validator and two shell scripts archive and restore the raw cache and publication as one immutable NAS unit.

**Tech Stack:** Python 3.12, pandas, PyArrow/Parquet with Zstandard compression, `nba_api`, requests, pytest, Bash, rsync, SHA-256, Git.

## Global Constraints

- The governing design is `docs/superpowers/specs/2026-08-02-player-games-workload-design.md`; the umbrella roadmap is `docs/superpowers/specs/2026-07-31-player-availability-foundation-design.md`.
- At execution time, create or enter an isolated worktree with `superpowers:using-git-worktrees`. Do not implement directly on `master`.
- Approval of this plan is not permission to merge or push. Finish with committed changes on an unmerged branch ready for separate-session review. Do not push, merge, deploy, or remove the worktree without explicit direction.
- Python support remains `>=3.12`; use only existing direct dependencies. Do not add another package.
- Ending seasons are exactly the integers `1984..2026`. This plan does not fetch a mutable in-progress season and does not extend to 2027.
- Game types are exactly `regular` and `playoff` for all 43 seasons, plus `play_in` for ending seasons `2020..2026`: exactly 93 raw identities.
- `fetch-player-logs` is the only network path. Parsing, auditing, building, publication validation, tests, archive validation, and restore validation are offline.
- Request starts are at least 1.5 seconds apart and never parallel. Transient failures receive five total attempts with 5/10/20/40-second delays; numeric `Retry-After` is honored only through 120 seconds.
- Use `stats_nba.preflight_dns()` before requesting `stats.nba.com`. Complete valid cache pairs are never overwritten or re-fetched.
- Raw bodies and sidecars live only under gitignored `data/raw/stats_nba/player_logs/`. Large outputs and their manifest live only under gitignored `data/parquet-large/player_games/`.
- The table primary key is `(game_id, person_id)`. A zero-minute credited row is an appearance; missing values are never filled with zero.
- Regular coverage is exact against `games.parquet` and `team_games.parquet`. Postseason coverage is exact against `playoff_games.parquet` on `(season, game_date, game_type, home_franchise_id, away_franchise_id)`.
- Player-point or player-minute discrepancies may pass only through exact entries in `src/nba_data/reference/player_game_validation_exceptions.json`. Unlisted, value-drifted, and stale entries fail.
- The exception ledger starts empty. After the full source audit, implementation pauses for explicit user review before any candidate entry is committed.
- The user approved the 2026-08-03 Task 6 recommendation for an exact 36-row matchup allowlist. Only entries in `src/nba_data/reference/player_game_matchup_exceptions.json` may repair derived matchup values; raw `matchup_raw` remains unchanged, live same-game consensus and downstream reference coverage remain mandatory, and unlisted, stale, duplicate, or drifted entries fail.
- Write exactly one deterministic `player_games.parquet` for every `season=1984` through `season=2026`, then `_manifest.json`. Rebuilds from the same cache and ledger must be byte-identical.
- The immutable snapshot root is `/mnt/nas/llm/documents/nba-predictions-archive/player-games-archive-2026-<manifest-sha12>/`, overridable only by the documented environment variables.
- Unit tests use small source-shaped fixtures and never access the network. Integration checks read complete local caches only.
- Roster stints, absence inference, injuries, schedule features, travel, lineups, play-by-play usage, tracking metrics, and future-season automation remain out of scope.

---

## File Structure

### Create

`src/nba_data/player_logs.py`
    Source scope/constants, cache paths and metadata identities, response-envelope validation, player-row normalization, cache inventory checks, request pacing/retries, capacity check, and resumable acquisition.

`src/nba_data/player_games_build.py`
    Exact Arrow schema, whole-cache loading, regular/postseason coverage joins, team-game aggregation, discrepancy collection, exception-ledger enforcement, per-season Parquet writing, manifest assembly, independent publication validation, transactional publication, and Markdown audit rendering.

`src/nba_data/player_games_snapshot.py`
    Offline snapshot layout, checksum, manifest-pin, raw-cache, and publication validation used by both recovery scripts.

`src/nba_data/reference/player_game_validation_exceptions.json`
    Exact reviewed `points` and `minutes` exceptions. It begins as `{"minutes": {}, "points": {}}` and changes only after Task 6's user-review checkpoint.

`src/nba_data/reference/player_game_matchup_exceptions.json`
    Canonical schema-versioned exact allowlist for the 36 reviewed Charles Oakley source rows enumerated in `.superpowers/sdd/2026-08-02-player-games-workload/task-6-matchup-diagnostic.md`. This ledger is approved now; it is separate from the still-pending point/minute candidate review.

`tests/test_player_logs.py`
    Source identity, payload, normalization, cache, pacing, retry, capacity, and resumable-fetch tests.

`tests/test_player_games_build.py`
    Arrow schema, coverage, aggregate, points/minutes gate, exception-ledger, partition, manifest, publication, validation, and audit tests.

`tests/test_player_games_snapshot.py`
    Archive/restore layout, checksum, immutability, existing-target, tamper, capacity, signal, and copy-race tests.

`scripts/audit_player_logs.py`
    Offline deterministic source-review command that writes `source-audit.md` and `player_game_validation_exceptions.json` to an explicit output directory and can render the final committed audit after publication.

`scripts/archive_player_games_snapshot.sh`
`scripts/restore_player_games_snapshot.sh`
    Immutable combined raw-plus-Parquet snapshot and exact absent-target restore.

`docs/audits/2026-08-02-player-games-source-audit.md`
    Generated only after the complete cache, reviewed exception ledger, two reproducible builds, archive validation, and restore verification pass.

### Modify

`src/nba_data/__main__.py:1-107`
    Import the new modules and add repeatable `fetch-player-logs --season` plus offline `build-player-games` dispatch.

`tests/test_cli.py:1-505`
    Verify full/selected fetch dispatch, input rejection, de-duplication, and strict offline build dispatch.

`README.md:32-170`
    Add the player-games large-data publication, commands, limitations, and links to the data dictionary and final audit.

`docs/data-dictionary.md:50-98,597-end`
    Add the exact player-games schema, source/cache/coverage/exception contracts, manifest, commands, and recovery snapshot.

`tests/test_repository_layout.py:120-150,1080-end`
    Require the final audit/docs contract and continue proving no raw or large publication file is tracked.

`scripts/verify_repository.py:1-80`
`tests/test_verify_repository.py`
    Include the new final audit and snapshot scripts in deterministic repository verification without weakening the existing `data/parquet-large/` protection.

### Generated but never committed

```text
data/raw/stats_nba/player_logs/season=<season>/<game_type>.json
data/raw/stats_nba/player_logs/season=<season>/<game_type>.json.meta.json
data/parquet-large/player_games/_manifest.json
data/parquet-large/player_games/season=<season>/player_games.parquet
/tmp/player-game-source-review/source-audit.md
/tmp/player-game-source-review/player_game_validation_exceptions.json
```

## Shared Contracts

Define these exact names in `src/nba_data/player_logs.py`:

```python
SEASONS = tuple(range(1984, 2027))
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
MIN_REQUEST_INTERVAL_S = 1.5
RETRY_DELAYS_S = (5.0, 10.0, 20.0, 40.0)
MAX_RETRY_AFTER_S = 120.0
FETCH_HEADROOM_BYTES = 5_368_709_120
MATCHUP_EXCEPTIONS_PATH = (
    Path(__file__).resolve().parent
    / "reference/player_game_matchup_exceptions.json"
)

PLAYER_GAME_COLUMNS = [
    "game_id", "person_id", "season", "game_type", "game_date",
    "player_name", "nba_team_id", "team_name_raw",
    "team_abbreviation_raw", "matchup_raw", "franchise_id",
    "opponent_franchise_id", "is_home", "min", "pts", "fgm", "fga",
    "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "reb", "ast",
    "stl", "blk", "tov", "pf", "plus_minus",
]

BOX_COLUMNS = (
    "min", "pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta",
    "oreb", "dreb", "reb", "ast", "stl", "blk", "tov", "pf",
    "plus_minus",
)
```

Required source headers are:

```python
REQUIRED_HEADERS = {
    "SEASON_ID", "PLAYER_ID", "PLAYER_NAME", "TEAM_ID",
    "TEAM_ABBREVIATION", "TEAM_NAME", "GAME_ID", "GAME_DATE",
    "MATCHUP", "WL", "MIN", "FGM", "FGA", "FG_PCT", "FG3M",
    "FG3A", "FG3_PCT", "FTM", "FTA", "FT_PCT", "OREB", "DREB",
    "REB", "AST", "STL", "BLK", "TOV", "PF", "PTS", "PLUS_MINUS",
}
```

Use these public functions and signatures:

```python
def season_label(season: int) -> str: ...
def applicable_game_types(season: int) -> tuple[str, ...]: ...
def cache_path(data_dir: Path, season: int, game_type: str) -> Path: ...
def validate_payload_bytes(payload: bytes) -> None: ...
def validate_payload(path: Path) -> None: ...
def read_cached(
    data_dir: Path, season: int, game_type: str
) -> tuple[bytes, dict] | None: ...
def normalize_payload(
    payload: bytes,
    *,
    season: int,
    game_type: str,
    matchup_exceptions: Mapping[str, Mapping[str, object]] | None = None,
) -> pd.DataFrame: ...
def parse_matchup_exceptions(payload: bytes) -> dict[str, dict]: ...
def load_matchup_exceptions(
    path: Path = MATCHUP_EXCEPTIONS_PATH,
) -> dict[str, dict]: ...
def normalize_payload_with_matchup_evidence(
    payload: bytes,
    *,
    season: int,
    game_type: str,
    matchup_exceptions: Mapping[str, Mapping[str, object]],
) -> tuple[pd.DataFrame, dict[str, dict]]: ...
def available_bytes(path: Path) -> int: ...
def validate_cache_inventory_root(raw_root: Path) -> tuple[Path, ...]: ...
def validate_cache_inventory(data_dir: Path) -> tuple[Path, ...]: ...
def fetch(
    data_dir: Path, seasons: Iterable[int] = SEASONS
) -> None: ...
```

Use these exact names in `src/nba_data/player_games_build.py`:

```python
EXCEPTIONS_PATH = (
    Path(__file__).resolve().parent
    / "reference/player_game_validation_exceptions.json"
)
MATCHUP_EXCEPTIONS_PATH = player_logs.MATCHUP_EXCEPTIONS_PATH
MINUTE_TOLERANCE = 5
MAX_OVERTIME_PERIODS = 6

def aggregate_team_games(player_games: pd.DataFrame) -> pd.DataFrame: ...
def cross_validate_coverage(
    team_aggregates: pd.DataFrame,
    games: pd.DataFrame,
    team_games: pd.DataFrame,
    playoff_games: pd.DataFrame,
) -> pd.DataFrame: ...
def collect_discrepancies(validated_team_games: pd.DataFrame) -> dict: ...
def load_exceptions(path: Path = EXCEPTIONS_PATH) -> dict: ...
def validate_exceptions(observed: dict, approved: dict) -> None: ...
def audit_source(data_dir: Path) -> dict: ...
def write_source_review(output_dir: Path, audit: dict) -> None: ...
def build_from_roots(
    raw_root: Path,
    reference_dir: Path,
    stage_dir: Path,
) -> dict: ...
def build_tree(data_dir: Path, stage_dir: Path) -> dict: ...
def validate_publication(
    raw_root: Path,
    publication_dir: Path,
    reference_dir: Path,
) -> dict: ...
def render_final_audit(
    manifest: dict,
    *,
    manifest_sha256: str,
    snapshot_name: str,
    restore_verified: bool,
) -> str: ...
def run(data_dir: Path) -> None: ...
```

`collect_discrepancies` and the committed ledger use a canonical JSON object
with exactly the top-level keys `minutes` and `points`. Each section is keyed
by `<10-digit-game-id>|<franchise-id>`. A `points` value contains exactly
`game_id: str`, `franchise_id: str`, `player_total: int | null`,
`published_total: int`, `season: int`, and `note: str`. A `minutes` value
contains exactly `game_id: str`, `franchise_id: str`, `player_total: int |
null`, `opponent_total: int | null`, `inferred_overtime_periods: int | null`,
`published_overtime_periods: int | null`, `reason: str`, `season: int`, and
`note: str`. JSON keys are sorted during serialization. The actual initial
ledger is exactly:

```json
{
  "minutes": {},
  "points": {}
}
```

---

### Task 1: Normalize one source response exactly

**Files:**
- Create: `src/nba_data/player_logs.py`
- Create: `tests/test_player_logs.py`

**Interfaces:**
- Consumes: `nba_data.franchises.resolve(name: str, season: int) -> str`; source-shaped `LeagueGameLog` JSON bytes.
- Produces: the constants/path helpers and `normalize_payload(payload: bytes, *, season: int, game_type: str) -> pd.DataFrame` contract in Shared Contracts.

- [ ] **Step 1: Write the source-shaped parser fixture and failing happy-path test**

In `tests/test_player_logs.py`, define the complete source header list and a
helper that serializes one `LeagueGameLog` result set. Include four rows for one
game: two Boston players and two Chicago players. One credited row has `MIN=0`;
all `PLUS_MINUS` values are `None`.

```python
HEADERS = [
    "SEASON_ID", "PLAYER_ID", "PLAYER_NAME", "TEAM_ID",
    "TEAM_ABBREVIATION", "TEAM_NAME", "GAME_ID", "GAME_DATE",
    "MATCHUP", "WL", "MIN", "FGM", "FGA", "FG_PCT", "FG3M",
    "FG3A", "FG3_PCT", "FTM", "FTA", "FT_PCT", "OREB", "DREB",
    "REB", "AST", "STL", "BLK", "TOV", "PF", "PTS", "PLUS_MINUS",
    "FANTASY_PTS", "VIDEO_AVAILABLE",
]


def _row(
    player_id: int,
    player_name: str,
    team_id: int,
    abbreviation: str,
    team_name: str,
    matchup: str,
    minutes: int,
    points: int,
) -> list[object]:
    return [
        "21983", player_id, player_name, team_id, abbreviation, team_name,
        "0028300001", "1983-10-28", matchup, "W" if abbreviation == "BOS" else "L",
        minutes, 1 if points else 0, 2 if points else 0, 0.5 if points else 0.0,
        0, 0, 0.0, 0, 0, 0.0, 0, 1 if minutes else 0,
        1 if minutes else 0, 1 if minutes else 0, 0, 0, 0, 1 if minutes else 0,
        points, None, 0.0, 0,
    ]


REGULAR_ROWS = [
    _row(1, "Chicago One", 1610612741, "CHI", "Chicago Bulls", "CHI @ BOS", 48, 2),
    _row(2, "Chicago Zero", 1610612741, "CHI", "Chicago Bulls", "CHI @ BOS", 0, 0),
    _row(3, "Boston One", 1610612738, "BOS", "Boston Celtics", "BOS vs. CHI", 48, 2),
    _row(4, "Boston Two", 1610612738, "BOS", "Boston Celtics", "BOS vs. CHI", 24, 2),
]


def _payload(rows: list[list[object]], headers=HEADERS) -> bytes:
    return json.dumps({
        "resultSets": [{
            "name": "LeagueGameLog",
            "headers": list(headers),
            "rowSet": rows,
        }]
    }).encode("utf-8")


def test_normalize_payload_preserves_source_and_nullable_semantics():
    frame = player_logs.normalize_payload(
        _payload(REGULAR_ROWS), season=1984, game_type="regular"
    )
    assert frame.columns.tolist() == player_logs.PLAYER_GAME_COLUMNS
    assert frame["game_id"].unique().tolist() == ["0028300001"]
    assert frame["person_id"].dtype == "int64"
    assert frame.loc[frame.person_id.eq(2), "min"].item() == 0
    assert frame["plus_minus"].isna().all()
    assert set(frame.loc[frame.is_home, "franchise_id"]) == {"BOS"}
    assert set(frame.loc[~frame.is_home, "franchise_id"]) == {"CHI"}
    assert set(frame.loc[frame.franchise_id.eq("BOS"), "opponent_franchise_id"]) == {"CHI"}
```

- [ ] **Step 2: Run the test and verify the module is absent**

Run:

```bash
.venv/bin/python -m pytest tests/test_player_logs.py::test_normalize_payload_preserves_source_and_nullable_semantics -v
```

Expected: collection fails because `nba_data.player_logs` does not exist.

- [ ] **Step 3: Add constants, source-envelope validation, and normalization**

Implement the Shared Contracts constants. `validate_payload_bytes` must reject
invalid JSON, non-object roots, missing/duplicate/wrong result sets, duplicate
headers, missing required headers, non-list `rowSet`, and row-width mismatch.
`normalize_payload` must:

```python
document = json.loads(payload)
result_set = document["resultSets"][0]
raw = pd.DataFrame(result_set["rowSet"], columns=result_set["headers"])
game_id = raw["GAME_ID"].astype(str).str.zfill(10)
season_id = pd.to_numeric(raw["SEASON_ID"], errors="raise").astype("int64")
ending_season = (season_id % 10_000) + 1
```

Require the requested season and the expected `SEASON_ID` leading digit and
`GAME_ID` prefix. Convert identifiers with exact integer validation (booleans,
fractional values, missing values, or strings with non-digits fail). Convert box
columns with `pd.to_numeric(errors="coerce").astype("Int64")`, but fail if a
non-null source value becomes null. Preserve source null as null.

Parse `MATCHUP` positionally using the same two accepted forms as
`pbp.normalize_worklist_payload`: `<AWAY> @ <HOME>` and
`<HOME> vs. <AWAY>`. Locate each row's own `TEAM_ABBREVIATION` on exactly one
side, group by game, require exactly two team/franchise identities, then assign
the other group's franchise as `opponent_franchise_id`.

- [ ] **Step 4: Add rejection and boundary tests**

Parameterize exact failures for unknown game type, out-of-range season,
SEASON_ID mismatch, prefix mismatch, missing header, duplicate header, row-width
drift, malformed matchup, self abbreviation on neither/both sides, one/three
teams, same resolved franchise, fractional IDs/stats, negative traditional
counting stats, duplicate `(game_id, person_id)`, and an unexpected null in a
required string. Add a neutral-style test where both teams carry identical
`CHI @ BOS` text and positional role detection still produces one home and one
away franchise.

Run:

```bash
.venv/bin/python -m pytest tests/test_player_logs.py -v
```

Expected: all parser tests pass without network access.

- [ ] **Step 5: Commit the parser slice**

```bash
git add src/nba_data/player_logs.py tests/test_player_logs.py
git commit -m "feat: normalize player game logs"
```

---

### Task 2: Add attested caching, serialized fetching, and fetch CLI dispatch

**Files:**
- Modify: `src/nba_data/player_logs.py`
- Modify: `tests/test_player_logs.py`
- Modify: `src/nba_data/__main__.py:1-107`
- Modify: `tests/test_cli.py:1-505`

**Interfaces:**
- Consumes: Task 1 payload validator/paths; `cache.read_attested`, `cache.publish_bytes`, `stats_nba.preflight_dns`, `LeagueGameLog`.
- Produces: `read_cached`, `validate_cache_inventory_root`, `validate_cache_inventory`, `available_bytes`, and `fetch` from Shared Contracts; CLI command `fetch-player-logs`.

- [ ] **Step 1: Write failing cache identity and inventory tests**

Create cache pairs in `tmp_path` with real hashes. Assert `read_cached` accepts
only the exact sidecar identity:

```python
expected = {
    "provider": "stats_nba",
    "endpoint": "LeagueGameLog",
    "season": 1984,
    "season_label": "1983-84",
    "game_type": "regular",
    "season_type": "Regular Season",
    "league_id": "00",
    "player_or_team_abbreviation": "P",
}
```

Test missing half, SHA mismatch, symlink/special entry, wrong identity field,
unexpected file, missing required identity, and body/sidecar mutation during
attestation. Test `validate_cache_inventory_root` requires 93 exact bodies plus
93 sidecars and returns body paths sorted by season then applicable game type;
`validate_cache_inventory` supplies the `data/raw/stats_nba/player_logs` root.

- [ ] **Step 2: Write failing fetch behavior tests**

Monkeypatch `stats_nba.preflight_dns`, `LeagueGameLog`, `time.monotonic`,
`time.sleep`, and `cache.publish_bytes`. Cover:

```python
def test_fetch_retries_four_times_then_publishes(monkeypatch, tmp_path):
    attempts = []
    sleeps = []
    published = []

    class FakeLeagueGameLog:
        def __init__(self, **kwargs):
            attempts.append(kwargs)
            if len(attempts) < 5:
                raise requests.Timeout("transient")

        def get_json(self):
            return _payload(REGULAR_ROWS).decode("utf-8")

    monkeypatch.setattr(player_logs, "LeagueGameLog", FakeLeagueGameLog)
    monkeypatch.setattr(
        player_logs, "applicable_game_types", lambda season: ("regular",)
    )
    monkeypatch.setattr(
        player_logs,
        "available_bytes",
        lambda path: player_logs.FETCH_HEADROOM_BYTES,
    )
    monkeypatch.setattr(player_logs, "read_cached", lambda *args: None)
    monkeypatch.setattr(
        player_logs.stats_nba,
        "preflight_dns",
        lambda: ("203.0.113.1",),
    )
    monkeypatch.setattr(player_logs.time, "sleep", sleeps.append)
    monkeypatch.setattr(
        player_logs.cache,
        "publish_bytes",
        lambda *args: published.append(args),
    )

    player_logs.fetch(tmp_path, [1984])

    assert sleeps == [5.0, 10.0, 20.0, 40.0]
    assert len(attempts) == 5
    assert attempts[-1]["player_or_team_abbreviation"] == "P"
    assert attempts[-1]["season"] == "1983-84"
    assert attempts[-1]["season_type_all_star"] == "Regular Season"
    assert len(published) == 1
```

Add separate tests named
`test_fetch_skips_only_complete_valid_pairs`,
`test_fetch_requests_exact_endpoint_identity`,
`test_fetch_spaces_request_starts_at_least_1_5_seconds`,
`test_fetch_honors_numeric_retry_after_through_120`,
`test_fetch_stops_on_retry_after_over_120`,
`test_fetch_stops_on_non_429_4xx`,
`test_fetch_does_not_replace_invalid_existing_pair`, and
`test_fetch_requires_five_gibibytes_free_before_first_uncached_request`. Each
asserts the named behavior and that no extra endpoint constructor ran.

The fake endpoint must record `league_id`, `player_or_team_abbreviation`,
`season`, `season_type_all_star`, `direction="ASC"`, `sorter="DATE"`, and
`timeout=90`.

- [ ] **Step 3: Run the new tests and verify the cache/fetch functions fail**

```bash
.venv/bin/python -m pytest tests/test_player_logs.py -v
```

Expected: failures name the missing `read_cached`, `validate_cache_inventory`,
and `fetch` behavior.

- [ ] **Step 4: Implement stable cache reads and exact inventory validation**

Use `cache.read_attested(path, validate_payload)` so body and sidecar are read
as one stable snapshot. Compare every expected metadata field exactly; verify
sidecar `content_length == len(payload)`. Root inventory validation must use `lstat`,
refuse symlinks/special files/directories below season directories, and compare
the observed relative path set with:

```python
{
    cache_path(data_dir, season, game_type).relative_to(root)
    for season in SEASONS
    for game_type in applicable_game_types(season)
} | {
    Path(str(cache_path(data_dir, season, game_type).relative_to(root)) + ".meta.json")
    for season in SEASONS
    for game_type in applicable_game_types(season)
}
```

- [ ] **Step 5: Implement capacity, pacing, retry, and immutable fetch behavior**

Before the first uncached request, require at least
`FETCH_HEADROOM_BYTES == 5_368_709_120` available at the nearest existing
ancestor of the raw root. Call `preflight_dns()` once. For each selected season
in first-occurrence order and each applicable game type in canonical order,
read/attest the pair; skip it only if valid.

Use monotonic request-start timestamps. Retry only request exceptions, 429, and
5xx responses; make five total attempts. A success response is published by:

```python
cache.publish_bytes(
    destination,
    endpoint.get_json().encode("utf-8"),
    expected_metadata(season, game_type),
    validate_payload,
)
```

If an existing pair is incomplete or invalid, propagate the integrity error
before any endpoint construction.

- [ ] **Step 6: Add and test CLI parsing**

In `__main__.py`, import `player_logs` and add:

```python
fetch_player_logs = commands.add_parser("fetch-player-logs")
fetch_player_logs.add_argument(
    "--season",
    type=int,
    action="append",
    choices=player_logs.SEASONS,
)
```

Dispatch fetch with the repository `data` directory, not `data/raw`:

```python
selected = (
    tuple(dict.fromkeys(args.season))
    if args.season
    else player_logs.SEASONS
)
player_logs.fetch(data, selected)
```

Task 5 registers `build-player-games` when the implementation exists. In
`tests/test_cli.py`, test default all seasons, repeatable ordered de-duplication,
rejection of 1983/2027/non-integer, and data-dir handling.

- [ ] **Step 7: Run focused tests and commit**

```bash
.venv/bin/python -m pytest tests/test_player_logs.py tests/test_cli.py -q
git add src/nba_data/player_logs.py src/nba_data/__main__.py tests/test_player_logs.py tests/test_cli.py
git commit -m "feat: fetch player game logs"
```

Expected: all focused tests pass; no test opens the network.

---

### Task 3: Build exact season partitions and Arrow schema

**Files:**
- Create: `src/nba_data/player_games_build.py`
- Create: `tests/test_player_games_build.py`
- Create: `src/nba_data/reference/player_game_validation_exceptions.json`

**Interfaces:**
- Consumes: Task 1 normalization, Task 2 complete cache inventory, existing `publication.publish_tree`.
- Produces: exact `PLAYER_GAMES_ARROW_SCHEMA`, cache-to-season assembly, one Zstandard partition per season, and the initial empty exception ledger.

- [ ] **Step 1: Write failing exact-schema and ordering tests**

Define the expected Arrow fields in the exact design order. Assert string fields
are `pa.large_string()`, dates are `pa.date32()`, IDs/counts are `pa.int64()`,
`is_home` is `pa.bool_()`, and only the 17 box fields are nullable.

Create a tiny complete test scope by monkeypatching `SEASONS=(1984, 1985)` and
`PLAY_IN_SEASONS=()`, publishing four valid cache pairs, then call the internal
season assembler. Assert:

```python
assert table.schema == player_games_build.PLAYER_GAMES_ARROW_SCHEMA
assert table.column_names == player_logs.PLAYER_GAME_COLUMNS
assert table.to_pandas()[
    ["season", "game_date", "game_id", "franchise_id", "person_id"]
].equals(
    table.to_pandas()[
        ["season", "game_date", "game_id", "franchise_id", "person_id"]
    ].sort_values(
        ["season", "game_date", "game_id", "franchise_id", "person_id"],
        kind="stable",
    ).reset_index(drop=True)
)
```

- [ ] **Step 2: Run and verify the builder module is absent**

```bash
.venv/bin/python -m pytest tests/test_player_games_build.py -v
```

Expected: collection fails because `nba_data.player_games_build` does not
exist.

- [ ] **Step 3: Implement exact Arrow conversion and partition writing**

Define `PLAYER_GAMES_ARROW_SCHEMA` literally. Never rely on pandas/Arrow type
inference. Convert each normalized season frame with:

```python
table = pa.Table.from_pandas(
    frame,
    schema=PLAYER_GAMES_ARROW_SCHEMA,
    preserve_index=False,
    safe=True,
)
pq.write_table(
    table,
    partition_path,
    compression="zstd",
    use_dictionary=False,
    write_statistics=True,
)
```

After writing, reread the file, require exact schema/order/rows, and reject
duplicate `(game_id, person_id)` across all game types in the season. The
staging tree contains only `_manifest.json` (added in Task 5) and
`season=<YYYY>/player_games.parquet`; any pre-existing stage entry fails.

- [ ] **Step 4: Add nullability, duplicate, compression, and deterministic-byte tests**

Seed invalid required nulls, wrong physical types, cross-game-type duplicate
keys, unexpected stage files, symlink season directories, and a non-Zstandard
rewrite. Write the same table into two fresh directories and assert identical
Parquet SHA-256 values under the pinned environment.

- [ ] **Step 5: Add the exact empty ledger and loader tests**

Create:

```json
{
  "minutes": {},
  "points": {}
}
```

Test `load_exceptions` rejects invalid JSON, non-object roots, extra/missing
top-level keys, non-object sections, malformed composite keys, missing/extra
fields, wrong types, empty notes on non-empty entries, noncanonical key/body
identity, and duplicate semantic identities.

- [ ] **Step 6: Run focused tests and commit**

```bash
.venv/bin/python -m pytest tests/test_player_games_build.py -q
git add src/nba_data/player_games_build.py src/nba_data/reference/player_game_validation_exceptions.json tests/test_player_games_build.py
git commit -m "feat: stage player game partitions"
```

---

### Task 4: Enforce exact coverage, points, minutes, and reviewed exceptions

**Files:**
- Modify: `src/nba_data/player_games_build.py`
- Modify: `tests/test_player_games_build.py`

**Interfaces:**
- Consumes: normalized rows; committed `games.parquet`, `team_games.parquet`, and `playoff_games.parquet`; exact ledger schema from Task 3.
- Produces: `aggregate_team_games`, `cross_validate_coverage`, `collect_discrepancies`, `validate_exceptions`, and `audit_source` from Shared Contracts.

- [ ] **Step 1: Write failing regular and postseason coverage tests**

Build small frames containing one regular, one playoff, and one play-in game.
Regular rows share NBA Stats IDs with `games`/`team_games`; postseason rows use
different Basketball Reference IDs in `playoff_games` but the same composite
identity.

Assert exact one-to-one success, then add independent tests named
`test_regular_coverage_rejects_missing_player_game`,
`test_regular_coverage_rejects_extra_player_game`,
`test_regular_coverage_rejects_team_game_identity_drift`,
`test_postseason_coverage_joins_without_source_game_id`,
`test_postseason_coverage_rejects_duplicate_composite_key`, and
`test_postseason_coverage_rejects_home_away_or_date_drift`. Each failure test
mutates only the named identity field and asserts the error includes the
affected game identity.

The postseason subset is exactly seasons 1984–2026 and game types
`{"playoff", "play_in"}`; tiebreaker and voided rows are excluded.

- [ ] **Step 2: Write failing point discrepancy tests**

Cover exact equality, unlisted mismatch, exact approved mismatch, approved
value drift, stale approved key, and a null player `pts`. Expected messages
include the composite key and both totals.

```python
with pytest.raises(PlayerGamesBuildError, match=r"0028300001\|BOS.*108.*107"):
    player_games_build.validate_exceptions(observed, approved)
```

- [ ] **Step 3: Write failing minute inference tests**

Parameterize team totals and expected outcomes:

```python
[
    (240, 0, 0),
    (241, 0, 1),
    (263, 1, 2),
    (265, 1, 0),
    (290, 2, 0),
]
```

Test null minutes, a 277-minute residual, opposing teams inferring different
overtime counts, a total beyond the six-overtime ceiling, postseason
inferred/published disagreement, exact approved
exception, value drift, and stale exception.

- [ ] **Step 4: Run and verify validation failures**

```bash
.venv/bin/python -m pytest tests/test_player_games_build.py -v
```

Expected: the new coverage and discrepancy tests fail against missing
functions.

- [ ] **Step 5: Implement aggregation and exact source-independent joins**

`aggregate_team_games` groups by the complete team-game identity, records
`player_count`, and uses completeness flags before sums:

```python
points_complete = group["pts"].notna().all()
player_total = int(group["pts"].sum()) if points_complete else None
minutes_complete = group["min"].notna().all()
minute_total = int(group["min"].sum()) if minutes_complete else None
```

Regular coverage joins by `(game_id, franchise_id)` and checks season/date,
opponent, side, and published points. Postseason first pivots the player
aggregates to one home/away game row, then joins by the five-field composite
key. Require uniqueness before every merge and use `validate="one_to_one"` or
`validate="many_to_one"` as appropriate; inspect `_merge` rather than allowing
an inner join to discard evidence.

- [ ] **Step 6: Implement deterministic discrepancy collection**

For each complete minute total, calculate every candidate target explicitly:

```python
candidates = {
    overtime: abs(total - (240 + 25 * overtime))
    for overtime in range(MAX_OVERTIME_PERIODS + 1)
}
minimum = min(candidates.values())
inferred_overtime = next(
    overtime for overtime, residual in candidates.items()
    if residual == minimum
)
```

Integer minute totals make the nearest candidate unique. A candidate with
residual `<=5`, agreement across both teams, and
postseason agreement with the published overtime count is normal. Every other
case becomes one canonical `minutes` discrepancy. Point mismatches and null
point groups become canonical `points` discrepancies. Sort keys before return.

- [ ] **Step 7: Implement exact ledger equality semantics**

Validate approved fields/types/notes first. Compare `observed` and `approved`
section-by-section:

```python
missing = sorted(observed_keys - approved_keys)
stale = sorted(approved_keys - observed_keys)
drift = sorted(
    key for key in observed_keys & approved_keys
    if _without_note(observed[key]) != _without_note(approved[key])
)
```

Reject any non-empty set. Notes are human evidence and must be non-empty in the
approved ledger, but observed candidates omit notes; comparison ignores only
the `note` field.

- [ ] **Step 8: Run focused tests and commit**

```bash
.venv/bin/python -m pytest tests/test_player_games_build.py -q
git add src/nba_data/player_games_build.py tests/test_player_games_build.py
git commit -m "feat: validate player game coverage"
```

---

### Task 5: Publish a deterministic manifest and add the offline audit command

**Files:**
- Modify: `src/nba_data/player_games_build.py`
- Modify: `tests/test_player_games_build.py`
- Create: `scripts/audit_player_logs.py`
- Modify: `src/nba_data/__main__.py:1-107`
- Modify: `tests/test_cli.py:330-end`

**Interfaces:**
- Consumes: Tasks 2–4 complete cache, partitions, validated coverage, discrepancies, and ledger.
- Produces: `write_source_review`, `build_from_roots`, `build_tree`, `validate_publication`, `render_final_audit`, and `run`; working offline `build-player-games`; source-review command.

- [ ] **Step 1: Write failing deterministic manifest tests**

Build a two-season test tree twice. Assert `_manifest.json` contains exactly:

```python
{
    "schema_version",
    "scope",
    "schema",
    "versions",
    "sources",
    "references",
    "exception_ledger",
    "partitions",
    "validation",
}
```

Assert no serialized `/home/`, temporary path, `fetched_at`, NaN, or unsorted
season/type list. Each source entry binds relative body and sidecar paths,
SHA-256, and byte length. `references` binds the exact bytes of
`games.parquet`, `team_games.parquet`, `build_manifest.json`,
`playoff_games.parquet`, and `playoff_games_manifest.json`; the manifest also
binds the packaged franchise reference and exception ledger. Each partition
binds path, byte SHA/length, logical row hash, row/player counts, and game counts
by type. The validation section binds coverage counts and exact point/minute
exception counts.

- [ ] **Step 2: Write failing publication and independent-validator tests**

Cover successful first publish, replacement, rollback after second rename
failure, retained rollback cleanup failure, stage reread, and refusal of
symlink/special/unexpected entries. `validate_publication` must catch a changed
Parquet byte, schema, row, manifest value, ledger byte, raw body, raw sidecar,
source metadata identity, franchise-reference byte, committed reference
publication byte/manifest, missing/extra partition, and stored manifest with a
self-consistent lie.

- [ ] **Step 3: Write failing audit-output tests**

`write_source_review` must create exactly two regular files in an absent/empty
output directory:

```text
source-audit.md
player_game_validation_exceptions.json
```

The Markdown includes scope, cache count, rows/games/players by season/type,
coverage status, point/minute candidate keys, and the no-absence warning. The
candidate JSON is the canonical output of `collect_discrepancies` with a
deterministic non-empty note stating the compared source/reference values and
that raw source semantics are retained. Human approval turns that candidate
note into reviewed evidence; the script never claims the mismatch is correct.
Refuse a symlink, non-empty output directory, or special file.

- [ ] **Step 4: Run and verify failures**

```bash
.venv/bin/python -m pytest tests/test_player_games_build.py tests/test_cli.py -q
```

Expected: manifest/publication/audit/build-dispatch tests fail until the new
functions exist.

- [ ] **Step 5: Implement manifest recomputation and transactional publication**

Use one shared recomputation path for build and validation. Hash Parquet bytes
with SHA-256 and hash logical rows using a canonical, type-tagged serialization
so a different Parquet encoding cannot hide logical drift. Write JSON with:

```python
json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False) + "\n"
```

Build under a `tempfile.mkdtemp` sibling of
`data/parquet-large/player_games`. `build_from_roots` takes the explicit raw and
committed-reference roots; `build_tree` is the `data_dir` wrapper used by the
CLI. Reread and independently recompute before
calling:

```python
publication.publish_tree(
    stage_dir,
    data_dir / "parquet-large/player_games",
    label="player-games publication",
)
```

Retain and report a rollback path returned because old-tree cleanup failed.

- [ ] **Step 6: Implement the source-review script**

Use `argparse` with required `--data-dir` and `--output-dir`, plus optional
`--final-report`, `--manifest-sha256`, `--snapshot-name`, and
`--restore-verified`. Normal source review calls `audit_source` and
`write_source_review`. Final rendering is allowed only when all four final
arguments are present and `validate_publication` succeeds against
`data_dir / "parquet"` as the committed reference directory.

The script must have no network import side effect and must exit nonzero on an
incomplete cache, coverage failure, unsafe output path, or final manifest/hash
disagreement.

- [ ] **Step 7: Replace temporary CLI build dispatch and prove it is offline**

Import `player_games_build` and dispatch:

```python
commands.add_parser("build-player-games")

# In the dispatch chain:
elif args.command == "build-player-games":
    player_games_build.run(data)
```

In `tests/test_cli.py`, monkeypatch every known fetch entry point and
`requests.Session.get` to raise. Assert build calls only
`player_games_build.run(tmp_path)` and rejects `--season`.

- [ ] **Step 8: Run focused and full pre-acquisition tests**

```bash
.venv/bin/python -m pytest tests/test_player_logs.py tests/test_player_games_build.py tests/test_cli.py -q
.venv/bin/python -m pytest -q
git diff --check
```

Expected: the full suite passes without network access.

- [ ] **Step 9: Commit the offline build slice**

```bash
git add src/nba_data/player_games_build.py scripts/audit_player_logs.py src/nba_data/__main__.py tests/test_player_games_build.py tests/test_cli.py
git commit -m "feat: publish player game workloads"
```

---

### Task 6: Acquire all 93 source pairs and stop for exception review

**Files:**
- Create: `src/nba_data/reference/player_game_matchup_exceptions.json`
- Modify: `src/nba_data/player_logs.py`
- Modify: `src/nba_data/player_games_build.py`
- Modify: `tests/test_player_logs.py`
- Modify: `tests/test_player_games_build.py`
- Generate, never commit: `data/raw/stats_nba/player_logs/**`
- Generate, never commit: `/tmp/player-game-source-review/**`

**Interfaces:**
- Consumes: Tasks 1–5, network access to stats.nba.com, committed game publications.
- Produces: the approved exact matchup-exception implementation and committed ledger, one complete attested raw inventory, plus deterministic point/minute review artifacts.

**Approved 2026-08-03 amendment:** The first source-audit attempt found exactly
36 malformed rows, all listed in
`.superpowers/sdd/2026-08-02-player-games-workload/task-6-matchup-diagnostic.md`.
The user approved that diagnostic's Option A recommendation. Implement the
exact allowlist below before rerunning Step 4. This approval applies only to
the matchup ledger; the Step 6 point/minute review boundary remains mandatory.

- [ ] **Step 1: Confirm the execution environment and disk headroom**

```bash
git status --short --branch
.venv/bin/python -m pytest -q
.venv/bin/python -c "from pathlib import Path; from nba_data import player_logs; print(player_logs.available_bytes(Path('data/raw/stats_nba/player_logs'))); assert player_logs.available_bytes(Path('data/raw/stats_nba/player_logs')) >= player_logs.FETCH_HEADROOM_BYTES"
```

Expected: clean implementation branch, green tests, and at least
5,368,709,120 available bytes. If the source host is DNS-blocked or returns an
access block, stop and ask the user to fix the root cause; do not add a proxy or
alternate dump.

- [ ] **Step 2: Run the complete resumable fetch**

```bash
.venv/bin/python -m nba_data fetch-player-logs
```

Expected: exactly 93 body/sidecar pairs; request starts remain serial and at
least 1.5 seconds apart. An interrupted run is resumed with the same command and
does not request any valid existing pair.

- [ ] **Step 3: Validate the complete inventory offline**

```bash
.venv/bin/python -c "from pathlib import Path; from nba_data import player_logs; paths=player_logs.validate_cache_inventory(Path('data')); assert len(paths)==93; print(len(paths))"
```

Expected: prints `93`.

- [ ] **Step 3a: Write failing behavioral tests for exact matchup approvals**

In `tests/test_player_logs.py`, add source-shaped, hand-checked fixtures that
name and catch these breaks:

- one exact approved duplicated-own-team row is normalized with its literal
  `matchup_raw` unchanged while `is_home` and `opponent_franchise_id` come from
  the approved ordered pair, and its exact entry is returned as evidence;
- an unlisted malformed row, stale approval, changed raw field, changed
  expected pair, unsupported syntax, own team on neither side, missing or
  conflicting valid-row consensus, valid rows from only one team, multiple
  malformed rows in one game, and malformed rows from both teams each fail;
- duplicate JSON member names, non-canonical top-level/entry fields, type
  drift, key/value disagreement, and an empty note each fail parsing.

Run the narrow tests and observe that the first approved case fails at the
current XOR check before adding implementation code.

- [ ] **Step 3b: Commit the exact reviewed ledger and two-pass proof logic**

Create canonical sorted JSON with exactly `schema_version` and `exceptions` at
the top level. Keys are exactly
`<ending-season>|<game-type>|<10-digit-game-id>|<person-id>`. Each value has
exactly these fields and types:

```text
season: int
game_type: str
game_id: str
person_id: int
game_date: YYYY-MM-DD str
nba_team_id: int
team_name_raw: str
team_abbreviation_raw: str
matchup_raw: str
expected_home_abbreviation: str
expected_away_abbreviation: str
note: non-empty str
```

Transcribe the exact 36 rows from the diagnostic's `Exact affected rows`
table. Derive expected ordered pairs positionally from `Consensus matchup`;
do not derive them from the malformed row. Use this exact note for every row:

```text
Reviewed source anomaly; same-game valid rows and committed references agree on the expected ordered matchup.
```

Implement `parse_matchup_exceptions`, `load_matchup_exceptions`, and
`normalize_payload_with_matchup_evidence`. `normalize_payload` remains
backward-compatible and delegates with the packaged ledger when the optional
mapping is omitted. Parsing rejects duplicate JSON keys and all schema/type/key
drift. For each season/type payload, observed malformed keys must equal the
approved subset bidirectionally.

For an approved row, require exact raw-field equality and exactly one source
row for the key. Its game must have exactly two raw team identities and two
resolved franchises; exactly one malformed row; valid companion rows from
both teams; one unanimous ordered home/away pair; exactly one valid identity
per side; one date; and the malformed identity must also occur among valid
same-team rows. The approved expected pair must equal that live consensus, and
the raw separator must imply the same side for the malformed team. Preserve
`matchup_raw`; substitute only the approved pair used to derive `is_home` and
the existing opponent mapping. Reject every unapproved shape rather than
generalizing consensus repair.

- [ ] **Step 3c: Bind matchup decisions into audit, manifest, and validation**

Pass one parsed mapping from a stable captured ledger payload through every
season assembly call so the semantics used for normalization are the exact
bytes bound into the manifest. Do not rely on a second implicit file read.
Aggregate applied evidence bidirectionally across all 93 payloads.

Keep the manifest's existing nine top-level keys. Change `exception_ledger`
to contain exactly `points_minutes` and `matchups`, each with path, SHA-256,
and byte length. In `validation`, retain existing point/minute counts and add:

```json
"matchup_repairs": {
  "count": 36,
  "sha256": "<SHA-256 of canonical sorted JSON bytes for the exact applied entry mapping>"
}
```

`write_source_review` must enumerate the 36 matchup repairs separately from
the point/minute candidates with key, raw matchup, expected home/away pair,
and the consensus proof fields. Independent publication validation must reread
stable bytes, recompute the exact applied set and digest, and reject ledger,
count, digest, raw-value, expected-pair, or repair-set drift.

Add behavior tests in `tests/test_player_games_build.py` for the two ledger
bindings, exact applied count/digest, deterministic audit enumeration, and
independent validation failures after mutating either the ledger binding or
the repair evidence. Expected literals must be hand-derived rather than
computed through the code under test.

- [ ] **Step 3d: Verify and commit the approved matchup amendment**

```bash
.venv/bin/python -m pytest tests/test_player_logs.py tests/test_player_games_build.py -q
.venv/bin/python -m pytest -q
git diff --check
git add src/nba_data/player_logs.py src/nba_data/player_games_build.py \
  src/nba_data/reference/player_game_matchup_exceptions.json \
  tests/test_player_logs.py tests/test_player_games_build.py
git commit -m "fix: review player matchup source anomalies"
```

Expected: all tests pass and the implementation commit contains no raw cache,
large Parquet publication, or point/minute candidate ledger change.

- [ ] **Step 4: Generate deterministic source-review artifacts**

Ensure the output target is absent, then run:

```bash
.venv/bin/python scripts/audit_player_logs.py \
  --data-dir data \
  --output-dir /tmp/player-game-source-review
```

Expected: `source-audit.md` and
`player_game_validation_exceptions.json` are created. The audit reports exact
regular and postseason coverage and separately enumerates all 36 approved
matchup repairs with their raw/expected values and consensus evidence. Based
on the verified 2026-08-02 probe, the
candidate is expected to include point discrepancies in ending season 1984 and
a minutes entry for `0028300095|LAC`; source revisions may change values, which
is why this is a review gate rather than a hard-coded ledger.

- [ ] **Step 5: Verify the review artifacts are deterministic**

```bash
test ! -e /tmp/player-game-source-review-first
mv /tmp/player-game-source-review /tmp/player-game-source-review-first
.venv/bin/python scripts/audit_player_logs.py \
  --data-dir data \
  --output-dir /tmp/player-game-source-review
cmp -s /tmp/player-game-source-review-first/source-audit.md \
  /tmp/player-game-source-review/source-audit.md
cmp -s /tmp/player-game-source-review-first/player_game_validation_exceptions.json \
  /tmp/player-game-source-review/player_game_validation_exceptions.json
sha256sum /tmp/player-game-source-review/source-audit.md \
  /tmp/player-game-source-review/player_game_validation_exceptions.json
```

Expected: both `cmp` commands exit 0 and the two final digests are reported.

- [ ] **Step 6: Stop and request explicit user review**

Provide the user:

- both review-artifact paths and SHA-256 values;
- counts of point and minute candidates;
- the exact keys and values;
- confirmation that coverage itself passed; and
- the source/raw values needed to assess every non-empty note.

Do not modify `player_game_validation_exceptions.json`, widen
`MINUTE_TOLERANCE`, publish Parquet, or start Task 7 until the user explicitly
approves the candidate ledger and its non-empty evidence notes.
Task 6 creates only the approved matchup-amendment commit before the source
review stop. It does not commit point/minute candidates or generated data.

---

### Task 7: Land the reviewed ledger, prove reproducibility, and publish

**Files:**
- Modify after explicit review: `src/nba_data/reference/player_game_validation_exceptions.json`
- Modify: `tests/test_player_games_build.py`
- Generate, never commit: `data/parquet-large/player_games/**`

**Interfaces:**
- Consumes: Task 6 user-approved candidate JSON and complete cache.
- Produces: exact committed ledger, green full build, 43 byte-stable partitions, deterministic manifest.

- [ ] **Step 1: Copy only the approved candidate ledger**

After the user marks the exact reviewed artifact approved, copy that artifact
to the committed path:

```bash
cp /tmp/player-game-source-review/player_game_validation_exceptions.json \
  src/nba_data/reference/player_game_validation_exceptions.json
```

Require every non-empty entry to retain the user-approved non-empty evidence
note. Do not edit source totals, published totals, inferred overtime, reason,
season, game ID, or franchise ID while copying.

- [ ] **Step 2: Add a regression assertion for every approved key**

In `tests/test_player_games_build.py`, load the reviewed JSON and assert each
entry passes only for its exact observed values and fails after changing each
numeric/null field one at a time. This makes a later source or ledger edit fail
with a concrete key rather than silently broadening the exception.

- [ ] **Step 3: Run ledger and full tests**

```bash
.venv/bin/python -m pytest tests/test_player_games_build.py -q
.venv/bin/python -m pytest -q
git diff --check
```

Expected: all tests pass.

- [ ] **Step 4: Build and capture the first complete publication inventory**

```bash
.venv/bin/python -m nba_data build-player-games
find data/parquet-large/player_games -type f -print0 \
  | LC_ALL=C sort -z \
  | xargs -0 sha256sum \
  > /tmp/player-games-first-build.sha256
```

Expected: exactly 44 files (43 Parquet partitions plus `_manifest.json`) and
independent validation succeeds before the command exits.

- [ ] **Step 5: Rebuild from the same inputs and prove byte equality**

```bash
.venv/bin/python -m nba_data build-player-games
find data/parquet-large/player_games -type f -print0 \
  | LC_ALL=C sort -z \
  | xargs -0 sha256sum \
  > /tmp/player-games-second-build.sha256
cmp -s /tmp/player-games-first-build.sha256 /tmp/player-games-second-build.sha256
```

Expected: `cmp` exits 0.

- [ ] **Step 6: Run exact publication acceptance checks**

```bash
.venv/bin/python -c "import json; from pathlib import Path; import pyarrow.dataset as ds; from nba_data import player_games_build; root=Path('data/parquet-large/player_games'); report=player_games_build.validate_publication(Path('data/raw/stats_nba/player_logs'), root, Path('data/parquet')); manifest=json.loads((root/'_manifest.json').read_text()); table=ds.dataset(root, format='parquet', partitioning='hive', exclude_invalid_files=True).to_table(); assert table.num_rows==manifest['validation']['row_count']; assert len(manifest['partitions'])==43; assert set(table.column('season').to_pylist())==set(range(1984,2027)); print({'rows':table.num_rows,'partitions':len(manifest['partitions']),'report':report['status']})"
```

Expected: 43 partitions, exact seasons 1984–2026, and `status == "ok"`.

- [ ] **Step 7: Commit only the reviewed tracked evidence**

```bash
git add src/nba_data/reference/player_game_validation_exceptions.json tests/test_player_games_build.py
git commit -m "data: record reviewed player game exceptions"
```

Do not add anything under `data/raw/` or `data/parquet-large/`.

---

### Task 8: Add immutable NAS archive and exact restore verification

**Files:**
- Create: `src/nba_data/player_games_snapshot.py`
- Create: `scripts/archive_player_games_snapshot.sh`
- Create: `scripts/restore_player_games_snapshot.sh`
- Create: `tests/test_player_games_snapshot.py`

**Interfaces:**
- Consumes: Task 7 complete raw root, publication root, and manifest SHA-256.
- Produces: `python -m nba_data.player_games_snapshot validate-layout` and `verify-rebuild`; tested archive/restore inverse.

- [ ] **Step 1: Write failing snapshot layout and tamper tests**

Seed a tiny valid raw/publication fixture with real checksums and manifest
bindings. Test validator success plus missing/extra body or sidecar, bad metadata
identity, changed body, changed Parquet, changed manifest, symlink/FIFO, wrong
top-level layout, wrong manifest pin, and a snapshot `SHA256SUMS` mismatch.

- [ ] **Step 2: Write failing archive/restore subprocess tests**

Run both scripts with temporary `NBA_PLAYER_GAMES_ARCHIVE_ROOT` and
`NBA_PLAYER_GAMES_DATA_DIR`. Cover:

- archive success and idempotent success only for a byte-identical final name;
- refusal to overwrite a different existing snapshot;
- stale `.player-games-archive.*` stage refusal;
- insufficient capacity before copy;
- archive source mutation during copy;
- INT/TERM leaving no final snapshot;
- restore success only when raw and publication targets are both absent;
- refusal when either target exists or is a symlink;
- snapshot mutation during restore;
- independent rebuilt-publication equality before final placement; and
- cleanup of temporary stages without deleting a pre-existing path.

- [ ] **Step 3: Run tests and verify failures**

```bash
.venv/bin/python -m pytest tests/test_player_games_snapshot.py -v
```

Expected: failures name the missing module and scripts.

- [ ] **Step 4: Implement the offline snapshot validator**

`validate-layout` accepts `--raw-root`, `--publication-root`, required
`--reference-dir`, and optional `--manifest-sha256`. It invokes
`player_logs.validate_cache_inventory_root(raw_root)` and
`player_games_build.validate_publication(raw_root, publication_root,
reference_dir)`. The reference directory is the checkout's committed
`data/parquet/` directory and is not copied into the large-data snapshot.
It walks with `lstat`, requires regular files/directories only, and never trusts
the snapshot's stored manifest without recomputation.

`verify-rebuild` calls `build_from_roots(raw_root, reference_dir, stage_dir)` in
a temporary sibling, compares the complete sorted file SHA inventory with the
snapshot publication, then removes only its own temporary stage.

- [ ] **Step 5: Implement archive orchestration**

The Bash script uses `set -euo pipefail`, `LC_ALL=C`, `umask 077`, separate
EXIT versus INT/TERM traps, exact layout checks, and same-filesystem `mktemp`.
It validates source semantics before copy, rejects stale stages, requires:

```text
available_bytes >= allocated_source_bytes + archive_filesystem_total_bytes / 5
```

It copies with `rsync -a --no-times --checksum`, validates the stage, writes a
complete sorted `SHA256SUMS`, revalidates the live source to detect copy-time
change, and atomically renames to:

```text
player-games-archive-2026-<manifest-sha12>
```

An existing final name is never modified; return success only after proving it
byte-identical to the new stage.

- [ ] **Step 6: Implement absent-target restore orchestration**

The restore script pins the expected manifest SHA from the snapshot name and
manifest, validates `SHA256SUMS`, validates the snapshot before copy, rebuilds
the publication independently from restored raw data, and requires byte
identity. It stages under each destination parent and renames only after all
checks pass. If either final target exists, fail before copying. Revalidate the
snapshot after copy to catch mutation. Both scripts pass
`NBA_PLAYER_GAMES_REFERENCE_DIR`, defaulting to the checkout's `data/parquet/`,
to every semantic validation call.

- [ ] **Step 7: Run focused tests and shell syntax checks**

```bash
bash -n scripts/archive_player_games_snapshot.sh
bash -n scripts/restore_player_games_snapshot.sh
.venv/bin/python -m pytest tests/test_player_games_snapshot.py -q
```

Expected: all pass.

- [ ] **Step 8: Archive the complete release and verify a disposable restore**

```bash
scripts/archive_player_games_snapshot.sh
```

Capture the printed immutable snapshot name and manifest SHA. Restore into
absent temporary destinations by setting `NBA_PLAYER_GAMES_DATA_DIR` to a
fresh directory and running:

```bash
scripts/restore_player_games_snapshot.sh
```

Run `player_games_build.validate_publication` on the restored publication and
compare the complete sorted SHA inventories with the originals. Expected:
byte-identical raw pairs, partitions, and manifest.

- [ ] **Step 9: Commit recovery code and tests**

```bash
git add src/nba_data/player_games_snapshot.py scripts/archive_player_games_snapshot.sh scripts/restore_player_games_snapshot.sh tests/test_player_games_snapshot.py
git commit -m "feat: archive player game workloads"
```

---

### Task 9: Document and verify the completed D1 release

**Files:**
- Modify: `README.md:32-170`
- Modify: `docs/data-dictionary.md:50-98,597-end`
- Modify: `tests/test_repository_layout.py:120-150,1080-end`
- Modify: `scripts/verify_repository.py:1-80`
- Modify: `tests/test_verify_repository.py`
- Create: `docs/audits/2026-08-02-player-games-source-audit.md`

**Interfaces:**
- Consumes: final manifest SHA, snapshot name, exact counts, reviewed exception keys, and verified restore from Tasks 7–8.
- Produces: user-facing commands/contracts, deterministic final audit, and repository gates that prevent documentation or tracking regression.

- [ ] **Step 1: Write failing documentation/layout tests**

Require README and data dictionary to contain:

- `player_games` schema columns in exact `PLAYER_GAME_COLUMNS` order;
- ending seasons 1984–2026 and exact game-type applicability;
- `fetch-player-logs`, `build-player-games`, audit, archive, and restore commands;
- cache and partition layouts;
- regular and postseason cross-source keys;
- points/minutes exception semantics and reviewed-ledger path;
- matchup-exception semantics, reviewed-ledger path, exact repair count and
  digest, and the rule that future malformed rows fail for new human review;
- the no-absence/no-roster warning;
- final audit link and immutable snapshot name; and
- explicit no-commit policy for `data/raw/` and `data/parquet-large/`.

Require the final audit file and both snapshot scripts in repository
verification. Assert no tracked file begins with either generated prefix.

- [ ] **Step 2: Run and verify documentation tests fail**

```bash
.venv/bin/python -m pytest tests/test_repository_layout.py tests/test_verify_repository.py -q
```

Expected: failures identify missing D1 documentation/audit requirements.

- [ ] **Step 3: Update README and data dictionary from the implemented contract**

Document exact physical types/nullability, ordering, primary key, source/cache
identity, coverage joins, exception behavior, manifest fields, commands,
snapshot variables/layout, and restore refusal rules. State plainly:

> A missing `player_games` row means only that LeagueGameLog did not credit an
> appearance; it does not prove roster membership, eligibility, injury, rest,
> or expected participation.

- [ ] **Step 4: Render the final audit from verified artifacts**

Derive the exact values printed by Task 8, then run the audit command:

```bash
player_manifest_sha256="$(sha256sum data/parquet-large/player_games/_manifest.json | cut -d' ' -f1)"
player_snapshot_name="player-games-archive-2026-${player_manifest_sha256:0:12}"
.venv/bin/python scripts/audit_player_logs.py \
  --data-dir data \
  --output-dir /tmp/player-game-final-audit-input \
  --final-report docs/audits/2026-08-02-player-games-source-audit.md \
  --manifest-sha256 "$player_manifest_sha256" \
  --snapshot-name "$player_snapshot_name" \
  --restore-verified
```

The script rejects a mismatch between the full digest and the snapshot prefix.

- [ ] **Step 5: Run the complete acceptance gate**

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/verify_repository.py
git diff --check
git status --short --branch
git ls-files data/raw data/parquet-large
```

Expected:

- full pytest suite passes;
- repository verification passes;
- `git diff --check` is silent;
- `git ls-files data/raw data/parquet-large` prints nothing; and
- status contains only intentional tracked implementation/docs changes on the
  unmerged implementation branch.

- [ ] **Step 6: Inspect the final diff against the design**

Check every success criterion in
`docs/superpowers/specs/2026-08-02-player-games-workload-design.md` against a
test, command output, manifest field, or final-audit section. Confirm D2/D3 code
did not enter the branch and no absence inference exists.

- [ ] **Step 7: Commit the final documentation and verification gates**

```bash
git add README.md docs/data-dictionary.md docs/audits/2026-08-02-player-games-source-audit.md tests/test_repository_layout.py scripts/verify_repository.py tests/test_verify_repository.py
git commit -m "docs: record player game workload release"
```

- [ ] **Step 8: Stop at the review boundary**

Report commit list, tests, exact manifest SHA, snapshot path, exception counts,
row/game/player counts, restore proof, and `git status`. Do not merge, push,
deploy, remove the worktree, or begin D2/D3.

---

## Plan acceptance mapping

| Design requirement | Implemented by |
|---|---|
| 93 immutable source identities and source-shaped normalization | Tasks 1–2 |
| Exact Arrow schema and 43 deterministic partitions | Task 3 |
| Regular exact-ID and postseason composite-key coverage | Task 4 |
| Exact points/minutes gates and stale-ledger rejection | Task 4 |
| Human-reviewed matchup and point/minute exception checkpoints | Tasks 6–7 |
| Deterministic manifest, independent validation, transactional publication | Task 5 |
| Two byte-identical full builds | Task 7 |
| Immutable NAS snapshot and verified absent-target restore | Task 8 |
| No-absence limitation, commands, data dictionary, and final audit | Task 9 |
| No committed raw/large data and fresh-clone policy | Tasks 2, 8, and 9 |
