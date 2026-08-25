import { Loader2, Play, Square } from "lucide-react";
import { useState } from "react";

export function RuntimeControlSection({
  onStartBackend,
  onStopBackend
}: {
  onStartBackend: () => Promise<void>;
  onStopBackend: () => Promise<void>;
}) {
  const [activeOperation, setActiveOperation] = useState<"start" | "stop" | null>(null);
  const [operationError, setOperationError] = useState("");
  const isWorking = activeOperation !== null;

  const runOperation = async (operation: "start" | "stop", action: () => Promise<void>) => {
    if (isWorking) return;
    setActiveOperation(operation);
    setOperationError("");
    try {
      await action();
    } catch { // broad-exception-boundary: Electron IPC failures must remain recoverable and user-safe.
      setOperationError(operation === "start" ? "启动服务失败，请稍后重试。" : "停止服务失败，请稍后重试。");
    } finally {
      setActiveOperation(null);
    }
  };

  return (
    <fieldset className="mcp-servers">
      <legend>运行控制</legend>
      <div className="button-row">
        <button
          className="button button--secondary"
          type="button"
          disabled={isWorking}
          aria-busy={activeOperation === "start"}
          onClick={() => void runOperation("start", onStartBackend)}
        >
          {activeOperation === "start" ? <Loader2 className="settings-spinner" size={16} aria-hidden="true" /> : <Play size={16} aria-hidden="true" />}
          {activeOperation === "start" ? "启动中" : "启动"}
        </button>
        <button
          className="button button--secondary"
          type="button"
          disabled={isWorking}
          aria-busy={activeOperation === "stop"}
          onClick={() => void runOperation("stop", onStopBackend)}
        >
          {activeOperation === "stop" ? <Loader2 className="settings-spinner" size={16} aria-hidden="true" /> : <Square size={16} aria-hidden="true" />}
          {activeOperation === "stop" ? "停止中" : "停止"}
        </button>
      </div>
      {operationError ? <p className="field-error" role="alert">{operationError}</p> : null}
    </fieldset>
  );
}
