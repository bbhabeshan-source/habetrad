from math import sqrt


STRATEGIES = {
    "ema_trend": "EMA 20/50 crossover",
    "rsi_bollinger": "RSI + Bollinger mean reversion",
    "donchian_breakout": "Donchian 20-bar breakout",
    "structure_break": "Swing break of structure",
    "ict_price_action": "ICT-inspired liquidity sweep + fair-value gap",
}


def _ema(values, period):
    alpha = 2 / (period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append(alpha * value + (1 - alpha) * result[-1])
    return result


def _rsi(values, period=14):
    changes = [values[index] - values[index - 1] for index in range(1, len(values))]
    recent = changes[-period:]
    gains = sum(max(change, 0) for change in recent) / period
    losses = sum(max(-change, 0) for change in recent) / period
    return 100.0 if losses == 0 else 100 - 100 / (1 + gains / losses)


def _atr(bars, period=14):
    ranges = []
    for index, bar in enumerate(bars):
        previous_close = bars[index - 1]["close"] if index else bar["close"]
        ranges.append(max(
            bar["high"] - bar["low"],
            abs(bar["high"] - previous_close),
            abs(bar["low"] - previous_close),
        ))
    return sum(ranges[-period:]) / period


def _confirmed_swings(bars):
    highs = []
    lows = []
    for index in range(2, len(bars) - 2):
        window = bars[index - 2:index + 3]
        if bars[index]["high"] == max(bar["high"] for bar in window):
            highs.append(bars[index]["high"])
        if bars[index]["low"] == min(bar["low"] for bar in window):
            lows.append(bars[index]["low"])
    return (highs[-1] if highs else None, lows[-1] if lows else None)


def generate_signals(bars, selected_strategies):
    closed = bars[:-1] if len(bars) > 1 else bars
    if len(closed) < 52:
        return []

    signals = []

    def add(strategy, side, reason):
        if strategy in selected_strategies:
            signals.append({"strategy": strategy, "label": STRATEGIES[strategy], "side": side, "reason": reason})

    closes = [bar["close"] for bar in closed]
    current = closed[-1]
    previous = closed[-2]
    atr = _atr(closed)
    if atr <= 0:
        return []

    if "ema_trend" in selected_strategies:
        fast = _ema(closes, 20)
        slow = _ema(closes, 50)
        if fast[-2] <= slow[-2] and fast[-1] > slow[-1]:
            add("ema_trend", "buy", "EMA 20 crossed above EMA 50 on the last closed bar.")
        elif fast[-2] >= slow[-2] and fast[-1] < slow[-1]:
            add("ema_trend", "sell", "EMA 20 crossed below EMA 50 on the last closed bar.")

    if "rsi_bollinger" in selected_strategies:
        recent = closes[-20:]
        average = sum(recent) / len(recent)
        deviation = sqrt(sum((value - average) ** 2 for value in recent) / len(recent))
        rsi = _rsi(closes)
        if current["close"] < average - 2 * deviation and rsi < 30:
            add("rsi_bollinger", "buy", f"Close below lower Bollinger band; RSI {rsi:.1f}.")
        elif current["close"] > average + 2 * deviation and rsi > 70:
            add("rsi_bollinger", "sell", f"Close above upper Bollinger band; RSI {rsi:.1f}.")

    if "donchian_breakout" in selected_strategies:
        channel = closed[-21:-1]
        if channel:
            upper = max(bar["high"] for bar in channel)
            lower = min(bar["low"] for bar in channel)
            if current["close"] > upper:
                add("donchian_breakout", "buy", "Last close broke above the prior 20-bar high.")
            elif current["close"] < lower:
                add("donchian_breakout", "sell", "Last close broke below the prior 20-bar low.")

    if "structure_break" in selected_strategies:
        swing_high, swing_low = _confirmed_swings(closed[:-1])
        if swing_high is not None and previous["close"] <= swing_high < current["close"]:
            add("structure_break", "buy", f"Close broke above confirmed swing high {swing_high:g}.")
        elif swing_low is not None and previous["close"] >= swing_low > current["close"]:
            add("structure_break", "sell", f"Close broke below confirmed swing low {swing_low:g}.")

    if "ict_price_action" in selected_strategies and len(closed) >= 22:
        liquidity = closed[-21:-1]
        low_pool = min(bar["low"] for bar in liquidity)
        high_pool = max(bar["high"] for bar in liquidity)
        bullish_gap = current["low"] > closed[-3]["high"]
        bearish_gap = current["high"] < closed[-3]["low"]
        displacement = max(atr * 0.5, 0)
        body = abs(current["close"] - current["open"])
        if current["low"] < low_pool and current["close"] > low_pool and current["close"] > current["open"] and bullish_gap and body >= displacement:
            add("ict_price_action", "buy", "Simplified ICT-inspired sweep below prior liquidity, bullish displacement, and fair-value gap.")
        elif current["high"] > high_pool and current["close"] < high_pool and current["close"] < current["open"] and bearish_gap and body >= displacement:
            add("ict_price_action", "sell", "Simplified ICT-inspired sweep above prior liquidity, bearish displacement, and fair-value gap.")

    return signals
