const pages = [
  ['launch-mm', 'Launch MM'],
  ['monitor-mm', 'Monitor MM'],
  ['advanced', 'Advanced']
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
const campaignTemplates = {
  liquidity_builder: {
    label: 'Increase Liquidity',
    tone: 'green',
    purpose: 'Create tighter books and healthier liquidity.',
    recommended: 'Projects that want stronger order books',
    riskLevel: 'low',
    spreadBps: 18,
    layers: 5,
    inventoryTarget: 0.55,
    refreshRateSeconds: 6,
    volumeMultiplier: 0.8
  },
  balanced: {
    label: 'Keep Price Stable',
    tone: 'yellow',
    purpose: 'Support orderly day-to-day trading around your preferred zone.',
    recommended: 'Most projects and steady markets',
    riskLevel: 'medium',
    spreadBps: 24,
    layers: 4,
    inventoryTarget: 0.55,
    refreshRateSeconds: 5,
    volumeMultiplier: 1
  },
  volume_booster: {
    label: 'Increase Trading Volume',
    tone: 'red',
    purpose: 'Increase market activity.',
    recommended: 'Growing markets',
    riskLevel: 'high',
    spreadBps: 12,
    layers: 6,
    inventoryTarget: 0.6,
    refreshRateSeconds: 3,
    volumeMultiplier: 1.4
  },
  token_launch: {
    label: 'Token Launch',
    tone: 'blue',
    purpose: 'Support newly listed tokens.',
    recommended: 'Launch campaigns',
    riskLevel: 'medium',
    spreadBps: 16,
    layers: 7,
    inventoryTarget: 0.5,
    refreshRateSeconds: 4,
    volumeMultiplier: 1.2
  },
  custom: {
    label: 'Custom',
    tone: 'gray',
    purpose: 'Manual template for advanced users.',
    recommended: 'Expert mode only',
    riskLevel: 'medium',
    spreadBps: 22,
    layers: 4,
    inventoryTarget: 0.55,
    refreshRateSeconds: 5,
    volumeMultiplier: 1
  }
};

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
state.orderSort = { key: 'created_at', direction: 'desc' };
state.lastConnectionTest = null;
state.monitorTab = 'open-orders';
state.expertMode = localStorage.getItem('ops.expertMode') === 'true';
state.launchStep = Number(localStorage.getItem('ops.launchStep') || 1);
state.dismissedRecommendations = new Set(JSON.parse(localStorage.getItem('ops.dismissedRecommendations') || '[]'));
state.onboardingResumeLater = localStorage.getItem('ops.onboardingResumeLater') === 'true';
state.mmCampaign = {
  name: localStorage.getItem('ops.campaignName') || '',
  exchange: localStorage.getItem('ops.campaignExchange') || 'coinstore',
  pair: localStorage.getItem('ops.campaignPair') || 'BTC/USDT',
  budget: Number(localStorage.getItem('ops.campaignBudget') || 1000),
  riskLevel: localStorage.getItem('ops.campaignRisk') || 'medium',
  targetDailyVolume: Number(localStorage.getItem('ops.campaignVolume') || 10000),
  template: localStorage.getItem('ops.campaignTemplate') || '',
  currentPrice: Number(localStorage.getItem('ops.currentPrice') || 0.1),
  priceFloor: Number(localStorage.getItem('ops.priceFloor') || 0.08),
  priceCeiling: Number(localStorage.getItem('ops.priceCeiling') || 0.15),
  preferredMin: Number(localStorage.getItem('ops.preferredMin') || 0.095),
  preferredMax: Number(localStorage.getItem('ops.preferredMax') || 0.11)
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

function setText(id, text) {
  const el = $(id);
  if (el) el.textContent = text;
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
  $('main-nav').innerHTML = pages.map(([id, label]) => `<button type="button" class="nav-tab ${id === 'launch-mm' ? 'active' : ''}" data-page="${id}">${label}</button>`).join('');
  $('main-nav').addEventListener('click', (event) => {
    const button = event.target.closest('[data-page]');
    if (!button) return;
    switchPage(button.dataset.page);
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
  $('last-refresh-at').textContent = new Date().toLocaleTimeString();
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

function switchPage(id) {
  document.querySelectorAll('.nav-tab').forEach((item) => item.classList.toggle('active', item.dataset.page === id));
  document.querySelectorAll('.page').forEach((item) => item.classList.toggle('active', item.id === `page-${id}`));
}

function setLaunchStep(step) {
  state.launchStep = Math.max(1, Math.min(8, Number(step) || 1));
  localStorage.setItem('ops.launchStep', String(state.launchStep));
  renderLaunchWizard();
}

function setExpertMode(enabled) {
  state.expertMode = Boolean(enabled);
  localStorage.setItem('ops.expertMode', String(state.expertMode));
  document.body.classList.toggle('expert-mode', state.expertMode);
  const toggle = $('expert-mode-toggle');
  if (toggle) toggle.checked = state.expertMode;
  renderTemplates();
}

function persistCampaign() {
  localStorage.setItem('ops.campaignName', state.mmCampaign.name || '');
  localStorage.setItem('ops.campaignExchange', state.mmCampaign.exchange || 'coinstore');
  localStorage.setItem('ops.campaignPair', state.mmCampaign.pair || '');
  localStorage.setItem('ops.campaignBudget', String(state.mmCampaign.budget || 0));
  localStorage.setItem('ops.campaignRisk', state.mmCampaign.riskLevel || '');
  localStorage.setItem('ops.campaignVolume', String(state.mmCampaign.targetDailyVolume || 0));
  localStorage.setItem('ops.campaignTemplate', state.mmCampaign.template || '');
  localStorage.setItem('ops.currentPrice', String(state.mmCampaign.currentPrice || 0));
  localStorage.setItem('ops.priceFloor', String(state.mmCampaign.priceFloor || 0));
  localStorage.setItem('ops.priceCeiling', String(state.mmCampaign.priceCeiling || 0));
  localStorage.setItem('ops.preferredMin', String(state.mmCampaign.preferredMin || 0));
  localStorage.setItem('ops.preferredMax', String(state.mmCampaign.preferredMax || 0));
}

function campaignExists() {
  return Boolean(state.mmCampaign.name && state.mmCampaign.template && state.mmCampaign.budget > 0);
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
  setPill('ws-status', 'Live updates connecting', 'warn');
  socket.onopen = () => { setPill('ws-status', 'Live updates connected', 'ok'); logEvent('websocket_connected'); };
  socket.onerror = () => { setPill('ws-status', 'Live updates error', 'bad'); };
  socket.onclose = () => {
    setPill('ws-status', 'Live updates disconnected', 'warn');
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
      renderAll();
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
      renderAll();
    } finally {
      setPending('coinstore:health', false, event.target);
    }
  });
  $('coinstore-sync').addEventListener('click', async (event) => {
    const exchangeName = selectedExchangeName();
    const confirmation = prompt(`Type sync to confirm ${title(exchangeName)} balance sync`);
    if (!confirmation) return;
    if (!confirmation.toLowerCase().includes('sync')) {
      logEvent('coinstore_balance_sync_rejected', { reason: "confirmation must include 'sync'" });
      return;
    }
    try {
      setPending('coinstore:sync', true, event.target);
      const account = selectedExchangeAccount();
      const result = await request(`/exchanges/${encodeURIComponent(exchangeName)}/sync`, { method: 'POST', body: JSON.stringify({ account_alias: account?.account_alias || 'primary', environment: account?.environment || 'production' }) });
      logEvent('exchange_balance_sync', result);
      await refreshCoinstore();
      await refreshOperations();
      renderAll();
    } finally {
      setPending('coinstore:sync', false, event.target);
    }
  });
  $('coinstore-test-current').addEventListener('click', async (event) => {
    const form = $('coinstore-form');
    const integration = (state.data.exchangeIntegrations || []).find((item) => item.exchange_name === form.exchange_name.value.trim().toLowerCase());
    const account = integration?.accounts?.find((item) => item.account_alias === form.account_alias.value.trim() && item.environment === form.environment.value) || integration?.accounts?.[0];
    if (!account) {
      validationError(form, 'Save this exchange account before testing the connection.', ['api_key', 'api_secret']);
      return;
    }
    await testExchangeAccount(account, event.target);
    renderAll();
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
  renderOperatorEmergencyActions(actions);
}

function renderOperatorEmergencyActions(actions) {
  const target = $('operator-emergency-actions');
  if (!target) return;
  const labels = {
    'disable-trading': 'Disable Trading',
    'enable-trading': 'Enable Trading',
    'cancel-all-orders': 'Cancel All Orders',
    'close-positions': 'Close Positions',
    shutdown: 'Kill Switch',
    'runtime-restart': 'Resume Quoting',
  };
  target.innerHTML = actions.map(([id, _label, word]) => `<button type="button" class="${['disable-trading','cancel-all-orders','close-positions','shutdown'].includes(id) ? 'danger' : 'secondary'}" data-operator-emergency="${id}" data-word="${word}">${labels[id] || _label}</button>`).join('');
  target.querySelectorAll('[data-operator-emergency]').forEach((button) => button.addEventListener('click', async () => {
    const id = button.dataset.operatorEmergency;
    const action = actions.find(([item]) => item === id);
    if (!action) return;
    const confirmation = prompt(`Type ${action[2]} to confirm ${action[1]}`);
    if (!confirmation || !confirmation.toLowerCase().includes(action[2])) return;
    try {
      setPending(`operator-emergency:${id}`, true, button);
      const result = await request(action[3], { method: 'POST', body: JSON.stringify({ confirmation, reason: `operator ${action[1].toLowerCase()}` }) });
      logEvent('operator_emergency_action', { action: id, command_id: result.event?.command_id });
      await refreshRuntimeEvents();
      renderRuntimeAckState(result.event?.command_id);
    } finally {
      setPending(`operator-emergency:${id}`, false, button);
    }
  }));
}

async function sendStrategy(command) {
  if (command === 'start') {
    const readiness = readinessState();
    const criticalFailures = readiness.items.filter((item) => item.critical && item.state === 'bad');
    if (criticalFailures.length) {
      const message = `Campaign cannot start: ${criticalFailures.map((item) => item.label).join(', ')}`;
      logEvent('campaign_start_blocked', { reason: message });
      renderReadiness();
      throw new Error(message);
    }
    localStorage.setItem('ops.campaignCreated', 'true');
  }
  const result = await request('/admin/strategy/command', { method: 'POST', body: JSON.stringify({ command, confirmation: command, reason: `operator ${command}` }) });
  logEvent('strategy_command', result.state);
  await refreshRuntimeEvents();
  await refreshOperations();
  renderAll();
}

function renderAll() {
  renderGlobalStatus();
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
  renderCoinstoreCompact();
  renderPremiumKpis();
  renderLiveMarketData();
  renderMarketMakerStatus();
  renderInventoryManagement();
  renderLaunchWizard();
  renderOnboardingWizard();
  renderTemplates();
  renderDashboardSummary();
  renderActivityTimeline();
  renderMarketMakingCampaign();
  renderReadiness();
  renderRecommendations();
  renderMonitoringSummary();
  renderRiskInventoryUx();
  renderRuntimeAckState();
}

function renderLaunchWizard() {
  const steps = [
    ['Exchange', Boolean(selectedExchangeName())],
    ['API', isConnected(selectedExchangeAccount()?.connection_status || selectedExchangeAccount()?.rest_status)],
    ['Balance', (state.data.exchangeBalances?.[selectedExchangeName()]?.balances || []).length > 0],
    ['Pair', Boolean(state.mmCampaign.pair)],
    ['Price Range', validPriceRange()],
    ['Goal', Boolean(state.mmCampaign.template)],
    ['Budget', Number(state.mmCampaign.budget || 0) > 0],
    ['Launch', readinessState().criticalFailures.length === 0]
  ];
  const stepper = $('launch-stepper');
  if (stepper) {
    stepper.innerHTML = steps.map(([label, complete], index) => `<button type="button" class="launch-step ${state.launchStep === index + 1 ? 'active' : ''} ${complete ? 'complete' : ''}" data-launch-step="${index + 1}"><span>${index + 1}</span>${esc(label)}</button>`).join('');
  }
  document.querySelectorAll('[data-launch-step-panel]').forEach((panel) => panel.classList.toggle('active', Number(panel.dataset.launchStepPanel) === state.launchStep));
}

function validPriceRange() {
  const { currentPrice, priceFloor, priceCeiling, preferredMin, preferredMax } = state.mmCampaign;
  return Number(priceFloor) > 0 && Number(priceCeiling) > Number(priceFloor) && Number(preferredMin) >= Number(priceFloor) && Number(preferredMax) <= Number(priceCeiling) && Number(currentPrice) > 0;
}

function renderQuickStartWizard() {
  const integrations = state.data.exchangeIntegrations || [];
  const coinstore = integrations.find((item) => item.exchange_name === 'coinstore');
  const account = coinstore?.accounts?.[0];
  const balances = state.data.exchangeBalances?.[selectedExchangeName()]?.balances || [];
  const makerRuntime = state.data.engines?.['market-maker-engine']?.runtime || {};
  const kill = state.data.kill || {};
  const steps = [
    ['1', 'Connect Exchange', Boolean(account), account ? `${account.account_alias} / ${account.environment}` : 'Create or select a Coinstore account'],
    ['2', 'Verify API', isConnected(account?.connection_status || account?.rest_status), account?.last_success_at || account?.last_error_message || 'Run connection test'],
    ['3', 'Sync Balances', balances.length > 0, balances.length ? `${balances.length} assets synced` : 'Sync balances from exchange'],
    ['4', 'Configure MM', Boolean(makerRuntime.runtime_config), makerRuntime.runtime_config ? `${makerRuntime.mode || 'runtime'} config loaded` : 'Load runtime configuration'],
    ['5', 'Start Market Making', Boolean(makerRuntime.runtime_config) && makerRuntime.trading_enabled !== false && !kill.active, kill.active ? 'Kill switch active' : makerRuntime.trading_enabled === false ? 'Trading disabled' : makerRuntime.runtime_config ? 'Ready / active' : 'Waiting for runtime state']
  ];
  const target = $('wizard-steps');
  if (!target) return;
  target.innerHTML = steps.map(([number, label, complete, subtitle]) => `<article class="wizard-step ${complete ? 'ok' : 'warn'}"><span>Step ${number}</span><strong>${esc(label)}</strong><small>${esc(subtitle)}</small><span class="pill ${complete ? 'ok' : 'warn'}">${complete ? 'Complete' : 'Action Required'}</span></article>`).join('');
}

function renderOnboardingWizard() {
  const wrapper = $('onboarding-wizard');
  if (!wrapper) return;
  const steps = onboardingSteps();
  const completed = steps.filter((step) => step.complete).length;
  const percent = Math.round((completed / steps.length) * 100);
  const needsSetup = !steps.every((step) => step.complete);
  wrapper.classList.toggle('hidden', !needsSetup || state.onboardingResumeLater);
  setText('onboarding-score', `${percent}%`);
  const score = $('onboarding-score');
  if (score) score.className = `status-chip ${percent === 100 ? 'ok' : percent >= 60 ? 'warn' : 'bad'}`;
  const bar = $('onboarding-progress-bar');
  if (bar) bar.style.width = `${percent}%`;
  const target = $('onboarding-steps');
  if (target) {
    target.innerHTML = steps.map((step, index) => `<div class="onboarding-step ${step.complete ? 'state-ok' : 'state-warn'}"><i>${step.complete ? 'OK' : index + 1}</i><div><strong>${esc(step.label)}</strong><small>${esc(step.detail)}</small></div></div>`).join('');
  }
}

function onboardingSteps() {
  const account = selectedExchangeAccount();
  const balances = state.data.exchangeBalances?.[selectedExchangeName()]?.balances || [];
  const readiness = readinessState();
  return [
    { label: 'Connect Exchange', complete: Boolean(account), detail: account ? `${title(selectedExchangeName())} account exists` : 'Connect an exchange account' },
    { label: 'Add API Credentials', complete: Boolean(account?.has_api_key && account?.has_api_secret), detail: account?.api_key_masked || 'Add API key and secret' },
    { label: 'Test Connection', complete: isConnected(account?.connection_status || account?.rest_status), detail: account?.last_success_at || account?.last_error_message || 'Run connection test' },
    { label: 'Sync Balances', complete: balances.length > 0, detail: balances.length ? `${balances.length} assets synced` : `Sync balances from ${title(selectedExchangeName())}` },
    { label: 'Create Campaign', complete: campaignExists(), detail: campaignExists() ? state.mmCampaign.name : 'Select a template and campaign details' },
    { label: 'Review', complete: readiness.score >= 75, detail: `${readiness.score}% readiness score` },
    { label: 'Start Market Making', complete: state.data.engines?.['market-maker-engine']?.runtime?.trading_enabled !== false && Boolean(state.data.engines?.['market-maker-engine']), detail: state.data.engines?.['market-maker-engine'] ? 'Runtime available' : 'Start after readiness checks pass' }
  ];
}

function renderTemplates() {
  const target = $('template-cards');
  if (!target) return;
  target.innerHTML = Object.entries(campaignTemplates).map(([key, template]) => {
    const isCustom = key === 'custom';
    const disabled = isCustom && !state.expertMode;
    return `<button type="button" class="template-card ${state.mmCampaign.template === key ? 'active' : ''}" data-template="${esc(key)}" ${disabled ? 'disabled' : ''}>
      <span class="status-chip ${template.tone === 'green' ? 'ok' : template.tone === 'yellow' ? 'warn' : template.tone === 'red' ? 'bad' : 'neutral'}">${esc(isCustom ? 'Expert' : 'Template')}</span>
      <strong>${esc(template.label)}</strong>
      <small><b>Purpose:</b> ${esc(template.purpose)}</small>
      <small><b>Recommended for:</b> ${esc(template.recommended)}</small>
      <small>Rocket MM automatically handles order amount, market depth, token balance target, risk profile and update speed.</small>
    </button>`;
  }).join('');
}

function applyTemplate(key) {
  const template = campaignTemplates[key];
  if (!template || (key === 'custom' && !state.expertMode)) return;
  state.mmCampaign.template = key;
  state.mmCampaign.riskLevel = template.riskLevel;
  if (!state.mmCampaign.name) state.mmCampaign.name = `${template.label} Campaign`;
  state.mmCampaign.targetDailyVolume = Math.max(10000, Math.round(Number(state.mmCampaign.budget || 1000) * 10 * template.volumeMultiplier));
  state.launchStep = Math.max(state.launchStep, 7);
  persistCampaign();
  const name = $('campaign-name');
  const volume = $('target-daily-volume');
  if (name) name.value = state.mmCampaign.name;
  if (volume) volume.value = String(state.mmCampaign.targetDailyVolume);
  document.querySelectorAll('[data-risk-level]').forEach((button) => button.classList.toggle('active', button.dataset.riskLevel === state.mmCampaign.riskLevel));
  renderTemplates();
  renderMarketMakingCampaign();
  renderReadiness();
  renderLaunchWizard();
}

function renderPremiumKpis() {
  const lifecycle = orderLifecycle();
  const spread = currentSpreadBps();
  const mid = currentMidPrice();
  setText('current-spread-value', `${num(spread)} bps`);
  setText('current-mid-value', `mid ${num(mid)}`);
  setText('paper-fills-value', String(counter('paper.fills') || counter('market_maker.paper_fills') || 0));
  setText('quote-refreshes-value', String(counter('market_maker.quote_refreshes') || counter('market_maker.quotes_generated') || 0));
  setText('reconciliation-actions-count', String(lifecycle.reconciliation_actions ?? 0));
}

function renderLiveMarketData() {
  const book = latestOrderbook();
  const { bid, ask, depth, symbol } = bestBidAsk(book);
  const mid = bid && ask ? (bid + ask) / 2 : currentMidPrice();
  const spread = bid && ask && mid ? ((ask - bid) / mid) * 10000 : currentSpreadBps();
  if (mid > 0) {
    state.mmCampaign.currentPrice = mid;
    localStorage.setItem('ops.currentPrice', String(mid));
  }
  setText('market-data-symbol', symbol || book?.symbol || 'symbol');
  setText('market-data-bid', num(bid));
  setText('market-data-ask', num(ask));
  setText('market-data-mid', num(mid));
  setText('market-data-spread', `${num(spread)} bps`);
  setText('market-data-depth', money(depth));
  setText('current-price-kpi', num(mid || state.mmCampaign.currentPrice));
}

function renderCoinstoreCompact() {
  const exchangeName = selectedExchangeName();
  const integration = selectedExchangeIntegration();
  const account = integration?.accounts?.[0];
  const health = exchangeName === 'coinstore' ? state.data.coinstore.health || {} : {};
  const balancesPayload = state.data.exchangeBalances?.[exchangeName] || {};
  const balances = balancesPayload.balances || [];
  setText('summary-connection', simpleStatus(account?.connection_status || health.rest?.status || 'Disconnected'));
  setText('summary-rest', account?.rest_status || health.rest?.status || 'Unknown');
  setText('summary-rest-sub', account?.last_success_at || health.rest?.error || 'No test');
  setText('summary-ws', account?.websocket_status || health.websocket?.status || 'Unknown');
  setText('summary-private-ws', account?.private_ws_status || 'Unknown');
  setText('summary-portfolio', money(balancesPayload.portfolio_value || 0));
  setText('summary-sync', balances.length ? `${balances.length} assets synced` : balancesPayload.error || 'No sync');
  setText('total-assets-count', String(balances.length));
  const connectionStatus = account?.connection_status || health.rest?.status || 'not tested';
  const connectionClass = isConnected(connectionStatus) ? 'ok' : String(connectionStatus).includes('error') || String(connectionStatus).includes('invalid') ? 'bad' : 'neutral';
  const pill = $('connection-test-status');
  if (pill) {
    pill.textContent = connectionStatus;
    pill.className = `pill ${connectionClass}`;
  }
  const compact = $('coinstore-compact-balances');
  if (compact) compact.innerHTML = balances.length ? balances.slice(0, 4).map((item) => stackItem(item.asset, `${num(item.available_balance)} available / ${num(item.total_balance)} total`)).join('') : stackItem('Balances', balancesPayload.error || 'No balances synced');
  const availableTotal = balances.reduce((total, item) => total + Number(item.available_balance || 0) * Number(item.valuation_price || 1), 0);
  const lockedTotal = balances.reduce((total, item) => total + Number(item.locked_balance || item.reserved_balance || 0) * Number(item.valuation_price || 1), 0);
  const usdt = balances.find((item) => item.asset === 'USDT') || {};
  const token = balances.find((item) => item.asset && item.asset !== 'USDT') || {};
  setText('available-usdt-balance', num(usdt.available_balance || 0));
  setText('available-token-balance', token.asset ? `${token.asset} ${num(token.available_balance || 0)}` : '0');
  setText('locked-balance-total', money(lockedTotal));
  const results = $('connection-test-results');
  if (results) {
    results.innerHTML = [
      stackItem('Connection Status', connectionStatus),
      stackItem('REST Status', account?.rest_status || health.rest?.status || 'not checked'),
      stackItem('Market Stream', account?.websocket_status || health.websocket?.status || 'not checked'),
      stackItem('Account Stream', account?.private_ws_status || 'not checked'),
      stackItem('API Key', account?.api_key_masked || 'not configured'),
      stackItem('Last Tested', account?.last_tested_at || 'never'),
      stackItem('Last Success', account?.last_success_at || 'never'),
      stackItem('Last Error', account?.last_error_message || account?.last_failure_at || 'none')
    ].join('');
  }
}

function renderDashboardSummary() {
  const integration = selectedExchangeIntegration();
  const account = integration?.accounts?.[0];
  const maker = state.data.engines?.['market-maker-engine'] || {};
  const makerRuntime = maker.runtime || {};
  const exchangeStatus = account?.connection_status || integration?.status || state.data.coinstore.health?.rest?.status || 'Disconnected';
  setText('dashboard-exchange-status', simpleStatus(exchangeStatus));
  setText('dashboard-exchange-subtitle', account ? `${title(integration.exchange_name)} ${account.account_alias}` : title(selectedExchangeName()));
  setText('dashboard-mm-status', makerRuntime.trading_enabled === false ? 'Paused' : simpleStatus(maker.status || makerRuntime.mode || 'Unknown'));
  setText('dashboard-mm-subtitle', makerRuntime.mode || 'Runtime');
  setHealth('health-api', 'health-api-dot', state.data.infrastructure?.api || 'checking');
  setHealth('health-exchange', 'health-exchange-dot', exchangeStatus);
  setHealth('health-market-data', 'health-market-data-dot', state.data.engines?.['market-data-engine']?.status || 'checking');
  setHealth('health-market-maker', 'health-market-maker-dot', maker.status || 'checking');
  setText('todays-fills-value', String((state.data.trades || []).length));
  const lifecycle = orderLifecycle();
  const mmScore = Math.max(0, Math.min(100, Math.round((makerRuntime.trading_enabled === false ? 35 : 70) + Math.min(20, Number(lifecycle.open_orders_count || 0) * 2) - Math.min(30, (state.data.riskEvents || []).length * 5))));
  const spread = currentSpreadBps();
  const liquidityScore = Math.max(0, Math.min(100, Math.round(100 - Math.min(60, spread) + Math.min(20, Number(lifecycle.open_orders_count || 0)))));
  setText('mm-score-value', String(mmScore));
  setText('mm-score-label', mmScore >= 75 ? 'Healthy' : mmScore >= 45 ? 'Needs attention' : 'Not running');
  setText('liquidity-score-value', String(liquidityScore));
  setText('liquidity-score-label', liquidityScore >= 75 ? 'Strong' : liquidityScore >= 45 ? 'Moderate' : 'Thin');
  const running = makerRuntime.trading_enabled !== false && Boolean(state.data.engines?.['market-maker-engine']);
  setText('active-campaigns-value', running ? '1' : '0');
  setText('active-campaigns-label', running ? state.mmCampaign.name || 'Campaign running' : 'No campaign running');
  setText('mm-health-value', mmScore >= 75 ? 'Healthy' : mmScore >= 45 ? 'Warning' : 'Stopped');
}

function renderActivityTimeline() {
  const items = [];
  const account = (state.data.exchangeIntegrations || []).find((item) => item.exchange_name === 'coinstore')?.accounts?.[0];
  if (account?.last_success_at) items.push(['Exchange Connected', `Coinstore ${account.account_alias}`, account.last_success_at]);
  const balances = state.data.exchangeBalances?.coinstore?.balances || [];
  if (balances.length) items.push(['Balances Synced', `${balances.length} assets available`, state.data.exchangeBalances?.coinstore?.as_of || 'Latest sync']);
  const runtime = state.data.engines?.['market-maker-engine']?.runtime || {};
  if (runtime.trading_enabled !== false && state.data.engines?.['market-maker-engine']) items.push(['Campaign Started', state.mmCampaign.name, state.data.engines?.['market-maker-engine']?.last_heartbeat_at || 'Latest heartbeat']);
  if (runtime.trading_enabled === false && state.data.engines?.['market-maker-engine']) items.push(['Campaign Paused', state.mmCampaign.name, state.data.engines?.['market-maker-engine']?.last_heartbeat_at || 'Latest heartbeat']);
  if (state.data.kill?.active) items.push(['Campaign Stopped', state.data.kill.reason || state.mmCampaign.name, 'Kill switch active']);
  for (const event of (state.data.auditLogs || []).filter((item) => String(item.action || '').includes('EXCHANGE')).slice(0, 2)) items.push(['API Updated', event.resource_type || 'Exchange account', event.occurred_at || '']);
  for (const trade of (state.data.trades || []).slice(0, 2)) items.push(['Order Filled', `${trade.side || ''} ${trade.symbol || ''} ${num(trade.quantity)} @ ${num(trade.price)}`, trade.created_at || trade.traded_at || '']);
  for (const order of (state.data.orders || []).filter((item) => String(item.status).toLowerCase().includes('cancel')).slice(0, 2)) items.push(['Order Cancelled', order.client_order_id || order.exchange_order_id || order.id, order.created_at || '']);
  for (const event of (state.data.runtimeEvents || []).slice(0, 4)) items.push([title(event.event_type || 'Runtime Event'), event.status || event.source_component || 'Runtime', event.created_at || event.acknowledged_at || '']);
  const target = $('activity-timeline');
  if (!target) return;
  target.innerHTML = items.length ? items.slice(0, 8).map(([name, detail, time]) => `<div class="timeline-item"><span class="timeline-icon">${esc(String(name).slice(0, 1))}</span><div><strong>${esc(name)}</strong><p>${esc(detail || '')}</p><small>${esc(time || '')}</small></div></div>`).join('') : `<div class="empty">No recent backend activity returned</div>`;
}

function renderMarketMakingCampaign() {
  const auto = automaticCampaignSettings();
  const review = $('mm-review');
  if (review) {
    review.innerHTML = [
      stackItem('Exchange', title(selectedExchangeName())),
      stackItem('Campaign Name', state.mmCampaign.name),
      stackItem('Trading Pair', state.mmCampaign.pair),
      stackItem('Current Price', num(state.mmCampaign.currentPrice)),
      stackItem('Price Floor', num(state.mmCampaign.priceFloor)),
      stackItem('Price Ceiling', num(state.mmCampaign.priceCeiling)),
      stackItem('Preferred Zone', `${num(state.mmCampaign.preferredMin)} - ${num(state.mmCampaign.preferredMax)}`),
      stackItem('Budget', `${num(state.mmCampaign.budget)} USDT`),
      stackItem('Goal', campaignTemplates[state.mmCampaign.template]?.label || 'Select goal'),
      stackItem('Risk Level', title(state.mmCampaign.riskLevel)),
      stackItem('Estimated Daily Volume', `${num(state.mmCampaign.targetDailyVolume)} USDT`),
      stackItem('Estimated Spread', `${num(auto.spreadBps)} bps`)
    ].join('');
  }
  const autoSummary = $('automatic-settings-summary');
  if (autoSummary) {
    autoSummary.innerHTML = [
      stackItem('Spread', `${num(auto.spreadBps)} bps`),
      stackItem('Market Depth', auto.layers),
      stackItem('Order Amount', `${num(auto.quoteSize)} USDT`),
      stackItem('Inventory Target', `${num(auto.inventoryTarget * 100)}%`),
      stackItem('Update Speed', `${num(auto.refreshRateSeconds)}s`),
      stackItem('Risk Limit', `${num(auto.riskLimit)} USDT`)
    ].join('');
  }
  const advanced = $('campaign-advanced-settings');
  if (advanced) {
    advanced.innerHTML = [
      stackItem('Spread', `${num(auto.spreadBps)} bps`),
      stackItem('Market Depth', auto.layers),
      stackItem('Order Amount', `${num(auto.quoteSize)} USDT`),
      stackItem('Inventory Target', `${num(auto.inventoryTarget * 100)}%`),
      stackItem('Update Speed', `${num(auto.refreshRateSeconds)}s`),
      stackItem('Risk Controls', `${num(auto.riskLimit)} USDT max exposure`)
    ].join('');
  }
  const pair = $('mm-pair-select');
  if (pair && pair.value !== state.mmCampaign.pair) pair.value = state.mmCampaign.pair;
  const name = $('campaign-name');
  const budget = $('mm-budget');
  const volume = $('target-daily-volume');
  const currentPrice = $('current-price');
  const floor = $('price-floor');
  const ceiling = $('price-ceiling');
  const preferredMin = $('preferred-zone-min');
  const preferredMax = $('preferred-zone-max');
  if (name && name.value !== state.mmCampaign.name) name.value = state.mmCampaign.name;
  if (budget && Number(budget.value) !== Number(state.mmCampaign.budget)) budget.value = String(state.mmCampaign.budget);
  if (volume && Number(volume.value) !== Number(state.mmCampaign.targetDailyVolume)) volume.value = String(state.mmCampaign.targetDailyVolume);
  if (currentPrice && Number(currentPrice.value) !== Number(state.mmCampaign.currentPrice)) currentPrice.value = String(state.mmCampaign.currentPrice);
  if (floor && Number(floor.value) !== Number(state.mmCampaign.priceFloor)) floor.value = String(state.mmCampaign.priceFloor);
  if (ceiling && Number(ceiling.value) !== Number(state.mmCampaign.priceCeiling)) ceiling.value = String(state.mmCampaign.priceCeiling);
  if (preferredMin && Number(preferredMin.value) !== Number(state.mmCampaign.preferredMin)) preferredMin.value = String(state.mmCampaign.preferredMin);
  if (preferredMax && Number(preferredMax.value) !== Number(state.mmCampaign.preferredMax)) preferredMax.value = String(state.mmCampaign.preferredMax);
  document.querySelectorAll('[data-budget]').forEach((button) => {
    const active = Number(button.dataset.budget) === Number(state.mmCampaign.budget);
    button.classList.toggle('active', active);
    button.classList.toggle('secondary', !active);
  });
  document.querySelectorAll('[data-risk-level]').forEach((button) => {
    const active = button.dataset.riskLevel === state.mmCampaign.riskLevel;
    button.classList.toggle('active', active);
    button.classList.toggle('secondary', !active);
  });
  document.querySelectorAll('[data-daily-volume]').forEach((button) => {
    const active = Number(button.dataset.dailyVolume) === Number(state.mmCampaign.targetDailyVolume);
    button.classList.toggle('active', active);
    button.classList.toggle('secondary', !active);
  });
  setText('campaign-status-name', state.mmCampaign.name || 'Market Making Campaign');
  setText('campaign-status-exchange', title(selectedExchangeName()));
  setText('campaign-status-pair', state.mmCampaign.pair);
  setText('campaign-status-budget', `${num(state.mmCampaign.budget)} USDT`);
  const used = Number(state.data.volume?.daily?.executed_notional || 0);
  setText('campaign-status-remaining', `${num(Math.max(0, state.mmCampaign.budget - used))} USDT`);
  const runtime = state.data.engines?.['market-maker-engine']?.runtime || {};
  const status = state.data.kill?.active ? 'Stopped' : runtime.trading_enabled === false ? 'Paused' : state.data.engines?.['market-maker-engine'] ? 'Running' : 'Stopped';
  setText('campaign-status-state', status);
  const pill = $('campaign-status-pill');
  if (pill) {
    pill.textContent = status;
    pill.className = `status-chip ${status === 'Running' ? 'ok' : status === 'Paused' ? 'warn' : 'bad'}`;
  }
  setText('campaign-status-state-copy', status);
  const monitorSummary = $('campaign-monitor-summary');
  if (monitorSummary) {
    monitorSummary.innerHTML = [
      stackItem('Goal', campaignTemplates[state.mmCampaign.template]?.label || 'Not selected'),
      stackItem('Price Zone', `${num(state.mmCampaign.preferredMin)} - ${num(state.mmCampaign.preferredMax)}`),
      stackItem('Remaining Budget', `${num(Math.max(0, state.mmCampaign.budget - used))} USDT`)
    ].join('');
  }
}

function automaticCampaignSettings() {
  const budget = Number(state.mmCampaign.budget || 0);
  const volume = Number(state.mmCampaign.targetDailyVolume || 0);
  const template = campaignTemplates[state.mmCampaign.template] || {};
  const risk = state.mmCampaign.riskLevel || template.riskLevel || 'medium';
  const riskMultiplier = risk === 'low' ? 0.6 : risk === 'high' ? 1.4 : 1.0;
  return {
    spreadBps: template.spreadBps ?? (risk === 'low' ? 35 : risk === 'high' ? 12 : 22),
    layers: template.layers ?? Math.max(1, Math.min(8, Math.round((budget / 2500) * riskMultiplier) || 1)),
    quoteSize: Math.max(10, Math.round((budget / 20) * riskMultiplier)),
    inventoryTarget: template.inventoryTarget ?? (risk === 'high' ? 0.6 : risk === 'low' ? 0.5 : 0.55),
    refreshRateSeconds: template.refreshRateSeconds ?? (risk === 'high' ? 3 : risk === 'low' ? 10 : 5),
    riskLimit: Math.max(budget, Math.round(volume * (risk === 'high' ? 0.2 : risk === 'low' ? 0.08 : 0.12)))
  };
}

function readinessState() {
  const account = coinstoreAccount();
  const balances = state.data.exchangeBalances?.coinstore?.balances || [];
  const dataRuntime = state.data.engines?.['market-data-engine']?.runtime || {};
  const maker = state.data.engines?.['market-maker-engine'] || {};
  const runtime = maker.runtime || {};
  const hasPair = Boolean(state.mmCampaign.pair);
  const marketKeys = Object.keys(dataRuntime.last_message_timestamp || runtime.latest_orderbook || {});
  const pairHasData = marketKeys.some((key) => key.includes(state.mmCampaign.pair)) || Boolean(latestOrderbook());
  const items = [
    { label: 'Exchange Connected', detail: account ? account.account_alias : 'No exchange account connected', state: account ? 'ok' : 'bad', critical: true },
    { label: 'API Valid', detail: account?.last_error_message || account?.last_success_at || 'Connection test required', state: isConnected(account?.connection_status || account?.rest_status) ? 'ok' : account ? 'warn' : 'bad', critical: true },
    { label: 'Account Stream Connected', detail: account?.private_ws_status || 'No account stream status', state: isConnected(account?.private_ws_status) || account?.private_ws_status === 'not_supported' ? 'ok' : account ? 'warn' : 'bad', critical: false },
    { label: 'Balance Synced', detail: balances.length ? `${balances.length} assets synced` : 'No synced balances', state: balances.length ? 'ok' : 'bad', critical: true },
    { label: 'Trading Pair Available', detail: state.mmCampaign.pair || 'Select a trading pair', state: hasPair ? 'ok' : 'bad', critical: true },
    { label: 'Budget Assigned', detail: `${num(state.mmCampaign.budget)} USDT`, state: state.mmCampaign.budget > 0 ? 'ok' : 'bad', critical: true },
    { label: 'Risk Configured', detail: title(state.mmCampaign.riskLevel || ''), state: state.mmCampaign.riskLevel ? 'ok' : 'bad', critical: true },
    { label: 'Strategy Selected', detail: campaignTemplates[state.mmCampaign.template]?.label || 'Select a template', state: state.mmCampaign.template ? 'ok' : 'bad', critical: true },
    { label: 'Market Data Available', detail: pairHasData ? 'Recent market data available' : 'Waiting for market data', state: pairHasData ? 'ok' : 'warn', critical: false },
    { label: 'Market Maker Ready', detail: maker.status || 'Runtime not reporting yet', state: simpleStatus(maker.status).toLowerCase().includes('connected') || runtime.mode ? 'ok' : 'warn', critical: false }
  ];
  const score = Math.round(items.reduce((total, item) => total + (item.state === 'ok' ? 10 : item.state === 'warn' ? 5 : 0), 0));
  const criticalFailures = items.filter((item) => item.critical && item.state === 'bad');
  return { items, score, criticalFailures };
}

function renderReadiness() {
  const readiness = readinessState();
  setText('readiness-score', `${readiness.score}%`);
  setText('readiness-label', readiness.score >= 95 ? 'Ready' : readiness.score >= 85 ? 'Minor Warnings' : 'Needs Attention');
  const checklist = $('readiness-checklist');
  if (checklist) {
    checklist.innerHTML = readiness.items.map((item) => `<div class="readiness-item state-${item.state}"><i>${item.state === 'ok' ? 'OK' : item.state === 'warn' ? '!' : 'X'}</i><div><strong>${esc(item.label)}</strong><small>${esc(item.detail)}${item.critical ? ' - Required' : ''}</small></div></div>`).join('');
  }
  const start = $('mm-start-large');
  if (start) start.disabled = readiness.criticalFailures.length > 0;
  setText('start-readiness-message', readiness.criticalFailures.length ? `Resolve required checks: ${readiness.criticalFailures.map((item) => item.label).join(', ')}` : 'Ready to start campaign.');
}

function recommendations() {
  const list = [];
  const balances = state.data.exchangeBalances?.coinstore?.balances || [];
  const current = Number(state.data.exchangeBalances?.[selectedExchangeName()]?.inventory_ratio || 0);
  const target = automaticCampaignSettings().inventoryTarget;
  const deviation = current - target;
  const spread = currentSpreadBps();
  const volumeRatio = Number(state.data.volume?.daily?.progress_ratio);
  const exposure = Number(state.data.inventory?.exposure_notional ?? state.data.inventory?.total_notional ?? 0);
  const maxExposure = Number(state.data.engines?.['market-maker-engine']?.runtime?.runtime_config?.inventory?.max_asset_exposure || 0);
  const usage = maxExposure ? exposure / maxExposure : 0;
  const lifecycle = orderLifecycle();
  if (!balances.length) list.push({ id: 'sync-balances', title: 'Balance sync needed', reason: `No synced balances are available from ${title(selectedExchangeName())}.`, action: 'Sync balances', type: 'sync' });
  if (Math.abs(deviation) > 0.08) list.push({ id: 'inventory-target', title: deviation < 0 ? 'Inventory below target' : 'Inventory above target', reason: `Current ratio is ${num(current * 100)}% vs target ${num(target * 100)}%.`, action: 'Review inventory', type: 'review-monitoring' });
  if (state.mmCampaign.budget > 0 && usage > 0.8) list.push({ id: 'risk-utilization', title: 'Risk utilization approaching threshold', reason: `${num(usage * 100)}% of max exposure is currently used.`, action: 'Review risk', type: 'review-settings' });
  if (spread > 35) list.push({ id: 'tighten-spread', title: 'Spread can be tightened', reason: `Current spread is ${num(spread)} bps.`, action: 'Review campaign', type: 'review-campaign' });
  if (Number.isFinite(volumeRatio) && volumeRatio < 0.5) list.push({ id: 'volume-target', title: 'Volume target unlikely to be met', reason: `Daily progress is ${Math.round(volumeRatio * 100)}%.`, action: 'Review campaign', type: 'review-campaign' });
  if (Number.isFinite(volumeRatio) && volumeRatio > 1) list.push({ id: 'above-target', title: 'Campaign performing above target', reason: `Daily volume is ${Math.round(volumeRatio * 100)}% of target.`, action: 'Review analytics', type: 'review-monitoring' });
  if (spread <= 20 && Number(lifecycle.open_orders_count || 0) > 0) list.push({ id: 'depth-healthy', title: 'Market depth healthy', reason: 'Spread and active orders indicate healthy liquidity.', action: 'Review analytics', type: 'review-monitoring' });
  return list.filter((item) => !state.dismissedRecommendations.has(item.id));
}

function renderRecommendations() {
  const target = $('recommendations-list');
  if (!target) return;
  const items = recommendations();
  target.innerHTML = items.length ? items.map((item) => `<article class="recommendation-card" data-recommendation="${esc(item.id)}"><strong>${esc(item.title)}</strong><p>${esc(item.reason)}</p><small>Suggested action: ${esc(item.action)}</small><div class="recommendation-actions"><button type="button" data-recommendation-action="apply">Fix Now</button><button type="button" class="secondary" data-recommendation-action="review">Learn More</button><button type="button" class="ghost" data-recommendation-action="dismiss">Dismiss</button></div></article>`).join('') : '<div class="empty">No live recommendations at this time</div>';
}

function handleRecommendation(id, action) {
  const item = recommendations().find((recommendation) => recommendation.id === id);
  if (!item) return;
  if (action === 'dismiss') {
    state.dismissedRecommendations.add(id);
    localStorage.setItem('ops.dismissedRecommendations', JSON.stringify([...state.dismissedRecommendations]));
    renderRecommendations();
    return;
  }
  if (item.type === 'sync') {
    $('coinstore-sync').click();
  } else if (item.type === 'review-settings') {
    switchPage('advanced');
    setExpertMode(true);
  } else if (item.type === 'review-campaign') {
    switchPage('launch-mm');
    setLaunchStep(6);
  } else {
    switchPage('monitor-mm');
  }
  if (action === 'apply' && id === 'volume-target') {
    state.mmCampaign.targetDailyVolume = Math.max(10000, Math.round(Number(state.mmCampaign.targetDailyVolume || 0) * 0.85));
    persistCampaign();
    renderMarketMakingCampaign();
  }
}

function renderMonitoringSummary() {
  const lifecycle = orderLifecycle();
  const spread = currentSpreadBps();
  const liquidityScore = Math.max(0, Math.min(100, Math.round(100 - Math.min(60, spread) + Math.min(20, Number(lifecycle.open_orders_count || 0)))));
  const volumeProgress = state.data.volume?.daily?.progress_ratio;
  setText('spread-quality', spread <= 15 ? 'Strong' : spread <= 35 ? 'Normal' : 'Wide');
  setText('liquidity-quality', liquidityScore >= 75 ? 'Strong' : liquidityScore >= 45 ? 'Moderate' : 'Thin');
  setText('inventory-health-card', $('inventory-health-label')?.textContent || 'Balanced');
  setText('volume-progress-status', Number.isFinite(Number(volumeProgress)) ? `${Math.round(Number(volumeProgress) * 100)}%` : 'Waiting for data');
  const analytics = $('analytics-summary');
  if (analytics) {
    analytics.innerHTML = [
      stackItem('Active Orders', lifecycle.open_orders_count ?? state.data.orders.length ?? 0),
      stackItem('Quote Refreshes', counter('market_maker.quote_refreshes') || counter('market_maker.quotes_generated') || 0),
      stackItem('Reconciliation Runs', counter('reconciliation.runs') || 0),
      stackItem('Risk Rejections Last Hour', lifecycle.risk_rejections_last_hour ?? 0)
    ].join('');
  }
  const history = $('order-history-summary');
  if (history) {
    history.innerHTML = [
      stackItem('Total Orders Loaded', (state.data.orders || []).length),
      stackItem('Open Orders', lifecycle.open_orders_count ?? 0),
      stackItem('Stale Orders', lifecycle.stale_orders_count ?? 0),
      stackItem('Cancelled Orders', lifecycle.cancelled_orders_count ?? 0)
    ].join('');
  }
}

function renderRiskInventoryUx() {
  const runtime = state.data.engines?.['market-maker-engine']?.runtime || {};
  const inventoryConfig = runtime.runtime_config?.inventory || state.config.inventory || {};
  const riskEvents = state.data.riskEvents || [];
  const current = Number(state.data.exchangeBalances?.coinstore?.inventory_ratio || 0);
  const target = Number(inventoryConfig.target_base_ratio || 0);
  const deviation = (current - target) * 100;
  const exposure = Number(state.data.inventory?.exposure_notional ?? state.data.inventory?.total_notional ?? 0);
  const maxExposure = Number(inventoryConfig.max_asset_exposure || 0);
  const usage = maxExposure > 0 ? Math.min(100, Math.max(0, exposure / maxExposure * 100)) : 0;
  const riskScore = Math.min(100, Math.round(Math.abs(deviation) * 2 + usage * 0.45 + riskEvents.length * 5));
  const health = riskScore >= 75 ? 'Critical' : riskScore >= 45 ? 'Warning' : 'Healthy';
  setText('inventory-health-label', deviation > 1 ? 'Overweight' : deviation < -1 ? 'Underweight' : 'Balanced');
  setText('inventory-health-kpi', deviation > 1 ? 'Overweight' : deviation < -1 ? 'Underweight' : 'Balanced');
  setText('target-inventory-ratio', `${num(target * 100)}%`);
  setText('inventory-deviation', `${num(deviation)}%`);
  setText('risk-score', String(riskScore));
  setText('risk-score-label', health);
  setText('exposure-usage-label', `${num(usage)}%`);
  const exposureBar = $('exposure-usage-bar');
  if (exposureBar) exposureBar.style.width = `${usage}%`;
  const exposureDetail = $('exposure-usage-detail');
  if (exposureDetail) {
    exposureDetail.innerHTML = [
      stackItem('Used', money(exposure)),
      stackItem('Remaining', money(Math.max(0, maxExposure - exposure))),
      stackItem('Maximum', money(maxExposure))
    ].join('');
  }
}

function renderGlobalStatus() {
  const maker = state.data.engines?.['market-maker-engine'] || {};
  const data = state.data.engines?.['market-data-engine'] || {};
  const makerRuntime = maker.runtime || {};
  const dataRuntime = data.runtime || {};
  const coinstoreHealth = state.data.coinstore.health || {};
  const coinstoreAccount = (state.data.exchangeIntegrations || []).find((item) => item.exchange_name === 'coinstore')?.accounts?.[0];
  setStatusTile('status-market-data', 'status-market-data-sub', data.status || 'unknown', `msgs ${dataRuntime.websocket_messages_received || 0}`);
  setStatusTile('status-market-maker', 'status-market-maker-sub', maker.status || 'unknown', `quotes ${counter('market_maker.quotes_generated')}`);
  setStatusTile('status-coinstore-rest', 'status-coinstore-rest-sub', coinstoreAccount?.rest_status || coinstoreHealth.rest?.status || 'unknown', coinstoreAccount?.last_success_at || coinstoreHealth.rest?.error || 'No test');
  setStatusTile('status-coinstore-public-ws', 'status-coinstore-public-ws-sub', coinstoreHealth.websocket?.status || dataRuntime.websocket_state || 'unknown', `known ${Object.keys(state.data.exchanges || {}).length}`);
  setStatusTile('status-coinstore-private-ws', 'status-coinstore-private-ws-sub', coinstoreAccount?.private_ws_status || 'unknown', coinstoreAccount?.last_tested_at || 'No test');
  setStatusTile('status-trading-mode', 'status-trading-mode-sub', makerRuntime.mode || 'unknown', makerRuntime.trading_enabled === false ? 'disabled' : 'enabled');
  setStatusTile('status-risk-engine', 'status-risk-engine-sub', (state.data.riskEvents || []).some((item) => item.severity === 'critical') ? 'failed' : 'healthy', `${(state.data.riskEvents || []).length} events`);
  setStatusTile('status-kill-switch', 'status-kill-switch-sub', state.data.kill?.active ? 'failed' : 'healthy', state.data.kill?.reason || 'inactive');
}

function setStatusTile(id, subId, status, subtitle) {
  const el = $(id);
  const sub = $(subId);
  if (!el) return;
  const parent = el.closest('.status-tile');
  const normalized = String(status || 'unknown').toLowerCase();
  const label = normalized.includes('healthy') || normalized.includes('connected') || normalized.includes('ok') || normalized.includes('paper') || normalized.includes('live') || normalized.includes('canary') ? 'Connected' : normalized.includes('warn') || normalized.includes('partial') || normalized.includes('degraded') ? 'Warning' : normalized.includes('fail') || normalized.includes('error') || normalized.includes('unhealthy') || normalized.includes('active') ? 'Failed' : title(normalized || 'unknown');
  parent.className = `status-tile ${label === 'Connected' ? 'ok' : label === 'Warning' ? 'warn' : label === 'Failed' ? 'bad' : ''}`;
  el.textContent = label;
  if (sub) sub.textContent = subtitle || '';
}

function setHealth(textId, dotId, status) {
  const label = simpleStatus(status);
  setText(textId, label);
  const dot = $(dotId);
  if (!dot) return;
  dot.className = `dot ${statusClass(status)}`;
}

function simpleStatus(status) {
  const value = String(status || 'unknown').toLowerCase();
  if (value.includes('healthy') || value.includes('connected') || value === 'ok') return 'Connected';
  if (value.includes('testing')) return 'Testing';
  if (value.includes('degraded') || value.includes('partial') || value.includes('warn')) return 'Warning';
  if (value.includes('fail') || value.includes('error') || value.includes('invalid') || value.includes('unhealthy')) return 'Disconnected';
  if (value.includes('paper') || value.includes('live') || value.includes('canary')) return title(value);
  return title(value || 'unknown');
}

function statusClass(status) {
  const label = simpleStatus(status).toLowerCase();
  if (label.includes('connected') || label.includes('healthy') || label.includes('ok')) return 'ok';
  if (label.includes('warning') || label.includes('testing')) return 'warn';
  if (label.includes('disconnected') || label.includes('failed') || label.includes('active')) return 'bad';
  return 'neutral';
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
  setText('open-orders-count', String(lifecycle.open_orders_count ?? state.data.orders.length ?? 0));
  setText('stale-orders-count', String(lifecycle.stale_orders_count ?? 0));
  setText('cancelled-orders-count', String(lifecycle.cancelled_orders_count ?? 0));
  setText('reconciliation-actions-count', String(lifecycle.reconciliation_actions ?? 0));
  const query = String($('orders-search')?.value || '').toLowerCase();
  const statusFilter = String($('orders-status-filter')?.value || '').toLowerCase();
  const sideFilter = String($('orders-side-filter')?.value || '').toLowerCase();
  const filtered = (state.data.orders || []).filter((o) => {
    const status = String(o.status || '').toLowerCase();
    const side = String(o.side || '').toLowerCase();
    return (!query || JSON.stringify(o).toLowerCase().includes(query))
      && (!statusFilter || status.includes(statusFilter))
      && (!sideFilter || side === sideFilter);
  });
  filtered.sort((a, b) => compareValues(a[state.orderSort.key], b[state.orderSort.key], state.orderSort.direction));
  state.pagination.orders.page = Math.min(state.pagination.orders.page, pageCount(filtered, state.pagination.orders) - 1);
  const items = pageSlice(filtered, state.pagination.orders);
  $('orders-body').innerHTML = rows(items, 6, (o) => `<tr><td>${esc(o.side)}</td><td>${num(o.price)}</td><td>${num(o.quantity)}</td><td><span class="order-status ${esc(o.status)}">${esc(o.status)}</span></td><td>${esc(o.created_at || '')}</td><td>${orderAge(o.created_at)}</td></tr>`);
  $('orders-page').textContent = `${state.pagination.orders.page + 1}/${pageCount(filtered, state.pagination.orders)}`;
}

function renderTrades() {
  const items = pageSlice(state.data.trades, state.pagination.trades);
  $('trades-body').innerHTML = rows(items, 6, (t) => `<tr><td>${esc(t.trade_id || t.id)}</td><td>${esc(t.symbol)}</td><td>${esc(t.side)}</td><td>${num(t.price)}</td><td>${num(t.quantity)}</td><td>${num(t.fee)}</td></tr>`);
  $('trades-page').textContent = `${state.pagination.trades.page + 1}/${pageCount(state.data.trades, state.pagination.trades)}`;
}

function renderRisk() {
  const risk = state.data.riskEvents || [];
  setText('risk-count', String(risk.length));
  $('risk-body').innerHTML = rows(risk, 4, (r) => `<tr><td>${esc(r.severity)}</td><td>${esc(r.event_type)}</td><td>${esc(r.message)}</td><td>${esc(r.occurred_at)}</td></tr>`);
  setPill('risk-summary', risk.length ? `${risk.length} risk events` : 'Risk normal', risk.some((r) => String(r.severity).includes('critical')) ? 'bad' : risk.length ? 'warn' : 'ok');
  const config = state.data.engines?.['market-maker-engine']?.runtime?.runtime_config?.risk || state.config.risk || {};
  const target = $('risk-limit-panel');
  if (target) {
    target.innerHTML = [
      stackItem('Max Position Notional', money(config.max_position_notional || 0)),
      stackItem('Max Capital At Risk', money(config.max_total_exposure || 0)),
      stackItem('Max Order Notional', money(config.max_order_notional || 0)),
      stackItem('Max Open Orders', config.max_open_orders || 0),
      stackItem('Circuit Breaker', config.circuit_breaker_enabled === false ? 'disabled' : 'enabled')
    ].join('');
  }
}

function renderAudit() {
  setText('audit-count', String((state.data.auditLogs || []).length));
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

function renderMarketMakerStatus() {
  const maker = state.data.engines?.['market-maker-engine'] || {};
  const runtime = maker.runtime || {};
  const lifecycle = runtime.order_lifecycle || {};
  const config = runtime.runtime_config || {};
  $('mm-status-mode').textContent = runtime.mode || 'unknown';
  $('mm-status-panel').innerHTML = [
    stackItem('Strategy Active', runtime.trading_enabled === false ? 'No' : 'Yes'),
    stackItem('Quoting Enabled', runtime.quoting_enabled === false ? 'No' : 'Yes'),
    stackItem('Trading Enabled', runtime.trading_enabled === false ? 'No' : 'Yes'),
    stackItem('Current Spread', `${num(currentSpreadBps())} bps`),
    stackItem('Current Mid Price', num(currentMidPrice())),
    stackItem('Buy Orders', lifecycle.active_buy_orders ?? 0),
    stackItem('Sell Orders', lifecycle.active_sell_orders ?? 0),
    stackItem('Total Open Orders', lifecycle.open_orders_count ?? state.data.orders.length ?? 0),
    stackItem('Last Quote Refresh', `${counter('market_maker.quote_refreshes')} refreshes`),
    stackItem('Last Reconciliation', `${counter('reconciliation.runs')} runs`),
    stackItem('Runtime Mode', runtime.mode || 'unknown')
  ].join('');
}

function renderInventoryManagement() {
  const balances = state.data.exchangeBalances?.[selectedExchangeName()]?.balances || [];
  const usdt = balances.find((item) => item.asset === 'USDT') || {};
  const base = balances.find((item) => item.asset && item.asset !== 'USDT') || {};
  const inv = state.data.inventory || {};
  const maker = state.data.engines?.['market-maker-engine']?.runtime || {};
  const target = maker.runtime_config?.inventory?.target_base_ratio ?? 0;
  const current = state.data.exchangeBalances?.[selectedExchangeName()]?.inventory_ratio ?? 0;
  const maxExposure = maker.runtime_config?.inventory?.max_asset_exposure ?? 0;
  const exposure = inv.exposure_notional ?? inv.total_notional ?? 0;
  const skew = (current - target) * 100;
  const bias = skew > 1 ? 'Sell bias' : skew < -1 ? 'Buy bias' : 'Neutral';
  $('inventory-bias-pill').textContent = bias;
  $('inventory-panel-live').innerHTML = [
    stackItem('USDT Balance', num(usdt.available_balance || 0)),
    stackItem('Token Balance', `${base.asset || 'Base'} ${num(base.available_balance || 0)}`),
    stackItem('Target Token %', `${num(target * 100)}%`),
    stackItem('Current Ratio', `${num(current * 100)}%`),
    stackItem('Token Imbalance', `${num(skew)}%`),
    stackItem('Balance Bias', bias),
    stackItem('Capital At Risk', money(exposure)),
    stackItem('Max Capital At Risk', money(maxExposure))
  ].join('');
  const bar = $('inventory-ratio-bar');
  if (bar) bar.style.width = `${Math.max(0, Math.min(100, current * 100))}%`;
  setText('portfolio-value', money(state.data.exchangeBalances?.[selectedExchangeName()]?.portfolio_value || 0));
  setText('inventory-ratio-value', `${num(current * 100)}%`);
  setText('inventory-gauge-value', `${num(current * 100)}%`);
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
    stackItem('Market Stream Health', health.websocket?.status || 'not checked'),
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
    ${stackItem('Market Stream Status', ws)}
    ${stackItem('Account Stream Status', privateWs)}
    ${stackItem('API Key', account?.api_key_masked || 'not configured')}
    ${stackItem('Last Successful Test', account?.last_success_at || 'never')}
    ${stackItem('Last Failure', account?.last_failure_at || 'none')}
    ${stackItem('Last Error', account?.last_error_message || 'none')}
    ${stackItem('Portfolio Value', money(balancePayload.portfolio_value || 0))}
    ${stackItem('Token Balance %', `${num((balancePayload.inventory_ratio || 0) * 100)}%`)}
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

function currentSpreadBps() {
  const analytics = latestAnalytics();
  const analyticSpread = Number(analytics?.spread?.spread_bps || analytics?.spread_bps || 0);
  if (analyticSpread) return analyticSpread;
  const { bid, ask } = bestBidAsk(latestOrderbook());
  const mid = bid && ask ? (bid + ask) / 2 : 0;
  return mid ? ((ask - bid) / mid) * 10000 : 0;
}

function currentMidPrice() {
  const analytics = latestAnalytics();
  const analyticMid = Number(analytics?.spread?.mid || analytics?.mid || 0);
  if (analyticMid) return analyticMid;
  const { bid, ask } = bestBidAsk(latestOrderbook());
  return bid && ask ? (bid + ask) / 2 : 0;
}

function latestOrderbook() {
  const makerBook = state.data.engines?.['market-maker-engine']?.runtime?.latest_orderbook || {};
  const dataBook = state.data.engines?.['market-data-engine']?.runtime?.latest_orderbook || {};
  return Object.values(makerBook)[0] || Object.values(dataBook)[0] || null;
}

function latestAnalytics() {
  const makerAnalytics = state.data.engines?.['market-maker-engine']?.runtime?.latest_analytics || {};
  const dataAnalytics = state.data.engines?.['market-data-engine']?.runtime?.latest_analytics || {};
  return Object.values(makerAnalytics)[0] || Object.values(dataAnalytics)[0] || null;
}

function bestBidAsk(book) {
  const bids = Array.isArray(book?.bids) ? book.bids.map(levelParts) : [];
  const asks = Array.isArray(book?.asks) ? book.asks.map(levelParts) : [];
  const bestBid = bids.reduce((best, item) => item.price > best.price ? item : best, { price: 0, size: 0 });
  const bestAsk = asks.reduce((best, item) => !best.price || item.price < best.price ? item : best, { price: 0, size: 0 });
  const depth = [...bids.slice(0, 5), ...asks.slice(0, 5)].reduce((total, item) => total + item.price * item.size, 0);
  return { bid: bestBid.price, ask: bestAsk.price, bidSize: bestBid.size, askSize: bestAsk.size, depth, symbol: book?.symbol };
}

function levelParts(level) {
  if (Array.isArray(level)) return { price: Number(level[0] || 0), size: Number(level[1] || 0) };
  return { price: Number(level?.price || 0), size: Number(level?.size || level?.quantity || 0) };
}

function counter(name) {
  return Number(state.data.engines?.['market-maker-engine']?.runtime?.metrics?.counters?.[name] || 0);
}

function exchangeFromOrder(order) {
  const id = String(order.exchange_order_id || order.client_order_id || '');
  if (id.includes('paper')) return 'paper';
  if (id.includes('coinstore') || order.client_order_id?.includes('mm-')) return 'coinstore';
  return order.exchange || 'unknown';
}

function orderAge(createdAt) {
  const ts = Date.parse(createdAt || '');
  if (!Number.isFinite(ts)) return '';
  const seconds = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m`;
}

function compareValues(a, b, direction) {
  const left = Number.isFinite(Number(a)) ? Number(a) : String(a || '');
  const right = Number.isFinite(Number(b)) ? Number(b) : String(b || '');
  if (left < right) return direction === 'asc' ? -1 : 1;
  if (left > right) return direction === 'asc' ? 1 : -1;
  return 0;
}

function isConnected(status) {
  const value = String(status || '').toLowerCase();
  return value.includes('connected') || value.includes('healthy') || value === 'ok';
}

function coinstoreAccount() {
  return (state.data.exchangeIntegrations || []).find((item) => item.exchange_name === 'coinstore')?.accounts?.[0] || null;
}

function selectedExchangeName() {
  return String($('coinstore-form')?.exchange_name?.value || state.mmCampaign.exchange || 'coinstore').trim().toLowerCase();
}

function selectedExchangeIntegration() {
  return (state.data.exchangeIntegrations || []).find((item) => item.exchange_name === selectedExchangeName()) || null;
}

function selectedExchangeAccount() {
  return selectedExchangeIntegration()?.accounts?.[0] || null;
}

function setChoiceActive(selector, activeButton) {
  document.querySelectorAll(selector).forEach((button) => {
    button.classList.toggle('active', button === activeButton);
    button.classList.toggle('secondary', button !== activeButton);
  });
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
  setExpertMode(state.expertMode);
  $('api-base').value = state.apiBase;
  $('ws-url').value = state.wsUrl;
  $('bearer-token').value = state.token;
  $('coinstore-form').exchange_name.value = state.mmCampaign.exchange || 'coinstore';
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
  document.querySelectorAll('[data-mm-command]').forEach((button) => button.addEventListener('click', async () => {
    try {
      setPending(`mm:${button.dataset.mmCommand}`, true, button);
      await sendStrategy(button.dataset.mmCommand);
    } catch (error) {
      logEvent('strategy_command_failed', { command: button.dataset.mmCommand, error: error.message });
    } finally {
      setPending(`mm:${button.dataset.mmCommand}`, false, button);
    }
  }));
  $('campaign-edit').addEventListener('click', () => switchPage('launch-mm'));
  $('campaign-analytics').addEventListener('click', () => switchPage('monitor-mm'));
  $('campaign-sync').addEventListener('click', () => $('coinstore-sync').click());
  $('onboarding-resume').addEventListener('click', () => {
    state.onboardingResumeLater = false;
    localStorage.setItem('ops.onboardingResumeLater', 'false');
    switchPage('launch-mm');
    renderOnboardingWizard();
  });
  $('onboarding-later').addEventListener('click', () => {
    state.onboardingResumeLater = true;
    localStorage.setItem('ops.onboardingResumeLater', 'true');
    renderOnboardingWizard();
  });
  $('template-cards').addEventListener('click', (event) => {
    const button = event.target.closest('[data-template]');
    if (!button || button.disabled) return;
    applyTemplate(button.dataset.template);
  });
  $('launch-stepper').addEventListener('click', (event) => {
    const button = event.target.closest('[data-launch-step]');
    if (!button) return;
    setLaunchStep(Number(button.dataset.launchStep));
  });
  document.querySelectorAll('[data-launch-next]').forEach((button) => button.addEventListener('click', () => setLaunchStep(state.launchStep + 1)));
  document.querySelectorAll('[data-launch-prev]').forEach((button) => button.addEventListener('click', () => setLaunchStep(state.launchStep - 1)));
  $('recommendations-list').addEventListener('click', (event) => {
    const action = event.target.closest('[data-recommendation-action]');
    const card = event.target.closest('[data-recommendation]');
    if (!action || !card) return;
    handleRecommendation(card.dataset.recommendation, action.dataset.recommendationAction);
  });
  $('mm-start-large').addEventListener('click', async (event) => {
    try {
      setPending('mm:start-large', true, event.target);
      await sendStrategy('start');
    } catch (error) {
      logEvent('strategy_command_failed', { command: 'start', error: error.message });
    } finally {
      setPending('mm:start-large', false, event.target);
    }
  });
  $('campaign-name').addEventListener('input', (event) => { state.mmCampaign.name = event.target.value; persistCampaign(); renderMarketMakingCampaign(); renderReadiness(); });
  $('mm-pair-select').addEventListener('change', (event) => { state.mmCampaign.pair = event.target.value; persistCampaign(); renderMarketMakingCampaign(); renderReadiness(); });
  $('mm-budget').addEventListener('input', (event) => { state.mmCampaign.budget = Number(event.target.value || 0); persistCampaign(); renderMarketMakingCampaign(); renderReadiness(); });
  $('coinstore-form').exchange_name.addEventListener('change', (event) => { state.mmCampaign.exchange = event.target.value.trim().toLowerCase(); persistCampaign(); refreshExchangeBalances().then(renderAll); });
  [['current-price', 'currentPrice'], ['price-floor', 'priceFloor'], ['price-ceiling', 'priceCeiling'], ['preferred-zone-min', 'preferredMin'], ['preferred-zone-max', 'preferredMax']].forEach(([id, key]) => {
    $(id).addEventListener('input', (event) => {
      state.mmCampaign[key] = Number(event.target.value || 0);
      persistCampaign();
      renderMarketMakingCampaign();
      renderReadiness();
    });
  });
  document.querySelectorAll('[data-budget]').forEach((button) => button.addEventListener('click', () => {
    state.mmCampaign.budget = Number(button.dataset.budget || 0);
    $('mm-budget').value = String(state.mmCampaign.budget);
    setChoiceActive('[data-budget]', button);
    persistCampaign();
    renderMarketMakingCampaign();
    renderReadiness();
  }));
  document.querySelectorAll('[data-risk-level]').forEach((button) => button.addEventListener('click', () => {
    state.mmCampaign.riskLevel = button.dataset.riskLevel;
    setChoiceActive('[data-risk-level]', button);
    persistCampaign();
    renderMarketMakingCampaign();
    renderReadiness();
  }));
  $('target-daily-volume').addEventListener('input', (event) => { state.mmCampaign.targetDailyVolume = Number(event.target.value || 0); persistCampaign(); renderMarketMakingCampaign(); });
  document.querySelectorAll('[data-daily-volume]').forEach((button) => button.addEventListener('click', () => {
    state.mmCampaign.targetDailyVolume = Number(button.dataset.dailyVolume || 0);
    $('target-daily-volume').value = String(state.mmCampaign.targetDailyVolume);
    setChoiceActive('[data-daily-volume]', button);
    persistCampaign();
    renderMarketMakingCampaign();
  }));
  $('expert-mode-toggle').addEventListener('change', (event) => setExpertMode(event.target.checked));
  document.querySelectorAll('[data-monitor-tab]').forEach((button) => button.addEventListener('click', () => {
    state.monitorTab = button.dataset.monitorTab;
    document.querySelectorAll('[data-monitor-tab]').forEach((item) => item.classList.toggle('active', item === button));
    document.querySelectorAll('.monitor-panel').forEach((panel) => panel.classList.toggle('active', panel.id === `monitor-${state.monitorTab}`));
  }));
  $('orders-prev').addEventListener('click', () => changePage('orders', -1));
  $('orders-next').addEventListener('click', () => changePage('orders', 1));
  $('orders-search').addEventListener('input', () => { state.pagination.orders.page = 0; renderOrders(); });
  $('orders-status-filter').addEventListener('change', () => { state.pagination.orders.page = 0; renderOrders(); });
  $('orders-side-filter').addEventListener('change', () => { state.pagination.orders.page = 0; renderOrders(); });
  $('orders-page-size').addEventListener('change', (event) => { state.pagination.orders.page = 0; state.pagination.orders.size = Number(event.target.value || 50); renderOrders(); });
  document.querySelectorAll('[data-sort-orders]').forEach((header) => header.addEventListener('click', () => {
    const key = header.dataset.sortOrders;
    state.orderSort = { key, direction: state.orderSort.key === key && state.orderSort.direction === 'desc' ? 'asc' : 'desc' };
    renderOrders();
  }));
  $('trades-prev').addEventListener('click', () => changePage('trades', -1));
  $('trades-next').addEventListener('click', () => changePage('trades', 1));
  $('clear-log').addEventListener('click', () => { state.lastEvents = []; $('event-log').textContent = ''; });
  renderAll();
  refreshRest();
  connectWebSocket();
  setInterval(refreshRest, 5000);
  setInterval(() => { if (state.token) refreshCoinstore().then(renderCoinstore); }, 30000);
}

document.addEventListener('DOMContentLoaded', init);
