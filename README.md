# Seasonality Tracker

Historical calendar-bias dashboard for your watchlist. Pulls ~20 years of free
daily history from Stooq, computes monthly and daily seasonal statistics, and
renders a terminal-style dashboard on GitHub Pages. Same architecture as your
em-tracker and gex-tracker. $0 to run.

Live dashboard (after setup): `https://marquisbware-sys.github.io/seasonality-tracker/dashboard.html`

## What you get

- **Current window** — the next ~2 weeks of trading days, each with its
  historical % green and average return, so you see what the calendar favors now.
- **Monthly seasonality** — all 12 months color-coded by win rate, with average
  return, best/worst years, and sample size.

## Setup (same as your other trackers)

1. Create a **public** repo named `seasonality-tracker`.
2. Upload `calculate_seasonality.py`, `dashboard.html`, `seasonality_data.json`,
   this README, and create `.github/workflows/update-seasonality.yml` via
   Add file -> Create new file (type the full path so the folders are made).
3. Settings -> Actions -> General -> **Read and write permissions** -> Save.
4. Settings -> Pages -> Deploy from branch -> **main** / root -> Save.
5. Actions tab -> `update-seasonality` -> **Run workflow**, then hard-refresh
   the dashboard.

## How to use it

Seasonality is a **probabilistic bias, not a signal**. A month being 70% green
historically is a tendency, not a guarantee, and it breaks in unusual years.
Use it as a confluence factor alongside your GEX levels, expected move, and
price action — never as a standalone trigger. The current-window strip is most
useful for tilting your daily bias; the monthly grid for planning weeks ahead.

## Files

| File | Purpose |
|---|---|
| `calculate_seasonality.py` | Pulls Stooq history, computes stats, writes JSON |
| `dashboard.html` | GitHub Pages dashboard (both views) |
| `.github/workflows/update-seasonality.yml` | Weekly + manual runner |
| `seasonality_data.json` | Generated output |
