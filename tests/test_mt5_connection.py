import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException

from app import MT5ConnectRequest, app, connect_mt5


class MT5ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.credentials_state = patch.object(app.state, "mt5_credentials", None)
        self.credentials_state.start()
        self.addCleanup(self.credentials_state.stop)
        self.environment = patch.dict(os.environ, {
            "MT5_PATH": "C:\\terminal64.exe",
            "MT5_LOGIN": "",
            "MT5_PASSWORD": "",
            "MT5_SERVER": "",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_connect_uses_terminal_path_and_keeps_credentials_in_memory(self):
        fake_mt5 = SimpleNamespace(
            ACCOUNT_TRADE_MODE_DEMO=0,
            ACCOUNT_TRADE_MODE_REAL=2,
            initialize=Mock(return_value=True),
            account_info=Mock(return_value=SimpleNamespace(
                login=12345,
                server="Broker-Demo",
                trade_mode=0,
            )),
            shutdown=Mock(),
            last_error=Mock(return_value=(0, "OK")),
        )
        credentials = MT5ConnectRequest(
            account_type="demo",
            login=12345,
            password="secret",
            server="Broker-Demo",
        )

        with patch.dict(sys.modules, {"MetaTrader5": fake_mt5}):
            result = connect_mt5(credentials)

        fake_mt5.initialize.assert_called_once_with(
            path="C:\\terminal64.exe",
            login=12345,
            password="secret",
            server="Broker-Demo",
        )
        self.assertEqual(app.state.mt5_credentials, (12345, "secret", "Broker-Demo"))
        self.assertTrue(result["connected"])
        self.assertEqual(result["trade_mode"], "demo")
        fake_mt5.shutdown.assert_called_once()

    def test_live_login_replaces_demo_credentials_after_mode_check(self):
        app.state.mt5_credentials = (11111, "old-secret", "Broker-Demo")
        fake_mt5 = SimpleNamespace(
            ACCOUNT_TRADE_MODE_DEMO=0,
            ACCOUNT_TRADE_MODE_REAL=2,
            initialize=Mock(return_value=True),
            account_info=Mock(return_value=SimpleNamespace(
                login=67890,
                server="Broker-Live",
                trade_mode=2,
            )),
            shutdown=Mock(),
            last_error=Mock(return_value=(0, "OK")),
        )
        request = MT5ConnectRequest(
            account_type="live",
            login=67890,
            password="new-secret",
            server="Broker-Live",
        )

        with patch.dict(sys.modules, {"MetaTrader5": fake_mt5}):
            result = connect_mt5(request)

        self.assertEqual(app.state.mt5_credentials, (67890, "new-secret", "Broker-Live"))
        self.assertEqual(result["trade_mode"], "live")
        fake_mt5.shutdown.assert_called_once()

    def test_account_type_mismatch_does_not_replace_current_credentials(self):
        current_credentials = (11111, "old-secret", "Broker-Demo")
        app.state.mt5_credentials = current_credentials
        fake_mt5 = SimpleNamespace(
            ACCOUNT_TRADE_MODE_DEMO=0,
            ACCOUNT_TRADE_MODE_REAL=2,
            initialize=Mock(return_value=True),
            account_info=Mock(return_value=SimpleNamespace(
                login=67890,
                server="Broker-Demo",
                trade_mode=0,
            )),
            shutdown=Mock(),
            last_error=Mock(return_value=(0, "OK")),
        )
        request = MT5ConnectRequest(
            account_type="live",
            login=67890,
            password="secret",
            server="Broker-Demo",
        )

        with patch.dict(sys.modules, {"MetaTrader5": fake_mt5}):
            with self.assertRaises(HTTPException) as error:
                connect_mt5(request)

        self.assertEqual(error.exception.status_code, 401)
        self.assertIn("confirmed a demo account", error.exception.detail)
        self.assertEqual(app.state.mt5_credentials, current_credentials)
        fake_mt5.shutdown.assert_called_once()

    def test_rejected_login_is_not_retained(self):
        fake_mt5 = SimpleNamespace(
            initialize=Mock(return_value=False),
            account_info=Mock(),
            shutdown=Mock(),
            last_error=Mock(return_value=(-6, "Authorization failed")),
        )
        credentials = MT5ConnectRequest(
            account_type="demo",
            login=12345,
            password="wrong",
            server="Broker-Demo",
        )

        with patch.dict(sys.modules, {"MetaTrader5": fake_mt5}):
            with self.assertRaises(HTTPException) as error:
                connect_mt5(credentials)

        self.assertEqual(error.exception.status_code, 401)
        self.assertIsNone(app.state.mt5_credentials)
        fake_mt5.shutdown.assert_called_once()


if __name__ == "__main__":
    unittest.main()
