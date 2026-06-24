import { AlertTriangle, CheckCircle2, Loader2, ShieldCheck, Trash2 } from "lucide-react";
import { useState } from "react";

import { PRIVACY_ERASE_CONFIRMATION_TEXT } from "../../../shared/privacy";
import type { PrivacyEraseResult } from "../../../shared/types";
import type { LengrvisApiClient } from "../../lib/apiClient";

const ERASED_DATA = [
  "任务、对话与运行记录",
  "录屏、审批与移动配对",
  "记忆、文件索引与诊断包"
];

export function PrivacyDataPanel({ api }: { api: LengrvisApiClient }) {
  const [confirmationText, setConfirmationText] = useState("");
  const [includeSettings, setIncludeSettings] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [result, setResult] = useState<PrivacyEraseResult | null>(null);
  const [error, setError] = useState("");
  const confirmed = confirmationText === PRIVACY_ERASE_CONFIRMATION_TEXT;

  const eraseLocalData = async () => {
    if (!confirmed || deleting) return;
    setDeleting(true);
    setError("");
    setResult(null);
    try {
      const response = await api.eraseLocalData({ confirmationText, includeSettings });
      if (!response.ok || !response.data) {
        throw new Error(response.error?.message || "本机数据删除失败");
      }
      setResult(response.data);
      setConfirmationText("");
    } catch (eraseError) {
      setError(readableMessage(eraseError, "本机数据删除失败"));
    } finally {
      setDeleting(false);
    }
  };

  return (
    <fieldset className="mcp-servers privacy-data-settings settings-grid__full">
      <legend>本机数据与隐私</legend>
      <div className="privacy-data-settings__header">
        <div>
          <strong>删除本机保存的个人数据</strong>
          <span>操作不可撤销，安全审计链会保留。</span>
        </div>
        <ShieldCheck size={20} aria-hidden="true" />
      </div>

      <ul className="privacy-data-settings__scope">
        {ERASED_DATA.map((item) => (
          <li key={item}>
            <Trash2 size={14} aria-hidden="true" />
            {item}
          </li>
        ))}
      </ul>

      <label className="checkbox-row privacy-data-settings__option">
        <input
          type="checkbox"
          checked={includeSettings}
          onChange={(event) => setIncludeSettings(event.target.checked)}
          disabled={deleting}
        />
        <span>同时恢复应用设置与权限策略为默认值</span>
      </label>

      <label className="field privacy-data-settings__confirm">
        <span>
          输入 <strong>{PRIVACY_ERASE_CONFIRMATION_TEXT}</strong> 以继续
        </span>
        <input
          value={confirmationText}
          onChange={(event) => setConfirmationText(event.target.value)}
          autoComplete="off"
          spellCheck={false}
          disabled={deleting}
          aria-describedby="privacy-data-delete-note"
        />
      </label>

      <div className="privacy-data-settings__actions">
        <p id="privacy-data-delete-note">
          本地日志不会在应用运行时自动删除，可在系统信息中找到日志目录后手动清理。
        </p>
        <button
          type="button"
          className="button button--danger"
          onClick={() => void eraseLocalData()}
          disabled={!confirmed || deleting}
        >
          {deleting ? <Loader2 className="settings-spinner" size={15} aria-hidden="true" /> : <Trash2 size={15} aria-hidden="true" />}
          {deleting ? "正在删除" : "删除本机数据"}
        </button>
      </div>

      {result ? (
        <div className="privacy-data-settings__result" role="status">
          <CheckCircle2 size={17} aria-hidden="true" />
          <div>
            <strong>本机数据已删除</strong>
            <span>
              已清除 {result.deletedRowsTotal} 条本地记录和 {result.deletedDiagnosticPackages} 个诊断包。
              {result.settingsReset ? " 请重启应用以重新加载默认设置。" : ""}
            </span>
          </div>
        </div>
      ) : null}
      {error ? (
        <p className="privacy-data-settings__error" role="alert">
          <AlertTriangle size={16} aria-hidden="true" />
          {error}
        </p>
      ) : null}
    </fieldset>
  );
}

function readableMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message.trim()) return error.message;
  return fallback;
}
