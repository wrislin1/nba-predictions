# NBA Predictions: Project and Data Findings

Reviewed from the working tree on 2026-07-29.

## Executive summary

This is a 2022 data-science capstone that collected historical NBA regular-season
data, engineered point-in-time team features, trained score and attendance models,
and displayed 2022-23 predictions in a Flask dashboard.

The project packaging is fragile, but much of the data is worth preserving:

- The strongest asset is a consistent archive of **40,828 regular-season games**
  from 1983-84 through 2018-19, represented both as 81,656 team-game records and
  as one-row-per-game tables.
- The original cumulative-average features were checked against all 81,656 source
  team-game rows. Every available running average matched a recomputation using
  only games before the current game.
- The untracked `nba-stats/` directory adds 37 seasons of roster and team-leader
  material plus later feature snapshots. All 3,243 CSV files parsed and all 1,044
  Excel workbooks opened successfully.
- The historical model notebook reports about **66.0% winner accuracy** on a random
  80/20 split. A small saved forward-looking slice performed worse: **56.9% on 72
  completed games**, below the 63.9% always-home-team baseline for that particular
  slice.

The most important immediate action is preservation. The 4,288-file `nba-stats/`
collection is not tracked by Git, while a non-portable Windows virtual environment
accounts for almost the entire tracked repository.

## Review scope and method

The findings below are based on the current files, not only on Git `HEAD`.
The working tree already contained a modified prediction CSV and the untracked
`nba-stats/` directory. Those were inspected but not changed.

Checks performed:

- inventoried Git history, tracked files, file sizes, and directory structure;
- read all three notebooks' source cells and relevant stored outputs;
- traced the Flask entry point and dashboard inputs;
- parsed every CSV with Python's standard CSV parser;
- opened every `.xlsx` as an OOXML ZIP and inspected workbook/sheet metadata;
- counted rows, columns, date ranges, season ranges, and key multiplicities;
- hashed data files to locate byte-identical copies;
- compared the historical raw, game-level, and cumulative-feature datasets;
- recomputed all historical cumulative averages from the raw team-game table;
- evaluated the saved 2022-23 predictions only where actual results are present;
- syntax-checked the Python file and every notebook code cell.

Sizes in this document are summed logical file sizes and are rounded in decimal
megabytes. Filesystem tools may report larger allocated sizes.

## What the project does

The intended pipeline is:

```text
NBA API LeagueGameFinder
  -> one row per team per game
  -> one row per game with HOME_* and AWAY_* fields
  -> attendance joined from Basketball Reference by date and team names
  -> pregame cumulative team averages, rest days, and venue streaks
  -> KNN + Random Forest + Gradient Boosting score models
  -> attendance model
  -> prediction CSV
  -> Flask/Bootstrap/DataTables dashboard
```

Confirmed sources in the tracked code are:

- `nba_api.stats.endpoints.LeagueGameFinder` for regular-season team box scores;
- Basketball Reference monthly schedule pages for attendance and future schedules.

The roster and team-leader files have Basketball Reference-style fields and values,
but their collection code is not present in the tracked notebooks. Their exact
provenance and extraction logic therefore cannot be reproduced from this repository
alone.

The roster and leader collections are not used by the saved prediction pipeline.
The models use box scores, cumulative team statistics, rest/streak features, and
attendance.

## Repository inventory

The Git repository has three commits, all from November-December 2022. It contains
12,538 tracked files totaling 514.0 MB.

| Area | Files | Payload | Git status | Finding |
|---|---:|---:|---|---|
| `venv/` | 12,526 | 511.6 MB | tracked | 99.5% of all tracked bytes; Windows Python 3.10.5 environment plus the actual app |
| `venv/static/data/` | 15 | 167.5 MB | tracked | 12 CSVs (85.4 MB) and 3 joblib models (82.1 MB) |
| `nba-stats/` | 4,288 | 73.3 MB | untracked | 3,243 CSVs, 1,044 XLSX files, and `desktop.ini` |
| Top-level notebooks | 3 | about 2.4 MB | tracked | data retrieval, automation, and modeling |
| Flask source | 2 principal files | small | tracked inside `venv/` | `venv/start.py` and `venv/templates/dashboard.html` |

The committed environment is Windows-specific:

```text
home = C:\Python310
version = 3.10.5
```

It cannot serve as a portable Linux environment. It also hides project code inside
a directory that editors and code-indexing tools conventionally ignore.

## Data inventory

### Canonical historical game data

These are the most coherent and reusable files.

| File | Rows | Coverage | Important properties |
|---|---:|---|---|
| `venv/static/data/nba-teams-1984-2019-ordered.csv` | 81,656 | 1983-10-28 to 2019-04-10 | exactly two team rows for each of 40,828 unique games |
| `venv/static/data/league_boxscores_extra.csv` | 40,828 | same 40,828 games | one row per game; raw home/away stats, rest/streak context, attendance |
| `venv/static/data/league_cum_game_avgs.csv` | 40,828 | same 40,828 games | pregame cumulative features plus score, winner, and attendance targets |

The `SEASON_ID` values run from `21983` through `22018`; the notebooks recover the
season start year with `% 20000`. The filename's `2019` is the end year of the last
season, not a season-start identifier.

Cross-file validation found:

- all 40,828 game IDs in the game-level and cumulative tables are unique;
- their game-ID sets are identical;
- home points, away points, attendance, and normalized winner labels agree for every
  game;
- the raw team-game table contains exactly two records for every game ID.

Attendance is missing for 472 of 40,828 historical games, or 1.16%.

### Cumulative team features in `nba-stats/`

| Directory | Files | Rows | Coverage | Notes |
|---|---:|---:|---|---|
| `computed_avgs_orig/` | 1,013 | 81,656 | season starts 1983-2018 | original cumulative means |
| `computed_avgs/` | 1,073 | 84,818 | 1983-2018, 2021, and partial 2022 | adds rest days, home/away streaks, and actual W/L |
| `computed_avgs_new/` | 30 | 1,044 | 2022-10-18 to 2022-12-28 | later 2022-23 snapshot |
| `computed_avgs_preds/` | 30 | 1,758 | 2022-12-05 to 2023-04-09 | scheduled-game feature rows |
| `computed_avgs_new_preds/` | 30 | 1,758 | same schedule range | another generation with different inputs |

The main `computed_avgs/` collection has a gap for season starts 2019 and 2020.
Season 2021 has a complete 2,460 team-game rows; season 2022 stops at 702 team-game
rows on 2022-12-04.

The best validation result in the review is for `computed_avgs_orig/`:

- all 81,656 feature rows matched a source team-game row;
- every non-missing `WL`, points, shooting, rebound, assist, steal, block, turnover,
  and foul running average exactly matched a recomputation from prior games;
- there were no mismatched averages;
- only one offensive-rebound average and one defensive-rebound average were blank,
  each because no earlier non-missing source observation existed.

This confirms that the core historical feature engineering was point-in-time and
did not include the current game's statistics.

### One-row `league/` exports

`nba-stats/league/` contains 15 files for 1983-1997, but every file has only one data
row. These appear to be an abandoned or partial export, not a usable league history.
The complete historical equivalent is `venv/static/data/league_cum_game_avgs.csv`.

### Rosters

The roster collection contains:

- 1,050 CSV files;
- 17,919 player-season rows;
- 39 filename-level team/lineage labels;
- season-ending years 1984-2020;
- a consistent ten-column schema.

Fields include jersey number, player, position, height, weight, birth date, country,
experience, and college. Every file parsed successfully.

Cleanup issues:

- every CSV contains a serialized pandas index;
- the country field is named `Unnamed: 6`;
- franchise changes are represented as different text labels rather than stable IDs;
- `New_Orleans_Hornets` is missing 2006 and 2007;
- `Washington_Bullets` files incorrectly continue through 2020 alongside
  `Washington_Wizards`;
- all 23 Bullets/Wizards roster pairs from 1998-2020 are byte-identical.

The last issue is a concrete alias/scraper duplication, not merely two names for a
franchise in different periods.

### Team-leader workbooks

The collection contains:

- 1,044 Excel workbooks;
- 38 filename-level team/lineage labels;
- season-ending years 1984-2020;
- 31,205 sheets;
- 40,631 leader entries.

All workbooks opened successfully. A workbook contains between 3 and 51 stat sheets,
depending on team, season, and which leader categories were available. The sheets
store strings such as:

```text
Dominique Wilkins 1750 (13th)
```

That string combines player, value, and league rank. It is valuable raw information,
but it needs a careful parser because player names can contain spaces and values may
be integers, decimals, or percentages.

There are 52 distinct sheet names across the collection, including totals, per-game
rates, shooting percentages, advanced metrics, and triple-doubles. The current
Charlotte Hornets has six roster workbooks for 2015-2020 but no matching leader
workbooks.

### Consolidated `leaders.csv`

`nba-stats/leaders.csv` and `nba-stats/leaders/leaders.csv` are byte-identical.
Each has 1,517 rows.

This derived table should not be mistaken for a normalized leaders dataset:

- it contains exactly 41 rows for each year from 1984 through 2020;
- there are only 40 distinct team strings because one Charlotte label is duplicated
  within every year;
- inactive franchise labels are retained and commonly zero-filled;
- stat values are small integers from 0 through 7;
- comparison with the workbooks shows that those integers are counts of returned
  leader entries for a category, not player names, stat values, or league ranks;
- only 41 stat columns are retained, while the workbooks collectively contain 52
  distinct sheet categories.

The raw workbooks are much more valuable than this lossy summary.

### Later transformed snapshots

These three 13.0 MB files are byte-identical:

- `venv/static/data/nba-transformed-recent.csv`
- `venv/static/data/nba_transformed_now.csv`
- `venv/static/data/transformed-_now.csv`

Each has 42,449 unique games and extends through 2022-12-10. Keeping one canonical
copy would remove 26.0 MB of redundant payload. Attendance is missing in 594 rows,
or 1.40%.

`boxscores-recent.csv` contains the 40 games from 2022-12-05 through 2022-12-10.
Eight of those 40 rows have missing attendance.

### Prediction outputs

The working copy of `nba_predictions_2022_2023_new.csv` contains 879 scheduled games
from 2022-12-05 through 2023-04-09.

Important cautions:

- only 72 rows, dated 2022-12-05 through 2022-12-14, have nonzero actual scores;
- the remaining 807 rows use zero placeholders rather than nulls;
- only 17 distinct `GAME_ID` values appear across all 879 rows;
- the automation code copies a team's most recent played game ID into future rows,
  so `GAME_ID` is not a scheduled-game identifier and cannot be used as a key;
- data snapshots are not synchronized: the general cumulative features stop on
  December 4, transformed games stop on December 10, completed prediction outcomes
  reach December 14, and `computed_avgs_new/` reaches December 28.

The prediction file was already modified relative to Git `HEAD` when this review
started. The report evaluates the working copy and does not treat it as the original
committed experiment.

## Model and analysis findings

### Saved notebook results

`NBA ML.ipynb` reports the following winner accuracies by comparing the sign of
predicted home-away scores with the actual winner:

| Regressor | Train accuracy | Random 20% test accuracy |
|---|---:|---:|
| Random Forest | 67.2% | 65.8% |
| KNN, 100 distance-weighted neighbors | 100.0% | 64.3% |
| Gradient Boosting | 98.0% | 65.1% |
| Equal average of all three | not reported | 66.0% |

The KNN and gradient-boosting train/test gaps show substantial memorization or
overfitting. The random forest is strongly regularized by its depth and split
settings.

The reported test result is not a trustworthy forward-season estimate because:

- the split is random rather than chronological;
- no `random_state` is set, so the result is not reproducible;
- games from the same teams and seasons are mixed across train and test;
- there is no rolling backtest, season holdout, confidence interval, or tuned
  baseline comparison;
- model selection and reporting use the same notebook experiment.

The feature construction itself is point-in-time, but the validation design still
allows strong temporal dependence between train and test observations.

### Attendance metric problem

The notebook labels `predicted attendance / actual attendance` as "Attendance
Accuracy" and averages the ratio. This is not an accuracy metric: over- and
under-prediction do not have symmetric effects.

There is also a pandas alignment problem. Predictions are placed in a new
zero-based DataFrame, while `y_test` retains shuffled source indexes. Assigning the
ratio back aligns on different indexes, so the stored test ratios are not reliable
model measurements. Use MAE, RMSE, MAPE with explicit zero handling, and residual
plots instead.

### Performance on saved completed predictions

Filtering the working prediction file to `ACTUAL_HOME > 0` and
`ACTUAL_AWAY > 0` gives 72 games:

| Metric | Result |
|---|---:|
| Winner accuracy | 41/72 = 56.9% |
| Home-team win rate / always-home baseline on this slice | 46/72 = 63.9% |
| Home-score MAE | 9.32 points |
| Away-score MAE | 8.77 points |
| Combined per-team score MAE | 9.04 points |
| Attendance MAE | 1,346 attendees over 66 games |
| Attendance MAPE | 8.11% over 66 games |
| Attendance mean error | +40 attendees |

This is a very small and non-independent slice, so it should be treated as a warning
rather than a definitive model ranking. It does show why the random historical
split should not be the only validation.

### Era analysis

The notebook's three principal components explain 34.35% of standardized variance.
Silhouette scores actually favor two clusters (0.2766) over three (0.2745), though
the notebook proceeds with three for an era story.

Two implementation details matter:

- KMeans is fit on the full standardized feature matrix, but its unprojected
  centroids are plotted directly on PCA coordinates. The cluster labels may still
  be usable, but the displayed centroid locations are not in the same coordinate
  system as the points.
- The final `nba-eras.csv` is produced from manually chosen date bins
  (1983-94, 1995-2013, and 2014 onward), not from KMeans-derived breakpoints.

The era averages are still descriptively interesting: the saved output shows the
rise in three-point attempts and declining free-throw attempts/personal fouls, but
the result should be described as manual era summarization informed by exploratory
clustering.

## Code and reproducibility findings

### Notebooks are research records, not a rerunnable pipeline

The three notebooks contain 67 code cells and no Markdown explanation cells.
Execution counts are highly out of order. Several cells have outputs even though
upstream cells are currently unexecuted or refer to absent files.

`NBA ML.ipynb` currently has a syntax error in cell 7:

```python
box_scores = ...
.reset_index(drop=True)
```

The later stored outputs therefore reflect an earlier cell state or execution order,
not a clean top-to-bottom run.

Referenced inputs that are absent include:

- `league_boxscores_extra2.csv`;
- `league_cum_game_avgs_current.csv`;
- `league_cum_game_avgs_recent.csv`;
- `league_cum_game_avgs_new_preds.csv`;
- `nba-transformed-current.csv`;
- `nba-attendances-current-season.csv`.

Many outputs exist under `venv/static/data/`, but the notebooks expect them in the
current directory. They also write to `nba/computed_avgs/...`, while the surviving
directory is named `nba-stats/`.

### Data collection and automation issues

The historical retrieval notebook:

- loops over Basketball Reference years beginning at `1883`, almost certainly a
  typo for `1983`;
- sleeps for 12 seconds after both successes and failures, making that typo
  especially expensive;
- catches broad exceptions and continues, leaving no failure manifest;
- identifies home teams by testing whether the matchup string contains `"vs"`;
- joins attendance by exact home-name, away-name, and date strings;
- selects attendance by positional column index `8`, which is brittle to source
  table changes;
- repeatedly filters DataFrames and concatenates one row at a time.

The automation notebook:

- hardcodes the 2022-23 season;
- chooses the Basketball Reference season as current calendar year plus one, which
  is wrong after New Year's Day for a season already in progress;
- assumes numerous mutable "current", "today", "recent", and "new" files with no
  manifest tying them to a single run;
- handles `LA Clippers` versus `Los Angeles Clippers` with one-off string rules;
- creates future rows with reused game IDs;
- defines `autoUpdate()` but does not invoke it in the saved notebook.

The row-by-row `iterrows()`/`pd.concat()` design is much slower than a grouped,
vectorized implementation and makes partial runs more likely.

### Environment and dependency issues

`requirements.txt` is UTF-16 little-endian with Windows line endings. It pins the
2022 stack, including pandas 1.5.2, NumPy 1.23.5, scikit-learn 1.1.3, and Flask 2.2.2.
It is unusual and tool-unfriendly even where package installers recognize its BOM.

The notebooks additionally require packages that are not listed:

- `nba_api`;
- `dataframe_image`;
- `requests`;
- `beautifulsoup4`;
- an HTML parser suitable for `pandas.read_html`.

There are no tests, no deterministic environment lock, no data manifest, no
documented run command, and only a one-line README.

### Flask dashboard

The Flask app is small and understandable. It reads the prediction and era CSVs,
renders the prediction table, and displays 17 era-average images.

Weaknesses:

- source, templates, assets, data, and model artifacts all live inside `venv/`;
- CSV paths are hardcoded relative to the repository working directory;
- the serving module imports a large unused scientific/ML stack;
- there is no explicit application runner or documented Flask CLI command;
- the app always loads a historical, static 2022-23 prediction file.

The Python source parses successfully, but the current Linux environment does not
have the required packages and the committed Windows virtual environment is not
portable, so the app was not executed during this review.

## Data quality and storage findings

Positive results:

- 3,243/3,243 CSVs parsed;
- 1,044/1,044 Excel workbooks opened;
- historical game keys and targets align across the central datasets;
- historical cumulative features are correctly point-in-time;
- season row counts reflect known shortened schedules, including 1998-99 and
  2011-12;
- the roster CSV schema is consistent.

Risks:

- every CSV has at least one serialized index column; some files have two;
- many row-built per-team files repeat `0` as the serialized index;
- franchise identity is encoded only in inconsistent names;
- raw and processed snapshots from different dates coexist without provenance;
- two entire season starts, 2019 and 2020, are missing from the main cumulative
  collection;
- future schedule rows do not have unique game IDs;
- prediction placeholders use zero instead of missing values;
- model files are version-bound Python pickles and should only be loaded from a
  trusted source;
- there is no license, source URL, retrieval timestamp, checksum manifest, or
  redistribution note for the collected data.

Exact hashing found 25 duplicate CSV groups and 26 redundant files. The material
duplication is dominated by:

- two redundant 13.0 MB transformed-game snapshots;
- one redundant 165 KB `leaders.csv`;
- 23 duplicate Washington Bullets/Wizards roster pairs.

`computed_avgs/` and `computed_avgs_orig/` are not byte duplicates. They are
different feature generations and should not be deleted merely because their
filenames overlap.

## What is worth keeping

### High confidence

1. `nba-teams-1984-2019-ordered.csv` as the canonical historical raw game-stat table.
2. `league_boxscores_extra.csv` as the canonical 1983-84 to 2018-19 one-row-per-game
   table.
3. `league_cum_game_avgs.csv` as a verified historical modeling table.
4. The raw roster CSVs, after preserving them unchanged and adding a team-lineage
   mapping.
5. The raw leader workbooks, which retain player, value, and league-rank information.

### Useful with qualification

1. `computed_avgs/` for rest/streak logic and 2021-22 additions, after reconciling
   missing seasons and snapshot dates.
2. The three exact transformed snapshots, reduced to one canonical copy with a
   manifest.
3. The era aggregates as descriptive outputs, not cluster-discovered truth.
4. The saved model results as a historical capstone artifact.

### Low value without reconstruction

1. `nba-stats/league/`, because each season file contains one row.
2. The consolidated `leaders.csv`, because it is a lossy count matrix with duplicate
   and inactive team rows.
3. The 2022-23 future prediction rows as a durable dataset, because most outcomes are
   placeholders and game IDs are not unique.
4. The committed virtual environment.

## Recommended preservation and modernization plan

### 1. Preserve before cleaning

- Create a read-only snapshot of `nba-stats/` immediately.
- Generate a manifest containing relative path, byte size, SHA-256, format, row or
  sheet count, date coverage, source, and retrieval date where known.
- Record that this review saw 4,288 files totaling 73,322,885 bytes.
- Do not normalize or deduplicate the only copy of raw files in place.

### 2. Separate code, raw data, derived data, and models

A practical layout would be:

```text
src/nba_predictions/
app/
notebooks/
tests/
data/raw/
data/interim/
data/processed/
models/
reports/
```

Move the real app out of `venv/`, recreate the environment from a modern dependency
file, and ignore local environments. Large data should use an immutable release,
object storage, DVC, or Git LFS rather than ordinary Git history.

### 3. Normalize the valuable collections

Create stable dimensions:

- `team_id` for the team identity used in a game;
- `franchise_id` for continuity across relocations and renames;
- `season_start_year` and `season_end_year`;
- canonical display name plus original source name.

Convert:

- roster files to one `roster_player_season` table;
- leader sheets to a long
  `(team_id, season, category, player, value, league_rank)` table;
- game files to typed Parquet with a documented primary key;
- cumulative features to a reproducible generated dataset rather than thousands of
  small CSVs.

Preserve the original source strings beside parsed fields.

### 4. Rebuild the pipeline as deterministic stages

Each stage should have explicit inputs and outputs:

1. acquire raw team games;
2. validate two team rows per game;
3. pivot home/away;
4. join attendance with an alias table and unmatched-row report;
5. calculate point-in-time rolling features;
6. train with fixed seeds and chronological folds;
7. score a schedule with a real game key;
8. backfill outcomes without overwriting the original prediction timestamp.

Tests should enforce game-key uniqueness, home/away pairing, chronological feature
cutoffs, row counts, valid score ranges, and no future observations in features.

### 5. Re-evaluate the models honestly

- Hold out whole seasons or use rolling-origin backtests.
- Report winner accuracy against home-team and rating baselines.
- Report score MAE/RMSE and margin MAE.
- Report attendance MAE/RMSE/MAPE with explicit missing-value rules.
- Separate model selection data from final evaluation data.
- Add rolling windows, Elo-style ratings, travel/rest, and roster availability before
  increasing model complexity.

## Bottom line

The project is not production-ready, but it is more than a disposable school
exercise. The historical game tables and raw roster/leader archive represent a
substantial data-collection effort. The core pregame cumulative features are also
better designed than the surrounding notebook workflow: a full recomputation
confirmed that they exclude the current game's statistics.

Treat the repository as a valuable but undocumented data archive. Preserve the raw
files first, then rebuild a small deterministic pipeline around canonical games,
team identity, provenance, and chronological evaluation.
