# Game-data dictionary

This is the contract for the two committed regular-season result publications
and the separate postseason-results publication.
`historical_games.parquet` contains 19,405 BAA/NBA games for ending seasons
1947–1983. The modern publication begins at ending season 1984 and runs
through 2026 with 49,117 `games` rows and 98,234 `team_games` rows.
`playoff_games.parquet` contains 4,652 BAA/NBA postseason rows for ending
seasons 1947–2026 and is not an input to either regular publication, the
models, or the analysis outputs. A season number is always its ending year, so
the regular handoff has no overlap or gap and `1947` means 1946–47.

The committed [historical manifest](../data/parquet/historical_games_manifest.json),
[modern build manifest](../data/parquet/build_manifest.json), and
[playoff manifest](../data/parquet/playoff_games_manifest.json) record the
exact row counts, table hashes, verified relative source paths and SHA-256
values, validation results, reference hashes, and dependency versions. The
historical and playoff table metadata also record their exact ordered Arrow
schemas shown below. Each manifest is authoritative for its publication if
this page and an artifact ever differ.

Historical collection and classification decisions are in the approved
[historical results design](superpowers/specs/2026-07-29-historical-game-results-design.md)
and the verified
[historical source spike](audits/2026-07-29-historical-bref-source-spike.md).
The independent validation inputs are the
[season-count reference](../data/reference/historical_season_counts.csv) and
[site-correction ledger](../data/reference/historical_site_corrections.csv).
The resulting descriptive series is documented in the
[historical trend report](../analysis/covid_hca/historical_trend.md).
Sports Reference's
[bot-traffic policy](https://www.sports-reference.com/bot-traffic.html) and
[data-use policy](https://www.sports-reference.com/data_use.html) govern
historical source access.

Modern decisions remain documented in the
[revival design](superpowers/specs/2026-07-29-nba-repo-revival-covid-hca-design.md),
the [Slice 2A plan](superpowers/plans/2026-07-29-slice2a-game-data-pipeline.md),
and the corrected
[modern source audit](audits/2026-07-29-slice2-source-spike.md).

Playoff collection and classification decisions are in the approved
[playoff-results design](superpowers/specs/2026-07-30-playoff-game-results-design.md)
and the verified
[playoff source spike](audits/2026-07-30-playoff-bref-source-spike.md).
Independent acceptance inputs are the
[playoff season-count reference](../data/reference/playoff_season_counts.csv)
and [2020 site-rule ledger](../data/reference/playoff_site_rules.csv).

## Tables and physical types

Types below are the actual Arrow/Parquet physical types (`large_string`,
`int64`, `bool`, and `date32[day]`), not inferred application types. Nullable
means missing values are represented as null; a nullable integer must never be
read as a zero-filled value.

### `team_games.parquet`

One canonical NBA box-score row per team per game; `game_id` has exactly two
rows, one `is_home=true` row and one `false` row.

| Column | Type | Meaning |
|---|---|---|
| game_id | large_string | Canonical NBA Stats `GAME_ID` |
| season | int64 | Ending season |
| source | large_string | `nba_stats` |
| franchise_id, opponent_franchise_id | large_string | Stable franchise codes |
| is_home | bool | NBA/schedule home designation |
| game_date | date32[day] | Game date |
| wl | int64 | Win (1) or loss (0) |
| pts, fga, fgm, fg3a, fg3m, fta, ftm, oreb, dreb, reb, ast, stl, blk, tov, pf | int64 nullable | Team box-score statistics |

### `games.parquet`

One paired game row, with final attendance, venue, and capacity enrichment.

| Column | Type | Meaning |
|---|---|---|
| game_id | large_string | Canonical NBA Stats `GAME_ID` |
| espn_game_id | large_string nullable | hoopR/ESPN ID prefixed `espn-`; absent for legacy |
| season | int64 | Ending season |
| source | large_string | `nba_stats` |
| game_date | date32[day] | Canonical game date |
| home_franchise_id, away_franchise_id | large_string | Stable franchise codes |
| home_pts, away_pts | int64 | Final points |
| neutral_site | bool | Schedule/corrected neutral designation |
| neutral_site_source | large_string | `hoopr`, a correction marker, or legacy assumption |
| venue_id, venue_name, venue_city | large_string nullable | Enriched venue identity/name/city |
| venue_source | large_string | `bref`, `espn`, or `legacy_unknown` |
| attendance_bref, attendance_espn | int64 nullable | Unselected provider-specific values |
| attendance | int64 nullable | Selected attendance under the precedence rule |
| bref_row_matched | bool | Whether a basketball-reference row matched |
| attendance_status | large_string | `reported_zero`, `reported_positive`, or `missing` |
| attendance_source | large_string | `bref`, `espn`, or `legacy` |
| attendance_coverage_exemption_reason | large_string nullable | Audited no-fan bubble exemption only |
| capacity | int64 nullable | Season-aware venue capacity |
| capacity_source_url, capacity_note | large_string nullable | Capacity provenance and exception note |

### `historical_games.parquet`

One results-level row per BAA/NBA regular-season game for ending seasons
1947–1983. These are the exact Arrow/Parquet columns and physical types:

| Column | Type | Meaning |
|---|---|---|
| game_id | large_string | Stable Basketball Reference-derived identifier |
| season | int64 | Ending season, exactly 1947 through 1983 |
| league | large_string | `BAA` for 1947–1949 and `NBA` for 1950–1983 |
| game_date | date32[day] | Scheduled game date |
| away_team_name | large_string | Source-era visitor name |
| home_team_name | large_string | Source-era designated-home name |
| away_franchise_id | large_string | Stable organizational-lineage code for the visitor |
| home_franchise_id | large_string | Stable organizational-lineage code for the designated home team |
| away_pts | int64 | Final visitor score |
| home_pts | int64 | Final designated-home score |
| home_margin | int64 | `home_pts - away_pts`, regardless of site classification |
| overtime_periods | int64 | `0` for regulation; otherwise the parsed overtime count |
| site_status | large_string | `home`, `neutral`, or `unknown` |
| venue_name | large_string nullable | Reported arena/site; null when the source cell is blank |
| attendance | int64 nullable | Reported attendance; null when the source cell is blank |
| attendance_status | large_string | `reported_positive`, `reported_zero`, or `missing` |
| boxscore_url | large_string nullable | Preserved rich-statistics source link when present |
| schedule_url | large_string | Verified schedule page that produced the row |

Team-name columns preserve the names printed for that source era; franchise-ID
columns normalize those names to stable organizational lineages. The names
therefore remain suitable for historical display while IDs support
longitudinal grouping. A game with a box-score link uses
`bref-<box-score-slug>` as its stable `game_id`. If the link is absent, the ID
is deterministically formed as
`bref-fallback-YYYYMMDD-<away-slug>-at-<home-slug>-NN`, using normalized
source values and a within-date matchup ordinal. Reordering input pages does
not change that fallback. Only an absent href uses the fallback: a present
empty, whitespace-only, or malformed box-score href is source drift and fails
parsing.

Visitor and designated-home columns always establish score orientation;
`site_status` separately describes the site evidence. `home` means the row
has the normal designated-home relationship and no conflicting neutral
signal. `neutral` requires an explicit source signal or provenance-backed
entry in the correction ledger. `unknown` preserves unresolved or
contradictory evidence. Primary home-court summaries include only `home`;
neutral and unknown games remain published and are counted separately.

A blank attendance cell is null with `attendance_status=missing`; a literal
source zero is retained as integer `0` with
`attendance_status=reported_zero`, never treated as a blank. A positive value
uses `reported_positive`. Blank venue cells remain null and are not inferred.
The historical manifest's per-season `boxscore_link_coverage`,
`venue_coverage`, and `attendance_raw_coverage` are respectively the fractions
of rows with non-null `boxscore_url`, `venue_name`, and `attendance`. These
coverage measures describe source completeness; they do not impute values.

The historical boundary is exactly ending seasons 1947–1983. The modern
`games.parquet` contract starts at ending season 1984. The historical product
contains schedule-level results and preserved box-score URLs only; collecting
rich historical team/player box scores is future separate scope requiring its
own design and review.

### `playoff_games.parquet`

One schedule-results row per scoped BAA/NBA postseason game for ending seasons
1947–2026. This table is independently published and is not joined into
`games.parquet`, `team_games.parquet`, or `historical_games.parquet`. These are
the exact Arrow/Parquet columns and physical types in publication order:

| Column | Type | Meaning |
|---|---|---|
| game_id | large_string | Stable Basketball Reference-derived identifier |
| season | int64 | Ending season, exactly 1947 through 2026 |
| league | large_string | `BAA` for 1947–1949 and `NBA` for 1950–2026 |
| game_date | date32[day] | Scheduled game date |
| game_type | large_string | Exact scoped classification defined below |
| away_team_name | large_string | Source-era visitor name |
| home_team_name | large_string | Source-era designated-home name |
| away_franchise_id | large_string | Stable organizational-lineage code for the visitor |
| home_franchise_id | large_string | Stable organizational-lineage code for the designated home team |
| away_pts | int64 | Final visitor score |
| home_pts | int64 | Final designated-home score |
| home_margin | int64 | `home_pts - away_pts`, regardless of site classification |
| overtime_periods | int64 | `0` for regulation; otherwise the parsed overtime count |
| site_status | large_string | `home`, `neutral`, or `unknown` |
| venue_name | large_string nullable | Reported arena/site; null when the source cell is blank |
| attendance | int64 nullable | Reported attendance; null when the source cell is blank |
| attendance_status | large_string | `reported_positive`, `reported_zero`, or `missing` |
| boxscore_url | large_string nullable | Preserved Basketball Reference box-score link when present |
| schedule_url | large_string | Verified schedule page that produced the row |

Only `venue_name`, `attendance`, and `boxscore_url` are nullable. Every other
field is non-null after validation. Rows are deterministically ordered by
`season`, `game_date`, and `game_id`.

#### `game_type` values

| Value | Meaning |
|---|---|
| `playoff` | A completed BAA/NBA playoff result |
| `play_in` | An exact `Play-In Game` row from one of the seven approved seeded monthly pages for 2020–2026 |
| `tiebreaker` | One of the eight historical rows whose exact source remark is `Tiebreaker` |
| `voided` | The single 1953 result marked `Protest; does not count toward record` |

The league and season boundaries use the ending-season convention: `1947`
means 1946–47, BAA covers 1947–1949, and NBA covers 1950–2026. ABA games are
not included.

Basketball Reference (BREF) owns every published result row. For ending
seasons 1947–1983, the builder reads the already verified
`raw/historical_bref/` pages and publishes the exact
`(schedule_url, source_row)` complement of the proven regular-season selector.
For 1984–2026, it reads 43 dedicated playoff schedule pages from the separate
`raw/playoff_bref/` cache. The 37 `play_in` rows for 2020–2026 come only from
seven checksum-verified monthly BREF pages copied from `raw/bref/` into that
cache with their original source identity preserved. The playoff fetch never
writes `raw/bref/` or `raw/historical_bref/`.

Visitor and designated-home columns always determine away/home score
orientation. `site_status` records site evidence separately, and neither a
`game_type` remark nor a play-in label reverses the scores. The one
provenance-backed cohort rule sets exactly all 84 ending-season 2020 rows to
`neutral`; no other cohort-wide site rule is applied.

A blank attendance cell is null with `attendance_status=missing`. A literal
source zero is retained as integer `0` with
`attendance_status=reported_zero`, never converted to missing. A positive
reported value uses `reported_positive`. Blank venue cells remain null.

Pinned hoopR schedules are validator-only evidence and never supply
publication rows or fill missing values. For 2020–2026, BREF and hoopR must be
bidirectionally equal on exactly 627 unique rows using:

```text
(season, game_date, game_type, away_franchise_id, home_franchise_id, away_pts, home_pts)
```

Canonical overlap with each regular publication must be zero using:

```text
(season, game_date, away_franchise_id, home_franchise_id, away_pts, home_pts)
```

Game IDs are deliberately absent from the overlap key because the
publications use different identifier namespaces.

The deterministic `playoff_games_manifest.json` contains the table hash,
ordered Arrow schema, versions, complete validation report, and only relative,
stable provenance identities. Its Basketball Reference source inventory pins
the historical manifest, every consumed historical page, all 43 dedicated
playoff pages, and all seven seeded play-in pages. Its hoopR inventory pins
the seven validator schedules and upstream commit. Its reference inventory
attests exactly `franchises.csv`, `hoopr_snapshot.json`,
`playoff_season_counts.csv`, and `playoff_site_rules.csv`. Fetch timestamps,
absolute checkout or temporary paths, and other machine-specific values are
forbidden. A complete offline build validates the exact 1947–2026
season/type counts and windows, the 627-row equality, the zero regular
overlap, the 84-row 2020 neutral cohort, source/cache inventories, schema,
ordering, and provenance before atomically replacing the Parquet/manifest
pair. Two consecutive offline builds must reproduce both files byte for byte,
and modern or historical regular rebuilds must leave all seven locked regular
artifact hashes unchanged.

Publication visibility uses the stable advisory lock
`data/.parquet-publication.lock`, outside the directory-swapped
`data/parquet/` tree. Historical and playoff pair publishers and the modern
directory publisher hold its exclusive lock through their transactions.
Compliant readers must hold its shared lock across the complete pair or
directory read; Python callers can use
`nba_data.publication.publication_lock(data_dir / "parquet", exclusive=False)`.
This is a mandatory application protocol, not unlocked filesystem
linearizability: direct-path readers that ignore the lock can observe
intermediate path states.

#### Playoff scope limitations

The publication intentionally has no round, seed, or series metadata; no ABA
games; and no box-score statistics beyond the schedule result fields. It does
not classify exhibitions, All-Star games, or NBA Cup games. It changes no
modeling input, analysis output, regular-publication schema, or regular row
set. The workflow also does not bulk-acquire 1984–2019 regular-season monthly
BREF pages.

### `franchises.parquet`

| Column | Type | Meaning |
|---|---|---|
| alias | large_string | Source-era team name |
| franchise_id | large_string | Stable franchise code |
| first_season, last_season | int64 nullable | Inclusive ending-season validity bounds |

The mapping preserves organizational lineage rather than rewriting history:
Charlotte Hornets through 2002 map to `NOP`; Charlotte Bobcats and later
Hornets map to `CHA`; New Jersey/Brooklyn is `BKN`; Vancouver/Memphis is `MEM`;
Seattle/Oklahoma City is `OKC`; Kansas City/Sacramento is `SAC`; and
Washington Bullets/Wizards is `WAS`.

### `venues.parquet`

| Column | Type | Meaning |
|---|---|---|
| venue_id, franchise_id | large_string | ESPN venue and home-franchise IDs |
| first_season, last_season, capacity | int64 | Inclusive venue era and stated capacity |
| capacity_source_url, capacity_note | large_string | Auditable capacity source and context |

It contains 40 curated venue eras for ending seasons 2020–2022. A non-neutral
game in that primary window must resolve exactly one provenance-backed row;
neutral/bubble sites may have no capacity. Capacity is not inferred from
attendance. Attendance more than 105% of capacity requires an explicit
`ATTENDANCE_EXCEPTION:` note.

## IDs, enrichment, and missingness

NBA Stats supplies canonical modern box scores and `GAME_ID`; legacy rows keep
their canonical NBA IDs. `source` identifies the statistical provider, so it
is `nba_stats` for every published row. It does not identify the ingestion
path: ending seasons 1984–2019 come through restored archived NBA Stats CSVs,
while ending seasons 2020–2026 come through cached `LeagueGameFinder` JSON.
hoopR is schedule enrichment only: its ESPN game and venue IDs are distinct,
prefixed `espn-`, and join through ending season, date, home franchise, and
away franchise (with audited next-day reconciliation).
Every retained modern NBA game joins one hoopR schedule row. hoopR supplies
`neutral_site`, venue ID/city, and ESPN attendance; basketball-reference is
the attendance and arena source of record. Its arena wins when a matching,
nonblank arena exists, otherwise ESPN supplies the venue name.

For a matched basketball-reference row, `attendance` is its value, including a
blank/null. ESPN is used only when no b-ref row matches. Thus a b-ref blank is
`missing`, never an ESPN fallback or zero; an explicit source zero is
`reported_zero`, never missing. Positive values are `reported_positive`.
Legacy attendance retains its historical selected value and source. For each
modern season, the build enforces at least 95% b-ref row-join coverage. It
also enforces at least 95% known attendance after removing only the 87
explicitly enumerated 2020 no-fan bubble games whose matched b-ref attendance
is blank. The manifest separately records raw numeric coverage, the eligible
denominator, and the exemption count. Consequently, 2020 raw numeric coverage
is 91.7847%, while its exception-aware validation coverage is 100%.

The build rejects a game unless it has exactly two team rows, one winner, and
`wl` agrees with the final score. Every published box-score statistic is
non-null and nonnegative. If a legacy source is blank or contradictory, the
build requires an exact provenance-backed correction; it does not publish the
blank and never converts it to zero merely to satisfy a gate. Makes may not
exceed attempts, `oreb + dreb` must equal `reb`, and
`2 * fgm + fg3m + ftm` must equal `pts`. Corrections never convert a blank
legacy statistic to zero merely to satisfy an identity. A corrected field is
populated only when a historical box score supplies an exact value.

`reference/game_corrections.csv` has a deliberately narrow scalar grammar.
Team box values are canonical unsigned base-10 integers (`0`, or a nonzero
digit followed by digits): signs, decimal points, leading zeroes, and
whitespace are invalid. An attendance correction must be exactly blank, and a
neutral-site correction must be exactly lowercase `true` or `false`. Every
team-field correction carries its direct historical box-score URL and notes
the canonical NBA game page used to identify the game.

## Sources, pins, and hashes

All raw responses are gitignored under `data/raw/`. Every NBA Stats, hoopR,
and b-ref schedule body must have exactly one metadata sidecar containing the
matching SHA-256; orphan bodies, orphan sidecars, and metadata without a
matching SHA are rejected before publication. The manifest records only these
verified relative cache paths and hashes. The source roles are NBA Stats
`LeagueGameFinder` (canonical modern box scores), pinned hoopR (schedule,
neutral/venue/ESPN enrichment), basketball-reference (attendance/arena and
secondary exact historical reconciliation), and the two restored legacy
inputs. hoopR is pinned to repository
`sportsdataverse/hoopR-nba-data` commit
`50fe7e429c6b5bcbe62e7a56d19afe5a80e6e143`, never a mutable branch.
Each cached hoopR body must also have checksum-valid sidecar metadata with
exact provider `hoopr`, its exact integer ending season, and that exact
upstream commit. The manifest records the verified binding on every season
entry rather than relying only on the global pin. Before a full build, the
checkout copies of `franchises.csv` and `hoopr_snapshot.json` must match the
runtime package assets byte for byte, so normalization, publication, and
manifest attestation cannot use divergent reference data.

| Source | Ending season(s) / path | SHA-256 |
|---|---|---|
| NBA Stats | 2020 `leaguegamefinder_2020.json` | `9349ad765225a5586d37197285fd73a0f407e7f630bcd04884e445536b90ce6a` |
| NBA Stats | 2021 `leaguegamefinder_2021.json` | `379fb6bb4874fb21efc80f7ed782c5cfb68d8d078d68930d01e51e6e47a4de8a` |
| NBA Stats | 2022 `leaguegamefinder_2022.json` | `892c54e9c203ea1f4ba4b2ecda5215e7347d96bee34050bc2558c0fbe665a3e2` |
| NBA Stats | 2023 `leaguegamefinder_2023.json` | `42d18653724ef95d1a34a56426abf4069650ba607d5673230a828bea3a850e5b` |
| NBA Stats | 2024 `leaguegamefinder_2024.json` | `abee3c8041c58df0f1c3920835a1313109d87c7782b221710d348a2ce05ab91a` |
| NBA Stats | 2025 `leaguegamefinder_2025.json` | `f710bbe024bb093ffcee08593234db2ebafa63c80596b99a4e2cacff5b32eb67` |
| NBA Stats | 2026 `leaguegamefinder_2026.json` | `b5f223d082300c92187223577bfd1d3b78b13f3afd9af9eb9cd51e5ea5265601` |
| hoopR | 2020 `nba_schedule_2020.parquet` | `cfe7e328c34cf36f160be9b8adebf16806c5c932274c0177566deb2ac2d743bf` |
| hoopR | 2021 `nba_schedule_2021.parquet` | `7fbdfe4350da317a063dedf17ecdad0b1c0a6ebf40c605cf0131fb1b002b43c2` |
| hoopR | 2022 `nba_schedule_2022.parquet` | `22703c94919428bf8134533ee5f652b1d156980736ee41f14fd297fcd7bcb16d` |
| hoopR | 2023 `nba_schedule_2023.parquet` | `eddbd2de64d401cc9e44b1a567f52e408f71b96bc413a9dfd22cc4ad0e404f13` |
| hoopR | 2024 `nba_schedule_2024.parquet` | `7840d80ebb7ab34cedec77fb620519b88c282c2662b2a0f9f45d538457bbb37f` |
| hoopR | 2025 `nba_schedule_2025.parquet` | `157dd9c130dd7518812302e8ed50496e7c3773fbc9c7efc98c40f5ff39314355` |
| hoopR | 2026 `nba_schedule_2026.parquet` | `4033bfb809a0cf60e706e733cb8b1fb561dec185055957570a8583e22258f8be` |
| legacy | `league_boxscores_extra.csv` | `e512c1a15b5a2046c47efb86152b1087c28c8955b183fdf5c0e8151876f8b822` |
| legacy | `nba-teams-1984-2019-ordered.csv` | `3a71f75829452032523098280542485f2a94513facde2ea3f78b5b4c4356764c` |

The 49 basketball-reference paths are
`raw/bref/NBA_<ending-season>_games-<month>.html`; their exact month/hash
ledger is:

```text
2020 august:b56ad42ce7ae6e4e8e68639fe6b4f443a8ba139399c619fa29ddeb5f79a4bed1 december:5e1c28a5b2c78a16fca1f9168cd93f07adcc970eee8363856babaea44b6149dd february:1779faefc05f60cfd2bfc274189f5449114a87375be56673fb5c275e2c811b54 january:b1fae333a0eb3470d75e64409dc925e2edb0fffc9a300efc0559669bf4fbe138 july:75eea33530e890f4c793b5e9c45de39c1fec80926e6420197804e29782489fa6 march:cd2cb36ccd5cac57164c4e0ab28c39f9e6a0e34057b2a6056d46b495ee0eef5b november:147e9aff2b9fe48143351c0f01f4fad168c05368a0698454f52257a051940146 october:64372e11233b62c6589c2be38275153c35f49e9ccae365532ad8970f7f253d0c
2021 april:82a6032917dfed015220d70f08b12f22c95a99b442893ad6dba3fe70416c9139 december:dec166dd15c54513055888d945c2908320cd0961b44fe302bbf08a840d59d056 february:b8a16de349dff464d5264523e0567f96c905dd66b759a6dd63fc6a6da7511c34 january:c891625f18e83ed333b727cbcace42a44974aa3d31b55631b0dcb2c7a0a61388 march:5d286e83b260db15c5dbf193b04743eefff6970841d3ed4484343b66d3374d49 may:9350206440fc0ec9a533c5504f8f4c79d895f281d6b98b8b51a68a9b0b4cad8c
2022 april:79bbe7936089f25c0a554473a22c3398b50b76ec42179cb2d1555068701813d1 december:fc9313ef9ddc8b18ca72f3f6028358315367d7a6ebd888e9e397c26c35d8bb25 february:f6e838c71935e377f0191c3e93805681c923a5b0547bfca92be291045dbbdd02 january:94e44e79fa0ae0fbd08d1fb7794e82e00c00dd71e8d1061634ecbdb289af9ed5 march:cf699d1e5de4cf26634d82c2f2e3282a343804ad0c52c66974938a880450ba0a november:e0a92d4bc470562cc3b8e6fe16bc3d43913eabf43600aa6f76aebfbd40956bf0 october:5d0b6f8c2e5dce76540195ee3a5b4a6165db82eb3b0708d1edf03297e598e5c6
2023 april:191849498aeffb9cc3d2984a69f7715514a7b32b7c7eba9b220b192c9ad716c5 december:f4bc113308fcdeaff3c4a020fc005131d80eb697386d91f4698f8d12e1ae9005 february:7899cc10e2dda31e26530e854118ec9e0f04521d059e49217e4157639cbc9842 january:c2319537fd947777769076f655e0a2ed82a0601f462b69f3b49574e6cf141980 march:a72b88ebda50f0f99f4fd323b614b1b5745dad2af2854f3b16984be5d5fe9c09 november:709f1fd0cea0c29900ab89572907ffe8464d38667e2920dc7284aa719135a5e2 october:6256173ec1d86d03bdf9c5fe72699429f2037b306f0ad526c0eab2f015200197
2024 april:7f5f43aad61c58ff3855c1b29fa853b373168568a006c35c808d0e938e1a28bc december:ae38940a30162bad80266d771a7c2a71f20b140542c77530b1c6bed1494e0c69 february:7cabe1392176b3bd166465de4b63af677d3f71d007cfcbd4c6bf9ae679072821 january:cd92dee673321e1cbf37d3f4d34a9e38a9d8b8a8bb1e2e295e2d95a583ecdc3d march:27af72875b95d5f4a7b5c21c223cf6e6072ffb0bf6aeddab8e65d3c8bb2aade1 november:10fd29e6e0abf1ebd84908fe9dfee2c7626124722a77bad240dd709fd2da6d1d october:ddc66462e08cc8feaf15b6b4055e9f27d52ca60af96f67a6ad94e112a625623e
2025 april:f939b6e5336f815e20bf8535f72984c520493f27bd55be1d9b39016fe6e2b32c december:81a47446904245c85aa2acf89f1b9d3a27884424470b870bc9c15bc7f2bb8255 february:6b8b4fb93a8a4ad480e768cae85ef6376301cba7c8b907ceb3194e8b869f18fe january:7adb088d7b3c47701f28032c52bf0e88c16d3b693974ea4f167d574d51664f01 march:016a10fce0ff270f0ad5a7f6d5d92c0f01b0a3a115a259705cedd3f66a71a907 november:238384fb3d3dedfa9512fe6375164a03e1aef123ba30365c5f14ea0004e7e419 october:34d33d33b25d8f4331ad2ed5c0b2f4ed295f6bf452257bdb2e4e8ade157f48b0
2026 april:486496f9f87c557e055184495624ad5be3585f3bb1e9642cc231a55cfbff1531 december:10cdb78803185010746a929c58ea8929b914f74e42ac1b0566fbbf0f976cac5d february:9ce90ce8173c1008bccf1180f721fdc21268297407568620b7df2feb9057f190 january:b09ba48c4e1a30439c7f6133ce4d06651c937ce20b25509a56320844259634a0 march:49ecc2eef0db978dacc9df4760689fe09e9e608ae32aa0ed9a1cfbf5543da1c7 november:e66964624aebbc8b02cb401f1c1b30bf6b9f3400ce68adacdcf57a8555183ca0 october:5e19280b0f68153fee40315f8071a1115278ca15a369de8c61d07abbf4655a65
```

The 23 checksum-verified historical reconciliation pages are under
`raw/reconciliation/bref/`. Their game ID, direct immutable URL, relative
path, and SHA-256 are recorded in
`sources.basketball_reference.historical_boxscores` in the manifest. They are
preserved and recovered through the verified raw snapshot; the normal
season-fetch command does not re-download these one-off audit pages.

The authoritative current `reference/franchises.csv` hash is read from the
committed manifests rather than duplicated here. Other curated-reference
hashes are `reference/game_corrections.csv`
`4fe5af540239bb1dfb1b08b99e256dfd88a8fe05531c092256a633a19c84e6e4`;
`reference/hoopr_snapshot.json`
`167dfaa6e1d949e9f4812687ff74f2316610b1f016b070fd5bb4151e857ff1dc`;
and `reference/venue_capacities.csv`
`7fcd2cfee1485d60541e77b89e6817f37fd6d18261b5680a5442a4bbc4bdbb45`.
The manifest also supplies every b-ref page's immutable URL. Print its
complete machine-readable ledger without touching the raw cache with:

```bash
jq '.sources.basketball_reference, .references' data/parquet/build_manifest.json
```

## Commands and safe source access

### Historical results

From a repository checkout, the historical operator flow is:

```bash
# The only network command in the historical workflow.
python -m nba_data fetch-historical

# Offline: requires the complete, verified 1947–1983 cache.
python -m nba_data build-historical
```

`fetch-historical` serially caches Basketball Reference league schedule/index
and month pages before parsing them. It uses ordinary TLS verification, a
browser-identifying user agent, no parallel requests, and at least 12 seconds
between requests; it stops on HTTP, TLS, rate-limit, block-page, or schema
failure. It does not collect individual box-score pages. `--season
<ending-season>` may restrict an audit fetch, but the command above is the
complete-cache path.

`build-historical` performs no network access. It verifies every cached body
against its metadata sidecar, rejects orphaned or unexpected files, requires
all 37 ending seasons, validates curated season counts independently, and
atomically publishes the historical Parquet/manifest pair. The verified raw
cache lives at `data/raw/historical_bref/` and remains untracked.

### Playoff results

Run the playoff operator flow in this order:

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

`fetch-playoffs` is the only playoff network path. It accepts ending seasons
1984–2026, reuses valid cache pairs, seeds the applicable verified play-in
monthly page, and requests only missing dedicated pages. Basketball Reference
access uses ordinary TLS verification, the browser-identifying User-Agent, no
parallel requests, and at least 12 seconds between requests. HTTP, TLS,
rate-limit, block-page, or schema failure stops acquisition immediately.

`build-playoffs` is strictly offline. It requires the complete verified
historical dependency, all 43 dedicated and seven seeded playoff cache pairs,
all pinned 2020–2026 hoopR schedules, and both curated playoff reference
files. It atomically publishes only `playoff_games.parquet` and
`playoff_games_manifest.json`. Run it twice and compare both file hashes for
the byte-identity gate; `python -m nba_data build` and
`python -m nba_data build-historical` must not change either playoff product
or any locked regular product.

### Modern results

```bash
# Cache all providers, or one ending season (2020..2026).
.venv/bin/python -m nba_data fetch [--season 2024]

# Produce the gitignored venue-era inventory used to curate capacities.
.venv/bin/python -m nba_data inspect-venues

# Validate and atomically publish the four Parquet products plus manifest.
.venv/bin/python -m nba_data build
```

The original NBA Stats failure was an **AdGuard DNS sinkhole**, not a TLS
problem: `stats.nba.com` resolved to `0.0.0.0`, sending curl to a local
listener. Before a fetch, the client resolves the hostname and rejects an
empty, unspecified (`0.0.0.0`/`::`), or loopback answer with an AdGuard query
log/allowlist diagnostic. Correct the DNS/AdGuard policy, then retry with
ordinary certificate validation; TLS bypasses are not an approved remedy.

## Independent recovery snapshots

Historical raw recovery is independent of the Slice 2A modern snapshot:

```bash
# Create the immutable historical snapshot after a successful publication.
bash scripts/archive_historical_raw_snapshot.sh

# Restore only data/raw/historical_bref; that target must not exist.
bash scripts/restore_historical_raw.sh
```

Both commands default to
`/mnt/nas/llm/documents/nba-predictions-archive`, overridable with
`NBA_HISTORICAL_RAW_ARCHIVE_ROOT`. The committed historical manifest supplies
the ending season and its own SHA-256 binds the archive name:
`historical-raw-1983-<first-12-hex-of-manifest-sha256>`. The archive command
refuses an existing final path, stages the cache and an exact copy of
`historical_games_manifest.json`, and validates the source and staged cache
against that trusted manifest. Validation requires the exact body paths and
body hashes plus matching `provider`, `resource`, `league`, `season`, `month`,
`url`, and `sha256` identity in every sidecar. It inventories every HTML body
and metadata sidecar in `SHA256SUMS`, verifies every checksum and a
checksum-based tree comparison, rechecks the staged manifest binding after
copying, then publishes the immutable directory atomically. Copy-time changes
fail without leaving a retry-blocking target.

Restore derives that exact snapshot from the current committed manifest. It
requires the snapshot manifest to match byte for byte, rejects incomplete,
extra, deleted, changed, or non-regular cache entries through a complete
checksum inventory and structure checks, and performs the same exact
body/sidecar identity validation before copying. It validates the stage
against the trusted committed manifest, revalidates the snapshot after the
copy, and performs a final checksum-based comparison before an atomic rename;
a consistent copy-time snapshot swap is rejected. The destination defaults to
`data/raw/historical_bref/` and must be absent: restore never merges with or
replaces an existing cache.

Playoff raw recovery is another independent snapshot family under the same
default NAS archive root:

```text
playoff-raw-2026-<first-12-hex-of-playoff-manifest-sha256>/
    playoff_bref/
    playoff_games_manifest.json
    SHA256SUMS
```

`bash scripts/archive_playoff_raw_snapshot.sh` pins and rechecks the trusted
playoff and historical manifests, requires the exact 50-body cache and
body/sidecar identities attested by `playoff_games_manifest.json`, stages on
the archive filesystem, detects source changes during copying, and publishes
an immutable snapshot atomically. An existing snapshot name is accepted only
when its structure, manifest, and checksums validate exactly.

`bash scripts/restore_playoff_raw.sh` derives that snapshot from the current
committed playoff manifest and restores only `playoff_bref/`. Its destination
defaults to `data/raw/playoff_bref/` and must be absent; restore never merges,
overwrites, or silently repairs a live cache. It validates the snapshot before
and after copying, stages under the destination parent, verifies the staged
cache and both manifest pins, and then renames atomically. The historical
manifest and current `historical_bref/` cache are prerequisites because the
playoff publication consumes them, but playoff restore never writes them. If
that cache is absent, first run
`bash scripts/restore_historical_raw.sh`, then run
`bash scripts/restore_playoff_raw.sh`.

The modern recovery flow remains separate:

```bash
# Archive modern Slice 2A raw sources; historical_bref is excluded.
bash scripts/archive_raw_snapshot.sh

# Restore the complete modern Slice 2A data/raw snapshot.
bash scripts/restore_raw.sh
```

The modern archive is named
`slice2a-raw-<games.max_season>-<first-12-hex-of-modern-manifest-sha256>` and
provides manifest binding to `build_manifest.json`. Its restore verifies that
binding and the complete regular-file checksum inventory, then refuses an
existing destination. It does not attest other filesystem entry types.
Neither recovery path substitutes for the other: the historical scripts
operate only on `historical_bref`, while `scripts/restore_raw.sh` remains the
Slice 2A modern restore.

## Play-by-play archive

`data/parquet-large/pbp/` is an independently published, unjoined event-level
archive built from the NBA Stats V3 `PlayByPlayV3` endpoint. It is not a
modeling input, does not change `games.parquet`, `team_games.parquet`,
`historical_games.parquet`, or `playoff_games.parquet`, and both its raw
cache and its Parquet publication are gitignored and never committed; a
repository clone does not contain raw PBP responses or the large
play-by-play Parquet partitions. The verified production archive covers
ending seasons 1997–2026, game types `regular`, `playoff`, and `play_in`
(play-in only for 2020–2026), 38,023 games, and 18,303,908 events. The
committed [final audit](audits/2026-07-31-play-by-play-archive.md) records
the exact per-season totals, anomaly summary, action-field availability, the
reviewed score-mismatch allowlist, source-spike evidence, build gates, and
the independent restore/reproducibility verification for that archive.

### `events.parquet`

One row per play-by-play action. The primary key is `(game_id, action_id)`;
`action_number` duplicates and non-monotonic values are valid source
behavior and are not part of the key. Rows are sorted by
`(game_id, action_id)`. Publication writes exactly one deterministic,
ZSTD-compressed `events.parquet` file per ending season under
`data/parquet-large/pbp/season=<ending season>/events.parquet`, for every
season `1997` through `2026`, alongside one `_manifest.json` at
`data/parquet-large/pbp/_manifest.json`. These are the exact
`EVENT_COLUMNS`/`PBP_ARROW_SCHEMA` columns and physical types, in
publication order:

| Column | Type | Nullable | Origin | Meaning |
|---|---|---|---|---|
| `game_id` | large_string | no | derived | Canonical NBA Stats `GAME_ID`, taken from the payload and cross-checked against the worklist |
| `action_id` | int64 | no | source | V3 `actionId`; positive and strictly increasing within one game's response order |
| `action_number` | int64 | no | source | V3 `actionNumber`; duplicates and non-monotonic values are valid source behavior |
| `season` | int64 | no | derived | Ending season, from the worklist row that identified this game |
| `game_type` | large_string | no | derived | `regular`, `playoff`, or `play_in`, from the worklist row |
| `clock` | large_string | no | source | V3 `PTxxMxx.xxxS` period-clock string |
| `clock_seconds` | double | no | derived | `clock` parsed to elapsed-remaining seconds (`parse_clock`); `0`–`720` in regulation, `0`–`300` in overtime |
| `period` | int64 | no | source | V3 `period`; a game's periods must run `1..max(period)` in order |
| `team_id` | int64 | yes | source | V3 `teamId` |
| `team_tricode` | large_string | yes | source | V3 `teamTricode` |
| `person_id` | int64 | yes | source | V3 `personId` |
| `player_name` | large_string | yes | source | V3 `playerName` |
| `player_name_i` | large_string | yes | source | V3 `playerNameI` |
| `x_legacy` | int64 | yes | source | V3 `xLegacy` |
| `y_legacy` | int64 | yes | source | V3 `yLegacy` |
| `shot_distance` | int64 | yes | source | V3 `shotDistance` |
| `shot_result` | large_string | yes | source | V3 `shotResult` |
| `is_field_goal` | int64 | yes | source | V3 `isFieldGoal` |
| `shot_value` | int64 | yes | source | V3 `shotValue` |
| `score_home_raw` | large_string | no | source | Exact source score string from V3 `scoreHome`, including empty-string blanks; never rewritten |
| `score_away_raw` | large_string | no | source | Exact source score string from V3 `scoreAway`, including empty-string blanks; never rewritten |
| `score_home` | int64 | no | derived | Non-null running home score derived from `score_home_raw`/`score_away_raw` (see below) |
| `score_away` | int64 | no | derived | Non-null running away score derived from `score_home_raw`/`score_away_raw` (see below) |
| `points_total` | int64 | yes | source | V3 `pointsTotal` |
| `location` | large_string | yes | source | V3 `location` |
| `description` | large_string | yes | source | V3 `description` |
| `action_type` | large_string | yes | source | V3 `actionType` |
| `sub_type` | large_string | yes | source | V3 `subType` |
| `video_available` | int64 | yes | source | V3 `videoAvailable` |

#### Score sentinel and running-score semantics

`score_home_raw`/`score_away_raw` preserve the exact source score string for
every action, including the empty-string blanks the modern V3 feed emits for
many non-scoring actions; these two columns are never rewritten. `score_home`
and `score_away` are separately derived, always-non-null running scores
(`derive_running_scores`). Running scores start implicitly at `0`-`0`. A
paired blank `score_home_raw`/`score_away_raw` — at any position, including
any number of leading pairs before the first numeric pair — carries the
current running score forward rather than being rejected for lacking a prior
established score. The first numeric pair encountered in a game's action
sequence may be any value: the implicit `0`-`0` start already covers the
pre-scoring portion of the game, and real full-corpus evidence includes
games whose first numeric pair is not `0`-`0` (a missing opener row, or a
start-truncated source response). After that first numeric pair, a later
`"0"`/`"0"` pair once a nonzero score is running is the historical "unknown"
zero sentinel and does not reset the running score; the audit's
`Early-zero-sentinel total` counts these. One-sided blanks, non-integer
strings, and negative values are all source contract violations and fail
the build.

A known, narrow class of early-source PBP error misattributes points between
the two teams (or drops/truncates events) while every other invariant still
holds; `fetch-pbp` logs and continues past such a mismatch. Publishing a
game whose PBP-derived terminal score disagrees with the worklist requires
an explicit, human-reviewed entry in
[`src/nba_data/reference/pbp_score_mismatches.json`](../src/nba_data/reference/pbp_score_mismatches.json)
recording both the exact worklist pair and the exact derived pair that was
reviewed and accepted; an unreviewed or since-changed mismatch still blocks
`build-pbp`. Events and PBP-derived running scores are never rewritten to
force agreement. The production archive has exactly 15 such reviewed
entries, reproduced in the manifest's `validation.terminal_mismatches` and
listed in the final audit's `Source score mismatches (allowlisted)` section.

#### Source schema and the 23-key contract

Each V3 `actions[]` element must have exactly the 23 keys in
`ACTION_SOURCE_KEYS` (`actionId`, `actionNumber`, `actionType`, `clock`,
`description`, `isFieldGoal`, `location`, `period`, `personId`,
`playerName`, `playerNameI`, `pointsTotal`, `scoreAway`, `scoreHome`,
`shotDistance`, `shotResult`, `shotValue`, `subType`, `teamId`,
`teamTricode`, `videoAvailable`, `xLegacy`, `yLegacy`) — a fixed 23-key
projection. A missing or additional key on any action is V3 schema drift:
the raw response remains cached as evidence, but publication fails until
the design, parser, and fixtures are reviewed together. The twelve integer
keys must be non-boolean integers; `clock`, `scoreHome`, and `scoreAway`
must never be null; every other string key may be null.

#### Worklist construction and cross-validation

Every ending season's play-by-play acquisition first fetches its dedicated
`LeagueGameFinderResults` worklists: `Regular Season` and `Playoffs` for all
30 seasons (1997–2026) plus `PlayIn` for 2020–2026, 67 dedicated worklists in
total, cached under `data/raw/stats_nba/pbp/worklists/<season>/<game_type>.json`
and normalized to `WORKLIST_COLUMNS`. Home/away roles are derived
positionally from each row's `MATCHUP` string rather than from `TEAM_ID`
ordering, because 2024-25-and-later neutral-site games can carry a
double-`@` `MATCHUP` anomaly that ordering alone does not resolve.
`cross_validate_worklist` independently rebuilds two distinct comparison
keys from the integrated regular (`games.parquet`) and postseason
(`playoff_games.parquet`) publications. The regular comparison key is
`(game_id, season, game_date, away_franchise_id, home_franchise_id,
away_pts, home_pts)`; the postseason comparison key is `(season, game_date,
game_type, away_franchise_id, home_franchise_id, away_pts, home_pts)`. Both
require bidirectional equality with the fetched worklist before any PBP
game is requested.

#### Game types, season scope, key, ordering, and partition layout

Game types are exactly `regular`, `playoff`, and `play_in` (`play_in` only
for ending seasons 2020–2026). Ending seasons are exactly the integers
`1997` through `2026`. The event primary key is `(game_id, action_id)`.
Rows within a partition are sorted by `(game_id, action_id)`. Publication
writes one `events.parquet` partition per ending season under
`season=<ending season>/` — never one file per game or per game type.

#### Manifest and provenance guarantees

`data/parquet-large/pbp/_manifest.json` is deterministic JSON: `schema_version`,
`scope` (`ending_seasons`, `game_types`), the ordered `schema` (name, Arrow
type, nullability, `source`/`derived` origin for every `EVENT_COLUMNS`
entry), `sources.worklists`/`sources.games` (sorted relative cache body and
sidecar paths plus independently recomputed SHA-256 and content-length
attestations), `versions` (`nba_api`, `numpy`, `pandas`, `pyarrow`, `python`),
per-season `partitions` (byte and logical-row SHA-256, row/game counts, max
period), and a full `validation` report (worklist/game/event counts, period
counts, per-`(season, game_type)` event-count distributions with Tukey
outliers, action-field null/blank availability, per-game terminal scores and
sentinel/blank counts, the `terminal_mismatches` allowlist ledger, and named
build gates: `arrow_schema_exact`, `primary_key_sorted`,
`primary_key_unique`, `row_season_consistent`, `worklist_membership_exact`,
`terminal_scores_verified`, `zstd_compression_verified`,
`partition_checksums_verified`, `manifest_json_canonical`). Absolute paths,
build times, hostnames, and other volatile execution data must never appear.
`validate_publication` independently recomputes this entire report from the
published tree — never trusting the stored manifest — and requires an exact
match; two consecutive offline `build-pbp` runs must reproduce every
`events.parquet` partition and `_manifest.json` byte for byte.

### Commands

```bash
# Networked and rate-limited (>= 1.5s between requests, 5 attempts,
# 5/10/20/40s retry delays); resumable full acquisition or one season.
python -m nba_data fetch-pbp --season 2026
python -m nba_data fetch-pbp

# Strictly offline deterministic publication of every events.parquet
# partition plus _manifest.json.
python -m nba_data build-pbp

# Independent immutable combined raw-plus-Parquet NAS recovery.
scripts/archive_pbp_snapshot.sh
scripts/restore_pbp_snapshot.sh
```

`fetch-pbp` is the only PBP network path; `build-pbp`, unit tests,
integration tests, audit rendering, archive validation, and restore
validation are all offline. The only source request host is
`stats.nba.com`. Complete valid raw cache body/sidecar pairs are never
silently overwritten or re-fetched.

### Immutable snapshot and recovery contract

The immutable combined snapshot root is
`/mnt/nas/llm/documents/nba-predictions-archive/pbp-archive-2026-<first-12-hex-of-manifest-sha256>/`,
overridable with `NBA_PBP_ARCHIVE_ROOT`/`NBA_PBP_DATA_DIR`. A snapshot
combines both the raw cache (`raw/stats_nba/pbp/`) and the active Parquet
publication (`parquet-large/pbp/`) under one name, plus a `SHA256SUMS`
covering every regular file. `scripts/archive_pbp_snapshot.sh` validates the
live source, stages a checksummed copy, and publishes it atomically; an
existing same-name snapshot is accepted only if it already matches the
freshly staged copy exactly, and is never modified otherwise.

`scripts/restore_pbp_snapshot.sh` requires `NBA_PBP_SNAPSHOT` (the exact
snapshot directory) and `NBA_PBP_MANIFEST_SHA256` (the trusted full
manifest SHA-256 to pin against), and restores into
`NBA_PBP_RAW_DESTINATION`/`NBA_PBP_PUBLICATION_DESTINATION` (defaulting to
`data/raw/stats_nba/pbp` and `data/parquet-large/pbp`), both of which must
be absent. It verifies the snapshot's `SHA256SUMS` inventory and manifest
pin, stages a checksummed copy, and delegates every JSON/Parquet semantic
question to `python -m nba_data.pbp_snapshot`:

- `validate-layout` fully revalidates one raw/publication pair (cache tree
  plus publication) offline, optionally pinning the manifest SHA-256;
- `rebuild-compare` deterministically rebuilds the raw cache into a
  temporary stage and requires an exact, byte-for-byte match against the
  already-published tree, after first requiring the installed
  Python/package versions to equal the manifest's pinned `versions`;
- `write-audit` independently reverifies the snapshot checksum inventory
  and manifest pin, revalidates the live cache/publication and its
  cross-source worklist equality, derives the four source-spike evidence
  rows straight from the live raw cache, and only then renders the final
  Markdown audit via `render_audit`.

Restore performs the checksum verification, the `validate-layout` semantic
check, and `rebuild-compare`'s offline rebuild-and-byte-compare before an
atomic two-destination publish with explicit rollback on partial failure.
The [final audit](audits/2026-07-31-play-by-play-archive.md) was rendered
only after an independently restored, absent-target copy of this exact
snapshot passed every one of these checks.

## Player-game logs

`data/parquet-large/player_games/` is an independently published,
player-appearance/workload archive built from the NBA Stats `LeagueGameLog`
endpoint in player mode (`PlayerOrTeamAbbreviation=P`). It is not a modeling
input and does not change `games.parquet`, `team_games.parquet`,
`historical_games.parquet`, or `playoff_games.parquet`, and both its raw
cache and its Parquet publication are gitignored and never committed; a
repository clone does not contain raw player-log responses or the large
player-game Parquet partitions.

> A missing `player_games` row means only that LeagueGameLog did not credit
> an appearance; it does not prove roster membership, eligibility, injury,
> rest, or expected participation.

### Two explicit scopes

`SOURCE_SEASONS` (ending seasons `1984`–`2026`, 93 `(season, game_type)` cache
identities: 43 regular, 43 playoff, seven play-in) governs fetch, the
complete-inventory validator, archive/restore, and the full diagnostic
source audit. `PUBLICATION_SEASONS` (ending seasons `2012`–`2026`, 37
identities: 15 regular, 15 playoff, seven play-in) governs assembly,
coverage validation, the manifest, and the trusted publication itself, which
writes exactly 15 partitions. Only the 37 publication-scope cache bodies are
bound into the publication manifest's `sources.player_logs`; the remaining
56 source-scope identities (ending seasons 1984–2011) stay in the raw
archive as quarantined evidence and never enter a coverage join, discrepancy
count, or exception ledger for the trusted publication. `play_in` applies
only to ending seasons `2020`–`2026` in both scopes. Each raw cache identity
is one attested body/sidecar pair at
`data/raw/stats_nba/player_logs/season=<ending season>/<game_type>.json`.

### `player_games.parquet`

One row per player per game. The primary key is `(game_id, person_id)`,
unique within a season partition. Rows are sorted by `(season, game_date,
game_id, franchise_id, person_id)`. Publication writes exactly one
deterministic, ZSTD-compressed `player_games.parquet` file per ending season
under `data/parquet-large/player_games/season=<ending season>/player_games.parquet`,
for every season `2012` through `2026`, alongside one `_manifest.json` at
`data/parquet-large/player_games/_manifest.json`. These are the exact
`PLAYER_GAMES_ARROW_SCHEMA` columns and physical types, in publication order:

| Column | Type | Nullable | Origin | Meaning |
|---|---|---|---|---|
| `game_id` | large_string | no | source | LeagueGameLog `GAME_ID` |
| `person_id` | int64 | no | source | LeagueGameLog `PLAYER_ID` |
| `season` | int64 | no | derived | Ending season, from the cache identity that was read, not parsed from `SEASON_ID` |
| `game_type` | large_string | no | derived | `regular`, `playoff`, or `play_in`, from the cache identity that was read |
| `game_date` | date32 | no | source | Parsed `GAME_DATE` |
| `player_name` | large_string | no | source | LeagueGameLog `PLAYER_NAME` |
| `nba_team_id` | int64 | no | source | LeagueGameLog `TEAM_ID` |
| `team_name_raw` | large_string | no | source | LeagueGameLog `TEAM_NAME` |
| `team_abbreviation_raw` | large_string | no | source | LeagueGameLog `TEAM_ABBREVIATION` |
| `matchup_raw` | large_string | no | source | LeagueGameLog `MATCHUP` |
| `franchise_id` | large_string | no | derived | Resolved via the franchise reference from `team_name_raw`/`nba_team_id`, applying any exact matchup/designation exceptions |
| `opponent_franchise_id` | large_string | no | derived | The other franchise present in the same `game_id` |
| `is_home` | bool | no | derived | Parsed from `matchup_raw` (`vs.` = home, `@` = away) |
| `min` | int64 | yes | source | LeagueGameLog `MIN` |
| `pts` | int64 | yes | source | LeagueGameLog `PTS` |
| `fgm` | int64 | yes | source | LeagueGameLog `FGM` |
| `fga` | int64 | yes | source | LeagueGameLog `FGA` |
| `fg3m` | int64 | yes | source | LeagueGameLog `FG3M` |
| `fg3a` | int64 | yes | source | LeagueGameLog `FG3A` |
| `ftm` | int64 | yes | source | LeagueGameLog `FTM` |
| `fta` | int64 | yes | source | LeagueGameLog `FTA` |
| `oreb` | int64 | yes | source | LeagueGameLog `OREB` |
| `dreb` | int64 | yes | source | LeagueGameLog `DREB` |
| `reb` | int64 | yes | source | LeagueGameLog `REB` |
| `ast` | int64 | yes | source | LeagueGameLog `AST` |
| `stl` | int64 | yes | source | LeagueGameLog `STL` |
| `blk` | int64 | yes | source | LeagueGameLog `BLK` |
| `tov` | int64 | yes | source | LeagueGameLog `TOV` |
| `pf` | int64 | yes | source | LeagueGameLog `PF` |
| `plus_minus` | int64 | yes | source | LeagueGameLog `PLUS_MINUS` |

#### Coverage joins and discrepancy classification

Player rows are aggregated to one team-game side per `(game_id,
franchise_id)` and cross-validated against the committed results
publications with two distinct joins, restricted to `PUBLICATION_SEASONS`:

- **Regular season**: an exact-`game_id` join against `games.parquet`/
  `team_games.parquet` (`_TEAM_GAME_IDENTITY`:
  `game_id, season, game_type, game_date, franchise_id,
  opponent_franchise_id, is_home`), `validate="one_to_one"`.
- **Postseason** (`playoff`, `play_in`): a composite join against
  `playoff_games.parquet` on `_POSTSEASON_GAME_IDENTITY`
  (`season, game_date, game_type, home_franchise_id, away_franchise_id`),
  since postseason `game_id`s are not shared with the player-log source.

Every aggregated side must match its reference exactly; an unmatched or
identity-drifted side fails the build. `collect_discrepancies` then compares
each side's summed player `pts`/`min` against the matched publication total
under the unchanged strict rules: a point candidate is any side whose
summed `pts` disagrees with the published total; a minute candidate is any
side whose summed `min` falls outside `MINUTE_TOLERANCE = 5` of
`240 + 25 * inferred_overtime_periods` (`MAX_OVERTIME_PERIODS = 6`), whose
opposing side infers a different overtime count, or whose inferred
postseason overtime disagrees with the published `overtime_periods`. The
trusted 2012–2026 publication observes **zero** point and **zero** minute
candidates under these gates.

#### Historical quarantine and full-audit evidence

Ending seasons **1984–2011 are quarantined raw source evidence, not a
released workload, and not an accepted exception population.** The complete
1984–2026 diagnostic audit (`scripts/audit_player_logs.py` in its default,
non-final review mode) deterministically reproduces:

- 1,082,012 raw player rows audited, 52,529 source games audited, 105,058
  team-game sides audited;
- 662 point candidates and 1,211 minute candidates (1,300 unique affected
  team-game keys, 1,164 unique affected games, 573 keys present in both
  sections);
- point causes, losslessly: 627 incomplete payloads, 27 bad references, 7
  phantom rows, 1 isolated discrepancy; and
- minute causes, losslessly: 573 point-deficit overlaps, 264 active `MIN=0`
  sides, 228 residual-six-to-eight sides, 146 other anomalies.

These are source incompleteness, reference errors, and corrupt fields, not
one approvable exception class, and all of them fall in ending seasons
1984–2011: the trusted 2012–2026 publication scope observes zero point and
zero minute candidates under the identical strict gates (see the governing
amendment). They are never imputed,
corrected, widened past `MINUTE_TOLERANCE`, or copied into the committed
exception ledger; the trusted publication instead excludes ending seasons
1984–2011 entirely. The generated source-audit report has SHA-256
`3e62e215555898ddade63322688760c24ba6c3a5d2c7f4b0face1169fd47dbd0`; the
generated 805,637-byte candidate JSON has SHA-256
`ade10d5af96f47ba633b8526488eb9cb94607ce024aa0ce7e0b63ed9b05d3388`. Both
values are reproduced verbatim in the
[final audit](audits/2026-08-02-player-games-source-audit.md) as immutable
reviewed facts, not recomputed at render time from the git-ignored SDD
diagnostic workspace. Publishing any ending season before 2012 requires a
new, separately reviewed design (see the amendment's historical remediation
boundary); this release performs no historical remediation, alternate-source
acquisition, quality-flag schema, or corrected player-row synthesis.

#### Exception ledgers and repairs

`src/nba_data/reference/player_game_validation_exceptions.json` is the
committed point/minute exception ledger and remains exactly `{"minutes":
{}, "points": {}}`: the trusted publication requires zero observed point and
zero observed minute discrepancies, and a non-empty or stale entry fails the
build. `src/nba_data/reference/player_game_matchup_exceptions.json` (36
reviewed rows, malformed `MATCHUP` strings in ending seasons 1997–2000) and
`src/nba_data/reference/player_game_designation_exceptions.json` (10
reviewed rows, neutral-site games in ending seasons 2025–2026) are both
full, canonical ledgers bound into the manifest regardless of scope; the
publication applies only the entries that fall inside `PUBLICATION_SEASONS`.
The trusted 2012–2026 release therefore applies **zero** of the 36 matchup
repairs (all fall in 1997–2000) and **all ten** designation repairs. Applied
repairs are recorded with their own deterministic SHA-256 digests in the
manifest's `validation.matchup_repairs`/`validation.designation_repairs`.

#### Manifest and provenance guarantees

`data/parquet-large/player_games/_manifest.json` is deterministic JSON with
exactly nine top-level keys: `schema_version`, `scope`
(`ending_seasons`, `game_types`, exactly `[2012..2026]`), the ordered
`schema` (name, Arrow type, nullability for every column above), `versions`
(`nba_api`, `pandas`, `pyarrow`, `python`), `sources`
(`player_logs` — the 37 consumed cache body/sidecar bindings, sorted; and
`franchise_reference`), `references` (the committed `games.parquet`,
`team_games.parquet`, `playoff_games.parquet`, and their manifests),
`exception_ledger` (bindings for all three exception ledger files),
`partitions` (exactly 15 entries, one per ending season, with byte and
logical-row SHA-256), and `validation` (row/game/player counts,
`cache_entries`, `coverage`, `exceptions` — `{"minutes": 0, "points": 0}` —
and `matchup_repairs`/`designation_repairs` counts with digests).
`validate_publication` independently recomputes this entire manifest from
the published tree — never trusting the stored copy — and requires an exact
match; two consecutive offline `build-player-games` runs must reproduce
every `player_games.parquet` partition and `_manifest.json` byte for byte.

### Commands

```bash
# Networked and rate-limited; resumable full acquisition or one season,
# covering all 93 SOURCE_SEASONS identities.
python -m nba_data fetch-player-logs --season 2026
python -m nba_data fetch-player-logs

# Strictly offline deterministic full-corpus (1984-2026) source review;
# reproduces the historical candidate counts but never approves them.
python scripts/audit_player_logs.py \
  --data-dir data --output-dir /tmp/player-game-source-review

# Strictly offline deterministic publication of the trusted 2012-2026
# scope: 15 season partitions plus _manifest.json.
python -m nba_data build-player-games

# Independent immutable combined raw-plus-Parquet NAS recovery, covering
# the full 93-pair raw archive and the narrower 15-partition publication.
scripts/archive_player_games_snapshot.sh
scripts/restore_player_games_snapshot.sh player-games-archive-2026-<manifest-sha12>
```

`fetch-player-logs` is the only player-log network path; `build-player-games`,
unit tests, the offline source audit, archive validation, and restore
validation are all offline. The only source request host is
`stats.nba.com`. Complete valid raw cache body/sidecar pairs are never
silently overwritten or re-fetched.

### Immutable snapshot and recovery contract

The immutable combined snapshot root is
`/mnt/nas/llm/documents/nba-predictions-archive/player-games-archive-2026-<first-12-hex-of-manifest-sha256>/`,
overridable with `NBA_PLAYER_GAMES_ARCHIVE_ROOT`/`NBA_PLAYER_GAMES_DATA_DIR`/
`NBA_PLAYER_GAMES_REFERENCE_DIR`. A snapshot combines both the raw cache
(`raw/stats_nba/player_logs/`, all 93 identities) and the active Parquet
publication (`parquet-large/player_games/`, the 15-partition trusted scope)
under one name, plus a `SHA256SUMS` covering every regular file.
`scripts/archive_player_games_snapshot.sh` validates the live source (two
independent gates: the complete 93-pair raw archive, and the narrower
37-source/15-partition publication), stages a checksummed copy, and
publishes it atomically; an existing same-name snapshot is accepted only if
it already matches the freshly staged copy exactly, and is never modified
otherwise.

`scripts/restore_player_games_snapshot.sh` takes one positional snapshot
name (`player-games-archive-2026-<12 hex>`), pins the manifest digest from
both that name and the snapshot's own manifest bytes, and restores into
`NBA_PLAYER_GAMES_DATA_DIR`-relative `raw/stats_nba/player_logs` and
`parquet-large/player_games` (both of which must be absent). It verifies the
snapshot's `SHA256SUMS` inventory, revalidates both semantic gates, stages a
checksummed copy, revalidates the source snapshot again for mutation, and
delegates every JSON/Parquet semantic question to
`python -m nba_data.player_games_snapshot`:

- `validate-layout` fully revalidates one raw-archive-plus-publication pair
  offline, proving the complete 93-pair raw tree and the exact 2012–2026,
  37-source, 15-partition publication as two independent checks;
- `verify-rebuild` deterministically rebuilds the publication from the
  restored raw cache into a temporary stage and requires an exact,
  byte-for-byte match against the already-restored publication.

Either check can also be invoked standalone, to independently validate any
restored or otherwise materialized raw-plus-publication pair offline:

```bash
python -m nba_data.player_games_snapshot validate-layout \
  --raw-root data/raw/stats_nba/player_logs \
  --publication-root data/parquet-large/player_games \
  --reference-dir data/parquet
```

Restore then performs an atomic two-destination publish (raw first, then
publication) with explicit rollback on partial failure, and prints
`independent rebuild: ok` once `verify-rebuild` passes. Unlike its
`scripts/restore_pbp_snapshot.sh` sibling, the player-games restore script
intentionally omits a restore-side capacity gate and a post-rename
`validate-layout` call, since Task 5 verified those checks externally as
part of the disposable-restore acceptance procedure rather than duplicating
them inside the script. The
[final audit](audits/2026-08-02-player-games-source-audit.md) was rendered
only after an independently restored, absent-target copy of this exact
snapshot passed every one of these checks.
