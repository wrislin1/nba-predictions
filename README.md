# NBA Predictions

## Project status

This repository began as a 2022 data science capstone and is being revived in
three controlled slices. Slice 1 preserves the original work and establishes a
small, portable, analysis-first repository. The archived dashboard and models
are historical artifacts, not maintained predictions.

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
- `analysis/covid_hca/` — the planned COVID-era home-court analysis.
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
.venv/bin/python -m pytest -q
```

`requirements.in` records direct dependencies; `requirements.txt` is the
fully pinned, UTF-8 environment compiled from it.

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
[build manifest](data/parquet/build_manifest.json) is the authoritative
machine-readable provenance ledger for this exact publication.

## Revival roadmap

1. Slice 1: rescue the authored project, archive local data, and remove the
   committed Windows environment from Git history.
2. Slice 2: implement validated, cached ingestion for 2019-20 through 2025-26.
3. Slice 3: publish the reproducible COVID-era home-court-advantage analysis.

The approved methodology and safety boundaries are in the
[COVID HCA revival design](docs/superpowers/specs/2026-07-29-nba-repo-revival-covid-hca-design.md).

## Historical caveats

The archived dashboard and notebooks preserve the capstone as it existed.
Their feature engineering, random train/test split, serialized models, and
2022-23 predictions have not been revalidated for current use. Treat them as
provenance and exploratory context, not as a supported forecasting product.
