import React from "react";
import ReactDOM from "react-dom/client";

import { App } from "./App";
import { ConsentGate } from "./components/legal/ConsentGate";
import "./styles.css";
import "./styles.foundation.css";
import "./styles.shell.css";
import "./styles.home.css";
import "./styles.home-agents.css";
import "./styles.home-inspector.css";
import "./styles.home-command.css";
import "./styles.home-actions.css";
import "./styles.details.css";
import "./styles.chat.css";
import "./styles.timeline-files.css";
import "./styles.file-tools.css";
import "./styles.file-tool-panels.css";
import "./styles.browser-explain.css";
import "./styles.settings.css";
import "./styles.settings-privacy.css";
import "./styles.settings-local-model.css";
import "./styles.mobile-pairing.css";
import "./styles.skills.css";
import "./styles.system-approval.css";
import "./styles.approval-modal.css";
import "./styles.approval-details.css";
import "./styles.responsive.css";
import "./components/legal/privacy-consent.css";
import "./components/settings/settings-commerce-privacy.css";
import "./styles.diagnostics-fix.css";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <ConsentGate>
      <App />
    </ConsentGate>
  </React.StrictMode>
);
