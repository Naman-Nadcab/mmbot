const state = {
  apiBase: localStorage.getItem('ops.apiBase') || '/api',
  token: localStorage.getItem('ops.token') || '',
  wsUrl: localStorage.getItem('ops.wsUrl') || defaultWsUrl(),
  socket: null,
  lastEvents: [],
  history: { pnl: [], inventory: [], marketMessages: [] },
  pagination: { orders: { page: 0, size: 50 }, trades: { page: 0, size: 50 } },
  data: {
    positions: [], orders: [], trades: [], riskEvents: [], reconciliation: [],
    engines: {}, exchanges: {}, infrastructure: {}, pnl: null, inventory: null, mode: null
  }
};

const $ = (id) => document.getElementById(id);
const emptyRow = (cols) => `<tr><td colspan="${cols}" class="empty">No records returned by backend</td></tr>`;

function defaultWsUrl() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}/api/ws/operations`;
}

function headers() {
  const h = { 'Accept': 'application/json' };
  if (state.token) h.Authorization = `Bearer ${state.token}`;
  return h;
}

function setPill(id, text, status = 'neutral') {
  const el = $(id);
  el.textContent = text;
  el.className = `pill ${status}`;
}

function logEvent(message, payload) {
  const line = `[${new Date().toISOString()}] ${message}${payload ? ' ' + JSON.stringify(payload) : ''}`;
  state.lastEvents.unshift(line);
  state.lastEvents = state.lastEvents.slice(0, 120);
  $('event-log').textContent = state.lastEvents.join('\n');
}

async function request(path) {
  const response = await fetch(`${state.apiBase}${path}`, { headers: headers() });
  if (!response.ok) throw new Error(`${path} ${response.status}`);
  return response.json();
}

async function refreshRest() {
  try {
    const health = await request('/health');
    state.data.infrastructure.api = health.status;
    state.data.infrastructure.database = health.dependencies?.database || 'unknown';
    state.data.infrastructure.redis = health.dependencies?.redis || 'unknown';
    setPill('api-status', `API ${health.status}`, health.status === 'ok' ? 'ok' : 'warn');
    renderInfrastructure();
  } catch (error) {
    setPill('api-status', 'API unavailable', 'bad');
    state.data.infrastructure.api = error.message;
    renderInfrastructure();
  }

  try {
    const version = await request('/version');
    state.data.infrastructure.version = version.version;
    renderInfrastructure();
  } catch (error) {
    state.data.infrastructure.version = 'unavailable';
  }

  try {
    const ready = await request('/ready');
    state.data.infrastructure.ready = ready.status;
    renderInfrastructure();
  } catch (error) {
    state.data.infrastructure.ready = error.message;
  }

  await refreshOperations();

  if (state.token) {
    try {
      const config = await request('/admin/config');
      state.data.mode = config?.exchange ? 'paper/canary/live configured' : state.data.mode;
      renderMode(config);
    } catch (error) {
      logEvent('admin_config_unavailable', { error: error.message });
    }

    try {
      const exchanges = await request('/admin/exchanges/capabilities');
      for (const [name, details] of Object.entries(exchanges)) {
        state.data.exchanges[name] = { status: 'configured', detail: details.websocket_url || details.rest_base_url || 'configured' };
      }
      renderExchanges();
    } catch (error) {
      logEvent('exchange_capabilities_unavailable', { error: error.message });
    }
  }
}

async function refreshOperations() {
  const calls = [
    ['engines', '/operations/engines'],
    ['infrastructureDetails', '/operations/infrastructure'],
    ['orders', '/operations/orders?limit=500'],
    ['trades', '/operations/trades?limit=500'],
    ['positions', '/operations/positions'],
    ['inventory', '/operations/inventory'],
    ['pnl', '/operations/pnl'],
    ['riskEvents', '/operations/risk-events'],
    ['reconciliationPayload', '/operations/reconciliation']
  ];
  for (const [key, path] of calls) {
    try {
      const payload = await request(path);
      if (key === 'engines') state.data.engines = payload.engines || {};
      else if (key === 'infrastructureDetails') state.data.infrastructure = { ...state.data.infrastructure, ...payload };
      else if (key === 'orders') state.data.orders = payload.items || [];
      else if (key === 'trades') state.data.trades = payload.items || [];
      else if (key === 'positions') state.data.positions = payload.items || [];
      else if (key === 'inventory') state.data.inventory = payload;
      else if (key === 'pnl') state.data.pnl = payload;
      else if (key === 'riskEvents') state.data.riskEvents = payload.items || [];
      else if (key === 'reconciliationPayload') {
        state.data.reconciliation = payload.mismatches || [];
        state.data.reconciliationStatus = `${payload.status} (${payload.runs} runs)`;
      }
    } catch (error) {
      logEvent('operations_endpoint_unavailable', { endpoint: path, error: error.message });
    }
  }
  sampleHistory();
  renderAll();
}

function connectWebSocket() {
  disconnectWebSocket();
  state.wsUrl = $('ws-url').value.trim();
  localStorage.setItem('ops.wsUrl', state.wsUrl);
  if (!state.wsUrl) return;
  try {
    const socket = new WebSocket(state.wsUrl);
    state.socket = socket;
    setPill('ws-status', 'WebSocket connecting', 'warn');
    socket.onopen = () => { setPill('ws-status', 'WebSocket connected', 'ok'); logEvent('websocket_connected', { url: state.wsUrl }); };
    socket.onclose = () => { setPill('ws-status', 'WebSocket disconnected', 'warn'); logEvent('websocket_disconnected'); };
    socket.onerror = () => { setPill('ws-status', 'WebSocket error', 'bad'); logEvent('websocket_error'); };
    socket.onmessage = (event) => handleStreamMessage(event.data);
  } catch (error) {
    setPill('ws-status', 'WebSocket error', 'bad');
    logEvent('websocket_connect_failed', { error: error.message });
  }
}

function disconnectWebSocket() {
  if (state.socket) state.socket.close();
  state.socket = null;
}

function handleStreamMessage(raw) {
  let message;
  try { message = JSON.parse(raw); } catch { logEvent('stream_non_json_message', { raw }); return; }
  const type = message.type || message.event_type || message.event || 'unknown';
  const payload = message.payload || message.data || message;
  logEvent(type, payload);
  switch (type) {
    case 'pnl': state.data.pnl = payload; break;
    case 'positions': state.data.positions = payload.items || payload.positions || []; break;
    case 'inventory': state.data.inventory = payload; break;
    case 'orders': state.data.orders = payload.items || payload.orders || []; break;
    case 'trades': state.data.trades = payload.items || payload.trades || []; break;
    case 'risk_events': state.data.riskEvents = payload.items || payload.events || []; break;
    case 'engine_health': state.data.engines = payload.engines || payload; sampleThroughput(payload); break;
    case 'exchange_connectivity': state.data.exchanges = payload.exchanges || payload; break;
    case 'infrastructure': state.data.infrastructure = { ...state.data.infrastructure, ...payload }; break;
    case 'reconciliation': state.data.reconciliation = payload.mismatches || payload.items || []; state.data.reconciliationStatus = payload.status; break;
    case 'mode': state.data.mode = payload.mode || payload; break;
    default: break;
  }
  renderAll();
}

function renderAll() {
  renderPnl(); renderPositions(); renderOrders(); renderTrades(); renderRisk(); renderEngines();
  renderExchanges(); renderInfrastructure(); renderReconciliation(); renderMode(); renderInventory(); renderCharts(); renderLatencyAndThroughput();
}

function renderPnl() {
  const pnl = state.data.pnl;
  $('pnl-value').textContent = pnl ? formatMoney(pnl.total ?? pnl.unrealized ?? pnl.realized) : formatMoney(0);
  $('pnl-subtitle').textContent = pnl ? `realized ${formatMoney(pnl.realized)} / unrealized ${formatMoney(pnl.unrealized)}` : 'realized / unrealized';
}

function renderInventory() {
  const inv = state.data.inventory;
  $('inventory-exposure').textContent = inv ? formatMoney(inv.exposure_notional ?? inv.total_notional) : formatMoney(0);
  $('inventory-subtitle').textContent = inv ? `${(inv.items || []).length} inventory snapshots` : '0 inventory snapshots';
}

function renderMode(config) {
  const mode = state.data.mode || config?.mode || config?.runtime?.mode || 'paper/canary/live unknown';
  const text = typeof mode === 'string' ? mode : JSON.stringify(mode);
  setPill('mode-pill', text, text.includes('live') ? 'bad' : text.includes('paper') ? 'ok' : 'neutral');
}

function renderInfrastructure() {
  const infra = state.data.infrastructure;
  $('infra-health').innerHTML = [
    stackItem('API', infra.api || 'checking'),
    stackItem('Database', infra.database || 'checking'),
    stackItem('Redis', infra.redis || 'checking'),
    stackItem('PostgreSQL', infra.postgres || infra.database || 'checking'),
    stackItem('Readiness', infra.ready || 'checking'),
    stackItem('Version', infra.version || 'unknown')
  ].join('');
}

function renderEngines() {
  const engines = state.data.engines;
  const entries = Object.entries(engines || {});
  $('engine-health').innerHTML = entries.length ? entries.map(([name, value]) => stackItem(name, value?.status || value?.health_status || JSON.stringify(value))).join('') : stackItem('Engine Health', 'No engine health records returned');
  $('engine-updated').textContent = entries.length ? 'Backend state' : 'No engine health records';
}

function renderExchanges() {
  const entries = Object.entries(state.data.exchanges || {});
  $('exchange-connectivity').innerHTML = entries.length ? entries.map(([name, value]) => stackItem(name, value.status || value.detail || JSON.stringify(value))).join('') : stackItem('Exchanges', 'No exchange state returned');
}

function renderPositions() {
  $('positions-body').innerHTML = rows(state.data.positions, 5, (p) => `<tr><td>${esc(p.symbol)}</td><td>${esc(p.asset)}</td><td>${formatNumber(p.quantity)}</td><td>${formatMoney(p.notional)}</td><td>${formatMoney(p.pnl ?? p.unrealized_pnl)}</td></tr>`);
}
function renderOrders() {
  $('open-orders-count').textContent = Array.isArray(state.data.orders) ? String(state.data.orders.length) : '0';
  const pageItems = pageSlice(state.data.orders, state.pagination.orders);
  $('orders-body').innerHTML = rows(pageItems, 6, (o) => `<tr><td>${esc(o.client_order_id || o.id)}</td><td>${esc(o.symbol)}</td><td>${esc(o.side)}</td><td>${esc(o.status)}</td><td>${formatNumber(o.price)}</td><td>${formatNumber(o.quantity)}</td></tr>`);
  $('orders-page').textContent = `${state.pagination.orders.page + 1}/${pageCount(state.data.orders, state.pagination.orders)}`;
}
function renderTrades() {
  const pageItems = pageSlice(state.data.trades, state.pagination.trades);
  $('trades-body').innerHTML = rows(pageItems, 6, (t) => `<tr><td>${esc(t.trade_id || t.id)}</td><td>${esc(t.symbol)}</td><td>${esc(t.side)}</td><td>${formatNumber(t.price)}</td><td>${formatNumber(t.quantity)}</td><td>${formatNumber(t.fee)}</td></tr>`);
  $('trades-page').textContent = `${state.pagination.trades.page + 1}/${pageCount(state.data.trades, state.pagination.trades)}`;
}
function renderRisk() {
  const risk = state.data.riskEvents || [];
  $('risk-count').textContent = String(risk.length);
  $('risk-body').innerHTML = rows(risk, 4, (r) => `<tr><td>${esc(r.severity)}</td><td>${esc(r.event_type || r.type)}</td><td>${esc(r.message)}</td><td>${esc(r.occurred_at || r.time)}</td></tr>`);
}
function renderReconciliation() {
  setPill('reconciliation-status', state.data.reconciliationStatus || 'No reconciliation state', String(state.data.reconciliationStatus || '').includes('ok') ? 'ok' : 'neutral');
  $('reconciliation-body').innerHTML = rows(state.data.reconciliation, 4, (r) => `<tr><td>${esc(r.category)}</td><td>${esc(r.key)}</td><td>${esc(r.severity)}</td><td>${esc(r.message)}</td></tr>`);
}

function rows(items, cols, renderer) { return Array.isArray(items) && items.length ? items.map(renderer).join('') : emptyRow(cols); }
function pageSlice(items, page) { const list = Array.isArray(items) ? items : []; const start = page.page * page.size; return list.slice(start, start + page.size); }
function pageCount(items, page) { return Math.max(1, Math.ceil((Array.isArray(items) ? items.length : 0) / page.size)); }
function changePage(kind, direction) { const page = state.pagination[kind]; page.page = Math.max(0, Math.min(page.page + direction, pageCount(state.data[kind], page) - 1)); renderAll(); }
function stackItem(name, status) { const cls = String(status).includes('healthy') || String(status).includes('ok') || String(status).includes('configured') ? 'ok' : String(status).includes('unhealthy') || String(status).includes('failed') ? 'bad' : 'neutral'; return `<div class="stack-item"><b>${esc(name)}</b><span class="pill ${cls}">${esc(status)}</span></div>`; }
function formatMoney(value) { if (value === undefined || value === null || value === '') return '$0.00'; const n = Number(value); return Number.isFinite(n) ? n.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }) : esc(value); }
function formatNumber(value) { if (value === undefined || value === null || value === '') return ''; const n = Number(value); return Number.isFinite(n) ? n.toLocaleString(undefined, { maximumFractionDigits: 8 }) : esc(value); }
function esc(value) { return String(value ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
function sampleHistory() {
  if (state.data.pnl) pushSample(state.history.pnl, Number(state.data.pnl.total || 0));
  if (state.data.inventory) pushSample(state.history.inventory, Number(state.data.inventory.exposure_notional || state.data.inventory.total_notional || 0));
}
function sampleThroughput(payload) {
  const engines = payload.engines || payload;
  const dataEngine = engines['market-data-engine'] || {};
  const counters = dataEngine.runtime?.metrics?.counters || {};
  pushSample(state.history.marketMessages, Number(counters['market_data.messages'] || 0));
}
function pushSample(series, value) { if (Number.isFinite(value)) { series.push(value); while (series.length > 40) series.shift(); } }
function renderCharts() { renderBarChart('pnl-chart', state.history.pnl, ''); renderBarChart('inventory-chart', state.history.inventory, 'inventory'); }
function renderBarChart(id, values, cls) {
  const el = $(id);
  const max = Math.max(...values.map((v) => Math.abs(v)), 1);
  el.innerHTML = values.length ? values.map((v) => `<div class="bar ${cls}" title="${formatNumber(v)}" style="height:${Math.max(4, Math.abs(v) / max * 100)}%"></div>`).join('') : '<div class="empty">No chart samples yet</div>';
}
function renderLatencyAndThroughput() {
  $('redis-latency').textContent = `${formatNumber(state.data.infrastructure.redis_latency_ms || 0)} ms`;
  $('db-latency').textContent = `${formatNumber(state.data.infrastructure.database_latency_ms || 0)} ms`;
  const engines = state.data.engines || {};
  const dataEngine = engines['market-data-engine'] || {};
  const runtime = dataEngine.runtime || {};
  const lastTimes = Object.values(runtime.last_message_timestamp || {});
  const latest = lastTimes.length ? Math.max(...lastTimes.map((value) => Date.parse(value)).filter(Number.isFinite)) : 0;
  $('market-latency').textContent = latest ? `${formatNumber(Date.now() - latest)} ms` : '0 ms';
  const series = state.history.marketMessages;
  const throughput = series.length > 1 ? Math.max(0, series[series.length - 1] - series[series.length - 2]) / 10 : 0;
  $('message-throughput').textContent = `${formatNumber(throughput)}/s`;
}

function init() {
  $('api-base').value = state.apiBase;
  $('ws-url').value = state.wsUrl;
  $('bearer-token').value = state.token;
  $('save-token').addEventListener('click', () => { state.apiBase = $('api-base').value.trim() || '/api'; state.token = $('bearer-token').value.trim(); localStorage.setItem('ops.apiBase', state.apiBase); localStorage.setItem('ops.token', state.token); refreshRest(); });
  $('refresh-now').addEventListener('click', refreshRest);
  $('connect-ws').addEventListener('click', connectWebSocket);
  $('disconnect-ws').addEventListener('click', disconnectWebSocket);
  $('orders-prev').addEventListener('click', () => changePage('orders', -1));
  $('orders-next').addEventListener('click', () => changePage('orders', 1));
  $('trades-prev').addEventListener('click', () => changePage('trades', -1));
  $('trades-next').addEventListener('click', () => changePage('trades', 1));
  $('clear-log').addEventListener('click', () => { state.lastEvents = []; $('event-log').textContent = ''; });
  $('kill-switch').addEventListener('click', () => { $('kill-output').textContent = 'Kill switch endpoint is not available from existing backend APIs.'; });
  renderAll(); refreshRest(); connectWebSocket(); setInterval(refreshRest, 10000);
}

document.addEventListener('DOMContentLoaded', init);
