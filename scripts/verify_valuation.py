#!/usr/bin/env python3
"""
verify_valuation.py - Portfolio NAV & P&L Audit Verifier

Audits Cosmos DB PortfolioHistory documents against the strict margin valuation model:
  NAV = freeCash + sum(marginUsed + unrealizedPnL)
  where unrealizedPnL = direction * notional * (markPrice / entryPrice - 1)

Cloud & Local Modes:
  1. Fetch live from Azure Cosmos DB:
     python scripts/verify_valuation.py --live --bot open_gemini --limit 5

  2. Fetch live from deployed API endpoint:
     python scripts/verify_valuation.py --api https://<your-func-app>.azurewebsites.net

  3. Audit from local JSON file or string:
     python scripts/verify_valuation.py --file snapshot.json
     python scripts/verify_valuation.py --json '{"botName": ...}'
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_cosmos_credentials():
    """Discover Cosmos DB endpoint and key from env, local settings, or Azure CLI."""
    endpoint = os.environ.get("COSMOS_ENDPOINT", "").strip()
    key = os.environ.get("COSMOS_KEY", "").strip()

    # 1. Try local.settings.json files if env vars are unset
    for settings_path in [
        REPO_ROOT / "functions" / "bot" / "local.settings.json",
        REPO_ROOT / "functions" / "api" / "local.settings.json",
    ]:
        if (not endpoint or not key) and settings_path.exists():
            try:
                data = json.loads(settings_path.read_text(encoding="utf-8"))
                values = data.get("Values", {})
                if not endpoint and "COSMOS_ENDPOINT" in values:
                    endpoint = values["COSMOS_ENDPOINT"]
                if not key and "COSMOS_KEY" in values:
                    key = values["COSMOS_KEY"]
            except Exception:
                pass

    # 2. Try discovery via Azure CLI (`az`)
    if not endpoint or not key:
        try:
            cmd = ["az", "cosmosdb", "list", "--resource-group", "rg-ai-trading-bots", "--query", "[0].{name:name, endpoint:documentEndpoint}", "-o", "json"]
            out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=10)
            account_info = json.loads(out)
            if account_info and account_info.get("endpoint"):
                endpoint = account_info["endpoint"]
                acc_name = account_info.get("name")
                key_cmd = ["az", "cosmosdb", "keys", "list", "--name", acc_name, "--resource-group", "rg-ai-trading-bots", "--query", "primaryMasterKey", "-o", "tsv"]
                key_out = subprocess.check_output(key_cmd, stderr=subprocess.DEVNULL, timeout=10)
                key = key_out.decode().strip()
        except Exception:
            pass

    return endpoint, key


def fetch_from_cosmos(endpoint: str, key: str, bot_name: str | None = None, limit: int = 5) -> list:
    """Fetch recent PortfolioHistory snapshots directly from Cosmos DB."""
    try:
        from azure.cosmos import CosmosClient
    except ImportError:
        print("Error: 'azure-cosmos' package required for live Cosmos DB query.")
        print("Install with: pip install azure-cosmos")
        sys.exit(1)

    client = CosmosClient(endpoint, key)
    database = client.get_database_client("TradingBots")
    history = database.get_container_client("PortfolioHistory")

    docs = []
    if bot_name and bot_name != "all":
        query = (
            "SELECT * FROM c WHERE c.botName = @bot "
            "ORDER BY c.timestamp DESC OFFSET 0 LIMIT @lim"
        )
        params = [{"name": "@bot", "value": bot_name}, {"name": "@lim", "value": limit}]
        items = list(history.query_items(query=query, parameters=params, partition_key=bot_name))
        docs.extend(items)
    else:
        # Fetch for all bots
        names_query = "SELECT DISTINCT VALUE c.botName FROM c"
        names = list(history.query_items(query=names_query, enable_cross_partition_query=True))
        for name in names:
            query = (
                "SELECT * FROM c WHERE c.botName = @bot "
                "ORDER BY c.timestamp DESC OFFSET 0 LIMIT @lim"
            )
            params = [{"name": "@bot", "value": name}, {"name": "@lim", "value": limit}]
            items = list(history.query_items(query=query, parameters=params, partition_key=name))
            docs.extend(items)

    return docs


def fetch_from_api(base_url: str) -> list:
    """Fetch current leaderboard snapshots from deployed Azure API."""
    try:
        import requests
    except ImportError:
        print("Error: 'requests' package required. Install with: pip install requests")
        sys.exit(1)

    url = base_url.rstrip("/")
    if not url.endswith("/api/leaderboard"):
        url = f"{url}/api/leaderboard"

    print(f"Connecting to API: {url} ...")
    r = requests.get(url, timeout=15)
    if not r.ok:
        print(f"Error fetching from API: HTTP {r.status_code} - {r.text[:200]}")
        sys.exit(1)

    return r.json()


def audit_snapshot(doc: dict) -> dict:
    """Verifies all math inside a single PortfolioHistory snapshot document."""
    bot_name = doc.get("botName", "unknown")
    timestamp = doc.get("timestamp", "unknown")
    doc_total = float(doc.get("totalValue", 0))
    cash = float(doc.get("cash", 0))
    free_cash = max(0.0, cash)
    positions = doc.get("positions", {})

    calc_margin_used = 0.0
    calc_unrealized_pnl = 0.0
    position_audits = []

    for ticker, pos in positions.items():
        shares = float(pos.get("shares", 0))
        entry_price = float(pos.get("entryPrice", 0))
        mark_price = float(pos.get("markPrice") or pos.get("currentPrice") or entry_price)
        margin_used = float(pos.get("marginUsed", 0))
        notional = float(pos.get("notional") or (abs(shares) * entry_price))
        leverage = float(pos.get("leverage", 1.0))
        direction_val = 1 if pos.get("direction") == "long" or shares > 0 else -1

        # Expected margin = notional / leverage
        expected_margin = notional / leverage if leverage > 0 else notional

        # Expected unrealized PnL
        if entry_price > 0 and mark_price > 0:
            calc_unreal = direction_val * notional * (mark_price / entry_price - 1)
        else:
            calc_unreal = 0.0

        calc_equity = margin_used + calc_unreal

        calc_margin_used += margin_used
        calc_unrealized_pnl += calc_unreal

        doc_unreal = pos.get("unrealizedPnl")
        pnl_diff = round(calc_unreal - doc_unreal, 2) if doc_unreal is not None else 0.0

        position_audits.append({
            "ticker": ticker,
            "direction": pos.get("direction", "long"),
            "shares": shares,
            "entryPrice": entry_price,
            "markPrice": mark_price,
            "marginUsed": margin_used,
            "notional": notional,
            "leverage": leverage,
            "calcUnrealizedPnl": round(calc_unreal, 2),
            "docUnrealizedPnl": doc_unreal,
            "pnlDiscrepancy": pnl_diff,
            "calcEquity": round(calc_equity, 2),
        })

    calc_total = round(free_cash + calc_margin_used + calc_unrealized_pnl, 2)
    nav_discrepancy = round(doc_total - calc_total, 2)
    is_valid = abs(nav_discrepancy) <= 0.05

    return {
        "botName": bot_name,
        "timestamp": timestamp,
        "recordedTotalValue": doc_total,
        "calculatedTotalValue": calc_total,
        "freeCash": free_cash,
        "calculatedMarginUsed": round(calc_margin_used, 2),
        "calculatedUnrealizedPnl": round(calc_unrealized_pnl, 2),
        "dailyPnL": doc.get("dailyPnL"),
        "dailyPnLPct": doc.get("dailyPnLPct"),
        "sessionPnL": doc.get("sessionPnL"),
        "sessionPnLPct": doc.get("sessionPnLPct"),
        "totalRealizedPnlCumulative": doc.get("totalRealizedPnlCumulative"),
        "discrepancy": nav_discrepancy,
        "isValid": is_valid,
        "positions": position_audits,
    }


def print_audit_report(result: dict):
    print("=" * 72)
    print(f"AUDIT REPORT: {result['botName']} @ {result['timestamp']}")
    print("=" * 72)
    print(f"  Free Cash           : ${result['freeCash']:>12,.2f}")
    print(f"  Margin Deployed     : ${result['calculatedMarginUsed']:>12,.2f}")
    print(f"  Unrealized P&L      : ${result['calculatedUnrealizedPnl']:>12,.2f}")
    print(f"  Calculated NAV      : ${result['calculatedTotalValue']:>12,.2f}")
    print(f"  Recorded NAV in DB  : ${result['recordedTotalValue']:>12,.2f}")
    print(f"  Discrepancy         : ${result['discrepancy']:>12,.2f}")
    print(f"  Status              : {'PASSED [OK]' if result['isValid'] else 'FAILED [MISMATCH]'}")

    if result.get("dailyPnL") is not None:
        print(f"  Daily Change        : ${result['dailyPnL']:>12,.2f} ({result.get('dailyPnLPct', 0):+.2f}%)")
    if result.get("sessionPnL") is not None:
        print(f"  Session Change      : ${result['sessionPnL']:>12,.2f} ({result.get('sessionPnLPct', 0):+.2f}%)")
    if result.get("totalRealizedPnlCumulative") is not None:
        print(f"  Cumul. Realized PnL : ${result['totalRealizedPnlCumulative']:>12,.2f}")

    print("-" * 72)

    if result["positions"]:
        print("  Positions breakdown:")
        for p in result["positions"]:
            unreal_str = f"${p['calcUnrealizedPnl']:>8,.2f}"
            disc_str = f" (doc pnl diff: ${p['pnlDiscrepancy']:.2f})" if p["docUnrealizedPnl"] is not None else ""
            print(f"    {p['ticker']:<10} {p['direction'].upper():<5} {p['shares']:>8.2f} sh | Entry: ${p['entryPrice']:>8.2f} -> Mark: ${p['markPrice']:>8.2f} | Margin: ${p['marginUsed']:>8.2f} | Unreal: {unreal_str}{disc_str}")
    else:
        print("  (No open positions - all cash)")
    print("=" * 72)


def main():
    parser = argparse.ArgumentParser(description="Audit portfolio valuation math from Cosmos DB or local files.")
    parser.add_argument("--live", action="store_true", help="Connect directly to Azure Cosmos DB in the cloud")
    parser.add_argument("--endpoint", help="Cosmos DB endpoint URI (optional if env var or local settings set)")
    parser.add_argument("--key", help="Cosmos DB master key (optional if env var or local settings set)")
    parser.add_argument("--bot", default="all", help="Bot name to audit (default: all)")
    parser.add_argument("--limit", type=int, default=3, help="Number of recent snapshots to audit per bot (default: 3)")
    parser.add_argument("--api", help="Fetch live state from deployed API endpoint (e.g. https://<app>.azurewebsites.net)")
    parser.add_argument("--file", help="Path to local JSON file containing snapshot documents")
    parser.add_argument("--json", help="Raw JSON string containing snapshot document")
    args = parser.parse_args()

    docs = []

    # Mode 1: Live Cosmos DB query
    if args.live:
        endpoint = args.endpoint
        key = args.key
        if not endpoint or not key:
            endpoint, key = load_cosmos_credentials()

        if not endpoint or not key:
            print("Error: Could not find Cosmos DB credentials.")
            print("Please set COSMOS_ENDPOINT and COSMOS_KEY env vars, or pass --endpoint and --key.")
            sys.exit(1)

        print(f"Connecting to Cosmos DB: {endpoint} ...")
        docs = fetch_from_cosmos(endpoint, key, bot_name=args.bot, limit=args.limit)
        print(f"Retrieved {len(docs)} snapshot(s) from Cosmos DB.\n")

    # Mode 2: Live API query
    elif args.api:
        docs = fetch_from_api(args.api)

    # Mode 3: Local file
    elif args.file:
        path = Path(args.file)
        if not path.exists():
            print(f"Error: file {path} not found.")
            sys.exit(1)
        data = json.loads(path.read_text(encoding="utf-8"))
        docs = data if isinstance(data, list) else [data]

    # Mode 4: Raw JSON argument
    elif args.json:
        data = json.loads(args.json)
        docs = data if isinstance(data, list) else [data]

    # Default help
    else:
        print("Usage:")
        print("  1. Live from Cosmos DB:  python scripts/verify_valuation.py --live")
        print("  2. Live from API:        python scripts/verify_valuation.py --api https://<your-app>.azurewebsites.net")
        print("  3. From local JSON file: python scripts/verify_valuation.py --file snapshot.json")
        sys.exit(0)

    if not docs:
        print("No documents found to audit.")
        sys.exit(0)

    for d in docs:
        res = audit_snapshot(d)
        print_audit_report(res)


if __name__ == "__main__":
    main()
