import { Play, Square } from "lucide-react";

export function RuntimeControlSection({
  onStartBackend,
  onStopBackend
}: {
  onStartBackend: () => Promise<void>;
  onStopBackend: () => Promise<void>;
}) {
  return (
    <fieldset className="mcp-servers">
      <legend>运行控制</legend>
      <div className="button-row">
        <button className="button button--secondary" onClick={() => void onStartBackend()}>
          <Play size={16} aria-hidden="true" />
          启动
        </button>
        <button className="button button--secondary" onClick={() => void onStopBackend()}>
          <Square size={16} aria-hidden="true" />
          停止
        </button>
      </div>
    </fieldset>
  );
}
