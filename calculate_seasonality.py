"""
Seasonality Calculator
======================
Pulls ~20 years of daily history from Twelve Data (free tier, works from
GitHub Actions) and computes seasonal statistics for a watchlist.

  MONTHLY VIEW
    - For each calendar month: % of years that month closed green,
      average return, best/worst year, sample size.

  DAILY VIEW
    - For each trading day of the year (by month/day-of-month bucket):
      % of years that day closed green, average return.
    - Also a "current window" read: the next ~10 trading days from today,
      with their historical bullish probability.

Seasonality is a PROBABILISTIC bias, not a signal. It describes historical
tendency for a period, which breaks in unusual years. Use as a confluence
factor alongside price action, GEX, and expected move — never standalone.

DATA SOURCE: Twelve Data (https://twelvedata.com). Requires a free API key,
read from the TWELVE_DATA_KEY environment variable (set as a GitHub Secret).
Free tier: 800 calls/day, 8 calls/minute — so we space requests out.

Output: writes seasonality_data.json to the repo root.
"""

import json
import os
import time
from datetime import datetime, timezone, timedelta

import requests

# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------

# Twelve Data uses plain ticker symbols.
WATCHLIST = ["SPY", "QQQ", "IWM", "DIA", "TSLA",
             "NVDA", "AAPL", "MSFT", "AMZN", "META"]

YEARS_BACK = 20
API_KEY = os.environ.get("TWELVE_DATA_KEY", "").strip()
TD_URL = "https://api.twelvedata.com/time_series"

# Free tier allows 8 requests/minute. We pause ~9s between calls to stay safe.
SECONDS_BETWEEN_CALLS = 9

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ----------------------------------------------------------------------------
# DATA FETCH
# ----------------------------------------------------------------------------

def fetch_history(symbol):
    """Return list of (date, close) tuples, oldest first, or None."""
    params = {
        "symbol": symbol,
        "interval": "1day",
        "outputsize": "5000",   # max; ~20 years of trading days
        "apikey": API_KEY,
        "format": "JSON",
        "order": "ASC",         # oldest first
    }
    for attempt in range(3):
        try:
            r = requests.get(TD_URL, params=params, timeout=30)
            if r.status_code == 200:
                data = r.json()
                # Rate-limit or error responses come back as {"code":..,"status":"error"}
                if isinstance(data, dict) and data.get("status") == "error":
                    msg = data.get("message", "")
                    print(f"  [{symbol}] API error: {msg}")
                    if "credits" in msg.lower() or data.get("code") == 429:
                        time.sleep(60)  # wait out the per-minute limit, retry
                        continue
                    return None
                values = data.get("values") if isinstance(data, dict) else None
                if values:
                    return parse_values(values)
                print(f"  [{symbol}] no values in response")
            else:
                print(f"  [{symbol}] HTTP {r.status_code}")
        except requests.RequestException as e:
            print(f"  [{symbol}] attempt {attempt+1} failed: {e}")
        time.sleep(3)
    return None


def parse_values(values):
    """Twelve Data values: list of {datetime, open, high, low, close, volume}."""
    rows = []
    for v in values:
        try:
            d = datetime.strptime(v["datetime"][:10], "%Y-%m-%d").date()
            close = float(v["close"])
            rows.append((d, close))
        except (ValueError, KeyError):
            continue
    rows.sort(key=lambda x: x[0])  # ensure oldest-first
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
    if not API_KEY:
        print("ERROR: TWELVE_DATA_KEY environment variable is not set.")
        print("Set it as a GitHub Secret named TWELVE_DATA_KEY.")
        # still write a file so the dashboard shows a clear state
        with open("seasonality_data.json", "w") as f:
            json.dump({
                "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                "method": "Twelve Data (API key missing)",
                "note": "No API key set. Add TWELVE_DATA_KEY as a GitHub Secret.",
                "errors": WATCHLIST,
                "tickers": {},
            }, f, indent=2)
        raise SystemExit(1)

    results = {}
    errors = []
    for i, name in enumerate(WATCHLIST):
        print(f"Fetching {name} ...")
        hist = fetch_history(name)
        if not hist:
            print(f"  [{name}] no data")
            errors.append(name)
        else:
            stats = compute_seasonality(hist)
            if not stats:
                print(f"  [{name}] insufficient history")
                errors.append(name)
            else:
                stats.pop("_day_buckets", None)
                results[name] = stats
                best_month = max((m for m in stats["monthly"] if m.get("n")),
                                 key=lambda x: x["win_rate"], default=None)
                if best_month:
                    print(f"  [{name}] best month: {best_month['month']} "
                          f"({best_month['win_rate']}% green over {best_month['n']}y)")
        # rate-limit spacing: free tier is 8 calls/min. Skip wait after last.
        if i < len(WATCHLIST) - 1:
            time.sleep(SECONDS_BETWEEN_CALLS)

    output = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "method": f"{YEARS_BACK}y daily history from Twelve Data, close-to-close returns",
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
