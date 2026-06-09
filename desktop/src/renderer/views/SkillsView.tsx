import { AlertTriangle, CheckCircle2, FolderPlus, PackagePlus, RefreshCw, ShieldCheck, Wrench } from "lucide-react";
import { useEffect, useState } from "react";

import type { InstalledSkill, SkillsCatalog } from "../../shared/types";
import { LengrvisApiClient } from "../lib/apiClient";
import { Badge, Panel } from "../components/Panel";

interface SkillsViewProps {
  api: LengrvisApiClient;
}

export function SkillsView({ api }: SkillsViewProps) {
  const [catalog, setCatalog] = useState<SkillsCatalog | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isImporting, setIsImporting] = useState(false);
  const [status, setStatus] = useState<string>("");
  const [error, setError] = useState<string>("");

  const refresh = async () => {
    setIsLoading(true);
    setError("");
    const response = await api.listSkills();
    setIsLoading(false);
    if (!response.ok || !response.data) {
      setError(response.error?.message ?? "技能加载失败。");
      return;
    }
    setCatalog(response.data);
  };

  useEffect(() => {
    void refresh();
  }, []);

  const refreshRegistry = async () => {
    setIsLoading(true);
    setStatus("");
    setError("");
    const response = await api.refreshSkills();
    setIsLoading(false);
    if (!response.ok || !response.data) {
      setError(response.error?.message ?? "技能注册表刷新失败。");
      return;
    }
    setStatus(`注册表已刷新：${response.data.skillCount} 个技能，${response.data.toolCount} 个工具。`);
    await refresh();
  };

  const importFromPath = async (path: string | null) => {
    if (!path) return;
    setIsImporting(true);
    setStatus("");
    setError("");
    const response = await api.importSkill(path);
    setIsImporting(false);
    if (!response.ok || !response.data) {
      setError(response.error?.message ?? "技能导入失败。");
      await refresh();
      return;
    }
    setStatus(`已安装 ${response.data.skill.name}，并刷新 ${response.data.refresh.toolCount} 个工具。`);
    await refresh();
  };

  const importDirectory = async () => {
    const path = await window.lengrvis?.dialog.chooseSkillDirectory();
    await importFromPath(path ?? null);
  };

  const importZip = async () => {
    const path = await window.lengrvis?.dialog.chooseSkillZip();
    await importFromPath(path ?? null);
  };

  const skills = catalog?.skills ?? [];
  const readyCount = skills.filter((skill) => skill.status === "ready").length;

  return (
    <Panel
      title="技能"
      eyebrow="本地扩展包"
      className="panel--skills"
      action={<Badge tone={skills.some((skill) => skill.status === "error") ? "warning" : "info"}>{readyCount}/{skills.length} 可用</Badge>}
    >
      <div className="skills-toolbar">
        <div className="skills-toolbar__meta">
          <span>安装目录</span>
          <code>{catalog?.installDirectory || "未加载"}</code>
        </div>
        <div className="skills-toolbar__actions">
          <button className="button button--secondary" type="button" disabled={isLoading || isImporting} onClick={() => void refreshRegistry()}>
            <RefreshCw size={15} aria-hidden="true" />
            刷新
          </button>
          <button className="button button--secondary" type="button" disabled={isImporting} onClick={() => void importDirectory()}>
            <FolderPlus size={15} aria-hidden="true" />
            目录
          </button>
          <button className="button button--primary" type="button" disabled={isImporting} onClick={() => void importZip()}>
            <PackagePlus size={15} aria-hidden="true" />
            Zip 包
          </button>
        </div>
      </div>

      {status ? (
        <div className="skills-status skills-status--ok">
          <CheckCircle2 size={15} aria-hidden="true" />
          <span>{status}</span>
        </div>
      ) : null}
      {error ? (
        <div className="skills-status skills-status--error">
          <AlertTriangle size={15} aria-hidden="true" />
          <span>{error}</span>
        </div>
      ) : null}

      <div className="skill-list">
        {skills.map((skill) => (
          <SkillRow key={`${skill.root}-${skill.name}`} skill={skill} />
        ))}
        {!isLoading && skills.length === 0 ? (
          <div className="skill-empty">
            <Wrench size={18} aria-hidden="true" />
            <strong>尚未安装技能</strong>
            <span>导入本地技能目录或 .zip 包。</span>
          </div>
        ) : null}
        {isLoading ? <p className="muted">正在加载技能...</p> : null}
      </div>
    </Panel>
  );
}

function SkillRow({ skill }: { skill: InstalledSkill }) {
  const ok = skill.status === "ready" && skill.safety.ok;
  return (
    <article className={ok ? "skill-row" : "skill-row skill-row--error"}>
      <header className="skill-row__head">
        <div className="skill-row__title">
          {ok ? <ShieldCheck size={16} aria-hidden="true" /> : <AlertTriangle size={16} aria-hidden="true" />}
          <div>
            <strong>{skill.name}</strong>
            <span>{skill.version || "未知版本"}</span>
          </div>
        </div>
        <div className="skill-row__badges">
          <Badge tone={ok ? "success" : "danger"}>{skill.status === "ready" ? "可用" : skill.status}</Badge>
          <Badge tone={riskTone(skill.risk)}>{skill.risk || "未知风险"}</Badge>
        </div>
      </header>

      <dl className="skill-meta">
        <div>
          <dt>归属</dt>
          <dd>{skill.agentOwner || "未知"}</dd>
        </div>
        <div>
          <dt>根目录</dt>
          <dd title={skill.root}>{skill.root}</dd>
        </div>
      </dl>

      {skill.error ? <p className="skill-error">{skill.error}</p> : null}
      {skill.safety.issues.length ? (
        <ul className="skill-issues">
          {skill.safety.issues.map((issue, index) => (
            <li key={`${issue.location}-${index}`}>
              <strong>{issue.severity}</strong>
              <span>{issue.location}: {issue.message}</span>
            </li>
          ))}
        </ul>
      ) : null}

      <div className="skill-manifest-summary">
        <strong>Skill Product Manifest</strong>
        <span>{skillManifestSummary(skill)}</span>
      </div>

      <div className="skill-permissions-note" aria-label="权限来源说明">
        <span>已授权只看 Manifest 声明。</span>
        <span>文本提示和安全检查只是提醒，不会自动授权。</span>
      </div>

      <div className="skill-permissions" aria-label="Manifest 声明权限、文本提示与安全检查">
        {skillPermissionCards(skill).map((permission) => (
          <span
            key={permission.label}
            className={`skill-permission skill-permission--${permission.tone} skill-permission--${permissionSourceClass(permission.source)}`}
          >
            <span className="skill-permission__head">
              <strong>{permission.label}</strong>
              <b>{permission.source}</b>
            </span>
            <em>{permission.detail}</em>
          </span>
        ))}
      </div>

      <div className="skill-tools">
        {skill.tools.map((tool) => (
          <div className="skill-tool" key={tool.name}>
            <Wrench size={13} aria-hidden="true" />
            <div>
              <strong>{tool.name}</strong>
              <span>{tool.executionType} · {tool.agentOwner} · {tool.risk}</span>
            </div>
          </div>
        ))}
      </div>
    </article>
  );
}

function riskTone(risk: string): "neutral" | "success" | "warning" | "danger" | "info" {
  if (risk.startsWith("R0") || risk.startsWith("R1")) return "success";
  if (risk.startsWith("R2")) return "warning";
  if (risk.startsWith("R3") || risk.startsWith("R4")) return "danger";
  return "neutral";
}

type ManifestCardTone = "safe" | "review" | "danger";
type ManifestCardSource = "Manifest 声明" | "文本提示" | "安全检查" | "未声明";

interface SkillManifestCard {
  label: string;
  detail: string;
  source: ManifestCardSource;
  tone: ManifestCardTone;
}

function skillPermissionCards(skill: InstalledSkill): SkillManifestCard[] {
  const signalText = skillSignalText(skill);
  const permissions = skillPermissions(skill);
  const executionTypes = new Set(skill.tools.map((tool) => tool.executionType));
  const risky = hasRisk(skill, "R2") || hasRisk(skill, "R3") || hasRisk(skill, "R4");
  const declaredDelete = hasPermission(permissions, /^filesystem\.delete$/);
  const signalDelete = matches(signalText, /delete|remove|trash|unlink|purge|clear|drop|uninstall|erase|wipe|destroy|删除|卸载|清空/);
  const destructive = declaredDelete || signalDelete;
  const handoff = hasRisk(skill, "R4");
  const hasDryRunIssue = skill.safety.issues.some((issue) => /dry-run|preview|supports_dry_run/i.test(`${issue.location} ${issue.message}`));
  const supportsPreview = skill.tools.some((tool) => tool.supportsDryRun);
  const hasRollbackHint = skill.tools.some((tool) => tool.rollbackHint.trim().length > 0);
  const rollbackSignal = matches(signalText, /rollback|revert|restore|undo|recover|checkpoint|handoff|hand off|回滚|恢复|撤销|人工|转交/);

  const declaredReadsFiles = hasPermission(permissions, /^filesystem\.read$/);
  const signalReadsFiles = matches(signalText, /file|document|path|pdf|docx|xlsx|pptx|csv|folder|directory|read|search|scan|index|image|photo|ocr|extract|文件|文档|图片/);
  const declaredWritesFiles = hasPermission(permissions, /^filesystem\.write$/);
  const signalWritesFiles = matches(signalText, /write|save|create|copy|move|rename|archive|zip|edit|update|export|generate|download|append|写入|保存|创建|移动|重命名/);
  const declaredControlsUi = hasPermission(permissions, /^ui\.(open|control|input)$/);
  const signalControlsUi = matches(signalText, /ui|automation|click|type|keyboard|mouse|screen|window|app|excel|browser|com|settings|launch|open|焦点|窗口|点击|输入/);
  const declaredNetwork = hasPermission(permissions, /^network\./);
  const signalNetwork =
    executionTypes.has("http") ||
    matches(signalText, /http|https|web|browser|url|api|mail|email|calendar|slack|teams|webhook|drive|sharepoint|github|notion|network|网页|网络/);
  const declaredMessages = hasPermission(permissions, /^messaging\.(send|write|create)$/);
  const signalMessages = matches(signalText, /send|message|mail|email|calendar|invite|slack|teams|webhook|post|notify|notification|sms|wechat|chat|发送|通知|消息|微信|企业微信/);

  return [
    capabilityCard({
      label: "读文件",
      declared: declaredReadsFiles,
      signal: signalReadsFiles,
      declaredDetail: "可读取授权目录或文档内容",
      signalDetail: "不是已授权；描述里提到读取文件，执行前需确认",
      emptyDetail: "没有看到读取权限或文本提示",
      declaredTone: "review",
      signalTone: "review"
    }),
    capabilityCard({
      label: "写文件",
      declared: declaredWritesFiles,
      signal: signalWritesFiles,
      declaredDetail: "可创建、更新或导出文件",
      signalDetail: "不是已授权；描述里提到写入文件，执行前需确认",
      emptyDetail: "没有看到写入权限或文本提示",
      declaredTone: destructive ? "danger" : "review",
      signalTone: destructive ? "danger" : "review"
    }),
    capabilityCard({
      label: "操作 UI",
      declared: declaredControlsUi,
      signal: signalControlsUi,
      declaredDetail: "可操作窗口、浏览器或本地应用",
      signalDetail: "不是已授权；描述里提到 UI 自动化，执行前需确认",
      emptyDetail: "没有看到 UI 控制权限或文本提示",
      declaredTone: hasRisk(skill, "R3") ? "danger" : "review",
      signalTone: hasRisk(skill, "R3") ? "danger" : "review"
    }),
    capabilityCard({
      label: "访问网络",
      declared: declaredNetwork,
      signal: signalNetwork,
      declaredDetail: "可访问本地服务或外部网络",
      signalDetail: "不是已授权；执行方式或描述提示可能访问网络",
      emptyDetail: "没有看到网络权限或文本提示",
      declaredTone: "review",
      signalTone: "review"
    }),
    capabilityCard({
      label: "发送消息",
      declared: declaredMessages,
      signal: signalMessages,
      declaredDetail: "可发送通知、邮件或聊天消息",
      signalDetail: "不是已授权；描述里提到发送消息，执行前需确认",
      emptyDetail: "没有看到发送消息权限或文本提示",
      declaredTone: "danger",
      signalTone: "danger"
    }),
    capabilityCard({
      label: "删除数据",
      declared: declaredDelete,
      signal: signalDelete,
      declaredDetail: "可删除、卸载或清空数据",
      signalDetail: "不是已授权；描述里提到删除动作，执行前需确认",
      emptyDetail: "没有看到删除权限或文本提示",
      declaredTone: "danger",
      signalTone: "danger"
    }),
    {
      label: "Preview",
      source: supportsPreview ? "Manifest 声明" : hasDryRunIssue || risky ? "安全检查" : "未声明",
      detail: hasDryRunIssue ? "安全检查提醒：缺少 dry-run preview，需修复" : supportsPreview ? "支持 dry-run preview，执行前可展示计划" : risky ? "安全检查提醒：高风险执行前需要 preview/审批" : "低风险默认无需 preview",
      tone: hasDryRunIssue ? "danger" : supportsPreview || risky ? "review" : "safe"
    },
    {
      label: "Rollback/Handoff",
      source: handoff ? "安全检查" : hasRollbackHint ? "Manifest 声明" : rollbackSignal ? "文本提示" : risky ? "安全检查" : "未声明",
      detail: handoff ? "安全检查提醒：R4 必须转人工或拒绝" : hasRollbackHint ? "提供回滚或人工交接说明" : rollbackSignal ? "不是已授权；描述里提到回滚或交接，需确认 Manifest 边界" : risky ? "安全检查提醒：需在 demo 中说明回滚或兜底" : "无高风险回滚要求",
      tone: handoff ? "danger" : hasRollbackHint || rollbackSignal || risky ? "review" : "safe"
    }
  ];
}

function capabilityCard({
  label,
  declared,
  signal,
  declaredDetail,
  signalDetail,
  emptyDetail,
  declaredTone,
  signalTone
}: {
  label: string;
  declared: boolean;
  signal: boolean;
  declaredDetail: string;
  signalDetail: string;
  emptyDetail: string;
  declaredTone: ManifestCardTone;
  signalTone: ManifestCardTone;
}): SkillManifestCard {
  if (declared) {
    return { label, source: "Manifest 声明", detail: declaredDetail, tone: declaredTone };
  }
  if (signal) {
    return { label, source: "文本提示", detail: signalDetail, tone: signalTone };
  }
  return { label, source: "未声明", detail: emptyDetail, tone: "safe" };
}

function skillManifestSummary(skill: InstalledSkill): string {
  const executionTypes = [...new Set(skill.tools.map((tool) => tool.executionType))].filter(Boolean);
  const risk = skill.risk || "未知风险";
  const execution = executionTypes.length ? executionTypes.join(" / ") : "未声明执行方式";
  return `${skill.tools.length} 个工具 · ${execution} · ${risk} · 权限以 Manifest 声明为准，文本提示只作提醒`;
}

function permissionSourceClass(source: ManifestCardSource): "declared" | "signal" | "check" | "absent" {
  if (source === "Manifest 声明") return "declared";
  if (source === "文本提示") return "signal";
  if (source === "安全检查") return "check";
  return "absent";
}

function skillSignalText(skill: InstalledSkill): string {
  return [
    skill.name,
    skill.agentOwner,
    skill.risk,
    skill.error,
    ...skill.tools.flatMap((tool) => [
      tool.name,
      tool.description,
      tool.agentOwner,
      tool.risk,
      tool.executionType,
      tool.entry,
      tool.rollbackHint
    ]),
    ...skill.safety.issues.flatMap((issue) => [issue.severity, issue.location, issue.message])
  ].filter(Boolean).join(" ").toLowerCase();
}

function skillPermissions(skill: InstalledSkill): string[] {
  return skill.tools.flatMap((tool) => tool.permissions).map((permission) => permission.toLowerCase());
}

function hasPermission(permissions: string[], pattern: RegExp): boolean {
  return permissions.some((permission) => pattern.test(permission));
}

function hasRisk(skill: InstalledSkill, riskPrefix: "R2" | "R3" | "R4"): boolean {
  return skill.risk.startsWith(riskPrefix) || skill.tools.some((tool) => tool.risk.startsWith(riskPrefix));
}

function matches(text: string, pattern: RegExp): boolean {
  return pattern.test(text);
}
