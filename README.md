# NBA Predictions

## Project status

This repository began as a 2022 data science capstone and is being revived in
three controlled slices. Slice 1 preserves the original work and establishes a
small, portable, analysis-first repository. The archived dashboard and models
are historical artifacts, not maintained predictions.

Results-level BAA/NBA history for ending seasons 1947–2026 is delivered
through two validated regular-season publications and a separate postseason
publication, with a reproducible long-run home-court trend.
The rich historical team/player box scores remain future work in a
separately reviewed phase; the 1947–1983 delivery intentionally preserves
box-score links without collecting those pages.

## What is preserved

The verified archive contains the complete original repository and local data
collection. The historical inventory includes:

- 40,828 NBA games (81,656 team-game rows) from 1983-84 through 2018-19;
- validated point-in-time cumulative features for the historical games;
- 1,050 roster CSVs; and
- 1,044 team-season leader workbooks.

See [PROJECT_REVIEW.md](PROJECT_REVIEW.md) for the repository review and
[PROJECT_DATA_FINDINGS.md](PROJECT_DATA_FINDINGS.md) for the detailed data
audit.

## Repository layout

- `src/nba_data/` — maintained ingestion and transformation code.
- `data/parquet/` — curated, reproducible analytical tables.
- `data/reference/` — reviewed mappings, expected counts, and correction
  ledgers used by validation.
- `data/raw/` and `data/parquet-large/` — gitignored raw provider caches and
  the large play-by-play and player-game-logs Parquet publications; absent
  from a fresh clone and recovered only from the independent NAS snapshots
  described below.
- `analysis/covid_hca/` — the delivered 1947–2026 descriptive home-court
  trend and the planned COVID-era causal analysis.
- `tests/` — repository and pipeline contracts.
- `scripts/` — deterministic maintenance and verification commands.
- `archive/notebooks/` — unchanged capstone notebooks kept for provenance.
- `archive/dashboard/` — the historical Flask dashboard, outside the
  supported runtime.
- `docs/` — design, implementation plans, and preservation audits.

## Environment setup

Python 3.12 is the supported development runtime.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
.venv/bin/python -m pytest -q
```

`requirements.in` records direct dependencies; `requirements.txt` is the
fully pinned, UTF-8 environment compiled from it. The wheel metadata also
declares the pipeline's runtime dependencies and ships the franchise and
hoopR pin reference assets needed to import `nba_data` outside this checkout.
Within a source checkout, CLI commands locate the repository's `data/`
directory regardless of the current working directory. An installed CLI
requires `--data-dir /path/to/data` before the subcommand.

Regenerate the lock with an explicit upgrade so uv does not preserve stale
preferences from an existing output file:

```bash
uv pip compile --upgrade --python-version 3.12 --universal \
  requirements.in --output-file requirements.txt
```

## Data policy

Small curated Parquet tables may be committed when their provenance is
documented. Raw provider responses, the legacy `nba-stats/` tree, DuckDB
working files, local environments, and secrets remain untracked. The original
raw data and pre-rewrite repository are retained in the verified NAS archive
documented in [the preservation audit](docs/audits/2026-07-29-pre-rewrite.md).

The supported game-data contract, including fields, sources, pinned hashes,
validation gates, and recovery procedure, is in the
[data dictionary](docs/data-dictionary.md). The committed
[modern build manifest](data/parquet/build_manifest.json) and
[historical build manifest](data/parquet/historical_games_manifest.json) are
the authoritative machine-readable provenance ledgers for their exact
publications. Historical raw schedule pages remain untracked and have an
independent immutable recovery snapshot; the data dictionary documents the
network, offline-build, archive, and restore commands.

## Playoff results

`data/parquet/playoff_games.parquet` is a separate results publication for
BAA/NBA postseason games in ending seasons 1947–2026. It is not a modeling
input and does not change analysis outputs. Its 19-column schema includes
`game_type`, whose values are exactly `playoff`, `play_in`, `tiebreaker`, and
`voided`. Basketball Reference (BREF) supplies the published schedule-result
rows; pinned hoopR schedules independently cross-validate the 2020–2026
results. The companion
`data/parquet/playoff_games_manifest.json` records deterministic source,
reference, table, and validation evidence. Publishing or rebuilding this pair
leaves both the modern and historical regular-season publications
byte-identical.

All three writers coordinate through the stable advisory lock
`data/.parquet-publication.lock`, which remains outside the replaceable
`data/parquet/` directory. Pair and directory writers take its exclusive
lock; compliant readers must take its shared lock for the complete read (the
Python helper is `nba_data.publication.publication_lock`). The atomic
visibility guarantee applies only to participants in that protocol:
direct-path readers that ignore the lock can observe intermediate path states.

From a repository checkout, use this operator flow in order:

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

The immutable snapshot family is
`playoff-raw-2026-<first-12-hex-of-playoff-manifest-sha256>`. Restore targets
must be absent. If the separately owned historical raw cache is missing,
restore it with `bash scripts/restore_historical_raw.sh` before running
`restore_playoff_raw.sh`. See the data dictionary for the exact schema,
source ownership, validation keys, deterministic rebuild contract, recovery
checks, and scope limitations.

## Play-by-play archive

`data/parquet-large/pbp/` is a separate, independently published event-level
archive of play-by-play actions (one `events.parquet` per ending season,
primary key `(game_id, action_id)`), sourced from the NBA Stats V3
`PlayByPlayV3` endpoint. It is not a modeling input and does not change any
of the game-results publications. Its raw cache
(`data/raw/stats_nba/pbp/`) and its Parquet publication
(`data/parquet-large/`) are both gitignored; a repository clone contains no
raw PBP responses and no large play-by-play Parquet data, only the code and
this documentation.

The archive is verified complete for ending seasons 1997–2026, all three
game types (`regular`, `playoff`, `play_in`), 38,023 games, and 18,303,908
events. It has been independently restored into an absent temporary
destination and offline-rebuilt byte-identical to the immutable NAS
snapshot `pbp-archive-2026-e6c0a2518673`. See the
[play-by-play data dictionary section](docs/data-dictionary.md#play-by-play-archive)
for the full `events.parquet` schema, score-sentinel and running-score
semantics, worklist cross-validation, and recovery contract, and the
[final audit](docs/audits/2026-07-31-play-by-play-archive.md) for the
verified per-season totals, anomaly summary, and execution evidence.

```bash
# Networked and rate-limited; resumable full acquisition or one season.
python -m nba_data fetch-pbp --season 2026
python -m nba_data fetch-pbp

# Strictly offline deterministic publication.
python -m nba_data build-pbp

# Independent immutable combined raw-plus-Parquet NAS recovery.
scripts/archive_pbp_snapshot.sh
scripts/restore_pbp_snapshot.sh
```

## Player-game logs

`data/parquet-large/player_games/` is a separate, independently published
player-appearance/workload archive (one `player_games.parquet` per ending
season, primary key `(game_id, person_id)`), sourced from the NBA Stats
`LeagueGameLog` endpoint in player mode. It is not a modeling input and does
not change any of the game-results publications. Its raw cache
(`data/raw/stats_nba/player_logs/`) and its Parquet publication
(`data/parquet-large/`) are both gitignored; a repository clone contains no
raw player-log responses and no large player-game Parquet data, only the code
and this documentation.

The raw source archive covers the complete ending-season range 1984–2026 (93
consumed cache identities: 43 regular, 43 playoff, seven play-in). The first
**trusted publication**, however, is deliberately narrower: ending seasons
2012–2026 only, consuming exactly 37 of those 93 cache identities (15
regular, 15 playoff, seven play-in) and writing exactly 15 partitions. Ending
seasons 1984–2011 remain **quarantined raw source evidence**, not a released
workload: the full 1984–2026 diagnostic audit reproduces 1,082,012 raw player
rows, 52,529 games, and 105,058 team-game sides, and finds 662 point and
1,211 minute discrepancy candidates (1,300 unique affected team-game keys,
1,164 unique affected games, 573 keys present in both sections) concentrated
before ending season 2012. None of those historical candidates are approved,
corrected, or published; see the
[player-game-logs data dictionary section](docs/data-dictionary.md#player-game-logs)
for the exact lossless cause breakdown and the
[final audit](docs/audits/2026-08-02-player-games-source-audit.md) for the
full rendered two-scope evidence. The trusted 2012–2026 publication itself
observes zero point and zero minute discrepancies under the unchanged strict
minute rule, applies zero of the 36 historical matchup repairs, and applies
all ten 2025–2026 designation repairs.

> A missing `player_games` row means only that LeagueGameLog did not credit
> an appearance; it does not prove roster membership, eligibility, injury,
> rest, or expected participation.

```bash
# Networked and rate-limited; resumable full acquisition or one season.
python -m nba_data fetch-player-logs --season 2026
python -m nba_data fetch-player-logs

# Strictly offline deterministic full-corpus (1984-2026) source audit;
# reproduces the historical candidate counts but never approves them.
python scripts/audit_player_logs.py --data-dir data --output-dir /tmp/player-game-source-review

# Strictly offline deterministic publication of the trusted 2012-2026
# scope: 15 season partitions plus _manifest.json.
python -m nba_data build-player-games

# Independent immutable combined raw-plus-Parquet NAS recovery, covering
# the full 93-pair raw archive and the narrower 15-partition publication.
scripts/archive_player_games_snapshot.sh
scripts/restore_player_games_snapshot.sh
```

`fetch-player-logs` is the only player-log network path; `build-player-games`,
unit tests, the offline source audit, archive validation, and restore
validation are all offline. See the
[player-game-logs data dictionary section](docs/data-dictionary.md#player-game-logs)
for the full schema, coverage-join contracts, exception-ledger semantics, and
recovery contract.

## Revival roadmap

1. Slice 1: rescue the authored project, archive local data, and remove the
   committed Windows environment from Git history.
2. Slice 2: delivered validated modern results through 2025-26 and
   results-level BAA/NBA history back to 1946-47.
3. Slice 3: delivered the descriptive 1947–2026 trend; the modern COVID
   fixed-effects analysis is the next causal-analysis task.

The approved methodology and safety boundaries are in the
[historical results design](docs/superpowers/specs/2026-07-29-historical-game-results-design.md)
and the
[COVID HCA revival design](docs/superpowers/specs/2026-07-29-nba-repo-revival-covid-hca-design.md).

## Historical caveats

The archived dashboard and notebooks preserve the capstone as it existed.
Their feature engineering, random train/test split, serialized models, and
2022-23 predictions have not been revalidated for current use. Treat them as
provenance and exploratory context, not as a supported forecasting product.
