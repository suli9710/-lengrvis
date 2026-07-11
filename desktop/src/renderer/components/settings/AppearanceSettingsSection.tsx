import { Eye, Gauge, Sparkles } from "lucide-react";

import {
  type InterfaceDetailMode,
  type MotionPreference,
  useUiPreferences
} from "../../lib/uiPreferences";

const detailOptions: Array<{
  value: InterfaceDetailMode;
  label: string;
  description: string;
}> = [
  {
    value: "standard",
    label: "清爽视图",
    description: "默认聚焦目标、状态、下一步与必要审批。"
  },
  {
    value: "expert",
    label: "专业视图",
    description: "同一页面默认展开执行链路、边界、证据与诊断。"
  }
];

const motionOptions: Array<{
  value: MotionPreference;
  label: string;
  description: string;
}> = [
  {
    value: "system",
    label: "跟随系统",
    description: "自动遵循 Windows 的减少动画设置。"
  },
  {
    value: "full",
    label: "完整动效",
    description: "保留状态驱动的角色动作与交互反馈。"
  },
  {
    value: "reduced",
    label: "精简动效",
    description: "使用静态角色、即时切换和非平滑滚动。"
  }
];

export function AppearanceSettingsSection() {
  const { preferences, effectiveMotion, setDetailMode, setMotionPreference } = useUiPreferences();

  return (
    <fieldset className="mcp-servers appearance-settings settings-grid__full">
      <legend>界面与动效</legend>
      <div className="appearance-settings__intro">
        <span className="appearance-settings__mark" aria-hidden="true">
          <Sparkles size={16} />
        </span>
        <div>
          <strong>按你的使用习惯显示信息</strong>
          <p>这里的选择立即应用并仅保存在本机，不会改变权限、审批或任务执行策略。</p>
        </div>
      </div>

      <div className="appearance-settings__grid">
        <div className="appearance-settings__group" role="radiogroup" aria-labelledby="appearance-detail-title">
          <div className="appearance-settings__group-title" id="appearance-detail-title">
            <Eye size={15} aria-hidden="true" />
            <span>
              <strong>界面层级</strong>
              <small>普通摘要与专业调试信息使用同一套页面。</small>
            </span>
          </div>
          <div className="mode-radio-row appearance-settings__options">
            {detailOptions.map((option) => (
              <label
                key={option.value}
                className={preferences.detailMode === option.value ? "mode-radio mode-radio--selected" : "mode-radio"}
              >
                <input
                  type="radio"
                  name="lengrvis-interface-detail"
                  value={option.value}
                  checked={preferences.detailMode === option.value}
                  onChange={() => setDetailMode(option.value)}
                />
                <span>
                  <strong>{option.label}</strong>
                  <small>{option.description}</small>
                </span>
              </label>
            ))}
          </div>
        </div>

        <div className="appearance-settings__group" role="radiogroup" aria-labelledby="appearance-motion-title">
          <div className="appearance-settings__group-title" id="appearance-motion-title">
            <Gauge size={15} aria-hidden="true" />
            <span>
              <strong>动画效果</strong>
              <small>当前实际效果：{effectiveMotion === "reduced" ? "精简动效" : "完整动效"}</small>
            </span>
          </div>
          <div className="mode-radio-row appearance-settings__options">
            {motionOptions.map((option) => (
              <label
                key={option.value}
                className={preferences.motionPreference === option.value ? "mode-radio mode-radio--selected" : "mode-radio"}
              >
                <input
                  type="radio"
                  name="lengrvis-motion-preference"
                  value={option.value}
                  checked={preferences.motionPreference === option.value}
                  onChange={() => setMotionPreference(option.value)}
                />
                <span>
                  <strong>{option.label}</strong>
                  <small>{option.description}</small>
                </span>
              </label>
            ))}
          </div>
        </div>
      </div>

      <p className="appearance-settings__status" role="status">已应用到本机</p>
    </fieldset>
  );
}
