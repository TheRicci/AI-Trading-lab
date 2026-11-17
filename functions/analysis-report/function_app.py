import azure.functions as func
import logging
import os
import json
import re
import datetime
import time
import requests
from azure.cosmos import CosmosClient
import tavily_budget

BUDGET = tavily_budget.TavilyBudget()

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
TAVILY_API_KEY = os.environ.get("TAVILY_RESEARCH_API_KEY") or os.environ.get("TAVILY_API_KEY")
COSMOS_ENDPOINT = os.environ.get("COSMOS_ENDPOINT")
COSMOS_KEY = os.environ.get("COSMOS_KEY")


def post_with_retry(
    url: str,
    headers: dict | None = None,
    json: dict | None = None,
    timeout: int = 60,
    max_retries: int = 6,
) -> requests.Response:
    for attempt in range(max_retries):
        try:
            r = requests.post(url, headers=headers, json=json, timeout=timeout)
            if r.status_code in (429, 503):
                # 429 = rate limited; 503 = transient overload (high demand). Both retryable.
                # Use exponential backoff to clear 1-minute API rate limit windows
                wait = min(60.0, 5.0 * (2 ** attempt))
                try:
                    err_msg = r.json().get("error", {}).get("message", "")
                    m = re.search(r"try again in ([0-9.]+)s", err_msg, re.IGNORECASE)
                    if m:
                        wait = min(float(m.group(1)) + 0.5, 60.0)
                except Exception:
                    pass
                logging.warning(
                    f"Rate limited ({r.status_code}). Retrying in {wait:.1f}s (attempt {attempt + 1}/{max_retries})..."
                )
                time.sleep(wait)
                continue
            return r
        except requests.RequestException as e:
            if attempt == max_retries - 1:
                raise
            logging.warning(f"Request error: {e}. Retrying in 2s...")
            time.sleep(2.0)
    return r


cosmos_client = CosmosClient(COSMOS_ENDPOINT, COSMOS_KEY)
database = cosmos_client.get_database_client("TradingBots")
analyses = database.get_container_client("Analyses")


@app.timer_trigger(schedule="0 0 12 * * 1-5", arg_name="dailyTimer", run_on_startup=False)
def generate_analysis_timer(dailyTimer: func.TimerRequest) -> None:
    """Scheduled morning run at 12:00 UTC (8am ET during DST), ahead of market open."""
    run_daily_analysis()


@app.route(route="generate-analysis", methods=["POST", "GET"])
def generate_analysis_http(req: func.HttpRequest) -> func.HttpResponse:
    """On-demand endpoint to trigger or refresh the daily analysis."""
    try:
        reports = run_daily_analysis()
        if reports:
            return func.HttpResponse(
                json.dumps({"status": "success", "reportsCount": len(reports)}),
                mimetype="application/json",
                status_code=200,
            )
        return func.HttpResponse(
            json.dumps({"status": "error", "message": "Failed to generate report"}),
            mimetype="application/json",
            status_code=500,
        )
    except Exception as e:
        logging.error(f"HTTP triggered analysis failed: {e}")
        return func.HttpResponse(
            json.dumps({"status": "error", "error": str(e)}),
            mimetype="application/json",
            status_code=500,
        )


def _generate_single_analysis(provider: str, model: str, today: str) -> dict | None:
    logging.info(f"Starting daily analysis report generation using {provider}/{model}...")
    try:
        raw_analysis = ask_llm_for_market_asymmetries(provider, model)
    except Exception as e:
        logging.error(f"Market scan call failed for {model}: {e}")
        return None

    if not isinstance(raw_analysis, dict):
        logging.error(f"Response was not a valid dictionary for {model}")
        return None

    raw_ideas = raw_analysis.get("ideas", [])
    if not raw_ideas:
        logging.warning(f"No ideas returned in analysis for {model}")
        return None

    verified_ideas = []
    for idea in raw_ideas:
        raw_ticker = idea.get("ticker") or idea.get("ticker_or_asset") or ""
        ticker, price_at_analysis, price_context = resolve_and_price_ticker(raw_ticker)

        if not ticker:
            continue

        verified_ideas.append({
            "ticker_or_asset": ticker,
            "company_or_asset_name": idea.get("company_or_asset_name") or idea.get("asset_name") or ticker,
            "thesis": idea.get("thesis", "").strip(),
            "detailed_report": idea.get("detailed_report", "").strip(),
            "suggested_direction": str(idea.get("suggested_direction", "watch")).strip().lower(),
            "price_context": price_context,
            "price_at_analysis": price_at_analysis,
        })

    report = {
        "id": f"{today}-{provider}",
        "reportDate": today,
        "model": model,
        "provider": provider,
        "overview": raw_analysis.get("overview", "").strip(),
        "ideas": verified_ideas,
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    try:
        analyses.upsert_item(report)
        logging.info(f"Successfully saved analysis for {today} ({provider}) with {len(verified_ideas)} ideas to Cosmos DB")
        return report
    except Exception as e:
        logging.error(f"Failed to save analysis report to Cosmos DB for {provider}: {e}")
        return None


def run_daily_analysis() -> dict:
    today = datetime.date.today().isoformat()
    reports = {}

    # The daily analysis is intentionally a single, well-thought-out report.
    # Keep it Gemini-only: one deep, self-consistent pass (with live Tavily
    # context baked into the prompt) instead of two providers duplicating the
    # work. This also halves the searches this feature spends from the shared
    # monthly budget.
    report = _generate_single_analysis("gemini", GEMINI_MODEL or "gemini-3.6-flash", today)
    if report:
        reports["gemini"] = report

    return reports


def search_tavily(query: str, max_results: int = 4) -> str:
    """Fetch live web search results from Tavily (basic = 1 credit)."""
    key = (TAVILY_API_KEY or "").strip().strip('"').strip("'")
    if not key:
        return ""
    if not BUDGET.allowance(1):
        logging.warning(f"[TavilyBudget] Blocking search (limit reached): {BUDGET.status()}")
        return ""
    BUDGET.spend(1)
    try:
        url = "https://api.tavily.com/search"
        payload = {
            "api_key": key,
            "query": query,
            "search_depth": "basic",
            "include_answer": True,
            "max_results": max_results,
        }
        res = post_with_retry(url, json=payload, timeout=20)
        if res.ok:
            data = res.json()
            answer = data.get("answer", "")
            results = data.get("results", [])
            snippets = []
            if answer:
                snippets.append(f"Summary: {answer}")
            for r in results:
                title = r.get("title", "")
                content = r.get("content", "")
                snippets.append(f"- [{title}]: {content}")
            return "\n".join(snippets)
        logging.warning(f"Tavily search failed ({res.status_code}): {res.text}")
    except Exception as e:
        logging.warning(f"Tavily search error for '{query}': {e}")
    return ""


def fetch_live_market_intelligence() -> str:
    """Fetch today's live breaking macro news and sector catalysts via Tavily.

    This is the deep-dive analysis task, so it legitimately spends more of the
    monthly Tavily budget than the per-review bots do (4 targeted searches per
    run). The budget tracker still blocks once the 1k/month free cap is hit.
    """
    today = datetime.date.today().isoformat()
    # Combine the queries into a single, comprehensive search to save Tavily budget
    q = f"stock market breaking news catalysts sector movements macro drivers earnings {today}"

    logging.info(f"Gathering live market intelligence via Tavily for {today}...")
    res = search_tavily(q, max_results=6)

    if not res:
        return "(Live web search context unavailable; relying on model intelligence)"

    return f"### Market Intelligence for {today}:\n{res}"


def ask_llm_for_market_asymmetries(provider: str, model: str) -> dict:
    today_str = datetime.date.today().isoformat()
    live_news = fetch_live_market_intelligence()

    prompt = f"""You are a global macro and multi-asset research analyst producing today's ({today_str}) market analysis.

Here is live, real-time market intelligence and breaking news gathered from the web immediately prior to this analysis:
{live_news}

Your job today is to pick the SINGLE most important tradable asset of the day — the one setup you believe matters most right now — and produce a deep, detailed report on it. Consider assets across equities, commodities, forex, crypto, and Brazilian (B3) markets. Choose the single highest-conviction, most market-relevant pick.

SUPPORTED ASSET MARKETS & TICKER FORMATS:
1. US Equities & ETFs: Use standard ticker (e.g. NVDA, AAPL, MSFT, SPY, QQQ, GLD, USO, TLT).
2. Brazilian Equities (B3): Use .SA suffix or B3 ticker (e.g. PETR4.SA, VALE3.SA, ITUB4.SA, BOVA11.SA).
3. Cryptocurrencies (quoted in USD): Use COIN/USD format (e.g. BTC/USD, ETH/USD, SOL/USD).
4. Global Equities & Forex: Use standard tickers (e.g. ASML, TSM, SIEGY, GBPUSD=X, EURUSD=X).

Respond ONLY with a JSON object, with no conversational filler, in exactly this schema:
{{
  "overview": "3-5 sentence overall market analysis for the day: broad market mood, dominant macro regime, key themes, and what is moving markets today.",
  "ideas": [
    {{
      "ticker": "<One valid ticker for your single pick-of-the-day, e.g. NVDA, BTC/USD, PETR4.SA, SPY>",
      "company_or_asset_name": "<Full company or asset name, e.g. Nvidia Corp, Bitcoin, Petrobras PN>",
      "thesis": "<1-2 sentence executive thesis explaining why this is THE most important setup today>",
      "detailed_report": "<A deep, multi-paragraph Markdown report (~400-700 words) with clear headings: ## Core Thesis, ## Why Today, ## Market Mispricing, ## Catalysts & Risks, ## Key Levels / What to Watch>",
      "suggested_direction": "long" | "short" | "watch" | "avoid"
    }}
  ]
}}
Important: output exactly ONE idea object in the ideas array — the single most important asset of the day. Make it genuinely deep and decision-useful.
"""
    if provider == "groq":
        text = call_openai_compatible("https://api.groq.com/openai/v1", (GROQ_API_KEY or "").strip().strip('"').strip("'"), model, prompt)
    else:
        text = call_gemini(model, prompt)
    return parse_json_response(text, default={"overview": "", "ideas": []})


def call_gemini(model: str, prompt: str) -> str:
    """Agentic Tavily search loop for Gemini.

    Mirrors functions/bot/function_app.py. The model decides what it wants to
    search; we run each query through Tavily and feed results back, iterating
    until it produces its final JSON. A turn budget and a prompt hint encourage
    it to minimize searches so we stay inside the free Tavily monthly quota.
    """
    key = (GEMINI_API_KEY or "").strip().strip('"').strip("'")
    if not model:
        model = "gemini-3.6-flash"

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"

    tools = [
        {
            "functionDeclarations": [
                {
                    "name": "web_search",
                    "description": (
                        "Search the live web for recent stock market news, asset movements, "
                        "macro updates, earnings, and financial data. Use it only when you "
                        "genuinely need fresh information."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Search query for financial information"}
                        },
                        "required": ["query"],
                    },
                }
            ]
        }
    ]

    contents = [
        {
            "role": "user",
            "parts": [
                {
                    "text": prompt
                    + "\n\nUse web_search calls to gather live context only when needed. "
                    + "Minimize the number of searches: do as few as you need, then produce your final JSON decision."
                }
            ],
        }
    ]

    for turn in range(4):
        body = {
            "contents": contents,
            "tools": tools,
            "generationConfig": {"temperature": 0.7},
        }
        response = post_with_retry(url, json=body, timeout=120)
        if not response.ok:
            logging.error(f"Gemini API error ({response.status_code}) for model '{model}': {response.text}")
            fallback_models = ["gemini-1.5-pro", "gemini-2.5-flash", "gemini-2.0-flash"]
            for fb_model in fallback_models:
                if fb_model != model:
                    logging.info(f"Attempting fallback to '{fb_model}'...")
                    fallback_url = f"https://generativelanguage.googleapis.com/v1beta/models/{fb_model}:generateContent?key={key}"
                    fallback_resp = post_with_retry(fallback_url, json=body, timeout=90)
                    if fallback_resp.ok:
                        fallback_cand = (fallback_resp.json().get("candidates") or [{}])[0]
                        fallback_parts = fallback_cand.get("content", {}).get("parts", [])
                        ftexts = [p.get("text", "") for p in fallback_parts if "text" in p]
                        if ftexts:
                            return "\n".join(t.strip() for t in ftexts if t.strip())
            raise RuntimeError(f"Gemini API error ({model}): {response.text[:500]}")

        data = response.json()
        candidates = data.get("candidates") or []
        if not candidates:
            break
        parts = (candidates[0].get("content") or {}).get("parts") or []

        calls = [pt for pt in parts if "functionCall" in pt]
        if calls:
            # Echo the model's full function-call part back verbatim (including
            # its thought_signature if present) so Gemini 3 accepts the turn.
            contents.append(
                {
                    "role": "model",
                    "parts": [dict(call) for call in calls],
                }
            )
            for call in calls:
                fc = call["functionCall"]
                name = fc.get("name", "")
                args = fc.get("args") or {}
                query = args.get("query", "") if isinstance(args, dict) else ""
                if name == "web_search" and query:
                    result_str = search_tavily(query)
                else:
                    result_str = "No usable query."
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": name,
                                    "response": {"result": result_str},
                                }
                            }
                        ],
                    }
                )
            continue

        texts = [pt.get("text", "") for pt in parts if "text" in pt]
        if texts:
            return "\n".join(t.strip() for t in texts if t.strip())

    # Final fallback: request a plain answer without further searching.
    last = {
        "contents": contents
        + [{"role": "user", "parts": [{"text": "Produce your final JSON decision now without further searching."}]}],
        "generationConfig": {"temperature": 0.7},
    }
    response = post_with_retry(url, json=last, timeout=120)
    if response.ok:
        data = response.json()
        candidates = data.get("candidates") or []
        if candidates:
            parts = (candidates[0].get("content") or {}).get("parts") or []
            texts = [p.get("text", "") for p in parts if "text" in p]
            if texts:
                return "\n".join(t.strip() for t in texts if t.strip())
    raise RuntimeError(f"Gemini Tavily could not obtain final decision from {model}")


def call_openai_compatible(base_url: str, api_key: str, model: str, prompt: str) -> str:
    url = f"{base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "response_format": {"type": "json_object"},
    }
    response = post_with_retry(url, headers=headers, json=body, timeout=60)
    if not response.ok:
        logging.error(f"Groq API error ({response.status_code}): {response.text}")
        response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


# ---------- Multi-Market Data Engine (US, Brazil B3, Crypto, Global) ----------

# The tradable crypto universe is discovered live (Coinbase + Binance) and cached,
# NOT a hardcoded allowlist — so the model can pick any listed coin.
CRYPTO_SYMBOLS = {
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK",
    "DOT", "MATIC", "POL", "NEAR", "SUI", "APT", "UNI", "LTC", "BCH",
    "SHIB", "PEPE", "RENDER", "FET", "TAO", "AAVE", "MKR", "ARB", "OP",
}
_crypto_universe_cache = {"t": 0.0, "symbols": None}
_CRYPTO_UNIVERSE_TTL = 6 * 3600.0  # refresh universe every 6h


def _coinbase_universe() -> set:
    try:
        r = requests.get("https://api.coinbase.com/v2/currencies", timeout=8)
        if r.ok:
            return {c.get("id", "").upper() for c in r.json().get("data", []) if c.get("id")}
    except Exception:
        pass
    return set()


def _binance_universe() -> set:
    try:
        r = requests.get("https://api.binance.com/api/v3/exchangeInfo", timeout=8)
        if r.ok:
            return {
                s.get("baseAsset", "").upper()
                for s in r.json().get("symbols", [])
                if s.get("quoteAsset") == "USDT" and s.get("baseAsset")
            }
    except Exception:
        pass
    return set()


def fetch_crypto_universe() -> set:
    """Return the set of tradeable crypto base symbols (Coinbase + Binance), cached."""
    now = time.time()
    if _crypto_universe_cache["symbols"] is not None and (now - _crypto_universe_cache["t"]) < _CRYPTO_UNIVERSE_TTL:
        return _crypto_universe_cache["symbols"]
    combined = _coinbase_universe() | _binance_universe()
    if combined:
        combined |= CRYPTO_SYMBOLS
        _crypto_universe_cache["symbols"] = combined
        _crypto_universe_cache["t"] = now
        return combined
    return CRYPTO_SYMBOLS


def get_crypto_price(ticker: str) -> tuple[float | None, float | None, str]:
    """Fetch live crypto price in USD from Coinbase or Binance."""
    raw = ticker.upper().replace(" ", "").replace("-", "").replace("/", "")
    if raw.endswith("USD"):
        base = raw[:-3]
    elif raw.endswith("USDT"):
        base = raw[:-4]
    else:
        base = raw

    # Soft guard: reject a *bare* symbol not in the live universe, but always try
    # an explicit pair form (X/USD, X-USD, XUSD).
    if base not in fetch_crypto_universe() and "/" not in ticker and "-USD" not in ticker and "USD" not in ticker:
        return None, None, ticker

    # 1. Coinbase
    try:
        url = f"https://api.coinbase.com/v2/prices/{base}-USD/spot"
        r = requests.get(url, timeout=6)
        if r.ok:
            data = r.json()
            price = float(data.get("data", {}).get("amount") or 0.0)
            if price > 0:
                return price, 0.0, f"{base}/USD"
    except Exception:
        pass

    # 2. Binance
    try:
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={base}USDT"
        r = requests.get(url, timeout=6)
        if r.ok:
            data = r.json()
            price = float(data.get("price") or 0.0)
            if price > 0:
                return price, 0.0, f"{base}/USD"
    except Exception:
        pass

    return None, None, ticker


def get_yahoo_price(ticker: str) -> tuple[float | None, float | None]:
    """Fetch live price and percent change from Yahoo Finance."""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }
        r = requests.get(url, headers=headers, timeout=8)
        if r.ok:
            data = r.json()
            meta = data.get("chart", {}).get("result", [{}])[0].get("meta", {})
            price = float(meta.get("regularMarketPrice") or 0.0)
            prev_close = float(meta.get("previousClose") or meta.get("chartPreviousClose") or price)
            if price > 0:
                dp = ((price - prev_close) / prev_close) * 100 if prev_close else 0.0
                return price, dp
    except Exception:
        pass
    return None, None


def fetch_live_quote(ticker: str) -> tuple[float | None, float | None]:
    """Fetch live quote from Finnhub for US symbols."""
    if not FINNHUB_API_KEY or not ticker:
        return None, None
    try:
        url = "https://finnhub.io/api/v1/quote"
        res = requests.get(url, params={"symbol": ticker, "token": FINNHUB_API_KEY}, timeout=8)
        if res.status_code == 200:
            data = res.json()
            price = float(data.get("c") or 0)
            if price > 0:
                dp = float(data.get("dp") or 0)
                return price, dp
    except Exception as e:
        logging.warning(f"Failed to fetch Finnhub quote for {ticker}: {e}")
    return None, None


def resolve_and_price_ticker(raw_ticker: str) -> tuple[str, float | None, str]:
    """Normalize symbol and query live multi-market feeds (Crypto, B3, US, Global)."""
    clean = str(raw_ticker or "").strip().upper()
    clean = re.sub(r"^\$", "", clean)

    # 1. Crypto
    c_price, c_dp, c_symbol = get_crypto_price(clean)
    if c_price is not None and c_price > 0:
        return c_symbol, round(c_price, 4), f"${c_price:,.2f} (24h spot)"

    # 2. Brazilian stock (e.g. PETR4 -> PETR4.SA)
    if re.fullmatch(r"[A-Z]{4}[0-9]{1,2}", clean) and not clean.endswith(".SA"):
        b3_price, b3_dp = get_yahoo_price(f"{clean}.SA")
        if b3_price is not None and b3_price > 0:
            dp_val = b3_dp if b3_dp is not None else 0.0
            return f"{clean}.SA", round(b3_price, 4), f"R${b3_price:.2f} ({dp_val:+.2f}% vs prev close)"

    # 3. International suffixes (.SA, .L, .DE, =X)
    if "." in clean or "=" in clean:
        y_price, y_dp = get_yahoo_price(clean)
        if y_price is not None and y_price > 0:
            dp_val = y_dp if y_dp is not None else 0.0
            return clean, round(y_price, 4), f"${y_price:.2f} ({dp_val:+.2f}% vs prev close)"

    # 4. Standard US stock via Finnhub
    price, dp = fetch_live_quote(clean)
    if price is not None and price > 0:
        price_at_analysis = round(price, 4)
        dp_val = dp if dp is not None else 0.0
        return clean, price_at_analysis, f"${price_at_analysis:.2f} ({dp_val:+.2f}% vs prev close)"

    # 5. Fallback to Yahoo Finance
    y_price, y_dp = get_yahoo_price(clean)
    if y_price is not None and y_price > 0:
        dp_val = y_dp if y_dp is not None else 0.0
        return clean, round(y_price, 4), f"${y_price:.2f} ({dp_val:+.2f}% vs prev close)"

    return clean, None, "Market quote unavailable at report time"


def parse_json_response(text: str, default):
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text.removeprefix("json").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"[\{\[].*[\}\]]", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        logging.error(f"Could not parse JSON from model response: {text[:200]}")
        return default
