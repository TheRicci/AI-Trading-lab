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
CURRENT_TAVILY_KEY = (
    os.environ.get("TAVILY_API_KEY")
    or os.environ.get("TAVILY_OPEN_API_KEY")
    or os.environ.get("TAVILY_CLOSE_API_KEY")
)

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
    max_retries: int = 4,
) -> requests.Response:
    for attempt in range(max_retries):
        try:
            r = requests.post(url, headers=headers, json=json, timeout=timeout)
            if r.status_code in (429, 503):
                # 429 = rate limited; 503 = transient overload.
                err_msg = ""
                try:
                    err_body = r.json()
                    err_msg = str(err_body.get("error", {}).get("message", ""))
                    # If daily quota is permanently exhausted (e.g. Gemini 20 RPD free tier limit), do not keep retrying
                    if "GenerateRequestsPerDay" in str(err_body) or ("RESOURCE_EXHAUSTED" in str(err_body) and "limit: 20" in str(err_body)):
                        logging.error(f"[{BOT_NAME}] Daily request quota permanently exhausted for this model: {err_msg}")
                        return r
                except Exception:
                    pass

                wait = min(30.0, 3.0 * (2 ** attempt))
                # 1. Respect Retry-After header if provided by Groq or Gemini
                retry_after = r.headers.get("Retry-After")
                if retry_after:
                    try:
                        wait = max(float(retry_after) + 0.5, wait)
                    except (ValueError, TypeError):
                        pass
                else:
                    # 2. Parse retry duration from error text (e.g. 'try again in 5s', 'retry in 19s')
                    m = re.search(r"(?:try again in|retry in|please retry in|retrydelay)\s*[:\" ]*\s*([0-9.]+)", err_msg, re.IGNORECASE)
                    if m:
                        wait = min(float(m.group(1)) + 0.5, 30.0)

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
    primary_model = (BOT_LLM_MODEL or "gemini-2.5-flash").strip().strip('"').strip("'")

    # Try primary model first; if daily quota (20 RPD on 3.6-flash preview) or 503 is hit, fall back to GA flash models
    candidate_models = [primary_model]
    for alt in ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]:
        if alt not in candidate_models:
            candidate_models.append(alt)

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

    for model_idx, model in enumerate(candidate_models):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
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

        try:
            for turn in range(2):
                body = {
                    "contents": contents,
                    "tools": tools,
                    "generationConfig": {"temperature": 0.7},
                }
                response = post_with_retry(url, json=body, timeout=60)
                if not response.ok:
                    logging.warning(f"[{BOT_NAME}] Gemini error ({response.status_code}) on '{model}': {response.text[:200]}")
                    if response.status_code in (429, 503) and model_idx < len(candidate_models) - 1:
                        # Break out of turn loop to try next candidate model
                        break
                    response.raise_for_status()

                data = response.json()
                candidates = data.get("candidates") or []
                if not candidates:
                    break
                parts = (candidates[0].get("content") or {}).get("parts") or []

                calls = [pt for pt in parts if "functionCall" in pt]
                if calls:
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
                        result_str = search_tavily(query) if (name == "web_search" and query) else "No usable query."
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
            if response.ok:
                last = {
                    "contents": contents
                    + [{"role": "user", "parts": [{"text": "Produce your final JSON decision now without further searching."}]}],
                    "generationConfig": {"temperature": 0.7},
                }
                final_resp = post_with_retry(url, json=last, timeout=60)
                if final_resp.ok:
                    data = final_resp.json()
                    candidates = data.get("candidates") or []
                    if candidates:
                        parts = (candidates[0].get("content") or {}).get("parts") or []
                        texts = [p.get("text", "") for p in parts if "text" in p]
                        if texts:
                            return "\n".join(t.strip() for t in texts if t.strip())
        except Exception as e:
            if model_idx < len(candidate_models) - 1:
                logging.warning(f"[{BOT_NAME}] Model {model} failed ({e}), falling back to {candidate_models[model_idx + 1]}...")
                continue
            raise

    raise RuntimeError(f"Gemini Tavily could not obtain final decision from any candidate models: {candidate_models}")


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
    if not CURRENT_TAVILY_KEY:
        return "Error: TAVILY_API_KEY not configured."
    if not BUDGET.allowance(1):
        logging.warning(f"[{BOT_NAME}] Tavily monthly limit reached; skipping search: {BUDGET.status()}")
        return "Search skipped: monthly Tavily budget exhausted."
    BUDGET.spend(1)
    url = "https://api.tavily.com/search"
    body = {
        "api_key": CURRENT_TAVILY_KEY,
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


def _extract_text_tool_calls(text: str) -> list[tuple[str, str]]:
    """Extract (func_name, query) when a model outputs tool calls in text content instead of tool_calls parameter."""
    calls = []
    cleaned = text.strip()

    # 1. Search for JSON blocks in markdown or bracketed [ ... ] / { ... }
    candidates = []
    for m in re.finditer(r"```(?:json)?\s*(\[.*?\]|\{.*?\})\s*```", cleaned, re.DOTALL | re.IGNORECASE):
        candidates.append(m.group(1))

    start_bracket = cleaned.find('[')
    end_bracket = cleaned.rfind(']')
    if start_bracket != -1 and end_bracket > start_bracket:
        candidates.append(cleaned[start_bracket:end_bracket+1])

    start_brace = cleaned.find('{')
    end_brace = cleaned.rfind('}')
    if start_brace != -1 and end_brace > start_brace:
        candidates.append(cleaned[start_brace:end_brace+1])

    candidates.append(cleaned)

    for cand in candidates:
        try:
            data = json.loads(cand)
            items = data if isinstance(data, list) else [data]
            for it in items:
                if isinstance(it, dict):
                    fn = it.get("name") or it.get("function", {}).get("name")
                    args = it.get("arguments") or it.get("function", {}).get("arguments", {})
                    if fn == "web_search":
                        if isinstance(args, dict):
                            q = args.get("query", "")
                        elif isinstance(args, str):
                            try:
                                q = json.loads(args).get("query", args)
                            except Exception:
                                q = args
                        else:
                            q = ""
                        if q and ("web_search", q) not in calls:
                            calls.append(("web_search", q))
            if calls:
                return calls
        except Exception:
            continue

    # 2. Regex fallback: match 'query': '...' or "query": "..." when web_search is present
    if "web_search" in text:
        query_matches = re.findall(r'["\']query["\']\s*:\s*["\']([^"\']+)["\']', text)
        for q in query_matches:
            if ("web_search", q) not in calls:
                calls.append(("web_search", q))
        if calls:
            return calls

        regex_matches = re.findall(r"web_search\s*\(\s*(?:query\s*=\s*)?[\"']([^\"']+)[\"']\s*\)", text)
        for q in regex_matches:
            if ("web_search", q) not in calls:
                calls.append(("web_search", q))

    return calls


def call_groq_with_tavily(prompt: str, max_turns: int = 2) -> str:
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
            # Check if model formatted tool calls directly in text content (e.g. gpt-oss-120b)
            text_calls = _extract_text_tool_calls(content)
            if text_calls:
                logging.info(f"[{BOT_NAME}] Executing text-encoded tool calls: {text_calls}")
                messages.append({"role": "assistant", "content": content})
                for fn_name, query in text_calls:
                    result_str = search_tavily(query)
                    messages.append({
                        "role": "user",
                        "content": f"Search results for '{query}':\n{result_str}\n\nPlease formulate your portfolio decisions now."
                    })
                continue
            elif content.strip():
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

    # Final attempt: force plain text response using tool_choice="none" so the model is forbidden from calling tools
    messages.append({
        "role": "user",
        "content": "You have completed your research. Output ONLY your final JSON array of decision objects now, starting with [ and ending with ]. Do not search anymore."
    })
    body = {
        "model": BOT_LLM_MODEL,
        "messages": messages,
        "tools": tools,
        "tool_choice": "none",
        "temperature": 0.7,
    }
    response = post_with_retry(url, headers=headers, json=body, timeout=60)
    if not response.ok:
        try:
            err_data = response.json()
            failed_gen = err_data.get("error", {}).get("failed_generation", "")
            if "[" in failed_gen or "{" in failed_gen:
                return failed_gen
        except Exception:
            pass
        logging.warning(f"[{BOT_NAME}] Groq final decision with tool_choice='none' returned {response.status_code}: {response.text}. Retrying without tools parameter...")
        body_no_tools = {"model": BOT_LLM_MODEL, "messages": messages, "temperature": 0.7}
        response = post_with_retry(url, headers=headers, json=body_no_tools, timeout=60)

    if response.ok:
        choice = response.json()["choices"][0]
        msg = choice.get("message", {})
        content = msg.get("content") or msg.get("reasoning") or ""
        # If model STILL outputted a text tool call on final attempt, execute search once and force decision
        final_text_calls = _extract_text_tool_calls(content)
        if final_text_calls:
            logging.info(f"[{BOT_NAME}] Final attempt emitted text tool call: {final_text_calls}. Executing research fallback...")
            messages.append({"role": "assistant", "content": content})
            for fn_name, query in final_text_calls:
                result_str = search_tavily(query)
                messages.append({
                    "role": "user",
                    "content": f"Search results for '{query}':\n{result_str}\n\nOutput ONLY your final JSON decision array now, e.g. [{{...}}]. No more tools."
                })
            fallback_body = {"model": BOT_LLM_MODEL, "messages": messages, "tools": tools, "tool_choice": "none", "temperature": 0.5}
            fb_res = post_with_retry(url, headers=headers, json=fallback_body, timeout=60)
            if fb_res.ok:
                fb_msg = fb_res.json()["choices"][0].get("message", {})
                fb_content = fb_msg.get("content") or fb_msg.get("reasoning") or ""
                if fb_content.strip() and not _extract_text_tool_calls(fb_content):
                    return fb_content
            logging.warning(f"[{BOT_NAME}] Final attempt emitted tool call and fallback had no valid decisions. Defaulting to cash hold [].")
            return "[]"

        if content.strip():
            return content
    else:
        logging.error(f"[{BOT_NAME}] Groq final decision error ({response.status_code}): {response.text}")

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


def get_quote(ticker: str, asset_type: str | None = None) -> dict:
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

    t = str(asset_type or "").strip().lower()

    # 1. Crypto check (skip if explicitly declared as another asset class)
    if t not in ("stock", "etf", "brazil", "forex"):
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
            "ORDER BY c.timestamp DESC OFFSET 0 LIMIT 10"
        )
        params = [{"name": "@name", "value": BOT_NAME}]
        items = list(history.query_items(query=query, parameters=params, partition_key=BOT_NAME))
        if items:
            portfolio = items[0]
            _normalise_positions(portfolio)  # backfill missing `shares` on legacy docs

            # Track previous session & previous day snapshots for P&L tracking
            today_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
            prev_day_item = None
            for it in items[1:]:
                ts = it.get("timestamp", "")
                if ts and not ts.startswith(today_str):
                    prev_day_item = it
                    break
            portfolio["_prevSnapshot"] = items[1] if len(items) > 1 else None
            portfolio["_prevDaySnapshot"] = prev_day_item
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


def save_portfolio_snapshot(portfolio: dict, total_value: float, session_id: str | None = None, extra_audit: dict | None = None):
    """Persist the full portfolio as the newest snapshot in the single history table."""
    portfolio.pop("_prevSnapshot", None)
    portfolio.pop("_prevDaySnapshot", None)
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
    if extra_audit:
        snapshot.update(extra_audit)
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
        quote = get_quote(ticker, asset_type=pos_meta.get("assetType"))
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
                price = float(get_quote(ticker, asset_type=pos.get("assetType")).get("c") or 0)
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

Use live web search to independently find the assets, news, filings, earnings, macro events, price history, technical levels, and metrics you need.

SUPPORTED MARKETS & TICKER FORMATS:
1. US Equities & ETFs: Use standard ticker (e.g. NVDA, AAPL, SPY, QQQ, TSLA, GLD, USO, TLT)
2. Brazilian Equities (B3): Use .SA suffix or B3 ticker (e.g. PETR4.SA, VALE3.SA, ITUB4.SA, BOVA11.SA)
3. Cryptocurrencies (quoted in USD): Use COIN/USD format (e.g. BTC/USD, ETH/USD, SOL/USD)
4. Currencies / Forex: Use YAHOO format (e.g. GBPUSD=X, EURUSD=X, BRL=X)

This is your complete portfolio marked to live prices. `freeCash` is undeployed equity; `marginUsed` is collateral; positions show unrealized P&L. Negative shares = short.
{json.dumps(portfolio_snapshot, separators=(',', ':'))}

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
- **One active position per ticker.** You can hold only one position per ticker (either spot OR leveraged, long OR short). To add to or reduce an existing position, output the new total `cash_amount` with the SAME `position` and `leverage`. If you output a different `position` or `leverage` for an already-held ticker, the existing position is closed first and replaced.
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
        items = data if isinstance(data, list) else [data]
        valid_orders = [
            d for d in items
            if isinstance(d, dict) and ("ticker" in d or "position" in d)
        ]
        return valid_orders
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
            q = get_quote(ticker, asset_type=pos.get("assetType"))
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
                "realizedPnl": -round(margin, 2),
            })
    return liquidations


def _build_trade_record(
    *,
    bot_name: str,
    llm_provider: str,
    llm_model: str,
    ticker: str,
    asset_type: str,
    action: str,
    position: str,
    leverage: float,
    shares: float,
    price: float,
    reasoning: str,
    session: str,
    session_id: str,
    nav_after: float,
    cash_after: float,
    closed_direction: str | None = None,
    closed_leverage: float | None = None,
    realized_pnl: float | None = None,
    partial: bool = False,
) -> dict:
    """Construct a Trades container record for an open, add, reduce, or close."""
    record = {
        "id": str(uuid.uuid4()),
        "botName": bot_name,
        "llmProvider": llm_provider,
        "llmModel": llm_model,
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
        "totalValue": round(nav_after, 2),
        "cash": round(float(cash_after), 2),
    }
    if position == "flat" and closed_direction is not None:
        record["closedDirection"] = closed_direction
        record["closedLeverage"] = closed_leverage
        if realized_pnl is not None:
            record["realizedPnl"] = realized_pnl
        if partial:
            record["partial"] = True
    return record


def _execute_ticker_decision(
    *,
    portfolio: dict,
    ticker: str,
    asset_type: str,
    position: str,
    leverage: float,
    cash_amount: float,
    price: float,
    reasoning: str,
    session: str,
    session_id: str,
    nav_cache: dict,
    fallback_nav: float,
) -> tuple[list[dict], float]:
    """Apply one portfolio decision in-place and return (trade_records, realized_pnl_delta).

    Rules (single active position per ticker):
    - Same direction & same leverage:
      * Target == current shares (or within 2% notional): implicit hold -> no-op.
      * Target > current shares: add incremental shares with weighted-average
        entryPrice without realizing phantom P&L on existing shares.
      * 0 < Target < current shares: partially close the delta, realizing
        proportional P&L and keeping remaining shares at their original entryPrice.
    - Close (cash_amount == 0 / target_shares == 0), direction flip, or leverage change:
      * Close and settle the existing position first (emitting an explicit close
        record with realizedPnl), then open the new target position (emitting an
        open record).
    """
    records: list[dict] = []
    realized_total = 0.0

    def _snap_nav() -> float:
        try:
            return compute_nav(portfolio, nav_cache)
        except Exception:
            return float(fallback_nav)

    if cash_amount == 0.0:
        position = "flat"

    existing_pos = portfolio.get("positions", {}).get(ticker)
    ex_margin = float(existing_pos.get("marginUsed", 0) or 0) if existing_pos else 0.0
    if existing_pos is not None and ex_margin <= 0:
        # Clean up stale zero-margin entries before deciding.
        portfolio.get("positions", {}).pop(ticker, None)
        existing_pos = None

    # Implicit-hold guard: only discard when direction AND leverage match.
    if existing_pos is not None and position == existing_pos.get("direction"):
        ex_leverage = float(existing_pos.get("leverage", 1.0) or 1.0)
        ex_notional = float(existing_pos.get("notional", ex_margin * ex_leverage))
        if abs(leverage - ex_leverage) <= 1e-6:
            target_notional = cash_amount * leverage
            if ex_notional > 0 and abs(target_notional - ex_notional) / ex_notional < 0.02:
                logging.info(f"[{BOT_NAME}] Discarding duplicate trade for {ticker} (implicit hold).")
                return records, realized_total

    is_fractional = "/" in ticker or "-USD" in ticker
    if price > 0 and position in ("long", "short"):
        target_notional = cash_amount * leverage
        raw_target = target_notional / price
        target_shares = (
            round(raw_target, 6) if is_fractional else int(raw_target)
        ) * (1 if position == "long" else -1)
        if target_shares == 0:
            position = "flat"
    else:
        target_shares = 0

    current_shares = existing_pos.get("shares", 0) if existing_pos else 0
    ex_leverage = float(existing_pos.get("leverage", 1.0) or 1.0) if existing_pos else 1.0
    same_side_and_leverage = (
        existing_pos is not None
        and current_shares != 0
        and position in ("long", "short")
        and target_shares != 0
        and position == existing_pos.get("direction")
        and abs(leverage - ex_leverage) <= 1e-6
    )

    # ── Branch 1: Same direction & same leverage (add or partial reduce) ─────
    if same_side_and_leverage:
        cur_abs = abs(current_shares)
        tgt_abs = abs(target_shares)
        if tgt_abs == cur_abs:
            return records, realized_total

        pos_dir = 1 if position == "long" else -1
        ex_entry = float(existing_pos.get("entryPrice", price) or price)
        ex_notional = float(existing_pos.get("notional", ex_margin * ex_leverage))

        if tgt_abs > cur_abs:
            added_abs = round(tgt_abs - cur_abs, 6) if is_fractional else (tgt_abs - cur_abs)
            if added_abs <= 0:
                return records, realized_total
            added_notional = added_abs * price
            added_margin = added_notional / leverage
            free_cash = max(0.0, float(portfolio["cash"]))
            if added_margin > free_cash:
                affordable = free_cash * leverage
                added_abs = (
                    round(affordable / price, 6) if is_fractional else int(affordable / price)
                )
                if added_abs <= 0:
                    logging.info(f"[{BOT_NAME}] Insufficient free cash (${free_cash:.2f}) to add to {ticker}.")
                    return records, realized_total
                added_notional = added_abs * price
                added_margin = added_notional / leverage

            portfolio["cash"] -= added_margin
            new_abs = round(cur_abs + added_abs, 6) if is_fractional else (cur_abs + added_abs)
            new_notional = ex_notional + added_notional
            new_margin = ex_margin + added_margin
            weighted_entry = new_notional / new_abs if new_abs > 0 else price

            portfolio["positions"][ticker] = {
                "direction": position,
                "shares": new_abs * pos_dir,
                "entryPrice": round(weighted_entry, 4),
                "leverage": leverage,
                "notional": round(new_notional, 2),
                "marginUsed": round(new_margin, 2),
                "assetType": asset_type or existing_pos.get("assetType") or None,
            }
            records.append(_build_trade_record(
                bot_name=BOT_NAME,
                llm_provider=BOT_LLM_PROVIDER,
                llm_model=BOT_LLM_MODEL,
                ticker=ticker,
                asset_type=asset_type or existing_pos.get("assetType") or "",
                action="buy" if pos_dir == 1 else "sell",
                position=position,
                leverage=leverage,
                shares=added_abs,
                price=price,
                reasoning=reasoning,
                session=session,
                session_id=session_id,
                nav_after=_snap_nav(),
                cash_after=portfolio["cash"],
            ))
            return records, realized_total

        # Partial reduce: realize P&L only on the closed slice.
        reduced_abs = round(cur_abs - tgt_abs, 6) if is_fractional else (cur_abs - tgt_abs)
        if reduced_abs <= 0:
            return records, realized_total
        frac = reduced_abs / cur_abs
        closed_margin = ex_margin * frac
        closed_notional = ex_notional * frac
        realized = pos_dir * closed_notional * ((price / ex_entry) - 1) if ex_entry > 0 else 0.0
        returned_cash = max(0.0, closed_margin + realized)
        realized_pnl = round(returned_cash - closed_margin, 2)
        realized_total += realized_pnl
        portfolio["cash"] += returned_cash

        rem_abs = round(tgt_abs, 6) if is_fractional else tgt_abs
        portfolio["positions"][ticker] = {
            "direction": position,
            "shares": rem_abs * pos_dir,
            "entryPrice": ex_entry,
            "leverage": leverage,
            "notional": round(max(0.0, ex_notional - closed_notional), 2),
            "marginUsed": round(max(0.0, ex_margin - closed_margin), 2),
            "assetType": asset_type or existing_pos.get("assetType") or None,
        }
        records.append(_build_trade_record(
            bot_name=BOT_NAME,
            llm_provider=BOT_LLM_PROVIDER,
            llm_model=BOT_LLM_MODEL,
            ticker=ticker,
            asset_type=asset_type or existing_pos.get("assetType") or "",
            action="sell" if pos_dir == 1 else "buy",
            position="flat",
            leverage=leverage,
            shares=reduced_abs,
            price=price,
            reasoning=reasoning,
            session=session,
            session_id=session_id,
            nav_after=_snap_nav(),
            cash_after=portfolio["cash"],
            closed_direction=position,
            closed_leverage=leverage,
            realized_pnl=realized_pnl,
            partial=True,
        ))
        return records, realized_total

    # ── Branch 2: Full close, fresh open, or replace (direction/leverage change) ─
    will_reopen = target_shares != 0 and price > 0 and position in ("long", "short")
    if existing_pos is not None:
        closed_direction = existing_pos.get("direction")
        closed_leverage = ex_leverage
        ex_entry = float(existing_pos.get("entryPrice", price) or price)
        ex_notional = float(existing_pos.get("notional", ex_margin))
        ex_dir = 1 if closed_direction == "long" else -1
        realized = ex_dir * ex_notional * ((price / ex_entry) - 1) if ex_entry > 0 else 0.0
        returned_cash = max(0.0, ex_margin + realized)
        realized_pnl = round(returned_cash - ex_margin, 2)
        realized_total += realized_pnl
        portfolio["cash"] += returned_cash
        portfolio.get("positions", {}).pop(ticker, None)

        close_shares = abs(current_shares)
        if close_shares == 0 and ex_entry > 0 and ex_notional > 0:
            raw_s = ex_notional / ex_entry
            close_shares = round(raw_s, 6) if is_fractional else int(raw_s)

        close_reason = (
            f"Closed prior {closed_leverage:g}x {closed_direction} before opening "
            f"{leverage:g}x {position}: {reasoning}"
            if will_reopen
            else reasoning
        )
        records.append(_build_trade_record(
            bot_name=BOT_NAME,
            llm_provider=BOT_LLM_PROVIDER,
            llm_model=BOT_LLM_MODEL,
            ticker=ticker,
            asset_type=asset_type or existing_pos.get("assetType") or "",
            action="sell" if ex_dir == 1 else "buy",
            position="flat",
            leverage=closed_leverage,
            shares=close_shares,
            price=price,
            reasoning=close_reason,
            session=session,
            session_id=session_id,
            nav_after=_snap_nav(),
            cash_after=portfolio["cash"],
            closed_direction=closed_direction,
            closed_leverage=closed_leverage,
            realized_pnl=realized_pnl,
        ))

    if will_reopen:
        new_notional = abs(target_shares) * price
        new_margin = new_notional / leverage
        free_cash = max(0.0, float(portfolio["cash"]))
        if new_margin > free_cash:
            affordable = free_cash * leverage
            target_shares = (
                round(affordable / price, 6) if is_fractional else int(affordable / price)
            ) * (1 if position == "long" else -1)
            new_notional = abs(target_shares) * price
            new_margin = new_notional / leverage

        if target_shares != 0:
            portfolio["cash"] -= new_margin
            portfolio.setdefault("positions", {})[ticker] = {
                "direction": position,
                "shares": target_shares,
                "entryPrice": round(price, 4),
                "leverage": leverage,
                "notional": round(new_notional, 2),
                "marginUsed": round(new_margin, 2),
                "assetType": asset_type or None,
            }
            records.append(_build_trade_record(
                bot_name=BOT_NAME,
                llm_provider=BOT_LLM_PROVIDER,
                llm_model=BOT_LLM_MODEL,
                ticker=ticker,
                asset_type=asset_type,
                action="buy" if position == "long" else "sell",
                position=position,
                leverage=leverage,
                shares=abs(target_shares),
                price=price,
                reasoning=reasoning,
                session=session,
                session_id=session_id,
                nav_after=_snap_nav(),
                cash_after=portfolio["cash"],
            ))

    return records, realized_total


def decide_and_trade(session: str) -> None:
    global CURRENT_TAVILY_KEY
    if os.environ.get("TAVILY_API_KEY"):
        CURRENT_TAVILY_KEY = os.environ.get("TAVILY_API_KEY")
    elif session == "open":
        CURRENT_TAVILY_KEY = os.environ.get("TAVILY_OPEN_API_KEY")
    elif session == "close":
        CURRENT_TAVILY_KEY = os.environ.get("TAVILY_CLOSE_API_KEY")
    else:
        CURRENT_TAVILY_KEY = os.environ.get("TAVILY_OPEN_API_KEY") or os.environ.get("TAVILY_CLOSE_API_KEY")

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

    # Stagger Groq bots to prevent simultaneous token-per-minute (TPM) quota exhaustion (8,000 TPM limit)
    if BOT_LLM_PROVIDER in ("groq", "groq_tavily") and "qwen" in BOT_NAME.lower():
        logging.info(f"[{BOT_NAME}] Staggering Groq API start by 15s to avoid TPM collisions with companion bot...")
        time.sleep(15.0)


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
    session_realized_pnl = sum(float(liq.get("realizedPnl", 0.0)) for liq in liquidations)
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

        # Skip tool-call dictionaries if any slipped through
        if "name" in decision and "ticker" not in decision:
            continue

        position = str(decision.get("position", "")).strip().lower()
        if position not in {"long", "short", "none"}:
            logging.error(f"[{BOT_NAME}] Unsupported position: '{position}' in decision: {decision}")
            continue

        if position == "none":
            continue

        try:
            asset_type = str(decision.get("type", "") or "").strip().lower()
            ticker = normalise_ticker(decision.get("ticker"), asset_type=asset_type or None)
            quote = get_quote(ticker, asset_type=asset_type or None)
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

        reasoning = str(decision.get("reasoning", ""))
        price = float(quote.get("c") or 0)
        nav_cache[ticker] = price

        emitted_records, realized_delta = _execute_ticker_decision(
            portfolio=portfolio,
            ticker=ticker,
            asset_type=asset_type,
            position=position,
            leverage=leverage,
            cash_amount=cash_amount,
            price=price,
            reasoning=reasoning,
            session=session,
            session_id=session_id,
            nav_cache=nav_cache,
            fallback_nav=portfolio_value,
        )
        session_realized_pnl += realized_delta
        trade_records_to_save.extend(emitted_records)
        trade_records_to_broadcast.extend(emitted_records)

    # ── Final Cleanup ─────────────────────────────────────────────────────────
    # Wipe any position whose shares landed at 0 (e.g. affordable rounding).
    for tk in list(portfolio.get("positions", {}).keys()):
        if not portfolio["positions"][tk].get("shares"):
            portfolio["positions"].pop(tk, None)

    # ── Final NAV: free cash + equity in each open position ──────────────────
    # Equity per position = marginUsed + unrealizedPnL (based on live price)
    free_cash = round(float(portfolio.get("cash", 0)), 2)
    total_margin_used = 0.0
    total_unrealized_pnl = 0.0

    for pos_ticker, pos in portfolio.get("positions", {}).items():
        # Use nav_cache first if available, otherwise fetch quote passing assetType
        live_px = nav_cache.get(pos_ticker) if 'nav_cache' in locals() and nav_cache else None
        if live_px is None or live_px <= 0:
            try:
                live_q = get_quote(pos_ticker, asset_type=pos.get("assetType"))
                live_px = float(live_q.get("c") or 0)
            except Exception:
                live_px = 0

        entry = float(pos.get("entryPrice", 0) or 0)
        margin = float(pos.get("marginUsed", 0) or 0)
        notional = float(pos.get("notional", 0) or 0)

        if live_px > 0 and entry > 0:
            pos_dir = 1 if pos.get("direction") == "long" else -1
            unreal = pos_dir * notional * (live_px / entry - 1)
        else:
            live_px = entry  # fallback to entry price if quote fails
            unreal = 0.0

        pos_equity = margin + unreal
        total_margin_used += margin
        total_unrealized_pnl += unreal

        # Enrich position with audit fields marked at this exact snapshot
        pos["markPrice"] = round(live_px, 4)
        pos["marginUsed"] = round(margin, 2)
        pos["notional"] = round(notional, 2)
        pos["unrealizedPnl"] = round(unreal, 2)
        pos["unrealizedPct"] = round((unreal / margin) * 100, 2) if margin > 0 else 0.0
        pos["positionEquity"] = round(pos_equity, 2)

    total_value = round(free_cash + total_margin_used + total_unrealized_pnl, 2)

    # ── Daily & Session PnL Tracking ─────────────────────────────────────────
    prev_snapshot = portfolio.get("_prevSnapshot")
    prev_day_snapshot = portfolio.get("_prevDaySnapshot")

    prev_total = float(prev_snapshot.get("totalValue", portfolio.get("startingCash", BOT_STARTING_CASH))) if prev_snapshot else float(portfolio.get("startingCash", BOT_STARTING_CASH))
    session_pnl = round(total_value - prev_total, 2)
    session_pnl_pct = round((session_pnl / prev_total) * 100, 2) if prev_total > 0 else 0.0

    if prev_day_snapshot:
        prev_day_total = float(prev_day_snapshot.get("totalValue", prev_total))
        daily_pnl = round(total_value - prev_day_total, 2)
        daily_pnl_pct = round((daily_pnl / prev_day_total) * 100, 2) if prev_day_total > 0 else 0.0
    else:
        daily_pnl = session_pnl
        daily_pnl_pct = session_pnl_pct

    prev_cumulative = float(prev_snapshot.get("totalRealizedPnlCumulative", 0.0)) if prev_snapshot else 0.0
    total_cumulative_realized = round(prev_cumulative + session_realized_pnl, 2)

    extra_audit = {
        "freeCash": free_cash,
        "marginUsed": round(total_margin_used, 2),
        "totalUnrealizedPnl": round(total_unrealized_pnl, 2),
        "sessionRealizedPnl": round(session_realized_pnl, 2),
        "totalRealizedPnlCumulative": total_cumulative_realized,
        "sessionPnL": session_pnl,
        "sessionPnLPct": session_pnl_pct,
        "dailyPnL": daily_pnl,
        "dailyPnLPct": daily_pnl_pct,
    }

    portfolio["strategyPrompt"] = BOT_STRATEGY_PROMPT
    portfolio["llmProvider"] = BOT_LLM_PROVIDER
    portfolio["llmModel"] = BOT_LLM_MODEL
    portfolio["totalValue"] = total_value
    portfolio.setdefault("startingCash", BOT_STARTING_CASH)

    # Single table of truth: write the full portfolio as a snapshot. The newest
    # row for this bot IS its current state; earlier rows are its history.
    save_portfolio_snapshot(portfolio, total_value, session_id, extra_audit=extra_audit)

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

    if trade_records_to_save:
        for rec in trade_records_to_save:
            logging.info(
                f"[{BOT_NAME}/{BOT_LLM_PROVIDER}/{BOT_LLM_MODEL}] "
                f"{rec['action']} {rec['shares']} {rec['ticker']} @ {rec['price']} -- {rec['reasoning']} "
                f"(Position: {rec['position']} {rec['leverage']}x)"
            )
    else:
        logging.info(f"[{BOT_NAME}/{BOT_LLM_PROVIDER}/{BOT_LLM_MODEL}] Review complete -- no decisions to execute.")

