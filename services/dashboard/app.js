const state = {
  apiBase: normalizeApiBase(localStorage.getItem('ops.apiBase') || '/api'),
  token: normalizeToken(localStorage.getItem('ops.token') || ''),
  wsUrl: localStorage.getItem('ops.wsUrl') || defaultWsUrl(),
  socket: null,
  wsReconnectTimer: null,
  manualDisconnect: false,
  lastEvents: [],
  history: { pnl: [], inventory: [], marketMessages: [] },
  pagination: { orders: { page: 0, size: 50 }, trades: { page: 0, size: 50 } },
  data: {
    positions: [], orders: [], trades: [], riskEvents: [], reconciliation: [],
    engines: {}, exchanges: {}, infrastructure: {}, pnl: null, inventory: null, mode: null
  }
};

window.__dashboardRequestEvidence = window.__dashboardRequestEvidence || [];
window.__dashboardFirst401 = window.__dashboardFirst401 || null;

const $ = (id) => document.getElementById(id);
const emptyRow = (cols) => `<tr><td colspan="${cols}" class="empty">No records returned by backend</td></tr>`;

function defaultWsUrl() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}/api/ws/operations`;
}

function normalizeApiBase(value) {
  const base = String(value || '/api').trim() || '/api';
  return base.length > 1 ? base.replace(/\/+$/g, '') : base;
}

function syncStoredAuthState() {
  state.apiBase = normalizeApiBase(localStorage.getItem('ops.apiBase') || state.apiBase || '/api');
  const storedToken = normalizeToken(localStorage.getItem('ops.token') || state.token || '');
  if (storedToken !== state.token) {
    state.token = storedToken;
    const tokenInput = $('bearer-token');
    if (tokenInput) tokenInput.value = storedToken;
  }
}

function headers() {
  syncStoredAuthState();
  const h = { 'Accept': 'application/json' };
  if (state.token) {
    console.info('jwt_auth_diagnostics', tokenDiagnostics('rest_authorization_header', state.token));
    h.Authorization = `Bearer ${state.token}`;
  }
  return h;
}

function normalizeToken(value) {
  return String(value || '')
    .trim()
    .replace(/^Bearer\s+/i, '')
    .replace(/^['"]|['"]$/g, '')
    .replace(/\s+/g, '')
    .trim();
}

function tokenDiagnostics(stage, token) {
  const value = String(token || '');
  return {
    stage,
    length: value.length,
    segments: value ? value.split('.').length : 0,
    hasWhitespace: /\s/.test(value),
    startsWithBearer: /^Bearer\s+/i.test(value),
    hasWrappingQuotes: /^['"].*['"]$/.test(value),
    prefix: value.slice(0, 8),
    suffix: value.slice(-8)
  };
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

function recordRequestEvidence(entry) {
  const evidence = {
    timestamp: new Date().toISOString(),
    ...entry
  };
  window.__dashboardRequestEvidence.push(evidence);
  if (evidence.status === 401 && !window.__dashboardFirst401) {
    window.__dashboardFirst401 = evidence;
  }
  console.info('dashboard_request_evidence', evidence);
}

async function request(path) {
  syncStoredAuthState();
  const url = `${state.apiBase}${path}`;
  const requestHeaders = headers();
  const authAttached = Boolean(requestHeaders.Authorization);
  try {
    const response = await fetch(url, { headers: requestHeaders });
    recordRequestEvidence({
      url,
      status: response.status,
      authAttached,
      usedRequest: true,
      bypassedRequest: false,
      functionName: 'request',
      sourceLine: 'services/dashboard/app.js:request'
    });
    if (!response.ok) throw new Error(`${path} ${response.status}`);
    return response.json();
  } catch (error) {
    if (!window.__dashboardRequestEvidence.some((item) => item.url === url && item.status !== undefined)) {
      recordRequestEvidence({
        url,
        status: null,
        authAttached,
        usedRequest: true,
        bypassedRequest: false,
        functionName: 'request',
        sourceLine: 'services/dashboard/app.js:request',
        error: error.message
      });
    }
    throw error;
  }
}

async function refreshRest() {
  syncStoredAuthState();
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
      renderKillSwitch(await request('/operations/kill-switch'));
    } catch (error) {
      logEvent('kill_switch_status_unavailable', { error: error.message });
    }
  }
}

async function refreshOperations() {
  syncStoredAuthState();
  if (!state.token) {
    setPill('ws-status', 'Token required', 'warn');
    logEvent('operations_auth_required', { message: 'Enter and save a JWT bearer token to load protected operations data.' });
    return;
  }
  const calls = [
    ['engines', '/operations/engines'],
    ['infrastructureDetails', '/operations/infrastructure'],
    ['exchanges', '/operations/exchanges'],
    ['orders', '/operations/orders?limit=500'],
    ['trades', '/operations/trades?limit=500'],
    ['positions', '/operations/positions'],
    ['inventory', '/operations/inventory'],
    ['pnl', '/operations/pnl'],
    ['riskEvents', '/operations/risk-events'],
    ['reconciliationPayload', '/operations/reconciliation'],
    ['canaryLimits', '/operations/canary-limits']
  ];
  for (const [key, path] of calls) {
    try {
      const payload = await request(path);
      if (key === 'engines') {
        state.data.engines = payload.engines || {};
        sampleThroughput(payload);
      }
      else if (key === 'infrastructureDetails') state.data.infrastructure = { ...state.data.infrastructure, ...payload };
      else if (key === 'exchanges') state.data.exchanges = payload.exchanges || {};
      else if (key === 'orders') state.data.orders = payload.items || [];
      else if (key === 'trades') state.data.trades = payload.items || [];
      else if (key === 'positions') state.data.positions = payload.items || [];
      else if (key === 'inventory') state.data.inventory = payload;
      else if (key === 'pnl') state.data.pnl = payload;
      else if (key === 'riskEvents') state.data.riskEvents = payload.items || [];
      else if (key === 'canaryLimits') renderCanaryLimits(payload);
      else if (key === 'reconciliationPayload') {
        state.data.reconciliation = reconciliationRows(payload);
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
  state.manualDisconnect = false;
  syncStoredAuthState();
  if (!state.token) {
    setPill('ws-status', 'Token required', 'warn');
    logEvent('websocket_auth_required', { message: 'Enter and save a JWT bearer token before opening the operations stream.' });
    return;
  }
  state.wsUrl = $('ws-url').value.trim();
  localStorage.setItem('ops.wsUrl', state.wsUrl);
  if (!state.wsUrl) return;
  try {
    const url = new URL(state.wsUrl, window.location.href);
    if (state.token) {
      console.info('jwt_auth_diagnostics', tokenDiagnostics('websocket_query_token', state.token));
      url.searchParams.set('token', state.token);
    }
    recordRequestEvidence({
      url: url.toString(),
      status: 'websocket_opening',
      authAttached: Boolean(state.token),
      usedRequest: false,
      bypassedRequest: true,
      functionName: 'connectWebSocket',
      sourceLine: 'services/dashboard/app.js:connectWebSocket'
    });
    const socket = new WebSocket(url.toString());
    state.socket = socket;
    setPill('ws-status', 'WebSocket connecting', 'warn');
    socket.onopen = () => { setPill('ws-status', 'WebSocket connected', 'ok'); logEvent('websocket_connected', { url: state.wsUrl }); };
    socket.onclose = () => {
      setPill('ws-status', 'WebSocket disconnected', 'warn');
      logEvent('websocket_disconnected');
      if (!state.manualDisconnect) scheduleWebSocketReconnect();
    };
    socket.onerror = () => { setPill('ws-status', 'WebSocket error', 'bad'); logEvent('websocket_error'); };
    socket.onmessage = (event) => handleStreamMessage(event.data);
  } catch (error) {
    setPill('ws-status', 'WebSocket error', 'bad');
    logEvent('websocket_connect_failed', { error: error.message });
  }
}

function disconnectWebSocket() {
  state.manualDisconnect = true;
  if (state.wsReconnectTimer) clearTimeout(state.wsReconnectTimer);
  state.wsReconnectTimer = null;
  if (state.socket) state.socket.close();
  state.socket = null;
}

function scheduleWebSocketReconnect() {
  if (state.wsReconnectTimer) return;
  state.wsReconnectTimer = setTimeout(() => {
    state.wsReconnectTimer = null;
    connectWebSocket();
  }, 3000);
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
    case 'reconciliation': state.data.reconciliation = reconciliationRows(payload); state.data.reconciliationStatus = `${payload.status} (${payload.runs ?? 0} runs)`; break;
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

function renderKillSwitch(payload) {
  const active = Boolean(payload?.active);
  setPill('kill-status', active ? `ACTIVE: ${payload.reason || 'unknown'}` : 'Inactive', active ? 'bad' : 'ok');
  $('kill-switch').disabled = true;
  $('kill-output').textContent = JSON.stringify(payload || { active: false }, null, 2);
}

function renderCanaryLimits(payload) {
  const current = $('kill-output').textContent || '';
  $('kill-output').textContent = `${current}\nCanary limits: ${JSON.stringify(payload || {}, null, 2)}`.trim();
}

function rows(items, cols, renderer) { return Array.isArray(items) && items.length ? items.map(renderer).join('') : emptyRow(cols); }
function pageSlice(items, page) { const list = Array.isArray(items) ? items : []; const start = page.page * page.size; return list.slice(start, start + page.size); }
function pageCount(items, page) { return Math.max(1, Math.ceil((Array.isArray(items) ? items.length : 0) / page.size)); }
function changePage(kind, direction) { const page = state.pagination[kind]; page.page = Math.max(0, Math.min(page.page + direction, pageCount(state.data[kind], page) - 1)); renderAll(); }
function stackItem(name, status) { const cls = String(status).includes('healthy') || String(status).includes('ok') || String(status).includes('configured') ? 'ok' : String(status).includes('unhealthy') || String(status).includes('failed') ? 'bad' : 'neutral'; return `<div class="stack-item"><b>${esc(name)}</b><span class="pill ${cls}">${esc(status)}</span></div>`; }
function formatMoney(value) { if (value === undefined || value === null || value === '') return '$0.00'; const n = Number(value); return Number.isFinite(n) ? n.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }) : esc(value); }
function formatNumber(value) { if (value === undefined || value === null || value === '') return ''; const n = Number(value); return Number.isFinite(n) ? n.toLocaleString(undefined, { maximumFractionDigits: 8 }) : esc(value); }
function formatDuration(seconds) { const total = Math.max(0, Math.floor(Number(seconds) || 0)); const hours = Math.floor(total / 3600); const minutes = Math.floor((total % 3600) / 60); const secs = total % 60; return hours ? `${hours}h ${minutes}m` : minutes ? `${minutes}m ${secs}s` : `${secs}s`; }
function esc(value) { return String(value ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
function sampleHistory() {
  if (state.data.pnl) pushSample(state.history.pnl, Number(state.data.pnl.total || 0));
  if (state.data.inventory) pushSample(state.history.inventory, Number(state.data.inventory.exposure_notional || state.data.inventory.total_notional || 0));
}
function sampleThroughput(payload) {
  const engines = payload.engines || payload;
  const dataEngine = engines['market-data-engine'] || {};
  const counters = dataEngine.runtime?.metrics?.counters || {};
  pushSample(state.history.marketMessages, { value: Number(counters['market_data.messages'] || 0), time: Date.now() });
}
function pushSample(series, value) { if (typeof value === 'object' || Number.isFinite(value)) { series.push(value); while (series.length > 40) series.shift(); } }
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
  const previous = series[series.length - 2];
  const current = series[series.length - 1];
  const throughput = previous && current ? Math.max(0, current.value - previous.value) / Math.max(1, (current.time - previous.time) / 1000) : 0;
  $('message-throughput').textContent = `${formatNumber(throughput)}/s`;
  const uptimes = Object.values(engines).map((engine) => Number(engine?.uptime_seconds || 0)).filter(Number.isFinite);
  $('engine-uptime').textContent = formatDuration(Math.max(0, ...uptimes));
}

function reconciliationRows(payload) {
  const mismatches = payload.mismatches || payload.items || [];
  if (mismatches.length) return mismatches;
  return [{
    category: 'summary',
    key: `runs=${payload.runs ?? 0}`,
    severity: payload.status || 'unknown',
    message: `mismatches=${payload.mismatch_count ?? 0}, alerts=${payload.alert_count ?? 0}`
  }];
}

function init() {
  syncStoredAuthState();
  $('api-base').value = state.apiBase;
  $('ws-url').value = state.wsUrl;
  $('bearer-token').value = state.token;
  $('save-token').addEventListener('click', () => {
    state.apiBase = $('api-base').value.trim() || '/api';
    state.token = normalizeToken($('bearer-token').value);
    $('bearer-token').value = state.token;
    console.info('jwt_auth_diagnostics', tokenDiagnostics('save_token', state.token));
    localStorage.setItem('ops.apiBase', state.apiBase);
    localStorage.setItem('ops.token', state.token);
    refreshRest();
    connectWebSocket();
  });
  $('refresh-now').addEventListener('click', refreshRest);
  $('connect-ws').addEventListener('click', connectWebSocket);
  $('disconnect-ws').addEventListener('click', disconnectWebSocket);
  $('orders-prev').addEventListener('click', () => changePage('orders', -1));
  $('orders-next').addEventListener('click', () => changePage('orders', 1));
  $('trades-prev').addEventListener('click', () => changePage('trades', -1));
  $('trades-next').addEventListener('click', () => changePage('trades', 1));
  $('clear-log').addEventListener('click', () => { state.lastEvents = []; $('event-log').textContent = ''; });
  $('kill-switch').addEventListener('click', () => { logEvent('kill_switch_read_only', { message: 'Use authenticated admin API to enable or disable kill switch.' }); });
  renderAll(); refreshRest(); connectWebSocket(); setInterval(refreshRest, 10000);
}

document.addEventListener('DOMContentLoaded', init);
