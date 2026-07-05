"""Creation and subscription-list CSS for the Admin workspace."""
# ruff: noqa: E501
ADMIN_STYLE_CREATION_AND_LISTS = r"""    .step {
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
      grid-template-columns: minmax(210px, 1fr) minmax(116px, 140px) minmax(116px, 140px);
      gap: 10px;
      padding: 16px;
      border-bottom: 1px solid var(--line-soft);
      background: #fff;
    }
    .filters label { margin: 0; }
    .subscription-list { max-height: min(680px, calc(100vh - 285px)); overflow: auto; }
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
      align-items: start;
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
      gap: 6px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }
    .meta-pill {
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      padding: 2px 7px;
      border-radius: 999px;
      background: #f2f5f7;
      color: #5d6875;
      font-weight: 760;
      overflow-wrap: anywhere;
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
"""
