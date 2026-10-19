import type { AppSurfaceProps } from "../AppSurfaceTypes";
import { ButlerView } from "../../features/butler/ButlerView";

export function ButlerRoute(props: Pick<AppSurfaceProps, "butler" | "onViewChange" | "onOpenButlerApproval">) {
  if (!props.butler) return <p className="empty-state">管家功能暂不可用。</p>;
  return <ButlerView butler={props.butler} onOpenConversation={(id) => {
    props.butler!.selectMatter(id);
    props.onViewChange("chat");
  }} onOpenApproval={(matter) => props.onOpenButlerApproval?.(matter)} onOpenBrowser={() => props.onViewChange("browser")} />;
}
