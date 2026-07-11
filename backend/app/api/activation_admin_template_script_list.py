"""Subscription list JavaScript for the Admin workspace."""
# ruff: noqa: E501
ADMIN_SCRIPT_LIST = r"""    async function loadSubscriptions() {
      setMessage('listMessage', '加载中...');
      try {
        const result = await api('/api/admin/subscriptions', {method: 'GET'});
        state.items = result.items || [];
        renderMetrics();
        applyFilters();
        setMessage('listMessage', state.items.length ? '' : '暂无订阅记录。');
      } catch (err) { // broad-exception-boundary
        setMessage('listMessage', err.message, 'error');
      }
    }
    function applyFilters() {
      const query = $('searchBox').value.trim().toLowerCase();
      const plan = $('planFilter').value;
      const status = $('statusFilter').value;
      state.filtered = [...state.items]
        .filter(item => !plan || String(item.plan || '') === plan)
        .filter(item => !status || String(item.status || '') === status)
        .filter(item => !query || searchText(item).includes(query))
        .sort(compareSubscription);
      if (state.selectedKeyHash && !state.filtered.some(item => item.key_hash === state.selectedKeyHash)) {
        state.selectedKeyHash = null;
      }
      if (!state.selectedKeyHash && state.filtered.length) {
        state.selectedKeyHash = state.filtered[0].key_hash;
      }
      renderSubscriptions();
      renderDetail();
      if (!state.filtered.length && state.items.length) {
        setMessage('listMessage', '没有匹配的订阅。');
      }
    }
    function searchText(item) {
      const devices = (item.devices || []).flatMap(device => [
        device.license_id,
        device.device_label,
        device.server_device_ref_label,
        device.device_fingerprint_label,
        device.app_version,
      ]);
      return [
        item.plan,
        item.status,
        item.subscription_id,
        item.key_hash_prefix,
        item.subject,
        item.order_ref,
        ...devices,
      ].filter(Boolean).join(' ').toLowerCase();
    }
    function compareSubscription(left, right) {
      const leftRank = statusRank[String(left.status || '')] ?? 9;
      const rightRank = statusRank[String(right.status || '')] ?? 9;
      if (leftRank !== rightRank) return leftRank - rightRank;
      return String(right.updated_at || '').localeCompare(String(left.updated_at || ''));
    }
    function renderSubscriptions() {
      const list = $('subscriptions');
      list.textContent = '';
      for (const item of state.filtered) {
        const card = document.createElement('button');
        card.type = 'button';
        card.className = 'subscription-card' + (item.key_hash === state.selectedKeyHash ? ' active' : '');
        card.onclick = () => selectSubscription(item.key_hash);
        const body = document.createElement('div');
        const title = document.createElement('div');
        title.className = 'card-title';
        title.append(document.createTextNode(item.subscription_id || item.key_hash_prefix || '未命名订阅'));
        title.append(badge(displayPlan(item.plan), item.plan));
        title.append(badge(displayStatus(item.status), item.status));
        const meta = document.createElement('div');
        meta.className = 'card-meta';
        const metaParts = [
          item.subject ? '客户 ' + item.subject : '未填客户标签',
          item.order_ref ? '订单 ' + item.order_ref : '',
          '设备 ' + String(item.device_count || 0) + ' / ' + String(item.max_devices || 1),
          '到期 ' + formatDate(item.expires_at),
        ].filter(Boolean);
        for (const part of metaParts) {
          const pill = document.createElement('span');
          pill.className = 'meta-pill';
          pill.textContent = part;
          meta.append(pill);
        }
        body.append(title, meta);
        const right = document.createElement('div');
        right.className = 'mono muted';
        right.textContent = item.key_hash_prefix || '';
        card.append(body, right);
        list.append(card);
      }
    }
    function renderMetrics() {
      const items = state.items || [];
      const active = items.filter(item => ['active', 'trialing'].includes(String(item.status || ''))).length;
      const paid = items.filter(item => ['plus', 'pro'].includes(String(item.plan || ''))).length;
      const devices = items.reduce((sum, item) => sum + Number(item.device_count || 0), 0);
      $('metricTotal').textContent = String(items.length);
      $('metricActive').textContent = String(active);
      $('metricPaid').textContent = String(paid);
      $('metricDevices').textContent = String(devices);
    }
    function selectSubscription(keyHash) {
      state.selectedKeyHash = keyHash;
      cancelRenew();
      cancelDanger();
      renderSubscriptions();
      renderDetail();
    }
    function selectedItem() {
      return state.items.find(item => item.key_hash === state.selectedKeyHash) || null;
    }
"""
