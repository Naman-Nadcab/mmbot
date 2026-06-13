const pages = [
  ['overview', 'Overview'],
  ['strategy', 'Strategy'],
  ['spread', 'Spread'],
  ['layers', 'Layers'],
  ['order-size', 'Order Size'],
  ['volume', 'Volume'],
  ['liquidity', 'Liquidity'],
  ['inventory', 'Inventory'],
  ['risk', 'Risk'],
  ['coinstore', 'Coinstore'],
  ['emergency', 'Emergency']
];

const configDomains = {
  strategy: [
    ['trading_enabled', 'boolean'], ['quoting_enabled', 'boolean'], ['passive_only', 'boolean'],
    ['cancel_replace_enabled', 'boolean'], ['quote_refresh_seconds', 'number'], ['stale_market_data_seconds', 'number']
  ],
  spread: [['base_spread_bps', 'number'], ['min_spread_bps', 'number'], ['max_spread_bps', 'number'], ['volatility_multiplier', 'number']],
  order_layers: [['enabled_levels', 'number'], ['spacing_bps', 'number'], ['outer_level_multiplier', 'number'], ['refresh_threshold_bps', 'number'], ['max_active_orders_per_side', 'number']],
  order_size: [['base_order_size', 'number'], ['min_order_size', 'number'], ['max_order_size', 'number'], ['ladder_levels', 'number'], ['ladder_size_multiplier', 'number']],
  volume: [['enabled', 'boolean'], ['hourly_target_notional', 'number'], ['daily_target_notional', 'number'], ['weekly_target_notional', 'number'], ['max_participation_rate', 'number'], ['pressure_threshold', 'number'], ['max_size_multiplier', 'number'], ['min_seconds_between_pressure_orders', 'number'], ['external_volume_required', 'boolean']],
  liquidity: [['depth_levels', 'number'], ['imbalance_threshold', 'number'], ['min_top_of_book_depth', 'number'], ['target_depth_notional', 'number'], ['build_liquidity', 'boolean']],
  inventory: [['target_base_ratio', 'number'], ['skew_intensity', 'number'], ['max_asset_exposure', 'number'], ['alert_threshold_ratio', 'number']],
  risk: [['max_position_notional', 'number'], ['max_total_exposure', 'number'], ['max_order_notional', 'number'], ['max_open_orders', 'number'], ['max_daily_loss', 'number'], ['max_position_quantity', 'number'], ['circuit_breaker_enabled', 'boolean'], ['circuit_breaker_error_threshold', 'number'], ['circuit_breaker_cooldown_seconds', 'number'], ['auto_recovery_enabled', 'boolean'], ['auto_recovery_cooldown_seconds', 'number']],
  exchange: [['enabled_exchanges', 'csv'], ['default_timeout_seconds', 'number'], ['max_reconnect_delay_seconds', 'number'], ['heartbeat_interval_seconds', 'number']],
  alert: [['enabled_channels', 'csv'], ['min_severity', 'text'], ['telegram_enabled', 'boolean']]
};

const supportedExchanges = ['coinstore', 'mexc', 'gate', 'bitmart', 'kucoin', 'binance'];

const state = {
  apiBase: normalizeApiBase(localStorage.getItem('ops.apiBase') || '/api'),
  token: normalizeToken(localStorage.getItem('ops.token') || ''),
  wsUrl: localStorage.getItem('ops.wsUrl') || defaultWsUrl(),
  socket: null,
  manualDisconnect: false,
  wsReconnectTimer: null,
  config: {},
  versions: {},
  lastEvents: [],
  pending: {},
  runtimeEvents: [],
  history: { pnl: [], inventory: [] },
  pagination: { orders: { page: 0, size: 50 }, trades: { page: 0, size: 50 } },
  data: { engines: {}, infrastructure: {}, exchanges: {}, exchangeIntegrations: [], exchangeBalances: {}, orders: [], trades: [], positions: [], inventory: null, pnl: null, riskEvents: [], auditLogs: [], volume: null, coinstore: {}, kill: null }
};

const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const emptyRow = (cols) => `<tr><td colspan="${cols}" class="empty">No records returned by backend</td></tr>`;

function defaultWsUrl() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}/api/ws/operations`;
}

function normalizeApiBase(value) {
  const base = String(value || '/api').trim() || '/api';
  return base.length > 1 ? base.replace(/\/+$/g, '') : base;
}

function normalizeToken(value) {
  return String(value || '').trim().replace(/^Bearer\s+/i, '').replace(/^['"]|['"]$/g, '').replace(/\s+/g, '');
}

function headers(json = false) {
  const h = { Accept: 'application/json' };
  if (json) h['Content-Type'] = 'application/json';
  if (state.token) h.Authorization = `Bearer ${state.token}`;
  return h;
}

async function request(path, options = {}) {
  const response = await fetch(`${state.apiBase}${path}`, { ...options, headers: { ...headers(Boolean(options.body)), ...(options.headers || {}) } });
  if (!response.ok) {
    const text = await response.text();
    if (response.status === 401) {
      clearStoredToken('backend rejected token; generate a fresh backend-signed JWT');
    }
    throw new Error(`${path} ${response.status} ${text.slice(0, 180)}`);
  }
  return response.status === 204 ? null : response.json();
}

function clearStoredToken(reason) {
  state.token = '';
  localStorage.removeItem('ops.token');
  const input = $('bearer-token');
  if (input) input.value = '';
  setPill('ws-status', 'Token rejected', 'bad');
  logEvent('auth_token_cleared', { reason });
  disconnectWebSocket();
}

function logEvent(message, payload) {
  const line = `[${new Date().toISOString()}] ${message}${payload ? ' ' + JSON.stringify(payload) : ''}`;
  state.lastEvents.unshift(line);
  state.lastEvents = state.lastEvents.slice(0, 160);
  $('event-log').textContent = state.lastEvents.join('\n');
}

function setPill(id, text, status = 'neutral') {
  const el = $(id);
  if (!el) return;
  el.textContent = text;
  el.className = `status-chip ${status}`;
}

function setPending(key, active, button) {
  state.pending[key] = active;
  if (!button) return;
  button.disabled = active;
  button.classList.toggle('pending', active);
}

function validationError(form, message, fieldNames = []) {
  form.querySelectorAll('.validation-error').forEach((el) => el.remove());
  form.querySelectorAll('.invalid').forEach((el) => el.classList.remove('invalid'));
  for (const name of fieldNames) {
    if (form.elements[name]) form.elements[name].classList.add('invalid');
  }
  if (!message) return;
  const error = document.createElement('div');
  error.className = 'validation-error';
  error.textContent = message;
  form.prepend(error);
}

function validateRequired(form, names) {
  const missing = names.filter((name) => !String(form.elements[name]?.value || '').trim());
  if (missing.length) {
    validationError(form, `Required fields missing: ${missing.join(', ')}`, missing);
    return false;
  }
  validationError(form, '');
  return true;
}

function initNav() {
  $('main-nav').innerHTML = pages.map(([id, label]) => `<button type="button" class="nav-tab ${id === 'overview' ? 'active' : ''}" data-page="${id}">${label}</button>`).join('');
  $('main-nav').addEventListener('click', (event) => {
    const button = event.target.closest('[data-page]');
    if (!button) return;
    document.querySelectorAll('.nav-tab').forEach((item) => item.classList.toggle('active', item === button));
    document.querySelectorAll('.page').forEach((item) => item.classList.toggle('active', item.id === `page-${button.dataset.page}`));
  });
}

function initConfigForms() {
  for (const [domain, fields] of Object.entries(configDomains)) {
    const panel = $(`${domain}-panel`);
    if (!panel) continue;
    panel.innerHTML = `
      <article class="glass-panel data-panel">
        <div class="panel-header"><h2>${title(domain)} Control</h2><span id="${domain}-version" class="pill neutral">version loading</span></div>
        <form id="${domain}-form" class="form-grid config-form" data-domain="${domain}">
          ${fields.map(([name, type]) => control(domain, name, type)).join('')}
          <label>Change Reason<input name="__reason" value="operator runtime update" /></label>
          <div class="button-row"><button type="submit">Apply Runtime Change</button><button type="button" class="secondary" data-reload="${domain}">Reload From Backend</button></div>
        </form>
        ${domain === 'strategy' ? '<div class="button-row strategy-command-row"><button type="button" data-strategy-command="start">Start</button><button type="button" class="secondary" data-strategy-command="pause">Pause</button><button type="button" class="secondary" data-strategy-command="resume">Resume</button><button type="button" class="danger" data-strategy-command="stop">Stop</button></div>' : ''}
      </article>`;
    $(`${domain}-form`).addEventListener('submit', saveConfig);
    panel.querySelector('[data-reload]').addEventListener('click', () => loadConfigDomain(domain));
    panel.querySelectorAll('[data-strategy-command]').forEach((button) => button.addEventListener('click', () => sendStrategy(button.dataset.strategyCommand)));
  }
}

function control(domain, name, type) {
  const label = title(name);
  if (type === 'boolean') {
    return `<label>${label}<select name="${name}" id="${domain}-${name}"><option value="true">true</option><option value="false">false</option></select></label>`;
  }
  return `<label>${label}<input name="${name}" id="${domain}-${name}" type="${type === 'number' ? 'number' : 'text'}" step="any" autocomplete="off" /></label>`;
}

async function loadConfig() {
  if (!state.token) return;
  await Promise.all(Object.keys(configDomains).map((domain) => loadConfigDomain(domain).catch((error) => logEvent('config_load_failed', { domain, error: error.message }))));
}

async function loadConfigDomain(domain) {
  const payload = await request(`/admin/config/${domain}`);
  state.config[domain] = payload.config || {};
  state.versions[domain] = payload.version;
  fillConfigForm(domain);
}

function fillConfigForm(domain) {
  const config = state.config[domain] || {};
  const version = $(`${domain}-version`);
  if (version) version.textContent = `version ${state.versions[domain] ?? 0}`;
  for (const [name, type] of configDomains[domain] || []) {
    const el = $(`${domain}-${name}`);
    if (!el) continue;
    const value = config[name];
    el.value = type === 'csv' ? (Array.isArray(value) ? value.join(',') : String(value ?? '')) : String(value ?? '');
  }
}

async function saveConfig(event) {
  event.preventDefault();
  const form = event.target;
  const domain = form.dataset.domain;
  const submit = form.querySelector('button[type="submit"]');
  validationError(form, '');
  const config = {};
  for (const [name, type] of configDomains[domain]) {
    const raw = form.elements[name].value;
    if (type === 'number') {
      if (raw === '' || !Number.isFinite(Number(raw))) {
        validationError(form, `${title(name)} must be a finite number`, [name]);
        return;
      }
      config[name] = Number(raw);
    } else if (type === 'boolean') {
      config[name] = raw === 'true';
    } else if (type === 'csv') {
      const values = raw.split(',').map((item) => item.trim()).filter(Boolean);
      if (!values.length) {
        validationError(form, `${title(name)} requires at least one value`, [name]);
        return;
      }
      config[name] = values;
    } else {
      if (!raw.trim()) {
        validationError(form, `${title(name)} is required`, [name]);
        return;
      }
      config[name] = raw;
    }
  }
  try {
    setPending(`config:${domain}`, true, submit);
    const saved = await request(`/admin/config/${domain}`, { method: 'PUT', body: JSON.stringify({ config }) });
    state.config[domain] = saved.config;
    state.versions[domain] = saved.version;
    fillConfigForm(domain);
    logEvent('runtime_config_updated', { domain, version: saved.version });
    await refreshRuntimeEvents();
    await refreshOperations();
  } catch (error) {
    validationError(form, error.message);
    logEvent('runtime_config_update_failed', { domain, error: error.message });
  } finally {
    setPending(`config:${domain}`, false, submit);
  }
}

async function refreshRest() {
  try {
    const health = await request('/health');
    state.data.infrastructure.api = health.status;
    state.data.infrastructure.database = health.dependencies?.database;
    state.data.infrastructure.redis = health.dependencies?.redis;
    setPill('api-status', `API ${health.status}`, health.status === 'ok' ? 'ok' : 'warn');
  } catch (error) {
    setPill('api-status', 'API unavailable', 'bad');
    logEvent('health_unavailable', { error: error.message });
  }
  if (state.token) {
    await Promise.all([loadConfig(), refreshOperations(), refreshCoinstore()].map((p) => p.catch((error) => logEvent('refresh_error', { error: error.message }))));
  }
  renderAll();
}

async function refreshOperations() {
  const calls = [
    ['engines', '/operations/engines'],
    ['infrastructure', '/operations/infrastructure'],
    ['exchanges', '/operations/exchanges'],
    ['orders', '/operations/orders?limit=500'],
    ['trades', '/operations/trades?limit=500'],
    ['positions', '/operations/positions'],
    ['inventory', '/operations/inventory'],
    ['pnl', '/operations/pnl'],
    ['riskEvents', '/operations/risk-events'],
    ['auditLogs', '/operations/audit-logs?limit=100'],
    ['runtimeEvents', '/operations/runtime-events?limit=100'],
    ['volume', '/operations/volume'],
    ['kill', '/operations/kill-switch']
  ];
  for (const [key, path] of calls) {
    try {
      const payload = await request(path);
      if (key === 'orders' || key === 'trades' || key === 'positions' || key === 'riskEvents' || key === 'auditLogs' || key === 'runtimeEvents') state.data[key] = payload.items || [];
      else if (key === 'engines') state.data.engines = payload.engines || {};
      else if (key === 'exchanges') state.data.exchanges = payload.exchanges || {};
      else state.data[key] = payload;
    } catch (error) {
      logEvent('operations_endpoint_unavailable', { path, error: error.message });
    }
  }
}

async function refreshRuntimeEvents() {
  try {
    state.data.runtimeEvents = (await request('/operations/runtime-events?limit=100')).items || [];
    renderRuntimeAckState();
  } catch (error) {
    logEvent('runtime_events_unavailable', { error: error.message });
  }
}

function connectWebSocket() {
  disconnectWebSocket();
  state.manualDisconnect = false;
  if (!state.token) {
    setPill('ws-status', 'Token required', 'warn');
    return;
  }
  state.wsUrl = $('ws-url').value.trim() || defaultWsUrl();
  localStorage.setItem('ops.wsUrl', state.wsUrl);
  const url = new URL(state.wsUrl, window.location.href);
  url.searchParams.set('token', state.token);
  const socket = new WebSocket(url.toString());
  state.socket = socket;
  setPill('ws-status', 'WebSocket connecting', 'warn');
  socket.onopen = () => { setPill('ws-status', 'WebSocket connected', 'ok'); logEvent('websocket_connected'); };
  socket.onerror = () => { setPill('ws-status', 'WebSocket error', 'bad'); };
  socket.onclose = () => {
    setPill('ws-status', 'WebSocket disconnected', 'warn');
    if (!state.manualDisconnect) state.wsReconnectTimer = setTimeout(connectWebSocket, 3000);
  };
  socket.onmessage = (event) => handleStreamMessage(event.data);
}

function disconnectWebSocket() {
  state.manualDisconnect = true;
  if (state.wsReconnectTimer) clearTimeout(state.wsReconnectTimer);
  if (state.socket) state.socket.close();
  state.socket = null;
}

function handleStreamMessage(raw) {
  let message;
  try { message = JSON.parse(raw); } catch { return; }
  const type = message.type || message.event_type || 'unknown';
  const payload = message.payload || message.data || message;
  logEvent(type, payload);
  if (type === 'pnl') state.data.pnl = payload;
  if (type === 'positions') state.data.positions = payload.items || [];
  if (type === 'inventory') state.data.inventory = payload;
  if (type === 'orders') state.data.orders = payload.items || [];
  if (type === 'trades') state.data.trades = payload.items || [];
  if (type === 'risk_events') state.data.riskEvents = payload.items || [];
  if (type === 'engine_health') state.data.engines = payload.engines || {};
  if (type === 'exchange_connectivity') state.data.exchanges = payload.exchanges || {};
  renderAll();
}

async function refreshCoinstore() {
  try { state.data.exchangeIntegrations = (await request('/exchanges')).items || []; } catch (error) { logEvent('exchange_integrations_unavailable', { error: error.message }); }
  try { state.data.coinstore.accounts = (await request('/admin/coinstore/accounts')).items || []; } catch (error) { logEvent('coinstore_accounts_unavailable', { error: error.message }); }
  try { state.data.coinstore.health = await request('/admin/coinstore/health'); } catch (error) { state.data.coinstore.health = { rest: { status: 'unavailable', error: error.message } }; }
  await refreshExchangeBalances();
}

async function refreshExchangeBalances() {
  for (const integration of state.data.exchangeIntegrations || []) {
    const account = integration.accounts?.[0];
    if (!account) continue;
    try {
      state.data.exchangeBalances[integration.exchange_name] = await request(`/exchanges/${integration.exchange_name}/balances?account_alias=${encodeURIComponent(account.account_alias)}&environment=${encodeURIComponent(account.environment)}`);
    } catch (error) {
      state.data.exchangeBalances[integration.exchange_name] = { balances: [], error: error.message };
    }
  }
}

function initCoinstore() {
  $('coinstore-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.target;
    const submit = form.querySelector('button[type="submit"]');
    if (!validateRequired(form, ['account_alias', 'api_key', 'api_secret'])) return;
    const payload = {
      exchange_name: form.exchange_name.value.trim().toLowerCase(),
      account_alias: form.account_alias.value.trim(),
      environment: form.environment.value,
      api_key: form.api_key.value,
      api_secret: form.api_secret.value,
      passphrase: form.passphrase.value || null,
      permissions: form.permissions.value.split(',').map((item) => item.trim()).filter(Boolean),
      enabled: form.is_enabled.value === 'true'
    };
    try {
      setPending('coinstore:save', true, submit);
      const saved = await request('/exchanges/connect', { method: 'POST', body: JSON.stringify(payload) });
      form.api_key.value = '';
      form.api_secret.value = '';
      form.passphrase.value = '';
      logEvent('exchange_account_saved', { id: saved.id, exchange: saved.exchange_name, alias: saved.account_alias });
      await refreshCoinstore();
      renderCoinstore();
    } catch (error) {
      validationError(form, error.message);
      logEvent('coinstore_account_save_failed', { error: error.message });
    } finally {
      setPending('coinstore:save', false, submit);
    }
  });
  $('coinstore-health').addEventListener('click', async (event) => {
    try {
      setPending('coinstore:health', true, event.target);
      await refreshCoinstore();
      renderCoinstore();
    } finally {
      setPending('coinstore:health', false, event.target);
    }
  });
  $('coinstore-sync').addEventListener('click', async (event) => {
    const confirmation = prompt('Type sync to confirm Coinstore balance sync');
    if (!confirmation) return;
    if (!confirmation.toLowerCase().includes('sync')) {
      logEvent('coinstore_balance_sync_rejected', { reason: "confirmation must include 'sync'" });
      return;
    }
    try {
      setPending('coinstore:sync', true, event.target);
      const account = (state.data.exchangeIntegrations || []).find((item) => item.exchange_name === 'coinstore')?.accounts?.[0];
      const result = await request('/exchanges/coinstore/sync', { method: 'POST', body: JSON.stringify({ account_alias: account?.account_alias || 'primary', environment: account?.environment || 'production' }) });
      logEvent('coinstore_balance_sync', result);
      await refreshCoinstore();
      await refreshOperations();
    } finally {
      setPending('coinstore:sync', false, event.target);
    }
  });
}

async function setCoinstoreAccountStatus(account, enabled, button) {
  const word = enabled ? 'enable' : 'disable';
  const confirmation = prompt(`Type ${word} to ${word} Coinstore account ${account.account_alias}`);
  if (!confirmation) return;
  if (!confirmation.toLowerCase().includes(word)) {
    logEvent('coinstore_account_status_rejected', { account: account.id, reason: `confirmation must include '${word}'` });
    return;
  }
  try {
    setPending(`coinstore:status:${account.id}`, true, button);
    await request(`/admin/coinstore/accounts/${account.id}/status`, { method: 'PUT', body: JSON.stringify({ is_enabled: enabled, confirmation, reason: `operator ${word} account` }) });
    logEvent('coinstore_account_status_updated', { account: account.id, is_enabled: enabled });
    await refreshCoinstore();
    renderCoinstore();
  } finally {
    setPending(`coinstore:status:${account.id}`, false, button);
  }
}

function editExchangeAccount(account) {
  const form = $('coinstore-form');
  form.exchange_name.value = account.exchange_name;
  form.account_alias.value = account.account_alias;
  form.environment.value = account.environment;
  form.permissions.value = (account.permissions || []).join(',');
  form.is_enabled.value = String(Boolean(account.is_enabled));
  form.api_key.value = '';
  form.api_secret.value = '';
  form.passphrase.value = '';
  logEvent('exchange_account_edit_loaded', { exchange: account.exchange_name, account_alias: account.account_alias });
}

async function testExchangeAccount(account, button) {
  try {
    setPending(`exchange:test:${account.id}`, true, button);
    const payload = { exchange_name: account.exchange_name, account_alias: account.account_alias, environment: account.environment };
    const result = await request('/exchanges/test', { method: 'POST', body: JSON.stringify(payload) });
    logEvent('exchange_connection_tested', { exchange: account.exchange_name, status: result.connection_status, rest: result.test_result?.rest_status, private_ws: result.test_result?.private_ws_status });
    await refreshCoinstore();
    renderCoinstore();
  } finally {
    setPending(`exchange:test:${account.id}`, false, button);
  }
}

async function removeExchangeAccount(account, button) {
  const confirmation = prompt(`Type remove to remove ${account.exchange_name} account ${account.account_alias}`);
  if (!confirmation || !confirmation.toLowerCase().includes('remove')) {
    logEvent('exchange_remove_rejected', { exchange: account.exchange_name, account_alias: account.account_alias });
    return;
  }
  try {
    setPending(`exchange:remove:${account.id}`, true, button);
    await request('/exchanges/remove', { method: 'DELETE', body: JSON.stringify({ exchange_name: account.exchange_name, account_alias: account.account_alias, environment: account.environment, confirmation }) });
    logEvent('exchange_account_removed', { exchange: account.exchange_name, account_alias: account.account_alias });
    await refreshCoinstore();
    renderCoinstore();
  } finally {
    setPending(`exchange:remove:${account.id}`, false, button);
  }
}

function initEmergency() {
  const actions = [
    ['cancel-all-orders', 'Cancel All Orders', 'cancel', '/admin/emergency/cancel-all-orders'],
    ['disable-trading', 'Disable Trading', 'disable', '/admin/emergency/disable-trading'],
    ['enable-trading', 'Enable Trading', 'enable', '/admin/emergency/enable-trading'],
    ['close-positions', 'Close Positions', 'close', '/admin/emergency/close-positions'],
    ['runtime-restart', 'Runtime Restart', 'restart', '/admin/emergency/runtime-restart'],
    ['shutdown', 'Emergency Shutdown', 'shutdown', '/admin/emergency/shutdown']
  ];
  $('emergency-actions').innerHTML = actions.map(([id, label, word]) => `
    <form class="emergency-card" data-endpoint="${id}" data-word="${word}">
      <h3>${label}</h3>
      <label>Reason<input name="reason" value="operator confirmed ${label.toLowerCase()}" /></label>
      <label>Confirmation<input name="confirmation" placeholder="must include '${word}'" /></label>
      <button type="submit" class="${id === 'enable-trading' ? 'secondary' : 'danger'}">${label}</button>
    </form>`).join('');
  $('emergency-actions').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.target;
    const submit = form.querySelector('button[type="submit"]');
    const endpoint = actions.find(([id]) => id === form.dataset.endpoint)[3];
    const requiredWord = form.dataset.word;
    if (!validateRequired(form, ['reason', 'confirmation'])) return;
    if (!form.confirmation.value.toLowerCase().includes(requiredWord)) {
      validationError(form, `Confirmation must include '${requiredWord}'`, ['confirmation']);
      return;
    }
    try {
      setPending(`emergency:${form.dataset.endpoint}`, true, submit);
      const result = await request(endpoint, { method: 'POST', body: JSON.stringify({ confirmation: form.confirmation.value, reason: form.reason.value }) });
      $('kill-output').textContent = JSON.stringify(result, null, 2);
      logEvent('emergency_action_accepted', { endpoint, command_id: result.event?.command_id });
      await refreshRuntimeEvents();
      renderRuntimeAckState(result.event?.command_id);
      await refreshOperations();
    } catch (error) {
      validationError(form, error.message);
      logEvent('emergency_action_failed', { endpoint, error: error.message });
    } finally {
      setPending(`emergency:${form.dataset.endpoint}`, false, submit);
    }
  });
}

async function sendStrategy(command) {
  const result = await request('/admin/strategy/command', { method: 'POST', body: JSON.stringify({ command, confirmation: command, reason: `operator ${command}` }) });
  logEvent('strategy_command', result.state);
}

function renderAll() {
  renderPnl();
  renderInventory();
  renderOrders();
  renderTrades();
  renderRisk();
  renderAudit();
  renderEngines();
  renderInfrastructure();
  renderExchanges();
  renderVolume();
  renderCoinstore();
  renderRuntimeAckState();
}

function renderPnl() {
  const pnl = state.data.pnl;
  $('pnl-value').textContent = pnl ? money(pnl.total ?? pnl.unrealized ?? pnl.realized) : money(0);
  $('pnl-subtitle').textContent = pnl ? `realized ${money(pnl.realized)} / unrealized ${money(pnl.unrealized)}` : 'realized / unrealized';
  if (pnl) pushSample(state.history.pnl, Number(pnl.total || 0));
  renderAreaChart('pnl-chart', state.history.pnl);
}

function renderInventory() {
  const inv = state.data.inventory;
  $('inventory-exposure').textContent = inv ? money(inv.exposure_notional ?? inv.total_notional) : money(0);
  $('inventory-subtitle').textContent = inv ? `${(inv.items || []).length} snapshots` : '0 snapshots';
  if (inv) pushSample(state.history.inventory, Number(inv.exposure_notional || inv.total_notional || 0));
  renderAreaChart('inventory-chart', state.history.inventory);
  const items = inv?.items || [];
  $('inventory-detail').innerHTML = items.length ? items.slice(0, 12).map((item) => stackItem(item.asset, `${num(item.total_balance)} / ${money(item.valuation_amount)}`)).join('') : stackItem('Inventory', 'No records returned by backend');
}

function renderOrders() {
  const lifecycle = orderLifecycle();
  $('open-orders-count').textContent = String(lifecycle.open_orders_count ?? state.data.orders.length ?? 0);
  $('stale-orders-count').textContent = String(lifecycle.stale_orders_count ?? 0);
  $('cancelled-orders-count').textContent = String(lifecycle.cancelled_orders_count ?? 0);
  $('reconciliation-actions-count').textContent = String(lifecycle.reconciliation_actions ?? 0);
  const items = pageSlice(state.data.orders, state.pagination.orders);
  $('orders-body').innerHTML = rows(items, 6, (o) => `<tr><td>${esc(o.client_order_id || o.id)}</td><td>${esc(o.symbol)}</td><td>${esc(o.side)}</td><td>${esc(o.status)}</td><td>${num(o.price)}</td><td>${num(o.quantity)}</td></tr>`);
  $('orders-page').textContent = `${state.pagination.orders.page + 1}/${pageCount(state.data.orders, state.pagination.orders)}`;
}

function renderTrades() {
  const items = pageSlice(state.data.trades, state.pagination.trades);
  $('trades-body').innerHTML = rows(items, 6, (t) => `<tr><td>${esc(t.trade_id || t.id)}</td><td>${esc(t.symbol)}</td><td>${esc(t.side)}</td><td>${num(t.price)}</td><td>${num(t.quantity)}</td><td>${num(t.fee)}</td></tr>`);
  $('trades-page').textContent = `${state.pagination.trades.page + 1}/${pageCount(state.data.trades, state.pagination.trades)}`;
}

function renderRisk() {
  const risk = state.data.riskEvents || [];
  $('risk-count').textContent = String(risk.length);
  $('risk-body').innerHTML = rows(risk, 4, (r) => `<tr><td>${esc(r.severity)}</td><td>${esc(r.event_type)}</td><td>${esc(r.message)}</td><td>${esc(r.occurred_at)}</td></tr>`);
  setPill('risk-summary', risk.length ? `${risk.length} risk events` : 'Risk normal', risk.some((r) => String(r.severity).includes('critical')) ? 'bad' : risk.length ? 'warn' : 'ok');
}

function renderAudit() {
  $('audit-count').textContent = String((state.data.auditLogs || []).length);
}

function renderEngines() {
  const entries = Object.entries(state.data.engines || {});
  const lifecycle = orderLifecycle();
  const lifecycleItems = Object.keys(lifecycle).length ? [
    stackItem('Active Buy Orders', lifecycle.active_buy_orders ?? 0),
    stackItem('Active Sell Orders', lifecycle.active_sell_orders ?? 0),
    stackItem('Average Order Age', `${num(lifecycle.average_order_age)}s`),
    stackItem('Replacement Count', lifecycle.replacement_count ?? 0),
    stackItem('Risk Rejections Last Hour', lifecycle.risk_rejections_last_hour ?? 0)
  ].join('') : '';
  $('engine-health').innerHTML = entries.length ? entries.map(([name, value]) => stackItem(name, value?.status || JSON.stringify(value))).join('') + lifecycleItems : stackItem('Engine Health', 'No engine health records returned');
  const maker = state.data.engines['market-maker-engine'];
  const runtime = maker?.runtime || {};
  setPill('mode-pill', `${runtime.mode || 'mode unknown'} ${runtime.trading_enabled === false ? 'disabled' : 'enabled'}`, runtime.trading_enabled === false ? 'bad' : 'ok');
}

function orderLifecycle() {
  return state.data.engines?.['market-maker-engine']?.runtime?.order_lifecycle || {};
}

function renderInfrastructure() {
  const infra = state.data.infrastructure || {};
  $('infra-health').innerHTML = [stackItem('API', infra.api || 'checking'), stackItem('Database', infra.database || 'checking'), stackItem('Redis', infra.redis || 'checking'), stackItem('DB Latency', `${num(infra.database_latency_ms)} ms`), stackItem('Redis Latency', `${num(infra.redis_latency_ms)} ms`)].join('');
}

function renderExchanges() {
  const entries = Object.entries(state.data.exchanges || {});
  const coinstore = state.data.coinstore.health;
  const label = coinstore?.rest?.status || state.data.exchanges.coinstore?.status || 'Coinstore unknown';
  setPill('exchange-summary', `Coinstore ${label}`, String(label).includes('healthy') || String(label).includes('connected') ? 'ok' : 'warn');
  const kill = state.data.kill || {};
  setPill('kill-status', kill.active ? `ACTIVE ${kill.reason || ''}` : 'Kill inactive', kill.active ? 'bad' : 'ok');
}

function renderVolume() {
  const volume = state.data.volume;
  const dailyValue = volume?.daily?.executed_notional ?? state.data.trades.reduce((total, trade) => total + Number(trade.price || 0) * Number(trade.quantity || 0), 0);
  $('daily-volume').textContent = money(dailyValue);
  $('daily-volume-subtitle').textContent = volume ? `${Math.round((volume.daily.progress_ratio || 0) * 100)}% daily target` : 'target progress';
  $('volume-progress').innerHTML = volume ? [
    stackItem('Hourly Progress', `${money(volume.hourly.executed_notional)} / ${money(volume.hourly.target_notional)}`),
    stackItem('Daily Progress', `${money(volume.daily.executed_notional)} / ${money(volume.daily.target_notional)}`),
    stackItem('Weekly Progress', `${money(volume.weekly.executed_notional)} / ${money(volume.weekly.target_notional)}`),
    stackItem('Participation', `${num((volume.participation_rate || 0) * 100)}%`),
    stackItem('Pressure', `${volume.pressure.reason} size x${num(volume.pressure.size_multiplier)} spread x${num(volume.pressure.spread_multiplier)}`)
  ].join('') : stackItem('Volume Engine', 'No backend volume state returned');
}

function renderCoinstore() {
  const accounts = state.data.coinstore.accounts || [];
  const integrations = state.data.exchangeIntegrations || [];
  const health = state.data.coinstore.health || {};
  $('exchange-cards').innerHTML = supportedExchanges.map((name) => exchangeCard(name, integrations.find((item) => item.exchange_name === name))).join('');
  $('exchange-cards').querySelectorAll('[data-exchange-action]').forEach((button) => {
    button.addEventListener('click', () => {
      const exchange = button.dataset.exchange;
      const action = button.dataset.exchangeAction;
      const integration = integrations.find((item) => item.exchange_name === exchange);
      const account = integration?.accounts?.[0];
      if (action === 'connect') {
        $('coinstore-form').exchange_name.value = exchange;
        $('coinstore-form').account_alias.value = account?.account_alias || 'primary';
        $('coinstore-form').environment.value = account?.environment || 'production';
        $('coinstore-form').is_enabled.value = 'true';
      } else if (account && action === 'edit') editExchangeAccount(account);
      else if (account && action === 'test') testExchangeAccount(account, button);
      else if (account && action === 'remove') removeExchangeAccount(account, button);
      else if (action === 'refresh') refreshCoinstore().then(renderCoinstore);
    });
  });
  $('coinstore-state').innerHTML = [
    stackItem('REST Health', health.rest?.status || 'not checked'),
    stackItem('REST Latency', `${num(health.rest?.latency_ms)} ms`),
    stackItem('WebSocket Health', health.websocket?.status || 'not checked'),
    stackItem('Rate Limit', health.rate_limit ? `${health.rate_limit.requests}/${health.rate_limit.window_seconds}s` : 'not returned'),
    stackItem('Stored Accounts', String(accounts.length)),
    ...accounts.map((account) => accountItem(account))
  ].join('');
  $('coinstore-state').querySelectorAll('[data-account-status]').forEach((button) => {
    button.addEventListener('click', () => {
      const account = accounts.find((item) => item.id === button.dataset.accountId);
      if (account) setCoinstoreAccountStatus(account, button.dataset.accountStatus === 'enable', button);
    });
  });
}

function exchangeCard(name, integration) {
  const account = integration?.accounts?.[0] || null;
  const balancePayload = state.data.exchangeBalances?.[name] || {};
  const balances = balancePayload.balances || [];
  const status = account?.connection_status || integration?.status || 'disconnected';
  const statusClass = status === 'connected' ? 'ok' : status === 'invalid_credentials' || status === 'error' ? 'bad' : status === 'testing' ? 'warn' : 'neutral';
  const rest = account?.rest_status || 'disconnected';
  const ws = account?.websocket_status || 'disconnected';
  const privateWs = account?.private_ws_status || (name === 'coinstore' ? 'disconnected' : 'not_supported');
  return `<div class="exchange-card">
    <h3>${esc(title(name))}<span class="pill ${statusClass}">${esc(status)}</span></h3>
    ${stackItem('REST Status', rest)}
    ${stackItem('WebSocket Status', ws)}
    ${stackItem('Private WS Status', privateWs)}
    ${stackItem('API Key', account?.api_key_masked || 'not configured')}
    ${stackItem('Last Successful Test', account?.last_success_at || 'never')}
    ${stackItem('Last Failure', account?.last_failure_at || 'none')}
    ${stackItem('Last Error', account?.last_error_message || 'none')}
    ${stackItem('Portfolio Value', money(balancePayload.portfolio_value || 0))}
    ${stackItem('Inventory Ratio', `${num((balancePayload.inventory_ratio || 0) * 100)}%`)}
    ${balances.length ? `<div class="metric-stack">${balances.slice(0, 6).map((item) => stackItem(item.asset, `${num(item.available_balance)} available / ${num(item.locked_balance)} locked / ${num(item.total_balance)} total`)).join('')}</div>` : stackItem('Balances', balancePayload.error || 'No balances synced')}
    <div class="button-row">
      <button type="button" data-exchange="${esc(name)}" data-exchange-action="connect">Connect</button>
      <button type="button" class="secondary" data-exchange="${esc(name)}" data-exchange-action="edit" ${account ? '' : 'disabled'}>Edit</button>
      <button type="button" class="secondary" data-exchange="${esc(name)}" data-exchange-action="test" ${account ? '' : 'disabled'}>Test Connection</button>
      <button type="button" class="secondary" data-exchange="${esc(name)}" data-exchange-action="refresh">Refresh Status</button>
      <button type="button" class="danger" data-exchange="${esc(name)}" data-exchange-action="remove" ${account ? '' : 'disabled'}>Remove</button>
    </div>
  </div>`;
}

function accountItem(account) {
  const next = account.is_enabled ? 'disable' : 'enable';
  return `<div class="stack-item"><b>${esc(account.account_alias)} ${esc(account.environment)}</b><span class="pill ${account.is_enabled ? 'ok' : 'neutral'}">${account.is_enabled ? 'enabled' : 'disabled'} key=${account.has_api_key} secret=${account.has_api_secret}</span><button type="button" class="ghost" data-account-id="${esc(account.id)}" data-account-status="${next}">${title(next)}</button></div>`;
}

function renderRuntimeAckState(commandId) {
  const target = $('runtime-ack-state');
  if (!target) return;
  const events = state.data.runtimeEvents || [];
  const ack = commandId ? events.find((event) => event.command_id === commandId && String(event.event_type).includes('ack')) : events.find((event) => String(event.event_type).includes('ack'));
  target.innerHTML = ack ? [
    stackItem('Runtime Ack', `${ack.status} ${ack.event_type}`),
    stackItem('Command ID', ack.command_id || 'none'),
    stackItem('Component', ack.source_component || 'runtime'),
    stackItem('Ack Time', ack.acknowledged_at || ack.created_at || 'pending')
  ].join('') : stackItem('Runtime Ack', 'No acknowledgement yet');
}

function rows(items, cols, renderer) { return Array.isArray(items) && items.length ? items.map(renderer).join('') : emptyRow(cols); }
function pageSlice(items, page) { const list = Array.isArray(items) ? items : []; return list.slice(page.page * page.size, page.page * page.size + page.size); }
function pageCount(items, page) { return Math.max(1, Math.ceil((Array.isArray(items) ? items.length : 0) / page.size)); }
function changePage(kind, direction) { const page = state.pagination[kind]; page.page = Math.max(0, Math.min(page.page + direction, pageCount(state.data[kind], page) - 1)); renderAll(); }
function stackItem(name, status) { const s = String(status); const cls = s.includes('healthy') || s.includes('ok') || s.includes('enabled') || s.includes('inactive') ? 'ok' : s.includes('unhealthy') || s.includes('failed') || s.includes('ACTIVE') ? 'bad' : 'neutral'; return `<div class="stack-item"><b>${esc(name)}</b><span class="pill ${cls}">${esc(status)}</span></div>`; }
function money(value) { const n = Number(value || 0); return Number.isFinite(n) ? n.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }) : esc(value); }
function num(value) { const n = Number(value || 0); return Number.isFinite(n) ? n.toLocaleString(undefined, { maximumFractionDigits: 8 }) : esc(value); }
function title(value) { return String(value).replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()); }
function pushSample(series, value) { if (Number.isFinite(value)) { series.push(value); while (series.length > 40) series.shift(); } }
function renderAreaChart(id, values) {
  const el = $(id);
  if (!el) return;
  if (!values.length) { el.innerHTML = ''; return; }
  const min = Math.min(...values, 0), max = Math.max(...values, 1), spread = max - min || 1;
  const points = values.map((v, i) => `${values.length === 1 ? 100 : i / (values.length - 1) * 100},${46 - ((v - min) / spread * 40)}`).join(' ');
  el.innerHTML = `<svg class="chart-svg" viewBox="0 0 100 50" preserveAspectRatio="none"><polyline class="chart-area" fill="none" points="${points}"></polyline></svg>`;
}

function init() {
  initNav();
  initConfigForms();
  initCoinstore();
  initEmergency();
  $('api-base').value = state.apiBase;
  $('ws-url').value = state.wsUrl;
  $('bearer-token').value = state.token;
  $('save-token').addEventListener('click', () => {
    state.apiBase = normalizeApiBase($('api-base').value);
    state.token = normalizeToken($('bearer-token').value);
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
  renderAll();
  refreshRest();
  connectWebSocket();
  setInterval(refreshRest, 10000);
  setInterval(() => { if (state.token) refreshCoinstore().then(renderCoinstore); }, 30000);
}

document.addEventListener('DOMContentLoaded', init);
