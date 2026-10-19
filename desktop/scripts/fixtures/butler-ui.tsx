// Local component acceptance fixture. No backend, credentials or external actions.
import React from "react";
import { createRoot } from "react-dom/client";
import { ButlerView } from "../../src/renderer/features/butler/ButlerView";
import { MemoryPanel } from "../../src/renderer/components/MemoryPanel";
import "../../src/renderer/styles.memory-schedule.css";
import { useButler } from "../../src/renderer/features/butler/useButler";
import type { LengrvisApiClient } from "../../src/renderer/lib/apiClient";
import type { ButlerArrangementInput, ButlerMatter, ButlerOverview } from "../../src/shared/butlerTypes";
import "../../src/renderer/styles.css";
import "../../src/renderer/styles.foundation.css";
import "../../src/renderer/styles.details.css";
import "../../src/renderer/styles.butler.css";

const now = new Date().toISOString();
const base: ButlerMatter = { id: "matter_a", version: 1, goal: "整理收件目录中的工作资料", source: "user", status: "running", mode: "efficiency", priority: 50, due_at: "", progress: "正在整理，完成后核验归档结果", interaction: null, waiting: null, task_ids: [], run_ids: [], active_task_id: "", active_run_id: "", completion_evidence: [], created_at: now, updated_at: now };
const state: ButlerOverview = { enabled: true, paused: false, defaults: { event_merge_seconds: 30, background_concurrency: 2, desktop_concurrency: 1 }, scopes: [{ id: "scope_files", version: 1, label: "工作资料", directories: ["D:\\工作资料"], applications: [], origins: [], allowed_actions: ["observe", "file_modify"], enabled: true, created_at: now, updated_at: now }], matters: [base,
  { ...base, id: "matter_b", goal: "把两份会议记录整理成报告", status: "waiting_input", progress: "需要确认输出格式", interaction: { id: "interaction_b", kind: "information", message: "报告希望使用什么格式？", fields: ["输出格式"], candidates: [], approval_id: "" } },
  { ...base, id: "matter_c", goal: "归档上周资料", status: "completed", progress: "归档完成，已核验源文件和目标文件", undo_available: true, completion_evidence: [{ kind: "文件归档", verified: true, verified_at: now, artifacts: ["D:\\工作资料\\归档\\上周记录.md"] }] }
] };
const actions: unknown[] = [];
state.matters.unshift({ ...base, id: "matter_office", goal: "公开网页整理为 Excel 与 Word 报告", progress: "表格已核验；报告有用户修改，需要核对后继续", artifacts: [
  { id: "artifact_xlsx", kind: "xlsx", name: "公开资料整理.xlsx", verified: true, opened: true, status: "内容与来源已核验" },
  { id: "artifact_docx", kind: "docx", name: "资料汇总报告.docx", verified: false, opened: false, status: "成果已被修改，请核对后继续；管家不会覆盖你的修改。" }
] });
state.coordination_enabled = true;
state.auto_memory_enabled = true;
const memories: any[] = [{ id: "mem_fixture", kind: "preference", content: "我的报告偏好先给结论再给依据", tags: ["butler", "自动保存", "用户陈述"], source: "user", state: "active", user_confirmed: true, created_at: now }];
state.matters.push({ ...base, id: "matter_report", goal: "资料齐全后生成交付报告", status: "queued", depends_on: [base.id], coordination: { blocked: true, blocking_dependencies: [{ matter_id: base.id, goal: base.goal, reason: "等待前置事项完成核验", needs_decision: false }], needs_decision: false, arrangement_reason: "等待工作资料归档完成核验", rank: 2, can_arrange_dependencies: true, suggestions: [] } });
Object.assign(window, { butlerAcceptance: { state, actions } });
const ok = (data: unknown) => Promise.resolve({ ok: true, data: structuredClone(data), status: 200, receivedAt: now });
const api = {
  openButlerArtifact: (matter_id: string, artifact_id: string, version: number) => { actions.push({ openArtifact: { matter_id, artifact_id, version } }); return ok({ opened: true }); },
  supportsButler: () => true, getButlerOverview: () => ok(state),
  controlButler: (action: string) => { actions.push({ control: action }); if (action === "pause" || action === "resume") state.paused = action === "pause"; else if (action.startsWith("coordination_")) state.coordination_enabled = action === "coordination_enable"; else if (action.startsWith("auto_memory_")) state.auto_memory_enabled = action === "auto_memory_enable"; else state.enabled = action === "enable"; return ok(state); },
  listMemories: () => ok(memories),
  saveMemory: (content: string, options: any) => { actions.push({ memory: content, options }); const previous = memories.find(item => item.id === options.supersedes); if (previous) previous.conflict_status = "superseded"; const item = { ...options, id: "mem_corrected", content, kind: "preference", source: "user", state: "active", user_confirmed: true, created_at: now }; memories.unshift(item); return ok(item); },
  forgetMemory: (id: string) => { const index = memories.findIndex(item => item.id === id); if (index >= 0) memories.splice(index, 1); actions.push({ forget: id }); return ok({ ok: true }); },
  arrangeButlerMatter: (input: ButlerArrangementInput) => { const item = state.matters.find(matter => matter.id === input.matter_id)!; actions.push({ arrangement: input }); if (input.version !== item.version) return Promise.resolve({ ok: false, status: 409, error: { message: "事项已更新" }, receivedAt: now }); Object.assign(item, input, { version: item.version + 1 }); return ok(item); },
  respondButlerMatter: (id: string, reply: unknown) => { actions.push({ respond: id, reply }); const matter = state.matters.find(item => item.id === id)!; matter.version++; matter.status = "running"; matter.interaction = null; matter.progress = "已收到补充，正在接续"; return ok({ matter }); },
  createButlerScope: (scope: any) => { actions.push({ scope }); const saved = { ...scope, id: scope.id || "scope_new", version: (scope.version || 0) + 1, enabled: true, created_at: now, updated_at: now }; state.scopes = [saved, ...state.scopes.filter(item => item.id !== saved.id)]; return ok(saved); },
  revokeButlerScope: (id: string) => { state.scopes.find(item => item.id === id)!.enabled = false; return ok(state); },
  undoButlerMatter: (id: string) => { actions.push({ undo: id }); state.matters.find(item => item.id === id)!.undo_available = false; return ok({}); },
  controlButlerMatter: (id: string, action: string) => { const item = state.matters.find(item => item.id === id)!; item.status = action === "pause" ? "paused" : action === "cancel" ? "cancelled" : "running"; return ok(item); }
} as unknown as LengrvisApiClient;

function App() {
  const butler = useButler(api, "efficiency", true);
  return <main style={{ padding: 24, maxWidth: 1200, margin: "0 auto", height: "100vh", overflow: "auto" }}><p>组件交互验收 · 全部为测试数据</p><ButlerView butler={butler} onOpenConversation={id => actions.push({ conversation: id })} onOpenApproval={() => actions.push("approval")} onOpenBrowser={() => actions.push("browser")} /><MemoryPanel api={api} /></main>;
}
createRoot(document.getElementById("root")!).render(<App />);
