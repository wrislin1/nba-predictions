# Player Games & Workload (1984–2026) — Design

*Approved as the first slice of the player-availability roadmap on 2026-08-02.
This design replaces the D1 and absence-inference sections of the original
umbrella design. It publishes appearances and workload only; it does not infer
roster membership or absence.*

## Goal

Publish one source-attested row per NBA player appearance for every regular
season, playoff, and play-in game in completed ending seasons 1984–2026. The
dataset answers who appeared, for which franchise and opponent, and what
traditional box-score workload the source credited to that player.

It deliberately does **not** answer whether a non-appearing player was on the
roster, eligible, injured, resting, suspended, assigned elsewhere, or expected
to play.

## Scope

- Ending seasons: every integer from 1984 through 2026 inclusive.
- Game types: `regular` and `playoff` in every season; `play_in` in ending
  seasons 2020–2026.
- Source: stats.nba.com `LeagueGameLog`, through the repository's locked
  `nba_api` dependency, with `PlayerOrTeam=P` and one response per applicable
  `(season, game_type)`.
- Request count: exactly 93 cache identities—43 regular, 43 playoff, and 7
  play-in responses.
- Release boundary: completed seasons only. Updating to ending season 2027 or
  supporting a mutable in-progress season requires a separately reviewed scope
  extension; this slice never replaces an already valid cache body.

The source boundary was directly probed on 2026-08-02: ending season 1984
regular and playoff requests returned player rows, and ending season 2026
`PlayIn` returned six games with `005` IDs.

## Architecture

`src/nba_data/player_logs.py` owns endpoint identity, pacing/retry behavior,
raw-cache validation, and normalization of one response. It does not know how
Parquet is published. `src/nba_data/player_games_build.py` owns whole-cache
inventory, cross-source validation, exact exception-ledger enforcement,
partition/manifest creation, independent publication validation, and
transactional tree publication. `src/nba_data/player_games_snapshot.py` wraps
those semantic validators for immutable raw-plus-publication archive and
restore checks; it does not define a second data contract.

A deterministic source-audit command runs against the complete raw cache
before final publication. It emits candidate exception artifacts for explicit
human review. The builder accepts only exact entries copied into the committed
exception ledger; an unlisted discrepancy or stale ledger entry fails.

## Raw acquisition and cache contract

### Endpoint identities

| `game_type` | `season_type_all_star` | `GAME_ID` prefix | Seasons |
|---|---|---|---|
| `regular` | `Regular Season` | `002` | 1984–2026 |
| `playoff` | `Playoffs` | `004` | 1984–2026 |
| `play_in` | `PlayIn` | `005` | 2020–2026 |

Each response uses `league_id="00"`, `player_or_team_abbreviation="P"`, the
canonical `YYYY-YY` season label, ascending date order, and a 90-second request
timeout.

### Transport policy

- Run `stats_nba.preflight_dns()` before the first network request.
- Issue requests serially with at least 1.5 seconds between request starts.
- Make at most five attempts per cache identity, with 5/10/20/40-second retry
  delays for request exceptions, HTTP 429, and HTTP 5xx responses.
- Honor a numeric `Retry-After` only when it is at most 120 seconds; otherwise
  stop and report the block.
- Never retry HTTP 4xx other than 429, a schema-invalid success response, or a
  block page.
- Skip a cache identity only after verifying both its body and sidecar. A
  missing half, bad checksum, mismatched metadata identity, or invalid payload
  is a hard failure, not a reason to refetch over the evidence.

### Layout and metadata

```text
data/raw/stats_nba/player_logs/
  season=1984/
    regular.json
    regular.json.meta.json
    playoff.json
    playoff.json.meta.json
  ...
  season=2020/
    regular.json
    regular.json.meta.json
    playoff.json
    playoff.json.meta.json
    play_in.json
    play_in.json.meta.json
  ...
  season=2026/
```

Every sidecar is written atomically by `nba_data.cache.publish_bytes` and binds
the body SHA-256 and length plus these identity fields:

`provider`, `endpoint`, `season`, `season_label`, `game_type`, `season_type`,
`league_id`, and `player_or_team_abbreviation`.

The cache validator requires exactly one `LeagueGameLog` result set, unique
headers, all required headers, list-shaped rows, exact row widths, and no HTML
or block-page payload. Extra source headers are allowed and remain preserved in
the raw body; required-header removal or type-family drift fails normalization.

## Normalized table

### Publication layout

```text
data/parquet-large/player_games/
  _manifest.json
  season=1984/player_games.parquet
  season=1985/player_games.parquet
  ...
  season=2026/player_games.parquet
```

There is exactly one Zstandard-compressed Parquet file per ending season. Rows
are ordered by `season`, `game_date`, `game_id`, `franchise_id`, and
`person_id`. The primary key is `(game_id, person_id)`.

### Exact Arrow schema

| Column | Arrow type | Nullable | Meaning |
|---|---|---:|---|
| `game_id` | `large_string` | no | Zero-padded NBA Stats game ID |
| `person_id` | `int64` | no | NBA Stats player ID |
| `season` | `int64` | no | Ending season |
| `game_type` | `large_string` | no | `regular`, `playoff`, or `play_in` |
| `game_date` | `date32[day]` | no | Source game date |
| `player_name` | `large_string` | no | Source `PLAYER_NAME` verbatim |
| `nba_team_id` | `int64` | no | Source `TEAM_ID` |
| `team_name_raw` | `large_string` | no | Source `TEAM_NAME` verbatim |
| `team_abbreviation_raw` | `large_string` | no | Source team abbreviation verbatim |
| `matchup_raw` | `large_string` | no | Source `MATCHUP` verbatim |
| `franchise_id` | `large_string` | no | Existing season-aware franchise resolution |
| `opponent_franchise_id` | `large_string` | no | Other resolved franchise in the game |
| `is_home` | `bool` | no | Designated side derived positionally from `MATCHUP` |
| `min` | `int64` | yes | Source minutes; zero remains zero and null remains null |
| `pts` | `int64` | yes | Source points |
| `fgm`, `fga`, `fg3m`, `fg3a` | `int64` | yes | Source shooting counts |
| `ftm`, `fta` | `int64` | yes | Source free-throw counts |
| `oreb`, `dreb`, `reb` | `int64` | yes | Source rebound counts |
| `ast`, `stl`, `blk`, `tov`, `pf` | `int64` | yes | Source traditional box counts |
| `plus_minus` | `int64` | yes | Source plus/minus; historically sparse and never imputed |

The parser validates but does not publish `SEASON_ID`, `WL`, percentage,
fantasy, and video-availability fields. Their exact source values remain in the
attested raw response. A future consumer need does not justify expanding this
first contract preemptively.

### Identity and matchup rules

- Requested season, `SEASON_ID`, and the ending season encoded by the source
  must agree.
- Requested game type, `SEASON_ID` leading digit, and `GAME_ID` prefix must
  agree.
- Every game has exactly two distinct NBA team IDs and two distinct resolved
  franchise IDs.
- `MATCHUP` must parse as `<AWAY> @ <HOME>` or `<HOME> vs. <AWAY>`. As in the
  play-by-play worklist parser, a row's side is derived by locating its own
  abbreviation in the parsed pair; the delimiter alone is not trusted for
  neutral games whose two rows can carry identical matchup text.
- `opponent_franchise_id` comes from the other team group in the same game,
  not from an abbreviation-only historical lookup.
- Duplicate `(game_id, person_id)` rows fail. Zero-minute credited rows remain
  appearances.

## Whole-dataset validation

### Coverage

Regular-season player-log games join exactly to `games.parquet` and
`team_games.parquet` by `(game_id, franchise_id)`. There are no missing or extra
regular games or team-games.

Postseason player-log games join one-to-one to the subset of
`playoff_games.parquet` whose `season` is 1984–2026 and whose `game_type` is
`playoff` or `play_in`. Because that publication uses Basketball Reference IDs,
the cross-source identity is exactly:

```text
(season, game_date, game_type, home_franchise_id, away_franchise_id)
```

The identity must be unique on both sides. Published home/away points are then
compared after the join; they are not part of the join key.

### Player points versus published team points

For every team-game, non-null player `pts` values are summed and compared with
the corresponding published team score. Null `pts` makes the team-game a
discrepancy; it is never zero-filled.

The 2026-08-02 source probe found 29 point disagreements among the 1,886 ending
season 1984 regular team-games, so exact equality without exceptions is known
to be false. Every disagreement must match an exact `points` entry in
`src/nba_data/reference/player_game_validation_exceptions.json`, keyed by
`<game_id>|<franchise_id>` and containing `season`, `player_total`,
`published_total`, and a non-empty evidence note. Unlisted discrepancies,
value drift, and stale entries all fail.

### Player-minute plausibility

For each team-game with all minutes present, sum player minutes. Choose the
`overtime_periods` value in `0..6` that minimizes distance to
`240 + 25 × overtime_periods`. Integer totals make this minimum unique. The
residual must be at most five
minutes, and the two teams in a game must infer the same overtime count.
Postseason inferred overtime must also equal `playoff_games.overtime_periods`.

A team-game with null minutes, residual greater than five, opposing overtime
disagreement, or postseason published-overtime disagreement must match an
exact `minutes` entry in the same exception ledger. Each entry records the raw
team total or null state, inferred/published overtime values, opposing team
total, and evidence note. Unlisted discrepancies, value drift, and stale
entries fail. The known ending season 1984 277-minute source total therefore
cannot disappear inside the normal tolerance.

### Source-review checkpoint

The initial committed exception ledger is the valid empty object:

```json
{
  "minutes": {},
  "points": {}
}
```

After all 93 raw pairs are cached, `scripts/audit_player_logs.py` writes a
deterministic Markdown audit and a complete candidate exception JSON to an
explicit output directory. Implementation pauses. The user reviews the exact
source/published values and evidence. Only the approved candidate JSON is
copied into the committed ledger. No code may auto-accept, widen a tolerance,
drop a row, or alter a source value to make the gates pass.

## Manifest and publication

`_manifest.json` is deterministic, sorted JSON with no timestamps or absolute
paths. It contains:

- `schema_version` and the exact ordered Arrow schema;
- scope: ending seasons, game types, and applicable types by season;
- dependency versions;
- every raw body and sidecar relative path, SHA-256, and byte length;
- the exception-ledger and packaged franchise-reference SHA-256 values;
- SHA-256 values for the exact committed `games.parquet`,
  `team_games.parquet`, `build_manifest.json`, `playoff_games.parquet`, and
  `playoff_games_manifest.json` files used for coverage validation;
- per-partition path, Parquet SHA-256/length, row count, unique player count,
  game counts by type, and a logical-row SHA-256 independent of Parquet bytes;
- whole-publication row, player, game, and validation summaries; and
- exact point/minute exception counts.

Builds happen in a sibling stage directory. Every staged Parquet file is
reread and every manifest value is recomputed before
`publication.publish_tree` replaces the prior tree. `validate_publication`
independently walks the published tree without following symlinks, requires
exactly 43 partition files plus `_manifest.json`, rereads every partition,
re-attests every raw pair and the exception ledger, repeats the coverage joins
against the committed regular/postseason reference publications, and rejects
any stored manifest claim that cannot be recomputed. Snapshot validation
therefore receives those committed reference publications separately; they are
not duplicated inside the large-data snapshot. A checkout whose committed
reference bytes differ from the manifest pins cannot validate or restore that
snapshot.

## Commands

```bash
# Networked, serial, resumable; all 93 identities or selected ending seasons.
python -m nba_data fetch-player-logs
python -m nba_data fetch-player-logs --season 1984 --season 2026

# Offline source comparison; writes review artifacts to an explicit directory.
python scripts/audit_player_logs.py --data-dir data --output-dir /tmp/player-game-source-review

# Strictly offline deterministic publication and independent validation.
python -m nba_data build-player-games
```

Outside a source checkout, `--data-dir` remains required before the subcommand.
`--season` is repeatable for fetch and rejected for build.

## Immutable snapshot and recovery

After publication passes, the combined raw-plus-Parquet snapshot is:

```text
/mnt/nas/llm/documents/nba-predictions-archive/
  player-games-archive-2026-<first-12-hex-of-manifest-sha256>/
    raw/stats_nba/player_logs/
    parquet-large/player_games/
    SHA256SUMS
```

`scripts/archive_player_games_snapshot.sh` and
`scripts/restore_player_games_snapshot.sh` follow the play-by-play archive
contract: immutable final name, same-filesystem staging, complete sorted
checksum inventory, independent publication validation before and after copy,
copy-time source revalidation, refusal to overwrite an existing snapshot or
restore target, and no symlink/special-file traversal. Defaults are overridable
through `NBA_PLAYER_GAMES_ARCHIVE_ROOT` and `NBA_PLAYER_GAMES_DATA_DIR`.

## Tests

- Cache payload/metadata identity, incomplete pairs, checksum failure, schema
  drift, row-width drift, retry budget, pacing, and no-refetch behavior.
- Parser schema and source semantics, including a zero-minute appearance,
  nullable plus/minus, unknown game-prefix rejection, neutral-style identical
  matchup text, opponent pairing, and duplicate player-game rejection.
- Regular exact coverage and postseason composite-key one-to-one coverage.
- Points gates: exact match, unlisted mismatch, exact approved mismatch, value
  drift, stale ledger entry, and null player points.
- Minutes gates: regulation, overtime, integer-rounding residual, null minutes,
  277-minute-style outlier, opposing-team disagreement, postseason overtime
  disagreement, exact approved exception, value drift, and stale entry.
- Deterministic partition bytes/logical hashes and manifest, stage reread,
  rollback on publication failure, and independent validator tamper cases.
- Archive/restore body, sidecar, manifest, checksum, symlink, existing-target,
  and copy-time race failures.
- CLI fetch/build separation and repeatable season selection.
- Repository fresh-clone policy: no file under `data/raw/` or
  `data/parquet-large/` is tracked.

## Documentation

The final implementation updates `README.md` and `docs/data-dictionary.md` and
commits `docs/audits/2026-08-02-player-games-source-audit.md`. The audit states
the manifest SHA-256, exact release scope, counts by season/type, point and
minute exception keys, source limitations, and the explicit rule that no
appearance row does not imply absence.

## Out of scope

- Roster stints, inactive lists, absence inference, and injury reasons.
- Schedule rest, density, travel, road-trip, or timezone features.
- Official injury-report or Pro Sports Transactions ingestion.
- Mutable in-progress seasons or automatic future-season extension.
- Per-game box-score endpoints, play-by-play-derived usage, lineup stints, or
  tracking metrics.
- Any committed raw response or large Parquet file.

## Success criteria

1. Exactly 93 raw body/sidecar pairs are complete, identity-checked, and bound
   into the publication manifest.
2. Exactly 43 season partitions cover every regular and postseason game in the
   existing publications for ending seasons 1984–2026.
3. Every player-point and player-minute discrepancy is either rejected or
   matched byte-for-byte in the reviewed exact exception ledger; no stale entry
   survives.
4. Two offline builds from the same raw cache and ledger produce identical
   partition and manifest bytes, and independent validation passes.
5. The immutable NAS snapshot validates and restores into absent targets, while
   the fresh-clone size and tracking policy remain unchanged.
