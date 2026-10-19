import { useState, type FormEvent } from "react";
import { Pause, Play, RefreshCw, ShieldCheck, Undo2 } from "lucide-react";
import type { AutonomyAction, AutonomyScope, AutonomyScopeInput, ButlerMatter, ButlerMatterStatus } from "../../../shared/butlerTypes";
import { Badge, Panel } from "../../components/Panel";
import type { ButlerController } from "./useButler";

export const matterStatusLabels: Record<ButlerMatterStatus, string> = {
  queued: "等待处理", planning: "正在准备", running: "正在处理", waiting_input: "需要补充", waiting_approval: "等待确认",
  waiting_event: "等待条件", paused: "已暂停", completed: "已完成", failed: "需要处理异常", cancelled: "已取消"
};

export const autonomyActionLabels: Record<AutonomyAction, string> = {
  observe: "读取与观察", file_modify: "可撤销的文件修改", ui_edit: "应用内容编辑", browser_prepare: "网页准备操作"
};

type MatterGroup = "active" | "waiting" | "decisions" | "completed" | "undo";
export function matterInGroup(matter: ButlerMatter, group: MatterGroup): boolean {
  if (group === "undo") return matter.undo_available === true;
  if (group === "active") return ["queued", "planning", "running"].includes(matter.status);
  if (group === "waiting") return ["waiting_event", "paused"].includes(matter.status);
  if (group === "decisions") return ["waiting_input", "waiting_approval", "failed"].includes(matter.status);
  return ["completed", "cancelled"].includes(matter.status);
}

interface ButlerViewProps {
  butler: ButlerController;
  onOpenConversation: (matterId: string | null) => void;
  onOpenApproval: (matter: ButlerMatter) => void;
  onOpenBrowser: () => void;
}

export function ButlerView({ butler, onOpenConversation, onOpenApproval, onOpenBrowser }: ButlerViewProps) {
  const [group, setGroup] = useState<MatterGroup>("active");
  const [showScopeForm, setShowScopeForm] = useState(false);
  const [editingScope, setEditingScope] = useState<AutonomyScope | null>(null);
  const overview = butler.overview;
  if (!butler.available) return <Panel title="AI 管家"><p className="empty-state">管家功能需要更新后的 Electron 桌面应用。</p></Panel>;

  const groups: Array<[MatterGroup, string]> = [["active", "正在处理"], ["waiting", "待跟进"], ["decisions", "待决策"], ["completed", "已结束"], ["undo", "可撤销"]];
  const matters = overview?.matters ?? [];
  return (
    <div className="butler-view">
      <Panel title="AI 管家" eyebrow="日常事务" action={
        <div className="butler-actions">
          <button className="button button--ghost" disabled={butler.busy} onClick={() => void butler.refresh()} aria-label="刷新管家进度"><RefreshCw size={15} />刷新</button>
          <button className="button button--primary" onClick={() => onOpenConversation(null)}>交代一件事</button>
        </div>
      }>
        <div className="butler-control-row">
          <div>
            <Badge tone={overview?.paused ? "warning" : overview?.enabled ? "success" : "neutral"}>
              {overview?.paused ? "管家已暂停" : overview?.enabled ? "主动管理已开启" : "主动管理未开启"}
            </Badge>
            <p>交代目标后持续处理，遇到缺少的信息或具体操作确认时再联系你。</p>
            <p className="butler-note">{overview?.auto_memory_enabled ? "会主动记住对话中的重要信息，可在“我的记忆”更正或删除。" : "自动记忆已关闭，仍可明确要求记住信息。"}</p>
          </div>
          <div className="butler-actions">
            <button className="button button--secondary" disabled={butler.busy || !overview} onClick={() => void butler.control(overview?.auto_memory_enabled ? "auto_memory_disable" : "auto_memory_enable")}>
              {overview?.auto_memory_enabled ? "关闭自动记忆" : "开启自动记忆"}
            </button>
            <button className="button button--secondary" disabled={butler.busy || !overview} onClick={() => void butler.control(overview?.coordination_enabled ? "coordination_disable" : "coordination_enable")}>
              {overview?.coordination_enabled ? "关闭事项统筹" : "开启事项统筹"}
            </button>
            <button className="button button--secondary" disabled={butler.busy || !overview} onClick={() => void butler.control(overview?.paused ? "resume" : "pause")}>
              {overview?.paused ? <Play size={15} /> : <Pause size={15} />}{overview?.paused ? "恢复管家" : "暂停管家"}
            </button>
            <button className="button button--ghost" disabled={butler.busy || !overview} onClick={() => void butler.control(overview?.enabled ? "disable" : "enable")}>
              {overview?.enabled ? "关闭主动管理" : "开启主动管理"}
            </button>
          </div>
        </div>
        {butler.error ? <p role="alert" className="butler-error">{butler.error}</p> : null}
        {!overview && !butler.error ? <p role="status">正在读取管家事项…</p> : null}
        <ButlerCoordinationSummary butler={butler} onOpenConversation={onOpenConversation} />
        <div className="butler-tabs" role="tablist" aria-label="事项状态">
          {groups.map(([key, label]) => <button key={key} id={`butler-tab-${key}`} role="tab" aria-selected={group === key} aria-controls="butler-matters" onClick={() => setGroup(key)}>
            {label}<span>{matters.filter((matter) => matterInGroup(matter, key)).length}</span>
          </button>)}
        </div>
        <div id="butler-matters" role="tabpanel" aria-labelledby={`butler-tab-${group}`} className="butler-matters">
          {matters.filter((matter) => matterInGroup(matter, group)).map((matter) => (
            <ButlerMatterCard key={matter.id} matter={matter} butler={butler} onOpenConversation={onOpenConversation} onOpenApproval={onOpenApproval} onOpenBrowser={onOpenBrowser} />
          ))}
          {overview && !matters.some((matter) => matterInGroup(matter, group)) ? <p className="empty-state">这个分组暂时没有事项。</p> : null}
        </div>
      </Panel>

      <Panel title="自主处理范围" eyebrow="由你授权" action={<button className="button button--secondary" disabled={butler.busy} onClick={() => { setEditingScope(null); setShowScopeForm((open) => !open); }}><ShieldCheck size={15} />{showScopeForm ? "收起" : "新增范围"}</button>}>
        <p className="butler-note">仅在授权的目录、应用和网站中主动处理事务。付款、对外发送、公开发布和不可恢复的改动仍需确认。</p>
        {showScopeForm ? <ButlerScopeForm key={editingScope ? `${editingScope.id}:${editingScope.version}` : "new"} initial={editingScope ?? undefined} busy={butler.busy} onSubmit={async (input) => {
          const result = await butler.createScope(input);
          if (result.ok) setShowScopeForm(false);
          return result.ok;
        }} /> : null}
        <div className="butler-scopes">
          {(overview?.scopes ?? []).map((scope) => <article className="butler-scope" key={scope.id}>
            <header><strong>{scope.label}</strong><Badge tone={scope.enabled ? "success" : "neutral"}>{scope.enabled ? "已授权" : "已撤销"}</Badge></header>
            <dl>
              {scope.directories.length ? <><dt>目录</dt><dd>{scope.directories.join("\n")}</dd></> : null}
              {scope.applications.length ? <><dt>应用</dt><dd>{scope.applications.join("\n")}</dd></> : null}
              {scope.origins.length ? <><dt>网站</dt><dd>{scope.origins.join("\n")}</dd></> : null}
              {scope.account_id ? <><dt>账号</dt><dd>{scope.account_id}</dd></> : null}
              <dt>操作</dt><dd>{scope.allowed_actions.map((action) => autonomyActionLabels[action]).join("、")}</dd>
            </dl>
            <div className="butler-actions"><button className="button button--secondary" disabled={butler.busy} onClick={() => { setEditingScope(scope); setShowScopeForm(true); }}>{scope.enabled ? "调整范围" : "重新授权"}</button>
            {scope.enabled ? <button className="button button--ghost" disabled={butler.busy} onClick={() => void butler.revokeScope(scope)}>撤销此范围授权</button> : null}</div>
          </article>)}
          {!overview?.scopes.length ? <p className="empty-state">添加范围后，管家才会持续关注其中的事务。</p> : null}
        </div>
      </Panel>
    </div>
  );
}

export function ButlerMatterCard({ matter, butler, onOpenConversation, onOpenApproval, onOpenBrowser }: Omit<ButlerViewProps, "onOpenConversation"> & { matter: ButlerMatter; onOpenConversation: (matterId: string) => void }) {
  const terminal = ["completed", "failed", "cancelled"].includes(matter.status);
  return <article className="butler-matter">
    <header><h3>{matter.goal}</h3><Badge tone={matter.status === "completed" ? "success" : matter.interaction || matter.status === "failed" ? "warning" : "info"}>{matterStatusLabels[matter.status]}</Badge></header>
    <p>{matter.progress}</p>
    {!!matter.artifacts?.length && <div className="butler-artifacts" aria-label="事项成果">
      {matter.artifacts.map((artifact) => <div className="butler-artifact" key={artifact.id}>
        <div><strong>{artifact.name}</strong><p>{artifact.status}{artifact.opened ? " · 已在应用中打开" : ""}</p></div>
        <button className="button button--secondary" disabled={butler.busy || !artifact.verified}
          onClick={() => void butler.openArtifact(matter, artifact.id)}>
          在 {artifact.kind === "xlsx" ? "Excel" : "Word"} 中打开
        </button>
      </div>)}
    </div>}
    {matter.memory_ids?.length ? <p className="butler-note">本事项保存或关联过 {matter.memory_ids.length} 条记忆，可在“我的记忆”查看当前状态。</p> : null}
    {matter.coordination?.arrangement_reason ? <p className="butler-note">安排：{matter.coordination.arrangement_reason}</p> : null}
    {matter.depends_on?.length ? <p className="butler-note">前置事项：{matter.depends_on.map((id) => butler.overview?.matters.find((item) => item.id === id)?.goal ?? id).join("、")}</p> : null}
    {matter.coordination?.suggestions.length ? <ul className="butler-note">{matter.coordination.suggestions.map((text, index) => <li key={index}>{text}</li>)}</ul> : null}
    {matter.due_at ? <p className="butler-note">期限：{displayDate(matter.due_at)}</p> : null}
    {matter.waiting ? <p className="butler-note">等待：{waitingLabel(matter.waiting.kind)}{matter.waiting.subject ? ` · ${matter.waiting.subject}` : ""}{matter.waiting.due_at ? ` · ${displayDate(matter.waiting.due_at)}` : ""}</p> : null}
    {matter.interaction ? <ButlerInteractionForm key={`${matter.id}:${matter.interaction.id}`} matter={matter} butler={butler} onOpenApproval={onOpenApproval} onOpenBrowser={onOpenBrowser} /> : null}
    {matter.completion_evidence.length ? <details className="butler-evidence"><summary>查看完成证据（{matter.completion_evidence.length} 项）</summary>
      {matter.completion_evidence.map((evidence, index) => <EvidenceSummary key={index} evidence={evidence} />)}
    </details> : null}
    <div className="butler-actions">
      <button className="button button--secondary" onClick={() => onOpenConversation(matter.id)}>在对话中接续</button>
      {!terminal ? <>
        <button className="button button--ghost" disabled={butler.busy} onClick={() => void butler.controlMatter(matter, matter.status === "paused" ? "resume" : "pause")}>{matter.status === "paused" ? "恢复此事项" : "暂停此事项"}</button>
        <button className="button button--ghost" disabled={butler.busy} onClick={() => void butler.controlMatter(matter, "cancel")}>取消事项</button>
      </> : null}
      {matter.undo_available ? <button className="button button--ghost" disabled={butler.busy} onClick={() => void butler.undo(matter)}><Undo2 size={15} />撤销已记录改动</button> : null}
    </div>
    {!terminal ? <details className="butler-evidence"><summary>调整事项安排</summary><ButlerArrangementForm key={matter.id} matter={matter} butler={butler} /></details> : null}
  </article>;
}

function ButlerCoordinationSummary({ butler, onOpenConversation }: Pick<ButlerViewProps, "butler" | "onOpenConversation">) {
  const matters = butler.overview?.matters ?? [];
  const active = matters.filter((item) => !["completed", "failed", "cancelled"].includes(item.status));
  const groups: Array<[string, ButlerMatter[]]> = [
    ["接下来处理", active.filter((item) => item.status === "queued" && !item.coordination?.blocked).sort((a, b) => (a.coordination?.rank ?? 0) - (b.coordination?.rank ?? 0))],
    ["等待前置事项", active.filter((item) => item.coordination?.blocked)],
    ["需要你决定", matters.filter((item) => item.interaction || item.coordination?.needs_decision || item.status === "failed")]
  ];
  if (!butler.overview?.coordination_enabled && !matters.some((item) => item.depends_on?.length)) return null;
  return <div className="butler-coordination">
    {groups.map(([label, items]) => <section key={label} aria-label={label}><h3>{label}</h3>
      {items.length ? <ul>{items.slice(0, 8).map((item) => <li key={item.id}>
        <button className="button button--ghost" onClick={() => onOpenConversation(item.id)}>{item.goal}</button>
        <p className="butler-note">{item.coordination?.arrangement_reason || item.progress}</p>
      </li>)}</ul> : <p className="butler-note">暂无事项</p>}
    </section>)}
  </div>;
}

export function ButlerArrangementForm({ matter, butler }: { matter: ButlerMatter; butler: ButlerController }) {
  const [editingVersion, setEditingVersion] = useState(matter.version);
  const [dependencies, setDependencies] = useState(matter.depends_on ?? []);
  const [priority, setPriority] = useState(matter.priority);
  const localDeadline = () => {
    if (!matter.due_at) return "";
    const date = new Date(matter.due_at);
    return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
  };
  const [deadline, setDeadline] = useState(localDeadline);
  const [deadlineEdited, setDeadlineEdited] = useState(false);
  const canChangeDependencies = matter.coordination?.can_arrange_dependencies ?? (!matter.task_ids.length && !matter.run_ids.length && !["planning", "running", "waiting_approval"].includes(matter.status));
  return <form className="butler-interaction" onSubmit={async (event) => {
    event.preventDefault();
      const result = await butler.arrange({ ...matter, version: editingVersion }, {
      ...(canChangeDependencies ? { depends_on: dependencies } : {}), priority,
      ...(deadlineEdited ? { due_at: deadline ? new Date(deadline).toISOString() : "" } : {})
      });
      if (result.ok && result.data) setEditingVersion(result.data.version);
  }}>
    <label>前置事项（可多选）<select multiple disabled={!canChangeDependencies || butler.busy} value={dependencies} onChange={(event) => setDependencies(Array.from(event.target.selectedOptions, (option) => option.value))}>
      {(butler.overview?.matters ?? []).filter((item) => item.id !== matter.id && item.interaction?.kind !== "choose_dependency").map((item) => <option key={item.id} value={item.id}>{matterStatusLabels[item.status]} · {item.goal}</option>)}
      {dependencies.filter((id) => !butler.overview?.matters.some((item) => item.id === id)).map((id) => <option key={id} value={id}>未显示的前置事项 · {id}</option>)}
    </select></label>
    {!canChangeDependencies ? <p className="butler-note">已开始执行，前置关系保持不变。</p> : null}
    <label>优先级（0–100，越高越优先）<input type="number" min={0} max={100} required value={priority} onChange={(event) => setPriority(Number(event.target.value))} /></label>
    <label>截止时间（本机时区）<input type="datetime-local" value={deadline} onChange={(event) => { setDeadline(event.target.value); setDeadlineEdited(true); }} /></label>
    {editingVersion !== matter.version ? <p className="butler-note">事项已更新，当前输入已保留。<button type="button" className="button button--ghost" onClick={() => { setDependencies(matter.depends_on ?? []); setPriority(matter.priority); setDeadline(localDeadline()); setDeadlineEdited(false); setEditingVersion(matter.version); }}>重新载入最新安排</button></p> : null}
    <button className="button button--primary" disabled={butler.busy} type="submit">保存明确安排</button>
  </form>;
}

export function ButlerInteractionForm({ matter, butler, onOpenApproval, onOpenBrowser }: Pick<ButlerViewProps, "butler" | "onOpenApproval" | "onOpenBrowser"> & { matter: ButlerMatter }) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [message, setMessage] = useState("");
  const [candidate, setCandidate] = useState("");
  const interaction = matter.interaction;
  if (!interaction || ["completed", "failed", "cancelled"].includes(matter.status)) return null;
  if (interaction.kind === "choose_dependency") {
    const request = matter.constraints?.coordination_request;
    if (!request) return <p role="alert">关联信息不可用，请重新提出请求。</p>;
    const roles = [{ key: "parent", label: "前置事项", choices: request.parents, goal: request.parent_goal }, { key: "child", label: "后续事项", choices: request.children, goal: request.child_goal }];
    return <form className="butler-interaction" onSubmit={async (event) => {
      event.preventDefault();
      if (!butler.busy) await butler.respond(matter, { answers });
    }}><p>{interaction.message}</p>{roles.map((role) => <label key={role.key}>{role.label}
      {role.choices.length ? <select required value={answers[role.key] ?? (role.choices.length === 1 ? role.choices[0] : "")} onChange={(event) => setAnswers((current) => ({ ...current, [role.key]: event.target.value }))}>
        <option value="">请选择事项</option>{role.choices.map((id) => <option key={id} value={id}>{butler.overview?.matters.find((item) => item.id === id)?.goal ?? id} · {id.slice(-8)}</option>)}
      </select> : <span>新建：{role.goal}</span>}
    </label>)}<button className="button button--primary" disabled={butler.busy} type="submit">确认关联</button></form>;
  }
  if (interaction.kind === "approval") return <div className="butler-interaction"><p>{interaction.message}</p><button className="button button--primary" onClick={() => onOpenApproval(matter)}>查看具体操作并确认</button></div>;
  const choosing = interaction.kind === "choose_matter";
  const fieldsComplete = interaction.fields.every((field) => answers[field]?.trim());
  const hasAnswer = choosing ? Boolean(candidate) : interaction.fields.length ? fieldsComplete : Boolean(message.trim());
  return <form className="butler-interaction" onSubmit={async (event) => {
    event.preventDefault();
    if (!hasAnswer || butler.busy) return;
    const ok = await butler.respond(matter, choosing ? { answers: { matter_id: candidate } } : { message, answers });
    if (ok) { setAnswers({}); setMessage(""); setCandidate(""); }
  }}>
    <p>{interaction.message}</p>
    {choosing ? <label>对应事项<select required value={candidate} onChange={(event) => setCandidate(event.target.value)}>
      <option value="">请选择事项</option>
      {interaction.candidates.map((id) => <option key={id} value={id}>{butler.overview?.matters.find((item) => item.id === id)?.goal ?? "事项已不可用"}</option>)}
    </select></label> : <>
      {interaction.fields.map((field) => <label key={field}>{field}<input required maxLength={12000} value={answers[field] ?? ""} onChange={(event) => setAnswers((current) => ({ ...current, [field]: event.target.value }))} /></label>)}
      <label>{interaction.fields.length ? "其他补充（可选）" : "补充信息"}<textarea maxLength={12000} required={!interaction.fields.length} value={message} onChange={(event) => setMessage(event.target.value)} rows={2} /></label>
    </>}
    <div className="butler-actions">
      {interaction.kind === "takeover" ? <button type="button" className="button button--secondary" onClick={onOpenBrowser}>打开原网页</button> : null}
      <button className="button button--primary" disabled={butler.busy || !hasAnswer} type="submit">{butler.busy ? "正在接续…" : "提交并接续"}</button>
    </div>
  </form>;
}

export function ButlerScopeForm({ busy, onSubmit, initial }: { busy: boolean; initial?: AutonomyScope; onSubmit: (input: AutonomyScopeInput) => Promise<boolean> }) {
  const [label, setLabel] = useState(initial?.label ?? "");
  const [directories, setDirectories] = useState(initial?.directories.join("\n") ?? "");
  const [applications, setApplications] = useState(initial?.applications.join("\n") ?? "");
  const [origins, setOrigins] = useState(initial?.origins.join("\n") ?? "");
  const [account, setAccount] = useState(initial?.account_id ?? "");
  const [actions, setActions] = useState<AutonomyAction[]>(initial?.allowed_actions ?? ["observe"]);
  const [error, setError] = useState<string | null>(null);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    if (!label.trim() || !(directories.trim() || applications.trim() || origins.trim()) || !actions.length) {
      setError("请填写名称、至少一项目标范围和允许的操作。"); return;
    }
    setError(null);
    await onSubmit({ ...(initial ? { id: initial.id, version: initial.version } : {}), label: label.trim(), directories: lines(directories), applications: lines(applications), origins: lines(origins), account_id: account.trim(), allowed_actions: actions });
  };
  return <form className="butler-scope-form" onSubmit={submit}>
    <label>范围名称<input required maxLength={160} value={label} onChange={(event) => setLabel(event.target.value)} placeholder="例如：工作资料" /></label>
    <label>目录（每行一个完整路径）<textarea rows={2} value={directories} onChange={(event) => setDirectories(event.target.value)} placeholder="D:\\工作资料" /></label>
    <label>应用（每行一个可执行文件的完整路径）<textarea rows={2} value={applications} onChange={(event) => setApplications(event.target.value)} placeholder="C:\\Windows\\System32\\notepad.exe" /></label>
    <label>网站（每行一个网站源）<textarea rows={2} value={origins} onChange={(event) => setOrigins(event.target.value)} placeholder="https://example.com" /></label>
    <label>网页账号标识（可选）<input maxLength={256} value={account} onChange={(event) => setAccount(event.target.value)} placeholder="使用的账号名称，不填写密码" /></label>
    <fieldset><legend>允许的操作</legend>{(Object.keys(autonomyActionLabels) as AutonomyAction[]).map((action) => <label className="butler-check" key={action}>
      <input type="checkbox" checked={actions.includes(action)} onChange={(event) => setActions((current) => event.target.checked ? [...current, action] : current.filter((item) => item !== action))} />{autonomyActionLabels[action]}
    </label>)}</fieldset>
    {error ? <p className="butler-error" role="alert">{error}</p> : null}
    <button className="button button--primary" type="submit" disabled={busy}>{busy ? "等待系统确认…" : "核对并授权范围"}</button>
  </form>;
}

function EvidenceSummary({ evidence }: { evidence: Record<string, unknown> }) {
  const labels: Record<string, string> = { kind: "证据类型", condition: "核验条件", subject: "核验对象", path: "文件", resource: "对象", summary: "结果", verified: "已核验", matched: "符合目标", verified_at: "核验时间" };
  const artifacts = Array.isArray(evidence.artifacts) ? evidence.artifacts.filter((item): item is string => typeof item === "string") : [];
  return <dl>{Object.keys(labels).filter((key) => typeof evidence[key] === "string" || typeof evidence[key] === "boolean").map((key) => <div key={key}><dt>{labels[key]}</dt><dd>{typeof evidence[key] === "boolean" ? (evidence[key] ? "是" : "否") : key === "verified_at" ? displayDate(String(evidence[key])) : String(evidence[key]).slice(0, 2000)}</dd></div>)}
    {artifacts.map((path) => <div key={path}><dt>已生成或修改</dt><dd>{path}</dd></div>)}
  </dl>;
}
function lines(value: string): string[] { return [...new Set(value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean))]; }
function displayDate(value: string): string { const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleString(); }
function waitingLabel(kind: string): string { return ({ file_exists: "文件出现", file_changed: "文件更新", time: "约定时间", browser_change: "网页结果", provider: "模型服务恢复", user: "用户操作", desktop_idle: "电脑空闲" })[kind] ?? kind; }
