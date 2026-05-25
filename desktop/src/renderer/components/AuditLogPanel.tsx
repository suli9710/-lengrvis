import { ScrollText } from "lucide-react";

import type { AuditLogEntry } from "../../shared/types";
import { zhAgentName, zhAuditAction, zhAuditLevel } from "../lib/zh";
import { Badge, Panel } from "./Panel";

interface AuditLogPanelProps {
  entries: AuditLogEntry[];
}

export function AuditLogPanel({ entries }: AuditLogPanelProps) {
  return (
    <Panel title="审计日志" eyebrow="事件记录" action={<Badge tone="neutral">{entries.length}</Badge>}>
      <div className="audit-table" role="table" aria-label="审计日志">
        {entries.map((entry) => (
          <div className="audit-row" role="row" key={entry.id}>
            <ScrollText size={15} aria-hidden="true" />
            <div role="cell">
              <strong>{zhAuditAction(entry.action)}</strong>
              <span className="muted">{zhAgentName(entry.actor)}</span>
            </div>
            <div role="cell">{entry.target}</div>
            <div role="cell">
              <Badge tone={entry.level === "error" ? "danger" : entry.level === "warning" ? "warning" : "neutral"}>
                {zhAuditLevel(entry.level)}
              </Badge>
            </div>
            <time role="cell">{new Date(entry.createdAt).toLocaleTimeString()}</time>
          </div>
        ))}
      </div>
    </Panel>
  );
}
