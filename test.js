
async function safeFetch(url, options){
  const controller = new AbortController();
  const timeoutMs = Number((options && options.timeoutMs) || 15000);
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try{
    const finalOptions = Object.assign({}, options || {}, { signal: controller.signal });
    const response = await fetch(url, finalOptions);
    if(!response.ok){
      let detail = "Request failed";
      try{
        const payload = await response.json();
        detail = payload.detail || JSON.stringify(payload);
      }catch(err){}
      return { __error:true, status:response.status, detail };
    }
    return await response.json();
  }catch(err){
    return { __error:true, status:0, detail: err && err.name === "AbortError" ? "Request timeout" : "Network error" };
  }finally{
    clearTimeout(timer);
  }
}

const defaultPairs = ["BTC_EUR","BTC_CZK","ETH_EUR","ETH_CZK","ADA_CZK"];
let pairOrder = loadPairOrder();
let dashboardSnapshot = null;
let currentMode = "paper";
let liveArmed = false;
let robotStatus = "STOPPED";
let refreshInFlight = null;
let refreshQueued = false;
let activeBigChart = null;
let activeUtilityModal = null;
let activeOrderTicket = null;
const pairCharts = {};
const pairSparkCharts = {};
const dashboardPairs = {};
const topbarStateCache = {};
const chartDataCache = {};

function storageKeyChart(pair){ return "myrobot_chart_state_" + pair; }
function loadChartState(pair){ try{ return JSON.parse(localStorage.getItem(storageKeyChart(pair)) || "null"); }catch(err){ return null; } }
function saveChartState(pair, state){ try{ localStorage.setItem(storageKeyChart(pair), JSON.stringify(state)); }catch(err){} }

function finiteOrNull(value){
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}
function sanitizeChartState(saved, len){
  if(!saved || typeof saved !== "object") return null;
  const total = Math.max(0, Number(len) || 0);
  const out = {
    panelLayout: saved.panelLayout || undefined,
    autoFollowLatest: saved.autoFollowLatest !== false,
    overlayEnabled: saved.overlayEnabled !== false,
    strategyFocus: saved.strategyFocus === true,
    yScale: (()=>{ const n = finiteOrNull(saved.yScale); return n != null ? Math.max(0.3, Math.min(6, n)) : 1; })(),
    yPan: (()=>{ const n = finiteOrNull(saved.yPan); return n != null ? n : 0; })()
  };
  const rawStart = finiteOrNull(saved.viewStart);
  const rawEnd = finiteOrNull(saved.viewEnd);
  if(rawStart != null && rawEnd != null && total > 0){
    let start = Math.max(0, Math.min(total, rawStart));
    let end = Math.max(0, Math.min(total, rawEnd));
    if(end <= start){
      const size = Math.min(total, Math.max(20, Math.min(96, total)));
      end = total;
      start = Math.max(0, end - size);
    }
    out.viewStart = start;
    out.viewEnd = end;
  }
  return out;
}
function payloadCandles(payload){
  if(payload && Array.isArray(payload.candles) && payload.candles.length) return payload.candles;
  if(payload && Array.isArray(payload.series) && payload.series.length) return payload.series;
  return [];
}
function hasChartCandles(payload){
  return payloadCandles(payload).length > 0;
}
function cacheChartPayload(pair, timeframe, payload){
  const candles = payloadCandles(payload);
  if(!pair || !candles.length) return;
  chartDataCache[`${pair}:${timeframe}`] = { candles, overlay: payload?.overlay || null, meta: payload?.meta || null, source: payload?.source || null, timeframe };
}
function getCachedChartPayload(pair, timeframe){
  return chartDataCache[`${pair}:${timeframe}`] || chartDataCache[`${pair}:24h`] || null;
}
function normalizeChartPayload(pair, timeframe, payload){
  const candles = payloadCandles(payload);
  const out = {
    ...(payload && typeof payload === "object" ? payload : {}),
    pair,
    timeframe,
    candles,
    series: candles
  };
  if(candles.length) cacheChartPayload(pair, timeframe, out);
  return out;
}
function buildSyntheticCandlesFromCloseSeries(prices, endTime){
  const clean = (Array.isArray(prices) ? prices : []).map(v=>Number(v)).filter(v=>Number.isFinite(v) && v > 0);
  if(!clean.length) return [];
  const nowSec = Math.floor((endTime != null ? Number(endTime) : Date.now()) / 1000);
  const step = clean.length >= 80 ? 15 * 60 : 24 * 60 * 60;
  let prev = clean[0];
  return clean.map((price, idx)=>{
    const time = nowSec - ((clean.length - 1 - idx) * step);
    const spread = Math.max(price * 0.0035, Math.abs(price - prev), 1e-8);
    const open = prev;
    const close = price;
    const high = Math.max(open, close) + spread * 0.45;
    const low = Math.min(open, close) - spread * 0.45;
    prev = price;
    return { time, open, high, low, close, volume: 0 };
  });
}
function fallbackChartPayload(pair, timeframe){
  const sparkCandles = buildCandles(pairSparkCharts[pair] || []);
  if(sparkCandles.length){
    const overlay = getCachedChartPayload(pair, timeframe)?.overlay || getCachedChartPayload(pair, "24h")?.overlay || null;
    return {
      pair,
      timeframe,
      candles: sparkCandles,
      series: sparkCandles,
      overlay,
      source: "frontend_pair_spark_fallback",
      meta: { available: true, degraded: true, source_state: "frontend_pair_spark_fallback", available_points: sparkCandles.length }
    };
  }
  const pairRow = pairSnapshot(pair) || {};
  const prices = [
    pairRow?.market?.price,
    pairRow?.market?.bid,
    pairRow?.market?.ask,
    pairRow?.portfolio?.equity,
  ].filter(v=>Number.isFinite(Number(v)) && Number(v) > 0);
  if(prices.length){
    const synthetic = buildSyntheticCandlesFromCloseSeries([prices[0], ...prices, prices[0]], Date.now());
    return {
      pair,
      timeframe,
      candles: synthetic,
      series: synthetic,
      overlay: null,
      source: "frontend_price_stub",
      meta: { available: synthetic.length > 0, degraded: true, source_state: "frontend_price_stub", available_points: synthetic.length }
    };
  }
  return { pair, timeframe, candles: [], series: [], overlay: null, meta: { available: false } };
}
async function fetchPairChart(pair, timeframe, days){
  const urls = [
    `/api/pair/${encodeURIComponent(pair)}/chart?timeframe=${encodeURIComponent(timeframe)}&days=${encodeURIComponent(days)}`,
    `/api/pair/${encodeURIComponent(pair)}/chart`
  ];
  for(const url of urls){
    const res = await safeFetch(url, { timeoutMs: 15000 });
    if(res && !res.__error && hasChartCandles(res)) return normalizeChartPayload(pair, timeframe, res);
  }
  if(timeframe !== "24h"){
    const intraday = await fetchPairChart(pair, "24h", Math.max(1, Math.min(7, days || 1)));
    if(hasChartCandles(intraday)){
      return normalizeChartPayload(pair, timeframe, {
        ...intraday,
        timeframe,
        source: `${intraday.source || "chart"}_fullscreen_fallback`,
        meta: { ...(intraday.meta || {}), degraded: true, source_state: "fullscreen_intraday_fallback" }
      });
    }
  }
  const cached = getCachedChartPayload(pair, timeframe);
  if(cached && hasChartCandles(cached)) return normalizeChartPayload(pair, timeframe, { ...cached, timeframe, source: `${cached.source || "cache"}_cache_fallback` });
  return normalizeChartPayload(pair, timeframe, fallbackChartPayload(pair, timeframe));
}
function overlaySeriesToMap(series){
  const map = new Map();
  (Array.isArray(series) ? series : []).forEach((row, idx)=>{
    if(row && typeof row === "object" && row.time != null){
      const v = finiteOrNull(row.value);
      const t = finiteOrNull(row.time);
      if(v != null && t != null) map.set(t, v);
    }else{
      const v = finiteOrNull(row);
      if(v != null) map.set(idx, v);
    }
  });
  return map;
}
function getVisiblePriceStats(){
  const visible = buildCandles(getVisibleSeries());
  if(!visible.length){
    return { min: 0, max: 1, baseMin: 0, baseMax: 1, baseRange: 1 };
  }
  const values = [];
  visible.forEach(c=>values.push(c.high, c.low));
  let baseMin = Math.min(...values), baseMax = Math.max(...values);
  if(baseMin === baseMax){ baseMin -= 1; baseMax += 1; }
  const baseRange = Math.max(1e-9, baseMax - baseMin);
  const center = (baseMin + baseMax) / 2 + (Number(activeBigChart?.yPan) || 0);
  const half = ((baseRange / 2) * (activeBigChart?.yScale || 1));
  return {
    baseMin, baseMax, baseRange,
    min: center - half,
    max: center + half
  };
}

function normalizeOverlayPointTime(value){
  const t = finiteOrNull(value);
  if(t == null) return null;
  return t > 10_000_000_000 ? Math.round(t / 1000) : Math.round(t);
}
function normalizeOverlayPrice(value){
  const v = finiteOrNull(value);
  return v != null && v > 0 ? v : null;
}
function visibleTimeRange(candles){
  if(!Array.isArray(candles) || !candles.length) return { start: null, end: null };
  return { start: candles[0].time, end: candles[candles.length - 1].time };
}
function candleIndexByTime(candles, time){
  const t = normalizeOverlayPointTime(time);
  if(t == null || !Array.isArray(candles) || !candles.length) return -1;
  let best = -1;
  let bestDelta = Infinity;
  candles.forEach((c, idx)=>{
    const delta = Math.abs((c?.time || 0) - t);
    if(delta < bestDelta){
      bestDelta = delta;
      best = idx;
    }
  });
  return best;
}
function normalizeOverlayRange(item, candles){
  if(!item || typeof item !== "object") return null;
  const top = normalizeOverlayPrice(item.top ?? item.upper ?? item.high ?? item.max ?? item.price_high);
  const bottom = normalizeOverlayPrice(item.bottom ?? item.lower ?? item.low ?? item.min ?? item.price_low);
  if(top == null && bottom == null) return null;
  const rangeTop = Math.max(top ?? bottom ?? 0, bottom ?? top ?? 0);
  const rangeBottom = Math.min(top ?? bottom ?? 0, bottom ?? top ?? 0);
  let startIdx = candleIndexByTime(candles, item.start_time ?? item.from_time ?? item.time ?? item.ts_start);
  let endIdx = candleIndexByTime(candles, item.end_time ?? item.to_time ?? item.time ?? item.ts_end);
  if(startIdx < 0) startIdx = 0;
  if(endIdx < 0) endIdx = Math.max(startIdx + 1, candles.length - 1);
  if(endIdx < startIdx){ const tmp = endIdx; endIdx = startIdx; startIdx = tmp; }
  return {
    startIdx,
    endIdx,
    top: rangeTop,
    bottom: rangeBottom,
    label: item.label || item.name || item.kind || "",
    kind: item.kind || item.type || "",
    confidence: finiteOrNull(item.confidence),
  };
}
function normalizeOverlayMarker(item){
  if(!item || typeof item !== "object") return null;
  const time = normalizeOverlayPointTime(item.time ?? item.ts ?? item.timestamp);
  const price = normalizeOverlayPrice(item.price ?? item.value ?? item.level ?? item.entry ?? item.exit);
  if(time == null || price == null) return null;
  return {
    time,
    price,
    label: item.label || item.signal || item.kind || item.type || "",
    kind: item.kind || item.type || item.signal || "",
    side: item.side || "",
    confidence: finiteOrNull(item.confidence),
  };
}
function extractOverlayCollections(overlay){
  const safe = overlay && typeof overlay === "object" ? overlay : {};
  return {
    signalBand: Array.isArray(safe.signal_band) ? safe.signal_band : [],
    entries: Array.isArray(safe.entries) ? safe.entries : [],
    exits: Array.isArray(safe.exits) ? safe.exits : [],
    stopLossBoxes: Array.isArray(safe.stop_loss_boxes) ? safe.stop_loss_boxes : [],
    takeProfitBoxes: Array.isArray(safe.take_profit_boxes) ? safe.take_profit_boxes : [],
    supportZones: Array.isArray(safe.support_zones) ? safe.support_zones : [],
    resistanceZones: Array.isArray(safe.resistance_zones) ? safe.resistance_zones : [],
    strategyMarkers: Array.isArray(safe.strategy_markers) ? safe.strategy_markers : [],
    structureMarkers: Array.isArray(safe.structure_markers) ? safe.structure_markers : [],
    aiMarkers: Array.isArray(safe.ai_markers) ? safe.ai_markers : [],
    decisionTimeline: Array.isArray(safe.decision_timeline) ? safe.decision_timeline : [],
    riskMarkers: Array.isArray(safe.risk_markers) ? safe.risk_markers : [],
  };
}
function drawOverlayRanges(ctx, candles, toY, xAt, ranges, fillStyle, strokeStyle){
  (Array.isArray(ranges) ? ranges : []).forEach(item=>{
    const r = normalizeOverlayRange(item, candles);
    if(!r) return;
    const x1 = xAt(Math.max(0, Math.min(candles.length-1, r.startIdx))) - 6;
    const x2 = xAt(Math.max(0, Math.min(candles.length-1, r.endIdx))) + 6;
    const y1 = toY(r.top);
    const y2 = toY(r.bottom);
    const top = Math.min(y1, y2);
    const h = Math.max(3, Math.abs(y2 - y1));
    ctx.save();
    ctx.fillStyle = fillStyle;
    ctx.strokeStyle = strokeStyle;
    ctx.lineWidth = 1;
    ctx.fillRect(x1, top, Math.max(8, x2 - x1), h);
    ctx.strokeRect(x1, top, Math.max(8, x2 - x1), h);
    if(r.label){
      ctx.fillStyle = strokeStyle;
      ctx.font = "11px Arial";
      ctx.fillText(r.label, x1 + 4, top + 12);
    }
    ctx.restore();
  });
}
function drawOverlayMarkers(ctx, candles, toY, xAt, markers, options){
  const opts = options || {};
  (Array.isArray(markers) ? markers : []).forEach(item=>{
    const m = normalizeOverlayMarker(item);
    if(!m) return;
    const idx = candleIndexByTime(candles, m.time);
    if(idx < 0) return;
    const x = xAt(idx);
    const y = toY(m.price);
    const color = opts.color || "#f0b34b";
    const shape = opts.shape || "dot";
    ctx.save();
    ctx.fillStyle = color;
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    if(shape === "triangle-up"){
      ctx.beginPath();
      ctx.moveTo(x, y - 8); ctx.lineTo(x - 6, y + 6); ctx.lineTo(x + 6, y + 6); ctx.closePath(); ctx.fill();
    }else if(shape === "triangle-down"){
      ctx.beginPath();
      ctx.moveTo(x, y + 8); ctx.lineTo(x - 6, y - 6); ctx.lineTo(x + 6, y - 6); ctx.closePath(); ctx.fill();
    }else if(shape === "diamond"){
      ctx.beginPath();
      ctx.moveTo(x, y - 7); ctx.lineTo(x - 6, y); ctx.lineTo(x, y + 7); ctx.lineTo(x + 6, y); ctx.closePath(); ctx.fill();
    }else{
      ctx.beginPath(); ctx.arc(x, y, 4, 0, Math.PI*2); ctx.fill();
    }
    if(opts.label !== false){
      const label = m.label || opts.defaultLabel || "";
      if(label){
        ctx.font = "11px Arial";
        ctx.fillStyle = color;
        ctx.fillText(label, x + 8, y - 8);
      }
    }
    ctx.restore();
  });
}
function drawSignalBand(ctx, candles, toY, xAt, signalBand){
  const normalized = (Array.isArray(signalBand) ? signalBand : [])
    .map(item => normalizeOverlayRange(item, candles))
    .filter(Boolean);
  normalized.forEach(r=>{
    const x1 = xAt(Math.max(0, Math.min(candles.length-1, r.startIdx))) - 4;
    const x2 = xAt(Math.max(0, Math.min(candles.length-1, r.endIdx))) + 4;
    const y1 = toY(r.top);
    const y2 = toY(r.bottom);
    const top = Math.min(y1, y2);
    const h = Math.max(2, Math.abs(y2 - y1));
    ctx.save();
    ctx.fillStyle = "rgba(96, 165, 250, 0.10)";
    ctx.strokeStyle = "rgba(96, 165, 250, 0.45)";
    ctx.lineWidth = 1;
    ctx.fillRect(x1, top, Math.max(8, x2 - x1), h);
    ctx.strokeRect(x1, top, Math.max(8, x2 - x1), h);
    ctx.restore();
  });
}


function loadPairOrder(){
  try{
    const parsed = JSON.parse(localStorage.getItem("myrobot_pair_order") || "null");
    if(!Array.isArray(parsed)) return [...defaultPairs];
    const out = parsed.filter(v => defaultPairs.includes(v));
    defaultPairs.forEach(pair=>{ if(!out.includes(pair)) out.push(pair); });
    return out;
  }catch(err){
    return [...defaultPairs];
  }
}
function savePairOrder(){
  localStorage.setItem("myrobot_pair_order", JSON.stringify(pairOrder));
}
function setFeedback(text, isError){
  const el = document.getElementById("actionFeedback");
  if(!el) return;
  el.innerText = text || "";
  el.className = "actionFeedback " + (text ? (isError ? "red" : "green") : "");
}

async function safeRun(label, fn){
  try{
    return await fn();
  }catch(err){
    console.error(label, err);
    return null;
  }
}

function setRobotActionBusy(isBusy){
  ["robotStartBtn","robotStopBtn","robotEmergencyBtn"].forEach(id=>{
    const el = document.getElementById(id);
    if(el) el.disabled = !!isBusy;
  });
}
function bindRobotButtons(){
  const startBtn = document.getElementById("robotStartBtn");
  const stopBtn = document.getElementById("robotStopBtn");
  const emergencyBtn = document.getElementById("robotEmergencyBtn");
  if(startBtn && !startBtn.dataset.bound){
    startBtn.dataset.bound = "1";
    startBtn.addEventListener("click", (ev)=>{ ev.preventDefault(); robotStart(); });
  }
  if(stopBtn && !stopBtn.dataset.bound){
    stopBtn.dataset.bound = "1";
    stopBtn.addEventListener("click", (ev)=>{ ev.preventDefault(); robotStop(); });
  }
  if(emergencyBtn && !emergencyBtn.dataset.bound){
    emergencyBtn.dataset.bound = "1";
    emergencyBtn.addEventListener("click", (ev)=>{ ev.preventDefault(); robotEmergency(); });
  }
}
function setModeUi(mode){
  currentMode = String(mode || "paper").toLowerCase() === "live" ? "live" : "paper";
  document.getElementById("modeBadge").className = "modeBadge " + currentMode;
  document.getElementById("modeBadge").innerText = currentMode.toUpperCase();
  document.getElementById("modeStatus").innerText = currentMode.toUpperCase();
  document.getElementById("modeSelect").value = currentMode;
  document.getElementById("modeEmoji").innerText = currentMode === "live" ? "🚀" : "🙂";
}
function setArmUi(armed){
  liveArmed = !!armed;
  document.getElementById("armDiode").className = "diode " + (liveArmed ? "on" : "off");
  document.getElementById("armStatus").innerText = liveArmed ? "ARMED" : "DISARMED";
}
function setRobotUi(status){
  robotStatus = String(status || "STOPPED").toUpperCase();
  const label = document.getElementById("robotStatusText");
  const diode = document.getElementById("robotDiode");
  const cls = robotStatus === "RUNNING" ? "running" : robotStatus === "STOPPED" ? "stopped" : (robotStatus === "ERROR" ? "red" : "yellow");
  label.innerText = robotStatus;
  label.className = "statusValue " + cls;
  diode.className = "diode " + (robotStatus === "RUNNING" ? "on" : "off");
}
function formatNumber(value){
  if(value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if(!Number.isFinite(n)) return "—";
  if(Math.abs(n) >= 1000) return n.toFixed(2);
  if(Math.abs(n) >= 1) return n.toFixed(4);
  return n.toFixed(5);
}
function formatPercent(value, scale){
  if(value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if(!Number.isFinite(n)) return "—";
  return (n * (scale || 1)).toFixed(2) + "%";
}
function isFiniteNumber(value){
  const n = Number(value);
  return Number.isFinite(n);
}
function hasPositiveNumber(value){
  const n = Number(value);
  return Number.isFinite(n) && n > 0;
}
function displayStatus(status){
  return String(status || "UNAVAILABLE").toUpperCase();
}
function pairSnapshot(pair){
  return dashboardPairs[pair] || null;
}
function createPanels(){
  const grid = document.getElementById("pairGrid");
  grid.innerHTML = "";
  pairOrder.forEach(pair=>{
    const card = document.createElement("div");
    card.className = "pairCard";
    card.dataset.pair = pair;
    const isDraggable = pair === "BTC_CZK";
    if(isDraggable) card.classList.add("drag-enabled");
    card.innerHTML = `
      ${isDraggable ? `<div class="dragHandle" draggable="true" title="Přetáhni panel">↕ drag</div>` : ``}
      <div class="pairHeader">${pair}</div>
      <div class="metricRow">Price: <span id="${pair}_price">—</span></div>
      <div class="metricRow">Bid/Ask: <span id="${pair}_bid">—</span> / <span id="${pair}_ask">—</span></div>
      <div class="metricRow">Spread: <span id="${pair}_spread">—</span></div>
      <div class="metricRow">Robot: <span id="${pair}_robot">UNAVAILABLE</span></div>
      <div class="metricRow">Capital: <span id="${pair}_capital_mode">—</span> <span id="${pair}_capital_value">—</span></div>
      <div class="metricRow">AI Prediction: <span id="${pair}_pred">—</span></div>
      <div class="metricRow">Confidence: <span id="${pair}_conf">—</span></div>
      <div class="aiRow">
        <div>Signal: <span id="${pair}_signal">—</span></div>
        <div>Strategy: <span id="${pair}_strategy">—</span></div>
        <div>Regime: <span id="${pair}_regime">—</span></div>
      </div>
      <div class="metricRow">PnL: <span id="${pair}_pnl">—</span></div>
      <canvas id="${pair}_spark" class="sparkline"></canvas>
      <button class="killBtn" onclick="event.stopPropagation(); killPair('${pair}')">KILL</button>
    `;
    card.onclick = (e)=>{ if(e.target.closest(".dragHandle")) return; openFullscreen(pair); };
    grid.appendChild(card);
  });
  attachPairDragAndDrop();
}
function attachPairDragAndDrop(){
  const grid = document.getElementById("pairGrid");
  const cards = Array.from(grid.querySelectorAll(".pairCard"));
  let draggedPair = null;
  cards.forEach(card=>{
    const pair = card.dataset.pair;
    const handle = card.querySelector(".dragHandle");
    if(handle){
      handle.addEventListener("dragstart",(e)=>{
        draggedPair = pair;
        card.classList.add("dragging");
        if(e.dataTransfer){
          e.dataTransfer.effectAllowed = "move";
          e.dataTransfer.setData("text/plain", pair);
        }
      });
      handle.addEventListener("dragend",()=>{
        draggedPair = null;
        cards.forEach(c=>c.classList.remove("dragging","drop-target"));
      });
    }
    card.addEventListener("dragover",(e)=>{
      if(!draggedPair || pair===draggedPair) return;
      e.preventDefault();
      card.classList.add("drop-target");
    });
    card.addEventListener("dragleave",()=>card.classList.remove("drop-target"));
    card.addEventListener("drop",(e)=>{
      if(!draggedPair || pair===draggedPair) return;
      e.preventDefault();
      cards.forEach(c=>c.classList.remove("drop-target"));
      const from = pairOrder.indexOf(draggedPair);
      const to = pairOrder.indexOf(pair);
      if(from === -1 || to === -1) return;
      pairOrder.splice(from,1);
      pairOrder.splice(to,0,draggedPair);
      savePairOrder();
      createPanels();
      renderDashboard();
    });
  });
}
function topbarFallbackForBase(base){
  const entries = Object.values(dashboardPairs || {}).filter(row => row && typeof row === "object");
  const matches = entries
    .filter(row => String(row.pair || "").toUpperCase().startsWith(String(base || "").toUpperCase() + "_"))
    .filter(row => hasPositiveNumber(row?.market?.price))
    .sort((a,b)=>{
      const aq = String(a.pair || "").split("_")[1] || "";
      const bq = String(b.pair || "").split("_")[1] || "";
      const rank = q => ({USD:0, EUR:1, CZK:2}[q] ?? 9);
      return rank(aq) - rank(bq);
    });
  if(!matches.length) return topbarStateCache[String(base || "").toUpperCase()] || null;
  const row = matches[0];
  const fallback = {
    price: Number(row.market.price),
    quote: String(row.pair || "").split("_")[1] || "",
    change_24h: finiteOrNull(row?.market?.change_24h),
    source: "pair_snapshot"
  };
  topbarStateCache[String(base || "").toUpperCase()] = fallback;
  return fallback;
}
function updateTopbarTicker(sym, row, fallback){
  const symbol = String(sym || "").toUpperCase();
  const id = symbol.toLowerCase();
  const cached = topbarStateCache[symbol] || null;
  const livePrice = hasPositiveNumber(row?.price_usd) ? Number(row.price_usd) : null;
  const fallbackPrice = hasPositiveNumber(fallback?.price) ? Number(fallback.price) : null;
  const cachedPrice = hasPositiveNumber(cached?.price) ? Number(cached.price) : null;
  const price = livePrice ?? fallbackPrice ?? cachedPrice;
  const change = finiteOrNull(row?.change_24h) ?? finiteOrNull(fallback?.change_24h) ?? finiteOrNull(cached?.change_24h);
  const quote = livePrice != null ? "USD" : (fallback?.quote || cached?.quote || "USD");
  if(price != null){
    topbarStateCache[symbol] = { price, quote, change_24h: change, source: livePrice != null ? "simple_prices" : (fallback?.source || cached?.source || "cache") };
  }
  const priceEl = document.getElementById(id + "Price");
  const quoteEl = document.getElementById(id + "Quote");
  const changeEl = document.getElementById(id + "Change");
  if(priceEl) priceEl.innerText = price != null ? formatNumber(price) : "—";
  if(quoteEl) quoteEl.innerText = quote;
  if(changeEl){
    changeEl.innerText = formatPercent(change,1);
    changeEl.className = isFiniteNumber(change) ? (Number(change) >= 0 ? "green change" : "red change") : "muted change";
  }
}
function updateTopSummary(summary, market, global){
  document.getElementById("portfolioValue").innerText = formatNumber(summary?.portfolio_value);
  document.getElementById("portfolioPnL").innerText = formatNumber(summary?.pnl_today);
  document.getElementById("portfolioExposure").innerText = formatNumber(summary?.exposure);
  document.getElementById("portfolioTrades").innerText = Number.isFinite(Number(summary?.open_trades)) ? String(summary.open_trades) : "—";
  const cryptoPct = Number(summary?.crypto_pct);
  const fiatPct = Number(summary?.fiat_pct);
  document.getElementById("cryptoRatio").style.width = Number.isFinite(cryptoPct) ? cryptoPct + "%" : "0%";
  document.getElementById("cryptoText").innerText = Number.isFinite(fiatPct) && Number.isFinite(cryptoPct) ? `${fiatPct}% fiat / ${cryptoPct}% crypto` : "—";
  document.getElementById("holdings").innerText = Array.isArray(summary?.holdings) && summary.holdings.length ? summary.holdings.join(" ") : "—";

  const top = market?.top || {};
  [["BTC","btc"],["ETH","eth"],["ADA","ada"]].forEach(([sym, id])=>{
    updateTopbarTicker(sym, top[sym] || {}, topbarFallbackForBase(sym));
  });
  const sent = Number(market?.market_sentiment);
  document.getElementById("sentFill").style.width = Number.isFinite(sent) ? Math.max(0, Math.min(100, sent)) + "%" : "0%";
  document.getElementById("sentValue").innerText = Number.isFinite(sent) ? `${Math.round(sent)} / 100` : "—";
  setModeUi(global?.mode);
  setArmUi(global?.armed);
  setRobotUi(global?.robot_status);
}
function renderPairCards(){
  pairOrder.forEach(pair=>{
    const snap = pairSnapshot(pair);
    const market = snap?.market || {};
    const portfolio = snap?.portfolio || {};
    const ai = snap?.ai || {};
    const capital = snap?.capital || {};
    const availability = snap?.availability || {};
    const readiness = snap?.readiness || {};
    const setText = (id, value, cls) => {
      const el = document.getElementById(id);
      if(!el) return;
      el.innerText = value;
      if(cls !== undefined) el.className = cls;
    };

    const marketReady = availability.market_data === true;
    const bidAskReady = availability.bid_ask === true;
    const aiReady = ai.available === true && ai.fallback_signal !== true;

    setText(`${pair}_price`, marketReady ? formatNumber(market.price) : "—");
    setText(`${pair}_bid`, bidAskReady ? formatNumber(market.bid) : "—");
    setText(`${pair}_ask`, bidAskReady ? formatNumber(market.ask) : "—");
    setText(`${pair}_spread`, bidAskReady ? formatPercent(market.spread_pct,1) : "—");

    const status = displayStatus(snap?.status);
    const statusClass = status === "STOPPED"
      ? "red"
      : (status === "RUNNING" && readiness.safe_to_trade === true ? "green" : "yellow");
    setText(`${pair}_robot`, status, statusClass);

    setText(`${pair}_capital_mode`, capital.mode ? capital.mode : "—");
    setText(`${pair}_capital_value`, formatNumber(capital.value));
    setText(`${pair}_pred`, aiReady ? formatPercent(ai.prediction,1) : "—", isFiniteNumber(ai.prediction) ? (Number(ai.prediction) >= 0 ? "green" : "red") : "muted");
    setText(`${pair}_conf`, aiReady ? formatPercent(ai.confidence,100) : "—", isFiniteNumber(ai.confidence) ? (Number(ai.confidence) >= 0.5 ? "cyan" : "muted") : "muted");
    setText(`${pair}_signal`, aiReady ? (ai.signal || "—") : "—", ai.signal === "BUY" ? "green" : (ai.signal === "SELL" ? "red" : "muted"));
    setText(`${pair}_strategy`, aiReady ? (ai.strategy || "—") : "—");
    setText(`${pair}_regime`, aiReady ? (ai.regime || "—") : "—");
    setText(`${pair}_pnl`, formatNumber(portfolio.pnl), Number(portfolio.pnl) >= 0 ? "green" : "red");
    drawSpark(pair);
  });
}
function normalizeCloseSeries(candles){
  if(!Array.isArray(candles)) return [];
  return candles.map(c=>Number((c && c.close !== undefined) ? c.close : c)).filter(v=>Number.isFinite(v) && v>0);
}
async function loadPairMiniChart(pair){
  const res = await fetchPairChart(pair, "24h", 1);
  pairSparkCharts[pair] = Array.isArray(res?.candles) ? res.candles : [];
}
async function loadAllMiniCharts(){
  await Promise.all(pairOrder.map(pair => loadPairMiniChart(pair)));
  renderPairCards();
}
function drawSpark(pair){
  const canvas = document.getElementById(pair + "_spark");
  if(!canvas) return;
  canvas.width = 360; canvas.height = 40;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0,0,canvas.width,canvas.height);
  const candles = pairSparkCharts[pair] || [];
  const prices = normalizeCloseSeries(candles);
  if(!prices.length){
    ctx.strokeStyle = "#353535";
    ctx.beginPath(); ctx.moveTo(0,20); ctx.lineTo(canvas.width,20); ctx.stroke();
    ctx.fillStyle = "#6b7280"; ctx.font = "11px Arial"; ctx.fillText("No data", 6, 14);
    return;
  }
  let min = Math.min(...prices), max = Math.max(...prices);
  if(min === max){ min -= Math.max(min*0.002, 1e-6); max += Math.max(max*0.002, 1e-6); }
  const green = prices[prices.length - 1] >= prices[0];
  ctx.strokeStyle = green ? "#00c853" : "#ff4976";
  ctx.lineWidth = 2;
  ctx.beginPath();
  prices.forEach((price, idx)=>{
    const x = idx * (canvas.width / Math.max(prices.length - 1, 1));
    const y = canvas.height - ((price - min) / (max - min)) * 36 - 2;
    if(idx===0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();
}
function renderDashboard(){
  if(!dashboardSnapshot) return;
  const pairList = Array.isArray(dashboardSnapshot.pairs) ? dashboardSnapshot.pairs : [];
  Object.keys(dashboardPairs).forEach(k=>delete dashboardPairs[k]);
  pairList.forEach(row=>{ dashboardPairs[row.pair] = row; });
  updateTopSummary(dashboardSnapshot.summary, dashboardSnapshot.market, dashboardSnapshot.global);
  renderPairCards();
}
async function fetchDashboardSnapshot(){
  const snap = await safeFetch("/api/dashboard/snapshot");
  if(snap && !snap.__error){
    dashboardSnapshot = snap;
    renderDashboard();
  }
}
async function loadTrades(){
  const data = await safeFetch("/api/trades");
  document.getElementById("tradesList").innerHTML = Array.isArray(data) && data.length
    ? data.slice(0,10).map(row=>`<div>${row.pair || ""} ${row.side || ""} ${row.pnl ?? "—"}</div>`).join("")
    : `<div class="sectionPlaceholder">No trades yet</div>`;
}
async function loadSignals(){
  const data = await safeFetch("/api/signals");
  document.getElementById("signalsList").innerHTML = Array.isArray(data) && data.length
    ? data.slice(0,10).map(row=>`<div>${row.pair || ""} ${row.signal || "—"} ${row.confidence != null ? '(' + formatPercent(row.confidence,100) + ')' : ''}</div>`).join("")
    : `<div class="sectionPlaceholder">No signals yet</div>`;
}
async function renderHeatmapSection(){
  const el = document.getElementById("heatmapPlaceholder");
  const data = await safeFetch("/api/signals");
  let rows = Array.isArray(data) ? data : [];
  if(!rows.length){
    const pairs = Array.isArray(dashboardSnapshot?.pairs) ? dashboardSnapshot.pairs : [];
    rows = pairs.filter(row => row?.ai?.signal || row?.signal).map(row => ({
      pair: row.pair,
      signal: row?.ai?.signal || row?.signal || "—",
      confidence: row?.ai?.confidence ?? row?.confidence ?? null
    }));
  }
  if(!rows.length){
    el.innerHTML = `<div class="sectionPlaceholder">No live signal data</div>`;
    return;
  }
  el.innerHTML = rows.slice(0,12).map(row=>`
    <div style="display:flex;justify-content:space-between;gap:12px;margin:4px 0;">
      <span>${row.pair || "—"}</span>
      <span class="${row.signal === "BUY" ? "green" : (row.signal === "SELL" ? "red" : "muted")}">${row.signal || "—"}</span>
      <span>${formatPercent(row.confidence,100)}</span>
    </div>`).join("");
}
async function renderEquityCurveSection(){
  const el = document.getElementById("equityCurvePlaceholder");
  const data = await safeFetch("/api/equity_curve");
  if(!Array.isArray(data) || !data.length){
    el.innerHTML = `<div class="sectionPlaceholder">No equity curve data</div>`;
    return;
  }
  const first = data[0], last = data[data.length - 1];
  const delta = Number(last.equity || 0) - Number(first.equity || 0);
  el.innerHTML = `
    <div style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;">
      <div>Points: ${data.length}</div>
      <div>Start: ${formatNumber(first.equity)}</div>
      <div>Latest: ${formatNumber(last.equity)}</div>
      <div class="${delta >= 0 ? "green" : "red"}">Delta: ${formatNumber(delta)}</div>
    </div>`;
}
async function renderRiskMapSection(){
  const el = document.getElementById("riskMapPlaceholder");
  const data = await safeFetch("/api/metrics");
  let rows = Array.isArray(data?.risk_map) ? data.risk_map : [];
  if(!rows.length){
    const pairs = Array.isArray(dashboardSnapshot?.pairs) ? dashboardSnapshot.pairs : [];
    rows = pairs.map(row => ({
      pair: row.pair,
      status: row?.status || "—",
      confidence: row?.ai?.confidence ?? row?.confidence ?? null,
      pnl: row?.portfolio?.unrealized_pnl ?? row?.portfolio?.realized_pnl ?? 0,
      risk_level: row?.risk?.risk_level ?? row?.ai?.risk_level ?? null,
      exposure: row?.portfolio?.exposure ?? 0
    }));
  }
  el.innerHTML = rows.length ? rows.map(row=>`
    <div style="display:flex;justify-content:space-between;gap:12px;margin:4px 0;">
      <span>${row.pair || "—"}</span>
      <span class="${row.status === "RUNNING" ? "green" : (row.status === "STOPPED" ? "red" : "yellow")}">${row.status || "—"}</span>
      <span>${formatPercent(row.confidence,100)}</span>
      <span class="${Number(row.pnl) >= 0 ? "green" : "red"}">${formatNumber(row.pnl)}</span>
    </div>`).join("") : `<div class="sectionPlaceholder">No risk data</div>`;
}
async function refreshSections(){ await Promise.all([renderHeatmapSection(), renderEquityCurveSection(), renderRiskMapSection()]); }

async function loadControlState(){
  const res = await safeFetch("/api/control/state");
  if(res && !res.__error){
    setModeUi(res.mode);
    setArmUi(res.armed);
    setRobotUi(res.robot_status || res.status);
  }
}
async function robotStart(){
  setRobotActionBusy(true);
  setFeedback("Starting robot…", false);
  const res = await safeFetch("/api/robot/start",{method:"POST", timeoutMs:10000});
  if(res && !res.__error){
    let status = null;
    for(let i=0;i<20;i++){
      await new Promise(r=>setTimeout(r, 300));
      status = await safeFetch("/api/control/state",{timeoutMs:5000});
      if(status && !status.__error){
        setModeUi(status.mode);
        setArmUi(status.armed);
        setRobotUi(status.robot_status || status.status);
        const robot = String((status.robot_status || status.status || "")).toUpperCase();
        if(robot === "RUNNING"){
          await safeRun("refreshAfterRobotStart", refresh);
          setFeedback("Robot started", false);
          setRobotActionBusy(false);
          return;
        }
      }
    }
    await safeRun("refreshAfterRobotStartPending", refresh);
    const robot = String((status && (status.robot_status || status.status)) || "").toUpperCase();
    setFeedback(robot === "RUNNING" ? "Robot started" : ((res.message || "Start request sent, but robot is still not RUNNING")), robot !== "RUNNING");
  } else {
    setFeedback((res && (res.detail || res.message)) || "Start failed", true);
  }
  setRobotActionBusy(false);
}
async function robotStop(){ const res = await safeFetch("/api/robot/stop",{method:"POST"}); if(res && !res.__error){ setFeedback("",false); await refresh(); } else setFeedback(res?.detail || "Stop failed", true); }
async function robotEmergency(){ const res = await safeFetch("/api/robot/emergency",{method:"POST"}); if(res && !res.__error){ setFeedback("Emergency activated", false); await refresh(); } else setFeedback(res?.detail || "Emergency failed", true); }
async function applyMode(){ const mode=document.getElementById("modeSelect").value; const res=await safeFetch("/api/control/mode",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({mode})}); if(res && !res.__error){ setFeedback(`Mode set to ${String(res.mode).toUpperCase()}`,false); await refresh(); } else setFeedback(res?.detail || "Mode change failed", true); }
async function armLive(){ const res=await safeFetch("/api/control/arm",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({armed:true})}); if(res && !res.__error){ setFeedback("LIVE armed",false); await refresh(); } else setFeedback(res?.detail || "ARM failed", true); }
async function disarmLive(){ const res=await safeFetch("/api/control/arm",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({armed:false})}); if(res && !res.__error){ setFeedback("LIVE disarmed",false); await refresh(); } else setFeedback(res?.detail || "DISARM failed", true); }

function closeUtilityModal(){ if(activeUtilityModal){ activeUtilityModal.remove(); activeUtilityModal=null; } }
function openUtilityModal(title, contentHtml, actionsHtml){
  closeUtilityModal();
  const modal = document.createElement("div");
  modal.className = "utilityModal";
  modal.innerHTML = `<div class="utilityCard"><h3>${title}</h3><div>${contentHtml}</div><div class="utilityActions">${actionsHtml || `<button type="button" onclick="closeUtilityModal()">Close</button>`}</div></div>`;
  modal.addEventListener("click",(e)=>{ if(e.target===modal) closeUtilityModal(); });
  document.body.appendChild(modal); activeUtilityModal = modal;
}
async function openHealth(){
  const [control, robot, pairs, metrics] = await Promise.all([
    safeFetch("/api/control/state"), safeFetch("/api/robot/status"), safeFetch("/api/pairs"), safeFetch("/api/metrics")
  ]);
  openUtilityModal("Health", `<pre>${JSON.stringify({control, robot, pairs, metrics}, null, 2)}</pre>`);
}
async function openRiskConfig(){
  const metrics = await safeFetch("/api/metrics");
  openUtilityModal("Risk Config", `<pre>${JSON.stringify(metrics, null, 2)}</pre>`);
}
async function clearEmergencyFromUi(){ const res=await safeFetch("/api/control/emergency/clear",{method:"POST"}); if(res && !res.__error){ closeUtilityModal(); await refresh(); } }
async function robotRestartFromUi(){ const res=await safeFetch("/api/robot/restart",{method:"POST"}); if(res && !res.__error){ closeUtilityModal(); await refresh(); } }

function defaultOrderTicket(pair){
  const snap = pairSnapshot(pair);
  return { pair, side:"BUY", type:"MARKET", amount:"", price:hasPositiveNumber(snap?.market?.price) ? String(snap.market.price) : "", stopLoss:"", takeProfit:"" };
}
function ensureOrderTicket(pair){ if(!activeOrderTicket || activeOrderTicket.pair !== pair){ activeOrderTicket = defaultOrderTicket(pair); } return activeOrderTicket; }
function syncOrderTicketFromInputs(){
  if(!activeBigChart) return;
  const ticket = ensureOrderTicket(activeBigChart.pair);
  ticket.amount = document.getElementById("orderAmount")?.value || "";
  ticket.price = document.getElementById("orderPrice")?.value || "";
  ticket.stopLoss = document.getElementById("orderStopLoss")?.value || "";
  ticket.takeProfit = document.getElementById("orderTakeProfit")?.value || "";
}
function updateOrderTicketUi(){
  if(!activeBigChart) return;
  const ticket = ensureOrderTicket(activeBigChart.pair);
  document.getElementById("orderSide").innerText = ticket.side;
  document.getElementById("orderType").innerText = ticket.type;
  document.getElementById("orderAmount").value = ticket.amount;
  document.getElementById("orderPrice").value = ticket.price;
  document.getElementById("orderStopLoss").value = ticket.stopLoss;
  document.getElementById("orderTakeProfit").value = ticket.takeProfit;
  document.getElementById("orderPrice").disabled = ticket.type === "MARKET";
}
function setOrderAction(action){
  if(!activeBigChart) return;
  const ticket = ensureOrderTicket(activeBigChart.pair);
  if(action === "BUY" || action === "SELL") ticket.side = action;
  if(action === "MARKET" || action === "LIMIT") ticket.type = action;
  updateOrderTicketUi();
}
async function submitOrderTicket(){
  if(!activeBigChart) return;
  syncOrderTicketFromInputs();
  const ticket = ensureOrderTicket(activeBigChart.pair);
  const statusEl = document.getElementById("orderTicketStatus");
  const payload = {
    pair: ticket.pair,
    side: ticket.side.toLowerCase(),
    amount: Number(ticket.amount),
    type: ticket.type.toLowerCase(),
    price: ticket.type === "LIMIT" ? Number(ticket.price) : null,
    stop_loss: ticket.stopLoss ? Number(ticket.stopLoss) : null,
    take_profit: ticket.takeProfit ? Number(ticket.takeProfit) : null,
    client_order_id: "ui-" + Date.now(),
    note: "manual order from dashboard"
  };
  const res = await safeFetch("/api/order/manual",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
  if(res && !res.__error){
    statusEl.className = "orderStatus green";
    statusEl.innerText = "Odesláno.";
    await refresh();
  }else{
    statusEl.className = "orderStatus red";
    statusEl.innerText = res?.detail || "Order failed.";
  }
}
async function saveCapital(pair){
  const payload = {
    capital_mode: document.getElementById("capitalModeInput").value,
    capital_value: Number(document.getElementById("capitalValueInput").value || 0)
  };
  const res = await safeFetch(`/api/pair/${encodeURIComponent(pair)}/capital`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
  const statusEl = document.getElementById("capitalStatus");
  if(res && !res.__error){
    statusEl.className = "capitalStatus green";
    statusEl.innerText = "Saved";
    await refresh();
  }else{
    statusEl.className = "capitalStatus red";
    statusEl.innerText = res?.detail || "Capital save failed";
  }
}

function normalizeChartTimestamp(value){
  if(value === null || value === undefined || value === "") return 0;
  const n = Number(value);
  if(Number.isFinite(n)) return n > 10_000_000_000 ? Math.floor(n/1000) : Math.floor(n);
  const parsed = Date.parse(String(value));
  return Number.isFinite(parsed) ? Math.floor(parsed/1000) : 0;
}
function normalizeCandleRow(row){
  if(!row || typeof row !== "object") return null;
  const time = normalizeChartTimestamp(row.time ?? row.timestamp);
  const open = Number(row.open), high=Number(row.high), low=Number(row.low), close=Number(row.close), volume=Number(row.volume || 0);
  if(!time || !Number.isFinite(open) || !Number.isFinite(high) || !Number.isFinite(low) || !Number.isFinite(close) || Math.min(open,high,low,close) <= 0) return null;
  return { time, open, high: Math.max(high, open, close), low: Math.min(low, open, close), close, volume: Number.isFinite(volume)&&volume>0 ? volume : 0, value:close };
}
function buildCandles(series){
  const map = new Map();
  (Array.isArray(series)?series:[]).forEach(row=>{ const c = normalizeCandleRow(row); if(c) map.set(c.time, c); });
  return Array.from(map.values()).sort((a,b)=>a.time-b.time);
}
function formatDailyLabel(ts){
  const d = new Date(ts*1000);
  return `${String(d.getDate()).padStart(2,"0")}.${String(d.getMonth()+1).padStart(2,"0")}`;
}
function computePanelDefaults(type){
  return type==="hud"
    ? {left:16, top:14, minimized:false, maximized:false}
    : {right:16, top:14, minimized:false, maximized:false};
}
function panelElementId(type){ return type==="hud" ? "chartHudPanel" : "chartLegendPanel"; }
function normalizePanelLayout(type, panel, cfg){
  const parent = document.getElementById("bigChart");
  const defaults = computePanelDefaults(type);
  const out = Object.assign({}, defaults, cfg || {});
  if(!parent || !panel) return out;
  const bounds = parent.getBoundingClientRect();
  const panelW = Math.max(180, panel.offsetWidth || 220);
  const panelH = Math.max(42, panel.offsetHeight || 120);
  const maxLeft = Math.max(8, bounds.width - panelW - 8);
  const maxTop = Math.max(8, bounds.height - panelH - 8);
  if(out.maximized){
    delete out.left;
    if(type === "legend"){ out.right = 16; } else { out.left = 16; out.right = undefined; }
    out.top = 14;
  }else if(type === "legend" && out.left == null){
    const right = finiteOrNull(out.right);
    const clampedRight = right != null ? Math.max(8, Math.min(bounds.width - panelW - 8, right)) : 16;
    out.right = clampedRight;
    out.top = Math.max(8, Math.min(maxTop, finiteOrNull(out.top) ?? defaults.top));
  }else{
    const left = finiteOrNull(out.left);
    out.left = Math.max(8, Math.min(maxLeft, left != null ? left : defaults.left));
    out.right = undefined;
    out.top = Math.max(8, Math.min(maxTop, finiteOrNull(out.top) ?? defaults.top));
  }
  out.minimized = !!out.minimized;
  out.maximized = !!out.maximized;
  return out;
}
function updatePanelLayout(type, patch){
  if(!activeBigChart) return;
  const panel = document.getElementById(panelElementId(type));
  const current = activeBigChart.panelLayout?.[type] || computePanelDefaults(type);
  const next = normalizePanelLayout(type, panel, Object.assign({}, current, patch || {}));
  activeBigChart.panelLayout[type] = next;
  applyPanelLayout();
  persistActiveChartState();
}
function togglePanelMinimize(type){
  if(!activeBigChart) return;
  const cfg = activeBigChart.panelLayout?.[type] || computePanelDefaults(type);
  updatePanelLayout(type, { minimized: !cfg.minimized, maximized: cfg.maximized && cfg.minimized ? cfg.maximized : false });
}
function togglePanelMaximize(type){
  if(!activeBigChart) return;
  const cfg = activeBigChart.panelLayout?.[type] || computePanelDefaults(type);
  updatePanelLayout(type, { maximized: !cfg.maximized, minimized: false });
}
function resetPanelPosition(type){
  if(!activeBigChart) return;
  activeBigChart.panelLayout[type] = computePanelDefaults(type);
  applyPanelLayout();
  persistActiveChartState();
}
function applyPanelLayout(){
  if(!activeBigChart) return;
  ["hud","legend"].forEach(type=>{
    const panel = document.getElementById(panelElementId(type));
    if(!panel) return;
    const cfg = normalizePanelLayout(type, panel, activeBigChart.panelLayout[type] || computePanelDefaults(type));
    activeBigChart.panelLayout[type] = cfg;
    panel.style.left = cfg.left != null ? cfg.left + "px" : "";
    panel.style.right = cfg.right != null ? cfg.right + "px" : "";
    panel.style.top = (cfg.top || 14) + "px";
    panel.classList.toggle("minimized", !!cfg.minimized);
    panel.classList.toggle("maximized", !!cfg.maximized);
    const minBtn = panel.querySelector("[data-panel-action='min']");
    const maxBtn = panel.querySelector("[data-panel-action='max']");
    if(minBtn) minBtn.textContent = cfg.minimized ? "□" : "–";
    if(maxBtn) maxBtn.textContent = cfg.maximized ? "❐" : "▢";
  });
}
function persistActiveChartState(){
  if(!activeBigChart) return;
  saveChartState(activeBigChart.pair, {
    viewStart: activeBigChart.viewStart,
    viewEnd: activeBigChart.viewEnd,
    yScale: activeBigChart.yScale,
    yPan: activeBigChart.yPan,
    autoFollowLatest: activeBigChart.autoFollowLatest,
    overlayEnabled: activeBigChart.overlayEnabled,
    strategyFocus: activeBigChart.strategyFocus === true,
    panelLayout: activeBigChart.panelLayout
  });
}
function clampViewWindow(){
  if(!activeBigChart) return;
  const len = Array.isArray(activeBigChart.fullSeries) ? activeBigChart.fullSeries.length : 0;
  if(len <= 0){ activeBigChart.viewStart = 0; activeBigChart.viewEnd = 0; activeBigChart.windowSize = 0; return; }
  let start = finiteOrNull(activeBigChart.viewStart);
  let end = finiteOrNull(activeBigChart.viewEnd);
  if(start == null || end == null){
    const size = Math.min(len, Math.max(20, activeBigChart.windowSize || 96));
    activeBigChart.viewEnd = len;
    activeBigChart.viewStart = Math.max(0, len - size);
    activeBigChart.windowSize = size;
    return;
  }
  let size = end - start;
  if(!Number.isFinite(size) || size < 20) size = Math.min(20, len);
  if(size > len) size = len;
  if(start < 0){ start = 0; end = size; }
  if(end > len){ end = len; start = Math.max(0, end - size); }
  if(end <= start){
    end = Math.min(len, Math.max(start + 20, len));
    start = Math.max(0, end - Math.min(size || 20, len));
  }
  activeBigChart.viewStart = start;
  activeBigChart.viewEnd = end;
  activeBigChart.windowSize = size;
}
function getVisibleSeries(){
  if(!activeBigChart) return [];
  const full = activeBigChart.fullSeries || [];
  if(!full.length) return [];
  if(activeBigChart.autoFollowLatest){
    const size = Math.min(full.length, Math.max(20, activeBigChart.windowSize || 96));
    activeBigChart.viewEnd = full.length;
    activeBigChart.viewStart = Math.max(0, full.length - size);
  }
  clampViewWindow();
  return full.slice(Math.floor(activeBigChart.viewStart), Math.ceil(activeBigChart.viewEnd));
}
function strategyCollections(base){
  const c = base || extractOverlayCollections(activeBigChart?.overlay || {});
  if(!activeBigChart?.strategyFocus) return c;
  const lifecycle = Array.isArray(activeBigChart?.overlay?.signal_lifecycle) ? activeBigChart.overlay.signal_lifecycle : [];
  const relevantIds = new Set(
    lifecycle
      .filter(row => Number(row?.timeframe_relevance || 0) >= 0.55)
      .map(row => `${row.signal || ""}_${row.ts || ""}`)
  );
  const filterMarkers = rows => (Array.isArray(rows) ? rows : []).filter(row=>{
    const key = `${row?.signal || ""}_${row?.ts || ""}`;
    const relevance = Number(row?.timeframe_relevance || 0);
    return relevance >= 0.55 || relevantIds.has(key) || !row?.timeframe_relevance;
  });
  return {
    signalBand: c.signalBand,
    entries: c.entries,
    exits: c.exits,
    stopLossBoxes: c.stopLossBoxes,
    takeProfitBoxes: c.takeProfitBoxes,
    supportZones: c.supportZones.slice(0, 4),
    resistanceZones: c.resistanceZones.slice(0, 4),
    strategyMarkers: filterMarkers(c.strategyMarkers),
    structureMarkers: filterMarkers(c.structureMarkers),
    aiMarkers: [],
    decisionTimeline: [],
    riskMarkers: [],
  };
}
function drawBigChartCanvas(canvas){
  const crosshairLabel = document.getElementById("crosshairLabel");
  const series = getVisibleSeries();
  const candles = buildCandles(series);
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.parentElement.getBoundingClientRect();
  const width = Math.max(720, rect.width), height = Math.max(640, rect.height);
  canvas.width = Math.floor(width * dpr); canvas.height = Math.floor(height * dpr);
  canvas.style.width = width + "px"; canvas.style.height = height + "px";
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr,0,0,dpr,0,0);
  ctx.clearRect(0,0,width,height);
  ctx.fillStyle = "#0b1020"; ctx.fillRect(0,0,width,height);

  const leftPad = 14, rightPad = 96, topPad = 24, bottomPad = 42;
  const plotW = width - leftPad - rightPad, plotH = height - topPad - bottomPad - 80;

  ctx.strokeStyle = "rgba(73,98,132,0.26)";
  ctx.lineWidth = 1;
  ctx.setLineDash([4,4]);
  for(let i=0;i<=6;i++){ const y=topPad+(plotH/6)*i; ctx.beginPath(); ctx.moveTo(leftPad,y); ctx.lineTo(leftPad+plotW,y); ctx.stroke(); }
  for(let i=0;i<=8;i++){ const x=leftPad+(plotW/8)*i; ctx.beginPath(); ctx.moveTo(x,topPad); ctx.lineTo(x,topPad+plotH+80); ctx.stroke(); }
  ctx.setLineDash([]);

  if(!candles.length){
    ctx.fillStyle="#94a3b8";
    ctx.font="13px Arial";
    ctx.fillText("No chart data available from backend — using fallback when possible", leftPad+20, topPad+28);
    if(crosshairLabel) crosshairLabel.style.display="none";
    return;
  }

  const priceStats = getVisiblePriceStats();
  let min = priceStats.min, max = priceStats.max;
  if(min === max){ min -= 1; max += 1; }
  const toY = (v)=> topPad + (1 - ((v - min) / (max - min))) * plotH;
  const stepX = plotW / Math.max(candles.length,1);
  const bodyW = Math.max(6, Math.min(18, stepX * 0.72));
  const xAt = idx => leftPad + idx * stepX + stepX/2;

  const overlay = activeBigChart.overlay || {};
  const collections = strategyCollections(extractOverlayCollections(overlay));
  const ai = (activeBigChart.snapshot && activeBigChart.snapshot.ai) || {};
  const showEma = activeBigChart.overlayEnabled && !activeBigChart.strategyFocus;

  if(activeBigChart.overlayEnabled){
    drawSignalBand(ctx, candles, toY, xAt, collections.signalBand);
    drawOverlayRanges(ctx, candles, toY, xAt, collections.supportZones, "rgba(16, 185, 129, 0.10)", "rgba(16, 185, 129, 0.55)");
    drawOverlayRanges(ctx, candles, toY, xAt, collections.resistanceZones, "rgba(239, 68, 68, 0.10)", "rgba(239, 68, 68, 0.55)");
    drawOverlayRanges(ctx, candles, toY, xAt, collections.stopLossBoxes, "rgba(244, 63, 94, 0.10)", "rgba(244, 63, 94, 0.65)");
    drawOverlayRanges(ctx, candles, toY, xAt, collections.takeProfitBoxes, "rgba(34, 197, 94, 0.10)", "rgba(34, 197, 94, 0.65)");
  }

  candles.forEach((c,idx)=>{
    const x=xAt(idx), yO=toY(c.open), yC=toY(c.close), yH=toY(c.high), yL=toY(c.low);
    const up = c.close >= c.open;
    ctx.strokeStyle = up ? "#24e0a0" : "#ff5b87";
    ctx.lineWidth = Math.max(1, Math.min(2, bodyW * 0.16));
    ctx.beginPath();
    ctx.moveTo(x,yH); ctx.lineTo(x,yL);
    ctx.stroke();

    ctx.fillStyle = up ? "#18d392" : "#ff4976";
    const bodyTop = Math.min(yO,yC);
    const bodyH = Math.max(2, Math.abs(yC-yO));
    ctx.fillRect(x-bodyW/2, bodyTop, bodyW, bodyH);
    ctx.strokeStyle = up ? "rgba(20,89,70,0.95)" : "rgba(106,21,50,0.95)";
    ctx.lineWidth = 1;
    ctx.strokeRect(x-bodyW/2, bodyTop, bodyW, bodyH);
  });

  if(showEma && overlay.indicators){
    const drawLine = (seriesData, color, width=2)=>{
      const byTime = overlaySeriesToMap(seriesData);
      const points = [];
      candles.forEach((c, idx)=>{
        let v = byTime.get(c.time);
        if(v == null && byTime.has(activeBigChart.viewStart + idx)) v = byTime.get(activeBigChart.viewStart + idx);
        if(Number.isFinite(Number(v))) points.push({x:xAt(idx), y:toY(Number(v))});
      });
      if(points.length > 1){
        ctx.save();
        ctx.strokeStyle = color;
        ctx.lineWidth = width;
        ctx.beginPath();
        points.forEach((p,i)=> i ? ctx.lineTo(p.x,p.y) : ctx.moveTo(p.x,p.y));
        ctx.stroke();
        ctx.restore();
      }
    };
    drawLine(overlay.indicators.ema20, "#6fa8ff", 2);
    drawLine(overlay.indicators.ema50, "#f0b34b", 2);
  }

  if(activeBigChart.overlayEnabled){
    drawOverlayMarkers(ctx, candles, toY, xAt, collections.entries, { color:"#18d392", shape:"triangle-up", defaultLabel:"Entry" });
    drawOverlayMarkers(ctx, candles, toY, xAt, collections.exits, { color:"#ff4976", shape:"triangle-down", defaultLabel:"Exit" });
    drawOverlayMarkers(ctx, candles, toY, xAt, collections.strategyMarkers, { color:"#f0b34b", shape:"diamond", defaultLabel:"Strategy" });
    drawOverlayMarkers(ctx, candles, toY, xAt, collections.structureMarkers, { color:"#d8b4fe", shape:"diamond", defaultLabel:"Structure" });
    drawOverlayMarkers(ctx, candles, toY, xAt, collections.aiMarkers, { color:"#60a5fa", shape:"dot", defaultLabel:"AI" });
    drawOverlayMarkers(ctx, candles, toY, xAt, collections.decisionTimeline, { color:"#c084fc", shape:"dot", defaultLabel:"Decision" });
    drawOverlayMarkers(ctx, candles, toY, xAt, collections.riskMarkers, { color:"#f97316", shape:"diamond", defaultLabel:"Risk" });

    if(ai && ai.signal){
      const last = candles[candles.length - 1];
      const x = xAt(candles.length - 1);
      const y = toY(last.close);
      const label = `AI ${ai.signal}${ai.confidence != null ? ` ${Math.round(Number(ai.confidence)*100)}%` : ""}`;
      const color = ai.signal === "BUY" ? "#18d392" : (ai.signal === "SELL" ? "#ff4976" : "#f0b34b");
      ctx.save();
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(x, y, 4, 0, Math.PI*2);
      ctx.fill();
      ctx.font = "bold 12px Arial";
      const labelW = Math.max(70, ctx.measureText(label).width + 12);
      const lx = Math.max(leftPad, Math.min(leftPad + plotW - labelW, x + 10));
      const ly = Math.max(topPad + 4, y - 18);
      ctx.fillStyle = "rgba(5,11,20,0.88)";
      ctx.fillRect(lx, ly - 12, labelW, 18);
      ctx.strokeStyle = color;
      ctx.strokeRect(lx, ly - 12, labelW, 18);
      ctx.fillStyle = color;
      ctx.fillText(label, lx + 6, ly + 1);
      ctx.restore();
    }
  }

  const ticks = 6;
  ctx.fillStyle = "#91a0b8";
  ctx.font = "12px Arial";
  ctx.textAlign = "left";
  for(let i=0;i<=ticks;i++){
    const value = max - ((max-min)/ticks)*i;
    const y = topPad + (plotH/ticks)*i;
    ctx.fillText(formatNumber(value), leftPad + plotW + 10, y + 4);
  }

  const timeStep = Math.max(1, Math.floor(candles.length/6));
  ctx.textAlign="center";
  for(let i=0;i<candles.length;i+=timeStep){
    ctx.fillStyle="#7f8ea8";
    ctx.fillText(formatDailyLabel(candles[i].time), xAt(i), height-14);
  }

  const priceLineValue = Number((activeBigChart.meta && activeBigChart.meta.current_price) || candles[candles.length-1].close || 0);
  if(priceLineValue > 0){
    const y = toY(priceLineValue);
    ctx.save();
    ctx.strokeStyle = "#1dd1a1";
    ctx.lineWidth = 1;
    ctx.setLineDash([5,4]);
    ctx.beginPath(); ctx.moveTo(leftPad,y); ctx.lineTo(leftPad+plotW,y); ctx.stroke();
    ctx.setLineDash([]);
    const labelX = leftPad + plotW + 8, labelW = rightPad - 16, labelH = 22;
    ctx.fillStyle = "#1dd1a1";
    ctx.fillRect(labelX, y - labelH/2, labelW, labelH);
    ctx.fillStyle = "#06111b";
    ctx.font = "bold 12px Arial";
    ctx.textAlign = "center";
    ctx.fillText(formatNumber(priceLineValue), labelX + labelW/2, y + 4);
    ctx.restore();
  }

  const focusIdx = Number.isInteger(activeBigChart.hoverIndex) && activeBigChart.hoverIndex >= 0 && activeBigChart.hoverIndex < candles.length ? activeBigChart.hoverIndex : candles.length - 1;
  const focus = candles[focusIdx];
  ctx.fillStyle = "#91a0b8";
  ctx.font = "12px Arial";
  ctx.textAlign = "left";
  ctx.fillText(`${activeBigChart.pair}  O ${formatNumber(focus.open)}  H ${formatNumber(focus.high)}  L ${formatNumber(focus.low)}  C ${formatNumber(focus.close)}`, leftPad + 4, 14);

  if(Number.isInteger(activeBigChart.hoverIndex) && activeBigChart.hoverIndex >= 0 && activeBigChart.hoverIndex < candles.length){
    const x = xAt(activeBigChart.hoverIndex);
    ctx.strokeStyle = "rgba(145,160,184,0.55)";
    ctx.setLineDash([4,4]);
    ctx.beginPath(); ctx.moveTo(x,topPad); ctx.lineTo(x,topPad+plotH+80); ctx.stroke();
    ctx.setLineDash([]);
    if(crosshairLabel){
      crosshairLabel.style.display="block";
      crosshairLabel.style.left = Math.min(width-170, Math.max(16, x+14)) + "px";
      crosshairLabel.style.top = Math.max(16, topPad+18) + "px";
      crosshairLabel.innerHTML = `${formatDailyLabel(focus.time)}<br>O ${formatNumber(focus.open)} H ${formatNumber(focus.high)}<br>L ${formatNumber(focus.low)} C ${formatNumber(focus.close)}`;
    }
  }else if(crosshairLabel){
    crosshairLabel.style.display="none";
  }
}
function updateChartHud(){
  if(!activeBigChart) return;
  const hud=document.getElementById("chartHudBody"), legend=document.getElementById("chartLegendBody"), toolbarInfo=document.getElementById("chartToolbarInfo");
  if(!hud || !legend || !toolbarInfo) return;
  const candles = buildCandles(getVisibleSeries());
  const overlay = activeBigChart.overlay || {};
  const collections = strategyCollections(extractOverlayCollections(overlay));
  const ai=(activeBigChart.snapshot && activeBigChart.snapshot.ai) || {};
  document.getElementById("overlayBtn")?.classList.toggle("active", !!activeBigChart.overlayEnabled);
  document.getElementById("aiStrategyBtn")?.classList.toggle("active", !!activeBigChart.strategyFocus);
  document.getElementById("autoFollowBtn")?.classList.toggle("active", !!activeBigChart.autoFollowLatest);

  const overlayCounts = {
    entries: collections.entries.length,
    exits: collections.exits.length,
    support: collections.supportZones.length,
    resistance: collections.resistanceZones.length,
    sl: collections.stopLossBoxes.length,
    tp: collections.takeProfitBoxes.length,
    strategy: collections.strategyMarkers.length,
    structure: collections.structureMarkers.length,
    risk: collections.riskMarkers.length,
    ai: collections.aiMarkers.length,
  };

  if(!candles.length){
    const currentPrice = Number((activeBigChart.meta && activeBigChart.meta.current_price) || 0);
    hud.innerHTML = `
      <div><b>${activeBigChart.pair}</b></div>
      <div>No chart data</div>
      <div>Signal: ${ai.signal || "—"}</div>
      <div>Confidence: ${formatPercent(ai.confidence,100)}</div>
      <div>Current price: ${currentPrice > 0 ? formatNumber(currentPrice) : "—"}</div>
      <div>Overlay markers: ${overlayCounts.entries + overlayCounts.exits + overlayCounts.strategy + overlayCounts.ai}</div>`;
    legend.innerHTML = activeBigChart.overlayEnabled ? `
      <div><span style="color:#1dd1a1">—</span> Current price</div>
      ${activeBigChart.strategyFocus ? "" : `<div><span style="color:#6ea8ff">—</span> EMA20</div>
      <div><span style="color:#f0b34b">—</span> EMA50</div>`}
      <div><span style="color:#60a5fa">■</span> Signal band</div>
      <div><span style="color:#18d392">▲</span> Entry</div>
      <div><span style="color:#ff4976">▼</span> Exit</div>
      <div><span style="color:#22c55e">▭</span> TP zone</div>
      <div><span style="color:#f43f5e">▭</span> SL zone</div>
      <div><span style="color:#10b981">▭</span> Support</div>
      <div><span style="color:#ef4444">▭</span> Resistance</div>
      <div><span style="color:#d8b4fe">◆</span> Structure</div>
      <div><span style="color:#60a5fa">●</span> AI marker</div>
      <div class="muted">Waiting for backend candles</div>` : `Overlay disabled`;
    toolbarInfo.innerText = activeBigChart.strategyFocus ? "Strategy focus · drag X · Shift+drag Y · wheel zoom" : "Backend truth chart · drag X · Shift+drag Y · wheel zoom";
    return;
  }

  const last = candles[candles.length - 1];
  const first = candles[0];
  const change = Number(last.close || 0) - Number(first.open || 0);
  const pct = Number(first.open) > 0 ? (change / Number(first.open)) * 100 : null;
  const meta = activeBigChart.meta || {};
  const marketStructure = overlay.market_structure || {};
  const lifecycle = Array.isArray(overlay.signal_lifecycle) ? overlay.signal_lifecycle : [];
  const activeSetups = lifecycle.filter(row=>["planned","armed"].includes(String(row?.state || "").toLowerCase())).length;
  hud.innerHTML = `
    <div><b>${activeBigChart.pair}</b></div>
    <div>Visible candles: ${candles.length}</div>
    <div>Last close: ${formatNumber(last.close)}</div>
    <div>Range: ${formatNumber(Math.min(...candles.map(c=>c.low)))} → ${formatNumber(Math.max(...candles.map(c=>c.high)))}</div>
    <div class="${change >= 0 ? "green" : "red"}">Window PnL: ${formatNumber(change)} ${pct != null ? `(${formatPercent(pct,1)})` : ""}</div>
    <div>Signal: ${ai.signal || overlay.signal || "—"} ${ai.confidence != null ? `(${formatPercent(ai.confidence,100)})` : ""}</div>
    <div>Strategy: ${ai.strategy || overlay.strategy || "—"}</div>
    <div>Regime: ${ai.regime || overlay.regime || marketStructure.volatility_regime || "—"}</div>
    <div>Trend bias: ${marketStructure.trend || overlay.bias || "—"} ${Number.isFinite(Number(marketStructure.trend_score)) ? `(${Math.round(Number(marketStructure.trend_score)*100)} score)` : ""}</div>
    <div>Momentum: ${marketStructure.momentum || "—"} ${Number.isFinite(Number(marketStructure.momentum_pct)) ? `(${formatPercent(Number(marketStructure.momentum_pct),1)})` : ""}</div>
    <div>Volatility: ${marketStructure.volatility_regime || "—"} ${Number.isFinite(Number(marketStructure.atr_pct)) ? `(ATR ${formatPercent(Number(marketStructure.atr_pct),1)})` : ""}</div>
    <div>Dominant setup: ${overlay?.summary?.dominant_setup ? String(overlay.summary.dominant_setup).replaceAll("_"," ") : "—"}</div>
    <div>Setups: active ${activeSetups} / total ${lifecycle.length} ${Number.isFinite(Number(overlay?.summary?.weighted_active_setups)) ? `(weighted ${Number(overlay.summary.weighted_active_setups).toFixed(2)})` : ""}</div>
    <div>Source: ${meta.source_state || meta.source || "—"}</div>
    <div>Mode: ${activeBigChart.strategyFocus ? "STRATEGY FOCUS" : (activeBigChart.overlayEnabled ? "OVERLAY" : "PRICE ONLY")}</div>
    <div>Overlay markers: E${overlayCounts.entries}/X${overlayCounts.exits}/S${overlayCounts.strategy}/M${overlayCounts.structure}/R${overlayCounts.risk}</div>`;

  legend.innerHTML = activeBigChart.overlayEnabled ? `
    <div><span style="color:#1dd1a1">—</span> Current price</div>
    ${activeBigChart.strategyFocus ? "" : `<div><span style="color:#6ea8ff">—</span> EMA20</div>
    <div><span style="color:#f0b34b">—</span> EMA50</div>`}
    <div><span style="color:#60a5fa">▭</span> Signal band (${collections.signalBand.length})</div>
    <div><span style="color:#18d392">▲</span> Entries (${collections.entries.length})</div>
    <div><span style="color:#ff4976">▼</span> Exits (${collections.exits.length})</div>
    <div><span style="color:#22c55e">▭</span> TP boxes (${collections.takeProfitBoxes.length})</div>
    <div><span style="color:#f43f5e">▭</span> SL boxes (${collections.stopLossBoxes.length})</div>
    <div><span style="color:#10b981">▭</span> Support zones (${collections.supportZones.length})</div>
    <div><span style="color:#ef4444">▭</span> Resistance zones (${collections.resistanceZones.length})</div>
    <div><span style="color:#f0b34b">◆</span> Strategy markers (${collections.strategyMarkers.length})</div>
    <div><span style="color:#d8b4fe">◆</span> Structure markers (${collections.structureMarkers.length})</div>
    <div><span style="color:#60a5fa">●</span> AI markers (${collections.aiMarkers.length})</div>
    <div><span style="color:#f97316">◆</span> Risk markers (${collections.riskMarkers.length})</div>` : `Overlay disabled`;

  toolbarInfo.innerText = activeBigChart.strategyFocus ? "Strategy focus · drag X · Shift+drag Y · wheel zoom" : "Backend truth chart · drag X · Shift+drag Y · wheel zoom";
}
function redrawActiveBigChart(){
  if(!activeBigChart) return;
  drawBigChartCanvas(document.getElementById("bigChartCanvas"));
  updateChartHud();
  applyPanelLayout();
  persistActiveChartState();
}
function scheduleActiveBigChartRedraw(){ requestAnimationFrame(redrawActiveBigChart); }
function zoomBigChart(factor){ if(!activeBigChart) return; const len=activeBigChart.fullSeries.length; const center=(activeBigChart.viewStart+activeBigChart.viewEnd)/2; let size=(activeBigChart.viewEnd-activeBigChart.viewStart)*factor; size=Math.max(20,Math.min(len,size)); activeBigChart.autoFollowLatest=false; activeBigChart.viewStart=center-size/2; activeBigChart.viewEnd=activeBigChart.viewStart+size; clampViewWindow(); scheduleActiveBigChartRedraw(); }
function zoomY(factor){ if(!activeBigChart) return; activeBigChart.yScale=Math.max(0.4,Math.min(4.0,(activeBigChart.yScale||1)*factor)); scheduleActiveBigChartRedraw(); }
function shiftTimeWindow(direction){ if(!activeBigChart) return; const size=activeBigChart.viewEnd-activeBigChart.viewStart; const step=Math.max(1,Math.round(size*0.25)); activeBigChart.autoFollowLatest=false; activeBigChart.viewStart += step*direction; activeBigChart.viewEnd += step*direction; clampViewWindow(); scheduleActiveBigChartRedraw(); }
function toggleAutoFollow(){ if(!activeBigChart) return; activeBigChart.autoFollowLatest=!activeBigChart.autoFollowLatest; scheduleActiveBigChartRedraw(); }
function resetBigChart(){
  if(!activeBigChart) return;
  const len=activeBigChart.fullSeries.length;
  const size=Math.min(len,96);
  activeBigChart.viewEnd=len;
  activeBigChart.viewStart=Math.max(0,len-size);
  activeBigChart.yScale=1;
  activeBigChart.yPan=0;
  activeBigChart.autoFollowLatest=true;
  activeBigChart.panelLayout={hud:computePanelDefaults("hud"),legend:computePanelDefaults("legend")};
  scheduleActiveBigChartRedraw();
}
function toggleOverlay(){ if(!activeBigChart) return; activeBigChart.overlayEnabled=!activeBigChart.overlayEnabled; scheduleActiveBigChartRedraw(); }
function toggleStrategy(){ if(!activeBigChart) return; activeBigChart.overlayEnabled=true; activeBigChart.strategyFocus=!activeBigChart.strategyFocus; scheduleActiveBigChartRedraw(); }
function attachBigChartInteractions(canvas){
  if(canvas.dataset.bound === "1") return;
  canvas.dataset.bound = "1";
  canvas.addEventListener("wheel",(e)=>{
    if(!activeBigChart) return;
    e.preventDefault();
    const rect=canvas.getBoundingClientRect();
    const nearPriceAxis = (e.clientX - rect.left) >= rect.width - 96;
    if(e.shiftKey || nearPriceAxis){ zoomY(e.deltaY < 0 ? 0.92 : 1.09); return; }
    zoomBigChart(e.deltaY < 0 ? 0.88 : 1.14);
  }, {passive:false});
  canvas.addEventListener("pointerdown",(e)=>{
    if(!activeBigChart) return;
    const stats = getVisiblePriceStats();
    const rect=canvas.getBoundingClientRect();
    const nearPriceAxis = (e.clientX - rect.left) >= rect.width - 96;
    activeBigChart.dragging=true;
    activeBigChart.dragMode=(e.shiftKey || nearPriceAxis) ? "xy" : "x";
    activeBigChart.dragStartX=e.clientX;
    activeBigChart.dragStartY=e.clientY;
    activeBigChart.dragStartStart=activeBigChart.viewStart;
    activeBigChart.dragStartEnd=activeBigChart.viewEnd;
    activeBigChart.dragStartScale=activeBigChart.yScale||1;
    activeBigChart.dragStartPan=activeBigChart.yPan||0;
    activeBigChart.dragPriceRange=stats.baseRange || (stats.max-stats.min) || 1;
    canvas.classList.add("dragging");
    canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener("pointermove",(e)=>{
    if(!activeBigChart) return;
    const rect=canvas.getBoundingClientRect();
    const leftPad=14,rightPad=96,topPad=24,bottomPad=42,plotW=Math.max(1,rect.width-leftPad-rightPad),plotH=Math.max(1,rect.height-topPad-bottomPad-80);
    const localX=e.clientX-rect.left-leftPad;
    const visibleSeries = getVisibleSeries();
    if(localX>=0 && localX<=plotW){
      activeBigChart.hoverIndex=Math.max(0,Math.min(visibleSeries.length-1, Math.round((localX/plotW)*Math.max(visibleSeries.length-1,0))));
    } else activeBigChart.hoverIndex=null;
    if(activeBigChart.dragging){
      const visible=activeBigChart.dragStartEnd-activeBigChart.dragStartStart;
      const deltaItems=((e.clientX-activeBigChart.dragStartX)/Math.max(plotW,1))*visible;
      activeBigChart.autoFollowLatest=false;
      activeBigChart.viewStart=activeBigChart.dragStartStart-deltaItems;
      activeBigChart.viewEnd=activeBigChart.dragStartEnd-deltaItems;
      if(activeBigChart.dragMode === "xy"){
        const deltaY=e.clientY-activeBigChart.dragStartY;
        activeBigChart.yPan=(activeBigChart.dragStartPan||0)+(deltaY/Math.max(plotH,1))*(activeBigChart.dragPriceRange||1)*(activeBigChart.dragStartScale||1);
      }
      clampViewWindow();
    }
    scheduleActiveBigChartRedraw();
  });
  function stopDrag(e){ if(!activeBigChart) return; activeBigChart.dragging=false; activeBigChart.dragMode=null; canvas.classList.remove("dragging"); try{ canvas.releasePointerCapture(e.pointerId); }catch(err){} persistActiveChartState(); }
  canvas.addEventListener("pointerup", stopDrag); canvas.addEventListener("pointercancel", stopDrag);
  canvas.addEventListener("pointerleave", ()=>{ if(!activeBigChart || activeBigChart.dragging) return; activeBigChart.hoverIndex=null; scheduleActiveBigChartRedraw(); });
}
function renderAiSourceBox(pair){
  const box=document.getElementById("aiSourceBox"); if(!box) return;
  const snap=pairSnapshot(pair); const ai=snap?.ai || {}; const readiness=snap?.readiness || {};
  const overlay = activeBigChart?.overlay || {};
  const collections = extractOverlayCollections(overlay);
  box.textContent=`signal: ${ai.signal || "—"}
confidence: ${formatPercent(ai.confidence,100)}
strategy: ${ai.strategy || "—"}
regime: ${ai.regime || "—"}
prediction: ${formatPercent(ai.prediction,1)}

overlay history:
- signal_band: ${collections.signalBand.length}
- entries: ${collections.entries.length}
- exits: ${collections.exits.length}
- support_zones: ${collections.supportZones.length}
- resistance_zones: ${collections.resistanceZones.length}
- strategy_markers: ${collections.strategyMarkers.length}
- ai_markers: ${collections.aiMarkers.length}
- risk_markers: ${collections.riskMarkers.length}

backend readiness:
- market_data_ready: ${readiness.market_data_ready ? "yes" : "no"}
- ai_ready: ${readiness.ai_ready ? "yes" : "no"}
- risk_ready: ${readiness.risk_ready ? "yes" : "no"}
- execution_ready: ${readiness.execution_ready ? "yes" : "no"}
- balance_synced: ${readiness.balance_synced ? "yes" : "no"}`;
}
async function createBigChart(pair){
  const pairSnap = pairSnapshot(pair);
  const chart = await fetchPairChart(pair, "1d", 180);
  const series = payloadCandles(chart);
  const saved = sanitizeChartState(loadChartState(pair), series.length);
  activeBigChart = {
    pair,
    snapshot: pairSnap,
    fullSeries: buildCandles(series),
    viewStart: 0,
    viewEnd: 0,
    overlay: chart?.overlay || null,
    meta: chart?.meta || null,
    overlayEnabled: true,
    strategyFocus: false,
    yScale: 1,
    yPan: 0,
    autoFollowLatest: true,
    hoverIndex: null,
    dragging: false,
    panelLayout: saved?.panelLayout || {hud:computePanelDefaults("hud"),legend:computePanelDefaults("legend")}
  };
  const len = activeBigChart.fullSeries.length;
  const size = Math.min(len, 96);
  activeBigChart.viewEnd = len;
  activeBigChart.viewStart = Math.max(0, len - size);
  if(saved){
    if(saved.viewStart != null) activeBigChart.viewStart = saved.viewStart;
    if(saved.viewEnd != null) activeBigChart.viewEnd = saved.viewEnd;
    activeBigChart.yScale = Number(saved.yScale ?? 1);
    activeBigChart.yPan = Number(saved.yPan ?? 0);
    activeBigChart.autoFollowLatest = saved.autoFollowLatest !== false;
    activeBigChart.overlayEnabled = saved.overlayEnabled !== false;
    activeBigChart.strategyFocus = saved.strategyFocus === true;
  }
  clampViewWindow();
  const canvas = document.getElementById("bigChartCanvas");
  attachBigChartInteractions(canvas);
  redrawActiveBigChart();
  renderAiSourceBox(pair);
  updateOrderTicketUi();
}
async function refreshActiveBigChartData(pair){
  if(!activeBigChart || activeBigChart.pair !== pair) return;
  const pairSnap = pairSnapshot(pair);
  const chart = await fetchPairChart(pair, "1d", 180);
  if(chart && !chart.__error){
    const series = payloadCandles(chart);
    activeBigChart.snapshot = pairSnap;
    activeBigChart.fullSeries = buildCandles(series);
    activeBigChart.overlay = chart?.overlay || null;
    activeBigChart.meta = chart?.meta || null;
    const len = activeBigChart.fullSeries.length;
    if(activeBigChart.autoFollowLatest){
      const size = Math.min(len, Math.max(20, activeBigChart.windowSize || 96));
      activeBigChart.viewEnd = len;
      activeBigChart.viewStart = Math.max(0, len - size);
    }else{
      activeBigChart.viewEnd = Number.isFinite(Number(activeBigChart.viewEnd)) ? Math.min(activeBigChart.viewEnd, len) : len;
      activeBigChart.viewStart = Number.isFinite(Number(activeBigChart.viewStart)) ? Math.max(0, Math.min(activeBigChart.viewStart, activeBigChart.viewEnd)) : Math.max(0, len - Math.min(len, 96));
    }
    clampViewWindow();
  }else{
    activeBigChart.snapshot = pairSnap;
  }
  redrawActiveBigChart();
  renderAiSourceBox(pair);
  updateOrderTicketUi();
}
function openFullscreen(pair){
  const snap = pairSnapshot(pair) || {};
  activeOrderTicket = defaultOrderTicket(pair);
  const fs = document.createElement("div");
  fs.className = "fullscreen";
  fs.innerHTML = `
  <div class="tradePanel">
    <h3>${pair}</h3>
    <div class="tradeLabel">Capital mode</div>
    <select id="capitalModeInput">
      <option value="fiat" ${snap?.capital?.mode === "fiat" ? "selected" : ""}>Fiat</option>
      <option value="crypto" ${snap?.capital?.mode === "crypto" ? "selected" : ""}>Crypto</option>
    </select>
    <div class="tradeLabel">Capital amount</div>
    <input id="capitalValueInput" type="number" min="0" step="any" value="${Number(snap?.capital?.value || 0)}">
    <button onclick="saveCapital('${pair}')">Save capital</button>
    <div id="capitalStatus" class="capitalStatus muted">Current: ${snap?.capital?.mode || "—"} ${formatNumber(snap?.capital?.value)}</div>
    <div class="tradeLabel">Actions</div>
    <button type="button" onclick="setOrderAction('BUY')">BUY</button>
    <button type="button" onclick="setOrderAction('SELL')">SELL</button>
    <button type="button" onclick="setOrderAction('LIMIT')">LIMIT</button>
    <button type="button" onclick="setOrderAction('MARKET')">MARKET</button>
    <button type="button" onclick="setOrderAction('STOP LOSS')">STOP LOSS</button>
    <button type="button" onclick="setOrderAction('TAKE PROFIT')">TAKE PROFIT</button>
    <button id="aiStrategyBtn" class="strategyBtn" type="button" onclick="toggleStrategy()">AI STRATEGY</button>
    <button class="danger" onclick="event.stopPropagation(); killPair('${pair}')">KILL</button>
    <button onclick="closeFullscreen()">Close</button>
    <div class="orderTicket">
      <div class="orderTicketTitle">Order ticket</div>
      <div class="orderMeta"><div>Side: <span id="orderSide">BUY</span></div><div>Type: <span id="orderType">MARKET</span></div></div>
      <input id="orderAmount" type="number" min="0" step="any" placeholder="Amount" oninput="syncOrderTicketFromInputs()">
      <input id="orderPrice" type="number" min="0" step="any" placeholder="Limit price" oninput="syncOrderTicketFromInputs()">
      <input id="orderStopLoss" type="number" min="0" step="any" placeholder="Stop loss level" oninput="syncOrderTicketFromInputs()">
      <input id="orderTakeProfit" type="number" min="0" step="any" placeholder="Take profit level" oninput="syncOrderTicketFromInputs()">
      <button type="button" onclick="submitOrderTicket()">Apply ticket</button>
      <div id="orderTicketStatus" class="orderStatus"></div>
    </div>
    <div class="tradeLabel">AI input block</div>
    <div id="aiSourceBox" class="monoBox">Loading AI context…</div>
  </div>
  <div class="chartArea" id="chartArea">
    <div class="chartToolbar">
      <button class="overlayBtn" id="overlayBtn" onclick="toggleOverlay()">AI Overlay</button>
      <button onclick="shiftTimeWindow(-0.25)">◀</button>
      <button onclick="shiftTimeWindow(0.25)">▶</button>
      <button onclick="zoomBigChart(0.8)">H+</button>
      <button onclick="zoomBigChart(1.25)">H-</button>
      <button onclick="zoomY(1.18)">V+</button>
      <button onclick="zoomY(0.85)">V-</button>
      <button id="autoFollowBtn" onclick="toggleAutoFollow()">Today</button>
      <button onclick="resetBigChart()">Reset</button>
      <div class="chartInfo" id="chartToolbarInfo">Backend truth chart</div>
    </div>
    <div id="bigChart" class="chartBox">
      <div id="chartHudPanel" class="floatPanel" style="left:16px; top:14px;"><div class="panelHeader"><div class="panelHeaderLeft">Info</div><div class="panelHeaderBtns"><button type="button" data-panel-action="min">–</button><button type="button" data-panel-action="max">▢</button><button type="button" data-panel-action="reset">↺</button></div></div><div id="chartHudBody" class="panelBody"></div></div>
      <div id="chartLegendPanel" class="floatPanel" style="right:16px; top:14px;"><div class="panelHeader"><div class="panelHeaderLeft">Overlay</div><div class="panelHeaderBtns"><button type="button" data-panel-action="min">–</button><button type="button" data-panel-action="max">▢</button><button type="button" data-panel-action="reset">↺</button></div></div><div id="chartLegendBody" class="panelBody"></div></div>
      <div id="crosshairLabel" class="crosshairLabel"></div>
      <div class="chartHint">Drag = horizontální pan. Shift + drag nebo price axis = X/Y pan. Wheel = H zoom. Shift + wheel = V zoom.</div>
      <canvas id="bigChartCanvas" class="bigCanvas"></canvas>
    </div>
  </div>`;
  document.body.appendChild(fs);
  attachFloatingPanelInteractions("hud");
  attachFloatingPanelInteractions("legend");
  createBigChart(pair);
  window.addEventListener("resize", redrawActiveBigChart);
}
function closeFullscreen(){
  persistActiveChartState();
  document.querySelector(".fullscreen")?.remove();
  activeBigChart = null;
  activeOrderTicket = null;
  window.removeEventListener("resize", redrawActiveBigChart);
}
async function killPair(pair){ const res=await safeFetch(`/api/pair/${encodeURIComponent(pair)}/kill`,{method:"POST"}); if(res && !res.__error){ await refresh(); } else setFeedback(res?.detail || "Kill failed", true); }

async function refresh(){
  if(refreshInFlight){ refreshQueued = true; return refreshInFlight; }
  refreshInFlight = (async ()=>{
    await Promise.allSettled([
      safeRun("fetchDashboardSnapshot", fetchDashboardSnapshot),
      safeRun("loadControlState", loadControlState),
      safeRun("loadTrades", loadTrades),
      safeRun("loadSignals", loadSignals),
      safeRun("refreshSections", refreshSections)
    ]);
    await safeRun("loadAllMiniCharts", loadAllMiniCharts);
    if(activeBigChart){
      const currentPair = activeBigChart.pair;
      await safeRun("refreshActiveBigChartData", ()=>refreshActiveBigChartData(currentPair));
    }
  })();
  try{
    await refreshInFlight;
  }finally{
    refreshInFlight = null;
    if(refreshQueued){ refreshQueued = false; setTimeout(()=>refresh(), 0); }
  }
}

setInterval(()=>{
  const d = new Date();
  document.getElementById("clock").innerText = d.toLocaleDateString() + " " + d.toLocaleTimeString();
},1000);

setModeUi("paper");
setArmUi(false);
setRobotUi("STOPPED");
createPanels();
bindRobotButtons();
refresh();
setInterval(refresh, 15000);
