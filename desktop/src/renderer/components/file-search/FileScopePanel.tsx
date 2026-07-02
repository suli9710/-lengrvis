import { FolderOpen, type LucideIcon } from "lucide-react";
import type { Ref } from "react";

import { compactPath } from "./FileSearchModels";

export interface KnownFolderShortcut {
  id: "desktop" | "downloads" | "documents" | "pictures";
  label: string;
  icon: LucideIcon;
  path: string | null;
}

interface FileScopePanelProps {
  currentScope: string;
  hasKnownFolderShortcuts: boolean;
  isSavingScope: boolean;
  knownFoldersChecked: boolean;
  manualScope: string;
  scopeError: string | null;
  scopeNotice: string | null;
  scopePanelRef: Ref<HTMLElement>;
  shortcuts: KnownFolderShortcut[];
  onApplyManualScope: () => void;
  onChooseFolder: () => void;
  onManualScopeChange: (value: string) => void;
  onSetSearchScope: (path: string | null) => void;
}

export function FileScopePanel({
  currentScope,
  hasKnownFolderShortcuts,
  isSavingScope,
  knownFoldersChecked,
  manualScope,
  scopeError,
  scopeNotice,
  scopePanelRef,
  shortcuts,
  onApplyManualScope,
  onChooseFolder,
  onManualScopeChange,
  onSetSearchScope
}: FileScopePanelProps) {
  return (
    <section className="file-scope-panel" aria-label="文件搜索范围" ref={scopePanelRef}>
      <div className="file-scope-panel__head">
        <div className="file-scope-current">
          <span className="file-scope-current__label">当前范围</span>
          <strong title={currentScope || undefined}>
            {isSavingScope ? "正在切换范围..." : currentScope || "未选择范围"}
          </strong>
          <small>{currentScope ? "搜索、分组、清理都会限定在这里。" : "先选择一个文件夹，再开始搜索或整理。"}</small>
        </div>
        <button className="button button--secondary" type="button" onClick={onChooseFolder} disabled={isSavingScope}>
          <FolderOpen size={16} aria-hidden="true" />
          选择要查找的文件夹
        </button>
      </div>
      {!currentScope ? (
        <p className="file-status file-status--info">
          第一步：选择要查找的文件夹。Lengrvis 只会扫描你选择的文件夹，清理前不会删除任何文件。
        </p>
      ) : null}
      <div className="file-scope-shortcuts" aria-label="常用文件夹">
        {shortcuts.map((shortcut) => {
          const Icon = shortcut.icon;
          const active = Boolean(shortcut.path && shortcut.path === currentScope);
          return (
            <button
              key={shortcut.id}
              className={active ? "scope-chip scope-chip--active" : "scope-chip"}
              type="button"
              onClick={() => onSetSearchScope(shortcut.path)}
              disabled={!shortcut.path || isSavingScope}
              aria-pressed={active}
              title={shortcut.path ?? "暂时读不到这个常用文件夹。可以先用上方主按钮选择位置。"}
            >
              <Icon size={14} aria-hidden="true" />
              {shortcut.label}
            </button>
          );
        })}
      </div>
      {knownFoldersChecked && !hasKnownFolderShortcuts ? (
        <p className="file-scope-note">暂时读不到桌面、下载、文档、图片。可以点“选择要查找的文件夹”，或直接粘贴文件夹路径。</p>
      ) : null}
      {currentScope ? (
        <div className="file-scope-path-preview" aria-label="当前完整范围路径">
          <span>完整路径</span>
          <strong title={currentScope}>{compactPath(currentScope)}</strong>
          <code>{currentScope}</code>
        </div>
      ) : null}
      <div className="file-scope-manual">
        <label className="field">
          <span>粘贴文件夹位置</span>
          <input
            value={manualScope}
            onChange={(event) => onManualScopeChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") onApplyManualScope();
            }}
            placeholder="粘贴文件夹路径，例如 C:\\Users\\你\\Documents"
          />
        </label>
        <button className="button button--ghost" type="button" onClick={onApplyManualScope} disabled={isSavingScope}>
          使用这个文件夹
        </button>
      </div>
      {scopeNotice ? <p className="file-status file-status--info">{scopeNotice}</p> : null}
      {scopeError ? <p className="field-error">{scopeError}</p> : null}
    </section>
  );
}
