"""
AWS Lambda bot handler — port of the Azure Functions bot (functions/bot/function_app.py).
Runs 7 trading bots (fed_gemini, fed_gptoss, flow_gemini, flow_qwen, conf_gemini,
conf_gptoss, conf_qwen) with identical portfolio/margin/trade-execution logic.

Key differences from Azure version:
  - Lambda handler entry point (not Event Grid trigger)
  - Reads pre-scraped signal data from S3 and injects into LLM prompt
  - Cosmos DB uses lazy initialization
  - No Web PubSub real-time broadcast (writes to Cosmos DB only)
"""
import json
import os
import logging
import re
import uuid
import datetime
import time
import tempfile

import boto3
import requests
from azure.cosmos import CosmosClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── Configuration from Lambda environment variables ──────────────────

BOT_NAME = os.environ.get("BOT_NAME", "unnamed-bot")
BOT_STRATEGY_PROMPT = os.environ.get("BOT_STRATEGY_PROMPT", "You are a trading bot.")
BOT_STARTING_CASH = float(os.environ.get("BOT_STARTING_CASH", "100000"))
BOT_LLM_PROVIDER = os.environ.get("BOT_LLM_PROVIDER", "gemini")
BOT_LLM_MODEL = os.environ.get("BOT_LLM_MODEL", "gemini-2.5-flash")

FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
CURRENT_TAVILY_KEY = (
    os.environ.get("TAVILY_API_KEY")
    or os.environ.get("TAVILY_CLOSE_API_KEY")
    or os.environ.get("TAVILY_OPEN_API_KEY")
)

COSMOS_ENDPOINT = os.environ.get("COSMOS_ENDPOINT")
COSMOS_KEY = os.environ.get("COSMOS_KEY")
S3_SIGNAL_BUCKET = os.environ.get("S3_SIGNAL_BUCKET", "")

# ── Lazy-init Cosmos DB ──────────────────────────────────────────────

_cosmos_client = None
_database = None
_trades = None
_history = None

def _ensure_cosmos():
    global _cosmos_client, _database, _trades, _history
    if _cosmos_client is None:
        _cosmos_client = CosmosClient(COSMOS_ENDPOINT, COSMOS_KEY)
        _database = _cosmos_client.get_database_client("TradingBots")
        _trades = _database.get_container_client("Trades")
        _history = _database.get_container_client("PortfolioHistory")

# ── S3 signal reader ────────────────────────────────────────────────

_s3 = None

def read_signal(signal_type):
    """Read today's pre-scraped signal JSON from S3."""
    global _s3
    if not S3_SIGNAL_BUCKET:
        return None
    if _s3 is None:
        _s3 = boto3.client("s3")
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    key = f"{signal_type}_signals/{today}.json"
    try:
        response = _s3.get_object(Bucket=S3_SIGNAL_BUCKET, Key=key)
        return json.loads(response["Body"].read().decode("utf-8"))
    except Exception:
        return None

# ── Tavily Budget ────────────────────────────────────────────────────

MONTHLY_LIMIT = int(os.environ.get("TAVILY_MONTHLY_LIMIT", "1000"))
WARN_AT = int(os.environ.get("TAVILY_WARN_AT", "850"))

class TavilyBudget:
    def __init__(self, path=None):
        self.path = path or os.path.join(tempfile.gettempdir(), "tavily_budget.json")
        self.month = datetime.date.today().strftime("%Y-%m")
        self.used = self._load()

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

    def allowance(self, n=1):
        return (self.used + n) <= MONTHLY_LIMIT

    def spend(self, n=1):
        self.used += n
        self._save()
        if self.used >= MONTHLY_LIMIT:
            logger.warning(f"[TavilyBudget] Monthly limit ({MONTHLY_LIMIT}) reached ({self.used}).")
        elif self.used >= WARN_AT:
            logger.warning(f"[TavilyBudget] Approaching limit: {self.used}/{MONTHLY_LIMIT}.")
        return self.used

    def status(self):
        return f"{self.used}/{MONTHLY_LIMIT} Tavily credits used ({self.month})"

BUDGET = TavilyBudget()


# ── Lambda entry point ──────────────────────────────────────────────

def _nyse_holidays(year):
    """Return NYSE market holidays for *year* as a set of 'YYYY-MM-DD' strings.
    Uses observed-day rules (Mon when holiday falls on Sun, Fri when Sat).
    No external packages — pure stdlib."""
    import calendar as _cal

    def observed(d):
        if d.weekday() == 5:
            return datetime.date(d.year, d.month, d.day - 1)
        if d.weekday() == 6:
            return datetime.date(d.year, d.month, d.day + 1)
        return d

    def nth_weekday(y, m, wd, n):
        first = datetime.date(y, m, 1)
        delta = (wd - first.weekday()) % 7
        return datetime.date(y, m, 1 + delta + (n - 1) * 7)

    def last_weekday(y, m, wd):
        last_day = _cal.monthrange(y, m)[1]
        last = datetime.date(y, m, last_day)
        return datetime.date(y, m, last_day - (last.weekday() - wd) % 7)

    holidays = {
        observed(datetime.date(year, 1, 1)),    # New Year's Day
        observed(datetime.date(year, 6, 19)),   # Juneteenth
        observed(datetime.date(year, 7, 4)),    # Independence Day
        observed(datetime.date(year, 12, 25)),  # Christmas Day
        nth_weekday(year, 1, 0, 3),             # MLK Day (3rd Mon Jan)
        nth_weekday(year, 2, 0, 3),             # Presidents' Day (3rd Mon Feb)
        last_weekday(year, 5, 0),               # Memorial Day (last Mon May)
        nth_weekday(year, 9, 0, 1),             # Labor Day (1st Mon Sep)
        nth_weekday(year, 11, 3, 4),            # Thanksgiving (4th Thu Nov)
    }
    # Good Friday (Easter Sunday - 2) via Butcher-Meeus algorithm
    a = year % 19
    b, c = divmod(year, 100)
    d_v, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d_v - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m_v = (a + 11 * h + 22 * l) // 451
    e_month = (h + l - 7 * m_v + 114) // 31
    e_day = ((h + l - 7 * m_v + 114) % 31) + 1
    easter = datetime.date(year, e_month, e_day)
    holidays.add(easter - datetime.timedelta(days=2))
    return {d.strftime("%Y-%m-%d") for d in holidays}


def is_nyse_holiday(d=None):
    """Return True if *d* (default: today UTC) is a NYSE market holiday."""
    if d is None:
        d = datetime.datetime.now(datetime.timezone.utc).date()
    return d.strftime("%Y-%m-%d") in _nyse_holidays(d.year)


def lambda_handler(event, context):
    session = event.get("session", "close")
    logger.info(f"[{BOT_NAME}] Lambda invoked for session={session}")

    # ── Holiday guard ────────────────────────────────────────────────────────
    if is_nyse_holiday():
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        logger.info(f"[{BOT_NAME}] {today} is a NYSE holiday — skipping {session} session.")
        return {"statusCode": 200, "body": json.dumps({"bot": BOT_NAME, "skipped": "holiday", "date": today})}

    decide_and_trade(session)
    return {"statusCode": 200, "body": json.dumps({"bot": BOT_NAME, "session": session})}


# ── HTTP retry helper ───────────────────────────────────────────────

def post_with_retry(url, headers=None, json_body=None, timeout=60, max_retries=4):
    for attempt in range(max_retries):
        try:
            r = requests.post(url, headers=headers, json=json_body, timeout=timeout)
            if r.status_code in (429, 503):
                err_msg = ""
                try:
                    err_body = r.json()
                    err_msg = str(err_body.get("error", {}).get("message", ""))
                    if "GenerateRequestsPerDay" in str(err_body) or (
                        "RESOURCE_EXHAUSTED" in str(err_body) and "limit: 20" in str(err_body)
                    ):
                        logger.error(f"[{BOT_NAME}] Daily quota exhausted: {err_msg}")
                        return r
                except Exception:
                    pass

                wait = min(30.0, 3.0 * (2 ** attempt))
                retry_after = r.headers.get("Retry-After")
                if retry_after:
                    try:
                        wait = max(float(retry_after) + 0.5, wait)
                    except (ValueError, TypeError):
                        pass
                else:
                    m = re.search(
                        r"(?:try again in|retry in|please retry in|retrydelay)"
                        r"\s*[:\" ]*\s*([0-9.]+)", err_msg, re.IGNORECASE
                    )
                    if m:
                        wait = min(float(m.group(1)) + 0.5, 30.0)

                logger.warning(f"[{BOT_NAME}] {r.status_code}. Retrying in {wait:.1f}s "
                               f"(attempt {attempt + 1}/{max_retries})")
                time.sleep(wait)
                continue
            return r
        except requests.RequestException as e:
            if attempt == max_retries - 1:
                raise
            logger.warning(f"[{BOT_NAME}] Request error: {e}. Retrying in 2s...")
            time.sleep(2.0)
    return r


# ── LLM provider dispatch ──────────────────────────────────────────

def search_tavily(query):
    if not CURRENT_TAVILY_KEY:
        return "Error: TAVILY_API_KEY not configured."
    if not BUDGET.allowance(1):
        logger.warning(f"[{BOT_NAME}] Tavily limit reached; skipping: {BUDGET.status()}")
        return "Search skipped: monthly Tavily budget exhausted."
    BUDGET.spend(1)
    body = {
        "api_key": CURRENT_TAVILY_KEY,
        "query": query,
        "search_depth": "basic",
        "include_answer": True,
        "max_results": 3,
    }
    try:
        res = post_with_retry("https://api.tavily.com/search", json_body=body, timeout=15)
        if not res.ok:
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
        logger.error(f"[{BOT_NAME}] Tavily search failed: {e}")
        return f"Search error: {e}"


def call_gemini(prompt):
    key = (GEMINI_API_KEY or "").strip().strip('"').strip("'")
    primary_model = (BOT_LLM_MODEL or "gemini-2.5-flash").strip().strip('"').strip("'")

    candidate_models = [primary_model]
    for alt in ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]:
        if alt not in candidate_models:
            candidate_models.append(alt)

    tools = [{"functionDeclarations": [{
        "name": "web_search",
        "description": "Search the live web for recent financial news, macro updates, and market data.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search query"}},
            "required": ["query"],
        },
    }]}]

    for model_idx, model in enumerate(candidate_models):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        contents = [{"role": "user", "parts": [{
            "text": prompt + "\n\nUse web_search calls to gather live context only when needed. "
                    "Minimize the number of searches: do as few as you need, then produce your final JSON decision."
        }]}]

        try:
            for turn in range(2):
                body = {"contents": contents, "tools": tools,
                        "generationConfig": {"temperature": 0.7}}
                response = post_with_retry(url, json_body=body, timeout=60)
                if not response.ok:
                    logger.warning(f"[{BOT_NAME}] Gemini error ({response.status_code}) on '{model}': {response.text[:200]}")
                    if response.status_code in (429, 503) and model_idx < len(candidate_models) - 1:
                        break
                    response.raise_for_status()

                data = response.json()
                candidates = data.get("candidates") or []
                if not candidates:
                    break
                parts = (candidates[0].get("content") or {}).get("parts") or []

                calls = [pt for pt in parts if "functionCall" in pt]
                if calls:
                    contents.append({"role": "model", "parts": [dict(c) for c in calls]})
                    for call in calls:
                        fc = call["functionCall"]
                        name = fc.get("name", "")
                        args = fc.get("args") or {}
                        query = args.get("query", "") if isinstance(args, dict) else ""
                        result_str = search_tavily(query) if (name == "web_search" and query) else "No usable query."
                        contents.append({"role": "user", "parts": [{
                            "functionResponse": {"name": name, "response": {"result": result_str}}
                        }]})
                    continue

                texts = [pt.get("text", "") for pt in parts if "text" in pt]
                if texts:
                    return "\n".join(t.strip() for t in texts if t.strip())

            if response.ok:
                last = {
                    "contents": contents + [{"role": "user", "parts": [{
                        "text": "Produce your final JSON decision now without further searching."
                    }]}],
                    "generationConfig": {"temperature": 0.7},
                }
                final_resp = post_with_retry(url, json_body=last, timeout=60)
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
                logger.warning(f"[{BOT_NAME}] Model {model} failed ({e}), falling back...")
                continue
            raise

    raise RuntimeError(f"Gemini could not obtain final decision from any model: {candidate_models}")


def _extract_text_tool_calls(text):
    calls = []
    cleaned = text.strip()

    candidates_list = []
    for m in re.finditer(r"```(?:json)?\s*(\[.*?\]|\{.*?\})\s*```", cleaned, re.DOTALL | re.IGNORECASE):
        candidates_list.append(m.group(1))

    start_bracket = cleaned.find('[')
    end_bracket = cleaned.rfind(']')
    if start_bracket != -1 and end_bracket > start_bracket:
        candidates_list.append(cleaned[start_bracket:end_bracket+1])

    start_brace = cleaned.find('{')
    end_brace = cleaned.rfind('}')
    if start_brace != -1 and end_brace > start_brace:
        candidates_list.append(cleaned[start_brace:end_brace+1])

    candidates_list.append(cleaned)

    for cand in candidates_list:
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


def call_openai_compatible(base_url, api_key, prompt):
    url = f"{base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": BOT_LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "response_format": {"type": "json_object"},
    }
    response = post_with_retry(url, headers=headers, json_body=body, timeout=60)
    if not response.ok:
        logger.error(f"[{BOT_NAME}] Groq API error ({response.status_code}): {response.text}")
        response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def call_groq_with_tavily(prompt, max_turns=2):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    tools = [{"type": "function", "function": {
        "name": "web_search",
        "description": "Search the live web for recent financial news and data.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search query"}},
            "required": ["query"],
        },
    }}]
    messages = [
        {"role": "system", "content": "You are a professional trading analyst. You may perform 1 to 2 targeted web searches if needed, then immediately formulate your final JSON decision."},
        {"role": "user", "content": prompt},
    ]

    for turn in range(max_turns):
        body = {"model": BOT_LLM_MODEL, "messages": messages, "tools": tools,
                "tool_choice": "auto", "temperature": 0.7}
        response = post_with_retry(url, headers=headers, json_body=body, timeout=60)
        if not response.ok:
            try:
                err_data = response.json()
                failed_gen = err_data.get("error", {}).get("failed_generation", "")
                if "{" in failed_gen and "position" in failed_gen:
                    return failed_gen
            except Exception:
                pass
            logger.error(f"[{BOT_NAME}] Groq Tavily error ({response.status_code}): {response.text}")
            response.raise_for_status()

        choice = response.json()["choices"][0]
        msg = choice["message"]
        tool_calls = msg.get("tool_calls")
        content = msg.get("content") or ""

        if not tool_calls:
            text_calls = _extract_text_tool_calls(content)
            if text_calls:
                logger.info(f"[{BOT_NAME}] Text-encoded tool calls: {text_calls}")
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
            messages.append({"role": "tool", "tool_call_id": tool_call["id"], "content": result_str})

        if turn == max_turns - 2:
            messages.append({"role": "user", "content": "You have completed your research. Now produce your final decision JSON object immediately."})

    # Final attempt
    messages.append({"role": "user", "content": "Output ONLY your final JSON array now, starting with [ and ending with ]. Do not search anymore."})
    body = {"model": BOT_LLM_MODEL, "messages": messages, "tools": tools,
            "tool_choice": "none", "temperature": 0.7}
    response = post_with_retry(url, headers=headers, json_body=body, timeout=60)
    if not response.ok:
        try:
            err_data = response.json()
            failed_gen = err_data.get("error", {}).get("failed_generation", "")
            if "[" in failed_gen or "{" in failed_gen:
                return failed_gen
        except Exception:
            pass
        body_no_tools = {"model": BOT_LLM_MODEL, "messages": messages, "temperature": 0.7}
        response = post_with_retry(url, headers=headers, json_body=body_no_tools, timeout=60)

    if response.ok:
        choice = response.json()["choices"][0]
        msg_out = choice.get("message", {})
        content = msg_out.get("content") or msg_out.get("reasoning") or ""
        final_text_calls = _extract_text_tool_calls(content)
        if final_text_calls:
            logger.info(f"[{BOT_NAME}] Final attempt text tool call: {final_text_calls}")
            messages.append({"role": "assistant", "content": content})
            for fn_name, query in final_text_calls:
                result_str = search_tavily(query)
                messages.append({"role": "user", "content": f"Search results for '{query}':\n{result_str}\n\nOutput ONLY your final JSON decision array now. No more tools."})
            fb_body = {"model": BOT_LLM_MODEL, "messages": messages, "tools": tools,
                       "tool_choice": "none", "temperature": 0.5}
            fb_res = post_with_retry(url, headers=headers, json_body=fb_body, timeout=60)
            if fb_res.ok:
                fb_msg = fb_res.json()["choices"][0].get("message", {})
                fb_content = fb_msg.get("content") or fb_msg.get("reasoning") or ""
                if fb_content.strip() and not _extract_text_tool_calls(fb_content):
                    return fb_content
            return "[]"
        if content.strip():
            return content

    raise RuntimeError(f"Groq Tavily could not obtain final decision from {BOT_LLM_MODEL}")


def call_llm(prompt):
    if BOT_LLM_PROVIDER == "gemini":
        return call_gemini(prompt)
    elif BOT_LLM_PROVIDER == "groq":
        return call_openai_compatible("https://api.groq.com/openai/v1", GROQ_API_KEY, prompt)
    elif BOT_LLM_PROVIDER == "groq_tavily":
        return call_groq_with_tavily(prompt)
    else:
        raise ValueError(f"Unknown BOT_LLM_PROVIDER: {BOT_LLM_PROVIDER}")


# ── Multi-Market Data Engine ────────────────────────────────────────

CRYPTO_SYMBOLS = {
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK",
    "DOT", "MATIC", "POL", "NEAR", "SUI", "APT", "UNI", "LTC", "BCH",
    "SHIB", "PEPE", "RENDER", "FET", "TAO", "AAVE", "MKR", "ARB", "OP",
}
_crypto_universe_cache = {"t": 0.0, "symbols": None}
_CRYPTO_UNIVERSE_TTL = 6 * 3600.0


def _coinbase_universe():
    try:
        r = requests.get("https://api.coinbase.com/v2/currencies", timeout=8)
        if r.ok:
            return {c.get("id", "").upper() for c in r.json().get("data", []) if c.get("id")}
    except Exception:
        pass
    return set()


def _binance_universe():
    try:
        r = requests.get("https://api.binance.com/api/v3/exchangeInfo", timeout=8)
        if r.ok:
            return {s.get("baseAsset", "").upper() for s in r.json().get("symbols", [])
                    if s.get("quoteAsset") == "USDT" and s.get("baseAsset")}
    except Exception:
        pass
    return set()


def fetch_crypto_universe():
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


def get_crypto_price(ticker):
    raw = ticker.upper().replace(" ", "").replace("-", "").replace("/", "")
    if raw.endswith("USD"):
        base = raw[:-3]
    elif raw.endswith("USDT"):
        base = raw[:-4]
    else:
        base = raw

    if base not in fetch_crypto_universe() and "/" not in ticker and "-USD" not in ticker and "USD" not in ticker:
        return None

    try:
        url = f"https://api.coinbase.com/v2/prices/{base}-USD/spot"
        r = requests.get(url, timeout=6)
        if r.ok:
            price = float(r.json().get("data", {}).get("amount") or 0.0)
            if price > 0:
                return {"c": price, "symbol": f"{base}/USD", "source": "coinbase"}
    except Exception:
        pass
    try:
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={base}USDT"
        r = requests.get(url, timeout=6)
        if r.ok:
            price = float(r.json().get("price") or 0.0)
            if price > 0:
                return {"c": price, "symbol": f"{base}/USD", "source": "binance"}
    except Exception:
        pass
    return None


def get_yahoo_price(ticker):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r = requests.get(url, headers=headers, timeout=8)
        if r.ok:
            data = r.json()
            meta = data.get("chart", {}).get("result", [{}])[0].get("meta", {})
            price = float(meta.get("regularMarketPrice") or 0.0)
            prev_close = float(meta.get("previousClose") or meta.get("chartPreviousClose") or price)
            if price > 0:
                return {"c": price, "pc": prev_close, "d": price - prev_close,
                        "dp": ((price - prev_close) / prev_close) * 100 if prev_close else 0.0,
                        "symbol": ticker, "source": "yahoo"}
    except Exception:
        pass
    return None


def get_quote(ticker, asset_type=None):
    clean = str(ticker or "").strip().upper()
    if not clean:
        raise ValueError("Empty ticker symbol provided")
    t = str(asset_type or "").strip().lower()

    if t not in ("stock", "etf", "brazil", "forex"):
        c_quote = get_crypto_price(clean)
        if c_quote and c_quote.get("c", 0) > 0:
            return c_quote

    if re.fullmatch(r"[A-Z]{4}[0-9]{1,2}", clean) and not clean.endswith(".SA"):
        b3_quote = get_yahoo_price(f"{clean}.SA")
        if b3_quote and b3_quote.get("c", 0) > 0:
            return b3_quote

    if "." in clean or "=" in clean:
        y_quote = get_yahoo_price(clean)
        if y_quote and y_quote.get("c", 0) > 0:
            return y_quote

    if FINNHUB_API_KEY:
        try:
            r = requests.get("https://finnhub.io/api/v1/quote",
                             params={"symbol": clean, "token": FINNHUB_API_KEY}, timeout=8)
            if r.ok:
                data = r.json()
                price = float(data.get("c") or 0.0)
                if price > 0:
                    return data
        except Exception:
            pass

    y_quote = get_yahoo_price(clean)
    if y_quote and y_quote.get("c", 0) > 0:
        return y_quote

    raise ValueError(f"No usable live market price found for {clean}")


def normalise_ticker(value, asset_type=None):
    raw = str(value or "").strip().upper()
    if not raw:
        raise ValueError("Model did not provide a valid ticker")
    t = str(asset_type or "").strip().lower()

    if t == "crypto":
        clean = raw.replace(" ", "").replace("-", "").replace("/", "")
        if clean.endswith("USDT"):
            clean = clean[:-4]
        elif clean.endswith("USD"):
            clean = clean[:-3]
        return f"{clean}/USD"
    if t == "brazil":
        return raw if raw.endswith(".SA") else f"{raw}.SA"
    if t == "forex":
        return raw if raw.endswith("=X") else f"{raw}=X"
    if t in ("stock", "etf"):
        return raw.replace("/USD", "").replace("-USD", "").replace("USDT", "")

    # Heuristic fallback
    clean_crypto = raw.replace(" ", "").replace("-", "").replace("/", "")
    universe = fetch_crypto_universe()
    base = None
    if clean_crypto.endswith("USDT"):
        base = clean_crypto[:-4]
    elif clean_crypto.endswith("USD"):
        base = clean_crypto[:-3]
    if base is not None and base in universe:
        return f"{base}/USD"
    if raw in universe and ("/" in raw or "-USD" in raw.upper()):
        return f"{raw}/USD"
    if re.fullmatch(r"[A-Z]{4}[0-9]{1,2}", raw):
        return f"{raw}.SA"
    if re.fullmatch(r"(EUR|GBP|USD|JPY|AUD|CAD|CHF|NZD|BRL){2}", raw) and not raw.endswith("=X"):
        return f"{raw}=X"
    return raw


# ── Portfolio + market data ─────────────────────────────────────────

def _normalise_positions(portfolio):
    for ticker, pos in portfolio.get("positions", {}).items():
        if pos.get("shares") is not None:
            continue
        notional_val = pos.get("notional", 0)
        ep = pos.get("entryPrice", 0)
        is_frac = "/" in ticker or "-USD" in ticker
        if ep > 0 and notional_val > 0:
            raw = notional_val / ep
            s = round(raw, 6) if is_frac else int(raw)
            s *= 1 if pos.get("direction") == "long" else -1
        else:
            s = 0
        pos["shares"] = s


def get_portfolio():
    _ensure_cosmos()
    try:
        query = ("SELECT * FROM c WHERE c.botName = @name "
                 "ORDER BY c.timestamp DESC OFFSET 0 LIMIT 10")
        params = [{"name": "@name", "value": BOT_NAME}]
        items = list(_history.query_items(query=query, parameters=params, partition_key=BOT_NAME))
        if items:
            portfolio = items[0]
            _normalise_positions(portfolio)
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
        logger.warning(f"[{BOT_NAME}] Could not read portfolio, starting fresh: {e}")

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


def save_portfolio_snapshot(portfolio, total_value, session_id=None, extra_audit=None):
    _ensure_cosmos()
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
    _history.create_item(snapshot)


def build_portfolio_snapshot(portfolio):
    positions = []
    quotes = {}
    free_cash = max(0.0, float(portfolio.get("cash", 0)))
    total_value = free_cash
    total_margin_used = 0.0

    for ticker, pos_meta in portfolio.get("positions", {}).items():
        shares = pos_meta.get("shares")
        if shares is None:
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
            raise ValueError(f"No usable live price for held symbol {ticker}")
        quotes[ticker] = quote

        entry_price = pos_meta.get("entryPrice", price)
        margin_used = pos_meta.get("marginUsed", 0)
        notional = pos_meta.get("notional", abs(shares) * entry_price)
        direction = 1 if pos_meta.get("direction") == "long" else -1
        unreal_pnl = direction * notional * (price / entry_price - 1) if entry_price > 0 else 0

        position_equity = margin_used + unreal_pnl
        total_value += position_equity
        total_margin_used += margin_used

        positions.append({
            "ticker": ticker, "side": "long" if shares > 0 else "short",
            "shares": shares, "markPrice": round(price, 4),
            "entryPrice": round(entry_price, 4), "marginUsed": round(margin_used, 2),
            "unrealizedPnl": round(unreal_pnl, 2), "positionEquity": round(position_equity, 2),
        })

    snapshot = {
        "freeCash": round(free_cash, 2), "marginUsed": round(total_margin_used, 2),
        "positions": positions, "totalNetLiquidationValue": round(total_value, 2),
        "valuationTimestamp": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return snapshot, quotes


def compute_nav(portfolio, quote_cache=None):
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
            total = total + margin
    return total


def _liquidate_margin_calls(portfolio):
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
        entry = pos.get("entryPrice", live_px)
        margin = pos.get("marginUsed", 0)
        notional = pos.get("notional", 0)
        unreal = pos_dir * notional * (live_px / entry - 1) if entry > 0 else 0
        if unreal <= -margin:
            logger.warning(f"[{BOT_NAME}] MARGIN CALL: {ticker} | Loss ${-unreal:,.2f} wiped margin ${margin:,.2f}")
            del portfolio["positions"][ticker]
            liquidations.append({
                "id": str(uuid.uuid4()), "botName": BOT_NAME,
                "llmProvider": BOT_LLM_PROVIDER, "llmModel": BOT_LLM_MODEL,
                "ticker": ticker, "action": "sell" if pos_dir == 1 else "buy",
                "position": "flat", "closedDirection": "long" if pos_dir == 1 else "short",
                "closedLeverage": pos.get("leverage", 1.0), "leverage": pos.get("leverage", 1.0),
                "shares": abs(pos.get("shares", 0)), "price": live_px,
                "reasoning": "MARGIN CALL — position liquidated automatically.",
                "trigger": "margin-call",
                "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "cash": round(float(portfolio["cash"]), 2),
                "realizedPnl": -round(margin, 2),
            })
    return liquidations


def _build_trade_record(
    *,
    bot_name,
    llm_provider,
    llm_model,
    ticker,
    asset_type,
    action,
    position,
    leverage,
    shares,
    price,
    reasoning,
    session,
    session_id,
    nav_after,
    cash_after,
    closed_direction=None,
    closed_leverage=None,
    realized_pnl=None,
    partial=False,
):
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
    portfolio,
    ticker,
    asset_type,
    position,
    leverage,
    cash_amount,
    price,
    reasoning,
    session,
    session_id,
    nav_cache,
    fallback_nav,
):
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
    records = []
    realized_total = 0.0

    def _snap_nav():
        try:
            return compute_nav(portfolio, nav_cache)
        except Exception:
            return float(fallback_nav)

    if cash_amount == 0.0:
        position = "flat"

    existing_pos = portfolio.get("positions", {}).get(ticker)
    ex_margin = float(existing_pos.get("marginUsed", 0) or 0) if existing_pos else 0.0
    if existing_pos is not None and ex_margin <= 0:
        portfolio.get("positions", {}).pop(ticker, None)
        existing_pos = None

    # Implicit-hold guard: only discard when direction AND leverage match.
    if existing_pos is not None and position == existing_pos.get("direction"):
        ex_leverage = float(existing_pos.get("leverage", 1.0) or 1.0)
        ex_notional = float(existing_pos.get("notional", ex_margin * ex_leverage))
        if abs(leverage - ex_leverage) <= 1e-6:
            target_notional = cash_amount * leverage
            if ex_notional > 0 and abs(target_notional - ex_notional) / ex_notional < 0.02:
                logger.info(f"[{BOT_NAME}] Discarding duplicate trade for {ticker} (implicit hold).")
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
                    logger.info(f"[{BOT_NAME}] Insufficient free cash (${free_cash:.2f}) to add to {ticker}.")
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


# ── Signal data injection ───────────────────────────────────────────

def _build_signal_context():
    """Read pre-scraped S3 signals and format for LLM prompt injection."""
    signal_context = ""
    bot_lower = BOT_NAME.lower()

    if "fed" in bot_lower or "conf" in bot_lower:
        fed_data = read_signal("fed")
        if fed_data:
            signal_context += "\n\n=== FEDERAL RESERVE SIGNAL DATA (pre-scraped) ===\n"
            signal_context += f"Date: {fed_data.get('date', 'unknown')}\n"
            if fed_data.get("speeches"):
                signal_context += "Recent speeches:\n"
                for s in fed_data["speeches"][:10]:
                    signal_context += f"  - {s.get('speaker', '?')}: {s.get('title', '?')} ({s.get('date', '?')})\n"
            if fed_data.get("tavily_summaries"):
                for ts in fed_data["tavily_summaries"][:3]:
                    signal_context += f"Research: {ts.get('summary', '')[:300]}\n"
            if fed_data.get("fomc_headlines"):
                signal_context += "FOMC headlines: " + "; ".join(fed_data["fomc_headlines"][:5]) + "\n"
            if fed_data.get("financialjuice_headlines"):
                signal_context += "FinancialJuice: " + "; ".join(fed_data["financialjuice_headlines"][:5]) + "\n"
            if fed_data.get("members_mentioned"):
                signal_context += "Members active: " + ", ".join(fed_data["members_mentioned"]) + "\n"

    if "flow" in bot_lower or "conf" in bot_lower:
        flow_data = read_signal("flow")
        if flow_data:
            signal_context += "\n\n=== OPTIONS FLOW SIGNAL DATA (pre-scraped) ===\n"
            signal_context += f"Date: {flow_data.get('date', 'unknown')}\n"
            signal_context += f"Aggregate Put/Call Ratio: {flow_data.get('aggregate_put_call_ratio', 'N/A')}\n"
            sentiment = flow_data.get("market_sentiment", {})
            if sentiment:
                signal_context += f"Market Direction: {sentiment.get('overall_direction', 'unknown')}\n"
                signal_context += f"Total Call Vol: {sentiment.get('total_call_volume', 0):,} | Total Put Vol: {sentiment.get('total_put_volume', 0):,}\n"
            if flow_data.get("ticker_flow"):
                signal_context += "Notable ticker flow:\n"
                for tf in flow_data["ticker_flow"][:10]:
                    signal_context += (
                        f"  - {tf.get('ticker', '?')}: Calls={tf.get('total_call_volume', 0):,}, "
                        f"Puts={tf.get('total_put_volume', 0):,}, "
                        f"P/C={tf.get('ticker_pc_ratio', 0):.2f}\n"
                    )
                    for vs in tf.get("volume_spikes", [])[:3]:
                        signal_context += (
                            f"    SPIKE: {vs.get('type', '?')} {vs.get('strike', '?')} "
                            f"exp={vs.get('expiry', '?')} vol={vs.get('volume', 0):,} "
                            f"vs OI={vs.get('open_interest', 0):,} "
                            f"({vs.get('vol_oi_ratio', 0):.1f}x)\n"
                        )
            if flow_data.get("tavily_flow_intel"):
                for ti in flow_data["tavily_flow_intel"][:3]:
                    signal_context += f"Flow Intel: {ti.get('summary', '')[:300]}\n"

    return signal_context


# ── Decision + execution ────────────────────────────────────────────

def ask_llm_for_decision(portfolio_snapshot, session):
    signal_context = _build_signal_context()

    prompt = f"""{BOT_STRATEGY_PROMPT}
{signal_context}

Use live web search to independently find the assets, news, filings, earnings, macro events, price history, technical levels, and metrics you need.

SUPPORTED MARKETS & TICKER FORMATS:
1. US Equities & ETFs: Use standard ticker (e.g. NVDA, AAPL, SPY, QQQ, TSLA, GLD, TLT)
2. Brazilian Equities (B3): Use .SA suffix (e.g. PETR4.SA, VALE3.SA)
3. Cryptocurrencies in USD: Use COIN/USD format (e.g. BTC/USD, ETH/USD)
4. Currencies / Forex: Use YAHOO format (e.g. GBPUSD=X, EURUSD=X)

This is your complete portfolio marked to live prices:
{json.dumps(portfolio_snapshot, separators=(',', ':'))}

Trigger: market {session} (a scheduled review)

Choose deliberate portfolio adjustments. Output up to 5 decisions.
For each: ticker, type ("stock"/"etf"/"crypto"/"forex"/"brazil"), position ("long"/"short"),
cash_amount (margin to allocate; 0 to close), leverage (1.0 to 10.0), reasoning.

Holding is implicit — omit tickers you want to keep.
One active position per ticker: you can hold only one position per ticker (either spot OR leveraged, long OR short). To add to or reduce an existing position, output the new total `cash_amount` with the SAME `position` and `leverage`. If you output a different `position` or `leverage` for an already-held ticker, the existing position is closed first and replaced.
List closes first.

Respond with ONLY a JSON array. Start with [ and end with ].
Example:
[
  {{"ticker": "SPY", "type": "etf", "position": "long", "cash_amount": 0, "leverage": 1.0, "reasoning": "Exiting."}},
  {{"ticker": "NVDA", "type": "stock", "position": "long", "cash_amount": 25000, "leverage": 1.0, "reasoning": "Strong catalyst."}}
]
"""
    text = call_llm(prompt).strip()

    md_match = re.search(r"```(?:json)?\s*(\[.*?\]|\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if md_match:
        text_to_parse = md_match.group(1)
    else:
        start_idx = text.find('[')
        end_idx = text.rfind(']')
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            text_to_parse = text[start_idx:end_idx+1]
        else:
            text_to_parse = text

    try:
        data = json.loads(text_to_parse)
        items = data if isinstance(data, list) else [data]
        return [d for d in items if isinstance(d, dict) and ("ticker" in d or "position" in d)]
    except json.JSONDecodeError:
        raise ValueError(f"Could not parse JSON from LLM: {text}")


def decide_and_trade(session):
    global CURRENT_TAVILY_KEY
    if os.environ.get("TAVILY_API_KEY"):
        CURRENT_TAVILY_KEY = os.environ.get("TAVILY_API_KEY")
    elif session == "open":
        CURRENT_TAVILY_KEY = os.environ.get("TAVILY_OPEN_API_KEY")
    elif session == "close":
        CURRENT_TAVILY_KEY = os.environ.get("TAVILY_CLOSE_API_KEY")
    else:
        CURRENT_TAVILY_KEY = os.environ.get("TAVILY_OPEN_API_KEY") or os.environ.get("TAVILY_CLOSE_API_KEY")

    # Stagger Groq bots to prevent TPM collisions
    if BOT_LLM_PROVIDER in ("groq", "groq_tavily"):
        if "qwen" in BOT_NAME.lower():
            logger.info(f"[{BOT_NAME}] Staggering Groq start by 15s...")
            time.sleep(15.0)
        elif "gptoss" in BOT_NAME.lower():
            logger.info(f"[{BOT_NAME}] Staggering Groq start by 30s...")
            time.sleep(30.0)

    portfolio = get_portfolio()

    session_id = f"{session}-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d')}"
    _ensure_cosmos()
    try:
        _query = 'SELECT VALUE COUNT(1) FROM c WHERE c.botName = @b AND c.sessionId = @sid'
        check = list(_history.query_items(
            query=_query,
            parameters=[{"name": "@b", "value": BOT_NAME}, {"name": "@sid", "value": session_id}],
            partition_key=BOT_NAME,
        ))
        if check and check[0] and check[0] > 0:
            logger.info(f"[{BOT_NAME}] Session {session_id} already handled; skipping.")
            return
    except Exception as e:
        logger.warning(f"[{BOT_NAME}] Idempotency check failed: {e}")

    # Migrate old negative-cash portfolios
    if float(portfolio.get("cash", 0)) < 0:
        total_margin_posted = sum(pos.get("marginUsed", 0) for pos in portfolio.get("positions", {}).values())
        current_total = float(portfolio.get("totalValue", portfolio.get("startingCash", BOT_STARTING_CASH)))
        portfolio["cash"] = max(0.0, current_total - total_margin_posted)

    liquidations = _liquidate_margin_calls(portfolio)
    session_realized_pnl = sum(float(liq.get("realizedPnl", 0.0)) for liq in liquidations)
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
        logger.error(f"[{BOT_NAME}] Could not value positions; skipping: {e}")
        return

    try:
        decisions = ask_llm_for_decision(portfolio_snapshot, session)
        logger.info(f"[{BOT_NAME}] Decisions: {decisions}")
    except Exception as e:
        logger.error(f"[{BOT_NAME}] LLM call failed: {e}")
        return

    if not isinstance(decisions, list):
        logger.error(f"[{BOT_NAME}] Model response was not a JSON list")
        return

    try:
        execution_snapshot, exec_quotes = build_portfolio_snapshot(portfolio)
    except Exception as e:
        logger.error(f"[{BOT_NAME}] Could not revalue before execution: {e}")
        return
    portfolio_value = execution_snapshot["totalNetLiquidationValue"]
    nav_cache = {t: float(q.get("c") or 0) for t, q in exec_quotes.items() if q.get("c")}

    trade_records = list(liquidations)
    decisions.sort(key=lambda d: 0 if float(d.get("cash_amount", 0) or 0) == 0 else 1)

    for decision in decisions:
        if not isinstance(decision, dict):
            continue
        if "name" in decision and "ticker" not in decision:
            continue

        position = str(decision.get("position", "")).strip().lower()
        if position not in {"long", "short", "none"}:
            continue
        if position == "none":
            continue

        try:
            asset_type = str(decision.get("type", "") or "").strip().lower()
            ticker = normalise_ticker(decision.get("ticker"), asset_type=asset_type or None)
            quote = get_quote(ticker, asset_type=asset_type or None)
            if float(quote.get("c") or 0) <= 0:
                raise ValueError(f"No usable live price for {ticker}")
        except Exception as e:
            logger.error(f"[{BOT_NAME}] Cannot execute ticker: {e}")
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
        trade_records.extend(emitted_records)

    # Cleanup zero-share positions
    for tk in list(portfolio.get("positions", {}).keys()):
        if not portfolio["positions"][tk].get("shares"):
            portfolio["positions"].pop(tk, None)

    # Final NAV
    free_cash = round(float(portfolio.get("cash", 0)), 2)
    total_margin_used = 0.0
    total_unrealized_pnl = 0.0
    for pos_ticker, pos in portfolio.get("positions", {}).items():
        live_px = nav_cache.get(pos_ticker) if nav_cache else None
        if live_px is None or live_px <= 0:
            try:
                live_px = float(get_quote(pos_ticker, asset_type=pos.get("assetType")).get("c") or 0)
            except Exception:
                live_px = 0
        entry = float(pos.get("entryPrice", 0) or 0)
        margin = float(pos.get("marginUsed", 0) or 0)
        notional = float(pos.get("notional", 0) or 0)
        if live_px > 0 and entry > 0:
            pos_dir = 1 if pos.get("direction") == "long" else -1
            unreal = pos_dir * notional * (live_px / entry - 1)
        else:
            live_px = entry
            unreal = 0.0
        pos_equity = margin + unreal
        total_margin_used += margin
        total_unrealized_pnl += unreal
        pos["markPrice"] = round(live_px, 4)
        pos["unrealizedPnl"] = round(unreal, 2)
        pos["unrealizedPct"] = round((unreal / margin) * 100, 2) if margin > 0 else 0.0
        pos["positionEquity"] = round(pos_equity, 2)

    total_value = round(free_cash + total_margin_used + total_unrealized_pnl, 2)

    # PnL tracking
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
        "freeCash": free_cash, "marginUsed": round(total_margin_used, 2),
        "totalUnrealizedPnl": round(total_unrealized_pnl, 2),
        "sessionRealizedPnl": round(session_realized_pnl, 2),
        "totalRealizedPnlCumulative": total_cumulative_realized,
        "sessionPnL": session_pnl, "sessionPnLPct": session_pnl_pct,
        "dailyPnL": daily_pnl, "dailyPnLPct": daily_pnl_pct,
    }

    portfolio["strategyPrompt"] = BOT_STRATEGY_PROMPT
    portfolio["llmProvider"] = BOT_LLM_PROVIDER
    portfolio["llmModel"] = BOT_LLM_MODEL
    portfolio["totalValue"] = total_value
    portfolio.setdefault("startingCash", BOT_STARTING_CASH)

    save_portfolio_snapshot(portfolio, total_value, session_id, extra_audit=extra_audit)

    _ensure_cosmos()
    for record in trade_records:
        _trades.create_item(record)

    # TODO: Add SNS/WebSocket notification for real-time dashboard updates
    # (Azure version uses Web PubSub; AWS equivalent would be API Gateway WebSocket or SNS)

    if trade_records:
        for rec in trade_records:
            logger.info(
                f"[{BOT_NAME}/{BOT_LLM_PROVIDER}/{BOT_LLM_MODEL}] "
                f"{rec['action']} {rec['shares']} {rec['ticker']} @ {rec['price']} -- {rec['reasoning']} "
                f"(Position: {rec['position']} {rec['leverage']}x)"
            )
    else:
        logger.info(f"[{BOT_NAME}/{BOT_LLM_PROVIDER}/{BOT_LLM_MODEL}] Review complete -- no decisions to execute.")
