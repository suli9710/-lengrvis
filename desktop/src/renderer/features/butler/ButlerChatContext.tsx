import type { ButlerMatter } from "../../../shared/butlerTypes";
import { ButlerInteractionForm, matterStatusLabels } from "./ButlerView";
import type { ButlerController } from "./useButler";

export function ButlerChatContext({ butler, onOpenApproval, onOpenBrowser, onOpenOverview }: {
  butler: ButlerController;
  onOpenApproval: (matter: ButlerMatter) => void;
  onOpenBrowser: () => void;
  onOpenOverview: () => void;
}) {
  if (!butler.available) return null;
  const selected = butler.selectedMatter;
  return <section className="butler-chat-context" aria-label="对话对应事项">
    <div className="butler-context-row">
      <label>本次对话<select value={butler.selectedMatterId ?? ""} onChange={(event) => butler.selectMatter(event.target.value || null)}>
        <option value="">新需求 / 自动判断关联事项</option>
        {(butler.overview?.matters ?? []).filter((matter) => matter.status !== "cancelled").map((matter) => <option value={matter.id} key={matter.id}>{matterStatusLabels[matter.status]} · {matter.goal.slice(0, 100)}</option>)}
      </select></label>
      <button className="button button--ghost" onClick={onOpenOverview}>管家概览</button>
    </div>
    {selected ? <p>正在接续：{selected.goal}<br /><span className="butler-note">{selected.progress}</span></p> : <p className="butler-note">可以交代新需求；需要修改已有事项时，从上方选择，或说清对应事项。</p>}
    {selected?.interaction ? <ButlerInteractionForm key={`${selected.id}:${selected.interaction.id}`} matter={selected} butler={butler} onOpenApproval={onOpenApproval} onOpenBrowser={onOpenBrowser} /> : null}
    {butler.error ? <p role="alert" className="butler-error">{butler.error}</p> : null}
  </section>;
}
