import { AlertTriangle, Bot, CheckCircle2, CircleDashed, Pencil, Play, Send, Sparkles, XCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { ChatMessage, ChatMessagePart, IntentSuggestion } from "../../shared/catalogTypes";
import type { LengrvisApiClient } from "../lib/apiClient";
import { sanitizeTechnicalText } from "../lib/technicalDetails";
import { zhUserFacingError } from "../lib/zh";
import { Badge, Panel } from "./Panel";
import { TechnicalDetails } from "./TechnicalDetails";
import { VoiceInputButton } from "./VoiceInputButton";

interface ChatPanelProps {
  messages: ChatMessage[];
  connectionState: "online" | "offline" | "checking";
  onSend: (content: string) => Promise<SendResult>;
  onExecuteSuggestion?: (suggestion: IntentSuggestion) => Promise<void>;
  initialDraft?: string;
  autoFocus?: boolean;
  suggestions?: IntentSuggestion[];
  api?: LengrvisApiClient;
}

type SendResult = { ok: boolean; error?: string } | void;

export function ChatPanel({
  messages,
  connectionState,
  onSend,
  onExecuteSuggestion,
  initialDraft = "",
  autoFocus = false,
  suggestions = [],
  api
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
    } catch (error) { // broad-exception-boundary
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
        {isSending ? (
          <article className="chat-message chat-message--assistant chat-message--pending" aria-label="Lengrvis 正在处理">
            <div className="chat-message__meta">
              <strong>Lengrvis</strong>
            </div>
            <span className="chat-typing" aria-hidden="true">
              <span />
              <span />
              <span />
            </span>
          </article>
        ) : null}
      </div>
      {suggestions.length ? (
        <div className="intent-suggestions" aria-label="建议">
          {suggestions.slice(0, 3).map((suggestion) => (
            <article
              className="intent-suggestion"
              key={suggestion.id}
            >
              <div className="intent-suggestion__head">
                <Sparkles size={14} aria-hidden="true" />
                <strong>{suggestion.title || suggestion.prompt}</strong>
                <span>{formatSuggestionStrength(suggestion.confidence)}</span>
              </div>
              <p>{suggestion.prompt}</p>
              <small>匹配原因：{suggestion.reason || "根据当前可见窗口与任务上下文匹配"}</small>
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
        <div className="composer__actions">
          {api ? (
            <VoiceInputButton
              api={api}
              disabled={isSending}
              onTranscript={(transcript) => {
                setSubmitError(null);
                setDraft((current) => (current.trim() ? `${current.trimEnd()} ${transcript}` : transcript));
                window.setTimeout(() => inputRef.current?.focus(), 0);
              }}
              onError={(message) => setSubmitError(message)}
            />
          ) : null}
          <button
            className="button button--primary composer__send"
            onClick={() => void submit()}
            disabled={isSending || !hasDraft}
          >
            <Send size={16} aria-hidden="true" />
            {isSending ? "发送中" : "发送"}
          </button>
        </div>
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
    const hasTechnicalPayload = Boolean(part.input || part.output || part.error);

    return (
      <section className={`message-part message-part--tool message-part--tool-${part.status}`}>
        <div className="message-part__head">
          <Icon size={14} aria-hidden="true" />
          <strong>{part.title || part.toolName}</strong>
          <Badge tone={tone}>{toolStatusLabel(part.status)}</Badge>
        </div>
        <p className="message-part__summary">{toolStatusSummary(part.status)}</p>
        {part.status === "error" ? (
          <p className="message-part__next-step">可以重试这一步，或缩小任务范围后再发送。</p>
        ) : null}
        {hasTechnicalPayload ? (
          <TechnicalDetails
            title="技术详情"
            description="查看调用参数、输出和脱敏诊断"
            className="technical-details--message"
            resetKey={`${part.toolName}:${part.status}`}
          >
            <div className="message-part__technical-grid">
              {part.input ? <TechnicalMessageDatum label="调用参数" value={part.input} /> : null}
              {part.output ? <TechnicalMessageDatum label="工具输出" value={part.output} /> : null}
              {part.error ? <TechnicalMessageDatum label="失败阶段与诊断" value={part.error} tone="error" /> : null}
            </div>
          </TechnicalDetails>
        ) : null}
      </section>
    );
  }

  if (part.type === "text") {
    return <p className="message-part message-part--text">{part.text}</p>;
  }

  if (part.type === "reasoning") {
    return (
      <section className="message-part message-part--reasoning">
        <div className="message-part__head">
          <CheckCircle2 size={14} aria-hidden="true" />
          <strong>{part.title || "任务分析"}</strong>
        </div>
        <p>已完成任务分析。界面不会展示模型内部推理过程。</p>
      </section>
    );
  }

  if (part.type === "subagent") {
    return (
      <section className="message-part message-part--subagent">
        <div className="message-part__head">
          <Bot size={14} aria-hidden="true" />
          <strong>{part.title || part.agent || "协作助手"}</strong>
        </div>
        <p className="message-part__summary">协作助手已返回一条执行记录。</p>
        <TechnicalDetails
          title="协作记录"
          description="查看经过脱敏的执行消息"
          className="technical-details--message"
          resetKey={part.agent || part.title || "subagent"}
        >
          <TechnicalMessageDatum label="执行记录" value={part.text} />
        </TechnicalDetails>
      </section>
    );
  }

  const isError = part.type === "error";
  const Icon = isError ? AlertTriangle : XCircle;
  return (
    <section className={`message-part message-part--${part.type}`}>
      <div className="message-part__head">
        <Icon size={14} aria-hidden="true" />
        <strong>{part.title || messagePartLabel(part.type)}</strong>
      </div>
      <p>{isError ? "发生了什么：这一步没有完成，系统已停止以避免产生不确定结果。" : "这项操作已取消，没有继续执行。"}</p>
      {isError ? <p className="message-part__next-step">可以怎么做：重试任务，或调整目标后重新发送。</p> : null}
      {part.text ? (
        <TechnicalDetails
          title="技术详情"
          description="查看经过脱敏的底层原因"
          className="technical-details--message"
          resetKey={part.type}
        >
          <TechnicalMessageDatum label="原始诊断" value={part.text} tone={isError ? "error" : undefined} />
        </TechnicalDetails>
      ) : null}
    </section>
  );
}

function TechnicalMessageDatum({
  label,
  value,
  tone
}: {
  label: string;
  value: string;
  tone?: "error";
}) {
  const redacted = sanitizeTechnicalText(value);
  return (
    <div className={tone === "error" ? "technical-datum technical-datum--error" : "technical-datum"}>
      <strong>{label}</strong>
      <pre>{redacted || "暂无内容"}</pre>
    </div>
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

function toolStatusLabel(status: "running" | "success" | "error"): string {
  if (status === "running") return "运行中";
  if (status === "success") return "成功";
  return "失败";
}

function toolStatusSummary(status: "running" | "success" | "error"): string {
  if (status === "running") return "正在完成这一步，结束后会自动继续。";
  if (status === "success") return "这一步已完成，结果已交回任务流程。";
  return "这一步未能完成，任务已停在安全位置。";
}

function messagePartLabel(type: ChatMessagePart["type"]): string {
  if (type === "reasoning") return "任务分析";
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

function formatSuggestionStrength(value: number): string {
  const score = Number.isFinite(value) ? value : 0;
  const normalized = Math.max(0, Math.min(1, score > 1 ? score / 100 : score));
  if (normalized >= 0.9) return "建议强度：高";
  if (normalized >= 0.8) return "建议强度：中";
  return "建议强度：低";
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
