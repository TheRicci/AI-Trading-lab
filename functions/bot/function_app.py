import azure.functions as func
import logging
import os
import json
import re
import uuid
import datetime
import requests
from azure.cosmos import CosmosClient
from azure.messaging.webpubsubservice import WebPubSubServiceClient

app = func.FunctionApp()

BOT_NAME = os.environ.get("BOT_NAME", "unnamed-bot")
BOT_STRATEGY_PROMPT = os.environ.get("BOT_STRATEGY_PROMPT", "You are a trading bot.")
# momentum | mean_reversion | event_driven -- selects which real indicator
# (if any) gets computed and handed to the model. See get_technical_context().
BOT_STRATEGY_KEY = os.environ.get("BOT_STRATEGY_KEY", "event_driven")
BOT_TICKERS = os.environ.get("BOT_TICKERS", "AAPL").split(",")
BOT_STARTING_CASH = float(os.environ.get("BOT_STARTING_CASH", "100000"))
BOT_LLM_PROVIDER = os.environ.get("BOT_LLM_PROVIDER", "gemini")  # gemini | groq
BOT_LLM_MODEL = os.environ.get("BOT_LLM_MODEL", "gemini-2.0-flash")

FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

COSMOS_ENDPOINT = os.environ.get("COSMOS_ENDPOINT")
COSMOS_KEY = os.environ.get("COSMOS_KEY")
WEBPUBSUB_CONNECTION_STRING = os.environ.get("WEBPUBSUB_CONNECTION_STRING")

# Shorter than the classic textbook defaults (SMA-50, RSI-14) on purpose.
# Finnhub's free tier doesn't give historical candles for US stocks
# anymore (moved to paid tiers), so this project builds its OWN price
# history one real observation at a time, at the pace bots actually
# trade -- one new daily close per trading day. A 50-day SMA would take
# ~2.5 months to ever produce a value; these shorter periods are still
# real, legitimately-used variants (short-period SMA/RSI are genuinely
# used by more active traders), just chosen to become useful in weeks
# rather than months. Until enough history exists, get_technical_context
# falls back to same-day proxies -- see the README for the honest
# ramp-up timeline.
SMA_PERIOD = 10
RSI_PERIOD = 7

cosmos_client = CosmosClient(COSMOS_ENDPOINT, COSMOS_KEY)
database = cosmos_client.get_database_client("TradingBots")
portfolios = database.get_container_client("Portfolios")
trades = database.get_container_client("Trades")
history = database.get_container_client("PortfolioHistory")
digests = database.get_container_client("Digests")
analyses = database.get_container_client("Analyses")
price_history = database.get_container_client("PriceHistory")

webpubsub_client = WebPubSubServiceClient.from_connection_string(
    WEBPUBSUB_CONNECTION_STRING, hub="tradingbots"
)


@app.event_grid_trigger(arg_name="event")
def on_event(event: func.EventGridEvent) -> None:
    # Only session/open and session/close reach here -- news doesn't
    # publish to Event Grid at all, see the project README.
    data = event.get_json()
    if event.event_type == "Trading.SessionReview":
        session = data.get("session")  # "open" | "close"
        for ticker in BOT_TICKERS:
            decide_and_trade(ticker, session)


# ---------- LLM provider dispatch ----------
# Two genuinely free, standing (not one-time-trial) integrations. GitHub
# Models was dropped from the active roster (see variables.tf) but
# call_openai_compatible stays generic -- it's what Groq uses too, and
# it's a two-line change to point a bot back at a different
# OpenAI-compatible provider later if you want a third brain again.

def call_gemini(prompt: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{BOT_LLM_MODEL}:generateContent?key={GEMINI_API_KEY}"
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    response = requests.post(url, json=body, timeout=20)
    response.raise_for_status()
    return response.json()["candidates"][0]["content"]["parts"][0]["text"]


def call_openai_compatible(base_url: str, api_key: str, prompt: str) -> str:
    url = f"{base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": BOT_LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
    }
    response = requests.post(url, headers=headers, json=body, timeout=20)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def call_llm(prompt: str) -> str:
    if BOT_LLM_PROVIDER == "gemini":
        return call_gemini(prompt)
    elif BOT_LLM_PROVIDER == "groq":
        return call_openai_compatible("https://api.groq.com/openai/v1", GROQ_API_KEY, prompt)
    else:
        raise ValueError(f"Unknown BOT_LLM_PROVIDER: {BOT_LLM_PROVIDER}")


# ---------- Portfolio + market data ----------

def get_portfolio() -> dict:
    try:
        return portfolios.read_item(item=BOT_NAME, partition_key=BOT_NAME)
    except Exception:
        portfolio = {"id": BOT_NAME, "botName": BOT_NAME, "cash": BOT_STARTING_CASH, "holdings": {}}
        portfolios.upsert_item(portfolio)
        return portfolio


def get_quote(ticker: str) -> dict:
    # c=current, d=change, dp=percent change, h/l=today's high/low, pc=previous close
    url = "https://finnhub.io/api/v1/quote"
    response = requests.get(url, params={"symbol": ticker, "token": FINNHUB_API_KEY}, timeout=10)
    response.raise_for_status()
    return response.json()


def compute_total_value(portfolio: dict) -> float:
    total = portfolio["cash"]
    for ticker, qty in portfolio.get("holdings", {}).items():
        if qty != 0:
            try:
                total += qty * get_quote(ticker).get("c", 0)
            except requests.RequestException as e:
                logging.warning(f"Price lookup failed for {ticker} during valuation: {e}")
    return total


# ---------- Real price history + technical indicators ----------

def record_price_point(ticker: str, session: str, quote: dict) -> None:
    # Natural-key upsert, same trick as the news watcher's dedup: the id
    # is deterministic (ticker-date-session), so multiple bots trading
    # the same ticker on the same session just harmlessly overwrite the
    # same document with the same real price, instead of creating
    # duplicates. This is what accumulates into real multi-day history.
    today = datetime.date.today().isoformat()
    try:
        price_history.upsert_item({
            "id": f"{ticker}-{today}-{session}",
            "ticker": ticker,
            "date": today,
            "session": session,
            "price": quote.get("c", 0),
            "high": quote.get("h", 0),
            "low": quote.get("l", 0),
            "previousClose": quote.get("pc", 0),
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        })
    except Exception as e:
        logging.warning(f"Failed to record price point for {ticker}: {e}")


def get_close_series(ticker: str, max_days: int = 60) -> list:
    # Oldest to newest. Uses only "close" session snapshots as the daily
    # observation -- the standard convention for a daily price series.
    query = (
        "SELECT * FROM c WHERE c.ticker = @ticker AND c.session = 'close' "
        "ORDER BY c.date DESC OFFSET 0 LIMIT @n"
    )
    params = [{"name": "@ticker", "value": ticker}, {"name": "@n", "value": max_days}]
    items = list(price_history.query_items(query=query, parameters=params, partition_key=ticker))
    return [item["price"] for item in reversed(items)]


def compute_sma(prices: list, period: int):
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


def compute_rsi(prices: list, period: int):
    # Simple-average RSI (not Wilder's smoothed version) -- a real,
    # commonly-taught simplification, noted here for transparency.
    if len(prices) < period + 1:
        return None
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    recent = deltas[-period:]
    avg_gain = sum(d for d in recent if d > 0) / period
    avg_loss = sum(-d for d in recent if d < 0) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def get_technical_context(ticker: str, quote: dict) -> str:
    closes = get_close_series(ticker)

    if BOT_STRATEGY_KEY == "momentum":
        sma = compute_sma(closes, SMA_PERIOD)
        if sma is not None:
            direction = "above" if quote.get("c", 0) > sma else "below"
            return f"{SMA_PERIOD}-day SMA: ${sma:.2f}. Current price ${quote.get('c', 0):.2f} is {direction} the {SMA_PERIOD}-day average."
        return (
            f"(Not enough price history yet for a {SMA_PERIOD}-day average -- "
            f"have {len(closes)} daily closes, need {SMA_PERIOD}. Using today's move "
            f"as an interim proxy instead: {quote.get('dp', 0):+.2f}% vs previous close.)"
        )

    if BOT_STRATEGY_KEY == "mean_reversion":
        rsi = compute_rsi(closes, RSI_PERIOD)
        if rsi is not None:
            return f"{RSI_PERIOD}-period RSI: {rsi:.1f} (below 30 = oversold, above 70 = overbought)."
        rng = quote.get("h", 0) - quote.get("l", 0)
        position = ((quote.get("c", 0) - quote.get("l", 0)) / rng) if rng > 0 else 0.5
        return (
            f"(Not enough price history yet for RSI -- have {len(closes)} daily closes, "
            f"need {RSI_PERIOD + 1}. Using today's range position as an interim proxy instead: "
            f"price is at {position * 100:.0f}% of today's low-high range, "
            f"where 0%=at the low, 100%=at the high.)"
        )

    return "(This strategy doesn't use technical indicators -- weigh the news digest and analysis context instead.)"


# ---------- Bot-facing synthesis + optional public daily analysis ----------

def get_digest(ticker: str) -> str:
    try:
        query = "SELECT * FROM c WHERE c.ticker = @ticker ORDER BY c.timestamp DESC OFFSET 0 LIMIT 1"
        params = [{"name": "@ticker", "value": ticker}]
        items = list(digests.query_items(query=query, parameters=params, partition_key=ticker))
        if not items:
            return "(no digest available yet today for this ticker)"
        d = items[0]
        return f"{d.get('digest', '')} (price at digest time: {d.get('priceSummary', '')})"
    except Exception as e:
        logging.warning(f"Digest lookup failed for {ticker}: {e}")
        return "(digest unavailable)"


def get_analysis_context(ticker: str) -> str:
    try:
        today = datetime.date.today().isoformat()
        analysis = analyses.read_item(item=today, partition_key=today)
        for idea in analysis.get("ideas", []):
            if ticker.upper() in idea.get("ticker_or_asset", "").upper():
                return (
                    f"Today's daily analysis flagged this ticker: {idea.get('thesis', '')} "
                    f"(suggested direction: {idea.get('suggested_direction', '')})"
                )
        return "(today's analysis didn't flag this ticker)"
    except Exception:
        return "(no analysis available today)"


# ---------- Decision + execution ----------

def ask_llm_for_decision(ticker: str, quote: dict, digest: str, analysis_context: str,
                          technical_context: str, portfolio: dict, session: str) -> dict:
    shares_held = portfolio["holdings"].get(ticker, 0)
    
    # Calculate net portfolio value to show the bot its total assets
    portfolio_value = portfolio["cash"]
    for t, q in portfolio.get("holdings", {}).items():
        if q != 0:
            if t == ticker:
                portfolio_value += q * quote.get("c", 0)
            else:
                try:
                    portfolio_value += q * get_quote(t).get("c", 0)
                except Exception:
                    pass

    prompt = f"""{BOT_STRATEGY_PROMPT}

Ticker: {ticker}
Current price: {quote.get('c', 0)}
Shares you currently hold: {shares_held} (negative indicates a short position)
Cash available: ${portfolio['cash']:.2f} (negative indicates borrowed money for leverage)
Total Portfolio Net Value: ${portfolio_value:.2f}
Trigger: market {session} (a scheduled twice-daily review)

Technical context for your strategy:
{technical_context}

Today's synthesized news digest for this ticker:
{digest}

Global market context (a separate, broader daily analysis you may or may not find relevant):
{analysis_context}

Weigh the above however your strategy dictates. 
You can decide to go "long" (buy/hold), "short" (sell short), or "flat" (no position).
You can also choose a leverage multiplier from 1.0 (standard spot trade) up to 3.0 (leveraged margin trading). If you choose "flat", leverage is ignored.

Respond with ONLY a JSON object, no other text, in exactly this shape:
{{"position": "long" | "short" | "flat", "leverage": <float between 1.0 and 3.0>, "reasoning": "<one or two sentences>"}}
"""
    text = call_llm(prompt).strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text.removeprefix("json").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


def decide_and_trade(ticker: str, session: str) -> None:
    portfolio = get_portfolio()

    try:
        quote = get_quote(ticker)
    except requests.RequestException as e:
        logging.error(f"[{BOT_NAME}] Price lookup failed for {ticker}, skipping this review: {e}")
        return

    record_price_point(ticker, session, quote)

    digest = get_digest(ticker)
    analysis_context = get_analysis_context(ticker)
    technical_context = get_technical_context(ticker, quote)

    try:
        decision = ask_llm_for_decision(ticker, quote, digest, analysis_context, technical_context, portfolio, session)
    except Exception as e:
        logging.error(f"[{BOT_NAME}] LLM call failed for {ticker}: {e}")
        return

    position = decision.get("position", "flat").lower()
    try:
        leverage = float(decision.get("leverage", 1.0) or 1.0)
    except (ValueError, TypeError):
        leverage = 1.0
    leverage = max(1.0, min(3.0, leverage))
    reasoning = decision.get("reasoning", "")
    price = quote.get("c", 0)

    portfolio_value = compute_total_value(portfolio)
    current_shares = portfolio["holdings"].get(ticker, 0)

    if price > 0:
        if position == "long":
            target_shares = int((portfolio_value * leverage) / price)
        elif position == "short":
            target_shares = int(-(portfolio_value * leverage) / price)
        else: # flat
            target_shares = 0
    else:
        target_shares = current_shares

    shares_to_trade = target_shares - current_shares

    if shares_to_trade > 0:
        action = "buy"
        shares = shares_to_trade
        portfolio["cash"] -= shares * price
        portfolio["holdings"][ticker] = target_shares
    elif shares_to_trade < 0:
        action = "sell"
        shares = abs(shares_to_trade)
        portfolio["cash"] += shares * price
        portfolio["holdings"][ticker] = target_shares
    else:
        action = "hold"
        shares = 0

    total_value = compute_total_value(portfolio)

    portfolio["strategyPrompt"] = BOT_STRATEGY_PROMPT
    portfolio["strategyKey"] = BOT_STRATEGY_KEY
    portfolio["llmProvider"] = BOT_LLM_PROVIDER
    portfolio["llmModel"] = BOT_LLM_MODEL
    portfolio["totalValue"] = total_value
    portfolios.upsert_item(portfolio)

    trade_record = {
        "id": str(uuid.uuid4()),
        "botName": BOT_NAME,
        "llmProvider": BOT_LLM_PROVIDER,
        "ticker": ticker,
        "action": action,
        "shares": shares,
        "price": price,
        "reasoning": reasoning,
        "trigger": f"session-{session}",
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    }
    trades.create_item(trade_record)

    history.create_item({
        "id": str(uuid.uuid4()),
        "botName": BOT_NAME,
        "cash": portfolio["cash"],
        "holdings": portfolio["holdings"],
        "totalValue": total_value,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    })

    try:
        webpubsub_client.send_to_all(
            message=json.dumps({"type": "trade", "totalValue": total_value, **trade_record}),
            content_type="application/json",
        )
    except Exception as e:
        logging.warning(f"[{BOT_NAME}] Failed to broadcast trade via Web PubSub: {e}")

    logging.info(f"[{BOT_NAME}/{BOT_LLM_PROVIDER}] {action} {shares} {ticker} @ {price} -- {reasoning} (Target position: {position} {leverage}x)")
