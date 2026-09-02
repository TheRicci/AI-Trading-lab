import azure.functions as func
import logging
import os
import json
import re
import uuid
import datetime
import time
import requests
from azure.cosmos import CosmosClient
from azure.messaging.webpubsubservice import WebPubSubServiceClient
import tempfile

# ── Tavily Budget ────────────────────────────────────────────────────────────
MONTHLY_LIMIT = int(os.environ.get("TAVILY_MONTHLY_LIMIT", "1000"))
WARN_AT = int(os.environ.get("TAVILY_WARN_AT", "850"))

class TavilyBudget:
    def __init__(self, path: str | None = None):
        self.path = path or os.path.join(
            tempfile.gettempdir(), "tavily_budget.json")
        self.month = datetime.date.today().strftime("%Y-%m")
        self.used = self._load()

    def _key(self):
        return f"{self.month}"

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("month") != self.month:
                return 0
            return int(data.get("used", 0))
        except Exception:
            return 0

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump({"month": self.month, "used": self.used}, fh)
        except Exception:
            pass

    def allowance(self, n: int = 1) -> bool:
        """Whether we may run n more searches."""
        return (self.used + n) <= MONTHLY_LIMIT

    def spend(self, n: int = 1):
        """Record n searches and log a warning near/at the cap."""
        self.used += n
        self._save()
        if self.used >= MONTHLY_LIMIT:
            logging.warning(
                f"[TavilyBudget] Monthly limit ({MONTHLY_LIMIT}) reached ({self.used}). "
                f"Further searches will be blocked until next month.")
        elif self.used >= WARN_AT:
            logging.warning(
                f"[TavilyBudget] Approaching monthly limit: {self.used}/{MONTHLY_LIMIT} used.")
        return self.used

    def status(self) -> str:
        return f"{self.used}/{MONTHLY_LIMIT} Tavily credits used this month ({self.month})"

BUDGET = TavilyBudget()

app = func.FunctionApp()

BOT_NAME = os.environ.get("BOT_NAME", "unnamed-bot")
BOT_STRATEGY_PROMPT = os.environ.get("BOT_STRATEGY_PROMPT", "You are a trading bot.")
BOT_STARTING_CASH = float(os.environ.get("BOT_STARTING_CASH", "100000"))
BOT_LLM_PROVIDER = os.environ.get("BOT_LLM_PROVIDER", "gemini")  # gemini | groq | groq_tavily
BOT_LLM_MODEL = os.environ.get("BOT_LLM_MODEL", "gemini-3.6-flash")

FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")

COSMOS_ENDPOINT = os.environ.get("COSMOS_ENDPOINT")
COSMOS_KEY = os.environ.get("COSMOS_KEY")
WEBPUBSUB_CONNECTION_STRING = os.environ.get("WEBPUBSUB_CONNECTION_STRING")

cosmos_client = CosmosClient(COSMOS_ENDPOINT, COSMOS_KEY)
database = cosmos_client.get_database_client("TradingBots")
trades = database.get_container_client("Trades")
# Single table of source data: full portfolio snapshots. The newest row for
# a bot IS its current state; earlier rows are its history.
history = database.get_container_client("PortfolioHistory")

webpubsub_client = WebPubSubServiceClient.from_connection_string(
    WEBPUBSUB_CONNECTION_STRING, hub="tradingbots"
)


@app.event_grid_trigger(arg_name="event")
def on_event(event: func.EventGridEvent) -> None:
    # Only session/open and session/close reach here. Each event produces one
    # portfolio-level decision, not one decision for a preconfigured ticker.
    data = event.get_json()
    if event.event_type == "Trading.SessionReview":
        decide_and_trade(data["session"])


# ---------- HTTP Request Helper with Rate-Limit & Backoff Retry ----------

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
                        wait = min(float(m.group(1)) + 0.5, 30.0)
                except Exception:
                    pass
                logging.warning(
                    f"[{BOT_NAME}] {r.status_code}. Retrying in {wait:.1f}s (attempt {attempt + 1}/{max_retries})..."
                )
                time.sleep(wait)
                continue
            return r
        except requests.RequestException as e:
            if attempt == max_retries - 1:
                raise
            logging.warning(f"[{BOT_NAME}] Request error: {e}. Retrying in 2s...")
            time.sleep(2.0)
    return r


# ---------- LLM provider dispatch ----------
# All search is backed by Tavily (free tier). "web_search" is the function the
# model calls; our code runs it through search_tavily(). Groq models use tool
# calling; Gemini uses native function calling against the same Tavily hook.

def call_gemini(prompt: str) -> str:
    """Agentic Tavily search loop for Gemini.

    The model decides what it wants to search; we run each query through Tavily
    and feed results back, iterating until it produces its final JSON. A turn
    budget and a prompt hint encourage it to minimize searches so we stay inside
    the free Tavily monthly quota."""
    key = (GEMINI_API_KEY or "").strip().strip('"').strip("'")
    model = (BOT_LLM_MODEL or "gemini-3.6-flash").strip().strip('"').strip("'")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"

    tools = [
        {
            "functionDeclarations": [
                {
                    "name": "web_search",
                    "description": "Search the live web for recent stock market news, asset movements, macro updates, earnings, and financial data. Use it only when you genuinely need fresh information.",
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
                    "text": prompt + "\n\nUse web_search calls to gather live context only when needed. " + "Minimize the number of searches: do as few as you need, then produce your final JSON decision."
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
            logging.error(f"[{BOT_NAME}] Gemini API error ({response.status_code}) for model '{model}': {response.text}")
            response.raise_for_status()

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

    # Final fallback: request a plain answer.
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


def call_openai_compatible(base_url: str, api_key: str, prompt: str) -> str:
    url = f"{base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": BOT_LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "response_format": {"type": "json_object"},  # forces syntactically valid JSON
    }
    response = post_with_retry(url, headers=headers, json=body, timeout=60)
    if not response.ok:
        logging.error(f"[{BOT_NAME}] Groq API error ({response.status_code}): {response.text}")
        response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def search_tavily(query: str) -> str:
    if not TAVILY_API_KEY:
        return "Error: TAVILY_API_KEY not configured."
    if not BUDGET.allowance(1):
        logging.warning(f"[{BOT_NAME}] Tavily monthly limit reached; skipping search: {BUDGET.status()}")
        return "Search skipped: monthly Tavily budget exhausted."
    BUDGET.spend(1)
    url = "https://api.tavily.com/search"
    body = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "search_depth": "basic",
        "include_answer": True,
        "max_results": 3,
    }
    try:
        res = post_with_retry(url, json=body, timeout=15)
        if not res.ok:
            logging.error(f"[{BOT_NAME}] Tavily HTTP error ({res.status_code}): {res.text}")
            return f"Tavily search HTTP {res.status_code}"
        data = res.json()
        answer = data.get("answer")
        results = data.get("results", [])
        snippets = []
        if answer:
            snippets.append(f"Summary: {answer[:250]}")
        for r in results[:3]:
            title = r.get("title", "")
            content = (r.get("content") or "")[:200]
            snippets.append(f"- {title}: {content}")
        return "\n".join(snippets) if snippets else "No results found."
    except Exception as e:
        logging.error(f"[{BOT_NAME}] Tavily search failed: {e}")
        return f"Search error: {e}"


def call_groq_with_tavily(prompt: str, max_turns: int = 5) -> str:
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    tools = [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the live web for recent stock market news, asset movements, macro updates, earnings, and financial data.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query for financial information",
                        }
                    },
                    "required": ["query"],
                },
            },
        }
    ]
    messages = [
        {
            "role": "system",
            "content": "You are a professional trading analyst. You may perform 1 to 2 targeted web searches if needed, then immediately formulate your final JSON decision."
        },
        {"role": "user", "content": prompt}
    ]

    for turn in range(max_turns):
        body = {
            "model": BOT_LLM_MODEL,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.7,
        }
        response = post_with_retry(url, headers=headers, json=body, timeout=60)
        if not response.ok:
            try:
                err_data = response.json()
                failed_gen = err_data.get("error", {}).get("failed_generation", "")
                if "{" in failed_gen and "position" in failed_gen:
                    return failed_gen
            except Exception:
                pass
            logging.error(f"[{BOT_NAME}] Groq Tavily tool-call error ({response.status_code}): {response.text}")
            response.raise_for_status()

        choice = response.json()["choices"][0]
        msg = choice["message"]
        tool_calls = msg.get("tool_calls")
        content = msg.get("content") or ""

        if not tool_calls:
            if content.strip():
                return content
            messages.append({"role": "user", "content": "Please output the final JSON decision now."})
            continue

        messages.append(msg)
        for tool_call in tool_calls:
            func_name = tool_call["function"]["name"]
            func_args_str = tool_call["function"].get("arguments", "{}")
            try:
                func_args = json.loads(func_args_str)
            except Exception:
                func_args = {}

            if func_name == "web_search":
                query = func_args.get("query", "")
                result_str = search_tavily(query)
            else:
                result_str = f"Unknown tool: {func_name}"

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "content": result_str,
            })

        if turn == max_turns - 2:
            messages.append({
                "role": "user",
                "content": "You have completed your research. Now produce your final decision JSON object immediately."
            })

    # Final attempt with tools enabled
    body = {"model": BOT_LLM_MODEL, "messages": messages, "tools": tools, "tool_choice": "auto", "temperature": 0.7}
    response = post_with_retry(url, headers=headers, json=body, timeout=60)
    if response.ok:
        choice = response.json()["choices"][0]
        content = choice["message"].get("content") or ""
        if content.strip():
            return content
    raise RuntimeError(f"Groq Tavily could not obtain final decision from {BOT_LLM_MODEL}")


def call_llm(prompt: str) -> str:
    if BOT_LLM_PROVIDER == "gemini":
        return call_gemini(prompt)
    elif BOT_LLM_PROVIDER == "groq":
        return call_openai_compatible("https://api.groq.com/openai/v1", GROQ_API_KEY, prompt)
    elif BOT_LLM_PROVIDER == "groq_tavily":
        return call_groq_with_tavily(prompt)
    else:
        raise ValueError(f"Unknown BOT_LLM_PROVIDER: {BOT_LLM_PROVIDER}")


# ---------- Multi-Market Data Engine (US, Brazil B3, Crypto, Global) ----------

# The tradable crypto universe is discovered live (Coinbase + Binance) and cached,
# NOT a hardcoded allowlist — so the bots can pick any listed coin, not just a
# small curated set. This static set is only a fast-path fallback if discovery
# is ever unavailable.
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
        combined |= CRYPTO_SYMBOLS  # ensure the curated fast-path is always present
        _crypto_universe_cache["symbols"] = combined
        _crypto_universe_cache["t"] = now
        return combined
    return CRYPTO_SYMBOLS  # fallback if live discovery fails


def get_crypto_price(ticker: str) -> dict | None:
    """Fetch live crypto price in USD from Coinbase or Binance public APIs (24/7, no key needed)."""
    raw = ticker.upper().replace(" ", "").replace("-", "").replace("/", "")
    if raw.endswith("USD"):
        base = raw[:-3]
    elif raw.endswith("USDT"):
        base = raw[:-4]
    else:
        base = raw

    # Soft guard: only reject a *bare* symbol that isn't in the live universe.
    # If the model supplied an explicit pair (X/USD, X-USD, XUSD) we still try it.
    if base not in fetch_crypto_universe() and "/" not in ticker and "-USD" not in ticker and "USD" not in ticker:
        return None

    # 1. Coinbase spot price
    try:
        url = f"https://api.coinbase.com/v2/prices/{base}-USD/spot"
        r = requests.get(url, timeout=6)
        if r.ok:
            data = r.json()
            price = float(data.get("data", {}).get("amount") or 0.0)
            if price > 0:
                return {"c": price, "symbol": f"{base}/USD", "source": "coinbase"}
    except Exception:
        pass

    # 2. Binance spot price
    try:
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={base}USDT"
        r = requests.get(url, timeout=6)
        if r.ok:
            data = r.json()
            price = float(data.get("price") or 0.0)
            if price > 0:
                return {"c": price, "symbol": f"{base}/USD", "source": "binance"}
    except Exception:
        pass

    return None


def get_yahoo_price(ticker: str) -> dict | None:
    """Fetch live quote from Yahoo Finance chart endpoint (supports US, Brazil .SA, forex, and global)."""
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
                return {
                    "c": price,
                    "pc": prev_close,
                    "d": price - prev_close,
                    "dp": ((price - prev_close) / prev_close) * 100 if prev_close else 0.0,
                    "symbol": ticker,
                    "source": "yahoo",
                }
    except Exception:
        pass
    return None


def get_quote(ticker: str) -> dict:
    """
    Unified multi-market quote provider:
      1. Cryptocurrencies (actual X/USD pairs via Coinbase / Binance)
      2. Brazilian Market B3 (e.g. PETR4.SA, VALE3.SA via Yahoo Finance)
      3. US Equities & ETFs (via Finnhub with Yahoo Finance fallback)
      4. Global Equities & Forex (via Yahoo Finance)
    """
    clean = str(ticker or "").strip().upper()
    if not clean:
        raise ValueError("Empty ticker symbol provided")

    # 1. Crypto check
    c_quote = get_crypto_price(clean)
    if c_quote and c_quote.get("c", 0) > 0:
        return c_quote

    # 2. Brazilian stock without suffix (e.g. PETR4, VALE3, ITUB4 -> PETR4.SA)
    if re.fullmatch(r"[A-Z]{4}[0-9]{1,2}", clean) and not clean.endswith(".SA"):
        b3_quote = get_yahoo_price(f"{clean}.SA")
        if b3_quote and b3_quote.get("c", 0) > 0:
            return b3_quote

    # 3. International suffixes (e.g. .SA, .L, .DE, =X for forex)
    if "." in clean or "=" in clean:
        y_quote = get_yahoo_price(clean)
        if y_quote and y_quote.get("c", 0) > 0:
            return y_quote

    # 4. Finnhub for standard US tickers
    if FINNHUB_API_KEY:
        try:
            url = "https://finnhub.io/api/v1/quote"
            r = requests.get(url, params={"symbol": clean, "token": FINNHUB_API_KEY}, timeout=8)
            if r.ok:
                data = r.json()
                price = float(data.get("c") or 0.0)
                if price > 0:
                    return data
        except Exception:
            pass

    # 5. Fallback to Yahoo Finance for any other symbol
    y_quote = get_yahoo_price(clean)
    if y_quote and y_quote.get("c", 0) > 0:
        return y_quote

    raise ValueError(f"No usable live market price found for {clean}")


# ---------- Portfolio + market data ----------

def _normalise_positions(portfolio: dict) -> None:
    """Ensure every stored position has a `shares` field.

    Older snapshots written before the `shares` field was added to the
    positions dict may be missing it. Without it, settlement logic treats
    the position as having 0 shares and skips the cash-return step, making
    margin silently evaporate. We reconstruct shares from notional / entryPrice
    so that every position is treated consistently regardless of its age.
    """
    for ticker, pos in portfolio.get("positions", {}).items():
        if pos.get("shares") is not None:
            continue  # already present
        notional_val = pos.get("notional", 0)
        ep           = pos.get("entryPrice", 0)
        is_frac      = "/" in ticker or "-USD" in ticker
        if ep > 0 and notional_val > 0:
            raw = notional_val / ep
            s   = round(raw, 6) if is_frac else int(raw)
            s  *= 1 if pos.get("direction") == "long" else -1
        else:
            s = 0
        pos["shares"] = s


def get_portfolio() -> dict:
    """Load the latest portfolio snapshot for this bot, or a fresh one."""
    try:
        query = (
            "SELECT * FROM c WHERE c.botName = @name "
            "ORDER BY c.timestamp DESC OFFSET 0 LIMIT 1"
        )
        params = [{"name": "@name", "value": BOT_NAME}]
        items = list(history.query_items(query=query, parameters=params, partition_key=BOT_NAME))
        if items:
            portfolio = items[0]
            _normalise_positions(portfolio)  # backfill missing `shares` on legacy docs
            return portfolio
    except Exception as e:
        logging.warning(f"[{BOT_NAME}] Could not read portfolio history, starting fresh: {e}")

    return {
        "id": BOT_NAME,
        "botName": BOT_NAME,
        "cash": BOT_STARTING_CASH,
        "positions": {},
        "startingCash": BOT_STARTING_CASH,
        "strategyPrompt": BOT_STRATEGY_PROMPT,
        "llmProvider": BOT_LLM_PROVIDER,
        "llmModel": BOT_LLM_MODEL,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def save_portfolio_snapshot(portfolio: dict, total_value: float, session_id: str | None = None):
    """Persist the full portfolio as the newest snapshot in the single history table."""
    snapshot = {
        "id": str(uuid.uuid4()),
        "botName": BOT_NAME,
        "cash": round(float(portfolio.get("cash", 0)), 2),
        "positions": portfolio.get("positions", {}),
        "totalValue": round(total_value, 2),
        "startingCash": round(float(portfolio.get("startingCash", BOT_STARTING_CASH)), 2),
        "strategyPrompt": portfolio.get("strategyPrompt", BOT_STRATEGY_PROMPT),
        "llmProvider": portfolio.get("llmProvider", BOT_LLM_PROVIDER),
        "llmModel": portfolio.get("llmModel", BOT_LLM_MODEL),
        "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if session_id:
        snapshot["sessionId"] = session_id
    history.create_item(snapshot)


def build_portfolio_snapshot(portfolio: dict) -> tuple[dict, dict]:
    """Mark every open position to live prices for a complete margin-model valuation.

    NAV = freeCash + sum(marginUsed + unrealizedPnL) for each position.
    Cash is always >= 0 in the new model (it represents undeployed equity).
    """
    positions = []
    quotes = {}
    free_cash  = max(0.0, float(portfolio.get("cash", 0)))
    total_value = free_cash
    total_margin_used = 0.0

    for ticker, pos_meta in portfolio.get("positions", {}).items():
        shares = pos_meta.get("shares")
        if shares is None:
            # Legacy position without shares — infer from notional / entryPrice.
            # _normalise_positions() should have run before this, but guard anyway.
            notional_val = pos_meta.get("notional", 0)
            ep = pos_meta.get("entryPrice", 0)
            is_frac = "/" in ticker or "-USD" in ticker
            if ep > 0 and notional_val > 0:
                raw = notional_val / ep
                shares = round(raw, 6) if is_frac else int(raw)
                shares *= 1 if pos_meta.get("direction") == "long" else -1
            else:
                shares = 0
        if not shares:
            continue
        quote = get_quote(ticker)
        price = float(quote.get("c") or 0)
        if price <= 0:
            raise ValueError(f"No usable live price returned for held symbol {ticker}")
        quotes[ticker] = quote

        entry_price = pos_meta.get("entryPrice", price)
        margin_used = pos_meta.get("marginUsed", 0)
        notional    = pos_meta.get("notional", abs(shares) * entry_price)
        direction   = 1 if pos_meta.get("direction") == "long" else -1
        unreal_pnl  = direction * notional * (price / entry_price - 1) if entry_price > 0 else 0

        # Position's equity contribution to NAV
        position_equity = margin_used + unreal_pnl
        total_value     += position_equity
        total_margin_used += margin_used

        positions.append({
            "ticker":        ticker,
            "side":          "long" if shares > 0 else "short",
            "shares":        shares,
            "markPrice":     round(price, 4),
            "entryPrice":    round(entry_price, 4),
            "marginUsed":    round(margin_used, 2),
            "unrealizedPnl": round(unreal_pnl, 2),
            "positionEquity": round(position_equity, 2),
        })

    snapshot = {
        "freeCash":                 round(free_cash, 2),
        "marginUsed":               round(total_margin_used, 2),
        "positions":                positions,
        "totalNetLiquidationValue": round(total_value, 2),
        "valuationTimestamp":       datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return snapshot, quotes


def compute_nav(portfolio: dict, quote_cache: dict | None = None):
    """Compute NAV = free cash + sum(marginUsed + unrealizedPnL) per position.
    Uses cached prices (dict of ticker to price) when available, otherwise
    fetches a live quote. Single source of truth for valuation so liquidation,
    per-trade and final NAVs all agree.
    """
    quote_cache = quote_cache if quote_cache is not None else {}
    total = float(portfolio.get("cash", 0))
    for ticker, pos in portfolio.get("positions", {}).items():
        price = quote_cache.get(ticker)
        if price is None:
            try:
                price = float(get_quote(ticker).get("c") or 0)
                quote_cache[ticker] = price
            except Exception:
                price = 0
        entry = pos.get("entryPrice", 0)
        margin = pos.get("marginUsed", 0)
        notional = pos.get("notional", 0)
        if (not price <= 0) and (not entry <= 0):
            pdir = 1 if pos.get("direction") == "long" else -1
            unreal = pdir * notional * (price / entry - 1)
            total = total + margin + unreal
        else:
            total = total + margin  # fallback: assume breakeven
    return total


# ---------- Decision + execution ----------

def ask_llm_for_decision(portfolio_snapshot: dict, session: str) -> list:
    prompt = f"""{BOT_STRATEGY_PROMPT}

You are not locked to a single strategy family. Choose the style that best fits today, such as trend-following, mean reversion, catalyst/event-driven, valuation, volatility breakout, sentiment, or staying out entirely.
Use live web search to independently find the assets, news, filings, earnings, macro events, price history, technical levels, and metrics you need.

SUPPORTED MARKETS & TICKER FORMATS:
1. US Equities & ETFs: Use standard ticker (e.g. NVDA, AAPL, SPY, QQQ, TSLA, GLD, USO, TLT)
2. Brazilian Equities (B3): Use .SA suffix or B3 ticker (e.g. PETR4.SA, VALE3.SA, ITUB4.SA, BOVA11.SA)
3. Cryptocurrencies (quoted in USD): Use COIN/USD format (e.g. BTC/USD, ETH/USD, SOL/USD)
4. Currencies / Forex: Use YAHOO format (e.g. GBPUSD=X, EURUSD=X, BRL=X)

This is your complete portfolio, freshly marked to live prices immediately before this review.
`freeCash` is undeployed equity you can use to open new positions. `marginUsed` is capital locked as collateral in open positions. Each position shows its unrealized P&L and equity (margin + P&L). Negative shares = short.
{json.dumps(portfolio_snapshot, indent=2)}

Trigger: market {session} (a scheduled twice-daily review)

Choose deliberate portfolio adjustments. You can output a list of up to 5 decisions to execute right now.
For each decision, choose:
- `ticker`: The symbol (e.g. NVDA, AAPL, BTC/USD)
- `type`: The asset class — one of: `"stock"`, `"etf"`, `"crypto"`, `"forex"`, `"brazil"`. This tells the backend which price feed to use. Use `"crypto"` ONLY for actual cryptocurrencies. Use `"stock"` for all equities including foreign ones like NIO.
- `position`: `"long"` or `"short"`. This represents the direction of the trade you want to enter (or the direction you were in when closing).
- `cash_amount`: The absolute dollar amount of margin you want allocated to this position (e.g. 15000). To close an existing position entirely, use `0`. To resize, output the new total amount.
- `leverage`: 1.0 (spot/no leverage) up to 10.0 (margin). Ignored when `cash_amount` is 0.
- `reasoning`: A short sentence explaining why.

CRITICAL INSTRUCTIONS:
- **Holding is implicit.** If you want to keep an existing position exactly as it is, DO NOT include it in your output at all.
- **List closes first.** If you are closing or reducing an existing position AND opening a new one in the same batch, always output the close (cash_amount: 0) before the open. This ensures freed-up margin is available for the new trade.
- You can allocate up to your current `freeCash`. When you close a position, its `marginUsed` is returned to your free cash pool and becomes available for subsequent trades in the same batch.

Respond with ONLY a JSON array containing your decision objects. 
DO NOT output any conversational text, preambles, or markdown formatting. 
Start your response exactly with [ and end exactly with ]. Example:
[
  {{"ticker": "SPY", "type": "etf", "position": "long", "cash_amount": 0, "leverage": 1.0, "reasoning": "Exiting — rally looks exhausted."}},
  {{"ticker": "NVDA", "type": "stock", "position": "long", "cash_amount": 25000, "leverage": 1.0, "reasoning": "Strong earnings catalyst."}},
  {{"ticker": "BTC/USD", "type": "crypto", "position": "long", "cash_amount": 10000, "leverage": 1.5, "reasoning": "Breakout above key resistance."}}
]
"""
    text = call_llm(prompt).strip()
    
    # 1. Try to extract markdown JSON blocks if the LLM used them
    md_match = re.search(r"```(?:json)?\s*(\[.*?\]|\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if md_match:
        text_to_parse = md_match.group(1)
    else:
        # 2. Fallback: find the first '[' and last ']' in the entire string
        start_idx = text.find('[')
        end_idx = text.rfind(']')
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            text_to_parse = text[start_idx:end_idx+1]
        else:
            text_to_parse = text
            
    try:
        data = json.loads(text_to_parse)
        return data if isinstance(data, list) else [data]
    except json.JSONDecodeError:
        raise ValueError(f"Could not parse JSON decision from LLM output: {text}")

def normalise_ticker(value, asset_type: str | None = None) -> str:
    """Normalise a ticker symbol to the canonical form expected by get_quote().

    asset_type is the 'type' field from the LLM decision object:
      "stock" | "etf" | "crypto" | "forex" | "brazil"

    When asset_type is present it is the primary routing signal and completely
    avoids the ticker-collision problem (e.g. NIO the stock vs NIO the crypto).
    Heuristics are only used as a fallback for legacy stored positions that
    pre-date the type field.
    """
    raw = str(value or "").strip().upper()
    if not raw:
        raise ValueError("Model did not provide a valid market-data ticker")

    t = str(asset_type or "").strip().lower()

    # ── Type-directed routing (LLM declared the asset class) ─────────────────
    if t == "crypto":
        # Normalise any variant (BTC, BTCUSD, BTC-USD, BTC/USD) -> BTC/USD
        clean = raw.replace(" ", "").replace("-", "").replace("/", "")
        if clean.endswith("USDT"):
            clean = clean[:-4]
        elif clean.endswith("USD"):
            clean = clean[:-3]
        return f"{clean}/USD"

    if t == "brazil":
        # Ensure .SA suffix for B3 stocks
        return raw if raw.endswith(".SA") else f"{raw}.SA"

    if t == "forex":
        # Ensure Yahoo Finance =X suffix for forex
        return raw if raw.endswith("=X") else f"{raw}=X"

    if t in ("stock", "etf"):
        # Plain ticker — strip any accidental /USD or -USD suffix the LLM may have added
        clean = raw.replace("/USD", "").replace("-USD", "").replace("USDT", "")
        return clean

    # ── Heuristic fallback (no type provided — old positions, etc.) ───────────
    # Crypto: only auto-promote to X/USD if the ticker explicitly carries a USD
    # suffix or if it's a bare symbol that exists in the live universe.
    clean_crypto = raw.replace(" ", "").replace("-", "").replace("/", "")
    universe = fetch_crypto_universe()

    base: str | None = None
    if clean_crypto.endswith("USDT"):
        base = clean_crypto[:-4]
    elif clean_crypto.endswith("USD"):
        base = clean_crypto[:-3]

    if base is not None and base in universe:
        return f"{base}/USD"

    # Bare symbol in crypto universe AND it explicitly looks like crypto
    # (i.e. had a / or -USD in the original string — not a bare "NIO"-type ticker)
    if raw in universe and ("/" in raw or "-USD" in raw.upper()):
        return f"{raw}/USD"

    # Brazilian stock pattern (PETR4, VALE3, …)
    if re.fullmatch(r"[A-Z]{4}[0-9]{1,2}", raw):
        return f"{raw}.SA"

    # Forex pair (GBPUSD, EURUSD, …)
    if re.fullmatch(r"(EUR|GBP|USD|JPY|AUD|CAD|CHF|NZD|BRL){2}", raw) and not raw.endswith("=X"):
        return f"{raw}=X"

    return raw


def _liquidate_margin_calls(portfolio: dict) -> list:
    """Check all open positions for margin calls and force-close any that are blown.
    Returns a list of liquidation trade records to broadcast."""
    liquidations = []
    for ticker in list(portfolio.get("positions", {}).keys()):
        pos = portfolio["positions"][ticker]
        try:
            q = get_quote(ticker)
            live_px = float(q.get("c") or 0)
        except Exception:
            continue
        if live_px <= 0:
            continue
        pos_dir = 1 if pos.get("direction") == "long" else -1
        entry   = pos.get("entryPrice", live_px)
        margin  = pos.get("marginUsed", 0)
        notional = pos.get("notional", 0)
        unreal  = pos_dir * notional * (live_px / entry - 1) if entry > 0 else 0
        if unreal <= -margin:
            logging.warning(
                f"[{BOT_NAME}] MARGIN CALL: {ticker} | Loss ${-unreal:,.2f} wiped margin ${margin:,.2f}"
            )
            # Position wiped — cash gets $0 back (entire margin lost)
            del portfolio["positions"][ticker]
            liquidations.append({
                "id": str(uuid.uuid4()),
                "botName": BOT_NAME,
                "llmProvider": BOT_LLM_PROVIDER,
                "llmModel": BOT_LLM_MODEL,
                "ticker": ticker,
                "action": "sell" if pos_dir == 1 else "buy",
                "position": "flat",
                "closedDirection": "long" if pos_dir == 1 else "short",
                "closedLeverage":  pos.get("leverage", 1.0),
                "leverage": pos.get("leverage", 1.0),
                "shares": abs(pos.get("shares", 0)),
                "price": live_px,
                "reasoning": "MARGIN CALL — position liquidated automatically.",
                "trigger": "margin-call",
                "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                # totalValue filled in by caller after the margin call executes
                "cash": round(float(portfolio["cash"]), 2),
            })
    return liquidations


def decide_and_trade(session: str) -> None:
    portfolio = get_portfolio()
    # Idempotency key: each bot reviews a session at most once per day.
    session_id = f"{session}-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d')}"
    try:
        _query = 'SELECT VALUE COUNT(1) FROM c WHERE c.botName = @b AND c.sessionId = @sid'
        check = list(history.query_items(
            query=_query,
            parameters=[{"name": "@b", "value": BOT_NAME}, {"name": "@sid", "value": session_id}],
            partition_key=BOT_NAME,
        ))
        if check and check[0] and check[0] > 0:
            logging.info(f"[{BOT_NAME}] Session {session_id} already handled; skipping.")
            return
    except Exception as e:
        logging.warning(f"[{BOT_NAME}] Idempotency check failed: {e}")


    # Migrate portfolios that were created under the old negative-cash model.
    # In the new model cash is always >= 0 (only margin is deducted, not full notional).
    if float(portfolio.get("cash", 0)) < 0:
        total_margin_posted = sum(
            pos.get("marginUsed", 0) for pos in portfolio.get("positions", {}).values()
        )
        current_total = float(portfolio.get("totalValue", portfolio.get("startingCash", BOT_STARTING_CASH)))
        portfolio["cash"] = max(0.0, current_total - total_margin_posted)
        logging.info(f"[{BOT_NAME}] Migrated portfolio cash to margin model: ${portfolio['cash']:,.2f} free")

    # Check for margin calls before asking the LLM
    liquidations = _liquidate_margin_calls(portfolio)
    # Set a real post-liquidation NAV on each liquidation record.
    if liquidations:
        try:
            liq_snapshot, _ = build_portfolio_snapshot(portfolio)
            liq_nav = liq_snapshot["totalNetLiquidationValue"]
        except Exception:
            liq_nav = float(portfolio.get("totalValue", portfolio.get("cash", 0)))
        for liq in liquidations:
            liq["totalValue"] = round(liq_nav, 2)

    try:
        portfolio_snapshot, _ = build_portfolio_snapshot(portfolio)
    except Exception as e:
        logging.error(f"[{BOT_NAME}] Could not value every open position; skipping review: {e}")
        return

    try:
        decisions = ask_llm_for_decision(portfolio_snapshot, session)
        logging.info(f"[{decisions}]")                                                     ##DEBUG
    except Exception as e:
        logging.error(f"[{BOT_NAME}] LLM call failed: {e}")
        return

    if not isinstance(decisions, list):
        logging.error(f"[{BOT_NAME}] Model response was not a JSON list")
        return

    # Re-snapshot right before execution (LLM call takes time)
    try:
        execution_snapshot, exec_quotes = build_portfolio_snapshot(portfolio)
    except Exception as e:
        logging.error(f"[{BOT_NAME}] Could not revalue portfolio before execution: {e}")
        return
    portfolio_value = execution_snapshot["totalNetLiquidationValue"]
    # Reusable price cache so per-decision NAVs don't re-fetch live quotes.
    nav_cache = {t: float(q.get("c") or 0) for t, q in exec_quotes.items() if q.get("c")}

    trade_records_to_save = list(liquidations)  # persist margin calls to the Trades history too
    trade_records_to_broadcast = list(liquidations)  # and broadcast them live

    # ── Sort: closes (cash_amount == 0) execute before opens ─────────────────
    # This ensures that when a bot wants to close one position and open another
    # in the same batch, freed margin is actually available for the new trade.
    decisions.sort(key=lambda d: 0 if float(d.get("cash_amount", 0) or 0) == 0 else 1)

    for decision in decisions:
        if not isinstance(decision, dict):
            continue

        position = str(decision.get("position", "")).strip().lower()
        if position not in {"long", "short", "none"}:
            logging.error(f"[{BOT_NAME}] Unsupported position: {position}")
            continue

        if position == "none":
            continue

        try:
            asset_type = str(decision.get("type", "") or "").strip().lower()
            ticker = normalise_ticker(decision.get("ticker"), asset_type=asset_type or None)
            quote = get_quote(ticker)
            if float(quote.get("c") or 0) <= 0:
                raise ValueError(f"No usable live price returned for {ticker}")
        except Exception as e:
            logging.error(f"[{BOT_NAME}] Cannot execute model-selected ticker: {e}")
            continue

        try:
            leverage = float(decision.get("leverage", 1.0) or 1.0)
        except (ValueError, TypeError):
            leverage = 1.0
        leverage = max(1.0, min(10.0, leverage))

        try:
            cash_amount = float(decision.get("cash_amount", 0.0) or 0.0)
        except (ValueError, TypeError):
            cash_amount = 0.0
        cash_amount = max(0.0, cash_amount)

        if cash_amount == 0.0:
            position = "flat"

        reasoning = str(decision.get("reasoning", ""))
        price = float(quote.get("c") or 0)

        # Catch LLM hallucinating a repeat of the exact same position it already has
        # and treat it as an implicit hold (discard trade) to prevent price-shift spam.
        existing_check = portfolio.get("positions", {}).get(ticker)
        if existing_check is not None and position == existing_check.get("direction"):
            ex_margin = existing_check.get("marginUsed", 0)
            ex_leverage = existing_check.get("leverage", 1.0)
            
            target_notional = cash_amount * leverage
            ex_notional = ex_margin * ex_leverage
            
            # If the total target position size (notional) is within 2% of the existing position size,
            # it's almost certainly an accidental "hold" hallucination.
            if ex_notional > 0 and abs(target_notional - ex_notional) / ex_notional < 0.02:
                logging.info(f"[{BOT_NAME}] Discarding duplicate trade for {ticker} (implicit hold).")
                continue

        # current_shares comes from positions (the sole source of truth)
        existing_pos = portfolio.get("positions", {}).get(ticker)
        current_shares = existing_pos.get("shares", 0) if existing_pos else 0
        is_fractional = "/" in ticker or "-USD" in ticker

        # ── Target share count ────────────────────────────────────────────────
        if price > 0 and position in ("long", "short"):
            # cash_amount is the margin the bot wants to post; notional = margin × leverage
            target_notional = cash_amount * leverage
            raw_target = target_notional / price
            target_shares = (
                round(raw_target, 6) if is_fractional else int(raw_target)
            ) * (1 if position == "long" else -1)
            if target_shares == 0:
                position = "flat"
        else:  # flat
            target_shares = 0

        # ── Proper margin model ───────────────────────────────────────────────
        # Step 1: Settle any existing position in this ticker.
        # Guard on marginUsed > 0, NOT on current_shares != 0.
        # If a legacy position is missing its `shares` field, current_shares would
        # be 0 and the old guard would silently skip settlement — popping the
        # position from the dict without returning the margin to cash (Bug: cash
        # evaporation). Using marginUsed as the canonical "capital is deployed" flag
        # is robust to any data-shape variation.
        closed_direction: str | None = None
        closed_leverage: float | None = None
        realized_pnl: float | None = None
        ex_margin = existing_pos.get("marginUsed", 0) if existing_pos else 0
        if existing_pos is not None and ex_margin > 0:
            closed_direction = existing_pos.get("direction")
            closed_leverage  = existing_pos.get("leverage", 1.0)
            ex_entry    = existing_pos.get("entryPrice", price)
            ex_notional = existing_pos.get("notional", ex_margin)  # notional ≥ margin always
            ex_dir      = 1 if existing_pos.get("direction") == "long" else -1
            realized    = ex_dir * ex_notional * ((price / ex_entry) - 1) if ex_entry > 0 else 0
            realized_pnl = round(realized, 2)
            # Return margin + P&L; floor at 0 (can't recover more than the posted margin)
            portfolio["cash"] += max(0.0, ex_margin + realized)
            portfolio.get("positions", {}).pop(ticker, None)
        elif existing_pos is not None:
            # Position record exists but has zero margin (stale / already-closed entry).
            # Clean it up without touching cash — there's nothing to return.
            portfolio.get("positions", {}).pop(ticker, None)

        # Step 2: Open new position if not flat.
        if target_shares != 0 and price > 0:
            new_notional = abs(target_shares) * price
            new_margin   = new_notional / leverage
            # Cap to available free cash — can't post more margin than you have
            free_cash = float(portfolio["cash"])
            if new_margin > free_cash:
                affordable = free_cash * leverage
                target_shares = (
                    round(affordable / price, 6) if is_fractional else int(affordable / price)
                ) * (1 if position == "long" else -1)
                new_notional = abs(target_shares) * price
                new_margin   = new_notional / leverage
            portfolio["cash"] -= new_margin
            portfolio.setdefault("positions", {})[ticker] = {
                "direction":  position,
                "shares":     target_shares,
                "entryPrice": round(price, 4),
                "leverage":   leverage,
                "notional":   round(new_notional, 2),
                "marginUsed": round(new_margin, 2),
                "assetType":  asset_type or None,
            }
        elif position == "flat":
            portfolio.get("positions", {}).pop(ticker, None)

        # ── Action / shares delta for trade record ────────────────────────────
        net_delta = target_shares - current_shares
        if net_delta > 0:
            action = "buy"
            shares = abs(net_delta)
        elif net_delta < 0:
            action = "sell"
            shares = abs(net_delta)
        else:
            action = "hold"
            shares = 0

        # Implicit holds produce no real event — skip saving and broadcasting.
        if action == "hold":
            continue

        # Per-decision NAV: marked to the portfolio right after this decision.
        try:
            nav_after_decision = compute_nav(portfolio, nav_cache)
        except Exception:
            nav_after_decision = float(portfolio_value)

        trade_record = {
            "id": str(uuid.uuid4()),
            "botName": BOT_NAME,
            "llmProvider": BOT_LLM_PROVIDER,
            "llmModel": BOT_LLM_MODEL,
            "ticker": ticker,
            "assetType": asset_type or None,
            "action": action,
            "position": position,
            "leverage": leverage,
            "shares": shares,
            "price": price,
            "reasoning": reasoning,
            "trigger": f"session-{session}",
            "sessionId": session_id,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "totalValue": round(nav_after_decision, 2),
            "cash": round(float(portfolio["cash"]), 2),
        }

        # Enrich closes with what was closed so the frontend can render
        # "SPOT SELL", "CLOSE 3× LONG", "COVER SHORT", etc.
        if position == "flat" and closed_direction is not None:
            trade_record["closedDirection"] = closed_direction
            trade_record["closedLeverage"]  = closed_leverage
            if realized_pnl is not None:
                trade_record["realizedPnl"] = realized_pnl

        trade_records_to_save.append(trade_record)
        trade_records_to_broadcast.append(trade_record)

    # ── Final Cleanup ─────────────────────────────────────────────────────────
    # Wipe any position whose shares landed at 0 (e.g. affordable rounding).
    for tk in list(portfolio.get("positions", {}).keys()):
        if not portfolio["positions"][tk].get("shares"):
            portfolio["positions"].pop(tk, None)

    # ── Final NAV: free cash + equity in each open position ──────────────────
    # Equity per position = marginUsed + unrealizedPnL (based on live price)
    total_value = float(portfolio["cash"])
    for pos_ticker, pos in portfolio.get("positions", {}).items():
        try:
            live_q  = get_quote(pos_ticker)
            live_px = float(live_q.get("c") or 0)
        except Exception:
            live_px = 0
        entry = pos.get("entryPrice", 0)
        margin = pos.get("marginUsed", 0)
        notional = pos.get("notional", 0)
        if live_px > 0 and entry > 0:
            pos_dir = 1 if pos.get("direction") == "long" else -1
            unreal  = pos_dir * notional * (live_px / entry - 1)
            total_value += margin + unreal
        else:
            total_value += margin  # fallback: assume breakeven

    portfolio["strategyPrompt"] = BOT_STRATEGY_PROMPT
    portfolio["llmProvider"] = BOT_LLM_PROVIDER
    portfolio["llmModel"] = BOT_LLM_MODEL
    portfolio["totalValue"] = total_value
    portfolio.setdefault("startingCash", BOT_STARTING_CASH)

    # Single table of truth: write the full portfolio as a snapshot. The newest
    # row for this bot IS its current state; earlier rows are its history.
    save_portfolio_snapshot(portfolio, total_value, session_id)

    for record in trade_records_to_save:
        trades.create_item(record)
        
    for record in trade_records_to_broadcast:
        try:
            webpubsub_client.send_to_all(
                message=json.dumps({"type": "trade", **record}),
                content_type="application/json",
            )
        except Exception as e:
            logging.error(f"[{BOT_NAME}] Failed to broadcast trade: {e}")

    try:
        logging.info(
            f"[{BOT_NAME}/{BOT_LLM_PROVIDER}/{BOT_LLM_MODEL}] "
            f"{action} {shares} {ticker} @ {price} -- {reasoning} "
            f"(Target position: {position} {leverage}x)"
        )
    except NameError:
        # No per-decision variables if the model returned zero decisions.
        logging.info(f"[{BOT_NAME}/{BOT_LLM_PROVIDER}/{BOT_LLM_MODEL}] Review complete -- no decisions to execute.")

