# Home-court advantage analysis

The committed long-run trend is a descriptive results-level view of
home-court advantage from the 1947 through 2026 seasons. Regenerate its exact
CSV, PNG, and Markdown products from the repository root with:

```bash
MPLBACKEND=Agg .venv/bin/python analysis/covid_hca/build_historical_trend.py
```

The command writes:

- `data/home_court_by_season.csv`
- `figures/home_court_trend_1947_2026.png`
- `historical_trend.md`

The historical source ends in 1983 and the modern results source begins in
1984. The chart marks that boundary and major league-era events
descriptively; it does not identify causal effects.

The later COVID fixed-effects analysis remains a separate causal-analysis
task. It will define the causal estimand and retain explicit bubble-site and
attendance semantics rather than treating this long-run chart as a causal
estimate.
