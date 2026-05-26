import { Sparkles, Send } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { ChatMessage, IntentSuggestion } from "../../shared/types";
import { zhConnectionState } from "../lib/zh";
import { Badge, Panel } from "./Panel";

interface ChatPanelProps {
  messages: ChatMessage[];
  connectionState: "online" | "offline" | "checking";
  onSend: (content: string) => Promise<void>;
  initialDraft?: string;
  autoFocus?: boolean;
  suggestions?: IntentSuggestion[];
}

export function ChatPanel({
  messages,
  connectionState,
  onSend,
  initialDraft = "",
  autoFocus = false,
  suggestions = []
}: ChatPanelProps) {
  const [draft, setDraft] = useState(initialDraft);
  const [isSending, setIsSending] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const hasDraft = draft.trim().length > 0;

  useEffect(() => {
    setDraft(initialDraft);
  }, [initialDraft]);

  useEffect(() => {
    if (!autoFocus) return;
    const focusId = window.setTimeout(() => inputRef.current?.focus(), 60);
    return () => window.clearTimeout(focusId);
  }, [autoFocus]);

  const submit = async () => {
    const content = draft.trim();
    if (!content || isSending) {
      return;
    }

    setDraft("");
    setIsSending(true);
    try {
      await onSend(content);
    } finally {
      setIsSending(false);
    }
  };

  return (
    <Panel
      title="问 Marvis"
      eyebrow="电脑 AI 管家"
      className="panel--chat"
      action={<Badge tone={connectionState === "online" ? "success" : "warning"}>{zhConnectionState(connectionState)}</Badge>}
    >
      <div className="chat-log" aria-live="polite">
        {messages.map((message) => (
          <article className={`chat-message chat-message--${message.role}`} key={message.id}>
            <div className="chat-message__meta">
              <strong>{message.author}</strong>
              <time>{formatTime(message.createdAt)}</time>
            </div>
            <p>{message.content}</p>
          </article>
        ))}
      </div>
      {suggestions.length ? (
        <div className="intent-suggestions" aria-label="Proactive suggestions">
          {suggestions.slice(0, 3).map((suggestion) => (
            <button
              className="intent-suggestion"
              key={suggestion.id}
              type="button"
              onClick={() => setDraft(suggestion.prompt)}
              title={suggestion.reason}
            >
              <Sparkles size={14} aria-hidden="true" />
              <span>{suggestion.prompt}</span>
            </button>
          ))}
        </div>
      ) : null}
      <div className="composer">
        <textarea
          ref={inputRef}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
              event.preventDefault();
              void submit();
            }
          }}
          placeholder="例如：帮我找上周的合同、查电脑配置、把发票整理到文件夹"
          rows={3}
        />
        <button
          className="button button--primary composer__send"
          onClick={() => void submit()}
          disabled={isSending || !hasDraft}
        >
          <Send size={16} aria-hidden="true" />
          {isSending ? "发送中" : "发送"}
        </button>
      </div>
    </Panel>
  );
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date(value));
}
