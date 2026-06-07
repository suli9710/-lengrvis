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

      <div className="skill-permissions" aria-label="Skill Product Manifest 权限卡">
        {skillPermissionCards(skill).map((permission) => (
          <span key={permission.label} className={`skill-permission skill-permission--${permission.tone}`}>
            <strong>{permission.label}</strong>
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

interface SkillManifestCard {
  label: string;
  detail: string;
  tone: ManifestCardTone;
}

function skillPermissionCards(skill: InstalledSkill): SkillManifestCard[] {
  const text = skillManifestText(skill);
  const executionTypes = new Set(skill.tools.map((tool) => tool.executionType));
  const risky = hasRisk(skill, "R2") || hasRisk(skill, "R3") || hasRisk(skill, "R4");
  const destructive = hasRisk(skill, "R3") || matches(text, /delete|remove|trash|unlink|purge|clear|drop|uninstall|erase|wipe|destroy|删除|卸载|清空/);
  const handoff = hasRisk(skill, "R4");
  const hasDryRunIssue = skill.safety.issues.some((issue) => /dry-run|preview|supports_dry_run/i.test(`${issue.location} ${issue.message}`));
  const rollback = matches(text, /rollback|revert|restore|undo|recover|checkpoint|回滚|恢复|撤销/);

  const readsFiles = matches(text, /file|document|path|pdf|docx|xlsx|pptx|csv|folder|directory|read|search|scan|index|image|photo|ocr|extract|文件|文档|图片/);
  const writesFiles = matches(text, /write|save|create|copy|move|rename|archive|zip|edit|update|export|generate|download|append|写入|保存|创建|移动|重命名/);
  const controlsUi = matches(text, /ui|automation|click|type|keyboard|mouse|screen|window|app|excel|browser|com|settings|launch|open|焦点|窗口|点击|输入/);
  const network = executionTypes.has("http") || matches(text, /http|https|web|browser|url|api|mail|email|calendar|slack|teams|webhook|drive|sharepoint|github|notion|network|网页|网络/);
  const messages = matches(text, /send|message|mail|email|calendar|invite|slack|teams|webhook|post|notify|notification|sms|wechat|chat|发送|通知|消息|微信|企业微信/);

  return [
    {
      label: "读文件",
      detail: readsFiles ? "可能读取授权目录或文档内容" : "未推断出文件读取",
      tone: readsFiles ? "review" : "safe"
    },
    {
      label: "写文件",
      detail: writesFiles ? "可能创建、更新或导出文件" : "未推断出文件写入",
      tone: writesFiles ? (destructive ? "danger" : "review") : "safe"
    },
    {
      label: "操作 UI",
      detail: controlsUi ? "可能操作窗口、浏览器或本地应用" : "未推断出 UI 控制",
      tone: controlsUi ? (hasRisk(skill, "R3") ? "danger" : "review") : "safe"
    },
    {
      label: "访问网络",
      detail: network ? "可能访问本地服务或外部网络" : "未声明网络访问",
      tone: network ? "review" : "safe"
    },
    {
      label: "发送消息",
      detail: messages ? "可能发送通知、邮件或聊天消息" : "未推断出消息发送",
      tone: messages ? "danger" : "safe"
    },
    {
      label: "删除数据",
      detail: destructive ? "可能删除、卸载或清空数据" : "未推断出删除动作",
      tone: destructive ? "danger" : "safe"
    },
    {
      label: "Preview",
      detail: hasDryRunIssue ? "缺少 dry-run preview，需修复" : risky ? "高风险执行前需要 preview/审批" : "低风险默认无需 preview",
      tone: hasDryRunIssue ? "danger" : risky ? "review" : "safe"
    },
    {
      label: "Rollback/Handoff",
      detail: handoff ? "R4 必须转人工或拒绝" : rollback ? "推断存在回滚/恢复路径" : risky ? "需在 demo 中说明回滚或兜底" : "无高风险回滚要求",
      tone: handoff ? "danger" : rollback ? "safe" : risky ? "review" : "safe"
    }
  ];
}

function skillManifestSummary(skill: InstalledSkill): string {
  const executionTypes = [...new Set(skill.tools.map((tool) => tool.executionType))].filter(Boolean);
  const risk = skill.risk || "未知风险";
  const execution = executionTypes.length ? executionTypes.join(" / ") : "未声明执行方式";
  return `${skill.tools.length} 个工具 · ${execution} · ${risk}`;
}

function skillManifestText(skill: InstalledSkill): string {
  return [
    skill.name,
    skill.agentOwner,
    skill.risk,
    skill.error,
    ...skill.tools.flatMap((tool) => [tool.name, tool.description, tool.agentOwner, tool.risk, tool.executionType, tool.entry]),
    ...skill.safety.issues.flatMap((issue) => [issue.severity, issue.location, issue.message])
  ].filter(Boolean).join(" ").toLowerCase();
}

function hasRisk(skill: InstalledSkill, riskPrefix: "R2" | "R3" | "R4"): boolean {
  return skill.risk.startsWith(riskPrefix) || skill.tools.some((tool) => tool.risk.startsWith(riskPrefix));
}

function matches(text: string, pattern: RegExp): boolean {
  return pattern.test(text);
}
