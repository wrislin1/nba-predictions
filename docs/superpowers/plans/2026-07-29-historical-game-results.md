# Historical BAA/NBA Game Results Implementation Plan

## Status — 2026-07-30

**Complete.** Tasks 1–12 are implemented + hardened on
`historical-game-results`; final implementation head `a889c82` passed
independent whole-branch review with no remaining findings.

- ✅ Default verification — 460 tests passed with zero skips.
- ✅ Historical publication — 19,405 regular-season games for ending seasons
  1947–1983; all 37 seasons match independent curated totals.
- ✅ Modern boundary — 49,117 modern games begin at ending season 1984 and
  the combined result panel spans 1947–2026.
- ✅ Reproducibility — modern, historical, and trend products rebuild
  byte-identically.
- ✅ Recovery — the immutable 622-file historical snapshot restores exactly,
  validates against its pinned manifest, and passes its canonical
  `SHA256SUMS` inventory.

### Landing commits

| Area | Commits |
|---|---|
| Source adapter, postseason filtering, and source audit | `d909f6c`, `fdbe938`, `423caa0` |
| Franchise identity and curated references | `a8ecda8`, `9b82fb1`, `4e21651` |
| Assembly and validation gates | `958eadb`, `73a4b82`, `ed96193` |
| Attested publication and full historical collection | `72cc7c6`, `18085f2`, `1155c00`, `f5e6f8e`, `a6541ce`, `ee9d49e`, `08c238a` |
| Independent raw recovery | `de2a9e1`, `66122d1` |
| Long-run analysis and deterministic deliverables | `2be4b91`, `d37b4d4` |
| Documentation and dependency provenance | `ca395c4`, `554ac5d`, `2f8b602`, `cabe2a6` |
| Final review hardening | `5cdf791`, `bba8173`, `9a2123b`, `db19a87`, `5495197`, `b012e51`, `fb0fd28`, `235e3c2`, `8a2cee6`, `4a12bf6`, `a889c82` |

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Completed steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Publish a provenance-complete BAA/NBA regular-season game table for ending seasons 1947–1983 and reproducible home-court trend outputs spanning 1947–2026 without weakening or reshaping the existing modern game products.

**Architecture:** A dedicated Basketball Reference adapter discovers and caches historical monthly schedule pages, and a separate historical builder resolves franchise identity, applies source-backed site corrections, validates all 37 seasons, and transactionally publishes `historical_games.parquet` plus its own manifest. A pure analysis module unions only compatible result fields from the historical and modern tables; a thin report script creates the season summary, long-run figure, and written context for the later COVID analysis.

**Tech Stack:** Python 3.12, pandas 3.0.5, NumPy, pyarrow, requests 2.34.2, lxml, matplotlib, pytest 9, Bash, jq, rsync, SHA-256.

## Global Constraints

- Repository: `/home/riz/dev/nba-predictions-revived`; run every command from this root unless a worktree path is explicitly shown.
- Begin execution by invoking `superpowers:using-git-worktrees`; create branch `historical-game-results` in `.worktrees/historical-game-results` from commit `66fc4e8`.
- CPU only; no task allocates GPU memory.
- Season is always the ending year: `1947` means 1946-47 and `1983` means 1982-83.
- Scope is BAA regular seasons ending 1947–1949 and NBA regular seasons ending 1950–1983. Exclude ABA, playoffs, exhibitions, and All-Star games.
- The source spike must cover ending seasons 1947, 1960, and 1983 before the all-season fetch.
- Basketball Reference access uses ordinary TLS verification, the browser-identifying User-Agent already used by `nba_data.bref`, one request no more often than every 12 seconds, no parallel requests, and immediate stop on HTTP, TLS, rate-limit, block-page, or schema failure.
- `fetch-historical` is the only historical network path. Tests and `build-historical` are offline-only.
- Raw historical HTML and JSON sidecars live only under gitignored `data/raw/historical_bref/`.
- Cache bodies and sidecars are atomically published through `nba_data.cache`; a missing half, checksum mismatch, metadata mismatch, duplicate resource identity, or unreferenced file fails the build.
- Pre-1984 rows never receive invented modern box-score values and never enter `games.parquet` or `team_games.parquet`.
- Historical products are exactly `data/parquet/historical_games.parquet` and `data/parquet/historical_games_manifest.json`.
- Existing `games.parquet` and `team_games.parquet` schemas, row sets, ordering, and hashes must not change during the one-time franchise-dimension rebuild.
- `data/reference/franchises.csv` and `src/nba_data/reference/franchises.csv` remain byte-identical.
- Blank historical attendance and venue fields remain null; attendance zero is preserved as reported zero.
- Primary home-court summaries use only `site_status == "home"`; neutral and unknown games remain published and are counted separately.
- No early-season attendance or venue coverage minimum is allowed.
- Historical row ordering is deterministic: `season, game_date, game_id`.
- Unit tests use committed small HTML fixtures and never perform network requests. Integration tests consume verified local caches only.
- Two consecutive offline historical builds and two consecutive analysis renders must be byte-identical.
- The historical raw-cache snapshot is separate from the Slice 2A raw
  snapshot. Archive and restore must bind the exact cache body paths and
  hashes plus every sidecar identity to the trusted committed historical
  manifest before and after staging, defend against copy-time changes, and
  also verify a complete `SHA256SUMS` inventory.
- Follow Sports Reference's bot-traffic and data-use policies linked in the approved design; do not add a TLS bypass, CAPTCHA workaround, proxy rotation, or retry hammer.

---

## File and Responsibility Map

```text
src/nba_data/historical_bref.py        source discovery, fetch, parse, cache load
src/nba_data/historical_build.py       references, gates, provenance, publication
src/nba_data/home_court.py             union, intervals, season summaries, trends
src/nba_data/__main__.py               fetch-historical/build-historical dispatch
src/nba_data/build.py                  preserve independent historical products
data/reference/franchises.csv          season-aware surviving/defunct lineage
data/reference/historical_season_counts.csv
                                        independent 1947..1983 count gate
data/reference/historical_site_corrections.csv
                                        source-backed site corrections
tests/fixtures/historical_bref/        three small era-boundary source fixtures
tests/test_historical_bref.py          source/parser/cache behavior
tests/test_historical_build.py         schema/gates/provenance/rollback
tests/test_home_court.py               union and statistical calculations
tests/test_historical_report.py        deterministic rendered products
tests/test_historical_raw_snapshot.py  archive/restore inverse
scripts/audit_historical_source.py     offline spike audit
scripts/archive_historical_raw_snapshot.sh
scripts/restore_historical_raw.sh      independent historical recovery
analysis/covid_hca/build_historical_trend.py
                                        reproducible output writer
README.md
docs/data-dictionary.md
analysis/covid_hca/README.md            operator and interpretation docs
```

## Execution Sequence

Execute strictly by task number:

1. historical source adapter and CLI;
2. three-season source spike;
3. franchise identity;
4. independent count/correction references;
5. frame assembly and gates;
6. provenance and publication;
7. full collection and published data;
8. raw recovery;
9. analysis primitives;
10. rendered trend products;
11. documentation; and
12. acceptance review.

The detailed Task 1–3 definitions and output contract appear in the
“Source adapter contract and foundation tasks” section below; Tasks 4–7 are
listed first only because they share the publication contract. The numeric
sequence above is authoritative.

## Detailed Output Contracts

```text
src/nba_data/historical_bref.py
    Historical season/league rules, schedule-index discovery, polite fetch,
    stat-keyed HTML parsing, stable source IDs, and verified cache loading.

src/nba_data/historical_build.py
    Curated-reference loading, franchise resolution, site corrections,
    publication gates, raw provenance, manifest creation, and two-file
    transactional publication.

src/nba_data/home_court.py
    Pure historical/modern result union, Wilson intervals, fixed-seed
    franchise-cluster bootstrap, season aggregation, and trailing trends.

src/nba_data/__main__.py
    `fetch-historical` and `build-historical` CLI dispatch.

src/nba_data/build.py
    Preserve a complete historical publication pair when the modern
    publication directory is atomically replaced.

data/reference/franchises.csv
src/nba_data/reference/franchises.csv
    Season-bounded source aliases for surviving and defunct BAA/NBA teams.

data/reference/historical_season_counts.csv
    Independently curated regular-season totals for endings 1947–1983.

data/reference/historical_site_corrections.csv
    Optional game-keyed `home`/`neutral` corrections with direct provenance.

docs/audits/2026-07-29-historical-bref-source-spike.md
    Generated three-season source-spike inventory and ambiguity decisions.

tests/fixtures/historical_bref/
    Small index/month fragments for 1947, 1960, and 1983.

tests/test_historical_bref.py
    Discovery, access policy, parser, stable-ID, and cached-season tests.

tests/test_historical_build.py
    Reference, correction, schema, gate, provenance, determinism, boundary,
    and failed-publication rollback tests.

tests/test_home_court.py
    Union orientation, site exclusion, intervals, aggregation, and rolling
    weighting tests.

tests/test_historical_report.py
    Deterministic CSV, PNG, and Markdown rendering tests.

tests/test_historical_raw_snapshot.py
    Historical archive/restore inverse and tamper-rejection tests.

tests/test_cli.py
tests/test_franchises.py
tests/test_build_gates.py
tests/test_repository_layout.py
    Integration with existing CLI, identity, modern publisher, and layout.

scripts/audit_historical_source.py
    Offline cache audit that writes the source-spike Markdown document.

scripts/archive_historical_raw_snapshot.sh
scripts/restore_historical_raw.sh
    Verified NAS snapshot and exact restore for `historical_bref` only.

scripts/archive_raw_snapshot.sh
    Exclude the independently attested historical cache from future modern
    Slice 2A snapshots.

analysis/covid_hca/build_historical_trend.py
    Thin deterministic writer for the historical context deliverables.

analysis/covid_hca/data/home_court_by_season.csv
analysis/covid_hca/figures/home_court_trend_1947_2026.png
analysis/covid_hca/historical_trend.md
    Committed reproducible analysis outputs.

README.md
docs/data-dictionary.md
analysis/covid_hca/README.md
    User-facing source, schema, rebuild, limitation, and interpretation docs.
```

### Historical publication contract

`historical_games.parquet` has these columns and pandas dtypes:

```text
game_id string
season int64
league string
game_date object containing datetime.date
away_team_name string
home_team_name string
away_franchise_id string
home_franchise_id string
away_pts int64
home_pts int64
home_margin int64
overtime_periods int64
site_status string
venue_name string nullable
attendance Int64 nullable
attendance_status string
boxscore_url string nullable
schedule_url string
```

`historical_games_manifest.json` has this stable top-level shape:

```json
{
  "schema_version": 1,
  "sources": {"basketball_reference": {"pages": []}},
  "references": {},
  "versions": {},
  "tables": {"historical_games": {}},
  "validation": {}
}
```

The `tables.historical_games` entry must also contain `schema`: the exact
ordered 18-column Arrow schema listed above, represented as
`{"name": <column>, "arrow_type": <physical type>}` objects. Publication tests
compare the complete ordered list, not merely its length or a set of names.

The parser may carry `overtime_marker` and `game_remarks` internally. Those
fields are validation/classification evidence and are not published.

---

### Task 1: Historical Basketball Reference adapter and CLI fetch path

**Files:**
- Create: `src/nba_data/historical_bref.py`
- Create: `tests/test_historical_bref.py`
- Create: `tests/fixtures/historical_bref/BAA_1947_games.html`
- Create: `tests/fixtures/historical_bref/BAA_1947_games-november.html`
- Create: `tests/fixtures/historical_bref/NBA_1960_games.html`
- Create: `tests/fixtures/historical_bref/NBA_1960_games-october.html`
- Create: `tests/fixtures/historical_bref/NBA_1983_games.html`
- Create: `tests/fixtures/historical_bref/NBA_1983_games-october.html`
- Modify: `src/nba_data/__main__.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `cache.read_valid(path: Path, validator) -> bytes | None`;
  `cache.require_valid(path: Path, validator) -> bytes`;
  `cache.publish_bytes(path: Path, payload: bytes, metadata: dict, validator) -> Path`;
  `bref.USER_AGENT: str`.
- Produces: `HISTORICAL_SEASONS: tuple[int, ...]`;
  `league_for_season(season: int) -> str`;
  `discover_month_pages(payload: bytes, season: int) -> tuple[HistoricalPage, ...]`;
  `fetch(raw_dir: Path, seasons: Iterable[int] = HISTORICAL_SEASONS, sleep_s: float = 12) -> None`;
  `parse_schedule_page(payload: bytes, *, season: int, schedule_url: str) -> pd.DataFrame`;
  `load_season(raw_dir: Path, season: int) -> pd.DataFrame`.

- [x] **Step 1: Commit exact small source fixtures**

Use stat-keyed HTML instead of positional columns. The 1947 month fixture must
contain a regulation row with blank attendance, an overtime row with positive
attendance, and a no-boxscore row:

```html
<table id="schedule">
  <thead><tr>
    <th data-stat="date_game">Date</th>
    <th data-stat="visitor_team_name">Visitor/Neutral</th>
    <th data-stat="visitor_pts">PTS</th>
    <th data-stat="home_team_name">Home/Neutral</th>
    <th data-stat="home_pts">PTS</th>
    <th data-stat="box_score_text">Box</th>
    <th data-stat="overtimes">OT</th>
    <th data-stat="attendance">Attend.</th>
    <th data-stat="arena_name">Arena</th>
    <th data-stat="game_remarks">Notes</th>
  </tr></thead>
  <tbody>
    <tr>
      <th data-stat="date_game">Fri, Nov 1, 1946</th>
      <td data-stat="visitor_team_name">New York Knicks</td>
      <td data-stat="visitor_pts">68</td>
      <td data-stat="home_team_name">Toronto Huskies</td>
      <td data-stat="home_pts">66</td>
      <td data-stat="box_score_text"><a href="/boxscores/194611010TRH.html">Box Score</a></td>
      <td data-stat="overtimes"></td>
      <td data-stat="attendance"></td>
      <td data-stat="arena_name">Maple Leaf Gardens</td>
      <td data-stat="game_remarks"></td>
    </tr>
    <tr>
      <th data-stat="date_game">Mon, Nov 11, 1946</th>
      <td data-stat="visitor_team_name">Chicago Stags</td>
      <td data-stat="visitor_pts">78</td>
      <td data-stat="home_team_name">New York Knicks</td>
      <td data-stat="home_pts">68</td>
      <td data-stat="box_score_text"><a href="/boxscores/194611110NYK.html">Box Score</a></td>
      <td data-stat="overtimes">OT</td>
      <td data-stat="attendance">4,329</td>
      <td data-stat="arena_name">Madison Square Garden (III)</td>
      <td data-stat="game_remarks"></td>
    </tr>
    <tr>
      <th data-stat="date_game">Tue, Nov 12, 1946</th>
      <td data-stat="visitor_team_name">Boston Celtics</td>
      <td data-stat="visitor_pts">55</td>
      <td data-stat="home_team_name">Providence Steamrollers</td>
      <td data-stat="home_pts">57</td>
      <td data-stat="box_score_text"></td>
      <td data-stat="overtimes"></td>
      <td data-stat="attendance">0</td>
      <td data-stat="arena_name"></td>
      <td data-stat="game_remarks">site not stated</td>
    </tr>
  </tbody>
</table>
```

The three index fixtures use the real league/season filenames and explicitly
include a playoff link that discovery must reject:

```html
<html><body>
  <a href="/leagues/BAA_1947_games-november.html">November</a>
  <a href="/leagues/BAA_1947_games-december.html">December</a>
  <a href="/leagues/BAA_1947_games-playoffs.html">Playoffs</a>
</body></html>
```

Create the 1960 index with
`/leagues/NBA_1960_games-october.html`,
`/leagues/NBA_1960_games-november.html`, and a rejected
`/leagues/NBA_1960_games-playoffs.html` link. Its October table uses the same
ten `data-stat` headers shown above and these two body rows:

```html
<tr>
  <th data-stat="date_game">Sat, Oct 17, 1959</th>
  <td data-stat="visitor_team_name">Syracuse Nationals</td>
  <td data-stat="visitor_pts">100</td>
  <td data-stat="home_team_name">Minneapolis Lakers</td>
  <td data-stat="home_pts">105</td>
  <td data-stat="box_score_text"><a href="/boxscores/195910170MNL.html">Box Score</a></td>
  <td data-stat="overtimes"></td>
  <td data-stat="attendance">8,000</td>
  <td data-stat="arena_name">Minneapolis Auditorium</td>
  <td data-stat="game_remarks"></td>
</tr>
<tr>
  <th data-stat="date_game">Sun, Oct 18, 1959</th>
  <td data-stat="visitor_team_name">Boston Celtics</td>
  <td data-stat="visitor_pts">112</td>
  <td data-stat="home_team_name">St. Louis Hawks</td>
  <td data-stat="home_pts">110</td>
  <td data-stat="box_score_text"><a href="/boxscores/195910180STL.html">Box Score</a></td>
  <td data-stat="overtimes">2OT</td>
  <td data-stat="attendance"></td>
  <td data-stat="arena_name">Kiel Auditorium</td>
  <td data-stat="game_remarks">Neutral Site</td>
</tr>
```

Create the 1983 index with October, November, and rejected playoff links.
Its October table uses the same headers and these rows:

```html
<tr>
  <th data-stat="date_game">Fri, Oct 29, 1982</th>
  <td data-stat="visitor_team_name">Golden State Warriors</td>
  <td data-stat="visitor_pts">104</td>
  <td data-stat="home_team_name">Los Angeles Lakers</td>
  <td data-stat="home_pts">110</td>
  <td data-stat="box_score_text"><a href="/boxscores/198210290LAL.html">Box Score</a></td>
  <td data-stat="overtimes"></td>
  <td data-stat="attendance">17,505</td>
  <td data-stat="arena_name">The Forum</td>
  <td data-stat="game_remarks"></td>
</tr>
<tr>
  <th data-stat="date_game">Sat, Oct 30, 1982</th>
  <td data-stat="visitor_team_name">Kansas City Kings</td>
  <td data-stat="visitor_pts">101</td>
  <td data-stat="home_team_name">Denver Nuggets</td>
  <td data-stat="home_pts">103</td>
  <td data-stat="box_score_text"><a href="/boxscores/198210300DEN.html">Box Score</a></td>
  <td data-stat="overtimes">2OT</td>
  <td data-stat="attendance"></td>
  <td data-stat="arena_name">McNichols Sports Arena</td>
  <td data-stat="game_remarks"></td>
</tr>
```

Keep each complete fixture below 8 KiB.

Add:

```python
def test_parser_accepts_only_the_explicit_neutral_marker():
    result = historical_bref.parse_schedule_page(
        (FIXTURES / "NBA_1960_games-october.html").read_bytes(),
        season=1960,
        schedule_url=(
            "https://www.basketball-reference.com/leagues/"
            "NBA_1960_games-october.html"
        ),
    )
    assert result["site_status"].tolist() == ["home", "neutral"]
```

- [x] **Step 2: Write failing discovery, parser, and cache tests**

```python
from pathlib import Path

import pandas as pd
import pytest

from nba_data import cache, historical_bref


FIXTURES = Path(__file__).parent / "fixtures/historical_bref"


@pytest.mark.parametrize(
    ("season", "league"),
    [(1947, "BAA"), (1949, "BAA"), (1950, "NBA"), (1983, "NBA")],
)
def test_league_for_season_uses_ending_year(season, league):
    assert historical_bref.league_for_season(season) == league


def test_discovery_accepts_month_pages_and_rejects_playoffs():
    pages = historical_bref.discover_month_pages(
        (FIXTURES / "BAA_1947_games.html").read_bytes(),
        1947,
    )
    assert [page.month for page in pages] == ["november", "december"]
    assert all("playoffs" not in page.url for page in pages)


def test_parser_preserves_missing_zero_and_overtime_semantics():
    result = historical_bref.parse_schedule_page(
        (FIXTURES / "BAA_1947_games-november.html").read_bytes(),
        season=1947,
        schedule_url=(
            "https://www.basketball-reference.com/leagues/"
            "BAA_1947_games-november.html"
        ),
    )
    assert pd.isna(result.iloc[0]["attendance"])
    assert result.iloc[1]["attendance"] == 4329
    assert result["attendance_status"].tolist() == [
        "missing", "reported_positive", "reported_zero"
    ]
    assert result["overtime_periods"].tolist() == [0, 1, 0]
    assert result["site_status"].tolist() == ["home", "home", "unknown"]
    assert result.iloc[0]["game_id"] == "bref-194611010TRH"
    assert result.iloc[2]["game_id"].startswith(
        "bref-fallback-19461112-boston-celtics-at-providence-steamrollers-"
    )


@pytest.mark.parametrize("href", ["", "   ", "/games/not-a-boxscore.html"])
def test_parser_rejects_present_invalid_boxscore_href(href):
    payload = (
        FIXTURES / "BAA_1947_games-november.html"
    ).read_text(encoding="utf-8").replace(
        "/boxscores/194611010TRH.html", href, 1
    )
    with pytest.raises(ValueError, match="invalid box-score href"):
        historical_bref.parse_schedule_page(
            payload.encode(),
            season=1947,
            schedule_url=(
                "https://www.basketball-reference.com/leagues/"
                "BAA_1947_games-november.html"
            ),
        )


def test_parser_allows_absent_boxscore_href():
    result = historical_bref.parse_schedule_page(
        (FIXTURES / "BAA_1947_games-november.html").read_bytes(),
        season=1947,
        schedule_url=(
            "https://www.basketball-reference.com/leagues/"
            "BAA_1947_games-november.html"
        ),
    )
    assert pd.isna(result.iloc[2]["boxscore_url"])
    assert result.iloc[2]["game_id"].startswith("bref-fallback-")
```

- [x] **Step 3: Run the focused tests and confirm the red state**

Run:

```bash
.venv/bin/python -m pytest tests/test_historical_bref.py -v
```

Expected: collection fails because `nba_data.historical_bref` does not exist.

- [x] **Step 4: Implement season rules, discovery, and stat-keyed parsing**

Create the module with these constants and value objects:

```python
from dataclasses import dataclass
import datetime
import re
import time
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

from lxml import html
import pandas as pd
import requests

from nba_data import bref, cache


BASE_URL = "https://www.basketball-reference.com"
HISTORICAL_SEASONS = tuple(range(1947, 1984))
CALENDAR_MONTHS = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)
REQUIRED_STATS = frozenset({
    "date_game",
    "visitor_team_name",
    "visitor_pts",
    "home_team_name",
    "home_pts",
    "box_score_text",
    "overtimes",
})
OVERTIME_PATTERN = re.compile(r"^(?:(\d+)OT|OT)?$")
BOXSCORE_PATTERN = re.compile(r"^/boxscores/(\d{9}[A-Z0-9]{3})\.html$")
EXPLICIT_NEUTRAL_REMARKS = frozenset({"neutral site"})


@dataclass(frozen=True, order=True)
class HistoricalPage:
    month: str
    url: str
    filename: str


def league_for_season(season: int) -> str:
    if season not in HISTORICAL_SEASONS:
        raise ValueError(f"historical season must be 1947..1983: {season}")
    return "BAA" if season <= 1949 else "NBA"


def index_filename(season: int) -> str:
    return f"{league_for_season(season)}_{season}_games.html"


def index_url(season: int) -> str:
    return f"{BASE_URL}/leagues/{index_filename(season)}"


def _document(payload: bytes, label: str):
    try:
        return html.fromstring(payload)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{label}: invalid HTML") from exc


def discover_month_pages(
    payload: bytes,
    season: int,
) -> tuple[HistoricalPage, ...]:
    league = league_for_season(season)
    root = _document(payload, index_url(season))
    pattern = re.compile(
        rf"^/leagues/{league}_{season}_games-([a-z]+)\.html$"
    )
    pages = {}
    for href in root.xpath("//a/@href"):
        match = pattern.fullmatch(str(href))
        if match is None:
            continue
        month = match.group(1)
        if month not in CALENDAR_MONTHS:
            continue
        page = HistoricalPage(
            month=month,
            url=urljoin(BASE_URL, str(href)),
            filename=f"{league}_{season}_games-{month}.html",
        )
        pages[page.url] = page
    if not pages:
        raise ValueError(
            f"{index_url(season)}: no regular-season month links"
        )
    return tuple(pages.values())
```

Parse cells by `data-stat`, not by table position:

```python
def _cell_text(row, stat: str) -> str:
    cells = row.xpath(f"./th[@data-stat='{stat}'] | ./td[@data-stat='{stat}']")
    if not cells:
        return ""
    return " ".join("".join(cells[0].itertext()).split())


def _cell_href(row, stat: str) -> str | None:
    hrefs = row.xpath(
        f"./th[@data-stat='{stat}']//a/@href | "
        f"./td[@data-stat='{stat}']//a/@href"
    )
    return str(hrefs[0]) if hrefs else None


def _overtime_periods(marker: str) -> int:
    normalized = marker.strip().upper()
    match = OVERTIME_PATTERN.fullmatch(normalized)
    if match is None:
        raise ValueError(f"unsupported overtime marker: {marker!r}")
    if not normalized:
        return 0
    return int(match.group(1) or 1)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _attendance(value: str) -> tuple[int | None, str]:
    if not value.strip():
        return None, "missing"
    parsed = int(value.replace(",", ""))
    return parsed, "reported_zero" if parsed == 0 else "reported_positive"


def parse_schedule_page(
    payload: bytes,
    *,
    season: int,
    schedule_url: str,
) -> pd.DataFrame:
    league = league_for_season(season)
    root = _document(payload, schedule_url)
    tables = root.xpath("//table[@id='schedule']")
    if len(tables) != 1:
        raise ValueError(
            f"{schedule_url}: expected one schedule table, found {len(tables)}"
        )
    stats = set(tables[0].xpath(".//*[@data-stat]/@data-stat"))
    missing = sorted(REQUIRED_STATS - stats)
    if missing:
        raise ValueError(f"{schedule_url}: missing schedule stats {missing}")
    records = []
    for source_row, row in enumerate(tables[0].xpath("./tbody/tr"), start=1):
        away_pts = _cell_text(row, "visitor_pts")
        home_pts = _cell_text(row, "home_pts")
        if not away_pts and not home_pts:
            continue
        box_href = _cell_href(row, "box_score_text")
        box_match = None
        if box_href is not None:
            box_match = BOXSCORE_PATTERN.fullmatch(box_href)
            if box_match is None:
                raise ValueError(
                    f"{schedule_url}: source row {source_row} has invalid "
                    f"box-score href {box_href!r}"
                )
        attendance, attendance_status = _attendance(
            _cell_text(row, "attendance")
        )
        remarks = _cell_text(row, "game_remarks")
        normalized_remarks = remarks.casefold()
        site_status = (
            "home"
            if not remarks
            else (
                "neutral"
                if normalized_remarks in EXPLICIT_NEUTRAL_REMARKS
                else "unknown"
            )
        )
        records.append({
            "season": season,
            "league": league,
            "game_date": pd.to_datetime(
                _cell_text(row, "date_game"), errors="raise"
            ).date(),
            "away_team_name": _cell_text(row, "visitor_team_name"),
            "home_team_name": _cell_text(row, "home_team_name"),
            "away_pts": int(away_pts),
            "home_pts": int(home_pts),
            "overtime_marker": _cell_text(row, "overtimes"),
            "overtime_periods": _overtime_periods(
                _cell_text(row, "overtimes")
            ),
            "site_status": site_status,
            "venue_name": _cell_text(row, "arena_name") or pd.NA,
            "attendance": attendance,
            "attendance_status": attendance_status,
            "boxscore_url": (
                urljoin(BASE_URL, box_href) if box_match else pd.NA
            ),
            "boxscore_slug": box_match.group(1) if box_match else pd.NA,
            "schedule_url": schedule_url,
            "game_remarks": remarks,
            "source_row": source_row,
        })
    frame = pd.DataFrame(records)
    return _assign_game_ids(frame)
```

`_assign_game_ids` sorts by date, source URL, and source row; box-score rows use
`bref-<slug>`. Only rows with no href attribute use
`bref-fallback-YYYYMMDD-<away>-at-<home>-NN`, where `NN` is the one-based
ordinal within the exact date/away/home group. Return nullable text as pandas
`string` and attendance as `Int64`.

Use this complete ID/type normalization:

```python
def _assign_game_ids(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.sort_values(
        ["game_date", "schedule_url", "source_row"],
        kind="stable",
    ).reset_index(drop=True)
    ordinals = (
        frame.groupby(
            ["game_date", "away_team_name", "home_team_name"],
            sort=False,
        )
        .cumcount()
        .add(1)
    )
    fallback_ids = [
        (
            f"bref-fallback-{date:%Y%m%d}-{_slug(away)}"
            f"-at-{_slug(home)}-{int(ordinal):02d}"
        )
        for date, away, home, ordinal in zip(
            frame["game_date"],
            frame["away_team_name"],
            frame["home_team_name"],
            ordinals,
            strict=True,
        )
    ]
    frame["game_id"] = pd.Series(fallback_ids, dtype="string")
    has_boxscore = frame["boxscore_slug"].notna()
    frame.loc[has_boxscore, "game_id"] = (
        "bref-" + frame.loc[has_boxscore, "boxscore_slug"].astype("string")
    )
    for column in (
        "league",
        "away_team_name",
        "home_team_name",
        "overtime_marker",
        "site_status",
        "venue_name",
        "attendance_status",
        "boxscore_url",
        "schedule_url",
        "game_remarks",
    ):
        frame[column] = frame[column].astype("string")
    frame["attendance"] = frame["attendance"].astype("Int64")
    return frame
```

- [x] **Step 5: Add strict index/month validators and polite cache fetch**

```python
def validate_index(path: Path, season: int) -> None:
    discover_month_pages(path.read_bytes(), season)


def validate_month(path: Path, season: int, url: str) -> None:
    parse_schedule_page(path.read_bytes(), season=season, schedule_url=url)


def _request(session: requests.Session, url: str) -> bytes:
    try:
        response = session.get(url, timeout=30, verify=True)
    except requests.RequestException as exc:
        raise cache.SourceAccessError(
            f"{url} failed: {type(exc).__name__}: {exc}"
        ) from exc
    if response.status_code != 200:
        raise cache.SourceAccessError(
            f"{url} returned HTTP {response.status_code}"
        )
    return response.content


def fetch(
    raw_dir: Path,
    seasons: Iterable[int] = HISTORICAL_SEASONS,
    sleep_s: float = 12,
) -> None:
    cache_dir = raw_dir / "historical_bref"
    session = requests.Session()
    session.headers.update({"User-Agent": bref.USER_AGENT})
    requested = False

    def obtain(path: Path, url: str, metadata: dict, validator) -> bytes:
        nonlocal requested
        cached = cache.read_valid(path, validator)
        if cached is not None:
            return cached
        if requested:
            time.sleep(sleep_s)
        payload = _request(session, url)
        cache.publish_bytes(path, payload, metadata, validator)
        requested = True
        return payload

    for season in tuple(seasons):
        league = league_for_season(season)
        index_path = cache_dir / index_filename(season)
        page_payload = obtain(
            index_path,
            index_url(season),
            {
                "provider": "basketball_reference",
                "resource": "historical_schedule_index",
                "league": league,
                "season": season,
                "url": index_url(season),
            },
            lambda path, value=season: validate_index(path, value),
        )
        for page in discover_month_pages(page_payload, season):
            destination = cache_dir / page.filename
            obtain(
                destination,
                page.url,
                {
                    "provider": "basketball_reference",
                    "resource": "historical_schedule_month",
                    "league": league,
                    "season": season,
                    "month": page.month,
                    "url": page.url,
                },
                lambda path, value=season, url=page.url: validate_month(
                    path, value, url
                ),
            )
```

`load_season` requires the verified index, rediscovers its expected month set,
requires every verified month page, concatenates the parsed rows, assigns
fallback IDs again across the complete season, and returns a stable
`game_date, game_id` sort. It raises `cache.CacheIntegrityError` for a missing
cache pair and never opens the network.

```python
def load_season(raw_dir: Path, season: int) -> pd.DataFrame:
    cache_dir = raw_dir / "historical_bref"
    index_path = cache_dir / index_filename(season)
    payload = cache.require_valid(
        index_path,
        lambda path: validate_index(path, season),
    )
    frames = []
    for page in discover_month_pages(payload, season):
        month_payload = cache.require_valid(
            cache_dir / page.filename,
            lambda path, value=season, url=page.url: validate_month(
                path, value, url
            ),
        )
        frames.append(
            parse_schedule_page(
                month_payload,
                season=season,
                schedule_url=page.url,
            )
        )
    combined = pd.concat(frames, ignore_index=True)
    combined = _assign_game_ids(combined.drop(columns=["game_id"]))
    return combined.sort_values(
        ["game_date", "game_id"], kind="stable"
    ).reset_index(drop=True)
```

- [x] **Step 6: Add fetch error/cadence/cache tests**

Use the `FakeResponse`/`FakeSession` pattern from `tests/test_bref.py`. Assert:

```python
assert fake_session.calls[0][1:] == (30, True)
assert fake_session.headers["User-Agent"] == historical_bref.bref.USER_AGENT
assert sleeps == [0.25]
assert not any(path.name.endswith(".part") for path in cache_dir.glob("*"))
```

Cover these exact cases in named tests:

```text
test_fetch_caches_index_before_discovering_months
test_fetch_sleeps_only_between_real_requests_across_seasons
test_fetch_cache_hits_make_no_requests_and_do_not_sleep
test_fetch_stops_on_tls_error_without_publishing
test_fetch_rejects_block_page_without_publishing
test_load_season_rejects_missing_month_pair
test_load_season_rejects_checksum_mismatch
test_fallback_ids_are_stable_across_input_order
test_parser_rejects_unknown_overtime_marker
```

- [x] **Step 7: Add the two historical CLI commands**

Modify parser construction and dispatch:

```python
from nba_data import (
    bref,
    build,
    historical_bref,
    historical_build,
    hoopr,
    stats_nba,
)

historical_fetch = commands.add_parser("fetch-historical")
historical_fetch.add_argument(
    "--season",
    type=int,
    action="append",
    choices=historical_bref.HISTORICAL_SEASONS,
)
commands.add_parser("build-historical")
```

Use repeated `--season` values for the spike and all seasons when omitted:

```python
elif args.command == "fetch-historical":
    selected = (
        tuple(args.season)
        if args.season
        else historical_bref.HISTORICAL_SEASONS
    )
    historical_bref.fetch(raw, selected)
elif args.command == "build-historical":
    historical_build.run(data)
```

Create `src/nba_data/historical_build.py` initially with a deliberate
pre-publication error so CLI imports are valid before Task 6:

```python
from pathlib import Path


def run(data_dir: Path) -> None:
    raise RuntimeError(
        "historical publication is unavailable until its gates are installed"
    )
```

Add CLI tests asserting `fetch-historical --season 1947 --season 1960
--season 1983` calls only `historical_bref.fetch(raw_dir, (1947, 1960, 1983))`,
`fetch-historical` defaults to all 37 seasons, `build-historical` calls
`historical_build.run(data_dir)`, and modern `fetch` still calls only its
three existing providers.

- [x] **Step 8: Run tests and commit the adapter**

```bash
.venv/bin/python -m pytest tests/test_historical_bref.py tests/test_cli.py -v
.venv/bin/python -m pytest tests/test_bref.py tests/test_cache.py -q
git add src/nba_data/historical_bref.py src/nba_data/historical_build.py src/nba_data/__main__.py tests/test_historical_bref.py tests/test_cli.py tests/fixtures/historical_bref
git commit -m "feat: add historical schedule fetch adapter"
```

Expected: all focused tests pass and no test performs a real request.

---

### Task 2: Three-season online source spike and generated audit

**Files:**
- Create: `scripts/audit_historical_source.py`
- Create: `docs/audits/2026-07-29-historical-bref-source-spike.md`
- Modify: `tests/test_historical_bref.py`

**Interfaces:**
- Consumes: `historical_bref.fetch(...)`;
  `historical_bref.load_season(raw_dir: Path, season: int) -> pd.DataFrame`.
- Produces: `audit_season(raw_dir: Path, season: int) -> dict`;
  `render_markdown(audits: list[dict]) -> str`; a committed source audit whose
  values are regenerated from checksum-valid local caches.

- [x] **Step 1: Write the failing offline audit test**

`scripts/` is not a package; load the audit module by file path (the same
`parents[1]` convention `tests/test_verify_repository.py` uses for script
paths). Add to `tests/test_historical_bref.py`:

```python
import importlib.util

_AUDIT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts/audit_historical_source.py"
)
_AUDIT_SPEC = importlib.util.spec_from_file_location(
    "audit_historical_source", _AUDIT_PATH
)
audit_historical_source = importlib.util.module_from_spec(_AUDIT_SPEC)
_AUDIT_SPEC.loader.exec_module(audit_historical_source)
```

Add a helper that publishes a complete verified one-month fixture season
through the real cache utilities. It builds its own single-month index so
`load_season`'s month-completeness requirement is satisfied with only the
committed month fixture (the Task 1 two-month index fixture stays dedicated
to discovery tests):

```python
FIXTURE_SPIKE_MONTH = {1947: "november", 1960: "october", 1983: "october"}


def publish_historical_fixture_season(raw_dir, *, season):
    league = historical_bref.league_for_season(season)
    month = FIXTURE_SPIKE_MONTH[season]
    cache_dir = raw_dir / "historical_bref"
    month_name = f"{league}_{season}_games-{month}.html"
    index_payload = (
        f'<html><body><a href="/leagues/{month_name}">'
        f"{month.title()}</a></body></html>"
    ).encode()
    cache.publish_bytes(
        cache_dir / historical_bref.index_filename(season),
        index_payload,
        {
            "provider": "basketball_reference",
            "resource": "historical_schedule_index",
            "league": league,
            "season": season,
            "url": historical_bref.index_url(season),
        },
        lambda path: historical_bref.validate_index(path, season),
    )
    month_url = f"{historical_bref.BASE_URL}/leagues/{month_name}"
    cache.publish_bytes(
        cache_dir / month_name,
        (FIXTURES / month_name).read_bytes(),
        {
            "provider": "basketball_reference",
            "resource": "historical_schedule_month",
            "league": league,
            "season": season,
            "month": month,
            "url": month_url,
        },
        lambda path: historical_bref.validate_month(path, season, month_url),
    )
```

Then add the failing test:

```python
def test_audit_reports_source_shape_and_ambiguities(tmp_path):
    publish_historical_fixture_season(tmp_path, season=1947)
    audit = audit_historical_source.audit_season(tmp_path, 1947)
    assert audit["season"] == 1947
    assert audit["league"] == "BAA"
    assert audit["games"] == 3
    assert audit["boxscore_links"] == 2
    assert audit["attendance"]["missing"] == 1
    assert audit["site_status"]["unknown"] == 1
    assert audit["teams"] == sorted(audit["teams"])
```

- [x] **Step 2: Verify the audit test fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_historical_bref.py::test_audit_reports_source_shape_and_ambiguities -v
```

Expected: fail because `scripts.audit_historical_source` does not exist.

- [x] **Step 3: Implement deterministic audit rendering**

```python
from collections import Counter
from pathlib import Path
import argparse

from nba_data import historical_bref


SPIKE_SEASONS = (1947, 1960, 1983)


def audit_season(raw_dir: Path, season: int) -> dict:
    frame = historical_bref.load_season(raw_dir, season)
    return {
        "season": season,
        "league": historical_bref.league_for_season(season),
        "games": len(frame),
        "date_min": frame["game_date"].min().isoformat(),
        "date_max": frame["game_date"].max().isoformat(),
        "teams": sorted(set(frame["away_team_name"]) | set(frame["home_team_name"])),
        "overtime_markers": dict(sorted(Counter(frame["overtime_marker"]).items())),
        "boxscore_links": int(frame["boxscore_url"].notna().sum()),
        "venue_reported": int(frame["venue_name"].notna().sum()),
        "attendance": dict(sorted(Counter(frame["attendance_status"]).items())),
        "site_status": dict(sorted(Counter(frame["site_status"]).items())),
        "nonblank_remarks": sorted(
            set(frame.loc[frame["game_remarks"].ne(""), "game_remarks"])
        ),
        "schedule_urls": sorted(frame["schedule_url"].unique()),
    }
```

`render_markdown` writes sections for discovery rules, regular-season/playoff
boundary, schema/stat keys, each season's exact values, explicit missingness
rules, and unresolved site remarks. Its final decision table states:

```text
blank attendance -> null + missing
literal 0 attendance -> 0 + reported_zero
blank venue -> null
blank OT -> 0; OT -> 1; nOT -> n
valid box href -> bref-<slug>; absent href -> deterministic fallback
present empty, whitespace, or malformed href -> hard failure
blank remarks -> home; explicit neutral marker -> neutral
nonblank unrecognized remarks -> unknown
games-playoffs links -> rejected by discovery
```

Implement both renderer and CLI:

```python
def render_markdown(audits: list[dict]) -> str:
    lines = [
        "# Historical Basketball Reference source spike",
        "",
        "Generated offline from checksum-valid cached index/month pages.",
        "",
        "## Discovery and boundary",
        "",
        "Only `{league}_{season}_games-{month}.html` links are accepted; "
        "`games-playoffs.html` is excluded.",
        "",
        "## Observed seasons",
        "",
    ]
    for audit in audits:
        lines.extend([
            f"### {audit['season']} {audit['league']}",
            "",
            f"- Games: {audit['games']}",
            f"- Dates: {audit['date_min']} through {audit['date_max']}",
            f"- Teams: {', '.join(audit['teams'])}",
            f"- Box-score links: {audit['boxscore_links']}",
            f"- Venue reported: {audit['venue_reported']}",
            f"- Attendance statuses: {audit['attendance']}",
            f"- Site statuses: {audit['site_status']}",
            f"- Overtime markers: {audit['overtime_markers']}",
            f"- Nonblank remarks: {audit['nonblank_remarks']}",
            f"- Schedule pages: {audit['schedule_urls']}",
            "",
        ])
    lines.extend([
        "## Missingness and normalization decisions",
        "",
        "| Source value | Normalized value |",
        "|---|---|",
        "| blank attendance | null + `missing` |",
        "| literal 0 attendance | 0 + `reported_zero` |",
        "| blank venue | null |",
        "| blank / OT / nOT | 0 / 1 / n overtime periods |",
        "| valid / blank box href | slug ID / deterministic fallback ID |",
        "| blank / explicit neutral / other remarks | `home` / `neutral` / `unknown` |",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--all-seasons", action="store_true")
    args = parser.parse_args()
    seasons = (
        historical_bref.HISTORICAL_SEASONS
        if args.all_seasons
        else SPIKE_SEASONS
    )
    rendered = render_markdown([
        audit_season(args.raw_dir, season)
        for season in seasons
    ])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
```

- [x] **Step 4: Run the real spike through one rate-limited process**

First confirm AdGuard is no longer sinking the host:

```bash
getent ahosts www.basketball-reference.com
```

Expected: no result is `0.0.0.0`, `127.0.0.0/8`, or `::`.

Then run:

```bash
.venv/bin/python -m nba_data fetch-historical --season 1947 --season 1960 --season 1983
.venv/bin/python scripts/audit_historical_source.py --raw-dir data/raw --output docs/audits/2026-07-29-historical-bref-source-spike.md
```

Expected: the first command fetches missing pages at a 12-second cadence and
the second command performs no network I/O.

- [x] **Step 5: Turn spike observations into regression assertions**

Add cached integration tests with the exact generated game totals and source
team-name sets:

```python
@pytest.mark.integration
@pytest.mark.parametrize(
    ("season", "expected_games"),
    [(1947, 331), (1960, 300), (1983, 943)],
)
def test_spike_season_game_counts(season, expected_games):
    frame = historical_bref.load_season(Path("data/raw"), season)
    assert len(frame) == expected_games
    assert frame["season"].eq(season).all()
    assert frame["game_id"].is_unique
```

If a generated audit total differs from one of these independently known
totals, stop. Diagnose page discovery, incomplete caches, or regular-season
filtering; do not edit the expected value to match a faulty parser.

- [x] **Step 6: Verify and commit the spike audit**

```bash
.venv/bin/python -m pytest tests/test_historical_bref.py -v
git status --short data/raw
git add scripts/audit_historical_source.py docs/audits/2026-07-29-historical-bref-source-spike.md tests/test_historical_bref.py
git commit -m "docs: audit historical schedule source"
```

Expected: tests pass, `git status --short data/raw` prints nothing because raw
caches are ignored, and the audit contains all three exact seasons.

---

### Task 3: Complete historical franchise identity and rebuild only the shared dimension

**Files:**
- Modify: `data/reference/franchises.csv`
- Modify: `src/nba_data/reference/franchises.csv`
- Modify: `tests/test_franchises.py`
- Modify: `data/parquet/franchises.parquet`
- Modify: `data/parquet/build_manifest.json`

**Interfaces:**
- Consumes: `franchises.resolve(name: str, season: int) -> str`;
  spike audit distinct team names.
- Produces: unambiguous season-valid mappings for every BAA/NBA source alias;
  `FRANCHISE_IDS` containing 30 current and 15 defunct lineage IDs; refreshed
  modern dimension/manifest with unchanged `games` and `team_games` bytes.

- [x] **Step 1: Write failing lineage and collision tests**

```python
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
```

- [x] **Step 2: Run the tests and confirm the mappings are absent**

```bash
.venv/bin/python -m pytest tests/test_franchises.py -v
```

Expected at this RED step: historical aliases raise `UnknownTeamError`, and
the two reused names resolve ambiguously across unrelated eras before bounded
mappings are added.

- [x] **Step 3: Add exact surviving-lineage aliases**

Add these bounded rows to `data/reference/franchises.csv`:

```csv
Tri-Cities Blackhawks,ATL,1950,1951
Milwaukee Hawks,ATL,1952,1955
St. Louis Hawks,ATL,1956,1968
Fort Wayne Pistons,DET,1949,1957
Philadelphia Warriors,GSW,1947,1962
San Francisco Warriors,GSW,1963,1971
San Diego Rockets,HOU,1968,1971
Buffalo Braves,LAC,1971,1978
Minneapolis Lakers,LAL,1949,1960
Syracuse Nationals,PHI,1950,1963
Rochester Royals,SAC,1949,1957
Cincinnati Royals,SAC,1958,1972
Kansas City-Omaha Kings,SAC,1973,1975
Chicago Packers,WAS,1962,1962
Chicago Zephyrs,WAS,1963,1963
Baltimore Bullets,WAS,1964,1973
Capital Bullets,WAS,1974,1974
New Orleans Jazz,UTA,1975,1979
```

Change the current `Denver Nuggets` row to:

```csv
Denver Nuggets,DEN,1977,
```

- [x] **Step 4: Add exact defunct-organization aliases**

```csv
Anderson Packers,AND_1949,1950,1950
Baltimore Bullets,BAL_1947,1948,1955
Chicago Stags,CHI_1946,1947,1950
Cleveland Rebels,CLE_1946,1947,1947
Denver Nuggets,DEN_1949,1950,1950
Detroit Falcons,DET_1946,1947,1947
Indianapolis Jets,IND_1948,1949,1949
Indianapolis Olympians,IND_1949,1950,1953
Pittsburgh Ironmen,PIT_1946,1947,1947
Providence Steamrollers,PRO_1946,1947,1949
Sheboygan Red Skins,SHE_1949,1950,1950
St. Louis Bombers,STL_1946,1947,1950
Toronto Huskies,TOR_1946,1947,1947
Washington Capitols,WAS_1946,1947,1951
Waterloo Hawks,WAT_1949,1950,1950
```

Sort the complete CSV by `franchise_id`, then `first_season`, then
`last_season`, then `alias`, using blank bounds before numeric bounds. Copy
the finished bytes:

```bash
cp data/reference/franchises.csv src/nba_data/reference/franchises.csv
cmp data/reference/franchises.csv src/nba_data/reference/franchises.csv
```

- [x] **Step 5: Assert every spike source name resolves exactly once**

```python
@pytest.mark.integration
@pytest.mark.parametrize("season", [1947, 1960, 1983])
def test_every_spike_source_name_resolves_once(season):
    frame = historical_bref.load_season(Path("data/raw"), season)
    names = sorted(
        set(frame["away_team_name"]) | set(frame["home_team_name"])
    )
    assert {
        name: franchises.resolve(name, season)
        for name in names
    }
```

- [x] **Step 6: Rebuild the modern dimension and prove game bytes did not move**

```bash
sha256sum data/parquet/games.parquet data/parquet/team_games.parquet > /tmp/historical-modern-game-hashes
.venv/bin/python -m nba_data build
sha256sum -c /tmp/historical-modern-game-hashes
.venv/bin/python -m pytest tests/test_franchises.py tests/test_build_gates.py::test_run_rejects_checkout_franchise_reference_divergence tests/test_repository_layout.py::test_packaged_reference_assets_match_publication_reference -v
```

Expected: both saved game hashes report `OK`; only
`franchises.parquet` and the modern manifest change among existing modern
products.

- [x] **Step 7: Verify and commit the identity expansion**

```bash
.venv/bin/python -m pytest tests/test_franchises.py tests/test_repository_layout.py -q
git add data/reference/franchises.csv src/nba_data/reference/franchises.csv tests/test_franchises.py data/parquet/franchises.parquet data/parquet/build_manifest.json
git commit -m "feat: map historical BAA and NBA franchises"
```

---

### Task 4: Independent season-count and site-correction references

**Files:**
- Create: `data/reference/historical_season_counts.csv`
- Create: `data/reference/historical_site_corrections.csv`
- Create: `tests/test_historical_build.py`
- Modify: `src/nba_data/historical_build.py`

**Interfaces:**
- Consumes: ending-season convention and `historical_bref.HISTORICAL_SEASONS`.
- Produces: `load_expected_counts(path: Path) -> dict[int, int]`;
  `load_site_corrections(path: Path) -> pd.DataFrame`;
  `apply_site_corrections(games: pd.DataFrame, corrections: pd.DataFrame) -> pd.DataFrame`;
  `HistoricalValidationError(ValueError)`.

- [x] **Step 1: Write failing reference-contract tests**

```python
from pathlib import Path

import pandas as pd
import pytest

from nba_data import historical_build


def test_expected_counts_cover_exact_historical_seasons():
    counts = historical_build.load_expected_counts(
        Path("data/reference/historical_season_counts.csv")
    )
    assert tuple(counts) == tuple(range(1947, 1984))
    assert counts[1947] == 331
    assert counts[1960] == 300
    assert counts[1983] == 943


def test_site_correction_changes_only_status():
    games = pd.DataFrame([{
        "game_id": "bref-194611010TRH",
        "site_status": "unknown",
        "home_pts": 66,
        "away_pts": 68,
    }])
    corrections = pd.DataFrame([{
        "game_id": "bref-194611010TRH",
        "site_status": "neutral",
        "source_url": "https://example.org/documented-neutral-game",
        "note": "Contemporary source identifies a neutral venue.",
    }])
    result = historical_build.apply_site_corrections(games, corrections)
    assert result.loc[0, "site_status"] == "neutral"
    assert result.loc[0, ["home_pts", "away_pts"]].tolist() == [66, 68]
```

- [x] **Step 2: Verify the focused tests fail**

```bash
.venv/bin/python -m pytest tests/test_historical_build.py -v
```

Expected: fail because the reference loaders do not exist.

- [x] **Step 3: Create the independently curated season-count reference**

Create:

```csv
season,league,expected_games,source_url,source_note
1947,BAA,331,https://www.basketball-reference.com/leagues/BAA_1947.html,Half the sum of final regular-season team wins and losses on the season summary.
1948,BAA,192,https://www.basketball-reference.com/leagues/BAA_1948.html,Half the sum of final regular-season team wins and losses on the season summary.
1949,BAA,360,https://www.basketball-reference.com/leagues/BAA_1949.html,Half the sum of final regular-season team wins and losses on the season summary.
1950,NBA,561,https://www.basketball-reference.com/leagues/NBA_1950.html,Half the sum of final regular-season team wins and losses on the season summary.
1951,NBA,354,https://www.basketball-reference.com/leagues/NBA_1951.html,Half the sum of final regular-season team wins and losses on the season summary.
1952,NBA,330,https://www.basketball-reference.com/leagues/NBA_1952.html,Half the sum of final regular-season team wins and losses on the season summary.
1953,NBA,351,https://www.basketball-reference.com/leagues/NBA_1953.html,Half the sum of final regular-season team wins and losses on the season summary.
1954,NBA,324,https://www.basketball-reference.com/leagues/NBA_1954.html,Half the sum of final regular-season team wins and losses on the season summary.
1955,NBA,288,https://www.basketball-reference.com/leagues/NBA_1955.html,Half the sum of final regular-season team wins and losses on the season summary.
1956,NBA,288,https://www.basketball-reference.com/leagues/NBA_1956.html,Half the sum of final regular-season team wins and losses on the season summary.
1957,NBA,288,https://www.basketball-reference.com/leagues/NBA_1957.html,Half the sum of final regular-season team wins and losses on the season summary.
1958,NBA,288,https://www.basketball-reference.com/leagues/NBA_1958.html,Half the sum of final regular-season team wins and losses on the season summary.
1959,NBA,288,https://www.basketball-reference.com/leagues/NBA_1959.html,Half the sum of final regular-season team wins and losses on the season summary.
1960,NBA,300,https://www.basketball-reference.com/leagues/NBA_1960.html,Half the sum of final regular-season team wins and losses on the season summary.
1961,NBA,316,https://www.basketball-reference.com/leagues/NBA_1961.html,Half the sum of final regular-season team wins and losses on the season summary.
1962,NBA,360,https://www.basketball-reference.com/leagues/NBA_1962.html,Half the sum of final regular-season team wins and losses on the season summary.
1963,NBA,360,https://www.basketball-reference.com/leagues/NBA_1963.html,Half the sum of final regular-season team wins and losses on the season summary.
1964,NBA,360,https://www.basketball-reference.com/leagues/NBA_1964.html,Half the sum of final regular-season team wins and losses on the season summary.
1965,NBA,360,https://www.basketball-reference.com/leagues/NBA_1965.html,Half the sum of final regular-season team wins and losses on the season summary.
1966,NBA,360,https://www.basketball-reference.com/leagues/NBA_1966.html,Half the sum of final regular-season team wins and losses on the season summary.
1967,NBA,405,https://www.basketball-reference.com/leagues/NBA_1967.html,Half the sum of final regular-season team wins and losses on the season summary.
1968,NBA,492,https://www.basketball-reference.com/leagues/NBA_1968.html,Half the sum of final regular-season team wins and losses on the season summary.
1969,NBA,574,https://www.basketball-reference.com/leagues/NBA_1969.html,Half the sum of final regular-season team wins and losses on the season summary.
1970,NBA,574,https://www.basketball-reference.com/leagues/NBA_1970.html,Half the sum of final regular-season team wins and losses on the season summary.
1971,NBA,697,https://www.basketball-reference.com/leagues/NBA_1971.html,Half the sum of final regular-season team wins and losses on the season summary.
1972,NBA,697,https://www.basketball-reference.com/leagues/NBA_1972.html,Half the sum of final regular-season team wins and losses on the season summary.
1973,NBA,697,https://www.basketball-reference.com/leagues/NBA_1973.html,Half the sum of final regular-season team wins and losses on the season summary.
1974,NBA,697,https://www.basketball-reference.com/leagues/NBA_1974.html,Half the sum of final regular-season team wins and losses on the season summary.
1975,NBA,738,https://www.basketball-reference.com/leagues/NBA_1975.html,Half the sum of final regular-season team wins and losses on the season summary.
1976,NBA,738,https://www.basketball-reference.com/leagues/NBA_1976.html,Half the sum of final regular-season team wins and losses on the season summary.
1977,NBA,902,https://www.basketball-reference.com/leagues/NBA_1977.html,Half the sum of final regular-season team wins and losses on the season summary.
1978,NBA,902,https://www.basketball-reference.com/leagues/NBA_1978.html,Half the sum of final regular-season team wins and losses on the season summary.
1979,NBA,902,https://www.basketball-reference.com/leagues/NBA_1979.html,Half the sum of final regular-season team wins and losses on the season summary.
1980,NBA,902,https://www.basketball-reference.com/leagues/NBA_1980.html,Half the sum of final regular-season team wins and losses on the season summary.
1981,NBA,943,https://www.basketball-reference.com/leagues/NBA_1981.html,Half the sum of final regular-season team wins and losses on the season summary.
1982,NBA,943,https://www.basketball-reference.com/leagues/NBA_1982.html,Half the sum of final regular-season team wins and losses on the season summary.
1983,NBA,943,https://www.basketball-reference.com/leagues/NBA_1983.html,Half the sum of final regular-season team wins and losses on the season summary.
```

These totals are independently cross-checked against the FiveThirtyEight NBA
Elo game archive before commit. The build reads this committed CSV and never
derives an expected count from schedule rows. Verify 37 unique seasons,
19,405 total games, sample totals `331`, `300`, and `943`, BAA only for
1947–1949, and NBA thereafter.

- [x] **Step 4: Create the correction ledger with a valid empty state**

Start with the exact header:

```csv
game_id,site_status,source_url,note
```

Rows are added only when a source URL directly documents the selected
`home` or `neutral` status. `unknown` is a published source state, not a
correction value, so the ledger permits only `home` and `neutral`.

- [x] **Step 5: Implement strict reference loaders**

```python
from pathlib import Path

import pandas as pd

from nba_data import historical_bref


COUNT_COLUMNS = [
    "season", "league", "expected_games", "source_url", "source_note"
]
CORRECTION_COLUMNS = ["game_id", "site_status", "source_url", "note"]


class HistoricalValidationError(ValueError):
    pass


def load_expected_counts(path: Path) -> dict[int, int]:
    frame = pd.read_csv(path, dtype="string")
    if frame.columns.tolist() != COUNT_COLUMNS:
        raise HistoricalValidationError(
            f"count columns must be {COUNT_COLUMNS}"
        )
    errors = []
    if frame["season"].duplicated(keep=False).any():
        errors.append("duplicate historical count season")
    seasons = pd.to_numeric(frame["season"], errors="raise").astype(int)
    games = pd.to_numeric(
        frame["expected_games"], errors="raise"
    ).astype(int)
    expected_seasons = list(historical_bref.HISTORICAL_SEASONS)
    if sorted(seasons.tolist()) != expected_seasons:
        errors.append("historical counts must cover exactly 1947..1983")
    expected_leagues = [
        historical_bref.league_for_season(int(season))
        for season in seasons
    ]
    if frame["league"].tolist() != expected_leagues:
        errors.append("historical count league/season mismatch")
    if games.le(0).any():
        errors.append("historical expected games must be positive")
    if (
        frame["source_url"].isna().any()
        or ~frame["source_url"].str.startswith("https://").all()
        or frame["source_note"].str.strip().eq("").any()
    ):
        errors.append("historical counts require source URL and note")
    if errors:
        raise HistoricalValidationError("; ".join(errors))
    return dict(zip(seasons.tolist(), games.tolist(), strict=True))


def load_site_corrections(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype="string")
    if frame.columns.tolist() != CORRECTION_COLUMNS:
        raise HistoricalValidationError(
            f"correction columns must be {CORRECTION_COLUMNS}"
        )
    errors = []
    if frame["game_id"].duplicated(keep=False).any():
        errors.append("duplicate historical site correction game_id")
    if not set(frame["site_status"].dropna()) <= {"home", "neutral"}:
        errors.append("site correction must be home or neutral")
    for column in ("game_id", "source_url", "note"):
        if frame[column].isna().any() or frame[column].str.strip().eq("").any():
            errors.append(f"site correction requires {column}")
    if len(frame) and not frame["source_url"].str.startswith("https://").all():
        errors.append("site correction source_url must use https")
    if errors:
        raise HistoricalValidationError("; ".join(errors))
    return frame
```

`apply_site_corrections` left-joins on `game_id`, rejects a correction that
matches zero or more than one row, assigns only `site_status`, drops correction
join columns, and preserves row order and all other values.

```python
def apply_site_corrections(
    games: pd.DataFrame,
    corrections: pd.DataFrame,
) -> pd.DataFrame:
    output = games.copy()
    if corrections.empty:
        return output
    counts = output["game_id"].value_counts()
    bad_targets = [
        game_id
        for game_id in corrections["game_id"]
        if int(counts.get(game_id, 0)) != 1
    ]
    if bad_targets:
        raise HistoricalValidationError(
            "site correction must resolve exactly one game: "
            f"{sorted(bad_targets)}"
        )
    selected = corrections.set_index("game_id")["site_status"]
    mask = output["game_id"].isin(selected.index)
    output.loc[mask, "site_status"] = output.loc[
        mask, "game_id"
    ].map(selected)
    return output
```

- [x] **Step 6: Add malformed-reference and orphan-correction tests**

Cover exact failures:

```text
test_counts_reject_missing_ending_season
test_counts_reject_duplicate_season
test_counts_reject_wrong_league
test_counts_reject_blank_provenance
test_corrections_reject_duplicate_game_id
test_corrections_reject_unknown_as_selected_value
test_corrections_reject_blank_source_or_note
test_apply_corrections_rejects_orphan_game_id
```

- [x] **Step 7: Verify and commit references**

```bash
.venv/bin/python -m pytest tests/test_historical_build.py -v
git add data/reference/historical_season_counts.csv data/reference/historical_site_corrections.csv src/nba_data/historical_build.py tests/test_historical_build.py
git commit -m "feat: curate historical publication references"
```

---

### Task 5: Historical frame assembly and publication gates

**Files:**
- Modify: `src/nba_data/historical_build.py`
- Modify: `tests/test_historical_build.py`

**Interfaces:**
- Consumes: `historical_bref.load_season(raw_dir: Path, season: int)`;
  `franchises.resolve(name: str, season: int) -> str`;
  Task 4 reference loaders.
- Produces: `HISTORICAL_COLUMNS: list[str]`;
  `HISTORICAL_DTYPES: dict[str, str]`;
  `assemble(data_dir: Path) -> tuple[pd.DataFrame, dict]`;
  `validate(games: pd.DataFrame, expected_counts: dict[int, int]) -> dict`.

- [x] **Step 1: Write a complete two-season assembly fixture**

Add helpers that produce source-normalized rows with all internal evidence:

```python
def source_game(
    *,
    game_id="bref-194611010TRH",
    season=1947,
    league="BAA",
    away="New York Knicks",
    home="Toronto Huskies",
    away_pts=68,
    home_pts=66,
    overtime_marker="",
    overtime_periods=0,
    site_status="home",
):
    return {
        "game_id": game_id,
        "season": season,
        "league": league,
        "game_date": pd.Timestamp("1946-11-01").date(),
        "away_team_name": away,
        "home_team_name": home,
        "away_pts": away_pts,
        "home_pts": home_pts,
        "overtime_marker": overtime_marker,
        "overtime_periods": overtime_periods,
        "site_status": site_status,
        "venue_name": pd.NA,
        "attendance": pd.NA,
        "attendance_status": "missing",
        "boxscore_url": (
            "https://www.basketball-reference.com/boxscores/"
            "194611010TRH.html"
        ),
        "schedule_url": (
            "https://www.basketball-reference.com/leagues/"
            "BAA_1947_games-november.html"
        ),
        "game_remarks": "",
    }
```

Add the two shared fixture helpers next to it:

```python
def seed_historical_references(data_dir, counts):
    reference = data_dir / "reference"
    reference.mkdir(parents=True, exist_ok=True)
    lines = ["season,league,expected_games,source_url,source_note"]
    for season, games in sorted(counts.items()):
        league = historical_build.historical_bref.league_for_season(season)
        lines.append(
            f"{season},{league},{games},"
            f"https://www.basketball-reference.com/leagues/"
            f"{league}_{season}.html,Curated test count."
        )
    (reference / "historical_season_counts.csv").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    (reference / "historical_site_corrections.csv").write_text(
        "game_id,site_status,source_url,note\n", encoding="utf-8"
    )


def assembled_game_frame():
    frame = pd.DataFrame([{
        **source_game(),
        "away_franchise_id": "NYK",
        "home_franchise_id": "TOR_1946",
        "home_margin": -2,
    }])
    frame = frame.loc[:, historical_build.HISTORICAL_COLUMNS]
    return frame.astype(historical_build.HISTORICAL_DTYPES)
```

`assembled_game_frame` must pass `validate(games, {1947: 1})` unchanged so
each Step 7 mutation isolates exactly one gate.

- [x] **Step 2: Write failing schema and gate tests**

```python
def test_assemble_resolves_lineage_and_orients_margin(monkeypatch, tmp_path):
    row = pd.DataFrame([source_game()])
    monkeypatch.setattr(
        historical_build.historical_bref,
        "HISTORICAL_SEASONS",
        (1947,),
    )
    monkeypatch.setattr(
        historical_build.historical_bref,
        "load_season",
        lambda raw_dir, season: row,
    )
    seed_historical_references(tmp_path, {1947: 1})
    games, report = historical_build.assemble(tmp_path)
    assert games.loc[0, "away_franchise_id"] == "NYK"
    assert games.loc[0, "home_franchise_id"] == "TOR_1946"
    assert games.loc[0, "home_margin"] == -2
    assert games.columns.tolist() == historical_build.HISTORICAL_COLUMNS
    assert report["games_by_season"] == {1947: 1}


def test_validation_rejects_wrong_season_count():
    games = assembled_game_frame()
    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="historical game counts",
    ):
        historical_build.validate(games, {1947: 2})
```

- [x] **Step 3: Verify the new tests fail**

```bash
.venv/bin/python -m pytest tests/test_historical_build.py -v
```

Expected: fail because `assemble`, `validate`, and publication schema
constants are absent.

- [x] **Step 4: Define and enforce the exact schema**

Add `import hashlib` at module scope, then define:

```python
HISTORICAL_COLUMNS = [
    "game_id",
    "season",
    "league",
    "game_date",
    "away_team_name",
    "home_team_name",
    "away_franchise_id",
    "home_franchise_id",
    "away_pts",
    "home_pts",
    "home_margin",
    "overtime_periods",
    "site_status",
    "venue_name",
    "attendance",
    "attendance_status",
    "boxscore_url",
    "schedule_url",
]
HISTORICAL_DTYPES = {
    "game_id": "string",
    "season": "int64",
    "league": "string",
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


def _enforce_schema(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.loc[:, HISTORICAL_COLUMNS].copy()
    for column, dtype in HISTORICAL_DTYPES.items():
        output[column] = output[column].astype(dtype)
    return output.sort_values(
        ["season", "game_date", "game_id"], kind="stable"
    ).reset_index(drop=True)
```

- [x] **Step 5: Assemble all seasons and retain parser evidence for gates**

```python
def assemble(data_dir: Path) -> tuple[pd.DataFrame, dict]:
    raw_dir = data_dir / "raw"
    frames = [
        historical_bref.load_season(raw_dir, season)
        for season in historical_bref.HISTORICAL_SEASONS
    ]
    source = pd.concat(frames, ignore_index=True)
    source["away_franchise_id"] = [
        franchises.resolve(name, int(season))
        for name, season in zip(
            source["away_team_name"], source["season"], strict=True
        )
    ]
    source["home_franchise_id"] = [
        franchises.resolve(name, int(season))
        for name, season in zip(
            source["home_team_name"], source["season"], strict=True
        )
    ]
    source = apply_site_corrections(
        source,
        load_site_corrections(
            data_dir / "reference/historical_site_corrections.csv"
        ),
    )
    source["home_margin"] = source["home_pts"] - source["away_pts"]
    evidence = source[[
        "game_id", "overtime_marker", "overtime_periods", "game_remarks"
    ]].copy()
    games = _enforce_schema(source)
    report = validate(
        games,
        load_expected_counts(
            data_dir / "reference/historical_season_counts.csv"
        ),
        evidence=evidence,
    )
    return games, report
```

Keep `evidence` out of the published frame. Pass it to validation so the
overtime marker agreement gate does not depend on a hidden global.

- [x] **Step 6: Implement one diagnostic pass covering every gate**

Use:

```python
def validate(
    games: pd.DataFrame,
    expected_counts: dict[int, int],
    *,
    evidence: pd.DataFrame | None = None,
) -> dict:
    errors = []
    games_by_season = {
        int(season): int(count)
        for season, count in games.groupby("season", sort=True).size().items()
    }
    if games_by_season != expected_counts:
        errors.append(
            "historical game counts: "
            f"actual={games_by_season}, expected={expected_counts}"
        )
```

Accumulate, then raise one semicolon-joined `HistoricalValidationError`, for:

```text
columns and dtypes differ from HISTORICAL_COLUMNS/HISTORICAL_DTYPES
season set is not exactly expected_counts keys
BAA appears outside 1947..1949 or NBA appears before 1950
duplicate or null game_id
null date, league, source team, franchise ID, or score
away and home source names are equal
away and home franchise IDs are equal
negative score or zero/tied final margin
home_margin differs from home_pts - away_pts
negative overtime count
overtime marker parses to a different count
attendance is negative
attendance_status is outside missing/reported_zero/reported_positive
attendance value/status disagree
site_status is outside home/neutral/unknown
boxscore URL is non-null but not an HTTPS basketball-reference boxscore URL
schedule URL is not an HTTPS basketball-reference league URL
minimum/maximum season differ from the minimum/maximum expected_counts key
```

Derive the span gate from `expected_counts`, not from literal 1947/1983:
`load_expected_counts` already pins production keys to exactly
`HISTORICAL_SEASONS`, so real builds still enforce 1947–1983, while unit
tests may validate small single-season frames such as `{1947: 1}` (Step 2's
`assemble` test and Step 7's mutation tests depend on that).

Return a deterministic report:

```python
def _season_sha256(frame: pd.DataFrame) -> str:
    payload = frame.to_csv(
        index=False,
        lineterminator="\n",
        na_rep="",
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


report = {
    "totals": {"games": len(games)},
    "games_by_season": games_by_season,
    "site_counts": {
        int(season): {
            status: int(count)
            for status, count in season_rows["site_status"]
            .value_counts()
            .sort_index()
            .items()
        }
        for season, season_rows in games.groupby("season", sort=True)
    },
    "boxscore_link_coverage": {},
    "venue_coverage": {},
    "attendance_raw_coverage": {},
    "season_sha256": {
        int(season): _season_sha256(season_rows)
        for season, season_rows in games.groupby("season", sort=True)
    },
}
```

Populate each coverage mapping for every season with a literal float
`notna().mean()`. Do not impose a minimum.

- [x] **Step 7: Add parameterized gate tests**

Each mutation gets its own assertion with the exact diagnostic substring:

```python
@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("game_id", pd.NA, "null game_id"),
        ("away_pts", -1, "negative score"),
        ("home_margin", 99, "home_margin disagreement"),
        ("overtime_periods", -1, "negative overtime"),
        ("site_status", "road", "unsupported site_status"),
        ("attendance", -1, "negative attendance"),
    ],
)
def test_validation_rejects_invalid_value(column, value, message):
    games = assembled_game_frame()
    games.loc[0, column] = value
    with pytest.raises(
        historical_build.HistoricalValidationError,
        match=message,
    ):
        historical_build.validate(games, {1947: 1})
```

Add separate tests for duplicate IDs, league boundary, team equality,
attendance/status disagreement, overtime marker disagreement, and multiple
diagnostics in one exception.

- [x] **Step 8: Verify and commit the gated frame**

```bash
.venv/bin/python -m pytest tests/test_historical_build.py -v
.venv/bin/python -m pytest tests/test_franchises.py tests/test_historical_bref.py -q
git add src/nba_data/historical_build.py tests/test_historical_build.py
git commit -m "feat: assemble and validate historical games"
```

---

### Task 6: Exact raw provenance, manifest, and transactional publication

**Files:**
- Modify: `src/nba_data/historical_build.py`
- Modify: `src/nba_data/build.py`
- Modify: `tests/test_historical_build.py`
- Modify: `tests/test_build_gates.py`

**Interfaces:**
- Consumes: Task 5 `assemble`; `cache.read_valid`; `cache.sha256_file`;
  these existing helpers reused from `nba_data.build` (do not redefine):
  `_assert_staged_frame(actual: pd.DataFrame, expected: pd.DataFrame, table: str) -> None`,
  `_table_manifest(path: Path, frame: pd.DataFrame) -> dict` (returns
  `rows`, `sha256`, `min_season`, `max_season`), and
  `_dependency_versions(project_dir: Path) -> dict`.
- Produces: `_raw_provenance(data_dir: Path) -> dict`;
  `_publish_historical_pair(stage_dir: Path, output_dir: Path) -> None`;
  `run(data_dir: Path) -> None`; modern publisher preservation of the complete
  historical pair and an explicit modern-reference allowlist.

- [x] **Step 1: Write failing raw-inventory and sidecar-binding tests**

Add a helper that publishes one verified index/month cache pair through the
real cache utilities (same shape as Task 2's
`publish_historical_fixture_season`, but rooted at `repo_dir / "raw"` and
kept local to `tests/test_historical_build.py`):

```python
SEED_INDEX_HTML = (
    b'<html><body>'
    b'<a href="/leagues/BAA_1947_games-november.html">November</a>'
    b"</body></html>"
)
SEED_MONTH_FIXTURE = (
    Path(__file__).parent
    / "fixtures/historical_bref/BAA_1947_games-november.html"
)
SEED_MONTH_URL = (
    "https://www.basketball-reference.com/leagues/"
    "BAA_1947_games-november.html"
)


def seed_verified_historical_cache(repo_dir):
    cache_dir = repo_dir / "raw/historical_bref"
    cache.publish_bytes(
        cache_dir / "BAA_1947_games.html",
        SEED_INDEX_HTML,
        {
            "provider": "basketball_reference",
            "resource": "historical_schedule_index",
            "league": "BAA",
            "season": 1947,
            "url": historical_bref.index_url(1947),
        },
        lambda path: historical_bref.validate_index(path, 1947),
    )
    cache.publish_bytes(
        cache_dir / "BAA_1947_games-november.html",
        SEED_MONTH_FIXTURE.read_bytes(),
        {
            "provider": "basketball_reference",
            "resource": "historical_schedule_month",
            "league": "BAA",
            "season": 1947,
            "month": "november",
            "url": SEED_MONTH_URL,
        },
        lambda path: historical_bref.validate_month(
            path, 1947, SEED_MONTH_URL
        ),
    )
```

Then add:

```python
def test_raw_provenance_rejects_orphan_body(tmp_path):
    cache_dir = tmp_path / "raw/historical_bref"
    cache_dir.mkdir(parents=True)
    (cache_dir / "BAA_1947_games.html").write_bytes(b"<html></html>")
    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="incomplete historical cache pair",
    ):
        historical_build._raw_provenance(tmp_path)


def test_raw_provenance_rejects_metadata_url_mismatch(tmp_path):
    seed_verified_historical_cache(tmp_path)
    sidecar = next(
        (tmp_path / "raw/historical_bref").glob(
            "*games-november.html.meta.json"
        )
    )
    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    metadata["url"] = "https://example.org/wrong"
    sidecar.write_text(
        json.dumps(metadata, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(
        historical_build.HistoricalValidationError,
        match="historical cache metadata mismatch",
    ):
        historical_build._raw_provenance(tmp_path)
```

Also cover an orphan sidecar, checksum mismatch, duplicate metadata URL,
unexpected file, wrong provider/resource/league/season/month, and an index
whose discovered month set differs from cached pages.

- [x] **Step 2: Write failed-publication rollback and preservation tests**

```python
def test_run_preserves_prior_historical_pair_when_validation_fails(
    monkeypatch,
    tmp_path,
):
    output = tmp_path / "parquet"
    output.mkdir()
    old = {
        "historical_games.parquet": b"old games",
        "historical_games_manifest.json": b"old manifest",
    }
    for name, payload in old.items():
        (output / name).write_bytes(payload)
    monkeypatch.setattr(
        historical_build,
        "assemble",
        lambda data_dir: (_ for _ in ()).throw(
            historical_build.HistoricalValidationError("seeded")
        ),
    )
    with pytest.raises(historical_build.HistoricalValidationError):
        historical_build.run(tmp_path)
    assert {
        name: (output / name).read_bytes()
        for name in old
    } == old
```

In `tests/test_build_gates.py`, seed both historical files before a modern
`build.run` fixture and assert their bytes are unchanged afterward. Seed only
one and assert the modern build fails before replacing `data/parquet`.
In `tests/test_historical_build.py`, seed a modern `games.parquet` whose
minimum ending season is 1985 and assert `historical_build.run` raises
`modern publication must begin at ending season 1984`.

- [x] **Step 3: Verify the provenance/publication tests fail**

```bash
.venv/bin/python -m pytest tests/test_historical_build.py tests/test_build_gates.py -k "historical or preserve" -v
```

Expected: fail because provenance and pair-preservation behavior are absent.

- [x] **Step 4: Implement exact cache-pair inventory and attestation**

For every file in `data/raw/historical_bref`, classify a body or
`.meta.json` sidecar. Reject `.part`, directories, unknown suffixes, and
body/sidecar set differences. For each season:

1. require exactly one verified index;
2. rediscover its regular-season month pages;
3. require exactly those month filenames;
4. require each body's checksum through `cache.read_valid`;
5. require sidecar identity fields to equal the filename/discovered URL; and
6. reject duplicate `(resource, league, season, month)` and duplicate URL.

Return stable path order and omit `fetched_at`:

```python
{
    "basketball_reference": {
        "pages": [
            {
                "path": "raw/historical_bref/BAA_1947_games.html",
                "provider": "basketball_reference",
                "resource": "historical_schedule_index",
                "league": "BAA",
                "season": 1947,
                "month": None,
                "url": (
                    "https://www.basketball-reference.com/leagues/"
                    "BAA_1947_games.html"
                ),
                "sha256": metadata["sha256"]
            }
        ]
    }
}
```

Here `metadata` is the checksum-verified sidecar object for that body.

- [x] **Step 5: Build a deterministic manifest in a stage directory**

At `historical_build` module scope add `import json`, `import os`,
`import shutil`, `import tempfile`, and `from nba_data import build`
(`build` does not import `historical_build`, so there is no cycle). `run`
performs:

```python
def run(data_dir: Path) -> None:
    games, report = assemble(data_dir)
    modern_seasons = pd.read_parquet(
        data_dir / "parquet/games.parquet",
        columns=["season"],
    )["season"]
    modern_min_season = int(modern_seasons.min())
    if modern_min_season != 1984:
        raise HistoricalValidationError(
            "modern publication must begin at ending season 1984"
        )
    report["modern_min_season"] = modern_min_season
    provenance = _raw_provenance(data_dir)
    references = _reference_provenance(data_dir)
    stage_dir = Path(
        tempfile.mkdtemp(prefix=".historical-stage-", dir=data_dir)
    )
    try:
        parquet_path = stage_dir / "historical_games.parquet"
        games.to_parquet(
            parquet_path,
            engine="pyarrow",
            compression="zstd",
            index=False,
        )
        reread = pd.read_parquet(parquet_path)
        build._assert_staged_frame(reread, games, "historical_games")
        reread_report = validate(
            reread,
            load_expected_counts(
                data_dir / "reference/historical_season_counts.csv"
            ),
        )
        reread_report["modern_min_season"] = modern_min_season
        if reread_report != report:
            raise HistoricalValidationError(
                "historical validation changed after Parquet re-read"
            )
        manifest = {
            "schema_version": 1,
            "sources": provenance,
            "references": references,
            "versions": build._dependency_versions(data_dir.parent),
            "tables": {
                "historical_games": build._table_manifest(
                    parquet_path, reread
                )
            },
            "validation": reread_report,
        }
        (stage_dir / "historical_games_manifest.json").write_text(
            json.dumps(
                manifest,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            ) + "\n",
            encoding="utf-8",
        )
        _publish_historical_pair(stage_dir, data_dir / "parquet")
    finally:
        if stage_dir.exists():
            shutil.rmtree(stage_dir)
```

**Final-review publication amendment:** `build._table_manifest` must serialize
the staged Parquet schema in table order. The deterministic publication test
must assert this exact value:

```python
assert manifest["tables"]["historical_games"]["schema"] == [
    {"name": "game_id", "arrow_type": "large_string"},
    {"name": "season", "arrow_type": "int64"},
    {"name": "league", "arrow_type": "large_string"},
    {"name": "game_date", "arrow_type": "date32[day]"},
    {"name": "away_team_name", "arrow_type": "large_string"},
    {"name": "home_team_name", "arrow_type": "large_string"},
    {"name": "away_franchise_id", "arrow_type": "large_string"},
    {"name": "home_franchise_id", "arrow_type": "large_string"},
    {"name": "away_pts", "arrow_type": "int64"},
    {"name": "home_pts", "arrow_type": "int64"},
    {"name": "home_margin", "arrow_type": "int64"},
    {"name": "overtime_periods", "arrow_type": "int64"},
    {"name": "site_status", "arrow_type": "large_string"},
    {"name": "venue_name", "arrow_type": "large_string"},
    {"name": "attendance", "arrow_type": "int64"},
    {"name": "attendance_status", "arrow_type": "large_string"},
    {"name": "boxscore_url", "arrow_type": "large_string"},
    {"name": "schedule_url", "arrow_type": "large_string"},
]
```

`_reference_provenance` attests exactly:

```text
reference/franchises.csv
reference/historical_season_counts.csv
reference/historical_site_corrections.csv
```

Before hashing, require the checkout franchise bytes to match
`franchises.REFERENCE`. The reused `build._dependency_versions` already
records Python plus direct runtime packages and omits host paths and
timestamps.

- [x] **Step 6: Implement recoverable two-file replacement**

`_publish_historical_pair` verifies the stage contains exactly the two product
names. It moves any prior complete pair into a temporary rollback directory,
moves both staged files into `data/parquet`, and restores the complete prior
pair if either replacement raises. It rejects an orphan prior file. Unrelated
modern files are never moved or copied.

```python
HISTORICAL_PRODUCTS = (
    "historical_games.parquet",
    "historical_games_manifest.json",
)


def _publish_historical_pair(stage_dir: Path, output_dir: Path) -> None:
    staged = sorted(path.name for path in stage_dir.iterdir())
    if staged != sorted(HISTORICAL_PRODUCTS):
        raise HistoricalValidationError(
            "historical stage must contain exactly "
            f"{sorted(HISTORICAL_PRODUCTS)}"
        )
    existing = [
        name for name in HISTORICAL_PRODUCTS if (output_dir / name).exists()
    ]
    if existing and len(existing) != len(HISTORICAL_PRODUCTS):
        raise HistoricalValidationError(
            "historical publication pair is incomplete"
        )
    rollback_dir = None
    if existing:
        rollback_dir = Path(
            tempfile.mkdtemp(
                prefix=".historical-rollback-", dir=output_dir.parent
            )
        )
        for name in HISTORICAL_PRODUCTS:
            os.replace(output_dir / name, rollback_dir / name)
    try:
        for name in HISTORICAL_PRODUCTS:
            os.replace(stage_dir / name, output_dir / name)
    except OSError:
        if rollback_dir is not None:
            for name in HISTORICAL_PRODUCTS:
                if (rollback_dir / name).exists():
                    os.replace(rollback_dir / name, output_dir / name)
        raise
    if rollback_dir is not None:
        shutil.rmtree(rollback_dir)
```

Every directory involved lives under `data/`, so each `os.replace` stays on
one filesystem. Step 8's rollback test must monkeypatch a *selective*
`os.replace` wrapper that fails only on the second stage-to-output move and
delegates every other call, so the restore path itself still works.

- [x] **Step 7: Preserve the historical pair during a modern directory swap**

In `src/nba_data/build.py` add:

```python
PRESERVED_HISTORICAL_FILES = (
    "historical_games.parquet",
    "historical_games_manifest.json",
)


def _copy_historical_publication(
    output_dir: Path,
    stage_dir: Path,
) -> set[str]:
    present = {
        name
        for name in PRESERVED_HISTORICAL_FILES
        if (output_dir / name).is_file()
    }
    if present and present != set(PRESERVED_HISTORICAL_FILES):
        raise ValidationError(
            "historical publication pair is incomplete"
        )
    for name in sorted(present):
        shutil.copyfile(output_dir / name, stage_dir / name)
    return present
```

Call it immediately after creating the modern stage and before writing modern
Parquet. Change the staged file-set gate to:

```python
if staged_products != set(PUBLISHED_FILES) | preserved:
    raise ValidationError("staged publication file set is incomplete")
```

The modern manifest does not attest the historical pair; it only preserves
the independently attested bytes.

Replace the current all-files reference glob with an explicit modern
allowlist so adding historical-only references cannot mutate the Slice 2A
manifest:

```python
MODERN_REFERENCE_FILES = (
    "franchises.csv",
    "game_corrections.csv",
    "hoopr_snapshot.json",
    "venue_capacities.csv",
)


def _reference_provenance(data_dir: Path) -> dict:
    reference_dir = data_dir / "reference"
    return {
        _relative_path(reference_dir / name, data_dir): _sha256(
            reference_dir / name
        )
        for name in MODERN_REFERENCE_FILES
    }
```

Add a regression test that writes
`reference/historical_season_counts.csv`, runs the modern publication fixture,
and asserts that filename is absent from `build_manifest.json`.

- [x] **Step 8: Add deterministic manifest and rollback tests**

Assert:

```python
assert manifest["schema_version"] == 1
assert manifest["tables"]["historical_games"]["min_season"] == 1947
assert manifest["tables"]["historical_games"]["max_season"] == 1983
assert "fetched_at" not in json.dumps(manifest)
assert "/home/" not in json.dumps(manifest)
```

Run the fixture build twice and compare both product hashes. Monkeypatch
`os.replace` to fail after the first historical product move and assert the
old pair is fully restored.

- [x] **Step 9: Verify and commit the publisher**

```bash
.venv/bin/python -m pytest tests/test_historical_build.py tests/test_build_gates.py -v
.venv/bin/python -m pytest tests/test_cache.py tests/test_repository_layout.py -q
git add src/nba_data/historical_build.py src/nba_data/build.py tests/test_historical_build.py tests/test_build_gates.py
git commit -m "feat: publish attested historical game data"
```

---

### Task 7: Full 1947–1983 collection and committed historical publication

**Files:**
- Modify: `tests/test_historical_bref.py`
- Modify: `data/reference/historical_site_corrections.csv` only for documented rows
- Create: `data/parquet/historical_games.parquet`
- Create: `data/parquet/historical_games_manifest.json`

**Interfaces:**
- Consumes: `python -m nba_data fetch-historical`;
  `python -m nba_data build-historical`; all 37 curated counts.
- Produces: complete verified raw cache, one committed row per historical
  regular-season game, and cached integration coverage for every season.

- [x] **Step 1: Fetch all missing historical pages**

Run one process so the 12-second cadence spans season boundaries:

```bash
.venv/bin/python -m nba_data fetch-historical
```

Expected: cached spike pages produce no request or sleep; every other request
uses ordinary TLS and stops on the first access/schema failure. Resume the same
command after resolving a transient provider-side failure; valid cache hits
are not refetched.

- [x] **Step 2: Add every-season cached integration coverage**

```python
@pytest.mark.integration
@pytest.mark.parametrize(
    "season",
    historical_bref.HISTORICAL_SEASONS,
)
def test_every_historical_cached_season_matches_curated_count(season):
    counts = historical_build.load_expected_counts(
        Path("data/reference/historical_season_counts.csv")
    )
    frame = historical_bref.load_season(Path("data/raw"), season)
    assert len(frame) == counts[season]
    assert frame["game_id"].notna().all()
    assert frame["game_id"].is_unique
    assert frame["away_pts"].notna().all()
    assert frame["home_pts"].notna().all()
```

Add a second integration test collecting every distinct
`(season, source-team-name)` pair and resolving it through
`franchises.resolve`. An unknown alias is fixed in the franchise reference
with a historically correct bound; it is never normalized by fuzzy matching.

- [x] **Step 3: Inspect nonblank remarks without changing parser rules**

Run:

```bash
.venv/bin/python scripts/audit_historical_source.py --raw-dir data/raw --all-seasons --output /tmp/historical-full-source-audit.md
rg -n "nonblank|unknown|remarks" /tmp/historical-full-source-audit.md
```

For a row with direct documentary evidence of a neutral or ordinary home
site, add its real game ID, selected `home` or `neutral` value, direct source
URL, and a specific explanatory note to the ledger. Rows without adequate
evidence stay `unknown`; do not infer status from arena names or city strings.

- [x] **Step 4: Run the full historical build**

```bash
.venv/bin/python -m nba_data build-historical
```

Expected: the build either publishes both files or leaves any prior pair
untouched. Any count discrepancy is investigated against index discovery,
month inventory, and the independent season-summary count before changing
code or references.

- [x] **Step 5: Verify exact panel properties**

```bash
.venv/bin/python -c "import json, pandas as pd; from pathlib import Path; p=Path('data/parquet'); g=pd.read_parquet(p/'historical_games.parquet'); m=json.loads((p/'historical_games_manifest.json').read_text(encoding='utf-8')); assert tuple(sorted(g.season.unique())) == tuple(range(1947, 1984)); assert g.game_id.is_unique; assert int(g.season.min()) == 1947; assert int(g.season.max()) == 1983; assert len(g) == sum(m['validation']['games_by_season'].values()); print(len(g))"
.venv/bin/python -m pytest tests/test_historical_bref.py tests/test_historical_build.py -v
```

- [x] **Step 6: Prove offline byte determinism**

```bash
sha256sum data/parquet/historical_games.parquet data/parquet/historical_games_manifest.json > /tmp/historical-first-build.sha256
.venv/bin/python -m nba_data build-historical
sha256sum -c /tmp/historical-first-build.sha256
```

Expected: both hashes report `OK`.

- [x] **Step 7: Commit curated corrections and products**

```bash
git add tests/test_historical_bref.py data/reference/historical_site_corrections.csv data/parquet/historical_games.parquet data/parquet/historical_games_manifest.json
git commit -m "data: publish historical BAA and NBA results"
```

Do not add `data/raw/historical_bref`.

---

### Task 8: Independent historical raw archive and restore

**Files:**
- Create: `scripts/archive_historical_raw_snapshot.sh`
- Create: `scripts/restore_historical_raw.sh`
- Create: `tests/test_historical_raw_snapshot.py`
- Modify: `scripts/archive_raw_snapshot.sh`
- Modify: `tests/test_repository_layout.py`

**Interfaces:**
- Consumes: committed `historical_games_manifest.json`;
  gitignored `data/raw/historical_bref`.
- Produces: immutable NAS directory
  `historical-raw-1983-<manifest-sha12>` with matching manifest, exact cache
  tree, complete `SHA256SUMS`, and cache provenance validated against the
  trusted manifest; verified refusal to overwrite or restore into an existing
  target.

**Final-review amendment (supersedes the self-checksum-only Steps 1, 3, and 4
examples below):** a self-consistent snapshot is not sufficient. Both scripts
must require a flat regular-file cache whose exact HTML body paths equal
`sources.basketball_reference.pages`; each body SHA-256 must equal the trusted
committed manifest, and every paired sidecar must match the manifest's
`provider`, `resource`, `league`, `season`, `month`, `url`, and `sha256`
identity. Archive validates source before copying, source and stage after
copying, and rechecks the staged manifest against the original manifest hash
immediately before atomic publication. Restore validates the snapshot against
the trusted committed manifest before copying, validates the stage against
that same trusted manifest, revalidates the snapshot after copying, performs
the final checksum-based dry run, and only then atomically renames. Existing
targets are never replaced and failure cleanup removes only the explicit
stage.

Task 8 tests must independently cover body/manifest SHA mismatch; missing and
extra body/sidecar pairs; every sidecar identity-field mismatch; symlink,
nested-directory, FIFO, and unexpected-root entries; failed-copy cleanup;
existing archive/restore targets; a snapshot changed consistently during
restore copy; and an archive stage manifest changed during copy. Each race
test must prove nonzero exit, no published/partial destination, and removal of
only the temporary stage.

- [x] **Step 1: Write failing archive/restore inverse tests**

Seed a temporary repository with a manifest and two cache pairs, and invoke
the checkout's scripts by absolute path (the temporary repository has no
`scripts/` directory; every path the scripts touch comes from the
environment overrides):

```python
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
ARCHIVE_SCRIPT = SCRIPTS / "archive_historical_raw_snapshot.sh"
RESTORE_SCRIPT = SCRIPTS / "restore_historical_raw.sh"


def inventory(directory):
    return {
        str(path.relative_to(directory)): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def seed_historical_snapshot_fixture(tmp_path):
    repo = tmp_path / "repo"
    archive_root = tmp_path / "nas"
    archive_root.mkdir()
    cache_dir = repo / "data/raw/historical_bref"
    cache_dir.mkdir(parents=True)
    bodies = {
        "BAA_1947_games.html": b"<html>index</html>",
        "BAA_1947_games-november.html": b"<html>month</html>",
    }
    pages = []
    for name, payload in bodies.items():
        (cache_dir / name).write_bytes(payload)
        month = "november" if "-november" in name else None
        resource = (
            "historical_schedule_month"
            if month is not None
            else "historical_schedule_index"
        )
        metadata = {
            "provider": "basketball_reference",
            "resource": resource,
            "league": "BAA",
            "season": 1947,
            "month": month,
            "url": f"https://www.basketball-reference.com/leagues/{name}",
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        (cache_dir / f"{name}.meta.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        pages.append({
            "path": f"raw/historical_bref/{name}",
            **metadata,
        })
    parquet_dir = repo / "data/parquet"
    parquet_dir.mkdir(parents=True)
    (parquet_dir / "historical_games_manifest.json").write_text(
        json.dumps(
            {
                "sources": {"basketball_reference": {"pages": pages}},
                "tables": {"historical_games": {"max_season": 1983}},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return repo, archive_root, inventory(cache_dir)
```

The fixture must use real body hashes and complete sidecar identities because
the scripts validate provenance as well as byte inventories. Then add:

```python
def historical_environment(repo, archive_root):
    return {
        **os.environ,
        "NBA_HISTORICAL_RAW_ARCHIVE_ROOT": str(archive_root),
        "NBA_HISTORICAL_RAW_MANIFEST": str(
            repo / "data/parquet/historical_games_manifest.json"
        ),
        "NBA_HISTORICAL_RAW_SOURCE": str(
            repo / "data/raw/historical_bref"
        ),
        "NBA_HISTORICAL_RAW_DESTINATION": str(
            repo / "restored/historical_bref"
        ),
    }


def test_archive_and_restore_are_exact_inverses(tmp_path):
    repo, archive_root, source_files = seed_historical_snapshot_fixture(
        tmp_path
    )
    archived = subprocess.run(
        ["bash", str(ARCHIVE_SCRIPT)],
        cwd=repo,
        env=historical_environment(repo, archive_root),
        capture_output=True,
        text=True,
    )
    assert archived.returncode == 0, archived.stderr
    restored = subprocess.run(
        ["bash", str(RESTORE_SCRIPT)],
        cwd=repo,
        env=historical_environment(repo, archive_root),
        capture_output=True,
        text=True,
    )
    assert restored.returncode == 0, restored.stderr
    assert inventory(repo / "restored/historical_bref") == source_files
```

Add tests that tamper with a body, delete a sidecar, change the snapshot
manifest, pre-create the restore target, and pre-create the archive target.
Every case must exit nonzero without a partial restored directory.

- [x] **Step 2: Run tests and confirm scripts are missing**

```bash
.venv/bin/python -m pytest tests/test_historical_raw_snapshot.py -v
```

Expected: fail because both historical scripts do not exist.

- [x] **Step 3: Implement the historical archive**

The following excerpt records the original baseline only. It is not complete
without the final-review manifest/cache validator, pre/post-copy validation,
copy-time race defense, same-filesystem staging, cleanup, and atomic publish
requirements above.

```bash
#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
manifest="${NBA_HISTORICAL_RAW_MANIFEST:-$root/data/parquet/historical_games_manifest.json}"
source="${NBA_HISTORICAL_RAW_SOURCE:-$root/data/raw/historical_bref}"
archive_root="${NBA_HISTORICAL_RAW_ARCHIVE_ROOT:-/mnt/nas/llm/documents/nba-predictions-archive}"

test -f "$manifest" || {
  printf 'missing historical manifest: %s\n' "$manifest" >&2
  exit 1
}
test -d "$source" || {
  printf 'missing historical raw cache: %s\n' "$source" >&2
  exit 1
}
ending_season="$(jq -er '.tables.historical_games.max_season' "$manifest")"
test "$ending_season" = 1983 || {
  printf 'unexpected historical ending season: %s\n' "$ending_season" >&2
  exit 1
}
manifest_sha="$(sha256sum "$manifest" | cut -d' ' -f1)"
destination="$archive_root/historical-raw-${ending_season}-${manifest_sha:0:12}"
test ! -e "$destination" || {
  printf 'refusing to overwrite existing snapshot: %s\n' "$destination" >&2
  exit 1
}

mkdir -p "$destination/historical_bref"
cp "$manifest" "$destination/historical_games_manifest.json"
rsync -a --checksum "$source/" "$destination/historical_bref/"
(
  cd "$destination"
  find historical_bref -type f -print0 \
    | LC_ALL=C sort -z \
    | xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)
changes="$(
  rsync -a --checksum --dry-run --itemize-changes --delete \
    "$source/" "$destination/historical_bref/"
)"
test -z "$changes" || {
  printf 'historical archive differs from source:\n%s\n' "$changes" >&2
  exit 1
}
printf 'verified historical raw snapshot: %s\n' "$destination"
```

- [x] **Step 4: Implement exact verified restore**

The restore script derives the same snapshot name, verifies the copied
manifest byte-for-byte, recomputes the complete sorted checksum inventory
before copying, refuses an existing destination, rsyncs into a same-parent
`mktemp -d` stage, verifies with `--delete --dry-run`, and renames the stage
into place. Its cleanup trap removes only the explicit temporary stage.

The excerpt below is likewise the original baseline, not the sufficient final
implementation; the final-review trusted-manifest and copy-time-race
requirements above govern.

Use these environment defaults:

```bash
manifest="${NBA_HISTORICAL_RAW_MANIFEST:-$root/data/parquet/historical_games_manifest.json}"
archive_root="${NBA_HISTORICAL_RAW_ARCHIVE_ROOT:-/mnt/nas/llm/documents/nba-predictions-archive}"
destination="${NBA_HISTORICAL_RAW_DESTINATION:-$root/data/raw/historical_bref}"
```

Complete the script with:

```bash
#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
manifest="${NBA_HISTORICAL_RAW_MANIFEST:-$root/data/parquet/historical_games_manifest.json}"
archive_root="${NBA_HISTORICAL_RAW_ARCHIVE_ROOT:-/mnt/nas/llm/documents/nba-predictions-archive}"
destination="${NBA_HISTORICAL_RAW_DESTINATION:-$root/data/raw/historical_bref}"

test -f "$manifest" || {
  printf 'missing historical manifest: %s\n' "$manifest" >&2
  exit 1
}
ending_season="$(jq -er '.tables.historical_games.max_season' "$manifest")"
manifest_sha="$(sha256sum "$manifest" | cut -d' ' -f1)"
snapshot="$archive_root/historical-raw-${ending_season}-${manifest_sha:0:12}"

for required in \
  "$snapshot/historical_games_manifest.json" \
  "$snapshot/SHA256SUMS" \
  "$snapshot/historical_bref"
do
  test -e "$required" || {
    printf 'incomplete historical snapshot: %s\n' "$required" >&2
    exit 1
  }
done

expected_checksums="$(< "$snapshot/SHA256SUMS")"
actual_checksums="$(
  cd "$snapshot"
  find historical_bref -type f -print0 \
    | LC_ALL=C sort -z \
    | xargs -0 sha256sum
)"
test "$actual_checksums" = "$expected_checksums" || {
  printf 'historical snapshot checksum mismatch: %s\n' "$snapshot" >&2
  exit 1
}
cmp -s "$manifest" "$snapshot/historical_games_manifest.json" || {
  printf 'historical snapshot manifest mismatch: %s\n' "$snapshot" >&2
  exit 1
}
test ! -e "$destination" || {
  printf 'refusing to replace historical raw directory: %s\n' "$destination" >&2
  exit 1
}

destination_parent="$(dirname "$destination")"
mkdir -p "$destination_parent"
staging="$(mktemp -d "$destination_parent/.historical-restore.XXXXXX")"
cleanup() {
  if test -n "${staging:-}" && test -d "$staging"; then
    rm -rf -- "$staging"
  fi
}
trap cleanup EXIT
rsync -a --checksum "$snapshot/historical_bref/" "$staging/"
changes="$(
  rsync -a --checksum --dry-run --itemize-changes --delete \
    "$snapshot/historical_bref/" "$staging/"
)"
test -z "$changes" || {
  printf 'restored historical cache differs:\n%s\n' "$changes" >&2
  exit 1
}
mv "$staging" "$destination"
staging=
trap - EXIT
printf 'verified historical raw restore: %s\n' "$destination"
```

- [x] **Step 5: Keep modern snapshots independent**

In `scripts/archive_raw_snapshot.sh`, add the same
`NBA_RAW_ARCHIVE_ROOT` override used by restore and exclude the historical
subtree from both real and dry-run rsync calls:

```bash
rsync -a --checksum --exclude='/historical_bref/' \
  "$root/data/raw/" "$destination/raw/"
```

The historical cache is attested only by its historical manifest and cannot
be silently bundled into a later Slice 2A snapshot.

- [x] **Step 6: Verify, create the real NAS snapshot, and commit scripts**

```bash
.venv/bin/python -m pytest tests/test_historical_raw_snapshot.py tests/test_repository_layout.py -v
bash -n scripts/archive_historical_raw_snapshot.sh
bash -n scripts/restore_historical_raw.sh
bash -n scripts/archive_raw_snapshot.sh
bash scripts/archive_historical_raw_snapshot.sh
git add scripts/archive_historical_raw_snapshot.sh scripts/restore_historical_raw.sh scripts/archive_raw_snapshot.sh tests/test_historical_raw_snapshot.py tests/test_repository_layout.py
git commit -m "ops: archive historical raw data independently"
```

Expected: the NAS snapshot path printed by the script exists and its complete
checksum inventory passes.

---

### Task 9: Pure long-run home-court analysis

**Files:**
- Create: `src/nba_data/home_court.py`
- Create: `tests/test_home_court.py`
- Modify: `requirements.in`
- Modify: `requirements.txt`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: `historical_games.parquet`; modern `games.parquet`.
- Produces: `load_results_panel(data_dir: Path) -> pd.DataFrame`;
  `wilson_interval(successes: int, trials: int, z: float = 1.959963984540054) -> tuple[float, float]`;
  `cluster_bootstrap_mean_margin(groups: dict[str, np.ndarray], *, seed: int, resamples: int = 5000) -> tuple[float, float]`;
  `summarize_by_season(panel: pd.DataFrame, *, seed: int = 1947, resamples: int = 5000) -> pd.DataFrame`.

- [x] **Step 1: Declare NumPy as a direct runtime dependency**

Add `numpy` to `requirements.in` and the `[project].dependencies` list. Then
compile **without** `--upgrade`: NumPy is already pinned transitively
(`numpy==2.5.1` via pandas/matplotlib), so a no-upgrade compile only
reclassifies it as direct. An `--upgrade` compile (the README's routine
maintenance command) could bump pandas/pyarrow and change committed Parquet
bytes, violating this plan's modern byte-stability constraint mid-slice.

```bash
/home/riz/.local/bin/uv pip compile --python-version 3.12 --universal requirements.in --output-file requirements.txt
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
git diff requirements.txt
```

Expected: the `requirements.txt` diff touches only NumPy's classification
comment lines; no pinned version changes.

- [x] **Step 2: Write failing union-boundary and orientation tests**

Define the three fixture helpers in `tests/test_home_court.py` first. Each
writes only the columns `load_results_panel` selects or derives:

```python
import datetime


def write_historical_parquet(
    data_dir,
    *,
    season,
    home_pts=100,
    away_pts=90,
    site_status="home",
):
    parquet = data_dir / "parquet"
    parquet.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{
        "game_id": f"bref-hist-{season}",
        "season": season,
        "league": "BAA" if season <= 1949 else "NBA",
        "game_date": datetime.date(season, 1, 15),
        "home_franchise_id": "BOS",
        "away_franchise_id": "NYK",
        "home_pts": home_pts,
        "away_pts": away_pts,
        "home_margin": home_pts - away_pts,
        "site_status": site_status,
    }]).to_parquet(parquet / "historical_games.parquet", index=False)


def write_modern_parquet(
    data_dir,
    *,
    season,
    home_pts=100,
    away_pts=90,
    neutral_site=False,
):
    parquet = data_dir / "parquet"
    parquet.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{
        "game_id": f"modern-{season}",
        "season": season,
        "game_date": datetime.date(season, 1, 15),
        "home_franchise_id": "BOS",
        "away_franchise_id": "NYK",
        "home_pts": home_pts,
        "away_pts": away_pts,
        "neutral_site": neutral_site,
    }]).to_parquet(parquet / "games.parquet", index=False)


def result_panel(rows):
    records = []
    for index, (season, franchise, site_status, margin) in enumerate(rows):
        records.append({
            "game_id": f"panel-{index:02d}",
            "season": season,
            "league": "BAA" if season <= 1949 else "NBA",
            "game_date": datetime.date(season, 2, 1),
            "home_franchise_id": franchise,
            "away_franchise_id": "OPP",
            "home_pts": 100 + margin,
            "away_pts": 100,
            "home_margin": margin,
            "site_status": site_status,
            "period": "historical",
        })
    return pd.DataFrame(records)
```

Then add the failing tests:

```python
def test_load_results_panel_joins_exact_1983_1984_boundary(tmp_path):
    write_historical_parquet(
        tmp_path,
        season=1983,
        home_pts=90,
        away_pts=95,
        site_status="home",
    )
    write_modern_parquet(
        tmp_path,
        season=1984,
        home_pts=110,
        away_pts=100,
        neutral_site=False,
    )
    panel = home_court.load_results_panel(tmp_path)
    assert panel["season"].tolist() == [1983, 1984]
    assert panel["home_margin"].tolist() == [-5, 10]
    assert panel["site_status"].tolist() == ["home", "home"]


def test_union_rejects_gap_or_overlap(tmp_path):
    write_historical_parquet(tmp_path, season=1982)
    write_modern_parquet(tmp_path, season=1984)
    with pytest.raises(ValueError, match="1983/1984 boundary"):
        home_court.load_results_panel(tmp_path)
```

- [x] **Step 3: Write failing site-exclusion and weighted-trend tests**

```python
def test_summary_excludes_neutral_and_unknown_from_home_estimands():
    panel = result_panel([
        (1947, "BOS", "home", 5),
        (1947, "NYK", "home", -1),
        (1947, "BOS", "neutral", 20),
        (1947, "NYK", "unknown", 30),
    ])
    summary = home_court.summarize_by_season(
        panel, seed=7, resamples=50
    )
    row = summary.iloc[0]
    assert row["games"] == 4
    assert row["home_site_games"] == 2
    assert row["neutral_site_games"] == 1
    assert row["unknown_site_games"] == 1
    assert row["home_win_pct"] == 0.5
    assert row["mean_home_margin"] == 2.0


def test_trailing_window_is_game_weighted_not_season_weighted():
    panel = result_panel([
        (1947, "BOS", "home", 1),
        (1948, "BOS", "home", -1),
        (1948, "NYK", "home", -2),
        (1948, "PHI", "home", -3),
    ])
    summary = home_court.summarize_by_season(
        panel, seed=9, resamples=20
    )
    assert summary.loc[
        summary["season"].eq(1948),
        "trailing_5_home_win_pct",
    ].item() == 0.25
```

- [x] **Step 4: Verify analysis tests fail**

```bash
.venv/bin/python -m pytest tests/test_home_court.py -v
```

Expected: fail because `nba_data.home_court` does not exist.

- [x] **Step 5: Implement the compatible result union**

```python
from pathlib import Path
import math

import numpy as np
import pandas as pd


RESULT_COLUMNS = [
    "game_id",
    "season",
    "league",
    "game_date",
    "home_franchise_id",
    "away_franchise_id",
    "home_pts",
    "away_pts",
    "home_margin",
    "site_status",
    "period",
]


def load_results_panel(data_dir: Path) -> pd.DataFrame:
    parquet = data_dir / "parquet"
    historical = pd.read_parquet(
        parquet / "historical_games.parquet"
    ).assign(period="historical")
    modern = pd.read_parquet(parquet / "games.parquet").assign(
        league="NBA",
        home_margin=lambda frame: frame["home_pts"] - frame["away_pts"],
        site_status=lambda frame: np.where(
            frame["neutral_site"], "neutral", "home"
        ),
        period="modern",
    )
    if (
        int(historical["season"].max()) != 1983
        or int(modern["season"].min()) != 1984
    ):
        raise ValueError("results panel must meet the exact 1983/1984 boundary")
    panel = pd.concat(
        [
            historical[RESULT_COLUMNS],
            modern[RESULT_COLUMNS],
        ],
        ignore_index=True,
    ).sort_values(
        ["season", "game_date", "game_id"], kind="stable"
    ).reset_index(drop=True)
    if panel["game_id"].duplicated().any():
        raise ValueError("results panel contains duplicate game_id")
    return panel
```

The union layer checks only the 1983/1984 boundary so the Step 2 fixtures
(one season on each side) remain valid. Full-span guarantees are enforced
elsewhere: the historical build gates 1947–1983 through
`load_expected_counts`, and Task 10 Step 6 plus Task 12 Step 4 assert the
real panel spans exactly 1947–2026. Do not hard-code 2026 here; the modern
table grows every season.

- [x] **Step 6: Implement Wilson and fixed-seed cluster-bootstrap intervals**

```python
def wilson_interval(
    successes: int,
    trials: int,
    z: float = 1.959963984540054,
) -> tuple[float, float]:
    if trials <= 0:
        return (math.nan, math.nan)
    proportion = successes / trials
    denominator = 1 + z * z / trials
    center = (
        proportion + z * z / (2 * trials)
    ) / denominator
    half_width = (
        z
        * math.sqrt(
            proportion * (1 - proportion) / trials
            + z * z / (4 * trials * trials)
        )
        / denominator
    )
    return center - half_width, center + half_width


def cluster_bootstrap_mean_margin(
    groups: dict[str, np.ndarray],
    *,
    seed: int,
    resamples: int = 5000,
) -> tuple[float, float]:
    if not groups:
        return (math.nan, math.nan)
    keys = tuple(sorted(groups))
    rng = np.random.default_rng(seed)
    draws = np.empty(resamples, dtype=float)
    for index in range(resamples):
        sampled = rng.choice(keys, size=len(keys), replace=True)
        margins = np.concatenate([groups[str(key)] for key in sampled])
        draws[index] = float(margins.mean())
    low, high = np.quantile(draws, [0.025, 0.975])
    return float(low), float(high)
```

The bootstrap resamples home-franchise clusters within each season. Repeated
clusters repeat all their games, preserving within-franchise dependence.

- [x] **Step 7: Implement season summaries and game-weighted trailing windows**

Return columns in this exact order:

```python
SUMMARY_COLUMNS = [
    "season",
    "league",
    "games",
    "home_site_games",
    "neutral_site_games",
    "unknown_site_games",
    "home_site_coverage",
    "home_wins",
    "home_losses",
    "home_win_pct",
    "home_win_ci_low",
    "home_win_ci_high",
    "mean_home_margin",
    "median_home_margin",
    "mean_margin_ci_low",
    "mean_margin_ci_high",
    "trailing_5_home_win_pct",
    "trailing_5_mean_home_margin",
]
```

For each season, filter `site_status == "home"`, group margins by
`home_franchise_id`, call the bootstrap with `seed + season`, and retain raw
`home_wins`, `home_site_games`, and margin sums long enough to calculate
rolling five-season ratios:

```python
summary["trailing_5_home_win_pct"] = (
    summary["home_wins"].rolling(5, min_periods=1).sum()
    / summary["home_site_games"].rolling(5, min_periods=1).sum()
)
summary["trailing_5_mean_home_margin"] = (
    summary["_home_margin_sum"].rolling(5, min_periods=1).sum()
    / summary["home_site_games"].rolling(5, min_periods=1).sum()
)
```

Drop `_home_margin_sum` before returning `SUMMARY_COLUMNS`.

- [x] **Step 8: Add exact interval and determinism tests**

Assert Wilson bounds contain the observed proportion and stay in `[0, 1]`.
Call `summarize_by_season` twice with the same seed and use
`pd.testing.assert_frame_equal`. Call it with a different seed and assert
point estimates are unchanged.

- [x] **Step 9: Verify and commit analysis primitives**

```bash
.venv/bin/python -m pytest tests/test_home_court.py -v
.venv/bin/python -m pytest tests/test_repository_layout.py::test_requirements_are_utf8_and_pinned tests/test_repository_layout.py::test_wheel_declares_runtime_dependencies_and_imports_off_repo -v
git add src/nba_data/home_court.py tests/test_home_court.py requirements.in requirements.txt pyproject.toml
git commit -m "feat: summarize long-run home-court advantage"
```

---

### Task 10: Deterministic historical trend deliverables

**Files:**
- Create: `analysis/covid_hca/build_historical_trend.py`
- Create: `tests/test_historical_report.py`
- Modify: `analysis/covid_hca/README.md`
- Create: `analysis/covid_hca/data/home_court_by_season.csv`
- Create: `analysis/covid_hca/figures/home_court_trend_1947_2026.png`
- Create: `analysis/covid_hca/historical_trend.md`

**Interfaces:**
- Consumes: `home_court.load_results_panel`;
  `home_court.summarize_by_season`.
- Produces: `render_figure(summary: pd.DataFrame, path: Path) -> None`;
  `render_report(summary: pd.DataFrame) -> str`;
  `run(data_dir: Path, output_dir: Path) -> None`; three deterministic
  committed outputs.

- [x] **Step 1: Write failing render smoke and determinism tests**

`analysis/` is not a package; load the report module by file path in
`tests/test_historical_report.py` and build a constant full-span summary
fixture:

```python
import hashlib
import importlib.util
from pathlib import Path

import pandas as pd

from nba_data import home_court

_REPORT_PATH = (
    Path(__file__).resolve().parents[1]
    / "analysis/covid_hca/build_historical_trend.py"
)
_REPORT_SPEC = importlib.util.spec_from_file_location(
    "build_historical_trend", _REPORT_PATH
)
historical_report = importlib.util.module_from_spec(_REPORT_SPEC)
_REPORT_SPEC.loader.exec_module(historical_report)


def synthetic_summary_1947_to_2026():
    seasons = list(range(1947, 2027))
    frame = pd.DataFrame({
        "season": seasons,
        "league": [
            "BAA" if season <= 1949 else "NBA" for season in seasons
        ],
        "games": 100,
        "home_site_games": 90,
        "neutral_site_games": 6,
        "unknown_site_games": 4,
        "home_site_coverage": 0.9,
        "home_wins": 54,
        "home_losses": 36,
        "home_win_pct": 0.6,
        "home_win_ci_low": 0.55,
        "home_win_ci_high": 0.65,
        "mean_home_margin": 3.2,
        "median_home_margin": 3.0,
        "mean_margin_ci_low": 2.4,
        "mean_margin_ci_high": 4.0,
        "trailing_5_home_win_pct": 0.6,
        "trailing_5_mean_home_margin": 3.2,
    })
    return frame.loc[:, home_court.SUMMARY_COLUMNS]
```

Then add:

```python
def test_report_run_writes_three_deterministic_products(
    tmp_path,
    monkeypatch,
):
    summary = synthetic_summary_1947_to_2026()
    monkeypatch.setattr(
        historical_report.home_court,
        "load_results_panel",
        lambda data_dir: pd.DataFrame(),
    )
    monkeypatch.setattr(
        historical_report.home_court,
        "summarize_by_season",
        lambda panel: summary,
    )
    output = tmp_path / "analysis"
    historical_report.run(tmp_path, output)
    first = {
        path.relative_to(output): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in output.rglob("*")
        if path.is_file()
    }
    historical_report.run(tmp_path, output)
    second = {
        path.relative_to(output): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in output.rglob("*")
        if path.is_file()
    }
    assert first == second
    assert set(first) == {
        Path("data/home_court_by_season.csv"),
        Path("figures/home_court_trend_1947_2026.png"),
        Path("historical_trend.md"),
    }
```

- [x] **Step 2: Verify the report test fails**

```bash
MPLBACKEND=Agg .venv/bin/python -m pytest tests/test_historical_report.py -v
```

Expected: fail because the report module does not exist.

- [x] **Step 3: Implement the two-panel figure**

Use the noninteractive backend before importing pyplot:

```python
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
```

Define descriptive markers:

```python
ERA_MARKERS = {
    1955: "24-second clock",
    1977: "ABA merger",
    1980: "3-point line",
    2020: "COVID/bubble",
}
```

The upper panel plots annual `home_win_pct`, Wilson shading, and
`trailing_5_home_win_pct`. The lower plots annual `mean_home_margin`,
bootstrap shading, and `trailing_5_mean_home_margin`. Both share the season
axis, use percentage points only on the win panel, draw a vertical dotted line
for each marker, and mark the 1983/1984 data-source boundary separately.

Save deterministically:

```python
figure.savefig(
    path,
    dpi=160,
    bbox_inches="tight",
    metadata={"Software": "nba-data historical trend"},
)
plt.close(figure)
```

- [x] **Step 4: Implement a data-derived, noncausal report**

`render_report` reads exact rows to report the inaugural estimate, the
historical maximum and minimum five-season win trends, the 1983 boundary, the
ending-2020 season home-site estimate, and the latest 2026 value. The report
must state that this estimate uses only `site_status == "home"` and excludes
neutral-site bubble games; it must not label the value a bubble-period
estimate. Use deterministic formatting:

```python
def percentage(value: float) -> str:
    return f"{100 * value:.1f}%"


def margin(value: float) -> str:
    return f"{value:+.2f} points"
```

The Markdown contains these sections:

```text
# Home-court advantage across BAA/NBA history
## What is measured
## Long-run pattern
## COVID context
## Data limitations
## Reproduce
```

State explicitly that the markers are descriptive, the historical
classification quality changes across eras, 1984–2019 inherits the legacy
home-designation assumption, and the later COVID fixed-effects analysis—not
this chart—defines the causal estimand.

- [x] **Step 5: Implement one-command output generation**

```python
def run(data_dir: Path, output_dir: Path) -> None:
    panel = home_court.load_results_panel(data_dir)
    summary = home_court.summarize_by_season(panel)
    data_path = output_dir / "data/home_court_by_season.csv"
    figure_path = (
        output_dir / "figures/home_court_trend_1947_2026.png"
    )
    report_path = output_dir / "historical_trend.md"
    data_path.parent.mkdir(parents=True, exist_ok=True)
    figure_path.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(data_path, index=False, lineterminator="\n")
    render_figure(summary, figure_path)
    report_path.write_text(
        render_report(summary),
        encoding="utf-8",
    )
```

The command-line entry defaults to `data/` and `analysis/covid_hca/` relative
to the source checkout, and accepts explicit `--data-dir` and `--output-dir`
for tests or installed use.

- [x] **Step 6: Render the real outputs twice**

```bash
MPLBACKEND=Agg .venv/bin/python analysis/covid_hca/build_historical_trend.py
sha256sum analysis/covid_hca/data/home_court_by_season.csv analysis/covid_hca/figures/home_court_trend_1947_2026.png analysis/covid_hca/historical_trend.md > /tmp/historical-analysis-first.sha256
MPLBACKEND=Agg .venv/bin/python analysis/covid_hca/build_historical_trend.py
sha256sum -c /tmp/historical-analysis-first.sha256
```

Expected: all three hashes report `OK`, CSV seasons are exactly 1947–2026,
and the report's numeric statements agree with the CSV.

- [x] **Step 7: Verify and commit deliverables**

```bash
MPLBACKEND=Agg .venv/bin/python -m pytest tests/test_home_court.py tests/test_historical_report.py -v
git add analysis/covid_hca/build_historical_trend.py analysis/covid_hca/README.md analysis/covid_hca/data/home_court_by_season.csv analysis/covid_hca/figures/home_court_trend_1947_2026.png analysis/covid_hca/historical_trend.md tests/test_historical_report.py
git commit -m "analysis: publish historical home-court trends"
```

---

### Task 11: Data dictionary, repository layout, and operator documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/data-dictionary.md`
- Modify: `analysis/covid_hca/README.md`
- Modify: `tests/test_repository_layout.py`

**Interfaces:**
- Consumes: final schema, manifests, commands, source audit, raw snapshot, and
  generated outputs.
- Produces: one authoritative operator path for fetch, offline build, restore,
  and analysis reproduction.

- [x] **Step 1: Write failing layout/documentation assertions**

Extend the required delivery set:

```python
required = {
    "data/parquet/historical_games.parquet",
    "data/parquet/historical_games_manifest.json",
    "data/reference/historical_season_counts.csv",
    "data/reference/historical_site_corrections.csv",
    "docs/audits/2026-07-29-historical-bref-source-spike.md",
    "scripts/archive_historical_raw_snapshot.sh",
    "scripts/restore_historical_raw.sh",
    "analysis/covid_hca/data/home_court_by_season.csv",
    "analysis/covid_hca/figures/home_court_trend_1947_2026.png",
    "analysis/covid_hca/historical_trend.md",
}
assert [path for path in sorted(required) if not (ROOT / path).is_file()] == []
```

Assert the historical manifest contains no `/home/`, no `fetched_at`, exactly
37 season counts, and only relative source paths. Assert `git ls-files
data/raw/historical_bref` is empty.

- [x] **Step 2: Run the focused layout test and observe missing docs**

```bash
.venv/bin/python -m pytest tests/test_repository_layout.py -v
```

Expected: fail until the documentation names the new contract and commands.

- [x] **Step 3: Document the historical table and manifest**

Add a `historical_games.parquet` section to the data dictionary with all 18
columns, exact types, source-era name versus lineage ID distinction, stable
ID/fallback rule, site-status semantics, attendance blank/zero behavior,
coverage definitions, and the 1947/1983 boundary.

Document that:

```text
python -m nba_data fetch-historical
```

is the only network command, while:

```text
python -m nba_data build-historical
```

is offline and cache-complete. Link the approved design, source spike, both
manifests, Sports Reference policies, season counts, correction ledger, and
historical trend report.

- [x] **Step 4: Document independent recovery**

State:

```bash
bash scripts/restore_historical_raw.sh
bash scripts/archive_historical_raw_snapshot.sh
```

The restore target must be absent. Explain snapshot naming, manifest binding,
complete checksum verification, the default NAS root, and why
`scripts/restore_raw.sh` remains the separate Slice 2A restore.

- [x] **Step 5: Update project and analysis status**

README marks results-level history 1947–2026 as delivered while historical
rich box scores remain a future separately reviewed phase. The analysis README
explains how to regenerate the CSV/PNG/report and preserves the modern COVID
fixed-effects analysis as the next causal-analysis task.

- [x] **Step 6: Verify and commit documentation**

```bash
.venv/bin/python -m pytest tests/test_repository_layout.py tests/test_verify_repository.py -v
.venv/bin/python scripts/verify_repository.py
git add README.md docs/data-dictionary.md analysis/covid_hca/README.md tests/test_repository_layout.py
git commit -m "docs: document historical results and recovery"
```

---

### Task 12: Acceptance verification and review handoff

**Files:**
- Modify only files required by verified review findings.

**Interfaces:**
- Consumes: every prior task.
- Produces: a clean, reviewed feature branch ready for
  `superpowers:finishing-a-development-branch`.

- [x] **Step 1: Run the full default suite with integration tests enabled**

```bash
.venv/bin/python -m pytest -q
```

Expected: zero failures and zero skips. Every collected historical season is
read from verified cache.

- [x] **Step 2: Run repository and shell verification**

```bash
.venv/bin/python scripts/verify_repository.py
bash -n scripts/archive_raw_snapshot.sh
bash -n scripts/restore_raw.sh
bash -n scripts/archive_historical_raw_snapshot.sh
bash -n scripts/restore_historical_raw.sh
```

Expected: all commands exit zero.

- [x] **Step 3: Rebuild all committed data products and analysis**

```bash
.venv/bin/python -m nba_data build
.venv/bin/python -m nba_data build-historical
MPLBACKEND=Agg .venv/bin/python analysis/covid_hca/build_historical_trend.py
git status --short
```

Expected: modern build preserves the historical pair; all three commands
produce no tracked diff.

- [x] **Step 4: Run explicit acceptance assertions**

```bash
.venv/bin/python -c "import json, pandas as pd; from pathlib import Path; p=Path('data/parquet'); h=pd.read_parquet(p/'historical_games.parquet'); g=pd.read_parquet(p/'games.parquet'); hm=json.loads((p/'historical_games_manifest.json').read_text(encoding='utf-8')); assert tuple(range(1947,1984)) == tuple(sorted(h.season.unique())); assert int(g.season.min()) == 1984; assert h.game_id.is_unique; assert not h[['game_date','league','away_team_name','home_team_name','away_franchise_id','home_franchise_id','away_pts','home_pts']].isna().any().any(); assert len(h) == sum(hm['validation']['games_by_season'].values()); assert set(h.site_status.unique()) <= {'home','neutral','unknown'}; print({'historical_games': len(h), 'modern_games': len(g)})"
git ls-files data/raw/historical_bref
rg -n "verify=False|ssl=False|CERT_NONE|disable_warnings" src scripts tests
```

Expected: the Python command exits zero; both text searches print nothing.

- [x] **Step 5: Verify NAS recovery without replacing the live cache**

Use a temporary destination:

```bash
NBA_HISTORICAL_RAW_DESTINATION=/tmp/nba-historical-raw-restore-check bash scripts/restore_historical_raw.sh
cmp data/raw/historical_bref/BAA_1947_games.html /tmp/nba-historical-raw-restore-check/BAA_1947_games.html
```

After the comparison, remove only the explicit temporary restore directory
through the normal workspace cleanup process.

- [x] **Step 6: Request independent code review**

Invoke `superpowers:requesting-code-review`. Give the reviewer:

```text
Base: 66fc4e8
Head: git rev-parse HEAD
Spec: docs/superpowers/specs/2026-07-29-historical-game-results-design.md
Plan: docs/superpowers/plans/2026-07-29-historical-game-results.md
Focus: source boundaries, cache/manifest provenance, franchise collisions,
season gates, pair rollback/preservation, site exclusions, interval math,
determinism, and recovery.
```

- [x] **Step 7: Process findings rigorously**

Invoke `superpowers:receiving-code-review`. Reproduce each claimed failure,
add a failing regression test for confirmed defects, implement the narrow fix,
rerun the focused and full suites, and commit each coherent correction.

- [x] **Step 8: Final clean-state verification**

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/verify_repository.py
git status --short --branch
git log --oneline --decorate -12
```

Expected: all tests pass with zero skips, verifier exits zero, and the feature
branch is clean and unmerged.

---

## Final Acceptance Checklist

- [x] Source spike documents 1947, 1960, and 1983 through one adapter.
- [x] All 37 ending seasons 1947–1983 match independent curated totals.
- [x] BAA/NBA boundary and regular-season-only discovery are explicit.
- [x] Every source team resolves one season-valid surviving or defunct lineage.
- [x] Historical table contains no fabricated modern statistics.
- [x] Attendance null/zero and venue null semantics are preserved.
- [x] Neutral and unknown games are published but excluded from home estimands.
- [x] Raw body/sidecar inventory and metadata identity match exactly.
- [x] Historical Parquet and manifest are byte-identical across offline builds.
- [x] Modern game/team-game bytes remain unchanged and modern rebuilds preserve the historical pair.
- [x] Historical NAS archive and restore are verified exact inverses.
- [x] Result union has an exact 1983/1984 boundary and spans 1947–2026.
- [x] Wilson, cluster-bootstrap, and game-weighted trailing calculations pass deterministic tests.
- [x] CSV, figure, and written trend regenerate byte-identically.
- [x] Default tests pass with zero skipped cached integration tests.
- [x] Repository contains no raw historical file, host path, mutable generated timestamp, or TLS bypass.
- [x] Rich historical team/player box scores remain outside this delivery.
