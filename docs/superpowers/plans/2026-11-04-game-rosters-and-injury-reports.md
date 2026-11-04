# Game Rosters & Official Injury Reports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish attested game rosters and scheduled starts for every 2012–2026 game in the trusted `player_games` publication, plus every version of the NBA's official injury report (2018-12-17 through 2026-09-30) parsed from the PDFs into resolved listing rows, as one deterministic, independently validated, NAS-archived publication.

**Architecture:** Part A (`box_rosters`) acquires two `stats.nba.com` V3 box-score bodies per game under the existing attested-cache contract and normalizes them into `game_rosters` and `game_schedule` rows. Part B (`injury_reports`, `injury_report_parse`) enumerates the two file-name slot grids, archives every retrievable PDF with control-checked, immutable monthly inventory generations, and parses each report with one header-driven parser. `availability_resolve` resolves listings to games, teams and persons; `availability_build` assembles four Parquet tables per season, enforces the gates and the exact exception ledger, measures agreement with the pinned sportsdataverse derivative, writes the manifest, validates independently and publishes transactionally; `availability_snapshot` plus two shell scripts archive and restore the raw evidence and the publication as one immutable NAS unit. A dependency-only rebuild of the existing reference manifests and the `player_games` publication precedes all of it because the repository binds direct requirements into committed manifests.

**Tech Stack:** Python 3.12, pandas, PyArrow/Parquet (Zstandard), `nba_api`, requests, `pdfplumber` 0.11.10 (new; `pdfminer.six`, `pypdfium2`), `zoneinfo`, pytest, Bash, rsync, SHA-256, Git.

**Spec:** `docs/superpowers/specs/2026-11-04-game-rosters-and-injury-reports-design.md` (approved 2026-11-04 after two external review rounds). The umbrella roadmap is `docs/superpowers/specs/2026-07-31-player-availability-foundation-design.md` (this plan completes its D2 row for completed seasons).

## Global Constraints

- At execution time, create or enter an isolated worktree with `superpowers:using-git-worktrees` on a branch named `availability-foundation`. Do not implement on `master`.
- Approval of this plan is not permission to merge or push. Finish with committed changes on the unmerged branch. Do not push, merge, deploy or remove the worktree without explicit direction.
- Every commit message ends with the trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- The only new direct dependency is `pdfplumber`, pinned exactly as the spec resolved it on 2026-11-04: `pdfplumber==0.11.10`, `pdfminer-six==20260107`, `pypdfium2==5.14.0`, `cryptography==50.0.2`. Every other pin in `requirements.txt` stays unchanged; the README's `--upgrade` regeneration command is not used for this change.
- Phase 0 (Tasks 1–3) must land before any Part A or Part B code is committed, because `tests/test_repository_layout.py::test_build_manifests_dependencies_match_direct_requirements` fails the moment `pdfplumber` enters `requirements.in`.
- Roster and schedule scope: exactly the 19,183 games of the committed `player_games` publication (17,888 regular, 1,258 playoff, 37 play-in; ending seasons 2012–2026). Two requests per game: 38,366 identities. A game absent from `player_games` is out of scope.
- Injury-report scope: slot dates 2018-12-17 through 2026-09-30; `hour` grid 2018-12-17 → 2025-12-31 (24 slots per day), `quarter` grid 2025-12-01 → 2026-09-30 (96 slots per day); 90,912 enumerated slots; ending seasons 2019–2026 by the version-season rule; no slot after 2026-09-30 and no 2026-27 game.
- `stats.nba.com` transport: `stats_nba.preflight_dns()` first; serial requests ≥ 1.5 s apart; five attempts with 5/10/20/40 s delays for request exceptions, HTTP 429 and 5xx; numeric `Retry-After` honoured only through 120 s; no retry on other 4xx, schema-invalid success or a block page; complete valid pairs are never re-fetched or replaced; a damaged pair is a hard failure.
- PDF host transport: serial GETs ≥ 0.5 s apart, fixed descriptive `User-Agent`, 60 s timeout; HTTP 403 with an S3 `AccessDenied` XML body (≤ 1,024 bytes) is recorded as `unavailable` with verbatim evidence, never as absence; three committed control files are checked at run start, after every 200 probes and as a closing control; outcomes are persisted only as immutable inventory generations written after a successful control; every `unavailable` slot must be rechecked on a later calendar day before the build accepts it.
- Raw bodies and sidecars live only under gitignored `data/raw/stats_nba/box_rosters/`, `data/raw/nba_injury_reports/`, `data/raw/sportsdataverse/nba_stats_injury_report/` and `data/raw/nba_gamebooks/`; large outputs only under gitignored `data/parquet-large/availability/`. Nothing under those roots is ever committed.
- `fetch-box-rosters`, `fetch-injury-reports`, `fetch-injury-derivative` are the only network paths. Parsing, resolution, building, validation, tests, audit, archive and restore are offline. Unit tests never touch the network.
- Timestamps: `scheduled_start_utc` is the league's scheduled start, never an actual tip-off; `report_time_printed_utc` is the printed title time localised with `America/New_York`, null when ambiguous (repeated November 1 AM hour) or unparsed, and `report_time_printed_utc_ambiguous` marks the ambiguous case; `report_slot_et` is acquisition metadata.
- Reasons, names, team strings, matchups, game times and comments are published verbatim. No episode, taxonomy, final-pre-tip status, star tier or trend is computed.
- Gates fail closed; only exact entries in `src/nba_data/reference/availability_exceptions.json` (four sections) may accept a disagreement, exclude a report or accept a title outside its slot window. The ledger starts with four empty sections and changes only at the Task 20 review checkpoint.
- Person resolution gate: per season ≥ 98 % of non-placeholder listing rows for resolved games **and** ≥ 98 % of distinct listed identities `(game_id, nba_team_id, normalised name)` resolve to a `person_id`.
- Publication: exactly 46 Zstandard Parquet partitions (`game_rosters`, `game_schedule` for 2012–2026; `injury_report_versions`, `injury_report_listings` for 2019–2026) plus `_manifest.json`; two builds from the same raw trees and ledger are byte-identical; the manifest is sorted JSON without timestamps or absolute paths.
- Snapshot root `/mnt/nas/llm/documents/nba-predictions-archive/availability-archive-2026-<first-12-hex-of-manifest-sha256>/`, overridable only through `NBA_AVAILABILITY_ARCHIVE_ROOT` and `NBA_AVAILABILITY_DATA_DIR`; recovery requires the D1 snapshot named in the manifest to be restored first.
- The repository's test suite must stay green at every commit; the archive tests need about 42 GB free on `/` (currently 50 GB), so run the full suite with that in mind and never stage snapshots on `/`.

## Review Focus

Five input classes the spec implies but a naive implementation would mishandle; each line names the task whose tests pin it.

1. A box-score player with a non-empty `comment` who nevertheless has a credited `player_games` appearance (or an empty comment with no appearance) must surface as an `appearance_consistency` disagreement that fails the build closed, never be silently classified by whichever rule runs first — Task 6.
2. A report tricode that never matches any box-score tricode (a league alias such as `PHO`/`PHX` or `UTH`/`UTA`) would quietly turn a whole team's season into `no_box_score_game`; the resolution metrics must report unresolved matchups grouped by tricode pair so a systematic alias is visible in one line — Task 12.
3. Names with suffixes, diacritics, hyphens, apostrophes and the report's `Family, Given` order (`Porter Jr., Michael` against roster `Porter Jr.` / `Michael`; `Dončić, Luka` against `Doncic`) must resolve, and two roster players sharing a normalised name must yield `unresolved_ambiguous`, never a silent first match — Task 12.
4. December 2025 is enumerated by both grids; `Injury-Report_2025-12-22_05PM` and `Injury-Report_2025-12-22_05_00PM` are distinct slots that are both probed, and no `report_id` repeats across the 90,912 slots — Task 7.
5. A run interrupted after a PDF pair was published but before the next inventory generation was written must, on resume, treat that slot as present from the PDF on disk, re-probe only the lost `unavailable` outcomes, and merge the previous generation's state into the next one — Task 8.

---

## File Structure

### Create

`src/nba_data/box_rosters.py`
    Part A: `player_games` worklist and index, box-score endpoint identities, cache paths and sidecar identities, raw validators, paced/retried fetch, normalization of one game into roster rows and a schedule row, appearance and schedule disagreement detection, roster counts.

`src/nba_data/gamebooks.py`
    Import of browser-downloaded official gamebook PDFs with sidecars, name extraction from the box-score page, roster comparison records for the sample. (The spec lists this under `box_rosters`; it is split here for file focus, nothing else changes.)

`src/nba_data/injury_reports.py`
    Part B acquisition: slot grids and file names, title parsing, slot-window rule, Eastern-to-UTC conversion with the ambiguity flag, PDF inspection at fetch, PDF paths and sidecars, immutable monthly inventory generations, control checks, batched fetch, recheck pass.

`src/nba_data/injury_report_parse.py`
    Part B parsing: header detection, word-to-cell assignment, row assembly with placeholders and carry-forward, multi-line reasons, per-report gates, verbatim listing rows. (The spec lists parsing under `injury_reports`; split for file focus.)

`src/nba_data/injury_derivative.py`
    Pin of the eight sportsdataverse release assets and their normalisation into comparison rows with placeholder `kind`.

`src/nba_data/availability_resolve.py`
    Version-season rule, name normalisation, game index, game/team/person resolution, resolution metrics (row and identity rates, breakdowns, unresolved names, listed-but-not-rostered, unresolved matchups by tricode pair).

`src/nba_data/availability_build.py`
    Arrow schemas for the four tables, exception ledger loading and exact validation, Part A and Part B assembly, whole-dataset gates, derivative agreement with pattern grouping, partition writing, manifest, independent validation, transactional publication, audit rendering.

`src/nba_data/availability_snapshot.py`
    Offline `validate-layout` for the raw trees plus publication (with the `player_games` digest binding) used by both recovery scripts.

`src/nba_data/reference/availability_exceptions.json`
    The exact four-section ledger; begins empty.

`src/nba_data/reference/injury_report_controls.json`
    The three control files with their SHA-256 and byte counts.

`scripts/diff_manifest_allowlist.py`
    Compares two manifest JSON documents and fails unless every differing leaf path lies under an allowed prefix.

`scripts/audit_availability_sources.py`
    Deterministic Markdown/JSON review artifacts (roster consistency and sizes, parse and title-check status, placeholder counts, resolution metrics, listed-but-not-rostered, derivative disagreement patterns, unmatched versions, ledger candidates, gamebook sample list) to an explicit empty output directory.

`scripts/archive_availability_snapshot.sh`, `scripts/restore_availability_snapshot.sh`
    Filesystem orchestration following `scripts/archive_player_games_snapshot.sh` and `scripts/restore_player_games_snapshot.sh`; semantics delegated to `availability_snapshot`.

`tests/test_manifest_allowlist.py`, `tests/test_box_rosters.py`, `tests/test_gamebooks.py`, `tests/test_injury_reports.py`, `tests/test_injury_report_parse.py`, `tests/test_injury_derivative.py`, `tests/test_availability_resolve.py`, `tests/test_availability_build.py`, `tests/test_availability_snapshot.py`
    One test module per source module.

`tests/fixtures/box_rosters/`
    Hand-authored source-shaped traditional and summary bodies (two tiny games) plus a tiny `player_games` publication fixture builder in the test module.

`tests/fixtures/injury_reports/`
    Six complete original report PDFs, one hand-checked `<report_id>.expected.csv` per PDF, and `SOURCES.json` (URL, SHA-256, bytes, pages per file).

`docs/audits/2026-11-<dd>-availability-source-audit.md`
    The committed final audit (Task 22).

### Modify

`src/nba_data/__main__.py`
    Add `fetch-box-rosters`, `fetch-injury-reports`, `fetch-injury-derivative`, `import-gamebooks`, `build-availability`.

`src/nba_data/pbp.py`
    One public alias line: `request_bytes = _request_bytes` (Task 5 reuses the paced, retried `stats.nba.com` request helper and `RequestPacer`).

`requirements.in`, `requirements.txt`, `pyproject.toml`
    Add `pdfplumber` (Task 2).

`data/parquet/build_manifest.json`, `data/parquet/historical_games_manifest.json`, `data/parquet/playoff_games_manifest.json`
    Regenerated dependency lists and the playoff manifest's historical-manifest pin (Task 2).

`docs/audits/2026-08-02-player-games-source-audit.md`
    Dated amendment for the dependency-only rebuild (Task 3).

`tests/test_cli.py`, `tests/test_repository_layout.py`
    CLI dispatch tests; executable-script, audit-presence, documentation and tracking-policy tests.

`README.md`, `docs/data-dictionary.md`
    New sections (Task 22).

---

## Shared Contracts

The names below are used across tasks exactly as written. A task that defines one owns its tests; a task that consumes one does not redefine it.

### Part A (`box_rosters.py`)

```python
PUBLICATION_SEASONS = tuple(range(2012, 2027))
RAW_SUBDIR = "raw/stats_nba/box_rosters"
PLAYER_GAMES_SUBDIR = "parquet-large/player_games"
ENDPOINTS = {
    "traditional": "boxscoretraditionalv3",   # nba_api endpoint path
    "summary": "boxscoresummaryv3",
}
ENDPOINT_CLASSES = {
    "traditional": "BoxScoreTraditionalV3",   # sidecar `endpoint` value
    "summary": "BoxScoreSummaryV3",
}
MIN_REQUEST_INTERVAL_S = 1.5
RETRY_DELAYS_S = (5.0, 10.0, 20.0, 40.0)
MAX_RETRY_AFTER_S = 120.0
FETCH_HEADROOM_BYTES = 5_368_709_120
GAME_CODE_PATTERN = re.compile(r"^\d{8}/[A-Z]{3}[A-Z]{3}$")

@dataclass(frozen=True)
class WorklistGame:
    game_id: str; season: int; game_type: str; game_date: datetime.date

@dataclass(frozen=True)
class PlayerGamesIndex:
    manifest_binding: dict                 # {"path": "parquet-large/player_games/_manifest.json", "sha256", "bytes"}
    games: tuple[WorklistGame, ...]        # sorted by (season, game_date, game_id)
    appearances: Mapping[str, frozenset[int]]            # game_id -> person_ids credited
    team_rows: Mapping[str, Mapping[int, tuple[str, str, bool]]]  # game_id -> nba_team_id -> (franchise_id, team_abbreviation_raw, is_home)

def load_player_games_index(player_games_root: Path) -> PlayerGamesIndex
def game_keys(index: PlayerGamesIndex) -> dict[tuple[datetime.date, str, str], str]   # (game_date, away abbreviation, home abbreviation) -> game_id
def request_parameters(game_id: str, endpoint: str) -> dict[str, str]
    # traditional: {"GameID": game_id, "StartPeriod": "0", "EndPeriod": "0",
    #               "StartRange": "0", "EndRange": "0", "RangeType": "0"}
    # summary:     {"GameID": game_id}
def cache_path(data_dir: Path, season: int, game_id: str, endpoint: str) -> Path
    # data_dir / RAW_SUBDIR / f"season={season}" / f"{game_id}.{endpoint}.json"
def expected_metadata(game: WorklistGame, endpoint: str) -> dict
    # {"provider": "stats_nba", "resource": "box_rosters",
    #  "endpoint": ENDPOINT_CLASSES[endpoint], "parameters": request_parameters(...),
    #  "season", "game_type", "game_id"}
def validate_traditional_bytes(payload: bytes, *, game_id: str) -> None
def validate_summary_bytes(payload: bytes, *, game_id: str) -> None
def validate_traditional(path: Path, *, game_id: str) -> None   # path form for cache.publish_bytes
def validate_summary(path: Path, *, game_id: str) -> None
def read_cached(data_dir: Path, game: WorklistGame, endpoint: str) -> tuple[bytes, dict] | None
def validate_cache_inventory(data_dir: Path, games: Iterable[WorklistGame]) -> tuple[Path, ...]
def fetch(data_dir: Path, seasons: Iterable[int] | None = None, *, session=None, pacer=None, sleeper=time.sleep) -> None

@dataclass(frozen=True)
class NormalizedGame:
    rosters: pd.DataFrame        # game_rosters columns, spec order
    schedule: dict               # one game_schedule row
    team_strings: dict[int, str] # nba_team_id -> teamCity + " " + teamName
    tricodes: dict[int, str]     # nba_team_id -> teamTricode
    appearance_disagreements: tuple[dict, ...]   # {"season","game_id","person_id","comment_raw","minutes_raw","appearance"}
    schedule_disagreement: dict | None           # {"game_id","game_et_raw","game_code_raw","game_date"} or None
    roster_counts: dict[int, dict[str, int]]     # nba_team_id -> {"active": n, "inactive": n}

def normalize_game(traditional: bytes, summary: bytes, *, game: WorklistGame, index: PlayerGamesIndex) -> NormalizedGame
def comment_code(comment: str) -> str      # text before the first " - ", whole comment otherwise
```

Roster status: `inactive` for every summary `inactives` entry; `played` for a traditional player whose `(game_id, personId)` is in `index.appearances[game_id]`; `active_not_played` otherwise. `starter_position_raw`, `comment_raw`, `minutes_raw` are null for inactives and for empty source strings; `jersey_num` is the source string stripped of surrounding whitespace, null when empty. `franchise_id` comes from `index.team_rows[game_id][nba_team_id]`.

### Part B acquisition (`injury_reports.py`)

```python
PDF_BASE_URL = "https://ak-static.cms.nba.com/referee/injury/"
RAW_SUBDIR = "raw/nba_injury_reports"
GRIDS = {                       # grid -> (first slot date, last slot date, minutes per slot)
    "hour": (date(2018, 12, 17), date(2025, 12, 31), 60),
    "quarter": (date(2025, 12, 1), date(2026, 9, 30), 15),
}
WINDOW = (date(2018, 12, 17), date(2026, 9, 30))
USER_AGENT = "nba-predictions-revived availability acquisition"
MIN_REQUEST_INTERVAL_S = 0.5
TIMEOUT_S = 60
RETRY_DELAYS_S = (5.0, 10.0, 20.0, 40.0)
CONTROL_INTERVAL = 200
MAX_ACCESS_DENIED_BYTES = 1024
CONTROLS_PATH = Path(__file__).resolve().parent / "reference/injury_report_controls.json"
TITLE_PATTERN = re.compile(r"Injury Report:\s*(\d{2})/(\d{2})/(\d{2})\s+(\d{2}):(\d{2})\s*(AM|PM)")
EASTERN = zoneinfo.ZoneInfo("America/New_York")

@dataclass(frozen=True)
class Slot:
    grid: str; slot_date: date; slot_label: str      # "05PM", "12_00AM"
    slot_start_et: datetime                          # naive
    report_id: str                                   # "Injury-Report_2024-01-31_05PM"
    file_name: str                                   # report_id + ".pdf"
    url: str
    month: str                                       # "2024-01"

def slot_label(grid: str, hour: int, minute: int) -> str
def enumerate_slots(months: Iterable[str] | None = None) -> tuple[Slot, ...]
def slot_from_report_id(report_id: str) -> Slot
def parse_title(text: str) -> datetime | None
def title_check(slot: Slot, printed: datetime | None) -> str          # "ok" | "failed" | "unparsed"
def printed_to_utc(printed_et: datetime) -> tuple[datetime | None, bool]  # (aware UTC or None, ambiguous)

@dataclass(frozen=True)
class PdfInspection:
    opens: bool; pages: int | None; title_text: str | None; title_printed_et: datetime | None

def inspect_pdf(payload: bytes) -> PdfInspection
def pdf_path(data_dir: Path, slot: Slot) -> Path          # data_dir/RAW_SUBDIR/pdfs/<month>/<file_name>
def pdf_metadata(slot: Slot, inspection: PdfInspection) -> dict
    # {"provider": "nba_official", "resource": "injury_report", "url", "grid", "slot_date" (ISO),
    #  "slot_label", "report_id", "opens", "pages", "title_printed_et" (ISO or None), "title_check"}
def validate_pdf(path: Path) -> None                       # body begins b"%PDF"
def inventory_dir(data_dir: Path, month: str) -> Path      # data_dir/RAW_SUBDIR/inventory/<month>
def generation_number(path: Path) -> int                   # "gen-0007.json" -> 7
def read_current_inventory(data_dir: Path, month: str) -> tuple[dict | None, tuple[Path, ...]]
    # (highest verifying generation state or None, damaged generation body paths)
def write_inventory_generation(data_dir: Path, month: str, state: dict) -> Path
def load_controls(path: Path = CONTROLS_PATH) -> dict[str, dict]
def check_control(session, file_name: str, expected: dict, *, pacer) -> dict   # raises SourceAccessError
def fetch(data_dir: Path, months: Iterable[str] | None = None, *, recheck_unavailable: bool = False,
          session=None, pacer=None, sleeper=time.sleep, today: date | None = None) -> dict  # summary counts
```

Inventory generation state (one JSON object per generation, sorted keys):

```json
{
  "schema_version": 1,
  "month": "2024-01",
  "generation": 3,
  "slots": {
    "Injury-Report_2024-01-31_05PM": {
      "grid": "hour", "slot_date": "2024-01-31", "slot_label": "05PM",
      "url": "https://ak-static.cms.nba.com/referee/injury/Injury-Report_2024-01-31_05PM.pdf",
      "status": "present", "sha256": "e39f…", "bytes": 80020, "evidence": []
    },
    "Injury-Report_2024-01-31_03AM": {
      "grid": "hour", "slot_date": "2024-01-31", "slot_label": "03AM", "url": "…",
      "status": "unavailable", "sha256": null, "bytes": null,
      "evidence": [
        {"fetched_at": "2026-11-10T14:02:11Z", "http_status": 403, "date_header": "…",
         "x_amz_request_id": "…", "x_amz_id_2": "…", "body_bytes": 243,
         "body_sha256": "…", "body_text": "<?xml version=\"1.0\" …AccessDenied…"}
      ]
    }
  },
  "controls": [
    {"checked_at": "2026-11-10T14:00:00Z", "file_name": "Injury-Report_2018-12-17_05PM.pdf",
     "sha256": "1650…", "ok": true}
  ]
}
```

A present slot keeps any earlier `unavailable` evidence in `evidence` as history. Generation sidecars carry `{"provider": "nba_official", "resource": "injury_report_inventory", "month", "generation"}`.

Controls file (`src/nba_data/reference/injury_report_controls.json`):

```json
{
  "schema_version": 1,
  "controls": {
    "Injury-Report_2018-12-17_05PM.pdf": {"bytes": 33874, "sha256": "1650d479009749f8a0fe2f665a6e808484967b5b37b6b7a9860d892d99b3ed9d"},
    "Injury-Report_2024-01-31_05PM.pdf": {"bytes": 80020, "sha256": "e39fe95bff31019494b82c9ec019919e94d8f10494395bce643d2c1ba02be17c"},
    "Injury-Report_2025-12-22_05_30PM.pdf": {"bytes": 88537, "sha256": "497f46407eaebbfd216b8f9544511675a3e4416856505267de41fc1603d04d30"}
  }
}
```

### Part B parsing (`injury_report_parse.py`)

```python
REQUIRED_COLUMNS = ("Game Date", "Game Time", "Matchup", "Team", "Player Name", "Current Status", "Reason")
OPTIONAL_COLUMNS = ("Category", "Previous Status")
STATUSES = ("Available", "Probable", "Questionable", "Doubtful", "Out")
PLACEHOLDER_PHRASES = ("NOT YET SUBMITTED", "ALL PLAYERS AVAILABLE", "Non-NBA Team")
X_TOLERANCE = 1.5
GAME_DATE_PATTERN = re.compile(r"^\d{2}/\d{2}/\d{4}$")
GAME_TIME_PATTERN = re.compile(r"^\d{2}:\d{2} \(ET\)$")
MATCHUP_PATTERN = re.compile(r"^[A-Z]{2,3}@[A-Z]{2,3}$")
FOOTER_PATTERN = re.compile(r"^Page (\d+) of (\d+)$")

class ReportParseError(ValueError): ...

@dataclass(frozen=True)
class Header:
    columns: tuple[tuple[str, float], ...]   # (label, left x) in page order
    bottom: float

@dataclass(frozen=True)
class Listing:
    page: int; row_number: int
    game_date: date; game_time_raw: str; matchup_raw: str; away_tricode: str; home_tricode: str
    team_raw: str; is_placeholder: bool; placeholder_raw: str | None
    player_name_raw: str | None; current_status_raw: str | None; reason_raw: str | None
    category_raw: str | None; previous_status_raw: str | None

@dataclass(frozen=True)
class ParsedReport:
    pages: int; title_text: str | None; title_printed_et: datetime | None
    listings: tuple[Listing, ...]

def page_words(page) -> list[dict]                     # pdfplumber extract_words(x_tolerance=X_TOLERANCE)
def detect_header(words: list[dict]) -> Header         # raises ReportParseError
def column_for(header: Header, x0: float) -> str
def group_lines(words: list[dict]) -> list[list[dict]] # by `top` within 1.0 pt
def line_cells(line: list[dict], header: Header) -> dict[str, str]
def footer_numbers(words: list[dict]) -> tuple[int, int]
def parse_page(page, page_number: int) -> tuple[Header, list[tuple[float, dict[str, str]]], tuple[int, int]]
def assemble_listings(pages: list[tuple[Header, list[tuple[float, dict[str, str]]]]]) -> tuple[Listing, ...]
def parse_report(payload: bytes) -> ParsedReport        # raises ReportParseError on any gate
```

Expected-rows CSV columns, in this order, empty string for null: `page,row_number,game_date,game_time_raw,matchup_raw,away_tricode,home_tricode,team_raw,is_placeholder,placeholder_raw,player_name_raw,current_status_raw,reason_raw,category_raw,previous_status_raw`. `game_date` is ISO (`2018-12-17`); `is_placeholder` is `true`/`false`.

### Derivative (`injury_derivative.py`)

```python
REPOSITORY = "sportsdataverse/sportsdataverse-data"
RELEASE_TAG = "nba_stats_injury_report"
SEASONS = tuple(range(2019, 2027))
RAW_SUBDIR = "raw/sportsdataverse/nba_stats_injury_report"
RELEASE_API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/tags/{RELEASE_TAG}"
def asset_url(season: int) -> str   # f"https://github.com/{REPOSITORY}/releases/download/{RELEASE_TAG}/injury_report_{season}.parquet"
def asset_path(data_dir: Path, season: int) -> Path
def validate_asset(path: Path) -> None            # Parquet with exactly the 17 known columns
def fetch(data_dir: Path, seasons: Iterable[int] | None = None, *, session=None) -> None
def comparison_rows(payload: bytes) -> pd.DataFrame
```

`comparison_rows` columns: `report_datetime` (naive Eastern), `game_date`, `matchup`, `team`, `kind` (`player` or the placeholder phrase), `player_name`, `status`, `reason`, `category`, `previous_status`, `artifact` (bool: null status and null name whose `reason` is none of the three phrases, e.g. the `,` rows). For a placeholder row `player_name`, `status`, `reason` are null and `kind` holds the phrase.

### Resolution (`availability_resolve.py`)

```python
def version_season(slot_date: date) -> int
    # 2018-12-17..2019-09-30 -> 2019; 2019-10-01..2020-11-30 -> 2020; 2020-12-01..2021-09-30 -> 2021;
    # then 1 October .. 30 September -> ending season
def normalize_name(value: str) -> str
    # NFKD, strip combining marks, casefold, remove ".", "-", "'", "’" and whitespace
def listing_name_key(player_name_raw: str) -> str          # "Family, Given" -> normalize(family) + "|" + normalize(given)
def roster_name_key(family_name_raw: str, first_name_raw: str) -> str

@dataclass(frozen=True)
class GameIndex:
    game_by_key: Mapping[tuple[date, str, str], str]                 # (game_date, away, home) -> game_id
    teams: Mapping[str, tuple[int, int]]                             # game_id -> (away nba_team_id, home nba_team_id)
    team_strings: Mapping[tuple[str, int], str]                      # (game_id, nba_team_id) -> "City Name"
    tricodes: Mapping[tuple[str, int], str]
    game_roster: Mapping[tuple[str, int], Mapping[str, frozenset[int]]]      # name key -> person_ids
    season_roster: Mapping[tuple[int, int], Mapping[str, frozenset[int]]]    # (season, nba_team_id) -> name key -> person_ids
    franchise: Mapping[tuple[str, int], str]

def build_game_index(schedule: pd.DataFrame, rosters: pd.DataFrame, team_strings: Mapping, tricodes: Mapping) -> GameIndex
def resolve_game(index: GameIndex, game_date: date, away: str, home: str) -> tuple[str | None, str]
    # ("<game_id>", "resolved") or (None, "no_box_score_game"); two matches raise AvailabilityResolveError
def resolve_team(index: GameIndex, game_id: str, team_raw: str) -> int | None
def resolve_person(index: GameIndex, *, game_id: str, nba_team_id: int, season: int, player_name_raw: str) -> tuple[int | None, str]
    # ("game_roster" | "season_roster" | "unresolved_no_match" | "unresolved_ambiguous")
def resolve_listings(listings: pd.DataFrame, index: GameIndex, team_exceptions: Mapping[str, dict]) -> pd.DataFrame
def resolution_metrics(listings: pd.DataFrame) -> dict
```

`resolution_metrics` returns, per season: `rows`, `rows_resolved`, `row_rate`, `identities`, `identities_resolved`, `identity_rate`, `identity_rate_by_team`, `identity_rate_by_month`, `identity_rate_by_status`, `unresolved_names` (name → row count, identity count), `listed_not_rostered` (`rows`, `identities`, `by_team`, `by_status`, `not_out_rows` list of `(report_id, row_number, game_id, player_name_raw, status)`), `unresolved_matchups` (tricode pair → row count).

### Build (`availability_build.py`)

```python
ROSTER_SEASONS = tuple(range(2012, 2027))
REPORT_SEASONS = tuple(range(2019, 2027))
EXCEPTIONS_PATH = Path(__file__).resolve().parent / "reference/availability_exceptions.json"
PUBLICATION_SUBDIR = "parquet-large/availability"
GAME_ROSTERS_SCHEMA, GAME_SCHEDULE_SCHEMA, INJURY_REPORT_VERSIONS_SCHEMA, INJURY_REPORT_LISTINGS_SCHEMA: pa.Schema
PERSON_RESOLUTION_FLOOR = 0.98

class AvailabilityBuildError(ValueError): ...
def load_exceptions(path: Path = EXCEPTIONS_PATH) -> dict
def validate_exceptions(observed: dict, approved: dict) -> None       # exact two-way equality per section, note ignored
def build_from_roots(raw_root: Path, player_games_root: Path, stage_dir: Path, *, exceptions_path: Path = EXCEPTIONS_PATH) -> dict
def build_tree(data_dir: Path, stage_dir: Path) -> dict
def validate_publication(raw_root: Path, player_games_root: Path, publication_dir: Path, *, exceptions_path: Path = EXCEPTIONS_PATH) -> dict
def run(data_dir: Path) -> None
def render_audit(manifest: dict, *, snapshot_name: str | None, restore_verified: bool) -> str
```

`raw_root` is `data_dir / "raw"`; the build reads `raw/stats_nba/box_rosters`, `raw/nba_injury_reports`, `raw/sportsdataverse/nba_stats_injury_report` and `raw/nba_gamebooks` beneath it.

Exception ledger (`availability_exceptions.json`), initial content exactly:

```json
{
  "appearance_consistency": {},
  "report_versions": {},
  "schedule_dates": {},
  "schema_version": 1,
  "team_resolution": {}
}
```

Entry shapes (every entry also carries a non-empty `note`):

| Section | Key | Fields |
|---|---|---|
| `appearance_consistency` | `season\|game_id\|person_id` | `comment_raw` (str or null), `minutes_raw` (str or null), `appearance` (`"credited"` or `"absent"`) |
| `report_versions` | `report_id` | `action` (`"exclude"` or `"accept_title"`), `sha256`, `failed_check` (str for `exclude`, null for `accept_title`), `title_printed_et` (ISO string or null), `slot_label` |
| `team_resolution` | `report_id\|row_number` | `team_raw`, `nba_team_id` (int) |
| `schedule_dates` | `game_id` | `game_et_raw`, `game_code_raw`, `game_date` (ISO) |

Manifest top-level keys: `schema_version`, `scope`, `schema`, `versions`, `sources`, `references`, `exception_ledger`, `partitions`, `validation`. `sources` has `box_rosters` (list of body bindings), `injury_reports` (`pdfs` list, `inventories` list of `{month, generation, path, sha256, bytes}`), `derivative` (list), `gamebooks` (`pdfs`, `comparisons`). `references` has `player_games_manifest` (`path`, `sha256`, `bytes`, `snapshot_name`), `controls` (path, sha256) and `exception_ledger` (path, sha256).

### Snapshot (`availability_snapshot.py`)

```python
class AvailabilitySnapshotError(ValueError): ...
def validate_layout(raw_root: Path, publication_root: Path, player_games_root: Path, manifest_sha256: str | None = None) -> dict
# CLI: python -m nba_data.availability_snapshot validate-layout --raw-root R --publication-root P --player-games-root G [--manifest-sha256 H]
```

Snapshot layout under `availability-archive-2026-<sha12>/`: `raw/stats_nba/box_rosters/`, `raw/nba_injury_reports/`, `raw/sportsdataverse/nba_stats_injury_report/`, `raw/nba_gamebooks/`, `parquet-large/availability/`, `SHA256SUMS`. Environment: `NBA_AVAILABILITY_ARCHIVE_ROOT` (default `/mnt/nas/llm/documents/nba-predictions-archive`), `NBA_AVAILABILITY_DATA_DIR` (default `data`); the player-games root is always `${NBA_AVAILABILITY_DATA_DIR}/parquet-large/player_games`.

---
## Phase 0 — Dependency gate and the player-games rebuild ceremony

### Task 1: Manifest allow-list diff tool

**Files:**
- Create: `scripts/diff_manifest_allowlist.py`
- Test: `tests/test_manifest_allowlist.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `flatten(document: object, prefix: str = "") -> dict[str, object]` (leaf paths joined with `.`, list indices rendered `[i]`), `differing_paths(before: dict, after: dict) -> list[str]` (sorted union of leaf paths whose values differ or exist on one side only), `check(before: dict, after: dict, allowed: Sequence[str]) -> list[str]` (differing paths not under any allowed prefix; a prefix matches a path equal to it or starting with prefix + `.`), and the CLI `python scripts/diff_manifest_allowlist.py --before A --after B [--allow PREFIX ...]` exiting 0 when `check` is empty (printing each allowed change as `allowed: <path>`), 1 otherwise (printing `disallowed: <path>` per path).

- [ ] **Step 1: Write the failing tests**

```python
def test_flatten_renders_nested_and_list_paths():
    assert flatten({"a": {"b": 1}, "c": [{"d": 2}]}) == {"a.b": 1, "c[0].d": 2}

def test_differing_paths_reports_changed_added_and_removed_leaves():
    before = {"versions": {"dependencies": {"pandas": "3.0.5"}}, "partitions": {"2026": {"sha256": "x"}}}
    after = {"versions": {"dependencies": {"pandas": "3.0.5", "pdfplumber": "0.11.10"}}, "partitions": {"2026": {"sha256": "y"}}}
    assert differing_paths(before, after) == ["partitions.2026.sha256", "versions.dependencies.pdfplumber"]

def test_check_accepts_changes_under_allowed_prefixes_only():
    before = {"versions": {"dependencies": {}}, "references": {"build_manifest.json": {"sha256": "a", "bytes": 1}}, "partitions": {"2026": {"sha256": "x"}}}
    after = {"versions": {"dependencies": {"pdfplumber": "0.11.10"}}, "references": {"build_manifest.json": {"sha256": "b", "bytes": 2}}, "partitions": {"2026": {"sha256": "x"}}}
    assert check(before, after, ["versions.dependencies", "references.build_manifest.json"]) == []
    assert check(before, after, ["versions.dependencies"]) == ["references.build_manifest.json.bytes", "references.build_manifest.json.sha256"]

def test_prefix_must_match_on_a_path_boundary():
    assert check({"a": {"bc": 1}}, {"a": {"bc": 2}}, ["a.b"]) == ["a.bc"]

def test_cli_exit_codes(tmp_path):
    # write before/after JSON, run via subprocess with sys.executable; allowed -> 0 with "allowed:" lines, disallowed -> 1 with "disallowed:" lines
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_manifest_allowlist.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `scripts/diff_manifest_allowlist.py`**

Standard library only (`argparse`, `json`, `sys`). Load both documents with `json.loads` and `object_pairs_hook` that rejects duplicate keys. Make the module importable from tests via `importlib.util.spec_from_file_location`, as `tests/test_verify_repository.py` does for `scripts/verify_repository.py`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_manifest_allowlist.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-11-04-game-rosters-and-injury-reports-design.md docs/superpowers/plans/2026-11-04-game-rosters-and-injury-reports.md
git commit -m "docs: availability foundation design and plan

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
git add scripts/diff_manifest_allowlist.py tests/test_manifest_allowlist.py
git commit -m "feat: add manifest allow-list diff tool

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Add `pdfplumber` and regenerate the dependency-bound manifests

**Files:**
- Modify: `requirements.in` (insert `pdfplumber` after `pandas`), `requirements.txt` (exact diff below), `pyproject.toml` (insert `"pdfplumber",` after `"pandas",` in `[project].dependencies`)
- Regenerate: `data/parquet/build_manifest.json`, `data/parquet/historical_games_manifest.json`, `data/parquet/playoff_games_manifest.json`, and the gitignored `data/parquet-large/player_games/` publication

**Interfaces:**
- Consumes: Task 1 CLI.
- Produces: a green suite with `pdfplumber` as a direct requirement; a rebuilt `player_games` publication whose new manifest digest Task 3 archives.

- [ ] **Step 1: Confirm the preconditions**

Run: `git status --short` (clean apart from untracked docs), `ls data/raw/bref data/raw/hoopr data/raw/stats_nba data/raw/historical_bref data/raw/playoff_bref data/raw/stats_nba/player_logs`, `df -h / /home`.
Expected: every raw cache present; ≥ 45 GB free on `/`.

- [ ] **Step 2: Save the before-copies of the four manifests**

```bash
mkdir -p /home/riz/tmp/dep-rebuild-2026-11
cp data/parquet/build_manifest.json data/parquet/historical_games_manifest.json data/parquet/playoff_games_manifest.json /home/riz/tmp/dep-rebuild-2026-11/
cp data/parquet-large/player_games/_manifest.json /home/riz/tmp/dep-rebuild-2026-11/player_games_manifest.json
sha256sum data/parquet-large/player_games/_manifest.json   # expected to begin 73a6085a7bb1
```

- [ ] **Step 3: Apply the requirement changes**

`requirements.in`: add the line `pdfplumber` between `pandas` and `pyarrow`. `pyproject.toml`: add `  "pdfplumber",` between `  "pandas",` and `  "pyarrow",`. `requirements.txt`: apply exactly this diff (hand-edit; it holds every other pin):

```diff
@@ -36,9 +36,12 @@
 cffi==2.1.0
     # via
     #   argon2-cffi-bindings
+    #   cryptography
     #   pyzmq
 charset-normalizer==3.4.9
-    # via requests
+    # via
+    #   pdfminer-six
+    #   requests
 colorama==0.4.6 ; sys_platform == 'win32'
@@ -49,6 +52,8 @@
     #   ipywidgets
 contourpy==1.3.3
     # via matplotlib
+cryptography==50.0.2
+    # via pdfminer-six
 cycler==0.12.1
     # via matplotlib
@@ -260,10 +265,16 @@
     # via jedi
 patsy==1.0.2
     # via statsmodels
+pdfminer-six==20260107
+    # via pdfplumber
+pdfplumber==0.11.10
+    # via -r requirements.in
 pexpect==4.9.0 ; sys_platform != 'emscripten' and sys_platform != 'win32'
     # via ipython
 pillow==12.3.0
-    # via matplotlib
+    # via
+    #   matplotlib
+    #   pdfplumber
 platformdirs==4.11.0
@@ -299,6 +310,8 @@
     # via linearmodels
 pyparsing==3.3.2
     # via matplotlib
+pypdfium2==5.14.0
+    # via pdfplumber
 pytest==9.1.1
     # via -r requirements.in
```

- [ ] **Step 4: Verify the lock is self-consistent and installed**

```bash
uv pip compile --python-version 3.12 --universal requirements.in --output-file requirements.txt
git diff --exit-code requirements.txt
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
.venv/bin/python -c "import pdfplumber, pdfminer, pypdfium2; print(pdfplumber.__version__)"
```

Expected: the compile (the header's own command, without `--upgrade`, so existing pins are preferred) leaves the file unchanged; `pip check` reports no broken requirements; prints `0.11.10`. If the compile changes any pin, stop and report rather than accept the change.

- [ ] **Step 5: Run the dependency gate test to see it fail for the right reason**

Run: `.venv/bin/python -m pytest tests/test_repository_layout.py::test_build_manifests_dependencies_match_direct_requirements -v`
Expected: FAIL because the manifests lack `pdfplumber`.

- [ ] **Step 6: Regenerate the reference products and the player-games publication, in this order**

```bash
.venv/bin/python -m nba_data build
.venv/bin/python -m nba_data build-historical
.venv/bin/python -m nba_data build-playoffs
.venv/bin/python -m nba_data build-player-games
```

Each is offline. `build-playoffs` runs after `build-historical` because it pins the historical manifest; `build-player-games` runs last because it pins `build_manifest.json` and `playoff_games_manifest.json`.

- [ ] **Step 7: Verify that only the allow-listed manifest paths changed**

```bash
git status --short data/      # expected: exactly the three manifest JSON files modified, no Parquet file
B=/home/riz/tmp/dep-rebuild-2026-11
.venv/bin/python scripts/diff_manifest_allowlist.py --before $B/build_manifest.json --after data/parquet/build_manifest.json --allow versions.dependencies
.venv/bin/python scripts/diff_manifest_allowlist.py --before $B/historical_games_manifest.json --after data/parquet/historical_games_manifest.json --allow versions.dependencies
.venv/bin/python scripts/diff_manifest_allowlist.py --before $B/playoff_games_manifest.json --after data/parquet/playoff_games_manifest.json --allow versions.dependencies --allow sources.basketball_reference.historical_manifest.sha256
.venv/bin/python scripts/diff_manifest_allowlist.py --before $B/player_games_manifest.json --after data/parquet-large/player_games/_manifest.json --allow versions --allow references.build_manifest.json --allow references.playoff_games_manifest.json
```

Expected: all four exit 0; the `player_games` diff lists only the two reference pins (its `partitions`, `sources`, `validation`, `schema`, `scope` and `exception_ledger` are unchanged, which proves the 15 partitions byte-identical). Any Parquet file reported modified by `git status` stops the task: report, do not commit.

- [ ] **Step 8: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests pass, including the dependency gate, `test_requirements_are_utf8_and_pinned` and `test_wheel_declares_runtime_dependencies_and_imports_off_repo`.

- [ ] **Step 9: Commit**

```bash
git add requirements.in requirements.txt pyproject.toml data/parquet/build_manifest.json data/parquet/historical_games_manifest.json data/parquet/playoff_games_manifest.json
git commit -m "build: add pdfplumber and regenerate dependency-bound manifests

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Archive, restore-verify and record the rebuilt player-games publication

**Files:**
- Modify: `docs/audits/2026-08-02-player-games-source-audit.md` (append the amendment)
- Modify: `tests/test_repository_layout.py::test_player_games_final_audit_is_present`

**Interfaces:**
- Consumes: the Task 2 publication at `data/parquet-large/player_games/`.
- Produces: NAS snapshot `player-games-archive-2026-<new sha12>`; the new digest and snapshot name that Task 15 binds into the availability manifest (`references.player_games_manifest.snapshot_name`).

- [ ] **Step 1: Archive the rebuilt publication**

```bash
NEW_SHA=$(sha256sum data/parquet-large/player_games/_manifest.json | cut -c1-64); echo "$NEW_SHA"
scripts/archive_player_games_snapshot.sh
ls /mnt/nas/llm/documents/nba-predictions-archive/ | grep player-games-archive
```

Expected: a new `player-games-archive-2026-${NEW_SHA:0:12}` beside the retained `player-games-archive-2026-73a6085a7bb1`.

- [ ] **Step 2: Restore into an absent target and prove byte identity**

```bash
RESTORE=/home/riz/tmp/pg-restore-$(date +%Y%m%d%H%M%S)
NBA_PLAYER_GAMES_DATA_DIR="$RESTORE" NBA_PLAYER_GAMES_REFERENCE_DIR="$PWD/data/parquet" \
  scripts/restore_player_games_snapshot.sh "player-games-archive-2026-${NEW_SHA:0:12}"
diff <(cd data && find raw/stats_nba/player_logs parquet-large/player_games -type f -print0 | sort -z | xargs -0 sha256sum) \
     <(cd "$RESTORE" && find raw/stats_nba/player_logs parquet-large/player_games -type f -print0 | sort -z | xargs -0 sha256sum)
rm -rf "$RESTORE"
```

Expected: the restore script succeeds and the `diff` prints nothing.

- [ ] **Step 3: Write the failing audit test change**

Extend `test_player_games_final_audit_is_present` with:

```python
    assert "## Amendment 2026-11" in audit
    assert "Dependency-only rebuild" in audit
    assert re.search(r"Snapshot: `player-games-archive-2026-[0-9a-f]{12}` \(amendment", audit)
```

Run: `.venv/bin/python -m pytest tests/test_repository_layout.py::test_player_games_final_audit_is_present -v` → FAIL.

- [ ] **Step 4: Append the amendment**

Append to `docs/audits/2026-08-02-player-games-source-audit.md`, filling the placeholders:

```markdown

## Amendment 2026-11-<dd>: Dependency-only rebuild

Reason: `pdfplumber` became a direct requirement for the availability
publication (`docs/superpowers/specs/2026-11-04-game-rosters-and-injury-reports-design.md`);
`test_build_manifests_dependencies_match_direct_requirements` binds direct
requirements into the reference manifests that this publication pins.

Manifest SHA-256: `<NEW_SHA>` (amendment; supersedes `73a6085a7bb1c738bc75a97cb48b380633d91b13bd14154ebfb2442e74201f78`)
Snapshot: `player-games-archive-2026-<NEW_SHA first 12>` (amendment; `player-games-archive-2026-73a6085a7bb1` retained)
Restore verified: yes (absent target on /home, <date>, byte-identical raw pairs and partitions)
Allowed manifest differences: `versions`; `references.build_manifest.json`; `references.playoff_games_manifest.json`
Reference manifests regenerated: `build_manifest.json`, `historical_games_manifest.json` (`versions.dependencies`), `playoff_games_manifest.json` (`versions.dependencies`, `sources.basketball_reference.historical_manifest.sha256`)
Partition digests: unchanged (15 of 15); every `data/parquet/*.parquet` product unchanged
Everything above this amendment describes the superseded digest and remains the reviewed record.
```

- [ ] **Step 5: Run the layout tests**

Run: `.venv/bin/python -m pytest tests/test_repository_layout.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add docs/audits/2026-08-02-player-games-source-audit.md tests/test_repository_layout.py
git commit -m "docs: amend player-games audit for the pdfplumber rebuild

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---
## Phase 1 — Part A: box-score rosters and scheduled starts

### Task 4: Box-roster worklist, identities, validators and cache reads

**Files:**
- Create: `src/nba_data/box_rosters.py` (first half), `tests/fixtures/box_rosters/0022500001.traditional.json`, `tests/fixtures/box_rosters/0022500001.summary.json`, `tests/fixtures/box_rosters/0042400401.traditional.json`, `tests/fixtures/box_rosters/0042400401.summary.json`
- Test: `tests/test_box_rosters.py`

**Interfaces:**
- Consumes: `cache.publish_bytes`, `cache.read_attested`, `cache.CacheIntegrityError`; the `player_games` publication layout (`_manifest.json` with `partitions[season].{path, sha256}`; partitions with columns `game_id, person_id, season, game_type, game_date, nba_team_id, franchise_id, team_abbreviation_raw`).
- Produces: the Part A contract names `WorklistGame`, `PlayerGamesIndex`, `load_player_games_index`, `game_keys`, `request_parameters`, `cache_path`, `expected_metadata`, `validate_traditional_bytes`, `validate_summary_bytes`, `validate_traditional`, `validate_summary`, `read_cached`, `validate_cache_inventory`, plus `class BoxRosterValidationError(ValueError)`.

- [ ] **Step 1: Author the four fixture bodies**

Source-shaped, hand-trimmed JSON with the real key names and types shown here; keep each under 6 KB. Traditional (`{"meta": {...}, "boxScoreTraditional": {...}}`):

```json
{"meta": {"version": 1, "code": 200},
 "boxScoreTraditional": {
  "gameId": "0022500001", "awayTeamId": 1610612745, "homeTeamId": 1610612760,
  "homeTeam": {"teamId": 1610612760, "teamCity": "Oklahoma City", "teamName": "Thunder", "teamTricode": "OKC", "teamSlug": "thunder",
    "players": [
      {"personId": 1629652, "firstName": "Luguentz", "familyName": "Dort", "nameI": "L. Dort", "playerSlug": "luguentz-dort", "position": "F", "comment": "", "jerseyNum": "5", "statistics": {"minutes": "32:10", "points": 12}},
      {"personId": 1631096, "firstName": "Ousmane", "familyName": "Dieng", "nameI": "O. Dieng", "playerSlug": "ousmane-dieng", "position": "", "comment": "DNP - Coach's Decision", "jerseyNum": "13", "statistics": {"minutes": "", "points": 0}}
    ],
    "statistics": {}, "starters": {}, "bench": {}},
  "awayTeam": {"teamId": 1610612745, "teamCity": "Houston", "teamName": "Rockets", "teamTricode": "HOU", "teamSlug": "rockets",
    "players": [
      {"personId": 1630224, "firstName": "Jabari", "familyName": "Smith Jr.", "nameI": "J. Smith Jr.", "playerSlug": "jabari-smith-jr", "position": "F", "comment": "", "jerseyNum": "10", "statistics": {"minutes": "35:02", "points": 20}},
      {"personId": 1627783, "firstName": "Dorian", "familyName": "Finney-Smith", "nameI": "D. Finney-Smith", "playerSlug": "dorian-finney-smith", "position": "", "comment": "DND - Injury/Illness", "jerseyNum": "7", "statistics": {"minutes": "", "points": 0}}
    ],
    "statistics": {}, "starters": {}, "bench": {}}}}
```

Summary (`{"meta": {...}, "boxScoreSummary": {...}}`) with `gameId`, `gameCode` (`"20251021/HOUOKC"`), `gameTimeUTC` (`"2025-10-21T23:30:00Z"`), `gameEt` (`"2025-10-21T19:30:00Z"`), `awayTeamId`, `homeTeamId`, `isNeutral`, `arena` (object), and `homeTeam`/`awayTeam` each with `teamId`, `teamCity`, `teamName`, `teamTricode`, `teamSlug` and `inactives` (`[{"personId": 1642850, "firstName": "Thomas", "familyName": "Sorber", "jerseyNum": "12  "}]` for OKC; `[]` for HOU). The second game (`0042400401`, `20250605/INDOKC`, `gameTimeUTC` `2025-06-06T00:30:00Z`, `gameEt` `2025-06-05T20:30:00Z`) has two players per team and one inactive per team, one of whom shares a normalised name with an active player on the other team (used by Task 12).

- [ ] **Step 2: Write the failing tests**

Include a module-level helper `write_player_games_fixture(root: Path, rows: list[dict]) -> None` that writes `season=<s>/player_games.parquet` with the full `player_games` schema (copy `player_games_build.PLAYER_GAMES_ARROW_SCHEMA`) and a `_manifest.json` whose `partitions` carry each partition's real `path`, `sha256`, `bytes`, `rows`; other manifest keys may be minimal.

```python
def test_load_player_games_index_returns_distinct_games_appearances_and_team_rows(tmp_path)
    # two rows for 0022500001 (Dort on OKC, Smith Jr. on HOU) and one row for 0042400401
    # -> games sorted (season, game_date, game_id); appearances["0022500001"] == frozenset({1629652, 1630224});
    #    team_rows["0022500001"][1610612760] == ("<franchise_id>", "OKC", True) i.e. (franchise_id, team_abbreviation_raw, is_home)
    #    manifest_binding["sha256"] == sha256 of the manifest bytes
def test_load_player_games_index_rejects_partition_digest_mismatch(tmp_path)   # tamper a partition -> BoxRosterValidationError
def test_request_parameters_are_the_fixed_v3_strings()
    # traditional == {"GameID": "0022500001", "StartPeriod": "0", "EndPeriod": "0", "StartRange": "0", "EndRange": "0", "RangeType": "0"}; summary == {"GameID": ...}
def test_cache_path_and_expected_metadata(tmp_path)
    # tmp_path/raw/stats_nba/box_rosters/season=2026/0022500001.traditional.json; metadata keys exactly
    # {"provider","resource","endpoint","parameters","season","game_type","game_id"}, endpoint "BoxScoreTraditionalV3"
@pytest.mark.parametrize("mutation", [...])
def test_validate_traditional_bytes_rejects_each_required_field(mutation)
    # mutations: wrong gameId; homeTeamId != homeTeam.teamId; empty players; player missing "comment";
    #            statistics.minutes not a string; teamCity missing; HTML body; non-object JSON
@pytest.mark.parametrize("mutation", [...])
def test_validate_summary_bytes_rejects_each_required_field(mutation)
    # wrong gameId; gameCode "2025-10-21/HOUOKC"; gameTimeUTC not ISO; gameEt missing; inactives not a list;
    # inactive missing personId; teamName missing
def test_validators_accept_the_fixture_bodies()
def test_read_cached_accepts_only_exact_identity(tmp_path)   # publish with expected_metadata -> returns (bytes, meta); altered "parameters" -> CacheIntegrityError
def test_read_cached_rejects_incomplete_pair_and_mismatched_content_length(tmp_path)
def test_validate_cache_inventory_requires_both_pairs_per_game_and_no_extra_files(tmp_path)
def test_game_keys_maps_date_and_tricodes_to_game_id()   # {(date(2025,10,21), "HOU", "OKC"): "0022500001"}
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_box_rosters.py -v`
Expected: FAIL (import error).

- [ ] **Step 4: Implement the identities, validators and reads**

Follow `player_logs.cache_path`/`_expected_metadata`/`_read_cached_entry` for the sidecar identity check (every expected key equal, `content_length` equal to the body length) and `pbp.validate_cache_tree` for the inventory walk (`lstat`, regular files only, no unexpected entries). `load_player_games_index` reads `_manifest.json`, verifies each partition's SHA-256 against its manifest entry before reading it with `pyarrow.parquet.read_table(columns=[...])`, and builds the index from pandas groupbys.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_box_rosters.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/nba_data/box_rosters.py tests/test_box_rosters.py tests/fixtures/box_rosters/
git commit -m "feat: add box-roster identities, validators and worklist index

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Box-roster acquisition and CLI

**Files:**
- Modify: `src/nba_data/box_rosters.py` (add `fetch`), `src/nba_data/pbp.py` (add `request_bytes = _request_bytes` after `_request_bytes`), `src/nba_data/__main__.py`
- Test: `tests/test_box_rosters.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `pbp.RequestPacer`, `pbp.request_bytes(*, session, pacer, endpoint_name, parameters, context, sleeper)`, `stats_nba.preflight_dns`, `player_logs.available_bytes`, Task 4 names.
- Produces: `box_rosters.fetch(data_dir, seasons=None, *, session=None, pacer=None, sleeper=time.sleep)`; CLI `python -m nba_data fetch-box-rosters [--season S ...]`.

- [ ] **Step 1: Write the failing fetch tests**

Fake `session.get` returning objects with `status_code`, `content`, `headers`, `url` (`https://stats.nba.com/stats/<endpoint>?...`), as `tests/test_pbp.py::test_fetch_games_sends_two_serialized_requests_with_exact_sidecars` does; fake `preflight_dns`, `available_bytes`, and the sleeper.

```python
def test_fetch_requests_two_identities_per_uncached_game_in_worklist_order(tmp_path, monkeypatch)
    # worklist from a two-game fixture publication; both pairs land at cache_path with expected_metadata; URLs are
    # .../boxscoretraditionalv3 and .../boxscoresummaryv3 with the fixed parameters; order: game 1 traditional, game 1 summary, game 2 ...
def test_fetch_skips_complete_valid_pairs_and_fails_on_a_damaged_pair(tmp_path, monkeypatch)   # damaged -> CacheIntegrityError before any request
def test_fetch_filters_seasons_and_deduplicates(tmp_path, monkeypatch)
def test_fetch_preflights_once_and_checks_capacity_once_before_the_first_request(tmp_path, monkeypatch)
def test_fetch_retries_transient_statuses_then_stops_and_never_retries_other_4xx(tmp_path, monkeypatch)
    # 503,503,200 -> published with two sleeps of 5 and 10 s; 404 -> SourceAccessError, no sleep
def test_fetch_stops_without_retry_on_a_schema_invalid_200(tmp_path, monkeypatch)   # HTML body -> BoxRosterValidationError, nothing written
def test_fetch_paces_requests_at_least_1_5_seconds_apart(tmp_path, monkeypatch)   # fake clock on the pacer
```

CLI tests in `tests/test_cli.py`, following `test_fetch_player_logs_defaults_to_all_seasons`:

```python
def test_fetch_box_rosters_defaults_to_all_publication_seasons(monkeypatch, tmp_path)   # calls box_rosters.fetch(data, PUBLICATION_SEASONS)
def test_fetch_box_rosters_forwards_deduplicated_seasons(monkeypatch, tmp_path)
def test_fetch_box_rosters_rejects_season_outside_2012_2026(monkeypatch, tmp_path)   # SystemExit
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_box_rosters.py tests/test_cli.py -k "fetch" -v`
Expected: FAIL.

- [ ] **Step 3: Implement `fetch` and the CLI dispatch**

`fetch` loads the index from `data_dir / PLAYER_GAMES_SUBDIR`, selects games whose season is in `seasons` (default `PUBLICATION_SEASONS`), lists uncached identities with `read_cached`, checks `available_bytes(nearest existing ancestor of the raw root) >= FETCH_HEADROOM_BYTES` once, calls `stats_nba.preflight_dns()` once, and for each identity calls `pbp.request_bytes(session=session, pacer=pacer, endpoint_name=ENDPOINTS[endpoint], parameters=request_parameters(game_id, endpoint), context=f"box_rosters {game_id} {endpoint}", sleeper=sleeper)` then `cache.publish_bytes(destination, payload, expected_metadata(game, endpoint), functools.partial(validate_<endpoint>, game_id=game_id))`. Default `session` is `requests.Session()` and default `pacer` is `pbp.RequestPacer()`. In `__main__.py` add `fetch_box_rosters = commands.add_parser("fetch-box-rosters")` with repeatable `--season` (`choices=box_rosters.PUBLICATION_SEASONS`) and dispatch like `fetch-player-logs`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_box_rosters.py tests/test_cli.py tests/test_pbp.py -q`
Expected: PASS (the `pbp` alias changes nothing else).

- [ ] **Step 5: Commit**

```bash
git add src/nba_data/box_rosters.py src/nba_data/pbp.py src/nba_data/__main__.py tests/test_box_rosters.py tests/test_cli.py
git commit -m "feat: add box-roster acquisition command

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Normalize one game into roster and schedule rows

**Files:**
- Modify: `src/nba_data/box_rosters.py` (add `NormalizedGame`, `normalize_game`, `comment_code`)
- Test: `tests/test_box_rosters.py`

**Interfaces:**
- Consumes: Task 4 fixtures and `PlayerGamesIndex`.
- Produces: `normalize_game(traditional, summary, *, game, index) -> NormalizedGame`; `comment_code(comment) -> str`. The `rosters` frame has exactly the `game_rosters` columns in spec order with pandas dtypes `string` for text, `Int64` for ids, `boolean` for `is_home`, `object` dates; the `schedule` dict has keys `game_id, season, game_type, game_date, scheduled_start_utc (aware UTC datetime), scheduled_start_et_raw, game_code_raw`.

- [ ] **Step 1: Write the failing tests**

```python
def test_normalize_game_derives_roster_status_from_appearances_and_inactives()
    # Dort (credited) -> played; Dieng (DNP, not credited) -> active_not_played; Sorber -> inactive;
    # starter_position_raw "F" for Dort, None for Dieng (empty) and Sorber; comment_raw None for Dort and Sorber;
    # comment_code "DNP" and "DND"; minutes_raw "32:10" / None; jersey_num "12" stripped from "12  "; is_home True for OKC
def test_normalize_game_orders_rows_and_resolves_franchise_from_player_games_rows()
    # ordered by (nba_team_id, roster_status, person_id) within the game; franchise_id from index.team_rows
def test_normalize_game_schedule_row_parses_scheduled_start_as_utc_and_keeps_raw_strings()
    # scheduled_start_utc == datetime(2025,10,21,23,30, tzinfo=UTC); scheduled_start_et_raw == "2025-10-21T19:30:00Z"
def test_normalize_game_reports_appearance_disagreements_instead_of_guessing()
    # (a) credited player with comment "DNP - Coach's Decision" -> disagreement {"appearance": "credited", "comment_raw": ...}
    # (b) empty-comment player absent from appearances -> disagreement {"appearance": "absent"}
    # (c) a player_games row with no traditional row -> disagreement; (d) a person in both lists -> BoxRosterValidationError
def test_normalize_game_reports_schedule_disagreement_when_dates_or_tricodes_differ()
    # gameEt date 2025-10-22 vs game_date 2025-10-21 -> schedule_disagreement set; gameCode tricodes != traditional tricodes -> error
def test_normalize_game_rejects_team_ids_that_differ_from_player_games_rows()
def test_normalize_game_returns_team_strings_tricodes_and_roster_counts()
    # team_strings[1610612760] == "Oklahoma City Thunder"; roster_counts == {1610612760: {"active": 2, "inactive": 1}, 1610612745: {"active": 2, "inactive": 0}}
def test_comment_code_splits_on_first_separator_only()   # "DNP - Coach's Decision" -> "DNP"; "NWT" -> "NWT"; "DND - Injury/Illness - Left Knee" -> "DND"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_box_rosters.py -k normalize -v`
Expected: FAIL.

- [ ] **Step 3: Implement `normalize_game` and `comment_code`**

Validate both bodies first (`validate_*_bytes`), require the two `teamId`s to equal the keys of `index.team_rows[game.game_id]` and the summary's `homeTeamId`/`awayTeamId`, build roster rows from `homeTeam.players`, `awayTeam.players` and both `inactives`, raise on a `personId` present in both an active list and an inactive list, then compute disagreements: a credited `person_id` with a non-empty comment, an empty-comment player not credited, a credited person with no traditional row. The schedule disagreement compares `gameEt[:10]`, `gameCode[:8]` (as a date) and `game.game_date`; unequal `gameCode` tricodes versus the traditional `teamTricode`s raise.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_box_rosters.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/nba_data/box_rosters.py tests/test_box_rosters.py
git commit -m "feat: normalize box-score bodies into roster and schedule rows

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Phase 2 — Part B: injury-report acquisition

### Task 7: Slot grids, titles, the slot-window rule and PDF inspection

**Files:**
- Create: `src/nba_data/injury_reports.py` (first half), `src/nba_data/reference/injury_report_controls.json`, `tests/fixtures/injury_reports/` (the six PDFs and `SOURCES.json`)
- Test: `tests/test_injury_reports.py`

**Interfaces:**
- Consumes: `pdfplumber`.
- Produces: `Slot`, `slot_label`, `enumerate_slots`, `slot_from_report_id`, `parse_title`, `title_check`, `printed_to_utc`, `PdfInspection`, `inspect_pdf`, `pdf_path`, `pdf_metadata`, `validate_pdf`, `load_controls`, `class InjuryReportError(ValueError)`.

- [ ] **Step 1: Download and record the six fixture PDFs**

The only network step in Phases 1–4; the host is the public static archive the spec verified. Fetch each URL `https://ak-static.cms.nba.com/referee/injury/<file>` with `curl -sS -o`, verify the digest, and write `tests/fixtures/injury_reports/SOURCES.json` (`{"<file>": {"url", "sha256", "bytes", "pages", "retrieved": "2026-11-<dd>"}}`):

| File | SHA-256 | Bytes | Pages |
|---|---|---:|---:|
| `Injury-Report_2018-12-17_05PM.pdf` | `1650d479009749f8a0fe2f665a6e808484967b5b37b6b7a9860d892d99b3ed9d` | 33874 | 4 |
| `Injury-Report_2019-02-15_01PM.pdf` | `ad6d4f22b9695e9e55b64258a240c5843b114e35cf48ac24b1dc56bce8d8b95e` | 14247 | 1 |
| `Injury-Report_2019-09-30_01PM.pdf` | `26d4f750e533cb95e2c5350106470335271b2f86bfa67ee6c49800867f92b53a` | 14369 | 1 |
| `Injury-Report_2021-04-01_05PM.pdf` | `d3d44a16eaa769d10aabee15bc77a19432f74d03cbf81c723014aa9d168250b0` | 30962 | 4 |
| `Injury-Report_2024-01-31_05PM.pdf` | `e39fe95bff31019494b82c9ec019919e94d8f10494395bce643d2c1ba02be17c` | 80020 | 7 |
| `Injury-Report_2026-01-10_12_00AM.pdf` | `48987251578d596867c1e35e8330a5fe23c0ba60f3b567e7e372db1cfbf0073c` | 74897 | 5 |

A digest mismatch stops the task (report it). Write `src/nba_data/reference/injury_report_controls.json` exactly as in Shared Contracts (the third control, `Injury-Report_2025-12-22_05_30PM.pdf`, is not a fixture; its digest is recorded from the spec's 2026-11-04 download).

- [ ] **Step 2: Write the failing tests**

```python
def test_slot_label_renders_both_grids()   # ("hour",0,0)->"12AM"; ("hour",13,0)->"01PM"; ("quarter",0,0)->"12_00AM"; ("quarter",17,30)->"05_30PM"
def test_enumerate_slots_counts_90912_with_both_grids_in_december_2025_and_no_duplicate_report_ids()
    # len == 90912; December 2025 == 31*24 + 31*96; {"Injury-Report_2025-12-22_05PM", "Injury-Report_2025-12-22_05_00PM"} both present
def test_enumerate_slots_month_filter_and_ordering()   # months=["2024-01"] -> 744 slots sorted by (slot_date, slot_start_et, grid); Slot.month/url/file_name shapes
def test_slot_from_report_id_round_trips()
def test_parse_title_reads_the_printed_time()   # "Injury Report: 01/31/24 05:30 PM" -> datetime(2024,1,31,17,30); garbage -> None
@pytest.mark.parametrize(...)
def test_title_check_uses_the_slot_window()
    # hour slot 01PM with printed 13:30 -> ok; printed 14:05 -> failed; quarter slot 01_15PM with 13:20 -> ok, 13:31 -> failed;
    # printed date != slot date -> failed; None -> unparsed; hour slot 10PM with 22:37 -> ok
def test_printed_to_utc_handles_est_edt_fold_and_gap()
    # 2024-01-31 17:30 -> 22:30Z (False); 2024-07-01 17:30 -> 21:30Z (False);
    # 2024-11-03 01:30 -> (None, True); 2024-03-10 02:30 -> raises InjuryReportError
def test_inspect_pdf_reads_pages_and_title_from_each_fixture()   # pages per SOURCES.json; title_printed_et as printed (2018-12-17 17:30 ...)
def test_inspect_pdf_marks_unopenable_bodies()   # b"%PDF-1.4 garbage" -> opens False, pages None, title None
def test_pdf_path_and_metadata(tmp_path)   # raw/nba_injury_reports/pdfs/2024-01/Injury-Report_2024-01-31_05PM.pdf; metadata keys exactly as contracted; title_check "ok"
def test_validate_pdf_requires_the_signature(tmp_path)
def test_load_controls_returns_three_entries_with_digests()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_injury_reports.py -v`
Expected: FAIL.

- [ ] **Step 4: Implement the first half of `injury_reports.py`**

`enumerate_slots` iterates each grid's date range and slot minutes; `Slot.slot_start_et` is the naive Eastern slot start; `title_check` is `ok` when `printed.date() == slot_date and slot_start_et <= printed < slot_start_et + minutes`. `printed_to_utc` uses `datetime.replace(tzinfo=EASTERN, fold=0)` and `fold=1`: different UTC results mean ambiguous (return `(None, True)`); a non-existent local time (the two folds round-trip to a different wall time) raises. `inspect_pdf` opens `io.BytesIO(payload)` with `pdfplumber.open` inside `try/except Exception` (`opens=False` on failure), reads page one's words with `x_tolerance=1.5` and joins them to find the title.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_injury_reports.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/nba_data/injury_reports.py src/nba_data/reference/injury_report_controls.json tests/test_injury_reports.py tests/fixtures/injury_reports/
git commit -m "feat: add injury-report slot grids, title rules and fixtures

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Inventory generations, controls, batched fetch and recheck

**Files:**
- Modify: `src/nba_data/injury_reports.py` (second half), `src/nba_data/__main__.py`
- Test: `tests/test_injury_reports.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 7 names; `cache.publish_bytes`, `cache.read_attested`.
- Produces: `inventory_dir`, `generation_number`, `read_current_inventory`, `write_inventory_generation`, `check_control`, `fetch`; CLI `python -m nba_data fetch-injury-reports [--month YYYY-MM ...] [--recheck-unavailable]`.

- [ ] **Step 1: Write the failing inventory tests**

```python
def test_write_inventory_generation_numbers_above_every_existing_file_and_binds_a_sidecar(tmp_path)
    # gen-0001 then gen-0002; a stray damaged gen-0003.json without sidecar -> next write is gen-0004
def test_read_current_inventory_returns_highest_verifying_generation_and_lists_damaged_ones(tmp_path)
    # gen-0002 body replaced but sidecar stale (simulated interruption) -> current is gen-0001, damaged == (gen-0002,)
def test_read_current_inventory_rejects_month_mismatch_and_schema_drift(tmp_path)
```

- [ ] **Step 2: Write the failing fetch tests**

Fake `session.get(url, headers=..., timeout=60)` by URL: `200` with a PDF fixture body, `403` with `b'<?xml version="1.0" encoding="UTF-8"?><Error><Code>AccessDenied</Code>...'` and headers `Date`, `x-amz-request-id`, `x-amz-id-2`, `503`, `200` HTML. Use `months=["2024-01"]` worklists trimmed by monkeypatching `enumerate_slots` to a handful of slots where needed, a fake pacer, a fake sleeper and a fixed `today`.

```python
def test_fetch_runs_a_control_before_the_first_probe_and_fails_closed_on_a_bad_control(tmp_path)   # control body wrong -> SourceAccessError, no probe, no generation
def test_fetch_archives_pdfs_and_records_unavailable_evidence_verbatim(tmp_path)
    # present slot -> pdf pair with pdf_metadata (title_check from the fixture); unavailable slot -> evidence entry with the exact fields and body_text
def test_fetch_writes_one_generation_per_touched_month_only_after_a_successful_control(tmp_path)
    # 450 probes -> controls at start, after 200, after 400 and a closing control; generations gen-0001..gen-0004 for the month
def test_fetch_discards_the_unpersisted_batch_when_a_mid_run_control_fails(tmp_path)
    # second control fails -> SourceAccessError; the first batch's generation exists, nothing from the second batch is on disk except already-published PDF pairs
def test_fetch_resumes_from_pdfs_on_disk_and_the_previous_generation(tmp_path)
    # a published PDF pair without any generation entry is treated as present and not re-requested; unavailable slots from the previous generation are not re-probed; new generation merges both
def test_fetch_retries_transient_errors_and_stops_on_other_outcomes(tmp_path)   # 503,503,200 -> archived; 200 HTML -> SourceAccessError after retries; 403 with a 2 KB body -> retry path, not unavailable
def test_fetch_never_rerequests_a_present_slot_and_never_replaces_a_body(tmp_path)
def test_recheck_unavailable_rerequests_only_unavailable_slots_dated_before_today(tmp_path)
    # still 403 -> second evidence entry; now 200 -> PDF archived, status present, history retained; slot with today's evidence skipped and counted
def test_fetch_paces_probes_at_least_half_a_second_apart(tmp_path)
```

CLI tests: `test_fetch_injury_reports_defaults_to_the_whole_window`, `test_fetch_injury_reports_forwards_months_and_recheck_flag`, `test_fetch_injury_reports_rejects_a_month_outside_the_window` (`2018-11`, `2026-10` → `SystemExit`).

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_injury_reports.py tests/test_cli.py -k "inventory or fetch_injury or recheck" -v`
Expected: FAIL.

- [ ] **Step 4: Implement generations, controls and `fetch`**

Generation files are written with `cache.publish_bytes(inventory_dir/ f"gen-{n:04d}.json", payload, {"provider": "nba_official", "resource": "injury_report_inventory", "month": month, "generation": n}, validator)` where the validator parses the JSON and checks `schema_version == 1` and `month`. `read_current_inventory` sorts `gen-*.json` bodies by number descending, returns the first whose `cache.read_attested` succeeds, and lists the others that failed. `fetch` builds the known state per month as the current generation's slots plus any PDF pair on disk (present, from the pair's own sidecar), probes the remaining slots in `enumerate_slots` order with `requests` (`headers={"User-Agent": USER_AGENT}`, `timeout=TIMEOUT_S`, `allow_redirects=False`), keeps pending outcomes in memory, and at every control checkpoint (start, each `CONTROL_INTERVAL` probes, closing) runs `check_control` against each of the three controls in turn, appending the result to each touched month's `controls` list and writing one generation per touched month. A 403 qualifies as `unavailable` only when `len(body) <= MAX_ACCESS_DENIED_BYTES` and `b"<Code>AccessDenied</Code>"` is in the body. The recheck pass iterates `unavailable` slots whose latest `fetched_at` date (UTC) is before `today`. The return value is `{"probed", "present", "unavailable", "rechecked", "skipped_today", "generations_written"}`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_injury_reports.py tests/test_cli.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/nba_data/injury_reports.py src/nba_data/__main__.py tests/test_injury_reports.py tests/test_cli.py
git commit -m "feat: acquire injury reports with controls and inventory generations

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---
## Phase 3 — Part B: parsing and the derivative pin

### Task 9: Report page geometry — header, columns, cells and footer

**Files:**
- Create: `src/nba_data/injury_report_parse.py` (first half)
- Test: `tests/test_injury_report_parse.py`

**Interfaces:**
- Consumes: the six fixture PDFs (Task 7), `pdfplumber`.
- Produces: `Header`, `page_words`, `detect_header`, `column_for`, `group_lines`, `line_cells`, `footer_numbers`, `parse_page`, `ReportParseError`, and the module constants in Shared Contracts.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("file_name, expected_labels", [
    ("Injury-Report_2018-12-17_05PM.pdf", ("Game Date","Game Time","Matchup","Team","Player Name","Category","Reason","Current Status","Previous Status")),
    ("Injury-Report_2019-09-30_01PM.pdf", ("Game Date","Game Time","Matchup","Team","Player Name","Current Status","Reason","Previous Status")),
    ("Injury-Report_2021-04-01_05PM.pdf", ("Game Date","Game Time","Matchup","Team","Player Name","Current Status","Reason")),
    ("Injury-Report_2024-01-31_05PM.pdf", ("Game Date","Game Time","Matchup","Team","Player Name","Current Status","Reason")),
    ("Injury-Report_2026-01-10_12_00AM.pdf", ("Game Date","Game Time","Matchup","Team","Player Name","Current Status","Reason")),
])
def test_detect_header_reads_each_layout_in_page_order(file_name, expected_labels)
    # on every page of the file; column x0 values strictly increasing
def test_detect_header_fails_when_a_required_column_is_missing()   # synthetic words without "Reason" -> ReportParseError
def test_column_for_assigns_by_left_edge_and_last_column_runs_to_the_page_edge()
def test_page_words_keeps_adjacent_cells_separate()   # 2018-12-17 page 1: "Minnesota" and "Timberwolves" (or the 2019 Minnesota row) are separate words, never "MinnesotaTimberwolves"
def test_group_lines_and_line_cells_join_words_within_a_column()   # synthetic words: "Left" "Ankle;" "Sprain" in Reason -> "Left Ankle; Sprain"
def test_footer_numbers_reads_page_i_of_n()   # 2024-01-31 page 3 -> (3, 7); missing footer -> ReportParseError
def test_parse_page_returns_header_lines_below_header_above_footer_and_footer()   # no word unassigned; every line's cells keyed by header labels only
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_injury_report_parse.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement the geometry half**

`detect_header` finds the text line that contains every required label (joining adjacent words for two-word labels such as `Game Date`, `Player Name`, `Current Status`, `Previous Status`), records `(label, x0)` per column in x order, and sets `bottom` to the header line's bottom. `column_for` returns the column whose `x0` is the greatest at or below the word's `x0` (words left of the first column fail the page: "word left of the first column"). `group_lines` sorts words by `top` and starts a new line when `top` differs by more than 1.0 pt from the line's first word. `line_cells` joins each column's words with single spaces in x order. `parse_page` excludes the header line and the footer line, and raises if any word is unassigned.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_injury_report_parse.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/nba_data/injury_report_parse.py tests/test_injury_report_parse.py
git commit -m "feat: parse injury-report page geometry

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Row assembly, placeholders, gates and the hand-checked fixtures

**Files:**
- Modify: `src/nba_data/injury_report_parse.py` (second half)
- Create: `tests/fixtures/injury_reports/<report_id>.expected.csv` for each of the six fixtures
- Test: `tests/test_injury_report_parse.py`

**Interfaces:**
- Consumes: Task 9 names, `injury_reports.parse_title`.
- Produces: `Listing`, `ParsedReport`, `assemble_listings`, `parse_report`.

- [ ] **Step 1: Produce the six expected-rows CSVs by hand-checking the rendered pages**

For each fixture, render every page to PNG with `pypdfium2` (`pdf = pypdfium2.PdfDocument(path); page.render(scale=2).to_pil().save(...)`) into the scratchpad, read each image, and type the rows into `<report_id>.expected.csv` with the contracted columns. The CSV is the oracle: it is written from the picture, not from the parser's output. Rows to be certain about: `Injury-Report_2018-12-17_05PM` page 3 has Portland's `ALL PLAYERS AVAILABLE` placeholder and at least one two-line reason; `Injury-Report_2019-02-15_01PM` has the All-Star `Non-NBA Team` rows (`UNK` matchups, `,` player cell → `is_placeholder=true`, `placeholder_raw=Non-NBA Team`, all other listing cells empty); `Injury-Report_2019-09-30_01PM` has preseason `UNK@…` matchups and `Previous Status`; `Injury-Report_2021-04-01_05PM` and `Injury-Report_2024-01-31_05PM` each include a group whose carried `Team` is printed on the line above its first player and a page break inside a game group; `Injury-Report_2026-01-10_12_00AM` has `NOT YET SUBMITTED` rows. `row_number` counts every listing (placeholders included) in document order across pages.

- [ ] **Step 2: Write the failing tests**

```python
@pytest.mark.parametrize("report_id", [six report ids])
def test_parse_report_matches_the_hand_checked_rows_exactly(report_id)
    # parse_report(bytes) listings == rows of the CSV (typed), pages == SOURCES.json pages, title_printed_et as printed
def test_assemble_listings_carries_forward_group_cells_until_replaced()   # synthetic lines: date/time/matchup/team on line 1, players on lines 1-3, new team on line 4
def test_assemble_listings_joins_a_wrapped_team_cell_over_two_lines()   # "Minnesota" / "Timberwolves" -> "Minnesota Timberwolves"
def test_assemble_listings_attaches_continuation_lines_to_the_nearest_anchor()   # two- and three-line reasons centred around the player row
def test_assemble_listings_recognises_each_placeholder_kind()   # NOT YET SUBMITTED; ALL PLAYERS AVAILABLE; Non-NBA Team with "," player cell -> nulls + placeholder_raw
def test_assemble_listings_fails_on_an_unknown_anchor_without_a_player_name()
@pytest.mark.parametrize("mutation", ["bad_game_date", "bad_game_time", "bad_matchup", "unknown_status", "missing_team", "footer_count_mismatch"])
def test_parse_report_gates_fail_closed(mutation)   # synthetic page structures fed through assemble_listings / a parse_report hook -> ReportParseError naming the gate
def test_parse_report_rejects_a_body_that_does_not_open()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_injury_report_parse.py -v`
Expected: FAIL.

- [ ] **Step 4: Implement row assembly and `parse_report`**

Anchors are lines with a non-empty `Player Name` cell or a placeholder phrase in `Player Name`/`Reason`/`Team`. Carry-forward state (`Game Date`, `Game Time`, `Matchup`, `Team`) updates whenever a line carries one of those cells and applies to the first anchor at or below that line; a line carrying only a `Team` cell directly below a line that also carried only a `Team` cell joins the two with a space. Non-anchor lines carrying `Reason`/`Category` text attach to the anchor with the nearest `top` (ties resolve to the anchor above) and join with single spaces in reading order. Gates, in this order, each raising `ReportParseError(f"{report_id or 'report'}: <gate>")`: header on every page; no unassigned word; carried cells present on every anchor; `GAME_DATE_PATTERN`, `GAME_TIME_PATTERN`, `MATCHUP_PATTERN`; status in `STATUSES` for player rows; footer numbers `(i, N)` equal to the page position and page count; title parses (`parse_title`). `parse_report` sets `title_printed_et` from page one.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_injury_report_parse.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/nba_data/injury_report_parse.py tests/test_injury_report_parse.py tests/fixtures/injury_reports/*.expected.csv
git commit -m "feat: assemble injury-report listings with fail-closed gates

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: Pin the sportsdataverse derivative and normalise it for comparison

**Files:**
- Create: `src/nba_data/injury_derivative.py`
- Modify: `src/nba_data/__main__.py`
- Test: `tests/test_injury_derivative.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `cache.publish_bytes`, `cache.read_attested`.
- Produces: the derivative contract names; CLI `python -m nba_data fetch-injury-derivative`.

- [ ] **Step 1: Write the failing tests**

Build tiny Parquet payloads in-test with the 17 columns (`season, report_datetime, game_date, game_time, report_matchup, away_team_abbr, home_team_abbr, team, report_player_name, current_status, reason, category, previous_status, game_id, team_id, person_id, match_method`).

```python
def test_asset_url_and_path(tmp_path)   # .../releases/download/nba_stats_injury_report/injury_report_2026.parquet; raw/sportsdataverse/nba_stats_injury_report/injury_report_2026.parquet
def test_validate_asset_requires_exactly_the_seventeen_columns(tmp_path)
def test_fetch_records_release_asset_updated_at_and_skips_valid_pairs(tmp_path)
    # fake session: GET RELEASE_API_URL -> {"assets": [{"name": "injury_report_2026.parquet", "updated_at": "2026-11-03T06:10:00Z"}, ...]};
    # GET asset_url -> parquet bytes; sidecar has provider "sportsdataverse", resource "nba_stats_injury_report", season, url, release_asset_updated_at
def test_fetch_fails_closed_when_an_asset_is_missing_from_the_release()
def test_comparison_rows_maps_player_and_placeholder_rows()
    # a NOT YET SUBMITTED reason row with null name/status -> kind "NOT YET SUBMITTED", player_name/status/reason None, artifact False
    # a null-status null-name row with reason "," -> artifact True
    # a player row -> kind "player", fields copied verbatim with whitespace collapsed
```

CLI test: `test_fetch_injury_derivative_dispatches_all_eight_seasons`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_injury_derivative.py tests/test_cli.py -k derivative -v`
Expected: FAIL.

- [ ] **Step 3: Implement the module and CLI**

`fetch` calls the release API once (`headers={"Accept": "application/vnd.github+json", "User-Agent": injury_reports.USER_AGENT}`, 60 s timeout), then downloads each missing season's asset with `allow_redirects=True`, publishing through `cache.publish_bytes` with `validate_asset`. `comparison_rows` reads the Parquet with `pyarrow`, collapses internal whitespace in text columns, and derives `kind`/`artifact` as contracted.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_injury_derivative.py tests/test_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/nba_data/injury_derivative.py src/nba_data/__main__.py tests/test_injury_derivative.py tests/test_cli.py
git commit -m "feat: pin and normalise the sportsdataverse injury derivative

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---
## Phase 4 — Resolution, ledger, build, audit and recovery

### Task 12: Resolution rules and metrics

**Files:**
- Create: `src/nba_data/availability_resolve.py`
- Test: `tests/test_availability_resolve.py`

**Interfaces:**
- Consumes: `box_rosters.NormalizedGame` frames (Task 6) to build the index; listing frames shaped like `injury_report_listings` before resolution columns.
- Produces: the resolution contract names and `class AvailabilityResolveError(ValueError)`.

- [ ] **Step 1: Write the failing tests**

Build small `schedule`/`rosters` frames in-test (two games on one date, three teams, a season roster spanning two games).

```python
@pytest.mark.parametrize("slot_date, season", [
    (date(2018,12,17), 2019), (date(2019,9,30), 2019), (date(2019,10,1), 2020), (date(2020,10,11), 2020),
    (date(2020,11,30), 2020), (date(2020,12,1), 2021), (date(2021,9,30), 2021), (date(2021,10,1), 2022),
    (date(2026,9,30), 2026)])
def test_version_season_boundaries(slot_date, season)
def test_normalize_name_and_name_keys()
    # normalize_name("Dončić") == "doncic"; normalize_name("Finney-Smith") == "finneysmith"; normalize_name("O'Neale") == "oneale"
    # listing_name_key("Porter Jr., Michael") == roster_name_key("Porter Jr.", "Michael") == "porterjr|michael"
    # listing_name_key("Dončić, Luka") == roster_name_key("Doncic", "Luka")
def test_resolve_game_requires_a_unique_key_and_tricode_membership()
    # (date, away, home) -> game_id; swapped tricodes -> no_box_score_game; duplicate key in index -> AvailabilityResolveError
def test_resolve_team_matches_city_plus_name_exactly()   # "LA Clippers" matches city "LA" + name "Clippers"; "Los Angeles Clippers" -> None
def test_resolve_person_prefers_game_roster_then_season_roster_then_unresolved()
    # on game roster -> ("game_roster"); absent from game roster but on the season roster for that team -> ("season_roster");
    # no candidate -> unresolved_no_match; two roster players with the same key -> unresolved_ambiguous (never the first)
def test_resolve_listings_adds_resolution_columns_and_applies_team_exceptions()
    # placeholder rows -> person_resolution "placeholder"; unresolved game -> "unresolved_game", nulls elsewhere;
    # team exception {"report_id|row_number": {"team_raw", "nba_team_id", "note"}} resolves an otherwise unmatched team string
def test_resolution_metrics_measures_rows_and_identities_separately()
    # 20 rows: one identity repeated 10 times unresolved, 10 distinct resolved identities -> row_rate 0.5, identity_rate 10/11
    # breakdowns by team, by month ("2024-01"), by status; unresolved_names carries row and identity counts
def test_resolution_metrics_enumerates_listed_not_rostered_and_unresolved_matchups()
    # season_roster rows counted by team and status; a non-Out season_roster row appears in not_out_rows with (report_id, row_number, game_id, player_name_raw, status)
    # unresolved games grouped by tricode pair: {"PHO@LAL": 7}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_availability_resolve.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement the module**

`normalize_name` uses `unicodedata.normalize("NFKD", value)`, drops combining characters (`unicodedata.combining`), casefolds, and removes `.`, `-`, `'`, `’` and all whitespace. `listing_name_key` splits on the first `", "`; a name without a comma uses the whole string as family and an empty given name. `build_game_index` derives `game_by_key` from the schedule's `game_code_raw` (`YYYYMMDD/AAAHHH` → date, away, home) cross-checked against `game_date` and the roster tricodes, and the roster maps from `rosters` grouped by `(game_id, nba_team_id)` and `(season, nba_team_id)`. `resolve_listings` is vectorised where convenient but must produce exactly the per-row outcomes of the three resolvers; `franchise_id` comes from `index.franchise`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_availability_resolve.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/nba_data/availability_resolve.py tests/test_availability_resolve.py
git commit -m "feat: resolve injury listings to games, teams and persons

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 13: Exception ledger

**Files:**
- Create: `src/nba_data/reference/availability_exceptions.json`, `src/nba_data/availability_build.py` (ledger section only)
- Test: `tests/test_availability_build.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `EXCEPTIONS_PATH`, `load_exceptions`, `validate_exceptions`, `AvailabilityBuildError`; the observed-entry shapes the build emits (same fields as approved entries minus `note`).

- [ ] **Step 1: Write the failing tests**

```python
def test_packaged_ledger_is_exact_canonical_json_with_four_empty_sections()   # bytes == json.dumps(..., indent=2, sort_keys=True) + "\n"; sections as contracted
@pytest.mark.parametrize("payload, match", [...])
def test_load_exceptions_rejects_schema_drift(payload, match)
    # missing section; extra section; duplicate key; malformed composite key ("2024|0022300001" for appearance_consistency);
    # report_versions action not in {"exclude","accept_title"}; exclude with null failed_check; accept_title with non-null failed_check;
    # team_resolution nba_team_id as string; schedule_dates game_date not ISO; empty note; sha256 not 64 hex
def test_validate_exceptions_requires_exact_two_way_equality_ignoring_note()
    # observed entry without approval -> error naming "missing approvals"; approved without observation -> "stale approvals"; value drift -> "drift"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_availability_build.py -k exceptions -v`
Expected: FAIL.

- [ ] **Step 3: Implement the ledger section**

Follow `player_games_build.load_exceptions`/`validate_exceptions`: `object_pairs_hook` rejecting duplicates, `parse_constant` rejecting NaN/Infinity, per-section field sets from the Shared Contracts table, exact types (`type(value) is int`), and the composite-key grammar: `appearance_consistency` keys are `season|game_id|person_id` with a four-digit season, a ten-digit game id and an integer person id; `team_resolution` keys are `report_id|row_number`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_availability_build.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/nba_data/reference/availability_exceptions.json src/nba_data/availability_build.py tests/test_availability_build.py
git commit -m "feat: add the availability exception ledger

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 14: Table assembly, Arrow schemas and partitions

**Files:**
- Modify: `src/nba_data/availability_build.py`
- Test: `tests/test_availability_build.py`

**Interfaces:**
- Consumes: `box_rosters` (Tasks 4–6), `injury_reports` (Tasks 7–8), `injury_report_parse` (Tasks 9–10), `availability_resolve` (Task 12), `load_exceptions` (Task 13).
- Produces:

```python
GAME_ROSTERS_SCHEMA, GAME_SCHEDULE_SCHEMA, INJURY_REPORT_VERSIONS_SCHEMA, INJURY_REPORT_LISTINGS_SCHEMA
@dataclass(frozen=True)
class PartA:
    rosters: pd.DataFrame; schedule: pd.DataFrame
    team_strings: dict[tuple[str, int], str]; tricodes: dict[tuple[str, int], str]
    appearance_observed: dict[str, dict]; schedule_observed: dict[str, dict]
    roster_counts: pd.DataFrame            # game_id, nba_team_id, active, inactive
def assemble_part_a(raw_root: Path, index: box_rosters.PlayerGamesIndex, approved: dict) -> PartA
@dataclass(frozen=True)
class PartB:
    versions: pd.DataFrame; listings: pd.DataFrame
    report_versions_observed: dict[str, dict]; team_resolution_observed: dict[str, dict]
    inventories: list[dict]                 # {month, generation, path}
    slot_totals: dict                       # per grid: present, unavailable, rechecked
    parse_counts: dict; title_counts: dict; placeholder_counts: dict
def assemble_part_b(raw_root: Path, part_a: PartA, approved: dict) -> PartB
def write_partition(table: pa.Table, stage_dir: Path, season: int, name: str) -> Path   # stage_dir/season=<s>/<name>.parquet
def table_from_frame(frame: pd.DataFrame, schema: pa.Schema) -> pa.Table
def logical_row_sha256(table: pa.Table, schema: pa.Schema) -> str
```

- [ ] **Step 1: Write the failing tests**

Seed a tiny raw tree in `tmp_path`: two box-roster pairs per game for the two fixture games (published with `box_rosters.expected_metadata`), a `player_games` fixture publication for those games (Task 4 helper), two PDF pairs in two months (`Injury-Report_2024-01-31_05PM.pdf` under `2024-01`, `Injury-Report_2026-01-10_12_00AM.pdf` under `2026-01`) with `pdf_metadata`, one `gen-0001.json` generation per month listing its PDF plus two rechecked `unavailable` slots (monkeypatch `enumerate_slots` so each month enumerates only those three slots), and the derivative assets for 2024 and 2026 from the Task 11 builder.

```python
def test_schemas_match_the_spec_column_by_column()   # names, arrow types, nullability for all four tables
def test_assemble_part_a_builds_rosters_schedule_and_observed_disagreements(tmp_path)
    # row order (season, game_date, game_id, nba_team_id, roster_status, person_id); appearance_observed keyed "2026|0022500001|<pid>" with
    # the contracted fields; roster_counts rows per team-game
def test_assemble_part_a_requires_every_worklist_game_to_have_both_bodies(tmp_path)   # delete a summary pair -> AvailabilityBuildError
def test_assemble_part_b_builds_versions_and_listings_from_pdfs_and_generations(tmp_path)
    # versions: season by slot date (2024 and 2026), report_slot_et, report_time_printed_et/utc, ambiguous flag False, parse_status "ok",
    #   listing_rows/placeholder_rows/games_listed/games_resolved, identical_listings_to_previous False (first version of each season)
    # listings: row_number per report, resolution columns, ordering (season, report_slot_et, report_id, row_number)
def test_identical_listings_to_previous_compares_multisets_in_slot_order()
    # frame-level: two synthetic parsed versions with the same listing multiset -> second True; a differing reason -> False; an excluded version -> False
def test_assemble_part_b_requires_a_rechecked_outcome_for_every_enumerated_slot(tmp_path)
    # a slot missing from the generation -> error; an unavailable slot with one evidence entry -> error; two entries on the same UTC date -> error
def test_assemble_part_b_uses_only_the_highest_verifying_generation_and_fails_when_it_is_damaged(tmp_path)
def test_assemble_part_b_excluded_and_accept_title_versions(tmp_path)
    # a PDF whose title fails its window: without a ledger entry -> report_versions_observed has it and the build errors in validate step;
    #   with accept_title -> parse_status "ok_title_accepted", listings published; with exclude -> parse_status "excluded", null counts, no listings,
    #   report_time_printed_et retained when the title parsed
def test_write_partition_is_deterministic_and_zstd(tmp_path)   # two writes -> identical bytes; parquet metadata compression ZSTD
def test_logical_row_sha256_ignores_parquet_encoding_but_not_schema()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_availability_build.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement assembly, schemas and partition writing**

Schemas are `pa.schema([...])` with the exact spec types (`pa.large_string()`, `pa.int64()`, `pa.date32()`, `pa.bool_()`, `pa.timestamp("us")`, `pa.timestamp("us", tz="UTC")`). `assemble_part_a` iterates `index.games`, reads both pairs with `box_rosters.read_cached` (missing → error), calls `normalize_game`, concatenates, and turns disagreements into observed-ledger entries. `assemble_part_b` enumerates slots, loads each month's current generation (`read_current_inventory`; damaged highest → error), requires every slot present or rechecked, reads each present PDF pair, parses it with `injury_report_parse.parse_report` (a `ReportParseError`, an unopenable body or a failed `title_check` becomes a `report_versions` observed entry with `failed_check`; an approved `exclude` yields an excluded version row with null counts; an approved `accept_title` publishes the listings with `ok_title_accepted`), resolves listings through `availability_resolve`, computes `identical_listings_to_previous` from `Counter` of listing tuples in slot order per season, and builds the counts. `write_partition` mirrors `player_games_build._write_partition` (Zstandard, deterministic writer options, stage reread); `logical_row_sha256` mirrors `_logical_row_sha256` with the given schema.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_availability_build.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/nba_data/availability_build.py tests/test_availability_build.py
git commit -m "feat: assemble availability tables and partitions

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 15: Gates, derivative agreement, manifest, independent validation and publication

**Files:**
- Modify: `src/nba_data/availability_build.py`, `src/nba_data/__main__.py`
- Test: `tests/test_availability_build.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 14 names, `injury_derivative.comparison_rows`, `publication.publish_tree`, `box_rosters.load_player_games_index`.
- Produces: `compare_version(ours: pd.DataFrame, theirs: pd.DataFrame) -> dict`, `derivative_agreement(part_b: PartB, derivative_root: Path) -> dict`, `build_from_roots`, `build_tree`, `validate_publication`, `run`, `manifest_bytes(manifest) -> bytes`; CLI `python -m nba_data build-availability` (no options; `--season`/`--month` are rejected).

- [ ] **Step 1: Write the failing tests**

```python
def test_compare_version_classifies_rows_and_normalises_placeholders()
    # ours: 2 player rows + 1 NOT YET SUBMITTED placeholder; theirs: same players, one reason differs, placeholder stored in reason
    # -> agree_all 1, agree_except_reason 1, only_ours 0, only_theirs 0; patterns {"reason: ours 'Left Ankle; Sprain' / theirs 'Left Ankle;'": {"count": 1, "report_ids": [...]}}
    # a derivative artifact row (",") is counted under "derivative_artifacts", never as only_theirs
def test_derivative_agreement_joins_on_printed_time_and_lists_unmatched_versions()
def test_gates_fail_closed_and_pass_with_exact_ledger_entries(tmp_path)
    # appearance disagreement without approval -> AvailabilityBuildError("appearance_consistency missing approvals ...");
    # with the exact approved entry -> passes and manifest.exception_ledger.applied lists it
    # identity rate below 0.98 in one season -> error naming the season; row rate fine but identity rate low -> still error
    # version-season mismatch (resolved game season != version season) -> error
def test_build_from_roots_writes_46_partitions_and_a_deterministic_manifest(tmp_path)
    # with the Task 14 seed extended so every season 2012-2026 has one game and 2019-2026 one report: exactly 46 files + _manifest.json;
    # second build -> byte-identical partitions and manifest; manifest keys exactly the nine contracted; no absolute path or timestamp in the JSON
def test_manifest_binds_every_raw_pair_the_player_games_manifest_controls_and_ledger(tmp_path)
    # sources.box_rosters count == 2 * games; sources.injury_reports.pdfs count == present slots; inventories one per month with generation number;
    # references.player_games_manifest.sha256 == index.manifest_binding["sha256"]; references.controls.sha256 == sha of the controls file
def test_validate_publication_recomputes_and_rejects_tampering(tmp_path)
    # tamper a partition byte, a manifest value, a raw PDF, an inventory generation, the ledger -> each rejected; untouched -> returns the manifest
def test_run_stages_rereads_publishes_and_rolls_back_on_failure(tmp_path, monkeypatch)
    # stage under data/parquet-large; a forced reread mismatch leaves no partial output and no stage behind
def test_manifest_records_the_player_games_snapshot_name_derived_from_the_bound_digest(tmp_path)
    # references.player_games_manifest.snapshot_name == "player-games-archive-2026-" + sha256[:12] of the bound manifest
```

CLI test: `test_build_availability_is_offline_dispatch` (monkeypatch `cli.availability_build.run`; no fetch called).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_availability_build.py tests/test_cli.py -k "availability or compare or agreement" -v`
Expected: FAIL.

- [ ] **Step 3: Implement gates, agreement, manifest, validation and run**

Gates, in order: coverage (both bodies per game; every slot rechecked or present; every present PDF parsed or excluded); appearance and schedule consistency against the ledger (`validate_exceptions(observed, approved)` across all four sections at once); version-season agreement; team resolution exact; the two resolution floors per season (`PERSON_RESOLUTION_FLOOR`); 46-partition layout. Derivative agreement joins versions on `report_time_printed_et == report_datetime` (naive, minute precision) and compares `Counter` multisets of `(game_date, matchup, team, kind, player_name, status, reason, category, previous_status)` with whitespace collapsed, where `category` and `previous_status` are dropped from both sides for layouts that lack them (season ≥ 2021 drops both, 2020 drops `category`); patterns are keyed by the differing field set and normalised difference and keep every `report_id`. `versions` in the manifest mirrors `player_games_build._versions` plus `pdfplumber`, `pdfminer-six`, `pypdfium2` pins from `requirements.txt`. The D1 snapshot name is derived, not supplied: `references.player_games_manifest.snapshot_name` is `"player-games-archive-2026-" + sha256[:12]` of the bound `player_games` manifest, which is exactly how `scripts/archive_player_games_snapshot.sh` names the snapshot; Task 22 proves that snapshot exists and restores. `validate_publication` recomputes the complete manifest from the raw trees and compares it with the stored one key by key, as `player_games_build.validate_publication` does. `run` stages under `data_dir / "parquet-large"` with `tempfile.mkdtemp(prefix=".availability-build-")`, builds, rereads with `validate_publication`, and publishes with `publication.publish_tree(stage_dir, output_dir, label="availability publication")`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_availability_build.py tests/test_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/nba_data/availability_build.py src/nba_data/__main__.py tests/test_availability_build.py tests/test_cli.py
git commit -m "feat: build, validate and publish the availability tables

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 16: Offline audit script and gamebook import

**Files:**
- Create: `scripts/audit_availability_sources.py`, `src/nba_data/gamebooks.py`
- Modify: `src/nba_data/__main__.py`, `src/nba_data/availability_build.py` (add `render_audit`)
- Test: `tests/test_gamebooks.py`, `tests/test_availability_build.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: Tasks 4–15.
- Produces:

```python
# gamebooks.py
RAW_SUBDIR = "raw/nba_gamebooks"
FILE_NAME_PATTERN = re.compile(r"^(\d{8})_([A-Z]{3})([A-Z]{3})_book\.pdf$")
def gamebook_url(game_code_raw: str) -> str      # "20250605/INDOKC" -> https://statsdmz.nba.com/pdfs/20250605/20250605_INDOKC_book.pdf
def import_gamebooks(data_dir: Path, source_dir: Path, *, game_keys: Mapping[tuple[date, str, str], str], retrieved_on: date) -> tuple[Path, ...]
def compare_rosters(game_id: str, gamebook_names: Mapping[str, Mapping[str, list[str]]], rosters: pd.DataFrame) -> dict
    # gamebook_names: tricode -> {"active": [...], "inactive": [...]} as printed; returns
    # {"game_id", "teams": {tricode: {"gamebook_active", "gamebook_inactive", "roster_active", "roster_inactive",
    #   "only_in_gamebook", "only_in_roster"}}, "differences": int, "reviewer_note": None}
def write_comparison(data_dir: Path, record: dict) -> Path   # publish_bytes to raw/nba_gamebooks/comparisons/<game_id>.json
def select_gamebook_sample(schedule: pd.DataFrame, roster_counts: pd.DataFrame) -> list[dict]
    # deterministic part: per season the earliest regular-season game and the latest playoff game (30 games), the earliest team-game with an
    # empty inactive list, the earliest 2020 bubble game (game_date >= 2020-07-30); plus three owner-selected placeholders
    # {"case": "two_way" | "trade_within_two_days" | "season_long_absence", "game_id": None}
```

`box_rosters.game_keys` (Task 4) supplies the `(game_date, away, home) -> game_id` mapping from `team_rows`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gamebooks.py
def test_gamebook_url_from_game_code()
def test_import_gamebooks_derives_game_id_checks_signature_and_writes_sidecars(tmp_path)
    # "20251021_HOUOKC_book.pdf" with b"%PDF..." -> raw/nba_gamebooks/0022500001.book.pdf; sidecar provider "nba_official", resource "gamebook",
    # game_id, game_code "20251021/HOUOKC", url, retrieved_on ISO, retrieved_by "browser"; a non-PDF or an unknown key -> error, nothing written
def test_compare_rosters_reports_differences_per_team_and_list()
def test_write_comparison_publishes_a_bound_record(tmp_path)
def test_select_gamebook_sample_is_deterministic_and_covers_the_required_cases()
# tests/test_availability_build.py
def test_render_audit_states_manifest_digest_snapshot_and_restore_line()   # "Manifest SHA-256: `...`", "Snapshot: `availability-archive-2026-...`", "Restore verified: yes"
def test_audit_script_writes_review_artifacts_to_an_empty_directory(tmp_path)   # subprocess; refuses a non-empty output dir; writes review.md, resolution.json, disagreement_patterns.json, ledger_candidates.json, gamebook_sample.json
# tests/test_cli.py
def test_import_gamebooks_dispatch(monkeypatch, tmp_path)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_gamebooks.py tests/test_availability_build.py tests/test_cli.py -k "gamebook or audit" -v`
Expected: FAIL.

- [ ] **Step 3: Implement the script and module**

`scripts/audit_availability_sources.py --data-dir D --output-dir O [--gamebook-sample]` loads the index and the raw trees through `assemble_part_a`/`assemble_part_b` with the committed ledger (without gating; observed disagreements are reported, not raised), computes `resolution_metrics` and `derivative_agreement`, and writes: `review.md` (sections for roster consistency and roster-size outliers, parse-status and title-check counts, placeholder counts, resolution rates with the three breakdowns, unresolved names and matchups, listed-but-not-rostered, derivative disagreement patterns, unmatched versions, proposed ledger candidates), `resolution.json`, `disagreement_patterns.json`, `ledger_candidates.json` (observed entries in approved-entry shape with `"note": ""`), and `gamebook_sample.json` (`select_gamebook_sample` with each game's `gamebook_url`). The output directory must be absent or empty (follow `player_games_build.write_source_review`). `render_audit` renders the committed audit text from the manifest plus snapshot name and restore flag, in the style of `docs/audits/2026-08-02-player-games-source-audit.md`. The CLI gains `import-gamebooks <source_dir>` (positional path; dispatches `gamebooks.import_gamebooks(data, source_dir, game_keys=box_rosters.game_keys(index), retrieved_on=date.today())`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gamebooks.py tests/test_availability_build.py tests/test_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/audit_availability_sources.py src/nba_data/gamebooks.py src/nba_data/availability_build.py src/nba_data/__main__.py tests/test_gamebooks.py tests/test_availability_build.py tests/test_cli.py
git commit -m "feat: add availability source audit and gamebook import

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 17: Snapshot validator and archive/restore scripts

**Files:**
- Create: `src/nba_data/availability_snapshot.py`, `scripts/archive_availability_snapshot.sh`, `scripts/restore_availability_snapshot.sh`
- Test: `tests/test_availability_snapshot.py`, `tests/test_repository_layout.py`

**Interfaces:**
- Consumes: `availability_build.validate_publication`, `box_rosters.validate_cache_inventory`, `injury_reports.read_current_inventory`, `cache.read_attested`.
- Produces: `validate_layout(raw_root, publication_root, player_games_root, manifest_sha256=None) -> dict`; CLI `python -m nba_data.availability_snapshot validate-layout ...`; the two scripts honouring `NBA_AVAILABILITY_ARCHIVE_ROOT` and `NBA_AVAILABILITY_DATA_DIR`.

- [ ] **Step 1: Write the failing validator tests**

Seed the Task 15 tiny end-to-end tree (raw roots, `player_games` publication, built availability publication).

```python
def test_validate_layout_accepts_a_complete_tree_and_returns_the_manifest(tmp_path)
def test_validate_layout_rejects_each_raw_defect(tmp_path, defect)
    # missing box-roster pair; unverifiable inventory generation (any generation, not only the highest); tampered PDF body; missing derivative sidecar;
    # tampered gamebook comparison record; symlink inside a raw tree
def test_validate_layout_rejects_publication_defects_and_wrong_pins(tmp_path)
    # tampered partition; manifest digest argument mismatch; player_games manifest digest != references.player_games_manifest.sha256
def test_cli_validate_layout_exit_codes(tmp_path)
```

- [ ] **Step 2: Write the failing script tests**

Follow `tests/test_player_games_snapshot.py::_run_archive`/`_run_restore_script` (subprocess with `NBA_AVAILABILITY_*` environment, a shim `PATH` where the existing tests use one). Cover: archive success with the exact top-level layout (`SHA256SUMS`, `parquet-large`, `raw` with the four subtrees) and name `availability-archive-2026-<sha12>`; idempotent success only for a byte-identical existing snapshot and refusal otherwise; stale `.availability-archive.*` stage refusal; capacity refusal; refusal when any raw pair is unverifiable (damaged generation); live mutation during copy; restore success only into absent `raw/...` and `parquet-large/availability` targets; refusal when a target exists or is a symlink; refusal when `${DATA_DIR}/parquet-large/player_games/_manifest.json` is missing or its digest differs from the bound `references.player_games_manifest.sha256`; snapshot `SHA256SUMS` tamper refusal; snapshot mutation during restore. Add to `tests/test_repository_layout.py` an executable-and-tracked test for the two scripts, modelled on `test_player_games_archive_scripts_are_executable_and_tracked`.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_availability_snapshot.py tests/test_repository_layout.py -k "availability" -v`
Expected: FAIL.

- [ ] **Step 4: Implement the validator**

`validate_layout` walks the four raw subtrees with an `lstat` regular-file check (copy `player_games_snapshot._require_regular_tree`), attests every `*.meta.json` pair under them (`cache.read_attested` with the owning module's validator: box rosters by endpoint, PDFs by `injury_reports.validate_pdf`, generations by the generation validator, derivative by `validate_asset`, gamebooks by signature, comparisons as JSON objects), requires `box_rosters.validate_cache_inventory` to pass for the worklist of `player_games_root`, then calls `availability_build.validate_publication(raw_root, player_games_root, publication_root)` and finally compares the optional digest. The CLI mirrors `player_games_snapshot._build_parser` with `--raw-root`, `--publication-root`, `--player-games-root`, `--manifest-sha256`.

- [ ] **Step 5: Implement the two scripts**

Copy the structure of `scripts/archive_player_games_snapshot.sh` and `scripts/restore_player_games_snapshot.sh` (`set -euo pipefail`, `LC_ALL=C`, `umask 077`, EXIT versus INT/TERM traps, same-filesystem `mktemp -d "$archive_root/.availability-archive.XXXXXX"`, `rsync -a --no-times --checksum`, sorted `SHA256SUMS`, capacity rule `available >= allocated + total/5`, live-drift dry run, atomic rename to `availability-archive-2026-<sha12>`, refusal to overwrite, byte-identical idempotence). Replace the module calls with `python -m nba_data.availability_snapshot validate-layout --raw-root "$data_dir/raw" --publication-root "$data_dir/parquet-large/availability" --player-games-root "$data_dir/parquet-large/player_games"`; the restore script additionally reads `references.player_games_manifest.sha256` from the snapshot's manifest with `python -c` and refuses unless the live `player_games/_manifest.json` hashes to it, then stages each of the five destination trees beside its parent and renames only after validation.

- [ ] **Step 6: Run the tests and shell syntax checks**

```bash
bash -n scripts/archive_availability_snapshot.sh && bash -n scripts/restore_availability_snapshot.sh
.venv/bin/python -m pytest tests/test_availability_snapshot.py tests/test_repository_layout.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
chmod +x scripts/archive_availability_snapshot.sh scripts/restore_availability_snapshot.sh
git add src/nba_data/availability_snapshot.py scripts/archive_availability_snapshot.sh scripts/restore_availability_snapshot.sh tests/test_availability_snapshot.py tests/test_repository_layout.py
git commit -m "feat: archive and restore the availability snapshot

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---
## Phase 5 — Acquisition, review checkpoint and release

The tasks in this phase run real network acquisitions that take hours. Launch each long command detached (`setsid nohup … > /home/riz/tmp/<name>.log 2>&1 &`) and supervise it as the `detached-job-supervision` skill describes; never leave an acquisition's success unverified because a log looked quiet. All fetches are resumable: re-running the same command after an interruption continues from the cache.

### Task 18: Acquire all box-score bodies

**Files:** none (raw data only).

**Interfaces:**
- Consumes: Task 5 CLI.
- Produces: 38,366 verified pairs under `data/raw/stats_nba/box_rosters/`.

- [ ] **Step 1: Check capacity and the player-games publication**

```bash
df -h /home
sha256sum data/parquet-large/player_games/_manifest.json    # must equal Task 3's NEW_SHA
```

- [ ] **Step 2: Launch the acquisition (about 16 hours)**

```bash
mkdir -p /home/riz/tmp
setsid nohup .venv/bin/python -m nba_data fetch-box-rosters > /home/riz/tmp/box-rosters-fetch.log 2>&1 &
```

Check progress with `find data/raw/stats_nba/box_rosters -name '*.json' ! -name '*.meta.json' | wc -l` (target 38,366). On a `SourceAccessError`, read the log, wait at least ten minutes, and relaunch the same command; it skips complete pairs.

- [ ] **Step 3: Verify the complete inventory**

```bash
.venv/bin/python - <<'EOF'
from pathlib import Path
from nba_data import box_rosters
index = box_rosters.load_player_games_index(Path("data/parquet-large/player_games"))
pairs = box_rosters.validate_cache_inventory(Path("data"), index.games)
print(len(index.games), len(pairs))
EOF
```

Expected: `19183 38366`.

---

### Task 19: Acquire the injury-report archive, recheck it, and pin the derivative

**Files:** none (raw data only).

**Interfaces:**
- Consumes: Task 8 and Task 11 CLIs.
- Produces: every present PDF archived, every enumerated slot recorded in its month's current generation with two evidence entries on different days when unavailable, and eight derivative assets with sidecars.

- [ ] **Step 1: Launch the first pass (about 13 hours)**

```bash
setsid nohup .venv/bin/python -m nba_data fetch-injury-reports > /home/riz/tmp/injury-reports-fetch.log 2>&1 &
```

Progress: `find data/raw/nba_injury_reports/pdfs -name '*.pdf' | wc -l` (expected on the order of 57,000) and `ls data/raw/nba_injury_reports/inventory | wc -l` (97 months). A failed control stops the run; read the log, confirm the control URLs still serve their known bodies with a single manual `curl -sS -o /dev/null -w '%{http_code}\n' <control url>`, wait at least thirty minutes, relaunch.

- [ ] **Step 2: Pin the derivative**

```bash
.venv/bin/python -m nba_data fetch-injury-derivative
ls -la data/raw/sportsdataverse/nba_stats_injury_report/
```

Expected: eight `injury_report_<season>.parquet` files with sidecars.

- [ ] **Step 3: On a later calendar day, run the recheck pass (about 5 hours)**

```bash
setsid nohup .venv/bin/python -m nba_data fetch-injury-reports --recheck-unavailable > /home/riz/tmp/injury-reports-recheck.log 2>&1 &
```

The summary printed at the end reports `rechecked`, newly `present` slots and `skipped_today` (must be 0 when run on a later day).

- [ ] **Step 4: Verify the inventory state**

```bash
.venv/bin/python - <<'EOF'
from pathlib import Path
from nba_data import injury_reports
present = unavailable = single = 0
for month in sorted({s.month for s in injury_reports.enumerate_slots()}):
    state, damaged = injury_reports.read_current_inventory(Path("data"), month)
    assert state is not None and not damaged, (month, damaged)
    for slot in state["slots"].values():
        if slot["status"] == "present":
            present += 1
        else:
            unavailable += 1
            days = {e["fetched_at"][:10] for e in slot["evidence"]}
            single += len(days) < 2
print(present, unavailable, present + unavailable, single)
EOF
```

Expected: the total is 90,912 and `single` is 0.

---

### Task 20: Parse, review, and stop for the owner's ledger decisions

**Files:**
- Modify (after review only): `src/nba_data/reference/availability_exceptions.json`

**Interfaces:**
- Consumes: Task 16 audit script on the complete raw trees.
- Produces: the reviewed ledger; the gamebook sample list handed to the owner.

- [ ] **Step 1: Run the offline audit (about two hours for the parse pass)**

```bash
OUT=/home/riz/tmp/availability-review-$(date +%Y%m%d)
setsid nohup .venv/bin/python scripts/audit_availability_sources.py --data-dir data --output-dir "$OUT" --gamebook-sample > /home/riz/tmp/availability-audit.log 2>&1 &
```

- [ ] **Step 2: Read the review artifacts and summarise them for the owner**

From `review.md` report: parse-status and title-check counts; every title-check failure and unopenable body with its slot and printed time; placeholder counts by phrase; per-season row and identity resolution rates with the team, month and status breakdowns; unresolved names (top counts) and unresolved matchups by tricode pair; listed-but-not-rostered counts and the non-`Out` list; roster-size outliers and empty inactive lists per season; derivative agreement with disagreement patterns (top patterns with counts) and unmatched versions; appearance and schedule disagreements. Inspect at least three disagreement patterns and three title-check failures against the rendered PDF pages and state, for each, whether ours or the derivative is right.

- [ ] **Step 3: STOP — owner review checkpoint**

Hand the owner: the summary, `ledger_candidates.json` (every observed entry in approved shape, note empty), and `gamebook_sample.json` with one URL per sampled game and the three owner-selected placeholders. Ask the owner to (a) approve, amend or reject each ledger candidate, (b) download the sampled gamebooks in a browser and name a directory on this machine that holds them (the project's notes describe copying from the Riz-PC OneDrive over its WSL login), filling the three owner-selected cases with specific games. Do not edit the ledger or continue to Task 21 until the owner answers. Implementation pauses here even if everything else is green.

- [ ] **Step 4: Record the approved entries**

Write exactly the approved entries into `src/nba_data/reference/availability_exceptions.json` (canonical sorted JSON, each with the owner's `note`), run `.venv/bin/python -m pytest tests/test_availability_build.py -q`, and commit:

```bash
git add src/nba_data/reference/availability_exceptions.json
git commit -m "data: record reviewed availability exceptions

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 21: Gamebook import, name extraction and the roster-sample comparison

**Files:**
- Modify: `src/nba_data/gamebooks.py` (add `extract_box_score_names(payload: bytes) -> dict[str, dict[str, list[str]]]`)
- Create: `tests/fixtures/gamebooks/<game_id>.box_score_page.pdf` (one-page `pypdfium2` excerpt of a downloaded gamebook's box-score page), `tests/fixtures/gamebooks/<game_id>.expected_names.json`
- Test: `tests/test_gamebooks.py`

**Interfaces:**
- Consumes: the owner's downloaded gamebooks; Task 16 names.
- Produces: `data/raw/nba_gamebooks/<game_id>.book.pdf` pairs and `comparisons/<game_id>.json` records for every sampled game; the extractor and its fixture.

- [ ] **Step 1: Import the downloads**

```bash
.venv/bin/python -m nba_data import-gamebooks /path/named/by/owner
ls data/raw/nba_gamebooks/
```

Expected: one `<game_id>.book.pdf` plus sidecar per sampled game; any file that is not a PDF or does not match a worklist game is reported and nothing for it is written.

- [ ] **Step 2: Study the box-score page and write the failing extractor test**

Render the first imported gamebook's pages with `pypdfium2`, locate the box-score page (team headers, player table, `DNP`/inactive lines), and write the page as a one-page excerpt fixture with `pypdfium2.PdfDocument.new()` + `import_pages`. Type `expected_names.json` (`{"<tricode>": {"active": [...], "inactive": [...]}}` as printed) from the picture. Test:

```python
def test_extract_box_score_names_reads_both_teams_from_the_fixture_page()
def test_extract_box_score_names_fails_when_no_box_score_page_is_found()   # a Task 7 injury-report PDF -> GamebookError
```

- [ ] **Step 3: Run the test to verify it fails, implement with `pdfplumber` words, run to pass**

`extract_box_score_names` opens the PDF, finds the page(s) with the two team headers, reads player names from the table rows and the inactive line(s), and returns them as printed. Run: `.venv/bin/python -m pytest tests/test_gamebooks.py -q` → PASS.

- [ ] **Step 4: Compare every sampled game and review the differences**

```bash
.venv/bin/python - <<'EOF'
from pathlib import Path
from nba_data import gamebooks, box_rosters, availability_build
# load the index, assemble Part A rosters for the sampled game ids only (box_rosters.read_cached + normalize_game),
# then for each imported gamebook: names = gamebooks.extract_box_score_names(pdf bytes); record = gamebooks.compare_rosters(game_id, names, rosters)
# print records with differences > 0
EOF
```

For every difference, look at the rendered page, decide whether it is a name-spelling difference, a two-way or inactive-list omission, or a roster error, and set `reviewer_note` accordingly; then write each record with `gamebooks.write_comparison`. A difference without an explanation blocks the release (Task 22 Step 1 checks every record has a non-null `reviewer_note` when `differences > 0`).

- [ ] **Step 5: Commit the code and fixtures (never the raw PDFs)**

```bash
git add src/nba_data/gamebooks.py tests/test_gamebooks.py tests/fixtures/gamebooks/
git commit -m "feat: extract gamebook rosters for the completeness sample

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 22: Build, validate, archive, restore-test, document and verify the release

**Files:**
- Create: `docs/audits/2026-11-<dd>-availability-source-audit.md`
- Modify: `README.md`, `docs/data-dictionary.md`, `tests/test_repository_layout.py`

**Interfaces:**
- Consumes: everything above.
- Produces: the published `data/parquet-large/availability/`, the NAS snapshot, the committed audit and documentation; the branch ready for review.

- [ ] **Step 1: Build and publish (about one day of serial CPU for build plus independent reread)**

```bash
PG_SNAPSHOT=player-games-archive-2026-$(sha256sum data/parquet-large/player_games/_manifest.json | cut -c1-12)
ls -d "/mnt/nas/llm/documents/nba-predictions-archive/$PG_SNAPSHOT"      # the D1 snapshot the manifest will name must exist
setsid nohup .venv/bin/python -m nba_data build-availability > /home/riz/tmp/availability-build.log 2>&1 &
```

Expected on completion: `data/parquet-large/availability/` holds 46 partitions plus `_manifest.json`; the log shows the reread validation passed. A gate failure is reported to the owner with the exact message; it is not worked around.

- [ ] **Step 2: Prove determinism with a second build**

```bash
STAGE=$(mktemp -d /home/riz/tmp/availability-rebuild.XXXXXX)
.venv/bin/python - <<EOF
from pathlib import Path
from nba_data import availability_build
availability_build.build_from_roots(Path("data/raw"), Path("data/parquet-large/player_games"), Path("$STAGE"))
EOF
diff <(cd data/parquet-large/availability && find . -type f -print0 | sort -z | xargs -0 sha256sum) <(cd "$STAGE" && find . -type f -print0 | sort -z | xargs -0 sha256sum) && rm -rf "$STAGE"
```

Expected: no output from `diff`.

- [ ] **Step 3: Archive and restore-test in the documented order**

```bash
scripts/archive_availability_snapshot.sh
AV_SHA=$(sha256sum data/parquet-large/availability/_manifest.json | cut -c1-64)
RESTORE=/home/riz/tmp/availability-restore-$(date +%Y%m%d%H%M%S)
NBA_PLAYER_GAMES_DATA_DIR="$RESTORE" NBA_PLAYER_GAMES_REFERENCE_DIR="$PWD/data/parquet" scripts/restore_player_games_snapshot.sh "$PG_SNAPSHOT"
NBA_AVAILABILITY_DATA_DIR="$RESTORE" scripts/restore_availability_snapshot.sh "availability-archive-2026-${AV_SHA:0:12}"
.venv/bin/python -m nba_data.availability_snapshot validate-layout --raw-root "$RESTORE/raw" --publication-root "$RESTORE/parquet-large/availability" --player-games-root "$RESTORE/parquet-large/player_games" --manifest-sha256 "$AV_SHA"
diff <(cd data && find raw/stats_nba/box_rosters raw/nba_injury_reports raw/sportsdataverse raw/nba_gamebooks parquet-large/availability -type f -print0 | sort -z | xargs -0 sha256sum) \
     <(cd "$RESTORE" && find raw/stats_nba/box_rosters raw/nba_injury_reports raw/sportsdataverse raw/nba_gamebooks parquet-large/availability -type f -print0 | sort -z | xargs -0 sha256sum)
rm -rf "$RESTORE"
```

Expected: both restores succeed, the validator passes with the digest, and the `diff` prints nothing. Record the exact commands and digests for the audit.

- [ ] **Step 4: Write the failing documentation tests**

In `tests/test_repository_layout.py` add, modelled on the player-games equivalents: `test_availability_final_audit_is_present` (asserts `Restore verified: yes`, `Rosters and schedule: ending seasons 2012–2026`, `Injury reports: ending seasons 2019–2026`, a `Snapshot: \`availability-archive-2026-[0-9a-f]{12}\`` line and a `Depends on: \`player-games-archive-2026-[0-9a-f]{12}\`` line), `test_availability_generated_data_paths_remain_ignored_and_untracked` (the four raw roots and `data/parquet-large/availability/`), and `test_availability_documentation_matches_the_delivered_contract` (README and data dictionary name the five commands, the four tables, `scheduled_start_utc`, `report_time_printed_utc_ambiguous`, the recovery order and both environment variables). Run them → FAIL.

- [ ] **Step 5: Write the audit and the documentation**

Render the audit skeleton with `availability_build.render_audit(manifest, snapshot_name=..., restore_verified=True)` and extend it with: the D1 snapshot name and digest it depends on; slot totals per grid with unavailable and recheck counts and control results; parse and title-check outcomes with every ledger-excluded or title-accepted report; placeholder counts by phrase; per-season row and identity resolution rates with the team, month and status breakdowns; listed-but-not-rostered counts; roster-size outliers and the gamebook sample table (game, differences, reviewer note, actual first-period start noted against `scheduled_start_utc` where the gamebook shows it); derivative agreement including reason agreement, every disagreeing and unmatched version and the inspection finding for each pattern inspected; applied ledger entries; the restore-test command sequence and digests; the file-naming correction to the 2026-11-03 spike note. Add a `## Game rosters and official injury reports` section to `README.md` after the player-game logs section and a matching data-dictionary section (the four tables with every column, the resolution rules, the last-version-before-start recipe exactly as the spec's Documentation section words it, the statement that an inactive-list row carries no reason, the statement that `scheduled_start_utc` is the scheduled start, the inventory-generation and unavailable-not-absent semantics, the commands, and the snapshot and recovery contract including the restore order).

- [ ] **Step 6: Run the full suite and the repository verifier**

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/verify_repository.py
git status --short     # nothing under data/raw or data/parquet-large; no fixture over 100 KB
```

Expected: all green; clone size and tracking policy unchanged apart from the committed fixtures (about 250 KB of PDFs and the gamebook page).

- [ ] **Step 7: Commit**

The audit cites `docs/audits/2026-11-03-injury-source-and-current-season-spike.md`; if the owner has not committed that note by now, include it in this commit.

```bash
git add README.md docs/data-dictionary.md docs/audits/2026-11-<dd>-availability-source-audit.md tests/test_repository_layout.py
git commit -m "docs: publish the availability foundation audit and contract

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

Stop on the branch. Integration (merge to `master`) is the owner's separate decision.

---

## Plan Acceptance Mapping

| Spec success criterion | Implemented by |
|---|---|
| 1. 38,366 raw pairs verify; every slot has an outcome in its month's highest verifying generation; every unavailable slot has two evidence entries from different days with verbatim bodies; controls (closing included) returned known bodies; every present PDF archived with sidecar and title check | Tasks 4–5, 7–8, 18–19 |
| 2. `game_rosters` and `game_schedule` cover all 19,183 games with zero unreviewed appearance or schedule disagreements; every `player_games` row is a `played` row; roster sample recorded in bound comparison records with no unexplained difference | Tasks 6, 14–16, 20–22 |
| 3. Every present report parsed or ledger-excluded; team resolution exact; both resolution floors per season; listed-but-not-rostered enumerated; derivative disagreements including reason enumerated and reviewed | Tasks 9–15, 20, 22 |
| 4. Two offline builds byte-identical; independent validation; the regenerated reference manifests and `player_games` change only within the allow-list with every Parquet digest unchanged | Tasks 1–2, 15, 22 |
| 5. NAS snapshot validates and restores into an absent target in the documented order after the D1 snapshot it names, using only documented prerequisites; clone size and tracking policy unchanged | Tasks 3, 17, 22 |
| Dependency decision: full ceremony (archive, restore-verify, audit amendment) | Tasks 2–3 |
| Scheduled-start naming, ambiguity flag, excluded filter in the recipe | Tasks 6, 7, 14, 22 |
| Unavailable-not-absent, controls, recheck, immutable generations, verbatim 403 bodies | Tasks 7–8, 14, 19 |
| Placeholder normalisation and pattern grouping before derivative comparison | Tasks 11, 15 |
| Complete-PDF fixtures with hand-checked expected rows | Tasks 7, 10 |
| Gamebook evidence bound and archived | Tasks 16, 17, 21, 22 |
| Out of scope stays out (no episodes, taxonomy, final-pre-tip status, 2027 feed, actual tip-off, live feed) | Global Constraints; Task 22 documentation |

## Execution Boundary

Implement this plan with `superpowers:subagent-driven-development`: one fresh implementer per task, one task-scoped reviewer after each task, fix/re-review loops for every Critical or Important finding, and one broad whole-branch review after Task 22. Task 20 contains a mandatory stop for the owner's ledger decisions and gamebook downloads; nothing after it starts until the owner has answered. Stop on the unmerged `availability-foundation` branch after verification and review; integration remains a separate explicit owner decision.
