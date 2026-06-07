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
