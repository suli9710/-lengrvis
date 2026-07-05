import type { Dispatch, SetStateAction } from "react";

import type { BackendStatus } from "../../../shared/types";
import type { AppSettings } from "../../../shared/settingsTypes";
import type { LLMCostSummary, LLMHealthStatus } from "../../../shared/llmContextTypes";
import { zhBackendState } from "../../lib/zh";

type SetDraft = Dispatch<SetStateAction<AppSettings>>;

interface AiConnectionSectionProps {
  draft: AppSettings;
  setDraft: SetDraft;
  llmHealth: LLMHealthStatus | null;
  llmCostSummary: LLMCostSummary | null;
  backendStatus: BackendStatus;
  realtimeStatusText: string;
  realtimeStatusProblem: boolean;
}

export function AiConnectionSection({
  draft,
  setDraft,
  llmHealth,
  llmCostSummary,
  backendStatus,
  realtimeStatusText,
  realtimeStatusProblem
}: AiConnectionSectionProps) {
  return (
    <fieldset className="mcp-servers">
      <legend>AI 连接</legend>
      <div className="settings-grid settings-grid--balanced">
        <label className="field">
          <span>服务商</span>
          <input
            value={draft.providerName}
            onChange={(event) => setDraft((current) => ({ ...current, providerName: event.target.value }))}
          />
        </label>
        <label className="field">
          <span>模型</span>
          <input
            list="lengrvis-model-options"
            value={draft.model}
            onChange={(event) => setDraft((current) => ({ ...current, model: event.target.value }))}
            placeholder="选择或输入模型名"
          />
        </label>
        <label className="field">
          <span>审核模型</span>
          <input
            list="lengrvis-model-options"
            value={draft.reviewModel}
            onChange={(event) => setDraft((current) => ({ ...current, reviewModel: event.target.value }))}
            placeholder="选择或输入模型名"
          />
        </label>
        <label className="field">
          <span>接口类型</span>
          <select
            value={draft.wireApi}
            onChange={(event) =>
              setDraft((current) => ({ ...current, wireApi: event.target.value as AppSettings["wireApi"] }))
            }
          >
            <option value="chat_completions">对话补全接口（chat_completions）</option>
            <option value="responses">响应式接口（responses）</option>
          </select>
        </label>
        <label className="field">
          <span>推理强度</span>
          <select
            value={draft.modelReasoningEffort}
            onChange={(event) => setDraft((current) => ({ ...current, modelReasoningEffort: event.target.value }))}
          >
            <option value="">默认（跟随模型）</option>
            <option value="minimal">最小</option>
            <option value="low">低</option>
            <option value="medium">中</option>
            <option value="high">高</option>
            {["", "minimal", "low", "medium", "high"].includes(draft.modelReasoningEffort) ? null : (
              <option value={draft.modelReasoningEffort}>{draft.modelReasoningEffort}（自定义）</option>
            )}
          </select>
        </label>
        <datalist id="lengrvis-model-options">
          <option value="gpt-4o-mini" />
          <option value="gpt-4o" />
          <option value="gpt-4.1-mini" />
          <option value="gpt-4.1" />
          <option value="o4-mini" />
          <option value="qwen2.5:7b-instruct" />
          <option value="qwen2.5:3b-instruct" />
          <option value="llama3.1:8b" />
        </datalist>
        <label className="field">
          <span>服务商 Base URL</span>
          <input
            value={draft.apiBaseUrl}
            onChange={(event) => setDraft((current) => ({ ...current, apiBaseUrl: event.target.value }))}
          />
        </label>
        <label className="mcp-servers__toggle">
          <input
            type="checkbox"
            checked={draft.requiresOpenAiAuth}
            onChange={(event) => setDraft((current) => ({ ...current, requiresOpenAiAuth: event.target.checked }))}
          />
          <span>需要 OpenAI 认证</span>
        </label>
        <label className="mcp-servers__toggle">
          <input
            type="checkbox"
            checked={draft.disableResponseStorage}
            onChange={(event) => setDraft((current) => ({ ...current, disableResponseStorage: event.target.checked }))}
          />
          <span>禁用响应存储</span>
        </label>
      </div>
      <div className="settings-status-grid">
        <p className="muted">
          当前：{llmHealth?.active.provider ?? "N/A"} / {llmHealth?.active.model ?? "N/A"} /{" "}
          {llmHealth?.active.profile.activeBackend ?? "N/A"}
        </p>
        <p className="muted">
          成本：
          {llmCostSummary
            ? `${llmCostSummary.calls} 次调用，${llmCostSummary.totalTokens} tokens，${
                llmCostSummary.totalCostUsd === null ? "N/A" : `$${llmCostSummary.totalCostUsd.toFixed(4)}`
              }`
            : "N/A"}
        </p>
        <p className="muted">运行状态：{zhBackendState(backendStatus.state)}</p>
        {realtimeStatusText ? (
          <p className={realtimeStatusProblem ? "settings-status settings-status--error" : "muted"}>
            实时状态：{realtimeStatusText}
          </p>
        ) : null}
      </div>
    </fieldset>
  );
}
