import { AlertTriangle, CheckCircle2, FolderPlus, PackagePlus, RefreshCw, ShieldCheck, Wrench } from "lucide-react";
import { useEffect, useState } from "react";

import type { InstalledSkill, SkillsCatalog } from "../../shared/types";
import { MavrisApiClient } from "../lib/apiClient";
import { Badge, Panel } from "../components/Panel";

interface SkillsViewProps {
  api: MavrisApiClient;
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
      setError(response.error?.message ?? "Failed to load skills.");
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
      setError(response.error?.message ?? "Skill registry refresh failed.");
      return;
    }
    setStatus(`Registry refreshed: ${response.data.skillCount} skills, ${response.data.toolCount} tools.`);
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
      setError(response.error?.message ?? "Skill import failed.");
      await refresh();
      return;
    }
    setStatus(`Installed ${response.data.skill.name} and refreshed ${response.data.refresh.toolCount} tools.`);
    await refresh();
  };

  const importDirectory = async () => {
    const path = await window.mavris?.dialog.chooseSkillDirectory();
    await importFromPath(path ?? null);
  };

  const importZip = async () => {
    const path = await window.mavris?.dialog.chooseSkillZip();
    await importFromPath(path ?? null);
  };

  const skills = catalog?.skills ?? [];
  const readyCount = skills.filter((skill) => skill.status === "ready").length;

  return (
    <Panel
      title="Skills"
      eyebrow="Local packages"
      className="panel--skills"
      action={<Badge tone={skills.some((skill) => skill.status === "error") ? "warning" : "info"}>{readyCount}/{skills.length} ready</Badge>}
    >
      <div className="skills-toolbar">
        <div className="skills-toolbar__meta">
          <span>Install directory</span>
          <code>{catalog?.installDirectory || "Not loaded"}</code>
        </div>
        <div className="skills-toolbar__actions">
          <button className="button button--secondary" type="button" disabled={isLoading || isImporting} onClick={() => void refreshRegistry()}>
            <RefreshCw size={15} aria-hidden="true" />
            Refresh
          </button>
          <button className="button button--secondary" type="button" disabled={isImporting} onClick={() => void importDirectory()}>
            <FolderPlus size={15} aria-hidden="true" />
            Directory
          </button>
          <button className="button button--primary" type="button" disabled={isImporting} onClick={() => void importZip()}>
            <PackagePlus size={15} aria-hidden="true" />
            Zip
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
            <strong>No skills installed</strong>
            <span>Import a local skill directory or a .zip package.</span>
          </div>
        ) : null}
        {isLoading ? <p className="muted">Loading skills...</p> : null}
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
            <span>{skill.version || "unknown version"}</span>
          </div>
        </div>
        <div className="skill-row__badges">
          <Badge tone={ok ? "success" : "danger"}>{skill.status}</Badge>
          <Badge tone={riskTone(skill.risk)}>{skill.risk || "risk unknown"}</Badge>
        </div>
      </header>

      <dl className="skill-meta">
        <div>
          <dt>Owner</dt>
          <dd>{skill.agentOwner || "Unknown"}</dd>
        </div>
        <div>
          <dt>Root</dt>
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
