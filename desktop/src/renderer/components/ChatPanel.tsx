import { AlertTriangle, Bot, Brain, CheckCircle2, CircleDashed, Pencil, Play, Send, Sparkles, XCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { ChatMessage, ChatMessagePart, IntentSuggestion } from "../../shared/types";
import { zhUserFacingError } from "../lib/zh";
import { Badge, Panel } from "./Panel";

interface ChatPanelProps {
  messages: ChatMessage[];
  connectionState: "online" | "offline" | "checking";
  onSend: (content: string) => Promise<SendResult>;
  onExecuteSuggestion?: (suggestion: IntentSuggestion) => Promise<void>;
  initialDraft?: string;
  autoFocus?: boolean;
  suggestions?: IntentSuggestion[];
}

type SendResult = { ok: boolean; error?: string } | void;

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
  const [submitError, setSubmitError] = useState<string | null>(null);
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
    setSubmitError(null);
    setIsSending(true);
    try {
      const result = await onSend(content);
      if (isSendFailure(result)) {
        setDraft(content);
        setSubmitError(result.error ?? "消息没有发送成功，输入内容已保留，可以稍后重试。");
        window.setTimeout(() => inputRef.current?.focus(), 0);
      }
    } catch (error) {
      setDraft(content);
      setSubmitError(formatSendError(error));
      window.setTimeout(() => inputRef.current?.focus(), 0);
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
      title="对话"
      eyebrow="Lengrvis"
      className="panel--chat"
      action={connectionState === "online" ? null : (
        <Badge tone="warning">{connectionState === "checking" ? "正在连接 Lengrvis" : "助手暂时连不上"}</Badge>
      )}
    >
      <div className="chat-log" aria-live="polite">
        {messages.length ? (
          messages.map((message) => (
            <article className={`chat-message chat-message--${message.role}`} key={message.id}>
              <div className="chat-message__meta">
                <strong>{friendlyAuthor(message)}</strong>
                <time>{formatTime(message.createdAt)}</time>
              </div>
              <MessageContent message={message} />
            </article>
          ))
        ) : (
          <p className="empty-state">暂无对话。发送消息后会显示真实回复。</p>
        )}
      </div>
      {suggestions.length ? (
        <div className="intent-suggestions" aria-label="建议">
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
                  {executingSuggestionId === suggestion.id ? "正在执行" : "执行"}
                </button>
                <button
                  className="button button--ghost button--small"
                  type="button"
                  onClick={() => editSuggestion(suggestion)}
                >
                  <Pencil size={14} aria-hidden="true" />
                  编辑
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
          placeholder="让 Lengrvis 帮你找文件、总结文档，或检查这台电脑。"
          rows={3}
          aria-invalid={Boolean(submitError)}
          aria-describedby={submitError ? "chat-composer-error" : undefined}
        />
        <button
          className="button button--primary composer__send"
          onClick={() => void submit()}
          disabled={isSending || !hasDraft}
        >
          <Send size={16} aria-hidden="true" />
          {isSending ? "发送中" : "发送"}
        </button>
        {submitError ? (
          <p className="field-error composer__error" id="chat-composer-error" role="alert">
            {submitError}
          </p>
        ) : null}
      </div>
    </Panel>
  );
}

function isSendFailure(result: SendResult): result is { ok: false; error?: string } {
  return Boolean(result && typeof result === "object" && "ok" in result && !result.ok);
}

function formatSendError(error: unknown): string {
  const message = error instanceof Error ? error.message : typeof error === "string" ? error : "";
  return zhUserFacingError(message || "消息没有发送成功，输入内容已保留，可以稍后重试。");
}

function MessageContent({ message }: { message: ChatMessage }) {
  const parts = normalizeMessageParts(message);

  return (
    <div className="chat-message__parts">
      {parts.map((part, index) => (
        <MessagePartView part={part} key={`${part.type}-${index}`} />
      ))}
    </div>
  );
}

function MessagePartView({ part }: { part: ChatMessagePart }) {
  if (part.type === "tool_call") {
    const tone = part.status === "success" ? "success" : part.status === "error" ? "danger" : "info";
    const Icon = part.status === "success" ? CheckCircle2 : part.status === "error" ? XCircle : CircleDashed;

    return (
      <section className={`message-part message-part--tool message-part--tool-${part.status}`}>
        <div className="message-part__head">
          <Icon size={14} aria-hidden="true" />
          <strong>{part.title || part.toolName}</strong>
          <Badge tone={tone}>{toolStatusLabel(part.status)}</Badge>
        </div>
        {part.input ? <pre>{part.input}</pre> : null}
        {part.output ? <p>{part.status === "error" ? friendlyMessageText(part.output) : part.output}</p> : null}
        {part.error ? <p className="message-part__error">{friendlyMessageText(part.error)}</p> : null}
      </section>
    );
  }

  const Icon = part.type === "reasoning"
    ? Brain
    : part.type === "subagent"
      ? Bot
      : part.type === "error"
        ? AlertTriangle
        : part.type === "cancelled"
          ? XCircle
          : null;

  if (part.type === "text") {
    return <p className="message-part message-part--text">{part.text}</p>;
  }

  return (
    <section className={`message-part message-part--${part.type}`}>
      <div className="message-part__head">
        {Icon ? <Icon size={14} aria-hidden="true" /> : null}
        <strong>{part.title || part.agent || messagePartLabel(part.type)}</strong>
      </div>
      <p>{part.type === "error" ? friendlyMessageText(part.text) : part.text}</p>
    </section>
  );
}

function normalizeMessageParts(message: ChatMessage): ChatMessagePart[] {
  if (Array.isArray(message.content)) return message.content;
  const text = message.content || "";

  if (message.status === "failed") {
    return [{ type: "error", title: "执行失败", text }];
  }

  if (message.role === "tool") {
    return [{ type: "tool_call", toolName: message.author || "tool", status: "success", output: text }];
  }

  return [{ type: "text", text }];
}

function friendlyMessageText(text: string): string {
  const friendly = zhUserFacingError(text);
  return friendly === text ? text : friendly;
}

function toolStatusLabel(status: "running" | "success" | "error"): string {
  if (status === "running") return "运行中";
  if (status === "success") return "成功";
  return "失败";
}

function messagePartLabel(type: ChatMessagePart["type"]): string {
  if (type === "reasoning") return "推理";
  if (type === "subagent") return "协作助手";
  if (type === "error") return "错误";
  if (type === "cancelled") return "已取消";
  return "消息";
}

function friendlyAuthor(message: ChatMessage): string {
  if (message.role === "user") return "你";
  if (message.role === "assistant") return friendlyAgentName(message.author || "Lengrvis");
  return friendlyAgentName(message.author || "工具");
}

function friendlyAgentName(value: string): string {
  return value
    .replace(/主管\s*Agent/gi, "Lengrvis")
    .replace(/Orchestrator\s*Agent/gi, "Lengrvis")
    .replace(/Planner\s*Agent/gi, "规划助手")
    .replace(/File\s*Agent|文件\s*Agent/gi, "文件助手")
    .replace(/Document\s*Agent|文档\s*Agent/gi, "文档助手")
    .replace(/Computer\s*Agent|电脑\s*Agent/gi, "电脑助手")
    .replace(/Browser\s*Agent|浏览器\s*Agent/gi, "网页助手")
    .replace(/Search\s*Agent|搜索\s*Agent/gi, "搜索助手")
    .replace(/SafetyReview\s*Agent|安全审核\s*Agent/gi, "安全助手")
    .replace(/\s*Agent\b/gi, "助手")
    .trim();
}

function formatConfidence(value: number): string {
  const normalized = value > 1 ? value : value * 100;
  return `${Math.round(normalized)}%`;
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "--:--";
  }
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit"
  }).format(date);
}
