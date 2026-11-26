import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException

from app import ConfirmationRequest, PaperOrderRequest, app, execute, paper_order


class TradeExecutionTests(unittest.TestCase):
    def setUp(self):
        self.original_proposals = app.state.proposals
        self.original_auto_demo_attempts = app.state.auto_demo_attempts
        app.state.proposals = {}
        app.state.auto_demo_attempts = {}
        self.proposal_id = "test-proposal"
        app.state.proposals[self.proposal_id] = {
            "proposal_id": self.proposal_id,
            "symbol": "EURUSD",
            "side": "buy",
            "volume": 0.1,
            "entry": 1.1,
            "stop_loss": 1.09,
            "take_profit": 1.12,
            "risk_amount": 60,
            "currency": "USD",
            "label": "Test setup",
            "account_login": 42,
            "equity": 10000,
            "risk_distance": 1,
            "strategy": "test_strategy",
            "timeframe": "M1",
            "bar_time": 100,
            "created_at": datetime.now(timezone.utc).timestamp(),
        }

    def tearDown(self):
        app.state.proposals = self.original_proposals
        app.state.auto_demo_attempts = self.original_auto_demo_attempts

    def test_execution_requires_explicit_confirmation(self):
        with self.assertRaises(HTTPException) as error:
            execute(ConfirmationRequest(
                proposal_id=self.proposal_id,
                mode="paper",
                confirmed=False,
            ))

        self.assertEqual(error.exception.status_code, 400)

    def test_paper_execution_never_uses_mt5(self):
        with patch("app._mt5") as get_mt5:
            result = execute(ConfirmationRequest(
                proposal_id=self.proposal_id,
                mode="paper",
                confirmed=True,
            ))

        get_mt5.assert_not_called()
        self.assertEqual(result["status"], "paper_fill")
        self.assertNotIn(self.proposal_id, app.state.proposals)

    def test_paper_desk_validates_confirmation_and_order_levels(self):
        order = PaperOrderRequest(
            symbol="EURUSD",
            side="buy",
            volume=0.1,
            entry=1.1,
            stop_loss=1.09,
            take_profit=1.12,
            confirmed=True,
        )

        with patch("app._mt5") as get_mt5:
            result = paper_order(order)

        get_mt5.assert_not_called()
        self.assertEqual(result["status"], "paper_fill")

        with self.assertRaises(HTTPException) as error:
            paper_order(order.model_copy(update={"stop_loss": 1.11}))
        self.assertEqual(error.exception.status_code, 422)

    def test_live_submission_is_rejected_for_demo_account(self):
        mt5 = Mock()
        mt5.ACCOUNT_TRADE_MODE_DEMO = 0
        mt5.ACCOUNT_TRADE_MODE_REAL = 2
        mt5.account_info.return_value = SimpleNamespace(
            login=42,
            trade_mode=0,
            equity=10000,
        )

        with patch("app._mt5", return_value=mt5):
            with self.assertRaises(HTTPException) as error:
                execute(ConfirmationRequest(
                    proposal_id=self.proposal_id,
                    mode="live",
                    confirmed=True,
                ))

        self.assertEqual(error.exception.status_code, 409)
        mt5.order_send.assert_not_called()
        mt5.shutdown.assert_called_once()

    def test_automatic_submission_is_restricted_to_demo_and_sends_one_order(self):
        mt5 = Mock()
        mt5.ACCOUNT_TRADE_MODE_DEMO = 0
        mt5.ACCOUNT_TRADE_MODE_REAL = 2
        mt5.ORDER_TYPE_BUY = 0
        mt5.ORDER_TYPE_SELL = 1
        mt5.TRADE_ACTION_DEAL = 1
        mt5.ORDER_TIME_GTC = 0
        mt5.SYMBOL_FILLING_IOC = 2
        mt5.SYMBOL_FILLING_FOK = 1
        mt5.SYMBOL_TRADE_EXECUTION_MARKET = 2
        mt5.ORDER_FILLING_IOC = 1
        mt5.ORDER_FILLING_FOK = 0
        mt5.ORDER_FILLING_RETURN = 2
        mt5.TRADE_RETCODE_DONE = 10009
        mt5.TRADE_RETCODE_PLACED = 10008
        mt5.account_info.return_value = SimpleNamespace(
            login=42,
            trade_mode=0,
            equity=10000,
        )
        mt5.symbol_info.return_value = SimpleNamespace(
            visible=True,
            filling_mode=3,
            trade_exemode=2,
        )
        mt5.symbol_info_tick.return_value = SimpleNamespace(bid=1.1, ask=1.1001)
        mt5.history_deals_get.return_value = []
        mt5.positions_get.return_value = [SimpleNamespace(ticket=ticket) for ticket in range(5)]
        mt5.order_check.return_value = SimpleNamespace(retcode=0, comment="Done")
        mt5.order_send.return_value = SimpleNamespace(retcode=10009, order=123, deal=456)

        proposal = dict(app.state.proposals[self.proposal_id])
        with patch("app._mt5", return_value=mt5):
            result = execute(ConfirmationRequest(
                proposal_id=self.proposal_id,
                mode="demo",
                confirmed=False,
                auto_submit=True,
            ))
            app.state.proposals["duplicate-proposal"] = {
                **proposal,
                "proposal_id": "duplicate-proposal",
            }
            with self.assertRaises(HTTPException) as error:
                execute(ConfirmationRequest(
                    proposal_id="duplicate-proposal",
                    mode="demo",
                    confirmed=False,
                    auto_submit=True,
                ))

        self.assertEqual(result["status"], "broker_order_submitted")
        self.assertIn("automatically submitted", result["message"])
        self.assertEqual(error.exception.status_code, 409)
        mt5.order_check.assert_called_once()
        mt5.order_send.assert_called_once()
        self.assertEqual(
            mt5.order_check.call_args.args[0]["type_filling"],
            mt5.ORDER_FILLING_IOC,
        )
        self.assertEqual(
            mt5.order_send.call_args.args[0]["type_filling"],
            mt5.ORDER_FILLING_IOC,
        )
        self.assertEqual(mt5.positions_get.call_count, 2)
        self.assertEqual(mt5.shutdown.call_count, 2)

    def test_broker_order_check_rejection_is_reported_without_sending(self):
        mt5 = Mock()
        mt5.ACCOUNT_TRADE_MODE_DEMO = 0
        mt5.ACCOUNT_TRADE_MODE_REAL = 2
        mt5.ORDER_TYPE_BUY = 0
        mt5.ORDER_TYPE_SELL = 1
        mt5.TRADE_ACTION_DEAL = 1
        mt5.ORDER_TIME_GTC = 0
        mt5.SYMBOL_FILLING_IOC = 2
        mt5.SYMBOL_FILLING_FOK = 1
        mt5.SYMBOL_TRADE_EXECUTION_MARKET = 2
        mt5.ORDER_FILLING_IOC = 1
        mt5.ORDER_FILLING_FOK = 0
        mt5.ORDER_FILLING_RETURN = 2
        mt5.account_info.return_value = SimpleNamespace(
            login=42,
            trade_mode=0,
            equity=10000,
        )
        mt5.symbol_info.return_value = SimpleNamespace(
            visible=True,
            filling_mode=3,
            trade_exemode=2,
        )
        mt5.symbol_info_tick.return_value = SimpleNamespace(bid=1.1, ask=1.1001)
        mt5.history_deals_get.return_value = []
        mt5.positions_get.return_value = []
        mt5.order_check.return_value = SimpleNamespace(retcode=10016, comment="Invalid stops")

        with patch("app._mt5", return_value=mt5):
            with self.assertRaises(HTTPException) as error:
                execute(ConfirmationRequest(
                    proposal_id=self.proposal_id,
                    mode="demo",
                    confirmed=True,
                ))

        self.assertEqual(error.exception.status_code, 422)
        self.assertIn("retcode 10016: Invalid stops", error.exception.detail)
        self.assertIn("no order was submitted", error.exception.detail)
        mt5.order_check.assert_called_once()
        mt5.order_send.assert_not_called()
        mt5.shutdown.assert_called_once()

    def test_manual_demo_approval_checks_then_submits_with_supported_fok(self):
        mt5 = Mock()
        mt5.ACCOUNT_TRADE_MODE_DEMO = 0
        mt5.ACCOUNT_TRADE_MODE_REAL = 2
        mt5.ORDER_TYPE_BUY = 0
        mt5.ORDER_TYPE_SELL = 1
        mt5.TRADE_ACTION_DEAL = 1
        mt5.ORDER_TIME_GTC = 0
        mt5.SYMBOL_FILLING_IOC = 2
        mt5.SYMBOL_FILLING_FOK = 1
        mt5.SYMBOL_TRADE_EXECUTION_MARKET = 2
        mt5.ORDER_FILLING_IOC = 1
        mt5.ORDER_FILLING_FOK = 0
        mt5.ORDER_FILLING_RETURN = 2
        mt5.TRADE_RETCODE_DONE = 10009
        mt5.TRADE_RETCODE_PLACED = 10008
        mt5.account_info.return_value = SimpleNamespace(
            login=42,
            trade_mode=0,
            equity=10000,
        )
        mt5.symbol_info.return_value = SimpleNamespace(
            visible=True,
            filling_mode=mt5.SYMBOL_FILLING_FOK,
            trade_exemode=mt5.SYMBOL_TRADE_EXECUTION_MARKET,
        )
        mt5.symbol_info_tick.return_value = SimpleNamespace(bid=1.1, ask=1.1001)
        mt5.history_deals_get.return_value = []
        mt5.positions_get.return_value = [SimpleNamespace(ticket=ticket) for ticket in range(5)]
        mt5.order_check.return_value = SimpleNamespace(retcode=0, comment="Done")
        mt5.order_send.return_value = SimpleNamespace(retcode=10009, order=123, deal=456)
        mt5.shutdown.side_effect = RuntimeError("simulated MT5 shutdown failure")

        with self.assertLogs("app", level="ERROR") as logs:
            with patch("app._mt5", return_value=mt5):
                result = execute(ConfirmationRequest(
                    proposal_id=self.proposal_id,
                    mode="demo",
                    confirmed=True,
                ))

        self.assertEqual(result["status"], "broker_order_submitted")
        self.assertIn("MT5 shutdown failed after order request", logs.output[0])
        mt5.order_check.assert_called_once()
        mt5.order_send.assert_called_once()
        self.assertEqual(
            mt5.order_check.call_args.args[0]["type_filling"],
            mt5.ORDER_FILLING_FOK,
        )
        self.assertEqual(
            mt5.order_send.call_args.args[0]["type_filling"],
            mt5.ORDER_FILLING_FOK,
        )
        mt5.shutdown.assert_called_once()

    def test_order_check_none_reports_terminal_error_without_sending(self):
        mt5 = Mock()
        mt5.ACCOUNT_TRADE_MODE_DEMO = 0
        mt5.ACCOUNT_TRADE_MODE_REAL = 2
        mt5.ORDER_TYPE_BUY = 0
        mt5.ORDER_TYPE_SELL = 1
        mt5.TRADE_ACTION_DEAL = 1
        mt5.ORDER_TIME_GTC = 0
        mt5.SYMBOL_FILLING_IOC = 2
        mt5.SYMBOL_FILLING_FOK = 1
        mt5.SYMBOL_TRADE_EXECUTION_MARKET = 2
        mt5.ORDER_FILLING_IOC = 1
        mt5.ORDER_FILLING_FOK = 0
        mt5.ORDER_FILLING_RETURN = 2
        mt5.account_info.return_value = SimpleNamespace(
            login=42,
            trade_mode=0,
            equity=10000,
        )
        mt5.symbol_info.return_value = SimpleNamespace(
            visible=True,
            filling_mode=3,
            trade_exemode=2,
        )
        mt5.symbol_info_tick.return_value = SimpleNamespace(bid=1.1, ask=1.1001)
        mt5.history_deals_get.return_value = []
        mt5.positions_get.return_value = []
        mt5.order_check.return_value = None
        mt5.last_error.return_value = (-2, "Terminal failure")

        with patch("app._mt5", return_value=mt5):
            with self.assertRaises(HTTPException) as error:
                execute(ConfirmationRequest(
                    proposal_id=self.proposal_id,
                    mode="demo",
                    confirmed=True,
                ))

        self.assertEqual(error.exception.status_code, 502)
        self.assertIn("Terminal failure", error.exception.detail)
        self.assertIn("no order was submitted", error.exception.detail)
        mt5.order_send.assert_not_called()
        mt5.shutdown.assert_called_once()

    def test_automatic_live_submission_is_rejected_before_mt5_access(self):
        with patch("app._mt5") as get_mt5:
            with self.assertRaises(HTTPException) as error:
                execute(ConfirmationRequest(
                    proposal_id=self.proposal_id,
                    mode="live",
                    confirmed=False,
                    auto_submit=True,
                ))

        self.assertEqual(error.exception.status_code, 400)
        get_mt5.assert_not_called()

    def test_automatic_demo_blocks_a_seventh_open_position(self):
        mt5 = Mock()
        mt5.ACCOUNT_TRADE_MODE_DEMO = 0
        mt5.ACCOUNT_TRADE_MODE_REAL = 2
        mt5.account_info.return_value = SimpleNamespace(
            login=42,
            trade_mode=0,
            equity=10000,
        )
        mt5.symbol_info.return_value = SimpleNamespace(visible=True)
        mt5.symbol_info_tick.return_value = SimpleNamespace(bid=1.1, ask=1.1001)
        mt5.positions_get.return_value = [
            SimpleNamespace(ticket=ticket) for ticket in range(6)
        ]

        with patch("app._mt5", return_value=mt5):
            with self.assertRaises(HTTPException) as error:
                execute(ConfirmationRequest(
                    proposal_id=self.proposal_id,
                    mode="demo",
                    confirmed=False,
                    auto_submit=True,
                ))

        self.assertEqual(error.exception.status_code, 409)
        self.assertIn("maximum of 6 open positions", error.exception.detail)
        mt5.order_send.assert_not_called()
        mt5.shutdown.assert_called_once()

    def test_manual_demo_approval_blocks_a_seventh_open_position(self):
        mt5 = Mock()
        mt5.ACCOUNT_TRADE_MODE_DEMO = 0
        mt5.ACCOUNT_TRADE_MODE_REAL = 2
        mt5.ORDER_TYPE_BUY = 0
        mt5.ORDER_TYPE_SELL = 1
        mt5.TRADE_ACTION_DEAL = 1
        mt5.ORDER_TIME_GTC = 0
        mt5.SYMBOL_FILLING_IOC = 2
        mt5.SYMBOL_FILLING_FOK = 1
        mt5.SYMBOL_TRADE_EXECUTION_MARKET = 2
        mt5.ORDER_FILLING_IOC = 1
        mt5.ORDER_FILLING_FOK = 0
        mt5.ORDER_FILLING_RETURN = 2
        mt5.TRADE_RETCODE_DONE = 10009
        mt5.TRADE_RETCODE_PLACED = 10008
        mt5.account_info.return_value = SimpleNamespace(
            login=42,
            trade_mode=0,
            equity=10000,
        )
        mt5.symbol_info.return_value = SimpleNamespace(
            visible=True,
            filling_mode=3,
            trade_exemode=2,
        )
        mt5.symbol_info_tick.return_value = SimpleNamespace(bid=1.1, ask=1.1001)
        mt5.history_deals_get.return_value = []
        mt5.positions_get.return_value = [SimpleNamespace(ticket=ticket) for ticket in range(6)]
        mt5.order_check.return_value = SimpleNamespace(retcode=0, comment="Done")
        mt5.order_send.return_value = SimpleNamespace(retcode=10009, order=123, deal=456)

        with patch("app._mt5", return_value=mt5):
            with self.assertRaises(HTTPException) as error:
                execute(ConfirmationRequest(
                    proposal_id=self.proposal_id,
                    mode="demo",
                    confirmed=True,
                ))

        self.assertEqual(error.exception.status_code, 409)
        self.assertIn("maximum of 6 open positions", error.exception.detail)
        mt5.positions_get.assert_called_once()
        mt5.order_send.assert_not_called()
        mt5.shutdown.assert_called_once()

    def test_daily_loss_limit_includes_proposed_order_risk(self):
        mt5 = Mock()
        mt5.ACCOUNT_TRADE_MODE_DEMO = 0
        mt5.ACCOUNT_TRADE_MODE_REAL = 2
        mt5.account_info.return_value = SimpleNamespace(
            login=42,
            trade_mode=0,
            equity=10000,
        )
        mt5.symbol_info.return_value = SimpleNamespace(visible=True)
        mt5.symbol_info_tick.return_value = SimpleNamespace(bid=1.0999, ask=1.1001)
        mt5.positions_get.return_value = []
        mt5.history_deals_get.return_value = [
            SimpleNamespace(profit=-150, swap=0, commission=0),
        ]

        with patch("app._mt5", return_value=mt5):
            with self.assertRaises(HTTPException) as error:
                execute(ConfirmationRequest(
                    proposal_id=self.proposal_id,
                    mode="demo",
                    confirmed=True,
                    daily_loss_limit=2,
                ))

        self.assertEqual(error.exception.status_code, 409)
        self.assertIn("daily loss limit", error.exception.detail)
        mt5.order_send.assert_not_called()
        mt5.shutdown.assert_called_once()


if __name__ == "__main__":
    unittest.main()
