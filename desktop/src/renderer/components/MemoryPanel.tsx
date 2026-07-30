import { Ban, Brain, Plus, Search, ShieldCheck, Trash2 } from "lucide-react";
import { useCallback, useState } from "react";

import { LengrvisApiClient } from "../lib/apiClient";
import type { BackendMemory } from "../lib/api/memoryBackendTypes";
import { Badge, Panel } from "./Panel";
import { CollectionPanelStatus, useCollectionPanelState } from "./useCollectionPanelState";

type MemoryItem = BackendMemory;

interface MemoryPanelProps {
  api: LengrvisApiClient;
}

export function MemoryPanel({ api }: MemoryPanelProps) {
  const [query, setQuery] = useState("");
  const [draftContent, setDraftContent] = useState("");
  const [draftTags, setDraftTags] = useState("");
  const [searchResultQuery, setSearchResultQuery] = useState<string | null>(null);
  const loader = useCallback(() => api.listMemories(), [api]);
  const {
    items,
    isLoading,
    loadError,
    mutationError,
    pendingAction,
    loadItems,
    mutate,
    refresh,
    setMutationError
  } = useCollectionPanelState<MemoryItem>(loader, "无法读取本地记忆");

  const refreshMemories = async () => {
    const refreshed = await refresh();
    if (refreshed) {
      setQuery("");
      setSearchResultQuery(null);
    }
    return refreshed;
  };

  const search = async () => {
    const normalizedQuery = query.trim();
    if (!normalizedQuery) {
      await refreshMemories();
      return;
    }
    if (await loadItems(() => api.recallMemory(normalizedQuery, { k: 10 }), "搜索记忆失败")) {
      setSearchResultQuery(normalizedQuery);
    }
  };

  const save = async () => {
    setMutationError(null);
    const content = draftContent.trim();
    if (!content) {
      setMutationError("请填写记忆内容");
      return;
    }
    const tags = draftTags
      .split(/[,，;；\s]+/)
      .map((tag) => tag.trim())
      .filter(Boolean);
    await mutate("save", () => api.saveMemory(content, { tags }), "保存记忆失败", async () => {
      setDraftContent("");
      setDraftTags("");
      await refreshMemories();
    });
  };

  const forget = async (item: MemoryItem) => {
    await mutate(`forget:${item.id}`, () => api.forgetMemory(item.id), "删除记忆失败", refreshMemories);
  };

  const promote = async (item: MemoryItem) => {
    const resolveConflict = item.conflict_status === "conflicting";
    await mutate(
      `promote:${item.id}`,
      () => api.promoteMemory(item.id, { reviewedBy: "desktop-user", resolveConflict }),
      resolveConflict ? "解决冲突并启用记忆失败" : "启用记忆失败",
      refreshMemories
    );
  };

  const revoke = async (item: MemoryItem) => {
    await mutate(
      `revoke:${item.id}`,
      () => api.revokeMemory(item.id, { reviewedBy: "desktop-user" }),
      "撤销记忆失败",
      refreshMemories
    );
  };

  const isMutating = pendingAction !== null;

  return (
    <Panel title="我的记忆" eyebrow="本地知识库" action={<Badge tone={loadError ? "danger" : "info"}>{items.length} 条</Badge>}>
      <div className="memory-panel" aria-busy={isLoading || isMutating}>
        <div className="memory-search">
          <Search size={15} aria-hidden="true" />
          <input
            aria-label="搜索记忆内容"
            value={query}
            disabled={isLoading || isMutating}
            onChange={(event) => setQuery(event.currentTarget.value)}
            placeholder="搜索记忆内容…"
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.nativeEvent.isComposing && !isLoading && !isMutating) void search();
            }}
          />
          <button
            className="button button--ghost"
            type="button"
            disabled={isLoading || isMutating}
            aria-label="执行记忆搜索"
            aria-busy={isLoading}
            onClick={() => void search()}
          >
            搜索
          </button>
        </div>

        <div className="memory-form">
          <label className="field">
            <span>新增记忆</span>
            <textarea
              aria-label="记忆内容"
              value={draftContent}
              disabled={pendingAction === "save"}
              onChange={(event) => setDraftContent(event.currentTarget.value)}
              rows={3}
              placeholder="例如：用户偏好按月份归档发票"
            />
          </label>
          <label className="field">
            <span>标签（逗号分隔）</span>
            <input
              aria-label="记忆标签"
              value={draftTags}
              disabled={pendingAction === "save"}
              onChange={(event) => setDraftTags(event.currentTarget.value)}
              placeholder="preference, invoice"
            />
          </label>
          <button
            className="button button--primary"
            type="button"
            aria-label="保存记忆"
            aria-busy={pendingAction === "save"}
            disabled={isMutating}
            onClick={() => void save()}
          >
            <Plus size={16} aria-hidden="true" />
            <span>{pendingAction === "save" ? "正在保存" : "记住这条"}</span>
          </button>
          {mutationError ? <p className="field-error panel-inline-error" role="alert">{mutationError}</p> : null}
        </div>

        <CollectionPanelStatus
          isLoading={isLoading}
          loadError={loadError}
          loadingLabel="正在读取记忆…"
          onRetry={() => void refreshMemories()}
        />

        {items.length ? (
          <ul className="memory-list" aria-label="记忆列表">
            {items.map((item) => {
              const isForgetting = pendingAction === `forget:${item.id}`;
              const isPromoting = pendingAction === `promote:${item.id}`;
              const isRevoking = pendingAction === `revoke:${item.id}`;
              const state = item.state ?? "active";
              const conflict = item.conflict_status ?? "none";
              const needsPromotion = state !== "active" || conflict === "conflicting";
              return (
                <li key={item.id} className="memory-row" aria-busy={isForgetting || isPromoting || isRevoking}>
                  <div className="memory-meta muted">
                    <Brain size={14} aria-hidden="true" />
                    <span className="memory-kind">{item.kind}</span>
                    <span className={`memory-state memory-state--${state}`}>{memoryStateLabel(state)}</span>
                    <span>{`v${item.version ?? 1}`}</span>
                    {conflict !== "none" ? (
                      <span className={`memory-conflict memory-conflict--${conflict}`}>{memoryConflictLabel(conflict)}</span>
                    ) : null}
                    {item.user_confirmed === false ? <span>未人工确认</span> : null}
                    {item.tags?.length ? <span className="memory-tags">{item.tags.join(" · ")}</span> : null}
                  </div>
                  <p className="memory-content">{item.content}</p>
                  <div className="memory-actions">
                    <span className="muted">
                      {item.created_at ? new Date(item.created_at).toLocaleString() : ""}
                      {item.use_count ? ` · 被引用 ${item.use_count} 次` : ""}
                      {item.reviewed_by ? ` · 最近由 ${item.reviewed_by} 审阅` : ""}
                    </span>
                    {needsPromotion ? (
                      <button
                        className="button button--ghost"
                        type="button"
                        aria-label={`审核并启用记忆：${item.content.slice(0, 32)}`}
                        aria-busy={isPromoting}
                        disabled={isMutating}
                        onClick={() => void promote(item)}
                      >
                        <ShieldCheck size={14} aria-hidden="true" />
                        <span>{isPromoting ? "正在启用" : conflict === "conflicting" ? "解决冲突并启用" : "审核并启用"}</span>
                      </button>
                    ) : null}
                    {state !== "revoked" ? (
                      <button
                        className="button button--ghost"
                        type="button"
                        aria-label={`撤销记忆：${item.content.slice(0, 32)}`}
                        aria-busy={isRevoking}
                        disabled={isMutating}
                        onClick={() => void revoke(item)}
                      >
                        <Ban size={14} aria-hidden="true" />
                        <span>{isRevoking ? "正在撤销" : "撤销"}</span>
                      </button>
                    ) : null}
                    <button
                      className="button button--ghost"
                      type="button"
                      aria-label={`忘记记忆：${item.content.slice(0, 32)}`}
                      aria-busy={isForgetting}
                      disabled={isMutating}
                      onClick={() => void forget(item)}
                    >
                      <Trash2 size={14} aria-hidden="true" />
                      <span>{isForgetting ? "正在删除" : "忘记"}</span>
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        ) : null}

        {!isLoading && !loadError && items.length === 0 ? (
          <p className="empty-state memory-empty">
            {searchResultQuery ? "没有找到匹配的记忆。换个关键词再试试。" : "还没有记忆。在上方输入一条试试。"}
          </p>
        ) : null}
      </div>
    </Panel>
  );
}

function memoryStateLabel(state: NonNullable<BackendMemory["state"]>): string {
  if (state === "quarantined") return "待审阅";
  if (state === "revoked") return "已撤销";
  return "已启用";
}

function memoryConflictLabel(conflict: NonNullable<BackendMemory["conflict_status"]>): string {
  if (conflict === "conflicting") return "存在冲突";
  if (conflict === "resolved") return "冲突已解决";
  if (conflict === "superseded") return "已被新版本替代";
  return "无冲突";
}
