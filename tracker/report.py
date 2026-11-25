from __future__ import annotations

import re
from datetime import date

from .analyze import amount_low, big_move, clusters


def group(trades: list[dict]) -> list[dict]:
    """Merge repeat trades (same person, ticker, direction) into one row.
    Moves are measured from the earliest trade in the group."""
    g = {}
    for t in sorted(trades, key=lambda t: t.get("traded") or ""):
        k = (t["politician"], t.get("owner"), t.get("ticker"), t["type"].split()[0], t.get("asset_type"))
        if k not in g:
            g[k] = {**t, "n": 0, "lo": 0, "hi": 0, "last_traded": t.get("traded"), "ids": []}
        r = g[k]
        nums = [int(x.replace(",", "")) for x in re.findall(r"\$([\d,]+)", t["amount"] or "")]
        r["lo"] += nums[0] if nums else 0
        r["hi"] += nums[-1] if nums else 0
        r["n"] += 1
        r["last_traded"] = t.get("traded")
        r["filed"] = max(r["filed"], t["filed"])
        r["ids"].append(t["id"])
        if t.get("description") and t["description"] not in (r.get("description") or ""):
            r["description"] = ((r.get("description") or "") + " / " + t["description"]).strip(" /")
    for r in g.values():
        if r["n"] > 1:
            r["amount"] = f"{r['n']} trades, ${r['lo']:,} - ${r['hi']:,}"
            if r["last_traded"] != r["traded"]:
                r["traded"] = f"{r['traded']} to {r['last_traded']}"
    return list(g.values())


def _f(v, suffix="%"):
    return "–" if v is None else f"{v:+.1f}{suffix}"


def _lag(t):
    if not t.get("traded"):
        return t["filed"]
    d = (date.fromisoformat(t["filed"]) - date.fromisoformat(t["traded"][:10])).days
    return f"{t['filed']} ({d}d)"


def _name(t):
    tag = "" if t.get("owner") in ("self", "", None) else f" ({t['owner']})"
    return f"{t['politician']}{tag} · {t['chamber']}"


def _ticker(t):
    s = f"**{t['ticker']}**"
    if t.get("asset_type") == "option":
        s += " (option)"
    return s


def _row_move(t):
    return (f"| {_name(t)} | {_ticker(t)} | {t['type']} | {t['traded']} | {_lag(t)} | {t['amount']} "
            f"| {_f(t.get('move_to_filed'))} | {_f(t.get('max_up') if t['type'].startswith('buy') else t.get('max_down'))} "
            f"| {_f(t.get('move_to_now'))} | {_f(t.get('spy_move_to_now'))} | {_f(t.get('move_since_filed'))} |")


MOVE_HEAD = ("| Politician | Ticker | Type | Traded | Filed (lag) | Amount | Trade→filed | Best move* | Trade→now | S&P 500 same period | Since filed |\n"
             "|---|---|---|---|---|---|---|---|---|---|---|")


def build(today: date, new: list[dict], recent: list[dict], unparsed: list[dict],
          errors: list[str], threshold: float) -> str:
    L = [f"# Congress trades: morning report for {today:%A, %B %d, %Y}", ""]
    srcs = "all sources OK" if not errors else "⚠️ " + "; ".join(errors)
    n_files = len({t['source_url'] for t in new})
    L += [f"**{len(new)} new transactions** from {n_files} newly published filings · {srcs}", ""]

    winners = sorted(group([t for t in new if big_move(t, threshold)]),
                     key=lambda t: -abs(t.get("max_up") if t["type"].startswith("buy") else t.get("max_down") or 0))
    L += [f"## 1. Newly disclosed trades made before a big move (±{threshold:.0f}% or more)", ""]
    if winners:
        L += [MOVE_HEAD] + [_row_move(t) for t in winners[:30]]
        for t in winners:
            if t.get("description") and t.get("asset_type") == "option":
                L.append(f"\n- {t['ticker']} option detail ({t['politician']}): {t['description']}")
    else:
        L.append("None in today's new filings.")
    L.append("")

    new_ids = {t["id"] for t in new}
    older = sorted(group([t for t in recent if t["id"] not in new_ids and big_move(t, threshold)]),
                   key=lambda t: -abs(t.get("move_to_now") or 0))
    L += ["## 2. Same, from filings published in the last 30 days (for context)", ""]
    L += ([MOVE_HEAD] + [_row_move(t) for t in older[:15]]) if older else ["None."]
    L.append("")

    cl = clusters(recent + new)
    buys = sorted(group([t for t in new if t["type"] == "buy" and t.get("ticker")]),
                  key=lambda t: (-(len(cl.get(t["ticker"], ()))), -(t.get("lo") or amount_low(t["amount"]))))
    L += ["## 3. Paper-trade watchlist: every new purchase disclosed today", "",
          "Last close is yesterday's close. Log your paper entry at today's open price, not this number.", "",
          "| Ticker | Politician | Amount | Traded | Last close | Trade→now | Bought by 2+ members (30d) |",
          "|---|---|---|---|---|---|---|"]
    for t in buys[:40]:
        who = ", ".join(sorted(cl[t["ticker"]])) if t["ticker"] in cl else ""
        L.append(f"| {_ticker(t)} | {_name(t)} | {t['amount']} | {t['traded']} | "
                 f"{'$%.2f' % t['px_now'] if t.get('px_now') else '–'} | {_f(t.get('move_to_now'))} | {who} |")
    if not buys:
        L.append("| – | No new purchases today | | | | | |")
    L.append("")

    sells = [t for t in new if t["type"].startswith("sell") and t.get("ticker")]
    if sells:
        L += [f"## 4. New sales ({len(sells)})", ""]
        L.append(", ".join(sorted({f"{t['ticker']} ({t['politician'].split()[-1]})" for t in sells})))
        L.append("")

    if unparsed:
        L += [f"## 5. Filings that couldn't be read automatically ({len(unparsed)})", ""]
        for u in unparsed[:25]:
            L.append(f"- {u['politician']} ({u.get('chamber', '')}), filed {u['filed']}: {u['reason']}, [open filing]({u['url']})")
        L.append("")

    L += ["---",
          f"*Best move: largest gain after the trade date for buys, largest drop for sells, measured on daily closes. "
          "Option trades show the move in the underlying stock; the option itself would have moved several times more. "
          "Filings are published up to 45 days after the trade, and amounts are ranges. "
          "This is a research tool, not investment advice.*"]
    return "\n".join(L)
