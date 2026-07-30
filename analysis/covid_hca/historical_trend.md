# Home-court advantage across BAA/NBA history

## What is measured

The chart summarizes regular-season games classified as true home sites. Annual home-win estimates use Wilson intervals; annual mean home margins use franchise-cluster bootstrap intervals. The darker lines pool the trailing five seasons by game rather than averaging season estimates.

The inaugural 1947 estimate is 61.0% home wins with a mean home margin of +2.40 points.

## Long-run pattern

The trailing five-season home-win trend peaks in 1955 at 70.8% and reaches its minimum in 2025 at 55.1%.

At the historical/modern data-source boundary, the 1983 estimate is 62.1% and +3.73 points.

Era markers are descriptive, not causal.

## COVID context

The ending-2020 season home-site estimate is 55.3% and +2.21 points.

Neutral-site bubble games are excluded from this estimate.

The latest 2026 estimate is 55.4% and +1.73 points.

The later COVID fixed-effects analysis—not this chart—defines the causal estimand.

## Data limitations

Historical site-classification quality changes across eras. Neutral and unknown sites are excluded from the home-site estimands, so coverage varies by season. 1984–2019 inherits the legacy home-designation assumption.

## Reproduce

From the repository root, run:

```bash
MPLBACKEND=Agg .venv/bin/python analysis/covid_hca/build_historical_trend.py
```
