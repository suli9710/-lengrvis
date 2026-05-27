import { Pencil, Play, Send, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { ChatMessage, IntentSuggestion } from "../../shared/types";
import { Badge, Panel } from "./Panel";

interface ChatPanelProps {
  messages: ChatMessage[];
  connectionState: "online" | "offline" | "checking";
  onSend: (content: string) => Promise<void>;
  onExecuteSuggestion?: (suggestion: IntentSuggestion) => Promise<void>;
  initialDraft?: string;
  autoFocus?: boolean;
  suggestions?: IntentSuggestion[];
}

export function ChatPanel({
  messages,
  connectionState,
  onSend,
  onExecuteSuggestion,
  initialDraft = "",
  autoFocus = false,
  suggestions = []
}: ChatPanelProps) {
  const [draft, setDraft] = useState(initialDraft);
  const [isSending, setIsSending] = useState(false);
  const [executingSuggestionId, setExecutingSuggestionId] = useState<string | null>(null);
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

  const editSuggestion = (suggestion: IntentSuggestion) => {
    setDraft(suggestion.prompt);
    window.setTimeout(() => inputRef.current?.focus(), 0);
  };

  const executeSuggestion = async (suggestion: IntentSuggestion) => {
    if (!onExecuteSuggestion || executingSuggestionId) return;
    setExecutingSuggestionId(suggestion.id);
    try {
      await onExecuteSuggestion(suggestion);
    } finally {
      setExecutingSuggestionId(null);
    }
  };

  return (
    <Panel
      title="Chat"
      eyebrow="Mavris"
      className="panel--chat"
      action={connectionState === "online" ? null : (
        <Badge tone="warning">{connectionState === "checking" ? "Checking connection" : "Offline"}</Badge>
      )}
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
        <div className="intent-suggestions" aria-label="Suggestions">
          {suggestions.slice(0, 3).map((suggestion) => (
            <article
              className="intent-suggestion"
              key={suggestion.id}
              title={suggestion.reason}
            >
              <div className="intent-suggestion__head">
                <Sparkles size={14} aria-hidden="true" />
                <strong>{suggestion.title || suggestion.prompt}</strong>
                <span>{formatConfidence(suggestion.confidence)}</span>
              </div>
              <p>{suggestion.prompt}</p>
              {suggestion.reason ? <small>{suggestion.reason}</small> : null}
              <div className="intent-suggestion__actions">
                <button
                  className="button button--primary button--small"
                  type="button"
                  onClick={() => void executeSuggestion(suggestion)}
                  disabled={!onExecuteSuggestion || executingSuggestionId !== null}
                >
                  <Play size={14} aria-hidden="true" />
                  {executingSuggestionId === suggestion.id ? "Executing" : "Execute"}
                </button>
                <button
                  className="button button--ghost button--small"
                  type="button"
                  onClick={() => editSuggestion(suggestion)}
                >
                  <Pencil size={14} aria-hidden="true" />
                  Edit
                </button>
              </div>
            </article>
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
          placeholder="Ask Mavris to find a file, summarize a document, or check this computer."
          rows={3}
        />
        <button
          className="button button--primary composer__send"
          onClick={() => void submit()}
          disabled={isSending || !hasDraft}
        >
          <Send size={16} aria-hidden="true" />
          {isSending ? "Sending" : "Send"}
        </button>
      </div>
    </Panel>
  );
}

function formatConfidence(value: number): string {
  const normalized = value > 1 ? value : value * 100;
  return `${Math.round(normalized)}%`;
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date(value));
}
