const data = window.SCREENING_DASHBOARD_DATA;
const deep = window.SCREENING_DEEP_ANALYSIS || { memos: [], portfolioPlan: {} };
const modelHistory = window.MODEL_TRADE_HISTORY || { policies: [] };
const nf = new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 1 });
const nf0 = new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 0 });
const LIVE_STATUS_FILE = "./dashboard_live_update_status.json";
const portfolioStorageKey = "hose_hnx_portfolio_v1";
const PERFORMANCE_START_DATE = "2021-01-01";
const LIVE_REFRESH_INTERVAL_MS = 5 * 60 * 1000;
const BUNDLED_LIVE_STALE_MS = 15 * 60 * 1000;
let portfolio = { navBil: 1, cashBil: null, holdings: [] };
let lastLiveRefreshAt = null;
let onlineLiveRefreshTimer = null;
const ACTIVE_POLICY_KEYS = [
  "r46_bear_stop_mcore",
];
const POLICY_DISPLAY = {
  r46_bear_stop_mcore: "R46 Bear Stop",
  technical_t2_vni30_v13: "T2 VNI+30 Research (loophole, not production)",
  rank_best_full_tier_a: "Best hiện tại - sạch",
};

// Dashboard version-keyed cache invalidation — auto clear stale localStorage when dashboard upgrades
const DASHBOARD_VERSION = "live_quote_refresh_2026_05_29_v10_online_poll_fallback";
const storedVersion = localStorage.getItem("hose_hnx_dashboard_version");
if (storedVersion !== DASHBOARD_VERSION) {
  // Wipe all dashboard-related localStorage keys
  Object.keys(localStorage).filter((k) => k.startsWith("hose_hnx_")).forEach((k) => localStorage.removeItem(k));
  localStorage.setItem("hose_hnx_dashboard_version", DASHBOARD_VERSION);
}
const storedStrategyMode = localStorage.getItem("hose_hnx_strategy_mode");
let strategyMode = storedStrategyMode || deep.defaultPolicy || ACTIVE_POLICY_KEYS[0];
let resizeRenderTimer = null;
// Force-upgrade legacy strategy modes to the current champion default
const LEGACY_MODES_TO_UPGRADE = [
  "phase18_meanreversion_boost",
  "weekly_alpha_v6",
  "pipeline_ensemble",
  "cyclical_overlay",
  "phase17_antigap_v7_candidate",
];
if (
  deep.defaultPolicy
  && LEGACY_MODES_TO_UPGRADE.includes(storedStrategyMode)
  && storedStrategyMode !== deep.defaultPolicy
) {
  strategyMode = deep.defaultPolicy;
  localStorage.setItem("hose_hnx_strategy_mode", strategyMode);
}
if (!ACTIVE_POLICY_KEYS.includes(strategyMode)) {
  strategyMode = ACTIVE_POLICY_KEYS.includes(deep.defaultPolicy) ? deep.defaultPolicy : ACTIVE_POLICY_KEYS[0];
  localStorage.setItem("hose_hnx_strategy_mode", strategyMode);
}
let performanceRange = localStorage.getItem("hose_hnx_performance_range") || "all";
const memoBySymbol = new Map((deep.memos || []).map((item) => [String(item.symbol || "").toUpperCase(), item]));
const stockBySymbol = new Map((data.all || []).map((item) => [String(item.symbol || "").toUpperCase(), item]));

function f(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return new Intl.NumberFormat("vi-VN", { maximumFractionDigits: digits }).format(Number(value));
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#39;"
  })[char]);
}

function n(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function lastItem(arr) {
  return Array.isArray(arr) && arr.length ? arr[arr.length - 1] : undefined;
}

function pad2(value) {
  return String(value).padStart(2, "0");
}

function formatTimeLabel(ts) {
  if (!ts) return "";
  const d = ts instanceof Date ? ts : new Date(ts);
  if (Number.isNaN(d.getTime())) return "";
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
}

function performanceChartHeight() {
  const w = Number(window.innerWidth) || 1280;
  if (w <= 420) return 180;
  if (w <= 560) return 200;
  if (w <= 720) return 220;
  if (w <= 960) return 260;
  return 320;
}

function isLocalDashboardHost() {
  const host = String(window.location.hostname || "").toLowerCase();
  return host === "localhost" || host === "127.0.0.1" || host === "";
}

function setStaticUpdateMode() {
  const btn = document.getElementById("updateBtn");
  const statusEl = document.getElementById("serverStatusText");
  if (!btn) return;
  const isLocal = isLocalDashboardHost();
  btn.style.display = isLocal ? "" : "none";
  if (!isLocal && statusEl) {
    statusEl.textContent = "Bản online: cập nhật giá tự động theo lịch 5 phút.";
  }
}

function normalizeQuoteRow(row = {}) {
  const symbol = String(row.symbol || "").toUpperCase().trim();
  if (!symbol) return null;
  const closeRaw = Number(row.close);
  if (!Number.isFinite(closeRaw) || closeRaw <= 0) return null;
  const rawDate = String(row.date || row.latestDate || row.latest || "").trim();
  const date = /^\d{4}-\d{2}-\d{2}$/.test(rawDate) ? rawDate : null;
  return { symbol, close: closeRaw, date };
}

async function fetchBundledLiveQuotes() {
  try {
    const res = await fetch(LIVE_STATUS_FILE, { cache: "no-store" });
    if (!res.ok) return null;
    const payload = await res.json();
    const quoteMap = new Map();
    const quotes = payload?.quotes || payload?.prices || [];
    if (Array.isArray(quotes)) {
      for (const item of quotes) {
        const parsed = normalizeQuoteRow(item);
        if (parsed) quoteMap.set(parsed.symbol, parsed);
      }
    } else if (quotes && typeof quotes === "object") {
      for (const [symbol, raw] of Object.entries(quotes)) {
        const parsed = normalizeQuoteRow({ symbol, ...raw });
        if (parsed) quoteMap.set(parsed.symbol, parsed);
      }
    }
    return {
      quoteMap,
      latestDate: (String(payload?.latestPriceDate || payload?.latest_date || "").slice(0, 10)) || null,
      updatedAt: String(payload?.updatedAt || ""),
      updatedAtMs: Date.parse(String(payload?.updatedAt || "").replace(" ", "T")),
      fetched: true,
    };
  } catch {
    return null;
  }
}

function fallbackQuotesFromPolicyHoldings(symbols) {
  const out = new Map();
  for (const symbol of symbols) {
    for (const policy of deep.strategyPolicies || []) {
      for (const row of policy?.holdings || []) {
        const rowSymbol = String(row?.symbol || "").toUpperCase().trim();
        if (!rowSymbol || rowSymbol !== symbol) continue;
        const closeRaw = Number(row.currentPrice);
        if (!Number.isFinite(closeRaw) || closeRaw <= 0) continue;
        const fallbackDate = String(
          row.priceAsOf || row.historyLastDate || row.entryDate || (data?.summary?.as_of || "")
        ).slice(0, 10);
        out.set(rowSymbol, {
          symbol: rowSymbol,
          close: closeRaw,
          date: fallbackDate || null,
        });
        break;
      }
    }
  }
  return out;
}

function uniqueSymbolsForLiveRefresh() {
  const symbols = new Set();
  const active = activePolicy();
  const scoped = active?.holdings?.length ? [active] : (deep.strategyPolicies || []);
  for (const policy of scoped) {
    for (const row of policy?.holdings || []) {
      const symbol = String(row?.symbol || "").toUpperCase().trim();
      if (symbol) symbols.add(symbol);
    }
  }
  return [...symbols];
}

async function fetchDailyClose(symbol) {
  const nowSec = Math.floor(Date.now() / 1000);
  const fromSec = nowSec - 86400 * 14;
  const url = `https://histdatafeed.vps.com.vn/tradingview/history?symbol=${encodeURIComponent(symbol)}&resolution=D&from=${fromSec}&to=${nowSec}`;
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`${symbol}: HTTP ${res.status}`);
  const payload = await res.json();
  if (payload?.s !== "ok" || !Array.isArray(payload?.t) || !payload.t.length) {
    throw new Error(`${symbol}: no_data`);
  }
  const idx = payload.t.length - 1;
  const closeRaw = Number(payload.c?.[idx]);
  const close = Number.isFinite(closeRaw) ? (closeRaw > 1000 && symbol !== "VNINDEX" ? closeRaw / 1000 : closeRaw) : NaN;
  const ts = Number(payload.t[idx]) * 1000;
  if (!Number.isFinite(close) || close <= 0 || !Number.isFinite(ts)) throw new Error(`${symbol}: bad_payload`);
  const date = new Date(ts).toISOString().slice(0, 10);
  return { symbol, close, date };
}

async function refreshLiveMarketData() {
  const symbols = uniqueSymbolsForLiveRefresh();
  if (!symbols.length) return { ok: false, reason: "no_symbols" };
  const targets = ["VNINDEX", ...symbols];
  let source = "live_direct";
  let quoteMap = null;
  let staleBundledQuoteMap = null;
  if (!isLocalDashboardHost()) {
    const bundled = await fetchBundledLiveQuotes();
    if (bundled?.quoteMap?.size) {
      const ageMs = Number.isFinite(bundled.updatedAtMs) ? Date.now() - bundled.updatedAtMs : Infinity;
      if (ageMs <= BUNDLED_LIVE_STALE_MS) {
        quoteMap = bundled.quoteMap;
        source = "bundled";
      } else {
        staleBundledQuoteMap = bundled.quoteMap;
        source = "bundled_stale";
      }
    }
  }
  if (!quoteMap) {
    const settled = await Promise.allSettled(targets.map((symbol) => fetchDailyClose(symbol)));
    quoteMap = new Map();
    for (const item of settled) {
      if (item.status === "fulfilled") {
        quoteMap.set(item.value.symbol, item.value);
      }
    }
  }
  if (!quoteMap.size && staleBundledQuoteMap?.size) {
    quoteMap = staleBundledQuoteMap;
    source = "bundled_stale";
  }
  if (!quoteMap.size) {
    const fallback = fallbackQuotesFromPolicyHoldings(symbols);
    if (fallback.size) {
      quoteMap = fallback;
      source = source === "live_direct" ? "cached_holdings" : `${source}+cached_holdings`;
    }
  }
  const refreshedSymbolCount = symbols.filter((symbol) => quoteMap.has(symbol)).length;
  const totalSymbols = symbols.length;
  const ok = refreshedSymbolCount;
  const sortedDates = [...quoteMap.values()].map((x) => x.date).filter(Boolean).sort();
  if (!ok) return { ok: false, reason: "all_failed", source };
  let latestDate = sortedDates.length ? sortedDates[sortedDates.length - 1] : null;
  if (!latestDate && source === "bundled" && data?.summary?.as_of) {
    latestDate = data.summary.as_of;
  }

  for (const policy of deep.strategyPolicies || []) {
    for (const row of policy?.holdings || []) {
      const symbol = String(row?.symbol || "").toUpperCase().trim();
      const q = quoteMap.get(symbol);
      if (!q) continue;
      row.currentPrice = q.close;
      row.priceAsOf = q.date;
      const shares = roundLot(displayTradeShares(row));
      if (shares > 0) {
        const currentValueMil = (shares * q.close) / 1000;
        row.currentValueMil = currentValueMil;
        const entry = Number(row.entryPrice) || 0;
        const costMil = entry > 0 ? (shares * entry) / 1000 : Number(row.costMil) || 0;
        if (costMil > 0) {
          row.costMil = costMil;
          row.currentPnlMil = currentValueMil - costMil;
          row.currentPnlPct = ((q.close / entry) - 1) * 100;
        }
      }
    }
  }
  if (data?.summary && latestDate) data.summary.as_of = latestDate;
  lastLiveRefreshAt = new Date();
  return {
    ok: true,
    refreshed: refreshedSymbolCount,
    total: totalSymbols,
    latestDate,
    source,
    refreshedAt: lastLiveRefreshAt.toISOString(),
    refreshedAtText: formatTimeLabel(lastLiveRefreshAt),
  };
}

async function refreshOnlineLivePrices() {
  if (isLocalDashboardHost()) return;
  const statusEl = document.getElementById("serverStatusText");
  try {
    const refreshed = await refreshLiveMarketData();
    if (!refreshed.ok) return;
    renderActiveModel();
    if (statusEl) {
      const sourceText = refreshed.source === "bundled"
        ? "dữ liệu deploy tự động"
        : refreshed.source === "bundled_stale"
          ? "dữ liệu deploy gần nhất"
          : "nguồn trực tiếp";
      statusEl.textContent = `Đã cập nhật giá live cho ${refreshed.refreshed}/${refreshed.total} mã lúc ${refreshed.refreshedAtText || "--:--:--"} từ ${sourceText} (ngày giá ${refreshed.latestDate || "-"})`;
    }
  } catch (err) {
    if (statusEl) {
      statusEl.textContent = "Chưa thể cập nhật giá live từ bản deploy, thử lại sau vài phút.";
    }
  }
}

function roundLot(shares) {
  const value = Number(shares) || 0;
  if (value <= 0) return 0;
  return Math.floor(value / 100) * 100;
}

function firstPositive(...values) {
  for (const value of values) {
    const parsed = Number(value);
    if (Number.isFinite(parsed) && parsed > 0) return parsed;
  }
  return 0;
}

function displayTradeShares(row = {}) {
  return firstPositive(
    row.orderShares,
    row.shares,
    row.rawShares,
    row.currentCopyShares,
    row.targetCopyShares,
    row.modelShares,
    row.copyShares
  );
}

function displayTradeValueMil(row = {}, shares = 0, priceK = 0) {
  const explicit = firstPositive(
    row.orderValueMil,
    row.currentValueMil,
    row.modelValueMil,
    Number(row.grossBil) ? Number(row.grossBil) * 1000 : 0
  );
  if (explicit) return explicit;
  return shares && priceK ? shares * Number(priceK) / 1000 : 0;
}

function displayTradePnlMil(row = {}, shares = 0, priceK = 0, entryPriceK = 0, grossMil = 0) {
  if (row.currentPnlMil !== null && row.currentPnlMil !== undefined && Number.isFinite(Number(row.currentPnlMil))) {
    return Number(row.currentPnlMil);
  }
  if (row.pnlBil !== null && row.pnlBil !== undefined && Number.isFinite(Number(row.pnlBil))) {
    return Number(row.pnlBil) * 1000;
  }
  if (row.returnPct !== null && row.returnPct !== undefined && Number.isFinite(Number(row.returnPct)) && grossMil) {
    return grossMil * Number(row.returnPct) / 100;
  }
  if (shares && priceK && entryPriceK) {
    return (Number(priceK) - Number(entryPriceK)) * shares / 1000;
  }
  return null;
}

function orderNote(label, priceK, smallLotNote = "") {
  const short = {
    "MUA MỚI": "Mua mới",
    "MUA THÊM": "Mua thêm",
    "BÁN 1 PHẦN": "Giảm tỷ trọng",
    "BÁN HẾT": "Thoát mã",
    "BỎ QUA": "Không khớp",
  }[label] || label;
  return `${short}${priceK ? ` @ ${f(priceK)}k` : ""}${smallLotNote}`;
}

function isBuyAction(value) {
  return displayAction(value).startsWith("MUA");
}

function isSellAction(value) {
  return displayAction(value).startsWith("BÁN");
}

function moneyMilLabel(valueMil) {
  const value = Number(valueMil) || 0;
  if (Math.abs(value) >= 1000) return `${f(value / 1000, 2)} tỷ`;
  return `${f(value, 0)} tr`;
}

function modelInitialNavMil() {
  const configured = Number(deep.initialCapital?.amount_vnd);
  return (Number.isFinite(configured) && configured > 0 ? configured : 1_000_000_000) / 1e6;
}

function performanceWindowEndDate(hist = activeHistory()) {
  const rows = allPerformanceRows(hist);
  return lastItem(rows)?.date || data?.summary?.as_of || null;
}

function performanceWindowLabel(hist = activeHistory()) {
  const endDate = performanceWindowEndDate(hist);
  const endYear = endDate ? String(endDate).slice(0, 4) : "";
  return endYear ? `2021 - ${endYear}` : "2021 - hiện tại";
}

function badge(status) {
  const label = displayAction(status || "-");
  const cls = actionClass(status || "");
  return `<span class="badge ${cls}">${label}</span>`;
}

function displayAction(value) {
  const raw = String(value || "").toUpperCase();
  if (!raw || raw === "-") return "-";
  if (raw.includes("BỎ") || raw.includes("MISS") || raw.includes("SKIP")) return "BỎ QUA";
  if (raw.includes("MUA THÊM")) return "MUA THÊM";
  if (raw.includes("MUA MỚI") || raw.includes("MUA MOI")) return "MUA MỚI";
  if (raw.includes("BÁN 1 PHẦN") || raw.includes("BAN 1 PHAN") || raw.includes("BÁN MỘT PHẦN")) return "BÁN 1 PHẦN";
  if (raw.includes("BÁN HẾT") || raw.includes("BAN HET")) return "BÁN HẾT";
  if (raw.includes("BUY") || raw === "MUA" || raw.includes("ACCUMULATE")) return "MUA";
  if (
    raw.includes("SELL")
    || raw.includes("BREAK")
    || raw.includes("DECAY")
    || raw.includes("FAIL")
    || raw.includes("FALLOFF")
    || raw.includes("BÁN")
    || raw.includes("THOÁT")
  ) return "BÁN";
  if (raw.includes("TARGET") || raw.includes("MỤC TIÊU") || raw.includes("MUC_TIEU")) return "MỤC TIÊU";
  if (raw.includes("HOLD") || raw.includes("GIỮ")) return "GIỮ";
  if (raw.includes("WATCH") || raw.includes("THEO")) return "THEO DÕI";
  return value;
}

function actionClass(value) {
  const label = displayAction(value);
  if (label.startsWith("MUA")) return "buy";
  if (label.startsWith("BÁN")) return "sell";
  if (label === "THEO DÕI") return "watch";
  if (label === "BỎ QUA") return "watch";
  return "hold";
}

function statusClass(value) {
  const raw = String(value || "").toUpperCase();
  if (raw.includes("KHỚP") || raw.includes("CHUẨN BỊ")) return "buy";
  if (raw.includes("BỎ") || raw.includes("CHỜ")) return "watch";
  if (raw.includes("BÁN")) return "sell";
  return "hold";
}

function scoreBar(value) {
  const v = Number(value || 0);
  return `<div class="bar"><i style="width:${Math.max(0, Math.min(100, v))}%"></i></div>`;
}

function renderKpis() {
  const s = data.summary;
  const hist = activeHistory();
  const policy = activePolicy();
  const holdings = policy?.holdings || [];
  const audit = policy?.productionAudit || null;
  const perf = policyPerformanceSummary(hist);
  const perfWindow = performanceWindowLabel(hist);
  const totalWeight = holdings.reduce((sum, item) => sum + n(item.suggestedWeight), 0);
  const explicitCash = Number(policy?.cashBuffer);
  const cashPct = policy ? (explicitCash > 0 ? explicitCash : Math.max(0, 100 - totalWeight)) : 0;
  const asset = currentAssetSnapshot();
  const kpiValue = (value) => typeof value === "number" ? nf0.format(value) : value;
  const marketDate = lastItem(holdings.map((h) => h.priceAsOf).filter(Boolean).sort()) || s.as_of;
  const liveClock = formatTimeLabel(lastLiveRefreshAt);
  document.getElementById("asOf").textContent = liveClock
    ? `Giá đến ${marketDate} lúc ${liveClock}`
    : `Giá đến ${marketDate}`;
  document.getElementById("kpis").innerHTML = [
    ["Policy", policyName(policy)],
    ["Tài sản", moneyMilLabel(asset.currentAssetMil)],
    ["Lãi/lỗ", `${asset.gainPct >= 0 ? "+" : ""}${f(asset.gainPct)}%`],
    [`CAGR ${perfWindow}`, perf ? `${f(perf.cagr)}%` : (audit ? `${f(audit.cagr)}%` : "-")],
    [`MaxDD ${perfWindow}`, perf ? `${f(perf.maxDrawdown)}%` : (audit ? `${f(audit.maxDrawdown)}%` : "-")],
    ["Vị thế / Cash", `${holdings.length} mã / ${f(cashPct)}%`]
  ].map(([label, value, note]) => `
    <article class="kpi">
      <span>${label}</span>
      <strong>${kpiValue(value)}</strong>
      ${note ? `<small>${note}</small>` : ""}
    </article>
  `).join("");
}

function policyName(policy) {
  if (!policy) return "Model portfolio";
  return POLICY_DISPLAY[policy.key] || String(policy.label || "Model portfolio").replace(/⭐/g, "").trim();
}

function activePolicies() {
  const policies = deep.strategyPolicies || [];
  const filtered = ACTIVE_POLICY_KEYS
    .map((key) => policies.find((item) => item.key === key))
    .filter(Boolean);
  return filtered.length ? filtered : policies.slice(0, 1);
}

function syncStrategyOptions() {
  const select = document.getElementById("strategyMode");
  if (!select) return;
  const policies = activePolicies();
  const perfWindow = performanceWindowLabel(activeHistory());
  select.innerHTML = policies.map((policy) => {
    const audit = policy.productionAudit;
    const suffix = Number.isFinite(Number(policy.historicalCagr))
      ? `${perfWindow} CAGR ${f(policy.historicalCagr)}%, MaxDD ${f(policy.historicalMaxDrawdown)}%`
      : audit
      ? `Strict 100-lot VNI+20 ${f(audit.passVni20 ?? audit.passVni30, 0)}/6`
      : `CAGR ${f(policy.historicalCagr)}%, Sharpe ${f(policy.historicalSharpe, 2)}`;
    return `<option value="${esc(policy.key)}">${esc(policyName(policy))} (${suffix})</option>`;
  }).join("");
  if (!policies.some((policy) => policy.key === strategyMode)) {
    strategyMode = policies[0]?.key || strategyMode;
    localStorage.setItem("hose_hnx_strategy_mode", strategyMode);
  }
  select.value = strategyMode;
  select.disabled = policies.length <= 1;
}

function setModelLoading(message = "") {
  const el = document.getElementById("modelLoading");
  if (!el) return;
  el.classList.toggle("is-active", Boolean(message));
  el.querySelector("span").textContent = message;
}

function renderActiveModel() {
  arrangePortfolioLayout();
  renderKpis();
  renderWatchlistTab();
  renderModelLogicTab();
  renderPortfolio();
}

function renderModelMethodology() {
  renderModelLogicTab();
}

function renderModelLogicTab() {
  const body = document.getElementById("modelLogicBody");
  if (!body) return;
  const policy = activePolicy();
  const perf = policyPerformanceSummary(activeHistory());
  const period = perf ? `${perf.startDate} đến ${perf.endDate}` : `${performanceWindowLabel(activeHistory())}`;
  const status = document.getElementById("modelLogicStatus");
  if (status) status.textContent = policy?.methodology?.status || "Candidate preview";
  const audit = policy?.productionAudit || null;
  const failText = audit?.failYears?.length ? audit.failYears.join(", ") : "không";
  const auditText = audit
    ? (audit.status === "R23_NAV3B"
      ? `R23_NAV3B tính fixed live NAV 3 tỷ, cap 20% ADV và thêm trượt giá ${f((audit.slippageBps || 15) / 100, 2)}% mỗi chiều. Giai đoạn ${period} đạt VNI+20 ${f(audit.passVni20 ?? audit.passVni30, 0)}/6 năm, VNI+30 ${f(audit.passVni30, 0)}/6 năm, CAGR ${perf ? f(perf.cagr) : f(audit.cagr)}%, MaxDD ${perf ? f(perf.maxDrawdown) : f(audit.maxDrawdown)}%, min edge ${f(audit.minEdgeVsVni)} điểm %. Ở stress 30bps vẫn giữ VNI+20 6/6.`
      : audit.status === "R46_BEAR_STOP_15BPS"
      ? `R46 Bear Stop tính fixed live NAV 3 tỷ, cap 20% ADV và thêm trượt giá ${f((audit.slippageBps || 15) / 100, 2)}% mỗi chiều. Giai đoạn ${period} đạt VNI+20 ${f(audit.passVni20, 0)}/6 năm, VNI+30 ${f(audit.passVni30, 0)}/6 năm, CAGR ${perf ? f(perf.cagr) : f(audit.cagr)}%, MaxDD ${perf ? f(perf.maxDrawdown) : f(audit.maxDrawdown)}%, min edge ${f(audit.minEdgeVsVni)} điểm %. Ở stress 20bps, VNI+30 recent còn 5/6 nên cần theo dõi chi phí khớp lệnh thật.`
      : `Backtest tính thêm trượt giá ${f((audit.slippageBps || 15) / 100, 2)}% mỗi chiều. Strict 100-lot hiện đạt VNI+20 ${f(audit.passVni20 ?? audit.passVni30, 0)}/6 năm, CAGR ${f(audit.cagr)}%, MaxDD ${f(audit.maxDrawdown)}%, min edge ${f(audit.minEdgeVsVni)} điểm %, fail năm ${failText}. VNI+30 đạt ${f(audit.passVni30, 0)}/6. Nếu trượt giá thực tế tăng lên 20bps, gate VNI+20 giảm còn 4/6 nên dashboard cần theo dõi chi phí khớp lệnh thật.`)
    : "Backtest tính thêm trượt giá 0,15% mỗi chiều. Strict 100-lot daily execution hiện chưa có audit mới.";
  const cards = policy?.methodology?.cards || [
    ["1. Tài sản được phép", "Chỉ cổ phiếu thường HOSE/HNX. Không ETF, không trái phiếu, không margin, không bán khống. Tiền mặt được giữ nhưng lãi tiền mặt = 0%."],
    ["2. Mục tiêu kiểm tra", "Gate đang áp dụng cho dashboard: mỗi năm model phải hơn VN-Index ít nhất 20 điểm %. VNI+30 vẫn được theo dõi như mục tiêu cao hơn, nhưng không phải điều kiện hiển thị chính."],
    ["3. Vốn hóa & khả năng mua bán", "Candidate hiện không đặt ngưỡng vốn hóa cứng; rào chính là thanh khoản. Mã phải giao dịch bình quân 20 phiên >= 3 tỷ đồng/ngày. Một lệnh không được chiếm quá 20% giá trị giao dịch bình quân 20 phiên. Nếu đưa vào production có thể thêm ngưỡng vốn hóa >= 1.500 tỷ như lớp audit bảo thủ."],
    ["4. Lọc xu hướng giá", "Tỷ suất 13 tuần phải >= -5%. Giá hiện tại phải còn ít nhất 75% so với đỉnh 52 tuần, tức không được rơi quá sâu khỏi vùng đỉnh cũ. Điểm xu hướng phải qua ngưỡng 35/100 trước khi được xếp hạng."],
    ["5. Lọc RSI", "RSI 14 phiên bình thường phải nằm trong vùng 40-75. Nếu cổ phiếu đang breakout sát đỉnh 52 tuần thì cho phép RSI cao hơn, tối đa 95, để không bỏ lỡ nhóm tăng mạnh như oil/chemical đầu 2026."],
    ["6. Dòng tiền", "Điểm dòng tiền phải >= 35/100. Điểm này dùng giá trị giao dịch và sức mua tương đối để tránh mã chỉ tăng giá nhưng không có tiền thật đi kèm."],
    ["7. Nhóm ngành", "Điểm ngành phải >= 35/100. Nếu trong 4 tuần gần nhất có nhiều mã cùng ngành cùng breakout, model cộng điểm ngành nóng: +10 điểm nền, cộng thêm theo số mã breakout và độ mạnh của cụm ngành."],
    ["8. Công thức xếp hạng", "Các nhóm điểm chính: sức mạnh tương đối 13 tuần 42,1%; điểm ngành 29,3%; chất lượng/cơ bản 16,2%; vị trí gần đỉnh 52 tuần 10,9%; momentum toàn thị trường 1,3%; kỹ thuật nền 0,2%. Mã có tổng điểm < 35 bị loại."],
    ["9. Chọn danh mục", "Tối đa 3 mã. Mỗi mã tối đa 33% NAV. Không lấy quá 1 mã trong cùng một ngành ở cùng kỳ. Nếu không đủ mã đạt chuẩn thì phần còn lại để cash."],
    ["10. Phanh thị trường", "Khi VN-Index 13 tuần < -7%, 4 tuần < -5% và nằm dưới SMA40 tuần, model chuyển về phòng thủ. Khi VN-Index 4 tuần > +1% hoặc 13 tuần > 0%, model được quay lại. Nếu breadth quá yếu dưới 15% thì tỷ trọng cổ phiếu về 0%."],
    ["11. Giá đặt lệnh", "Tín hiệu chốt cuối tuần. Đầu tuần sau model không mua đuổi nếu giá mở cửa sát trần hoặc tăng quá mạnh so với close tham chiếu. Ngưỡng mua được chặn theo từng sàn: HOSE khoảng 6,5%; HNX/UPCoM dùng mức thấp hơn giữa 9% và biên độ sàn trừ vùng an toàn. Nếu vượt ngưỡng, model chờ tối đa 2 phiên để giá quay lại vùng close tham chiếu; không quay lại thì bỏ qua. Nếu open thấp hơn close tham chiếu thì mua ở giá thấp hơn."],
    ["12. Bán & làm tròn lệnh", "Lệnh bán chỉ thực hiện sau khi cổ phiếu đã đủ tối thiểu 3 phiên giao dịch, tương ứng T+2,5. Toàn bộ lệnh copy trade làm tròn xuống lô 100 cổ phiếu; phần dưới 100 cổ phiếu không đặt lệnh."],
    ["13. Chi phí & trạng thái", auditText],
  ];
  body.innerHTML = `
    <ol class="policy-checklist">
      ${cards.map(([title, text]) => `
        <li>
          <b>${esc(title)}</b>
          <span>${esc(text)}</span>
        </li>
      `).join("")}
    </ol>
  `;
}

async function getServerStatus() {
  try {
    const res = await fetch("/api/status", { cache: "no-store" });
    if (!res.ok) throw new Error("server unavailable");
    return await res.json();
  } catch {
    return null;
  }
}

async function refreshStatus() {
  if (!isLocalDashboardHost()) {
    setStaticUpdateMode();
    return;
  }
  const status = await getServerStatus();
  const el = document.getElementById("serverStatusText");
  const updateBtn = document.getElementById("updateBtn");
  if (!status) {
    el.textContent = "Mở bằng open-dashboard-server.cmd để dùng nút update.";
    if (updateBtn) updateBtn.disabled = true;
    return;
  }
  if (updateBtn) updateBtn.disabled = Boolean(status.running);
  el.textContent = status.running
    ? `Đang cập nhật từ ${status.started_at}...`
    : `${status.message}${status.finished_at ? ` | ${status.finished_at}` : ""}`;
}

async function triggerUpdate(mode) {
  if (!isLocalDashboardHost()) {
    const el = document.getElementById("serverStatusText");
    if (el) el.textContent = "Đang lấy giá live...";
    try {
      const refreshed = await refreshLiveMarketData();
      if (!refreshed.ok) {
        if (el) el.textContent = "Không lấy được giá live lúc này.";
        alert("Không lấy được giá live, anh thử lại sau vài giây.");
        return;
      }
      renderActiveModel();
      if (el) {
        const sourceText = refreshed.source === "bundled" ? "dữ liệu deploy tự động" : "nguồn trực tiếp";
        el.textContent = `Đã cập nhật giá live cho ${refreshed.refreshed}/${refreshed.total} mã lúc ${refreshed.refreshedAtText || "--:--:--"} từ ${sourceText} (ngày giá ${refreshed.latestDate || "-"})`;
      }
    } catch (err) {
      if (el) el.textContent = "Lỗi kết nối nguồn giá live.";
      alert(`Lỗi update live: ${err?.message || err}`);
    }
    return;
  }
  const endpoint = "/api/update-fast";
  const label = "Cập nhật";
  const res = await fetch(endpoint, { method: "POST" });
  if (!res.ok && res.status !== 409) {
    alert(`${label} không chạy được. Hãy mở dashboard qua open-dashboard-server.cmd.`);
    return;
  }
  await refreshStatus();
  const timer = setInterval(async () => {
    const status = await getServerStatus();
    await refreshStatus();
    if (status && !status.running) {
      clearInterval(timer);
      if (status.ok) {
        window.location.reload();
      } else {
        alert(status.message || `${label} failed`);
      }
    }
  }, 1200);
}

function drawBarChart(canvasId, rows, colors) {
  const canvas = document.getElementById(canvasId);
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * dpr;
  canvas.height = canvas.height * dpr;
  ctx.scale(dpr, dpr);
  const width = rect.width;
  const height = canvas.height / dpr;
  const pad = 34;
  const max = Math.max(...rows.map((r) => r.value), 1);
  ctx.clearRect(0, 0, width, height);
  ctx.font = "12px Segoe UI";
  rows.forEach((row, i) => {
    const barW = (width - pad * 2) / rows.length - 16;
    const x = pad + i * ((width - pad * 2) / rows.length) + 8;
    const h = (row.value / max) * (height - pad * 2);
    const y = height - pad - h;
    ctx.fillStyle = colors[i % colors.length];
    ctx.fillRect(x, y, barW, h);
    ctx.fillStyle = "#8d9aaa";
    ctx.textAlign = "center";
    ctx.fillText(row.name, x + barW / 2, height - 12);
    ctx.fillStyle = "#edf3f8";
    ctx.fillText(row.value, x + barW / 2, y - 7);
  });
}

function chartTickCount(range, width) {
  if (range === "3m") return width < 760 ? 5 : 8;
  if (range === "6m") return width < 760 ? 6 : 10;
  if (range === "ytd") return width < 760 ? 6 : 10;
  if (range === "1y") return width < 760 ? 6 : 11;
  return width < 760 ? 6 : 12;
}

function chartTickLabel(date, range) {
  const d = new Date(date);
  if (Number.isNaN(d.getTime())) return String(date || "");
  const dd = String(d.getDate()).padStart(2, "0");
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const yy = String(d.getFullYear()).slice(-2);
  if (range === "3m" || range === "6m" || range === "ytd") return `${dd}/${mm}`;
  return `${mm}/${yy}`;
}

function nearestPoint(points, x) {
  if (!points.length) return null;
  return points.reduce((best, point) => (
    !best || Math.abs(point.x - x) < Math.abs(best.x - x) ? point : best
  ), null);
}

function installChartTooltip(canvas, series, helpers, options = {}) {
  const host = canvas.parentElement || canvas;
  let tooltip = document.getElementById("performanceTooltip");
  if (!tooltip) {
    tooltip = document.createElement("div");
    tooltip.id = "performanceTooltip";
    tooltip.className = "chart-tooltip";
    host.appendChild(tooltip);
  }
  let crosshair = document.getElementById("performanceCrosshair");
  if (!crosshair) {
    crosshair = document.createElement("div");
    crosshair.id = "performanceCrosshair";
    crosshair.className = "chart-crosshair";
    host.appendChild(crosshair);
  }
  const modelPoints = series[0]?.points || [];
  const vniPoints = series[1]?.points || [];
  const { padL, padR, padT, plotW, plotH, xFor } = helpers;
  canvas.onmouseleave = () => {
    tooltip.hidden = true;
    crosshair.hidden = true;
  };
  canvas.onmousemove = (event) => {
    const rect = canvas.getBoundingClientRect();
    const hostRect = host.getBoundingClientRect();
    const canvasLeft = rect.left - hostRect.left;
    const canvasTop = rect.top - hostRect.top;
    const mouseX = event.clientX - rect.left;
    const mouseY = event.clientY - rect.top;
    if (mouseX < padL || mouseX > rect.width - padR || mouseY < padT || mouseY > padT + plotH) {
      tooltip.hidden = true;
      crosshair.hidden = true;
      return;
    }
    const x = Math.max(0, Math.min(1, (mouseX - padL) / plotW));
    const model = nearestPoint(modelPoints, x);
    if (!model) {
      tooltip.hidden = true;
      crosshair.hidden = true;
      return;
    }
    const vni = vniPoints.find((point) => point.date === model.date) || nearestPoint(vniPoints, model.x);
    tooltip.hidden = false;
    crosshair.hidden = false;
    tooltip.innerHTML = `
      <b>${esc(model.date)}</b>
      <span>Model NAV: ${moneyMilLabel(model.navMil)} (${model.value >= 0 ? "+" : ""}${f(model.value)}%)</span>
      <span>VNI quy đổi: ${vni?.navMil ? moneyMilLabel(vni.navMil) : "-"}${vni ? ` (${vni.value >= 0 ? "+" : ""}${f(vni.value)}%)` : ""}</span>
      <span>VNI close: ${vni?.vniClose ? f(vni.vniClose, 2) : "-"}</span>
    `;
    const tooltipW = tooltip.offsetWidth || 220;
    const tooltipH = tooltip.offsetHeight || 92;
    const px = xFor(model.x);
    const tooltipLeft = canvasLeft + Math.max(8, Math.min(rect.width - tooltipW - 8, px + 12));
    const tooltipTop = canvasTop + Math.max(8, Math.min(rect.height - tooltipH - 8, mouseY - tooltipH - 10));
    crosshair.style.left = `${canvasLeft + px}px`;
    crosshair.style.top = `${canvasTop + padT}px`;
    crosshair.style.height = `${plotH}px`;
    tooltip.style.left = `${tooltipLeft}px`;
    tooltip.style.top = `${tooltipTop}px`;
  };
}

function drawLineChart(canvasId, series, colors, options = {}) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  if (!rect.width) return;
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const cssHeight = Number(canvas.getAttribute("height")) || rect.height || 320;
  canvas.width = rect.width * dpr;
  canvas.height = cssHeight * dpr;
  ctx.scale(dpr, dpr);
  const width = rect.width;
  const height = cssHeight;
  const padL = 54;
  const padR = options.rightAxis ? 82 : 18;
  const padT = 18;
  const padB = 46;
  const allValues = series.flatMap((s) => s.points.map((p) => p.value).filter((v) => Number.isFinite(v)));
  const minVal = Math.min(...allValues, 0);
  const maxVal = Math.max(...allValues, 0);
  const span = Math.max(1, maxVal - minVal);
  const plotW = width - padL - padR;
  const plotH = height - padT - padB;
  const yFor = (value) => padT + plotH * (1 - (value - minVal) / span);
  const xFor = (x) => padL + plotW * x;
  const allDates = [...new Set(series.flatMap((s) => s.points.map((p) => p.date).filter(Boolean)))]
    .sort((a, b) => new Date(a) - new Date(b));
  const tickCount = Math.min(chartTickCount(options.range, width), allDates.length);
  const dateTicks = tickCount > 1
    ? Array.from({ length: tickCount }, (_, i) => allDates[Math.round(i * (allDates.length - 1) / (tickCount - 1))])
    : allDates;
  ctx.clearRect(0, 0, width, height);

  ctx.strokeStyle = "#263241";
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let i = 0; i <= 4; i++) {
    const y = padT + (plotH / 4) * i;
    ctx.moveTo(padL, y);
    ctx.lineTo(width - padR, y);
  }
  ctx.stroke();

  if (dateTicks.length) {
    ctx.strokeStyle = "rgba(142, 160, 180, 0.16)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    dateTicks.forEach((date) => {
      const p = series.flatMap((s) => s.points).find((point) => point.date === date);
      if (!p) return;
      const x = xFor(p.x);
      ctx.moveTo(x, padT);
      ctx.lineTo(x, padT + plotH);
    });
    ctx.stroke();
  }

  const zeroY = yFor(0);
  ctx.save();
  ctx.setLineDash([5, 5]);
  ctx.strokeStyle = "rgba(238, 245, 251, 0.72)";
  ctx.lineWidth = 1.4;
  ctx.beginPath();
  ctx.moveTo(padL, zeroY);
  ctx.lineTo(width - padR, zeroY);
  ctx.stroke();
  ctx.restore();

  ctx.font = "11px Segoe UI";
  ctx.fillStyle = "#8d9aaa";
  ctx.textAlign = "right";
  for (let i = 0; i <= 4; i++) {
    const value = maxVal - (span / 4) * i;
    const y = padT + (plotH / 4) * i + 4;
    ctx.fillText(`${f(value)}%`, padL - 8, y);
  }
  ctx.fillStyle = "#edf3f8";
  ctx.fillText("0%", padL - 8, zeroY + 4);

  if (options.rightAxis) {
    const initialMil = Number(options.rightAxis.initialMil) || 1000;
    const axisX = width - padR + 10;
    ctx.save();
    ctx.strokeStyle = "rgba(142, 160, 180, 0.38)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(width - padR, padT);
    ctx.lineTo(width - padR, padT + plotH);
    ctx.stroke();
    ctx.font = "11px Segoe UI";
    ctx.fillStyle = "#8d9aaa";
    ctx.textAlign = "left";
    for (let i = 0; i <= 4; i++) {
      const retValue = maxVal - (span / 4) * i;
      const navMil = initialMil * (1 + retValue / 100);
      const y = padT + (plotH / 4) * i + 4;
      ctx.fillText(moneyMilLabel(navMil), axisX, y);
    }
    ctx.fillStyle = "#edf3f8";
    ctx.fillText(moneyMilLabel(initialMil), axisX, zeroY + 4);
    ctx.restore();
  }

  if (dateTicks.length) {
    ctx.fillStyle = "#8d9aaa";
    ctx.textAlign = "center";
    dateTicks.forEach((date, idx) => {
      const p = series.flatMap((s) => s.points).find((point) => point.date === date);
      if (!p) return;
      const label = chartTickLabel(date, options.range);
      const x = Math.max(padL + 18, Math.min(width - padR - 18, xFor(p.x)));
      ctx.fillText(label, x, height - 13);
      ctx.strokeStyle = "#8d9aaa";
      ctx.beginPath();
      ctx.moveTo(xFor(p.x), padT + plotH);
      ctx.lineTo(xFor(p.x), padT + plotH + 4);
      ctx.stroke();
    });
  }

  series.forEach((s, idx) => {
    if (s.points.length < 2) return;
    ctx.strokeStyle = colors[idx % colors.length];
    ctx.lineWidth = idx === 0 ? 2.4 : 1.8;
    ctx.beginPath();
    s.points.forEach((p, i) => {
      const x = xFor(p.x);
      const y = yFor(p.value);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
    const last = s.points[s.points.length - 1];
    const lx = xFor(last.x);
    const ly = yFor(last.value);
    ctx.fillStyle = colors[idx % colors.length];
    ctx.beginPath();
    ctx.arc(lx, ly, 3, 0, Math.PI * 2);
    ctx.fill();
    ctx.font = "11px Segoe UI";
    ctx.textAlign = "left";
    const label = idx === 0 && Number(last.navMil) > 0 ? `${s.name} ${moneyMilLabel(last.navMil)}` : s.name;
    ctx.fillText(label, Math.min(width - padR - 82, lx + 8), Math.max(padT + 12, Math.min(height - padB - 4, ly + 4)));
  });
  installChartTooltip(canvas, series, { padL, padR, padT, plotW, plotH, xFor }, options);
}

function card(item) {
  return `
    <article class="stock-card">
      <header>
        <strong class="symbol">${item.symbol}</strong>
        ${badge(item.status)}
      </header>
      <small>${item.industry_name || "-"} | ${item.exchange || "-"}</small>
      <div class="price-stack">
        <span>Market <b>${f(item.current_price_k)}k</b></span>
        <span>TP <b>${f(item.target_price_k)}k</b></span>
        <span>SL <b>${f(item.stop_price_k)}k</b></span>
      </div>
      <div>
        <div class="num">${f(item.composite_score)}</div>
        ${scoreBar(item.composite_score)}
      </div>
      <small>Upside ${f(Number(item.upside_pct) * 100)}% | R:R ${f(item.risk_reward)}x</small>
    </article>
  `;
}

function filterRows(rows, query) {
  const q = String(query || "").toLowerCase().trim();
  if (!q) return rows;
  return rows.filter((row) => JSON.stringify(row).toLowerCase().includes(q));
}

function renderTopGrid(query = "") {
  document.getElementById("topGrid").innerHTML = filterRows(data.topAll, query).map(card).join("");
}

function rowCandidate(item) {
  return `
    <tr>
      <td class="symbol">${item.symbol}</td>
      <td>${badge(item.status)}</td>
      <td>${item.industry_name || "-"}</td>
      <td class="num">${f(item.current_price_k)}k</td>
      <td class="num">${f(item.buy_zone_low_k)}-${f(item.buy_zone_high_k)}k</td>
      <td class="num target">${f(item.target_price_k)}k</td>
      <td class="num stop">${f(item.stop_price_k)}k</td>
      <td class="num target">${f(Number(item.upside_pct) * 100)}%</td>
      <td class="num stop">-${f(Number(item.downside_pct) * 100)}%</td>
      <td class="num">${f(item.risk_reward)}x</td>
      <td>${item.sleeve || "-"}</td>
      <td class="num">${f(item.composite_score)}</td>
      <td class="num">${f(item.quality_score)}</td>
      <td class="num">${f(item.valuation_score)}</td>
      <td class="num">${f(item.catalyst_score)}</td>
      <td class="num">${f(item.technical_score)}</td>
      <td class="num">${f(item.market_cap_bil)}</td>
      <td class="num">${f(item.avg_value_20d_bil)}</td>
      <td class="num">${f(item.pe_ratio)}</td>
      <td class="num">${f(item.pb_ratio)}</td>
      <td class="num">${f(item.roe_use)}</td>
    </tr>
  `;
}

function policyHoldingSet() {
  const policy = activePolicy();
  return new Set((policy?.holdings || []).map((h) => String(h.symbol || "").toUpperCase()));
}

function isHeld(symbol) {
  return policyHoldingSet().has(String(symbol || "").toUpperCase());
}

function renderCandidates(query = "") {
  const rows = filterRows(data.candidates, query);
  const html = rows.length ? rows.map(rowCandidate).join("")
    : `<tr><td colspan="21" class="empty-state">Không có mã nào trong universe đạt BUY/ACCUMULATE.</td></tr>`;
  document.getElementById("candidateRows").innerHTML = html;
}

function renderWatch(query = "") {
  const rows = filterRows(data.watch, query);
  const html = rows.length ? rows.map((item) => `
    <tr>
      <td class="symbol">${item.symbol}</td>
      <td>${item.qualitative_overlay || "-"}</td>
      <td>${item.industry_name || "-"}</td>
      <td class="num">${f(item.current_price_k)}k</td>
      <td class="num target">${f(item.target_price_k)}k</td>
      <td class="num stop">${f(item.stop_price_k)}k</td>
      <td class="num">${f(item.risk_reward)}x</td>
      <td class="num">${f(item.composite_score)}</td>
      <td>${item.hard_gate || "-"}</td>
      <td class="num">${f(item.quality_score)}</td>
      <td class="num">${f(item.valuation_score)}</td>
      <td class="num">${f(item.catalyst_score)}</td>
      <td class="num">${f(item.technical_score)}</td>
      <td class="num">${f(item.pe_ratio)}</td>
      <td class="num">${f(item.pb_ratio)}</td>
      <td class="num">${f(item.roe_use)}</td>
    </tr>
  `).join("") : `<tr><td colspan="16" class="empty-state">Không có mã nào trong Watchlist.</td></tr>`;
  document.getElementById("watchRows").innerHTML = html;
}

function renderUniverse(query = "") {
  document.getElementById("universeRows").innerHTML = filterRows(data.all, query).map((item) => `
    <tr>
      <td class="symbol">${item.symbol}</td>
      <td>${item.exchange || "-"}</td>
      <td>${badge(item.status)}</td>
      <td>${item.hard_gate || "-"}</td>
      <td>${item.industry_name || "-"}</td>
      <td class="num">${f(item.current_price_k)}k</td>
      <td class="num target">${f(item.target_price_k)}k</td>
      <td class="num stop">${f(item.stop_price_k)}k</td>
      <td class="num">${f(item.composite_score)}</td>
      <td class="num">${f(item.market_cap_bil)}</td>
      <td class="num">${f(item.avg_value_20d_bil)}</td>
    </tr>
  `).join("");
}

function renderMemos() {
  const plan = deep.portfolioPlan || {};
  const policy = activePolicy();
  const perf = policyPerformanceSummary(activeHistory());
  const memoCash = policy
    ? (Number(policy.cashBuffer) > 0 ? Number(policy.cashBuffer) : Math.max(0, 100 - (policy.holdings || []).reduce((sum, item) => sum + n(item.suggestedWeight), 0)))
    : n(plan.cashBuffer);
  document.getElementById("portfolioPlan").innerHTML = `
    <strong>${f(policy?.totalSuggestedWeight ?? plan.totalSuggestedWeight)}% NAV</strong>
    <small>${policy ? `${policyName(policy)} | Cash ${f(memoCash)}% | CAGR ${performanceWindowLabel(activeHistory())} ${perf ? f(perf.cagr) : f(policy.historicalCagr)}%` : `Cash buffer ${f(memoCash)}%`}</small>
  `;
  const memos = deep.memos || [];
  const memoHtml = memos.length ? memos.map((memo) => `
    <article class="memo-card">
      <header>
        <div>
          <h3 class="symbol">${memo.symbol}</h3>
          <small>${memo.industry || "-"} | ${memo.sleeve || "-"}</small>
        </div>
        ${badge(memo.status)}
      </header>
      <div class="price-stack">
        <span>Giá TT <b>${f(memo.currentPrice)}k</b></span>
        <span>Mục tiêu <b>${f(memo.targetPrice)}k</b></span>
        <span>Cắt lỗ <b>${f(memo.stopPrice)}k</b></span>
      </div>
      <div class="price-stack">
        <span>Upside <b>${f(memo.upsidePct)}%</b></span>
        <span>Downside <b>-${f(memo.downsidePct)}%</b></span>
        <span>Tỷ trọng <b>${f(memo.suggestedWeight)}%</b></span>
      </div>
      <div class="memo-plan">${memo.plan}</div>
      <ul>${(memo.reasons || []).map((line) => `<li>${line}</li>`).join("")}</ul>
    </article>
  `).join("") : `<article class="panel"><div class="empty-state">Chưa có memo nào.</div></article>`;
  document.getElementById("memoGrid").innerHTML = memoHtml;
}

function normalizeWatchItem(raw = {}, source = "unknown") {
  const symbol = String(raw.symbol || "").toUpperCase().trim();
  if (!symbol) return null;
  const action = String(raw.action || raw.orderAction || "").toUpperCase().trim();
  const currentPriceK = firstPositive(raw.current_price_k, raw.currentPrice);
  let buyLow = n(raw.buy_zone_low_k, null);
  let buyHigh = n(raw.buy_zone_high_k, null);
  const maxBuyPriceK = n(raw.maxBuyPrice, null);
  const limitPriceK = n(raw.limitPrice, null);
  if (!Number.isFinite(buyLow) || !Number.isFinite(buyHigh)) {
    const low = Number.isFinite(limitPriceK) ? limitPriceK : maxBuyPriceK;
    const high = Number.isFinite(maxBuyPriceK) ? maxBuyPriceK : limitPriceK;
    if (Number.isFinite(low) && Number.isFinite(high)) {
      buyLow = Math.min(low, high);
      buyHigh = Math.max(low, high);
    }
  }
  const targetPriceK = n(raw.target_price_k ?? raw.targetPrice, null);
  const stopPriceK = n(raw.stop_price_k ?? raw.stopPrice, null);
  const support20K = n(raw.support20_k, null);
  const atr20K = n(raw.atr20_k, null);
  const rrMin = n(raw.rr_min, 2);
  let liq20dBil = n(raw.avg_value_20d_bil, null);
  if (Number.isFinite(liq20dBil) && liq20dBil <= 0) liq20dBil = null;
  const status = String(raw.status || raw.qualitative_overlay || "WATCH").toUpperCase();
  const hardGate = String(raw.hard_gate || "PASS").toUpperCase();
  const rrRaw = n(raw.risk_reward ?? raw.riskReward, null);
  const upsideRaw = n(raw.upside_pct ?? raw.upsidePct, null);
  const upsidePct = upsideRaw === null ? null : (Math.abs(upsideRaw) <= 2 ? upsideRaw * 100 : upsideRaw);
  let riskReward = rrRaw;
  if ((riskReward === null || riskReward <= 0) && targetPriceK && stopPriceK && currentPriceK && currentPriceK > stopPriceK) {
    riskReward = (targetPriceK - currentPriceK) / (currentPriceK - stopPriceK);
  }
  const isCenteredFeedZone = Number.isFinite(currentPriceK)
    && Number.isFinite(buyLow)
    && Number.isFinite(buyHigh)
    && Math.abs((buyLow / currentPriceK) - 0.98) < 0.0015
    && Math.abs((buyHigh / currentPriceK) - 1.02) < 0.0015;
  if (isCenteredFeedZone) {
    const lowBySupport = Number.isFinite(support20K) && Number.isFinite(atr20K)
      ? support20K - (0.35 * atr20K)
      : null;
    const highBySupport = Number.isFinite(support20K) && Number.isFinite(atr20K)
      ? support20K + (0.85 * atr20K)
      : null;
    const highByRR = Number.isFinite(targetPriceK) && Number.isFinite(stopPriceK)
      ? (targetPriceK + rrMin * stopPriceK) / (1 + rrMin)
      : null;
    const floorByStop = Number.isFinite(stopPriceK) ? (stopPriceK * 1.03) : null;
    const resolvedLow = [lowBySupport, floorByStop].filter((x) => Number.isFinite(x));
    const resolvedHigh = [highBySupport, highByRR].filter((x) => Number.isFinite(x));
    if (resolvedLow.length && resolvedHigh.length) {
      buyLow = Math.max(...resolvedLow);
      buyHigh = Math.min(...resolvedHigh);
      if (buyHigh < buyLow) {
        buyHigh = buyLow;
      }
    } else {
      // Reject synthetic centered zones when we cannot resolve a real zone from rule anchors.
      buyLow = null;
      buyHigh = null;
    }
  }
  return {
    symbol,
    source,
    action,
    status,
    hardGate,
    industry: raw.industry_name || raw.industry || raw.sleeve || "-",
    currentPriceK,
    buyLow,
    buyHigh,
    targetPriceK,
    stopPriceK,
    support20K,
    atr20K,
    upsidePct,
    riskReward,
    liq20dBil,
    rrMin,
  };
}

function activePolicyPlannedRows() {
  const policy = activePolicy();
  const planned = policy?.plannedOrders || deep.plannedOrders || {};
  return Array.isArray(planned.rows) ? planned.rows : [];
}

function watchlistUniverseRows() {
  const plannedRows = activePolicyPlannedRows();
  const shortlistSymbols = new Set();
  for (const row of deep.memos || []) {
    const symbol = String(row?.symbol || "").toUpperCase().trim();
    if (symbol) shortlistSymbols.add(symbol);
  }
  for (const row of plannedRows) {
    const symbol = String(row?.symbol || "").toUpperCase().trim();
    if (symbol) shortlistSymbols.add(symbol);
  }

  const merged = new Map();
  const sourceRank = {
    watch: 1,
    memo: 2,
    candidate: 3,
    live_shortlist: 4,
  };
  const put = (row, source) => {
    const item = normalizeWatchItem(row, source);
    if (!item) return;
    const prev = merged.get(item.symbol);
    const prevRank = prev ? (sourceRank[prev.source] || 0) : -1;
    const nextRank = sourceRank[source] || 0;
    if (!prev || nextRank >= prevRank) {
      merged.set(item.symbol, item);
    }
  };

  if (shortlistSymbols.size) {
    for (const symbol of shortlistSymbols) {
      const stock = stockBySymbol.get(symbol) || {};
      const memo = (deep.memos || []).find((row) => String(row.symbol || "").toUpperCase() === symbol) || {};
      const plan = plannedRows.find((row) => String(row.symbol || "").toUpperCase() === symbol) || {};
      put({ ...stock, ...memo, ...plan }, "live_shortlist");
    }
  }

  for (const row of data.candidates || []) put(row, "candidate");
  for (const row of data.watch || []) put(row, "watch");
  for (const row of deep.memos || []) {
    const enriched = stockBySymbol.get(String(row.symbol || "").toUpperCase()) || {};
    put({ ...enriched, ...row }, "memo");
  }
  return [...merged.values()];
}

function classifyWatchCandidate(item) {
  const inPortfolio = isHeld(item.symbol);
  const passHardGate = item.hardGate.includes("PASS");
  const notAvoid = !item.status.includes("AVOID");
  const buyAction = item.action.includes("MUA");
  const liquidityOk = Number.isFinite(item.liq20dBil) && item.liq20dBil >= 3;
  const rrOk = Number.isFinite(item.riskReward) && item.riskReward >= 2;
  const upsideOk = Number.isFinite(item.upsidePct) && item.upsidePct >= 12;
  const strongStatus = item.status.includes("BUY") || item.status.includes("ACCUMULATE") || buyAction;

  const reasons = [];
  if (item.action) reasons.push(`Lệnh policy: ${item.action}`);
  if (strongStatus) reasons.push("Trạng thái BUY/ACCUMULATE");
  if (liquidityOk) reasons.push(`Thanh khoản 20D ${f(item.liq20dBil)} tỷ`);
  if (rrOk) reasons.push(`R:R ${f(item.riskReward, 2)}x`);
  if (upsideOk) reasons.push(`Target upside ${f(item.upsidePct)}%`);

  const failedConditions = [];
  if (inPortfolio) failedConditions.push("đang nắm trong danh mục");
  if (!passHardGate) failedConditions.push(`hard gate ${item.hardGate || "không đạt"}`);
  if (!notAvoid) failedConditions.push("trạng thái AVOID");
  if (!strongStatus) failedConditions.push("chưa có trạng thái BUY/ACCUMULATE hoặc lệnh MUA từ policy");
  if (!Number.isFinite(item.liq20dBil)) failedConditions.push("thiếu dữ liệu thanh khoản 20D");
  else if (!liquidityOk) failedConditions.push("thanh khoản 20D < 3 tỷ");
  if (!rrOk) failedConditions.push("R:R < 2");
  if (!upsideOk) failedConditions.push("target upside < 12%");

  const canBuySoon = !inPortfolio && passHardGate && notAvoid && strongStatus && liquidityOk && rrOk && upsideOk;
  const gatePassCount = [!inPortfolio, passHardGate, notAvoid, strongStatus, liquidityOk, rrOk, upsideOk]
    .filter(Boolean)
    .length;
  const gateTotal = 7;
  if (!canBuySoon && failedConditions.length) {
    reasons.push(`Chưa đạt: ${failedConditions.join(", ")}`);
  } else if (canBuySoon) {
    reasons.push(`Đạt chuẩn mua: ${gatePassCount}/${gateTotal} điều kiện`);
  }
  return {
    ...item,
    isLiveShortlist: item.source === "live_shortlist",
    inPortfolio,
    passHardGate,
    liquidityOk,
    rrOk,
    upsideOk,
    gatePassCount,
    gateTotal,
    bucket: canBuySoon ? "BUY_SOON" : "WATCH",
    reasons: reasons.length ? reasons : ["Chờ tín hiệu rõ hơn theo rule"],
  };
}

function renderWatchlistTab() {
  const plannedMap = new Map(
    activePolicyPlannedRows().map((row) => [
      String(row.symbol || "").toUpperCase(),
      {
        action: displayAction(row.action || row.side || ""),
        status: String(row.status || ""),
      },
    ])
  );
  const allRows = watchlistUniverseRows().map(classifyWatchCandidate).sort((a, b) => {
    const byScore = (b.gatePassCount || 0) - (a.gatePassCount || 0);
    if (byScore !== 0) return byScore;
    const byUpside = (b.upsidePct || 0) - (a.upsidePct || 0);
    if (byUpside !== 0) return byUpside;
    const byRR = (b.riskReward || 0) - (a.riskReward || 0);
    if (byRR !== 0) return byRR;
    return String(a.symbol || "").localeCompare(String(b.symbol || ""));
  });
  const excludedHeld = allRows.filter((row) => row.inPortfolio);
  const rows = allRows.filter((row) => !row.inPortfolio);
  const liveScope = rows.filter((row) => row.isLiveShortlist);
  const buySoon = rows.filter((row) => row.bucket === "BUY_SOON");
  const watchMore = rows.filter((row) => row.bucket === "WATCH");

  const summaryEl = document.getElementById("watchlistSummary");
  if (summaryEl) {
    summaryEl.innerHTML = `
      <span>Shortlist live policy <b>${liveScope.length} mã</b></span>
      <span>Có thể mua sớm <b>${buySoon.length} mã</b></span>
      <span>Cần theo dõi thêm <b>${watchMore.length} mã</b></span>
      <span>Tổng watchlist <b>${rows.length} mã</b></span>
      <span>Đang nắm (đã loại khỏi mua mới) <b>${excludedHeld.length} mã</b></span>
    `;
  }

  const rulesEl = document.getElementById("watchlistRules");
  if (rulesEl) {
    rulesEl.innerHTML = [
      "Điểm lọc mã chỉ dùng tiêu chí thuộc rule live (không phải auto-buy)",
      "Sắp xếp theo Điểm lọc mã giảm dần, rồi Target Upside giảm dần",
      "Gate live hiện tại gồm 7 điều kiện: không nắm, hard gate PASS, không AVOID, có tín hiệu BUY/ACC/MUA, thanh khoản 20D >= 3 tỷ, R:R >= 2, target upside >= 12%",
      "Lệnh mua thực tế chỉ chạy khi mã nằm trong target tuần của policy hiện tại",
    ].map((rule) => `<span>${rule}</span>`).join("");
  }

  const body = document.getElementById("watchlistRows");
  if (!body) return;
  body.innerHTML = rows.length ? rows.map((row) => `
    <tr>
      <td><strong>${esc(row.symbol)}</strong></td>
      <td>${row.bucket === "BUY_SOON" ? `<span class="action-pill buy">CÓ THỂ MUA</span>` : `<span class="action-pill watch">THEO DÕI</span>`}</td>
      <td class="num">${row.bucket === "BUY_SOON"
        ? `<span class="action-pill buy">${row.gatePassCount}/${row.gateTotal}</span>`
        : `<span class="action-pill watch">${row.gatePassCount}/${row.gateTotal}</span>`}</td>
      <td class="num ${row.upsideOk ? "target" : ""}">${row.upsidePct === null ? "-" : `${f(row.upsidePct)}%`}</td>
      <td class="num ${row.rrOk ? "target" : ""}">${row.riskReward === null ? "-" : `${f(row.riskReward, 2)}x`}</td>
      <td class="num ${row.liquidityOk ? "target" : ""}">${row.liq20dBil === null ? "-" : f(row.liq20dBil)}</td>
      <td>${plannedMap.has(row.symbol) ? "Có" : "Không"}</td>
      <td>${plannedMap.has(row.symbol) ? esc(plannedMap.get(row.symbol)?.action || "-") : "-"}</td>
      <td>${row.inPortfolio ? "Không" : "Có"}</td>
      <td>${row.passHardGate ? "PASS" : "FAIL"}</td>
      <td>${row.status.includes("AVOID") ? "Không" : "Có"}</td>
      <td>${(row.status.includes("BUY") || row.status.includes("ACCUMULATE") || row.action.includes("MUA")) ? "Có" : "Không"}</td>
      <td>${esc(row.reasons.join(" · "))}</td>
    </tr>
  `).join("") : `<tr><td colspan="13" class="empty-state">Chưa có mã phù hợp cho watchlist.</td></tr>`;
}

function currentPriceK(symbol) {
  const key = String(symbol || "").toUpperCase();
  return n(stockBySymbol.get(key)?.current_price_k || memoBySymbol.get(key)?.currentPrice, null);
}

function activePolicy() {
  const policies = activePolicies();
  return policies.find((item) => item.key === strategyMode) || policies[0] || null;
}

function activePolicyMemo(symbol) {
  const key = String(symbol || "").toUpperCase();
  const policy = activePolicy();
  return (policy?.holdings || []).find((item) => String(item.symbol || "").toUpperCase() === key) || null;
}

function activeHistory() {
  return (modelHistory.policies || []).find((item) => item.key === strategyMode) || null;
}

function arrangePortfolioLayout() {
  const panel = document.querySelector("#portfolio > article.panel");
  if (!panel || panel.dataset.layoutArranged === "1") return;
  const planned = panel.querySelector(".planned-orders");
  const ordersSection = panel.querySelector(".execution-panel");
  const holdingsTitle = panel.querySelector(".compact-title");
  const holdingsSummary = document.getElementById("holdingsSummary");
  const holdingsWrap = holdingsSummary?.nextElementSibling;
  const performance = panel.querySelector(".performance-card");
  if (planned) panel.insertBefore(planned, ordersSection);
  if (holdingsTitle && ordersSection) panel.insertBefore(holdingsTitle, ordersSection);
  if (holdingsSummary && ordersSection) panel.insertBefore(holdingsSummary, ordersSection);
  if (holdingsWrap && ordersSection) panel.insertBefore(holdingsWrap, ordersSection);
  if (performance && ordersSection.nextSibling) panel.insertBefore(performance, ordersSection.nextSibling);

  const orderTitle = ordersSection?.querySelector(".panel-title h2");
  if (orderTitle) orderTitle.textContent = "Danh sách lệnh gần nhất";
  const plannedTable = panel.querySelector(".planned-table");
  const holdingsTable = panel.querySelector(".portfolio-table");
  const ordersTable = panel.querySelector(".copy-table:not(.planned-table)");
  [plannedTable, holdingsTable, ordersTable].forEach((table) => table?.classList.add("uniform-table"));
  [plannedTable, holdingsTable, ordersTable].forEach((table) => table?.closest(".table-wrap")?.classList.add("uniform-table-wrap"));
  panel.dataset.layoutArranged = "1";
}

function inputNavBil() {
  const value = Number(portfolio.navBil);
  return value > 0 ? value : 1;
}

function rawEquityCurveRows(hist = activeHistory()) {
  return (hist?.equityCurve || []).filter((row) => Number(row.navBil) > 0 && row.date);
}

function performanceRawBaseNav(hist = activeHistory()) {
  const row = rawEquityCurveRows(hist)
    .find((item) => item.date && new Date(item.date) >= new Date(`${PERFORMANCE_START_DATE}T00:00:00`));
  return Number(row?.navBil) || null;
}

function modelStartDate(hist = activeHistory()) {
  return PERFORMANCE_START_DATE;
}

function equityCurveRows(hist = activeHistory()) {
  const rows = rawEquityCurveRows(hist);
  if (!rows.length) return [];
  const initialBil = modelInitialNavMil() / 1000;
  const first = rows[0];
  if (Number(first.navBil) > initialBil * 1.001) {
    return [{
      ...first,
      date: "2021-01-01",
      navBil: initialBil,
      initialCapital: true,
    }].concat(rows);
  }
  return rows;
}

function latestHoldingPriceDate(holdings = activePolicy()?.holdings || []) {
  return lastItem(holdings.map((h) => h.priceAsOf).filter(Boolean).sort()) || null;
}

function lastEquitySnapshot(hist = activeHistory()) {
  const rows = rawEquityCurveRows(hist)
    .filter((row) => row.date && new Date(row.date) >= new Date(`${PERFORMANCE_START_DATE}T00:00:00`));
  if (!rows.length) {
    return {
      navMil: modelInitialNavMil(),
      navBil: modelInitialNavMil() / 1000,
      date: PERFORMANCE_START_DATE,
    };
  }
  const base = rows[0];
  const last = lastItem(rows);
  const baseNav = Number(base.navBil) || 1;
  const navMil = modelInitialNavMil() * Number(last.navBil) / baseNav;
  return {
    navMil,
    navBil: navMil / 1000,
    date: last?.date || PERFORMANCE_START_DATE,
  };
}

function cutoffDateForRange(lastDate, range) {
  const d = new Date(lastDate);
  if (Number.isNaN(d.getTime()) || range === "all") return new Date(`${PERFORMANCE_START_DATE}T00:00:00`);
  if (range === "ytd") return new Date(d.getFullYear(), 0, 1);
  const months = range === "3m" ? 3 : range === "6m" ? 6 : range === "1y" ? 12 : 0;
  if (!months) return null;
  const c = new Date(d);
  c.setMonth(c.getMonth() - months);
  return c;
}

function performanceBaseRows(hist) {
  const rows = curveWithCurrentNav(hist)
    .filter((row) => row.date && new Date(row.date) >= new Date(`${PERFORMANCE_START_DATE}T00:00:00`));
  if (!rows.length) return [];
  const baseNav = Number(rows[0].navBil);
  const baseVni = Number(rows.find((row) => Number(row.vniClose) > 0)?.vniClose);
  if (baseNav <= 0) return rows;
  const initialMil = modelInitialNavMil();
  const mapped = rows.map((row) => ({
    ...row,
    modelNavMil: initialMil * Number(row.navBil) / baseNav,
    vniNavMil: baseVni && Number(row.vniClose) > 0 ? initialMil * Number(row.vniClose) / baseVni : null,
  }));
  const first = mapped[0];
  if (first.date !== PERFORMANCE_START_DATE) {
    mapped.unshift({
      ...first,
      date: PERFORMANCE_START_DATE,
      modelNavMil: initialMil,
      vniNavMil: first.vniNavMil ? initialMil : null,
      performanceBase: true,
    });
  }
  return mapped;
}

function filteredCurve(hist) {
  const rows = performanceBaseRows(hist);
  if (!rows.length) return [];
  const last = rows[rows.length - 1].date;
  const cutoff = cutoffDateForRange(last, performanceRange);
  return cutoff ? rows.filter((row) => new Date(row.date) >= cutoff) : rows;
}

function currentAssetSnapshot() {
  const initialNavMil = modelInitialNavMil();
  const hist = activeHistory();
  const ledger = lastEquitySnapshot(hist);
  const holdings = activePolicy()?.holdings || [];
  if (holdings.length) {
    const holdingsStats = modelHoldingsStats(holdings, ledger.navBil);
    const currentAssetMil = holdingsStats.totalNavMil;
    const gainMil = currentAssetMil - initialNavMil;
    const gainPct = initialNavMil > 0 ? gainMil / initialNavMil * 100 : 0;
    return {
      initialNavMil,
      currentAssetMil,
      gainMil,
      gainPct,
      source: "live_mtm",
      lastDate: latestHoldingPriceDate(holdings) || ledger.date,
      ledgerNavMil: ledger.navMil,
      ledgerDate: ledger.date,
      holdingsStats,
    };
  }
  const currentAssetMil = ledger.navMil;
  const gainMil = currentAssetMil - initialNavMil;
  const gainPct = initialNavMil > 0 ? gainMil / initialNavMil * 100 : 0;
  return { initialNavMil, currentAssetMil, gainMil, gainPct, source: "equity", lastDate: ledger.date, ledgerNavMil: ledger.navMil };
}

function curveWithCurrentNav(hist) {
  const rows = equityCurveRows(hist);
  if (!rows.length) return [];
  const asset = currentAssetSnapshot();
  if (!asset?.currentAssetMil) return rows;
  const rowLast = lastItem(rows);
  const liveDate = asset.lastDate || rowLast?.date;
  const baseRawNav = performanceRawBaseNav(hist);
  const liveNavBil = baseRawNav
    ? baseRawNav * (asset.currentAssetMil / modelInitialNavMil())
    : asset.currentAssetMil / 1000;
  const last = lastItem(rows);
  const liveTime = new Date(liveDate).getTime();
  const lastTime = new Date(last.date).getTime();
  if (Number.isFinite(liveTime) && liveTime > lastTime) {
    return rows.concat([{ ...last, date: liveDate, navBil: liveNavBil, liveMtm: true }]);
  }
  if (liveDate === last.date && Math.abs(liveNavBil - Number(last.navBil)) > 0.000001) {
    return rows.slice(0, -1).concat([{ ...last, navBil: liveNavBil, liveMtm: true }]);
  }
  return rows;
}

function normalizeCurve(rows) {
  if (!rows.length) return { model: [], vni: [] };
  const firstNav = Number(rows[0].navBil);
  const firstVni = Number(rows.find((row) => row.vniClose)?.vniClose);
  const start = new Date(rows[0].date).getTime();
  const end = new Date(rows[rows.length - 1].date).getTime();
  const span = Math.max(1, end - start);
  const xValue = (date) => (new Date(date).getTime() - start) / span;
  const model = rows.map((row) => ({
    x: xValue(row.date),
    date: row.date,
    navMil: Number(row.modelNavMil || Number(row.navBil) * 1000),
    value: (Number(row.navBil) / firstNav - 1) * 100,
  }));
  const vni = firstVni ? rows
    .filter((row) => Number(row.vniClose) > 0)
    .map((row) => ({
      x: xValue(row.date),
      date: row.date,
      navMil: Number(row.vniNavMil || 0),
      vniClose: Number(row.vniClose),
      value: (Number(row.vniClose) / firstVni - 1) * 100,
    })) : [];
  return { model, vni };
}

function allPerformanceRows(hist = activeHistory()) {
  return performanceBaseRows(hist);
}

function policyPerformanceSummary(hist = activeHistory()) {
  const rows = allPerformanceRows(hist);
  if (rows.length < 2) return null;
  const normalized = normalizeCurve(rows);
  const model = normalized.model;
  const vni = normalized.vni;
  const first = rows[0];
  const last = lastItem(rows);
  const startTime = new Date(first.date).getTime();
  const endTime = new Date(last.date).getTime();
  const years = Math.max(1 / 365, (endTime - startTime) / (365.25 * 24 * 60 * 60 * 1000));
  const firstNav = Number(first.navBil) || 1;
  const lastNav = Number(last.navBil) || firstNav;
  const cagr = (Math.pow(lastNav / firstNav, 1 / years) - 1) * 100;
  let peak = firstNav;
  let maxDrawdown = 0;
  rows.forEach((row) => {
    const nav = Number(row.navBil) || 0;
    peak = Math.max(peak, nav);
    if (peak > 0) maxDrawdown = Math.min(maxDrawdown, (nav / peak - 1) * 100);
  });
  const modelRet = lastItem(model)?.value ?? 0;
  const vniRet = vni.length ? lastItem(vni)?.value : null;
  return {
    startDate: PERFORMANCE_START_DATE,
    endDate: last.date,
    navMil: lastItem(model)?.navMil || modelInitialNavMil() * lastNav / firstNav,
    modelRet,
    vniRet,
    spread: vniRet === null ? null : modelRet - vniRet,
    cagr,
    maxDrawdown,
  };
}

function renderPerformanceChart() {
  document.querySelectorAll("#performanceRange .range").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.range === performanceRange);
  });
  const hist = activeHistory();
  const rows = filteredCurve(hist);
  const statsEl = document.getElementById("performanceStats");
  const canvas = document.getElementById("performanceChart");
  if (canvas) {
    canvas.setAttribute("height", String(performanceChartHeight()));
  }
  if (!hist || rows.length < 2) {
    if (statsEl) statsEl.innerHTML = `<span>Chưa có đủ NAV history cho policy này.</span>`;
    if (canvas) canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
    return;
  }
  const { model, vni } = normalizeCurve(rows);
  const modelRet = model[model.length - 1]?.value ?? 0;
  const vniRet = vni.length ? vni[vni.length - 1].value : null;
  const spread = vniRet === null ? null : modelRet - vniRet;
  const peak = rows.reduce((m, row) => Math.max(m, Number(row.navBil)), Number(rows[0].navBil));
  const lastNav = Number(rows[rows.length - 1].navBil);
  const drawdown = (lastNav / peak - 1) * 100;
  const currentNavMil = model[model.length - 1]?.navMil || lastNav * 1000;
  const currentNavDate = rows[rows.length - 1].date;
  if (statsEl) {
    statsEl.innerHTML = `
      <span>NAV hiện tại <b>${moneyMilLabel(currentNavMil)}</b></span>
      <span>Model <b class="${modelRet >= 0 ? "target" : "stop"}">${f(modelRet)}%</b></span>
      <span>VN-Index <b>${vniRet === null ? "-" : `${f(vniRet)}%`}</b></span>
      <span>Spread <b class="${spread === null || spread >= 0 ? "target" : "stop"}">${spread === null ? "-" : `${f(spread)}%`}</b></span>
      <span>Drawdown hiện tại <b class="${drawdown >= -5 ? "target" : "stop"}">${f(drawdown)}%</b></span>
      <span>Giai đoạn <b>${rows[0].date} → ${currentNavDate}</b></span>
    `;
  }
  const chartInitialMil = model[0]?.navMil || Number(rows[0].navBil) * 1000 || modelInitialNavMil();
  drawLineChart("performanceChart", [
    { name: "Model", points: model },
    { name: "VN-Index", points: vni },
  ], ["#29d391", "#6aa7ff"], { range: performanceRange, rightAxis: { initialMil: chartInitialMil } });
}

function modelTargetWeight(symbol) {
  const policyMemo = activePolicyMemo(symbol);
  if (policyMemo) return n(policyMemo.suggestedWeight);
  const memo = memoBySymbol.get(String(symbol || "").toUpperCase());
  if (!memo || !["BUY", "ACCUMULATE"].includes(memo.status)) return 0;
  return n(memo.suggestedWeight);
}

function modelStatus(symbol) {
  const key = String(symbol || "").toUpperCase();
  const policyMemo = activePolicyMemo(key);
  if (policyMemo) return policyMemo.status || "POLICY";
  return memoBySymbol.get(key)?.status || stockBySymbol.get(key)?.status || "NO_SIGNAL";
}

function portfolioRows() {
  return (portfolio.holdings || [])
    .map((row) => ({
      symbol: String(row.symbol || "").toUpperCase().trim(),
      quantity: n(row.quantity),
      avgPriceK: row.avgPriceK === null || row.avgPriceK === undefined || row.avgPriceK === "" ? null : n(row.avgPriceK),
      note: row.note || ""
    }))
    .filter((row) => row.symbol && row.quantity > 0);
}

function portfolioTotals(rows) {
  const marketValueBil = rows.reduce((sum, row) => {
    const price = currentPriceK(row.symbol);
    return price ? sum + row.quantity * price / 1000000 : sum;
  }, 0);
  const inputNav = n(portfolio.navBil, 0);
  const inputCash = n(portfolio.cashBil, 0);
  const navBil = inputNav > 0 ? inputNav : marketValueBil + inputCash;
  const cashBil = portfolio.cashBil === null || portfolio.cashBil === undefined || portfolio.cashBil === ""
    ? Math.max(0, navBil - marketValueBil)
    : inputCash;
  return { marketValueBil, navBil, cashBil };
}

function holdingDecision(row, totals) {
  const symbol = row.symbol;
  const memo = memoBySymbol.get(symbol) || {};
  const price = currentPriceK(symbol);
  const targetPrice = n(memo.targetPrice || stockBySymbol.get(symbol)?.target_price_k, null);
  const stopPrice = n(memo.stopPrice || stockBySymbol.get(symbol)?.stop_price_k, null);
  const status = modelStatus(symbol);
  const marketValueBil = price ? row.quantity * price / 1000000 : 0;
  const costBil = row.avgPriceK ? row.quantity * row.avgPriceK / 1000000 : null;
  const pnlPct = row.avgPriceK && price ? (price / row.avgPriceK - 1) * 100 : null;
  const currentWeight = totals.navBil > 0 ? marketValueBil / totals.navBil * 100 : 0;
  const targetWeight = modelTargetWeight(symbol);
  const deltaWeight = targetWeight - currentWeight;
  const deltaBil = totals.navBil > 0 ? totals.navBil * deltaWeight / 100 : 0;
  let action = "CHỜ DỮ LIỆU";
  let actionClass = "watch";
  let reason = "Chưa có đủ giá thị trường hoặc dữ liệu model.";

  if (price && stopPrice && price <= stopPrice) {
    action = "CẮT LỖ";
    actionClass = "sell";
    reason = `Giá ${f(price)}k đã chạm hoặc thấp hơn stop ${f(stopPrice)}k.`;
  } else if (price && targetPrice && price >= targetPrice) {
    action = status === "BUY" || status === "ACCUMULATE" ? "CHỐT 30-50%" : "CHỐT/THOÁT";
    actionClass = "trim";
    reason = `Giá ${f(price)}k đã đạt vùng mục tiêu ${f(targetPrice)}k.`;
  } else if (status === "BUY" || status === "ACCUMULATE") {
    if (deltaWeight > 1) {
      action = "MUA THÊM";
      actionClass = "buy";
      reason = `Tỷ trọng hiện tại ${f(currentWeight)}%, thấp hơn target ${f(targetWeight)}%.`;
    } else if (deltaWeight < -1) {
      action = "GIẢM VỀ TARGET";
      actionClass = "trim";
      reason = `Tỷ trọng hiện tại ${f(currentWeight)}%, cao hơn target ${f(targetWeight)}%.`;
    } else {
      action = "GIỮ";
      actionClass = "hold";
      reason = `Tỷ trọng hiện tại gần target ${f(targetWeight)}%.`;
    }
  } else if (status === "WATCH") {
    action = "NGỪNG MUA / GIẢM RỦI RO";
    actionClass = "trim";
    reason = "Model chuyển sang WATCH; chưa giải ngân thêm trước khi xử lý memo.";
  } else if (status === "AVOID" || status === "NO_SIGNAL") {
    action = "BÁN/THOÁT";
    actionClass = "sell";
    reason = "Mã không còn trong nhóm mua hoặc watch có thể bảo vệ vị thế.";
  }

  return {
    ...row,
    price,
    targetPrice,
    stopPrice,
    status,
    marketValueBil,
    costBil,
    pnlPct,
    currentWeight,
    targetWeight,
    deltaWeight,
    deltaBil,
    action,
    actionClass,
    reason
  };
}

function openBuyIdeas(decisions, totals) {
  const held = new Set(decisions.map((row) => row.symbol));
  const policy = activePolicy();
  const source = policy?.holdings || (deep.memos || []).filter((memo) => ["BUY", "ACCUMULATE"].includes(memo.status));
  return source
    .filter((memo) => !held.has(memo.symbol))
    .map((memo) => {
      const targetWeight = n(memo.suggestedWeight);
      const policyReason = policy
        ? `${policyName(policy)}: theo gate live hiện tại; target ${f(targetWeight)}% NAV, stop theo memo/policy.`
        : memo.plan;
      return {
        symbol: memo.symbol,
        status: memo.status,
        targetWeight,
        amountBil: totals.navBil > 0 ? totals.navBil * targetWeight / 100 : null,
        price: n(memo.currentPrice, null),
        targetPrice: n(memo.targetPrice, null),
        stopPrice: n(memo.stopPrice, null),
        reason: policyReason || "Mã đạt điều kiện mua/tích lũy nhưng chưa có trong danh mục."
      };
    });
}

function latestTradeSignals(limit = 12) {
  const hist = activeHistory();
  const trades = (hist?.trades || []).filter((row) => row.date && row.symbol);
  if (!trades.length) return [];
  const sorted = trades.slice().sort((a, b) => String(a.date).localeCompare(String(b.date)));
  const latestDate = sorted[sorted.length - 1].date;
  return sorted
    .filter((row) => row.date === latestDate)
    .slice(-limit)
    .map((row) => ({ ...row, signalDate: latestDate, source: "ledger" }));
}

function historyNavBilAt(date) {
  const hist = activeHistory();
  const curve = hist?.equityCurve || [];
  if (!curve.length) return null;
  const target = String(date || "");
  let selected = null;
  for (const row of curve) {
    if (!row.date || String(row.date) > target) break;
    selected = row;
  }
  return selected && Number(selected.navBil) > 0 ? Number(selected.navBil) : null;
}

function rebalanceTradeSignals() {
  return latestTradeSignals().map((row) => {
    const sym = String(row.symbol || "").toUpperCase();
    const memo = activePolicyMemo(sym) || memoBySymbol.get(sym) || {};
    const modelNavBil = historyNavBilAt(row.date) || lastItem(activeHistory()?.equityCurve || [])?.navBil || 1;
    const navBil = inputNavBil();
    const scale = modelNavBil > 0 ? navBil / modelNavBil : navBil;
    const rawShares = Number(row.shares) || 0;
    const scaledShares = roundLot(rawShares * scale);
    const theoreticalShares = rawShares * scale;
    const label = displayAction(row.actionLabel || row.side);
    const sell = isSellAction(label);
    const executionPrice = Number(row.executionPriceK || row.priceK) || 0;
    const marketPrice = Number(memo.currentPrice || row.marketPriceK || executionPrice) || 0;
    const entryPrice = Number(row.entryPriceK || executionPrice) || 0;
    const grossMil = scaledShares && executionPrice ? scaledShares * executionPrice / 1000 : 0;
    const feeMil = Number(row.feesBil) ? Number(row.feesBil) * 1000 * scale : 0;
    const pnlPct = sell
      ? (row.returnPct === null || row.returnPct === undefined
        ? (entryPrice && executionPrice ? (executionPrice / entryPrice - 1) * 100 : null)
        : Number(row.returnPct))
      : (entryPrice && marketPrice ? (marketPrice / entryPrice - 1) * 100 : null);
    const pnlMil = sell
      ? (entryPrice && executionPrice && scaledShares ? (executionPrice - entryPrice) * scaledShares / 1000 - feeMil : null)
      : (entryPrice && marketPrice && scaledShares ? (marketPrice - entryPrice) * scaledShares / 1000 : null);
    const smallLotNote = theoreticalShares > 0 && scaledShares < 100
      ? "Dưới 100cp, không đặt lệnh"
      : "";
    return {
      ...row,
      side: row.actionLabel || row.side,
      signalDate: row.date,
      entryPriceK: row.entryPriceK || executionPrice,
      executionPriceK: executionPrice || row.priceK,
      marketPriceK: marketPrice || executionPrice,
      priceK: executionPrice || row.priceK,
      priceAsOf: memo.priceAsOf,
      targetPrice: memo.targetPrice,
      stopPrice: memo.stopPrice,
      suggestedWeight: grossMil && navBil ? grossMil / (navBil * 1000) * 100 : null,
      orderValueMil: grossMil,
      orderShares: scaledShares,
      currentPnlMil: scaledShares ? pnlMil : 0,
      currentPnlPct: pnlPct,
      note: orderNote(label, executionPrice || marketPrice, smallLotNote ? ` · ${smallLotNote}` : ""),
      source: "ledger",
    };
  });
}

function currentPositionSignals() {
  const policy = activePolicy();
  const navBil = inputNavBil();
  const scaledShares = (shares) => roundLot((Number(shares) || 0) * navBil);
  return (policy?.holdings || []).map((h) => ({
    date: h.signalDate || h.entryDate || activeHistory()?.lastTradeDate || data.summary?.as_of,
    signalDate: h.signalDate || h.entryDate || activeHistory()?.lastTradeDate || data.summary?.as_of,
    symbol: h.symbol,
    side: "TARGET",
    entryPriceK: h.entryPrice,
    executionPriceK: null,
    marketPriceK: h.currentPrice,
    priceK: h.currentPrice,
    priceAsOf: h.priceAsOf,
    fillMode: h.fillMode,
    entryDate: h.entryDate,
    sellableFrom: h.sellableFrom,
    isSellableNow: h.isSellableNow,
    targetPrice: h.targetPrice,
    stopPrice: h.stopPrice,
    suggestedWeight: h.suggestedWeight,
    orderValueMil: (Number(h.modelValueMil) || 0) * navBil,
    orderShares: scaledShares(h.copyShares || h.modelShares),
    currentPnlMil: h.currentPnlMil === null || h.currentPnlMil === undefined ? h.currentPnlMil : Number(h.currentPnlMil) * navBil,
    currentPnlPct: h.currentPnlPct,
    note: `Mục tiêu hiện tại${h.sellableFrom ? ` · T+ từ ${h.sellableFrom}` : ""}`,
    source: "position",
  }));
}

function activePlannedOrders() {
  return activePolicy()?.plannedOrders || deep.plannedOrders || { rows: [] };
}

function isFridayClosePlan(plan = activePlannedOrders()) {
  const asOf = plan?.asOf;
  if (!asOf) return false;
  const date = new Date(`${asOf}T12:00:00`);
  return !Number.isNaN(date.getTime()) && date.getDay() === 5;
}

function shouldShowPlannedBlock(plan = activePlannedOrders()) {
  return Boolean(
    plan
    && plan.stage === "pre_open"
    && isFridayClosePlan(plan)
    && Array.isArray(plan.rows)
    && plan.rows.length
  );
}

function plannedTradeSignals() {
  const plan = activePlannedOrders();
  const navBil = inputNavBil();
  return (plan.rows || []).map((row) => {
    const action = row.action || "GIỮ";
    const tradable = isBuyAction(action) || isSellAction(action);
    const baseShares = tradable
      ? firstPositive(row.orderShares, row.targetCopyShares, row.currentCopyShares)
      : firstPositive(row.currentCopyShares, row.targetCopyShares, row.orderShares);
    const currentShares = row.currentCopyShares === undefined
      ? 0
      : roundLot((Number(row.currentCopyShares) || 0) * navBil);
    const targetShares = row.targetCopyShares === undefined
      ? currentShares
      : roundLot((Number(row.targetCopyShares) || 0) * navBil);
    const scaledShares = tradable
      ? roundLot(baseShares * navBil)
      : (currentShares || roundLot(baseShares * navBil));
    const marketPriceK = Number(row.currentPrice || row.referenceClose || 0);
    const actualExecutionPriceK = Number(row.executionPrice || row.executionPriceK || 0);
    const executionPriceK = isBuyAction(action)
      ? Number(actualExecutionPriceK || row.maxBuyPrice || row.limitPrice || marketPriceK || 0)
      : Number(actualExecutionPriceK || row.currentPrice || row.referenceClose || 0);
    const entryPriceK = Number(row.entryPrice || row.entryPriceK || 0);
    const priceForValue = tradable ? executionPriceK : marketPriceK;
    const valueMil = scaledShares && priceForValue
      ? scaledShares * priceForValue / 1000
      : 0;
    const pnlBasePrice = isSellAction(action) ? executionPriceK : marketPriceK;
    const pnlPct = entryPriceK && pnlBasePrice && scaledShares
      ? (pnlBasePrice / entryPriceK - 1) * 100
      : null;
    const pnlMil = pnlPct === null
      ? null
      : (pnlBasePrice - entryPriceK) * scaledShares / 1000;
    const displayWeight = tradable
      ? (navBil > 0 && valueMil ? valueMil / (navBil * 1000) * 100 : null)
      : Number(row.currentWeight ?? row.targetWeight ?? 0);
    return {
      ...row,
      orderShares: scaledShares,
      orderValueMil: valueMil,
      marketPriceK,
      executionPriceK,
      entryPriceK,
      currentPnlMil: pnlMil,
      currentPnlPct: pnlPct,
      suggestedWeight: displayWeight,
      currentCopyShares: currentShares,
      targetCopyShares: targetShares,
      isOrder: tradable,
      priceK: executionPriceK || marketPriceK,
      signalDate: row.executionDate || row.planDate || plan.planDate,
    };
  });
}

function evaluatedPlannedTradeSignals() {
  const plan = activePlannedOrders();
  if (!plan || shouldShowPlannedBlock(plan)) return [];
  return plannedTradeSignals()
    .filter((row) => row.isOrder || ["ĐÃ KHỚP", "ĐÃ BÁN", "CHỜ GIÁ", "CHỜ DỮ LIỆU", "BỎ QUA"].some((status) => String(row.status || "").toUpperCase().includes(status)))
    .map((row) => {
      const skipped = String(row.status || "").toUpperCase().includes("BỎ");
      const side = skipped ? "BỎ QUA" : (row.action || row.side || "THEO DÕI");
      const statusPrefix = row.status ? `${row.status} · ` : "";
      return {
        ...row,
        date: row.executionDate || row.planDate || plan.planDate,
        signalDate: row.executionDate || row.planDate || plan.planDate,
        side,
        actionLabel: side,
        source: "planned_evaluated",
        note: `${statusPrefix}${row.note || ""}`.trim(),
      };
    });
}

function renderPlannedTrades() {
  const plan = activePlannedOrders();
  const rows = plannedTradeSignals();
  const watchGateMap = new Map(
    watchlistUniverseRows()
      .map(classifyWatchCandidate)
      .map((row) => [row.symbol, row])
  );
  const summaryEl = document.getElementById("plannedTradeSummary");
  const body = document.getElementById("plannedTradeRows");
  const wrapper = document.querySelector(".planned-orders");
  if (!shouldShowPlannedBlock(plan)) {
    if (wrapper) wrapper.hidden = true;
    if (summaryEl) summaryEl.innerHTML = "";
    if (body) body.innerHTML = "";
    return;
  }
  if (wrapper) wrapper.hidden = false;
  if (summaryEl) {
    const stageText = {
      pre_open: "Chuẩn bị trước phiên",
      live_window: "Đang trong cửa sổ T2-T4",
      closed_window: "Đã qua cửa sổ mua",
    }[plan.stage] || "Theo dõi";
    const buyRows = rows.filter((row) => isBuyAction(row.action));
    const buyGatePass = buyRows.filter((row) => {
      const gate = watchGateMap.get(String(row.symbol || "").toUpperCase());
      return gate && gate.gatePassCount >= gate.gateTotal;
    }).length;
    summaryEl.innerHTML = `
      <span>${esc(stageText)}</span>
      <span>Kế hoạch ${esc(plan.planDate || "-")}</span>
      <span>Mua đạt đủ điểm ${buyGatePass}/${buyRows.length}</span>
      <span>${esc(plan.summary || "Chưa có kế hoạch kỳ tới.")}</span>
    `;
  }
  if (!body) return;
  body.innerHTML = rows.length ? rows.map((row) => {
    const status = row.status || "-";
    const action = row.action || "GIỮ";
    const watchGate = watchGateMap.get(String(row.symbol || "").toUpperCase()) || null;
    const buyGateOk = !isBuyAction(action)
      || (watchGate && watchGate.gatePassCount >= watchGate.gateTotal);
    const actionDisplay = isBuyAction(action) && !buyGateOk ? "CHỜ ĐỦ ĐIỂM" : action;
    const target = row.targetPrice;
    const stop = row.stopPrice;
    const weight = row.suggestedWeight;
    const shares = Number(row.orderShares) || 0;
    const valueMil = Number(row.orderValueMil) || 0;
    const pnlMil = row.currentPnlMil === null || row.currentPnlMil === undefined ? null : Number(row.currentPnlMil);
    const pnlPct = row.currentPnlPct === null || row.currentPnlPct === undefined ? null : Number(row.currentPnlPct);
    const marketPrice = row.marketPriceK || row.currentPrice;
    const executionPrice = row.executionPriceK;
    const entryPrice = row.entryPriceK || row.entryPrice;
    const positionHint = row.currentCopyShares !== undefined && row.targetCopyShares !== undefined
      ? `Đang có ${f(row.currentCopyShares, 0)} · Mục tiêu ${f(row.targetCopyShares, 0)}. `
      : "";
    const rawNote = row.gapPct === null || row.gapPct === undefined
      ? `${positionHint}${row.note || ""}`
      : `${positionHint}${row.note || ""} Gap hiện tại ${f(row.gapPct)}%.`;
    const gateNote = (isBuyAction(action) && watchGate)
      ? `Điểm mua ${watchGate.gatePassCount}/${watchGate.gateTotal}. `
      : "";
    const missingGateNote = isBuyAction(action) && !buyGateOk
      ? "Chưa đủ điểm theo watchlist gate, tạm không kích hoạt lệnh mua. "
      : "";
    const note = `${status} · ${gateNote}${missingGateNote}${rawNote}`;
    return `
      <tr>
        <td>${esc(row.planDate || plan.planDate || "-")}</td>
        <td><strong>${esc(row.symbol || "-")}</strong></td>
        <td><span class="action-pill ${actionClass(actionDisplay)}">${esc(displayAction(actionDisplay))}</span></td>
        <td class="num">${marketPrice ? `${f(marketPrice)}k` : "-"}</td>
        <td class="num">${executionPrice ? `${f(executionPrice)}k` : "-"}</td>
        <td class="num">${entryPrice ? `${f(entryPrice)}k` : "-"}</td>
        <td class="num">${shares ? f(shares, 0) : "-"}</td>
        <td class="num">${valueMil ? f(valueMil, 1) : "-"}</td>
        <td class="num">${weight ? `${f(weight)}%` : "-"}</td>
        <td class="num ${pnlMil === null || pnlMil >= 0 ? "target" : "stop"}">${pnlMil === null ? "-" : f(pnlMil, 1)}</td>
        <td class="num ${pnlPct === null || pnlPct >= 0 ? "target" : "stop"}">${pnlPct === null ? "-" : `${f(pnlPct)}%`}</td>
        <td class="num target">${target ? `${f(target)}k` : "-"}</td>
        <td class="num stop">${stop ? `${f(stop)}k` : "-"}</td>
        <td>${esc(note || "")}</td>
      </tr>
    `;
  }).join("") : `<tr><td colspan="14" class="empty-state">Chưa có kế hoạch lệnh thứ 2.</td></tr>`;
}

function tradeSignalKey(row) {
  return [strategyMode, row.date || row.signalDate, row.symbol, row.side, row.priceK].join("|");
}

function unseenTradeSignals(signals) {
  const seen = localStorage.getItem(`hose_hnx_seen_trade_${strategyMode}`) || "";
  return signals.filter((row) => tradeSignalKey(row) > seen);
}

function markTradeSignalsSeen() {
  const signals = evaluatedPlannedTradeSignals().length ? evaluatedPlannedTradeSignals() : latestTradeSignals();
  if (!signals.length) return;
  const latest = lastItem(signals.map(tradeSignalKey).sort());
  localStorage.setItem(`hose_hnx_seen_trade_${strategyMode}`, latest);
  renderTradeAlerts();
}

function maybeNotifyNewTrades(signals) {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  const unseen = unseenTradeSignals(signals);
  if (!unseen.length) return;
  const key = lastItem(unseen.map(tradeSignalKey).sort());
  const notifiedKey = localStorage.getItem(`hose_hnx_notified_trade_${strategyMode}`);
  if (key === notifiedKey) return;
  localStorage.setItem(`hose_hnx_notified_trade_${strategyMode}`, key);
  const buys = unseen.filter((row) => isBuyAction(row.side)).length;
  new Notification("AI Trading Dashboard", {
    body: `${unseen.length} lệnh mới trên ${policyName(activePolicy())}${buys ? `, gồm ${buys} lệnh mua` : ""}.`,
  });
}

function renderTradeAlerts() {
  renderPlannedTrades();
  const evaluatedPlan = evaluatedPlannedTradeSignals();
  const latest = evaluatedPlan.length ? evaluatedPlan : latestTradeSignals();
  const signals = evaluatedPlan.length ? evaluatedPlan : (latest.length ? rebalanceTradeSignals() : currentPositionSignals());
  const unseen = unseenTradeSignals(latest);
  const summaryEl = document.getElementById("tradeAlertSummary");
  const body = document.getElementById("copyTradeRows");
  if (summaryEl) {
    const hist = activeHistory();
    const navBil = inputNavBil();
    const label = evaluatedPlan.length
      ? `Kế hoạch ${activePlannedOrders().planDate || ""}: ${evaluatedPlan.length} dòng đã cập nhật`
      : (latest.length ? `Lệnh mới nhất: ${latest.length} dòng` : "Danh mục mục tiêu");
    summaryEl.innerHTML = `
      <span class="${unseen.length ? "alert-hot" : ""}">${esc(label)}</span>
      <span>NAV ${f(navBil, 2)} tỷ · ${latest.length ? "Lệnh đã scale theo NAV nhập" : "Theo tỷ trọng policy hiện tại"}</span>
    `;
  }
  if (body) {
    body.innerHTML = signals.length ? signals.map((row) => {
      const sym = String(row.symbol || "").toUpperCase();
      const memo = activePolicyMemo(sym) || memoBySymbol.get(sym) || {};
      const side = displayAction(row.actionLabel || row.side || "MUA");
      const cls = actionClass(row.actionLabel || row.side || side);
      const target = row.targetPrice || memo.targetPrice;
      const stop = row.stopPrice || memo.stopPrice;
      const weight = row.source === "ledger"
        ? row.suggestedWeight
        : (row.suggestedWeight || row.targetWeight || memo.suggestedWeight);
      const isSell = isSellAction(row.actionLabel || row.side || side);
      const entryPrice = row.entryPriceK || memo.entryPrice;
      const executionPrice = row.executionPriceK || row.priceK;
      const hasCurrentPnlPct = Object.prototype.hasOwnProperty.call(row, "currentPnlPct");
      const hasCurrentPnlMil = Object.prototype.hasOwnProperty.call(row, "currentPnlMil");
      const pnlPctRaw = hasCurrentPnlPct
        ? (row.currentPnlPct === null || row.currentPnlPct === undefined ? null : Number(row.currentPnlPct))
        : (row.returnPct === null || row.returnPct === undefined ? null : Number(row.returnPct));
      const pnlMilRaw = hasCurrentPnlMil
        ? (row.currentPnlMil === null || row.currentPnlMil === undefined ? null : Number(row.currentPnlMil))
        : (row.pnlBil === null || row.pnlBil === undefined ? null : Number(row.pnlBil) * 1000);
      const pnlPct = isSell ? pnlPctRaw : null;
      const pnlMil = isSell ? pnlMilRaw : null;
      const orderShares = roundLot(displayTradeShares(row));
      const orderValueMil = displayTradeValueMil(row, orderShares, executionPrice || row.priceK);
      const note = row.note || `Theo tỷ trọng ${f(weight)}%`;
      const orderSharesCell = orderShares ? f(orderShares, 0) : "-";
      const orderValueCell = orderValueMil ? f(orderValueMil, 1) : "-";
      return `
        <tr>
          <td>${esc(row.signalDate || row.date || "-")}</td>
          <td><strong>${esc(sym)}</strong></td>
          <td><span class="action-pill ${cls}">${esc(side)}</span></td>
          <td class="num">${executionPrice ? `${f(executionPrice)}k` : "-"}</td>
          <td class="num">${entryPrice ? `${f(entryPrice)}k` : "-"}</td>
          <td class="num">${orderSharesCell}</td>
          <td class="num">${orderValueCell}</td>
          <td class="num">${weight ? `${f(weight)}%` : "-"}</td>
          <td class="num ${pnlMil === null || pnlMil >= 0 ? "target" : "stop"}">${pnlMil === null ? "-" : f(pnlMil, 1)}</td>
          <td class="num ${pnlPct === null || pnlPct >= 0 ? "target" : "stop"}">${pnlPct === null ? "-" : `${f(pnlPct)}%`}</td>
          <td class="num target">${target ? `${f(target)}k` : "-"}</td>
          <td class="num stop">${stop ? `${f(stop)}k` : "-"}</td>
          <td>${esc(note)}</td>
        </tr>
      `;
    }).join("") : `<tr><td colspan="13" class="empty-state">Chưa có lệnh cho policy này.</td></tr>`;
  }
  maybeNotifyNewTrades(latest);
}

async function loadPortfolio() {
  try {
    const res = await fetch("/api/portfolio", { cache: "no-store" });
    if (!res.ok) throw new Error("portfolio api unavailable");
    portfolio = await res.json();
    localStorage.setItem(portfolioStorageKey, JSON.stringify(portfolio));
    document.getElementById("portfolioSaveStatus").textContent = portfolio.updatedAt
      ? `Đã tải danh mục lưu lúc ${portfolio.updatedAt}`
      : "Chưa có danh mục đã lưu";
  } catch {
    const local = localStorage.getItem(portfolioStorageKey);
    portfolio = local ? JSON.parse(local) : portfolio;
    document.getElementById("portfolioSaveStatus").textContent = "Đang dùng bản lưu trong trình duyệt";
  }
  if (!Number(portfolio.navBil)) portfolio.navBil = 1;
  document.getElementById("portfolioNav").value = portfolio.navBil ?? 1;
  document.getElementById("portfolioCash").value = portfolio.cashBil ?? "";
  renderPortfolio();
}

function readPortfolioFromInputs() {
  portfolio.navBil = document.getElementById("portfolioNav").value || null;
  portfolio.cashBil = document.getElementById("portfolioCash").value || null;
  portfolio.holdings = [...document.querySelectorAll("#holdingRows tr")].map((tr) => ({
    symbol: tr.querySelector("[data-field='symbol']")?.value || "",
    quantity: tr.querySelector("[data-field='quantity']")?.value || "",
    avgPriceK: tr.querySelector("[data-field='avgPriceK']")?.value || "",
    note: tr.querySelector("[data-field='note']")?.value || ""
  }));
}

async function savePortfolio() {
  readPortfolioFromInputs();
  localStorage.setItem(portfolioStorageKey, JSON.stringify(portfolio));
  try {
    const res = await fetch("/api/portfolio", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(portfolio)
    });
    if (!res.ok) throw new Error("save failed");
    portfolio = await res.json();
    localStorage.setItem(portfolioStorageKey, JSON.stringify(portfolio));
    document.getElementById("portfolioSaveStatus").textContent = `Đã lưu ${portfolio.holdings.length} mã lúc ${portfolio.updatedAt}`;
  } catch {
    document.getElementById("portfolioSaveStatus").textContent = "Đã lưu local; mở server để lưu vào file.";
  }
  if (!Number(portfolio.navBil)) portfolio.navBil = 1;
  document.getElementById("portfolioNav").value = portfolio.navBil ?? 1;
  document.getElementById("portfolioCash").value = portfolio.cashBil ?? "";
  renderPortfolio();
}

function addHolding(row = {}) {
  readPortfolioFromInputs();
  portfolio.holdings.push({
    symbol: row.symbol || "",
    quantity: row.quantity || "",
    avgPriceK: row.avgPriceK || "",
    note: row.note || ""
  });
  renderPortfolio();
}

function modelHoldingsStats(holdings, navBil = lastEquitySnapshot().navBil) {
  const rows = holdings.map((h) => {
    const price = Number(h.currentPrice) || 0;
    const entryPrice = Number(h.entryPrice) || 0;
    const baseShares = n(h.modelShares, null);
    const shares = baseShares === null ? 0 : roundLot(baseShares * navBil);
    const currentMil = shares && price ? shares * price / 1000 : 0;
    const costMil = shares && entryPrice ? shares * entryPrice / 1000 : 0;
    const pnlMil = currentMil - costMil;
    const pnlPct = costMil > 0 ? pnlMil / costMil * 100 : null;
    return { symbol: String(h.symbol || "").toUpperCase(), shares, currentMil, costMil, pnlMil, pnlPct };
  });
  const investedCostMil = rows.reduce((sum, row) => sum + row.costMil, 0);
  const marketValueMil = rows.reduce((sum, row) => sum + row.currentMil, 0);
  const cashMil = Math.max(0, navBil * 1000 - investedCostMil);
  const totalNavMil = marketValueMil + cashMil;
  const baseMil = navBil * 1000;
  const gainMil = totalNavMil - baseMil;
  const gainPct = baseMil > 0 ? gainMil / baseMil * 100 : 0;
  return { rows, navBil, investedCostMil, marketValueMil, cashMil, totalNavMil, gainMil, gainPct };
}

function renderPortfolio() {
  arrangePortfolioLayout();
  const policy = activePolicy();
  const holdings = policy?.holdings || [];
  const modelLedger = lastEquitySnapshot();
  const holdingsStats = modelHoldingsStats(holdings, inputNavBil());
  const holdingsBySymbol = new Map(holdingsStats.rows.map((row) => [row.symbol, row]));

  const holdingsSummary = document.getElementById("holdingsSummary");
  if (holdingsSummary) {
    const positionLabel = holdings.map((h) => String(h.symbol || "").toUpperCase()).filter(Boolean).join(", ") || "-";
    holdingsSummary.innerHTML = `
      <span>Đang nắm <b>${positionLabel}</b></span>
      <span>Cổ phiếu <b>${moneyMilLabel(holdingsStats.marketValueMil)}</b></span>
      <span>Cash còn lại <b>${moneyMilLabel(holdingsStats.cashMil)}</b></span>
      <span>Tổng NAV hiện tại <b>${moneyMilLabel(holdingsStats.totalNavMil)}</b></span>
    `;
  }

  // Render danh mục model đang nắm giữ (read-only)
  const body = document.getElementById("holdingRows");
  if (body) {
    body.innerHTML = holdings.length ? holdings.map((h) => {
      const sym = String(h.symbol || "").toUpperCase();
      const weight = Number(h.suggestedWeight) || 0;
      const price = h.currentPrice;
      const target = h.targetPrice;
      const stop = h.stopPrice;
      const industry = h.industry || h.sleeve || "-";
      const entryDate = h.entryDate || "-";
      const entryPrice = n(h.entryPrice, null);
      const stat = holdingsBySymbol.get(sym);
      const shares = stat?.shares ?? null;
      const valueMil = stat ? stat.currentMil : null;
      const fallbackPnlMil = stat ? stat.pnlMil : null;
      const fallbackPnlPct = price && entryPrice ? (price / entryPrice - 1) * 100 : null;
      const pnlMil = fallbackPnlMil;
      const pnlPct = fallbackPnlPct;
      const priceLabel = price ? `${f(price)}k` : "-";
      return `
        <tr>
          <td><strong>${esc(sym)}</strong></td>
          <td><small>${esc(industry)}</small></td>
          <td>${esc(entryDate)}</td>
          <td class="num">${entryPrice ? `${f(entryPrice)}k` : "-"}</td>
          <td class="num">${priceLabel}</td>
          <td class="num">${shares ? f(shares, 0) : "-"}</td>
          <td class="num">${valueMil === null ? "-" : f(valueMil, 1)}</td>
          <td class="num ${pnlMil === null || pnlMil >= 0 ? "target" : "stop"}">${pnlMil === null ? "-" : f(pnlMil, 1)}</td>
          <td class="num ${pnlPct === null || pnlPct >= 0 ? "target" : "stop"}">${pnlPct === null ? "-" : `${f(pnlPct)}%`}</td>
          <td class="num">${f(weight)}%</td>
          <td class="num target">${target ? `${f(target)}k` : "-"}</td>
          <td class="num stop">${stop ? `${f(stop)}k` : "-"}</td>
        </tr>
      `;
    }).join("") : `<tr><td colspan="12" class="empty-state">Chưa có mã nào trong danh mục model.</td></tr>`;
  }

  // Summary metrics for the live model account; copy-trade order sizing is handled separately.
  const totalWeight = holdings.reduce((s, h) => s + (Number(h.suggestedWeight) || 0), 0);
  const cashPct = policy ? (Number(policy.cashBuffer) > 0 ? Number(policy.cashBuffer) : (100 - totalWeight)) : (100 - totalWeight);
  const perf = policyPerformanceSummary(activeHistory());

  const asset = currentAssetSnapshot();
  const assetSource = asset.source === "live_mtm"
    ? `Mark-to-market theo giá đến ${asset.lastDate}; đường NAV backtest gần nhất ${asset.ledgerDate}`
    : `NAV backtest tới ${asset.lastDate}`;

  const summary = `
    <div class="portfolio-summary">
      <span>Policy <b>${policyName(policy)}</b></span>
      <span>Vốn ban đầu <b>${moneyMilLabel(asset.initialNavMil)}</b> (${PERFORMANCE_START_DATE})</span>
      <span>Tổng tài sản hiện tại <b>${moneyMilLabel(asset.currentAssetMil)}</b></span>
      <span style="color:${asset.gainPct >= 0 ? '#2c7e4d' : '#c0392b'}">Lãi/lỗ <b>${asset.gainPct >= 0 ? '+' : ''}${moneyMilLabel(asset.gainMil)} (${asset.gainPct >= 0 ? '+' : ''}${f(asset.gainPct)}%)</b></span>
      <span>Nguồn <b>${assetSource}</b></span>
    </div>
    <div class="portfolio-summary">
      <span>Mã đang nắm <b>${holdings.length}</b></span>
      <span>Tổng tỷ trọng <b>${f(totalWeight)}% NAV</b></span>
      <span>Cash buffer <b>${f(cashPct)}%</b></span>
      ${policy ? `<span>Hiệu quả ${performanceWindowLabel(activeHistory())} <b>CAGR ${perf ? f(perf.cagr) : f(policy.historicalCagr)}%</b>, MaxDD <b>${perf ? f(perf.maxDrawdown) : f(policy.historicalMaxDrawdown)}%</b></span>` : ""}
      ${policy?.currentMode ? `<span>Regime <b>${policy.currentMode}</b> (spread ${policy.currentSpread > 0 ? '+' : ''}${policy.currentSpread}%)</span>` : ""}
    </div>
  `;

  const actionEl = document.getElementById("portfolioActionList");
  if (actionEl) actionEl.innerHTML = summary;
  const buyEl = document.getElementById("portfolioBuyList");
  if (buyEl) buyEl.innerHTML = "";
  renderTradeAlerts();
  renderPerformanceChart();
  renderModelLedger();
}

function renderModelLedger() {
  const hist = activeHistory();
  const rows = hist?.trades || [];
  const visible = rows.slice().reverse();
  const period = hist?.firstTradeDate && hist?.lastTradeDate ? ` | ${hist.firstTradeDate} đến ${hist.lastTradeDate}` : "";
  const summary = hist
    ? `${hist.label}${period} | ${hist.tradeCount} dòng lệnh đã gom theo mã/ngày | Khối lượng hiển thị làm tròn lô 100.`
    : "Chưa có lịch sử model";
  const summaryEl = document.getElementById("ledgerSummary");
  if (summaryEl) summaryEl.textContent = summary;
  const body = document.getElementById("modelLedgerRows");
  if (!body) return;
  body.innerHTML = visible.length ? visible.map((row) => {
    const shares = roundLot(displayTradeShares(row));
    const priceK = Number(row.executionPriceK || row.priceK || 0);
    const grossMil = displayTradeValueMil(row, shares, priceK) || null;
    const label = displayAction(row.actionLabel || row.side);
    const cls = actionClass(row.actionLabel || row.side);
    const isSell = isSellAction(label);
    const pnlMilRaw = displayTradePnlMil(row, shares, priceK, Number(row.entryPriceK || 0), grossMil || 0);
    const pnlMil = isSell ? pnlMilRaw : null;
    const returnPctRaw = row.returnPct === null || row.returnPct === undefined ? null : Number(row.returnPct);
    const returnPct = isSell ? returnPctRaw : null;
    return `
    <tr>
      <td>${row.triggerDate || row.date}</td>
      <td><strong>${row.symbol}</strong></td>
      <td><span class="action-pill ${cls}">${label}</span></td>
      <td class="num">${shares ? f(shares, 0) : "-"}</td>
      <td class="num">${priceK ? f(priceK) : "-"}${priceK ? "k" : ""}</td>
      <td class="num">${grossMil === null ? "-" : f(grossMil, 1)}</td>
      <td class="num ${pnlMil === null || pnlMil >= 0 ? "target" : "stop"}">${pnlMil === null ? "-" : f(pnlMil, 1)}</td>
      <td class="num ${returnPct === null || returnPct >= 0 ? "target" : "stop"}">${returnPct === null ? "-" : `${f(returnPct)}%`}</td>
      <td class="num">${row.holdDays === null || row.holdDays === undefined ? "-" : `${f(row.holdDays, 0)} ngày`}</td>
    </tr>
  `;
  }).join("") : `<tr><td colspan="9" class="empty-state">Chưa có dữ liệu mua bán cho policy này.</td></tr>`;
}

function activate(view) {
  document.querySelectorAll(".nav").forEach((btn) => btn.classList.toggle("active", btn.dataset.view === view));
  document.querySelectorAll(".view").forEach((el) => el.classList.toggle("is-active", el.id === view));
  if (view === "portfolio") setTimeout(renderPortfolio, 0);
  if (view === "watchlist") setTimeout(renderWatchlistTab, 0);
  if (view === "modelLogic") setTimeout(renderModelLogicTab, 0);
}

function on(id, event, handler) {
  const el = document.getElementById(id);
  if (el) el.addEventListener(event, handler);
}

document.querySelectorAll(".nav").forEach((btn) => btn.addEventListener("click", () => activate(btn.dataset.view)));
on("printBtn", "click", () => window.print());
on("updateBtn", "click", () => triggerUpdate("fast"));
on("strategyMode", "change", (e) => {
  strategyMode = e.target.value;
  localStorage.setItem("hose_hnx_strategy_mode", strategyMode);
  renderActiveModel();
});
on("portfolioNav", "input", () => {
  const el = document.getElementById("portfolioNav");
  portfolio.navBil = el ? el.value || null : null;
  localStorage.setItem(portfolioStorageKey, JSON.stringify(portfolio));
  renderPortfolio();
});
on("enableAlertsBtn", "click", () => {
  if (typeof Notification !== "undefined") Notification.requestPermission();
});
on("markAlertsSeenBtn", "click", () => {
  markTradeSignalsSeen();
  renderTradeAlerts();
});
document.querySelectorAll("#performanceRange .range").forEach((btn) => btn.addEventListener("click", () => {
  performanceRange = btn.dataset.range || "ytd";
  localStorage.setItem("hose_hnx_performance_range", performanceRange);
  document.querySelectorAll("#performanceRange .range").forEach((b) => b.classList.toggle("active", b === btn));
  renderPerformanceChart();
}));

async function init() {
  setStaticUpdateMode();
  setModelLoading("");
  await refreshStatus();
  if (!isLocalDashboardHost()) {
    await refreshOnlineLivePrices();
    if (!onlineLiveRefreshTimer) {
      onlineLiveRefreshTimer = setInterval(refreshOnlineLivePrices, LIVE_REFRESH_INTERVAL_MS);
    }
  }
  syncStrategyOptions();
  renderActiveModel();
}

window.addEventListener("resize", () => {
  if (resizeRenderTimer) clearTimeout(resizeRenderTimer);
  resizeRenderTimer = setTimeout(() => {
    renderPerformanceChart();
  }, 120);
});

init();
