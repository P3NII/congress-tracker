# Congress trade tracker

A free morning report on newly disclosed stock and option trades by members of the US House and Senate. It flags trades that were made **before a big price move** and lists every new purchase so you can paper trade them.

## How it works

1. **Data (free, official):** the House Clerk's PTR filings (yearly index + one PDF per filing) and the Senate's eFD site (HTML tables).
2. **Prices (free):** daily closes from Yahoo Finance via `yfinance`, plus the S&P 500 (SPY) over the same window for comparison.
3. **Schedule:** GitHub Actions runs `run.py` every weekday at about 6 AM Atlantic and saves `reports/latest.md` plus a dated copy. It's free on a public repo.
4. **Delivery:** a Claude scheduled task reads `reports/latest.md` before market open and sends you the summary.

## Report sections

1. Newly disclosed trades made before a big move (default ±10%): price at trade date, move before the filing went public, best move since, current move, S&P 500 over the same period, and move since filing (what a copier would actually have caught)
2. The same for filings from the last 30 days
3. Paper-trade watchlist: every new purchase, with tickers bought by 2+ members flagged
4. New sales
5. Filings that couldn't be read (scanned paper filings), with links

## Run it yourself

```bash
pip install -r requirements.txt
python run.py                  # first run looks back 30 days
python run.py --threshold 15   # change what counts as a big move
```

## Limits

- Filings are published up to 45 days after the trade, and dollar amounts are ranges.
- Paper filings are scanned images, so they're listed with links instead of being parsed.
- Option rows show the move in the underlying stock, not in the option's price.
- Some House PDFs use unusual layouts. Rows the parser can't read are skipped, not guessed.
- This is a research tool, not investment advice.
