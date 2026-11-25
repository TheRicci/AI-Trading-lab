"""
test_trading_logic.py - Unit tests for core trading, valuation, and parsing logic.
Uses standard library unittest so it runs out-of-the-box with zero extra dependencies.
"""

import json
import re
import unittest


def calculate_nav(free_cash: float, positions: list[dict], current_prices: dict[str, float]) -> dict:
    """
    Core valuation model replicated from bot/function_app.py and verify_valuation.py:
    NAV = freeCash + sum(marginUsed + unrealizedPnL)
    where:
      marginUsed = notional / leverage
      unrealizedPnL = direction * notional * (markPrice / entryPrice - 1)
      direction = +1 for long, -1 for short
    """
    total_margin = 0.0
    total_unrealized = 0.0
    evaluated_positions = []

    for pos in positions:
        ticker = pos["ticker"]
        direction = 1 if pos.get("action", "long").lower() == "long" else -1
        leverage = float(pos.get("leverage", 1.0))
        entry_price = float(pos["entryPrice"])
        notional = float(pos["notional"])
        mark_price = current_prices.get(ticker, entry_price)

        margin_used = notional / leverage
        if entry_price > 0:
            unrealized_pnl = direction * notional * ((mark_price / entry_price) - 1.0)
        else:
            unrealized_pnl = 0.0

        position_equity = margin_used + unrealized_pnl

        total_margin += margin_used
        total_unrealized += unrealized_pnl

        evaluated_positions.append({
            "ticker": ticker,
            "marginUsed": margin_used,
            "unrealizedPnL": unrealized_pnl,
            "positionEquity": position_equity,
        })

    nav = free_cash + total_margin + total_unrealized
    return {
        "nav": nav,
        "freeCash": free_cash,
        "marginUsed": total_margin,
        "unrealizedPnL": total_unrealized,
        "positions": evaluated_positions,
    }


def extract_llm_json(raw_text: str):
    """
    Robust JSON extractor used in bot/function_app.py to handle LLM conversational
    output, markdown code blocks, and preambles.
    """
    text = raw_text.strip()

    # Strip markdown code blocks if present
    md_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if md_match:
        text = md_match.group(1).strip()

    # Find start of JSON array or object
    array_start = text.find("[")
    obj_start = text.find("{")

    if array_start != -1 and (obj_start == -1 or array_start < obj_start):
        array_end = text.rfind("]")
        if array_end > array_start:
            text = text[array_start : array_end + 1]
    elif obj_start != -1:
        obj_end = text.rfind("}")
        if obj_end > obj_start:
            text = text[obj_start : obj_end + 1]

    # Clean trailing commas before closing braces/brackets
    text = re.sub(r",\s*([}\]])", r"\1", text)

    return json.loads(text)


def classify_asset_type(ticker: str) -> str:
    """Classifier for ticker routing to prevent crypto/stock collisions."""
    ticker_upper = ticker.upper()
    if ticker_upper.endswith(".SA"):
        return "brazil"
    if "=X" in ticker_upper or ticker_upper in {"EURUSD=X", "GBPUSD=X", "USDJPY=X"}:
        return "forex"
    if "/" in ticker_upper or ticker_upper in {"BTC", "ETH", "SOL", "DOGE", "BTC/USD", "ETH/USD"}:
        return "crypto"
    return "stock"


class TestValuationModel(unittest.TestCase):
    def test_spot_long_gain(self):
        # Bought $10,000 NVDA at $100 (1x leverage). Price rises to $120 (+20%).
        free_cash = 90000.0
        positions = [{
            "ticker": "NVDA",
            "action": "long",
            "leverage": 1.0,
            "entryPrice": 100.0,
            "notional": 10000.0,
        }]
        prices = {"NVDA": 120.0}

        val = calculate_nav(free_cash, positions, prices)
        self.assertAlmostEqual(val["nav"], 102000.0)
        self.assertAlmostEqual(val["unrealizedPnL"], 2000.0)
        self.assertAlmostEqual(val["marginUsed"], 10000.0)

    def test_leveraged_short_gain(self):
        # Short $10,000 SPY at $500 with 2x leverage. Price drops to $450 (-10%).
        free_cash = 95000.0
        positions = [{
            "ticker": "SPY",
            "action": "short",
            "leverage": 2.0,
            "entryPrice": 500.0,
            "notional": 10000.0,
        }]
        prices = {"SPY": 450.0}

        val = calculate_nav(free_cash, positions, prices)
        self.assertAlmostEqual(val["nav"], 101000.0)
        self.assertAlmostEqual(val["marginUsed"], 5000.0)
        self.assertAlmostEqual(val["unrealizedPnL"], 1000.0)

    def test_flat_portfolio(self):
        val = calculate_nav(100000.0, [], {})
        self.assertEqual(val["nav"], 100000.0)
        self.assertEqual(val["marginUsed"], 0.0)
        self.assertEqual(val["unrealizedPnL"], 0.0)

    def test_audit_snapshot_verifier(self):
        """Verifies that audit_snapshot in scripts/verify_valuation.py validates a matching snapshot."""
        import sys
        from pathlib import Path
        repo_root = Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(repo_root / "scripts"))
        from verify_valuation import audit_snapshot

        sample_doc = {
            "botName": "open_gemini",
            "timestamp": "2026-11-16T12:00:00Z",
            "cash": 90000.0,
            "totalValue": 102000.0,
            "positions": {
                "NVDA": {
                    "direction": "long",
                    "shares": 100,
                    "entryPrice": 100.0,
                    "markPrice": 120.0,
                    "marginUsed": 10000.0,
                    "notional": 10000.0,
                    "leverage": 1.0,
                    "unrealizedPnl": 2000.0,
                }
            }
        }
        res = audit_snapshot(sample_doc)
        self.assertTrue(res["isValid"])
        self.assertAlmostEqual(res["discrepancy"], 0.0)


class TestLLMResponseParser(unittest.TestCase):
    def test_clean_json_array(self):
        raw = '[{"ticker": "NVDA", "action": "long", "notional": 15000, "leverage": 1.5}]'
        parsed = extract_llm_json(raw)
        self.assertIsInstance(parsed, list)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["ticker"], "NVDA")

    def test_markdown_code_fences(self):
        raw = """Here is my trading decision for today:
```json
[
  {
    "ticker": "BTC/USD",
    "action": "long",
    "notional": 20000,
    "leverage": 1.0,
    "reasoning": "Bitcoin consolidating above support."
  }
]
```
Good luck."""
        parsed = extract_llm_json(raw)
        self.assertIsInstance(parsed, list)
        self.assertEqual(parsed[0]["ticker"], "BTC/USD")
        self.assertEqual(parsed[0]["notional"], 20000)

    def test_trailing_comma_handling(self):
        raw = '{"ticker": "PETR4.SA", "action": "long", "leverage": 1.0,}'
        parsed = extract_llm_json(raw)
        self.assertEqual(parsed["ticker"], "PETR4.SA")


class TestAssetRouting(unittest.TestCase):
    def test_brazil_stock(self):
        self.assertEqual(classify_asset_type("PETR4.SA"), "brazil")
        self.assertEqual(classify_asset_type("VALE3.SA"), "brazil")

    def test_crypto(self):
        self.assertEqual(classify_asset_type("BTC/USD"), "crypto")
        self.assertEqual(classify_asset_type("ETH/USD"), "crypto")
        self.assertEqual(classify_asset_type("SOL"), "crypto")

    def test_forex(self):
        self.assertEqual(classify_asset_type("EURUSD=X"), "forex")

    def test_us_equity(self):
        self.assertEqual(classify_asset_type("NVDA"), "stock")
        self.assertEqual(classify_asset_type("SPY"), "stock")


def _load_bot_module():
    """Import functions/bot/function_app.py with Azure SDK dependencies stubbed."""
    import importlib.util
    import sys
    from pathlib import Path
    from unittest.mock import MagicMock, patch

    stubs = {
        "azure": MagicMock(),
        "azure.functions": MagicMock(),
        "azure.cosmos": MagicMock(),
        "azure.messaging": MagicMock(),
        "azure.messaging.webpubsubservice": MagicMock(),
        "requests": MagicMock(),
    }
    stubs["azure.functions"].EventGridEvent = object
    stubs["requests"].RequestException = Exception

    bot_path = Path(__file__).resolve().parent.parent / "functions" / "bot" / "function_app.py"
    with patch.dict(sys.modules, stubs):
        spec = importlib.util.spec_from_file_location("bot_function_app", bot_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


class TestExecuteTickerDecision(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bot = _load_bot_module()

    def test_leverage_change_emits_close_then_open(self):
        # Holds 2x LONG NVDA @ 125.53 (398 shares -> notional 49960.94, margin 24980.47)
        portfolio = {
            "cash": 35000.0,
            "positions": {
                "NVDA": {
                    "direction": "long",
                    "shares": 398,
                    "entryPrice": 125.53,
                    "leverage": 2.0,
                    "notional": 49960.94,
                    "marginUsed": 24980.47,
                    "assetType": "stock",
                }
            },
        }
        price = 165.05
        nav_before = self.bot.compute_nav(portfolio, {"NVDA": price})

        records, realized_delta = self.bot._execute_ticker_decision(
            portfolio=portfolio,
            ticker="NVDA",
            asset_type="stock",
            position="long",
            leverage=1.0,
            cash_amount=40000.0,
            price=price,
            reasoning="Switching to spot exposure",
            session="open",
            session_id="open-2026-11-24",
            nav_cache={"NVDA": price},
            fallback_nav=nav_before,
        )

        self.assertEqual(len(records), 2)
        close_rec, open_rec = records
        self.assertEqual(close_rec["position"], "flat")
        self.assertEqual(close_rec["action"], "sell")
        self.assertEqual(close_rec["closedDirection"], "long")
        self.assertEqual(close_rec["closedLeverage"], 2.0)
        self.assertGreater(close_rec["realizedPnl"], 15000.0)
        self.assertAlmostEqual(realized_delta, close_rec["realizedPnl"], places=2)

        self.assertEqual(open_rec["position"], "long")
        self.assertEqual(open_rec["action"], "buy")
        self.assertEqual(open_rec["leverage"], 1.0)
        self.assertEqual(open_rec["shares"], 242)

        nav_after = self.bot.compute_nav(portfolio, {"NVDA": price})
        self.assertAlmostEqual(nav_after, nav_before, delta=0.05)

    def test_same_side_add_blends_entry_without_realized_pnl(self):
        # Holds 40 shares SPOT GLD @ 322.87 -> notional 12914.80, margin 12914.80
        portfolio = {
            "cash": 50000.0,
            "positions": {
                "GLD": {
                    "direction": "long",
                    "shares": 40,
                    "entryPrice": 322.87,
                    "leverage": 1.0,
                    "notional": 12914.80,
                    "marginUsed": 12914.80,
                    "assetType": "etf",
                }
            },
        }
        price = 333.02
        nav_before = self.bot.compute_nav(portfolio, {"GLD": price})

        records, realized_delta = self.bot._execute_ticker_decision(
            portfolio=portfolio,
            ticker="GLD",
            asset_type="etf",
            position="long",
            leverage=1.0,
            cash_amount=20000.0,
            price=price,
            reasoning="Increase position for diversification",
            session="open",
            session_id="open-2026-11-24",
            nav_cache={"GLD": price},
            fallback_nav=nav_before,
        )

        self.assertEqual(realized_delta, 0.0)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["position"], "long")
        self.assertEqual(records[0]["action"], "buy")
        self.assertEqual(records[0]["shares"], 20)  # 60 target - 40 existing

        pos = portfolio["positions"]["GLD"]
        self.assertEqual(pos["shares"], 60)
        expected_entry = round((12914.80 + 20 * 333.02) / 60, 4)
        self.assertAlmostEqual(pos["entryPrice"], expected_entry, places=4)

        nav_after = self.bot.compute_nav(portfolio, {"GLD": price})
        self.assertAlmostEqual(nav_after, nav_before, delta=0.05)

    def test_same_side_partial_reduce_preserves_entry_and_realizes_slice(self):
        # Holds 200 shares 2x LONG SPY @ 100.00 -> notional 20000, margin 10000
        portfolio = {
            "cash": 50000.0,
            "positions": {
                "SPY": {
                    "direction": "long",
                    "shares": 200,
                    "entryPrice": 100.0,
                    "leverage": 2.0,
                    "notional": 20000.0,
                    "marginUsed": 10000.0,
                    "assetType": "etf",
                }
            },
        }
        price = 120.0
        nav_before = self.bot.compute_nav(portfolio, {"SPY": price})

        # Resize down to 6000 margin at 2x = 12000 notional = 100 shares (half closed)
        records, realized_delta = self.bot._execute_ticker_decision(
            portfolio=portfolio,
            ticker="SPY",
            asset_type="etf",
            position="long",
            leverage=2.0,
            cash_amount=6000.0,
            price=price,
            reasoning="Trimming half to lock in gains",
            session="open",
            session_id="open-2026-11-24",
            nav_cache={"SPY": price},
            fallback_nav=nav_before,
        )

        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec["position"], "flat")
        self.assertEqual(rec["action"], "sell")
        self.assertTrue(rec.get("partial"))
        self.assertEqual(rec["shares"], 100)
        self.assertAlmostEqual(rec["realizedPnl"], 2000.0, places=2)
        self.assertAlmostEqual(realized_delta, 2000.0, places=2)

        pos = portfolio["positions"]["SPY"]
        self.assertEqual(pos["shares"], 100)
        self.assertEqual(pos["entryPrice"], 100.0)
        self.assertAlmostEqual(pos["marginUsed"], 5000.0, places=2)

        nav_after = self.bot.compute_nav(portfolio, {"SPY": price})
        self.assertAlmostEqual(nav_after, nav_before, delta=0.05)

    def test_duplicate_hold_guard_respects_leverage_change(self):
        # 2x LONG with 20k notional -> switching to 1x LONG with 20k notional must NOT be discarded
        portfolio = {
            "cash": 50000.0,
            "positions": {
                "AAPL": {
                    "direction": "long",
                    "shares": 100,
                    "entryPrice": 200.0,
                    "leverage": 2.0,
                    "notional": 20000.0,
                    "marginUsed": 10000.0,
                    "assetType": "stock",
                }
            },
        }
        records, _ = self.bot._execute_ticker_decision(
            portfolio=portfolio,
            ticker="AAPL",
            asset_type="stock",
            position="long",
            leverage=1.0,
            cash_amount=20000.0,
            price=200.0,
            reasoning="De-leveraging to spot",
            session="open",
            session_id="open-2026-11-24",
            nav_cache={"AAPL": 200.0},
            fallback_nav=60000.0,
        )
        self.assertEqual(len(records), 2)


if __name__ == "__main__":
    unittest.main()
