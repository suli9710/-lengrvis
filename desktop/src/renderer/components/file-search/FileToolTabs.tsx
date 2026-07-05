import { FileText, Search, Trash2, type LucideIcon } from "lucide-react";

import type { FileToolTabValue } from "./FileSearchModels";

const TOOL_TABS: Array<{ id: FileToolTabValue; label: string; description: string; icon: LucideIcon }> = [
  { id: "search", label: "搜索", description: "查找文件", icon: Search },
  { id: "document", label: "文档", description: "读取和提问", icon: FileText },
  { id: "cleanup", label: "清理", description: "先预览", icon: Trash2 }
];

export function FileToolTabs({
  activeTool,
  onSelectTool
}: {
  activeTool: FileToolTabValue;
  onSelectTool: (tool: FileToolTabValue) => void;
}) {
  return (
    <div className="file-tool-tabs" role="tablist" aria-label="文件工具类型">
      {TOOL_TABS.map((tab) => {
        const Icon = tab.icon;
        return (
          <button
            key={tab.id}
            className={activeTool === tab.id ? "file-tool-tab file-tool-tab--active" : "file-tool-tab"}
            type="button"
            role="tab"
            aria-selected={activeTool === tab.id}
            onClick={() => onSelectTool(tab.id)}
          >
            <Icon size={15} aria-hidden="true" />
            <span>{tab.label}</span>
            <small>{tab.description}</small>
          </button>
        );
      })}
    </div>
  );
}
