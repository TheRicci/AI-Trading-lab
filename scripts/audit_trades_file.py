#!/usr/bin/env python3
import json
from pathlib import Path

file_path = Path("TradingBots_Trades_query_result-1.json")
trades = json.loads(file_path.read_text(encoding="utf-8"))

print(f"Total trades loaded: {len(trades)}")

errors = []
bots = set()
closed_trades = 0

for i, t in enumerate(trades):
    bot = t.get("botName")
    bots.add(bot)
    ticker = t.get("ticker")
    action = t.get("action")
    pos = t.get("position")
    shares = float(t.get("shares", 0))
    price = float(t.get("price", 0))
    leverage = float(t.get("leverage", 1))
    cash = float(t.get("cash", 0))
    ts = t.get("timestamp", "")
    
    # Check sizing
    notional = round(shares * price, 2)
    expected_margin = round(notional / leverage, 2)
    
    # Check realized PnL if closed
    if pos == "flat" and "realizedPnl" in t:
        closed_trades += 1
        realized = float(t["realizedPnl"])
        # Can check if closedDirection is present
        cdir = t.get("closedDirection")
        clev = t.get("closedLeverage")
        if not cdir:
            errors.append(f"Trade {t['id']} ({bot} {ticker}): flat trade missing closedDirection")

print(f"Bots found: {sorted(list(bots))}")
print(f"Total closed trades with realized PnL: {closed_trades}")
print(f"Errors found: {len(errors)}")
if errors:
    for e in errors[:10]:
        print(" -", e)

# Show last 10 trades summary
print("\n--- Last 10 Trades in File ---")
for t in trades[-10:]:
    realized_str = f" | Realized PnL: ${t.get('realizedPnl'):.2f}" if "realizedPnl" in t else ""
    print(f"[{t.get('timestamp')}] {t.get('botName')} | {t.get('action').upper()} {t.get('shares')} {t.get('ticker')} @ ${t.get('price')} ({t.get('position')} {t.get('leverage')}x) -> Cash: ${t.get('cash'):,.2f} | NAV: ${t.get('totalValue'):,.2f}{realized_str}")
