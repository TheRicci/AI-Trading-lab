"""
Flow Scraper — gathers options flow intelligence from free public sources:
Yahoo Finance options chains (volume vs OI anomalies, put/call ratios) and
Tavily web searches for publicly shared institutional flow insights.
Runs every 2 hours (offset from fed-scraper) via EventBridge Scheduler.
"""
import json
import os
import logging
import time
from datetime import datetime, timezone

import boto3
import requests

logger = logging.getLogger()
logger.setLevel(logging.INFO)

S3_BUCKET = os.environ.get("S3_SIGNAL_BUCKET", "")
TAVILY_KEY = os.environ.get("TAVILY_RESEARCH_API_KEY", "")

# Top 20 most liquid tickers for options chain analysis.  Covers major
# indices, mega-cap tech, sector ETFs, and high-volume crypto-adjacent
# equities.
TOP_TICKERS = [
    "SPY", "QQQ", "NVDA", "AAPL", "TSLA", "AMD", "META", "MSFT",
    "AMZN", "GOOGL", "XLF", "XLE", "XLK", "GLD", "TLT", "IWM",
    "SMH", "SOXX", "COIN", "MARA",
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


# ── Lambda entry point ───────────────────────────────────────────────

def lambda_handler(event, context):
    tavily_data = run_tavily_flow_searches()
    options_data = scrape_yahoo_options(TOP_TICKERS)
    signal = build_flow_signal(tavily_data, options_data)
    key = write_to_s3(signal)
    logger.info(f"Flow signal written to s3://{S3_BUCKET}/{key} — "
                f"{len(options_data)} tickers scanned")
    return {"statusCode": 200, "body": json.dumps({
        "tickers_scanned": len(options_data),
        "tavily_results": len(tavily_data),
        "s3_key": key,
    })}


# ── Tavily flow searches ────────────────────────────────────────────

def _tavily_search(query):
    if not TAVILY_KEY:
        return {"query": query, "summary": "TAVILY_KEY not configured",
                "notable_mentions": []}
    try:
        r = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_KEY,
                "query": query,
                "search_depth": "basic",
                "include_answer": True,
                "max_results": 3,
            },
            timeout=15,
        )
        if not r.ok:
            return {"query": query, "summary": f"HTTP {r.status_code}",
                    "notable_mentions": []}
        data = r.json()
        mentions = []
        for res in data.get("results", [])[:3]:
            content = (res.get("content") or "")[:250]
            mentions.append(f"{res.get('title', '')}: {content}")
        return {
            "query": query,
            "summary": (data.get("answer") or "")[:500],
            "notable_mentions": mentions,
        }
    except Exception as e:
        return {"query": query, "summary": f"Error: {e}",
                "notable_mentions": []}


def run_tavily_flow_searches():
    queries = [
        "unusual options activity today sweeps blocks largest trades",
        "cheddarflow unusual whales options flow today biggest",
        "put call ratio CBOE today market sentiment VIX",
    ]
    results = []
    for q in queries:
        results.append(_tavily_search(q))
        time.sleep(0.3)
    return results


# ── Yahoo Finance options chains ─────────────────────────────────────

def scrape_yahoo_options(tickers):
    """Fetch options chains for each ticker and compute flow metrics."""
    results = []
    for ticker in tickers:
        try:
            url = f"https://query1.finance.yahoo.com/v7/finance/options/{ticker}"
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
            if not r.ok:
                logger.warning(f"Yahoo options {ticker}: HTTP {r.status_code}")
                continue

            data = r.json()
            chain_result = data.get("optionChain", {}).get("result", [])
            if not chain_result:
                continue

            options = chain_result[0].get("options", [])
            if not options:
                continue

            calls = options[0].get("calls", [])
            puts = options[0].get("puts", [])

            total_call_vol = sum(c.get("volume", 0) or 0 for c in calls)
            total_put_vol = sum(p.get("volume", 0) or 0 for p in puts)
            pc_ratio = (total_put_vol / total_call_vol
                        if total_call_vol > 0 else 0.0)

            # Find volume spikes (volume > 3x open interest)
            spikes = []
            for opt_list, opt_type in [(calls, "call"), (puts, "put")]:
                for opt in opt_list:
                    vol = opt.get("volume", 0) or 0
                    oi = opt.get("openInterest", 0) or 0
                    if oi > 0 and vol > 3 * oi and vol > 500:
                        spikes.append({
                            "strike": opt.get("strike", 0),
                            "expiry": datetime.fromtimestamp(
                                opt.get("expiration", 0),
                                tz=timezone.utc
                            ).strftime("%Y-%m-%d") if opt.get("expiration") else "unknown",
                            "type": opt_type,
                            "volume": vol,
                            "open_interest": oi,
                            "vol_oi_ratio": round(vol / oi, 1),
                        })

            # Sort spikes by volume descending, keep top 5
            spikes.sort(key=lambda x: x["volume"], reverse=True)

            results.append({
                "ticker": ticker,
                "total_call_volume": total_call_vol,
                "total_put_volume": total_put_vol,
                "ticker_pc_ratio": round(pc_ratio, 3),
                "volume_spikes": spikes[:5],
            })
        except Exception as e:
            logger.warning(f"Yahoo options {ticker} failed: {e}")

        time.sleep(0.5)  # polite delay between tickers

    return results


# ── Build + write signal ─────────────────────────────────────────────

def build_flow_signal(tavily_data, options_data):
    now = datetime.now(timezone.utc)

    # Aggregate put/call ratio across all tickers that had data
    total_calls = sum(t.get("total_call_volume", 0) for t in options_data)
    total_puts = sum(t.get("total_put_volume", 0) for t in options_data)
    agg_pc = round(total_puts / total_calls, 3) if total_calls > 0 else 0.0

    # Simple aggregate sentiment heuristic
    if agg_pc > 1.2:
        direction = "extremely_bearish_contrarian_opportunity"
    elif agg_pc > 0.9:
        direction = "moderately_bearish"
    elif agg_pc > 0.6:
        direction = "neutral"
    elif agg_pc > 0.4:
        direction = "moderately_bullish"
    else:
        direction = "extremely_bullish_caution"

    return {
        "date": now.strftime("%Y-%m-%d"),
        "scraped_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "aggregate_put_call_ratio": agg_pc,
        "ticker_flow": options_data,
        "tavily_flow_intel": tavily_data,
        "market_sentiment": {
            "overall_direction": direction,
            "total_call_volume": total_calls,
            "total_put_volume": total_puts,
        },
    }


def write_to_s3(signal):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"flow_signals/{today}.json"
    s3 = boto3.client("s3")
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=key,
        Body=json.dumps(signal, indent=2),
        ContentType="application/json",
    )
    return key
