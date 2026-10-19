/* Opt-in real Electron/Chromium acceptance; owns its server, windows and profile. */
const { app, BrowserWindow } = require('electron');
const { createServer } = require('node:http');
const { randomBytes, createHmac } = require('node:crypto');
const { mkdtempSync, writeFileSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { join, resolve } = require('node:path');
const assert = require('node:assert/strict');

if (process.env.MAVRIS_NATIVE_APP_ACCEPTANCE !== '1') {
  console.error('Set MAVRIS_NATIVE_APP_ACCEPTANCE=1 for isolated native browser acceptance.');
  app.exit(2);
} else {
  const scratch = mkdtempSync(join(tmpdir(), 'lengrvis-butler-browser-'));
  app.setPath('userData', scratch);
  app.disableHardwareAcceleration();
  process.env.LENGRVIS_BROWSER_HOST_ALLOW_PRIVATE_NETWORK = '1';
  void app.whenReady().then(async () => {
    const { BrowserHost } = require(resolve(__dirname, '../dist/main/browserHost.js'));
    const { BrowserExecutionGrantVerifier } = require(resolve(__dirname, '../dist/main/browserExecutionGrant.js'));
    let submissions = [];
    const submissionEntries = [];
    const server = createServer((request, response) => {
      if (request.method === 'POST') {
        let body = '';
        request.on('data', chunk => { body += chunk; });
        request.on('end', () => {
          submissionEntries.push([...new URLSearchParams(body).entries()]);
          submissions.push(Object.fromEntries(new URLSearchParams(body)));
          response.setHeader('Content-Type', 'text/html; charset=utf-8');
          response.end('<h1 id="receipt">Submission received</h1>');
        });
      } else {
        response.setHeader('Content-Type', 'text/html; charset=utf-8');
        response.end('<!doctype html><meta charset="utf-8"><title>Owned test form</title>' +
          '<form id="form" method="post" action="/submitted"><input name="amount" id="amount" type="hidden" value="100">' +
          '<textarea id="notes" name="notes">original</textarea><button type="submit">Submit sample</button></form>' +
          '<p id="result">Ready</p><script>notes.addEventListener("input",()=>result.textContent=notes.value);</script>');
      }
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const origin = `http://127.0.0.1:${server.address().port}`;
    const window = new BrowserWindow({ show: false, webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false } });
    const host = new BrowserHost(() => window);
    const key = randomBytes(32).toString('hex');
    const verifier = new BrowserExecutionGrantVerifier(() => key);
    const base = { schema: 'browser-execution/v1', session_id: 'fixture-session', task_id: 'fixture-task',
      account_id: 'fixture-account', profile_key: 'fixture-scope', allowed_origins: [origin], scope_versions: { 'fixture-scope': 1 } };
    async function command(operation, action, fingerprint = '', extra = {}) {
      const claim = { ...base, ...extra, operation, action, nonce: randomBytes(16).toString('hex'), expires_at: Date.now() + 30000,
        approval_id: fingerprint ? 'fixture-approval' : '', expected_fingerprint: fingerprint };
      const payload = Buffer.from(JSON.stringify(claim)).toString('base64');
      const signature = createHmac('sha256', key).update(`browser-execution/v1:${payload}`).digest('hex');
      return host.performAuthorized(verifier.consume({ payload, signature }));
    }
    const report = { ok: false, checks: [] };
    try {
      assert.equal((await command('open', { kind: 'open', url: `${origin}/form` })).ok, true);
      assert.equal(host.getSnapshot().visible, false);
      const text = '跨应用正文 😀\n第二行保持完整';
      const fill = { kind: 'fill', url: `${origin}/form`, fields: { '#notes': text } };
      const preview = await command('inspect', fill);
      assert.equal(preview.ok, true);
      assert.equal((await command('act', fill, preview.inspection.fingerprint)).ok, true);
      const observed = await command('act', { kind: 'observe', url: `${origin}/form`, selector: '#notes' });
      assert.equal(observed.ok, true);
      assert.ok(observed.observation.text.includes(text));
      report.checks.push('persistent_unicode_readback');
      const previousTask = base.task_id;
      base.task_id = 'fixture-continuation';
      assert.equal((await command('adopt', { kind: 'observe', url: `${origin}/form` }, '', { previous_task_id: 'foreign-task' })).ok, false);
      assert.equal((await command('adopt', { kind: 'observe', url: `${origin}/form` }, '', { previous_task_id: previousTask })).ok, true);
      assert.equal(host.getSnapshot().sessions.length, 1);
      report.checks.push('adoption_preserves_tab_and_rejects_wrong_owner');
      const missing = await command('act', { kind: 'wait', url: `${origin}/form`, selector: '#missing', timeout_ms: 100 });
      assert.equal(missing.ok, false);
      report.checks.push('missing_selector_wait_fails');
      const submit = { kind: 'submit', url: `${origin}/form`, selector: '#form' };
      const beforeChange = await command('inspect', submit);
      const entry = host.sessions.get(base.session_id);
      await entry.container.view.webContents.executeJavaScript('document.querySelector("#amount").value="999"; JSON.stringify=()=>"spoof"; void 0;');
      await assert.rejects(command('act', submit, beforeChange.inspection.fingerprint));
      assert.equal(submissions.length, 0);
      report.checks.push('main_world_and_hidden_amount_change_blocked');
      await entry.container.view.webContents.executeJavaScript('document.querySelector("#amount").value="100";');
      const refreshed = await command('inspect', submit);
      assert.equal((await command('act', submit, refreshed.inspection.fingerprint)).ok, true);
      const waited = await command('act', { kind: 'wait', url: `${origin}/submitted`, selector: '#receipt', timeout_ms: 3000 });
      assert.equal(waited.ok, true);
      assert.equal(submissions.length, 1);
      assert.deepEqual(submissions[0], { amount: '100', notes: text.replace(/\n/g, '\r\n') });
      report.checks.push('one_matching_submission_and_receipt');
      assert.equal((await command('act', { kind: 'navigate', url: `${origin}/form` })).ok, true);
      await entry.container.view.webContents.executeJavaScript('notes.required=true;');
      const combined = { kind: 'submit', url: `${origin}/form`, selector: '#form', fields: { notes: text } };
      await assert.rejects(command('inspect', { ...combined, fields: { '#amount': '999' } }));
      await assert.rejects(command('inspect', { ...combined, fields: { notes: 'one', '#notes': 'two' } }));
      report.checks.push('combined_rejects_hidden_fields_and_alias_conflicts');
      const invalid = { ...combined, fields: { notes: '' } };
      const invalidPreview = await command('inspect', invalid);
      const rejected = await command('act', invalid, invalidPreview.inspection.fingerprint);
      assert.equal(rejected.ok, false);
      assert.equal(rejected.commit_state, 'not_submitted');
      assert.equal(await entry.container.view.webContents.executeJavaScript('notes.value'), 'original');
      assert.equal(submissions.length, 1);
      report.checks.push('native_validation_restores_form_without_submission');
      await entry.container.view.webContents.executeJavaScript('form.method="get";');
      await assert.rejects(command('inspect', combined));
      await entry.container.view.webContents.executeJavaScript('form.method="post";');
      for (const attributes of ['form.enctype="multipart/form-data"', 'form.acceptCharset="windows-1252"']) {
        await entry.container.view.webContents.executeJavaScript(attributes);
        await assert.rejects(command('inspect', combined));
        await entry.container.view.webContents.executeJavaScript('form.enctype="application/x-www-form-urlencoded";form.acceptCharset="UTF-8";');
      }
      report.checks.push('combined_requires_explicit_native_post');
      const changed = await command('inspect', combined);
      assert.ok(JSON.stringify(changed.inspection.preview).includes('跨应用正文'));
      await entry.container.view.webContents.executeJavaScript('amount.value="999";');
      await assert.rejects(command('act', combined, changed.inspection.fingerprint));
      await entry.container.view.webContents.executeJavaScript('amount.value="100"; notes.addEventListener("input",()=>amount.value="999"); form.addEventListener("submit",()=>amount.value="888");');
      await entry.container.view.webContents.executeJavaScript('form.addEventListener("formdata", event => event.formData.set("amount", "777"), { once: true });');
      const peer = { session_id: 'fixture-peer', task_id: 'fixture-peer-task' };
      assert.equal((await command('open', { kind: 'open', url: `${origin}/form` }, '', peer)).ok, true);
      assert.equal(host.sessions.get(peer.session_id).container.view.webContents.session, entry.container.view.webContents.session);
      const poisoned = await command('inspect', combined);
      const blocked = await command('act', combined, poisoned.inspection.fingerprint);
      assert.equal(blocked.ok, false);
      assert.equal(blocked.commit_state, 'not_submitted');
      assert.equal(submissions.length, 1);
      report.checks.push('actual_formdata_payload_change_blocked_before_network');
      assert.equal((await command('act', { kind: 'observe', url: `${origin}/form`, selector: '#notes' }, '', peer)).ok, true);
      report.checks.push('shared_profile_tabs_keep_independent_request_guards');
      const restored = await command('act', { kind: 'navigate', url: `${origin}/form` });
      assert.equal(restored.ok, true, restored.error);
      await entry.container.view.webContents.executeJavaScript('notes.addEventListener("input",()=>amount.value="999"); form.addEventListener("submit",()=>amount.value="888");');
      const concrete = await command('inspect', combined);
      assert.equal((await command('act', combined, concrete.inspection.fingerprint)).commit_state, 'submitted');
      assert.equal((await command('act', { kind: 'wait', url: `${origin}/submitted`, selector: '#receipt', timeout_ms: 3000 })).ok, true);
      assert.equal(submissions.length, 2);
      assert.deepEqual(submissions[1], { amount: '100', notes: text.replace(/\n/g, '\r\n') });
      report.checks.push('combined_confirmed_values_submit_once_without_application_handlers');
      assert.equal((await command('act', { kind: 'navigate', url: `${origin}/form` })).ok, true);
      const extraControls = '<input type="hidden" name="csrf_token" value="private-fixture-token">' +
        '<input type="hidden" name="_charset_" value="placeholder">' +
        '<fieldset disabled><legend><input name="legend" value="kept"></legend><input name="disabled" value="omitted"></fieldset>' +
        '<input name="choice" type="checkbox" value="甲" checked><input name="choice" type="checkbox" value="乙" checked>' +
        '<input name="unchecked" type="checkbox" value="omitted">' +
        '<select name="selection"><option value="a">A</option><option selected value="b">B</option></select>';
      await entry.container.view.webContents.executeJavaScript(`form.insertAdjacentHTML("beforeend", ${JSON.stringify(extraControls)});`);
      const ordinary = await command('inspect', combined);
      assert.ok(!JSON.stringify(ordinary.inspection).includes('private-fixture-token'));
      assert.equal((await command('act', combined, ordinary.inspection.fingerprint)).commit_state, 'submitted');
      assert.equal((await command('act', { kind: 'wait', url: `${origin}/submitted`, selector: '#receipt', timeout_ms: 3000 })).ok, true);
      assert.equal(submissions.length, 3);
      assert.deepEqual(submissionEntries[2], [['amount', '100'], ['notes', text.replace(/\n/g, '\r\n')],
        ['csrf_token', 'private-fixture-token'], ['_charset_', 'UTF-8'], ['legend', 'kept'], ['choice', '甲'], ['choice', '乙'], ['selection', 'b']]);
      report.checks.push('native_control_encoding_and_private_csrf_preserved');
      const profile = entry.container.view.webContents.session;
      await profile.cookies.set({ url: origin, name: 'owned-test-cookie', value: 'ordinary-fixture' });
      await host.cancelScope(base.profile_key, 2);
      assert.equal((await profile.cookies.get({ url: origin })).length, 0);
      report.checks.push('scope_revocation_erases_profile');
      assert.equal((await command('open', { kind: 'open', url: `${origin}/form` })).ok, false);
      report.checks.push('outstanding_old_scope_grant_rejected');
      await host.eraseLocalPrivateData();
      report.checks.push('managed_profile_erased');
      report.ok = true;
    } catch (error) {
      report.error = String(error?.message || error).slice(0, 600);
    } finally {
      host.destroy();
      window.destroy();
      server.close();
      const target = resolve(__dirname, '../../.tmp/audit-fixes/butler-browser-native.json');
      writeFileSync(target, JSON.stringify(report, null, 2));
      console.log(JSON.stringify(report));
      app.exit(report.ok ? 0 : 1);
    }
  }).catch(error => { console.error(error?.message || 'Native acceptance failed'); app.exit(1); });
}
