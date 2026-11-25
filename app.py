import os
import threading
import uuid
from datetime import datetime, timezone
from math import floor
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from trading import STRATEGIES, generate_signals

APP_DIR = Path(__file__).resolve().parent
TIMEFRAME_NAMES = (
    "M1", "M2", "M3", "M4", "M5", "M6", "M10", "M12", "M15",
    "M20", "M30", "H1", "H2", "H3", "H4", "H6", "H8", "H12", "D1",
)
FOCUS_SYMBOL_ORDER = ("XAUUSD", "BTCUSD", "GBPUSD", "EURUSD", "GBPJPY", "ETHUSD")
FOCUS_SYMBOL_PREFIXES = {
    "XAUUSD": ("XAUUSD", "GOLD"),
    "BTCUSD": ("BTCUSD", "XBTUSD"),
    "GBPUSD": ("GBPUSD",),
    "EURUSD": ("EURUSD",),
    "GBPJPY": ("GBPJPY",),
    "ETHUSD": ("ETHUSD",),
}
FOCUS_SYMBOL_LABELS = {
    "XAUUSD": "Gold",
    "BTCUSD": "Bitcoin",
    "GBPUSD": "GBP/USD",
    "EURUSD": "EUR/USD",
    "GBPJPY": "GBP/JPY",
    "ETHUSD": "Ethereum",
}
app = FastAPI(title="Hub Trading Assistant")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
app.state.mt5_credentials = None
app.state.proposals = {}
app.state.auto_demo_attempts = {}
AUTO_DEMO_LOCK = threading.Lock()


def _focus_instrument(symbol):
    candidate = str(symbol).strip()
    normalized = candidate.upper()
    for instrument in FOCUS_SYMBOL_ORDER:
        for prefix in FOCUS_SYMBOL_PREFIXES[instrument]:
            if normalized == prefix:
                return instrument
            if normalized.startswith(prefix):
                suffix = candidate[len(prefix):]
                if suffix and (suffix[0].islower() or suffix[0] in "._-#"):
                    return instrument
    return None


def _require_focus_symbol(symbol):
    candidate = str(symbol).strip()
    if _focus_instrument(candidate) is None:
        raise HTTPException(
            status_code=422,
            detail="Choose Gold, Bitcoin, GBP/USD, EUR/USD, GBP/JPY, or Ethereum.",
        )
    return candidate


def _fallback_symbol_items():
    return [
        {
            "symbol": instrument,
            "description": FOCUS_SYMBOL_LABELS[instrument],
            "instrument": instrument,
        }
        for instrument in FOCUS_SYMBOL_ORDER
    ]


class AnalysisRequest(BaseModel):
    symbol: str = Field(default="XAUUSD", min_length=1, max_length=32)
    timeframe: str = Field(default="H1", min_length=2, max_length=3)
    count: int = Field(default=1000, ge=120, le=2000)
    shift: int = Field(default=0, ge=0, le=10000)
    strategies: list[Literal["ema_trend", "rsi_bollinger", "donchian_breakout", "structure_break", "ict_price_action"]] = Field(
        default_factory=lambda: list(STRATEGIES)
    )
    risk_percent: float = Field(default=1, ge=1, le=100)


class ConfirmationRequest(BaseModel):
    proposal_id: str = Field(min_length=1, max_length=64)
    mode: Literal["paper", "demo", "live"]
    confirmed: bool
    auto_submit: bool = False
    daily_loss_limit: float = Field(default=2, ge=0.5, le=5)


class PaperOrderRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    side: Literal["buy", "sell"]
    volume: float = Field(gt=0, le=10)
    entry: float = Field(gt=0)
    stop_loss: float = Field(gt=0)
    take_profit: float = Field(gt=0)
    confirmed: bool


class MT5ConnectRequest(BaseModel):
    account_type: Literal["demo", "live"]
    login: int = Field(gt=0)
    password: str = Field(min_length=1, max_length=256)
    server: str = Field(min_length=1, max_length=128)


def _mt5(credentials_override=None):
    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="MetaTrader5 is unavailable. Install requirements and run the MT5 terminal.",
        ) from exc

    credentials = credentials_override or app.state.mt5_credentials or (
        os.getenv("MT5_LOGIN"),
        os.getenv("MT5_PASSWORD"),
        os.getenv("MT5_SERVER"),
    )
    if any(credentials) and not all(credentials):
        raise HTTPException(
            status_code=503,
            detail="MT5_LOGIN, MT5_PASSWORD, and MT5_SERVER must be set together.",
        )
    initialize_options = {}
    terminal_path = os.getenv("MT5_PATH")
    if terminal_path:
        initialize_options["path"] = terminal_path
    if all(credentials):
        initialize_options.update({
            "login": int(credentials[0]),
            "password": credentials[1],
            "server": credentials[2],
        })
    initialized = mt5.initialize(**initialize_options)
    if not initialized:
        error = mt5.last_error()
        mt5.shutdown()
        raise HTTPException(
            status_code=503,
            detail=f"Could not connect to MetaTrader 5: {error}",
        )
    return mt5


def _timeframe_constant(mt5, name: str):
    normalized = name.upper()
    if normalized not in TIMEFRAME_NAMES:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported timeframe. Choose one of: {', '.join(TIMEFRAME_NAMES)}",
        )
    constant = getattr(mt5, f"TIMEFRAME_{normalized}", None)
    if constant is None:
        raise HTTPException(status_code=422, detail=f"MT5 does not support {normalized}")
    return normalized, constant


def _market_rates(symbol: str, timeframe: str, count: int, shift: int = 0):
    mt5 = _mt5()
    try:
        normalized_timeframe, timeframe_constant = _timeframe_constant(mt5, timeframe)
        info = mt5.symbol_info(symbol)
        if info is None:
            raise HTTPException(status_code=404, detail=f"{symbol} is not available on this broker")
        if not info.visible and not mt5.symbol_select(symbol, True):
            raise HTTPException(status_code=503, detail=f"Could not select broker symbol {symbol}")

        rates = mt5.copy_rates_from_pos(symbol, timeframe_constant, shift, count)
        tick = mt5.symbol_info_tick(symbol)
        if rates is None or len(rates) == 0:
            raise HTTPException(
                status_code=503,
                detail=f"No {normalized_timeframe} price history for {symbol}. Check broker history/download.",
            )
        if tick is None:
            raise HTTPException(status_code=503, detail=f"No live quote available for {symbol}")

        bars = [
            {
                "time": int(row["time"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "tick_volume": int(row["tick_volume"]),
            }
            for row in rates
        ]
        return {
            "mt5": mt5,
            "timeframe": normalized_timeframe,
            "bars": bars,
            "tick": {"bid": float(tick.bid), "ask": float(tick.ask)},
            "digits": int(info.digits),
            "symbol_info": info,
            "account": mt5.account_info(),
        }
    except Exception:
        mt5.shutdown()
        raise


def _indicators(bars):
    closes = [bar["close"] for bar in bars]
    latest = bars[-2] if len(bars) > 1 else bars[-1]
    previous = bars[-3] if len(bars) > 2 else None
    sma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
    sma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else None

    changes = [closes[index] - closes[index - 1] for index in range(1, len(closes))]
    rsi = None
    if len(changes) >= 14:
        recent = changes[-14:]
        average_gain = sum(max(change, 0) for change in recent) / 14
        average_loss = sum(max(-change, 0) for change in recent) / 14
        rsi = 100.0 if average_loss == 0 else 100 - 100 / (1 + average_gain / average_loss)

    true_ranges = []
    for index, bar in enumerate(bars):
        prior_close = bars[index - 1]["close"] if index else bar["close"]
        true_ranges.append(max(
            bar["high"] - bar["low"],
            abs(bar["high"] - prior_close),
            abs(bar["low"] - prior_close),
        ))
    atr14 = sum(true_ranges[-14:]) / 14 if len(true_ranges) >= 14 else None

    if sma20 is None or sma50 is None:
        trend = "Insufficient history for trend averages"
    elif sma20 > sma50:
        trend = "Uptrend (SMA 20 above SMA 50)"
    elif sma20 < sma50:
        trend = "Downtrend (SMA 20 below SMA 50)"
    else:
        trend = "Sideways (SMA 20 equals SMA 50)"

    return {
        "trend": trend,
        "last_closed_bar": latest,
        "previous_close": previous["close"] if previous else None,
        "sma20": sma20,
        "sma50": sma50,
        "rsi14": rsi,
        "atr14": atr14,
        "range20_high": max(bar["high"] for bar in bars[-20:]),
        "range20_low": min(bar["low"] for bar in bars[-20:]),
    }


def _status():
    mt5 = _mt5()
    try:
        account = mt5.account_info()
        if account is None:
            return {
                "connected": False,
                "reason": "MT5 terminal is reachable, but no trading account is signed in.",
            }
        return {
            "connected": True,
            "login": account.login,
            "server": account.server,
            "balance": account.balance,
            "equity": account.equity,
            "currency": account.currency,
            "trade_mode": (
                "demo" if account.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO
                else "live" if account.trade_mode == mt5.ACCOUNT_TRADE_MODE_REAL
                else "contest"
            ),
        }
    finally:
        mt5.shutdown()


@app.get("/")
def index():
    return FileResponse(APP_DIR / "static" / "index.html")


@app.get("/api/status")
def status():
    try:
        mt5_status = _status()
    except HTTPException as error:
        mt5_status = {"connected": False, "reason": error.detail}
    return {"mt5": mt5_status, "server_time": datetime.now(timezone.utc).isoformat()}


@app.get("/api/quote")
def live_quote(symbol: str, timeframe: str):
    symbol = _require_focus_symbol(symbol)
    mt5 = _mt5()
    try:
        normalized_timeframe, timeframe_constant = _timeframe_constant(mt5, timeframe)
        info = mt5.symbol_info(symbol)
        if info is None:
            raise HTTPException(status_code=404, detail=f"{symbol} is not available on this broker")
        if not info.visible and not mt5.symbol_select(symbol, True):
            raise HTTPException(status_code=503, detail=f"Could not select broker symbol {symbol}")

        tick = mt5.symbol_info_tick(symbol)
        rates = mt5.copy_rates_from_pos(symbol, timeframe_constant, 0, 1)
        if tick is None:
            raise HTTPException(status_code=503, detail=f"No live quote available for {symbol}")
        if rates is None or len(rates) == 0:
            raise HTTPException(status_code=503, detail=f"No live {normalized_timeframe} candle available for {symbol}")

        row = rates[-1]
        current_bar = {
            "time": int(row["time"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "tick_volume": int(row["tick_volume"]),
        }
        account_info = mt5.account_info()
        account = None
        if account_info is not None:
            account_trade_mode = (
                "demo" if account_info.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO
                else "live" if account_info.trade_mode == mt5.ACCOUNT_TRADE_MODE_REAL
                else "contest"
            )
            account = {
                "login": account_info.login,
                "server": account_info.server,
                "balance": float(account_info.balance),
                "equity": float(account_info.equity),
                "currency": account_info.currency,
                "trade_mode": account_trade_mode,
            }
        return {
            "symbol": symbol,
            "timeframe": normalized_timeframe,
            "server_time": datetime.now(timezone.utc).isoformat(),
            "digits": int(info.digits),
            "quote": {"bid": float(tick.bid), "ask": float(tick.ask)},
            "spread": float(tick.ask) - float(tick.bid),
            "current_bar": current_bar,
            "account": account,
        }
    finally:
        mt5.shutdown()


@app.post("/api/connect")
def connect_mt5(request: MT5ConnectRequest):
    credentials = (request.login, request.password, request.server)
    try:
        mt5 = _mt5(credentials)
    except HTTPException as error:
        raise HTTPException(
            status_code=401,
            detail="MT5 rejected the connection. Check the account number, password, broker server, and terminal path.",
        ) from error

    try:
        account = mt5.account_info()
        if account is None or account.login != request.login:
            raise HTTPException(
                status_code=401,
                detail="MT5 did not confirm this account. Check the account number, password, and broker server.",
            )
        actual_mode = (
            "demo" if account.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO
            else "live" if account.trade_mode == mt5.ACCOUNT_TRADE_MODE_REAL
            else "contest"
        )
        if actual_mode != request.account_type:
            raise HTTPException(
                status_code=401,
                detail=f"The broker confirmed a {actual_mode} account, but {request.account_type} was selected. Choose the matching account type.",
            )
        app.state.mt5_credentials = credentials
        return {
            "connected": True,
            "login": account.login,
            "server": account.server,
            "trade_mode": actual_mode,
            "message": f"Connected to the MetaTrader 5 {actual_mode} account. Credentials stay in memory until the app stops.",
        }
    finally:
        mt5.shutdown()


@app.get("/api/symbols")
def symbols():
    try:
        mt5 = _mt5()
    except HTTPException as error:
        return {
            "connected": False,
            "reason": error.detail,
            "source": "focus markets; broker list unavailable",
            "symbols": _fallback_symbol_items(),
        }

    try:
        broker_symbols = mt5.symbols_get()
        if broker_symbols is None:
            return {
                "connected": False,
                "reason": "MT5 did not return its symbol list.",
                "source": "focus markets; broker list unavailable",
                "symbols": _fallback_symbol_items(),
            }
        if not broker_symbols:
            return {
                "connected": False,
                "reason": "MT5 returned no broker symbols. Sign in to an account and enable symbols in Market Watch.",
                "source": "focus markets; broker list unavailable",
                "symbols": _fallback_symbol_items(),
            }
        found = {}
        for item in broker_symbols:
            instrument = _focus_instrument(item.name)
            if instrument is None:
                continue
            symbol = item.name
            primary_prefix = FOCUS_SYMBOL_PREFIXES[instrument][0]
            rank = (
                0 if symbol.upper().startswith(primary_prefix) else 1,
                len(symbol),
                symbol.upper(),
            )
            previous = found.get(instrument)
            if previous is None or rank < previous[0]:
                found[instrument] = (rank, symbol)
        if not found:
            return {
                "connected": False,
                "reason": "None of the selected markets were found. Enable Gold, Bitcoin, GBP/USD, EUR/USD, GBP/JPY, and Ethereum in Market Watch.",
                "source": "focus markets; broker list unavailable",
                "symbols": _fallback_symbol_items(),
            }
        selected = [
            {
                "symbol": found[instrument][1],
                "description": FOCUS_SYMBOL_LABELS[instrument],
                "instrument": instrument,
            }
            for instrument in FOCUS_SYMBOL_ORDER
            if instrument in found
        ]
        return {"connected": True, "source": "Selected MetaTrader 5 markets", "symbols": selected}
    finally:
        mt5.shutdown()


@app.post("/api/analyze")
def analyze(request: AnalysisRequest):
    symbol = _require_focus_symbol(request.symbol)
    data = _market_rates(symbol, request.timeframe, request.count, request.shift)
    try:
        now = datetime.now(timezone.utc).timestamp()
        app.state.proposals = {
            key: value
            for key, value in app.state.proposals.items()
            if now - value["created_at"] <= 300
        }
        with AUTO_DEMO_LOCK:
            app.state.auto_demo_attempts = {
                key: expiry
                for key, expiry in app.state.auto_demo_attempts.items()
                if expiry > now
            }
        bars = data["bars"]
        indicators = _indicators(bars)
        closed = indicators["last_closed_bar"]
        previous_close = indicators["previous_close"]
        change_percent = (
            (closed["close"] - previous_close) / previous_close * 100
            if previous_close else None
        )
        spread = data["tick"]["ask"] - data["tick"]["bid"]
        account = data["account"]
        symbol_info = data["symbol_info"]
        equity = float(account.equity) if account is not None else 0
        signals = generate_signals(bars, request.strategies)
        proposals = []
        risk_budget = equity * request.risk_percent / 100
        sizing = {
            "available": False,
            "risk_budget": round(risk_budget, 2),
            "minimum_volume": None,
            "minimum_volume_risk": None,
            "minimum_risk_percent": None,
            "reason": "Account equity is unavailable.",
        }

        if account is not None and equity > 0 and indicators["atr14"]:
            tick_size = float(symbol_info.trade_tick_size or 0)
            tick_value = float(symbol_info.trade_tick_value_loss or symbol_info.trade_tick_value or 0)
            if tick_size > 0 and tick_value > 0:
                stop_distance = indicators["atr14"] * 1.5
                risk_per_lot = (stop_distance / tick_size) * tick_value
                volume_step = float(symbol_info.volume_step or 0.01)
                min_volume = float(symbol_info.volume_min or volume_step)
                max_volume = min(10.0, float(symbol_info.volume_max or 10))
                raw_volume = risk_budget / risk_per_lot if risk_per_lot else 0
                volume = floor(raw_volume / volume_step) * volume_step if volume_step > 0 else 0
                volume = round(min(volume, max_volume), 8)
                minimum_volume_risk = risk_per_lot * min_volume
                sizing = {
                    "available": volume >= min_volume,
                    "risk_budget": round(risk_budget, 2),
                    "minimum_volume": min_volume,
                    "minimum_volume_risk": round(minimum_volume_risk, 2),
                    "minimum_risk_percent": round(minimum_volume_risk / equity * 100, 2),
                    "reason": (
                        f"The broker's minimum volume ({min_volume:g} lots) risks about "
                        f"{minimum_volume_risk:.2f} {account.currency} "
                        f"({minimum_volume_risk / equity * 100:.2f}% of equity), above "
                        f"your {request.risk_percent:g}% risk cap."
                        if volume < min_volume
                        else ""
                    ),
                }

                if sizing["available"]:
                    for signal in signals:
                        entry = data["tick"]["ask"] if signal["side"] == "buy" else data["tick"]["bid"]
                        direction = 1 if signal["side"] == "buy" else -1
                        stop_loss = entry - direction * stop_distance
                        take_profit = entry + direction * stop_distance * 2
                        proposal_id = uuid.uuid4().hex
                        proposal = {
                            "proposal_id": proposal_id,
                            "symbol": symbol,
                            "strategy": signal["strategy"],
                            "label": signal["label"],
                            "reason": signal["reason"],
                            "side": signal["side"],
                            "entry": round(entry, data["digits"]),
                            "stop_loss": round(stop_loss, data["digits"]),
                            "take_profit": round(take_profit, data["digits"]),
                            "volume": volume,
                            "risk_amount": round(risk_per_lot * volume, 2),
                            "risk_percent": round(risk_per_lot * volume / equity * 100, 2),
                            "risk_limit_percent": request.risk_percent,
                            "equity": round(equity, 2),
                            "currency": account.currency if account else "",
                            "bar_time": int(indicators["last_closed_bar"]["time"]),
                            "expires_at": now + 300,
                        }
                        app.state.proposals[proposal_id] = {
                            **proposal,
                            "symbol": symbol,
                            "account_login": account.login,
                            "timeframe": data["timeframe"],
                            "created_at": now,
                            "risk_distance": stop_distance,
                        }
                        proposals.append(proposal)
            else:
                sizing["reason"] = "Broker tick-value data is unavailable, so a safe position size cannot be calculated."
        elif not indicators["atr14"]:
            sizing["reason"] = "Not enough candle history to calculate volatility and position size."

        if signals and not proposals:
            for signal in signals:
                signal["proposal_error"] = sizing["reason"]

        trade_mode = None
        if account is not None:
            if account.trade_mode == data["mt5"].ACCOUNT_TRADE_MODE_DEMO:
                trade_mode = "demo"
            elif account.trade_mode == data["mt5"].ACCOUNT_TRADE_MODE_REAL:
                trade_mode = "live"
        if proposals:
            trade_status = f"{len(proposals)} strategy proposal(s) ready for review."
        elif not sizing["available"]:
            trade_status = f"No order fits the current risk settings: {sizing['reason']}"
        elif signals:
            trade_status = "A setup matched, but no order proposal could be created."
        else:
            trade_status = "Monitoring selected strategies; no entry condition currently matches."
        return {
            "symbol": symbol,
            "timeframe": data["timeframe"],
            "source": "MetaTrader 5",
            "server_time": datetime.now(timezone.utc).isoformat(),
            "quote": data["tick"],
            "spread": spread,
            "digits": data["digits"],
            "indicators": indicators,
            "change_percent": change_percent,
            "bars": bars,
            "signals": signals,
            "proposals": proposals,
            "account": {
                "equity": equity,
                "currency": account.currency if account else "",
                "trade_mode": trade_mode,
            },
            "sizing": sizing,
            "news": {
                "available": False,
                "message": "No economic calendar is connected. Check scheduled news separately.",
            },
            "trade_status": trade_status,
        }
    finally:
        data["mt5"].shutdown()


@app.post("/api/execute")
def execute(request: ConfirmationRequest):
    if request.auto_submit and request.mode != "demo":
        raise HTTPException(status_code=400, detail="Automatic submission is available only for MT5 demo mode.")
    if not request.confirmed and not request.auto_submit:
        raise HTTPException(status_code=400, detail="Confirm the order in the terminal before submitting.")
    proposal = app.state.proposals.get(request.proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="Proposal expired or was not found. Run analysis again.")
    if datetime.now(timezone.utc).timestamp() - proposal["created_at"] > 300:
        app.state.proposals.pop(request.proposal_id, None)
        raise HTTPException(status_code=410, detail="Proposal expired. Run analysis again for a fresh quote.")

    if request.mode == "paper":
        app.state.proposals.pop(request.proposal_id, None)
        return {
            "status": "paper_fill",
            **{key: proposal[key] for key in (
                "symbol", "side", "volume", "entry", "stop_loss", "take_profit",
                "risk_amount", "currency", "label",
            )},
            "message": "Paper order simulated. No broker order was submitted.",
        }

    mt5 = _mt5()
    try:
        account = mt5.account_info()
        if account is None or account.login != proposal["account_login"]:
            raise HTTPException(status_code=409, detail="The connected MT5 account changed. Run analysis again.")
        expected_trade_mode = (
            mt5.ACCOUNT_TRADE_MODE_DEMO if request.mode == "demo"
            else mt5.ACCOUNT_TRADE_MODE_REAL
        )
        if account.trade_mode != expected_trade_mode:
            mode_name = "demo" if request.mode == "demo" else "live"
            raise HTTPException(
                status_code=409,
                detail=f"Selected {mode_name} mode does not match the currently connected MT5 account.",
            )
        symbol_info = mt5.symbol_info(proposal["symbol"])
        if symbol_info is None:
            raise HTTPException(status_code=404, detail="Broker symbol is no longer available.")
        if not symbol_info.visible and not mt5.symbol_select(proposal["symbol"], True):
            raise HTTPException(status_code=503, detail="Could not select broker symbol.")

        tick = mt5.symbol_info_tick(proposal["symbol"])
        if tick is None:
            raise HTTPException(status_code=503, detail="No current broker quote; order not submitted.")
        price = tick.ask if proposal["side"] == "buy" else tick.bid
        if abs(price - proposal["entry"]) > proposal["risk_distance"] * 0.25:
            raise HTTPException(
                status_code=409,
                detail="Price moved materially since the proposal. Re-analyze and review the new setup.",
            )

        if request.auto_submit:
            positions = mt5.positions_get()
            if positions is None:
                raise HTTPException(status_code=503, detail="Could not verify open positions; automatic order not submitted.")
            if positions:
                raise HTTPException(
                    status_code=409,
                    detail="Automatic demo trading allows only one open position at a time.",
                )

        day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        deals = mt5.history_deals_get(day_start, datetime.now(timezone.utc))
        if deals is None:
            raise HTTPException(status_code=503, detail="Could not read today's MT5 deal history; order not submitted.")
        realized_today = sum(
            float(deal.profit) + float(deal.swap) + float(deal.commission)
            for deal in deals
        )
        daily_loss_cap = float(account.equity) * request.daily_loss_limit / 100
        if realized_today - float(proposal["risk_amount"]) <= -daily_loss_cap:
            raise HTTPException(
                status_code=409,
                detail=f"This order could reach the daily loss limit ({request.daily_loss_limit:g}% of current equity).",
            )

        if account.equity < proposal["equity"] * 0.95:
            raise HTTPException(
                status_code=409,
                detail="Account equity changed by more than 5% since analysis. Re-analyze before trading.",
            )

        signal_key = (
            proposal["symbol"],
            proposal["timeframe"],
            proposal["bar_time"],
        )
        if request.auto_submit:
            with AUTO_DEMO_LOCK:
                if app.state.auto_demo_attempts.get(signal_key, 0) > datetime.now(timezone.utc).timestamp():
                    raise HTTPException(
                        status_code=409,
                        detail="An automatic order was already attempted for this market candle.",
                    )
                app.state.auto_demo_attempts[signal_key] = datetime.now(timezone.utc).timestamp() + 300

        order_type = mt5.ORDER_TYPE_BUY if proposal["side"] == "buy" else mt5.ORDER_TYPE_SELL
        result = mt5.order_send({
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": proposal["symbol"],
            "volume": proposal["volume"],
            "type": order_type,
            "price": price,
            "sl": proposal["stop_loss"],
            "tp": proposal["take_profit"],
            "deviation": 20,
            "magic": 20261125,
            "comment": proposal["strategy"][:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        })
        if result is None or result.retcode not in (
            mt5.TRADE_RETCODE_DONE,
            mt5.TRADE_RETCODE_PLACED,
        ):
            raise HTTPException(status_code=502, detail=f"Broker rejected order: {result}")

        app.state.proposals.pop(request.proposal_id, None)
        return {
            "status": "broker_order_submitted",
            "order": result.order,
            "deal": result.deal,
            "symbol": proposal["symbol"],
            "side": proposal["side"],
            "volume": proposal["volume"],
            "entry": price,
            "stop_loss": proposal["stop_loss"],
            "take_profit": proposal["take_profit"],
            "risk_amount": proposal["risk_amount"],
            "currency": proposal["currency"],
            "message": (
                "Demo order automatically submitted within the configured risk limits."
                if request.auto_submit
                else f"{request.mode.capitalize()} order submitted after confirmation."
            ),
        }
    finally:
        mt5.shutdown()


@app.post("/api/paper-order")
def paper_order(request: PaperOrderRequest):
    _require_focus_symbol(request.symbol)
    if not request.confirmed:
        raise HTTPException(status_code=400, detail="Confirm the paper order before recording it.")
    if request.side == "buy" and not request.stop_loss < request.entry < request.take_profit:
        raise HTTPException(status_code=422, detail="For a buy, stop loss must be below entry and take profit above it.")
    if request.side == "sell" and not request.take_profit < request.entry < request.stop_loss:
        raise HTTPException(status_code=422, detail="For a sell, take profit must be below entry and stop loss above it.")
    return {
        "status": "paper_fill",
        "symbol": request.symbol,
        "side": request.side,
        "volume": request.volume,
        "entry": request.entry,
        "stop_loss": request.stop_loss,
        "take_profit": request.take_profit,
        "message": "Paper order simulated. No broker order was submitted.",
    }
