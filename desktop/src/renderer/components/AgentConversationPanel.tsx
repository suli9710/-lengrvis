import { Bot, CircleDot } from "lucide-react";

import type { AgentConversation } from "../../shared/types";
import { zhAgentName, zhConversationStatus, zhMessageKind, zhRole, zhToolName } from "../lib/zh";
import { Badge, Panel } from "./Panel";

interface AgentConversationPanelProps {
  conversations: AgentConversation[];
}

export function AgentConversationPanel({ conversations }: AgentConversationPanelProps) {
  return (
    <Panel title="Agent 协作记录" eyebrow="多 Agent 协调">
      <div className="agent-stack">
        {conversations.map((conversation) => (
          <article className="agent-thread" key={conversation.id}>
            <div className="row row--between">
              <div className="agent-thread__title">
                <Bot size={16} aria-hidden="true" />
                <strong>{conversation.title}</strong>
              </div>
              <Badge tone={conversation.status === "running" ? "info" : "neutral"}>
                {zhConversationStatus(conversation.status)}
              </Badge>
            </div>
            <div className="agent-messages">
              {conversation.messages.map((message) => (
                <div className="agent-message" key={message.id}>
                  <CircleDot size={10} aria-hidden="true" />
                  <div>
                    <span className="muted">
                      {zhAgentName(message.name ?? message.agent ?? message.role)} / {zhRole(message.role)}
                      {message.kind ? ` / ${zhMessageKind(message.kind)}` : ""}
                    </span>
                    <p>{message.content}</p>
                    {message.toolCalls?.map((toolCall) => (
                      <p className="muted" key={toolCall.id}>
                        工具调用：{zhToolName(toolCall.function.name)}
                      </p>
                    ))}
                    {rationaleText(message.metadata) ? (
                      <details className="agent-message__rationale">
                        <summary>推理理由</summary>
                        <p>{rationaleText(message.metadata)}</p>
                      </details>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          </article>
        ))}
      </div>
    </Panel>
  );
}

function rationaleText(metadata: Record<string, unknown> | undefined): string {
  if (!metadata) return "";
  const direct = metadata.rationale;
  if (typeof direct === "string" && direct.trim()) return direct.trim();
  const payload = metadata.structured_payload;
  if (payload && typeof payload === "object" && payload !== null) {
    const nested = (payload as Record<string, unknown>).rationale;
    if (typeof nested === "string" && nested.trim()) return nested.trim();
  }
  return "";
}
