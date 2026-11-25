"""Price moves around each disclosed trade, using free Yahoo Finance data (yfinance)."""
from __future__ import annotations

import re
from datetime import date, timedelta

import pandas as pd


def fetch_closes(tickers: list[str], start: date) -> pd.DataFrame:
    import yfinance as yf

    tickers = sorted(set(t for t in tickers if t))
    if not tickers:
        return pd.DataFrame()
    df = yf.download(tickers + ["SPY"], start=start.isoformat(), progress=False,
                     auto_adjust=True, threads=True, group_by="column")
    closes = df["Close"] if isinstance(df.columns, pd.MultiIndex) else df[["Close"]].rename(columns={"Close": tickers[0]})
    closes.index = pd.to_datetime(closes.index).date
    return closes.dropna(how="all", axis=1)


def amount_low(amount: str) -> int:
    nums = [int(x.replace(",", "")) for x in re.findall(r"\$([\d,]+)", amount or "")]
    return nums[0] if nums else 0


def _px_on_or_after(s: pd.Series, d: date):
    s = s.dropna()
    after = s[s.index >= d]
    return (after.index[0], float(after.iloc[0])) if len(after) else (None, None)


def pct(a, b):
    return None if a in (None, 0) or b is None else round((b / a - 1) * 100, 1)


def enrich(trades: list[dict], closes: pd.DataFrame) -> list[dict]:
    """Add price-move fields to each trade that has a ticker with price data."""
    out = []
    spy = closes["SPY"] if "SPY" in closes else None
    for t in trades:
        t = dict(t)
        tk = t.get("ticker")
        if not tk or tk not in closes or not t.get("traded"):
            t["priced"] = False
            out.append(t)
            continue
        s = closes[tk].dropna()
        traded = date.fromisoformat(t["traded"])
        filed = date.fromisoformat(t["filed"])
        d0, p0 = _px_on_or_after(s, traded)
        _, pf = _px_on_or_after(s, filed)
        if p0 is None:
            t["priced"] = False
            out.append(t)
            continue
        window = s[s.index >= d0]
        pnow = float(window.iloc[-1])
        t.update({
            "priced": True,
            "px_trade": round(p0, 2),
            "px_filed": round(pf, 2) if pf else None,
            "px_now": round(pnow, 2),
            "move_to_filed": pct(p0, pf),          # what happened before the public knew
            "move_to_now": pct(p0, pnow),
            "move_since_filed": pct(pf, pnow),     # what a copier would have caught
            "max_up": pct(p0, float(window.max())),
            "max_down": pct(p0, float(window.min())),
        })
        if spy is not None:
            _, s0 = _px_on_or_after(spy, traded)
            t["spy_move_to_now"] = pct(s0, float(spy.dropna().iloc[-1])) if s0 else None
        out.append(t)
    return out


def big_move(t: dict, threshold: float) -> bool:
    """Did the stock move sharply in the trade's favour after the trade date?"""
    if not t.get("priced"):
        return False
    if t["type"].startswith("buy"):
        return (t["max_up"] or 0) >= threshold or (t["move_to_now"] or 0) >= threshold
    if t["type"].startswith("sell"):
        return (t["max_down"] or 0) <= -threshold or (t["move_to_now"] or 0) <= -threshold
    return False


def clusters(trades: list[dict], days: int = 30) -> dict[str, set[str]]:
    """Tickers bought by 2+ different politicians within the last `days` (by trade date)."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    by = {}
    for t in trades:
        if t.get("ticker") and t["type"] == "buy" and (t.get("traded") or "") >= cutoff:
            by.setdefault(t["ticker"], set()).add(t["politician"])
    return {k: v for k, v in by.items() if len(v) >= 2}
