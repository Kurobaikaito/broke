const state = {
  horizon: "20d",
  capital: 50000,
  researchCapital: 50000,
  minScore: "",
  items: [],
  selectedCode: null,
  currentView: "analysisView",
  syncStatus: "idle",
  researchStatus: "idle",
  demoMode: null,
  hasDataCheckpoint: false,
  syncDataset: "a_share_daily_full_v2",
  detailCode: null,
  detailLimit: 120,
  detailRequestId: 0,
  analysisRequestId: 0,
  explanationRequestId: 0,
  stockChart: null,
  detailReturnFocus: null,
  detailReturnCode: null,
  detailReturnClass: null,
};

const viewRoutes = {
  analysisView: "analysis",
  researchView: "research",
  dataView: "data",
};

const routeViews = Object.fromEntries(Object.entries(viewRoutes).map(([view, route]) => [route, view]));

const refreshLabels = {
  analysisView: "刷新选股",
  researchView: "刷新研究",
  dataView: "刷新数据",
  stockDetailView: "刷新行情",
};

const factorNames = {
  momentum_20d: "20日动量",
  momentum_60d: "60日动量",
  trend_20d: "20日趋势",
  reversal_5d: "5日反转",
  low_volatility_20d: "低波动",
  drawdown_60d: "60日回撤",
  liquidity_20d: "成交额流动性",
  turnover_20d: "换手活跃",
  momentum_120_20d: "120-20日动量",
  high_proximity_120d: "120日高点距离",
  downside_risk_20d: "下行风险",
  market_relative_momentum_60d: "市场相对动量",
  market_beta_60d: "市场Beta",
  amihud_liquidity_20d: "Amihud流动性",
  amount_stability_20d: "成交额稳定性",
};

const tableNames = {
  daily_bar: "原始日线",
  daily_bar_adj: "复权日线",
  adj_factor: "复权因子",
  daily_basic: "每日指标",
  dim_stock: "股票主数据",
  trade_calendar: "交易日历",
  factor_daily: "因子历史",
  model_prediction: "模型预测",
  research_model_run: "研究发布记录",
  backtest_summary: "回测摘要",
  data_sync_state: "同步断点",
  data_maintenance_run: "维护审计",
};

const categoryNames = {
  reference: "主数据",
  raw_market_data: "原始数据",
  materialized_derived_data: "派生物化",
  rebuildable_derived_data: "可重建因子",
  derived_serving_data: "在线预测",
  research_audit: "研究审计",
  operational_state: "运行状态",
  operational_audit: "运维审计",
};

function $(id) {
  return document.getElementById(id);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatPercent(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function formatNumber(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  return Number(value).toFixed(digits);
}

function formatInteger(value) {
  return new Intl.NumberFormat("zh-CN").format(Number(value || 0));
}

function numericOrNull(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function firstNumber(...values) {
  for (const value of values) {
    const number = numericOrNull(value);
    if (number !== null) return number;
  }
  return null;
}

function formatSignedNumber(value, digits = 2) {
  const number = numericOrNull(value);
  if (number === null) return "--";
  return `${number > 0 ? "+" : ""}${number.toFixed(digits)}`;
}

function formatSignedPercent(value) {
  const number = numericOrNull(value);
  if (number === null) return "--";
  return `${number > 0 ? "+" : ""}${(number * 100).toFixed(2)}%`;
}

function formatChineseAmount(value) {
  const number = numericOrNull(value);
  if (number === null) return "--";
  if (Math.abs(number) >= 100000000) return `${(number / 100000000).toFixed(2)}亿元`;
  if (Math.abs(number) >= 10000) return `${(number / 10000).toFixed(2)}万元`;
  return `${formatInteger(number)}元`;
}

function formatMarketValue(value) {
  const number = numericOrNull(value);
  return number === null ? "--" : `${(number / 100000000).toFixed(2)}亿元`;
}

function formatVolume(value) {
  const number = numericOrNull(value);
  if (number === null) return "--";
  if (Math.abs(number) >= 1000000) return `${(number / 1000000).toFixed(2)}万手`;
  return `${formatInteger(number / 100)}手`;
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = response.statusText;
    try {
      const payload = await response.json();
      message = payload.detail || message;
    } catch (_error) {
      message = await response.text();
    }
    throw new Error(message || `HTTP ${response.status}`);
  }
  return response.json();
}

async function loadHealth() {
  const health = await fetchJson("/api/health");
  state.demoMode = Boolean(health.demo_mode);
  $("modeBadge").textContent = health.demo_mode ? "Demo模式" : health.ok ? "MySQL已连接" : "数据库异常";
  $("modeBadge").style.color = health.ok ? "#11845b" : "#c2410c";
  updateResearchAvailability();
}

async function loadBacktest(requestId = state.analysisRequestId) {
  const summary = await fetchJson(`/api/backtest/summary?horizon=${state.horizon}`);
  if (requestId !== state.analysisRequestId) return;
  $("topReturn").textContent = formatPercent(summary.top_group_return);
  $("benchmarkReturn").textContent = formatPercent(summary.benchmark_return);
  $("winRate").textContent = formatPercent(summary.win_rate);
  $("rankIc").textContent = formatNumber(summary.rank_ic, 3);
}

async function loadRecommendations(requestId = ++state.analysisRequestId) {
  const params = new URLSearchParams({ horizon: state.horizon, capital: String(state.capital), limit: "100" });
  if (state.minScore !== "") params.set("min_score", state.minScore);
  const payload = await fetchJson(`/api/recommendations?${params.toString()}`);
  if (requestId !== state.analysisRequestId) return;
  state.explanationRequestId += 1;
  state.items = payload.items || [];
  const sourceBadge = $("analysisSourceBadge");
  if (payload.mode === "mysql-empty-demo-fallback") {
    sourceBadge.textContent = "演示推荐 · 暂无真实预测";
    sourceBadge.dataset.mode = "fallback";
  } else if (payload.mode === "demo") {
    sourceBadge.textContent = "演示推荐";
    sourceBadge.dataset.mode = "demo";
  } else {
    sourceBadge.textContent = "真实模型推荐";
    sourceBadge.dataset.mode = "mysql";
  }
  $("resultCount").textContent = `${state.items.length}只 · 已分配${formatInteger(payload.allocated_amount)}元 · 现金${formatInteger(payload.cash_remaining)}元`;
  renderRecommendations();
  if (state.items.length > 0) {
    await selectStock(state.items[0].code);
  } else {
    $("explainBody").innerHTML = `<p class="empty">暂无结果</p>`;
  }
}

function renderRecommendations() {
  const tbody = $("recommendationBody");
  if (state.items.length === 0) {
    tbody.innerHTML = `<tr><td colspan="10" class="empty">当前资金下暂无可买满一手的结果</td></tr>`;
    return;
  }
  tbody.innerHTML = state.items
    .map((item) => {
      const risks = item.risk_flags?.length ? item.risk_flags.join("、") : "正常";
      const riskClass = item.risk_flags?.length ? "risk" : "risk ok";
      const active = state.selectedCode === item.code ? "active" : "";
      return `<tr class="${active}" data-code="${escapeHtml(item.code)}">
        <td>${item.rank ?? "--"}</td><td><button class="stock-select-btn" type="button" data-select-code="${escapeHtml(item.code)}" aria-label="查看${escapeHtml(item.name)}的入选依据">${escapeHtml(item.code)}</button></td>
        <td><button class="stock-name-link" type="button" data-detail-code="${escapeHtml(item.code)}" aria-label="查看${escapeHtml(item.name)}详情">${escapeHtml(item.name)}</button></td>
        <td>${escapeHtml(item.industry || "未分类")}</td><td class="score">${formatNumber(item.score, 3)}</td>
        <td class="prob">${formatPercent(item.probability)}</td>
        <td>${formatInteger(item.target_shares)}</td><td>${formatInteger(item.target_amount)}</td>
        <td><span class="${riskClass}" title="${escapeHtml(risks)}">${escapeHtml(risks)}</span></td>
        <td><button class="table-detail-btn" type="button" data-detail-code="${escapeHtml(item.code)}">查看</button></td></tr>`;
    })
    .join("");
  tbody.querySelectorAll("tr[data-code]").forEach((row) => row.addEventListener("click", () => selectStock(row.dataset.code)));
  tbody.querySelectorAll("button[data-select-code]").forEach((button) => button.addEventListener("click", (event) => {
    event.stopPropagation();
    selectStock(button.dataset.selectCode);
  }));
  tbody.querySelectorAll("button[data-detail-code]").forEach((button) => button.addEventListener("click", (event) => {
    event.stopPropagation();
    openStockDetail(button.dataset.detailCode);
  }));
}

async function selectStock(code) {
  const requestId = ++state.explanationRequestId;
  state.selectedCode = code;
  renderRecommendations();
  $("selectedCode").textContent = code;
  $("explainBody").innerHTML = `<p class="empty">加载中</p>`;
  const explanation = await fetchJson(`/api/stocks/${encodeURIComponent(code)}/explain?horizon=${state.horizon}`);
  if (requestId !== state.explanationRequestId || code !== state.selectedCode) return;
  renderExplanation(explanation);
}

function renderExplanation(explanation) {
  const prediction = explanation.prediction;
  const highlights = prediction.factor_highlights || [];
  const notes = explanation.notes || [];
  const riskText = prediction.risk_flags?.length ? prediction.risk_flags.join("、") : "无明显风险标记";
  $("tradeMeta").textContent = `${prediction.trade_date || "--"} · ${state.horizon} · ${explanation.method}`;
  $("explainBody").innerHTML = `<div class="stock-title"><h3>${escapeHtml(prediction.name)}</h3><span>${escapeHtml(prediction.code)}</span></div>
    <div class="explain-stats">
      <div class="stat"><span>综合评分</span><strong>${formatNumber(prediction.score, 3)}</strong></div>
      <div class="stat"><span>上涨概率</span><strong>${formatPercent(prediction.probability)}</strong></div>
      <div class="stat"><span>行业</span><strong>${escapeHtml(prediction.industry || "未分类")}</strong></div>
      <div class="stat"><span>风险</span><strong>${escapeHtml(riskText)}</strong></div>
    </div><div class="factor-list">${highlights.map(renderFactor).join("")}</div>
    <ul class="note-list">${notes.map((note) => `<li>${escapeHtml(note)}</li>`).join("")}</ul>`;
}

function renderFactor(factor) {
  const label = factorNames[factor.name] || factor.name;
  const value = Number(factor.contribution || 0);
  const width = Math.min(100, Math.max(4, Math.abs(value) * 360));
  return `<div class="factor-row"><div><div class="factor-name">${escapeHtml(label)}</div>
    <div class="factor-bar"><span style="width:${width}%"></span></div></div>
    <div class="factor-value">${value >= 0 ? "+" : ""}${value.toFixed(3)}</div></div>`;
}

function normalizeBars(rawBars) {
  const byDate = new Map();
  (rawBars || []).forEach((bar) => {
    const time = String(bar.trade_date || bar.date || "").slice(0, 10);
    const open = numericOrNull(bar.open);
    const high = numericOrNull(bar.high);
    const low = numericOrNull(bar.low);
    const close = numericOrNull(bar.close);
    if (!time || [open, high, low, close].some((value) => value === null)) return;
    byDate.set(time, {
      time,
      open,
      high,
      low,
      close,
      volume: numericOrNull(bar.volume) ?? 0,
      amount: numericOrNull(bar.amount),
    });
  });
  return [...byDate.values()].sort((left, right) => left.time.localeCompare(right.time));
}

function movingAverage(bars, period) {
  let total = 0;
  return bars.reduce((points, bar, index) => {
    total += bar.close;
    if (index >= period) total -= bars[index - period].close;
    if (index >= period - 1) points.push({ time: bar.time, value: total / period });
    return points;
  }, []);
}

function chartTimeToIso(time) {
  if (typeof time === "string") return time;
  if (time && typeof time === "object") {
    return `${time.year}-${String(time.month).padStart(2, "0")}-${String(time.day).padStart(2, "0")}`;
  }
  return "";
}

function renderChartLegend(bar) {
  if (!bar) {
    $("chartLegend").textContent = "悬停图表查看每日开高低收";
    return;
  }
  $("chartLegend").textContent = `${bar.time}  开 ${formatNumber(bar.open)}  高 ${formatNumber(bar.high)}  低 ${formatNumber(bar.low)}  收 ${formatNumber(bar.close)}  量 ${formatVolume(bar.volume)}`;
}

function metricMarkup(label, value) {
  return `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
}

function renderDetailOverview(payload, bars) {
  const stock = payload.stock || {};
  const latest = bars.at(-1) || {};
  const previous = bars.at(-2) || {};
  const lastClose = firstNumber(stock.last_close, latest.close);
  const change = firstNumber(
    stock.change,
    lastClose !== null && numericOrNull(previous.close) !== null ? lastClose - Number(previous.close) : null,
  );
  const changePct = firstNumber(
    stock.change_pct,
    change !== null && numericOrNull(previous.close) ? change / Number(previous.close) : null,
  );
  const trendClass = change === null ? "" : change > 0 ? "is-up" : change < 0 ? "is-down" : "is-flat";

  $("detailStockName").textContent = stock.name || state.detailCode || "--";
  $("detailStockCode").textContent = stock.code || state.detailCode || "--";
  $("detailIndustry").textContent = stock.industry || "未分类";
  $("detailTradeDate").textContent = `最新交易日 ${stock.trade_date || latest.time || "--"}`;
  $("detailLastClose").textContent = lastClose === null ? "--" : formatNumber(lastClose);
  $("detailLastClose").className = trendClass;
  $("detailChange").textContent = `${formatSignedNumber(change)}  ${formatSignedPercent(changePct)}`;
  $("detailChange").className = trendClass;

  const modeLabels = {
    demo: "演示行情",
    mysql: "MySQL 行情",
    "mysql-empty-demo-fallback": "演示回退行情",
  };
  const modeBadge = $("detailDataMode");
  modeBadge.textContent = modeLabels[payload.mode] || (payload.mode ? String(payload.mode) : "");
  modeBadge.hidden = !modeBadge.textContent;

  const open = firstNumber(stock.open, latest.open);
  const high = firstNumber(stock.high, latest.high);
  const low = firstNumber(stock.low, latest.low);
  const volume = firstNumber(stock.volume, latest.volume);
  const amount = firstNumber(stock.amount, latest.amount);
  $("detailMetrics").innerHTML = [
    metricMarkup("开盘", formatNumber(open)),
    metricMarkup("最高", formatNumber(high)),
    metricMarkup("最低", formatNumber(low)),
    metricMarkup("成交量", formatVolume(volume)),
    metricMarkup("成交额", formatChineseAmount(amount)),
    metricMarkup("换手率", formatPercent(stock.turnover_rate)),
    metricMarkup("PE(TTM)", formatNumber(stock.pe_ttm)),
    metricMarkup("PB", formatNumber(stock.pb)),
    metricMarkup("总市值", formatMarketValue(stock.total_market_value)),
    metricMarkup("流通市值", formatMarketValue(stock.circ_market_value)),
  ].join("");
}

function destroyStockChart() {
  if (state.stockChart) {
    state.stockChart.remove();
    state.stockChart = null;
  }
  $("stockChart").replaceChildren();
}

function setDetailChartState(kind, title, message) {
  const panel = $("detailChartState");
  const spinner = panel.querySelector(".loading-spinner");
  panel.dataset.state = kind;
  panel.hidden = kind === "ready";
  spinner.hidden = kind !== "loading";
  $("detailStateTitle").textContent = title;
  $("detailStateMessage").textContent = message;
  $("retryDetailBtn").hidden = kind !== "error";
  $("stockChart").setAttribute("aria-busy", kind === "loading" ? "true" : "false");
}

function renderStockChart(bars) {
  const library = window.LightweightCharts;
  if (!library?.createChart || !library.CandlestickSeries || !library.HistogramSeries) {
    throw new Error("K线图组件未正确加载");
  }

  destroyStockChart();
  const container = $("stockChart");
  const chart = library.createChart(container, {
    autoSize: true,
    height: 500,
    layout: {
      background: { type: "solid", color: "#ffffff" },
      textColor: "#667085",
      fontFamily: 'Inter, "Segoe UI", "Microsoft YaHei", sans-serif',
      attributionLogo: true,
      panes: {
        separatorColor: "#e5eaf1",
        separatorHoverColor: "#93b4f7",
        enableResize: true,
      },
    },
    grid: {
      vertLines: { color: "#eef2f6" },
      horzLines: { color: "#eef2f6" },
    },
    crosshair: {
      mode: library.CrosshairMode?.Normal ?? 0,
      vertLine: { color: "#94a3b8", labelBackgroundColor: "#475569" },
      horzLine: { color: "#94a3b8", labelBackgroundColor: "#475569" },
    },
    rightPriceScale: { borderColor: "#d9e1ea", minimumWidth: 70 },
    timeScale: {
      borderColor: "#d9e1ea",
      timeVisible: false,
      rightOffset: 3,
      barSpacing: 7,
      minBarSpacing: 2,
    },
    localization: { locale: "zh-CN", dateFormat: "yyyy-MM-dd" },
  });
  state.stockChart = chart;

  const candleSeries = chart.addSeries(library.CandlestickSeries, {
    upColor: "#e5484d",
    downColor: "#10a36c",
    borderUpColor: "#e5484d",
    borderDownColor: "#10a36c",
    wickUpColor: "#e5484d",
    wickDownColor: "#10a36c",
    priceLineVisible: false,
  });
  candleSeries.setData(bars.map(({ time, open, high, low, close }) => ({ time, open, high, low, close })));

  const volumeSeries = chart.addSeries(library.HistogramSeries, {
    priceFormat: { type: "volume" },
    priceLineVisible: false,
    lastValueVisible: false,
  }, 1);
  volumeSeries.setData(bars.map((bar) => ({
    time: bar.time,
    value: bar.volume,
    color: bar.close >= bar.open ? "rgba(229, 72, 77, 0.66)" : "rgba(16, 163, 108, 0.66)",
  })));

  [
    [5, "#f59e0b"],
    [10, "#7c3aed"],
    [20, "#2563eb"],
  ].forEach(([period, color]) => {
    const points = movingAverage(bars, period);
    if (!points.length) return;
    const series = chart.addSeries(library.LineSeries, {
      color,
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    series.setData(points);
  });

  const panes = chart.panes();
  if (panes[0]?.setStretchFactor) panes[0].setStretchFactor(4);
  if (panes[1]?.setStretchFactor) panes[1].setStretchFactor(1);
  chart.timeScale().fitContent();

  const barsByTime = new Map(bars.map((bar) => [bar.time, bar]));
  const latest = bars.at(-1);
  renderChartLegend(latest);
  chart.subscribeCrosshairMove((parameter) => {
    const hovered = barsByTime.get(chartTimeToIso(parameter.time));
    renderChartLegend(hovered || latest);
  });
  setDetailChartState("ready", "", "");
}

function renderStockDetailLoading(code) {
  destroyStockChart();
  const recommendation = state.items.find((item) => item.code === code);
  $("detailStockName").textContent = recommendation?.name || code;
  $("detailStockCode").textContent = code;
  $("detailIndustry").textContent = recommendation?.industry || "--";
  $("detailTradeDate").textContent = "最新交易日 --";
  $("detailLastClose").textContent = "--";
  $("detailLastClose").className = "";
  $("detailChange").textContent = "--";
  $("detailChange").className = "";
  $("detailDataMode").hidden = true;
  $("detailMetrics").innerHTML = ["开盘", "最高", "最低", "成交量", "成交额", "换手率", "PE(TTM)", "PB", "总市值", "流通市值"]
    .map((label) => metricMarkup(label, "--")).join("");
  renderChartLegend(null);
  setDetailChartState("loading", "正在加载行情", `正在读取 ${code} 的K线与核心指标…`);
}

async function loadStockDetail() {
  const code = state.detailCode;
  if (!code) return;
  const requestId = ++state.detailRequestId;
  renderStockDetailLoading(code);
  try {
    const payload = await fetchJson(`/api/stocks/${encodeURIComponent(code)}/detail?limit=${state.detailLimit}`);
    if (requestId !== state.detailRequestId || code !== state.detailCode) return;
    const bars = normalizeBars(payload.bars);
    renderDetailOverview(payload, bars);
    if (!bars.length) {
      setDetailChartState("empty", "暂无K线数据", "该股票当前没有可展示的日线行情。");
      return;
    }
    try {
      renderStockChart(bars);
    } catch (error) {
      setDetailChartState("error", "图表暂时无法显示", error.message || "K线图初始化失败");
    }
  } catch (error) {
    if (requestId !== state.detailRequestId || code !== state.detailCode) return;
    setDetailChartState("error", "行情加载失败", error.message || "请检查服务后重试。");
  }
}

function updateDetailRangeButtons() {
  document.querySelectorAll(".range-btn[data-detail-limit]").forEach((button) => {
    const active = Number(button.dataset.detailLimit) === state.detailLimit;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}

function openStockDetail(code, options = {}) {
  const normalizedCode = String(code || "").trim();
  if (!normalizedCode) return;
  if (options.pushHistory !== false) {
    state.detailReturnFocus = document.activeElement;
    state.detailReturnCode = normalizedCode;
    state.detailReturnClass = document.activeElement?.classList.contains("table-detail-btn") ? "table-detail-btn" : "stock-name-link";
  }
  state.detailCode = normalizedCode;
  updateDetailRangeButtons();
  if (options.pushHistory !== false) {
    const hash = `#stock/${encodeURIComponent(normalizedCode)}`;
    if (window.location.hash !== hash) window.history.pushState({ stockDetail: true, code: normalizedCode }, "", hash);
  }
  switchView("stockDetailView");
  const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  window.scrollTo({ top: 0, behavior: reducedMotion ? "auto" : "smooth" });
  window.requestAnimationFrame(() => $("backToAnalysisBtn").focus());
  loadStockDetail();
}

function returnToAnalysis() {
  if (window.history.state?.stockDetail && window.location.hash.startsWith("#stock/")) {
    window.history.back();
    return;
  }
  window.history.replaceState({ view: "analysisView" }, "", "#analysis");
  switchView("analysisView");
}

function routeFromLocation() {
  const match = window.location.hash.match(/^#stock\/([^/?#]+)/);
  if (match) {
    try {
      openStockDetail(decodeURIComponent(match[1]), { pushHistory: false });
    } catch (_error) {
      returnToAnalysis();
    }
    return;
  }
  const route = window.location.hash.replace(/^#/, "");
  const viewId = routeViews[route] || "analysisView";
  if (!routeViews[route]) window.history.replaceState({ view: viewId }, "", "#analysis");
  switchView(viewId);
}

function navigateToMainView(viewId) {
  const route = viewRoutes[viewId];
  if (!route) return;
  const hash = `#${route}`;
  if (window.location.hash !== hash) window.history.pushState({ view: viewId }, "", hash);
  switchView(viewId);
  window.scrollTo({ top: 0, behavior: "auto" });
}

async function loadDataConfig() {
  const config = await fetchJson("/api/data/config");
  state.syncDataset = config.sync_dataset || state.syncDataset;
  $("syncPolicy").textContent = `首次从 ${config.history_start} 拉取沪深历史数据；以后自动从最后成功日期继续，不包含北交所。`;
}

async function loadInventory() {
  const inventory = await fetchJson("/api/data/inventory");
  const rows = inventory.tables || [];
  $("inventoryBody").innerHTML = rows.length
    ? rows.map((item) => `<tr><td>${escapeHtml(tableNames[item.table] || item.table)}</td>
      <td><span class="category-tag">${escapeHtml(categoryNames[item.category] || item.category || "--")}</span></td>
      <td>${formatInteger(item.estimated_rows)}</td><td>${item.start_date || "--"}</td><td>${item.end_date || "--"}</td>
      <td>${item.retention_days === null || item.retention === "permanent" ? "永久" : `${formatInteger(item.retention_days)}天`}</td></tr>`).join("")
    : `<tr><td colspan="6" class="empty">暂无数据</td></tr>`;
  const summary = inventory.summary || {};
  $("inventoryRowCount").textContent = summary.estimated_total_rows === undefined ? "--" : formatInteger(summary.estimated_total_rows);
  $("inventoryTableCount").textContent = formatInteger(summary.table_count ?? rows.length);
  $("inventoryPermanentCount").textContent = formatInteger(rows.filter((item) => item.retention_days === null || item.retention === "permanent").length);
  $("inventoryRetainedCount").textContent = formatInteger(rows.filter((item) => item.retention_days !== null && item.retention !== "permanent").length);
  const states = inventory.states || [];
  const checkpoint = states.find((item) => item.dataset === state.syncDataset);
  state.hasDataCheckpoint = Boolean(checkpoint?.last_trade_date);
  const checkpointStatus = {
    ready: "就绪", running: "同步中", completed: "已完成", failed: "失败", stopped: "已停止",
  }[checkpoint?.status] || checkpoint?.status || "未知";
  const checkpointText = checkpoint
    ? `同步断点 ${checkpoint.last_trade_date || "--"} · ${checkpointStatus}`
    : "同步断点 --";
  $("dataCheckpoint").textContent = checkpointText;
  $("researchDataCheckpoint").textContent = checkpoint
    ? `市场数据已同步至 ${checkpoint.last_trade_date || "--"}，可以在确认后生成推荐。`
    : "尚未发现同步断点，请先到数据中心准备市场数据。";
  updateTaskAvailability();
}

const statusLabels = {
  idle: "空闲", running: "运行中", stopping: "停止中", stopped: "已停止", completed: "已完成", failed: "失败",
};

const researchRunModeLabels = {
  auto: "自动判断",
  full: "完整回测",
  latest: "增量更新",
  incremental: "增量更新",
};

function taskIsActive(status) {
  return ["running", "stopping"].includes(status);
}

function updateTaskAvailability() {
  const syncRunning = taskIsActive(state.syncStatus);
  const researchRunning = taskIsActive(state.researchStatus);
  $("startSyncBtn").disabled = syncRunning || researchRunning;
  $("stopSyncBtn").disabled = !syncRunning || state.syncStatus === "stopping";
  $("startResearchBtn").disabled = researchRunning || syncRunning || state.demoMode === true;
  $("forceFullResearchBtn").disabled = $("startResearchBtn").disabled;
  $("stopResearchBtn").disabled = !researchRunning || state.researchStatus === "stopping";
  $("startSyncBtn").title = researchRunning ? "研究任务运行期间不能同步数据" : "";
  $("startResearchBtn").title = syncRunning
    ? "数据同步运行期间不能启动研究"
    : state.demoMode === true ? "MySQL 模式下才能生成真实推荐" : "";
  $("forceFullResearchBtn").title = $("startResearchBtn").title;

  const syncState = $("researchSyncStatus");
  syncState.textContent = syncRunning ? "同步中" : state.hasDataCheckpoint ? "数据就绪" : statusLabels[state.syncStatus] || "待确认";
  syncState.dataset.status = syncRunning ? "running" : state.hasDataCheckpoint ? "completed" : state.syncStatus;
}

function renderSyncStatus(payload) {
  const previous = state.syncStatus;
  state.syncStatus = payload.status || "idle";
  const running = ["running", "stopping"].includes(state.syncStatus);
  $("syncStatusBadge").textContent = statusLabels[state.syncStatus] || state.syncStatus;
  $("syncStatusBadge").style.color = state.syncStatus === "failed" ? "#b42318" : running ? "#2563eb" : "#11845b";
  const progress = Math.min(100, Math.max(0, Number(payload.progress_pct || 0)));
  $("progressPercent").textContent = `${progress.toFixed(0)}%`;
  $("progressBar").style.width = `${progress}%`;
  $("syncProgressTrack").setAttribute("aria-valuenow", String(Math.round(progress)));
  $("progressMessage").textContent = payload.message || "--";
  $("currentSyncDate").textContent = payload.current_date || "--";
  $("dateProgress").textContent = `${payload.completed_dates || 0} / ${payload.total_dates || 0}`;
  const totals = payload.totals || {};
  $("rawRows").textContent = formatInteger(totals.daily);
  $("adjustedRows").textContent = formatInteger(totals.adjusted);
  $("factorRows").textContent = formatInteger(totals.factors);
  $("basicRows").textContent = formatInteger(totals.basics);
  $("jobIdText").textContent = payload.job_id ? payload.job_id.slice(0, 12) : "--";
  renderLogs(payload.logs || []);
  updateTaskAvailability();
  if (["completed", "failed", "stopped"].includes(state.syncStatus) && previous !== state.syncStatus) loadInventory().catch(() => {});
}

function renderLogs(logs) {
  renderJobLogs("syncLogs", logs);
}

function renderJobLogs(containerId, logs) {
  const container = $(containerId);
  if (!logs.length) {
    container.innerHTML = `<p class="empty">暂无日志</p>`;
    return;
  }
  container.innerHTML = logs.map((entry) => `<div class="log-line ${entry.level === "error" ? "error" : ""}">
    <time>${escapeHtml(entry.time)}</time><span>${escapeHtml(entry.message)}</span></div>`).join("");
  container.scrollTop = container.scrollHeight;
}

function updateResearchAvailability() {
  const running = taskIsActive(state.researchStatus);
  $("researchCapitalText").textContent = `${formatInteger(state.researchCapital)}元`;
  updateTaskAvailability();
  if (state.demoMode === true && !running && state.researchStatus === "idle") {
    $("researchStatusBadge").textContent = "Demo模式";
    $("researchMessage").textContent = "切换至 MySQL 模式并完成数据同步后可生成真实推荐";
  }
}

function renderResearchStatus(payload) {
  const previous = state.researchStatus;
  state.researchStatus = payload.status || "idle";
  const running = ["running", "stopping"].includes(state.researchStatus);
  $("researchStatusBadge").textContent = statusLabels[state.researchStatus] || state.researchStatus;
  $("researchStatusBadge").style.color = state.researchStatus === "failed" ? "#b42318" : running ? "#2563eb" : "#11845b";
  const progress = Math.min(100, Math.max(0, Number(payload.progress_pct || 0)));
  $("researchProgressPercent").textContent = `${progress.toFixed(0)}%`;
  $("researchProgressBar").style.width = `${progress}%`;
  $("researchProgressTrack").setAttribute("aria-valuenow", String(Math.round(progress)));
  $("researchMessage").textContent = payload.message || "--";
  const runMode = payload.actual_run_mode || payload.run_mode || "auto";
  $("researchRunModeText").textContent = researchRunModeLabels[runMode] || runMode;
  $("researchCurrentStepText").textContent = payload.current_step || "--";
  $("researchStepProgress").textContent = `${payload.step_completed ?? 0} / ${payload.step_total ?? 0}`;
  $("researchWindowProgress").textContent = `${payload.completed_windows ?? payload.fitted_windows ?? 0} / ${payload.total_windows ?? 0}`;
  const skipped = payload.skipped_horizons?.length ? ` · 跳过 ${payload.skipped_horizons.join("/")}日` : "";
  $("researchHorizonProgress").textContent = `${payload.completed_horizons || 0} / ${payload.total_horizons || 0}${skipped}`;
  $("researchJobIdText").textContent = payload.job_id ? payload.job_id.slice(0, 12) : "--";
  renderJobLogs("researchLogs", payload.logs || []);
  updateResearchAvailability();
  if (["completed", "failed", "stopped"].includes(state.researchStatus) && previous !== state.researchStatus) {
    loadInventory().catch(() => {});
    if (state.researchStatus === "completed") refreshAnalysis().catch(() => {});
  }
}

async function loadResearchStatus() {
  renderResearchStatus(await fetchJson("/api/research/status"));
}

async function startResearch(mode = "auto") {
  try {
    const params = new URLSearchParams({ capital: String(state.researchCapital), mode });
    renderResearchStatus(await fetchJson(`/api/research/run?${params.toString()}`, { method: "POST" }));
  } catch (error) {
    $("researchStatusBadge").textContent = "未启动";
    $("researchStatusBadge").style.color = "#b42318";
    $("researchMessage").textContent = error.message;
  }
}

async function stopResearch() {
  try {
    renderResearchStatus(await fetchJson("/api/research/stop", { method: "POST" }));
  } catch (error) {
    $("researchMessage").textContent = error.message;
  }
}

async function loadSyncStatus() {
  renderSyncStatus(await fetchJson("/api/data/sync/status"));
}

async function startSync() {
  try {
    renderSyncStatus(await fetchJson("/api/data/sync", {
      method: "POST",
    }));
  } catch (error) {
    $("progressMessage").textContent = error.message;
    $("syncStatusBadge").textContent = "未启动";
    $("syncStatusBadge").style.color = "#b42318";
  }
}

async function stopSync() {
  try {
    renderSyncStatus(await fetchJson("/api/data/sync/stop", { method: "POST" }));
  } catch (error) {
    $("progressMessage").textContent = error.message;
  }
}

async function refreshAnalysis() {
  const requestId = ++state.analysisRequestId;
  try {
    await Promise.all([loadHealth(), loadBacktest(requestId)]);
    if (requestId !== state.analysisRequestId) return;
    await loadRecommendations(requestId);
  } catch (error) {
    if (requestId !== state.analysisRequestId) return;
    $("recommendationBody").innerHTML = `<tr><td colspan="10" class="empty">请求失败</td></tr>`;
    $("explainBody").innerHTML = `<p class="empty">${escapeHtml(error.message)}</p>`;
  }
}

async function refreshData() {
  try {
    await Promise.all([loadHealth(), loadDataConfig(), loadInventory(), loadSyncStatus()]);
  } catch (error) {
    $("progressMessage").textContent = error.message;
  }
}

async function refreshResearch() {
  try {
    await Promise.all([loadHealth(), loadDataConfig(), loadInventory(), loadSyncStatus(), loadResearchStatus()]);
    updateResearchAvailability();
  } catch (error) {
    $("researchMessage").textContent = error.message;
  }
}

function switchView(viewId) {
  const leavingDetail = state.currentView === "stockDetailView" && viewId !== "stockDetailView";
  if (leavingDetail) {
    state.detailRequestId += 1;
    destroyStockChart();
  }
  state.currentView = viewId;
  document.querySelectorAll(".view").forEach((view) => { view.hidden = view.id !== viewId; });
  document.querySelectorAll(".main-tab").forEach((tab) => {
    const active = tab.dataset.view === viewId || (viewId === "stockDetailView" && tab.dataset.view === "analysisView");
    tab.classList.toggle("active", active);
    if (active) tab.setAttribute("aria-current", "page");
    else tab.removeAttribute("aria-current");
  });
  $("refreshBtn").textContent = refreshLabels[viewId] || "刷新本页";
  let refreshTask = null;
  if (viewId === "analysisView") refreshTask = refreshAnalysis();
  else if (viewId === "researchView") refreshTask = refreshResearch();
  else if (viewId === "dataView") refreshTask = refreshData();
  if (leavingDetail && viewId === "analysisView") {
    Promise.resolve(refreshTask).finally(() => window.requestAnimationFrame(() => {
      const fallback = [...document.querySelectorAll(`.${state.detailReturnClass}[data-detail-code]`)]
        .find((button) => button.dataset.detailCode === state.detailReturnCode);
      const target = state.detailReturnFocus?.isConnected ? state.detailReturnFocus : fallback;
      target?.focus();
    }));
  }
}

function bindEvents() {
  document.querySelectorAll(".main-tab").forEach((tab) => tab.addEventListener("click", () => navigateToMainView(tab.dataset.view)));
  document.querySelectorAll("[data-jump-view]").forEach((button) => button.addEventListener("click", () => navigateToMainView(button.dataset.jumpView)));
  document.querySelectorAll(".segment").forEach((button) => button.addEventListener("click", async () => {
    document.querySelectorAll(".segment").forEach((item) => {
      const active = item === button;
      item.classList.toggle("active", active);
      item.setAttribute("aria-pressed", String(active));
    });
    button.classList.add("active"); state.horizon = button.dataset.horizon; await refreshAnalysis();
  }));
  $("capitalInput").addEventListener("change", async (event) => {
    const value = Math.min(100000, Math.max(10000, Number(event.target.value) || 50000));
    event.target.value = value;
    state.capital = value;
    await loadRecommendations();
  });
  $("researchCapitalInput").addEventListener("input", (event) => {
    const rawValue = Number(event.target.value);
    if (!Number.isFinite(rawValue)) return;
    const value = Math.min(100000, Math.max(10000, rawValue));
    state.researchCapital = value;
    updateResearchAvailability();
  });
  $("researchCapitalInput").addEventListener("change", (event) => {
    const value = Math.min(100000, Math.max(10000, Number(event.target.value) || 50000));
    event.target.value = value;
    state.researchCapital = value;
    updateResearchAvailability();
  });
  $("minScoreInput").addEventListener("change", async (event) => { state.minScore = event.target.value; await loadRecommendations(); });
  $("refreshBtn").addEventListener("click", () => {
    if (state.currentView === "dataView") refreshData();
    else if (state.currentView === "researchView") refreshResearch();
    else if (state.currentView === "stockDetailView") loadStockDetail();
    else refreshAnalysis();
  });
  $("inventoryRefreshBtn").addEventListener("click", loadInventory);
  $("startSyncBtn").addEventListener("click", startSync);
  $("stopSyncBtn").addEventListener("click", stopSync);
  $("startResearchBtn").addEventListener("click", () => startResearch("auto"));
  $("forceFullResearchBtn").addEventListener("click", () => startResearch("full"));
  $("stopResearchBtn").addEventListener("click", stopResearch);
  $("backToAnalysisBtn").addEventListener("click", returnToAnalysis);
  $("retryDetailBtn").addEventListener("click", loadStockDetail);
  document.querySelectorAll(".range-btn[data-detail-limit]").forEach((button) => button.addEventListener("click", () => {
    const limit = Number(button.dataset.detailLimit);
    if (!Number.isFinite(limit) || limit === state.detailLimit) return;
    state.detailLimit = limit;
    updateDetailRangeButtons();
    loadStockDetail();
  }));
  window.addEventListener("popstate", routeFromLocation);
}

bindEvents();
routeFromLocation();
loadSyncStatus().catch(() => {});
loadResearchStatus().catch(() => {});
setInterval(() => {
  if (["dataView", "researchView"].includes(state.currentView) || taskIsActive(state.syncStatus)) loadSyncStatus().catch(() => {});
  if (state.currentView === "researchView" || taskIsActive(state.researchStatus)) loadResearchStatus().catch(() => {});
}, 1200);
