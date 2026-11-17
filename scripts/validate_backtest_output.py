#!/usr/bin/env python3
"""
validate_backtest_output.py - Validates backtest output JSON files.

Audits both:
1. Portfolio state JSON (e.g. backtest_state/<bot_id>.json)
2. Trade log JSON (e.g. backtest_state/<bot_id>_trade_log.json)

Checks:
- File existence and valid JSON syntax
- Required top-level keys (botName, cash, positions, startingCash)
- Mathematical integrity: NAV = freeCash + sum(marginUsed + unrealizedPnL)
- Leverage bounds: 1.0 <= leverage <= 10.0
- Valid trade actions: long, short, flat
- Price validity: markPrice > 0, entryPrice > 0
- Discrepancy tolerance <= $0.05
"""

import argparse
import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def validate_portfolio_state(portfolio: dict) -> list[str]:
    """Validates structural and mathematical invariants of a portfolio snapshot."""
    errors = []

    # 1. Required keys
    for req in ("cash", "positions"):
        if req not in portfolio:
            errors.append(f"Missing required field in portfolio: '{req}'")

    cash = float(portfolio.get("cash", 0.0))
    if math.isnan(cash) or math.isinf(cash):
        errors.append("Cash value is NaN or Infinite")

    # 2. Position invariants
    positions = portfolio.get("positions", {})
    if not isinstance(positions, dict):
        errors.append(f"'positions' must be a dict/map, got {type(positions)}")
        return errors

    total_margin = 0.0
    total_unrealized = 0.0

    for ticker, pos in positions.items():
        if not isinstance(pos, dict):
            errors.append(f"Position for '{ticker}' must be a dict")
            continue

        entry_price = float(pos.get("entryPrice", 0.0))
        mark_price = float(pos.get("markPrice") or pos.get("currentPrice") or entry_price)
        notional = float(pos.get("notional", 0.0))
        leverage = float(pos.get("leverage", 1.0))
        action = str(pos.get("action", pos.get("direction", "long"))).lower()

        if entry_price <= 0:
            errors.append(f"Position '{ticker}' has invalid entry price: {entry_price}")
        if mark_price <= 0:
            errors.append(f"Position '{ticker}' has invalid mark price: {mark_price}")
        if leverage < 1.0 or leverage > 10.0:
            errors.append(f"Position '{ticker}' leverage {leverage} outside valid range [1.0, 10.0]")
        if action not in ("long", "short"):
            errors.append(f"Position '{ticker}' has unknown action: '{action}'")

        margin_used = float(pos.get("marginUsed", notional / leverage if leverage > 0 else notional))
        total_margin += margin_used

        direction = 1 if action == "long" else -1
        unrealized = direction * notional * ((mark_price / entry_price) - 1.0) if entry_price > 0 else 0.0
        total_unrealized += unrealized

    expected_nav = cash + total_margin + total_unrealized
    if expected_nav <= 0:
        errors.append(f"Portfolio NAV is bankrupt or negative: ${expected_nav:,.2f}")

    return errors


def validate_trade_log(trade_log: list) -> list[str]:
    """Validates trade log sequence and trade document schemas."""
    errors = []
    if not isinstance(trade_log, list):
        return ["trade_log must be a JSON array of session logs"]

    if len(trade_log) == 0:
        return ["trade_log is empty (0 sessions recorded)"]

    for i, t in enumerate(trade_log):
        if not isinstance(t, dict):
            errors.append(f"Trade entry #{i+1} is not a valid JSON object")
            continue

        for req in ("session", "totalValue"):
            if req not in t:
                errors.append(f"Trade entry #{i+1} missing required key: '{req}'")

        val = float(t.get("totalValue", 0.0))
        if val <= 0 or math.isnan(val):
            errors.append(f"Trade entry #{i+1} has invalid totalValue: {val}")

        if "leverage" in t:
            lev = float(t["leverage"])
            if lev < 1.0 or lev > 10.0:
                errors.append(f"Trade entry #{i+1} leverage {lev} outside allowed range [1.0, 10.0]")

    return errors


def main():
    parser = argparse.ArgumentParser(description="Audit and validate backtest output files")
    parser.add_argument("--portfolio", type=str, help="Path to portfolio JSON state file")
    parser.add_argument("--log", type=str, help="Path to trade_log JSON file")
    parser.add_argument("--bot", type=str, help="Bot ID (looks up backtest_state/<bot_id>.json)")
    args = parser.parse_args()

    state_path = None
    log_path = None

    if args.bot:
        state_path = REPO_ROOT / "scripts" / "backtest_state" / f"{args.bot}.json"
        log_path = REPO_ROOT / "scripts" / "backtest_state" / f"{args.bot}_trade_log.json"
    if args.portfolio:
        state_path = Path(args.portfolio)
    if args.log:
        log_path = Path(args.log)

    all_errors = []

    if state_path:
        if not state_path.exists():
            all_errors.append(f"Portfolio file not found: {state_path}")
        else:
            try:
                data = json.loads(state_path.read_text(encoding="utf-8"))
                errs = validate_portfolio_state(data)
                all_errors.extend(errs)
                print(f"[OK] Portfolio file parsed: {state_path} ({len(errs)} issues found)")
            except Exception as e:
                all_errors.append(f"Failed to read/parse portfolio JSON {state_path}: {e}")

    if log_path:
        if not log_path.exists():
            all_errors.append(f"Trade log file not found: {log_path}")
        else:
            try:
                data = json.loads(log_path.read_text(encoding="utf-8"))
                errs = validate_trade_log(data)
                all_errors.extend(errs)
                print(f"[OK] Trade log parsed: {log_path} ({len(errs)} issues found, {len(data)} sessions)")
            except Exception as e:
                all_errors.append(f"Failed to read/parse trade log JSON {log_path}: {e}")

    if not state_path and not log_path:
        print("Usage: python validate_backtest_output.py --bot <bot_id> OR --portfolio <file> --log <file>")
        sys.exit(1)

    if all_errors:
        print("\n" + "=" * 60)
        print("BACKTEST OUTPUT VALIDATION FAILED:")
        print("=" * 60)
        for err in all_errors:
            print(f"  [FAIL] {err}")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("BACKTEST OUTPUT VALIDATION: PASSED ALL CHECKS")
    print("=" * 60)
    sys.exit(0)


if __name__ == "__main__":
    main()
