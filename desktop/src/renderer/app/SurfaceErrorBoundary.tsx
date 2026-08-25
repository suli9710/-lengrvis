import { RefreshCw, TriangleAlert } from "lucide-react";
import { Component, type ErrorInfo, type ReactNode } from "react";

import { AccessibleDialog } from "../components/AccessibleDialog";

interface SurfaceErrorBoundaryProps {
  children: ReactNode;
  fallback: ReactNode;
}

interface SurfaceErrorBoundaryState {
  failed: boolean;
}

export class SurfaceErrorBoundary extends Component<SurfaceErrorBoundaryProps, SurfaceErrorBoundaryState> {
  state: SurfaceErrorBoundaryState = { failed: false };

  static getDerivedStateFromError(): SurfaceErrorBoundaryState {
    return { failed: true };
  }

  componentDidCatch(error: unknown, _info: ErrorInfo): void {
    const errorName = error instanceof Error ? error.name : "UnknownError";
    console.error(`Renderer surface failed to load (${errorName}).`);
  }

  render(): ReactNode {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

export function RouteLoadFailure({ onReload = reloadRenderer }: { onReload?: () => void }) {
  return (
    <section className="route-load-failure" role="alert" aria-labelledby="route-load-failure-title">
      <TriangleAlert size={22} aria-hidden="true" />
      <div>
        <strong id="route-load-failure-title">此页面没有载入成功</strong>
        <p>本地页面资源暂时不可用。重新加载 Lengrvis 后再试。</p>
      </div>
      <button className="button button--primary" type="button" onClick={onReload}>
        <RefreshCw size={15} aria-hidden="true" />
        重新加载
      </button>
    </section>
  );
}

export function ApprovalLoadState() {
  return (
    <div className="modal-backdrop" role="presentation">
      <div className="modal approval-load-state" role="status" aria-live="polite">
        <span className="spin-icon" aria-hidden="true" />
        <strong>正在载入审批</strong>
      </div>
    </div>
  );
}

export function ApprovalLoadFailure({
  onClose,
  onReload = reloadRenderer
}: {
  onClose: () => void;
  onReload?: () => void;
}) {
  return (
    <AccessibleDialog
      labelledBy="approval-load-failure-title"
      describedBy="approval-load-failure-description"
      role="alertdialog"
      onClose={onClose}
    >
      <header className="modal__header">
        <div>
          <span className="panel__eyebrow">审批</span>
          <h2 id="approval-load-failure-title">审批内容没有载入成功</h2>
        </div>
        <TriangleAlert size={20} aria-hidden="true" />
      </header>
      <div className="modal__body approval-load-failure">
        <p id="approval-load-failure-description">
          当前不会执行待确认操作。请重新加载 Lengrvis 后再查看审批内容。
        </p>
      </div>
      <footer className="modal__footer">
        <button className="button button--ghost" type="button" onClick={onClose}>关闭</button>
        <button className="button button--primary" type="button" onClick={onReload}>
          <RefreshCw size={15} aria-hidden="true" />
          重新加载
        </button>
      </footer>
    </AccessibleDialog>
  );
}

function reloadRenderer(): void {
  window.location.reload();
}
