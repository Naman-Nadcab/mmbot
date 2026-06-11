const state = {
  apiBase: localStorage.getItem('ops.apiBase') || '/api',
  token: localStorage.getItem('ops.token') || '',
  wsUrl: localStorage.getItem('ops.wsUrl') || defaultWsUrl(),
  socket: null,
  lastEvents: [],
  data: {
    positions: [], orders: [], trades: [], riskEvents: [], reconciliation: [],
    engines: {}, exchanges: {}, infrastructure: {}, pnl: null, inventory: null, mode: null
  }
};

const $ = (id) => document.getElementById(id);
const emptyRow = (cols) => `<tr><td colspan="${cols}" class="empty">Awaiting live data</td></tr>`;

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
    case 'engine_health': state.data.engines = payload.engines || payload; break;
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
  renderExchanges(); renderInfrastructure(); renderReconciliation(); renderMode(); renderInventory();
}

function renderPnl() {
  const pnl = state.data.pnl;
  $('pnl-value').textContent = pnl ? formatMoney(pnl.total ?? pnl.unrealized ?? pnl.realized) : 'Awaiting stream';
  $('pnl-subtitle').textContent = pnl ? `realized ${formatMoney(pnl.realized)} / unrealized ${formatMoney(pnl.unrealized)}` : 'realized / unrealized';
}

function renderInventory() {
  const inv = state.data.inventory;
  $('inventory-exposure').textContent = inv ? formatMoney(inv.exposure_notional ?? inv.total_notional) : 'Awaiting stream';
  $('inventory-subtitle').textContent = inv ? `skew ${formatNumber(inv.skew_bps)} bps` : 'notional / ratio';
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
    stackItem('Version', infra.version || 'unknown')
  ].join('');
}

function renderEngines() {
  const engines = state.data.engines;
  const entries = Object.entries(engines || {});
  $('engine-health').innerHTML = entries.length ? entries.map(([name, value]) => stackItem(name, value.status || value.health_status || JSON.stringify(value))).join('') : stackItem('Market Data Engine', 'Awaiting stream') + stackItem('Market Maker Engine', 'Awaiting stream') + stackItem('Risk Engine', 'Awaiting stream');
  $('engine-updated').textContent = entries.length ? 'Live stream' : 'No stream update';
}

function renderExchanges() {
  const entries = Object.entries(state.data.exchanges || {});
  $('exchange-connectivity').innerHTML = entries.length ? entries.map(([name, value]) => stackItem(name, value.status || value.detail || JSON.stringify(value))).join('') : stackItem('Exchanges', 'Awaiting stream or admin token');
}

function renderPositions() {
  $('positions-body').innerHTML = rows(state.data.positions, 5, (p) => `<tr><td>${esc(p.symbol)}</td><td>${esc(p.asset)}</td><td>${formatNumber(p.quantity)}</td><td>${formatMoney(p.notional)}</td><td>${formatMoney(p.pnl ?? p.unrealized_pnl)}</td></tr>`);
}
function renderOrders() {
  $('open-orders-count').textContent = Array.isArray(state.data.orders) ? String(state.data.orders.length) : 'Awaiting stream';
  $('orders-body').innerHTML = rows(state.data.orders, 6, (o) => `<tr><td>${esc(o.client_order_id || o.id)}</td><td>${esc(o.symbol)}</td><td>${esc(o.side)}</td><td>${esc(o.status)}</td><td>${formatNumber(o.price)}</td><td>${formatNumber(o.quantity)}</td></tr>`);
}
function renderTrades() {
  $('trades-body').innerHTML = rows(state.data.trades, 6, (t) => `<tr><td>${esc(t.trade_id || t.id)}</td><td>${esc(t.symbol)}</td><td>${esc(t.side)}</td><td>${formatNumber(t.price)}</td><td>${formatNumber(t.quantity)}</td><td>${formatNumber(t.fee)}</td></tr>`);
}
function renderRisk() {
  const risk = state.data.riskEvents || [];
  $('risk-count').textContent = risk.length ? String(risk.length) : 'Awaiting stream';
  $('risk-body').innerHTML = rows(risk, 4, (r) => `<tr><td>${esc(r.severity)}</td><td>${esc(r.event_type || r.type)}</td><td>${esc(r.message)}</td><td>${esc(r.occurred_at || r.time)}</td></tr>`);
}
function renderReconciliation() {
  setPill('reconciliation-status', state.data.reconciliationStatus || 'Awaiting stream', state.data.reconciliationStatus === 'ok' ? 'ok' : 'neutral');
  $('reconciliation-body').innerHTML = rows(state.data.reconciliation, 4, (r) => `<tr><td>${esc(r.category)}</td><td>${esc(r.key)}</td><td>${esc(r.severity)}</td><td>${esc(r.message)}</td></tr>`);
}

function rows(items, cols, renderer) { return Array.isArray(items) && items.length ? items.map(renderer).join('') : emptyRow(cols); }
function stackItem(name, status) { const cls = String(status).includes('healthy') || String(status).includes('ok') || String(status).includes('configured') ? 'ok' : String(status).includes('unhealthy') || String(status).includes('failed') ? 'bad' : 'neutral'; return `<div class="stack-item"><b>${esc(name)}</b><span class="pill ${cls}">${esc(status)}</span></div>`; }
function formatMoney(value) { if (value === undefined || value === null || value === '') return 'Awaiting stream'; const n = Number(value); return Number.isFinite(n) ? n.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }) : esc(value); }
function formatNumber(value) { if (value === undefined || value === null || value === '') return ''; const n = Number(value); return Number.isFinite(n) ? n.toLocaleString(undefined, { maximumFractionDigits: 8 }) : esc(value); }
function esc(value) { return String(value ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

function init() {
  $('api-base').value = state.apiBase;
  $('ws-url').value = state.wsUrl;
  $('bearer-token').value = state.token;
  $('save-token').addEventListener('click', () => { state.apiBase = $('api-base').value.trim() || '/api'; state.token = $('bearer-token').value.trim(); localStorage.setItem('ops.apiBase', state.apiBase); localStorage.setItem('ops.token', state.token); refreshRest(); });
  $('refresh-now').addEventListener('click', refreshRest);
  $('connect-ws').addEventListener('click', connectWebSocket);
  $('disconnect-ws').addEventListener('click', disconnectWebSocket);
  $('clear-log').addEventListener('click', () => { state.lastEvents = []; $('event-log').textContent = ''; });
  $('kill-switch').addEventListener('click', () => { $('kill-output').textContent = 'Kill switch endpoint is not available from existing backend APIs.'; });
  renderAll(); refreshRest(); setInterval(refreshRest, 10000);
}

document.addEventListener('DOMContentLoaded', init);
