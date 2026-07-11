import { Bot, CircleDot } from "lucide-react";

import type { AgentConversation } from "../../shared/executionTypes";
import { sanitizeTechnicalText } from "../lib/technicalDetails";
import { zhAgentName, zhConversationStatus, zhMessageKind, zhRole, zhToolName } from "../lib/zh";
import { Badge, Panel } from "./Panel";
import { TechnicalDetails } from "./TechnicalDetails";

interface AgentConversationPanelProps {
  conversations: AgentConversation[];
}

export function AgentConversationPanel({ conversations }: AgentConversationPanelProps) {
  return (
    <Panel title="Agent 协作" eyebrow="渐进式专业信息" className="panel--agent-conversations">
      <div className="agent-stack">
        {conversations.length ? conversations.map((conversation) => (
          <article className="agent-thread" key={conversation.id}>
            <div className="row row--between">
              <div className="agent-thread__title">
                <Bot size={16} aria-hidden="true" />
                <strong>{conversation.title}</strong>
              </div>
              <Badge tone={conversation.status === "running" ? "info" : conversation.status === "waiting" ? "warning" : "neutral"}>
                {zhConversationStatus(conversation.status)}
              </Badge>
            </div>
            <p className="agent-thread__summary">{conversationSummary(conversation)}</p>
            <TechnicalDetails
              title="协作记录"
              description={`${conversation.messages.length} 条结构化执行消息与工具记录`}
              className="technical-details--agent-thread"
              resetKey={conversation.id}
            >
              <div className="agent-messages">
                {conversation.messages.length ? conversation.messages.map((message) => (
                  <div className="agent-message" key={message.id}>
                    <CircleDot size={10} aria-hidden="true" />
                    <div>
                      <span className="muted">
                        {zhAgentName(message.name ?? message.agent ?? message.role)} / {zhRole(message.role)}
                        {message.kind ? ` / ${zhMessageKind(message.kind)}` : ""}
                      </span>
                      <p>{sanitizeTechnicalText(message.content) || "暂无消息内容"}</p>
                      {message.toolCalls?.map((toolCall) => (
                        <div className="agent-message__tool" key={toolCall.id}>
                          <strong>工具调用：{zhToolName(toolCall.function.name)}</strong>
                          {toolCall.function.arguments ? (
                            <pre>{sanitizeTechnicalText(toolCall.function.arguments)}</pre>
                          ) : null}
                        </div>
                      ))}
                    </div>
                  </div>
                )) : <p className="technical-details__empty">暂无协作消息。</p>}
              </div>
            </TechnicalDetails>
          </article>
        )) : <p className="empty-state">暂无 Agent 协作记录。任务开始后会在这里显示摘要。</p>}
      </div>
    </Panel>
  );
}

function conversationSummary(conversation: AgentConversation): string {
  const count = conversation.messages.length;
  if (conversation.status === "running") return `正在协作，已有 ${count} 条执行更新。`;
  if (conversation.status === "waiting") return `协作暂时等待外部条件，已有 ${count} 条记录。`;
  if (conversation.status === "done") return `协作已完成，共记录 ${count} 条执行更新。`;
  return count ? `当前有 ${count} 条协作记录。` : "尚未产生协作消息。";
}
