import { Brain, Plus, Search, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { MavrisApiClient } from "../lib/apiClient";
import { Badge, Panel } from "./Panel";

interface MemoryItem {
  id: string;
  kind: string;
  content: string;
  tags: string[];
  task_id?: string;
  source?: string;
  use_count?: number;
  last_used_at?: string;
  created_at?: string;
}

interface MemoryPanelProps {
  api: MavrisApiClient;
}

export function MemoryPanel({ api }: MemoryPanelProps) {
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [query, setQuery] = useState("");
  const [draftContent, setDraftContent] = useState("");
  const [draftTags, setDraftTags] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    const response = await api.listMemories();
    if (response.ok && response.data) {
      setItems(response.data as MemoryItem[]);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const search = async () => {
    if (!query.trim()) {
      await refresh();
      return;
    }
    const response = await api.recallMemory(query.trim(), { k: 10 });
    if (response.ok && response.data) {
      setItems(response.data as MemoryItem[]);
    }
  };

  const save = async () => {
    setError(null);
    if (!draftContent.trim()) {
      setError("请填写记忆内容");
      return;
    }
    setIsSaving(true);
    const tags = draftTags
      .split(/[,，;；\s]+/)
      .map((t) => t.trim())
      .filter(Boolean);
    const response = await api.saveMemory(draftContent.trim(), { tags });
    setIsSaving(false);
    if (!response.ok) {
      setError(response.error?.message ?? "保存失败");
      return;
    }
    setDraftContent("");
    setDraftTags("");
    await refresh();
  };

  const forget = async (id: string) => {
    await api.forgetMemory(id);
    await refresh();
  };

  return (
    <Panel title="我的记忆" eyebrow="本地知识库" action={<Badge tone="info">{items.length} 条</Badge>}>
      <div className="memory-search">
        <Search size={14} aria-hidden="true" />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="搜索记忆内容…"
          onKeyDown={(event) => {
            if (event.key === "Enter") void search();
          }}
        />
        <button className="button button--ghost" onClick={() => void search()}>
          搜索
        </button>
      </div>

      <div className="memory-form">
        <label className="field">
          <span>新增记忆</span>
          <textarea value={draftContent} onChange={(event) => setDraftContent(event.target.value)} rows={2} placeholder="例如：用户偏好按月份归档发票" />
        </label>
        <label className="field">
          <span>标签（逗号分隔）</span>
          <input value={draftTags} onChange={(event) => setDraftTags(event.target.value)} placeholder="preference, invoice" />
        </label>
        <button className="button button--primary" disabled={isSaving} onClick={() => void save()}>
          <Plus size={16} aria-hidden="true" />
          记住这条
        </button>
        {error ? <p className="field-error">{error}</p> : null}
      </div>

      <ul className="memory-list">
        {items.map((item) => (
          <li key={item.id} className="memory-row">
            <div className="memory-meta">
              <Brain size={14} aria-hidden="true" />
              <span className="memory-kind">{item.kind}</span>
              {item.tags?.length ? <span className="memory-tags">[{item.tags.join("、")}]</span> : null}
            </div>
            <p className="memory-content">{item.content}</p>
            <div className="memory-actions">
              <span className="muted">
                {item.created_at ? new Date(item.created_at).toLocaleString() : ""}
                {item.use_count ? ` · 被引用 ${item.use_count} 次` : ""}
              </span>
              <button className="button button--ghost" onClick={() => void forget(item.id)}>
                <Trash2 size={14} aria-hidden="true" />
                忘记
              </button>
            </div>
          </li>
        ))}
        {items.length === 0 ? <p className="memory-empty">还没有记忆。在上方输入一条试试。</p> : null}
      </ul>
    </Panel>
  );
}
