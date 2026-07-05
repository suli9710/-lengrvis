"""Session and creation JavaScript for the Admin workspace."""
# ruff: noqa: E501
ADMIN_SCRIPT_CORE = r"""    const $ = (id) => document.getElementById(id);
    const planDefaults = {
      free: {label: 'Free', devices: 1, note: '免费或内部测试，默认 1 台设备。'},
      pro: {label: 'Pro', devices: 2, note: '个人专业版，默认 2 台设备。'},
      max: {label: 'Max', devices: 5, note: '高阶或小团队试点，默认 5 台设备。'},
    };
    const terminalStatuses = new Set(['canceled', 'expired', 'revoked']);
    const statusRank = {active: 0, trialing: 1, past_due: 2, canceled: 3, expired: 4, revoked: 5};
    const state = {
      items: [],
      filtered: [],
      selectedKeyHash: null,
      renewItem: null,
      danger: null,
      plan: 'pro',
      pendingKey: '',
      pendingRecord: null,
    };
    function cookie(name) {
      return document.cookie.split('; ').find(v => v.startsWith(name + '='))?.split('=').slice(1).join('=') || '';
    }
    async function api(path, options = {}) {
      const headers = Object.assign({'Content-Type': 'application/json'}, options.headers || {});
      const csrf = cookie('lengrvis_admin_csrf');
      if (csrf) headers['X-Lengrvis-Admin-Csrf'] = decodeURIComponent(csrf);
      const res = await fetch(path, Object.assign({}, options, {headers}));
      const text = await res.text();
      let body = {};
      try { body = text ? JSON.parse(text) : {}; } catch { body = {detail: text}; }
      if (!res.ok) throw new Error(body?.error?.message || body?.detail || ('HTTP ' + res.status));
      return body;
    }
    function setMessage(id, text, kind = '') {
      const el = $(id);
      el.textContent = text || '';
      el.className = 'message' + (kind ? ' ' + kind : '');
    }
    function addDaysIso(days, baseIso) {
      const now = Date.now();
      const parsedBase = baseIso ? Date.parse(baseIso) : NaN;
      const base = Number.isFinite(parsedBase) ? Math.max(parsedBase, now) : now;
      const date = new Date(base + Number(days) * 24 * 60 * 60 * 1000);
      date.setMilliseconds(0);
      return date.toISOString();
    }
    function dateInputToIso(value) {
      if (!value) return null;
      const date = new Date(value + 'T23:59:59.000Z');
      return Number.isNaN(date.getTime()) ? null : date.toISOString();
    }
    function expiryFromControls(selectId, dateId, baseIso = null) {
      const preset = $(selectId).value;
      if (preset === 'none') return {expires_at: null, renews_at: null, label: '长期有效'};
      if (preset === 'custom') {
        const expires = dateInputToIso($(dateId).value);
        if (!expires) throw new Error('请选择自定义到期日期。');
        return {expires_at: expires, renews_at: expires, label: formatDate(expires)};
      }
      const days = Number(preset);
      if (!Number.isFinite(days) || days <= 0) throw new Error('有效期选项无效。');
      const expires = addDaysIso(days, baseIso);
      const baseNote = baseIso ? '，从当前到期日或今天较晚者起算' : '';
      return {expires_at: expires, renews_at: expires, label: days + ' 天后（' + formatDate(expires) + baseNote + '）'};
    }
    function updateExpiryPreview(selectId, dateWrapId, dateId, previewId, baseIso = null) {
      const custom = $(selectId).value === 'custom';
      $(dateWrapId).classList.toggle('hidden', !custom);
      try {
        const expiry = expiryFromControls(selectId, dateId, baseIso);
        $(previewId).textContent = '将设置为：' + expiry.label;
      } catch (err) { // broad-exception-boundary
        $(previewId).textContent = err.message || '';
      }
    }
    function formatDate(value) {
      if (!value) return '未设置';
      const date = new Date(value);
      return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString('zh-CN');
    }
    function showAuthed(authed) {
      $('loginPanel').classList.toggle('hidden', authed);
      $('loginShell').classList.toggle('hidden', authed);
      $('dashboard').classList.toggle('hidden', !authed);
      $('logout').classList.toggle('hidden', !authed);
    }
    async function checkSession() {
      const session = await api('/api/admin/session', {method: 'GET'});
      if (!session.configured) {
        setMessage('loginMessage', '服务器尚未配置管理员认证。', 'error');
        $('login').disabled = true;
      }
      showAuthed(session.authenticated);
      if (session.authenticated) await loadSubscriptions();
    }
    async function login() {
      setMessage('loginMessage', '');
      try {
        await api('/api/admin/login', {method: 'POST', body: JSON.stringify({password: $('password').value})});
        $('password').value = '';
        showAuthed(true);
        await loadSubscriptions();
      } catch (err) { // broad-exception-boundary
        setMessage('loginMessage', err.message, 'error');
      }
    }
    async function logout() {
      await api('/api/admin/logout', {method: 'POST'});
      showAuthed(false);
    }
    function selectPlan(plan) {
      state.plan = planDefaults[plan] ? plan : 'pro';
      for (const button of $('planSegment').querySelectorAll('button')) {
        button.classList.toggle('active', button.dataset.plan === state.plan);
      }
      $('maxDevices').value = String(planDefaults[state.plan].devices);
      $('planPreview').textContent = planDefaults[state.plan].note;
    }
    async function createKey() {
      setMessage('createMessage', '');
      $('newKeyWrap').classList.add('hidden');
      $('ackKeySaved').checked = false;
      let expiry;
      try {
        expiry = expiryFromControls('expiresPreset', 'expiresDate');
      } catch (err) { // broad-exception-boundary
        setMessage('createMessage', err.message, 'error');
        return;
      }
      const payload = {
        plan: state.plan,
        subscription_id: $('subscriptionId').value,
        status: $('status').value,
        subject: $('subject').value,
        seats: Number($('seats').value || 1),
        max_devices: Number($('maxDevices').value || 1),
        expires_at: expiry.expires_at,
        renews_at: expiry.renews_at,
        order_ref: $('orderRef').value,
        cancel_at_period_end: $('cancelAtPeriodEnd').checked,
      };
      try {
        const result = await api('/api/admin/subscriptions', {method: 'POST', body: JSON.stringify(payload)});
        state.pendingKey = result.activation_key || '';
        state.pendingRecord = result.record || null;
        $('newKey').textContent = state.pendingKey;
        $('newKeyWrap').classList.remove('hidden');
        setMessage('createMessage', '授权码已创建。这个值只显示一次，请复制或下载交接文本。', 'ok');
        state.selectedKeyHash = result.record?.key_hash || state.selectedKeyHash;
        await loadSubscriptions();
      } catch (err) { // broad-exception-boundary
        setMessage('createMessage', err.message, 'error');
      }
    }
"""
