import type { Dispatch, SetStateAction } from "react";

import type { AppSettings, LLMHealthStatus } from "../../../shared/types";

type SetDraft = Dispatch<SetStateAction<AppSettings>>;

export function GenerationStabilitySection({
  draft,
  setDraft,
  llmHealth
}: {
  draft: AppSettings;
  setDraft: SetDraft;
  llmHealth: LLMHealthStatus | null;
}) {
  return (
    <fieldset className="mcp-servers">
      <legend>生成与稳定性</legend>
      <div className="settings-grid settings-grid--balanced">
        <label className="field">
          <span>温度</span>
          <input
            type="number"
            min={0}
            max={2}
            step={0.05}
            value={draft.temperature}
            onChange={(event) => setDraft((current) => ({ ...current, temperature: Number(event.target.value) || 0 }))}
          />
        </label>
        <label className="field">
          <span>最大 Tokens</span>
          <input
            type="number"
            min={1}
            step={1}
            value={draft.maxTokens}
            onChange={(event) =>
              setDraft((current) => ({ ...current, maxTokens: Math.max(1, Number(event.target.value) || 1) }))
            }
          />
        </label>
        <label className="field">
          <span>超时</span>
          <input
            type="number"
            min={1}
            step={1}
            value={draft.timeout}
            onChange={(event) =>
              setDraft((current) => ({ ...current, timeout: Math.max(1, Number(event.target.value) || 1) }))
            }
          />
        </label>
        <label className="field">
          <span>重试次数</span>
          <input
            type="number"
            min={0}
            step={1}
            value={draft.llmApiMaxRetries}
            onChange={(event) =>
              setDraft((current) => ({ ...current, llmApiMaxRetries: Math.max(0, Number(event.target.value) || 0) }))
            }
          />
        </label>
        <label className="field">
          <span>重试退避</span>
          <input
            type="number"
            min={0}
            step={0.05}
            value={draft.llmApiRetryBackoffSeconds}
            onChange={(event) =>
              setDraft((current) => ({
                ...current,
                llmApiRetryBackoffSeconds: Math.max(0, Number(event.target.value) || 0)
              }))
            }
          />
        </label>
        <label className="field">
          <span>熔断阈值</span>
          <input
            type="number"
            min={1}
            step={1}
            value={draft.llmApiCircuitFailureThreshold}
            onChange={(event) =>
              setDraft((current) => ({
                ...current,
                llmApiCircuitFailureThreshold: Math.max(1, Number(event.target.value) || 1)
              }))
            }
          />
        </label>
        <label className="field">
          <span>熔断冷却</span>
          <input
            type="number"
            min={0}
            step={1}
            value={draft.llmApiCircuitCooldownSeconds}
            onChange={(event) =>
              setDraft((current) => ({
                ...current,
                llmApiCircuitCooldownSeconds: Math.max(0, Number(event.target.value) || 0)
              }))
            }
          />
        </label>
        <label className="field">
          <span>上下文窗口</span>
          <input
            type="number"
            min={1}
            step={1}
            value={draft.modelContextWindow}
            onChange={(event) =>
              setDraft((current) => ({
                ...current,
                modelContextWindow: Math.max(1, Number(event.target.value) || 1)
              }))
            }
          />
        </label>
        <label className="field">
          <span>自动压缩上限</span>
          <input
            type="number"
            min={1}
            step={1}
            value={draft.modelAutoCompactTokenLimit}
            onChange={(event) =>
              setDraft((current) => ({
                ...current,
                modelAutoCompactTokenLimit: Math.max(1, Number(event.target.value) || 1)
              }))
            }
          />
        </label>
      </div>
      <div className="settings-status-grid">
        <p className="muted">
          重试：{llmHealth?.retry.maxRetries ?? "N/A"} 次，退避 {llmHealth?.retry.backoffSeconds ?? "N/A"} 秒，熔断状态{" "}
          {llmHealth?.retry.circuit.state ?? "N/A"}
        </p>
      </div>
    </fieldset>
  );
}
