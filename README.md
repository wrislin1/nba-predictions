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
