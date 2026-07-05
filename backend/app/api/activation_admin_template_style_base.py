"""Base CSS for the subscription activation Admin workspace."""
# ruff: noqa: E501
ADMIN_STYLE_BASE = r"""    :root {
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
      grid-template-columns: minmax(320px, 380px) minmax(460px, 1fr) minmax(340px, 410px);
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
    .message.panel-body {
      margin: 0;
      min-height: 46px;
      display: flex;
      align-items: center;
      border-bottom: 1px solid var(--line-soft);
      background: #fff;
    }
"""
