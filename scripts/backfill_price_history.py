"""
One-time (or whenever-you-change-tickers) backfill: seeds PriceHistory
with real historical daily closes so the momentum (SMA-10) and
mean-reversion (RSI-7) bots have enough data to compute real indicators
immediately, instead of waiting ~2-3 weeks for organic accumulation from
live decisions (see the README's "honest ramp-up" section for why that
wait exists at all -- short version: Finnhub's free tier doesn't
provide historical candles for US stocks anymore).

Deliberately NOT scheduled or deployed alongside the other functions.
Two different jobs get conflated easily here, so worth being explicit:
backfilling is about the past, and the past doesn't change hour to hour
-- running this more often would just re-fetch the same settled data.
The actual continuous feed already exists and needs no help: every bot
decision, at every open and close, already records a real observed
price via record_price_point() in functions/bot. Running this script on
a schedule would add nothing there, while hammering an unofficial,
undocumented endpoint (see below) far more than the one-time use that
made it a reasonable choice in the first place.

Safe to re-run any time, though -- each ticker is checked against what's
already in PriceHistory first, and skipped if it already has enough
real daily closes. So if you add a new ticker to variables.tf's `tickers`
list later, just re-run this; it'll only fetch what's actually missing.

Source: Stooq's CSV export (stooq.com/q/d/l/) -- free, keyless, no
signup. Worth being honest about the tradeoff: this is NOT a documented,
supported API with a stability guarantee, just a long-standing CSV
download endpoint that happens to work well when queried directly. That
matters less here than it would for a live dependency, because this
script runs by hand, occasionally, and if Stooq's format ever changes,
nothing in the running system breaks -- bots simply fall back to the
organic accumulation that already works as the baseline.

Usage:
    pip install requests azure-cosmos
    COSMOS_ENDPOINT=https://your-cosmos-account.documents.azure.com:443/ \
    COSMOS_KEY=your-cosmos-key \
    TICKERS=AAPL,MSFT,NVDA \
    python3 backfill_price_history.py
"""

import os
import csv
import io
import requests
from azure.cosmos import CosmosClient

COSMOS_ENDPOINT = os.environ["COSMOS_ENDPOINT"]
COSMOS_KEY = os.environ["COSMOS_KEY"]
TICKERS = os.environ.get("TICKERS", "AAPL,MSFT,NVDA").split(",")
# More than enough for SMA-10/RSI-7 -- generous on purpose so there's
# headroom if you raise those periods later.
DAYS_BACK = int(os.environ.get("DAYS_BACK", "60"))
# The larger of the two indicator periods (RSI-7 needs 8 closes,
# SMA-10 needs 10) -- a ticker with at least this many real closes
# already doesn't need Stooq at all.
SUFFICIENT_HISTORY_THRESHOLD = 10

client = CosmosClient(COSMOS_ENDPOINT, COSMOS_KEY)
database = client.get_database_client("TradingBots")
price_history = database.get_container_client("PriceHistory")


def existing_close_count(ticker: str) -> int:
    query = "SELECT VALUE COUNT(1) FROM c WHERE c.ticker = @ticker AND c.session = 'close'"
    params = [{"name": "@ticker", "value": ticker}]
    result = list(price_history.query_items(query=query, parameters=params, partition_key=ticker))
    return result[0] if result else 0


def fetch_stooq_daily(ticker: str) -> list:
    # .us suffix is how Stooq disambiguates US-listed tickers from other
    # exchanges that might share the same short symbol.
    symbol = f"{ticker.lower()}.us"
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    response = requests.get(url, timeout=15)
    response.raise_for_status()

    text = response.text.strip()
    if not text or "," not in text.splitlines()[0]:
        raise ValueError(f"Response didn't look like CSV, got: {text[:100]!r}")

    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or "Close" not in rows[0]:
        columns = list(rows[0].keys()) if rows else "none"
        raise ValueError(f"Unexpected CSV columns for {ticker}: {columns}")
    return rows[-DAYS_BACK:]


def backfill_ticker(ticker: str) -> int:
    existing = existing_close_count(ticker)
    if existing >= SUFFICIENT_HISTORY_THRESHOLD:
        print(f"  {ticker}: already has {existing} real daily closes, skipping Stooq entirely")
        return 0

    try:
        rows = fetch_stooq_daily(ticker)
    except (requests.RequestException, ValueError) as e:
        print(f"  {ticker}: FAILED -- {e}")
        return 0

    count = 0
    prev_close = None
    for row in rows:
        try:
            date = row["Date"]  # already YYYY-MM-DD from Stooq
            close = float(row["Close"])
            high = float(row["High"])
            low = float(row["Low"])
        except (KeyError, ValueError):
            continue

        # Same natural-key upsert pattern used everywhere else in this
        # project -- re-running this script is always safe, it just
        # overwrites the same documents with the same real values.
        price_history.upsert_item({
            "id": f"{ticker}-{date}-close",
            "ticker": ticker,
            "date": date,
            "session": "close",
            "price": close,
            "high": high,
            "low": low,
            "previousClose": prev_close if prev_close is not None else close,
            "timestamp": f"{date}T21:00:00Z",  # approximate US market close in UTC
            "backfilled": True,
        })
        prev_close = close
        count += 1
    return count


def main():
    print(f"Checking {', '.join(TICKERS)} against existing PriceHistory (threshold: {SUFFICIENT_HISTORY_THRESHOLD} closes)")
    for ticker in TICKERS:
        n = backfill_ticker(ticker)
        if n:
            print(f"  {ticker}: wrote {n} historical closes")
    print(
        "Done. Momentum (SMA-10) and mean-reversion (RSI-7) bots should now "
        "have enough history to compute real indicators on their next decision, "
        "instead of using the same-day proxy fallback."
    )


if __name__ == "__main__":
    main()
