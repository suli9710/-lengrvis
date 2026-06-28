"""HTML template for the subscription activation Admin workspace."""
# ruff: noqa: E501

ADMIN_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Lengrvis 激活管理后台</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #edf0f3;
      --panel: #fbfcfd;
      --panel-strong: #ffffff;
      --line: #cfd6de;
      --line-soft: #e7ebef;
      --text: #151b23;
      --muted: #66717f;
      --muted-2: #8995a3;
      --accent: #116a5b;
      --accent-dark: #0b4c41;
      --accent-soft: #dcefe9;
      --danger: #b42318;
      --danger-soft: #fff0ee;
      --warn: #8a5a00;
      --warn-soft: #fff4d6;
      --ok: #087443;
      --ok-soft: #e1f4eb;
      --ink: #111820;
      --shadow: 0 16px 40px rgba(16, 24, 40, .08);
      --radius: 8px;
      font-family: "Microsoft YaHei UI", "Aptos", "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background:
        linear-gradient(rgba(21, 27, 35, .035) 1px, transparent 1px),
        linear-gradient(90deg, rgba(21, 27, 35, .03) 1px, transparent 1px),
        var(--bg);
      background-size: 28px 28px;
      color: var(--text);
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      min-height: 68px;
      padding: 0 28px;
      border-bottom: 1px solid #27313c;
      background: var(--ink);
      color: #f8fafc;
    }
    .brand { display: flex; align-items: center; gap: 12px; min-width: 0; }
    .brand-mark {
      display: grid;
      place-items: center;
      width: 34px;
      height: 34px;
      border: 1px solid rgba(255, 255, 255, .24);
      border-radius: 7px;
      background: #18302c;
      color: #9ee2cf;
      font-weight: 800;
    }
    .brand-copy { min-width: 0; }
    h1 { margin: 0; font-size: 18px; font-weight: 760; letter-spacing: 0; }
    h2 { margin: 0; font-size: 15px; font-weight: 760; letter-spacing: 0; }
    h3 { margin: 0; font-size: 14px; font-weight: 820; letter-spacing: 0; }
    .eyebrow { margin-top: 2px; color: #9aa8b6; font-size: 12px; }
    main { max-width: 1480px; margin: 0 auto; padding: 18px 18px 44px; }
    .hidden { display: none !important; }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      overflow: hidden;
    }
    .panel-title {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      min-height: 52px;
      padding: 0 16px;
      border-bottom: 1px solid var(--line-soft);
      background: var(--panel-strong);
    }
    .panel-title span {
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }
    .panel-body { padding: 16px; }
    .login-shell {
      display: grid;
      place-items: center;
      min-height: calc(100vh - 150px);
    }
    #loginPanel {
      width: min(420px, 100%);
      background: var(--panel-strong);
    }
    .dashboard-grid {
      display: grid;
      grid-template-columns: minmax(320px, 380px) minmax(460px, 1fr) minmax(320px, 390px);
      gap: 14px;
      align-items: start;
    }
    .metrics {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 1px;
      margin: 0 0 14px;
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--line);
      box-shadow: var(--shadow);
    }
    .metric {
      min-height: 76px;
      padding: 12px;
      background: var(--panel-strong);
    }
    .metric strong { display: block; font-size: 24px; line-height: 1.1; }
    .metric span { display: block; margin-top: 6px; color: var(--muted); font-size: 12px; font-weight: 760; }
    label {
      display: grid;
      gap: 6px;
      margin: 0 0 12px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 760;
    }
    input, select, textarea {
      width: 100%;
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 8px 10px;
      font: inherit;
      font-size: 14px;
      background: #fff;
      color: var(--text);
      outline: none;
    }
    textarea { min-height: 72px; resize: vertical; }
    input:focus, select:focus, textarea:focus {
      border-color: #4a9184;
      box-shadow: 0 0 0 3px rgba(17, 106, 91, .14);
    }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .field-note {
      margin: -4px 0 12px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }
    .checkbox-line {
      display: flex;
      align-items: center;
      gap: 8px;
      margin: 0 0 12px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 760;
    }
    .checkbox-line input {
      width: 16px;
      min-height: 16px;
      height: 16px;
      padding: 0;
    }
    .actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    button {
      min-height: 36px;
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 7px 12px;
      background: #fff;
      color: var(--text);
      font: inherit;
      font-weight: 760;
      cursor: pointer;
      transition: transform .12s ease, border-color .12s ease, background .12s ease;
    }
    button:hover { border-color: #a9b4bf; transform: translateY(-1px); }
    button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
    button.primary:hover { background: var(--accent-dark); }
    button.danger { color: var(--danger); border-color: #efb5ae; }
    button.compact { min-height: 30px; padding: 4px 9px; font-size: 12px; }
    button:disabled { opacity: .55; cursor: not-allowed; transform: none; }
    .message { min-height: 20px; margin-top: 10px; color: var(--muted); font-size: 13px; overflow-wrap: anywhere; }
    .message.error { color: var(--danger); }
    .message.ok { color: var(--ok); }
    .step {
      position: relative;
      padding: 14px 0 4px;
      border-top: 1px solid var(--line-soft);
    }
    .step:first-child { padding-top: 0; border-top: 0; }
    .step-head {
      display: flex;
      align-items: center;
      gap: 8px;
      margin: 0 0 12px;
    }
    .step-index {
      display: grid;
      place-items: center;
      width: 24px;
      height: 24px;
      border-radius: 999px;
      background: var(--ink);
      color: #fff;
      font-size: 12px;
      font-weight: 800;
    }
    .segment {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
      margin-bottom: 12px;
    }
    .segment button {
      min-height: 58px;
      text-align: left;
      background: #fff;
    }
    .segment button strong { display: block; font-size: 14px; }
    .segment button span { display: block; margin-top: 3px; color: var(--muted); font-size: 11px; font-weight: 700; }
    .segment button.active {
      border-color: #74ad9f;
      background: var(--accent-soft);
      color: var(--accent-dark);
      box-shadow: inset 0 0 0 1px #74ad9f;
    }
    .handoff {
      margin-top: 12px;
      border: 1px solid #9db4ad;
      border-radius: 8px;
      background: #f4fbf8;
      overflow: hidden;
    }
    .handoff-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 8px 10px;
      border-bottom: 1px solid #c9ddd6;
      color: var(--accent-dark);
      font-size: 12px;
      font-weight: 800;
    }
    .keybox {
      padding: 11px;
      font-family: "Cascadia Mono", "SFMono-Regular", Consolas, monospace;
      font-size: 12px;
      line-height: 1.55;
      overflow-wrap: anywhere;
      user-select: all;
    }
    .filters {
      display: grid;
      grid-template-columns: minmax(180px, 1fr) 140px 140px;
      gap: 10px;
      padding: 16px;
      border-bottom: 1px solid var(--line-soft);
      background: #fff;
    }
    .filters label { margin: 0; }
    .subscription-list { max-height: calc(100vh - 285px); overflow: auto; }
    .subscription-card {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 12px;
      width: 100%;
      padding: 13px 16px;
      border: 0;
      border-bottom: 1px solid var(--line-soft);
      border-radius: 0;
      text-align: left;
      background: #fff;
    }
    .subscription-card:hover { background: #f7faf9; transform: none; }
    .subscription-card.active {
      background: #eef8f5;
      box-shadow: inset 3px 0 0 var(--accent);
    }
    .card-title {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 5px;
      font-size: 14px;
      font-weight: 820;
      overflow-wrap: anywhere;
    }
    .card-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px 12px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 3px 8px;
      border: 1px solid transparent;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 800;
      background: #eef2f5;
      white-space: nowrap;
    }
    .badge.free { color: #415062; background: #edf1f5; border-color: #d6dde5; }
    .badge.pro { color: #0b4c41; background: var(--accent-soft); border-color: #bbdcd2; }
    .badge.max { color: #1f4a83; background: #e8f1ff; border-color: #c8dbf6; }
    .badge.active, .badge.trialing { color: var(--ok); background: var(--ok-soft); border-color: #bde5d2; }
    .badge.revoked, .badge.expired, .badge.canceled {
      color: var(--danger);
      background: var(--danger-soft);
      border-color: #f1c1bb;
    }
    .badge.past_due { color: var(--warn); background: var(--warn-soft); border-color: #f0d38c; }
    .muted { color: var(--muted); }
    .mono { font-family: "Cascadia Mono", "SFMono-Regular", Consolas, monospace; font-size: 12px; }
    .detail-empty { padding: 18px; color: var(--muted); line-height: 1.6; }
    .detail-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      margin: 14px 0;
    }
    .detail-cell {
      min-height: 62px;
      padding: 10px;
      border: 1px solid var(--line-soft);
      border-radius: 8px;
      background: #fff;
    }
    .detail-cell span {
      display: block;
      color: var(--muted);
      font-size: 11px;
      font-weight: 800;
    }
    .detail-cell strong {
      display: block;
      margin-top: 6px;
      font-size: 13px;
      overflow-wrap: anywhere;
    }
    .device-row {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px;
      padding: 10px 0;
      border-top: 1px solid var(--line-soft);
    }
    .device-row:first-child { border-top: 0; }
    .device-meta {
      display: block;
      margin-top: 3px;
      color: var(--muted);
      font-size: 11px;
      line-height: 1.35;
    }
    .device-risk {
      display: inline-flex;
      margin-top: 4px;
      padding: 2px 6px;
      border-radius: 999px;
      background: var(--warn-soft);
      color: var(--warn);
      font-size: 11px;
      font-weight: 800;
    }
    .renew-panel, .danger-panel {
      margin-top: 14px;
      border: 1px solid #9db4ad;
      border-radius: 8px;
      background: #f6fbf9;
      overflow: hidden;
    }
    .danger-panel {
      border-color: #efb5ae;
      background: #fff7f6;
    }
    .renew-head, .danger-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      padding: 10px 12px;
      border-bottom: 1px solid #d5e5df;
      color: var(--accent-dark);
      font-weight: 800;
    }
    .danger-head {
      border-bottom-color: #f3ccc7;
      color: var(--danger);
    }
    .renew-body, .danger-body { padding: 12px; }
    .callout {
      margin: 12px 0;
      padding: 10px 12px;
      border: 1px solid #f0d38c;
      border-radius: 8px;
      background: var(--warn-soft);
      color: #5f3d00;
      font-size: 12px;
      line-height: 1.5;
    }
    .toolbar { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
    @media (max-width: 1180px) {
      .dashboard-grid { grid-template-columns: minmax(320px, 380px) minmax(0, 1fr); }
      .detail-panel { grid-column: 1 / -1; }
      .subscription-list { max-height: none; }
    }
    @media (max-width: 860px) {
      header { padding: 0 16px; }
      main { padding: 14px 10px 32px; }
      .dashboard-grid { grid-template-columns: 1fr; }
      .row, .filters, .detail-grid { grid-template-columns: 1fr; }
      .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .segment { grid-template-columns: 1fr; }
      .subscription-card { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <div class="brand-mark">L</div>
      <div class="brand-copy">
        <h1>Lengrvis 激活管理后台</h1>
        <div class="eyebrow">订阅授权码、设备绑定与售后处理</div>
      </div>
    </div>
    <button id="logout" class="hidden">退出登录</button>
  </header>
  <main>
    <div id="loginShell" class="login-shell">
      <section id="loginPanel" class="panel">
        <div class="panel-title">
          <h2>管理员登录</h2>
          <span>安全会话</span>
        </div>
        <div class="panel-body">
          <label>密码<input id="password" type="password" autocomplete="current-password"></label>
          <div class="actions"><button id="login" class="primary">登录</button></div>
          <div id="loginMessage" class="message"></div>
        </div>
      </section>
    </div>

    <section id="dashboard" class="hidden">
      <div class="metrics">
        <div class="metric"><strong id="metricTotal">0</strong><span>订阅总数</span></div>
        <div class="metric"><strong id="metricActive">0</strong><span>可激活</span></div>
        <div class="metric"><strong id="metricPaid">0</strong><span>付费套餐</span></div>
        <div class="metric"><strong id="metricDevices">0</strong><span>已绑设备</span></div>
      </div>

      <div class="dashboard-grid">
        <section class="panel">
          <div class="panel-title">
            <h2>创建授权码</h2>
            <span>一次性显示</span>
          </div>
          <div class="panel-body">
            <div class="step">
              <div class="step-head"><span class="step-index">1</span><h3>选择套餐</h3></div>
              <div id="planSegment" class="segment" role="group" aria-label="套餐选择">
                <button data-plan="free" type="button"><strong>Free</strong><span>默认 1 台设备</span></button>
                <button data-plan="pro" type="button" class="active"><strong>Pro</strong><span>默认 2 台设备</span></button>
                <button data-plan="max" type="button"><strong>Max</strong><span>默认 5 台设备</span></button>
              </div>
              <p id="planPreview" class="field-note"></p>
              <div class="row">
                <label>状态
                  <select id="status">
                    <option value="active" selected>生效中</option>
                    <option value="trialing">试用中</option>
                  </select>
                </label>
                <label>最大设备数<input id="maxDevices" type="number" min="1" value="2"></label>
              </div>
            </div>

            <div class="step">
              <div class="step-head"><span class="step-index">2</span><h3>设置周期</h3></div>
              <label>有效期
                <select id="expiresPreset">
                  <option value="7">7 天试用</option>
                  <option value="30" selected>30 天月付</option>
                  <option value="90">90 天季度</option>
                  <option value="180">180 天半年</option>
                  <option value="365">365 天年付</option>
                  <option value="none">长期有效</option>
                  <option value="custom">自定义日期</option>
                </select>
              </label>
              <div id="customExpiryWrap" class="hidden">
                <label>自定义到期日期<input id="expiresDate" type="date"></label>
              </div>
              <p id="expiryPreview" class="field-note"></p>
              <label class="checkbox-line">
                <input id="cancelAtPeriodEnd" type="checkbox">
                周期结束后取消，不自动续期
              </label>
            </div>

            <div class="step">
              <div class="step-head"><span class="step-index">3</span><h3>客户与交付</h3></div>
              <label>订阅 ID<input id="subscriptionId" placeholder="可留空，系统自动生成"></label>
              <label>客户标签<input id="subject" placeholder="客户或试点标签，可选"></label>
              <div class="row">
                <label>席位数<input id="seats" type="number" min="1" value="1"></label>
                <label>订单备注<input id="orderRef" placeholder="订单号或付款备注，可选"></label>
              </div>
              <div class="actions"><button id="createKey" class="primary">创建授权码</button></div>
              <div id="createMessage" class="message"></div>
              <div id="newKeyWrap" class="handoff hidden">
                <div class="handoff-head">
                  <span>新授权码，只显示一次</span>
                  <div class="actions">
                    <button id="copyKey" class="compact" type="button">复制</button>
                    <button id="downloadKey" class="compact" type="button">下载交接文本</button>
                  </div>
                </div>
                <div id="newKey" class="keybox"></div>
                <div class="panel-body">
                  <label class="checkbox-line">
                    <input id="ackKeySaved" type="checkbox">
                    我已把授权码交接到安全位置
                  </label>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section class="panel">
          <div class="panel-title">
            <h2>订阅管理</h2>
            <div class="toolbar"><button id="refresh" type="button">刷新</button></div>
          </div>
          <div class="filters">
            <label>搜索<input id="searchBox" placeholder="订阅号、客户、订单、key、设备、license"></label>
            <label>套餐
              <select id="planFilter">
                <option value="">全部套餐</option>
                <option value="free">Free</option>
                <option value="pro">Pro</option>
                <option value="max">Max</option>
              </select>
            </label>
            <label>状态
              <select id="statusFilter">
                <option value="">全部状态</option>
                <option value="active">生效中</option>
                <option value="trialing">试用中</option>
                <option value="past_due">逾期</option>
                <option value="canceled">已取消</option>
                <option value="expired">已过期</option>
                <option value="revoked">已撤销</option>
              </select>
            </label>
          </div>
          <div id="listMessage" class="message panel-body"></div>
          <div id="subscriptions" class="subscription-list"></div>
        </section>

        <aside class="panel detail-panel">
          <div class="panel-title">
            <h2>详情与操作</h2>
            <span id="detailStatusLabel">未选择</span>
          </div>
          <div id="detailEmpty" class="detail-empty">选择左侧订阅后，可以续期、撤销、解绑设备，并查看设备指纹风险。</div>
          <div id="detailContent" class="panel-body hidden">
            <div class="card-title" id="detailTitle"></div>
            <div class="actions" id="detailBadges"></div>
            <div class="detail-grid" id="detailGrid"></div>
            <h3>设备绑定</h3>
            <div id="detailDevices"></div>
            <div class="actions" style="margin-top: 14px;">
              <button id="openRenew" class="primary" type="button">续期 / 改状态</button>
              <button id="openRevoke" class="danger" type="button">撤销授权码</button>
            </div>

            <div id="renewPanel" class="renew-panel hidden">
              <div class="renew-head">
                <span id="renewTitle">续期订阅</span>
                <button id="cancelRenew" class="compact" type="button">取消</button>
              </div>
              <div class="renew-body">
                <div class="row">
                  <label>状态
                    <select id="renewStatus">
                      <option value="active">生效中</option>
                      <option value="trialing">试用中</option>
                      <option value="past_due">逾期</option>
                      <option value="canceled">已取消</option>
                      <option value="expired">已过期</option>
                    </select>
                  </label>
                  <label>有效期
                    <select id="renewExpiresPreset">
                      <option value="30" selected>再续 30 天</option>
                      <option value="90">再续 90 天</option>
                      <option value="180">再续 180 天</option>
                      <option value="365">再续 365 天</option>
                      <option value="none">改为长期有效</option>
                      <option value="custom">自定义日期</option>
                    </select>
                  </label>
                </div>
                <div id="renewCustomExpiryWrap" class="hidden">
                  <label>自定义到期日期<input id="renewExpiresDate" type="date"></label>
                </div>
                <p id="renewExpiryPreview" class="field-note"></p>
                <div class="row">
                  <label>席位数<input id="renewSeats" type="number" min="1" value="1"></label>
                  <label>最大设备数<input id="renewMaxDevices" type="number" min="1" value="1"></label>
                </div>
                <label class="checkbox-line">
                  <input id="renewCancelAtPeriodEnd" type="checkbox">
                  周期结束后取消，不自动续期
                </label>
                <div class="actions">
                  <button id="submitRenew" class="primary" type="button">确认续期</button>
                </div>
              </div>
            </div>

            <div id="dangerPanel" class="danger-panel hidden">
              <div class="danger-head">
                <span id="dangerTitle">危险操作确认</span>
                <button id="cancelDanger" class="compact" type="button">取消</button>
              </div>
              <div class="danger-body">
                <p id="dangerText" class="field-note"></p>
                <label id="dangerReasonWrap">原因
                  <select id="dangerReason">
                    <option value="refund">退款</option>
                    <option value="chargeback">拒付</option>
                    <option value="replacement">换发</option>
                    <option value="breach">违规</option>
                    <option value="admin">管理员处理</option>
                  </select>
                </label>
                <label>确认文字<input id="dangerConfirm" placeholder="按提示输入确认文字"></label>
                <div class="actions">
                  <button id="confirmDanger" class="danger" type="button">确认执行</button>
                </div>
              </div>
            </div>
          </div>
        </aside>
      </div>
    </section>
  </main>
  <script>
    const $ = (id) => document.getElementById(id);
    const planDefaults = {
      free: {label: 'Free', devices: 1, note: '免费或内部测试，默认 1 台设备。'},
      pro: {label: 'Pro', devices: 2, note: '个人专业版，默认 2 台设备。'},
      max: {label: 'Max', devices: 5, note: '高阶或小团队试点，默认 5 台设备。'},
    };
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
      } catch (err) {
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
      } catch (err) {
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
      } catch (err) {
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
      } catch (err) {
        setMessage('createMessage', err.message, 'error');
      }
    }
    async function loadSubscriptions() {
      setMessage('listMessage', '加载中...');
      try {
        const result = await api('/api/admin/subscriptions', {method: 'GET'});
        state.items = result.items || [];
        renderMetrics();
        applyFilters();
        setMessage('listMessage', state.items.length ? '' : '暂无订阅记录。');
      } catch (err) {
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
        meta.textContent = [
          item.subject ? '客户 ' + item.subject : '未填客户标签',
          item.order_ref ? '订单 ' + item.order_ref : '',
          '设备 ' + String(item.device_count || 0) + ' / ' + String(item.max_devices || 1),
          '到期 ' + formatDate(item.expires_at),
        ].filter(Boolean).join(' · ');
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
      const paid = items.filter(item => ['pro', 'max'].includes(String(item.plan || ''))).length;
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
    function renderDetail() {
      const item = selectedItem();
      $('detailEmpty').classList.toggle('hidden', Boolean(item));
      $('detailContent').classList.toggle('hidden', !item);
      $('detailStatusLabel').textContent = item ? displayStatus(item.status) : '未选择';
      if (!item) return;
      $('detailTitle').textContent = item.subscription_id || item.key_hash_prefix || '未命名订阅';
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
      $('openRenew').disabled = item.status === 'revoked';
      $('openRevoke').disabled = item.status === 'revoked';
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
    async function renewSubscription(item) {
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
      } catch (err) {
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
      } catch (err) {
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
    function cancelDanger() {
      state.danger = null;
      $('dangerPanel').classList.add('hidden');
    }
    async function confirmDanger() {
      if (!state.danger) return;
      const required = state.danger.type === 'revoke' ? '撤销' : '解绑';
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
        } else {
          const device = state.danger.device;
          await api('/api/admin/devices/' + device.license_id, {method: 'DELETE'});
          cancelDanger();
          await loadSubscriptions();
          setMessage('listMessage', '设备已解绑。', 'ok');
        }
      } catch (err) {
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
  </script>
</body>
</html>
"""
