# NBA Predictions — Project & Data Review

*Reviewed 2026-07-29. Original work: Nov–Dec 2022, DTSC 691 (data science capstone).*

## What this project is

A three-part NBA analytics capstone:

1. **Data collection** — scraped ~37 seasons (1983-84 → 2019-20, plus 2022-23) of NBA
   data from two sources: the `nba_api` package (stats.nba.com box scores) and
   basketball-reference.com (attendance, schedules, rosters, team leaders) via
   `pandas.read_html` / BeautifulSoup with a polite 12-second delay between requests.
2. **Modeling** (`NBA ML.ipynb`) — predicts per-game home/away scores (win/loss derived
   from the score diff) and game attendance using KNN, Random Forest, and Gradient
   Boosting regressors trained on cumulative season-to-date team averages. Also a
   KMeans/PCA clustering exercise that segments league history into three statistical
   "eras" (~1983–95, ~1995–2014, ~2014+).
3. **Dashboard** — a small Flask app (`venv/start.py` + `venv/templates/dashboard.html`,
   Bootstrap + DataTables) that displays 2022-23 game predictions and the era charts.

## Repository layout

| Path | What it is |
|---|---|
| `Data Retrieval and Transformation.ipynb` | One-time historical scrape + feature engineering (cumulative averages, HOME/AWAY merge, attendance join) |
| `NBA Data Automated.ipynb` | The same pipeline refactored into functions for incremental daily updates of the 2022-23 season |
| `NBA ML.ipynb` | EDA plots, PCA + KMeans era clustering, model training/evaluation, model export |
| `venv/` | **Both** a committed Windows virtualenv *and* the Flask app (start.py, templates, static assets, all data the app reads, trained models) |
| `venv/static/data/` | ~160 MB of processed datasets + 3 trained `.joblib` models |
| `nba-stats/` | ~80 MB, ~4,290 files of raw/intermediate scraped data — **not tracked by git** |

## Data inventory (the valuable part)

### Game-level tables — `venv/static/data/`

These are the cleanest, most reusable assets:

- **`nba-teams-1984-2019-ordered.csv`** (81,656 rows, 13 MB) — every regular-season
  team-game box score from 1983-84 through 2019-20, straight from stats.nba.com.
  One row per team per game: PTS, FGM/FGA, FG3M/FG3A, FTM/FTA, OREB/DREB/REB, AST,
  STL, BLK, TOV, PF, PLUS_MINUS, W/L. This is the foundational raw table.
- **`nba_transformed_now.csv`** (42,449 rows, 13 MB) — the same games pivoted to one
  row per game with `HOME_*` / `AWAY_*` column pairs plus an ATTENDANCE column joined
  from basketball-reference. (`nba-transformed-recent.csv` and `transformed-_now.csv`
  are byte-identical copies of this file.)
- **`league_boxscores_extra.csv`** (40,828 rows) — game-level table with engineered
  context features: each team's game number in the season, days since last game, and
  current home/away streaks, plus raw stats and attendance.
- **`league_cum_game_avgs.csv`** (40,828 rows, 24 MB) — **the main ML training table.**
  Same games, but every stat replaced by the team's season-to-date cumulative average
  *entering* that game (a leakage-safe design), with the actual HOME_PTS / AWAY_PTS /
  WL / ATTENDANCE kept as targets.
- **`nba_predictions_2022_2023_new.csv`** (879 rows) — model predictions for the
  2022-23 season from Dec 5, 2022 onward, with `ACTUAL_*` columns partially backfilled
  (see caveats). `nba_scores_predictions_2022_2023_new.csv` and
  `attendances_preds_new.csv` are companion outputs; `nba-eras.csv` holds the 3-era
  average stats behind the dashboard charts.
- **Trained models** — `nba-preds-gbr.joblib` (55 MB), `nba-preds-rfr.joblib` (18 MB),
  `nba-preds-knn.joblib` (10 MB). Pickled with scikit-learn 1.1.3 / Python 3.10;
  they will only load reliably in a matching environment.

### Raw scraped data — `nba-stats/` (~80 MB, untracked)

- **Rosters** — 1,050 files, `<Team>_roster_<year>.csv`, 1984–2020, 39 distinct
  franchise names (renames/relocations get separate files; Charlotte is split into
  `CHARLOTTE_HORNETS_(1988-2004)` and `(2014-Present)` variants). Columns: jersey
  number, player, position, height, weight, birth date, country, experience, college.
- **Team leaders** — 1,044 files, `<Team>_leaders_<year>.xlsx`, 1984–2020. Each
  workbook has ~21 sheets, one per stat category, with cells like
  `"Trae Young 653 (2nd)"` — team leader, value, and league rank. Note: player name,
  value, and rank are packed into a single string, so these need parsing before use.
- **`leaders/leaders.csv`** (1,517 rows, 1984–2020) — a derived team × season matrix
  with a small integer per stat category (0–3), summarizing the leaders workbooks.
- **`computed_avgs/`** (1,073 files) and **`computed_avgs_orig/`** (1,013 files) —
  per-team, per-season cumulative game averages (`<Team>_cum_game_avg_<year>.csv`),
  1983–2018 plus 2021–22. The two directories are different generations of the same
  computation (spot-checked files differ); which is authoritative isn't recorded.
- **`computed_avgs_new/`, `computed_avgs_preds/`, `computed_avgs_new_preds/`** —
  30 files each (one per current team) for the 2022-23 prediction pipeline.
- **`league/`** — 15 files, league-wide per-game cumulative-average tables for
  1983–1997 only (an abandoned partial run; the complete equivalent is
  `league_cum_game_avgs.csv`).

## Model results

From the executed notebook outputs (train/test split 80/20 on ~40k games):

| Task | Model | Test performance |
|---|---|---|
| Win/loss (via predicted scores) | Random Forest | 65.8% accuracy |
| | Gradient Boosting | 65.1% |
| | KNN (k=100, distance-weighted) | 64.3% |
| | **3-model average ensemble** | **66.0%** |
| Attendance | KNN / RFR / GBR | over-predicts by ~5–9% on test |

For context, 65–66% is a respectable result for this feature set — home teams win
~57–58% of games baseline, and Vegas lines hit roughly 68–70%.

**Live 2022-23 performance:** only 72 games (Dec 5–14, 2022) ever had actual results
backfilled into the predictions file. On those: **56.9% win/loss accuracy** (41/72),
**9.0 points MAE** per team score, and **8.1% mean absolute error** on attendance
(66 games). Small sample, and notably weaker than the historical test split — partly
because early-season cumulative averages are noisy.

## Caveats to know before reusing the data

1. **The `ACTUAL_*` columns in the predictions CSV are mostly placeholder zeros.**
   Only 72 of 879 rows are real results. Any naive accuracy computation over the whole
   file gives garbage (17.5%); filter to `ACTUAL_HOME > 0` first.
2. **First-game rows have zeroed averages.** The cumulative-average tables emit 0 for
   every stat on a team's first game of a season; the notebooks filter
   `TEAM_GAME_NUMBER > 1` before training. Do the same.
3. **Franchise continuity is not resolved.** Seattle SuperSonics → OKC Thunder,
   New Jersey → Brooklyn, both Charlottes, etc. appear as separate names. Any
   longitudinal team analysis needs a franchise-mapping table.
4. **Attendance is joined by (home team, away team, date) string match** against
   basketball-reference; unmatched games are NaN. Coverage is good but not complete.
5. **Season ID convention:** `SEASON_ID` is `2XXXX` (e.g. `22015` = 2015-16 regular
   season); notebooks use `SEASON_ID % 20000` to recover the year. Filenames use the
   season *start* year.
6. **Duplicates waste ~40 MB:** three identical 13 MB transformed CSVs, and the
   near-duplicate `computed_avgs` vs `computed_avgs_orig` trees.
7. **Notebook paths have drifted.** Cells reference files that don't exist under the
   current layout (`league_boxscores_extra2.csv`, `league_cum_game_avgs_current.csv`,
   `nba/computed_avgs/...`), and the app hardcodes `venv/static/data/...` relative
   paths — the notebooks won't re-run without path fixes.
8. **Scrapers are date-sensitive.** `NBA Data Automated.ipynb` hardcodes the 2022-23
   season, and basketball-reference has since tightened rate limits — expect to update
   both before rerunning.

## Housekeeping issues

- **A full Windows virtualenv is committed to git** — 12,526 tracked files, inflating
  `.git` to 146 MB — and the Flask app source lives *inside* it. The app
  (`start.py`, `templates/`, `static/`) should be at the repo root with `venv/`
  gitignored.
- **`nba-stats/` (the actual scraped dataset, 80 MB) is untracked** — the most
  valuable artifact has no version control or backup via this repo. Git LFS or a data
  release would fix that.
- **`requirements.txt` is UTF-16 encoded** (saved from Windows PowerShell) — pip on
  Linux can't parse it as-is. Contents are readable: Flask 2.2.2, pandas 1.5.2,
  scikit-learn 1.1.3, numpy 1.23.5, matplotlib/seaborn, joblib. No pin for `nba_api`,
  `beautifulsoup4`, or `lxml` even though the scraping notebooks need them.
- **README is one line**; `desktop.ini` in `nba-stats/` betrays a OneDrive-synced
  Windows origin.
- Heavy use of `pd.concat` in row-by-row loops and `iterrows()` makes the pipeline
  very slow (the notebooks time themselves for this reason); vectorized groupby
  operations would cut the cumulative-average computation from hours to seconds.

## What's worth salvaging

- **`league_cum_game_avgs.csv` + `league_boxscores_extra.csv`** — a clean, leakage-safe,
  ~40k-game modeling table spanning 36 seasons is genuinely useful and would take days
  to rebuild from scratch under current scraping restrictions.
- **`nba-teams-1984-2019-ordered.csv`** — a tidy raw box-score archive.
- **The rosters + leaders collections** — 37 years of player-level breadth that could
  join onto the game data for roster-aware models (the piece the original models lack).
- **The feature-engineering ideas** — rest days, home/away streaks, and
  cumulative-averages-entering-the-game are the right shape for this problem; a modern
  gradient-boosting stack (XGBoost/LightGBM) with rolling windows instead of
  season-to-date means, plus Elo or player-availability features, is the natural
  upgrade path.
