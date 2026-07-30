# Playoff Game Results (1947–2026) — Design

Date: 2026-07-30
Status: Approved for implementation planning

## Purpose

Publish the BAA/NBA games intentionally excluded from the two regular-season
tables but needed for a complete results archive:

- playoff series games;
- the 2020 play-in game and the 2021–2026 play-in tournaments;
- eight regular-season tiebreakers; and
- the one played game voided after a successful protest.

The result is a separate, labeled table with the same archival rigor as the
historical regular-season collection: raw source pages preserved locally and
on the NAS, offline deterministic rebuilds, independently curated counts,
cross-source validation where available, and verified snapshot/restore
support.

The driving goal is preservation. The project acquires the missing source
pages while they remain available without changing any existing modeling
input or regular-season row.

## Current state (verified 2026-07-30)

- `data/parquet/games.parquet` contains 49,117 regular-season rows for ending
  seasons 1984–2026.
- `data/parquet/historical_games.parquet` contains 19,405 regular-season rows
  for ending seasons 1947–1983.
- The checksum-verified pages in `data/raw/historical_bref/` already contain
  1,231 playoff games, eight tiebreakers, and one voided game that do not
  appear in either published table. The tiebreakers are two games in each of
  1948, 1950, 1956, and 1957. The voided game is
  `bref-195211280MLH` in ending season 1953.
- The legacy 1984–2019 source is regular-season only, and
  `data/raw/bref/` has no pre-2020 pages. The 1984–2019 playoffs therefore are
  not archived locally.
- The raw hoopR schedules already contain 590 playoff rows
  (`season_type == 3`) and 37 play-in rows (`season_type == 5`) for
  2020–2026. This includes the 2020 Portland–Memphis play-in game plus six
  play-in games in each ending season 2021–2026.
- `data/raw/bref/` has partial 2020–2026 postseason coverage. Its August 2020,
  May 2021, and April 2022–2026 pages contain every BREF row marked
  `Play-In Game` and some playoff rows, but the cache does not contain every
  playoff month.
- The modern monthly BREF pages have no in-table `Playoffs` separator. The
  historical separator algorithm therefore cannot be generalized to modern
  monthly pages.
- Basketball Reference publishes one dedicated playoff schedule page per
  season, using URLs such as
  `https://www.basketball-reference.com/playoffs/NBA_1984_games.html`.

## Decisions

1. **Separate publication.** The new products are
   `data/parquet/playoff_games.parquet` and
   `data/parquet/playoff_games_manifest.json`. Existing tables do not gain
   playoff-shaped or fabricated modern columns.
2. **Hybrid BREF source model.**
   - Ending seasons 1947–1983 use the already archived monthly pages under
     `data/raw/historical_bref/` and their verified historical boundary rules.
   - Ending seasons 1984–2026 use one dedicated BREF playoff schedule page per
     season.
   - Play-in rows use the seven already cached BREF monthly pages that carry
     the exact `Play-In Game` remark.
   - hoopR is an independent 2020–2026 validator, not a publication source.
     stats.nba.com is not used.
3. **A dedicated modern raw cache.** New playoff pages and verified,
   provenance-preserving copies of the seven play-in-bearing pages live under
   `data/raw/playoff_bref/`. They never enter `data/raw/bref/`.
4. **One labeled table.** `game_type` is exactly one of `playoff`,
   `play_in`, `tiebreaker`, or `voided`. Only these four explicitly scoped
   classes enter the table; exhibitions, All-Star games, and other
   non-standings events remain out of scope.
5. **One implementation project.** All eras share the same row parser,
   franchise resolver, schema, validator, publisher, and recovery contract.
   Era-specific code is limited to source selection and historical boundary
   evidence.

## Source acquisition and cache ownership

### Historical input

`data/raw/historical_bref/` remains byte-identical and is never re-fetched by
this project. The playoff manifest records the exact historical body hashes it
consumes and pins the committed
`historical_games_manifest.json` SHA-256 as a recovery dependency.

### New playoff cache

`data/raw/playoff_bref/` contains:

- 43 newly fetched dedicated playoff pages, one for every ending season
  1984–2026, with UTF-8 JSON sidecars; and
- seven play-in-bearing monthly bodies seeded without network access from
  verified cache pairs:
  - `NBA_2020_games-august.html`;
  - `NBA_2021_games-may.html`; and
  - `NBA_2022_games-april.html` through
    `NBA_2026_games-april.html`.

The seeded sidecars record the immutable request URL, body SHA-256, original
cache path, provider, resource type, ending season, and seed operation. A
source body is copied only after its original body/sidecar pair passes the
existing cache validator.

The dedicated playoff URL is deterministic:

```text
https://www.basketball-reference.com/playoffs/NBA_{season}_games.html
```

No index or regular-season month crawl is required for 1984–2026.

### Access policy

`fetch-playoffs` is the only network path. It follows the repository's current
Basketball Reference policy:

- ordinary TLS verification;
- the existing browser-identifying User-Agent;
- no request more often than every 12 seconds;
- no parallel requests;
- validation and atomic cache publication before continuing; and
- immediate stop on HTTP, TLS, rate-limit, block-page, or schema failure.

The fetch is resumable from complete verified pairs. A missing half, checksum
mismatch, metadata mismatch, duplicate resource identity, symlink, special
file, or unreferenced cache entry fails validation.

Before the bulk fetch, a source spike validates the dedicated page contract
for 1984, 2002, 2020, and 2026. All four pages must parse through one adapter
and expose the required schedule columns, valid box-score links, final scores,
overtime, venue, and attendance semantics.

## Parser and classifier boundaries

Shared, source-neutral BREF schedule parsing moves into a focused module:

```text
src/nba_data/bref_schedule.py
    validate and parse one BREF schedule table
    normalize source values and assign stable BREF game IDs

src/nba_data/historical_bref.py
    retain historical fetch and regular-season boundary behavior
    call the shared row parser without changing historical output

src/nba_data/playoff_bref.py
    seed/fetch/validate the dedicated playoff cache
    select and classify the four scoped game types

src/nba_data/playoff_build.py
    resolve franchises, validate the panel, and publish the artifact pair
```

The parser refactor is behavior-preserving for the historical pipeline.
Before-and-after hashes of `historical_games.parquet` and
`historical_games_manifest.json` must match.

Classification is deterministic:

1. An exact case-insensitive
   `Protest; does not count toward record` remark is `voided`.
2. An exact case-insensitive `Tiebreaker` remark is `tiebreaker`.
3. An exact case-insensitive `Play-In Game` remark in ending seasons
   2020–2026 is `play_in`.
4. Every completed row on a validated dedicated BREF playoff page is
   `playoff`.
5. For 1947–1983 monthly pages, all remaining rows in the already verified
   postseason region are `playoff`. This reuses the curated 1947/1980
   boundaries and the four verified duplicate-separator layouts, including
   the 1953 carryover regions.
6. Every other parsed row is regular season and is excluded.

The explicit remark rules take precedence over the historical postseason
region, because several tiebreakers occur between duplicate `Playoffs`
separators. After precedence is applied, each published game must have exactly
one type. A new unrecognized remark at a scoped boundary fails the build
instead of being guessed.

The verified historical complement also contains 59 known, non-type
annotations. They are an audited exact allowlist rather than an open-ended
pattern:

- 44 explicit alternate-location remarks: `at Saint Paul, MN`,
  `at 69th Regiment Armory`, `at Madison, WI`, `at Indianapolis, IN`,
  `at Oakland, CA`, `at Chicago, IL (Chicago Arena)`,
  `at Washington, DC (National Guard Armory)`, `at Daly City, CA`,
  `at Kingdome`, `at Elkhart, IN`,
  `at Detroit, MI (University of Detroit Fieldhouse)`, and `at Tempe, AZ`;
- 11 `Round Robin` annotations; and
- one each of `Havlicek Stole the Ball`, `The Willis Reed Game`,
  `The Greatest Game Ever Played`, and `Mother's Day Massacre`.

The alternate-location allowlist is an exact neutral-site source signal. The
format/nickname allowlist is non-site annotation and therefore does not turn a
visitor/home result into an unknown-site game. Matching is case-insensitive
after trimming; any other nonempty historical complement remark fails.

Game-type classification and site classification are independent. In
particular, `Play-In Game` is not a neutral-site signal.

## Output schema and publication

`data/parquet/playoff_games.parquet` contains one row per scoped game, ordered
by `season, game_date, game_id`, with the historical results schema plus
`game_type`:

- `game_id` (`bref-` plus the box-score slug, with the existing deterministic
  date/team/ordinal fallback when no valid box-score link exists);
- `season`, `league`, `game_date`, `game_type`;
- `away_team_name`, `home_team_name`, `away_franchise_id`,
  `home_franchise_id`;
- `away_pts`, `home_pts`, `home_margin`, `overtime_periods`;
- `site_status`, `venue_name`, `attendance`, `attendance_status`;
- `boxscore_url`, `schedule_url`.

Blank venue and attendance fields remain null. Reported attendance zero stays
zero with `attendance_status == "reported_zero"`. Missing attendance is never
converted to zero.

The existing franchise reference is authoritative. Every source team must
resolve to exactly one season-valid franchise. An unknown or ambiguous team
fails the build; this project does not silently modify franchise identity or
the current regular-season dimensions.

`site_status` retains the historical meanings `home`, `neutral`, and
`unknown`. Source visitor/home columns determine score orientation, not site
status. Only an exact neutral-site source signal or a provenance-backed
playoff site rule can produce `neutral`. Non-site remarks do not automatically
produce `unknown`.

All 84 scoped 2020 games occurred at the neutral Disney restart site. A
tracked, provenance-backed 2020 cohort rule classifies those 83 playoff games
and one play-in game as `neutral`; validation requires exactly 84 matches.

The table and manifest publish as one atomic pair. Validation failure leaves
the prior pair untouched. The manifest contains:

- every consumed raw body path, URL, resource type, and SHA-256;
- the pinned historical manifest SHA-256;
- every reference-file SHA-256;
- dependency versions;
- exact schema, row count, season range, and output SHA-256;
- per-season and per-type counts;
- site, box-score-link, venue, and attendance coverage; and
- deterministic per-season row hashes.

Fetch timestamps, absolute checkout paths, and other machine-specific values
do not enter the deterministic manifest.

## Reference data

`data/reference/playoff_season_counts.csv` has exactly one row for each ending
season 1947–2026 and these columns:

```text
season,playoff_games,play_in_games,tiebreaker_games,voided_games,
total_games,first_postseason_date,last_postseason_date,source_url,note
```

The four type counts must sum to `total_games`. Count and date-window
provenance must be independent of the BREF schedule rows being validated;
league playoff summaries or another documented historical record may supply
it. The modern known edge counts are fixed: one play-in game in 2020, six in
each season 2021–2026, two tiebreakers in each of 1948/1950/1956/1957, and one
voided game in 1953.

`data/reference/playoff_site_rules.csv` has these columns:

```text
season,site_status,expected_matches,source_url,note
```

Its single approved row records the 2020 neutral-site cohort and requires
exactly 84 matches. Reference files are included in the playoff manifest but
excluded from the existing regular-season manifests.

## Validation and acceptance gates

The build fails unless all of the following hold:

1. Ending seasons are exactly 1947–2026; league is `BAA` for 1947–1949 and
   `NBA` afterward.
2. Actual counts match every per-season and per-type reference count, including
   the fixed edge counts above.
3. Every 2020–2026 BREF `playoff`/`play_in` row equals the hoopR
   `season_type` 3/5 set in both directions after franchise normalization.
   The equality key is season, date, type, away franchise, home franchise,
   away score, and home score. The expected cross-source total is 627 rows.
4. Game IDs are non-null and globally unique. Dates, leagues, source teams,
   franchise IDs, scores, and `game_type` are non-null.
5. Teams and franchises differ, scores are nonnegative, final margins are
   nonzero, `home_margin` equals `home_pts - away_pts`, and overtime periods
   are nonnegative and agree with the source marker.
6. Every `playoff`, `play_in`, and `tiebreaker` row falls within its curated
   season window. The lone `voided` exemption must be exactly
   `bref-195211280MLH` on 1952-11-28; no general window exemption exists.
7. Attendance values and statuses agree, site values are supported, and the
   2020 neutral-site rule matches exactly 84 rows.
8. No published row overlaps either regular-season table on the canonical game
   identity `(season, game_date, away_franchise_id, home_franchise_id,
   away_pts, home_pts)`. A game-ID-only comparison is insufficient because the
   modern table uses a different ID namespace.
9. Historical boundary evidence is unchanged, all copied play-in pages match
   their original body hashes, and raw body/sidecar inventories match the
   manifest exactly.
10. Two consecutive offline builds reproduce both playoff artifacts
    byte-for-byte.
11. Unit tests use committed small fixtures and never access the network.
    Integration tests use only verified local caches.

### Non-interference

The regular-season algorithms and row sets do not change. Minimal publication
plumbing must nevertheless recognize the new pair:

- the modern atomic directory swap preserves both the complete historical pair
  and the complete playoff pair, rejecting an orphan or non-regular file;
- the modern raw snapshot excludes `/playoff_bref/`, just as it excludes
  `/historical_bref/`; and
- modern raw provenance ignores `/playoff_bref/`.

A deterministic baseline/check test records and then compares the hashes of:

```text
data/parquet/build_manifest.json
data/parquet/team_games.parquet
data/parquet/games.parquet
data/parquet/franchises.parquet
data/parquet/venues.parquet
data/parquet/historical_games.parquet
data/parquet/historical_games_manifest.json
```

All seven files must remain byte-identical after parser refactoring, cache
seeding, playoff publication, a modern rebuild, and a historical rebuild.

## CLI

The source-checkout CLI adds:

```text
python -m nba_data fetch-playoffs --season 1984
python -m nba_data fetch-playoffs
python -m nba_data build-playoffs
```

`fetch-playoffs` accepts ending seasons 1984–2026, validates and seeds the
applicable cached play-in page, and fetches only missing dedicated playoff
pages. `build-playoffs` is strictly offline and requires the complete verified
historical and playoff cache inventories.

## Archival and recovery

The new cache has its own NAS snapshot family under
`/mnt/nas/llm/documents/nba-predictions-archive/`:

```text
playoff-raw-2026-<playoff-manifest-sha12>/
    playoff_bref/
    playoff_games_manifest.json
    SHA256SUMS
```

The playoff snapshot contains only the new `playoff_bref` cache. The existing
historical snapshot remains authoritative for `historical_bref`.

`scripts/archive_playoff_raw_snapshot.sh` and
`scripts/restore_playoff_raw.sh` are exact inverses. They:

- pin and recheck the trusted committed playoff manifest during the operation;
- validate the complete body/sidecar inventory against that manifest;
- reject symlinks, special files, incomplete pairs, unexpected files, and
  existing destinations;
- validate before and after copying to detect source or snapshot races;
- stage on the destination filesystem and publish atomically; and
- verify the historical cache against the historical manifest SHA-256 pinned
  by the playoff manifest.

If `historical_bref` is absent, the operator first runs the existing historical
restore and then the playoff restore. The playoff restore never overwrites or
silently repairs the historical cache.

## Non-goals

- No round, seed, or series metadata.
- No ABA games.
- No box-score statistics beyond the schedule result fields.
- No exhibitions, All-Star games, or NBA Cup classification.
- No changes to modeling inputs or analysis outputs.
- No changes to the schemas or row sets of `games.parquet`,
  `team_games.parquet`, or `historical_games.parquet`.
- No bulk acquisition of 1984–2019 regular-season monthly BREF pages.

## Implementation shape

This remains one implementation plan because the parser, cache contract,
classifier, schema, validator, publisher, and recovery path are shared.
Implementation proceeds in this order:

1. add the deterministic non-interference baseline and representative source
   fixtures;
2. extract the shared BREF row parser while proving historical byte identity;
3. implement dedicated playoff fetch and verified play-in cache seeding;
4. implement classification, reference loading, build validation, and atomic
   publication;
5. add modern publisher/snapshot preservation seams and CLI commands;
6. run the representative source spike, then the full rate-limited fetch;
7. curate independent counts, run the full BREF/hoopR reconciliation, and
   publish deterministic artifacts; and
8. create and independently restore-verify the NAS snapshot.
