# Historical BAA/NBA Game Results and Home-Court Trends — Design

*Approved in conversation on 2026-07-29.*

## Goal

Extend the repository's results-level game history from its current 1983-84
start back through the inaugural 1946-47 BAA season, then use the continuous
1946-47 through 2025-26 panel to describe long-run changes in home-court
advantage.

The first delivery is intentionally narrower than a complete historical
box-score archive. It establishes trustworthy game identities, dates, teams,
scores, site classifications, and provenance while retaining every available
box-score link for richer future projects.

## Scope

The initial historical extension includes:

- BAA regular seasons ending 1947 through 1949;
- NBA regular seasons ending 1950 through 1983; and
- a descriptive union with the existing NBA game panel ending 1984 through
  2026.

It excludes ABA games, playoffs, exhibitions, All-Star games, and other
non-standings events. The existing modern `games.parquet` and
`team_games.parquet` contracts remain unchanged.

The primary product is a separate results-level table. Pre-1984 games do not
receive fabricated modern box-score fields and are not inserted into
`team_games.parquet`.

## Source and access policy

Basketball Reference league schedule pages are the initial source. Its
published archive contains schedule/result and box-score links back to the
1946-47 BAA season, including the
[inaugural BAA schedule](https://www.basketball-reference.com/leagues/BAA_1947_games.html)
and the
[first recorded game](https://www.basketball-reference.com/boxscores/194611010TRH.html).

Collection follows the repository's existing Basketball Reference policy:

- ordinary TLS verification;
- a browser-identifying User-Agent;
- one request no more often than every 12 seconds;
- no parallel requests;
- every response cached before parsing;
- immediate stop on HTTP, TLS, rate-limit, block-page, or schema failure; and
- no CAPTCHA, Cloudflare, or other access-control bypass.

The 12-second interval is below Sports Reference's published limit of 20
requests per minute for its non-FBref sites. Its
[bot-traffic policy](https://www.sports-reference.com/bot-traffic.html) and
[data-use policy](https://www.sports-reference.com/data_use.html) remain
operational constraints.

The collector fetches league schedule/index pages and their historical month
pages only. It does not fetch every individual box score in the initial
delivery. Box-score URLs are preserved for the later rich-statistics phase.

## Source spike

Before implementing the all-season fetch, run an online spike against:

- 1946-47, to exercise the inaugural BAA schema and defunct teams;
- 1959-60, to exercise a mid-century NBA schema; and
- 1982-83, to exercise the boundary with the existing panel.

The spike must establish:

1. the exact schedule/index and month-link discovery rules;
2. the regular-season/playoff boundary;
3. team-name and team-link behavior;
4. score and overtime parsing;
5. box-score-link coverage;
6. venue and attendance availability;
7. explicit or inferable neutral-site signals; and
8. exact expected game totals for the three sample seasons.

The full collection does not begin until all three seasons parse through one
adapter and every ambiguous field has an explicit missingness rule.

## Raw cache and provenance

Historical source files live under:

```text
data/raw/historical_bref/
```

Each HTML body has one UTF-8 JSON sidecar containing:

- immutable request URL;
- provider and resource type;
- league and ending season;
- month when applicable;
- response SHA-256; and
- fetch timestamp.

Bodies and sidecars are published atomically through the existing cache
utilities. Missing halves, checksum mismatches, duplicate resource identities,
and unreferenced cache files fail the build.

The committed publication consists of:

```text
data/parquet/historical_games.parquet
data/parquet/historical_games_manifest.json
```

The historical manifest is separate from the existing Slice 2A manifest so
the historical table and raw-snapshot recovery contract can be rebuilt and
restored independently. It records every source page, checksum, row count,
schema, coverage measure, reference-file hash, package version, and output
hash.

Expanding the shared franchise dimension is an explicit one-time update to
`data/reference/franchises.csv`, its packaged runtime copy,
`franchises.parquet`, and the existing Slice 2A manifest. The modern
`games.parquet` and `team_games.parquet` row sets and schemas do not change.

After publication, the complete historical raw cache receives its own verified
NAS snapshot. Restore and archive scripts must be exact inverses and must
verify a complete `SHA256SUMS` inventory.

## Historical game schema

`historical_games.parquet` has one row per BAA/NBA regular-season game:

| Column | Type | Meaning |
|---|---|---|
| `game_id` | string | Stable `bref-` identifier derived from the box-score slug when available, otherwise a deterministic date/team/ordinal fallback |
| `season` | int64 | Ending season, 1947 through 1983 |
| `league` | string | `BAA` for 1947-1949; `NBA` for 1950-1983 |
| `game_date` | date | Scheduled game date |
| `away_team_name` | string | Source-era away/visitor name |
| `home_team_name` | string | Source-era home/designated-home name |
| `away_franchise_id` | string | Stable organizational lineage |
| `home_franchise_id` | string | Stable organizational lineage |
| `away_pts` | int64 | Final away score |
| `home_pts` | int64 | Final home score |
| `home_margin` | int64 | `home_pts - away_pts` |
| `overtime_periods` | int64 | Zero for regulation, otherwise parsed overtime count |
| `site_status` | string | `home`, `neutral`, or `unknown` |
| `venue_name` | nullable string | Reported arena/site |
| `attendance` | nullable int64 | Reported attendance |
| `attendance_status` | string | `reported_positive`, `reported_zero`, or `missing` |
| `boxscore_url` | nullable string | Preserved rich-statistics source link |
| `schedule_url` | string | Schedule page that produced the row |

Blank attendance and venue values remain null. They are never normalized to
zero or inferred from another game.

When a schedule row contains no box-score link, its fallback game ID is built
from normalized source values and a deterministic within-date matchup ordinal.
The manifest reports box-score-link coverage by season.

## Franchise identity

`data/reference/franchises.csv` expands to cover every BAA/NBA source team
name through 1983 while preserving its current schema and modern mappings.
The synchronized package asset under `src/nba_data/reference/` must remain
byte-identical, and the existing runtime-reference gate must reject divergence.

Surviving lineages map through relocations and renames to their existing stable
IDs, for example Minneapolis Lakers to `LAL`. Defunct organizations receive
permanent, unambiguous historical IDs. When a historical source abbreviation
would collide with a later unrelated franchise, the historical ID includes an
era qualifier rather than overloading the modern lineage.

Every alias has inclusive ending-season bounds sufficient to resolve it
uniquely. Tests enumerate every distinct source name in the three-season spike
and then in the complete raw cache. Unknown and multiply matched aliases fail
the build.

Expected regular-season counts live in a curated
`data/reference/historical_season_counts.csv` with one row per ending season
and a cited source URL. The build never learns its expected totals from the
same rows it is validating.

## Home and neutral-site classification

The schedule's visitor and designated-home columns establish score
orientation. `site_status` is classified independently:

1. an explicit neutral-site source signal produces `neutral`;
2. a provenance-backed correction ledger may classify a documented historical
   neutral game;
3. a normal designated-home row with no conflicting neutral signal produces
   `home`; and
4. an unresolved or contradictory row produces `unknown`.

Corrections live in a small tracked reference ledger keyed by `game_id`, with
the selected status, source URL, and explanatory note. Corrections cannot
alter teams, dates, or scores.

Primary home-court summaries use only `site_status == "home"`. Neutral and
unknown games remain published and receive separate counts; neither is
silently included in the home sample.

## Parser and build boundaries

Historical ingestion is isolated from the current modern adapters:

```text
src/nba_data/historical_bref.py
    discover and fetch schedule pages
    validate cached historical HTML
    parse source rows into a normalized historical frame

src/nba_data/historical_build.py
    reconcile duplicates
    resolve franchises and site corrections
    validate season and panel contracts
    stage and publish Parquet plus manifest
```

The CLI exposes separate offline-safe commands:

```text
python -m nba_data fetch-historical --season 1947
python -m nba_data fetch-historical
python -m nba_data build-historical
```

`fetch-historical` is the only network path. `build-historical` reads verified
caches only and publishes through a staged atomic directory/file replacement.
A failed validation leaves the previous publication intact.

## Publication gates

The build fails unless all of the following hold:

- ending seasons are exactly 1947 through 1983;
- league is `BAA` for 1947-1949 and `NBA` afterward;
- every row is a regular-season game;
- every season matches its independently curated expected game count;
- every game ID is non-null and globally unique;
- date, league, source teams, franchise IDs, and scores are non-null;
- home and away teams/franchises differ;
- scores are nonnegative and `home_margin` matches the final score;
- overtime counts are nonnegative and agree with the parsed source marker;
- every source team resolves exactly one season-valid franchise;
- attendance is nonnegative when reported and its status agrees with its
  value;
- every site status is one of the three allowed values;
- every correction references exactly one published game and includes
  provenance;
- the maximum historical ending season is 1983 and the minimum modern ending
  season is 1984;
- source body/sidecar inventories and hashes match exactly; and
- two consecutive builds produce byte-identical artifacts.

The manifest reports, for every season:

- total games;
- home, neutral, and unknown site counts;
- box-score-link coverage;
- venue coverage;
- raw numeric attendance coverage; and
- final output hash contribution.

No minimum attendance or venue coverage is imposed on early seasons. Missing
historical reporting is measured rather than converted into a publication
failure.

## Historical home-court analysis

A small analysis adapter selects compatible fields from
`historical_games.parquet` and the current `games.parquet`, then concatenates
them into an in-memory results panel. It does not rewrite either source table.

The initial historical analysis publishes:

- games and usable-site coverage by season;
- designated-home win percentage;
- mean and median home margin;
- neutral and unknown-site counts;
- 95% Wilson intervals for home win percentage;
- fixed-seed home-franchise cluster-bootstrap intervals for mean margin; and
- game-weighted trailing five-season trends for win percentage and margin.

The figure spans 1946-47 through 2025-26 and marks major league/rule eras for
interpretation. Era markers are descriptive annotations, not causal
interventions.

The historical output becomes the opening context layer of the COVID
home-court report. The primary COVID estimand and fixed-effects models remain
restricted to the previously approved modern comparison window; difficulty in
the historical extension cannot broaden or weaken that causal design.

Generated deliverables live under:

```text
analysis/covid_hca/data/home_court_by_season.csv
analysis/covid_hca/figures/home_court_trend_1947_2026.png
analysis/covid_hca/historical_trend.md
```

## Future rich-statistics phase

The initial table is also a durable box-score index. A later, separately
approved phase may fetch the preserved links and publish:

- `historical_stat_availability.parquet`;
- era-aware team box scores; and
- historical player-game box scores where the source supports them.

That phase first measures per-field completeness by season. It may use
separate schemas for team and player grains, and it never fills unavailable
historical statistics with zero. The results-only table remains authoritative
for dates, participants, scores, and source links.

SportsDataverse hoopR data is not a pre-1984 source; its published NBA
coverage begins in 2002. Unverified bulk datasets may be used for discrepancy
discovery, but not as publication sources without a separate provenance and
license review.

## Tests

Tests use committed small fixtures and never perform network requests.

Required coverage includes:

- one parser fixture each for 1946-47, 1959-60, and 1982-83;
- schedule index and month-link discovery;
- regular-season filtering;
- score, overtime, attendance, and blank-field semantics;
- box-score IDs and deterministic fallback IDs;
- surviving and defunct franchise mappings;
- neutral, corrected, home, and unknown site classifications;
- orphan and contradictory corrections;
- incomplete or checksum-invalid cache pairs;
- wrong/missing season counts and duplicate games;
- deterministic ordering, manifest contents, and failed-publication rollback;
- the 1983/1984 union boundary;
- home-margin orientation and neutral/unknown exclusion;
- season aggregation and rolling-window behavior; and
- a cached integration test for every collected historical season.

## Acceptance criteria

The historical extension is complete when:

1. the three-season spike is documented and all ambiguities are resolved;
2. all 37 BAA/NBA regular seasons ending 1947-1983 are cached and validated;
3. `historical_games.parquet` contains exactly the curated expected number of
   unique games with no invented statistics;
4. source and correction provenance are complete and machine-readable;
5. the second offline build is byte-identical to the first;
6. the historical raw snapshot is verified and recoverable from NAS;
7. the default test suite passes with no skipped cached integration tests; and
8. the season summary, figure, and written historical trend are reproducible
   from the two committed game tables.
