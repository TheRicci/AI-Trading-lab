import azure.functions as func
import logging
import os
import uuid
import datetime
import requests
from azure.cosmos import CosmosClient

app = func.FunctionApp()

TRACKED_TICKERS = os.environ.get("TRACKED_TICKERS", "AAPL,MSFT,NVDA").split(",")
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
COSMOS_ENDPOINT = os.environ.get("COSMOS_ENDPOINT")
COSMOS_KEY = os.environ.get("COSMOS_KEY")

cosmos_client = CosmosClient(COSMOS_ENDPOINT, COSMOS_KEY)
database = cosmos_client.get_database_client("TradingBots")
news_items = database.get_container_client("NewsItems")
digests = database.get_container_client("Digests")


# Three checkpoints spread through the trading day (UTC, see daily-timer's
# note on DST drift -- same caveat applies here). Deliberately offset from
# the 13:30/20:00 open/close times so this doesn't just echo the bell.
@app.timer_trigger(schedule="0 0 14,16,18 * * 1-5", arg_name="synthTimer", run_on_startup=False)
def synthesize(synthTimer: func.TimerRequest) -> None:
    for ticker in TRACKED_TICKERS:
        news_today = get_news_since_market_open(ticker)
        if not news_today:
            logging.info(f"No new news for {ticker} since open, skipping digest")
            continue

        price_summary = get_price_summary(ticker)

        try:
            digest_text = ask_gemini_for_digest(ticker, news_today, price_summary)
        except Exception as e:
            logging.error(f"Gemini digest call failed for {ticker}: {e}")
            continue

        digests.create_item({
            "id": str(uuid.uuid4()),
            "ticker": ticker,
            "digest": digest_text,
            "priceSummary": price_summary,
            "newsItemCount": len(news_today),
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        })
        logging.info(f"Wrote digest for {ticker} ({len(news_today)} items)")


def get_news_since_market_open(ticker: str) -> list:
    today = datetime.date.today().isoformat()
    query = "SELECT * FROM c WHERE c.ticker = @ticker AND STARTSWITH(c.timestamp, @today) ORDER BY c.timestamp DESC"
    params = [{"name": "@ticker", "value": ticker}, {"name": "@today", "value": today}]
    return list(news_items.query_items(query=query, parameters=params, partition_key=ticker))


def get_price_summary(ticker: str) -> str:
    try:
        url = "https://finnhub.io/api/v1/quote"
        response = requests.get(url, params={"symbol": ticker, "token": FINNHUB_API_KEY}, timeout=10)
        response.raise_for_status()
        q = response.json()
        # c=current, d=change, dp=percent change, pc=previous close
        return f"${q.get('c', 0):.2f} ({q.get('dp', 0):+.2f}% vs previous close ${q.get('pc', 0):.2f})"
    except requests.RequestException as e:
        logging.warning(f"Price lookup failed for {ticker}: {e}")
        return "(price unavailable)"


def ask_gemini_for_digest(ticker: str, news_items_list: list, price_summary: str) -> str:
    # NOTE: no retrieval-augmented history here on purpose -- Gemini's
    # own training already covers general market-pattern knowledge far
    # more broadly than a small, days-old, single-source internal index
    # could, so a from-scratch synthesis, letting the model bring its
    # own background knowledge to bear, is the better default here. See
    # the README for the fuller reasoning.
    headlines = "\n".join(f"- [{n.get('source', '')}] {n.get('headline', '')}" for n in news_items_list)
    prompt = f"""Summarize today's news for {ticker} so far, in 2-3 plain, neutral sentences.
This is internal prep for a trading bot's decision later -- be factual, not persuasive. If any
of these headlines are reporting the same underlying story from different sources, treat them
as one thing, not several.

Current price: {price_summary}
Headlines today:
{headlines}

Respond with ONLY the summary text, no preamble, no JSON, no markdown formatting.
"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    response = requests.post(url, json=body, timeout=20)
    response.raise_for_status()
    return response.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
