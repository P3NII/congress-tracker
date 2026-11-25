"""Daily congressional-trade tracker. Run:  python run.py
Fetches newly published House + Senate PTRs, prices them, writes reports/latest.md."""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

from tracker import analyze, house, report, senate

ROOT = Path(__file__).parent
DATA, REPORTS = ROOT / "data", ROOT / "reports"


def load(p: Path, default):
    return json.loads(p.read_text()) if p.exists() else default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--first-run-days", type=int, default=30, help="how far back to look on the very first run")
    ap.add_argument("--threshold", type=float, default=10.0, help="%% move that counts as 'big'")
    ap.add_argument("--keep-days", type=int, default=120, help="history kept in data/trades.json")
    a = ap.parse_args()

    DATA.mkdir(exist_ok=True)
    REPORTS.mkdir(exist_ok=True)
    today = date.today()
    seen = set(load(DATA / "seen.json", []))
    trades = load(DATA / "trades.json", [])
    since = today - timedelta(days=a.first_run_days if not seen else 10)

    new, unparsed, errors = [], [], []
    for name, mod in (("House", house), ("Senate", senate)):
        try:
            t, u = mod.fetch_recent(since, seen)
            new += t
            unparsed += u
        except Exception as e:
            print(f"[{name}] source failed: {e!r}")
            errors.append(f"{name} source unavailable today ({type(e).__name__})")

    known = {t["id"] for t in trades}
    new = [t for t in new if t["id"] not in known]
    cutoff = (today - timedelta(days=a.keep_days)).isoformat()
    trades = [t for t in trades if t["filed"] >= cutoff] + new

    # price everything filed in the last 30 days
    recent_cut = (today - timedelta(days=30)).isoformat()
    to_price = [t for t in trades if t["filed"] >= recent_cut and t.get("ticker") and t.get("traded")]
    try:
        start = min(date.fromisoformat(t["traded"]) for t in to_price) - timedelta(days=3) if to_price else today
        closes = analyze.fetch_closes([t["ticker"] for t in to_price], start)
    except Exception as e:
        print(f"[prices] failed: {e!r}")
        errors.append("price data unavailable")
        import pandas as pd
        closes = pd.DataFrame()
    priced = {t["id"]: t for t in analyze.enrich(to_price, closes)}
    new_p = [priced.get(t["id"], t) for t in new]
    recent_p = [t for t in priced.values()]

    md = report.build(today, new_p, recent_p, unparsed, errors, a.threshold)
    (REPORTS / f"{today.isoformat()}.md").write_text(md)
    (REPORTS / "latest.md").write_text(md)
    (DATA / "latest.json").write_text(json.dumps(
        {"date": today.isoformat(), "errors": errors, "new": new_p, "unparsed": unparsed}, indent=1, default=str))
    (DATA / "trades.json").write_text(json.dumps(trades, indent=1))
    (DATA / "seen.json").write_text(json.dumps(sorted(seen)))
    print(f"done: {len(new)} new transactions, {len(unparsed)} unreadable filings, errors={errors}")


if __name__ == "__main__":
    main()
