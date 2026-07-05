"""Renewal and destructive-action JavaScript for the Admin workspace."""
# ruff: noqa: E501
ADMIN_SCRIPT_ACTIONS = r"""    async function renewSubscription(item) {
      state.renewItem = item;
      cancelDanger();
      $('renewTitle').textContent = '续期订阅 · ' + (item.subscription_id || item.key_hash_prefix || '');
      $('renewStatus').value = item.status || 'active';
      $('renewExpiresPreset').value = '30';
      $('renewExpiresDate').value = '';
      $('renewSeats').value = String(item.seats || 1);
      $('renewMaxDevices').value = String(item.max_devices || 1);
      $('renewCancelAtPeriodEnd').checked = Boolean(item.cancel_at_period_end);
      updateExpiryPreview('renewExpiresPreset', 'renewCustomExpiryWrap', 'renewExpiresDate', 'renewExpiryPreview', item.expires_at);
      $('renewPanel').classList.remove('hidden');
      $('renewPanel').scrollIntoView({behavior: 'smooth', block: 'nearest'});
    }
    function cancelRenew() {
      state.renewItem = null;
      $('renewPanel').classList.add('hidden');
    }
    async function submitRenewal() {
      const item = state.renewItem;
      if (!item) return;
      let expiry;
      try {
        expiry = expiryFromControls('renewExpiresPreset', 'renewExpiresDate', item.expires_at);
      } catch (err) { // broad-exception-boundary
        setMessage('listMessage', err.message, 'error');
        return;
      }
      try {
        await api('/api/admin/subscriptions/' + item.key_hash + '/renew', {
          method: 'POST',
          body: JSON.stringify({
            status: $('renewStatus').value,
            expires_at: expiry.expires_at,
            renews_at: expiry.renews_at,
            cancel_at_period_end: $('renewCancelAtPeriodEnd').checked,
            seats: Number($('renewSeats').value || item.seats || 1),
            max_devices: Number($('renewMaxDevices').value || item.max_devices || 1),
          }),
        });
        cancelRenew();
        await loadSubscriptions();
        setMessage('listMessage', '订阅已更新。', 'ok');
      } catch (err) { // broad-exception-boundary
        setMessage('listMessage', err.message, 'error');
      }
    }
    function openRevokeDanger(item) {
      cancelRenew();
      state.danger = {type: 'revoke', item};
      $('dangerTitle').textContent = '撤销授权码';
      $('dangerText').textContent = '将撤销订阅 ' + (item.subscription_id || item.key_hash_prefix || '') + '。如果已有激活设备，仍需发布 signed revocation manifest 才能让本地 license 降级。请输入“撤销”确认。';
      $('dangerReasonWrap').classList.remove('hidden');
      $('dangerConfirm').value = '';
      $('dangerConfirm').placeholder = '输入 撤销';
      $('confirmDanger').textContent = '确认撤销';
      $('dangerPanel').classList.remove('hidden');
      $('dangerPanel').scrollIntoView({behavior: 'smooth', block: 'nearest'});
    }
    function openUnbindDanger(device) {
      cancelRenew();
      const item = selectedItem();
      state.danger = {type: 'unbind', item, device};
      $('dangerTitle').textContent = '解绑设备';
      $('dangerText').textContent = '将解绑设备脱敏标签 ' + (device.device_label || '') + '。解绑后该席位可被新设备使用。请输入“解绑”确认。';
      $('dangerReasonWrap').classList.add('hidden');
      $('dangerConfirm').value = '';
      $('dangerConfirm').placeholder = '输入 解绑';
      $('confirmDanger').textContent = '确认解绑';
      $('dangerPanel').classList.remove('hidden');
      $('dangerPanel').scrollIntoView({behavior: 'smooth', block: 'nearest'});
    }
    function openDeleteDanger(item) {
      if (!isSubscriptionDeletable(item)) {
        setMessage('listMessage', deleteBlockReason(item), 'error');
        return;
      }
      cancelRenew();
      state.danger = {type: 'delete', item};
      $('dangerTitle').textContent = '删除订阅记录';
      $('dangerText').textContent = '将从后台列表删除订阅 ' + (item.subscription_id || item.key_hash_prefix || '') + '。这只清理无设备绑定的终态记录，不用于吊销已安装 license。请输入“删除”确认。';
      $('dangerReasonWrap').classList.add('hidden');
      $('dangerConfirm').value = '';
      $('dangerConfirm').placeholder = '输入 删除';
      $('confirmDanger').textContent = '确认删除';
      $('dangerPanel').classList.remove('hidden');
      $('dangerPanel').scrollIntoView({behavior: 'smooth', block: 'nearest'});
    }
    function cancelDanger() {
      state.danger = null;
      $('dangerPanel').classList.add('hidden');
    }
    async function confirmDanger() {
      if (!state.danger) return;
      const required = state.danger.type === 'revoke' ? '撤销' : state.danger.type === 'unbind' ? '解绑' : '删除';
      if ($('dangerConfirm').value.trim() !== required) {
        setMessage('listMessage', '确认文字不匹配，操作未执行。', 'error');
        return;
      }
      try {
        if (state.danger.type === 'revoke') {
          const item = state.danger.item;
          const result = await api('/api/admin/subscriptions/' + item.key_hash + '/revoke', {method: 'POST'});
          const ids = result?.record?.revoked_license_ids || [];
          cancelDanger();
          await loadSubscriptions();
          if (result?.record?.revocation_manifest_required) {
            setMessage('listMessage', '订阅授权码已撤销；仍需为 ' + ids.length + ' 个已激活设备发布 signed revocation manifest 或换发许可。', 'error');
          } else {
            setMessage('listMessage', '订阅授权码已撤销。', 'ok');
          }
        } else if (state.danger.type === 'unbind') {
          const device = state.danger.device;
          await api('/api/admin/devices/' + device.license_id, {method: 'DELETE'});
          cancelDanger();
          await loadSubscriptions();
          setMessage('listMessage', '设备已解绑。', 'ok');
        } else {
          const item = state.danger.item;
          await api('/api/admin/subscriptions/' + item.key_hash, {method: 'DELETE'});
          state.selectedKeyHash = null;
          cancelDanger();
          await loadSubscriptions();
          setMessage('listMessage', '订阅记录已删除。', 'ok');
        }
      } catch (err) { // broad-exception-boundary
        setMessage('listMessage', err.message, 'error');
      }
    }
    $('login').onclick = login;
    $('password').addEventListener('keydown', (event) => { if (event.key === 'Enter') login(); });
    $('logout').onclick = logout;
    $('createKey').onclick = createKey;
    $('copyKey').onclick = copyNewKey;
    $('downloadKey').onclick = downloadHandoff;
    $('refresh').onclick = loadSubscriptions;
    $('searchBox').oninput = applyFilters;
    $('planFilter').onchange = applyFilters;
    $('statusFilter').onchange = applyFilters;
    $('openRenew').onclick = () => { const item = selectedItem(); if (item) renewSubscription(item); };
    $('openRevoke').onclick = () => { const item = selectedItem(); if (item) openRevokeDanger(item); };
    $('openDelete').onclick = () => { const item = selectedItem(); if (item) openDeleteDanger(item); };
    $('cancelRenew').onclick = cancelRenew;
    $('submitRenew').onclick = submitRenewal;
    $('cancelDanger').onclick = cancelDanger;
    $('confirmDanger').onclick = confirmDanger;
    $('expiresPreset').onchange = () => updateExpiryPreview('expiresPreset', 'customExpiryWrap', 'expiresDate', 'expiryPreview');
    $('expiresDate').onchange = () => updateExpiryPreview('expiresPreset', 'customExpiryWrap', 'expiresDate', 'expiryPreview');
    $('renewExpiresPreset').onchange = () => {
      const item = state.renewItem;
      updateExpiryPreview('renewExpiresPreset', 'renewCustomExpiryWrap', 'renewExpiresDate', 'renewExpiryPreview', item?.expires_at || null);
    };
    $('renewExpiresDate').onchange = () => {
      const item = state.renewItem;
      updateExpiryPreview('renewExpiresPreset', 'renewCustomExpiryWrap', 'renewExpiresDate', 'renewExpiryPreview', item?.expires_at || null);
    };
    for (const button of $('planSegment').querySelectorAll('button')) {
      button.onclick = () => selectPlan(button.dataset.plan);
    }
    window.addEventListener('beforeunload', (event) => {
      if (state.pendingKey && !$('ackKeySaved').checked) {
        event.preventDefault();
        event.returnValue = '授权码只显示一次，请确认已经安全保存。';
      }
    });
    selectPlan('pro');
    updateExpiryPreview('expiresPreset', 'customExpiryWrap', 'expiresDate', 'expiryPreview');
    checkSession().catch(err => setMessage('loginMessage', err.message, 'error'));
"""
