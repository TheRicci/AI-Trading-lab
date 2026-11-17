#!/usr/bin/env python3
"""
backtest.py - Local Backtesting & Simulation Runner for AI Trading Bots

Supports:
  1. Live Session Simulation (single bot or multi-bot competition)
  2. Auto-key loading from terraform.tfvars or local.settings.json
  3. Interactive or automated multi-session replay
  4. Daily Market Analysis Report generation & testing
  5. Local portfolio persistence (JSON sidecar) with P&L tracking & leaderboard
"""

import argparse
import datetime
import json
import logging
import os
import re
import sys
import time
import uuid
from pathlib import Path

try:
    import requests
except ImportError:
    print("Error: 'requests' package is required. Install with: pip install requests")
    sys.exit(1)

import tavily_budget

# ---------------------------------------------------------------------------
# Paths & Key Discovery
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
STATE_DIR = SCRIPTS_DIR / "backtest_state"


def load_api_keys():
    """Extract API keys from env, terraform.tfvars, or local.settings.json."""
    keys = {
        "FINNHUB_API_KEY": os.environ.get("FINNHUB_API_KEY", ""),
        "GEMINI_API_KEY": os.environ.get("GEMINI_API_KEY", ""),
        "GROQ_API_KEY": os.environ.get("GROQ_API_KEY", ""),
        "TAVILY_API_KEY": os.environ.get("TAVILY_API_KEY", ""),
        "TAVILY_OPEN_API_KEY": os.environ.get("TAVILY_OPEN_API_KEY", ""),
        "TAVILY_CLOSE_API_KEY": os.environ.get("TAVILY_CLOSE_API_KEY", ""),
        "TAVILY_RESEARCH_API_KEY": os.environ.get("TAVILY_RESEARCH_API_KEY", ""),
    }
    
    # Try reading terraform.tfvars if keys are missing
    tfvars_path = REPO_ROOT / "terraform" / "terraform.tfvars"
    if tfvars_path.exists():
        try:
            content = tfvars_path.read_text(encoding="utf-8")
            for var_name, key_name in [
                ("finnhub_api_key", "FINNHUB_API_KEY"),
                ("gemini_api_key", "GEMINI_API_KEY"),
                ("groq_api_key", "GROQ_API_KEY"),
                ("tavily_api_key", "TAVILY_API_KEY"),
                ("tavily_open_api_key", "TAVILY_OPEN_API_KEY"),
                ("tavily_close_api_key", "TAVILY_CLOSE_API_KEY"),
                ("tavily_research_api_key", "TAVILY_RESEARCH_API_KEY"),
            ]:
                if not keys[key_name]:
                    m = re.search(rf'{var_name}\s*=\s*"([^"]+)"', content)
                    if m:
                        keys[key_name] = m.group(1)
        except Exception as e:
            logging.debug(f"Failed to parse tfvars: {e}")

    # Try reading functions/bot/local.settings.json
    bot_settings = REPO_ROOT / "functions" / "bot" / "local.settings.json"
    if bot_settings.exists():
        try:
            data = json.loads(bot_settings.read_text(encoding="utf-8"))
            values = data.get("Values", {})
            for k in keys:
                if not keys[k] and k in values:
                    keys[k] = values[k]
        except Exception as e:
            logging.debug(f"Failed to parse bot local.settings.json: {e}")

    return keys


API_KEYS = load_api_keys()
FINNHUB_API_KEY = API_KEYS["FINNHUB_API_KEY"]
GEMINI_API_KEY = API_KEYS["GEMINI_API_KEY"]
GROQ_API_KEY = API_KEYS["GROQ_API_KEY"]
TAVILY_API_KEY = (
    API_KEYS["TAVILY_API_KEY"]
    or API_KEYS["TAVILY_OPEN_API_KEY"]
    or API_KEYS["TAVILY_CLOSE_API_KEY"]
    or API_KEYS["TAVILY_RESEARCH_API_KEY"]
)

BUDGET = tavily_budget.TavilyBudget()

STRATEGY_PROMPT = (
    "You are an autonomous trading agent. You can choose whatever style fits "
    "today's conditions best, including trend-following, mean reversion, "
    "catalyst/event-driven, valuation, volatility breakout, sentiment, or "
    "staying out entirely. Use web search whenever it helps you gather live "
    "market context, recent news, filings, earnings, macro events, technical "
    "levels, or other relevant metrics."
)

BOT_CONFIGS: dict[str, dict] = {
    "open_gemini": {
        "name": "Gemini 2.5 Flash (Open)",
        "llm_provider": "gemini",
        "llm_model": "gemini-2.5-flash",
    },
    "open_gptoss": {
        "name": "GPT-OSS 120B (Groq + Tavily Open)",
        "llm_provider": "groq_tavily",
        "llm_model": "openai/gpt-oss-120b",
    },
    "open_qwen": {
        "name": "Alibaba Qwen 3.8 27B (Groq + Tavily Open)",
        "llm_provider": "groq_tavily",
        "llm_model": "qwen/qwen3.8-27b",
    },
    "close_gemini": {
        "name": "Gemini 2.5 Flash (Close - Catalyst Breakout)",
        "llm_provider": "gemini",
        "llm_model": "gemini-2.5-flash",
    },
    "close_gptoss": {
        "name": "GPT-OSS 120B (Close - Macro & Sector Rotation)",
        "llm_provider": "groq_tavily",
        "llm_model": "openai/gpt-oss-120b",
    },
    "close_qwen": {
        "name": "Alibaba Qwen 3.8 27B (Close - Contrarian Mean Reversion)",
        "llm_provider": "groq_tavily",
        "llm_model": "qwen/qwen3.8-27b",
    },
}



# ---------------------------------------------------------------------------
# HTTP Helper with Rate-Limit & Backoff Retry
# ---------------------------------------------------------------------------

def post_with_retry(
    url: str,
    headers: dict | None = None,
    json: dict | None = None,
    timeout: int = 60,
    max_retries: int = 6,
    base_wait: float = 6.0,
) -> requests.Response:
    for attempt in range(max_retries):
        try:
            r = requests.post(url, headers=headers, json=json, timeout=timeout)
            if r.status_code in (429, 503):
                # 429 = rate limited; 503 = transient overload (high demand). Both retryable.
                wait = base_wait * (attempt + 1)
                
                # 1. Check Retry-After header
                retry_after = r.headers.get("Retry-After")
                if retry_after:
                    try:
                        wait = max(float(retry_after) + 1.0, wait)
                    except (ValueError, TypeError):
                        pass

                # 2. Check JSON error message for explicit wait recommendation
                try:
                    err_json = r.json()
                    err_msg = err_json.get("error", {}).get("message", "") or str(err_json)
                    print(f"    [HTTP {r.status_code}] Message: {err_msg}")
                    m = re.search(r"try again in ([0-9.]+)s", err_msg, re.IGNORECASE)
                    if m:
                        wait = max(float(m.group(1)) + 1.0, wait)
                except Exception:
                    print(f"    [HTTP {r.status_code}] Raw response: {r.text}")

                print(f"    [HTTP {r.status_code}] Waiting {wait:.1f}s before retry (attempt {attempt + 1}/{max_retries})...")
                time.sleep(wait)
                continue
            return r
        except requests.RequestException as e:
            if attempt == max_retries - 1:
                raise
            print(f"    [Network error: {e}] Retrying in 3s...")
            time.sleep(3.0)
    return r


# ---------------------------------------------------------------------------
# LLM Providers (mirrors functions/bot/function_app.py)
# ---------------------------------------------------------------------------

def call_gemini(model: str, prompt: str, verbose: bool = False) -> str:
    """Agentic Tavily search loop for Gemini.

    Mirrors functions/bot/function_app.py. The model decides what it wants to
    search; we run each query through Tavily and feed results back, iterating
    until it produces its final JSON. A turn budget and a prompt hint encourage
    it to minimize searches so we stay inside the free Tavily monthly quota.
    """
    key = (GEMINI_API_KEY or "").strip().strip('"').strip("'")
    if not key:
        raise ValueError("GEMINI_API_KEY is not set.")
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
        if verbose:
            print(f"    [Gemini turn {turn + 1}/4] awaiting model reply...")
        body = {
            "contents": contents,
            "tools": tools,
            "generationConfig": {"temperature": 0.7},
        }
        r = post_with_retry(url, json=body, timeout=120, max_retries=4, base_wait=3.0)
        if not r.ok:
            raise RuntimeError(f"Gemini API error ({model}): {r.text[:500]}")

        data = r.json()
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
                    if verbose:
                        print(f"    [Tavily Search] '{query}'")
                    result_str = search_tavily(query, verbose=verbose)
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

    # Final fallback: ask for a plain answer without further searching.
    last = {
        "contents": contents
        + [{"role": "user", "parts": [{"text": "Produce your final JSON decision now without further searching."}]}],
        "generationConfig": {"temperature": 0.7},
    }
    r = post_with_retry(url, json=last, timeout=120, max_retries=4, base_wait=3.0)
    if r.ok:
        data = r.json()
        candidates = data.get("candidates") or []
        if candidates:
            parts = (candidates[0].get("content") or {}).get("parts") or []
            texts = [p.get("text", "") for p in parts if "text" in p]
            if texts:
                return "\n".join(t.strip() for t in texts if t.strip())
    raise RuntimeError(f"Gemini Tavily could not obtain final decision from {model}")


def call_openai_compatible(model: str, base_url: str, api_key: str, prompt: str, verbose: bool = False) -> str:
    if not api_key:
        raise ValueError("API key for OpenAI-compatible endpoint is not set.")
    url = f"{base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "response_format": {"type": "json_object"},
    }
    r = post_with_retry(url, headers=headers, json=body, timeout=60)
    if not r.ok:
        raise RuntimeError(f"Groq API error ({r.status_code}): {r.text[:500]}")
    return r.json()["choices"][0]["message"]["content"]


def search_tavily(query: str, verbose: bool = False) -> str:
    if not TAVILY_API_KEY:
        return "Error: TAVILY_API_KEY not configured."
    if not BUDGET.allowance(1):
        print(f"    [TavilyBudget] Skipping search (monthly limit reached): {BUDGET.status()}")
        return "Search skipped: monthly Tavily budget exhausted."
    BUDGET.spend(1)
    r = post_with_retry(
        "https://api.tavily.com/search",
        json={
            "api_key": TAVILY_API_KEY,
            "query": query,
            "search_depth": "basic",
            "include_answer": True,
            "max_results": 3,
        },
        timeout=15,
    )
    if not r.ok:
        return f"Tavily search HTTP error: {r.status_code}"
    data = r.json()
    answer = data.get("answer")
    results = data.get("results", [])
    snippets = []
    if answer:
        snippets.append(f"Summary: {answer[:250]}")
    for res in results[:3]:
        title = res.get("title", "")
        content = (res.get("content") or "")[:200]
        snippets.append(f"- {title}: {content}")
    return "\n".join(snippets) if snippets else "No results found."


def call_groq_with_tavily(model: str, prompt: str, max_turns: int = 5, verbose: bool = False) -> str:
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY is not set.")
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    tools = [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": (
                    "Search the live web for recent stock market news, asset movements, "
                    "macro updates, earnings, and financial data."
                ),
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
            "model": model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.7,
        }
        r = post_with_retry(url, headers=headers, json=body, timeout=60)
        if not r.ok:
            # Check if failed_generation has JSON decision
            try:
                err_data = r.json()
                failed_gen = err_data.get("error", {}).get("failed_generation", "")
                if "{" in failed_gen and "position" in failed_gen:
                    return failed_gen
            except Exception:
                pass
            raise RuntimeError(f"Groq Tavily loop error ({r.status_code}): {r.text[:500]}")

        choice = r.json()["choices"][0]
        msg = choice["message"]
        tool_calls = msg.get("tool_calls")
        content = msg.get("content") or ""

        if not tool_calls:
            if content.strip():
                return content
            # If empty content with no tool calls, ask for final JSON
            messages.append({"role": "user", "content": "Please output the final JSON decision now."})
            continue

        messages.append(msg)
        for tc in tool_calls:
            try:
                args = json.loads(tc["function"].get("arguments", "{}"))
            except Exception:
                args = {}
            query = args.get("query", "") if tc["function"]["name"] == "web_search" else ""
            if verbose:
                print(f"    [Tavily Search] '{query}'")
            res_snippet = search_tavily(query, verbose=verbose) if query else "No query provided"
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": res_snippet})

        # If reaching max turns, explicitly ask to conclude
        if turn == max_turns - 2:
            messages.append({
                "role": "user",
                "content": "You have completed your research. Now produce your final decision JSON object immediately."
            })

    # Final attempt with tools enabled
    body = {"model": model, "messages": messages, "tools": tools, "tool_choice": "auto", "temperature": 0.7}
    r = post_with_retry(url, headers=headers, json=body, timeout=60)
    if r.ok:
        choice = r.json()["choices"][0]
        content = choice["message"].get("content") or ""
        if content.strip():
            return content
    raise RuntimeError(f"Groq Tavily could not obtain final decision from {model}")


def dispatch_llm(provider: str, model: str, prompt: str, verbose: bool = False) -> str:
    if verbose:
        print(f"\n{'='*70}\n[LLM PROMPT -> {provider} / {model}]\n{'='*70}\n{prompt}\n{'='*70}")

    if provider == "gemini":
        raw = call_gemini(model, prompt, verbose=verbose)
    elif provider == "groq":
        raw = call_openai_compatible(model, "https://api.groq.com/openai/v1", GROQ_API_KEY, prompt, verbose=verbose)
    elif provider == "groq_tavily":
        raw = call_groq_with_tavily(model, prompt, verbose=verbose)
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")

    if verbose:
        print(f"\n[RAW LLM RESPONSE]\n{raw}\n{'='*70}")
    return raw


# ---------- Multi-Market Data Engine (US, Brazil B3, Crypto, Global) ----------

# The tradable crypto universe is discovered live (Coinbase + Binance) and cached,
# NOT a hardcoded allowlist — so the bots can pick any listed coin, not just a
# small curated set. This static set is only a fast-path fallback if discovery fails.
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


def get_crypto_price(ticker: str) -> dict | None:
    """Fetch live crypto price in USD from Coinbase or Binance public APIs (24/7, free)."""
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


def get_quote(ticker: str, cache: dict | None = None) -> dict:
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

    if cache is not None and clean in cache:
        return cache[clean]

    # 1. Crypto check
    c_quote = get_crypto_price(clean)
    if c_quote and c_quote.get("c", 0) > 0:
        if cache is not None:
            cache[clean] = c_quote
        return c_quote

    # 2. Brazilian stock without suffix (e.g. PETR4, VALE3, ITUB4 -> PETR4.SA)
    if re.fullmatch(r"[A-Z]{4}[0-9]{1,2}", clean) and not clean.endswith(".SA"):
        b3_quote = get_yahoo_price(f"{clean}.SA")
        if b3_quote and b3_quote.get("c", 0) > 0:
            if cache is not None:
                cache[clean] = b3_quote
            return b3_quote

    # 3. International suffixes (e.g. .SA, .L, .DE, =X for forex)
    if "." in clean or "=" in clean:
        y_quote = get_yahoo_price(clean)
        if y_quote and y_quote.get("c", 0) > 0:
            if cache is not None:
                cache[clean] = y_quote
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
                    if cache is not None:
                        cache[clean] = data
                    return data
        except Exception:
            pass

    # 5. Fallback to Yahoo Finance for any other symbol
    y_quote = get_yahoo_price(clean)
    if y_quote and y_quote.get("c", 0) > 0:
        if cache is not None:
            cache[clean] = y_quote
        return y_quote

    raise ValueError(f"No usable live market price found for {clean}")


# ---------------------------------------------------------------------------
# Local Portfolio & Decision Logic
# ---------------------------------------------------------------------------

def normalise_ticker(value) -> str:
    raw = str(value or "").strip().upper()
    if not raw:
        raise ValueError("Model did not provide a valid market-data ticker")

    # 1. Normalize crypto (e.g. BTC, BTCUSD, BTC-USD, BTC/USD -> BTC/USD).
    #    Any coin in the live tradable universe is accepted, not just a fixed set.
    clean_crypto = raw.replace(" ", "").replace("-", "").replace("/", "")
    universe = fetch_crypto_universe()
    if clean_crypto.endswith("USDT"):
        base = clean_crypto[:-4]
        if base in universe or "/" in raw or "-USD" in raw:
            return f"{base}/USD"
    if clean_crypto.endswith("USD"):
        base = clean_crypto[:-3]
        if base in universe or "/" in raw or "-USD" in raw:
            return f"{base}/USD"
    if raw in universe:
        return f"{raw}/USD"

    # 2. Normalize Brazilian stocks (e.g. PETR4 -> PETR4.SA)
    if re.fullmatch(r"[A-Z]{4}[0-9]{1,2}", raw):
        return f"{raw}.SA"

    # 3. Normalize common forex pairs (e.g. GBPUSD -> GBPUSD=X, EURUSD -> EURUSD=X)
    if re.fullmatch(r"(EUR|GBP|USD|JPY|AUD|CAD|CHF|NZD|BRL){2}", raw) and not raw.endswith("=X"):
        return f"{raw}=X"

    return raw


def build_portfolio_snapshot(portfolio: dict, quote_cache: dict | None = None) -> tuple[dict, dict]:
    """Mark every open position to live prices using the margin model.

    NAV = freeCash + sum(marginUsed + unrealizedPnL) for each position.
    Cash is always >= 0 (undeployed equity); leverage is tracked per-position.
    """
    positions = []
    quotes = {}
    free_cash  = max(0.0, float(portfolio.get("cash", 0)))
    total_value = free_cash
    total_margin_used = 0.0

    for ticker, shares in portfolio.get("holdings", {}).items():
        if not shares:
            continue
        q = get_quote(ticker, cache=quote_cache)
        price = float(q.get("c") or 0)
        if price <= 0:
            raise ValueError(f"No usable live price returned for held symbol {ticker}")
        quotes[ticker] = q

        pos_meta    = portfolio.get("positions", {}).get(ticker, {})
        entry_price = pos_meta.get("entryPrice", price)
        margin_used = pos_meta.get("marginUsed", 0)
        notional    = pos_meta.get("notional", abs(shares) * entry_price)
        direction   = 1 if shares > 0 else -1
        unreal_pnl  = direction * notional * (price / entry_price - 1) if entry_price > 0 else 0

        position_equity = margin_used + unreal_pnl
        total_value     += position_equity
        total_margin_used += margin_used

        positions.append({
            "ticker":         ticker,
            "side":           "long" if shares > 0 else "short",
            "shares":         shares,
            "markPrice":      round(price, 4),
            "entryPrice":     round(entry_price, 4),
            "marginUsed":     round(margin_used, 2),
            "unrealizedPnl":  round(unreal_pnl, 2),
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


def ask_llm_for_decision(portfolio_snapshot: dict, session: str, provider: str, model: str, verbose: bool = False) -> list:
    prompt = f"""{STRATEGY_PROMPT}

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
- `ticker`: The symbol (e.g. NVDA, AAPL)
- `position`: "long" or "short". To completely close an existing position, just output its ticker with `"cash_amount": 0`.
- `cash_amount`: The absolute dollar amount of your free cash you want to allocate to this trade as margin (e.g. 15000). To increase/decrease an existing position, output the NEW total cash amount you want allocated to it. To close an existing position, output 0.
- `leverage`: 1.0 (spot) up to 10.0 (margin). If you choose 0 cash_amount, this is ignored.
- `reasoning`: A short sentence explaining why.

CRITICAL INSTRUCTIONS:
- If you want to hold an existing position exactly as it is without changing its size, DO NOT include it in your output.
- You can allocate up to your current `freeCash`. If you close or reduce existing positions, remember that their `marginUsed` will be returned to your freeCash pool during execution, making it available for new trades in this same batch.

Respond with ONLY a JSON array containing your decision objects, no other text. Example:
[
  {{"ticker": "NVDA", "position": "long", "cash_amount": 25000, "leverage": 1.0, "reasoning": "..."}},
  {{"ticker": "SPY", "position": "short", "cash_amount": 0, "leverage": 1.0, "reasoning": "Closing out this position completely."}}
]
"""
    text = dispatch_llm(provider, model, prompt, verbose=verbose).strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text.removeprefix("json").strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else [data]
    except json.JSONDecodeError:
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        m2 = re.search(r"\{.*\}", text, re.DOTALL)
        if m2:
            return [json.loads(m2.group(0))]
        raise ValueError(f"Could not parse JSON decision from LLM output: {text}")


def execute_session_step(
    bot_id: str,
    session: str,
    portfolio: dict,
    trade_log: list,
    session_num: int,
    verbose: bool = False,
    quote_cache: dict | None = None,
) -> dict:
    cfg = BOT_CONFIGS[bot_id]
    provider = cfg["llm_provider"]
    model = cfg["llm_model"]
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"\n  +-----------------------------------------------------------")
    print(f"  | Bot: {bot_id} ({cfg['name']})")
    print(f"  | Session {session_num:02d} | Trigger: market-{session} | Time: {ts}")
    print(f"  +-----------------------------------------------------------")

    # 1. Snapshot
    try:
        snap, _ = build_portfolio_snapshot(portfolio, quote_cache=quote_cache)
    except Exception as e:
        print(f"  [ERROR] Snapshot failed: {e}")
        return {}

    nlv      = snap["totalNetLiquidationValue"]
    avail    = max(0.0, float(snap.get("freeCash", 0)))
    used     = float(snap.get("marginUsed", 0))
    if used > 0:
        print(f"  | Current NAV : ${nlv:>12,.2f}  |  Margin Used: ${used:>12,.2f}  |  Available: ${avail:>10,.2f}")
    else:
        print(f"  | Current NAV : ${nlv:>12,.2f}  |  Cash: ${avail:>12,.2f}")
    if snap["positions"]:
        for p in snap["positions"]:
            side_str = p['side'].upper()
            print(f"  | Position    : {p['ticker']:<8} {side_str:<5} {p['shares']:>+10.4f} sh @ ${p['markPrice']:>8.2f} = ${p['marketValue']:>10.2f}")
    else:
        print(f"  | Position    : (all cash / flat)")

    # ── Check for margin calls before asking LLM ─────────────────────────────
    for mc_ticker in list(portfolio.get("positions", {}).keys()):
        mc_pos = portfolio["positions"][mc_ticker]
        mc_q   = get_quote(mc_ticker, cache=quote_cache)
        mc_px  = float(mc_q.get("c") or 0)
        if mc_px <= 0:
            continue
        mc_dir = 1 if mc_pos.get("direction") == "long" else -1
        mc_entry = mc_pos.get("entryPrice", mc_px)
        mc_margin = mc_pos.get("marginUsed", 0)
        mc_notional = mc_pos.get("notional", 0)
        mc_unreal = mc_dir * mc_notional * (mc_px / mc_entry - 1) if mc_entry > 0 else 0
        if mc_unreal <= -mc_margin:
            print(f"  | MARGIN CALL : {mc_ticker} | Loss ${-mc_unreal:,.2f} >= Margin ${mc_margin:,.2f} — LIQUIDATED")
            portfolio["holdings"][mc_ticker] = 0
            del portfolio["positions"][mc_ticker]

    # 2. Decision
    print(f"  | Querying {provider}/{model}...")
    try:
        decisions = ask_llm_for_decision(snap, session, provider, model, verbose=verbose)
    except Exception as e:
        print(f"  [ERROR] LLM decision failed: {e}")
        return {}

    if not isinstance(decisions, list):
        print(f"  [ERROR] Non-list decision received: {decisions}")
        return {}

    # Re-snapshot right before execution (LLM call takes time)
    try:
        exec_snap, _ = build_portfolio_snapshot(portfolio, quote_cache=quote_cache)
    except Exception as e:
        print(f"  [ERROR] Execution re-snapshot failed: {e}")
        return {}

    pv = exec_snap["totalNetLiquidationValue"]
    trade_records = []

    for decision in decisions:
        if not isinstance(decision, dict):
            continue

        position = str(decision.get("position", "")).strip().lower()
        if position not in {"long", "short", "none"}:
            print(f"  [ERROR] Invalid position {position!r}")
            continue

        raw_ticker = decision.get("ticker")
        reasoning = str(decision.get("reasoning", ""))

        try:
            leverage = max(1.0, min(10.0, float(decision.get("leverage") or 1.0)))
        except (ValueError, TypeError):
            leverage = 1.0

        try:
            cash_amount = max(0.0, float(decision.get("cash_amount") or 0.0))
        except (ValueError, TypeError):
            cash_amount = 0.0

        if cash_amount == 0.0:
            position = "flat"

        print(f"  | Decision    : {position.upper()} {str(raw_ticker):<8} | Margin: ${cash_amount:.2f} | Lev: {leverage}")
        print(f"  | Rationale   : {reasoning}")

        # Execution Quote
        if position == "none":
            continue  # nothing to do
        else:
            try:
                ticker = normalise_ticker(raw_ticker)
                q = get_quote(ticker, cache=quote_cache)
                price = float(q.get("c") or 0)
                if price <= 0:
                    raise ValueError(f"Zero or negative price for {ticker}")
            except Exception as e:
                print(f"  [ERROR] Quote fetch failed for {raw_ticker}: {e}")
                continue

        current_shares = portfolio["holdings"].get(ticker, 0)
        is_fractional = "/" in ticker or "-USD" in ticker

        # Target share count
        if price > 0 and position in ("long", "short"):
            target_notional = cash_amount * leverage
            raw_target = target_notional / price
            target_shares = (
                round(raw_target, 6) if is_fractional else int(raw_target)
            ) * (1 if position == "long" else -1)
        else:  # flat
            target_shares = 0

        # ── Proper margin model ───────────────────────────────────────────────
        # Step 1: Settle existing position — release margin + realize P&L
        existing_pos = portfolio.get("positions", {}).get(ticker)
        if existing_pos and current_shares != 0:
            ex_entry    = existing_pos.get("entryPrice", price)
            ex_margin   = existing_pos.get("marginUsed", 0)
            ex_notional = existing_pos.get("notional", 0)
            ex_dir      = 1 if existing_pos.get("direction") == "long" else -1
            realized    = ex_dir * ex_notional * ((price / ex_entry) - 1) if ex_entry > 0 else 0
            portfolio["cash"] += max(0.0, ex_margin + realized)
            portfolio["holdings"][ticker] = 0
            portfolio.get("positions", {}).pop(ticker, None)

        # Step 2: Open new position if not flat
        if target_shares != 0 and price > 0:
            new_notional = abs(target_shares) * price
            new_margin   = new_notional / leverage
            # Cap to free cash
            free_cash = float(portfolio["cash"])
            if new_margin > free_cash:
                affordable = free_cash * leverage
                target_shares = (
                    round(affordable / price, 6) if is_fractional else int(affordable / price)
                ) * (1 if position == "long" else -1)
                new_notional = abs(target_shares) * price
                new_margin   = new_notional / leverage
            portfolio["cash"] -= new_margin
            portfolio["holdings"][ticker] = target_shares
            portfolio.setdefault("positions", {})[ticker] = {
                "direction":  position,
                "shares":     target_shares,
                "entryPrice": round(price, 4),
                "leverage":   leverage,
                "notional":   round(new_notional, 2),
                "marginUsed": round(new_margin, 2),
            }
        elif position == "flat":
            portfolio["holdings"][ticker] = 0
            portfolio.get("positions", {}).pop(ticker, None)

        # Action / shares delta
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

        if position != "none":
            record = {
                "id": str(uuid.uuid4()),
                "botName": bot_id,
                "llmProvider": provider,
                "llmModel": model,
                "session": session_num,
                "trigger": f"session-{session}",
                "timestamp": ts,
                "ticker": ticker,
                "action": action,
                "position": position,
                "leverage": leverage,
                "shares": shares,
                "price": price,
                "reasoning": reasoning,
            }
            trade_records.append(record)

        # Display single trade
        lev_str = f"{leverage:.1g}x " if leverage > 1.0 else ""
        if action != "hold":
            sh_str = f"{shares:.4f}" if is_fractional else str(int(shares))
            print(f"  | Executed    : {lev_str}{position.upper()} {sh_str} sh of {ticker} @ ${price:.2f}")
        else:
            print(f"  | Executed    : HOLD (no position change)")

    # ── Final Cleanup ─────────────────────────────────────────────────────────
    # Ensure any positions with 0 shares are completely wiped from the database
    for tk in list(portfolio.get("positions", {}).keys()):
        if not portfolio.get("holdings", {}).get(tk):
            portfolio["positions"].pop(tk, None)

    # 5. NAV: free cash + equity in each open position (marginUsed + unrealizedPnL)
    total_val = float(portfolio["cash"])
    for pos_ticker, pos in portfolio.get("positions", {}).items():
        lq  = get_quote(pos_ticker, cache=quote_cache)
        lpx = float(lq.get("c") or 0)
        entry = pos.get("entryPrice", 0)
        margin = pos.get("marginUsed", 0)
        notional = pos.get("notional", 0)
        if lpx > 0 and entry > 0:
            pd = 1 if pos.get("direction") == "long" else -1
            total_val += margin + pd * notional * (lpx / entry - 1)
        else:
            total_val += margin

    portfolio["llmProvider"] = provider
    portfolio["llmModel"] = model
    portfolio["totalValue"] = total_val

    # Summary display
    free_cash   = float(portfolio["cash"])
    margin_used = sum(pos.get("marginUsed", 0) for pos in portfolio.get("positions", {}).values())
    if margin_used > 0:
        print(f"  | Margin Used : ${margin_used:>12,.2f}  |  Free Cash: ${free_cash:>12,.2f}")
    else:
        print(f"  | Cash        : ${free_cash:>12,.2f}")
    print(f"  | Post-NAV    : ${total_val:>12,.2f}")
    print(f"  +-----------------------------------------------------------")

    for record in trade_records:
        record["totalValue"] = round(total_val, 2)
        record["cash"] = round(float(portfolio["cash"]), 2)
        trade_log.append(record)

    return {}


# ---------------------------------------------------------------------------
# Daily Analysis Report Testing (mirrors analysis-report/function_app.py)
# ---------------------------------------------------------------------------

def test_daily_analysis_report(verbose: bool = False) -> dict:
    print(f"\n{'='*70}\n[TESTING DAILY MARKET ANALYSIS REPORT]\n{'='*70}")
    if not GEMINI_API_KEY:
        print("Error: GEMINI_API_KEY is required for analysis report generation.")
        return {}

    today_str = datetime.date.today().isoformat()
    print("  | Fetching live market intelligence via Tavily...")
    q1 = f"stock market breaking news macro drivers inflation fed earnings {today_str}"
    q2 = f"today market catalysts sector rotations top stock movers {today_str}"
    res1 = search_tavily(q1, verbose=verbose)
    res2 = search_tavily(q2, verbose=verbose)

    combined = []
    if res1 and not res1.startswith("Error") and "No results found" not in res1:
        combined.append("### Macro & Global Market Catalysts Today:\n" + res1)
    if res2 and not res2.startswith("Error") and "No results found" not in res2:
        combined.append("### Sector Movements & Breaking News:\n" + res2)
    live_news = "\n\n".join(combined) if combined else "(Live web search context unavailable)"

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
    print("  | Analyzing live news with Gemini 3.6 Flash...")
    try:
        raw = dispatch_llm("gemini", "gemini-3.6-flash", prompt, verbose=verbose)
    except Exception as e:
        print(f"  [ERROR] Gemini call failed: {e}")
        return {}

    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1].removeprefix("json").strip()

    try:
        report_data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            report_data = json.loads(m.group(0))
        else:
            raise ValueError(f"Could not parse analysis JSON: {text}")

    print(f"\n  Report Date     : {today_str}")
    print(f"  Overview        : {report_data.get('overview', '')}\n")

    raw_ideas = report_data.get("ideas", [])
    verified_ideas = []
    print(f"  Trade Ideas ({len(raw_ideas)} ideas generated):")
    for i, idea in enumerate(raw_ideas, 1):
        raw_ticker = idea.get("ticker") or idea.get("ticker_or_asset") or ""
        p_str = "–"
        price_at_analysis = None
        clean_ticker = normalise_ticker(raw_ticker)
        try:
            q = get_quote(clean_ticker)
            if q.get("c"):
                price_at_analysis = round(float(q["c"]), 4)
                dp = float(q.get("dp") or 0)
                p_str = f"${price_at_analysis:.2f} ({dp:+.2f}% vs prev close)"
        except Exception as e:
            p_str = f"(quote error: {e})"

        verified_ideas.append({
            "ticker_or_asset": clean_ticker,
            "company_or_asset_name": idea.get("company_or_asset_name", clean_ticker),
            "thesis": idea.get("thesis", ""),
            "detailed_report": idea.get("detailed_report", ""),
            "suggested_direction": idea.get("suggested_direction", "watch"),
            "price_context": p_str,
            "price_at_analysis": price_at_analysis,
        })

        dir_str = idea.get("suggested_direction", "watch").upper()
        print(f"    [{i}] {dir_str:<6} {clean_ticker:<10} | Price: {p_str}")
        print(f"        Asset : {idea.get('company_or_asset_name', clean_ticker)}")
        print(f"        Thesis: {idea.get('thesis', '')}")

    final_report = {
        "id": today_str,
        "reportDate": today_str,
        "overview": report_data.get("overview", ""),
        "ideas": verified_ideas,
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    out_file = STATE_DIR / f"analysis_report_{today_str}.json"
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(final_report, indent=2), encoding="utf-8")
    print(f"\n  [SUCCESS] Analysis report saved to {out_file}\n{'='*70}\n")
    return final_report


# ---------------------------------------------------------------------------
# CLI & Runners
# ---------------------------------------------------------------------------

def run_single_bot(bot_id: str, sessions: int, starting_cash: float, resume: bool, no_save: bool, verbose: bool, delay: float = 3.0):
    state_file = STATE_DIR / f"{bot_id}.json"
    if resume and state_file.exists():
        portfolio = json.loads(state_file.read_text(encoding="utf-8"))
        print(f"[backtest] Resuming {bot_id} from {state_file}")
    else:
        portfolio = {"id": bot_id, "botName": bot_id, "cash": starting_cash, "holdings": {}, "positions": {}, "startingCash": starting_cash}

    trade_log = []
    quote_cache = {}

    for i in range(1, sessions + 1):
        session = "open" if (i % 2 == 1) else "close"
        execute_session_step(
            bot_id=bot_id,
            session=session,
            portfolio=portfolio,
            trade_log=trade_log,
            session_num=i,
            verbose=verbose,
            quote_cache=quote_cache,
        )
        if i < sessions and delay > 0:
            time.sleep(delay)

    # Save state
    if not no_save:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(portfolio, indent=2), encoding="utf-8")
        log_file = STATE_DIR / f"{bot_id}_trade_log.json"
        log_file.write_text(json.dumps(trade_log, indent=2), encoding="utf-8")

    # Summary
    print_summary(bot_id, trade_log, starting_cash)


def run_multi_bot_competition(sessions: int, starting_cash: float, resume: bool, no_save: bool, verbose: bool, delay: float = 3.0):
    print(f"\n{'='*70}\n[MULTI-BOT COMPETITION BACKTEST - {len(BOT_CONFIGS)} BOTS - {sessions} SESSIONS]\n{'='*70}")
    portfolios = {}
    trade_logs = {b: [] for b in BOT_CONFIGS}

    for b in BOT_CONFIGS:
        sf = STATE_DIR / f"{b}.json"
        if resume and sf.exists():
            portfolios[b] = json.loads(sf.read_text(encoding="utf-8"))
        else:
            portfolios[b] = {"id": b, "botName": b, "cash": starting_cash, "holdings": {}, "positions": {}, "startingCash": starting_cash}

    quote_cache = {}
    for i in range(1, sessions + 1):
        session = "open" if (i % 2 == 1) else "close"
        print(f"\n==================== SESSION {i:02d} ({session.upper()}) ====================")
        for b in BOT_CONFIGS:
            execute_session_step(
                bot_id=b,
                session=session,
                portfolio=portfolios[b],
                trade_log=trade_logs[b],
                session_num=i,
                verbose=verbose,
                quote_cache=quote_cache,
            )
            if delay > 0:
                time.sleep(delay)


    # Save all
    if not no_save:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        for b in BOT_CONFIGS:
            (STATE_DIR / f"{b}.json").write_text(json.dumps(portfolios[b], indent=2), encoding="utf-8")
            (STATE_DIR / f"{b}_trade_log.json").write_text(json.dumps(trade_logs[b], indent=2), encoding="utf-8")

    # Leaderboard
    print(f"\n{'='*70}\n[FINAL LEADERBOARD & COMPARISON]\n{'='*70}")
    leaderboard = []
    for b, p in portfolios.items():
        nav = p.get("totalValue", starting_cash)
        pnl = nav - starting_cash
        pct = (pnl / starting_cash) * 100
        leaderboard.append({
            "bot": b,
            "name": BOT_CONFIGS[b]["name"],
            "model": BOT_CONFIGS[b]["llm_model"],
            "nav": nav,
            "pnl": pnl,
            "pct": pct,
            "trades": len([t for t in trade_logs[b] if t.get("action") != "hold"]),
        })

    leaderboard.sort(key=lambda x: x["nav"], reverse=True)
    print(f"  {'Rank':<5} {'Bot Name':<28} {'Model':<22} {'Trades':<7} {'NAV ($)':>12} {'Return':>10}")
    print(f"  {'-'*5} {'-'*28} {'-'*22} {'-'*7} {'-'*12} {'-'*10}")
    for rank, row in enumerate(leaderboard, 1):
        print(f"  {rank:<5} {row['name']:<28} {row['model']:<22} {row['trades']:<7} ${row['nav']:>11,.2f} {row['pct']:>+9.2f}%")
    print(f"{'='*70}\n")


def print_summary(bot_id: str, trade_log: list, starting_cash: float):
    cfg = BOT_CONFIGS[bot_id]
    print(f"\n{'='*70}\n  BACKTEST SUMMARY: {bot_id} ({cfg['name']})\n{'='*70}")
    if not trade_log:
        print("  No sessions executed.")
        return

    final_nav = trade_log[-1]["totalValue"]
    pnl = final_nav - starting_cash
    pct = (pnl / starting_cash) * 100
    print(f"  Total Sessions : {len(trade_log)}")
    print(f"  Starting Cash  : ${starting_cash:>12,.2f}")
    print(f"  Final NAV      : ${final_nav:>12,.2f}")
    print(f"  Total P&L      : ${pnl:>+12,.2f} ({pct:+.2f}%)")
    print()
    print(f"  {'#':>3}  {'Trigger':<13}  {'Pos':<6}  {'Lev':<4}  {'Ticker':<8}  {'Price':>8}  {'NAV':>12}  Reasoning")
    print(f"  {'─'*3}  {'─'*13}  {'─'*6}  {'─'*4}  {'─'*8}  {'─'*8}  {'─'*12}  {'─'*35}")
    for t in trade_log:
        rsn   = (t["reasoning"][:45] + "…") if len(t["reasoning"]) > 45 else t["reasoning"]
        pstr  = f"${t['price']:.2f}" if t.get("price", 0) > 0 else "–"
        pos   = t.get("position", t.get("action", "?")).upper()
        lev   = t.get("leverage", 1.0)
        tkstr = t.get("ticker", "–")
        levstr = f"{lev:.1g}x" if lev > 1.0 else "1x"
        print(f"  {t['session']:>3}  {t['trigger']:<13}  {pos:<6}  {levstr:<4}  {tkstr:<8}  {pstr:>8}  ${t['totalValue']:>11,.2f}  {rsn}")
    print(f"{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(description="AI Trading Bots Local Backtester")
    parser.add_argument("--bot", choices=list(BOT_CONFIGS.keys()) + ["all"], default="open_gemini",
                        help="Bot to test or 'all' for multi-bot competition (default: open_gemini)")
    parser.add_argument("--sessions", type=int, default=2, help="Number of market sessions to simulate (default: 2)")
    parser.add_argument("--cash", type=float, default=100000.0, help="Initial portfolio cash in USD (default: 100000)")
    parser.add_argument("--resume", action="store_true", help="Resume from last saved portfolio state")
    parser.add_argument("--no-save", action="store_true", help="Do not persist portfolio state to disk")
    parser.add_argument("--test-analysis", action="store_true", help="Test the Daily Analysis Report generator")
    parser.add_argument("--delay", type=float, default=3.0, help="Delay in seconds between bot LLM requests to avoid 429 rate limits (default: 3.0s)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output (shows raw prompts & responses)")
    args = parser.parse_args()

    if args.test_analysis:
        test_daily_analysis_report(verbose=args.verbose)
        return

    if args.bot == "all":
        run_multi_bot_competition(
            sessions=args.sessions,
            starting_cash=args.cash,
            resume=args.resume,
            no_save=args.no_save,
            verbose=args.verbose,
            delay=args.delay,
        )
    else:
        run_single_bot(
            bot_id=args.bot,
            sessions=args.sessions,
            starting_cash=args.cash,
            resume=args.resume,
            no_save=args.no_save,
            verbose=args.verbose,
            delay=args.delay,
        )


if __name__ == "__main__":
    main()
