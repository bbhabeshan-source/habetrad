import unittest

from trading import generate_signals


class TradingStrategyTests(unittest.TestCase):
    def test_donchian_signal_uses_last_closed_bar(self):
        bars = [
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0}
            for _ in range(60)
        ]
        bars[-2] = {"open": 100.0, "high": 103.0, "low": 99.0, "close": 102.0}

        signals = generate_signals(bars, ["donchian_breakout"])

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["strategy"], "donchian_breakout")
        self.assertEqual(signals[0]["side"], "buy")

    def test_flat_market_does_not_create_signals(self):
        bars = [
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0}
            for _ in range(60)
        ]

        self.assertEqual(generate_signals(bars, [
            "ema_trend",
            "rsi_bollinger",
            "donchian_breakout",
            "structure_break",
            "ict_price_action",
        ]), [])


if __name__ == "__main__":
    unittest.main()
