import azure.functions as func
import logging
import os
import json
import re
import datetime
import requests
from azure.cosmos import CosmosClient

app = func.FunctionApp()

FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
COSMOS_ENDPOINT = os.environ.get("COSMOS_ENDPOINT")
COSMOS_KEY = os.environ.get("COSMOS_KEY")

cosmos_client = CosmosClient(COSMOS_ENDPOINT, COSMOS_KEY)
database = cosmos_client.get_database_client("TradingBots")
analyses = database.get_container_client("Analyses")

# A few categories for breadth -- "any market" rather than a fixed
# watchlist. Finnhub's free tier covers these; add more categories here
# if you want wider coverage later.
NEWS_CATEGORIES = ["general", "crypto", "forex", "merger"]


# 12:00 UTC = 8am ET during daylight saving time -- well before the
# 13:30 UTC market open, so the day's analysis is ready in time for the
# opening-bell decision if a bot chooses to read it. Weekdays only for
# now; crypto/forex technically move on weekends too, a reasonable
# extension later.
@app.timer_trigger(schedule="0 0 12 * * 1-5", arg_name="dailyTimer", run_on_startup=False)
def generate_analysis(dailyTimer: func.TimerRequest) -> None:
    headlines = fetch_broad_market_news()
    if not headlines:
        logging.warning("No headlines fetched, skipping today's analysis")
        return

    try:
        candidates = ask_gemini_for_candidates(headlines)
    except Exception as e:
        logging.error(f"Candidate-identification Gemini call failed: {e}")
        return

    grounded_candidates = ground_candidates_with_price(candidates)

    try:
        analysis = ask_gemini_for_analysis(grounded_candidates)
    except Exception as e:
        logging.error(f"Final analysis Gemini call failed: {e}")
        return

    today = datetime.date.today().isoformat()
    analyses.upsert_item({
        "id": today,
        "reportDate": today,
        "overview": analysis.get("overview", ""),
        "ideas": analysis.get("ideas", []),
        "generatedAt": datetime.datetime.utcnow().isoformat() + "Z",
    })
    logging.info(f"Wrote analysis for {today} with {len(analysis.get('ideas', []))} ideas")


def fetch_broad_market_news() -> list:
    all_items = []
    for category in NEWS_CATEGORIES:
        try:
            url = "https://finnhub.io/api/v1/news"
            response = requests.get(url, params={"category": category, "token": FINNHUB_API_KEY}, timeout=10)
            response.raise_for_status()
            items = response.json()[:15]  # cap per category to keep the prompt a reasonable size
            all_items.extend(items)
        except requests.RequestException as e:
            logging.warning(f"Failed to fetch {category} news: {e}")
    return all_items


# The word "asymmetry" lives here, in the actual instruction to the
# model -- deliberately. This is the feature's current analytical focus,
# not its name; renaming the container/function/route above doesn't
# change what we're asking Gemini to look for.
def ask_gemini_for_candidates(headlines: list) -> list:
    headlines_text = "\n".join(f"- [{h.get('category', '')}] {h.get('headline', '')}" for h in headlines)
    prompt = f"""You are a global macro and equity analyst scanning for market asymmetries -- situations
where the market's reaction to news seems disproportionately small or large relative to the
news's apparent significance.

Here are today's headlines from across global markets (stocks, crypto, forex, M&A):
{headlines_text}

Identify 3 to 5 specific tickers, assets, or situations from this news that could represent a
genuine asymmetry -- either an underreaction (an opportunity the market hasn't priced in yet) or
an overreaction (a situation priced too aggressively). For each, give a real, specific ticker
symbol if one exists, or a clear asset/market description if not (e.g. "EUR/USD", "Bitcoin").

Respond with ONLY a JSON array, no other text:
[{{"ticker_or_asset": "...", "rationale": "..."}}]
"""
    text = call_gemini(prompt)
    return parse_json_response(text, default=[])


def ground_candidates_with_price(candidates: list) -> list:
    grounded = []
    for c in candidates:
        symbol = c.get("ticker_or_asset", "")
        price_context = "(no price data available for this asset)"
        # Best-effort only -- crypto/forex/international symbols often
        # won't resolve on Finnhub's free tier, and that's fine; the
        # final analysis is told explicitly when grounding wasn't possible.
        try:
            url = "https://finnhub.io/api/v1/quote"
            response = requests.get(url, params={"symbol": symbol, "token": FINNHUB_API_KEY}, timeout=10)
            response.raise_for_status()
            q = response.json()
            if q.get("c"):
                price_context = f"${q.get('c', 0):.2f} ({q.get('dp', 0):+.2f}% vs previous close)"
        except requests.RequestException:
            pass
        grounded.append({**c, "price_context": price_context})
    return grounded


def ask_gemini_for_analysis(grounded_candidates: list) -> dict:
    candidates_text = "\n".join(
        f"- {c.get('ticker_or_asset', '')}: {c.get('rationale', '')} [price: {c.get('price_context', '')}]"
        for c in grounded_candidates
    )
    prompt = f"""You are writing today's investment analysis report for a research sandbox. This is
explicitly NOT real financial advice, and your response should read that way (hedged,
clearly a hypothesis, not a confident recommendation).

Based on an earlier scan, here are candidate market asymmetries and, where available, real current
price data:
{candidates_text}

For each candidate, write both:
1. A short, specific executive summary thesis (1-2 sentences).
2. A detailed, multi-paragraph investment report (written in clean Markdown format, roughly 150-250 words) that showcases exactly why this represents a compelling asymmetric opportunity, the core market pricing error, key risks, potential catalysts, and what metrics to watch.

Be honest if a candidate doesn't hold up under scrutiny -- it's fine to drop one
rather than force a thesis that isn't there.

Respond with ONLY a JSON object, no other text:
{{"overview": "2-3 sentence summary of today's overall market mood and primary asymmetries",
  "ideas": [
    {{
      "ticker_or_asset": "...",
      "thesis": "...",
      "detailed_report": "...",
      "suggested_direction": "long" | "short" | "watch" | "avoid",
      "price_context": "..."
    }}
  ]
}}
"""
    text = call_gemini(prompt)
    return parse_json_response(text, default={"overview": "", "ideas": []})


def call_gemini(prompt: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    response = requests.post(url, json=body, timeout=30)
    response.raise_for_status()
    return response.json()["candidates"][0]["content"]["parts"][0]["text"]


def parse_json_response(text: str, default):
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text.removeprefix("json").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Best-effort recovery: grab the first {...} or [...] block.
        match = re.search(r"[\{\[].*[\}\]]", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        logging.error(f"Could not parse JSON from model response: {text[:200]}")
        return default
