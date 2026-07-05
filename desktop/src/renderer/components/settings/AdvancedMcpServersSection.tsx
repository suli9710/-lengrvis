import { Plus, Trash2 } from "lucide-react";
import type { Dispatch, SetStateAction } from "react";

import type { AppSettings } from "../../../shared/settingsTypes";
import { addMcpServer, removeMcpServer, splitSettingList, updateMcpServer } from "./SettingsPanelHelpers";

type SetDraft = Dispatch<SetStateAction<AppSettings>>;

export function McpServersSection({ draft, setDraft }: { draft: AppSettings; setDraft: SetDraft }) {
  return (
    <fieldset className="mcp-servers">
      <legend>工具连接</legend>
      {draft.mcpServers.length === 0 ? <p className="muted">尚未配置工具连接。</p> : null}
      <ul className="mcp-servers__list">
        {draft.mcpServers.map((server, index) => (
          <li className="mcp-servers__row mcp-servers__row--server" key={index}>
            <input
              placeholder="名称"
              value={server.name}
              onChange={(event) => updateMcpServer(setDraft, index, { name: event.target.value })}
            />
            <input
              placeholder="URL"
              value={server.url}
              onChange={(event) => updateMcpServer(setDraft, index, { url: event.target.value })}
            />
            <input
              placeholder="命令"
              value={server.command ?? ""}
              onChange={(event) => updateMcpServer(setDraft, index, { command: event.target.value })}
            />
            <input
              placeholder="参数"
              value={server.args?.join("; ") ?? ""}
              onChange={(event) => updateMcpServer(setDraft, index, { args: splitSettingList(event.target.value) })}
            />
            <input
              placeholder="传输方式"
              value={server.transport ?? ""}
              onChange={(event) => updateMcpServer(setDraft, index, { transport: event.target.value })}
            />
            <label className="mcp-servers__toggle">
              <input
                type="checkbox"
                checked={server.enabled}
                onChange={(event) => updateMcpServer(setDraft, index, { enabled: event.target.checked })}
              />
              <span>启用</span>
            </label>
            <button
              type="button"
              className="button button--ghost"
              onClick={() => removeMcpServer(setDraft, index)}
              aria-label="删除工具连接"
            >
              <Trash2 size={14} aria-hidden="true" />
            </button>
          </li>
        ))}
      </ul>
      <button type="button" className="button button--ghost" onClick={() => addMcpServer(setDraft)}>
        <Plus size={14} aria-hidden="true" />
        添加工具连接
      </button>
    </fieldset>
  );
}
