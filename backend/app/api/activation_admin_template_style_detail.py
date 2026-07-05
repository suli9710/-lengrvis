"""Detail panel and responsive CSS for the Admin workspace."""
# ruff: noqa: E501
ADMIN_STYLE_DETAIL_AND_RESPONSIVE = r"""    .detail-empty { padding: 18px; color: var(--muted); line-height: 1.6; }
    .detail-panel { position: sticky; top: 16px; }
    .detail-header {
      display: grid;
      gap: 8px;
      padding-bottom: 14px;
      border-bottom: 1px solid var(--line-soft);
    }
    .detail-title-row {
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      gap: 8px;
    }
    .detail-title {
      margin-bottom: 0;
      font-size: 16px;
      line-height: 1.35;
    }
    .badge-row { gap: 6px; }
    .detail-subtitle {
      margin: 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
      overflow-wrap: anywhere;
    }
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
    .section-caption {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin: 16px 0 6px;
    }
    .section-caption span {
      color: var(--muted);
      font-size: 12px;
      font-weight: 800;
    }
    .device-row {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px;
      padding: 10px 0;
      border-top: 1px solid var(--line-soft);
      align-items: start;
    }
    .device-row:first-child { border-top: 0; }
    .device-row button { align-self: start; }
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
    .operation-grid {
      display: grid;
      gap: 10px;
      margin-top: 16px;
    }
    .operation-block {
      display: grid;
      gap: 9px;
      padding: 12px;
      border: 1px solid var(--line-soft);
      border-radius: 8px;
      background: #fff;
    }
    .operation-block.danger-zone {
      border-color: #efb5ae;
      background: #fff7f6;
    }
    .operation-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }
    .operation-head span {
      color: var(--muted);
      font-size: 11px;
      font-weight: 800;
    }
    .operation-copy {
      margin: 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }
    .operation-actions { display: flex; flex-wrap: wrap; gap: 8px; }
    .operation-actions button { flex: 1 1 130px; }
    .inline-note { margin: 0; }
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
      .detail-panel { grid-column: 1 / -1; position: static; }
      .subscription-list { max-height: none; }
      .operation-grid { grid-template-columns: 1fr 1fr; }
    }
    @media (max-width: 860px) {
      header { padding: 0 16px; }
      main { padding: 14px 10px 32px; }
      .dashboard-grid { grid-template-columns: 1fr; }
      .row, .filters, .detail-grid { grid-template-columns: 1fr; }
      .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .segment { grid-template-columns: 1fr; }
      .subscription-card { grid-template-columns: 1fr; }
      .operation-grid { grid-template-columns: 1fr; }
      .operation-actions button { flex-basis: 100%; }
    }
"""
