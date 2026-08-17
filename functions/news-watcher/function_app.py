import azure.functions as func
import logging
import os
import datetime
import requests
from azure.cosmos import CosmosClient

app = func.FunctionApp()

FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY")
TRACKED_TICKERS = os.environ.get("TRACKED_TICKERS", "AAPL,MSFT,NVDA").split(",")
COSMOS_ENDPOINT = os.environ.get("COSMOS_ENDPOINT")
COSMOS_KEY = os.environ.get("COSMOS_KEY")

cosmos_client = CosmosClient(COSMOS_ENDPOINT, COSMOS_KEY)
database = cosmos_client.get_database_client("TradingBots")
news_items = database.get_container_client("NewsItems")

# Pure relevance filter -- keeps obviously-minor headlines out of the
# collection the synthesizer reads later. Nothing here publishes to
# Event Grid or triggers anything.
RELEVANCE_KEYWORDS = [
    "acquisition", "merger", "bankruptcy", "lawsuit", "investigation",
    "resigns", "resignation", "guidance", "recall", "halted", "fraud",
    "earnings", "upgrade", "downgrade", "outlook",
]


# Every 10 minutes. This used to be every 15 seconds, back when the
# design still chased fast reaction to news -- now that news is purely
# collected for the synthesizer to read every few hours (see the
# project README), there's no benefit to polling faster than this, and
# every 15s meant ~173,000 executions/month, which was eating a real
# (if small) chunk of Log Analytics' free monthly ingestion allowance
# for no actual freshness gain. At 10 minutes that drops to ~4,300/month.
@app.timer_trigger(schedule="0 */10 * * * *", arg_name="watchTimer", run_on_startup=False)
def watch_news(watchTimer: func.TimerRequest) -> None:
    for ticker in TRACKED_TICKERS:
        for item in fetch_company_news(ticker):
            if is_relevant(item):
                store_news_item(ticker, item)


def fetch_company_news(ticker: str) -> list:
    # NOTE: Finnhub's /company-news returns ALL of today's articles on
    # every call, not just new ones since the last poll -- so the same
    # handful of headlines will show up here roughly 40 times over a
    # trading day. That's expected; store_news_item()'s natural-key
    # upsert is what keeps that from becoming 40 duplicate documents.
    today = datetime.date.today().isoformat()
    url = "https://finnhub.io/api/v1/company-news"
    params = {"symbol": ticker, "from": today, "to": today, "token": FINNHUB_API_KEY}

    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logging.error(f"Finnhub request failed for {ticker}: {e}")
        return []


def is_relevant(item: dict) -> bool:
    headline = item.get("headline", "").lower()
    return any(keyword in headline for keyword in RELEVANCE_KEYWORDS)


def store_news_item(ticker: str, item: dict) -> None:
    # Finnhub's own numeric article id becomes the Cosmos document id,
    # and this is an upsert, not a create -- re-seeing the same article
    # on a later poll just harmlessly overwrites itself instead of
    # creating a duplicate. No query, no AI Search, no embeddings
    # needed to get idempotent dedup; the natural key does the whole job.
    news_id = item.get("id")
    if news_id is None:
        # Fallback for the rare item missing Finnhub's id field -- still
        # deterministic, so it's still idempotent across repeated polls.
        news_id = f"{ticker}-{hash(item.get('url') or item.get('headline', ''))}"

    try:
        news_items.upsert_item({
            "id": str(news_id),
            "ticker": ticker,
            "headline": item.get("headline", ""),
            "source": item.get("source", ""),
            "url": item.get("url", ""),
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        })
    except Exception as e:
        logging.error(f"Failed to store news item in Cosmos for {ticker}: {e}")
