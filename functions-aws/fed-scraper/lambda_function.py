"""
Fed Scraper — pre-fetches Federal Reserve Board member speeches and
commentary, caches a structured JSON summary in S3 for the trading bots.
Runs every 2 hours during US market hours via EventBridge Scheduler.
"""
import json
import os
import logging
import re
import time
from datetime import datetime, timezone

import boto3
import requests

logger = logging.getLogger()
logger.setLevel(logging.INFO)

S3_BUCKET = os.environ.get("S3_SIGNAL_BUCKET", "")
TAVILY_KEY = os.environ.get("TAVILY_RESEARCH_API_KEY", "")

# Current FOMC members — update at the start of each calendar year when
# the regional-president rotation changes.
FED_MEMBERS = [
    "Jerome Powell", "Philip Jefferson", "Michael Barr", "Lisa Cook",
    "Adriana Kugler", "Christopher Waller", "Michelle Bowman",
    "John Williams",   # NY Fed — permanent voter
    "Susan Collins", "Austan Goolsbee", "Alberto Musalem", "Jeff Schmid",
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


# ── Lambda entry point ───────────────────────────────────────────────

def lambda_handler(event, context):
    speeches = scrape_fed_speeches()
    tavily_results = run_tavily_searches()
    signal = build_signal(speeches, tavily_results)
    key = write_to_s3(signal)
    logger.info(f"Fed signal written to s3://{S3_BUCKET}/{key} — "
                f"{len(speeches)} speeches, {len(tavily_results)} tavily results")
    return {"statusCode": 200, "body": json.dumps({
        "scraped_speeches": len(speeches),
        "tavily_results": len(tavily_results),
        "s3_key": key,
    })}


# ── Scrape federalreserve.gov ────────────────────────────────────────

def scrape_fed_speeches():
    """Pull recent speech metadata from the Fed's public speeches page."""
    speeches = []
    try:
        url = "https://www.federalreserve.gov/newsevents/speeches.htm"
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        if not r.ok:
            logger.warning(f"Fed speeches page returned {r.status_code}")
            return speeches

        html = r.text
        # The page lists speeches in <div class="row"> blocks with links
        # and date spans.  We use a broad regex to grab title+url pairs
        # near recognisable date strings (e.g. "November 24, 2026").
        pattern = re.compile(
            r'<a[^>]+href="(/newsevents/speech/[^"]+)"[^>]*>([^<]+)</a>'
            r'.*?'
            r'(\w+ \d{1,2}, \d{4})',
            re.DOTALL,
        )
        for m in pattern.finditer(html):
            path, title, date_str = m.group(1), m.group(2).strip(), m.group(3).strip()
            # Identify speaker from title or surrounding text
            speaker = _identify_speaker(title + " " + m.group(0))
            speeches.append({
                "speaker": speaker or "Unknown",
                "title": title,
                "date": date_str,
                "url": f"https://www.federalreserve.gov{path}",
                "source": "federalreserve.gov",
            })
    except Exception as e:
        logger.error(f"Failed to scrape Fed speeches: {e}")

    # Also check the press-releases page for FOMC statements
    try:
        url2 = "https://www.federalreserve.gov/newsevents/pressreleases.htm"
        r2 = requests.get(url2, headers={"User-Agent": USER_AGENT}, timeout=15)
        if r2.ok:
            fomc_pattern = re.compile(
                r'<a[^>]+href="(/newsevents/pressreleases/monetary\d+[^"]*)"[^>]*>'
                r'([^<]+)</a>.*?(\w+ \d{1,2}, \d{4})',
                re.DOTALL,
            )
            for m in fomc_pattern.finditer(r2.text):
                speeches.append({
                    "speaker": "FOMC",
                    "title": m.group(2).strip(),
                    "date": m.group(3).strip(),
                    "url": f"https://www.federalreserve.gov{m.group(1)}",
                    "source": "federalreserve.gov/pressreleases",
                })
    except Exception as e:
        logger.warning(f"Failed to scrape FOMC press releases: {e}")

    return speeches[:20]  # cap to avoid bloating the signal file


def _identify_speaker(text):
    """Try to match a Fed member's name in the given text."""
    for name in FED_MEMBERS:
        if name.lower() in text.lower():
            return name
        # Also check last name only
        last = name.split()[-1]
        if last.lower() in text.lower():
            return name
    return None


# ── Tavily searches ──────────────────────────────────────────────────

def _tavily_search(query):
    """Run a single Tavily search and return structured results."""
    if not TAVILY_KEY:
        return {"query": query, "summary": "TAVILY_KEY not configured", "snippets": []}
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
            return {"query": query, "summary": f"HTTP {r.status_code}", "snippets": []}
        data = r.json()
        snippets = []
        for res in data.get("results", [])[:3]:
            snippets.append(f"{res.get('title', '')}: {(res.get('content') or '')[:200]}")
        return {
            "query": query,
            "summary": (data.get("answer") or "")[:500],
            "snippets": snippets,
        }
    except Exception as e:
        return {"query": query, "summary": f"Error: {e}", "snippets": []}


def run_tavily_searches():
    now = datetime.now(timezone.utc)
    month_year = now.strftime("%B %Y")
    queries = [
        "Federal Reserve speech today hawkish dovish interest rates",
        f"FOMC member statement monetary policy inflation {month_year}",
        "site:financialjuice.com Federal Reserve",
    ]
    results = []
    for q in queries:
        results.append(_tavily_search(q))
        time.sleep(0.3)  # small courtesy delay
    return results


# ── Build + write signal ─────────────────────────────────────────────

def build_signal(speeches, tavily_results):
    now = datetime.now(timezone.utc)
    # Extract FinancialJuice-specific results
    fj_headlines = []
    general_summaries = []
    for tr in tavily_results:
        if "financialjuice" in tr.get("query", "").lower():
            fj_headlines.extend(tr.get("snippets", []))
        else:
            general_summaries.append(tr)

    # Which members were mentioned
    all_text = " ".join(
        [s.get("title", "") for s in speeches]
        + [tr.get("summary", "") for tr in tavily_results]
    )
    mentioned = [m for m in FED_MEMBERS if m.lower() in all_text.lower()]

    return {
        "date": now.strftime("%Y-%m-%d"),
        "scraped_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "speeches": speeches,
        "fomc_headlines": [
            s["title"] for s in speeches if s.get("speaker") == "FOMC"
        ],
        "tavily_summaries": general_summaries,
        "financialjuice_headlines": fj_headlines,
        "members_mentioned": list(set(mentioned)),
    }


def write_to_s3(signal):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"fed_signals/{today}.json"
    s3 = boto3.client("s3")
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=key,
        Body=json.dumps(signal, indent=2),
        ContentType="application/json",
    )
    return key
