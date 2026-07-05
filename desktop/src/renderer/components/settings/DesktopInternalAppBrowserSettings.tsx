import type { Dispatch, SetStateAction } from "react";

import type { AppSettings } from "../../../shared/settingsTypes";
import { splitSettingList } from "./SettingsPanelHelpers";

type SetDraft = Dispatch<SetStateAction<AppSettings>>;

export function DesktopInternalAppBrowserSettings({ draft, setDraft }: { draft: AppSettings; setDraft: SetDraft }) {
  return (
    <>
      <label className="field">
        <span>允许的应用</span>
        <textarea
          value={draft.appAllowlist.join("; ")}
          onChange={(event) =>
            setDraft((current) => ({
              ...current,
              appAllowlist: splitSettingList(event.target.value)
            }))
          }
        />
      </label>
      <label className="field">
        <span>浏览器截图目录</span>
        <input
          value={draft.browserScreenshotDir}
          onChange={(event) => setDraft((current) => ({ ...current, browserScreenshotDir: event.target.value }))}
        />
      </label>
      <label className="field">
        <span>网页读取上限</span>
        <input
          type="number"
          min={1000}
          step={1000}
          value={draft.browserMaxPageBytes}
          onChange={(event) =>
            setDraft((current) => ({
              ...current,
              browserMaxPageBytes: Math.max(1000, Number(event.target.value) || 1000)
            }))
          }
        />
      </label>
    </>
  );
}
