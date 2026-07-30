# Home-court advantage analysis

The committed long-run trend is a descriptive results-level view of
home-court advantage from the 1947 through 2026 seasons. Regenerate its exact
CSV, PNG, and Markdown products from the repository root with:

```bash
MPLBACKEND=Agg .venv/bin/python analysis/covid_hca/build_historical_trend.py
```

That one command deterministically regenerates all three committed products:

- `analysis/covid_hca/data/home_court_by_season.csv`
- `analysis/covid_hca/figures/home_court_trend_1947_2026.png`
- `analysis/covid_hca/historical_trend.md`

The historical source ends in 1983 and the modern results source begins in
1984. The chart marks that boundary and major league-era events
descriptively; it does not identify causal effects.

The later COVID fixed-effects analysis remains a separate causal-analysis
task. The descriptive series includes only `site_status == "home"` in its
home-win and home-margin estimands while reporting `neutral` and `unknown`
counts separately. The causal phase will preserve that explicit site
classification and the distinction among `attendance_status` values
`reported_positive`, `reported_zero`, and `missing`; it will define its
estimand and bubble-period treatment rather than treating this long-run chart
as a causal estimate.
