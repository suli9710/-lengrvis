import React from "react";
import ReactDOM from "react-dom/client";

import { App } from "./App";
import { ConsentGate } from "./components/legal/ConsentGate";
import "./styles.css";
import "./styles.diagnostics-fix.css";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <ConsentGate>
      <App />
    </ConsentGate>
  </React.StrictMode>
);
