let marketData = null;
let isConnected = false;
let selectedSide = "buy";
let visibleBars = 80;
let chartStart = 0;
let dragState = null;
let scanTimer = null;
let scanInProgress = false;
let scanRequested = false;
let quoteInProgress = false;
let orderInProgress = false;
let connectedTradeMode = null;
let isSwitchingAccount = false;
let accountGeneration = 0;
const pendingProposals = new Map();
const seenSignalKeys = new Set();

const statusElement = document.getElementById("connectionStatus");
const statusDot = document.querySelector(".live-dot");
const analyzeButton = document.getElementById("analyzeButton");
const connectForm = document.getElementById("connectForm");
const symbolInput = document.getElementById("symbol");
const timeframeInput = document.getElementById("timeframe");
const notice = document.getElementById("dataNotice");
const chart = document.getElementById("priceChart");
const paperButton = document.getElementById("paperButton");
const paperConfirm = document.getElementById("paperConfirm");
const executionMode = document.getElementById("executionMode");
const executionPolicy = document.getElementById("executionPolicy");
const accountTypeInput = document.getElementById("mt5AccountType");
const switchAccountButton = document.getElementById("switchAccountButton");
const cancelConnectButton = document.getElementById("cancelConnectButton");
const focusMarketFallback = [
  { symbol: "XAUUSD", description: "Gold", instrument: "XAUUSD" },
  { symbol: "BTCUSD", description: "Bitcoin", instrument: "BTCUSD" },
  { symbol: "GBPUSD", description: "GBP/USD", instrument: "GBPUSD" },
  { symbol: "EURUSD", description: "EUR/USD", instrument: "EURUSD" },
  { symbol: "GBPJPY", description: "GBP/JPY", instrument: "GBPJPY" },
  { symbol: "ETHUSD", description: "Ethereum", instrument: "ETHUSD" },
];

function showNotice(message) {
  notice.textContent = message;
  notice.hidden = !message;
}

function formatPrice(value, digits = 2) {
  return Number.isFinite(value) ? Number(value).toFixed(digits) : "—";
}

function formatAccountAmount(value, currency) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  const amount = Number(value).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return currency ? `${amount} ${currency}` : amount;
}

function setValue(id, value) {
  const element = document.getElementById(id);
  if (element) element.textContent = value == null ? "—" : String(value);
}

function syncConnectionControls() {
  connectForm.hidden = isConnected && !isSwitchingAccount;
  switchAccountButton.hidden = !isConnected || isSwitchingAccount;
  cancelConnectButton.hidden = !isConnected;
  document.getElementById("connectTitle").textContent = isConnected
    ? "Switch your MT5 account"
    : "Connect your MT5 account";
  updateConnectButtonLabel();
}

function updateConnectButtonLabel() {
  const accountType = accountTypeInput.value === "live" ? "LIVE" : "DEMO";
  const button = document.getElementById("connectButton");
  if (!button.disabled) button.textContent = `CONNECT ${accountType} ACCOUNT`;
}

function updateAccountSnapshot(account) {
  const connected = Boolean(account.connected);
  const snapshot = document.getElementById("accountSnapshot");
  snapshot.classList.toggle("connected", connected);
  snapshot.classList.toggle("disconnected", !connected);
  setValue("accountTypeValue", connected
    ? `${String(account.trade_mode || "account").toUpperCase()} ACCOUNT`
    : "DISCONNECTED");
  setValue("accountDetailsValue", connected
    ? `${account.login} · ${account.server}`
    : "CONNECT MT5 TO LOAD");
  setValue("balanceValue", connected ? formatAccountAmount(account.balance, account.currency) : "—");
  setValue("equityValue", connected ? formatAccountAmount(account.equity, account.currency) : "—");
}

function friendlyConnectionMessage(reason) {
  if (reason && reason.toLowerCase().includes("authorization failed")) {
    return "MT5 login failed. Sign in to your broker account in the MT5 desktop terminal, or configure MT5 credentials.";
  }
  return reason || "MT5 is not connected. Sign in to your broker account in the desktop terminal.";
}

async function loadStatus() {
  try {
    const response = await fetch("/api/status");
    const data = await response.json();
    isConnected = Boolean(data.mt5.connected);
    connectedTradeMode = data.mt5.trade_mode || null;
    if (isConnected && ["demo", "live"].includes(connectedTradeMode)) {
      accountTypeInput.value = connectedTradeMode;
      executionMode.value = connectedTradeMode;
    }
    syncExecutionPolicy();
    syncConnectionControls();
    statusElement.querySelector("strong").textContent = isConnected
      ? `MT5 CONNECTED · ${data.mt5.login || data.mt5.server || "BROKER"}`
      : "MT5 DISCONNECTED";
    statusDot.classList.toggle("connected", isConnected);
    updateAccountSnapshot(data.mt5);
    setValue(
      "accountBadge",
      isConnected
        ? `${String(connectedTradeMode || "ACCOUNT").toUpperCase()} · ${data.mt5.currency}`
        : "ACCOUNT —",
    );
    if (!isConnected) showNotice(friendlyConnectionMessage(data.mt5.reason));
    else showNotice("");
  } catch {
    isConnected = false;
    connectedTradeMode = null;
    syncExecutionPolicy();
    syncConnectionControls();
    updateAccountSnapshot({ connected: false });
    statusElement.querySelector("strong").textContent = "STATUS UNAVAILABLE";
    showNotice("Could not check the MT5 connection. Refresh the page or restart the local server.");
  }
}

function syncExecutionPolicy() {
  const autoDemoOption = executionPolicy.querySelector('option[value="auto_demo"]');
  autoDemoOption.disabled = connectedTradeMode !== "demo";
  if (autoDemoOption.disabled && executionPolicy.value === "auto_demo") {
    executionPolicy.value = "manual";
  }
  const autoDemo = executionPolicy.value === "auto_demo";
  executionMode.disabled = autoDemo;
  if (autoDemo) executionMode.value = "demo";
}

async function refreshLiveQuote() {
  if (!isConnected || isSwitchingAccount || scanInProgress || quoteInProgress || orderInProgress) return;
  quoteInProgress = true;
  const requestAccountGeneration = accountGeneration;
  try {
    const symbol = symbolInput.value.trim();
    const timeframe = timeframeInput.value;
    const query = new URLSearchParams({ symbol, timeframe });
    const response = await fetch(`/api/quote?${query}`);
    const data = await response.json();
    if (requestAccountGeneration !== accountGeneration) return;
    if (!response.ok) throw new Error(data.detail || "Live quote unavailable.");
    if (symbol !== symbolInput.value.trim() || timeframe !== timeframeInput.value) return;

    if (data.account) {
      updateAccountSnapshot({ connected: true, ...data.account });
      setValue(
        "accountBadge",
        `${String(data.account.trade_mode || "ACCOUNT").toUpperCase()} · ${data.account.currency}`,
      );
    }
    setValue("bidValue", formatPrice(data.quote.bid, data.digits));
    setValue("askValue", formatPrice(data.quote.ask, data.digits));
    setValue("spreadValue", formatPrice(data.spread, data.digits));
    document.getElementById("chartTitle").replaceChildren(
      document.createTextNode(`${data.symbol} `),
      Object.assign(document.createElement("span"), { textContent: formatPrice(data.quote.bid, data.digits) }),
    );
    setValue("dataSource", `MT5 LIVE · ${new Date(data.server_time).toLocaleTimeString()}`);

    if (marketData && marketData.symbol === data.symbol && marketData.timeframe === data.timeframe) {
      const bars = marketData.bars;
      const lastBar = bars[bars.length - 1];
      const wasFollowingLatest = chartStart + visibleBars >= bars.length;
      marketData.quote = data.quote;
      marketData.spread = data.spread;
      if (data.current_bar.time === lastBar.time) {
        bars[bars.length - 1] = data.current_bar;
      } else if (data.current_bar.time > lastBar.time) {
        bars.push(data.current_bar);
        if (bars.length > 2000) {
          bars.shift();
          chartStart = Math.max(0, chartStart - 1);
        }
        if (wasFollowingLatest) chartStart = Math.max(0, bars.length - visibleBars);
      }
      drawChart();
    }
    if (notice.textContent.startsWith("Live quote update failed:")) showNotice("");
  } catch (error) {
    if (requestAccountGeneration === accountGeneration) {
      setValue("dataSource", "LIVE QUOTE PAUSED · RETRYING");
      showNotice(`Live quote update failed: ${error.message}`);
    }
  } finally {
    quoteInProgress = false;
  }
}

function renderWatchlist(symbols) {
  const marketItems = Array.isArray(symbols) && symbols.length ? symbols : focusMarketFallback;
  const list = document.getElementById("watchlist");
  const previousSymbol = symbolInput.value.trim().toUpperCase();
  list.replaceChildren();
  symbolInput.replaceChildren();

  marketItems.forEach((item) => {
    const option = document.createElement("option");
    option.value = item.symbol;
    option.textContent = `${item.description || item.symbol} · ${item.symbol}`;
    symbolInput.appendChild(option);

    const button = document.createElement("button");
    button.type = "button";
    button.className = "watch-item";
    button.dataset.symbol = item.symbol;
    button.setAttribute("aria-label", `${item.description || item.symbol}: ${item.symbol}`);
    const label = document.createElement("span");
    label.className = "watch-symbol";
    label.textContent = item.description || item.symbol;
    const category = document.createElement("span");
    category.className = "watch-type";
    category.textContent = item.symbol;
    button.append(label, category);
    button.addEventListener("click", () => {
      symbolInput.value = item.symbol;
      updateActiveSymbol();
      if (isConnected) analyze();
      else showNotice("Sign into MetaTrader 5 to load a live quote and historical chart.");
    });
    list.appendChild(button);
  });

  const retained = marketItems.find((item) => item.symbol.toUpperCase() === previousSymbol);
  const gold = marketItems.find((item) => item.instrument === "XAUUSD");
  const selected = retained || gold || marketItems[0];
  if (selected) symbolInput.value = selected.symbol;
  setValue("watchCount", String(marketItems.length).padStart(2, "0"));
  updateActiveSymbol();
}

function updateActiveSymbol() {
  const selected = symbolInput.value.trim().toUpperCase();
  document.querySelectorAll(".watch-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.symbol.toUpperCase() === selected);
  });
}

async function loadSymbols() {
  try {
    const response = await fetch("/api/symbols");
    const data = await response.json();
    if (!response.ok || !Array.isArray(data.symbols)) {
      throw new Error(data.detail || "The broker symbol list is unavailable.");
    }
    renderWatchlist(data.symbols);
    setValue("symbolSource", data.source || "MT5 broker symbols");

    if (!data.connected && data.reason) showNotice(friendlyConnectionMessage(data.reason));
  } catch (error) {
    renderWatchlist(focusMarketFallback);
    setValue("symbolSource", "Selected focus markets; broker symbols unavailable");
    showNotice(`Could not load broker symbols: ${error.message}`);
  }
}

async function connectAccount(event) {
  event.preventDefault();
  const connectButton = document.getElementById("connectButton");
  const passwordInput = document.getElementById("mt5Password");
  const accountType = accountTypeInput.value;
  connectButton.disabled = true;
  cancelConnectButton.disabled = true;
  connectButton.textContent = "CONNECTING...";
  showNotice("Connecting to your MT5 broker...");

  try {
    const response = await fetch("/api/connect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        account_type: accountType,
        login: Number(document.getElementById("mt5Login").value),
        password: passwordInput.value,
        server: document.getElementById("mt5Server").value.trim(),
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "MT5 connection failed.");
    isSwitchingAccount = false;
    accountGeneration += 1;
    clearPendingProposals();
    setValue("paperStatus", result.message);
    await loadStatus();
    await loadSymbols();
    if (isConnected) analyze();
  } catch (error) {
    showNotice(error.message);
  } finally {
    passwordInput.value = "";
    connectButton.disabled = false;
    cancelConnectButton.disabled = false;
    updateConnectButtonLabel();
  }
}

function beginAccountSwitch() {
  if (scanTimer) toggleBot();
  if (orderInProgress) {
    showNotice("Wait for the current order request to finish before switching accounts.");
    return;
  }
  accountGeneration += 1;
  clearPendingProposals();
  setValue("tradeStatus", "Scanner stopped for account switching. Analyze again after connecting.");
  setValue("dataSource", "ACCOUNT SWITCH · QUOTES PAUSED");
  isSwitchingAccount = true;
  accountTypeInput.value = connectedTradeMode === "live" ? "live" : "demo";
  syncConnectionControls();
  showNotice("Your current account stays connected until the new login is verified.");
}

function cancelAccountSwitch() {
  isSwitchingAccount = false;
  syncConnectionControls();
  showNotice("");
}

function renderAnalysis(data, preserveViewport = false) {
  const previousViewport = preserveViewport
    && marketData
    && marketData.symbol === data.symbol
    && marketData.timeframe === data.timeframe
    ? {
      firstVisibleTime: marketData.bars[chartStart]?.time,
      visibleBars,
    }
    : null;
  marketData = data;
  if (previousViewport) {
    visibleBars = Math.min(previousViewport.visibleBars, data.bars.length);
    const firstMatchingBar = data.bars.findIndex((bar) => bar.time >= previousViewport.firstVisibleTime);
    chartStart = Math.max(0, Math.min(
      firstMatchingBar < 0 ? 0 : firstMatchingBar,
      data.bars.length - visibleBars,
    ));
  } else {
    visibleBars = Math.min(80, data.bars.length);
    chartStart = Math.max(0, data.bars.length - visibleBars);
  }
  const digits = data.digits;
  const indicators = data.indicators;

  setValue("bidValue", formatPrice(data.quote.bid, digits));
  setValue("askValue", formatPrice(data.quote.ask, digits));
  setValue("spreadValue", formatPrice(data.spread, digits));
  const change = data.change_percent;
  setValue("changeValue", change == null ? "—" : `${change > 0 ? "+" : ""}${change.toFixed(2)}%`);
  const changeElement = document.getElementById("changeValue");
  changeElement.classList.toggle("positive", change > 0);
  changeElement.classList.toggle("negative", change < 0);

  setValue("trendValue", indicators.trend);
  setValue("smaValue", `${formatPrice(indicators.sma20, digits)} / ${formatPrice(indicators.sma50, digits)}`);
  setValue("rsiValue", indicators.rsi14 == null ? "Insufficient history" : indicators.rsi14.toFixed(1));
  setValue("atrValue", formatPrice(indicators.atr14, digits));
  setValue(
    "rangeValue",
    `20 BAR HIGH / LOW: ${formatPrice(indicators.range20_high, digits)} / ${formatPrice(indicators.range20_low, digits)}`,
  );
  setValue("newsValue", data.news.message);
  setValue("tradeStatus", data.trade_status);
  setValue(
    "accountBadge",
    `${String(data.account.trade_mode || "ACCOUNT").toUpperCase()} · ${data.account.equity} ${data.account.currency}`,
  );
  setValue("dataSource", `${data.source} · LIVE · ${new Date(data.server_time).toLocaleTimeString()}`);
  document.getElementById("chartTitle").replaceChildren(
    document.createTextNode(`${data.symbol} `),
    Object.assign(document.createElement("span"), { textContent: formatPrice(data.quote.bid, digits) }),
  );
  setValue("barCount", `${visibleBars} VISIBLE`);

  const rsiFill = document.querySelector(".indicator-track i");
  if (rsiFill) rsiFill.style.width = `${Math.max(0, Math.min(100, indicators.rsi14 || 0))}%`;
  showNotice("");
  drawChart();
  updatePaperButton();
  renderProposals(data.proposals || [], data);
}

function renderProposals(proposals, data) {
  const list = document.getElementById("proposalsList");
  let autoAttemptSelected = false;
  const now = Date.now() / 1000;
  for (const [id, proposal] of pendingProposals) {
    if (proposal.expires_at <= now) {
      pendingProposals.delete(id);
      const card = list.querySelector(`[data-proposal-id="${CSS.escape(id)}"]`);
      card?.classList.add("expired");
      const button = card?.querySelector(".proposal-approve");
      if (button) {
        button.disabled = true;
        button.textContent = "EXPIRED · RESCAN";
      }
    }
  }
  proposals.forEach((proposal) => {
    const key = `${data.symbol}:${data.timeframe}:${proposal.strategy}:${proposal.side}:${proposal.bar_time}`;
    if (seenSignalKeys.has(key)) return;
    seenSignalKeys.add(key);
    pendingProposals.set(proposal.proposal_id, proposal);

    const card = document.createElement("div");
    card.className = "proposal-card";
    card.dataset.proposalId = proposal.proposal_id;

    const strategy = document.createElement("div");
    strategy.className = "proposal-strategy";
    const title = document.createElement("strong");
    title.textContent = `${proposal.label} · ${proposal.side.toUpperCase()}`;
    const reason = document.createElement("small");
    reason.textContent = `${data.symbol} ${data.timeframe} · ${proposal.reason}`;
    strategy.append(title, reason);
    card.appendChild(strategy);

    const metrics = [
      ["ENTRY", proposal.entry],
      ["STOP", proposal.stop_loss],
      ["TARGET", proposal.take_profit],
      ["SIZE / RISK", `${proposal.volume} lots · ${proposal.risk_amount} ${proposal.currency} (actual ${proposal.risk_percent}% / cap ${proposal.risk_limit_percent}%)`],
    ];
    metrics.forEach(([label, value]) => {
      const metric = document.createElement("div");
      metric.className = "proposal-metric";
      const caption = document.createElement("span");
      caption.textContent = label;
      const content = document.createElement("strong");
      content.textContent = String(value);
      metric.append(caption, content);
      card.appendChild(metric);
    });

    const approve = document.createElement("button");
    approve.type = "button";
    approve.className = "proposal-approve";
    const shouldAutoDemo = Boolean(scanTimer)
      && executionPolicy.value === "auto_demo"
      && connectedTradeMode === "demo"
      && isConnected;
    const autoDemo = shouldAutoDemo && !autoAttemptSelected;
    if (autoDemo) autoAttemptSelected = true;
    approve.textContent = autoDemo
      ? "AUTO-SUBMITTING…"
      : shouldAutoDemo
        ? "ONE AUTO ORDER PER SCAN"
        : "REVIEW & APPROVE";
    approve.disabled = shouldAutoDemo;
    approve.addEventListener("click", () => submitProposal(proposal));
    card.appendChild(approve);
    list.querySelector(".empty-note")?.remove();
    list.prepend(card);
    if (autoDemo) submitProposal(proposal, true);
  });
  let emptyNote = list.querySelector(".empty-note");
  if (pendingProposals.size === 0) {
    if (!emptyNote) {
      emptyNote = document.createElement("p");
      emptyNote.className = "empty-note";
      list.appendChild(emptyNote);
    }
    emptyNote.textContent = data.signals?.length || !data.sizing?.available
      ? data.trade_status
      : "No selected strategy currently matches. The scanner will keep checking.";
  } else {
    emptyNote?.remove();
  }
  setValue("proposalCount", String(pendingProposals.size));
}

function clearPendingProposals() {
  pendingProposals.clear();
  seenSignalKeys.clear();
  const list = document.getElementById("proposalsList");
  if (list) {
    const note = document.createElement("p");
    note.className = "empty-note";
    note.textContent = "Account changed. Run analysis for fresh proposals.";
    list.replaceChildren(note);
  }
  setValue("proposalCount", "0");
}

function selectedStrategies() {
  return Array.from(document.querySelectorAll(".strategy-choice:checked"), (input) => input.value);
}

function drawChart() {
  const context = chart.getContext("2d");
  if (!context) return;
  const palette = getComputedStyle(document.documentElement);
  const mutedColor = palette.getPropertyValue("--muted").trim();
  const lineColor = palette.getPropertyValue("--line").trim();
  const greenColor = palette.getPropertyValue("--green").trim();
  const vermilionColor = palette.getPropertyValue("--vermilion").trim();

  const bounds = chart.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  chart.width = Math.max(1, Math.floor(bounds.width * ratio));
  chart.height = Math.max(1, Math.floor(bounds.height * ratio));
  context.scale(ratio, ratio);
  const width = bounds.width;
  const height = bounds.height;
  context.clearRect(0, 0, width, height);

  if (!marketData || marketData.bars.length === 0) {
    context.fillStyle = mutedColor;
    context.font = "12px Segoe UI";
    context.fillText("Historical candles appear here after MT5 connects.", 14, 25);
    return;
  }

  const bars = marketData.bars.slice(chartStart, chartStart + visibleBars);
  if (!bars.length) return;
  const dateOptions = { dateStyle: "medium", timeStyle: "short" };
  document.getElementById("chartRange").replaceChildren(
    document.createTextNode(new Date(bars[0].time * 1000).toLocaleString(undefined, dateOptions)),
    Object.assign(document.createElement("span"), {
      textContent: new Date(bars[bars.length - 1].time * 1000).toLocaleString(undefined, dateOptions),
    }),
  );
  const digits = marketData.digits;
  const left = 8;
  const right = 76;
  const top = 14;
  const bottom = 10;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const high = Math.max(...bars.map((bar) => bar.high));
  const low = Math.min(...bars.map((bar) => bar.low));
  const padding = (high - low || Math.abs(high) * 0.001 || 1) * 0.08;
  const maxPrice = high + padding;
  const minPrice = low - padding;
  const priceY = (price) => top + (maxPrice - price) / (maxPrice - minPrice) * plotHeight;
  const step = plotWidth / bars.length;

  context.font = "9px ui-monospace, Consolas, monospace";
  context.textAlign = "right";
  for (let index = 0; index <= 4; index += 1) {
    const y = top + (plotHeight * index) / 4;
    const price = maxPrice - ((maxPrice - minPrice) * index) / 4;
    context.strokeStyle = lineColor;
    context.beginPath();
    context.moveTo(left, y);
    context.lineTo(left + plotWidth, y);
    context.stroke();
    context.fillStyle = mutedColor;
    context.fillText(price.toFixed(digits), width - 4, y + 3);
  }

  bars.forEach((bar, index) => {
    const x = left + index * step + step / 2;
    const candleWidth = Math.max(1, Math.min(9, step * 0.62));
    const rising = bar.close >= bar.open;
    const color = rising ? greenColor : vermilionColor;
    context.strokeStyle = color;
    context.fillStyle = color;
    context.lineWidth = Math.max(1, Math.min(2, step * 0.15));
    context.beginPath();
    context.moveTo(x, priceY(bar.high));
    context.lineTo(x, priceY(bar.low));
    context.stroke();
    const openY = priceY(bar.open);
    const closeY = priceY(bar.close);
    context.fillRect(x - candleWidth / 2, Math.min(openY, closeY), candleWidth, Math.max(1, Math.abs(closeY - openY)));
  });
  const liveBid = marketData.quote?.bid;
  if (Number.isFinite(liveBid) && liveBid >= minPrice && liveBid <= maxPrice) {
    const y = priceY(liveBid);
    context.save();
    context.setLineDash([4, 4]);
    context.strokeStyle = greenColor;
    context.beginPath();
    context.moveTo(left, y);
    context.lineTo(left + plotWidth, y);
    context.stroke();
    context.setLineDash([]);
    context.fillStyle = greenColor;
    context.textAlign = "right";
    context.fillText(`LIVE ${formatPrice(liveBid, digits)}`, left + plotWidth - 4, y - 4);
    context.restore();
  }
}

function setVisibleBars(nextCount, anchorRatio = 0.5) {
  if (!marketData) return;
  const oldCount = visibleBars;
  const anchorIndex = chartStart + Math.round(anchorRatio * oldCount);
  visibleBars = Math.max(20, Math.min(200, marketData.bars.length, Math.round(nextCount)));
  chartStart = Math.max(
    0,
    Math.min(
      marketData.bars.length - visibleBars,
      Math.round(anchorIndex - anchorRatio * visibleBars),
    ),
  );
  setValue("barCount", `${visibleBars} VISIBLE`);
  drawChart();
}

function panChart(deltaPixels) {
  if (!marketData || !dragState) return;
  const plotWidth = Math.max(1, chart.getBoundingClientRect().width - 84);
  const shift = Math.round((deltaPixels / plotWidth) * visibleBars);
  chartStart = Math.max(0, Math.min(marketData.bars.length - visibleBars, dragState.start - shift));
  setValue("barCount", `${visibleBars} VISIBLE`);
  drawChart();
}

async function analyze({ preserveViewport = false } = {}) {
  if (isSwitchingAccount) {
    showNotice("Finish or cancel the account switch before analyzing.");
    return;
  }
  if (scanInProgress) {
    if (scanTimer) scanRequested = true;
    return;
  }
  if (quoteInProgress || orderInProgress) {
    if (!preserveViewport || scanTimer) {
      setTimeout(() => analyze({ preserveViewport }), 250);
    }
    return;
  }
  scanInProgress = true;
  const requestAccountGeneration = accountGeneration;
  analyzeButton.disabled = true;
  analyzeButton.textContent = "…";
  if (!marketData) showNotice("Loading broker quote and candle history...");
  try {
    const strategies = selectedStrategies();
    if (strategies.length === 0) throw new Error("Select at least one strategy before scanning.");
    const riskPercent = Number(document.getElementById("riskPercent").value);
    if (!Number.isInteger(riskPercent) || riskPercent < 1 || riskPercent > 100) {
      throw new Error("Set per-order risk to a whole-number percentage from 1 to 100.");
    }
    const response = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        symbol: symbolInput.value,
        timeframe: timeframeInput.value,
        count: 1000,
        shift: 0,
        strategies,
        risk_percent: riskPercent,
      }),
    });
    const data = await response.json();
    if (requestAccountGeneration !== accountGeneration) return;
    if (!response.ok) throw new Error(data.detail || "Could not load market data.");
    renderAnalysis(data, preserveViewport);
  } catch (error) {
    if (requestAccountGeneration !== accountGeneration) return;
    if (!preserveViewport) {
      marketData = null;
      drawChart();
      updatePaperButton();
    } else {
      setValue("tradeStatus", `Scanner check failed: ${error.message}`);
    }
    showNotice(friendlyConnectionMessage(error.message));
  } finally {
    analyzeButton.disabled = false;
    analyzeButton.textContent = "↻";
    scanInProgress = false;
    if (scanRequested && scanTimer) {
      scanRequested = false;
      setTimeout(() => analyze({ preserveViewport: true }), 0);
    } else {
      scanRequested = false;
    }
  }
}

async function submitProposal(proposal, autoDemo = false) {
  const mode = autoDemo ? "demo" : executionMode.value;
  if (autoDemo && (!isConnected || connectedTradeMode !== "demo")) {
    showNotice("Automatic order submission is restricted to the connected MT5 demo account.");
    return;
  }
  if (!autoDemo && mode !== "paper" && mode !== connectedTradeMode) {
    showNotice(`Your MT5 account is ${connectedTradeMode || "not identified"}. Select the matching order mode.`);
    return;
  }
  if (!autoDemo) {
    const modeLabel = mode === "paper" ? "PAPER SIMULATION" : `MT5 ${mode.toUpperCase()}`;
    const confirmed = window.confirm(
      `${modeLabel}\n\n${proposal.label} ${proposal.side.toUpperCase()} ${proposal.symbol}\n`
      + `Entry ${proposal.entry} · Stop ${proposal.stop_loss} · Target ${proposal.take_profit}\n`
      + `Size ${proposal.volume} lots · estimated risk ${proposal.risk_amount} ${proposal.currency} (${proposal.risk_percent}% of equity)\n\n`
      + "Submit this order?",
    );
    if (!confirmed) return;
  }

  if (orderInProgress) return;
  orderInProgress = true;
  const button = document.querySelector(`[data-proposal-id="${CSS.escape(proposal.proposal_id)}"] .proposal-approve`);
  if (button) button.disabled = true;
  try {
    const response = await fetch("/api/execute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        proposal_id: proposal.proposal_id,
        mode,
        confirmed: !autoDemo,
        auto_submit: autoDemo,
        daily_loss_limit: Number(document.getElementById("dailyLossLimit").value),
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Order was not submitted.");
    pendingProposals.delete(proposal.proposal_id);
    document.querySelector(`[data-proposal-id="${CSS.escape(proposal.proposal_id)}"]`)?.remove();
    setValue("proposalCount", String(pendingProposals.size));
    setValue("tradeStatus", result.message);
    setValue("paperStatus", `${result.message} ${result.symbol} ${result.side.toUpperCase()} ${result.volume} lots.`);
  } catch (error) {
    if (button) {
      button.disabled = false;
      if (autoDemo) button.textContent = "REVIEW & APPROVE";
    }
    showNotice(error.message);
  } finally {
    orderInProgress = false;
  }
}

function toggleBot() {
  const button = document.getElementById("botToggle");
  const status = document.getElementById("botStatus");
  if (scanTimer) {
    clearInterval(scanTimer);
    scanTimer = null;
    button.textContent = "START SCANNING";
    button.classList.remove("running");
    status.textContent = "STOPPED";
    setValue("tradeStatus", "Scanner stopped. Pending proposals remain available for review.");
    return;
  }
  if (isSwitchingAccount) {
    showNotice("Finish or cancel the account switch before starting the scanner.");
    return;
  }
  if (!isConnected) {
    showNotice("Connect the MT5 account before starting the scanner.");
    return;
  }
  if (selectedStrategies().length === 0) {
    showNotice("Select at least one strategy before starting the scanner.");
    return;
  }
  scanTimer = setInterval(() => analyze({ preserveViewport: true }), 15000);
  button.textContent = "STOP SCANNING";
  button.classList.add("running");
  status.textContent = "SCANNING · 15S";
  setValue("tradeStatus", "Scanner running for the selected pair and timeframe. Orders still require your approval.");
  analyze({ preserveViewport: true });
}

function paperOrderError() {
  if (!marketData) return "Load a broker quote before paper trading.";
  const volume = Number(document.getElementById("paperVolume").value);
  const stop = Number(document.getElementById("paperStop").value);
  const target = Number(document.getElementById("paperTarget").value);
  if (!Number.isFinite(volume) || volume <= 0 || volume > 10) return "Enter a volume above 0 and no greater than 10 lots.";
  if (!Number.isFinite(stop) || !Number.isFinite(target) || stop <= 0 || target <= 0) return "Enter a valid stop loss and take profit.";

  const entry = selectedSide === "buy" ? marketData.quote.ask : marketData.quote.bid;
  if (selectedSide === "buy" && !(stop < entry && target > entry)) return "For a buy, stop loss must be below entry and take profit above it.";
  if (selectedSide === "sell" && !(target < entry && stop > entry)) return "For a sell, take profit must be below entry and stop loss above it.";
  return "";
}

function updatePaperButton() {
  const validationError = paperOrderError();
  paperButton.disabled = !paperConfirm.checked || Boolean(validationError);
  if (validationError) setValue("paperStatus", validationError);
  else setValue("paperStatus", paperConfirm.checked ? "Confirmed. Paper order only; no broker order will be sent." : "Load data, set risk levels, then confirm.");
}

async function submitPaperOrder() {
  const error = paperOrderError();
  if (error || !paperConfirm.checked) {
    setValue("paperStatus", error || "Confirm before recording the paper order.");
    return;
  }

  paperButton.disabled = true;
  const payload = {
    symbol: marketData.symbol,
    side: selectedSide,
    volume: Number(document.getElementById("paperVolume").value),
    entry: selectedSide === "buy" ? marketData.quote.ask : marketData.quote.bid,
    stop_loss: Number(document.getElementById("paperStop").value),
    take_profit: Number(document.getElementById("paperTarget").value),
    confirmed: true,
  };
  try {
    const response = await fetch("/api/paper-order", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Paper order failed.");
    setValue("paperStatus", `${result.message} ${result.symbol} ${result.side.toUpperCase()} ${result.volume} lots.`);
    paperConfirm.checked = false;
  } catch (error) {
    setValue("paperStatus", error.message);
  }
}

document.querySelectorAll(".direction").forEach((button) => {
  button.addEventListener("click", () => {
    selectedSide = button.dataset.side;
    document.querySelectorAll(".direction").forEach((item) => item.classList.toggle("active", item === button));
    updatePaperButton();
  });
});

document.querySelectorAll("#paperVolume, #paperStop, #paperTarget").forEach((input) => {
  input.addEventListener("input", updatePaperButton);
});
paperConfirm.addEventListener("change", updatePaperButton);
paperButton.addEventListener("click", submitPaperOrder);
analyzeButton.addEventListener("click", analyze);
connectForm.addEventListener("submit", connectAccount);
switchAccountButton.addEventListener("click", beginAccountSwitch);
cancelConnectButton.addEventListener("click", cancelAccountSwitch);
accountTypeInput.addEventListener("change", updateConnectButtonLabel);
symbolInput.addEventListener("input", updateActiveSymbol);
symbolInput.addEventListener("change", () => { if (isConnected) analyze(); });
timeframeInput.addEventListener("change", () => { if (isConnected) analyze(); });
document.getElementById("resetChart").addEventListener("click", () => {
  if (!marketData) return;
  visibleBars = Math.min(80, marketData.bars.length);
  chartStart = Math.max(0, marketData.bars.length - visibleBars);
  setValue("barCount", `${visibleBars} VISIBLE`);
  drawChart();
});
document.getElementById("botToggle").addEventListener("click", toggleBot);
executionPolicy.addEventListener("change", syncExecutionPolicy);
chart.addEventListener("pointerdown", (event) => {
  if (!marketData) return;
  chart.setPointerCapture(event.pointerId);
  dragState = { x: event.clientX, start: chartStart };
  chart.classList.add("dragging");
});
chart.addEventListener("pointermove", (event) => {
  if (!dragState) return;
  panChart(event.clientX - dragState.x);
});
chart.addEventListener("pointerup", () => {
  dragState = null;
  chart.classList.remove("dragging");
});
chart.addEventListener("pointercancel", () => {
  dragState = null;
  chart.classList.remove("dragging");
});
chart.addEventListener("wheel", (event) => {
  if (!marketData) return;
  event.preventDefault();
  const bounds = chart.getBoundingClientRect();
  const ratio = Math.max(0, Math.min(1, (event.clientX - bounds.left - 8) / Math.max(1, bounds.width - 84)));
  const factor = event.deltaY < 0 ? 0.8 : 1.25;
  setVisibleBars(visibleBars * factor, ratio);
}, { passive: false });
chart.addEventListener("dblclick", () => {
  if (!marketData) return;
  visibleBars = Math.min(80, marketData.bars.length);
  chartStart = Math.max(0, marketData.bars.length - visibleBars);
  setValue("barCount", `${visibleBars} VISIBLE`);
  drawChart();
});
window.addEventListener("resize", drawChart);

function updateClock() {
  setValue("clock", `${new Date().toISOString().slice(11, 16)} UTC`);
}

updateClock();
setInterval(updateClock, 30000);
drawChart();
updatePaperButton();
Promise.all([loadStatus(), loadSymbols()]).then(() => {
  if (isConnected) {
    analyze();
    refreshLiveQuote();
  }
});
setInterval(refreshLiveQuote, 2000);
