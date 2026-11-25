import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from app import (
    AnalysisRequest,
    PaperOrderRequest,
    TIMEFRAME_NAMES,
    _focus_instrument,
    _indicators,
    _timeframe_constant,
    analyze,
    live_quote,
    paper_order,
    symbols,
)


class MarketAnalysisTests(unittest.TestCase):
    def test_supported_timeframes_cover_minute_to_day(self):
        self.assertIn("M1", TIMEFRAME_NAMES)
        self.assertIn("D1", TIMEFRAME_NAMES)
        self.assertEqual(len(TIMEFRAME_NAMES), len(set(TIMEFRAME_NAMES)))

    def test_timeframe_maps_to_mt5_constant(self):
        mt5 = SimpleNamespace(TIMEFRAME_M1=1)
        self.assertEqual(_timeframe_constant(mt5, "m1"), ("M1", 1))

    def test_unsupported_timeframe_is_rejected(self):
        with self.assertRaises(HTTPException) as error:
            _timeframe_constant(SimpleNamespace(), "W1")
        self.assertEqual(error.exception.status_code, 422)

    def test_focus_instruments_accept_broker_suffixes_only_for_selected_markets(self):
        self.assertEqual(_focus_instrument("XAUUSDm"), "XAUUSD")
        self.assertEqual(_focus_instrument("XBTUSD"), "BTCUSD")
        self.assertEqual(_focus_instrument("GBPJPYm"), "GBPJPY")
        self.assertIsNone(_focus_instrument("AUDUSD"))
        self.assertIsNone(_focus_instrument("GBPUSDJPY"))

    def test_market_api_rejects_symbols_outside_the_focus_list(self):
        with patch("app._market_rates") as market_rates, patch("app._mt5") as get_mt5:
            with self.assertRaises(HTTPException) as analysis_error:
                analyze(AnalysisRequest(symbol="AUDUSD"))
            with self.assertRaises(HTTPException) as quote_error:
                live_quote("AUDUSD", "M1")
            with self.assertRaises(HTTPException) as paper_error:
                paper_order(PaperOrderRequest(
                    symbol="AUDUSD",
                    side="buy",
                    volume=0.01,
                    entry=1.1,
                    stop_loss=1.0,
                    take_profit=1.2,
                    confirmed=True,
                ))

        self.assertEqual(analysis_error.exception.status_code, 422)
        self.assertEqual(quote_error.exception.status_code, 422)
        self.assertEqual(paper_error.exception.status_code, 422)
        market_rates.assert_not_called()
        get_mt5.assert_not_called()

    def test_broker_watchlist_contains_only_selected_instruments(self):
        mt5 = Mock()
        mt5.symbols_get.return_value = [
            SimpleNamespace(name="AUDUSD"),
            SimpleNamespace(name="XAUUSDm"),
            SimpleNamespace(name="GOLD"),
            SimpleNamespace(name="BTCUSDm"),
            SimpleNamespace(name="BTCUSD"),
            SimpleNamespace(name="GBPUSD"),
            SimpleNamespace(name="EURUSD"),
            SimpleNamespace(name="GBPJPYm"),
            SimpleNamespace(name="ETHUSD"),
        ]

        with patch("app._mt5", return_value=mt5):
            result = symbols()

        self.assertEqual(
            [item["symbol"] for item in result["symbols"]],
            ["XAUUSDm", "BTCUSD", "GBPUSD", "EURUSD", "GBPJPYm", "ETHUSD"],
        )
        self.assertEqual(
            [item["description"] for item in result["symbols"]],
            ["Gold", "Bitcoin", "GBP/USD", "EUR/USD", "GBP/JPY", "Ethereum"],
        )
        self.assertTrue(result["connected"])
        mt5.shutdown.assert_called_once()

    def test_risk_percent_accepts_only_one_through_one_hundred(self):
        self.assertEqual(AnalysisRequest(risk_percent=100).risk_percent, 100)
        for value in (0.99, 100.01):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                AnalysisRequest(risk_percent=value)

    def test_indicators_use_historical_closed_candle_and_ranges(self):
        bars = [
            {"open": float(value), "high": float(value + 2), "low": float(value - 1), "close": float(value + 1)}
            for value in range(1, 61)
        ]

        result = _indicators(bars)

        self.assertEqual(result["last_closed_bar"], bars[-2])
        self.assertEqual(result["previous_close"], bars[-3]["close"])
        self.assertAlmostEqual(result["sma20"], 51.5)
        self.assertAlmostEqual(result["sma50"], 36.5)
        self.assertAlmostEqual(result["rsi14"], 100.0)
        self.assertAlmostEqual(result["atr14"], 3.0)
        self.assertEqual(result["range20_high"], bars[-1]["high"])
        self.assertEqual(result["range20_low"], bars[-20]["low"])

    def test_analysis_preserves_broker_symbol_suffix_case(self):
        bars = [
            {"time": value, "open": 1.0, "high": 1.2, "low": 0.9, "close": 1.1, "tick_volume": 10}
            for value in range(60)
        ]
        mt5 = Mock()
        market_data = {
            "mt5": mt5,
            "timeframe": "M1",
            "bars": bars,
            "tick": {"bid": 1.1, "ask": 1.2},
            "digits": 5,
            "account": None,
            "symbol_info": SimpleNamespace(
                trade_tick_size=0,
                trade_tick_value_loss=0,
                trade_tick_value=0,
                volume_step=0.01,
                volume_min=0.01,
                volume_max=10,
            ),
        }

        with patch("app._market_rates", return_value=market_data) as market_rates:
            analyze(AnalysisRequest(symbol="XAUUSDm", timeframe="M1"))

        market_rates.assert_called_once_with("XAUUSDm", "M1", 1000, 0)
        mt5.shutdown.assert_called_once()

    def test_live_quote_returns_current_tick_and_candle(self):
        mt5 = Mock()
        mt5.TIMEFRAME_M1 = 1
        mt5.ACCOUNT_TRADE_MODE_DEMO = 0
        mt5.ACCOUNT_TRADE_MODE_REAL = 2
        mt5.symbol_info.return_value = SimpleNamespace(visible=True, digits=5)
        mt5.symbol_info_tick.return_value = SimpleNamespace(bid=1.1, ask=1.1002)
        mt5.account_info.return_value = SimpleNamespace(
            login=12345,
            server="Broker-Demo",
            balance=1000.0,
            equity=990.5,
            currency="USD",
            trade_mode=0,
        )
        mt5.copy_rates_from_pos.return_value = [{
            "time": 100,
            "open": 1.09,
            "high": 1.11,
            "low": 1.08,
            "close": 1.1,
            "tick_volume": 20,
        }]

        with patch("app._mt5", return_value=mt5):
            result = live_quote("EURUSD", "M1")

        self.assertEqual(result["quote"], {"bid": 1.1, "ask": 1.1002})
        self.assertEqual(result["current_bar"]["time"], 100)
        self.assertEqual(result["account"]["balance"], 1000.0)
        self.assertEqual(result["account"]["equity"], 990.5)
        self.assertAlmostEqual(result["spread"], 0.0002)
        mt5.copy_rates_from_pos.assert_called_once_with("EURUSD", 1, 0, 1)
        mt5.shutdown.assert_called_once()

    def test_analysis_explains_when_broker_minimum_exceeds_risk_budget(self):
        bars = [
            {
                "time": value,
                "open": 1.0,
                "high": 1.0005,
                "low": 0.9995,
                "close": 1.0,
                "tick_volume": 10,
            }
            for value in range(60)
        ]
        mt5 = Mock()
        mt5.ACCOUNT_TRADE_MODE_DEMO = 0
        mt5.ACCOUNT_TRADE_MODE_REAL = 2
        account = SimpleNamespace(equity=100.0, currency="USD", login=42, trade_mode=0)
        symbol_info = SimpleNamespace(
            trade_tick_size=0.00001,
            trade_tick_value_loss=1.0,
            trade_tick_value=1.0,
            volume_step=0.01,
            volume_min=0.01,
            volume_max=10,
        )
        market_data = {
            "mt5": mt5,
            "timeframe": "M1",
            "bars": bars,
            "tick": {"bid": 1.0, "ask": 1.0001},
            "digits": 5,
            "account": account,
            "symbol_info": symbol_info,
        }

        with patch("app._market_rates", return_value=market_data), patch(
            "app.generate_signals",
            return_value=[{"strategy": "test", "side": "buy", "label": "Test", "reason": "test"}],
        ):
            result = analyze(AnalysisRequest(
                symbol="EURUSD",
                timeframe="M1",
                risk_percent=1,
            ))

        self.assertEqual(result["proposals"], [])
        self.assertEqual(result["sizing"]["minimum_volume"], 0.01)
        self.assertGreater(result["sizing"]["minimum_risk_percent"], 1)
        self.assertIn("minimum volume", result["trade_status"])
        mt5.shutdown.assert_called_once()


if __name__ == "__main__":
    unittest.main()
