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

The committed [historical manifest](../data/parquet/historical_games_manifest.json)
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
