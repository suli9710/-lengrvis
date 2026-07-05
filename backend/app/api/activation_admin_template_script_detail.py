"""Detail rendering JavaScript for the Admin workspace."""
# ruff: noqa: E501
ADMIN_SCRIPT_DETAIL = r"""    function renderDetail() {
      const item = selectedItem();
      $('detailEmpty').classList.toggle('hidden', Boolean(item));
      $('detailContent').classList.toggle('hidden', !item);
      $('detailStatusLabel').textContent = item ? displayStatus(item.status) : '未选择';
      if (!item) return;
      $('detailTitle').textContent = item.subscription_id || item.key_hash_prefix || '未命名订阅';
      $('detailSubtitle').textContent = [
        item.subject ? '客户 ' + item.subject : '未填写客户标签',
        item.order_ref ? '订单 ' + item.order_ref : '',
        'Key ' + (item.key_hash_prefix || ''),
      ].filter(Boolean).join(' · ');
      const badges = $('detailBadges');
      badges.textContent = '';
      badges.append(badge(displayPlan(item.plan), item.plan), badge(displayStatus(item.status), item.status));
      const detailGrid = $('detailGrid');
      detailGrid.textContent = '';
      detailGrid.append(
        detailCell('客户标签', item.subject || '未填写'),
        detailCell('订单备注', item.order_ref || '未填写'),
        detailCell('到期时间', formatDate(item.expires_at)),
        detailCell('续费时间', formatDate(item.renews_at)),
        detailCell('设备占用', String(item.device_count || 0) + ' / ' + String(item.max_devices || 1)),
        detailCell('Key 前缀', item.key_hash_prefix || ''),
      );
      renderDetailDevices(item);
      $('deviceSummary').textContent = String(item.device_count || 0) + ' / ' + String(item.max_devices || 1) + ' 台设备';
      $('openRenew').disabled = item.status === 'revoked';
      $('openRevoke').disabled = item.status === 'revoked';
      $('openDelete').disabled = !isSubscriptionDeletable(item);
      $('openDelete').title = deleteBlockReason(item);
      $('deleteHint').textContent = deleteBlockReason(item);
    }
    function isSubscriptionDeletable(item) {
      return terminalStatuses.has(String(item?.status || '')) && Number(item?.device_count || 0) === 0;
    }
    function deleteBlockReason(item) {
      if (!item) return '';
      if (!terminalStatuses.has(String(item.status || ''))) return '只能删除已取消、已过期或已撤销的订阅记录。';
      if (Number(item.device_count || 0) > 0) return '仍有设备绑定，请先完成撤销交接或解绑设备。';
      return '删除后台订阅记录。';
    }
    function detailCell(label, value) {
      const box = document.createElement('div');
      box.className = 'detail-cell';
      const span = document.createElement('span');
      span.textContent = label;
      const strong = document.createElement('strong');
      strong.textContent = value || '';
      box.append(span, strong);
      return box;
    }
    function renderDetailDevices(item) {
      const box = $('detailDevices');
      box.textContent = '';
      const devices = item.devices || [];
      if (!devices.length) {
        const empty = document.createElement('p');
        empty.className = 'field-note';
        empty.textContent = '暂无设备绑定。';
        box.append(empty);
        return;
      }
      for (const device of devices) {
        const row = document.createElement('div');
        row.className = 'device-row';
        const label = document.createElement('div');
        const title = document.createElement('strong');
        title.textContent = device.device_label + (device.app_version ? ' · ' + device.app_version : '');
        const meta = document.createElement('small');
        meta.className = 'device-meta';
        const profile = device.device_profile || {};
        const profileText = [profile.os, profile.arch].filter(Boolean).join(' / ');
        meta.textContent = [
          device.server_device_ref_label ? '服务端引用 ' + device.server_device_ref_label : '',
          device.device_fingerprint_label ? '指纹 ' + device.device_fingerprint_label : '未提交设备指纹',
          profileText,
          profile.signal_count !== undefined ? '信号 ' + String(profile.signal_count) : '',
          device.license_id ? 'license ' + device.license_id : '',
        ].filter(Boolean).join(' · ');
        label.append(title, meta);
        if (device.risk_label === 'legacy_device_id_only') {
          const risk = document.createElement('span');
          risk.className = 'device-risk';
          risk.textContent = '旧版绑定';
          label.append(risk);
        }
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.textContent = '解绑';
        btn.className = 'compact';
        btn.onclick = () => openUnbindDanger(device);
        row.append(label, btn);
        box.append(row);
      }
    }
    function displayPlan(plan) {
      const labels = {free: '免费版', pro: '专业版', max: '旗舰版'};
      return labels[String(plan || '').toLowerCase()] || plan || '';
    }
    function displayStatus(status) {
      const labels = {
        active: '生效中',
        trialing: '试用中',
        past_due: '逾期',
        canceled: '已取消',
        expired: '已过期',
        revoked: '已撤销',
      };
      return labels[String(status || '').toLowerCase()] || status || '';
    }
    function badge(text, cls) {
      const span = document.createElement('span');
      span.className = 'badge ' + String(cls || '');
      span.textContent = text || '';
      return span;
    }
    async function copyNewKey() {
      const value = state.pendingKey || $('newKey').textContent || '';
      if (!value) return;
      try {
        await navigator.clipboard.writeText(value);
        setMessage('createMessage', '已复制到剪贴板。', 'ok');
      } catch {
        setMessage('createMessage', '复制失败，请手动选中授权码。', 'error');
      }
    }
    function downloadHandoff() {
      if (!state.pendingKey || !state.pendingRecord) return;
      const record = state.pendingRecord;
      const text = [
        'Lengrvis 订阅授权码交接',
        '订阅 ID: ' + (record.subscription_id || ''),
        '套餐: ' + displayPlan(record.plan),
        '状态: ' + displayStatus(record.status),
        '到期: ' + formatDate(record.expires_at),
        '最大设备数: ' + String(record.max_devices || 1),
        '',
        '授权码:',
        state.pendingKey,
        '',
        '注意: 授权码只应通过批准的安全渠道交付，后台刷新后不会再次显示。',
      ].join('\n');
      const blob = new Blob([text], {type: 'text/plain;charset=utf-8'});
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = (record.subscription_id || 'lengrvis-activation') + '.txt';
      document.body.append(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      setMessage('createMessage', '交接文本已下载。', 'ok');
    }
"""
