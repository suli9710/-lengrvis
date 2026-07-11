import { ChevronDown, Wrench } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { useUiPreferences } from "../lib/uiPreferences";

interface TechnicalDetailsProps {
  title?: string;
  description?: string;
  children?: ReactNode;
  className?: string;
  testId?: string;
  emptyMessage?: string;
  resetKey?: string;
}

export function TechnicalDetails({
  title = "技术详情",
  description = "执行链路、权限边界、证据与诊断信息",
  children,
  className = "",
  testId,
  emptyMessage = "暂无可用的技术详情。",
  resetKey = ""
}: TechnicalDetailsProps) {
  const { preferences } = useUiPreferences();
  const classes = ["technical-details", className].filter(Boolean).join(" ");
  const [isOpen, setIsOpen] = useState(preferences.detailMode === "expert");

  useEffect(() => {
    setIsOpen(preferences.detailMode === "expert");
  }, [preferences.detailMode, resetKey]);

  return (
    <details
      className={classes}
      open={isOpen}
      onToggle={(event) => setIsOpen(event.currentTarget.open)}
      data-testid={testId}
    >
      <summary className="technical-details__summary">
        <span className="technical-details__icon" aria-hidden="true">
          <Wrench size={14} />
        </span>
        <span className="technical-details__summary-copy">
          <strong>{title}</strong>
          <em>{description}</em>
        </span>
        <ChevronDown className="technical-details__chevron" size={16} aria-hidden="true" />
      </summary>
      <div className="technical-details__body">
        {children ?? <p className="technical-details__empty">{emptyMessage}</p>}
      </div>
    </details>
  );
}
