# NBA Repo Revival + COVID Home-Court Advantage Analysis — Design

*Approved 2026-07-29 and revised after the repository/data audit. Brainstormed
from `PROJECT_REVIEW.md` and `PROJECT_DATA_FINDINGS.md`.*

## Goal

Turn the 2022 DTSC 691 capstone repo into a clean, analysis-first NBA data
project; extend its dataset from 2020 through the 2025-26 season; and produce a
rigorous, shareable analysis of how much of NBA home-court advantage is the
crowd, using the COVID era as a natural experiment.

## Decisions made during brainstorming

| Decision | Choice |
|---|---|
| Repo identity | Analysis-first; Flask dashboard archived, not maintained |
| Git history | Rewrite with `git filter-repo` to purge `venv/`; force-push to `wrislin1/nba-predictions` |
| Data storage | Tidy Parquet tables committed to git (no LFS); raw originals cold-archived to NAS; DuckDB generated locally, gitignored |
| Data sources | Legacy 1983–2019 box scores from the archived stats.nba.com data; 2019-20 → 2025-26 box scores/schedules from the sportsdataverse hoopR parquet dumps (stats.nba.com is TLS-blocked from this network — see `docs/audits/2026-07-29-slice2-source-spike.md`); attendance source of record: basketball-reference month pages, with hoopR attendance as cross-validation |
| Analysis deliverable | Markdown report + reproducible notebook, figures committed as PNG |
| Methodology | Descriptive layer + explicitly scoped quasi-experimental fixed-effects models with cluster-bootstrap CIs |

The three slices are intentionally planned in sequence. The repository-rescue
plan is written first; the ingestion plan waits for a live source/schema spike,
and the analysis plan waits for the cleaned data and a finalized attendance
encoding. This prevents operational guesses from becoming implementation
requirements.

## Global constraints

- **CPU-only for now.** Both RTX 3090s are currently running a long-term
  training task; while that runs, nothing in this project may allocate GPU
  memory. This is a temporary condition, not a design principle — once the
  training completes (or between runs), the GPUs are available to this project
  for whatever it needs. Check `nvidia-smi` before any GPU work. The planned
  slices happen to need only pandas/DuckDB/statsmodels regardless.
- **Polite scraping.** basketball-reference: ≤1 request per 12 s, comfortably
  below [Sports Reference's published limit](https://www.sports-reference.com/bot-traffic.html);
  browser-like User-Agent; every fetched page cached to disk before parsing so
  no re-fetch is ever needed. Do not attempt CAPTCHA, Cloudflare, or other
  access-control bypasses.
- **Nothing is deleted until the NAS backup is verified** (mirror clone + raw
  data copy, checksummed).
- **No history rewrite in the dev-laptop working copy.** Backup and ordinary
  restructuring happen first; `git filter-repo` runs only in a disposable
  clone. The current clone remains the pre-rewrite archive.
- **Force-push requires a separate, just-in-time user approval.** Approval of
  this design or an implementation plan is not approval to replace the remote
  branch.
- **Python:** Linux `.venv` via `python3 -m venv`, UTF-8 `requirements.txt`,
  pinned versions.
- After the force-push, the old dev-laptop clone is archive-only (incompatible
  hashes). The NAS mirror preserves pre-rewrite history.

## Slice 1 — Repo restructure & history rewrite

Target layout:

```
nba-predictions/
├── README.md              # rewritten: project story, data dictionary, how to run
├── requirements.txt       # UTF-8, pinned: pandas, duckdb, pyarrow, nba_api,
│                          #   requests, beautifulsoup4, lxml, statsmodels,
│                          #   linearmodels, matplotlib, seaborn, pytest, jupyter
├── .gitignore             # .venv/, data/raw/, *.duckdb, .serena/, .idea/, __pycache__/
├── data/
│   ├── parquet/           # tidy tables — committed
│   └── raw/               # gitignored scraper staging
├── src/nba_data/          # scraper + build pipeline package
├── analysis/covid_hca/    # notebook, figures/, report.md
├── archive/
│   ├── dashboard/         # start.py, templates/, static/img era PNGs
│   └── notebooks/         # the 3 original .ipynb, unchanged
├── scripts/               # deterministic repository/data audit helpers
├── tests/                 # repository-layout checks now; data tests later
└── docs/
    ├── audits/            # preservation/rewrite manifests
    └── superpowers/       # specs + plans
```

Surgery order (sequencing is load-bearing):

1. **Preflight and inventory:** record HEAD, all refs, exact remote URL, dirty
   status, file counts/bytes, and content hashes for preservation-critical
   inputs. Confirm the archive path is a writable NFS mount and install the
   currently missing `git-filter-repo` tool before any mutation.
2. **NAS backup:** create a `git clone --mirror`, copy untracked `nba-stats/`
   and the complete current `venv/static/data/` (including the modified
   prediction CSV and 3 joblib models), and preserve both project-review
   documents. Verify the mirror with `git fsck` and the copies by checksum and
   checksum-aware dry-run comparison.
3. **Disposable staging clone:** clone locally from the current repository with
   no shared object hardlinks. Copy the two review documents into it. The dirty
   dev-laptop clone is not used for the rewrite.
4. **Move commit (normal commit):** app files out of `venv/` into
   `archive/dashboard/`; original notebooks into `archive/notebooks/`;
   `PROJECT_REVIEW.md` and `PROJECT_DATA_FINDINGS.md` retained at root. Remove
   the remaining generated environment files from the staging clone only.
5. **History rewrite:** `git filter-repo` purges `venv/` across all history in
   the disposable clone. Files moved in step 4 keep their new paths. Verify
   refs, object integrity, required files, absence of `venv/`, and repository
   size against the preflight manifest.
6. **Remote checkpoint:** `git filter-repo` removes `origin` by design. Re-add
   only the exact recorded GitHub URL, fetch the current remote branch, and
   prove it has not changed since preflight. Then stop for explicit user
   approval before a guarded `--force-with-lease` push.
7. **Fresh-clone verification:** after an approved push, clone GitHub into a
   new adjacent directory, create the Linux `.venv`, install the UTF-8 pinned
   requirements, and run repository-layout tests. The old clone remains
   untouched and archive-only.

## Slice 2 — Data platform & scraper

### Tidy Parquet tables (`data/parquet/`)

| Table | Grain | Source |
|---|---|---|
| `team_games` | team-game, 1983-84 → 2025-26 | nba_api (existing 81k rows + seven new seasons fetched) |
| `games` | game (home/away paired) + attendance | derived from `team_games` + b-ref attendance |
| `franchises` | franchise-name-era (~45 rows) | hand-built; stable `franchise_id` across renames/relocations |
| `venues` | venue/team era + capacity provenance | curated exceptions + non-COVID empirical validation |
| `rosters` | player-team-season, 1984–2020 | parsed from the 1,050 existing roster CSVs |
| `leaders` | team-season-category leader | parsed from 1,044 xlsx workbooks: `"Trae Young 653 (2nd)"` → player/value/rank |

Engineered features (cumulative averages, Elo, rest days) are regenerated by
code, never stored. `computed_avgs*` trees, duplicate transformed CSVs, and
joblib models exist only in the NAS archive afterward.

### Scraper package (`src/nba_data/`)

- `hoopr.py` — provider adapter over the sportsdataverse hoopR-nba-data
  GitHub parquet files (team box scores + schedules, seasons 2019-20 →
  2025-26), downloaded once and cached under `data/raw/`; normalizes ESPN
  identifiers to `franchise_id` via a date+team mapping and filters to
  completed regular-season games between mapped franchises (excludes
  All-Star and play-in rows).
- `fetch_attendance.py` — b-ref month pages (~7/season), `requests` with real
  UA, 12 s spacing, HTML and request metadata cached to disk before parsing.
  A failed access check stops with diagnostics; it does not silently switch
  sources or bypass the block.
- `build.py` — raw → Parquet with hard validation gates: no duplicate
  `GAME_ID`s; per-season game counts match known values (1,230 normal; 1,059
  for 2019-20; 1,080 for 2020-21); attendance join coverage ≥95% per season
  (reported); score sanity ranges. Build fails loudly rather than writing bad
  tables.
- CLI: `python -m nba_data update --season 2025-26`.

Before the ingestion plan is written, run one-season online smoke tests against
both providers and save representative cached responses. In particular,
determine whether a basketball-reference blank means unknown or zero; blanks
must never be normalized to zero without source evidence. Store
`attendance_status` (`reported_zero`, `reported_positive`, `missing`, or
`not_applicable`) separately from numeric attendance.

Seasons to fetch: **2019-20 → 2025-26 (seven seasons)**, including a full
re-fetch of 2022-23 because the existing copy stops in mid-December 2022. The
earlier 2020-21 start was a gap: the canonical local data ends in 2018-19, and
2019-20 contains the bubble treatment required by the analysis.

### Tests (pytest)

- Franchise mapping covers every distinct team name present in the data.
- Leaders parser against real quirky strings (`DeAndre' Bembry 82 (1st)`,
  `.560` percentages, missing ranks).
- Validation gates fire on seeded bad input (duplicate game, wrong count).

## Slice 3 — COVID home-court advantage analysis

**Question:** How much of NBA home-court advantage is the crowd — and what did
removing crowds do to outcomes, scoring, and refereeing?

### Identification strategy (three natural experiments)

1. **2019-20 bubble** — neutral site: removes crowd + ordinary travel + home
   arena. Analyze the
   [88 regular-season seeding games](https://www.nba.com/nuggets/news/nba-schedule-release-20200626)
   separately from both pre-shutdown games and playoffs.
2. **2020-21 empty home arenas** — removes only the crowd.
3. **2020-21 within-season variation** — arenas ranged 0–50% capacity, varying
   by city and month, enabling fixed-effects identification against
   season-wide confounders.

Interpretive decomposition: the bubble contrast combines crowd, travel, arena
familiarity, and restart-context changes; the empty-home-arena contrast is the
closer crowd comparison. Their difference is reported as suggestive rather than
as a literal causal subtraction.

### Cohort and features (built from Parquet tables)

- **Scope:** regular season only. The full 1983-84 → 2025-26 panel supports
  descriptive trends; the pre-specified primary comparison window is 2017-18
  → 2021-22 so the COVID seasons are bracketed by nearby normal seasons.
- **Primary crowd treatment:** `empty` versus `spectators_present`, available
  only when attendance status is known. `missing` remains missing. Bubble
  games are `neutral_site`, not empty home games.
- **Secondary dose:** attendance rate and descriptive buckets (`empty`,
  `partial` <50%, `higher_capacity` ≥50%). Capacity comes from a versioned
  `venues` table keyed to arena/team era with source/provenance and explicit
  exceptions. A non-COVID venue-era p99/max is a validation aid, not an
  unquestioned franchise-wide capacity proxy.
- **Elo** computed over the full 1983–2026 sequence: K=20, seasonal mean
  reversion toward 1505, no future leakage.
- **Rest:** days since last game + back-to-back flags, both teams.

### Layer 1 — Descriptive

Home win % and home margin by season 1984–2026 (the secular decline of HCA);
crowd-level buckets across 2019-20 / 2020-21 / 2021-22; bubble vs empty vs
full side-by-side; home−away foul differential by crowd level (referee test).

### Layer 2 — Quasi-experimental

The primary estimand is the change in expected home margin for comparable
non-neutral regular-season games played in an empty arena rather than with
spectators present. The game-level model uses an explicit positive-for-home
`home_margin`, empty-arena treatment, Elo and rest differences, home-team and
away-team effects, and season-month effects. Inference uses team-season
clusters plus a cluster bootstrap (2,000 replicates, fixed seed) as a
robustness check.

A separately labeled team-game home-status × crowd-condition interaction model
is the difference-in-differences robustness specification. The bubble is a
separate neutral-site contrast, not another level of the home-arena treatment.
Attendance-rate/event-study variants are sensitivity analyses because arena
reopening and attendance were not randomly assigned. Secondary outcomes are
home win, home-minus-away personal fouls, and home-minus-away free-throw
attempts; all sign conventions are fixed in the data dictionary. Implemented
with statsmodels/linearmodels.

### Deliverable

`analysis/covid_hca/report.md` (~8 committed PNG figures) backed by
`analysis/covid_hca/analysis.ipynb` that regenerates everything from Parquet.
Pytest toy-fixture coverage for Elo, crowd bucketing, and DiD design-matrix
construction.

### Stated limitations

2020-21 crowd policies were not randomly assigned (correlated with local
politics/COVID rates; mitigated but not eliminated by within-season,
within-team variation). Accordingly, the report calls the headline result a
quasi-experimental estimate rather than a definitive causal fraction.
Attendance is announced, not turnstile. Bubble games also differ in season
context (restart after hiatus, playoff seeding stakes).

## Success criteria

1. Fresh clone of the rewritten repo is <100 MB including Parquet data and
   passes `pytest` after `pip install -r requirements.txt`.
2. `games` table covers 1983-84 → 2025-26 with per-season validation gates
   green. At least 95% of post-2019 games join to a source schedule row;
   numeric-attendance and documented-no-fans coverage are reported separately,
   with no blank-to-zero imputation.
3. Report states a headline crowd effect in points with a confidence interval,
   the bubble-vs-empty decomposition, and the referee-channel result — each
   backed by a figure and a regression table reproducible from the notebook.

## Slices are independent deliverables

Each slice produces working, verifiable output on its own and gets its own
implementation plan: Slice 1 (repo), Slice 2 (data), Slice 3 (analysis).
Slice 1 must complete before Slice 2 (the rewrite moves paths the pipeline
reads); Slice 2 before Slice 3 (the analysis needs the new seasons).
