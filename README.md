# Aurum Trading Terminal

A neon-styled FastAPI market terminal for broker-provided forex pairs and XAUUSD.
When MetaTrader 5 is authorized, it displays the broker's live bid/ask quote,
up to 1,000 historical candles, a candlestick chart, SMA 20/50, RSI 14, ATR 14,
and a 20-candle high/low range. Timeframes cover M1 through D1, including
standard intermediate MT5 intervals. The watchlist selects broker symbols when
connected; otherwise it shows a clearly labeled common-pairs fallback.
Drag the chart horizontally to pan through loaded history, scroll over it to
zoom, or double-click/reset the view to return to the newest candles.
The live broker quote and the current candle update approximately every two
seconds while MT5 is connected; the strategy scanner separately checks every
15 seconds while running.

Quotes and history are broker-specific and can differ between brokers. No fixed
or externally guessed gold price is used. If MT5 is disconnected, the dashboard
shows a common-pairs list but does not invent prices or candle data. The paper
desk is only enabled after a real broker quote is available; it simulates the
requested parameters and does not persist fills or send orders.

## Run locally

```powershell
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

Open `http://127.0.0.1:8000`.

## MetaTrader 5

Install and sign in to the desktop MT5 terminal. If automatic detection picks the
wrong terminal, set its executable path. Credentials can be configured locally if
the terminal does not retain a signed-in session:

```powershell
$env:MT5_PATH="C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe"
$env:MT5_LOGIN="your-account-number"
$env:MT5_PASSWORD="your-password"
$env:MT5_SERVER="your-broker-server"
python -m uvicorn app:app --reload
```

`MT5_PATH` and credentials are optional when automatic detection works and the
terminal is already signed in. The dashboard also provides a local connection
form; credentials entered there stay in server memory and are cleared from the
form after submission. Keep the app bound to `127.0.0.1` and never expose its
port publicly. Never paste broker passwords into chat or commit them to source
control. The broker's
available forex symbols are discovered from MT5 and may use broker suffixes. If
MT5 is unavailable, the UI offers a common-pairs fallback; market prices require
a successful MT5 authorization.

## Strategy scanner and order approval

The scanner checks the selected broker symbol and timeframe every 15 seconds
while running. Select one or more simplified rule sets: EMA crossover,
RSI/Bollinger mean reversion, Donchian breakout, swing break, and
ICT-inspired liquidity sweep/FVG. These are sample heuristics, not validated
systems; there is no promised win rate or profit. The economic calendar is not
connected, so check scheduled news separately.

When a rule matches, the app calculates an ATR-based stop and target and
estimates volume from the connected account's equity, the selected per-order
risk (configurable from 1% to 100%), and broker tick/volume settings. The
proposal shows estimated risk alongside the selected cap. The separate daily
loss stop remains capped at 5%; very high per-order risk can therefore be
blocked by that stop. The selected execution policy can require
confirmation for every order or automatically submit eligible orders while the
scanner is running on an MT5 demo account. Automatic submission is rejected for
live accounts; live orders require per-order confirmation. Paper mode only
simulates the order.
If the broker's minimum lot would exceed the selected risk cap, the app explains
the estimated minimum-lot risk and does not create an order.
MT5 demo/live submissions require the selected mode to match the connected
account, and the server checks quote drift, proposal age, equity changes, and
the daily realized-loss limit plus the proposed order's estimated risk. Automatic
demo orders are deduplicated by symbol, timeframe, and candle, so repeated scans
won't submit the same setup more than once, and only one open position is
allowed for automatic demo trading. These checks reduce some risks but
do not guarantee a maximum loss: gaps, slippage,
broker execution, currency conversion, and other open positions can change the
result. Test in paper or demo mode before considering any live order.

Automatic demo submission runs only while scanning and only when an order fits
the configured risk controls. The scanner only monitors the currently selected
symbol and timeframe; it does not fetch calendar news or guarantee detection of
every market condition. Keep the app bound to localhost and do not expose its
order endpoint publicly.
