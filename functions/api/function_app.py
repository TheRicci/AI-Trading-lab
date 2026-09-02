import azure.functions as func
import logging
import os
import json
import datetime
import re
import time
import requests
from azure.cosmos import CosmosClient
from azure.messaging.webpubsubservice import WebPubSubServiceClient

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

COSMOS_ENDPOINT = os.environ.get("COSMOS_ENDPOINT")
COSMOS_KEY = os.environ.get("COSMOS_KEY")
WEBPUBSUB_CONNECTION_STRING = os.environ.get("WEBPUBSUB_CONNECTION_STRING")
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY")

cosmos_client = CosmosClient(COSMOS_ENDPOINT, COSMOS_KEY)
database = cosmos_client.get_database_client("TradingBots")
trades = database.get_container_client("Trades")
history = database.get_container_client("PortfolioHistory")
analyses = database.get_container_client("Analyses")
# Deliberately exposing only Analyses here. The bots' internal inputs
# stay private.

webpubsub_client = WebPubSubServiceClient.from_connection_string(
    WEBPUBSUB_CONNECTION_STRING, hub="tradingbots"
)


def fetch_live_price(ticker: str) -> float | None:
    """Fetch a live price for a single ticker (Finnhub → Yahoo Finance fallback)."""
    clean = ticker.upper().strip()
    # Crypto via Coinbase
    try:
        base = clean.replace("/USD", "").replace("-USD", "").replace("USD", "")
        r = requests.get(f"https://api.coinbase.com/v2/prices/{base}-USD/spot", timeout=5)
        if r.ok:
            price = float(r.json().get("data", {}).get("amount") or 0)
            if price > 0:
                return price
    except Exception:
        pass
    # Finnhub for equities
    if FINNHUB_API_KEY:
        try:
            r = requests.get("https://finnhub.io/api/v1/quote",
                             params={"symbol": clean, "token": FINNHUB_API_KEY}, timeout=5)
            if r.ok:
                price = float(r.json().get("c") or 0)
                if price > 0:
                    return price
        except Exception:
            pass
    # Yahoo Finance fallback
    try:
        ticker_yf = f"{clean}.SA" if re.fullmatch(r"[A-Z]{4}[0-9]{1,2}", clean) else clean
        r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker_yf}",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
        if r.ok:
            meta = r.json().get("chart", {}).get("result", [{}])[0].get("meta", {})
            price = float(meta.get("regularMarketPrice") or 0)
            if price > 0:
                return price
    except Exception:
        pass
    return None


def _latest_portfolio_snapshots() -> list:
    """Return the newest portfolio snapshot for every known bot.

    Current state is just the latest row per bot in the single history
    container -- there is no separate "current portfolio" table to keep in
    sync with the snapshots anymore.
    """
    names_query = "SELECT DISTINCT VALUE c.botName FROM c"
    names = list(history.query_items(query=names_query, enable_cross_partition_query=True))

    snapshots = []
    for name in names:
        query = (
            "SELECT * FROM c WHERE c.botName = @name "
            "ORDER BY c.timestamp DESC OFFSET 0 LIMIT 1"
        )
        params = [{"name": "@name", "value": name}]
        found = list(history.query_items(query=query, parameters=params, partition_key=name))
        if found:
            snapshots.append(found[0])
    return snapshots


@app.route(route="leaderboard", methods=["GET"])
def leaderboard(req: func.HttpRequest) -> func.HttpResponse:
    items = _latest_portfolio_snapshots()
    result = []
    for p in items:
        starting_cash = p.get("startingCash", 100_000)
        raw_cash = p.get("cash", starting_cash)

        # In the new margin model, cash is always >= 0 (it's free, undeployed capital).
        # Support old docs that still have negative cash by clamping.
        free_cash   = max(0.0, raw_cash)
        # Margin used = sum of per-position margins (accurate in new model),
        # or fall back to abs(negative cash) for legacy portfolios.
        positions_data = p.get("positions", {})
        margin_used = sum(pos.get("marginUsed", 0) for pos in positions_data.values()) \
                      if positions_data else abs(min(0.0, raw_cash))

        # Enrich positions with live price and unrealized P&L.
        # NAV contribution per position = marginUsed + unrealizedPnL.
        enriched_positions = {}
        positions_equity = 0.0
        for ticker, pos in positions_data.items():
            # Skip positions that aren't actually open. Require BOTH a non-zero
            # share count AND deployed margin. Using 'and' (old code) allowed
            # stale positions with marginUsed > 0 but shares = 0 to slip through.
            if not pos.get("shares") or not pos.get("marginUsed"):
                continue
            entry_price = pos.get("entryPrice", 0)
            pos_margin  = pos.get("marginUsed", 0)
            notional    = pos.get("notional", 0)
            direction   = 1 if pos.get("direction") == "long" else -1
            live_price  = fetch_live_price(ticker) if entry_price > 0 else None
            if live_price and entry_price > 0:
                unreal_pnl = direction * notional * (live_price / entry_price - 1)
                unreal_pct = (unreal_pnl / starting_cash) * 100
                positions_equity += pos_margin + unreal_pnl
            else:
                unreal_pnl = None
                unreal_pct = None
                positions_equity += pos_margin  # assume breakeven if no live price
            enriched_positions[ticker] = {
                **pos,
                "currentPrice":  round(live_price, 4) if live_price else None,
                "unrealizedPnl": round(unreal_pnl, 2) if unreal_pnl is not None else None,
                "unrealizedPct": round(unreal_pct, 2) if unreal_pct is not None else None,
            }

        # Live NAV = free cash + equity locked in positions
        live_total_value = free_cash + positions_equity

        result.append({
            "botName":         p["botName"],
            "cash":            raw_cash,
            "availableMargin": free_cash,
            "marginUsed":      round(margin_used, 2),
            "positions":       enriched_positions,
            "totalValue":      round(live_total_value, 2),
            "startingCash":    starting_cash,
            "strategyPrompt":  p.get("strategyPrompt", ""),
            "llmProvider":     p.get("llmProvider", "unknown"),
            "llmModel":        p.get("llmModel", "unknown"),
        })
    return func.HttpResponse(json.dumps(result), mimetype="application/json")




@app.route(route="trades", methods=["GET"])
def recent_trades(req: func.HttpRequest) -> func.HttpResponse:
    bot = req.params.get("bot")
    if bot:
        query = "SELECT * FROM c WHERE c.botName = @bot ORDER BY c.timestamp DESC OFFSET 0 LIMIT 50"
        params = [{"name": "@bot", "value": bot}]
    else:
        query = "SELECT * FROM c ORDER BY c.timestamp DESC OFFSET 0 LIMIT 50"
        params = []
    items = list(trades.query_items(query=query, parameters=params, enable_cross_partition_query=True))
    return func.HttpResponse(json.dumps(items), mimetype="application/json")


@app.route(route="portfolio-history", methods=["GET"])
def portfolio_history(req: func.HttpRequest) -> func.HttpResponse:
    # Returns every bot's history in one call, grouped by bot name, so the
    # frontend can plot them as separate lines on one comparison chart.
    query = "SELECT c.botName, c.totalValue, c.timestamp FROM c ORDER BY c.timestamp ASC"
    items = list(history.query_items(query=query, enable_cross_partition_query=True))

    grouped: dict = {}
    for item in items:
        grouped.setdefault(item["botName"], []).append({
            "timestamp": item["timestamp"],
            "totalValue": item["totalValue"],
        })
    return func.HttpResponse(json.dumps(grouped), mimetype="application/json")


@app.route(route="analysis", methods=["GET"])
def analysis(req: func.HttpRequest) -> func.HttpResponse:
    date_param = req.params.get("date") or datetime.date.today().isoformat()
    try:
        # Query by reportDate to get all models for this date
        query = "SELECT * FROM c WHERE c.reportDate = @date"
        params = [{"name": "@date", "value": date_param}]
        items = list(analyses.query_items(query=query, parameters=params, enable_cross_partition_query=True))
        
        if not items:
            return func.HttpResponse(
                json.dumps({"reportDate": date_param, "overview": "", "ideas": [], "notFound": True}),
                mimetype="application/json",
                status_code=404,
            )
            
        # Group them into a dictionary by provider
        result = {}
        for item in items:
            provider = item.get("provider") or item.get("model", "unknown")
            result[provider] = item
            
        return func.HttpResponse(json.dumps(result), mimetype="application/json")
    except Exception as e:
        logging.error(f"Error reading analysis for {date_param}: {e}")
        return func.HttpResponse(
            json.dumps({"reportDate": date_param, "overview": "", "ideas": [], "notFound": True}),
            mimetype="application/json",
            status_code=404,
        )


@app.route(route="analysis/dates", methods=["GET"])
def analysis_dates(req: func.HttpRequest) -> func.HttpResponse:
    query = "SELECT DISTINCT VALUE c.reportDate FROM c ORDER BY c.reportDate DESC"
    dates = list(analyses.query_items(query=query, enable_cross_partition_query=True))
    return func.HttpResponse(json.dumps(dates), mimetype="application/json")


@app.route(route="analysis/outcomes", methods=["GET"])
def analysis_outcomes(req: func.HttpRequest) -> func.HttpResponse:
    """Return post-publication price movement for every valid ticker in a report.

    Price history is deliberately fetched at read time. That means a report's
    original thesis remains immutable while its scorecard keeps improving as
    more sessions pass, without a second scheduled write pipeline.
    """
    date_param = req.params.get("date") or datetime.date.today().isoformat()
    try:
        query = "SELECT * FROM c WHERE c.reportDate = @date"
        params = [{"name": "@date", "value": date_param}]
        items = list(analyses.query_items(query=query, parameters=params, enable_cross_partition_query=True))
        
        if not items:
            return func.HttpResponse(json.dumps([]), mimetype="application/json", status_code=404)
    except Exception:
        return func.HttpResponse(json.dumps([]), mimetype="application/json", status_code=404)

    outcomes = []
    seen_tickers = set()
    for report in items:
        for idea in report.get("ideas", []):
            ticker = str(idea.get("ticker_or_asset", "")).upper().strip()
            if ticker in seen_tickers:
                continue
            seen_tickers.add(ticker)
            if not re.fullmatch(r"[A-Z0-9]{1,6}(?:\.[A-Z0-9]+)?", ticker):
                outcomes.append({"ticker_or_asset": ticker, "history": []})
                continue
            outcomes.append(build_outcome(ticker, date_param, idea.get("price_at_analysis")))
    return func.HttpResponse(json.dumps(outcomes), mimetype="application/json")


def fetch_candle_history(ticker: str, start: datetime.datetime, end: datetime.datetime) -> list:
    """Return [{date, price}] candle history. Tries Finnhub first, then Yahoo
    Finance, then Coinbase/Binance for crypto pairs — so assets that aren't on
    Finnhub (crypto, B3, forex, many global listings) still get chart history.
    """
    history = []

    # 1. Finnhub candles (US / most listed equities)
    try:
        response = requests.get("https://finnhub.io/api/v1/stock/candle", params={
            "symbol": ticker,
            "resolution": "60",
            "from": int(start.timestamp()),
            "to": int(end.timestamp()),
            "token": FINNHUB_API_KEY,
        }, timeout=8)
        candle = response.json()
        if response.ok and candle.get("s") == "ok" and candle.get("c"):
            history = [
                {"date": datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M"), "price": close}
                for ts, close in zip(candle.get("t", []), candle.get("c", []))
            ]
            if history:
                return history
    except requests.RequestException:
        pass

    # 2. Crypto pairs via Coinbase spot candles, then Binance klines
    is_crypto = "/" in ticker or "-USD" in ticker or ticker.endswith("USD")
    if is_crypto:
        base = ticker.split("/")[0].split("-")[0]
        try:
            # Coinbase candlestick endpoint (granularity=86400 = 1 day)
            url = f"https://api.exchange.coinbase.com/products/{base}-USD/candles?granularity=86400"
            r = requests.get(url, timeout=8)
            if r.ok:
                candles = r.json()
                if isinstance(candles, list) and candles:
                    history = [
                        {"date": datetime.datetime.fromtimestamp(int(c[0]), datetime.timezone.utc).date().isoformat(),
                         "price": float(c[4])}
                        for c in candles
                    ]
                    history.sort(key=lambda p: p["date"])
                    # filter to the requested window
                    start_date = start.date().isoformat()
                    history = [p for p in history if p["date"] >= start_date]
                    if history:
                        return history
        except requests.RequestException:
            pass
        try:
            url = f"https://api.binance.com/api/v3/klines?symbol={base}USDT&interval=1d&startTime={int(start.timestamp()*1000)}&endTime={int(end.timestamp()*1000)}&limit=500"
            r = requests.get(url, timeout=8)
            if r.ok:
                klines = r.json()
                if isinstance(klines, list) and klines:
                    history = [
                        {"date": datetime.datetime.fromtimestamp(int(k[0]) / 1000, datetime.timezone.utc).date().isoformat(),
                         "price": float(k[4])}
                        for k in klines
                    ]
                    history.sort(key=lambda p: p["date"])
                    if history:
                        return history
        except requests.RequestException:
            pass

    # 3. Yahoo Finance chart endpoint (B3 .SA, forex =X, global, and most US)
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        if r.ok:
            data = r.json()
            result = (data.get("chart") or {}).get("result") or []
            if result:
                ts_list = result[0].get("timestamp", [])
                close_list = (result[0].get("indicators") or {}).get("quote", [{}])[0].get("close", [])
                if ts_list and close_list:
                    # Fall back to the daily chart (query range covers window)
                    assert len(ts_list) == len(close_list)
                    filtered = [
                        (ts, close)
                        for ts, close in zip(ts_list, close_list)
                        if close is not None and ts >= int(start.timestamp())
                    ]
                    # Yahoo timestamps can be seconds; unix seconds
                    history = [
                        {"date": datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M"), "price": close}
                        for ts, close in filtered
                    ]
                    if history:
                        return history
    except requests.RequestException:
        pass

    return history


def build_outcome(ticker: str, report_date: str, entry_price) -> dict:
    start = datetime.datetime.fromisoformat(report_date).replace(tzinfo=datetime.timezone.utc)
    end = datetime.datetime.now(datetime.timezone.utc)

    history = fetch_candle_history(ticker, start, end)

    baseline = float(entry_price) if entry_price else (history[0]["price"] if history else None)
    current = history[-1]["price"] if history else None

    # Candle data may not include the in-progress current session, so try to add a
    # fresh quote as the final point when it differs from the last close.
    try:
        live_price = fetch_live_price(ticker)
        if live_price:
            current = live_price
            now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M")
            if not history or history[-1]["date"] != now_str:
                history.append({"date": now_str, "price": current})
    except Exception:
        pass

    return {
        "ticker_or_asset": ticker,
        "initialPrice": baseline,
        "currentPrice": current,
        "returnPct": ((current / baseline) - 1) * 100 if baseline and current else None,
        "history": history,
        "asOf": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


@app.route(route="negotiate", methods=["GET"])
def negotiate(req: func.HttpRequest) -> func.HttpResponse:
    token = webpubsub_client.get_client_access_token()
    return func.HttpResponse(json.dumps({"url": token["url"]}), mimetype="application/json")
