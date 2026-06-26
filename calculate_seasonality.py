"""
Seasonality Calculator
======================
Pulls ~20 years of free daily history from Stooq (no API key, no rate limit)
and computes seasonal statistics for a watchlist:

  MONTHLY VIEW
    - For each calendar month: % of years that month closed green,
      average return, best/worst year, sample size.

  DAILY VIEW
    - For each trading day of the year (by month/day-of-month bucket):
      % of years that day closed green, average return.
    - Also a "current window" read: the next ~10 trading days from today,
      with their historical bullish probability, so you can see what the
      calendar favors right now.

Seasonality is a PROBABILISTIC bias, not a signal. It describes historical
tendency for a period, which breaks in unusual years. Use as a confluence
factor alongside price action, GEX, and expected move — never standalone.

Output: writes seasonality_data.json to the repo root.
"""

import json
import io
import time
from datetime import datetime, timezone, timedelta

import requests

# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------

# Stooq symbols. US tickers use the .US suffix; indices use ^ codes.
# We map a clean display name -> Stooq symbol.
WATCHLIST = {
    "SPY":  "spy.us",
    "QQQ":  "qqq.us",
    "IWM":  "iwm.us",
    "DIA":  "dia.us",
    "TSLA": "tsla.us",
    "NVDA": "nvda.us",
    "AAPL": "aapl.us",
    "MSFT": "msft.us",
    "AMZN": "amzn.us",
    "META": "meta.us",
}

YEARS_BACK = 20
STOOQ_URL = "https://stooq.com/q/d/l/?s={sym}&i=d"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
}

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ----------------------------------------------------------------------------
# DATA FETCH
# ----------------------------------------------------------------------------

def fetch_history(stooq_sym):
    """Return list of (date, close) tuples, oldest first, or None."""
    url = STOOQ_URL.format(sym=stooq_sym)
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200 and "Date,Open" in r.text[:60]:
                return parse_csv(r.text)
            # Stooq returns "No data" plain text when symbol is wrong/empty
        except requests.RequestException as e:
            print(f"  fetch attempt {attempt+1} failed: {e}")
        time.sleep(2)
    return None


def parse_csv(text):
    """Parse Stooq daily CSV: Date,Open,High,Low,Close,Volume."""
    rows = []
    lines = text.strip().splitlines()
    for line in lines[1:]:  # skip header
        parts = line.split(",")
        if len(parts) < 5:
            continue
        try:
            d = datetime.strptime(parts[0], "%Y-%m-%d").date()
            close = float(parts[4])
            rows.append((d, close))
        except (ValueError, IndexError):
            continue
    return rows


# ----------------------------------------------------------------------------
# SEASONAL COMPUTE
# ----------------------------------------------------------------------------

def compute_seasonality(history):
    """
    Given [(date, close), ...] oldest-first, compute monthly and daily stats.
    Daily returns = close-to-close % change.
    """
    cutoff = datetime.now(timezone.utc).date().replace(
        year=datetime.now(timezone.utc).year - YEARS_BACK)
    history = [(d, c) for d, c in history if d >= cutoff]
    if len(history) < 250:
        return None

    # daily close-to-close returns
    daily = []  # (date, ret_pct)
    for i in range(1, len(history)):
        prev_c = history[i - 1][1]
        d, c = history[i]
        if prev_c > 0:
            daily.append((d, (c - prev_c) / prev_c * 100.0))

    monthly = compute_monthly(history)
    day_buckets = compute_daily_buckets(daily)
    current = compute_current_window(daily)

    return {
        "monthly": monthly,
        "daily_window": current,
        "years_sample": YEARS_BACK,
        "data_start": history[0][0].isoformat(),
        "data_end": history[-1][0].isoformat(),
        "_day_buckets": day_buckets,  # kept for the window calc; trimmed before output
    }


def compute_monthly(history):
    """
    For each month, aggregate by YEAR: the month's return = last close of
    month vs last close of prior month. Then stats across years.
    """
    # group closes by (year, month) -> last close
    last_close = {}
    for d, c in history:
        last_close[(d.year, d.month)] = c  # overwrites; ends on last of month

    # build ordered list of (year, month, close)
    keys = sorted(last_close.keys())
    monthly_returns = {m: [] for m in range(1, 13)}  # month -> list of (year, ret)
    for i in range(1, len(keys)):
        (py, pm), (yr, mo) = keys[i - 1], keys[i]
        prev_c = last_close[keys[i - 1]]
        cur_c = last_close[keys[i]]
        if prev_c > 0:
            ret = (cur_c - prev_c) / prev_c * 100.0
            monthly_returns[mo].append((yr, ret))

    out = []
    for m in range(1, 13):
        rets = monthly_returns[m]
        if not rets:
            out.append({"month": MONTHS[m - 1], "n": 0})
            continue
        vals = [r for _, r in rets]
        greens = sum(1 for v in vals if v > 0)
        best = max(rets, key=lambda x: x[1])
        worst = min(rets, key=lambda x: x[1])
        out.append({
            "month": MONTHS[m - 1],
            "n": len(vals),
            "win_rate": round(greens / len(vals) * 100, 1),
            "avg_return": round(sum(vals) / len(vals), 2),
            "best": {"year": best[0], "ret": round(best[1], 1)},
            "worst": {"year": worst[0], "ret": round(worst[1], 1)},
        })
    return out


def compute_daily_buckets(daily):
    """Bucket daily returns by (month, day) -> stats."""
    buckets = {}
    for d, ret in daily:
        key = (d.month, d.day)
        buckets.setdefault(key, []).append(ret)
    stats = {}
    for key, vals in buckets.items():
        greens = sum(1 for v in vals if v > 0)
        stats[key] = {
            "n": len(vals),
            "win_rate": round(greens / len(vals) * 100, 1),
            "avg_return": round(sum(vals) / len(vals), 3),
        }
    return stats


def compute_current_window(daily, days_ahead=14):
    """
    Look at the next `days_ahead` calendar days from today, and for each,
    report the historical bullish probability from the day buckets.
    """
    buckets = {}
    for d, ret in daily:
        buckets.setdefault((d.month, d.day), []).append(ret)

    today = datetime.now(timezone.utc).date()
    window = []
    for offset in range(days_ahead):
        day = today + timedelta(days=offset)
        # skip weekends (no equity trading)
        if day.weekday() >= 5:
            continue
        vals = buckets.get((day.month, day.day), [])
        if not vals:
            continue
        greens = sum(1 for v in vals if v > 0)
        window.append({
            "date": day.isoformat(),
            "label": day.strftime("%a %b %d"),
            "win_rate": round(greens / len(vals) * 100, 1),
            "avg_return": round(sum(vals) / len(vals), 3),
            "n": len(vals),
        })
    return window


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------

def main():
    results = {}
    errors = []
    for name, sym in WATCHLIST.items():
        print(f"Fetching {name} ({sym}) ...")
        hist = fetch_history(sym)
        if not hist:
            print(f"  [{name}] no data")
            errors.append(name)
            continue
        stats = compute_seasonality(hist)
        if not stats:
            print(f"  [{name}] insufficient history")
            errors.append(name)
            continue
        stats.pop("_day_buckets", None)  # trim internal field
        results[name] = stats
        best_month = max((m for m in stats["monthly"] if m.get("n")),
                         key=lambda x: x["win_rate"], default=None)
        if best_month:
            print(f"  [{name}] best month: {best_month['month']} "
                  f"({best_month['win_rate']}% green over {best_month['n']}y)")
        time.sleep(1)

    output = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "method": f"{YEARS_BACK}y daily history from Stooq, close-to-close returns",
        "note": "Seasonality is a probabilistic bias, not a signal. Confluence only.",
        "errors": errors,
        "tickers": results,
    }
    with open("seasonality_data.json", "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nWrote seasonality_data.json with {len(results)} tickers. "
          f"Errors: {errors or 'none'}")


if __name__ == "__main__":
    main()
