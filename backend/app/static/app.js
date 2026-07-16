const state = {
  horizon: "20d",
  capital: 50000,
  minScore: "",
  items: [],
  selectedCode: null,
  currentView: "analysisView",
  syncStatus: "idle",
  syncDataset: "a_share_daily_full_v2",
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
};

const tableNames = {
  daily_bar: "原始日线",
  daily_bar_adj: "复权日线",
  adj_factor: "复权因子",
  daily_basic: "每日指标",
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
  $("modeBadge").textContent = health.demo_mode ? "Demo模式" : health.ok ? "MySQL已连接" : "数据库异常";
  $("modeBadge").style.color = health.ok ? "#11845b" : "#c2410c";
}

async function loadBacktest() {
  const summary = await fetchJson(`/api/backtest/summary?horizon=${state.horizon}`);
  $("topReturn").textContent = formatPercent(summary.top_group_return);
  $("benchmarkReturn").textContent = formatPercent(summary.benchmark_return);
  $("winRate").textContent = formatPercent(summary.win_rate);
  $("rankIc").textContent = formatNumber(summary.rank_ic, 3);
}

async function loadRecommendations() {
  const params = new URLSearchParams({ horizon: state.horizon, capital: String(state.capital), limit: "100" });
  if (state.minScore !== "") params.set("min_score", state.minScore);
  const payload = await fetchJson(`/api/recommendations?${params.toString()}`);
  state.items = payload.items || [];
  if (payload.mode === "mysql-empty-demo-fallback") {
    $("modeBadge").textContent = "暂无真实预测 · 当前为演示推荐";
    $("modeBadge").style.color = "#c2410c";
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
    tbody.innerHTML = `<tr><td colspan="9" class="empty">当前资金下暂无可买满一手的结果</td></tr>`;
    return;
  }
  tbody.innerHTML = state.items
    .map((item) => {
      const risks = item.risk_flags?.length ? item.risk_flags.join("、") : "正常";
      const riskClass = item.risk_flags?.length ? "risk" : "risk ok";
      const active = state.selectedCode === item.code ? "active" : "";
      return `<tr class="${active}" data-code="${escapeHtml(item.code)}">
        <td>${item.rank ?? "--"}</td><td>${escapeHtml(item.code)}</td><td>${escapeHtml(item.name)}</td>
        <td>${escapeHtml(item.industry || "未分类")}</td><td class="score">${formatNumber(item.score, 3)}</td>
        <td class="prob">${formatPercent(item.probability)}</td>
        <td>${formatInteger(item.target_shares)}</td><td>${formatInteger(item.target_amount)}</td>
        <td><span class="${riskClass}" title="${escapeHtml(risks)}">${escapeHtml(risks)}</span></td></tr>`;
    })
    .join("");
  tbody.querySelectorAll("tr[data-code]").forEach((row) => row.addEventListener("click", () => selectStock(row.dataset.code)));
}

async function selectStock(code) {
  state.selectedCode = code;
  renderRecommendations();
  $("selectedCode").textContent = code;
  $("explainBody").innerHTML = `<p class="empty">加载中</p>`;
  renderExplanation(await fetchJson(`/api/stocks/${encodeURIComponent(code)}/explain?horizon=${state.horizon}`));
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
      <td>${formatInteger(item.estimated_rows)}</td><td>${item.start_date || "--"}</td><td>${item.end_date || "--"}</td></tr>`).join("")
    : `<tr><td colspan="4" class="empty">暂无数据</td></tr>`;
  const states = inventory.states || [];
  const checkpoint = states.find((item) => item.dataset === state.syncDataset);
  $("dataCheckpoint").textContent = checkpoint
    ? `同步断点 ${checkpoint.last_trade_date || "--"} · ${checkpoint.status}`
    : "同步断点 --";
}

const statusLabels = {
  idle: "空闲", running: "运行中", stopping: "停止中", stopped: "已停止", completed: "已完成", failed: "失败",
};

function renderSyncStatus(payload) {
  const previous = state.syncStatus;
  state.syncStatus = payload.status || "idle";
  const running = ["running", "stopping"].includes(state.syncStatus);
  $("syncStatusBadge").textContent = statusLabels[state.syncStatus] || state.syncStatus;
  $("syncStatusBadge").style.color = state.syncStatus === "failed" ? "#b42318" : running ? "#2563eb" : "#11845b";
  $("progressPercent").textContent = `${Number(payload.progress_pct || 0).toFixed(0)}%`;
  $("progressBar").style.width = `${Math.min(100, Math.max(0, Number(payload.progress_pct || 0)))}%`;
  $("progressMessage").textContent = payload.message || "--";
  $("currentSyncDate").textContent = payload.current_date || "--";
  $("dateProgress").textContent = `${payload.completed_dates || 0} / ${payload.total_dates || 0}`;
  const totals = payload.totals || {};
  $("rawRows").textContent = formatInteger(totals.daily);
  $("adjustedRows").textContent = formatInteger(totals.adjusted);
  $("factorRows").textContent = formatInteger(totals.factors);
  $("basicRows").textContent = formatInteger(totals.basics);
  $("startSyncBtn").disabled = running;
  $("stopSyncBtn").disabled = !running || state.syncStatus === "stopping";
  $("jobIdText").textContent = payload.job_id ? payload.job_id.slice(0, 12) : "--";
  renderLogs(payload.logs || []);
  if (["completed", "failed", "stopped"].includes(state.syncStatus) && previous !== state.syncStatus) loadInventory().catch(() => {});
}

function renderLogs(logs) {
  const container = $("syncLogs");
  if (!logs.length) {
    container.innerHTML = `<p class="empty">暂无日志</p>`;
    return;
  }
  container.innerHTML = logs.map((entry) => `<div class="log-line ${entry.level === "error" ? "error" : ""}">
    <time>${escapeHtml(entry.time)}</time><span>${escapeHtml(entry.message)}</span></div>`).join("");
  container.scrollTop = container.scrollHeight;
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
  try {
    await Promise.all([loadHealth(), loadBacktest()]);
    await loadRecommendations();
  } catch (error) {
    $("recommendationBody").innerHTML = `<tr><td colspan="9" class="empty">请求失败</td></tr>`;
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

function switchView(viewId) {
  state.currentView = viewId;
  document.querySelectorAll(".view").forEach((view) => { view.hidden = view.id !== viewId; });
  document.querySelectorAll(".main-tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.view === viewId));
  if (viewId === "dataView") refreshData();
}

function bindEvents() {
  document.querySelectorAll(".main-tab").forEach((tab) => tab.addEventListener("click", () => switchView(tab.dataset.view)));
  document.querySelectorAll(".segment").forEach((button) => button.addEventListener("click", async () => {
    document.querySelectorAll(".segment").forEach((item) => item.classList.remove("active"));
    button.classList.add("active"); state.horizon = button.dataset.horizon; await refreshAnalysis();
  }));
  $("capitalInput").addEventListener("change", async (event) => {
    const value = Math.min(100000, Math.max(10000, Number(event.target.value) || 50000));
    event.target.value = value;
    state.capital = value;
    await loadRecommendations();
  });
  $("minScoreInput").addEventListener("change", async (event) => { state.minScore = event.target.value; await loadRecommendations(); });
  $("refreshBtn").addEventListener("click", () => state.currentView === "dataView" ? refreshData() : refreshAnalysis());
  $("inventoryRefreshBtn").addEventListener("click", loadInventory);
  $("startSyncBtn").addEventListener("click", startSync);
  $("stopSyncBtn").addEventListener("click", stopSync);
}

bindEvents();
refreshAnalysis();
loadSyncStatus().catch(() => {});
setInterval(() => {
  if (state.currentView === "dataView" || ["running", "stopping"].includes(state.syncStatus)) loadSyncStatus().catch(() => {});
}, 1200);
