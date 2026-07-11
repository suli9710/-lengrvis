"""HTML shell sections for the subscription activation Admin workspace."""
# ruff: noqa: E501

from app.api.activation_admin_template_script import ADMIN_SCRIPT
from app.api.activation_admin_template_styles import ADMIN_STYLES

ADMIN_DOCUMENT_START = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Lengrvis 激活管理后台</title>
  <style>
"""

ADMIN_DOCUMENT_BODY = r"""  </style>
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
                <button data-plan="plus" type="button" class="active"><strong>Plus</strong><span>¥49/月 · 默认 2 台设备</span></button>
                <button data-plan="pro" type="button"><strong>Pro</strong><span>¥129/月 · 默认 5 台设备</span></button>
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
                <option value="plus">Plus</option>
                <option value="pro">Pro</option>
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
          <div id="detailEmpty" class="detail-empty">选择左侧订阅后，可以续期、撤销、解绑设备、清理终态订阅记录，并查看设备指纹风险。</div>
          <div id="detailContent" class="panel-body hidden">
            <div class="detail-header">
              <div class="detail-title-row">
                <div class="card-title detail-title" id="detailTitle"></div>
                <div class="actions badge-row" id="detailBadges"></div>
              </div>
              <p class="detail-subtitle" id="detailSubtitle"></p>
            </div>
            <div class="detail-grid" id="detailGrid"></div>
            <div class="section-caption">
              <h3>设备绑定</h3>
              <span id="deviceSummary">0 台设备</span>
            </div>
            <div id="detailDevices"></div>
            <div class="operation-grid">
              <div class="operation-block">
                <div class="operation-head">
                  <h3>维护订阅</h3>
                  <span>RENEW</span>
                </div>
                <p class="operation-copy">调整状态、到期时间、席位数和最大设备数。</p>
                <div class="operation-actions">
                  <button id="openRenew" class="primary" type="button">续期 / 改状态</button>
                </div>
              </div>
              <div class="operation-block danger-zone">
                <div class="operation-head">
                  <h3>清理与停用</h3>
                  <span>DANGER</span>
                </div>
                <p class="operation-copy" id="deleteHint">终态且无设备绑定的记录可删除；已有设备时先处理吊销或解绑。</p>
                <div class="operation-actions">
                  <button id="openRevoke" class="danger" type="button">撤销授权码</button>
                  <button id="openDelete" class="danger" type="button">删除记录</button>
                </div>
              </div>
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
"""

ADMIN_DOCUMENT_END = r"""  </script>
</body>
</html>
"""


def render_activation_admin_template() -> str:
    """Return the full Admin workspace HTML document."""
    return "".join(
        (
            ADMIN_DOCUMENT_START,
            ADMIN_STYLES,
            ADMIN_DOCUMENT_BODY,
            ADMIN_SCRIPT,
            ADMIN_DOCUMENT_END,
        )
    )
