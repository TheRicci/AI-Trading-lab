"""
test_backtest_mock.py - Mock and smoke tests for the backtest engine.
Validates simulation loop, session recording, position updates, and NAV tracking
without making live external network calls or using API keys.
"""

import unittest


class MockPortfolio:
    def __init__(self, cash=100000.0):
        self.cash = cash
        self.positions = {}
        self.history = []

    def execute_decision(self, decision: dict, current_price: float):
        ticker = decision["ticker"]
        action = decision.get("action", "long").lower()
        notional = float(decision.get("notional", 10000.0))
        leverage = float(decision.get("leverage", 1.0))
        margin_required = notional / leverage

        if action in ("long", "short"):
            if margin_required <= self.cash:
                self.cash -= margin_required
                self.positions[ticker] = {
                    "ticker": ticker,
                    "action": action,
                    "entryPrice": current_price,
                    "notional": notional,
                    "leverage": leverage,
                    "marginUsed": margin_required,
                }
        elif action == "flat":
            if ticker in self.positions:
                pos = self.positions.pop(ticker)
                direction = 1 if pos["action"] == "long" else -1
                pnl = direction * pos["notional"] * (current_price / pos["entryPrice"] - 1.0)
                self.cash += pos["marginUsed"] + pnl

    def compute_nav(self, prices: dict[str, float]) -> float:
        nav = self.cash
        for ticker, pos in self.positions.items():
            price = prices.get(ticker, pos["entryPrice"])
            direction = 1 if pos["action"] == "long" else -1
            pnl = direction * pos["notional"] * (price / pos["entryPrice"] - 1.0)
            nav += pos["marginUsed"] + pnl
        return nav


class TestMockBacktest(unittest.TestCase):
    def test_mock_backtest_session(self):
        portfolio = MockPortfolio(cash=100000.0)

        # Session 1: Buy NVDA at $100 with 1x leverage, $10k notional
        s1_decision = {"ticker": "NVDA", "action": "long", "notional": 10000.0, "leverage": 1.0}
        portfolio.execute_decision(s1_decision, current_price=100.0)

        self.assertEqual(portfolio.cash, 90000.0)
        self.assertIn("NVDA", portfolio.positions)
        self.assertEqual(portfolio.compute_nav({"NVDA": 100.0}), 100000.0)

        # Between sessions: NVDA rises to $110
        self.assertEqual(portfolio.compute_nav({"NVDA": 110.0}), 101000.0)

        # Session 2: Close position (flat) at $110
        s2_decision = {"ticker": "NVDA", "action": "flat"}
        portfolio.execute_decision(s2_decision, current_price=110.0)

        self.assertNotIn("NVDA", portfolio.positions)
        self.assertEqual(portfolio.cash, 101000.0)
        self.assertEqual(portfolio.compute_nav({}), 101000.0)

    def test_validate_backtest_output_pass(self):
        """Verifies that validate_backtest_output passes valid state and logs."""
        import sys
        from pathlib import Path
        repo_root = Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(repo_root / "scripts"))
        from validate_backtest_output import validate_portfolio_state, validate_trade_log

        valid_portfolio = {
            "id": "open_gemini",
            "botName": "open_gemini",
            "cash": 80000.0,
            "startingCash": 100000.0,
            "positions": {
                "NVDA": {
                    "ticker": "NVDA",
                    "action": "long",
                    "entryPrice": 100.0,
                    "markPrice": 110.0,
                    "notional": 20000.0,
                    "leverage": 1.0,
                    "marginUsed": 20000.0,
                }
            }
        }
        valid_log = [
            {"session": 1, "trigger": "market-open", "totalValue": 100000.0, "leverage": 1.0},
            {"session": 2, "trigger": "market-close", "totalValue": 102000.0, "leverage": 1.0},
        ]
        errors = validate_portfolio_state(valid_portfolio)
        self.assertEqual(len(errors), 0, f"Unexpected errors: {errors}")

        log_errors = validate_trade_log(valid_log)
        self.assertEqual(len(log_errors), 0, f"Unexpected log errors: {log_errors}")

    def test_validate_backtest_output_catches_invalid_leverage(self):
        """Verifies that invalid leverage (> 10x) is rejected."""
        import sys
        from pathlib import Path
        repo_root = Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(repo_root / "scripts"))
        from validate_backtest_output import validate_portfolio_state

        invalid_portfolio = {
            "cash": 80000.0,
            "positions": {
                "NVDA": {
                    "entryPrice": 100.0,
                    "markPrice": 100.0,
                    "notional": 10000.0,
                    "leverage": 50.0,  # Invalid: capped at 10.0x
                    "action": "long",
                }
            }
        }
        errors = validate_portfolio_state(invalid_portfolio)
        self.assertTrue(any("leverage" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
