import azure.functions as func
import logging
import os
import json
import datetime
from azure.cosmos import CosmosClient
from azure.messaging.webpubsubservice import WebPubSubServiceClient

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

COSMOS_ENDPOINT = os.environ.get("COSMOS_ENDPOINT")
COSMOS_KEY = os.environ.get("COSMOS_KEY")
WEBPUBSUB_CONNECTION_STRING = os.environ.get("WEBPUBSUB_CONNECTION_STRING")

cosmos_client = CosmosClient(COSMOS_ENDPOINT, COSMOS_KEY)
database = cosmos_client.get_database_client("TradingBots")
portfolios = database.get_container_client("Portfolios")
trades = database.get_container_client("Trades")
history = database.get_container_client("PortfolioHistory")
analyses = database.get_container_client("Analyses")
# Deliberately NOT exposing Digests or NewsItems here -- those are
# bot-facing only, per the project's design. Only Analyses is meant to
# be public.

webpubsub_client = WebPubSubServiceClient.from_connection_string(
    WEBPUBSUB_CONNECTION_STRING, hub="tradingbots"
)


@app.route(route="leaderboard", methods=["GET"])
def leaderboard(req: func.HttpRequest) -> func.HttpResponse:
    items = list(portfolios.read_all_items())
    result = [{
        "botName": p["botName"],
        "cash": p["cash"],
        "holdings": p.get("holdings", {}),
        # Falls back to cash for a bot that exists but hasn't made its
        # first decision yet -- holdings would be empty at that point
        # too, so cash alone is the correct total, not a placeholder.
        "totalValue": p.get("totalValue", p["cash"]),
        "strategyPrompt": p.get("strategyPrompt", ""),
        "llmProvider": p.get("llmProvider", "unknown"),
        "llmModel": p.get("llmModel", "unknown"),
    } for p in items]
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
        result = analyses.read_item(item=date_param, partition_key=date_param)
        return func.HttpResponse(json.dumps(result), mimetype="application/json")
    except Exception:
        return func.HttpResponse(
            json.dumps({"reportDate": date_param, "overview": "", "ideas": [], "notFound": True}),
            mimetype="application/json",
            status_code=404,
        )


@app.route(route="negotiate", methods=["GET"])
def negotiate(req: func.HttpRequest) -> func.HttpResponse:
    token = webpubsub_client.get_client_access_token()
    return func.HttpResponse(json.dumps({"url": token["url"]}), mimetype="application/json")
